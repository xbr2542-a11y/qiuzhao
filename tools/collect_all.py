"""按扩展后的关键词，把所有来源依次抓一遍并入库。

用后台运行，日志写到 .tmp/collect_all.log，方便边跑边看。
    python tools/collect_all.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import collectors  # noqa: E402
import ingest  # noqa: E402

# (来源, 每个关键词最多抓多少条, 每个关键词最多翻几页)
PLAN = [
    (collectors.guopin.CODE, 40, 2),
    (collectors.ncss.CODE, 40, 2),
    (collectors.bigtech.CODE, 20, 1),
    (collectors.wechat.CODE, 12, 1),
    (collectors.workday.CODE, 12, 1),
]


def run_source(source: str, limit: int, max_pages: int) -> dict:
    module = collectors.REGISTRY[source]
    profile = ingest.get_profile()
    keywords = collectors.keywords_from_profile(profile, source)
    print(f"\n=== {module.NAME}（{len(keywords)} 个关键词）===", flush=True)
    collected: list[dict] = []
    for index, keyword in enumerate(keywords, start=1):
        try:
            if source == collectors.workday.CODE:
                jobs = []
                for company in module.COMPANIES:
                    jobs.extend(module.collect(keyword, limit=limit, company=company))
            else:
                jobs = module.collect(keyword, limit=limit, max_pages=max_pages)
        except Exception as exc:
            print(f"  [{index}/{len(keywords)}] {keyword}: 失败 {exc}", flush=True)
            continue
        if jobs:
            print(f"  [{index}/{len(keywords)}] {keyword}: {len(jobs)} 条", flush=True)
        collected.extend(jobs)
    unique: dict[str, dict] = {}
    for job in collected:
        unique.setdefault(f"{job['company']}|{job['title']}|{job['city']}", job)
    result = ingest.upsert_jobs(list(unique.values()), source) if unique else {"inserted": 0, "updated": 0}
    print(f"  → 去重 {len(unique)} 条，入库 {result}", flush=True)
    return result


def main() -> int:
    started = time.time()
    total_inserted = 0
    for source, limit, max_pages in PLAN:
        try:
            result = run_source(source, limit, max_pages)
            total_inserted += result.get("inserted", 0)
        except Exception as exc:
            print(f"来源 {source} 整体失败：{exc}", flush=True)
    print(f"\n全部完成，用时 {(time.time()-started)/60:.1f} 分钟，新增 {total_inserted} 条", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
