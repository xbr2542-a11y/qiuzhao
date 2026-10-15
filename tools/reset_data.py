"""清空岗位池与本机测试数据（不动已上传的简历文件）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="跳过确认")
    parser.add_argument("--resumes", action="store_true", help="同时清空简历记录（文件保留）")
    args = parser.parse_args()

    db.init_db()
    if not args.yes:
        answer = input("将清空岗位池、投递记录与运行日志，确认继续？(yes/no) ").strip().lower()
        if answer not in {"y", "yes"}:
            print("已取消")
            return 1

    with db.connect() as conn:
        conn.execute("DELETE FROM materials")
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM events")
        if args.resumes:
            conn.execute("DELETE FROM resumes")
        conn.execute("UPDATE profile SET full_name='', school='', phone='', email='' WHERE id = 1")
        conn.commit()
    print("已清空。" + ("（简历记录也已清空，文件仍在 data/resumes 目录）" if args.resumes else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
