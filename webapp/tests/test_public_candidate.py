"""Fresh public contracts. MySQL is mandatory, with no SQLite fallback."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text, UniqueConstraint, CheckConstraint
from sqlalchemy.exc import IntegrityError, DBAPIError
from demo.initialize_accounts import initialize_accounts, DEMO_PASSWORD
from demo.generate_data import generate
from demo.seed import ensure_demo_target
from webapp.mdm.database import get_engine, get_session_factory

ROOT=Path(__file__).resolve().parents[2]

class PublicCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_demo_target()
        from webapp.main import app
        cls.client=TestClient(app)
        cls.factory=get_session_factory()
        cls.engine=get_engine()
        cls.tmp=tempfile.TemporaryDirectory(prefix='public-tests-')
        cls.data=generate(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.client.close();cls.tmp.cleanup()

    def login(self,username='demo_admin'):
        r=self.client.post('/api/auth/login',json={'username':username,'password':DEMO_PASSWORD})
        self.assertEqual(r.status_code,200,r.text)
        return {'Authorization':'Bearer '+r.json()['token']}

    def test_empty_sqlite_accounts(self):
        db=Path(self.tmp.name)/'empty-accounts.db'
        initialize_accounts(db)
        with sqlite3.connect(db) as c:
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables-{'sqlite_sequence'},{'users','user_permissions','account_schema_migration','operation_log'})
            self.assertEqual(c.execute('SELECT COUNT(*) FROM users').fetchone()[0],3)
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_demo_permission_initialization_and_persistence(self):
        from webapp.auth.account_store import migrate_account_db, permissions_for, replace_permissions
        from webapp.auth.permissions import ACCOUNT_PERMISSION_MIGRATION
        expected = {
            'demo_admin': dict(mdm='EDIT', sales_actual='EDIT', ordering='EDIT', user_management='EDIT'),
            'demo_operator': dict(mdm='EDIT', sales_actual='EDIT', ordering='EDIT', user_management='NONE'),
            'demo_viewer': dict(mdm='VIEW', sales_actual='VIEW', ordering='VIEW', user_management='NONE'),
        }
        path = Path(self.tmp.name) / 'permission-persistence' / 'accounts.db'

        def seed_permissions(conn, *args):
            self.assertIsNotNone(conn.execute(
                'SELECT 1 FROM account_schema_migration WHERE version=?',
                (ACCOUNT_PERMISSION_MIGRATION,),
            ).fetchone())
            return replace_permissions(conn, *args)

        with patch('demo.initialize_accounts.migrate_account_db', wraps=migrate_account_db) as migration, \
                patch('demo.initialize_accounts.replace_permissions', side_effect=seed_permissions):
            initialize_accounts(path)
            migration.assert_called_once()

        def stored_permissions():
            with sqlite3.connect(path) as conn:
                return {name: permissions_for(conn, uid)
                        for uid, name in conn.execute('SELECT id, username FROM users')}

        self.assertEqual(stored_permissions(), expected)
        # Separate processes exercise real import-time initialization and restart.
        script = """
import json
from fastapi.testclient import TestClient
from webapp.main import app
from demo.initialize_accounts import DEMO_PASSWORD
with TestClient(app) as client:
    permissions = {}
    for username in ('demo_admin', 'demo_operator', 'demo_viewer'):
        response = client.post('/api/auth/login', json={'username': username, 'password': DEMO_PASSWORD})
        assert response.status_code == 200, response.text
        body = response.json()
        permissions[username] = body['user']['permissions']
        assert client.get('/').status_code == 200
        if username == 'demo_viewer':
            denied = client.post('/api/v1/ordering/cycles',
                headers={'Authorization': 'Bearer ' + body['token']},
                json={'cycle_month': '2000-03-01'})
            assert denied.status_code == 403, denied.text
    print(json.dumps(permissions))
"""
        env = {**os.environ, 'CCGTOOLS_ACCOUNT_DB_PATH': str(path), 'PYTHONDONTWRITEBYTECODE': '1'}
        for stage in ['first startup/login', 'application restart/login']:
            with self.subTest(stage=stage):
                result = subprocess.run([sys.executable, '-c', script], cwd=ROOT, env=env,
                                        capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(result.stdout), expected)
                self.assertEqual(stored_permissions(), expected)
        with sqlite3.connect(path) as conn:
            self.assertFalse(migrate_account_db(conn))
        self.assertEqual(stored_permissions(), expected)
        initialize_accounts(path)
        self.assertEqual(stored_permissions(), expected)
        # An explicit post-migration edit must survive migration and seed rechecks.
        with sqlite3.connect(path) as conn:
            uid = conn.execute("SELECT id FROM users WHERE username='demo_operator'").fetchone()[0]
            edited = {**expected['demo_operator'], 'mdm': 'VIEW'}
            replace_permissions(conn, uid, edited, uid)
        with sqlite3.connect(path) as conn:
            self.assertFalse(migrate_account_db(conn))
        initialize_accounts(path)
        self.assertEqual(stored_permissions()['demo_operator'], edited)

    def test_public_baseline_graph_and_full_schema(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from webapp.mdm.models import Base
        from webapp.sales import models
        from webapp.ordering import models as ordering
        script=ScriptDirectory.from_config(Config(str(ROOT/'webapp/mdm/alembic.ini')))
        self.assertEqual(script.get_heads(),['0001_public_baseline'])
        self.assertIsNone(script.get_revision('0001_public_baseline').down_revision)
        self.assertEqual(len(list(script.walk_revisions())),1)
        inspector=inspect(self.engine)
        cname=lambda c:self.engine.dialect.identifier_preparer.format_constraint(c).strip('`')
        self.assertEqual(set(inspector.get_table_names())-{'alembic_version'},set(Base.metadata.tables))
        for table in Base.metadata.tables.values():
            with self.subTest(table=table.name):
                self.assertEqual({c['name'] for c in inspector.get_columns(table.name)},set(table.columns.keys()))
                expected={(cname(fk),tuple(fk.column_keys),fk.referred_table.name,tuple(e.column.name for e in fk.elements)) for fk in table.foreign_key_constraints}
                actual={(fk['name'],tuple(fk['constrained_columns']),fk['referred_table'],tuple(fk['referred_columns'])) for fk in inspector.get_foreign_keys(table.name)}
                self.assertEqual(actual,expected)
                indexes={i['name'] for i in inspector.get_indexes(table.name)}
                self.assertTrue({i.name for i in table.indexes}<=indexes)
                self.assertEqual({u['name'] for u in inspector.get_unique_constraints(table.name)},
                    {cname(u) for u in table.constraints if isinstance(u,UniqueConstraint)} | {i.name for i in table.indexes if i.unique})
                self.assertEqual({c['name'] for c in inspector.get_check_constraints(table.name)},
                    {c.name for c in table.constraints if isinstance(c,CheckConstraint)})
        from webapp.ordering.schema_verifier import verify
        result=verify();self.assertEqual(result['head'],'0001_public_baseline')
        self.assertEqual(result['tables'],19);self.assertEqual(result['triggers'],68)

    def test_login_portal_logout_and_module_permissions(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/',follow_redirects=False).headers['location'],'/login')
        self.assertEqual(self.client.get('/login').status_code,200)
        self.assertEqual(self.client.post('/api/auth/login',json={'username':'demo_admin','password':'invalid-demo-password'}).status_code,401)
        headers=self.login()
        self.assertEqual(self.client.get('/').status_code,200)
        for p in ['/mdm','/mdm/import-center','/sales/actual','/ordering','/admin/users']:
            self.assertEqual(self.client.get(p).status_code,200,p)
        for p in ['/workbench','/admin','/reports']:
            self.assertEqual(self.client.get(p).status_code,404)
        self.login('demo_viewer')
        viewer=self.login('demo_viewer')
        self.assertEqual(self.client.get('/api/admin/users',headers=viewer).status_code,403)
        self.assertEqual(self.client.post('/api/v1/ordering/cycles',headers=viewer,json={'cycle_month':'2000-03-01'}).status_code,403)
        self.assertEqual(self.client.get('/api/v1/mdm/customers',headers=viewer).status_code,200)
        self.assertEqual(self.client.post('/api/auth/logout').status_code,204)
        self.assertEqual(self.client.get('/',follow_redirects=False).status_code,307)

    def test_none_module_permissions(self):
        headers=self.login()
        r=self.client.post('/api/admin/users',headers=headers,json={
            'username':'demo_none','display_name':'Demo No Access A','role':'operator',
            'password':DEMO_PASSWORD,'permissions':{'mdm':'NONE','sales_actual':'NONE','ordering':'NONE','user_management':'NONE'}})
        self.assertIn(r.status_code,[201,400],r.text)
        denied=self.login('demo_none')
        self.assertEqual(self.client.get('/').status_code,200)
        for path in ['/mdm','/sales/actual','/ordering']:
            self.assertEqual(self.client.get(path).status_code,403,path)
        for path in ['/api/v1/mdm/customers','/api/v1/sales/actual/dashboard/context','/api/v1/ordering/cycles']:
            self.assertEqual(self.client.get(path,headers=denied).status_code,403,path)
        self.client.cookies.clear()

    def test_cookie_attributes(self):
        for secure in [False,True]:
            with patch.dict(os.environ,{'CCGTOOLS_PORTAL_COOKIE_SECURE':str(secure).lower()}):
                r=self.client.post('/api/auth/login',json={'username':'demo_admin','password':DEMO_PASSWORD})
                cookie=r.headers['set-cookie']
                self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=lax',cookie)
                self.assertEqual('Secure' in cookie,secure)
        self.client.cookies.clear()

    def test_change_password_invalidates_token(self):
        admin=self.login()
        created=self.client.post('/api/admin/users',headers=admin,json={'username':'demo_password_test',
            'display_name':'Demo Password Tester','role':'operator','password':DEMO_PASSWORD})
        if created.status_code==400:
            users=self.client.get('/api/admin/users',headers=admin).json()
            uid=next(u['id'] for u in users if u['username']=='demo_password_test')
            self.client.post(f'/api/admin/users/{uid}/reset-password',headers=admin,json={'password':DEMO_PASSWORD})
        else:self.assertEqual(created.status_code,201,created.text)
        headers=self.login('demo_password_test')
        r=self.client.post('/api/auth/change-password',headers=headers,json={'old_password':DEMO_PASSWORD,'new_password':'demo-new-password-only'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.get('/api/auth/me',headers=headers).status_code,401)

    def test_import_center_customer_commit(self):
        headers=self.login()
        endpoint='/api/v1/mdm/customer-import/batches'
        with (self.data/'customers.xlsx').open('rb') as f:
            r=self.client.post(endpoint,headers=headers,files={'file':('customers.xlsx',f,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
        self.assertEqual(r.status_code,201,r.text)
        batch=r.json()['batch_id']
        review=self.client.get(endpoint+'/'+batch+'/review',headers=headers)
        self.assertEqual(review.status_code,200,review.text)
        payload=review.json()
        self.assertTrue(payload['commit_eligible'],payload)
        r=self.client.post(endpoint+'/'+batch+'/commit',headers=headers,json={'review_version':payload['review_version']})
        self.assertEqual(r.status_code,200,r.text)
        from webapp.mdm.models import Customer
        with self.factory() as s:
            self.assertIsNotNone(s.scalar(select(Customer).where(Customer.customer_code=='CUST-DEMO-002')))

    def test_sales_published_dashboard(self):
        headers=self.login()
        from webapp.sales.models import SalesImportBatch,SalesBatchStatus
        with self.factory() as session:
            batch=session.scalar(select(SalesImportBatch).where(SalesImportBatch.status==SalesBatchStatus.PUBLISHED))
            batch_id=batch.batch_id
        r=self.client.get('/api/v1/sales/actual/dashboard/summary',headers=headers,params={'batch_id':batch_id})
        self.assertEqual(r.status_code,200,r.text)
        from webapp.sales.models import SalesFact
        with self.factory() as s:
            facts=s.scalars(select(SalesFact)).all()
            self.assertTrue(facts);self.assertEqual(sum(f.actual_qty for f in facts),Decimal('17'))

    def test_inventory_actual_incoming_and_projection(self):
        from webapp.ordering.workbench import read_workbench
        from webapp.ordering.models import PlanningCycle, InventoryRaw, ActualRaw, IncomingSnapshotLine
        with self.factory() as s:
            cycle=s.scalar(select(PlanningCycle).where(PlanningCycle.cycle_code=='CYCLE-DEMO-001'))
            cycle_id=cycle.id
            for model in [InventoryRaw,ActualRaw,IncomingSnapshotLine]:
                self.assertIsNotNone(s.scalar(select(model).limit(1)))
        work=read_workbench(self.factory,cycle_id)
        self.assertEqual(work['baseline_state'],'Ready')
        row=work['rows'][0]
        self.assertEqual(Decimal(row['opening']),Decimal('41'))
        self.assertEqual(Decimal(row['incoming'][0]),Decimal('13'))
        self.assertEqual(row['channels'][0]['forecast'],['5.000','6.000','7.000','8.000'])

    def test_forecast_order_revision_and_lock_triggers(self):
        from webapp.ordering.models import PlanningCycle,IncomingSnapshot,ForecastVersion,InventoryRaw,CycleSourceBinding,DatasetVersion
        from webapp.ordering.forecast_version import ForecastLineInput
        from webapp.ordering.workbench import save_forecast
        from webapp.ordering.final_order import write_final_order,confirm_final_order,read_final_order_history
        from webapp.ordering.planning_cycle_lock import lock_planning_cycle
        from sqlalchemy import func
        with self.factory() as s,s.begin():
            number=s.scalar(select(func.count()).select_from(PlanningCycle))+1
            cycle_code=f'CYCLE-DEMO-LOCK-{number}'
            cycle=PlanningCycle(cycle_code=cycle_code,cycle_month=date(2000,4,1),created_by='demo_admin')
            s.add(cycle);s.flush()
            cycle_id=cycle.id
            for version in s.scalars(select(DatasetVersion)):
                s.add(CycleSourceBinding(cycle_id=cycle_id,dataset_version_id=version.id,period_start=version.period_start,period_end=version.period_end,created_by='demo_admin'))
            incoming=s.scalar(select(IncomingSnapshot));inc_id=incoming.id
        forecast=save_forecast(self.factory,cycle_id,None,[ForecastLineInput('CHN_DEMO_A','PRD_DEMO_A',date(2000,4,1),Decimal('3'))],'demo_admin')
        write_final_order(self.factory,cycle_code=cycle_code,product_stable_id='PRD_DEMO_A',order_qty=Decimal('4'),updated_by='demo_admin')
        write_final_order(self.factory,cycle_code=cycle_code,product_stable_id='PRD_DEMO_A',order_qty=Decimal('6'),updated_by='demo_admin')
        history=read_final_order_history(self.factory,cycle_code=cycle_code,product_stable_id='PRD_DEMO_A')
        self.assertEqual(len(history),1)
        confirm_final_order(self.factory,cycle_code=cycle_code,product_stable_id='PRD_DEMO_A',confirmed_by='demo_admin')
        lock_planning_cycle(self.factory,cycle_code=cycle_code,incoming_snapshot_id=inc_id,forecast_version_id=forecast.forecast_version_id,locked_by='demo_admin')
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as c:c.execute(text('UPDATE ordering_planning_cycle SET cycle_code=:name WHERE id=:id'),{'name':'DEMO-FORBIDDEN','id':cycle_id})
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as c:c.execute(text('UPDATE ordering_inventory_raw SET warehouse_name=warehouse_name'))

    def test_public_route_contract(self):
        from demo.route_contract import contract
        self.assertEqual(json.loads((ROOT/'webapp/tests/fixtures/synthetic/public_route_contract.json').read_text()),contract())

    def test_synthetic_generation_reproducible_and_office_metadata(self):
        import zipfile
        from datetime import datetime
        a=Path(self.tmp.name)/'gen-a';b=Path(self.tmp.name)/'gen-b'
        for output,year in [(a,2001),(b,2002)]:
            with patch('openpyxl.writer.excel.datetime') as clock:
                clock.datetime.now.return_value=datetime(year,1,1)
                generate(output)
        self.assertEqual({p.name:p.read_bytes() for p in a.iterdir()},{p.name:p.read_bytes() for p in b.iterdir()})
        for p in a.glob('*.xlsx'):
            with zipfile.ZipFile(p) as z:
                core=z.read('docProps/core.xml').decode()
                self.assertIn('Synthetic Demo Generator',core)
                self.assertIn('2000-01-01T00:00:00Z',core)
                self.assertNotIn('2001-01-01T00:00:00Z',core)
                self.assertNotIn('2002-01-01T00:00:00Z',core)

    def test_security_scan(self):
        from demo.security_scan import scan
        self.assertEqual(scan()['findings'],[])

if __name__=='__main__':unittest.main()
