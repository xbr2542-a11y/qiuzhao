"""任务处理器注册表。

每个处理器签名：handler(ctx: TaskContext, payload: dict) -> dict（返回值写入 tasks.result_json）
处理器必须：在长循环里调用 ctx.checkpoint()、用 ctx.progress() 上报进度、用 ctx.log() 写日志、
在发起网络请求前调用 ctx.rate_limit()。
"""

from __future__ import annotations

from typing import Any

from tasks import TaskContext, TaskManager


def collect_source(ctx: TaskContext, payload: dict, source: str) -> dict:
    """按求职方向在指定官方渠道搜索岗位并入库。

    payload: {"keywords": [...], "cities": [...], "limit": 25, "reset": false,
              "company": "micron"（仅 Workday 类来源需要）}
    """
    import ingest
    from collectors import REGISTRY, keywords_from_profile

    module = REGISTRY[source]
    profile = ingest.get_profile()
    keywords = payload.get("keywords") or keywords_from_profile(profile, source)
    # 外企岗位不在你的目标城市里，只保留中国的岗位；
    # 国聘默认全国抓取（先把池子做大，城市偏好交给匹配度排序处理）
    china_only = source == "workday"
    nationwide = bool(payload.get("nationwide", True))
    cities = () if (china_only or nationwide) else tuple(
        payload.get("cities") or profile.get("prefer_cities") or [])
    limit = int(payload.get("limit") or 40)
    reset = bool(payload.get("reset"))
    company = payload.get("company") or ""

    ctx.log(f"开始在「{module.NAME}」搜索，方向：{'、'.join(keywords)}", module="collect")
    scope = "仅限中国" if china_only else ("全国" if not cities else "、".join(cities))
    ctx.log(f"范围：{scope}；每个关键词最多 {limit} 条", module="collect")
    ctx.progress(0, len(keywords), "准备中")

    collected: list[dict] = []
    for index, keyword in enumerate(keywords, start=1):
        ctx.checkpoint()
        ctx.progress(index - 1, len(keywords), f"正在搜索：{keyword}")
        try:
            if source == "workday":
                jobs = []
                for key in (company,) if company else module.COMPANIES:
                    jobs.extend(module.collect(keyword, cities=cities, limit=limit, company=key))
            else:
                jobs = module.collect(keyword, cities=cities, limit=limit)
        except Exception as exc:
            ctx.log(f"「{keyword}」采集失败：{exc}", level="error", module="collect")
            continue
        ctx.log(f"「{keyword}」命中 {len(jobs)} 条", module="collect",
                payload={"sample": [f"{j['company']}·{j['title']}" for j in jobs[:3]]})
        collected.extend(jobs)
        ctx.rate_limit()

    unique: dict[str, dict] = {}
    for job in collected:
        unique.setdefault(f"{job['company']}|{job['title']}|{job['city']}", job)

    ctx.checkpoint()
    ctx.progress(len(keywords), len(keywords), f"入库 {len(unique)} 条")
    if reset:
        import db
        # 只清空本采集器写入的岗位，而且必须精确匹配。
        # 之前用 LIKE '%官网' 把"美团校招官网"等其他来源一起删了（数据事故）。
        labels = list(getattr(module, "SOURCE_LABELS", []) or [])
        if not labels:
            raise RuntimeError(f"{source} 未声明 SOURCE_LABELS，拒绝清空")
        removed = ingest.clear_source(labels)
        ctx.log(f"已清空「{module.NAME}」原有岗位 {removed} 条（其他来源不受影响）",
                level="warn", module="collect")
    result = ingest.upsert_jobs(list(unique.values()), source)
    ctx.log(f"入库完成：新增 {result['inserted']}，更新 {result['updated']}", module="collect")
    return {"keywords": keywords, "collected": len(unique), **result}


def collect_guopin(ctx: TaskContext, payload: dict) -> dict:
    """国聘（国资委官方平台）：央企国企岗位。"""
    return collect_source(ctx, payload, "guopin")


def collect_workday(ctx: TaskContext, payload: dict) -> dict:
    """外企官网（Workday 系统）：美光、恩智浦等。"""
    return collect_source(ctx, payload, "workday")


def collect_ncss(ctx: TaskContext, payload: dict) -> dict:
    """24365 国家大学生就业服务平台（教育部）：民企、国企、合资校招都有。"""
    return collect_source(ctx, payload, "ncss")


def collect_bigtech(ctx: TaskContext, payload: dict) -> dict:
    """大厂校招官网：美团等。"""
    return collect_source(ctx, payload, "bigtech")


def collect_wechat(ctx: TaskContext, payload: dict) -> dict:
    """微信公众号校招公告（经搜狗微信搜索）。"""
    return collect_source(ctx, payload, "wechat")


def analyze_jobs(ctx: TaskContext, payload: dict) -> dict:
    """批量重新评分（规则引擎；接入 AI 后在此扩展）。"""
    import db
    import ingest
    import rules

    profile = ingest.get_profile()
    with db.connect() as conn:
        rows = db.rows_to_dicts(conn.execute("SELECT * FROM jobs").fetchall())
    ctx.progress(0, len(rows), "开始评分")
    for index, row in enumerate(rows, start=1):
        ctx.checkpoint()
        score, _ = rules.score_job(row, profile)
        tier, note = rules.classify_major(row.get("major_req", ""), profile.get("major", ""))
        with db.connect() as conn:
            conn.execute("UPDATE jobs SET match_score = ?, major_tier = ?, filter_note = ? WHERE id = ?",
                         (score, tier, note, row["id"]))
            conn.commit()
        if index % 20 == 0 or index == len(rows):
            ctx.progress(index, len(rows), f"已评分 {index}/{len(rows)}")
    ctx.log(f"完成 {len(rows)} 条岗位评分", module="analyze")
    return {"scored": len(rows)}


def demo_pipeline(ctx: TaskContext, payload: dict) -> dict:
    """自检用任务：模拟一个分步流程，用于验证进度、暂停、恢复、停止、失败。

    payload: {"steps": 20, "step_seconds": 0.3, "fail_at": 0}
    """
    steps = int(payload.get("steps") or 20)
    step_seconds = float(payload.get("step_seconds") or 0.3)
    fail_at = int(payload.get("fail_at") or 0)
    start = ctx.resume_from
    ctx.progress(start, steps, "运行中")
    ctx.log(f"演示任务开始，共 {steps} 步，从第 {start} 步继续", module="demo")

    for index in range(start, steps):
        if fail_at and index + 1 == fail_at:
            raise RuntimeError(f"演示任务在第 {index + 1} 步按 payload 要求失败")
        ctx.checkpoint()
        ctx.sleep(step_seconds)
        ctx.progress(index + 1, steps, f"已完成 {index + 1}/{steps}")
        if (index + 1) % 5 == 0 or index + 1 == steps:
            ctx.log(f"进度 {index + 1}/{steps}", module="demo", payload={"step": index + 1})

    return {"steps": steps, "finished_from": start}


def register_all(manager: TaskManager) -> None:
    manager.register("demo.pipeline", demo_pipeline)
    manager.register("collect.guopin", collect_guopin)
    manager.register("collect.ncss", collect_ncss)
    manager.register("collect.bigtech", collect_bigtech)
    manager.register("collect.wechat", collect_wechat)
    manager.register("collect.workday", collect_workday)
    manager.register("analyze.jobs", analyze_jobs)
    # 后续阶段在此注册：
    # manager.register("generate.greeting", ...)
    # manager.register("track.replies", ...)
    # manager.register("resume.tailor", ...)


def available_types() -> list[dict[str, Any]]:
    return [
        {"type": "demo.pipeline", "label": "演示流程（自检用）",
         "payload": {"steps": 20, "step_seconds": 0.3, "fail_at": 0}},
        {"type": "collect.guopin", "label": "去国聘找岗位",
         "payload": {"limit": 25, "reset": False}},
        {"type": "collect.workday", "label": "去外企官网找岗位",
         "payload": {"limit": 15, "reset": False}},
        {"type": "collect.ncss", "label": "去 24365 国家平台找岗位",
         "payload": {"limit": 60, "reset": False}},
        {"type": "collect.bigtech", "label": "去大厂校招官网找岗位",
         "payload": {"limit": 40, "reset": False}},
        {"type": "collect.wechat", "label": "去公众号找校招公告",
         "payload": {"limit": 20, "reset": False}},
        {"type": "analyze.jobs", "label": "重新计算匹配度", "payload": {}},
    ]
