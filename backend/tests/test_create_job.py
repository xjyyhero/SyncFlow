"""Exercise the real POST route against isolated MySQL, Redis and filesystem."""

import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from app import repository
from app.database import connection, initialize
from app.jobs import queue_client
from app.main import app
from fastapi.testclient import TestClient
from mysql.connector import Error as MySQLError
from redis.exceptions import TimeoutError as RedisTimeoutError
from reporting import EvidenceCase


class CreateJobTests(EvidenceCase):
    @classmethod
    def setUpClass(cls):
        if (
            os.environ.get("MYSQL_HOST"),
            os.environ.get("MYSQL_DATABASE"),
            os.environ.get("REDIS_ADDR"),
        ) != (
            "mysql-test",
            "syncflow_test",
            "redis-test:6379",
        ):
            raise RuntimeError("Creation tests require isolated MySQL and Redis")
        initialize()

    def setUp(self):
        self.evidence = []
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.key = f"syncflow:test:{uuid4()}"
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

    def rows(self, table="sync_jobs"):
        assert table in ("sync_jobs", "sync_errors")
        with connection() as db, db.cursor(dictionary=True) as cursor:
            cursor.execute(f"SELECT * FROM {table} ORDER BY id")
            return cursor.fetchall()

    def upload(self, filename="input.csv", content=b"id,name\n1,test\n", name=None):
        return self.client.post(
            "/api/v1/jobs",
            files={"file": (filename, content, "text/csv")},
            data={} if name is None else {"name": name},
        )

    def test_real_upload_persists_file_hash_and_queue(self):
        """真实上传：201/PENDING、原名和 SHA-256 入库，Redis 中仅存任务 ID。"""
        content = "external_id,name\nA01,小米\n".encode()
        response = self.upload("原始.csv", content, "人工命名")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(set(body), {"data", "meta"})
        self.assertEqual(set(body["data"]), {"id", "name", "status", "created_at"})
        self.assertEqual(body["data"]["status"], "PENDING")
        self.assertEqual(body["data"]["name"], "人工命名")
        self.assertTrue(body["data"]["created_at"].endswith("Z"))
        row = self.rows()[0]
        self.assertEqual(str(UUID(row["id"])), body["data"]["id"])
        self.assertEqual(row["source_file_name"], "原始.csv")
        self.assertEqual(row["file_sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(Path(row["stored_file_path"]).read_bytes(), content)
        self.assertEqual(Path(row["stored_file_path"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.queue.lrange(self.key, 0, -1), [row["id"]])
        self.assertIsNone(row["last_error_code"])
        self.assertIsNone(row["started_at"])
        self.assertEqual(self.rows("sync_errors"), [])
        self.assertNotIn("stored_file_path", response.text)
        detail = self.client.get(f"/api/v1/jobs/{row['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["name"], "人工命名")
        listing = self.client.get("/api/v1/jobs?status=PENDING").json()
        self.assertEqual([r["id"] for r in listing["data"]], [row["id"]])
        self.assertEqual(listing["meta"]["total"], 1)

    def test_traversal_names_and_repeat_uploads_are_safe(self):
        """路径穿越文件名不会影响存储位置；重复上传生成不同 UUID，不覆盖文件。"""
        for filename in ("../../outside.csv", "/tmp/outside.csv", r"..\outside.csv"):
            response = self.upload(filename)
            self.assertEqual(response.status_code, 201)
            self.assertTrue(response.json()["data"]["name"].startswith("导入任务-"))
        rows = self.rows()
        self.assertEqual(len({row["id"] for row in rows}), 3)
        self.assertEqual(len(list(self.root.iterdir())), 3)
        for row in rows:
            path = Path(row["stored_file_path"])
            self.assertEqual(path.parent, self.root)
            self.assertEqual(path.name, row["id"] + ".csv")
        self.assertIn("../../outside.csv", [row["source_file_name"] for row in rows])

    def test_rejected_requests_have_no_side_effects(self):
        """无效文件/字段和超限文件不会创建任务、文件或队列消息。"""
        checks = [
            (self.client.post("/api/v1/jobs"), "INVALID_REQUEST"),
            (self.upload("a.txt"), "INVALID_FILE_EXTENSION"),
            (self.upload(name="名" * 129), "INVALID_REQUEST"),
            (self.upload("x" * 256 + ".csv"), "INVALID_REQUEST"),
        ]
        with patch.dict(os.environ, {"MAX_UPLOAD_FILE_SIZE_MB": "1"}):
            checks.append(
                (self.upload(content=b"x" * (1024 * 1024 + 1)), "FILE_TOO_LARGE")
            )
        for response, code in checks:
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["code"], code)
        self.assertEqual(self.rows(), [])
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.queue.llen(self.key), 0)
        with patch.dict(os.environ, {"MAX_UPLOAD_FILE_SIZE_MB": "1"}):
            self.assertEqual(
                self.upload(content=b"x" * (1024 * 1024), name="名" * 128).status_code,
                201,
            )

    def test_redis_failure_records_failed_job(self):
        """Redis 实际连接失败：返回脱敏 500，任务 FAILED 且留有文件级错误记录。"""
        with patch.dict(os.environ, {"REDIS_ADDR": "redis-test:1"}):
            response = self.upload()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "INTERNAL_ERROR")
        row = self.rows()[0]
        self.assertEqual(row["status"], "FAILED")
        self.assertEqual(row["last_error_code"], "QUEUE_DISPATCH_FAILED")
        self.assertIsNotNone(row["finished_at"])
        self.assertTrue(Path(row["stored_file_path"]).exists())
        error = self.rows("sync_errors")[0]
        self.assertEqual(error["job_id"], row["id"])
        self.assertEqual(error["error_code"], "QUEUE_DISPATCH_FAILED")
        self.assertIsNone(error["row_number"])
        self.assertNotIn("redis-test", response.text)
        self.assertEqual(self.queue.llen(self.key), 0)

    def test_dispatch_failure_when_database_update_also_fails(self):
        """队列失败且错误回写失败时，先前提交的待投递标记仍可用于排查。"""
        with (
            patch.dict(os.environ, {"REDIS_ADDR": "redis-test:1"}),
            patch("app.jobs.repository.fail_job", side_effect=MySQLError("private")),
        ):
            response = self.upload()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.rows()[0]["last_error_code"], "QUEUE_DISPATCH_PENDING")
        self.assertNotIn("private", response.text)

    def test_insert_and_disk_failures_do_not_enqueue(self):
        """入库或文件保存失败返回 500；未持久化的输入被清理，不投递队列。"""
        with patch(
            "app.jobs.repository.create_job", side_effect=MySQLError("private SQL")
        ):
            response = self.upload()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(list(self.root.iterdir()), [])
        with patch("app.jobs.Path.open", side_effect=OSError("private disk")):
            response = self.upload()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.queue.llen(self.key), 0)
        self.assertNotIn("private", response.text)

    def test_live_openapi_has_creation_contract(self):
        """正式应用的 OpenAPI 声明真实 POST、multipart 以及 201/400/500。"""
        operation = self.client.get("/openapi.json").json()["paths"]["/api/v1/jobs"][
            "post"
        ]
        self.assertEqual(set(operation["responses"]), {"201", "400", "500"})
        self.assertIn("multipart/form-data", operation["requestBody"]["content"])
        self.assertNotIn("x-implementation-status", operation)

    def test_commit_acknowledgement_loss_retains_committed_input(self):
        """提交确认丢失：保留已提交任务和输入文件，留下待投递标记。"""
        first = True

        @contextmanager
        def lost_ack():
            nonlocal first
            fail = first
            first = False
            with connection() as db:
                yield db
            if fail:
                raise MySQLError("commit acknowledgement lost")

        with patch("app.jobs.connection", lost_ack):
            response = self.upload()
        self.assertEqual(response.status_code, 500)
        row = self.rows()[0]
        self.assertTrue(Path(row["stored_file_path"]).exists())
        self.assertEqual(row["last_error_code"], "QUEUE_DISPATCH_PENDING")
        self.assertEqual(self.queue.llen(self.key), 0)

    def test_redis_acknowledgement_loss_does_not_retry(self):
        """Redis 已收消息但响应超时：不重复投递；FAILED 任务不能进入 RUNNING。"""

        def accepted_then_timeout(*args):
            self.queue.rpush(*args)
            raise RedisTimeoutError("acknowledgement lost")

        with patch("app.jobs.queue_client") as mocked:
            mocked.return_value.__enter__.return_value.rpush.side_effect = (
                accepted_then_timeout
            )
            response = self.upload()
        self.assertEqual(response.status_code, 500)
        row = self.rows()[0]
        self.assertEqual(self.queue.lrange(self.key, 0, -1), [row["id"]])
        self.assertEqual(row["status"], "FAILED")
        with connection() as db:
            self.assertFalse(repository.start_job(db, row["id"]))

    def test_successful_dispatch_with_cleanup_failure_still_returns_201(self):
        """已投递成功但标记清理失败：仍返回 201，避免客户端误认为失败而重传。"""
        with patch(
            "app.jobs.repository.clear_dispatch_pending",
            side_effect=MySQLError("private"),
        ):
            response = self.upload()
        self.assertEqual(response.status_code, 201)
        row = self.rows()[0]
        self.assertEqual(self.queue.lrange(self.key, 0, -1), [row["id"]])
        self.assertEqual(row["last_error_code"], "QUEUE_DISPATCH_PENDING")
