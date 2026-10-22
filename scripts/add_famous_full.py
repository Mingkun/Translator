#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""名人内容升级为全文版。

模式：
  --stanford  斯坦福演讲：全文+全程本人原声（用已缓存的 whisper 词流与完整音频，无 TTS）
  --full      Playboy 1985 / D5 2007 全文分部 + Fortune 2008 全文（TTS 合成，幂等）

原文均来自 data/famous_src/ 抓取缓存，未改写原句；D5 剥掉 [HH:MM:SS] 时间戳。
"""
import html
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from fetch_daily_news import DB, NEWS, tts, urlhash  # noqa: E402

SRC = ROOT / "data" / "famous_src"
NOW = time.time()


def text_of(name):
    s = open(SRC / name, encoding="utf-8", errors="ignore").read()
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", s, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def cut_parts(t, limit=29000):
    """按句边界切分成 ≤limit 字符的若干部。"""
    parts, buf = [], ""
    for sent in re.split(r"(?<=[.?!]) ", t):
        if len(buf) + len(sent) + 1 > limit and buf:
            parts.append(buf.strip())
            buf = sent
        else:
            buf = (buf + " " + sent).strip()
    if buf:
        parts.append(buf.strip())
    return parts


def upsert(conn, url, date, title, text, phase_note=""):
    conn.execute(
        "INSERT OR IGNORE INTO news_items (date,title,category,url,text,created_at) VALUES (?,?,?,?,?,?)",
        (date, title, "名人", url, text, NOW),
    )


def ensure_audio(url, text):
    h = urlhash(url)
    mp3, meta = NEWS / f"{h}.mp3", NEWS / f"{h}.json"
    if not mp3.is_file() or not meta.is_file():
        print(f"  TTS: {url} ({len(text)} 字) …", flush=True)
        tts(text, str(mp3), str(meta))
    return mp3.is_file() and meta.is_file()


def stanford():
    words = json.loads((SRC / "Stanford2005_words.json").read_text(encoding="utf-8"))
    # 拼全文：停顿 >1.5s 断段
    text, prev_end = "", 0.0
    for w in words:
        if prev_end and w["t"] - prev_end > 1.5:
            text += "\n\n"
        elif text:
            text += " "
        text += w["w"]
        prev_end = w["t"] + w["d"]
    marks = [{"t": w["t"], "d": w["d"], "w": w["w"]} for w in words]
    h = urlhash("famous:jobs-stanford-2005")
    (NEWS / f"{h}.json").write_text(json.dumps(marks, ensure_ascii=False), encoding="utf-8")
    full_mp3 = SRC / "Stanford2005_full.mp3"
    (NEWS / f"{h}.mp3").write_bytes(full_mp3.read_bytes())
    conn = sqlite3.connect(DB, timeout=15)
    conn.execute(
        "UPDATE news_items SET title=?, text=? WHERE url=?",
        ("乔布斯：斯坦福大学毕业演讲 2005（全程·本人原声）", text, "famous:jobs-stanford-2005"),
    )
    conn.commit()
    checks = {k: (k in text) for k in ["Stay Hungry", "Reed", "calligraph", "pancreat", "Whole Earth"]}
    print(f"斯坦福全文: {len(words)} 词, {len(text)} 字符; 名句校验 {checks}")
    print(f"  音频: {(NEWS / (h + '.mp3')).stat().st_size}B 全程原声")


def full_others():
    conn = sqlite3.connect(DB, timeout=15)
    # 删旧节选版（斯坦福保留，已就地升级）
    old = [r[0] for r in conn.execute("SELECT url FROM news_items WHERE category='名人' AND url NOT LIKE '%stanford%'")]
    for u in old:
        h = urlhash(u)
        for f in (NEWS / f"{h}.mp3", NEWS / f"{h}.json"):
            if f.is_file():
                f.rename(str(f) + ".excerpt.bak")
        conn.execute("DELETE FROM news_items WHERE url=?", (u,))
    print("已移除旧节选:", old)

    pb = text_of("playboy.html")
    pb_full = pb[pb.find("We survived 1984"): pb.find("How to grow obsolete with grace.") + len("How to grow obsolete with grace.")]
    d5 = text_of("d5.html")
    d5_body = d5[d5.find("SJ (1983)"): d5.find("It's great.") + len("It's great.")]
    d5_body = re.sub(r"\[\d{1,2}:\d{2}:\d{2}\]\s*", "", d5_body)
    ft = text_of("fortune08.html")
    ft_full = ft[ft.find("If I'd have asked my customers"): ft.find("we'll do this time.") + len("we'll do this time.")]

    plans = []
    for i, p in enumerate(cut_parts(pb_full), 1):
        plans.append((f"famous:jobs-playboy-1985-f{i}", "1985-02-01", f"乔布斯：《花花公子》访谈 1985（全文·{'一二三'[i-1]}）", p))
    for i, p in enumerate(cut_parts(d5_body), 1):
        plans.append((f"famous:jobs-d5-2007-f{i}", "2007-05-30", f"乔布斯 & 比尔·盖茨：D5 同台对谈 2007（全文·{'一二三'[i-1]}）", p))
    plans.append(("famous:jobs-fortune-2008-full", "2008-05-06", "乔布斯语录精选：Fortune 2008（全文）", ft_full))
    conn.commit()
    for url, date, title, text in plans:
        assert len(text) > 2000, f"{url} 切片异常({len(text)})"
        upsert(conn, url, date, title, text)
        ok = ensure_audio(url, text)
        print(f"  {title} {len(text)}字 {'OK' if ok else 'FAIL'}", flush=True)
    conn.commit()
    print("名人全文共:", conn.execute("SELECT COUNT(*) FROM news_items WHERE category='名人'").fetchone()[0], "篇")


if __name__ == "__main__":
    if "--stanford" in sys.argv:
        stanford()
    if "--full" in sys.argv:
        full_others()
    if not sys.argv[1:]:
        print("用法: add_famous_full.py --stanford | --full")
