"""SQLite 结构与访问层。"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RESUME_DIR = DATA_DIR / "resumes"
EXPORT_DIR = DATA_DIR / "exports"
# 真实库路径。写死在这里，是为了让"脚本误连真实库"这件事可以被检测出来。
REAL_DB_PATH = DATA_DIR / "qiuzhao.db"
# 允许用环境变量指向别的库（例如工具脚本、并行验证），默认仍是真实库。
DB_PATH = Path(os.environ["QIUZHAO_DB"]) if os.environ.get("QIUZHAO_DB") else REAL_DB_PATH

STATUSES = ["待投", "已填待确认", "已投", "笔试", "面试", "Offer", "已挂", "放弃"]

FAMILIES = {
    "general": "通用",
    "soe": "央国企",
    "data": "数据分析",
    "trade": "外贸",
    "semi": "半导体销售",
}


def points_at_real_db() -> bool:
    """当前 db.DB_PATH 是否指向用户真实数据。测试与验证脚本必须为 False。"""
    try:
        return Path(DB_PATH).resolve() == Path(REAL_DB_PATH).resolve()
    except OSError:
        return True

JOB_TYPES = ["校招", "实习", "社招", "不限"]

# 任务系统的状态集合
TASK_STATUSES = ["queued", "running", "paused", "stopped", "done", "failed", "interrupted"]

DEFAULT_SETTINGS: dict[str, Any] = {
    "score_weights": {"skill": 30, "degree": 15, "location": 20,
                      "experience": 10, "salary": 10, "direction": 15},
    "rate_limit_per_min": 6,
    "ai": {
        "provider": "rules",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key_env": "QIUZHAO_LLM_API_KEY",
        "timeout": 60,
    },
    "notify": {"desktop": True, "due_days": 7},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
  id          INTEGER PRIMARY KEY CHECK (id = 1),
  full_name   TEXT DEFAULT '',
  school      TEXT DEFAULT '',
  major       TEXT DEFAULT '',
  degree      TEXT DEFAULT '',
  grad_date   TEXT DEFAULT '',
  political   TEXT DEFAULT '',
  hometown    TEXT DEFAULT '',
  phone       TEXT DEFAULT '',
  email       TEXT DEFAULT '',
  prefer_cities TEXT DEFAULT '[]',
  extra_json  TEXT DEFAULT '{}',
  updated_at  TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS resumes (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,
  families    TEXT DEFAULT '[]',
  filename    TEXT DEFAULT '',
  stored_path TEXT DEFAULT '',
  size        INTEGER DEFAULT 0,
  sha256      TEXT DEFAULT '',
  parsed_json TEXT DEFAULT '{}',
  is_default  INTEGER DEFAULT 0,
  archived    INTEGER DEFAULT 0,
  uploaded_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS jobs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  dedupe_key   TEXT UNIQUE,
  company      TEXT DEFAULT '',
  title        TEXT DEFAULT '',
  city         TEXT DEFAULT '',
  source       TEXT DEFAULT '',
  family       TEXT DEFAULT 'general',
  channel_type TEXT DEFAULT 'it',
  url          TEXT DEFAULT '',
  deadline     TEXT DEFAULT '',
  major_req    TEXT DEFAULT '',
  major_tier   INTEGER DEFAULT 0,
  degree_req   TEXT DEFAULT '',
  grad_only    INTEGER DEFAULT 1,
  salary       TEXT DEFAULT '',
  match_score  INTEGER DEFAULT 0,
  filter_note  TEXT DEFAULT '',
  status       TEXT DEFAULT '待投',
  resume_id    INTEGER,
  note         TEXT DEFAULT '',
  is_active    INTEGER DEFAULT 1,
  collected_at TEXT DEFAULT '',
  updated_at   TEXT DEFAULT '',
  raw_json     TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS materials (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id    INTEGER NOT NULL,
  item      TEXT NOT NULL,
  required  INTEGER DEFAULT 1,
  have      INTEGER DEFAULT 0,
  note      TEXT DEFAULT '',
  UNIQUE(job_id, item)
);

CREATE TABLE IF NOT EXISTS events (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  ts      TEXT DEFAULT '',
  kind    TEXT DEFAULT '',
  message TEXT DEFAULT '',
  payload TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_jobs_family ON jobs(family);
CREATE INDEX IF NOT EXISTS idx_jobs_city ON jobs(city);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""

# 新增表（全部 IF NOT EXISTS，可重复执行）
SCHEMA_EXT = """
CREATE TABLE IF NOT EXISTS settings (
  key        TEXT PRIMARY KEY,
  value_json TEXT DEFAULT '{}',
  updated_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS authorizations (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  scope      TEXT NOT NULL,
  target     TEXT DEFAULT '',
  granted_at TEXT DEFAULT '',
  revoked_at TEXT DEFAULT '',
  note       TEXT DEFAULT '',
  UNIQUE(scope, target)
);

CREATE TABLE IF NOT EXISTS search_configs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL,
  keywords      TEXT DEFAULT '',
  cities        TEXT DEFAULT '',
  industries    TEXT DEFAULT '',
  job_types     TEXT DEFAULT '',
  families      TEXT DEFAULT '',
  salary_min    INTEGER DEFAULT 0,
  salary_max    INTEGER DEFAULT 0,
  degree_req    TEXT DEFAULT '',
  experience_max INTEGER DEFAULT 99,
  exclude_words TEXT DEFAULT '',
  is_active     INTEGER DEFAULT 1,
  created_at    TEXT DEFAULT '',
  updated_at    TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sources (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  code         TEXT UNIQUE,
  name         TEXT NOT NULL,
  category     TEXT DEFAULT '',
  url          TEXT DEFAULT '',
  adapter      TEXT DEFAULT '',
  family       TEXT DEFAULT 'general',
  requires_login INTEGER DEFAULT 0,
  enabled      INTEGER DEFAULT 1,
  health       TEXT DEFAULT 'unknown',
  last_run_at  TEXT DEFAULT '',
  last_error   TEXT DEFAULT '',
  note         TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tasks (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  type               TEXT NOT NULL,
  payload_json       TEXT DEFAULT '{}',
  status             TEXT DEFAULT 'queued',
  progress           INTEGER DEFAULT 0,
  total              INTEGER DEFAULT 0,
  message            TEXT DEFAULT '',
  priority           INTEGER DEFAULT 0,
  rate_limit_per_min INTEGER DEFAULT 6,
  resume_from        INTEGER DEFAULT 0,
  repeat_seconds     INTEGER DEFAULT 0,
  paused             INTEGER DEFAULT 0,
  cancel_requested   INTEGER DEFAULT 0,
  result_json        TEXT DEFAULT '{}',
  error              TEXT DEFAULT '',
  created_at         TEXT DEFAULT '',
  started_at         TEXT DEFAULT '',
  finished_at        TEXT DEFAULT '',
  next_run_at        TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS task_logs (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL,
  ts      TEXT DEFAULT '',
  level   TEXT DEFAULT 'info',
  module  TEXT DEFAULT '',
  message TEXT DEFAULT '',
  payload TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS job_analyses (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id         INTEGER NOT NULL,
  provider       TEXT DEFAULT '',
  model          TEXT DEFAULT '',
  prompt_version TEXT DEFAULT '',
  total_score    INTEGER DEFAULT 0,
  dimension_json TEXT DEFAULT '{}',
  reason_text    TEXT DEFAULT '',
  gap_text       TEXT DEFAULT '',
  risk_text      TEXT DEFAULT '',
  suggestion_text TEXT DEFAULT '',
  fact_ids_json  TEXT DEFAULT '[]',
  created_at     TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS resume_facts (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  resume_id    INTEGER NOT NULL,
  fact_id      TEXT NOT NULL,
  category     TEXT DEFAULT '',
  title        TEXT DEFAULT '',
  org          TEXT DEFAULT '',
  role         TEXT DEFAULT '',
  start_date   TEXT DEFAULT '',
  end_date     TEXT DEFAULT '',
  detail       TEXT DEFAULT '',
  keywords_json TEXT DEFAULT '[]',
  confidence   REAL DEFAULT 0.5,
  confirmed    INTEGER DEFAULT 0,
  UNIQUE(resume_id, fact_id)
);

CREATE TABLE IF NOT EXISTS tailored_resumes (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id           INTEGER,
  base_resume_id   INTEGER,
  provider         TEXT DEFAULT '',
  docx_path        TEXT DEFAULT '',
  pdf_path         TEXT DEFAULT '',
  content_json     TEXT DEFAULT '{}',
  fact_coverage    REAL DEFAULT 0,
  unverified_sentences INTEGER DEFAULT 0,
  status           TEXT DEFAULT '草稿',
  created_at       TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS applications (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id             INTEGER NOT NULL,
  resume_id          INTEGER,
  greeting_id        INTEGER,
  channel            TEXT DEFAULT '',
  status             TEXT DEFAULT '待投递',
  applied_at         TEXT DEFAULT '',
  confirmed_by_user_at TEXT DEFAULT '',
  last_action        TEXT DEFAULT '',
  note               TEXT DEFAULT '',
  UNIQUE(job_id, resume_id)
);

CREATE TABLE IF NOT EXISTS greetings (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id       INTEGER NOT NULL,
  version      INTEGER DEFAULT 1,
  text         TEXT DEFAULT '',
  provider     TEXT DEFAULT '',
  model        TEXT DEFAULT '',
  fact_ids_json TEXT DEFAULT '[]',
  is_selected  INTEGER DEFAULT 0,
  edited_by_user INTEGER DEFAULT 0,
  sent_at      TEXT DEFAULT '',
  send_mode    TEXT DEFAULT '复制',
  created_at   TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS hr_messages (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id INTEGER,
  source         TEXT DEFAULT 'manual',
  from_addr      TEXT DEFAULT '',
  subject        TEXT DEFAULT '',
  body           TEXT DEFAULT '',
  received_at    TEXT DEFAULT '',
  is_invite      INTEGER DEFAULT 0,
  read           INTEGER DEFAULT 0,
  action_required INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notifications (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  kind       TEXT DEFAULT '',
  title      TEXT DEFAULT '',
  body       TEXT DEFAULT '',
  link       TEXT DEFAULT '',
  created_at TEXT DEFAULT '',
  read       INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_task_logs_task ON task_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_facts_resume ON resume_facts(resume_id);
CREATE INDEX IF NOT EXISTS idx_analyses_job ON job_analyses(job_id);

-- 行业赛道
CREATE TABLE IF NOT EXISTS industry_tracks (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  code         TEXT UNIQUE NOT NULL,
  name         TEXT NOT NULL,
  tier         INTEGER DEFAULT 1,
  products     TEXT DEFAULT '[]',
  job_keywords TEXT DEFAULT '[]',
  customers    TEXT DEFAULT '[]',
  note         TEXT DEFAULT '',
  enabled      INTEGER DEFAULT 1,
  created_at   TEXT DEFAULT ''
);

-- 目标公司池（种子公司 + 扩展公司）
CREATE TABLE IF NOT EXISTS target_companies (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL,
  entity        TEXT DEFAULT '',
  track_code    TEXT DEFAULT '',
  company_type  TEXT DEFAULT '',
  website       TEXT DEFAULT '',
  career_url    TEXT DEFAULT '',
  source        TEXT DEFAULT '',
  verified      INTEGER DEFAULT 0,
  note          TEXT DEFAULT '',
  created_at    TEXT DEFAULT '',
  UNIQUE(name, track_code)
);

-- 排除规则（数据化，便于调整和解释）
CREATE TABLE IF NOT EXISTS exclusion_rules (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  code      TEXT UNIQUE NOT NULL,
  name      TEXT NOT NULL,
  action    TEXT DEFAULT 'exclude',
  patterns  TEXT DEFAULT '[]',
  note      TEXT DEFAULT '',
  enabled   INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_companies_track ON target_companies(track_code);

-- 岗位与简历的匹配结果（V2.0 第三章）
CREATE TABLE IF NOT EXISTS job_resume_matches (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id                INTEGER NOT NULL,
  resume_id             INTEGER NOT NULL,
  match_score           INTEGER DEFAULT 0,
  match_level           TEXT DEFAULT 'unknown',
  matched_requirements  TEXT DEFAULT '[]',
  missing_requirements  TEXT DEFAULT '[]',
  risk_tags             TEXT DEFAULT '[]',
  recommended           INTEGER DEFAULT 0,
  explanation           TEXT DEFAULT '',
  created_at            TEXT DEFAULT '',
  updated_at            TEXT DEFAULT '',
  UNIQUE(job_id, resume_id)
);

-- 申请过程事件（V2.0 第八章）
CREATE TABLE IF NOT EXISTS application_events (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id INTEGER NOT NULL,
  event_type     TEXT DEFAULT '',
  event_time     TEXT DEFAULT '',
  source         TEXT DEFAULT 'user_input',
  evidence       TEXT DEFAULT '',
  confidence     TEXT DEFAULT 'unconfirmed',
  notes          TEXT DEFAULT ''
);

-- 提醒与截止时间（V2.0 第七章）
CREATE TABLE IF NOT EXISTS reminders (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id        INTEGER,
  application_id INTEGER,
  kind          TEXT DEFAULT '',
  title         TEXT DEFAULT '',
  detail        TEXT DEFAULT '',
  due_at        TEXT DEFAULT '',
  source        TEXT DEFAULT '',
  confidence    TEXT DEFAULT 'unconfirmed',
  done          INTEGER DEFAULT 0,
  created_at    TEXT DEFAULT ''
);

-- 自动化授权范围（V2.0 第五、八章）
CREATE TABLE IF NOT EXISTS automation_permissions (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  mode           TEXT DEFAULT 'assist',
  target_tracks  TEXT DEFAULT '[]',
  target_job_types TEXT DEFAULT '[]',
  target_cities  TEXT DEFAULT '[]',
  resume_id      INTEGER,
  overseas_policy TEXT DEFAULT 'ask',
  daily_limit    INTEGER DEFAULT 5,
  enabled        INTEGER DEFAULT 0,
  granted_at     TEXT DEFAULT '',
  revoked_at     TEXT DEFAULT '',
  note           TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_matches_job ON job_resume_matches(job_id);
CREATE INDEX IF NOT EXISTS idx_app_events ON application_events(application_id);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_at);
"""

# 既有表的新增列：一次性迁移，幂等
COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "profile": [
        ("skills_json", "TEXT DEFAULT '[]'"),
        ("salary_min", "INTEGER DEFAULT 0"),
        ("salary_max", "INTEGER DEFAULT 0"),
        ("salary_unit", "TEXT DEFAULT '月'"),
        ("accept_internship", "INTEGER DEFAULT 0"),
        ("accept_travel", "INTEGER DEFAULT 0"),
        ("accept_relocate", "INTEGER DEFAULT 0"),
        ("target_families_json", "TEXT DEFAULT '[]'"),
        ("veteran", "INTEGER DEFAULT 0"),
        ("privacy_json", "TEXT DEFAULT '{}'"),
        # ---- V2.0：求职意向（需求文档第三章）----
        ("overseas_preference", "TEXT DEFAULT 'unset'"),
        ("preferred_work_mode", "TEXT DEFAULT ''"),
        ("languages_json", "TEXT DEFAULT '[]'"),
    ],
    "jobs": [
        ("jd_text", "TEXT DEFAULT ''"),
        ("salary_min", "INTEGER DEFAULT 0"),
        ("salary_max", "INTEGER DEFAULT 0"),
        ("published_at", "TEXT DEFAULT ''"),
        ("experience_req", "TEXT DEFAULT ''"),
        ("job_type", "TEXT DEFAULT '校招'"),
        ("industry", "TEXT DEFAULT ''"),
        ("company_nature", "TEXT DEFAULT ''"),
        ("company_scale", "TEXT DEFAULT ''"),
        ("is_veteran_special", "INTEGER DEFAULT 0"),
        ("source_id", "INTEGER"),
        ("priority", "INTEGER DEFAULT 0"),
        ("manual_score_override", "INTEGER"),
        ("fact_ids_json", "TEXT DEFAULT '[]'"),
        # ---- 本次扩展的标准化字段（对应需求文档第十一章）----
        ("industry_track", "TEXT DEFAULT ''"),
        ("industry_subtrack", "TEXT DEFAULT ''"),
        ("sales_type", "TEXT DEFAULT ''"),
        # 岗位职能（采购/供应链/销售/外贸/…）——与 sales_type 正交，
        # 因为用户除了销售岗还要找采购、供应链这类非销售岗位。
        ("job_function", "TEXT DEFAULT ''"),
        ("candidate_level", "TEXT DEFAULT ''"),
        ("customer_type", "TEXT DEFAULT ''"),
        ("product_or_solution", "TEXT DEFAULT ''"),
        ("language_requirement", "TEXT DEFAULT ''"),
        ("technical_requirement", "TEXT DEFAULT ''"),
        ("overseas_assignment_status", "TEXT DEFAULT 'not_mentioned'"),
        ("overseas_assignment_evidence", "TEXT DEFAULT ''"),
        ("review_status", "TEXT DEFAULT 'pending'"),
        ("review_note", "TEXT DEFAULT ''"),
        ("exclusion_reason", "TEXT DEFAULT ''"),
        ("match_tags_json", "TEXT DEFAULT '[]'"),
        ("risk_tags_json", "TEXT DEFAULT '[]'"),
        ("classify_evidence", "TEXT DEFAULT ''"),
        ("data_fetched_at", "TEXT DEFAULT ''"),
    ],
    "resumes": [
        ("target_families_note", "TEXT DEFAULT ''"),
        ("parsed_version", "TEXT DEFAULT 'v1'"),
        # ---- V2.0：简历版本与求职意向（需求文档第三章）----
        ("target_industries", "TEXT DEFAULT '[]'"),   # 目标行业赛道
        ("target_job_types", "TEXT DEFAULT '[]'"),    # 目标岗位方向
        ("target_cities", "TEXT DEFAULT '[]'"),
        ("education_level", "TEXT DEFAULT ''"),
        ("skills_json", "TEXT DEFAULT '[]'"),
        ("languages_json", "TEXT DEFAULT '[]'"),
        ("experience_summary", "TEXT DEFAULT ''"),
        ("preferred_work_mode", "TEXT DEFAULT ''"),
        ("overseas_preference", "TEXT DEFAULT 'unset'"),
        ("status", "TEXT DEFAULT 'active'"),
        ("updated_at", "TEXT DEFAULT ''"),
    ],
    "applications": [
        ("platform", "TEXT DEFAULT ''"),
        ("platform_job_id", "TEXT DEFAULT ''"),
        ("application_status", "TEXT DEFAULT 'not_applied'"),
        ("current_stage", "TEXT DEFAULT ''"),
        ("submitted_at", "TEXT DEFAULT ''"),
        ("last_checked_at", "TEXT DEFAULT ''"),
        ("status_source", "TEXT DEFAULT 'unknown'"),
        ("status_confidence", "TEXT DEFAULT 'unconfirmed'"),
        ("evidence", "TEXT DEFAULT ''"),
        ("next_action", "TEXT DEFAULT ''"),
        ("next_action_deadline", "TEXT DEFAULT ''"),
        ("created_at", "TEXT DEFAULT ''"),
        ("updated_at", "TEXT DEFAULT ''"),
    ],
    "events": [
        ("level", "TEXT DEFAULT 'info'"),
        ("module", "TEXT DEFAULT ''"),
        ("task_id", "INTEGER"),
    ],
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dirs() -> None:
    for path in (DATA_DIR, RESUME_DIR, EXPORT_DIR):
        path.mkdir(parents=True, exist_ok=True)


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.executescript(SCHEMA_EXT)
        applied = migrate_schema(conn)
        row = conn.execute("SELECT id FROM profile WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO profile (id, prefer_cities, updated_at) VALUES (1, ?, ?)",
                (json.dumps(["东营", "济南", "北京", "深圳", "广州", "成都"], ensure_ascii=False), now()),
            )
        ensure_settings(conn)
        ensure_seed_data(conn)
        conn.commit()
    if applied:
        log("migrate", f"数据层迁移：新增 {len(applied)} 个列", {"columns": applied})


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def migrate_schema(conn: sqlite3.Connection) -> list[str]:
    """幂等迁移：只做 ADD COLUMN，不删列、不改旧列名，保证用户数据不丢。"""
    applied: list[str] = []
    for table, columns in COLUMN_MIGRATIONS.items():
        existing = table_columns(conn, table)
        if not existing:
            continue
        for name, ddl in columns:
            if name in existing:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            applied.append(f"{table}.{name}")
    return applied


def ensure_settings(conn: sqlite3.Connection) -> None:
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value_json, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value, ensure_ascii=False), now()),
        )


# 排除规则（数据化，可在面板里改；action=exclude 直接排除，review 进入人工复核）
DEFAULT_EXCLUSION_RULES = [
    {"code": "telemarketing", "name": "纯电话销售", "action": "exclude",
     "patterns": ["电话销售", "电销", "电话邀约", "外呼", "话务员", "呼叫中心",
                  "每日通电话", "拨打客户电话"],
     "note": "以拨打量为核心考核、无企业客户与方案属性的岗位"},
    {"code": "consumer_goods", "name": "低门槛小商品贸易", "action": "exclude",
     "patterns": ["服装", "饰品", "日用百货", "家居小商品", "玩具批发", "鞋类",
                  "箱包批发", "铺货", "网店代运营"],
     "note": "无工业产品/技术应用的消费品贸易"},
    {"code": "pure_ops", "name": "与目标方向无关", "action": "exclude",
     "patterns": ["纯客服", "客服专员", "售后客服", "电商客服", "店铺美工",
                  "仓管员", "普工", "操作工", "司机", "保安", "保洁"],
     "note": "纯客服/纯操作类，不含客户开发与销售职责"},
    {"code": "insurance_finance", "name": "保险/贷款/房产等非目标销售", "action": "exclude",
     "patterns": ["保险代理", "保险销售", "贷款专员", "信用卡推广", "房产中介",
                  "置业顾问", "pos机", "催收"],
     "note": "用户明确排除的非目标行业销售"},
    {"code": "needs_review_support", "name": "销售支持类需人工复核", "action": "review",
     "patterns": ["销售支持", "商务助理", "外贸跟单", "跟单员", "销售助理",
                  "渠道专员", "海外运营", "内勤"],
     "note": "描述里若含客户开发/报价谈判/项目推进则保留"},
]


def ensure_seed_data(conn: sqlite3.Connection) -> None:
    """写入赛道、种子公司、排除规则（幂等）。"""
    import tracks as tracks_mod

    stamp = now()
    for rule in DEFAULT_EXCLUSION_RULES:
        conn.execute(
            """INSERT INTO exclusion_rules (code, name, action, patterns, note, enabled)
               VALUES (?,?,?,?,?,1)
               ON CONFLICT(code) DO UPDATE SET name=excluded.name, action=excluded.action,
                 patterns=excluded.patterns, note=excluded.note""",
            (rule["code"], rule["name"], rule["action"],
             json.dumps(rule["patterns"], ensure_ascii=False), rule["note"]),
        )
    for track in tracks_mod.TRACKS:
        conn.execute(
            """INSERT INTO industry_tracks (code, name, tier, products, job_keywords, customers, created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(code) DO UPDATE SET name=excluded.name, tier=excluded.tier,
                 products=excluded.products, job_keywords=excluded.job_keywords,
                 customers=excluded.customers""",
            (track["code"], track["name"], track["tier"],
             json.dumps(track["products"], ensure_ascii=False),
             json.dumps(track["jobs"], ensure_ascii=False),
             json.dumps(track["customers"], ensure_ascii=False), stamp),
        )
        for company in track["seed_companies"]:
            conn.execute(
                """INSERT OR IGNORE INTO target_companies
                   (name, entity, track_code, company_type, source, verified, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (company, "", track["code"], "种子公司", "用户提供", 1, stamp),
            )


def get_setting(key: str, default: Any = None) -> Any:
    with connect() as conn:
        row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default if default is not None else DEFAULT_SETTINGS.get(key)
    try:
        return json.loads(row["value_json"])
    except json.JSONDecodeError:
        return default


def set_setting(key: str, value: Any) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), now()),
        )
        conn.commit()


def grant(scope: str, target: str = "", note: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO authorizations (scope, target, granted_at, revoked_at, note) VALUES (?, ?, ?, '', ?) "
            "ON CONFLICT(scope, target) DO UPDATE SET granted_at = excluded.granted_at, revoked_at = '', note = excluded.note",
            (scope, target, now(), note),
        )
        conn.commit()


def revoke(scope: str, target: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE authorizations SET revoked_at = ? WHERE scope = ? AND target = ?",
            (now(), scope, target),
        )
        conn.commit()


def is_authorized(scope: str, target: str = "") -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT granted_at, revoked_at FROM authorizations WHERE scope = ? AND target = ?",
            (scope, target),
        ).fetchone()
    return bool(row and row["granted_at"] and not row["revoked_at"])


def replace_facts(resume_id: int, facts: list[dict[str, Any]]) -> int:
    """整份替换某简历的事实条目（重新解析时使用）。"""
    with connect() as conn:
        conn.execute("DELETE FROM resume_facts WHERE resume_id = ?", (int(resume_id),))
        for fact in facts:
            conn.execute(
                """INSERT INTO resume_facts (resume_id, fact_id, category, title, org, role,
                   start_date, end_date, detail, keywords_json, confidence, confirmed)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (int(resume_id), fact.get("fact_id", ""), fact.get("category", ""),
                 fact.get("title", ""), fact.get("org", ""), fact.get("role", ""),
                 fact.get("start_date", ""), fact.get("end_date", ""), fact.get("detail", ""),
                 json.dumps(fact.get("keywords", []), ensure_ascii=False),
                 float(fact.get("confidence", 0.5)), int(fact.get("confirmed", 0))),
            )
        conn.commit()
    return len(facts)


def list_facts(resume_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = rows_to_dicts(conn.execute(
            "SELECT * FROM resume_facts WHERE resume_id = ? ORDER BY fact_id", (int(resume_id),)).fetchall())
    for row in rows:
        try:
            row["keywords"] = json.loads(row.pop("keywords_json") or "[]")
        except json.JSONDecodeError:
            row["keywords"] = []
    return rows


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def log(kind: str, message: str, payload: dict[str, Any] | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO events (ts, kind, message, payload) VALUES (?, ?, ?, ?)",
            (now(), kind, message, json.dumps(payload or {}, ensure_ascii=False)),
        )
        conn.commit()


if __name__ == "__main__":
    init_db()
    print(f"初始化完成: {DB_PATH}")
