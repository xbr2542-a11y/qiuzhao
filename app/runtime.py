"""定位本机可用的运行时路径（Node / Playwright 模块 / Chrome）。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _codex_runtimes() -> list[Path]:
    base = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".cache" / "codex-runtimes"
    if not base.exists():
        return []
    dirs = [p for p in base.iterdir() if p.is_dir()]
    # 正式运行时目录优先，其次才是 .previous / 备份目录
    dirs.sort(key=lambda p: (p.name != "codex-primary-runtime", p.name), reverse=False)
    return dirs


def find_node() -> str | None:
    local = ROOT / ".runtime" / "node.exe"
    if local.exists():
        return str(local)
    for rt in _codex_runtimes():
        exe = rt / "dependencies" / "node" / "bin" / "node.exe"
        if exe.exists():
            return str(exe)
    return shutil.which("node")


def find_node_modules() -> str | None:
    local = ROOT / ".runtime" / "node_modules"
    if local.exists():
        return str(local)
    for rt in _codex_runtimes():
        mods = rt / "dependencies" / "node" / "node_modules"
        if (mods / "playwright").exists():
            return str(mods)
    return None


def find_chrome() -> str | None:
    override = os.environ.get("QIUZHAO_CHROME")
    if override and Path(override).exists():
        return override
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def worker_env() -> dict[str, str]:
    env = dict(os.environ)
    node_modules = find_node_modules()
    if node_modules:
        env["NODE_PATH"] = node_modules
    chrome = find_chrome()
    if chrome:
        env["QIUZHAO_CHROME"] = chrome
    env.setdefault("QIUZHAO_ROOT", str(ROOT))
    return env


def runtime_report() -> dict[str, str | None]:
    return {
        "node": find_node(),
        "node_modules": find_node_modules(),
        "chrome": find_chrome(),
        "project_root": str(ROOT),
    }


if __name__ == "__main__":
    for key, value in runtime_report().items():
        print(f"{key:>14}: {value}")
