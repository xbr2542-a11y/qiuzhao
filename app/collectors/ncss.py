"""24365 国家大学生就业服务平台（教育部）采集器。

接口：GET https://job.ncss.cn/student/jobs/jobslist/ajax/?jobName=关键词&offset=页码&limit=条数
详情：https://job.ncss.cn/student/jobs/{jobId}/detail.html

特点：国家平台，岗位覆盖央企、地方国企、民企、合资、外企；字段里**带专业要求**（major），
比国聘更适合做"专业档位"判断。公开接口，无需登录。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from .base import http_request

CODE = "ncss"
NAME = "24365 国家大学生就业服务平台"
NOTE = "教育部国家平台，民企/国企/合资校招都有，含专业要求字段；公开接口，无需登录"
SUPPORTS = {"keyword": True, "city": True, "campus_only": False}
SOURCE_LABELS = ["24365国家平台"]

API = "https://job.ncss.cn/student/jobs/jobslist/ajax/"
DETAIL_URL = "https://job.ncss.cn/student/jobs/{job_id}/detail.html"
HEADERS = {
    "Referer": "https://job.ncss.cn/student/jobs/index.html",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}


def search(keyword: str, offset: int = 1, limit: int = 20, area_code: str = "",
           recruit_type: str = "", degree_code: str = "") -> tuple[int, int, list[dict]]:
    """返回 (命中总数, 总页数, 本页条目)。"""
    from urllib.parse import urlencode

    params = {
        "jobType": "", "areaCode": area_code, "jobName": keyword, "monthPay": "",
        "industrySectors": "", "property": "", "categoryCode": "", "memberLevel": "",
        "recruitType": recruit_type, "offset": offset, "limit": limit, "keyUnits": "",
        "degreeCode": degree_code, "sourcesName": 0, "sourcesType": "",
        "_": int(time.time() * 1000),
    }
    status, payload, raw = http_request(f"{API}?{urlencode(params)}", headers=HEADERS)
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"24365 接口请求失败：HTTP {status} {raw[:160]}")
    if not payload.get("flag"):
        raise RuntimeError(f"24365 接口返回异常：{payload.get('errors')}")
    data = payload.get("data") or {}
    page_info = data.get("pagenation") or {}
    count = int(page_info.get("count") or 0)
    pages = int(page_info.get("total") or 0)
    return count, pages, list(data.get("list") or [])


def _stamp(value: Any) -> str:
    """接口返回的是毫秒时间戳。"""
    try:
        return datetime.fromtimestamp(int(value) / 1000).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def guess_job_type(title: str, recruit_type: str) -> str:
    if any(word in title for word in ("实习", "Intern", "intern")):
        return "实习"
    return "校招" if str(recruit_type) == "1" else "校招"


def normalize(item: dict) -> dict:
    job_id = str(item.get("jobId") or "")
    low = item.get("lowMonthPay") or 0
    high = item.get("highMonthPay") or 0
    salary = ""
    if low or high:
        salary = f"{low}-{high}K/月" if high and high != low else f"{low or high}K/月"
    title = str(item.get("jobName") or "").strip()
    return {
        "company": str(item.get("recName") or "").strip(),
        "title": title,
        "city": str(item.get("areaCodeName") or "").strip(),
        "source": "24365国家平台",
        "url": DETAIL_URL.format(job_id=job_id) if job_id else "",
        "salary": salary,
        "salary_min": int(float(low) * 1000),
        "salary_max": int(float(high) * 1000),
        "degree_req": str(item.get("degreeName") or "").strip(),
        "major_req": str(item.get("major") or "").strip(),
        "job_type": guess_job_type(title, item.get("recruitType")),
        "company_nature": str(item.get("recProperty") or "").strip(),
        "company_scale": str(item.get("recScale") or "").strip(),
        "industry": str(item.get("industryName") or "").strip(),
        "published_at": _stamp(item.get("publishDate") or item.get("updateDate")),
        "deadline": "",
        "raw_text": f"{title} {item.get('recName', '')} {item.get('major', '')}",
        "raw": item,
    }


def collect(keyword: str, cities: tuple[str, ...] = (), limit: int = 60,
            max_pages: int = 5, page_size: int = 20) -> list[dict]:
    """按关键词抓取。24365 每页 20 条，接口给的是总页数。"""
    collected: list[dict] = []
    page = 1
    while len(collected) < limit and page <= max_pages:
        try:
            _count, pages, items = search(keyword, offset=page, limit=page_size)
        except Exception:
            break
        if not items:
            break
        for item in items:
            job = normalize(item)
            if not job["title"] or not job["company"]:
                continue
            if cities and job["city"] and not any(city in job["city"] for city in cities):
                continue
            collected.append(job)
            if len(collected) >= limit:
                break
        if pages and page >= pages:
            break
        page += 1
    return collected
