#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""名人·巴菲特 & 马斯克 首批内容入库。

源（缓存于 data/famous_src/，全部原文，未改写）：
- brk1987.html / brk1989.html  berkshirehathaway.com 官方致股东信
- musk_wq.html                 en.wikiquote.org/wiki/Elon_Musk

用法：add_famous_bm.py --insert（仅入库） | --tts（幂等合成）
"""
import html
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from fetch_daily_news import DB, NEWS, tts, urlhash  # noqa: E402

SRC = ROOT / "data" / "famous_src"


def text_of(name):
    s = open(SRC / name, encoding="utf-8", errors="ignore").read()
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", s, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def snap(t, i, back=True):
    j = t.rfind(". ", 0, i) if back else t.find(". ", i)
    return (j + 2 if back else j + 2) if j > 0 else i


def cut_words(t, cap):
    w = t.split(" ")
    if len(w) <= cap:
        return t
    t2 = " ".join(w[:cap])
    p = t2.rfind(". ")
    return t2[: p + 1] if p > 100 else t2


def clean_wiki(x):
    x = x.replace("[[", "").replace("]]", "").replace("'''", "").replace("''", "'")
    return x.replace('\\"', '"').replace("<!--", " ").replace("-->", " ").strip()


def build_items():
    b87 = text_of("brk1987.html")
    i = b87.find("Mr. Market")
    assert i > 0, "1987 无 Mr. Market"
    start = snap(b87, max(0, i - 6500))
    end = snap(b87, min(len(b87), i + 8500), back=False)
    b87_seg = cut_words(clean_wiki(b87[start:end]), 2200)

    # Musk：Wikiquote 平文本——「名句 + (年份/出处)」形态
    mflat = clean_wiki(text_of("musk_wq.html"))
    quotes, seen, words = [], set(), 0
    for m in re.finditer(r'([A-Z][^.!?"\n]{25,350}[.!?])\s*[\[(](?:19|20)\d\d[^\])]*[\])]', mflat):
        q = m.group(1).strip()
        low = q.lower()
        if any(k in low for k in ('wikiquote', 'navigation', 'jump to', 'main menu', 'login', 'born ')):
            continue
        if q in seen:
            continue
        seen.add(q)
        quotes.append(q)
        words += len(q.split())
        if words > 2300:
            break
    musk_q = "\n\n".join(quotes)

    return [
        ("famous:buffett-1987-mrmarket", "1988-02-27",
         "巴菲特：1987 致股东信·「市场先生」名段（节选）", b87_seg),
        ("famous:musk-quotes-wikiquote", "2026-10-22",
         "马斯克：语录与金句精选（Wikiquote 收录）", musk_q),
    ]


def main():
    conn = sqlite3.connect(DB, timeout=15)
    items = build_items()
    if "--insert" in sys.argv:
        for url, date, title, text in items:
            if len(text) < 1500:
                print(f"[跳过] {url} 过短 {len(text)}")
                continue
            bad = [m for m in ("[[", "]]", "href", "http://") if m in text]
            print(f"[{len(text.split())}词] {title} 残留:{bad or '无'}")
            print("   开头:", text[:90])
            conn.execute(
                "INSERT OR IGNORE INTO news_items (date,title,category,url,text,created_at) VALUES (?,?,?,?,?,?)",
                (date, title, "名人", url, text, time.time()),
            )
        conn.commit()
        print("入库:", conn.execute("SELECT COUNT(*) FROM news_items WHERE category='名人'").fetchone()[0], "篇")
    if "--tts" in sys.argv:
        for url, date, title, text in items:
            h = urlhash(url)
            mp3, meta = NEWS / f"{h}.mp3", NEWS / f"{h}.json"
            if mp3.is_file() and meta.is_file():
                print("  已有:", title, flush=True)
                continue
            print(f"  TTS: {title} …", flush=True)
            tts(text, str(mp3), str(meta))
            print(f"  完成: {mp3.stat().st_size//1024}KB", flush=True)


if __name__ == "__main__":
    if "--insert" in sys.argv or "--tts" in sys.argv:
        main()
    else:
        print("用法: add_famous_bm.py --insert | --tts")
