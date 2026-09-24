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
                       LOCATION, GROUP, CONFIRM_EXPLICIT_OVERRIDE,
                       EVENT_DEFERRED, EVENT_SKIPPED, EVENT_RESTORED, RETURN_TIME, RETURN_EVIDENCE_CHANGE,
                       RETURN_MANUAL, DEFER_DISPOSITIONS, ORIGIN_EXPLICIT_HUMAN, ORIGIN_BULK_EXPLICIT_HUMAN,
                       DISP_DECIDED, MODE_PRESERVE)
from .resolve import _policy_covers

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

    def active(self):
        return active_decisions(ev_mod.load_decisions(self.conn))

    def active_for(self, target_kind, target_ref):
        ref = str(target_ref)
        return [d for d in self.active() if d.target_kind == target_kind and d.target_ref == ref]

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
            " WHERE d.target_kind = ? AND d.target_ref = ? "
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
            " WHERE d.target_kind = ? AND d.target_ref = ? ORDER BY d.decision_id",
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
            "       (SELECT COUNT(*) FROM p3_review_event), (SELECT COUNT(*) FROM p3_bulk_batch)").fetchone()
        return {"operations": row[0], "decisions": row[1], "withdrawals": row[2], "policy_versions": row[3],
                "review_events": row[4], "batches": row[5]}

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
        if target_kind not in (LOCATION, GROUP):
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
        rk = DEFER_DISPOSITIONS[disposition][0]
        if target_kind == GROUP and not ev_mod.group_members(self.conn, target_ref):
            raise ValueError("this content is not a current exact-duplicate group")

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
        if kind == "canonical_location":
            member_ids = {str(m["file_path_id"]) for m in evidence_binding.get("members", [])}
            if value not in member_ids:
                raise ValueError("the canonical must be a current member of the group")
        return kind, target_kind, target_ref, value, evidence_binding

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

    def batches(self):
        """Every batch, newest first, with what it recorded and what still stands."""
        rows = self.conn.execute(
            "SELECT b.*, o.occurred_utc, o.actor_id, "
            "       (SELECT COUNT(*) FROM p3_bulk_member m WHERE m.batch_id = b.batch_id) AS members "
            "  FROM p3_bulk_batch b JOIN p3_operation o ON o.operation_id = b.operation_id "
            " WHERE b.project_id = 1 ORDER BY b.batch_id DESC").fetchall()
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
        for b in self.batches():
            if int(b["batch_id"]) == int(batch_id):
                return b
        return None

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

def export_decision_journal(conn, path):
    """Every operation with its rows, oldest first, as UTF-8 text. The
    database is the record; this is the copy a person can read or keep."""
    lines = [f"The File Organizer {VERSION} -- decision journal", "=" * 60, ""]
    ops = conn.execute("SELECT * FROM p3_operation ORDER BY operation_id").fetchall()
    for o in ops:
        lines.append(f"#{o['operation_id']}  {o['occurred_utc']}  {o['kind']}  by {o['actor_id']} via {o['agent_kind']} {o['agent_version']}")
        if o["note"]:
            lines.append(f"    note: {o['note']}")
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
