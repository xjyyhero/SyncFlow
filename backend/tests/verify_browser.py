"""Read-only verification of browser-created sample jobs in isolated MySQL."""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.database import connection


def verify(report_dir, samples_dir):
    if (os.environ.get("MYSQL_HOST"), os.environ.get("MYSQL_DATABASE")) not in {
        ("mysql-test", "syncflow_test"),
        ("mysql", "syncflow_release_test"),
    }:
        raise RuntimeError("Browser verification requires isolated MySQL")
    expected = {
        case["file"]: case
        for case in json.loads((samples_dir / "week-03-expected.json").read_text())
    }
    scenarios = json.loads((report_dir / "browser-scenarios.json").read_text())
    assert len(scenarios) == len(expected)
    assert {case["sample"] for case in scenarios} == set(expected)
    results = []
    with connection() as db, db.cursor(dictionary=True) as cursor:
        for scenario in scenarios:
            case = expected[scenario["sample"]]
            cursor.execute("SELECT * FROM sync_jobs WHERE id=%s", (scenario["id"],))
            job = cursor.fetchone()
            assert job is not None
            for key in ("status", "total_records", "success_records", "failed_records"):
                assert job[key] == case[key] == scenario["detail"][key], (
                    key,
                    job,
                    case,
                )
            assert job["created_at"] <= job["started_at"] <= job["finished_at"]
            cursor.execute(
                "SELECT external_id, name, amount, record_date FROM sync_records WHERE job_id=%s ORDER BY id",
                (scenario["id"],),
            )
            records = [
                [
                    row["external_id"],
                    row["name"],
                    str(row["amount"]),
                    row["record_date"].isoformat(),
                ]
                for row in cursor.fetchall()
            ]
            assert records == case["records"], (records, case["records"])
            cursor.execute(
                "SELECT job_id, `row_number`, field_name, error_code, error_message, raw_row, created_at FROM sync_errors WHERE job_id=%s ORDER BY `row_number`, id",
                (scenario["id"],),
            )
            errors = cursor.fetchall()
            assert [row["row_number"] for row in errors] == case["error_rows"]
            assert len(errors) == len(scenario["errors"])
            for stored, api in zip(errors, scenario["errors"]):
                assert stored["error_code"] == case["error_code"]
                stored["raw_row"] = (
                    json.loads(stored["raw_row"])
                    if stored["raw_row"] is not None
                    else None
                )
                stored["created_at"] = (
                    stored["created_at"]
                    .replace(tzinfo=UTC)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
                assert stored == api, (stored, api)
            results.append(
                {
                    "sample": case["file"],
                    "job_id": scenario["id"],
                    "status": job["status"],
                    "counts": [
                        job[key]
                        for key in (
                            "total_records",
                            "success_records",
                            "failed_records",
                        )
                    ],
                    "records": records,
                    "errors": errors,
                    "passed": True,
                }
            )
    (report_dir / "browser-database.json").write_text(
        json.dumps(
            {
                "passed": True,
                "verified_at": datetime.now(UTC).isoformat(),
                "cases": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(
        f"PASS: {len(results)} browser sample jobs match expected values, HTTP and MySQL"
    )


if __name__ == "__main__":
    verify(Path(sys.argv[1]), Path(sys.argv[2]))
