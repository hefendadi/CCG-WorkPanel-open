"""Customer Import V1 — shared App/Engine contract (public contract simplified).

This module is the single source of truth for the wire shapes and vocabulary
that the App (Upload / Review / Decision API and Review page) and the Import
Engine (parse / normalize / validate / stage) agree on.

Frozen boundary for Customer Import V1:

    Upload -> Validation Result -> Operator Review -> READY   (this step)
    Admin Commit                                             (next step, reserved)

public contract simplification (per docs/import-contracts.md and the public contract verdict):

- required field missing        = ERROR (fix in Excel, re-upload; no fill in Review)
- any within-file repeated Customer Code = ERROR on ALL occurrences
- existing code with different/ambiguous identity = ERROR (no acknowledge/create)
- exact Existing (code+name)    = INFO, informational, does not block
  existing differences snapshot covers regular fields AND references
- normal reference unresolved   = ERROR
- parent unresolved             = WARNING + acknowledge -> final write NULL
- parent cycle                  = ERROR
- unacknowledged Warning blocks Commit eligibility
- review_version is kept as the optimistic concurrency guard
- Decision is the minimal acknowledge only: KEEP_SEPARATE / ACKNOWLEDGE
- removed: FILL_VALUE, MAP_PARENT, SET_NULL, REVIEW/FILL machinery, VALIDATED
- issue/status vocabulary reuses the existing Engine/DB vocabulary
  (MDM_IMPORT_* shared codes + MDM_IMPORT_CUSTOMER_* new codes)

It deliberately contains no engine validation logic, persistence logic, HTTP
endpoints or UI. Neither side may add codes, statuses, fields or endpoints
without a versioned contract change.
"""

from __future__ import annotations

import enum
from typing import Any, Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Frozen Customer Import V1 workbook template
# ---------------------------------------------------------------------------

CUSTOMER_TEMPLATE_SHEET = "Customers"
CUSTOMER_TEMPLATE_HEADER_ROW = 1

CUSTOMER_TEMPLATE_COLUMNS: tuple[str, ...] = (
    "客户编码",
    "客户名称",
    "组织",
    "部门",
    "Business Type",
    "Market Type",
    "渠道",
    "销售地区",
    "省份",
    "业态",
    "渠道明细",
    "销售员",
    "是否直营",
    "上级客户",
    "创建年月",
)

# Normalized fields that must resolve to a non-empty value for every row.
CUSTOMER_TEMPLATE_REQUIRED_FIELDS: tuple[str, ...] = (
    "customer_code",
    "customer_name",
    "channel_ref",
    "salesrep_ref",
)

# Default source system tag for V1 operational uploads.
CUSTOMER_IMPORT_SOURCE_SYSTEM = "EXCEL_CUSTOMER_IMPORT_V1"


# ---------------------------------------------------------------------------
# Vocabulary enums
# ---------------------------------------------------------------------------


class FindingSeverity(str, enum.Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class RowStatus(str, enum.Enum):
    """Contract subset of the mdm_import_row status CHECK.

    Every value is a valid mdm_import_row.status value; the remaining DB values
    (SKIPPED, EXCLUDED, REVIEW, RETURNED) belong to other flows, not Customer
    Import V1. READY is the reviewed, commit-eligible row state with a frozen
    reviewed_values snapshot (the V1 path persists clean rows as READY at
    stage); VALID is the transient in-memory parse state never persisted by
    V1. A WARNING row becomes READY after its acknowledge.
    """

    PENDING = "PENDING"
    VALID = "VALID"
    READY = "READY"
    WARNING = "WARNING"
    ERROR = "ERROR"
    EXISTING = "EXISTING"
    COMMITTED = "COMMITTED"


class CommitResult(str, enum.Enum):
    """Terminal row outcomes produced by the (next-step) Admin Commit."""

    CREATED = "CREATED"
    EXISTING = "EXISTING"


class BatchStatus(str, enum.Enum):
    """Existing mdm_import_batch status values used by the V1 lifecycle.

        UPLOADED -> READY_FOR_REVIEW -> READY_TO_COMMIT -> COMMITTED   (next step)
        REVIEW -------------------------^  (has ERROR rows or open Warnings)
        FAILED = file-level rejection; CANCELLED = deferred.

    A clean Batch (error == 0 and open_warning == 0) transitions directly to
    READY_TO_COMMIT with no Operator confirmation; READY_TO_COMMIT is reached
    through REVIEW only when acknowledged Warnings were required.
    """

    UPLOADED = "UPLOADED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    REVIEW = "REVIEW"
    READY_TO_COMMIT = "READY_TO_COMMIT"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FindingCategory(str, enum.Enum):
    """How the App may act on a finding.

    FILE     -> file-level rejection; no rows are produced.
    BLOCK    -> not fixable in Review; fix the workbook and upload a new Batch.
    DECIDE   -> a Warning requiring one minimal operator acknowledge.
    EXISTING -> informational terminal outcome; no decision required.
    INFO     -> informational only.
    """

    FILE = "FILE"
    BLOCK = "BLOCK"
    DECIDE = "DECIDE"
    EXISTING = "EXISTING"
    INFO = "INFO"


# ---------------------------------------------------------------------------
# Issue / finding vocabulary (reuses existing Engine/DB codes)
# ---------------------------------------------------------------------------

FindingSpec = tuple[FindingSeverity, FindingCategory, tuple[str, ...]]

FINDING_SPECS: dict[str, FindingSpec] = {
    # File-level rejection (Batch FAILED, zero rows).
    "MDM_IMPORT_FILE_INVALID": (FindingSeverity.ERROR, FindingCategory.FILE, ()),
    "MDM_IMPORT_SHEET_INVALID": (FindingSeverity.ERROR, FindingCategory.FILE, ()),
    # Row-level blocking errors (fix workbook, upload a new Batch).
    "MDM_IMPORT_REQUIRED_FIELD_MISSING": (FindingSeverity.ERROR, FindingCategory.BLOCK, ()),
    "MDM_IMPORT_VALUE_INVALID": (FindingSeverity.ERROR, FindingCategory.BLOCK, ()),
    "MDM_IMPORT_CODE_NOT_STRING_SAFE": (FindingSeverity.ERROR, FindingCategory.BLOCK, ()),
    # Within-file exact duplicate: ERROR on BOTH duplicate rows (V1 severity).
    "MDM_IMPORT_DUPLICATE_EXACT": (FindingSeverity.ERROR, FindingCategory.BLOCK, ()),
    "MDM_IMPORT_REFERENCE_UNRESOLVED": (FindingSeverity.ERROR, FindingCategory.BLOCK, ()),
    "MDM_IMPORT_INACTIVE_REFERENCE": (FindingSeverity.ERROR, FindingCategory.BLOCK, ()),
    "MDM_IMPORT_PARENT_CYCLE": (FindingSeverity.ERROR, FindingCategory.BLOCK, ()),
    "MDM_IMPORT_CUSTOMER_CODE_CONFLICT": (
        FindingSeverity.ERROR,
        FindingCategory.BLOCK,
        (),
    ),
    # Remaining warnings allow one minimal operator acknowledge during Review.
    # KEEP_SEPARATE/SET_NULL/MAP_PARENT are not part of the V1 wire contract.
    "MDM_IMPORT_CUSTOMER_NAME_CONFLICT": (
        FindingSeverity.WARNING,
        FindingCategory.DECIDE,
        ("ACKNOWLEDGE",),
    ),
    "MDM_IMPORT_PARENT_UNRESOLVED": (
        FindingSeverity.WARNING,
        FindingCategory.DECIDE,
        ("ACKNOWLEDGE",),
    ),
    # Informational outcomes.
    "MDM_IMPORT_CUSTOMER_EXISTING": (FindingSeverity.INFO, FindingCategory.EXISTING, ()),
    "MDM_IMPORT_FIELD_MISSING_NONCRITICAL": (
        FindingSeverity.INFO,
        FindingCategory.INFO,
        (),
    ),
}

# Existing-with-difference code: the frozen difference snapshot lives in
# FindingView.details["differences"] and covers regular Customer fields AND
# reference fields (channel / salesrep / region / province / parent_customer).
EXISTING_CODE = "MDM_IMPORT_CUSTOMER_EXISTING"

# Regular Customer fields compared for the Existing difference snapshot.
EXISTING_DIFFERENCE_FIELDS = (
    "organization",
    "department",
    "business_type",
    "market_type",
    "format_type",
    "channel_detail",
    "is_direct",
    "source_created_ym",
)

# Reference fields compared for the Existing difference snapshot (by resolved
# stable_id: {"source": ..., "master": ...}).
EXISTING_DIFFERENCE_REFERENCES = (
    "channel",
    "salesrep",
    "region",
    "province",
    "parent_customer",
)


def finding_spec(code: str) -> FindingSpec:
    """Contract lookup for one issue code (raises KeyError for unknown codes)."""
    return FINDING_SPECS[code]


def severity_value(code: str) -> str:
    return finding_spec(code)[0].value


def category_value(code: str) -> str:
    return finding_spec(code)[1].value


def allowed_decisions(code: str) -> tuple[str, ...]:
    return finding_spec(code)[2]


# ---------------------------------------------------------------------------
# Contract derivation rules (the App renders these; the Engine must match them)
# ---------------------------------------------------------------------------


def expected_row_status(codes: Iterable[str]) -> RowStatus:
    """Contract status-precedence rule for a row given its issue codes.

    ERROR(BLOCK) > EXISTING > WARNING(DECIDE) > READY.
    A row is never both EXISTING and Warning-bearing: exact pair match and
    cross-match Warnings are mutually exclusive by construction. Clean rows
    (no findings) are READY: in V1 they are immediately reviewed with a frozen
    reviewed_values snapshot and require no Operator confirmation.
    """
    categories = {category_value(code) for code in codes}
    if FindingCategory.BLOCK.value in categories:
        return RowStatus.ERROR
    if FindingCategory.EXISTING.value in categories:
        return RowStatus.EXISTING
    if FindingCategory.DECIDE.value in categories:
        return RowStatus.WARNING
    return RowStatus.READY


def row_create_intent(status: RowStatus) -> bool:
    """Whether the row participates in CREATE at Commit (next step)."""
    return status not in {RowStatus.ERROR, RowStatus.EXISTING, RowStatus.COMMITTED}


def derive_counts(statuses: Iterable[RowStatus]) -> "ReviewCounts":
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


def derive_commit_eligible(counts: "ReviewCounts") -> bool:
    """commit_eligible = (error == 0) and (open_warning == 0).

    open_warning counts only rows in WARNING status, i.e. Warnings that are not
    yet acknowledged. Acknowledged Warning rows become READY; their historical
    Warning Findings are retained (resolved=True with the linked append-only
    Decision) and never block again.
    """
    return counts.error == 0 and counts.warning == 0


def derive_batch_status(counts: "ReviewCounts") -> BatchStatus:
    """Batch lifecycle rule for valid-file batches.

    A clean Batch (error == 0 and open_warning == 0) is READY_TO_COMMIT
    directly — no meaningless Operator confirmation is required. A Batch with
    ERROR rows or open Warnings is REVIEW and reaches READY_TO_COMMIT only
    after every Warning is acknowledged.
    """
    if derive_commit_eligible(counts):
        return BatchStatus.READY_TO_COMMIT
    return BatchStatus.REVIEW


# ---------------------------------------------------------------------------
# API payload schemas (Review page data structures and decision wire shapes)
# ---------------------------------------------------------------------------


class FindingView(BaseModel):
    """One validation finding exactly as the Review page renders it."""

    model_config = ConfigDict(extra="forbid")

    finding_id: Optional[int] = None
    code: str
    severity: FindingSeverity
    message: str
    field: Optional[str] = None
    details: Optional[dict[str, Any]] = None
    resolved: bool = False
    allowed_decisions: list[str] = Field(default_factory=list)
    decision: Optional[str] = None


class ExistingView(BaseModel):
    """Existing-with-(optionally)-Difference block for one Review row."""

    model_config = ConfigDict(extra="forbid")

    is_existing: bool = False
    matched_stable_id: Optional[str] = None
    master_detail_url: Optional[str] = None
    # Frozen field-level difference: {field: {"source": ..., "master": ...}}.
    # Regular fields use normalized values; reference fields use resolved
    # stable_id. Historical display must never query live Master values.
    difference: Optional[dict[str, Any]] = None


class ReviewRow(BaseModel):
    """One staged row in the Review payload."""

    model_config = ConfigDict(extra="forbid")

    row_number: int = Field(ge=1)
    status: RowStatus
    identity: dict[str, str] = Field(
        ..., description='{"customer_code": ..., "customer_name": ...}'
    )
    raw_values: dict[str, Any] = Field(
        ..., description="immutable source cell values, one key per template column"
    )
    normalized_values: Optional[dict[str, Any]] = None
    reviewed_values: Optional[dict[str, Any]] = None
    findings: list[FindingView] = Field(default_factory=list)
    existing: ExistingView = Field(default_factory=ExistingView)


class ReviewCounts(BaseModel):
    """Readiness counts; keys match the Engine's review readiness response.

    ready = commit-eligible rows (READY, or transient VALID at parse). create
    is derived by the App as total - error - existing; the Engine returns the
    remaining five keys.
    """

    model_config = ConfigDict(extra="forbid")

    total: int = 0
    ready: int = 0
    warning: int = 0
    error: int = 0
    existing: int = 0
    create: int = 0


class ReviewGates(BaseModel):
    """What still blocks the Batch from becoming commit-eligible.

    open_warnings lists only open (unacknowledged) Warnings. Acknowledged
    Warning Findings remain in row history but never appear here again.
    """

    model_config = ConfigDict(extra="forbid")

    open_errors: list[dict[str, Any]] = Field(
        default_factory=list, description="[{row_number, issue_code}]"
    )
    open_warnings: list[dict[str, Any]] = Field(
        default_factory=list, description="[{row_number, issue_code}]"
    )
    ready: bool = False


class ReviewPayload(BaseModel):
    """GET .../review — the complete Review page data structure."""

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    status: str
    import_mode: str = "OPERATIONAL"
    source_file: str
    review_version: int = Field(ge=1)
    counts: ReviewCounts
    gates: ReviewGates
    commit_eligible: bool
    rows: list[ReviewRow] = Field(default_factory=list)


class BatchListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str
    source_file: str
    status: str
    uploaded_by: Optional[int] = None
    created_at: Optional[str] = None
    counts: ReviewCounts
    commit_eligible: bool


class BatchDetail(BaseModel):
    """GET .../{batch_id} — batch identity + validation result."""

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    status: str
    import_mode: str = "OPERATIONAL"
    source_file: str
    source_sha256: Optional[str] = None
    uploaded_by: Optional[int] = None
    created_at: Optional[str] = None
    review_version: Optional[int] = None
    counts: ReviewCounts
    gates: ReviewGates
    commit_eligible: bool
    # {issue_code: [row_numbers]} for the batch-level readiness summary.
    summary_by_issue: dict[str, list[int]] = Field(default_factory=dict)


class RowSummary(BaseModel):
    """Lightweight per-row result included in the Upload response."""

    model_config = ConfigDict(extra="forbid")

    row_number: int = Field(ge=1)
    status: RowStatus
    issue_codes: list[str] = Field(default_factory=list)


class FileError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: FindingSeverity
    message: str


class ValidationResult(BaseModel):
    """Validation result returned by Upload and embedded in Batch detail."""

    model_config = ConfigDict(extra="forbid")

    file_valid: bool
    sheet: Optional[str] = None
    file_errors: list[FileError] = Field(default_factory=list)
    counts: ReviewCounts = Field(default_factory=ReviewCounts)
    commit_eligible: bool = False


class UploadResult(BaseModel):
    """POST .../batches (multipart) — the Upload -> Validation Result response."""

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    status: str
    source_file: str
    source_sha256: Optional[str] = None
    review_version: Optional[int] = None
    validation: ValidationResult
    rows: list[RowSummary] = Field(default_factory=list)


class AcknowledgeRequest(BaseModel):
    """POST .../acknowledge — minimal warning acknowledgement.

    Select current-version Warning Findings by finding_id; the single verb is
    ACKNOWLEDGE (parent unresolved -> reviewed parent NULL). Optimistic
    concurrency via review_version; a stale version is rejected with 409
    MDM_IMPORT_STALE_REVIEW_VERSION. reason is OPTIONAL (public contract product
    decision: the Operator is not forced to type a reason).
    """

    model_config = ConfigDict(extra="forbid")

    review_version: int = Field(ge=1)
    finding_ids: list[int] = Field(min_length=1)
    reason: Optional[str] = None

    @model_validator(mode="after")
    def finding_ids_must_be_unique(self) -> "AcknowledgeRequest":
        if len(set(self.finding_ids)) != len(self.finding_ids):
            raise ValueError("finding_ids must be unique")
        if not all(isinstance(item, int) and item > 0 for item in self.finding_ids):
            raise ValueError("finding_ids must be positive integers")
        return self


class AcknowledgeResponse(BaseModel):
    """Response after one acknowledge round; review_version is the NEW version.

    Matches the Engine's readiness response; create is derived by the App as
    total - error - existing.
    """

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    status: str
    review_version: int = Field(ge=1)
    commit_eligible: bool
    counts: ReviewCounts
    # Serialized append-only decisions (id, finding_id, row_number, issue_code,
    # decision, reason, operator_id, review_version).
    decisions: list[dict[str, Any]] = Field(default_factory=list)
