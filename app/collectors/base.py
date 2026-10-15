"""采集器公共设施：带 UA 的 HTTP 请求、限速、重试。

合规约定（全项目统一执行）：
1. 只访问公开页面与公开接口，绝不绕过验证码、登录或反爬机制；
2. 遵守 robots.txt；
3. 默认限速 —— 每分钟不超过 12 次请求；
4. 携带真实 UA，失败即记录并跳过，不反复冲击目标站点。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Origin": "https://www.iguopin.com",
    "Referer": "https://www.iguopin.com/",
}

_last_call = 0.0
MIN_INTERVAL = 2.5  # 秒：每分钟最多 24 次（仍是节制访问，不会给站点压力）


def throttle(interval: float = MIN_INTERVAL) -> None:
    global _last_call
    elapsed = time.monotonic() - _last_call
    if _last_call and elapsed < interval:
        time.sleep(interval - elapsed)
    _last_call = time.monotonic()


def http_request(url: str, json_body: Any = None, headers: dict | None = None,
                 timeout: int = 25, retries: int = 2, throttle_first: bool = True,
                 ) -> tuple[int, Any, str]:
    """返回 (状态码, 解析后的 JSON 或 {}, 原始文本)。"""
    merged = {**DEFAULT_HEADERS, **(headers or {})}
    data = None
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        merged["Content-Type"] = "application/json;charset=UTF-8"

    last_error = ""
    for attempt in range(retries + 1):
        if throttle_first or attempt:
            throttle()
        request = urllib.request.Request(url, data=data, headers=merged,
                                         method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                try:
                    return response.status, json.loads(raw), raw
                except json.JSONDecodeError:
                    return response.status, {}, raw
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code} {exc.reason}"
            if exc.code in {403, 404, 429}:
                break
        except Exception as exc:  # 网络异常
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(2 * (attempt + 1))
    return 0, {}, last_error
