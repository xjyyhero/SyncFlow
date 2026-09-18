"""Create one labelled development task via Web proxy and save its terminal state."""

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    origin = "http://127.0.0.1:5173"
    boundary = uuid4().hex
    name = "Week 2 端到端验收 " + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="name"\r\n\r\n'
        f'{name}\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        'filename="acceptance.csv"\r\nContent-Type: text/csv\r\n\r\n'
        "external_id,name,amount,record_date\nacceptance,Demo,12.50,2026-09-18\n"
        f"\r\n--{boundary}--\r\n"
    ).encode()
    request = Request(
        origin + "/api/v1/jobs",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urlopen(request, timeout=10) as response:
        assert response.status == 201
        created = json.load(response)
    job_id = created["data"]["id"]
    history = []
    deadline = time.monotonic() + 20
    while True:
        with urlopen(origin + "/api/v1/jobs/" + job_id, timeout=10) as response:
            detail = json.load(response)
        history.append(detail["data"]["status"])
        if history[-1] in {"SUCCESS", "FAILED"} or time.monotonic() >= deadline:
            break
        time.sleep(0.1)
    result = {"created": created, "detail": detail, "observed_states": history}
    output = Path("output/week-02-acceptance")
    output.mkdir(parents=True, exist_ok=True)
    (output / "e2e.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert detail["data"]["status"] == "SUCCESS", result
    job = detail["data"]
    assert job["created_at"] <= job["started_at"] <= job["finished_at"]
    assert "stored_file_path" not in job
    print(
        f"PASS: Web proxy -> POST 201 -> Redis -> Worker -> GET SUCCESS; job_id={job_id}"
    )
    print("验收任务与上传文件保留在开发环境，供人工审查。")


if __name__ == "__main__":
    main()
