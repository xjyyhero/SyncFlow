"""Worker integration checks using real Redis and MySQL, isolated from development."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app import repository
from app.database import connection, initialize
from app.jobs import queue_client
from app.main import app
from app.worker import process_job
from fastapi.testclient import TestClient
from reporting import EvidenceCase


class WorkerTests(EvidenceCase):
    @classmethod
    def setUpClass(cls):
        if (
            os.environ.get("MYSQL_HOST"),
            os.environ.get("MYSQL_DATABASE"),
            os.environ.get("REDIS_ADDR"),
        ) != ("mysql-test", "syncflow_test", "redis-test:6379"):
            raise RuntimeError("Worker tests require isolated MySQL and Redis")
        initialize()

    def setUp(self):
        self.evidence = []
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.key = f"syncflow:worker-test:{uuid4()}"
        env = patch.dict(
            os.environ, {"UPLOAD_DIR": str(self.root), "REDIS_JOB_QUEUE": self.key}
        )
        env.start()
        self.addCleanup(env.stop)
        self.queue = queue_client()
        self.addCleanup(self.queue.close)
        self.addCleanup(self.queue.delete, self.key)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        with connection() as db, db.cursor() as cursor:
            for table in ("sync_errors", "sync_records", "sync_jobs"):
                cursor.execute(f"DELETE FROM {table}")

    def create(self):
        response = self.client.post(
            "/api/v1/jobs",
            files={"file": ("worker.csv", b"external_id,name\nA1,test\n")},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["data"]["id"]

    def row(self, job_id):
        with connection() as db:
            return repository.get_job(db, job_id)

    def test_running_commit_and_success_are_visible(self):
        """Worker 先提交 RUNNING 和首次开始时间，再读文件并提交 SUCCESS。"""
        job_id = self.create()
        original_open = Path.open
        checked = []

        def inspect_open(path, *args, **kwargs):
            row = self.row(job_id)
            self.assertEqual(row["status"], "RUNNING")
            self.assertIsNotNone(row["started_at"])
            self.assertIsNone(row["finished_at"])
            checked.append(True)
            return original_open(path, *args, **kwargs)

        with patch("app.worker.Path.open", inspect_open):
            self.assertTrue(process_job(job_id))
        self.assertEqual(checked, [True])
        row = self.row(job_id)
        self.assertEqual(row["status"], "SUCCESS")
        self.assertGreaterEqual(row["finished_at"], row["started_at"])
        self.assertEqual(row["total_records"], 0)
        self.assertFalse(process_job(job_id))
        repeated = self.row(job_id)
        self.assertEqual(repeated["started_at"], row["started_at"])
        self.assertEqual(repeated["finished_at"], row["finished_at"])

    def test_missing_and_uncontrolled_files_fail_safely(self):
        """文件缺失或路径位于上传目录之外时 FAILED，写入文件级错误。"""
        missing = self.create()
        Path(self.row(missing)["stored_file_path"]).unlink()
        outside = self.create()
        with connection() as db, db.cursor() as cursor:
            cursor.execute(
                "UPDATE sync_jobs SET stored_file_path='/etc/passwd' WHERE id=%s",
                (outside,),
            )
        for job_id in (missing, outside):
            self.assertFalse(process_job(job_id))
            row = self.row(job_id)
            self.assertEqual(row["status"], "FAILED")
            self.assertEqual(row["last_error_code"], "WORKER_PROCESSING_FAILED")
            self.assertIsNotNone(row["finished_at"])
            self.assertNotIn("/etc/passwd", row["last_error_message"])
            with connection() as db, db.cursor() as cursor:
                cursor.execute(
                    "SELECT error_code FROM sync_errors WHERE job_id=%s", (job_id,)
                )
                self.assertEqual(cursor.fetchall(), [("WORKER_PROCESSING_FAILED",)])

    def test_missing_and_nonpending_jobs_are_skipped(self):
        """不存在、FAILED、RUNNING 任务都不能被重复认领或误标成功。"""
        self.assertFalse(process_job(str(uuid4())))
        for status in ("FAILED", "RUNNING"):
            job_id = self.create()
            with connection() as db, db.cursor() as cursor:
                cursor.execute(
                    "UPDATE sync_jobs SET status=%s WHERE id=%s", (status, job_id)
                )
            self.assertFalse(process_job(job_id))
            self.assertEqual(self.row(job_id)["status"], status)

    def test_independent_worker_consumes_real_queue_and_stops(self):
        """独立 Worker 进程消费真实队列，重复/无效消息不影响后续任务，SIGTERM 正常退出。"""
        first = self.create()
        self.queue.rpush(self.key, first, str(uuid4()))
        second = self.create()
        with tempfile.TemporaryFile(mode="w+") as log:
            worker = subprocess.Popen(
                [sys.executable, "-m", "app.worker"], stdout=log, stderr=log
            )
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if (
                        self.row(second)["status"] == "SUCCESS"
                        and self.queue.llen(self.key) == 0
                    ):
                        break
                    if worker.poll() is not None:
                        break
                    time.sleep(0.05)
                self.assertEqual(self.row(first)["status"], "SUCCESS")
                self.assertEqual(self.row(second)["status"], "SUCCESS")
                self.assertEqual(self.queue.llen(self.key), 0)
                self.assertIsNone(worker.poll())
                worker.terminate()
                worker.wait(timeout=5)
                self.assertEqual(worker.returncode, 0)
                log.seek(0)
                output = log.read()
                for text in (
                    f"job_id={first} received",
                    f"job_id={first} status=RUNNING",
                    f"job_id={first} status=SUCCESS",
                    "skipped",
                    "upload readable",
                    "Stopped",
                ):
                    self.assertIn(text, output)
            finally:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=5)
