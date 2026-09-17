"""Deterministic, shared value normalization for all four import types."""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl.utils.datetime import from_excel

from .domain import ADAPTERS, Adapter, CellValue, ImportType, ParsedRow


class NormalizationError(ValueError):
    pass


_BOOLEAN_TRUE = {"是", "yes", "y", "1", "true", "☑"}
_BOOLEAN_FALSE = {"否", "no", "n", "0", "false", "☐"}
_ZERO_FORMAT = re.compile(r"^0+$")


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value))
    text = " ".join(text.split()).strip()
    return text or None


def normalize_code(cell: CellValue) -> str | None:
    value = cell.value
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise NormalizationError("boolean cannot be used as a code")
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, (float, Decimal)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise NormalizationError("non-finite numeric code")
        decimal_value = Decimal(str(value))
        text = str(int(decimal_value)) if decimal_value == decimal_value.to_integral() else format(decimal_value.normalize(), "f")
    else:
        return normalize_text(value)
    number_format = unicodedata.normalize("NFKC", cell.number_format or "").strip()
    if _ZERO_FORMAT.fullmatch(number_format) and len(text) < len(number_format):
        text = text.zfill(len(number_format))
    return text


def normalize_boolean(value: Any) -> bool | None:
    text = normalize_text(value)
    if text is None:
        return None
    folded = text.casefold()
    if folded in _BOOLEAN_TRUE:
        return True
    if folded in _BOOLEAN_FALSE:
        return False
    raise NormalizationError(f"unsupported boolean: {text}")


def normalize_decimal(value: Any) -> str | None:
    text = normalize_text(value)
    if text is None:
        return None
    try:
        number = Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise NormalizationError(f"invalid decimal: {text}") from exc
    if not number.is_finite():
        raise NormalizationError(f"invalid decimal: {text}")
    return format(number.normalize(), "f")


def normalize_date(cell: CellValue) -> str | None:
    value = cell.value
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and (cell.is_date or 1 <= value <= 60000):
        try:
            converted = from_excel(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise NormalizationError(f"invalid Excel date: {value}") from exc
        return converted.isoformat()
    text = normalize_text(value)
    if text is None:
        return None
    for parser in (date.fromisoformat, datetime.fromisoformat):
        try:
            return parser(text).isoformat()
        except ValueError:
            continue
    for pattern in (r"^(\d{4})[./-](\d{1,2})[./-](\d{1,2})$", r"^(\d{4})(\d{2})(\d{2})$"):
        matched = re.match(pattern, text)
        if matched:
            try:
                return date(*(int(part) for part in matched.groups())).isoformat()
            except ValueError as exc:
                raise NormalizationError(f"invalid date: {text}") from exc
    matched = re.match(r"^(\d{2})(\d{2})(\d{2})$", text)
    if matched:
        year, month, day = (int(part) for part in matched.groups())
        try:
            return date(2000 + year, month, day).isoformat()
        except ValueError as exc:
            raise NormalizationError(f"invalid date: {text}") from exc
    raise NormalizationError(f"ambiguous date: {text}")


def normalize_row(
    import_type: ImportType, row: ParsedRow, *, adapter: Adapter | None = None
) -> tuple[dict[str, Any], dict[str, str]]:
    # SKU Import V1 passes its own frozen adapter; Bootstrap defaults to
    # ADAPTERS[import_type] (backward compatible).
    resolved_adapter = adapter or ADAPTERS[import_type]
    normalized: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for field, kind in resolved_adapter.field_kinds.items():
        cell = row.cells.get(field)
        if cell is None:
            # Workbook did not provide this (optional) template column.
            normalized[field] = None
            continue
        try:
            if kind == "text":
                normalized[field] = normalize_text(cell.value)
            elif kind == "code":
                normalized[field] = normalize_code(cell)
            elif kind == "boolean":
                normalized[field] = normalize_boolean(cell.value)
            elif kind == "decimal":
                normalized[field] = normalize_decimal(cell.value)
            elif kind == "date":
                normalized[field] = normalize_date(cell)
            else:
                raise RuntimeError(f"Unknown normalizer kind: {kind}")
        except NormalizationError as exc:
            normalized[field] = None
            errors[field] = str(exc)
    return normalized, errors
