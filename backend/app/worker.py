"""Week 2 worker: consume a job, verify file access, and simulate success."""

import logging
import os
import signal
from pathlib import Path
from threading import Event

from mysql.connector import Error as MySQLError
from redis.exceptions import RedisError

from app import repository
from app.database import connection
from app.jobs import queue_client, queue_name

stopped = Event()
logger = logging.getLogger(__name__)


def stop(_signum: int, _frame: object) -> None:
    stopped.set()


def process_job(job_id: str) -> bool:
    with connection() as db:
        if not repository.start_job(db, job_id):
            logger.info("job_id=%s skipped (missing or not PENDING)", job_id)
            return False
        job = repository.get_job(db, job_id)
    logger.info("job_id=%s status=RUNNING", job_id)
    try:
        root = Path(os.environ.get("UPLOAD_DIR", "var/uploads")).resolve()
        path = Path(job["stored_file_path"]).resolve()
        if not path.is_relative_to(root):
            raise OSError("File outside upload directory")
        # Prove that the independent worker can read the shared upload volume.
        # CSV parsing and record writes are intentionally deferred to Week 3.
        with path.open("rb") as source:
            source.read(1)
        logger.info("job_id=%s upload readable; simulating completion", job_id)
        with connection() as db:
            completed = repository.complete_job(db, job_id)
        logger.info(
            "job_id=%s %s",
            job_id,
            "status=SUCCESS" if completed else "completion skipped (state changed)",
        )
        return completed
    except (OSError, MySQLError):
        with connection() as db:
            repository.fail_job(
                db,
                job_id,
                error_code="WORKER_PROCESSING_FAILED",
                error_message="后台处理失败，请检查上传文件或数据库状态",
            )
        logger.error("job_id=%s status=FAILED", job_id)
        return False


def run() -> None:
    logger.info("Worker started; queue=%s", queue_name())
    with queue_client() as queue:
        while not stopped.is_set():
            job_id = None
            try:
                # ponytail: BLPOP has no crash recovery; use an acknowledged queue
                # when automatic recovery of interrupted jobs is required.
                item = queue.blpop(queue_name(), timeout=1)
                if item is not None:
                    job_id = item[1]
                    logger.info("job_id=%s received", job_id)
                    process_job(job_id)
            except (RedisError, MySQLError, OSError):
                logger.error("job_id=%s worker operation failed", job_id or "none")
                # Bound retry frequency; signal handling interrupts this wait.
                stopped.wait(1)
    logger.info("Stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s worker %(message)s"
    )
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    run()
