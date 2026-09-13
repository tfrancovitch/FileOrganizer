r"""The Dashboard: one window from New Project to the answers.

This began as the Phase 2 "Hybrid Evidence Explorer", a second window reached
from the Phase 1 dashboard. Decision 1 (2026-09-10) merged the two: a person
should not know they were ever separate programs. So this window also creates
projects and runs the Phase 1 collection stages -- through `runner.py`, which
drives the same RunCoordinator the old dashboard did -- and lands on the
project summary (`hub.py`).

The layout follows the user's real-use notes of 2026-09-11: Open Project and
New Project at the top of the side panel, Options and Exit at the bottom,
nothing open until one of them is clicked; a project summary rather than a
"what can this answer" page; a Files page that sorts, filters, chooses its
columns, pages both ways and exports; a Reports list that scrolls with Run in
the first column and exports its result; a confirmation before a project is
closed by opening another.

The Phase 2 rules still hold for everything analytical: stored evidence only,
no source file is reopened to answer a question. The collection stages that
DO open files are Phase 1 stages, invoked from here, recorded as runs.
"""
from __future__ import annotations

import csv
import json
import os
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk, messagebox, simpledialog, filedialog

from .core import connect, require_phase2_schema, project_info, stamp_core_version, utc_now
from .coverage import evidence_health
from .derived import ensure_duplicate_projection
from .fts import FtsManager, FtsUnavailable
from .query import QueryEngine, QueryError, QUERY_SCHEMA, SEMANTIC_CONTRACT
from .reports import ReportCatalog
from .saved import SavedQueryStore
from . import hub as hub_view
from .runner import RunRequest, PRESCAN, run_blocking, app_root_for

#: The application root this window is installed under: <root>\Scripts\Phase2\gui.py
APP_ROOT = Path(__file__).resolve().parents[2]
PROJECTS_DIR = APP_ROOT / "Projects"

#: Every column the Files page can show: field id -> (heading, width, kind).
#: `kind` decides how a value is rendered and what "Filter this column" asks.
FILE_COLUMNS = {
    "path.file_name": ("File", 240, "text"),
    "folder.parent": ("Folder", 240, "text"),
    "path.relative": ("Path", 320, "text"),
    "path.extension": ("Ext", 70, "enum"),
    "file.size_bytes": ("Size", 100, "bytes"),
    "time.modified_utc": ("Modified", 170, "datetime"),
    "time.created_utc": ("Created", 170, "datetime"),
    "time.accessed_utc": ("Accessed", 170, "datetime"),
    "file.in_current_exact_duplicate_group": ("Duplicate", 80, "bool"),
    "hash.status": ("Hash status", 120, "enum"),
    "hash.authority": ("Evidence", 100, "enum"),
    "path.depth": ("Depth", 60, "int"),
    "file.hard_link_count": ("Hard links", 80, "int"),
    "file.is_offline_or_cloud": ("Cloud/offline", 90, "bool"),
    "file.allocated_size_bytes": ("Size on disk", 100, "bytes"),
    # From analysis -- the newest current result for the file. NULL until the
    # relevant bucket has been analysed.
    "analysis.title": ("Title", 220, "text"),
    "analysis.author": ("Author", 140, "text"),
    "analysis.content_created_reported": ("Created (per file)", 170, "text"),
    "analysis.width_px": ("Width", 70, "int"),
    "analysis.height_px": ("Height", 70, "int"),
    "analysis.duration_seconds": ("Duration", 90, "duration"),
    "analysis.pdf.page_count": ("Pages", 60, "int"),
    "analysis.word_count": ("Words", 80, "int"),
    "analysis.char_count": ("Characters", 90, "int"),
    "analysis.camera.make": ("Camera make", 110, "text"),
    "analysis.camera.model": ("Camera model", 130, "text"),
    "analysis.archive.entry_count": ("Archive entries", 100, "int"),
    "analysis.analyzed_count": ("Analyzers run", 90, "int"),
    "analysis.error": ("Analysis error", 300, "text"),
    "analysis.source_type": ("Read as", 130, "enum"),
    "analysis.ocr.pages": ("OCR pages", 80, "int"),
    "analysis.ocr.quality": ("OCR quality", 90, "int"),
    "analysis.ocr.review": ("OCR review", 90, "enum"),
    "analysis.ocr.reasons": ("OCR review reasons", 320, "text"),
}

#: What each column means, for the Columns dialog -- the user asked what Size
#: versus Allocated was, and what a hard link is. Plain answers, kept here.
COLUMN_HELP = {
    "path.file_name": "The file's name.",
    "folder.parent": "The folder the file sits in, relative to the source root.",
    "path.relative": "The full path relative to the source root.",
    "path.extension": "The extension, lower-cased.",
    "file.size_bytes": "How many bytes the file holds (its length).",
    "time.modified_utc": "When the file's contents were last changed, per the file system (UTC).",
    "time.created_utc": "When the file was created here, per the file system (UTC).",
    "time.accessed_utc": "When the file was last opened, per the file system (UTC). Windows often stops updating this.",
    "file.in_current_exact_duplicate_group": "Yes when another current file has byte-identical contents.",
    "hash.status": "The fingerprint verdict: unique_by_hash, confirmed_duplicate, size_unique (proven unique without being opened), not_attempted, unresolved, error.",
    "hash.authority": "Whether the fingerprint describes the file as it is now (Current), an older version (Stale), or there is none.",
    "path.depth": "How many folders deep the file is.",
    "file.hard_link_count": "How many names on the disk point at this same file. 1 is normal; 2 or more means the same bytes appear under other names without using extra space.",
    "file.is_offline_or_cloud": "Yes when the file is a cloud placeholder not present on the local disk. Never opened by the product.",
    "file.allocated_size_bytes": "The space the disk actually reserves for the file. Equals Size on an ordinary volume; smaller for compressed or sparse files, and the walk only measures it separately for unusual files.",
    "analysis.title": "The title recorded inside the file (PDF, Office, audio).",
    "analysis.author": "The author recorded inside the file (PDF, Office).",
    "analysis.content_created_reported": "The creation date the file itself claims (PDF/Office properties, photo EXIF). Not the file system's date.",
    "analysis.width_px": "Image or video width in pixels.",
    "analysis.height_px": "Image or video height in pixels.",
    "analysis.duration_seconds": "Audio or video length.",
    "analysis.pdf.page_count": "PDF page count.",
    "analysis.word_count": "Words counted by the text analyzer or extraction.",
    "analysis.char_count": "Characters counted by the text analyzer or extraction.",
    "analysis.camera.make": "Camera make from a RAW image's EXIF.",
    "analysis.camera.model": "Camera model from a RAW image's EXIF.",
    "analysis.archive.entry_count": "Number of entries inside an archive (listed without extracting).",
    "analysis.analyzed_count": "How many analyzers have a current result for this file.",
    "analysis.error": "The reason the newest analysis attempt on this file failed, if it did.",
    "analysis.source_type": "What extraction read the file as, judged from its bytes (a .doc holding RTF says RTF; a scanned PDF says PDF (OCR)).",
    "analysis.ocr.pages": "How many pages had no text layer and were read by OCR.",
    "analysis.ocr.quality": "OCR's worst page, 0-100: resolution, contrast, focus, skew, and how much of the result reads as words.",
    "analysis.ocr.review": "yes when a page scored under 70 -- a poor scan worth a person's look.",
    "analysis.ocr.reasons": "Why the OCR review flag was raised, page by page.",
}
DEFAULT_FILE_COLUMNS = ["path.file_name", "folder.parent", "path.extension", "file.size_bytes",
                        "time.modified_utc", "file.in_current_exact_duplicate_group", "hash.authority"]
PAGE_SIZE = 200


def human_bytes(value):
    if value is None: return "Unknown"
    n=float(value)
    for unit in ("B","KB","MB","GB","TB"):
        if n < 1024 or unit=="TB": return f"{n:,.1f} {unit}" if unit!="B" else f"{int(n):,} B"
        n/=1024


def render_cell(field, value):
    """One cell, rendered for the kind of column it sits in."""
    kind = FILE_COLUMNS.get(field, (None, None, "text"))[2]
    if value is None: return "" if kind in ("text", "enum") else "Unavailable" if kind == "datetime" else ""
    if kind == "bytes": return human_bytes(value)
    if kind == "bool": return "Yes" if value else "No"
    if kind == "duration":
        try:
            total = int(round(float(value))); return f"{total // 60}:{total % 60:02d}"
        except (TypeError, ValueError): return str(value)
    if kind == "int":
        try: return f"{int(value):,}"
        except (TypeError, ValueError): return str(value)
    if field == "hash.authority": return {"current": "Current", "absent": "None", "stale": "Stale hash"}.get(value, str(value))
    return str(value)


def base_file_query(scope):
    return {
        "query_schema":QUERY_SCHEMA,
        "semantic_contract":SEMANTIC_CONTRACT,
        "subject":{"entity":"file","temporal":{"mode":"current"},"current_file_states":["present"]},
        "scope":scope,
    }


class Phase2App(tk.Tk):
    """The one window. Opens on nothing; Open Project and New Project are the
    first two buttons in the side panel, Options and Exit the last two."""

    def __init__(self, project_dir=None):
        super().__init__()
        self.app_root=APP_ROOT
        self.project_dir=None
        self.conn=None; self.store=None; self.fts=None; self.engine=None; self.info={}
        self.reports=ReportCatalog()
        self.scope={"kind":"project"}
        self.filters=[]
        self.search_text=""
        self.current_rows=[]
        self.current_query=None
        # Files page state: chosen columns, sort, and the stack of page cursors.
        self.files_columns=list(DEFAULT_FILE_COLUMNS)
        self.files_sort=None                       # (field, "asc"|"desc") or None
        self.files_cursors=[None]                  # cursor that opens page i
        self.files_page=0
        self.filter_labels=[]                      # a name per filter, when one is known
        #: (stop_event, worker thread) while a run owns the window; else None.
        self.active_run=None

        self.title("The File Organizer")
        enable_fault_log(self.app_root)
        width=min(1280,max(1000,self.winfo_screenwidth()-120))
        height=min(820,max(650,self.winfo_screenheight()-160))
        self.geometry(f"{width}x{height}")
        self.minsize(1000,650)
        self.protocol("WM_DELETE_WINDOW",self.close)
        self._style()
        self._build_shell()
        # Open maximized: the user's note, and the tables want the width. The
        # fitted geometry above is what it falls back to un-maximized.
        try: self.state("zoomed")
        except tk.TclError: pass
        if project_dir is not None:
            self.open_project(project_dir)
        else:
            self.show_welcome()

    def report_callback_exception(self, exc_type, exc_value, exc_traceback):
        r"""A Tk callback raised. Log it, tell the person, keep running.

        Tkinter's default handler prints the traceback to stderr, and under
        pythonw there is no stderr, so a callback error left no trace anywhere
        -- which is what the first real corpus session's crash looked like from
        here: nothing. The window survives a callback exception either way
        (verified under pythonw); what this adds is the evidence, in
        Logs\app.log, and a dialog so the person knows something happened.
        """
        import traceback
        detail="".join(traceback.format_exception(exc_type,exc_value,exc_traceback))
        try:
            import fo_log
            fo_log.get_app_log(str(self.app_root)).log("CRITICAL","Unhandled exception in a window callback:\n"+detail)
        except Exception: pass
        try:
            messagebox.showerror("Unexpected error",
                f"Something went wrong on this screen:\n\n{exc_value}\n\nThe window is still open and the project "
                "is unaffected. The details are in Logs\\app.log.",parent=self)
        except Exception: pass

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
        self.nav_open=ttk.Button(nav,text="Open Project",style="Nav.TButton",command=self.show_open_panel); self.nav_open.pack(fill="x",pady=2)
        self.nav_new=ttk.Button(nav,text="New Project",style="Nav.TButton",command=self.show_new_panel); self.nav_new.pack(fill="x",pady=2)
        self.nav_sep=ttk.Separator(nav); self.nav_sep.pack(fill="x",pady=10)
        self.nav_buttons=[]
        for label,cmd in [
            ("Project",self.show_hub),("Files",self.show_files),("Reports",self.show_reports),
            ("Saved Queries",self.show_saved),("Evidence",self.show_evidence),("History",self.show_history)]:
            b=ttk.Button(nav,text=label,style="Nav.TButton",command=cmd); b.pack(fill="x",pady=2)
            self.nav_buttons.append(b)
        # Bottom of the panel: Options (the architecture is here; there is
        # nothing to set yet) and Exit.
        self.nav_exit=ttk.Button(nav,text="Exit",style="Nav.TButton",command=self.close); self.nav_exit.pack(side="bottom",fill="x",pady=2)
        self.nav_options=ttk.Button(nav,text="Options",style="Nav.TButton",command=self.show_options); self.nav_options.pack(side="bottom",fill="x",pady=2)
        ttk.Label(nav,text="Answers come from stored evidence.\nNo source file is modified.",foreground="#666",justify="left").pack(side="bottom",fill="x",pady=8)

        main=ttk.Frame(self); main.grid(row=0,column=1,sticky="nsew"); main.columnconfigure(0,weight=1); main.rowconfigure(2,weight=1)
        top=ttk.Frame(main,padding=(12,8)); top.grid(row=0,column=0,sticky="ew"); top.columnconfigure(2,weight=1)
        self.project_var=tk.StringVar(value="No project open")
        ttk.Label(top,textvariable=self.project_var,font=("Segoe UI",10,"bold")).grid(row=0,column=0,sticky="w")
        self.scope_var=tk.StringVar(value="")
        ttk.Label(top,textvariable=self.scope_var,foreground="#666").grid(row=1,column=0,sticky="w")
        self.find_var=tk.StringVar()
        entry=ttk.Entry(top,textvariable=self.find_var); entry.grid(row=0,column=2,rowspan=2,sticky="ew",padx=12)
        entry.bind("<Return>",lambda e:self.apply_find())
        self.top_buttons=[entry]
        for label,cmd in (("Find in files",self.apply_find),("+ Filter",self.add_filter),("Save Query",self.save_current_query)):
            b=ttk.Button(top,text=label,command=cmd); b.grid(row=0,column=3+len(self.top_buttons)-1,rowspan=2,padx=(0,6))
            self.top_buttons.append(b)

        self.evidence_var=tk.StringVar()
        self.evidence_label=ttk.Label(main,textvariable=self.evidence_var,style="Warn.TLabel")
        self.evidence_label.grid(row=1,column=0,sticky="ew")
        self.content=ttk.Frame(main,padding=12); self.content.grid(row=2,column=0,sticky="nsew")
        self._set_project_controls(False)

    # -- project lifecycle -------------------------------------------------

    def open_project(self, project_dir):
        """Open a project through the trusted migration boundary, then land on its summary."""
        project_dir=Path(project_dir).resolve()
        self.release_connection()
        import fo_db
        try:
            conn,_project=fo_db.open_project(str(project_dir),app_version=f"P2-{__import__('Phase2').VERSION}")
            conn.close()
        except Exception as exc:
            messagebox.showerror("Open project",f"This project could not be opened.\n\n{exc}",parent=self)
            self.show_open_panel(); return
        self.project_dir=project_dir
        self.reopen_connection()
        self.scope={"kind":"project"}; self.filters=[]; self.filter_labels=[]; self.search_text=""; self.find_var.set("")
        self.files_sort=None; self.files_cursors=[None]; self.files_page=0
        self.scope_var.set("Scope: All Sources")
        self._set_project_controls(True)
        self.show_hub()

    def reopen_connection(self):
        """(Re)establish the analytical connection. The runner closes it before a run."""
        self.conn=connect(self.project_dir,write=True)
        require_phase2_schema(self.conn)
        stamp_core_version(self.conn)
        self.store=SavedQueryStore(self.conn)
        self.fts=FtsManager(self.conn,self.project_dir)
        self.engine=QueryEngine(self.conn,saved_store=self.store,fts_manager=self.fts)
        self.info=project_info(self.conn)
        name=self.info.get("name",self.project_dir.name)
        self.title(f"The File Organizer — {name}")
        self.project_var.set(f"Project: {name}")
        self.refresh_evidence_strip()

    def release_connection(self):
        """Close the write connection so a run can own the database.

        fo_db documents a single-writer rule and migrates under BEGIN
        IMMEDIATE; holding this connection across a run would sit against the
        busy timeout at best and conflict with a migration at worst.
        """
        if self.conn is not None:
            try: self.conn.close()
            except Exception: pass
        self.conn=None; self.store=None; self.fts=None; self.engine=None

    def close_project(self):
        """Leave the open project. Callers confirm first (see _confirm_leave)."""
        self.release_connection()
        self.project_dir=None; self.info={}
        self.title("The File Organizer"); self.project_var.set("No project open"); self.scope_var.set("")
        self._set_project_controls(False)

    def _confirm_leave(self, verb):
        """The user's note: opening another project by accident closed the one
        they were in. Ask first, and say which project."""
        if self.project_dir is None: return True
        name=self.info.get("name",self.project_dir.name)
        return messagebox.askyesno("Close this project?",
                                   f"This will close the project '{name}' so you can {verb}.\n\n"
                                   "Nothing is lost -- everything it knows is in its database -- "
                                   "but the screen you are on will close.\n\nContinue?",parent=self)

    def can_run(self):
        return self.project_dir is not None and app_root_for(self.project_dir) is not None

    def _set_project_controls(self, enabled):
        state="normal" if enabled else "disabled"
        for b in self.nav_buttons: b.configure(state=state)
        for w in self.top_buttons: w.configure(state=state)

    def set_busy(self, busy):
        """While a run owns the window nothing else in it may be clicked."""
        state="disabled" if busy else "normal"
        for b in (self.nav_open,self.nav_new,self.nav_options,self.nav_exit): b.configure(state=state)
        if busy:
            for b in self.nav_buttons: b.configure(state="disabled")
            for w in self.top_buttons: w.configure(state="disabled")
        else:
            self._set_project_controls(self.project_dir is not None)

    # -- runs ---------------------------------------------------------------

    def start_run(self, request):
        """Hand the window to the runner. The connection is closed until it is done."""
        if self.active_run is not None:
            return
        if request.kind!=PRESCAN or not request.source_roots:
            if not self.can_run():
                messagebox.showinfo("Cannot run","This project is not under the application's Projects folder.",parent=self); return
        self.set_busy(True)
        self._last_request=request
        self.evidence_var.set(f"Running: {request.title}")
        self.evidence_label.configure(style="Warn.TLabel")
        run_blocking(self,request,lambda outcome:self._run_finished(request,outcome))

    def _run_finished(self, request, outcome):
        self.active_run=None
        self.set_busy(False)
        if outcome.status=="declined":
            # The caution was answered No, or Cancel was pressed while the
            # estimate was being measured: nothing ran, nothing to announce.
            if self.project_dir is not None: self.reopen_connection(); self.show_hub()
            else: self.show_new_panel()
            return
        if outcome.project_dir is not None and (self.project_dir is None or Path(outcome.project_dir)!=self.project_dir):
            # A project was just created: open it properly, migration boundary and all.
            self.project_dir=None
            if outcome.status in ("completed","completed_with_warnings","cancelled"):
                self.open_project(outcome.project_dir)
                self._announce(request,outcome)
                if outcome.ok and request.after=="doors": hub_view.show_doors(self)
                return
            self.show_open_panel(); self._announce(request,outcome); return
        if self.project_dir is None:
            self.show_open_panel(); self._announce(request,outcome); return
        self.reopen_connection()
        self._announce(request,outcome)
        if outcome.ok and request.after=="doors": hub_view.show_doors(self)
        else: self.show_hub()

    def _announce(self, request, outcome):
        minutes=outcome.elapsed_sec/60.0
        took=f"{outcome.elapsed_sec:.0f} s" if outcome.elapsed_sec<120 else f"{minutes:.0f} min"
        estimate=(request.estimate or {}).get("text") if isinstance(request.estimate,dict) else None
        tail=f"\n\nTook {took}."+(f" Estimate was {estimate}." if estimate else "")
        if outcome.status=="failed":
            messagebox.showerror(request.title,outcome.message+tail,parent=self)
        elif outcome.cancelled:
            messagebox.showinfo(request.title,outcome.message+tail,parent=self)
        elif outcome.warnings:
            messagebox.showwarning(request.title,outcome.message+"\n\n"+"\n".join(outcome.warnings[:6])+tail,parent=self)

    # -- the start: nothing open, then Open Project or New Project ------------

    def show_welcome(self):
        """What the window shows before anything is clicked: nothing, on purpose."""
        self.clear()
        self.evidence_label.configure(style="Good.TLabel"); self.evidence_var.set("Inventory. Organize. De-duplicate.")
        ttk.Label(self.content,text="Open a project, or start a new one.",style="Sub.TLabel").pack(anchor="w",pady=(40,0))

    def show_open_panel(self):
        """The list of projects on disk. Reached from Open Project."""
        if self.active_run is not None: return
        if self.project_dir is not None:
            if not self._confirm_leave("open another"): return
            self.close_project()
        self.clear(); self.header("Open Project","Projects on disk, under the application's Projects folder.")
        self.evidence_label.configure(style="Good.TLabel"); self.evidence_var.set("Inventory. Organize. De-duplicate.")
        box=ttk.Frame(self.content); box.pack(fill="both",expand=True)
        names=list_projects()
        listbox=tk.Listbox(box,height=16,exportselection=False,font=("Segoe UI",10))
        for n in names: listbox.insert("end",n)
        listbox.pack(fill="both",expand=True)
        if not names: ttk.Label(box,text="No projects yet. Use New Project.",foreground="#666").pack(anchor="w",pady=6)
        def open_selected(_=None):
            sel=listbox.curselection()
            if not sel: messagebox.showinfo("Open","Select a project first.",parent=self); return
            self.open_project(PROJECTS_DIR/listbox.get(sel[0]))
        listbox.bind("<Double-Button-1>",open_selected)
        row=ttk.Frame(box); row.pack(fill="x",pady=(8,0))
        ttk.Button(row,text="Open",command=open_selected,state="normal" if names else "disabled").pack(side="left")
        ttk.Button(row,text="Re-run (not yet decided)",state="disabled").pack(side="left",padx=6)
        ttk.Label(box,text="Whether a re-run is a new project is undecided, so that button ships greyed out rather than clickable but wrong.",foreground="#666",wraplength=700,justify="left").pack(anchor="w",pady=(6,0))

    def show_new_panel(self):
        """The New Project form. Reached from New Project."""
        if self.active_run is not None: return
        if self.project_dir is not None:
            if not self._confirm_leave("start a new one"): return
            self.close_project()
        self.clear(); self.header("New Project","Pick the folder(s) to inventory. The Pre-Scan runs as soon as the project is created.")
        self.evidence_label.configure(style="Good.TLabel"); self.evidence_var.set("Inventory. Organize. De-duplicate.")
        new=ttk.Frame(self.content); new.pack(fill="x")
        ttk.Label(new,text="Folder(s) to inventory:").pack(anchor="w")
        path_var=tk.StringVar()
        prow=ttk.Frame(new); prow.pack(fill="x",pady=4)
        ttk.Entry(prow,textvariable=path_var).pack(side="left",fill="x",expand=True)
        roots=tk.Listbox(new,height=4)
        def browse():
            folder=filedialog.askdirectory(title="Select a folder to inventory",parent=self)
            if folder: path_var.set(os.path.normpath(folder))
        def add():
            folder=path_var.get().strip()
            if not folder: return
            ok,title,msg=validate_folder(folder)
            if not ok: messagebox.showerror(title,msg,parent=self); return
            if os.path.normcase(os.path.normpath(folder)) in [os.path.normcase(os.path.normpath(roots.get(i))) for i in range(roots.size())]:
                messagebox.showwarning("Already added",folder,parent=self); return
            roots.insert("end",os.path.normpath(folder)); path_var.set("")
        def remove():
            sel=roots.curselection()
            if sel: roots.delete(sel[0])
        ttk.Button(prow,text="Browse...",command=browse).pack(side="left",padx=(6,0))
        ttk.Button(prow,text="Add",command=add).pack(side="left",padx=(6,0))
        lrow=ttk.Frame(new); lrow.pack(fill="x")
        roots.pack(in_=lrow,side="left",fill="x",expand=True)
        ttk.Button(lrow,text="Remove",command=remove).pack(side="left",padx=(6,0),anchor="n")
        ttk.Label(new,text="Add a second folder only if you want one project to cover both.",foreground="#666").pack(anchor="w",pady=(2,8))
        ttk.Label(new,text="Project name (blank to auto-name):").pack(anchor="w")
        name_var=tk.StringVar(); ttk.Entry(new,textvariable=name_var,width=50).pack(anchor="w",pady=4)
        def create():
            chosen=[roots.get(i) for i in range(roots.size())]
            typed=path_var.get().strip()
            if typed and os.path.normcase(os.path.normpath(typed)) not in [os.path.normcase(x) for x in chosen]:
                ok,title,msg=validate_folder(typed)
                if not ok: messagebox.showerror(title,msg,parent=self); return
                chosen.append(os.path.normpath(typed))
            if not chosen: messagebox.showwarning("Missing folder","Browse to a folder to inventory first.",parent=self); return
            name=name_var.get().strip() or default_project_name()
            if (PROJECTS_DIR/name).exists():
                messagebox.showerror("Name in use",f"A project called '{name}' already exists.",parent=self); return
            # Straight into the Pre-Scan -- no screen in between (the user's
            # second real-use note). The walk has no estimate to show anyway.
            self.start_run(RunRequest(PRESCAN,"Pre-Scan",source_roots=chosen,project_name=name,after="doors"))
        ttk.Button(new,text="Create project and run the Pre-Scan",command=create).pack(anchor="w",pady=(10,0))

    def show_options(self):
        """Options: what Extract text may leave out. Saved to the project's settings.json."""
        if self.active_run is not None: return
        self.clear(); self.header("Options","What text extraction reads. Each switch is saved to this project; the estimate before a run reflects them.")
        if self.conn is None or self.project_dir is None:
            ttk.Label(self.content,text="Open a project to set its options.",wraplength=800,justify="left").pack(anchor="w"); return
        from Phase2.runner import load_settings
        import json
        settings=load_settings(self.project_dir)
        switches=[("ExtractionOcr","Read pages that have no text layer with OCR (Windows' own engine)",
                   "Scanned PDFs and photographed pages. About half a second a page. Off, such pages stay unread and the file says so."),
                  ("ExtractionPictures","Read pictures (JPEG, PNG, TIFF...) for text when they look like documents",
                   "Every picture is looked at briefly; only the ones that look like a page of paper are read. A folder of photographs costs the look and nothing more."),
                  ("ExtractionArchives","Read the documents inside ZIP and 7z archives",
                   "Two levels deep. A zip of scanned PDFs costs what its PDFs cost.")]
        self._option_vars={}
        def save():
            current=load_settings(self.project_dir)
            for key,var in self._option_vars.items(): current[key]=bool(var.get())
            try:
                with open(Path(self.project_dir)/"settings.json","w",encoding="utf-8") as handle: json.dump(current,handle,indent=2)
            except OSError as exc: messagebox.showerror("Options",f"Could not save settings.json: {exc}")
        for key,label,detail in switches:
            var=tk.BooleanVar(value=bool(settings.get(key,True))); self._option_vars[key]=var
            ttk.Checkbutton(self.content,text=label,variable=var,command=save).pack(anchor="w",pady=(10,0))
            ttk.Label(self.content,text=detail,wraplength=800,justify="left",foreground="#555").pack(anchor="w",padx=(24,0))

    # -- views ---------------------------------------------------------------

    def show_hub(self):
        """The project summary -- the landing view for an open project."""
        if self.conn is None: self.show_welcome(); return
        hub_view.show_hub(self)

    def clear(self):
        # A scrolling list binds the mouse wheel for the whole window while
        # the pointer is over it; if the list is destroyed under the pointer
        # the unbind never fires and the next wheel turn reaches a widget
        # that no longer exists ("invalid command name ... !canvas"). Drop it
        # here, every time, before the screen goes.
        try: self.unbind_all("<MouseWheel>")
        except tk.TclError: pass
        for w in self.content.winfo_children(): w.destroy()
        self.content.columnconfigure(0,weight=0); self.content.rowconfigure(0,weight=0)
        # The screen just torn down is collected NOW, on the main thread. Its
        # widgets, variables and closures form reference cycles, and a cycle
        # is freed by whichever thread triggers the collector; a Tk object
        # freed on a worker thread raises at best and aborts the process at
        # worst (Tcl_AsyncDelete). See runner.run_blocking for the reproduction.
        import gc; gc.collect()

    def header(self,title,subtitle=None,back=False):
        """Page title, optional subtitle, and -- on project pages -- a way back to the summary."""
        row=ttk.Frame(self.content); row.pack(fill="x")
        ttk.Label(row,text=title,style="Title.TLabel").pack(side="left",anchor="w")
        if back and self.project_dir is not None:
            ttk.Button(row,text="◀ Project summary",command=self.show_hub).pack(side="right",anchor="e")
        if subtitle: ttk.Label(self.content,text=subtitle,style="Sub.TLabel",wraplength=980,justify="left").pack(anchor="w",pady=(2,12))
        else: ttk.Frame(self.content,height=8).pack()

    def refresh_evidence_strip(self):
        h=evidence_health(self.conn)
        self.evidence_label.unbind("<Button-1>"); self.evidence_label.configure(cursor="")
        if h["coverage"]=="complete" and not h["warnings"]:
            self.evidence_label.configure(style="Good.TLabel"); self.evidence_var.set("Coverage complete · Current project evidence")
        else:
            self.evidence_label.configure(style="Warn.TLabel")
            parts=["Coverage "+h["coverage"]]
            if h["stale_hashes"]: parts.append(f"{h['stale_hashes']:,} stale hashes")
            failed=h.get("files_with_analyzer_failures",0)
            if failed:
                # Files, not analyzers: "16 analyzer failures" read as sixteen
                # broken analyzers. Click to see which files and why.
                parts.append(f"{failed:,} file{'s' if failed!=1 else ''} could not be analyzed -- click to see which")
                self.evidence_label.configure(cursor="hand2")
                self.evidence_label.bind("<Button-1>",lambda e:self.show_files_for_failures())
            self.evidence_var.set(" · ".join(parts))

    # -- Files, pre-filtered ---------------------------------------------------

    def show_files_for_extensions(self, extensions, label, exclude=False):
        """The Files page showing one bucket: files whose extension is in the
        set (or, for "Other", not in any bucket's set). Largest first, because
        that is the question a person clicking a 31 GB line is asking."""
        if self.conn is None: return
        values=[{"kind":"literal","value":e} for e in sorted(extensions)]
        self.filters=[{"condition":{"left":{"kind":"field","id":"path.extension"},"op":"not_in" if exclude else "in","values":values}}]
        self.filter_labels=[f"type: {label}"]
        self.search_text=""; self.find_var.set(""); self.files_sort=("file.size_bytes","desc")
        self.show_files()

    def show_files_for_ocr_review(self):
        """The Files page showing every file OCR flagged for a person to look at."""
        if self.conn is None: return
        self.filters=[{"condition":{"left":{"kind":"field","id":"analysis.ocr.review"},"op":"eq","value":{"kind":"literal","value":"yes"}}}]
        self.filter_labels=["OCR: double-check"]
        self.search_text=""; self.find_var.set(""); self.files_sort=("analysis.ocr.quality","asc")
        cols=list(self.files_columns)
        for c in ("analysis.ocr.quality","analysis.ocr.reasons"):
            if c not in cols: cols.append(c)
        self.files_columns=cols
        self.show_files()

    def show_files_for_failures(self):
        """The Files page showing every file whose newest analysis attempt failed, with the reason."""
        if self.conn is None: return
        self.filters=[{"relation":{"relation":"current_analysis","quantifier":"any",
                                   "where":{"condition":{"left":{"kind":"field","id":"analysis.status"},"op":"eq","value":{"kind":"literal","value":"error"}}}}}]
        self.filter_labels=["could not be analyzed"]
        self.search_text=""; self.find_var.set(""); self.files_sort=None
        if "analysis.error" not in self.files_columns: self.files_columns=list(self.files_columns)+["analysis.error"]
        self.show_files()

    def _scope_label(self):
        if self.scope["kind"]=="project": return "All Sources"
        if self.scope["kind"]=="source_roots": return "Selected Source Root"
        if self.scope["kind"]=="folder_subtrees": return self.scope["folders"][0].get("display_name") or self.scope["folders"][0].get("relative_path_key") or "Root"
        return self.scope["kind"]

    # -- Files ---------------------------------------------------------------

    def show_files(self, reset_paging=True):
        if self.conn is None: self.show_welcome(); return
        if reset_paging: self.files_cursors=[None]; self.files_page=0
        self.clear(); self.header("Files","Every current file in the scope. Click a heading to sort; right-click one to filter or choose columns.",back=True)
        tools=ttk.Frame(self.content); tools.pack(fill="x",pady=(0,8))
        ttk.Label(tools,textvariable=self.scope_var,font=("Segoe UI",9,"bold")).pack(side="left")
        if self.filters:
            labels=getattr(self,"filter_labels",None) or []
            ttk.Label(tools,text=" · filtered: "+", ".join(labels) if labels and len(labels)==len(self.filters) else f" · {len(self.filters)} active filter(s)",foreground="#445").pack(side="left")
        if self.search_text: ttk.Label(tools,text=f" · find: {self.search_text!r}",foreground="#445").pack(side="left")
        ttk.Button(tools,text="Clear filters and sort",command=self.clear_query).pack(side="right")
        ttk.Button(tools,text="Export CSV...",command=self.export_files_csv).pack(side="right",padx=6)
        ttk.Button(tools,text="Columns...",command=self.choose_columns).pack(side="right")

        # A classic PanedWindow: it honours minsize, which is what keeps the
        # details pane from being squeezed to nothing.
        panes=tk.PanedWindow(self.content,orient="horizontal",sashrelief="raised",sashwidth=6,bd=0)
        panes.pack(fill="both",expand=True)
        left=ttk.Frame(panes,padding=6); center=ttk.Frame(panes); right=ttk.Frame(panes,padding=8)
        panes.add(left,minsize=180,width=220); panes.add(center,minsize=400); panes.add(right,minsize=300,width=320)
        self._populate_scope_tree(left)
        self._populate_file_table(center)
        self.detail_host=right
        ttk.Label(right,text="Details & Evidence",font=("Segoe UI",10,"bold")).pack(anchor="w")
        ttk.Label(right,text="Select a file.",foreground="#666").pack(anchor="w",pady=8)

    def _subfolders(self, root_id, prefix):
        """The folders directly under `prefix` (a relative path, '' for the root) that hold files."""
        if prefix:
            key=prefix.replace("/","\\").strip("\\").lower()
            rows=self.conn.execute(
                "SELECT DISTINCT substr(fp.relative_path, ?, instr(substr(fp.relative_path, ?), '\\')-1) AS child "
                "FROM file_state fs JOIN file_path fp ON fp.file_path_id=fs.file_path_id "
                "WHERE fs.state='present' AND fs.source_root_id=? AND fp.relative_path_key>=? AND fp.relative_path_key<? "
                "AND instr(substr(fp.relative_path, ?), '\\')>0 ORDER BY lower(child) LIMIT 2000",
                (len(key)+2,len(key)+2,root_id,key+"\\",key+"]",len(key)+2)).fetchall()
        else:
            rows=self.conn.execute(
                "SELECT DISTINCT fo_top_level(fp.relative_path) AS child FROM file_state fs JOIN file_path fp ON fp.file_path_id=fs.file_path_id "
                "WHERE fs.state='present' AND fs.source_root_id=? AND fo_top_level(fp.relative_path)<>'' ORDER BY lower(child) LIMIT 2000",
                (root_id,)).fetchall()
        return [r["child"] for r in rows if r["child"]]

    def _populate_scope_tree(self,parent):
        """Source roots and their folders, expandable to any depth.

        Folders load when their node is opened, so a 41,000-file tree costs
        one small query per click rather than a walk of the whole inventory.
        Selecting a folder scopes the page to that subtree.
        """
        ttk.Label(parent,text="Folders / Source Roots",font=("Segoe UI",9,"bold")).pack(anchor="w",pady=(0,5))
        tree=ttk.Treeview(parent,show="tree",height=25); tree.pack(fill="both",expand=True)
        allid=tree.insert("","end",text="All Sources",values=("project",))
        roots=self.conn.execute("SELECT source_root_id,root_path FROM source_root WHERE project_id=1 AND is_active=1 ORDER BY root_ordinal,root_path").fetchall()
        def add_folder(parent_id,root_id,relative,display):
            node=tree.insert(parent_id,"end",text=display,values=(f"folder:{root_id}:{relative}",))
            tree.insert(node,"end",text="...",values=("placeholder",))     # opens lazily
            return node
        for root in roots:
            rid=tree.insert(allid,"end",text=root["root_path"],values=(f"root:{root['source_root_id']}",))
            for child in self._subfolders(root["source_root_id"],""):
                add_folder(rid,root["source_root_id"],child,child)
        tree.item(allid,open=True)
        def opened(_=None):
            node=tree.focus()
            if not node: return
            kids=tree.get_children(node)
            if len(kids)==1 and tree.item(kids[0],"values") and tree.item(kids[0],"values")[0]=="placeholder":
                tree.delete(kids[0])
                token=tree.item(node,"values")[0]
                _,root_id,relative=token.split(":",2)
                for child in self._subfolders(int(root_id),relative):
                    add_folder(node,int(root_id),relative+"\\"+child,child)
        tree.bind("<<TreeviewOpen>>",opened)
        def selected(_=None):
            sel=tree.selection()
            if not sel:return
            val=tree.item(sel[0],"values")
            if not val or val[0]=="placeholder":return
            token=val[0]
            if token=="project": self.scope={"kind":"project"}
            elif token.startswith("root:"): self.scope={"kind":"source_roots","source_root_ids":[int(token.split(':')[1])]}
            elif token.startswith("folder:"):
                _,root,key=token.split(":",2); self.scope={"kind":"folder_subtrees","folders":[{"source_root_id":int(root),"relative_path_key":key,"display_name":key}]}
            self.scope_var.set("Scope: "+self._scope_label()); self.show_files()
        tree.bind("<<TreeviewSelect>>",selected)

    def _populate_file_table(self,parent):
        columns=[c for c in self.files_columns if c in FILE_COLUMNS] or list(DEFAULT_FILE_COLUMNS)
        host=ttk.Frame(parent); host.pack(fill="both",expand=True)
        host.rowconfigure(0,weight=1); host.columnconfigure(0,weight=1)
        table=ttk.Treeview(host,columns=columns,show="headings")
        sort_field,sort_dir=self.files_sort or (None,None)
        for c in columns:
            heading,width,_kind=FILE_COLUMNS[c]
            if c==sort_field: heading+=" ▲" if sort_dir=="asc" else " ▼"
            table.heading(c,text=heading,command=lambda c=c:self.sort_files_by(c))
            table.column(c,width=width,minwidth=50,anchor="w",stretch=False)
        sy=ttk.Scrollbar(host,orient="vertical",command=table.yview); sx=ttk.Scrollbar(host,orient="horizontal",command=table.xview)
        table.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
        table.grid(row=0,column=0,sticky="nsew"); sy.grid(row=0,column=1,sticky="ns"); sx.grid(row=1,column=0,sticky="ew")
        cursor=self.files_cursors[self.files_page]
        try:
            result=self.engine.list_files(scope=self.scope,search=self.search_text,filters=self.filters,limit=PAGE_SIZE,
                                          sort=self.files_sort,cursor=cursor,columns=columns)
            total=self.engine.count_files(scope=self.scope,search=self.search_text,filters=self.filters)
        except Exception as exc:
            messagebox.showerror("Files",str(exc),parent=self); return
        self.current_query=result["normalized_query"]; self.current_rows=result["rows"]
        for i,row in enumerate(self.current_rows):
            table.insert("","end",iid=str(i),values=[render_cell(c,row.get(c)) for c in columns])
        def choose(_=None):
            sel=table.selection()
            if sel:self.show_file_detail(self.current_rows[int(sel[0])])
        table.bind("<<TreeviewSelect>>",choose)
        table.bind("<Button-3>",lambda e:self._files_header_menu(e,table,columns))
        # Paging, both ways, with honest numbers.
        first=self.files_page*PAGE_SIZE+1 if self.current_rows else 0
        last=self.files_page*PAGE_SIZE+len(self.current_rows)
        bar=ttk.Frame(parent); bar.pack(fill="x",pady=(4,0))
        ttk.Label(bar,text=(f"Files {first:,}–{last:,} of {total:,}" if total else "No files match."),foreground="#444").pack(side="left")
        nxt=ttk.Button(bar,text=f"Next {PAGE_SIZE} ▶",command=self.next_files_page,state="normal" if result.get("next_cursor") else "disabled"); nxt.pack(side="right",padx=4)
        prv=ttk.Button(bar,text=f"◀ Previous {PAGE_SIZE}",command=self.previous_files_page,state="normal" if self.files_page>0 else "disabled"); prv.pack(side="right")
        if result.get("next_cursor"):
            self.files_cursors=self.files_cursors[:self.files_page+1]+[result["next_cursor"]]

    def next_files_page(self):
        if self.files_page+1<len(self.files_cursors):
            self.files_page+=1; self.show_files(reset_paging=False)

    def previous_files_page(self):
        if self.files_page>0:
            self.files_page-=1; self.show_files(reset_paging=False)

    def sort_files_by(self,field,direction=None):
        """Click a heading: sort by it, ascending first, then descending."""
        if direction is None:
            current=self.files_sort
            direction="desc" if current and current[0]==field and current[1]=="asc" else "asc"
        self.files_sort=(field,direction)
        self.show_files()

    def _files_header_menu(self,event,table,columns):
        region=table.identify_region(event.x,event.y)
        col=table.identify_column(event.x)
        if not col: return
        try: field=columns[int(col[1:])-1]
        except (ValueError,IndexError): return
        menu=tk.Menu(self,tearoff=0)
        heading=FILE_COLUMNS[field][0]
        menu.add_command(label=f"Sort by {heading}, ascending",command=lambda:self.sort_files_by(field,"asc"))
        menu.add_command(label=f"Sort by {heading}, descending",command=lambda:self.sort_files_by(field,"desc"))
        menu.add_separator()
        menu.add_command(label=f"Filter by {heading}...",command=lambda:self.filter_column(field))
        menu.add_command(label="Clear all filters",command=self.clear_query,state="normal" if (self.filters or self.search_text) else "disabled")
        menu.add_separator()
        menu.add_command(label="Choose columns...",command=self.choose_columns)
        menu.add_command(label="Export CSV...",command=self.export_files_csv)
        try: menu.tk_popup(event.x_root,event.y_root)
        finally: menu.grab_release()

    def filter_column(self,field):
        """A filter on one column, asked for in the column's own terms.

        Columns with a small set of values (extension, hash status, evidence,
        yes/no) offer the values that actually occur, with counts -- the way a
        spreadsheet's filter dropdown does -- rather than a blank box.
        """
        heading,_w,kind=FILE_COLUMNS[field]
        cond=None
        if kind in ("enum","bool"):
            chosen=self._pick_values(field,heading)
            if chosen is None: return
            if kind=="bool":
                cond={"left":{"kind":"field","id":field},"op":"is_true" if chosen==[1] else "is_false"} if len(chosen)==1 else None
            elif len(chosen)==1:
                cond={"left":{"kind":"field","id":field},"op":"eq","value":{"kind":"literal","value":chosen[0]}}
            else:
                cond={"left":{"kind":"field","id":field},"op":"in","values":[{"kind":"literal","value":v} for v in chosen]}
            if cond is None: return
            self.filters.append({"condition":cond}); self.show_files(); return
        if kind=="text":
            val=simpledialog.askstring(heading,f"{heading} contains:",parent=self)
            if val: cond={"left":{"kind":"field","id":field},"op":"contains","value":{"kind":"literal","value":val}}
        elif kind=="bytes":
            val=simpledialog.askfloat(heading,f"{heading} at least (MB):",parent=self,minvalue=0)
            if val is not None: cond={"left":{"kind":"field","id":field},"op":"gte","value":{"kind":"literal","value":int(val*1024*1024)}}
        elif kind=="int":
            val=simpledialog.askinteger(heading,f"{heading} at least:",parent=self,minvalue=0)
            if val is not None: cond={"left":{"kind":"field","id":field},"op":"gte","value":{"kind":"literal","value":int(val)}}
        elif kind=="datetime":
            val=simpledialog.askstring(heading,f"{heading} before (YYYY-MM-DD), or after (prefix with >):",parent=self)
            if val:
                v=val.strip()
                op="after" if v.startswith(">") else "before"
                v=v.lstrip("> ").strip()
                cond={"left":{"kind":"field","id":field},"op":op,"value":{"kind":"literal","value":v+"T00:00:00Z"}}
        elif kind=="bool":
            yes=messagebox.askyesno(heading,f"Show only files where {heading} is Yes?\n\n(No shows only where it is No.)",parent=self)
            cond={"left":{"kind":"field","id":field},"op":"is_true" if yes else "is_false"}
        if cond is None: return
        self.filters.append({"condition":cond}); self.show_files()

    def _pick_values(self,field,heading):
        """A checklist of the values a column takes in the current question, with counts.

        Returns the chosen values (raw, as stored), or None if cancelled. For a
        yes/no column the values are 1 and 0.
        """
        try:
            # Values are drawn from the page's question minus any filter already
            # on this column, so a second pick can widen as well as narrow.
            others=[f for f in self.filters if not (f.get("condition") and f["condition"].get("left",{}).get("id")==field)]
            values=self.engine.field_values(field,scope=self.scope,search=self.search_text,filters=others)
        except Exception as exc:
            messagebox.showerror(heading,str(exc),parent=self); return None
        if not values:
            messagebox.showinfo(heading,"No values in the current scope.",parent=self); return None
        win=tk.Toplevel(self); win.title(f"Filter by {heading}"); win.transient(self); win.grab_set()
        ttk.Label(win,text=f"Show files whose {heading} is:",font=("Segoe UI",10,"bold")).pack(anchor="w",padx=12,pady=(12,6))
        host=scrollable_frame(win)
        ticks=[]
        def show(v):
            if v is None: return "(none)"
            return render_cell(field,v) if FILE_COLUMNS[field][2]=="bool" else str(v)
        for value,count in values:
            var=tk.BooleanVar(value=False); ticks.append((value,var))
            ttk.Checkbutton(host,text=f"{show(value)}   ({count:,})",variable=var).pack(anchor="w",padx=6)
        result={"chosen":None}
        def apply():
            chosen=[v for v,var in ticks if var.get() and v is not None]
            if not chosen: messagebox.showinfo(heading,"Tick at least one value.",parent=win); return
            result["chosen"]=chosen; win.destroy()
        def select_all(state):
            for _v,var in ticks: var.set(state)
        row=ttk.Frame(win); row.pack(fill="x",padx=12,pady=12)
        ttk.Button(row,text="Apply",command=apply).pack(side="left")
        ttk.Button(row,text="All",command=lambda:select_all(True)).pack(side="left",padx=(6,0))
        ttk.Button(row,text="None",command=lambda:select_all(False)).pack(side="left",padx=(6,0))
        ttk.Button(row,text="Cancel",command=win.destroy).pack(side="left",padx=6)
        win.geometry("420x520"); self.wait_window(win)
        try: self.unbind_all("<MouseWheel>")
        except tk.TclError: pass
        return result["chosen"]

    def choose_columns(self):
        """Tick the columns the Files table shows, with a line on what each one means."""
        win=tk.Toplevel(self); win.title("Columns"); win.transient(self); win.grab_set()
        ttk.Label(win,text="Show these columns:",font=("Segoe UI",10,"bold")).pack(anchor="w",padx=12,pady=(12,6))
        host=scrollable_frame(win)
        vars_={}
        section_done=set()
        for field,(heading,_w,_k) in FILE_COLUMNS.items():
            section="From analysis (empty until the bucket is analysed)" if field.startswith("analysis.") else "From the file system and fingerprints"
            if section not in section_done:
                section_done.add(section)
                ttk.Label(host,text=section,font=("Segoe UI",9,"bold"),foreground="#444").pack(anchor="w",padx=6,pady=(8,2))
            v=tk.BooleanVar(value=field in self.files_columns); vars_[field]=v
            ttk.Checkbutton(host,text=heading,variable=v).pack(anchor="w",padx=12)
            ttk.Label(host,text=COLUMN_HELP.get(field,""),foreground="#666",wraplength=520,justify="left").pack(anchor="w",padx=36)
        def apply():
            chosen=[f for f in FILE_COLUMNS if vars_[f].get()]
            if not chosen: messagebox.showinfo("Columns","Keep at least one column.",parent=win); return
            self.files_columns=chosen; win.destroy(); self.show_files(reset_paging=False)
        row=ttk.Frame(win); row.pack(fill="x",padx=12,pady=12)
        ttk.Button(row,text="Apply",command=apply).pack(side="left")
        ttk.Button(row,text="Cancel",command=win.destroy).pack(side="left",padx=6)
        win.geometry("620x700"); self.wait_window(win)
        try: self.unbind_all("<MouseWheel>")
        except tk.TclError: pass

    def export_files_csv(self):
        """Every file the current question covers -- not just this page -- to a CSV the person chooses."""
        if self.conn is None: return
        columns=[c for c in self.files_columns if c in FILE_COLUMNS] or list(DEFAULT_FILE_COLUMNS)
        exports=self.project_dir/"Exports"; exports.mkdir(exist_ok=True)
        stamp=datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path=filedialog.asksaveasfilename(parent=self,title="Export files to CSV",initialdir=str(exports),
                                          initialfile=f"files_{self.info.get('name',self.project_dir.name)}_{stamp}.csv",
                                          defaultextension=".csv",filetypes=[("CSV","*.csv")])
        if not path: return
        try:
            n=export_files(self.engine,path,columns,scope=self.scope,search=self.search_text,filters=self.filters,sort=self.files_sort)
        except Exception as exc:
            messagebox.showerror("Export CSV",str(exc),parent=self); return
        messagebox.showinfo("Export CSV",f"{n:,} file(s) written to:\n{path}",parent=self)

    def show_file_detail(self,row):
        for w in self.detail_host.winfo_children(): w.destroy()
        ttk.Label(self.detail_host,text="Details & Evidence",font=("Segoe UI",10,"bold")).pack(anchor="w")
        pairs=[(FILE_COLUMNS[c][0],render_cell(c,row.get(c))) for c in self.files_columns if c in FILE_COLUMNS and c in row]
        for k,v in pairs:
            f=ttk.Frame(self.detail_host); f.pack(fill="x",pady=2)
            ttk.Label(f,text=k+":",width=14,foreground="#666").pack(side="left",anchor="n")
            ttk.Label(f,text=str(v),wraplength=200,justify="left").pack(side="left",fill="x")
        ttk.Separator(self.detail_host).pack(fill="x",pady=8)
        ttk.Button(self.detail_host,text="Metadata Explorer",command=lambda:self.show_metadata(row.get("path.id"),row.get("path.file_name") or "")).pack(anchor="w")
        ttk.Label(self.detail_host,text="Shown from stored project evidence.\nThe source file is not reopened.",foreground="#666",justify="left").pack(anchor="w",pady=10)

    def show_metadata(self,file_path_id,file_name=""):
        """Everything the analyzers recorded about this file, as it is now.

        The first version showed five columns per analyzer and the user asked
        where all the data was. This shows every field every analyzer stored:
        the promoted values, every detail field, the error if the attempt
        failed, the extracted-text reference, and an archive's summary and
        members. From stored evidence; the file is not reopened.
        """
        win=tk.Toplevel(self); win.title(f"Metadata Explorer -- {file_name}" if file_name else "Metadata Explorer"); win.geometry("820x640")
        text=tk.Text(win,wrap="word",font=("Consolas",9)); sy=ttk.Scrollbar(win,orient="vertical",command=text.yview); text.configure(yscrollcommand=sy.set)
        text.pack(side="left",fill="both",expand=True,padx=(8,0),pady=8); sy.pack(side="right",fill="y",pady=8)
        text.tag_configure("h",font=("Segoe UI",11,"bold")); text.tag_configure("k",foreground="#555"); text.tag_configure("err",foreground="#8a1c1c"); text.tag_configure("dim",foreground="#888")
        try: entries=self.engine.file_analysis(file_path_id)
        except Exception as exc: messagebox.showerror("Metadata",str(exc),parent=win); return
        if not entries:
            text.insert("end","No analyzer has a result for this file yet.\n\nAnalysis is per bucket: run Analyze on the file's type from the project page.",("dim",))
        for e in entries:
            text.insert("end",f"{e['label']}\n",("h",))
            text.insert("end",f"  status: {e['status']}    analysed: {e['analyzed_utc'] or ''}    run: {e['run_folder'] or ''}\n",("dim",))
            if e["error"]: text.insert("end",f"  {e['error']}\n",("err",))
            for k,v in e["fields"]:
                text.insert("end",f"  {k}: ",("k",)); text.insert("end",f"{v}\n")
            if e["text"]:
                t=e["text"]; text.insert("end",f"  extracted text: {t['status']}, {t.get('word_count') or 0:,} words, {t.get('char_count') or 0:,} characters, artifact {'present' if t.get('artifact_exists') else 'missing'}\n",("k",))
            if e["archive"]:
                a=e["archive"]; text.insert("end",f"  archive: {a['entry_total_count']:,} entries ({a['analysis_mode']}), {human_bytes(a['total_uncompressed_bytes'])} uncompressed, {human_bytes(a['total_compressed_bytes'])} compressed\n",("k",))
                for m in e["members"]:
                    text.insert("end",f"    {m['entry_path']}  {human_bytes(m['entry_size_bytes'])}\n")
                if a["entry_total_count"] and a["entry_total_count"]>len(e["members"]):
                    text.insert("end",f"    ... {a['entry_total_count']-len(e['members']):,} more (all are in the database; export the archive report for the full list)\n",("dim",))
            text.insert("end","\n")
        text.configure(state="disabled")

    def apply_find(self):
        if self.conn is None: return
        self.search_text=self.find_var.get().strip(); self.show_files()

    def add_filter(self):
        if self.conn is None: return
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
            messagebox.showinfo("Add Filter","That quick filter is not implemented yet. Right-click a column heading on the Files page to filter by that column.")
            return
        self.show_files()

    def clear_query(self):
        self.filters=[]; self.filter_labels=[]; self.search_text=""; self.find_var.set(""); self.files_sort=None
        self.scope={"kind":"project"}; self.scope_var.set("Scope: All Sources"); self.show_files()

    def save_current_query(self):
        if self.conn is None: return
        q=self.current_query or base_file_query(self.scope)
        # Remove execution-only normalization keys.
        q={k:v for k,v in q.items() if not k.startswith("_")}
        name=simpledialog.askstring("Save Query","Name this question:",parent=self)
        if not name:return
        try:
            rec=self.store.create(name,q); messagebox.showinfo("Saved Query",f"Saved as revision {rec['revision_number']}.")
        except Exception as exc: messagebox.showerror("Saved Query",str(exc))

    # -- Reports -------------------------------------------------------------

    def show_reports(self):
        if self.conn is None: self.show_welcome(); return
        self.clear(); self.header("Reports",f"{len(self.reports.reports)} standard reports. Each runs in the current scope ({self._scope_label()}).",back=True)
        tools=ttk.Frame(self.content); tools.pack(fill="x",pady=(0,8)); search=tk.StringVar(); family=tk.StringVar(value="All")
        ttk.Label(tools,text="Find:").pack(side="left")
        ent=ttk.Entry(tools,textvariable=search,width=30); ent.pack(side="left",padx=(4,10))
        fams=["All"]+sorted({r["family"] for r in self.reports.reports}); cb=ttk.Combobox(tools,textvariable=family,values=fams,state="readonly",width=18); cb.pack(side="left")
        host=scrollable_frame(self.content)
        def redraw(*_):
            for w in host.winfo_children():w.destroy()
            rows=self.reports.list(None if family.get()=="All" else family.get(),search=search.get())
            for i,r in enumerate(rows):
                # Run first, then the name: the button sits next to what it runs.
                ttk.Button(host,text="Run",width=6,command=lambda rid=r["report_id"]:self.run_report(rid)).grid(row=i,column=0,sticky="w",pady=2,padx=(0,8))
                ttk.Label(host,text=r["title"],font=("Segoe UI",9,"bold")).grid(row=i,column=1,sticky="w",padx=(0,12))
                ttk.Label(host,text=f"{r['family']} · {r['tier']}",foreground="#666").grid(row=i,column=2,sticky="w",padx=(0,12))
                ttk.Label(host,text=r["counting_unit"],foreground="#666").grid(row=i,column=3,sticky="w")
            if not rows: ttk.Label(host,text="No report matches.").grid(row=0,column=0,sticky="w")
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
        self.clear(); self.header(r["title"],r["purpose"],back=True)
        line=ttk.Frame(self.content); line.pack(fill="x",pady=(0,8))
        ttk.Label(line,text=f"Scope: {self._scope_label()} · Unit: {r['counting_unit']}",foreground="#666").pack(side="left")
        ttk.Button(line,text="◀ Reports",command=self.show_reports).pack(side="right")
        ttk.Button(line,text="Export CSV...",command=lambda:self.export_result_csv(result,r["report_id"])).pack(side="right",padx=6)
        self._generic_result_table(result)

    def export_result_csv(self,result,stem):
        rows=result.get("rows") or []
        if not rows: messagebox.showinfo("Export CSV","There are no rows to export.",parent=self); return
        exports=self.project_dir/"Exports"; exports.mkdir(exist_ok=True)
        stamp=datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path=filedialog.asksaveasfilename(parent=self,title="Export to CSV",initialdir=str(exports),
                                          initialfile=f"{stem}_{stamp}.csv",defaultextension=".csv",filetypes=[("CSV","*.csv")])
        if not path: return
        try: n=write_rows_csv(path,rows)
        except Exception as exc: messagebox.showerror("Export CSV",str(exc),parent=self); return
        messagebox.showinfo("Export CSV",f"{n:,} row(s) written to:\n{path}",parent=self)

    def _generic_result_table(self,result):
        rows=result["rows"]
        if not rows:
            ttk.Label(self.content,text="No matching results.").pack(anchor="w"); return
        cols=list(rows[0])
        frame=ttk.Frame(self.content); frame.pack(fill="both",expand=True)
        tv=ttk.Treeview(frame,columns=cols,show="headings")
        for c in cols: tv.heading(c,text=c); tv.column(c,width=150,anchor="w",stretch=False)
        sy=ttk.Scrollbar(frame,orient="vertical",command=tv.yview); sx=ttk.Scrollbar(frame,orient="horizontal",command=tv.xview); tv.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
        tv.grid(row=0,column=0,sticky="nsew"); sy.grid(row=0,column=1,sticky="ns"); sx.grid(row=1,column=0,sticky="ew"); frame.rowconfigure(0,weight=1); frame.columnconfigure(0,weight=1)
        for row in rows[:5000]: tv.insert("","end",values=[human_bytes(row[c]) if c.endswith("bytes") and isinstance(row[c],(int,float)) else row[c] for c in cols])
        ttk.Label(self.content,text=f"{len(rows):,} row/group(s) · Coverage: {result['coverage']['coverage']}",foreground="#666").pack(anchor="w",pady=5)

    # -- Saved queries -------------------------------------------------------

    def show_saved(self):
        if self.conn is None: self.show_welcome(); return
        self.clear(); self.header("Saved Queries",
            "Standard reports are the questions that ship with the product. A saved query is a question you built "
            "yourself on the Files page -- scope, find text and filters -- and saved by name. Both are queries; both "
            "re-run against the current evidence every time, so rows are never frozen.",back=True)
        host=scrollable_frame(self.content)
        saved=self.store.list()
        if not saved: ttk.Label(host,text="Nothing saved yet. On the Files page, set a scope and filters, then press Save Query.",foreground="#666").grid(row=0,column=0,sticky="w")
        for i,q in enumerate(saved):
            ttk.Button(host,text="Run",width=6,command=lambda uid=q["saved_query_uid"]:self.run_saved(uid)).grid(row=i,column=0,sticky="w",pady=3,padx=(0,8))
            ttk.Label(host,text=q["name"],font=("Segoe UI",9,"bold")).grid(row=i,column=1,sticky="w",padx=(0,12))
            ttk.Label(host,text=f"Revision {q['revision_number']} · {q['updated_utc']}",foreground="#666").grid(row=i,column=2,sticky="w")

    def run_saved(self,uid):
        try:
            rec=self.store.get_latest(uid); result=self.engine.execute(rec["query"],retain_kind="saved_query",saved_revision_id=rec["saved_query_revision_id"])
        except Exception as exc: messagebox.showerror("Saved Query",str(exc)); return
        self.clear(); self.header(rec["name"],"Saved query · re-run against current evidence",back=True)
        line=ttk.Frame(self.content); line.pack(fill="x",pady=(0,8))
        ttk.Button(line,text="◀ Saved queries",command=self.show_saved).pack(side="right")
        ttk.Button(line,text="Export CSV...",command=lambda:self.export_result_csv(result,"saved_query")).pack(side="right",padx=6)
        self._generic_result_table(result)

    # -- Evidence, History ---------------------------------------------------

    def show_evidence(self):
        if self.conn is None: self.show_welcome(); return
        self.clear(); self.header("Evidence","What the project knows, what it does not know, and why.",back=True)
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
        if self.conn is None: self.show_welcome(); return
        self.clear(); self.header("History","Stored observations — separate from Current State.",back=True)
        q={"query_schema":QUERY_SCHEMA,"semantic_contract":SEMANTIC_CONTRACT,"subject":{"entity":"file","temporal":{"mode":"history"}},"scope":{"kind":"project"},
           "where":{"condition":{"left":{"kind":"field","id":"observation.change_kind"},"op":"is_not_null"}},
           "sort":[{"ref":{"kind":"field","id":"observation.observed_utc"},"direction":"desc","nulls":"last"}],"semantic_limit":1000}
        try:self._generic_result_table(self.engine.execute(q))
        except Exception as exc:messagebox.showerror("History",str(exc))

    # -- closing -------------------------------------------------------------

    def close(self):
        """The window's X, and Exit. A run in progress is stopped cleanly first, never abandoned.

        The worker is a daemon thread, so closing without waiting would kill
        it mid-file and leave a half-written result -- exactly what the
        between-files stop exists to prevent. So: ask, stop, wait for the
        current file, then close.
        """
        if self.active_run is not None:
            stop_event,thread=self.active_run
            if thread.is_alive():
                if not messagebox.askyesno("Run in progress",
                        "A run is in progress.\n\nStop it and close? Everything already done is "
                        "kept, and the run is recorded as stopped.\n\nChoose No to let it continue.",
                        icon="warning",parent=self):
                    return
                stop_event.set()
                self.clear(); self.header("Stopping...","Waiting for the current file to finish.")
                self.update_idletasks()
                self._wait_then_destroy(thread)
                return
        self.release_connection()
        self.destroy()

    def _wait_then_destroy(self, thread, waited=0.0):
        if thread.is_alive() and waited<600:
            self.after(250,lambda:self._wait_then_destroy(thread,waited+0.25)); return
        self.release_connection()
        self.destroy()


# ---------------------------------------------------------------------------
# Helpers kept as functions so the headless checks can call them.
# ---------------------------------------------------------------------------

def enable_fault_log(app_root):
    r"""Arm faulthandler on Logs\faulthandler.log.

    A crash inside a native library -- an image decoder on a malformed file,
    say -- takes the whole process with it and, under pythonw, leaves no
    trace at all. The first real corpus session ended in exactly that kind
    of silence. With this armed, a native fault writes every thread's Python
    stack to the file before the process dies, so the next one is not a
    mystery.
    """
    import faulthandler
    try:
        logs=Path(app_root)/"Logs"; logs.mkdir(parents=True,exist_ok=True)
        handle=open(logs/"faulthandler.log","a",encoding="utf-8")
        handle.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} pid {os.getpid()} armed ===\n"); handle.flush()
        faulthandler.enable(file=handle,all_threads=True)
        return handle
    except Exception:
        return None


def scrollable_frame(parent):
    """A frame that scrolls vertically: the reports list has 31 rows, and a
    window shows about twenty. Returns the inner frame to fill."""
    outer=ttk.Frame(parent); outer.pack(fill="both",expand=True)
    canvas=tk.Canvas(outer,highlightthickness=0,bd=0)
    bar=ttk.Scrollbar(outer,orient="vertical",command=canvas.yview)
    inner=ttk.Frame(canvas)
    inner.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all")))
    window=canvas.create_window((0,0),window=inner,anchor="nw")
    canvas.bind("<Configure>",lambda e:canvas.itemconfigure(window,width=e.width))
    canvas.configure(yscrollcommand=bar.set)
    canvas.pack(side="left",fill="both",expand=True); bar.pack(side="right",fill="y")
    def wheel(event):
        # The list may already be gone when a late wheel turn arrives (the
        # <Leave> that would have unbound it never fires for a destroyed
        # widget). Unbind and stop rather than raise.
        try:
            if not canvas.winfo_exists(): raise tk.TclError
            canvas.yview_scroll(int(-event.delta/120),"units")
        except tk.TclError:
            try: canvas.unbind_all("<MouseWheel>")
            except tk.TclError: pass
    canvas.bind("<Enter>",lambda e:canvas.bind_all("<MouseWheel>",wheel))
    canvas.bind("<Leave>",lambda e:canvas.unbind_all("<MouseWheel>"))
    return inner


def csv_cell(value):
    """A cell a spreadsheet will not run.

    A file named =1+1.txt is a formula the moment the export is opened in
    Excel; so is a name beginning with + - or @, and a tab or return can be
    stripped by an importer to expose one. Such a cell gets a leading
    apostrophe -- visible in the sheet, and the price of an export that
    cannot execute anything (matrix Y-018 / Y-019, found 2026-09-13).
    Numbers are left alone: a size is never a formula.
    """
    if isinstance(value,str) and value[:1] in ("=","+","-","@","\t","\r"):
        return "'"+value
    return value


def write_rows_csv(path, rows, columns=None):
    """Write dict rows to a CSV (UTF-8 with BOM, so Excel reads it). Returns the row count."""
    columns=list(columns or (rows[0].keys() if rows else []))
    n=0
    with open(path,"w",encoding="utf-8-sig",newline="") as handle:
        writer=csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(["" if row.get(c) is None else csv_cell(row.get(c)) for c in columns]); n+=1
    return n


def export_files(engine, path, columns, scope=None, search=None, filters=None, sort=None, page=5000):
    """Every file the question covers, page after page, to one CSV. Returns the count.

    Walks the same sorted keyset pages the Files page uses, so the export is
    the whole answer in the same order -- never just the page on screen.
    """
    columns=list(columns)
    n=0
    cursor=None
    with open(path,"w",encoding="utf-8-sig",newline="") as handle:
        writer=csv.writer(handle)
        writer.writerow([FILE_COLUMNS.get(c,(c,))[0] for c in columns])
        while True:
            result=engine.list_files(scope=scope,search=search,filters=filters,limit=page,sort=sort,cursor=cursor,columns=columns)
            for row in result["rows"]:
                writer.writerow(["" if row.get(c) is None else csv_cell(row.get(c)) for c in columns]); n+=1
            cursor=result.get("next_cursor")
            if not cursor: break
    return n


def list_projects(projects_dir=None):
    """Project folders on disk, by name. A project is a folder with a project.json."""
    root=Path(projects_dir) if projects_dir else PROJECTS_DIR
    if not root.is_dir(): return []
    return sorted((p.name for p in root.iterdir() if p.is_dir() and (p/"project.json").is_file()),key=str.lower)


def default_project_name(projects_dir=None):
    """Mirrors the old dashboard's Get-DefaultProjectName: New-Project, New-Project (2), ..."""
    root=Path(projects_dir) if projects_dir else PROJECTS_DIR
    candidate="New-Project"; counter=2
    while (root/candidate).exists():
        candidate=f"New-Project ({counter})"; counter+=1
    return candidate


def validate_folder(target_path):
    """Check a folder BEFORE anything is created. Returns (ok, title, message).

    "Not found" and "cannot be accessed" are told apart deliberately: they
    call for different responses -- retype the path, versus check
    permissions or plug the drive in -- and reporting a permissions problem
    as a missing folder sends a person to look for something that is right
    where they left it.
    """
    if not os.path.exists(target_path) or not os.path.isdir(target_path):
        return (False,"Folder not found",
                "The folder could not be found. Check the location and try again."
                +("" if not os.path.exists(target_path) else "\n\n(That location exists, but it is a file, not a folder.)"))
    try:
        os.scandir(target_path).close()
    except PermissionError:
        return (False,"Folder cannot be accessed",
                "The folder was found but could not be opened. You may not have permission to read it.")
    except OSError as exc:
        return (False,"Folder cannot be accessed",
                f"The folder was found but could not be opened.\n\n{exc.strerror or exc}\n\n"
                "If it is on a removable or network drive, check that the drive is still connected.")
    return (True,None,None)
