"""大厂招聘官网采集器。

只抓**校招/实习**岗位（社招对在校生没意义）。

已核实可用：
  美团校招  POST https://zhaopin.meituan.com/api/official/job/getJobList
            jobType=[{"code":"1"},{"code":"2"}] 即校招+实习

待接入（原因记录在案，不猜、不绕）：
  字节跳动  jobs.bytedance.com 接口带 _signature 反爬签名，必须用真实浏览器会话才能取，
            属于"用浏览器正常访问"范畴，等浏览器采集器做好再接（不做签名逆向）。
  腾讯校招  join.qq.com/api/v1/position/* 接口路径未探明，需继续排查。
  其他大厂  小米/京东/华为等，同样需要逐个探接口。
"""

from __future__ import annotations

from typing import Any

from .base import http_request

CODE = "bigtech"
NAME = "大厂校招官网"
NOTE = "美团等大厂的校招/实习岗位；公开接口，无需登录、无签名校验"
SUPPORTS = {"keyword": True, "city": True, "campus_only": True}
SOURCE_LABELS = ["美团校招官网"]

MEITUAN_API = "https://zhaopin.meituan.com/api/official/job/getJobList"
MEITUAN_DETAIL = "https://zhaopin.meituan.com/web/position/detail?jobUnionId={job_id}"
MEITUAN_HEADERS = {
    "Origin": "https://zhaopin.meituan.com",
    "Referer": "https://zhaopin.meituan.com/web/campus",
    "Content-Type": "application/json;charset=UTF-8",
}


def meituan_search(keyword: str, page: int = 1, page_size: int = 20) -> list[dict]:
    """美团校招搜索：jobType 1=校招、2=实习。"""
    body = {
        "page": {"pageNo": page, "pageSize": page_size},
        "jobShareType": "1",
        "keywords": keyword,
        "cityList": [], "department": [], "jfJgList": [],
        "jobType": [{"code": "1", "subCode": []}, {"code": "2", "subCode": []}],
        "typeCode": [], "specialCode": [],
    }
    status, payload, raw = http_request(MEITUAN_API, json_body=body, headers=MEITUAN_HEADERS)
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"美团接口请求失败：HTTP {status} {raw[:160]}")
    data = payload.get("data") or {}
    return list(data.get("list") or [])


def meituan_normalize(item: dict) -> dict:
    cities = [str(c.get("name") or "") for c in (item.get("cityList") or []) if isinstance(c, dict)]
    departments = [str(d.get("name") or "") for d in (item.get("department") or []) if isinstance(d, dict)]
    title = str(item.get("name") or "").strip()
    job_type = "实习" if str(item.get("jobType")) == "2" else "校招"
    duty = str(item.get("jobDuty") or "")
    requirement = str(item.get("jobRequirement") or "")
    return {
        "company": "美团",
        "title": title,
        "city": " / ".join(dict.fromkeys(cities)),
        "source": "美团校招官网",
        "url": MEITUAN_DETAIL.format(job_id=item.get("jobUnionId")) if item.get("jobUnionId") else "",
        "job_type": job_type,
        "industry": "互联网",
        "company_nature": "民营企业",
        "department": " / ".join(departments),
        "jd_text": (duty + "\n\n" + requirement).strip(),
        "major_req": "",
        "degree_req": "",
        "deadline": "",
        "raw_text": f"{title} {item.get('jobFamilyGroup', '')} 美团",
        "raw": item,
    }


# 公司注册表：后续接新大厂只要往这里加一条
COMPANIES: dict[str, dict[str, Any]] = {
    "meituan": {"label": "美团", "search": meituan_search, "normalize": meituan_normalize,
                "page_size": 20, "family": "general"},
}


def collect(keyword: str, cities: tuple[str, ...] = (), limit: int = 40,
            company: str = "meituan", max_pages: int = 2) -> list[dict]:
    if company not in COMPANIES:
        raise ValueError(f"未收录的大厂：{company}，可选 {list(COMPANIES)}")
    target = COMPANIES[company]
    collected: list[dict] = []
    page = 1
    while len(collected) < limit and page <= max_pages:
        try:
            items = target["search"](keyword, page=page, page_size=target["page_size"])
        except Exception:
            break
        if not items:
            break
        for item in items:
            job = target["normalize"](item)
            if not job["title"]:
                continue
            if cities and job["city"] and not any(city in job["city"] for city in cities):
                continue
            collected.append(job)
            if len(collected) >= limit:
                break
        page += 1
    return collected
