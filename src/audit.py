"""本番の実行結果を点検し、撤退の基準を確かめる。

使い方: uv run python src/audit.py   （先に src/backtest.py と --no-fee を実行しておく）
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest import indicators  # noqa: E402
from universe import BENCHMARK, SEMI_ETF, STOCKS, eligible_from, suspended  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "data" / "runs"


def main():
    px = pd.read_pickle(ROOT / "data" / "prices.pkl")
    trades = pd.read_csv(RUNS / "trades_llm.csv", parse_dates=["day", "decided"])
    closed = pd.read_csv(RUNS / "closed_llm.csv", parse_dates=["entry_day", "exit_day"])
    eq = pd.read_csv(RUNS / "equity_llm.csv", index_col=0, parse_dates=True).iloc[:, 0]
    eq0 = pd.read_csv(RUNS / "equity_llm_nofee.csv", index_col=0, parse_dates=True).iloc[:, 0]
    reg = pd.read_csv(RUNS / "regime_llm.csv", index_col=0, parse_dates=True).iloc[:, 0]
    out = []

    # 1) 判断日 < 売買日
    out.append(f"- 判断日が売買日より前: {len(trades)}件すべてで成立 = {(trades.decided < trades.day).all()}")

    # 2) 費用
    out.append(f"- 費用あり最終資産 {eq.iloc[-1]:,.0f}円 / 費用なし {eq0.iloc[-1]:,.0f}円（差 {eq0.iloc[-1] - eq.iloc[-1]:,.0f}円、"
               f"売買金額合計 {trades.value.sum():,.0f}円 × 0.1% = {trades.value.sum() * 0.001:,.0f}円）")

    # 3) 未来のデータを使っていないか: 空売りの判断を、判断日までに切ったデータで計算し直して一致するか
    ok = 0
    shorts = trades[trades.side == "空売り"]
    for _, t in shorts.iterrows():
        cut = px.loc[: t.decided]
        ind = indicators(cut)
        c = ind["close"][t.ticker].dropna()
        ma = ind["ma20"][t.ticker].loc[c.index]
        v = ind["volume"][t.ticker].loc[c.index]
        cond = (c.iloc[-1] <= 0.9 * c.max() and c.iloc[-1] >= ma.iloc[-1] and c.iloc[-2] < ma.iloc[-2]
                and v.iloc[-1] < v.iloc[-2] and t.decided > eligible_from(t.ticker)
                and not suspended(t.ticker, t.day) and reg[t.decided] == "下落相場")
        ok += bool(cond)
    out.append(f"- 空売り{len(shorts)}件を、判断日までのデータだけで計算し直して条件が一致: {ok}件")

    # 4) 相場判断の内訳
    out.append(f"- LLMの判断の日数: {reg.value_counts().to_dict()}")

    # 5) 撤退の基準
    semi = px["Close"][SEMI_ETF].dropna()
    crash = semi <= 0.9 * semi.rolling(60).max()  # semi は dropna 済みなので取引日だけで数える
    crash_days = crash[crash].loc[eq.index[0]: eq.index[-1]].index
    n225 = px["Close"][BENCHMARK]
    if len(closed):
        stop = closed.reason.str.startswith("損切り")
        down = closed[closed.regime == "下落相場"]
        share = down.reason.str.startswith("損切り").mean() if len(down) else float("nan")
        out.append(f"- 撤退の基準2: 「下落相場」の判断で空売りし、買い戻しまで終えた{len(down)}件のうち損切り {share:.0%}"
                   f"（基準は2/3以上で撤退） → {'該当' if share >= 2 / 3 else '該当しない'}")
    # 半導体が崩れた日に、空売り中の銘柄が日経平均より下がったか
    c = px["Close"][list(STOCKS)]
    rows = []
    for _, p in pd.concat([closed, pd.DataFrame()]).iterrows():
        days = [d for d in crash_days if p.entry_day < d <= p.exit_day]
        for d in days:
            s, n = c[p.ticker].loc[:d].dropna(), n225.loc[:d].dropna()  # 米国だけの営業日の空行を飛ばす
            if s.index[-1] != d or n.index[-1] != d:
                continue
            rows.append(s.iloc[-1] / s.iloc[-2] - n.iloc[-1] / n.iloc[-2])
    if rows:
        out.append(f"- 撤退の基準1: 半導体株ETFが60日高値から10%以上下げた日（{len(crash_days)}日）のうち、空売り中だった延べ{len(rows)}日で、"
                   f"空売り銘柄の騰落率 − 日経平均の騰落率 の平均 {sum(rows) / len(rows) * 100:+.2f}%（負なら仮説どおり）")
    else:
        out.append(f"- 撤退の基準1: 半導体株ETFが60日高値から10%以上下げた日は{len(crash_days)}日。そのとき空売り中の銘柄はなく、判定できない")

    # 6) 月ごとの損益
    m = eq.resample("ME").last()
    prev = pd.concat([pd.Series([1_000_000.0]), m.iloc[:-1]]).values
    out.append("- 月ごとの資産の増減: " + "、".join(f"{d:%Y-%m} {v - p:+,.0f}円" for d, v, p in zip(m.index, m.values, prev)))
    out.append("- 銘柄別の損益（買い戻し済み）: " + ("、".join(
        f"{STOCKS[t][0]} {g.ret.mean() * 100:+.1f}%×{len(g)}" for t, g in closed.groupby("ticker")) or "なし"))
    out.append("- 買い戻しの理由: " + str(closed.reason.value_counts().to_dict() if len(closed) else {}))
    print("\n".join(out))


if __name__ == "__main__":
    main()
