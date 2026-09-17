"""Demo ERP Excel -> validated Batch/Raw only; no MDM access or business filtering."""
from __future__ import annotations

import enum
import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import openpyxl
from openpyxl.utils.datetime import from_excel

from webapp.sales.models import SalesBatchStatus, SalesImportBatch, SalesImportRow


REQUIRED_COLUMNS = (
    "明细信息行号", "日期", "单据编号", "客户", "产品类型", "物料编码", "物料名称", "实发数量",
)


class ParserErrorCode(str, enum.Enum):
    FILE_STRUCTURE_INVALID = "FILE_STRUCTURE_INVALID"
    REQUIRED_COLUMN_MISSING = "REQUIRED_COLUMN_MISSING"
    BUSINESS_DATE_INVALID = "BUSINESS_DATE_INVALID"
    SHIPPED_QTY_INVALID = "SHIPPED_QTY_INVALID"
    BUSINESS_KEY_INVALID = "BUSINESS_KEY_INVALID"
    DUPLICATE_BUSINESS_KEY = "DUPLICATE_BUSINESS_KEY"
    MULTIPLE_SNAPSHOT_MONTHS = "MULTIPLE_SNAPSHOT_MONTHS"


@dataclass(frozen=True)
class ParserError:
    code: ParserErrorCode
    message: str
    row_number: int | None = None
    field: str | None = None


@dataclass(frozen=True)
class ParsedSalesRow:
    source_row_no: int
    source_document_no: str
    source_line_no: str
    sales_date: date
    source_customer_name: str | None
    source_product_type: str | None
    source_sku_code: str | None
    source_sku_name: str | None
    actual_qty: Decimal


@dataclass(frozen=True)
class SalesParseResult:
    filename: str
    file_hash: str
    sheet_name: str | None
    rows: tuple[ParsedSalesRow, ...]
    errors: tuple[ParserError, ...]
    snapshot_month: date | None
    data_date_start: date | None
    data_date_end: date | None

    @property
    def blocked(self):
        return bool(self.errors)


class SalesParserBlocked(ValueError):
    def __init__(self, result: SalesParseResult):
        self.result = result
        super().__init__(", ".join(dict.fromkeys(error.code.value for error in result.errors)))


@dataclass(frozen=True)
class SalesRawImportResult:
    id: int
    batch_id: str
    snapshot_month: date
    row_count: int
    total_qty: Decimal


def _text(value, limit):
    if value is None:
        return None
    value = str(value).strip()
    if len(value) > limit:
        raise ValueError(f"field exceeds {limit} characters")
    return value or None


def normalize_document_no(value):
    value = _text(value, 128)
    if value is None:
        raise ValueError("document number is required")
    return value


def normalize_line_no(value):
    if value is None or isinstance(value, bool):
        raise ValueError("line number must be an integer")
    text = str(value).strip()
    if not re.fullmatch(r"[+-]?[0-9]+(?:\.0+)?", text):
        raise ValueError("line number must be an integer")
    normalized = str(int(Decimal(text)))
    if len(normalized) > 64:
        raise ValueError("line number exceeds 64 characters")
    return normalized


def _quantity(value):
    if value is None or isinstance(value, bool):
        raise ValueError("shipped quantity is required")
    try:
        text = str(value).strip()
        if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text):
            raise ValueError("invalid shipped quantity")
        qty = Decimal(text)
        if not qty.is_finite() or abs(qty) >= Decimal('100000000000000') or qty != qty.quantize(Decimal('0.0001')):
            raise ValueError("quantity cannot be stored exactly as DECIMAL(18,4)")
        return qty
    except InvalidOperation as exc:
        raise ValueError("invalid shipped quantity") from exc


def _business_date(value, epoch):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            converted = from_excel(value, epoch)
            if isinstance(converted, datetime):
                return converted.date()
        except (ValueError, OverflowError):
            pass
    if isinstance(value, str):
        for pattern in ('%Y-%m-%d', '%Y/%m/%d', '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S'):
            try:
                return datetime.strptime(value.strip(), pattern).date()
            except ValueError:
                pass
    raise ValueError("invalid business date")


def parse_sales_workbook(path: str | Path, *, sheet_name: str | None = None) -> SalesParseResult:
    source = Path(path)
    errors, rows, dates = [], [], []
    digest = ''
    selected_sheet = None

    def error(code, message, row_number=None, field=None):
        errors.append(ParserError(code, message, row_number, field))

    def result():
        months = {day.replace(day=1) for day in dates}
        return SalesParseResult(source.name, digest, selected_sheet, tuple(rows), tuple(errors),
                               next(iter(months)) if len(months) == 1 else None,
                               min(dates) if dates else None, max(dates) if dates else None)

    workbook = None
    try:
        if source.suffix.lower() != '.xlsx':
            raise ValueError('expected .xlsx workbook')
        payload = source.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        # Parse and hash identical bytes; formulas are not silently accepted as cached values.
        workbook = openpyxl.load_workbook(BytesIO(payload), read_only=True, data_only=False)
        if sheet_name is None and len(workbook.worksheets) != 1:
            raise ValueError('select one sheet explicitly for a multi-sheet workbook')
        sheet = workbook[sheet_name] if sheet_name is not None else workbook.worksheets[0]
        selected_sheet = sheet.title
        # ERP exports can carry inaccurate dimension metadata.
        sheet.reset_dimensions()
        iterator = sheet.iter_rows()
        headers = [str(c.value).strip() if c.value is not None else '' for c in next(iterator, ())]
        missing = [name for name in REQUIRED_COLUMNS if name not in headers]
        if missing:
            error(ParserErrorCode.REQUIRED_COLUMN_MISSING, 'missing columns: ' + ', '.join(missing), 1)
            return result()
        if any(headers.count(name) > 1 for name in REQUIRED_COLUMNS):
            raise ValueError('duplicate required column headers')
        indexes = {name: headers.index(name) for name in REQUIRED_COLUMNS}
        seen = {}
        count = 0
        for row_number, cells in enumerate(iterator, 2):
            values = {name: cells[index].value if index < len(cells) else None for name, index in indexes.items()}
            if all(value is None or (isinstance(value, str) and not value.strip()) for value in values.values()):
                continue
            count += 1
            before = len(errors)
            for name, index in indexes.items():
                if index < len(cells) and cells[index].data_type == 'f':
                    error(ParserErrorCode.FILE_STRUCTURE_INVALID, 'formula in required ERP field', row_number, name)
            normalized = {}
            fields = (
                ('source_document_no', '单据编号', normalize_document_no, ParserErrorCode.BUSINESS_KEY_INVALID),
                ('source_line_no', '明细信息行号', normalize_line_no, ParserErrorCode.BUSINESS_KEY_INVALID),
                ('sales_date', '日期', lambda value: _business_date(value, workbook.epoch), ParserErrorCode.BUSINESS_DATE_INVALID),
                ('actual_qty', '实发数量', _quantity, ParserErrorCode.SHIPPED_QTY_INVALID),
                ('source_customer_name', '客户', lambda value: _text(value, 255), ParserErrorCode.FILE_STRUCTURE_INVALID),
                ('source_product_type', '产品类型', lambda value: _text(value, 128), ParserErrorCode.FILE_STRUCTURE_INVALID),
                ('source_sku_code', '物料编码', lambda value: _text(value, 128), ParserErrorCode.FILE_STRUCTURE_INVALID),
                ('source_sku_name', '物料名称', lambda value: _text(value, 255), ParserErrorCode.FILE_STRUCTURE_INVALID),
            )
            for field, column, convert, code in fields:
                try:
                    normalized[field] = convert(values[column])
                except ValueError as exc:
                    error(code, str(exc), row_number, column)
            if 'sales_date' in normalized:
                dates.append(normalized['sales_date'])
            if all(key in normalized for key in ('source_document_no', 'source_line_no')):
                key = (normalized['source_document_no'], normalized['source_line_no'])
                if key in seen:
                    error(ParserErrorCode.DUPLICATE_BUSINESS_KEY, f'duplicate business key; first row {seen[key]}', row_number)
                else:
                    seen[key] = row_number
            if len(errors) == before:
                rows.append(ParsedSalesRow(source_row_no=row_number, **normalized))
        if not count:
            error(ParserErrorCode.FILE_STRUCTURE_INVALID, 'workbook has no data rows')
        if len({day.replace(day=1) for day in dates}) > 1:
            error(ParserErrorCode.MULTIPLE_SNAPSHOT_MONTHS, 'business dates span multiple natural months')
        if not errors:
            try:
                _quantity(sum((row.actual_qty for row in rows), Decimal(0)))
            except ValueError:
                error(ParserErrorCode.SHIPPED_QTY_INVALID, 'batch total exceeds DECIMAL(18,4)')
    except Exception as exc:
        error(ParserErrorCode.FILE_STRUCTURE_INVALID, str(exc))
    finally:
        if workbook is not None:
            workbook.close()
    return result()


def import_sales_workbook(session_factory, path: str | Path, *, sheet_name: str | None = None) -> SalesRawImportResult:
    """Validate fully before an atomic Batch/Raw insert. Blocked files write nothing.

    VALIDATED means parser validation only. All raw rows remain PENDING for the
    later business-filter/mapping phase; zero/negative quantities and kit parents
    are retained here. Business batch IDs follow MDM's str(uuid4()) convention.
    """
    from dataclasses import asdict
    parsed = parse_sales_workbook(path, sheet_name=sheet_name)
    if parsed.blocked:
        raise SalesParserBlocked(parsed)
    total_qty = sum((row.actual_qty for row in parsed.rows), Decimal(0))
    with session_factory() as session, session.begin():
        batch = SalesImportBatch(batch_id=str(uuid4()), filename=parsed.filename, file_hash=parsed.file_hash,
                                 source_system='DEMO_ERP', snapshot_month=parsed.snapshot_month,
                                 data_date_start=parsed.data_date_start, data_date_end=parsed.data_date_end,
                                 status=SalesBatchStatus.VALIDATED, total_rows=len(parsed.rows), total_qty=total_qty)
        session.add(batch)
        session.flush()
        session.add_all(SalesImportRow(import_batch_id=batch.id, **asdict(row)) for row in parsed.rows)
        session.flush()
        return SalesRawImportResult(batch.id, batch.batch_id, parsed.snapshot_month, len(parsed.rows), total_qty)
