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

Build 8 adds the Plan Builder's vocabulary (action kinds, preconditions,
item states). Build 9 adds metadata (at the end of this module): a fourth
target, the metadata question; the semantic field registry and the project
metadata fields; the observed value-states; the capability record's
vocabulary; and four decision kinds recorded through the same engine.
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
    """keep_both names the pair's two locations (the fixture lab's shape: ["A","B"]).

    B19 (the JSON Schema, registry.schema.json): each is a location id as
    text with no surrounding whitespace -- through B18 any value was turned
    into text (None became "None") and stripped, so ["1", " 1"] was refused
    only after stripping, a rule a schema cannot state."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("keep_both names the two file locations of the pair")
    ids = list(value)
    if not all(isinstance(v, str) and v and v == v.strip() for v in ids):
        raise ValueError("keep_both names two file locations, each its id as text")
    if ids[0] == ids[1]:
        raise ValueError("keep_both names two different file locations")
    return sorted(ids, key=_id_order)


def _id_value(value):
    """(B19) A policy or policy-version id in an override: an integer (a
    whole-number float is the same JSON number) or non-blank text. Through
    B18 anything was turned into text -- None became the id "None"."""
    if isinstance(value, bool):
        raise ValueError("an id is an integer or text")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError("an id is an integer or non-blank text")


#: (B20, RC 2) The action an override lifts protection for. One override lifts one
#: action only (P3-A144): a removal-scope override never lets a move through, an
#: organize-scope one never lets a removal through. A removal scope is stored with
#: NO member at all -- every row recorded before B20 is exactly that -- so absent
#: means removal and no existing row, fingerprint or conflict domain changes.
SCOPE_REMOVAL, SCOPE_ORGANIZE = "removal", "organize"
OVERRIDE_SCOPES = (SCOPE_REMOVAL, SCOPE_ORGANIZE)


def override_scope(value) -> str:
    """The action scope of a stored override value (absent: removal)."""
    return (value or {}).get("action_scope", SCOPE_REMOVAL) if isinstance(value, dict) else SCOPE_REMOVAL


def _override(value):
    if not isinstance(value, dict):
        raise ValueError("override_protection names the policy it overrides")
    if "policy_id" not in value or "policy_version_id" not in value:
        raise ValueError("override_protection needs policy_id and policy_version_id")
    try:
        policy_id = _id_value(value["policy_id"])
        version_id = _id_value(value["policy_version_id"])
    except ValueError:
        raise ValueError("override_protection needs policy_id and policy_version_id (integers or text)")
    out = {"policy_id": policy_id, "policy_version_id": version_id}
    if "action_scope" in value:
        scope = value["action_scope"]
        if not isinstance(scope, str) or scope not in OVERRIDE_SCOPES:
            raise ValueError("an override lifts protection for one action: removal or organize")
        if scope == SCOPE_ORGANIZE:
            out["action_scope"] = SCOPE_ORGANIZE         # removal: no member (byte-identical to B19)
    return out


def _desired_location(value):
    """desired_location: {"destination_root_id", "destination_folder", "on_collision"} (B20, R12 §5.1).
    A literal, root-relative destination -- byte-exact, never a template (P3-A126). The checks that
    need the evidence (the root is a project source root, the folder is not a file, the spelling of
    an existing folder, the destination differs from where the file is) are the store's."""
    from .organize import folder_problem, ON_COLLISION_FAIL
    if not isinstance(value, dict) or set(value) != {"destination_root_id", "destination_folder", "on_collision"}:
        raise ValueError('a desired location is {"destination_root_id", "destination_folder", "on_collision"}')
    rid = value["destination_root_id"]
    if isinstance(rid, bool):
        raise ValueError("the destination root is a source root's id")
    if isinstance(rid, float) and rid.is_integer():
        rid = int(rid)
    if not isinstance(rid, int) or rid < 1:
        raise ValueError("the destination root is a source root's id")
    why = folder_problem(value["destination_folder"])
    if why:
        raise ValueError(why)
    if value["on_collision"] != ON_COLLISION_FAIL:
        raise ValueError('on_collision is "fail": a collision is never resolved silently (P3-A134)')
    return {"destination_root_id": rid, "destination_folder": value["destination_folder"], "on_collision": ON_COLLISION_FAIL}


def _desired_name(value):
    """desired_name: {"name", "on_collision", "extension_change_confirmed"} (B20, R12 §5.2)."""
    from .organize import name_problem, ON_COLLISION_FAIL
    if not isinstance(value, dict) or set(value) != {"name", "on_collision", "extension_change_confirmed"}:
        raise ValueError('a desired name is {"name", "on_collision", "extension_change_confirmed"}')
    why = name_problem(value["name"])
    if why:
        raise ValueError(why)
    if value["on_collision"] != ON_COLLISION_FAIL:
        raise ValueError('on_collision is "fail": a collision is never resolved silently (P3-A134)')
    if not isinstance(value["extension_change_confirmed"], bool):
        raise ValueError("extension_change_confirmed is true or false")
    return {"name": value["name"], "on_collision": ON_COLLISION_FAIL,
            "extension_change_confirmed": value["extension_change_confirmed"]}


@dataclass(frozen=True)
class DecisionKind:
    key: str
    label: str
    target_kinds: frozenset
    value_kind: str                 # 'flag' | 'location_ref' | 'override' | 'location_pair' |
                                    # (Build 9) 'metadata_value' | 'project_metadata'
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
                "location_pair": _location_pair, "metadata_value": _desired_value,
                "project_metadata": _project_metadata, "desired_location": _desired_location,
                "desired_name": _desired_name}[self.value_kind](value)

    def domain_key(self, value):
        """The conflict domain instance for one recorded value.

        Protection overrides conflict per rule overridden, not per location:
        a location under a protected root AND a protected folder may carry
        one override for each. Project metadata conflicts per field: a new
        note replaces the note, never the tags.
        """
        if self.key == "override_protection":
            # B20: an organize-scope override has its own domain per policy, so a removal
            # override and an organize override on one policy never supersede each other.
            suffix = ":organize" if override_scope(value) == SCOPE_ORGANIZE else ""
            return f"{self.conflict_domain}:{value['policy_id']}{suffix}"
        if self.key == "project_metadata_set":
            return f"{self.conflict_domain}:{value['field']}"
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
        """(B19) The key is non-blank text and the display path, when given,
        is text -- through B18 any value was turned into text (5 became the
        root "5"), which registry.schema.json could not state."""
        if not isinstance(scope, dict):
            raise ValueError("a policy scope is an object")
        key_name, path_name = ("root_key", "root_path") if self.scope_kind == SOURCE_ROOT else ("path_key", "path")
        key = scope.get(key_name)
        if not isinstance(key, str) or not key.strip():
            raise ValueError("a source-root policy names the root (root_key)" if self.scope_kind == SOURCE_ROOT
                             else "a folder policy names the folder (path_key)")
        shown = scope.get(path_name)
        if shown is not None and not isinstance(shown, str):
            raise ValueError(f"a policy scope's {path_name} is text")
        key = key.strip()
        return {key_name: key, path_name: shown or key}

    def validate_effect(self, effect):
        """None or an object. Only Prefer folder reads anything from it: an
        optional `over_path_key` (non-blank text) with its display text `over`.
        (B19: None, not any empty value, means no effect; `over` is text.)"""
        if effect is None:
            effect = {}
        if not isinstance(effect, dict):
            raise ValueError("a policy effect is an object")
        out = {}
        if self.key == "prefer_folder_subtree":
            over_key, over = effect.get("over_path_key"), effect.get("over")
            if over_key is not None and over_key != "":
                if not isinstance(over_key, str) or not over_key.strip():
                    raise ValueError("Prefer folder ... over names the other folder as text (over_path_key)")
                if over is not None and not isinstance(over, str):
                    raise ValueError("Prefer folder ... over: its display text (over) is text")
                out["over_path_key"] = over_key.strip()
                out["over"] = over or out["over_path_key"]
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

PRECONDITION_CLASSES = ("identity", "retention", "evidence", "environment", "collision", "concurrency", "reversibility",
                        "integrity")          # Build 9: the source's own integrity (signed / hard-linked)
SEVERITIES = ("hard", "warning", "informational")
RESULTS = ("pass", "fail", "unknown", "not_applicable", "unsupported")
#: Level 1 (checked now, from project evidence only -- zero source reads) and
#: Level 2 (declared for Phase 4's Live Preflight; not evaluated here).
STAGE_EVIDENCE, STAGE_LIVE = "evidence_preview", "live_preflight"

#: R7 §37's baseline vocabulary with each condition's class, plus one Build 8
#: addition: no_retaining_decision (the question B13 carried forward), and
#: Build 9's three for an update_source_metadata item: writer_supported (the
#: capability record's status; only 'supported' passes) and the two
#: evidence-level integrity blockers (P3-A115 signed / certified, P3-A116
#: hard-linked), which metadata.blocked_by_evidence answers with no plan at all.
PRECONDITIONS = {
    "writer_supported": "environment",
    "source_not_signed_or_certified": "integrity",
    "source_not_encrypted": "integrity",              # B17 (P3.R8 §34)
    "no_removal_metadata_overlap": "collision",       # B17 (P3.R8 §48): a WARNING, attached only where it applies
    "source_not_hardlinked": "integrity",
    "target_exists": "identity",
    "target_observation_current": "identity",
    "target_content_matches": "identity",
    "keeper_exists": "retention",
    "keeper_content_matches": "identity",
    "minimum_location_count_satisfied": "retention",
    "minimum_physical_copy_count_satisfied": "retention",
    "target_not_protected": "retention",
    "no_retaining_decision": "retention",
    "no_dangling_protection": "retention",            # B19: a protection whose scope matches nothing scanned
    # B20 (P3.R12): move / rename items. Reused keys keep their class; these are the new ones.
    "source_identity_known": "identity",
    "no_conflicting_intent_same_location": "evidence",
    "destination_within_project_root": "environment",
    "destination_name_valid": "evidence",
    "extension_change_confirmed": "evidence",
    "same_volume": "environment",
    "no_dependency_cycle": "collision",
    "companions_travel_together": "collision",
    "destination_not_protected": "retention",
    "organize_executor_supported": "environment",
    "source_identity_matches": "identity",              # declared for Phase 4 only
    "companions_present_unchanged": "collision",        # declared for Phase 4 only
    "keeper_not_moving": "retention",
    "no_organize_metadata_overlap": "evidence",         # a WARNING, attached only where it applies (RC 7)
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
                  "rollback_record_exists", "verified_reclaim_recorded",
                  # B20 (R12 section 9.6)
                  "source_path_absent", "physical_identity_unchanged", "companions_at_destinations",
                  "no_other_file_replaced")

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
    # B20: made from desired_location / desired_name decisions. The report's 27 keys map onto the
    # built vocabulary (synthesis 3.1): reused keys keep their class; `source_local_if_required` is
    # HARD here (a warning for the others); same_volume is evaluated BEFORE the executor, so a
    # cross-volume item says "cross-volume", not "executor"; no_dependency_cycle precedes the
    # collision check, so a swap says "cycle". Order is evaluation order.
    ACTION_MOVE: (
        ("target_exists", "hard", STAGE_EVIDENCE),
        ("decision_current", "hard", STAGE_EVIDENCE),
        ("no_current_conflict", "hard", STAGE_EVIDENCE),
        ("source_identity_known", "hard", STAGE_EVIDENCE),
        ("source_local_if_required", "hard", STAGE_EVIDENCE),
        ("target_not_protected", "hard", STAGE_EVIDENCE),
        ("no_dangling_protection", "hard", STAGE_EVIDENCE),
        ("destination_within_project_root", "hard", STAGE_EVIDENCE),
        ("destination_name_valid", "hard", STAGE_EVIDENCE),
        ("extension_change_confirmed", "hard", STAGE_EVIDENCE),
        ("no_conflicting_intent_same_location", "hard", STAGE_EVIDENCE),
        ("no_dependency_cycle", "hard", STAGE_EVIDENCE),
        ("destination_collision_absent", "hard", STAGE_EVIDENCE),
        ("companions_travel_together", "hard", STAGE_EVIDENCE),
        ("source_not_hardlinked", "warning", STAGE_EVIDENCE),
        ("destination_not_protected", "warning", STAGE_EVIDENCE),
        ("same_volume", "hard", STAGE_EVIDENCE),
        ("organize_executor_supported", "hard", STAGE_EVIDENCE),
    ) + _LIVE_COMMON + (("destination_available", "hard", STAGE_LIVE), ("free_space_sufficient", "hard", STAGE_LIVE),
                        ("filesystem_capability_supported", "hard", STAGE_LIVE),
                        ("source_identity_matches", "hard", STAGE_LIVE), ("companions_present_unchanged", "hard", STAGE_LIVE),
                        ("source_local_if_required", "hard", STAGE_LIVE), ("same_volume", "hard", STAGE_LIVE)),
    ACTION_RENAME: (
        ("target_exists", "hard", STAGE_EVIDENCE),
        ("decision_current", "hard", STAGE_EVIDENCE),
        ("no_current_conflict", "hard", STAGE_EVIDENCE),
        ("source_identity_known", "hard", STAGE_EVIDENCE),
        ("source_local_if_required", "hard", STAGE_EVIDENCE),
        ("target_not_protected", "hard", STAGE_EVIDENCE),
        ("no_dangling_protection", "hard", STAGE_EVIDENCE),
        ("destination_name_valid", "hard", STAGE_EVIDENCE),
        ("extension_change_confirmed", "hard", STAGE_EVIDENCE),
        ("no_conflicting_intent_same_location", "hard", STAGE_EVIDENCE),
        ("no_dependency_cycle", "hard", STAGE_EVIDENCE),
        ("destination_collision_absent", "hard", STAGE_EVIDENCE),
        ("companions_travel_together", "hard", STAGE_EVIDENCE),
        ("source_not_hardlinked", "warning", STAGE_EVIDENCE),
        ("organize_executor_supported", "hard", STAGE_EVIDENCE),
    ) + _LIVE_COMMON + (("destination_available", "hard", STAGE_LIVE), ("filesystem_capability_supported", "hard", STAGE_LIVE),
                        ("source_identity_matches", "hard", STAGE_LIVE), ("companions_present_unchanged", "hard", STAGE_LIVE),
                        ("source_local_if_required", "hard", STAGE_LIVE)),
    # Build 9: made from desired_source_metadata_set / _clear decisions. The
    # writer is the capability record's; the integrity flags are evidence.
    ACTION_METADATA: (
        ("target_exists", "hard", STAGE_EVIDENCE),
        ("decision_current", "hard", STAGE_EVIDENCE),
        ("writer_supported", "hard", STAGE_EVIDENCE),
        ("source_not_signed_or_certified", "hard", STAGE_EVIDENCE),
        ("source_not_encrypted", "hard", STAGE_EVIDENCE),
        ("source_not_hardlinked", "hard", STAGE_EVIDENCE),
        ("source_local_if_required", "warning", STAGE_EVIDENCE),
    ) + _LIVE_COMMON,
    ACTION_PRESERVE: (),
}
#: (B19) Preconditions attached only where they apply, so a revision in which
#: none applies is exactly the earlier builds' (and fingerprints as they did):
#: no_dangling_protection on every removal while an active protection policy
#: is dangling (its scope matches nothing in the current scan -- `scopes.py`;
#: unknown, never pass: nobody can say whether the removed file was meant to be
#: protected), and B17's removal / metadata overlap warning on the two items
#: it names.
#:
#: (B20, RC 8) The same rule for the cross-kind checks organizing intent adds: a removal
#: carries no_conflicting_intent_same_location only when an organize item exists for its
#: location, and keeper_not_moving only when its keeper's location has one; a metadata item
#: and a move / rename item on one location carry the no_organize_metadata_overlap warning.
#: A revision with no organize decisions is therefore B19's, item for item and byte for byte.
CONDITIONAL_PRECONDITIONS = {
    ACTION_REMOVE: (("no_dangling_protection", "hard", STAGE_EVIDENCE),
                    ("no_conflicting_intent_same_location", "hard", STAGE_EVIDENCE),
                    ("keeper_not_moving", "hard", STAGE_EVIDENCE)),
    ACTION_METADATA: (("no_removal_metadata_overlap", "warning", STAGE_EVIDENCE),
                      ("no_organize_metadata_overlap", "warning", STAGE_EVIDENCE)),
    ACTION_MOVE: (("no_organize_metadata_overlap", "warning", STAGE_EVIDENCE),),
    ACTION_RENAME: (("no_organize_metadata_overlap", "warning", STAGE_EVIDENCE),),
    ACTION_PRESERVE: (),
}
ACTION_POSTCONDITIONS = {
    ACTION_REMOVE: ("source_removed", "keeper_still_exists", "minimum_retention_satisfied", "rollback_record_exists",
                    "verified_reclaim_recorded"),
    ACTION_MOVE: ("destination_exists", "source_path_absent", "physical_identity_unchanged", "destination_content_matches",
                  "companions_at_destinations", "no_other_file_replaced", "project_state_updated", "rollback_record_exists"),
    ACTION_RENAME: ("destination_exists", "source_path_absent", "physical_identity_unchanged", "destination_content_matches",
                    "companions_at_destinations", "no_other_file_replaced", "project_state_updated", "rollback_record_exists"),
    ACTION_METADATA: ("desired_metadata_observed", "rollback_record_exists"),
    ACTION_PRESERVE: (),
}
#: (Build 8's placeholder for a known writer profile, METADATA_WRITERS = (),
#: is replaced in Build 9 by the metadata capability record -- migration 014,
#: p3_metadata_capability -- and the writer_supported precondition.)

#: An item's state, from its evidence-stage results: a hard 'fail' blocks, a
#: hard 'unknown' blocks as evidence (P3-A91: Unknown is not Pass), a hard
#: 'unsupported' is an unsupported action; warnings never block.
ITEM_READY, ITEM_BLOCKED, ITEM_BLOCKED_EVIDENCE, ITEM_UNSUPPORTED, ITEM_PRESERVE = (
    "ready", "blocked", "blocked_evidence", "unsupported_action", "preserve")
ITEM_STATES = (ITEM_READY, ITEM_BLOCKED, ITEM_BLOCKED_EVIDENCE, ITEM_UNSUPPORTED, ITEM_PRESERVE)

#: An item's blocked_reason is the key of the hard precondition that settled
#: it -- except where the research names the reason itself (Build 9, F13 and
#: F14): WHY it is blocked, where the item's state says WHAT that produces.
BLOCKED_REASONS = {
    ("same_volume", "unsupported"): "cross_volume_move_not_supported_initially",      # B20 (F27)
    ("writer_supported", "unsupported"): "writer_unsupported",
    ("source_not_signed_or_certified", "fail"): "signed_or_certified",
    ("source_not_encrypted", "fail"): "encrypted",
    ("source_not_hardlinked", "fail"): "hardlinked_generic_write",
}


def blocked_reason(key, result):
    return BLOCKED_REASONS.get((key, result), key)
LINK_JUSTIFIES, LINK_RETAINS = "justifies", "retains"

#: The disclosure every preview makes (P3.R7 §14, required wording).
PREVIEW_DISCLOSURE = ("This preview evaluates the Plan against Project Evidence. Phase 4 must re-check live "
                      "filesystem conditions before execution.")


def action_kind(key):
    if key not in ACTION_KINDS:
        raise ValueError(f"unknown plan action kind: {key!r}")
    return key


# -- Build 9: project metadata and desired source metadata (P3.R8, scoped by R10 §4.6-§8, §16) --
#
# Four layers never collapse into one "field -> value -> save" (P3-A103):
# observed source metadata (Phase 1/2 evidence), project metadata (the
# project's own data), desired source metadata (a Phase 3 decision: what a
# person wants written LATER), and an executed / verified change (Phase 4,
# which does not exist -- nothing here simulates it). Metadata is not a
# second decision engine (R10 §4.6): the four kinds below are recorded,
# superseded and withdrawn like every other p3_decision row. Only what is
# genuinely different gets its own code: the semantic field registry, the
# capability record (migration 014), typed values, and the review data.

#: The new target: one file location x one semantic field (R10 §7).
#: target_ref = p3_metadata_question.question_id (as text).
METADATA_QUESTION = "metadata_question"

#: Value types (R8 §26's list is the ceiling; this is the floor that keeps
#: text, dates and keyword lists apart -- R10 §8: no universal free-form field).
VALUE_TYPES = ("text", "person", "date", "keyword_list", "choice")


@dataclass(frozen=True)
class MetadataField:
    key: str
    label: str
    value_type: str
    description: str
    choices: tuple = ()


#: The semantic field registry (R8 §3-4): a semantic field is not a physical
#: tag. Which physical tags a field maps to, and how to reconcile them, is
#: R8 §25's mapping profile -- a writer's concern, not built here.
METADATA_FIELDS = {f.key: f for f in (
    MetadataField("title", "Title", "text", "The document's or item's title."),
    MetadataField("creator", "Creator", "person", "Who made it (author, artist)."),
    MetadataField("content_created", "Content created", "date",
                  "When the content says it was created -- not the file system's timestamp."),
    MetadataField("description", "Description", "text", "A description or subject line."),
    MetadataField("keywords", "Keywords", "keyword_list", "Keywords or tags embedded in the file."),
    MetadataField("copyright", "Copyright", "text", "A copyright or rights statement."),
)}

#: Project metadata fields: the project's OWN data about an item, stored only
#: in the project database, edited freely, never written to a file, and fully
#: functional with no writer at all (P3-A112, P3-A113; R8 §6, §54-56).
PROJECT_METADATA_FIELDS = {f.key: f for f in (
    MetadataField("tags", "Tags", "keyword_list", "Your tags for this file."),
    MetadataField("category", "Category", "text", "Your category for this file."),
    MetadataField("note", "Note", "text", "A note about this file."),
    MetadataField("review_priority", "Review priority", "choice", "How soon it needs a look.",
                  ("low", "normal", "high")),
    MetadataField("retention_rationale", "Retention rationale", "text", "Why this file is kept."),
    MetadataField("owner", "Owner", "person", "Who the project records as its owner -- R8 §54's "
                  "project-only correction: the file keeps what it says."),
)}
#: The words any surface shows beside project metadata (P3-A112).
PROJECT_METADATA_LABEL = "Stored in the File Organizer project -- never written to the file"
DESIRED_METADATA_LABEL = ("Desired source metadata -- what you want written to the file later; nothing is written "
                          "in Phase 3, and no writer exists yet")

#: The value-states of observed metadata (R8 §5), kept apart: 'no_value' (the
#: field is empty) is not 'unknown' (nobody could say), and neither is a
#: reason to CLEAR anything (P3-A111: clear is intent, unknown is evidence).
OBS_KNOWN, OBS_NO_VALUE, OBS_UNKNOWN, OBS_UNSUPPORTED, OBS_UNAVAILABLE, OBS_STALE, OBS_CONFLICTING = (
    "known", "no_value", "unknown", "unsupported", "unavailable", "stale", "conflicting")
OBSERVED_STATES = (OBS_KNOWN, OBS_NO_VALUE, OBS_UNKNOWN, OBS_UNSUPPORTED, OBS_UNAVAILABLE, OBS_STALE, OBS_CONFLICTING)

#: The capability record's vocabularies (R8 §16-18). Only 'supported'
#: satisfies writer_supported; experimental, read_only, blocked and unknown
#: all fail it -- no partial credit (Build 9 synthesis §8).
CAPABILITY_STATUSES = ("supported", "experimental", "read_only", "blocked", "unknown")
CAPABILITY_SUPPORTED = "supported"
READ_SUPPORT = ("phase2_analyzer", "none", "unknown")
WRITE_SUPPORT = ("none", "unknown")

#: File families, by extension (a capability is per family x field).
FILE_FAMILIES = {}
for _family, _exts in (
        ("pdf", (".pdf",)),
        ("ooxml", (".docx", ".docm", ".dotx", ".xlsx", ".xlsm", ".pptx", ".pptm")),
        ("ole_office", (".doc", ".xls", ".ppt")),
        ("odf", (".odt", ".ods", ".odp")),
        ("jpeg", (".jpg", ".jpeg", ".jfif")),
        ("png", (".png",)),
        ("tiff", (".tif", ".tiff")),
        ("heif", (".heic", ".heif")),
        ("webp", (".webp",)),
        ("raw_image", (".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".raf", ".srw", ".pef")),
        ("audio", (".mp3", ".flac", ".m4a", ".ogg", ".wav", ".wma", ".aac", ".opus")),
        ("video", (".mp4", ".mov", ".mkv", ".avi", ".wmv", ".m4v", ".webm"))):
    for _e in _exts:
        FILE_FAMILIES[_e] = _family
FAMILY_OTHER = "other"
#: Families whose formats carry digital signatures or certification a
#: metadata write could invalidate (R8 §2.8, §33). Phase 1/2 records no
#: signature evidence, so for these the flag is unknown until it does.
SIGNABLE_FAMILIES = ("pdf", "ooxml", "ole_office", "odf")
#: Families whose formats can be encrypted or password-protected in a way a
#: metadata write must respect (R8 §34, B17). Elsewhere: not applicable.
ENCRYPTABLE_FAMILIES = ("pdf", "ooxml", "ole_office", "odf")
#: signature_state words the analyzers record (B17, migration 015).
SIGNATURE_STATES = ("signed", "certified", "unsigned", "unknown", "not_applicable")


def file_family(path) -> str:
    s = str(path or "").replace("/", "\\").rsplit("\\", 1)[-1].lower()
    ext = "." + s.rsplit(".", 1)[-1] if "." in s else ""
    return FILE_FAMILIES.get(ext, FAMILY_OTHER)


#: What Phase 1/2 reads, field by field: (analyzer key, promoted column or
#: detail_json key). The capability record says the same per family; this
#: says where in analyzer_result to find it.
METADATA_READ_MAP = {
    "title": (("pdf", "column", "title"), ("office", "column", "title"), ("audio", "column", "title")),
    "creator": (("pdf", "column", "author"), ("office", "column", "author")),
    "content_created": (("pdf", "column", "content_created_reported"), ("office", "column", "content_created_reported"),
                        ("raw_image", "column", "content_created_reported")),
}


TEXT_MAX = 4000        # characters, after surrounding whitespace is stripped


def _text_value(value, what="a value"):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} is non-empty text")
    value = value.strip()
    if len(value) > TEXT_MAX:
        raise ValueError(f"{what} is at most {TEXT_MAX:,} characters")
    return value


#: (B19) A date, stated without Python's own parser: ASCII digits, a real
#: calendar day in years 0001-9999, an optional time HH:MM[:SS] (00-23, 00-59,
#: 00-59) and an optional offset Z or +HH:MM / -HH:MM (00-23, 00-59). Through
#: B18 `datetime.fromisoformat` decided, and Python 3.14's accepts T24:00 and a
#: +05:60 offset -- rules registry.schema.json should not have to copy.
_DATE_SHAPE = r"([0-9]{4})-([0-9]{2})-([0-9]{2})(?:T([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?(?:Z|[+-]([0-9]{2}):([0-9]{2}))?)?"


def _date_value(value):
    import re
    s = _text_value(value, "a date")
    m = re.fullmatch(_DATE_SHAPE, s)
    if m is None:
        raise ValueError("a date is YYYY-MM-DD or YYYY-MM-DDTHH:MM[:SS][Z|+HH:MM]")
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days = (31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    if not (1 <= year and 1 <= month <= 12 and 1 <= day <= days[month - 1]):
        raise ValueError("a date names a real calendar day (years 0001-9999)")
    hour, minute, second, off_h, off_m = (int(g) if g is not None else 0 for g in m.group(4, 5, 6, 7, 8))
    if hour > 23 or minute > 59 or second > 59 or off_h > 23 or off_m > 59:
        raise ValueError("a time is 00:00 to 23:59:59, an offset -23:59 to +23:59")
    return s


def _keyword_list(value):
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("a keyword list is a non-empty list of keywords")
    out = []
    for v in value:
        v = _text_value(v, "each keyword")
        if v not in out:
            out.append(v)
    return out


def validate_field_value(spec: MetadataField, value):
    """A value for this field as it will be stored, or ValueError."""
    if spec.value_type in ("text", "person"):
        return _text_value(value, f"{spec.label}")
    if spec.value_type == "date":
        return _date_value(value)
    if spec.value_type == "keyword_list":
        return _keyword_list(value)
    if spec.value_type == "choice":
        if value not in spec.choices:
            raise ValueError(f"{spec.label} is one of: {', '.join(spec.choices)}")
        return value
    raise ValueError(f"unknown value type {spec.value_type!r}")


def metadata_field(key):
    try:
        return METADATA_FIELDS[key]
    except KeyError:
        raise ValueError(f"unknown semantic metadata field: {key!r}")


def _desired_value(value):
    """The kind-level gate for desired_source_metadata_set; the field's own
    type is checked where the question is known (store._validate_decision).
    An empty value is refused: clearing is its own decision (P3-A111, R8 §9)."""
    if value is None or (isinstance(value, str) and not value.strip()) or value == []:
        raise ValueError("to clear a field, record Clear -- an empty value is not a clear")
    return value


def _project_metadata(value):
    """project_metadata_set: {"field": one of PROJECT_METADATA_FIELDS, "value": v};
    v None means the project holds no value for that field."""
    if not isinstance(value, dict) or set(value) != {"field", "value"}:
        raise ValueError('project metadata is {"field": ..., "value": ...}')
    if not isinstance(value["field"], str) or value["field"] not in PROJECT_METADATA_FIELDS:
        raise ValueError(f"unknown project metadata field: {value.get('field')!r}")
    spec = PROJECT_METADATA_FIELDS[value["field"]]
    v = value["value"]
    return {"field": spec.key, "value": None if v is None else validate_field_value(spec, v)}


DECISION_KINDS.update({k.key: k for k in (
    DecisionKind(
        "project_metadata_set", "Set project metadata", frozenset({LOCATION}), "project_metadata",
        "none", "project_metadata",
        requires_evidence=True, supersedable=True, affects_reclaim=False, confirmation=CONFIRM_NONE,
        description="The project's own data about this file (a tag, category, note, priority, owner, why it is "
                    "kept). " + PROJECT_METADATA_LABEL + "; never a plan item."),
    DecisionKind(
        "desired_source_metadata_set", "Desired: set", frozenset({METADATA_QUESTION}), "metadata_value",
        "none", "source_metadata",
        requires_evidence=True, supersedable=True, affects_reclaim=False, confirmation=CONFIRM_NONE,
        description="What you want this field of the file to say, written later by Phase 4 if a certified writer "
                    "exists. Plans as Update Source Metadata; blocked until then."),
    DecisionKind(
        "desired_source_metadata_clear", "Desired: clear", frozenset({METADATA_QUESTION}), "flag",
        "none", "source_metadata",
        requires_evidence=True, supersedable=True, affects_reclaim=False, confirmation=CONFIRM_NONE,
        description="You want this field of the file to have no value -- the semantic clear, never a "
                    "format's empty string. Always a person's intent; an unknown observation never makes one."),
    DecisionKind(
        "leave_source_metadata_unchanged", "Leave unchanged", frozenset({METADATA_QUESTION}), "flag",
        "none", "source_metadata",
        requires_evidence=True, supersedable=True, affects_reclaim=False, confirmation=CONFIRM_NONE,
        description="You looked and want the file's value left as it is (R8 §53). Keeps the question out of "
                    "review unless the evidence materially changes; never a plan item."),
)})

#: The four metadata kinds, and which of them propose a source action.
METADATA_DECISION_KINDS = ("project_metadata_set", "desired_source_metadata_set", "desired_source_metadata_clear",
                           "leave_source_metadata_unchanged")
SOURCE_METADATA_KINDS = METADATA_DECISION_KINDS[1:]
PLANNED_METADATA_KINDS = ("desired_source_metadata_set", "desired_source_metadata_clear")

#: (B17) A metadata question can be deferred with Build 3's review events and
#: triggers -- a date, or the evidence changing (the file observed again or
#: re-read). It is not a routing target (owner's decision Q3): these events are
#: kept out of the retention model and read by metadata.py alone.
DEFER_CHOICES[METADATA_QUESTION] = ("defer_indefinitely", "snooze_until_date", "snooze_until_evidence_change")

#: (B18) Bulk Confirm on the Metadata Review queue (the owner's Q2 decision):
#: B10's frozen batch over metadata questions -- a `metadata_question` member
#: kind. Each member whose decision needs revalidation gets the SAME decision
#: re-recorded (same kind, same value) on the evidence as it is now: a
#: supersession with a fresh binding. Never a new desired value, never a
#: question without a decision, never a Clear or Leave unchanged turned into
#: anything else. Not offered by the Decide page or Similar Images (its member
#: kind is neither's). Bulk SETTING of a desired value is not this.
METADATA_CONFIRM = "metadata_confirm"
BULK_ACTIONS[METADATA_CONFIRM] = BulkAction(
    METADATA_CONFIRM, "Confirm", METADATA_QUESTION, SOURCE_METADATA_KINDS, False, None, False,
    "Re-record each checked or listed question's current decision -- the same kind and the same value -- on the "
    "evidence as it is now, for the questions whose decision needs revalidation. A question that is already current, "
    "has no decision, or whose evidence cannot support a decision is left as it is.",
    kinds_text="the same decision, re-recorded on the evidence as it is now")


# -- B20: organizing intent (P3.R12, accepted with required changes) ---------------------------
#
# Two literal, human-authored kinds in the one engine (principle 58): where a file should be, and
# what it should be called. Neither is retention (P3-A130), neither is a policy (P3-A129), a
# destination is never assigned by a rule, template or suggestion (P3-A126, P3-A141). They are
# loaded APART from the retention decisions (evidence.load_decisions(organize=True)) as the
# metadata decisions are, so the duplicate and pair router never sees them. Only a person authors
# them: explicit_human, or bulk_explicit_human for a frozen batch.

ORGANIZE_DECISION_KINDS = ("desired_location", "desired_name")
ORGANIZE_ORIGINS = ("explicit_human", "bulk_explicit_human")
ORGANIZE_LABEL = "Stored in the project; never moves the file"

DECISION_KINDS.update({k.key: k for k in (
    DecisionKind(
        "desired_location", "Desired location", frozenset({LOCATION}), "desired_location",
        "none", "organize_location",
        requires_evidence=True, supersedable=True, affects_reclaim=False, confirmation=CONFIRM_NONE,
        description="Where you want this file to be: a literal folder, relative to a project source root. "
                    + ORGANIZE_LABEL + ". Plans as Move location; every such item stays blocked until Phase 4 "
                    "certifies an executor."),
    DecisionKind(
        "desired_name", "Desired name", frozenset({LOCATION}), "desired_name",
        "none", "organize_name",
        requires_evidence=True, supersedable=True, affects_reclaim=False, confirmation=CONFIRM_NONE,
        description="What you want this file to be called: a literal Windows file name. " + ORGANIZE_LABEL
                    + ". Plans as Rename location (or as the name of a Move), blocked until Phase 4."),
)})


#: B20: a frozen batch over file locations -- "Move these to a folder" (R12 8). B10's pipeline unchanged:
#: members with dispositions, origin bulk_explicit_human + the batch id, commit equal to the preview,
#: undo_batch. Survey-only in the registry's sense: the Decide page and Similar Images never offer it
#: (its member kind is the file location, but its window is the Files page). Never a policy.
ORGANIZE_MOVE = "organize_move"
BULK_ACTIONS[ORGANIZE_MOVE] = BulkAction(
    ORGANIZE_MOVE, "Move to a folder", LOCATION, ("desired_location",), False, None, False,
    "Record one destination folder (inside a project source root) for every checked file, or every file the Files "
    "page lists now. Stored in the project; never moves the file. A file whose destination is taken, protected, a "
    "redundant candidate or not safely identified is left out and said so; sidecars found by name are added visibly.",
    survey_only=True, kinds_text="a destination (Desired location)")


# -- B18: what a plan revision records about the vocabulary it was built with (S4) --------

#: The registry's contract version; bumped when a kind, a precondition or a
#: vocabulary word changes meaning. `registry_digest()` is the content check
#: beside it: a SHA-256 over every decision kind, policy kind, action kind and
#: precondition (sorted, JSON) -- so a vocabulary that changed without a bump
#: still shows.
#:
#: /2 (B19): the rule definitions are also a JSON Schema,
#: Resources\Phase3\registry.schema.json (draft 2020-12), generated from this
#: module by `registry_schema.py`; the value gates were narrowed to what the
#: schema states (ids and keys are text, not anything str() can print; dates
#: without Python's parser); no_dangling_protection was added. The digest
#: covers the schema's bytes.
#:
#: /3 (B20): desired_location and desired_name (their value kinds, domains organize_location /
#: organize_name); override_protection's value gains the optional action_scope (an existing kind's
#: value schema -- a removal scope is stored with no member, so every earlier row is unchanged);
#: the precondition vocabulary for move / rename items and the conditional cross-kind checks.
#: The registry DIGEST therefore changes; the plan payload schema does not.
REGISTRY_VERSION = "fileorganizer.p3.registry/3"

#: (B19) What a policy's scope matches in the current scan (`scopes.py`):
#: something is there (present); the scope's root was not observed in the
#: latest scan, so nobody can say (unavailable -- offline, not renamed); or
#: the place was observed and the scope is absent from it (dangling).
SCOPE_PRESENT, SCOPE_UNAVAILABLE, SCOPE_DANGLING = "present", "unavailable", "dangling"
SCOPE_STATES = (SCOPE_PRESENT, SCOPE_UNAVAILABLE, SCOPE_DANGLING)


def registry_digest() -> str:
    import hashlib
    import json
    from .registry_schema import schema_bytes
    body = {
        "decision_kinds": sorted([k.key, sorted(k.target_kinds), k.value_kind, k.resolution_class, k.conflict_domain,
                                  k.requires_evidence, k.supersedable, k.affects_reclaim, k.confirmation]
                                 for k in DECISION_KINDS.values()),
        "policy_kinds": sorted(POLICY_KINDS),
        "action_kinds": list(ACTION_KINDS),
        "preconditions": sorted([k, v] for k, v in PRECONDITIONS.items()),
        "action_preconditions": sorted([a, [list(x) for x in v]] for a, v in ACTION_PRECONDITIONS.items()),
        "conditional_preconditions": sorted([a, [list(x) for x in v]] for a, v in CONDITIONAL_PRECONDITIONS.items()),
        "plan_states": list(PLAN_STATES), "plan_event_kinds": list(PLAN_EVENT_KINDS),
        "action_postconditions": sorted([a, list(v)] for a, v in ACTION_POSTCONDITIONS.items()),
        # B19: the bytes Resources\Phase3\registry.schema.json holds (the
        # schema suite fails if the committed file differs from them).
        "schema_sha256": hashlib.sha256(schema_bytes()).hexdigest(),
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
