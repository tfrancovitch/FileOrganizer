r"""The Plan page (Phase 3, Build 8), inside the one window.

What the decisions already say, turned into proposed logical actions and
previewed against project evidence -- then frozen and fingerprinted. Nothing
on this page executes anything, reads a source file or writes one: a Plan
records intent (P3-A85); Phase 4, which does not exist yet, will re-check the
live filesystem and decide how.

  Build a new revision   the draft from the current decisions (plans.build),
                         frozen at once as a new revision of the plan, with
                         the Phase 3 Evidence Preview's results
  Lock revision          P3.R7 §29: the record and the evidence must be where
                         the preview found them; the payload is fingerprinted
                         (SHA-256) and the revision becomes immutable
  Compare with previous  what changed from the plan's previous revision

The routing detector runs when the page opens: a locked revision whose own
decisions, policies or evidence have changed since is shown as obsolete,
with the reason -- never rewritten.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from . import evidence as E
from . import plans as PL
from . import resolve as R
from . import routing as RT
from . import scopes as SC
from .registry import (ACTION_WORDS, ITEM_READY, ITEM_PRESERVE, STAGE_EVIDENCE, STAGE_LIVE, PREVIEW_DISCLOSURE,
                       ACTION_METADATA, GROUP, VISUAL_PAIR, METADATA_QUESTION)
from .review import store_for, run_detector
from .store import new_command_id

STATE_WORDS = {"ready": "Ready", "blocked": "Blocked", "blocked_evidence": "Blocked (unknown evidence)",
               "unsupported_action": "Unsupported action", "preserve": "Preserved"}
#: How an item names what produced it.
SOURCE_WORDS = {GROUP: "group ", VISUAL_PAIR: "pair #", METADATA_QUESTION: "metadata question #"}
STATUS_WORDS = {"previewed": "Previewed", "locked": "Locked", "obsolete": "Obsolete", "superseded": "Superseded",
                "review_ready": "Review ready", "draft": "Draft", "handed_off": "Handed off", "archived": "Archived"}


def show_plan(app, plan_id=None):
    if app.conn is None:
        app.show_welcome()
        return
    app.clear()
    app.header("Plan",
               "Proposed logical actions from the decisions you have made -- previewed against the project's evidence, "
               "then frozen and fingerprinted. Nothing here removes, moves or renames a file, or reads one: a plan is "
               "intent, and whatever carries it out later must re-check the files first.", back=True)
    page = PlanPage(app)
    app.plan_page = page
    page.build(app.content)
    page.detect()
    page.reload(plan_id=plan_id)


def _name(path):
    return str(path or "").replace("/", "\\").rstrip("\\").rsplit("\\", 1)[-1]


class PlanPage:
    def __init__(self, app):
        self.app = app
        self.store = store_for(app)
        self.plan_id = None
        self.revision_id = None
        self.rev, self.items = None, []
        self.model = None

    # -- layout ---------------------------------------------------------------------

    def build(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill="x", pady=(0, 6))
        self.population_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.population_var, font=("Segoe UI", 9, "bold"), wraplength=560,
                  justify="left").pack(side="left", fill="x", expand=True)
        self.compare_button = ttk.Button(top, text="Compare with previous", command=self.compare, state="disabled")
        self.compare_button.pack(side="right")
        self.lock_button = ttk.Button(top, text="Lock revision", command=self.lock, state="disabled")
        self.lock_button.pack(side="right", padx=(0, 6))
        ttk.Button(top, text="Build a new revision", command=self.build_revision).pack(side="right", padx=(0, 6))
        prow = ttk.Frame(parent)
        prow.pack(fill="x", pady=(0, 6))
        ttk.Label(prow, text="Plan:").pack(side="left")
        self.plan_var = tk.StringVar(value="")
        self.plan_box = ttk.Combobox(prow, textvariable=self.plan_var, state="readonly", width=40)
        self.plan_box.pack(side="left", padx=(4, 12))
        self.plan_box.bind("<<ComboboxSelected>>", lambda e: self._plan_chosen())
        ttk.Label(prow, text="Name for a new plan:").pack(side="left")
        self.name_var = tk.StringVar(value="Plan")
        ttk.Entry(prow, textvariable=self.name_var, width=24).pack(side="left", padx=(4, 0))
        self.new_plan_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(prow, text="Build as a new plan", variable=self.new_plan_var).pack(side="left", padx=(8, 0))

        panes = tk.PanedWindow(parent, orient="horizontal", sashrelief="raised", sashwidth=6, bd=0)
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes)
        right = ttk.Frame(panes, padding=(8, 0, 0, 0))
        panes.add(left, minsize=240, width=330)
        panes.add(right, minsize=520)
        ttk.Label(left, text="Revisions", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.revisions = ttk.Treeview(left, columns=("rev", "status", "when", "items", "fingerprint"), show="headings",
                                      selectmode="browse", height=12)
        for key, heading, width in (("rev", "Rev", 36), ("status", "Status", 80), ("when", "Built (UTC)", 110),
                                    ("items", "Items", 44), ("fingerprint", "Fingerprint", 90)):
            self.revisions.heading(key, text=heading)
            self.revisions.column(key, width=width, stretch=key == "fingerprint", anchor="w")
        self.revisions.pack(fill="both", expand=True)
        self.revisions.bind("<<TreeviewSelect>>", lambda e: self._revision_chosen())
        self.history_var = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.history_var, foreground="#444", wraplength=320, justify="left").pack(anchor="w", pady=(6, 0))

        self.preview_text = tk.Text(right, height=13, wrap="word", state="disabled", background="#f6f6f6", relief="flat",
                                    font=("Segoe UI", 9))
        self.preview_text.pack(fill="x")
        # R7 §14's required disclosure, always in view (never scrolled away).
        ttk.Label(right, text=PREVIEW_DISCLOSURE, style="Warn.TLabel", wraplength=900, justify="left").pack(anchor="w", pady=(2, 0))
        mid = ttk.Frame(right)
        mid.pack(fill="both", expand=True, pady=(6, 0))
        self.items_tree = ttk.Treeview(mid, columns=("n", "action", "target", "state", "why"), show="headings",
                                       selectmode="browse", height=10)
        for key, heading, width in (("n", "#", 36), ("action", "Action", 130), ("target", "Location", 190),
                                    ("state", "State", 150), ("why", "Why / blocked by", 280)):
            self.items_tree.heading(key, text=heading)
            self.items_tree.column(key, width=width, stretch=key in ("target", "why"), anchor="w")
        for tag, colour in (("blocked", "#8a1c1c"), ("preserve", "#555"), ("ready", "#1f6244")):
            self.items_tree.tag_configure(tag, foreground=colour)
        sy = ttk.Scrollbar(mid, orient="vertical", command=self.items_tree.yview)
        self.items_tree.configure(yscrollcommand=sy.set)
        self.items_tree.pack(side="left", fill="both", expand=True)
        sy.pack(side="left", fill="y")
        self.items_tree.bind("<<TreeviewSelect>>", lambda e: self._item_chosen())
        self.detail_text = tk.Text(right, height=12, wrap="word", state="disabled", background="#fbfbfb", relief="flat",
                                   font=("Segoe UI", 9))
        self.detail_text.pack(fill="both", expand=False, pady=(6, 0))
        # (B17) An Update Source Metadata item opens its file's Metadata page.
        self.chosen_item = None
        self.metadata_button = ttk.Button(right, text="Open this file's metadata", state="disabled",
                                          command=self.open_metadata)
        self.metadata_button.pack(anchor="w", pady=(4, 0))
        self.status_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.status_var, foreground="#1f6244", wraplength=900, justify="left").pack(anchor="w", pady=(4, 0))

    # -- data ------------------------------------------------------------------------

    def detect(self):
        """The routing detector, with the pairs: a locked revision whose own
        dependencies changed becomes obsolete before the page draws it. Its
        model is kept for the page's first draw (B14's pattern: one load at
        page open, not two)."""
        self._detected = {}
        self.detector_result = run_detector(self.app.conn, self.store, keep=self._detected)

    def _load_model(self, reuse=False):
        """The model the population line and a build read, current as of now.
        It depends only on the decision record and the evidence, so it is
        reused while neither has moved (the record watermarks and the evidence
        mark are where they were when it was loaded): building or locking a
        plan moves neither. At page open, the detector's own model is taken
        on the same terms -- one load, not two."""
        key = (E.record_marks(self.app.conn), E.evidence_mark(self.app.conn))
        kept, self._detected = getattr(self, "_detected", None) or {}, None
        if kept.get("model") is not None and kept.get("marks") == key[0]:
            self.model, self._model_key = kept["model"], key
            return self.model
        if self.model is not None and getattr(self, "_model_key", None) == key:
            return self.model
        ev = E.load_evidence(self.app.conn)
        proj = R.resolve_all(ev)
        self.model, self._model_key = (ev, proj, RT.route_all(ev, proj, ev.now)), key
        return self.model

    def reload(self, plan_id=None, revision_id=None):
        try:
            ev, proj, routing = self._load_model()
            groups, pairs, excluded = PL.population(proj, routing)
            self.plans = self.store.plans()
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Plan", str(exc), parent=self.app)
            return
        text = (f"Ready for a plan: {len(groups):,} duplicate group{'s' if len(groups) != 1 else ''} and "
                f"{len(pairs):,} similar-image pair{'s' if len(pairs) != 1 else ''} whose decisions name a copy to remove")
        if excluded.get("deferred"):
            text += f" · {excluded['deferred']:,} deferred by you (left out)"
        # B19: say which policy matches nothing, and how to clear it, before a revision is built.
        self.dangling = SC.summary(ev)
        for d in self.dangling:
            text += (" · BLOCKED -- every removal until this is cleared: " if d["effect"] == "protect"
                     else " · Warning: ") + d["words"]
        self.population_var.set(text + ("." if not self.dangling else ""))
        self.plan_box["values"] = [f"{p['name']} (plan #{p['plan_id']})" for p in self.plans]
        if plan_id is None and self.plan_id is not None and any(p["plan_id"] == self.plan_id for p in self.plans):
            plan_id = self.plan_id
        if plan_id is None and self.plans:
            plan_id = self.plans[0]["plan_id"]
        self.plan_id = plan_id
        if plan_id is not None:
            idx = next(i for i, p in enumerate(self.plans) if p["plan_id"] == plan_id)
            self.plan_box.current(idx)
        self._fill_revisions(revision_id)

    def _plan(self):
        return next((p for p in self.plans if p["plan_id"] == self.plan_id), None)

    def _plan_chosen(self):
        i = self.plan_box.current()
        if i >= 0:
            self.plan_id = self.plans[i]["plan_id"]
            self._fill_revisions(None)

    def _fill_revisions(self, revision_id):
        self.revisions.delete(*self.revisions.get_children())
        plan = self._plan()
        revs = plan["revisions"] if plan else []
        for r in revs:
            self.revisions.insert("", "end", iid=str(r["plan_revision_id"]), values=[
                r["revision_no"], STATUS_WORDS.get(r["status"], r["status"]), (r["occurred_utc"] or "")[:16].replace("T", " "),
                r["summary"].get("items", ""), (r["fingerprint"] or "")[:12]])
        target = revision_id or (revs[0]["plan_revision_id"] if revs else None)
        if target is not None:
            self.revisions.selection_set(str(target))
            self._show_revision(target)
        else:
            self._show_revision(None)

    def _revision_chosen(self):
        sel = self.revisions.selection()
        if sel and int(sel[0]) != self.revision_id:
            self._show_revision(int(sel[0]))

    def _show_revision(self, revision_id):
        self.revision_id = revision_id
        self.items_tree.delete(*self.items_tree.get_children())
        self._write(self.detail_text, "")
        if revision_id is None:
            self.rev, self.items = None, []
            self._write(self.preview_text, "No plan yet. Build a new revision: the Plan Builder turns the decisions you "
                                           "have already made into proposed logical actions -- it decides nothing itself.\n\n"
                        + PREVIEW_DISCLOSURE)
            self.lock_button.configure(state="disabled")
            self.compare_button.configure(state="disabled")
            self.history_var.set("")
            return
        self.rev, self.items = self.store.plan_revision(revision_id)
        rev = self.rev
        head = [f"{rev['name']} -- revision {rev['revision_no']} -- {STATUS_WORDS.get(rev['status'], rev['status'])}"]
        if rev["fingerprint"]:
            head.append(f"Fingerprint (SHA-256): {rev['fingerprint']}")
        obsolete = [e for e in rev["events"] if e["event_kind"] == "obsolete"]
        if obsolete:
            reasons = obsolete[-1]["detail"].get("reasons") or []
            head.append("OBSOLETE -- no longer to be carried out: " + "; ".join(f"{r['words']} ({r['detail']})" for r in reasons[:4]))
        self._write(self.preview_text, "\n".join(head + [""] + PL.summary_lines(rev["summary"])))
        for it in self.items:
            why = (it.blocking.detail if it.blocking is not None else
                   "; ".join(it.desired_effect.get("why", [])) if it.action_kind == "no_op_preserve" else "")
            tag = "ready" if it.state == ITEM_READY else "preserve" if it.state == ITEM_PRESERVE else "blocked"
            self.items_tree.insert("", "end", iid=str(it.item_no), tags=(tag,), values=[
                it.item_no, ACTION_WORDS.get(it.action_kind, it.action_kind), _name(it.desired_effect.get("path") or it.target_ref),
                STATE_WORDS.get(it.state, it.state) + (f" ({it.blocked_reason})" if it.blocked_reason else ""), why])
        self.lock_button.configure(state="normal" if rev["status"] in ("previewed", "review_ready") else "disabled")
        plan = self._plan()
        self.compare_button.configure(state="normal" if plan and rev["revision_no"] > 1 else "disabled")
        self.history_var.set("\n".join(f"{(e['occurred_utc'] or '')[:16].replace('T', ' ')}  {e['event_kind']}"
                                       + (" (the routing detector)" if e["actor_kind"] == "system_evidence" else "")
                                       for e in rev["events"]))

    def open_metadata(self):
        it = self.chosen_item
        if it is not None and it.action_kind == ACTION_METADATA:
            self.app.show_metadata_review(file_path_id=int(it.target_ref))

    def _item_chosen(self):
        sel = self.items_tree.selection()
        if not sel:
            return
        it = next((i for i in self.items if i.item_no == int(sel[0])), None)
        if it is None:
            return
        lines = [f"Item {it.item_no}: {ACTION_WORDS.get(it.action_kind, it.action_kind)} -- "
                 f"{it.desired_effect.get('path') or it.target_ref}",
                 f"Logical intent: {it.desired_effect.get('effect')} (how is not decided here; that is Phase 4's)",
                 f"State: {STATE_WORDS.get(it.state, it.state)} · impact: {it.impact_class}",
                 "From: " + ", ".join(f"{SOURCE_WORDS.get(k, k + ' ')}{r}" for k, r in it.sources)]
        if it.action_kind == ACTION_METADATA:
            op = it.desired_effect.get("operation")
            lines.append(f"Desired: {it.desired_effect.get('field')} "
                         + (f"= {it.desired_effect.get('value')!r}" if op == "set" else "cleared (no value)")
                         + f"; observed now: {it.estimated_effect.get('observed_state')}"
                         + (f" {it.estimated_effect.get('observed_value')!r}" if it.estimated_effect.get("observed_value") is not None else ""))
        if it.decision_ids:
            lines.append("Justified by decision(s): " + ", ".join(f"#{d}" for d in it.decision_ids))
        if it.retaining_ids:
            lines.append("Kept by decision(s): " + ", ".join(f"#{d}" for d in it.retaining_ids))
        if it.policy_version_ids:
            lines.append("Policy versions: " + ", ".join(it.policy_version_ids))
        if it.depends_on:
            lines.append("Depends on item(s): " + ", ".join(str(d) for d in it.depends_on))
        ev_pre = [c for c in it.preconditions if c.stage == STAGE_EVIDENCE]
        live = [c for c in it.preconditions if c.stage == STAGE_LIVE]
        if ev_pre:
            lines.append("")
            lines.append("Checked now, from project evidence:")
            for c in ev_pre:
                lines.append(f"  [{c.result or '-'}] {c.key} ({c.severity}, {c.klass}) -- {c.detail}")
        if live:
            lines.append("")
            lines.append("Declared for Phase 4's live check (not evaluated here): " + ", ".join(c.key for c in live))
        if it.postconditions:
            lines.append("Afterwards, Phase 4 must verify: " + ", ".join(it.postconditions))
        for note in it.estimated_effect.get("notes") or []:
            lines.append("Note: " + note)
        self._write(self.detail_text, "\n".join(lines))
        self.metadata_button.configure(state="normal" if it.action_kind == ACTION_METADATA else "disabled")
        self.chosen_item = it

    def _write(self, widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    # -- actions ------------------------------------------------------------------------

    def build_revision(self):
        """A new revision from the current decisions: built, previewed and
        frozen in one operation."""
        try:
            ev, proj, routing = self._load_model()
            draft = PL.build(ev, proj, routing)
            plan_id = None if (self.new_plan_var.get() or self.plan_id is None) else self.plan_id
            res = self.store.record_plan_revision(draft, name=self.name_var.get().strip() or "Plan", plan_id=plan_id,
                                                  command_id=new_command_id())
        except ValueError as exc:
            messagebox.showwarning("Plan", str(exc), parent=self.app)
            return
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Plan", f"The plan was not built.\n\n{exc}", parent=self.app)
            return
        self.new_plan_var.set(False)
        self.last_build = res
        self.reload(plan_id=res["plan_id"], revision_id=res["plan_revision_id"])
        s = draft.summary
        self.status_var.set(f"Revision {res['revision_no']} built and previewed: {s['items']:,} items "
                            f"({s['states'].get(ITEM_READY, 0):,} ready, {s['blocked']:,} blocked). Nothing was carried out.")

    def lock(self):
        if self.revision_id is None:
            return
        if not messagebox.askyesno("Lock revision", "Lock this revision? Its items and preconditions are frozen and "
                                   "fingerprinted. Locking records reviewed intent only -- nothing is carried out, and "
                                   "whatever carries it out later must re-check the files first.", parent=self.app):
            return
        try:
            res = self.store.lock_plan_revision(self.revision_id, command_id=new_command_id())
        except ValueError as exc:
            messagebox.showwarning("Lock revision", str(exc), parent=self.app)
            return
        self.reload(plan_id=self.plan_id, revision_id=self.revision_id)
        self.status_var.set(f"Locked. Fingerprint {res['fingerprint']}.")

    def compare(self):
        plan = self._plan()
        if plan is None or self.rev is None:
            return
        prev = next((r for r in plan["revisions"] if r["revision_no"] == self.rev["revision_no"] - 1), None)
        if prev is None:
            return
        old_rev, old_items = self.store.plan_revision(prev["plan_revision_id"])
        d = PL.diff(old_items, self.items, old_rev["summary"], self.rev["summary"])
        self.last_diff = d
        messagebox.showinfo("Compare with previous", f"Revision {prev['revision_no']} -> {self.rev['revision_no']}:\n\n"
                            + "\n".join(d["lines"]), parent=self.app)
