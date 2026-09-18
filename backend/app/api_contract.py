"""Shared Week 2 response schemas, safe errors, and request validation."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator
from starlette.exceptions import HTTPException

JobStatus = Literal["PENDING", "RUNNING", "SUCCESS", "FAILED"]


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadyResponse(HealthResponse):
    checks: dict[str, Literal["ok"]]


class PageMeta(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class SuccessResponse[T](BaseModel):
    model_config = ConfigDict(json_schema_extra={"required": ["data", "meta"]})
    data: T
    meta: PageMeta | dict[str, Any] = Field(default_factory=dict)


class ErrorDetail(BaseModel):
    field: str
    message: str


class ErrorBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"required": ["code", "message", "details"]}
    )
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorBody


ERRORS = {
    "INVALID_REQUEST": (400, "请求参数不合法"),
    "INVALID_FILE_EXTENSION": (400, "仅支持 CSV 文件"),
    "FILE_TOO_LARGE": (400, "文件超过大小限制"),
    "JOB_NOT_FOUND": (404, "任务不存在"),
    "INTERNAL_ERROR": (500, "服务内部错误，请稍后重试"),
    "DATABASE_UNAVAILABLE": (503, "数据库暂不可用"),
    "NOT_FOUND": (404, "接口不存在"),
    "METHOD_NOT_ALLOWED": (405, "请求方法不支持"),
}


class APIError(Exception):
    def __init__(self, code: str, details: list[ErrorDetail] | None = None):
        self.status, self.message = ERRORS[code]
        self.code = code
        self.details = details or []
        super().__init__(code)


def error_response(error: APIError):
    return JSONResponse(
        status_code=error.status,
        content=ErrorResponse(
            error=ErrorBody(
                code=error.code,
                message=error.message,
                details=error.details,
            )
        ).model_dump(),
    )


def error_docs(*codes):
    responses = {}
    for code in codes:
        status, message = ERRORS[code]
        item = responses.setdefault(
            status,
            {
                "model": ErrorResponse,
                "description": "",
                "content": {"application/json": {"examples": {}}},
            },
        )
        item["description"] = ", ".join(filter(None, [item["description"], code]))
        item["content"]["application/json"]["examples"][code] = {
            "value": {"error": {"code": code, "message": message, "details": []}}
        }
    return responses


def install_error_handlers(app: FastAPI):
    @app.exception_handler(APIError)
    async def known_error(_request, error):
        return error_response(error)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, error):
        messages = {
            "missing": "必填字段缺失",
            "string_too_long": "字符串超出长度限制",
            "greater_than_equal": "小于允许下限",
            "less_than_equal": "超出允许上限",
            "literal_error": "状态值无效",
            "int_parsing": "参数必须为整数",
        }
        fields = {"page", "page_size", "status", "name", "file", "job_id"}
        details = [
            ErrorDetail(
                field=next(
                    (str(part) for part in e["loc"] if part in fields), "request"
                ),
                message=messages.get(e["type"], "字段格式不正确"),
            )
            for e in error.errors()
        ]
        # Never serialize validation input/context or exception messages.
        return error_response(APIError("INVALID_REQUEST", details))

    @app.exception_handler(HTTPException)
    async def http_error(_request, error):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(
            error.status_code,
            "INTERNAL_ERROR" if error.status_code >= 500 else "INVALID_REQUEST",
        )
        response = error_response(APIError(code))
        if error.status_code == 405 and error.headers and "Allow" in error.headers:
            response.headers["Allow"] = error.headers["Allow"]
        return response

    @app.exception_handler(Exception)
    async def internal_error(_request, _error):
        return error_response(APIError("INTERNAL_ERROR"))

    original_openapi = app.openapi

    def openapi():
        schema = original_openapi()
        # FastAPI otherwise advertises its default 422 validation response.
        for path in schema["paths"].values():
            for operation in path.values():
                if isinstance(operation, dict) and "responses" in operation:
                    operation["responses"].pop("422", None)
        return schema

    app.openapi = openapi


class JobQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    status: JobStatus | None = None

    @field_validator("page", "page_size", mode="before")
    @classmethod
    def integer_only(cls, value):
        if type(value) is int or (
            isinstance(value, str) and value.isascii() and value.isdecimal()
        ):
            return value
        raise ValueError("Expected an integer")


class JobCreated(BaseModel):
    id: str = Field(max_length=36)
    name: str = Field(max_length=128)
    status: JobStatus
    created_at: datetime

    @field_serializer("created_at", check_fields=False)
    def utc_created(self, value):
        return (
            value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
            if value.tzinfo is None
            else value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        )


class JobDetail(JobCreated):
    source_file_name: str
    total_records: int = Field(ge=0)
    success_records: int = Field(ge=0)
    failed_records: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    last_error_code: str | None
    last_error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None

    @field_serializer("started_at", "finished_at")
    def utc_optional(self, value):
        return self.utc_created(value) if value is not None else None


class JobListResponse(SuccessResponse[list[JobDetail]]):
    meta: PageMeta


@dataclass
class ValidatedUpload:
    file: UploadFile
    name: str | None
    size: int


async def validate_upload(
    file: Annotated[UploadFile, File(description="CSV 文件，默认不超过 10 MiB")],
    name: Annotated[
        str | None, Form(max_length=128, description="不传时由系统生成任务名称")
    ] = None,
) -> ValidatedUpload:
    if len(file.filename or "") > 255 or "\x00" in (file.filename or ""):
        raise APIError(
            "INVALID_REQUEST",
            [ErrorDetail(field="file", message="文件名不合法或超过 255 字符")],
        )
    if PurePath(file.filename or "").suffix.lower() != ".csv":
        raise APIError("INVALID_FILE_EXTENSION")
    limit = int(os.environ.get("MAX_UPLOAD_FILE_SIZE_MB", "10")) * 1024 * 1024
    if limit <= 0:
        raise RuntimeError("Invalid upload limit configuration")
    size = 0
    try:
        while chunk := await file.read(min(64 * 1024, limit - size + 1)):
            size += len(chunk)
            if size > limit:
                raise APIError("FILE_TOO_LARGE")
    finally:
        await file.seek(0)
    return ValidatedUpload(file=file, name=name, size=size)
