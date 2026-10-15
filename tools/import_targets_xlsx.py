"""导入《央国企投递清单》这类"单位级投递入口"表格。

注意：这类表格给的是**单位 + 招聘入口 + 往年网申窗口**，不是具体岗位。
入池时会标成"单位投递入口"，并把表格自带的匹配度写进备注（那是按环境设计专业打的，
和系统按你的求职意向算的匹配分是两回事，不能混为一谈）。

用法：
    python tools/import_targets_xlsx.py "D:\桌面\央国企投递清单_2027届山东.xlsx" --dry-run
    python tools/import_targets_xlsx.py "D:\桌面\央国企投递清单_2027届山东.xlsx" --ingest
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import db  # noqa: E402
import ingest  # noqa: E402


def normalize_url(entry: str) -> str:
    """把 "zhaopin.sgcc.com.cn" 这种域名补成可点击的 https 链接；非网址返回空。"""
    text = (entry or "").strip()
    if not text:
        return ""
    if text.startswith("http"):
        return text
    if re.fullmatch(r"[a-zA-Z0-9\-\.]+\.(com|cn|com\.cn|org|net)(/[^\s]*)?", text):
        return "https://" + text
    return ""      # "官网人才招聘栏目""集团官网" 这类不是网址，不编造


def read_sheet(workbook, need_header_first: str) -> list[tuple]:
    for name in workbook.sheetnames:
        rows = list(workbook[name].iter_rows(values_only=True))
        for index, row in enumerate(rows):
            if row and row[0] == need_header_first:
                header = [str(c).strip() if c else "" for c in row]
                return header, rows[index + 1:]
    return [], []


def cell(row: tuple, index: int) -> str:
    """安全取单元格：行可能比表头短。"""
    return str(row[index]).strip() if row and len(row) > index and row[index] else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--ingest", action="store_true")
    args = parser.parse_args()

    from openpyxl import load_workbook

    db.init_db()
    workbook = load_workbook(args.path, read_only=True, data_only=True)
    print(f"读取：{args.path}")
    print(f"工作表：{workbook.sheetnames}")

    # ---------- 1) 单位清单 ----------
    header, tail = read_sheet(workbook, "序号")
    print(f"\n单位清单表头：{header}")
    units: list[dict] = []
    for row in tail:
        if not row or not isinstance(row[0], int):
            continue
        record = {header[i]: row[i] for i in range(min(len(header), len(row)))}
        name = str(record.get("单位名称") or "").strip()
        if not name:
            continue
        direction = str(record.get("对口岗位方向") or "").strip()
        portal = str(record.get("招聘入口／平台") or "").strip()
        nature = str(record.get("单位类型") or "").strip()
        block = str(record.get("板块") or "").strip()
        window = str(record.get("往年网申窗口") or "").strip()
        fit = str(record.get("匹配度") or "").strip()
        status = str(record.get("投递状态") or "").strip()
        remark = str(record.get("备注") or "").strip()
        deadline = record.get("报名截止日")
        note_parts = [f"单位类型：{nature}", f"板块：{block}", f"往年网申窗口：{window}",
                      f"文件标注匹配度：{fit}（按环境设计专业）", f"清单状态：{status}"]
        if remark:
            note_parts.append(remark)
        units.append({
            "company": name,
            "title": f"2027届校园招聘（{direction}）" if direction else "2027届校园招聘",
            "city": str(record.get("工作地点") or "").strip(),
            "source": "央国企投递清单",
            "channel_type": "unit",       # 标记为单位级入口，分类器不会按岗位规则排除
            "url": normalize_url(portal),
            "deadline": str(deadline or "").strip(),
            "job_type": "校招",
            "family": "soe",
            "company_nature": nature,
            "note": "；".join(note_parts),
            "raw_text": f"{name} {block} {direction}",
        })

    high = [u for u in units if "高" in u["note"]]
    print(f"单位数：{len(units)}（文件标注高匹配 {len(high)} 家）")
    print(f"有可点击招聘入口的：{sum(1 for u in units if u['url'])} 家")
    for unit in units[:6]:
        print(f"  · {unit['company'][:20]:<22} | {unit['city'][:10]:<12} | {unit['url'][:44]}")

    # ---------- 2) 通用投递入口 ----------
    channels: list[dict] = []
    for name in workbook.sheetnames:
        rows = list(workbook[name].iter_rows(values_only=True))
        for row in rows:
            if row and row[0] == "平台" and row[1] == "用途":
                for item in rows[rows.index(row) + 1:]:
                    # 空行代表这张表结束，后面的表是别的主题，不能一起抓
                    if not item or not item[0]:
                        break
                    channels.append({"name": str(item[0]).strip(),
                                     "note": cell(item, 1),
                                     "url": normalize_url(cell(item, 2))})
                break
    print(f"\n通用投递入口：{len(channels)} 个")
    for channel in channels:
        print(f"  · {channel['name'][:28]:<30} {channel['url'][:40] or '（无链接，需自行搜索）'}")

    # ---------- 3) 招聘日历 → 提醒 ----------
    reminders: list[dict] = []
    for name in workbook.sheetnames:
        rows = list(workbook[name].iter_rows(values_only=True))
        for row in rows:
            if row and row[0] == "时间" and row[1] == "招聘节点":
                for item in rows[rows.index(row) + 1:]:
                    if item and item[0]:
                        reminders.append({"when": str(item[0]), "node": cell(item, 1),
                                          "focus": cell(item, 2)})
                break
    print(f"\n招聘日历节点：{len(reminders)} 条")
    for item in reminders:
        print(f"  · {item['when']}: {item['node'][:44]}")

    if not args.ingest:
        print("\n（未写入，加 --ingest 才会入库）")
        return 0

    result = ingest.upsert_jobs(units, "央国企投递清单")
    print(f"\n单位入口入库：{result}")

    stamp = db.now()
    with db.connect() as conn:
        for channel in channels:
            conn.execute(
                """INSERT INTO sources (code, name, category, url, adapter, family, note)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(code) DO UPDATE SET name=excluded.name, url=excluded.url,
                     note=excluded.note""",
                (channel["name"], channel["name"], "通用入口", channel["url"], "", "soe", channel["note"]))
        for item in reminders:
            match = re.search(r"(\d{4})年(\d{1,2})", item["when"])
            due = f"{match.group(1)}-{int(match.group(2)):02d}-01" if match else ""
            title = f"{item['when']}：{item['node'][:60]}"
            # 幂等：重复导入同一份表格不能产生重复提醒
            exists = conn.execute(
                "SELECT id FROM reminders WHERE title = ? AND due_at = ?", (title, due)).fetchone()
            if exists:
                continue
            conn.execute(
                """INSERT INTO reminders (kind, title, detail, due_at, source, confidence, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                ("招聘节点", title, item["focus"], due,
                 "央国企投递清单", "confirmed", stamp))
        conn.commit()
    print(f"渠道入库：{len(channels)} 个；日历提醒入库：{len(reminders)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
