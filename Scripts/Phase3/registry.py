r"""The decision-kind and policy-kind registries.

The research's first engineering gate: before a generic Decision engine
exists, say -- for every kind it will accept -- what it may target, what its
value looks like, whether it needs evidence behind it, what it conflicts
with, whether it can be superseded, whether it changes reclaim, and what the
window must ask before recording it. That is what keeps `p3_decision.value_json`
from becoming an unstructured escape hatch: the store refuses any kind or
value the registry does not describe.

Six decision kinds serve the exact-duplicate domain (Builds 1-2); five
policy kinds (protect / prefer / avoid over a source root or folder). The
vocabulary is the research's working vocabulary, adopted provisionally: the
keys below are what the database stores, so a later rename of the display
words costs nothing.

Build 3 added the review-event vocabulary (deferral dispositions and their
return kinds). Build 4 adds the origin kinds a decision may carry
(`explicit_human`, and `bulk_explicit_human` for a decision a batch
recorded), the two bulk scope kinds, the bulk actions the window offers
(each names the decision kinds it records and, where one exists, the policy
kind that says the same thing about the future), and the dispositions a
batch member can land in.

Build 5 (Image Game) adds a third target, the visual candidate pair
(`visual_candidate_pair`, target_ref = the pair's candidate_id), and the
research's rule that relationship and retention are separate decisions:

    relationship  equivalent_copy | related_variant | different
                  (one conflict domain: a new one supersedes the old)
    retention     canonical_location (reused: "this copy represents the
                  pair" -- Prefer Left / Prefer Right) | keep_both (new:
                  the pair-scoped Keep All)

A fast action ("Equivalent -> Prefer Left") records two rows in one
operation; each is its own decision, supersedable on its own. Nothing here
is a group of images: every relationship names exactly one pair.

Build 6 adds three bulk actions over pairs (`pairs_related_keep_both`,
`pairs_equivalent_keep_both`, `pairs_different`): the same rows V, E and D
record on one pair, applied pair by pair to a frozen population. None has a
policy twin -- the research rules out a standing rule for relationship or
retention decisions (P3.R4).
"""
from __future__ import annotations

from dataclasses import dataclass

# -- targets -----------------------------------------------------------------

LOCATION = "file_location"                    # target_ref: file_path_id (as text)
GROUP = "exact_duplicate_group_version"       # target_ref: content_id (as text)
VISUAL_PAIR = "visual_candidate_pair"         # target_ref: visual_candidate.candidate_id (as text) -- Build 5
TARGET_KINDS = (LOCATION, GROUP, VISUAL_PAIR)

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


def _id_order(value):
    s = str(value)
    return (0, int(s), s) if s.isdigit() else (1, 0, s)


def _location_pair(value):
    """keep_both names the pair's two locations (the fixture lab's shape: ["A","B"])."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("keep_both names the two file locations of the pair")
    ids = [str(v).strip() for v in value]
    if not all(ids) or ids[0] == ids[1]:
        raise ValueError("keep_both names two different file locations")
    return sorted(ids, key=_id_order)


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
    value_kind: str                 # 'flag' | 'location_ref' | 'override' | 'location_pair'
    resolution_class: str           # one of RESOLUTION_ORDER, or 'none' (not a retention decision)
    conflict_domain: str            # same target + same domain: the new decision supersedes the old
    requires_evidence: bool
    supersedable: bool
    affects_reclaim: bool
    confirmation: str
    description: str

    def validate_value(self, value):
        """The value as it will be stored, or ValueError."""
        return {"flag": _flag, "location_ref": _location_ref, "override": _override,
                "location_pair": _location_pair}[self.value_kind](value)

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
        "canonical_location", "Set canonical", frozenset({GROUP, VISUAL_PAIR}), "location_ref",
        "explicit_location_decision", "group_canonical",
        requires_evidence=True, supersedable=True, affects_reclaim=True, confirmation=CONFIRM_NONE,
        description="Which copy represents the group -- or, on a visual candidate pair, which of the two is "
                    "preferred (Prefer Left / Prefer Right). Optional; the canonical is always a keeper. "
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
    # -- Build 5: visual candidate pairs. Relationship (about the pair) ------
    DecisionKind(
        "equivalent_copy", "Equivalent copy", frozenset({VISUAL_PAIR}), "flag",
        "none", "pair_relationship",
        requires_evidence=True, supersedable=True, affects_reclaim=False, confirmation=CONFIRM_NONE,
        description="The same underlying image for cleanup purposes (recompressed, resized, a trivial "
                    "watermark or colour change the person considers redundant). About this pair only: "
                    "it says nothing about any third image."),
    DecisionKind(
        "related_variant", "Related variant", frozenset({VISUAL_PAIR}), "flag",
        "none", "pair_relationship",
        requires_evidence=True, supersedable=True, affects_reclaim=False, confirmation=CONFIRM_NONE,
        description="Meaningfully related but NOT interchangeable (a deliberate crop, a black-and-white edit, "
                    "a retouch, an annotated export). Its default retention is Keep Both, and it never falls "
                    "into the ordinary redundant-copy cleanup."),
    DecisionKind(
        "different", "Different", frozenset({VISUAL_PAIR}), "flag",
        "none", "pair_relationship",
        requires_evidence=True, supersedable=True, affects_reclaim=False, confirmation=CONFIRM_NONE,
        description="This candidate pair is a false positive. Pair-specific: it does not label either image "
                    "as unique."),
    # -- ... and retention (about which of the two survive) -------------------
    DecisionKind(
        "keep_both", "Keep both", frozenset({VISUAL_PAIR}), "location_pair",
        "explicit_group_decision", "pair_retention",
        requires_evidence=True, supersedable=True, affects_reclaim=True, confirmation=CONFIRM_NONE,
        description="Retain both images of the pair (the pair-scoped Keep all). With a canonical beside it, "
                    "the canonical names the representative and both stay."),
)}

#: The relationship kinds of a visual candidate pair (one conflict domain).
PAIR_RELATIONSHIPS = ("equivalent_copy", "related_variant", "different")
#: The retention kinds a pair may carry.
PAIR_RETENTION = ("canonical_location", "keep_both")


def decision_kind(key):
    try:
        return DECISION_KINDS[key]
    except KeyError:
        raise ValueError(f"unknown decision kind: {key!r}")


# -- where a decision came from (Build 4) -----------------------------------------

ORIGIN_EXPLICIT_HUMAN = "explicit_human"            # one person, one target, one click
ORIGIN_BULK_EXPLICIT_HUMAN = "bulk_explicit_human"  # one person, many targets, one previewed batch (origin_ref = batch id)
ORIGIN_KINDS = (ORIGIN_EXPLICIT_HUMAN, ORIGIN_BULK_EXPLICIT_HUMAN)


def origin_kind(key):
    if key not in ORIGIN_KINDS:
        raise ValueError(f"unknown decision origin: {key!r}")
    return key


# -- review events (Build 3) ------------------------------------------------------

EVENT_DEFERRED = "deferred"
EVENT_BLOCKED = "blocked"
EVENT_NEEDS_REVALIDATION = "needs_revalidation"
EVENT_SKIPPED = "skipped"
EVENT_RESTORED = "restored"
EVENT_KINDS = (EVENT_DEFERRED, EVENT_BLOCKED, EVENT_NEEDS_REVALIDATION, EVENT_SKIPPED, EVENT_RESTORED)

RETURN_TIME = "time"                        # snooze until a date
RETURN_EVIDENCE_CHANGE = "evidence_change"  # until the target's evidence changes
RETURN_SOURCE_AVAILABLE = "source_available"
RETURN_HASH_CURRENT = "hash_current"
RETURN_PREVIEW_AVAILABLE = "preview_available"   # both images local and present (Build 5 computes it for pairs)
RETURN_MANUAL = "manual"                    # defer indefinitely; only a person restores it
RETURN_KINDS = (RETURN_TIME, RETURN_EVIDENCE_CHANGE, RETURN_SOURCE_AVAILABLE, RETURN_HASH_CURRENT,
                RETURN_PREVIEW_AVAILABLE, RETURN_MANUAL)

#: The deferral dispositions a person may choose (synthesis §5), with the
#: return kind each records. snooze_until_preview_available is Build 5's and
#: is offered on a visual candidate pair only (see DEFER_CHOICES).
DEFER_DISPOSITIONS = {
    "defer_indefinitely": (RETURN_MANUAL, "Defer indefinitely", "Until you restore it."),
    "snooze_until_date": (RETURN_TIME, "Snooze until a date", "Returns to the queue on that day."),
    "snooze_until_evidence_change": (RETURN_EVIDENCE_CHANGE, "Snooze until the evidence changes",
                                     "Returns when a copy is observed again, or one arrives or leaves."),
    "snooze_until_source_available": (RETURN_SOURCE_AVAILABLE, "Snooze until the source is available",
                                      "Returns when every root the copies are under is available and scanned."),
    "snooze_until_hash_current": (RETURN_HASH_CURRENT, "Snooze until fingerprints are current",
                                  "Returns when every copy's fingerprint rests on its current observation."),
    "snooze_until_preview_available": (RETURN_PREVIEW_AVAILABLE, "Snooze until a preview is possible",
                                       "Returns when both images are present and local -- the product never "
                                       "downloads a cloud-only file to show it."),
}

#: Which dispositions each target offers. A group has no preview to wait
#: for; a pair of images has no fingerprint of its own to wait for.
DEFER_CHOICES = {
    GROUP: ("defer_indefinitely", "snooze_until_date", "snooze_until_evidence_change",
            "snooze_until_source_available", "snooze_until_hash_current"),
    VISUAL_PAIR: ("defer_indefinitely", "snooze_until_date", "snooze_until_evidence_change",
                  "snooze_until_source_available", "snooze_until_preview_available"),
}
DEFER_CHOICES[LOCATION] = DEFER_CHOICES[GROUP]


def event_kind(key):
    if key not in EVENT_KINDS:
        raise ValueError(f"unknown review event kind: {key!r}")
    return key


def return_kind(key):
    if key is not None and key not in RETURN_KINDS:
        raise ValueError(f"unknown return kind: {key!r}")
    return key


# -- bulk batches (Build 4) ----------------------------------------------------------

SCOPE_EXPLICIT_SELECTION = "explicit_selection"      # the exact set a person checked
SCOPE_QUERY_SNAPSHOT = "query_result_snapshot"       # what a filter listed at one moment, frozen
SCOPE_KINDS = (SCOPE_EXPLICIT_SELECTION, SCOPE_QUERY_SNAPSHOT)

#: What became of each member of a batch. Only 'decided' records a decision;
#: the others are the exceptions the preview counted and the batch kept.
DISP_DECIDED = "decided"
DISP_SATISFIED = "already_satisfied"      # an active decision already says this
DISP_PRESERVED = "preserved"              # an explicit, incompatible decision stands; never overwritten
DISP_CONFLICT = "conflict"                # recording it would contradict a hard constraint (protection, the last-copy floor)
DISP_BLOCKED = "blocked"                  # the evidence is not fit to decide on (Build 3's blockers)
DISP_NOT_APPLICABLE = "not_applicable"    # nothing to do here (no recommendation to accept, say)
DISPOSITIONS = (DISP_DECIDED, DISP_SATISFIED, DISP_PRESERVED, DISP_CONFLICT, DISP_BLOCKED, DISP_NOT_APPLICABLE)

#: How a batch treats a target that already carries incompatible intent.
#: 'preserve' is the only mode this build records; 'supersede' (deliberately
#: replacing prior intent across a batch) is the research's distinct
#: operation, left for a later build -- the store refuses it.
MODE_PRESERVE = "preserve"
MODE_SUPERSEDE = "supersede"


def scope_kind(key):
    if key not in SCOPE_KINDS:
        raise ValueError(f"unknown bulk scope kind: {key!r}")
    return key


def disposition(key):
    if key not in DISPOSITIONS:
        raise ValueError(f"unknown batch disposition: {key!r}")
    return key


@dataclass(frozen=True)
class BulkAction:
    key: str
    label: str
    member_kind: str                # what one batch member is: a group, one copy, or (Build 6) one visual pair
    decision_kinds: tuple           # the decision kinds it records (empty for a deferral)
    needs_folder: bool              # the action takes a folder (copies under it)
    policy_twin: str | None         # the policy kind that says the same about the future, if one exists
    records_events: bool            # a deferral records review events, not decisions
    description: str
    survey_only: bool = False       # (Build 7) a Survey Mode group control's action: not offered by the Bulk dialogs
    kinds_text: str | None = None   # the preview's words for what it records, when one action records a choice of rows


BULK_ACTIONS = {a.key: a for a in (
    BulkAction("keep_all", "Keep all copies", GROUP, ("keep_all_group",), False, None, False,
               "Every copy of each group is a keeper. A group with a redundant mark is left as it is (the mark is "
               "explicit intent); Keep all on one group by hand asks to withdraw its marks -- a batch never withdraws."),
    BulkAction("accept_recommendation", "Accept the recommendation", GROUP, ("canonical_location", "redundant_location"), False, None, False,
               "Where policy singles out one copy, set it as the canonical and (unless switched off) mark the other "
               "undecided copies redundant candidates. Protected and explicitly kept copies stay; a group with an "
               "explicit canonical of its own is preserved."),
    BulkAction("keep_under", "Keep copies under a folder", LOCATION, ("must_keep_location",), True, "protect_folder_subtree", False,
               "Every copy under the folder (in the groups in scope) gets an explicit Keep. The policy that says the "
               "same about the future is Protect folder."),
    BulkAction("redundant_under", "Mark copies under a folder redundant", LOCATION, ("redundant_location",), True, "avoid_folder_subtree", False,
               "Every copy under the folder (in the groups in scope) becomes a redundant candidate -- unless it is "
               "protected, explicitly kept, the canonical, or the last copy of its group. No policy can mark a copy "
               "redundant; Avoid folder only ranks such copies below others in the recommendation."),
    BulkAction("defer", "Defer", GROUP, (), False, None, True,
               "Park every group in scope with one return trigger. A group already parked is left as it is."),
    # -- Build 6: visual candidate pairs. The same two rows a fast action on
    # the Similar Images page records, pair by pair; never a group-level
    # object and never a policy (P3.R4: no dynamic policy for relationship
    # or retention decisions -- a frozen batch only).
    BulkAction("pairs_related_keep_both", "Related variant, keep both", VISUAL_PAIR, ("related_variant", "keep_both"),
               False, None, False,
               "Each pair in scope: Related variant (meaningfully related, not interchangeable) and Keep both -- two "
               "decisions per pair, exactly what V records on one pair. For a folder of variations of one prompt or scene."),
    BulkAction("pairs_equivalent_keep_both", "Equivalent copy, keep both", VISUAL_PAIR, ("equivalent_copy", "keep_both"),
               False, None, False,
               "Each pair in scope: Equivalent copy and Keep both -- what E records on one pair. Nothing becomes a "
               "redundant candidate; which copy to prefer stays a pair-by-pair choice."),
    BulkAction("pairs_different", "Different", VISUAL_PAIR, ("different",), False, None, False,
               "Each pair in scope is a false positive -- what D records on one pair. A pair that carries a retention "
               "decision is preserved (D by hand withdraws it; a batch never withdraws)."),
    # -- Build 7: Survey Mode's group controls. Each is a batch over the
    # candidate pairs inside one displayed cluster -- the same pipeline,
    # scoped to a cluster; never a group-level decision. Mark Remaining
    # Equivalent without a Canonical and Mark Selected as Related Variant
    # are the two Build 6 actions above; these three are the rest.
    BulkAction("pairs_keep_both", "Keep both (Keep All)", VISUAL_PAIR, ("keep_both",), False, None, False,
               "Each candidate pair in the cluster: Keep both -- retention only; the relationship stays each pair's own "
               "question. A pair that prefers one image, or is marked Different, is preserved.", survey_only=True),
    BulkAction("pairs_equivalent_prefer", "Equivalent copy, prefer the Canonical", VISUAL_PAIR,
               ("equivalent_copy", "canonical_location", "keep_both"), False, None, False,
               "Each candidate pair in the cluster with no relationship yet: Equivalent copy. A pair that includes the "
               "Canonical prefers it (the other image becomes that pair's redundant candidate) -- unless the other image "
               "is a Keeper; every other pair keeps both.", survey_only=True,
               kinds_text="Equivalent copy + Set canonical (pairs with the Canonical) or Keep both"),
    BulkAction("pairs_defer", "Defer", VISUAL_PAIR, (), False, None, True,
               "Park each undecided candidate pair in the cluster with one return trigger -- the per-pair deferral, "
               "fanned out; no 'group deferred' state exists.", survey_only=True),
)}


def bulk_action(key):
    try:
        return BULK_ACTIONS[key]
    except KeyError:
        raise ValueError(f"unknown bulk action: {key!r}")


def bulk_actions_for(member_kinds):
    """The bulk actions whose members are of these kinds, in registry order
    (the Decide page offers its five; the Similar Images page its three).
    Survey Mode's own actions are its group controls, not offered here."""
    kinds = set(member_kinds)
    return [a for a in BULK_ACTIONS.values() if a.member_kind in kinds and not a.survey_only]


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


# -- Build 8: the Plan Builder (P3.R7, scoped by P3.R10 §15) ---------------------
#
# A Plan records proposed LOGICAL intent. Nothing here names an execution
# method (P3-A88): remove_location is "this location should no longer
# exist"; how, reversibly, is Phase 4's, which does not exist yet.

PLAN_STATES = ("draft", "previewed", "review_ready", "locked", "handed_off", "obsolete", "superseded", "archived")
PLAN_EVENT_KINDS = ("previewed", "locked", "obsolete", "superseded", "archived")

#: R7 §8's vocabulary, narrowed to R10 §15's minimum. replace_with_hardlink
#: is deliberately absent (R7 §21: never a default cleanup strategy).
ACTION_REMOVE, ACTION_MOVE, ACTION_RENAME, ACTION_METADATA, ACTION_PRESERVE = (
    "remove_location", "move_location", "rename_location", "update_source_metadata", "no_op_preserve")
ACTION_KINDS = (ACTION_REMOVE, ACTION_MOVE, ACTION_RENAME, ACTION_METADATA, ACTION_PRESERVE)
ACTION_WORDS = {ACTION_REMOVE: "Remove location", ACTION_MOVE: "Move location", ACTION_RENAME: "Rename location",
                ACTION_METADATA: "Update source metadata", ACTION_PRESERVE: "Preserve (no action)"}
IMPACT_CLASSES = ("low", "medium", "high", "critical")
ACTION_IMPACT = {ACTION_REMOVE: "high", ACTION_MOVE: "medium", ACTION_RENAME: "medium", ACTION_METADATA: "medium",
                 ACTION_PRESERVE: "low"}

PRECONDITION_CLASSES = ("identity", "retention", "evidence", "environment", "collision", "concurrency", "reversibility")
SEVERITIES = ("hard", "warning", "informational")
RESULTS = ("pass", "fail", "unknown", "not_applicable", "unsupported")
#: Level 1 (checked now, from project evidence only -- zero source reads) and
#: Level 2 (declared for Phase 4's Live Preflight; not evaluated here).
STAGE_EVIDENCE, STAGE_LIVE = "evidence_preview", "live_preflight"

#: R7 §37's baseline vocabulary with each condition's class, plus one Build 8
#: addition: no_retaining_decision (the question B13 carried forward).
PRECONDITIONS = {
    "target_exists": "identity",
    "target_observation_current": "identity",
    "target_content_matches": "identity",
    "keeper_exists": "retention",
    "keeper_content_matches": "identity",
    "minimum_location_count_satisfied": "retention",
    "minimum_physical_copy_count_satisfied": "retention",
    "target_not_protected": "retention",
    "no_retaining_decision": "retention",
    "decision_current": "evidence",
    "policy_version_current": "evidence",
    "no_current_conflict": "evidence",
    "required_coverage_complete": "evidence",
    "source_root_available": "environment",
    "source_local_if_required": "environment",
    "destination_available": "collision",
    "destination_collision_absent": "collision",
    "filesystem_capability_supported": "environment",
    "permissions_sufficient": "environment",
    "free_space_sufficient": "environment",
    "action_lock_available": "concurrency",
    "no_conflicting_run": "concurrency",
    "rollback_ready": "reversibility",
}
POSTCONDITIONS = ("source_removed", "destination_exists", "destination_content_matches", "keeper_still_exists",
                  "minimum_retention_satisfied", "desired_metadata_observed", "project_state_updated",
                  "rollback_record_exists", "verified_reclaim_recorded")

#: What each action kind declares: (key, severity, stage), in evaluation
#: order. Evidence-stage conditions are checked by the Phase 3 Evidence
#: Preview; live-stage ones are declared for Phase 4 and never evaluated here.
_LIVE_COMMON = (("target_exists", "hard", STAGE_LIVE), ("target_content_matches", "hard", STAGE_LIVE),
                ("source_root_available", "hard", STAGE_LIVE), ("permissions_sufficient", "hard", STAGE_LIVE),
                ("action_lock_available", "hard", STAGE_LIVE), ("no_conflicting_run", "hard", STAGE_LIVE),
                ("rollback_ready", "hard", STAGE_LIVE))
ACTION_PRECONDITIONS = {
    ACTION_REMOVE: (
        ("target_exists", "hard", STAGE_EVIDENCE),
        ("decision_current", "hard", STAGE_EVIDENCE),
        ("no_current_conflict", "hard", STAGE_EVIDENCE),
        ("required_coverage_complete", "hard", STAGE_EVIDENCE),
        ("target_not_protected", "hard", STAGE_EVIDENCE),
        ("no_retaining_decision", "hard", STAGE_EVIDENCE),
        ("keeper_exists", "hard", STAGE_EVIDENCE),
        ("minimum_location_count_satisfied", "hard", STAGE_EVIDENCE),
        ("minimum_physical_copy_count_satisfied", "hard", STAGE_EVIDENCE),
        ("source_local_if_required", "warning", STAGE_EVIDENCE),
    ) + _LIVE_COMMON + (("keeper_content_matches", "hard", STAGE_LIVE),),
    ACTION_MOVE: (
        ("target_exists", "hard", STAGE_EVIDENCE),
        ("decision_current", "hard", STAGE_EVIDENCE),
        ("no_current_conflict", "hard", STAGE_EVIDENCE),
        ("destination_collision_absent", "hard", STAGE_EVIDENCE),
    ) + _LIVE_COMMON + (("destination_available", "hard", STAGE_LIVE), ("free_space_sufficient", "hard", STAGE_LIVE),
                        ("filesystem_capability_supported", "hard", STAGE_LIVE)),
    ACTION_RENAME: (
        ("target_exists", "hard", STAGE_EVIDENCE),
        ("decision_current", "hard", STAGE_EVIDENCE),
        ("no_current_conflict", "hard", STAGE_EVIDENCE),
        ("destination_collision_absent", "hard", STAGE_EVIDENCE),
    ) + _LIVE_COMMON + (("destination_available", "hard", STAGE_LIVE), ("filesystem_capability_supported", "hard", STAGE_LIVE)),
    ACTION_METADATA: (
        ("target_exists", "hard", STAGE_EVIDENCE),
        ("decision_current", "hard", STAGE_EVIDENCE),
        ("filesystem_capability_supported", "hard", STAGE_EVIDENCE),    # the known writer profile: none yet
    ) + _LIVE_COMMON,
    ACTION_PRESERVE: (),
}
ACTION_POSTCONDITIONS = {
    ACTION_REMOVE: ("source_removed", "keeper_still_exists", "minimum_retention_satisfied", "rollback_record_exists",
                    "verified_reclaim_recorded"),
    ACTION_MOVE: ("destination_exists", "destination_content_matches", "project_state_updated", "rollback_record_exists"),
    ACTION_RENAME: ("destination_exists", "destination_content_matches", "project_state_updated", "rollback_record_exists"),
    ACTION_METADATA: ("desired_metadata_observed", "rollback_record_exists"),
    ACTION_PRESERVE: (),
}
#: The known Phase 4 capability profile, as far as Phase 3 can know it: no
#: source-metadata writer exists (R10 §16 -- format certification is later
#: work), so an update_source_metadata item plans correctly and stays blocked.
METADATA_WRITERS = ()

#: An item's state, from its evidence-stage results: a hard 'fail' blocks, a
#: hard 'unknown' blocks as evidence (P3-A91: Unknown is not Pass), a hard
#: 'unsupported' is an unsupported action; warnings never block.
ITEM_READY, ITEM_BLOCKED, ITEM_BLOCKED_EVIDENCE, ITEM_UNSUPPORTED, ITEM_PRESERVE = (
    "ready", "blocked", "blocked_evidence", "unsupported_action", "preserve")
ITEM_STATES = (ITEM_READY, ITEM_BLOCKED, ITEM_BLOCKED_EVIDENCE, ITEM_UNSUPPORTED, ITEM_PRESERVE)
LINK_JUSTIFIES, LINK_RETAINS = "justifies", "retains"

#: The disclosure every preview makes (P3.R7 §14, required wording).
PREVIEW_DISCLOSURE = ("This preview evaluates the Plan against Project Evidence. Phase 4 must re-check live "
                      "filesystem conditions before execution.")


def action_kind(key):
    if key not in ACTION_KINDS:
        raise ValueError(f"unknown plan action kind: {key!r}")
    return key
