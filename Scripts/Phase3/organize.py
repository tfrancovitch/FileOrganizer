r"""Organizing intent (B20): the pure helpers the Plan Builder, the store and the
dialogs share. P3.R12, accepted with required changes (the technical
evaluation), scoped by 01_B20_SYNTHESIS.md.

Two literal, human-authored decision kinds -- `desired_location` and
`desired_name` -- act on a file location. They are decisions about where a
file should be and what it should be called, recorded in the project and
nothing else: no source file is read, moved or renamed here, ever (axiom 17;
P3-A125). Phase 4 would be the first thing that could change a namespace, and
it does not exist; every plan item built from these decisions stays
`unsupported_action` until it does.

What this module is. Pure functions over the model (`Evidence`, and the
`ScopeFacts` the latest scan produced) -- no database, no Tk, no clock:

  names        record-time Windows name validation (P3-A142, F28, F38)
  intents      the current organize intent of every location, loaded apart
               from `Evidence.decisions` (the metadata pattern: the duplicate
               and pair router never sees an organize decision)
  geometry     where each intent would move or rename a file: from_path,
               to_path, the folders it would create
  occupancy    "known free" -- a destination is free only where the walk
               covered the whole folder chain (P3-A135, P3-A145)
  chains       a destination another item vacates; swaps and rotations
  companions   sidecars found by name from stored paths alone (P3-A133)
  drift        what is material to a decision's evidence binding (R12 §5.4)

Windows facts the rules rest on (Microsoft, "Naming Files, Paths, and
Namespaces"; R12 source S29): the reserved characters < > : " / \ | ? *, the
control characters 0-31, the device names CON PRN AUX NUL COM1-9 LPT1-9 (and
the superscript-digit forms), a name ending in a space or a period, names are
case-insensitive. A reserved device name followed by an extension (`NUL.tar.gz`)
is refused here although Microsoft words that as "avoid": a POLICY CHOICE,
recorded in the closeout (a name some Windows tools cannot open is not worth a
file the product is asked to create).
"""
from __future__ import annotations

import json
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .model import path_key, under

# -- the decision kinds' vocabulary --------------------------------------------------------

KIND_LOCATION = "desired_location"
KIND_NAME = "desired_name"
ORGANIZE_KINDS = (KIND_LOCATION, KIND_NAME)
ON_COLLISION_FAIL = "fail"                       # the only collision policy (P3-A134)
COLLISION_POLICY = "FAIL_ON_CONFLICT"            # what a plan item declares for Phase 4

# -- names ---------------------------------------------------------------------------------

RESERVED_CHARS = '<>:"/\\|?*'
MAX_UNITS = 255                                  # one name or folder segment, in UTF-16 code units
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])", re.IGNORECASE | re.ASCII)


def utf16_units(text) -> int:
    """Windows counts a name in UTF-16 code units: a character outside the BMP
    is two. (A lone surrogate -- a Windows name that is not valid UTF-16 -- is one.)"""
    return len(str(text).encode("utf-16-le", "surrogatepass")) // 2


def component_problem(part):
    """Why one name or folder segment is not a legal Windows name, or None."""
    if not isinstance(part, str) or part == "":
        return "a name cannot be empty"
    if part in (".", ".."):
        return f"'{part}' is not a name (it would leave or repeat the folder)"
    bad = sorted({c for c in part if c in RESERVED_CHARS or ord(c) < 32})
    if bad:
        shown = ", ".join(repr(c) if ord(c) < 32 else c for c in bad)
        extra = " (use \\ between folders)" if "/" in bad else ""
        return f"'{part}' contains a character Windows does not allow in a name: {shown}{extra}"
    if part[-1] in " .":
        return f"'{part}' ends with a {'space' if part[-1] == ' ' else 'period'}, which Windows does not allow"
    stem = part.split(".", 1)[0].rstrip(" ")
    if _DEVICE.fullmatch(stem):
        return f"'{part}' is a reserved device name ({stem.upper()}), with or without an extension"
    n = utf16_units(part)
    if n > MAX_UNITS:
        return f"'{part[:24]}...' is {n} UTF-16 code units long; Windows allows {MAX_UNITS}"
    return None


def folder_problem(folder):
    """Why a destination folder is not a root-relative path of legal names, or
    None. '' is the root itself. A drive letter, a leading separator or a UNC
    path would leave the root; '..' would too (a trap the research does not
    state)."""
    if not isinstance(folder, str):
        return "the destination folder is text"
    if folder == "":
        return None
    if folder.startswith(("\\", "/")):
        return "the destination folder is relative to the root: it does not start with a separator"
    if re.match(r"^[A-Za-z]:", folder):
        return "the destination folder is relative to the root: it carries no drive letter"
    for part in folder.split("\\"):
        why = component_problem(part)
        if why:
            return why
    return None


def name_problem(name):
    return component_problem(name)


def extension_of(name) -> str:
    """The extension of a file name, lower-cased with its dot ('' when none)."""
    base = str(name)
    i = base.rfind(".")
    return base[i:].lower() if i > 0 else ""


def split_rel(rel) -> tuple:
    """(folder, name) of a root-relative path."""
    rel = str(rel).replace("/", "\\")
    folder, _sep, name = rel.rpartition("\\")
    return folder, name


def join_rel(folder, name) -> str:
    return f"{folder}\\{name}" if folder else str(name)


def full_key(root_key, rel) -> str:
    """The path key of root + root-relative path (what Location.path_key gives
    for the same file)."""
    k = path_key(rel)
    return f"{root_key}\\{k}" if k else root_key


# -- the folders a walk saw ----------------------------------------------------------------

class FolderIndex:
    """What the latest walk saw, by folder: built once from `ScopeFacts`.

    `children(folder_key)` -> {child key: [spellings]} of the folders directly
    under a folder; `files(folder_key)` -> {name key: [spelled names]} of the
    files directly in it. A folder exists in the evidence when a present file is
    under it or the scan recorded it (an empty directory, a skipped link)."""

    def __init__(self, facts):
        self._kids, self._files, self._known = {}, {}, set()
        paths = list(getattr(facts, "present_paths", ()) or ())
        for full in paths:
            self._add_file(full)
        keys = set(self._known)
        for key in list(getattr(facts, "folders", ()) or ()) + list(getattr(facts, "reparse", ()) or ()):
            if key not in keys:                       # recorded folders carry keys only; their spelling is the key's
                self._add_dir_key(key)

    def _add_file(self, full):
        full = str(full).replace("/", "\\")
        parent, _sep, name = full.rpartition("\\")
        pk = path_key(parent)
        self._files.setdefault(pk, {}).setdefault(path_key(name), []).append(name)
        # every ancestor folder, spelled as this file spells it
        spelled = parent
        while spelled:
            key = path_key(spelled)
            grand, _s, leaf = spelled.rpartition("\\")
            self._kids.setdefault(path_key(grand), {}).setdefault(key, [])
            if leaf not in self._kids[path_key(grand)][key]:
                self._kids[path_key(grand)][key].append(leaf)
            self._known.add(key)
            if not grand or grand == spelled:
                break
            spelled = grand

    def _add_dir_key(self, key):
        parent, _s, leaf = key.rpartition("\\")
        self._kids.setdefault(parent, {}).setdefault(key, [])
        if leaf not in self._kids[parent][key]:
            self._kids[parent][key].append(leaf)
        self._known.add(key)

    def children(self, folder_key) -> dict:
        return self._kids.get(folder_key, {})

    def files(self, folder_key) -> dict:
        return self._files.get(folder_key, {})

    def exists(self, folder_key) -> bool:
        return folder_key in self._known


def folder_index(facts) -> FolderIndex:
    """The FolderIndex of a ScopeFacts, built once and kept on it."""
    idx = getattr(facts, "_folder_index", None)
    if idx is None:
        idx = FolderIndex(facts)
        try:
            facts._folder_index = idx
        except AttributeError:                        # a frozen stand-in: rebuild each time
            pass
    return idx


def canonical_folder(facts, root_key, root_path, folder):
    """(canonical text, how) for a destination folder against the folders the
    walk saw (RC 13, P3-A138). Each segment that `path_key`-matches an existing
    folder takes that folder's observed spelling; two existing folders that
    match with different spellings make the value ambiguous (ValueError); a
    segment that matches an existing FILE is refused (the folder could not be
    created); new segments stay as typed. `how` lists what changed."""
    if folder == "":
        return "", []
    idx = folder_index(facts)
    parent_key = root_key
    out, changed = [], []
    created = False
    for part in folder.split("\\"):
        k = path_key(part)
        if not created:
            file_hit = idx.files(parent_key).get(k)
            if file_hit:
                raise ValueError(f"'{part}' is an existing file in that folder, so a folder of that name cannot be created")
            spellings = idx.children(parent_key).get(f"{parent_key}\\{k}", [])
            if len(spellings) > 1:
                raise ValueError(f"two existing folders match '{part}' with different spellings "
                                 f"({', '.join(sorted(spellings))}): choose one")
            if spellings:
                if spellings[0] != part:
                    changed.append((part, spellings[0]))
                part = spellings[0]
            else:
                created = True
        out.append(part)
        parent_key = f"{parent_key}\\{path_key(part)}"
    return "\\".join(out), changed


def new_folders(facts, root_key, folder) -> list:
    """The folders (root-relative, as typed or canonicalised) a move to `folder`
    would create: every segment, from the first one the walk did not see.
    Compared through path_key (RC 13)."""
    if folder == "":
        return []
    idx = folder_index(facts)
    parts = folder.split("\\")
    out, parent_key, created = [], root_key, False
    for i, part in enumerate(parts):
        key = f"{parent_key}\\{path_key(part)}"
        if not created and not idx.exists(key):
            created = True
        if created:
            out.append("\\".join(parts[:i + 1]))
        parent_key = key
    return out


# -- occupancy and coverage ----------------------------------------------------------------

def _count(keys, key) -> int:
    return bisect_right(keys, key) - bisect_left(keys, key)


def _has_under(keys, folder_key) -> bool:
    if not folder_key:
        return False
    i = bisect_left(keys, folder_key + "\\")
    return i < len(keys) and keys[i].startswith(folder_key + "\\")


def chain_keys(root_key, folder) -> list:
    """The destination folder and every ancestor up to (and including) the root,
    as path keys, root first."""
    out, key = [root_key], root_key
    if folder:
        for part in folder.split("\\"):
            key = f"{key}\\{path_key(part)}"
            out.append(key)
    return out


def coverage_gap(facts, root_key, folder):
    """Why the walk cannot vouch for this destination folder, or None.

    Complete only when the root has a usable latest walk, its inaccessible list
    was not truncated, and neither the folder nor any ancestor up to the root is
    an inaccessible directory, a skipped link (a reparse directory) or has an
    unverified entry in it. Read from what the scan recorded -- the same
    evidence `required_coverage_complete` reads (scan_path_event, the inaccessible
    entries, the truncation flag); no second coverage query (P3-A145)."""
    root = (getattr(facts, "roots", None) or {}).get(root_key)
    if root is None:
        return "that root is not one of the project's scanned roots"
    if root.state != "scanned":
        return root.detail or f"the root was not scanned in the latest scan ({root.state})"
    if root_key in (getattr(facts, "truncated", None) or ()):
        return "the latest scan stored only part of the root's inaccessible list, so no folder in it can be called fully seen"
    inaccessible, reparse, unknown = facts.inaccessible, facts.reparse, facts.unknown
    keys = chain_keys(root_key, folder)
    for k in keys:
        if k != root_key and _count(reparse, k):
            return f"{_rel_of(root_key, k)} is a link the scan did not follow"
        if _count(inaccessible, k) or (k != root_key and _count(unknown, k)):
            return f"{_rel_of(root_key, k) or 'the root'} could not be read in the latest scan"
    if _has_under(unknown, keys[-1]):
        return "part of that folder could not be read in the latest scan"
    return None


def _rel_of(root_key, key) -> str:
    return key[len(root_key) + 1:] if key.startswith(root_key + "\\") else key


def through_link(facts, root_key, folder) -> bool:
    """Does the path to this destination pass through a directory link the scan
    recorded (a junction, a symbolic link, a mount point)? Such a path may land
    on another volume; `same_volume` cannot see through it."""
    reparse = getattr(facts, "reparse", None) or ()
    return any(_count(reparse, k) for k in chain_keys(root_key, folder)[1:])


@dataclass(frozen=True)
class Occupancy:
    state: str                       # free | occupied | unknown
    detail: str
    occupant: str | None = None      # the occupying path, as stored, when known


def occupancy(facts, dest_key, own_key=None, root_key=None, folder=None) -> Occupancy:
    """Is the destination path occupied, judged against the walk alone?

    A known occupant is `occupied` whatever the coverage (something IS there).
    `free` needs complete coverage of the folder chain. Everything else is
    `unknown`, and unknown blocks (P3-A135). `own_key` is the moving file's own
    current path: a case-only or normalization-only rename is not a collision
    with itself -- but a DIFFERENT file whose name path_key-equals the
    destination is (counted, not just looked up)."""
    present = facts.present
    n = _count(present, dest_key) - (1 if own_key is not None and dest_key == own_key else 0)
    if n > 0:
        who = _spelled(facts, dest_key)
        return Occupancy("occupied", f"the evidence already holds a file there ({who})", who)
    if _count(facts.inaccessible, dest_key):
        return Occupancy("occupied", "something is there that the scan found but could not read", dest_key)
    if _has_under(present, dest_key) or _count(getattr(facts, "folders", ()), dest_key):
        return Occupancy("occupied", "a folder of that name is already there", dest_key)
    if root_key is not None:
        gap = coverage_gap(facts, root_key, folder or "")
        if gap:
            return Occupancy("unknown", f"nobody can say whether the destination is free: {gap}")
    return Occupancy("free", "no file known to the evidence is at the destination, and the walk saw the whole folder chain")


def _spelled(facts, key) -> str:
    paths, keys = getattr(facts, "present_paths", ()) or (), facts.present
    i = bisect_left(keys, key)
    if i < len(keys) and keys[i] == key and i < len(paths):
        return paths[i]
    return key


# -- intents and geometry ------------------------------------------------------------------

@dataclass(frozen=True)
class Intent:
    """The current organize intent of one file location."""
    location_id: str
    move: object = None              # Decision (desired_location) or None
    rename: object = None            # Decision (desired_name) or None

    @property
    def decisions(self):
        return tuple(d for d in (self.move, self.rename) if d is not None)

    @property
    def kind(self):
        return "move_location" if self.move is not None else "rename_location"


def active_organize(ev):
    """The organize decisions that currently speak (not withdrawn, not superseded)."""
    from .model import active_decisions
    return active_decisions(getattr(getattr(ev, "organize", None), "decisions", None) or [])


def intents(ev) -> dict:
    """location_id -> Intent for every location with an active organize decision."""
    out = {}
    for d in sorted(active_organize(ev), key=lambda d: (d.sequence, str(d.decision_id))):
        cur = out.get(d.target_ref, Intent(d.target_ref))
        out[d.target_ref] = Intent(d.target_ref, d if d.kind == KIND_LOCATION else cur.move,
                                   d if d.kind == KIND_NAME else cur.rename)
    return out


@dataclass(frozen=True)
class Geometry:
    """Where an intent would put a file. Strings are byte-exact; keys go
    through path_key (collisions err toward a collision, P3-A138)."""
    location_id: str
    kind: str                        # move_location | rename_location
    from_root_key: str
    from_rel: str
    from_key: str
    to_root_key: str | None          # None: the destination root cannot be resolved
    to_folder: str
    to_name: str
    to_rel: str
    to_key: str | None
    dest_root_id: int | None = None
    case_only: bool = False


def geometry(ev, intent: Intent, root_ids=None):
    """The Geometry of one intent, or None when the location is not in the
    evidence. `root_ids` maps source_root_id -> root key (ScopeFacts.root_ids)."""
    loc = ev.locations.get(intent.location_id)
    if loc is None or not loc.rel_path:
        return None
    folder, name = split_rel(loc.rel_path)
    to_root, to_folder, root_id = loc.root_key, folder, None
    if intent.move is not None:
        v = intent.move.value
        root_id = v.get("destination_root_id")
        to_root = (root_ids or {}).get(int(root_id)) if root_id is not None else None
        to_folder = v.get("destination_folder", "")
    to_name = intent.rename.value["name"] if intent.rename is not None else name
    to_rel = join_rel(to_folder, to_name)
    to_key = full_key(to_root, to_rel) if to_root is not None else None
    from_key = full_key(loc.root_key, loc.rel_path)
    return Geometry(intent.location_id, intent.kind, loc.root_key, loc.rel_path, from_key, to_root, to_folder, to_name,
                    to_rel, to_key, int(root_id) if root_id is not None else None,
                    case_only=(to_key is not None and to_key == from_key and to_rel != loc.rel_path))


# -- chains, cycles, and the exception ------------------------------------------------------

def vacates(geoms) -> dict:
    """path key -> the geometry of the organize item that leaves it."""
    return {g.from_key: g for g in geoms if g.to_key is not None and g.to_key != g.from_key}


def chain_graph(geoms, facts) -> dict:
    """location_id -> the location_id of the item that vacates its destination,
    for every item whose ONLY occupant is another item's source (the exception
    to occupancy, P3-A136). Items whose destination is free, or occupied by
    something no item vacates, have no edge."""
    by_source = vacates(geoms)
    edges = {}
    for g in geoms:
        if g.to_key is None:
            continue
        v = by_source.get(g.to_key)
        if v is not None and v.location_id != g.location_id:
            others = _count(facts.present, g.to_key) - 1          # any other file at that key besides the vacator
            if others <= 0:
                edges[g.location_id] = v.location_id
    return edges


def cycle_members(edges) -> set:
    """Locations that lie on a dependency cycle (swaps, rotations)."""
    members = set()
    for start in edges:
        seen, cur = [], start
        while cur in edges and cur not in seen:
            seen.append(cur)
            cur = edges[cur]
        if cur in seen:
            members.update(seen[seen.index(cur):])
    return members


def intra_plan_collisions(geoms) -> dict:
    """location_id -> the other location ids sharing its destination key."""
    by_dest = {}
    for g in geoms:
        if g.to_key is not None:
            by_dest.setdefault(g.to_key, []).append(g.location_id)
    return {lid: [x for x in ids if x != lid] for ids in by_dest.values() if len(ids) > 1 for lid in ids}


# -- companions (stored paths only) -------------------------------------------------------

PATTERNS_PATH = Path(__file__).resolve().parents[2] / "Resources" / "Phase3" / "companion_patterns.json"


@lru_cache(maxsize=1)
def patterns() -> dict:
    return json.loads(PATTERNS_PATH.read_text(encoding="utf-8"))


def raw_extensions() -> frozenset:
    return frozenset(patterns()["raw_extensions"]["extensions"])


_VERSION = re.compile(r"^(?P<base>.+)_(?P<n>[0-9]+)(?P<ext>\.[^.]+)$")


@dataclass(frozen=True)
class Link:
    """One sidecar and what it belongs to, found by name alone."""
    sidecar: str                     # the sidecar's file name as stored
    kind: str                        # adobe_xmp | darktable_xmp | darktable_version_xmp
    primary: str | None              # the one primary file name; None when ambiguous
    candidates: tuple = ()           # every primary the sidecar could belong to
    ambiguous: bool = False


def detect(names) -> list:
    """The Links among the file names of ONE folder (stored names; nothing is
    read). Deterministic; the patterns are the versioned resource
    Resources\\Phase3\\companion_patterns.json (each with a cited basis)."""
    names = sorted(set(names), key=lambda n: (path_key(n), n))
    keyed = {}
    for n in names:
        keyed.setdefault(path_key(n), n)
    sidecars = [n for n in names if path_key(n).endswith(".xmp")]
    plain = [n for n in names if n not in sidecars]
    raw = raw_extensions()
    links = []
    for s in sidecars:
        t = s[:-4]
        tk = path_key(t)
        if extension_of(t) and tk in keyed and tk != path_key(s):
            links.append(Link(s, "darktable_xmp", keyed[tk]))
            continue
        m = _VERSION.match(t)
        if m is not None and tk not in keyed:
            base = path_key(m["base"] + m["ext"])
            if base in keyed:
                links.append(Link(s, "darktable_version_xmp", keyed[base]))
                continue
        group = [f for f in plain if path_key(f[:len(f) - len(extension_of(f))] if extension_of(f) else f) == tk]
        if any(extension_of(f) in raw for f in group):
            if len(group) == 1:
                links.append(Link(s, "adobe_xmp", group[0]))
            else:
                links.append(Link(s, "adobe_xmp", None, tuple(sorted(group, key=lambda n: (path_key(n), n))), True))
    return links


def mapped_sidecar_name(link: Link, primary_old: str, primary_new: str) -> str:
    """The sidecar's name once its primary is renamed (P3-A133: the dialog shows
    it; nothing propagates). `IMG_0002.NEF.xmp` -> `Trip_0002.NEF.xmp`."""
    def stem(n):
        e = extension_of(n)
        return n[:len(n) - len(e)] if e else n
    if link.kind == "adobe_xmp":
        return stem(primary_new) + ".xmp"
    if link.kind == "darktable_xmp":
        return primary_new + ".xmp"
    m = _VERSION.match(link.sidecar[:-4])
    n = m["n"] if m is not None else "1"
    e = extension_of(primary_new)
    return f"{stem(primary_new)}_{n}{primary_new[len(primary_new) - len(e):] if e else ''}.xmp"


# -- material change (R12 §5.4) -----------------------------------------------------------

def binding_drift(ev, decision, root_ids=None) -> tuple:
    """What is MATERIAL about an organize decision's evidence binding -- the
    reasons it must go back to a person (Needs Revalidation) -- in B18's
    discipline: name what is material, and what is not.

    Material: the location is missing or its root unavailable; the observed
    path or name differs from the bound value (it was moved outside the
    product); its content identity changed (a new observation whose content is
    not provably the same); its physical identity changed; the destination root
    was removed from the project.

    Not material, and so never here: an unchanged rescan; an analyzer re-run; a
    timestamp or attribute change with the same content; a change in the
    destination folder's contents; the destination root's walk run (which
    matters to a plan's obsolescence, F29, not to whether the person still
    wants the move)."""
    b = decision.binding or {}
    ob = b.get("organize") or {}
    loc = ev.locations.get(str(decision.target_ref))
    drift = []
    if loc is None or not loc.present:
        return ("the file is missing from the latest scan",)
    root = (getattr(ev, "roots", None) or {}).get(loc.root_key)
    if root is not None and not root.available:
        drift.append("its root is not available in the latest scan")
    if ob.get("path") is not None and loc.rel_path and ob["path"] != loc.rel_path:
        drift.append(f"it is no longer at {ob['path']}: it is at {loc.rel_path} now (moved or renamed outside the product)")
    if b.get("observation_id") is not None and loc.observation_id is not None \
            and str(b["observation_id"]) != str(loc.observation_id):
        bound, now = b.get("content_id"), loc.content_id
        if bound is None or now is None or str(bound) != str(now):
            drift.append("its content changed since the decision was made")
    bound_phys = ob.get("physical_id")
    if bound_phys and loc.physical_id and str(bound_phys) != str(loc.physical_id):
        drift.append("its physical identity (volume and file index) changed")
    if decision.kind == KIND_LOCATION and root_ids is not None:
        rid = decision.value.get("destination_root_id")
        if rid is None or int(rid) not in root_ids:
            drift.append("the destination root is no longer a source root of this project")
    return tuple(drift)
