"""SKU Import V1 wire contract and frozen workbook vocabulary.

Reuses the Customer Import V1 payload schemas (UploadResult, ReviewPayload,
BatchDetail, ...) exactly like Product Import V1 does. entity_type = SKU.
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


SKU_TEMPLATE_SHEET = "SKU导入"
SKU_TEMPLATE_HEADER_ROW = 1
SKU_TEMPLATE_COLUMNS = (
    "SKU Code",
    "SKU 名称",
    "所属 Product Code",
    "ERP分组",
    "产品形式",
    "产地",
    "产品一级分类（大类）",
    "产品二级分类（品类）",
    "产品三级分类（规格）",
    "产品四级分类（克数）",
    "简称",
    "扩展分类",
    "箱规",
    "创建日期",
)
SKU_TEMPLATE_REQUIRED_FIELDS = ("sku_code", "sku_name", "product_code_ref")
SKU_IMPORT_SOURCE_SYSTEM = "EXCEL_SKU_IMPORT_V1"

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
    # Frozen Final-Gate decision: unparseable optional 创建日期 is a blocking
    # ERROR in SKU V1 (never an acknowledgeable Warning, never silent NULL).
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
