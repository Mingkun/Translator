"""Translator Web 服务：中英互译 API + 发音音频 + 简单网页界面。"""
from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, request, jsonify, Response, abort

import translate as engine

APP_ROOT = Path(__file__).resolve().parents[1]
API_TOKEN = os.environ.get("TRANSLATOR_API_TOKEN", "")
app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False


def _client_token() -> str:
    return (
        request.headers.get("X-Api-Token", "")
        or request.args.get("token", "")
        or ""
    )


def _authorized() -> bool:
    if not API_TOKEN:
        return True
    return _client_token() == API_TOKEN


@app.get("/health")
def health():
    return jsonify(ok=True)


@app.get("/")
def index():
    html = (APP_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    html = html.replace("__API_TOKEN__", API_TOKEN)
    return html


def _translate_with_cache(q: str, sl: str, tl: str) -> dict:
    """完整响应级缓存：查过的词直接秒回；大小写不敏感命中。"""
    cache_key = f"resp:{sl}:{tl}:{q.strip().lower()}"
    cached = engine.cache_get(cache_key)
    if isinstance(cached, dict) and cached.get("ok"):
        engine.record_history(q)
        return {**cached, "query": q}
    result = build_translation(q, sl, tl)
    engine.cache_set(cache_key, result)
    engine.record_history(q)
    return result


def _save_movie_quotes(word: str, result: dict) -> None:
    import sqlite3
    movies = result.get("movie_examples") or []
    if not movies:
        return
    conn = sqlite3.connect(engine.DB_PATH, timeout=15)
    try:
        for m in movies:
            en = str(m.get("en") or "").strip()
            if not en:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO movie_quotes (en, zh, source, word, created_at) VALUES (?,?,?,?,?)",
                (en, str(m.get("zh") or ""), str(m.get("source") or ""), word, __import__("time").time()),
            )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


@app.get("/api/translate")
def api_translate():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify(ok=False, error="empty query"), 400
    if len(q) > 500:
        q = q[:500]
    sl = request.args.get("from") or "auto"
    tl = request.args.get("to") or ""
    try:
        result = _translate_with_cache(q, sl, tl)
        _save_movie_quotes(q, result)
        return jsonify(result)
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, error=f"translate failed: {exc}"), 502


def build_translation(q: str, sl: str, tl: str) -> dict:
    result = engine.smart_translate(q, sl=sl, tl=tl)
    detected = result.get("detected") or sl
    translation = result.get("translation") or ""
    if not tl:
        tl = "zh-CN" if detected.startswith("en") else "en"

    if detected.startswith("en"):
        en_text, zh_text = q, translation
    else:
        en_text, zh_text = translation, q

    term = str(result.get("term") or "").strip()
    phonetic = str(result.get("phonetic") or "").strip()
    # 可选增强：词典 API 可达时补充真人音频
    info = engine.dictionary_lookup(term) if term else None

    def tts_url(text: str, lang: str) -> str:
        if not text:
            return ""
        from urllib.parse import quote as _quote
        suffix = f"&token={_client_token()}" if _client_token() else ""
        return f"api/tts?text={_quote(text[:180])}&lang={lang}{suffix}"

    audio_en = tts_url(term or en_text, "en")
    audio_zh = tts_url(zh_text, "zh-CN")

    examples = []
    for example in result.get("examples", [])[:5]:
        en_sent = str(example.get("en") or "").strip()
        if not en_sent:
            continue
        examples.append(
            {
                "en": en_sent,
                "zh": str(example.get("zh") or "").strip(),
                "audio": tts_url(en_sent, "en"),
                "source": "llm",
            }
        )

    movie_examples = []
    for item in result.get("movie_examples", [])[:3]:
        en_line = str(item.get("en") or "").strip()
        if not en_line:
            continue
        movie_examples.append(
            {
                "en": en_line,
                "zh": str(item.get("zh") or "").strip(),
                "source": str(item.get("source") or "").strip(),
                "audio": tts_url(en_line, "en"),
            }
        )

    return {
        "ok": True,
        "query": q,
        "detected": detected,
        "target": tl,
        "translation": translation,
        "term": term,
        "phonetic": phonetic,
        "audio_en": audio_en,
        "audio_zh": audio_zh,
        "meanings": result.get("meanings", []),
        "examples": examples,
        "movie_examples": movie_examples,
    }


@app.post("/api/ocr-translate")
def api_ocr_translate():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    file = request.files.get("image")
    if file is None:
        return jsonify(ok=False, error="缺少图片"), 400
    data = file.read()
    if len(data) < 100:
        return jsonify(ok=False, error="图片无效"), 400
    if len(data) > 8 * 1024 * 1024:
        return jsonify(ok=False, error="图片过大（限8MB）"), 400
    try:
        from urllib.parse import quote as _quote
        extracted = engine.extract_text_from_image(data)
        q = extracted[:500]
        result = _translate_with_cache(q, "auto", "")
        result["extracted"] = extracted
        return jsonify(result)
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, error=f"识别或翻译失败: {exc}"), 502


@app.get("/api/history")
def api_history():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    limit = request.args.get("limit", "50")
    offset = request.args.get("offset", "0")
    try:
        limit = int(limit)
    except ValueError:
        limit = 50
    try:
        offset = int(offset)
    except ValueError:
        offset = 0
    sort = request.args.get("sort", "time")
    if sort not in {"time", "alpha"}:
        sort = "time"
    filter_str = request.args.get("filter", "")
    return jsonify(ok=True, **engine.load_history(limit, offset, sort, filter_str))


@app.post("/api/history/delete")
def api_history_delete():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    payload = request.get_json(silent=True) or {}
    q = str(payload.get("q") or "").strip()
    if not q:
        return jsonify(ok=False, error="缺少词条"), 400
    import sqlite3
    conn = sqlite3.connect(engine.DB_PATH, timeout=15)
    try:
        n = conn.execute("DELETE FROM history WHERE lower(q) = lower(?)", (q,)).rowcount
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True, deleted=n)


@app.get("/api/quote")
def api_quote():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    code = request.args.get("code", "").strip()
    if not code:
        return jsonify(ok=False, error="missing code"), 400
    try:
        data = engine.fetch_realtime_quote(code)
        return jsonify(ok=True, **data)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)[:120]), 502


@app.get("/api/movies")
def api_movies():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    import sqlite3
    offset = request.args.get("offset", "0")
    try:
        offset = int(offset)
    except ValueError:
        offset = 0
    limit = request.args.get("limit", "50")
    try:
        limit = min(int(limit), 5000)
    except ValueError:
        limit = 50
    conn = sqlite3.connect(engine.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute("SELECT COUNT(*) FROM movie_quotes").fetchone()[0]
        rows = conn.execute(
            "SELECT id, en, zh, source, word FROM movie_quotes ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        items = [{"id": r["id"], "en": r["en"], "zh": r["zh"], "source": r["source"], "word": r["word"]} for r in rows]
    finally:
        conn.close()
    return jsonify(ok=True, items=items, total=total)


@app.get("/api/news")
def api_news():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    import sqlite3
    conn = sqlite3.connect(engine.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT date, title, url, text FROM news_daily ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if not row:
            return jsonify(ok=False, error="no news yet")
        audio = APP_ROOT / "data" / "news" / (row["date"] + ".mp3")
        return jsonify(
            ok=True,
            date=row["date"],
            title=row["title"],
            url=row["url"],
            text=row["text"],
            has_audio=audio.is_file(),
        )
    finally:
        conn.close()


@app.get("/api/news/audio")
def api_news_audio():
    if not _authorized():
        abort(401)
    import re as _re2
    date = (request.args.get("date") or "").strip()
    if not _re2.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        abort(400)
    path = APP_ROOT / "data" / "news" / (date + ".mp3")
    if not path.is_file():
        abort(404)
    return Response(path.read_bytes(), mimetype="audio/mpeg")


@app.get("/downloads/<path:name>")
def downloads(name: str):
    import urllib.parse as _up
    safe = _up.unquote(name)
    if "/" in safe or ".." in safe or not safe.isascii():
        abort(400)
    path = APP_ROOT / "static" / "downloads" / safe
    if not path.is_file():
        abort(404)
    mime = "application/vnd.android.package-archive" if safe.endswith(".apk") else "application/octet-stream"
    return Response(path.read_bytes(), mimetype=mime)


@app.get("/api/tts")
def api_tts():
    if not _authorized():
        abort(401)
    text = (request.args.get("text") or "").strip()
    lang = request.args.get("lang") or "en"
    voice = (request.args.get("voice") or "").strip()
    rate = (request.args.get("rate") or "").strip()
    if not text or lang not in {"en", "zh-CN"} or len(text) > 200:
        abort(400)
    if voice and voice not in {"en-US-GuyNeural", "en-US-ChristopherNeural", "en-US-EricNeural", "en-US-AndrewNeural", "en-US-AriaNeural", "en-US-JennyNeural", "zh-CN-YunxiNeural", "zh-CN-YunyangNeural", "zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural"}:
        abort(400)
    import re as _re
    if rate and not _re.fullmatch(r"[+-][0-9]{1,2}%", rate):
        abort(400)
    try:
        data = engine.tts_bytes(text, lang, voice, rate)
        return Response(data, mimetype="audio/mpeg")
    except Exception:
        abort(502)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("TRANSLATOR_PORT", "5030")))
