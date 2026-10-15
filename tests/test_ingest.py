"""采集入库测试：把采集器返回的完整字段写进库，防止"代码写了列、迁移没加列"这类错误。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
import ingest  # noqa: E402
import collectors  # noqa: E402


class IngestTest(unittest.TestCase):
    def setUp(self) -> None:
        common.fresh_db("ingest")
        ingest.save_profile({"major": "环境设计", "degree": "本科",
                             "prefer_cities": ["东营", "济南"]})

    def test_collected_job_with_all_fields_is_stored(self) -> None:
        """国聘采集器会带回 jd_text / industry / experience_req 等字段，必须能落库。"""
        sample = {
            "company": "中国移动通信有限公司在线营销服务中心",
            "title": "数据分析",
            "city": "济南-槐荫区",
            "source": "国聘",
            "url": "https://www.iguopin.com/job/detail?id=1",
            "salary": "面议",
            "salary_min": 0,
            "salary_max": 0,
            "major_req": "计算机科学与技术、大数据相关专业",
            "degree_req": "本科",
            "experience_req": "经验不限",
            "job_type": "校招",
            "industry": "软件和信息技术服务业",
            "company_nature": "国企",
            "published_at": "2026-09-02 21:18:12",
            "deadline": "2026-10-15",
            "jd_text": "工作职责：……任职要求：本科及以上学历。",
            "family": "soe",
            "raw": {"job_id": "1"},
        }
        result = ingest.upsert_jobs([sample], "guopin")
        self.assertEqual(result["inserted"], 1)

        with ingest.db.connect() as conn:
            row = dict(conn.execute("SELECT * FROM jobs WHERE dedupe_key != ''").fetchone())
        self.assertEqual(row["industry"], "软件和信息技术服务业")
        self.assertEqual(row["experience_req"], "经验不限")
        self.assertEqual(row["job_type"], "校招")
        self.assertEqual(row["deadline"], "2026-10-15")
        self.assertTrue(row["jd_text"])
        self.assertEqual(row["major_tier"], 3, "环境设计不该命中计算机类专业要求")

    def test_duplicate_is_updated_not_repeated(self) -> None:
        job = {"company": "某国企", "title": "综合管理", "city": "济南", "source": "国聘"}
        ingest.upsert_jobs([job], "guopin")
        second = ingest.upsert_jobs([{**job, "salary": "8-12K"}], "guopin")
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["updated"], 1)
        self.assertEqual(common.count("SELECT COUNT(*) FROM jobs"), 1)

    def test_ingest_classifies_on_the_spot(self) -> None:
        """新采集的岗位必须当场完成识别，否则在筛选里等于"看不见"。"""
        ingest.upsert_jobs([{"company": "某国企", "title": "采购专员", "city": "东营",
                             "source": "国聘", "jd_text": "负责物资采购与供应商管理"}], "guopin")
        with ingest.db.connect() as conn:
            row = dict(conn.execute("SELECT * FROM jobs").fetchone())
        self.assertEqual(row["job_function"], "procurement")
        self.assertEqual(row["review_status"], "pending")
        self.assertEqual(row["sales_type"], "采购/供应链岗")
        self.assertTrue(row["classify_evidence"])

    def test_update_does_not_reset_user_status(self) -> None:
        """重新采集同一岗位时，用户手动改过的投递状态不能被覆盖回「待投」。"""
        job = {"company": "某国企", "title": "采购专员", "city": "济南", "source": "国聘"}
        ingest.upsert_jobs([job], "guopin")
        with ingest.db.connect() as conn:
            conn.execute("UPDATE jobs SET status = '已投递'")
            conn.commit()
        ingest.upsert_jobs([{**job, "salary": "9-12K"}], "guopin")
        with ingest.db.connect() as conn:
            status = conn.execute("SELECT status FROM jobs").fetchone()["status"]
        self.assertEqual(status, "已投递")

    def test_update_path_writes_all_mutable_fields(self) -> None:
        """回归测试：更新已有岗位时，note / channel_type 曾经被静默丢弃。"""
        job = {"company": "某单位", "title": "2027届校园招聘", "city": "济南",
               "source": "央国企投递清单", "channel_type": "unit", "note": "单位类型：央企"}
        ingest.upsert_jobs([job], "央国企投递清单")
        # 再跑一次（导入同一份表格的场景），字段必须被正确写回
        ingest.upsert_jobs([{**job, "note": "单位类型：央企；板块：电力"}], "央国企投递清单")
        with ingest.db.connect() as conn:
            row = dict(conn.execute("SELECT note, channel_type FROM jobs").fetchone())
        self.assertIn("电力", row["note"])
        self.assertEqual(row["channel_type"], "unit")

    def test_unit_channel_is_not_excluded_by_classifier(self) -> None:
        """单位级投递入口不该被当成"非销售岗位"排除掉。"""
        import classify
        import tracks as tracks_mod
        job = {"company": "某央企", "title": "2027届校园招聘（综合管理）",
               "channel_type": "unit", "jd_text": ""}
        result = classify.classify_job(job, tracks_mod.TRACKS, [])
        self.assertEqual(result["review_status"], "pending")
        self.assertEqual(result["sales_type"], "单位投递入口")

    def test_keyword_derivation_skips_useless_tokens(self) -> None:
        profile = {"extra": {"job_intent": "数据分析 / ToB 半导体销售及半导体相关行业 / 外贸 / 环境设计"}}
        keywords = collectors.keywords_from_profile(profile)
        # 对口方向必须在
        for expected in ["数据分析", "半导体", "外贸"]:
            self.assertIn(expected, keywords)
        # 用户要求不限职业、把池子做大：通用岗位关键词也要在
        for expected in ["管培生", "销售", "采购", "财务"]:
            self.assertIn(expected, keywords)
        # 无意义词元和用户明确不要的方向必须被剔除
        for bad in ["ToB", "环境设计"]:
            self.assertNotIn(bad, keywords, f"不该把 {bad} 当搜索词")

    def test_relevance_filter_drops_unrelated_jobs(self) -> None:
        unrelated = {"title": "整车软件集成测试岗", "jd_text": "负责整车软件集成测试",
                     "major_req": ""}
        related = {"title": "数据分析工程师", "jd_text": "负责数据分析建模", "major_req": ""}
        self.assertFalse(collectors.guopin.is_relevant(unrelated, "数据分析"))
        self.assertTrue(collectors.guopin.is_relevant(related, "数据分析"))

    def test_clear_source_only_removes_that_source(self) -> None:
        """回归测试：曾经用 LIKE '%官网' 清空，把美团官网的岗位一起删了（数据事故）。"""
        ingest.upsert_jobs([
            {"company": "某国企", "title": "综合管理", "city": "济南", "source": "国聘"},
            {"company": "美团", "title": "数据分析实习生", "city": "北京", "source": "美团校招官网"},
            {"company": "恩智浦", "title": "Sales Intern", "city": "上海", "source": "恩智浦 NXP官网"},
        ])
        self.assertEqual(common.count("SELECT COUNT(*) FROM jobs"), 3)

        removed = ingest.clear_source(["24365国家平台"])
        self.assertEqual(removed, 0, "清空 24365 不该影响任何岗位")
        self.assertEqual(common.count("SELECT COUNT(*) FROM jobs"), 3)

        removed = ingest.clear_source(["美团校招官网"])
        self.assertEqual(removed, 1)
        self.assertEqual(common.count("SELECT COUNT(*) FROM jobs WHERE source = '恩智浦 NXP官网'"), 1,
                         "清空美团不该动到外企官网")
        self.assertEqual(common.count("SELECT COUNT(*) FROM jobs WHERE source = '国聘'"), 1)

    def test_clear_source_rejects_wildcards(self) -> None:
        with self.assertRaises(ValueError):
            ingest.clear_source(["%官网"])
        with self.assertRaises(ValueError):
            ingest.clear_source([])


if __name__ == "__main__":
    unittest.main(verbosity=2)
