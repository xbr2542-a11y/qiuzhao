"""秋招管家 API 层。

数据读写统一走 app/ingest.py，筛选规则走 app/rules.py，任务走 app/tasks.py。
这里只负责路由、参数校验与静态资源——不放任何业务实现，避免出现两份实现互相不一致。
"""

from __future__ import annotations

import argparse
import gzip
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import export  # noqa: E402
import handlers  # noqa: E402
import ingest  # noqa: E402
import applications as app_mod  # noqa: E402
import match as match_mod  # noqa: E402
import resume as resume_mod  # noqa: E402
import runtime  # noqa: E402
import tasks as tasks_mod  # noqa: E402

# 统一由 ingest 提供，这里保留旧名字以兼容
get_profile = ingest.get_profile
save_profile = ingest.save_profile
upsert_jobs = ingest.upsert_jobs
parse_pasted_table = ingest.parse_pasted_table

WEB_DIR = Path(__file__).resolve().parent / "web"
WORKER_DIR = runtime.ROOT / "workers"
DEFAULT_PORT = 8760

MANAGER = tasks_mod.TaskManager()
handlers.register_all(MANAGER)

TASK_ACTION_RE = re.compile(r"^/api/tasks/(\d+)/(pause|resume|stop|retry)$")
TASK_ID_RE = re.compile(r"^/api/tasks/(\d+)$")
TASK_LOGS_RE = re.compile(r"^/api/tasks/(\d+)/logs$")
JOB_ID_RE = re.compile(r"^/api/jobs/(\d+)$")
JOB_MATCH_RE = re.compile(r"^/api/jobs/(\d+)/match$")
RESUME_INTENT_RE = re.compile(r"^/api/resumes/(\d+)/intent$")
APP_EVENT_RE = re.compile(r"^/api/applications/(\d+)/events$")


# ---------------------------------------------------------------- 查询

def list_resumes(include_archived: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM resumes"
    if not include_archived:
        sql += " WHERE archived = 0"
    sql += " ORDER BY is_default DESC, id DESC"
    with db.connect() as conn:
        rows = db.rows_to_dicts(conn.execute(sql).fetchall())
    for row in rows:
        row["families"] = json.loads(row.get("families") or "[]")
        row["parsed"] = json.loads(row.get("parsed_json") or "{}")
        # V2.0 的求职意向字段也是 JSON，必须一起还原，否则前端拿到字符串会崩
        for field in ("target_industries", "target_job_types", "target_cities",
                      "skills_json", "languages_json"):
            try:
                row[field] = json.loads(row.get(field) or "[]")
            except (TypeError, json.JSONDecodeError):
                row[field] = []
        row["skills"] = row["skills_json"]
        row["languages"] = row["languages_json"]
    return rows


def fetch_jobs(filters: dict[str, Any]) -> list[dict[str, Any]]:
    import rules

    clauses: list[str] = []
    params: list[Any] = []
    if filters.get("family"):
        clauses.append("j.family = ?")
        params.append(filters["family"])
    if filters.get("status"):
        clauses.append("j.status = ?")
        params.append(filters["status"])
    if filters.get("city"):
        clauses.append("j.city LIKE ?")
        params.append(f"%{filters['city']}%")
    if filters.get("city_in"):
        wanted = [c.strip() for c in str(filters["city_in"]).split(",") if c.strip()]
        if wanted:
            clauses.append("(" + " OR ".join(["j.city LIKE ?"] * len(wanted)) + ")")
            params.extend(f"%{c}%" for c in wanted)
    if filters.get("tier"):
        clauses.append("j.major_tier = ?")
        params.append(int(filters["tier"]))
    if filters.get("q"):
        clauses.append("(j.company LIKE ? OR j.title LIKE ? OR j.jd_text LIKE ? OR j.note LIKE ?)")
        keyword = f"%{filters['q']}%"
        params.extend([keyword, keyword, keyword, keyword])
    # ---- 行业赛道 / 销售类型 / 外派 / 复核状态（本次扩展）----
    if filters.get("track"):
        clauses.append("j.industry_track = ?")
        params.append(filters["track"])
    if filters.get("sales_type"):
        clauses.append("j.sales_type LIKE ?")
        params.append(f"%{filters['sales_type']}%")
    if filters.get("function"):
        wanted = [c.strip() for c in str(filters["function"]).split(",") if c.strip()]
        if wanted:
            clauses.append("(" + " OR ".join(["j.job_function = ?"] * len(wanted)) + ")")
            params.extend(wanted)
    if filters.get("overseas"):
        clauses.append("j.overseas_assignment_status = ?")
        params.append(filters["overseas"])
    if filters.get("review"):
        clauses.append("j.review_status = ?")
        params.append(filters["review"])
    if filters.get("target_only") == "1":
        # 只看目标销售岗位（B端解决方案销售 / 外贸BD），且未被排除
        clauses.append("(j.sales_type LIKE '%解决方案型%' OR j.sales_type LIKE '%外贸BD%')")
        clauses.append("j.review_status != 'excluded'")
    if filters.get("no_overseas_mandatory") == "1":
        clauses.append("j.overseas_assignment_status NOT IN "
                       "('mandatory_overseas_assignment','long_term_assignment')")
    # 隐藏"专业明确限定且不对口"的岗位（档位 3）
    if filters.get("hide_major_block") == "1" or filters.get("max_tier") == "1":
        clauses.append("j.major_tier IN (0, 1, 2)")
    if filters.get("source"):
        clauses.append("j.source LIKE ?")
        params.append(f"%{filters['source']}%")
    if filters.get("job_type"):
        clauses.append("j.job_type = ?")
        params.append(filters["job_type"])
    if filters.get("degree"):
        clauses.append("j.degree_req LIKE ?")
        params.append(f"%{filters['degree']}%")
    if filters.get("min_score"):
        clauses.append("j.match_score >= ?")
        params.append(int(filters["min_score"]))
    if filters.get("open_only") == "1":
        clauses.append("(j.deadline = '' OR j.deadline >= date('now'))")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order_by = {
        "score": "j.match_score DESC, j.deadline IS NULL, j.deadline ASC",
        "deadline": "j.deadline IS NULL, j.deadline ASC",
        "recent": "j.collected_at DESC",
        "published": "j.published_at DESC",
        "salary": "j.salary_max DESC, j.match_score DESC",
        "company": "j.company ASC",
    }.get(filters.get("sort") or "score", "j.match_score DESC")

    # 注意：列表接口不返回 jd_text / raw_json —— 这两个字段很大，
    # 2500 条岗位会把响应撑到几十 MB，页面直接卡死。详情另走 /api/jobs/{id}。
    sql = f"""
        SELECT j.id, j.company, j.title, j.city, j.source, j.family, j.channel_type, j.url,
               j.deadline, j.major_req, j.major_tier, j.filter_note, j.degree_req, j.grad_only,
               j.salary, j.salary_min, j.salary_max, j.match_score, j.status, j.resume_id,
               j.note, j.job_type, j.industry, j.published_at, j.experience_req,
               j.is_veteran_special, j.collected_at, j.company_nature,
               j.industry_track, j.industry_subtrack, j.sales_type, j.candidate_level,
               j.job_function,
               j.overseas_assignment_status, j.overseas_assignment_evidence,
               j.review_status, j.exclusion_reason, j.match_tags_json, j.risk_tags_json,
               r.name AS resume_name
        FROM jobs j LEFT JOIN resumes r ON r.id = j.resume_id
        {where} ORDER BY {order_by} LIMIT 20000
    """
    with db.connect() as conn:
        rows = db.rows_to_dicts(conn.execute(sql, params).fetchall())
    for row in rows:
        row["days_left"] = rules.days_left(row.get("deadline", ""))
        # 标签从 JSON 字段还原，前端直接用
        for key in ("match_tags_json", "risk_tags_json"):
            raw = row.pop(key, "[]")
            try:
                row[key.replace("_json", "")] = json.loads(raw or "[]")
            except json.JSONDecodeError:
                row[key.replace("_json", "")] = []
    return rows


def get_job(job_id: int) -> dict[str, Any] | None:
    """岗位详情：包含 JD 全文。"""
    import rules

    with db.connect() as conn:
        row = conn.execute(
            """SELECT j.*, r.name AS resume_name FROM jobs j
               LEFT JOIN resumes r ON r.id = j.resume_id WHERE j.id = ?""",
            (int(job_id),),
        ).fetchone()
        if row is None:
            return None
        materials = db.rows_to_dicts(conn.execute(
            "SELECT item, required, have, note FROM materials WHERE job_id = ?", (int(job_id),)).fetchall())
    job = dict(row)
    job["days_left"] = rules.days_left(job.get("deadline", ""))
    job["materials"] = materials
    return job


def stats() -> dict[str, Any]:
    with db.connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        by_family = db.rows_to_dicts(conn.execute(
            "SELECT family, COUNT(*) AS n FROM jobs GROUP BY family ORDER BY n DESC").fetchall())
        by_status = db.rows_to_dicts(conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status ORDER BY n DESC").fetchall())
        by_tier = db.rows_to_dicts(conn.execute(
            "SELECT major_tier, COUNT(*) AS n FROM jobs GROUP BY major_tier ORDER BY major_tier").fetchall())
        soon = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE deadline != '' AND deadline >= date('now') "
            "AND deadline <= date('now', '+7 day')").fetchone()[0]
        resumes = conn.execute("SELECT COUNT(*) FROM resumes WHERE archived = 0").fetchone()[0]
        applied = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN ('已投','笔试','面试','Offer')").fetchone()[0]
    return {"total": total, "by_family": by_family, "by_status": by_status, "by_tier": by_tier,
            "due_in_7_days": soon, "resumes": resumes, "applied": applied}


def facets() -> dict[str, list[str]]:
    """筛选面板用到的可选值。"""
    with db.connect() as conn:
        # 注意：这里查出来的每个元素已经是字符串（row["字段名"]），
        # 不能再对结果取 [0]——那样会把「本科」变成「本」。
        sources = [r["source"] for r in conn.execute(
            "SELECT source FROM jobs WHERE source != '' GROUP BY source "
            "ORDER BY COUNT(*) DESC").fetchall()]
        degrees = [r["degree_req"] for r in conn.execute(
            "SELECT degree_req FROM jobs WHERE degree_req != '' GROUP BY degree_req "
            "ORDER BY COUNT(*) DESC").fetchall()]
        job_types = [r["job_type"] for r in conn.execute(
            "SELECT job_type FROM jobs WHERE job_type != '' GROUP BY job_type "
            "ORDER BY COUNT(*) DESC").fetchall()]
        # 赛道 / 销售类型 / 外派 / 复核状态
        track_rows = db.rows_to_dicts(conn.execute(
            "SELECT industry_track code, industry_subtrack name, COUNT(*) n FROM jobs "
            "WHERE industry_track != '' GROUP BY industry_track ORDER BY n DESC").fetchall())
        sales_rows = db.rows_to_dicts(conn.execute(
            "SELECT sales_type, COUNT(*) n FROM jobs WHERE sales_type != '' "
            "GROUP BY sales_type ORDER BY n DESC LIMIT 12").fetchall())
        function_rows = db.rows_to_dicts(conn.execute(
            "SELECT job_function, COUNT(*) n FROM jobs WHERE job_function != '' "
            "GROUP BY job_function ORDER BY n DESC").fetchall())
        overseas_rows = db.rows_to_dicts(conn.execute(
            "SELECT overseas_assignment_status, COUNT(*) n FROM jobs "
            "GROUP BY overseas_assignment_status ORDER BY n DESC").fetchall())
        review_rows = db.rows_to_dicts(conn.execute(
            "SELECT review_status, COUNT(*) n FROM jobs GROUP BY review_status").fetchall())
        family_rows = db.rows_to_dicts(conn.execute(
            "SELECT family, COUNT(*) n FROM jobs GROUP BY family ORDER BY n DESC").fetchall())
        # 按"拆开后的城市名"统计岗位数，取前 40 —— 之前直接截前 80 个，
        # 结果济南、北京这种常用城市根本不在列表里。
        city_rows = conn.execute("SELECT city FROM jobs WHERE city != ''").fetchall()
    token_counts: dict[str, int] = {}
    for row in city_rows:
        city = row["city"] if isinstance(row, dict) else row[0]
        for token in re.split(r"[/、,\s]+", city or ""):
            name = normalize_city(token)
            if name:
                token_counts[name] = token_counts.get(name, 0) + 1
    top_cities = sorted(token_counts.items(), key=lambda item: (-item[1], item[0]))[:40]
    # 你的意向城市永远排在最前面，哪怕岗位数不多（济南、东营就是这样被挤出去的）
    preferred = [normalize_city(c) for c in (ingest.get_profile().get("prefer_cities") or [])]
    ordered = [c for c in preferred if c]
    for name, _ in top_cities:
        if name not in ordered:
            ordered.append(name)
    return {
        "sources": sources, "degrees": degrees, "job_types": job_types,
        "cities": ordered,
        "city_counts": {name: token_counts.get(name, 0) for name in ordered},
        "tracks": track_rows, "sales_types": sales_rows,
        "functions": function_rows, "families": family_rows,
        "overseas": overseas_rows, "review": review_rows,
    }


def normalize_city(token: str) -> str:
    """把「北京-朝阳区」「上海-浦东新区」归并成「北京」「上海」，
    否则细分行政区会把真正的地级市挤出下拉列表。"""
    token = (token or "").strip().strip("（）()")
    if not token:
        return ""
    for separator in ("-", "—", "－", "（", "("):
        if separator in token:
            token = token.split(separator)[0].strip()
    return token


# ---------------------------------------------------------------- 简历 / 采集器

def save_resume(payload: dict[str, Any]) -> dict[str, Any]:
    saved = resume_mod.save_file(payload["filename"], payload["data"])
    parsed = resume_mod.parse_resume_file(saved["stored_path"])
    families = payload.get("families") or []
    if isinstance(families, str):
        families = [f for f in families.split(",") if f]
    is_default = 1 if payload.get("is_default") else 0
    with db.connect() as conn:
        if is_default:
            conn.execute("UPDATE resumes SET is_default = 0")
        cursor = conn.execute(
            """INSERT INTO resumes (name, families, filename, stored_path, size, sha256,
               parsed_json, is_default, uploaded_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (payload.get("name") or Path(saved["filename"]).stem or "未命名版本",
             json.dumps(families, ensure_ascii=False), saved["filename"], saved["stored_path"],
             saved["size"], saved["sha256"], json.dumps(parsed, ensure_ascii=False),
             is_default, db.now()),
        )
        resume_id = int(cursor.lastrowid)
        conn.commit()
    db.log("resume", f"上传简历 {payload.get('name')}", {"id": resume_id})
    return {"id": resume_id, "parsed": parsed, **saved}


def list_workers() -> list[dict[str, str]]:
    if not WORKER_DIR.exists():
        return []
    return [{"name": p.stem, "path": str(p)} for p in sorted(WORKER_DIR.glob("*.cjs"))
            if not p.stem.startswith("_")]


def run_worker(name: str, args: list[str] | None = None) -> dict[str, Any]:
    script = WORKER_DIR / f"{name}.cjs"
    if not script.exists():
        return {"ok": False, "error": f"采集器尚未实现：{name}"}
    node = runtime.find_node()
    if not node:
        return {"ok": False, "error": "未找到 Node.js 运行时"}
    try:
        proc = subprocess.run([node, str(script), *(args or [])], capture_output=True, text=True,
                              encoding="utf-8", errors="ignore", env=runtime.worker_env(), timeout=900)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "采集超时（15 分钟）"}
    output = (proc.stdout or "").strip()
    payload: dict[str, Any] = {}
    for line in reversed(output.splitlines()):
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "jobs": payload.get("jobs", []), "log": output[-4000:],
            "error": (proc.stderr or "")[-2000:]}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "QiuzhaoPanel/0.2"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}
        # 岗位池有几 MB，压缩后能小一个数量级，页面加载快很多
        if len(body) > 1024 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body = gzip.compress(body, 6)
            headers["Content-Encoding"] = "gzip"
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, download_name: str | None = None) -> None:
        if not path.exists():
            self.send_json({"error": "文件不存在"}, 404)
            return
        data = path.read_bytes()
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        # 本地工具：永不缓存，避免改了界面但浏览器还在用旧文件
        self.send_header("Cache-Control", "no-store, must-revalidate")
        if download_name:
            quoted = urllib.parse.quote(download_name)
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quoted}")
        self.end_headers()
        self.wfile.write(data)

    def read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    # ---- GET
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        try:
            if parsed.path in {"/", "/index.html"}:
                self.send_file(WEB_DIR / "index.html")
                return
            if parsed.path.startswith("/web/"):
                target = (WEB_DIR / parsed.path[len("/web/"):]).resolve()
                if target.parent == WEB_DIR.resolve():
                    self.send_file(target)
                else:
                    self.send_json({"error": "非法路径"}, 403)
                return
            if parsed.path == "/api/state":
                self.send_json({
                    "profile": get_profile(),
                    "resumes": list_resumes(),
                    "jobs": fetch_jobs(query),
                    "stats": stats(),
                    "facets": facets(),
                    "applications": app_mod.list_applications(),
                    "stage_labels": app_mod.STAGE_LABELS,
                    "tracks_def": [{"code": t["code"], "name": t["name"], "tier": t["tier"],
                                    "job_keywords": t["jobs"]}
                                   for t in __import__("tracks").TRACKS],
                    "reminders": db.rows_to_dicts(db.connect().execute(
                        "SELECT * FROM reminders WHERE done = 0 ORDER BY due_at LIMIT 50").fetchall()),
                    "runtime": runtime.runtime_report(),
                    "workers": list_workers(),
                    "tasks": MANAGER.list(30),
                    "task_types": handlers.available_types(),
                    "sources": __import__("collectors").available(),
                    "families": db.FAMILIES,
                    "statuses": db.STATUSES,
                })
                return
            if parsed.path == "/api/jobs":
                self.send_json({"jobs": fetch_jobs(query), "stats": stats(), "facets": facets()})
                return
            match = JOB_ID_RE.match(parsed.path)
            if match:
                job = get_job(int(match.group(1)))
                if job is None:
                    self.send_json({"error": "岗位不存在"}, 404)
                else:
                    self.send_json({"job": job})
                return
            match = JOB_MATCH_RE.match(parsed.path)
            if match:
                job = get_job(int(match.group(1)))
                if job is None:
                    self.send_json({"error": "岗位不存在"}, 404)
                    return
                resumes = list_resumes()
                profile = get_profile()
                result = match_mod.match_job_against_resumes(job, resumes, profile)
                self.send_json(result)
                return
            if parsed.path == "/api/applications":
                self.send_json({"applications": app_mod.list_applications(),
                                "stage_labels": app_mod.STAGE_LABELS})
                return
            if parsed.path == "/api/tasks":
                self.send_json({"tasks": MANAGER.list(int(query.get("limit", 100)),
                                                      query.get("status", ""))})
                return
            match = TASK_LOGS_RE.match(parsed.path)
            if match:
                self.send_json({"logs": MANAGER.logs(int(match.group(1)),
                                                     int(query.get("limit", 300)),
                                                     int(query.get("after_id", 0)))})
                return
            match = TASK_ID_RE.match(parsed.path)
            if match:
                task = MANAGER.get(int(match.group(1)))
                if task is None:
                    self.send_json({"error": "任务不存在"}, 404)
                else:
                    self.send_json({"task": task, "logs": MANAGER.logs(task["id"], 300)})
                return
            if parsed.path == "/api/settings":
                self.send_json({"settings": {
                    "score_weights": db.get_setting("score_weights"),
                    "rate_limit_per_min": db.get_setting("rate_limit_per_min"),
                    "ai": db.get_setting("ai"),
                    "notify": db.get_setting("notify"),
                }, "task_types": handlers.available_types()})
                return
            if parsed.path == "/api/authorizations":
                with db.connect() as conn:
                    rows = db.rows_to_dicts(conn.execute(
                        "SELECT * FROM authorizations ORDER BY id DESC").fetchall())
                self.send_json({"authorizations": rows})
                return
            if parsed.path == "/api/export.xlsx":
                jobs = fetch_jobs(query)
                target = export.export_workbook(jobs, get_profile())
                self.send_file(target, target.name)
                return
            self.send_json({"error": "未知路径"}, 404)
        except Exception:
            self.send_json({"error": traceback.format_exc()}, 500)

    # ---- POST
    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        payload = self.read_body()
        try:
            if parsed.path == "/api/profile":
                self.send_json({"profile": save_profile(payload)})
                return
            if parsed.path == "/api/resume/upload":
                self.send_json({"result": save_resume(payload)})
                return
            if parsed.path == "/api/resume/update":
                with db.connect() as conn:
                    if payload.get("is_default"):
                        conn.execute("UPDATE resumes SET is_default = 0")
                    conn.execute(
                        "UPDATE resumes SET name=?, families=?, is_default=?, archived=? WHERE id=?",
                        (payload.get("name", ""),
                         json.dumps(payload.get("families", []), ensure_ascii=False),
                         1 if payload.get("is_default") else 0,
                         1 if payload.get("archived") else 0,
                         int(payload["id"])))
                    conn.commit()
                self.send_json({"ok": True})
                return
            match = RESUME_INTENT_RE.match(parsed.path)
            if match:
                resume_id = int(match.group(1))
                with db.connect() as conn:
                    conn.execute(
                        """UPDATE resumes SET target_industries=?, target_job_types=?, target_cities=?,
                           overseas_preference=?, preferred_work_mode=?, education_level=?,
                           skills_json=?, languages_json=?, experience_summary=?, updated_at=?
                           WHERE id=?""",
                        (json.dumps(payload.get("target_industries", []), ensure_ascii=False),
                         json.dumps(payload.get("target_job_types", []), ensure_ascii=False),
                         json.dumps(payload.get("target_cities", []), ensure_ascii=False),
                         payload.get("overseas_preference", "unset"),
                         payload.get("preferred_work_mode", ""),
                         payload.get("education_level", ""),
                         json.dumps(payload.get("skills", []), ensure_ascii=False),
                         json.dumps(payload.get("languages", []), ensure_ascii=False),
                         payload.get("experience_summary", ""), db.now(), resume_id))
                    conn.commit()
                self.send_json({"ok": True, "resume_id": resume_id})
                return
            if parsed.path == "/api/applications":
                job_id = int(payload["job_id"])
                resume_id = payload.get("resume_id")
                status = payload.get("status", "not_applied")
                check = None
                if payload.get("check"):
                    job = get_job(job_id)
                    resume = next((r for r in list_resumes() if str(r["id"]) == str(resume_id)), None)
                    permission = None
                    with db.connect() as conn:
                        row = conn.execute(
                            "SELECT * FROM automation_permissions WHERE enabled=1 ORDER BY id DESC LIMIT 1"
                        ).fetchone()
                        permission = dict(row) if row else None
                    check = app_mod.pre_submit_check(job, resume, get_profile(), permission)
                    if not check["can_submit"] and payload.get("enforce"):
                        self.send_json({"ok": False, "blocked": True, "check": check}, 400)
                        return
                result = app_mod.save_application(
                    job_id, resume_id, status, payload.get("source", "user_input"),
                    payload.get("confidence", "unconfirmed"), payload.get("evidence", ""),
                    payload.get("stage", ""), payload.get("note", ""))
                self.send_json({"ok": True, "result": result, "check": check})
                return
            match = APP_EVENT_RE.match(parsed.path)
            if match:
                with db.connect() as conn:
                    conn.execute(
                        """INSERT INTO application_events (application_id, event_type, event_time,
                           source, evidence, confidence, notes) VALUES (?,?,?,?,?,?,?)""",
                        (int(match.group(1)), payload.get("event_type", "note"), db.now(),
                         payload.get("source", "user_input"), payload.get("evidence", ""),
                         payload.get("confidence", "unconfirmed"), payload.get("notes", "")))
                    conn.commit()
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/resume/delete":
                with db.connect() as conn:
                    row = conn.execute("SELECT stored_path FROM resumes WHERE id=?",
                                       (int(payload["id"]),)).fetchone()
                    if row and row["stored_path"]:
                        Path(row["stored_path"]).unlink(missing_ok=True)
                    conn.execute("DELETE FROM resumes WHERE id=?", (int(payload["id"]),))
                    conn.commit()
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/jobs/ingest":
                self.send_json({"result": ingest.upsert_jobs(payload.get("jobs") or [],
                                                             payload.get("source", ""))})
                return
            if parsed.path == "/api/jobs/import_text":
                rows = ingest.parse_pasted_table(payload.get("text", ""))
                self.send_json({"parsed": len(rows),
                                "result": ingest.upsert_jobs(rows, payload.get("source", "手工导入"))})
                return
            if parsed.path == "/api/jobs/update":
                assignments, values = [], []
                for key in ("status", "note", "resume_id", "review_status"):
                    value = payload.get(key)
                    if value is None:
                        continue
                    assignments.append(f"{key} = ?")
                    values.append(int(value) if key == "resume_id" and value != "" else value)
                if assignments:
                    values.extend([db.now(), int(payload["id"])])
                    with db.connect() as conn:
                        conn.execute(
                            f"UPDATE jobs SET {', '.join(assignments)}, updated_at=? WHERE id=?", values)
                        conn.commit()
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/jobs/delete":
                with db.connect() as conn:
                    conn.execute("DELETE FROM jobs WHERE id=?", (int(payload["id"]),))
                    conn.commit()
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/jobs/rescore":
                task_id = MANAGER.create("analyze.jobs", {}, priority=3)
                self.send_json({"ok": True, "task": MANAGER.get(task_id)})
                return
            if parsed.path == "/api/materials/save":
                items = payload.get("items") or []
                job_id = int(payload["job_id"])
                with db.connect() as conn:
                    conn.execute("DELETE FROM materials WHERE job_id=?", (job_id,))
                    for item in items:
                        if item.get("item"):
                            conn.execute(
                                "INSERT INTO materials (job_id, item, required, have, note) VALUES (?,?,?,?,?)",
                                (job_id, item["item"], 1 if item.get("required") else 0,
                                 1 if item.get("have") else 0, item.get("note", "")))
                    conn.commit()
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/collect":
                worker = payload.get("worker", "")
                result = run_worker(worker, payload.get("args") or [])
                inserted = {"inserted": 0}
                if result.get("ok") and result.get("jobs"):
                    inserted = ingest.upsert_jobs(result["jobs"], payload.get("source", worker))
                self.send_json({"result": result, "ingest": inserted})
                return
            if parsed.path == "/api/collect/run":
                import collectors
                profile = get_profile()
                source = payload.get("source") or "guopin"
                if source not in collectors.REGISTRY:
                    self.send_json({"ok": False, "error": f"未知来源：{source}"}, 400)
                    return
                keywords = payload.get("keywords") or collectors.keywords_from_profile(profile, source)
                task_id = MANAGER.create(f"collect.{source}", {
                    "keywords": keywords,
                    "cities": payload.get("cities") or profile.get("prefer_cities") or [],
                    "limit": int(payload.get("limit") or 40),
                    "reset": bool(payload.get("reset")),
                    "company": payload.get("company") or "",
                    "nationwide": bool(payload.get("nationwide", True)),
                }, priority=5)
                self.send_json({"ok": True, "task": MANAGER.get(task_id), "keywords": keywords})
                return
            if parsed.path == "/api/analyze/run":
                task_id = MANAGER.create("analyze.jobs", {}, priority=3)
                self.send_json({"ok": True, "task": MANAGER.get(task_id)})
                return
            if parsed.path == "/api/open":
                try:
                    os.startfile(payload.get("url", ""))  # type: ignore[attr-defined]
                    self.send_json({"ok": True})
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)})
                return
            if parsed.path == "/api/tasks":
                task_id = MANAGER.create(payload.get("type", ""), payload.get("payload") or {},
                                         int(payload.get("priority", 0)),
                                         payload.get("rate_limit_per_min"),
                                         int(payload.get("repeat_seconds", 0)))
                self.send_json({"ok": True, "task": MANAGER.get(task_id)})
                return
            match = TASK_ACTION_RE.match(parsed.path)
            if match:
                task_id, action = int(match.group(1)), match.group(2)
                task = {"pause": MANAGER.pause, "resume": MANAGER.resume,
                        "stop": MANAGER.stop, "retry": MANAGER.retry}[action](task_id)
                self.send_json({"ok": True, "task": task})
                return
            if parsed.path == "/api/settings":
                if payload.get("key"):
                    db.set_setting(payload["key"], payload.get("value"))
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/authorizations":
                if payload.get("action", "grant") == "grant":
                    db.grant(payload.get("scope", ""), payload.get("target", ""), payload.get("note", ""))
                else:
                    db.revoke(payload.get("scope", ""), payload.get("target", ""))
                self.send_json({"ok": True})
                return
            self.send_json({"error": "未知路径"}, 404)
        except Exception:
            self.send_json({"error": traceback.format_exc()}, 500)


def main() -> None:
    parser = argparse.ArgumentParser(description="秋招管家面板")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args()

    db.init_db()
    MANAGER.start()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"秋招管家面板已启动: http://127.0.0.1:{args.port}")
    print(f"数据目录: {db.DATA_DIR}")
    print(f"任务类型: {', '.join(MANAGER.handlers)}")
    if args.open:
        threading.Timer(0.8, lambda: os.startfile(f"http://127.0.0.1:{args.port}"))  # type: ignore
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        MANAGER.shutdown()
        print("\n已停止")


if __name__ == "__main__":
    main()
