"""国聘（国资委官方招聘平台）采集器。

接口：POST https://gp-api.iguopin.com/api/jobs/v1/recom-job
合规：robots.txt 允许抓取；只读公开岗位数据；全局限速 12 次/分钟；不登录、不绕验证码。
"""

from __future__ import annotations

import sys
from typing import Any

from .base import http_request

CODE = "guopin"
NAME = "国聘（国资委官方平台）"
NOTE = "央企、国企校招岗位，含大量 2027 届公告；公开接口，无需登录"
SUPPORTS = {"keyword": True, "city": True, "campus_only": True}
SOURCE_LABELS = ["国聘"]  # 本采集器写入 jobs.source 的取值，清空时必须精确匹配

API = "https://gp-api.iguopin.com/api/jobs/v1/recom-job"
DETAIL_URL = "https://www.iguopin.com/job/detail?id={job_id}"
COMPANY_URL = "https://www.iguopin.com/company?id={company_id}"


def search(keyword: str, page: int = 1, page_size: int = 20,
           extra: dict | None = None) -> tuple[int, list[dict]]:
    """按关键词搜索岗位，返回 (总数, 原始条目列表)。"""
    body: dict[str, Any] = {
        "search": {"page": page, "page_size": page_size, "keyword": keyword},
        "recom": {"update_time": True, "company_nature": True, "hot_job": True},
    }
    if extra:
        body["search"].update(extra)
    status, payload, raw = http_request(API, json_body=body)
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"国聘接口请求失败：HTTP {status} {raw[:200]}")
    if payload.get("code") not in (200, 0, None):
        raise RuntimeError(f"国聘接口返回异常：{payload.get('msg')}")
    data = payload.get("data") or {}
    return int(data.get("total") or 0), list(data.get("list") or [])


def _first(item: dict, *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", 0, "0"):
            return str(value).strip()
    return ""


def extract_city(item: dict) -> str:
    districts = item.get("district_list") or []
    names = [str(d.get("area_cn") or "").strip() for d in districts if isinstance(d, dict)]
    names = [n for n in names if n]
    return " / ".join(dict.fromkeys(names))[:120]


def extract_major_req(contents: str) -> str:
    """从 JD 正文里找出专业要求那一句——跨专业求职时这是最关键的筛选依据。"""
    import re

    text = contents or ""
    for match in re.finditer(r"[^。；\n]{0,80}专业[^。；\n]{0,40}", text):
        phrase = match.group(0).strip(" 　:：，,")
        if any(key in phrase for key in ["要求", "相关", "类", "限", "及以上"]):
            return phrase[:120]
    return ""


def normalize(item: dict) -> dict:
    """把国聘的原始条目映射成本系统的岗位结构。"""
    job_id = _first(item, "job_id", "id")
    company = _first(item, "company_name", "company_full_name", "company_short_name")
    title = _first(item, "job_name", "job_title", "name")
    city = extract_city(item)
    wage = ""
    min_wage, max_wage = item.get("min_wage"), item.get("max_wage")
    unit = _first(item, "wage_unit_cn") or "元/月"
    if min_wage or max_wage:
        wage = f"{min_wage or 0}-{max_wage or 0} {unit}".strip()
    elif item.get("is_negotiable"):
        wage = "面议"

    contents = str(item.get("contents") or "")
    company_info = item.get("company_info") or {}
    major_req = extract_major_req(contents)
    degree_req = _first(item, "education_cn", "degree_cn", "education", "degree")
    nature = _first(item, "nature_cn", "recruitment_type_cn", "job_nature_cn")
    deadline = _first(item, "end_time", "deadline", "expire_time")

    return {
        "company": company,
        "title": title,
        "city": city,
        "source": "国聘",
        "url": DETAIL_URL.format(job_id=job_id) if job_id else "",
        "company_url": COMPANY_URL.format(company_id=item.get("company_id")) if item.get("company_id") else "",
        "salary": wage,
        "salary_min": int(min_wage or 0),
        "salary_max": int(max_wage or 0),
        "major_req": major_req,
        "degree_req": degree_req,
        "experience_req": _first(item, "experience_cn"),
        "jd_text": contents,
        "department": _first(item, "department_cn"),
        "job_type": "校招" if "校招" in nature or "校园" in nature else ("实习" if "实习" in nature else "社招"),
        "published_at": _first(item, "start_time", "publish_time", "create_time", "refresh_time"),
        "deadline": deadline[:10] if deadline else "",
        "industry": _first(company_info, "industry_cn") or _first(item, "industry_cn"),
        "company_nature": _first(company_info, "nature_cn") or _first(item, "company_nature_cn"),
        "company_scale": _first(company_info, "scale_cn"),
        "is_graduates": bool(item.get("is_graduates")),
        "family": "soe",
        "raw_text": " ".join(str(item.get(k, "")) for k in ("job_name", "company_name", "category_cn")),
        "raw": item,
    }


def collect(keyword: str, cities: tuple[str, ...] = (), limit: int = 40,
            campus_only: bool = True, relevant_only: bool = True,
            max_pages: int = 6) -> list[dict]:
    """按关键词抓取岗位，可按城市本地过滤。cities 为空表示不过滤。"""
    page_size = 20
    collected: list[dict] = []
    dropped = 0
    page = 1
    total = None
    while len(collected) < limit:
        total_now, items = search(keyword, page=page, page_size=page_size)
        if total is None:
            total = total_now
        if not items:
            break
        for item in items:
            job = normalize(item)
            if not job["title"] or not job["company"]:
                continue
            if campus_only and job["job_type"] == "社招":
                continue
            if relevant_only and not is_relevant(job, keyword):
                dropped += 1
                continue
            if cities and not any(city in job["city"] for city in cities):
                continue
            collected.append(job)
            if len(collected) >= limit:
                break
        page += 1
        if page > max_pages or (page - 1) * page_size >= (total or 0):
            break
    if dropped:
        print(f"    （已过滤 {dropped} 条与“{keyword}”不相关的岗位）", file=sys.stderr)
    return collected


def keyword_tokens(keyword: str) -> list[str]:
    """把关键词拆成用于相关性判断的词元：空格分词 + 中文二元组。"""
    tokens: list[str] = []
    for chunk in keyword.replace("/", " ").replace("、", " ").split():
        if len(chunk) <= 2:
            tokens.append(chunk)
            continue
        tokens.append(chunk)
        tokens.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return list(dict.fromkeys(t for t in tokens if len(t) >= 2))


def is_relevant(job: dict, keyword: str) -> bool:
    """岗位标题或 JD 里必须真的出现关键词——国聘的模糊搜索会返回大量无关岗位。"""
    if not keyword:
        return True
    title = job.get("title", "")
    body = f"{job.get('jd_text', '')} {job.get('major_req', '')}"
    if keyword in title or keyword in body:
        return True
    tokens = keyword_tokens(keyword)
    parts = [token for token in tokens if token != keyword]
    if not parts:
        return True
    title_hits = {token for token in parts if token in title}
    body_hits = {token for token in parts if token in body}
    # 标题里至少命中一个词元，且整体命中两个以上不同的词元，才算相关
    return bool(title_hits) and len(title_hits | body_hits) >= 2
