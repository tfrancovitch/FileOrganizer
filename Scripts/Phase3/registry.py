r"""The decision-kind and policy-kind registries.

The research's first engineering gate: before a generic Decision engine
exists, say -- for every kind it will accept -- what it may target, what its
value looks like, whether it needs evidence behind it, what it conflicts
with, whether it can be superseded, whether it changes reclaim, and what the
window must ask before recording it. That is what keeps `p3_decision.value_json`
from becoming an unstructured escape hatch: the store refuses any kind or
value the registry does not describe.

Six decision kinds exist in this build (the exact-duplicate domain); five
policy kinds (protect / prefer / avoid over a source root or folder). The
vocabulary is the research's working vocabulary, adopted provisionally: the
keys below are what the database stores, so a later rename of the display
words costs nothing.
"""
from __future__ import annotations

from dataclasses import dataclass

# -- targets -----------------------------------------------------------------

LOCATION = "file_location"                    # target_ref: file_path_id (as text)
GROUP = "exact_duplicate_group_version"       # target_ref: content_id (as text)

# -- the resolution order (P3.R2 keeper-rule catalog), earlier outranks later --

RESOLUTION_ORDER = (
    "hard_constraint",              # protection, explicit must-keep, minimum-copies floors
    "explicit_location_decision",   # canonical_location, redundant_location
    "explicit_group_decision",      # keep_all_group
    "folder_policy",                # prefer / avoid folder subtree
    "source_root_policy",           # prefer source root
    "project_policy",               # placeholder tier: no project-level kind exists yet
    "optional_heuristic",           # disabled by default; no heuristic ships in this build
    "display_only_tiebreak",        # presentation order only; never intent
)

# -- what the window must do before a kind is recorded ------------------------

CONFIRM_NONE = "none"                       # one click records it (and Undo is one click)
CONFIRM_EXPLICIT_OVERRIDE = "explicit_override"   # its own dialog naming the rule being defeated


def _flag(value):
    if value is not True:
        raise ValueError("this decision kind takes the value true")
    return True


def _location_ref(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("this decision kind names one file location (its id as text)")
    return value.strip()


def _override(value):
    if not isinstance(value, dict):
        raise ValueError("override_protection names the policy it overrides")
    try:
        policy_id = str(value["policy_id"]).strip()
        version_id = str(value["policy_version_id"]).strip()
    except (KeyError, TypeError):
        raise ValueError("override_protection needs policy_id and policy_version_id")
    if not policy_id or not version_id:
        raise ValueError("override_protection needs policy_id and policy_version_id")
    return {"policy_id": policy_id, "policy_version_id": version_id}


@dataclass(frozen=True)
class DecisionKind:
    key: str
    label: str
    target_kinds: frozenset
    value_kind: str                 # 'flag' | 'location_ref' | 'override'
    resolution_class: str           # one of RESOLUTION_ORDER, or 'none' (not a retention decision)
    conflict_domain: str            # same target + same domain: the new decision supersedes the old
    requires_evidence: bool
    supersedable: bool
    affects_reclaim: bool
    confirmation: str
    description: str

    def validate_value(self, value):
        """The value as it will be stored, or ValueError."""
        return {"flag": _flag, "location_ref": _location_ref, "override": _override}[self.value_kind](value)

    def domain_key(self, value):
        """The conflict domain instance for one recorded value.

        Protection overrides conflict per rule overridden, not per location:
        a location under a protected root AND a protected folder may carry
        one override for each.
        """
        if self.key == "override_protection":
            return f"{self.conflict_domain}:{value['policy_id']}"
        return self.conflict_domain


DECISION_KINDS = {k.key: k for k in (
    DecisionKind(
        "must_keep_location", "Keep", frozenset({LOCATION}), "flag",
        "hard_constraint", "location_retention",
        requires_evidence=True, supersedable=True, affects_reclaim=True, confirmation=CONFIRM_NONE,
        description="An explicit, hard 'keep this location'. Outranks every policy and every ranking."),
    DecisionKind(
        "keep_all_group", "Keep all", frozenset({GROUP}), "flag",
        "explicit_group_decision", "group_retention",
        requires_evidence=True, supersedable=True, affects_reclaim=True, confirmation=CONFIRM_NONE,
        description="Retain every current member of the group. Multiple keepers are a normal resolved state; "
                    "zero reclaim is a valid answer."),
    DecisionKind(
        "canonical_location", "Set canonical", frozenset({GROUP}), "location_ref",
        "explicit_location_decision", "group_canonical",
        requires_evidence=True, supersedable=True, affects_reclaim=True, confirmation=CONFIRM_NONE,
        description="Which copy represents the group. Optional; the canonical is always a keeper. "
                    "It says nothing about which copy came first."),
    DecisionKind(
        "redundant_location", "Mark redundant candidate", frozenset({LOCATION}), "flag",
        "explicit_location_decision", "location_retention",
        requires_evidence=True, supersedable=True, affects_reclaim=True, confirmation=CONFIRM_NONE,
        description="A candidate for future removal. Takes no effect against an active protection policy "
                    "until that protection is explicitly overridden, and never against the last copy."),
    DecisionKind(
        "defer_review", "Defer", frozenset({LOCATION, GROUP}), "flag",
        "none", "review_deferral",
        requires_evidence=True, supersedable=True, affects_reclaim=False, confirmation=CONFIRM_NONE,
        description="An explicit, durable 'not deciding yet'. Distinct from a retention decision and from "
                    "merely having looked."),
    DecisionKind(
        "override_protection", "Override protection", frozenset({LOCATION}), "override",
        "hard_constraint", "protection_override",
        requires_evidence=True, supersedable=True, affects_reclaim=True, confirmation=CONFIRM_EXPLICIT_OVERRIDE,
        description="The only way a protected location can become a redundant candidate: names the policy "
                    "rule it defeats, for this one location, with the actor and rationale recorded."),
)}


def decision_kind(key):
    try:
        return DECISION_KINDS[key]
    except KeyError:
        raise ValueError(f"unknown decision kind: {key!r}")


# -- policies ------------------------------------------------------------------

SOURCE_ROOT = "source_root"
FOLDER_SUBTREE = "folder_subtree"


@dataclass(frozen=True)
class PolicyKind:
    key: str
    label: str
    scope_kind: str                 # SOURCE_ROOT | FOLDER_SUBTREE
    effect: str                     # 'protect' | 'prefer' | 'avoid'
    resolution_class: str
    description: str

    def validate_scope(self, scope):
        if not isinstance(scope, dict):
            raise ValueError("a policy scope is an object")
        if self.scope_kind == SOURCE_ROOT:
            key = str(scope.get("root_key") or "").strip()
            if not key:
                raise ValueError("a source-root policy names the root (root_key)")
            return {"root_key": key, "root_path": str(scope.get("root_path") or key)}
        key = str(scope.get("path_key") or "").strip()
        if not key:
            raise ValueError("a folder policy names the folder (path_key)")
        return {"path_key": key, "path": str(scope.get("path") or key)}

    def validate_effect(self, effect):
        effect = effect or {}
        if not isinstance(effect, dict):
            raise ValueError("a policy effect is an object")
        out = {}
        if self.key == "prefer_folder_subtree" and effect.get("over_path_key"):
            out["over_path_key"] = str(effect["over_path_key"]).strip()
            out["over"] = str(effect.get("over") or out["over_path_key"])
        return out


POLICY_KINDS = {k.key: k for k in (
    PolicyKind("protect_source_root", "Protect source root", SOURCE_ROOT, "protect", "hard_constraint",
               "Every current file location in the root stays ineligible for removal."),
    PolicyKind("protect_folder_subtree", "Protect folder", FOLDER_SUBTREE, "protect", "hard_constraint",
               "Every current file location in the folder subtree stays ineligible for removal."),
    PolicyKind("prefer_source_root", "Prefer source root", SOURCE_ROOT, "prefer", "source_root_policy",
               "Locations in the root outrank locations elsewhere for the canonical recommendation."),
    PolicyKind("prefer_folder_subtree", "Prefer folder", FOLDER_SUBTREE, "prefer", "folder_policy",
               "Locations in the folder subtree outrank other candidates for the canonical recommendation "
               "(optionally only over one other folder)."),
    PolicyKind("avoid_folder_subtree", "Avoid folder", FOLDER_SUBTREE, "avoid", "folder_policy",
               "Locations in the folder subtree rank below otherwise equivalent candidates."),
)}


def policy_kind(key):
    try:
        return POLICY_KINDS[key]
    except KeyError:
        raise ValueError(f"unknown policy kind: {key!r}")
