"""戦略・日経平均・対象銘柄の均等空売りの損益を、見比べやすいグラフにする。

使い方: uv run python src/plot_pnl.py   （先に src/backtest.py を実行しておく）
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest import CAPITAL, benchmarks  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "data" / "runs"
NAMES = {"6908.T": "Iriso", "3992.T": "Needswell", "6861.T": "Keyence"}
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID = "#2b2b29", "#6b6a63", "#e6e5e0"


def main():
    px = pd.read_pickle(ROOT / "data" / "prices.pkl")
    eq = pd.read_csv(RUNS / "equity_llm.csv", index_col=0, parse_dates=True).iloc[:, 0]
    reg = pd.read_csv(RUNS / "regime_llm.csv", index_col=0, parse_dates=True).iloc[:, 0]
    trades = pd.read_csv(RUNS / "trades_llm.csv", parse_dates=["day"])
    nikkei, short_all = benchmarks(px, list(eq.index))
    pnl = {  # 損益（万円）
        "This strategy (LLM regime + rebound short)": (eq - CAPITAL) / 1e4,
        "Nikkei 225 buy & hold": (nikkei - CAPITAL) / 1e4,
        "Short all 22 stocks equally": (short_all - CAPITAL) / 1e4,
    }
    colors = [BLUE, ORANGE, AQUA]

    plt.rcParams.update({"font.family": "sans-serif", "font.size": 11, "axes.edgecolor": GRID,
                         "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 9), height_ratios=[3, 2], facecolor="#fcfcfb")

    # LLMが「下落相場」と判断した日の翌営業日（空売りできる日）に薄い帯を引く
    days = list(eq.index)
    down_next = [days[i + 1] for i, d in enumerate(days[:-1]) if reg.get(d) == "下落相場"]

    def shade(ax):
        for d in down_next:
            ax.axvspan(d - pd.Timedelta(hours=12), d + pd.Timedelta(hours=12), color="#d9d8d2", alpha=0.6, lw=0, zorder=0)

    # 上: 3つを並べる
    shade(ax1)
    for (name, s), c in zip(pnl.items(), colors):
        ax1.plot(s.index, s.values, color=c, lw=2, label=name)
        ax1.annotate(f"{s.iloc[-1]:+.1f}", (s.index[-1], s.iloc[-1]), xytext=(6, 0), textcoords="offset points",
                     va="center", color=INK, fontsize=11, fontweight="bold")
    ax1.axhline(0, color=MUTED, lw=1)
    ax1.set_ylabel("P&L (10,000 JPY)")
    ax1.set_title("P&L from 1,000,000 JPY  (Dec 1, 2025 - Jun 30, 2026)", loc="left", color=INK, fontsize=14, pad=12)
    ax1.legend(loc="upper left", frameon=False, labelcolor=INK)

    # 下: 戦略だけを拡大し、売買を書き込む
    shade(ax2)
    s = pnl["This strategy (LLM regime + rebound short)"]
    ax2.plot(s.index, s.values, color=BLUE, lw=2)
    ax2.axhline(0, color=MUTED, lw=1)
    for _, t in trades.iterrows():
        y = s.loc[t.day]
        is_short = t.side == "空売り"
        ax2.scatter(t.day, y, marker="v" if is_short else "^", s=80, color=BLUE if is_short else INK,
                    edgecolor="#fcfcfb", linewidth=1.5, zorder=3)
    labels = [("2026-03-06", "Short Iriso", (-30, -38)), ("2026-03-11", "Short Needswell + Keyence", (10, -38)),
              ("2026-03-30", "Cover Needswell\n(below -2σ, +14.3%)", (-150, 10)),
              ("2026-04-03", "Cover Iriso + Keyence\n(LLM says uptrend, +5.7% / +5.8%)", (25, 25))]
    for d, txt, off in labels:
        d = pd.Timestamp(d)
        ax2.annotate(txt, (d, s.loc[d]), xytext=off, textcoords="offset points", fontsize=10, color=INK,
                     arrowprops={"arrowstyle": "-", "color": MUTED, "lw": 0.8})
    ax2.set_ylabel("P&L (10,000 JPY)")
    ax2.set_ylim(-2.5, 8)
    ax2.set_title("This strategy, zoomed in on Mar - mid-Apr 2026 (no trades outside this window)   ▼ short  ▲ cover",
                  loc="left", color=INK, fontsize=12, pad=10)

    for ax in (ax1, ax2):
        ax.set_facecolor("#fcfcfb")
        ax.grid(axis="y", color=GRID, lw=0.8)
        ax.spines[["top", "right"]].set_visible(False)
    ax1.xaxis.set_major_locator(mdates.MonthLocator())
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax1.set_xlim(s.index[0], s.index[-1] + pd.Timedelta(days=8))
    ax2.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax2.set_xlim(pd.Timestamp("2026-02-27"), pd.Timestamp("2026-04-15"))
    fig.text(0.01, 0.005, "Gray bands: days a new short could start (the LLM called a 'downtrend' at the previous close).",
             color=MUTED, fontsize=10)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    out = ROOT / "pnl_comparison.png"
    fig.savefig(out, dpi=150)
    print(out)


if __name__ == "__main__":
    main()
