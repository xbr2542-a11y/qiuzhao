"""真实采集岗位并入库。

用法：
    python tools/collect.py --source guopin --keyword 数据分析 --city 济南 --limit 40
    python tools/collect.py --source guopin --keyword 数据分析 --keyword 半导体 --city 东营 --city 济南 --limit 60 --ingest
    python tools/collect.py --source guopin --use-profile --limit 80 --ingest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import collectors  # noqa: E402
import ingest  # noqa: E402
import server  # noqa: E402  （只用于读取档案）


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="guopin")
    parser.add_argument("--company", default="", help="Workday 类来源的公司代号，如 micron / nxp，留空表示全部")
    parser.add_argument("--keyword", action="append", default=[])
    parser.add_argument("--city", action="append", default=[])
    parser.add_argument("--limit", type=int, default=40, help="每个关键词最多抓多少条")
    parser.add_argument("--use-profile", action="store_true", help="用档案里的求职方向当关键词")
    parser.add_argument("--ingest", action="store_true", help="写入岗位池")
    parser.add_argument("--reset", action="store_true", help="入库前清空岗位池（只清岗位，不动简历与档案）")
    parser.add_argument("--json", action="store_true", help="把结果以 JSON 打印")
    args = parser.parse_args()

    module = collectors.REGISTRY.get(args.source)
    if module is None:
        print(f"[错误] 未知来源 {args.source}，可用：{list(collectors.REGISTRY)}")
        return 1

    profile = server.get_profile()
    keywords = list(args.keyword)
    if args.use_profile:
        keywords = list(dict.fromkeys(keywords + collectors.keywords_from_profile(profile, args.source)))
    if not keywords:
        keywords = collectors.keywords_from_profile(profile, args.source)
    # 默认全国抓取（先把池子做大）；需要限定城市时显式传 --city
    cities = tuple(args.city)

    print(f"来源：{module.NAME}")
    print(f"关键词：{keywords}")
    print(f"城市过滤：{cities or '不过滤'}")
    print("-" * 60)

    all_jobs: list[dict] = []
    sites = [args.company] if args.company else (list(module.COMPANIES) if hasattr(module, "COMPANIES") else [""])
    for keyword in keywords:
        for site in sites:
            try:
                if site:
                    jobs = module.collect(keyword, cities=cities, limit=args.limit, company=site)
                else:
                    jobs = module.collect(keyword, cities=cities, limit=args.limit)
            except Exception as exc:
                print(f"  [{keyword}{'/' + site if site else ''}] 采集失败：{exc}")
                continue
            label = f"{keyword}/{site}" if site else keyword
            print(f"  [{label}] 命中 {len(jobs)} 条")
            for job in jobs[:5]:
                print(f"      · {job['company']} | {job['title']} | {job['city']}"
                      f" | {job['job_type']} | {job['degree_req'] or '学历未标注'}")
            if len(jobs) > 5:
                print(f"      …… 其余 {len(jobs) - 5} 条")
            all_jobs.extend(jobs)

    # 同一批内先去重
    unique: dict[str, dict] = {}
    for job in all_jobs:
        key = f"{job['company']}|{job['title']}|{job['city']}"
        unique.setdefault(key, job)
    print("-" * 60)
    print(f"合计去重后 {len(unique)} 条")

    if args.json:
        print(json.dumps(list(unique.values()), ensure_ascii=False, indent=2)[:3000])

    if args.ingest and unique:
        if args.reset:
            # 只清本采集器写入的来源，必须精确匹配（曾用 LIKE 误删过别的来源）
            labels = list(getattr(module, "SOURCE_LABELS", []) or [])
            if not labels:
                print(f"[错误] {args.source} 未声明 SOURCE_LABELS，拒绝清空")
                return 1
            removed = ingest.clear_source(labels)
            print(f"已清空本来源原有岗位 {removed} 条（其他来源不受影响）")
        # 用 ingest 里的完整实现入库（server 里那份是旧版本，会丢字段）
        result = ingest.upsert_jobs(list(unique.values()), args.source)
        print(f"入库：新增 {result['inserted']}，更新 {result['updated']}，跳过 {result['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
