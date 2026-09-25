"""A failing boundary subtest must not appear as passed in the JSON report."""

import io
import unittest

from reporting import EvidenceCase, ReportResult


class ReportingTests(EvidenceCase):
    def setUp(self):
        self.evidence = []

    def test_subtest_failures_are_reported_on_parent_case(self):
        """边界子用例失败或异常时，父用例报告必须标记失败或异常。"""
        for kind, expected in (("assertion", "failed"), ("exception", "error")):

            class BrokenBoundary(EvidenceCase):
                def runTest(self, kind=kind):
                    with self.subTest(boundary=kind):
                        if kind == "assertion":
                            self.assertEqual(1, 2)
                        else:
                            raise ValueError("test error")

            result = unittest.TextTestRunner(
                stream=io.StringIO(), resultclass=ReportResult
            ).run(BrokenBoundary())
            self.assertFalse(result.wasSuccessful())
            self.assertEqual(result.cases[0]["status"], expected)
