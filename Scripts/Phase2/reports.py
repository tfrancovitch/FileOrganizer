"""Standard Report catalog/runner."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .query import _resolve_value


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

    def effective_parameters(self,report_id,parameters=None,execution_time=None):
        """Caller-supplied values over catalog-declared defaults.

        A report that declares a default must be runnable with no caller input:
        unattended harnesses, saved-query runs and scheduled reports have nobody
        to prompt. The GUI still prompts, and anything it supplies wins here.

        A default may be a plain value or a value expression (e.g.
        {"kind":"relative_time","offset":{"years":-5}}), resolved with the same
        semantics the query engine uses so cutoffs stay relative to run time
        instead of drifting into meaninglessness.
        """
        effective=dict(parameters or {})
        now=execution_time or datetime.now(timezone.utc)
        for p in self.get(report_id).get("parameters") or []:
            name=p["name"]
            if effective.get(name) is not None or "default" not in p: continue
            default=p["default"]
            effective[name]=_resolve_value(default,effective,now) if isinstance(default,dict) and "kind" in default else default
        return effective

    def instantiate(self,report_id,scope=None,parameters=None,execution_time=None):
        r=self.get(report_id); q=json.loads(json.dumps(r["query_ast"]))
        if scope is not None: q["scope"]=scope
        effective=self.effective_parameters(report_id,parameters,execution_time)
        # Parameter roles used by P2.7 report templates.
        for p in r.get("parameters") or []:
            name=p["name"]
            if effective.get(name) is None: continue
            value=effective[name]
            if p.get("role")=="semantic_limit": q["semantic_limit"]=int(value)
        return q

    def run(self,engine,report_id,scope=None,parameters=None):
        # One execution_time for both calls so a relative default resolves once.
        now=datetime.now(timezone.utc)
        q=self.instantiate(report_id,scope,parameters,execution_time=now)
        effective=self.effective_parameters(report_id,parameters,execution_time=now)
        return engine.execute(q,parameters=effective,retain_kind="report")
