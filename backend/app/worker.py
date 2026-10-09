"""Consume CSV jobs and persist validated records and diagnostic errors."""

import logging
import os
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from uuid import UUID

from mysql.connector import Error as MySQLError
from redis.exceptions import RedisError

from app import repository
from app.api_contract import APIError
from app.csv_source import CSVValidationError, read_csv
from app.database import connection
from app.jobs import queue_client, queue_name

stopped = Event()
cleanup_failed = Event()
shutdown_deadline = None
shutdown_timeout = 30
logger = logging.getLogger(__name__)
BATCH_SIZE = 250
DISPATCH_SCAN_SECONDS = 30


def stop(_signum: int, _frame: object) -> None:
    global shutdown_deadline
    if shutdown_deadline is None:
        shutdown_deadline = monotonic() + shutdown_timeout
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


def process_job(job_id: str, *, claimed=False) -> bool:
    with connection() as db:
        if not claimed and not repository.start_job(db, job_id):
            logger.info("job_id=%s skipped (missing or not PENDING)", job_id)
            return False
        job = repository.get_job(db, job_id)
        if job is None or job["status"] != "RUNNING":
            return False
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
        try:
            with connection() as db:
                failed = repository.fail_job(
                    db,
                    job_id,
                    error_code=code,
                    error_message=message,
                    total_records=total,
                )
        except APIError as conflict:
            if conflict.code != "INVALID_JOB_TRANSITION":
                raise
            failed = False  # Another action already finalized this task.
        logger.error(
            "job_id=%s status=%s stage=%s failure_recorded=%s error_code=%s",
            job_id,
            "FAILED" if failed else "unchanged",
            stage,
            failed,
            code,
        )
        return False


def redispatch_pending(queue):
    """Retry lost pending deliveries; duplicate IDs are safe at atomic claim."""
    with connection() as db:
        pending = repository.stale_pending_jobs(db)
    for job in pending:
        if stopped.is_set():
            break
        job_id = job["id"]
        # ponytail: stale queued tasks may also be requeued; atomic claim deduplicates.
        # Use an acknowledged queue if large backlogs make duplicates costly.
        try:
            queue.rpush(queue_name(), job_id)
            # Publish first: a crash or lost ACK leaves it eligible to retry.
            with connection() as db:
                repository.mark_redispatched(db, job_id)
        except (RedisError, MySQLError, OSError):
            logger.error("job_id=%s redispatch unconfirmed; will retry", job_id)
            raise
        logger.info("job_id=%s redispatched (stale PENDING)", job_id)


def supervise_job(job_id, timeout_seconds):
    """Each claimed job owns one process and deadline; never share its context."""
    if stopped.is_set():
        return False  # Caller returns this unclaimed delivery to Redis.
    with connection() as db:
        if not repository.start_job(db, job_id):
            logger.info("job_id=%s skipped (missing or not PENDING)", job_id)
            return
        if stopped.is_set():
            db.rollback()
            return False
    deadline = monotonic() + timeout_seconds
    process = None
    failure = None
    try:
        if stopped.is_set():
            failure = ("WORKER_SHUTDOWN", "服务停止，已领取任务尚未启动")
        else:
            process = subprocess.Popen(
                [sys.executable, "-m", "app.worker", "--execute", job_id],
                stdin=subprocess.PIPE,
                start_new_session=True,
            )
            logger.info("job_id=%s process_id=%s started", job_id, process.pid)
        while process is not None:
            end = min(deadline, shutdown_deadline or deadline)
            remaining = end - monotonic()
            if remaining <= 0:
                failure = (
                    ("WORKER_SHUTDOWN_TIMEOUT", "服务停止收尾超时，已停止执行")
                    if end < deadline
                    else ("JOB_TIMEOUT", "任务执行超时，已停止执行")
                )
                break
            try:
                process.wait(timeout=min(0.2, remaining))
            except subprocess.TimeoutExpired:
                pass
            # Bound monitoring I/O too, so a DB stall cannot occupy a slot forever.
            with connection(timeout=1) as db:
                job = repository.get_job(db, job_id)
            if job is None or job["status"] != "RUNNING":
                break
            if process.returncode is not None:
                failure = ("WORKER_PROCESS_EXITED", "任务进程异常退出，未提交最终结果")
                break
    except (OSError, MySQLError):
        failure = ("WORKER_CONTROL_FAILED", "任务进程启动或监控失败，已停止执行")
        logger.error("job_id=%s task supervision failed", job_id)
    finally:
        # Close child file descriptors/DB sessions before recording final status.
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            logger.info("job_id=%s process_id=%s stopped", job_id, process.pid)
            if process.stdin is not None:
                process.stdin.close()

    try:
        with connection() as db, db.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT status FROM sync_jobs WHERE id=%s FOR UPDATE", (job_id,)
            )
            current = cursor.fetchone()
            if current is None or current["status"] in repository.TERMINAL_STATUSES:
                return  # A completed batch wins a concurrent cancellation/timeout.
            if current["status"] == "CANCELING" and failure is None:
                repository.transition_job(
                    db,
                    job_id,
                    "CANCELED",
                    expected_status="CANCELING",
                    error_code="JOB_CANCELED",
                    error_message="任务已安全取消，保留已提交数据",
                )
            else:
                code, message = failure or (
                    "WORKER_PROCESS_EXITED",
                    "任务进程未提交最终结果",
                )
                repository.fail_job(db, job_id, error_code=code, error_message=message)
    except (MySQLError, OSError):
        cleanup_failed.set()
        logger.error(
            "job_id=%s final state persistence failed; recovery required", job_id
        )
        raise


def consume(timeout_seconds):
    next_scan = 0
    with queue_client() as queue:
        while not stopped.is_set():
            job_id = None
            try:
                if monotonic() >= next_scan:
                    next_scan = monotonic() + DISPATCH_SCAN_SECONDS
                    with connection() as db:
                        repository.fail_expired_jobs(db, timeout_seconds)
                    redispatch_pending(queue)
                if stopped.is_set():
                    break
                # BLPOP removes the delivery; the committed RUNNING row confirms claim.
                item = queue.blpop(queue_name(), timeout=1)
                if item is not None:
                    job_id = item[1]
                    if stopped.is_set():
                        queue.lpush(queue_name(), job_id)
                        break
                    try:
                        if str(UUID(job_id)) != job_id:
                            raise ValueError("Non-canonical job ID")
                    except ValueError:
                        logger.warning(
                            "invalid queue message discarded (expected UUID)"
                        )
                        continue
                    logger.info("job_id=%s received", job_id)
                    if supervise_job(job_id, timeout_seconds) is False:
                        queue.lpush(queue_name(), job_id)
            except UnicodeError:
                logger.warning("invalid queue message discarded (expected UTF-8)")
            except (RedisError, MySQLError, OSError) as error:
                if job_id is not None and isinstance(error, (MySQLError, OSError)):
                    # A lost claim COMMIT acknowledgement may leave RUNNING in SQL.
                    cleanup_failed.set()
                logger.error("job_id=%s worker operation failed", job_id or "none")
                # Bound retry frequency; signal handling interrupts this wait.
                stopped.wait(1)


def worker_settings():
    concurrency = int(
        os.environ.get("WORKER_CONCURRENCY", min(4, max(2, os.cpu_count() or 2)))
    )
    timeout = int(os.environ.get("JOB_TIMEOUT_SECONDS", "300"))
    grace = int(os.environ.get("WORKER_SHUTDOWN_TIMEOUT_SECONDS", "30"))
    if concurrency <= 0 or timeout <= 0 or grace <= 0:
        raise ValueError(
            "Worker concurrency, job timeout and shutdown timeout must be positive integers"
        )
    return concurrency, timeout, grace


def run() -> None:
    global shutdown_timeout
    concurrency, timeout, shutdown_timeout = worker_settings()
    logger.info(
        "Worker started; queue=%s concurrency=%s job_timeout=%ss shutdown_timeout=%ss",
        queue_name(),
        concurrency,
        timeout,
        shutdown_timeout,
    )
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        consumers = [pool.submit(consume, timeout) for _ in range(concurrency)]
        for consumer in consumers:
            consumer.result()
    if cleanup_failed.is_set():
        logger.error("Stopped with unconfirmed task states; recovery required")
        raise SystemExit(1)
    logger.info("Stopped")


def watch_parent():
    # The parent owns the only pipe writer. EOF also detects SIGKILL/crashes.
    os.read(sys.stdin.fileno(), 1)
    os._exit(1)  # Release files/DB sessions even if the task is blocked in I/O.


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s worker %(message)s"
    )
    if len(sys.argv) == 3 and sys.argv[1] == "--execute":
        # Task children keep the default SIGTERM behavior for bounded termination.
        Thread(target=watch_parent, daemon=True).start()
        sys.exit(0 if process_job(sys.argv[2], claimed=True) else 1)
    else:
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        run()
