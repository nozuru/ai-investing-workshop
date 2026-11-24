"""LLMに「下落相場 / どちらでもない / 上昇相場」を判断させる。

判断日 d（東京市場の終値のあと）にLLMへ渡すのは、次の2つだけ。
- d の15時（東京）より前に公開された Shen の週次記事（題名が「S&P 500」で始まるもの）の最新4本
- Shen がよく見ている米国の指標の、d より前の日付の直近20営業日分（米国の d の取引は東京の終値の時点で終わっていない）
"""

import json
import os
import re
from pathlib import Path

import diskcache
import pandas as pd

from universe import LLM_INDICATORS

ROOT = Path(__file__).resolve().parent.parent
ARTICLES = json.loads((ROOT / "data" / "shen_articles.json").read_text())
for a in ARTICLES:
    a["ts"] = pd.Timestamp(a["published"])
WEEKLY = [a for a in ARTICLES if a["title"].startswith("S&P 500")]

# 会場のLLM（DGX Spark）。モデル名 dgx-default は稼働中のモデルを指す
DEFAULT_BASE_URL = "https://dgx.yozorafinance.com/v1"
DEFAULT_MODEL = "dgx-default"

LABELS = ["下落相場", "どちらでもない", "上昇相場"]
cache = diskcache.Cache(str(ROOT / "llm_cache"))


def articles_before(day: pd.Timestamp, n: int = 4) -> list[dict]:
    cutoff = pd.Timestamp(day.date()).tz_localize("Asia/Tokyo") + pd.Timedelta(hours=15)
    return [a for a in WEEKLY if a["ts"] < cutoff][-n:]


def indicator_table(close: pd.DataFrame, day: pd.Timestamp, n: int = 20) -> str:
    cols = list(LLM_INDICATORS)
    df = close[cols].loc[: day - pd.Timedelta(days=1)].dropna(how="all").tail(n)
    df = df.rename(columns={k: f"{k}（{v}）" for k, v in LLM_INDICATORS.items()})
    return df.round(2).to_csv()


def build_prompt(close: pd.DataFrame, day: pd.Timestamp) -> str:
    arts = articles_before(day)
    art_text = "\n\n".join(
        f"### 記事{i + 1}（公開: {a['ts'].strftime('%Y-%m-%d %H:%M')}）{a['title']}\n{a['body']}"
        for i, a in enumerate(arts)
    )
    return f"""あなたは、個人投資家 Shen（shenmacro）の相場の見方と考え方を身につけた分析者です。
判断日は {day.date()} です。この日より後に起きた出来事や価格は、知らないものとして扱ってください。
以下の材料だけをもとに、Shen ならこの時点の米国株（とくにAI・半導体株）の相場をどう見るかを判断してください。
記事が最新のものから数日たっている場合は、記事で示された考え方（注目している水準、指標の読み方）を、その後の指標の動きに当てはめて判断してください。

## Shen の直近の週次記事（判断日より前に公開されたもの、古い順）

{art_text}

## 判断日より前の指標の終値（直近20営業日）

{indicator_table(close, day)}

## 答え方

次のJSONだけを出力してください。
{{"判断": "下落相場" または "どちらでもない" または "上昇相場", "理由": "2〜3文"}}
"""


@cache.memoize()
def ask(prompt: str) -> str:
    import litellm

    response = litellm.completion(
        model=f"openai/{os.environ.get('WORKSHOP_LLM_MODEL', DEFAULT_MODEL)}",
        api_base=os.environ.get("WORKSHOP_LLM_BASE_URL", DEFAULT_BASE_URL),
        api_key=os.environ["WORKSHOP_LLM_API_KEY"],
        messages=[{"role": "user", "content": prompt}],
        timeout=120,
    )
    return response.choices[0].message.content


def parse(text: str) -> tuple[str, str]:
    m = re.search(r"\{.*\}", text, flags=re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            label = str(obj.get("判断", "")).strip()
            if label in LABELS:
                return label, str(obj.get("理由", ""))
        except json.JSONDecodeError:
            pass
    for label in LABELS:  # JSONが崩れていたら、本文に出てきたラベルで判定する
        if label in text:
            return label, text.strip()
    return "どちらでもない", f"（応答を解釈できなかった）{text.strip()}"


def llm_available() -> bool:
    return bool(os.environ.get("WORKSHOP_LLM_API_KEY"))
