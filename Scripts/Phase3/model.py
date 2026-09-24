r"""The in-memory model the resolver reads.

Evidence (locations, groups) is Phase 2 truth; intent (decisions, policies)
is Phase 3 truth. Both are loaded into these plain records so that the
resolution in `resolve.py` is a pure function of them -- the same function
runs against a project database (`evidence.py`) and against the research's
synthetic fixture lab (`p3_fixture_check.py`), which is what makes the
fixture results evidence about the product and not about a test double.

Identifiers are text throughout. In a project they are the Phase 1/2
integer ids rendered as text (a file location is its `file_path_id`, a
group is its `content_id`); in the fixtures they are 'L1', 'G1'. Nothing
here depends on their form.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def path_key(path) -> str:
    r"""One comparable form of a Windows path: backslashes, no trailing
    separator, lower-case. The same fold fo_db.path_key applies."""
    s = str(path or "").replace("/", "\\").rstrip("\\").lower()
    return s


def under(candidate_key: str, folder_key: str) -> bool:
    """Is the path (already a key) the folder itself or inside it?"""
    if not folder_key:
        return False
    return candidate_key == folder_key or candidate_key.startswith(folder_key + "\\")


@dataclass(frozen=True)
class Location:
    location_id: str
    path: str                       # the full path, for display and folder policies
    root_key: str                   # which source root it is under
    content_id: str | None
    physical_id: str | None         # volume + file index when known; hard links share it
    present: bool
    size_bytes: int | None
    observation_id: str | None      # the current observation the evidence rests on
    sort_key: tuple = ()            # display order (root ordinal, path sort key)
    # Build 3 -- the evidence conditions routing reads. True is the ordinary
    # case; the loader sets them from file_state / the root's latest scan.
    hash_current: bool = True       # its fingerprint (identity or verdict) rests on its current observation
    cloud_only: bool = False        # a placeholder the product never opens
    in_current_group: bool = True   # a member of a current exact-duplicate group

    @property
    def path_key(self) -> str:
        return path_key(self.path)


@dataclass(frozen=True)
class Group:
    group_id: str                   # == content_id as text
    content_id: str
    member_ids: tuple               # location ids in display order
    evidence_current: bool = True


@dataclass(frozen=True)
class Decision:
    decision_id: str
    target_kind: str
    target_ref: str
    kind: str
    value: Any
    origin_kind: str = "explicit_human"
    supersedes: str | None = None
    withdrawn: bool = False
    operation_id: str | None = None
    sequence: int = 0               # recording order; ties in the same class go to the later one
    binding: dict | None = None     # the evidence binding as recorded (ids only); Build 3 reads it
    occurred_utc: str = ""


@dataclass(frozen=True)
class ReviewEvent:
    """One routing event (Build 3): a deferral with its return trigger, a
    skip, what the detector found stale or blocked, or a restore."""
    review_event_id: str
    target_kind: str
    target_ref: str
    kind: str                       # deferred | blocked | needs_revalidation | skipped | restored
    return_kind: str | None = None  # time | evidence_change | source_available | hash_current | preview_available | manual
    return_condition: str | None = None
    return_on_utc: str | None = None
    detail: dict = field(default_factory=dict)
    refers_to: str | None = None    # a restore names the event it ends
    occurred_utc: str = ""
    actor_kind: str = "explicit_human"
    sequence: int = 0


@dataclass(frozen=True)
class RootCoverage:
    """What the latest scan says about a source root (Build 3's blockers)."""
    root_key: str
    complete: bool = True           # completed, available, nothing inaccessible
    available: bool = True
    detail: str = ""


@dataclass(frozen=True)
class BatchMember:
    """One target a bulk batch addressed, and what became of it (Build 4)."""
    target_kind: str
    target_ref: str
    disposition: str = "decided"    # decided | already_satisfied | preserved | conflict | blocked | not_applicable
    detail: str = ""


@dataclass(frozen=True)
class Batch:
    """A bulk batch as recorded: its frozen membership and the query or
    selection that produced it. Membership never changes after commit -- a
    later matching target is not a member (P3-A41: a snapshot is not a
    policy)."""
    batch_id: str
    scope_kind: str                 # explicit_selection | query_result_snapshot
    query: dict | None              # the frozen query (None for an explicit selection)
    members: tuple                  # BatchMember, in recorded order
    action: str = ""
    parameters: dict = field(default_factory=dict)
    frozen: bool = True
    committed: bool = True
    operation_id: str | None = None
    occurred_utc: str = ""

    def member_refs(self, target_kind=None):
        return tuple(m.target_ref for m in self.members if target_kind is None or m.target_kind == target_kind)


@dataclass(frozen=True)
class PolicyVersion:
    policy_version_id: str
    policy_id: str
    kind: str
    scope: dict
    effect: dict = field(default_factory=dict)
    version_no: int = 1
    active: bool = True


@dataclass
class Evidence:
    """Everything the resolver and the router need, and nothing they may not use."""
    locations: dict                 # location_id -> Location (current members, plus any location a decision or event names)
    groups: dict                    # group_id -> Group
    decisions: list                 # every Decision row, withdrawn and superseded ones included
    policies: list                  # the current version of every policy (active or retired)
    events: list = field(default_factory=list)      # every ReviewEvent row, oldest first
    roots: dict = field(default_factory=dict)       # root_key -> RootCoverage
    now: str = ""                                   # the moment routing is evaluated at (ISO UTC)
    batches: list = field(default_factory=list)     # Batch records, when a caller loads them (the fixture check does)

    def group_of(self, location_id):
        for g in self.groups.values():
            if location_id in g.member_ids:
                return g
        return None


def active_decisions(decisions):
    """The decisions that currently speak.

    A decision is active when it has not been withdrawn and no later
    decision supersedes it. Supersession is permanent: withdrawing the
    superseding decision does not revive the one it replaced -- the
    research's F09 fixture records the re-application as a new decision,
    and so does this product (the window offers it as one click).
    """
    superseded = {d.supersedes for d in decisions if d.supersedes}
    return [d for d in decisions if not d.withdrawn and d.decision_id not in superseded]


def active_policies(policies):
    return [p for p in policies if p.active]
