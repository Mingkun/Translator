"""城市攻略 + 行程规划：中文 Wikivoyage / Wikipedia 按需抓取，带本地缓存。

App（我的推荐）调用：
  /api/city-guide?city=xx   → 美食/景点/住宿/街区/抵达/实用信息 + 简介
  /api/trip?from=xx&to=yy   → 距离、高铁/飞机时长与票价区间、推荐方式、两地火车站
全部内容由服务端整理好，App 内直接呈现，不跳转外部 App。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CACHE_DIR = DATA_DIR / "city_guide"
CACHE_TTL = 30 * 24 * 3600  # 30 天
UA = "MyRecommend/1.0 (https://5130599.best/Translator/; personal travel guide) python-urllib"
_LAST_CALL = [0.0]

SECTION_MAP = {
    "foods": ("用餐", "吃", "美食", "饮食", "小吃", "餐饮"),
    "spots": ("观光", "景点", "游览", "看", "活动", "必看", "地标"),
    "hotels": ("住宿", "睡", "酒店", "旅馆", "宾馆"),
    "streets": ("购物", "市场", "商圈", "街区"),
    "arrive": ("抵达", "到达", "交通", "铁路", "航空", "长途"),
    "tips": ("了解", "实用", "注意", "安全", "通讯", "习俗", "提示"),
}

TPL_TYPE = {
    "see": "spots", "do": "spots", "listing": "spots",
    "eat": "foods", "drink": "foods",
    "sleep": "hotels",
    "buy": "streets",
    "go": "arrive", "around": "arrive",
}

# 这些不算景点/美食（多来自“抵达”“交通”段）
SKIP_WORDS = ("机场", "機場", "车站", "車站", "火车站", "高铁站", "汽车站", "客运站",
              "码头", "碼頭", "地铁站", "服务区", "服務區", "收费站", "高速公路",
              "人民政府", "市政府", "公安局", "派出所", "镇政府", "邮局", "供电局",
              "行政中心", "政务中心", "政務中心")


def _skip(name: str) -> bool:
    return any(w in name for w in SKIP_WORDS)


def _http_json(url: str, timeout: int = 10, attempts: int = 3):
    """带节流 + 退避重试的 JSON 请求（Wikimedia 限流会返回 403）。"""
    last = None
    for i in range(attempts):
        gap = time.time() - _LAST_CALL[0]
        if gap < 1.2:
            time.sleep(1.2 - gap)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                _LAST_CALL[0] = time.time()
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            _LAST_CALL[0] = time.time()
            last = e
            time.sleep(2.0 + 2.0 * i)
    if last:
        raise last
    return {}


def _clean(text: str) -> str:
    """去掉 wiki 标记，得到可读纯文本。"""
    t = text
    t = re.sub(r"\{\{[^{}]*\}\}", "", t)
    t = re.sub(r"<ref[^>]*>.*?</ref>", "", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\[\[([^\[\]|]+)\|([^\[\]]+)\]\]", r"\2", t)
    t = re.sub(r"\[\[([^\[\]]+)\]\]", r"\1", t)
    t = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", t)
    t = re.sub(r"https?://\S+", "", t)
    t = t.replace("'''", "").replace("''", "")
    t = re.sub(r"^[*#:;\s]+", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip(" ，,。.;；、-·|")


def _split_name_desc(line: str):
    s = _clean(line)
    if not s:
        return None
    for sep in ("——", "—", "：", ":", "，", ",", "。", "("):
        if sep in s:
            a, b = s.split(sep, 1)
            a, b = a.strip(), b.strip()
            if 1 <= len(a) <= 24 and len(b) >= 4:
                return [a, b[:70]]
    if len(s) <= 26:
        return [s, ""]
    return [s[:18], s[18:90]]


def _iter_templates(text: str):
    i = 0
    n = len(text)
    while i < n:
        s = text.find("{{", i)
        if s < 0:
            return
        depth = 0
        j = s
        while j < n:
            if text.startswith("{{", j):
                depth += 1
                j += 2
            elif text.startswith("}}", j):
                depth -= 1
                j += 2
                if depth == 0:
                    break
            else:
                j += 1
        yield text[s:j]
        i = max(j, s + 2)


def _tpl_params(tpl: str):
    body = tpl[2:-2]
    body = re.sub(r"\{\{.*?\}\}", "", body, flags=re.S)
    parts = body.split("|")
    kind = parts[0].strip().lower()
    d = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            d[k.strip().lower()] = _clean(v)
    return kind, d


def _from_templates(wikitext: str) -> dict:
    buckets = {k: [] for k in SECTION_MAP}
    for tpl in _iter_templates(wikitext):
        try:
            kind, d = _tpl_params(tpl)
        except Exception:
            continue
        key = TPL_TYPE.get(kind)
        if not key:
            continue
        name = _clean(d.get("name") or d.get("alt") or "")[:26]
        if not name:
            continue
        if key in ("spots", "foods") and _skip(name):
            continue
        desc = _clean(d.get("content") or d.get("description") or "")[:70]
        item = [name, desc]
        if item not in buckets[key]:
            buckets[key].append(item)
    return buckets


def _parse_sections(wikitext: str) -> dict:
    """按 == 标题 == 切段；列表类分类抽取，正文类（抵达/实用）取段落文字。"""
    lines = wikitext.split("\n")
    cur = None
    buckets = {k: [] for k in SECTION_MAP}
    heading = re.compile(r"^\s*={2,4}\s*(.+?)\s*={2,4}\s*$")

    for ln in lines:
        m = heading.match(ln)
        if m:
            title = _clean(m.group(1))
            cur = None
            for key, words in SECTION_MAP.items():
                if any(w in title for w in words):
                    cur = key
                    break
            continue
        if not cur:
            continue
        stripped = ln.strip()
        if not stripped or stripped.startswith(("{{", "|", "!", "=")):
            continue
        if stripped.startswith(("*", "#")):
            item = _split_name_desc(stripped)
            if (item and item[0] and len(item[0]) >= 2 and not item[0].startswith("{")
                    and not (cur in ("spots", "foods") and _skip(item[0]))
                    and item not in buckets[cur]):
                buckets[cur].append(item)
        elif cur in ("arrive", "tips") and len(stripped) > 12 and "。" in stripped:
            txt = _clean(stripped)
            if txt and [txt, ""] not in buckets[cur]:
                buckets[cur].append([txt, ""])
    return buckets


def _wikivoyage(city: str):
    for name in (city, f"{city}市"):
        q = urllib.parse.urlencode({
            "action": "parse", "page": name, "prop": "wikitext",
            "format": "json", "formatversion": "2", "redirects": "1",
        })
        try:
            j = _http_json(f"https://zh.wikivoyage.org/w/api.php?{q}")
        except Exception:
            continue
        wt = (((j or {}).get("parse") or {}).get("wikitext") or "")
        if len(wt) < 200:
            continue
        buckets = _from_templates(wt)
        sec = _parse_sections(wt)
        for k, items in sec.items():
            have = {x[0] for x in buckets[k]}
            for it in items:
                if it[0] not in have:
                    buckets[k].append(it)
        if any(buckets.values()):
            return ((j.get("parse") or {}).get("title") or name), buckets
    return None


def _wikipedia_summary(city: str) -> str:
    for name in (city, f"{city}市"):
        try:
            j = _http_json("https://zh.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(name))
            ex = (j or {}).get("extract") or ""
            if ex:
                return ex.strip()[:240]
        except Exception:
            continue
    return ""


def coords(city: str):
    """城市坐标：Wikivoyage → Wikipedia。"""
    for host in ("zh.wikivoyage.org", "zh.wikipedia.org"):
        for name in (city, f"{city}市"):
            q = urllib.parse.urlencode({
                "action": "query", "prop": "coordinates", "titles": name,
                "format": "json", "formatversion": "2", "redirects": "1",
            })
            try:
                j = _http_json(f"https://{host}/w/api.php?{q}")
            except Exception:
                continue
            pages = ((j or {}).get("query") or {}).get("pages") or []
            if pages and pages[0].get("coordinates"):
                c = pages[0]["coordinates"][0]
                return float(c["lat"]), float(c["lon"])
    return None


# 缓存版本：解析/提示词逻辑一变就升级，旧缓存自动失效
GUIDE_CACHE_VERSION = "v2"


def _cache_path(city: str) -> Path:
    h = hashlib.sha1(city.encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{GUIDE_CACHE_VERSION}-{h}.json"


def _read_cache(city: str):
    p = _cache_path(city)
    try:
        if p.is_file() and (time.time() - p.stat().st_mtime) < CACHE_TTL:
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def _write_cache(city: str, data: dict):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(city).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def get_guide(city: str, refresh: bool = False) -> dict:
    city = (city or "").strip()[:32]
    if not city:
        return {"ok": False, "city": city, "reason": "empty-city"}

    if not refresh:
        hit = _read_cache(city)
        if hit:
            hit["cached"] = True
            return hit

    got = None
    try:
        got = _wikivoyage(city)
    except Exception:
        got = None

    data = {"ok": False, "city": city, "source": "wikivoyage", "cached": False,
            "summary": "", "foods": [], "spots": [], "hotels": [],
            "streets": [], "arrive": [], "tips": []}

    if got:
        title, buckets = got
        data["title"] = title
        data["foods"] = buckets.get("foods", [])[:12]
        data["spots"] = buckets.get("spots", [])[:12]
        data["hotels"] = buckets.get("hotels", [])[:8]
        data["streets"] = buckets.get("streets", [])[:8]
        data["arrive"] = buckets.get("arrive", [])[:5]
        data["tips"] = buckets.get("tips", [])[:5]
        data["ok"] = bool(data["foods"] or data["spots"] or data["hotels"])

    try:
        data["summary"] = _wikipedia_summary(city)
    except Exception:
        data["summary"] = ""

    # 交给大模型整合梳理（素材零散/繁体/有噪音时效果最明显）
    try:
        ai = _llm_guide(city, data)
    except Exception:
        ai = None
    if ai:
        data["summary"] = ai["summary"] or data["summary"]
        for k in ("foods", "spots", "hotels", "streets", "arrive", "tips"):
            if ai.get(k):
                data[k] = ai[k]
        data["ok"] = True
        data["ai"] = True

    if not data["ok"] and not data["summary"]:
        return data

    _write_cache(city, data)
    return data


# ==================== 大模型整理 ====================

LLM_SYSTEM = (
    "你是资深的城市旅行攻略编辑，擅长把零散素材整理成准确、实用的攻略。"
    "只输出 JSON，不要任何解释文字。所有内容用简体中文。"
)


def _llm_json(system: str, user: str, max_tokens: int = 8192):
    """调大模型并返回解析后的 JSON。DeepSeek 主 → zai GLM 备。失败返回 None。

    总预算 36 秒（gunicorn 工作进程 60 秒超时，App 取数也有限时）。
    """
    started = time.time()
    try:
        import translate as engine  # 复用项目已有的密钥读取与 JSON 提取
    except Exception:
        return None

    payload_base = {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        # 注意：deepseek-flash 是推理模型，开 json_object 时推理会吃掉预算、content 返回空串；
        # 因此不开 response_format，靠提示词约束 + 本地 JSON 提取。
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }
    attempts = []
    # deepseek-chat 是非推理模型：秒回、不占推理预算（实测 3 秒 vs 推理模型 16 秒）；
    # 失败再退到推理模型 deepseek-flash，最后 GLM。
    try:
        dk = engine._deepseek_key()
        if dk:
            attempts.append(("https://api.deepseek.com/chat/completions", "deepseek-chat", dk))
            attempts.append(("https://api.deepseek.com/chat/completions", "deepseek-flash", dk))
    except Exception:
        pass
    try:
        zk = engine._zai_key()
        if zk:
            attempts.append(("https://api.z.ai/api/coding/paas/v4/chat/completions", "glm-5.3", zk))
    except Exception:
        pass

    for url, model, key in attempts:
        if time.time() - started > 34:
            break
        body = dict(payload_base)
        body["model"] = model
        # 非推理模型给足就行；推理模型需要 8192，否则推理会吃掉全部预算
        if model.endswith("flash"):
            body["max_tokens"] = max(int(body.get("max_tokens") or 0), 8192)
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(), method="POST",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {key}",
                         "User-Agent": UA})
            left = max(6.0, 36.0 - (time.time() - started))
            with urllib.request.urlopen(req, timeout=left) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            content = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            parsed = engine._extract_json_object(content)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return None


def _pairs(v, limit: int):
    """把模型返回的数组规范成 [[名称, 说明], ...]。"""
    out = []
    if not isinstance(v, list):
        return out
    for it in v:
        if isinstance(it, (list, tuple)) and len(it) >= 2:
            a, b = _clean(str(it[0])), _clean(str(it[1]))
        elif isinstance(it, dict):
            a = _clean(str(it.get("name") or it.get("title") or ""))
            b = _clean(str(it.get("desc") or it.get("content") or it.get("note") or ""))
        else:
            a, b = _clean(str(it)), ""
        if a and [a, b] not in out:
            out.append([a, b[:80]])
        if len(out) >= limit:
            break
    return out


def _build_guide_prompt(city: str, raw: dict, limit: int = 12) -> str:
    lines = []
    if raw.get("summary"):
        lines.append("【简介】" + raw["summary"][:300])
    for key, label in (("spots", "景点"), ("foods", "美食"), ("hotels", "住宿"),
                       ("streets", "街区购物"), ("arrive", "抵达交通"), ("tips", "实用信息")):
        items = raw.get(key) or []
        if items:
            txt = "；".join((i[0] + ("—" + i[1] if len(i) > 1 and i[1] else "")) for i in items[:limit])
            lines.append(f"【{label}】{txt[:800]}")
    if not lines:
        lines.append("（无外部素材）")
    return (
        f"请为「{city}」整理一份旅行攻略，输出 JSON，字段固定为：\n"
        '{"summary":"60-120字城市概览",'
        '"foods":[["菜名","一句话说明"]],"spots":[["景点名","一句话说明"]],'
        '"hotels":[["住宿区域或代表性酒店","一句话说明"]],'
        '"streets":[["街区/商圈","一句话说明"]],'
        '"arrive":[["交通方式","说明"]],"tips":[["要点","说明"]]}\n'
        "数量：foods 5-8、spots 6-10、hotels 3-5、streets 2-4、arrive 2-4、tips 3-5。\n"
        "要求：基于素材整理，剔除机场/车站/政务机构等非游玩项；素材不足时用你确知的地道内容补齐；"
        "不要编造不存在的地点；每条说明不超过 40 字；全部简体中文。\n\n"
        f"素材如下：\n" + "\n".join(lines)
    )


def _llm_guide(city: str, raw: dict):
    """把抓取到的零散素材交给大模型整合成结构化攻略。

    deepseek-flash 是推理模型：推理过程会占掉大量 token（实测 5-6k），
    max_tokens 给小了会得到 finish_reason=length、content 为空 → 必须给足 8192。
    首次失败时用更精简的素材再试一次。
    """
    for limit, mt in ((12, 8192), (6, 8192)):
        j = _llm_json(LLM_SYSTEM, _build_guide_prompt(city, raw, limit), max_tokens=mt)
        out = _normalize_guide(j)
        if out:
            return out
    return None


def _normalize_guide(j):
    if not isinstance(j, dict):
        return None
    out = {
        "summary": _clean(str(j.get("summary") or ""))[:300],
        "foods": _pairs(j.get("foods"), 10),
        "spots": _pairs(j.get("spots"), 12),
        "hotels": _pairs(j.get("hotels"), 6),
        "streets": _pairs(j.get("streets"), 5),
        "arrive": _pairs(j.get("arrive"), 5),
        "tips": _pairs(j.get("tips"), 5),
    }
    if not any(out[k] for k in ("foods", "spots", "hotels")):
        return None
    return out


# ==================== 行程规划 ====================

STATION_CACHE = DATA_DIR / "station_names.js"
TRIP_CACHE_DIR = DATA_DIR / "trip_cache"
TRIP_TTL = 7 * 24 * 3600
# 行程提示词一变就升级版本，旧缓存自动失效
TRIP_CACHE_VERSION = "v2"


def _trip_key(frm: str, to: str) -> str:
    return hashlib.sha1((frm + "|" + to).encode("utf-8")).hexdigest()[:16]


def _trip_cache_read(frm: str, to: str):
    p = TRIP_CACHE_DIR / f"{TRIP_CACHE_VERSION}-{_trip_key(frm, to)}.json"
    try:
        if p.is_file() and (time.time() - p.stat().st_mtime) < TRIP_TTL:
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def _trip_cache_write(frm: str, to: str, data: dict):
    try:
        TRIP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (TRIP_CACHE_DIR / f"{TRIP_CACHE_VERSION}-{_trip_key(frm, to)}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _llm_trip(frm: str, to: str, base: dict):
    """让大模型给出这条线路的真实车次与运行时长，并整合成可执行方案。

    距离/票价区间仍由服务端算（可复核），车次与时长用模型知识（非实时）。
    """
    facts = []
    if base.get("distance"):
        facts.append(f"直线推算陆路距离约 {base['distance']} 公里")
    fs = "、".join(s["name"] for s in (base.get("fromStations") or [])[:4]) or "未知"
    ts = "、".join(s["name"] for s in (base.get("toStations") or [])[:4]) or "未知"
    user = (
        f"请给出「{frm} → {to}」的真实出行信息。参考：{frm}车站有 {fs}；{to}车站有 {ts}。\n"
        '输出 JSON：{"summary":"一句话总体建议",'
        '"hsrHours":"高铁实际约几小时（含中转，如 6-7 小时）",'
        '"trains":[["车次或方案","区间/时长/说明"]],'
        '"modes":[["交通方式","什么时候选它、要注意什么"]],'
        '"plan":[["环节","具体做法"]],"tips":[["要点","说明"]]}\n'
        "要求：\n"
        "1) trains 列出这条线路的 3-6 个常见高铁方案，写清是直达还是中转、大致运行时长、经停主要城市；"
        "能确定车次号（如 G 字头）就写，不确定就写「直达」或「经××中转」而不编造具体车号。\n"
        "2) hsrHours 必须是你判断的实际时长（山区/绕行线路往往比直线距离估的更长），不要照抄按距离算的值。\n"
        "3) modes 给 2-3 条（高铁/飞机/自驾）；plan 给 4 条（订票/出发当天/途中/到达后）；tips 给 3 条。\n"
        "4) 每条说明 40 字以内，简体中文。\n"
        + (f"5) 距离参考（仅参考，不要直接据此算时长）：约 {base.get('distance')} 公里。\n" if base.get("distance") else "")
    )
    j = _llm_json(LLM_SYSTEM, user, max_tokens=8192)
    if not isinstance(j, dict):
        return None
    out = {
        "summary": _clean(str(j.get("summary") or ""))[:120],
        "hsrHours": _clean(str(j.get("hsrHours") or ""))[:40],
        "trains": _pairs(j.get("trains"), 8),
        "modes": _pairs(j.get("modes"), 4),
        "plan": _pairs(j.get("plan"), 6),
        "tips": _pairs(j.get("tips"), 5),
    }
    if not (out["plan"] or out["trains"] or out["summary"]):
        return None
    return out


def _station_table():
    """12306 官方站点表（含电报码），本地缓存 30 天。"""
    try:
        if (not STATION_CACHE.is_file()
                or time.time() - STATION_CACHE.stat().st_mtime > CACHE_TTL):
            req = urllib.request.Request(
                "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js",
                headers={"User-Agent": "Mozilla/5.0 (Linux; Android)"})
            with urllib.request.urlopen(req, timeout=15) as r:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                STATION_CACHE.write_bytes(r.read())
        raw = STATION_CACHE.read_text(encoding="utf-8", errors="replace")
        return re.findall(r"@[a-z]+\|([^|]+)\|([A-Z]+)\|", raw)
    except Exception:
        return []


def city_stations(city: str):
    city = (city or "").replace("市", "").strip()
    out = []
    for name, code in _station_table():
        n = name.replace("市", "")
        if n.startswith(city) or n == city:
            out.append({"name": name, "code": code})
    # 主站优先：名称最短/不含方位后缀的排前面
    out.sort(key=lambda x: len(x["name"]))
    return out[:6]


def _haversine(a, b) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def get_trip(frm: str, to: str) -> dict:
    frm = (frm or "").strip()[:24]
    to = (to or "").strip()[:24]
    if not frm or not to:
        return {"ok": False, "reason": "missing-city"}

    hit = _trip_cache_read(frm, to)
    if hit:
        hit["cached"] = True
        return hit

    ca, cb = coords(frm), coords(to)
    res = {"ok": True, "from": frm, "to": to,
           "fromStations": city_stations(frm), "toStations": city_stations(to),
           "distance": None, "modes": [], "advice": "",
           "ai": False, "summary": "", "plan": [], "tips": []}
    if ca and cb:
        km = _haversine(ca, cb) * 1.22  # 陆路里程约为直线 1.2 倍
        res["distance"] = round(km)

        # 高铁：有效均速约 230km/h（含停站），二等座约 0.46 元/km
        hh = km / 230.0
        hsr_h = int(hh) + 1
        hsr_price = max(30, round(km * 0.46 / 5) * 5)
        res["modes"].append({
            "mode": "高铁", "hours": round(hh, 1), "hoursText": f"约 {hsr_h} 小时",
            "priceText": f"二等座约 {hsr_price} 元",
            "note": "含安检进站约需再留 30 分钟",
        })

        # 飞机：巡航 750km/h + 前后各约 1 小时地面流程
        ff = km / 750.0 + 2.0
        fprice_lo = max(300, round(km * 0.55 / 50) * 50)
        fprice_hi = max(600, round(km * 1.05 / 50) * 50)
        res["modes"].append({
            "mode": "飞机", "hours": round(ff, 1),
            "hoursText": f"约 {int(ff) + 1} 小时（含机场流程）",
            "priceText": f"约 {fprice_lo}~{fprice_hi} 元",
            "note": "建议提前 3~6 周订票，周二/周三出发通常更低",
        })

        if km < 700:
            res["advice"] = f"两地约 {round(km)} 公里，高铁更省心（门到门通常比飞机快），推荐高铁。"
        elif km < 1500:
            res["advice"] = f"两地约 {round(km)} 公里，高铁与飞机时间接近；赶时间选飞机、图省心选高铁。"
        else:
            res["advice"] = f"两地约 {round(km)} 公里，路途较远，推荐飞机；若沿途游玩可考虑分段高铁。"
        if res["fromStations"] and res["toStations"]:
            res["advice"] += f"　车站参考：{res['fromStations'][0]['name']} → {res['toStations'][0]['name']}。"
    else:
        res["advice"] = "未取到两地坐标，无法估算距离；可参考两地火车站名称规划。"

    # 大模型把事实数据整合成可执行方案
    try:
        ai = _llm_trip(frm, to, res)
    except Exception:
        ai = None
    if ai:
        res["ai"] = True
        if ai.get("summary"):
            res["summary"] = ai["summary"]
        if ai.get("plan"):
            res["plan"] = ai["plan"]
        if ai.get("tips"):
            res["tips"] = ai["tips"]
        if ai.get("modes"):
            res["aiModes"] = ai["modes"]
        if ai.get("trains"):
            res["trains"] = ai["trains"]
        # 用模型的真实判断纠正按距离估算的时长
        if ai.get("hsrHours"):
            for m in res.get("modes", []):
                if m.get("mode") == "高铁":
                    m["hoursText"] = ai["hsrHours"] + "（大模型判断）"
                    m["estimated"] = False
            res["hsrHours"] = ai["hsrHours"]

    _trip_cache_write(frm, to, res)
    return res


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if args and args[0] == "trip":
        print(json.dumps(get_trip(args[1], args[2]), ensure_ascii=False, indent=1))
    else:
        for c in (args or ["洛阳"]):
            d = get_guide(c)
            print(f"=== {c} ok={d['ok']} 食{len(d['foods'])} 景{len(d['spots'])} 宿{len(d['hotels'])} 抵达{len(d['arrive'])} 实用{len(d['tips'])}")
            for it in d["spots"][:3]:
                print("  景:", it[0], "|", it[1][:36])
