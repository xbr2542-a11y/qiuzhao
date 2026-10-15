"""岗位—简历匹配测试（V2.0 第三章）。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402,F401
import match  # noqa: E402

PROFILE = {"prefer_cities": ["北京", "济南"]}

RESUME = {
    "id": 1, "name": "外贸版",
    "education_level": "本科",
    "skills_json": '["Excel", "英语"]',
    "languages_json": '["英语"]',
    "target_industries": '["energy_storage"]',
    "target_job_types": '["海外销售", "外贸BD"]',
    "target_cities": '["北京"]',
    "overseas_preference": "no_long_term",
    "parsed_json": '{"degree": "本科", "major": "环境设计"}',
}


def make_job(**overrides) -> dict:
    job = {
        "id": 1, "title": "储能海外销售", "company": "阳光电源", "city": "北京-海淀区",
        "degree_req": "本科", "experience_req": "经验不限", "candidate_level": "应届可投",
        "industry_track": "energy_storage", "industry_subtrack": "储能/工商业光储解决方案",
        "sales_type": "外贸BD销售", "overseas_assignment_status": "travel_only",
        "language_requirement": "英语", "major_req": "", "major_tier": 1,
        "jd_text": "负责海外储能客户开发，需要英语沟通", "match_tags_json": '["外贸/国际业务"]',
    }
    job.update(overrides)
    return job


class MatchTest(unittest.TestCase):
    def test_high_match_for_aligned_job(self) -> None:
        result = match.match_resume_to_job(make_job(), RESUME, PROFILE)
        self.assertGreaterEqual(result["match_score"], 70)
        self.assertIn(result["match_level"], {"high", "medium"})
        self.assertTrue(result["matched_requirements"])
        self.assertTrue(result["explanation"])

    def test_overseas_conflict_exposes_risk(self) -> None:
        """简历偏好不接受长期外派，岗位强制驻外 → 必须有风险标签且降级。"""
        job = make_job(overseas_assignment_status="mandatory_overseas_assignment")
        result = match.match_resume_to_job(job, RESUME, PROFILE)
        self.assertTrue(any("外派冲突" in r for r in result["risk_tags"]))
        self.assertIn(result["match_level"], {"low", "unknown", "medium"})
        self.assertLess(result["match_score"], 75)

    def test_education_conflict_exposed(self) -> None:
        result = match.match_resume_to_job(make_job(degree_req="硕士"), RESUME, PROFILE)
        self.assertTrue(any("学历不符" in r for r in result["risk_tags"]))

    def test_missing_skills_are_reported_not_hidden(self) -> None:
        job = make_job(jd_text="要求熟练使用 Python、SQL、Tableau 做数据分析")
        result = match.match_resume_to_job(job, RESUME, PROFILE)
        self.assertTrue(any("未体现" in m for m in result["missing_requirements"]))

    def test_unknown_dimensions_declared(self) -> None:
        """信息不足时不能靠猜——必须报未知维度。"""
        job = make_job(degree_req="", experience_req="", candidate_level="",
                       industry_track="", industry_subtrack="", language_requirement="",
                       city="", jd_text="负责客户维护与订单跟进", match_tags_json="[]")
        result = match.match_resume_to_job(job, RESUME, PROFILE)
        self.assertIn(result["match_level"], {"unknown", "low", "medium"})
        self.assertTrue(result["unknown_dimensions"])
        self.assertGreater(len(result["unknown_dimensions"]), 3)

    def test_non_sales_job_is_flagged(self) -> None:
        result = match.match_resume_to_job(make_job(sales_type="非销售岗位"), RESUME, PROFILE)
        self.assertTrue(any("非销售" in r for r in result["risk_tags"]))

    def test_recommended_resume_is_best_of_many(self) -> None:
        other = {**RESUME, "id": 2, "name": "通用版", "target_job_types": "[]",
                 "target_industries": "[]", "overseas_preference": "accept_long_term"}
        result = match.match_job_against_resumes(make_job(), [RESUME, other], PROFILE)
        self.assertEqual(result["recommended"]["resume_name"], "外贸版")
        self.assertTrue(result["recommended"]["recommended_resume"])
        self.assertEqual(len(result["matches"]), 2)

    def test_no_resume_returns_empty(self) -> None:
        result = match.match_job_against_resumes(make_job(), [], PROFILE)
        self.assertEqual(result["matches"], [])
        self.assertIsNone(result["recommended"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
