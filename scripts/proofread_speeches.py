#!/usr/bin/env python3
# coding: utf-8
"""Proofread speech transcripts: collapse ASR duplication artifacts.

Cleans: adjacent duplicate words, back-to-back repeated phrases (2-10 words),
consecutive identical sentences, consecutive same-speaker labels (devday).
Rebuilds word timelines in sync. Idempotent.
"""
import re, html, json, sqlite3, sys
from pathlib import Path

ROOT = Path('/work/Translator')
DATA = ROOT / 'data' / 'news'
SPEECHES = [
    # nid, hash, source html, kind
    (69, 'bda598bf38c6ac31', '/tmp/sh_gtc.html', 'scribehawk', 8336),
    (70, '931c719b91a09fa1', '/tmp/vtb.html', 'videotobe', 3159),
    (71, '00ebf5518db488da', '/tmp/sh_io.html', 'scribehawk', 6675),
]
KEEP_REPEAT = {'very', 'so', 'no', 'yeah', 'yep', 'okay', 'ok', 'right', 'wow', 'boom'}


def norm(w):
    return re.sub(r"[.,!?;:\"'()\u2019\u201c\u201d]", '', w).lower()


def to_sec(s):
    p = [float(x) for x in s.split(':')]
    while len(p) < 3:
        p = [0] + p
    return p[0] * 3600 + p[1] * 60 + p[2]


def parse_scribehawk(path):
    s = open(path, encoding='utf-8', errors='ignore').read()
    s = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', s, flags=re.S)
    t = re.sub(r'<[^>]+>', ' ', s)
    t = html.unescape(t)
    t = re.sub(r'\s+', ' ', t)
    m = re.search(r'\[\d{1,2}:\d{2}:\d{2}\]', t)
    t = t[m.start():] if m else t
    for cut in ['Paste a URL', 'Powered by ScribeHawk', 'Transcribe Any Video', 'Yahoo Finance', 'Latest Transcripts', 'Related', 'Transcribe Anything', 'Foxconn fine-tunes']:
        idx = t.find(cut)
        if idx > 0:
            t = t[:idx]
    parts = re.split(r'\[(\d{1,2}:\d{2}:\d{2})\]', t)
    segs = []
    for i in range(1, len(parts) - 1, 2):
        ws = parts[i + 1].split()
        if ws:
            segs.append((to_sec(parts[i]), None, ws))
    return segs


def parse_videotobe(path):
    s = open(path, encoding='utf-8', errors='ignore').read()
    parts = re.findall(r'class="(timestamp-text|speaker-name|transcript-text)[^"]*"[^>]*>(.*?)</span>', s, re.S)
    segs = []
    cur = None
    buf = []
    label = []

    def flush():
        if buf or label:
            segs.append((cur or 0, label or None, buf[:]))

    for kind, txt in parts:
        txt = html.unescape(re.sub(r'<!--.*?-->', '', txt)).strip()
        if not txt:
            continue
        if kind == 'timestamp-text':
            flush()
            buf, label = [], []
            cur = to_sec(txt)
        elif kind == 'speaker-name':
            label = txt.split()
        else:
            buf += txt.split()
    flush()
    return segs


def emit_words(segs, total):
    """Flatten segments to (word, t) with interpolation; merge same-speaker labels."""
    out = []
    last_speaker = None
    for si, (t0, speaker, ws) in enumerate(segs):
        t1 = segs[si + 1][0] if si + 1 < len(segs) else max(total, t0 + 5)
        span = max(t1 - t0, 0.4)
        n = len(ws)
        skip_label = 0
        if speaker is not None:
            if speaker == last_speaker:
                skip_label = len(speaker)  # same speaker consecutively: drop label words
        base = out if speaker is None or skip_label else None
        for j, w in enumerate(ws):
            if skip_label and j < skip_label:
                continue
            out.append([w, round(t0 + span * j / max(n, 1), 2), round(span / max(n, 1), 2), speaker if j == 0 and speaker else None])
        if speaker is not None:
            last_speaker = speaker
    return out


def clean(words):
    """words: list of [w, t, d, speaker_marker]. Returns cleaned list."""
    # 1) consecutive identical sentences -> keep first
    def sentence_ranges(ws):
        ranges = []
        start = 0
        for i, w in enumerate(ws):
            if re.search(r'[.!?]$', w[0]):
                ranges.append((start, i))
                start = i + 1
        if start < len(ws):
            ranges.append((start, len(ws) - 1))
        return ranges

    def sent_key(ws, a, b):
        return ' '.join(norm(w[0]) for w in ws[a:b + 1] if norm(w[0]))

    keep = [True] * len(words)
    prev_key = None
    prev_end = -1
    for a, b in sentence_ranges(words):
        key = sent_key(words, a, b)
        if key and key == prev_key and (b - a) >= 0 and len(key) > 3:
            for i in range(a, b + 1):
                keep[i] = False
        else:
            prev_key = key
    words = [w for i, w in enumerate(words) if keep[i]]

    # 2) back-to-back repeated phrases (n = 2..10)
    changed = True
    while changed:
        changed = False
        i = 0
        ws = words
        n_len = len(ws)
        while i < n_len:
            for n in range(min(10, (n_len - i) // 2), 1, -1):
                a = [norm(w[0]) for w in ws[i:i + n]]
                if not all(a):
                    continue
                b = [norm(w[0]) for w in ws[i + n:i + 2 * n]]
                if a == b:
                    del ws[i + n:i + 2 * n]
                    changed = True
                    n_len -= n
                    break
            i += 1
        words = ws

    # 3) adjacent duplicate words (keep whitelisted double emphasis)
    out = []
    for w in words:
        if out and norm(w[0]) and norm(out[-1][0]) == norm(w[0]) and norm(w[0]) not in KEEP_REPEAT:
            continue
        out.append(w)
    return out


def build(segs, total):
    words = emit_words(segs, total)
    before = len(words)
    words = clean(words)
    return words, before


def main():
    conn = sqlite3.connect(ROOT / 'data' / 'cache.db')
    for nid, h, src, kind, total in SPEECHES:
        segs = parse_scribehawk(src) if kind == 'scribehawk' else parse_videotobe(src)
        words, before = build(segs, total)
        # text reconstruction
        parts = []
        cur_line = []
        for w in words:
            if w[3] is not None:  # speaker marker (first word of a kept label)
                if cur_line:
                    parts.append(' '.join(cur_line))
                    cur_line = []
                cur_line.append(w[3][0] + ':' if False else w[0])
                # speaker label reconstructed below instead
            cur_line.append(w[0])
        text = ' '.join(cur_line)
        # devday: rebuild with speaker labels properly
        if kind == 'videotobe':
            blocks = []
            cur = []
            cur_sp = None
            for w in words:
                if w[3] is not None and w[3] != cur_sp:
                    if cur:
                        blocks.append((cur_sp, ' '.join(cur)))
                        cur = []
                    cur_sp = w[3]
                cur.append(w[0])
            if cur:
                blocks.append((cur_sp, ' '.join(cur)))
            text = '\n\n'.join(((' '.join(x.rstrip(':') for x in sp)) + ': ' + txt) if sp else txt for sp, txt in blocks)
        else:
            text = ' '.join(w[0] for w in words)
        marks = [{'t': w[1], 'd': w[2], 'w': w[0]} for w in words]
        conn.execute('UPDATE news_items SET text=? WHERE id=?', (text, nid))
        json.dump(marks, open(DATA / f'{h}.json', 'w'), ensure_ascii=False)
        print(f'id {nid}: {before} -> {len(words)} words (-{before - len(words)})')
    conn.commit()
    print('done')


main()
