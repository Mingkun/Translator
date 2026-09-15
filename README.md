# 翻译鹦鹉 🦜（Translator）

任务4 · 中英互译服务端与安卓客户端。

## 功能
- 中英互译（自动检测语言方向）
- 译文侧提供：常用含义（词性+释义）、发音音标（IPA）、发音音频（词典真人发音 / TTS 兜底）、常用例句及例句发音
- 服务端后台：Flask + Gunicorn，本机 systemd 部署，nginx 反代 `https://5130599.best/Translator/`
- 安卓客户端：WebView 壳，调用本机服务端
- SQLite 缓存翻译与词典结果，音频文件本地缓存，减少外部 API 压力

## 结构
```
server/          Flask 服务（app.py 路由 + translate.py 引擎）
static/          网页界面
deploy/          systemd / nginx 部署文件
android/         安卓客户端工程
data/            运行时缓存（不入库）
```

## API
- `GET /Translator/api/translate?q=<文本>&from=auto&to=` — 翻译+词典增强
- `GET /Translator/api/audio/<id>` — 发音音频（缓存）
- 鉴权：`?token=` 或 `X-Api-Token`（配置于 `/etc/translator/translator.env`）

## 数据来源
- 翻译：Google 免费接口
- 词典（音标/发音/释义/例句）：Free Dictionary API (dictionaryapi.dev)
- TTS：Google 文本转语音（中英例句朗读）
- 影视例句：预留 provider 接口（`movie_examples`），待接入字幕语料

## 部署
```bash
cp deploy/translator.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now translator
# nginx: include deploy/nginx-translator.locations.conf 于 443 server 块
```
