**[中文](README.md) | [English](README_EN.md)**

> ## ⚠️ 法律声明 / Legal Disclaimer
>
> **本工具仅用于解密你「自己合法下载、有权使用」的音频文件。** 请勿将其用于任何侵权或未经授权的用途；下载或复制受版权保护的音乐可能违反当地法律。使用前请确认你对相应文件拥有使用权，作者不对任何滥用行为负责。
>
> **This tool is intended *solely* for decrypting audio files that you have legally downloaded and have the right to use.** Do not use it for infringing or unauthorized purposes. Downloading or copying copyright-protected music may violate applicable laws. Verify your rights to each file before use. The author is not responsible for any misuse.

---

# QQMusicDecryptor — QQ音乐 QMC 加密文件解密工具

纯 Python 实现，**零第三方依赖**（仅标准库），Windows / macOS / Linux 直接可跑。

> 💡 **配套 Claude Code 技能**（自动解密流程 / adb 拉库 / 提醒清单，内置 adb 工具）：
> **[github.com/mzj3920/qqmusic-decrypt-skill](https://github.com/mzj3920/qqmusic-decrypt-skill)**

## 图形界面版（推荐）

**免安装 exe（已打包）：双击 `QQMusicDecryptor.exe` 即可，无需安装 Python。**

```bash
# 或用源码运行：
python qmc_decrypt_gui.py     # Windows 也可双击 run_gui.bat
```

功能：**拖拽歌曲/文件夹**进列表、选择密钥库 `player_process_db`、实时进度条（总进度 + 当前文件）、详细日志、取消。
* 拖拽支持需要 `pip install tkinterdnd2`（可选）；不装也能用"添加文件/添加文件夹"按钮。
* 解密 PC 版 `.mflac` 等无内嵌密钥文件时，在"密钥库"栏选择 `player_process_db` 即可自动取密钥。
* 输出冲突会自动加序号（如 `song (1).flac`），不会覆盖已有文件。

### 目录结构（新增）

```
├── qmc_decrypt_gui.py   # 图形界面版
├── run_gui.bat          # Windows 双击启动
└── requirements.txt     # 可选拖拽依赖 tkinterdnd2
```

> 配套 Claude Code 技能 `qqmusic-decrypt`（独立仓库：[qqmusic-decrypt-skill](https://github.com/mzj3920/qqmusic-decrypt-skill)）。
> 以后说"解密/解析某首歌"，Claude 会自动调用该方法并提醒你要准备什么
> （提供文件 / 最新的 player_process_db / 手机播放一次后重新导出数据库）。

---

## 支持的格式

| 类别 | 扩展名 | 说明 |
| --- | --- | --- |
| **v2（内嵌密钥）** | `.mflac` `.mflac0` `.mgg` `.mgg0` `.mgg1` `.mggl` `.mmp4` `.qmcflac` `.qmcogg` `.qmc0` `.qmc2` `.qmc3` `.qmc4` `.qmc6` `.qmc8` | 现代格式，密钥内嵌于文件尾部（PC 经典 `PcV1Legacy` / Android `QTag`），**可离线解密**。含"QQMusic EncV2,Key:"双层 TEA 与单层 V1 两种 EKey，Map（≤300 字节密钥）与 RC4（>300 字节密钥）两种流密码 |
| **v1（静态密钥）** | `.tkm` `.bkcmp3` `.bkcm4a` `.bkcflac` `.bkcwav` `.bkcape` `.bkcogg` `.bkcwma` 及 `666c6163`/`6d7033`/`6f6767`/`6d3461`/`776176` 等十六进制扩展名 | 整文件与 128 字节静态密钥异或，可离线解密 |
| **无内嵌密钥** | Android `STag`、PC `MusicEx`（`.mflac0`/`.mgg0` 等） | 密钥存在服务器/客户端本地，可用 `--ekey` 或 `--ekey-db` 提供（见下） |

> **关于 PC `MusicEx` 文件（如 `.mflac` 报"未内嵌密钥"）**：QQ 音乐 PC 客户端 **19.57 及更高版本**下载的文件不再把密钥写进文件，
> 密钥获取方式官方未公开（unlock-music 项目亦明确"不支持"）。可行办法：
> * **降级** QQ 音乐 PC 到 19.51 或更低，重新下载 → 文件无需密钥即可解密；
> * 若手机（安卓）上也缓存了同曲目，把客户端密钥库 `player_process_db`
>   （`/data/data/com.tencent.qqmusic/databases/player_process_db`，需 root/ADB 导出）
>   用 `--ekey-db` 传入，工具会自动取密钥。**密钥库中两张表都会被读取**：
>   * `audio_file_ekey_table` / `EKeyFileInfo` —— 安卓端下载文件（按文件名匹配）
>   * `p2p_cache_info_table` —— PC 媒体文件名（`AIM0000<mid>.mflac`）的缓存密钥，
>     按 media_mid 子串即可匹配到 PC 版 `.mflac`/`.mflac0` 文件
>
> 注意：约 2017 年前的远古 `.qmc0/.qmc3` 格式（旧 0x77 静态密钥 + 头部偏移）已不被官方解锁项目支持，本工具同样不支持。

---

## 获取密钥库 player_process_db 的注意事项（实测经验）

> 无内嵌密钥的文件需要密钥库。密钥按歌曲 MID 绑定；只要手机（同账号）播放过目标歌曲，
> 密钥就会缓存在 `player_process_db` 里（路径 `/data/data/com.tencent.qqmusic/databases/`）。

### ✅ root 手机（推荐，最方便）
1. 手机开 USB 调试连电脑，`adb devices` 确认显示 `device`（非 `unauthorized`/空）。
2. 手机 QQ 音乐（**同一账号**）播放目标歌曲几秒 → **强制停止** App（确保数据库写入）。
3. 导出并拉取（先 `su` 复制到 /sdcard 再 pull，避免二进制经管道被 CRLF 破坏）：
   ```bash
   adb shell "su -c 'cp /data/data/com.tencent.qqmusic/databases/player_process_db /sdcard/player_process_db'"
   adb pull /sdcard/player_process_db player_process_db
   adb shell rm /sdcard/player_process_db
   ```
   > Windows Git Bash 下 `/sdcard/...` 会被 MSYS 改写，命令前加 `MSYS_NO_PATHCONV=1`。
4. 用 `--ekey-db player_process_db <歌曲文件>` 解密。

### ❌ 无 root 手机（实测会失败，别浪费时间）
- `adb backup` 在现代系统（Android 12+ / 小米 HyperOS 等）只产出**约 47 字节的空备份**，拉不到任何数据；
- `adb shell run-as com.tencent.qqmusic` 报 **`package not debuggable`**（App 非可调试）；
- 直接访问 `/data/data/com.tencent.qqmusic/` 报 **`Permission denied`**（应用沙箱）。

**对策**：
- 用 **root 模拟器**（雷电 / MuMu 自带 root）：装 QQ 音乐 → 同账号播放一次 → `adb root` 后直接 `adb pull`，无需 su；
- 旧系统可试 `adb backup` + abe.jar 解包（详见配套技能仓库）。

### 💡 多来源密钥库合并（避免丢 key）
不同手机/模拟器的库可能各有对方没有的 key。以最新库为基础，用 sqlite3 把其他库的
`p2p_cache_info_table(file_id, ekey)` 与 `audio_file_ekey_table(file_path, ekey)` **INSERT OR IGNORE**
合并成超集后覆盖为默认库（旧库改名备份）。

---

## Frida 借壳解密模式（可选，Windows）

> 思路来源见文末「致谢 / Credits」。

不想碰手机/root？可以让 **QQ 音乐客户端亲手替你解密**：用 Frida 注入正在运行的 `QQMusic.exe`，
调用它进程内的解密引擎（`QQMusicCommon.dll` 的 `EncAndDesMediaFile` 类），
直接把 `.mflac` / `.mgg` 的明文读出来——**不需要密钥，也就绕开了"PC 端密钥不进磁盘"的限制**。

```bash
pip install frida            # 可选依赖，仅此模式需要
# 方式一：已集成进主程序（推荐）——无密钥时自动兜底：
python qmc_decrypt.py --frida-fallback -i "D:\Music"
# 方式二：独立批量工具：
python frida_mode\decrypt_frida.py -i "D:\Music" -o "D:\out"
```

**前提**：Windows 10/11；先启动 QQ 音乐客户端并登录（无需播放歌曲）；被杀软误报属 Frida 注入的正常现象。
**缺点**：客户端更新加密算法后 hook 会失效（脚本会明确报缺方法，等待适配）；批量逐文件 RPC，比离线解密慢。

---

## 使用方法

### 环境要求
* Python 3.8+（自带即可）
* 可选：Node.js（仅用于运行对拍测试，解密本身不需要）

### 解密单个文件

```bash
python qmc_decrypt.py "D:\Music\song.mflac"
```

输出到输入文件同目录下的 `decrypted\` 子文件夹，扩展名按实际音频内容自动识别
（如 `.mflac` → `.flac`、`.mgg` → `.ogg` 等）。

### 解密整个目录

```bash
python qmc_decrypt.py "D:\Music"
```

递归扫描目录下所有 QMC 扩展名的文件，批量解密。

### 指定输出目录 / 覆盖已有文件

```bash
python qmc_decrypt.py "song.mflac" -o "D:\out" -f
```

| 参数 | 说明 |
| --- | --- |
| `-o, --output-dir` | 输出目录（默认：输入同目录下的 `decrypted\`） |
| `-e, --ekey <key>` | 手动指定 EKey 字符串（用于无内嵌密钥的文件） |
| `--ekey-db <DB>` | QQ 音乐安卓端密钥库 `player_process_db`（SQLite），按文件名自动查找 EKey |
| `--list-ekey-db <DB>` | 列出密钥库中的 文件名→EKey 条目（验证库/查密钥），可配合 `--find <名字>` 过滤 |
| `--frida-fallback` | 无密钥时用 Frida 借壳解密兜底（Windows 需 QQ 音乐客户端运行中，无需密钥库；思路来源见文末致谢） |
| `-f, --force` | 覆盖已存在的输出文件 |
| `-v, --verbose` | 输出详细日志 |
| `--self-test` | 运行内置自检后退出 |

---

## 验证工具本身是否可靠

算法以 unlock-music 官方 Rust 实现（[`lib_um_crypto_rust`](https://git.um-react.app/um/lib_um_crypto_rust)）为参照
**逐一对拍移植**，附三层验证：

```bash
# 1) 内置自检：内建 Rust 单元测试向量（v1 变换/边界、key_compress、RC4、TEA、嗅探…）
python qmc_decrypt.py --self-test

# 2) WASM 行为对拍：与官方编译产物（@clamber_l/crypto 0.1.12）逐字节比对
python tests/check_wasm.py

# 3) 端到端：用 WASM 反向"加密"构造合成文件，跑通完整 CLI
python tests/e2e_cli.py

# 或一次跑完（需要 Node.js）
python tests/run_all.py
```

对拍覆盖：v1 静态密钥（含 `0x7FFF` 边界）、v2 Map/RC4、双层 EKey、四种 footer
（PCv1 / PCv2 MusicEx / Android QTag / Android STag）、音频类型嗅探。

### 目录结构

```
QQMusicDecryptor/
├── qmc_decrypt.py          # 主程序（单文件，零依赖）
├── frida_mode/             # 可选：Frida 借壳解密（Windows + QQ音乐客户端运行）
│   ├── decrypt_frida.py    #   Python 驱动（需 pip install frida）
│   └── hook_qq_music.js    #   注入脚本（思路来自 decrypt-mflac-frida，见致谢）
├── README.md               # 本文档（中文）
├── README_EN.md            # English README
├── tests/
│   ├── run_all.py        # 一键跑全部测试
│   ├── check_wasm.py     # WASM 行为对拍
│   ├── e2e_cli.py        # 端到端 CLI 集成测试
│   └── fixtures/         # 官方 footer 测试夹具（.bin）
└── reference/            # 官方 WASM 参考实现（仅测试用，可删）
    ├── um_wasm_bg.wasm   # @clamber_l/crypto 0.1.12
    ├── loader.js / .d.ts / loader.mjs / loader-inline.js
    ├── golden.js         # 黄金数据生成器
    └── encrypt.js        # 加密辅助（XOR 自反）
```

---

## 算法要点（实现参考）

* **v1 静态密钥**：128 字节固定密钥，整文件 `data[i] ^= key[offset % 128]`；
  注意边界语义 `offset > 0x7FFF` 时先对 `0x7FFF` 取模。
* **EKey 解密**：
  * V1：`base64` 解码 → 前 8 字节为 header，与 8 字节 `simple_key` 交错成 16 字节
    TEA 密钥 → 解密剩余密文 → 主密钥 = header + 明文。
  * V2（`QQMusic EncV2,Key:` 前缀）：两次 TEA 解密（固定 KEY1/KEY2）去零后再走 V1。
* **Tencent TEA**：标准 TEA 的 16 轮变体 + tweaked CBC（盐 2 字节、尾部 7 字节校验）。
* **流密码**：主密钥 1~300 字节 → Map（压缩成 128 字节密钥后按 v1 变换异或）；
  >300 字节 → Modified RC4（状态长度 = 密钥长度），分段密钥由 `hash` 派生。

详见源码内对应函数与 `--self-test` 内建向量。

---

## 致谢 / Credits

**Frida 借壳解密模式**（`frida_mode/`）的机制与思路来源于以下开源项目，向原作者致敬：

- **[yllhwa/decrypt-mflac-frida](https://github.com/yllhwa/decrypt-mflac-frida)** —— Frida 注入解密的原始版
- **[ericjuice/music-decryptor](https://github.com/ericjuice/music-decryptor)** —— 简化实现（TuneFree 等桌面工具亦基于它）

本仓库的 `hook_qq_music.js` 为**独立重写**（导出符号模糊解析，对客户端版本更鲁棒），解密机制与上述项目同源：
调用 QQ 音乐客户端进程内 `EncAndDesMediaFile` 解密引擎，让客户端自行取钥并解密。
另感谢 [Frida](https://frida.re/) 动态插桩框架、[unlock-music](https://github.com/unlock-music) 社区的算法研究。

---

## 关键词 / Keywords

QQ音乐 解密、QQ Music decrypt、QMC、mflac、mflac0、mgg、qmcflac、qmcogg、tkm、player_process_db、密钥库、EKey、unlock music、解锁歌曲、flac、ogg、Claude Code 技能
