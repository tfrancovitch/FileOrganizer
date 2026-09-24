r"""The Exact-Duplicate Review -- Phase 3's first surface, inside the one window.

Reached from the project summary (the "Review duplicates" button on the
Decide line), from the side panel (Decide), and from a file's Details pane
on the Files page ("Review this duplicate group"). Not a window of its own:
the user should not know Phase 3 was ever a separate program, the same rule
Phase 1 and 2 were merged under.

Per group the page shows what Phase 2 knows -- file locations, physical
copies, hard-link aliases, size, roots -- and what Phase 3 has decided:
protection, keepers, the canonical, redundant candidates, the policy
recommendation (labelled as such), conflicts, and the reclaim a plan could
count on. The actions are the research's minimum set: Keep, Keep All, Set
Canonical, Mark Redundant Candidate, Defer, and -- through its own dialog --
Override Protection. Every one records a row and re-resolves; every one has
Undo (a withdrawal row; nothing is ever deleted).

Build 3 (B9) adds the routes: each group's Status is its primary route --
Conflict, Needs revalidation, Blocked, Deferred, Ready for plan, Resolved,
or the ordinary queue (In progress / Unreviewed) -- from `routing.py`, with
every condition that applies listed beside it. The Show box filters by
route or by lens. Defer takes a return trigger; Skip is a different key
and records nothing but an audit row; Restore returns a parked group; Confirm
decisions re-records decisions whose evidence moved. The detector runs when
the page opens, so the record of what it found is current before the page
draws.

Build 4 (B10) adds the first multi-select the product has had: a check
column on the group list (click it, or Space on the focused row), a running
"N checked" count, and -- only once something is checked -- Bulk action...,
which applies one decision to many groups after a categorized preview
(`bulk.py`): what would be recorded, what already holds, what an explicit
decision preserves, what would conflict, what the evidence blocks. The
scope is the checked groups, or every current match of the Show filter,
frozen at commit -- or, for a folder action, a reusable policy instead,
which is the opposite thing and is named as such. Batches... lists every
batch with Undo. The Add policy dialog now previews what a policy covers
and changes today, and says that future matches will be evaluated against
it, before it can be created. The letter keys act on the selected row only,
checked or not; a check never changes what a keystroke does.

Recording a decision is instant, so there is no estimate and no progress
screen: that pattern belongs to the collection runs.

Nothing on this page renames, moves, deletes or edits a source file. The
words "Keeper" and "Canonical" describe retention and representation
decisions; no copy is ever called the original.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from . import bulk as BK
from . import evidence as E
from . import resolve as R
from . import routing as RT
from .registry import (LOCATION, GROUP, DECISION_KINDS, POLICY_KINDS, BULK_ACTIONS, SOURCE_ROOT, DEFER_DISPOSITIONS,
                       SCOPE_EXPLICIT_SELECTION, SCOPE_QUERY_SNAPSHOT, ORIGIN_BULK_EXPLICIT_HUMAN, DISP_DECIDED)
from .store import DecisionStore, new_command_id, export_decision_journal

STATUS_WORDS = {R.PROTECTED: "Protected", R.KEEPER: "Keeper", R.REDUNDANT: "Redundant candidate", R.UNDECIDED: "Undecided"}
REVIEW_WORDS = {R.UNREVIEWED: "Unreviewed", R.IN_PROGRESS: "In progress", R.DEFERRED: "Deferred",
                R.RESOLVED: "Resolved", R.CONFLICT: "Conflict"}
#: The Show box: the routes (synthesis §2, §6), then the lenses -- filters
#: over the same groups, not separate workflows. "Queue" is the primary
#: queue: groups no special condition has routed elsewhere. The filter
#: itself is a pure function in bulk.py (a snapshot batch freezes it).
ROUTE_FILTERS = BK.ROUTE_FILTERS
LENSES = BK.LENSES
FILTERS = BK.FILTERS
HIGH_RECLAIM_BYTES = BK.HIGH_RECLAIM_BYTES
CHECKED, UNCHECKED = "\u2611", "\u2610"
CONDITION_WORDS = {
    R.PROTECTED_VS_REDUNDANT: "protected vs redundant", R.CANONICAL_NO_LONGER_KEEPER: "canonical no longer a keeper",
    R.SAME_PRECEDENCE_POLICY_TIE: "policy tie", R.MINIMUM_FILE_LOCATIONS_KIND: "last copy", R.MINIMUM_PHYSICAL_COPIES_KIND: "last physical copy",
    "drift": "evidence changed", RT.PHYSICAL_IDENTITY_UNKNOWN: "identity unknown", RT.STALE_CONTENT_HASH: "fingerprint stale",
    RT.INCOMPLETE_ROOT_COVERAGE: "root coverage unusable", RT.RECORDED_BLOCK: "blocked (recorded)", "coverage_warning": "coverage warnings",
    "time": "snoozed until a date", "evidence_change": "snoozed until evidence changes", "source_available": "snoozed until source available",
    "hash_current": "snoozed until fingerprints current", "manual": "deferred", "preview_available": "deferred until preview",
    "ready": "", "kept": "", "in_progress": "", "unreviewed": "",
}
RULE_WORDS = {
    "protect_source_root": "protected root", "protect_folder_subtree": "protected folder",
    "explicit_must_keep_location": "Keep", "explicit_canonical_location": "Canonical",
    "explicit_keep_all_group": "Keep all", "explicit_redundant_location": "Mark redundant",
}

#: The group list's columns: key -> (heading, width, sort key on a GroupProjection)
GROUP_COLUMNS = {
    "check": ("\u2713", 30, lambda p: 0),                      # replaced by the checked set at sort time
    "file": ("File", 210, lambda p: _file_name(p.members[0].path).lower() if p.members else ""),
    "copies": ("Copies", 60, lambda p: p.location_count),
    "physical": ("Physical", 65, lambda p: p.physical_copies if p.physical_copies is not None else -1),
    "aliases": ("Aliases", 60, lambda p: p.hardlink_aliases if p.hardlink_aliases is not None else -1),
    "size": ("Size", 85, lambda p: p.size_bytes or 0),
    "potential": ("Potential", 90, lambda p: p.potential_reclaim_bytes or 0),
    "eligible": ("Eligible", 90, lambda p: p.plan_eligible_reclaim_bytes or 0),
    "roots": ("Roots", 50, lambda p: len({m.root_key for m in p.members})),
    "state": ("Status", 110, lambda p: p.review_state),        # replaced by the route order at sort time
    "conditions": ("Conditions", 220, lambda p: ""),           # replaced by the badge text at sort time
    "canonical": ("Canonical", 190, lambda p: p.effective_canonical or ""),
}
MEMBER_COLUMNS = [("status", "Status", 130), ("file", "File", 170), ("folder", "Folder", 260),
                  ("root", "Root", 150), ("protection", "Protection", 130), ("decided", "Decided by", 120),
                  ("policy", "Policy", 80)]


_file_name = BK.file_name
_folder = BK.folder_of


def human_bytes(value):
    if value is None:
        return "Unknown"
    n = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{int(n):,} B"
        n /= 1024


def store_for(app):
    """The app's decision store, built on its current connection."""
    store = getattr(app, "p3_store", None)
    if store is None or store.conn is not app.conn:
        store = DecisionStore(app.conn, session_id=getattr(app, "p3_session_id", None))
        app.p3_store = store
    return store


def decision_summary(conn):
    """The numbers the project summary shows on its Decide line -- the
    resolver's totals plus the routes' counts."""
    ev = E.load_evidence(conn)
    proj = R.resolve_all(ev)
    t = dict(proj.totals)
    t["reviewed"] = t["groups"] - t["unreviewed"]
    routing = RT.route_all(ev, proj, ev.now)
    t["routes"] = {route: routing.counts[route] for route in RT.ROUTE_ORDER}
    return t


def group_for_file(conn, file_path_id):
    row = conn.execute("SELECT content_id FROM p2_current_duplicate_member WHERE file_path_id=?", (int(file_path_id),)).fetchone()
    return str(row[0]) if row else None


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def show_review(app, content_id=None, file_path_id=None):
    """Duplicate decisions: every current exact-duplicate group, and the
    selected group's members with the actions."""
    if app.conn is None:
        app.show_welcome()
        return
    if file_path_id is not None and content_id is None:
        content_id = group_for_file(app.conn, file_path_id)
        if content_id is None:
            messagebox.showinfo("Duplicate decisions", "This file is not in a current exact-duplicate group.", parent=app)
    app.clear()
    app.header("Duplicate decisions",
               "Which copies to keep. A decision is a record of what should happen; nothing is deleted, moved or "
               "renamed here -- carrying decisions out is a later phase. Protection is a hard constraint: a "
               "protected copy cannot be marked redundant without an explicit, separately confirmed override.",
               back=True)
    page = ReviewPage(app)
    app.review_page = page
    page.build(app.content)
    page.detect()
    page.reload(select=content_id)


def run_detector(conn, store):
    """The routing detector, for the window: after a collection run and when
    the Decide page opens. Never raises -- a failure to write provenance must
    not stop a run from finishing or a page from drawing; it is logged."""
    try:
        return RT.reconcile(conn, store)
    except Exception as exc:                                        # noqa: BLE001
        try:
            import fo_log
            from Phase2.gui import APP_ROOT
            fo_log.get_app_log(str(APP_ROOT)).log("ERROR", f"Routing detector failed: {exc}")
        except Exception:                                           # noqa: BLE001
            pass
        return {"written": 0, "error": str(exc)}


class ReviewPage:
    def __init__(self, app):
        self.app = app
        self.store = store_for(app)
        self.ev = None
        self.proj = None
        self.routing = None
        self.filter_var = tk.StringVar(value=FILTERS[0])
        self.sort = ("potential", "desc")
        self.selected_group = None
        self.selected_member = None
        self.rows_by_iid = {}
        self.checked = set()                    # group ids a person has checked (Build 4)

    # -- layout ---------------------------------------------------------------

    def build(self, parent):
        tools = ttk.Frame(parent)
        tools.pack(fill="x", pady=(0, 8))
        self.summary_var = tk.StringVar(value="")
        ttk.Label(tools, textvariable=self.summary_var, font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Button(tools, text="Export journal...", command=self.export_journal).pack(side="right")
        ttk.Button(tools, text="Batches...", command=self.open_batches).pack(side="right", padx=(6, 0))
        ttk.Button(tools, text="Policies...", command=self.open_policies).pack(side="right", padx=6)
        ttk.Label(tools, text="Show:").pack(side="right", padx=(0, 4))
        cb = ttk.Combobox(tools, textvariable=self.filter_var, values=FILTERS, state="readonly", width=26)
        cb.pack(side="right", padx=(0, 10))
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_list())

        panes = tk.PanedWindow(parent, orient="horizontal", sashrelief="raised", sashwidth=6, bd=0)
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes)
        right = ttk.Frame(panes, padding=(8, 0, 0, 0))
        panes.add(left, minsize=420, width=560)
        panes.add(right, minsize=420)
        self._build_group_table(left)
        self._build_detail(right)

    def _build_group_table(self, host):
        host.rowconfigure(0, weight=1)
        host.columnconfigure(0, weight=1)
        cols = list(GROUP_COLUMNS)
        self.table = ttk.Treeview(host, columns=cols, show="headings", selectmode="browse")
        for key in cols:
            heading, width, _ = GROUP_COLUMNS[key]
            self.table.heading(key, text=heading, command=lambda k=key: self.sort_by(k))
            self.table.column(key, width=width, minwidth=28 if key == "check" else 40, stretch=False,
                              anchor="center" if key == "check" else "e" if key in ("copies", "physical", "aliases", "size", "potential", "eligible", "roots") else "w")
        # The check column: click it, or Space on the focused row. A check
        # never changes what a keystroke does; the bulk path is the dialog.
        self.table.bind("<Button-1>", self._table_click, add="+")
        self.table.bind("<KeyPress-space>", lambda e: self.toggle_check())
        self.table.tag_configure("revalidate", foreground="#7a3e00")
        self.table.tag_configure("blocked", foreground="#555")
        sy = ttk.Scrollbar(host, orient="vertical", command=self.table.yview)
        sx = ttk.Scrollbar(host, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        self.table.bind("<<TreeviewSelect>>", lambda e: self._group_selected())
        self.table.tag_configure("conflict", foreground="#8a1c1c")
        self.table.tag_configure("resolved", foreground="#1f6244")
        self.table.tag_configure("deferred", foreground="#7a5700")
        self.count_var = tk.StringVar(value="")
        ttk.Label(host, textvariable=self.count_var, foreground="#444").grid(row=2, column=0, sticky="w", pady=(4, 0))
        bar = ttk.Frame(host)
        bar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.checked_var = tk.StringVar(value="Nothing checked")
        ttk.Label(bar, textvariable=self.checked_var, font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Button(bar, text="Check all shown", command=self.check_all_shown).pack(side="left", padx=(10, 0))
        ttk.Button(bar, text="Clear", command=self.clear_checks).pack(side="left", padx=(4, 0))
        # Appears only once something is checked (plan necessity 2).
        self.bulk_button = ttk.Button(bar, text="Bulk action...", command=self.bulk_action)

    def _build_detail(self, host):
        self.detail = host
        self.group_title = ttk.Label(host, text="Select a group.", font=("Segoe UI", 11, "bold"), wraplength=520, justify="left")
        self.group_title.pack(anchor="w")
        self.group_facts = ttk.Label(host, text="", foreground="#444", wraplength=520, justify="left")
        self.group_facts.pack(anchor="w", pady=(2, 6))
        self.conflict_label = ttk.Label(host, text="", style="Warn.TLabel", wraplength=520, justify="left")
        self.route_label = ttk.Label(host, text="", foreground="#333", wraplength=520, justify="left")

        mhost = ttk.Frame(host)
        mhost.pack(fill="both", expand=True)
        mhost.rowconfigure(0, weight=1)
        mhost.columnconfigure(0, weight=1)
        cols = [c[0] for c in MEMBER_COLUMNS]
        self.members = ttk.Treeview(mhost, columns=cols, show="headings", selectmode="browse", height=8)
        for key, heading, width in MEMBER_COLUMNS:
            self.members.heading(key, text=heading)
            self.members.column(key, width=width, minwidth=40, anchor="w", stretch=False)
        sy = ttk.Scrollbar(mhost, orient="vertical", command=self.members.yview)
        sx = ttk.Scrollbar(mhost, orient="horizontal", command=self.members.xview)
        self.members.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.members.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        self.members.bind("<<TreeviewSelect>>", lambda e: self._member_selected())
        self.members.tag_configure(R.PROTECTED, foreground="#1a4d8f")
        self.members.tag_configure(R.KEEPER, foreground="#1f6244")
        self.members.tag_configure(R.REDUNDANT, foreground="#8a1c1c")
        for key, fn in (("k", self.keep), ("a", self.keep_all), ("c", self.set_canonical),
                        ("r", self.mark_redundant), ("d", self.defer), ("s", self.skip)):
            self.members.bind(f"<KeyPress-{key}>", lambda e, f=fn: f())
        self.table.bind("<KeyPress-s>", lambda e: self.skip())
        self.table.bind("<KeyPress-d>", lambda e: self.defer())

        # The actions. A location action needs a selected member; a group
        # action does not. Override has its own dialog (the research's
        # own-confirmation rule); Undo withdraws a chosen decision.
        acts = ttk.Frame(host)
        acts.pack(fill="x", pady=(8, 0))
        self.buttons = {}
        for key, text, cmd in (("keep", "Keep", self.keep), ("keep_all", "Keep all", self.keep_all),
                               ("canonical", "Set canonical", self.set_canonical),
                               ("redundant", "Mark redundant", self.mark_redundant),
                               ("defer", "Defer...", self.defer), ("skip", "Skip", self.skip),
                               ("override", "Override protection...", self.override_protection)):
            b = ttk.Button(acts, text=text, command=cmd)
            b.pack(side="left", padx=(0, 6))
            self.buttons[key] = b
        acts2 = ttk.Frame(host)
        acts2.pack(fill="x", pady=(4, 0))
        self.buttons["confirm"] = ttk.Button(acts2, text="Confirm decisions", command=self.confirm_decisions)
        self.buttons["confirm"].pack(side="left", padx=(0, 6))
        ttk.Label(acts2, text="Keys: k keep · a keep all · c canonical · r redundant · d defer · s skip (skip records only that you passed by) · "
                              "Space checks the group -- keys act on the selected row only",
                  foreground="#666", wraplength=520, justify="left").pack(side="left")

        hist = ttk.Frame(host)
        hist.pack(fill="both", expand=True, pady=(10, 0))
        top = ttk.Frame(hist)
        top.pack(fill="x")
        ttk.Label(top, text="Decisions on this group", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.undo_button = ttk.Button(top, text="Undo selected", command=self.undo_selected, state="disabled")
        self.undo_button.pack(side="right")
        hhost = ttk.Frame(hist)
        hhost.pack(fill="both", expand=True)
        hhost.rowconfigure(0, weight=1)
        hhost.columnconfigure(0, weight=1)
        self.history = ttk.Treeview(hhost, columns=("when", "what", "target", "who", "state"), show="headings", selectmode="browse", height=6)
        for key, heading, width in (("when", "When (UTC)", 150), ("what", "Decision", 150), ("target", "On", 190), ("who", "By", 90), ("state", "State", 110)):
            self.history.heading(key, text=heading)
            self.history.column(key, width=width, minwidth=40, anchor="w", stretch=False)
        hy = ttk.Scrollbar(hhost, orient="vertical", command=self.history.yview)
        self.history.configure(yscrollcommand=hy.set)
        self.history.grid(row=0, column=0, sticky="nsew")
        hy.grid(row=0, column=1, sticky="ns")
        self.history.tag_configure("inactive", foreground="#888")
        self.history.bind("<<TreeviewSelect>>", lambda e: self._history_selected())
        self.history_rows = {}

    # -- data -----------------------------------------------------------------

    def detect(self):
        """The routing detector: what the evidence says, written to the record."""
        self.detector_result = run_detector(self.app.conn, self.store)

    def reload(self, select=None):
        """Re-derive everything from the record and redraw."""
        try:
            ev = E.load_evidence(self.app.conn)
            self.ev = ev
            self.proj = R.resolve_all(ev)
            self.routing = RT.route_all(ev, self.proj, ev.now)
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Duplicate decisions", str(exc), parent=self.app)
            return
        self.checked &= {g.group_id for g in self.proj.groups}      # a check on a group that is gone is gone
        t = self.proj.totals
        c = self.routing.counts
        parts = [f"{t['groups']:,} groups", f"{c[RT.UNRESOLVED]:,} in the queue"]
        for route in (RT.CONFLICT, RT.NEEDS_REVALIDATION, RT.BLOCKED, RT.DEFERRED, RT.READY_FOR_PLAN, RT.RESOLVED):
            if c[route]:
                parts.append(f"{c[route]:,} {RT.STATUS_WORDS[route].lower()}")
        reclaim = f"{human_bytes(t['plan_eligible_reclaim_bytes'])} plan-eligible reclaim of {human_bytes(t['potential_reclaim_bytes'])} potential"
        if t["reclaim_unknown_groups"]:
            reclaim += f" (+{t['reclaim_unknown_groups']} groups unknown)"
        parts.append(reclaim)
        if t["orphaned_decisions"]:
            parts.append(f"{t['orphaned_decisions']} decision(s) on files no longer in a group")
        self.summary_var.set(" · ".join(parts))
        self.refresh_list(select=select or self.selected_group)

    def _route(self, p):
        return self.routing.groups[p.group_id]

    def _badges(self, p):
        r = self._route(p)
        words = []
        for c in r.conditions:
            w = CONDITION_WORDS.get(c.kind, c.kind)
            if w and w not in words:
                words.append(w)
        return ", ".join(words)

    def _filtered(self):
        f = self.filter_var.get()
        groups = BK.filter_groups(self.proj, self.routing, f)
        if dict(ROUTE_FILTERS).get(f) == RT.CONFLICT and self.sort == ("potential", "desc"):
            return groups                                          # the route's own order: reclaim at stake, then oldest
        key, direction = self.sort
        if key == "state":
            sort_key = lambda p: RT.ROUTE_ORDER.index(self._route(p).primary)     # noqa: E731
        elif key == "conditions":
            sort_key = self._badges
        elif key == "check":
            sort_key = lambda p: p.group_id in self.checked                       # noqa: E731
        else:
            sort_key = GROUP_COLUMNS[key][2]
        # Identity first, presentation second: ties fall to display order, so
        # the list is the same list every time.
        groups = sorted(groups, key=lambda p: (sort_key(p), p.group_id), reverse=(direction == "desc"))
        return groups

    def _row_values(self, p):
        canonical = ""
        if p.effective_canonical:
            v = p.verdict(p.effective_canonical)
            canonical = _file_name(v.path) if v else p.effective_canonical
            if p.canonical_origin == "policy":
                canonical += " (policy)"
        return [CHECKED if p.group_id in self.checked else UNCHECKED,
                _file_name(p.members[0].path) if p.members else p.group_id,
                f"{p.location_count:,}",
                "?" if p.physical_copies is None else f"{p.physical_copies:,}",
                "?" if p.hardlink_aliases is None else f"{p.hardlink_aliases:,}",
                human_bytes(p.size_bytes), human_bytes(p.potential_reclaim_bytes),
                human_bytes(p.plan_eligible_reclaim_bytes),
                f"{len({m.root_key for m in p.members})}",
                self._route(p).word, self._badges(p), canonical]

    def refresh_list(self, select=None):
        self.table.delete(*self.table.get_children())
        self.rows_by_iid = {}
        key, direction = self.sort
        for k in GROUP_COLUMNS:
            heading = GROUP_COLUMNS[k][0]
            if k == key:
                heading += " ▲" if direction == "asc" else " ▼"
            self.table.heading(k, text=heading)
        groups = self._filtered()
        for p in groups:
            primary = self._route(p).primary
            tag = {RT.CONFLICT: "conflict", RT.NEEDS_REVALIDATION: "revalidate", RT.BLOCKED: "blocked",
                   RT.DEFERRED: "deferred", RT.READY_FOR_PLAN: "resolved", RT.RESOLVED: "resolved"}.get(primary, "")
            iid = self.table.insert("", "end", iid=p.group_id, values=self._row_values(p), tags=(tag,))
            self.rows_by_iid[iid] = p
        self._update_checked()
        f = self.filter_var.get()
        if not groups and f == "Queue":
            # A dynamic queue's empty state is not a milestone: new evidence
            # can reopen it. "Currently clear", never "complete" (P3-A58).
            c = self.routing.counts
            elsewhere = ", ".join(f"{c[r]:,} {RT.STATUS_WORDS[r].lower()}" for r in RT.ROUTE_ORDER if r != RT.UNRESOLVED and c[r])
            self.count_var.set("Currently clear -- no group awaits an ordinary decision" + (f" ({elsewhere})" if elsewhere else "") + ".")
        elif not groups:
            self.count_var.set(f"Nothing on this route or lens ({len(self.proj.groups):,} groups in all).")
        else:
            self.count_var.set(f"{len(groups):,} of {len(self.proj.groups):,} groups shown")
        if select is not None and select not in self.rows_by_iid and self.proj.group(select) is not None:
            # The action just moved this group off the route being shown (a
            # mark made it Ready for plan, say). Keep it in view so the result
            # is seen; the list itself no longer holds it.
            self.table.selection_remove(*self.table.selection())
            self.show_group(select)
            return
        target = select if select in self.rows_by_iid else (groups[0].group_id if groups else None)
        if target is not None:
            self.table.selection_set(target)
            self.table.see(target)
            self.show_group(target)
        else:
            self.selected_group = None
            self.show_group(None)

    def sort_by(self, key):
        direction = "desc" if self.sort[0] == key and self.sort[1] == "asc" else "asc"
        self.sort = (key, direction)
        self.refresh_list(select=self.selected_group)

    def _group_selected(self):
        sel = self.table.selection()
        if sel:
            self.show_group(sel[0])

    # -- the checked set (Build 4) ----------------------------------------------

    def _table_click(self, event):
        if self.table.identify_region(event.x, event.y) == "cell" and self.table.identify_column(event.x) == "#1":
            row = self.table.identify_row(event.y)
            if row:
                self._toggle(row)

    def toggle_check(self):
        row = self.table.focus() or (self.table.selection()[0] if self.table.selection() else None)
        if row:
            self._toggle(row)

    def _toggle(self, group_id):
        if group_id in self.checked:
            self.checked.discard(group_id)
        else:
            self.checked.add(group_id)
        if self.table.exists(group_id):
            self.table.set(group_id, "check", CHECKED if group_id in self.checked else UNCHECKED)
        self._update_checked()

    def check_all_shown(self):
        self.checked |= set(self.table.get_children())
        for iid in self.table.get_children():
            self.table.set(iid, "check", CHECKED)
        self._update_checked()

    def clear_checks(self):
        self.checked.clear()
        for iid in self.table.get_children():
            self.table.set(iid, "check", UNCHECKED)
        self._update_checked()

    def _update_checked(self):
        n = len(self.checked)
        shown = sum(1 for iid in self.table.get_children() if iid in self.checked)
        if not n:
            self.checked_var.set("Nothing checked")
            self.bulk_button.pack_forget()
        else:
            self.checked_var.set(f"{n:,} checked" + (f" ({shown:,} shown)" if shown != n else ""))
            if not self.bulk_button.winfo_manager():
                self.bulk_button.pack(side="left", padx=(10, 0))

    def shown_group_ids(self):
        return list(self.table.get_children())

    def bulk_action(self):
        """One decision over many groups, after a preview (bulk.py)."""
        if self.proj is None:
            return
        self.bulk_dialog = BulkDialog(self.app, self, self.store, self._bulk_done)

    def _bulk_done(self):
        self.checked.clear()
        self.reload(select=self.selected_group)
        try:
            self.app.refresh_evidence_strip()
        except Exception:                                           # noqa: BLE001
            pass

    def open_batches(self):
        self.batches_dialog = BatchesDialog(self.app, self.store, lambda: self.reload(select=self.selected_group))

    # -- the selected group -----------------------------------------------------

    def show_group(self, group_id):
        self.selected_group = group_id
        self.selected_member = None
        self.members.delete(*self.members.get_children())
        self.history.delete(*self.history.get_children())
        self.history_rows = {}
        self.conflict_label.pack_forget()
        self.route_label.pack_forget()
        p = self.proj.group(group_id) if group_id else None
        if p is None:
            self.group_title.configure(text="Select a group.")
            self.group_facts.configure(text="")
            self._set_buttons(None, None)
            return
        route = self._route(p)
        names = sorted({_file_name(m.path) for m in p.members}, key=str.lower)
        title = names[0] + (f"  (+{len(names) - 1} other name{'s' if len(names) > 2 else ''})" if len(names) > 1 else "")
        self.group_title.configure(text=f"{title} -- {route.word}")
        physical = "physical copies unknown" if p.physical_copies is None else (
            f"{p.physical_copies:,} physical cop{'y' if p.physical_copies == 1 else 'ies'}"
            + (f", {p.hardlink_aliases:,} hard-link alias{'es' if p.hardlink_aliases != 1 else ''}" if p.hardlink_aliases else ""))
        facts = [f"{p.location_count:,} file locations in {len({m.root_key for m in p.members})} root(s), {physical}, {human_bytes(p.size_bytes)} each.",
                 f"Keepers {len(p.keeper_set)} (protected {len(p.protected)}) · redundant candidates {len(p.redundant_candidates)} · undecided {len(p.undecided)}.",
                 f"Potential reclaim {human_bytes(p.potential_reclaim_bytes)}; plan-eligible now {human_bytes(p.plan_eligible_reclaim_bytes)}"
                 + (" -- ready for a plan." if p.ready_for_plan else ".")]
        if p.canonical:
            v = p.verdict(p.canonical)
            facts.append(f"Canonical: {v.path if v else p.canonical} (decided).")
        elif p.suggested_canonical:
            v = p.verdict(p.suggested_canonical)
            facts.append(f"Policy would suggest {v.path if v else p.suggested_canonical} as canonical -- a recommendation, not a decision.")
        elif p.resolution_state == R.RS_TIE and len(p.members) > 1 and not p.canonical:
            facts.append("No policy distinguishes these copies; the order shown is presentation only.")
        self.group_facts.configure(text="\n".join(facts))
        if p.conflicts:
            self.conflict_label.configure(text="\n".join(
                f"{'Conflict' if c.severity == R.BLOCKING else 'Exception'}: {c.message}" for c in p.conflicts))
            self.conflict_label.pack(fill="x", pady=(0, 6), before=self.members.master)
        lines = []
        for c in route.conditions:
            if c.route == RT.CONFLICT or c.kind in ("ready", "kept", "in_progress", "unreviewed"):
                continue
            lines.append(f"{RT.STATUS_WORDS.get(c.route, 'Note')}: {c.detail}")
        if route.deferral is not None and route.deferral_fired:
            lines.append(f"Returned to the queue: {route.deferral_fired_why}.")
        if lines:
            self.route_label.configure(text="\n".join(lines))
            self.route_label.pack(fill="x", pady=(0, 6), before=self.members.master)
        for v in p.members:
            protection = ""
            if v.status == R.PROTECTED:
                protection = RULE_WORDS.get(v.rule, v.rule)
            elif v.overridden:
                protection = "overridden"
            decided = RULE_WORDS.get(v.rule, "") if v.rule.startswith("explicit_") else ""
            if v.deferred:
                decided = (decided + ", deferred").strip(", ")
            self.members.insert("", "end", iid=v.location_id, tags=(v.status,), values=[
                STATUS_WORDS.get(v.status, v.status) + (" (canonical)" if p.canonical == v.location_id else ""),
                _file_name(v.path), _folder(v.path), v.root_key, protection, decided,
                {"preferred": "preferred", "avoided": "avoided"}.get(v.policy_tier, "")])
        self._fill_history(p)
        self._set_buttons(p, None)

    def _fill_history(self, p):
        rows = []
        for kind, ref in [(GROUP, p.group_id)] + [(LOCATION, v.location_id) for v in p.members]:
            for d in self.store.history_for(kind, ref):
                rows.append((d, kind, ref))
        rows.sort(key=lambda r: r[0]["decision_id"])
        entries = []
        for d, kind, ref in rows:
            spec = DECISION_KINDS.get(d["decision_kind"])
            what = spec.label if spec else d["decision_kind"]
            if d["decision_kind"] == "canonical_location":
                v = p.verdict(str(d["value"]))
                what += f": {_file_name(v.path) if v else d['value']}"
            if d.get("origin_kind") == ORIGIN_BULK_EXPLICIT_HUMAN:
                what += f" (batch #{d['origin_ref']})"
            target = "the group" if kind == GROUP else _file_name((p.verdict(ref) or p.members[0]).path)
            state = "active" if d["active"] else "withdrawn" if d["withdrawn"] else "superseded"
            entries.append((d["occurred_utc"], 0, int(d["decision_id"]), what, target, d["actor_id"] or "", state, d, ()))
        # Routing events, in the same list: a deferral and its trigger, a
        # skip, what the detector found, a restore. Not undoable -- they are
        # not decisions.
        for kind, ref in [(GROUP, p.group_id)] + [(LOCATION, v.location_id) for v in p.members]:
            for e in self.store.review_events_for(kind, ref):
                what = {"deferred": "Deferred", "skipped": "Skipped", "restored": "Restored", "needs_revalidation": "Detector: evidence changed",
                        "blocked": "Detector: blocked"}.get(e["event_kind"], e["event_kind"])
                if e["return_condition"]:
                    what += f" ({e['return_condition'][:60]})"
                target = "the group" if kind == GROUP else _file_name((p.verdict(ref) or p.members[0]).path)
                who = e["actor_id"] or "" if e["actor_kind"] == "explicit_human" else "the evidence"
                entries.append((e["occurred_utc"], 1, int(e["review_event_id"]), what, target, who, "event", None, ("inactive",)))
        entries.sort(key=lambda x: (x[0], x[1], x[2]))
        for when, _k, _i, what, target, who, state, d, tags in entries:
            iid = self.history.insert("", "end", values=[when[:19].replace("T", " "), what, target, who, state],
                                      tags=tags if d is None else (() if d["active"] else ("inactive",)))
            if d is not None:
                self.history_rows[iid] = d
        self.undo_button.configure(state="disabled")

    def _member_selected(self):
        sel = self.members.selection()
        self.selected_member = sel[0] if sel else None
        self._set_buttons(self.proj.group(self.selected_group) if self.selected_group else None, self.selected_member)

    def _history_selected(self):
        sel = self.history.selection()
        d = self.history_rows.get(sel[0]) if sel else None
        self.undo_button.configure(state="normal" if d and d["active"] else "disabled")

    def _set_buttons(self, p, member):
        v = p.verdict(member) if (p is not None and member) else None
        have_group = p is not None
        for key in ("keep", "canonical", "redundant"):
            self.buttons[key].configure(state="normal" if v is not None else "disabled")
        self.buttons["keep_all"].configure(state="normal" if have_group else "disabled")
        parked = have_group and self._route(p).has(RT.DEFERRED)
        self.buttons["defer"].configure(state="normal" if have_group else "disabled", text="Restore" if parked else "Defer...")
        self.buttons["skip"].configure(state="normal" if have_group else "disabled")
        drifted = have_group and self._route(p).has(RT.NEEDS_REVALIDATION)
        self.buttons["confirm"].configure(state="normal" if drifted else "disabled")
        self.buttons["override"].configure(state="normal" if (v is not None and v.status == R.PROTECTED) else "disabled")

    # -- actions ------------------------------------------------------------------

    def _current(self):
        p = self.proj.group(self.selected_group) if self.selected_group else None
        v = p.verdict(self.selected_member) if (p is not None and self.selected_member) else None
        return p, v

    def _after(self, group_id):
        """Re-derive after a change; keep the selection where the eye is."""
        member = self.selected_member
        self.reload(select=group_id)
        if member and self.members.exists(member):
            self.members.selection_set(member)
            self.members.focus(member)
            self._member_selected()
        self.members.focus_set()
        try:
            self.app.refresh_evidence_strip()
        except Exception:                                           # noqa: BLE001
            pass

    def _record(self, fn, title):
        try:
            fn()
        except ValueError as exc:
            messagebox.showwarning(title, str(exc), parent=self.app)
            return False
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror(title, f"The decision was not recorded.\n\n{exc}", parent=self.app)
            return False
        return True

    def _active_decision_ids(self, verdict, kind):
        return [int(d["decision_id"]) for d in self.store.history_for(LOCATION, verdict.location_id)
                if d["active"] and d["decision_kind"] == kind]

    def keep(self):
        p, v = self._current()
        if v is None:
            return
        cmd = new_command_id()
        if self._record(lambda: self.store.record_decision("must_keep_location", LOCATION, v.location_id, True, command_id=cmd), "Keep"):
            self._after(p.group_id)

    def keep_all(self):
        p, _v = self._current()
        if p is None:
            return
        marks = [d for v in p.members for d in self._active_decision_ids(v, "redundant_location")]
        if marks and not messagebox.askyesno(
                "Keep all", f"{len(marks)} redundant mark(s) on this group will be withdrawn so that every copy is kept. "
                "The marks stay in the record as withdrawn.\n\nContinue?", parent=self.app):
            return
        cmd = new_command_id()
        if self._record(lambda: self.store.record_decision("keep_all_group", GROUP, p.group_id, True, command_id=cmd, withdraw=marks), "Keep all"):
            self._after(p.group_id)

    def set_canonical(self):
        p, v = self._current()
        if v is None:
            return
        if v.status == R.REDUNDANT:
            if not messagebox.askyesno("Set canonical", "This copy is marked a redundant candidate. The canonical must be a keeper, "
                                       "so that mark will be withdrawn.\n\nContinue?", parent=self.app):
                return
        marks = self._active_decision_ids(v, "redundant_location")
        cmd = new_command_id()
        if self._record(lambda: self.store.record_decision("canonical_location", GROUP, p.group_id, v.location_id, command_id=cmd, withdraw=marks), "Set canonical"):
            self._after(p.group_id)

    def mark_redundant(self):
        p, v = self._current()
        if v is None:
            return
        if v.status == R.PROTECTED:
            messagebox.showinfo("Mark redundant",
                                f"This copy is protected ({RULE_WORDS.get(v.rule, v.rule)}). Protection is a hard constraint: "
                                "to mark it redundant, override the protection first -- Override protection... records that "
                                "as its own decision, with a rationale.", parent=self.app)
            return
        if p.canonical == v.location_id:
            messagebox.showinfo("Mark redundant", "This copy is the canonical. Set another canonical first, or undo that decision.", parent=self.app)
            return
        others = [m for m in p.members if m.location_id != v.location_id and m.status != R.REDUNDANT]
        if not others:
            messagebox.showinfo("Mark redundant", "Every other copy is already a redundant candidate. A plan must leave at least one copy; "
                                "the product never removes the last one.", parent=self.app)
            return
        cmd = new_command_id()
        if self._record(lambda: self.store.record_decision("redundant_location", LOCATION, v.location_id, True, command_id=cmd), "Mark redundant"):
            self._after(p.group_id)

    def defer(self):
        """Defer with a return trigger -- or, on a parked group, Restore."""
        p, _v = self._current()
        if p is None:
            return
        route = self._route(p)
        if route.has(RT.DEFERRED):
            self.restore()
            return
        self.defer_dialog = DeferDialog(self.app, p, self.store, lambda: self._after(p.group_id))

    def restore(self):
        """A person returns a parked group to the queue: a restore event that
        ends the open deferral; a B8 defer_review decision is withdrawn in the
        same operation."""
        p, _v = self._current()
        if p is None:
            return
        route = self._route(p)
        legacy = [d["decision_id"] for d in self.store.history_for(GROUP, p.group_id) if d["active"] and d["decision_kind"] == "defer_review"]
        refers_to = route.deferral.review_event_id if route.deferral is not None else None
        if self._record(lambda: self.store.restore(GROUP, p.group_id, refers_to=refers_to, reason="restored by hand",
                                                   command_id=new_command_id(), withdraw=legacy), "Restore"):
            self._after(p.group_id)

    def skip(self):
        """Skip: an audit row, nothing else -- the group stays exactly where it
        is; the selection moves to the next group in the list."""
        p, _v = self._current()
        if p is None:
            return
        if not self._record(lambda: self.store.skip(GROUP, p.group_id, command_id=new_command_id()), "Skip"):
            return
        rows = list(self.table.get_children())
        try:
            nxt = rows[rows.index(p.group_id) + 1]
        except (ValueError, IndexError):
            nxt = rows[0] if rows else None
        self.reload(select=nxt)
        if nxt is not None:
            self.table.focus(nxt)
        self.table.focus_set()

    def confirm_decisions(self):
        """Re-record the decisions whose evidence moved, on current evidence:
        each is a new decision superseding the old (the record keeps both),
        and the revalidation flag clears because the comparison clears --
        not because anyone marked it so. A decision that no longer makes
        sense (a canonical that left the group, a redundant mark on a copy
        that is no longer a duplicate) is left for Undo."""
        p, _v = self._current()
        if p is None:
            return
        route = self._route(p)
        drifted = [x for x in route.drift if not x.applicable]
        for v in p.members:
            lr = self.routing.locations.get(v.location_id)
            if lr is not None:
                drifted.extend(x for x in lr.drift if not x.applicable)
        if not drifted:
            return
        active = {d.decision_id: d for d in self.store.active()}
        members = {v.location_id for v in p.members}
        todo, skipped = [], []
        for x in drifted:
            d = active.get(x.decision_id)
            if d is None:
                continue
            if d.kind == "canonical_location" and str(d.value) not in members:
                skipped.append(f"canonical {d.value}: no longer a member -- set another, or undo it")
            elif d.target_kind == LOCATION and d.target_ref not in members:
                skipped.append(f"{DECISION_KINDS[d.kind].label} on a copy that is no longer in this group -- undo it")
            else:
                todo.append(d)
        if not todo:
            messagebox.showinfo("Confirm decisions", "Nothing here can be confirmed as it stands:\n\n" + "\n".join(skipped), parent=self.app)
            return
        if not messagebox.askyesno("Confirm decisions",
                                   f"Re-record {len(todo)} decision(s) on the evidence as it is now? Each becomes a new "
                                   "decision that replaces the old one; the old rows stay." + ("\n\nLeft alone:\n" + "\n".join(skipped) if skipped else ""),
                                   parent=self.app):
            return
        cmd = new_command_id()

        def work():
            for d in todo:
                if d.kind == "override_protection":
                    self.store.record_override(d.target_ref, d.value["policy_id"], "reconfirmed on current evidence",
                                               command_id=cmd, confirmed=True)
                else:
                    self.store.record_decision(d.kind, d.target_kind, d.target_ref, d.value, rationale="reconfirmed on current evidence",
                                               command_id=cmd)
        if self._record(work, "Confirm decisions"):
            self.detect()
            self._after(p.group_id)

    def undo_selected(self):
        sel = self.history.selection()
        d = self.history_rows.get(sel[0]) if sel else None
        if not d or not d["active"]:
            return
        spec = DECISION_KINDS.get(d["decision_kind"])
        if not messagebox.askyesno("Undo", f"Withdraw '{spec.label if spec else d['decision_kind']}'?\n\nThe decision stops applying; "
                                   "its record is kept, marked withdrawn. A decision it replaced is not revived -- make it again if wanted.",
                                   parent=self.app):
            return
        if self._record(lambda: self.store.withdraw(d["decision_id"], None, command_id=new_command_id()), "Undo"):
            self._after(self.selected_group)

    def override_protection(self):
        p, v = self._current()
        if v is None or v.status != R.PROTECTED:
            return
        covering = [pol for pol in self.store.policies() if str(pol["policy_version_id"]) in v.protected_by]
        if not covering:
            messagebox.showinfo("Override protection", "The protection on this copy comes from no current policy version.", parent=self.app)
            return
        # Kept on the page so the headless checks can drive the real dialog.
        self.override_dialog = OverrideDialog(self.app, v, covering, self.store, lambda: self._after(p.group_id))

    # -- policies & journal ---------------------------------------------------------

    def open_policies(self):
        self.policies_dialog = PoliciesDialog(self.app, self.store, lambda: self.reload(select=self.selected_group))

    def export_journal(self):
        exports = self.app.project_dir / "Exports"
        exports.mkdir(exist_ok=True)
        path = filedialog.asksaveasfilename(parent=self.app, title="Export decision journal", initialdir=str(exports),
                                            initialfile="DecisionJournal.txt", defaultextension=".txt",
                                            filetypes=[("Text", "*.txt")])
        if not path:
            return
        try:
            n = export_decision_journal(self.app.conn, path)
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Export journal", str(exc), parent=self.app)
            return
        messagebox.showinfo("Export journal", f"{n:,} operation(s) written to\n{path}", parent=self.app)


# ---------------------------------------------------------------------------
# Defer: a return trigger, chosen
# ---------------------------------------------------------------------------

class DeferDialog:
    def __init__(self, app, group, store, on_done):
        self.app, self.group, self.store, self.on_done = app, group, store, on_done
        win = tk.Toplevel(app)
        self.win = win
        win.title("Defer")
        win.transient(app)
        win.grab_set()
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Not deciding yet", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, text="A deferral is your choice, with a trigger that returns the group to the queue. "
                              "It is not a decision about any copy, and it is different from Skip, which only "
                              "records that you passed by.", wraplength=520, justify="left", foreground="#444").pack(anchor="w", pady=(2, 10))
        self.choice = tk.StringVar(value="defer_indefinitely")
        for key, (rk, label, why) in DEFER_DISPOSITIONS.items():
            row = ttk.Frame(frame)
            row.pack(fill="x", pady=1)
            ttk.Radiobutton(row, text=label, variable=self.choice, value=key, command=self._update).pack(side="left")
            ttk.Label(row, text=why, foreground="#666").pack(side="left", padx=(8, 0))
        drow = ttk.Frame(frame)
        drow.pack(fill="x", pady=(6, 0))
        ttk.Label(drow, text="Date (YYYY-MM-DD):").pack(side="left")
        self.date_var = tk.StringVar(value="")
        self.date_entry = ttk.Entry(drow, textvariable=self.date_var, width=14, state="disabled")
        self.date_entry.pack(side="left", padx=(6, 0))
        ttk.Label(frame, text="Note (optional):", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(10, 2))
        self.note = tk.Text(frame, height=2, width=64, wrap="word")
        self.note.pack(fill="x")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(14, 0))
        ttk.Button(row, text="Defer", command=self._confirm).pack(side="right")
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 8))
        self._update()

    def _update(self):
        self.date_entry.configure(state="normal" if self.choice.get() == "snooze_until_date" else "disabled")

    def _confirm(self):
        disposition = self.choice.get()
        note = self.note.get("1.0", "end").strip() or None
        try:
            self.store.defer(GROUP, self.group.group_id, disposition, until=self.date_var.get().strip() or None,
                             note=note, command_id=new_command_id())
        except ValueError as exc:
            messagebox.showwarning("Defer", str(exc), parent=self.win)
            return
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Defer", f"The deferral was not recorded.\n\n{exc}", parent=self.win)
            return
        self.win.destroy()
        self.on_done()


# ---------------------------------------------------------------------------
# Override protection: its own confirmation, naming the rule it defeats
# ---------------------------------------------------------------------------

class OverrideDialog:
    def __init__(self, app, verdict, policies, store, on_done):
        self.app, self.verdict, self.policies, self.store, self.on_done = app, verdict, policies, store, on_done
        win = tk.Toplevel(app)
        self.win = win
        win.title("Override protection")
        win.transient(app)
        win.grab_set()
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Override protection for one copy", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, text=verdict.path, wraplength=560, justify="left").pack(anchor="w", pady=(4, 10))
        ttk.Label(frame, text="Protection is a hard constraint. This records an explicit override of ONE rule for THIS copy only, "
                              "with your reason; only then can the copy be marked a redundant candidate. Nothing is deleted "
                              "by this or by any decision.", wraplength=560, justify="left", foreground="#444").pack(anchor="w")
        ttk.Label(frame, text="Rule to override:", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(10, 2))
        self.choice = tk.IntVar(value=int(policies[0]["policy_id"]))
        for pol in policies:
            ttk.Radiobutton(frame, text=f"{pol['label']}: {pol['scope_text']}  (policy #{pol['policy_id']} v{pol['version_no']})",
                            variable=self.choice, value=int(pol["policy_id"])).pack(anchor="w")
        ttk.Label(frame, text="Why (required):", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(10, 2))
        self.reason = tk.Text(frame, height=3, width=70, wrap="word")
        self.reason.pack(fill="x")
        self.reason.bind("<KeyRelease>", lambda e: self._update())
        self.ack = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="I understand this copy will no longer be protected by that rule.",
                        variable=self.ack, command=self._update).pack(anchor="w", pady=(10, 0))
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(14, 0))
        self.ok = ttk.Button(row, text="Override protection", command=self._confirm, state="disabled")
        self.ok.pack(side="right")
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 8))
        self.reason.focus_set()

    def _update(self):
        ready = self.ack.get() and bool(self.reason.get("1.0", "end").strip())
        self.ok.configure(state="normal" if ready else "disabled")

    def _confirm(self):
        reason = self.reason.get("1.0", "end").strip()
        try:
            self.store.record_override(self.verdict.location_id, self.choice.get(), reason,
                                       command_id=new_command_id(), confirmed=bool(self.ack.get()))
        except ValueError as exc:
            messagebox.showwarning("Override protection", str(exc), parent=self.win)
            return
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Override protection", f"The override was not recorded.\n\n{exc}", parent=self.win)
            return
        self.win.destroy()
        self.on_done()


# ---------------------------------------------------------------------------
# Policies: protect / prefer / avoid, over a source root or a folder
# ---------------------------------------------------------------------------

class PoliciesDialog:
    def __init__(self, app, store, on_change):
        self.app, self.store, self.on_change = app, store, on_change
        win = tk.Toplevel(app)
        self.win = win
        win.title("Policies")
        win.transient(app)
        win.geometry("760x460")
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Policies", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Protection is a hard constraint: a protected copy cannot become a redundant candidate without an "
                              "explicit override. Preference and avoidance only shape the recommendation; they never decide "
                              "for you. Each change is a new version; a retired policy keeps its history.",
                  wraplength=720, justify="left", foreground="#444").pack(anchor="w", pady=(2, 8))
        host = ttk.Frame(frame)
        host.pack(fill="both", expand=True)
        host.rowconfigure(0, weight=1)
        host.columnconfigure(0, weight=1)
        self.table = ttk.Treeview(host, columns=("id", "kind", "scope", "over", "status", "version", "since"), show="headings", selectmode="browse")
        for key, heading, width in (("id", "#", 40), ("kind", "Policy", 150), ("scope", "Scope", 260), ("over", "Over", 150),
                                    ("status", "Status", 70), ("version", "Version", 60), ("since", "Since (UTC)", 140)):
            self.table.heading(key, text=heading)
            self.table.column(key, width=width, minwidth=40, anchor="w", stretch=False)
        sy = ttk.Scrollbar(host, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=sy.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        self.table.bind("<<TreeviewSelect>>", lambda e: self._selected())
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="Add...", command=self.add).pack(side="left")
        self.retire_button = ttk.Button(row, text="Retire", command=self.retire, state="disabled")
        self.retire_button.pack(side="left", padx=6)
        self.revive_button = ttk.Button(row, text="Reactivate", command=self.revive, state="disabled")
        self.revive_button.pack(side="left")
        ttk.Button(row, text="Close", command=win.destroy).pack(side="right")
        self.refresh()

    def refresh(self):
        self.table.delete(*self.table.get_children())
        for pol in self.store.policies(include_retired=True):
            self.table.insert("", "end", iid=str(pol["policy_id"]), values=[
                pol["policy_id"], pol["label"], pol["scope_text"], pol["effect"].get("over", ""),
                pol["status"], f"v{pol['version_no']}", (pol["occurred_utc"] or "")[:19].replace("T", " ")])
        self._selected()

    def _selected(self):
        sel = self.table.selection()
        pol = self.store.policy(sel[0]) if sel else None
        self.retire_button.configure(state="normal" if pol and pol["status"] == "active" else "disabled")
        self.revive_button.configure(state="normal" if pol and pol["status"] == "retired" else "disabled")

    def add(self):
        AddPolicyDialog(self.win, self.app, self.store, self._changed)

    def retire(self):
        sel = self.table.selection()
        if not sel:
            return
        pol = self.store.policy(sel[0])
        if pol and messagebox.askyesno("Retire policy", f"Retire '{pol['label']}: {pol['scope_text']}'?\n\nIt stops applying; its history is kept.", parent=self.win):
            self.store.retire_policy(pol["policy_id"], "retired", command_id=new_command_id())
            self._changed()

    def revive(self):
        sel = self.table.selection()
        if not sel:
            return
        pol = self.store.policy(sel[0])
        if pol:
            self.store.revise_policy(pol["policy_id"], rationale="reactivated", command_id=new_command_id())
            self._changed()

    def _changed(self):
        self.refresh()
        self.on_change()


class AddPolicyDialog:
    """Create a reusable policy -- deliberately. Before it can be created the
    dialog shows what it covers and changes today and says, in so many
    words, that future matching evidence will be evaluated against it
    (synthesis §2): a policy must not be made by someone who thinks it
    only affects what is visible now."""

    def __init__(self, parent, app, store, on_done, kind=None, folder=None):
        self.app, self.store, self.on_done = app, store, on_done
        self.previewed = None                   # the inputs the preview was computed for
        win = tk.Toplevel(parent)
        self.win = win
        win.title("Add policy")
        win.transient(parent)
        win.grab_set()
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Kind:", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        self.kinds = list(POLICY_KINDS)
        self.kind_var = tk.StringVar(value=POLICY_KINDS[kind].label if kind in POLICY_KINDS else POLICY_KINDS[self.kinds[0]].label)
        cb = ttk.Combobox(frame, textvariable=self.kind_var, values=[POLICY_KINDS[k].label for k in self.kinds], state="readonly", width=28)
        cb.grid(row=0, column=1, sticky="w", pady=2)
        cb.bind("<<ComboboxSelected>>", lambda e: self._kind_changed())
        self.desc = ttk.Label(frame, text="", wraplength=520, justify="left", foreground="#444")
        self.desc.grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 8))
        ttk.Label(frame, text="Scope:", font=("Segoe UI", 9, "bold")).grid(row=2, column=0, sticky="w")
        self.roots = [dict(r) for r in app.conn.execute(
            "SELECT source_root_id, root_path, root_path_key FROM source_root WHERE project_id=1 AND is_active=1 ORDER BY root_ordinal, root_path")]
        self.root_var = tk.StringVar(value=self.roots[0]["root_path"] if self.roots else "")
        self.root_box = ttk.Combobox(frame, textvariable=self.root_var, values=[r["root_path"] for r in self.roots], state="readonly", width=60)
        self.folder_var = tk.StringVar(value=str(folder or ""))
        self.folder_row = ttk.Frame(frame)
        ttk.Entry(self.folder_row, textvariable=self.folder_var, width=52).pack(side="left")
        ttk.Button(self.folder_row, text="Browse...", command=lambda: self._browse(self.folder_var)).pack(side="left", padx=4)
        self.over_label = ttk.Label(frame, text="Over folder (optional):", font=("Segoe UI", 9, "bold"))
        self.over_var = tk.StringVar(value="")
        self.over_row = ttk.Frame(frame)
        ttk.Entry(self.over_row, textvariable=self.over_var, width=52).pack(side="left")
        ttk.Button(self.over_row, text="Browse...", command=lambda: self._browse(self.over_var)).pack(side="left", padx=4)
        ttk.Label(frame, text="Why (optional):", font=("Segoe UI", 9, "bold")).grid(row=5, column=0, sticky="nw", pady=(8, 0))
        self.reason = tk.Text(frame, height=2, width=60, wrap="word")
        self.reason.grid(row=5, column=1, columnspan=2, sticky="w", pady=(8, 0))
        prow = ttk.Frame(frame)
        prow.grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 2))
        ttk.Label(prow, text="What it would do:", font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Button(prow, text="Preview", command=self.preview).pack(side="left", padx=(8, 0))
        self.preview_text = tk.Text(frame, height=7, width=78, wrap="word", state="disabled", background="#f6f6f6", relief="flat")
        self.preview_text.grid(row=7, column=0, columnspan=3, sticky="ew")
        row = ttk.Frame(frame)
        row.grid(row=8, column=0, columnspan=3, sticky="e", pady=(14, 0))
        self.ok = ttk.Button(row, text="Create policy", command=self._confirm, state="disabled")
        self.ok.pack(side="right")
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 8))
        for var in (self.root_var, self.folder_var, self.over_var):
            var.trace_add("write", lambda *a: self._invalidate())
        self._kind_changed()

    def _kind(self):
        label = self.kind_var.get()
        for k in self.kinds:
            if POLICY_KINDS[k].label == label:
                return POLICY_KINDS[k]
        return POLICY_KINDS[self.kinds[0]]

    def _kind_changed(self):
        kind = self._kind()
        self.desc.configure(text=kind.description)
        self._invalidate()
        self.root_box.grid_forget()
        self.folder_row.grid_forget()
        self.over_label.grid_forget()
        self.over_row.grid_forget()
        if kind.scope_kind == SOURCE_ROOT:
            self.root_box.grid(row=2, column=1, columnspan=2, sticky="w", pady=2)
        else:
            self.folder_row.grid(row=2, column=1, columnspan=2, sticky="w", pady=2)
            if kind.key == "prefer_folder_subtree":
                self.over_label.grid(row=3, column=0, sticky="w")
                self.over_row.grid(row=3, column=1, columnspan=2, sticky="w", pady=2)

    def _browse(self, var):
        start = var.get() or (self.roots[0]["root_path"] if self.roots else "")
        path = filedialog.askdirectory(parent=self.win, initialdir=start or None, title="Choose a folder")
        if path:
            var.set(path.replace("/", "\\"))

    def _inputs(self):
        """(kind, scope, effect) as the store will take them, or None after a warning."""
        kind = self._kind()
        from .model import path_key
        if kind.scope_kind == SOURCE_ROOT:
            root = next((r for r in self.roots if r["root_path"] == self.root_var.get()), None)
            if root is None:
                messagebox.showwarning("Add policy", "Choose a source root.", parent=self.win)
                return None
            scope = {"root_key": root["root_path_key"] or path_key(root["root_path"]), "root_path": root["root_path"]}
        else:
            folder = self.folder_var.get().strip()
            if not folder:
                messagebox.showwarning("Add policy", "Choose a folder.", parent=self.win)
                return None
            scope = {"path_key": path_key(folder), "path": folder}
        effect = {}
        over = self.over_var.get().strip()
        if kind.key == "prefer_folder_subtree" and over:
            effect = {"over_path_key": path_key(over), "over": over}
        return kind, scope, effect

    def _key(self):
        return (self.kind_var.get(), self.root_var.get(), self.folder_var.get().strip(), self.over_var.get().strip())

    def _invalidate(self):
        """Any change to what the policy says voids the preview: the button
        that creates it is enabled only for the inputs previewed."""
        if getattr(self, "ok", None) is None:
            return
        self.previewed = None
        self.ok.configure(state="disabled")
        self._show("Preview to see what this policy covers and changes today, before creating it.")

    def _show(self, text):
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", text)
        self.preview_text.configure(state="disabled")

    def preview(self):
        got = self._inputs()
        if got is None:
            return
        kind, scope, effect = got
        try:
            self.impact = BK.policy_preview(self.app.conn, kind.key, scope, effect)
        except ValueError as exc:
            messagebox.showwarning("Add policy", str(exc), parent=self.win)
            return
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Add policy", f"The preview failed.\n\n{exc}", parent=self.win)
            return
        self._show("\n".join(self.impact["lines"]))
        self.previewed = self._key()
        self.ok.configure(state="normal")

    def _confirm(self):
        if self.previewed != self._key():
            self._invalidate()
            messagebox.showinfo("Add policy", "Preview first: what this policy covers and changes today, and that future matches "
                                "will be evaluated against it.", parent=self.win)
            return
        got = self._inputs()
        if got is None:
            return
        kind, scope, effect = got
        reason = self.reason.get("1.0", "end").strip() or None
        try:
            self.store.create_policy(kind.key, scope, effect, rationale=reason, command_id=new_command_id())
        except ValueError as exc:
            messagebox.showwarning("Add policy", str(exc), parent=self.win)
            return
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Add policy", f"The policy was not recorded.\n\n{exc}", parent=self.win)
            return
        self.win.destroy()
        self.on_done()


# ---------------------------------------------------------------------------
# Bulk action: one decision, many groups, after a preview (Build 4)
# ---------------------------------------------------------------------------

class BulkDialog:
    """The three choices the research asks for, in so many words: the N
    checked groups; the N current matches of the Show filter (frozen at
    commit); or a reusable policy instead, which affects future matches
    too. The narrowest available option is the default. Nothing is
    recorded without a preview, and the commit records exactly what the
    preview showed."""

    def __init__(self, app, page, store, on_done):
        self.app, self.page, self.store, self.on_done = app, page, store, on_done
        self.preview = None
        self.previewed = None
        win = tk.Toplevel(app)
        self.win = win
        win.title("Bulk action")
        win.transient(app)
        win.grab_set()
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="One decision, many groups -- after a preview", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Nothing is recorded until you confirm the preview. A batch never overwrites an explicit decision: the "
                              "exceptions it meets are counted and kept in its record, not skipped in silence. Nothing here renames, "
                              "moves or deletes a file.", wraplength=640, justify="left", foreground="#444").pack(anchor="w", pady=(2, 10))

        arow = ttk.Frame(frame)
        arow.pack(fill="x")
        ttk.Label(arow, text="Action:", font=("Segoe UI", 9, "bold")).pack(side="left")
        self.actions = list(BULK_ACTIONS)
        self.action_var = tk.StringVar(value=BULK_ACTIONS[self.actions[0]].label)
        cb = ttk.Combobox(arow, textvariable=self.action_var, values=[BULK_ACTIONS[k].label for k in self.actions], state="readonly", width=36)
        cb.pack(side="left", padx=(8, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self._action_changed())
        self.desc = ttk.Label(frame, text="", wraplength=640, justify="left", foreground="#444")
        self.desc.pack(anchor="w", pady=(2, 6))

        # parameters, shown per action
        self.folder_row = ttk.Frame(frame)
        ttk.Label(self.folder_row, text="Folder:", font=("Segoe UI", 9, "bold")).pack(side="left")
        self.folder_var = tk.StringVar(value="")
        ttk.Entry(self.folder_row, textvariable=self.folder_var, width=58).pack(side="left", padx=(8, 4))
        ttk.Button(self.folder_row, text="Browse...", command=self._browse).pack(side="left")
        self.folder_var.trace_add("write", lambda *a: self._inputs_changed())
        self.defer_row = ttk.Frame(frame)
        self.defer_var = tk.StringVar(value="defer_indefinitely")
        for key, (rk, label, why) in DEFER_DISPOSITIONS.items():
            r = ttk.Frame(self.defer_row)
            r.pack(fill="x")
            ttk.Radiobutton(r, text=label, variable=self.defer_var, value=key, command=self._inputs_changed).pack(side="left")
            ttk.Label(r, text=why, foreground="#666").pack(side="left", padx=(8, 0))
        drow = ttk.Frame(self.defer_row)
        drow.pack(fill="x", pady=(4, 0))
        ttk.Label(drow, text="Date (YYYY-MM-DD):").pack(side="left")
        self.date_var = tk.StringVar(value="")
        ttk.Entry(drow, textvariable=self.date_var, width=14).pack(side="left", padx=(6, 0))
        self.date_var.trace_add("write", lambda *a: self._inputs_changed())
        self.mark_row = ttk.Frame(frame)
        self.mark_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.mark_row, text="Also mark the other undecided copies redundant candidates (protected and kept copies stay)",
                        variable=self.mark_var, command=self._inputs_changed).pack(side="left")

        # scope: the three choices, narrowest first
        ttk.Label(frame, text="Apply to:", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(8, 2))
        self.scope_var = tk.StringVar(value="checked" if page.checked else "matches")
        self.scope_buttons = {}
        for key in ("checked", "matches", "policy"):
            b = ttk.Radiobutton(frame, text="", variable=self.scope_var, value=key, command=self._inputs_changed)
            b.pack(anchor="w")
            self.scope_buttons[key] = b

        prow = ttk.Frame(frame)
        prow.pack(fill="x", pady=(10, 2))
        ttk.Label(prow, text="Preview:", font=("Segoe UI", 9, "bold")).pack(side="left")
        self.preview_button = ttk.Button(prow, text="Preview", command=self.run_preview)
        self.preview_button.pack(side="left", padx=(8, 0))
        self.preview_text = tk.Text(frame, height=11, width=86, wrap="word", state="disabled", background="#f6f6f6", relief="flat")
        self.preview_text.pack(fill="both", expand=True)
        ttk.Label(frame, text="Note (optional):", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(8, 2))
        self.note = tk.Text(frame, height=2, width=86, wrap="word")
        self.note.pack(fill="x")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(14, 0))
        self.ok = ttk.Button(row, text="Record", command=self._confirm, state="disabled")
        self.ok.pack(side="right")
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 8))
        self._action_changed()

    # -- state ------------------------------------------------------------------

    def _action(self):
        label = self.action_var.get()
        for k in self.actions:
            if BULK_ACTIONS[k].label == label:
                return BULK_ACTIONS[k]
        return BULK_ACTIONS[self.actions[0]]

    def _params(self):
        spec = self._action()
        params = {}
        if spec.needs_folder:
            params["folder"] = self.folder_var.get().strip()
        if spec.key == "defer":
            params["disposition"] = self.defer_var.get()
            params["until"] = self.date_var.get().strip() or None
        if spec.key == "accept_recommendation":
            params["mark_others"] = bool(self.mark_var.get())
        return params

    def _key(self):
        return (self._action().key, tuple(sorted(self._params().items())), self.scope_var.get(), self.page.filter_var.get(),
                tuple(sorted(self.page.checked)))

    def _action_changed(self):
        spec = self._action()
        self.desc.configure(text=spec.description)
        for r in (self.folder_row, self.defer_row, self.mark_row):
            r.pack_forget()
        if spec.needs_folder:
            self.folder_row.pack(fill="x", pady=(2, 0), after=self.desc)
        if spec.key == "defer":
            self.defer_row.pack(fill="x", after=self.desc)
        if spec.key == "accept_recommendation":
            self.mark_row.pack(fill="x", after=self.desc)
        self._inputs_changed()

    def _inputs_changed(self):
        spec = self._action()
        n_checked = len(self.page.checked)
        shown = self.page.shown_group_ids()
        show = self.page.filter_var.get()
        self.scope_buttons["checked"].configure(
            text=f"{n_checked:,} checked group{'s' if n_checked != 1 else ''}",
            state="normal" if n_checked else "disabled")
        self.scope_buttons["matches"].configure(
            text=f"{len(shown):,} current match{'es' if len(shown) != 1 else ''} (Show: {show}) -- frozen at commit; a later match is not included",
            state="normal" if shown else "disabled")
        twin = POLICY_KINDS.get(spec.policy_twin) if spec.policy_twin else None
        self.scope_buttons["policy"].configure(
            text=(f"Create a reusable policy instead ({twin.label}: future matches too, no decision recorded)" if twin
                  else "Create a reusable policy instead (no policy says this)"),
            state="normal" if twin else "disabled")
        if self.scope_var.get() == "checked" and not n_checked:
            self.scope_var.set("matches")
        if self.scope_var.get() == "policy" and not twin:
            self.scope_var.set("checked" if n_checked else "matches")
        if self.previewed != self._key():
            self.preview, self.previewed = None, None
            if self.scope_var.get() == "policy":
                self._show("A policy is the opposite of a snapshot: it is evaluated against every current and future match. "
                           "Continue to the policy dialog, which previews what it covers today before creating it.")
                self.ok.configure(state="normal", text="Continue to the policy...")
                self.preview_button.configure(state="disabled")
            else:
                self._show("Preview to see what would be recorded, what already holds, what is preserved, what would conflict "
                           "and what the evidence blocks -- with examples.")
                self.ok.configure(state="disabled", text="Record")
                self.preview_button.configure(state="normal")

    def _show(self, text):
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", text)
        self.preview_text.configure(state="disabled")

    def _browse(self):
        start = self.folder_var.get() or ""
        path = filedialog.askdirectory(parent=self.win, initialdir=start or None, title="Choose a folder")
        if path:
            self.folder_var.set(path.replace("/", "\\"))

    # -- preview and commit ---------------------------------------------------------

    def run_preview(self):
        spec = self._action()
        page = self.page
        scope = self.scope_var.get()
        try:
            if scope == "checked":
                pv = BK.preview(page.ev, page.proj, page.routing, spec.key, self._params(), SCOPE_EXPLICIT_SELECTION,
                                selection=sorted(page.checked), conn=self.app.conn)
            else:
                query = BK.snapshot_query(show=page.filter_var.get())
                pv = BK.preview(page.ev, page.proj, page.routing, spec.key, self._params(), SCOPE_QUERY_SNAPSHOT, query, conn=self.app.conn)
        except ValueError as exc:
            messagebox.showwarning("Bulk action", str(exc), parent=self.win)
            return
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Bulk action", f"The preview failed.\n\n{exc}", parent=self.win)
            return
        self.preview, self.previewed = pv, self._key()
        self._show("\n".join(pv.lines()))
        n = pv.decision_count if not spec.records_events else pv.event_count
        if n:
            self.ok.configure(state="normal", text=f"Record {n:,} {'deferral' if spec.records_events else 'decision'}{'s' if n != 1 else ''}")
        else:
            self.ok.configure(state="disabled", text="Nothing to record")

    def _confirm(self):
        spec = self._action()
        if self.scope_var.get() == "policy":
            folder = self.folder_var.get().strip()
            self.win.destroy()
            self.page.policy_dialog = AddPolicyDialog(self.app, self.app, self.store, lambda: self.page.reload(select=self.page.selected_group),
                                                      kind=spec.policy_twin, folder=folder)
            return
        if self.preview is None or self.previewed != self._key():
            self._inputs_changed()
            messagebox.showinfo("Bulk action", "Preview first; the batch records exactly what the preview shows.", parent=self.win)
            return
        note = self.note.get("1.0", "end").strip() or None
        try:
            result = self.store.commit_bulk(self.preview, note=note, command_id=new_command_id())
        except ValueError as exc:
            messagebox.showwarning("Bulk action", str(exc), parent=self.win)
            return
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Bulk action", f"The batch was not recorded.\n\n{exc}", parent=self.win)
            return
        self.result = result
        self.win.destroy()
        exceptions = len(self.preview.candidates) - self.preview.counts[DISP_DECIDED]
        recorded = (f"{result['events']:,} deferral(s)" if result["events"] and not result["decisions"]
                    else f"{result['decisions']:,} decision(s)" + (f", {result['events']:,} deferral(s)" if result["events"] else ""))
        messagebox.showinfo("Bulk action",
                            f"Batch #{result['batch_id']} recorded: {recorded} over {result['members']:,} {self.preview.member_word}"
                            + (f"; {exceptions:,} left as exceptions (see Batches...)" if exceptions else "")
                            + ".\n\nUndo the whole batch any time from Batches...", parent=self.app)
        self.on_done()


# ---------------------------------------------------------------------------
# Batches: every bulk batch, with Undo (Build 4)
# ---------------------------------------------------------------------------

class BatchesDialog:
    def __init__(self, app, store, on_change):
        self.app, self.store, self.on_change = app, store, on_change
        self._labels = {}
        win = tk.Toplevel(app)
        self.win = win
        win.title("Bulk batches")
        win.transient(app)
        win.geometry("900x560")
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Bulk batches", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, text="A batch is one previewed decision over many targets, its membership frozen at commit: a target that comes "
                              "to match the same filter later is not a member. Undo withdraws what the batch recorded and still stands "
                              "(a decision you have since changed by hand is left alone); the batch and its members stay in the record.",
                  wraplength=860, justify="left", foreground="#444").pack(anchor="w", pady=(2, 8))
        host = ttk.Frame(frame)
        host.pack(fill="both", expand=True)
        host.rowconfigure(0, weight=1)
        host.columnconfigure(0, weight=1)
        cols = ("id", "when", "action", "scope", "members", "recorded", "force", "note")
        self.table = ttk.Treeview(host, columns=cols, show="headings", selectmode="browse", height=7)
        for key, heading, width in (("id", "#", 40), ("when", "When (UTC)", 140), ("action", "Action", 200), ("scope", "Scope", 250),
                                    ("members", "Members", 70), ("recorded", "Recorded", 70), ("force", "In force", 70), ("note", "Note", 200)):
            self.table.heading(key, text=heading)
            self.table.column(key, width=width, minwidth=40, anchor="e" if key in ("members", "recorded", "force") else "w", stretch=False)
        sy = ttk.Scrollbar(host, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=sy.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        self.table.bind("<<TreeviewSelect>>", lambda e: self._selected())
        ttk.Label(frame, text="Members of the selected batch", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(10, 2))
        mhost = ttk.Frame(frame)
        mhost.pack(fill="both", expand=True)
        mhost.rowconfigure(0, weight=1)
        mhost.columnconfigure(0, weight=1)
        self.members = ttk.Treeview(mhost, columns=("target", "disposition", "detail"), show="headings", selectmode="browse", height=7)
        for key, heading, width in (("target", "Target", 320), ("disposition", "What became of it", 200), ("detail", "Detail", 330)):
            self.members.heading(key, text=heading)
            self.members.column(key, width=width, minwidth=40, anchor="w", stretch=False)
        my = ttk.Scrollbar(mhost, orient="vertical", command=self.members.yview)
        self.members.configure(yscrollcommand=my.set)
        self.members.grid(row=0, column=0, sticky="nsew")
        my.grid(row=0, column=1, sticky="ns")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(8, 0))
        self.undo_button = ttk.Button(row, text="Undo batch", command=self.undo, state="disabled")
        self.undo_button.pack(side="left")
        ttk.Button(row, text="Close", command=win.destroy).pack(side="right")
        self.refresh()

    def refresh(self):
        self.table.delete(*self.table.get_children())
        self.batches = {str(b["batch_id"]): b for b in self.store.batches()}
        for bid, b in self.batches.items():
            scope = BK.describe_query(b["query"], b["parameters"]) if b["query"] else f"{len(b['parameters'].get('selection', [])):,} checked groups"
            if b["parameters"].get("folder") and not b["query"]:
                scope += f"; copies under {b['parameters']['folder']}"
            recorded = b["decisions"] if not BULK_ACTIONS[b["action"]].records_events else b["events"]
            self.table.insert("", "end", iid=bid, values=[
                b["batch_id"], (b["occurred_utc"] or "")[:19].replace("T", " "), b["label"], scope,
                f"{b['members']:,}", f"{recorded:,}", f"{b['in_force']:,}", b["note"] or ""])
        first = next(iter(self.batches), None)
        if first is not None:
            self.table.selection_set(first)
        self._selected()

    def _label(self, target_kind, ref):
        key = (target_kind, str(ref))
        if key not in self._labels:
            try:
                if target_kind == LOCATION:
                    r = self.app.conn.execute(
                        "SELECT sr.root_path, fp.relative_path FROM file_path fp JOIN source_root sr ON sr.source_root_id = fp.source_root_id "
                        " WHERE fp.file_path_id = ?", (int(ref),)).fetchone()
                    self._labels[key] = E.full_path(r["root_path"], r["relative_path"]) if r else f"location {ref}"
                else:
                    r = self.app.conn.execute(
                        "SELECT fp.relative_path FROM p2_current_duplicate_member m JOIN file_path fp ON fp.file_path_id = m.file_path_id "
                        " WHERE m.content_id = ? ORDER BY fp.path_sort_key LIMIT 1", (int(ref),)).fetchone()
                    self._labels[key] = (_file_name(r["relative_path"]) + " (group)") if r else f"group {ref} (no longer current)"
            except Exception:                                       # noqa: BLE001
                self._labels[key] = f"{target_kind} {ref}"
        return self._labels[key]

    def _selected(self):
        sel = self.table.selection()
        b = self.batches.get(sel[0]) if sel else None
        self.members.delete(*self.members.get_children())
        if b is not None:
            for m in self.store.batch_members(b["batch_id"]):
                self.members.insert("", "end", values=[self._label(m["target_kind"], m["target_ref"]),
                                                       BK.DISPOSITION_WORDS.get(m["disposition"], m["disposition"]), m["detail"] or ""])
        self.undo_button.configure(state="normal" if b is not None and b["in_force"] else "disabled")

    def undo(self):
        sel = self.table.selection()
        b = self.batches.get(sel[0]) if sel else None
        if b is None or not b["in_force"]:
            return
        if not messagebox.askyesno("Undo batch", f"Undo batch #{b['batch_id']} ({b['label']})?\n\n{b['in_force']:,} decision(s) or deferral(s) it "
                                   "recorded still stand and will be withdrawn; the batch and its members stay in the record.", parent=self.win):
            return
        try:
            self.store.undo_batch(b["batch_id"], command_id=new_command_id())
        except ValueError as exc:
            messagebox.showwarning("Undo batch", str(exc), parent=self.win)
            return
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Undo batch", f"The batch was not undone.\n\n{exc}", parent=self.win)
            return
        self.refresh()
        self.on_change()

