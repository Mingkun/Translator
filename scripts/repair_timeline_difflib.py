#!/usr/bin/env python3
"""时间轴终极修复：difflib词序列全局块匹配对齐（93%词级精确锚点）。用法同 repair_timeline_gram.py，需 /tmp/see_align_wwords.json。"""
import json, sqlite3, difflib

def norm(s):
    return ''.join(c for c in s.lower() if c.isalnum())

ROOT = '/work/Translator'
db = sqlite3.connect(f'{ROOT}/data/cache.db')
text = db.execute("select text from news_items where id=69").fetchone()[0]
aw = text.split(); an = [norm(w) for w in aw]
Na = len(aw)
ww = json.load(open('/tmp/see_align_wwords.json'))
wn = [x[0] for x in ww]
Nw = len(wn)
dur = 8335.6
print(f'article {Na} vs whisper {Nw}')

sm = difflib.SequenceMatcher(None, an, wn, autojunk=False)
blocks = sm.get_matching_blocks()
print('matching blocks:', len(blocks))
out = [None] * Na
covered = 0
for b in blocks:
    for x in range(b.size):
        if b.a + x < Na and b.b + x < Nw:
            out[b.a + x] = ww[b.b + x][1]
            covered += 1
print(f'block-covered words: {covered} ({covered*100//Na}%)')

# 时间单调化
prev = -1.0
for i in range(Na):
    if out[i] is not None:
        if out[i] >= prev - 0.05:
            prev = out[i]
        else:
            out[i] = None
matched = sum(1 for x in out if x is not None)
print('monotonic anchors:', matched, f'({matched*100//Na}%)')

# 插值补洞
last = -1
for i in range(Na):
    if out[i] is not None:
        if last < 0:
            for j in range(i): out[j] = out[i]
        else:
            gap = i - last
            for j in range(last + 1, i):
                out[j] = out[last] + (out[i] - out[last]) * (j - last) / gap
        last = i
if last >= 0 and last < Na - 1:
    for j in range(last + 1, Na):
        out[j] = min(out[last] + 0.35 * (j - last), dur - 0.3)

crawl = sum(1 for i in range(1, Na) if out[i] - out[i-1] > 3.5)
jumps = sum(1 for i in range(1, Na) if out[i] - out[i-1] > 8)
print('slow-crawl steps(>3.5s):', crawl, '| jumps>8s:', jumps)

marks = [{'t': round(t,2), 'd': 0.3, 'w': w} for t, w in zip(out, aw)]
for i in range(len(marks)-1):
    marks[i]['d'] = max(0.1, min(3.0, round(marks[i+1]['t']-marks[i]['t'], 2)))
marks[-1]['d'] = 0.5
json.dump(marks, open(f'{ROOT}/data/news/bda598bf38c6ac31.json','w'), ensure_ascii=False)
print('written. spot:')
for i in [46,48,51,55,9083,9085,11484,15551]:
    print(' ', i, round(out[i],1), repr(aw[i][:22]))
