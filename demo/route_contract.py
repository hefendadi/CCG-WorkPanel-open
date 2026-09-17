"""Generate the public route contract from this candidate's actual app."""
import json
from pathlib import Path

def contract():
    from webapp.main import app
    return sorted([{'path':r.path,'methods':sorted(r.methods)} for r in app.routes
                   if getattr(r,'methods',None)],key=lambda r:(r['path'],r['methods']))

def generate(output=None):
    if output is None:
        output=Path(__file__).resolve().parents[1]/'webapp/tests/fixtures/synthetic/public_route_contract.json'
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(contract(),ensure_ascii=False,indent=2)+'\n')
    return output

if __name__=='__main__': generate()
