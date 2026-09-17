"""Customer Import V1 — UI Contract (public contract, frozen).

Executable definition of the Import Center V1 user interface: page routes,
page states, the Engine/API status -> Chinese copy mapping, the four Review
tabs (需要修改 / 需要确认 / 已存在 / 可新增), the Error-first gate, the
Operator/Admin button rules and every user-facing success/error string.

Boundaries (frozen for public contract):

- NO HTML/CSS/JS lives here; this module only freezes the *contract* a future
  page implements.
- NO Engine rule is copied: every derivation below is a pure function over the
  frozen HTTP API responses (webapp.mdm.customer_import.contract). The Engine
  remains the single authority for statuses, counts and acknowledge/commit.
- NO API extension: anything the UI needs that the current HTTP API cannot
  provide is reported as a gap in docs/import-contracts.md, never patched here.

Display rules (frozen):

- The UI never shows these technical terms: Finding, Decision,
  review_version, commit_eligible, READY_TO_COMMIT, ACKNOWLEDGE,
  committed_snapshot. Internal values (finding ids, review_version) travel in
  API calls but are never rendered.
- Error-first: while Batch ERROR > 0 the UI does not offer any Warning
  acknowledgement and shows 请修改 Excel 后重新上传; Warning confirmation is
  opened only when ERROR == 0.
- Existing rows are display-only (no operation, no UPDATE).
- Warning rows show a concrete business-action sentence; the backend call is
  always the single ACKNOWLEDGE verb.
- Operator: Upload / Review / Acknowledge, never Commit.
  Admin: additionally Commit when the Batch is READY_TO_COMMIT.
"""

from __future__ import annotations

import enum
from collections import defaultdict
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .contract import (
    ExistingView,
    FINDING_SPECS,
    ReviewPayload,
    severity_value,
)

# ---------------------------------------------------------------------------
# Page routes (frozen; document-only, no routing code is provided)
# ---------------------------------------------------------------------------

PAGE_ROUTES: dict[str, str] = {
    "home": "/mdm/import-center",
    "upload": "/mdm/import-center/upload",
    "review": "/mdm/import-center/batches/{batch_id}/review",
    "commit": "/mdm/import-center/batches/{batch_id}/commit",
}

# Page sections (页面结构, frozen).
PAGE_STRUCTURE: dict[str, tuple[str, ...]] = {
    "home": ("批次列表", "新建客户导入"),
    "upload": ("文件选择", "校验结果"),
    "review": ("批次摘要", "四类 Tab", "确认操作"),
    "commit": ("提交前汇总", "Admin Commit", "提交结果"),
}

# ---------------------------------------------------------------------------
# Page states (页面状态, frozen)
# ---------------------------------------------------------------------------

PAGE_STATES: dict[str, tuple[str, ...]] = {
    "home": ("LOADING", "EMPTY", "READY", "ERROR"),
    "upload": ("IDLE", "SUBMITTING", "VALIDATED", "FILE_REJECTED", "SERVER_ERROR"),
    "review": ("LOADING", "READY", "ACKNOWLEDGING", "ACKNOWLEDGED", "NOT_FOUND", "SERVER_ERROR"),
    "commit": ("SUMMARY", "SUBMITTING", "COMMITTED", "REJECTED", "SERVER_ERROR"),
}

# ---------------------------------------------------------------------------
# Technical terms the UI must never render
# ---------------------------------------------------------------------------

BANNED_TECH_TERMS: tuple[str, ...] = (
    "Finding",
    "Decision",
    "review_version",
    "commit_eligible",
    "READY_TO_COMMIT",
    "ACKNOWLEDGE",
    "committed_snapshot",
)

# ---------------------------------------------------------------------------
# Engine/API status -> Chinese UI copy (状态中文化, frozen)
# ---------------------------------------------------------------------------

BATCH_STATUS_COPY: dict[str, str] = {
    "UPLOADED": "已上传",
    "READY_FOR_REVIEW": "待审核",
    "REVIEW": "待处理",
    "READY_TO_COMMIT": "可提交",
    "COMMITTED": "已提交",
    "FAILED": "导入失败",
    "CANCELLED": "已取消",
}

ROW_STATUS_COPY: dict[str, str] = {
    "PENDING": "待处理",
    "VALID": "有效",
    "READY": "可新增",
    "WARNING": "需要确认",
    "ERROR": "需要修改",
    "EXISTING": "已存在",
    "COMMITTED": "已提交",
}

# Per-batch home-list action label by status.
BATCH_ACTION_COPY: dict[str, str] = {
    "REVIEW": "去处理",
    "READY_TO_COMMIT": "查看并提交",
    "COMMITTED": "查看结果",
    "FAILED": "查看原因",
}


def batch_status_copy(status: str) -> str:
    """Chinese display copy for one Engine batch status (falls back to the
    raw status, which the UI contract test forbids for known values)."""
    return BATCH_STATUS_COPY.get(status, status)


def row_status_copy(status: str) -> str:
    return ROW_STATUS_COPY.get(status, status)


# ---------------------------------------------------------------------------
# Issue -> user-facing copy (business action text; frozen 14-code vocabulary)
# ---------------------------------------------------------------------------

INFO_ISSUE_COPY: dict[str, str] = {
    "MDM_IMPORT_CUSTOMER_EXISTING": "该行客户编码与名称已存在于主数据，仅展示，不会重复创建",
    "MDM_IMPORT_FIELD_MISSING_NONCRITICAL": "存在非必填字段未填写，不影响导入",
}

WARNING_ISSUE_COPY: dict[str, str] = {
    "MDM_IMPORT_CUSTOMER_CODE_CONFLICT": "该客户编码在主数据中对应了不同的客户名称，确认后按本行数据继续创建",
    "MDM_IMPORT_CUSTOMER_NAME_CONFLICT": "该客户名称在主数据中对应了不同的客户编码，确认后按本行数据继续创建",
    "MDM_IMPORT_PARENT_UNRESOLVED": "上级客户未匹配到主数据，确认后该客户不设置上级客户",
}

ERROR_ISSUE_COPY: dict[str, str] = {
    "MDM_IMPORT_FILE_INVALID": "文件不是有效的 Excel 工作簿，请重新上传",
    "MDM_IMPORT_SHEET_INVALID": "工作表不符合导入模板要求，请使用冻结模板",
    "MDM_IMPORT_REQUIRED_FIELD_MISSING": "必填字段为空，请在 Excel 中补充后重新上传",
    "MDM_IMPORT_VALUE_INVALID": "字段值不符合格式要求，请在 Excel 中修正后重新上传",
    "MDM_IMPORT_CODE_NOT_STRING_SAFE": "编码无法保真，请在 Excel 中修改后重新上传",
    "MDM_IMPORT_DUPLICATE_EXACT": "文件内存在完全重复的行，请删除重复行后重新上传",
    "MDM_IMPORT_REFERENCE_UNRESOLVED": "引用的渠道/销售员/地区/省份不存在，请先在主数据中创建后重新上传",
    "MDM_IMPORT_INACTIVE_REFERENCE": "引用了已停用的主数据，请改用有效的主数据后重新上传",
    "MDM_IMPORT_PARENT_CYCLE": "上级客户存在循环引用，请修正后重新上传",
}

ISSUE_COPY: dict[str, str] = {**INFO_ISSUE_COPY, **WARNING_ISSUE_COPY, **ERROR_ISSUE_COPY}

FALLBACK_ISSUE_COPY = "请查看该行详情后处理"


def issue_copy(code: str) -> str:
    return ISSUE_COPY.get(code, FALLBACK_ISSUE_COPY)


# ---------------------------------------------------------------------------
# User-facing success / error copy (frozen)
# ---------------------------------------------------------------------------

COPY_UPLOAD_VALIDATED = "文件校验完成"
COPY_UPLOAD_FILE_REJECTED = "文件无法解析，请检查后重新上传"
COPY_UPLOAD_SERVER_ERROR = "上传失败，请稍后重试"
COPY_UPLOAD_NOT_XLSX = "请选择 .xlsx 格式的导入文件"

COPY_REVIEW_LOAD_ERROR = "加载失败，请稍后重试"
COPY_REVIEW_NOT_FOUND = "批次不存在或已删除"
COPY_ACKNOWLEDGE_SUCCESS = "确认完成"
COPY_ACKNOWLEDGE_STALE = "页面内容已更新，请刷新后重试"
COPY_ACKNOWLEDGE_ERROR = "确认失败，请稍后重试"
COPY_ERROR_FIRST_BLOCKED = "该批次存在需要修改的行，请修改 Excel 后重新上传"
COPY_ERROR_FIRST_NO_CONFIRM = "该批次没有需要确认的行"

COPY_COMMIT_NOT_READY = "批次尚未就绪，无法提交"
COPY_COMMIT_FORBIDDEN = "需要 MDM EDIT 权限才能提交"
COPY_COMMIT_SERVER_ERROR = "提交失败，请稍后重试"


def commit_success_banner(created: int, existing: int) -> str:
    return f"提交完成，共创建 {created} 个客户、{existing} 个已存在"


def acknowledge_success_banner(confirmed_count: int) -> str:
    return f"已确认 {confirmed_count} 项，可继续处理"

# ---------------------------------------------------------------------------
# Four Review tabs (四类 Review Tab 数据模型, frozen)
# ---------------------------------------------------------------------------


class TabName(str, enum.Enum):
    NEEDS_FIX = "needs_fix"          # 需要修改  (ERROR rows)
    NEEDS_CONFIRM = "needs_confirm"  # 需要确认  (WARNING rows)
    EXISTING = "existing"            # 已存在    (EXISTING rows)
    CREATE = "create"                # 可新增    (READY rows)


TAB_LABELS: dict[str, str] = {
    TabName.NEEDS_FIX.value: "需要修改",
    TabName.NEEDS_CONFIRM.value: "需要确认",
    TabName.EXISTING.value: "已存在",
    TabName.CREATE.value: "可新增",
}

# Engine row status -> the tab that owns it (COMMITTED/PENDING/VALID rows are
# not tab items: committed batches are rendered by the Commit result view).
TAB_STATUS_MAP: dict[str, str] = {
    "ERROR": TabName.NEEDS_FIX.value,
    "WARNING": TabName.NEEDS_CONFIRM.value,
    "EXISTING": TabName.EXISTING.value,
    "READY": TabName.CREATE.value,
}


class ReviewTabItem(BaseModel):
    """One row rendered inside a Review tab (pure API data + frozen copy).

    finding_ids is internal only (the acknowledge wire needs them); it is
    never rendered. existing is present only on the 已存在 tab.
    """

    model_config = ConfigDict(extra="forbid")

    row_number: int = Field(ge=1)
    status: str
    status_copy: str
    identity: dict[str, str]
    messages: list[str] = Field(default_factory=list)
    finding_ids: list[int] = Field(default_factory=list)
    existing: Optional[ExistingView] = None


class ReviewTabs(BaseModel):
    """The four Review tabs derived from one ReviewPayload (frozen)."""

    model_config = ConfigDict(extra="forbid")

    needs_fix: list[ReviewTabItem] = Field(default_factory=list)
    needs_confirm: list[ReviewTabItem] = Field(default_factory=list)
    existing: list[ReviewTabItem] = Field(default_factory=list)
    create: list[ReviewTabItem] = Field(default_factory=list)

    def count(self, tab: str) -> int:
        return len(getattr(self, tab))


def build_review_tabs(payload: ReviewPayload) -> ReviewTabs:
    """Partition ReviewPayload.rows into the four tabs by Engine row status.

    Pure UI derivation: no status/count logic is invented here, the Engine's
    stored row status is the single source of truth.
    """
    tabs: dict[str, list[ReviewTabItem]] = defaultdict(list)
    for row in payload.rows:
        tab = TAB_STATUS_MAP.get(row.status)
        if tab is None:
            continue
        messages = [issue_copy(finding.code) for finding in row.findings]
        tabs[tab].append(
            ReviewTabItem(
                row_number=row.row_number,
                status=row.status,
                status_copy=row_status_copy(row.status),
                identity=row.identity,
                messages=messages,
                finding_ids=[
                    finding.finding_id
                    for finding in row.findings
                    if finding.finding_id is not None
                    and severity_value(finding.code) == "WARNING"
                ],
                existing=row.existing if tab == TabName.EXISTING.value else None,
            )
        )
    return ReviewTabs(**{name: tabs.get(name, []) for name in TAB_LABELS})


# ---------------------------------------------------------------------------
# Error-first gate (frozen product rule)
# ---------------------------------------------------------------------------


class ErrorFirstGate(BaseModel):
    """Whether Warning acknowledgement is offered on the Review page.

    Frozen rule: while Batch ERROR > 0 the UI does not allow continuing with
    Warning acknowledgement and instructs the Operator to fix the workbook and
    re-upload. Only ERROR == 0 opens the 需要确认 actions.
    """

    model_config = ConfigDict(extra="forbid")

    acknowledge_allowed: bool
    message: str


def error_first_gate(payload: ReviewPayload) -> ErrorFirstGate:
    if payload.counts.error > 0:
        return ErrorFirstGate(acknowledge_allowed=False, message=COPY_ERROR_FIRST_BLOCKED)
    return ErrorFirstGate(acknowledge_allowed=True, message="")


# ---------------------------------------------------------------------------
# Operator / Admin button rules (frozen permission matrix)
# ---------------------------------------------------------------------------

ROLE_OPERATOR = "operator"
ROLE_ADMIN = "admin"
UI_ROLES = (ROLE_OPERATOR, ROLE_ADMIN)

# Batch statuses under which Review/Acknowledge make sense (V1 reachable set).
REVIEWABLE_STATUSES = ("REVIEW", "READY_TO_COMMIT")


def can_upload(role: str) -> bool:
    return role in UI_ROLES


def can_review(role: str) -> bool:
    return role in UI_ROLES


def can_acknowledge(role: str, payload: ReviewPayload) -> bool:
    if role not in UI_ROLES:
        return False
    if payload.status not in REVIEWABLE_STATUSES:
        return False
    return error_first_gate(payload).acknowledge_allowed


def can_commit(role: str, payload: ReviewPayload) -> bool:
    """Admin-only, and only for a READY_TO_COMMIT Batch."""
    return role == ROLE_ADMIN and payload.status == "READY_TO_COMMIT"


class PermissionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    can_upload: bool
    can_review: bool
    can_acknowledge: bool
    can_commit: bool


def permission_view(role: str, payload: Optional[ReviewPayload] = None) -> PermissionView:
    if payload is None:
        return PermissionView(
            can_upload=can_upload(role),
            can_review=can_review(role),
            can_acknowledge=False,
            can_commit=False,
        )
    return PermissionView(
        can_upload=can_upload(role),
        can_review=can_review(role),
        can_acknowledge=can_acknowledge(role, payload),
        can_commit=can_commit(role, payload),
    )


# ---------------------------------------------------------------------------
# Acknowledge / Commit request builders (wire helpers, internal values only)
# ---------------------------------------------------------------------------


def build_acknowledge_request(
    payload: ReviewPayload, finding_ids: list[int]
) -> dict[str, Any]:
    """Wire body for one acknowledge call.

    review_version is carried internally (never rendered) exactly as the API
    contract requires; reason stays optional and is omitted here.
    """
    return {"review_version": payload.review_version, "finding_ids": finding_ids}


class CommitResultRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_number: int = Field(ge=1)
    identity: dict[str, str]
    outcome: str  # 已创建 | 已存在


class CommitResultView(BaseModel):
    """Admin Commit result page data (frozen).

    Built from the commit response's result_summary plus the post-commit
    Review payload (row status COMMITTED -> 已创建, EXISTING -> 已存在).
    """

    model_config = ConfigDict(extra="forbid")

    created: int
    existing: int
    total: int
    banner: str
    rows: list[CommitResultRow] = Field(default_factory=list)


def build_commit_result(result_summary: dict[str, Any], payload: ReviewPayload) -> CommitResultView:
    created = int(result_summary.get("created") or 0)
    existing = int(result_summary.get("existing") or 0)
    rows = [
        CommitResultRow(
            row_number=row.row_number,
            identity=row.identity,
            outcome="已创建" if row.status == "COMMITTED" else "已存在",
        )
        for row in payload.rows
        if row.status in ("COMMITTED", "EXISTING")
    ]
    return CommitResultView(
        created=created,
        existing=existing,
        total=int(result_summary.get("total") or 0),
        banner=commit_success_banner(created, existing),
        rows=rows,
    )


# ---------------------------------------------------------------------------
# Frozen vocabulary completeness (used by the UI contract tests)
# ---------------------------------------------------------------------------

# Every frozen issue code must have user-facing copy so the UI never renders a
# raw code: ERROR/FILE codes -> 需要修改 guidance, WARNING/DECIDE -> business
# action text, INFO/EXISTING -> informational copy.
MISSING_ISSUE_COPY: set[str] = {
    code for code in FINDING_SPECS if code not in ISSUE_COPY
}
