"""CSV input contract. Returns typed records/errors; does not write or finish jobs."""

import csv
import io
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

HEADER = ["external_id", "name", "amount", "record_date"]
MAX_AMOUNT = Decimal("9999999999.99")
# The byte limit below bounds input; do not let csv's default 128 KiB field
# ceiling turn an oversized name into a file-level syntax error.
csv.field_size_limit(sys.maxsize)


class CSVValidationError(ValueError):
    def __init__(self, code: str, message: str, field_name: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field_name = field_name


@dataclass
class CSVData:
    rows: list[dict] = field(default_factory=list)

    @property
    def records(self) -> list[dict]:
        return [row["record"] for row in self.rows if "record" in row]

    @property
    def errors(self) -> list[dict]:
        return [row["error"] for row in self.rows if "error" in row]

    @property
    def total_records(self) -> int:
        return len(self.rows)


def normalize_record(row: list[str], seen: set[str]) -> dict:
    """First fully valid record wins; invalid rows do not reserve an ID."""
    if len(row) != len(HEADER):
        raise CSVValidationError("ROW_COLUMN_COUNT_MISMATCH", "每行必须包含四列")
    values = dict(zip(HEADER, (value.strip() for value in row)))
    for name, value in values.items():
        if not value:
            raise CSVValidationError("FIELD_REQUIRED", f"{name} 不能为空", name)

    external_id = values["external_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", external_id):
        raise CSVValidationError(
            "EXTERNAL_ID_INVALID",
            "external_id 必须为 1–64 位字母、数字、下划线或短横线",
            "external_id",
        )
    external_id = external_id.upper()
    if external_id in seen:
        raise CSVValidationError(
            "EXTERNAL_ID_DUPLICATE", "external_id 在同一任务内重复", "external_id"
        )
    if len(values["name"]) > 128:
        raise CSVValidationError("NAME_TOO_LONG", "name 不能超过 128 字符", "name")

    amount_text = values["amount"]
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", amount_text):
        raise CSVValidationError(
            "AMOUNT_INVALID", "amount 必须为非负普通十进制数，最多两位小数", "amount"
        )
    amount = Decimal(amount_text)
    if amount > MAX_AMOUNT:
        raise CSVValidationError(
            "AMOUNT_INVALID", "amount 不能超过 9999999999.99", "amount"
        )
    amount = amount.quantize(Decimal("0.01"))

    date_text = values["record_date"]
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date_text):
            raise ValueError
        record_date = date.fromisoformat(date_text)
        if record_date.year < 1000:
            raise ValueError
    except ValueError:
        raise CSVValidationError(
            "DATE_INVALID",
            "record_date 必须为 YYYY-MM-DD 格式的有效日期，范围 1000-01-01 至 9999-12-31",
            "record_date",
        ) from None

    seen.add(external_id)
    return {
        "external_id": external_id,
        "name": values["name"],
        "amount": amount,
        "record_date": record_date,
    }


def read_csv(path: str | Path) -> CSVData:
    """Read a trusted local path; caller enforces the upload directory boundary.

    Nonblank CSV records count toward the limit, even if invalid. Row numbers
    are starting physical lines (header is line 1), including skipped blanks.
    File errors raise without returning partial data. Row errors collect one
    error per row and allow later rows to be validated.
    """
    path = Path(path)
    if path.suffix.lower() != ".csv":
        raise CSVValidationError("INVALID_FILE_EXTENSION", "仅支持 CSV 文件")
    byte_limit = int(os.environ.get("MAX_UPLOAD_FILE_SIZE_MB", "10")) * 1024 * 1024
    record_limit = int(os.environ.get("MAX_RECORDS_PER_JOB", "10000"))
    if byte_limit <= 0 or record_limit <= 0:
        raise RuntimeError("CSV limits must be positive integers")
    try:
        # ponytail: bounded in-memory preflight (default 10 MiB/10,000 rows).
        # Switch to streaming preflight when larger imports are required.
        with path.open("rb") as source:
            content = source.read(byte_limit + 1)
    except OSError:
        raise CSVValidationError("FILE_UNREADABLE", "文件不存在或无法读取") from None
    if len(content) > byte_limit:
        raise CSVValidationError("FILE_TOO_LARGE", "文件超过大小限制")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CSVValidationError(
            "FILE_ENCODING_INVALID", "文件必须使用 UTF-8 编码（允许 BOM）"
        ) from None

    stream = io.StringIO(text, newline="")
    reader = csv.reader(stream, delimiter=",", strict=True)
    result = CSVData()
    seen: set[str] = set()
    try:
        header = next(reader, None)
        if header is None:
            raise CSVValidationError("FILE_EMPTY", "文件为空或只有表头")
        if header != HEADER:
            raise CSVValidationError(
                "CSV_HEADER_INVALID", "表头必须为 external_id,name,amount,record_date"
            )
        while True:
            row_number = reader.line_num + 1
            start = stream.tell()
            row = next(reader, None)
            if row is None:
                break
            # A delimiter-only row is data with missing fields, not a blank line.
            if not row or (len(row) == 1 and not text[start : stream.tell()].strip()):
                continue
            if result.total_records >= record_limit:
                raise CSVValidationError("FILE_TOO_MANY_ROWS", "数据行数超过配置上限")
            entry = {"row_number": row_number, "raw_row": row}
            try:
                entry["record"] = normalize_record(row, seen)
            except CSVValidationError as error:
                entry["error"] = {
                    **entry,
                    "field_name": error.field_name,
                    "error_code": error.code,
                    "error_message": error.message,
                }
            result.rows.append(entry)
    except csv.Error:
        raise CSVValidationError(
            "CSV_MALFORMED", "CSV 引号或记录结构损坏，无法可靠划分数据行"
        ) from None
    if not result.total_records:
        raise CSVValidationError("FILE_EMPTY", "文件为空或只有表头及空行")
    return result
