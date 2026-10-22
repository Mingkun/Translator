#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""名人·乔布斯 首期内容入库（4 篇：演讲名段节选 x1 + 访谈/语录节选 x3）

原文来源（全部为抓取缓存于 data/famous_src/ 的真实文本，绝不改写原句）：
- wq.html        en.wikiquote.org/wiki/Steve_Jobs（斯坦福 2005 演讲名段）
- playboy.html   allaboutstevejobs.com/verbatim/interviews/playboy_1985（全文）
- d5.html        allaboutstevejobs.com/verbatim/interviews/d5_conference_steve_bill_2007（全文）
- fortune08.html allaboutstevejobs.com/verbatim/interviews/fortune_2008（语录集）

用法：python3 add_famous_jobs.py [--dry]   # --dry 只打印切片，不写库不合成
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
DRY = "--dry" in sys.argv


def text_of(name):
    s = open(SRC / name, encoding="utf-8", errors="ignore").read()
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", s, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def cut_words(t, cap):
    words = t.split(" ")
    if len(words) <= cap:
        return t
    t2 = " ".join(words[:cap])
    p = t2.rfind(". ")
    return t2[: p + 1] if p > 100 else t2


def snap_start(t, i):
    """回溯到上一句句末，让切片从整句开始。"""
    j = t.rfind(". ", 0, i)
    return j + 2 if j > 0 and i - j < 400 else i


def seg(t, start, end=None, cap=None, back=None, snap=False):
    """按锚点切原文片段；snap=回溯到整句开头。返回 None 表示锚点缺失。"""
    i = t.find(start)
    if i < 0:
        return None
    if back:
        j = t.rfind(back, 0, i)
        i = j if j > 0 else i
    if snap:
        i = snap_start(t, i)
    j = (t.find(end, i) + len(end)) if end else i + 40000
    out = t[i:j]
    # 清理 wiki 标记残留
    out = out.replace("[[", "").replace("]]", "").replace("'''", "").replace("''", "'")
    out = out.replace('\\"', '"').replace("<!--", " ").replace("-->", " ")
    if cap:
        out = cut_words(out, cap)
    return out.strip()


wq = text_of("wq.html")
pb = text_of("playboy.html")
d5 = text_of("d5.html")
ft = text_of("fortune08.html")

# 斯坦福演讲：两个完整名段（时间有限 / Whole Earth Catalog 结尾），节选间用省略号
blocks = []
for a, b in [
    ("Your time is limited", "Everything else is secondary"),
    ("When I was young, there was an amazing publication", "as they signed off"),
]:
    s = seg(wq, a, b)
    if s:
        blocks.append(s)
stanford = "\n\n…\n\n".join(blocks)

playboy = seg(pb, "field has made millionaires", cap=1350, snap=True)
d5txt = seg(d5, "last several weeks", cap=1350, snap=True)
fortune = seg(ft, "If I'd have asked my customers", cap=1100)

ITEMS = [
    ("famous:jobs-stanford-2005", "2005-06-12",
     "乔布斯：斯坦福大学毕业演讲 2005（名段节选）", stanford),
    ("famous:jobs-playboy-1985", "1985-02-01",
     "乔布斯：《花花公子》访谈 1985（开篇节选）", playboy),
    ("famous:jobs-d5-2007", "2007-05-30",
     "乔布斯 & 比尔·盖茨：D5 大会同台对谈 2007（开篇节选）", d5txt),
    ("famous:jobs-fortune-2008", "2008-05-06",
     "乔布斯谈产品与管理：Fortune 语录精选 2008", fortune),
]

print("=== 切片预览 ===")
for url, date, title, txt in ITEMS:
    if not txt:
        print(f"[缺失] {title} —— 锚点未命中，跳过")
        continue
    bad = [m for m in ("[[", "]]", "'''", "http://", "href") if m in txt]
    print(f"[{len(txt.split())}词] {title} 残留:{bad or '无'}")
    print("   开头:", txt[:100])
    print("   结尾:", txt[-80:])
if DRY:
    print("（dry 模式，未写库）")
    sys.exit(0)

conn = sqlite3.connect(DB, timeout=15)
now = time.time()
for url, date, title, txt in ITEMS:
    if not txt:
        continue
    h = urlhash(url)
    cur = conn.execute(
        "INSERT OR IGNORE INTO news_items (date,title,category,url,text,created_at) VALUES (?,?,?,?,?,?)",
        (date, title, "名人", url, txt, now),
    )
    mp3, meta = NEWS / f"{h}.mp3", NEWS / f"{h}.json"
    need = (not mp3.is_file()) or (not meta.is_file())
    if need:
        print(f"TTS 合成中: {title} …")
        tts(txt, str(mp3), str(meta))
    print(f"  入库{'(新)' if cur.rowcount else '(已存在)'}: {url} 音频:{mp3.is_file()} 词表:{meta.is_file()}")
conn.commit()
print("完成：", conn.execute("SELECT COUNT(*) FROM news_items WHERE category='名人'").fetchone()[0], "篇 名人 内容")
