"""MySQL connections and re-runnable initialization/migrations."""

import os
from contextlib import contextmanager
from pathlib import Path

import mysql.connector


@contextmanager
def connection():
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
        connection_timeout=5,
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
        if status_check and "PARTIAL_SUCCESS" not in status_check[0]:
            cursor.execute(
                """ALTER TABLE sync_jobs DROP CHECK ck_sync_jobs_status,
                   ADD CONSTRAINT ck_sync_jobs_status CHECK
                   (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'PARTIAL_SUCCESS', 'FAILED'))"""
            )


def check_mysql():
    with connection() as db, db.cursor() as cursor:
        cursor.execute("SELECT 1")
        return cursor.fetchone() == (1,)


if __name__ == "__main__":
    initialize()
    print("MySQL tables initialized and migrations applied.")
