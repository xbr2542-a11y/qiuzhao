"""微信公众号校招公告采集器（通过搜狗微信搜索）。

说明：
- 微信官方没有公开的文章检索接口，这里走**搜狗微信搜索**（公开可访问的聚合页）。
- 搜狗给的 /link?url=... 是跳转链接，浏览器打开时会由 JS 跳到真实公众号文章，
  所以直接存这个链接即可，用户点击就能看到原文。
- 抓的是**招聘公告**（"XX公司2027届校园招聘启动"），不是具体岗位，
  存进岗位池时会标注来源为"微信公众号"，方便你顺着公告找到官方投递入口。
"""

from __future__ import annotations

import re
import time
from html import unescape
from urllib.parse import quote

from .base import http_request

CODE = "wechat"
NAME = "微信公众号招聘公告"
NOTE = "通过搜狗微信搜索抓取校招公告（微信无公开接口，这是合法可达的公开入口）"
SUPPORTS = {"keyword": True, "city": False, "campus_only": False}
SOURCE_LABELS = ["微信公众号"]

SEARCH = "https://weixin.sogou.com/weixin?type=2&query={query}"
HEADERS = {"Referer": "https://weixin.sogou.com/"}

# 校招公告类关键词（比岗位名更容易搜到公告）
KEYWORDS = [
    "2027届校园招聘", "2027校园招聘 国企", "2027届秋招 数据分析",
    "2027届校园招聘 半导体", "2027届校园招聘 外贸",
    # 跨境电商与外贸相关
    "2027届校园招聘 跨境电商", "跨境电商 2027 校招", "跨境电商BD 招聘",
    "2027届校园招聘 外贸业务员", "2027届校园招聘 BD",
    # 覆盖面更广的校招公告
    "2027届校园招聘 不限专业", "2027届校园招聘 民营企业", "2027届秋季校园招聘",
    "2027届校园招聘 外企", "2027届校招 汇总",
]

COMPANY_PATTERNS = [
    r"【([\u4e00-\u9fa5]{2,20}?(?:集团|公司|银行|研究院|局|院|所|中心|事业部))】",
    r"^([\u4e00-\u9fa5]{2,20}?(?:集团|公司|银行|研究院|局|院|所|中心))20\d{2}",
    r"【[^】]*?】\s*([\u4e00-\u9fa5]{2,20}?(?:集团|公司|银行|研究院|局|院|厂|中心|事业部))",
    r"([\u4e00-\u9fa5]{2,20}?(?:集团|公司|银行|研究院|局|院|所|中心))[\s\-—|]*"
    r"(?:20\d{2}|启动|开启|正式|招聘)",
    r"^(?:央企校招|国企招聘|校园招聘)\s*[|｜]\s*([\u4e00-\u9fa5]{2,20})",
]

# 只保留近期的校招公告：过时文章（2017/2018 年的校招）对当届学生毫无价值
MAX_AGE_DAYS = 400
OLD_YEAR_PATTERN = re.compile(r"(20[01]\d)\s*(?:届|年)?\s*(?:校园招聘|校招|秋招|春季招聘|秋季招聘)")


def is_fresh(item: dict) -> bool:
    """按发布时间和标题里的年份过滤过时公告。"""
    title = item.get("title", "")
    match = OLD_YEAR_PATTERN.search(title)
    if match and int(match.group(1)) < time.localtime().tm_year - 1:
        return False
    stamp = item.get("_ts")
    if stamp and str(stamp).isdigit():
        seconds = int(stamp)
        if seconds > 10_000_000_000:  # 毫秒
            seconds //= 1000
        age_days = (time.time() - seconds) / 86400
        if age_days > MAX_AGE_DAYS:
            return False
    return True


def search(keyword: str, page: int = 1) -> list[dict]:
    url = SEARCH.format(query=quote(keyword))
    if page > 1:
        url += f"&page={page}"
    status, _, html = http_request(url, headers=HEADERS)
    if status != 200:
        raise RuntimeError(f"搜狗微信搜索失败：HTTP {status}")
    if "antispider" in html or "请输入验证码" in html:
        raise RuntimeError("搜狗微信搜索触发了验证码，已停止（不做绕过）")
    return parse_results(html)


def parse_results(html: str) -> list[dict]:
    """解析搜狗微信搜索结果列表。"""
    items: list[dict] = []
    # 每条结果是一个 <li> 块，含标题、公众号名、摘要、时间
    for block in re.findall(r"<li[^>]*>(.*?)</li>", html, re.S):
        link = re.search(r'href="(/link\?url=[^"]+)"', block)
        title = re.search(r'<h3>\s*<a[^>]*>(.*?)</a>', block, re.S)
        if not link or not title:
            continue
        name = re.search(r'<a[^>]*class="account"[^>]*>(.*?)</a>', block, re.S)
        date = re.search(r"document\.write\(timeConvert\('(\d+)'\)\)", block)
        summary = re.search(r'<p class="txt-info"[^>]*>(.*?)</p>', block, re.S)
        items.append({
            "title": unescape(re.sub(r"<[^>]+>", "", title.group(1))).strip(),
            "link": "https://weixin.sogou.com" + unescape(link.group(1)).replace("&amp;", "&"),
            "account": unescape(re.sub(r"<[^>]+>", "", name.group(1))).strip() if name else "",
            "summary": unescape(re.sub(r"<[^>]+>", "", summary.group(1))).strip() if summary else "",
            "_ts": date.group(1) if date else "",
        })
    return items


def extract_company(title: str) -> str:
    for pattern in COMPANY_PATTERNS:
        match = re.search(pattern, title)
        if match:
            return match.group(1).strip()
    return ""


def normalize(item: dict) -> dict:
    title = item.get("title", "")
    company = extract_company(title) or item.get("account", "")
    summary = item.get("summary", "")
    return {
        "company": company,
        "title": title,
        "city": "",
        "source": "微信公众号",
        "url": item.get("link", ""),
        "job_type": "校招",
        "industry": "",
        "company_nature": "",
        "jd_text": summary,
        "major_req": "",
        "degree_req": "",
        "deadline": "",
        "raw_text": f"{title} {summary} {company}",
        "raw": item,
    }


def collect(keyword: str, cities: tuple[str, ...] = (), limit: int = 20,
            max_pages: int = 1) -> list[dict]:
    collected: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            items = search(keyword, page=page)
        except Exception:
            break
        if not items:
            break
        for item in items:
            if not is_fresh(item):
                continue
            job = normalize(item)
            if job["title"]:
                collected.append(job)
            if len(collected) >= limit:
                return collected
    return collected
