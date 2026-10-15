"""投递记录、状态识别、重复检测、提交前检查测试（V2.0 第二、三、五章）。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
import applications as A  # noqa: E402
import ingest  # noqa: E402


class ApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        common.fresh_db("applications")
        ingest.save_profile({"full_name": "测试", "phone": "13800000000",
                             "email": "a@b.com", "school": "某学院", "major": "环境设计"})
        ingest.upsert_jobs([
            {"company": "阳光电源", "title": "储能海外销售", "city": "北京-海淀区",
             "source": "国聘", "url": "https://example.com/job/1001",
             "deadline": "2030-01-01"},
        ])
        with A.db.connect() as conn:
            self.job_id = conn.execute("SELECT id FROM jobs").fetchone()["id"]

    def job(self) -> dict:
        with A.db.connect() as conn:
            return dict(conn.execute("SELECT * FROM jobs WHERE id=?", (self.job_id,)).fetchone())

    def test_cannot_mark_submitted_without_evidence(self) -> None:
        """需求文档第一条铁律：点按钮不能算投递成功。"""
        with self.assertRaises(ValueError):
            A.save_application(self.job_id, None, "submitted", "user_input", "unconfirmed",
                               "用户点击了投递按钮")
        self.assertEqual(common.count("SELECT COUNT(*) FROM applications"), 0)

    def test_unknown_state_goes_to_pending_verification(self) -> None:
        result = A.save_application(self.job_id, None, "pending_verification", "unknown",
                                    "unconfirmed", "页面状态无法确认")
        self.assertEqual(result["stage_label"], "状态待确认")
        row = A.list_applications()[0]
        self.assertEqual(row["status_confidence"], "unconfirmed")
        self.assertEqual(row["status_source"], "unknown")

    def test_confirmed_submission_records_evidence_and_time(self) -> None:
        A.save_application(self.job_id, None, "submitted", "official", "confirmed",
                           "平台返回申请编号 APP-001")
        row = A.list_applications()[0]
        self.assertEqual(row["application_status"], "submitted")
        self.assertEqual(row["status_confidence"], "confirmed")
        self.assertIn("APP-001", row["evidence"])
        self.assertTrue(row["submitted_at"])
        # 岗位状态同步
        self.assertEqual(self.job()["status"], "已投递")

    def test_failure_state_records_reason(self) -> None:
        A.save_application(self.job_id, None, "failed", "official", "confirmed",
                           "必填信息缺失：未填写实习经历")
        self.assertEqual(A.list_applications()[0]["application_status"], "failed")

    def test_duplicate_detection_by_job_and_url(self) -> None:
        self.assertFalse(A.duplicate_check(self.job_id)["duplicate"])
        A.save_application(self.job_id, None, "submitted", "official", "confirmed", "已提交")
        dup = A.duplicate_check(self.job_id)
        self.assertTrue(dup["duplicate"])
        self.assertEqual(dup["level"], "confirmed")

    def test_duplicate_detection_ignores_different_positions_in_same_company(self) -> None:
        """同一公司不同岗位不算重复（需求文档明确要求）。"""
        A.save_application(self.job_id, None, "submitted", "official", "confirmed", "已提交")
        ingest.upsert_jobs([{"company": "阳光电源", "title": "储能结构工程师",
                             "city": "北京-海淀区", "source": "国聘",
                             "url": "https://example.com/job/1002"}])
        with A.db.connect() as conn:
            other_id = conn.execute("SELECT id FROM jobs WHERE title='储能结构工程师'").fetchone()["id"]
        self.assertFalse(A.duplicate_check(other_id)["duplicate"])

    def test_pre_submit_check_blocks_overseas_conflict(self) -> None:
        with A.db.connect() as conn:
            conn.execute("UPDATE jobs SET overseas_assignment_status='mandatory_overseas_assignment' "
                         "WHERE id=?", (self.job_id,))
            conn.commit()
        resume = {"id": 1, "overseas_preference": "no_long_term", "status": "active"}
        check = A.pre_submit_check(self.job(), resume, ingest.get_profile(), None)
        self.assertFalse(check["can_submit"])
        self.assertTrue(any("外派" in b for b in check["blockers"]))

    def test_pre_submit_check_blocks_out_of_scope_track(self) -> None:
        permission = {"enabled": 1, "target_tracks": '["energy_storage"]',
                      "target_cities": '["上海"]'}
        resume = {"id": 1, "overseas_preference": "accept_travel", "status": "active"}
        check = A.pre_submit_check(self.job(), resume, ingest.get_profile(), permission)
        self.assertFalse(check["can_submit"])
        self.assertTrue(any("超出授权" in b for b in check["blockers"]))

    def test_pre_submit_check_blocks_expired_job(self) -> None:
        with A.db.connect() as conn:
            conn.execute("UPDATE jobs SET deadline='2020-01-01' WHERE id=?", (self.job_id,))
            conn.commit()
        resume = {"id": 1, "overseas_preference": "unset", "status": "active"}
        check = A.pre_submit_check(self.job(), resume, ingest.get_profile(), None)
        self.assertFalse(check["can_submit"])
        self.assertTrue(any("截止" in b for b in check["blockers"]))

    def test_events_timeline_records_source_and_confidence(self) -> None:
        result = A.save_application(self.job_id, None, "pending_verification", "manual_review",
                                    "unconfirmed", "人工确认中")
        A.save_application(self.job_id, None, "interview_invited", "email", "confirmed",
                           "收到面试邀请邮件")
        events = A.application_events(result["application_id"])
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_type"], "interview_invited")
        self.assertEqual(events[0]["source"], "email")
        self.assertEqual(events[0]["confidence"], "confirmed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
