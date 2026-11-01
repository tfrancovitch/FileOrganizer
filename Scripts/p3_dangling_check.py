r"""B19 -- renamed roots and folders: dangling policies (Phase3\scopes.py).

    py -3 Scripts\p3_dangling_check.py

The gap: rename or move a protected root or folder on disk and scan again,
and through B18 the protection silently covered nothing -- the renamed files
could be marked redundant and planned for removal. The rule (synthesis §4):

  1. never silently re-map: a policy whose scope matches nothing in the
     current scan is DANGLING, named on the Decide page, in the Policies
     dialog and in the Plan preview; while an active protection is dangling
     every removal carries the hard precondition no_dangling_protection
     (unknown -- never pass); a dangling prefer / avoid is a warning only;
  2. a person re-points it (a new policy version) or retires it; identity
     evidence (volume serial + file index) may SUGGEST where the files went,
     never applied automatically.

  1. The rule, pure: present / unavailable / dangling for root and folder
     scopes; the suggestion comes from identity alone (a copy is no match);
     the plan precondition, its detail, the summary; a plan with no dangling
     policy is byte-for-byte what it was without the rule; a locked revision
     goes obsolete when a protection dangles.
  2. A protected ROOT renamed on disk and rescanned (a real project): dangling;
     every removal blocked on no_dangling_protection; the suggestion names the
     new root; nothing re-mapped by any system path; re-pointing clears the
     block and the protection covers the renamed files; the old root's
     last-known files route blocked (they are not current); retiring (a
     second project) clears it too.
  3. A protected FOLDER renamed inside a scanned root: the same, for a
     folder-subtree policy; a dangling PREFER policy is a warning only.
  4. An OFFLINE root (a subst drive removed): unavailable, not dangling -- no
     precondition; its copies route blocked.
  5. Links and mounts: a junction inside a protected folder is recorded there
     and is not followed out of the scope; a folder holding only a junction,
     or only an empty folder, is present; a hard link is one location per
     path; the mount-point and symlink tags are the ones Phase 1 never follows.
  6. The window: the Decide page's banner, the Policies dialog's column and
     Re-point... (the suggestion fills the field only on request; nothing is
     recorded until Re-point), the Plan page's line.
"""
from __future__ import annotations

import json
import os
import shutil
import string
import subprocess
import sys
import tempfile
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
from Phase3 import scopes as SC                                     # noqa: E402
from Phase3.model import PolicyVersion, path_key                    # noqa: E402

RESULTS = []
KEY = PL.DANGLING_KEY


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def pv(pvid, kind, scope, effect=None, active=True):
    if "path" in scope and "path_key" not in scope:
        scope = {"path_key": path_key(scope["path"]), "path": scope["path"]}
    if "root_path" in scope and "root_key" not in scope:
        scope = {"root_key": path_key(scope["root_path"]), "root_path": scope["root_path"]}
    return PolicyVersion(pvid, "P" + pvid, kind, scope, effect or {}, 1, active)


# -- 1. the rule, pure ------------------------------------------------------------------------

def facts(**kw):
    f = SC.ScopeFacts(roots={
        "c:\\r1": SC.RootState("c:\\r1", "C:\\R1", SC.ROOT_SCANNED),
        "d:\\gone": SC.RootState("d:\\gone", "D:\\Gone", SC.ROOT_ABSENT, "D:\\Gone was not found although its volume was there"),
        "e:\\ext": SC.RootState("e:\\ext", "E:\\Ext", SC.ROOT_UNAVAILABLE, "offline"),
        "d:\\new": SC.RootState("d:\\new", "D:\\New", SC.ROOT_SCANNED)})
    f.present = sorted(["c:\\r1\\docs\\a.txt", "c:\\r1\\kept\\b.txt", "c:\\r1\\kept\\sub\\c.txt", "d:\\new\\x.jpg",
                        "d:\\new\\sub\\y.jpg", "c:\\r1\\docs2\\z.txt", "c:\\r1\\copy\\b.txt"])
    f.unknown = sorted(["c:\\r1\\locked\\q.txt"])
    f.folders = sorted(["c:\\r1\\empty", "c:\\r1\\linkonly\\j"])
    f.identities = {"v:1": [("d:\\new\\x.jpg", "D:\\New\\x.jpg")], "v:2": [("d:\\new\\sub\\y.jpg", "D:\\New\\sub\\y.jpg")],
                    "v:3": [("c:\\r1\\kept\\b.txt", "C:\\R1\\Kept\\b.txt")],
                    "v:4": [("c:\\r1\\kept\\sub\\c.txt", "C:\\R1\\Kept\\sub\\c.txt")],
                    "v:9": [("c:\\r1\\copy\\b.txt", "C:\\R1\\Copy\\b.txt")]}
    f.lost = [("d:\\gone\\x.jpg", "v:1"), ("d:\\gone\\sub\\y.jpg", "v:2"), ("c:\\r1\\keep\\b.txt", "v:3"),
              ("c:\\r1\\keep\\sub\\c.txt", "v:4"), ("c:\\r1\\copied\\b.txt", "v:8")]
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def test_pure():
    section("1. The rule, pure: present / unavailable / dangling; the suggestion; the plan")
    f = facts()
    st = lambda p: SC.status(p, f)                                                    # noqa: E731
    check("a root policy on a scanned root is present",
          st(pv("1", "protect_source_root", {"root_path": "C:\\R1"})).state == REG.SCOPE_PRESENT)
    gone = st(pv("2", "protect_source_root", {"root_path": "D:\\Gone"}))
    check("a root policy whose root was observed absent (volume there, folder not) is DANGLING, with the reason",
          gone.state == REG.SCOPE_DANGLING and "volume was there" in gone.reason)
    check("...and the suggestion names the new root from identity: both files (volume serial + file index) seen under "
          "D:\\New with the same relative paths; it is a scanned root; labelled as a suggestion",
          gone.suggestion is not None and gone.suggestion.scope_key == "d:\\new" and gone.suggestion.path == "D:\\New"
          and gone.suggestion.is_root and (gone.suggestion.matches, gone.suggestion.of) == (2, 2)
          and SC.SUGGESTION_LABEL in gone.suggestion.words
          and SC.SUGGESTION_LABEL == "suggested \u2014 same file identities (volume serial + file index) seen under this new path")
    off = st(pv("3", "protect_source_root", {"root_path": "E:\\Ext"}))
    check("a root that is merely OFFLINE is unavailable -- not dangling, no suggestion", off.state == REG.SCOPE_UNAVAILABLE
          and off.suggestion is None)
    check("a root policy naming no root of the project is dangling",
          st(pv("4", "protect_source_root", {"root_path": "F:\\Never"})).state == REG.SCOPE_DANGLING)
    keep = st(pv("5", "protect_folder_subtree", {"path": "C:\\R1\\Keep"}))
    check("a folder policy whose folder is gone from a scanned root is DANGLING; the suggestion is the renamed folder "
          "(a folder inside a root -- a folder policy can point there)",
          keep.state == REG.SCOPE_DANGLING and keep.suggestion is not None and keep.suggestion.path == "C:\\R1\\Kept"
          and not keep.suggestion.is_root and (keep.suggestion.matches, keep.suggestion.of) == (2, 2))
    check("a folder with present files, a folder holding only an empty folder, and one holding only a link are present",
          all(st(pv(str(10 + i), "protect_folder_subtree", {"path": p})).state == REG.SCOPE_PRESENT
              for i, p in enumerate(("C:\\R1\\Docs", "C:\\R1\\Empty", "C:\\R1\\LinkOnly", "C:\\R1"))))
    check("the folder boundary is a whole segment: C:\\R1\\Doc (not C:\\R1\\Docs, not C:\\R1\\Docs2) is dangling",
          st(pv("20", "protect_folder_subtree", {"path": "C:\\R1\\Doc"})).state == REG.SCOPE_DANGLING)
    check("a folder whose files could not be read in the latest scan is unavailable, not dangling",
          st(pv("21", "protect_folder_subtree", {"path": "C:\\R1\\Locked"})).state == REG.SCOPE_UNAVAILABLE)
    check("a folder inside an offline root is unavailable; inside an absent root it is dangling",
          st(pv("22", "protect_folder_subtree", {"path": "E:\\Ext\\A"})).state == REG.SCOPE_UNAVAILABLE
          and st(pv("23", "protect_folder_subtree", {"path": "D:\\Gone\\Sub"})).state == REG.SCOPE_DANGLING)
    check("a folder under no root of the project is dangling",
          st(pv("24", "protect_folder_subtree", {"path": "G:\\Elsewhere"})).state == REG.SCOPE_DANGLING)
    copied = st(pv("25", "protect_folder_subtree", {"path": "C:\\R1\\Copied"}))
    check("a COPY is no match: C:\\R1\\Copied\\b.txt's identity (v:8) is nowhere now; the same name and size under "
          "C:\\R1\\Copy (identity v:9) is not suggested -- dangling, no suggestion",
          copied.state == REG.SCOPE_DANGLING and copied.suggestion is None)
    noisy = SC.suggest("c:\\r1\\keep", facts(identities={"v:3": [("c:\\r1\\other\\renamed.txt", "C:\\R1\\Other\\renamed.txt")]}))
    check("the same identity at a path that does not keep the file's place under the scope implies no scope: no suggestion",
          noisy is None)
    root_to_folder = SC.suggest("d:\\gone", facts(identities={"v:1": [("c:\\r1\\moved\\x.jpg", "C:\\R1\\Moved\\x.jpg")]}),
                                want_root=True)
    check("a root whose files are now a folder inside another root: suggested, but marked not a root -- a root policy "
          "cannot point there, and the words say what to do instead",
          root_to_folder is not None and not root_to_folder.is_root and "cannot point there" in root_to_folder.words)

    # The plan: the precondition on every removal, only while a protection dangles.
    import p3_regression as BASE
    loc, dec = BASE.loc, BASE.dec
    locs = [loc("L1", "C:\\R1\\Docs\\a.txt", "c:\\r1", "C1", "v:1"), loc("L2", "C:\\R1\\DL\\a.txt", "c:\\r1", "C1", "v:2"),
            loc("L3", "C:\\R1\\X\\b.txt", "c:\\r1", "C2", "v:3"), loc("L4", "C:\\R1\\Y\\b.txt", "c:\\r1", "C2", "v:4")]
    decisions = [dec("D1", "canonical_location", REG.GROUP, "C1", "L1"), dec("D2", "redundant_location", REG.LOCATION, "L2"),
                 dec("D3", "canonical_location", REG.GROUP, "C2", "L3"), dec("D4", "redundant_location", REG.LOCATION, "L4")]

    def build(policies, scopes):
        ev = BASE.model(locs, decisions, policies)
        ev.now = BASE.NOW
        ev.scopes = scopes
        proj = R.resolve_all(ev)
        return ev, proj, PL.build(ev, proj, RT.route_all(ev, proj, ev.now))
    ok_pol = [pv("PV1", "protect_folder_subtree", {"path": "C:\\R1\\Docs2"})]
    _ev, _p, plain = build(ok_pol, None)
    _ev, _p, judged = build(ok_pol, f)
    check("no dangling policy: the plan is byte-for-byte the plan without the rule (the same fingerprint; nothing attached)",
          PL.fingerprint(plain.items) == PL.fingerprint(judged.items) and "dangling_policies" not in judged.summary
          and not any(it.condition(KEY) for it in judged.items))
    bad_pol = [pv("PV1", "protect_folder_subtree", {"path": "C:\\R1\\Keep"}),
               pv("PV2", "prefer_folder_subtree", {"path": "C:\\R1\\Gone2"})]
    ev, proj, draft = build(bad_pol, f)
    removes = [it for it in draft.items if it.action_kind == REG.ACTION_REMOVE]
    conds = [it.condition(KEY) for it in removes]
    check(f"a dangling protection: every removal ({len(removes)}) carries {KEY} -- hard, evidence stage, result UNKNOWN, "
          "class retention -- and is blocked on evidence with that reason",
          len(removes) == 2 and all(c is not None and c.severity == "hard" and c.stage == REG.STAGE_EVIDENCE
                                    and c.result == "unknown" and c.klass == "retention" for c in conds)
          and all(it.state == REG.ITEM_BLOCKED_EVIDENCE and it.blocked_reason == KEY for it in removes))
    check("...its detail names the policy, why, and how to clear it (Re-point... or Retire); its params name the policy; "
          "the item links the policy version (re-pointing is a policy change for it)",
          all("Protect folder C:\\R1\\Keep" in c.detail and "Re-point" in c.detail and "retire" in c.detail
              and c.params == {"policies": ["PPV1"]} for c in conds)
          and all("PV1" in it.policy_version_ids for it in removes))
    check("a preserve item carries no such condition (it removes nothing)",
          all(it.condition(KEY) is None for it in draft.items if it.action_kind != REG.ACTION_REMOVE))
    lines = PL.summary_lines(draft.summary)
    check("the preview names both: the protection as BLOCKED, the dangling prefer as a warning only",
          any(l.startswith("BLOCKED") and "C:\\R1\\Keep" in l for l in lines)
          and any(l.startswith("Warning (the recommendation only)") and "C:\\R1\\Gone2" in l for l in lines))
    _ev, _p, pref_only = build([bad_pol[1]], f)
    check("a dangling PREFER policy alone: no precondition on any item, nothing blocked by it; a warning in the summary",
          not any(it.condition(KEY) for it in pref_only.items)
          and not any(it.blocked_reason == KEY for it in pref_only.items)
          and [d["effect"] for d in pref_only.summary["dangling_policies"]] == ["prefer"])
    retired = [pv("PV1", "protect_folder_subtree", {"path": "C:\\R1\\Keep"}, active=False)]
    _ev, _p, after_retire = build(retired, f)
    check("a RETIRED dangling policy blocks nothing", not any(it.condition(KEY) for it in after_retire.items))
    ev2, proj2, before = build(ok_pol, f)
    rt2 = RT.route_all(ev2, proj2, ev2.now)
    ev3, proj3, _d = build(bad_pol, f)
    reasons = PL.obsolescence(before.items, ev3, proj3, RT.route_all(ev3, proj3, ev3.now))
    check("a revision built with no dangling protection goes obsolete once one dangles (protection_dangling), and not before",
          [r[0] for r in reasons] == ["protection_dangling"] and not PL.obsolescence(before.items, ev2, proj2, rt2))
    check("the registry states it: no_dangling_protection is a retention precondition, attached conditionally to "
          "remove_location (B20: and, being a new item kind with no earlier fingerprint to keep, declared outright on "
          "move_location / rename_location -- on no other action)", REG.PRECONDITIONS[KEY] == "retention"
          and REG.CONDITIONAL_PRECONDITIONS[REG.ACTION_REMOVE][0] == (KEY, "hard", REG.STAGE_EVIDENCE)
          and all(k != KEY for a, rows in REG.ACTION_PRECONDITIONS.items()
                  if a not in (REG.ACTION_MOVE, REG.ACTION_RENAME) for k, _s, _st in rows)
          and all(any(k == KEY for k, _s, _st in REG.ACTION_PRECONDITIONS[a]) for a in (REG.ACTION_MOVE, REG.ACTION_RENAME)))


# -- the real-project scaffolding ----------------------------------------------------------------

def build_corpus(base: Path):
    r1, r2 = base / "Root1", base / "Root2"
    for d in (r1 / "Documents", r1 / "Downloads", r1 / "Keep" / "Sub", r1 / "Other", r1 / "Outside", r1 / "Empty" / "Leaf",
              r1 / "LinkOnly", r2 / "Backup"):
        d.mkdir(parents=True, exist_ok=True)
    A, B, N, O = b"alpha" * 20000, b"beta" * 30000, b"notes" * 3000, b"outside" * 2000
    (r1 / "Documents" / "report.pdf").write_bytes(A)
    (r1 / "Downloads" / "report.pdf").write_bytes(A)
    (r2 / "Backup" / "report.pdf").write_bytes(A)
    (r2 / "Backup" / "tax.docx").write_bytes(B)
    (r1 / "Other" / "tax.docx").write_bytes(B)
    (r1 / "Keep" / "notes.txt").write_bytes(N)
    (r1 / "Keep" / "Sub" / "deep.txt").write_bytes(N + b"deep")
    (r1 / "Other" / "notes.txt").write_bytes(N)
    (r1 / "Outside" / "far.txt").write_bytes(O)
    (r1 / "Other" / "far-copy.txt").write_bytes(O)
    os.link(r1 / "Keep" / "notes.txt", r1 / "Other" / "notes-link.txt")      # a hard link out of the protected folder
    junctions = []
    for link, target in ((r1 / "Keep" / "j", r1 / "Outside"), (r1 / "LinkOnly" / "j", r1 / "Outside")):
        p = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        junctions.append(p.returncode == 0)
    return r1, r2, all(junctions)


def scan(app_root, project_dir, roots=None, name=None):
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, DUPLICATES
    if project_dir is None:
        out1 = RunWorker(app_root, None, RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(r) for r in roots],
                                                    project_name=name)).run()
        project_dir = app_root / "Projects" / name
    else:
        out1 = RunWorker(app_root, project_dir, RunRequest(PRESCAN, "Pre-Scan")).run()
    out2 = RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run()
    return project_dir, out1.ok and out2.ok, f"{out1.status} {out1.message[:80]} / {out2.status} {out2.message[:80]}"


def set_roots(project_dir, roots):
    path = project_dir / "settings.json"
    s = json.loads(path.read_text(encoding="utf-8-sig"))
    s["SourceRoots"] = [str(r) for r in roots]
    path.write_text(json.dumps(s, indent=2), encoding="utf-8-sig")


def open_model(project_dir):
    from Phase2.core import connect
    from Phase3 import evidence as E, store as S
    conn = connect(project_dir, write=True)
    return conn, S.DecisionStore(conn, actor_id="dangling-check", session_id="dangling"), E


def model(conn, E):
    ev = E.load_evidence(conn)
    proj = R.resolve_all(ev)
    return ev, proj, RT.route_all(ev, proj, ev.now)


def fid(conn, E, *parts):
    """A file location id by its path's last parts (root folder name included)."""
    want = "\\".join(parts).lower()
    for i, root, rel in conn.execute("SELECT fp.file_path_id, sr.root_path, fp.relative_path FROM file_path fp "
                                     "JOIN source_root sr ON sr.source_root_id = fp.source_root_id"):
        if E.full_path(root, rel).lower().endswith("\\" + want):
            return str(i)
    return None


def versions(conn):
    return conn.execute("SELECT COUNT(*) FROM p3_policy_version").fetchone()[0]


def removals(draft):
    return [it for it in draft.items if it.action_kind == REG.ACTION_REMOVE]


# -- 2 + 3. a protected root, then a protected folder, renamed ----------------------------------

def test_root_rename(tmp: Path):
    section("2. A protected ROOT renamed on disk and rescanned (a real project)")
    app_root = tmp / "AppRoot"
    (app_root / "Projects").mkdir(parents=True)
    r1, r2, junctions = build_corpus(tmp / "Corpus")
    project_dir, ok, why = scan(app_root, None, [r1, r2], "Dangling")
    check("Pre-Scan and Find My Duplicates through the worker", ok, why)
    conn, st, E = open_model(project_dir)
    model(conn, E)                                   # builds Phase 2's current duplicate projection
    p_root = st.create_policy("protect_source_root", {"root_key": path_key(str(r2)), "root_path": str(r2)})
    p_fold = st.create_policy("protect_folder_subtree", {"path_key": path_key(str(r1 / "Keep")), "path": str(r1 / "Keep")})
    p_pref = st.create_policy("prefer_folder_subtree", {"path_key": path_key(str(r1 / "Documents")), "path": str(r1 / "Documents")})
    rep_group = conn.execute("SELECT content_id FROM file_state WHERE file_path_id = ?",
                             (int(fid(conn, E, "Documents", "report.pdf")),)).fetchone()[0]
    st.record_decision("canonical_location", REG.GROUP, str(rep_group), fid(conn, E, "Documents", "report.pdf"))
    st.record_decision("redundant_location", REG.LOCATION, fid(conn, E, "Downloads", "report.pdf"))
    ev, proj, rt = model(conn, E)
    check("before: every scope is present; nothing dangles", ev.scopes is not None and not SC.dangling(ev)
          and all(s.state == REG.SCOPE_PRESENT for s in SC.statuses(ev)), str([(s.scope_text, s.state) for s in SC.statuses(ev)]))
    draft = PL.build(ev, proj, rt)
    ready = [it for it in removals(draft) if it.state == REG.ITEM_READY]
    check("before: the Downloads copy's removal is ready, with no dangling-protection condition",
          len(ready) == 1 and ready[0].condition(KEY) is None, str([(it.target_ref, it.state, it.blocked_reason) for it in removals(draft)]))
    res = st.record_plan_revision(draft, name="before the rename")
    st.lock_plan_revision(res["plan_revision_id"])
    locked_items = st.plan_revision(res["plan_revision_id"])[1]
    backup_copy = fid(conn, E, "Root2", "Backup", "report.pdf")
    conn.close()

    # Rename the protected root on disk; point the project at the new name, as a person would; scan again.
    r2b = r2.with_name("Root2b")
    os.rename(r2, r2b)
    set_roots(project_dir, [r1, r2b])
    _pd, ok, why = scan(app_root, project_dir)
    check("the root renamed on disk (Root2 -> Root2b), the project's roots edited, scanned again", ok, why)
    conn, st, E = open_model(project_dir)
    before_versions = versions(conn)
    ev, proj, rt = model(conn, E)
    root_st = next(s for s in SC.statuses(ev) if s.policy_id == str(p_root["policy_id"]))
    check("the root policy is DANGLING: Root2 was not found although its volume was there",
          root_st.state == REG.SCOPE_DANGLING and "volume" in root_st.reason and "renamed" in root_st.reason, root_st.reason)
    check("the suggestion names the new root, from identity evidence (volume serial + file index), labelled as such",
          root_st.suggestion is not None and root_st.suggestion.is_root and path_key(root_st.suggestion.path) == path_key(str(r2b))
          and root_st.suggestion.matches == 2 and SC.SUGGESTION_LABEL in root_st.suggestion.words,
          str(root_st.suggestion))
    check("the folder and prefer policies (their folders untouched) are still present",
          {s.policy_id: s.state for s in SC.statuses(ev)} == {str(p_root["policy_id"]): REG.SCOPE_DANGLING,
                                                              str(p_fold["policy_id"]): REG.SCOPE_PRESENT,
                                                              str(p_pref["policy_id"]): REG.SCOPE_PRESENT})
    renamed = fid(conn, E, "Root2b", "Backup", "report.pdf")
    check("the renamed copies are new locations, and NOT protected (the gap, as it was)",
          renamed is not None and renamed != backup_copy and renamed not in R.protected_locations(ev))
    old_root = next(k for k in ev.roots if path_key(k) == path_key(str(r2)))
    ghost_group = next(g for g in proj.groups if backup_copy in [v.location_id for v in g.members])
    route = rt.groups[ghost_group.group_id]
    check("the old root's last-known files are not current: the root routes unavailable and a group holding one carries "
          "the blocker on root coverage (through B18 it read complete from its last scan); its route is not ready",
          not ev.roots[old_root].available and route.primary in (RT.BLOCKED, RT.NEEDS_REVALIDATION)
          and any(c.kind == RT.INCOMPLETE_ROOT_COVERAGE and "renamed, moved or deleted" in c.detail for c in route.conditions),
          str(route.conditions[:2]))
    st.record_decision("redundant_location", REG.LOCATION, renamed)
    ev, proj, rt = model(conn, E)
    draft = PL.build(ev, proj, rt)
    rem = removals(draft)
    check(f"every removal ({len(rem)}) is blocked with {KEY} -- unknown, never pass -- naming the policy and how to clear it",
          rem and all(it.condition(KEY) is not None and it.condition(KEY).result == "unknown"
                      and str(p_root["policy_id"]) in it.condition(KEY).params["policies"]
                      and "Re-point" in it.condition(KEY).detail and it.state != REG.ITEM_READY for it in rem),
          str([(it.target_ref, it.state, it.blocked_reason) for it in rem]))
    check("...the renamed copy's removal among them: planned, blocked, not ready",
          any(it.target_ref == renamed and it.state != REG.ITEM_READY and it.condition(KEY) for it in rem))
    check("the preview says which policy and how to clear it",
          any("BLOCKED" in l and str(r2) in l and "Re-point" in l for l in PL.summary_lines(draft.summary)))
    from Phase3 import review as RV
    RV.run_detector(conn, st, pairs=True)
    st.reconcile_plans(*model(conn, E))
    check("nothing re-mapped the policy on its own: loading, building, the detector and plan reconciliation recorded no "
          "policy version (S3; axiom 17)", versions(conn) == before_versions)
    rev, _items = st.plan_revision(res["plan_revision_id"])
    check("the revision locked before the rename is obsolete now (its obsolete event gives the reasons)",
          rev["status"] == "obsolete", str([e["detail"] for e in rev["events"]][-1:]))
    ev_now, proj_now, rt_now = model(conn, E)
    check("...and protection_dangling is among the reasons the locked revision's own items give",
          "protection_dangling" in [r[0] for r in PL.obsolescence(locked_items, ev_now, proj_now, rt_now)])

    # Re-point: the same policy, a new version, the new root.
    st.revise_policy(p_root["policy_id"], scope={"root_key": path_key(str(r2b)), "root_path": str(r2b)},
                     rationale="re-pointed: the root was renamed")
    ev, proj, rt = model(conn, E)
    draft = PL.build(ev, proj, rt)
    check("re-pointing (a new version of the same policy) clears it: nothing dangles, no removal carries the condition",
          not SC.dangling(ev) and not any(it.condition(KEY) for it in removals(draft)))
    check("...and the protection covers the renamed files: protected, the redundant mark a conflict, its removal not planned "
          "(or blocked by target_not_protected)",
          renamed in R.protected_locations(ev)
          and not any(it.target_ref == renamed and it.state == REG.ITEM_READY for it in removals(draft)))
    hist = st.policy_history(p_root["policy_id"])
    check("the policy's history keeps both scopes (v1 Root2, v2 Root2b); decisions naming it are untouched",
          [path_key(h["scope"]["root_key"]) for h in hist] == [path_key(str(r2)), path_key(str(r2b))])
    # The mark on the now-protected copy is a conflict; take it back, so the
    # group plans again (its Downloads removal) for the folder half below.
    for d in st.active_for(REG.LOCATION, renamed):
        st.withdraw(d["decision_id"] if isinstance(d, dict) else d.decision_id)
    st.record_decision("redundant_location", REG.LOCATION, fid(conn, E, "Other", "tax.docx"))   # kept: Root2b's (protected)
    conn.close()

    section("3. A protected FOLDER renamed inside a scanned root; a dangling PREFER policy")
    os.rename(r1 / "Keep", r1 / "Kept")
    os.rename(r1 / "Documents", r1 / "Docs")
    _pd, ok, why = scan(app_root, project_dir)
    check("Keep -> Kept and Documents -> Docs renamed on disk, scanned again", ok, why)
    conn, st, E = open_model(project_dir)
    ev, proj, rt = model(conn, E)
    by = {s.policy_id: s for s in SC.statuses(ev)}
    fs, ps = by[str(p_fold["policy_id"])], by[str(p_pref["policy_id"])]
    check("the folder policy is DANGLING: its root was scanned and nothing is at or under Keep",
          fs.state == REG.SCOPE_DANGLING and "nothing is at or under" in fs.reason, fs.reason)
    check("the suggestion is the renamed folder (Kept), from the identities of notes.txt and Sub\\deep.txt",
          fs.suggestion is not None and path_key(fs.suggestion.path) == path_key(str(r1 / "Kept"))
          and not fs.suggestion.is_root and fs.suggestion.matches == 2, str(fs.suggestion))
    check("the prefer policy on Documents dangles too", ps.state == REG.SCOPE_DANGLING)
    draft = PL.build(ev, proj, rt)
    check("the dangling protection blocks every removal again (the folder one)",
          removals(draft) and all(it.condition(KEY) and str(p_fold["policy_id"]) in it.condition(KEY).params["policies"]
                                  for it in removals(draft)))
    st.revise_policy(p_fold["policy_id"], scope={"path_key": path_key(str(r1 / "Kept")), "path": str(r1 / "Kept")},
                     rationale="re-pointed: the folder was renamed")
    ev, proj, rt = model(conn, E)
    draft = PL.build(ev, proj, rt)
    kept = fid(conn, E, "Kept", "notes.txt")
    check("re-pointed to Kept: no removal carries the condition; Kept\\notes.txt is protected",
          not any(it.condition(KEY) for it in removals(draft)) and kept in R.protected_locations(ev))
    lines = PL.summary_lines(draft.summary)
    check("the dangling PREFER policy alone: a warning in the preview, blocks nothing",
          any(l.startswith("Warning (the recommendation only)") and "Documents" in l for l in lines)
          and not any(it.blocked_reason == KEY for it in draft.items))
    link = fid(conn, E, "Other", "notes-link.txt")
    check("a hard link is one location per path: Kept\\notes.txt protected, its alias Other\\notes-link.txt (the same "
          "physical file) is not", link is not None and link not in R.protected_locations(ev)
          and ev.locations.get(link) is not None and ev.locations[link].physical_id == ev.locations[kept].physical_id)

    section("5. Links and mounts: paths as scanned")
    if not junctions:
        check("junctions could be made here (mklink /J)", False, "mklink /J failed")
    else:
        rows = conn.execute("SELECT e.event_kind, e.relative_path, e.reparse_tag FROM scan_path_event e JOIN inventory_scan s "
                            "ON s.inventory_scan_id = e.inventory_scan_id WHERE s.run_id = (SELECT MAX(run_id) FROM inventory_scan)"
                            ).fetchall()
        rel = {(r[0], r[1].lower()): r[2] for r in rows}
        check("a junction inside the protected folder is recorded there as a skipped reparse directory (tag 0xA0000003) "
              "and NOT followed: nothing under Kept\\j is in the scan",
              rel.get(("skipped_reparse_directory", "kept\\j")) == 0xA0000003
              and not conn.execute("SELECT 1 FROM file_path WHERE lower(relative_path) LIKE 'kept\\j\\%'").fetchone())
        far = fid(conn, E, "Outside", "far.txt")
        check("the junction's target (Outside\\far.txt, scanned at its own path) is not protected by the Kept policy",
              far is not None and far not in R.protected_locations(ev) and ev.locations.get(far) is not None
              and not any(p.policy_id == str(p_fold["policy_id"])
                          for p in R.location_protection(R.active_policies(ev.policies), {}, ev.locations[far])[0]))
        link_only = SC.status(PolicyVersion("x", "x", "protect_folder_subtree",
                                            {"path_key": path_key(str(r1 / "LinkOnly")), "path": str(r1 / "LinkOnly")}), ev.scopes)
        empty = SC.status(PolicyVersion("y", "y", "protect_folder_subtree",
                                        {"path_key": path_key(str(r1 / "Empty")), "path": str(r1 / "Empty")}), ev.scopes)
        check("a folder holding only a junction (a mount point is the same kind of entry) is present -- the link is "
              "recorded under it; a folder holding only an empty folder is present",
              link_only.state == REG.SCOPE_PRESENT and empty.state == REG.SCOPE_PRESENT, f"{link_only.state} / {empty.state}")
    import win_meta
    check("Phase 1 follows neither kind of directory reparse point: the mount-point tag (junctions AND volume mount "
          "points) and the symbolic-link tag are both 'true links'",
          win_meta.is_true_link(win_meta.FILE_ATTRIBUTE_REPARSE_POINT | 0x10, 0xA0000003)
          and win_meta.is_true_link(win_meta.FILE_ATTRIBUTE_REPARSE_POINT | 0x10, 0xA000000C)
          and not win_meta.is_true_link(0x10, 0xA0000003))
    conn.close()
    return app_root, project_dir, r1


def test_retire(tmp: Path):
    section("2b. Retiring a dangling protection clears the block too (a second project)")
    app_root = tmp / "AppRoot2"
    (app_root / "Projects").mkdir(parents=True)
    r1, r2, _j = build_corpus(tmp / "Corpus2")
    project_dir, ok, why = scan(app_root, None, [r1, r2], "Dangling2")
    conn, st, E = open_model(project_dir)
    model(conn, E)
    p_root = st.create_policy("protect_source_root", {"root_key": path_key(str(r2)), "root_path": str(r2)})
    rep = fid(conn, E, "Documents", "report.pdf")
    st.record_decision("redundant_location", REG.LOCATION, fid(conn, E, "Downloads", "report.pdf"))
    conn.close()
    os.rename(r2, r2.with_name("Root2c"))
    set_roots(project_dir, [r1, r2.with_name("Root2c")])
    _pd, ok2, why2 = scan(app_root, project_dir)
    conn, st, E = open_model(project_dir)
    ev, proj, rt = model(conn, E)
    blocked = [it for it in removals(PL.build(ev, proj, rt)) if it.condition(KEY)]
    st.retire_policy(p_root["policy_id"], rationale="the root is gone")
    ev, proj, rt = model(conn, E)
    after = removals(PL.build(ev, proj, rt))
    check("dangling -> removals blocked on it; retired -> the condition is gone from every removal",
          ok and ok2 and blocked and not SC.dangling(ev) and not any(it.condition(KEY) for it in after), f"{why} / {why2}")
    check("...what still blocks a removal is only what always did (here: the renamed root's last-known copies are not "
          "current, so the group's coverage is unknown) -- never the retired policy",
          all(it.blocked_reason != KEY for it in after) and rep is not None)
    conn.close()


# -- 4. an offline root ----------------------------------------------------------------------

def _free_letter():
    used = {d[0].upper() for d in os.popen("wmic logicaldisk get name 2>nul").read().split() if len(d) == 2 and d[1] == ":"}
    for letter in reversed(string.ascii_uppercase[7:]):
        if letter not in used and not os.path.exists(f"{letter}:\\"):
            return letter
    return None


def test_offline(tmp: Path):
    section("4. An OFFLINE root (a subst drive, removed): unavailable -- not dangling")
    letter = _free_letter()
    if letter is None:
        check("a free drive letter for subst", False, "none free")
        return
    host = tmp / "SubstHost"
    (host / "Data").mkdir(parents=True)
    base = tmp / "Corpus3" / "Main"
    base.mkdir(parents=True)
    blob = b"offline" * 20000
    (base / "a.bin").write_bytes(blob)
    (host / "Data" / "a.bin").write_bytes(blob)
    drive = f"{letter}:"
    made = subprocess.run(["subst", drive, str(host)], capture_output=True, text=True)
    if made.returncode != 0:
        check("subst could map a drive letter here", False, made.stderr.strip() or made.stdout.strip())
        return
    try:
        app_root = tmp / "AppRoot3"
        (app_root / "Projects").mkdir(parents=True)
        ext = Path(f"{drive}\\Data")
        project_dir, ok, why = scan(app_root, None, [base, ext], "Offline")
        conn, st, E = open_model(project_dir)
        model(conn, E)
        pol = st.create_policy("protect_source_root", {"root_key": path_key(str(ext)), "root_path": str(ext)})
        st.record_decision("redundant_location", REG.LOCATION, fid(conn, E, "Main", "a.bin"))
        conn.close()
        subprocess.run(["subst", drive, "/D"], capture_output=True)
        _pd, ok2, why2 = scan(app_root, project_dir)
        conn, st, E = open_model(project_dir)
        ev, proj, rt = model(conn, E)
        s = next(x for x in SC.statuses(ev) if x.policy_id == str(pol["policy_id"]))
        check(f"scanned with {drive} mapped, then with it gone: the root's volume was not there -- UNAVAILABLE, not dangling, "
              "no suggestion", ok and s.state == REG.SCOPE_UNAVAILABLE and "offline" in s.reason and s.suggestion is None,
              f"{why} / {why2} / {s.state}: {s.reason}")
        draft = PL.build(ev, proj, rt)
        check("no removal carries no_dangling_protection (offline is not renamed)",
              not any(it.condition(KEY) for it in removals(draft)))
        g = next((g for g in proj.groups if len(g.members) == 2), None)
        check("the copies on the offline root are not current: their group routes BLOCKED on root coverage, and the "
              "removal is not ready", g is not None and rt.groups[g.group_id].primary == RT.BLOCKED
              and not any(it.state == REG.ITEM_READY for it in removals(draft)),
              str(rt.groups[g.group_id].conditions[:2]) if g else "no group")
        conn.close()
    finally:
        subprocess.run(["subst", drive, "/D"], capture_output=True)


# -- 6. the window -----------------------------------------------------------------------------

def test_gui(app_root, project_dir, r1):
    section("6. The window: the Decide page, the Policies dialog and Re-point..., the Plan page")
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
    from Phase3 import review as RV
    real = (messagebox.showinfo, messagebox.showwarning, messagebox.askyesno)
    messagebox.showinfo = messagebox.showwarning = lambda *a, **k: None
    messagebox.askyesno = lambda *a, **k: True
    try:
        # Make one policy dangle again: rename Kept -> Kept2 (the policy points at Kept).
        os.rename(r1 / "Kept", r1 / "Kept2")
        _pd, ok, why = scan(app_root, project_dir)
        app = gui.Phase2App(None)
        app.withdraw()
        app.open_project(project_dir)
        app.show_decide()
        app.update()
        page = app.review_page
        text = page.dangling_label.cget("text")
        check("the Decide page names the dangling protection, says removals are blocked, and how to clear it",
              ok and page.dangling_frame.winfo_manager() == "pack" and "Protect folder" in text and "Kept" in text
              and "removals are blocked" in text and "Re-point" in text, text[:200])
        page.open_policies()
        app.update()
        dlg = page.policies_dialog
        marked = {iid: dlg.table.item(iid, "values") for iid in dlg.table.get_children()
                  if "MATCHES NOTHING" in dlg.table.item(iid, "values")}
        dangling_rows = [iid for iid, v in marked.items() if v[1] == "Protect folder"]
        check("the Policies dialog marks MATCHES NOTHING on exactly the dangling ones: the protect folder (Kept, renamed) "
              "and the prefer folder (Documents, renamed in section 3, never re-pointed)",
              len(dangling_rows) == 1 and sorted(v[1] for v in marked.values()) == ["Prefer folder", "Protect folder"],
              str(marked))
        dlg.table.selection_set(dangling_rows[0])
        dlg._selected()
        check("selecting it shows why and how to clear it; Re-point... is enabled",
              "Re-point" in str(dlg.scope_label.cget("text")) and str(dlg.repoint_button.cget("state")) == "normal")
        conn = app.conn
        n0 = versions(conn)
        dlg.repoint()
        app.update()
        rp = dlg.repoint_dialog
        check("Re-point... shows the suggestion (Kept2, labelled) and leaves the field EMPTY until asked",
              rp.suggestion is not None and path_key(rp.suggestion.path) == path_key(str(r1 / "Kept2"))
              and rp.var.get() == "" and SC.SUGGESTION_LABEL in rp.suggestion.words)
        rp.use_suggestion()
        check("'Use the suggestion' fills the field -- and records nothing", path_key(rp.var.get()) == path_key(str(r1 / "Kept2"))
              and versions(conn) == n0)
        rp._confirm()
        app.update()
        text = page.dangling_label.cget("text")
        check("Re-point records ONE new version with the new scope; the banner no longer names a protection (the prefer "
              "policy's warning stays, as it should)",
              versions(conn) == n0 + 1 and "removals are blocked" not in text and "Protect folder" not in text
              and "recommendation only" in text, text[:200])
        pol = RV.policy_version(app.review_page.store.policy(dangling_rows[0]))
        check("...the policy now points at Kept2 and matches it", path_key(pol.scope["path_key"]) == path_key(str(r1 / "Kept2"))
              and SC.status(pol, RV.E.load_scope_facts(conn)).state == REG.SCOPE_PRESENT)
        dlg.win.destroy()
        # Dangle it once more and look at the Plan page.
        os.rename(r1 / "Kept2", r1 / "Kept3")
        app.release_connection()
        app.destroy()
        _pd, ok, why = scan(app_root, project_dir)
        app = gui.Phase2App(None)
        app.withdraw()
        app.open_project(project_dir)
        app.show_plan()
        app.update()
        text = app.plan_page.population_var.get()
        check("the Plan page names it before a revision is built: BLOCKED, which policy, how to clear it",
              ok and "BLOCKED" in text and "Kept2" in text and "Re-point" in text, text[:200])
        app.plan_page.build_revision()
        app.update()
        preview = app.plan_page.preview_text.get("1.0", "end")
        check("...and the built revision's preview says it too", "no_dangling_protection" in preview and "Kept2" in preview,
              preview[-300:])
        app.release_connection()
        app.destroy()
    finally:
        messagebox.showinfo, messagebox.showwarning, messagebox.askyesno = real


def main():
    tmp = Path(tempfile.mkdtemp(prefix="p3dangle_"))
    try:
        test_pure()
        app_root, project_dir, r1 = test_root_rename(tmp)
        test_retire(tmp)
        test_offline(tmp)
        test_gui(app_root, project_dir, r1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 DANGLING: {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
