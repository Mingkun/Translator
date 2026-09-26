#!/usr/bin/env python3
"""用 faster-whisper 词级时间戳重建演讲/播客的词级时间轴（分段版）。

用途：段落级时间戳插值漂移时，对音频转写取词级时间，
再与文章文本词做单调模糊对齐，生成 data/news/<hash>.json。
分段转写（默认 14 分钟/段）避免整段 139 分钟音频把内存吃爆。

用法：python3 -u scripts/align_whisper_timeline.py <news_item_id>
"""
import sys
import os
import json
import sqlite3
import shutil
import hashlib
import math

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHUNK = 840  # 每段秒数（14 分钟）


def norm(s: str) -> str:
    return ''.join(c for c in s.lower() if c.isalnum())


def audio_duration(path: str) -> float:
    from faster_whisper.audio import decode_audio
    return len(decode_audio(path, sampling_rate=16000)) / 16000.0


def main(item_id: int) -> None:
    db = sqlite3.connect(os.path.join(ROOT, 'data', 'cache.db'))
    row = db.execute("SELECT url, text FROM news_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        print('item not found:', item_id, flush=True)
        sys.exit(1)
    url, text = row
    h = hashlib.sha1(url.encode()).hexdigest()[:16]
    jf = os.path.join(ROOT, 'data', 'news', h + '.json')
    mp3 = os.path.join(ROOT, 'data', 'news', h + '.mp3')
    if not os.path.exists(mp3):
        print('audio missing:', mp3, flush=True)
        sys.exit(1)

    aw = text.split()
    print('article words:', len(aw), flush=True)

    import gc
    import wave
    import numpy as np
    from faster_whisper.audio import decode_audio
    print('decoding audio...', flush=True)
    audio = decode_audio(mp3, sampling_rate=16000)
    dur = len(audio) / 16000.0
    print('audio duration:', round(dur, 1), 's =', round(dur / 60, 1), 'min', flush=True)

    tmpdir = '/tmp/see_align'
    os.makedirs(tmpdir, exist_ok=True)
    chunks = []
    for ci, start in enumerate(range(0, math.ceil(dur), CHUNK)):
        a = int(start * 16000)
        b = min(len(audio), int((start + CHUNK) * 16000))
        cp = os.path.join(tmpdir, 'c%02d.wav' % ci)
        with wave.open(cp, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes((np.clip(audio[a:b], -1, 1) * 32767).astype('<i2').tobytes())
        chunks.append((start, cp))
    del audio
    gc.collect()
    print('chunks written:', len(chunks), flush=True)

    from faster_whisper import WhisperModel
    model = WhisperModel('base', device='cpu', compute_type='int8', cpu_threads=2)

    wwords = []
    for start, cp in chunks:
        segments, _ = model.transcribe(
            cp, word_timestamps=True, language='en',
            beam_size=1, condition_on_previous_text=False, vad_filter=True,
        )
        n = 0
        for seg in segments:
            for w in (seg.words or []):
                nw = norm(w.word)
                if nw:
                    wwords.append((nw, w.start + start, w.end + start))
                    n += 1
        rss = open('/proc/self/status').read().split('VmRSS:')[1].split()[0]
        print(f'chunk @{start//60}min: +{n} words (total {len(wwords)}) rss={rss}kB', flush=True)
        os.remove(cp)
    shutil.rmtree(tmpdir, ignore_errors=True)

    # 比例定位 + 就近搜索的单调对齐（防指针卡死）
    with open('/tmp/see_align_wwords.json', 'w', encoding='utf-8') as f:
        json.dump([[w[0], w[1], w[2]] for w in wwords], f, ensure_ascii=False)

    Nw = len(wwords)
    Na = len(aw)
    prop = (Nw / Na) if Na else 1.0
    RADIUS = 80
    offsets = [0]
    for dd in range(1, RADIUS + 1):
        offsets.extend([dd, -dd])
    out = [None] * Na
    last_k = -1
    last_t = 0.0
    anorm = [norm(a) for a in aw]
    wnorm = [w[0] for w in wwords]
    for i in range(Na):
        na = anorm[i]
        if not na:
            continue
        center = int(i * prop)
        for off in offsets:
            k = center + off
            if k < 0 or k >= Nw or k <= last_k:
                continue
            nw = wnorm[k]
            if na == nw or (min(len(na), len(nw)) >= 4 and (na in nw or nw in na)):
                t = wwords[k][1]
                out[i] = t
                last_k = k
                last_t = t
                break
    matched = sum(1 for x in out if x is not None)
    print('matched:', matched, '/', len(aw), f'({matched * 100 // max(1, len(aw))}%)', flush=True)
    for dec in range(10):
        lo, hi = Na * dec // 10, Na * (dec + 1) // 10
        seg = out[lo:hi]
        print(f'  decile {dec}: anchors {sum(1 for x in seg if x is not None)}/{hi-lo}', flush=True)

    # 插值补洞
    last = -1
    for i in range(len(out)):
        if out[i] is not None:
            if last < 0:
                for j in range(i):
                    out[j] = out[i]
            else:
                gap = i - last
                for j in range(last + 1, i):
                    out[j] = out[last] + (out[i] - out[last]) * (j - last) / gap
            last = i
    if last >= 0 and last < len(out) - 1:
        for j in range(last + 1, len(out)):
            out[j] = min(out[last] + 0.35 * (j - last), dur - 0.3)
    overshoot = sum(1 for x in out if x > dur - 0.3)
    print('overshoot words:', overshoot, '| max t:', round(max(out), 1), '| dur:', round(dur, 1), flush=True)

    marks = [{'t': round(t, 2), 'd': 0.3, 'w': w} for t, w in zip(out, aw)]
    for i in range(len(marks) - 1):
        marks[i]['d'] = max(0.1, min(3.0, round(marks[i + 1]['t'] - marks[i]['t'], 2)))
    marks[-1]['d'] = 0.5

    if os.path.exists(jf):
        shutil.copy(jf, jf + '.bak')
    with open(jf, 'w', encoding='utf-8') as f:
        json.dump(marks, f, ensure_ascii=False)
    print('written:', jf, 'marks:', len(marks), flush=True)


if __name__ == '__main__':
    main(int(sys.argv[1]))
