"""MySQL connections and re-runnable Week 2 initialization."""

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


def check_mysql():
    with connection() as db, db.cursor() as cursor:
        cursor.execute("SELECT 1")
        return cursor.fetchone() == (1,)


if __name__ == "__main__":
    initialize()
    print("Week 2 MySQL tables initialized.")
