"""Worker integration checks using real Redis and MySQL, isolated from development."""

import csv
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app import repository
from app.csv_source import HEADER
from app.database import connection, initialize
from app.jobs import queue_client
from app.main import app
from app.worker import BATCH_SIZE, process_job, write_batch
from fastapi.testclient import TestClient
from httpx import Client, HTTPError
from mysql.connector import Error as MySQLError
from reporting import EvidenceCase


class WorkerTests(EvidenceCase):
    @classmethod
    def setUpClass(cls):
        if (
            os.environ.get("MYSQL_HOST"),
            os.environ.get("MYSQL_DATABASE"),
            os.environ.get("REDIS_ADDR"),
        ) != ("mysql-test", "syncflow_test", "redis-test:6379"):
            raise RuntimeError("Worker tests require isolated MySQL and Redis")
        initialize()

    def setUp(self):
        self.evidence = []
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.key = f"syncflow:worker-test:{uuid4()}"
        env = patch.dict(
            os.environ, {"UPLOAD_DIR": str(self.root), "REDIS_JOB_QUEUE": self.key}
        )
        env.start()
        self.addCleanup(env.stop)
        self.queue = queue_client()
        self.addCleanup(self.queue.close)
        self.addCleanup(self.queue.delete, self.key)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        with connection() as db, db.cursor() as cursor:
            for table in ("sync_errors", "sync_records", "sync_jobs"):
                cursor.execute(f"DELETE FROM {table}")

    def create(
        self, content=b"external_id,name,amount,record_date\nA1,test,19.90,2026-01-01\n"
    ):
        response = self.client.post(
            "/api/v1/jobs",
            files={"file": ("worker.csv", content)},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["data"]["id"]

    def row(self, job_id):
        with connection() as db:
            return repository.get_job(db, job_id)

    def errors(self, job_id):
        with connection() as db, db.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT * FROM sync_errors WHERE job_id=%s ORDER BY id", (job_id,)
            )
            return cursor.fetchall()

    def records(self, job_id):
        with connection() as db, db.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT * FROM sync_records WHERE job_id=%s ORDER BY id", (job_id,)
            )
            return cursor.fetchall()

    def csv_rows(self, count):
        return [[f"S{i}", "商品", "19.90", "2026-01-01"] for i in range(count)]

    def create_rows(self, rows):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(HEADER)
        writer.writerows(rows)
        return self.create(stream.getvalue().encode())

    def counts(self, job_id):
        row = self.row(job_id)
        return (row["total_records"], row["success_records"], row["failed_records"])

    def test_batch_boundaries_and_committed_progress(self):
        """250 行事务边界；每批提交后独立连接可见准确进度，最后一批原子完成。"""
        self.assertEqual(BATCH_SIZE, 250)
        for size in (249, 250, 251, 500, 501):
            with self.subTest(size=size):
                job_id = self.create_rows(self.csv_rows(size))
                snapshots = []

                def observe(
                    current_id, rows, offset, total, snapshots=snapshots, size=size
                ):
                    result = write_batch(current_id, rows, offset, total)
                    row = self.row(current_id)
                    count = len(self.records(current_id))
                    snapshots.append(count)
                    self.assertEqual(
                        self.counts(current_id), (size, offset + len(rows), 0)
                    )
                    self.assertEqual(count, offset + len(rows))
                    self.assertEqual(
                        row["status"], "SUCCESS" if count == size else "RUNNING"
                    )
                    self.assertEqual(row["finished_at"] is None, count != size)
                    return result

                with patch("app.worker.write_batch", side_effect=observe):
                    self.assertTrue(process_job(job_id))
                self.assertEqual(snapshots, list(range(250, size, 250)) + [size])
                self.assertEqual(self.errors(job_id), [])

    def test_middle_batch_unique_violation_rolls_back_only_that_batch(self):
        """第二批写入后触发真实唯一约束冲突：整批回滚，前后批保留，原行错误保留。"""
        rows = self.csv_rows(501)
        rows[270][2] = " -1 "
        job_id = self.create_rows(rows)
        original = repository.add_records

        def violate_unique(db, current_id, records):
            original(db, current_id, records)
            if records and records[0]["external_id"] == "S250":
                duplicate = {**records[0], "external_id": "S0"}
                original(db, current_id, [duplicate])

        with patch("app.repository.add_records", side_effect=violate_unique):
            self.assertTrue(process_job(job_id))
        self.assertEqual(self.counts(job_id), (501, 251, 250))
        self.assertEqual(self.row(job_id)["status"], "PARTIAL_SUCCESS")
        self.assertEqual(
            [r["external_id"] for r in self.records(job_id)],
            [f"S{i}" for i in range(250)] + ["S500"],
        )
        errors = self.errors(job_id)
        self.assertEqual(len(errors), 250)
        self.assertEqual([e["row_number"] for e in errors], list(range(252, 502)))
        for i, error in enumerate(errors):
            self.assertEqual(json.loads(error["raw_row"]), rows[250 + i])
            if error["row_number"] == 272:
                self.assertEqual(error["error_code"], "AMOUNT_INVALID")
                self.assertEqual(error["field_name"], "amount")
            else:
                self.assertEqual(error["error_code"], "BATCH_WRITE_FAILED")
                self.assertIsNone(error["field_name"])
                self.assertIn("第 2 批", error["error_message"])
                self.assertIn("252–501", error["error_message"])
                self.assertIn("1062", error["error_message"])

    def test_all_batches_system_write_failure(self):
        """所有合法行系统写入失败：每批均回滚，逐行记错，总数等于失败数。"""
        job_id = self.create_rows(self.csv_rows(251))
        original = repository.add_records

        def reject_records(db, current_id, records):
            original(db, current_id, records)
            if records:
                raise MySQLError("private SQL password", errno=1406)

        with patch("app.repository.add_records", side_effect=reject_records):
            self.assertTrue(process_job(job_id))
        self.assertEqual(self.counts(job_id), (251, 0, 251))
        self.assertEqual(self.row(job_id)["status"], "FAILED")
        self.assertEqual(self.records(job_id), [])
        errors = self.errors(job_id)
        self.assertEqual(len(errors), 251)
        self.assertEqual({e["error_code"] for e in errors}, {"BATCH_WRITE_FAILED"})
        self.assertNotIn("private", str(errors))

    def test_duplicate_across_batches_uses_original_line_error(self):
        """跨 250 行边界的大小写重复仍为行校验错误，之后合法行继续入库。"""
        rows = self.csv_rows(252)
        rows[250][0] = " s0 "
        job_id = self.create_rows(rows)
        self.assertTrue(process_job(job_id))
        self.assertEqual(self.counts(job_id), (252, 251, 1))
        self.assertEqual(self.errors(job_id)[0]["error_code"], "EXTERNAL_ID_DUPLICATE")
        self.assertEqual(self.errors(job_id)[0]["row_number"], 252)
        self.assertEqual(self.records(job_id)[-1]["external_id"], "S251")

    def test_file_preflight_errors_never_commit_earlier_batches(self):
        """超过一批的合法数据后出现文件级错误，所有业务记录仍为零。"""
        for code in ("FILE_ENCODING_INVALID", "CSV_MALFORMED", "FILE_TOO_MANY_ROWS"):
            with self.subTest(code=code):
                job_id = self.create_rows(self.csv_rows(501))
                path = Path(self.row(job_id)["stored_file_path"])
                if code != "FILE_TOO_MANY_ROWS":
                    with path.open("ab") as out:
                        out.write(
                            b"\xff"
                            if code == "FILE_ENCODING_INVALID"
                            else b'S502,"broken'
                        )
                with patch.dict(
                    os.environ,
                    {
                        "MAX_RECORDS_PER_JOB": "500"
                        if code == "FILE_TOO_MANY_ROWS"
                        else "10000"
                    },
                ):
                    self.assertFalse(process_job(job_id))
                self.assertEqual(self.counts(job_id), (0, 0, 0))
                self.assertEqual(self.records(job_id), [])
                self.assertEqual(self.row(job_id)["status"], "FAILED")
                self.assertEqual([e["error_code"] for e in self.errors(job_id)], [code])

    def test_committed_batch_acknowledgement_loss_does_not_double_count(self):
        """首批/末批成功或中间失败批次的提交确认丢失，核对进度后继续，不重复计数。"""
        for mode in ("first", "last", "failed_middle"):
            with self.subTest(mode=mode):
                job_id = self.create_rows(self.csv_rows(501))
                lost = False
                original = repository.add_records

                @contextmanager
                def lose_ack(job_id=job_id, mode=mode):
                    nonlocal lost
                    with connection() as db:
                        yield db
                        row = repository.get_job(db, job_id)
                    target = {"first": 250, "last": 501, "failed_middle": 500}[mode]
                    if (
                        not lost
                        and row["success_records"] + row["failed_records"] == target
                    ):
                        lost = True
                        raise MySQLError("private lost acknowledgement", errno=2013)

                def fail_middle(db, current_id, records, original=original, mode=mode):
                    original(db, current_id, records)
                    if (
                        mode == "failed_middle"
                        and records
                        and records[0]["external_id"] == "S250"
                    ):
                        raise MySQLError("private failure", errno=1213)

                with (
                    patch("app.worker.connection", lose_ack),
                    patch("app.repository.add_records", side_effect=fail_middle),
                ):
                    self.assertTrue(process_job(job_id))
                self.assertTrue(lost)
                failed = 250 if mode == "failed_middle" else 0
                self.assertEqual(self.counts(job_id), (501, 501 - failed, failed))
                self.assertEqual(len(self.errors(job_id)), failed)
                self.assertEqual(len(self.records(job_id)), 501 - failed)
                self.assertEqual(
                    self.row(job_id)["status"],
                    "PARTIAL_SUCCESS" if failed else "SUCCESS",
                )

    def test_final_batch_status_and_counts_rollback_together(self):
        """末批更新成功状态后发生事务错误，独立连接看不到假成功；回滚后记部分成功。"""
        job_id = self.create_rows(self.csv_rows(251))
        original = repository.save_csv_batch

        def fail_final(db, current_id, rows, *, offset, total):
            result = original(db, current_id, rows, offset=offset, total=total)
            if offset == 250 and "record" in rows[0]:
                self.assertEqual(result["status"], "SUCCESS")
                self.assertEqual(self.row(current_id)["status"], "RUNNING")
                self.assertEqual(self.counts(current_id), (251, 250, 0))
                raise MySQLError("private final update", errno=1213)
            return result

        with patch("app.repository.save_csv_batch", side_effect=fail_final):
            self.assertTrue(process_job(job_id))
        self.assertEqual(self.counts(job_id), (251, 250, 1))
        self.assertEqual(self.row(job_id)["status"], "PARTIAL_SUCCESS")
        self.assertIsNotNone(self.row(job_id)["finished_at"])
        self.assertEqual(len(self.records(job_id)), 250)
        self.assertEqual(self.errors(job_id)[0]["error_code"], "BATCH_WRITE_FAILED")

    def test_unrecoverable_error_persistence_preserves_prior_successes(self):
        """后批连错误明细都无法保存时终止，保留首批成功数据，剩余行计为失败。"""
        rows = self.csv_rows(501)
        rows[250][2] = "-1"
        job_id = self.create_rows(rows)
        original = repository.add_error

        def fail_row_errors(db, **kwargs):
            if kwargs.get("row_number") is not None:
                raise MySQLError("private error store", errno=1406)
            return original(db, **kwargs)

        with patch("app.repository.add_error", side_effect=fail_row_errors):
            self.assertFalse(process_job(job_id))
        self.assertEqual(self.counts(job_id), (501, 250, 251))
        self.assertEqual(len(self.records(job_id)), 250)
        self.assertEqual(self.row(job_id)["status"], "FAILED")
        self.assertEqual(
            [e["error_code"] for e in self.errors(job_id)], ["WORKER_PROCESSING_FAILED"]
        )
        self.assertIsNone(self.errors(job_id)[0]["row_number"])

    def test_running_commit_and_success_are_visible(self):
        """Worker 先提交 RUNNING 和首次开始时间，再读文件并提交 SUCCESS。"""
        job_id = self.create()
        original_open = Path.open
        checked = []

        def inspect_open(path, *args, **kwargs):
            row = self.row(job_id)
            self.assertEqual(row["status"], "RUNNING")
            self.assertIsNotNone(row["started_at"])
            self.assertIsNone(row["finished_at"])
            checked.append(True)
            return original_open(path, *args, **kwargs)

        with patch("app.worker.Path.open", inspect_open):
            self.assertTrue(process_job(job_id))
        self.assertEqual(checked, [True])
        row = self.row(job_id)
        self.assertEqual(row["status"], "SUCCESS")
        self.assertGreaterEqual(row["finished_at"], row["started_at"])
        self.assertEqual(row["total_records"], 1)
        self.assertEqual(row["success_records"], 1)
        self.assertEqual(len(self.records(job_id)), 1)
        self.assertFalse(process_job(job_id))
        repeated = self.row(job_id)
        self.assertEqual(repeated["started_at"], row["started_at"])
        self.assertEqual(repeated["finished_at"], row["finished_at"])

    def test_missing_and_uncontrolled_files_fail_safely(self):
        """文件缺失或路径位于上传目录之外时 FAILED，写入文件级错误。"""
        missing = self.create()
        Path(self.row(missing)["stored_file_path"]).unlink()
        outside = self.create()
        with connection() as db, db.cursor() as cursor:
            cursor.execute(
                "UPDATE sync_jobs SET stored_file_path='/etc/passwd' WHERE id=%s",
                (outside,),
            )
        for job_id in (missing, outside):
            self.assertFalse(process_job(job_id))
            row = self.row(job_id)
            self.assertEqual(row["status"], "FAILED")
            self.assertEqual(row["last_error_code"], "FILE_UNREADABLE")
            self.assertIsNotNone(row["finished_at"])
            self.assertNotIn("/etc/passwd", row["last_error_message"])
            with connection() as db, db.cursor() as cursor:
                cursor.execute(
                    "SELECT error_code FROM sync_errors WHERE job_id=%s", (job_id,)
                )
                self.assertEqual(cursor.fetchall(), [("FILE_UNREADABLE",)])

    def test_file_errors_persist_without_row_numbers(self):
        """文件级五类错误及损坏引号落库：FAILED、空行号、无部分业务数据。"""
        header = ",".join(HEADER) + "\n"
        cases = [
            (b"", "FILE_EMPTY"),
            (header.encode(), "FILE_EMPTY"),
            (b"\xff", "FILE_ENCODING_INVALID"),
            (b"A,name,1,2026-01-01\n", "CSV_HEADER_INVALID"),
            (b"external_id,name,amount,amount\n", "CSV_HEADER_INVALID"),
            (b"name,external_id,amount,record_date\n", "CSV_HEADER_INVALID"),
            ((header.rstrip() + ",extra\n").encode(), "CSV_HEADER_INVALID"),
            (
                (header + "A,n,1,2026-01-01\nB,n,1,2026-01-01\n").encode(),
                "FILE_TOO_MANY_ROWS",
            ),
            ((header + 'A,n,1,2026-01-01\nB,"broken\n').encode(), "CSV_MALFORMED"),
        ]
        with patch.dict(os.environ, {"MAX_RECORDS_PER_JOB": "1"}):
            for content, code in cases:
                with self.subTest(code=code, content=content):
                    job_id = self.create(content)
                    self.assertFalse(process_job(job_id))
                    row = self.row(job_id)
                    self.assertEqual(row["status"], "FAILED")
                    self.assertEqual(row["last_error_code"], code)
                    self.assertIsNotNone(row["finished_at"])
                    errors = self.errors(job_id)
                    self.assertEqual(len(errors), 1)
                    self.assertEqual(errors[0]["error_code"], code)
                    self.assertEqual(errors[0]["job_id"], job_id)
                    for field in ("row_number", "field_name", "raw_row"):
                        self.assertIsNone(errors[0][field])
                    self.assertIsNotNone(errors[0]["created_at"])
                    self.assertTrue(errors[0]["error_message"] != code)
                    self.assertEqual(self.records(job_id), [])
                    self.assertFalse(process_job(job_id))
                    self.assertEqual(len(self.errors(job_id)), 1)

    def test_all_row_codes_persist_and_later_valid_rows_continue(self):
        """七类行错误完整落库，保留原始空白与字段，后续合法行入库且部分成功可查询。"""
        rows = [
            ["A", "正常", "1", "2026-01-01"],
            ["two", "columns"],
            ["B", " \t ", "1", "2026-01-01"],
            ["!", "名称", "1", "2026-01-01"],
            [" a ", "重复", "1", "2026-01-01"],
            ["C", "名" * 129, "1", "2026-01-01"],
            ["D", "负金额", " -1 ", "2026-01-01"],
            ["E", "不存在日期", "1", "2026-02-29"],
            ["F", "后续合法", "2", "2026-01-02"],
        ]
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(HEADER)
        writer.writerows(rows)
        job_id = self.create(stream.getvalue().encode("utf-8-sig"))
        self.assertTrue(process_job(job_id))
        row = self.row(job_id)
        self.assertEqual(row["status"], "PARTIAL_SUCCESS")
        self.assertEqual(
            (row["total_records"], row["success_records"], row["failed_records"]),
            (9, 2, 7),
        )
        errors = self.errors(job_id)
        expected = [
            ("ROW_COLUMN_COUNT_MISMATCH", None),
            ("FIELD_REQUIRED", "name"),
            ("EXTERNAL_ID_INVALID", "external_id"),
            ("EXTERNAL_ID_DUPLICATE", "external_id"),
            ("NAME_TOO_LONG", "name"),
            ("AMOUNT_INVALID", "amount"),
            ("DATE_INVALID", "record_date"),
        ]
        self.assertEqual(len(errors), len(expected))
        for index, (error, (code, field)) in enumerate(zip(errors, expected)):
            self.assertEqual(error["job_id"], job_id)
            self.assertEqual(error["row_number"], index + 3)
            self.assertEqual(error["field_name"], field)
            self.assertEqual(error["error_code"], code)
            self.assertEqual(json.loads(error["raw_row"]), rows[index + 1])
            self.assertIsNotNone(error["created_at"])
            self.assertTrue(error["error_message"] != code)
        self.assertEqual([r["external_id"] for r in self.records(job_id)], ["A", "F"])
        detail = self.client.get(f"/api/v1/jobs/{job_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["last_error_code"], "DATE_INVALID")
        listing = self.client.get("/api/v1/jobs?status=PARTIAL_SUCCESS")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual([r["id"] for r in listing.json()["data"]], [job_id])
        self.assertFalse(process_job(job_id))
        self.assertEqual(len(self.errors(job_id)), 7)
        self.assertEqual(len(self.records(job_id)), 2)

    def test_all_invalid_rows_fail_without_extra_file_error(self):
        """全行校验失败：FAILED，失败数按行统计，不额外伪造文件错误。"""
        job_id = self.create(
            (",".join(HEADER) + "\nA,n,-1,2026-01-01\nB,n,1,2026-02-29\n").encode()
        )
        self.assertTrue(process_job(job_id))
        row = self.row(job_id)
        self.assertEqual(row["status"], "FAILED")
        self.assertEqual(
            (row["total_records"], row["success_records"], row["failed_records"]),
            (2, 0, 2),
        )
        self.assertEqual([e["row_number"] for e in self.errors(job_id)], [2, 3])
        self.assertEqual(self.records(job_id), [])

    def test_error_insert_failure_rolls_back_records_and_prior_errors(self):
        """错误持久化失败时回滚业务记录及已写入错误，随后记录脱敏系统失败。"""
        job_id = self.create(
            (",".join(HEADER) + "\nA,n,1,2026-01-01\nB,n,-1,2026-01-01\n").encode()
        )
        original = repository.add_error

        def fail_after_row_insert(db, **kwargs):
            result = original(db, **kwargs)
            if kwargs.get("row_number") is not None:
                raise MySQLError("private SQL or password")
            return result

        with patch("app.repository.add_error", side_effect=fail_after_row_insert):
            self.assertFalse(process_job(job_id))
        self.assertEqual(self.records(job_id), [])
        errors = self.errors(job_id)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["error_code"], "WORKER_PROCESSING_FAILED")
        self.assertIsNone(errors[0]["row_number"])
        self.assertNotIn("private", errors[0]["error_message"])
        row = self.row(job_id)
        self.assertEqual(row["status"], "FAILED")
        self.assertEqual(row["success_records"], 0)

    def test_file_failure_status_and_error_are_atomic(self):
        """文件错误写入失败时 FAILED 更新一并回滚，不能留下无错误记录的失败状态。"""
        job_id = self.create(b"")
        with (
            patch("app.repository.add_error", side_effect=MySQLError("offline")),
            self.assertRaises(MySQLError),
        ):
            process_job(job_id)
        self.assertEqual(self.row(job_id)["status"], "RUNNING")
        self.assertEqual(self.errors(job_id), [])
        self.assertEqual(self.records(job_id), [])

    def test_missing_and_nonpending_jobs_are_skipped(self):
        """不存在、FAILED、RUNNING 任务都不能被重复认领或误标成功。"""
        self.assertFalse(process_job(str(uuid4())))
        for status in ("FAILED", "RUNNING"):
            job_id = self.create()
            with connection() as db, db.cursor() as cursor:
                cursor.execute(
                    "UPDATE sync_jobs SET status=%s WHERE id=%s", (status, job_id)
                )
            self.assertFalse(process_job(job_id))
            self.assertEqual(self.row(job_id)["status"], status)

    def test_live_http_api_and_worker_complete_all_terminal_scenarios(self):
        """独立 HTTP API 先返回并入队；真实 Worker 完成四类终态，数据库故障不阻塞后续任务。"""
        with connection() as db, db.cursor() as cursor:
            cursor.execute(
                """ALTER TABLE sync_records ADD CONSTRAINT ck_worker_test_failure
                   CHECK (external_id NOT LIKE 'SYS%')"""
            )

        def remove_failure_constraint():
            with connection() as db, db.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE sync_records DROP CHECK ck_worker_test_failure"
                )

        self.addCleanup(remove_failure_constraint)
        header = ",".join(HEADER) + "\n"
        success_csv = header + "".join(
            f" s{i} , 商品 ,19.9,2026-01-01\n" for i in range(501)
        )
        cases = [
            ("success", success_csv, "SUCCESS", (501, 501, 0), None),
            (
                "mixed",
                header + "A,n,-1,2026-01-01\nB,n,2,2026-01-01\n",
                "PARTIAL_SUCCESS",
                (2, 1, 1),
                "AMOUNT_INVALID",
            ),
            ("file_error", "wrong,header\n", "FAILED", (0, 0, 0), "CSV_HEADER_INVALID"),
            (
                "database_error",
                header + "SYS1,n,1,2026-01-01\nSYS2,n,2,2026-01-01\n",
                "FAILED",
                (2, 0, 2),
                "BATCH_WRITE_FAILED",
            ),
            (
                "after_error",
                header + "LAST,n,1,2026-01-01\n",
                "SUCCESS",
                (1, 1, 0),
                None,
            ),
        ]
        processes = []
        summary = []
        with (
            socket.socket() as listener,
            tempfile.TemporaryFile(mode="w+") as api_log,
            tempfile.TemporaryFile(mode="w+") as worker_log,
        ):
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--fd",
                    str(listener.fileno()),
                ],
                pass_fds=(listener.fileno(),),
                stdout=api_log,
                stderr=api_log,
            )
            processes.append(server)
            try:
                with Client(
                    base_url=f"http://127.0.0.1:{port}", timeout=3, trust_env=False
                ) as client:
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        try:
                            if client.get("/healthz").status_code == 200:
                                break
                        except HTTPError:
                            if server.poll() is not None:
                                break
                        time.sleep(0.05)
                    self.assertIsNone(server.poll())
                    self.assertEqual(client.get("/readyz").status_code, 200)
                    job_ids = []
                    # No Worker exists yet: receiving 201/PENDING proves that the
                    # HTTP response does not depend on completion of CSV work.
                    for label, content, status, counts, code in cases:
                        started = time.monotonic()
                        response = client.post(
                            "/api/v1/jobs",
                            data={"name": label},
                            files={
                                "file": (
                                    label + ".csv",
                                    content.encode("utf-8-sig"),
                                    "text/csv",
                                )
                            },
                        )
                        elapsed = time.monotonic() - started
                        self.assertEqual(response.status_code, 201)
                        created = response.json()["data"]
                        self.assertEqual(created["status"], "PENDING")
                        job_ids.append(created["id"])
                        pending = client.get(f"/api/v1/jobs/{created['id']}").json()[
                            "data"
                        ]
                        self.assertEqual(pending["status"], "PENDING")
                        self.assertIsNone(pending["started_at"])
                        self.assertIsNone(pending["finished_at"])
                        summary.append(
                            {
                                "scenario": label,
                                "job_id": created["id"],
                                "create_seconds": round(elapsed, 4),
                            }
                        )
                    self.assertEqual(self.queue.lrange(self.key, 0, -1), job_ids)

                    # A real DB read lock pauses the first INSERT, so RUNNING can
                    # be observed over HTTP without adding delays to production.
                    with connection() as db, db.cursor() as cursor:
                        cursor.execute("LOCK TABLES sync_records READ")
                        try:
                            worker = subprocess.Popen(
                                [sys.executable, "-m", "app.worker"],
                                stdout=worker_log,
                                stderr=worker_log,
                            )
                            processes.append(worker)
                            deadline = time.monotonic() + 10
                            while time.monotonic() < deadline:
                                running = client.get(
                                    f"/api/v1/jobs/{job_ids[0]}"
                                ).json()["data"]
                                if (
                                    running["status"] == "RUNNING"
                                    or worker.poll() is not None
                                ):
                                    break
                                time.sleep(0.05)
                            self.assertEqual(running["status"], "RUNNING")
                            self.assertIsNotNone(running["started_at"])
                            self.assertIsNone(running["finished_at"])
                            self.assertEqual(running["success_records"], 0)
                        finally:
                            cursor.execute("UNLOCK TABLES")

                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        last = client.get(f"/api/v1/jobs/{job_ids[-1]}").json()["data"]
                        if last["status"] == "SUCCESS" or worker.poll() is not None:
                            break
                        time.sleep(0.05)
                    self.assertIsNone(worker.poll())
                    for job_id, case, evidence in zip(job_ids, cases, summary):
                        label, _, expected_status, expected_counts, expected_code = case
                        response = client.get(f"/api/v1/jobs/{job_id}")
                        self.assertEqual(response.status_code, 200)
                        result = response.json()["data"]
                        self.assertEqual(result["status"], expected_status)
                        counts = tuple(
                            result[key]
                            for key in (
                                "total_records",
                                "success_records",
                                "failed_records",
                            )
                        )
                        self.assertEqual(counts, expected_counts)
                        self.assertEqual(len(self.records(job_id)), counts[1])
                        self.assertEqual(result["last_error_code"], expected_code)
                        self.assertGreaterEqual(
                            datetime.fromisoformat(result["started_at"]),
                            datetime.fromisoformat(result["created_at"]),
                        )
                        self.assertGreaterEqual(
                            datetime.fromisoformat(result["finished_at"]),
                            datetime.fromisoformat(result["started_at"]),
                        )
                        errors = self.errors(job_id)
                        error_response = client.get(f"/api/v1/jobs/{job_id}/errors")
                        self.assertEqual(error_response.status_code, 200)
                        error_body = error_response.json()
                        self.assertEqual(
                            error_body["meta"],
                            {
                                "page": 1,
                                "page_size": 20,
                                "total": len(errors),
                            },
                        )
                        self.assertEqual(len(error_body["data"]), len(errors))
                        for actual, stored in zip(error_body["data"], errors):
                            for field in (
                                "job_id",
                                "row_number",
                                "field_name",
                                "error_code",
                                "error_message",
                            ):
                                self.assertEqual(actual[field], stored[field])
                            self.assertEqual(
                                actual["raw_row"],
                                json.loads(stored["raw_row"])
                                if stored["raw_row"] is not None
                                else None,
                            )
                            self.assertTrue(actual["created_at"].endswith("Z"))
                        if expected_code is None:
                            self.assertEqual(errors, [])
                        else:
                            self.assertEqual(
                                {e["error_code"] for e in errors}, {expected_code}
                            )
                            self.assertEqual(len(errors), max(1, counts[2]))
                        if label == "database_error":
                            self.assertEqual([e["row_number"] for e in errors], [2, 3])
                            self.assertIn("3819", errors[0]["error_message"])
                        evidence.update(result)
                    self.assertEqual(self.queue.llen(self.key), 0)
                    first_record = self.records(job_ids[0])[0]
                    self.assertEqual(first_record["external_id"], "S0")
                    self.assertEqual(first_record["name"], "商品")
                    self.assertEqual(str(first_record["amount"]), "19.90")
                    worker.terminate()
                    worker.wait(timeout=5)
                    self.assertEqual(worker.returncode, 0)
                    worker_log.seek(0)
                    output = worker_log.read()
                    for job_id in job_ids:
                        self.assertIn(f"job_id={job_id} received", output)
                        self.assertIn(f"job_id={job_id} status=RUNNING", output)
                    for text in (
                        f"job_id={job_ids[0]} stage=csv_validated total_records=501",
                        "batch=1 source_lines=2-251 processed=250/501",
                        "batch=2 source_lines=252-501 processed=500/501",
                        "batch=3 source_lines=502-502 processed=501/501",
                        f"job_id={job_ids[2]} status=FAILED stage=csv_validation",
                        f"job_id={job_ids[3]} stage=batch_write batch=1 write_error=3819",
                        "Stopped",
                    ):
                        self.assertIn(text, output)
            finally:
                for process in reversed(processes):
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                reports = Path("/reports")
                if reports.is_dir():
                    for name, log in (("api", api_log), ("worker", worker_log)):
                        log.seek(0)
                        (reports / f"week03-{name}.log").write_text(log.read())
                    (reports / "week03-worker-scenarios.json").write_text(
                        json.dumps(summary, ensure_ascii=False, indent=2)
                    )

    def test_independent_worker_consumes_real_queue_and_stops(self):
        """独立 Worker 进程消费真实队列，重复/无效消息不影响后续任务，SIGTERM 正常退出。"""
        first = self.create()
        self.queue.rpush(self.key, first, str(uuid4()))
        invalid = self.create(b"wrong,header\n")
        mixed = self.create(
            (",".join(HEADER) + "\nA,n,-1,2026-01-01\nB,n,2,2026-01-01\n").encode()
        )
        second = self.create()
        with tempfile.TemporaryFile(mode="w+") as log:
            worker = subprocess.Popen(
                [sys.executable, "-m", "app.worker"], stdout=log, stderr=log
            )
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if (
                        self.row(second)["status"] == "SUCCESS"
                        and self.queue.llen(self.key) == 0
                    ):
                        break
                    if worker.poll() is not None:
                        break
                    time.sleep(0.05)
                self.assertEqual(self.row(first)["status"], "SUCCESS")
                self.assertEqual(self.row(second)["status"], "SUCCESS")
                self.assertEqual(self.row(invalid)["status"], "FAILED")
                self.assertEqual(
                    self.errors(invalid)[0]["error_code"], "CSV_HEADER_INVALID"
                )
                self.assertEqual(self.row(mixed)["status"], "PARTIAL_SUCCESS")
                self.assertEqual(self.errors(mixed)[0]["error_code"], "AMOUNT_INVALID")
                self.assertEqual(len(self.records(mixed)), 1)
                self.assertEqual(self.queue.llen(self.key), 0)
                self.assertIsNone(worker.poll())
                worker.terminate()
                worker.wait(timeout=5)
                self.assertEqual(worker.returncode, 0)
                log.seek(0)
                output = log.read()
                for text in (
                    f"job_id={first} received",
                    f"job_id={first} status=RUNNING",
                    f"job_id={first} status=SUCCESS",
                    "skipped",
                    "processed=1/1",
                    "Stopped",
                ):
                    self.assertIn(text, output)
            finally:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=5)
