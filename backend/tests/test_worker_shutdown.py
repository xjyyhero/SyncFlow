"""Shutdown/restart checks using actual signals, child processes, Redis and MySQL."""

import os
import re
import signal
import tempfile
import time
from threading import Event
from unittest.mock import patch

import test_worker_control
from app import repository, worker
from app.csv_source import read_csv
from app.database import connection
from mysql.connector import Error as MySQLError
from reporting import EvidenceCase


class WorkerShutdownTests(EvidenceCase):
    setUpClass = classmethod(test_worker_control.WorkerControlTests.setUpClass.__func__)
    setUp = test_worker_control.WorkerControlTests.setUp
    create = test_worker_control.WorkerControlTests.create
    create_rows = test_worker_control.WorkerControlTests.create_rows
    csv_rows = test_worker_control.WorkerControlTests.csv_rows
    row = test_worker_control.WorkerControlTests.row
    records = test_worker_control.WorkerControlTests.records
    fifo = test_worker_control.WorkerControlTests.fifo
    wait_status = test_worker_control.WorkerControlTests.wait_status
    start_worker = test_worker_control.WorkerControlTests.start_worker
    assert_children_reaped = (
        test_worker_control.WorkerControlTests.assert_children_reaped
    )

    def wait_child(self, log, job_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            text = os.pread(log.fileno(), 1000000, 0).decode()
            match = re.search(rf"job_id={job_id} process_id=(\d+) started", text)
            if match:
                return int(match[1])
            time.sleep(0.03)
        self.fail("Child did not start")

    def test_first_signal_sets_one_shared_deadline(self):
        """重复停止信号不能重置收尾期限，默认 30 秒。"""
        with (
            patch.object(worker, "stopped", Event()),
            patch.object(worker, "shutdown_deadline", None),
            patch.object(worker, "shutdown_timeout", 30),
            patch.object(worker, "monotonic", return_value=100),
        ):
            worker.stop(signal.SIGTERM, None)
            self.assertTrue(worker.stopped.is_set())
            self.assertEqual(worker.shutdown_deadline, 130)
            with patch.object(worker, "monotonic", return_value=120):
                worker.stop(signal.SIGINT, None)
            self.assertEqual(worker.shutdown_deadline, 130)

    def test_signals_stop_new_work_and_expire_all_inflight_jobs(self):
        """SIGTERM/SIGINT 均停止领取，两个阻塞任务共用 1 秒收尾期，退出后没有运行中任务。"""
        for stop_signal in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=stop_signal):
                self.queue.delete(self.key)
                blocked = [self.create(), self.create()]
                for job_id in blocked:
                    self.fifo(job_id)
                pending = self.create()
                with tempfile.TemporaryFile(mode="w+") as log:
                    with patch.dict(
                        os.environ, {"WORKER_SHUTDOWN_TIMEOUT_SECONDS": "1"}
                    ):
                        process = self.start_worker(log, concurrency=2, timeout=30)
                    for job_id in blocked:
                        self.wait_child(log, job_id)
                    started = time.monotonic()
                    process.send_signal(stop_signal)
                    process.wait(timeout=6)
                    self.assertLess(time.monotonic() - started, 6)
                    self.assertEqual(process.returncode, 0)
                    for job_id in blocked:
                        row = self.row(job_id)
                        self.assertEqual(row["status"], "FAILED")
                        self.assertEqual(
                            row["last_error_code"], "WORKER_SHUTDOWN_TIMEOUT"
                        )
                        self.assertIsNotNone(row["finished_at"])
                    self.assertEqual(self.row(pending)["status"], "PENDING")
                    self.assertIsNone(self.row(pending)["started_at"])
                    self.assertIn(pending, self.queue.lrange(self.key, 0, -1))
                    self.assert_children_reaped(log)

    def test_inflight_task_can_finish_during_grace(self):
        """停止信号后给执行中任务时间完成，保留 SUCCESS，不领取下一任务。"""
        job_id = self.create()
        self.fifo(job_id)
        pending = self.create()
        with tempfile.TemporaryFile(mode="w+") as log:
            with patch.dict(os.environ, {"WORKER_SHUTDOWN_TIMEOUT_SECONDS": "5"}):
                process = self.start_worker(log, concurrency=1, timeout=30)
            self.wait_child(log, job_id)
            process.send_signal(signal.SIGTERM)
            deadline = time.monotonic() + 4
            path = self.row(job_id)["stored_file_path"]
            while True:
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fail("CSV reader did not open during grace")
                    time.sleep(0.03)
            try:
                os.write(
                    fd, b"external_id,name,amount,record_date\nA1,test,1,2026-01-01\n"
                )
            finally:
                os.close(fd)
            process.wait(timeout=6)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(self.row(job_id)["status"], "SUCCESS")
            self.assertEqual(len(self.records(job_id)), 1)
            self.assertEqual(self.row(pending)["status"], "PENDING")
            self.assert_children_reaped(log)

    def test_delivery_at_shutdown_is_returned_and_claim_is_rolled_back(self):
        """阻塞弹出期间收到停止信号会归还消息；领取事务期间停止则回滚，不启动子进程。"""
        job_id = self.create()
        pop = self.queue.blpop
        claim = repository.start_job
        with (
            patch.object(worker, "stopped", Event()),
            patch.object(worker, "shutdown_deadline", None),
            patch.object(worker, "queue_client") as factory,
        ):
            factory.return_value.__enter__.return_value = self.queue

            def stop_after_pop(*args, **kwargs):
                item = pop(*args, **kwargs)
                worker.stop(signal.SIGTERM, None)
                return item

            with (
                patch.object(self.queue, "blpop", side_effect=stop_after_pop),
                patch.object(worker, "supervise_job") as supervise,
            ):
                worker.consume(30)
                supervise.assert_not_called()
            self.assertEqual(self.queue.lrange(self.key, 0, -1), [job_id])
            worker.stopped.clear()
            worker.shutdown_deadline = None

            def stop_after_claim(db, current_id):
                result = claim(db, current_id)
                worker.stop(signal.SIGINT, None)
                return result

            with (
                patch.object(repository, "start_job", side_effect=stop_after_claim),
                patch.object(worker.subprocess, "Popen") as spawn,
            ):
                self.assertFalse(worker.supervise_job(job_id, 30))
                spawn.assert_not_called()
            self.assertEqual(self.row(job_id)["status"], "PENDING")
            self.assertIsNone(self.row(job_id)["started_at"])

    def test_recovery_preserves_progress_skips_live_and_locked_rows(self):
        """超时 RUNNING/CANCELING 恢复为 FAILED；保留数据，不碰新任务/终态/锁定批次，重复扫描幂等。"""
        partial = self.create_rows(self.csv_rows(251))
        parsed = read_csv(self.row(partial)["stored_file_path"])
        with connection() as db:
            repository.start_job(db, partial)
        worker.write_batch(partial, parsed.rows[:250], 0, 251)
        canceled, legacy, fresh, terminal = [self.create() for _ in range(4)]
        with connection() as db, db.cursor() as cursor:
            for job_id, status, started in (
                (partial, "RUNNING", "2020-01-01"),
                (canceled, "CANCELING", "2020-01-01"),
                (legacy, "RUNNING", None),
                (terminal, "SUCCESS", "2020-01-01"),
            ):
                cursor.execute(
                    "UPDATE sync_jobs SET status=%s, started_at=%s, updated_at='2020-01-01' WHERE id=%s",
                    (status, started, job_id),
                )
            repository.start_job(db, fresh)
        with connection() as held, held.cursor() as lock:
            lock.execute("SELECT id FROM sync_jobs WHERE id=%s FOR UPDATE", (partial,))
            lock.fetchone()
            with connection() as db:
                recovered = repository.fail_expired_jobs(db, 30)
            self.assertEqual(set(recovered), {canceled, legacy})
        with connection() as db:
            self.assertEqual(repository.fail_expired_jobs(db, 30), [partial])
        with connection() as db:
            self.assertEqual(repository.fail_expired_jobs(db, 30), [])
            self.assertEqual(
                repository.list_job_errors(db, partial)["meta"]["total"], 1
            )
        self.assertEqual(self.row(fresh)["status"], "RUNNING")
        self.assertEqual(self.row(terminal)["status"], "SUCCESS")
        row = self.row(partial)
        self.assertEqual(row["status"], "FAILED")
        self.assertEqual(row["last_error_code"], "WORKER_EXECUTION_EXPIRED")
        self.assertEqual((row["total_records"], row["success_records"]), (251, 250))
        self.assertIsNone(worker.write_batch(partial, parsed.rows[250:], 250, 251))
        self.assertEqual(len(self.records(partial)), 250)

    def test_parent_crash_stops_child_and_restart_recovers_task(self):
        """SIGKILL 父进程后管道关闭终止孤儿；重启扫描超时任务并继续处理新任务。"""
        job_id = self.create()
        self.fifo(job_id)
        with tempfile.TemporaryFile(mode="w+") as old_log:
            process = self.start_worker(old_log, concurrency=1, timeout=3)
            pid = self.wait_child(old_log, job_id)
            process.kill()
            process.wait(timeout=5)
            deadline = time.monotonic() + 5
            while True:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                if time.monotonic() >= deadline:
                    self.fail("Orphan child remained alive")
                time.sleep(0.03)
        self.assertEqual(self.row(job_id)["status"], "RUNNING")
        # Simulate elapsed timeout without slowing down the whole test suite.
        with connection() as db, db.cursor() as cursor:
            cursor.execute(
                "UPDATE sync_jobs SET started_at='2020-01-01' WHERE id=%s", (job_id,)
            )
        normal = self.create()
        self.queue.rpush(self.key, job_id)
        with tempfile.TemporaryFile(mode="w+") as log:
            restarted = self.start_worker(log, concurrency=1, timeout=3)
            row = self.wait_status(job_id, {"FAILED"})
            self.assertEqual(row["last_error_code"], "WORKER_EXECUTION_EXPIRED")
            self.wait_status(normal, {"SUCCESS"})
            restarted.terminate()
            restarted.wait(timeout=5)
            self.assertEqual(restarted.returncode, 0)
            self.assertEqual(self.records(job_id), [])
            self.assertEqual(len(self.records(normal)), 1)
            self.assert_children_reaped(log)

    def test_failed_final_write_is_reported_and_later_recovered(self):
        """最终状态回写失败时明确记录恢复需求、服务非零退出，数据库恢复后扫描修复。"""
        job_id = self.create()
        self.fifo(job_id)
        with patch.object(worker, "cleanup_failed", Event()):
            with (
                patch.object(repository, "fail_job", side_effect=MySQLError("offline")),
                self.assertLogs("app.worker", level="ERROR") as logs,
                self.assertRaises(MySQLError),
            ):
                worker.supervise_job(job_id, 1)
            self.assertTrue(worker.cleanup_failed.is_set())
            self.assertIn("recovery required", logs.output[-1])
            with (
                patch.object(worker, "consume"),
                self.assertRaises(SystemExit) as exit_code,
            ):
                worker.run()
            self.assertEqual(exit_code.exception.code, 1)
        self.assertEqual(self.row(job_id)["status"], "RUNNING")
        with connection() as db:
            self.assertEqual(repository.fail_expired_jobs(db, 1), [job_id])
        self.assertEqual(self.row(job_id)["status"], "FAILED")

    def test_lost_claim_commit_acknowledgement_requires_recovery(self):
        """领取提交已成功但响应丢失时，停止不能假报全清理成功；恢复扫描能修复。"""
        job_id = self.create()
        original = repository.start_job

        def lost_ack(db, current_id):
            original(db, current_id)
            db.commit()
            worker.stop(signal.SIGTERM, None)
            raise MySQLError("commit acknowledgement lost")

        with (
            patch.object(worker, "stopped", Event()),
            patch.object(worker, "cleanup_failed", Event()),
            patch.object(worker, "shutdown_deadline", None),
            patch.object(worker, "queue_client") as factory,
            patch.object(repository, "start_job", side_effect=lost_ack),
            patch.object(worker.subprocess, "Popen") as spawn,
        ):
            factory.return_value.__enter__.return_value = self.queue
            worker.consume(30)
            spawn.assert_not_called()
            self.assertTrue(worker.cleanup_failed.is_set())
            self.assertEqual(self.row(job_id)["status"], "RUNNING")
        with connection() as db, db.cursor() as cursor:
            cursor.execute(
                "UPDATE sync_jobs SET started_at='2020-01-01' WHERE id=%s", (job_id,)
            )
            self.assertEqual(repository.fail_expired_jobs(db, 30), [job_id])
        self.assertEqual(self.row(job_id)["status"], "FAILED")
