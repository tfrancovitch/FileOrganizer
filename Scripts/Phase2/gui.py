"""Tkinter Hybrid Evidence Explorer for Phase 2 P2.9.1."""
from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox, simpledialog

from .core import connect, require_phase2_schema, project_info, stamp_core_version, utc_now
from .coverage import evidence_health
from .derived import ensure_duplicate_projection
from .fts import FtsManager, FtsUnavailable
from .query import QueryEngine, QueryError, QUERY_SCHEMA, SEMANTIC_CONTRACT
from .reports import ReportCatalog
from .saved import SavedQueryStore


def human_bytes(value):
    if value is None: return "Unknown"
    n=float(value)
    for unit in ("B","KB","MB","GB","TB"):
        if n < 1024 or unit=="TB": return f"{n:,.1f} {unit}" if unit!="B" else f"{int(n):,} B"
        n/=1024


def base_file_query(scope):
    return {
        "query_schema":QUERY_SCHEMA,
        "semantic_contract":SEMANTIC_CONTRACT,
        "subject":{"entity":"file","temporal":{"mode":"current"},"current_file_states":["present"]},
        "scope":scope,
    }


class Phase2App(tk.Tk):
    def __init__(self, project_dir):
        super().__init__()
        self.project_dir=Path(project_dir).resolve()
        self.conn=connect(self.project_dir,write=True)
        require_phase2_schema(self.conn)
        stamp_core_version(self.conn)
        self.store=SavedQueryStore(self.conn)
        self.fts=FtsManager(self.conn,self.project_dir)
        self.engine=QueryEngine(self.conn,saved_store=self.store,fts_manager=self.fts)
        self.reports=ReportCatalog()
        self.info=project_info(self.conn)
        self.scope={"kind":"project"}
        self.filters=[]
        self.search_text=""
        self.current_rows=[]
        self.current_query=None
        self.after_id=None

        self.title(f"The File Organizer — Understand — {self.info.get('name',self.project_dir.name)}")
        self.geometry("1280x820")
        self.minsize(1000,650)
        self.protocol("WM_DELETE_WINDOW",self.close)
        self._style()
        self._build_shell()
        self.refresh_evidence_strip()
        self.show_overview()

    def _style(self):
        style=ttk.Style(self)
        try: style.theme_use("vista")
        except tk.TclError: pass
        style.configure("Nav.TButton",anchor="w",padding=(12,9))
        style.configure("Title.TLabel",font=("Segoe UI",18,"bold"))
        style.configure("Sub.TLabel",foreground="#666")
        style.configure("Warn.TLabel",background="#fff4d6",foreground="#5c4300",padding=(10,7))
        style.configure("Good.TLabel",background="#e8f5ed",foreground="#1f6244",padding=(10,7))

    def _build_shell(self):
        self.columnconfigure(1,weight=1); self.rowconfigure(0,weight=1)
        nav=ttk.Frame(self,padding=8); nav.grid(row=0,column=0,sticky="nsew")
        ttk.Label(nav,text="The File Organizer",font=("Segoe UI",13,"bold")).pack(fill="x",pady=(4,16))
        for label,cmd in [
            ("Overview",self.show_overview),("Files",self.show_files),("Reports",self.show_reports),
            ("Saved Queries",self.show_saved),("Evidence",self.show_evidence),("History",self.show_history)]:
            ttk.Button(nav,text=label,style="Nav.TButton",command=cmd).pack(fill="x",pady=2)
        ttk.Label(nav,text="Read-only analytical mode\nNo source-file actions",foreground="#666",justify="left").pack(side="bottom",fill="x",pady=8)

        main=ttk.Frame(self); main.grid(row=0,column=1,sticky="nsew"); main.columnconfigure(0,weight=1); main.rowconfigure(2,weight=1)
        top=ttk.Frame(main,padding=(12,8)); top.grid(row=0,column=0,sticky="ew"); top.columnconfigure(2,weight=1)
        self.project_var=tk.StringVar(value=f"Project: {self.info.get('name',self.project_dir.name)}")
        ttk.Label(top,textvariable=self.project_var,font=("Segoe UI",10,"bold")).grid(row=0,column=0,sticky="w")
        self.scope_var=tk.StringVar(value="Scope: All Sources")
        ttk.Label(top,textvariable=self.scope_var,foreground="#666").grid(row=1,column=0,sticky="w")
        self.find_var=tk.StringVar()
        entry=ttk.Entry(top,textvariable=self.find_var); entry.grid(row=0,column=2,rowspan=2,sticky="ew",padx=12)
        entry.bind("<Return>",lambda e:self.apply_find())
        ttk.Button(top,text="Find",command=self.apply_find).grid(row=0,column=3,rowspan=2,padx=(0,6))
        ttk.Button(top,text="+ Filter",command=self.add_filter).grid(row=0,column=4,rowspan=2,padx=(0,6))
        ttk.Button(top,text="Save Query",command=self.save_current_query).grid(row=0,column=5,rowspan=2)

        self.evidence_var=tk.StringVar()
        self.evidence_label=ttk.Label(main,textvariable=self.evidence_var,style="Warn.TLabel")
        self.evidence_label.grid(row=1,column=0,sticky="ew")
        self.content=ttk.Frame(main,padding=12); self.content.grid(row=2,column=0,sticky="nsew")

    def clear(self):
        for w in self.content.winfo_children(): w.destroy()
        self.content.columnconfigure(0,weight=0); self.content.rowconfigure(0,weight=0)

    def header(self,title,subtitle=None):
        ttk.Label(self.content,text=title,style="Title.TLabel").pack(anchor="w")
        if subtitle: ttk.Label(self.content,text=subtitle,style="Sub.TLabel").pack(anchor="w",pady=(2,12))

    def refresh_evidence_strip(self):
        h=evidence_health(self.conn)
        if h["coverage"]=="complete" and not h["warnings"]:
            self.evidence_label.configure(style="Good.TLabel"); self.evidence_var.set("Coverage complete · Current project evidence")
        else:
            self.evidence_label.configure(style="Warn.TLabel")
            parts=["Coverage "+h["coverage"]]
            if h["stale_hashes"]: parts.append(f"{h['stale_hashes']:,} stale hashes")
            if h["current_analyzer_failures"]: parts.append(f"{h['current_analyzer_failures']:,} analyzer failures")
            self.evidence_var.set(" · ".join(parts))

    def show_overview(self):
        self.clear(); self.header("Overview","A few high-value lenses on the current scope.")
        body=ttk.Frame(self.content); body.pack(fill="both",expand=True)
        try:
            count=self.conn.execute("SELECT COUNT(*) FROM file_state WHERE project_id=1 AND state='present'").fetchone()[0]
            bytes_=self.conn.execute("SELECT COALESCE(SUM(size_bytes),0) FROM file_state WHERE project_id=1 AND state='present'").fetchone()[0]
            ensure_duplicate_projection(self.conn)
            dup=self.conn.execute("SELECT COUNT(*),COALESCE(SUM(reclaimable_bytes),0) FROM p2_current_duplicate_summary").fetchone()
            health=evidence_health(self.conn)
        except Exception as exc:
            messagebox.showerror("Overview",str(exc)); return
        cards=ttk.Frame(body); cards.pack(fill="x",pady=(0,12))
        for title,big,small,cmd in [
            ("Current File Locations",f"{count:,}",human_bytes(bytes_),self.show_files),
            ("Exact Duplicate Groups",f"{dup[0]:,}",f"{human_bytes(dup[1])} potentially reclaimable",lambda:self.run_report("DUP-001")),
            ("Evidence Health",health["coverage"].title(),f"{len(health['warnings'])} material warning(s)",self.show_evidence),
        ]:
            f=ttk.LabelFrame(cards,text=title,padding=12); f.pack(side="left",fill="both",expand=True,padx=(0,8))
            ttk.Label(f,text=big,font=("Segoe UI",18,"bold")).pack(anchor="w"); ttk.Label(f,text=small,foreground="#666").pack(anchor="w")
            ttk.Button(f,text="Open",command=cmd).pack(anchor="e",pady=(8,0))
        rpt=ttk.Frame(body); rpt.pack(fill="both",expand=True)
        ttk.Label(rpt,text="Quick reports",font=("Segoe UI",11,"bold")).pack(anchor="w",pady=(4,8))
        for rid in ("STO-003","STO-002","STO-001","QUAL-001","QUAL-004"):
            r=self.reports.get(rid)
            row=ttk.Frame(rpt); row.pack(fill="x",pady=2)
            ttk.Button(row,text=r["title"],command=lambda x=rid:self.run_report(x)).pack(side="left")
            ttk.Label(row,text=r["purpose"],foreground="#666").pack(side="left",padx=8)

    def _scope_label(self):
        if self.scope["kind"]=="project": return "All Sources"
        if self.scope["kind"]=="source_roots": return "Selected Source Root"
        if self.scope["kind"]=="folder_subtrees": return self.scope["folders"][0].get("display_name") or self.scope["folders"][0].get("relative_path_key") or "Root"
        return self.scope["kind"]

    def show_files(self,reset_cursor=True):
        if reset_cursor: self.after_id=None
        self.clear(); self.header("Files","Browse current File Locations. Scope and filters are part of the analytical question.")
        tools=ttk.Frame(self.content); tools.pack(fill="x",pady=(0,8))
        ttk.Label(tools,textvariable=self.scope_var,font=("Segoe UI",9,"bold")).pack(side="left")
        if self.filters: ttk.Label(tools,text=f" · {len(self.filters)} active filter(s)",foreground="#445").pack(side="left")
        ttk.Button(tools,text="Clear Query",command=self.clear_query).pack(side="right")

        panes=ttk.Panedwindow(self.content,orient="horizontal"); panes.pack(fill="both",expand=True)
        left=ttk.Frame(panes,padding=6); center=ttk.Frame(panes); right=ttk.Frame(panes,padding=8)
        panes.add(left,weight=1); panes.add(center,weight=4); panes.add(right,weight=2)
        self._populate_scope_tree(left)
        self._populate_file_table(center)
        self.detail_host=right
        ttk.Label(right,text="Details & Evidence",font=("Segoe UI",10,"bold")).pack(anchor="w")
        ttk.Label(right,text="Select a File Location.",foreground="#666").pack(anchor="w",pady=8)

    def _populate_scope_tree(self,parent):
        ttk.Label(parent,text="Folders / Source Roots",font=("Segoe UI",9,"bold")).pack(anchor="w",pady=(0,5))
        tree=ttk.Treeview(parent,show="tree",height=25); tree.pack(fill="both",expand=True)
        allid=tree.insert("","end",text="All Sources",values=("project",))
        roots=self.conn.execute("SELECT source_root_id,root_path FROM source_root WHERE project_id=1 AND is_active=1 ORDER BY root_ordinal,root_path").fetchall()
        for root in roots:
            rid=tree.insert(allid,"end",text=root["root_path"],values=(f"root:{root['source_root_id']}",))
            tops=self.conn.execute(
                "SELECT DISTINCT fo_top_level(fp.relative_path) top FROM file_state fs JOIN file_path fp ON fp.file_path_id=fs.file_path_id WHERE fs.state='present' AND fs.source_root_id=? AND fo_top_level(fp.relative_path)<>'' ORDER BY lower(top) LIMIT 500",
                (root["source_root_id"],)).fetchall()
            for t in tops: tree.insert(rid,"end",text=t["top"],values=(f"folder:{root['source_root_id']}:{t['top']}",))
        tree.item(allid,open=True)
        def selected(_=None):
            sel=tree.selection()
            if not sel:return
            val=tree.item(sel[0],"values")
            if not val:return
            token=val[0]
            if token=="project": self.scope={"kind":"project"}
            elif token.startswith("root:"): self.scope={"kind":"source_roots","source_root_ids":[int(token.split(':')[1])]}
            elif token.startswith("folder:"):
                _,root,key=token.split(":",2); self.scope={"kind":"folder_subtrees","folders":[{"source_root_id":int(root),"relative_path_key":key,"display_name":key}]}
            self.scope_var.set("Scope: "+self._scope_label()); self.show_files()
        tree.bind("<<TreeviewSelect>>",selected)

    def _populate_file_table(self,parent):
        columns=("name","folder","ext","size","modified","dup","evidence")
        table=ttk.Treeview(parent,columns=columns,show="headings")
        for c,w in [("name",230),("folder",230),("ext",70),("size",100),("modified",135),("dup",75),("evidence",110)]:
            table.heading(c,text=c.title()); table.column(c,width=w,anchor="w")
        sy=ttk.Scrollbar(parent,orient="vertical",command=table.yview); table.configure(yscrollcommand=sy.set)
        table.pack(side="left",fill="both",expand=True); sy.pack(side="right",fill="y")
        try:
            result=self.engine.list_files(scope=self.scope,search=self.search_text,filters=self.filters,limit=200,after_id=self.after_id)
        except Exception as exc:
            messagebox.showerror("Files",str(exc)); return
        self.current_query=result["normalized_query"]; self.current_rows=result["rows"]
        for i,row in enumerate(self.current_rows):
            ev="Current" if row.get("hash.authority") in ("current","absent") else "Stale hash"
            table.insert("","end",iid=str(i),values=(row.get("path.file_name"),row.get("folder.parent"),row.get("path.extension"),human_bytes(row.get("file.size_bytes")),row.get("time.modified_utc") or "Unavailable","Yes" if row.get("file.in_current_exact_duplicate_group") else "No",ev))
        def choose(_=None):
            sel=table.selection()
            if sel:self.show_file_detail(self.current_rows[int(sel[0])])
        table.bind("<<TreeviewSelect>>",choose)
        bottom=ttk.Frame(parent); bottom.place(relx=0,rely=1,anchor="sw",relwidth=1)
        if self.current_rows:
            ttk.Button(bottom,text="Next 200 →",command=lambda:self.next_files_page(self.current_rows[-1].get("path.id"))).pack(side="right",padx=5,pady=5)

    def next_files_page(self,last_id):
        self.after_id=last_id; self.show_files(reset_cursor=False)

    def show_file_detail(self,row):
        for w in self.detail_host.winfo_children(): w.destroy()
        ttk.Label(self.detail_host,text="Details & Evidence",font=("Segoe UI",10,"bold")).pack(anchor="w")
        pairs=[("File",row.get("path.file_name")),("Parent",row.get("folder.parent")),("Extension",row.get("path.extension")),("Logical Size",human_bytes(row.get("file.size_bytes"))),
               ("Filesystem Modified",row.get("time.modified_utc") or "Unavailable"),("Exact Duplicate","Yes" if row.get("file.in_current_exact_duplicate_group") else "No"),("Hash Authority",row.get("hash.authority"))]
        for k,v in pairs:
            f=ttk.Frame(self.detail_host); f.pack(fill="x",pady=2); ttk.Label(f,text=k+":",width=20,foreground="#666").pack(side="left"); ttk.Label(f,text=str(v)).pack(side="left")
        ttk.Separator(self.detail_host).pack(fill="x",pady=8)
        ttk.Button(self.detail_host,text="Metadata Explorer",command=lambda:self.show_metadata(row.get("path.id"))).pack(anchor="w")
        ttk.Label(self.detail_host,text="Phase 2 shows stored project evidence only.\nIt does not reopen the Source File.",foreground="#666",justify="left").pack(anchor="w",pady=10)

    def show_metadata(self,file_path_id):
        win=tk.Toplevel(self); win.title("Metadata Explorer"); win.geometry("760x500")
        cols=("analyzer","status","title","author","analyzed")
        tv=ttk.Treeview(win,columns=cols,show="headings")
        for c in cols: tv.heading(c,text=c.title()); tv.column(c,width=130)
        tv.pack(fill="both",expand=True,padx=8,pady=8)
        q={"query_schema":QUERY_SCHEMA,"semantic_contract":SEMANTIC_CONTRACT,"subject":{"entity":"analysis_result","temporal":{"mode":"current"}},"scope":{"kind":"project"},
           "where":{"condition":{"left":{"kind":"field","id":"path.id"},"op":"eq","value":{"kind":"literal","value":file_path_id}}},
           "sort":[{"ref":{"kind":"field","id":"analysis.analyzer_key"},"direction":"asc"}]}
        try: rows=self.engine.execute(q)["rows"]
        except Exception as exc: messagebox.showerror("Metadata",str(exc),parent=win); return
        for row in rows:
            author=row.get("analysis.author")
            if author is None:
                status=row.get("analysis.status"); author="No value" if status=="analyzed" else "Unknown"
            tv.insert("","end",values=(row.get("analysis.analyzer_key"),row.get("analysis.status"),row.get("analysis.title") or "",author,row.get("analysis.analyzed_utc")))

    def apply_find(self):
        self.search_text=self.find_var.get().strip(); self.show_files()

    def add_filter(self):
        field=simpledialog.askstring("Add Filter","Field (extension, min size MB, modified before YYYY-MM-DD, stale hash, exact duplicate):",parent=self)
        if not field:return
        f=field.strip().lower()
        if f in ("extension","ext"):
            val=simpledialog.askstring("Extension","Extension (for example .pdf):",parent=self)
            if val:self.filters.append({"condition":{"left":{"kind":"field","id":"path.extension"},"op":"eq","value":{"kind":"literal","value":val.lower()}}})
        elif f in ("min size mb","size","min size"):
            val=simpledialog.askfloat("Minimum Size","Minimum logical size in MB:",parent=self,minvalue=0)
            if val is not None:self.filters.append({"condition":{"left":{"kind":"field","id":"file.size_bytes"},"op":"gte","value":{"kind":"literal","value":int(val*1024*1024)}}})
        elif f in ("modified before","date"):
            val=simpledialog.askstring("Modified Before","YYYY-MM-DD:",parent=self)
            if val:self.filters.append({"condition":{"left":{"kind":"field","id":"time.modified_utc"},"op":"before","value":{"kind":"literal","value":val+"T00:00:00Z"}}})
        elif f in ("stale hash","stale"):
            self.filters.append({"condition":{"left":{"kind":"field","id":"hash.authority"},"op":"eq","value":{"kind":"literal","value":"stale"}}})
        elif f in ("exact duplicate","duplicate"):
            self.filters.append({"condition":{"left":{"kind":"field","id":"file.in_current_exact_duplicate_group"},"op":"is_true"}})
        else:
            messagebox.showinfo("Add Filter","That quick filter is not implemented yet. Advanced query remains available through Saved Query/Report definitions.")
            return
        self.show_files()

    def clear_query(self):
        self.filters=[]; self.search_text=""; self.find_var.set(""); self.scope={"kind":"project"}; self.scope_var.set("Scope: All Sources"); self.show_files()

    def save_current_query(self):
        q=self.current_query or base_file_query(self.scope)
        # Remove execution-only normalization keys.
        q={k:v for k,v in q.items() if not k.startswith("_")}
        name=simpledialog.askstring("Save Query","Name this analytical question:",parent=self)
        if not name:return
        try:
            rec=self.store.create(name,q); messagebox.showinfo("Saved Query",f"Saved as revision {rec['revision_number']}.")
        except Exception as exc: messagebox.showerror("Saved Query",str(exc))

    def show_reports(self):
        self.clear(); self.header("Reports",f"{len(self.reports.reports)} standard reports. Reports run in the current Scope.")
        tools=ttk.Frame(self.content); tools.pack(fill="x",pady=(0,8)); search=tk.StringVar(); family=tk.StringVar(value="All")
        ent=ttk.Entry(tools,textvariable=search); ent.pack(side="left",fill="x",expand=True)
        fams=["All"]+sorted({r["family"] for r in self.reports.reports}); cb=ttk.Combobox(tools,textvariable=family,values=fams,state="readonly",width=18); cb.pack(side="left",padx=6)
        host=ttk.Frame(self.content); host.pack(fill="both",expand=True)
        def redraw(*_):
            for w in host.winfo_children():w.destroy()
            rows=self.reports.list(None if family.get()=="All" else family.get(),search=search.get())
            for r in rows:
                line=ttk.Frame(host); line.pack(fill="x",pady=2); ttk.Label(line,text=r["title"],width=38,font=("Segoe UI",9,"bold")).pack(side="left")
                ttk.Label(line,text=f"{r['family']} · {r['tier']}",width=22,foreground="#666").pack(side="left")
                ttk.Label(line,text=r["counting_unit"],foreground="#666").pack(side="left",fill="x",expand=True)
                ttk.Button(line,text="Run",command=lambda rid=r["report_id"]:self.run_report(rid)).pack(side="right")
        ent.bind("<KeyRelease>",redraw); cb.bind("<<ComboboxSelected>>",redraw); redraw()

    def _report_params(self,r):
        params={}
        for p in r.get("parameters") or []:
            name=p["name"]
            if p["type"]=="integer": val=simpledialog.askinteger(r["title"],name.replace('_',' ').title()+":",initialvalue=p.get("default"),parent=self)
            elif p["type"]=="bytes":
                mb=simpledialog.askfloat(r["title"],name.replace('_',' ').title()+" (MB):",parent=self); val=None if mb is None else int(mb*1024*1024)
            else: val=simpledialog.askstring(r["title"],name.replace('_',' ').title()+" (ISO date/time if applicable):",parent=self)
            if val is None:return None
            params[name]=val
        return params

    def run_report(self,rid):
        r=self.reports.get(rid); params=self._report_params(r)
        if params is None:return
        try: result=self.reports.run(self.engine,rid,scope=self.scope,parameters=params)
        except Exception as exc: messagebox.showerror(r["title"],str(exc)); return
        self.clear(); self.header(r["title"],r["purpose"])
        ttk.Label(self.content,text=f"Scope: {self._scope_label()} · Unit: {r['counting_unit']}",foreground="#666").pack(anchor="w",pady=(0,8))
        self._generic_result_table(result)

    def _generic_result_table(self,result):
        rows=result["rows"]
        if not rows:
            ttk.Label(self.content,text="No matching results.").pack(anchor="w"); return
        cols=list(rows[0])
        frame=ttk.Frame(self.content); frame.pack(fill="both",expand=True)
        tv=ttk.Treeview(frame,columns=cols,show="headings")
        for c in cols: tv.heading(c,text=c); tv.column(c,width=150,anchor="w")
        sy=ttk.Scrollbar(frame,orient="vertical",command=tv.yview); sx=ttk.Scrollbar(frame,orient="horizontal",command=tv.xview); tv.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
        tv.grid(row=0,column=0,sticky="nsew"); sy.grid(row=0,column=1,sticky="ns"); sx.grid(row=1,column=0,sticky="ew"); frame.rowconfigure(0,weight=1); frame.columnconfigure(0,weight=1)
        for row in rows[:5000]: tv.insert("","end",values=[human_bytes(row[c]) if c.endswith("bytes") and isinstance(row[c],(int,float)) else row[c] for c in cols])
        ttk.Label(self.content,text=f"{len(rows):,} returned row/group(s) in this execution · Coverage: {result['coverage']['coverage']}",foreground="#666").pack(anchor="w",pady=5)

    def show_saved(self):
        self.clear(); self.header("Saved Queries","Saved questions re-execute against project evidence; rows are not frozen.")
        host=ttk.Frame(self.content); host.pack(fill="both",expand=True)
        for q in self.store.list():
            line=ttk.Frame(host); line.pack(fill="x",pady=3); ttk.Label(line,text=q["name"],width=40,font=("Segoe UI",9,"bold")).pack(side="left")
            ttk.Label(line,text=f"Revision {q['revision_number']} · {q['updated_utc']}",foreground="#666").pack(side="left",fill="x",expand=True)
            ttk.Button(line,text="Run",command=lambda uid=q["saved_query_uid"]:self.run_saved(uid)).pack(side="right")

    def run_saved(self,uid):
        try:
            rec=self.store.get_latest(uid); result=self.engine.execute(rec["query"],retain_kind="saved_query",saved_revision_id=rec["saved_query_revision_id"])
            self.clear(); self.header(rec["name"],"Saved Query · re-executed against current evidence"); self._generic_result_table(result)
        except Exception as exc: messagebox.showerror("Saved Query",str(exc))

    def show_evidence(self):
        self.clear(); self.header("Evidence","What the project knows, what it does not know, and why.")
        h=evidence_health(self.conn)
        items=[("Coverage",h["coverage"]),("Present File Locations",f"{h['present_locations']:,}"),("Inaccessible",f"{h['inaccessible_locations']:,}"),("Unverified",f"{h['unverified_locations']:,}"),("Missing",f"{h['missing_locations']:,}"),("Stale Hashes",f"{h['stale_hashes']:,}"),("Cloud / Offline",f"{h['cloud_offline']:,}"),("Current Analyzer Failures",f"{h['current_analyzer_failures']:,}")]
        for k,v in items:
            row=ttk.Frame(self.content); row.pack(fill="x",pady=3); ttk.Label(row,text=k,width=30).pack(side="left"); ttk.Label(row,text=v,font=("Segoe UI",9,"bold")).pack(side="left")
        if h["warnings"]:
            ttk.Separator(self.content).pack(fill="x",pady=10)
            for w in h["warnings"]: ttk.Label(self.content,text="• "+w,foreground="#7a5700").pack(anchor="w")
        ttk.Button(self.content,text="Rebuild Exact-Duplicate Projection",command=self.rebuild_duplicates).pack(anchor="w",pady=(14,4))
        ttk.Button(self.content,text="Build / Refresh Literal Text Index",command=self.rebuild_fts).pack(anchor="w")

    def rebuild_duplicates(self):
        try:
            n=ensure_duplicate_projection(self.conn); messagebox.showinfo("Exact Duplicates",f"Current duplicate projection valid: {n:,} group(s).")
        except Exception as exc: messagebox.showerror("Exact Duplicates",str(exc))

    def rebuild_fts(self):
        try:
            res=self.fts.rebuild(); messagebox.showinfo("Literal Text Index",f"Indexed {res['indexed']:,} unique extracted-text artifact(s).\nMissing evidence artifacts: {res['missing_artifacts']:,}")
        except Exception as exc: messagebox.showerror("Literal Text Index",str(exc))

    def show_history(self):
        self.clear(); self.header("History","Stored observations — separate from Current State.")
        q={"query_schema":QUERY_SCHEMA,"semantic_contract":SEMANTIC_CONTRACT,"subject":{"entity":"file","temporal":{"mode":"history"}},"scope":{"kind":"project"},
           "where":{"condition":{"left":{"kind":"field","id":"observation.change_kind"},"op":"is_not_null"}},
           "sort":[{"ref":{"kind":"field","id":"observation.observed_utc"},"direction":"desc","nulls":"last"}],"semantic_limit":1000}
        try:self._generic_result_table(self.engine.execute(q))
        except Exception as exc:messagebox.showerror("History",str(exc))

    def close(self):
        try:self.conn.close()
        finally:self.destroy()
