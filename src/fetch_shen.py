"""Shen（shenmacro）の記事を取得し、data/shen_articles.json に保存する。

- 2026年以降: note「人文科学アカデミー」（note.com/shennote）の非公式API
- 2025年まで: 旧ブログ「炭鉱のカナリア、炭鉱の龍」の Atom フィード
2026年7月1日以降に公開された記事は保存しない（本文も取得しない）。
"""

import html
import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parent.parent / "data" / "shen_articles.json"
CUTOFF = pd.Timestamp("2026-07-01", tz="Asia/Tokyo")
UA = {"User-Agent": "Mozilla/5.0 (workshop backtest)"}


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def to_text(h: str) -> str:
    h = re.sub(r"<(br|/p|/h\d|/li|/div)[^>]*>", "\n", h, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "", h))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def fetch_note() -> list[dict]:
    items, page = [], 1
    while True:
        data = json.loads(get(f"https://note.com/api/v2/creators/shennote/contents?kind=note&page={page}"))["data"]
        for c in data["contents"]:
            items.append({"key": c["key"], "title": c["name"], "published": c["publishAt"], "price": c.get("price", 0)})
        if data.get("isLastPage", True):
            break
        page += 1
        time.sleep(1)
    out = []
    for it in items:
        pub = pd.Timestamp(it["published"]).tz_convert("Asia/Tokyo")
        if pub >= CUTOFF:
            continue
        body = json.loads(get(f"https://note.com/api/v3/notes/{it['key']}"))["data"].get("body") or ""
        out.append({"source": "note", "published": pub.isoformat(), "title": it["title"],
                    "paid": bool(it["price"]), "body": to_text(body)})
        time.sleep(1)
    return out


def fetch_blog() -> list[dict]:
    # livedoor は Atom 0.3 形式（名前空間と要素名が Atom 1.0 と違う）
    ns = {"a": "http://purl.org/atom/ns#"}
    root = ET.fromstring(get("https://www.shenmacro.com/atom.xml"))
    out = []
    for e in root.findall("a:entry", ns):
        pub = pd.Timestamp(e.findtext("a:issued", namespaces=ns)).tz_convert("Asia/Tokyo")
        if pub >= CUTOFF:
            continue
        # フィードの本文は冒頭だけなので、記事ページから本文を取る
        url = e.find("a:link", ns).get("href")
        page = get(url).decode("utf-8", "replace")
        m = re.search(r'<div class="article-body-inner">(.*?)<div class="article-social-btn', page, flags=re.S)
        content = m.group(1) if m else (e.findtext("a:summary", namespaces=ns) or "")
        time.sleep(1)
        out.append({"source": "blog", "published": pub.isoformat(), "title": e.findtext("a:title", namespaces=ns),
                    "paid": False, "body": to_text(content)})
    return out


def main() -> None:
    if OUT.exists():
        print(f"既に保存済み: {OUT}")
        return
    arts = fetch_blog() + fetch_note()
    arts.sort(key=lambda a: a["published"])
    OUT.write_text(json.dumps(arts, ensure_ascii=False, indent=1))
    print(f"保存: {len(arts)}本")
    for a in arts:
        print(a["published"][:16], a["source"], "有料" if a["paid"] else "無料", len(a["body"]), a["title"][:40])


if __name__ == "__main__":
    main()
