"""SyncFlow API and health endpoints."""

import logging
from typing import Annotated

from fastapi import Depends, FastAPI, Path, Query
from mysql.connector import Error

from app.api_contract import (
    APIError,
    HealthResponse,
    JobCreated,
    JobDetail,
    JobErrorListResponse,
    JobListResponse,
    JobQuery,
    PageQuery,
    ReadyResponse,
    SuccessResponse,
    ValidatedUpload,
    error_docs,
    error_response,
    install_error_handlers,
    validate_upload,
)
from app.database import check_mysql
from app.jobs import create_job, get_job, list_job_errors, list_jobs

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)

app = FastAPI(
    title="SyncFlow",
    version="1.0.0",
    responses=error_docs("INVALID_REQUEST", "INTERNAL_ERROR"),
)
install_error_handlers(app)


@app.get("/healthz", response_model=HealthResponse)
def healthz() -> dict[str, str]:
    """Report process liveness without checking database or queue availability."""
    return {"status": "ok"}


@app.get("/api/v1/info", response_model=SuccessResponse[dict[str, str]])
def project_info():
    return SuccessResponse(
        data={
            "name": "SyncFlow",
            "version": "1.0.0",
            "stage": "V1.0 CSV 同步与任务管理",
        }
    )


@app.get(
    "/readyz",
    response_model=ReadyResponse,
    responses=error_docs("DATABASE_UNAVAILABLE"),
)
def readyz():
    """Check MySQL connectivity; keep internal errors out of HTTP responses."""
    try:
        if check_mysql():
            return {"status": "ok", "checks": {"mysql": "ok"}}
    except (Error, KeyError, ValueError):
        pass
    return error_response(APIError("DATABASE_UNAVAILABLE"))


@app.post(
    "/api/v1/jobs",
    status_code=201,
    response_model=SuccessResponse[JobCreated],
    operation_id="createJob",
    summary="创建任务",
    description="校验并保存 CSV、提交 PENDING 任务并投递 Redis，不等待 Worker。默认限制 10 MiB，扩展名大小写不敏感。投递失败记录任务错误并返回 500。",
    responses=error_docs(
        "INVALID_REQUEST", "INVALID_FILE_EXTENSION", "FILE_TOO_LARGE", "INTERNAL_ERROR"
    ),
)
def post_job(upload: Annotated[ValidatedUpload, Depends(validate_upload)]):
    return SuccessResponse(data=create_job(upload))


@app.get(
    "/api/v1/jobs",
    response_model=JobListResponse,
    operation_id="listJobs",
    summary="查询任务列表",
    description="支持 page（默认 1）、page_size（默认 20，最大 100）与状态筛选；按 created_at DESC, id DESC 排序。返回公开任务字段及 page/page_size/total。",
)
def get_jobs(query: Annotated[JobQuery, Query()]):
    return list_jobs(query)


@app.get(
    "/api/v1/jobs/{job_id}",
    response_model=SuccessResponse[JobDetail],
    operation_id="getJob",
    summary="查询任务详情",
    description="返回任务公开字段、统计与 UTC 时间；不包含内部存储路径。不存在返回 JOB_NOT_FOUND。",
    responses=error_docs("JOB_NOT_FOUND"),
)
def get_job_detail(
    job_id: Annotated[
        str, Path(min_length=1, max_length=36, description="UUID 或 ULID 任务标识")
    ],
):
    return SuccessResponse(data=get_job(job_id))


@app.get(
    "/api/v1/jobs/{job_id}/errors",
    response_model=JobErrorListResponse,
    operation_id="listJobErrors",
    summary="查询任务错误明细",
    description="支持 page（默认 1）与 page_size（默认 20，最大 100）。按 row_number ASC, id ASC 排序，文件级空行号排在前面；返回原始 JSON 和 UTC 时间。任务不存在返回 JOB_NOT_FOUND，任务存在但无错误返回空列表，运行中也可查询。",
    responses={
        **error_docs("JOB_NOT_FOUND"),
        200: {
            "description": "错误明细与分页信息",
            "content": {
                "application/json": {
                    "examples": {
                        "row_error": {
                            "summary": "行级错误",
                            "value": {
                                "data": [
                                    {
                                        "job_id": "example-job",
                                        "row_number": 2,
                                        "field_name": "amount",
                                        "error_code": "AMOUNT_INVALID",
                                        "error_message": "amount 不能为负数",
                                        "raw_row": [
                                            "S001",
                                            "商品A",
                                            "-1",
                                            "2026-01-01",
                                        ],
                                        "created_at": "2026-01-01T10:00:00Z",
                                    }
                                ],
                                "meta": {"page": 1, "page_size": 20, "total": 1},
                            },
                        },
                        "file_error": {
                            "summary": "文件级错误",
                            "value": {
                                "data": [
                                    {
                                        "job_id": "example-job",
                                        "row_number": None,
                                        "field_name": None,
                                        "error_code": "FILE_EMPTY",
                                        "error_message": "文件为空或只有表头",
                                        "raw_row": None,
                                        "created_at": "2026-01-01T10:00:00Z",
                                    }
                                ],
                                "meta": {"page": 1, "page_size": 20, "total": 1},
                            },
                        },
                        "empty": {
                            "summary": "任务存在但无错误",
                            "value": {
                                "data": [],
                                "meta": {"page": 1, "page_size": 20, "total": 0},
                            },
                        },
                    }
                }
            },
        },
    },
)
def get_job_errors(
    job_id: Annotated[
        str, Path(min_length=1, max_length=36, description="UUID 或 ULID 任务标识")
    ],
    query: Annotated[PageQuery, Query()],
):
    return list_job_errors(job_id, query)
