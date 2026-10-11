r"""B19 -- invariant I15's missing half: identifier relabelling.

    py -3 Scripts\p3_relabel_check.py

The R1-R2 evaluation's I15: "Stable display ordering must remain semantically
inert even when rows are inserted or identifiers change." B8 tested row order
(shuffled evidence). This suite tests the other half: every identifier is
replaced, and nothing a person could see or act on may change.

  1. A hand-built model holding every kind of thing Phase 3 reasons about:
     exact-duplicate groups (a hard link, a protected root, a protected folder,
     prefer / avoid / tie policies, a retired policy, the last-copy floor), a
     chain of visual pairs (equivalent, related, different with a retention,
     the chain guard), decisions with supersessions, a withdrawal and an
     override, review events (deferrals, a skip, a restore, a fired snooze), a
     bulk batch, metadata questions with desired set / clear / leave unchanged
     and project metadata -- and a plan built from all of it.
  2. The same on a real project: real files through Phase 1/2 (scan,
     fingerprints, image and document analysis), every decision recorded
     through the store, loaded back by the product's own loader.

For each, and for 3 seeds, a random bijection is applied to EVERY identifier
space -- file location, content (group), visual pair, decision, policy, policy
version, review event, batch, metadata question, operation, observation,
physical identity and analyzer result -- and every collection the loader
returns in id order is put in the new ids' order, exactly as a database
holding the new ids would load it. Recording order is time and sequence: the
`sequence` of a decision or event is kept, never recomputed from an id.

Then everything is recomputed -- keepers, canonical, protection, redundant
candidates, conflicts, routes, queue membership and queue order, every Show
filter and lens, bulk previews, Survey Mode's clusters, plan items (excluding
their numbering) and metadata review states -- mapped back through the
inverse bijection, and compared for equality, list order included: a list's
order must be a function of what it shows, never of the ids.

A fingerprint legitimately contains ids. For the plan payload the assertion
is instead: the relabelled payload equals the original payload with the
bijection applied (an id list the payload keeps in id order -- a set --
re-sorted under the new ids, as the builder itself would).
"""
from __future__ import annotations

import json
import random
import re
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                                   # noqa: BLE001
    pass

from Phase3 import registry as REG                                  # noqa: E402
from Phase3 import resolve as R                                     # noqa: E402
from Phase3 import routing as RT                                    # noqa: E402
from Phase3 import plans as PL                                      # noqa: E402
from Phase3 import metadata as MD                                   # noqa: E402
from Phase3 import bulk as BK                                       # noqa: E402
from Phase3 import survey as SV                                     # noqa: E402
from Phase3 import pairs as PR                                      # noqa: E402
from Phase3.model import (Evidence, Group, Decision, PolicyVersion, ReviewEvent, RootCoverage, Batch,  # noqa: E402
                          BatchMember, VisualPair, MetadataEvidence, path_key)

RESULTS = []
SEEDS = (1, 2, 3)
NOW = "2026-09-27T12:00:00Z"


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


SPACES = ("location", "group", "pair", "decision", "policy", "policy_version", "event", "batch", "question",
          "operation", "observation", "physical", "analyzer_result")
TARGET_SPACE = {REG.LOCATION: "location", REG.GROUP: "group", REG.VISUAL_PAIR: "pair", REG.METADATA_QUESTION: "question"}
#: Binding / detail / query keys that hold an id, and the space it is in.
ID_KEYS = {"file_path_id": "location", "observation_id": "observation", "content_observation_id": "observation",
           "content_id": "group", "question_id": "question", "pair_id": "pair", "policy_id": "policy",
           "policy_version_id": "policy_version", "decision_id": "decision", "batch_id": "batch",
           "review_event_id": "event", "prefer": "location"}
ID_LIST_KEYS = {"analyzer_result_ids": "analyzer_result", "compared_observations": "observation", "group_ids": "group",
                "question_ids": "question", "keepers": "location", "excluded": "location", "selected": "location",
                "images": "location"}


def _id_order(x):
    s = str(x)
    return (0, int(s), "") if s.isdigit() else (1, 0, s)


class Bijection:
    """A random one-to-one relabelling of every id space. A digit id stays
    digits (the product's own ids are integers), any other keeps its letters
    as a prefix; an int stays an int."""

    def __init__(self, seed):
        self.seed = seed
        self.rng = random.Random(seed)
        self.fwd = {s: {} for s in SPACES}
        self.inv = {s: {} for s in SPACES}

    def _fresh(self, space, key):
        while True:
            n = self.rng.randint(1, 10 ** 7)
            if key.isdigit():
                new = str(n)
            else:
                m = re.match(r"^([A-Za-z_\-]*)", key)
                new = (m.group(1) or space[:2]) + str(n)
            if new not in self.inv[space] and new != key:
                return new

    def m(self, space, x):
        if x is None:
            return None
        key = str(x)
        if key not in self.fwd[space]:
            new = self._fresh(space, key)
            self.fwd[space][key] = new
            self.inv[space][new] = key
        new = self.fwd[space][key]
        return int(new) if isinstance(x, int) and not isinstance(x, bool) and new.isdigit() else new

    def back(self, space, x):
        if x is None:
            return None
        return self.inv[space].get(str(x), f"<unmapped {space}:{x}>")


def identity(space, x):
    return None if x is None else str(x)


def rb(obj, B: Bijection):
    """Relabel the ids inside a binding, an event's detail, a batch query or parameters."""
    if isinstance(obj, dict):
        tk = obj.get("target_kind")
        out = {}
        for k, v in obj.items():
            if k in ID_KEYS and not isinstance(v, (dict, list)):
                out[k] = B.m(ID_KEYS[k], v)
            elif k in ID_LIST_KEYS and isinstance(v, list):
                out[k] = [B.m(ID_LIST_KEYS[k], x) for x in v]
            elif k == "target_ref" and tk in TARGET_SPACE:
                out[k] = B.m(TARGET_SPACE[tk], v)
            elif k == "edges" and isinstance(v, list):
                out[k] = [B.m("pair", x) for x in v]
            else:
                out[k] = rb(v, B)
        return out
    if isinstance(obj, list):
        return [rb(x, B) for x in obj]
    return obj


def _value(kind, value, B):
    if kind == "canonical_location":
        return B.m("location", value)
    if kind == "keep_both":
        return REG.DECISION_KINDS["keep_both"].validate_value([B.m("location", x) for x in value])
    if kind == "override_protection":
        return {"policy_id": B.m("policy", value["policy_id"]), "policy_version_id": B.m("policy_version", value["policy_version_id"])}
    return value


def _decision(d: Decision, B):
    return replace(d, decision_id=B.m("decision", d.decision_id), target_ref=B.m(TARGET_SPACE[d.target_kind], d.target_ref),
                   value=_value(d.kind, d.value, B), supersedes=B.m("decision", d.supersedes),
                   operation_id=B.m("operation", d.operation_id), binding=rb(d.binding, B) if d.binding else d.binding)


def _event(e: ReviewEvent, B):
    return replace(e, review_event_id=B.m("event", e.review_event_id),
                   target_ref=B.m(TARGET_SPACE.get(e.target_kind, "location"), e.target_ref),
                   detail=rb(e.detail, B), refers_to=B.m("event", e.refers_to))


def _in_new_order(items, key):
    return dict(sorted(items, key=lambda kv: _id_order(key(kv))))


def relabel(ev: Evidence, B: Bijection) -> Evidence:
    """The same evidence and intent under new ids, loaded as a database holding
    those ids would load it: collections the loader returns in id order are in
    the NEW ids' order; decisions and events keep their recording order."""
    locs = []
    for l in ev.locations.values():
        sk = l.sort_key
        if len(sk) == 3 and isinstance(sk[2], int):
            sk = (sk[0], sk[1], B.m("location", sk[2]))
        new = replace(l, location_id=B.m("location", l.location_id), content_id=B.m("group", l.content_id),
                      physical_id=B.m("physical", l.physical_id), observation_id=B.m("observation", l.observation_id),
                      sort_key=sk)
        locs.append((new.location_id, new))
    groups = [(B.m("group", g.group_id), Group(B.m("group", g.group_id), B.m("group", g.content_id),
                                             tuple(B.m("location", x) for x in g.member_ids), g.evidence_current))
              for g in ev.groups.values()]
    pairs = []
    for p in ev.pairs.values():
        a, b, ao, bo = B.m("location", p.a_id), B.m("location", p.b_id), B.m("observation", p.a_observation_id), \
            B.m("observation", p.b_observation_id)
        if _id_order(a) > _id_order(b):                     # the database keeps a_file_path_id < b_file_path_id
            a, b, ao, bo = b, a, bo, ao
        pairs.append((B.m("pair", p.pair_id), replace(p, pair_id=B.m("pair", p.pair_id), a_id=a, b_id=b,
                                                      a_observation_id=ao, b_observation_id=bo)))
    policies = sorted((replace(p, policy_version_id=B.m("policy_version", p.policy_version_id),
                               policy_id=B.m("policy", p.policy_id)) for p in ev.policies),
                      key=lambda p: _id_order(p.policy_id))
    batches = [replace(b, batch_id=B.m("batch", b.batch_id), query=rb(b.query, B) if b.query else b.query,
                       members=tuple(replace(m, target_ref=B.m(TARGET_SPACE[m.target_kind], m.target_ref)) for m in b.members),
                       parameters=rb(b.parameters, B), operation_id=B.m("operation", b.operation_id)) for b in ev.batches]
    md = ev.metadata
    questions = []
    for q in md.questions.values():
        questions.append((B.m("question", q.question_id), replace(
            q, question_id=B.m("question", q.question_id), location_id=B.m("location", q.location_id),
            observation_id=B.m("observation", q.observation_id), content_id=B.m("group", q.content_id),
            analyzer_result_ids=tuple(sorted((B.m("analyzer_result", x) for x in q.analyzer_result_ids), key=_id_order)))))
    metadata = MetadataEvidence(_in_new_order(questions, lambda kv: kv[0]), [_decision(d, B) for d in md.decisions],
                                dict(md.capabilities), [_event(e, B) for e in md.events])
    out = Evidence(_in_new_order(locs, lambda kv: kv[0]), _in_new_order(groups, lambda kv: kv[0]),
                   [_decision(d, B) for d in ev.decisions], policies, [_event(e, B) for e in ev.events],
                   dict(ev.roots), ev.now, batches, _in_new_order(pairs, lambda kv: kv[0]), metadata)
    scopes = getattr(ev, "scopes", None)
    if scopes is not None:
        out.scopes = scopes.relabelled(lambda space, x: B.m(space, x))
    return out


# -- what a person could see or act on, with ids mapped back --------------------------------

def _set(back, space, xs):
    return sorted(str(back(space, x)) for x in (xs or ()))


def _conflict(c, back):
    return {"kind": c.kind, "severity": c.severity, "reason": c.reason,
            "locations": _set(back, "location", c.location_ids), "decisions": _set(back, "decision", c.decision_ids)}


def _route(r, back):
    if r is None:
        return None
    return {"primary": r.primary, "word": r.word,
            "conditions": [{"route": c.route, "kind": c.kind, "severity": c.severity,
                            "event": back("event", c.event_id), "decisions": _set(back, "decision", c.decision_ids)}
                           for c in r.conditions],
            "drift": sorted([back("decision", x.decision_id), x.kind, len(x.changes)] for x in r.drift),
            "deferral": back("event", r.deferral.review_event_id) if r.deferral else None,
            "fired": r.deferral_fired,
            "open": {k: [back("event", e.review_event_id) for e in v] for k, v in sorted(r.open_events.items())}}


def _target(kind, ref, back):
    return [kind, back(TARGET_SPACE.get(kind, "location"), ref)]


def _params(params, back, item_target):
    out = {}
    for k, v in sorted((params or {}).items()):
        if k == "keepers":
            out[k] = _set(back, "location", v)
        elif k == "decisions":
            out[k] = _set(back, "decision", v)
        elif k == "policies":
            out[k] = _set(back, "policy", v)
        elif k == "observation_id":
            out[k] = back("observation", v)
        elif k == "other_item":
            out[k] = item_target.get(v)
        else:
            out[k] = v
    return out


def semantics(ev: Evidence, back) -> dict:
    """Every result, id-mapped through `back` -- the identity for the original
    model, the inverse bijection for a relabelled one."""
    L = lambda x: back("location", x)                                          # noqa: E731
    D = lambda x: back("decision", x)                                          # noqa: E731
    proj = R.resolve_all(ev)
    rt = RT.route_all(ev, proj, ev.now)
    out = {}
    out["groups"] = [{
        "group": back("group", g.group_id), "content": back("group", g.content_id),
        "members": [[L(v.location_id), v.status, v.rule, v.resolution_class, _set(back, "decision", v.decision_ids),
                     _set(back, "policy_version", v.protected_by),
                     sorted([back("policy_version", a), D(b)] for a, b in v.overridden), v.policy_tier, v.deferred,
                     v.present, back("physical", v.physical_id), v.size_bytes, len(v.notes)] for v in g.members],
        "keepers": _set(back, "location", g.keeper_set), "protected": _set(back, "location", g.protected),
        "redundant": _set(back, "location", g.redundant_candidates), "undecided": _set(back, "location", g.undecided),
        "canonical": L(g.canonical), "canonical_decision": D(g.canonical_decision_id),
        "suggested": L(g.suggested_canonical), "origin": g.canonical_origin, "effective": L(g.effective_canonical),
        "policy_preferred": [L(x) for x in g.policy_preferred],
        "conflicts": [_conflict(c, back) for c in g.conflicts],
        "counts": [g.location_count, g.physical_copies, g.hardlink_aliases, g.physical_identity_complete, g.size_bytes],
        "eligible": _set(back, "location", g.plan_eligible_locations),
        "reclaim": [g.plan_eligible_reclaim_bytes, g.potential_reclaim_bytes], "ready": g.ready_for_plan,
        "states": [g.review_state, g.resolution_state, g.deferred, g.decision_count, len(g.notes)]}
        for g in proj.groups]
    out["pairs"] = [{
        "pair": back("pair", p.pair_id), "images": _set(back, "location", (p.a_id, p.b_id)),
        "relationship": p.relationship, "rel_decision": D(p.relationship_decision_id),
        "canonical": L(p.canonical), "canonical_decision": D(p.canonical_decision_id),
        "keep_both": p.keep_both, "keep_both_decision": D(p.keep_both_decision_id),
        "keepers": _set(back, "location", p.keeper_set), "redundant": L(p.redundant_candidate),
        "default": p.default_retention, "conflicts": [_conflict(c, back) for c in p.conflicts],
        "state": p.review_state, "ready": p.ready_for_plan, "decisions": _set(back, "decision", p.decision_ids),
        "flags": [p.current, p.exact_content, p.distance, p.method, len(p.notes)]} for p in proj.pairs]
    out["totals"] = dict(proj.totals)
    out["orphaned"] = _set(back, "decision", proj.orphaned_decisions)
    out["protection"] = {L(k): _set(back, "policy_version", v) for k, v in R.protected_locations(ev).items()}
    out["routes"] = {"groups": {back("group", k): _route(v, back) for k, v in rt.groups.items()},
                     "locations": {L(k): _route(v, back) for k, v in rt.locations.items()},
                     "pairs": {back("pair", k): _route(v, back) for k, v in rt.pairs.items()},
                     "counts": dict(rt.counts), "pair_counts": dict(rt.pair_counts)}
    # Queue membership and ORDER: every route, every Show filter and lens.
    out["queues"] = {route: [back("group", g.group_id) for g in RT.ordered(proj, rt, route)] for route in RT.ROUTE_ORDER}
    out["pair_queues"] = {route: [back("pair", p.pair_id) for p in RT.ordered_pairs(proj, rt, route)] for route in RT.ROUTE_ORDER}
    out["filters"] = {show: [back("group", g.group_id) for g in BK.filter_groups(proj, rt, show)] for show in BK.FILTERS}
    out["pair_filters"] = {show: [back("pair", p.pair_id) for p in BK.filter_pairs(proj, rt, ev, show)]
                           for show in BK.PAIR_FILTERS}
    # Bulk: a folder query's targets (in order) and two previews' dispositions.
    folder = _a_folder(ev)
    kind, refs = BK.query_targets(BK.snapshot_query(folder=folder, targets=REG.LOCATION), ev, proj, rt)
    out["query_targets"] = [kind, [back(TARGET_SPACE[kind], r) for r in refs]]
    previews = {}
    for action, params in (("keep_all", {}), ("accept_recommendation", {}), ("redundant_under", {"folder": folder}),
                           ("keep_under", {"folder": folder}), ("defer", {"disposition": "defer_indefinitely"})):
        pv = BK.preview(ev, proj, rt, action, params, REG.SCOPE_QUERY_SNAPSHOT, query=BK.snapshot_query(show="All"))
        previews[action] = [[c.target_kind, back(TARGET_SPACE[c.target_kind], c.target_ref), c.disposition,
                             [o[0] for o in c.outcomes],
                             [[k, tk, back(TARGET_SPACE[tk], ref), back("location", v) if k == "canonical_location" else v]
                              for k, tk, ref, v in c.to_record]] for c in pv.candidates]
    if proj.pairs:
        for action in ("pairs_related_keep_both", "pairs_different"):
            pv = BK.preview_pairs(ev, proj, rt, action, REG.SCOPE_QUERY_SNAPSHOT, query=BK.pair_snapshot_query(show=BK.LENS_ALL))
            previews[action] = [[back("pair", c.target_ref), c.disposition, [o[0] for o in c.outcomes]] for c in pv.candidates]
    out["previews"] = previews
    # Survey Mode: clusters, their numbering and their images' order.
    out["survey"] = {show: [[c.number, c.kind, [L(i) for i in c.images], [back("pair", p) for p in c.pair_ids], c.folders]
                            for c in SV.cluster_map(proj, rt, ev, show).clusters]
                     for show in (BK.LENS_ALL, BK.PAIR_FILTERS[0])}
    # The plan: items in order, excluding their numbering.
    draft = PL.build(ev, proj, rt)
    target_of = {it.item_no: [it.action_kind] + _target(it.target_kind, it.target_ref, back) for it in draft.items}
    out["plan"] = [{
        "action": it.action_kind, "target": _target(it.target_kind, it.target_ref, back),
        "sources": [_target(k, r, back) for k, r in it.sources], "effect": _effect(it.desired_effect, back),
        "decisions": _set(back, "decision", it.decision_ids), "retaining": _set(back, "decision", it.retaining_ids),
        "policies": _set(back, "policy_version", it.policy_version_ids),
        "pre": [[c.key, c.klass, c.severity, c.stage, c.result, _params(c.params, back, target_of)] for c in it.preconditions],
        "post": list(it.postconditions), "deps": sorted(target_of[d] for d in it.depends_on),
        "state": it.state, "blocked": it.blocked_reason, "impact": it.impact_class,
        "estimated": {k: (back("physical", v) if k == "physical_id" else v) for k, v in sorted(it.estimated_effect.items())
                      if k != "notes"}} for it in draft.items]
    out["plan_summary"] = draft.summary
    # Metadata review: every question's state, in the surface's order.
    out["metadata"] = [[back("question", r.question_id), L(r.location_id), r.semantic_field, r.review_state,
                        r.observed_state, r.writer, D(r.decision_id), r.decision_kind, r.desired_value,
                        sorted(r.blocked_by_evidence), sorted(r.integrity_unknown), r.deferral is not None, r.returned]
                       for r in MD.review(ev, ev.now)]
    out["metadata_queue"] = [back("question", r.question_id) for r in MD.queue(ev, now=ev.now)]
    out["project_metadata"] = {L(k): {f: {**x, "decision_id": D(x.get("decision_id"))} if isinstance(x, dict) else x
                                      for f, x in sorted(v.items())} for k, v in MD.project_metadata(ev).items()}
    return out, draft


_PAIR_LABEL = re.compile(r"pair #(\S+)")


def _label(text, fn):
    """A label that names a pair by its number ("preferred by pair #12"), with
    the number put through `fn` -- an id inside words is still an id."""
    return _PAIR_LABEL.sub(lambda m: f"pair #{fn('pair', m.group(1))}", text) if isinstance(text, str) else text


def _effect(effect, back=identity):
    out = {}
    for k, v in sorted(effect.items()):
        if k == "sources":
            continue
        out[k] = [_label(x, back) for x in v] if k == "why" and isinstance(v, list) else v
    return out


def _a_folder(ev):
    """A folder with copies in it, chosen by path (never by id): the parent of
    the first location in display order that has a sibling."""
    locs = sorted(ev.locations.values(), key=lambda l: l.sort_key)
    folders = {}
    for l in locs:
        folders.setdefault(BK.folder_of(l.path), []).append(l)
    for l in locs:
        f = BK.folder_of(l.path)
        if len(folders[f]) > 1:
            return f
    return BK.folder_of(locs[0].path) if locs else "c:\\"


# -- the payload with the bijection applied -----------------------------------------------------

def _payload_forward(payload, B: Bijection):
    """The original payload with every id replaced by its image under the
    bijection, and every id list the payload keeps in id order (a set) put in
    the new ids' order -- what the builder writes for the relabelled model."""
    def ids(space, xs):
        return sorted((B.m(space, x) for x in xs), key=_id_order)
    out = {"schema": payload["schema"], "items": []}
    for it in payload["items"]:
        tk, ref = it["target"]
        new = dict(it)
        new["target"] = [tk, str(B.m(TARGET_SPACE[tk], ref))]
        new["sources"] = [[k, str(B.m(TARGET_SPACE[k], r))] for k, r in it["sources"]]
        if isinstance(it["effect"].get("why"), list):
            new["effect"] = {**it["effect"], "why": [_label(x, B.m) for x in it["effect"]["why"]]}
        new["decisions"] = ids("decision", it["decisions"])
        new["retaining"] = ids("decision", it["retaining"])
        new["policies"] = ids("policy_version", it["policies"])
        new["binding"] = _binding_forward(it["binding"], B)
        pre = []
        for key, klass, sev, stage, params, result in it["pre"]:
            p = dict(params)
            if "keepers" in p:
                p["keepers"] = [B.m("location", x) for x in p["keepers"]]
                if key != "keeper_exists" or p["keepers"] == sorted(p["keepers"], key=_id_order):
                    pass
            if "decisions" in p:
                p["decisions"] = ids("decision", p["decisions"])
            if "policies" in p:
                p["policies"] = ids("policy", p["policies"])
            if "observation_id" in p:
                p["observation_id"] = B.m("observation", p["observation_id"])
            pre.append([key, klass, sev, stage, p, result])
        new["pre"] = pre
        out["items"].append(new)
    return out


def _binding_forward(b, B):
    b = rb(b, B)
    for key in ("members",):
        if isinstance(b.get(key), list):
            b[key] = sorted(b[key], key=lambda m: _id_order(m.get("file_path_id")))
    if isinstance(b.get("source"), dict) and isinstance(b["source"].get("members"), list):
        b["source"]["members"] = sorted(b["source"]["members"], key=lambda m: _id_order(m.get("file_path_id")))
    q = b.get("question")
    if isinstance(q, dict) and isinstance(q.get("analyzer_result_ids"), list):
        q["analyzer_result_ids"] = sorted(q["analyzer_result_ids"], key=_id_order)
    return b


def _canonical_payload(payload):
    """Keeper lists in a keeper_exists params are presented in display order
    (the present keepers) or id order (the declared keepers): compare them as
    sets. Everything else exactly."""
    out = json.loads(json.dumps(payload))
    for it in out["items"]:
        for c in it["pre"]:
            if "keepers" in c[4]:
                c[4]["keepers"] = sorted(str(x) for x in c[4]["keepers"])
    return out


# -- the comparison -----------------------------------------------------------------------------

def _diff(a, b, path="", out=None, limit=6):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        out.append(f"{path}: {str(a)[:80]} != {str(b)[:80]}")
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: only in {'relabelled' if k in b else 'original'}")
            else:
                _diff(a[k], b[k], f"{path}/{k}", out, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} vs {len(b)} entries -- {str(a)[:120]} vs {str(b)[:120]}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                _diff(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append(f"{path}: {str(a)[:100]} != {str(b)[:100]}")
    return out


AREAS = (("groups", "keepers, canonical, protection, redundant candidates and conflicts of every exact-duplicate group, "
                    "in display order"),
         ("pairs", "every visual pair's relationship, retention, keepers and conflicts, in presentation order"),
         ("protection", "which locations each protection policy protects"),
         ("routes", "every route and its conditions, deferrals and open events"),
         ("queues", "queue membership and queue ORDER for every route (groups)"),
         ("pair_queues", "queue membership and order for every route (pairs)"),
         ("filters", "every Show filter and lens on the Decide page, in order"),
         ("pair_filters", "every Show filter and lens on the Similar Images page, in order"),
         ("query_targets", "a frozen folder query's targets, in order"),
         ("previews", "bulk previews: members, order and dispositions"),
         ("survey", "Survey Mode: clusters, their numbers and their images' order"),
         ("plan", "plan items and their links, preconditions, dependencies and states (numbering excluded)"),
         ("plan_summary", "the plan preview's summary"),
         ("metadata", "metadata review states, in the surface's order"),
         ("metadata_queue", "the Metadata Review queue"),
         ("project_metadata", "project metadata"),
         ("totals", "the project totals"), ("orphaned", "orphaned decisions"))


def compare(label, ev: Evidence, seeds=SEEDS):
    base, draft = semantics(ev, identity)
    payload = PL.payload(draft.items)
    for seed in seeds:
        B = Bijection(seed)
        ev2 = relabel(ev, B)
        got, draft2 = semantics(ev2, B.back)
        for key, words in AREAS:
            diffs = _diff(base[key], got[key], key)
            check(f"{label}, seed {seed}: {words} -- the same after relabelling", not diffs, "; ".join(diffs[:4]))
        fwd = _canonical_payload(_payload_forward(payload, B))
        new = _canonical_payload(PL.payload(draft2.items))
        diffs = _diff(fwd, new, "payload")
        check(f"{label}, seed {seed}: the relabelled plan payload is the original payload with the bijection applied "
              f"({len(draft.items)} items; the fingerprint itself names ids and so differs)", not diffs and (
                  PL.fingerprint(draft.items) != PL.fingerprint(draft2.items) or not draft.items), "; ".join(diffs[:4]))
    return base


# -- 1. the hand-built model --------------------------------------------------------------------

def hand_built() -> Evidence:
    import p3_regression as BASE
    import p3_metadata_check as MC
    loc, dec, pol, ev_row = BASE.loc, BASE.dec, BASE.pol, BASE.ev_row
    G, L = REG.GROUP, REG.LOCATION
    r1, r2 = "c:\\r1", "d:\\r2"
    locs = [
        loc("L1", "C:\\R1\\Docs\\a.pdf", r1, "C1", "v:1", 5000), loc("L2", "C:\\R1\\DL\\a.pdf", r1, "C1", "v:2", 5000),
        loc("L3", "C:\\R1\\DL\\a-link.pdf", r1, "C1", "v:2", 5000), loc("L4", "D:\\R2\\Backup\\a.pdf", r2, "C1", "w:4", 5000),
        loc("L5", "C:\\R1\\Docs\\b.docx", r1, "C2", "v:5", 700), loc("L6", "C:\\R1\\Tmp\\b.docx", r1, "C2", "v:6", 700),
        loc("L7", "D:\\R2\\b.docx", r2, "C2", "w:7", 700),
        loc("L8", "C:\\R1\\Keep\\c.txt", r1, "C3", "v:8", 90), loc("L9", "C:\\R1\\Docs\\c.txt", r1, "C3", "v:9", 90),
        loc("L10", "C:\\R1\\X\\d.txt", r1, "C4", "v:10", 40), loc("L11", "C:\\R1\\Y\\d.txt", r1, "C4", None, 40, known=False),
        loc("L12", "C:\\R1\\Z\\e.bin", r1, "C5", "v:12", 10), loc("L13", "C:\\R1\\Z2\\e.bin", r1, "C5", "v:13", 10),
        loc("L14", "C:\\R1\\X\\f.txt", r1, "C6", "v:14", 60), loc("L15", "C:\\R1\\Docs\\f.txt", r1, "C6", "v:15", 60),
        # Two conflicts of one kind in one group: their order must follow the display.
        loc("L16", "D:\\R2\\X\\g.txt", r2, "C7", "w:16", 20), loc("L17", "D:\\R2\\Y\\g.txt", r2, "C7", "w:17", 20),
        loc("L18", "C:\\R1\\g.txt", r1, "C7", "v:18", 20),
    ]
    imgs = [loc(f"L{20 + i}", f"C:\\R1\\Img\\{n}.jpg", r1, None, f"v:{20 + i}", 30000) for i, n in
            enumerate(("p", "q", "r", "s", "t", "u"))]
    # Keep them out of any exact group, as the loader does for unique content.
    ev = BASE.model(locs + imgs)
    pairs = {}
    for pid, a, b, dist in (("P1", "L20", "L21", 2), ("P2", "L21", "L22", 3), ("P3", "L22", "L23", 5),
                            ("P4", "L23", "L24", 4), ("P5", "L24", "L25", 6), ("P6", "L20", "L25", 6),
                            ("P7", "L21", "L23", 3), ("P8", "L22", "L25", 3)):
        pairs[pid] = VisualPair(pid, a, b, "phash", dist, 64, None, f"obs-{a}", f"obs-{b}", True, False)
    ev.pairs = pairs
    VP = REG.VISUAL_PAIR
    ev.decisions = [
        BASE.bdec("D1", "canonical_location", G, "C1", "L1", [l for l in locs if l.content_id == "C1"]),
        dec("D2", "redundant_location", L, "L2"), dec("D3", "must_keep_location", L, "L2", supersedes="D2"),
        dec("D4", "redundant_location", L, "L3", withdrawn=True), dec("D5", "redundant_location", L, "L3"),
        dec("D6", "redundant_location", L, "L4"),
        dec("D7", "override_protection", L, "L4", {"policy_id": "PPV1", "policy_version_id": "PV1"}),
        dec("D8", "redundant_location", L, "L6"), dec("D9", "canonical_location", G, "C2", "L5"),
        dec("D10", "redundant_location", L, "L8"),
        dec("D11", "keep_all_group", G, "C4"),
        dec("D12", "redundant_location", L, "L12"), dec("D13", "redundant_location", L, "L13"),
        dec("D14", "equivalent_copy", VP, "P1"), dec("D15", "canonical_location", VP, "P1", "L20"),
        dec("D16", "equivalent_copy", VP, "P2"), dec("D17", "canonical_location", VP, "P2", "L21"),
        dec("D18", "related_variant", VP, "P3"),
        dec("D19", "different", VP, "P4"), dec("D20", "keep_both", VP, "P4", ["L23", "L24"]),
        dec("D21", "equivalent_copy", VP, "P5"), dec("D22", "canonical_location", VP, "P5", "L25"),
        dec("D23", "must_keep_location", L, "L7"), dec("D24", "redundant_location", L, "L7", supersedes="D23"),
        dec("D25", "keep_both", VP, "P6", ["L20", "L25"]), dec("D26", "equivalent_copy", VP, "P6"),
        dec("D27", "redundant_location", L, "L17"), dec("D28", "redundant_location", L, "L16"),
        dec("D29", "canonical_location", G, "C6", "L15"), dec("D30", "redundant_location", L, "L15"),
    ]
    ev.policies = [
        pol("PV1", "protect_source_root", {"root_key": r2, "root_path": "D:\\R2"}),
        pol("PV2", "protect_folder_subtree", {"path": "C:\\R1\\Keep"}),
        pol("PV3", "prefer_folder_subtree", {"path": "C:\\R1\\Docs"}, {"over": "C:\\R1\\DL"}),
        pol("PV4", "avoid_folder_subtree", {"path": "C:\\R1\\Tmp"}),
        pol("PV5", "prefer_source_root", {"root_key": r1, "root_path": "C:\\R1"}, active=False),
        pol("PV6", "prefer_folder_subtree", {"path": "C:\\R1\\X"}), pol("PV7", "avoid_folder_subtree", {"path": "C:\\R1\\X"}),
        pol("PV8", "protect_folder_subtree", {"path": "D:\\R2"}),
    ]
    ev.events = [
        ev_row("E1", "deferred", G, "C4", "manual", when="2026-09-11T00:00:00Z"),
        ev_row("E2", "skipped", G, "C3"),
        ev_row("E3", "deferred", VP, "P5", "time", "2026-09-01", "2026-09-01T00:00:00Z"),
        ev_row("E4", "deferred", G, "C5", "evidence_change", detail={"members": [{"file_path_id": "L12", "observation_id": "obs-L12"}]}),
        ev_row("E5", "restored", G, "C5", refers_to="E4"),
        ev_row("E6", "deferred", G, "C6", "manual"),
    ]
    ev.roots = {r1: RootCoverage(r1), r2: RootCoverage(r2)}
    ev.now = NOW
    ev.batches = [Batch("B1", REG.SCOPE_QUERY_SNAPSHOT, {"schema": "x", "show": "All", "group_ids": ["C4", "C2"]},
                        (BatchMember(G, "C4", "decided"), BatchMember(G, "C2", "preserved")), "keep_all", {},
                        operation_id="op-B1", occurred_utc="2026-09-12T00:00:00Z")]
    questions = [MC.mq("Q1", "L5", "creator", "ooxml", value="Alice"), MC.mq("Q2", "L1", "title", "pdf", state="unknown"),
                 MC.mq("Q3", "L9", "creator", "other", value="Bob"), MC.mq("Q4", "L15", "title", "pdf", value="Old"),
                 MC.mq("Q5", "L5", "title", "ooxml", value="Report")]
    mdecs = [MC.mdec("M1", "desired_source_metadata_set", "Q1", "Carol", seq=101),
             MC.mdec("M2", "desired_source_metadata_clear", "Q2", seq=102),
             MC.mdec("M3", "leave_source_metadata_unchanged", "Q3", seq=103),
             MC.mdec("M4", "desired_source_metadata_set", "Q4", "New", seq=104),
             MC.mdec("M5", "desired_source_metadata_set", "Q4", "Newer", seq=105, supersedes="M4"),
             MC.mdec("M6", "project_metadata_set", "L6", {"field": "note", "value": "keep the tmp copy"}, seq=106,
                     target_kind=L)]
    MC.with_metadata(ev, questions, mdecs)
    ev.metadata.events = [ev_row("E7", "deferred", REG.METADATA_QUESTION, "Q5", "manual")]
    return ev


def test_hand_built():
    section("1. A hand-built model: every kind of thing, relabelled (3 seeds)")
    ev = hand_built()
    base, _draft = semantics(ev, identity)
    # The model exercises what it claims to (a relabelling of an empty model proves nothing).
    kinds = {c["kind"] for g in base["groups"] for c in g["conflicts"]} | {c["kind"] for p in base["pairs"] for c in p["conflicts"]}
    check("the model holds groups, pairs, conflicts of every kind (protected vs redundant, the floor, canonical no longer "
          "keeper, the policy tie, the chain guard, retention on Different), supersessions, a withdrawal, an override, "
          "deferrals, a restore, a batch, metadata questions and plan items",
          len(base["groups"]) == 7 and len(base["pairs"]) == 8 and {
              R.PROTECTED_VS_REDUNDANT, R.MINIMUM_FILE_LOCATIONS_KIND, R.SAME_PRECEDENCE_POLICY_TIE,
              R.CANONICAL_NO_LONGER_KEEPER, PR.KEEPER_REDUNDANT_ELSEWHERE, PR.RETENTION_ON_DIFFERENT} <= kinds
          and sum(1 for c in next(g for g in base["groups"] if g["group"] == "C7")["conflicts"]
                  if c["kind"] == R.PROTECTED_VS_REDUNDANT) == 2
          and any(d.supersedes for d in ev.decisions) and any(d.withdrawn for d in ev.decisions)
          and base["plan"] and base["metadata"] and base["previews"]["keep_all"], str(sorted(kinds)))
    compare("hand-built", ev)


# -- 2. a real project ----------------------------------------------------------------------------

def real_project(tmp: Path):
    """Real files through Phase 1/2; every decision through the store."""
    import p3_regression as BASE
    import p3_visual_bulk_check as VB
    import p3_metadata_check as MC
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, DUPLICATES, ANALYSIS
    from Phase2.core import connect
    from Phase3 import evidence as E, store as S
    app_root = tmp / "AppRoot"
    (app_root / "Projects").mkdir(parents=True)
    corpus = tmp / "Corpus"
    root1, root2 = BASE.build_corpus(corpus / "Dup")
    photos, _variant = VB.build_variations(corpus / "Img")
    docs, md_photos = MC.build_corpus(corpus / "Md")
    roots = [str(root1), str(root2), str(photos), str(docs), str(md_photos)]
    out = RunWorker(app_root, None, RunRequest(PRESCAN, "Pre-Scan", source_roots=roots, project_name="Relabel")).run()
    project_dir = app_root / "Projects" / "Relabel"
    outs = [out, RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run(),
            RunWorker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze", analyzer_keys=["image", "pdf", "office"])).run()]
    check("the real project: scan, fingerprints, image and document analysis through the worker",
          all(o.ok for o in outs), str([(o.status, o.message[:80]) for o in outs if not o.ok]))
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="relabel-check", session_id="relabel")
    ev = E.load_evidence(conn)
    by = {}
    for fid, root_path, rel in conn.execute("SELECT fp.file_path_id, sr.root_path, fp.relative_path FROM file_path fp "
                                            "JOIN source_root sr ON sr.source_root_id = fp.source_root_id"):
        full = Path(E.full_path(root_path, rel))
        by[full.name.lower() + "|" + full.parent.name.lower()] = str(fid)
    idx = lambda name, folder: by[f"{name}|{folder}"]                          # noqa: E731
    report_group = ev.locations[idx("report.pdf", "documents")].content_id
    tax_group = ev.locations[idx("tax.docx", "documents")].content_id
    photo_group = ev.locations[idx("photo.jpg", "photos")].content_id
    p1 = st.create_policy("protect_folder_subtree", {"path_key": path_key(str(root2 / "Backup")), "path": str(root2 / "Backup")})
    st.create_policy("prefer_folder_subtree", {"path_key": path_key(str(root1 / "Documents")), "path": str(root1 / "Documents")},
                     {"over_path_key": path_key(str(root1 / "Downloads")), "over": str(root1 / "Downloads")})
    st.create_policy("avoid_folder_subtree", {"path_key": path_key(str(root1 / "Archive")), "path": str(root1 / "Archive")})
    old = st.create_policy("prefer_source_root", {"root_key": path_key(str(root1)), "root_path": str(root1)})
    st.retire_policy(old["policy_id"])
    st.record_decision("canonical_location", REG.GROUP, report_group, idx("report.pdf", "documents"))
    d = st.record_decision("redundant_location", REG.LOCATION, idx("report.pdf", "downloads"))
    st.withdraw(d["decision_id"])
    st.record_decision("redundant_location", REG.LOCATION, idx("report.pdf", "downloads"))
    st.record_decision("redundant_location", REG.LOCATION, idx("report.pdf", "backup"))            # protected: a conflict
    st.record_override(idx("report.pdf", "backup"), p1["policy_id"], "relabel check", confirmed=True)
    st.record_decision("must_keep_location", REG.LOCATION, idx("tax.docx", "archive"))
    st.record_decision("redundant_location", REG.LOCATION, idx("tax.docx", "archive"))              # supersedes the keep
    st.record_decision("keep_all_group", REG.GROUP, photo_group)
    st.defer(REG.GROUP, tax_group, "snooze_until_evidence_change", note="later")
    ev = E.load_evidence(conn)
    proj = R.resolve_all(ev)
    rt = RT.route_all(ev, proj, NOW)
    pairs = [p for p in proj.pairs if not p.exact_content]
    for i, pp in enumerate(pairs[:6]):
        if i % 3 == 0:
            st.record_decisions([("equivalent_copy", REG.VISUAL_PAIR, pp.pair_id, True),
                                 ("canonical_location", REG.VISUAL_PAIR, pp.pair_id, pp.a_id)])
        elif i % 3 == 1:
            st.record_decisions([("related_variant", REG.VISUAL_PAIR, pp.pair_id, True),
                                 ("keep_both", REG.VISUAL_PAIR, pp.pair_id, sorted([pp.a_id, pp.b_id], key=_id_order))])
        else:
            st.record_decision("different", REG.VISUAL_PAIR, pp.pair_id)
    if len(pairs) > 7:
        st.defer(REG.VISUAL_PAIR, pairs[7].pair_id, "snooze_until_date", until="2026-09-01")
    pv = BK.preview(ev, proj, rt, "keep_all", {}, REG.SCOPE_EXPLICIT_SELECTION,
                    selection=[g.group_id for g in proj.groups if g.group_id not in (report_group, photo_group)], conn=conn)
    st.commit_bulk(pv, note="relabel batch")
    rep, memo = idx("report.docx", "docs"), idx("paper.pdf", "docs")
    st.record_metadata_decision("desired_source_metadata_set", rep, "creator", "Carol")
    st.record_metadata_decision("desired_source_metadata_clear", memo, "title")
    st.record_metadata_decision("leave_source_metadata_unchanged", rep, "title")
    st.record_metadata_decision("project_metadata_set", memo, value={"field": "tags", "value": ["q3", "paper"]})
    st.defer_metadata(idx("linked.pdf", "docs"), "creator")
    ev = E.load_evidence(conn)
    proj = R.resolve_all(ev)
    rt = RT.route_all(ev, proj, NOW)
    st.record_plan_revision(PL.build(ev, proj, rt), name="relabel plan")
    ev = E.load_evidence(conn)
    ev.now = NOW
    conn.close()
    return ev


def test_real_project(tmp: Path):
    section("2. A real project: real files, the store, the product's loader -- relabelled (3 seeds)")
    ev = real_project(tmp)
    base, _draft = semantics(ev, identity)
    check("the real project holds groups, pairs with decisions, protection, a batch, metadata questions and plan items",
          base["groups"] and any(p["decisions"] for p in base["pairs"]) and base["protection"] and base["plan"]
          and base["metadata"], f"{len(base['groups'])} groups, {len(base['pairs'])} pairs, {len(base['plan'])} items")
    compare("real project", ev)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="p3relabel_"))
    try:
        test_hand_built()
        test_real_project(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 RELABEL: {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
