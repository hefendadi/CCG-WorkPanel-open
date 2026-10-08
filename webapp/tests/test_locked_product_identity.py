"""MySQL regressions for the complete locked workbench product identity."""
from datetime import date, datetime
from decimal import Decimal
import unittest
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from demo.seed import ensure_demo_target
from webapp.mdm.database import get_engine
from webapp.mdm.models import Channel, Product, SKU
from webapp.ordering.models import (
    ActualChannelProductMonth, CycleProductSnapshot, CycleSourceBinding,
    DatasetType, DatasetVersion, FinalOrder, FinalOrderStatus, ForecastLine,
    ForecastVersion, IncomingSnapshot, IncomingSnapshotLine,
    InventoryProductMonth, PlanningCycle,
)
from webapp.ordering.planning_cycle_lock import lock_planning_cycle
from webapp.ordering.workbench import read_workbench


class LockedProductIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_demo_target()
        cls.engine = get_engine()
        if cls.engine.dialect.name != 'mysql':
            raise RuntimeError('Locked product identity regressions require MySQL')

    def setUp(self):
        # Core service commits release savepoints; rollback the outer transaction
        # after each test, including immutable source rows and locked snapshots.
        self.connection = self.engine.connect()
        self.transaction = self.connection.begin()
        self.addCleanup(self.connection.close)
        self.addCleanup(self.transaction.rollback)
        self.factory = sessionmaker(bind=self.connection, expire_on_commit=False,
                                   join_transaction_mode='create_savepoint')
        tag = uuid4().hex[:10]
        roles = ('actual', 'inventory', 'incoming', 'forecast', 'order',
                 'old_forecast', 'outside_period', 'unbound', 'unselected_incoming')
        self.products = {}
        jan, feb = date(2000, 1, 1), date(2000, 2, 1)
        with self.factory() as s, s.begin():
            channel = Channel(stable_id=f'CHN_{tag}', channel_code=f'DEMO-{tag}',
                              channel_name='Synthetic Identity Channel')
            s.add(channel)
            for index, role in enumerate(roles):
                product = Product(stable_id=f'PRD_{tag}_{index}',
                                  product_code=f'DEMO-{tag}-{index}',
                                  product_name=f'Synthetic {role} at lock')
                s.add(product)
                self.products[role] = product
            s.flush()
            actual = DatasetVersion(dataset_type=DatasetType.ACTUAL,
                version_key=f'DEMO-ACT-{tag}', version_label='Synthetic Actual',
                period_start=jan, period_end=feb, created_by='demo_admin')
            inventory = DatasetVersion(dataset_type=DatasetType.INVENTORY,
                version_key=f'DEMO-INV-{tag}', version_label='Synthetic Inventory',
                period_start=jan, period_end=jan, created_by='demo_admin')
            unbound = DatasetVersion(dataset_type=DatasetType.ACTUAL,
                version_key=f'DEMO-UNBOUND-{tag}', version_label='Synthetic Unbound',
                period_start=jan, period_end=jan, created_by='demo_admin')
            incoming = IncomingSnapshot(snapshot_key=f'DEMO-INC-{tag}',
                snapshot_at=datetime(2000, 2, 1), source_name='synthetic incoming',
                created_by='demo_admin')
            other_incoming = IncomingSnapshot(snapshot_key=f'DEMO-OTHER-{tag}',
                snapshot_at=datetime(2000, 2, 1), source_name='synthetic unselected',
                created_by='demo_admin')
            s.add_all([actual, inventory, unbound, incoming, other_incoming])
            s.flush()
            cycle = PlanningCycle(cycle_code=f'DEMO-IDENTITY-{tag}', cycle_month=feb,
                                  incoming_snapshot_id=other_incoming.id,
                                  created_by='demo_admin')
            s.add(cycle)
            s.flush()
            self.cycle_id, self.cycle_code = cycle.id, cycle.cycle_code
            self.incoming_id = incoming.id
            for version in (actual, inventory):
                s.add(CycleSourceBinding(cycle_id=cycle.id, dataset_version_id=version.id,
                                        period_start=jan, period_end=jan,
                                        created_by='demo_admin'))
            for version, role, month in ((actual, 'actual', jan),
                                        (actual, 'outside_period', feb),
                                        (unbound, 'unbound', jan)):
                s.add(ActualChannelProductMonth(dataset_version_id=version.id,
                    channel_id=channel.id, product_id=self.products[role].id,
                    actual_month=month, shipped_qty=Decimal('1')))
            s.add(InventoryProductMonth(dataset_version_id=inventory.id,
                product_id=self.products['inventory'].id, inventory_month=jan,
                warehouse_scope='PLANNING_AVAILABLE', warehouse_policy_version='DEMO_V1',
                opening_qty=Decimal('2'), receipt_qty=Decimal('0'),
                issue_qty=Decimal('0'), ending_qty=Decimal('2')))
            for snapshot, role in ((incoming, 'incoming'),
                                   (other_incoming, 'unselected_incoming')):
                product = self.products[role]
                sku = SKU(stable_id=f'SKU_{tag}_{snapshot.id}',
                          sku_code=f'DEMO-SKU-{tag}-{snapshot.id}',
                          sku_name=f'Synthetic {role} SKU', product_id=product.id)
                s.add(sku)
                s.flush()
                s.add(IncomingSnapshotLine(snapshot_id=snapshot.id,
                    source_sheet_name='Synthetic', source_row_no=1,
                    source_sku_code=sku.sku_code, sku_id=sku.id, sku_stable_id=sku.stable_id,
                    product_id=product.id, product_stable_id=product.stable_id,
                    expected_arrival_date=feb, incoming_qty=Decimal('3'),
                    warehouse_entry_recorded=False, raw_payload={}))
            for number, role in ((1, 'old_forecast'), (2, 'forecast')):
                forecast = ForecastVersion(cycle_id=cycle.id, version_no=number,
                                           created_by='demo_admin')
                s.add(forecast)
                s.flush()
                s.add(ForecastLine(forecast_version_id=forecast.id, channel_id=channel.id,
                    product_id=self.products[role].id, forecast_month=feb,
                    forecast_qty=Decimal('4'), created_by='demo_admin'))
                if number == 1:
                    cycle.forecast_version_id = forecast.id
                else:
                    self.forecast_id = forecast.id
            s.add(FinalOrder(cycle_id=cycle.id, product_id=self.products['order'].id,
                order_qty=Decimal('5'), status=FinalOrderStatus.CONFIRMED,
                confirmed_by='demo_admin', confirmed_at=datetime(2000, 2, 1),
                created_by='demo_admin', updated_by='demo_admin'))

    def lock(self):
        return lock_planning_cycle(self.factory, cycle_code=self.cycle_code,
            incoming_snapshot_id=self.incoming_id, forecast_version_id=self.forecast_id,
            locked_by='demo_admin')

    def adopt_lock_sources(self):
        with self.factory() as s, s.begin():
            cycle = s.get(PlanningCycle, self.cycle_id)
            cycle.incoming_snapshot_id = self.incoming_id
            cycle.forecast_version_id = self.forecast_id

    def assert_identity(self, row, product):
        self.assertEqual((row['stable_id'], row['code'], row['name']),
                         (product.stable_id, product.product_code, product.product_name))

    def test_source_only_actual_inventory_incoming_preserved(self):
        self.adopt_lock_sources()
        before = {row['id']: row for row in read_workbench(self.factory, self.cycle_id)['rows']}
        self.lock()
        work = read_workbench(self.factory, self.cycle_id)
        after = {row['id']: row for row in work['rows']}
        self.assertEqual(set(after), set(before))
        for role in ('actual', 'inventory', 'incoming'):
            with self.subTest(source=role):
                product = self.products[role]
                self.assert_identity(before[product.id], product)
                self.assertIsNone(before[product.id]['final_order'])
                self.assertTrue(all(value is None for channel in before[product.id]['channels']
                                    for value in channel['forecast']))
                self.assert_identity(after[product.id], product)
        self.assertEqual(work['gaps'], [])
        self.assertFalse(work['save_allowed'])

    def test_mdm_rename_after_lock_keeps_historical_identity(self):
        self.lock()
        roles = ('actual', 'inventory', 'incoming', 'forecast', 'order')
        with self.factory() as s, s.begin():
            for role in roles:
                s.get(Product, self.products[role].id).product_name = f'Synthetic {role} renamed'
        after = {row['id']: row for row in read_workbench(self.factory, self.cycle_id)['rows']}
        for role in roles:
            with self.subTest(product=role):
                product = self.products[role]
                with self.factory() as s:
                    self.assertEqual(s.get(Product, product.id).product_name,
                                     f'Synthetic {role} renamed')
                self.assert_identity(after[product.id], product)

    def test_selected_sources_forecast_and_order_history(self):
        # Lock explicitly adopts sources different from the OPEN selection.
        self.lock()
        work = read_workbench(self.factory, self.cycle_id)
        rows = {row['id']: row for row in work['rows']}
        self.assertEqual(set(rows), {self.products[role].id for role in
                         ('actual', 'inventory', 'incoming', 'forecast', 'order')})
        self.assertEqual(work['incoming_snapshot']['id'], self.incoming_id)
        self.assertEqual(work['forecast_version']['id'], self.forecast_id)
        self.assert_identity(rows[self.products['forecast'].id], self.products['forecast'])
        self.assert_identity(rows[self.products['order'].id], self.products['order'])
        self.assertEqual(rows[self.products['forecast'].id]['channels'][0]['forecast'][0], '4.000')
        self.assertEqual(rows[self.products['order'].id]['final_order'], '5.000')
        self.assertEqual(rows[self.products['order'].id]['confirmation'], 'CONFIRMED')
        with self.factory() as s:
            snapshots = {p.product_id: p for p in s.scalars(select(CycleProductSnapshot).where(
                CycleProductSnapshot.cycle_id == self.cycle_id))}
        expected = set(rows) | {self.products['old_forecast'].id}
        self.assertEqual(set(snapshots), expected)
        for role in ('forecast', 'order', 'old_forecast'):
            with self.subTest(history=role):
                product = self.products[role]
                snapshot = snapshots[product.id]
                self.assertEqual((snapshot.product_stable_id, snapshot.product_code,
                                  snapshot.product_name),
                                 (product.stable_id, product.product_code, product.product_name))


if __name__ == '__main__':
    unittest.main()
