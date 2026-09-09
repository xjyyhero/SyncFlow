"""Week 1 API skeleton. Business endpoints will be added in later tasks."""

from fastapi import FastAPI

app = FastAPI(title="SyncFlow", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Report process liveness without checking database or queue availability."""
    return {"status": "ok"}


@app.get("/api/v1/info")
def project_info() -> dict:
    return {
        "data": {"name": "SyncFlow", "stage": "Week 1 项目骨架"},
        "meta": {},
    }
