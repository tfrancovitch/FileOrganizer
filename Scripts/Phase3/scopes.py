r"""Policy scopes against the current scan (B19): renamed roots and folders.

The gap this closes. Policies are scoped by a source root or a folder path.
Rename or move a protected root or folder on disk and scan again: Phase 1
records the new place as new paths, and the old scope matches nothing that is
there now. Through B18 the protection then silently covered nothing -- the
renamed files could be marked redundant and planned for removal. A
protection must never silently lapse (S7, S4).

The rule has two halves, and both are here or in the store:

  1. Never silently re-map. A policy whose scope matches nothing in the
     current scan is DANGLING. It is named on the Decide page, in the
     Policies dialog and in the Plan preview; while any active PROTECT policy
     is dangling, every Remove Location item carries the hard precondition
     `no_dangling_protection`, evaluated `unknown` -- nobody can say whether a
     removed file was meant to be protected, and unknown never passes. A
     dangling prefer or avoid policy is a warning only.
  2. A person re-points it (Policies... > Re-point...: a new policy version
     with the new scope, `store.revise_policy`) or retires it. When Phase 1's
     identity evidence (volume serial + file index) shows where the scope's
     files went, the dialog SUGGESTS that place, labelled as a suggestion. It
     is never applied automatically (S3; axiom 17).

Three states, kept apart (the handoff's trap: offline is not renamed):

  present      something in the current scan is at or under the scope
  unavailable  the scope's root was not observed in the latest scan -- its
               volume was not there (offline), it was not scanned, or part of
               the scope could not be read. Nobody can say; not dangling.
               (Routing already blocks the copies there: `evidence.load_roots`.)
  dangling     the place was observed and the scope is absent from it: its
               root's folder was missing while its volume was present, or its
               root was scanned completely and nothing is at or under the
               folder -- no file, no empty folder, no link Phase 1 recorded.

What "current scan" means: the project's latest inventory run (the newest
run with an inventory_scan row). A root that run walked, available, to a
completed status is scanned; its file_state rows are the present. A root it
did not walk keeps its last-known rows (Phase 1 never marks the files of an
unreachable root missing) -- those are not "present" here.

Links and mounts (ARCHITECTURE.md, "Path matching"): a scope covers paths AS
SCANNED under it. Phase 1 never descends into a directory junction, a
directory symbolic link or a volume mount point (all directory reparse
points: it records each as a skipped_reparse_directory event under the path
it was found at), so nothing reached through one is ever under the scope; the
link itself is recorded under the scope and counts as something there. A file
symbolic link is one location at its own path; a hard link is one location per
path, as always (hard-link aliases are handled by physical identity).

Everything here is a pure function of `ScopeFacts`, which
`evidence.load_scope_facts` reads from the project database.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field, replace

from .model import path_key, under
from .registry import (POLICY_KINDS, SOURCE_ROOT, SCOPE_PRESENT, SCOPE_UNAVAILABLE, SCOPE_DANGLING)

ROOT_SCANNED, ROOT_UNAVAILABLE, ROOT_ABSENT = "scanned", "unavailable", "absent"

#: The words beside a suggestion (the handoff's wording).
SUGGESTION_LABEL = "suggested — same file identities (volume serial + file index) seen under this new path"
HOW_TO_CLEAR = "Re-point it to where its files are now (Policies... > Re-point...), or retire it (Policies... > Retire)."


@dataclass(frozen=True)
class RootState:
    root_key: str
    root_path: str
    state: str                      # scanned | unavailable | absent
    detail: str = ""


@dataclass
class ScopeFacts:
    """What the latest scan observed, keyed by path (never by a location id)."""
    roots: dict = field(default_factory=dict)       # root_key -> RootState
    present: list = field(default_factory=list)     # sorted path keys of files present in a scanned root
    unknown: list = field(default_factory=list)     # sorted path keys a scanned root could not verify
    folders: list = field(default_factory=list)     # sorted path keys of folders / links the latest scans recorded
    identities: dict = field(default_factory=dict)  # physical id -> [(path key, path)] present in a scanned root
    lost: list = field(default_factory=list)        # [(path key, physical id)] known before, not present now
    run_id: int | None = None                       # the latest inventory run
    # -- B20 (organizing intent): the same scan, read for occupancy and coverage. Pure additions: nothing
    # B19 reads changes. The sources are the ones coverage already reads (file_state, scan_path_event,
    # inventory_scan) -- no second coverage query.
    present_paths: list = field(default_factory=list)   # the full path as stored, parallel to `present`
    inaccessible: list = field(default_factory=list)    # sorted path keys the scan met and could not read (a folder it could not list too)
    reparse: list = field(default_factory=list)         # sorted path keys of directory links the scan did not follow
    truncated: set = field(default_factory=set)         # root keys whose latest scan stored only part of its inaccessible list
    root_ids: dict = field(default_factory=dict)        # source_root_id -> root key (active roots)
    volumes: dict = field(default_factory=dict)         # root key -> sorted volume serials seen among its present files

    def relabelled(self, fn):
        """The same facts with the physical ids put through fn('physical', id)
        (the identifier-relabelling check, p3_relabel_check)."""
        return replace(self, identities={fn("physical", k): list(v) for k, v in self.identities.items()},
                       lost=[(p, fn("physical", ph)) for p, ph in self.lost])


@dataclass(frozen=True)
class Suggestion:
    scope_key: str                  # the implied new scope, as a key
    path: str                       # ... and as the scan spelled it
    matches: int                    # files of the old scope found (same identity) under it
    of: int                         # files of the old scope with a known identity
    is_root: bool                   # it is a source root of the project (a root policy can point there)
    words: str


@dataclass(frozen=True)
class ScopeStatus:
    policy_id: str
    policy_version_id: str
    kind: str
    effect: str                     # protect | prefer | avoid
    scope_text: str
    state: str                      # present | unavailable | dangling
    reason: str
    suggestion: Suggestion | None = None

    @property
    def label(self):
        return f"{POLICY_KINDS[self.kind].label} {self.scope_text}"

    def words(self):
        """What a surface says about a dangling policy: which, why, how to clear it."""
        text = f"{self.label} (policy #{self.policy_id}) matches nothing in the latest scan: {self.reason}."
        if self.suggestion is not None:
            text += f" {self.suggestion.words}"
        return text + " " + HOW_TO_CLEAR


def _has_under(keys, folder_key) -> bool:
    """Is any key the folder itself or inside it? (keys sorted)"""
    if not folder_key:
        return False
    i = bisect_left(keys, folder_key)
    if i < len(keys) and keys[i] == folder_key:
        return True
    j = bisect_left(keys, folder_key + "\\")
    return j < len(keys) and keys[j].startswith(folder_key + "\\")


def _under_count(keys, folder_key) -> int:
    j = bisect_left(keys, folder_key + "\\")
    n = 0
    while j < len(keys) and keys[j].startswith(folder_key + "\\"):
        n += 1
        j += 1
    return n


def _scope_of(policy):
    kind = POLICY_KINDS.get(policy.kind)
    scope = policy.scope or {}
    if kind is None:
        return None, "", ""
    if kind.scope_kind == SOURCE_ROOT:
        return kind, path_key(scope.get("root_key") or ""), scope.get("root_path") or scope.get("root_key") or ""
    return kind, path_key(scope.get("path_key") or scope.get("path") or ""), scope.get("path") or scope.get("path_key") or ""


def status(policy, facts: ScopeFacts) -> ScopeStatus:
    """Where one policy's scope stands in the current scan."""
    kind, key, text = _scope_of(policy)
    effect = kind.effect if kind else "?"

    def out(state, reason, suggestion=None):
        return ScopeStatus(str(policy.policy_id), str(policy.policy_version_id), policy.kind, effect, text, state, reason,
                           suggestion)
    if kind is None or not key:
        return out(SCOPE_DANGLING, "the policy names no scope this product can match")
    if kind.scope_kind == SOURCE_ROOT:
        root = facts.roots.get(key)
        if root is None:
            return out(SCOPE_DANGLING, f"no source root of this project is {text}", suggest(key, facts, want_root=True))
        if root.state == ROOT_SCANNED:
            return out(SCOPE_PRESENT, "scanned in the latest scan")
        if root.state == ROOT_ABSENT:
            return out(SCOPE_DANGLING, root.detail, suggest(key, facts, want_root=True))
        return out(SCOPE_UNAVAILABLE, root.detail)
    # A folder scope.
    involved = [r for r in facts.roots.values() if under(key, r.root_key) or under(r.root_key, key)]
    if _has_under(facts.present, key) or _has_under(facts.folders, key):
        return out(SCOPE_PRESENT, "present in the latest scan")
    if not involved:
        return out(SCOPE_DANGLING, f"no source root of this project contains {text}", suggest(key, facts))
    if _has_under(facts.unknown, key):
        return out(SCOPE_UNAVAILABLE, "part of it could not be read in the latest scan")
    waiting = [r for r in involved if r.state == ROOT_UNAVAILABLE]
    if waiting:
        return out(SCOPE_UNAVAILABLE, "; ".join(f"{r.root_path}: {r.detail}" for r in waiting))
    absent = [r for r in involved if r.state == ROOT_ABSENT]
    if absent:
        reason = "; ".join(f"{r.root_path}: {r.detail}" for r in absent)
    else:
        reason = "its root was scanned and nothing is at or under this folder"
    return out(SCOPE_DANGLING, reason, suggest(key, facts))


def suggest(key, facts: ScopeFacts, want_root=False):
    """Where the scope's files went, from identity alone: each file known under
    the old scope and not present now whose volume serial + file index is
    present elsewhere, at a path that ends with the same relative path. The
    implied new scope with the most such files is the suggestion. Never by
    name or size: a copy has a new file index and is not a match."""
    counts, spelled = {}, {}
    known = 0
    for old_key, phys in facts.lost:
        if not under(old_key, key):
            continue
        known += 1
        rel = old_key[len(key) + 1:] if old_key != key else ""
        for new_key, new_path in facts.identities.get(phys, ()):
            if under(new_key, key) or not rel or not new_key.endswith("\\" + rel):
                continue
            implied = new_key[:-len(rel) - 1]
            counts[implied] = counts.get(implied, 0) + 1
            spelled.setdefault(implied, new_path[:len(new_path) - len(rel) - 1])
    if not counts:
        return None
    best = min(counts, key=lambda k: (-counts[k], k))
    is_root = best in facts.roots and facts.roots[best].state == ROOT_SCANNED
    words = f"{spelled[best]} ({SUGGESTION_LABEL}: {counts[best]:,} of {known:,} files)."
    if want_root and not is_root:
        container = next((r for r in facts.roots.values() if under(best, r.root_key)), None)
        words += (" It is a folder" + (f" inside the root {container.root_path}" if container else "")
                  + ", not a source root: a root policy cannot point there -- retire this one and add a Protect folder "
                  "policy on that folder instead.")
    return Suggestion(best, spelled[best], counts[best], known, is_root, words)


def statuses(ev, include_inactive=False) -> list:
    """Every (active) policy's ScopeStatus -- empty when the model carries no
    scan facts (a hand-built model, a fixture: nothing to judge against)."""
    facts = getattr(ev, "scopes", None)
    if facts is None:
        return []
    return [status(p, facts) for p in ev.policies if include_inactive or p.active]


def dangling(ev, effect=None) -> list:
    """The active policies whose scope is dangling (optionally of one effect)."""
    return [s for s in statuses(ev) if s.state == SCOPE_DANGLING and (effect is None or s.effect == effect)]


def dangling_protection(ev) -> list:
    return dangling(ev, "protect")


def summary(ev) -> list:
    """The dangling policies as plain data (the Plan preview's summary; the
    Decide page's banner): protect ones first, then prefer / avoid."""
    out = []
    for s in sorted(dangling(ev), key=lambda s: (s.effect != "protect", s.label.lower())):
        out.append({"policy_id": s.policy_id, "policy_version_id": s.policy_version_id, "kind": s.kind,
                    "effect": s.effect, "scope": s.scope_text, "reason": s.reason, "words": s.words(),
                    "suggestion": s.suggestion.path if s.suggestion else None})
    return out
