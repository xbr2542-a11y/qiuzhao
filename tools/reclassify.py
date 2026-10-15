"""对岗位池里的岗位重新执行识别（赛道 / 销售类型 / 外派 / 应届生分级 / 排除规则）。

用法：
    python tools/reclassify.py            # 处理所有岗位
    python tools/reclassify.py --only-new # 只处理还没识别过的
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import classify  # noqa: E402
import db  # noqa: E402


def load_tracks() -> list[dict]:
    import tracks as tracks_mod

    return tracks_mod.TRACKS


def load_rules() -> list[dict]:
    with db.connect() as conn:
        rows = db.rows_to_dicts(conn.execute("SELECT * FROM exclusion_rules").fetchall())
    for row in rows:
        row["patterns"] = json.loads(row.get("patterns") or "[]")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only-new", action="store_true", help="只处理 review_status 为空的岗位")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    db.init_db()
    tracks = load_tracks()
    rules = load_rules()

    sql = "SELECT * FROM jobs"
    if args.only_new:
        sql += " WHERE review_status IS NULL OR review_status = ''"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"

    with db.connect() as conn:
        rows = db.rows_to_dicts(conn.execute(sql).fetchall())
    print(f"待识别岗位：{len(rows)} 条", flush=True)

    stats = Counter()
    with db.connect() as conn:
        for index, job in enumerate(rows, start=1):
            result = classify.classify_job(job, tracks, rules)
            conn.execute(
                """UPDATE jobs SET industry_track=?, industry_subtrack=?, sales_type=?,
                   job_function=?, candidate_level=?, overseas_assignment_status=?,
                   overseas_assignment_evidence=?, review_status=?, exclusion_reason=?,
                   match_tags_json=?, risk_tags_json=?, language_requirement=?,
                   classify_evidence=?, data_fetched_at=?
                   WHERE id=?""",
                (result["industry_track"], result["industry_subtrack"], result["sales_type"],
                 result["job_function"], result["candidate_level"],
                 result["overseas_assignment_status"], result["overseas_assignment_evidence"],
                 result["review_status"], result["exclusion_reason"], result["match_tags_json"],
                 result["risk_tags_json"], result["language_requirement"],
                 result["classify_evidence"], db.now(), job["id"]),
            )
            stats[result["review_status"]] += 1
            stats[f"overseas:{result['overseas_assignment_status']}"] += 1
            if result["industry_track"]:
                stats[f"track:{result['industry_track']}"] += 1
            if result["job_function"]:
                stats[f"function:{result['job_function']}"] += 1
            if index % 1000 == 0:
                conn.commit()
                print(f"  已处理 {index}/{len(rows)}", flush=True)
        conn.commit()

    print()
    print("=== 复核状态 ===")
    for key in ["pending", "need_review", "excluded"]:
        print(f"  {key}: {stats[key]}")
    print("=== 外派识别 ===")
    for key, value in sorted(stats.items()):
        if key.startswith("overseas:"):
            print(f"  {key.split(':')[1]}: {value}")
    print("=== 命中赛道 ===")
    for key, value in sorted(stats.items(), key=lambda kv: -kv[1]):
        if key.startswith("track:"):
            print(f"  {key.split(':')[1]}: {value}")
    print("=== 岗位职能 ===")
    for key, value in sorted(stats.items(), key=lambda kv: -kv[1]):
        if key.startswith("function:"):
            print(f"  {key.split(':')[1]}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
