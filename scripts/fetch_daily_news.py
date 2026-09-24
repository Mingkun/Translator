#!/usr/bin/env python3
"""Daily CNN news fetcher: multiple articles/day across categories, TTS audio + word timings."""
import asyncio, hashlib, json, re, sqlite3, time, urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data' / 'cache.db'
NEWS = ROOT / 'data' / 'news'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
PER_DAY = 5
SKIP_PREFIX = ('read more', 'related article', 'this story', 'sign up', 'listen to',
               'click here', 'follow cnn', 'see more', 'the-cnn', 'contributed')
CATEGORY_MAP = {
    'tech': '科技', 'science': '科技', 'business': '经济', 'politics': '政治',
    'health': '健康', 'style': '生活', 'travel': '生活', 'entertainment': '生活', 'media': '生活',
    'sport': '体育', 'football': '体育',
    'world': '国际', 'europe': '国际', 'americas': '国际', 'asia': '国际', 'middleeast': '国际', 'africa': '国际',
    'us': '国际',
}
CAT_ORDER = ['科技', '经济', '国际', '政治', '健康', '生活', '体育', '日常']
PODCASTS = [
    ('CNN 5 Things', 'https://feeds.megaphone.fm/WMHY2007701094'),
]


def urlhash(u):
    return hashlib.sha1(u.encode('utf-8')).hexdigest()[:16]


def cat_of(u):
    m = re.search(r'/20\d\d/\d\d/\d\d/([^/]+)/', u)
    if not m:
        return '日常'
    return CATEGORY_MAP.get(m.group(1), '日常')


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    return urllib.request.urlopen(req, timeout=25).read().decode('utf-8', 'ignore')


def strip_tags(s):
    s = re.sub(r'<[^>]+>', ' ', s)
    s = s.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&#39;', "'").replace('&quot;', '"').replace('&lt;', '<').replace('&gt;', '>')
    return re.sub(r'\s+', ' ', s).strip()


def homepage_candidates():
    html = fetch('https://lite.cnn.com')
    links = re.findall(r'<a[^>]+href="(/20\d\d/\d\d/\d\d/[^"]+)"[^>]*>\s*([^<]{10,150}?)\s*</a>', html)
    seen, out = set(), []
    for u, _t in links:
        if u in seen:
            continue
        seen.add(u)
        if re.search(r'/video/|/live/|/photos/|/videos/|/interactive/', u):
            continue
        out.append('https://lite.cnn.com' + u)
    return out


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


def tts(text, audio_path, meta_path):
    import edge_tts

    async def run():
        com = edge_tts.Communicate(text, 'en-US-AndrewNeural', boundary='WordBoundary')
        audio = bytearray()
        marks = []
        async for chunk in com.stream():
            if chunk['type'] == 'audio':
                audio.extend(chunk['data'])
            elif chunk['type'] == 'WordBoundary':
                marks.append({'t': round(chunk['offset'] / 1e7, 3),
                              'd': round(chunk['duration'] / 1e7, 3),
                              'w': chunk['text']})
        Path(audio_path).write_bytes(bytes(audio))
        Path(meta_path).write_text(json.dumps(marks), encoding='utf-8')

    asyncio.run(run())


def ensure_schema(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS news_items ('
                 'id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, title TEXT, '
                 'category TEXT, url TEXT UNIQUE, text TEXT, created_at REAL, audio_url TEXT DEFAULT \'\')')


def ensure_audio_url_col(conn):
    cols = [r[1] for r in conn.execute('PRAGMA table_info(news_items)')]
    if 'audio_url' not in cols:
        conn.execute("ALTER TABLE news_items ADD COLUMN audio_url TEXT DEFAULT ''")
        conn.commit()


def transcribe_one(mp3_url, words_path, model):
    """Download podcast audio (kept locally), generate word timestamps via faster-whisper. Returns transcript text."""
    h = urlhash(mp3_url)
    local = NEWS / (h + '.mp3')
    if not (local.is_file() and local.stat().st_size > 10000):
        req = urllib.request.Request(mp3_url, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=180) as r, open(local, 'wb') as f:
            f.write(r.read())
    segments, info = model.transcribe(str(local), word_timestamps=True, vad_filter=True)
    words, parts = [], []
    for seg in segments:
        for w in (seg.words or []):
            ww = (w.word or '').strip()
            if ww:
                words.append({'t': round(float(w.start), 2), 'd': round(float(w.end - w.start), 2), 'w': ww})
        parts.append(seg.text.strip())
    Path(words_path).write_text(json.dumps(words), encoding='utf-8')
    return ' '.join(parts)


def fetch_podcasts(conn):
    import email.utils
    ensure_audio_url_col(conn)
    model = None
    for name, feed in PODCASTS:
        try:
            rss = fetch(feed)
            items = re.findall(r'<item>(.*?)</item>', rss, re.S)[:5]
            added = 0
            for it in items:
                t = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', it, re.S)
                enc = re.search(r'<enclosure[^>]+url="([^"]+)"', it)
                pub = re.search(r'<pubDate>([^<]+)</pubDate>', it)
                desc = re.search(r'<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>', it, re.S)
                if not (t and enc):
                    continue
                title = strip_tags(t.group(1))[:120]
                mp3 = enc.group(1).replace('&amp;', '&')
                date = datetime.now().strftime('%Y-%m-%d')
                if pub:
                    try:
                        date = email.utils.parsedate_to_datetime(pub.group(1).strip()).strftime('%Y-%m-%d')
                    except Exception:
                        pass
                body = strip_tags(desc.group(1)) if desc else ''
                text = (title + '. ' + body)[:900]
                cur = conn.execute('INSERT OR IGNORE INTO news_items (date,title,category,url,text,created_at,audio_url) VALUES (?,?,?,?,?,?,?)',
                                   (date, title, '播客', mp3, text, time.time(), mp3))
                if cur.rowcount > 0:
                    added += 1
                    print('PODCAST [%s] %s' % (date, title[:60]))
                h = urlhash(mp3)
                jp = NEWS / (h + '.json')
                if cur.rowcount > 0 or not jp.is_file():
                    try:
                        if model is None:
                            from faster_whisper import WhisperModel
                            print('loading whisper base model...', flush=True)
                            model = WhisperModel('base', device='cpu', compute_type='int8', cpu_threads=2)
                        transcript = transcribe_one(mp3, jp, model)
                        if transcript:
                            conn.execute('UPDATE news_items SET text=? WHERE url=?', (transcript[:30000], mp3))
                            conn.commit()
                            print('TRANSCRIPT %s (%d chars)' % (title[:40], len(transcript)))
                    except Exception as e:
                        print('TRANSCRIPT FAIL', title[:40], str(e)[:70])
            conn.commit()
            if not added:
                print('PODCAST %s: no new episodes' % name)
        except Exception as e:
            print('PODCAST FAIL', name, str(e)[:70])


def migrate_legacy(conn):
    has = conn.execute("SELECT name FROM sqlite_master WHERE name='news_daily'").fetchone()
    if not has:
        return
    for date, title, url, text in conn.execute('SELECT date, title, url, text FROM news_daily'):
        try:
            conn.execute('INSERT OR IGNORE INTO news_items (date,title,category,url,text,created_at) VALUES (?,?,?,?,?,?)',
                         (date, title, cat_of(url), url, text, time.time()))
            h = urlhash(url)
            old_a, old_j = NEWS / (date + '.mp3'), NEWS / (date + '.json')
            if old_a.is_file() and not (NEWS / (h + '.mp3')).is_file():
                old_a.rename(NEWS / (h + '.mp3'))
            if old_j.is_file() and not (NEWS / (h + '.json')).is_file():
                old_j.rename(NEWS / (h + '.json'))
        except Exception:
            pass
    conn.commit()


def main():
    NEWS.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=15)
    ensure_schema(conn)
    conn.commit()
    migrate_legacy(conn)
    ensure_audio_url_col(conn)
    fetch_podcasts(conn)
    have = set(r[0] for r in conn.execute('SELECT url FROM news_items'))
    cands = [u for u in homepage_candidates() if u not in have]
    by_cat = {}
    for u in cands:
        by_cat.setdefault(cat_of(u), []).append(u)
    picked = []
    while len(picked) < PER_DAY:
        added = False
        for c in CAT_ORDER:
            bucket = by_cat.get(c) or []
            while bucket:
                u = bucket.pop(0)
                if u not in picked:
                    picked.append(u)
                    added = True
                    break
            if len(picked) >= PER_DAY:
                break
        if not added:
            break
    today = datetime.now().strftime('%Y-%m-%d')
    ok = 0
    for u in picked:
        try:
            title, text = extract(u)
            if len(text) < 200:
                print('SKIP short', u[:60])
                continue
            h = urlhash(u)
            ap, jp = NEWS / (h + '.mp3'), NEWS / (h + '.json')
            if not (ap.is_file() and jp.is_file()):
                tts(text, ap, jp)
            if not (ap.is_file() and ap.stat().st_size > 1000):
                print('SKIP tts', u[:60])
                continue
            conn.execute('INSERT OR IGNORE INTO news_items (date,title,category,url,text,created_at) VALUES (?,?,?,?,?,?)',
                         (today, title, cat_of(u), u, text, time.time()))
            conn.commit()
            ok += 1
            print('ADDED [%s] %s' % (cat_of(u), title[:60]))
        except Exception as e:
            print('FAIL', u[:60], str(e)[:80])
    total = conn.execute('SELECT COUNT(*) FROM news_items').fetchone()[0]
    conn.close()
    print('DONE +%d today, %d total' % (ok, total))


if __name__ == '__main__':
    main()
