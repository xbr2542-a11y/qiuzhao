"""识别公司招聘官网背后用的是哪套系统（Workday / SuccessFactors / Greenhouse …）。

外企几乎都用这几套 ATS。识别出来之后，Workday 这类有公开 JSON 接口的就能直接采集，
而需要登录、有强风控的（领英等）就明确放弃。

用法：python tools/ats_probe.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from collectors.base import http_request  # noqa: E402

TARGETS = [
    ("ASML", "https://www.asml.com/en/careers/find-your-job"),
    ("应用材料 Applied Materials", "https://www.appliedmaterials.com/us/en/careers.html"),
    ("美光 Micron", "https://careers.micron.com/"),
    ("德州仪器 TI", "https://careers.ti.com/"),
    ("恩智浦 NXP", "https://www.nxp.com/company/about-nxp/careers:CAREERS"),
    ("英飞凌 Infineon", "https://www.infineon.com/cms/en/careers/"),
    ("是德科技 Keysight", "https://jobs.keysight.com/"),
    ("西门子 Siemens", "https://jobs.siemens.com/careers"),
    ("施耐德 Schneider", "https://www.se.com/ww/en/about-us/careers/"),
    ("博世 Bosch", "https://www.bosch.com/careers/"),
]

SIGNATURES = [
    ("Workday", r"([\w\-]+\.wd\d+\.myworkdayjobs\.com[\w\-/]*)"),
    ("SuccessFactors", r"([\w\-]+\.successfactors\.(?:com|eu)[\w\-/]*)"),
    ("Greenhouse", r"(boards\.greenhouse\.io/[\w\-]+|job-boards\.greenhouse\.io/[\w\-]+)"),
    ("Lever", r"(jobs\.lever\.co/[\w\-]+)"),
    ("SmartRecruiters", r"(jobs\.smartrecruiters\.com/[\w\-]+|careers\.smartrecruiters\.com/[\w\-]+)"),
    ("iCIMS", r"([\w\-]+\.icims\.com)"),
    ("Taleo", r"([\w\-]+\.taleo\.net)"),
    ("Phenom", r"([\w\-]+\.phenompeople\.com|phenom\.cloud)"),
    ("Avature", r"([\w\-]+\.avature\.net)"),
    ("Oracle ORC", r"([\w\-]+\.oraclecloud\.com/hcmUI/CandidateExperience[\w\-/]*)"),
    ("LinkedIn", r"(linkedin\.com/jobs)"),
]


def main() -> int:
    for name, url in TARGETS:
        status, _, raw = http_request(url, retries=1)
        if status == 0:
            print(f"{name:24} 请求失败：{raw[:60]}")
            continue
        found = []
        for label, pattern in SIGNATURES:
            for match in re.findall(pattern, raw, re.I):
                token = match if isinstance(match, str) else match[0]
                if token and token not in found:
                    found.append(f"{label}:{token}")
        job_hint = len(re.findall(r"(job|career|position)", raw, re.I))
        marker = "、".join(found[:3]) if found else "未识别到已知 ATS"
        print(f"{name:24} HTTP {status:3}  {len(raw) // 1024:5} KB  招聘词 {job_hint:4}  -> {marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
