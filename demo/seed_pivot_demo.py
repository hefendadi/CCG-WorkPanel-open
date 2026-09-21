"""Rich deterministic synthetic demo dataset for the local ``mdm_test`` database.

Purpose: give the Sales Pivot Workbench (PIVOT-UX-DEMO-V3) enough non-null
business attributes that every catalog field can actually be analyzed, without
touching the Published-fact snapshot schema, Alembic, the public demo database or
any production system.

Safety contract
---------------
* refuses to run unless ``MDM_DATABASE_URL`` targets the isolated local
  ``mdm_test`` database on loopback;
* ``reset`` deletes only the synthetic Sales + MDM rows of that database;
* every value is obviously fictional (``Demo …``) and generated from a fixed
  seed, so two runs produce the same dataset;
* facts are written through the real Preview → Publish services, so no import,
  mapping or publish contract is bypassed.

Usage
-----
    python3 -m demo.seed_pivot_demo                 # reset + seed + canaries
    python3 -m demo.seed_pivot_demo --validate-only  # canaries on the current data
"""
from __future__ import annotations

import argparse
import calendar
import copy
import os
import random
import sys
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url

from webapp.mdm.database import get_database_url, get_session_factory
from webapp.mdm.models import (
    ChangeLog,
    Channel,
    Customer,
    EntityAlias,
    ExternalMapping,
    Product,
    Province,
    Region,
    SKU,
    SalesRep,
)
from webapp.sales.models import (
    SalesBatchStatus,
    SalesFact,
    SalesImportBatch,
    SalesImportCandidate,
    SalesImportRow,
    SalesMappingIssue,
    SalesPublishConfirmation,
)
from webapp.sales.preview_service import create_sales_preview
from webapp.sales.publish_service import publish_batch

DEMO_DB_NAME = "mdm_test"
SOURCE_SYSTEM = "DEMO_ERP"
RANDOM_SEED = 20000501

MONTHS = (date(2000, 5, 1), date(2000, 6, 1), date(2000, 7, 1))
ROWS_PER_MONTH = (300, 350, 450)
SHEET_NAME = "Demo Pivot Sales"
PRODUCT_TYPE = "DEMO_STANDARD"

# ---------------------------------------------------------------------------
# Synthetic reference vocabulary (all values are fictional demo labels)
# ---------------------------------------------------------------------------

BRANDS = ("Demo Brand A", "Demo Brand B", "Demo Brand C", "Demo Brand D", "Demo Brand E")

PRODUCT_GROUPS = ("膨化", "麦片", "薯类", "谷物", "礼包", "周边", "物料")
PRODUCT_FORMS = ("单品", "礼包", "物料包材", "周边产品", "组合装")
ORIGINS = ("中", "日", "泰", "韩", "混合")
CATEGORY_L1_TO_L2 = (
    ("薯类", ("薯条", "薯片")),
    ("麦片", ("水果麦片", "谷物麦片", "即食麦片")),
    ("膨化", ("玉米膨化", "小麦膨化")),
    ("谷物", ("早餐谷物", "营养谷物")),
    ("其他", ("礼包", "周边")),
)
CATEGORY_L3 = ("袋装", "盒装", "桶装", "礼盒", "组合装")
CATEGORY_L4 = ("40g", "60g", "75g", "100g", "150g", "200g", "400g", "500g")
CATEGORY_EXTRA = ("常规", "联名", "限定", "促销")
CASE_PACKS = (Decimal("6"), Decimal("8"), Decimal("12"), Decimal("24"), Decimal("30"))

REGIONS = tuple(f"Demo Region {index:02d}" for index in range(1, 6))
PROVINCES = tuple(f"Demo Province {index:02d}" for index in range(1, 13))

CHANNELS = tuple(
    (f"CHN-DEMO-{100 + index}", name, channel_type)
    for index, (name, channel_type) in enumerate(
        (
            ("Demo Channel Hyper", "DEMO_HYPER"),
            ("Demo Channel Super", "DEMO_SUPER"),
            ("Demo Channel BP", "DEMO_BP"),
            ("Demo Channel CVS", "DEMO_CVS"),
            ("Demo Channel EC", "DEMO_EC"),
            ("Demo Channel Wholesale", "DEMO_WHOLESALE"),
            ("Demo Channel NKA", "DEMO_NKA"),
            ("Demo Channel RKA", "DEMO_RKA"),
            ("Demo Channel Special", "DEMO_SPECIAL"),
        ),
        start=1,
    )
)

SALESREPS = tuple(
    (f"REP-DEMO-{100 + index}", f"Demo Rep {letter}")
    for index, letter in enumerate("ABCDEFGHI", start=1)
)

ORGANIZATIONS = ("营业本部", "电商本部", "特通本部", "国际业务部", "其他")
DEPARTMENTS = ("营业一部", "营业二部", "营业三部", "CEC")
BUSINESS_TYPES = ("B2B", "B2C")
MARKET_TYPES = ("EC", "REAL", "其他")
FORMAT_TYPES = ("NKA", "RKA", "EC", "CVS", "流通", "特渠")
CHANNEL_DETAILS = (
    "NKA-全国", "NKA-区域", "RKA-连锁", "CVS-便利",
    "EC-平台", "EC-自营", "流通-批发", "特渠-团购",
)

PRODUCT_COUNT = 14
SKU_PER_PRODUCT = 2
CUSTOMER_COUNT = 36

# Product tier drives both how often a product is sold and how large the quantity
# is, so the pivot ranking is visibly uneven instead of uniform noise.
PRODUCT_TIERS = (3, 3, 2, 2, 2, 1, 1, 3, 2, 1, 3, 2, 1, 1)
TIER_FREQUENCY = {3: 8, 2: 4, 1: 2}
TIER_QTY = {3: (30, 60), 2: (12, 30), 1: (3, 12)}


# ---------------------------------------------------------------------------
# Target guard
# ---------------------------------------------------------------------------

def ensure_isolated_target() -> None:
    """Refuse anything that is not the isolated local ``mdm_test`` demo database."""
    url = make_url(get_database_url())
    if os.environ.get("CCGTOOLS_DEMO_MODE") != "true":
        raise RuntimeError("Set CCGTOOLS_DEMO_MODE=true to seed synthetic demo data")
    if url.database != DEMO_DB_NAME:
        raise RuntimeError(
            f"refusing to touch database {url.database!r}: this seeder only writes "
            f"the isolated local demo database {DEMO_DB_NAME!r}"
        )
    if url.host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError(f"refusing non-loopback host: {url.host!r}")


# ---------------------------------------------------------------------------
# Deterministic reference data
# ---------------------------------------------------------------------------

def _deal(rng: random.Random, values, count: int) -> list:
    """Deterministically deal ``count`` values so every value is used at least once.

    Dealing shuffled decks (instead of ``index % len(values)``) keeps the
    attributes decorrelated: e.g. ``market_type = REAL`` must co-occur with every
    ``format_type``, otherwise the pivot Filter would look artificially narrow.
    """
    deck: list = []
    while len(deck) < count:
        block = list(values)
        rng.shuffle(block)
        deck.extend(block)
    return deck[:count]


def _build_sku_attributes(rng: random.Random, count: int) -> list:
    """Deterministic SKU attribute rows with a valid category_l1 -> l2 hierarchy."""
    l2_by_l1 = dict(CATEGORY_L1_TO_L2)
    decks = {
        "l1": _deal(rng, [l1 for l1, _ in CATEGORY_L1_TO_L2], count),
        "product_group": _deal(rng, PRODUCT_GROUPS, count),
        "product_form": _deal(rng, PRODUCT_FORMS, count),
        "origin": _deal(rng, ORIGINS, count),
        "category_l3": _deal(rng, CATEGORY_L3, count),
        "category_l4": _deal(rng, CATEGORY_L4, count),
        "category_extra": _deal(rng, CATEGORY_EXTRA, count),
        "case_pack": _deal(rng, CASE_PACKS, count),
    }
    attributes = []
    for index in range(count):
        l1 = decks["l1"][index]
        l2 = rng.choice(l2_by_l1[l1])
        l4 = decks["category_l4"][index]
        attributes.append({
            "product_group": decks["product_group"][index],
            "product_form": decks["product_form"][index],
            "origin": decks["origin"][index],
            "category_l1": l1,
            "category_l2": l2,
            "category_l3": decks["category_l3"][index],
            "category_l4": l4,
            "short_name": f"Demo {l2} {l4}",
            "category_extra": decks["category_extra"][index],
            "case_pack": decks["case_pack"][index],
        })
    return attributes


def reset_demo_rows(factory) -> dict:
    """Delete only this seeder's synthetic rows. Order respects the FK graph."""
    deleted = {}
    with factory() as session, session.begin():
        for model in (
            SalesFact,
            SalesImportCandidate,
            SalesMappingIssue,
            SalesPublishConfirmation,
            SalesImportRow,
            SalesImportBatch,
            Customer,
            SKU,
            Product,
            Channel,
            SalesRep,
            Province,
            Region,
            EntityAlias,
            ExternalMapping,
            ChangeLog,
        ):
            deleted[model.__tablename__] = session.execute(
                delete(model)
            ).rowcount or 0
    return deleted


def seed_masters(factory) -> dict:
    """Create the synthetic MDM masters the rich demo facts reference."""
    created = {}
    with factory() as session, session.begin():
        regions = []
        for index, name in enumerate(REGIONS, start=1):
            region = Region(
                stable_id=f"REG_DEMO_{index:02d}",
                region_code=f"REG-DEMO-{100 + index}",
                region_name=name,
            )
            session.add(region)
            regions.append(region)
        session.flush()

        provinces = []
        for index, name in enumerate(PROVINCES, start=1):
            province = Province(
                stable_id=f"PRV_DEMO_{index:02d}",
                province_code=f"PRV-DEMO-{100 + index}",
                province_name=name,
                region_id=regions[(index - 1) % len(regions)].id,
            )
            session.add(province)
            provinces.append(province)
        session.flush()

        channels = []
        for code, name, channel_type in CHANNELS:
            channel = Channel(
                stable_id=code.replace("-", "_"),
                channel_code=code,
                channel_name=name,
                channel_type=channel_type,
                sort_order=len(channels) + 1,
            )
            session.add(channel)
            channels.append(channel)
        session.flush()

        reps = []
        for index, (code, name) in enumerate(SALESREPS):
            rep = SalesRep(
                stable_id=code.replace("-", "_"),
                employee_code=code,
                salesrep_name=name,
                organization=ORGANIZATIONS[index % len(ORGANIZATIONS)],
                department=DEPARTMENTS[index % len(DEPARTMENTS)],
                region_id=regions[index % len(regions)].id,
            )
            session.add(rep)
            reps.append(rep)
        session.flush()

        products = []
        for index in range(PRODUCT_COUNT):
            product = Product(
                stable_id=f"PRD_DEMO_{100 + index}",
                product_code=f"PROD-DEMO-{100 + index}",
                product_name=f"Demo Product {index + 1:02d}",
                brand=BRANDS[index % len(BRANDS)],
            )
            session.add(product)
            products.append(product)
        session.flush()

        skus = []
        sku_attributes = _build_sku_attributes(random.Random(RANDOM_SEED + 1),
                                               PRODUCT_COUNT * SKU_PER_PRODUCT)
        for index in range(PRODUCT_COUNT * SKU_PER_PRODUCT):
            product = products[index // SKU_PER_PRODUCT]
            sku = SKU(
                stable_id=f"SKU_DEMO_{100 + index}",
                sku_code=f"SKU-DEMO-{100 + index}",
                sku_name=f"Demo SKU {index + 1:02d}",
                product_id=product.id,
                source_product_code=product.product_code,
                **sku_attributes[index],
            )
            session.add(sku)
            skus.append(sku)
        session.flush()

        customer_rng = random.Random(RANDOM_SEED + 2)
        decks = {
            "organization": _deal(customer_rng, ORGANIZATIONS, CUSTOMER_COUNT),
            "department": _deal(customer_rng, DEPARTMENTS, CUSTOMER_COUNT),
            "business_type": _deal(customer_rng, BUSINESS_TYPES, CUSTOMER_COUNT),
            "market_type": _deal(customer_rng, MARKET_TYPES, CUSTOMER_COUNT),
            "format_type": _deal(customer_rng, FORMAT_TYPES, CUSTOMER_COUNT),
            "channel_detail": _deal(customer_rng, CHANNEL_DETAILS, CUSTOMER_COUNT),
            "channel": _deal(customer_rng, channels, CUSTOMER_COUNT),
            "salesrep": _deal(customer_rng, reps, CUSTOMER_COUNT),
            "province": _deal(customer_rng, provinces, CUSTOMER_COUNT),
        }
        customers = []
        for index in range(CUSTOMER_COUNT):
            province = decks["province"][index]
            customer = Customer(
                stable_id=f"CUS_DEMO_{100 + index}",
                customer_code=f"CUST-DEMO-{100 + index}",
                customer_name=f"Demo Customer {index + 1:02d}",
                # Evenly dealt assignments: every Channel / SalesRep / Province is
                # used, no customer cluster dominates a single channel.
                channel_id=decks["channel"][index].id,
                salesrep_id=decks["salesrep"][index].id,
                province_id=province.id,
                region_id=province.region_id,
                organization=decks["organization"][index],
                department=decks["department"][index],
                business_type=decks["business_type"][index],
                market_type=decks["market_type"][index],
                format_type=decks["format_type"][index],
                channel_detail=decks["channel_detail"][index],
                is_direct=customer_rng.random() < 0.35,
                source_created_ym=f"{2500 + (index % 12):04d}",
            )
            session.add(customer)
            customers.append(customer)
        session.flush()

        created = {
            "regions": len(regions),
            "provinces": len(provinces),
            "channels": len(channels),
            "salesreps": len(reps),
            "products": len(products),
            "skus": len(skus),
            "customers": len(customers),
        }
    return created


def _build_fact_plan() -> dict:
    """Deterministic, weighted fact plan per month (no database access)."""
    rng = random.Random(RANDOM_SEED)
    product_tiers = {
        f"PROD-DEMO-{100 + index}": PRODUCT_TIERS[index] for index in range(PRODUCT_COUNT)
    }
    sku_codes = [f"SKU-DEMO-{100 + index}" for index in range(PRODUCT_COUNT * SKU_PER_PRODUCT)]
    customers = [f"Demo Customer {index + 1:02d}" for index in range(CUSTOMER_COUNT)]

    plan = {}
    for month, row_count in zip(MONTHS, ROWS_PER_MONTH):
        days_in_month = calendar.monthrange(month.year, month.month)[1]
        rows = []
        for index in range(row_count):
            product_code = rng.choices(
                list(product_tiers), weights=[TIER_FREQUENCY[product_tiers[code]] for code in product_tiers]
            )[0]
            product_index = int(product_code.rsplit("-", 1)[1]) - 100
            sku_code = sku_codes[product_index * SKU_PER_PRODUCT + rng.randrange(SKU_PER_PRODUCT)]
            customer_name = rng.choice(customers)
            low, high = TIER_QTY[product_tiers[product_code]]
            quantity = rng.randint(low, high)
            day = rng.randint(1, days_in_month)
            rows.append({
                "line_no": index + 1,
                "business_date": date(month.year, month.month, day),
                "document_no": f"DEMO-{month.strftime('%Y%m')}-{index + 1:05d}",
                "customer_name": customer_name,
                "sku_code": sku_code,
                "quantity": quantity,
            })
        plan[month] = rows
    return plan


def _write_workbook(path: Path, rows) -> Path:
    import openpyxl
    from webapp.sales.parser import REQUIRED_COLUMNS

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = SHEET_NAME
    sheet.append(list(REQUIRED_COLUMNS))
    for row in rows:
        sheet.append([
            row["line_no"],
            row["business_date"].isoformat(),
            row["document_no"],
            row["customer_name"],
            PRODUCT_TYPE,
            row["sku_code"],
            f"Demo SKU {int(row['sku_code'].rsplit('-', 1)[1]) - 100 + 1:02d}",
            row["quantity"],
        ])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    workbook.close()
    return path


def seed_facts(factory, plan) -> list:
    batches = []
    with tempfile.TemporaryDirectory(prefix="pivot-demo-") as tmp:
        for month in MONTHS:
            workbook_path = _write_workbook(
                Path(tmp) / f"pivot-demo-{month.strftime('%Y%m')}.xlsx", plan[month]
            )
            preview = create_sales_preview(factory, workbook_path)
            publish_batch(factory, preview.batch_id, user="demo_admin")
            batches.append(preview.batch_id)
    return batches


def database_counts(factory) -> dict:
    with factory() as session:
        return {
            "products": session.scalar(select(func.count()).select_from(Product)),
            "skus": session.scalar(select(func.count()).select_from(SKU)),
            "customers": session.scalar(select(func.count()).select_from(Customer)),
            "channels": session.scalar(select(func.count()).select_from(Channel)),
            "salesreps": session.scalar(select(func.count()).select_from(SalesRep)),
            "regions": session.scalar(select(func.count()).select_from(Region)),
            "provinces": session.scalar(select(func.count()).select_from(Province)),
            "sales_fact": session.scalar(select(func.count()).select_from(SalesFact)),
        }


def latest_current_batch(factory) -> SalesImportBatch:
    with factory() as session:
        return session.scalars(
            select(SalesImportBatch)
            .where(
                SalesImportBatch.source_system == SOURCE_SYSTEM,
                SalesImportBatch.status == SalesBatchStatus.PUBLISHED,
                SalesImportBatch.replaced_at.is_(None),
            )
            .order_by(SalesImportBatch.snapshot_month.desc())
        ).first()


# ---------------------------------------------------------------------------
# Pivot canaries
# ---------------------------------------------------------------------------

CANARIES = (
    ("一级分类 × 实际销量", dict(dimensions=["category_l1"])),
    ("部门 × Market Type × 营业 × 实际销量",
     dict(dimensions=["department", "market_type", "salesrep_current"])),
    ("Product（当前主档）× BP渠道 × 实际销量",
     dict(dimensions=["product_current", "bp_channel_snapshot"])),
    ("业态 × 实际销量（Market Type = REAL）",
     dict(dimensions=["format_type"],
          filters=[{"field": "market_type", "operator": "in", "values": ["REAL"]}])),
    ("ERP分组 × 实际销量（销售日期范围）",
     dict(dimensions=["product_group"],
          filters=[{"field": "sales_date", "operator": "between"}])),
)


def run_canaries(factory, batch_id: str, verbose: bool = True) -> list:
    from webapp.sales.pivot_catalog import METRIC_ACTUAL_SALES
    from webapp.sales.pivot_query_engine import run_query, validate_query

    results = []
    month_start = None
    with factory() as session:
        batch = session.scalars(
            select(SalesImportBatch).where(SalesImportBatch.batch_id == batch_id)
        ).first()
        month_start = batch.snapshot_month

    for label, body in CANARIES:
        payload = {"batch_id": batch_id, "metrics": [METRIC_ACTUAL_SALES]}
        payload.update(copy.deepcopy(body))
        for item in payload.get("filters", []):
            if item["operator"] == "between" and "values" not in item:
                first = month_start
                last = first + timedelta(days=14)
                item["values"] = [first.isoformat(), last.isoformat()]
        result = run_query(factory, validate_query(payload))
        values = [Decimal(item[METRIC_ACTUAL_SALES]) for item in result["items"]]
        cardinality = result["plan"]["join_cardinality"]
        checks = {
            "success": True,
            "rows": len(result["items"]),
            "more_than_one_row": len(result["items"]) > 1,
            "metric_varies": len(set(values)) > 1,
            "no_multiplication": bool(cardinality["no_multiplication"]),
            "base_rows": cardinality["base_rows"],
            "joined_rows": cardinality["joined_rows"],
        }
        entry = {"name": label, "dimensions": payload["dimensions"],
                 "filters": payload.get("filters", []), "checks": checks}
        results.append(entry)
        if verbose:
            verdict = "PASS" if all(
                checks[key] for key in ("more_than_one_row", "metric_varies", "no_multiplication")
            ) else "FAIL"
            print(f"CANARY {verdict}  {label}")
            print(f"        rows={checks['rows']} distinct_metric={len(set(values))} "
                  f"join={checks['base_rows']}->{checks['joined_rows']}")
    return results


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def seed(verbose: bool = True) -> dict:
    ensure_isolated_target()
    factory = get_session_factory()

    deleted = reset_demo_rows(factory)
    created = seed_masters(factory)
    plan = _build_fact_plan()
    batches = seed_facts(factory, plan)
    counts = database_counts(factory)

    with factory() as session:
        date_range = session.execute(
            select(func.min(SalesFact.sales_date), func.max(SalesFact.sales_date))
        ).one()

    info = {
        "database": make_url(get_database_url()).database,
        "source_system": SOURCE_SYSTEM,
        "random_seed": RANDOM_SEED,
        "deleted_rows": deleted,
        "created": created,
        "counts": counts,
        "batches": batches,
        "date_range": [date_range[0].isoformat(), date_range[1].isoformat()],
    }

    latest = latest_current_batch(factory)
    info["canaries"] = run_canaries(factory, latest.batch_id, verbose=verbose)
    info["latest_batch_id"] = latest.batch_id
    info["latest_snapshot_month"] = latest.snapshot_month.isoformat()

    canary_ok = all(
        all(entry["checks"][key] for key in ("more_than_one_row", "metric_varies", "no_multiplication"))
        for entry in info["canaries"]
    )
    info["canary_verdict"] = "PASS" if canary_ok else "FAIL"

    if verbose:
        print(f"PIVOT_DEMO_SEED={info['canary_verdict']}")
        print(f"  database: {info['database']}")
        print(f"  counts: {counts}")
        print(f"  date_range: {info['date_range']}")
        print(f"  latest_batch: {info['latest_batch_id']} ({info['latest_snapshot_month']})")
    return info


def validate_only(verbose: bool = True) -> dict:
    ensure_isolated_target()
    factory = get_session_factory()
    latest = latest_current_batch(factory)
    if latest is None:
        raise RuntimeError("no current Published demo batch to validate")
    canaries = run_canaries(factory, latest.batch_id, verbose=verbose)
    ok = all(
        all(entry["checks"][key] for key in ("more_than_one_row", "metric_varies", "no_multiplication"))
        for entry in canaries
    )
    return {"latest_batch_id": latest.batch_id, "canaries": canaries,
            "verdict": "PASS" if ok else "FAIL"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only", action="store_true",
                        help="run the pivot canaries against the current data")
    args = parser.parse_args(argv)
    try:
        if args.validate_only:
            info = validate_only()
            print(f"PIVOT_DEMO_VALIDATE={info['verdict']}")
            return 0 if info["verdict"] == "PASS" else 1
        info = seed()
        return 0 if info["canary_verdict"] == "PASS" else 1
    except Exception as exc:  # pragma: no cover - operational entry point
        print(f"SEED FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
