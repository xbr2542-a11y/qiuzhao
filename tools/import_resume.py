"""把一份简历导入系统：存原件 → 结构化解析 → 写入事实库 → 可选回填档案。

用法：
    python tools/import_resume.py "简历.docx" --name "通用版" --families general,soe
    python tools/import_resume.py <文件> --fill-profile        # 只填空缺字段
    python tools/import_resume.py <文件> --fill-profile --force # 覆盖已有字段
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import db  # noqa: E402
import resume as resume_mod  # noqa: E402

PROFILE_COLUMNS = ["full_name", "school", "major", "degree", "grad_date",
                   "political", "hometown", "phone", "email"]


def import_resume(path: Path, name: str, families: list[str], is_default: bool,
                  fill_profile: bool, force: bool) -> dict:
    db.init_db()
    suffix = path.suffix.lower()
    if suffix == ".docx":
        sections = resume_mod.read_docx_sections(str(path))
        text = "\n".join(line for lines in sections.values() for line in lines)
        parsed = resume_mod.parse_resume_text(text)
        extracted = resume_mod.extract_profile_fields(sections)
    else:
        sections = {}
        text = resume_mod.read_text(str(path))
        parsed = resume_mod.parse_resume_text(text)
        extracted = {}

    saved = resume_mod.save_file(path.name, base64.b64encode(path.read_bytes()).decode())
    parsed["sections"] = {key: len(value) for key, value in sections.items()}
    parsed["text_length"] = len(text)

    with db.connect() as conn:
        if is_default:
            conn.execute("UPDATE resumes SET is_default = 0")
        cursor = conn.execute(
            """INSERT INTO resumes (name, families, filename, stored_path, size, sha256,
               parsed_json, is_default, uploaded_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (name, json.dumps(families, ensure_ascii=False), saved["filename"], saved["stored_path"],
             saved["size"], saved["sha256"], json.dumps(parsed, ensure_ascii=False),
             1 if is_default else 0, db.now()),
        )
        resume_id = int(cursor.lastrowid)
        conn.commit()

    facts = resume_mod.build_facts(resume_id, sections, parsed) if sections else []
    if facts:
        db.replace_facts(resume_id, facts)

    profile_updated: dict[str, object] = {}
    if fill_profile and extracted:
        with db.connect() as conn:
            current = dict(conn.execute("SELECT * FROM profile WHERE id = 1").fetchone())
            assignments, values = [], []
            for key, value in extracted.items():
                if key in {"job_intent", "current_city", "education_period", "skills"}:
                    continue
                if key not in PROFILE_COLUMNS:
                    continue
                if not value:
                    continue
                existing = current.get(key)
                if existing and not force:
                    continue
                assignments.append(f"{key} = ?")
                values.append(value)
                profile_updated[key] = value
            extra = json.loads(current.get("extra_json") or "{}")
            for key in ("job_intent", "current_city", "education_period"):
                if extracted.get(key):
                    extra[key] = extracted[key]
            assignments.append("extra_json = ?")
            values.append(json.dumps(extra, ensure_ascii=False))
            assignments.append("updated_at = ?")
            values.append(db.now())
            values.append(1)
            conn.execute(f"UPDATE profile SET {', '.join(assignments)} WHERE id = ?", values)
            conn.commit()

    return {"resume_id": resume_id, "facts": len(facts), "profile": profile_updated,
            "extracted": extracted, "parsed": parsed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="简历文件路径")
    parser.add_argument("--name", default="", help="版本名称，默认取文件名")
    parser.add_argument("--families", default="general", help="适用岗位族，逗号分隔：general,soe,data,trade,semi")
    parser.add_argument("--default", action="store_true", help="设为默认版本")
    parser.add_argument("--fill-profile", action="store_true", help="用简历内容回填档案空缺字段")
    parser.add_argument("--force", action="store_true", help="覆盖档案已有字段")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"[错误] 文件不存在：{path}")
        return 1
    families = [f.strip() for f in args.families.split(",") if f.strip()]
    name = args.name or path.stem

    result = import_resume(path, name, families, args.default, args.fill_profile, args.force)
    parsed = result["parsed"]
    print(f"✅ 已导入简历 #{result['resume_id']}：{name}")
    print(f"   文件：{path.name}（{path.stat().st_size // 1024} KB）")
    print(f"   解析文本：{parsed.get('text_length', 0)} 字；章节：{parsed.get('sections', {})}")
    print(f"   事实条目：{result['facts']} 条（AI 生成时只能引用这些条目）")
    print("   从简历里认出的信息：")
    for key, value in result["extracted"].items():
        if value:
            print(f"     - {key}: {value}")
    if result["profile"]:
        print("   已回填档案：", "、".join(result["profile"].keys()))
    notes = parsed.get("hints") or []
    if notes:
        print("   提示：", "；".join(notes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
