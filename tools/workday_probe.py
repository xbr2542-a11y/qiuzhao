"""批量探测 Workday 招聘站点：找出能直接取到岗位的公司。

用法：python tools/workday_probe.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from collectors.base import http_request  # noqa: E402

# (代号, 公司名, host 编号, site 名称)
CANDIDATES = [
    ("nvidia", "英伟达 NVIDIA", "wd5", "NVIDIAExternalCareerSite"),
    ("nvidia", "英伟达 NVIDIA", "wd5", "External"),
    ("amd", "AMD", "wd1", "AMD_External"),
    ("amd", "AMD", "wd1", "External"),
    ("intel", "英特尔 Intel", "wd1", "External"),
    ("qualcomm", "高通 Qualcomm", "wd5", "External"),
    ("appliedmaterials", "应用材料 Applied Materials", "wd1", "External"),
    ("lamresearch", "泛林 Lam Research", "wd1", "External"),
    ("kla", "KLA", "wd1", "External"),
    ("te", "泰科 TE Connectivity", "wd1", "External"),
    ("honeywell", "霍尼韦尔 Honeywell", "wd1", "External"),
    ("siemens", "西门子 Siemens", "wd3", "External"),
    ("danaher", "丹纳赫 Danaher", "wd1", "External"),
    ("thermofisher", "赛默飞 Thermo Fisher", "wd1", "External"),
    ("maersk", "马士基 Maersk", "wd3", "External"),
    ("cargill", "嘉吉 Cargill", "wd1", "External"),
]


def probe(tenant: str, host: str, site: str, keyword: str = "sales") -> tuple[bool, str]:
    base = f"https://{tenant}.{host}.myworkdayjobs.com"
    url = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    status, payload, raw = http_request(
        url,
        json_body={"appliedFacets": {}, "limit": 5, "offset": 0, "searchText": keyword},
        headers={"Origin": base, "Referer": f"{base}/en-US/{site}", "Accept": "application/json"},
        retries=0,
    )
    if status == 200 and isinstance(payload, dict) and "jobPostings" in payload:
        total = payload.get("total")
        sample = (payload["jobPostings"][0].get("title") if payload["jobPostings"] else "")
        return True, f"total={total} 例：{sample[:40]}"
    return False, f"HTTP {status} {raw[:60]}"


def main() -> int:
    ok = []
    for tenant, name, host, site in CANDIDATES:
        try:
            success, note = probe(tenant, host, site)
        except Exception as exc:
            success, note = False, f"{type(exc).__name__}: {exc}"
        flag = "✔ 可用" if success else "✘"
        print(f"{name:26} {tenant}.{host}/{site:32} {flag} {note}")
        if success:
            ok.append((tenant, name, host, site))
    print("\n=== 可用清单 ===")
    for tenant, name, host, site in ok:
        print(f'    "{tenant}": {{"label": "{name}", "tenant": "{tenant}", '
              f'"host": "{host}", "site": "{site}"}},')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
