r"""The Metadata page (Phase 3, finishing Build 9 in B17), inside the one window.

Two views of one page (R10 §21: no new top-level area beyond it):

  the Metadata Review queue   metadata.queue -- the questions that need a
                              person (see metadata.QUEUE_STATES), with filters
                              by field, file family, value-state and blocker,
                              counts, and "Currently clear" when empty;
                              B18: a check column and bulk Confirm (the same
                              decision re-recorded on the evidence as it is
                              now), on B10's frozen batch, with its Batches
                              list and Undo; the slice is read through the
                              session's cache (refresh.MetadataCache)
  one file's metadata         every semantic field as the evidence reads it
                              (value and value-state, capability, blockers,
                              your decision, review state, the plan item it
                              would make), the actions R8 §51-52 name, the
                              project's own metadata, and the history

What this page never does: open, preview or stat the source file, or hydrate
a cloud-only one -- everything shown is project evidence (P3-A117); write
metadata anywhere (no writer exists); create a question by looking -- a
question is named only when a person records something. Layout and wording
are provisional (R10 §22, UI-5).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from Phase2.core import utc_now

from . import bulk as BK
from . import evidence as E
from . import metadata as MD
from . import plans as PL
from . import refresh as RF
from .model import Evidence, MetadataEvidence, active_decisions
from .registry import (METADATA_FIELDS, PROJECT_METADATA_FIELDS, PROJECT_METADATA_LABEL, METADATA_QUESTION, LOCATION,
                       OBSERVED_STATES, FILE_FAMILIES, BULK_ACTIONS, METADATA_CONFIRM, SCOPE_EXPLICIT_SELECTION,
                       SCOPE_QUERY_SNAPSHOT)
from .review import store_for, DeferDialog, CHECKED, UNCHECKED
from .store import new_command_id

#: The source section's label (R8 §50: every editable field says where it is stored).
SOURCE_LABEL = ("Stored in the source file. Saving records what you want; it creates a Phase 4 plan item and never "
                "edits the file.")
STATE_WORDS = {"known": "Known", "no_value": "No value", "unknown": "Unknown (not read)",
               "unsupported": "Unsupported (nothing reads it)", "unavailable": "Unavailable (not present / cloud-only)",
               "stale": "Stale (file changed since)", "conflicting": "Conflicting"}
BLOCKER_WORDS = {"signed_or_certified": "signed / certified", "encrypted": "encrypted",
                 "hardlinked_generic_write": "hard-linked"}
UNKNOWN_WORDS = {MD.SIGNED_KEY: "signature unknown", MD.ENCRYPTED_KEY: "encryption unknown",
                 MD.HARDLINK_KEY: "link count unknown"}
ITEM_WORDS = {"ready": "ready", "blocked": "blocked", "blocked_evidence": "blocked (unknown evidence)",
              "unsupported_action": "blocked"}
BLOCKER_FILTERS = (("", "Any blocker"), ("writer", "No certified writer"), ("signed_or_certified", "Signed / certified"),
                   ("encrypted", "Encrypted"), ("hardlinked_generic_write", "Hard-linked"),
                   ("unknown", "Integrity unknown"))
ALL = "All"


def show_metadata_review(app, file_path_id=None):
    if app.conn is None:
        app.show_welcome()
        return
    app.clear()
    if file_path_id is None:
        app.header("Metadata",
                   "The Metadata Review queue: metadata questions that need you -- a field you named and have not "
                   "decided, a decision whose evidence changed, a deferral that has come back, or two readings that "
                   "disagree. Nothing here reads or writes a file.", back=True)
        page = MetadataQueuePage(app)
    else:
        app.header("Metadata", "One file's metadata, as the project's evidence reads it, and what you want done with "
                               "it. Nothing here reads, previews or writes the file.", back=True)
        page = MetadataFilePage(app, file_path_id)
    app.metadata_page = page
    page.build(app.content)
    page.reload()


def _blockers(r):
    words = [BLOCKER_WORDS.get(b, b) for b in r.blocked_by_evidence]
    words += [UNKNOWN_WORDS.get(k, k) for k in r.integrity_unknown]
    if r.writer != "pass":
        words.insert(0, "no certified writer")
    return ", ".join(words) or "none"


def _observed(r):
    if r.observed_state == "conflicting":
        return "Conflicting: " + " / ".join(repr(v) for v in r.observed_values)
    if r.observed_state == "known":
        return repr(r.observed_value)
    return STATE_WORDS.get(r.observed_state, r.observed_state)


def _desired(r):
    if r.decision_kind == "desired_source_metadata_set":
        return repr(r.desired_value)
    if r.decision_kind == "desired_source_metadata_clear":
        return "(clear)"
    if r.decision_kind == "leave_source_metadata_unchanged":
        return "(leave unchanged)"
    return ""


def _name(path):
    return str(path or "").replace("/", "\\").rstrip("\\").rsplit("\\", 1)[-1]


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------

class MetadataQueuePage:
    def __init__(self, app):
        self.app = app
        self.store = store_for(app)
        self.rows, self.shown = [], []
        self.checked = set()                    # B18: checked question ids (Confirm's explicit scope)
        self.confirm_dialog = None
        self.batches_dialog = None

    def build(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill="x", pady=(0, 6))
        self.count_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.count_var, font=("Segoe UI", 9, "bold"), wraplength=900,
                  justify="left").pack(side="left", fill="x", expand=True)
        frow = ttk.Frame(parent)
        frow.pack(fill="x", pady=(0, 6))
        self.field_var, self.family_var = tk.StringVar(value=ALL), tk.StringVar(value=ALL)
        self.state_var, self.blocker_var = tk.StringVar(value=ALL), tk.StringVar(value=BLOCKER_FILTERS[0][1])
        for label, var, values, width in (
                ("Field:", self.field_var, [ALL] + [f.label for f in METADATA_FIELDS.values()], 16),
                ("File family:", self.family_var, [ALL] + sorted(set(FILE_FAMILIES.values()) | {"other"}), 12),
                ("Value state:", self.state_var, [ALL] + [STATE_WORDS[s] for s in OBSERVED_STATES], 26),
                ("Blocker:", self.blocker_var, [w for _k, w in BLOCKER_FILTERS], 20)):
            ttk.Label(frow, text=label).pack(side="left", padx=(0, 4))
            box = ttk.Combobox(frow, textvariable=var, values=values, state="readonly", width=width)
            box.pack(side="left", padx=(0, 12))
            box.bind("<<ComboboxSelected>>", lambda e: self.apply_filters())
        ttk.Button(frow, text="Open file", command=self.open_selected).pack(side="right")
        brow = ttk.Frame(parent)
        brow.pack(fill="x", pady=(0, 6))
        ttk.Button(brow, text="Check all shown", command=self.check_all_shown).pack(side="left")
        ttk.Button(brow, text="Clear checks", command=self.clear_checks).pack(side="left", padx=(6, 0))
        self.checked_var = tk.StringVar(value="")
        ttk.Label(brow, textvariable=self.checked_var, foreground="#444").pack(side="left", padx=(10, 0))
        ttk.Button(brow, text="Batches...", command=self.open_batches).pack(side="right")
        self.confirm_button = ttk.Button(brow, text="Confirm...", command=self.confirm)
        self.confirm_button.pack(side="right", padx=(0, 6))
        mid = ttk.Frame(parent)
        mid.pack(fill="both", expand=True)
        cols = (("check", "", 28), ("file", "File", 220), ("field", "Field", 110), ("observed", "Observed", 210),
                ("why", "Needs you because", 170), ("capability", "Write capability", 100), ("blockers", "Blockers", 230))
        self.tree = ttk.Treeview(mid, columns=[c[0] for c in cols], show="headings", selectmode="browse", height=18)
        for key, heading, width in cols:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, stretch=key in ("file", "observed", "blockers"),
                             anchor="center" if key == "check" else "w")
        sy = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sy.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sy.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.open_selected())
        self.tree.bind("<Return>", lambda e: self.open_selected())
        self.tree.bind("<space>", lambda e: (self.toggle_check(), "break")[1])
        self.tree.bind("<Button-1>", self._table_click, add="+")
        self.empty_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.empty_var, foreground="#1f6244", wraplength=900, justify="left").pack(anchor="w", pady=(4, 0))

    def cache(self):
        """The session's metadata cache (refresh.MetadataCache) -- rebuildable,
        never authoritative; dropped with the connection."""
        if getattr(self.app, "p3_metadata_cache", None) is None:
            self.app.p3_metadata_cache = RF.MetadataCache()
        return self.app.p3_metadata_cache

    def reload(self):
        conn = self.app.conn
        self.md, conflicts = self.cache().load(conn)
        ev = Evidence({}, {}, [], [], now=utc_now())
        ev.metadata = self.md
        self.all_rows = MD.review(ev)
        self.rows = MD.queue(ev, conflicts, ev.now, reviewed=self.all_rows)
        self.totals = {s: sum(1 for r in self.all_rows if r.review_state == s) for s in MD.REVIEW_WORDS}
        listed = {r.question_id for r in self.rows if r.question_id is not None}
        self.checked &= listed
        self.apply_filters()

    def _filters(self):
        field = next((k for k, f in METADATA_FIELDS.items() if f.label == self.field_var.get()), None)
        family = None if self.family_var.get() == ALL else self.family_var.get()
        state = next((k for k, w in STATE_WORDS.items() if w == self.state_var.get()), None)
        blocker = next((k for k, w in BLOCKER_FILTERS if w == self.blocker_var.get()), "") or None
        return field, family, state, blocker

    def filters_dict(self):
        field, family, state, blocker = self._filters()
        return {"field": field, "family": family, "value_state": state, "blocker": blocker}

    def apply_filters(self):
        self.shown = MD.queue_filter(self.rows, *self._filters())
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.shown):
            why = ("two readings disagree" if r.question_id is None else
                   "a deferral came back" if r.returned else MD.REVIEW_WORDS[r.review_state])
            self.tree.insert("", "end", iid=str(i), values=[
                (CHECKED if r.question_id in self.checked else UNCHECKED) if r.question_id is not None else "",
                _name(r.path), r.field_label, _observed(r), why, r.capability_status, _blockers(r)])
        n = len(self.rows)
        t = self.totals
        others = (f"{len(self.all_rows):,} metadata question(s) in this project: {t[MD.LEFT_UNCHANGED]:,} left unchanged, "
                  f"{t[MD.DEFERRED]:,} deferred, {t[MD.DESIRED] + t[MD.CLEAR_REQUESTED]:,} with a desired value or clear.")
        if not n:
            self.count_var.set("Currently clear -- no metadata question needs you. " + others)
            self.empty_var.set("Open a file's metadata from Files (Details: Metadata for this file) or from a plan's "
                               "Update Source Metadata item to record what you want.")
        else:
            self.count_var.set(f"{n:,} metadata question(s) need you"
                               + (f" ({len(self.shown):,} shown by the filters)" if len(self.shown) != n else "") + ". " + others)
            self.empty_var.set("" if self.shown else "Nothing matches these filters.")
        self._update_checked()

    # -- checks (B18) ---------------------------------------------------------------------

    def _update_checked(self):
        self.checked_var.set(f"{len(self.checked):,} checked" if self.checked else "")

    def _table_click(self, event):
        if self.tree.identify_column(event.x) == "#1" and self.tree.identify_region(event.x, event.y) == "cell":
            row = self.tree.identify_row(event.y)
            if row:
                self._toggle(int(row))
                return "break"
        return None

    def _toggle(self, index):
        r = self.shown[index]
        if r.question_id is None:
            return
        self.checked.symmetric_difference_update({r.question_id})
        self.tree.set(str(index), "check", CHECKED if r.question_id in self.checked else UNCHECKED)
        self._update_checked()

    def toggle_check(self):
        sel = self.tree.selection()
        if sel:
            self._toggle(int(sel[0]))

    def check_all_shown(self):
        self.checked |= {r.question_id for r in self.shown if r.question_id is not None}
        self.apply_filters()

    def clear_checks(self):
        self.checked.clear()
        self.apply_filters()

    # -- Confirm (B18) ---------------------------------------------------------------------

    def confirm(self):
        """Bulk Confirm: re-record the same decision on the evidence as it is
        now, for the checked questions or every question the filters list."""
        self.confirm_dialog = ConfirmDialog(self.app, self, self.store, self._confirmed)
        return self.confirm_dialog

    def _confirmed(self):
        self.checked.clear()
        self.reload()

    def open_batches(self):
        from .review import BatchesDialog
        self.batches_dialog = BatchesDialog(self.app, self.store, self.reload)
        return self.batches_dialog

    def open_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        r = self.shown[int(sel[0])]
        show_metadata_review(self.app, file_path_id=int(r.location_id))


class ConfirmDialog:
    """(B18) Bulk Confirm on B10's frozen batch: choose the scope (the checked
    questions, or every question the filters list now), preview the buckets,
    record exactly the preview. The commit refuses if the record or the
    evidence moved since; the batch undoes as one (Batches...)."""

    def __init__(self, app, page, store, on_done):
        self.app, self.page, self.store, self.on_done = app, page, store, on_done
        self.preview = None
        self.win = tk.Toplevel(app)
        self.win.title("Confirm metadata decisions")
        self.win.transient(app)
        frame = ttk.Frame(self.win, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=BULK_ACTIONS[METADATA_CONFIRM].description, wraplength=560, justify="left").pack(anchor="w")
        self.scope_var = tk.StringVar(value="checked" if page.checked else "listed")
        srow = ttk.Frame(frame)
        srow.pack(fill="x", pady=(8, 4))
        self.scope_buttons = {}
        for key, text in (("checked", f"The {len(page.checked):,} checked question(s)"),
                          ("listed", f"Every question the filters list now ({sum(1 for r in page.shown if r.question_id):,})")):
            b = ttk.Radiobutton(srow, text=text, variable=self.scope_var, value=key, command=self._invalidate)
            b.pack(anchor="w")
            self.scope_buttons[key] = b
        if not page.checked:
            self.scope_buttons["checked"].configure(state="disabled")
        self.text = tk.Text(frame, height=12, width=78, wrap="word", state="disabled", relief="flat", background="#fbfbfb")
        self.text.pack(fill="both", expand=True, pady=(6, 6))
        brow = ttk.Frame(frame)
        brow.pack(fill="x")
        ttk.Button(brow, text="Preview", command=self.run_preview).pack(side="left")
        self.ok = ttk.Button(brow, text="Nothing to record", command=self._confirm, state="disabled")
        self.ok.pack(side="right")
        ttk.Button(brow, text="Cancel", command=self.win.destroy).pack(side="right", padx=(0, 6))

    def _ids(self):
        if self.scope_var.get() == "checked":
            return [q for q in (r.question_id for r in self.page.rows) if q in self.page.checked]
        return [r.question_id for r in self.page.shown if r.question_id is not None]

    def _invalidate(self):
        self.preview = None
        self.ok.configure(text="Nothing to record", state="disabled")

    def run_preview(self):
        ids = self._ids()
        listed = self.scope_var.get() == "listed"
        query = BK.metadata_confirm_query(ids, self.page.filters_dict() if listed else None, queue=listed)
        self.preview = BK.preview_metadata_confirm(self.app.conn, ids, SCOPE_QUERY_SNAPSHOT if listed else SCOPE_EXPLICIT_SELECTION,
                                                   query, md=self.page.md)
        self._show("\n".join(self.preview.lines()))
        n = self.preview.decision_count
        self.ok.configure(text=f"Confirm {n:,} decision{'s' if n != 1 else ''}" if n else "Nothing to record",
                          state="normal" if n else "disabled")
        return self.preview

    def _show(self, text):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.configure(state="disabled")

    def _confirm(self):
        if self.preview is None or not self.preview.decision_count:
            return None
        try:
            res = self.store.commit_bulk(self.preview, note="Confirm on the Metadata Review queue", command_id=new_command_id())
        except ValueError as exc:
            messagebox.showwarning("Confirm", f"Not recorded: {exc}", parent=self.win)
            self._invalidate()
            return None
        self.win.destroy()
        self.on_done()
        return res


# ---------------------------------------------------------------------------
# One file
# ---------------------------------------------------------------------------

class MetadataFilePage:
    def __init__(self, app, file_path_id):
        self.app = app
        self.store = store_for(app)
        self.file_path_id = int(file_path_id)
        self.rows, self.selected_field = [], None
        self.undo_stack = []                    # ("decision", id) | ("deferral", question_id, event_id)
        self.project_vars = {}

    # -- layout -----------------------------------------------------------------------

    def build(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill="x")
        self.file_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.file_var, font=("Segoe UI", 10, "bold"), wraplength=820, justify="left").pack(side="left", fill="x", expand=True)
        ttk.Button(top, text="Metadata Review queue", command=lambda: show_metadata_review(self.app)).pack(side="right")
        self.facts_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.facts_var, foreground="#444", wraplength=980, justify="left").pack(anchor="w", pady=(2, 6))

        src = ttk.LabelFrame(parent, text="Source metadata", padding=6)
        src.pack(fill="both", expand=True)
        ttk.Label(src, text=SOURCE_LABEL, style="Warn.TLabel", wraplength=960, justify="left").pack(anchor="w", pady=(0, 4))
        cols = (("field", "Field", 110), ("observed", "Observed", 190), ("state", "Value state", 150), ("desired", "Desired", 130),
                ("capability", "Write capability", 100), ("blockers", "Blockers", 200), ("review", "Review state", 130),
                ("action", "Expected action", 240))
        self.tree = ttk.Treeview(src, columns=[c[0] for c in cols], show="headings", selectmode="browse", height=6)
        for key, heading, width in cols:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, stretch=key in ("observed", "blockers", "action"), anchor="w")
        self.tree.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._field_chosen())
        ed = ttk.Frame(src)
        ed.pack(fill="x", pady=(6, 0))
        self.field_label_var = tk.StringVar(value="Choose a field above.")
        ttk.Label(ed, textvariable=self.field_label_var, width=30).pack(side="left")
        self.value_var = tk.StringVar(value="")
        self.value_entry = ttk.Entry(ed, textvariable=self.value_var, width=36)
        self.value_entry.pack(side="left", padx=(0, 6))
        self.value_entry.bind("<Return>", lambda e: self.run_default())
        self.save_button = ttk.Button(ed, text="Save desired value", command=self.save_desired, default="active")
        self.save_button.pack(side="left")
        self.clear_button = ttk.Button(ed, text="Clear...", command=self.clear_field, default="normal")
        self.clear_button.pack(side="left", padx=(6, 0))
        self.leave_button = ttk.Button(ed, text="Leave source unchanged", command=self.leave_unchanged)
        self.leave_button.pack(side="left", padx=(6, 0))
        self.defer_button = ttk.Button(ed, text="Defer...", command=self.defer)
        self.defer_button.pack(side="left", padx=(6, 0))
        self.undo_button = ttk.Button(ed, text="Undo", command=self.undo, state="disabled")
        self.undo_button.pack(side="left", padx=(6, 0))
        self.hint_var = tk.StringVar(value="")
        ttk.Label(src, textvariable=self.hint_var, foreground="#666", wraplength=960, justify="left").pack(anchor="w")

        lower = ttk.Frame(parent)
        lower.pack(fill="both", expand=True, pady=(6, 0))
        pm = ttk.LabelFrame(lower, text="Project metadata", padding=6)
        pm.pack(side="left", fill="both", expand=True)
        ttk.Label(pm, text=PROJECT_METADATA_LABEL, style="Good.TLabel", wraplength=460, justify="left").pack(anchor="w", pady=(0, 4))
        trow = ttk.Frame(pm)
        trow.pack(fill="x")
        ttk.Label(trow, text="Tags:", width=16).pack(side="left", anchor="n")
        self.tags_list = tk.Listbox(trow, height=3, width=24, exportselection=False)
        self.tags_list.pack(side="left")
        tcol = ttk.Frame(trow)
        tcol.pack(side="left", padx=(6, 0))
        self.tag_var = tk.StringVar(value="")
        ttk.Entry(tcol, textvariable=self.tag_var, width=16).pack(anchor="w")
        ttk.Button(tcol, text="Add tag", command=self.add_tag).pack(anchor="w", pady=(2, 0))
        ttk.Button(tcol, text="Remove tag", command=self.remove_tag).pack(anchor="w", pady=(2, 0))
        for key in ("category", "note", "review_priority", "retention_rationale", "owner"):
            spec = PROJECT_METADATA_FIELDS[key]
            row = ttk.Frame(pm)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=spec.label + ":", width=16).pack(side="left")
            var = tk.StringVar(value="")
            self.project_vars[key] = var
            if spec.value_type == "choice":
                ttk.Combobox(row, textvariable=var, values=("",) + spec.choices, state="readonly", width=12).pack(side="left")
            else:
                ttk.Entry(row, textvariable=var, width=34).pack(side="left")
            ttk.Button(row, text="Save", command=lambda k=key: self.save_project(k)).pack(side="left", padx=(6, 0))
        hist = ttk.LabelFrame(lower, text="History (this file's metadata decisions)", padding=6)
        hist.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.history_text = tk.Text(hist, height=10, wrap="word", state="disabled", background="#fbfbfb", relief="flat",
                                    font=("Segoe UI", 9))
        self.history_text.pack(fill="both", expand=True)
        self.status_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.status_var, foreground="#1f6244", wraplength=980, justify="left").pack(anchor="w", pady=(4, 0))

    # -- data -------------------------------------------------------------------------

    def reload(self):
        conn = self.app.conn
        now = utc_now()
        self.md = E.file_metadata(conn, self.file_path_id)
        self.loc = E.location(conn, self.file_path_id)
        self.rows = MD.file_review(self.md, now)
        self.items = self._expected_items()
        if self.loc is not None:
            self.file_var.set(self.loc.path)
        fam = next(iter(self.md.questions.values())).file_family if self.md.questions else "other"
        where = ("cloud-only (never opened or downloaded)" if self.loc is not None and self.loc.cloud_only else
                 "present at the last scan" if self.loc is not None and self.loc.present else "not present at the last scan")
        self.facts_var.set(f"File family: {fam} · Current observation: {self.loc.observation_id if self.loc else 'none'} · {where}")
        self.tree.delete(*self.tree.get_children())
        for r in self.rows:
            self.tree.insert("", "end", iid=r.semantic_field, values=[
                r.field_label, _observed(r), STATE_WORDS.get(r.observed_state, r.observed_state), _desired(r),
                r.capability_status, _blockers(r), MD.REVIEW_WORDS[r.review_state], self.expected_action(r)])
        if self.selected_field:
            self.tree.selection_set(self.selected_field)
        self._fill_project()
        self._fill_history()
        self.undo_button.configure(state="normal" if self.undo_stack else "disabled")

    def _expected_items(self):
        """The plan item each asked field's decision would make -- the Plan
        Builder's own preview (plans.metadata_items) over this file alone."""
        if self.loc is None:
            return {}
        asked = {q.question_id: q for q in self.md.questions.values() if q.question_id is not None}
        ev = Evidence({str(self.file_path_id): self.loc}, {}, [], [], now=utc_now())
        ev.metadata = MetadataEvidence(asked, self.md.decisions, self.md.capabilities, self.md.events)
        return {it.sources[0][1]: it for it in PL.metadata_items(ev)}

    def expected_action(self, r):
        it = self.items.get(r.question_id) if r.question_id is not None else None
        if it is not None:
            return f"Update Source Metadata -- {ITEM_WORDS.get(it.state, it.state)}: {it.blocked_reason or 'none'}"
        if r.decision_kind == "leave_source_metadata_unchanged":
            return "none (never a plan item)"
        return ""

    def _fill_project(self):
        pm = {}
        for d in active_decisions(self.md.decisions):
            if d.kind == "project_metadata_set" and d.target_kind == LOCATION:
                prev = pm.get(d.value["field"])
                if prev is None or d.sequence > prev.sequence:
                    pm[d.value["field"]] = d
        self.project = {k: d.value.get("value") for k, d in pm.items()}
        self.tags_list.delete(0, "end")
        for t in self.project.get("tags") or []:
            self.tags_list.insert("end", t)
        for key, var in self.project_vars.items():
            v = self.project.get(key)
            var.set("" if v is None else str(v))

    def _fill_history(self):
        lines = []
        for q in self.md.questions.values():
            if q.question_id is None:
                continue
            for h in self.store.history_for(METADATA_QUESTION, q.question_id):
                lines.append(self._history_line(h, METADATA_FIELDS[q.semantic_field].label))
        for h in self.store.history_for(LOCATION, self.file_path_id):
            if h["decision_kind"] == "project_metadata_set":
                lines.append(self._history_line(h, "Project " + PROJECT_METADATA_FIELDS[h["value"]["field"]].label.lower()))
        lines.sort()
        self.history_text.configure(state="normal")
        self.history_text.delete("1.0", "end")
        self.history_text.insert("1.0", "\n".join(x[1] for x in lines) or "No metadata decision on this file yet.")
        self.history_text.configure(state="disabled")

    @staticmethod
    def _history_line(h, what):
        value = h["value"]
        if h["decision_kind"] == "project_metadata_set":
            value = value.get("value")
        state = "withdrawn" if h["withdrawn"] else "superseded" if h["superseded"] else "current"
        kind = {"desired_source_metadata_set": "desired", "desired_source_metadata_clear": "clear",
                "leave_source_metadata_unchanged": "leave unchanged", "project_metadata_set": "set"}[h["decision_kind"]]
        text = (f"{(h['occurred_utc'] or '')[:16].replace('T', ' ')}  {what}: {kind}"
                + (f" {value!r}" if h["decision_kind"] in ("desired_source_metadata_set", "project_metadata_set") else "")
                + f"  (#{h['decision_id']} by {h['actor_id']}, {state})")
        return (h["occurred_utc"] or "", h["decision_id"]), text

    # -- the selected field -----------------------------------------------------------

    def _row(self, field=None):
        field = field or self.selected_field
        return next((r for r in self.rows if r.semantic_field == field), None)

    def _field_chosen(self):
        sel = self.tree.selection()
        if not sel:
            return
        self.selected_field = sel[0]
        r = self._row()
        spec = METADATA_FIELDS[r.semantic_field]
        self.field_label_var.set(f"{spec.label} ({spec.value_type.replace('_', ' ')}):")
        self.value_var.set("; ".join(r.desired_value) if isinstance(r.desired_value, list) else
                           (r.desired_value or ""))
        hint = {"date": "A date: YYYY-MM-DD (or YYYY-MM-DDTHH:MM[:SS][Z]).",
                "keyword_list": "Keywords separated by ';'.",
                "text": "Text.", "person": "A name."}[spec.value_type]
        if r.observed_state == "unknown":
            hint += " The file's value is unknown (not read) -- that is not a reason to clear it."
        self.hint_var.set(hint)

    def default_action(self):
        """What Return does in the value box: always Save desired value --
        never Clear, least of all for a field whose value is unknown (P3-A111)."""
        return "save"

    def run_default(self):
        return self.save_desired()

    def _need_field(self, what):
        if self._row() is None:
            messagebox.showinfo(what, "Choose a field first.", parent=self.app)
            return False
        return True

    def _typed_value(self):
        spec = METADATA_FIELDS[self.selected_field]
        raw = self.value_var.get()
        if spec.value_type == "keyword_list":
            return [x.strip() for x in raw.replace(",", ";").split(";") if x.strip()]
        return raw

    def _record(self, kind, value=True, what="Metadata"):
        try:
            res = self.store.record_metadata_decision(kind, self.file_path_id, self.selected_field, value,
                                                      command_id=new_command_id())
        except ValueError as exc:
            messagebox.showwarning(what, f"Not recorded: {exc}", parent=self.app)
            return None
        self.undo_stack.append(("decision", res["decision_id"]))
        self.reload()
        return res

    def save_desired(self):
        if not self._need_field("Save desired value"):
            return None
        res = self._record("desired_source_metadata_set", self._typed_value(), "Save desired value")
        if res:
            self.status_var.set(f"Recorded: {METADATA_FIELDS[self.selected_field].label} should be {self._row().desired_value!r} "
                                "-- a plan item for later; the file is not changed.")
        return res

    def clear_field(self):
        if not self._need_field("Clear"):
            return None
        r = self._row()
        extra = (" The file's current value is unknown (it was not read) -- clearing is your intent, not a consequence of "
                 "that." if r.observed_state == "unknown" else "")
        if not messagebox.askyesno("Clear this field?", f"Record that {r.field_label} should have NO value in the file?"
                                   f"{extra}\n\nThis records intent only; nothing is written.", parent=self.app,
                                   default="no"):
            return None
        res = self._record("desired_source_metadata_clear", True, "Clear")
        if res:
            self.status_var.set(f"Recorded: clear {r.field_label} (intent only).")
        return res

    def leave_unchanged(self):
        if not self._need_field("Leave source unchanged"):
            return None
        res = self._record("leave_source_metadata_unchanged", True, "Leave source unchanged")
        if res:
            self.status_var.set(f"Recorded: leave {self._row().field_label} as the file has it. It returns for review only "
                                "if the file changes.")
        return res

    def defer(self):
        if not self._need_field("Defer"):
            return None
        field = self.selected_field
        recorded = {}

        def record(disposition, until, note, command_id):
            res = self.store.defer_metadata(self.file_path_id, field, disposition, until=until, note=note, command_id=command_id)
            recorded.update(res)
            return res

        def done():
            if recorded:
                self.undo_stack.append(("deferral", recorded["question_id"], recorded["review_event_id"]))
                self.status_var.set(f"Deferred: {METADATA_FIELDS[field].label}.")
            self.reload()
        self.defer_dialog = DeferDialog(self.app, None, self.store, done, target_kind=METADATA_QUESTION,
                                        target_ref=None, record=record)
        return self.defer_dialog

    def undo(self):
        if not self.undo_stack:
            return None
        entry = self.undo_stack.pop()
        try:
            if entry[0] == "decision":
                self.store.withdraw(entry[1], reason="undo on the Metadata page", command_id=new_command_id())
                self.status_var.set(f"Undone: decision #{entry[1]} withdrawn (it stays in the history).")
            else:
                self.store.restore(METADATA_QUESTION, entry[1], refers_to=entry[2], reason="undo on the Metadata page",
                                   command_id=new_command_id())
                self.status_var.set("Undone: the deferral is ended.")
        except ValueError as exc:
            messagebox.showwarning("Undo", str(exc), parent=self.app)
        self.reload()
        return entry

    # -- project metadata -----------------------------------------------------------------

    def _record_project(self, field, value):
        try:
            res = self.store.record_metadata_decision("project_metadata_set", self.file_path_id,
                                                      value={"field": field, "value": value}, command_id=new_command_id())
        except ValueError as exc:
            messagebox.showwarning("Project metadata", f"Not recorded: {exc}", parent=self.app)
            return None
        self.undo_stack.append(("decision", res["decision_id"]))
        self.status_var.set(f"Saved in the project: {PROJECT_METADATA_FIELDS[field].label} (never written to the file).")
        self.reload()
        return res

    def add_tag(self):
        tag = self.tag_var.get().strip()
        if not tag:
            return None
        tags = list(self.project.get("tags") or [])
        if tag in tags:
            return None
        self.tag_var.set("")
        return self._record_project("tags", tags + [tag])

    def remove_tag(self):
        sel = self.tags_list.curselection()
        if not sel:
            return None
        tags = [t for i, t in enumerate(self.project.get("tags") or []) if i != sel[0]]
        return self._record_project("tags", tags or None)

    def save_project(self, field):
        raw = self.project_vars[field].get().strip()
        value = raw or None
        if value == self.project.get(field):
            return None
        return self._record_project(field, value)
