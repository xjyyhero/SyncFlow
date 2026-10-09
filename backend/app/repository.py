"""Parameterized SQL only; callers own transactions and business decisions."""

import json
import logging
from typing import get_args

from app.api_contract import APIError, JobStatus

logger = logging.getLogger(__name__)
JOB_STATUSES = set(get_args(JobStatus))
TRANSITIONS = {
    "PENDING": {"RUNNING", "CANCELED"},
    "RUNNING": {"SUCCESS", "PARTIAL_SUCCESS", "RETRYING", "FAILED", "CANCELING"},
    "RETRYING": {"PENDING", "FAILED", "CANCELED"},
    "CANCELING": {"CANCELED", "FAILED"},
    "SUCCESS": set(),
    "PARTIAL_SUCCESS": set(),
    "FAILED": set(),
    "CANCELED": set(),
}
TERMINAL_STATUSES = {status for status, targets in TRANSITIONS.items() if not targets}


def create_job(db, *, job_id, name, source_file_name, stored_file_path, file_sha256):
    with db.cursor() as cursor:
        cursor.execute(
            """INSERT INTO sync_jobs
               (id, name, source_file_name, stored_file_path, file_sha256)
               VALUES (%s, %s, %s, %s, %s)""",
            (job_id, name, source_file_name, stored_file_path, file_sha256),
        )
    return get_job(db, job_id)


def get_job(db, job_id):
    """Internal row, including storage path; API must select public fields."""
    with db.cursor(dictionary=True) as cursor:
        cursor.execute("SELECT * FROM sync_jobs WHERE id = %s", (job_id,))
        return cursor.fetchone()


def validate_pagination(page, page_size):
    if (
        type(page) is not int
        or page < 1
        or type(page_size) is not int
        or not 1 <= page_size <= 100
    ):
        raise ValueError("Invalid pagination")


def list_jobs(db, *, page=1, page_size=20, status=None):
    validate_pagination(page, page_size)
    if status is not None and status not in JOB_STATUSES:
        raise ValueError("Invalid job status")
    where = " WHERE status = %s" if status is not None else ""
    params = (status,) if status is not None else ()
    with db.cursor(dictionary=True) as cursor:
        cursor.execute("SELECT COUNT(*) AS total FROM sync_jobs" + where, params)
        total = cursor.fetchone()["total"]
        if (page - 1) * page_size >= total:
            return {
                "data": [],
                "meta": {"page": page, "page_size": page_size, "total": total},
            }
        cursor.execute(
            "SELECT * FROM sync_jobs"
            + where
            + " ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
            (*params, page_size, (page - 1) * page_size),
        )
        return {
            "data": cursor.fetchall(),
            "meta": {
                "page": page,
                "page_size": page_size,
                "total": total,
            },
        }


def list_job_errors(db, job_id, *, page=1, page_size=20):
    validate_pagination(page, page_size)
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS total FROM sync_errors WHERE job_id = %s", (job_id,)
        )
        total = cursor.fetchone()["total"]
        result = {
            "data": [],
            "meta": {"page": page, "page_size": page_size, "total": total},
        }
        offset = (page - 1) * page_size
        if offset >= total:
            return result
        cursor.execute(
            """SELECT job_id, `row_number`, field_name, error_code,
                      error_message, raw_row, created_at FROM sync_errors
               WHERE job_id = %s ORDER BY `row_number` ASC, id ASC LIMIT %s OFFSET %s""",
            (job_id, page_size, offset),
        )
        result["data"] = cursor.fetchall()
        for row in result["data"]:
            if row["raw_row"] is not None:
                row["raw_row"] = json.loads(row["raw_row"])
        return result


def transition_job(
    db, job_id, status, *, error_message, error_code=None, expected_status=None
):
    """Guard every state change in SQL; caller owns commit/rollback.

    expected_status additionally guards actions tied to one observed state.
    Worker claiming handles conflicts as a normal duplicate-message skip.
    """
    if not error_message or not error_message.strip():
        raise ValueError("A transition reason is required")
    sources = sorted(
        source
        for source, targets in TRANSITIONS.items()
        if status in targets and (expected_status is None or source == expected_status)
    )
    with db.cursor() as cursor:
        if sources:
            placeholders = ", ".join(["%s"] * len(sources))
            cursor.execute(
                f"""UPDATE sync_jobs SET status = %s,
                    updated_at = CURRENT_TIMESTAMP(3),
                    started_at = IF(%s, CURRENT_TIMESTAMP(3), started_at),
                    finished_at = IF(%s, CURRENT_TIMESTAMP(3), NULL),
                    last_error_code = %s, last_error_message = %s
                    WHERE id = %s AND status IN ({placeholders})""",
                (
                    status,
                    status == "RUNNING",
                    status in TERMINAL_STATUSES,
                    error_code,
                    error_message,
                    job_id,
                    *sources,
                ),
            )
            if cursor.rowcount == 1:
                logger.info(
                    "job_id=%s status=%s reason=%s", job_id, status, error_message
                )
                return True
        # Locking read sees the latest committed status, even under REPEATABLE READ.
        cursor.execute(
            "SELECT status FROM sync_jobs WHERE id = %s FOR UPDATE", (job_id,)
        )
        current = cursor.fetchone()
    if current is None:
        raise APIError("JOB_NOT_FOUND")
    logger.warning(
        "job_id=%s illegal_transition=%s->%s expected=%s",
        job_id,
        current[0],
        status,
        expected_status,
    )
    raise APIError("INVALID_JOB_TRANSITION")


def start_job(db, job_id):
    try:
        return transition_job(db, job_id, "RUNNING", error_message="Worker 已领取任务")
    except APIError as error:
        if error.code not in {"JOB_NOT_FOUND", "INVALID_JOB_TRANSITION"}:
            raise
        return False


def complete_job(db, job_id):
    return transition_job(db, job_id, "SUCCESS", error_message="所有有效记录写入成功")


def request_cancel(db, job_id):
    # Serialize cancellation with claim and batch commit; never overwrite a result.
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(
            "SELECT status FROM sync_jobs WHERE id = %s FOR UPDATE", (job_id,)
        )
        row = cursor.fetchone()
    if row is None:
        raise APIError("JOB_NOT_FOUND")
    status = "CANCELING" if row["status"] == "RUNNING" else "CANCELED"
    if row["status"] == "CANCELING":
        # A second request must not claim the running process has stopped.
        logger.warning("job_id=%s cancellation already requested", job_id)
        raise APIError("INVALID_JOB_TRANSITION")
    transition_job(
        db,
        job_id,
        status,
        expected_status=row["status"],
        error_code="CANCEL_REQUESTED" if status == "CANCELING" else "JOB_CANCELED",
        error_message="已请求取消，等待 Worker 安全停止"
        if status == "CANCELING"
        else "任务已取消",
    )
    return get_job(db, job_id)


def add_records(db, job_id, records):
    if not records:
        return
    with db.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO sync_records
               (job_id, external_id, name, amount, record_date)
               VALUES (%s, %s, %s, %s, %s)""",
            [
                (
                    job_id,
                    row["external_id"],
                    row["name"],
                    row["amount"],
                    row["record_date"],
                )
                for row in records
            ],
        )


def save_csv_batch(db, job_id, rows, *, offset, total):
    """Commit batch data and progress together; counters also guard lost ACKs."""
    with db.cursor(dictionary=True) as cursor:
        cursor.execute("SELECT * FROM sync_jobs WHERE id = %s FOR UPDATE", (job_id,))
        job = cursor.fetchone()
        if job is None:
            return None
        processed = job["success_records"] + job["failed_records"]
        end = offset + len(rows)
        if processed == end and job["total_records"] == total:
            return job  # A previous commit succeeded but its acknowledgement was lost.
        if job["status"] != "RUNNING" or processed != offset:
            return None
        records = [row["record"] for row in rows if "record" in row]
        errors = [row["error"] for row in rows if "error" in row]
        add_records(db, job_id, records)
        for error in errors:
            add_error(db, job_id=job_id, **error)
        success = job["success_records"] + len(records)
        failed = job["failed_records"] + len(errors)
        status = "RUNNING"
        if end == total:
            status = (
                "FAILED" if not success else "PARTIAL_SUCCESS" if failed else "SUCCESS"
            )
        last_error = (
            errors[-1]
            if errors
            else {
                "error_code": job["last_error_code"],
                "error_message": job["last_error_message"],
            }
        )
        if not failed:
            last_error = {"error_code": None, "error_message": "正在写入记录"}
            if end == total:
                last_error["error_message"] = "所有有效记录写入成功"
        cursor.execute(
            """UPDATE sync_jobs SET total_records = %s,
               success_records = %s, failed_records = %s,
               last_error_code = %s, last_error_message = %s,
               updated_at = CURRENT_TIMESTAMP(3) WHERE id = %s""",
            (
                total,
                success,
                failed,
                last_error.get("error_code"),
                last_error.get("error_message"),
                job_id,
            ),
        )
    if end == total:
        transition_job(
            db,
            job_id,
            status,
            error_code=last_error.get("error_code"),
            error_message=last_error.get("error_message") or "所有有效记录写入成功",
            expected_status="RUNNING",
        )
    return get_job(db, job_id)


def add_error(
    db,
    *,
    job_id,
    error_code,
    error_message,
    row_number=None,
    field_name=None,
    raw_row=None,
):
    with db.cursor() as cursor:
        cursor.execute(
            """INSERT INTO sync_errors
               (job_id, `row_number`, field_name, error_code, error_message, raw_row)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                job_id,
                row_number,
                field_name,
                error_code,
                error_message,
                json.dumps(raw_row, ensure_ascii=False)
                if raw_row is not None
                else None,
            ),
        )
        return cursor.lastrowid


def fail_job(db, job_id, *, error_code, error_message, total_records=None):
    """Caller transaction makes the status update and error insert atomic."""
    transition_job(
        db, job_id, "FAILED", error_code=error_code, error_message=error_message
    )
    if total_records is not None:
        with db.cursor() as cursor:
            cursor.execute(
                """UPDATE sync_jobs SET total_records = %s,
                   failed_records = %s - success_records WHERE id = %s""",
                (total_records, total_records, job_id),
            )
    add_error(db, job_id=job_id, error_code=error_code, error_message=error_message)
    return True


def cancel_dispatch(db, job_id):
    """A failed/ambiguous publish may only cancel a still-pending task."""
    try:
        transition_job(
            db,
            job_id,
            "CANCELED",
            expected_status="PENDING",
            error_code="QUEUE_DISPATCH_FAILED",
            error_message="任务队列投递失败，已取消尚未开始的任务",
        )
    except APIError as error:
        if error.code != "INVALID_JOB_TRANSITION":
            raise
        # Redis may have accepted the message before losing its acknowledgement.
        # If a worker claimed it, preserve that worker's state and result.
        return False
    add_error(
        db,
        job_id=job_id,
        error_code="QUEUE_DISPATCH_FAILED",
        error_message="任务队列投递失败，已取消尚未开始的任务",
    )
    return True


def mark_dispatch_pending(db, job_id):
    with db.cursor() as cursor:
        cursor.execute(
            """UPDATE sync_jobs SET last_error_code = 'QUEUE_DISPATCH_PENDING',
               last_error_message = '任务尚未确认投递到队列' WHERE id = %s""",
            (job_id,),
        )


def clear_dispatch_pending(db, job_id):
    with db.cursor() as cursor:
        cursor.execute(
            """UPDATE sync_jobs SET last_error_code = NULL, last_error_message = NULL
               WHERE id = %s AND last_error_code = 'QUEUE_DISPATCH_PENDING'""",
            (job_id,),
        )


def stale_pending_jobs(db):
    """Bound each recovery pass; MySQL is the source of truth for missing messages."""
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(
            """SELECT id FROM sync_jobs WHERE status = 'PENDING'
               AND updated_at < CURRENT_TIMESTAMP(3) - INTERVAL 60 SECOND
               ORDER BY updated_at, id LIMIT 100"""
        )
        return cursor.fetchall()


def fail_expired_jobs(db, timeout_seconds):
    """Fence overdue executions without replay; skip batches currently committing."""
    with db.cursor() as cursor:
        cursor.execute(
            """SELECT id FROM sync_jobs WHERE status IN ('RUNNING', 'CANCELING')
               AND COALESCE(started_at, updated_at)
                   < CURRENT_TIMESTAMP(3) - INTERVAL %s SECOND
               ORDER BY started_at, id LIMIT 100 FOR UPDATE SKIP LOCKED""",
            (timeout_seconds,),
        )
        ids = [row[0] for row in cursor.fetchall()]
    for job_id in ids:
        fail_job(
            db,
            job_id,
            error_code="WORKER_EXECUTION_EXPIRED",
            error_message="恢复扫描发现执行已超时，任务已终止，保留已提交数据",
        )
    return ids


def mark_redispatched(db, job_id):
    with db.cursor() as cursor:
        cursor.execute(
            """UPDATE sync_jobs SET updated_at = CURRENT_TIMESTAMP(3),
               last_error_code = 'QUEUE_REDISPATCHED',
               last_error_message = '等待领取超时，任务已重新投递'
               WHERE id = %s AND status = 'PENDING'""",
            (job_id,),
        )
