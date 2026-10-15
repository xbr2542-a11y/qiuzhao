"""清理历史验证脚本污染真实库留下的记录（一次性工具）。

背景：2026-09-28 的一次人工验证脚本直接连了真实库 `data/qiuzhao.db`，
写入了一条并不存在的投递记录（美团·产品运营实习生），并伪造了
"平台返回申请编号 APP-20260928-001 / 收到面试邀请邮件" 两条依据。
这条记录会让用户在「投递记录」页看到凭空出现的"面试邀约"。

本工具的做法：
1. 先用 SQLite 在线备份 API 备份整库（WAL 安全），备份放 data/backups/。
2. 只删除「依据是占位符」的那条记录，绝不按公司名/来源名批量删。
3. 把被误标为「已投递」的岗位状态改回「待投」。

用法：
    python tools/cleanup_test_pollution.py --dry-run   # 只看会删什么
    python tools/cleanup_test_pollution.py --apply     # 真正执行（先自动备份）
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import db  # noqa: E402

# 测试脚本留下的"证据"字符串——只认这些占位符，避免误删用户真实记录
FAKE_EVIDENCE = [
    "APP-20260928-001",
    "收到面试邀请邮件 2026-10-08",
]


def find_polluted_rows(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows: list[dict] = []
    for app in conn.execute("SELECT * FROM applications").fetchall():
        evidence = conn.execute(
            "SELECT evidence FROM application_events WHERE application_id = ?",
            (app["id"],)).fetchall()
        hits = [e["evidence"] for e in evidence if e["evidence"] in FAKE_EVIDENCE]
        if hits:
            rows.append({"application": dict(app), "fake_evidence": hits})
    return rows


def backup() -> Path:
    backups = ROOT / "data" / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backups / f"qiuzhao_before_cleanup_{stamp}.db"
    source = sqlite3.connect(db.DB_PATH)
    try:
        with sqlite3.connect(dest) as out:
            source.backup(out)
    finally:
        source.close()
    return dest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真正执行删除")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不修改（默认）")
    args = parser.parse_args()

    conn = sqlite3.connect(db.DB_PATH)
    try:
        rows = find_polluted_rows(conn)
        if not rows:
            print("没有发现测试污染记录，数据库是干净的。")
            return 0
        print(f"发现 {len(rows)} 条测试污染记录：")
        for item in rows:
            app = item["application"]
            print(f"  application#{app['id']} job_id={app['job_id']} "
                  f"阶段={app['application_status']} 依据={item['fake_evidence']}")

        if not args.apply:
            print("\n这是 dry-run，没有修改任何数据。确认无误后加 --apply 执行。")
            return 0

        dest = backup()
        print(f"\n已备份到：{dest}")

        conn.row_factory = sqlite3.Row
        for item in rows:
            app_id = item["application"]["id"]
            job_id = item["application"]["job_id"]
            conn.execute("DELETE FROM application_events WHERE application_id = ?", (app_id,))
            conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
            conn.execute(
                "UPDATE jobs SET status = '待投' WHERE id = ? AND status IN ('已投递', '已投')",
                (job_id,))
            conn.execute("DELETE FROM events WHERE kind = 'application' AND payload LIKE ?",
                         (f'%"job_id": {job_id}%',))
            print(f"  已删除 application#{app_id}（含时间线），岗位 #{job_id} 状态改回「待投」")
        conn.commit()
        print("\n清理完成。")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
