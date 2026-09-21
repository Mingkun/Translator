import json, sqlite3, time, urllib.request

DB = '/work/Translator/data/cache.db'
key = json.loads(open('/root/.openclaw/secrets.json').read())['models']['providers']['zai']['apiKey']
auth = 'Bearer ' + key
TARGET = 300

genres = [
    'action', 'sci-fi', 'romance', 'comedy', 'drama', 'thriller', 'animation',
    'war', 'crime', 'fantasy', 'horror', 'western', 'musical', 'noir', 'superhero',
    'adventure', 'mystery', 'sports', 'biography', 'historical', 'disaster',
    'spy', 'courtroom', 'road trip', 'heist', 'zombie', 'time travel', 'dystopian',
]

def gen(topic, sub):
    prompt = (f'List 15 famous {topic} movie quotes (variety #{sub}). '
              f'JSON array only: [{{"en":"quote","zh":"chinese","s":"movie name"}}] '
              f'Real famous quotes, no repeats across batches.')
    payload = json.dumps({
        'model': 'glm-4.6v',
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 1.0,
        'max_tokens': 3000,
    }).encode()
    req = urllib.request.Request('https://api.z.ai/api/coding/paas/v4/chat/completions',
        data=payload, method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': ***})
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode())
    content = data['choices'][0]['message']['content']
    s = content.find('[')
    e = content.rfind(']')
    if s < 0 or e <= s:
        return []
    return json.loads(content[s:e+1])

def save(items):
    conn = sqlite3.connect(DB, timeout=15)
    added = 0
    for it in items:
        en = str(it.get('en') or it.get('english') or it.get('quote') or '').strip()
        zh = str(it.get('zh') or it.get('chinese') or '').strip()
        src = str(it.get('s') or it.get('source') or it.get('movie') or '').strip()
        if not en or len(en) < 5:
            continue
        cur = conn.execute(
            'INSERT OR IGNORE INTO movie_quotes (en, zh, source, word, created_at) VALUES (?,?,?,?,?)',
            (en, zh, src, '', time.time()))
        if cur.rowcount > 0:
            added += 1
    conn.commit()
    total = conn.execute('SELECT COUNT(*) FROM movie_quotes').fetchone()[0]
    conn.close()
    return added, total

for gi, genre in enumerate(genres):
    conn = sqlite3.connect(DB, timeout=15)
    current = conn.execute('SELECT COUNT(*) FROM movie_quotes').fetchone()[0]
    conn.close()
    if current >= TARGET:
        break
    for sub in range(1, 4):
        try:
            items = gen(genre, sub)
            added, total = save(items)
            print(f'{genre}#{sub}: +{added} -> {total}/{TARGET}', flush=True)
            if current >= TARGET:
                break
            time.sleep(2)
        except Exception as e:
            print(f'{genre}#{sub}: {str(e)[:60]}', flush=True)
            time.sleep(3)

print(f'FINAL: {total} quotes', flush=True)
