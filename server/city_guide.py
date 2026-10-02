"""城市攻略按需抓取：中文 Wikivoyage + Wikipedia，带本地缓存。

App（我的推荐）在本地攻略库没有命中该城市时，会调用 /api/city-guide?city=xx，
这里实时去取真实攻略内容，解析出「美食 / 景点 / 住宿 / 街区」，落盘缓存后返回。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "city_guide"
CACHE_TTL = 30 * 24 * 3600  # 30 天
# Wikimedia 对 UA 有硬性要求（必须带联系方式），否则 403 Too Many Reqs
UA = "MyRecommend/1.0 (https://5130599.best/Translator/; personal travel guide) python-urllib"
_LAST_CALL = [0.0]

SECTION_MAP = {
    "foods": ("用餐", "吃", "美食", "饮食", "小吃", "餐饮"),
    "spots": ("观光", "景点", "游览", "看", "活动", "必看", "地标"),
    "hotels": ("住宿", "睡", "酒店", "旅馆", "宾馆"),
    "streets": ("购物", "市场", "商圈", "街区"),
}


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
    t = re.sub(r"\[\[([^\[\]|]+)\|([^\[\]]+)\]\]", r"\2", t)   # [[A|B]] -> B
    t = re.sub(r"\[\[([^\[\]]+)\]\]", r"\1", t)                # [[A]] -> A
    t = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", t)
    t = re.sub(r"https?://\S+", "", t)
    t = t.replace("'''", "").replace("''", "")
    t = re.sub(r"^[*#:;\s]+", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip(" ，,。.;；、-·|")


def _split_name_desc(line: str):
    """把一条列表项拆成 (名称, 说明)。"""
    s = _clean(line)
    if not s:
        return None
    # 常见格式：名称 —— 说明 / 名称：说明 / 名称，说明
    for sep in ("——", "—", "：", ":", "，", ",", "。", "("):
        if sep in s:
            a, b = s.split(sep, 1)
            a, b = a.strip(), b.strip()
            if 1 <= len(a) <= 24 and len(b) >= 4:
                return [a, b[:60]]
    if len(s) <= 26:
        return [s, ""]
    return [s[:18], s[18:80]]


def _iter_templates(text: str):
    """按花括号配对扫描出所有 {{...}} 模板（含多行）。"""
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


# Wikivoyage 列表模板 → 分类
# 这些不算景点（多来自“抵达”“交通”段）
SKIP_WORDS = ("机场", "機場", "车站", "車站", "火车站", "高铁站", "汽车站", "客运站",
              "码头", "碼頭", "地铁站", "服務區", "服务区", "收费站", "高速公路",
              "人民政府", "市政府", "公安局", "派出所", "镇政府", "邮局", "供电局",
              "行政中心", "政务中心", "政務中心")


def _skip(name: str) -> bool:
    return any(w in name for w in SKIP_WORDS)


TPL_TYPE = {
    "see": "spots", "do": "spots", "listing": "spots",
    "eat": "foods", "drink": "foods",
    "sleep": "hotels",
    "buy": "streets",
}


def _tpl_params(tpl: str):
    """拆 {{see | name=x | content=y}} → (类型, {参数})。"""
    body = tpl[2:-2]
    body = re.sub(r"\{\{.*?\}\}", "", body, flags=re.S)  # 去掉内层嵌套
    parts = body.split("|")
    kind = parts[0].strip().lower()
    d = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            d[k.strip().lower()] = _clean(v)
    return kind, d


def _from_templates(wikitext: str) -> dict:
    """从结构化模板提取，质量最高。"""
    buckets = {k: [] for k in SECTION_MAP}
    for tpl in _iter_templates(wikitext):
        try:
            kind, d = _tpl_params(tpl)
        except Exception:
            continue
        key = TPL_TYPE.get(kind)
        if not key:
            continue
        name = d.get("name") or d.get("alt") or ""
        if not name:
            continue
        name = _clean(name)[:26]
        if not name:
            continue
        if key == "spots" and _skip(name):
            continue
        desc = d.get("content") or d.get("description") or ""
        desc = _clean(desc)[:70]
        item = [name, desc]
        if item not in buckets[key]:
            buckets[key].append(item)
    return buckets


def _parse_sections(wikitext: str) -> dict:
    """按 == 标题 == 切段，归类提取列表项。"""
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
        if stripped.startswith(("*", "#")) or (len(stripped) > 8 and "。" in stripped):
            item = _split_name_desc(stripped)
            if (item and item[0] and len(item[0]) >= 2 and not item[0].startswith("{")
                    and not (cur == "spots" and _skip(item[0])) and item not in buckets[cur]):
                buckets[cur].append(item)
    return buckets


def _wikivoyage(city: str):
    """取 Wikivoyage 正文并解析；返回 (title, buckets) 或 None。"""
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
        # 结构化模板优先，其次标题分段的列表
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
            j = _http_json(
                "https://zh.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(name)
            )
            ex = (j or {}).get("extract") or ""
            if ex:
                return ex.strip()[:220]
        except Exception:
            continue
    return ""


def _cache_path(city: str) -> Path:
    h = hashlib.sha1(city.encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{h}.json"


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
            "summary": "", "foods": [], "spots": [], "hotels": [], "streets": []}

    if got:
        title, buckets = got
        data["title"] = title
        data["foods"] = buckets.get("foods", [])[:10]
        data["spots"] = buckets.get("spots", [])[:10]
        data["hotels"] = buckets.get("hotels", [])[:6]
        data["streets"] = buckets.get("streets", [])[:6]
        data["ok"] = bool(data["foods"] or data["spots"] or data["hotels"])

    try:
        data["summary"] = _wikipedia_summary(city)
    except Exception:
        data["summary"] = ""

    if not data["ok"] and not data["summary"]:
        return data

    _write_cache(city, data)
    return data


if __name__ == "__main__":
    import sys
    for c in (sys.argv[1:] or ["洛阳"]):
        d = get_guide(c)
        print(f"=== {c} ok={d['ok']} 美食{len(d['foods'])} 景点{len(d['spots'])} 住宿{len(d['hotels'])}")
        for k in ("foods", "spots", "hotels", "streets"):
            for it in d[k][:3]:
                print(f"  [{k}] {it[0]} | {it[1][:40]}")
        if d.get("summary"):
            print("  简介:", d["summary"][:60])
