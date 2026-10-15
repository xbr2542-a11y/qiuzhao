"""岗位与档案的数据操作（被 API 层和任务处理器共用，避免循环依赖）。"""

from __future__ import annotations

import json
import re
from typing import Any

import classify
import db
import rules
import tracks as tracks_mod

# 入库时要写的列。用"列清单 + 动态 SQL"而不是手写两条长语句：
# 历史上就是因为手写 UPDATE 时漏列，note / channel_type 被静默丢弃过。
INSERT_COLUMNS = [
    "dedupe_key", "company", "title", "city", "source", "family", "channel_type", "url",
    "deadline", "major_req", "major_tier", "degree_req", "grad_only", "salary", "salary_min",
    "salary_max", "match_score", "filter_note", "status", "jd_text", "experience_req",
    "job_type", "industry", "published_at", "company_nature", "company_scale", "note",
    "industry_track", "industry_subtrack", "sales_type", "job_function", "candidate_level",
    "overseas_assignment_status", "overseas_assignment_evidence", "review_status",
    "exclusion_reason", "match_tags_json", "risk_tags_json", "language_requirement",
    "classify_evidence", "data_fetched_at", "collected_at", "updated_at", "raw_json",
]

# 更新时刷新的列：不含 dedupe_key / status / collected_at —— 
# 用户手动改过的投递状态不能被重新采集覆盖掉。
UPDATE_COLUMNS = [c for c in INSERT_COLUMNS
                  if c not in {"dedupe_key", "status", "collected_at"}]


def _load_rules() -> list[dict]:
    with db.connect() as conn:
        rows = db.rows_to_dicts(conn.execute(
            "SELECT * FROM exclusion_rules WHERE enabled = 1").fetchall())
    for row in rows:
        try:
            row["patterns"] = json.loads(row.get("patterns") or "[]")
        except json.JSONDecodeError:
            row["patterns"] = []
    return rows


def get_profile() -> dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
    profile = dict(row) if row else {}
    profile["prefer_cities"] = json.loads(profile.get("prefer_cities") or "[]")
    profile["extra"] = json.loads(profile.get("extra_json") or "{}")
    profile["skills"] = json.loads(profile.get("skills_json") or "[]")
    profile["target_families"] = json.loads(profile.get("target_families_json") or "[]")
    return profile


def save_profile(payload: dict[str, Any]) -> dict[str, Any]:
    current = get_profile()
    fields = {
        "full_name": payload.get("full_name", current.get("full_name", "")),
        "school": payload.get("school", current.get("school", "")),
        "major": payload.get("major", current.get("major", "")),
        "degree": payload.get("degree", current.get("degree", "")),
        "grad_date": payload.get("grad_date", current.get("grad_date", "")),
        "political": payload.get("political", current.get("political", "")),
        "hometown": payload.get("hometown", current.get("hometown", "")),
        "phone": payload.get("phone", current.get("phone", "")),
        "email": payload.get("email", current.get("email", "")),
    }
    cities = payload.get("prefer_cities", current.get("prefer_cities", []))
    if isinstance(cities, str):
        cities = [c.strip() for c in re.split(r"[、,，\s]+", cities) if c.strip()]
    extra = payload.get("extra", current.get("extra", {}))
    skills = payload.get("skills", current.get("skills", []))
    veteran = payload.get("veteran", current.get("veteran", 0))
    with db.connect() as conn:
        conn.execute(
            """UPDATE profile SET full_name=?, school=?, major=?, degree=?, grad_date=?,
               political=?, hometown=?, phone=?, email=?, prefer_cities=?, extra_json=?,
               skills_json=?, veteran=?, updated_at=? WHERE id = 1""",
            (*fields.values(), json.dumps(cities, ensure_ascii=False),
             json.dumps(extra, ensure_ascii=False), json.dumps(skills, ensure_ascii=False),
             1 if veteran else 0, db.now()),
        )
        conn.commit()
    return get_profile()


HEADER_ALIASES = {
    "公司": "company", "企业": "company", "公司名称": "company", "company": "company",
    "岗位": "title", "职位": "title", "岗位名称": "title", "title": "title",
    "城市": "city", "地点": "city", "工作地点": "city", "city": "city",
    "来源": "source", "渠道": "source", "来源渠道": "source", "source": "source",
    "链接": "url", "网址": "url", "投递链接": "url", "url": "url",
    "截止日期": "deadline", "截止时间": "deadline", "报名截止": "deadline", "deadline": "deadline",
    "专业要求": "major_req", "专业": "major_req", "major_req": "major_req",
    "学历要求": "degree_req", "学历": "degree_req", "degree_req": "degree_req",
    "岗位族": "family", "family": "family",
    "薪资": "salary", "薪资范围": "salary", "salary": "salary",
    "备注": "note", "说明": "note", "note": "note",
}


def parse_pasted_table(text: str) -> list[dict[str, Any]]:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return []
    delimiter = "\t" if "\t" in lines[0] else ","
    headers = [HEADER_ALIASES.get(h.strip(), h.strip()) for h in lines[0].split(delimiter)]
    jobs: list[dict[str, Any]] = []
    for line in lines[1:]:
        values = [v.strip() for v in line.split(delimiter)]
        record = {headers[i]: values[i] for i in range(min(len(headers), len(values)))}
        if record.get("company") or record.get("title"):
            jobs.append(record)
    return jobs


def upsert_jobs(raw_jobs: list[dict[str, Any]], source_default: str = "") -> dict[str, Any]:
    profile = get_profile()
    rules_list = _load_rules()
    track_defs = tracks_mod.TRACKS
    inserted, updated, skipped = 0, 0, 0
    insert_sql = "INSERT INTO jobs ({}) VALUES ({})".format(
        ", ".join(INSERT_COLUMNS), ", ".join("?" * len(INSERT_COLUMNS)))
    update_sql = "UPDATE jobs SET {} WHERE id = ?".format(
        ", ".join(f"{c} = ?" for c in UPDATE_COLUMNS))
    with db.connect() as conn:
        for raw in raw_jobs:
            if not isinstance(raw, dict):
                skipped += 1
                continue
            raw = dict(raw)
            if source_default and not raw.get("source"):
                raw["source"] = source_default
            job = rules.evaluate(raw, profile)
            if not job.get("dedupe_key"):
                skipped += 1
                continue
            # 采集进来的岗位当场完成识别（赛道 / 职能 / 销售类型 / 外派 / 复核）。
            # 以前这步只在 tools/reclassify.py 里做，新采集的岗位在筛选里等于"看不见"。
            try:
                job.update(classify.classify_job(job, track_defs, rules_list))
            except Exception:
                pass
            now = db.now()
            values = {
                "dedupe_key": job["dedupe_key"],
                "company": job.get("company", ""),
                "title": job.get("title", ""),
                "city": job.get("city", ""),
                "source": job.get("source", ""),
                "family": job.get("family", "general"),
                "channel_type": job.get("channel_type") or "it",
                "url": job.get("url", ""),
                "deadline": job.get("deadline", ""),
                "major_req": job.get("major_req", ""),
                "major_tier": job.get("major_tier", 0),
                "degree_req": job.get("degree_req", ""),
                "grad_only": 1,
                "salary": job.get("salary", ""),
                "salary_min": int(job.get("salary_min") or 0),
                "salary_max": int(job.get("salary_max") or 0),
                "match_score": job.get("match_score", 0),
                "filter_note": job.get("filter_note", ""),
                "status": "待投",
                "jd_text": job.get("jd_text", ""),
                "experience_req": job.get("experience_req", ""),
                "job_type": job.get("job_type", ""),
                "industry": job.get("industry", ""),
                "published_at": job.get("published_at", ""),
                "company_nature": job.get("company_nature", ""),
                "company_scale": job.get("company_scale", ""),
                "note": job.get("note", ""),
                "industry_track": job.get("industry_track", ""),
                "industry_subtrack": job.get("industry_subtrack", ""),
                "sales_type": job.get("sales_type", ""),
                "job_function": job.get("job_function", ""),
                "candidate_level": job.get("candidate_level", ""),
                "overseas_assignment_status": job.get("overseas_assignment_status")
                                              or "not_mentioned",
                "overseas_assignment_evidence": job.get("overseas_assignment_evidence", ""),
                "review_status": job.get("review_status", "pending"),
                "exclusion_reason": job.get("exclusion_reason", ""),
                "match_tags_json": job.get("match_tags_json", "[]"),
                "risk_tags_json": job.get("risk_tags_json", "[]"),
                "language_requirement": job.get("language_requirement", ""),
                "classify_evidence": job.get("classify_evidence", ""),
                "data_fetched_at": now,
                "collected_at": now,
                "updated_at": now,
                "raw_json": json.dumps(job.get("raw") or {}, ensure_ascii=False, default=str),
            }
            existing = conn.execute(
                "SELECT id FROM jobs WHERE dedupe_key = ?", (job["dedupe_key"],)).fetchone()
            if existing:
                conn.execute(update_sql,
                             [values[col] for col in UPDATE_COLUMNS] + [existing["id"]])
                updated += 1
            else:
                conn.execute(insert_sql, [values[col] for col in INSERT_COLUMNS])
                inserted += 1
        conn.commit()
    db.log("ingest", f"新增 {inserted}，更新 {updated}，跳过 {skipped}")
    return {"inserted": inserted, "updated": updated, "skipped": skipped, "total": inserted + updated}


def clear_source(labels: list[str]) -> int:
    """只清空指定来源的岗位。

    必须传精确的来源名（jobs.source 的完整取值）。历史事故：曾用 LIKE '%官网'
    清空，把"美团校招官网"等其他来源一起删了，所以这里拒绝模糊匹配。
    """
    labels = [label for label in labels if label]
    if not labels:
        raise ValueError("clear_source 需要明确的来源名，拒绝执行空条件删除")
    if any("%" in label or "_" in label for label in labels):
        raise ValueError("来源名不允许包含通配符")
    condition = "source IN (" + ",".join("?" * len(labels)) + ")"
    with db.connect() as conn:
        removed = conn.execute(f"DELETE FROM jobs WHERE {condition}", labels).rowcount
        conn.commit()
    return removed
