"""商品导入 V2 wire contract and frozen workbook vocabulary.

Reuses the Customer Import V1 payload schemas (UploadResult, ReviewPayload,
BatchDetail, ...) exactly like Product Import V1 / SKU Import V1 do.
entity_type = PRODUCT_SKU; every staged row is an SKU row that also carries its
Product plan (existing reference or batch-created Product) in _meta.
"""

from __future__ import annotations

from typing import Iterable

from webapp.mdm.customer_import.contract import (
    AcknowledgeRequest,
    AcknowledgeResponse,
    BatchDetail,
    BatchListItem,
    BatchStatus,
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

from webapp.mdm.import_engine.domain import (
    GOODS_V1_OPTIONAL_COLUMNS,
    GOODS_V1_REQUIRED_COLUMNS,
    GOODS_V1_SHEET,
)


GOODS_TEMPLATE_SHEET = GOODS_V1_SHEET
GOODS_TEMPLATE_HEADER_ROW = 1
# The frozen 商品导入 template = the 14 legacy business columns plus the two
# OPTIONAL explicit Product attribute columns (Product 名称 / 品牌).
GOODS_TEMPLATE_COLUMNS: tuple[str, ...] = GOODS_V1_REQUIRED_COLUMNS + GOODS_V1_OPTIONAL_COLUMNS
GOODS_TEMPLATE_REQUIRED_FIELDS = ("sku_code", "sku_name", "product_code")
GOODS_IMPORT_SOURCE_SYSTEM = "EXCEL_GOODS_IMPORT_V2"

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
    # Frozen: unparseable (non business-label) 创建日期 is a blocking ERROR.
    "MDM_IMPORT_DATE_INVALID": (
        FindingSeverity.ERROR,
        FindingCategory.BLOCK,
        (),
    ),
    "MDM_IMPORT_DUPLICATE_UNIQUE_CODE": (
        FindingSeverity.ERROR,
        FindingCategory.BLOCK,
        (),
    ),
    "MDM_IMPORT_SKU_CODE_CONFLICT": (
        FindingSeverity.ERROR,
        FindingCategory.BLOCK,
        (),
    ),
    "MDM_IMPORT_SKU_PRODUCT_CONFLICT": (
        FindingSeverity.ERROR,
        FindingCategory.BLOCK,
        (),
    ),
    # One Product Code group contradicts the Product attributes required to
    # create one coherent Product in this batch.
    "MDM_IMPORT_PRODUCT_ATTR_CONFLICT": (
        FindingSeverity.ERROR,
        FindingCategory.BLOCK,
        (),
    ),
    # A NEW Product cannot be created without an explicit Product 名称 or a
    # unique SKU Code == Product Code representative row.
    "MDM_IMPORT_PRODUCT_NAME_REQUIRED": (
        FindingSeverity.ERROR,
        FindingCategory.BLOCK,
        (),
    ),
    "MDM_IMPORT_REFERENCE_UNRESOLVED": (
        FindingSeverity.ERROR,
        FindingCategory.BLOCK,
        (),
    ),
    "MDM_IMPORT_INACTIVE_REFERENCE": (
        FindingSeverity.ERROR,
        FindingCategory.BLOCK,
        (),
    ),
    "MDM_IMPORT_SKU_EXISTING": (
        FindingSeverity.INFO,
        FindingCategory.EXISTING,
        (),
    ),
    # Existing SKU name equal under deterministic identity normalization but
    # raw text differs in format only (whitespace / full-width etc).
    "MDM_IMPORT_SKU_NAME_FORMAT_DIFFERENCE": (
        FindingSeverity.INFO,
        FindingCategory.INFO,
        (),
    ),
    "MDM_IMPORT_SOURCE_DATE_LABEL": (
        FindingSeverity.INFO,
        FindingCategory.INFO,
        (),
    ),
}
EXISTING_CODE = "MDM_IMPORT_SKU_EXISTING"


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
