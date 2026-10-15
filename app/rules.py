"""筛选与排序规则：专业档位、岗位族、匹配打分。"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

# 专业别名：用于把画像里的专业扩展成一组可命中的关键词
MAJOR_ALIASES = {
    "环境设计": ["环境设计", "室内设计", "环境艺术", "艺术设计", "设计学", "景观设计", "空间设计"],
    "室内设计": ["室内设计", "环境设计", "环境艺术", "艺术设计", "设计学"],
    "视觉传达": ["视觉传达", "平面设计", "艺术设计", "设计学"],
    "计算机": ["计算机", "软件工程", "信息技术", "计算机科学", "软件", "数据科学"],
    "统计学": ["统计", "应用统计", "数据科学", "数学"],
    "国际贸易": ["国际经济与贸易", "国际贸易", "经贸", "商务英语", "外贸"],
    "市场营销": ["市场营销", "销售", "工商管理", "商务"],
    "英语": ["英语", "商务英语", "翻译", "外语"],
}

UNLIMITED_PATTERNS = [
    "专业不限", "不限专业", "专业要求不限", "无专业限制", "不限专业背景",
    "专业：不限", "不限",
]

RELATED_PATTERNS = ["相关专业", "等相关专业", "或相关", "优先考虑", "相近专业"]

FAMILY_KEYWORDS = {
    "semi": ["半导体", "晶圆", "集成电路", "芯片", "封测", "光刻", "刻蚀", "EDA", "设备销售",
             "技术销售", "现场应用", "FAE", "功率器件", "碳化硅", "氮化镓"],
    "data": ["数据分析", "数据运营", "商业分析", "BI", "数据科学", "算法", "数据挖掘", "商业智能",
             "数据治理", "报表", "SQL", "数据开发"],
    "trade": ["外贸", "国际贸易", "海外销售", "跨境电商", "进出口", "业务员", "外销", "海外业务",
              "海外市场", "外贸业务"],
    "soe": ["中国石油", "中国石化", "国家电网", "中国移动", "中国联通", "中国电信", "中国建筑",
            "中国中铁", "中国铁建", "中交", "中核", "中船", "国家能源", "华能", "大唐", "华电",
            "国家电投", "三峡", "中粮", "招商局", "兵器", "航天", "航空", "中国电科", "东风",
            "胜利油田", "石油工程", "齐鲁石化", "山东能源", "鲁信", "水发", "城投", "国资"],
}

CITY_ALIASES = {
    "东营": ["东营", "胜利油田", "垦利", "广饶", "河口"],
    "济南": ["济南", "历下", "高新区", "章丘", "莱芜"],
}

DEGREE_ORDER = {"不限": 0, "中专": 1, "高中": 1, "大专": 2, "专科": 2, "本科": 3, "学士": 3,
                "硕士": 4, "研究生": 4, "博士": 5}


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(n and n in text for n in needles)


def major_keywords(major: str) -> list[str]:
    major = (major or "").strip()
    if not major:
        return []
    words = [major]
    for key, aliases in MAJOR_ALIASES.items():
        if key in major or major in key:
            words.extend(aliases)
    return sorted({w for w in words if len(w) >= 2 and w not in {"不限", "专业"}})


def classify_major(major_req: str, profile_major: str, jd_text: str = "") -> tuple[int, str]:
    """返回 (档位, 说明)。档位：0 未知 / 1 不限 / 2 相关或命中 / 3 明确限定且不对口。"""
    text = (major_req or "").strip()
    if not text:
        # 职位描述里没提专业要求，实际等于不限——跨专业求职时这是最重要的放宽信号
        excerpt = _major_sentence(jd_text)
        if excerpt:
            text = excerpt
        elif (jd_text or "").strip():
            return 1, "岗位未提专业限制，按专业不限处理"
        else:
            return 0, "岗位未标注专业要求，需人工确认"
    if _contains_any(text, ["专业不限", "不限专业", "无专业限制"]) or text in {"不限", "无", "-", "—"}:
        return 1, "专业不限"
    keywords = major_keywords(profile_major)
    hit = [k for k in keywords if k in text]
    if hit:
        return 2, f"命中专业关键词：{'、'.join(hit[:3])}"
    if _contains_any(text, RELATED_PATTERNS):
        return 3, f"限定“相关专业”：{text[:40]}（你的专业未命中）"
    return 3, f"明确限定专业：{text[:40]}"


def _major_sentence(text: str) -> str:
    """从 JD 正文里找出提到专业要求的那一句。"""
    for match in re.finditer(r"[^。；\n]{0,80}专业[^。；\n]{0,40}", text or ""):
        phrase = match.group(0).strip(" 　:：，,")
        if any(key in phrase for key in ["要求", "相关", "类专业", "限", "及以上"]):
            return phrase[:120]
    return ""


def infer_family(title: str, company: str = "", raw: str = "") -> str:
    blob = f"{title} {company} {raw}"
    scores: dict[str, int] = {}
    for family, keywords in FAMILY_KEYWORDS.items():
        scores[family] = sum(1 for k in keywords if k in blob)
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "general"


def match_city(city_text: str, prefer_cities: list[str]) -> tuple[int, str]:
    city_text = (city_text or "").strip()
    if not city_text:
        return 30, "地点未标注"
    if city_text in {"不限", "全国", "多地"}:
        return 20, "地点不限"
    for index, city in enumerate(prefer_cities):
        needles = CITY_ALIASES.get(city, [city])
        if _contains_any(city_text, needles):
            return max(10, 100 - index * 15), f"命中意向城市 {city}"
    if "山东" in city_text:
        return 45, "山东省内"
    return 20, "非意向城市"


def degree_ok(degree_req: str, profile_degree: str) -> tuple[bool, str]:
    text = (degree_req or "").strip()
    if not text or text in {"不限", "-", "—"}:
        return True, ""
    requirement = 0
    for name, rank in DEGREE_ORDER.items():
        if name in text:
            requirement = max(requirement, rank)
    mine = 0
    for name, rank in DEGREE_ORDER.items():
        if name in (profile_degree or ""):
            mine = max(mine, rank)
    if requirement and mine and mine < requirement:
        return False, f"学历要求 {text}，你的学历是 {profile_degree or '未填写'}"
    return True, ""


def normalize_deadline(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    text = text.replace("年", "-").replace("月", "-").replace("日", "")
    text = text.replace("/", "-").replace(".", "-")
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        y, m, d = (int(g) for g in match.groups())
        return f"{y:04d}-{m:02d}-{d:02d}"
    match = re.search(r"(\d{1,2})-(\d{1,2})", text)
    if match:
        year = datetime.now().year
        m, d = (int(g) for g in match.groups())
        return f"{year:04d}-{m:02d}-{d:02d}"
    return text


def days_left(deadline: str) -> int | None:
    normalized = normalize_deadline(deadline)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized or ""):
        return None
    try:
        target = date.fromisoformat(normalized)
    except ValueError:
        return None
    return (target - date.today()).days


def score_job(job: dict[str, Any], profile: dict[str, Any]) -> tuple[int, list[str]]:
    reasons: list[str] = []
    total = 0

    tier, tier_note = classify_major(job.get("major_req", ""), profile.get("major", ""),
                                     job.get("jd_text", ""))
    tier_points = {0: 5, 1: 35, 2: 25, 3: -70}
    total += tier_points[tier]
    reasons.append(f"专业档位 {tier}：{tier_note}")

    city_points, city_note = match_city(job.get("city", ""), profile.get("prefer_cities", []))
    total += city_points
    reasons.append(city_note)

    ok, degree_note = degree_ok(job.get("degree_req", ""), profile.get("degree", ""))
    if not ok:
        total -= 80 if "博士" in (job.get("degree_req") or "") else 55
        reasons.append(degree_note)

    left = days_left(job.get("deadline", ""))
    if left is None:
        reasons.append("截止日期未标注")
    elif left < 0:
        total -= 50
        reasons.append(f"已过截止 {abs(left)} 天")
    elif left <= 3:
        total += 25
        reasons.append(f"仅剩 {left} 天截止")
    elif left <= 7:
        total += 15
        reasons.append(f"剩余 {left} 天截止")

    if job.get("family") == "soe":
        total += 10
        reasons.append("央国企优先线")

    return max(0, min(100, total)), reasons


def build_dedupe_key(company: str, title: str, source: str, url: str = "") -> str:
    def norm(value: str) -> str:
        return re.sub(r"[\s\-—_（）()【】\[\]，,。.·:：/]+", "", (value or "").strip().lower())

    base = f"{norm(company)}|{norm(title)}|{norm(source)}"
    if not norm(company) and not norm(title) and url:
        base = norm(url)
    return base


def evaluate(job: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """把一条原始岗位补齐为可入库记录。"""
    title = (job.get("title") or "").strip()
    company = (job.get("company") or "").strip()
    source = (job.get("source") or "").strip() or "手工导入"
    family = job.get("family") or infer_family(title, company, job.get("raw_text", ""))
    tier, tier_note = classify_major(job.get("major_req", ""), profile.get("major", ""),
                                     job.get("jd_text", ""))
    enriched = {
        **job,
        "company": company,
        "title": title,
        "source": source,
        "family": family,
        "city": (job.get("city") or "").strip(),
        "url": (job.get("url") or "").strip(),
        "deadline": normalize_deadline(job.get("deadline", "")),
        "major_tier": tier,
        "filter_note": tier_note,
        "dedupe_key": build_dedupe_key(company, title, source, job.get("url", "")),
    }
    score, reasons = score_job(enriched, profile)
    enriched["match_score"] = score
    enriched["score_reasons"] = reasons
    return enriched
