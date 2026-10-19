"""Row-level validation for uploaded application CSV files."""
import csv
import io
import re
from datetime import date

COLUMNS = ("case_ref", "applicant_name", "id_number", "dob", "issue_date", "image_ref")
SYNTHETIC_ID_PREFIX = "TEST-ID-"
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


class CsvFormatError(ValueError):
    """The file as a whole cannot be read, so no row can be imported."""


def parse_date(value: str) -> date | None:
    """Parse a strict YYYY-MM-DD date, or return None.

    The pattern check comes first because date.fromisoformat() also accepts
    other ISO forms such as 20210202 on Python 3.11+.
    """
    if not DATE_PATTERN.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:  # well-formed but impossible, e.g. 1975-13-12
        return None


def _row_problems(row: dict[str, str], first_row_of_ref: dict[str, int]) -> list[str]:
    problems = [f"{column} is required" for column in COLUMNS if not row[column]]
    if row["id_number"] and not row["id_number"].startswith(SYNTHETIC_ID_PREFIX):
        problems.append(f"id_number must start with {SYNTHETIC_ID_PREFIX}")

    dates = {}
    for column in ("dob", "issue_date"):
        if row[column]:
            dates[column] = parse_date(row[column])
            if dates[column] is None:
                problems.append(f"{column} '{row[column]}' is not a valid YYYY-MM-DD date")
    if dates.get("dob") and dates.get("issue_date") and dates["issue_date"] <= dates["dob"]:
        problems.append("issue_date must be after dob")

    if row["case_ref"] in first_row_of_ref:
        problems.append(f"duplicate case_ref; first appears on row {first_row_of_ref[row['case_ref']]}")
    return problems


def validate_csv(content: bytes) -> tuple[list[dict], list[dict]]:
    """Split a CSV file into accepted rows and rejected rows with reasons.

    `row` is the row number staff see in a spreadsheet: the header is row 1
    and every CSV record is one row, blank lines included. A quoted value with
    a line break spans two file lines but is still one row, so records are
    counted rather than lines. A case_ref may appear once per file: the first
    row keeps it and later rows are rejected as duplicates.
    """
    try:
        text = content.decode("utf-8-sig")  # -sig drops the byte-order mark Excel adds
    except UnicodeDecodeError:
        raise CsvFormatError("file must be a UTF-8 encoded CSV") from None

    records = csv.reader(io.StringIO(text))
    accepted, rejected = [], []
    first_row_of_ref: dict[str, int] = {}
    try:
        header = next(records, [])
        if not header:
            raise CsvFormatError("file is empty")
        missing = [column for column in COLUMNS if column not in header]
        if missing:
            raise CsvFormatError(f"missing required columns: {', '.join(missing)}")

        for row_number, values in enumerate(records, start=2):
            if not values:  # a blank line is an empty spreadsheet row with nothing to import
                continue
            raw = dict(zip(header, values))
            row = {column: raw.get(column, "").strip() for column in COLUMNS}
            problems = _row_problems(row, first_row_of_ref)
            if len(values) > len(header):
                problems.append("row has more values than the header")
            if row["case_ref"]:
                first_row_of_ref.setdefault(row["case_ref"], row_number)

            if problems:
                rejected.append({"row": row_number, "case_ref": row["case_ref"] or None, "reasons": problems})
            else:
                accepted.append({"row": row_number, **row})
    except csv.Error as exc:
        # A parse error is located by file line, which is what an editor shows.
        raise CsvFormatError(f"malformed CSV at line {records.line_num}: {exc}") from None

    if not accepted and not rejected:
        raise CsvFormatError("file has a header but no application rows")
    return accepted, rejected
