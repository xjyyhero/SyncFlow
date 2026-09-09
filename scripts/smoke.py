"""Run after scripts/start.sh: verify Web, API, proxy and Worker shutdown."""

import json
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

with urlopen("http://127.0.0.1:8000/healthz", timeout=10) as response:
    assert response.status == 200
    assert json.load(response) == {"status": "ok"}

for origin in ("http://127.0.0.1:8000", "http://127.0.0.1:5173"):
    with urlopen(f"{origin}/api/v1/info", timeout=10) as response:
        assert response.status == 200
        assert json.load(response)["data"]["name"] == "SyncFlow"

with urlopen("http://127.0.0.1:5173", timeout=10) as response:
    assert response.status == 200
    assert '<div id="root"></div>' in response.read().decode()

worker = subprocess.Popen(
    [sys.executable, "-m", "app.worker"],
    cwd=Path(__file__).resolve().parents[1] / "backend",
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)
try:
    # communicate's timeout also bounds startup, so a broken worker cannot hang this check.
    try:
        worker.communicate(timeout=1)
        raise AssertionError("Worker exited before receiving a stop signal")
    except subprocess.TimeoutExpired:
        worker.terminate()
    _, logs = worker.communicate(timeout=5)
    assert worker.returncode == 0, logs
    assert "Skeleton started" in logs and "Stopped" in logs, logs
finally:
    if worker.poll() is None:
        worker.kill()
        worker.communicate()

print("PASS: healthz, API, Web, Web-to-API proxy, Worker startup and graceful shutdown")
