"""Parameterized SQL only; callers own transactions and business decisions."""

import json

JOB_STATUSES = {"PENDING", "RUNNING", "SUCCESS", "FAILED"}


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


def list_jobs(db, *, page=1, page_size=20, status=None):
    if (
        type(page) is not int
        or page < 1
        or type(page_size) is not int
        or not 1 <= page_size <= 100
        or (status is not None and status not in JOB_STATUSES)
    ):
        raise ValueError("Invalid pagination or job status")
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


def start_job(db, job_id):
    with db.cursor() as cursor:
        cursor.execute(
            """UPDATE sync_jobs SET status = 'RUNNING',
               started_at = COALESCE(started_at, CURRENT_TIMESTAMP(3))
               WHERE id = %s AND status = 'PENDING'""",
            (job_id,),
        )
        return cursor.rowcount == 1


def complete_job(db, job_id):
    with db.cursor() as cursor:
        cursor.execute(
            """UPDATE sync_jobs SET status = 'SUCCESS',
               finished_at = CURRENT_TIMESTAMP(3)
               WHERE id = %s AND status = 'RUNNING'""",
            (job_id,),
        )
        return cursor.rowcount == 1


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


def fail_job(db, job_id, *, error_code, error_message):
    """Caller transaction makes the status update and error insert atomic."""
    with db.cursor() as cursor:
        cursor.execute(
            """UPDATE sync_jobs SET status = 'FAILED', last_error_code = %s,
               last_error_message = %s, finished_at = CURRENT_TIMESTAMP(3)
               WHERE id = %s AND status IN ('PENDING', 'RUNNING')""",
            (error_code, error_message, job_id),
        )
        if cursor.rowcount != 1:
            return False
    add_error(db, job_id=job_id, error_code=error_code, error_message=error_message)
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
