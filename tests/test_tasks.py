"""S4 任务系统测试：成功、暂停/恢复、停止、失败、周期重排、重启恢复。"""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
import db  # noqa: E402
import handlers  # noqa: E402
import tasks as tasks_mod  # noqa: E402


class TaskSystemTest(unittest.TestCase):
    def setUp(self) -> None:
        common.fresh_db("tasks")
        self.manager = tasks_mod.TaskManager(poll_interval=0.05)
        handlers.register_all(self.manager)

    def tearDown(self) -> None:
        self.manager.shutdown()

    # ---- 工具
    def task(self, task_id: int) -> dict:
        return self.manager.get(task_id) or {}

    def wait_status(self, task_id: int, status: str, timeout: float = 25.0) -> dict:
        return common.wait_for(
            lambda: self.task(task_id) if self.task(task_id).get("status") == status else None,
            timeout=timeout, message=f"任务 {task_id} 未进入 {status}")

    # ---- 用例
    def test_success_path(self) -> None:
        self.manager.start()
        task_id = self.manager.create("demo.pipeline", {"steps": 6, "step_seconds": 0.02})
        task = self.wait_status(task_id, "done")
        self.assertEqual(task["progress"], 6)
        self.assertTrue(task["started_at"])
        self.assertTrue(task["finished_at"])
        logs = self.manager.logs(task_id)
        self.assertGreaterEqual(len(logs), 3)
        self.assertEqual(logs[0]["level"], "info")
        self.assertIn("任务开始", logs[0]["message"])

    def test_pause_then_resume(self) -> None:
        self.manager.start()
        task_id = self.manager.create("demo.pipeline", {"steps": 40, "step_seconds": 0.05})

        common.wait_for(lambda: self.task(task_id)["progress"] >= 3,
                        message="任务未能开始推进")
        self.manager.pause(task_id)
        self.wait_status(task_id, "paused")

        # 暂停是协作式的：处理器在下一个检查点（最多 0.5 秒）才会真正停下
        time.sleep(1.0)
        frozen = self.task(task_id)["progress"]
        self.assertLess(frozen, 40)
        time.sleep(1.2)
        self.assertEqual(self.task(task_id)["progress"], frozen, "暂停后进度仍在增长")

        self.manager.resume(task_id)
        task = self.wait_status(task_id, "done")
        self.assertEqual(task["result"]["finished_from"], frozen)
        self.assertEqual(task["progress"], 40)

    def test_stop_is_terminal_and_retry_restarts(self) -> None:
        self.manager.start()
        task_id = self.manager.create("demo.pipeline", {"steps": 60, "step_seconds": 0.05})
        common.wait_for(lambda: self.task(task_id)["progress"] >= 2, message="任务未能开始推进")

        self.manager.stop(task_id)
        self.wait_status(task_id, "stopped")
        frozen = self.task(task_id)["progress"]
        time.sleep(1.0)
        self.assertEqual(self.task(task_id)["progress"], frozen)
        self.assertLess(frozen, 60)

        self.manager.retry(task_id)
        task = self.wait_status(task_id, "done")
        self.assertEqual(task["progress"], 60)

    def test_failure_records_error(self) -> None:
        self.manager.start()
        task_id = self.manager.create("demo.pipeline", {"steps": 10, "step_seconds": 0.01, "fail_at": 2})
        task = self.wait_status(task_id, "failed")
        self.assertIn("RuntimeError", task["error"])
        self.assertIn("第 2 步", task["error"])
        levels = [row["level"] for row in self.manager.logs(task_id)]
        self.assertIn("error", levels)

    def test_repeat_task_is_rescheduled(self) -> None:
        self.manager.start()
        task_id = self.manager.create("demo.pipeline", {"steps": 2, "step_seconds": 0.01},
                                      repeat_seconds=1)
        self.wait_status(task_id, "done")
        common.wait_for(
            lambda: common.count("SELECT COUNT(*) FROM tasks WHERE type = ? AND status = 'queued'",
                                 ("demo.pipeline",)) >= 1,
            message="周期任务未重新入队")

    def test_running_task_recovered_as_interrupted(self) -> None:
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO tasks (type, payload_json, status, created_at) VALUES (?,?,?,?)",
                ("demo.pipeline", "{}", "running", db.now()))
            conn.commit()
        self.manager.start()
        status = common.wait_for(
            lambda: self.task(1)["status"] if self.task(1)["status"] == "interrupted" else None,
            timeout=5, message="残留 running 任务未被标记为 interrupted")
        self.assertEqual(status, "interrupted")

    def test_unknown_task_type_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.create("no.such.task", {})

    def test_rate_limit_is_enforced(self) -> None:
        def timed_handler(ctx, payload):
            stamps = []
            for _ in range(3):
                ctx.rate_limit()
                stamps.append(time.monotonic())
            return {"gaps": [round(stamps[i + 1] - stamps[i], 3) for i in range(len(stamps) - 1)]}

        self.manager.register("test.timed", timed_handler)
        self.manager.start()
        task_id = self.manager.create("test.timed", {}, rate_limit_per_min=600)  # 0.1 秒一次
        task = self.wait_status(task_id, "done")
        gaps = task["result"]["gaps"]
        self.assertEqual(len(gaps), 2)
        for gap in gaps:
            self.assertGreaterEqual(gap, 0.09, f"限流间隔不足：{gaps}")
        self.assertEqual(task["rate_limit_per_min"], 600)

    def test_shutdown_interrupts_running_task(self) -> None:
        self.manager.start()
        task_id = self.manager.create("demo.pipeline", {"steps": 200, "step_seconds": 0.05})
        common.wait_for(lambda: self.task(task_id)["progress"] >= 1, message="任务未能开始")
        self.manager.shutdown()
        status = common.wait_for(
            lambda: self.task(task_id)["status"] if self.task(task_id)["status"] == "interrupted" else None,
            timeout=10, message="调度器停止后任务未标记为 interrupted")
        self.assertEqual(status, "interrupted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
