"""Capture actual unittest assertions without changing their pass/fail behavior."""

import functools
import json
import time
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


class EvidenceCase(unittest.TestCase):
    @contextmanager
    def assertRaises(self, expected_exception):
        with super().assertRaises(expected_exception) as caught:
            yield caught
        self.evidence.append(
            {
                "assertion": "assertRaises",
                "actual": type(caught.exception).__name__,
                "expected": expected_exception.__name__,
                "passed": True,
            }
        )


def observed_assertion(name):
    original = getattr(unittest.TestCase, name)

    @functools.wraps(original)
    def observed(self, actual, *args, **kwargs):
        expected = (
            args[0]
            if args
            else {
                "assertTrue": True,
                "assertFalse": False,
                "assertIsNone": None,
                "assertIsNotNone": "非 NULL",
            }.get(name)
        )
        entry = {
            "assertion": name,
            "actual": actual,
            "expected": expected,
            "passed": False,
        }
        self.evidence.append(entry)
        original(self, actual, *args, **kwargs)
        entry["passed"] = True

    return observed


for assertion in (
    "assertEqual",
    "assertTrue",
    "assertFalse",
    "assertIsNone",
    "assertIsNotNone",
    "assertGreaterEqual",
    "assertIn",
    "assertNotIn",
):
    setattr(EvidenceCase, assertion, observed_assertion(assertion))


class ReportResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []

    def startTest(self, test):
        test.evidence = []
        test.report_started = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        status, detail = "passed", ""
        for label, entries in (
            ("failed", self.failures),
            ("error", self.errors),
            ("skipped", self.skipped),
        ):
            for target, text in entries:
                if target is test:
                    status, detail = label, text
        self.cases.append(
            {
                "id": test.id(),
                "description": test.shortDescription() or test.id(),
                "status": status,
                "seconds": round(time.perf_counter() - test.report_started, 4),
                "assertions": test.evidence,
                "detail": detail,
            }
        )
        super().stopTest(test)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent))
    result = unittest.TextTestRunner(verbosity=2, resultclass=ReportResult).run(suite)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "database": "syncflow_test",
        "isolated": True,
        "successful": result.wasSuccessful(),
        "tests_run": result.testsRun,
        "cases": result.cases,
        # Includes class setup/import failures that do not call startTest/stopTest.
        "errors": [
            {"id": str(test), "detail": detail}
            for test, detail in result.errors + result.failures
        ],
    }
    destination = Path("/reports/test-results.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
