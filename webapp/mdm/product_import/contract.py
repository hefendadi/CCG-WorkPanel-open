"""Product Import V1 wire contract and frozen workbook vocabulary."""

from __future__ import annotations

from typing import Iterable

from webapp.mdm.customer_import.contract import (
    AcknowledgeRequest,
    AcknowledgeResponse,
    BatchDetail,
    BatchListItem,
    BatchStatus,
    CommitResult,
    ExistingView,
    FileError,
    FindingCategory,
    FindingSeverity,
    FindingView,
    ReviewCounts,
    ReviewGates,
    ReviewPayload,
    ReviewRow,
    RowStatus,
    RowSummary,
    UploadResult,
    ValidationResult,
)


PRODUCT_TEMPLATE_SHEET = "Product导入"
PRODUCT_TEMPLATE_HEADER_ROW = 1
PRODUCT_TEMPLATE_COLUMNS = ("Product Code", "Product 名称", "品牌")
PRODUCT_TEMPLATE_REQUIRED_FIELDS = ("product_code", "product_name")
PRODUCT_IMPORT_SOURCE_SYSTEM = "EXCEL_PRODUCT_IMPORT_V1"

FindingSpec = tuple[FindingSeverity, FindingCategory, tuple[str, ...]]
FINDING_SPECS: dict[str, FindingSpec] = {
    "MDM_IMPORT_FILE_INVALID": (FindingSeverity.ERROR, FindingCategory.FILE, ()),
    "MDM_IMPORT_SHEET_INVALID": (FindingSeverity.ERROR, FindingCategory.FILE, ()),
    "MDM_IMPORT_REQUIRED_FIELD_MISSING": (
        FindingSeverity.ERROR,
        FindingCategory.BLOCK,
        (),
    ),
    "MDM_IMPORT_VALUE_INVALID": (FindingSeverity.ERROR, FindingCategory.BLOCK, ()),
    "MDM_IMPORT_CODE_NOT_STRING_SAFE": (
        FindingSeverity.ERROR,
        FindingCategory.BLOCK,
        (),
    ),
    "MDM_IMPORT_DUPLICATE_UNIQUE_CODE": (
        FindingSeverity.ERROR,
        FindingCategory.BLOCK,
        (),
    ),
    "MDM_IMPORT_PRODUCT_CODE_CONFLICT": (
        FindingSeverity.ERROR,
        FindingCategory.BLOCK,
        (),
    ),
    "MDM_IMPORT_PRODUCT_NAME_CONFLICT": (
        FindingSeverity.WARNING,
        FindingCategory.DECIDE,
        ("ACKNOWLEDGE",),
    ),
    "MDM_IMPORT_PRODUCT_EXISTING": (
        FindingSeverity.INFO,
        FindingCategory.EXISTING,
        (),
    ),
}
EXISTING_CODE = "MDM_IMPORT_PRODUCT_EXISTING"


def finding_spec(code: str) -> FindingSpec:
    return FINDING_SPECS[code]


def allowed_decisions(code: str) -> tuple[str, ...]:
    return finding_spec(code)[2]


def derive_counts(statuses: Iterable[RowStatus]) -> ReviewCounts:
    counts = ReviewCounts(total=0)
    for status in statuses:
        counts.total += 1
        if status in {RowStatus.VALID, RowStatus.READY}:
            counts.ready += 1
        elif status == RowStatus.WARNING:
            counts.warning += 1
        elif status == RowStatus.ERROR:
            counts.error += 1
        elif status == RowStatus.EXISTING:
            counts.existing += 1
    counts.create = counts.total - counts.error - counts.existing
    return counts


def derive_commit_eligible(counts: ReviewCounts) -> bool:
    return counts.error == 0 and counts.warning == 0
