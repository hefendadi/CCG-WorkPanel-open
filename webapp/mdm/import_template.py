"""Generate empty MDM Import workbooks from canonical contract values."""

from __future__ import annotations

import io
from collections.abc import Sequence

from openpyxl import Workbook


XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def build_import_template(sheet_name: str, columns: Sequence[str]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet.append(list(columns))
    payload = io.BytesIO()
    workbook.save(payload)
    return payload.getvalue()
