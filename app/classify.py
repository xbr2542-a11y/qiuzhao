"""岗位识别引擎：赛道归类、销售类型、外派状态、应届生分级、排除规则。

设计原则（来自需求文档）：
1. 不能只看岗位名称，必须结合职责与任职要求。
2. 重要判断要留下**证据文本**，方便人工复核。
3. 拿不准的标记为待复核，而不是直接删除。
4. 不做主观推断：岗位没写是否驻外，就标 not_mentioned。
"""

from __future__ import annotations

import json
import re
from typing import Any

# ---------------------------------------------------------------- 外派识别

# 注意顺序：从最重到最轻，先匹配到的就是结论
OVERSEAS_LEVELS = [
    ("mandatory_overseas_assignment", [
        "常驻海外", "长期驻外", "驻外", "外派至", "服从海外派遣", "接受长期海外驻点",
        "长期驻海外", "派驻海外", "海外常驻", "境外驻派", "外派海外",
    ]),
    ("long_term_assignment", [
        "长期外派", "海外长期", "常驻境外", "海外常驻岗位", "至少驻外",
    ]),
    ("short_term_assignment", [
        "短期外派", "海外项目现场", "驻场", "外派", "海外驻点",
    ]),
    ("travel_only", [
        "海外出差", "国际展会", "出差", "接受频繁出差", "客户拜访", "海外拜访",
        "国际差旅",
    ]),
    ("overseas_local_position", [
        "海外当地", "当地招聘", "工作地点：海外", "base海外", "base 海外",
    ]),
]


def _evidence(text: str, keyword: str, width: int = 40) -> str:
    """截取命中关键词附近的一小段原文作为证据。"""
    index = text.find(keyword)
    if index < 0:
        return ""
    start = max(0, index - width // 2)
    end = min(len(text), index + len(keyword) + width)
    return text[start:end].replace("\n", " ").strip()


def detect_overseas(*texts: str) -> tuple[str, str]:
    """返回 (外派状态, 证据原文)。"""
    blob = " ".join(t for t in texts if t)
    if not blob:
        return "not_mentioned", ""
    for status, keywords in OVERSEAS_LEVELS:
        for keyword in keywords:
            if keyword in blob:
                evidence = _evidence(blob, keyword)
                if evidence:
                    return status, f"[{keyword}] {evidence}"
    return "not_mentioned", ""


# ---------------------------------------------------------------- 销售类型

SALES_PATTERNS = [
    ("解决方案型B端销售", [
        "解决方案销售", "解决方案", "大客户", "KA", "企业客户", "行业销售", "工业品销售",
        "技术销售", "项目型销售", "渠道销售", "客户经理", "工业客户", "商务拓展",
        "BD", "行业客户", "售前商务", "系统集成", "经销商", "销售工程师", "设备销售",
        "系统销售", "项目销售", "客户开发", "客户拓展",
        # 注意：不要放"方案""报价""选型"这种泛词——建筑设计/研发类 JD 里到处都是，
        # 会把技术岗误判成销售岗（已踩过坑）。
    ]),
    ("外贸BD销售", [
        # 注意："外贸" 要单独列出来——否则"俄语外贸助理"这类岗位会被判成非销售而误杀
        "外贸", "国际贸易", "海外销售", "国际销售", "国际业务", "海外市场",
        "出口业务", "export", "overseas", "international sales", "business development",
        "海外商务", "外贸BD", "海外客户开发", "跨境", "海外", "出口", "外销",
    ]),
]

REVIEW_SALES_PATTERNS = [
    "销售支持", "商务助理", "销售助理", "外贸跟单", "跟单员", "渠道专员",
    "海外运营", "内勤", "单证员",
]

# 明显的技术/职能角色词：标题里出现这些词、又没有明确销售词时，判定为非销售
TECHNICAL_ROLE_WORDS = [
    "设计师", "工程师", "技术员", "研发", "开发", "测试", "运维", "算法", "管理员",
    "操作工", "操作员", "检验员", "检测员", "维护", "维修", "监理", "预算", "会计",
    "出纳", "审计", "护士", "医生", "药师", "教师", "讲师", "文员", "前台", "司机",
    "保安", "普工", "厨师", "店长", "导购", "收银",
]
SALES_WORDS_IN_TITLE = ["销售", "业务", "客户", "商务", "BD", "渠道", "拓展", "招商", "外贸"]

SALES_DUTY_HINTS = [
    "客户开发", "开发客户", "报价", "谈判", "商务谈判", "项目推进", "销售目标",
    "客户维护", "签单", "订单", "渠道拓展", "客户关系", "业绩",
]


def classify_sales_type(title: str, jd_text: str = "") -> tuple[str, str]:
    """判定销售类型，支持多标签。

    一个岗位可能同时是"外贸BD销售"和"解决方案型B端销售"（例如海外销售工程师
    卖储能方案），所以返回用 " + " 连接的多标签，而不是只取一个。
    """
    blob = f"{title} {jd_text}"
    lower = blob.lower()

    # 1) 支持类岗位：要看职责里有没有真正的销售动作，否则进复核
    for keyword in REVIEW_SALES_PATTERNS:
        if keyword in title:
            has_duty = any(hint in blob for hint in SALES_DUTY_HINTS)
            if has_duty:
                return "销售支持（含开发职责）", f"[{keyword}] 职责含客户开发/报价/谈判等销售动作"
            return "待复核", f"[{keyword}] 名称属支持类，未见明确销售职责"

    # 2) 正常销售岗位
    # 先排除明显不是销售的技术/职能岗（标题判断，避免 JD 里的泛词造成误判）
    if any(word in title for word in TECHNICAL_ROLE_WORDS) \
            and not any(word in title for word in SALES_WORDS_IN_TITLE):
        return "非销售岗位", "标题为技术/职能角色，且无销售类关键词"

    matched: list[str] = []
    hits_detail: list[str] = []
    for label, keywords in SALES_PATTERNS:
        hit = next((k for k in keywords if k in blob or k.lower() in lower), "")
        if hit:
            matched.append(label)
            hits_detail.append(f"{label}←[{hit}]")
    if matched:
        return " + ".join(matched), "、".join(hits_detail)

    # 3) 只写了"销售/业务"但没有方向
    if any(word in title for word in ["销售", "业务", "客户", "商务", "BD", "市场", "渠道"]):
        return "一般销售（待确认方向）", "名称含销售/业务，但未见行业或客户类型描述"
    return "非销售岗位", ""


# ---------------------------------------------------------------- 岗位职能
# 销售类型只回答"这是不是销售岗"，但用户还要找采购 / 供应链这类非销售岗位。
# 所以再加一个正交的职能维度：同一个岗位既有 sales_type，也有 job_function。

JOB_FUNCTIONS: list[tuple[str, str, list[str]]] = [
    ("procurement", "采购/寻源", [
        "采购", "寻源", "sourcing", "procurement", "buyer", "供应商开发", "供应商管理",
        "招标采购", "采购成本",
    ]),
    ("supply_chain", "供应链/物流/计划", [
        # 只用能确定职能的词。曾经用"物流/配送/调度"，结果
        # 「无人配送/机器人算法工程师」「调度自动化工程师」都被算成供应链岗。
        "供应链", "物流管理", "物流专员", "物流运营", "国际物流", "仓储管理", "库存管理",
        "生产计划", "物料计划", "计划员", "关务", "报关", "单证", "船务", "仓管",
    ]),
]

FUNCTION_LABELS = {"procurement": "采购/寻源", "supply_chain": "供应链/物流与计划",
                   "sales": "销售/商务", "trade": "外贸/国际业务", "data": "数据分析",
                   "tech": "技术/工程", "other": "其他"}

# 采购/供应链是我们新增的目标方向，不能被"非销售岗位"这条规则误排除
TARGET_FUNCTIONS = {"procurement", "supply_chain"}


def classify_job_function(title: str, jd_text: str = "") -> tuple[str, str]:
    """返回 (职能代码, 证据)。

    只看标题：JD 里出现"采购""物流"这类词的岗位太多了，按全文判断会大面积误伤。
    """
    head = (title or "").lower()
    for code, label, words in JOB_FUNCTIONS:
        hit = next((w for w in words if w in head), "")
        if hit:
            return code, f"[{hit}] 标题命中{label}"
    return "", ""


def normalize_function(code: str, sales_type: str, family: str) -> str:
    """把职能归到一个前端可筛选的粗粒度值上。"""
    if code:
        return code
    if sales_type and sales_type != "非销售岗位":
        return "trade" if "外贸" in sales_type else "sales"
    if family == "data":
        return "data"
    return "other"


# ---------------------------------------------------------------- 排除规则

# 默认所有排除规则都只看"标题"：职位名称才是"这是什么岗"的可靠信号。
# JD 正文里出现某个词，往往只是顺带提一句——实测踩过的坑：
# 「采购专员」的 JD 写了"及时催收返利"，命中"催收"，于是被
# 「保险/贷款/房产等非目标销售」这条规则整条排除掉了。
# 只有电话销售这类必须看职责才能判断的规则，才允许扫全文。
JD_SCOPE_RULES = {"telemarketing"}


def apply_exclusion_rules(title: str, jd_text: str, rules: list[dict]) -> tuple[str, str]:
    """返回 (排除原因, 处置)。处置为空表示保留。"""
    blob = f"{title} {jd_text}"
    for rule in rules:
        if not rule.get("enabled", 1):
            continue
        patterns = rule.get("patterns") or []
        scope = blob if rule["code"] in JD_SCOPE_RULES else title
        hits = [p for p in patterns if p in scope]
        if not hits:
            continue
        # 电话销售：只有"存在电话销售特征且没有企业客户/方案特征"才算
        if rule["code"] == "telemarketing":
            if any(k in blob for k in ["企业客户", "解决方案", "工业", "大客户", "项目", "技术方案"]):
                continue
        return rule["name"], rule["action"]
    return "", ""


# ---------------------------------------------------------------- 应届生分级

SENIOR_HINTS = ["销售总监", "销售经理", "区域经理", "大区经理", "资深", "高级销售",
                "5年以上", "3年以上", "五年以上", "三年以上", "要求客户资源",
                "自带客户资源", "成熟客户"]
FRESH_HINTS = ["应届", "校招", "秋招", "2026届", "2027届", "2028届", "经验不限",
               "不限经验", "管培生", "培训生", "实习生", "0-1年", "1年以内"]


def classify_candidate_level(title: str, jd_text: str, job_type: str = "",
                             experience_req: str = "") -> tuple[str, str]:
    blob = f"{title} {jd_text} {experience_req}"
    if job_type == "实习" or "实习" in title:
        return "实习", "岗位类型为实习"
    for hint in FRESH_HINTS:
        if hint in blob:
            return "应届可投", f"[{hint}]"
    for hint in SENIOR_HINTS:
        if hint in blob:
            return "资深岗（优先级低）", f"[{hint}]"
    if "1-3年" in blob:
        return "初级（1-3年）", "[1-3年]"
    return "未标明", ""


# ---------------------------------------------------------------- 赛道归类

def classify_track(title: str, jd_text: str, company: str,
                   tracks: list[dict]) -> tuple[str, str, str]:
    """返回 (赛道代码, 赛道名, 证据)。先看公司池，再看产品词。"""
    company_lower = (company or "").lower()
    # 1) 公司池匹配（种子公司）
    for track in tracks:
        for seed in track.get("seed_companies", []):
            token = re.split(r"[A-Za-z]", seed)[0] or seed
            token = token.strip()
            if len(token) >= 2 and (token in company or seed.lower() in company_lower):
                return track["code"], track["name"], f"公司池命中：{seed}"
    # 2) 产品词匹配
    #    title_only 的赛道（采购/供应链这类职能型赛道）只在标题里找词：
    #    JD 里随口提到"采购""供应商"太常见，放在全文匹配会把技术岗误判成采购岗。
    blob = f"{title} {jd_text}"
    best: tuple[int, str, str, str] = (0, "", "", "")
    for track in tracks:
        scope = title if track.get("title_only") else blob
        hits = [p for p in track["products"] if p in scope]
        if len(hits) > best[0]:
            best = (len(hits), track["code"], track["name"], "、".join(hits[:3]))
    if best[0] > 0:
        return best[1], best[2], f"产品词命中：{best[3]}"
    return "", "", ""


# ---------------------------------------------------------------- 其他字段

LANGUAGE_HINTS = ["英语", "四级", "六级", "CET", "雅思", "托福", "日语", "韩语",
                  "俄语", "西班牙语", "法语", "德语", "阿拉伯语", "葡萄牙语"]


def extract_language_requirement(text: str) -> str:
    hits = [word for word in LANGUAGE_HINTS if word in (text or "")]
    return "、".join(dict.fromkeys(hits))[:120]


def build_tags(job: dict, sales_type: str, track: str, overseas: str,
               level: str) -> tuple[list[str], list[str]]:
    """匹配标签与风险标签（需求文档第 13 章：评分必须可解释）。"""
    matches: list[str] = []
    risks: list[str] = []
    if sales_type.startswith("解决方案型"):
        matches.append("B端解决方案销售")
    if sales_type.startswith("外贸"):
        matches.append("外贸/国际业务")
    if track:
        matches.append(f"赛道：{track}")
    if level == "应届可投":
        matches.append("接受应届生")
    if level == "实习":
        matches.append("实习岗")
    if job.get("company_nature") in {"民营企业", "国企", "国有企业", "股份制企业", "上市公司"}:
        matches.append(f"单位性质：{job['company_nature']}")
    if job.get("major_tier") == 1:
        matches.append("专业不限")

    if overseas == "mandatory_overseas_assignment":
        risks.append("强制境外驻派")
    elif overseas == "long_term_assignment":
        risks.append("长期外派")
    elif overseas == "short_term_assignment":
        risks.append("短期外派")
    if level == "资深岗（优先级低）":
        risks.append("偏资深岗")
    if job.get("major_tier") == 3:
        risks.append("专业要求不对口")
    return matches, risks


def classify_job(job: dict, tracks: list[dict], rules: list[dict]) -> dict:
    """对单个岗位做全套识别，返回要写库的字段。"""
    title = job.get("title") or ""
    jd_text = job.get("jd_text") or ""
    company = job.get("company") or ""

    # 单位级投递入口（如《央国企投递清单》导入的"单位+招聘入口"）：
    # 它们不是具体岗位，不能按岗位规则排除，否则用户会看不到这些入口。
    if (job.get("channel_type") or "") == "unit":
        track_code, track_name, track_evidence = classify_track(title, jd_text, company, tracks)
        return {
            "industry_track": track_code,
            "industry_subtrack": track_name,
            "sales_type": "单位投递入口",
            "job_function": "other",
            "candidate_level": "应届可投",
            "overseas_assignment_status": detect_overseas(title, jd_text,
                                                          job.get("experience_req") or "")[0],
            "overseas_assignment_evidence": "",
            "review_status": "pending",
            "exclusion_reason": "",
            "match_tags_json": json.dumps(["单位级投递入口", "需自行网申"], ensure_ascii=False),
            "risk_tags_json": "[]",
            "language_requirement": "",
            "classify_evidence": f"单位投递入口：{track_evidence or '按单位归入渠道池'}",
        }

    track_code, track_name, track_evidence = classify_track(title, jd_text, company, tracks)
    sales_type, sales_evidence = classify_sales_type(title, jd_text)
    function_code, function_evidence = classify_job_function(title, jd_text)
    job_family = job.get("family") or ""
    job_function = normalize_function(function_code, sales_type, job_family)
    if function_code in TARGET_FUNCTIONS:
        # 采购/供应链岗：销售类型一栏改成可读的职能名，而不是"非销售岗位"
        sales_type = "采购/供应链岗"
    overseas, overseas_evidence = detect_overseas(title, jd_text, job.get("experience_req") or "")
    level, level_evidence = classify_candidate_level(
        title, jd_text, job.get("job_type") or "", job.get("experience_req") or "")
    exclude_name, action = apply_exclusion_rules(title, jd_text, rules)
    matches, risks = build_tags(job, sales_type, track_name, overseas, level)
    if function_code in TARGET_FUNCTIONS:
        matches.append(f"职能：{FUNCTION_LABELS[job_function]}")

    evidence_parts = [p for p in [track_evidence, sales_evidence, function_evidence,
                                  level_evidence] if p]
    review_status = "pending"
    if exclude_name:
        review_status = "excluded" if action == "exclude" else "need_review"
        if action == "exclude":
            risks.append(f"已排除：{exclude_name}")
    elif function_code in TARGET_FUNCTIONS:
        review_status = "pending"      # 采购/供应链是新增目标方向，不按"非销售"排除
    elif sales_type == "待复核":
        review_status = "need_review"
    elif not track_code and sales_type not in {"非销售岗位"}:
        review_status = "need_review"   # 赛道不明，交人工判断
    elif sales_type == "非销售岗位":
        review_status = "excluded"

    return {
        "industry_track": track_code,
        "industry_subtrack": track_name,
        "sales_type": sales_type,
        "job_function": job_function,
        "candidate_level": level,
        "overseas_assignment_status": overseas,
        "overseas_assignment_evidence": overseas_evidence[:300],
        "review_status": review_status,
        "exclusion_reason": exclude_name,
        "match_tags_json": json.dumps(matches, ensure_ascii=False),
        "risk_tags_json": json.dumps(risks, ensure_ascii=False),
        "language_requirement": extract_language_requirement(f"{title} {jd_text}"),
        "classify_evidence": "；".join(evidence_parts)[:500],
    }
