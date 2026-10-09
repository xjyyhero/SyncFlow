"""Real child processes verify concurrency, timeout, cancellation and cleanup."""

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import test_worker
from app import repository
from app.csv_source import read_csv
from app.database import connection
from app.worker import supervise_job, worker_settings, write_batch
from reporting import EvidenceCase


class WorkerControlTests(EvidenceCase):
    setUpClass = classmethod(test_worker.WorkerTests.setUpClass.__func__)
    setUp = test_worker.WorkerTests.setUp
    create = test_worker.WorkerTests.create
    create_rows = test_worker.WorkerTests.create_rows
    csv_rows = test_worker.WorkerTests.csv_rows
    row = test_worker.WorkerTests.row
    records = test_worker.WorkerTests.records

    def fifo(self, job_id):
        path = Path(self.row(job_id)["stored_file_path"])
        path.unlink()
        os.mkfifo(path)

    def wait_status(self, job_id, statuses, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = self.row(job_id)
            if row["status"] in statuses:
                return row
            time.sleep(0.03)
        self.fail(f"job {job_id} remained {row['status']}, expected {statuses}")

    def start_worker(self, log, concurrency=2, timeout=3):
        process = subprocess.Popen(
            [sys.executable, "-m", "app.worker"],
            stdout=log,
            stderr=log,
            env={
                **os.environ,
                "WORKER_CONCURRENCY": str(concurrency),
                "JOB_TIMEOUT_SECONDS": str(timeout),
            },
        )

        def cleanup():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        self.addCleanup(cleanup)
        return process

    def assert_children_reaped(self, log):
        log.flush()
        log.seek(0)
        pids = re.findall(r"process_id=(\d+) started", log.read())
        self.assertTrue(pids)
        for pid in pids:
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pid), 0)

    def test_settings_default_and_validation(self):
        """默认并发按 CPU 限定在 2–4；显式配置生效，非正整数在启动时拒绝。"""
        with patch.dict(os.environ):
            os.environ.pop("WORKER_CONCURRENCY", None)
            os.environ.pop("JOB_TIMEOUT_SECONDS", None)
            os.environ.pop("WORKER_SHUTDOWN_TIMEOUT_SECONDS", None)
            for cpus, expected in ((None, 2), (1, 2), (3, 3), (64, 4)):
                with patch("app.worker.os.cpu_count", return_value=cpus):
                    self.assertEqual(worker_settings(), (expected, 300, 30))
            os.environ.update(WORKER_CONCURRENCY="3", JOB_TIMEOUT_SECONDS="7")
            self.assertEqual(worker_settings(), (3, 7, 30))
            for name in (
                "WORKER_CONCURRENCY",
                "JOB_TIMEOUT_SECONDS",
                "WORKER_SHUTDOWN_TIMEOUT_SECONDS",
            ):
                for invalid in ("0", "-1", "1.5", "abc", ""):
                    with (
                        patch.dict(os.environ, {name: invalid}),
                        self.assertRaises(ValueError),
                    ):
                        worker_settings()

    def test_cancel_api_state_contract(self):
        """取消 API 覆盖八状态、404 和 409；终态/重复请求不改数据。"""
        for status in repository.JOB_STATUSES:
            job_id = self.create()
            with connection() as db, db.cursor() as cursor:
                cursor.execute(
                    "UPDATE sync_jobs SET status=%s WHERE id=%s", (status, job_id)
                )
            before = self.row(job_id)
            response = self.client.post(f"/api/v1/jobs/{job_id}/cancel")
            if status in {"PENDING", "RETRYING", "RUNNING"}:
                self.assertEqual(response.status_code, 200)
                expected = "CANCELING" if status == "RUNNING" else "CANCELED"
                self.assertEqual(response.json()["data"]["status"], expected)
                self.assertEqual(
                    self.row(job_id)["finished_at"] is None, expected == "CANCELING"
                )
                again = self.client.post(f"/api/v1/jobs/{job_id}/cancel")
                self.assertEqual(again.status_code, 409)
            else:
                self.assertEqual(response.status_code, 409)
                self.assertEqual(
                    response.json()["error"]["code"], "INVALID_JOB_TRANSITION"
                )
                self.assertEqual(self.row(job_id), before)
        response = self.client.post("/api/v1/jobs/missing/cancel")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "JOB_NOT_FOUND")
        schema = self.client.get("/openapi.json").json()
        self.assertIn(
            "409", schema["paths"]["/api/v1/jobs/{job_id}/cancel"]["post"]["responses"]
        )

    def test_concurrency_timeout_releases_slots_and_child_processes(self):
        """两个 FIFO 读取同时阻塞，第三任务等待；超时杀进程释放槽位，重复消息不执行。"""
        blocked = [self.create(), self.create()]
        for job_id in blocked:
            self.fifo(job_id)
        normal = self.create()
        self.queue.rpush(self.key, blocked[0], normal)
        with tempfile.TemporaryFile(mode="w+") as log:
            process = self.start_worker(log)
            for job_id in blocked:
                self.wait_status(job_id, {"RUNNING"})
            self.assertEqual(self.row(normal)["status"], "PENDING")
            for job_id in blocked:
                row = self.wait_status(job_id, {"FAILED"})
                self.assertEqual(row["last_error_code"], "JOB_TIMEOUT")
                self.assertIsNotNone(row["finished_at"])
                self.assertEqual(self.records(job_id), [])
            self.wait_status(normal, {"SUCCESS"})
            self.assertEqual(len(self.records(normal)), 1)
            self.assertIsNone(process.poll())
            process.terminate()
            process.wait(timeout=5)
            self.assertEqual(process.returncode, 0)
            self.assert_children_reaped(log)

    def test_running_cancel_preserves_committed_batch_and_other_jobs(self):
        """运行中取消实际阻塞子进程，保留已提交批次；其他任务独立成功且槽位可复用。"""
        job_id = self.create_rows(self.csv_rows(501))
        parsed = read_csv(self.row(job_id)["stored_file_path"])
        with connection() as db:
            repository.start_job(db, job_id)
        write_batch(job_id, parsed.rows[:250], 0, 501)
        # Fixture simulates a task with prior committed progress before a blocked read.
        with connection() as db, db.cursor() as cursor:
            cursor.execute(
                "UPDATE sync_jobs SET status='PENDING' WHERE id=%s", (job_id,)
            )
        self.fifo(job_id)
        normal = self.create()
        with tempfile.TemporaryFile(mode="w+") as log:
            process = self.start_worker(log, timeout=10)
            self.wait_status(job_id, {"RUNNING"})
            response = self.client.post(f"/api/v1/jobs/{job_id}/cancel")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["status"], "CANCELING")
            row = self.wait_status(job_id, {"CANCELED"})
            self.assertEqual(row["last_error_code"], "JOB_CANCELED")
            self.assertEqual(
                (row["total_records"], row["success_records"], row["failed_records"]),
                (501, 250, 0),
            )
            self.assertEqual(len(self.records(job_id)), 250)
            self.wait_status(normal, {"SUCCESS"})
            later = self.create()
            self.wait_status(later, {"SUCCESS"})
            self.assertEqual(len(self.records(normal)), 1)
            self.assertEqual(len(self.records(later)), 1)
            process.terminate()
            process.wait(timeout=5)
            self.assertEqual(process.returncode, 0)
            self.assert_children_reaped(log)

    def test_killed_transaction_rolls_back_before_failure(self):
        """真实子进程写入未提交记录后阻塞；超时终止会回滚，不留下业务数据。"""
        job_id = self.create()
        script = """
import sys, time
from app.database import connection
with connection() as db, db.cursor() as cursor:
    cursor.execute("INSERT INTO sync_records (job_id, external_id, name, amount, record_date) VALUES (%s,'UNCOMMITTED','test',1,'2026-01-01')", (sys.argv[1],))
    print("transaction-open", flush=True)
    time.sleep(60)
"""
        original = subprocess.Popen
        children = []
        with tempfile.TemporaryFile(mode="w+") as log:

            def start(*args, **kwargs):
                child = original(
                    [sys.executable, "-c", script, job_id], stdout=log, stderr=log
                )
                children.append(child)
                return child

            with patch("app.worker.subprocess.Popen", side_effect=start):
                supervise_job(job_id, 2)
            log.seek(0)
            self.assertIn("transaction-open", log.read())
        self.assertIsNotNone(children[0].poll())
        self.assertEqual(self.row(job_id)["status"], "FAILED")
        self.assertEqual(self.row(job_id)["last_error_code"], "JOB_TIMEOUT")
        self.assertEqual(self.records(job_id), [])

    def test_child_start_and_exit_failures_are_recorded(self):
        """启动失败及进程无结果退出均记 FAILED，不遗留 RUNNING。"""
        failed_start = self.create()
        with patch("app.worker.subprocess.Popen", side_effect=OSError("private path")):
            supervise_job(failed_start, 3)
        self.assertEqual(
            self.row(failed_start)["last_error_code"], "WORKER_CONTROL_FAILED"
        )
        failed_exit = self.create()
        original = subprocess.Popen
        with patch(
            "app.worker.subprocess.Popen",
            side_effect=lambda *a, **kw: original(
                [sys.executable, "-c", "raise SystemExit(7)"]
            ),
        ):
            supervise_job(failed_exit, 3)
        row = self.row(failed_exit)
        self.assertEqual(row["status"], "FAILED")
        self.assertEqual(row["last_error_code"], "WORKER_PROCESS_EXITED")
        self.assertNotIn("private", self.row(failed_start)["last_error_message"])
