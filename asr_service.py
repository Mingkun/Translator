#!/usr/bin/env python3
"""独立 ASR 服务：语音识别（faster-whisper）+ 中文翻译（DeepSeek）

独立进程运行，模型只加载一次，避免 gunicorn 多 worker 重复占用内存。
端口 5031，仅供 nginx 内网转发。
"""
import os
import json
import hashlib
import tempfile
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
    """把编号的英文句子翻译成中文，返回同编号的中文列表"""
    if not _DS_KEY or not text_lines:
        return []
    numbered = "\n".join("%d. %s" % (i + 1, t) for i, t in enumerate(text_lines))
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
    # 解析 "N. 译文"
    mapped = {}
    for line in out.split("\n"):
        line = line.strip()
        if not line:
            continue
        dot = line.find(".")
        if dot > 0 and line[:dot].strip().isdigit():
            mapped[int(line[:dot].strip()) - 1] = line[dot + 1:].strip()
    return [mapped.get(i, "") for i in range(len(text_lines))]


@app.route("/api/asr", methods=["POST", "OPTIONS"])
def api_asr():
    if request.method == "OPTIONS":
        return _cors(jsonify(ok=True))
    tok = (request.args.get("token") or "").strip()
    if _API_TOKEN and tok != _API_TOKEN:
        return _cors(jsonify(ok=False, error="unauthorized")), 401

    data = request.get_data()
    if not data or len(data) < 512:
        return _cors(jsonify(ok=False, error="no audio")), 400
    if len(data) > _MAX_BYTES:
        return _cors(jsonify(ok=False, error="audio too large")), 413

    # 按音频内容哈希缓存：命中则直接返回，不再调用 AI
    audio_hash = hashlib.sha256(data).hexdigest()[:32]
    cache_fp = os.path.join(_CACHE_DIR, audio_hash + ".json")
    if os.path.exists(cache_fp):
        try:
            with open(cache_fp, "r", encoding="utf-8") as f:
                cached = json.load(f)
            cached["cached"] = True
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
