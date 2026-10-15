"""任务系统：SQLite 任务表 + 后台调度线程 + 可暂停/可停止的状态机。

设计要点
- 单调度线程串行执行：避免同一站点并发请求触发风控，也简化限流。
- 处理器通过 TaskContext 上报进度、写日志、检查暂停/停止信号。
- 暂停与停止都是"协作式"的：处理器在检查点上让出控制权，最多损失一个检查点的进度。
- 进程重启后，残留的 running 任务标记为 interrupted，由用户决定是否续跑。
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, Callable

import db


class TaskCancelled(Exception):
    """处理器在检查点收到停止信号。"""


class TaskPaused(Exception):
    """处理器在检查点收到暂停信号。"""


class TaskShutdown(Exception):
    """调度器正在停止，处理器应尽快让出控制权。"""


def _now() -> datetime:
    return datetime.now()


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class TaskContext:
    """处理器与调度器之间的唯一接口。"""

    def __init__(self, manager: "TaskManager", task_id: int, rate_limit_per_min: int, resume_from: int = 0):
        self.manager = manager
        self.task_id = task_id
        self.rate_limit_per_min = max(0, int(rate_limit_per_min or 0))
        self.resume_from = max(0, int(resume_from or 0))
        self.progress_count = self.resume_from
        self._last_request_at = 0.0
        self._last_pause_check = 0.0
        self._pause_check_interval = 0.5  # 秒：限制 DB 查询频率

    # ---- 日志与进度
    def log(self, message: str, level: str = "info", module: str = "", payload: dict | None = None) -> None:
        try:
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO task_logs (task_id, ts, level, module, message, payload) VALUES (?,?,?,?,?,?)",
                    (self.task_id, db.now(), level, module or self.manager.current_type or "", message,
                     json.dumps(payload or {}, ensure_ascii=False)),
                )
                conn.commit()
        except Exception:
            # 写日志失败绝不能中断任务本身
            pass

    def progress(self, done: int | None = None, total: int | None = None, message: str = "") -> None:
        if done is not None:
            self.progress_count = int(done)
        assignments = ["progress = ?"]
        values: list[Any] = [self.progress_count]
        if total is not None:
            assignments.append("total = ?")
            values.append(int(total))
        if message:
            assignments.append("message = ?")
            values.append(message)
        values.append(self.task_id)
        with db.connect() as conn:
            conn.execute(f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?", values)
            conn.commit()

    # ---- 控制信号
    def checkpoint(self) -> None:
        """检查暂停/停止信号。高频循环里调用；内部有节流。"""
        now_ts = time.monotonic()
        if now_ts - self._last_pause_check < self._pause_check_interval:
            return
        self._last_pause_check = now_ts
        if self.manager._stop_event.is_set():
            raise TaskShutdown("调度器正在停止")
        with db.connect() as conn:
            row = conn.execute(
                "SELECT paused, cancel_requested FROM tasks WHERE id = ?", (self.task_id,)
            ).fetchone()
        if row is None:
            raise TaskCancelled("任务记录已不存在")
        if row["cancel_requested"]:
            raise TaskCancelled("用户请求停止")
        if row["paused"]:
            raise TaskPaused("用户请求暂停")

    def sleep(self, seconds: float) -> None:
        """可被暂停/停止打断的等待。"""
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.checkpoint()
            time.sleep(min(0.2, remaining))

    def rate_limit(self) -> None:
        """按 rate_limit_per_min 控制请求间隔；这是对目标站点的基本礼貌。"""
        if self.rate_limit_per_min <= 0:
            return
        interval = 60.0 / self.rate_limit_per_min
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < interval:
            self.sleep(interval - elapsed)
        self._last_request_at = time.monotonic()


class TaskManager:
    """任务注册、调度与状态机。"""

    def __init__(self, poll_interval: float = 1.0):
        self.handlers: dict[str, Callable[[TaskContext, dict], Any]] = {}
        self.poll_interval = poll_interval
        self.current_type: str = ""
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    # ---- 注册与创建
    def register(self, task_type: str, handler: Callable[[TaskContext, dict], Any]) -> None:
        self.handlers[task_type] = handler

    def create(self, task_type: str, payload: dict | None = None, priority: int = 0,
               rate_limit_per_min: int | None = None, repeat_seconds: int = 0) -> int:
        if task_type not in self.handlers:
            raise ValueError(f"未注册的任务类型：{task_type}")
        limit = int(rate_limit_per_min if rate_limit_per_min is not None
                    else db.get_setting("rate_limit_per_min", 6) or 6)
        with db.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO tasks (type, payload_json, status, priority, rate_limit_per_min,
                   repeat_seconds, created_at, next_run_at) VALUES (?,?,?,?,?,?,?,?)""",
                (task_type, json.dumps(payload or {}, ensure_ascii=False), "queued",
                 int(priority), limit, int(repeat_seconds), db.now(), db.now()),
            )
            task_id = int(cursor.lastrowid)
            conn.commit()
        db.log("task", f"创建任务 #{task_id} ({task_type})", {"payload": payload or {}})
        return task_id

    # ---- 查询
    def list(self, limit: int = 100, status: str = "") -> list[dict]:
        sql = "SELECT * FROM tasks"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with db.connect() as conn:
            rows = db.rows_to_dicts(conn.execute(sql, params).fetchall())
        for row in rows:
            row["payload"] = _load_json(row.pop("payload_json", "{}"), {})
            row["result"] = _load_json(row.pop("result_json", "{}"), {})
        return rows

    def get(self, task_id: int) -> dict | None:
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (int(task_id),)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["payload"] = _load_json(data.pop("payload_json", "{}"), {})
        data["result"] = _load_json(data.pop("result_json", "{}"), {})
        return data

    def logs(self, task_id: int, limit: int = 300, after_id: int = 0) -> list[dict]:
        with db.connect() as conn:
            rows = db.rows_to_dicts(conn.execute(
                "SELECT * FROM task_logs WHERE task_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
                (int(task_id), int(after_id), int(limit)),
            ).fetchall())
        for row in rows:
            row["payload"] = _load_json(row.pop("payload", "{}"), {})
        return rows

    # ---- 控制
    def pause(self, task_id: int) -> dict:
        return self._control(task_id, {"paused": 1})

    def resume(self, task_id: int) -> dict:
        task = self.get(task_id)
        if task is None:
            raise ValueError("任务不存在")
        resume_from = task["progress"] if task["status"] == "paused" else 0
        with db.connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'queued', paused = 0, cancel_requested = 0, "
                "resume_from = ?, next_run_at = ? WHERE id = ?",
                (int(resume_from), db.now(), int(task_id)),
            )
            conn.commit()
        db.log("task", f"任务 #{task_id} 已恢复（从第 {resume_from} 步继续）")
        return self.get(task_id) or {}

    def stop(self, task_id: int) -> dict:
        task = self.get(task_id)
        if task is None:
            raise ValueError("任务不存在")
        if task["status"] in {"queued", "failed", "interrupted", "stopped", "done"}:
            with db.connect() as conn:
                conn.execute(
                    "UPDATE tasks SET status = 'stopped', paused = 0, cancel_requested = 1, "
                    "finished_at = ? WHERE id = ?", (db.now(), int(task_id)),
                )
                conn.commit()
            db.log("task", f"任务 #{task_id} 已停止")
        else:
            self._control(task_id, {"cancel_requested": 1, "paused": 0})
        return self.get(task_id) or {}

    def _control(self, task_id: int, fields: dict[str, int]) -> dict:
        task = self.get(task_id)
        if task is None:
            raise ValueError("任务不存在")
        assignments = ", ".join(f"{key} = ?" for key in fields)
        status = "paused" if fields.get("paused") else task["status"]
        with db.connect() as conn:
            conn.execute(
                f"UPDATE tasks SET {assignments}, status = ? WHERE id = ?",
                [*fields.values(), status, int(task_id)],
            )
            conn.commit()
        db.log("task", f"任务 #{task_id} 状态更新：{fields}")
        return self.get(task_id) or {}

    def retry(self, task_id: int) -> dict:
        """把失败/中断/停止的任务重新入队。"""
        task = self.get(task_id)
        if task is None:
            raise ValueError("任务不存在")
        with db.connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'queued', error = '', paused = 0, cancel_requested = 0, "
                "resume_from = 0, started_at = '', finished_at = '', next_run_at = ? WHERE id = ?",
                (db.now(), int(task_id)),
            )
            conn.commit()
        db.log("task", f"任务 #{task_id} 重新入队")
        return self.get(task_id) or {}

    # ---- 调度
    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            recovered = self._recover_interrupted()
            if recovered:
                db.log("task", f"启动时恢复：{recovered} 个中断任务被标记为 interrupted")
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, name="qiuzhao-scheduler", daemon=True)
            self._thread.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _recover_interrupted(self) -> int:
        with db.connect() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET status = 'interrupted', finished_at = ? "
                "WHERE status IN ('running', 'paused')", (db.now(),),
            )
            conn.commit()
        return cursor.rowcount or 0

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                task = self._claim_next()
                if task is None:
                    self._stop_event.wait(self.poll_interval)
                    continue
                self._run(task)
            except Exception:  # 调度线程绝不能因单次异常退出
                db.log("task", "调度线程异常：" + traceback.format_exc()[-1500:], {"level": "error"})
                self._stop_event.wait(2)

    def _claim_next(self) -> dict | None:
        with db.connect() as conn:
            row = conn.execute(
                """SELECT * FROM tasks
                   WHERE status = 'queued' AND paused = 0 AND cancel_requested = 0
                     AND (next_run_at = '' OR next_run_at <= ?)
                   ORDER BY priority DESC, id ASC LIMIT 1""",
                (db.now(),),
            ).fetchone()
            if row is None:
                return None
            task = dict(row)
            updated = conn.execute(
                "UPDATE tasks SET status = 'running', started_at = ? WHERE id = ? AND status = 'queued'",
                (db.now(), task["id"]),
            )
            conn.commit()
            if updated.rowcount == 0:
                return None
            task["status"] = "running"
            return task

    def _run(self, task: dict) -> None:
        task_id = int(task["id"])
        self.current_type = task["type"]
        handler = self.handlers.get(task["type"])
        payload = _load_json(task.get("payload_json", "{}"), {})
        ctx = TaskContext(self, task_id, task.get("rate_limit_per_min") or 0, task.get("resume_from") or 0)

        ctx.log(f"任务开始：{task['type']}", level="info", module=task["type"],
                payload={"payload": payload, "resume_from": ctx.resume_from})
        try:
            if handler is None:
                raise RuntimeError(f"未注册的任务类型：{task['type']}")
            result = handler(ctx, payload)
            ctx.log("任务完成", level="info", module=task["type"], payload=result or {})
            with db.connect() as conn:
                conn.execute(
                    "UPDATE tasks SET status = 'done', finished_at = ?, result_json = ?, message = ? WHERE id = ?",
                    (db.now(), json.dumps(result or {}, ensure_ascii=False, default=str), "已完成", task_id),
                )
                conn.commit()
            self._schedule_repeat(task)
        except TaskPaused:
            with db.connect() as conn:
                conn.execute(
                    "UPDATE tasks SET status = 'paused', paused = 1, progress = ?, message = ? WHERE id = ?",
                    (ctx.progress_count, "已暂停", task_id),
                )
                conn.commit()
            ctx.log(f"任务在第 {ctx.progress_count} 步暂停", level="warn", module=task["type"])
        except TaskCancelled:
            ctx.log(f"任务在第 {ctx.progress_count} 步被停止", level="warn", module=task["type"])
            with db.connect() as conn:
                conn.execute(
                    "UPDATE tasks SET status = 'stopped', finished_at = ?, progress = ?, message = ? WHERE id = ?",
                    (db.now(), ctx.progress_count, "已停止", task_id),
                )
                conn.commit()
        except TaskShutdown:
            ctx.log(f"调度器停止，任务在第 {ctx.progress_count} 步中断", level="warn",
                    module=task["type"])
            with db.connect() as conn:
                conn.execute(
                    "UPDATE tasks SET status = 'interrupted', finished_at = ?, progress = ?, message = ? WHERE id = ?",
                    (db.now(), ctx.progress_count, "调度器停止，任务中断", task_id),
                )
                conn.commit()
        except Exception as exc:
            detail = traceback.format_exc()
            # 先写日志再改状态：否则读日志的人可能在状态已变、日志还没落库的
            # 空档里读到"失败但没有任何错误日志"（这个竞态曾让测试偶发失败）。
            ctx.log(f"任务失败：{type(exc).__name__}: {exc}", level="error",
                    module=task["type"], payload={"traceback": detail[-1500:]})
            with db.connect() as conn:
                conn.execute(
                    "UPDATE tasks SET status = 'failed', finished_at = ?, error = ?, message = ? WHERE id = ?",
                    (db.now(), detail[-4000:], f"{type(exc).__name__}: {exc}", task_id),
                )
                conn.commit()
        finally:
            self.current_type = ""

    def _schedule_repeat(self, task: dict) -> None:
        repeat = int(task.get("repeat_seconds") or 0)
        if repeat <= 0:
            return
        next_run = _stamp(_now() + timedelta(seconds=repeat))
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO tasks (type, payload_json, status, priority, rate_limit_per_min,
                   repeat_seconds, created_at, next_run_at) VALUES (?,?,?,?,?,?,?,?)""",
                (task["type"], task.get("payload_json", "{}"), "queued", task.get("priority", 0),
                 task.get("rate_limit_per_min", 6), repeat, db.now(), next_run),
            )
            conn.commit()
        db.log("task", f"周期任务已排下一次：{next_run}",
               {"type": task["type"], "repeat_seconds": repeat})


def _load_json(raw: Any, default: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw) if raw else default
    except (TypeError, json.JSONDecodeError):
        return default
