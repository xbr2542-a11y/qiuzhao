"""导出浏览器演示版需要的静态数据。

产物是 demo/data.json：一批真实岗位的抽样快照 + 几份虚构的示例简历。
演示页在前端重算筛选和匹配分，所以这里只导出"原料"，不导出结果。

为什么要有这个演示版：秋招管家本体需要 Python 后端 + 本机数据库 + 联网采集，
没法直接挂到 GitHub Pages 上；但"筛选 + 匹配打分"这一层本来就是纯前端能算的，
把它抽出来配上脱敏样本，就能让别人用手机打开一个真正点得动的页面。

用法：
    python tools/export_demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import db  # noqa: E402

OUT = ROOT / "demo" / "data.json"
JD_LIMIT = 420          # 岗位描述只保留前 N 个字符，控制体积

# 每个方向抽多少条，保证演示里每类筛选都有东西可看。
# 条件写全在每一行里，不再用一个全局 WHERE —— 因为"数据分析岗"会被
# 「非销售岗位」规则整体标成 excluded，套上全局过滤就会一条都抽不到，
# 演示里那一个筛选项就会是空的。
PLAN = [
    ("采购 / 寻源", "job_function = 'procurement' AND review_status != 'excluded'", 90),
    ("供应链 / 物流", "job_function = 'supply_chain' AND review_status != 'excluded'", 90),
    ("外贸 / 国际业务", "job_function = 'trade' AND review_status != 'excluded'", 90),
    ("B端销售", "job_function = 'sales' AND review_status != 'excluded'", 90),
    # 数据岗照收（含被规则标为 excluded 的），这样演示里能看到复核状态的差异
    ("数据分析", "family = 'data'", 45),
    ("央国企（其他职能）", "family = 'soe' AND job_function = 'other' "
                          "AND review_status != 'excluded'", 70),
    ("其他", "job_function = 'other' AND review_status != 'excluded'", 60),
]

# 公众号采集回来的"岗位"其实是推文标题（「校招 | 【某集团】招聘…五险一金!…」），
# 放进演示里像广告。标题过长的直接不要。
MAX_TITLE_LEN = 40

JOB_COLUMNS = """
    id, company, title, city, source, family, url, deadline, major_req, major_tier,
    filter_note, degree_req, salary, salary_min, salary_max, match_score, job_type,
    industry, industry_track, industry_subtrack, sales_type, job_function,
    candidate_level, overseas_assignment_status, overseas_assignment_evidence,
    review_status, match_tags_json, risk_tags_json, language_requirement,
    experience_req, published_at, jd_text
"""


def sample_jobs(conn) -> list[dict]:
    seen: set[int] = set()
    picked: list[dict] = []
    for _label, condition, limit in PLAN:
        rows = conn.execute(
            f"SELECT {JOB_COLUMNS} FROM jobs "
            f"WHERE ({condition}) AND length(title) BETWEEN 2 AND {MAX_TITLE_LEN} "
            f"ORDER BY match_score DESC, id LIMIT ?", (limit,)).fetchall()
        for row in rows:
            job = dict(row)
            if job["id"] in seen:
                continue
            seen.add(job["id"])
            picked.append(job)
    return picked


def shape(job: dict) -> dict:
    """只保留演示需要的字段，并去掉体积大的部分。"""
    for key in ("match_tags_json", "risk_tags_json"):
        raw = job.pop(key, "[]") or "[]"
        try:
            job[key.replace("_json", "")] = json.loads(raw)
        except json.JSONDecodeError:
            job[key.replace("_json", "")] = []
    jd = job.get("jd_text") or ""
    job["jd_text"] = jd[:JD_LIMIT]
    job["jd_truncated"] = len(jd) > JD_LIMIT
    return job


# 示例简历：全部为虚构，用于演示"同一批岗位、不同简历 → 分数不同"
DEMO_RESUMES = [
    {
        "id": 1,
        "name": "示例·通用版",
        "note": "跨专业求职，方向较宽",
        "education": "本科",
        "skills": ["Excel", "Word", "PowerPoint", "Photoshop", "CAD", "SketchUp"],
        "languages": ["英语"],
        "target_industries": [],
        "target_job_types": ["B端解决方案销售", "海外销售"],
        "target_cities": ["东营", "济南", "北京", "深圳"],
        "overseas_preference": "unset",
    },
    {
        "id": 2,
        "name": "示例·国际采购版",
        "note": "采购 / 供应链方向，外语可用",
        "education": "本科",
        "skills": ["Excel", "英语", "PowerPoint", "数据分析"],
        "languages": ["英语", "CET-6"],
        "target_industries": [],
        "target_job_types": ["采购/寻源", "供应链/物流"],
        "target_cities": ["东营", "济南", "青岛", "北京"],
        "overseas_preference": "accept_travel",
    },
    {
        "id": 3,
        "name": "示例·海外销售版",
        "note": "外贸 / 海外市场，接受长期外派",
        "education": "本科",
        "skills": ["英语", "Excel", "PowerPoint"],
        "languages": ["英语", "CET-6"],
        "target_industries": [],
        "target_job_types": ["海外销售", "国际业务", "外贸BD", "工业品销售"],
        "target_cities": ["深圳", "广州", "上海", "北京"],
        "overseas_preference": "accept_long_term",
    },
    {
        "id": 4,
        "name": "示例·数据分析版",
        "note": "数据方向，工具栈偏技术",
        "education": "本科",
        "skills": ["Excel", "SQL", "Python", "Tableau", "Power BI", "数据分析"],
        "languages": ["英语", "CET-6"],
        "target_industries": [],
        "target_job_types": ["数据分析", "技术销售"],
        "target_cities": ["北京", "深圳", "杭州", "上海"],
        "overseas_preference": "no_long_term",
    },
]


def main() -> int:
    db.init_db()
    with db.connect() as conn:
        jobs = [shape(job) for job in sample_jobs(conn)]
        tracks = db.rows_to_dicts(conn.execute(
            "SELECT code, name FROM industry_tracks ORDER BY tier, id").fetchall())

    payload = {
        "generated_at": db.now(),
        "note": "岗位为真实公开岗位的抽样快照；简历是虚构示例，不对应任何真实个人。",
        "resumes": DEMO_RESUMES,
        "tracks": tracks,
        "jobs": jobs,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    size_kb = OUT.stat().st_size / 1024
    print(f"已导出 {len(jobs)} 条岗位 → {OUT}（{size_kb:.0f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
