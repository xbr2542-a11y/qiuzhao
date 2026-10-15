"""外企官网采集器（Workday 招聘系统）。

很多外企的招聘官网跑在 Workday 上，它对外提供公开的职位检索接口：
    POST https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    GET  https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}

合规：只读公开职位接口，无需登录，不绕验证码；全局限速 12 次/分钟。
"""

from __future__ import annotations

import re
from typing import Any

from .base import http_request

CODE = "workday"
NAME = "外企官网（Workday 招聘系统）"
NOTE = "美光、恩智浦等外企的官方招聘接口，公开 JSON，无需登录"
SUPPORTS = {"keyword": True, "city": True, "campus_only": False}

# 已核实可用的公司（接口真实返回过数据）
COMPANIES: dict[str, dict[str, str]] = {
    "micron": {"label": "美光 Micron", "tenant": "micron", "host": "wd1", "site": "External",
               "family": "semi", "industry": "半导体"},
    "nxp": {"label": "恩智浦 NXP", "tenant": "nxp", "host": "wd3", "site": "careers",
            "family": "semi", "industry": "半导体"},
    "nvidia": {"label": "英伟达 NVIDIA", "tenant": "nvidia", "host": "wd5",
               "site": "NVIDIAExternalCareerSite", "family": "semi", "industry": "半导体/人工智能"},
}
# 本采集器写入 jobs.source 的取值（必须放在 COMPANIES 之后）
SOURCE_LABELS = [f"{item['label']}官网" for item in COMPANIES.values()]

# 外企用英文检索更准
KEYWORDS = ["sales", "account manager", "field application engineer", "data analyst",
            "business development"]

CHINA_HINTS = ["China", "Shanghai", "Beijing", "Shenzhen", "Xi'an", "Xian", "Suzhou",
               "Tianjin", "Dalian", "Chengdu", "Wuhan", "Hangzhou", "Nanjing", "Guangzhou",
               "中国", "上海", "北京", "深圳", "西安", "苏州", "天津", "大连", "成都"]

INTERN_HINTS = ["intern", "internship", "实习", "student", "graduate", "campus", "university"]


def _api(company: dict[str, str]) -> str:
    return (f"https://{company['tenant']}.{company['host']}.myworkdayjobs.com"
            f"/wday/cxs/{company['tenant']}/{company['site']}/jobs")


def _headers(company: dict[str, str]) -> dict[str, str]:
    base = f"https://{company['tenant']}.{company['host']}.myworkdayjobs.com"
    return {"Origin": base, "Referer": f"{base}/en-US/{company['site']}",
            "Accept": "application/json"}


def search(company: dict[str, str], keyword: str, limit: int = 20, offset: int = 0,
           ) -> tuple[int, list[dict]]:
    status, payload, raw = http_request(
        _api(company),
        json_body={"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": keyword},
        headers=_headers(company),
    )
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"{company['label']} 接口请求失败：HTTP {status} {raw[:160]}")
    return int(payload.get("total") or 0), list(payload.get("jobPostings") or [])


def fetch_detail(company: dict[str, str], external_path: str) -> dict[str, Any]:
    """取单个岗位详情，拿岗位描述原文（用于提取专业与学历要求）。"""
    url = (f"https://{company['tenant']}.{company['host']}.myworkdayjobs.com"
           f"/wday/cxs/{company['tenant']}/{company['site']}{external_path}")
    status, payload, _ = http_request(url, headers=_headers(company))
    if status != 200 or not isinstance(payload, dict):
        return {}
    info = payload.get("jobPostingInfo") or {}
    return info if isinstance(info, dict) else {}


def detail_url(company: dict[str, str], external_path: str) -> str:
    return (f"https://{company['tenant']}.{company['host']}.myworkdayjobs.com"
            f"/en-US/{company['site']}{external_path}")


def is_china(location: str) -> bool:
    return any(hint.lower() in (location or "").lower() for hint in CHINA_HINTS)


def guess_job_type(title: str, time_type: str = "") -> str:
    blob = f"{title} {time_type}".lower()
    if any(hint in blob for hint in INTERN_HINTS):
        return "实习"
    return "社招"


def clean_text(html: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>|</li>", "\n", html or "", flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", text)).strip()


def extract_major_req(text: str) -> str:
    """从英文 JD 里找专业/学历要求，中英文都试。"""
    patterns = [
        r"[^\n.]{0,80}(?:Bachelor|Master|PhD|Degree)[^\n.]{0,80}",
        r"[^\n。；]{0,60}专业[^\n。；]{0,40}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I)
        if match:
            return match.group(0).strip()[:160]
    return ""


def normalize(posting: dict, company_key: str, detail: dict | None = None) -> dict:
    company = COMPANIES[company_key]
    external_path = str(posting.get("externalPath") or "")
    detail = detail or {}
    jd_text = clean_text(str(detail.get("jobDescription") or ""))
    title = str(posting.get("title") or detail.get("title") or "").strip()
    location = str(posting.get("locationsText") or detail.get("location") or "").strip()
    return {
        "company": company["label"],
        "title": title,
        "city": location,
        "source": f"{company['label']}官网",
        "url": detail_url(company, external_path) if external_path else "",
        "published_at": str(posting.get("postedOn") or ""),
        "deadline": "",
        "job_type": guess_job_type(title, str(posting.get("timeType") or "")),
        "industry": company.get("industry", ""),
        "company_nature": "外企",
        "major_req": extract_major_req(jd_text),
        "degree_req": "",
        "jd_text": jd_text,
        "family": company.get("family", "general"),
        "raw_text": f"{title} {company['label']}",
        "raw": {"posting": posting, "external_path": external_path},
    }


def collect(keyword: str, cities: tuple[str, ...] = (), limit: int = 20,
            company: str = "micron", china_only: bool = True, with_detail: int = 4) -> list[dict]:
    """按关键词抓取某家外企的岗位。china_only 时只保留中国的岗位。"""
    if company not in COMPANIES:
        raise ValueError(f"未收录的公司：{company}，可选 {list(COMPANIES)}")
    target = COMPANIES[company]
    collected: list[dict] = []
    offset = 0
    detail_budget = max(0, with_detail)
    while len(collected) < limit and offset < 200:
        total, postings = search(target, keyword, limit=20, offset=offset)
        if not postings:
            break
        for posting in postings:
            location = str(posting.get("locationsText") or "")
            if china_only and not is_china(location):
                continue
            detail = {}
            if detail_budget > 0:
                detail = fetch_detail(target, str(posting.get("externalPath") or ""))
                detail_budget -= 1
            job = normalize(posting, company, detail)
            if not job["title"]:
                continue
            collected.append(job)
            if len(collected) >= limit:
                break
        offset += 20
        if offset >= total:
            break
    return collected
