"""重新解析已上传的简历，并把求职意向写成可编辑的初始值。

注意：这里写入的目标行业/岗位/城市来自"简历里写明的求职意向"和你在档案里设的偏好，
不是猜测。全部可以在面板「简历库」里改。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import db  # noqa: E402
import resume as resume_mod  # noqa: E402
import tracks as tracks_mod  # noqa: E402

# 简历里"求职意向"写的方向 → 系统赛道代码
INTENT_TO_TRACK = {
    "数据分析": [],
    "半导体": ["lab_instrument"],
    "外贸": ["chem_lubricant"],
    "环境设计": [],
}

# 简历里的岗位方向 → 系统岗位方向标签
INTENT_TO_JOB_TYPE = {
    "数据分析": [],
    "半导体": ["技术销售", "B端解决方案销售"],
    "外贸": ["外贸BD", "国际业务", "海外销售"],
    "环境设计": [],
}


def main() -> int:
    db.init_db()
    with db.connect() as conn:
        rows = db.rows_to_dicts(conn.execute("SELECT * FROM resumes").fetchall())
        profile = dict(conn.execute("SELECT * FROM profile WHERE id=1").fetchone())

    for row in rows:
        path = row.get("stored_path") or ""
        if not path or not Path(path).exists():
            print(f"简历 #{row['id']} 文件缺失，跳过：{path}")
            continue
        sections = resume_mod.read_docx_sections(path) if path.lower().endswith(".docx") else {}
        text = "\n".join(line for lines in sections.values() for line in lines) if sections \
            else resume_mod.read_text(path)
        parsed = resume_mod.parse_resume_text(text)
        extracted = resume_mod.extract_profile_fields(sections) if sections else {}

        intent = str(extracted.get("job_intent") or (json.loads(profile.get("extra_json") or "{}")).get("job_intent") or "")
        industries: list[str] = []
        job_types: list[str] = []
        for keyword, track_codes in INTENT_TO_TRACK.items():
            if keyword in intent:
                industries.extend(track_codes)
        for keyword, types in INTENT_TO_JOB_TYPE.items():
            if keyword in intent:
                job_types.extend(types)
        cities = json.loads(profile.get("prefer_cities") or "[]")
        skills = parsed.get("skills") or []

        with db.connect() as conn:
            conn.execute(
                """UPDATE resumes SET parsed_json=?, education_level=?, skills_json=?,
                   languages_json=?, target_cities=?, target_industries=?, target_job_types=?,
                   updated_at=? WHERE id=?""",
                (json.dumps(parsed, ensure_ascii=False), extracted.get("degree") or parsed.get("degree") or "",
                 json.dumps(skills, ensure_ascii=False),
                 json.dumps(parsed.get("languages") or [], ensure_ascii=False),
                 json.dumps(cities, ensure_ascii=False),
                 json.dumps(list(dict.fromkeys(industries)), ensure_ascii=False),
                 json.dumps(list(dict.fromkeys(job_types)), ensure_ascii=False),
                 db.now(), row["id"]),
            )
            conn.commit()
        print(f"简历 #{row['id']} 已刷新：学历={extracted.get('degree') or parsed.get('degree')}"
              f" 技能={skills} 目标行业={industries} 目标岗位={job_types}")
        print(f"  求职意向原文：{intent[:60]}")
        print(f"  可用赛道代码：{'、'.join(t['code'] for t in tracks_mod.TRACKS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
