"""Start/stop skeleton only; this worker does not consume jobs yet."""

import logging
import signal
from threading import Event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s worker %(message)s",
)
stopped = Event()
logger = logging.getLogger(__name__)


def stop(_signum: int, _frame: object) -> None:
    stopped.set()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info("Skeleton started; CSV processing is not implemented yet")
    stopped.wait()
    logger.info("Stopped")
