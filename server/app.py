"""Translator Web 服务：中英互译 API + 发音音频 + 简单网页界面。"""
from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, request, jsonify, Response, abort

import translate as engine

import json as _json, base64 as _base64, uuid as _uuid, urllib.request as _urllib_req

VOLC_TTS_PATH = '/work/Translator/volc_tts.json'
TTS_CACHE_DIR = Path('/work/Translator/tts_cache')

def _tts_cache_key(speaker, rate, text):
    import hashlib
    return hashlib.md5((speaker + '|' + (rate or '') + '|' + text).encode('utf-8')).hexdigest()

def _tts_cache_get(key):
    try:
        p = TTS_CACHE_DIR / (key + '.mp3')
        if p.exists():
            return p.read_bytes()
    except Exception:
        pass
    return None

def _tts_cache_put(key, data):
    try:
        TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = TTS_CACHE_DIR / (key + '.tmp')
        tmp.write_bytes(data)
        tmp.rename(TTS_CACHE_DIR / (key + '.mp3'))
        files = sorted(TTS_CACHE_DIR.glob('*.mp3'), key=lambda f: f.stat().st_mtime)
        if len(files) > 5000:
            for f in files[:len(files) - 4000]:
                try:
                    f.unlink()
                except Exception:
                    pass
    except Exception:
        pass

def _volc_cfg():
    try:
        with open(VOLC_TTS_PATH, encoding='utf-8') as f:
            j = _json.load(f)
        key = j.get('api_key') or j.get('token') or ''
        if key:
            return {'api_key': key, 'male_speaker': j.get('male_speaker') or '', 'female_speaker': j.get('female_speaker') or ''}
    except Exception:
        pass
    return None

def _volc_voice_for(voice):
    cfg = _volc_cfg()
    if not cfg:
        return None
    if voice in ('zh-CN-YunxiNeural', 'zh-CN-YunyangNeural'):
        return cfg.get('male_speaker') or None
    if voice in ('zh-CN-XiaoxiaoNeural', 'zh-CN-XiaoyiNeural'):
        return cfg.get('female_speaker') or None
    return None

def _volc_tts_bytes(text, speaker, rate=""):
    import time as _time
    cfg = _volc_cfg()
    headers = {'Content-Type': 'application/json', 'X-Api-Key': cfg['api_key'], 'X-Api-Resource-Id': 'seed-tts-2.0'}
    body = _json.dumps({'user': {'uid': 'translator'},
                        'unique_id': str(_uuid.uuid4()),
                        'req_params': {'text': text[:200], 'speaker': speaker,
                                       'audio_params': {'format': 'mp3', 'sample_rate': 24000},
                                       'additions': '{}'}}).encode('utf-8')
    req = _urllib_req.Request('https://openspeech.bytedance.com/api/v3/tts/submit', data=body,
                              headers=dict(headers, **{'X-Api-Request-Id': str(_uuid.uuid4())}))
    with _urllib_req.urlopen(req, timeout=20) as r:
        j = _json.loads(r.read().decode('utf-8'))
    task_id = (j.get('data') or {}).get('task_id') or j.get('task_id')
    if not task_id:
        raise RuntimeError('volc submit %s %s' % (j.get('code'), j.get('message')))
    for _ in range(30):
        _time.sleep(1)
        qreq = _urllib_req.Request('https://openspeech.bytedance.com/api/v3/tts/query',
                                   data=_json.dumps({'task_id': task_id}).encode('utf-8'),
                                   headers=dict(headers, **{'X-Api-Request-Id': str(_uuid.uuid4())}))
        try:
            with _urllib_req.urlopen(qreq, timeout=20) as r:
                q = _json.loads(r.read().decode('utf-8'))
        except Exception:
            continue
        d = q.get('data') or {}
        if d.get('audio_url'):
            with _urllib_req.urlopen(_urllib_req.Request(d['audio_url']), timeout=30) as ar:
                return ar.read()
        if (d.get('task_status') or 0) >= 3:
            raise RuntimeError('volc task failed status=%s' % d.get('task_status'))
    raise RuntimeError('volc timeout')

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
    return _client_token().strip() == API_TOKEN.strip()


@app.get("/health")
def health():
    return jsonify(ok=True)


@app.get("/")
def index():
    html = (APP_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    html = html.replace("__API_TOKEN__", API_TOKEN)
    return html


def _serve_static_file(name: str, mime: str):
    def _inner():
        p = APP_ROOT / "static" / name
        if not p.is_file():
            abort(404)
        return Response(p.read_bytes(), mimetype=mime)
    return _inner


app.add_url_rule("/manifest.json", "pwa_manifest", _serve_static_file("manifest.json", "application/manifest+json"))
app.add_url_rule("/apple-touch-icon.png", "pwa_touch_icon", _serve_static_file("apple-touch-icon.png", "image/png"))
app.add_url_rule("/icon-192.png", "pwa_icon192", _serve_static_file("icon-192.png", "image/png"))
app.add_url_rule("/icon-512.png", "pwa_icon512", _serve_static_file("icon-512.png", "image/png"))
app.add_url_rule("/parrot_squawk.mp3", "parrot_squawk", _serve_static_file("parrot_squawk.mp3", "audio/mpeg"))


def _translate_with_cache(q: str, sl: str, tl: str, ut: str = "") -> dict:
    """完整响应级缓存：查过的词直接秒回；大小写不敏感命中。"""
    cache_key = f"resp:{sl}:{tl}:{q.strip().lower()}"
    cached = engine.cache_get(cache_key)
    if isinstance(cached, dict) and cached.get("ok"):
        engine.record_history(q, ut)
        return {**cached, "query": q}
    result = build_translation(q, sl, tl)
    engine.cache_set(cache_key, result)
    engine.record_history(q, ut)
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
    ut = request.args.get("ut") or ""
    try:
        result = _translate_with_cache(q, sl, tl, ut)
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
        result = _translate_with_cache(q, "auto", "", request.args.get("ut") or "")
        result["extracted"] = extracted
        return jsonify(result)
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, error=f"识别或翻译失败: {exc}"), 502


@app.post("/api/interpret")
def api_interpret():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    file = request.files.get("audio")
    if file is None:
        return jsonify(ok=False, error="缺少音频"), 400
    data = file.read()
    if len(data) < 200:
        return jsonify(ok=False, error="录音太短"), 400
    if len(data) > 20 * 1024 * 1024:
        return jsonify(ok=False, error="音频过大（限20MB）"), 400
    try:
        tr = engine.interpret_transcribe(data)
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("interpret transcribe failed: %s", exc)
        return jsonify(ok=False, error="语音识别失败，请重试"), 500
    if not tr or not tr.get("text"):
        return jsonify(ok=False, error="没有识别到语音内容"), 400
    out = {"ok": True, "transcript": tr["text"], "lang": tr.get("lang", "")}
    try:
        res = engine.interpret_translate(tr["text"])
        if res:
            out["translation"] = res.get("translation", "")
            if res.get("detected"):
                out["detected"] = res["detected"]
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("interpret translate failed: %s", exc)
    out.setdefault("translation", "")
    return jsonify(out)


@app.post("/api/user/login")
def api_user_login():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    u = engine.user_login(username, password)
    if not u:
        return jsonify(ok=False, error="用户名或密码错误"), 401
    return jsonify(ok=True, **u)


@app.post("/api/user/register")
def api_user_register():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    u, err = engine.user_register(username, password)
    if not u:
        return jsonify(ok=False, error=err or "注册失败"), 400
    return jsonify(ok=True, **u)


@app.get("/api/user/me")
def api_user_me():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    u = engine.user_touch(request.args.get("ut") or "")
    if not u:
        return jsonify(ok=False, error="未登录"), 401
    return jsonify(ok=True, **u)


@app.get("/api/user/list")
def api_user_list():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    u = engine.user_by_token(request.args.get("ut") or "")
    if not u or not u.get("is_manager"):
        return jsonify(ok=False, error="仅管理员可查看"), 403
    return jsonify(ok=True, users=engine.users_list())


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
    ut = request.args.get("ut", "")
    return jsonify(ok=True, **engine.load_history(ut, limit, offset, sort, filter_str))


@app.post("/api/history/delete")
def api_history_delete():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    payload = request.get_json(silent=True) or {}
    q = str(payload.get("q") or "").strip()
    if not q:
        return jsonify(ok=False, error="缺少词条"), 400
    ut = str(payload.get("ut") or request.args.get("ut") or "")
    n = engine.delete_history(ut, q)
    return jsonify(ok=True, deleted=n)


@app.get("/api/dict")
def api_dict():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    q = (request.args.get("q") or "").strip().lower()[:60]
    if not q:
        return jsonify(ok=False, error="empty"), 400
    cached = engine.cache_get(f"resp:auto::{q}")
    if isinstance(cached, dict) and cached.get("ok"):
        return jsonify(ok=True, full=cached, fast=engine.dict_fast(q))
    return jsonify(ok=True, fast=engine.dict_fast(q))


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


def _news_hash(url: str) -> str:
    import hashlib
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


@app.get("/api/news/list")
def api_news_list():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    import sqlite3
    try:
        limit = min(int(request.args.get("limit", "300")), 500)
    except ValueError:
        limit = 300
    filt = (request.args.get("filter") or "").strip().lower()
    cat = (request.args.get("category") or "").strip()
    conn = sqlite3.connect(engine.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS news_items ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, title TEXT, "
            "category TEXT, url TEXT UNIQUE, text TEXT, created_at REAL)"
        )
        cond, args = [], []
        if filt:
            like = "%" + filt + "%"
            cond.append("(lower(title) LIKE ? OR lower(text) LIKE ?)")
            args += [like, like]
        if cat:
            cond.append("category = ?")
            args.append(cat)
        where = (" WHERE " + " AND ".join(cond)) if cond else ""
        rows = conn.execute(
            "SELECT id, date, title, category FROM news_items" + where + " ORDER BY id DESC LIMIT ?",
            args + [limit],
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM news_items" + where, args
        ).fetchone()[0]
        return jsonify(ok=True, total=total, items=[dict(r) for r in rows])
    finally:
        conn.close()


@app.get("/api/news")
def api_news():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    import sqlite3
    try:
        nid = int(request.args.get("id", "0"))
    except ValueError:
        nid = 0
    if nid <= 0:
        return jsonify(ok=False, error="bad id"), 400
    conn = sqlite3.connect(engine.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS news_items ("
                         "id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, title TEXT, "
                         "category TEXT, url TEXT UNIQUE, text TEXT, created_at REAL, audio_url TEXT DEFAULT ''")
            cols = [r[1] for r in conn.execute("PRAGMA table_info(news_items)")]
            if "audio_url" not in cols:
                conn.execute("ALTER TABLE news_items ADD COLUMN audio_url TEXT DEFAULT ''")
        except Exception:
            pass
        row = conn.execute(
            "SELECT id, date, title, category, url, text, audio_url FROM news_items WHERE id=?", (nid,)
        ).fetchone()
        if not row:
            return jsonify(ok=False, error="not found")
        h = _news_hash(row["url"])
        audio = APP_ROOT / "data" / "news" / (h + ".mp3")
        audio_url = (row["audio_url"] or "").strip()
        if audio.is_file():
            audio_url = ""
        words = []
        meta = APP_ROOT / "data" / "news" / (h + ".json")
        if meta.is_file():
            try:
                import json as _json
                words = _json.loads(meta.read_text(encoding="utf-8"))
            except Exception:
                words = []
        return jsonify(
            ok=True,
            id=row["id"],
            date=row["date"],
            title=row["title"],
            category=row["category"],
            url=row["url"],
            text=row["text"],
            hash=h,
            has_audio=audio.is_file() or bool(audio_url),
            audio_url=audio_url,
            words=words,
        )
    finally:
        conn.close()


@app.get("/api/news/audio")
def api_news_audio():
    if not _authorized():
        abort(401)
    import re as _re2
    h = (request.args.get("h") or "").strip()
    if not _re2.fullmatch(r"[0-9a-f]{16}", h):
        abort(400)
    path = APP_ROOT / "data" / "news" / (h + ".mp3")
    if not path.is_file():
        abort(404)
    from flask import send_file as _send_file
    return _send_file(path, mimetype="audio/mpeg", conditional=True)


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
    resp = Response(path.read_bytes(), mimetype=mime)
    # App 页面以 file:// 打开（null origin），缺这个头会被 CORS 拦截，导致检查更新失效
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.get("/api/city-guide")
def api_city_guide():
    """按需获取任意城市的攻略（本地库未命中时由 App 调用）。"""
    city = (request.args.get("city") or "").strip()[:32]
    if not city:
        abort(400)
    import city_guide
    resp = jsonify(city_guide.get_guide(city))
    resp.headers["Cache-Control"] = "public, max-age=600"
    # App 页面是 file:// 加载，origin 为 null，必须放行 CORS
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.get("/api/trip")
def api_trip():
    """行程估算：距离 / 高铁与飞机时长票价区间 / 推荐方式 / 两地火车站。"""
    frm = (request.args.get("from") or "").strip()[:24]
    to = (request.args.get("to") or "").strip()[:24]
    if not frm or not to:
        abort(400)
    import city_guide
    try:
        data = city_guide.get_trip(frm, to)
    except Exception:
        data = {"ok": False, "reason": "upstream-error", "from": frm, "to": to}
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "public, max-age=1800"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.get("/api/lrc-lookup")
def api_lrc_lookup():
    _resp_cors = True  # 标记需要CORS
    """歌词查找：lrclib → 网易云 → 生成提示"""
    import urllib.request as _u, json as _json, urllib.parse as _up
    if not _authorized():
        abort(401)
    track = (request.args.get("track") or "").strip()
    artist = (request.args.get("artist") or "").strip()
    if not track:
        return jsonify(ok=False, error="missing track"), 400

    # 0) 服务端缓存
    try:
        _cd = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'lrc_cache')
        _fp = _os.path.join(_cd, _hash.md5((artist.lower() + '|' + track.lower()).encode('utf-8')).hexdigest() + '.lrc')
        if _os.path.exists(_fp):
            with open(_fp, 'r', encoding='utf-8') as f:
                _cached = f.read()
            if _cached.strip():
                return jsonify(ok=True, source="cache", lrc=_cached, name=track)
    except Exception:
        pass

    # 1) lrclib.net
    try:
        q = _up.urlencode({"track_name": track, **({"artist_name": artist} if artist else {})})
        req = _u.Request(f"https://lrclib.net/api/search?{q}", headers={"Accept": "application/json", "User-Agent": "PD FViewer/1.0"})
        with _u.urlopen(req, timeout=10) as r:
            j = _json.loads(r.read().decode())
        if j and j[0].get("syncedLyrics"):
            return jsonify(ok=True, source="lrclib", lrc=j[0]["syncedLyrics"], name=j[0].get("trackName", track))
        if j and j[0].get("plainLyrics"):
            return jsonify(ok=True, source="lrclib-plain", lrc=j[0]["plainLyrics"], name=j[0].get("trackName", track))
    except Exception:
        pass

    # 2) 网易云音乐（多策略搜索 + 结果验证）
    import re as _re
    def _netease_search(q_str):
        try:
            q = _up.urlencode({"s": q_str, "type": 1, "limit": 5})
            req = _u.Request(f"https://music.163.com/api/search/get?{q}",
                             headers={"User-Agent": "Mozilla/5.0", "Referer": "https://music.163.com"})
            with _u.urlopen(req, timeout=10) as r:
                return (_json.loads(r.read().decode()).get("result") or {}).get("songs") or []
        except Exception:
            return []
    def _netease_lyric(sid):
        try:
            req2 = _u.Request(f"https://music.163.com/api/song/lyric?id={sid}&lv=1&kv=1&tv=-1",
                              headers={"User-Agent": "Mozilla/5.0", "Referer": "https://music.163.com"})
            with _u.urlopen(req2, timeout=10) as r2:
                lj = _json.loads(r2.read().decode())
            return ((lj.get("lrc") or {}).get("lyric") or "").strip()
        except Exception:
            return ""
    # 清理歌手名：取第一个歌手、去掉&和空格后缀
    clean_artist = _re.sub(r'[&＆].*$', '', artist).strip() or artist
    # 搜索策略：歌名+歌手 → 歌名+第一歌手 → 只搜歌名
    queries = []
    if artist and artist != clean_artist:
        queries.append(f"{track} {clean_artist}")
    if clean_artist:
        queries.append(f"{track} {clean_artist}")
    queries.append(track)
    for qs in queries:
        songs = _netease_search(qs)
        for song in songs:
            sname = song.get("name", "")
            # 验证：歌名必须包含搜索词的关键部分
            if track.lower() not in sname.lower() and sname.lower() not in track.lower():
                continue
            lrc = _netease_lyric(song["id"])
            if lrc:
                return jsonify(ok=True, source="netease", lrc=lrc, name=sname)
    # 3) AI 生成歌词（DeepSeek）
    try:
        import os as _os
        _ds_key = _os.environ.get("DEEPSEEK_API_KEY", "")
        if _ds_key:
            _prompt = f"请完整输出歌曲《{track}》的歌词"
            if clean_artist:
                _prompt += f"，演唱：{clean_artist}"
            _prompt += "。只输出歌词文本，不要任何解释或标记。"
            _body = _json.dumps({
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": _prompt}],
                "max_tokens": 2000,
                "temperature": 0.3
            }).encode("utf-8")
            _req = _u.Request("https://api.deepseek.com/v1/chat/completions",
                data=_body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {_ds_key}"})
            with _u.urlopen(_req, timeout=30) as r:
                _cj = _json.loads(r.read().decode())
            _lyrics = ((_cj.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
            if _lyrics and len(_lyrics) > 20:
                return jsonify(ok=True, source="ai", lrc=_lyrics, name=track)
    except Exception:
        pass

    return jsonify(ok=False, error="not found")

@app.route("/api/lrc-cache", methods=["OPTIONS", "POST", "GET"])
def api_lrc_cache():
    """歌词缓存：POST 存 / GET 取"""
    import os as _os, hashlib as _hash
    if request.method == 'OPTIONS':
        _r = jsonify(ok=True)
        _r.headers['Access-Control-Allow-Origin'] = '*'
        _r.headers['Access-Control-Allow-Methods'] = 'POST, GET, OPTIONS'
        return _r
    cache_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'lrc_cache')
    _os.makedirs(cache_dir, exist_ok=True)
    def _key(track, artist):
        raw = (artist.lower() + '|' + track.lower()).encode('utf-8')
        return _hash.md5(raw).hexdigest() + '.lrc'
    track = (request.values.get('track') or '').strip()
    artist = (request.values.get('artist') or '').strip()
    if not track:
        return jsonify(ok=False, error='missing track'), 400
    fp = _os.path.join(cache_dir, _key(track, artist))
    if request.method == 'POST':
        lrc = request.get_data(as_text=True) or ''
        if lrc.strip():
            with open(fp, 'w', encoding='utf-8') as f:
                f.write(lrc)
        return jsonify(ok=True)
    else:
        if _os.path.exists(fp):
            with open(fp, 'r', encoding='utf-8') as f:
                return jsonify(ok=True, lrc=f.read(), source='cache')
        return jsonify(ok=False)

@app.after_request
def _add_lrc_cors(resp):
    if request.path == '/api/lrc-lookup':
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    return resp

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
        volc_voice = _volc_voice_for(voice) if voice else None
        if volc_voice:
            ck = _tts_cache_key(volc_voice, rate, text)
            cached = _tts_cache_get(ck)
            if cached is not None:
                return Response(cached, mimetype="audio/mpeg")
            try:
                data = _volc_tts_bytes(text, volc_voice, rate)
                _tts_cache_put(ck, data)
                return Response(data, mimetype="audio/mpeg")
            except Exception:
                pass
        data = engine.tts_bytes(text, lang, voice, rate)
        return Response(data, mimetype="audio/mpeg")
    except Exception:
        abort(502)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("TRANSLATOR_PORT", "5030")))
