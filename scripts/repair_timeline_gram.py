#!/usr/bin/env python3
"""离线修复：用落盘的 whisper 词表(/tmp/see_align_wwords.json)做 4-gram 窗口重对齐，免重转写。"""
import json, sqlite3

def norm(s):
    return ''.join(c for c in s.lower() if c.isalnum())

ROOT = '/work/Translator'
db = sqlite3.connect(f'{ROOT}/data/cache.db')
text = db.execute("select text from news_items where id=69").fetchone()[0]
aw = text.split()
an = [norm(w) for w in aw]
Na = len(an)
ww = json.load(open('/tmp/see_align_wwords.json'))
wn = [x[0] for x in ww]
Nw = len(wn)
prop = Nw / Na
print(f'article {Na} words, whisper {Nw} words')

def wmatch(a, b):
    return a == b or (min(len(a), len(b)) >= 4 and (a in b or b in a))

def score4(k, i):
    c = 0; wi = i; kj = k
    while wi < i + 4 and kj < k + 6 and kj < Nw:
        if an[wi] and wmatch(an[wi], wn[kj]):
            c += 1; wi += 1
        kj += 1
    return c

out = [None] * Na
last_k = -1
for i in range(Na):
    if not an[i]:
        continue
    center = int(i * prop)
    best_k, best_s = -1, 0
    for off in range(-60, 61):
        k = center + off
        if k < 0 or k >= Nw or k <= last_k:
            continue
        s = score4(k, i)
        if s > best_s:
            best_k, best_s = k, s
    if best_k >= 0 and best_s >= 3:
        t = ww[best_k][1]
        if out and any(x is not None and x > t + 0.25 for x in out[max(0, i-200):i]):
            continue
        out[i] = t
        last_k = best_k
matched = sum(1 for x in out if x is not None)
print('matched:', matched, f'({matched*100//Na}%)')

last = -1
for i in range(Na):
    if out[i] is not None:
        if last < 0:
            for j in range(i): out[j] = out[i]
        else:
            gap = i - last
            for j in range(last+1, i):
                out[j] = out[last] + (out[i]-out[last])*(j-last)/gap
        last = i
dur = 8335.6
if last >= 0 and last < Na-1:
    for j in range(last+1, Na):
        out[j] = min(out[last] + 0.35*(j-last), dur-0.3)

jumps = []
for i in range(1, Na):
    if out[i] - out[i-1] > 8:
        dens = sum(1 for w in ww if out[i-1] <= w[1] <= out[i])
        jumps.append((i, round(out[i-1],1), round(out[i],1), dens))
print('jumps>8s:', len(jumps))
for j in jumps[:12]:
    kind = 'REAL-GAP(whisper empty)' if j[3] <= 2 else 'CHECK'
    print(' ', j, kind)

marks = [{'t': round(t,2), 'd': 0.3, 'w': w} for t, w in zip(out, aw)]
for i in range(len(marks)-1):
    marks[i]['d'] = max(0.1, min(3.0, round(marks[i+1]['t']-marks[i]['t'], 2)))
marks[-1]['d'] = 0.5
json.dump(marks, open(f'{ROOT}/data/news/bda598bf38c6ac31.json','w'), ensure_ascii=False)
print('written. spot check:')
for i in [40,44,46,47,48,49,50,51,55,60,15549,15550,15551,15552]:
    print(' ', i, round(out[i],1), repr(aw[i][:24]))
