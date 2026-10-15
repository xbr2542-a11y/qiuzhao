"""岗位—简历匹配引擎（V2.0 第三章）。

十条匹配维度，每条都输出依据，不允许只给一个分数。
硬规则（来自需求文档）：
1. 不得虚构简历里没有的信息——缺失就是"未知"，不是"符合"。
2. 明显冲突（学历不够、外派不接受、专业不符）必须作为风险暴露，不得隐藏。
3. 分数高 ≠ 一定合适，必须能查看依据。
"""

from __future__ import annotations

import json
import re
from typing import Any

# 各维度权重（合计 100）
WEIGHTS = {
    "skill": 18, "education": 12, "experience": 10, "industry": 16, "job_type": 14,
    "product": 8, "language": 6, "location": 10, "overseas": 6,
}

SKILL_VOCAB = ["Excel", "SQL", "Python", "Tableau", "Power BI", "SPSS", "SAS", "R",
               "Word", "PowerPoint", "PPT", "CAD", "SketchUp", "V-Ray", "Photoshop",
               "Office", "WPS", "AI", "ChatGPT", "DeepSeek", "数据分析", "数据可视化",
               "英语", "CET-4", "CET-6", "雅思", "托福", "日语", "韩语", "俄语",
               "西班牙语", "法语", "德语", "阿拉伯语"]

JOB_TYPE_ALIASES = {
    "B端解决方案销售": ["解决方案型B端销售", "销售支持（含开发职责）"],
    "工业品销售": ["解决方案型B端销售"],
    "海外销售": ["外贸BD销售"],
    "外贸BD": ["外贸BD销售"],
    "国际业务": ["外贸BD销售"],
    "技术销售": ["解决方案型B端销售"],
    "项目型销售": ["解决方案型B端销售"],
    "商务拓展": ["解决方案型B端销售"],
}

# 职能型岗位（采购 / 供应链）的匹配词：简历"目标岗位方向"里出现其中一个就算命中。
# 用户上传了「国际采购」简历，这条就是为它准备的。
FUNCTION_LABELS = {"procurement": "采购/寻源", "supply_chain": "供应链/物流与计划"}
FUNCTION_TARGET_WORDS = {
    "procurement": ["采购", "寻源", "供应商", "sourcing", "procurement"],
    "supply_chain": ["供应链", "物流", "仓储", "库存", "计划", "关务", "报关", "单证"],
}

DEGREE_RANK = {"中专": 1, "中技": 1, "高中": 1, "大专": 2, "专科": 2,
               "本科": 3, "学士": 3, "硕士": 4, "研究生": 4, "博士": 5}

OVERSEAS_ACCEPT = {
    "accept_long_term": {"mandatory_overseas_assignment", "long_term_assignment",
                         "short_term_assignment", "travel_only", "not_mentioned"},
    "accept_short_term": {"short_term_assignment", "travel_only", "not_mentioned"},
    "accept_travel": {"travel_only", "not_mentioned"},
    "no_long_term": {"travel_only", "short_term_assignment", "not_mentioned"},
    "decline_all": {"not_mentioned"},
    "ask": set(),      # 逐岗确认：不算冲突
    "unset": set(),    # 未设置：不算冲突
}

OVERSEAS_LABELS = {
    "not_mentioned": "未提及外派", "travel_only": "仅海外出差",
    "short_term_assignment": "短期外派", "long_term_assignment": "长期外派",
    "mandatory_overseas_assignment": "强制境外驻派", "overseas_local_position": "海外当地岗位",
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
        return [v.strip() for v in str(value).replace("、", ",").split(",") if v.strip()]


def resume_view(resume: dict) -> dict:
    """把简历记录整理成匹配用的视图（含解析结果兜底）。"""
    # 传进来的可能是 API 层整理过的（parsed 是 dict），也可能是原始行（parsed_json 是字符串）
    parsed = resume.get("parsed")
    if parsed is None:
        parsed = resume.get("parsed_json") or {}
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            parsed = {}
    skills = _json_list(resume.get("skills_json")) or list(parsed.get("skills") or [])
    languages = _json_list(resume.get("languages_json"))
    if not languages:
        blob = " ".join(str(v) for v in parsed.values() if isinstance(v, str))
        languages = [w for w in ["英语", "CET-4", "CET-6", "日语", "韩语", "俄语",
                                 "西班牙语", "法语", "德语"] if w in blob]
    return {
        "id": resume.get("id"),
        "name": resume.get("name") or "未命名简历",
        "skills": [s for s in skills if s],
        "languages": languages,
        "education": resume.get("education_level") or parsed.get("degree") or "",
        "target_industries": _json_list(resume.get("target_industries")),
        "target_job_types": _json_list(resume.get("target_job_types")),
        "target_cities": _json_list(resume.get("target_cities")),
        "overseas_preference": resume.get("overseas_preference") or "unset",
        "experience_summary": resume.get("experience_summary") or "",
        "major": parsed.get("major") or "",
        "parsed_ok": bool(parsed),
    }


def _dim_skill(job: dict, rv: dict) -> tuple[int | None, list[str], list[str]]:
    jd = f"{job.get('title','')} {job.get('jd_text','')} {job.get('major_req','')}"
    required = [s for s in SKILL_VOCAB if s.lower() in jd.lower()]
    if not required:
        return None, [], ["岗位未明确技能要求"]      # 未知，不猜
    if not rv["skills"]:
        missing = [f"简历未填写技能，无法比对：{'、'.join(required[:4])}"]
        return None, [], missing
    mine = {s.lower() for s in rv["skills"]}
    hit = [s for s in required if s.lower() in mine]
    miss = [s for s in required if s.lower() not in mine]
    score = int(100 * len(hit) / len(required)) if required else 0
    matched = [f"技能命中：{'、'.join(hit)}"] if hit else []
    missing = [f"岗位提到但简历未体现：{'、'.join(miss)}"] if miss else []
    return score, matched, missing


def _dim_education(job: dict, rv: dict) -> tuple[int | None, list[str], list[str], list[str]]:
    req = job.get("degree_req") or ""
    mine = rv.get("education") or ""
    risks: list[str] = []
    if not req:
        return None, [], ["岗位未标注学历要求"], risks
    req_rank = max([r for name, r in DEGREE_RANK.items() if name in req] or [0])
    my_rank = max([r for name, r in DEGREE_RANK.items() if name in mine] or [0])
    if not my_rank:
        return None, [], ["简历未解析出学历，请补充"], risks
    if req_rank > my_rank:
        risks.append(f"学历不符：岗位要求{req}，简历为{mine}")
        return 0, [], [f"学历要求 {req}"], risks
    return 100, [f"学历满足：{mine} ≥ {req}"], [], risks


def _dim_experience(job: dict, rv: dict) -> tuple[int | None, list[str], list[str], list[str]]:
    level = job.get("candidate_level") or ""
    req = job.get("experience_req") or ""
    risks: list[str] = []
    if level == "应届可投" or level == "实习":
        return 100, [f"接受应届生：{level}"], [], risks
    if level.startswith("资深"):
        risks.append("岗位偏资深，应届生竞争力弱")
        return 40, [], [req or "要求多年经验"], risks
    if not level:
        return None, [], ["岗位未标注经验要求"], risks
    return 60, [f"经验档位：{level}"], [], risks


def _dim_industry(job: dict, rv: dict) -> tuple[int | None, list[str], list[str]]:
    track = job.get("industry_track") or ""
    targets = rv.get("target_industries") or []
    if not track:
        return None, [], ["岗位未归入任何目标赛道"]
    if not targets:
        return None, [], ["简历未设置目标行业，无法比对"]
    if track in targets:
        return 100, [f"行业匹配：{job.get('industry_subtrack') or track}"], []
    return 40, [], [f"岗位赛道（{job.get('industry_subtrack') or track}）不在简历目标行业内"]


def _dim_job_type(job: dict, rv: dict) -> tuple[int | None, list[str], list[str], list[str]]:
    sales_type = job.get("sales_type") or ""
    targets = rv.get("target_job_types") or []
    risks: list[str] = []
    # 采购 / 供应链属于"职能型岗位"，没有 sales_type 可言。
    # 不能因为"不是销售岗"就判 0 分并打风险标签——那是用户明确要投的方向。
    function = job.get("job_function") or ""
    if function in FUNCTION_TARGET_WORDS:
        label = FUNCTION_LABELS.get(function, function)
        words = FUNCTION_TARGET_WORDS[function]
        if any(any(w in t for w in words) for t in targets):
            return 100, [f"岗位职能匹配：{label}"], [], risks
        if not targets:
            return None, [], ["简历未设置目标岗位方向"], risks
        return 60, [], [f"岗位职能（{label}）不在目标方向内"], risks
    if not sales_type:
        return None, [], ["岗位未识别销售类型"], risks
    if sales_type == "非销售岗位":
        risks.append("非销售岗位，与求职方向不符")
        return 0, [], ["岗位属于非销售类"], risks
    if not targets:
        return None, [], ["简历未设置目标岗位方向"], risks
    hit = [t for t in targets if any(alias in sales_type for alias in JOB_TYPE_ALIASES.get(t, []))]
    if hit:
        return 100, [f"岗位方向匹配：{'、'.join(hit)}"], [], risks
    return 40, [], [f"岗位方向（{sales_type}）不在目标内"], risks


def _dim_product(job: dict, rv: dict) -> tuple[int | None, list[str], list[str]]:
    subtrack = job.get("industry_subtrack") or ""
    blob = f"{job.get('title','')} {job.get('jd_text','')}"
    if not subtrack and not job.get("match_tags"):
        return None, [], ["岗位未提取到产品/客户信息"]
    tags = _json_list(job.get("match_tags_json"))
    if any("B端" in t or "外贸" in t for t in tags):
        return 80, ["岗位带 B端/外贸属性标签"], []
    if subtrack:
        return 70, [f"产品方向：{subtrack}"], []
    return None, [], ["产品与客户类型信息不足"]


def _dim_language(job: dict, rv: dict) -> tuple[int | None, list[str], list[str]]:
    req = job.get("language_requirement") or ""
    if not req:
        return None, [], []
    required = [w for w in re.split(r"[、,，\s]+", req) if w]
    if not required:
        return None, [], []
    if not rv["languages"]:
        return None, [], [f"岗位要求语言（{req}），简历未填写语言能力"]
    hit = [w for w in required if any(w in mine or mine in w for mine in rv["languages"])]
    if hit:
        return 100, [f"语言满足：{'、'.join(hit)}"], []
    return 30, [], [f"岗位要求语言（{req}），简历未体现"]


def _dim_location(job: dict, rv: dict, profile: dict) -> tuple[int | None, list[str], list[str]]:
    city = job.get("city") or ""
    targets = rv.get("target_cities") or _json_list(profile.get("prefer_cities"))
    if not city:
        return None, [], ["岗位未标注工作地点"]
    if not targets:
        return None, [], ["简历未设置目标城市"]
    hit = [c for c in targets if c and c in city]
    if hit:
        return 100, [f"地点命中：{'、'.join(hit)}"], []
    return 40, [], [f"工作地点（{city}）不在目标城市内"]


def _dim_overseas(job: dict, rv: dict) -> tuple[int, list[str], list[str], list[str]]:
    status = job.get("overseas_assignment_status") or "not_mentioned"
    pref = rv.get("overseas_preference") or "unset"
    allowed = OVERSEAS_ACCEPT.get(pref, set())
    label = OVERSEAS_LABELS.get(status, status)
    risks: list[str] = []
    if pref in ("unset", "ask") or not allowed:
        # 逐岗确认或未设置：不算冲突，但要提示
        return (80 if status == "not_mentioned" else 60), ([f"外派情况：{label}"] if status != "not_mentioned" else []), [], risks
    if status in allowed or status == "not_mentioned":
        return 100, [f"外派情况可接受：{label}"], [], risks
    risks.append(f"外派冲突：岗位为{label}，你的偏好是不接受")
    return 0, [], [f"外派要求：{label}"], risks


def match_resume_to_job(job: dict, resume: dict, profile: dict) -> dict:
    """单个岗位 × 单份简历的匹配结果。"""
    rv = resume_view(resume)
    matched: list[str] = []
    missing: list[str] = []
    risks: list[str] = []
    details: list[dict] = []
    total = 0
    weight_sum = 0
    unknown_dims: list[str] = []

    skill, m, ms = _dim_skill(job, rv)
    details.append({"dim": "技能匹配", "score": skill, "matched": m, "missing": ms})
    matched += m; missing += ms
    if skill is None: unknown_dims.append("技能")
    else: total += skill * WEIGHTS["skill"]; weight_sum += WEIGHTS["skill"]

    edu, m, ms, rk = _dim_education(job, rv)
    details.append({"dim": "学历匹配", "score": edu, "matched": m, "missing": ms})
    matched += m; missing += ms; risks += rk
    if edu is None: unknown_dims.append("学历")
    else: total += edu * WEIGHTS["education"]; weight_sum += WEIGHTS["education"]

    exp, m, ms, rk = _dim_experience(job, rv)
    details.append({"dim": "经验匹配", "score": exp, "matched": m, "missing": ms})
    matched += m; missing += ms; risks += rk
    if exp is None: unknown_dims.append("经验")
    else: total += exp * WEIGHTS["experience"]; weight_sum += WEIGHTS["experience"]

    ind, m, ms = _dim_industry(job, rv)
    details.append({"dim": "行业方向", "score": ind, "matched": m, "missing": ms})
    matched += m; missing += ms
    if ind is None: unknown_dims.append("行业")
    else: total += ind * WEIGHTS["industry"]; weight_sum += WEIGHTS["industry"]

    jt, m, ms, rk = _dim_job_type(job, rv)
    details.append({"dim": "岗位类型", "score": jt, "matched": m, "missing": ms})
    matched += m; missing += ms; risks += rk
    if jt is None: unknown_dims.append("岗位类型")
    else: total += jt * WEIGHTS["job_type"]; weight_sum += WEIGHTS["job_type"]

    prod, m, ms = _dim_product(job, rv)
    details.append({"dim": "产品/客户", "score": prod, "matched": m, "missing": ms})
    matched += m; missing += ms
    if prod is None: unknown_dims.append("产品客户")
    else: total += prod * WEIGHTS["product"]; weight_sum += WEIGHTS["product"]

    lang, m, ms = _dim_language(job, rv)
    details.append({"dim": "语言要求", "score": lang, "matched": m, "missing": ms})
    matched += m; missing += ms
    if lang is None: unknown_dims.append("语言")
    else: total += lang * WEIGHTS["language"]; weight_sum += WEIGHTS["language"]

    loc, m, ms = _dim_location(job, rv, profile)
    details.append({"dim": "工作地点", "score": loc, "matched": m, "missing": ms})
    matched += m; missing += ms
    if loc is None: unknown_dims.append("地点")
    else: total += loc * WEIGHTS["location"]; weight_sum += WEIGHTS["location"]

    ovs, m, ms, rk = _dim_overseas(job, rv)
    details.append({"dim": "外派要求", "score": ovs, "matched": m, "missing": ms})
    matched += m; missing += ms; risks += rk
    total += ovs * WEIGHTS["overseas"]; weight_sum += WEIGHTS["overseas"]

    score = int(total / weight_sum) if weight_sum else 0
    # 硬冲突必须在排序上体现出来：只加风险标签不够，否则 92 分的"强制驻外"会排在前面
    hard_conflicts = [r for r in risks
                      if r.startswith(("学历不符", "外派冲突", "非销售"))]
    if hard_conflicts:
        score = max(0, score - 25 * len(hard_conflicts))
    # 维度未知太多时，分数不可信
    if weight_sum < 40:
        level = "unknown"
    elif score >= 75:
        level = "high"
    elif score >= 55:
        level = "medium"
    else:
        level = "low"
    if hard_conflicts and level != "unknown":
        level = "low"

    explanation_parts = [f"{d['dim']}：{'／'.join(d['matched']) or '／'.join(d['missing']) or '信息不足'}"
                         for d in details]
    return {
        "job_id": job.get("id"),
        "resume_id": rv["id"],
        "resume_name": rv["name"],
        "match_score": score,
        "match_level": level,
        "matched_requirements": matched,
        "missing_requirements": missing,
        "risk_tags": risks,
        "unknown_dimensions": unknown_dims,
        "dimensions": details,
        "recommended_resume": False,
        "explanation": "；".join(explanation_parts)[:800],
    }


def match_job_against_resumes(job: dict, resumes: list[dict], profile: dict) -> dict:
    """一个岗位对多份简历，选出推荐版本。"""
    results = [match_resume_to_job(job, r, profile) for r in resumes]
    results = [r for r in results if r["resume_id"]]
    if not results:
        return {"job_id": job.get("id"), "matches": [], "recommended": None}
    results.sort(key=lambda r: (-r["match_score"], r["resume_id"]))
    results[0]["recommended_resume"] = True
    return {"job_id": job.get("id"), "matches": results, "recommended": results[0]}
