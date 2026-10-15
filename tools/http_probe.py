"""站点结构探针：抓一个 URL，报告它能被怎么抓（服务端渲染 / JSON 接口 / SPA）。

用法：
    python tools/http_probe.py <url> [--save 文件名] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / ".tmp" / "probe"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")


def fetch(url: str, timeout: int = 20) -> tuple[int, dict, str]:
    request = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        try:
            text = raw.decode(charset, errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        return response.status, dict(response.headers), text


def analyze(url: str, status: int, headers: dict, text: str) -> dict:
    lower = text.lower()
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    markers = {
        "next_data": "__NEXT_DATA__" in text,
        "nuxt": "__NUXT__" in text,
        "window_init": bool(re.search(r"window\.__[A-Z_]+__", text)),
        "vue": "vue" in lower[:4000],
        "react": "react" in lower[:4000],
        "jquery": "jquery" in lower,
        "json_api_hint": bool(re.search(r"/(api|v\d)/[\w\-/]+", text)),
    }
    apis = sorted(set(re.findall(r"[\"'](https?://[\w.\-]+/[^\s\"'<>]{0,80}?/(?:api|job|jobs|search|list)[^\s\"'<>]{0,60})[\"']", text)))[:15]
    # 可见文本量（粗略剥标签）
    visible = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
    visible = re.sub(r"<[^>]+>", " ", visible)
    visible = re.sub(r"\s+", " ", visible).strip()
    job_words = ["招聘", "岗位", "校招", "校园招聘", "薪资", "工作地点", "应届"]
    hits = {word: visible.count(word) for word in job_words}
    return {
        "status": status,
        "content_type": headers.get("Content-Type", ""),
        "bytes": len(text.encode("utf-8", errors="ignore")),
        "title": (title.group(1).strip() if title else ""),
        "frameworks": markers,
        "api_candidates": apis,
        "visible_text_chars": len(visible),
        "keyword_hits": hits,
        "visible_head": visible[:600],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--save", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--raw", action="store_true", help="直接输出原始响应前 4000 字")
    args = parser.parse_args()

    try:
        status, headers, text = fetch(args.url)
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code} {exc.reason}")
        return 1
    except Exception as exc:
        print(f"请求失败：{type(exc).__name__}: {exc}")
        return 1

    if args.raw:
        print(text[:4000])
        return 0

    report = analyze(args.url, status, headers, text)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"状态: {report['status']}  {report['content_type']}  {report['bytes']} 字节")
        print(f"标题: {report['title']}")
        print(f"框架特征: {report['frameworks']}")
        print(f"可见文本: {report['visible_text_chars']} 字")
        print(f"招聘词频: {report['keyword_hits']}")
        print(f"接口候选({len(report['api_candidates'])}):")
        for api in report["api_candidates"]:
            print("   ", api)
        print("可见文本开头:", report["visible_head"][:300])

    if args.save:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        target = OUT_DIR / args.save
        target.write_text(text, encoding="utf-8")
        print(f"已保存: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
