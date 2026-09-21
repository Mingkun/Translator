#!/usr/bin/env python3
"""Daily CNN news fetcher for Translator: pick one article, extract text, generate TTS audio."""
import asyncio, re, sqlite3, sys, time, urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / 'data' / 'cache.db'
AUDIO_DIR = ROOT / 'data' / 'news'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
SKIP_PREFIX = ('read more', 'related article', 'this story', 'sign up', 'listen to',
               'click here', 'follow cnn', 'see more', 'the-cnn', 'contributed')


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    return urllib.request.urlopen(req, timeout=25).read().decode('utf-8', 'ignore')


def strip_tags(s):
    s = re.sub(r'<[^>]+>', ' ', s)
    s = s.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&#39;', "'").replace('&quot;', '"').replace('&lt;', '<').replace('&gt;', '>')
    return re.sub(r'\s+', ' ', s).strip()


def pick_article():
    html = fetch('https://lite.cnn.com')
    links = re.findall(r'<a[^>]+href="(/20\d\d/\d\d/\d\d/[^"]+)"[^>]*>\s*([^<]{10,150}?)\s*</a>', html)
    seen, cands = set(), []
    for u, t in links:
        if u in seen:
            continue
        seen.add(u)
        if re.search(r'/video/|/live/|/photos/|/videos/|/interactive/', u):
            continue
        cands.append(('https://lite.cnn.com' + u, t))
    if not cands:
        raise RuntimeError('no article candidates')
    idx = int(datetime.now().strftime('%Y%m%d')) % min(len(cands), 12)
    return cands[idx]


def extract(url):
    html = fetch(url)
    html = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.S | re.I)
    html = re.sub(r'<!--.*?-->', ' ', html, flags=re.S)
    tm = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
    title = strip_tags(tm.group(1)) if tm else ''
    if not title:
        tm = re.search(r'<title>(.*?)</title>', html, re.S)
        title = strip_tags(tm.group(1)).rsplit('|', 1)[0].strip() if tm else 'CNN News'
    paras = re.findall(r'<p[^>]*>(.*?)</p>', html, re.S)
    out, total = [], 0
    for p in paras:
        t = strip_tags(p)
        if len(t) < 50:
            continue
        if '{' in t or '}' in t or t.count(';') > 3 or t.count('px') > 2:
            continue
        low = t.lower()
        if any(low.startswith(x) for x in SKIP_PREFIX):
            continue
        t = re.sub(r'^CNN\s*[—–-]\s*', '', t)
        out.append(t)
        total += len(t)
        if total > 950:
            break
    return title, ' '.join(out)


def tts(text, path):
    import edge_tts

    async def run():
        c = edge_tts.Communicate(text, 'en-US-GuyNeural')
        await c.save(str(path))

    asyncio.run(run())


def main():
    today = datetime.now().strftime('%Y-%m-%d')
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute('CREATE TABLE IF NOT EXISTS news_daily (date TEXT PRIMARY KEY, title TEXT, url TEXT, text TEXT, created_at REAL)')
    row = conn.execute('SELECT title, text FROM news_daily WHERE date=?', (today,)).fetchone()
    audio = AUDIO_DIR / (today + '.mp3')
    if row and audio.is_file() and audio.stat().st_size > 1000:
        print('EXISTS', today, '|', row[0][:60])
        conn.close()
        return
    url, _t = pick_article()
    title, text = extract(url)
    if len(text) < 200:
        raise RuntimeError('article text too short: %d' % len(text))
    tts(text, audio)
    if not audio.is_file() or audio.stat().st_size < 1000:
        raise RuntimeError('tts output empty')
    conn.execute('INSERT OR REPLACE INTO news_daily (date, title, url, text, created_at) VALUES (?,?,?,?,?)',
                 (today, title, url, text, time.time()))
    conn.commit()
    conn.close()
    print('OK', today, '|', title[:70], '|', len(text), 'chars |', audio.stat().st_size, 'B')


if __name__ == '__main__':
    main()
