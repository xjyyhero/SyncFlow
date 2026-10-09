"""MySQL connections and re-runnable initialization/migrations."""

import os
from contextlib import contextmanager
from pathlib import Path

import mysql.connector


@contextmanager
def connection(*, timeout=5):
    """One transaction per context; always close, rollback on failure."""
    db = mysql.connector.connect(
        host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        database=os.environ["MYSQL_DATABASE"],
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        charset="utf8mb4",
        time_zone="+00:00",
        sql_mode="STRICT_TRANS_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE,NO_ENGINE_SUBSTITUTION",
        connection_timeout=timeout,
        read_timeout=timeout,
        write_timeout=timeout,
        autocommit=False,
    )
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def initialize():
    with connection() as db, db.cursor() as cursor:
        cursor.execute(Path(__file__).with_name("schema.sql").read_text())
        while cursor.nextset():
            pass
        cursor.execute(
            """SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS
               WHERE CONSTRAINT_SCHEMA = DATABASE()
               AND CONSTRAINT_NAME = 'ck_sync_jobs_status'"""
        )
        status_check = cursor.fetchone()
        if status_check and any(
            status not in status_check[0]
            for status in ("PARTIAL_SUCCESS", "RETRYING", "CANCELING", "CANCELED")
        ):
            cursor.execute(
                """ALTER TABLE sync_jobs DROP CHECK ck_sync_jobs_status,
                   ADD CONSTRAINT ck_sync_jobs_status CHECK
                   (status IN ('PENDING', 'RUNNING', 'RETRYING', 'SUCCESS', 'PARTIAL_SUCCESS', 'FAILED', 'CANCELING', 'CANCELED'))"""
            )


def check_mysql():
    with connection() as db, db.cursor() as cursor:
        cursor.execute("SELECT 1")
        return cursor.fetchone() == (1,)


if __name__ == "__main__":
    initialize()
    print("MySQL tables initialized and migrations applied.")
