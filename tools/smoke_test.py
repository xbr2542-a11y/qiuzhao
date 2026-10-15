"""离线自检：不依赖网络，验证数据库、规则、导入、导出整条链路。"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import db  # noqa: E402
import export  # noqa: E402
import rules  # noqa: E402
import server  # noqa: E402


def main() -> int:
    # 用临时目录做自检，绝不触碰 data/ 里的真实数据
    scratch = ROOT / ".tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    tmp = scratch / f"smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    tmp.mkdir(parents=True, exist_ok=True)
    db.DATA_DIR = tmp
    db.RESUME_DIR = tmp / "resumes"
    db.EXPORT_DIR = tmp / "exports"
    db.DB_PATH = tmp / "smoke.db"
    export.EXPORT_DIR = tmp / "exports"

    db.init_db()
    print("[1] 数据库:", db.DB_PATH)

    profile = server.save_profile({
        "full_name": "测试同学",
        "school": "示例学院",
        "major": "环境设计",
        "degree": "本科",
        "grad_date": "2027-06",
        "political": "共青团员",
        "prefer_cities": ["东营", "济南", "北京", "深圳", "广州", "成都"],
    })
    print("[2] 档案保存:", profile["major"], profile["prefer_cities"])

    sample_rows = [
        {"company": "中国石化胜利油田", "title": "数据分析岗", "city": "东营", "source": "国聘",
         "url": "https://example.com/a", "deadline": "2026-10-08", "major_req": "专业不限",
         "degree_req": "本科", "family": "soe"},
        {"company": "某半导体设备公司", "title": "技术销售工程师", "city": "深圳", "source": "牛客",
         "url": "https://example.com/b", "deadline": "2026-09-25",
         "major_req": "微电子、材料、物理相关专业", "degree_req": "本科", "family": "semi"},
        {"company": "某外贸公司", "title": "外贸业务员", "city": "东营", "source": "应届生",
         "url": "https://example.com/c", "deadline": "2026-10-30", "major_req": "国际贸易、英语相关专业",
         "degree_req": "大专", "family": "trade"},
        {"company": "某互联网公司", "title": "数据运营", "city": "北京", "source": "官网",
         "url": "https://example.com/d", "deadline": "2026-11-15", "major_req": "统计学、计算机相关专业",
         "degree_req": "本科", "family": "data"},
        {"company": "中国石化胜利油田", "title": "数据分析岗", "city": "东营", "source": "国聘",
         "url": "https://example.com/a", "deadline": "2026-10-08", "major_req": "专业不限"},
    ]
    result = server.upsert_jobs(sample_rows)
    print("[3] 导入（含 1 条重复）:", result)
    assert result["inserted"] == 4, result

    jobs = server.fetch_jobs({"sort": "score"})
    print("[4] 岗位池:", len(jobs), "条")
    for job in jobs:
        left = job["days_left"]
        print(f"    {job['match_score']:>3} 分 | 档位 {job['major_tier']} | {job['company']} · {job['title']}"
              f" | {job['city']} | 截止 {job['deadline'] or '—'} | 剩 {left if left is not None else '?'} 天")
        print(f"        判定：{job['filter_note']}")

    # 校验三条关键规则
    assert any(j["major_tier"] == 1 for j in jobs), "应存在“专业不限”档位"
    assert any(j["major_tier"] == 3 for j in jobs), "应存在“明确限定”档位"
    top = jobs[0]
    print("[5] 排序第一:", top["company"], top["title"], top["match_score"])

    pasted = ("公司\t岗位\t城市\t来源\t链接\t截止日期\t专业要求\n"
              "某国企\t综合管理\t济南\t国聘\thttps://example.com/e\t2026年10月10日\t专业不限\n")
    rows = server.parse_pasted_table(pasted)
    assert rows and rows[0]["company"] == "某国企", rows
    server.upsert_jobs(rows, "国聘")
    with db.connect() as conn:
        row = conn.execute(
            "SELECT company, city, deadline, major_tier FROM jobs WHERE company = '某国企'").fetchone()
    assert row and row["deadline"] == "2026-10-10", dict(row) if row else None
    assert row["major_tier"] == 1, dict(row)
    print("[6] 粘贴解析并入库:", dict(row), "（中文日期已归一化）")

    target = export.export_workbook(server.fetch_jobs({}), server.get_profile())
    print("[7] 导出 Excel:", target, f"{target.stat().st_size} 字节")

    print("\n全部自检通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
