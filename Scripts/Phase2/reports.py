"""Standard Report catalog/runner."""
from __future__ import annotations

import json
from pathlib import Path


class ReportCatalog:
    def __init__(self, catalog_path=None):
        if catalog_path is None:
            catalog_path=Path(__file__).resolve().parents[2]/"Resources"/"Phase2"/"standard_report_catalog.json"
        self.path=Path(catalog_path)
        data=json.loads(self.path.read_text(encoding="utf-8"))
        self.version=data.get("catalog_version")
        self.reports=data.get("reports") or []
        self.by_id={r["report_id"]:r for r in self.reports}

    def list(self,family=None,tier=None,search=None):
        rows=self.reports
        if family: rows=[r for r in rows if r["family"]==family]
        if tier: rows=[r for r in rows if r["tier"]==tier]
        if search:
            s=search.lower(); rows=[r for r in rows if s in (r["title"]+" "+r.get("purpose","")).lower()]
        return rows

    def get(self,report_id):
        if report_id not in self.by_id: raise KeyError(report_id)
        return self.by_id[report_id]

    def instantiate(self,report_id,scope=None,parameters=None):
        r=self.get(report_id); q=json.loads(json.dumps(r["query_ast"]))
        if scope is not None: q["scope"]=scope
        # Parameter roles used by P2.7 report templates.
        for p in r.get("parameters") or []:
            name=p["name"]
            if name not in (parameters or {}): continue
            value=parameters[name]
            if p.get("role")=="semantic_limit": q["semantic_limit"]=int(value)
        return q

    def run(self,engine,report_id,scope=None,parameters=None):
        q=self.instantiate(report_id,scope,parameters)
        return engine.execute(q,parameters=parameters or {},retain_kind="report")
