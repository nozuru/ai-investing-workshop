"""株価データを1回の yf.download でまとめて取得し、data/prices.pkl に保存する。"""

from pathlib import Path

import pandas as pd
import yfinance as yf

from universe import ALL_TICKERS

OUT = Path(__file__).resolve().parent.parent / "data" / "prices.pkl"
END = "2026-07-01"  # 開発期間。7月以降は進行役の合図まで取得しない


def main() -> None:
    if OUT.exists():
        print(f"既に保存済み: {OUT}")
        return
    OUT.parent.mkdir(exist_ok=True)
    df = yf.download(
        ALL_TICKERS,
        start="1970-01-01",  # 上場来の最高値を求めるため、取れる限り古くから
        end=END,
        auto_adjust=True,
        progress=False,
        threads=True,
    )
    df.to_pickle(OUT)
    close = df["Close"]
    missing = [t for t in ALL_TICKERS if t not in close.columns or close[t].dropna().empty]
    print(f"保存: {OUT}  行数={len(df)}  最終日={df.index.max().date()}")
    print("取得できなかった銘柄:", missing or "なし")
    for t in ALL_TICKERS:
        if t in close.columns and not close[t].dropna().empty:
            s = close[t].dropna()
            print(f"  {t:10s} {s.index.min().date()} 〜 {s.index.max().date()}  {len(s)}行")


if __name__ == "__main__":
    main()
