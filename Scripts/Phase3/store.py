r"""Recording intent: operations, decisions, withdrawals, policy versions.

Append-only. Nothing this module writes is ever updated or deleted:

  - a change of mind is a new decision that names the one it supersedes,
    or a withdrawal row against the old one; the old row stays as recorded;
  - a change to a policy is a new version; switching a policy off is a
    version whose status is 'retired'.

Every write belongs to one Operation -- one user action, with who
(actor_id), through what (agent_kind, agent_version), in which session and
which command. One operation is one transaction: its rows all land or none
do. The registry validates every kind and value before anything is written.

The protection gate does not live here: a redundant mark against a
protected location is recorded if asked for (the window asks for the
override first) and simply has no effect in the resolution until the
protection is overridden. What DOES live here is the override's own
discipline: `record_override` demands an explicit confirmation flag, a
rationale, and a protection policy that actually covers the location.

Build 3 adds the review events (`p3_review_event`): a deferral with its
return trigger, a skip, a manual restore -- each a person's operation of
kind 'review' -- and the detector's rows (`system_events`): what it found
stale or blocked and what it restored, under an operation whose actor kind
is 'system_evidence'. A system operation never records a decision.

Build 4 adds the bulk batch (`commit_bulk`): one operation of kind 'bulk'
that records the batch row, its frozen membership with each member's
disposition, and -- for the members the preview classed as 'decided' --
the decisions themselves, each with origin_kind 'bulk_explicit_human' and
origin_ref = the batch id, bound to the evidence the preview captured. The
store commits exactly what the preview showed; it never re-evaluates.
`undo_batch` withdraws the batch's still-active decisions (and restores
its deferrals) in one operation of kind 'bulk_undo'; the batch and member
rows stay as recorded.

Build 5 adds the visual candidate pair as a target (decisions and review
events). `record_decisions` records several decisions in ONE operation --
the Image Game's fast actions ("Equivalent -> Prefer Left" is a
relationship row and a retention row, each its own decision, landing
together or not at all) -- and `withdraw_many` withdraws several in one
operation (the Image Game's Undo of such an action).

Build 6 commits batches over visual candidate pairs through the same
`commit_bulk`; such a preview also carries an evidence mark (the newest
comparison of the image hashes and the newest observation), and the commit
refuses when the evidence moved since -- a re-analysis or a scan adds no
operation, so the record guard alone would not see it.
"""
from __future__ import annotations

import getpass
import json
import uuid

from Phase2.core import utc_now

from . import VERSION
from . import evidence as ev_mod
from . import routing as RT
from .model import active_decisions, PolicyVersion
from .registry import (decision_kind, policy_kind, event_kind, return_kind, origin_kind, bulk_action, disposition,
                       LOCATION, GROUP, VISUAL_PAIR, TARGET_KINDS, CONFIRM_EXPLICIT_OVERRIDE,
                       EVENT_DEFERRED, EVENT_SKIPPED, EVENT_RESTORED, RETURN_TIME, RETURN_EVIDENCE_CHANGE,
                       RETURN_MANUAL, DEFER_DISPOSITIONS, DEFER_CHOICES, ORIGIN_EXPLICIT_HUMAN, ORIGIN_BULK_EXPLICIT_HUMAN,
                       DISP_DECIDED, MODE_PRESERVE)
from .resolve import _policy_covers
from .registry import action_kind, PLAN_EVENT_KINDS, LINK_JUSTIFIES, LINK_RETAINS
from .registry import METADATA_QUESTION, METADATA_DECISION_KINDS, metadata_field, validate_field_value

#: Which decision kinds a read covers (B17, review R1), as
#: evidence.load_decisions(metadata=...) takes it.
SCOPE_RETENTION, SCOPE_METADATA, SCOPE_ALL = "retention", "metadata", "all"
_SCOPES = {SCOPE_RETENTION: False, SCOPE_METADATA: True, SCOPE_ALL: None}


def _scope(scope):
    try:
        return _SCOPES[SCOPE_RETENTION if scope is None else scope]
    except KeyError:
        raise ValueError(f"unknown decision scope: {scope!r}")


SYSTEM_ACTOR_KIND = "system_evidence"
DETECTOR_AGENT = "routing_detector"


def new_command_id() -> str:
    return "cmd:" + str(uuid.uuid4())


def default_actor_id() -> str:
    """The person at the keyboard, as Windows names them. Phase 1/2 keep user
    identifiers out of their records on purpose; a decision is different --
    it is attributable intent, and the actor is part of the record."""
    try:
        return getpass.getuser()
    except Exception:                                               # noqa: BLE001
        return "unknown"


class DecisionStore:
    def __init__(self, conn, actor_id=None, agent_kind="dashboard", session_id=None):
        self.conn = conn
        self.actor_id = actor_id or default_actor_id()
        self.agent_kind = agent_kind
        self.session_id = session_id or ("session:" + str(uuid.uuid4()))

    # -- operations ----------------------------------------------------------

    def _operation(self, kind, command_id, note=None, actor_kind="explicit_human", actor_id=None, agent_kind=None):
        cur = self.conn.execute(
            "INSERT INTO p3_operation(project_id, kind, actor_kind, actor_id, agent_kind, agent_version, "
            "session_id, command_id, occurred_utc, note) VALUES(1,?,?,?,?,?,?,?,?,?)",
            (kind, actor_kind, actor_id if actor_id is not None else self.actor_id, agent_kind or self.agent_kind,
             VERSION, self.session_id, command_id or new_command_id(), utc_now(), note))
        return int(cur.lastrowid)

    def _run(self, fn):
        """One operation, one transaction."""
        if self.conn.in_transaction:
            self.conn.commit()
        try:
            result = fn()
            self.conn.commit()
            return result
        except Exception:
            self.conn.rollback()
            raise

    # -- reading ---------------------------------------------------------------

    def active(self, scope=None):
        """The active decisions of one scope (B17, review R1: the scope is
        named, never implied). SCOPE_RETENTION -- the retention and review
        kinds the duplicate and pair model reads (Build 9 keeps metadata out
        of it; the default, as in B16); SCOPE_METADATA -- the four metadata
        kinds; SCOPE_ALL -- every row."""
        return active_decisions(ev_mod.load_decisions(self.conn, metadata=_scope(scope)))

    def active_for(self, target_kind, target_ref, scope=None):
        """One target's active decisions of one scope. B14: read for that
        target only (a decision only ever supersedes the chain head on its
        own target, so the target's rows alone say which of them still speak)."""
        ref = str(target_ref)
        return active_decisions(ev_mod.load_decisions(self.conn, "d.target_kind = ? AND d.target_ref = ?", (target_kind, ref),
                                                      metadata=_scope(scope)))

    def still_active(self, decision_ids):
        """(B14) Which of these decision ids still speak -- not withdrawn, not
        superseded by any row -- as a set of strings, without reading the
        whole record."""
        ids = [int(i) for i in decision_ids if str(i).isdigit()]
        out = set()
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" for _ in chunk)
            out.update(str(r[0]) for r in self.conn.execute(
                f"SELECT d.decision_id FROM p3_decision d WHERE d.decision_id IN ({marks}) "
                "   AND NOT EXISTS (SELECT 1 FROM p3_decision_withdrawal w WHERE w.decision_id = d.decision_id) "
                "   AND NOT EXISTS (SELECT 1 FROM p3_decision s WHERE s.supersedes_decision_id = d.decision_id)", chunk))
        return out

    def _chain_head(self, target_kind, target_ref, domain):
        """The decision a new one in this domain supersedes: the newest row on
        the target in the domain that nothing has superseded yet -- withdrawn
        or not, so the chain stays complete (F09: the re-apply supersedes the
        withdrawn decision). Superseding a withdrawn row changes nothing
        about what is active; it records what replaced what."""
        # The target's own rows only (indexed): a batch records thousands of
        # decisions in one operation, and each one asks this question.
        rows = self.conn.execute(
            "SELECT d.decision_id, d.decision_kind, d.value_json FROM p3_decision d "
            " WHERE d.project_id = 1 AND d.target_kind = ? AND d.target_ref = ? "      # the index's own prefix (B12)
            "   AND NOT EXISTS (SELECT 1 FROM p3_decision x WHERE x.supersedes_decision_id = d.decision_id)",
            (target_kind, str(target_ref))).fetchall()
        heads = [int(r["decision_id"]) for r in rows
                 if decision_kind(r["decision_kind"]).domain_key(json.loads(r["value_json"])) == domain]
        return max(heads, default=None)

    def history_for(self, target_kind, target_ref):
        """Every decision ever recorded on the target, oldest first, with the
        operation that recorded it and the one that withdrew it (if any)."""
        rows = self.conn.execute(
            "SELECT d.decision_id, d.decision_kind, d.value_json, d.supersedes_decision_id, d.rationale, "
            "       d.origin_kind, d.origin_ref, "
            "       o.occurred_utc, o.actor_id, o.kind AS operation_kind, o.operation_id, "
            "       w.withdrawal_id, w.reason AS withdrawal_reason, wo.occurred_utc AS withdrawn_utc "
            "  FROM p3_decision d JOIN p3_operation o ON o.operation_id = d.operation_id "
            "  LEFT JOIN p3_decision_withdrawal w ON w.decision_id = d.decision_id "
            "  LEFT JOIN p3_operation wo ON wo.operation_id = w.operation_id "
            " WHERE d.project_id = 1 AND d.target_kind = ? AND d.target_ref = ? ORDER BY d.decision_id",
            (target_kind, str(target_ref))).fetchall()
        superseded = {r["supersedes_decision_id"] for r in rows if r["supersedes_decision_id"] is not None}
        out = []
        for r in rows:
            d = dict(r)
            d["value"] = json.loads(d.pop("value_json"))
            d["withdrawn"] = r["withdrawal_id"] is not None
            d["superseded"] = r["decision_id"] in superseded
            d["active"] = not d["withdrawn"] and not d["superseded"]
            out.append(d)
        return out

    def operations(self, limit=200):
        return [dict(r) for r in self.conn.execute(
            "SELECT o.*, (SELECT COUNT(*) FROM p3_decision d WHERE d.operation_id = o.operation_id) AS decisions, "
            "       (SELECT COUNT(*) FROM p3_decision_withdrawal w WHERE w.operation_id = o.operation_id) AS withdrawals, "
            "       (SELECT COUNT(*) FROM p3_policy_version v WHERE v.operation_id = o.operation_id) AS policy_versions "
            "  FROM p3_operation o ORDER BY o.operation_id DESC LIMIT ?", (int(limit),))]

    def counts(self):
        row = self.conn.execute(
            "SELECT (SELECT COUNT(*) FROM p3_operation), (SELECT COUNT(*) FROM p3_decision), "
            "       (SELECT COUNT(*) FROM p3_decision_withdrawal), (SELECT COUNT(*) FROM p3_policy_version), "
            "       (SELECT COUNT(*) FROM p3_review_event), (SELECT COUNT(*) FROM p3_bulk_batch), "
            "       (SELECT COUNT(*) FROM p3_metadata_question)").fetchone()
        return {"operations": row[0], "decisions": row[1], "withdrawals": row[2], "policy_versions": row[3],
                "review_events": row[4], "batches": row[5], "metadata_questions": row[6]}

    # -- review events (Build 3) ------------------------------------------------

    def review_events_for(self, target_kind, target_ref):
        """Every review event on the target, oldest first, with its operation."""
        rows = self.conn.execute(
            "SELECT e.*, o.occurred_utc, o.actor_kind, o.actor_id, o.agent_kind "
            "  FROM p3_review_event e JOIN p3_operation o ON o.operation_id = e.operation_id "
            " WHERE e.target_kind = ? AND e.target_ref = ? ORDER BY e.review_event_id",
            (target_kind, str(target_ref))).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["detail"] = json.loads(d.pop("detail_json") or "{}")
            except ValueError:
                d["detail"] = {}
            out.append(d)
        return out

    def _event_row(self, operation_id, target_kind, target_ref, kind, return_kind_=None, return_condition=None,
                   return_on_utc=None, detail=None, refers_to=None):
        event_kind(kind)
        return_kind(return_kind_)
        if target_kind not in TARGET_KINDS + (METADATA_QUESTION,):
            raise ValueError(f"unknown target kind: {target_kind!r}")
        if refers_to is not None:
            row = self.conn.execute("SELECT review_event_id FROM p3_review_event WHERE review_event_id=?", (int(refers_to),)).fetchone()
            if row is None:
                raise ValueError(f"no review event {refers_to} to refer to")
        cur = self.conn.execute(
            "INSERT INTO p3_review_event(project_id, operation_id, target_kind, target_ref, event_kind, return_kind, "
            "return_condition, return_on_utc, detail_json, refers_to_review_event_id) VALUES(1,?,?,?,?,?,?,?,?,?)",
            (operation_id, target_kind, str(target_ref), kind, return_kind_, return_condition, return_on_utc,
             json.dumps(detail or {}, sort_keys=True), int(refers_to) if refers_to is not None else None))
        return int(cur.lastrowid)

    def defer(self, target_kind, target_ref, disposition="defer_indefinitely", until=None, note=None, command_id=None):
        """A person's deferral with its return trigger (synthesis §5). A new
        deferral replaces an open one. For 'until the evidence changes' the
        target's evidence at this moment is recorded in the event's detail,
        the same ids-only shape a decision binds -- the trigger compares
        against it. Returns {review_event_id, operation_id, return_kind}."""
        if disposition not in DEFER_DISPOSITIONS:
            raise ValueError(f"unknown deferral disposition: {disposition!r}")
        if disposition not in DEFER_CHOICES.get(target_kind, ()):
            raise ValueError(f"{DEFER_DISPOSITIONS[disposition][1]} does not apply to a {target_kind}")
        rk = DEFER_DISPOSITIONS[disposition][0]
        if target_kind == GROUP and not ev_mod.group_members(self.conn, target_ref):
            raise ValueError("this content is not a current exact-duplicate group")
        if target_kind == VISUAL_PAIR and ev_mod.pair_of(self.conn, target_ref) is None:
            raise ValueError("no such visual candidate pair")
        if target_kind == METADATA_QUESTION and not ev_mod.question_binding(self.conn, target_ref):
            raise ValueError("no such metadata question")

        def work():
            op = self._operation("review", command_id, note)
            eid = self._deferral_row(op, target_kind, target_ref, disposition, until)
            return {"review_event_id": eid, "operation_id": op, "return_kind": rk}
        return self._run(work)

    def skip(self, target_kind, target_ref, command_id=None):
        """Skip: an audit row that changes nothing -- not a deferral."""
        def work():
            op = self._operation("review", command_id, None)
            eid = self._event_row(op, target_kind, target_ref, EVENT_SKIPPED)
            return {"review_event_id": eid, "operation_id": op}
        return self._run(work)

    def restore(self, target_kind, target_ref, refers_to=None, reason=None, command_id=None, withdraw=()):
        """A person restores a target to the queue: ends the deferral (or, with
        no reference, every open deferral, blocker and flag on the target). A
        B8 defer_review decision on the target may be withdrawn in the same
        operation (`withdraw`)."""
        withdraw_ids = [int(w) for w in withdraw]

        def work():
            op = self._operation("review", command_id, reason)
            for w in withdraw_ids:
                self._withdraw_row(w, op, "restored to the queue")
            eid = self._event_row(op, target_kind, target_ref, EVENT_RESTORED, RETURN_MANUAL, reason or "restored", None,
                                  {}, refers_to)
            return {"review_event_id": eid, "operation_id": op, "withdrawn": withdraw_ids}
        return self._run(work)

    def system_events(self, events, note=None):
        """The detector's rows, under one operation whose actor is the
        evidence, not a person. Each item: event_kind, target_kind,
        target_ref, and optionally return_kind, return_condition,
        return_on_utc, detail, refers_to. Never a decision."""
        def work():
            op = self._operation("review", None, note, actor_kind=SYSTEM_ACTOR_KIND, actor_id=DETECTOR_AGENT,
                                 agent_kind=DETECTOR_AGENT)
            ids = []
            for e in events:
                ids.append(self._event_row(op, e["target_kind"], e["target_ref"], e["event_kind"], e.get("return_kind"),
                                           e.get("return_condition"), e.get("return_on_utc"), e.get("detail"),
                                           e.get("refers_to")))
            return {"operation_id": op, "review_event_ids": ids}
        return self._run(work)

    # -- decisions -------------------------------------------------------------

    def record_decision(self, kind, target_kind, target_ref, value=True, rationale=None,
                        command_id=None, evidence_binding=None, withdraw=()):
        """Record one decision. Returns {decision_id, operation_id,
        supersedes_decision_id, withdrawn}.

        The registry decides whether the kind may target this target kind
        and what its value may be. The active decision in the same conflict
        domain on the same target (the previous canonical, say) is
        superseded by the new row. `withdraw` names further active decisions
        to withdraw in the same operation -- Keep All withdrawing the
        redundant marks on the group's members is the case.
        """
        if kind == "override_protection":
            raise ValueError("override_protection is recorded through record_override, with its own confirmation")
        kind, target_kind, target_ref, value, evidence_binding = self._validate_decision(kind, target_kind, target_ref, value, evidence_binding)
        withdraw_ids = [int(w) for w in withdraw]

        def work():
            op = self._operation("decision", command_id, rationale)
            withdrawn = [self._withdraw_row(w, op, "withdrawn by a later decision in the same operation")
                         for w in withdraw_ids]
            decision_id, supersedes = self._decision_row(op, kind, target_kind, target_ref, value, evidence_binding, rationale)
            return {"decision_id": decision_id, "operation_id": op,
                    "supersedes_decision_id": supersedes, "withdrawn": withdrawn}
        return self._run(work)

    def _validate_decision(self, kind, target_kind, target_ref, value, evidence_binding):
        """The registry's gate, and the evidence a decision must rest on."""
        spec = decision_kind(kind)
        if target_kind not in spec.target_kinds:
            raise ValueError(f"{kind} cannot target a {target_kind}")
        value = spec.validate_value(value)
        target_ref = str(target_ref)
        if evidence_binding is None and spec.requires_evidence:
            evidence_binding = ev_mod.binding_for(self.conn, target_kind, target_ref)
        if target_kind == GROUP and not (evidence_binding or {}).get("members"):
            raise ValueError("this content is not a current exact-duplicate group")
        if target_kind == VISUAL_PAIR:
            if not (evidence_binding or {}).get("members"):
                raise ValueError("no such visual candidate pair")
            pair_ids = sorted(str(m["file_path_id"]) for m in evidence_binding["members"])
            if kind == "canonical_location" and value not in pair_ids:
                raise ValueError("the preferred image must be one of the pair")
            if kind == "keep_both" and sorted(value) != pair_ids:
                raise ValueError("keep_both names exactly the pair's two images")
        elif kind == "canonical_location":
            member_ids = {str(m["file_path_id"]) for m in evidence_binding.get("members", [])}
            if value not in member_ids:
                raise ValueError("the canonical must be a current member of the group")
        if target_kind == METADATA_QUESTION:
            # Build 9: the question must exist, and a desired value must be of
            # its field's type (the kind-level gate only refuses an empty one).
            if not (evidence_binding or {}).get("question_id"):
                raise ValueError("no such metadata question")
            if kind == "desired_source_metadata_set":
                value = validate_field_value(metadata_field(evidence_binding["semantic_field"]), value)
        return kind, target_kind, target_ref, value, evidence_binding

    # -- metadata (Build 9) --------------------------------------------------------
    #
    # The four metadata kinds are ordinary decisions; these helpers only name
    # the question a desired-metadata decision targets. Nothing here reads a
    # source file (the observed value is Phase 1/2 evidence) or writes one.

    def metadata_question(self, file_path_id, semantic_field, create=False, command_id=None):
        """The question id for one location x one semantic field, or None --
        or, with `create`, a new question under its own operation."""
        metadata_field(semantic_field)
        row = self.conn.execute("SELECT question_id FROM p3_metadata_question WHERE project_id = 1 AND file_path_id = ? "
                                "AND semantic_field = ?", (int(file_path_id), semantic_field)).fetchone()
        if row is not None or not create:
            return int(row[0]) if row is not None else None
        return self._run(lambda: self._question_row(self._operation("metadata_question", command_id, None),
                                                    file_path_id, semantic_field))

    def _question_row(self, operation_id, file_path_id, semantic_field):
        metadata_field(semantic_field)
        if self.conn.execute("SELECT 1 FROM file_path WHERE file_path_id = ?", (int(file_path_id),)).fetchone() is None:
            raise ValueError(f"no such file location: {file_path_id}")
        row = self.conn.execute("SELECT question_id FROM p3_metadata_question WHERE project_id = 1 AND file_path_id = ? "
                                "AND semantic_field = ?", (int(file_path_id), semantic_field)).fetchone()
        if row is not None:
            return int(row[0])
        return int(self.conn.execute("INSERT INTO p3_metadata_question(project_id, file_path_id, semantic_field, operation_id) "
                                     "VALUES(1,?,?,?)", (int(file_path_id), semantic_field, operation_id)).lastrowid)

    def record_metadata_decision(self, kind, file_path_id, semantic_field=None, value=True, rationale=None, command_id=None):
        """One metadata decision in one operation, the question named (and, the
        first time, created) in the same operation.

          project_metadata_set             file_path_id, value={"field", "value"}: the project's own
                                           data, on the file location itself (never a question)
          desired_source_metadata_set      file_path_id + semantic_field, value = the desired value
          desired_source_metadata_clear    file_path_id + semantic_field (value true)
          leave_source_metadata_unchanged  file_path_id + semantic_field (value true)

        Returns {decision_id, operation_id, supersedes_decision_id, question_id}."""
        if kind not in METADATA_DECISION_KINDS:
            raise ValueError(f"{kind} is not a metadata decision kind")
        if kind == "project_metadata_set":
            result = self.record_decision(kind, LOCATION, file_path_id, value, rationale=rationale, command_id=command_id)
            result["question_id"] = None
            return result
        spec = metadata_field(semantic_field)
        dk = decision_kind(kind)
        value = dk.validate_value(value)
        if kind == "desired_source_metadata_set":
            value = validate_field_value(spec, value)

        def work():
            op = self._operation("decision", command_id, rationale)
            qid = self._question_row(op, file_path_id, spec.key)
            binding = ev_mod.question_binding(self.conn, qid)
            decision_id, supersedes = self._decision_row(op, kind, METADATA_QUESTION, qid, value, binding, rationale)
            # (B17) A decision ends an open deferral on the question, in the
            # same operation: a deferral is "not deciding yet".
            restored = [self._event_row(op, METADATA_QUESTION, qid, EVENT_RESTORED, RETURN_MANUAL, "decided", None, {}, e)
                        for e in self._open_deferrals(METADATA_QUESTION, qid)]
            return {"decision_id": decision_id, "operation_id": op, "supersedes_decision_id": supersedes,
                    "withdrawn": [], "question_id": qid, "restored": restored}
        return self._run(work)

    def _open_deferrals(self, target_kind, target_ref):
        """Deferred events on the target that no restore has ended yet."""
        rows = self.conn.execute("SELECT review_event_id, event_kind, refers_to_review_event_id FROM p3_review_event "
                                 "WHERE target_kind = ? AND target_ref = ? ORDER BY review_event_id",
                                 (target_kind, str(target_ref))).fetchall()
        open_ = []
        for r in rows:
            if r["event_kind"] == EVENT_DEFERRED:
                open_ = [int(r["review_event_id"])]          # a new deferral replaces an open one
            elif r["event_kind"] == EVENT_RESTORED:
                ref = r["refers_to_review_event_id"]
                open_ = [] if ref is None else [e for e in open_ if e != int(ref)]
        return open_

    def defer_metadata(self, file_path_id, semantic_field, disposition="defer_indefinitely", until=None, note=None,
                       command_id=None):
        """(B17) Defer one field of one file: the question named (created the
        first time) and the deferral recorded in ONE operation -- deferring is
        a person recording something, so it may name a question; looking never
        does. Build 3's deferral: a date, or the evidence changing."""
        spec = metadata_field(semantic_field)
        if disposition not in DEFER_CHOICES[METADATA_QUESTION]:
            raise ValueError(f"{DEFER_DISPOSITIONS.get(disposition, (None, disposition))[1]} does not apply to a metadata field")

        def work():
            op = self._operation("review", command_id, note)
            qid = self._question_row(op, file_path_id, spec.key)
            eid = self._deferral_row(op, METADATA_QUESTION, qid, disposition, until)
            return {"review_event_id": eid, "operation_id": op, "question_id": qid}
        return self._run(work)

    def record_decisions(self, decisions, rationale=None, command_id=None, withdraw=()):
        """Several decisions in ONE operation: every row lands or none does.
        `decisions` = [(kind, target_kind, target_ref, value)]. Each row is
        its own decision -- superseding the chain head in its own conflict
        domain -- so each can later be superseded or withdrawn on its own
        (the research's rule: relationship and retention are two rows even
        when one click records both). `withdraw` names active decisions to
        withdraw in the same operation. Returns {operation_id, decision_ids,
        superseded, withdrawn}."""
        if not decisions:
            raise ValueError("nothing to record")
        plan = []
        for kind, target_kind, target_ref, value in decisions:
            if kind == "override_protection":
                raise ValueError("override_protection is recorded through record_override, with its own confirmation")
            plan.append(self._validate_decision(kind, target_kind, target_ref, value, None))
        withdraw_ids = [int(w) for w in withdraw]

        def work():
            op = self._operation("decision", command_id, rationale)
            withdrawn = [self._withdraw_row(w, op, "withdrawn by a later decision in the same operation")
                         for w in withdraw_ids]
            ids, superseded = [], []
            for kind, target_kind, target_ref, value, binding in plan:
                did, sup = self._decision_row(op, kind, target_kind, target_ref, value, binding, rationale)
                ids.append(did)
                if sup is not None:
                    superseded.append(sup)
            return {"operation_id": op, "decision_ids": ids, "superseded": superseded, "withdrawn": withdrawn}
        return self._run(work)

    def withdraw_many(self, decision_ids, reason=None, command_id=None):
        """Withdraw several active decisions in one operation (the Image
        Game's Undo of a fast action). A decision no longer active is
        refused -- the whole operation with it."""
        ids = [int(d) for d in decision_ids]
        if not ids:
            raise ValueError("nothing to withdraw")

        def work():
            op = self._operation("withdrawal", command_id, reason)
            for did in ids:
                self._withdraw_row(did, op, reason)
            return {"operation_id": op, "withdrawn": ids}
        return self._run(work)

    def _decision_row(self, operation_id, kind, target_kind, target_ref, value, evidence_binding, rationale,
                      origin=ORIGIN_EXPLICIT_HUMAN, origin_ref=None):
        """One decision row inside an open operation: supersedes the chain
        head in its conflict domain. Returns (decision_id, supersedes)."""
        origin_kind(origin)
        domain = decision_kind(kind).domain_key(value)
        supersedes = self._chain_head(target_kind, target_ref, domain)
        cur = self.conn.execute(
            "INSERT INTO p3_decision(project_id, operation_id, target_kind, target_ref, decision_kind, value_json, "
            "origin_kind, origin_ref, evidence_binding_json, supersedes_decision_id, rationale) "
            "VALUES(1,?,?,?,?,?,?,?,?,?,?)",
            (operation_id, target_kind, str(target_ref), kind, json.dumps(value, sort_keys=True), origin,
             str(origin_ref) if origin_ref is not None else None,
             json.dumps(evidence_binding or {}, sort_keys=True), supersedes, rationale))
        return int(cur.lastrowid), supersedes

    def _withdraw_row(self, decision_id, operation_id, reason):
        active = self.conn.execute(
            "SELECT 1 FROM p3_decision d WHERE d.decision_id = ? "
            "   AND NOT EXISTS (SELECT 1 FROM p3_decision_withdrawal w WHERE w.decision_id = d.decision_id) "
            "   AND NOT EXISTS (SELECT 1 FROM p3_decision x WHERE x.supersedes_decision_id = d.decision_id)",
            (int(decision_id),)).fetchone()
        if active is None:
            raise ValueError(f"decision {decision_id} is not active (already withdrawn or superseded)")
        self.conn.execute(
            "INSERT INTO p3_decision_withdrawal(decision_id, operation_id, reason) VALUES(?,?,?)",
            (int(decision_id), operation_id, reason))
        return int(decision_id)

    def withdraw(self, decision_id, reason=None, command_id=None):
        """Withdraw one active decision (Undo). The row stays; a withdrawal
        row is added. Does not revive whatever it superseded."""
        def work():
            op = self._operation("withdrawal", command_id, reason)
            self._withdraw_row(decision_id, op, reason)
            return {"decision_id": int(decision_id), "operation_id": op}
        return self._run(work)

    def record_override(self, file_path_id, policy_id, rationale, command_id=None, confirmed=False):
        """The one way past a protection: names the policy version defeated,
        for this location only, with a rationale; demands the caller's
        explicit confirmation flag so no code path records one in passing."""
        spec = decision_kind("override_protection")
        if spec.confirmation == CONFIRM_EXPLICIT_OVERRIDE and not confirmed:
            raise ValueError("overriding protection requires explicit confirmation")
        if not (rationale or "").strip():
            raise ValueError("overriding protection requires a rationale")
        policy = self.policy(policy_id)
        if policy is None or policy["status"] != "active":
            raise ValueError("no active policy with that id")
        if policy_kind(policy["kind"]).effect != "protect":
            raise ValueError("only a protection policy can be overridden")
        loc = ev_mod.location_of(self.conn, file_path_id)
        if loc is None:
            raise ValueError("that file is not in a current exact-duplicate group")
        pv = PolicyVersion(str(policy["policy_version_id"]), str(policy["policy_id"]), policy["kind"],
                           policy["scope"], policy["effect"], policy["version_no"], True)
        if not _policy_covers(pv, loc):
            raise ValueError("that policy does not cover this location")
        value = spec.validate_value({"policy_id": policy["policy_id"], "policy_version_id": policy["policy_version_id"]})
        binding = ev_mod.location_binding(self.conn, file_path_id)
        domain = spec.domain_key(value)
        supersedes = self._chain_head(LOCATION, str(file_path_id), domain)

        def work():
            op = self._operation("override_protection", command_id, rationale)
            cur = self.conn.execute(
                "INSERT INTO p3_decision(project_id, operation_id, target_kind, target_ref, decision_kind, value_json, "
                "origin_kind, origin_ref, evidence_binding_json, supersedes_decision_id, rationale) "
                "VALUES(1,?,?,?,?,?,?,?,?,?,?)",
                (op, LOCATION, str(file_path_id), "override_protection", json.dumps(value, sort_keys=True),
                 "explicit_human", None, json.dumps(binding, sort_keys=True), supersedes, rationale))
            return {"decision_id": int(cur.lastrowid), "operation_id": op, "supersedes_decision_id": supersedes}
        return self._run(work)

    # -- bulk batches (Build 4) ---------------------------------------------------

    def commit_bulk(self, preview, note=None, command_id=None):
        """Record a previewed batch: exactly what the preview showed, in one
        operation of kind 'bulk' -- the batch row (scope, frozen query,
        the counts as previewed), one member row per target with its
        disposition, and a decision (origin bulk_explicit_human, origin_ref
        = the batch id) for every 'decided' target, bound to the evidence
        the preview captured; a deferral batch records deferred events
        instead. Never re-evaluates. Returns {batch_id, operation_id,
        decisions, events, members}."""
        spec = bulk_action(preview.action)
        if preview.mode != MODE_PRESERVE:
            raise ValueError("only the preserve mode is recorded in this build")
        if preview.record_mark is not None:
            now_mark = int(self.conn.execute("SELECT MAX(operation_id) FROM p3_operation").fetchone()[0] or 0)
            if now_mark != preview.record_mark:
                raise ValueError("the decision record changed since this preview was made (operations "
                                 f"{preview.record_mark} -> {now_mark}); preview again")
        if getattr(preview, "evidence_mark", None) is not None:
            # A batch is judged on evidence as well as on the record (pairs
            # since Build 6, groups since Build 7 -- one check for every target
            # kind). A scan, a re-hash or a re-analysis adds no operation, so
            # the record guard alone would not see it; the evidence mark does.
            now_evidence = ev_mod.evidence_mark(self.conn)
            if now_evidence != preview.evidence_mark:
                raise ValueError("the evidence changed since this preview was made (a scan, a re-hash or a re-analysis: "
                                 f"{preview.evidence_mark} -> {now_evidence}); preview again")
        for cand in preview.candidates:
            disposition(cand.disposition)
        decided = [c for c in preview.candidates if c.disposition == DISP_DECIDED]
        plan = []
        for cand in decided:
            for kind, tk, ref, value in cand.to_record:
                binding = cand.bindings.get((kind, tk, ref))
                plan.append((cand, self._validate_decision(kind, tk, ref, value, binding)))
        events = []
        for cand in decided:
            for disp, until in cand.events_to_record:
                events.append((cand, disp, until))
        if not preview.candidates:
            raise ValueError("nothing in scope")

        def work():
            op = self._operation("bulk", command_id, note)
            cur = self.conn.execute(
                "INSERT INTO p3_bulk_batch(project_id, operation_id, scope_kind, query_json, frozen, committed, action, "
                "parameters_json, mode, preview_json, note) VALUES(1,?,?,?,1,1,?,?,?,?,?)",
                (op, preview.scope_kind, json.dumps(preview.query, sort_keys=True) if preview.query is not None else None,
                 preview.action, json.dumps(preview.parameters, sort_keys=True, default=str), preview.mode,
                 json.dumps(preview.as_dict(), sort_keys=True, default=str), note))
            batch_id = int(cur.lastrowid)
            for cand in preview.candidates:
                self.conn.execute(
                    "INSERT INTO p3_bulk_member(batch_id, target_kind, target_ref, disposition, detail) VALUES(?,?,?,?,?)",
                    (batch_id, cand.target_kind, str(cand.target_ref), cand.disposition, cand.detail or None))
            ids = []
            for _cand, (kind, tk, ref, value, binding) in plan:
                did, _sup = self._decision_row(op, kind, tk, ref, value, binding, note, ORIGIN_BULK_EXPLICIT_HUMAN, batch_id)
                ids.append(did)
            eids = []
            for cand, disp, until in events:
                eids.append(self._deferral_row(op, cand.target_kind, cand.target_ref, disp, until))
            return {"batch_id": batch_id, "operation_id": op, "decisions": len(ids), "decision_ids": ids,
                    "events": len(eids), "members": len(preview.candidates), "action": spec.key}
        return self._run(work)

    def _deferral_row(self, operation_id, target_kind, target_ref, disposition_="defer_indefinitely", until=None):
        """One deferred event inside an open operation (the store's defer(),
        factored so a batch can record many under one operation)."""
        if disposition_ not in DEFER_DISPOSITIONS:
            raise ValueError(f"unknown deferral disposition: {disposition_!r}")
        rk = DEFER_DISPOSITIONS[disposition_][0]
        return_on = None
        condition = DEFER_DISPOSITIONS[disposition_][1]
        detail = {"disposition": disposition_}
        if rk == RETURN_TIME:
            when = str(until or "").strip()
            if len(when) == 10:
                when += "T00:00:00Z"
            if len(when) < 19 or when[4] != "-" or when[7] != "-" or when[10] != "T":
                raise ValueError("snoozing until a date needs the date as YYYY-MM-DD")
            return_on = when
            condition = f"until {when[:10]}"
        elif rk == RETURN_EVIDENCE_CHANGE:
            detail.update(ev_mod.binding_for(self.conn, target_kind, target_ref))
        return self._event_row(operation_id, target_kind, target_ref, EVENT_DEFERRED, rk, condition, return_on, detail)

    def batch_decisions(self, batch_id):
        """Every decision the batch recorded, with whether each still speaks."""
        rows = self.conn.execute(
            "SELECT d.decision_id, d.decision_kind, d.target_kind, d.target_ref, d.value_json, w.withdrawal_id, "
            "       (SELECT COUNT(*) FROM p3_decision x WHERE x.supersedes_decision_id = d.decision_id) AS superseded_by "
            "  FROM p3_decision d LEFT JOIN p3_decision_withdrawal w ON w.decision_id = d.decision_id "
            " WHERE d.origin_kind = ? AND d.origin_ref = ? ORDER BY d.decision_id",
            (ORIGIN_BULK_EXPLICIT_HUMAN, str(int(batch_id)))).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["value"] = json.loads(d.pop("value_json"))
            d["withdrawn"] = r["withdrawal_id"] is not None
            d["superseded"] = bool(r["superseded_by"])
            d["active"] = not d["withdrawn"] and not d["superseded"]
            out.append(d)
        return out

    def batch_events(self, batch_id, opened=None):
        """The deferrals a deferral batch recorded, with whether each is still open."""
        row = self.conn.execute("SELECT operation_id FROM p3_bulk_batch WHERE batch_id=?", (int(batch_id),)).fetchone()
        if row is None:
            return []
        if opened is None:
            opened = RT.open_events(ev_mod.load_events(self.conn))
        out = []
        for e in self.conn.execute("SELECT * FROM p3_review_event WHERE operation_id=? AND event_kind=? ORDER BY review_event_id",
                                   (row["operation_id"], EVENT_DEFERRED)):
            slot = opened.get((e["target_kind"], str(e["target_ref"])), {})
            is_open = any(x.review_event_id == str(e["review_event_id"]) for x in slot.get(EVENT_DEFERRED, []))
            d = dict(e)
            d["open"] = is_open
            out.append(d)
        return out

    def batches(self, batch_id=None):
        """Every batch, newest first, with what it recorded and what still
        stands -- or (B14) the one batch `batch_id` names."""
        rows = self.conn.execute(
            "SELECT b.*, o.occurred_utc, o.actor_id, "
            "       (SELECT COUNT(*) FROM p3_bulk_member m WHERE m.batch_id = b.batch_id) AS members "
            "  FROM p3_bulk_batch b JOIN p3_operation o ON o.operation_id = b.operation_id "
            " WHERE b.project_id = 1" + (" AND b.batch_id = ?" if batch_id is not None else "") + " ORDER BY b.batch_id DESC",
            (int(batch_id),) if batch_id is not None else ()).fetchall()
        out = []
        opened = RT.open_events(ev_mod.load_events(self.conn)) if any(bulk_action(r["action"]).records_events for r in rows) else {}
        for r in rows:
            d = dict(r)
            d["query"] = json.loads(d.pop("query_json")) if d.get("query_json") else None
            d["parameters"] = json.loads(d.pop("parameters_json") or "{}")
            d["preview"] = json.loads(d.pop("preview_json") or "{}")
            decisions = self.batch_decisions(d["batch_id"])
            d["decisions"] = len(decisions)
            d["active_decisions"] = sum(1 for x in decisions if x["active"])
            d["withdrawn_decisions"] = sum(1 for x in decisions if x["withdrawn"])
            events = self.batch_events(d["batch_id"], opened) if bulk_action(d["action"]).records_events else []
            d["events"] = len(events)
            d["open_events"] = sum(1 for x in events if x["open"])
            d["in_force"] = d["active_decisions"] + d["open_events"]
            d["label"] = bulk_action(d["action"]).label
            out.append(d)
        return out

    def batch(self, batch_id):
        found = self.batches(batch_id)                  # B14: that batch only (was: every batch, then pick one)
        return found[0] if found else None

    def batch_members(self, batch_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM p3_bulk_member WHERE batch_id = ? ORDER BY member_id", (int(batch_id),))]

    def undo_batch(self, batch_id, reason=None, command_id=None):
        """Reverse a batch: withdraw every decision it recorded that still
        speaks, and restore every deferral it recorded that is still open --
        one operation of kind 'bulk_undo'. A decision a person has since
        superseded or withdrawn is left alone (their later intent stands).
        The batch and its member rows stay untouched. Returns the counts."""
        b = self.batch(batch_id)
        if b is None:
            raise ValueError(f"no batch {batch_id}")
        active = [d["decision_id"] for d in self.batch_decisions(batch_id) if d["active"]]
        open_events = [e for e in self.batch_events(batch_id) if e["open"]]
        if not active and not open_events:
            raise ValueError("nothing from this batch is still in force")
        why = reason or f"batch #{int(batch_id)} undone"

        def work():
            op = self._operation("bulk_undo", command_id, why)
            for did in active:
                self._withdraw_row(did, op, why)
            for e in open_events:
                self._event_row(op, e["target_kind"], e["target_ref"], EVENT_RESTORED, RETURN_MANUAL, why, None, {},
                                e["review_event_id"])
            return {"batch_id": int(batch_id), "operation_id": op, "withdrawn": len(active), "restored": len(open_events)}
        return self._run(work)

    # -- policies --------------------------------------------------------------

    def policies(self, include_retired=False):
        """Every policy's current version."""
        rows = self.conn.execute(
            "SELECT p.policy_id, p.kind, p.created_operation_id, v.policy_version_id, v.version_no, v.scope_json, "
            "       v.effect_json, v.status, v.rationale, o.occurred_utc, o.actor_id, "
            "       (SELECT COUNT(*) FROM p3_policy_version x WHERE x.policy_id = p.policy_id) AS versions "
            "  FROM p3_policy p JOIN p3_policy_version v ON v.policy_id = p.policy_id "
            "  JOIN p3_operation o ON o.operation_id = v.operation_id "
            " WHERE p.project_id = 1 AND v.version_no = (SELECT MAX(version_no) FROM p3_policy_version y WHERE y.policy_id = p.policy_id) "
            " ORDER BY p.policy_id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["scope"] = json.loads(d.pop("scope_json"))
            d["effect"] = json.loads(d.pop("effect_json") or "{}")
            d["label"] = policy_kind(d["kind"]).label
            d["scope_text"] = d["scope"].get("root_path") or d["scope"].get("path") or ""
            if include_retired or d["status"] == "active":
                out.append(d)
        return out

    def policy(self, policy_id):
        for p in self.policies(include_retired=True):
            if int(p["policy_id"]) == int(policy_id):
                return p
        return None

    def policy_history(self, policy_id):
        rows = self.conn.execute(
            "SELECT v.*, o.occurred_utc, o.actor_id FROM p3_policy_version v JOIN p3_operation o ON o.operation_id = v.operation_id "
            " WHERE v.policy_id = ? ORDER BY v.version_no", (int(policy_id),)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["scope"] = json.loads(d.pop("scope_json"))
            d["effect"] = json.loads(d.pop("effect_json") or "{}")
            out.append(d)
        return out

    def create_policy(self, kind, scope, effect=None, rationale=None, command_id=None):
        spec = policy_kind(kind)
        scope = spec.validate_scope(scope)
        effect = spec.validate_effect(effect)

        def work():
            op = self._operation("policy", command_id, rationale)
            cur = self.conn.execute(
                "INSERT INTO p3_policy(project_id, kind, created_operation_id) VALUES(1,?,?)", (kind, op))
            policy_id = int(cur.lastrowid)
            cur = self.conn.execute(
                "INSERT INTO p3_policy_version(policy_id, operation_id, version_no, scope_json, effect_json, status, "
                "supersedes_policy_version_id, rationale) VALUES(?,?,1,?,?,'active',NULL,?)",
                (policy_id, op, json.dumps(scope, sort_keys=True), json.dumps(effect, sort_keys=True), rationale))
            return {"policy_id": policy_id, "policy_version_id": int(cur.lastrowid), "version_no": 1}
        return self._run(work)

    def _new_version(self, policy_id, scope, effect, status, rationale, command_id):
        current = self.policy(policy_id)
        if current is None:
            raise ValueError(f"no policy {policy_id}")
        spec = policy_kind(current["kind"])
        scope = spec.validate_scope(scope if scope is not None else current["scope"])
        effect = spec.validate_effect(effect if effect is not None else current["effect"])

        def work():
            op = self._operation("policy", command_id, rationale)
            cur = self.conn.execute(
                "INSERT INTO p3_policy_version(policy_id, operation_id, version_no, scope_json, effect_json, status, "
                "supersedes_policy_version_id, rationale) VALUES(?,?,?,?,?,?,?,?)",
                (int(policy_id), op, int(current["version_no"]) + 1, json.dumps(scope, sort_keys=True),
                 json.dumps(effect, sort_keys=True), status, int(current["policy_version_id"]), rationale))
            return {"policy_id": int(policy_id), "policy_version_id": int(cur.lastrowid),
                    "version_no": int(current["version_no"]) + 1, "status": status}
        return self._run(work)

    def revise_policy(self, policy_id, scope=None, effect=None, rationale=None, command_id=None):
        """A new active version (re-activates a retired policy, too)."""
        return self._new_version(policy_id, scope, effect, "active", rationale, command_id)

    def retire_policy(self, policy_id, rationale=None, command_id=None):
        """Switch a policy off: a new version whose status is 'retired'."""
        return self._new_version(policy_id, None, None, "retired", rationale, command_id)


# -- a human-readable copy of the record, on disk (Axiom 19) --------------------

    # -- plans (Build 8) ---------------------------------------------------------
    #
    # A revision is written once, in one 'plan_build' operation, and never
    # edited: locking adds its fingerprint and a lock event; drift adds an
    # obsolete event. Nothing here reads or writes a source file.

    def _marks(self):
        record = int(self.conn.execute("SELECT MAX(operation_id) FROM p3_operation").fetchone()[0] or 0)
        return record, ev_mod.evidence_mark(self.conn)

    def record_plan_revision(self, draft, name=None, plan_id=None, note=None, command_id=None):
        """Freeze a built draft as a new revision: the plan (created when
        `plan_id` is None), the revision (status 'previewed', with the record
        and evidence marks it was built on and the preview's summary), every
        item with its decision / policy links, dependencies and preconditions,
        and a 'previewed' event. Earlier revisions of the plan that are
        neither locked nor obsolete become 'superseded'. Returns
        {plan_id, plan_revision_id, revision_no, operation_id, items}."""
        from . import plans as PL
        if not PL.acyclic(draft.items):
            raise ValueError("the plan's dependency graph has a cycle")
        for it in draft.items:
            action_kind(it.action_kind)

        def work():
            op = self._operation("plan_build", command_id, note)
            pid = plan_id
            if pid is None:
                pid = int(self.conn.execute("INSERT INTO p3_plan(project_id, operation_id, name, note) VALUES(1,?,?,?)",
                                            (op, name or "Plan", note)).lastrowid)
            elif self.conn.execute("SELECT 1 FROM p3_plan WHERE plan_id = ?", (int(pid),)).fetchone() is None:
                raise ValueError(f"no plan {pid}")
            prev = self.conn.execute(
                "SELECT plan_revision_id, revision_no, status FROM p3_plan_revision WHERE plan_id = ? "
                "ORDER BY revision_no DESC", (int(pid),)).fetchall()
            revision_no = (int(prev[0]["revision_no"]) + 1) if prev else 1
            record, evidence = self._marks()
            basis = {"record_mark": record, "evidence_mark": evidence, "routing_version": RT.ROUTING_VERSION,
                     "built_utc": utc_now()}
            rid = int(self.conn.execute(
                "INSERT INTO p3_plan_revision(plan_id, revision_no, operation_id, status, locked, obsolete, "
                "supersedes_plan_revision_id, basis_json, summary_json) VALUES(?,?,?,?,0,0,?,?,?)",
                (int(pid), revision_no, op, "previewed", int(prev[0]["plan_revision_id"]) if prev else None,
                 json.dumps(basis, sort_keys=True), json.dumps(draft.summary, sort_keys=True, default=str))).lastrowid)
            for r in prev:
                if r["status"] in ("draft", "previewed", "review_ready"):
                    self.conn.execute("UPDATE p3_plan_revision SET status = 'superseded' WHERE plan_revision_id = ?",
                                      (int(r["plan_revision_id"]),))
                    self._plan_event(r["plan_revision_id"], op, "superseded", {"by_revision": rid})
            ids = {}
            for it in draft.items:
                ids[it.item_no] = int(self.conn.execute(
                    "INSERT INTO p3_plan_item(plan_revision_id, item_no, action_kind, target_kind, target_ref, source_kind, "
                    "source_ref, desired_effect_json, evidence_binding_json, estimated_effect_json, postconditions_json, "
                    "impact_class, state, blocked_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, it.item_no, it.action_kind, it.target_kind, str(it.target_ref), it.source_kind,
                     str(it.source_ref) if it.source_ref is not None else None,
                     json.dumps({**it.desired_effect, "sources": [[k, str(r)] for k, r in it.sources]}, sort_keys=True),
                     json.dumps(it.evidence_binding, sort_keys=True), json.dumps(it.estimated_effect, sort_keys=True),
                     json.dumps(list(it.postconditions)), it.impact_class, it.state, it.blocked_reason)).lastrowid)
            for it in draft.items:
                iid = ids[it.item_no]
                for role, dids in ((LINK_JUSTIFIES, it.decision_ids), (LINK_RETAINS, it.retaining_ids)):
                    for did in dids:
                        self.conn.execute("INSERT OR IGNORE INTO p3_plan_item_decision(plan_item_id, decision_id, role) "
                                          "VALUES(?,?,?)", (iid, int(did), role))
                for pv in it.policy_version_ids:
                    self.conn.execute("INSERT OR IGNORE INTO p3_plan_item_policy(plan_item_id, policy_version_id) VALUES(?,?)",
                                      (iid, int(pv)))
                for dep in it.depends_on:
                    self.conn.execute("INSERT INTO p3_plan_dependency(plan_item_id, depends_on_item_id) VALUES(?,?)",
                                      (iid, ids[dep]))
                for c in it.preconditions:
                    self.conn.execute(
                        "INSERT INTO p3_plan_precondition(plan_item_id, condition_key, condition_class, severity, stage, "
                        "parameters_json, result, detail) VALUES(?,?,?,?,?,?,?,?)",
                        (iid, c.key, c.klass, c.severity, c.stage, json.dumps(c.params, sort_keys=True, default=str),
                         c.result, c.detail or None))
            self._plan_event(rid, op, "previewed", {"items": len(draft.items), "summary": draft.summary})
            return {"plan_id": int(pid), "plan_revision_id": rid, "revision_no": revision_no, "operation_id": op,
                    "items": len(draft.items)}
        return self._run(work)

    def _plan_event(self, revision_id, operation_id, kind, detail=None):
        if kind not in PLAN_EVENT_KINDS:
            raise ValueError(f"unknown plan event kind: {kind!r}")
        self.conn.execute("INSERT INTO p3_plan_event(plan_revision_id, operation_id, event_kind, detail_json) VALUES(?,?,?,?)",
                          (int(revision_id), int(operation_id), kind, json.dumps(detail or {}, sort_keys=True, default=str)))

    def plan_revision(self, revision_id):
        """(revision row as a dict, [PlanItem]) -- read back exactly as frozen."""
        from . import plans as PL
        row = self.conn.execute("SELECT r.*, p.name FROM p3_plan_revision r JOIN p3_plan p ON p.plan_id = r.plan_id "
                                "WHERE r.plan_revision_id = ?", (int(revision_id),)).fetchone()
        if row is None:
            return None, []
        rev = dict(row)
        rev["basis"] = json.loads(rev.pop("basis_json") or "{}")
        rev["summary"] = json.loads(rev.pop("summary_json") or "{}")
        rev["events"] = [dict(r, detail=json.loads(r["detail_json"] or "{}")) for r in self.conn.execute(
            "SELECT e.*, o.occurred_utc, o.actor_kind FROM p3_plan_event e JOIN p3_operation o ON o.operation_id = e.operation_id "
            "WHERE e.plan_revision_id = ? ORDER BY e.plan_event_id", (int(revision_id),))]
        rows = self.conn.execute("SELECT * FROM p3_plan_item WHERE plan_revision_id = ? ORDER BY item_no",
                                 (int(revision_id),)).fetchall()
        by_id = {int(r["plan_item_id"]): int(r["item_no"]) for r in rows}
        links, pols, deps, pres = {}, {}, {}, {}
        marks = ",".join("?" for _ in by_id) or "NULL"
        args = tuple(by_id)
        for r in self.conn.execute(f"SELECT * FROM p3_plan_item_decision WHERE plan_item_id IN ({marks})", args):
            links.setdefault(int(r["plan_item_id"]), {}).setdefault(r["role"], []).append(str(r["decision_id"]))
        for r in self.conn.execute(f"SELECT * FROM p3_plan_item_policy WHERE plan_item_id IN ({marks})", args):
            pols.setdefault(int(r["plan_item_id"]), []).append(str(r["policy_version_id"]))
        for r in self.conn.execute(f"SELECT * FROM p3_plan_dependency WHERE plan_item_id IN ({marks})", args):
            deps.setdefault(int(r["plan_item_id"]), []).append(by_id[int(r["depends_on_item_id"])])
        for r in self.conn.execute(f"SELECT * FROM p3_plan_precondition WHERE plan_item_id IN ({marks}) "
                                   "ORDER BY plan_precondition_id", args):
            pres.setdefault(int(r["plan_item_id"]), []).append(PL.Precondition(
                r["condition_key"], r["condition_class"], r["severity"], r["stage"], json.loads(r["parameters_json"] or "{}"),
                r["result"], r["detail"] or ""))
        items = []
        for r in rows:
            iid = int(r["plan_item_id"])
            effect = json.loads(r["desired_effect_json"] or "{}")
            sources = tuple((k, str(ref)) for k, ref in effect.pop("sources", []))
            items.append(PL.PlanItem(
                int(r["item_no"]), r["action_kind"], r["target_kind"], r["target_ref"], sources, effect,
                decision_ids=PL._ids(links.get(iid, {}).get(LINK_JUSTIFIES, [])),
                retaining_ids=PL._ids(links.get(iid, {}).get(LINK_RETAINS, [])),
                policy_version_ids=PL._ids(pols.get(iid, [])),
                evidence_binding=json.loads(r["evidence_binding_json"] or "{}"),
                preconditions=pres.get(iid, []), postconditions=tuple(json.loads(r["postconditions_json"] or "[]")),
                depends_on=tuple(sorted(deps.get(iid, []))), estimated_effect=json.loads(r["estimated_effect_json"] or "{}"),
                impact_class=r["impact_class"], state=r["state"], blocked_reason=r["blocked_reason"]))
        return rev, items

    def plans(self):
        """Every plan with its revisions, newest revision first."""
        out = []
        for p in self.conn.execute("SELECT p.*, o.occurred_utc FROM p3_plan p JOIN p3_operation o ON o.operation_id = p.operation_id "
                                   "WHERE p.project_id = 1 ORDER BY p.plan_id DESC"):
            d = dict(p)
            d["revisions"] = [dict(r, summary=json.loads(r["summary_json"] or "{}")) for r in self.conn.execute(
                "SELECT r.plan_revision_id, r.revision_no, r.status, r.fingerprint, r.locked, r.obsolete, r.summary_json, "
                "       o.occurred_utc FROM p3_plan_revision r JOIN p3_operation o ON o.operation_id = r.operation_id "
                " WHERE r.plan_id = ? ORDER BY r.revision_no DESC", (int(d["plan_id"]),))]
            out.append(d)
        return out

    def revisions_depending_on(self, decision_ids):
        """The dependency index (R7 §33): locked, standing revisions that any
        of these decisions feeds -- one indexed lookup, not a scan."""
        ids = [int(i) for i in decision_ids if str(i).isdigit()]
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        return [int(r[0]) for r in self.conn.execute(
            f"SELECT DISTINCT i.plan_revision_id FROM p3_plan_item_decision l JOIN p3_plan_item i ON i.plan_item_id = l.plan_item_id "
            f"  JOIN p3_plan_revision r ON r.plan_revision_id = i.plan_revision_id "
            f" WHERE l.decision_id IN ({marks}) AND r.locked = 1 AND r.obsolete = 0 ORDER BY 1", ids)]

    def lock_plan_revision(self, revision_id, note=None, command_id=None):
        """Lock a previewed revision (P3.R7 §29): (1) verify the Phase 3
        blockers still stand as previewed -- the decision record and the
        evidence must be where the preview found them, else build again;
        (2) canonicalize the frozen payload; (3) compute its SHA-256;
        (4) mark it immutable (locked); (5) record the lock event; (6) capture
        the exact dependency set in that event. No source file is read.
        Returns {plan_revision_id, fingerprint, operation_id}."""
        from . import plans as PL
        rev, items = self.plan_revision(revision_id)
        if rev is None:
            raise ValueError(f"no plan revision {revision_id}")
        if rev["status"] not in ("previewed", "review_ready"):
            raise ValueError(f"revision {rev['revision_no']} is {rev['status']}: only a previewed revision can be locked")
        record, evidence = self._marks()
        if record != rev["basis"].get("record_mark") or evidence != rev["basis"].get("evidence_mark"):
            raise ValueError("the decision record or the evidence changed since this revision was previewed "
                             f"(operations {rev['basis'].get('record_mark')} -> {record}); build a new revision")
        fp = PL.fingerprint(items)
        deps = {"decisions": sorted({d for it in items for d in it.decision_ids + it.retaining_ids}, key=PL._id_order),
                "policy_versions": sorted({p for it in items for p in it.policy_version_ids}, key=PL._id_order),
                "evidence_bindings": sum(1 for it in items if it.evidence_binding), "record_mark": record,
                "evidence_mark": evidence}

        def work():
            op = self._operation("plan_lock", command_id, note)
            self.conn.execute("UPDATE p3_plan_revision SET status = 'locked', locked = 1, fingerprint = ? "
                              "WHERE plan_revision_id = ?", (fp, int(revision_id)))
            self._plan_event(revision_id, op, "locked", {"fingerprint": fp, "dependencies": deps})
            return {"plan_revision_id": int(revision_id), "fingerprint": fp, "operation_id": op}
        return self._run(work)

    def mark_plans_obsolete(self, findings, note=None):
        """The detector's verdicts: {revision_id: [(code, words, detail)]}. One
        system operation; each revision gets an 'obsolete' event with the
        reasons and becomes status obsolete -- its items, links and
        fingerprint are never touched (P3-A97)."""
        if not findings:
            return {"operation_id": None, "revisions": []}

        def work():
            op = self._operation("plan_obsolete", None, note, actor_kind=SYSTEM_ACTOR_KIND, actor_id=DETECTOR_AGENT,
                                 agent_kind=DETECTOR_AGENT)
            for rid, reasons in sorted(findings.items()):
                self.conn.execute("UPDATE p3_plan_revision SET status = 'obsolete', obsolete = 1 WHERE plan_revision_id = ?",
                                  (int(rid),))
                self._plan_event(rid, op, "obsolete", {"reason": reasons[0][1],
                                                       "reasons": [{"code": c, "words": w, "detail": d} for c, w, d in reasons]})
            return {"operation_id": op, "revisions": sorted(int(r) for r in findings)}
        return self._run(work)

    def reconcile_plans(self, ev, proj, routing):
        """Part of the revalidation detector (routing.reconcile): each locked,
        standing revision against its OWN dependencies (plans.obsolescence);
        those whose dependencies moved become obsolete. Returns the ids."""
        from . import plans as PL
        findings = {}
        for (rid,) in self.conn.execute("SELECT plan_revision_id FROM p3_plan_revision WHERE locked = 1 AND obsolete = 0 "
                                        "ORDER BY plan_revision_id").fetchall():
            _rev, items = self.plan_revision(rid)
            reasons = PL.obsolescence(items, ev, proj, routing)
            if reasons:
                findings[int(rid)] = reasons
        return self.mark_plans_obsolete(findings, note="routing detector: plan dependencies changed")["revisions"]


def export_decision_journal(conn, path):
    """Every operation with its rows, oldest first, as UTF-8 text. The
    database is the record; this is the copy a person can read or keep."""
    lines = [f"The File Organizer {VERSION} -- decision journal", "=" * 60, ""]
    ops = conn.execute("SELECT * FROM p3_operation ORDER BY operation_id").fetchall()
    for o in ops:
        lines.append(f"#{o['operation_id']}  {o['occurred_utc']}  {o['kind']}  by {o['actor_id']} via {o['agent_kind']} {o['agent_version']}")
        if o["note"]:
            lines.append(f"    note: {o['note']}")
        for q in conn.execute("SELECT * FROM p3_metadata_question WHERE operation_id=? ORDER BY question_id", (o["operation_id"],)):
            lines.append(f"    metadata question #{q['question_id']}: {q['semantic_field']} of file location {q['file_path_id']}")
        for d in conn.execute("SELECT * FROM p3_decision WHERE operation_id=? ORDER BY decision_id", (o["operation_id"],)):
            sup = f"  supersedes #{d['supersedes_decision_id']}" if d["supersedes_decision_id"] else ""
            origin = f"  (batch #{d['origin_ref']})" if d["origin_kind"] == ORIGIN_BULK_EXPLICIT_HUMAN else ""
            lines.append(f"    decision #{d['decision_id']}: {d['decision_kind']} on {d['target_kind']} {d['target_ref']} = {d['value_json']}{sup}{origin}")
        for w in conn.execute("SELECT * FROM p3_decision_withdrawal WHERE operation_id=? ORDER BY withdrawal_id", (o["operation_id"],)):
            lines.append(f"    withdrew decision #{w['decision_id']}" + (f": {w['reason']}" if w["reason"] else ""))
        for v in conn.execute(
                "SELECT v.*, p.kind FROM p3_policy_version v JOIN p3_policy p ON p.policy_id=v.policy_id "
                "WHERE v.operation_id=? ORDER BY v.policy_version_id", (o["operation_id"],)):
            lines.append(f"    policy #{v['policy_id']} v{v['version_no']} ({v['status']}): {v['kind']} {v['scope_json']} {v['effect_json']}")
        for e in conn.execute("SELECT * FROM p3_review_event WHERE operation_id=? ORDER BY review_event_id", (o["operation_id"],)):
            ref = f"  ends #{e['refers_to_review_event_id']}" if e["refers_to_review_event_id"] else ""
            trigger = f" [{e['return_kind']}: {e['return_condition']}]" if e["return_kind"] or e["return_condition"] else ""
            lines.append(f"    review event #{e['review_event_id']}: {e['event_kind']} on {e['target_kind']} {e['target_ref']}{trigger}{ref}")
        for b in conn.execute("SELECT * FROM p3_bulk_batch WHERE operation_id=? ORDER BY batch_id", (o["operation_id"],)):
            members = conn.execute("SELECT disposition, COUNT(*) FROM p3_bulk_member WHERE batch_id=? GROUP BY disposition ORDER BY disposition",
                                   (b["batch_id"],)).fetchall()
            summary = ", ".join(f"{n} {disp}" for disp, n in members)
            lines.append(f"    bulk batch #{b['batch_id']}: {b['action']} over {b['scope_kind']}"
                         + (f" {b['query_json']}" if b["query_json"] else "") + f" -- {summary}; mode {b['mode']}")
        lines.append("")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))
    return len(ops)
