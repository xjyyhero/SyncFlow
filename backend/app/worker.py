"""Consume CSV jobs and persist validated records and diagnostic errors."""

import logging
import os
import signal
from pathlib import Path
from threading import Event

from mysql.connector import Error as MySQLError
from redis.exceptions import RedisError

from app import repository
from app.csv_source import CSVValidationError, read_csv
from app.database import connection
from app.jobs import queue_client, queue_name

stopped = Event()
logger = logging.getLogger(__name__)
BATCH_SIZE = 250


def stop(_signum: int, _frame: object) -> None:
    stopped.set()


def write_batch(job_id, rows, offset, total):
    try:
        with connection() as db:
            return repository.save_csv_batch(
                db, job_id, rows, offset=offset, total=total
            )
    except MySQLError as error:
        # Do not expose SQL/credentials from exception text. errno is a stable
        # diagnostic; every affected valid row retains its source location.
        reason = {
            1062: "数据唯一性冲突",
            1205: "数据库锁等待超时",
            1213: "数据库死锁",
            1406: "字段长度超过数据库限制",
            1264: "数值超出数据库范围",
            3819: "数据检查约束冲突",
            2006: "数据库连接中断",
            2013: "数据库连接中断",
        }.get(error.errno, "数据库写入异常")
        message = (
            f"第 {offset // BATCH_SIZE + 1} 批（源文件第 {rows[0]['row_number']}"
            f"–{rows[-1]['row_number']} 行）写入失败：{reason}"
            f"（MySQL {error.errno or '未知'}）；本批已回滚"
        )
        failed_rows = [
            {
                "error": row.get("error")
                or {
                    "row_number": row["row_number"],
                    "raw_row": row["raw_row"],
                    "field_name": None,
                    "error_code": "BATCH_WRITE_FAILED",
                    "error_message": message,
                }
            }
            for row in rows
        ]
        try:
            with connection() as db:
                # Recheck committed progress under lock before recording failure:
                # an ambiguous COMMIT must never double-count a successful batch.
                result = repository.save_csv_batch(
                    db, job_id, failed_rows, offset=offset, total=total
                )
        except MySQLError:
            # The failure-record transaction can also lose its COMMIT response.
            with connection() as db:
                result = repository.get_job(db, job_id)
            if result is None or (
                result["success_records"] + result["failed_records"]
                != offset + len(rows)
                or result["total_records"] != total
            ):
                raise
        logger.warning(
            "job_id=%s stage=batch_write batch=%s write_error=%s persisted_progress=%s",
            job_id,
            offset // BATCH_SIZE + 1,
            error.errno,
            None
            if result is None
            else result["success_records"] + result["failed_records"],
        )
        return result


def process_job(job_id: str) -> bool:
    with connection() as db:
        if not repository.start_job(db, job_id):
            logger.info("job_id=%s skipped (missing or not PENDING)", job_id)
            return False
        job = repository.get_job(db, job_id)
    logger.info("job_id=%s status=RUNNING", job_id)
    total = None
    stage = "file_access"
    try:
        root = Path(os.environ.get("UPLOAD_DIR", "var/uploads")).resolve()
        path = Path(job["stored_file_path"]).resolve()
        if not path.is_relative_to(root):
            raise CSVValidationError("FILE_UNREADABLE", "文件不存在或无法读取")
        stage = "csv_validation"
        result = read_csv(path)
        total = result.total_records
        logger.info("job_id=%s stage=csv_validated total_records=%s", job_id, total)
        stage = "batch_write"
        for offset in range(0, total, BATCH_SIZE):
            rows = result.rows[offset : offset + BATCH_SIZE]
            final = write_batch(job_id, rows, offset, total)
            if final is None:
                logger.info("job_id=%s skipped (state or progress changed)", job_id)
                return False
            logger.info(
                "job_id=%s status=%s batch=%s source_lines=%s-%s processed=%s/%s success=%s failed=%s",
                job_id,
                final["status"],
                offset // BATCH_SIZE + 1,
                rows[0]["row_number"],
                rows[-1]["row_number"],
                final["success_records"] + final["failed_records"],
                total,
                final["success_records"],
                final["failed_records"],
            )
        return True
    except (CSVValidationError, OSError, MySQLError, ValueError, RuntimeError) as error:
        code = (
            error.code
            if isinstance(error, CSVValidationError)
            else (
                "FILE_UNREADABLE"
                if isinstance(error, OSError)
                else "WORKER_PROCESSING_FAILED"
            )
        )
        message = (
            error.message
            if isinstance(error, CSVValidationError)
            else (
                "文件不存在或无法读取"
                if isinstance(error, OSError)
                else "后台处理失败，请检查配置或数据库状态"
            )
        )
        with connection() as db:
            failed = repository.fail_job(
                db,
                job_id,
                error_code=code,
                error_message=message,
                total_records=total,
            )
        logger.error(
            "job_id=%s status=%s stage=%s failure_recorded=%s error_code=%s",
            job_id,
            "FAILED" if failed else "unchanged",
            stage,
            failed,
            code,
        )
        return False


def run() -> None:
    logger.info("Worker started; queue=%s", queue_name())
    with queue_client() as queue:
        while not stopped.is_set():
            job_id = None
            try:
                # ponytail: BLPOP has no crash recovery; use an acknowledged queue
                # when automatic recovery of interrupted jobs is required.
                item = queue.blpop(queue_name(), timeout=1)
                if item is not None:
                    job_id = item[1]
                    logger.info("job_id=%s received", job_id)
                    process_job(job_id)
            except (RedisError, MySQLError, OSError):
                logger.error("job_id=%s worker operation failed", job_id or "none")
                # Bound retry frequency; signal handling interrupts this wait.
                stopped.wait(1)
    logger.info("Stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s worker %(message)s"
    )
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    run()
