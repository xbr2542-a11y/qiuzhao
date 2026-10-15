"""投递记录、状态识别、重复检测、提交前检查（V2.0 第二、三、五、六章）。

核心原则（来自需求文档，代码里逐条落实）：
1. 只有拿到**真实提交结果或可靠证据**才能标记"已投递"；点按钮、打开网页、填了表单都不算。
2. 分不清的状态一律 `pending_verification`，不猜。
3. 区分状态来源与置信度：official / user_input / email / manual_review / unknown ×
   confirmed / inferred / unconfirmed。
4. 重复投递用平台职位ID + 标准化公司/岗位/地点综合判断，不能只看岗位名。
"""

from __future__ import annotations

import json
import re
from typing import Any

import db

# 全流程状态（需求文档第六章）
# 需求文档用了两套词汇：第二章讲"投递状态"，第六章讲"进度阶段"。这里合并成一套。
APPLICATION_STAGES = [
    # 第二章：投递状态
    "not_applied", "in_progress", "submitted", "failed", "pending_verification", "need_info",
    # 第六章：后续进度阶段
    "resume_screening", "online_assessment",
    "interview_invited", "interviewing", "offer_received", "rejected", "withdrawn",
    "expired", "unknown",
]

# 投递确实发生过的状态（用于重复投递统计）
SUBMITTED_LIKE = {"submitted", "resume_screening", "online_assessment", "interview_invited",
                  "interviewing", "offer_received", "rejected", "withdrawn", "expired"}

STAGE_LABELS = {
    "not_applied": "未投递", "in_progress": "投递进行中", "submitted": "已投递",
    "failed": "投递失败", "pending_verification": "状态待确认", "need_info": "需要补充信息",
    "resume_screening": "简历筛选", "online_assessment": "笔测/在线测评",
    "interview_invited": "收到面试邀请", "interviewing": "面试中",
    "offer_received": "收到 Offer", "rejected": "已拒绝", "withdrawn": "已撤回",
    "expired": "职位关闭", "unknown": "无法确认",
}

# 失败原因分类（需求文档第二章）
FAILURE_REASONS = ["网络错误", "必填信息缺失", "平台拒绝提交", "页面状态无法确认", "超出授权范围"]

SOURCE_TYPES = ["official", "user_input", "email", "manual_review", "unknown"]
CONFIDENCE_LEVELS = ["confirmed", "inferred", "unconfirmed"]


def normalize_company(name: str) -> str:
    """公司主体归一化：去掉括号后缀、地区修饰，避免同一家公司被判成两家。"""
    text = (name or "").strip()
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    text = re.sub(r"(有限责任公司|股份有限公司|有限公司|集团|公司)$", "", text)
    return re.sub(r"\s+", "", text).lower()


def duplicate_check(job_id: int) -> dict:
    """重复投递检测：平台职位ID → 公司+岗位+地点 → 来源URL。"""
    with db.connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (int(job_id),)).fetchone()
        if job is None:
            return {"duplicate": False, "reason": "岗位不存在"}
        job = dict(job)
        # 1) 同一个 job 已有投递记录
        same_job = conn.execute(
            "SELECT id, application_status, submitted_at FROM applications WHERE job_id = ?",
            (int(job_id),)).fetchone()
        if same_job:
            return {"duplicate": True, "level": "confirmed", "application_id": same_job["id"],
                    "reason": f"该岗位已有投递记录（{STAGE_LABELS.get(same_job['application_status'], same_job['application_status'])}）"}

        # 2) 平台职位ID相同
        platform_job_id = str(job.get("url") or "").rstrip("/").split("/")[-1]
        if platform_job_id:
            hit = conn.execute(
                "SELECT id FROM applications WHERE platform_job_id = ?", (platform_job_id,)).fetchone()
            if hit:
                return {"duplicate": True, "level": "confirmed", "application_id": hit["id"],
                        "reason": f"平台职位ID {platform_job_id} 已有投递记录"}

        # 3) 公司主体 + 岗位名 + 地点（标准化后）
        rows = db.rows_to_dicts(conn.execute("""
            SELECT a.id, a.application_status, j.company, j.title, j.city
            FROM applications a JOIN jobs j ON j.id = a.job_id""").fetchall())
        company = normalize_company(job.get("company", ""))
        title = re.sub(r"\s+", "", job.get("title", "") or "")
        for row in rows:
            if normalize_company(row["company"]) != company:
                continue
            same_title = re.sub(r"\s+", "", row["title"] or "") == title
            same_city = (row["city"] or "") and (job.get("city") or "") and \
                (row["city"][:4] in job.get("city", "") or job["city"][:4] in row["city"])
            if same_title and same_city:
                return {"duplicate": True, "level": "inferred", "application_id": row["id"],
                        "reason": f"疑似重复：{row['company']}·{row['title']}·{row['city']} 已有投递记录"}

        # 4) 来源URL相同
        same_url = conn.execute("""
            SELECT a.id FROM applications a JOIN jobs j ON j.id = a.job_id
            WHERE j.url != '' AND j.url = ?""", (job.get("url", ""),)).fetchone()
        if same_url:
            return {"duplicate": True, "level": "confirmed", "application_id": same_url["id"],
                    "reason": "同一投递链接已有记录"}
    return {"duplicate": False, "reason": ""}


def pre_submit_check(job: dict, resume: dict | None, profile: dict, permission: dict | None) -> dict:
    """提交前质量检查（需求文档第五章第 3 节）。返回是否可以提交 + 阻断原因。"""
    blockers: list[str] = []
    warnings: list[str] = []

    # 1) 岗位是否仍有效
    deadline = job.get("deadline") or ""
    left = None
    if deadline:
        import rules
        left = rules.days_left(deadline)
        if left is not None and left < 0:
            blockers.append(f"岗位已过截止日期（{deadline}）")

    # 2) 是否重复投递
    dup = duplicate_check(job["id"])
    if dup.get("duplicate"):
        blockers.append(dup["reason"])

    # 3) 是否超出用户授权范围
    if permission and permission.get("enabled"):
        tracks = _json_list(permission.get("target_tracks"))
        if tracks and job.get("industry_track") and job["industry_track"] not in tracks:
            blockers.append(f"岗位赛道（{job.get('industry_subtrack') or job['industry_track']}）超出授权范围")
        cities = _json_list(permission.get("target_cities"))
        if cities and job.get("city") and not any(c in job["city"] for c in cities):
            blockers.append(f"工作地点（{job['city']}）超出授权城市范围")

    # 4) 外派条件冲突
    pref = (resume or {}).get("overseas_preference") or profile.get("overseas_preference") or "unset"
    status = job.get("overseas_assignment_status") or "not_mentioned"
    if pref in {"no_long_term", "accept_short_term", "accept_travel", "decline_all"}:
        if status in {"mandatory_overseas_assignment", "long_term_assignment"}:
            label = {"mandatory_overseas_assignment": "强制境外驻派",
                     "long_term_assignment": "长期外派"}.get(status, status)
            blockers.append(f"岗位为「{label}」，与你的外派偏好冲突")
    elif status == "mandatory_overseas_assignment":
        warnings.append("该岗位明确要求长期境外驻派，请确认后提交")

    # 5) 简历是否选对
    if not resume:
        blockers.append("未选择简历版本")
    elif resume.get("status") not in (None, "", "active"):
        blockers.append("所选简历版本已停用")

    # 6) 关键信息是否完整（不得猜测）
    for field, label in [("full_name", "姓名"), ("phone", "手机号"), ("email", "邮箱"),
                         ("school", "学校"), ("major", "专业")]:
        if not (profile.get(field) or "").strip():
            warnings.append(f"档案缺少{label}，在线表单需要手填")

    # 7) 岗位要求与简历明显冲突
    if job.get("major_tier") == 3:
        warnings.append("岗位专业要求与你的专业不符，可能被机器筛选淘汰")

    return {
        "can_submit": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "duplicate": dup,
    }


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def save_application(job_id: int, resume_id: int | None = None, status: str = "not_applied",
                     source: str = "user_input", confidence: str = "unconfirmed",
                     evidence: str = "", stage: str = "", note: str = "") -> dict:
    """写入或更新投递记录。**不会**因为调用就标记为已投递——状态由调用方给出并必须带依据。"""
    if status not in APPLICATION_STAGES:
        raise ValueError(f"未知状态：{status}")
    if status == "submitted" and confidence != "confirmed":
        raise ValueError("标记已投递必须提供已确认的依据（confidence=confirmed）")
    with db.connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (int(job_id),)).fetchone()
        if job is None:
            raise ValueError("岗位不存在")
        existing = conn.execute("SELECT * FROM applications WHERE job_id=?", (int(job_id),)).fetchone()
        platform_job_id = str(job["url"] or "").rstrip("/").split("/")[-1]
        now = db.now()
        if existing:
            conn.execute(
                """UPDATE applications SET resume_id=COALESCE(?, resume_id), application_status=?,
                   current_stage=?, status_source=?, status_confidence=?, evidence=?, note=?,
                   submitted_at=CASE WHEN ?='submitted' AND submitted_at='' THEN ? ELSE submitted_at END,
                   last_checked_at=?, updated_at=? WHERE id=?""",
                (resume_id, status, stage or status, source, confidence, evidence, note,
                 status, now, now, now, existing["id"]))
            app_id = existing["id"]
        else:
            cursor = conn.execute(
                """INSERT INTO applications (job_id, resume_id, platform, platform_job_id,
                   application_status, current_stage, status_source, status_confidence, evidence,
                   note, submitted_at, last_checked_at, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (int(job_id), resume_id, job["source"], platform_job_id, status, stage or status,
                 source, confidence, evidence, note,
                 now if status == "submitted" else "", now, now, now))
            app_id = cursor.lastrowid
        conn.execute("""
            INSERT INTO application_events (application_id, event_type, event_time, source,
              evidence, confidence, notes) VALUES (?,?,?,?,?,?,?)""",
            (app_id, status, now, source, evidence, confidence, note))
        if status == "submitted":
            conn.execute("UPDATE jobs SET status='已投递', updated_at=? WHERE id=?", (now, int(job_id)))
        conn.commit()
    db.log("application", f"投递状态更新为 {STAGE_LABELS.get(status, status)}",
           {"job_id": job_id, "source": source, "confidence": confidence})
    return {"application_id": app_id, "status": status, "stage_label": STAGE_LABELS.get(status, status)}


def list_applications() -> list[dict]:
    with db.connect() as conn:
        rows = db.rows_to_dicts(conn.execute("""
            SELECT a.*, j.company, j.title, j.city, j.url, j.deadline, j.industry_subtrack,
                   r.name AS resume_name
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            LEFT JOIN resumes r ON r.id = a.resume_id
            ORDER BY a.updated_at DESC""").fetchall())
    for row in rows:
        row["stage_label"] = STAGE_LABELS.get(row.get("application_status"), row.get("application_status"))
    return rows


def application_events(application_id: int) -> list[dict]:
    with db.connect() as conn:
        return db.rows_to_dicts(conn.execute(
            "SELECT * FROM application_events WHERE application_id=? ORDER BY id DESC",
            (int(application_id),)).fetchall())
