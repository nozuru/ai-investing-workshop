# Shenmacro（Shen）の記事の調査メモ（2026-11-24）

壁打ち中にAIがWeb検索で調べた結果。2026年7月以降の記事の中身は見ていない。

## 書き手と媒体
- 個人投資家 Shen（ハンドル名 shenmacro）。
- 旧ブログ「炭鉱のカナリア、炭鉱の龍」（livedoor）: https://www.shenmacro.com/ 。最終記事は2025-12-22。フィード: /atom.xml、/index.rdf
- 2026年からは note「人文科学アカデミー」: https://note.com/shennote
- 転載先: アゴラ https://agora-web.jp/archives/author/shen 、X: https://x.com/shenmacro

## 中身
- 「炭鉱のカナリア」は旧ブログのタイトルで、指標の連載ではない。
- 中心は毎週日曜〜月曜（日本時間）に出る S&P 500 の週次解説（無料）。翌週の想定レンジやガンマの境目などを裁量で述べる。数値の機械的なルールはない。
- 2025-12-21〜2026-06-29 の note 記事は36本。2025年11〜12月は旧ブログ。有料記事は日銀などのテーマ記事9本で、週次解説ではない。
- よく出てくる材料: NAAIM、BofAのディーラーガンマ、GSのCTA推計（いずれもyfinanceで取得不可）。原油 CL=F、半導体 ^SOX、金利 ^TNX、VIX ^VIX、ラッセル2000 ^RUT、HYG、金 GC=F、銅 HG=F、NVDA、ORCL、USDJPY=X（yfinanceで取得可）。
- 図表の多くはXからの転載画像で、文章だけでは情報が欠ける。

## 取得方法
- 一覧: https://note.com/api/v2/creators/shennote/contents?kind=note&page=N （publishAt、priceを含む）
- 本文: https://note.com/api/v3/notes/<key> の body
- いずれも非公式のAPI。一度取得して data/ に保存して使う。
- 判断に使う日時は題名の日付ではなく publishAt とし、公開後の翌営業日から反映する。
