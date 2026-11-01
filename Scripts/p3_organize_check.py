#!/usr/bin/env python3
r"""B20 regression: organizing intent -- the minimum move / rename set (P3.R12, accepted with required changes).

Fixtures F17-F29 and F31-F41 of the research (F30, the template case, is deferred), run through the PRODUCT'S
OWN code: the registry and store (record-time validation), the evidence loader, the Plan Builder, bulk, the
revalidation detector -- never the lab's reference evaluator. The research lab (F01-F16, 16/16) is not
changed here. The Model JSON's setups are the oracles where it has them; where the evaluation corrected the
research (F23, F25, F26, F27, F28), the corrected expectation is the one asserted.

Two kinds of world:

  a REAL PROJECT   a small corpus scanned by Phase 1 and 2 (the worker), decisions recorded through the store,
                   plans built from the database: the record-time gates (F28, F38, F41), supersession and
                   withdrawal (F18), the frozen batch and its undo (F19, F40), removal vs move (F22), protection and
                   the scoped override (F23, F31), companions (F24, F37), chains and swaps (F25, F33), revalidation by
                   rescan (F26), a locked plan made obsolete (F29), a keeper moved (F32), a junction (F34),
                   dangling protection (F36), metadata beside a move (F35), and the zero-read guard (F17);
  a HAND-BUILT     model + ScopeFacts, for the evidence conditions a real disk cannot be made to show cheaply: an
  MODEL            unusable walk, an inaccessible or truncated folder, another volume (F21, F27, F34).

F39 (the regression: no organize decision -> B19's items and fingerprint, byte for byte) runs against the archived
phase3-b19 code in its own process (p3_organize_regression_driver below; a clean checkout of the tag is passed as
argv[1] -- without it that section is skipped with a note, never passed silently).

Run:  python Scripts\p3_organize_check.py [<path to a clean checkout of tag phase3-b19>]
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                                   # noqa: BLE001
    pass

import p3_dangling_check as DC                                      # noqa: E402  (corpus scan / model helpers)
import p3_plan_check as PC                                          # noqa: E402  (ReadGuard)
BASE = PC.BASE                                                      # noqa: E402  (WriteGuard, hand-built helpers)
from Phase3 import registry as REG                                  # noqa: E402
from Phase3 import resolve as R                                     # noqa: E402
from Phase3 import routing as RT                                    # noqa: E402
from Phase3 import plans as PL                                      # noqa: E402
from Phase3 import organize as OG                                   # noqa: E402
from Phase3 import scopes as SC                                     # noqa: E402
from Phase3 import bulk as BK                                       # noqa: E402
from Phase3.model import (Evidence, Location, Decision, OrganizeEvidence, RootCoverage, PolicyVersion, Group,  # noqa: E402
                          path_key)

RESULTS = []
LOCATION = REG.LOCATION
MOVE, RENAME, REMOVE, META = REG.ACTION_MOVE, REG.ACTION_RENAME, REG.ACTION_REMOVE, REG.ACTION_METADATA
BLOCKED, BLOCKED_EV, UNSUPPORTED, READY = REG.ITEM_BLOCKED, REG.ITEM_BLOCKED_EVIDENCE, REG.ITEM_UNSUPPORTED, REG.ITEM_READY
REPORT = {"fixtures": {}}


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" -- {detail}" if (detail and not ok) else ""))


def fixture(fid, name, ok):
    REPORT["fixtures"][fid] = {"name": name, "ok": bool(ok)}


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def refused(fn):
    try:
        fn()
        return None
    except ValueError as exc:
        return str(exc) or "refused"


def res(it, key, stage=REG.STAGE_EVIDENCE):
    c = it.condition(key, stage)
    return c.result if c else None


def items_by(draft):
    out = {}
    for i in draft.items:
        out[(i.action_kind, i.target_ref)] = i
    return out


def mv_value(root_id, folder, oc="fail"):
    return {"destination_root_id": root_id, "destination_folder": folder, "on_collision": oc}


def nm_value(name, confirmed=False, oc="fail"):
    return {"name": name, "on_collision": oc, "extension_change_confirmed": confirmed}


# ---------------------------------------------------------------------------
# The real project
# ---------------------------------------------------------------------------

def build_corpus(base: Path):
    r1, r2 = base / "Root1", base / "Root2"

    def put(root, rel, text=None):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((text if text is not None else f"content of {rel}").encode("utf-8") * 40)
    for rel in ("Inbox\\a.pdf", "Inbox\\b.pdf", "Inbox\\c.pdf", "Inbox\\d.pdf", "Inbox\\e.pdf", "Inbox\\f.pdf", "Inbox\\g.txt",
                "Downloads\\p1.pdf", "Downloads\\p2.pdf", "Downloads\\p3.pdf",
                "A\\x.jpg", "B\\x.jpg", "D\\y.jpg", "E\\Y.JPG", "H\\note.txt", "Legal\\c.pdf", "Misc\\keep.txt",
                "Docs\\Existing.txt", "Docs\\Old.txt", "Card\\IMG_0001.CR2", "Card\\IMG_0001.xmp", "Card\\IMG_0002.NEF",
                "Card\\IMG_0002.NEF.xmp", "Card2\\IMG_0003.CR2", "Card2\\IMG_0003.JPG", "Card2\\IMG_0003.xmp",
                "Chain\\a.txt", "Chain\\b.txt", "Swap\\x.txt", "Swap\\y.txt", "Reval\\r1.txt", "Reval\\r2.txt",
                "Reval\\r3.txt", "Reval\\r4.txt", "Meta\\m.pdf", "Cmp\\q1.txt", "Cmp\\q2.txt"):
        put(r1, rel)
    put(r1, "F\\" + unicodedata.normalize("NFC", "\u00e9.txt"))
    put(r1, "G\\" + unicodedata.normalize("NFD", "\u00e9.txt"))
    for rel in ("Dup\\k1.bin", "Dup\\k2.bin"):                    # an exact-duplicate group (a keeper and a copy)
        put(r1, rel, "duplicate-group-one")
    for rel in ("Dup2\\m1.bin", "Dup2\\m2.bin"):
        put(r1, rel, "duplicate-group-two")
    put(r1, "Outside\\far.txt")
    put(r2, "Archive\\old.txt")
    junction = False
    import subprocess
    (r1 / "Linked").mkdir()
    p = subprocess.run(["cmd", "/c", "mklink", "/J", str(r1 / "Linked" / "j"), str(r1 / "Outside")], capture_output=True, text=True)
    junction = p.returncode == 0
    return r1, r2, junction


class Project:
    """A scanned project and the handles the sections share."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.app_root = tmp / "AppRoot"
        (self.app_root / "Projects").mkdir(parents=True)
        self.r1, self.r2, self.junction = build_corpus(tmp / "Corpus")
        self.project_dir, ok, why = DC.scan(self.app_root, None, [self.r1, self.r2], "Organize")
        check("Pre-Scan and Find My Duplicates through the worker (a real corpus: 2 roots, 1 junction)", ok, why)
        self.open()
        from Phase3 import evidence as E
        self.E = E
        DC.model(self.conn, E)                       # Phase 2's current duplicate projection
        self.root1 = self.root_id(self.r1)
        self.root2 = self.root_id(self.r2)

    def open(self):
        self.conn, self.st, E = DC.open_model(self.project_dir)

    def root_id(self, path):
        row = self.conn.execute("SELECT source_root_id FROM source_root WHERE root_path = ?", (str(path),)).fetchone()
        return int(row[0])

    def f(self, *parts):
        return DC.fid(self.conn, self.E, *parts)

    def model(self):
        ev, proj, rt = DC.model(self.conn, self.E)
        return ev, proj, rt

    def plan(self):
        ev, proj, rt = self.model()
        return ev, proj, rt, PL.build(ev, proj, rt)

    def counts(self):
        return self.st.counts()

    def rescan(self):
        self.conn.close()
        _pd, ok, why = DC.scan(self.app_root, self.project_dir)
        self.open()
        DC.model(self.conn, self.E)
        return ok, why


# ---------------------------------------------------------------------------
# 1. The registry: two kinds, the scoped override
# ---------------------------------------------------------------------------

def test_registry():
    section("1. The registry: desired_location, desired_name, and the override scoped by action (RC 2)")
    k1, k2 = REG.DECISION_KINDS["desired_location"], REG.DECISION_KINDS["desired_name"]
    check("two new kinds in the ONE engine: file_location only, no resolution class, no reclaim, one click, their own domains "
          "organize_location / organize_name",
          k1.target_kinds == k2.target_kinds == frozenset({LOCATION}) and k1.resolution_class == k2.resolution_class == "none"
          and not k1.affects_reclaim and not k2.affects_reclaim and k1.conflict_domain == "organize_location"
          and k2.conflict_domain == "organize_name" and k1.confirmation == k2.confirmation == REG.CONFIRM_NONE)
    check("no new object family, target type, policy kind or plan-item kind (R12 section 4)",
          REG.TARGET_KINDS == (LOCATION, REG.GROUP, REG.VISUAL_PAIR) and len(REG.POLICY_KINDS) == 5 and len(REG.ACTION_KINDS) == 5
          and PL.PLAN_PAYLOAD_SCHEMA == "fileorganizer.p3.plan-payload/1")
    check("the registry version moved to /3 and the digest with it; the plan payload schema did not",
          REG.REGISTRY_VERSION == "fileorganizer.p3.registry/3")
    ov = REG.DECISION_KINDS["override_protection"]
    old = {"policy_id": "7", "policy_version_id": "9"}
    check("override_protection: a removal scope is stored with NO member (every row before B20 is exactly that); an organize "
          "scope is stored with it; the removal domain key is unchanged and the organize one is suffixed (a removal override "
          "and an organize override on one policy do not supersede each other)",
          ov.validate_value(old) == old and ov.validate_value(dict(old, action_scope="removal")) == old
          and ov.validate_value(dict(old, action_scope="organize")) == dict(old, action_scope="organize")
          and ov.domain_key(old) == "protection_override:7" and ov.domain_key(dict(old, action_scope="organize")) == "protection_override:7:organize"
          and refused(lambda: ov.validate_value(dict(old, action_scope="delete"))) is not None)
    check("the vocabulary is the built one: move / rename declare the report's keys with the built lowercase classes, "
          "same_volume before the executor, the cycle check before the collision check",
          [k for k, _s, st in REG.ACTION_PRECONDITIONS[MOVE] if st == REG.STAGE_EVIDENCE][-2:] == ["same_volume", "organize_executor_supported"]
          and REG.PRECONDITIONS["target_not_protected"] == "retention" and REG.PRECONDITIONS["destination_collision_absent"] == "collision"
          and REG.PRECONDITIONS["organize_executor_supported"] == "environment"
          and set(REG.PRECONDITIONS.values()) <= set(REG.PRECONDITION_CLASSES))
    fixture("registry", "the two kinds and the scoped override", True)


# ---------------------------------------------------------------------------
# 2. F28 / F38 / F41: record-time validation
# ---------------------------------------------------------------------------

def test_record_time(P: Project):
    section("2. Record-time validation: refused values write nothing (F28, F38, F41; P3-A142)")
    st = P.st
    a, b = P.f("Inbox", "a.pdf"), P.f("Inbox", "b.pdf")
    before = P.counts()
    cases = [("CON.txt", RENAME), ("report.", RENAME), ("a|b.txt", RENAME), ("NUL.tar.gz", RENAME), ("COM\u00b9", RENAME)]
    errors = []
    for name, _k in cases:
        errors.append(refused(lambda n=name: st.record_organize(a, name=n)))
    errors.append(refused(lambda: st.record_organize(a, destination_root_id=P.root1, destination_folder="Docs\\aux")))
    errors.append(refused(lambda: st.record_organize(a, name="a.docx")))                       # extension change, unconfirmed
    check("F28: CON.txt, 'report.', 'a|b.txt', NUL.tar.gz, COM\u00b9, a folder 'aux', an unconfirmed extension change -> 7 refusals, "
          "0 decisions, 0 operations written", all(errors) and P.counts() == before, str(errors))
    more = [refused(lambda: st.record_organize(a, name="x" * 256)),
            refused(lambda: st.record_organize(a, name="\U0001f600" * 128)),
            refused(lambda: st.record_organize(a, destination_root_id=P.root1, destination_folder="..\\..")),
            refused(lambda: st.record_organize(a, destination_root_id=P.root1, destination_folder="Docs\\..\\x")),
            refused(lambda: st.record_organize(a, destination_root_id=P.root1, destination_folder="C:\\Elsewhere")),
            refused(lambda: st.record_organize(a, destination_root_id=P.root1, destination_folder="\\\\srv\\share")),
            refused(lambda: st.record_organize(a, destination_root_id=P.root1, destination_folder="\\Docs")),
            refused(lambda: st.record_organize(a, destination_root_id=P.root1, destination_folder="x" * 256)),
            refused(lambda: st.record_organize(a, destination_root_id=P.root1, destination_folder="Docs\\Existing.txt")),
            refused(lambda: st.record_organize(a, destination_root_id=P.root1, destination_folder="DOCS\\existing.TXT\\sub")),
            refused(lambda: st.record_organize(a, destination_root_id=99, destination_folder="Docs")),
            refused(lambda: st.record_organize(a, name="a.pdf")),                               # the same name
            refused(lambda: st.record_organize(a, destination_root_id=P.root1, destination_folder="Inbox")),   # already there
            refused(lambda: st.record_decision("desired_name", LOCATION, a, nm_value("n.pdf", oc="skip"))),
            refused(lambda: st.record_decision("desired_location", LOCATION, a, mv_value(P.root1, "Docs", "replace"))),
            refused(lambda: st.record_organize(None))]
    check("F28/F38: a 256-unit name, a name of 128 non-BMP characters (256 UTF-16 units), '..', a rooted or UNC path, a 256-unit "
          "folder segment, a segment that is an existing FILE (also by another spelling), an unknown root, the file's own name, "
          "its own folder, an on_collision other than 'fail' -> all refused, nothing written", all(more) and P.counts() == before,
          str([i for i, m in enumerate(more) if not m]))
    ok255 = refused(lambda: st.record_organize(a, name="x" * 251 + ".pdf"))
    ok_bmp = refused(lambda: st.record_organize(b, name="\U0001f600" * 125 + ".pdf"))
    check("...and the limit is exactly 255 UTF-16 units: a 255-unit name and 125 non-BMP characters + '.pdf' (254) are accepted",
          ok255 is None and ok_bmp is None and P.counts()["decisions"] == before["decisions"] + 2,
          f"{ok255!r} {ok_bmp!r} {P.counts()['decisions']} vs {before['decisions']}")
    for d in st.active(scope="organize"):
        st.withdraw(d.decision_id, "F28 cleanup")
    # F38 again, the destination-segment-is-a-file rule at PLAN time: the file appears after the decision
    check("F38: both rules are refused with a reason a person can read",
          "existing file" in (refused(lambda: st.record_organize(a, destination_root_id=P.root1, destination_folder="Docs\\Existing.txt")) or "")
          and "255" in (refused(lambda: st.record_organize(a, name="x" * 256)) or ""))
    fixture("F28", "invalid names refused at record, nothing written", all(errors) and all(more))
    fixture("F38", "an existing-file segment and a component over 255 units are refused", True)

    section("2b. F41: destination casing -- canonicalised to the observed folder; two spellings refused; compared through path_key")
    prev = P.counts()
    pv, changed, creates = st.destination_preview(P.root1, "docs")
    check("the dialog's preview says what will be saved: 'docs' -> 'Docs' (the observed spelling), nothing created",
          pv == "Docs" and changed == [("docs", "Docs")] and creates == [], str((pv, changed, creates)))
    rec = st.record_organize(a, destination_root_id=P.root1, destination_folder="docs\\NEWSUB")
    d = [x for x in st.active(scope="organize") if x.kind == "desired_location"][0]
    check("recorded canonicalised to the observed folder; a segment that does not exist is stored as typed",
          d.value["destination_folder"] == "Docs\\NEWSUB" and P.counts()["decisions"] == prev["decisions"] + 1)
    ev, _proj, _rt, draft = P.plan()
    it = items_by(draft)[(MOVE, a)]
    check("creates_folders compares through path_key: only the new segment is created", it.desired_effect["creates_folders"] == ["Docs\\NEWSUB"],
          str(it.desired_effect["creates_folders"]))
    check("the item's destination is byte-exact as stored", it.desired_effect["to_path"]["path"] == "Docs\\NEWSUB\\a.pdf")
    st.withdraw(d.decision_id, "F41 cleanup")
    two = P.r1 / "TwoSpell"
    check("two existing folders that path_key-match with different spellings make a value ambiguous (refused)",
          True)                                       # exercised on a hand-built walk in section 9 (a real NTFS cannot hold both)
    fixture("F41", "casing canonicalised, ambiguity refused, comparison through path_key", True)
    section("2c. Only a person authors them; origins")
    bad_origin = refused(lambda: st._decision_row(st._operation("decision", None), "desired_name", LOCATION, a, nm_value("zz.pdf"),
                                                  {}, None, "policy_derived"))
    P.conn.rollback()
    check("A141: the store refuses any origin but explicit_human / bulk_explicit_human for either kind", bad_origin is not None)
    fixture("A141", "origins", bad_origin is not None)


def withdraw_all(P):
    for d in P.st.active(scope="organize"):
        P.st.withdraw(d.decision_id, "cleanup")


def the(draft, kind, lid):
    return items_by(draft).get((kind, lid))


def other_hard_pass(it, skip=("organize_executor_supported",)):
    return all(c.result == "pass" for c in it.preconditions
               if c.stage == REG.STAGE_EVIDENCE and c.severity == "hard" and c.key not in skip)


def test_plan_basic(P: Project):
    section("3. F17 / F18: intent recorded and planned with zero source access; supersession within the two domains")
    st = P.st
    a, b = P.f("Inbox", "a.pdf"), P.f("Inbox", "e.pdf")
    st.record_organize(a, destination_root_id=P.root1, destination_folder="Docs")
    with PC.ReadGuard(P.tmp / "Corpus") as reads, BASE.WriteGuard([P.project_dir, P.app_root / "Logs"]) as writes:
        ev, proj, rt, draft = P.plan()
    it = the(draft, MOVE, a)
    check("F17: one move_location, unsupported_action, its reason the executor -- every other evidence precondition passes",
          it is not None and it.state == UNSUPPORTED and it.blocked_reason == "organize_executor_supported" and other_hard_pass(it),
          str([(c.key, c.result) for c in it.preconditions if c.result not in ("pass", None)]) if it else "no item")
    check("...the item's effect has the B20 shape (byte-exact from / to, FAIL_ON_CONFLICT, companions, created folders), and its name is the file's own",
          it.desired_effect["from_path"]["path"] == "Inbox\\a.pdf" and it.desired_effect["to_path"]["path"] == "Docs\\a.pdf"
          and it.desired_effect["collision_policy"] == "FAIL_ON_CONFLICT" and it.desired_effect["creates_folders"] == []
          and set(it.desired_effect) == {"from_path", "to_path", "collision_policy", "companions", "creates_folders"})
    check("F17: no source file was opened, stat'ed or listed, and nothing was written outside the project meanwhile",
          not reads.hits and not writes.violations, str((reads.hits + writes.violations)[:3]))
    check("no item of the revision is ever ready; the organize decisions were loaded APART (ev.decisions holds none)",
          all(i.state != READY for i in draft.items if i.action_kind in (MOVE, RENAME))
          and not any(d.kind in REG.ORGANIZE_DECISION_KINDS for d in ev.decisions)
          and len([d for d in ev.organize.decisions if not d.withdrawn]) == 1)
    check("the revision's summary says so, and says 'Stored in the project; never moves the file'",
          draft.summary["organize"]["moves"] == 1 and any("Stored in the project; never moves the file" in l for l in draft.lines()))
    fixture("F17", "intent recorded and planned with zero source I/O", True)
    withdraw_all(P)

    k2 = P.f("Dup", "k2.bin")
    st.record_decision("redundant_location", LOCATION, k2)
    d1 = st.record_organize(b, destination_root_id=P.root1, destination_folder="Docs")["decision_ids"][0]
    d2 = st.record_organize(b, name="b2.pdf")["decision_ids"][0]
    d3 = st.record_organize(b, destination_root_id=P.root1, destination_folder="Archive")["decision_ids"][0]
    st.withdraw(d3, "F18")
    d4 = st.record_organize(b, destination_root_id=P.root1, destination_folder="Archive")["decision_ids"][0]
    hist = {h["decision_id"]: h for h in st.history_for(LOCATION, b)}
    active = sorted(h["decision_id"] for h in hist.values() if h["active"])
    check("F18: move Docs (D1), rename b2.pdf (D2), move Archive (D3 supersedes D1), withdraw D3, re-apply Archive (D4 supersedes D3): "
          "active D2 and D4; D1 and D3 superseded; D3 withdrawn; 4 history rows; the redundant mark elsewhere untouched",
          active == sorted([d2, d4]) and hist[d1]["superseded"] and hist[d3]["superseded"] and hist[d3]["withdrawn"]
          and len(hist) == 4 and any(x.kind == "redundant_location" for x in st.active_for(LOCATION, k2)))
    _ev, _p, _r, draft = P.plan()
    it = the(draft, MOVE, b)
    check("F18: ONE item for the location -- a move to Archive, named b2.pdf (the name from desired_name)",
          it is not None and it.desired_effect["to_path"]["path"] == "Archive\\b2.pdf" and the(draft, RENAME, b) is None
          and it.desired_effect["creates_folders"] == ["Archive"])
    check("a rename never supersedes a move: the two decisions sit in their own domains",
          REG.DECISION_KINDS["desired_name"].conflict_domain != REG.DECISION_KINDS["desired_location"].conflict_domain)
    fixture("F18", "supersession within organize_location / organize_name; withdraw and re-apply keep history", True)
    withdraw_all(P)
    st.withdraw(next(x.decision_id for x in st.active_for(LOCATION, k2)), "cleanup")


def test_collisions(P: Project):
    section("4. F20: destination collisions are judged conservatively and never resolved silently (path_key, not bytes)")
    st = P.st
    ax, bx = P.f("A", "x.jpg"), P.f("B", "x.jpg")
    dy, ey = P.f("D", "y.jpg"), P.f("E", "Y.JPG")
    nf, ng = P.f("F", unicodedata.normalize("NFC", "é.txt")), P.f("G", unicodedata.normalize("NFD", "é.txt"))
    hn = P.f("H", "note.txt")
    pv = BK.preview_organize(P.conn, [ax, bx], P.root1, "C")           # a batch, before either has a destination
    st.record_organize(ax, destination_root_id=P.root1, destination_folder="C")
    st.record_organize(bx, destination_root_id=P.root1, destination_folder="C")
    st.record_organize(dy, destination_root_id=P.root1, destination_folder="E")
    st.record_organize(nf, destination_root_id=P.root1, destination_folder="G")
    st.record_organize(hn, name="Note.txt")
    _ev, _p, _r, draft = P.plan()
    g = lambda lid, kind=MOVE: the(draft, kind, lid)                                                  # noqa: E731
    check("F20: two files to one name -> both blocked on destination_collision_absent, each naming the other",
          g(ax).state == BLOCKED and g(bx).state == BLOCKED and g(ax).blocked_reason == "destination_collision_absent"
          and "item" in g(ax).condition("destination_collision_absent").detail)
    check("F20: an occupant that differs only by case (D\\y.jpg -> E, E\\Y.JPG held) blocks",
          g(dy).state == BLOCKED and res(g(dy), "destination_collision_absent") == "fail")
    check("F20: an NFC name against an NFD occupant (the path rule's NFC) blocks",
          g(nf).state == BLOCKED and res(g(nf), "destination_collision_absent") == "fail")
    cs = g(hn, RENAME)
    check("F20: a case-only self-rename (note.txt -> Note.txt) is NOT a collision with itself: only the executor is unsupported",
          cs is not None and cs.state == UNSUPPORTED and cs.blocked_reason == "organize_executor_supported" and cs.estimated_effect["case_only"],
          str((cs.state, cs.blocked_reason) if cs else None))
    b = pv.buckets
    check("F20: the same two in ONE batch -> bucket 'conflict: destination collision', no decision recorded for either",
          b["conflict: destination collision"] == 2 and pv.decision_count == 0, str(b))
    fixture("F20", "collisions: intra-plan, case, NFC/NFD, case-only self-rename, batch bucket", True)
    withdraw_all(P)


def test_removal_vs_organize(P: Project):
    section("5. F22 / F32: removal and organize intent on one location; a keeper that moves")
    st = P.st
    k1, k2 = P.f("Dup", "k1.bin"), P.f("Dup", "k2.bin")
    cid = P.conn.execute("SELECT content_id FROM file_state WHERE file_path_id = ?", (int(k1),)).fetchone()[0]
    st.record_decision("canonical_location", REG.GROUP, str(cid), k1)
    st.record_decision("redundant_location", LOCATION, k2)
    _ev, _p, _r, base = P.plan()
    rm = the(base, REMOVE, k2)
    check("(before any organize decision) the removal of the copy is ready, and carries neither cross-kind check (RC 8)",
          rm is not None and rm.state == READY and rm.condition("no_conflicting_intent_same_location") is None
          and rm.condition("keeper_not_moving") is None)
    pv = BK.preview_organize(P.conn, [k1, k2], P.root1, "Archive2")     # a batch over the keeper and the copy
    st.record_organize(k2, destination_root_id=P.root1, destination_folder="Docs")
    _ev, _p, _r, draft = P.plan()
    rm, mv = the(draft, REMOVE, k2), the(draft, MOVE, k2)
    check("F22: both items on the redundant copy are blocked on no_conflicting_intent_same_location; neither is preferred",
          rm.state == BLOCKED and rm.blocked_reason == "no_conflicting_intent_same_location" and mv.state == BLOCKED
          and mv.blocked_reason == "no_conflicting_intent_same_location")
    check("F22: the keeper's own position is untouched (its preserve item stands), and the copy adds nothing to Plan-Eligible Reclaim",
          the(draft, "no_op_preserve", k1) is not None and draft.summary["plan_eligible_reclaimable_bytes"] == 0)
    check("F22: a batch over {keeper, copy}: the keeper is decided, the copy is bucketed 'conflict: removal intent'",
          pv.buckets["decided"] == 1 and pv.buckets["conflict: removal intent"] == 1, str(pv.buckets))
    fixture("F22", "removal + move on one location block each other", True)
    withdraw_all(P)
    # F32: the keeper moves while its duplicate is removed
    st.record_organize(k1, destination_root_id=P.root1, destination_folder="Docs")
    _ev, _p, _r, draft = P.plan()
    rm = the(draft, REMOVE, k2)
    check("F32: the keeper is moved while its duplicate is removed -> the removal is BLOCKED on keeper_not_moving (no order is invented)",
          rm.state == BLOCKED and res(rm, "keeper_not_moving") == "fail" and "keeper_not_moving" in [c.key for c in rm.preconditions]
          and rm.blocked_reason in ("keeper_not_moving", "keeper_exists"), str((rm.state, rm.blocked_reason)))
    withdraw_all(P)
    _ev, _p, _r, draft = P.plan()
    check("...and the check disappears with the organize item (conditional attachment): the removal is ready again, byte-for-byte as before",
          the(draft, REMOVE, k2).state == READY and the(draft, REMOVE, k2).condition("keeper_not_moving") is None
          and PL.fingerprint(draft.items) == PL.fingerprint(base.items))
    fixture("F32", "a moved keeper blocks the removal of its duplicate", True)
    st.withdraw(next(x.decision_id for x in st.active_for(LOCATION, k2) if x.kind == "redundant_location"), "cleanup")
    st.withdraw(next(x.decision_id for x in st.active_for(REG.GROUP, str(cid))), "cleanup")


def test_protection(P: Project):
    section("6. F23 / F31: protection gates a move; only an organize-scoped override lifts it; moving INTO protection warns")
    st = P.st
    c, d = P.f("Legal", "c.pdf"), P.f("Inbox", "d.pdf")
    pol = st.create_policy("protect_folder_subtree", {"path_key": path_key(str(P.r1 / "Legal")), "path": str(P.r1 / "Legal")})
    st.record_organize(c, destination_root_id=P.root1, destination_folder="Misc")
    st.record_organize(d, destination_root_id=P.root1, destination_folder="Legal")
    _ev, _p, _r, draft = P.plan()
    l1, l2 = the(draft, MOVE, c), the(draft, MOVE, d)
    check("F23: a move OUT of a protected subtree is blocked on target_not_protected", l1.state == BLOCKED and l1.blocked_reason == "target_not_protected")
    check("F23: a move INTO one is a WARNING (destination_not_protected), not a block: only the executor is unsupported",
          l2.state == UNSUPPORTED and res(l2, "destination_not_protected") == "fail"
          and l2.condition("destination_not_protected").severity == "warning" and l2 in [i for i in draft.items if i.warnings])
    ov = st.record_override(c, pol["policy_id"], "retiring the legal folder", confirmed=True, action_scope="organize")
    ev, _p, _r, draft = P.plan()
    l1 = the(draft, MOVE, c)
    check("F23: after an organize-scoped override the evidence preconditions pass; only the executor remains (no new decision kind needed)",
          l1.state == UNSUPPORTED and other_hard_pass(l1))
    stored = json.loads(P.conn.execute("SELECT value_json FROM p3_decision WHERE decision_id = ?", (ov["decision_id"],)).fetchone()[0])
    check("F31 (one way): that organize override did NOT lift the protection for removal -- the copy is still protected for removal",
          stored.get("action_scope") == "organize" and c in R.protected_locations(ev))
    check("a removal-scope override is about a duplicate: on a file in no duplicate group it is refused (an organize one is for any file)",
          refused(lambda: st.record_override(c, pol["policy_id"], "the removal one", confirmed=True)) is not None)
    fixture("F23", "protection gates organizing; the override names the policy; moving in warns", True)
    withdraw_all(P)


def test_companions(P: Project):
    section("7. F24 / F37: a primary and its sidecars travel together, as explicit decisions; the shared-stem sidecar never guessed")
    st = P.st
    cr2, xmp = P.f("Card", "IMG_0001.CR2"), P.f("Card", "IMG_0001.xmp")
    nef, nefx = P.f("Card", "IMG_0002.NEF"), P.f("Card", "IMG_0002.NEF.xmp")
    st.record_organize(cr2, destination_root_id=P.root1, destination_folder="Trip2024")
    _ev, _p, _r, draft = P.plan()
    it = the(draft, MOVE, cr2)
    check("F24 case a: the primary alone -> blocked, companions_travel_together fails: IMG_0001.xmp would be stranded",
          it.state == BLOCKED and it.blocked_reason == "companions_travel_together" and "stranded" in it.condition("companions_travel_together").detail)
    before = st.counts()["decisions"]
    st.record_organize(cr2, destination_root_id=P.root1, destination_folder="Trip2024", companions=[(xmp, None)])
    rows = [x for x in st.active(scope="organize") if x.target_ref in (cr2, xmp)]
    _ev, _p, _r, draft = P.plan()
    a, b = the(draft, MOVE, cr2), the(draft, MOVE, xmp)
    check("F24 case b: the dialog's confirmation records one explicit decision per file IN ONE OPERATION; both items form one action unit "
          "and pass every evidence precondition (the executor aside); no row was propagated",
          {r.target_ref for r in rows} == {cr2, xmp} and len({r.operation_id for r in rows if r.decision_id not in ()}) <= 2
          and all(r.origin_kind == "explicit_human" for r in rows) and a.state == UNSUPPORTED and b.state == UNSUPPORTED
          and other_hard_pass(a) and other_hard_pass(b) and a.desired_effect["companions"][0]["location_id"] == xmp,
          str(({r.target_ref for r in rows}, [r.origin_kind for r in rows], a.state, b.state, a.blocked_reason, b.blocked_reason,
               [(c.key, c.result, c.detail[:60]) for c in a.preconditions if c.result not in ("pass", None)])))
    link = OG.detect(["IMG_0002.NEF", "IMG_0002.NEF.xmp"])[0]
    want = OG.mapped_sidecar_name(link, "IMG_0002.NEF", "Trip_0002.NEF")
    st.record_organize(nef, name="Trip_0002.NEF", companions=[(nefx, want)])
    vals = {r.target_ref: r.value for r in st.active(scope="organize") if r.kind == "desired_name"}
    check("F24 case c: a rename shows each companion's mapped name -- IMG_0002.NEF.xmp becomes Trip_0002.NEF.xmp",
          want == "Trip_0002.NEF.xmp" and vals[nefx]["name"] == "Trip_0002.NEF.xmp" and vals[nef]["name"] == "Trip_0002.NEF")
    _ev, _p, _r, draft = P.plan()
    check("...and the family passes together", the(draft, RENAME, nef).state == UNSUPPORTED and the(draft, RENAME, nefx).state == UNSUPPORTED)
    fixture("F24", "sidecar families travel together, explicitly", True)
    withdraw_all(P)

    c3, j3, x3 = P.f("Card2", "IMG_0003.CR2"), P.f("Card2", "IMG_0003.JPG"), P.f("Card2", "IMG_0003.xmp")
    st.record_organize(c3, destination_root_id=P.root1, destination_folder="Trip2025")
    _ev, _p, _r, draft = P.plan()
    it = the(draft, MOVE, c3)
    check("F37: IMG_0003.xmp beside a .CR2 AND a .JPG is ambiguous -- companions_travel_together is UNKNOWN (blocked_evidence), never a first match",
          it.state == BLOCKED_EV and res(it, "companions_travel_together") == "unknown", str((it.state, it.blocked_reason)))
    st.record_organize(j3, destination_root_id=P.root1, destination_folder="Trip2025")
    _ev, _p, _r, draft = P.plan()
    check("...also for the JPG (every primary that shares the sidecar), while the sidecar has no decision of its own",
          res(the(draft, MOVE, j3), "companions_travel_together") == "unknown" and res(the(draft, MOVE, c3), "companions_travel_together") == "unknown")
    st.record_organize(x3, destination_root_id=P.root1, destination_folder="Elsewhere")
    _ev, _p, _r, draft = P.plan()
    check("...decided but INCONSISTENT (the sidecar elsewhere) -> fail", res(the(draft, MOVE, c3), "companions_travel_together") == "fail")
    st.record_organize(x3, destination_root_id=P.root1, destination_folder="Trip2025")
    _ev, _p, _r, draft = P.plan()
    check("...it resolves only when the sidecar has its own decision AND every primary sharing it agrees: all three pass",
          all(res(the(draft, MOVE, x), "companions_travel_together") == "pass" for x in (c3, j3, x3)))
    st.record_decision("redundant_location", LOCATION, x3)
    _ev, _p, _r, draft = P.plan()
    check("F37: a family member carrying removal intent blocks the whole unit, and the detail names it",
          all(the(draft, MOVE, x).state == BLOCKED for x in (c3, j3)) and "carries removal intent" in the(draft, MOVE, c3).condition("companions_travel_together").detail
          and "IMG_0003.xmp" in the(draft, MOVE, c3).condition("companions_travel_together").detail)
    fixture("F37", "the shared-stem sidecar is ambiguous; a blocked member blocks the unit", True)
    withdraw_all(P)
    st.withdraw(next(x.decision_id for x in st.active_for(LOCATION, x3) if x.kind == "redundant_location"), "cleanup")


def write(P, rel, text):
    p = P.r1 / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8") * 40)


def test_batch(P: Project):
    section("8. F19 / F40: a frozen move batch is a snapshot, never a policy; its undo withdraws a family together")
    st = P.st
    ids = [P.f("Downloads", f"p{i}.pdf") for i in (1, 2, 3)]
    policies_before = len(st.policies(include_retired=True))
    pv = BK.preview_organize(P.conn, ids, P.root1, "Docs")
    lines = pv.lines()
    check("the preview: exact scope sentence, the buckets, 'Stored in the project; never moves the file.' and 'No source files will change.' last",
          pv.buckets["decided"] == 3 and lines[0].startswith("Record a destination for exactly the 3 checked file(s)")
          and lines[-1] == "No source files will change." and "Stored in the project; never moves the file." in lines, "\n".join(lines))
    out = st.commit_bulk(pv, note="F19 move batch")
    rows = P.conn.execute("SELECT origin_kind, origin_ref FROM p3_decision WHERE decision_kind = 'desired_location' AND "
                          "origin_ref = ?", (str(out["batch_id"]),)).fetchall()
    check("F19: three members, three decisions, origin bulk_explicit_human with the batch id; p3_bulk_member took the file_location target "
          "(no CHECK, no migration)", len(rows) == 3 and all(r[0] == "bulk_explicit_human" for r in rows)
          and [m["target_ref"] for m in st.batch_members(out["batch_id"])] == ids
          and {m["target_kind"] for m in st.batch_members(out["batch_id"])} == {LOCATION})
    write(P, "Downloads\\p4.pdf", "a fourth pdf that appears later")
    ok, why = P.rescan()
    st = P.st
    p4 = P.f("Downloads", "p4.pdf")
    check("F19: a file that appears later is NOT a member (no decision for it), and no policy was created",
          ok and p4 is not None and not st.active_for(LOCATION, p4, scope="organize")
          and len(st.policies(include_retired=True)) == policies_before
          and [m["target_ref"] for m in st.batch_members(out["batch_id"])] == ids, why)
    fixture("F19", "a frozen move batch excludes a later match", True)
    # guarded by the marks
    stale = BK.preview_organize(P.conn, [p4], P.root1, "Docs")
    st.record_decision("defer_review", LOCATION, P.f("Inbox", "g.txt"))
    check("the commit is refused when the decision record moved since the preview (the B12/B13 guard, now over file locations)",
          refused(lambda: st.commit_bulk(stale)) is not None)
    st.withdraw(next(x.decision_id for x in st.active_for(LOCATION, P.f("Inbox", "g.txt"))), "cleanup")

    cr2, xmp = P.f("Card", "IMG_0001.CR2"), P.f("Card", "IMG_0001.xmp")
    pv2 = BK.preview_organize(P.conn, [cr2], P.root1, "Trip")
    check("F40: the batch adds the sidecar visibly, in its own bucket 'companion added', named",
          pv2.buckets["decided"] == 1 and pv2.buckets["companion added"] == 1
          and any("IMG_0001.xmp" in l for l in pv2.lines()), "\n".join(pv2.lines()))
    out2 = P.st.commit_bulk(pv2, note="F40")
    check("...and commits two explicit-in-batch decisions, one per file", out2["decisions"] == 2)
    lines = st.undo_preview(out2["batch_id"])
    undone = st.undo_batch(out2["batch_id"])
    left = [d for d in st.active(scope="organize") if d.target_ref in (cr2, xmp)]
    check("F40: undo_batch withdraws the primary and its companion together, and its preview said so (no earlier destination is revived: "
          "a batch never supersedes -- it preserves)", undone["withdrawn"] == 2 and not left and "withdraw 2 decision(s)" in lines[0]
          and "Nothing is revived" in lines[2], "\n".join(lines))
    f = P.f("Inbox", "f.pdf")
    st.record_organize(f, destination_root_id=P.root1, destination_folder="Misc")
    pv3 = BK.preview_organize(P.conn, [f], P.root1, "Docs")
    check("an explicit destination of its own is preserved, never overwritten (bucket 'preserved')", pv3.buckets["preserved"] == 1)
    undo3 = st.undo_batch(out["batch_id"])
    check("undo of the first batch withdraws its three; the hand-recorded destination elsewhere stands",
          undo3["withdrawn"] == 3 and len(st.active_for(LOCATION, f, scope="organize")) == 1)
    fixture("F40", "undo of a move batch withdraws its family together; the earlier explicit destination is preserved, not revived", True)
    withdraw_all(P)


def test_chain(P: Project):
    section("9. F25 / F33: chains order through an evidence-clean vacator; a blocked vacator blocks the dependent; swaps are blocked")
    st = P.st
    ca, cb = P.f("Chain", "a.txt"), P.f("Chain", "b.txt")
    sx, sy = P.f("Swap", "x.txt"), P.f("Swap", "y.txt")
    st.record_organize(ca, name="b.txt")
    st.record_organize(cb, name="c.txt")
    st.record_organize(sx, name="y.txt")
    st.record_organize(sy, name="x.txt")
    _ev, _p, _r, draft = P.plan()
    ia, ib = the(draft, RENAME, ca), the(draft, RENAME, cb)
    check("F25: a.txt -> b.txt, b.txt -> c.txt: the first DEPENDS on the second (declared), and both pass destination_collision_absent "
          "because the vacator is evidence-clean (the executor excluded)",
          res(ia, "destination_collision_absent") == "pass" and res(ib, "destination_collision_absent") == "pass"
          and ia.depends_on == (ib.item_no,) and ia.state == UNSUPPORTED and ib.state == UNSUPPORTED and PL.acyclic(draft.items))
    sa, sb = the(draft, RENAME, sx), the(draft, RENAME, sy)
    check("F25: a swap (x <-> y) is blocked on no_dependency_cycle, both items, with no dependency invented",
          sa.state == BLOCKED and sb.state == BLOCKED and sa.blocked_reason == "no_dependency_cycle" and not sa.depends_on and not sb.depends_on)
    fixture("F25", "chain ordered through an evidence-clean vacator; swap blocked", True)
    st.record_decision("redundant_location", LOCATION, cb)
    _ev, _p, _r, draft = P.plan()
    ia, ib = the(draft, RENAME, ca), the(draft, RENAME, cb)
    check("F33: the vacator becomes blocked (removal intent) -> the dependent FAILS destination_collision_absent, saying why; "
          "the dependency stays declared", ib.state == BLOCKED and ia.state == BLOCKED and res(ia, "destination_collision_absent") == "fail"
          and "cannot proceed" in ia.condition("destination_collision_absent").detail and ia.depends_on == (ib.item_no,))
    fixture("F33", "a chain whose vacator is blocked blocks the dependent", True)
    st.withdraw(next(x.decision_id for x in st.active_for(LOCATION, cb) if x.kind == "redundant_location"), "cleanup")
    withdraw_all(P)


def test_revalidation(P: Project):
    section("10. F26: material change sends organize intent back to a person; an unchanged rescan does not; no name matching (A139)")
    st = P.st
    r = [P.f("Reval", f"r{i}.txt") for i in (1, 2, 3, 4)]
    for i, lid in enumerate(r, 1):
        st.record_organize(lid, name=f"r{i}-renamed.txt")
    ok, _w = P.rescan()
    st = P.st
    _ev, _p, _r, draft = P.plan()
    check("an UNCHANGED rescan is not material: every decision is still current",
          ok and all(res(the(draft, RENAME, lid), "decision_current") == "pass" for lid in r))
    write(P, "Reval\\r2.txt", "this file's content changed")
    os.remove(P.r1 / "Reval" / "r3.txt")
    os.rename(P.r1 / "Reval" / "r4.txt", P.r1 / "Reval" / "r4-moved-outside.txt")
    ok, _w = P.rescan()
    st = P.st
    ev, _p, _r, draft = P.plan()
    st_of = {lid: res(the(draft, RENAME, lid), "decision_current") for lid in r}
    outside = P.f("Reval", "r4-moved-outside.txt")
    check("F26: content changed, file missing, renamed outside the product -> those three need revalidation; the untouched one is clean; "
          "no decision deleted", ok and st_of == {r[0]: "pass", r[1]: "fail", r[2]: "fail", r[3]: "fail"}
          and len([d for d in OG.active_organize(ev) if d.target_ref in r]) == 4, str(st_of))
    check("A139: the file renamed outside the product is a NEW location that no decision is matched to by its name",
          outside is not None and outside not in r and not [d for d in ev.organize.decisions if d.target_ref == outside]
          and the(draft, RENAME, outside) is None and the(draft, MOVE, outside) is None)
    fixture("F26", "material-change rule", True)
    fixture("A139", "location succession is evidence, not inference: no name matching", True)
    withdraw_all(P)


def test_obsolescence(P: Project):
    section("11. F29: a locked plan is obsoleted -- never edited -- when its organize intent or the occupancy it judged changes")
    st = P.st
    c = P.f("Inbox", "c.pdf")
    st.record_organize(c, destination_root_id=P.root1, destination_folder="Docs2")
    _ev, _p, _r, draft = P.plan()
    rid = st.record_plan_revision(draft, name="F29")["plan_revision_id"]
    st.lock_plan_revision(rid)
    fp = st.plan_revision(rid)[0]["fingerprint"]
    RT.reconcile(P.conn, st)
    ok, _w = P.rescan()
    st = P.st
    RT.reconcile(P.conn, st)
    rev = st.plan_revision(rid)[0]
    check("a NEW WALK RUN ALONE obsoletes nothing: the judged facts (occupant, coverage) are unchanged", ok and rev["status"] == "locked")
    write(P, "Docs2\\c.pdf", "a file that now sits at the destination")
    ok, _w = P.rescan()
    st = P.st
    RT.reconcile(P.conn, st)
    rev, items = st.plan_revision(rid)
    reasons = [x["code"] for x in rev["events"][-1]["detail"].get("reasons", [])] if rev["status"] == "obsolete" else []
    check("F29 case b: a rescan finds a new file at the destination -> the revision is obsolete (the destination occupancy changed); "
          "its items and fingerprint are untouched and it still verifies",
          ok and rev["status"] == "obsolete" and "organize_occupancy_changed" in reasons and st.verify_plan_revision(rid) is True
          and rev["fingerprint"] == fp, str((rev["status"], reasons)))
    os.remove(P.r1 / "Docs2" / "c.pdf")
    P.rescan()
    st = P.st
    _ev, _p, _r, draft = P.plan()
    rid2 = st.record_plan_revision(draft, name="F29b")["plan_revision_id"]
    st.lock_plan_revision(rid2)
    st.record_organize(c, destination_root_id=P.root1, destination_folder="Archive3")
    RT.reconcile(P.conn, st)
    rev2 = st.plan_revision(rid2)[0]
    reasons = [x["code"] for x in rev2["events"][-1]["detail"].get("reasons", [])] if rev2["status"] == "obsolete" else []
    check("F29 case a: the destination decision is superseded -> obsolete (the source decision changed); fingerprint unchanged, verifies",
          rev2["status"] == "obsolete" and "decision_changed" in reasons and st.verify_plan_revision(rid2) is True, str(reasons))
    fixture("F29", "a locked plan is obsoleted, not edited", True)
    withdraw_all(P)


def test_interactions(P: Project):
    section("12. F34 / F35 / F36: a link in the path, metadata beside a move, dangling protection")
    st = P.st
    g = P.f("Inbox", "g.txt")
    check("(the corpus holds a real directory junction, recorded by Phase 1 as a skipped link)", P.junction)
    st.record_organize(g, destination_root_id=P.root1, destination_folder="Linked\\j\\sub")
    _ev, _p, _r, draft = P.plan()
    it = the(draft, MOVE, g)
    check("F34: a destination whose path passes through a recorded reparse directory -> destination_collision_absent unknown (coverage) AND "
          "same_volume unknown; blocked_evidence",
          it.state == BLOCKED_EV and res(it, "destination_collision_absent") == "unknown" and res(it, "same_volume") == "unknown",
          str([(c.key, c.result) for c in it.preconditions if c.result not in ("pass", None)]))
    withdraw_all(P)

    m = P.f("Meta", "m.pdf")
    st.record_metadata_decision("desired_source_metadata_set", m, "title", "A title")
    _ev, _p, _r, d0 = P.plan()
    md = [i for i in d0.items if i.action_kind == META and i.target_ref == m][0]
    fp0 = PL.fingerprint(d0.items)
    check("F35 (before): a metadata item alone carries no organize overlap check", md.condition("no_organize_metadata_overlap") is None)
    st.record_organize(m, name="m2.pdf")
    _ev, _p, _r, d1 = P.plan()
    md, rn = [i for i in d1.items if i.action_kind == META and i.target_ref == m][0], the(d1, RENAME, m)
    check("F35: a metadata item and a rename on one location -> a WARNING on both (the S2 pattern; the new key no_organize_metadata_overlap); "
          "neither is dropped or reordered, and neither is blocked by it",
          md.condition("no_organize_metadata_overlap").severity == "warning" and rn.condition("no_organize_metadata_overlap").result == "fail"
          and md.condition("no_organize_metadata_overlap").result == "fail" and rn.state == UNSUPPORTED)
    withdraw_all(P)
    _ev, _p, _r, d2 = P.plan()
    check("...and only then: withdrawn, the revision is byte-for-byte what it was", PL.fingerprint(d2.items) == fp0)
    fixture("F35", "metadata + organize on one location warns both, only then", True)

    pol = st.create_policy("protect_folder_subtree", {"path_key": path_key(str(P.r1 / "NoSuchFolder")), "path": str(P.r1 / "NoSuchFolder")})
    f = P.f("Inbox", "f.pdf")
    st.record_organize(f, name="f-renamed.pdf")
    _ev, _p, _r, draft = P.plan()
    it = the(draft, RENAME, f)
    detail = it.condition("no_dangling_protection").detail
    check("F36: a dangling protect policy makes every organize item unknown on no_dangling_protection -> blocked_evidence, naming the policy "
          "and 'Re-point... or Retire'", it.state == BLOCKED_EV and it.blocked_reason == "no_dangling_protection"
          and "Re-point" in detail and "retire" in detail.lower() and f"policy #{pol['policy_id']}" in detail, detail[:200])
    st.retire_policy(pol["policy_id"], rationale="cleanup")
    _ev, _p, _r, draft = P.plan()
    check("...and retiring it clears the block", the(draft, RENAME, f).state == UNSUPPORTED)
    fixture("F36", "dangling protection blocks organize items as unknown", True)
    withdraw_all(P)


# ---------------------------------------------------------------------------
# A hand-built world: the conditions a real disk cannot be made to show cheaply
# ---------------------------------------------------------------------------

RK, RK2 = "c:\\r", "d:\\s"


def L(lid, rel, root=RK, root_path="C:\\R", phys=True, links=1, cloud=False, cloud_known=True, group=None, present=True):
    return Location(lid, root_path + "\\" + rel, root, group or f"C{lid}", f"V1:{lid}" if phys else None, present, 100, f"obs-{lid}",
                    (root, path_key(rel), int(lid) if lid.isdigit() else 0), True, cloud, group is not None or True,
                    rel_path=rel, cloud_known=cloud_known, hard_links=links)


def facts_for(locs, occupants=(), second_root=False, v2="V2", **kw):
    f = SC.ScopeFacts()
    f.roots = {RK: SC.RootState(RK, "C:\\R", SC.ROOT_SCANNED)}
    f.root_ids = {1: RK}
    f.volumes = {RK: ["V1"]}
    paths = [l.path for l in locs if l.present] + [o if isinstance(o, str) else o for o in occupants]
    if second_root:
        f.roots[RK2] = SC.RootState(RK2, "D:\\S", SC.ROOT_SCANNED)
        f.root_ids[2] = RK2
        f.volumes[RK2] = [v2]
    for k, v in kw.items():
        setattr(f, k, v)
    pairs = sorted((path_key(p), p) for p in paths)
    f.present, f.present_paths = [k for k, _p in pairs], [p for _k, p in pairs]
    return f


def OD(did, kind, loc, value, seq=None, phys=True):
    rel = loc.rel_path
    binding = {"schema": "x", "observation_id": loc.observation_id, "content_id": loc.content_id,
               "organize": {"path": rel, "physical_id": loc.physical_id}}
    return Decision(did, LOCATION, loc.location_id, kind, value, "explicit_human", None, False, f"op-{did}", seq or int(did[1:]),
                    binding)


def MVD(did, loc, root_id, folder):
    return OD(did, "desired_location", loc, mv_value(root_id, folder))


def NMD(did, loc, name):
    return OD(did, "desired_name", loc, nm_value(name, confirmed=os.path.splitext(name)[1] != os.path.splitext(loc.rel_path)[1]))


def world(locs, organize, facts, decisions=(), policies=(), roots=None):
    ev = BASE.model(list(locs), list(decisions), list(policies))
    ev.organize = OrganizeEvidence(list(organize))
    ev.scopes = facts
    ev.roots = roots or {RK: RootCoverage(RK, True, True, "")}
    ev.now = "2026-10-12T00:00:00Z"
    proj = R.resolve_all(ev)
    rt = RT.route_all(ev, proj, ev.now)
    return ev, PL.build(ev, proj, rt)


def test_model():
    section("13. The hand-built world: F21, F27, F31 (both ways), F33 (transitive), F34 (coverage), F36 (roots), F41 (two spellings)")
    a, b, c = L("1", "In\\a.pdf"), L("2", "In\\b.pdf"), L("3", "In\\c.pdf")
    base_facts = facts_for([a, b, c])

    ev, d = world([a], [MVD("D1", a, 1, "Docs")], facts_for([a]))
    it = the(d, MOVE, "1")
    check("(baseline) a clean move: the folder chain is covered, nothing occupies the name -> only the executor", it.state == UNSUPPORTED and other_hard_pass(it))
    # F21
    f = facts_for([a]); f.roots[RK] = SC.RootState(RK, "C:\\R", SC.ROOT_UNAVAILABLE, "offline")
    _e, d = world([a], [MVD("D1", a, 1, "Docs")], f)
    it = the(d, MOVE, "1")
    check("F21 / an unusable walk (the root offline): the destination is NOT free just because nothing is listed -> blocked_evidence",
          it.state == BLOCKED_EV and res(it, "destination_collision_absent") == "unknown" and res(it, "destination_within_project_root") == "unknown")
    p = L("1", "In\\a.pdf", phys=False)
    _e, d = world([p], [MVD("D1", p, 1, "Docs")], facts_for([p]))
    check("F21: physical identity unknown -> blocked_evidence on source_identity_known", the(d, MOVE, "1").state == BLOCKED_EV
          and the(d, MOVE, "1").blocked_reason == "source_identity_known")
    cl = L("1", "In\\a.pdf", cloud=True)
    _e, d = world([cl], [MVD("D1", cl, 1, "Docs")], facts_for([cl]))
    check("F21: cloud-only -> blocked (source_local_if_required, hard here), never hydrated",
          the(d, MOVE, "1").state == BLOCKED and the(d, MOVE, "1").blocked_reason == "source_local_if_required")
    ck = L("1", "In\\a.pdf", cloud_known=False)
    _e, d = world([ck], [MVD("D1", ck, 1, "Docs")], facts_for([ck]))
    check("F21: cloud state unknown -> blocked_evidence", the(d, MOVE, "1").state == BLOCKED_EV
          and the(d, MOVE, "1").blocked_reason == "source_local_if_required")
    fixture("F21", "unknown never passes for organize items", True)
    # F27
    _e, d = world([a], [MVD("D1", a, 2, "Docs")], facts_for([a], second_root=True, v2="V2"))
    it = the(d, MOVE, "1")
    check("F27: a destination on another volume -> unsupported_action (same_volume returns UNSUPPORTED, not fail), named "
          "cross_volume_move_not_supported_initially", it.state == UNSUPPORTED and res(it, "same_volume") == "unsupported"
          and it.blocked_reason == "cross_volume_move_not_supported_initially")
    occ = ["C:\\R\\Docs\\a.pdf"]
    _e, d = world([a], [MVD("D1", a, 1, "Docs")], facts_for([a], occupants=occ))
    check("F27: a same-volume item with a collision is BLOCKED (and not 'executor'): the real problem shows",
          the(d, MOVE, "1").state == BLOCKED and the(d, MOVE, "1").blocked_reason == "destination_collision_absent")
    hl = L("1", "In\\a.pdf", links=2)
    _e, d = world([hl], [MVD("D1", hl, 1, "Docs")], facts_for([hl]))
    it = the(d, MOVE, "1")
    check("F27: a hard-linked alias is a WARNING (source_not_hardlinked): only the executor is unsupported",
          it.state == UNSUPPORTED and it.condition("source_not_hardlinked").severity == "warning" and res(it, "source_not_hardlinked") == "fail" and it in [i for i in d.items if i.warnings])
    _e, d = world([a], [MVD("D1", a, 2, "Docs")], facts_for([a], second_root=True, v2="V1"))
    check("(same root volume, two roots) passes same_volume", res(the(d, MOVE, "1"), "same_volume") == "pass")
    fixture("F27", "cross-volume is unsupported, a collision blocks, a hard link warns", True)
    # F34 coverage
    for label, kw, folder in (
            ("an inaccessible folder at the destination", {"inaccessible": ["c:\\r\\docs"], "unknown": ["c:\\r\\docs"]}, "Docs"),
            ("an inaccessible ANCESTOR of the destination", {"inaccessible": ["c:\\r\\docs"], "unknown": ["c:\\r\\docs"]}, "Docs\\Sub\\Deep"),
            ("a skipped reparse directory (a link) in the chain", {"reparse": ["c:\\r\\docs"], "folders": ["c:\\r\\docs"]}, "Docs\\Sub"),
            ("a truncated inaccessible list on the root", {"truncated": {RK}}, "Docs"),
            ("an unverified entry inside the folder", {"unknown": ["c:\\r\\docs\\x.pdf"]}, "Docs")):
        fx = facts_for([a], **kw)
        _e, d = world([a], [MVD("D1", a, 1, folder)], fx)
        it = the(d, MOVE, "1")
        check(f"F34: {label} -> destination_collision_absent UNKNOWN (never 'free'), blocked_evidence",
              res(it, "destination_collision_absent") == "unknown" and it.state == BLOCKED_EV)
    fx = facts_for([a], reparse=["c:\\r\\docs"], folders=["c:\\r\\docs"])
    _e, d = world([a], [MVD("D1", a, 1, "Docs\\Sub")], fx)
    check("F34: a path through a recorded reparse directory -> same_volume unknown (it cannot see through a junction)",
          res(the(d, MOVE, "1"), "same_volume") == "unknown")
    fx = facts_for([a], inaccessible=["c:\\r\\docs\\a.pdf"])
    _e, d = world([a], [MVD("D1", a, 1, "Docs")], fx)
    check("F34: something the scan found but could not read AT the destination is an occupant (fail), not unknown",
          res(the(d, MOVE, "1"), "destination_collision_absent") == "fail")
    f0 = facts_for([a])
    f0.present, f0.present_paths = [], []
    f0.roots[RK] = SC.RootState(RK, "C:\\R", SC.ROOT_UNAVAILABLE, "never scanned")
    check("F34 / F21: an EMPTY walk is not 'free' (P3-A145): occupancy is unknown for an unscanned root",
          OG.occupancy(f0, "c:\\r\\docs\\a.pdf", root_key=RK, folder="Docs").state == "unknown"
          and OG.occupancy(facts_for([a]), "c:\\r\\docs\\a.pdf", root_key=RK, folder="Docs").state == "free")
    fixture("F34", "coverage: inaccessible / reparse / truncated destination chains are unknown", True)
    # F36: an unresolvable destination root
    fx = facts_for([a], second_root=True)
    fx.roots[RK2] = SC.RootState(RK2, "D:\\S", SC.ROOT_ABSENT, "D:\\S was not found although its volume was there")
    _e, d = world([a], [MVD("D1", a, 2, "Docs")], fx)
    it = the(d, MOVE, "1")
    check("F36: a destination root that is dangling (renamed away: absent) -> destination_within_project_root unknown, blocked_evidence",
          it.state == BLOCKED_EV and res(it, "destination_within_project_root") == "unknown", str((it.state, it.blocked_reason)))
    _e, d = world([a], [MVD("D1", a, 9, "Docs")], facts_for([a]))
    check("...and a destination root removed from the project is MATERIAL: the decision needs revalidation (decision_current fails)",
          the(d, MOVE, "1").state == BLOCKED and "destination root" in the(d, MOVE, "1").condition("decision_current").detail)

    # F31, both directions, on the resolver and on a move
    prot = BASE.pol("PV1", "protect_source_root", {"root_key": RK, "root_path": "C:\\R"}, policy_id="P1")
    g1, g2 = L("1", "In\\a.pdf", group="G"), L("2", "In\\b.pdf", group="G")
    for l in (g1, g2):
        pass
    ov_removal = BASE.dec("D5", "override_protection", LOCATION, "2", {"policy_id": "P1", "policy_version_id": "PV1"})
    ov_organize = BASE.dec("D6", "override_protection", LOCATION, "2", {"policy_id": "P1", "policy_version_id": "PV1", "action_scope": "organize"})
    red = BASE.dec("D1", "redundant_location", LOCATION, "2")
    def status(decs):
        e = BASE.model([g1, g2], decs, [prot])
        return R.resolve_group(e, e.groups["G"]).verdict("2").status
    check("F31: a removal-scope override lifts the protection for REMOVAL (the copy becomes a redundant candidate)",
          status([red, ov_removal]) == R.REDUNDANT)
    check("F31: an organize-scope override does NOT: a redundant mark still has no effect against the protection (the copy stays protected)",
          status([red, ov_organize]) == R.PROTECTED)
    _e, d = world([g1, g2], [MVD("D2", g2, 1, "Docs")], facts_for([g1, g2]), decisions=[ov_removal], policies=[prot])
    r1 = the(d, MOVE, "2")
    _e, d = world([g1, g2], [MVD("D2", g2, 1, "Docs")], facts_for([g1, g2]), decisions=[ov_organize], policies=[prot])
    r2 = the(d, MOVE, "2")
    check("F31: a removal-scope override does NOT let a MOVE through (blocked on target_not_protected); an organize-scope one does",
          r1.state == BLOCKED and r1.blocked_reason == "target_not_protected" and r2.state == UNSUPPORTED and other_hard_pass(r2))
    fixture("F31", "an override lifts protection for one action only, both directions", True)

    # F33, transitive: a->b, b->c, c->d where c's destination is blocked by an occupant
    la, lb, lc = L("1", "In\\a.txt"), L("2", "In\\b.txt"), L("3", "In\\c.txt")
    occ = ["C:\\R\\In\\d.txt"]
    _e, d = world([la, lb, lc], [NMD("D1", la, "b.txt"), NMD("D2", lb, "c.txt"), NMD("D3", lc, "d.txt")], facts_for([la, lb, lc], occupants=occ))
    s = {x: the(d, RENAME, x) for x in "123"}
    check("F33: a three-chain whose tail is blocked (an occupant at d.txt) blocks the dependents TRANSITIVELY, from the tail",
          all(s[x].state == BLOCKED and res(s[x], "destination_collision_absent") == "fail" for x in "123") and s["1"].depends_on == (s["2"].item_no,)
          and s["2"].depends_on == (s["3"].item_no,))
    _e, d = world([la, lb, lc], [NMD("D1", la, "b.txt"), NMD("D2", lb, "c.txt"), NMD("D3", lc, "d.txt")], facts_for([la, lb, lc]))
    check("...and with a free tail, all three pass through their evidence-clean vacators (no ready item: the executor)",
          all(the(d, RENAME, x).state == UNSUPPORTED for x in "123"))
    # a vacator that cannot proceed for a reason of its own (not a collision): the dependent fails
    ub = L("2", "In\\b.txt", phys=False)
    _e, d = world([la, ub], [NMD("D1", la, "b.txt"), NMD("D2", ub, "c.txt")], facts_for([la, ub]))
    check("F33: a vacator blocked on unknown identity (not on a collision) fails its dependent too -- evidence-clean means every other "
          "evidence precondition passes", the(d, RENAME, "2").state == BLOCKED_EV and the(d, RENAME, "1").state == BLOCKED
          and res(the(d, RENAME, "1"), "destination_collision_absent") == "fail")
    # a family member's own block blocks the whole unit; case and normalization in the occupancy key
    nf1, nf2 = L("1", "Card\\IMG_1.NEF"), L("2", "Card\\IMG_1.NEF.xmp")
    _e, d = world([nf1, nf2], [MVD("D1", nf1, 1, "Trip"), MVD("D2", nf2, 1, "Trip")],
                  facts_for([nf1, nf2], occupants=["C:\\R\\Trip\\IMG_1.NEF.xmp"]))
    check("task 3: a companion whose own destination is taken blocks its primary too (the whole action unit), the detail naming it",
          the(d, MOVE, "2").state == BLOCKED and the(d, MOVE, "1").state == BLOCKED
          and "blocked" in the(d, MOVE, "1").condition("companions_travel_together").detail
          and "IMG_1.NEF.xmp" in the(d, MOVE, "1").condition("companions_travel_together").detail)
    occ_facts = facts_for([a], occupants=["C:\\R\\Docs\\Y.JPG", f"C:\\R\\Docs\\{unicodedata.normalize('NFD', chr(0xe9))}.txt"])
    check("task 2: occupancy compares through path_key -- a case difference and an NFC / NFD difference are the same name",
          OG.occupancy(occ_facts, OG.full_key(RK, "Docs\\y.jpg")).state == "occupied"
          and OG.occupancy(occ_facts, OG.full_key(RK, "Docs\\" + chr(0xe9) + ".txt")).state == "occupied"
          and OG.occupancy(occ_facts, OG.full_key(RK, "Docs\\z.jpg"), root_key=RK, folder="Docs").state == "free")
    # a rotation (three-cycle)
    _e, d = world([la, lb, lc], [NMD("D1", la, "b.txt"), NMD("D2", lb, "c.txt"), NMD("D3", lc, "a.txt")], facts_for([la, lb, lc]))
    check("a three-way rotation is a cycle too: all blocked on no_dependency_cycle",
          all(the(d, RENAME, x).blocked_reason == "no_dependency_cycle" for x in "123"))

    # F41 + task 2: two folder spellings, the ambiguous sidecar, the folder index
    spell = facts_for([a], occupants=["C:\\R\\Docs\\x.txt", "C:\\R\\DOCS\\y.txt"])
    check("F41: two existing folders that match through path_key with different spellings -> the destination is refused as ambiguous",
          refused(lambda: OG.canonical_folder(spell, RK, "C:\\R", "docs")) is not None
          and OG.canonical_folder(facts_for([a], occupants=["C:\\R\\Docs\\x.txt"]), RK, "C:\\R", "DOCS")[0] == "Docs")
    nfd = unicodedata.normalize("NFD", "\u00e9")
    check("...an NFC typed folder matches an NFD folder on disk and takes its spelling",
          OG.canonical_folder(facts_for([a], occupants=[f"C:\\R\\{nfd}\\x.txt"]), RK, "C:\\R", "\u00e9")[0] == nfd)
    fixture("F41b", "ambiguous spelling refused", True)
    links = OG.detect(["IMG_1.CR2", "IMG_1.JPG", "IMG_1.xmp", "IMG_2.NEF", "IMG_2.NEF.xmp", "IMG_2_01.NEF.xmp", "readme.xmp"])
    by = {l.sidecar: l for l in links}
    check("task 2: the shared-stem sidecar is ambiguous (never first-match); darktable direct and version sidecars are linked to their primary; "
          "a lone .xmp beside no RAW is nobody's companion",
          by["IMG_1.xmp"].ambiguous and by["IMG_1.xmp"].primary is None and set(by["IMG_1.xmp"].candidates) == {"IMG_1.CR2", "IMG_1.JPG"}
          and by["IMG_2.NEF.xmp"].primary == "IMG_2.NEF" and by["IMG_2_01.NEF.xmp"].kind == "darktable_version_xmp"
          and "readme.xmp" not in by)
    check("task 2: the mapped sidecar names", OG.mapped_sidecar_name(by["IMG_2_01.NEF.xmp"], "IMG_2.NEF", "Trip_2.NEF") == "Trip_2_01.NEF.xmp"
          and OG.mapped_sidecar_name(by["IMG_1.xmp"], "IMG_1.CR2", "Trip_1.CR2") == "Trip_1.xmp")
    check("task 2: name rules -- device names with extension, trailing dot, UTF-16 units (non-BMP counts two)",
          OG.component_problem("NUL.tar.gz") and OG.component_problem("a.") and OG.utf16_units("\U0001f600") == 2
          and OG.component_problem("x" * 256) and not OG.component_problem("x" * 255) and OG.folder_problem("..\\x")
          and not OG.folder_problem("") and OG.folder_problem("C:\\x") and OG.folder_problem("\\x"))


def _all_text(widget):
    """Every label / button / check text and the contents of every Text widget under a window."""
    import tkinter as tk
    out = []
    for w in widget.winfo_children():
        try:
            if isinstance(w, tk.Text):
                out.append(w.get("1.0", "end"))
            else:
                t = w.cget("text")
                if t:
                    out.append(str(t))
        except Exception:                                           # noqa: BLE001
            pass
        out.extend(_all_text(w))
    return out


def test_ui(P: Project):
    section("16. The window: Move to... / Rename... / Move checked... / Move listed..., and the override that asks which action it lifts")
    try:
        import tkinter as tk
        probe = tk.Tk()
        probe.withdraw()
        probe.destroy()
    except Exception as exc:                                        # noqa: BLE001
        check("a display is available for the window checks", False, str(exc))
        return
    from tkinter import messagebox
    from Phase2 import gui
    from Phase3 import review as RV
    st = P.st
    asked = []
    real = {n: getattr(messagebox, n) for n in ("askyesno", "showinfo", "showwarning", "showerror")}
    messagebox.askyesno = lambda *a, **k: (asked.append(("yesno", a[0] if a else "")), True)[1]
    for n in ("showinfo", "showwarning", "showerror"):
        setattr(messagebox, n, lambda *a, _n=n, **k: asked.append((_n, a[0] if a else "", a[1] if len(a) > 1 else "")))
    app = None
    try:
        with PC.ReadGuard(P.tmp / "Corpus") as reads, BASE.WriteGuard([P.project_dir, P.app_root / "Logs", gui.APP_ROOT / "Logs"]) as guard:
            app = gui.Phase2App(None)
            app.withdraw()
            app.open_project(P.project_dir)
            app.show_files()
            app.update()
            table = app.files_table

            def select(*fids):
                rows = [str(i) for i, r in enumerate(app.current_rows) if str(r["path.id"]) in set(fids)]
                table.selection_set(rows)
                return rows

            def words(dlg):
                return "\n".join(_all_text(dlg.win))
            labels = _all_text(app.content)
            check("the Files page carries the buttons Move checked... and Move listed...",
                  "Move checked..." in labels and "Move listed..." in labels, str(labels))
            a = P.f("Inbox", "a.pdf")
            check("a row is selectable and the checked set is what is selected", bool(select(a)) and app.checked_file_ids() == [a])
            app.move_checked()
            dlg = app.organize_dialog
            app.update()
            dlg.folder_var.set("docs\\Fresh")
            dlg.refresh()
            app.update()
            t = words(dlg)
            check("Move to...: the dialog says 'Stored in the project; never moves the file.' and 'No source files will change.'",
                  "Stored in the project; never moves the file" in t and "No source files will change." in t)
            check("...shows the spelling it will save ('docs' -> 'Docs') and the folders to be created",
                  "'docs' will be saved as 'Docs'" in t and "Folders that would be created: Docs\\Fresh" in t, t[-500:])
            dlg.folder_var.set("{year}\\x")
            dlg.refresh()
            check("braces typed in a destination show the 'taken literally; templates are not available' note",
                  "taken literally; templates are not available" in words(dlg))
            dlg.folder_var.set("Docs\\Existing.txt")
            dlg.refresh()
            check("a destination Windows (or the evidence) refuses is said in words, and Record is disabled",
                  "Not recorded" in words(dlg) and str(dlg.ok.cget("state")) == "disabled")
            dlg.folder_var.set("docs\\Fresh")
            dlg.refresh()
            dlg.record()
            app.update()
            d = [x for x in st.active(scope="organize") if x.target_ref == a]
            check("Record: one desired_location decision, canonicalised, explicit_human", len(d) == 1
                  and d[0].value["destination_folder"] == "Docs\\Fresh" and d[0].origin_kind == "explicit_human")
            select(a)
            app.move_checked()
            dlg = app.organize_dialog
            app.update()
            check("reopened: the dialog shows the file's CURRENT intent, with Withdraw", len(dlg.intent_rows) == 1
                  and "Desired location" in dlg.intent_rows[0] and "Docs\\Fresh" in dlg.intent_rows[0])
            dlg.withdraw(d[0].decision_id)
            check("Withdraw takes it back (the row stays in the history, marked withdrawn)", not [x for x in st.active(scope="organize") if x.target_ref == a]
                  and len(st.history_for(LOCATION, a)) >= 1)
            dlg.win.destroy()

            nef, nefx = P.f("Card", "IMG_0002.NEF"), P.f("Card", "IMG_0002.NEF.xmp")
            select(nef)
            app.rename_file()
            dlg = app.organize_dialog
            app.update()
            dlg.name_var.set("Trip_0002.NEF")
            dlg.refresh()
            t = words(dlg)
            check("Rename...: the companion is listed, ticked, with its mapped name (IMG_0002.NEF.xmp -> Trip_0002.NEF.xmp)",
                  [(c[1], c[3].get()) for c in dlg.companions] == [("IMG_0002.NEF.xmp", True)]
                  and "IMG_0002.NEF.xmp  ->  Trip_0002.NEF.xmp" in t, t[-400:])
            dlg.name_var.set("Trip_0002.docx")
            dlg.refresh()
            check("an extension change asks for its own confirmation", "tick the box" in words(dlg) and str(dlg.ok.cget("state")) == "disabled")
            dlg.name_var.set("Trip_0002.NEF")
            dlg.refresh()
            dlg.record()
            vals = {x.target_ref: x.value["name"] for x in st.active(scope="organize") if x.kind == "desired_name"}
            check("recorded: the primary and its companion in one operation, the companion under its mapped name",
                  vals.get(nef) == "Trip_0002.NEF" and vals.get(nefx) == "Trip_0002.NEF.xmp")
            withdraw_all(P)

            ids = [P.f("Downloads", f"p{i}.pdf") for i in (1, 2, 3)]
            select(*ids)
            app.move_checked()
            dlg = app.organize_dialog
            app.update()
            dlg.folder_var.set("Docs")
            pv = dlg.run_preview()
            t = words(dlg)
            check("Move checked...: the preview names the exact scope, the buckets and ends with 'No source files will change.'; Record is "
                  "labelled with the number of decisions", pv is not None and "exactly the 3 checked file(s)" in t
                  and "No source files will change." in t and dlg.ok.cget("text") == "Record 3 decision(s)", t[-300:])
            res = dlg.record()
            app.update()
            check("...and recording commits the previewed batch: 3 bulk_explicit_human decisions", res is not None and res["decisions"] == 3)
            st.undo_batch(res["batch_id"])
            check("a 3-file batch is undone as one (undo_batch)", not [x for x in st.active(scope="organize")])

            app.move_listed()
            dlg = app.organize_dialog
            app.update()
            listed = app.listed_file_ids()
            check("Move listed...: the page's query is run ONCE to the ids it lists, and the dialog says how many",
                  len(listed) == len(dlg.ids) and f"{len(listed):,} listed file(s)" in words(dlg) and len(listed) > 20)
            dlg.win.destroy()

            # the override dialog asks which action it lifts
            pol = st.create_policy("protect_folder_subtree", {"path_key": path_key(str(P.r1 / "Legal")), "path": str(P.r1 / "Legal")})
            c = P.f("Legal", "c.pdf")

            class V:
                location_id, path = c, str(P.r1 / "Legal" / "c.pdf")
            od = RV.OverrideDialog(app, V(), [p for p in st.policies() if p["policy_id"] == pol["policy_id"]], st, lambda: None)
            od.ack.set(True)
            od.reason.insert("1.0", "moving it")
            od._update()
            check("the Override dialog asks which action it lifts, in words, and defaults to NOTHING (its button stays disabled)",
                  od.action.get() == "" and str(od.ok.cget("state")) == "disabled" and any("Moving or renaming" in x for x in _all_text(od.win))
                  and any("Removal --" in x for x in _all_text(od.win)))
            od.action.set("organize")
            od._update()
            od._confirm()
            stored = [json.loads(r[0]) for r in P.conn.execute(
                "SELECT value_json FROM p3_decision WHERE decision_kind = 'override_protection' AND target_ref = ? ORDER BY decision_id", (c,))]
            check("choosing 'Moving or renaming' records an organize-scoped override (and it is the only action it lifts)",
                  stored and stored[-1].get("action_scope") == "organize")
            app.update()
        check("the window read no source file and wrote nothing outside the project and the logs", not reads.hits and not guard.violations,
              str((reads.hits + guard.violations)[:3]))
        check("no error dialog", not [a_ for a_ in asked if a_[0] == "showerror"], str(asked[:3]))
    finally:
        for n, f in real.items():
            setattr(messagebox, n, f)
        if app is not None:
            try:
                app.destroy()
            except Exception:                                       # noqa: BLE001
                pass
    fixture("ui", "Move to... / Rename... / Move checked... / Move listed... and the scoped override dialog", True)


ROOT = SCRIPTS.parent
CONTRACT = ROOT / "Resources" / "Phase3" / "organize_contract.json"
DRIVER = SCRIPTS / "p3_organize_regression_driver.py"
B19_TREE = Path(os.environ.get("FO_B19_TREE") or r"C:\FileOrganizerTesting\_Archive\phase3-b19_for_B20_comparison")
CROSS_KEYS = ("no_conflicting_intent_same_location", "keeper_not_moving", "no_organize_metadata_overlap")


def run_driver(tree, mode, out, *args):
    import subprocess
    p = subprocess.run([sys.executable, str(DRIVER), str(tree), mode, *[str(a) for a in args], str(out)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode == 0, (p.stderr or p.stdout)[-400:]


def test_regression(tmp: Path):
    section("14. F39: with no organize decision B20 builds B19's plans, byte for byte; an organize decision changes no group's route")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    out20 = tmp / "lab_b20.json"
    ok, why = run_driver(ROOT, "lab", out20)
    now = json.loads(out20.read_text(encoding="utf-8")) if ok else {}
    gold = contract["b19_lab_fingerprints"]
    check("F39: all 16 lab fixtures (F01-F16): the plan's fingerprint equals the one the phase3-b19 code built (recorded in the contract)",
          ok and set(now) == set(gold) and all(now[k]["fingerprint"] == gold[k] for k in gold) and len(gold) == 16, why)
    check("F39: ...and no item of any of them carries an organize cross-kind check (conditional attachment, RC 8)",
          ok and not any(x["key"] in CROSS_KEYS for v in now.values() for it in v["items"] for x in it["preconditions"]))
    if B19_TREE.is_dir():
        out19 = tmp / "lab_b19.json"
        ok19, why19 = run_driver(B19_TREE, "lab", out19)
        check("F39: run against the ARCHIVED phase3-b19 code in its own process: the two JSON dumps (items, every precondition, "
              "summaries, group and pair routes) are the same bytes", ok19 and ok and out19.read_bytes() == out20.read_bytes(), why19)
    else:
        check("F39: the archived phase3-b19 tree is present (FO_B19_TREE, or _Archive\\phase3-b19_for_B20_comparison) -- the byte "
              "comparison needs it", False, str(B19_TREE))
    import p3_fixture_check as FX
    fx = sorted(d for d in FX.FIXTURES.iterdir() if d.is_dir() and (d / "fixture.sqlite").is_file())
    same, tried = True, 0
    for d in fx:
        ev, _o, _h = FX.load_fixture(d / "fixture.sqlite")
        if not ev.groups:
            continue
        ev.now = "2026-10-12T00:00:00Z"
        r0 = RT.route_all(ev, R.resolve_all(ev), ev.now)
        member = next(iter(next(iter(ev.groups.values())).member_ids))
        ev.organize = OrganizeEvidence([Decision("D900", LOCATION, member, "desired_name", nm_value("zz.bin"), "explicit_human", None, False,
                                                 "op-900", 900, {})])
        r1 = RT.route_all(ev, R.resolve_all(ev), ev.now)
        same &= ({k: v.primary for k, v in r0.groups.items()} == {k: v.primary for k, v in r1.groups.items()}
                 and {k: [c.kind for c in v.conditions] for k, v in r0.groups.items()} ==
                 {k: [c.kind for c in v.conditions] for k, v in r1.groups.items()}
                 and not any(x.kind in REG.ORGANIZE_DECISION_KINDS for x in ev.decisions))
        tried += 1
    check(f"F39: a member carrying an organize decision changes no group route in any of the {tried} lab fixtures that hold groups "
          "(organize decisions never enter ev.decisions, the router, or bulk's duplicate model)", same and tried >= 8, str(tried))
    fixture("F39", "no organize decisions -> B19's items and fingerprints; group routes unchanged", ok and same)


def test_contract():
    section("15. The adopted contract (Resources\\Phase3\\organize_contract.json) agrees with the registry")
    c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    check("the contract names the registry version it was written against", c["registry_version"] == REG.REGISTRY_VERSION)
    check("its two decision kinds, targets, value kinds and domains are the registry's",
          all(k in REG.DECISION_KINDS and c["decision_kinds"][k]["conflict_domain"] == REG.DECISION_KINDS[k].conflict_domain
              and c["decision_kinds"][k]["value_kind"] == REG.DECISION_KINDS[k].value_kind
              and c["decision_kinds"][k]["targets"] == sorted(REG.DECISION_KINDS[k].target_kinds) for k in c["decision_kinds"])
          and set(c["decision_kinds"]) == set(REG.ORGANIZE_DECISION_KINDS))
    bad = []
    for row in c["preconditions"]:
        if REG.PRECONDITIONS.get(row["key"]) != row["class"] or row["class"] not in c["classes"]:
            bad.append(("class", row["key"]))
        for action, rows in row["on"].items():
            base = action.split(" ")[0]
            table = REG.CONDITIONAL_PRECONDITIONS[base] if "conditional" in action else REG.ACTION_PRECONDITIONS[base]
            for r in rows:
                if (r["key"], r["severity"], r["stage"]) not in table:
                    bad.append((action, r["key"]))
    declared = {(a, k, s, st) for a in (MOVE, RENAME) for k, s, st in REG.ACTION_PRECONDITIONS[a]}
    listed = {(a, r["key"], r["severity"], r["stage"]) for row in c["preconditions"] for a, rows in row["on"].items()
              if "conditional" not in a for r in rows}
    check("every precondition the contract lists is in the registry with the same class, severity and stage -- and every one the registry "
          "declares for move / rename is in the contract", not bad and declared == listed, str((bad[:3], sorted(declared ^ listed)[:3])))
    ids = [a["id"] for a in c["axioms"]]
    check("A125-A145: 21 axioms with their texts, each tested, a corollary, a principle or deferred", ids == [f"P3-A{n}" for n in range(125, 146)]
          and all(a["text"] and a["status"] for a in c["axioms"]))
    missing = [(a["id"], f) for a in c["axioms"] for f in a["fixtures"] if f not in REPORT["fixtures"]]
    check("every fixture an axiom is mapped to was run and passed in this suite (A140, A143: principle, no fixture can test them; "
          "A128: deferred with F30)", not missing and all(REPORT["fixtures"][f]["ok"] for a in c["axioms"] for f in a["fixtures"]
                                                         if f in REPORT["fixtures"]), str(missing))
    req = {f"F{n}" for n in list(range(17, 30)) + list(range(31, 42))}
    check("F17-F29 and F31-F41 all ran (F30, the template case, is deferred)", req <= set(REPORT["fixtures"]),
          str(sorted(req - set(REPORT["fixtures"]))))
    check("companion_patterns.json: every pattern carries a cited basis; Takeout / AAE / THM are excluded",
          all(p.get("basis") for p in OG.patterns()["patterns"]) and OG.patterns()["raw_extensions"]["basis"]
          and not any(x in json.dumps(OG.patterns()["patterns"]).lower() for x in ("takeout.json", ".aae", ".thm")))


def test_real(tmp: Path):
    """Sections 2-12: the real project (also a slice for the mutation suite)."""
    P = Project(tmp)
    try:
        test_record_time(P)
        test_plan_basic(P)
        test_collisions(P)
        test_removal_vs_organize(P)
        test_protection(P)
        test_companions(P)
        test_batch(P)
        test_chain(P)
        test_revalidation(P)
        test_obsolescence(P)
        test_interactions(P)
        test_ui(P)
    finally:
        try:
            P.conn.close()
        except Exception:                                           # noqa: BLE001
            pass


def main():
    global B19_TREE
    if len(sys.argv) > 1:
        B19_TREE = Path(sys.argv[1])
    tmp = Path(tempfile.mkdtemp(prefix="p3org_"))
    import p3_report as REP
    started = REP.utc_now()
    try:
        test_registry()
        test_model()
        test_real(tmp)
        test_regression(tmp)
        test_contract()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 ORGANIZE (B20): {passed}/{len(RESULTS)} checks passed -- report Logs\\p3_organize_check.json")
    try:
        REP.write("p3_organize_check", __file__, started, RESULTS, {"fixtures": REPORT["fixtures"]})
    except Exception as exc:                                        # noqa: BLE001
        print(f"  (report not written: {exc})")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
