#!/usr/bin/env python3
"""独立 ASR 服务：语音识别（faster-whisper）+ 中文翻译（DeepSeek）

独立进程运行，模型只加载一次，避免 gunicorn 多 worker 重复占用内存。
端口 5031，仅供 nginx 内网转发。
"""
import os
import json
import time
import sqlite3
import hashlib
import tempfile
import threading
import urllib.request

from flask import Flask, request, jsonify

app = Flask(__name__)

_MODEL = None
_MAX_BYTES = 60 * 1024 * 1024  # 60MB 上限
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "asr_cache")
try:
    os.makedirs(_CACHE_DIR, exist_ok=True)
except Exception:
    pass


def _prune_cache(max_files=800):
    try:
        files = [os.path.join(_CACHE_DIR, x) for x in os.listdir(_CACHE_DIR) if x.endswith(".json")]
        if len(files) <= max_files:
            return
        files.sort(key=lambda p: os.path.getmtime(p))
        for p in files[:len(files) - max_files]:
            try:
                os.remove(p)
            except Exception:
                pass
    except Exception:
        pass


# ── 翻译记忆库（持久化在服务器，按句子文本复用，跨音频生效） ──
_TM_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "asr_tm.db")
_tm_lock = threading.Lock()
try:
    with _tm_lock:
        _c = sqlite3.connect(_TM_DB)
        _c.execute("CREATE TABLE IF NOT EXISTS tm (h TEXT PRIMARY KEY, src TEXT, zh TEXT, ts INTEGER)")
        _c.execute("CREATE TABLE IF NOT EXISTS name_idx (k TEXT PRIMARY KEY, h TEXT, ts INTEGER)")
        _c.commit()
        _c.close()
except Exception:
    pass


def _tm_key(t):
    return hashlib.sha1(t.strip().lower().encode("utf-8")).hexdigest()


def _tm_get(keys):
    out = {}
    try:
        with _tm_lock:
            con = sqlite3.connect(_TM_DB)
            cur = con.cursor()
            for k in keys:
                row = cur.execute("SELECT zh FROM tm WHERE h=?", (k,)).fetchone()
                if row and row[0]:
                    out[k] = row[0]
            con.close()
    except Exception:
        pass
    return out


def _tm_put(pairs):
    if not pairs:
        return
    try:
        with _tm_lock:
            con = sqlite3.connect(_TM_DB)
            for k, src, zh in pairs:
                con.execute("INSERT OR REPLACE INTO tm (h, src, zh, ts) VALUES (?,?,?,?)", (k, src, zh, int(time.time())))
            con.commit()
            con.close()
    except Exception:
        pass


# ── 文件名索引（name|size → 内容哈希），用于「同名文件」宽松命中 ──
def _ni_key(name, size):
    return hashlib.sha1((str(name) + "|" + str(size)).strip().lower().encode("utf-8")).hexdigest()


def _ni_get(name, size):
    try:
        with _tm_lock:
            con = sqlite3.connect(_TM_DB)
            row = con.execute("SELECT h FROM name_idx WHERE k=?", (_ni_key(name, size),)).fetchone()
            con.close()
        return row[0] if row else None
    except Exception:
        return None


def _ni_put(name, size, h):
    try:
        if not name:
            return
        with _tm_lock:
            con = sqlite3.connect(_TM_DB)
            con.execute("INSERT OR REPLACE INTO name_idx (k, h, ts) VALUES (?,?,?)", (_ni_key(name, size), h, int(time.time())))
            con.commit()
            con.close()
    except Exception:
        pass

_API_TOKEN = os.environ.get("TRANSLATOR_API_TOKEN", "").strip()
_DS_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()


def _model():
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel
        _MODEL = WhisperModel("small", device="cpu", compute_type="int8")
    return _MODEL


def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


def _translate(text_lines):
    """把编号的英文句子翻译成中文，返回同编号的中文列表（先查服务器持久化翻译记忆）"""
    if not _DS_KEY or not text_lines:
        return []
    keys = [_tm_key(t) for t in text_lines]
    memo = _tm_get(keys)
    miss = [(i, text_lines[i]) for i in range(len(text_lines)) if keys[i] not in memo]
    if not miss:
        return [memo.get(keys[i], "") for i in range(len(text_lines))]
    numbered = "\n".join("%d. %s" % (n + 1, t) for n, (_, t) in enumerate(miss))
    prompt = (
        "把下面英文语音识别结果逐句翻译成中文。"
        "保持编号格式，每行输出「编号. 中文翻译」，只输出译文，不要解释：\n" + numbered
    )
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4000,
        "temperature": 0.2,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + _DS_KEY},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        j = json.loads(r.read().decode())
    out = ((j.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    mapped = {}
    for line in out.split("\n"):
        line = line.strip()
        if not line:
            continue
        dot = line.find(".")
        if dot > 0 and line[:dot].strip().isdigit():
            mapped[int(line[:dot].strip()) - 1] = line[dot + 1:].strip()
    new_pairs = []
    for n, (i, src) in enumerate(miss):
        zh = mapped.get(n, "")
        if zh:
            memo[keys[i]] = zh
            new_pairs.append((keys[i], src, zh))
    _tm_put(new_pairs)
    return [memo.get(keys[i], "") for i in range(len(text_lines))]


@app.route("/api/diag", methods=["POST", "OPTIONS"])
def api_diag():
    """接收客户端上传的文件用于分析，存到 data/diag/（与 ASR 同一服务，走同样的 nginx 精确匹配）"""
    if request.method == "OPTIONS":
        return _cors(jsonify(ok=True))
    tok = (request.args.get("token") or "").strip()
    if _API_TOKEN and tok != _API_TOKEN:
        return _cors(jsonify(ok=False, error="unauthorized")), 401
    try:
        import re as _re
        name = (request.args.get("name") or "upload.bin").strip()
        safe = _re.sub(r'[^\w\u4e00-\u9fff.\-\[\]() ]', '_', name)[:140] or "upload.bin"
        data = request.get_data()
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "diag")
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, time.strftime("%Y%m%d_%H%M%S_") + safe)
        with open(fp, "wb") as f:
            f.write(data)
        return _cors(jsonify(ok=True, saved=os.path.basename(fp), size=len(data)))
    except Exception as e:
        return _cors(jsonify(ok=False, error=str(e)[:150])), 500


@app.route("/api/asr", methods=["POST", "OPTIONS", "GET"])
def api_asr():
    if request.method == "OPTIONS":
        return _cors(jsonify(ok=True))
    tok = (request.args.get("token") or "").strip()
    if _API_TOKEN and tok != _API_TOKEN:
        return _cors(jsonify(ok=False, error="unauthorized")), 401

    # GET：免上传预检（只传哈希/文件名+大小，命中直接返回结果，不走上传）
    if request.method == "GET":
        h = (request.args.get("check") or "").strip()
        nm = (request.args.get("name") or "").strip()
        sz = (request.args.get("size") or "").strip()
        cands = []
        if h:
            cands.append((os.path.join(_CACHE_DIR, h + ".json"), "hash"))
        if nm and sz:
            nh = _ni_get(nm, sz)
            if nh:
                cands.append((os.path.join(_CACHE_DIR, nh + ".json"), "name"))
        for fp, via in cands:
            if os.path.exists(fp):
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        cached = json.load(f)
                    cached["cached"] = True
                    cached["via"] = via
                    return _cors(jsonify(cached))
                except Exception:
                    pass
        return _cors(jsonify(ok=False, miss=True))

    data = request.get_data()
    if not data or len(data) < 512:
        return _cors(jsonify(ok=False, error="no audio")), 400
    if len(data) > _MAX_BYTES:
        return _cors(jsonify(ok=False, error="audio too large")), 413

    rq_name = (request.args.get("name") or "").strip()
    rq_size = (request.args.get("size") or "").strip() or str(len(data))

    # 按音频内容哈希缓存：命中则直接返回，不再调用 AI
    audio_hash = hashlib.sha256(data).hexdigest()[:32]
    cache_fp = os.path.join(_CACHE_DIR, audio_hash + ".json")
    if os.path.exists(cache_fp):
        try:
            with open(cache_fp, "r", encoding="utf-8") as f:
                cached = json.load(f)
            cached["cached"] = True
            cached["via"] = "hash"
            if rq_name:
                _ni_put(rq_name, rq_size, audio_hash)
            return _cors(jsonify(cached))
        except Exception:
            pass

    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".bin")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        m = _model()
        segments, info = m.transcribe(tmp, beam_size=1, vad_filter=True)
        segs = []
        for s in segments:
            txt = s.text.strip()
            if txt:
                segs.append({"s": round(s.start, 1), "e": round(s.end, 1), "t": txt})
        lang = (info.language or "").lower()
        zh = []
        if lang != "zh" and segs:
            try:
                zh = _translate([x["t"] for x in segs])
            except Exception:
                zh = []
        for i, x in enumerate(segs):
            x["zh"] = zh[i] if i < len(zh) else ""
        result = {"ok": True, "lang": lang, "segments": segs}
        try:
            with open(cache_fp, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)
            _prune_cache()
        except Exception:
            pass
        if rq_name:
            _ni_put(rq_name, rq_size, audio_hash)
        return _cors(jsonify(result))
    except Exception as e:
        return _cors(jsonify(ok=False, error=str(e)[:160])), 500
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5031, threaded=True)
