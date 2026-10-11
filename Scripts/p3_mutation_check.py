#!/usr/bin/env python3
r"""B18 (extended in B19): mutation sensitivity (task 1.2, M5) -- break each safety-critical rule
on purpose and show the suites fail.

Each mutant is ONE exact text substitution in the product source, applied to a
sandboxed COPY of Scripts\ and Resources\ in a temporary folder (the real tree
is never touched; its every file is hashed before and after the run). The
mutated copy runs the suite slice that owns the rule, in its own process. A
mutant is KILLED when the slice reports at least one failed check (or raises);
it SURVIVES when every check passes -- a survivor is a finding: a test is
missing, and it is added (the mutant is never weakened). Each slice is also run
unmutated first (the control): a slice that fails without a mutant proves
nothing.

The table this prints is the closeout's "Mutation killed?" column
(Docs\Handoffs\P3_CLOSEOUT.md §3).

Run:  python Scripts\p3_mutation_check.py            (every mutant; ~30-60 min)
      python Scripts\p3_mutation_check.py M01 M14    (just these)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent

#: slice id -> (suite module, function, needs a temp folder)
SLICES = {
    "reg.resolver": ("p3_regression", "test_resolver", False),
    "reg.routing": ("p3_regression", "test_routing_model", False),
    "reg.bulk_model": ("p3_regression", "test_bulk_model", False),
    "reg.project": ("p3_regression", "test_project", True),
    "reg.bulk_project": ("p3_regression", "test_bulk_project", True),
    "fixtures": ("p3_fixture_check", "main", False),
    "vis.resolver": ("p3_visual_check", "test_resolver", False),
    "plan.builder": ("p3_plan_check", "test_builder", False),
    "plan.retention": ("p3_plan_check", "test_retention_rule", False),
    "md.integrity": ("p3_metadata_check", "test_capability_and_integrity", False),
    "md.items": ("p3_metadata_check", "test_plan_items", False),
    "mv.project": ("p3_metadata_view_check", "test_project", True),
    "fp.vectors": ("p3_fingerprint_check", "test_vectors", False),
    "enc.detector": ("p3_encryption_check", "test_detector", True),
    "enc.all": ("p3_encryption_check", "main", False),
    "reval.rule": ("p3_revalidation_check", "test_rule", False),
    "reval.all": ("p3_revalidation_check", "main", False),
    "close.pure": ("p3_closeout_check", "test_conflicts_pure", False),
    "close.all": ("p3_closeout_check", "main", False),
    # B19
    "dangle.pure": ("p3_dangling_check", "test_pure", False),
    "dangle.root": ("p3_dangling_check", "test_root_rename", True),
    "dangle.offline": ("p3_dangling_check", "test_offline", True),
    "relabel.hand": ("p3_relabel_check", "test_hand_built", False),
    "schema.committed": ("p3_registry_schema_check", "test_committed", False),
    "schema.cross": ("p3_registry_schema_check", "test_cross_check", False),
}

#: (id, rule, file under Scripts, old text, new text, slices)
MUTANTS = [
    ("M01", "Protection gate: a protection policy covering a copy is in force unless overridden",
     "Phase3/resolve.py", "    in_force = [p for p in covering if p.policy_version_id not in overrides]\n", "    in_force = []\n",
     ["reg.resolver", "fixtures"]),
    ("M02", "Keeper floor: a plan never removes the last file location of a content",
     "Phase3/resolve.py", "    if member_ids and remaining < MINIMUM_FILE_LOCATIONS:", "    if member_ids and remaining < 0:",
     ["reg.resolver"]),
    ("M03", "Physical-copy floor: a plan never removes every physical copy",
     "Phase3/resolve.py", "        if present and remaining_objects < MINIMUM_PHYSICAL_COPIES and remaining >= MINIMUM_FILE_LOCATIONS:",
     "        if False:", ["reg.resolver", "close.pure"]),
    ("M04", "Physical-copy floor in the Plan Builder's evidence preview",
     "Phase3/plans.py", '            _set(it, "minimum_physical_copy_count_satisfied", "pass" if left else "fail",',
     '            _set(it, "minimum_physical_copy_count_satisfied", "pass",', ["plan.builder", "plan.retention"]),
    ("M05", "Unknown never passes: _settle blocks a hard precondition whose result is unknown",
     "Phase3/plans.py", '    unknown = [c for c in hard if c.result == "unknown"]\n', "    unknown = []\n",
     ["plan.builder", "md.items"]),
    ("M06", "Frozen batch membership: a batch records exactly the membership its preview showed",
     "Phase3/store.py", "            for cand in preview.candidates:\n                self.conn.execute(\n"
                        "                    \"INSERT INTO p3_bulk_member",
     "            for cand in [c for c in preview.candidates if c.disposition == DISP_DECIDED]:\n                self.conn.execute(\n"
     "                    \"INSERT INTO p3_bulk_member", ["reg.bulk_project", "reval.all"]),
    ("M07", "Preserve, not overwrite: a bulk action never supersedes an explicit incompatible decision",
     "Phase3/bulk.py", '                return DISP_PRESERVED, f"{decision_kind(d.kind).label} (#{d.decision_id}) stands"',
     '                return DISP_DECIDED, f"{decision_kind(d.kind).label} (#{d.decision_id}) stands"', ["reg.bulk_model"]),
    ("M08", "Withdrawal, not deletion: withdrawing adds a withdrawal row; the decision row stays",
     "Phase3/store.py", '            "INSERT INTO p3_decision_withdrawal(decision_id, operation_id, reason) VALUES(?,?,?)",\n'
                        "            (int(decision_id), operation_id, reason))",
     '            "DELETE FROM p3_decision WHERE decision_id = ? AND ? IS NOT NULL AND ? IS NULL",\n'
     "            (int(decision_id), operation_id, None))", ["reg.project"]),
    ("M09", "The pair chain guard: equivalence is never inferred along a chain of pairs",
     "Phase3/pairs.py", "            others = [q for q in given_up.get(keeper, []) if q != pid]\n            if others:",
     "            others = [q for q in given_up.get(keeper, []) if q != pid]\n            if False:", ["vis.resolver", "fixtures"]),
    ("M10", "No retaining decision: a removal is blocked while any decision keeps the copy",
     "Phase3/plans.py", "    retaining = retaining_decisions(ev, proj, index, pairs_by_image, lid)\n", "    retaining = []\n",
     ["plan.retention"]),
    ("M11", "The fingerprint covers every item",
     "Phase3/plans.py", "    for it in sorted(items, key=lambda i: i.item_no):\n        out.append({",
     "    for it in sorted(items, key=lambda i: i.item_no)[:1]:\n        out.append({", ["fp.vectors", "plan.builder"]),
    ("M12", "The fingerprint covers every field of an item (here: its state)",
     "Phase3/plans.py", '"post": list(it.postconditions), "deps": list(it.depends_on), "state": it.state,',
     '"post": list(it.postconditions), "deps": list(it.depends_on),', ["fp.vectors"]),
    ("M13", "Metadata items are never made from Leave Unchanged (or project metadata)",
     "Phase3/metadata.py", "        if q is not None and d.kind in PLANNED_METADATA_KINDS:", "        if q is not None:",
     ["md.items"]),
    ("M14", "Zero source write: building a plan writes no source file",
     "Phase3/plans.py", "    active = active_decisions(ev.decisions)\n    index = R._index_decisions(active)\n    policies = active_policies(ev.policies)\n"
                        "    pairs_by_image = {}\n    for pp in getattr(proj, \"pairs\", ()):\n        pairs_by_image.setdefault(pp.a_id, []).append(pp)\n"
                        "        pairs_by_image.setdefault(pp.b_id, []).append(pp)\n    groups, pairs, excluded = population(proj, routing)",
     "    active = active_decisions(ev.decisions)\n    index = R._index_decisions(active)\n    policies = active_policies(ev.policies)\n"
     "    for _l in [l for l in ev.locations.values() if l.present][:1]:\n        open(_l.path, \"ab\").close()\n"
     "    pairs_by_image = {}\n    for pp in getattr(proj, \"pairs\", ()):\n        pairs_by_image.setdefault(pp.a_id, []).append(pp)\n"
     "        pairs_by_image.setdefault(pp.b_id, []).append(pp)\n    groups, pairs, excluded = population(proj, routing)",
     ["mv.project"]),
    ("M15", "Integrity blocker: a signed or certified file blocks a metadata write",
     "Phase3/metadata.py", '        out[SIGNED_KEY] = ("fail", f"{q.signature_state or \'signed\'}',
     '        out[SIGNED_KEY] = ("pass", f"{q.signature_state or \'signed\'}', ["md.integrity", "md.items"]),
    ("M16", "Integrity blocker: an encrypted file blocks a metadata write",
     "Phase3/metadata.py", '        out[ENCRYPTED_KEY] = ("fail", "encrypted or password-protected',
     '        out[ENCRYPTED_KEY] = ("pass", "encrypted or password-protected', ["enc.all"]),
    ("M17", "Integrity blocker: a hard-linked file blocks a generic metadata write",
     "Phase3/metadata.py", '        out[HARDLINK_KEY] = ("fail", "hard-linked', '        out[HARDLINK_KEY] = ("pass", "hard-linked',
     ["md.integrity", "md.items"]),
    ("M18", "Routing precedence: conflict > needs revalidation > blocked > deferred > ready > resolved > unresolved",
     "Phase3/routing.py", "ROUTE_ORDER = (CONFLICT, NEEDS_REVALIDATION, BLOCKED, DEFERRED,",
     "ROUTE_ORDER = (CONFLICT, NEEDS_REVALIDATION, DEFERRED, BLOCKED,", ["reg.routing"]),
    # -- B18's own rules --------------------------------------------------------------------
    ("M19", "Material change (B18): a changed observed value sends a metadata decision back",
     "Phase3/metadata.py", "            if value_key(bound) != value_key(q.observed_values):", "            if False:", ["reval.rule"]),
    ("M20", "Material change (B18): a changed file (new observation, different content) sends it back",
     "Phase3/metadata.py", "        if bound_content is None or q.content_id is None or str(bound_content) != str(q.content_id):",
     "        if False:", ["reval.rule"]),
    ("M21", "Material change (B18): a re-read with the same value is NOT a change (never compare ids when values are known)",
     "Phase3/metadata.py", '        bound = b.get("observed_values") if "observed_values" in b else b.get("resolved_values")',
     "        bound = None", ["reval.rule"]),
    ("M22", "Encryption evidence (B18): Word's FIB fEncrypted bit is read",
     "OfficeAnalysis.py", '    return "encrypted" if _u16(head, 0x0A) & WORD_F_ENCRYPTED else "not_encrypted"',
     '    return "not_encrypted"', ["enc.detector"]),
    ("M23", "Encryption evidence (B18): the structured column is read first; the error text only for NULL",
     "Phase3/evidence.py", "        if column is not None:\n            na = (\"source_not_encrypted\",) if column == \"not_applicable\" else ()",
     "        if False:\n            na = (\"source_not_encrypted\",) if column == \"not_applicable\" else ()", ["enc.all"]),
    ("M24", "Bulk Confirm (B18): the same decision re-recorded -- never another kind or value",
     "Phase3/bulk.py", "                c.decisions = ((d.kind, METADATA_QUESTION, qid, d.value),)",
     "                c.decisions = ((\"desired_source_metadata_set\", METADATA_QUESTION, qid, \"Confirmed\"),)", ["reval.all"]),
    ("M25", "The commit guard: a batch is refused when the decision record moved since its preview",
     "Phase3/store.py", "            if now_mark != preview.record_mark:", "            if False:", ["reval.all", "reg.bulk_project"]),
    ("M26", "Undo of a Confirm (B18) puts every question back as it was (re-applies the superseded decision)",
     "Phase3/store.py", '        reapply = self._confirm_originals(active) if b["action"] == METADATA_CONFIRM else []', "        reapply = []",
     ["reval.all"]),
    ("M27", "The queue's cache (B18) is never stale: withdrawals are caught up",
     "Phase3/refresh.py", '        gone = {did for wid, did in E.withdrawals_after(conn, was["withdrawal"], metadata=True) if wid <= now["withdrawal"]}',
     "        gone = set()", ["reval.all"]),
    ("M28", "The path rule (B18): folder policies compare in NFC",
     "Phase3/model.py", '    s = unicodedata.normalize("NFC", str(path or "")).replace("/", "\\\\")', '    s = str(path or "").replace("/", "\\\\")',
     ["close.pure"]),
    ("M29", "Plan lifecycle: only a previewed revision can be locked (lock twice, lock obsolete refused)",
     "Phase3/store.py", '        if rev["status"] not in ("previewed", "review_ready"):', "        if False:", ["close.all"]),
    ("M30", "Plan lifecycle (B18): a re-analysis between preview and lock is refused",
     "Phase3/store.py", '        if "analyzer_mark" in rev["basis"] and rev["basis"]["analyzer_mark"] != ev_mod.analyzer_mark(self.conn):',
     "        if False:", ["close.all"]),
    ("M31", "Status is a cache of the plan events: obsolete sets the column with the event",
     "Phase3/store.py", "UPDATE p3_plan_revision SET status = 'obsolete', obsolete = 1 WHERE plan_revision_id = ?",
     "UPDATE p3_plan_revision SET status = 'obsolete' WHERE plan_revision_id = ?", ["close.all"]),
    ("M32", "Decision lifecycle: a decision supersedes only the head of its own conflict domain",
     "Phase3/store.py", "                 if decision_kind(r[\"decision_kind\"]).domain_key(json.loads(r[\"value_json\"])) == domain]",
     "                 ]", ["close.all"]),
    # -- B19: renamed roots and folders (the dangling guard) --------------------------------
    ("M33", "Dangling guard (B19): every removal carries no_dangling_protection while a protection dangles",
     "Phase3/plans.py", "    if not dangling:\n        return\n    c = Precondition(DANGLING_KEY",
     "    return\n    c = Precondition(DANGLING_KEY", ["dangle.pure"]),
    ("M34", "Dangling guard (B19): its result is unknown -- never pass",
     "Phase3/plans.py", '    c.result = "unknown"\n    c.detail = (" ".join(s.words() for s in dangling)',
     '    c.result = "pass"\n    c.detail = (" ".join(s.words() for s in dangling)', ["dangle.pure"]),
    ("M35", "Dangling detection (B19): a folder scope with nothing under it in a scanned root is dangling",
     "Phase3/scopes.py", "    return out(SCOPE_DANGLING, reason, suggest(key, facts))",
     "    return out(SCOPE_PRESENT, reason, suggest(key, facts))", ["dangle.pure"]),
    ("M36", "Renamed is not offline (B19): a root missing from a volume that answered is absent (dangling)",
     "Phase3/evidence.py", "            st = RootState(key, path, ROOT_ABSENT, f\"{path} was not found",
     "            st = RootState(key, path, ROOT_UNAVAILABLE, f\"{path} was not found", ["dangle.root"]),
    ("M37", "Offline is not renamed (B19): a root whose volume was not there is unavailable, never dangling",
     "Phase3/evidence.py", "        elif run_root is not None and run_root[0] == 0 and run_root[1] in _VOLUME_TYPES and run_root[2]:",
     "        elif run_root is not None and run_root[0] == 0:", ["dangle.offline"]),
    ("M38", "The suggestion is identity only (B19): a copy -- a new file index -- is never suggested",
     "Phase3/scopes.py", "        for new_key, new_path in facts.identities.get(phys, ()):",
     "        for new_key, new_path in [x for v in facts.identities.values() for x in v]:", ["dangle.pure"]),
    # -- B19: I15, identifiers never order or decide anything -------------------------------
    ("M39", "I15 (B19): a queue's 'oldest decision' is recording sequence, never the smallest id",
     "Phase3/routing.py", "    seqs = [sequence[d] for c in route.conditions for d in c.decision_ids if d in sequence]\n"
                          "    return min(seqs) if seqs else float(\"inf\")",
     "    return min((str(d) for c in route.conditions for d in c.decision_ids), default=\"~\")", ["relabel.hand"]),
    ("M40", "I15 (B19): which protection a copy shows as its rule follows the policies' content, never their ids",
     "Phase3/resolve.py", "    covering = sorted((p for p in protect_policies if _policy_covers(p, loc)), key=policy_order)",
     "    covering = [p for p in protect_policies if _policy_covers(p, loc)]", ["relabel.hand"]),
    ("M41", "I15 (B19): a pair's place in the list follows its images' display order, not which has the lower id",
     "Phase3/pairs.py", "        a, b = sorted((_display(ev, pp.a_id), _display(ev, pp.b_id)))",
     "        a, b = _display(ev, pp.a_id), _display(ev, pp.b_id)", ["relabel.hand"]),
    # -- B19: the registry as JSON Schema ----------------------------------------------------
    ("M42", "The schema (B19): registry_digest covers registry.schema.json's bytes",
     "Phase3/registry.py", '        "schema_sha256": hashlib.sha256(schema_bytes()).hexdigest(),', "", ["schema.committed"]),
    ("M43", "The schema (B19): the code accepts a value exactly when the schema does (keep_both ids carry no whitespace)",
     "Phase3/registry.py", "    if not all(isinstance(v, str) and v and v == v.strip() for v in ids):",
     "    if not all(isinstance(v, str) and v.strip() for v in ids):", ["schema.cross"]),
    ("M44", "W2 (B19): a bulk folder scope that matched more than one on-disk spelling warns",
     "Phase3/bulk.py", "    if len(found) < 2:\n        return None", "    if True:\n        return None", ["close.all"]),
]


# ---------------------------------------------------------------------------
# The child: one slice, maybe one mutant, in a sandboxed tree
# ---------------------------------------------------------------------------

def child(tree: Path, slice_id: str):
    scripts = tree / "Scripts"
    sys.path.insert(0, str(scripts))
    sys.path.insert(0, str(scripts / "Database"))
    os.chdir(str(scripts))
    mod_name, fn, needs_tmp = SLICES[slice_id]
    import importlib
    import io
    import contextlib
    out = io.StringIO()
    error = None
    tmp = Path(tempfile.mkdtemp(prefix="p3mut_slice_"))
    try:
        with contextlib.redirect_stdout(out):
            mod = importlib.import_module(mod_name)
            try:
                res = getattr(mod, fn)(tmp) if needs_tmp else getattr(mod, fn)()
                if fn == "main" and res not in (0, None):
                    error = f"main returned {res}"
            except SystemExit as exc:
                if exc.code not in (0, None):
                    error = f"exit {exc.code}"
    except Exception as exc:                                        # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    failed, passed, names = 0, 0, []
    for name, m in list(sys.modules.items()):
        if name.startswith("p3_") and hasattr(m, "RESULTS"):
            for n, ok, _d in m.RESULTS:
                if ok:
                    passed += 1
                else:
                    failed += 1
                    names.append(n[:120])
    sys.__stdout__.write(json.dumps({"failed": failed, "passed": passed, "error": error, "first_failures": names[:3]}) + "\n")


# ---------------------------------------------------------------------------
# The parent
# ---------------------------------------------------------------------------

def tree_hashes():
    out = {}
    for base in (ROOT / "Scripts", ROOT / "Resources"):
        for p in sorted(base.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                out[str(p.relative_to(ROOT))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def sandbox(dest: Path):
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "InstallationCheckReport.txt", "DependencyCheckReport.txt")
    shutil.copytree(ROOT / "Scripts", dest / "Scripts", ignore=ignore)
    shutil.copytree(ROOT / "Resources", dest / "Resources", ignore=ignore)


def run_slice(tree: Path, slice_id: str, timeout=1800):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    t = time.time()
    try:
        p = subprocess.run([sys.executable, str(tree / "Scripts" / "p3_mutation_check.py"), "--child", str(tree), slice_id],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)
        line = next((x for x in p.stdout.splitlines()[::-1] if x.startswith("{")), None)
        res = json.loads(line) if line else {"failed": 0, "passed": 0, "error": ("no result: " + p.stderr[-300:])}
    except subprocess.TimeoutExpired:
        res = {"failed": 0, "passed": 0, "error": "timeout"}
    res["seconds"] = round(time.time() - t, 1)
    return res


def main(argv):
    only = [a for a in argv if a.startswith("M")]
    mutants = [m for m in MUTANTS if not only or m[0] in only]
    before = tree_hashes()
    work = Path(tempfile.mkdtemp(prefix="p3mut_"))
    results, controls = [], {}
    try:
        clean = work / "clean"
        sandbox(clean)
        # Every mutant's text must be found exactly once (a mutant that does not apply proves nothing).
        for mid, _rule, f, old, _new, _sl in mutants:
            n = (clean / "Scripts" / f).read_text(encoding="utf-8").count(old)
            if n != 1:
                print(f"  ERROR  {mid}: its text occurs {n} times in {f}")
                return 2
        print("Controls (each slice unmutated must pass):")
        for sl in sorted({s for m in mutants for s in m[5]}):
            controls[sl] = run_slice(clean, sl)
            c = controls[sl]
            print(f"  {'OK  ' if not c['failed'] and not c['error'] else 'BAD '} {sl}: {c['passed']} passed, {c['failed']} failed"
                  f"{' -- ' + c['error'] if c['error'] else ''} ({c['seconds']} s)")
        print("\nMutants:")
        for mid, rule, f, old, new, slices in mutants:
            tree = work / mid
            sandbox(tree)
            p = tree / "Scripts" / f
            p.write_text(p.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
            outcome = []
            for sl in slices:
                r = run_slice(tree, sl)
                outcome.append((sl, r))
                if r["failed"] or r["error"]:
                    break                               # killed: no need to run its other slices
            killed = next(((sl, r) for sl, r in outcome if r["failed"] or r["error"]), None)
            results.append({"id": mid, "rule": rule, "file": f, "killed": killed is not None,
                            "by": killed[0] if killed else None,
                            "evidence": (f"{killed[1]['failed']} check(s) failed" + (f" / {killed[1]['error']}" if killed[1]["error"] else "")
                                         + (f": {killed[1]['first_failures'][0]}" if killed[1]["first_failures"] else ""))
                            if killed else "all checks passed",
                            "seconds": sum(r["seconds"] for _s, r in outcome)})
            x = results[-1]
            print(f"  {'KILLED  ' if x['killed'] else 'SURVIVED'} {mid} {rule}\n           by {x['by']}: {x['evidence'][:200]}")
            shutil.rmtree(tree, ignore_errors=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    after = tree_hashes()
    bad_controls = [s for s, c in controls.items() if c["failed"] or c["error"]]
    survived = [r["id"] for r in results if not r["killed"]]
    untouched = before == after
    (SCRIPTS.parent / "Logs").mkdir(exist_ok=True)
    report = {"mutants": results, "controls": controls, "tree_untouched": untouched}
    (SCRIPTS.parent / "Logs" / "p3_mutation_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"\n{'=' * 70}")
    print(f"Controls clean: {len(controls) - len(bad_controls)}/{len(controls)}; mutants killed: "
          f"{len(results) - len(survived)}/{len(results)}; real tree untouched: {untouched}")
    if survived:
        print("SURVIVED (findings): " + ", ".join(survived))
    print(f"P3 MUTATION (B19): {(len(results) - len(survived)) + (len(controls) - len(bad_controls)) + int(untouched)}/"
          f"{len(results) + len(controls) + 1} checks passed")
    return 0 if not survived and not bad_controls and untouched else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(Path(sys.argv[2]), sys.argv[3])
    else:
        raise SystemExit(main(sys.argv[1:]))
