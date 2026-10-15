"""按行业赛道抓取岗位：产品词检索 + 种子公司检索 + 关键词组合。

用法：
    python tools/collect_tracks.py --tier 1          # 只抓第一梯队
    python tools/collect_tracks.py --track mro       # 只抓某个赛道
    python tools/collect_tracks.py                   # 全部赛道
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
import tracks as tracks_mod  # noqa: E402

# 用哪些来源去搜赛道（都是公开接口，无需登录）
SEARCH_SOURCES = [collectors.guopin.CODE, collectors.ncss.CODE]


def search_keywords(track: dict) -> list[str]:
    """产品词 + 种子公司名 + 少量组合词。产品词命中率最高。"""
    words = list(track["products"])
    words.extend(track["seed_companies"])
    # 每个赛道加 3 组"产品 + 招聘方向"的组合，覆盖公告类岗位
    for product in track["products"][:1]:
        words.extend([f"{product} 销售", f"{product} 海外", f"{product} 招聘"])
    return list(dict.fromkeys(w for w in words if w))


def run_track(track: dict, per_keyword: int, max_pages: int) -> int:
    print(f"\n===== {track['name']}（第 {track['tier']} 梯队）=====", flush=True)
    keywords = search_keywords(track)
    print(f"  检索词 {len(keywords)} 个：{'、'.join(keywords[:8])}…", flush=True)
    total_inserted = 0
    for source in SEARCH_SOURCES:
        module = collectors.REGISTRY[source]
        collected: list[dict] = []
        for keyword in keywords:
            try:
                jobs = module.collect(keyword, limit=per_keyword, max_pages=max_pages)
            except Exception as exc:
                print(f"    [{source}/{keyword}] 失败：{exc}", flush=True)
                continue
            if jobs:
                print(f"    [{source}/{keyword}] {len(jobs)} 条", flush=True)
            collected.extend(jobs)
        unique: dict[str, dict] = {}
        for job in collected:
            unique.setdefault(f"{job['company']}|{job['title']}|{job['city']}", job)
        if unique:
            result = ingest.upsert_jobs(list(unique.values()), source)
            total_inserted += result.get("inserted", 0)
            print(f"    → {source} 入库 {result}", flush=True)
    return total_inserted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", type=int, default=0, help="只抓某个梯队（1/2/3），0 表示全部")
    parser.add_argument("--track", default="", help="只抓某个赛道代码")
    parser.add_argument("--per-keyword", type=int, default=20)
    parser.add_argument("--max-pages", type=int, default=1)
    args = parser.parse_args()

    db_ready = ingest.get_profile()  # 触发初始化
    targets = tracks_mod.TRACKS
    if args.track:
        targets = [t for t in targets if t["code"] == args.track]
    elif args.tier:
        targets = [t for t in targets if t["tier"] == args.tier]

    started = time.time()
    total = 0
    for track in targets:
        total += run_track(track, args.per_keyword, args.max_pages)
    print(f"\n全部完成：新增 {total} 条，用时 {(time.time()-started)/60:.1f} 分钟", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
