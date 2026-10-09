"""Week 4 state matrix against real MySQL and the shared HTTP error handler."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import test_database
from app import repository as repo
from app.api_contract import APIError, install_error_handlers
from app.database import connection, initialize
from fastapi import FastAPI
from fastapi.testclient import TestClient
from reporting import EvidenceCase
from test_database import job


class JobStateTests(EvidenceCase):
    setUpClass = classmethod(test_database.DatabaseTests.setUpClass.__func__)
    setUp = test_database.DatabaseTests.setUp

    def test_all_state_pairs_and_http_conflicts(self):
        """全部 64 个状态组合：合法转换更新原因/时间；非法转换 HTTP 409、日志且不改数据。"""
        allowed = {
            "PENDING": {"RUNNING", "CANCELED"},
            "RUNNING": {
                "SUCCESS",
                "PARTIAL_SUCCESS",
                "RETRYING",
                "FAILED",
                "CANCELING",
            },
            "RETRYING": {"PENDING", "FAILED", "CANCELED"},
            "CANCELING": {"CANCELED", "FAILED"},
            "SUCCESS": set(),
            "PARTIAL_SUCCESS": set(),
            "FAILED": set(),
            "CANCELED": set(),
        }
        fixture = FastAPI()
        install_error_handlers(fixture)

        @fixture.post("/transition/{target}")
        def transition(target: str):
            with connection() as db:
                repo.transition_job(
                    db,
                    "a",
                    target,
                    error_code="TEST_REASON",
                    error_message="状态转换验证",
                )
            return {"ok": True}

        with connection() as db:
            job(db)
        with TestClient(fixture) as client:
            for source, targets in allowed.items():
                for target in (*allowed, "INVALID"):
                    with self.subTest(source=source, target=target):
                        with connection() as db, db.cursor() as cursor:
                            cursor.execute(
                                """UPDATE sync_jobs SET status=%s,
                                   updated_at='2020-01-01', started_at=NULL,
                                   finished_at=NULL, last_error_code=NULL,
                                   last_error_message=NULL WHERE id='a'""",
                                (source,),
                            )
                            before = repo.get_job(db, "a")
                        if target in targets:
                            response = client.post(f"/transition/{target}")
                            self.assertEqual(response.status_code, 200)
                            with connection() as db:
                                after = repo.get_job(db, "a")
                            self.assertEqual(after["status"], target)
                            self.assertEqual(after["last_error_code"], "TEST_REASON")
                            self.assertEqual(
                                after["last_error_message"], "状态转换验证"
                            )
                            self.assertGreater(
                                after["updated_at"], before["updated_at"]
                            )
                            self.assertEqual(
                                after["finished_at"] is not None, not allowed[target]
                            )
                            self.assertEqual(
                                after["started_at"] is not None, target == "RUNNING"
                            )
                        else:
                            with self.assertLogs(
                                "app.repository", level="WARNING"
                            ) as logs:
                                response = client.post(f"/transition/{target}")
                            self.assertEqual(response.status_code, 409)
                            self.assertEqual(
                                response.json()["error"]["code"],
                                "INVALID_JOB_TRANSITION",
                            )
                            self.assertIn(f"{source}->{target}", logs.output[0])
                            with connection() as db:
                                self.assertEqual(repo.get_job(db, "a"), before)

    def test_concurrent_claims_only_one_wins(self):
        """两个真实数据库连接同时认领同一任务，仅一个成功。"""
        with connection() as db:
            job(db)
        barrier = Barrier(2)

        def claim():
            with connection() as db:
                barrier.wait(timeout=5)
                return repo.start_job(db, "a")

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = [pool.submit(claim) for _ in range(2)]
            self.assertEqual(
                sorted(f.result(timeout=10) for f in claims), [False, True]
            )

    def test_week3_migration_and_new_status_queries(self):
        """旧五状态约束无损升级；三个新增状态可查询/筛选，重复初始化安全。"""
        with connection() as db, db.cursor() as cursor:
            job(db)
            cursor.execute(
                """ALTER TABLE sync_jobs DROP CHECK ck_sync_jobs_status,
                   ADD CONSTRAINT ck_sync_jobs_status CHECK
                   (status IN ('PENDING','RUNNING','SUCCESS','PARTIAL_SUCCESS','FAILED'))"""
            )
        initialize()
        initialize()
        from app.main import app

        with TestClient(app) as client:
            for status in ("RETRYING", "CANCELING", "CANCELED"):
                with connection() as db, db.cursor() as cursor:
                    cursor.execute(
                        "UPDATE sync_jobs SET status=%s WHERE id='a'", (status,)
                    )
                detail = client.get("/api/v1/jobs/a")
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(detail.json()["data"]["status"], status)
                listing = client.get("/api/v1/jobs", params={"status": status})
                self.assertEqual(listing.status_code, 200)
                self.assertEqual(listing.json()["meta"]["total"], 1)
                self.assertEqual(listing.json()["data"][0]["name"], "测试导入")

    def test_dispatch_failure_does_not_override_claimed_or_terminal_job(self):
        """投递确认丢失但任务已被领取/完成时，补偿不覆盖执行结果。"""
        with connection() as db:
            job(db)
            repo.start_job(db, "a")
            for finish in (False, True):
                if finish:
                    repo.complete_job(db, "a")
                before = repo.get_job(db, "a")
                self.assertFalse(repo.cancel_dispatch(db, "a"))
                self.assertEqual(repo.get_job(db, "a"), before)
            self.assertEqual(repo.list_job_errors(db, "a")["meta"]["total"], 0)
            with self.assertRaises(APIError) as error:
                repo.transition_job(db, "missing", "RUNNING", error_message="认领")
            self.assertEqual(error.exception.status, 404)
