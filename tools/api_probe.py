"""直接调用 JSON 接口并打印结构，用于确认采集器可以脱离浏览器工作。

用法：
    python tools/api_probe.py <url> --json '{"a":1}' [--headers '{"X":"y"}'] [--full]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from collectors.base import http_request  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--json", default="")
    parser.add_argument("--headers", default="")
    parser.add_argument("--full", action="store_true", help="打印完整响应")
    parser.add_argument("--keys", action="store_true", help="打印 data.list[0] 的所有字段")
    args = parser.parse_args()

    body = json.loads(args.json) if args.json else None
    headers = json.loads(args.headers) if args.headers else {}
    status, payload, raw = http_request(args.url, json_body=body, headers=headers)
    print(f"HTTP {status}")
    if args.full:
        print(raw[:4000])
        return 0
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        print("data 字段:", list(data.keys()))
        print("total:", data.get("total"), "page:", data.get("page"), "page_size:", data.get("page_size"))
        items = data.get("list")
        if isinstance(items, list) and items:
            print(f"共 {len(items)} 条，第一条完整字段：")
            print(json.dumps(items[0], ensure_ascii=False, indent=2)[:3000])
    else:
        print(json.dumps(payload, ensure_ascii=False)[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
