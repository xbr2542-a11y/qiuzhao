"""S3 数据层迁移测试：在"老库"上执行迁移，验证旧数据不丢、新结构可用、可重复执行。"""

from __future__ import annotations

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
import db  # noqa: E402

# 迁移前的原始结构（与上一轮交付的 db.py 一致）
LEGACY_SCHEMA = """
CREATE TABLE profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  full_name TEXT DEFAULT '', school TEXT DEFAULT '', major TEXT DEFAULT '',
  degree TEXT DEFAULT '', grad_date TEXT DEFAULT '', political TEXT DEFAULT '',
  hometown TEXT DEFAULT '', phone TEXT DEFAULT '', email TEXT DEFAULT '',
  prefer_cities TEXT DEFAULT '[]', extra_json TEXT DEFAULT '{}', updated_at TEXT DEFAULT ''
);
CREATE TABLE resumes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, families TEXT DEFAULT '[]',
  filename TEXT DEFAULT '', stored_path TEXT DEFAULT '', size INTEGER DEFAULT 0,
  sha256 TEXT DEFAULT '', parsed_json TEXT DEFAULT '{}', is_default INTEGER DEFAULT 0,
  archived INTEGER DEFAULT 0, uploaded_at TEXT DEFAULT ''
);
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT UNIQUE, company TEXT DEFAULT '',
  title TEXT DEFAULT '', city TEXT DEFAULT '', source TEXT DEFAULT '',
  family TEXT DEFAULT 'general', channel_type TEXT DEFAULT 'it', url TEXT DEFAULT '',
  deadline TEXT DEFAULT '', major_req TEXT DEFAULT '', major_tier INTEGER DEFAULT 0,
  degree_req TEXT DEFAULT '', grad_only INTEGER DEFAULT 1, salary TEXT DEFAULT '',
  match_score INTEGER DEFAULT 0, filter_note TEXT DEFAULT '', status TEXT DEFAULT '待投',
  resume_id INTEGER, note TEXT DEFAULT '', is_active INTEGER DEFAULT 1,
  collected_at TEXT DEFAULT '', updated_at TEXT DEFAULT '', raw_json TEXT DEFAULT '{}'
);
CREATE TABLE materials (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL, item TEXT NOT NULL,
  required INTEGER DEFAULT 1, have INTEGER DEFAULT 0, note TEXT DEFAULT '',
  UNIQUE(job_id, item)
);
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT DEFAULT '', kind TEXT DEFAULT '',
  message TEXT DEFAULT '', payload TEXT DEFAULT '{}'
);
"""


class MigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = common.scratch_dir("migration")
        # 先造一个"老版本"数据库并塞入用户数据
        conn = sqlite3.connect(db.DB_PATH)
        conn.executescript(LEGACY_SCHEMA)
        conn.execute(
            "INSERT INTO profile (id, full_name, school, major, prefer_cities, updated_at) "
            "VALUES (1, '张三', '某学院', '环境设计', '[\"东营\", \"济南\"]', '2026-09-20 10:00:00')")
        conn.execute(
            "INSERT INTO jobs (dedupe_key, company, title, city, source, status) "
            "VALUES ('胜利油田|数据分析|国聘', '胜利油田', '数据分析岗', '东营', '国聘', '已投')")
        conn.execute(
            "INSERT INTO resumes (name, filename, stored_path, uploaded_at) "
            "VALUES ('通用版', 'resume.docx', 'x', '2026-09-20 10:00:00')")
        conn.execute("INSERT INTO events (ts, kind, message) VALUES ('2026-09-20 10:00:00', 'ingest', '旧日志')")
        conn.commit()
        conn.close()

    def test_migration_preserves_data_and_adds_structure(self) -> None:
        db.init_db()

        # 1) 旧数据完好
        with db.connect() as conn:
            profile = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
            job = conn.execute("SELECT * FROM jobs WHERE id = 1").fetchone()
        self.assertEqual(profile["full_name"], "张三")
        self.assertEqual(profile["major"], "环境设计")
        self.assertEqual(job["company"], "胜利油田")
        self.assertEqual(job["status"], "已投")
        self.assertEqual(common.count("SELECT COUNT(*) FROM resumes"), 1)
        # 迁移本身会追加一条审计事件，因此旧日志必须仍然存在而不是数量相等
        self.assertEqual(
            common.count("SELECT COUNT(*) FROM events WHERE message = ?", ("旧日志",)), 1)
        self.assertGreaterEqual(common.count("SELECT COUNT(*) FROM events"), 1)

        # 2) 新列已补齐
        profile_columns = common.columns("profile")
        for name in ["skills_json", "salary_min", "salary_max", "veteran",
                     "accept_travel", "privacy_json", "target_families_json"]:
            self.assertIn(name, profile_columns, f"profile 缺列 {name}")
        job_columns = common.columns("jobs")
        for name in ["jd_text", "salary_min", "job_type", "is_veteran_special",
                     "experience_req", "published_at", "manual_score_override"]:
            self.assertIn(name, job_columns, f"jobs 缺列 {name}")
        self.assertIn("level", common.columns("events"))

        # 3) 新表已建立
        existing = common.tables()
        for name in ["settings", "authorizations", "search_configs", "sources", "tasks",
                     "task_logs", "job_analyses", "resume_facts", "tailored_resumes",
                     "applications", "greetings", "hr_messages", "notifications"]:
            self.assertIn(name, existing, f"缺表 {name}")

        # 4) 默认设置已写入
        weights = db.get_setting("score_weights")
        self.assertEqual(weights["skill"], 30)
        self.assertEqual(sum(weights.values()), 100)

    def test_migration_is_idempotent(self) -> None:
        db.init_db()
        first_profile = common.columns("profile")
        first_tables = common.tables()
        db.init_db()  # 再跑一次不应报错
        db.init_db()
        self.assertEqual(first_profile, common.columns("profile"))
        self.assertEqual(first_tables, common.tables())
        self.assertEqual(common.count("SELECT COUNT(*) FROM jobs"), 1)
        self.assertEqual(common.count("SELECT COUNT(*) FROM settings"),
                         len(db.DEFAULT_SETTINGS))

    def test_authorization_grant_and_revoke(self) -> None:
        db.init_db()
        self.assertFalse(db.is_authorized("ai:analyze"))
        db.grant("ai:analyze", note="测试授权")
        self.assertTrue(db.is_authorized("ai:analyze"))
        db.revoke("ai:analyze")
        self.assertFalse(db.is_authorized("ai:analyze"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
