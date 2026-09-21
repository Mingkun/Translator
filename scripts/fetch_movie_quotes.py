"""批量生成经典影视台词入库（DeepSeek）。"""
import json
import sqlite3
import time
import urllib.request
import urllib.error

DB_PATH = "/work/Translator/data/cache.db"
BATCH_SIZE = 50
TOTAL_TARGET = 1000

def get_key():
    data = json.loads(open("/root/.openclaw/secrets.json").read())
    return data["models"]["providers"]["deepseek"]["apiKey"]

def gen_batch(start, count):
    key = get_key()
    auth = "Bearer " + key
    system = (
        "你是经典电影和美剧台词专家。生成编号从" + str(start) + "开始的" + str(count) + "条最经典的电影/美剧台词。"
        '只输出JSON对象，格式：{"quotes":[{"en":"英文台词","zh":"中文翻译","source":"出处片名"}]}。'
        "选台词标准：1)真实存在于著名电影或美剧中；2)广为流传的经典台词；3)覆盖不同年代和类型；"
        "4)每条1-3句话；5)不要重复；6)不需要写编号。"
    )
    payload = json.dumps({
        "model": "deepseek-flash",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "生成第" + str(start) + "到第" + str(start + count - 1) + "条经典影视台词"},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.8,
        "max_tokens": 4000,
    }).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=payload, method="POST",
        headers={"Content-Type": "application/json", "Authorization": auth},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode())
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    if isinstance(parsed, dict):
        for k in ("quotes", "data", "items"):
            if k in parsed:
                return parsed[k]
        return []
    if isinstance(parsed, list):
        return parsed
    return []

def save_quotes(quotes):
    conn = sqlite3.connect(DB_PATH, timeout=15)
    for q in quotes:
        en = str(q.get("en") or "").strip()
        zh = str(q.get("zh") or "").strip()
        source = str(q.get("source") or "").strip()
        if not en or len(en) < 5:
            continue
        try:
            conn.execute(
                "INSERT OR IGNORE INTO movie_quotes (en, zh, source, word, created_at) VALUES (?,?,?,?,?)",
                (en, zh, source, "", time.time()),
            )
        except Exception:
            pass
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM movie_quotes").fetchone()[0]
    conn.close()
    return total

start = 1
for batch in range(TOTAL_TARGET // BATCH_SIZE):
    try:
        quotes = gen_batch(start, BATCH_SIZE)
        total = save_quotes(quotes)
        print(f"batch {batch+1}: got {len(quotes)} -> total {total}", flush=True)
        start += len(quotes)
        if len(quotes) < BATCH_SIZE // 2:
            print("too few, stopping early")
            break
        time.sleep(1.5)
    except Exception as e:
        print(f"batch {batch+1} failed: {e}", flush=True)
        time.sleep(3)
        if batch > 5:
            break

conn = sqlite3.connect(DB_PATH, timeout=15)
final = conn.execute("SELECT COUNT(*) FROM movie_quotes").fetchone()[0]
conn.close()
print(f"done: {final} quotes in library")
