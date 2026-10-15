"""识别引擎测试：赛道 / 销售类型 / 外派 / 应届分级 / 排除规则。"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402,F401  （它会把 app/ 加入 sys.path）
import classify  # noqa: E402
import tracks as tracks_mod  # noqa: E402

RULES = [
    {"code": "telemarketing", "name": "纯电话销售", "action": "exclude", "enabled": 1,
     "patterns": ["电话销售", "电销", "电话邀约", "外呼"]},
    {"code": "insurance_finance", "name": "保险/贷款/房产等非目标销售", "action": "exclude",
     "enabled": 1, "patterns": ["保险代理", "房产中介", "贷款专员"]},
    {"code": "needs_review_support", "name": "销售支持类需人工复核", "action": "review",
     "enabled": 1, "patterns": ["销售支持", "商务助理", "外贸跟单", "销售助理"]},
]


class OverseasTest(unittest.TestCase):
    def test_mandatory_assignment_detected_with_evidence(self) -> None:
        status, evidence = classify.detect_overseas("本岗位需常驻海外，负责当地市场开拓")
        self.assertEqual(status, "mandatory_overseas_assignment")
        self.assertIn("常驻海外", evidence)

    def test_travel_is_not_assignment(self) -> None:
        """需求文档重点：海外出差 ≠ 强制长期外派。"""
        status, _ = classify.detect_overseas("需要海外出差，参加国际展会与客户拜访")
        self.assertEqual(status, "travel_only")

    def test_unmentioned_stays_unmentioned(self) -> None:
        status, evidence = classify.detect_overseas("负责国内客户维护与订单跟进")
        self.assertEqual(status, "not_mentioned")
        self.assertEqual(evidence, "")

    def test_empty_text(self) -> None:
        self.assertEqual(classify.detect_overseas("", None)[0], "not_mentioned")


class SalesTypeTest(unittest.TestCase):
    def test_trade_assistant_is_not_non_sales(self) -> None:
        """回归测试：'俄语外贸助理' 曾被误判为非销售岗位而排除。"""
        sales_type, _ = classify.classify_sales_type("俄语外贸助理", "承担翻译与业务支持")
        self.assertEqual(sales_type, "外贸BD销售")

    def test_solution_sales_detected(self) -> None:
        self.assertEqual(classify.classify_sales_type("大客户经理", "面向企业客户提供解决方案")[0],
                         "解决方案型B端销售")

    def test_support_role_goes_to_review(self) -> None:
        sales_type, _ = classify.classify_sales_type("商务助理", "整理合同与台账")
        self.assertEqual(sales_type, "待复核")

    def test_support_role_with_sales_duty_is_kept(self) -> None:
        sales_type, _ = classify.classify_sales_type("商务助理", "协助客户开发与报价谈判")
        self.assertEqual(sales_type, "销售支持（含开发职责）")

    def test_engineer_is_non_sales(self) -> None:
        self.assertEqual(classify.classify_sales_type("电气工程师", "负责设备维护")[0], "非销售岗位")


class ExclusionTest(unittest.TestCase):
    def test_telemarketing_excluded(self) -> None:
        reason, action = classify.apply_exclusion_rules("电话销售专员", "每日拨打客户电话", RULES)
        self.assertEqual(reason, "纯电话销售")
        self.assertEqual(action, "exclude")

    def test_telemarketing_kept_when_enterprise_customers(self) -> None:
        """有企业客户与方案属性的，不算纯电销。"""
        reason, _ = classify.apply_exclusion_rules(
            "电话销售", "面向企业客户销售工业解决方案，需要技术方案沟通", RULES)
        self.assertEqual(reason, "")

    def test_support_role_marked_for_review_not_deleted(self) -> None:
        reason, action = classify.apply_exclusion_rules("外贸跟单", "负责订单跟进", RULES)
        self.assertEqual(action, "review")


class CandidateLevelTest(unittest.TestCase):
    def test_fresh_graduate(self) -> None:
        self.assertEqual(classify.classify_candidate_level("销售管培生", "面向2027届应届生")[0], "应届可投")

    def test_senior_role_downgraded(self) -> None:
        self.assertEqual(classify.classify_candidate_level("区域销售总监", "要求5年以上经验")[0],
                         "资深岗（优先级低）")

    def test_intern(self) -> None:
        self.assertEqual(classify.classify_candidate_level("市场实习生", "", job_type="实习")[0], "实习")


class TrackTest(unittest.TestCase):
    def test_track_by_seed_company(self) -> None:
        code, name, evidence = classify.classify_track("销售工程师", "", "SGS通标标准技术服务有限公司",
                                                       tracks_mod.TRACKS)
        self.assertEqual(code, "testing_cert")
        self.assertIn("公司池命中", evidence)

    def test_track_by_product_keyword(self) -> None:
        code, _, evidence = classify.classify_track("海外销售", "负责工商业储能系统海外渠道拓展", "某公司",
                                                    tracks_mod.TRACKS)
        self.assertEqual(code, "energy_storage")
        self.assertIn("产品词命中", evidence)

    def test_unknown_track_returns_empty(self) -> None:
        code, _, _ = classify.classify_track("人力资源专员", "负责招聘与考勤", "某公司", tracks_mod.TRACKS)
        self.assertEqual(code, "")


class ClassifyJobTest(unittest.TestCase):
    def test_full_classification_fields(self) -> None:
        job = {
            "title": "海外销售工程师", "company": "阳光电源股份有限公司",
            "jd_text": "负责工商业储能系统海外经销商的开发与报价谈判，需要海外出差",
            "job_type": "校招", "experience_req": "经验不限", "major_tier": 1,
            "company_nature": "民营企业",
        }
        result = classify.classify_job(job, tracks_mod.TRACKS, RULES)
        self.assertEqual(result["industry_track"], "energy_storage")
        # 海外销售工程师卖储能方案：同时属于两种类型，应多标签标注
        self.assertIn("解决方案型B端销售", result["sales_type"])
        self.assertIn("外贸BD销售", result["sales_type"])
        self.assertEqual(result["overseas_assignment_status"], "travel_only")
        self.assertEqual(result["candidate_level"], "应届可投")
        self.assertEqual(result["review_status"], "pending")
        matches = json.loads(result["match_tags_json"])
        self.assertIn("接受应届生", matches)
        self.assertIn("专业不限", matches)

    def test_mandatory_overseas_flagged_as_risk(self) -> None:
        job = {"title": "海外销售", "company": "某公司",
               "jd_text": "需常驻海外，服从海外派遣", "job_type": "校招", "major_tier": 3}
        result = classify.classify_job(job, tracks_mod.TRACKS, RULES)
        risks = json.loads(result["risk_tags_json"])
        self.assertIn("强制境外驻派", risks)


class JobFunctionTest(unittest.TestCase):
    """采购 / 供应链是用户新增的目标方向，不能被"非销售岗位"这条规则排除。"""

    def test_procurement_title_is_recognized(self) -> None:
        code, evidence = classify.classify_job_function("采购专员")
        self.assertEqual(code, "procurement")
        self.assertIn("采购", evidence)

    def test_supply_chain_title_is_recognized(self) -> None:
        for title in ["供应链专员", "国际物流专员", "生产计划员", "关务专员"]:
            self.assertEqual(classify.classify_job_function(title)[0], "supply_chain", title)

    def test_jd_mention_does_not_turn_engineer_into_buyer(self) -> None:
        """JD 里随口提到"采购"不能把技术岗判成采购岗。"""
        code, _ = classify.classify_job_function("机械设计工程师",
                                                 "负责设备采购选型与供应商对接")
        self.assertEqual(code, "")

    def test_generic_words_do_not_create_false_functions(self) -> None:
        """回归：用"物流/配送/调度"这类泛词匹配，把算法岗也判成了供应链岗。"""
        for title in ["无人配送/机器人算法工程师", "调度自动化工程师", "物流算法工程师"]:
            self.assertEqual(classify.classify_job_function(title)[0], "", title)
            self.assertEqual(classify.classify_track(title, "", "某公司", tracks_mod.TRACKS)[0],
                             "", title)

    def test_procurement_and_supply_chain_are_not_excluded(self) -> None:
        for title in ["采购专员", "供应链管理岗", "采购管培生", "物流专员"]:
            job = {"title": title, "company": "某公司", "jd_text": "负责相关事务",
                   "job_type": "校招"}
            result = classify.classify_job(job, tracks_mod.TRACKS, RULES)
            self.assertEqual(result["review_status"], "pending", title)
            self.assertNotEqual(result["sales_type"], "非销售岗位", title)

    def test_jd_body_word_does_not_trigger_exclusion(self) -> None:
        """回归：采购专员的 JD 写了"及时催收返利"，曾被"催收"这个词整条排除。"""
        rules = [{"code": "insurance_finance", "name": "保险/贷款/房产等非目标销售",
                  "action": "exclude", "enabled": 1,
                  "patterns": ["保险代理", "贷款专员", "催收", "房产中介"]}]
        job = {"title": "采购专员", "company": "国药控股某公司", "job_type": "校招",
               "jd_text": "参与供应商谈判，负责采购合同签订；确保毛利，及时催收返利。"}
        result = classify.classify_job(job, tracks_mod.TRACKS, rules)
        self.assertEqual(result["review_status"], "pending")
        self.assertEqual(result["exclusion_reason"], "")

    def test_title_still_triggers_exclusion(self) -> None:
        """标题命中排除规则时该排除还是要排除——放宽不能变成不排除。"""
        rules = [{"code": "insurance_finance", "name": "保险/贷款/房产等非目标销售",
                  "action": "exclude", "enabled": 1, "patterns": ["保险代理"]}]
        job = {"title": "保险代理人", "company": "某保险公司", "jd_text": ""}
        result = classify.classify_job(job, tracks_mod.TRACKS, rules)
        self.assertEqual(result["review_status"], "excluded")


if __name__ == "__main__":
    unittest.main(verbosity=2)
