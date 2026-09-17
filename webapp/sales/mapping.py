"""Read-only Sales mapping. Results and snapshots are values, never ORM entities."""
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from webapp.mdm.import_engine.normalize import normalize_text
from webapp.mdm.models import SKU, Customer
from webapp.ordering.actual_sales_import import DatabaseActualSalesMdmResolver
from webapp.sales.models import MappingIssueType, ISSUE_SEVERITY, SalesRowStatus


class MappingDataError(ValueError):
    """Invalid raw input or inconsistent MDM relationships; do not infer repairs."""


# Row outcome for a blocked SKU whose normalized ERP code matches several MDM
# SKUs. Kept as a reason string: no new issue enum value and no MDM/ERP write.
SKU_AMBIGUOUS_REASON = "SKU_AMBIGUOUS"


def normalize_sku_code(value: str) -> str:
    """Deterministic ERP SKU code lookup key: Unicode NFKC, then strip edges.

    NFKC folds full-width forms (e.g. ``）`` U+FF09 -> ``)`` U+0029) so an ERP
    code typed with full-width parentheses can match the unique MDM code. Exact
    code equality is always tried first; this key is only a checked fallback.
    There is no fuzzy matching and no modification of ERP or MDM values.
    """
    return unicodedata.normalize("NFKC", value).strip()


@dataclass(frozen=True)
class MappingSnapshot:
    sku_id: int
    sku_code_snapshot: str
    sku_name_snapshot: str
    product_id: int
    product_code_snapshot: Optional[str]
    product_name_snapshot: str
    customer_id: Optional[int]
    customer_code_snapshot: Optional[str]
    customer_name_snapshot: Optional[str]
    channel_id_snapshot: Optional[int]
    channel_code_snapshot: Optional[str]
    channel_name_snapshot: Optional[str]
    salesrep_id_snapshot: Optional[int]
    salesrep_code_snapshot: Optional[str]
    salesrep_name_snapshot: Optional[str]


@dataclass(frozen=True)
class RowMappingResult:
    source_row_no: int
    status: SalesRowStatus
    reason: Optional[str]
    actual_qty: Decimal
    snapshot: Optional[MappingSnapshot]


@dataclass(frozen=True)
class MappingIssueResult:
    issue_type: MappingIssueType
    external_code: Optional[str]
    external_name: Optional[str]
    affected_row_count: int
    affected_qty: Decimal

    @property
    def severity(self):
        return ISSUE_SEVERITY[self.issue_type]


@dataclass(frozen=True)
class MappingSummary:
    raw_rows: int
    ready_rows: int
    skipped_rows: int
    kit_parent_skipped_rows: int
    nonpositive_qty_skipped_rows: int
    sku_not_found_rows: int
    product_not_assigned_rows: int
    customer_not_found_rows: int
    ready_qty: Decimal
    skipped_qty: Decimal
    unassigned_qty: Decimal


@dataclass(frozen=True)
class SalesMappingResult:
    rows: tuple[RowMappingResult, ...]
    issues: tuple[MappingIssueResult, ...]
    summary: MappingSummary


def map_sales_rows(session, raw_rows) -> SalesMappingResult:
    """Map one batch's parser-validated rows without writing any table.

    SKU resolution order is exact MDM sku_code first, then a deterministic
    NFKC + trimmed normalized code that must match exactly one MDM SKU. A
    normalized code shared by several MDM SKUs blocks the row with reason
    SKU_AMBIGUOUS instead of auto-selecting; truly absent codes stay
    SKU_NOT_FOUND. Both are aggregated as blocking SKU issues, so
    summary.sku_not_found_rows counts every row whose ERP SKU did not resolve.

    Customer resolution reuses Ordering's exact ERP name / unique MDM Customer
    rule. An unresolved name is the external_code when ERP supplies no customer
    code, so distinct missing ERP customers aggregate separately.
    """
    if any(getattr(obj, "__tablename__", "").startswith("mdm_")
           for obj in (*session.new, *session.dirty, *session.deleted)):
        raise MappingDataError("mapping requires a session without pending MDM changes")
    rows = tuple(raw_rows)
    if len({r.source_row_no for r in rows}) != len(rows):
        raise MappingDataError('expected unique physical row numbers within one batch')
    eligible = []
    outcomes = {}
    for row in rows:
        qty = row.actual_qty
        if row.source_product_type == 'KIT_PARENT':
            outcomes[row.source_row_no] = ('KIT_PARENT', None)
        elif qty is None or not Decimal(qty).is_finite():
            raise MappingDataError('mapping requires parser-validated quantity')
        elif qty <= 0:
            outcomes[row.source_row_no] = ('NONPOSITIVE_QTY', None)
        else:
            eligible.append(row)
    codes = {r.source_sku_code for r in eligible if r.source_sku_code is not None}
    grouped = {}

    def issue(kind, code, name, qty):
        key = (kind, code)
        previous = grouped.get(key)
        grouped[key] = MappingIssueResult(kind, code, previous.external_name if previous else name,
                                         (previous.affected_row_count if previous else 0) + 1,
                                         (previous.affected_qty if previous else Decimal(0)) + qty)

    # Prevent even an autoflush of caller-owned MDM changes during these reads.
    with session.no_autoflush:
        # Load the MDM SKU index once for the batch. Exact Python equality on the
        # stored sku_code is the primary key; a normalized (NFKC + trimmed) key is
        # the deterministic fallback when the raw ERP code is absent, and only
        # resolves when it names exactly one MDM SKU.
        skus = session.scalars(select(SKU).options(joinedload(SKU.product))).all() if codes else []
        sku_by_code = {sku.sku_code: sku for sku in skus}
        sku_by_normalized: dict[str, list] = {}
        for sku in skus:
            sku_by_normalized.setdefault(normalize_sku_code(sku.sku_code), []).append(sku)
        product_ready = []
        for row in eligible:
            sku = sku_by_code.get(row.source_sku_code) if row.source_sku_code is not None else None
            blocked_reason = None
            if sku is None and row.source_sku_code is not None:
                matches = sku_by_normalized.get(normalize_sku_code(row.source_sku_code), ())
                if len(matches) == 1:
                    sku = matches[0]
                elif len(matches) > 1:
                    # Multiple MDM SKUs share the normalized code: never auto-select.
                    blocked_reason = SKU_AMBIGUOUS_REASON
            if sku is None:
                kind = MappingIssueType.SKU_NOT_FOUND
            elif sku.product_id is None:
                kind = MappingIssueType.PRODUCT_NOT_ASSIGNED
            elif sku.product is None:
                raise MappingDataError('SKU references a missing MDM Product')
            else:
                product_ready.append((row, sku))
                continue
            issue(kind, row.source_sku_code, row.source_sku_name, row.actual_qty)
            outcomes[row.source_row_no] = ((blocked_reason or kind.value), None)
        names = {row.source_customer_name for row, _ in product_ready if row.source_customer_name}
        customer_matches = DatabaseActualSalesMdmResolver(session).resolve_customers(names)
        ids = {matches[0].customer_id for matches in customer_matches.values() if len(matches) == 1}
        customers = session.scalars(select(Customer).options(joinedload(Customer.channel), joinedload(Customer.salesrep)).where(Customer.id.in_(ids))).all() if ids else []
        customer_index = {customer.id: customer for customer in customers}
        for row, sku in product_ready:
            matches = customer_matches.get(normalize_text(row.source_customer_name), ())
            customer = customer_index.get(matches[0].customer_id) if len(matches) == 1 else None
            reason = None
            if customer is None:
                reason = MappingIssueType.CUSTOMER_NOT_FOUND.value
                issue(MappingIssueType.CUSTOMER_NOT_FOUND,
                      getattr(row, 'source_customer_code', None) or row.source_customer_name,
                      row.source_customer_name, row.actual_qty)
            elif customer.channel is None or customer.salesrep is None:
                raise MappingDataError('Customer references a missing MDM Channel or SalesRep')
            snapshot = MappingSnapshot(
                sku.id, sku.sku_code, sku.sku_name, sku.product_id,
                sku.product.product_code, sku.product.product_name,
                customer.id if customer else None,
                customer.customer_code if customer else None, customer.customer_name if customer else None,
                customer.channel_id if customer else None,
                customer.channel.channel_code if customer else None,
                customer.channel.channel_name if customer else None,
                customer.salesrep_id if customer else None,
                customer.salesrep.employee_code if customer else None,
                customer.salesrep.salesrep_name if customer else None,
            )
            outcomes[row.source_row_no] = (reason, snapshot)
    mapped = tuple(RowMappingResult(row.source_row_no,
                                   SalesRowStatus.READY if outcomes[row.source_row_no][1] else SalesRowStatus.SKIPPED,
                                   outcomes[row.source_row_no][0], row.actual_qty, outcomes[row.source_row_no][1]) for row in rows)
    count = lambda reason: sum(r.reason == reason for r in mapped)
    ready = tuple(r for r in mapped if r.status == SalesRowStatus.READY)
    skipped = tuple(r for r in mapped if r.status == SalesRowStatus.SKIPPED)
    sku_unmatched_rows = count('SKU_NOT_FOUND') + count(SKU_AMBIGUOUS_REASON)
    summary = MappingSummary(len(rows), len(ready), len(skipped), count('KIT_PARENT'), count('NONPOSITIVE_QTY'),
                             sku_unmatched_rows, count('PRODUCT_NOT_ASSIGNED'), count('CUSTOMER_NOT_FOUND'),
                             sum((r.actual_qty for r in ready), Decimal(0)),
                             sum((r.actual_qty for r in skipped), Decimal(0)),
                             sum((r.actual_qty for r in ready if r.snapshot.customer_id is None), Decimal(0)))
    return SalesMappingResult(mapped, tuple(grouped.values()), summary)
