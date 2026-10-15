"""只抓「采购 / 供应链」这一条线的岗位，补充到岗位池。

为什么单独做一个脚本：collect_all 会把所有关键词跑一遍（几千次请求），
只想补一类岗位时没必要。这里只发 PROCUREMENT_SUPPLY 里的检索词。

用法：
    python tools/collect_procurement_supply.py --limit 20
    python tools/collect_procurement_supply.py --source guopin
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import collectors  # noqa: E402
import ingest  # noqa: E402

DEFAULT_SOURCES = [collectors.guopin.CODE, collectors.ncss.CODE, collectors.wechat.CODE]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="append", default=[],
                        help="只跑指定来源，可重复；默认国聘 + 24365 + 公众号")
    parser.add_argument("--limit", type=int, default=20, help="每个关键词最多取多少条")
    parser.add_argument("--max-pages", type=int, default=2)
    args = parser.parse_args()

    sources = args.source or DEFAULT_SOURCES
    keywords = list(collectors.PROCUREMENT_SUPPLY)
    print(f"检索词 {len(keywords)} 个：{'、'.join(keywords)}", flush=True)
    started = time.time()
    grand_total = 0

    for source in sources:
        module = collectors.REGISTRY[source]
        print(f"\n=== {module.NAME} ===", flush=True)
        collected: list[dict] = []
        for index, keyword in enumerate(keywords, start=1):
            try:
                if source == collectors.wechat.CODE:
                    jobs = module.collect(keyword, limit=args.limit)
                else:
                    jobs = module.collect(keyword, limit=args.limit, max_pages=args.max_pages)
            except Exception as exc:
                print(f"  [{index}/{len(keywords)}] {keyword}: 失败 {exc}", flush=True)
                continue
            if jobs:
                print(f"  [{index}/{len(keywords)}] {keyword}: {len(jobs)} 条", flush=True)
            collected.extend(jobs)
        unique: dict[str, dict] = {}
        for job in collected:
            unique.setdefault(f"{job['company']}|{job['title']}|{job['city']}", job)
        result = (ingest.upsert_jobs(list(unique.values()), source) if unique
                  else {"inserted": 0, "updated": 0})
        grand_total += result.get("inserted", 0)
        print(f"  → 去重 {len(unique)} 条，入库 {result}", flush=True)

    print(f"\n完成，用时 {(time.time() - started) / 60:.1f} 分钟，新增 {grand_total} 条", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
