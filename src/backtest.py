"""STRATEGY.md「フィジカルAI戻り売り」のバックテスト。

使い方:
  uv run python src/backtest.py                  # 本番（LLMで相場を判断）
  uv run python src/backtest.py --regime down    # 動作確認用: 毎日「下落相場」とみなす（戦略そのものではない）
  uv run python src/backtest.py --no-fee         # 監査用: 売買費用なし
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from universe import BENCHMARK, SEMI_ETF, STOCKS, eligible_from, suspended

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
import llm_regime  # noqa: E402  (.env を読んでから読み込む)

START, END = pd.Timestamp("2025-12-01"), pd.Timestamp("2026-06-30")
CAPITAL = 1_000_000
FEE = 0.001
MAX_PER_STOCK, MAX_TOTAL = 0.20, 1.00
TICKERS = list(STOCKS)


# ---------- 指標（すべて「その日の終値までのデータ」で計算する） ----------

def indicators(px: pd.DataFrame) -> dict[str, pd.DataFrame]:
    c, v = px["Close"][TICKERS], px["Volume"][TICKERS]

    # 価格表には米国だけが開いている日の行もあり、日本株はその行が空になる。
    # 空の行を含めて20日を数えると移動平均が欠けるため、銘柄ごとに自分の取引日だけで計算する
    def own_days(df, f):
        return pd.concat({t: f(df[t].dropna()) for t in df.columns}, axis=1, sort=True).reindex(df.index)

    ma = own_days(c, lambda s: s.rolling(20).mean())
    sd = own_days(c, lambda s: s.rolling(20).std(ddof=0))
    vol20_prev = own_days(v, lambda s: s.shift(1).rolling(20).mean())
    return {
        "close": c,
        "open": px["Open"][TICKERS],
        "volume": v,
        "ma20": ma,
        "lower": ma - 2 * sd,
        "ath": c.cummax(),  # その日までの最高値（上場来。yfinance で取れる最古の日から）
        "vol20_prev": vol20_prev,  # 前日までの20日平均出来高
    }


def shikori(px: pd.DataFrame, t: str, day: pd.Timestamp, lookback: int = 60, bins: int = 20) -> float:
    """過去60営業日の価格帯別出来高のうち、その日の終値より上の価格帯にある出来高の割合。"""
    h = px["High"][t].loc[:day].dropna().tail(lookback)
    l = px["Low"][t].loc[:day].dropna().tail(lookback)
    c = px["Close"][t].loc[:day].dropna().tail(lookback)
    v = px["Volume"][t].loc[:day].dropna().tail(lookback)
    if len(c) < lookback or v.sum() == 0:
        return 0.0
    typical = (h + l + c) / 3
    edges = np.linspace(l.min(), h.max(), bins + 1)
    idx = np.clip(np.digitize(typical, edges) - 1, 0, bins - 1)
    vol_by_bin = np.bincount(idx, weights=v.values, minlength=bins)
    centers = (edges[:-1] + edges[1:]) / 2
    return float(vol_by_bin[centers > c.iloc[-1]].sum() / v.sum())


# ---------- 相場判断 ----------

def regimes(px: pd.DataFrame, days: list[pd.Timestamp], mode: str) -> dict:
    if mode == "down":
        return {d: ("下落相場", "動作確認用の固定値", "") for d in days}
    if not llm_regime.llm_available():
        sys.exit("LLMのキーが .env にありません（WORKSHOP_LLM_API_KEY）。.env.example を参照。")
    close_all = px["Close"]
    prompts = {d: llm_regime.build_prompt(close_all, d) for d in days}
    out, done, failed = {}, 0, []

    def work(d):
        try:
            return d, llm_regime.ask(prompts[d])
        except Exception as e:  # 1件の失敗で全体を止めない。再実行すれば、その日だけ問い直す
            return d, e

    # 判断は互いに独立なので、古い日付から順に投げて並行で処理する（DGX Spark の同時実行上限は1人4件、全体4件）
    with ThreadPoolExecutor(max_workers=3) as ex:
        for d, text in ex.map(work, days):
            done += 1
            if isinstance(text, Exception):
                failed.append(d)
                print(f"\n{d.date()} のLLM呼び出しに失敗: {type(text).__name__}: {str(text)[:200]}")
                continue
            label, reason = llm_regime.parse(text)
            out[d] = (label, reason, prompts[d])
            print(f"\rLLM判断 {done}/{len(days)}  {d.date()} {label}      ", end="", flush=True)
    print()
    if failed:
        sys.exit(f"{len(failed)}日分のLLM判断が取れなかった。もう一度実行すると、その日だけ問い直す。")
    return out


# ---------- 売買の再現 ----------

def run(px: pd.DataFrame, mode: str, fee: float, log_path: Path | None):
    ind = indicators(px)
    cal = px["Close"][BENCHMARK].dropna().index
    days = [d for d in cal if START <= d <= END]
    prev_day = cal[cal < START][-1]  # 初日の売買は、期間開始前の最終営業日の判断で行う
    decision_days = [prev_day] + days[:-1]
    reg = regimes(px, decision_days + [days[-1]], mode)

    cash = float(CAPITAL)
    pos: dict[str, dict] = {}  # ticker -> {shares, entry_price, entry_day, decided}
    orders: list[dict] = []    # 翌営業日の始値で執行する注文
    trades, closed = [], []
    equity = {}
    log_f = open(log_path, "a") if log_path else None

    def decide(day: pd.Timestamp, eq: float) -> list[dict]:
        label = reg[day][0]
        new = []
        # 買い戻しの判定
        for t, p in pos.items():
            c, why = ind["close"][t].get(day), None
            if pd.isna(c):
                continue
            prev_c = ind["close"][t].loc[:day].dropna().iloc[-2]
            if label == "上昇相場":
                why = "LLMが上昇相場"
            elif c >= p["entry_price"] * 1.10:
                why = "損切り（+10%）"
            elif ind["volume"][t][day] >= 1.5 * ind["vol20_prev"][t][day] and c > prev_c:
                why = "損切り（出来高1.5倍で上昇）"
            elif c <= ind["lower"][t][day]:
                why = "利益確定（−2σ）"
            if why:
                new.append({"side": "cover", "ticker": t, "decided": day, "reason": why})
        # 空売りの判定
        if label == "下落相場":
            cands = []
            for t in TICKERS:
                if t in pos or not (day > eligible_from(t)):
                    continue
                c = ind["close"][t].loc[:day].dropna()
                if len(c) < 21 or c.index[-1] != day:
                    continue
                ma = ind["ma20"][t].loc[c.index]
                v = ind["volume"][t].loc[c.index]
                if (c.iloc[-1] <= 0.9 * ind["ath"][t][day]
                        and c.iloc[-1] >= ma.iloc[-1] and c.iloc[-2] < ma.iloc[-2]
                        and v.iloc[-1] < v.iloc[-2]):
                    cands.append((shikori(px, t, day), t))
            cands.sort(reverse=True)
            covering = {o["ticker"] for o in new}
            exposure = sum(p["shares"] * ind["close"][t][day] for t, p in pos.items() if t not in covering)
            room = MAX_TOTAL * eq - exposure
            for s, t in cands:
                size = min(MAX_PER_STOCK * eq, room)
                if size <= 0:
                    break
                new.append({"side": "short", "ticker": t, "decided": day, "value": size,
                            "reason": f"戻り売り（しこり{s:.2f}）"})
                room -= size
        return new

    def mark(day):
        return cash - sum(p["shares"] * ind["close"][t].loc[:day].dropna().iloc[-1] for t, p in pos.items())

    orders = decide(prev_day, CAPITAL)
    for i, day in enumerate(days):
        # 1) 始値で、前営業日までに決めた注文を執行する
        pending = []
        for o in orders:
            t, op = o["ticker"], ind["open"][o["ticker"]].get(day)
            if pd.isna(op):
                pending.append(o)  # 売買停止などで始値がない日は、次の営業日に回す
                continue
            assert o["decided"] < day, "判断日が売買日より前になっていない"
            if o["side"] == "cover" and t in pos:
                p = pos.pop(t)
                cost = p["shares"] * op
                cash -= cost + cost * fee
                ret = p["entry_price"] / op - 1
                closed.append({**p, "ticker": t, "exit_day": day, "exit_price": op, "ret": ret, "reason": o["reason"]})
                trades.append({"day": day, "decided": o["decided"], "side": "買い戻し", "ticker": t,
                               "price": op, "value": cost, "reason": o["reason"]})
            elif o["side"] == "short" and t not in pos:
                if suspended(t, day):
                    continue  # 日証金の申込停止中は新しい空売りをしない
                shares = o["value"] / op
                cash += o["value"] - o["value"] * fee
                pos[t] = {"shares": shares, "entry_price": op, "entry_day": day, "regime": reg[o["decided"]][0]}
                trades.append({"day": day, "decided": o["decided"], "side": "空売り", "ticker": t,
                               "price": op, "value": o["value"], "reason": o["reason"]})
        # 2) 終値で資産を評価し、次の営業日の注文を決める
        eq = mark(day)
        equity[day] = eq
        orders = pending + (decide(day, eq) if i < len(days) - 1 else [])
        if log_f:
            label, reason, prompt = reg[day]
            log_f.write(json.dumps({
                "判断日": str(day.date()), "渡した内容": prompt, "判断": label, "理由": reason,
                "翌営業日の注文": [{k: (str(v.date()) if isinstance(v, pd.Timestamp) else v) for k, v in o.items()} for o in orders],
                "資産額": round(eq),
            }, ensure_ascii=False) + "\n")
        print(f"\r売買の再現 {day.date()}  資産 {eq:,.0f}円   ", end="", flush=True)
    print()
    if log_f:
        log_f.close()
    return pd.Series(equity), pd.DataFrame(trades), pd.DataFrame(closed), pos, reg, days


# ---------- 比較対象 ----------

def benchmarks(px: pd.DataFrame, days: list[pd.Timestamp]) -> tuple[pd.Series, pd.Series]:
    n225 = px["Close"][BENCHMARK].loc[days]
    nikkei = CAPITAL * n225 / px["Open"][BENCHMARK].loc[days[0]]
    # 対象銘柄の均等空売り: 毎日、その日までに対象になった銘柄に資産の100%を均等に空売りする（費用なし）
    c = px["Close"][TICKERS].ffill()
    o = px["Open"][TICKERS]
    vals, eq = [], float(CAPITAL)
    for i, d in enumerate(days):
        if i == 0:
            elig = [t for t in TICKERS if d > eligible_from(t)] or []
            r = -np.nanmean([c[t][d] / o[t][d] - 1 for t in elig]) if elig else 0.0
        else:
            prev = days[i - 1]
            elig = [t for t in TICKERS if prev > eligible_from(t)]
            r = -np.nanmean([c[t][d] / c[t][prev] - 1 for t in elig]) if elig else 0.0
        eq *= 1 + r
        vals.append(eq)
    return nikkei, pd.Series(vals, index=days)


# ---------- 成績 ----------

def stats(eq: pd.Series, first_open_value: float = CAPITAL) -> dict:
    r = eq.pct_change().fillna(eq.iloc[0] / first_open_value - 1)
    total = eq.iloc[-1] / CAPITAL - 1
    years = len(eq) / 252
    dd = (eq / eq.cummax().clip(lower=CAPITAL) - 1).min()
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else float("nan")
    return {"total": total, "cagr": (1 + total) ** (1 / years) - 1, "mdd": dd, "sharpe": sharpe}


def pct(x):
    return f"{x * 100:.1f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regime", choices=["llm", "down"], default="llm")
    ap.add_argument("--no-fee", action="store_true")
    args = ap.parse_args()
    fee = 0.0 if args.no_fee else FEE
    official = args.regime == "llm" and not args.no_fee

    px = pd.read_pickle(ROOT / "data" / "prices.pkl")
    log_path = ROOT / "llm_log.jsonl" if official else None
    if log_path and log_path.exists():
        log_path.unlink()
    eq, trades, closed, open_pos, reg, days = run(px, args.regime, fee, log_path)
    nikkei, eqshort = benchmarks(px, days)

    s, b1, b2 = stats(eq), stats(nikkei), stats(eqshort)
    label = {("llm", False): "本番（LLMで相場判断・費用あり）", ("llm", True): "監査用（LLMで相場判断・費用なし）",
             ("down", False): "動作確認用（毎日「下落相場」とみなす・費用あり。戦略そのものではない）",
             ("down", True): "動作確認用（毎日「下落相場」とみなす・費用なし。戦略そのものではない）"}[(args.regime, args.no_fee)]

    # グラフ
    png = ROOT / ("equity.png" if official else f"equity_check_{args.regime}{'_nofee' if args.no_fee else ''}.png")
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(eq.index, eq / 1e4, label="Strategy (Physical-AI rebound short)")
    ax.plot(nikkei.index, nikkei / 1e4, label="Nikkei 225 buy & hold")
    ax.plot(eqshort.index, eqshort / 1e4, label="Equal-weight short of all targets")
    ax.set_ylabel("Equity (10k JPY)")
    ax.set_title(f"Backtest {days[0].date()} - {days[-1].date()} ({args.regime}{', no fee' if args.no_fee else ''})")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(png, dpi=120)

    # results.md
    period = f"{days[0].date()} 〜 {days[-1].date()}"
    lines = [
        f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')} {label}\n",
        "| 項目 | 戦略 | 日経平均 | 対象銘柄の均等空売り |",
        "|---|---|---|---|",
        f"| 期間 | {period} | {period} | {period} |",
        f"| 総リターン | {pct(s['total'])} | {pct(b1['total'])} | {pct(b2['total'])} |",
        f"| 年率リターン | {pct(s['cagr'])} | {pct(b1['cagr'])} | {pct(b2['cagr'])} |",
        f"| 最大下落率 | {pct(s['mdd'])} | {pct(b1['mdd'])} | {pct(b2['mdd'])} |",
        f"| シャープレシオ | {s['sharpe']:.2f} | {b1['sharpe']:.2f} | {b2['sharpe']:.2f} |",
        f"| 売買回数 | {len(trades)} | — | — |",
        "",
        f"グラフ: `{png.name}`",
    ]
    with open(ROOT / "results.md", "a") as f:
        f.write("\n".join(lines) + "\n")

    # 詳細（標準出力と data/ に保存）
    out_dir = ROOT / "data" / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{args.regime}{'_nofee' if args.no_fee else ''}"
    trades.to_csv(out_dir / f"trades_{tag}.csv", index=False)
    closed.to_csv(out_dir / f"closed_{tag}.csv", index=False)
    eq.to_csv(out_dir / f"equity_{tag}.csv")
    pd.Series({d: v[0] for d, v in reg.items()}).to_csv(out_dir / f"regime_{tag}.csv")
    print("\n".join(lines))
    print(f"\n持ち越した空売り: {list(open_pos)}")


if __name__ == "__main__":
    main()
