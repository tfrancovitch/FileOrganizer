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
from .registry import (METADATA_FIELDS, PROJECT_METADATA_FIELDS, METADATA_READ_MAP, SIGNABLE_FAMILIES, ENCRYPTABLE_FAMILIES,
                       CAPABILITY_SUPPORTED, PLANNED_METADATA_KINDS, SOURCE_METADATA_KINDS, LOCATION, METADATA_QUESTION,
                       OBS_KNOWN, OBS_NO_VALUE, OBS_UNKNOWN, OBS_UNSUPPORTED, OBS_UNAVAILABLE, OBS_STALE, OBS_CONFLICTING,
                       PROJECT_METADATA_LABEL, DESIRED_METADATA_LABEL, blocked_reason)

SIGNED_KEY, HARDLINK_KEY, WRITER_KEY = "source_not_signed_or_certified", "source_not_hardlinked", "writer_supported"
ENCRYPTED_KEY = "source_not_encrypted"

#: Review states of a metadata question (the data a review surface sorts by).
UNDECIDED, DESIRED, LEFT_UNCHANGED, NEEDS_REVALIDATION = "undecided", "desired", "left_unchanged", "needs_revalidation"
CLEAR_REQUESTED, DEFERRED = "clear_requested", "deferred"          # B17 (R8 §52)
REVIEW_WORDS = {UNDECIDED: "Undecided", DESIRED: "Desired value recorded", LEFT_UNCHANGED: "Left unchanged",
                NEEDS_REVALIDATION: "Needs revalidation", CLEAR_REQUESTED: "Clear requested", DEFERRED: "Deferred"}


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
    if not present and current_observation_id is not None:
        # (B18) A file the last scan did not find keeps its old readings on its
        # last observation; they describe a file that is not there. Its value
        # is unavailable -- a value-state change, so a decision on it returns.
        return OBS_UNAVAILABLE, None, (), ids, "not present at the last scan"
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
    # (B18) In the order of the readings themselves (newest attempt's id), not
    # of which older rows happened to be read first: a conflict lists its values
    # the same way however much history is loaded.
    analyzed = [r for r in sorted(latest.values(), key=lambda r: int(r[0])) if r[3] == "analyzed"]
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
    """The integrity checks from the evidence flags -- {key: (result, detail)}:
    signed / certified, encrypted (B17, R8 §34), hard-linked. True fails (the
    write is blocked); False passes; None is 'unknown', which a hard
    precondition treats as blocking (P3-A91). A family with no such scheme,
    or a file the analyzer found to be RTF saved as .doc, is not applicable."""
    out = {}
    if q.signed_or_certified is True:
        out[SIGNED_KEY] = ("fail", f"{q.signature_state or 'signed'}: a metadata write could invalidate the signature; "
                                   "blocked unless a future specialized workflow supports it (P3-A115)")
    elif q.signed_or_certified is False:
        out[SIGNED_KEY] = ("pass", "not signed or certified (the analyzer found no signature)")
    elif q.file_family in SIGNABLE_FAMILIES and SIGNED_KEY not in q.integrity_na:
        out[SIGNED_KEY] = ("unknown", f"a {q.file_family} file can carry a signature, and no current analysis says whether "
                                      "this one does (analyzed before B17, or the analyzer could not read it)")
    else:
        out[SIGNED_KEY] = ("not_applicable", f"no signature or certification scheme is recognised for this "
                                             f"{'RTF' if SIGNED_KEY in q.integrity_na else q.file_family} file")
    if q.encrypted is True:
        out[ENCRYPTED_KEY] = ("fail", "encrypted or password-protected: a metadata write is blocked (P3.R8 §34)")
    elif q.encrypted is False:
        out[ENCRYPTED_KEY] = ("pass", "not encrypted (the analyzer opened it without a password)")
    elif q.file_family in ENCRYPTABLE_FAMILIES and ENCRYPTED_KEY not in q.integrity_na:
        out[ENCRYPTED_KEY] = ("unknown", f"a {q.file_family} file can be encrypted, and the evidence does not say whether "
                                         "this one is")
    else:
        out[ENCRYPTED_KEY] = ("not_applicable", f"no encryption scheme is recognised for this "
                                                f"{'RTF' if ENCRYPTED_KEY in q.integrity_na else q.file_family} file")
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
    """What materially changed since the decision was recorded (R8 §53; see
    binding_drift). Empty: nothing. A decision recorded without a binding
    (the fixture lab's) has nothing to compare and never drifts."""
    return binding_drift(q, (decision.binding if decision is not None else None) or {})


def value_key(values) -> tuple:
    """The observed value(s) as compared: stripped, distinct, order-free."""
    out = set()
    for v in values or ():
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue
        elif v is None:
            continue
        out.add(v if isinstance(v, str) else repr(v))
    return tuple(sorted(out))


def _shown(values):
    vals = value_key(values)
    return " / ".join(repr(v) for v in vals) if vals else "no value"


def binding_drift(q: MetadataQuestion, b) -> tuple:
    """(B17; narrowed in B18) What materially changed since a binding was
    recorded -- a decision's, or the one a deferral 'until the evidence
    changes' recorded. Judged by WHAT changed, never by a newer row existing:

      1. the file's content identity changed -- a new observation (Phase 1/2
         write one only when the file's size or times changed), unless both
         sides carry the same authoritative content hash; or the file is no
         longer in the current evidence;
      2. the observed value of the field changed;
      3. the field's value-state changed (known -> conflicting, -> unavailable...).

    NOT material: a new analyzer row with the same value and state, an
    analyzer version change, a rescan that finds the file unchanged, or a
    change in integrity evidence (signature, encryption, hard link -- those
    re-check plans, see plans._metadata_obsolescence, never the decision).

    A B18 binding carries the value(s) it was decided on; a B16 / B17 one
    carries analyzer-result ids, resolved to values at load
    (evidence.resolve_bound_values). One whose ids could not be resolved
    falls back to comparing ids, B17's rule -- conservative, never silent."""
    b = b or {}
    if not b:
        return ()
    out = []
    bound_obs = b.get("observation_id")
    if bound_obs is not None and q.observation_id is None:
        out.append("no longer in the current evidence")
    elif bound_obs is not None and str(bound_obs) != str(q.observation_id):
        bound_content = b.get("content_id")
        if bound_content is None or q.content_id is None or str(bound_content) != str(q.content_id):
            out.append(f"the file changed since (observation {bound_obs} -> {q.observation_id})")
    if not out and b.get("observed_state") and b.get("observed_state") != q.observed_state:
        out.append(f"its observed state changed ({b.get('observed_state')} -> {q.observed_state})")
    if not out:
        bound = b.get("observed_values") if "observed_values" in b else b.get("resolved_values")
        if bound is not None:
            if value_key(bound) != value_key(q.observed_values):
                out.append(f"its observed value changed ({_shown(bound)} -> {_shown(q.observed_values)})")
        elif "analyzer_result_ids" in b:
            if tuple(str(x) for x in (b.get("analyzer_result_ids") or ())) != tuple(q.analyzer_result_ids):
                out.append("read again by an analyzer since, and the reading it was decided on is no longer in "
                           "the database to compare values")
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
    deferral: object = None                     # B17: the open deferral event, if any
    returned: bool = False                      # B17: a deferral whose trigger has fired (back in the queue)
    file_family: str = "other"

    @property
    def word(self):
        return REVIEW_WORDS[self.review_state]


def open_deferral(events):
    """The deferral still open among one question's events (oldest first):
    a new deferral replaces an open one; a restore ends the one it names, or
    every one when it names none (Build 3's rule)."""
    current = None
    for e in events:
        if e.kind == "deferred":
            current = e
        elif e.kind == "restored" and current is not None and (e.refers_to is None or e.refers_to == current.review_event_id):
            current = None
    return current


def deferral_fired(q: MetadataQuestion, e, now) -> bool:
    """Has a metadata deferral's trigger fired? A date that has come; the
    evidence changed since (the binding the event recorded, compared the way a
    decision's is); 'until you restore it' never fires by itself."""
    if e is None:
        return False
    if e.return_kind == "time":
        return bool(e.return_on_utc) and bool(now) and str(now) >= str(e.return_on_utc)
    if e.return_kind == "evidence_change":
        return bool(binding_drift(q, e.detail))
    return False


def _row(q, d, events, now, path=None):
    spec = METADATA_FIELDS.get(q.semantic_field)
    cap = q.capability or CapabilityRecord(q.file_family, q.semantic_field)
    drift = question_drift(q, d) if d is not None else ()
    deferral = open_deferral(events)
    fired = deferral_fired(q, deferral, now)
    if drift:
        state = NEEDS_REVALIDATION
    elif deferral is not None and not fired:
        state = DEFERRED
    elif d is None:
        state = UNDECIDED
    elif d.kind == "leave_source_metadata_unchanged":
        state = LEFT_UNCHANGED
    elif d.kind == "desired_source_metadata_clear":
        state = CLEAR_REQUESTED
    else:
        state = DESIRED
    integ = integrity(q)
    return QuestionReview(
        q.question_id, q.location_id, path or q.path, q.semantic_field,
        spec.label if spec else q.semantic_field, spec.value_type if spec else "text",
        q.observed_state, q.observed_value, q.observed_values, cap.status, cap.read_support, cap.write_support,
        [blocked_reason(k, r) for k, (r, _d) in integ.items() if r == "fail"],
        [k for k, (r, _d) in integ.items() if r == "unknown"], writer_check(q)[0],
        d.decision_id if d else None, d.kind if d else None,
        d.value if d is not None and d.kind == "desired_source_metadata_set" else None, state, drift,
        deferral=deferral, returned=deferral is not None and fired, file_family=q.file_family)


def review(ev: Evidence, now=None) -> list:
    """Every metadata question with what a review surface shows (R8 §51-52):
    the value-state, capability and blockers, the current decision, an open
    deferral, and whether it needs a look. Sorted by location display order
    (or path), then field."""
    current = current_decisions(active_metadata(ev))
    by_q = {}
    for e in ev.metadata.events:
        by_q.setdefault(e.target_ref, []).append(e)
    now = now or ev.now
    out = []
    for q in ev.metadata.questions.values():
        loc = ev.locations.get(q.location_id)
        out.append(_row(q, current.get(q.question_id), by_q.get(q.question_id, []), now, loc.path if loc else None))
    order = {k: i for i, k in enumerate(METADATA_FIELDS)}

    def key(r):
        loc = ev.locations.get(r.location_id)
        return (loc.sort_key if loc else (), (r.path or "").lower(), r.location_id, order.get(r.semantic_field, 99))
    return sorted(out, key=key)


def file_review(md, now=None) -> list:
    """(B17) One file's rows for the Metadata page: every semantic field,
    asked or not, in registry order. `md` is evidence.file_metadata's slice;
    an unasked field is simply Undecided -- looking at it asked nothing."""
    current = current_decisions(active_decisions(md.decisions))
    by_q = {}
    for e in md.events:
        by_q.setdefault(e.target_ref, []).append(e)
    order = {k: i for i, k in enumerate(METADATA_FIELDS)}
    rows = [_row(q, current.get(q.question_id), by_q.get(q.question_id, []), now) for q in md.questions.values()]
    return sorted(rows, key=lambda r: order.get(r.semantic_field, 99))


#: (B17) The Metadata Review queue's population -- the questions that need a
#: person (judgment call, reported in the B17 handback):
#:   1. a question a person named and has not decided (or whose deferral returned);
#:   2. a decided question whose evidence materially changed (needs revalidation);
#:   3. a question whose deferral trigger has fired;
#:   4. a field observed as `conflicting` that no question names yet (R8 §22:
#:      surfaced, never guessed) -- listed without creating a question row.
#: Left Unchanged and an open deferral stay out until their evidence changes or
#: their trigger fires (R8 §53). Every file x every field is NOT the queue.
QUEUE_STATES = (UNDECIDED, NEEDS_REVALIDATION)


def queue(ev: Evidence, conflicts=(), now=None, reviewed=None) -> list:
    """The Metadata Review queue (see QUEUE_STATES): review rows needing a
    person, then any surfaced conflicts (question_id None). `reviewed`: the
    rows review(ev, now) already returned, when the caller has them."""
    rows = [r for r in (reviewed if reviewed is not None else review(ev, now))
            if r.review_state in QUEUE_STATES or r.returned]
    asked = {(r.location_id, r.semantic_field) for r in rows}
    for q in conflicts:
        if (q.location_id, q.semantic_field) not in asked:
            rows.append(_row(q, None, [], now))
    return rows


def queue_filter(rows, semantic_field=None, family=None, observed_state=None, blocker=None) -> list:
    """(B17) The queue page's filters. `blocker`: an integrity reason
    (signed_or_certified, encrypted, hardlinked_generic_write), 'writer'
    (no certified writer) or 'unknown' (an integrity check the evidence
    cannot answer)."""
    out = []
    for r in rows:
        if semantic_field and r.semantic_field != semantic_field:
            continue
        if family and r.file_family != family:
            continue
        if observed_state and r.observed_state != observed_state:
            continue
        if blocker == "writer" and r.writer == "pass":
            continue
        if blocker == "unknown" and not r.integrity_unknown:
            continue
        if blocker not in (None, "", "writer", "unknown") and blocker not in r.blocked_by_evidence:
            continue
        out.append(r)
    return out


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
