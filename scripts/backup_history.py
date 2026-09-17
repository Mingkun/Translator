#!/usr/bin/env python3
"""每日把查询历史与翻译缓存推送到私有仓库 Mingkun/TranslaterData。"""
from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

MAIN_DB = Path("/work/Translator/data/cache.db")
BACKUP_DIR = Path("/work/TranslaterData")
DATA_DIR = BACKUP_DIR / "data"


def export_history() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=30)
    try:
        rows = conn.execute(
            "SELECT q, created_at, initial FROM history ORDER BY created_at DESC"
        ).fetchall()
    finally:
        conn.close()
    payload = {
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(rows),
        "records": [
            {"q": r[0], "created_at": r[1], "initial": r[2]} for r in rows
        ],
    }
    (DATA_DIR / "history.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return len(rows)


def export_cache_snapshot() -> bool:
    tmp = DATA_DIR / "cache.db.tmp"
    gz = DATA_DIR / "cache.db.gz"
    shutil.copyfile(MAIN_DB, tmp)
    with open(tmp, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)
    tmp.unlink(missing_ok=True)
    return gz.is_file() and gz.stat().st_size > 0


def git_push() -> bool:
    stamp = time.strftime("%Y-%m-%d %H:%M")
    cmds = [
        ["git", "add", "-A"],
        ["git", "commit", "-m", f"daily backup {stamp}"],
        ["git", "push", "origin", "main"],
    ]
    for cmd in cmds:
        proc = subprocess.run(cmd, cwd=BACKUP_DIR, capture_output=True, text=True)
        if proc.returncode != 0 and "nothing to commit" not in (proc.stdout + proc.stderr):
            print("git失败:", cmd, (proc.stderr or proc.stdout)[:200], file=sys.stderr)
            return False
    return True


def main() -> int:
    try:
        count = export_history()
        snapshot_ok = export_cache_snapshot()
        pushed = git_push()
        print(f"导出历史 {count} 条; 快照 {'OK' if snapshot_ok else '失败'}; 推送 {'OK' if pushed else '失败'}")
        return 0 if (snapshot_ok and pushed) else 1
    except Exception as exc:  # noqa: BLE001
        print("备份异常:", exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
