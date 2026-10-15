"""测试公共工具：把数据层指向独立的临时库，绝不触碰 data/ 里的真实数据。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import db  # noqa: E402

_counter = 0


def scratch_dir(name: str = "test") -> Path:
    """创建独立临时目录并让 db 指向它（不初始化）。"""
    global _counter
    _counter += 1
    stamp = time.strftime("%Y%m%d_%H%M%S")
    scratch = ROOT / ".tmp" / f"{name}_{stamp}_{_counter}"
    scratch.mkdir(parents=True, exist_ok=True)
    db.DATA_DIR = scratch
    db.RESUME_DIR = scratch / "resumes"
    db.EXPORT_DIR = scratch / "exports"
    db.DB_PATH = scratch / "test.db"
    # 防线：测试永远不许碰真实库。历史上就是这条线被绕过，
    # 导致一条伪造的"收到面试邀请"记录出现在用户的投递记录里。
    if db.points_at_real_db():
        raise AssertionError(
            f"测试数据库指向了真实库：{db.DB_PATH}。测试必须使用 .tmp/ 下的临时库。")
    return scratch


def fresh_db(name: str = "test") -> Path:
    """创建独立临时库并执行初始化。"""
    scratch = scratch_dir(name)
    db.init_db()
    return scratch


def make_db_file(scratch: Path):
    """在指定目录建库文件并连上去（供构造"老库"用）。"""
    import sqlite3
    scratch.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(scratch / "test.db")


def wait_for(predicate, timeout: float = 25.0, interval: float = 0.1, message: str = "等待超时"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(message)


def columns(table: str) -> set[str]:
    with db.connect() as conn:
        return db.table_columns(conn, table)


def tables() -> set[str]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def count(sql: str, params: tuple = ()) -> int:
    with db.connect() as conn:
        return conn.execute(sql, params).fetchone()[0]
