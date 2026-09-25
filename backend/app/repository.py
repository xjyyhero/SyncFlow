"""Parameterized SQL only; callers own transactions and business decisions."""

import json

JOB_STATUSES = {"PENDING", "RUNNING", "SUCCESS", "PARTIAL_SUCCESS", "FAILED"}


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
            last_error = {}
        cursor.execute(
            """UPDATE sync_jobs SET status = %s, total_records = %s,
               success_records = %s, failed_records = %s,
               last_error_code = %s, last_error_message = %s,
               finished_at = IF(%s, CURRENT_TIMESTAMP(3), NULL) WHERE id = %s""",
            (
                status,
                total,
                success,
                failed,
                last_error.get("error_code"),
                last_error.get("error_message"),
                end == total,
                job_id,
            ),
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
    with db.cursor() as cursor:
        cursor.execute(
            """UPDATE sync_jobs SET status = 'FAILED', last_error_code = %s,
               last_error_message = %s, finished_at = CURRENT_TIMESTAMP(3),
               total_records = COALESCE(%s, total_records),
               failed_records = IF(%s IS NULL, failed_records, %s - success_records)
               WHERE id = %s AND status IN ('PENDING', 'RUNNING')""",
            (
                error_code,
                error_message,
                total_records,
                total_records,
                total_records,
                job_id,
            ),
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
