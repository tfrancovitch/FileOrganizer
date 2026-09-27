#!/usr/bin/env python3
r"""B18 regression: the plan fingerprint specification and its golden vectors (task 1.1, M3).

The specification is Docs\Specifications\PLAN_FINGERPRINT_SPEC.md (summarized
in ARCHITECTURE.md, "The plan fingerprint"); the vectors are
Resources\Phase3\fingerprint_vectors.json. What this proves, in order:

  1. The vectors: each fixed payload (an empty plan; a removal with every kind
     of precondition result; non-ASCII paths composed and decomposed -- two
     different hashes, because strings are hashed as stored; an Update Source
     Metadata item with a keyword list; a blocked item on unknown evidence; an
     unpaired surrogate; escaping and key order) canonicalizes to the fixed
     bytes and the fixed SHA-256 -- through plans.canonical AND through an
     independent encoder written in this file from the specification's text
     alone. The items behind each payload are rebuilt here as PlanItems, so
     plans.payload's field mapping is pinned too.
  2. The encoders are explicit: a datetime, a Path, a Decimal, a set, bytes,
     NaN, a non-string object key are REFUSED (TypeError), never passed
     through str(); tuples are arrays.
  3. No existing fingerprint changed: B15-B17 hashed with `default=str`; for
     every payload built here, and every revision the other projects in this
     suite lock, the old canonical form (with a default that records any
     call) and the new one are the same bytes, and the old default was never
     called. So the payload schema stays /1.
  4. Revisions LOCKED BY THE B15, B16 AND B17 CODE (each materialized from its
     git tag and run in its own process on a corpus with non-ASCII names) are
     opened by this build -- the database migrated to schema 16 -- and verify
     against the fingerprint they were locked with, under the payload schema
     recorded (none: /1). A revision locked by this build records its schema
     in its basis and verifies too.

Run:  python Scripts\p3_fingerprint_check.py
"""
from __future__ import annotations

import decimal
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path, PureWindowsPath

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

from Phase3 import plans as PL                                               # noqa: E402
from Phase3 import registry as REG                                           # noqa: E402

RESULTS = []
VECTORS = SCRIPTS.parent / "Resources" / "Phase3" / "fingerprint_vectors.json"
ROOT = SCRIPTS.parent


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" -- {detail}" if (detail and not ok) else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


# ---------------------------------------------------------------------------
# The items behind the vectors
# ---------------------------------------------------------------------------

def _pre(key, severity="hard", stage=REG.STAGE_EVIDENCE, params=None, result="pass"):
    return PL.Precondition(key, REG.PRECONDITIONS[key], severity, stage, params or {}, result, "")


RESUME_NFC = "C:\\Docs\\R\u00e9sum\u00e9 \u2014 \u65e5\u672c\u8a9e.pdf"


def vector_items():
    """name -> (description, [PlanItem])"""
    remove = PL.PlanItem(
        1, REG.ACTION_REMOVE, REG.LOCATION, "12", ((REG.GROUP, "7"),),
        {"effect": "location_absent", "path": "C:\\Backup\\a.pdf"}, decision_ids=("3", "11"), retaining_ids=(),
        policy_version_ids=("2",),
        evidence_binding={"schema": "fileorganizer.p3.evidence-binding/1", "target_kind": REG.LOCATION, "file_path_id": 12,
                          "observation_id": 40, "content_id": 7, "members": [{"file_path_id": 5, "observation_id": 33}],
                          "source": {"target_kind": REG.GROUP, "target_ref": "7",
                                     "members": [{"file_path_id": 5, "observation_id": 33}, {"file_path_id": 12, "observation_id": 40}]}},
        preconditions=[_pre("target_exists", params={"observation_id": "40"}),
                       _pre("keeper_exists", params={"keepers": ["5"]}),
                       _pre("minimum_location_count_satisfied", params={"minimum": 1}),
                       _pre("target_content_matches", stage=REG.STAGE_LIVE, result=None)],
        postconditions=("location_absent",), depends_on=(2,), impact_class="high", state=REG.ITEM_READY)
    preserve_nfc = PL.PlanItem(1, REG.ACTION_PRESERVE, REG.LOCATION, "5", ((REG.GROUP, "7"),),
                               {"effect": "retain", "path": RESUME_NFC, "why": ["the group's canonical"]},
                               decision_ids=("11",), state=REG.ITEM_PRESERVE, impact_class="none")
    preserve_nfd = PL.PlanItem(1, REG.ACTION_PRESERVE, REG.LOCATION, "5", ((REG.GROUP, "7"),),
                               {"effect": "retain", "path": unicodedata.normalize("NFD", RESUME_NFC), "why": ["the group's canonical"]},
                               decision_ids=("11",), state=REG.ITEM_PRESERVE, impact_class="none")
    metadata = PL.PlanItem(
        3, REG.ACTION_METADATA, REG.LOCATION, "21", ((REG.METADATA_QUESTION, "4"),),
        {"effect": "metadata_value", "field": "keywords", "operation": "set", "path": "C:\\Docs\\report.docx",
         "value": ["tax", "2024"]}, decision_ids=("30",),
        evidence_binding={"file_path_id": 21, "observation_id": 50, "content_id": None, "members": [],
                          "question": {"question_id": 4, "semantic_field": "keywords", "analyzer_result_ids": [88],
                                       "observed_state": "unsupported"}},
        preconditions=[_pre("writer_supported", params={"file_family": "ooxml", "semantic_field": "keywords",
                                                          "capability_status": "unknown"}, result="unsupported"),
                       _pre("source_not_signed_or_certified", result="pass"), _pre("source_not_encrypted", result="pass"),
                       _pre("source_not_hardlinked", result="pass")],
        postconditions=("desired_metadata_observed",), impact_class="medium", state=REG.ITEM_UNSUPPORTED,
        blocked_reason="writer_unsupported")
    blocked = PL.PlanItem(
        2, REG.ACTION_REMOVE, REG.LOCATION, "13", ((REG.VISUAL_PAIR, "9"),), {"effect": "location_absent", "path": "C:\\Pics\\b.jpg"},
        decision_ids=("40", "41"), retaining_ids=("42",),
        preconditions=[_pre("required_coverage_complete", result="unknown"),
                       _pre("no_retaining_decision", params={"decisions": ["42"]}, result="fail"),
                       _pre("minimum_physical_copy_count_satisfied", params={"minimum": 1}, result="not_applicable"),
                       _pre("no_removal_metadata_overlap", severity="warning", params={"other_item": 3}, result="fail")],
        postconditions=("location_absent",), impact_class="high", state=REG.ITEM_BLOCKED, blocked_reason="no_retaining_decision")
    surrogate = PL.PlanItem(1, REG.ACTION_PRESERVE, REG.LOCATION, "6", ((REG.GROUP, "8"),),
                            {"effect": "retain", "path": "C:\\Odd\\name\udc80.txt", "why": ["a keeper of its group"]},
                            state=REG.ITEM_PRESERVE, impact_class="none")
    escapes = PL.PlanItem(1, REG.ACTION_PRESERVE, REG.LOCATION, "7", ((REG.GROUP, "8"),),
                          {"effect": "retain", "path": "C:\\Q\\say \"hi\"\t\u007f\u2028 \U0001F600.txt",
                           "why": ["z", "a"], "zeta": 1, "Alpha": 2, "\u00e9": 3, "E": 4, "\U0001F600": 5, "\uff41": 6},
                          state=REG.ITEM_PRESERVE, impact_class="none")
    return {
        "empty_plan": ("A revision with no items.", []),
        "remove_with_preconditions": ("One Remove Location item: its sources, decisions, policy version, evidence binding, "
                                      "evidence-stage results and a live-stage condition (result null), a dependency.", [remove]),
        "non_ascii_path_nfc": ("A preserved copy whose path is non-ASCII, composed (NFC).", [preserve_nfc]),
        "non_ascii_path_nfd": ("The same path decomposed (NFD): a DIFFERENT hash -- strings are hashed as stored.", [preserve_nfd]),
        "metadata_item": ("An Update Source Metadata item: a keyword list, a writer 'unsupported', the integrity checks.", [metadata]),
        "blocked_item": ("A blocked removal: an unknown result, a failed retention check naming the decision, a warning.", [blocked]),
        "unpaired_surrogate": ("A path holding an unpaired surrogate (a Windows name that is not valid UTF-16): its "
                               "three-byte generalized UTF-8 form.", [surrogate]),
        "escaping_and_key_order": ("A quote, a tab, DEL, U+2028, an astral character; object keys in code-point order "
                                   "(uppercase before lowercase, U+FF41 before U+1F600).", [escapes]),
        "two_items": ("A removal and a metadata item together, numbered 1 and 3 (numbers as recorded, never renumbered).",
                      [remove, metadata]),
    }


# ---------------------------------------------------------------------------
# An independent encoder, written from the specification's text alone
# ---------------------------------------------------------------------------

_SHORT = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}


def spec_string(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch in _SHORT:
            out.append(_SHORT[ch])
        elif ord(ch) < 0x20:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def spec_encode(v) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return spec_string(v)
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(spec_encode(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ",".join(spec_string(k) + ":" + spec_encode(v[k]) for k in sorted(v)) + "}"
    raise TypeError(type(v).__name__)


def spec_bytes(v) -> bytes:
    return spec_encode(v).encode("utf-8", "surrogatepass")


def old_canonical(obj, calls):
    def default(x):
        calls.append(type(x).__name__)
        return str(x)
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=default).encode("utf-8")
    except UnicodeEncodeError:
        return None                         # B15-B17 raised here: no fingerprint of such a payload can exist


# ---------------------------------------------------------------------------
# 1-3
# ---------------------------------------------------------------------------

def test_vectors():
    section("1. The golden vectors: fixed payloads -> fixed bytes -> fixed SHA-256")
    stored = json.loads(VECTORS.read_text(encoding="utf-8"))
    built = vector_items()
    check("the vectors file names the specification, the payload schema and at least 5 vectors (an empty plan, non-ASCII "
          "paths, a metadata item and a blocked item among them)",
          stored["payload_schema"] == PL.PLAN_PAYLOAD_SCHEMA and "PLAN_FINGERPRINT_SPEC.md" in stored["specification"]
          and len(stored["vectors"]) >= 5 and {"empty_plan", "non_ascii_path_nfc", "metadata_item", "blocked_item"}
          <= {v["name"] for v in stored["vectors"]})
    calls, crashed = [], []
    for v in stored["vectors"]:
        payload = v["payload"]
        mine = PL.canonical(payload)
        indep = spec_bytes(payload)
        from_items = PL.payload(built[v["name"]][1])
        check(f"{v['name']}: canonical bytes and SHA-256 as fixed; the independent encoder agrees; the items rebuild the payload",
              mine.hex() == v["canonical_utf8_hex"] and hashlib.sha256(mine).hexdigest() == v["sha256"] and indep == mine
              and from_items == payload and PL.fingerprint(built[v["name"]][1]) == v["sha256"],
              f"{hashlib.sha256(mine).hexdigest()} vs {v['sha256']}")
        old = old_canonical(payload, calls)
        if old is None:
            crashed.append(v["name"])
        elif old != mine:
            calls.append(f"differs:{v['name']}")
    nfc = next(v for v in stored["vectors"] if v["name"] == "non_ascii_path_nfc")
    nfd = next(v for v in stored["vectors"] if v["name"] == "non_ascii_path_nfd")
    check("as stored, no normalization: the composed and the decomposed path hash differently", nfc["sha256"] != nfd["sha256"])
    sur = next(v for v in stored["vectors"] if v["name"] == "unpaired_surrogate")
    check("an unpaired surrogate U+DC80 is written as the three bytes ED B2 80", "edb280" in sur["canonical_utf8_hex"])
    esc = PL.canonical(next(v for v in stored["vectors"] if v["name"] == "escaping_and_key_order")["payload"]).decode("utf-8")
    check("escaping: '\"' and '\\t' short-escaped, DEL and U+2028 and U+1F600 written raw; keys in code-point order",
          '\\"hi\\"\\t\u007f\u2028 \U0001F600' in esc and esc.index('"Alpha"') < esc.index('"E"') < esc.index('"effect":"retain"')
          < esc.index('"\u00e9"') < esc.index('"\uff41"') < esc.index('"\U0001F600"'))
    check("B15-B17's canonical form (default=str) gives the same bytes for every vector, and its default was never called",
          calls == [], str(calls))
    check("...except the unpaired surrogate, which B15-B17 could not fingerprint at all (their UTF-8 encode raised): no "
          "existing fingerprint can hold one, and the specification now defines it", crashed == ["unpaired_surrogate"], str(crashed))

    section("2. The encoders are explicit: nothing is passed through str()")
    for label, value in (("a datetime", datetime.datetime(2026, 9, 27, 12, 0)), ("a date", datetime.date(2026, 9, 27)),
                         ("a Path", PureWindowsPath("C:/Docs")), ("a Decimal", decimal.Decimal("1.5")),
                         ("a set", {"a"}), ("bytes", b"x"), ("NaN", float("nan")), ("infinity", float("inf"))):
        try:
            PL.canonical({"schema": PL.PLAN_PAYLOAD_SCHEMA, "items": [{"x": value}]})
            ok = False
        except (TypeError, ValueError):
            ok = True
        check(f"{label} is refused", ok)
    try:
        PL.canonical({"schema": PL.PLAN_PAYLOAD_SCHEMA, "items": [{1: "int key"}]})
        ok = False
    except TypeError:
        ok = True
    check("a non-string object key is refused (json.dumps would have coerced it after sorting it as a number)", ok)
    check("a tuple is an array; a finite float is written in its shortest round-trip form",
          PL.canonical({"t": (1, 2), "f": 0.1}) == b'{"f":0.1,"t":[1,2]}')
    check("verify() uses the payload schema it is given (none recorded: /1); an unknown schema is refused, never guessed",
          PL.verify([], hashlib.sha256(PL.canonical(PL.payload([]))).hexdigest(), None)
          and _refuses(lambda: PL.verify([], "0" * 64, "fileorganizer.p3.plan-payload/9")))


def _refuses(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


# ---------------------------------------------------------------------------
# 4. Revisions locked by the B15, B16 and B17 code
# ---------------------------------------------------------------------------

DRIVER = r'''
import json, sys, os
from pathlib import Path
scripts, app_root, corpus = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, scripts); sys.path.insert(0, os.path.join(scripts, "Database"))
from Phase2.runner import RunRequest, RunWorker, PRESCAN, DUPLICATES, ANALYSIS
from Phase2.core import connect
from Phase3 import evidence as E, store as S, resolve as R, routing as RT, plans as PL
roots = [str(corpus / "Docs"), str(corpus / "Backup")]
outs = [RunWorker(app_root, None, RunRequest(PRESCAN, "Pre-Scan", source_roots=roots, project_name="Old")).run(),
        RunWorker(app_root, app_root / "Projects" / "Old", RunRequest(DUPLICATES, "Find My Duplicates")).run()]
st_meta = hasattr(S.DecisionStore, "record_metadata_decision")
if st_meta:
    outs.append(RunWorker(app_root, app_root / "Projects" / "Old", RunRequest(ANALYSIS, "Analyze", analyzer_keys=["office"])).run())
conn = connect(app_root / "Projects" / "Old", write=True)
st = S.DecisionStore(conn, actor_id="old-build", session_id="session:old")
ev = E.load_evidence(conn)
fids = {r[1].replace("/", "\\").rsplit("\\", 1)[-1] + "@" + r[2]: str(r[0]) for r in conn.execute(
    "SELECT fp.file_path_id, fp.relative_path, sr.root_path FROM file_path fp JOIN source_root sr ON sr.source_root_id = fp.source_root_id")}
def fid(name, root):
    return next(v for k, v in fids.items() if k.startswith(name + "@") and k.endswith(root))
for name in ("R\u00e9sum\u00e9.txt", "\u65e5\u672c\u8a9e.txt", "plain.txt"):
    st.record_decision("redundant_location", "file_location", fid(name, "Backup"))
    st.record_decision("must_keep_location", "file_location", fid(name, "Docs"))
if st_meta:
    st.record_metadata_decision("desired_source_metadata_set", fid("report.docx", "Docs"), "creator", "Thomas")
ev = E.load_evidence(conn)
proj = R.resolve_all(ev)
draft = PL.build(ev, proj, RT.route_all(ev, proj, ev.now))
rec = st.record_plan_revision(draft, name="locked by an older build")
lk = st.lock_plan_revision(rec["plan_revision_id"])
print(json.dumps({"ok": all(o.ok for o in outs), "revision": rec["plan_revision_id"], "fingerprint": lk["fingerprint"],
                  "items": len(draft.items), "kinds": sorted({i.action_kind for i in draft.items})}))
'''


def build_corpus(base: Path):
    docs, backup = base / "Docs", base / "Backup"
    docs.mkdir(parents=True)
    backup.mkdir(parents=True)
    for d in (docs, backup):
        (d / "R\u00e9sum\u00e9.txt").write_text("r\u00e9sum\u00e9 " * 40, encoding="utf-8")
        (d / "\u65e5\u672c\u8a9e.txt").write_text("\u65e5\u672c " * 40, encoding="utf-8")
        (d / "plain.txt").write_text("plain " * 40, encoding="utf-8")
    import docx
    doc = docx.Document()
    doc.add_paragraph("A report.")
    doc.core_properties.author = "Alice"
    doc.save(str(docs / "report.docx"))


def materialize(tag, dest: Path):
    dest.mkdir(parents=True)
    arc = subprocess.run(["git", "-C", str(ROOT), "-c", "core.autocrlf=false", "archive", f"refs/tags/{tag}"], capture_output=True)
    if arc.returncode != 0:
        return False
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(arc.stdout)) as t:
        t.extractall(dest, filter="data")
    return True


def test_old_locks(tmp: Path):
    section("4. Revisions locked by the B15, B16 and B17 code verify in this build (under the schema they were locked with)")
    import fo_db
    from Phase2.core import connect
    from Phase3 import store as S, evidence as E, resolve as R, routing as RT
    corpus = tmp / "FpCorpus"
    build_corpus(corpus)
    (tmp / "driver.py").write_text(DRIVER, encoding="utf-8")
    calls_all = []
    for tag, schema in (("phase3-b15", 13), ("phase3-b16", 14), ("phase3-b17", 15)):
        tree = tmp / tag
        if not materialize(tag, tree):
            print(f"  SKIP  {tag}: the tag could not be materialized from git")
            continue
        app_root = tmp / f"{tag}-root"
        (app_root / "Projects").mkdir(parents=True)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        run = subprocess.run([sys.executable, str(tmp / "driver.py"), str(tree / "Scripts"), str(app_root), str(corpus)],
                             capture_output=True, text=True, encoding="utf-8", env=env, timeout=900)
        line = next((x for x in run.stdout.splitlines()[::-1] if x.startswith("{")), None)
        if line is None:
            check(f"{tag}: the old build locked a revision", False, (run.stderr or run.stdout)[-600:])
            continue
        old = json.loads(line)
        pd = app_root / "Projects" / "Old"
        before = int(__import__("sqlite3").connect(str(pd / "Database" / "FileOrganizer.db")).execute("PRAGMA user_version").fetchone()[0])
        c, _row = fo_db.open_project(str(pd))
        c.close()
        conn = connect(pd, write=True)
        st = S.DecisionStore(conn, actor_id="b18", session_id="session:fp")
        rev, items = st.plan_revision(old["revision"])
        calls = []
        same = old_canonical(PL.payload(items), calls) == PL.canonical(PL.payload(items))
        calls_all += calls
        check(f"{tag}: its code locked a revision on schema {schema} ({old['items']} items: {', '.join(old['kinds'])}, non-ASCII "
              "paths among them); this build migrated the project to 16 and it verifies against the fingerprint it was "
              "locked with, under /1 (its basis records no schema)",
              old["ok"] and before == schema and conn.execute("PRAGMA user_version").fetchone()[0] == 16
              and rev["fingerprint"] == old["fingerprint"] and "payload_schema" not in rev["basis"]
              and st.verify_plan_revision(old["revision"]) is True and PL.fingerprint(items) == old["fingerprint"] and same)
        if tag == "phase3-b17":
            ev = E.load_evidence(conn)
            proj = R.resolve_all(ev)
            draft = PL.build(ev, proj, RT.route_all(ev, proj, ev.now))
            rec = st.record_plan_revision(draft, name="locked by B18")
            lk = st.lock_plan_revision(rec["plan_revision_id"])
            rev2, _i = st.plan_revision(rec["plan_revision_id"])
            check("a revision locked by this build on the same project records its payload schema in its basis and verifies; "
                  "the same state fingerprints as the B17 code's revision did (the encoding did not change)",
                  rev2["basis"]["payload_schema"] == PL.PLAN_PAYLOAD_SCHEMA and st.verify_plan_revision(rec["plan_revision_id"])
                  and lk["fingerprint"] == old["fingerprint"], f"{lk['fingerprint']} vs {old['fingerprint']}")
        conn.close()
    check("B15-B17's default=str was never called for any payload these builds locked", calls_all == [], str(calls_all))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="p3fp_"))
    try:
        test_vectors()
        test_old_locks(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 FINGERPRINT (B18): {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
