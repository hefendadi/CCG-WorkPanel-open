"""Reproducible fictional fixtures: no business source file is read."""
import argparse
import csv
import json
import re
from pathlib import Path
import zipfile
import openpyxl
from datetime import datetime

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / 'webapp/tests/fixtures/synthetic'

def workbook(path, sheet, headers, rows, header_row=1):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    wb.properties.creator = 'Synthetic Demo Generator'
    wb.properties.lastModifiedBy = 'Synthetic Demo Generator'
    wb.properties.created = wb.properties.modified = datetime(2000, 1, 1)
    for _ in range(header_row - 1): ws.append(['Synthetic demo — fictional data'])
    ws.append(list(headers))
    for row in rows: ws.append(list(row))
    wb.save(path)
    wb.close()
    # Normalize ZIP timestamps too, without deriving anything from external data.
    with zipfile.ZipFile(path) as original:
        entries = [(i.filename, original.read(i.filename)) for i in original.infolist()]
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as out:
        for name, payload in entries:
            if name == 'docProps/core.xml':
                # openpyxl updates modified during save; normalize it explicitly.
                payload=re.sub(rb'(<dcterms:modified\b[^>]*>)[^<]*(</dcterms:modified>)',
                               rb'\g<1>2000-01-01T00:00:00Z\g<2>',payload)
            out.writestr(zipfile.ZipInfo(name, (2000,1,1,0,0,0)), payload)

def generate(output=DEFAULT_OUTPUT):
    from webapp.mdm.customer_import.contract import CUSTOMER_TEMPLATE_COLUMNS, CUSTOMER_TEMPLATE_SHEET
    from webapp.sales.parser import REQUIRED_COLUMNS as SALES_COLUMNS
    from webapp.ordering.inventory_import import REQUIRED_COLUMNS as INVENTORY_COLUMNS
    from webapp.ordering.actual_sales_import import REQUIRED_COLUMNS as ACTUAL_COLUMNS
    from webapp.ordering.incoming_import import FIELD_ALIASES
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    customer = {'客户编码':'CUST-DEMO-002','客户名称':'Demo Retail B','组织':'Demo Organization A',
        '部门':'Demo Department A','Business Type':'DEMO_B2B','Market Type':'DEMO_RETAIL','渠道':'Demo Channel A',
        '销售地区':'Demo Region A','省份':'Demo Province A','业态':'Demo Format A',
        '渠道明细':'Demo Detail A','销售员':'Demo Operator A','是否直营':'是',
        '上级客户':None,'创建年月':'0001'}
    workbook(output/'customers.xlsx',CUSTOMER_TEMPLATE_SHEET,CUSTOMER_TEMPLATE_COLUMNS,
        [[customer.get(c) for c in CUSTOMER_TEMPLATE_COLUMNS]])
    sales_rows = [[1,'2000-02-15','DOC-DEMO-001','Demo Retail A','DEMO_STANDARD',
                   'SKU-DEMO-001','Demo Product A',17]]
    workbook(output/'sales.xlsx','Demo Sales',SALES_COLUMNS,sales_rows)
    inventory = {'物料编码':'SKU-DEMO-001','物料名称':'Demo Product A','仓库名称':'Demo Warehouse A',
        '生产日期':'2000-01-01','有效期至':'2001-01-01','批号':'LOT-DEMO-001','库存单位':'Pcs',
        '(期初)数量（库存）':30,'(收入)数量（库存）':20,'(发出)数量（库存）':9,'(结存)数量（库存）':41}
    workbook(output/'inventory-2000-01.xlsx','Demo Inventory',INVENTORY_COLUMNS,
        [[inventory[c] for c in INVENTORY_COLUMNS]],4)
    actual = {'日期':'2000-01-15','客户':'Demo Retail A','单据状态':'已审核','物料编码':'SKU-DEMO-001',
        '物料名称':'Demo Product A','实发数量':9,'仓库':'Demo Warehouse A','批号':'LOT-DEMO-001',
        '生产日期':'2000-01-01'}
    workbook(output/'actual.xlsx','Demo Actual',ACTUAL_COLUMNS,[[actual[c] for c in ACTUAL_COLUMNS]])
    incoming_headers = [v[0] for v in FIELD_ALIASES.values()]
    incoming = {'SKU编码':'SKU-DEMO-001','商品名':'Demo Product A','订单':'ORDER-DEMO-GROUP',
        '工厂':'Demo Factory A','发货港':'Demo Port A','目的港':'Demo Port B','订单号':'ORDER-DEMO-001',
        '入数':1,'订单数量':13,'出荷数量':13,'已出合计':13,'出荷差额':0,'批号':'LOT-DEMO-002',
        'ETD':'2000-02-01','ETA':'2000-02-20','备注':'Synthetic demo','船名/航次':'Demo Voyage A',
        '实际到港时间':None,'进仓时间':None,'免箱期截至时间':None}
    workbook(output/'incoming.xlsx','Demo Incoming',incoming_headers,
        [[incoming.get(c) for c in incoming_headers]])
    with (output/'demo-identities.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['customer_code','customer_name','sku_code','warehouse_name'])
        w.writerow(['CUST-DEMO-001','Demo Retail A','SKU-DEMO-001','Demo Warehouse A'])
    (output/'provenance.json').write_text(json.dumps({'synthetic':True,'generated_from':'fictional constants',
        'customers':['CUST-DEMO-001','CUST-DEMO-002'],'skus':['SKU-DEMO-001']},indent=2)+'\n')
    return output

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    generate(p.parse_args().output)
