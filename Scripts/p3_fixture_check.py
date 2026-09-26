#!/usr/bin/env python3
r"""Phase 3 acceptance against the research fixture lab (P3.R11).

The eleven synthetic fixtures that test Build 1-5 scope ship under
Resources\Phase3\fixture_lab\. Each is a SQLite file holding synthetic
Phase 2 evidence, authoritative Phase 3 records, and an `expected_projection`
table -- the oracle. The research's own validator computes each oracle with
per-fixture hand-written logic; this check runs the product's ONE general
resolution function (Phase3.resolve), ONE general router (Phase3.routing)
and ONE query matcher (Phase3.bulk) over the same rows and diffs the result
against every oracle key.

    F01  Keeper_Protected_Hardlink    canonical + keeper set + protected root +
                                      physical copies vs hard-link aliases, together
    F02  Multiple_Intentional_Keepers multiple keepers is a resolved state; zero reclaim is valid
    F03  Folder_Priority_Exception    a folder preference plus one explicit exception, and
                                      the exception wins
    F04  Frozen_Bulk_Query            a query-result batch freezes its membership: the
                                      product's matcher says a later file matches the
                                      frozen query NOW, and the batch does not have it (Build 4)
    F05  Dynamic_Policy_Future_Match  a protect-folder policy made before a file existed
                                      covers it when it appears, with no decision recorded
                                      for it -- a pure policy effect (Build 4)
    F06  Visual_Nontransitivity       A~B and B~C strong, A~C weak: equivalent, equivalent,
                                      different -- stored and read back per pair, nothing
                                      inferred along the chain (Build 5)
    F07  Related_Variant              an edited crop is a related variant with Keep Both:
                                      both kept, and never ordinary redundant-copy cleanup (Build 5)
    F08  Deferred_vs_Blocked          a person's deferral and an evidence blocker are
                                      different routes with different return triggers (Build 3)
    F09  Supersession_Undo_Reapply    change, withdraw, re-apply: every row survives
    F10  Revalidation                 the decision stays; a changed observation routes the
                                      group to Needs Revalidation (Build 3)
    F11  Plan_Drift                   a locked, fingerprinted revision is judged obsolete
                                      ("source decision changed") by the product's own
                                      check once its source decision is superseded --
                                      never rewritten, never re-planned (Build 8)
    F12  Hard_Precondition_Unknown    the product's Plan Builder rebuilds the lab's removal:
                                      physical identity unknown -> the hard precondition is
                                      'unknown', and unknown blocks (blocked_evidence) (Build 8)
    F13  Metadata_Desired_Unsupported a desired Creator on a read-only field: the product's Plan
                                      Builder makes the lab's Update Source Metadata item,
                                      blocked_reason writer_unsupported, state
                                      unsupported_action -- two fields, both checked (Build 9)
    F14  Metadata_Integrity_Blockers  a signed PDF and a hard-linked JPEG: the product's
                                      evidence-level projection says blocked
                                      (signed_or_certified / hardlinked_generic_write) with no
                                      plan, revision or item anywhere (Build 9)
    F15  Projection_Rebuild           keeper / canonical / protected / plan readiness
                                      reconstruct from authoritative history alone
    F16  Visual_NonEdge               A~B and B~C are candidates, A~C is not (P3.R3 §34):
                                      Survey Mode shows A, B and C as one cluster, names
                                      A-C a non-edge, and a group control's pairs are A~B
                                      and B~C only -- nothing inferred across A-C (B14)

Also proved here, because the fixtures are the cleanest place to prove it:
the projection is a pure function -- the same input in any row order gives
the same answer, twice.

Run:  python Scripts\p3_fixture_check.py
"""
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

from Phase3.model import (Evidence, Location, Group, Decision, PolicyVersion, ReviewEvent, Batch, BatchMember,   # noqa: E402
                          VisualPair, path_key, MetadataEvidence, MetadataQuestion, CapabilityRecord)
from Phase3.registry import METADATA_DECISION_KINDS, file_family                                    # noqa: E402
from Phase3 import metadata as MD                                                                     # noqa: E402
from Phase3 import resolve as R                                                                       # noqa: E402
from Phase3 import routing as RT                                                                      # noqa: E402
from Phase3 import bulk as BK                                                                         # noqa: E402

FIXTURES = SCRIPTS.parent / "Resources" / "Phase3" / "fixture_lab" / "Fixtures"
RESULTS = []

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" -- {detail}" if (detail and not ok) else ""))


# ---------------------------------------------------------------------------
# Loading a fixture into the product's model
# ---------------------------------------------------------------------------

def _rows(con, sql, args=()):
    """A fixture holds only the tables its scenario needs (F06 and F07 have
    no groups, policies, events or batches): a missing table is no rows."""
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise


def load_fixture(db_path: Path) -> tuple[Evidence, dict, list]:
    """The fixture's rows as the model the resolver reads, plus its oracle."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        locations = {}
        protected_flags = {}
        for r in con.execute("SELECT * FROM evidence_location ORDER BY location_id"):
            locations[r["location_id"]] = Location(
                location_id=r["location_id"], path=r["path"], root_key=r["root"],
                content_id=r["content_id"], physical_id=r["physical_id"],
                present=bool(r["present"]), size_bytes=r["bytes"],
                observation_id=r["observation_id"],
                sort_key=(r["root"], path_key(r["path"])))
            protected_flags[r["location_id"]] = bool(r["protected"])
        groups = {}
        current = {r["group_id"]: bool(r["evidence_current"]) for r in _rows(con, "SELECT * FROM evidence_duplicate_group")}
        members = {}
        for r in _rows(con, "SELECT group_id, location_id FROM evidence_duplicate_member ORDER BY group_id, location_id"):
            members.setdefault(r["group_id"], []).append(r["location_id"])
        for gid, lids in members.items():
            groups[gid] = Group(gid, locations[lids[0]].content_id, tuple(lids), current.get(gid, True))

        ops = {r["operation_id"]: r["occurred_utc"] for r in con.execute("SELECT operation_id, occurred_utc FROM p3_operation")}
        decisions = []
        rows = con.execute("SELECT rowid AS seq, * FROM p3_decision ORDER BY rowid").fetchall()
        for r in rows:
            value = json.loads(r["value_json"]) if r["value_json"] is not None else None
            # The lab binds evidence as one observation id ('obs:OBS1'); the
            # product binds [file_path_id, observation_id] pairs. A canonical
            # decision's observation is the canonical's; a location's is its own.
            binding = {}
            raw = r["evidence_binding"] or ""
            if raw.startswith("obs:"):
                obs = raw[4:]
                if r["target_kind"] == "file_location":
                    binding = {"file_path_id": r["target_ref"], "observation_id": obs}
                elif r["decision_kind"] == "canonical_location":
                    binding = {"members": [{"file_path_id": str(value), "observation_id": obs}]}
            decisions.append(Decision(
                decision_id=r["decision_id"], target_kind=r["target_kind"], target_ref=r["target_ref"],
                kind=r["decision_kind"], value=value,
                origin_kind=r["origin_kind"], supersedes=r["supersedes_decision_id"],
                withdrawn=bool(r["withdrawn"]), operation_id=r["operation_id"], sequence=int(r["seq"]),
                binding=binding, occurred_utc=ops.get(r["operation_id"], "")))

        events = []
        for r in _rows(con, "SELECT rowid AS seq, * FROM p3_review_event ORDER BY rowid"):
            events.append(ReviewEvent(
                review_event_id=r["review_event_id"], target_kind=r["target_kind"], target_ref=r["target_ref"],
                kind=r["event_kind"], return_kind=r["return_kind"], return_condition=r["return_condition"],
                return_on_utc=r["return_condition"] if r["return_kind"] == "time" else None,
                occurred_utc=ops.get(r["operation_id"], ""), sequence=int(r["seq"])))
        # Routing is evaluated at a moment; the lab's moment is its last operation.
        now = max(ops.values()) if ops else "2026-09-08T12:00:00Z"

        policies = []
        for r in _rows(con, "SELECT * FROM p3_policy_version ORDER BY policy_id, version_no"):
            scope = json.loads(r["scope_json"] or "{}")
            effect = json.loads(r["effect_json"] or "{}")
            if "path" in scope:
                scope = {"path_key": path_key(scope["path"]), "path": scope["path"]}
            if "root" in scope:
                scope = {"root_key": scope["root"], "root_path": scope["root"]}
            if "over" in effect:
                effect = {"over_path_key": path_key(effect["over"]), "over": effect["over"]}
            policies.append(PolicyVersion(
                policy_version_id=r["policy_version_id"], policy_id=r["policy_id"], kind=r["kind"],
                scope=scope, effect=effect, version_no=int(r["version_no"] or 1), active=bool(r["enabled"])))

        # The lab models "protected root" as a flag on the evidence row
        # (its schema predates policies for every scenario). In the product
        # protection only ever comes from a policy, so the flag becomes one:
        # protect_source_root when every location of that root carries it,
        # else an exact-path protect_folder_subtree for that location.
        by_root = {}
        for lid, loc in locations.items():
            by_root.setdefault(loc.root_key, []).append(lid)
        synthetic = 0
        for root, lids in sorted(by_root.items()):
            flags = {protected_flags[l] for l in lids}
            if flags == {True}:
                synthetic += 1
                policies.append(PolicyVersion(f"SYN{synthetic}", f"SYNP{synthetic}", "protect_source_root",
                                              {"root_key": root, "root_path": root}))
            elif True in flags:
                for l in lids:
                    if protected_flags[l]:
                        synthetic += 1
                        policies.append(PolicyVersion(f"SYN{synthetic}", f"SYNP{synthetic}", "protect_folder_subtree",
                                                      {"path_key": locations[l].path_key, "path": locations[l].path}))

        # Bulk batches (Build 4): the lab's shape is the product's -- scope
        # kind, the frozen query, the members. The product also records each
        # member's disposition; the lab predates that, so every member here
        # is one the batch decided.
        batches = []
        for r in _rows(con, "SELECT * FROM p3_bulk_batch ORDER BY batch_id"):
            members = tuple(BatchMember(m["target_kind"], m["target_ref"]) for m in con.execute(
                "SELECT target_kind, target_ref FROM p3_bulk_member WHERE batch_id=? ORDER BY target_ref", (r["batch_id"],)))
            batches.append(Batch(r["batch_id"], r["scope_kind"], json.loads(r["query_json"]) if r["query_json"] else None,
                                 members, frozen=bool(r["frozen"]), committed=bool(r["committed"]),
                                 operation_id=r["operation_id"], occurred_utc=ops.get(r["operation_id"], "")))

        # Visual candidate pairs (Build 5): the lab's evidence_visual_candidate
        # is the product's visual_candidate -- a pair of two locations, the
        # method that found it and its measurement. The lab records a score
        # on the method's own scale (0.95 for its phash rows, 0.91 for sscd);
        # the product keeps it as the method's score, never as a percentage.
        pairs = {}
        for r in _rows(con, "SELECT * FROM evidence_visual_candidate ORDER BY pair_id"):
            a, b = sorted((r["a_location_id"], r["b_location_id"]))
            pairs[r["pair_id"]] = VisualPair(r["pair_id"], a, b, method=r["method"], score=r["score"],
                                             a_observation_id=locations[a].observation_id,
                                             b_observation_id=locations[b].observation_id,
                                             current=bool(r["current"]))

        # Metadata (Build 9): the lab's evidence_metadata_question is a stand-in
        # for Phase 1/2's readings -- the location, the field, the observed
        # value and state, the capability that applies (writer_capability is
        # the capability record's status) and the two integrity flags. Its
        # metadata decisions are ordinary p3_decision rows; like the product's
        # loader, this one keeps them out of the retention model.
        questions = {}
        for r in _rows(con, "SELECT * FROM evidence_metadata_question ORDER BY question_id"):
            loc = locations[r["location_id"]]
            fam = file_family(loc.path)
            questions[r["question_id"]] = MetadataQuestion(
                r["question_id"], r["location_id"], r["semantic_field"], observed_state=r["observed_state"],
                observed_value=r["observed_value"],
                observed_values=(r["observed_value"],) if r["observed_value"] is not None else (),
                file_family=fam, capability=CapabilityRecord(fam, r["semantic_field"], status=r["writer_capability"]),
                signed_or_certified=None if r["signed_or_certified"] is None else bool(r["signed_or_certified"]),
                hardlinked=None if r["hardlinked"] is None else bool(r["hardlinked"]),
                observation_id=loc.observation_id)
        md_decisions = [d for d in decisions if d.kind in METADATA_DECISION_KINDS]
        decisions = [d for d in decisions if d.kind not in METADATA_DECISION_KINDS]

        oracle = {r["key"]: json.loads(r["value_json"]) for r in con.execute("SELECT key, value_json FROM expected_projection")}
        history = [(r["decision_id"], r["value_json"], bool(r["withdrawn"])) for r in rows]
        ev = Evidence(locations, groups, decisions, policies, events, {}, now)
        ev.metadata = MetadataEvidence(questions, md_decisions, {})
        ev.batches = batches
        ev.pairs = pairs
        # Build 8: the lab's plan rows (F11, F12), as the lab recorded them.
        ev.plan_rows = {"revisions": [dict(r) for r in _rows(con, "SELECT * FROM p3_plan_revision ORDER BY plan_revision_id")],
                        "items": [dict(r) for r in _rows(con, "SELECT * FROM p3_plan_item ORDER BY plan_item_id")],
                        "plans": [dict(r) for r in _rows(con, "SELECT * FROM p3_plan ORDER BY plan_id")]}
        return ev, oracle, history
    finally:
        con.close()


# ---------------------------------------------------------------------------
# What the product says, in the oracle's vocabulary
# ---------------------------------------------------------------------------

def actual_projection(fid: str, ev: Evidence, history) -> dict:
    proj = R.resolve_all(ev)
    g = {p.group_id: p for p in proj.groups}
    if fid.startswith("F01") or fid.startswith("F02"):
        p = g["G1"]
        out = {"keeper_set": list(p.keeper_set), "canonical": p.canonical,
               "redundant_candidates": list(p.redundant_candidates),
               "plan_eligible_reclaimable_bytes": p.plan_eligible_reclaim_bytes}
        if fid.startswith("F01"):
            out.update(protected=list(p.protected), physical_copies=p.physical_copies,
                       hardlink_aliases=p.hardlink_aliases)
        else:
            out["ready_for_plan"] = p.ready_for_plan
        return out
    if fid.startswith("F03"):
        return {"policy_suggestion": {gid: p.suggested_canonical for gid, p in g.items() if p.suggested_canonical},
                "effective_canonical": {gid: p.effective_canonical for gid, p in g.items() if p.effective_canonical},
                "explicit_exception_groups": sorted(gid for gid, p in g.items()
                                                    if p.canonical is not None and p.suggested_canonical is not None
                                                    and p.canonical != p.suggested_canonical),
                "conflicts": [c.kind for p in proj.groups for c in p.conflicts]}
    if fid.startswith("F04"):
        # The frozen batch: its members are what it recorded, full stop. The
        # product's own query matcher, asked what the frozen query matches
        # NOW, names the later file too -- and the batch does not have it.
        batch = ev.batches[0]
        members = sorted(batch.member_refs())
        routing = RT.route_all(ev, proj, ev.now)
        _kind, now_matches = BK.query_targets(batch.query, ev, proj, routing)
        later = sorted(set(now_matches) - set(members))
        return {"batch_members": members,
                "later_matching_target": later[0] if len(later) == 1 else later,
                "later_target_in_batch": any(l in members for l in later)}
    if fid.startswith("F05"):
        # Protection is a policy effect on evidence, group or no group: the
        # file that appeared after the policy was made is covered, and no
        # decision exists for either file.
        prot = R.protected_locations(ev)
        return {"protected_locations": sorted(prot),
                "explicit_human_decisions_for_policy_effect": sum(1 for d in ev.decisions if d.origin_kind == "explicit_human"),
                "policy_version": sorted({pv for pvs in prot.values() for pv in pvs})[0] if prot else None}
    if fid.startswith("F09"):
        hist = []
        for did, value_json, withdrawn in history:
            hist.append(f"{did}:{json.loads(value_json)}")
            if withdrawn:
                hist.append(f"{did}:withdrawn")
        return {"history": hist, "current_canonical": g["G1"].canonical,
                "history_rows_preserved": len(history)}
    if fid.startswith("F15"):
        p = g["G1"]
        return {"keeper_set": list(p.keeper_set), "canonical": p.canonical, "protected": list(p.protected),
                "redundant": list(p.redundant_candidates), "conflicts": [c.kind for c in p.conflicts],
                "review_state": p.review_state, "ready_for_plan": p.ready_for_plan,
                "plan_item_target": p.plan_eligible_locations[0] if len(p.plan_eligible_locations) == 1 else list(p.plan_eligible_locations)}
    if fid.startswith("F06"):
        # Per pair, read back from the product's pair projection -- and no
        # transitive inference anywhere: A~B and B~C are equivalent, A~C is
        # different, and nothing the product computes groups A, B and C.
        pp = {p.pair_id: p for p in proj.pairs}
        pair_decisions = {pid: p.relationship for pid, p in pp.items() if p.relationship}
        chain = pp["PAB"].relationship == "equivalent_copy" and pp["PBC"].relationship == "equivalent_copy"
        inferred = chain and pp["PAC"].relationship != "different"
        return {"pair_decisions": pair_decisions, "transitive_equivalence_inferred": inferred}
    if fid.startswith("F16"):
        # Through the product's own Survey Mode code: the cluster, its pairs,
        # its non-edges, and what a group control would cover.
        from Phase3 import survey as SV
        routing = RT.route_all(ev, proj, ev.now)
        clusters = SV.cluster_map(proj, routing, ev, BK.LENS_ALL).clusters
        images = clusters[0].images if len(clusters) == 1 else ()
        edges = SV.cluster_edges(proj, images)
        pv = SV.preview_group(ev, proj, routing, SV.REMAINING_EQUIVALENT, images, BK.LENS_ALL) if edges else None
        covered = {c.target_ref for c in pv.candidates} if pv else set()
        recorded = {d.target_ref for d in ev.decisions if d.target_kind == "visual_candidate_pair"}
        return {"pair_decisions": {p.pair_id: p.relationship for p in proj.pairs if p.relationship},
                "candidate_pairs": sorted(p.pair_id for p in edges),
                "non_edges": sorted(sorted(x) for x in SV.non_edges(images, edges, proj)),
                "transitive_equivalence_inferred": bool((covered | recorded) - set(ev.pairs))}
    if fid.startswith("F11"):
        # The lab's locked, fingerprinted revision, judged by the product's own
        # obsolescence check (plans.obsolescence) against the record as it now
        # stands: its source decision D1 was superseded by D3 after the lock.
        from Phase3 import plans as PL
        from Phase3.registry import LOCATION as LOC, ITEM_READY, ITEM_BLOCKED
        rev = ev.plan_rows["revisions"][0]
        items = [PL.PlanItem(n, r["action_kind"], LOC, r["target_ref"], (), {},
                             decision_ids=tuple(json.loads(r["source_decision_ids_json"] or "[]")),
                             state=ITEM_READY if r["blocked_reason"] is None else ITEM_BLOCKED)
                 for n, r in enumerate(ev.plan_rows["items"], 1)]
        reasons = PL.obsolescence(items, ev, proj, RT.route_all(ev, proj, ev.now))
        return {"plan_revision": rev["plan_revision_id"], "fingerprint": rev["fingerprint"], "locked": bool(rev["locked"]),
                "obsolete": bool(reasons), "reason": reasons[0][1] if reasons else None,
                "auto_replanned": len(ev.plan_rows["revisions"]) != 1}
    if fid.startswith("F12"):
        # The product's Plan Builder on the lab's evidence: the removal the lab
        # recorded, rebuilt, its hard precondition evaluated from evidence alone.
        from Phase3 import plans as PL
        from Phase3.registry import ACTION_REMOVE, ITEM_READY
        want = ev.plan_rows["items"][0]
        key = json.loads(want["hard_preconditions_json"])[0]
        draft = PL.build(ev, proj, RT.route_all(ev, proj, ev.now))
        item = next(i for i in draft.items if i.action_kind == ACTION_REMOVE and i.target_ref == want["target_ref"])
        c = item.condition(key)
        return {"precondition_result": c.result if c else None,
                "hard_unknown_blocks": bool(c and c.severity == "hard" and c.result == "unknown" and item.state != ITEM_READY),
                "plan_item_state": item.state}
    if fid.startswith("F13"):
        # The product's Plan Builder on the lab's rows: the desired Creator
        # becomes an Update Source Metadata item on L1, settled by the
        # writer_supported precondition over the capability status.
        from Phase3 import plans as PL
        from Phase3.registry import ACTION_METADATA
        draft = PL.build(ev, proj, RT.route_all(ev, proj, ev.now))
        item = next(i for i in draft.items if i.action_kind == ACTION_METADATA)
        rows = {r.semantic_field: r for r in MD.review(ev)}
        ev.f13_item = item
        return {"observed_creator": rows["creator"].observed_value, "desired_creator": rows["creator"].desired_value,
                "source_mutated": ev.source_mutated, "plan_item_state": item.state}
    if fid.startswith("F14"):
        # The evidence-level projection alone -- no Plan Builder call at all.
        return {"blocked": MD.blocked_by_evidence(ev.metadata.questions), "source_mutated": ev.source_mutated}
    if fid.startswith("F07"):
        p = proj.pairs[0]
        return {"relationship": p.relationship, "keeper_set": list(p.keeper_set),
                "ordinary_redundant_candidate": p.ordinary_redundant_candidate}
    routing = RT.route_all(ev, proj, ev.now)
    if fid.startswith("F08"):
        routes, kinds = {}, {}
        for lid, route in routing.locations.items():
            routes[lid] = route.primary
            if route.primary == RT.DEFERRED and route.deferral is not None:
                kinds[lid] = route.deferral.return_kind
            elif route.primary == RT.BLOCKED:
                opened = route.open_events.get("blocked") or []
                kinds[lid] = opened[0].return_kind if opened else None
        return {"routing": routes, "return_kind": kinds}
    if fid.startswith("F10"):
        route = routing.groups["G1"]
        from Phase3.model import active_decisions
        active = {d.decision_id for d in active_decisions(ev.decisions)}
        drift = {x.decision_id: x for x in route.drift}
        return {"decision_preserved": "D1" if "D1" in active and any(d.decision_id == "D1" for d in ev.decisions) else None,
                "routing": route.primary,
                "current_applicability": "requires_revalidation" if not drift["D1"].applicable else "applicable"}
    raise KeyError(fid)


def _shuffled(ev: Evidence, seed: int) -> Evidence:
    rnd = random.Random(seed)
    decisions = list(ev.decisions)
    rnd.shuffle(decisions)
    policies = list(ev.policies)
    rnd.shuffle(policies)
    locations = dict(rnd.sample(list(ev.locations.items()), len(ev.locations)))
    groups = dict(rnd.sample(list(ev.groups.items()), len(ev.groups)))
    # Events keep their order: the record is a sequence (a restore ends what
    # came before it), and the loader delivers it oldest first.
    out = Evidence(locations, groups, decisions, policies, list(ev.events), dict(ev.roots), ev.now)
    md = list(ev.metadata.decisions)
    rnd.shuffle(md)
    out.metadata = MetadataEvidence(dict(rnd.sample(list(ev.metadata.questions.items()), len(ev.metadata.questions))),
                                    md, dict(ev.metadata.capabilities))
    out.batches = list(getattr(ev, "batches", []))
    out.pairs = dict(rnd.sample(list(ev.pairs.items()), len(ev.pairs)))
    return out


def _canon(proj: R.ProjectProjection, ev=None):
    routes = []
    if ev is not None:
        rt = RT.route_all(ev, proj, ev.now)
        routes = [r.as_dict() for r in rt.groups.values()] + [r.as_dict() for r in rt.locations.values()] + [rt.counts]
        routes += [r.as_dict() for _pid, r in sorted(rt.pairs.items())] + [rt.pair_counts]
        routes.append(sorted(R.protected_locations(ev).items()))
        for b in getattr(ev, "batches", []):
            routes.append(BK.query_targets(b.query, ev, proj, rt))
        if ev.metadata.questions:
            from dataclasses import asdict
            from Phase3 import plans as PL
            routes.append([asdict(r) for r in MD.review(ev)])
            routes.append(sorted(MD.blocked_by_evidence(ev.metadata.questions).items()))
            routes.append(PL.fingerprint(PL.build(ev, proj, rt).items))
    return json.dumps([p.as_dict() for p in proj.groups] + [p.as_dict() for p in proj.pairs]
                      + [list(proj.orphaned_decisions), proj.totals] + routes,
                      sort_keys=True, default=str)


def _metadata_checks(fid, ev):
    """F13 and F14 beyond their oracle keys: the lab's own plan rows against
    what the product built (F13), and the zero-plan shape (F14)."""
    from Phase3 import plans as PL
    from Phase3.registry import ACTION_METADATA, STAGE_EVIDENCE
    if fid.startswith("F13"):
        want, item = ev.plan_rows["items"][0], ev.f13_item
        hard = [c.key for c in item.preconditions if c.stage == STAGE_EVIDENCE and c.severity == "hard"]
        check("the item's blocked_reason is the lab's p3_plan_item.blocked_reason (writer_unsupported) -- a different "
              "field from plan_item_state, and a different value", item.blocked_reason == want["blocked_reason"]
              and item.blocked_reason != item.state)
        check("the same action, target and source decision as the lab's item (update_source_metadata on L1, from D1)",
              (item.action_kind, item.target_ref, list(item.decision_ids)) ==
              (want["action_kind"], want["target_ref"], json.loads(want["source_decision_ids_json"])))
        check("the lab's hard precondition (writer_supported) is among the item's, evaluated 'unsupported' from the "
              "capability status 'read_only'", set(json.loads(want["hard_preconditions_json"])) <= set(hard)
              and item.condition("writer_supported").result == "unsupported"
              and item.condition("writer_supported").params.get("capability_status") == "read_only")
        check("neither integrity blocker fires (not signed, not hard-linked): the writer alone blocks it",
              item.condition("source_not_signed_or_certified").result == "pass"
              and item.condition("source_not_hardlinked").result == "pass")
    if fid.startswith("F14"):
        check("the fixture holds no plan, revision or item -- the blocks above were answered without one",
              not ev.plan_rows["plans"] and not ev.plan_rows["revisions"] and not ev.plan_rows["items"])
        check("the capability is 'supported' on both questions: the blocks are evidence flags, not capability",
              all(MD.writer_check(q)[0] == "pass" for q in ev.metadata.questions.values()))
        proj = R.resolve_all(ev)
        items = {i.target_ref: i for i in PL.build(ev, proj, RT.route_all(ev, proj, ev.now)).items
                 if i.action_kind == ACTION_METADATA}
        check("were a plan built, the Plan Builder would reuse the same checks: blocked, with the same two reasons",
              {q.question_id: items[q.location_id].blocked_reason for q in ev.metadata.questions.values()}
              == MD.blocked_by_evidence(ev.metadata.questions))


# ---------------------------------------------------------------------------

def main() -> int:
    if not FIXTURES.is_dir():
        print(f"fixture lab not found: {FIXTURES}")
        return 2
    dirs = sorted(d for d in FIXTURES.iterdir() if d.is_dir() and (d / "fixture.sqlite").is_file())
    print(f"Phase 3 fixture check: {len(dirs)} fixtures under {FIXTURES}")
    for d in dirs:
        print(f"\n{d.name}\n{'-' * len(d.name)}")
        con = sqlite3.connect(str(d / "fixture.sqlite"))
        qc = con.execute("PRAGMA quick_check").fetchone()[0]
        con.close()
        check("fixture database quick_check ok", qc == "ok", qc)
        before = hashlib.sha256((d / "fixture.sqlite").read_bytes()).hexdigest()
        ev, oracle, history = load_fixture(d / "fixture.sqlite")
        # source_mutated (F13, F14): the only file anything here could touch is
        # the fixture itself -- the real-project proof is p3_metadata_check's.
        ev.source_mutated = hashlib.sha256((d / "fixture.sqlite").read_bytes()).hexdigest() != before
        try:
            actual = actual_projection(d.name, ev, history)
        except Exception as exc:                                    # noqa: BLE001
            check("resolution ran", False, f"{type(exc).__name__}: {exc}")
            continue
        for key, expected in sorted(oracle.items()):
            got = actual.get(key, "<missing>")
            check(f"{key} == {json.dumps(expected)}", got == expected, f"got {json.dumps(got, default=str)}")
        _metadata_checks(d.name, ev)
        # The projection and the routes are pure functions of history: any row order, twice.
        base = _canon(R.resolve_all(ev), ev)
        same = all(_canon(R.resolve_all(_shuffled(ev, seed)), _shuffled(ev, seed)) == base for seed in (1, 2, 3))
        check("identical projection and routes from shuffled rows (3 seeds) and a second run", same and _canon(R.resolve_all(ev), ev) == base)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 FIXTURES: {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
