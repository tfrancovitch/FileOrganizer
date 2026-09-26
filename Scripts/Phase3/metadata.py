r"""Project metadata and desired source metadata (Phase 3, Build 9).

P3.R8 is the source, scoped by P3.R10 §4.6-§8 and §16. Four layers never
collapse into one "field -> value -> save" (P3-A103):

    observed source metadata     Phase 1/2 evidence: what the file says now
    project metadata             the project's own data about the file
    desired source metadata      a Phase 3 decision: what a person wants
                                 written to the file LATER
    executed / verified change   Phase 4's -- which does not exist, and
                                 nothing here simulates it

Metadata is not a second decision engine (R10 §4.6): its four decision kinds
are p3_decision rows like every other. This module is the part that is
genuinely different, and, like `resolve.py` and `plans.py`, it is pure: a
function of the model, reading no source file and writing nothing.

  observe(...)              one field's observed value-state from analyzer
                            results (known / no_value / unknown / unsupported /
                            unavailable / stale / conflicting -- R8 §5)
  capability_for(...)       the capability record for a family x field;
                            no record is status 'unknown'
  writer_check(q)           the writer_supported precondition: only status
                            'supported' passes (none does)
  integrity(q)              the two integrity checks, from evidence flags
  blocked_by_evidence(qs)   the "Blocked by Evidence" projection (R10 §6):
                            question -> signed_or_certified |
                            hardlinked_generic_write, answerable with no plan
                            in existence (F14); the Plan Builder reuses the
                            same checks as hard preconditions
  review(ev) / queue(ev)    the data a metadata review surface needs: value-
                            state, capability, blockers, the current decision,
                            whether it needs another look
  project_metadata(ev)      the project's own metadata per location, labelled
                            as stored in the project (P3-A112)

What this module never does: write or clear anything; pick a winner between
conflicting observed values (P3-A110 -- 'conflicting' is surfaced with every
value); turn an 'unknown' observation into a clear (P3-A111 -- clear is
intent, unknown is evidence; no function here makes a decision at all).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import Evidence, MetadataQuestion, CapabilityRecord, active_decisions
from .registry import (METADATA_FIELDS, PROJECT_METADATA_FIELDS, METADATA_READ_MAP, SIGNABLE_FAMILIES,
                       CAPABILITY_SUPPORTED, PLANNED_METADATA_KINDS, SOURCE_METADATA_KINDS, LOCATION, METADATA_QUESTION,
                       OBS_KNOWN, OBS_NO_VALUE, OBS_UNKNOWN, OBS_UNSUPPORTED, OBS_UNAVAILABLE, OBS_STALE, OBS_CONFLICTING,
                       PROJECT_METADATA_LABEL, DESIRED_METADATA_LABEL, blocked_reason)

SIGNED_KEY, HARDLINK_KEY, WRITER_KEY = "source_not_signed_or_certified", "source_not_hardlinked", "writer_supported"

#: Review states of a metadata question (the data a review surface sorts by).
UNDECIDED, DESIRED, LEFT_UNCHANGED, NEEDS_REVALIDATION = "undecided", "desired", "left_unchanged", "needs_revalidation"
REVIEW_WORDS = {UNDECIDED: "Undecided", DESIRED: "Desired value recorded", LEFT_UNCHANGED: "Left unchanged",
                NEEDS_REVALIDATION: "Needs revalidation"}


# -- observed value-states ---------------------------------------------------------------------

def capability_for(capabilities, family, semantic_field) -> CapabilityRecord:
    """The record for this family x field, or an 'unknown' one: no record is
    never read as support (R10 §16: nothing is writable merely because it is
    readable -- and nothing is readable merely because nobody said no)."""
    rec = (capabilities or {}).get((family, semantic_field))
    return rec if rec is not None else CapabilityRecord(family, semantic_field)


def observe(results, semantic_field, capability, present=True, cloud_only=False, current_observation_id=None):
    """One field's observed state from analyzer results, R8 §5's vocabulary.

    `results`: (analyzer_result_id, analyzer_key, observation_id, status, value)
    rows for the location. For each analyzer that reads this field, the
    newest attempt on the CURRENT observation speaks (Phase 2's latest-attempt
    and staleness rules); older observations only ever make it 'stale'.
    Returns (state, value, values, result_ids, detail)."""
    readers = {a: (kind, src) for a, kind, src in METADATA_READ_MAP.get(semantic_field, ())}
    if capability.read_support == "none" or (not readers):
        return OBS_UNSUPPORTED, None, (), (), "Phase 1/2 records no reading of this field"
    mine = [r for r in results if r[1] in readers]
    current = [r for r in mine if current_observation_id is not None and str(r[2]) == str(current_observation_id)]
    latest = {}
    for r in sorted(current, key=lambda r: int(r[0])):
        latest[r[1]] = r
    ids = tuple(str(r[0]) for r in sorted(latest.values(), key=lambda r: int(r[0])))
    if not latest:
        if not present:
            return OBS_UNAVAILABLE, None, (), (), "not present at the last scan"
        if cloud_only:
            return OBS_UNAVAILABLE, None, (), (), "cloud-only: the product never opens it to read metadata"
        if mine:
            return OBS_STALE, None, (), (), "read from an earlier observation; the file has been observed again since"
        if capability.read_support != "phase2_analyzer":
            return OBS_UNSUPPORTED, None, (), (), "no analyzer reads this field for this kind of file"
        return OBS_UNKNOWN, None, (), (), "not analyzed yet"
    analyzed = [r for r in latest.values() if r[3] == "analyzed"]
    if not analyzed:
        statuses = sorted({r[3] for r in latest.values()})
        if statuses == ["skipped_cloud_only"]:
            return OBS_UNAVAILABLE, None, (), ids, "cloud-only when analyzed; never opened"
        return OBS_UNKNOWN, None, (), ids, "the analyzer did not read it (" + ", ".join(statuses) + ")"
    values = []
    for r in analyzed:
        v = r[4]
        if isinstance(v, str):
            v = v.strip() or None
        if v is not None and v not in values:
            values.append(v)
    if not values:
        return OBS_NO_VALUE, None, (), ids, "analyzed: the field is empty"
    if len(values) == 1:
        return OBS_KNOWN, values[0], (values[0],), ids, ""
    # Two readings disagree: surface both, never pick (P3-A110, R8 §22-24).
    return OBS_CONFLICTING, None, tuple(values), ids, "the readings disagree: " + " / ".join(map(str, values))


# -- the writer and the integrity blockers ------------------------------------------------------

def writer_check(q: MetadataQuestion):
    """writer_supported: (result, detail). Only status 'supported' passes;
    experimental, read_only, blocked and unknown are all 'unsupported' -- no
    partial credit (Build 9 synthesis §8)."""
    cap = q.capability or CapabilityRecord(q.file_family, q.semantic_field)
    if cap.status == CAPABILITY_SUPPORTED:
        return "pass", f"a certified writer covers {cap.semantic_field} in {cap.file_family} files"
    return "unsupported", (f"capability '{cap.status}' for {cap.semantic_field} in {cap.file_family} files: no certified "
                           "source-metadata writer exists (R10 §16) -- planned, not executable")


def integrity(q: MetadataQuestion) -> dict:
    """The two integrity checks from the evidence flags -- {key: (result, detail)}.
    True fails (the write is blocked); False passes; None is 'unknown', which
    a hard precondition treats as blocking (P3-A91)."""
    out = {}
    if q.signed_or_certified is True:
        out[SIGNED_KEY] = ("fail", "signed or certified: a metadata write could invalidate the signature; blocked unless "
                                   "a future specialized workflow supports it (P3-A115)")
    elif q.signed_or_certified is False:
        out[SIGNED_KEY] = ("pass", "not signed or certified")
    elif q.file_family in SIGNABLE_FAMILIES:
        out[SIGNED_KEY] = ("unknown", f"a {q.file_family} file can carry a signature, and Phase 1/2 records no evidence "
                                      "of whether this one does")
    else:
        out[SIGNED_KEY] = ("not_applicable", f"no signature or certification scheme is recognised for {q.file_family} files")
    if q.hardlinked is True:
        out[HARDLINK_KEY] = ("fail", "hard-linked: writing one name's bytes changes every alias; generic metadata "
                                     "writes are blocked until specialized behaviour is proven (P3-A116)")
    elif q.hardlinked is False:
        out[HARDLINK_KEY] = ("pass", "a single name (one hard link)")
    else:
        out[HARDLINK_KEY] = ("unknown", "the link count was not resolved for this file (its size is unique in the "
                                        "scan, so its identity was not read)")
    return out


def integrity_blocks(q: MetadataQuestion) -> list:
    """The definite evidence-level blocks on a question, as reason codes."""
    return [blocked_reason(key, result) for key, (result, _d) in integrity(q).items() if result == "fail"]


def blocked_by_evidence(questions) -> dict:
    """The "Blocked by Evidence" projection (R10 §6; F14): question id -> its
    first definite integrity block. A derived, rebuildable answer straight from
    the evidence flags -- no Plan, Revision or Item is read or needed. The
    Plan Builder asks the same checks (integrity) as hard preconditions."""
    out = {}
    for q in (questions.values() if isinstance(questions, dict) else questions):
        blocks = integrity_blocks(q)
        if blocks:
            out[q.question_id] = blocks[0]
    return out


def integrity_unknown(questions) -> dict:
    """Question id -> the integrity checks the evidence cannot answer."""
    out = {}
    for q in (questions.values() if isinstance(questions, dict) else questions):
        keys = [k for k, (r, _d) in integrity(q).items() if r == "unknown"]
        if keys:
            out[q.question_id] = keys
    return out


# -- decisions and drift ------------------------------------------------------------------------

def active_metadata(ev: Evidence):
    return active_decisions(ev.metadata.decisions)


def current_decisions(active) -> dict:
    """question id -> its active source-metadata decision (one conflict domain:
    set, clear and leave-unchanged supersede one another)."""
    out = {}
    for d in active:
        if d.target_kind == METADATA_QUESTION and d.kind in SOURCE_METADATA_KINDS:
            prev = out.get(d.target_ref)
            if prev is None or d.sequence > prev.sequence:
                out[d.target_ref] = d
    return out


def question_drift(q: MetadataQuestion, decision) -> tuple:
    """What materially changed since the decision was recorded (R8 §53): the
    file observed again, re-read by an analyzer, or gone. Empty: nothing.
    A decision recorded without a binding (the fixture lab's) has nothing to
    compare and never drifts."""
    b = (decision.binding if decision is not None else None) or {}
    if not b:
        return ()
    out = []
    bound_obs = b.get("observation_id")
    if bound_obs is not None and q.observation_id is not None and str(bound_obs) != str(q.observation_id):
        out.append(f"observed again since (observation {bound_obs} -> {q.observation_id})")
    elif bound_obs is not None and q.observation_id is None:
        out.append("no longer in the current evidence")
    bound_ids = tuple(str(x) for x in (b.get("analyzer_result_ids") or ()))
    if not out and bound_ids != tuple(q.analyzer_result_ids):
        out.append("read again by an analyzer since")
    if not out and b.get("observed_state") and b.get("observed_state") != q.observed_state:
        out.append(f"its observed state changed ({b.get('observed_state')} -> {q.observed_state})")
    return tuple(out)


def planned(ev: Evidence) -> list:
    """(question, decision) for every active desired set / clear -- the only
    metadata decisions that propose a source action. project_metadata_set and
    leave_source_metadata_unchanged never appear here: nothing about the
    source is proposed to change, so the Plan Builder has nothing to plan."""
    out = []
    for qid, d in current_decisions(active_metadata(ev)).items():
        q = ev.metadata.questions.get(qid)
        if q is not None and d.kind in PLANNED_METADATA_KINDS:
            out.append((q, d))
    return out


# -- the review data (a surface's needs; no screen ships this round) ----------------------------

@dataclass
class QuestionReview:
    question_id: str
    location_id: str
    path: str | None
    semantic_field: str
    field_label: str
    value_type: str
    observed_state: str
    observed_value: object
    observed_values: tuple
    capability_status: str
    read_support: str
    write_support: str
    blocked_by_evidence: list = field(default_factory=list)
    integrity_unknown: list = field(default_factory=list)
    writer: str = "unsupported"
    decision_id: str | None = None
    decision_kind: str | None = None
    desired_value: object = None
    review_state: str = UNDECIDED
    drift: tuple = ()
    label: str = DESIRED_METADATA_LABEL

    @property
    def word(self):
        return REVIEW_WORDS[self.review_state]


def review(ev: Evidence) -> list:
    """Every metadata question with what a review surface shows (R8 §51):
    the value-state, capability and blockers, the current decision, and
    whether it needs a look. Sorted by location display order, then field."""
    current = current_decisions(active_metadata(ev))
    out = []
    for q in ev.metadata.questions.values():
        spec = METADATA_FIELDS.get(q.semantic_field)
        cap = q.capability or CapabilityRecord(q.file_family, q.semantic_field)
        d = current.get(q.question_id)
        drift = question_drift(q, d) if d is not None else ()
        if d is None:
            state = UNDECIDED
        elif drift:
            state = NEEDS_REVALIDATION
        elif d.kind == "leave_source_metadata_unchanged":
            state = LEFT_UNCHANGED
        else:
            state = DESIRED
        loc = ev.locations.get(q.location_id)
        out.append(QuestionReview(
            q.question_id, q.location_id, loc.path if loc else None, q.semantic_field,
            spec.label if spec else q.semantic_field, spec.value_type if spec else "text",
            q.observed_state, q.observed_value, q.observed_values, cap.status, cap.read_support, cap.write_support,
            integrity_blocks(q), [k for k, (r, _d) in integrity(q).items() if r == "unknown"], writer_check(q)[0],
            d.decision_id if d else None, d.kind if d else None,
            d.value if d is not None and d.kind == "desired_source_metadata_set" else None, state, drift))
    order = {k: i for i, k in enumerate(METADATA_FIELDS)}

    def key(r):
        loc = ev.locations.get(r.location_id)
        return (loc.sort_key if loc else (), r.location_id, order.get(r.semantic_field, 99))
    return sorted(out, key=key)


def queue(ev: Evidence) -> list:
    """The Metadata Review queue: questions with no decision, and decided ones
    whose evidence materially changed. Left Unchanged stays out until then
    (R8 §53) -- the same durable routing intent an explicit deferral carries."""
    return [r for r in review(ev) if r.review_state in (UNDECIDED, NEEDS_REVALIDATION)]


def project_metadata(ev: Evidence) -> dict:
    """location id -> {field: {"value", "decision_id", "stored_in"}}: the
    project's own metadata, from active project_metadata_set rows. A field
    whose latest value is None holds no value in the project."""
    out = {}
    for d in active_metadata(ev):
        if d.kind != "project_metadata_set" or d.target_kind != LOCATION:
            continue
        f = d.value.get("field")
        if f not in PROJECT_METADATA_FIELDS:
            continue
        cur = out.setdefault(d.target_ref, {})
        prev = cur.get(f)
        if prev is None or int(d.sequence) > prev["sequence"]:
            cur[f] = {"value": d.value.get("value"), "decision_id": d.decision_id, "sequence": int(d.sequence),
                      "stored_in": PROJECT_METADATA_LABEL}
    for fields in out.values():
        for v in fields.values():
            v.pop("sequence", None)
    return out
