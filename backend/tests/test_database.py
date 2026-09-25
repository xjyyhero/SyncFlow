"""Real MySQL checks. Run exclusively in the isolated compose.test.yaml project."""

import os
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app import repository as repo
from app.csv_source import read_csv
from app.database import check_mysql, connection, initialize
from app.main import readyz
from mysql.connector import Error, IntegrityError
from reporting import EvidenceCase


def job(db, job_id="a"):
    return repo.create_job(
        db,
        job_id=job_id,
        name="测试导入",
        source_file_name="测试.csv",
        stored_file_path=f"/uploads/{job_id}.csv",
        file_sha256="a" * 64,
    )


class DatabaseTests(EvidenceCase):
    @classmethod
    def setUpClass(cls):
        if (os.environ.get("MYSQL_DATABASE"), os.environ.get("MYSQL_HOST")) != (
            "syncflow_test",
            "mysql-test",
        ):
            raise RuntimeError("Tests require the isolated mysql-test service")
        initialize()

    def setUp(self):
        self.evidence = []
        with connection() as db, db.cursor() as cursor:
            for table in ("sync_errors", "sync_records", "sync_jobs"):
                cursor.execute(f"DELETE FROM {table}")

    def test_repeat_initialization_preserves_schema_and_data(self):
        """重复初始化：先写入任务 a，再初始化两遍，表结构与任务保留。"""
        with connection() as db:
            job(db)

        def schema():
            with connection() as db, db.cursor() as cursor:
                result = []
                for table in ("sync_jobs", "sync_records", "sync_errors"):
                    cursor.execute(f"SHOW CREATE TABLE {table}")
                    result.append(cursor.fetchone()[1])
                return result

        before = schema()
        initialize()
        initialize()
        self.assertEqual(before, schema())
        with connection() as db, db.cursor() as cursor:
            self.assertIsNotNone(repo.get_job(db, "a"))
            cursor.execute("SHOW TABLES")
            self.assertEqual(
                {r[0] for r in cursor.fetchall()},
                {"sync_jobs", "sync_records", "sync_errors"},
            )

    def test_defaults_utc_and_state_transitions(self):
        """状态流转：PENDING → RUNNING → SUCCESS；跳步和重复更新不生效。"""
        with connection() as db, db.cursor() as cursor:
            row = job(db)
            self.assertEqual(row["status"], "PENDING")
            for field in (
                "total_records",
                "success_records",
                "failed_records",
                "retry_count",
            ):
                self.assertEqual(row[field], 0)
            self.assertIsNone(row["started_at"])
            self.assertIsNone(row["finished_at"])
            self.assertIsNone(row["idempotency_key"])
            cursor.execute("SELECT @@session.time_zone")
            self.assertEqual(cursor.fetchone()[0], "+00:00")
            self.assertFalse(repo.complete_job(db, "a"))
            self.assertTrue(repo.start_job(db, "a"))
            started = repo.get_job(db, "a")["started_at"]
            self.assertFalse(repo.start_job(db, "a"))
            self.assertTrue(repo.complete_job(db, "a"))
            self.assertFalse(repo.complete_job(db, "a"))
            row = repo.get_job(db, "a")
            self.assertEqual(row["started_at"], started)
            self.assertGreaterEqual(row["finished_at"], started)
            self.assertEqual(row["status"], "SUCCESS")
            self.assertIsNone(repo.get_job(db, "missing"))
            self.assertFalse(repo.start_job(db, "missing"))

    def test_week2_status_constraint_upgrades_without_data_loss(self):
        """从第二周四状态约束升级，保留旧任务；重复迁移后支持 PARTIAL_SUCCESS。"""
        with connection() as db, db.cursor() as cursor:
            job(db)
            cursor.execute(
                """ALTER TABLE sync_jobs DROP CHECK ck_sync_jobs_status,
                   ADD CONSTRAINT ck_sync_jobs_status CHECK
                   (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED'))"""
            )
        try:
            initialize()
            initialize()
            with connection() as db, db.cursor() as cursor:
                self.assertEqual(repo.get_job(db, "a")["name"], "测试导入")
                cursor.execute(
                    "UPDATE sync_jobs SET status='PARTIAL_SUCCESS' WHERE id='a'"
                )
                self.assertEqual(
                    repo.list_jobs(db, status="PARTIAL_SUCCESS")["meta"]["total"], 1
                )
                with self.assertRaises(Error):
                    cursor.execute("UPDATE sync_jobs SET status='INVALID' WHERE id='a'")
        finally:
            initialize()

    def test_stable_pagination_filter_and_injection(self):
        """分页示例：同时间插入 a/b/c，每页 2 条，第一页 c/b、第二页 a。"""
        with connection() as db, db.cursor() as cursor:
            for job_id in ("a", "b", "c"):
                job(db, job_id)
            cursor.execute(
                "UPDATE sync_jobs SET created_at = '2026-01-01 00:00:00.123'"
            )
            self.assertEqual(
                [r["id"] for r in repo.list_jobs(db, page_size=2)["data"]], ["c", "b"]
            )
            second = repo.list_jobs(db, page=2, page_size=2)
            self.assertEqual([r["id"] for r in second["data"]], ["a"])
            self.assertEqual(second["meta"], {"page": 2, "page_size": 2, "total": 3})
            self.assertEqual(repo.list_jobs(db, page=4)["data"], [])
            self.assertEqual(repo.list_jobs(db, page_size=100)["meta"]["total"], 3)
            repo.start_job(db, "b")
            self.assertEqual(repo.list_jobs(db, status="RUNNING")["data"][0]["id"], "b")
            self.assertEqual(repo.list_jobs(db, status="SUCCESS")["meta"]["total"], 0)
            self.assertIsNone(repo.get_job(db, "a' OR '1'='1"))
            for params in (
                {"page": 0},
                {"page_size": 0},
                {"page_size": 101},
                {"page": True},
                {"page": "1"},
                {"status": "BAD"},
            ):
                with self.assertRaises(ValueError):
                    repo.list_jobs(db, **params)

    def test_rollback_and_atomic_failure_record(self):
        """事务示例：故意中断后回滚；失败状态与错误记录同时提交或回滚。"""
        with self.assertRaises(RuntimeError), connection() as db:
            job(db)
            raise RuntimeError("abort")
        with connection() as db:
            self.assertIsNone(repo.get_job(db, "a"))
            job(db)
        with self.assertRaises(RuntimeError), connection() as db:
            repo.fail_job(db, "a", error_code="QUEUE_ERROR", error_message="投递失败")
            raise RuntimeError("abort")
        with connection() as db, db.cursor() as cursor:
            self.assertEqual(repo.get_job(db, "a")["status"], "PENDING")
            cursor.execute("SELECT COUNT(*) FROM sync_errors")
            self.assertEqual(cursor.fetchone()[0], 0)
            self.assertTrue(
                repo.fail_job(
                    db, "a", error_code="QUEUE_ERROR", error_message="投递失败"
                )
            )
            self.assertFalse(
                repo.fail_job(db, "a", error_code="QUEUE_ERROR", error_message="重复")
            )
            self.assertEqual(repo.get_job(db, "a")["last_error_code"], "QUEUE_ERROR")
            cursor.execute("SELECT `row_number`, raw_row FROM sync_errors")
            self.assertEqual(cursor.fetchall(), [(None, None)])
            repo.add_error(
                db,
                job_id="a",
                row_number=2,
                field_name="amount",
                error_code="INVALID_AMOUNT",
                error_message="金额错误",
                raw_row={"amount": "错误"},
            )
            cursor.execute(
                "SELECT JSON_UNQUOTE(JSON_EXTRACT(raw_row, '$.amount')) FROM sync_errors WHERE `row_number` = 2"
            )
            self.assertEqual(cursor.fetchone()[0], "错误")

    def test_record_precision_uppercase_and_uniqueness(self):
        """约束示例：1234567890.12 精确存储；重复标识、小写、负计数被拒绝。"""
        sql = """INSERT INTO sync_records
                 (job_id, external_id, name, amount, record_date)
                 VALUES (%s, %s, '记录', %s, '2026-01-01')"""
        with connection() as db, db.cursor() as cursor:
            job(db)
            job(db, "b")
            cursor.execute(sql, ("a", "ABC", Decimal("1234567890.12")))
            cursor.execute("SELECT amount FROM sync_records")
            self.assertEqual(cursor.fetchone()[0], Decimal("1234567890.12"))
            with self.assertRaises(IntegrityError):
                cursor.execute(sql, ("a", "ABC", Decimal("1.00")))
            cursor.execute(sql, ("b", "ABC", Decimal("1.00")))
            with self.assertRaises(Error):
                cursor.execute(sql, ("a", "lowercase", Decimal("1.00")))
            with self.assertRaises(Error):
                cursor.execute("UPDATE sync_jobs SET status = 'BAD' WHERE id = 'a'")
            with self.assertRaises(Error):
                cursor.execute("UPDATE sync_jobs SET total_records = -1 WHERE id = 'a'")

    def test_csv_normalized_values_round_trip_in_mysql(self):
        """真实 CSV 规范化后存入 DECIMAL(12,2)/DATE，边界精确且金额越界被拒绝。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "values.csv"
            path.write_text(
                "external_id,name,amount,record_date\n"
                " min , 最小值 ,0,1000-01-01\n"
                " max , 最大值 ,9999999999.99,9999-12-31\n"
                " normal , 商品 ,19.9,2024-02-29\n",
                encoding="utf-8-sig",
            )
            parsed = read_csv(path)
        self.assertEqual(parsed.errors, [])
        sql = """INSERT INTO sync_records
                 (job_id, external_id, name, amount, record_date)
                 VALUES (%s, %s, %s, %s, %s)"""
        with connection() as db, db.cursor() as cursor:
            job(db)
            cursor.executemany(
                sql,
                [
                    (
                        "a",
                        row["external_id"],
                        row["name"],
                        row["amount"],
                        row["record_date"],
                    )
                    for row in parsed.records
                ],
            )
            with self.assertRaises(Error):
                cursor.execute(
                    sql, ("a", "OVER", "越界", Decimal(10000000000), date(2026, 1, 1))
                )
        with connection() as db, db.cursor() as cursor:
            cursor.execute(
                "SELECT external_id, name, amount, record_date FROM sync_records ORDER BY id"
            )
            self.assertEqual(
                cursor.fetchall(),
                [
                    ("MIN", "最小值", Decimal("0.00"), date(1000, 1, 1)),
                    ("MAX", "最大值", Decimal("9999999999.99"), date(9999, 12, 31)),
                    ("NORMAL", "商品", Decimal("19.90"), date(2024, 2, 29)),
                ],
            )
            cursor.execute(
                """SELECT column_name, column_type FROM information_schema.columns
                   WHERE table_schema = DATABASE() AND table_name = 'sync_records'
                   AND column_name IN ('amount', 'record_date') ORDER BY column_name"""
            )
            self.assertEqual(
                cursor.fetchall(),
                [("amount", "decimal(12,2)"), ("record_date", "date")],
            )

    def test_health_and_real_connection_failure(self):
        """连接示例：正常返回可用；连接端口 1 失败后返回 503，响应不含凭据。"""
        self.assertTrue(check_mysql())
        self.assertEqual(readyz()["checks"]["mysql"], "ok")
        with patch.dict(os.environ, {"MYSQL_PORT": "1"}):
            with self.assertRaises(Error):
                check_mysql()
            response = readyz()
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(b"mysql-test", response.body)
            self.assertNotIn(b"test-only-password", response.body)
            self.assertIn(b"DATABASE_UNAVAILABLE", response.body)


if __name__ == "__main__":
    unittest.main()
