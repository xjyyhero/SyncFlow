"""Query the real HTTP endpoints against the isolated MySQL database."""

import os
from unittest.mock import patch
from uuid import UUID

from app import repository
from app.database import connection, initialize
from app.main import app
from fastapi.testclient import TestClient
from reporting import EvidenceCase

PUBLIC_FIELDS = {
    "id",
    "name",
    "status",
    "source_file_name",
    "total_records",
    "success_records",
    "failed_records",
    "retry_count",
    "last_error_code",
    "last_error_message",
    "created_at",
    "started_at",
    "finished_at",
}


def job_id(number):
    return str(UUID(int=number))


class QueryJobTests(EvidenceCase):
    @classmethod
    def setUpClass(cls):
        if (os.environ.get("MYSQL_HOST"), os.environ.get("MYSQL_DATABASE")) != (
            "mysql-test",
            "syncflow_test",
        ):
            raise RuntimeError("Query tests require isolated MySQL")
        initialize()

    def setUp(self):
        self.evidence = []
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        with connection() as db, db.cursor() as cursor:
            for table in ("sync_errors", "sync_records", "sync_jobs"):
                cursor.execute(f"DELETE FROM {table}")

    def seed(self, count=1):
        with connection() as db, db.cursor() as cursor:
            for n in range(1, count + 1):
                repository.create_job(
                    db,
                    job_id=job_id(n),
                    name=f"任务{n}",
                    source_file_name=f"来源{n}.csv",
                    stored_file_path="/private/internal.csv",
                    file_sha256="a" * 64,
                )
            cursor.execute(
                "UPDATE sync_jobs SET created_at = '2026-01-01 10:00:00.123'"
            )

    def test_detail_has_all_public_fields_and_null_times(self):
        """详情返回全部 13 个公开字段；内部路径、哈希和 updated_at 不会泄露。"""
        self.seed()
        response = self.client.get(f"/api/v1/jobs/{job_id(1)}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["meta"], {})
        self.assertEqual(set(body["data"]), PUBLIC_FIELDS)
        self.assertEqual(body["data"]["name"], "任务1")
        self.assertEqual(body["data"]["source_file_name"], "来源1.csv")
        self.assertEqual(body["data"]["status"], "PENDING")
        self.assertEqual(body["data"]["created_at"], "2026-01-01T10:00:00.123000Z")
        for field in (
            "started_at",
            "finished_at",
            "last_error_code",
            "last_error_message",
        ):
            self.assertIsNone(body["data"][field])
        for field in (
            "total_records",
            "success_records",
            "failed_records",
            "retry_count",
        ):
            self.assertEqual(body["data"][field], 0)
        self.assertNotIn("private", response.text)
        self.assertNotIn("stored_file_path", response.text)

    def test_detail_statistics_errors_and_completed_times(self):
        """详情读取真实统计、错误摘要和开始/结束时间，不用固定模拟值。"""
        self.seed()
        with connection() as db, db.cursor() as cursor:
            cursor.execute("""UPDATE sync_jobs SET status='FAILED', total_records=10,
                success_records=8, failed_records=2, retry_count=1,
                last_error_code='INVALID_AMOUNT', last_error_message='金额不合法',
                started_at='2026-01-01 10:01:00', finished_at='2026-01-01 10:02:00'""")
        data = self.client.get(f"/api/v1/jobs/{job_id(1)}").json()["data"]
        self.assertEqual(
            [
                data[k]
                for k in (
                    "total_records",
                    "success_records",
                    "failed_records",
                    "retry_count",
                )
            ],
            [10, 8, 2, 1],
        )
        self.assertEqual(data["status"], "FAILED")
        self.assertEqual(data["last_error_code"], "INVALID_AMOUNT")
        self.assertEqual(data["last_error_message"], "金额不合法")
        self.assertEqual(data["started_at"], "2026-01-01T10:01:00Z")
        self.assertEqual(data["finished_at"], "2026-01-01T10:02:00Z")

    def test_missing_id_and_invalid_id(self):
        """不存在和 SQL 注入样式的 ID 返回 JOB_NOT_FOUND；超长 ID 返回 400。"""
        self.seed()
        for value in (job_id(99), "a' OR '1'='1"):
            response = self.client.get(f"/api/v1/jobs/{value}")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(
                response.json(),
                {
                    "error": {
                        "code": "JOB_NOT_FOUND",
                        "message": "任务不存在",
                        "details": [],
                    }
                },
            )
        response = self.client.get("/api/v1/jobs/" + "a" * 37)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "INVALID_REQUEST")

    def test_empty_default_and_maximum_page_size(self):
        """空列表返回完整分页信息；105 条数据默认 20 条，最大页大小 100。"""
        self.assertEqual(
            self.client.get("/api/v1/jobs").json(),
            {"data": [], "meta": {"page": 1, "page_size": 20, "total": 0}},
        )
        self.seed(105)
        response = self.client.get("/api/v1/jobs")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["data"]), 20)
        self.assertEqual(body["meta"], {"page": 1, "page_size": 20, "total": 105})
        self.assertEqual(set(body["data"][0]), PUBLIC_FIELDS)
        self.assertNotIn("private", response.text)
        self.assertEqual(
            len(self.client.get("/api/v1/jobs?page_size=100").json()["data"]), 100
        )
        self.assertEqual(
            len(self.client.get("/api/v1/jobs?page=2&page_size=100").json()["data"]), 5
        )
        body = self.client.get("/api/v1/jobs?page=" + "9" * 40).json()
        self.assertEqual(body["data"], [])
        self.assertEqual(body["meta"]["total"], 105)

    def test_stable_order_and_status_filter(self):
        """同时间按 ID 倒序，新时间优先；状态筛选后 total 只统计匹配任务。"""
        self.seed(5)
        with connection() as db, db.cursor() as cursor:
            cursor.execute(
                "UPDATE sync_jobs SET created_at='2026-01-02', status='SUCCESS' WHERE id=%s",
                (job_id(1),),
            )
            cursor.execute(
                "UPDATE sync_jobs SET status='SUCCESS' WHERE id=%s", (job_id(4),)
            )
        first = self.client.get("/api/v1/jobs?page_size=2").json()
        second = self.client.get("/api/v1/jobs?page=2&page_size=2").json()
        self.assertEqual([r["id"] for r in first["data"]], [job_id(1), job_id(5)])
        self.assertEqual([r["id"] for r in second["data"]], [job_id(4), job_id(3)])
        body = self.client.get("/api/v1/jobs?status=SUCCESS&page_size=1&page=2").json()
        self.assertEqual([r["id"] for r in body["data"]], [job_id(4)])
        self.assertEqual(body["meta"], {"page": 2, "page_size": 1, "total": 2})
        for status in ("RUNNING", "FAILED"):
            body = self.client.get("/api/v1/jobs", params={"status": status}).json()
            self.assertEqual(body["meta"]["total"], 0)
        self.assertEqual(
            self.client.get("/api/v1/jobs?status=PENDING").json()["meta"]["total"], 3
        )

    def test_invalid_parameters_never_query_database(self):
        """非法分页/状态返回 400，并在访问数据库之前拒绝。"""
        with patch("app.jobs.connection") as connect:
            for query in (
                "page=0",
                "page=-1",
                "page=1.1",
                "page=true",
                "page=",
                "page_size=0",
                "page_size=101",
                "page_size=text",
                "status=QUEUED",
                "status=",
            ):
                response = self.client.get("/api/v1/jobs?" + query)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "INVALID_REQUEST")
            self.assertFalse(connect.called)

    def test_database_failure_is_safe_and_redis_is_not_required(self):
        """数据库真实连接失败时返回脱敏 500；查询不依赖 Redis。"""
        self.seed()
        with patch.dict(os.environ, {"MYSQL_PORT": "1"}):
            for route in ("/api/v1/jobs", f"/api/v1/jobs/{job_id(1)}"):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.json()["error"]["code"], "INTERNAL_ERROR")
                for secret in (
                    "mysql-test",
                    "test-only-password",
                    "Traceback",
                    "SELECT",
                ):
                    self.assertNotIn(secret, response.text)
        with patch.dict(os.environ, {"REDIS_ADDR": "redis-test:1"}):
            self.assertEqual(self.client.get("/api/v1/jobs").status_code, 200)
            self.assertEqual(
                self.client.get(f"/api/v1/jobs/{job_id(1)}").status_code, 200
            )

    def test_runtime_openapi_documents_queries(self):
        """运行时 OpenAPI 声明列表、详情的真实模型、参数和 404 错误。"""
        schema = self.client.get("/openapi.json").json()
        listing = schema["paths"]["/api/v1/jobs"]["get"]
        detail = schema["paths"]["/api/v1/jobs/{job_id}"]["get"]
        self.assertEqual(set(listing["responses"]), {"200", "400", "500"})
        self.assertEqual(set(detail["responses"]), {"200", "400", "404", "500"})
        self.assertEqual(
            {p["name"] for p in listing["parameters"]}, {"page", "page_size", "status"}
        )
        self.assertEqual(
            schema["components"]["schemas"]["JobListResponse"]["properties"]["meta"],
            {"$ref": "#/components/schemas/PageMeta"},
        )
