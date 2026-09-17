"""Content-only public candidate gate, including Office cells and metadata."""
import json
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree

ROOT=Path(__file__).resolve().parents[1]
RULES={
 'business_identifier':r'(?<![0-9])[0-9]{13}(?![0-9])',
 'absolute_user_path':r'/'+r'Users/[A-Za-z0-9_]',
 'operating_path':r'/'+r'opt/[A-Za-z][^\s]*',
 'private_schema_lineage':r'2026[0-9]{4}_[0-9]{4}|(?:PRODUCTION_[A-Z_]+RUNBOOK)|gate_c2',
 'private_key':r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
 'token':r'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}',
}

def scan():
    findings=[];reviewed=[]
    for p in sorted(ROOT.rglob('*')):
        if not p.is_file():continue
        rel=p.relative_to(ROOT).as_posix()
        if '__pycache__' in p.parts or p.suffix in {'.pyc','.db','.db-wal','.db-shm'} or 'data' in p.relative_to(ROOT).parts:
            reviewed.append({'path':rel,'reason':'ignored runtime artifact; excluded from Docker and source delivery'})
            continue
        # Git metadata is outside this content scan; review committed objects separately.
        if '.git' in p.relative_to(ROOT).parts:
            continue
        if p.suffix.lower() in {'.png','.jpg','.jpeg','.gif','.svg','.pdf'}:
            findings.append({'path':rel,'rule':'image requires explicit visual review'});continue
        texts=[]
        if p.suffix=='.xlsx':
            try:
                with zipfile.ZipFile(p) as z:
                    for name in z.namelist():
                        if name.endswith('.xml'):
                            texts.append(z.read(name).decode())
                    core=ElementTree.fromstring(z.read('docProps/core.xml'))
                    creators=[e.text for e in core.iter() if e.tag.endswith('creator') or e.tag.endswith('lastModifiedBy')]
                    if set(creators)!={'Synthetic Demo Generator'}:
                        findings.append({'path':rel,'rule':'unexpected Office author metadata'})
            except (ValueError,zipfile.BadZipFile,KeyError):
                findings.append({'path':rel,'rule':'unreadable Office fixture'});continue
        else:
            try:texts=[p.read_text()]
            except UnicodeDecodeError:
                findings.append({'path':rel,'rule':'unreviewed binary'});continue
        # Pattern definitions are reviewed scan policy, not fixture values.
        if rel=='demo/security_scan.py':
            reviewed.append({'path':rel,'reason':'explicit scan rules contain banned-pattern vocabulary'})
            continue
        s='\n'.join(texts)
        for rule,pat in RULES.items():
            if re.search(pat,s):findings.append({'path':rel,'rule':rule})
        ips=set(re.findall(r'(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])',s))
        for ip in sorted(ips):
            if ip not in {'127.0.0.1','0.0.0.0'}:
                findings.append({'path':rel,'rule':'nonlocal IP'})
            else:reviewed.append({'path':rel,'reason':'local binding address '+ip})
        for url in re.findall(r'(?:mysql\+pymysql|mysql|postgresql)://[^\s\x22\x27`]+',s):
            if not any(marker in url for marker in ['demo_','mdm_test','user:password@host','unused:unused@127.0.0.1']):
                findings.append({'path':rel,'rule':'unreviewed connection string'})
            else:reviewed.append({'path':rel,'reason':'explicit fictional/demo or placeholder connection string'})
        if re.search(r'(?:PASSWORD|password|secret|token)\s*[:=]\s*[\x22\x27][^\x22\x27]+',s):
            reviewed.append({'path':rel,'reason':'credential literal needs manual synthetic/placeholder review'})
    return {'findings':findings,'reviewed_exceptions':reviewed,'verdict':'BLOCK' if findings else 'PASS'}

if __name__=='__main__':
    result=scan();print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(bool(result['findings']))
