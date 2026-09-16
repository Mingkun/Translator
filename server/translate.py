"""中英互译核心：翻译 + 词典增强（音标/发音/释义/例句）+ 缓存。"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
DB_PATH = Path(__file__).resolve().parents[1] / "data" / "cache.db"
AUDIO_DIR = Path(__file__).resolve().parents[1] / "data" / "audio"


def _http_get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ---------- 缓存 ----------

def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, created_at REAL)"
    )
    return conn


def _cache_get(key: str):
    with _db() as conn:
        row = conn.execute("SELECT v FROM kv WHERE k = ?", (key,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def _cache_set(key: str, value) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO kv (k, v, created_at) VALUES (?, ?, ?)",
            (key, json.dumps(value, ensure_ascii=False), time.time()),
        )


# ---------- 翻译（Google 免费接口） ----------

def google_translate(q: str, sl: str = "auto", tl: str = "en") -> dict:
    cache_key = f"tr:{sl}:{tl}:{q}"
    cached = _cache_get(cache_key)
    if cached:
        return cached
    url = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl={urllib.parse.quote(sl)}&tl={urllib.parse.quote(tl)}"
        f"&dt=t&q={urllib.parse.quote(q)}"
    )
    raw = _http_get(url)
    data = json.loads(raw.decode("utf-8"))
    segments = data[0] or []
    translated = "".join(str(seg[0]) for seg in segments if seg and seg[0])
    detected = str(data[2] or sl) if len(data) > 2 else sl
    result = {"translation": translated, "detected": detected}
    _cache_set(cache_key, result)
    return result


# ---------- 英语词典（Free Dictionary API） ----------

def _term_for_dict(text: str) -> str | None:
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
    if not words:
        return None
    # 多词短语时取主干词（最长的一个），单词直接用
    if len(words) == 1:
        return words[0].lower()
    longest = max(words, key=len)
    return longest.lower() if len(longest) >= 4 else words[0].lower()


_DICT_API_DEAD_UNTIL = 0.0


def cache_get(key: str):
    return _cache_get(key)


def cache_set(key: str, value) -> None:
    _cache_set(key, value)


def dictionary_lookup(term: str) -> dict | None:
    global _DICT_API_DEAD_UNTIL
    term = term.strip().lower()
    if not term or " " in term:
        return None
    cache_key = f"dict:{term}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached or None
    if time.time() < _DICT_API_DEAD_UNTIL:
        return None
    try:
        raw = _http_get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(term)}", timeout=4)
        entries = json.loads(raw.decode("utf-8"))
    except Exception:
        _DICT_API_DEAD_UNTIL = time.time() + 3600
        _cache_set(cache_key, [])
        return None
    if not isinstance(entries, list) or not entries:
        _cache_set(cache_key, [])
        return None
    entry = entries[0]
    phonetic = str(entry.get("phonetic") or "")
    audio_url = ""
    for item in entry.get("phonetics") or []:
        if item.get("audio"):
            audio_url = str(item["audio"])
            if not phonetic:
                phonetic = str(item.get("text") or "")
            break
    if not phonetic:
        for item in entry.get("phonetics") or []:
            if item.get("text"):
                phonetic = str(item["text"])
                break
    meanings: list[dict] = []
    examples: list[str] = []
    for meaning in entry.get("meanings") or []:
        pos = str(meaning.get("partOfSpeech") or "")
        for definition in (meaning.get("definitions") or [])[:3]:
            text = str(definition.get("definition") or "").strip()
            if not text:
                continue
            item = {"pos": pos, "definition": text}
            example = str(definition.get("example") or "").strip()
            if example:
                item["example"] = example
                if len(examples) < 6 and example not in examples:
                    examples.append(example)
            meanings.append(item)
        for synonym in (meaning.get("synonyms") or [])[:4]:
            meanings.append({"pos": pos, "definition": f"同义词：{synonym}"})
    result = {
        "term": term,
        "phonetic": phonetic,
        "audio_url": audio_url,
        "meanings": meanings[:12],
        "examples": examples,
    }
    _cache_set(cache_key, result)
    return result


# ---------- 发音音频 ----------

def audio_id_for(kind: str, payload: str) -> str:
    return hashlib.sha1(f"{kind}:{payload}".encode("utf-8")).hexdigest()[:20]


MALE_VOICES = {"en": "en-US-ChristopherNeural", "zh-CN": "zh-CN-YunxiNeural"}


def tts_bytes(text: str, lang: str) -> bytes:
    """优先 Edge TTS 男声；失败回退 Google TTS。缓存键含音色命名空间。"""
    try:
        return _edge_tts_bytes(text, lang)
    except Exception:
        pass
    return _google_tts_bytes(text, lang)


def _edge_tts_bytes(text: str, lang: str) -> bytes:
    import asyncio
    import edge_tts

    voice = MALE_VOICES.get(lang, "en-US-ChristopherNeural")
    cache_id = audio_id_for("edge", f"{voice}:{text}")
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIO_DIR / f"{cache_id}.mp3"
    if path.is_file() and path.stat().st_size > 0:
        return path.read_bytes()

    async def _run() -> None:
        communicate = edge_tts.Communicate(text[:180], voice)
        await communicate.save(str(path))

    asyncio.run(_run())
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("edge tts empty")
    return path.read_bytes()


def _google_tts_bytes(text: str, lang: str) -> bytes:
    cache_id = audio_id_for("tts", f"{lang}:{text}")
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIO_DIR / f"{cache_id}.mp3"
    if path.is_file() and path.stat().st_size > 0:
        return path.read_bytes()
    url = (
        "https://translate.google.com/translate_tts"
        f"?ie=UTF-8&client=tw-ob&tl={urllib.parse.quote(lang)}"
        f"&q={urllib.parse.quote(text[:180])}"
    )
    raw = _http_get(url)
    path.write_bytes(raw)
    return raw


def dict_audio(audio_url: str) -> bytes:
    cache_id = audio_id_for("url", audio_url)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIO_DIR / f"{cache_id}.mp3"
    if path.is_file() and path.stat().st_size > 0:
        return path.read_bytes()
    raw = _http_get(audio_url)
    path.write_bytes(raw)
    return raw


def serve_cached_audio(cache_id: str) -> bytes | None:
    path = AUDIO_DIR / f"{cache_id}.mp3"
    if path.is_file():
        return path.read_bytes()
    return None


# ---------- DeepSeek 翻译（主引擎） ----------

def _deepseek_key() -> str | None:
    import os
    key = os.environ.get("TRANSLATOR_DEEPSEEK_KEY", "")
    if key:
        return key
    try:
        secrets_path = Path("/root/.openclaw/secrets.json")
        data = json.loads(secrets_path.read_text(encoding="utf-8"))
        return data.get("models", {}).get("providers", {}).get("deepseek", {}).get("apiKey")
    except Exception:
        return None


_TRANSLATE_SYSTEM_PROMPT = (
    "你是中英互译与词典引擎。对用户输入完成中英互译（若同时含中英文，把英文部分翻成中文、中文部分保留），并尽量给出词典信息。"
    '只输出 JSON：{"translation":"译文","detected":"en 或 zh-CN","term":"英文原词或英文译文核心词",'
    '"phonetic":"term的美式发音IPA音标（General American，如 oʊ、ɑː、ɚ、t̬，不用英式 əʊ、ɒ、非儿化 r）","meanings":[{"pos":"词性","definition":"英文释义","zh":"中文释义"}],'
    '"examples":[{"en":"英文例句","zh":"例句中文翻译"}],"movie_examples":[{"en":"影视台词","zh":"台词中文翻译","source":"出处片名"}]}。'
    "meanings 给 3-6 条最常用含义；examples 给 3-5 个自然常用的例句；"
    'movie_examples 给 1-3 条该词/短语出现过的著名电影或美剧真实台词，格式 [{"en":"台词","zh":"台词中文翻译","source":"片名"}]，'
    "只引用你确定真实存在的著名台词并标注片名，没有合适的不确定就给空数组，严禁编造；"
    "中文输入时 term 取英文译文的核心词；若输入是句子而非单词，term 与 phonetic 留空、"
    "meanings 与 examples 与 movie_examples 留空数组。"
)


def deepseek_full(q: str) -> dict | None:
    """一次调用输出：译文 + 语言检测 + 词典词 + IPA音标 + 常用含义(带中文) + 常用例句(带中文)。"""
    key = _deepseek_key()
    if not key:
        return None
    system = _TRANSLATE_SYSTEM_PROMPT
    payload = json.dumps(
        {
            "model": "deepseek-flash",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": q},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 3000,
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=75) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = str((data.get("choices") or [{}])[0].get("message", {}).get("content") or "")
    parsed = _extract_json_object(content)
    if parsed is None:
        raise RuntimeError("deepseek json parse failed")
    result = {
        "translation": str(parsed.get("translation") or "").strip(),
        "detected": str(parsed.get("detected") or "").strip() or "en",
        "term": str(parsed.get("term") or "").strip(),
        "phonetic": str(parsed.get("phonetic") or "").strip(),
        "meanings": [m for m in (parsed.get("meanings") or []) if isinstance(m, dict)][:6],
        "examples": [e for e in (parsed.get("examples") or []) if isinstance(e, dict)][:5],
        "movie_examples": [e for e in (parsed.get("movie_examples") or []) if isinstance(e, dict) and e.get("en")][:3],
    }
    if not result["translation"]:
        return None
    return result


_GOOGLE_RATE_LIMITED_UNTIL = 0.0


def smart_translate(q: str, sl: str = "auto", tl: str = "") -> dict:
    """引擎链：deepseek → glm-5.3 → glm-5.1 → deepseek重试×2 → google → 报错。"""
    cache_key = f"smart5:{sl}:{tl}:{q}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    def _try_deepseek() -> dict | None:
        try:
            r = deepseek_full(q)
            if r and r.get("detected", "").startswith("zh"):
                r["detected"] = "zh-CN"
            return r
        except Exception:
            return None

    def _try_glm(model: str) -> dict | None:
        try:
            r = glm_full(q, model=model)
            if r and r.get("detected", "").startswith("zh"):
                r["detected"] = "zh-CN"
            return r
        except Exception:
            return None

    result = None
    # 1) DeepSeek 首轮
    result = _try_deepseek()
    # 2) GLM-5.3
    if result is None:
        result = _try_glm("glm-5.3")
    # 3) GLM-5.1
    if result is None:
        result = _try_glm("glm-5.1")
    # 4) DeepSeek 重试2次（带退避）
    if result is None:
        for attempt in range(2):
            time.sleep(2.0 + attempt * 2.0)
            result = _try_deepseek()
            if result:
                break
    # 5) Google 兜底
    if result is None:
        global _GOOGLE_RATE_LIMITED_UNTIL
        if time.time() > _GOOGLE_RATE_LIMITED_UNTIL:
            try:
                target = tl or "en"
                g = google_translate(q, sl=sl, tl=target)
                result = {
                    "translation": g.get("translation") or "",
                    "detected": g.get("detected") or sl,
                    "term": "", "phonetic": "", "meanings": [], "examples": [], "movie_examples": [],
                }
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    _GOOGLE_RATE_LIMITED_UNTIL = time.time() + 600
            except Exception:
                pass
    # 6) 仍失败则报错
    if not result or not result.get("translation"):
        raise RuntimeError("翻译服务暂时繁忙（限流），请稍等几秒重试；查过的词不受影响")
    _cache_set(cache_key, result)
    return result

def _zai_key() -> str | None:
    import os
    key = os.environ.get("TRANSLATOR_ZAI_KEY", "")
    if key:
        return key
    try:
        data = json.loads(Path("/root/.openclaw/secrets.json").read_text(encoding="utf-8"))
        return data.get("models", {}).get("providers", {}).get("zai", {}).get("apiKey")
    except Exception:
        return None


def extract_text_from_image(image_bytes: bytes) -> str:
    """用 GLM 视觉模型提取图片中的中英文文字。"""
    import base64
    key = _zai_key()
    if not key:
        raise RuntimeError("视觉模型密钥未配置")
    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = json.dumps(
        {
            "model": "glm-4.6v",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": "提取图片中所有可见的中英文文字，保持阅读顺序，只输出提取到的文字本身，不要解释。"},
                    ],
                }
            ],
            "temperature": 0.1,
            "max_tokens": 2000,
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.z.ai/api/coding/paas/v4/chat/completions",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = str(data["choices"][0]["message"]["content"] or "").strip()
    if not text:
        raise RuntimeError("未识别到文字")
    return text

# ---------- 查询历史 ----------

def _history_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS history (q TEXT PRIMARY KEY, created_at REAL)"
    )
    return conn


def record_history(q: str) -> None:
    q = q.strip()
    if not q or len(q) > 500:
        return
    with _history_db() as conn:
        conn.execute(
            "INSERT INTO history (q, created_at) VALUES (?, ?) "
            "ON CONFLICT(q) DO UPDATE SET created_at = excluded.created_at",
            (q, time.time()),
        )


def load_history(limit: int = 50) -> list[dict]:
    with _history_db() as conn:
        rows = conn.execute(
            "SELECT q, created_at FROM history ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
    return [{"q": r[0], "created_at": r[1]} for r in rows]

def glm_full(q: str, model: str = "glm-5.3") -> dict | None:
    """GLM 备用引擎（单模型，由 smart_translate 编排调用顺序）。"""
    key = _zai_key()
    if not key:
        return None
    last_error: Exception | None = None
    for model in (model,):
        payload = json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": _TRANSLATE_SYSTEM_PROMPT},
                    {"role": "user", "content": q},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
                "max_tokens": 2500,
            }
        ).encode()
        req = urllib.request.Request(
            "https://api.z.ai/api/coding/paas/v4/chat/completions",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": UA},
        )
        try:
            with urllib.request.urlopen(req, timeout=75) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = str((data.get("choices") or [{}])[0].get("message", {}).get("content") or "")
            parsed = _extract_json_object(content)
            if parsed is None:
                last_error = RuntimeError("glm json parse failed")
                continue
            result = {
                "translation": str(parsed.get("translation") or "").strip(),
                "detected": str(parsed.get("detected") or "").strip() or "en",
                "term": str(parsed.get("term") or "").strip(),
                "phonetic": str(parsed.get("phonetic") or "").strip(),
                "meanings": [m for m in (parsed.get("meanings") or []) if isinstance(m, dict)][:6],
                "examples": [e for e in (parsed.get("examples") or []) if isinstance(e, dict)][:5],
                "movie_examples": [e for e in (parsed.get("movie_examples") or []) if isinstance(e, dict) and e.get("en")][:3],
            }
            if result["translation"]:
                return result
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return None

def _extract_json_object(text: str) -> dict | None:
    """括号配平提取首个完整 JSON 对象（容忍前后缀文本与换行）。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("` \n")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == chr(34):
                in_string = False
            continue
        if ch == chr(34):
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start:index + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None

