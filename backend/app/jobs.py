"""Create a persisted job and publish its ID without waiting for a worker."""

import hashlib
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mysql.connector import Error as MySQLError
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from app import repository
from app.api_contract import (
    APIError,
    JobCreated,
    JobDetail,
    JobListResponse,
    JobQuery,
    ValidatedUpload,
)
from app.database import connection

logger = logging.getLogger(__name__)


def queue_client():
    address = os.environ.get("REDIS_ADDR", "127.0.0.1:6379")
    url = address if "://" in address else "redis://" + address
    return Redis.from_url(
        url,
        socket_connect_timeout=3,
        socket_timeout=3,
        retry=Retry(NoBackoff(), 0),
        decode_responses=True,
    )


def queue_name():
    return os.environ.get("REDIS_JOB_QUEUE", "syncflow:jobs")


def create_job(upload: ValidatedUpload) -> JobCreated:
    job_id = str(uuid4())
    root = Path(os.environ.get("UPLOAD_DIR", "var/uploads")).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / f"{job_id}.csv"
    name = (
        upload.name
        if upload.name is not None
        else f"导入任务-{datetime.now(UTC):%Y%m%d-%H%M%S}-{job_id[:8]}"
    )
    digest = hashlib.sha256()
    # Exclusive creation prevents overwrites and following an existing symlink.
    with path.open("xb") as destination:
        try:
            os.chmod(path, 0o600)
            while chunk := upload.file.file.read(64 * 1024):
                destination.write(chunk)
                digest.update(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    try:
        with connection() as db:
            row = repository.create_job(
                db,
                job_id=job_id,
                name=name,
                source_file_name=upload.file.filename,
                stored_file_path=str(path),
                file_sha256=digest.hexdigest(),
            )
            # Durable breadcrumb covers a crash between SQL commit and Redis publish.
            repository.mark_dispatch_pending(db, job_id)
    except Exception:
        # A lost COMMIT acknowledgement is ambiguous: retain the file if we cannot
        # prove that the row is absent, so a committed task never loses its input.
        try:
            with connection() as db:
                if repository.get_job(db, job_id) is None:
                    path.unlink(missing_ok=True)
        except (MySQLError, OSError):
            logger.error(
                "job_id=%s persistence outcome unknown; input retained", job_id
            )
        raise

    try:
        with queue_client() as queue:
            queue.rpush(queue_name(), job_id)
    except (RedisError, ValueError):
        logger.error("job_id=%s queue dispatch failed", job_id)
        try:
            with connection() as db:
                repository.fail_job(
                    db,
                    job_id,
                    error_code="QUEUE_DISPATCH_FAILED",
                    error_message="任务队列投递失败",
                )
        except (MySQLError, OSError):
            logger.error(
                "job_id=%s failed to record dispatch failure; pending marker retained",
                job_id,
            )
        raise APIError("INTERNAL_ERROR") from None

    try:
        with connection() as db:
            repository.clear_dispatch_pending(db, job_id)
    except (MySQLError, OSError):
        # Publication succeeded. Do not report failure and invite duplicate uploads.
        logger.error("job_id=%s published; pending marker could not be cleared", job_id)
    logger.info("job_id=%s created and queued", job_id)
    return JobCreated.model_validate(row)


def get_job(job_id: str) -> JobDetail:
    with connection() as db:
        row = repository.get_job(db, job_id)
    if row is None:
        raise APIError("JOB_NOT_FOUND")
    return JobDetail.model_validate(row)


def list_jobs(query: JobQuery) -> JobListResponse:
    with connection() as db:
        result = repository.list_jobs(db, **query.model_dump())
    return JobListResponse.model_validate(result)
