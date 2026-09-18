"""HTTP-level contract tests. Fixture routes never mount in the production app."""

import io
import os
import unittest
from datetime import datetime
from typing import Annotated
from unittest.mock import patch

from app.api_contract import (
    APIError,
    JobDetail,
    JobQuery,
    SuccessResponse,
    ValidatedUpload,
    error_docs,
    install_error_handlers,
    validate_upload,
)
from app.main import app
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from mysql.connector import DatabaseError
from reporting import EvidenceCase


def contract_app():
    fixture = FastAPI(
        responses=error_docs(
            "INVALID_REQUEST",
            "INVALID_FILE_EXTENSION",
            "FILE_TOO_LARGE",
            "JOB_NOT_FOUND",
            "INTERNAL_ERROR",
        )
    )
    install_error_handlers(fixture)

    @fixture.get("/query", response_model=SuccessResponse[dict])
    def query(params: Annotated[JobQuery, Query()]):
        return SuccessResponse(data=params.model_dump())

    @fixture.post("/upload", status_code=201, response_model=SuccessResponse[dict])
    async def upload(value: Annotated[ValidatedUpload, Depends(validate_upload)]):
        # Verify the validator rewinds the file for its downstream caller.
        return SuccessResponse(
            data={
                "size": value.size,
                "name": value.name,
                "first_byte": (await value.file.read(1)).decode(),
            }
        )

    @fixture.get("/missing")
    def missing():
        raise APIError("JOB_NOT_FOUND")

    @fixture.get("/database-error")
    def database_error():
        raise DatabaseError("SELECT secret FROM users mysql://admin:private@mysql:3306")

    @fixture.get("/unexpected")
    def unexpected():
        raise RuntimeError("/private/uploads/internal.csv password=private Traceback")

    @fixture.get("/http-error")
    def http_error():
        raise HTTPException(500, detail="SQL password=private /private/uploads")

    @fixture.get("/invalid-response", response_model=SuccessResponse[JobDetail])
    def invalid_response():
        return {"data": {"id": "internal-secret"}}

    return fixture


class APIContractTests(EvidenceCase):
    def setUp(self):
        self.evidence = []
        self.client = TestClient(contract_app(), raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def check_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        payload = response.json()
        self.assertEqual(set(payload), {"error"})
        self.assertEqual(set(payload["error"]), {"code", "message", "details"})
        self.assertEqual(payload["error"]["code"], code)
        self.assertTrue(isinstance(payload["error"]["details"], list))
        return payload

    def test_success_envelope_and_query_defaults(self):
        """成功结构：data/meta 始终存在；分页默认 1/20，状态默认空。"""
        response = self.client.get("/query")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "data": {"page": 1, "page_size": 20, "status": None},
                "meta": {},
            },
        )
        self.assertEqual(
            self.client.get("/query?page=2&page_size=100&status=SUCCESS").json()[
                "data"
            ],
            {
                "page": 2,
                "page_size": 100,
                "status": "SUCCESS",
            },
        )
        with TestClient(app) as live:
            self.assertEqual(set(live.get("/api/v1/info").json()), {"data", "meta"})
            self.assertEqual(live.get("/healthz").json(), {"status": "ok"})

    def test_invalid_query_returns_400_not_422(self):
        """非法分页与状态：0、101、小数、空值、字符串和未知状态均返回 400。"""
        for query in (
            "page=0",
            "page=-1",
            "page=1.0",
            "page=true",
            "page=",
            "page_size=0",
            "page_size=101",
            "page_size=1.5",
            "status=QUEUED",
            "status=password%3Dprivate",
        ):
            payload = self.check_error(
                self.client.get("/query?" + query), 400, "INVALID_REQUEST"
            )
            self.assertTrue(bool(payload["error"]["details"]))
            self.assertNotIn("private", str(payload))
            self.assertNotIn("input", str(payload))

    def test_upload_required_name_and_extension(self):
        """上传字段：缺文件、超长名称返回 INVALID_REQUEST；非 CSV 返回专用错误码。"""
        self.check_error(self.client.post("/upload"), 400, "INVALID_REQUEST")
        self.check_error(
            self.client.post("/upload", json={"file": "private"}),
            400,
            "INVALID_REQUEST",
        )
        self.check_error(
            self.client.post(
                "/upload", files={"file": ("a.csv", b"x")}, data={"name": "名" * 129}
            ),
            400,
            "INVALID_REQUEST",
        )
        for filename in ("a.txt", "a.csv.exe", "csv"):
            self.check_error(
                self.client.post("/upload", files={"file": (filename, b"x")}),
                400,
                "INVALID_FILE_EXTENSION",
            )
        response = self.client.post(
            "/upload", files={"file": ("a.CSV", b"x,y\n")}, data={"name": "名" * 128}
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["data"]["name"], "名" * 128)
        self.assertEqual(response.json()["data"]["first_byte"], "x")
        self.assertEqual(response.json()["meta"], {})

    def test_upload_size_boundary_and_configuration(self):
        """文件大小：配置 1 MiB，恰好上限成功、多 1 字节返回 FILE_TOO_LARGE。"""
        with patch.dict(os.environ, {"MAX_UPLOAD_FILE_SIZE_MB": "1"}):
            for size, expected in ((1024 * 1024, 201), (1024 * 1024 + 1, 400)):
                response = self.client.post(
                    "/upload", files={"file": ("a.csv", io.BytesIO(b"x" * size))}
                )
                self.assertEqual(response.status_code, expected)
                if expected == 400:
                    self.check_error(response, 400, "FILE_TOO_LARGE")
                else:
                    self.assertEqual(response.json()["data"]["size"], size)
        with patch.dict(os.environ, {"MAX_UPLOAD_FILE_SIZE_MB": "invalid-private"}):
            response = self.client.post("/upload", files={"file": ("a.csv", b"x")})
            self.check_error(response, 500, "INTERNAL_ERROR")
            self.assertNotIn("invalid-private", response.text)

    def test_missing_job_and_internal_errors_are_safe(self):
        """任务缺失为 404；数据库、未知异常及响应校验错误统一为脱敏 500。"""
        self.check_error(self.client.get("/missing"), 404, "JOB_NOT_FOUND")
        for path in (
            "/database-error",
            "/unexpected",
            "/http-error",
            "/invalid-response",
        ):
            response = self.client.get(path)
            self.check_error(response, 500, "INTERNAL_ERROR")
            for secret in (
                "SELECT",
                "mysql://",
                "private",
                "Traceback",
                "internal-secret",
            ):
                self.assertNotIn(secret, response.text)
        self.check_error(self.client.get("/no-such-route"), 404, "NOT_FOUND")
        response = self.client.post("/query")
        self.check_error(response, 405, "METHOD_NOT_ALLOWED")
        self.assertIn("GET", response.headers["allow"])

    def test_public_job_fields_and_utc(self):
        """公开任务模型过滤内部路径、连接信息，字段统一且时间使用 UTC Z。"""
        row = {
            "id": "a",
            "name": "导入",
            "status": "PENDING",
            "created_at": datetime(2026, 1, 1),  # noqa: DTZ001 - MySQL DATETIME is naive UTC.
            "source_file_name": "a.csv",
            "total_records": 0,
            "success_records": 0,
            "failed_records": 0,
            "retry_count": 0,
            "last_error_code": None,
            "last_error_message": None,
            "started_at": None,
            "finished_at": None,
            "stored_file_path": "/private/a.csv",
            "password": "private",
        }
        payload = SuccessResponse(data=JobDetail.model_validate(row)).model_dump(
            mode="json"
        )
        self.assertNotIn("private", str(payload))
        self.assertEqual(payload["data"]["created_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(
            set(payload["data"]),
            {
                "id",
                "name",
                "status",
                "created_at",
                "source_file_name",
                "total_records",
                "success_records",
                "failed_records",
                "retry_count",
                "last_error_code",
                "last_error_message",
                "started_at",
                "finished_at",
            },
        )

    def test_openapi_matches_error_contract(self):
        """OpenAPI 描述实际 400/404/500 错误，并移除框架默认 422 响应。"""
        schema = self.client.get("/openapi.json").json()
        for path in schema["paths"].values():
            for operation in path.values():
                self.assertNotIn("422", operation["responses"])
        examples = schema["paths"]["/upload"]["post"]["responses"]["400"]["content"][
            "application/json"
        ]["examples"]
        self.assertEqual(
            set(examples),
            {"INVALID_REQUEST", "INVALID_FILE_EXTENSION", "FILE_TOO_LARGE"},
        )
        self.assertIn(
            "multipart/form-data",
            schema["paths"]["/upload"]["post"]["requestBody"]["content"],
        )


if __name__ == "__main__":
    unittest.main()
