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

Recording a decision is instant, so there is no estimate and no progress
screen: that pattern belongs to the collection runs.

Nothing on this page renames, moves, deletes or edits a source file. The
words "Keeper" and "Canonical" describe retention and representation
decisions; no copy is ever called the original.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from . import evidence as E
from . import resolve as R
from .registry import LOCATION, GROUP, DECISION_KINDS, POLICY_KINDS, SOURCE_ROOT
from .store import DecisionStore, new_command_id, export_decision_journal

STATUS_WORDS = {R.PROTECTED: "Protected", R.KEEPER: "Keeper", R.REDUNDANT: "Redundant candidate", R.UNDECIDED: "Undecided"}
REVIEW_WORDS = {R.UNREVIEWED: "Unreviewed", R.IN_PROGRESS: "In progress", R.DEFERRED: "Deferred",
                R.RESOLVED: "Resolved", R.CONFLICT: "Conflict"}
FILTERS = ["All", "Unreviewed", "In progress", "Resolved", "Deferred", "Conflicts", "Ready for plan"]
RULE_WORDS = {
    "protect_source_root": "protected root", "protect_folder_subtree": "protected folder",
    "explicit_must_keep_location": "Keep", "explicit_canonical_location": "Canonical",
    "explicit_keep_all_group": "Keep all", "explicit_redundant_location": "Mark redundant",
}

#: The group list's columns: key -> (heading, width, sort key on a GroupProjection)
GROUP_COLUMNS = {
    "file": ("File", 210, lambda p: _file_name(p.members[0].path).lower() if p.members else ""),
    "copies": ("Copies", 60, lambda p: p.location_count),
    "physical": ("Physical", 65, lambda p: p.physical_copies if p.physical_copies is not None else -1),
    "aliases": ("Aliases", 60, lambda p: p.hardlink_aliases if p.hardlink_aliases is not None else -1),
    "size": ("Size", 85, lambda p: p.size_bytes or 0),
    "potential": ("Potential", 90, lambda p: p.potential_reclaim_bytes or 0),
    "eligible": ("Eligible", 90, lambda p: p.plan_eligible_reclaim_bytes or 0),
    "roots": ("Roots", 50, lambda p: len({m.root_key for m in p.members})),
    "state": ("Status", 95, lambda p: REVIEW_WORDS.get(p.review_state, p.review_state)),
    "canonical": ("Canonical", 190, lambda p: p.effective_canonical or ""),
}
MEMBER_COLUMNS = [("status", "Status", 130), ("file", "File", 170), ("folder", "Folder", 260),
                  ("root", "Root", 150), ("protection", "Protection", 130), ("decided", "Decided by", 120),
                  ("policy", "Policy", 80)]


def _file_name(path):
    return str(path).replace("/", "\\").rsplit("\\", 1)[-1]


def _folder(path):
    s = str(path).replace("/", "\\")
    return s.rsplit("\\", 1)[0] if "\\" in s else ""


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
    """The numbers the project summary shows on its Decide line."""
    proj = R.resolve_all(E.load_evidence(conn))
    t = dict(proj.totals)
    t["reviewed"] = t["groups"] - t["unreviewed"]
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
    page.reload(select=content_id)


class ReviewPage:
    def __init__(self, app):
        self.app = app
        self.store = store_for(app)
        self.proj = None
        self.filter_var = tk.StringVar(value="All")
        self.sort = ("potential", "desc")
        self.selected_group = None
        self.selected_member = None
        self.rows_by_iid = {}

    # -- layout ---------------------------------------------------------------

    def build(self, parent):
        tools = ttk.Frame(parent)
        tools.pack(fill="x", pady=(0, 8))
        self.summary_var = tk.StringVar(value="")
        ttk.Label(tools, textvariable=self.summary_var, font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Button(tools, text="Export journal...", command=self.export_journal).pack(side="right")
        ttk.Button(tools, text="Policies...", command=self.open_policies).pack(side="right", padx=6)
        ttk.Label(tools, text="Show:").pack(side="right", padx=(0, 4))
        cb = ttk.Combobox(tools, textvariable=self.filter_var, values=FILTERS, state="readonly", width=14)
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
            self.table.column(key, width=width, minwidth=40, anchor="e" if key in ("copies", "physical", "aliases", "size", "potential", "eligible", "roots") else "w", stretch=False)
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

    def _build_detail(self, host):
        self.detail = host
        self.group_title = ttk.Label(host, text="Select a group.", font=("Segoe UI", 11, "bold"), wraplength=520, justify="left")
        self.group_title.pack(anchor="w")
        self.group_facts = ttk.Label(host, text="", foreground="#444", wraplength=520, justify="left")
        self.group_facts.pack(anchor="w", pady=(2, 6))
        self.conflict_label = ttk.Label(host, text="", style="Warn.TLabel", wraplength=520, justify="left")

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
                        ("r", self.mark_redundant), ("d", self.defer)):
            self.members.bind(f"<KeyPress-{key}>", lambda e, f=fn: f())

        # The actions. A location action needs a selected member; a group
        # action does not. Override has its own dialog (the research's
        # own-confirmation rule); Undo withdraws a chosen decision.
        acts = ttk.Frame(host)
        acts.pack(fill="x", pady=(8, 0))
        self.buttons = {}
        for key, text, cmd in (("keep", "Keep", self.keep), ("keep_all", "Keep all", self.keep_all),
                               ("canonical", "Set canonical", self.set_canonical),
                               ("redundant", "Mark redundant", self.mark_redundant),
                               ("defer", "Defer", self.defer),
                               ("override", "Override protection...", self.override_protection)):
            b = ttk.Button(acts, text=text, command=cmd)
            b.pack(side="left", padx=(0, 6))
            self.buttons[key] = b
        ttk.Label(host, text="Keys on the member list: k keep · a keep all · c canonical · r redundant · d defer",
                  foreground="#666").pack(anchor="w", pady=(4, 0))

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

    def reload(self, select=None):
        """Re-derive everything from the record and redraw."""
        try:
            self.proj = R.resolve_all(E.load_evidence(self.app.conn))
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Duplicate decisions", str(exc), parent=self.app)
            return
        t = self.proj.totals
        parts = [f"{t['groups']:,} groups", f"{t['unreviewed']:,} unreviewed", f"{t['in_progress']:,} in progress",
                 f"{t['resolved']:,} resolved"]
        if t["deferred"]:
            parts.append(f"{t['deferred']:,} deferred")
        if t["conflicts"]:
            parts.append(f"{t['conflicts']:,} with conflicts")
        reclaim = f"{human_bytes(t['plan_eligible_reclaim_bytes'])} plan-eligible reclaim of {human_bytes(t['potential_reclaim_bytes'])} potential"
        if t["reclaim_unknown_groups"]:
            reclaim += f" (+{t['reclaim_unknown_groups']} groups unknown)"
        parts.append(reclaim)
        if t["orphaned_decisions"]:
            parts.append(f"{t['orphaned_decisions']} decision(s) on files no longer in a group")
        self.summary_var.set(" · ".join(parts))
        self.refresh_list(select=select or self.selected_group)

    def _filtered(self):
        f = self.filter_var.get()
        groups = self.proj.groups
        if f == "Unreviewed":
            groups = [g for g in groups if g.review_state == R.UNREVIEWED]
        elif f == "In progress":
            groups = [g for g in groups if g.review_state == R.IN_PROGRESS]
        elif f == "Resolved":
            groups = [g for g in groups if g.review_state == R.RESOLVED]
        elif f == "Deferred":
            groups = [g for g in groups if g.review_state == R.DEFERRED]
        elif f == "Conflicts":
            groups = [g for g in groups if g.conflicts]
        elif f == "Ready for plan":
            groups = [g for g in groups if g.ready_for_plan]
        key, direction = self.sort
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
        return [_file_name(p.members[0].path) if p.members else p.group_id,
                f"{p.location_count:,}",
                "?" if p.physical_copies is None else f"{p.physical_copies:,}",
                "?" if p.hardlink_aliases is None else f"{p.hardlink_aliases:,}",
                human_bytes(p.size_bytes), human_bytes(p.potential_reclaim_bytes),
                human_bytes(p.plan_eligible_reclaim_bytes),
                f"{len({m.root_key for m in p.members})}",
                REVIEW_WORDS.get(p.review_state, p.review_state), canonical]

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
            tag = "conflict" if p.conflicts else "resolved" if p.review_state == R.RESOLVED else "deferred" if p.review_state == R.DEFERRED else ""
            iid = self.table.insert("", "end", iid=p.group_id, values=self._row_values(p), tags=(tag,))
            self.rows_by_iid[iid] = p
        self.count_var.set(f"{len(groups):,} of {len(self.proj.groups):,} groups shown")
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

    # -- the selected group -----------------------------------------------------

    def show_group(self, group_id):
        self.selected_group = group_id
        self.selected_member = None
        self.members.delete(*self.members.get_children())
        self.history.delete(*self.history.get_children())
        self.history_rows = {}
        self.conflict_label.pack_forget()
        p = self.proj.group(group_id) if group_id else None
        if p is None:
            self.group_title.configure(text="Select a group.")
            self.group_facts.configure(text="")
            self._set_buttons(None, None)
            return
        names = sorted({_file_name(m.path) for m in p.members}, key=str.lower)
        title = names[0] + (f"  (+{len(names) - 1} other name{'s' if len(names) > 2 else ''})" if len(names) > 1 else "")
        self.group_title.configure(text=f"{title} -- {REVIEW_WORDS.get(p.review_state, p.review_state)}")
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
        if p.deferred:
            facts.append("Deferred: an explicit 'not deciding yet'.")
        self.group_facts.configure(text="\n".join(facts))
        if p.conflicts:
            self.conflict_label.configure(text="\n".join(f"Conflict: {c.message}" for c in p.conflicts))
            self.conflict_label.pack(fill="x", pady=(0, 6), before=self.members.master)
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
        for d, kind, ref in rows:
            spec = DECISION_KINDS.get(d["decision_kind"])
            what = spec.label if spec else d["decision_kind"]
            if d["decision_kind"] == "canonical_location":
                v = p.verdict(str(d["value"]))
                what += f": {_file_name(v.path) if v else d['value']}"
            target = "the group" if kind == GROUP else _file_name((p.verdict(ref) or p.members[0]).path)
            state = "active" if d["active"] else "withdrawn" if d["withdrawn"] else "superseded"
            iid = self.history.insert("", "end", values=[d["occurred_utc"][:19].replace("T", " "), what, target, d["actor_id"] or "", state],
                                      tags=() if d["active"] else ("inactive",))
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
        self.buttons["defer"].configure(state="normal" if have_group else "disabled",
                                        text="Undefer" if (have_group and p.deferred) else "Defer")
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
        p, _v = self._current()
        if p is None:
            return
        cmd = new_command_id()
        if p.deferred:
            active = [d for d in self.store.history_for(GROUP, p.group_id) if d["active"] and d["decision_kind"] == "defer_review"]
            ok = all(self._record(lambda d=d: self.store.withdraw(d["decision_id"], "undeferred", command_id=cmd), "Undefer") for d in active)
        else:
            ok = self._record(lambda: self.store.record_decision("defer_review", GROUP, p.group_id, True, command_id=cmd), "Defer")
        if ok:
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
    def __init__(self, parent, app, store, on_done):
        self.app, self.store, self.on_done = app, store, on_done
        win = tk.Toplevel(parent)
        self.win = win
        win.title("Add policy")
        win.transient(parent)
        win.grab_set()
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Kind:", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        self.kinds = list(POLICY_KINDS)
        self.kind_var = tk.StringVar(value=POLICY_KINDS[self.kinds[0]].label)
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
        self.folder_var = tk.StringVar(value="")
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
        row = ttk.Frame(frame)
        row.grid(row=6, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(row, text="Add policy", command=self._confirm).pack(side="right")
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 8))
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

    def _confirm(self):
        kind = self._kind()
        from .model import path_key
        if kind.scope_kind == SOURCE_ROOT:
            root = next((r for r in self.roots if r["root_path"] == self.root_var.get()), None)
            if root is None:
                messagebox.showwarning("Add policy", "Choose a source root.", parent=self.win)
                return
            scope = {"root_key": root["root_path_key"] or path_key(root["root_path"]), "root_path": root["root_path"]}
        else:
            folder = self.folder_var.get().strip()
            if not folder:
                messagebox.showwarning("Add policy", "Choose a folder.", parent=self.win)
                return
            scope = {"path_key": path_key(folder), "path": folder}
        effect = {}
        over = self.over_var.get().strip()
        if kind.key == "prefer_folder_subtree" and over:
            effect = {"over_path_key": path_key(over), "over": over}
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
