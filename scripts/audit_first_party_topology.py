#!/usr/bin/env python3
"""Static first-party topology audit. Reports candidates; never deletes files."""
from __future__ import annotations
import ast, json, pathlib, re, sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
SCOPES=("core","bin","cli","gateway","api","browser","daemon","services","terminal","web-panel","tools","config")
files=[p for s in SCOPES if (ROOT/s).exists() for p in (ROOT/s).rglob("*") if p.is_file()]
py=[p for p in files if p.suffix==".py"]
refs={p:set() for p in py}
module_to_path={}
for p in py:
    rel=p.relative_to(ROOT).with_suffix("")
    module_to_path[".".join(rel.parts)]=p
for p in py:
    try: tree=ast.parse(p.read_text(encoding="utf-8",errors="replace"))
    except SyntaxError: continue
    for n in ast.walk(tree):
        names=[]
        if isinstance(n,ast.Import): names=[a.name for a in n.names]
        elif isinstance(n,ast.ImportFrom) and n.module: names=[n.module]
        for name in names:
            for mod,target in module_to_path.items():
                if name==mod or name.startswith(mod+".") or mod.startswith(name+"."):
                    if target!=p: refs[target].add(str(p.relative_to(ROOT)))
hardcodes=[]
for p in files:
    if p.stat().st_size>2_000_000: continue
    try: txt=p.read_text(encoding="utf-8",errors="replace")
    except Exception: continue
    if "/home/ser" in txt:
        hardcodes.append(str(p.relative_to(ROOT)))
candidates=[str(p.relative_to(ROOT)) for p in py if not refs[p] and p.name!="__init__.py"]
artifacts=[str(p.relative_to(ROOT)) for p in files if re.search(r"(\.bak(?:\.|$)|\.stub$|backup|_original\.html$|_pre_s\d+\.html$)",p.name,re.I)]
report={"python_files":len(py),"zero_import_reference_candidates":sorted(candidates),"historical_artifact_candidates":sorted(artifacts),"developer_path_files":sorted(hardcodes),"note":"Candidates are not proof of dead code; dynamic/CLI/service entrypoints require committee review."}
out=pathlib.Path(sys.argv[1] if len(sys.argv)>1 else "reports/first-party-topology.json")
out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,indent=2),encoding="utf-8")
print(json.dumps({k:(len(v) if isinstance(v,list) else v) for k,v in report.items()},indent=2))
