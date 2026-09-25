"""Standalone CSV contract checks; also included in the isolated DB report."""

import csv
import io
import os
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app.csv_source import HEADER, CSVValidationError, read_csv
from reporting import EvidenceCase


class CSVSourceTests(EvidenceCase):
    def setUp(self):
        self.evidence = []
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "input.csv"
        env = patch.dict(
            os.environ,
            {"MAX_UPLOAD_FILE_SIZE_MB": "10", "MAX_RECORDS_PER_JOB": "10000"},
        )
        env.start()
        self.addCleanup(env.stop)

    def write_rows(self, rows):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(HEADER)
        writer.writerows(rows)
        self.path.write_text(stream.getvalue(), encoding="utf-8", newline="")

    def parse_field(self, field, value):
        row = ["S001", "商品A", "19.90", "2026-01-01"]
        row[HEADER.index(field)] = value
        self.write_rows([row])
        return read_csv(self.path)

    def file_error(self, content, code):
        self.path.write_bytes(content)
        with self.assertRaises(CSVValidationError) as caught:
            read_csv(self.path)
        self.assertEqual(caught.exception.code, code)

    def test_utf8_bom_normalization_and_native_types(self):
        """UTF-8/BOM、CRLF、中文与四字段 trim；金额保留两位，日期为 date。"""
        content = ",".join(HEADER) + '\r\n s_001-z ," 商品,A ", 19.9 , 2026-01-02 \r\n'
        for encoding in ("utf-8", "utf-8-sig"):
            with self.subTest(encoding=encoding):
                self.path.write_bytes(content.encode(encoding))
                result = read_csv(self.path)
                self.assertEqual(result.total_records, 1)
                self.assertEqual(result.errors, [])
                self.assertEqual(
                    result.records,
                    [
                        {
                            "external_id": "S_001-Z",
                            "name": "商品,A",
                            "amount": Decimal("19.90"),
                            "record_date": date(2026, 1, 2),
                        }
                    ],
                )
                self.assertEqual(str(result.records[0]["amount"]), "19.90")

    def test_headers_are_exact_and_first(self):
        """缺失、重复、错序、多列、少列、空白前缀和分号分隔的表头均拒绝。"""
        for header in (
            "",
            "S001,A,19,2026-01-01",
            "external_id,name,amount,amount",
            "name,external_id,amount,record_date",
            ",".join(HEADER) + ",extra",
            "external_id,name,amount",
            " external_id,name,amount,record_date",
            ";".join(HEADER),
        ):
            with self.subTest(header=header):
                self.file_error(
                    (header + "\nS001,A,19,2026-01-01\n").encode(),
                    "CSV_HEADER_INVALID",
                )

    def test_empty_and_invalid_encoding(self):
        """空文件、仅表头及空行被拒绝；UTF-16/GBK/尾部损坏字节不能被替换解码。"""
        header = (",".join(HEADER) + "\n").encode()
        for content in (b"", b"\xef\xbb\xbf", header, header + b"\n \t\n"):
            self.file_error(content, "FILE_EMPTY")
        for content in (
            "商品".encode("gbk"),
            "中文".encode("utf-16"),
            header + b"S1,A,19,2026-01-01\n" + b"\n" * 65536 + b"\xff",
        ):
            self.file_error(content, "FILE_ENCODING_INVALID")

    def test_blank_lines_and_physical_line_numbers(self):
        """忽略空白物理行，保留引号逗号/换行；错误行号取记录起始物理行。"""
        self.path.write_text(
            ",".join(HEADER)
            + '\n\n \t\nA,"商品,\n名称",19,2026-01-01\n'
            + ',,,\nB,"多行\n错误",-1,2026-01-01\nC,A,19,2026-01-01\n'
            + '""\n',
            encoding="utf-8",
        )
        result = read_csv(self.path)
        self.assertEqual(result.total_records, 5)
        self.assertEqual(len(result.records), 2)
        self.assertEqual(result.records[0]["name"], "商品,\n名称")
        self.assertEqual([e["row_number"] for e in result.errors], [6, 7, 10])
        self.assertEqual(
            [e["error_code"] for e in result.errors],
            ["FIELD_REQUIRED", "AMOUNT_INVALID", "ROW_COLUMN_COUNT_MISMATCH"],
        )
        self.assertEqual(
            result.errors[1]["raw_row"], ["B", "多行\n错误", "-1", "2026-01-01"]
        )

    def test_required_and_column_count(self):
        """各字段 trim 后为空均报必填错误，列数错误保留原始行并继续。"""
        for name in HEADER:
            for empty in ("", " \t\u3000 "):
                with self.subTest(field=name, empty=empty):
                    result = self.parse_field(name, empty)
                    self.assertEqual(result.errors[0]["error_code"], "FIELD_REQUIRED")
                    self.assertEqual(result.errors[0]["field_name"], name)
        self.write_rows([["A", "B"], ["A", "B", "1", "2026-01-01", "extra"]])
        result = read_csv(self.path)
        self.assertEqual(result.total_records, 2)
        self.assertEqual(
            [e["error_code"] for e in result.errors], ["ROW_COLUMN_COUNT_MISMATCH"] * 2
        )
        self.assertIsNone(result.errors[0]["field_name"])

    def test_external_id_format_and_boundaries(self):
        """标识长度 1/64 合法；65、非 ASCII 字母数字及空白/标点不合法。"""
        for value in ("a", "a" * 64, "aZ09_-"):
            self.assertEqual(
                self.parse_field("external_id", value).records[0]["external_id"],
                value.upper(),
            )
        for value in ("a" * 65, "中文", "é", "Ａ", "ſ", "a b", "a.b", "a/b"):
            with self.subTest(value=value):
                self.assertEqual(
                    self.parse_field("external_id", value).errors[0]["error_code"],
                    "EXTERNAL_ID_INVALID",
                )

    def test_duplicate_ids_first_valid_wins_and_scope_is_per_file(self):
        """大小写/空白规范化后去重；首个合法行获胜，下一次读取重置去重集合。"""
        self.write_rows(
            [
                [" s1 ", "坏金额", "-1", "2026-01-01"],
                ["s1", "合法", "1", "2026-01-01"],
                [" S1 ", "重复", "2", "2026-01-01"],
                ["s2", "后续合法", "3", "2026-01-01"],
            ]
        )
        for _ in range(2):
            result = read_csv(self.path)
            self.assertEqual([r["external_id"] for r in result.records], ["S1", "S2"])
            self.assertEqual(
                [e["error_code"] for e in result.errors],
                ["AMOUNT_INVALID", "EXTERNAL_ID_DUPLICATE"],
            )

    def test_name_length_in_characters_and_large_field(self):
        """中文名按字符计数，128 合法；129 和超过 csv 默认字段上限均为行错误。"""
        for value in ("名", "名" * 128):
            self.assertEqual(self.parse_field("name", value).records[0]["name"], value)
        for value in ("名" * 129, "x" * 140000):
            self.assertEqual(
                self.parse_field("name", value).errors[0]["error_code"], "NAME_TOO_LONG"
            )

    def test_decimal_exactness_and_range(self):
        """金额精确到两位，最大值不溢出；负数、指数、超精度/范围等均拒绝。"""
        for value, expected in (
            ("0", "0.00"),
            ("19", "19.00"),
            ("19.9", "19.90"),
            ("19.90", "19.90"),
            ("0.01", "0.01"),
            ("9999999999.99", "9999999999.99"),
            ("00019.9", "19.90"),
        ):
            self.assertEqual(
                str(self.parse_field("amount", value).records[0]["amount"]), expected
            )
        for value in (
            "-0",
            "-1",
            "+1",
            "1.001",
            "1.000",
            "1e2",
            "1E2",
            "NaN",
            "Infinity",
            "abc",
            ".1",
            "1.",
            "1,000",
            "１００",
            "1 9",
            "10000000000",
            "9999999999.999",
            "9" * 1000,
        ):
            with self.subTest(value=value[:40]):
                self.assertEqual(
                    self.parse_field("amount", value).errors[0]["error_code"],
                    "AMOUNT_INVALID",
                )

    def test_date_format_calendar_and_mysql_range(self):
        """严格日期格式、闰年及 MySQL DATE 上下界，拒绝隐式日期格式转换。"""
        for value in ("2024-02-29", "1000-01-01", "9999-12-31"):
            self.assertEqual(
                self.parse_field("record_date", value).records[0]["record_date"],
                date.fromisoformat(value),
            )
        for value in (
            "2026/01/01",
            "01-01-2026",
            "2026-1-01",
            "2026-01-1",
            "20260101",
            "2026-01-01T00:00:00",
            "2026-01-01 00:00:00",
            "2026-02-29",
            "2026-04-31",
            "2026-13-01",
            "2026-00-01",
            "2026-01-00",
            "0000-00-00",
            "0999-12-31",
            "10000-01-01",
            "２０２６-01-01",
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    self.parse_field("record_date", value).errors[0]["error_code"],
                    "DATE_INVALID",
                )

    def test_default_and_configured_row_limits_include_invalid_rows(self):
        """默认接受 10000 行、拒绝 10001；自定义上限包含非法行，不含空行。"""
        self.write_rows([[f"S{i}", "A", "1", "2026-01-01"] for i in range(10000)])
        self.assertEqual(read_csv(self.path).total_records, 10000)
        with self.path.open("a") as out:
            out.write("EXTRA,A,1,2026-01-01\n")
        with self.assertRaises(CSVValidationError) as caught:
            read_csv(self.path)
        self.assertEqual(caught.exception.code, "FILE_TOO_MANY_ROWS")
        with patch.dict(os.environ, {"MAX_RECORDS_PER_JOB": "2"}):
            self.write_rows([[], ["S1", "A", "1", "2026-01-01"], [",", "", "", ""]])
            self.assertEqual(read_csv(self.path).total_records, 2)
            with self.path.open("a") as out:
                out.write(",,,\n")
            with self.assertRaises(CSVValidationError) as caught:
                read_csv(self.path)
            self.assertEqual(caught.exception.code, "FILE_TOO_MANY_ROWS")

    def test_default_and_configured_byte_limits(self):
        """默认 10 MiB 和配置 1 MiB 的等于/超限边界，按字节计算。"""
        for mb in (10, 1):
            with patch.dict(os.environ, {"MAX_UPLOAD_FILE_SIZE_MB": str(mb)}):
                prefix = (",".join(HEADER) + "\nS1,商品,1,2026-01-01\n").encode()
                content = prefix + b" " * (mb * 1024 * 1024 - len(prefix))
                self.path.write_bytes(content)
                self.assertEqual(read_csv(self.path).total_records, 1)
                self.file_error(content + b" ", "FILE_TOO_LARGE")

    def test_extension_read_failures_and_invalid_configuration(self):
        """不存在/无权限的文件被拒绝；CSV 大写后缀兼容既有上传规则。"""
        with self.assertRaises(CSVValidationError) as caught:
            read_csv(self.path)
        self.assertEqual(caught.exception.code, "FILE_UNREADABLE")
        with patch("app.csv_source.Path.open", side_effect=PermissionError):
            with self.assertRaises(CSVValidationError) as caught:
                read_csv(self.path)
            self.assertEqual(caught.exception.code, "FILE_UNREADABLE")
        self.write_rows([["S1", "A", "1", "2026-01-01"]])
        upper = self.path.with_suffix(".CSV")
        upper.write_bytes(self.path.read_bytes())
        self.assertEqual(read_csv(upper).total_records, 1)
        with self.assertRaises(CSVValidationError) as caught:
            read_csv(self.path.with_suffix(".txt"))
        self.assertEqual(caught.exception.code, "INVALID_FILE_EXTENSION")
        for key in ("MAX_UPLOAD_FILE_SIZE_MB", "MAX_RECORDS_PER_JOB"):
            for value in ("0", "-1", "bad"):
                with (
                    patch.dict(os.environ, {key: value}),
                    self.assertRaises(ValueError if value == "bad" else RuntimeError),
                ):
                    read_csv(self.path)

    def test_malformed_quoting_is_file_error(self):
        """无法可靠确定记录边界的引号损坏不返回部分成功数据。"""
        for tail in ('S2,"unclosed,1,2026-01-01', 'S2,"name"x,1,2026-01-01'):
            self.file_error(
                (",".join(HEADER) + "\nS1,A,1,2026-01-01\n" + tail).encode(),
                "CSV_MALFORMED",
            )
