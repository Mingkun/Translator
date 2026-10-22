#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「名人·乔布斯·斯坦福演讲」条目的 TTS 音频替换为本人原声（节选窗口）。

源：data/famous_src/Stanford2005_full.mp3（archive.org 公共镜像，16:57 全程）
步骤：faster-whisper 逐词时间戳 → 定位两段名段窗口 → ffmpeg 裁剪/拼接
     → 生成 {t,d,w} 跟读词表 → 覆盖 data/news/<hash>.mp3/.json（TTS 版留 .bak）
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from fetch_daily_news import NEWS, urlhash  # noqa: E402

import imageio_ffmpeg  # noqa: E402
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

FULL = ROOT / "data" / "famous_src" / "Stanford2005_full.mp3"
URL = "famous:jobs-stanford-2005"
H = urlhash(URL)
OUT_MP3 = NEWS / f"{H}.mp3"
OUT_JSON = NEWS / f"{H}.json"


def norm(w):
    return "".join(c for c in w.lower() if c.isalnum())


def find_seq(words, seq, start=0):
    """在词流中找连续子序列，返回起始下标或 -1。"""
    n, m = len(words), len(seq)
    for i in range(start, n - m + 1):
        if [norm(words[i + k]["w"]) for k in range(m)] == seq:
            return i
    return -1


def main():
    cache = ROOT / "data" / "famous_src" / "Stanford2005_words.json"
    if cache.is_file():
        words = json.loads(cache.read_text(encoding="utf-8"))
        print(f"词流缓存 {len(words)} 词")
    else:
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=2)
        segments, info = model.transcribe(str(FULL), word_timestamps=True, vad_filter=True)
        words = []
        for seg in segments:
            for w in seg.words or []:
                if w.word.strip():
                    words.append({"w": w.word.strip(), "t": round(w.start, 3), "d": round(w.end - w.start, 3)})
        cache.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        print(f"全篇词数 {len(words)}，时长 {info.duration:.0f}s")

    i1 = find_seq(words, ["your", "time", "is", "limited"])
    j1 = find_seq(words, ["everything", "else", "is", "secondary"], i1)
    i2 = find_seq(words, ["when", "i", "was", "young"], j1)
    j2 = find_seq(words, ["signed", "off"], i2)
    assert i1 >= 0 and j1 > i1 and i2 > j1 and j2 > i2, f"锚点失败 {i1} {j1} {i2} {j2}"
    w1s, w1e = words[i1]["t"], words[j1]["t"] + words[j1]["d"]
    w2s, w2e = words[i2]["t"], words[j2]["t"] + words[j2]["d"]
    gap = w2s - w1e
    print(f"块1 [{w1s:.1f}-{w1e:.1f}s] 块2 [{w2s:.1f}-{w2e:.1f}s] 间隔 {gap:.1f}s")

    GAP_SIL = 0.8

    def run(args):
        r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error"] + args, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-300:])

    tmp1, tmp2, sil = "/tmp/_f1.mp3", "/tmp/_f2.mp3", "/tmp/_sil.mp3"
    run(["-ss", f"{w1s:.2f}", "-to", f"{w1e:.2f}", "-i", str(FULL), "-c:a", "libmp3lame", "-q:a", "4", tmp1])
    run(["-ss", f"{w2s:.2f}", "-to", f"{w2e:.2f}", "-i", str(FULL), "-c:a", "libmp3lame", "-q:a", "4", tmp2])
    run(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", str(GAP_SIL), "-c:a", "libmp3lame", "-q:a", "9", sil])
    run(["-i", f"concat:{tmp1}|{sil}|{tmp2}", "-c:a", "libmp3lame", "-q:a", "4", "-f", "mp3", str(OUT_MP3) + ".new.mp3"])

    d1 = w1e - w1s
    marks = []
    for k in range(i1, j1 + 1):
        marks.append({"t": round(words[k]["t"] - w1s, 3), "d": words[k]["d"], "w": words[k]["w"]})
    for k in range(i2, j2 + 1):
        marks.append({"t": round(words[k]["t"] - w2s + d1 + GAP_SIL, 3), "d": words[k]["d"], "w": words[k]["w"]})

    Path(str(OUT_JSON) + ".new").write_text(json.dumps(marks, ensure_ascii=False), encoding="utf-8")
    for dst, ext in ((OUT_MP3, ".new.mp3"), (OUT_JSON, ".new")):
        src = Path(str(dst) + ext)
        if dst.is_file():
            dst.rename(str(dst) + ".bak")
        src.rename(dst)
    print(f"替换完成: {OUT_MP3.name} {OUT_MP3.stat().st_size}B, 词表 {len(marks)} 词")


if __name__ == "__main__":
    main()
