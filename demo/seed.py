"""Seed the isolated public demo through supported imports and core services."""
import os
import tempfile
from pathlib import Path
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import select, func
from sqlalchemy.engine import make_url
from webapp.auth.account_store import DB_PATH
from webapp.mdm.database import get_session_factory, get_database_url
from webapp.mdm.models import Channel, SalesRep, Region, Province, Product, SKU, Customer
from demo.initialize_accounts import initialize_accounts
from demo.generate_data import generate

def ensure_demo_target():
    url=make_url(get_database_url())
    if os.environ.get('CCGTOOLS_DEMO_MODE') != 'true' or url.database != 'ccg_public_demo' or url.host not in {'mysql','127.0.0.1'}:
        raise RuntimeError('Seed/tests require explicit isolated public demo target')

def seed():
    ensure_demo_target()
    initialize_accounts(DB_PATH)
    factory=get_session_factory()
    with factory() as s:
        if s.scalar(select(func.count()).select_from(Channel)):
            if s.scalar(select(Channel).where(Channel.channel_code=='CHN-DEMO-001')) is None:
                raise RuntimeError('Database is not a synthetic public demo')
            from webapp.ordering.models import PlanningCycle, FinalOrder
            from webapp.sales.models import SalesFact
            cycle=s.scalar(select(PlanningCycle).where(PlanningCycle.cycle_code=='CYCLE-DEMO-001'))
            if cycle is None or cycle.forecast_version_id is None or not s.scalar(select(func.count()).select_from(SalesFact)) or not s.scalar(select(func.count()).select_from(FinalOrder)):
                raise RuntimeError('Incomplete synthetic seed; use scoped local reset, do not resume partial seed')
            print('Complete synthetic demo already initialized; scoped reset creates a clean seed')
            return
    with factory() as s, s.begin():
        channel=Channel(stable_id='CHN_DEMO_A',channel_code='CHN-DEMO-001',channel_name='Demo Channel A')
        rep=SalesRep(stable_id='REP_DEMO_A',employee_code='EMP-DEMO-001',salesrep_name='Demo Operator A')
        region=Region(stable_id='REG_DEMO_A',region_code='REG-DEMO-001',region_name='Demo Region A')
        s.add_all([channel,rep,region]);s.flush()
        province=Province(stable_id='PRV_DEMO_A',province_code='PRV-DEMO-001',province_name='Demo Province A',region_id=region.id)
        product=Product(stable_id='PRD_DEMO_A',product_code='PRODUCT-DEMO-001',product_name='Demo Product A',brand='Demo Brand A')
        s.add_all([province,product]);s.flush()
        sku=SKU(stable_id='SKU_DEMO_A',sku_code='SKU-DEMO-001',sku_name='Demo Product A',product_id=product.id,
                source_product_code=product.product_code,case_pack=Decimal('1'),category_extra='Demo Extra A')
        customer=Customer(stable_id='CUS_DEMO_A',customer_code='CUST-DEMO-001',customer_name='Demo Retail A',
                channel_id=channel.id,salesrep_id=rep.id,region_id=region.id,province_id=province.id)
        s.add_all([sku,customer])
    from webapp.sales.preview_service import create_sales_preview
    from webapp.sales.publish_service import publish_batch
    from webapp.ordering.inventory_import import preview_inventory_workbook, WarehousePlanningPolicy, WarehousePlanningStatus, DatabaseInventoryMdmResolver
    from webapp.ordering.inventory_commit import commit_inventory_preview, InventoryVersionRequest, inventory_source_checksum
    from webapp.ordering.actual_sales_import import preview_actual_sales_workbook, DatabaseActualSalesMdmResolver
    from webapp.ordering.actual_sales_commit import commit_actual_sales_preview, ActualSalesVersionRequest, actual_sales_source_checksum
    from webapp.ordering.incoming_import import preview_incoming_workbook, DatabaseIncomingMdmResolver
    from webapp.ordering.incoming_commit import commit_incoming_preview, IncomingSnapshotRequest, incoming_source_checksum
    from webapp.ordering.models import PlanningCycle, CycleSourceBinding, DatasetVersion
    from webapp.ordering.workbench import save_forecast, month_offset
    from webapp.ordering.forecast_version import ForecastLineInput
    from webapp.ordering.final_order import write_final_order
    with tempfile.TemporaryDirectory(prefix='public-demo-') as tmp:
        data=generate(Path(tmp))
        sales=create_sales_preview(factory,data/'sales.xlsx')
        publish_batch(factory,sales.batch_id,user='demo_admin')
        with factory() as s:
            inv=preview_inventory_workbook(data/'inventory-2000-01.xlsx',period_start=date(2000,1,1),period_end=date(2000,1,31),mdm_session=s,
                warehouse_policy=WarehousePlanningPolicy('DEMO_POLICY_V1',{'Demo Warehouse A':WarehousePlanningStatus.PLANNING_AVAILABLE}))
            act=preview_actual_sales_workbook(data/'actual.xlsx',mdm_session=s)
            inc=preview_incoming_workbook(data/'incoming.xlsx',mdm_session=s)
        inv_result=commit_inventory_preview(factory,inv,InventoryVersionRequest('INV-DEMO-001','Demo inventory',
            'inventory-2000-01.xlsx',inventory_source_checksum(data/'inventory-2000-01.xlsx'),'demo_admin'))
        commit_actual_sales_preview(factory,act,ActualSalesVersionRequest('ACT-DEMO-001','Demo actual',
            'actual.xlsx',actual_sales_source_checksum(data/'actual.xlsx'),'demo_admin'))
        incoming=commit_incoming_preview(factory,inc,IncomingSnapshotRequest('INCOMING-DEMO-001',datetime(2000,2,1),
            'incoming.xlsx',incoming_source_checksum(data/'incoming.xlsx'),'demo_admin'))
    with factory() as s,s.begin():
        cycle=PlanningCycle(cycle_code='CYCLE-DEMO-001',cycle_month=date(2000,2,1),created_by='demo_admin',incoming_snapshot_id=incoming.snapshot_id)
        s.add(cycle);s.flush();cycle_id=cycle.id
        for version in s.scalars(select(DatasetVersion)):
            s.add(CycleSourceBinding(cycle_id=cycle.id,dataset_version_id=version.id,
                period_start=version.period_start,period_end=version.period_end,created_by='demo_admin'))
    lines=[ForecastLineInput('CHN_DEMO_A','PRD_DEMO_A',month_offset(date(2000,2,1),i),Decimal(5+i)) for i in range(4)]
    save_forecast(factory,cycle_id,None,lines,'demo_admin')
    write_final_order(factory,cycle_code='CYCLE-DEMO-001',product_stable_id='PRD_DEMO_A',order_qty=Decimal(11),updated_by='demo_admin')
    print('SYNTHETIC_DEMO_SEED=PASS')

if __name__=='__main__': seed()
