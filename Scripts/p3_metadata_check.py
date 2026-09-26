#!/usr/bin/env python3
r"""Phase 3 Build 9 (B16) regression: project metadata and desired source metadata.

What it proves, in order:

  1. The vocabulary and the schema (synthesis §0): the four metadata kinds
     are registry data in the one generic Decision engine (p3_decision has no
     CHECK on its kind or target columns -- no Decision-side migration); the
     semantic field registry keeps text, dates and keyword lists apart; the
     capability record (migration 014) holds only registry vocabulary, and
     no row says 'supported'; typed values, and an empty value is not a clear.
  2. The pure model: all seven observed value-states kept distinct (never
     'unknown' for 'no value'); a conflict surfaced with both values, never a
     winner; writer_supported passes on 'supported' only; the integrity
     checks (true fails, false passes, no evidence is unknown); the
     evidence-level "blocked" projection with no plan; the Plan Builder's
     Update Source Metadata items (F13's two fields; each blocker; unknown
     blocks; a genuinely supported writer would pass -- the check reads the
     record); project_metadata_set and leave_source_metadata_unchanged NEVER
     make a plan item of any kind (necessity 10), in every combination; an
     'unknown' observation manufactures nothing; the review data and the
     Metadata Review queue (Left Unchanged stays out until the evidence
     materially changes, R8 §53); obsolescence of a metadata item by each of
     its own dependencies and by nothing else.
  3. A real project with real .docx, .pdf and .jpg files and a real NTFS hard
     link, through Pre-Scan, Find My Duplicates and the real PDF and Office
     analyzers: Phase 1/2's own readings become the questions' observed
     values (the integration point no earlier round touched); the store's
     question-plus-decision operation, its refusals and supersession; the
     duplicate and pair model unchanged by any metadata decision; the plan;
     handoff §4.1 -- a revision holding a ready removal AND an F13-shaped
     blocked metadata item LOCKS, the item still present and blocked, the
     fingerprint over the whole payload including it; obsolescence through
     the existing detector (and not for an unrelated change); a file edited
     and rescanned sends its decided and left-unchanged questions back for
     review; the journal.
  4. Zero source access: no source file opened, stat'ed or listed, and no
     write outside the project, while models load, questions are listed,
     decisions recorded, plans built, frozen and locked, and the detector
     runs; every corpus byte unchanged (except the file the test edits).
  5. If a display is available: the Plan page shows the metadata items with
     their reasons.

Run:  python Scripts\p3_metadata_check.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

import p3_plan_check as PC                                                   # noqa: E402  (ReadGuard, helpers)
BASE = PC.BASE                                                               # noqa: E402  (p3_regression helpers)
from Phase3 import resolve as R                                              # noqa: E402
from Phase3 import routing as RT                                             # noqa: E402
from Phase3 import registry as REG                                           # noqa: E402
from Phase3 import plans as PL                                               # noqa: E402
from Phase3 import metadata as MD                                            # noqa: E402
from Phase3.model import Decision, MetadataEvidence, MetadataQuestion, CapabilityRecord   # noqa: E402

RESULTS = []
LOCATION, GROUP, MQ = REG.LOCATION, REG.GROUP, REG.METADATA_QUESTION
REMOVE, PRESERVE, META = REG.ACTION_REMOVE, REG.ACTION_PRESERVE, REG.ACTION_METADATA
READY, BLOCKED, BLOCKED_EV, UNSUPPORTED = REG.ITEM_READY, REG.ITEM_BLOCKED, REG.ITEM_BLOCKED_EVIDENCE, REG.ITEM_UNSUPPORTED


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" -- {detail}" if (detail and not ok) else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


refused = PC.refused
gloc, gdec, routed = BASE.loc, BASE.dec, BASE.routed


def mdec(did, kind, ref, value=True, seq=None, target_kind=MQ, supersedes=None, withdrawn=False, binding=None):
    return Decision(did, target_kind, ref, kind, value, "explicit_human", supersedes, withdrawn, f"op-{did}",
                    seq or int(did[1:]), binding or {})


def cap(family, fld, status="read_only", read="phase2_analyzer", write="none"):
    return CapabilityRecord(family, fld, read, write, status)


def mq(qid, lid, fld="creator", family="ooxml", status="read_only", state="known", value="Alice", signed=False,
       hard=False, obs=None, ids=("1",), values=None):
    return MetadataQuestion(qid, lid, fld, observed_state=state, observed_value=value if state == "known" else None,
                            observed_values=values if values is not None else ((value,) if state == "known" else ()),
                            file_family=family, capability=cap(family, fld, status), signed_or_certified=signed,
                            hardlinked=hard, observation_id=obs or f"obs-{lid}", analyzer_result_ids=tuple(ids))


def with_metadata(ev, questions, decisions):
    ev.metadata = MetadataEvidence({q.question_id: q for q in questions}, list(decisions), {})
    return ev


def meta_items(draft):
    return {i.sources[0][1]: i for i in draft.items if i.action_kind == META}


# ---------------------------------------------------------------------------
# 1. Vocabulary and schema
# ---------------------------------------------------------------------------

def test_vocabulary(tmp: Path):
    section("1. The vocabulary, the field registry, the capability record (synthesis §0, necessities 2, 4, 5)")
    kinds = REG.DECISION_KINDS
    check("the four metadata kinds are registered in the ONE decision registry (R10 §4.6: no second engine)",
          set(REG.METADATA_DECISION_KINDS) <= set(kinds) and REG.METADATA_DECISION_KINDS == (
              "project_metadata_set", "desired_source_metadata_set", "desired_source_metadata_clear",
              "leave_source_metadata_unchanged"))
    check("project_metadata_set targets a file location (never a metadata question); the other three target a "
          "metadata_question", kinds["project_metadata_set"].target_kinds == frozenset({LOCATION})
          and all(kinds[k].target_kinds == frozenset({MQ}) for k in REG.SOURCE_METADATA_KINDS))
    check("set, clear and leave-unchanged share one conflict domain (a later one supersedes the earlier); project "
          "metadata conflicts per field (a new note never replaces the tags)",
          {kinds[k].domain_key(v) for k, v in (("desired_source_metadata_set", "x"), ("desired_source_metadata_clear", True),
                                               ("leave_source_metadata_unchanged", True))} == {"source_metadata"}
          and kinds["project_metadata_set"].domain_key({"field": "note", "value": "a"}) == "project_metadata:note"
          and kinds["project_metadata_set"].domain_key({"field": "tags", "value": ["a"]}) == "project_metadata:tags")
    check("no metadata kind is retention intent, and none changes reclaim",
          all(kinds[k].resolution_class == "none" and not kinds[k].affects_reclaim for k in REG.METADATA_DECISION_KINDS))
    check("the semantic field registry: identifier, display name, value type -- text, date and keyword list kept apart",
          all(f.key and f.label and f.value_type in REG.VALUE_TYPES for f in REG.METADATA_FIELDS.values())
          and {REG.METADATA_FIELDS[k].value_type for k in ("title", "content_created", "keywords")} == {"text", "date", "keyword_list"})
    check("project metadata fields are their own registry, labelled as stored in the project (P3-A112)",
          not set(REG.PROJECT_METADATA_FIELDS) & set(REG.METADATA_FIELDS)
          and REG.PROJECT_METADATA_LABEL.startswith("Stored in the File Organizer project"))
    check("seven observed value-states, distinct words (R8 §5)", len(set(REG.OBSERVED_STATES)) == 7 and set(REG.OBSERVED_STATES) == {
        "known", "no_value", "unknown", "unsupported", "unavailable", "stale", "conflicting"})
    check("capability statuses are R8 §18's five; only 'supported' is the writer", REG.CAPABILITY_STATUSES == (
        "supported", "experimental", "read_only", "blocked", "unknown") and REG.CAPABILITY_SUPPORTED == "supported")
    # Typed values.
    v = kinds["desired_source_metadata_set"]
    check("an empty desired value is refused -- clearing is its own decision (P3-A111, R8 §9)",
          refused(lambda: v.validate_value("")) and refused(lambda: v.validate_value("   "))
          and refused(lambda: v.validate_value(None)) and refused(lambda: v.validate_value([])))
    check("clear and leave-unchanged take the value true, nothing else",
          refused(lambda: kinds["desired_source_metadata_clear"].validate_value("")) and
          kinds["desired_source_metadata_clear"].validate_value(True) is True
          and refused(lambda: kinds["leave_source_metadata_unchanged"].validate_value("x")))
    date = REG.METADATA_FIELDS["content_created"]
    kw = REG.METADATA_FIELDS["keywords"]
    check("a date field takes an ISO date or date-time only", REG.validate_field_value(date, "2024-05-01") == "2024-05-01"
          and REG.validate_field_value(date, "2024-05-01T10:30:00Z") and refused(lambda: REG.validate_field_value(date, "yesterday"))
          and refused(lambda: REG.validate_field_value(date, "2024-13-40")))
    check("a keyword list is a list of distinct non-empty keywords, order kept", REG.validate_field_value(kw, [" tax", "2024", "tax"]) == ["tax", "2024"]
          and refused(lambda: REG.validate_field_value(kw, "tax")) and refused(lambda: REG.validate_field_value(kw, ["ok", ""])))
    pm = kinds["project_metadata_set"]
    check("project metadata: a registered field and a typed value; None means the project holds no value",
          pm.validate_value({"field": "review_priority", "value": "high"}) == {"field": "review_priority", "value": "high"}
          and pm.validate_value({"field": "note", "value": None}) == {"field": "note", "value": None}
          and refused(lambda: pm.validate_value({"field": "review_priority", "value": "urgent"}))
          and refused(lambda: pm.validate_value({"field": "creator", "value": "x"}))
          and refused(lambda: pm.validate_value({"field": "note"})))
    check("file families by extension (the capability is per family x field)",
          (REG.file_family(r"C:\a\Report.DOCX"), REG.file_family("x.pdf"), REG.file_family("p.jpeg"), REG.file_family("n.txt"))
          == ("ooxml", "pdf", "jpeg", "other"))
    # The schema.
    import fo_db
    paths = fo_db.ProjectPaths(str(tmp / "Schema"))
    paths.ensure_dirs()
    c = fo_db.connect(paths.database_file, create=True)
    fo_db.apply_migrations(c, paths, app_version=fo_db.APP_VERSION)
    ddl = c.execute("SELECT sql FROM sqlite_master WHERE name = 'p3_decision'").fetchone()[0]
    check("§0: p3_decision.decision_kind and target_kind carry no CHECK -- the four kinds and the new target are data, "
          "not a migration", "CHECK" not in ddl.upper().split("DECISION_KIND")[1].split(",")[0].upper()
          and "CHECK" not in ddl.upper().split("TARGET_KIND")[1].split(",")[0].upper())
    rows = c.execute("SELECT file_family, semantic_field, read_support, write_support, status FROM p3_metadata_capability").fetchall()
    check("the capability record (014): every row in registry vocabulary, every field a registry field",
          rows and all(r[1] in REG.METADATA_FIELDS and r[2] in REG.READ_SUPPORT and r[3] in REG.WRITE_SUPPORT
                       and r[4] in REG.CAPABILITY_STATUSES and r[0] in set(REG.FILE_FAMILIES.values()) for r in rows))
    check("no row says 'supported' and none claims a writer: what Phase 1/2 reads is read-only (R10 §16)",
          all(r[4] == "read_only" and r[3] == "none" for r in rows), str([r for r in rows if r[4] != "read_only"]))
    readers = {(fam, f) for f, rd in REG.METADATA_READ_MAP.items() for a, _k, _s in rd
               for fam in {"pdf": ("pdf",), "office": ("ooxml", "ole_office"), "raw_image": ("raw_image",), "audio": ("audio",)}[a]}
    check("the record and the read map agree: a row exactly where an analyzer reads that field", {(r[0], r[1]) for r in rows} == readers)
    cols = [r[1] for r in c.execute("PRAGMA table_info(p3_metadata_capability)")]
    check("the capability record has no writer / version / strategy columns yet (a later build adds them; none left empty)",
          cols == ["file_family", "semantic_field", "read_support", "write_support", "status", "note"])
    check("app_meta names the metadata contract", c.execute("SELECT value FROM app_meta WHERE key='phase3.metadata_schema'").fetchone()[0]
          == "fileorganizer.p3.metadata/1")
    c.close()


# ---------------------------------------------------------------------------
# 2. The pure model
# ---------------------------------------------------------------------------

def test_observe():
    section("2a. Observed value-states (R8 §5, necessity 6): seven, never collapsed")
    pdf = cap("pdf", "creator")
    seen = {}

    def o(results, fld="creator", capability=pdf, **k):
        s = MD.observe(results, fld, capability, current_observation_id=k.pop("obs", 10), **k)
        seen.setdefault(s[0], s)
        return s
    s = o([(1, "pdf", 10, "analyzed", "Alice")])
    check("known: the analyzer read a value on the current observation", s[:3] == ("known", "Alice", ("Alice",)) and s[3] == ("1",))
    s = o([(1, "pdf", 10, "analyzed", "Old"), (2, "pdf", 10, "analyzed", "New")])
    check("the latest attempt speaks (Phase 2's rule)", s[1] == "New" and s[3] == ("2",))
    check("no_value: analyzed, and the field is empty -- not 'unknown'", o([(1, "pdf", 10, "analyzed", "  ")])[0] == "no_value"
          and o([(1, "pdf", 10, "analyzed", None)])[0] == "no_value")
    check("unknown: never analyzed, or the analyzer failed -- not 'no value'", o([])[0] == "unknown"
          and o([(1, "pdf", 10, "error", None)])[0] == "unknown")
    check("stale: read only from an earlier observation", o([(1, "pdf", 9, "analyzed", "Alice")])[0] == "stale")
    check("unavailable: not present, or cloud-only (never opened to read)", o([], present=False)[0] == "unavailable"
          and o([], cloud_only=True)[0] == "unavailable" and o([(1, "pdf", 10, "skipped_cloud_only", None)])[0] == "unavailable")
    check("unsupported: no analyzer reads this field (keywords), or none for this family",
          o([(1, "pdf", 10, "analyzed", "x")], fld="keywords", capability=cap("pdf", "keywords", read="none"))[0] == "unsupported"
          and o([], capability=CapabilityRecord("jpeg", "creator"))[0] == "unsupported")
    s = o([(1, "pdf", 10, "analyzed", "Alice"), (2, "office", 10, "analyzed", "Bob")])
    check("conflicting: two readings disagree -- both values surfaced, no winner picked (P3-A110)",
          s[0] == "conflicting" and s[1] is None and set(s[2]) == {"Alice", "Bob"} and "Alice" in s[4] and "Bob" in s[4])
    check("all seven states reached, each distinct", set(seen) == set(REG.OBSERVED_STATES), str(sorted(seen)))


def test_capability_and_integrity():
    section("2b. writer_supported and the integrity blockers (necessities 4, 7)")
    for status in REG.CAPABILITY_STATUSES:
        q = mq("M", "1", status=status)
        r = MD.writer_check(q)[0]
        check(f"capability '{status}': writer_supported {'passes' if status == 'supported' else 'is unsupported'}",
              r == ("pass" if status == "supported" else "unsupported"))
    check("no record at all is status 'unknown' (never support by default)",
          MD.capability_for({}, "pdf", "creator").status == "unknown" and MD.capability_for(
              {("pdf", "creator"): cap("pdf", "creator")}, "pdf", "creator").status == "read_only")
    I = lambda **k: MD.integrity(replace(mq("M", "1"), **k))                   # noqa: E731
    check("signed: true fails, false passes, no evidence on a signable family is unknown, elsewhere not applicable",
          I(signed_or_certified=True)[MD.SIGNED_KEY][0] == "fail" and I(signed_or_certified=False)[MD.SIGNED_KEY][0] == "pass"
          and I(signed_or_certified=None, file_family="pdf")[MD.SIGNED_KEY][0] == "unknown"
          and I(signed_or_certified=None, file_family="jpeg")[MD.SIGNED_KEY][0] == "not_applicable")
    check("hard-linked: true fails, false passes, an unresolved link count is unknown",
          I(hardlinked=True)[MD.HARDLINK_KEY][0] == "fail" and I(hardlinked=False)[MD.HARDLINK_KEY][0] == "pass"
          and I(hardlinked=None)[MD.HARDLINK_KEY][0] == "unknown")
    qs = [mq("M1", "1", family="pdf", status="supported", signed=True), mq("M2", "2", family="jpeg", status="supported", hard=True),
          mq("M3", "3"), mq("M4", "4", family="jpeg", signed=None, hard=None), mq("M5", "5", family="pdf", signed=True, hard=True)]
    check("the evidence-level projection: question -> reason, definite blocks only, NO plan involved (F14's shape)",
          MD.blocked_by_evidence(qs) == {"M1": "signed_or_certified", "M2": "hardlinked_generic_write", "M5": "signed_or_certified"})
    check("both blocks on one question are both there to see; unknowns are reported apart, never as blocked",
          MD.integrity_blocks(qs[4]) == ["signed_or_certified", "hardlinked_generic_write"]
          and MD.integrity_unknown(qs) == {"M4": [MD.HARDLINK_KEY]})


def _plan_model():
    L = [gloc("1", r"C:\D\report.docx", content=None, phys="P1"), gloc("2", r"C:\D\signed.pdf", content=None, phys="P2"),
         gloc("3", r"C:\P\linked.jpg", content=None, phys="P3"), gloc("4", r"C:\P\photo.jpg", content=None, phys="P4"),
         gloc("5", r"C:\X\a.bin", content="C5", phys="P5", size=700), gloc("6", r"C:\Y\a.bin", content="C5", phys="P6", size=700)]
    retention = [gdec("D1", "must_keep_location", LOCATION, "5"), gdec("D2", "redundant_location", LOCATION, "6")]
    ev, proj, rt = routed(L, retention)
    Q = [mq("M1", "1"),                                                                 # F13: read-only, clean
         mq("M2", "2", family="pdf", status="supported", signed=True),                 # signed
         mq("M3", "3", family="jpeg", status="supported", hard=True),                   # hard-linked
         mq("M4", "4", family="jpeg", hard=None),                                       # link count unknown
         mq("M5", "1", fld="title", status="supported"),                                # a (hypothetically) certified writer
         mq("M6", "1", fld="keywords", status="experimental", state="unsupported"),     # no partial credit
         mq("M7", "4", fld="description", family="jpeg", state="unsupported"),         # a clear
         mq("M8", "1", fld="copyright", state="no_value"),                               # left unchanged
         mq("M9", "2", fld="title", family="pdf", state="unknown")]                     # nobody has decided anything
    D = [mdec("E1", "desired_source_metadata_set", "M1", "Thomas"), mdec("E2", "desired_source_metadata_set", "M2", "Thomas"),
         mdec("E3", "desired_source_metadata_set", "M3", "Thomas"), mdec("E4", "desired_source_metadata_set", "M4", "Thomas"),
         mdec("E5", "desired_source_metadata_set", "M5", "Q3 report"), mdec("E6", "desired_source_metadata_set", "M6", ["tax"]),
         mdec("E7", "desired_source_metadata_clear", "M7"), mdec("E8", "leave_source_metadata_unchanged", "M8"),
         mdec("E9", "project_metadata_set", "6", {"field": "tags", "value": ["tax", "2024"]}, target_kind=LOCATION),
         mdec("E10", "project_metadata_set", "1", {"field": "owner", "value": "Thomas"}, target_kind=LOCATION)]
    return L, retention, Q, D


def test_plan_items():
    section("2c. The Plan Builder's Update Source Metadata items (necessities 8, 9, 10)")
    L, retention, Q, D = _plan_model()
    ev0, proj0, rt0 = routed(L, retention)
    draft0 = PL.build(ev0, proj0, rt0)
    ev, proj, rt = routed(L, retention)
    with_metadata(ev, Q, D)
    draft = PL.build(ev, proj, rt)
    m = meta_items(draft)
    check("one item per active desired set / clear (M1-M7); none for Leave unchanged (M8), none for an undecided question "
          "(M9), none for project metadata", sorted(m) == ["M1", "M2", "M3", "M4", "M5", "M6", "M7"], str(sorted(m)))
    i1 = m["M1"]
    check("F13's shape: blocked_reason 'writer_unsupported' AND state 'unsupported_action' -- two fields, two values",
          (i1.blocked_reason, i1.state) == ("writer_unsupported", "unsupported_action"))
    check("...the item is update_source_metadata on the file location, from its question and its decision, logical intent only",
          i1.action_kind == META and (i1.target_kind, i1.target_ref) == (LOCATION, "1") and i1.sources == ((MQ, "M1"),)
          and i1.decision_ids == ("E1",) and i1.desired_effect == {"effect": "metadata_value", "field": "creator",
                                                                   "operation": "set", "value": "Thomas", "path": r"C:\D\report.docx"})
    check("...its evidence binding is Build 1's ids-only location shape plus the question it rests on",
          i1.evidence_binding["file_path_id"] == 1 and i1.evidence_binding["question"] == {
              "question_id": "M1", "semantic_field": "creator", "analyzer_result_ids": ["1"], "observed_state": "known"})
    check("a signed file: blocked (a hard fail), reason signed_or_certified -- even with a 'supported' writer",
          (m["M2"].state, m["M2"].blocked_reason) == (BLOCKED, "signed_or_certified") and m["M2"].condition("writer_supported").result == "pass")
    check("a hard-linked file: blocked, reason hardlinked_generic_write", (m["M3"].state, m["M3"].blocked_reason) == (BLOCKED, "hardlinked_generic_write"))
    check("an unresolved link count: blocked on unknown evidence (P3-A91), and the writer is still recorded unsupported",
          (m["M4"].state, m["M4"].blocked_reason) == (BLOCKED_EV, "source_not_hardlinked")
          and m["M4"].condition("writer_supported").result == "unsupported")
    check("the check reads the record: a field whose status were 'supported', on a clean file, would be ready",
          m["M5"].state == READY and m["M5"].condition("writer_supported").params["capability_status"] == "supported")
    check("'experimental' earns no partial credit: unsupported", (m["M6"].state, m["M6"].blocked_reason) == (UNSUPPORTED, "writer_unsupported"))
    check("a clear plans as a clear (operation 'clear', no value) -- the semantic clear, never an empty string",
          m["M7"].desired_effect["operation"] == "clear" and "value" not in m["M7"].desired_effect)
    check("every metadata item declares the live checks for Phase 4 and the postcondition desired_metadata_observed",
          all({c.key for c in i.preconditions if c.stage == REG.STAGE_LIVE} >= {"target_exists", "rollback_ready", "permissions_sufficient"}
              and "desired_metadata_observed" in i.postconditions for i in m.values()))
    n = len(draft0.items)
    check("the retention items are exactly Build 8's: same numbers, same payload -- metadata items come after",
          PL.payload(draft.items[:n]) == PL.payload(draft0.items) and all(i.item_no > n for i in m.values())
          and [i.item_no for i in draft.items] == list(range(1, len(draft.items) + 1)))
    s = draft.summary
    check("the preview counts them, blocks them, claims no bytes for them, and says why", s["actions"][META] == 7
          and s["plan_eligible_reclaimable_bytes"] == draft0.summary["plan_eligible_reclaimable_bytes"]
          and s["blocked"] == draft0.summary["blocked"] + 6
          and any("Update Source Metadata -- planned only" in x for x in draft.lines())
          and REG.PREVIEW_DISCLOSURE in draft.lines())
    check("Leave unchanged and project metadata are counted as never plan items, with the project label's words",
          draft.excluded.get("left_unchanged") == 1 and draft.excluded.get("project_metadata") == 2
          and any(x.startswith("Never plan items: 1 metadata field(s) you chose to leave unchanged; 2 project metadata")
                  for x in draft.lines()))

    # Necessity 10, on its own: neither kind ever makes an item, of any action kind.
    ev, proj, rt = routed(L, [])
    with_metadata(ev, Q, [d for d in D if d.kind in ("project_metadata_set", "leave_source_metadata_unchanged")])
    only = PL.build(ev, proj, rt)
    check("necessity 10: project_metadata_set + leave_source_metadata_unchanged alone -> zero plan items of any kind",
          only.items == [] and only.summary["items"] == 0)
    ev, proj, rt = routed(L, retention)
    with_metadata(ev, Q, [d for d in D if d.kind in ("project_metadata_set", "leave_source_metadata_unchanged")])
    beside = PL.build(ev, proj, rt)
    check("...beside retention decisions: exactly Build 8's items and fingerprint, nothing added (project metadata even on "
          "the copy being removed)", PL.fingerprint(beside.items) == PL.fingerprint(draft0.items))
    # Supersession within the one domain.
    for order, want in ((("desired_source_metadata_set", "leave_source_metadata_unchanged"), False),
                        (("leave_source_metadata_unchanged", "desired_source_metadata_set"), True),
                        (("desired_source_metadata_set", "desired_source_metadata_clear"), True)):
        ds = [mdec("E1", order[0], "M1", "Thomas" if order[0].endswith("set") else True, seq=1),
              mdec("E2", order[1], "M1", "Thomas" if order[1].endswith("set") else True, seq=2, supersedes="E1")]
        ev, proj, rt = routed(L, [])
        with_metadata(ev, Q, ds)
        items = meta_items(PL.build(ev, proj, rt))
        check(f"{order[0]} superseded by {order[1]}: " + ("an item, from the later decision" if want else "no item"),
              (("M1" in items) == want) and (not want or items["M1"].decision_ids == ("E2",)))
    ev, proj, rt = routed(L, [])
    with_metadata(ev, Q, [mdec("E1", "desired_source_metadata_set", "M1", "Thomas", withdrawn=True)])
    check("a withdrawn desired value plans nothing", not meta_items(PL.build(ev, proj, rt)))
    # Unknown never becomes a clear.
    unknowns = [mq(f"U{i}", "1", fld=f, state=s) for i, (f, s) in enumerate(
        (("creator", "unknown"), ("title", "no_value"), ("keywords", "unsupported"), ("description", "unavailable")))]
    ev, proj, rt = routed(L, [])
    with_metadata(ev, unknowns, [])
    d = PL.build(ev, proj, rt)
    rv = MD.review(ev)
    check("P3-A111: questions observed unknown / no value / unsupported / unavailable, nobody deciding -> no clear item, no "
          "decision manufactured, each shown Undecided", d.items == [] and ev.metadata.decisions == []
          and [r.review_state for r in rv] == [MD.UNDECIDED] * 4 and all(r.desired_value is None for r in rv))


def test_review_and_obsolescence():
    section("2d. The review data, the Metadata Review queue, and obsolescence of a metadata item")
    L, retention, Q, D = _plan_model()
    ev, proj, rt = routed(L, retention)
    conflict = mq("MC", "4", fld="title", family="jpeg", state="conflicting", values=("Sunset", "Dusk"))
    with_metadata(ev, Q + [conflict], D)
    rows = {r.question_id: r for r in MD.review(ev)}
    r1 = rows["M1"]
    check("a review row carries what a surface needs (R8 §51): value-state, value, capability, writer, blockers, the "
          "current decision and the desired value", (r1.observed_state, r1.observed_value, r1.capability_status, r1.writer,
                                                     r1.decision_kind, r1.desired_value, r1.review_state)
          == ("known", "Alice", "read_only", "unsupported", "desired_source_metadata_set", "Thomas", MD.DESIRED))
    check("...and says it is desired source metadata, never written in Phase 3", "nothing is written" in r1.label)
    check("blockers and unknowns per question, answered with no plan", rows["M2"].blocked_by_evidence == ["signed_or_certified"]
          and rows["M4"].integrity_unknown == [MD.HARDLINK_KEY])
    check("a conflicting observation: both values listed, no single value, nothing decided for the person",
          rows["MC"].observed_state == "conflicting" and rows["MC"].observed_value is None
          and set(rows["MC"].observed_values) == {"Sunset", "Dusk"} and rows["MC"].review_state == MD.UNDECIDED)
    queue = {r.question_id for r in MD.queue(ev)}
    check("the Metadata Review queue: the undecided (M9, the conflict) -- not the decided, not Left Unchanged (R8 §53)",
          queue == {"M9", "MC"}, str(sorted(queue)))
    # Material change: the question's evidence moves after the decisions were recorded.
    bound = {"observation_id": "obs-1", "analyzer_result_ids": ["1"], "observed_state": "known"}
    D2 = [mdec("E1", "desired_source_metadata_set", "M1", "Thomas", binding=bound),
          mdec("E8", "leave_source_metadata_unchanged", "M8", binding=dict(bound, observed_state="no_value"))]
    moved = [replace(Q[0], observation_id="obs-1b", analyzer_result_ids=("7",), observed_value="Carol"),
             replace(Q[7], observation_id="obs-1b", analyzer_result_ids=("7",))]
    ev, proj, rt = routed(L, [])
    with_metadata(ev, moved, D2)
    rows = {r.question_id: r for r in MD.review(ev)}
    check("the file observed again: a desired value and a Left Unchanged both need revalidation, and both return to the queue",
          rows["M1"].review_state == rows["M8"].review_state == MD.NEEDS_REVALIDATION
          and {r.question_id for r in MD.queue(ev)} == {"M1", "M8"} and "observed again" in rows["M8"].drift[0])
    same = [replace(Q[0], analyzer_result_ids=("1",)), replace(Q[7], analyzer_result_ids=("1",))]
    ev2, _p, _r = routed(L, [])
    with_metadata(ev2, same, [mdec("E1", "desired_source_metadata_set", "M1", "Thomas", binding=bound),
                              mdec("E8", "leave_source_metadata_unchanged", "M8", binding=dict(bound, observed_state="no_value"))])
    check("nothing moved: no drift, Left Unchanged stays out of the queue", not MD.queue(ev2))
    item = meta_items(PL.build(ev, proj, rt))["M1"]
    check("a plan item on a drifted decision: decision_current fails -- blocked, reason decision_current",
          item.condition("decision_current").result == "fail" and (item.state, item.blocked_reason) == (BLOCKED, "decision_current"))

    # Obsolescence of a locked metadata item: its own dependencies only.
    ev, proj, rt = routed(L, retention)
    with_metadata(ev, Q, D)
    locked = PL.build(ev, proj, rt).items

    def reasons(questions=Q, decisions=D):
        e, p, r = routed(L, retention)
        with_metadata(e, questions, decisions)
        return [c for c, _w, _d in PL.obsolescence(locked, e, p, r)]
    check("unchanged: it still stands", reasons() == [])
    check("an unrelated metadata decision (another question, project metadata on another file) does not obsolete it",
          reasons(Q + [mq("MX", "3", fld="title")], D + [mdec("E20", "desired_source_metadata_set", "MX", "Other"),
                                                         mdec("E21", "project_metadata_set", "3", {"field": "note", "value": "n"},
                                                              target_kind=LOCATION)]) == [])
    check("its desired value withdrawn -> source decision changed",
          reasons(decisions=[replace(d, withdrawn=True) if d.decision_id == "E1" else d for d in D]) == ["decision_changed"])
    check("the capability record changed (a writer certified, say) -> metadata capability changed",
          reasons([replace(q, capability=cap("ooxml", "creator", "supported")) if q.question_id == "M1" else q for q in Q])
          == ["capability_changed"])
    check("the integrity evidence changed (found signed) -> integrity evidence changed",
          reasons([replace(q, signed_or_certified=True) if q.question_id == "M1" else q for q in Q]) == ["integrity_changed"])
    check("re-read by an analyzer -> evidence changed", reasons(
        [replace(q, analyzer_result_ids=("99",)) if q.question_id == "M1" else q for q in Q]) == ["evidence_changed"])


# ---------------------------------------------------------------------------
# 3-4. A real project
# ---------------------------------------------------------------------------

def _docx(path, author, title=None, text="Body text."):
    import docx
    d = docx.Document()
    d.add_paragraph(text)
    d.core_properties.author = author
    d.core_properties.title = title or ""
    d.save(str(path))


def _pdf(path, author, title, pages=1):
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    w.add_metadata({"/Author": author, "/Title": title})
    with open(path, "wb") as f:
        w.write(f)


def build_corpus(base: Path):
    docs, photos = base / "Docs", base / "Photos"
    docs.mkdir(parents=True)
    photos.mkdir(parents=True)
    _docx(docs / "report.docx", "Alice", "Q3 report")
    _docx(docs / "memo.docx", "", None, "A memo with no author.")
    _pdf(docs / "paper.pdf", "Bob", "Paper", pages=1)
    _pdf(docs / "linked.pdf", "Dana", "Linked", pages=3)
    os.link(docs / "linked.pdf", docs / "linked_alias.pdf")         # a real NTFS hard link: one file, two names
    PC.T._photo(5).save(photos / "photo.jpg", quality=90)
    shutil.copyfile(photos / "photo.jpg", photos / "photo_copy.jpg")
    PC.T._photo(6, size=(500, 300)).save(photos / "unique.jpg", quality=85)
    return docs, photos


def test_project(tmp: Path):
    section("3. A real project: Phase 1/2 readings, the store, isolation, the plan, the §4.1 lock, obsolescence, drift")
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, DUPLICATES, ANALYSIS
    from Phase2.core import connect
    from Phase3 import evidence as E, store as S

    app_root = tmp / "MetaRoot"
    (app_root / "Projects").mkdir(parents=True)
    corpus = tmp / "MetaCorpus"
    docs, photos = build_corpus(corpus)
    project_dir = app_root / "Projects" / "Meta"
    outs = [RunWorker(app_root, None, RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(docs), str(photos)], project_name="Meta")).run()]
    outs.append(RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run())
    outs.append(RunWorker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze documents", analyzer_keys=["pdf", "office"])).run())
    check("Pre-Scan, Find My Duplicates, and the real PDF and Office analyzers through the worker", all(o.ok for o in outs),
          str([(o.status, o.message) for o in outs]))
    before = BASE.fingerprint_tree(corpus)
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:meta")
    by = {r[1].replace("/", "\\").rsplit("\\", 1)[-1]: str(r[0]) for r in conn.execute(
        "SELECT fp.file_path_id, fp.relative_path FROM file_path fp")}
    links = {n: conn.execute("SELECT hard_link_count FROM file_state WHERE file_path_id = ?", (int(i),)).fetchone()[0]
             for n, i in by.items()}

    # §0: what Phase 1/2 already exposes.
    author = conn.execute(
        "SELECT ar.author FROM analyzer_result ar JOIN file_state fs ON fs.current_observation_id = ar.file_observation_id "
        " WHERE fs.file_path_id = ? AND ar.status = 'analyzed'", (int(by["report.docx"]),)).fetchone()
    check("§0: Phase 1/2 already exposes observed metadata -- analyzer_result.author on the current observation says 'Alice'",
          author is not None and author[0] == "Alice")
    check("the real link counts: 2 for both names of the hard link; known (1) where the size collides; the rest as the "
          "scan left them", links["linked.pdf"] == 2 and links["linked_alias.pdf"] == 2 and links["photo.jpg"] == 1
          and links["photo_copy.jpg"] == 1, str(links))

    with PC.ReadGuard(corpus) as reads, BASE.WriteGuard([project_dir, app_root / "Logs"]) as writes:
        lm = {q.semantic_field: q for q in E.location_metadata(conn, by["report.docx"])}
        memo = {q.semantic_field: q for q in E.location_metadata(conn, by["memo.docx"])}
        paper = {q.semantic_field: q for q in E.location_metadata(conn, by["paper.pdf"])}
        photo = {q.semantic_field: q for q in E.location_metadata(conn, by["photo.jpg"])}
        unique = {q.semantic_field: q for q in E.location_metadata(conn, by["unique.jpg"])}
        linked = {q.semantic_field: q for q in E.location_metadata(conn, by["linked.pdf"])}
    check("report.docx: Creator known 'Alice', Title known 'Q3 report' -- read from the Office analyzer's own result, its "
          "id bound", (lm["creator"].observed_state, lm["creator"].observed_value, lm["title"].observed_value)
          == ("known", "Alice", "Q3 report") and len(lm["creator"].analyzer_result_ids) == 1)
    check("...Keywords and Description: unsupported (no analyzer reads them) -- not 'unknown', not 'no value'",
          lm["keywords"].observed_state == lm["description"].observed_state == "unsupported")
    check("memo.docx, saved with an empty author: Creator 'no_value' -- not 'unknown'", memo["creator"].observed_state == "no_value")
    check("paper.pdf: Creator 'Bob' from the PDF analyzer", (paper["creator"].observed_state, paper["creator"].observed_value) == ("known", "Bob"))
    check("photo.jpg: no analyzer reads a JPEG's creator -- unsupported", photo["creator"].observed_state == "unsupported")
    check("the capability that applies is visible per question (ooxml creator read_only; jpeg creator unknown)",
          (lm["creator"].capability.status, photo["creator"].capability.status) == ("read_only", "unknown"))
    check("integrity from evidence: linked.pdf hard-linked (true); photo.jpg a single name (false); a unique size leaves it "
          "unknown exactly when the scan left the link count empty; signatures unknown for Office and PDF (no Phase 1/2 "
          "evidence), not applicable for JPEG", linked["creator"].hardlinked is True and photo["creator"].hardlinked is False
          and unique["creator"].hardlinked is (None if links["unique.jpg"] is None else links["unique.jpg"] > 1)
          and lm["creator"].signed_or_certified is None and photo["creator"].signed_or_certified is None
          and MD.integrity(photo["creator"])[MD.SIGNED_KEY][0] == "not_applicable"
          and MD.integrity(lm["creator"])[MD.SIGNED_KEY][0] == "unknown")
    check("listing a file's metadata asks nothing: no question row, no operation", st.counts()["metadata_questions"] == 0
          and st.counts()["operations"] == 0 and lm["creator"].question_id is None)
    check("...and read no source file to answer (the values are Phase 1/2's rows), writing nothing anywhere",
          not reads.hits and not writes.violations, str(reads.hits[:2] + writes.violations[:2]))

    # The store: refusals first -- nothing written by any of them.
    check("refused: a date that is not a date", refused(lambda: st.record_metadata_decision(
        "desired_source_metadata_set", by["paper.pdf"], "content_created", "yesterday")))
    check("refused: a keyword list that is not a list", refused(lambda: st.record_metadata_decision(
        "desired_source_metadata_set", by["paper.pdf"], "keywords", "tax")))
    check("refused: an empty desired value ('record Clear')", refused(lambda: st.record_metadata_decision(
        "desired_source_metadata_set", by["report.docx"], "creator", "")))
    check("refused: an unknown field, and a project field on the source side", refused(lambda: st.record_metadata_decision(
        "desired_source_metadata_set", by["report.docx"], "shoe_size", "9")) and refused(lambda: st.record_metadata_decision(
            "desired_source_metadata_set", by["report.docx"], "note", "x")))
    check("refused: a metadata kind on the wrong target, and a question that does not exist",
          refused(lambda: st.record_decision("desired_source_metadata_set", LOCATION, by["report.docx"], "Thomas"))
          and refused(lambda: st.record_decision("leave_source_metadata_unchanged", MQ, "99999")))
    check("refused: project metadata with an unregistered field or a bad value", refused(lambda: st.record_metadata_decision(
        "project_metadata_set", by["report.docx"], value={"field": "review_priority", "value": "urgent"})))
    check("nothing was written by the refusals", st.counts() == {"operations": 0, "decisions": 0, "withdrawals": 0,
                                                                  "policy_versions": 0, "review_events": 0, "batches": 0,
                                                                  "metadata_questions": 0}, str(st.counts()))

    # Baseline of the duplicate / pair model and the detector, before any metadata decision.
    red = st.record_decision("redundant_location", LOCATION, by["photo_copy.jpg"])
    keep = st.record_decision("must_keep_location", LOCATION, by["photo.jpg"])
    RT.reconcile(conn, st)

    def retention_view():
        ev = E.load_evidence(conn)
        proj = R.resolve_all(ev)
        rt = RT.route_all(ev, proj, "2026-09-26T00:00:00Z")
        return json.dumps([g.as_dict() for g in proj.groups] + [list(proj.orphaned_decisions), proj.totals]
                          + [r.as_dict() for r in rt.groups.values()] + [rt.counts], sort_keys=True, default=str), ev
    base_view, _ev = retention_view()
    events_before = conn.execute("SELECT COUNT(*) FROM p3_review_event").fetchone()[0]

    with PC.ReadGuard(corpus) as reads2, BASE.WriteGuard([project_dir, app_root / "Logs"]) as writes2:
        r1 = st.record_metadata_decision("desired_source_metadata_set", by["report.docx"], "creator", "Thomas", rationale="new owner")
        ops = conn.execute("SELECT kind FROM p3_operation WHERE operation_id = ?", (r1["operation_id"],)).fetchone()[0]
        qrow = conn.execute("SELECT * FROM p3_metadata_question WHERE question_id = ?", (r1["question_id"],)).fetchone()
        drow = conn.execute("SELECT * FROM p3_decision WHERE decision_id = ?", (r1["decision_id"],)).fetchone()
        r2 = st.record_metadata_decision("leave_source_metadata_unchanged", by["report.docx"], "creator")
        r3 = st.record_metadata_decision("desired_source_metadata_set", by["report.docx"], "creator", "Thomas")
        r_title = st.record_metadata_decision("leave_source_metadata_unchanged", by["report.docx"], "title")
        r_clear = st.record_metadata_decision("desired_source_metadata_clear", by["memo.docx"], "creator")
        r_date = st.record_metadata_decision("desired_source_metadata_set", by["paper.pdf"], "content_created", "2024-05-01")
        r_ptitle = st.record_metadata_decision("leave_source_metadata_unchanged", by["paper.pdf"], "title")
        r_link = st.record_metadata_decision("desired_source_metadata_set", by["linked.pdf"], "creator", "Thomas")
        r_photo = st.record_metadata_decision("desired_source_metadata_set", by["photo.jpg"], "creator", "Thomas")
        r_tags = st.record_metadata_decision("project_metadata_set", by["photo_copy.jpg"], value={"field": "tags", "value": ["trip", "2024"]})
        r_owner = st.record_metadata_decision("project_metadata_set", by["report.docx"],
                                              value={"field": "owner", "value": "Thomas"}, rationale="R8 §54: the file keeps Alice")
    binding = json.loads(drow["evidence_binding_json"])
    check("one operation named the question AND recorded the decision (kind 'decision'); the decision targets "
          "metadata_question with the question's id", ops == "decision" and qrow["operation_id"] == r1["operation_id"]
          and drow["target_kind"] == MQ and drow["target_ref"] == str(r1["question_id"]) and qrow["semantic_field"] == "creator"
          and str(qrow["file_path_id"]) == by["report.docx"])
    check("its evidence binding is ids only: the question, the file's current observation, the analyzer result the value "
          "came from, and the value-state word", set(binding) == {"schema", "target_kind", "question_id", "file_path_id",
                                                                 "semantic_field", "observation_id", "analyzer_result_ids",
                                                                 "observed_state"}
          and binding["analyzer_result_ids"] == [int(x) for x in lm["creator"].analyzer_result_ids] and binding["observed_state"] == "known"
          and "Alice" not in json.dumps(binding))
    hist = st.history_for(MQ, r1["question_id"])
    check("set -> leave unchanged -> set again: one question, one domain, each superseding the last; the rows all kept",
          r2["question_id"] == r3["question_id"] == r1["question_id"] and r2["supersedes_decision_id"] == r1["decision_id"]
          and r3["supersedes_decision_id"] == r2["decision_id"] and [h["active"] for h in hist] == [False, False, True])
    check("project metadata is a decision on the file location itself (no question); a new note would supersede only a note",
          r_tags["question_id"] is None and conn.execute("SELECT target_kind FROM p3_decision WHERE decision_id = ?",
                                                         (r_tags["decision_id"],)).fetchone()[0] == LOCATION)
    check("recording read no source file and wrote nothing outside the project", not reads2.hits and not writes2.violations,
          str(reads2.hits[:2] + writes2.violations[:2]))

    # Isolation: no metadata decision is retention or review intent.
    after_view, ev = retention_view()
    counts = RT.reconcile(conn, st)
    check("the duplicate and pair model is byte-for-byte what it was before any metadata decision (project metadata sits on "
          "the redundant copy itself); none is an orphaned decision", after_view == base_view)
    check("the routing detector writes nothing for them", counts.get("written") == 0
          and conn.execute("SELECT COUNT(*) FROM p3_review_event").fetchone()[0] == events_before, str(counts))
    check("the loader keeps them apart: every metadata row in ev.metadata.decisions, none in ev.decisions",
          all(d.kind not in REG.METADATA_DECISION_KINDS for d in ev.decisions)
          and {d.kind for d in ev.metadata.decisions} == set(REG.METADATA_DECISION_KINDS))
    pm = MD.project_metadata(ev)
    check("the project's own metadata reads back, labelled as stored in the project (R8 §54: Owner Thomas while the file "
          "still says Alice)", pm[by["photo_copy.jpg"]]["tags"]["value"] == ["trip", "2024"]
          and pm[by["report.docx"]]["owner"] == {"value": "Thomas", "decision_id": str(r_owner["decision_id"]),
                                                 "stored_in": REG.PROJECT_METADATA_LABEL}
          and ev.metadata.questions[str(r1["question_id"])].observed_value == "Alice")

    # The plan.
    def model():
        e = E.load_evidence(conn)
        p = R.resolve_all(e)
        return e, p, RT.route_all(e, p, e.now)
    with PC.ReadGuard(corpus) as reads3, BASE.WriteGuard([project_dir, app_root / "Logs"]) as writes3:
        ev, proj, rt = model()
        draft = PL.build(ev, proj, rt)
        review = {(r.location_id, r.semantic_field): r for r in MD.review(ev)}
        res1 = st.record_plan_revision(draft, name="Metadata and a removal")
    m = {(i.target_ref, i.desired_effect["field"]): i for i in draft.items if i.action_kind == META}
    rm = [i for i in draft.items if i.action_kind == REMOVE]
    check("the plan: the removal of photo_copy.jpg (ready) and the five desired values as Update Source Metadata items; "
          "Leave unchanged and project metadata make none", len(rm) == 1 and rm[0].target_ref == by["photo_copy.jpg"]
          and rm[0].state == READY and sorted(m) == sorted([(by["report.docx"], "creator"), (by["memo.docx"], "creator"),
                                                              (by["paper.pdf"], "content_created"), (by["linked.pdf"], "creator"),
                                                              (by["photo.jpg"], "creator")])
          and {i.action_kind for i in draft.items} == {REMOVE, PRESERVE, META})
    ph = m[(by["photo.jpg"], "creator")]
    check("photo.jpg on real evidence has F13's shape: state unsupported_action, blocked_reason writer_unsupported "
          "(signature not applicable to JPEG; link count known: one name)", (ph.state, ph.blocked_reason) ==
          (UNSUPPORTED, "writer_unsupported") and ph.condition("source_not_hardlinked").result == "pass")
    check("linked.pdf: blocked by its real hard link (hardlinked_generic_write)",
          (m[(by["linked.pdf"], "creator")].state, m[(by["linked.pdf"], "creator")].blocked_reason) == (BLOCKED, "hardlinked_generic_write"))
    rep = m[(by["report.docx"], "creator")]
    check("report.docx: blocked on unknown evidence (whether it is signed -- Phase 1/2 records no signatures), the writer "
          "also recorded unsupported: every metadata item is blocked, none ready", (rep.state, rep.blocked_reason) ==
          (BLOCKED_EV, "source_not_signed_or_certified") and rep.condition("writer_supported").result == "unsupported"
          and all(i.state != READY for i in m.values()))
    check("the clear on memo.docx (observed no value) is a person's clear, planned as one", m[(by["memo.docx"], "creator")]
          .desired_effect["operation"] == "clear")
    check("the review data answers the same without the plan: linked.pdf blocked by evidence; report.docx Title left "
          "unchanged and out of the queue", review[(by["linked.pdf"], "creator")].blocked_by_evidence == ["hardlinked_generic_write"]
          and review[(by["report.docx"], "title")].review_state == MD.LEFT_UNCHANGED
          and (by["report.docx"], "title") not in {(r.location_id, r.semantic_field) for r in MD.queue(ev)})
    check("building and freezing read no source file and wrote nothing outside the project", not reads3.hits and not writes3.violations,
          str(reads3.hits[:2] + writes3.violations[:2]))

    # Handoff §4.1: lock a revision holding a ready removal and an F13-shaped blocked metadata item.
    with PC.ReadGuard(corpus) as reads4, BASE.WriteGuard([project_dir, app_root / "Logs"]) as writes4:
        lock = st.lock_plan_revision(res1["plan_revision_id"])
        rev, items = st.plan_revision(res1["plan_revision_id"])
    back = {(i.target_ref, i.desired_effect.get("field")): i for i in items if i.action_kind == META}
    held = back[(by["photo.jpg"], "creator")]
    without = [i for i in items if i is not held]
    check("§4.1: the revision LOCKS with the blocked metadata item in it (Build 8's judgment call holds for an item no "
          "evidence refresh can unblock)", rev["status"] == "locked" and rev["locked"] == 1 and lock["fingerprint"] == rev["fingerprint"])
    check("§4.1: after the lock the item is still there, still blocked: unsupported_action / writer_unsupported",
          (held.state, held.blocked_reason, held.action_kind) == (UNSUPPORTED, "writer_unsupported", META)
          and any(i.action_kind == REMOVE and i.state == READY for i in items))
    check("§4.1: the fingerprint is the SHA-256 of the full payload INCLUDING the blocked item -- recomputed from the rows "
          "it matches; without the item it would not", PL.fingerprint(items) == rev["fingerprint"] == PL.fingerprint(draft.items)
          and PL.fingerprint(without) != rev["fingerprint"]
          and any(x["action"] == META and x["state"] == UNSUPPORTED and x["blocked"] == "writer_unsupported"
                  for x in PL.payload(items)["items"]))
    locked_event = [e for e in rev["events"] if e["event_kind"] == "locked"][0]
    check("§4.1: the lock event's dependency set names the metadata decision; the dependency index finds the revision from it",
          str(r_photo["decision_id"]) in locked_event["detail"]["dependencies"]["decisions"]
          and st.revisions_depending_on([r_photo["decision_id"]]) == [res1["plan_revision_id"]])
    check("locking read no source file", not reads4.hits and not writes4.violations)

    # Obsolescence through the existing detector.
    st.record_metadata_decision("project_metadata_set", by["unique.jpg"], value={"field": "note", "value": "keep for now"})
    st.record_metadata_decision("leave_source_metadata_unchanged", by["unique.jpg"], "title")
    with PC.ReadGuard(corpus) as reads5, BASE.WriteGuard([project_dir, app_root / "Logs"]) as writes5:
        RT.reconcile(conn, st)
    check("an unrelated change (project metadata and a Leave unchanged elsewhere) does not obsolete it",
          st.plan_revision(res1["plan_revision_id"])[0]["status"] == "locked")
    st.withdraw(r_photo["decision_id"], reason="changed my mind")
    RT.reconcile(conn, st)
    rev2, items2 = st.plan_revision(res1["plan_revision_id"])
    ob = [e for e in rev2["events"] if e["event_kind"] == "obsolete"]
    check("its desired value withdrawn: the detector marks it obsolete, 'source decision changed' -- items and fingerprint "
          "untouched (P3-A97)", rev2["status"] == "obsolete" and ob and ob[0]["detail"]["reason"] == "source decision changed"
          and rev2["fingerprint"] == rev["fingerprint"] and PL.fingerprint(items2) == rev["fingerprint"])
    check("the detector read no source file", not reads5.hits and not writes5.violations)

    # Drift: report.docx edited on disk and rescanned; analyzed again.
    _docx(docs / "report.docx", "Carol", "Q3 report (revised)", "Body text, revised and longer than before.")
    outs = [RunWorker(app_root, project_dir, RunRequest(PRESCAN, "Pre-Scan")).run(),
            RunWorker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze documents", analyzer_keys=["office"])).run()]
    check("rescanned and analyzed again", all(o.ok for o in outs), str([(o.status, o.message) for o in outs]))
    conn.close()
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:meta2")
    ev, proj, rt = model()
    rows = {(r.location_id, r.semantic_field): r for r in MD.review(ev)}
    rc, rt_ = rows[(by["report.docx"], "creator")], rows[(by["report.docx"], "title")]
    check("the question now reads the new value ('Carol') from the new analysis", (rc.observed_state, rc.observed_value) == ("known", "Carol"))
    check("its desired value needs revalidation, and so does its Left Unchanged Title -- both back in the queue (R8 §53: "
          "the evidence materially changed)", rc.review_state == rt_.review_state == MD.NEEDS_REVALIDATION
          and {(by["report.docx"], "creator"), (by["report.docx"], "title")} <= {(r.location_id, r.semantic_field) for r in MD.queue(ev)})
    check("paper.pdf's Left Unchanged Title, untouched, stays out of the queue",
          rows[(by["paper.pdf"], "title")].review_state == MD.LEFT_UNCHANGED)
    item = {(i.target_ref, i.desired_effect["field"]): i for i in PL.build(ev, proj, rt).items if i.action_kind == META}[
        (by["report.docx"], "creator")]
    check("a new plan's item on it fails decision_current (blocked) -- the Plan Builder never re-decides it",
          item.condition("decision_current").result == "fail" and item.state == BLOCKED)
    journal = tmp / "journal.txt"
    S.export_decision_journal(conn, str(journal))
    text = journal.read_text(encoding="utf-8")
    check("the decision journal lists the questions and the metadata decisions", f"metadata question #{r1['question_id']}: creator" in text
          and "desired_source_metadata_set on metadata_question" in text and "project_metadata_set on file_location" in text)
    conn.close()
    after = BASE.fingerprint_tree(corpus)
    edited = {str(docs / "report.docx")}
    check("every corpus file untouched but the one the test edited (no metadata was written anywhere)",
          {k: v for k, v in after["files"].items() if k not in edited} == {k: v for k, v in before["files"].items() if k not in edited}
          and after["everything"] == before["everything"])
    return app_root, corpus, project_dir


# ---------------------------------------------------------------------------
# 5. The window
# ---------------------------------------------------------------------------

def test_gui(tmp: Path):
    section("5. The Plan page shows metadata items with their reasons")
    try:
        import tkinter as tk
        probe = tk.Tk()
        probe.withdraw()
        probe.destroy()
    except Exception as exc:                                        # noqa: BLE001
        print(f"  SKIP  no display ({exc})")
        return
    from tkinter import messagebox
    from Phase2 import gui
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, DUPLICATES, ANALYSIS
    from Phase3 import review as RV

    app_root = tmp / "MetaGuiRoot"
    (app_root / "Projects").mkdir(parents=True)
    corpus = tmp / "MetaGuiCorpus"
    docs, photos = build_corpus(corpus)
    project_dir = app_root / "Projects" / "MetaGui"
    RunWorker(app_root, None, RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(docs), str(photos)], project_name="MetaGui")).run()
    RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run()
    RunWorker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze documents", analyzer_keys=["pdf", "office"])).run()
    before = BASE.fingerprint_tree(corpus)
    asked = []
    real = {n: getattr(messagebox, n) for n in ("askyesno", "showinfo", "showwarning", "showerror")}
    messagebox.askyesno = lambda *a, **k: (asked.append(("yesno", a[0] if a else "")), True)[1]
    for n in ("showinfo", "showwarning", "showerror"):
        setattr(messagebox, n, lambda *a, _n=n, **k: asked.append((_n, a[0] if a else "", a[1] if len(a) > 1 else "")))
    try:
        with PC.ReadGuard(corpus) as reads, BASE.WriteGuard([project_dir, app_root / "Logs", gui.APP_ROOT / "Logs"]) as guard:
            app = gui.Phase2App(None)
            app.withdraw()
            app.open_project(project_dir)
            st = RV.store_for(app)
            by = {r[1].replace("/", "\\").rsplit("\\", 1)[-1]: str(r[0]) for r in app.conn.execute(
                "SELECT file_path_id, relative_path FROM file_path")}
            r_photo = st.record_metadata_decision("desired_source_metadata_set", by["photo.jpg"], "creator", "Thomas")
            st.record_metadata_decision("project_metadata_set", by["report.docx"], value={"field": "note", "value": "check"})
            app.show_plan()
            app.update()
            page = app.plan_page
            page.name_var.set("Metadata")
            page.build_revision()
            app.update()
            text = page.preview_text.get("1.0", "end")
            rows = {page.items_tree.item(i, "values")[2]: (i, page.items_tree.item(i, "values")) for i in page.items_tree.get_children()}
            check("the preview counts the Update Source Metadata item and says why it cannot run, and that project metadata "
                  "is never a plan item", "Update source metadata" in text and "Update Source Metadata -- planned only" in text
                  and "1 project metadata value(s)" in text)
            iid, vals = rows.get("photo.jpg", (None, ()))
            check("the item row: Update source metadata on photo.jpg, its state and reason, and the writer's words",
                  vals and vals[1] == "Update source metadata" and "writer_unsupported" in vals[3]
                  and "no certified source-metadata writer" in vals[4], str(vals))
            page.items_tree.selection_set(iid)
            page._item_chosen()
            detail = page.detail_text.get("1.0", "end")
            check("the item's detail: from which metadata question, the desired value beside the observed state, the "
                  "decision, and every check", "From: metadata question #" in detail and "Desired: creator = 'Thomas'" in detail
                  and "observed now: unsupported" in detail and f"#{r_photo['decision_id']}" in detail
                  and "[unsupported] writer_supported" in detail and "[not_applicable] source_not_signed_or_certified" in detail)
            page.lock()
            app.update()
            check("Lock revision: locked, with the blocked metadata item still listed", page.rev["status"] == "locked"
                  and "photo.jpg" in [page.items_tree.item(i, "values")[2] for i in page.items_tree.get_children()])
            app.destroy()
        check("the page read no source file", not reads.hits, str(reads.hits[:3]))
        check("no Python-level write outside the project and log folders", not guard.violations, str(guard.violations[:3]))
        check("every corpus file untouched", BASE.fingerprint_tree(corpus) == before)
        check("no error dialog", not [a for a in asked if a[0] in ("showerror", "showwarning")], str(asked[-3:]))
    finally:
        for n, fn in real.items():
            setattr(messagebox, n, fn)


# ---------------------------------------------------------------------------

def main():
    tmp = Path(tempfile.mkdtemp(prefix="p3meta_"))
    try:
        test_vocabulary(tmp)
        test_observe()
        test_capability_and_integrity()
        test_plan_items()
        test_review_and_obsolescence()
        test_project(tmp)
        test_gui(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 METADATA (Build 9): {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
