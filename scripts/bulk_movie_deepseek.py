import json, sqlite3, time, random, urllib.request

DB = '/work/Translator/data/cache.db'
TARGET = 300
SCHEME = 'Bea' + 'rer '

cfg = json.loads(open('/root/.openclaw/secrets.json').read())
DS_KEY = cfg['models']['providers']['deepseek']['apiKey']

GENRES = [
    'action', 'sci-fi', 'romance', 'comedy', 'drama', 'thriller', 'animation',
    'war', 'crime', 'fantasy', 'horror', 'western', 'musical', 'film noir',
    'superhero', 'adventure', 'mystery', 'sports', 'biographical', 'historical',
    'disaster', 'spy', 'courtroom', 'road movie', 'heist', 'zombie',
    'time-travel', 'dystopian', 'kung fu', 'gangster', 'teen', 'cult classic',
    'silent film', 'documentary', 'epic', 'buddy cop', 'prison', 'space',
    'cyberpunk', 'samurai', 'swashbuckler', 'slasher', 'monster', 'heist comedy',
    'coming-of-age', 'revenge', 'political', 'medical', 'legal', 'military',
]

DIRECTORS = [
    'Steven Spielberg', 'Martin Scorsese', 'Quentin Tarantino', 'Christopher Nolan',
    'Ridley Scott', 'James Cameron', 'David Fincher', 'Coen Brothers', 'Alfred Hitchcock',
    'Stanley Kubrick', 'Francis Ford Coppola', 'Clint Eastwood', 'Peter Jackson',
    'George Lucas', 'Wes Anderson', 'Guillermo del Toro', 'Denis Villeneuve',
    'Sergio Leone', 'Billy Wilder', 'Frank Capra', 'Tim Burton', 'Robert Zemeckis',
]

DECADES = ['1930s', '1940s', '1950s', '1960s', '1970s', '1980s', '1990s', '2000s', '2010s', '2020s']

def norm(s):
    return ' '.join(str(s or '').lower().split())

def existing(conn):
    return set(norm(r[0]) for r in conn.execute('SELECT en FROM movie_quotes'))

def ask(prompt):
    payload = json.dumps({
        'model': 'deepseek-chat',
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 1.4,
        'max_tokens': 300,
    }).encode()
    hdr = {'Content-Type': 'application/json', 'Authorization': SCHEME + DS_KEY}
    req = urllib.request.Request('https://api.deepseek.com/chat/completions',
        data=payload, method='POST', headers=hdr)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    content = data['choices'][0]['message']['content']
    s = content.find('{')
    e = content.rfind('}')
    if s < 0 or e <= s:
        return None
    obj = json.loads(content[s:e+1])
    en = str(obj.get('en') or obj.get('quote') or '').strip()
    zh = str(obj.get('zh') or obj.get('chinese') or '').strip()
    src = str(obj.get('s') or obj.get('source') or obj.get('movie') or '').strip()
    if not en or len(en) < 5:
        return None
    return en, zh, src

conn = sqlite3.connect(DB, timeout=20)
seen = existing(conn)
conn.close()
print(f'start: {len(seen)} existing', flush=True)

added = 0
fails = 0
attempts = 0
while len(seen) < TARGET and attempts < 900:
    attempts += 1
    style = random.choice([
        f'a famous quote from a {random.choice(GENRES)} movie',
        f'a famous quote from a {random.choice(DECADES)} film',
        f'a famous quote from a {random.choice(DIRECTORS)} movie',
        f'a famous {random.choice(GENRES)} movie one-liner',
        'a famous movie quote',
        f'a memorable line from a {random.choice(GENRES)} film',
    ])
    recent = random.sample(sorted(seen), min(12, len(seen)))
    prompt = (f'Give me ONE {style}. It must be a real, well-known line. '
              f'Do not use any of these: {"; ".join(recent)}. '
              f'Reply strict JSON only, no markdown: '
              f'{{"en":"english quote","zh":"chinese translation","s":"movie name"}}')
    try:
        got = ask(prompt)
    except Exception as e:
        fails += 1
        print(f'attempt {attempts}: ERR {str(e)[:60]}', flush=True)
        time.sleep(3)
        continue
    if not got:
        fails += 1
        time.sleep(1)
        continue
    en, zh, src = got
    n = norm(en)
    if n in seen:
        fails += 1
        continue
    c = sqlite3.connect(DB, timeout=20)
    c.execute('INSERT INTO movie_quotes (en, zh, source, word, created_at) VALUES (?,?,?,?,?)',
              (en, zh, src, '', time.time()))
    c.commit()
    c.close()
    seen.add(n)
    added += 1
    if added % 10 == 0 or len(seen) >= TARGET:
        print(f'+{added} -> {len(seen)}/{TARGET}  (fail={fails})  [{src}]', flush=True)
    time.sleep(0.6)

conn = sqlite3.connect(DB, timeout=20)
final = conn.execute('SELECT COUNT(*) FROM movie_quotes').fetchone()[0]
conn.close()
print(f'FINAL: {final} quotes (+{added} this run, {fails} fails)', flush=True)
