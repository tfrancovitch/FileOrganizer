r"""The rule registry as JSON Schema (B19; the R1-R2 evaluation's P0 item).

`registry.py` stays the source: the decision kinds, the policy kinds and the
value gates the store runs are code. This module states the same rules as a
JSON Schema (draft 2020-12) document, generated -- never hand-edited -- and
committed as Resources\Phase3\registry.schema.json:

  schema()          the document, as a dict
  schema_bytes()    its exact bytes (sorted keys, one-space indent, ASCII,
                    LF, one trailing newline): deterministic, so the suite's
                    staleness check cannot flap
  write(path)       writes those bytes (p3_registry_schema_check --write)

What an instance is. A recorded rule instance, in the shape the database
holds it:

  decision         {"decision_kind", "target_kind", "value"[, "origin_kind"]}
                   -- p3_decision's kind, target kind and value_json
  policy version   {"policy_kind", "scope"[, "effect"][, "status"]}
                   -- p3_policy.kind and p3_policy_version's scope_json,
                   effect_json and status

What it states: for every decision kind, the targets it may name and the
shape of its value; for every policy kind, the shape of its scope and effect.
The schema says exactly what the registry's own gates accept
(`DecisionKind.validate_value`, `PolicyKind.validate_scope` /
`validate_effect`): p3_registry_schema_check proves it both ways with an
independent validator (the `jsonschema` package, used by that suite only --
the product never imports it).

What it cannot state, because the answer is in the evidence, not the value:
a canonical must be a current member of its group or one of its pair's two
images; keep_both names exactly its pair's images; a desired value must suit
its question's semantic field (the schema gives each field's value shape as
`$defs/semantic_field.<field>` -- the suite validates each recorded desired
value against its binding's field); an override must name a protection policy
that covers the location. The store checks those against the evidence.

Rule instances are immutable already (a decision is superseded or withdrawn,
never edited; a policy changes only by a new version): the schema adds its
`$id` and `version`, and `registry.registry_digest()` covers its bytes.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import registry as REG

DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID = "urn:fileorganizer:p3:registry:3"

#: (B20) A name's length limit is in UTF-16 code units (Windows counts so: a character outside
#: the BMP is two). A regular expression cannot say that in both ECMA-262 and Python's `re`, so the
#: schema states it with this annotation keyword -- {"max": 255[, "split": "\\"]} (split: every
#: part between separators is counted on its own). A validator that does not know the keyword
#: ignores it (JSON Schema's rule for unknown keywords); the suite's validator implements it from
#: this sentence alone, independently of registry.py.
UTF16_KEYWORD = "x-fo-max-utf16-units"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "Resources" / "Phase3" / "registry.schema.json"

#: The characters Python's str.isspace() -- and so str.strip() -- treats as
#: whitespace (Python 3.14 / Unicode 16), written out: '\s' means something
#: else in an ECMA-262 validator (it leaves out U+001C-U+001F) than in
#: Python's re, and the schema must mean one thing. The suite checks the
#: running Python still agrees with this list.
PY_WHITESPACE = (0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x85, 0xA0, 0x1680,
                 *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000)


def _class_body(points) -> str:
    """A regex character-class body over code points, runs as ranges, every
    character as a \\uXXXX escape (the same in ECMA-262 and Python's re)."""
    points = sorted(set(points))
    runs, start, prev = [], None, None
    for p in points:
        if start is None:
            start = prev = p
        elif p == prev + 1:
            prev = p
        else:
            runs.append((start, prev))
            start = prev = p
    if start is not None:
        runs.append((start, prev))
    esc = lambda p: "\\u%04x" % p                                        # noqa: E731
    return "".join(esc(a) if a == b else esc(a) + "-" + esc(b) for a, b in runs)


WS = "[" + _class_body(PY_WHITESPACE) + "]"
NON_WS = "[^" + _class_body(PY_WHITESPACE) + "]"

#: A calendar date, leap years included, years 0001-9999 (registry._date_value).
_MONTH_DAY = ("(?:(?:0[13578]|1[02])-(?:0[1-9]|[12][0-9]|3[01])"
              "|(?:0[469]|11)-(?:0[1-9]|[12][0-9]|30)"
              "|02-(?:0[1-9]|1[0-9]|2[0-8]))")
_LEAP_DAY = "(?:[0-9]{2}(?:0[48]|[2468][048]|[13579][26])|(?:[02468][048]|[13579][26])00)-02-29"
_TIME = "(?:T(?:[01][0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9])?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])?)?"
DATE_PATTERN = "^" + WS + "*(?!0000)(?:[0-9]{4}-" + _MONTH_DAY + "|" + _LEAP_DAY + ")" + _TIME + WS + "*$"


#: (B20) One Windows name (a file name or a single folder segment): no reserved character
#: < > : " / \\ | ? * and no control character 0-31; it does not end in a space or a period; and it
#: is not a reserved device name (CON PRN AUX NUL COM1-9 LPT1-9, with the superscript digits) even
#: when an extension follows (NUL.tar.gz) -- a POLICY CHOICE (Microsoft words it "avoid"). ASCII
#: case-insensitivity is spelled out, so the pattern means one thing in ECMA-262 and in Python.
_RESERVED = '<>:"/\\\\|?*\\u0000-\\u001f'
_DEVICE = ("(?:[Cc][Oo][Nn]|[Pp][Rr][Nn]|[Aa][Uu][Xx]|[Nn][Uu][Ll]"
           "|[Cc][Oo][Mm][1-9\\u00b9\\u00b2\\u00b3]|[Ll][Pp][Tt][1-9\\u00b9\\u00b2\\u00b3])")
COMPONENT = "(?!" + _DEVICE + " *(?:[.]|$))[^" + _RESERVED + "]*[^" + _RESERVED + " .]"
NAME_PATTERN = "^" + COMPONENT + "$"
FOLDER_PATTERN = "^(?:" + COMPONENT + "(?:[\\\\]" + COMPONENT + ")*)?$"


def _ref(name):
    return {"$ref": f"#/$defs/{name}"}


def _field_value(spec: REG.MetadataField):
    if spec.value_type == "choice":
        return {"enum": list(spec.choices)}
    return _ref(f"field_value.{'text' if spec.value_type == 'person' else spec.value_type}")


def _defs() -> dict:
    d = {}
    # -- shared value shapes ---------------------------------------------------
    d["nonblank_text"] = {"description": "Text with at least one character that is not whitespace.",
                          "type": "string", "pattern": NON_WS}
    d["trimmed_id"] = {"description": "An id as text, with no whitespace around it.",
                       "type": "string", "minLength": 1,
                       "not": {"anyOf": [{"pattern": "^" + WS}, {"pattern": WS + "$"}]}}
    d["id"] = {"description": "A policy or policy-version id: an integer, or non-blank text.",
               "anyOf": [{"type": "integer"}, _ref("nonblank_text")]}
    d["field_value.text"] = {
        "description": f"Non-blank text, at most {REG.TEXT_MAX:,} characters once surrounding whitespace is removed.",
        "type": "string", "pattern": NON_WS,
        "not": {"pattern": NON_WS + "[\\s\\S]{%d,}" % (REG.TEXT_MAX - 1) + NON_WS}}
    d["field_value.date"] = {"description": "YYYY-MM-DD, or YYYY-MM-DDTHH:MM[:SS][Z|+HH:MM|-HH:MM]: a real calendar "
                                            "day (0001-9999), times 00:00-23:59:59, offsets up to 23:59.",
                             "type": "string", "pattern": DATE_PATTERN}
    d["field_value.keyword_list"] = {"description": "A non-empty list of keywords, each non-blank text.",
                                     "type": "array", "minItems": 1, "items": _ref("field_value.text")}
    # -- decision values, by the registry's value_kind -------------------------
    d["value.flag"] = {"description": "The value true, and nothing else.", "const": True}
    d["value.location_ref"] = {**d["nonblank_text"], "description": "One file location, its id as text."}
    d["value.location_pair"] = {"description": "The pair's two file locations, each its id as text; different.",
                                "type": "array", "minItems": 2, "maxItems": 2, "uniqueItems": True,
                                "items": _ref("trimmed_id")}
    d["value.override"] = {"description": "The protection policy (and the version) this override defeats, for one action "
                                          "(B20, P3-A144): action_scope 'removal' (the default; a removal scope is stored "
                                          "with no member, as every row before B20 is) or 'organize'.",
                           "type": "object", "required": ["policy_id", "policy_version_id"],
                           "properties": {"policy_id": _ref("id"), "policy_version_id": _ref("id"),
                                          "action_scope": {"enum": list(REG.OVERRIDE_SCOPES)}}}
    d["path.name"] = {"description": "One legal Windows file name, at most 255 UTF-16 code units (never '.' or '..').",
                      "type": "string", "pattern": NAME_PATTERN, UTF16_KEYWORD: {"max": 255}}
    d["path.folder"] = {"description": "A folder relative to a project source root: legal Windows names joined by a "
                                       "backslash, each at most 255 UTF-16 code units; '' is the root itself. No drive "
                                       "letter, no leading separator, no '.' or '..'.",
                        "type": "string", "pattern": FOLDER_PATTERN, UTF16_KEYWORD: {"max": 255, "split": "\\"}}
    d["value.desired_location"] = {
        "description": "A literal destination (P3-A126): a project source root's id and a root-relative folder, byte-exact; "
                       "a collision is never resolved silently (on_collision is 'fail', P3-A134). That the root is a "
                       "current source root, that no segment is an existing file and the spelling of an existing folder "
                       "are the store's, from the evidence.",
        "type": "object", "required": ["destination_root_id", "destination_folder", "on_collision"],
        "additionalProperties": False,
        "properties": {"destination_root_id": {"type": "integer", "minimum": 1},
                       "destination_folder": _ref("path.folder"), "on_collision": {"const": "fail"}}}
    d["value.desired_name"] = {
        "description": "A literal file name (P3-A126), refused at record time when Windows cannot hold it (P3-A142). An "
                       "extension change needs extension_change_confirmed = true -- checked by the store against the "
                       "observed name.",
        "type": "object", "required": ["name", "on_collision", "extension_change_confirmed"], "additionalProperties": False,
        "properties": {"name": _ref("path.name"), "on_collision": {"const": "fail"},
                       "extension_change_confirmed": {"type": "boolean"}}}
    d["value.metadata_value"] = {
        "description": "A desired value: anything but nothing -- clearing a field is its own decision "
                       "(desired_source_metadata_clear). Its shape per field: semantic_field.<field>.",
        "not": {"anyOf": [{"type": "null"}, {"type": "string", "not": {"pattern": NON_WS}}, {"const": []}]}}
    d["value.project_metadata"] = {
        "description": "The project's own data about a file: one field and its value (null: no value).",
        "oneOf": [{"type": "object", "required": ["field", "value"], "additionalProperties": False,
                   "properties": {"field": {"const": f.key},
                                  "value": {"anyOf": [{"type": "null"}, _field_value(f)]}}}
                  for f in REG.PROJECT_METADATA_FIELDS.values()]}
    for f in REG.METADATA_FIELDS.values():
        d[f"semantic_field.{f.key}"] = {"title": f.label, "description": f.description, **_field_value(f)}
    # -- one definition per decision kind ----------------------------------------
    for k in REG.DECISION_KINDS.values():
        d[f"decision.{k.key}"] = {
            "title": k.label, "description": k.description, "type": "object",
            "required": ["decision_kind", "target_kind", "value"],
            "properties": {"decision_kind": {"const": k.key},
                           "target_kind": {"enum": sorted(k.target_kinds)},
                           "value": _ref(f"value.{k.value_kind}"),
                           "origin_kind": {"enum": list(REG.ORIGIN_KINDS)}},
            "x-fo-resolution-class": k.resolution_class, "x-fo-conflict-domain": k.conflict_domain,
            "x-fo-requires-evidence": k.requires_evidence, "x-fo-supersedable": k.supersedable,
            "x-fo-affects-reclaim": k.affects_reclaim, "x-fo-confirmation": k.confirmation}
    d["decision"] = {"description": "A recorded decision: p3_decision's kind, target kind and value.",
                     "type": "object", "required": ["decision_kind", "target_kind", "value"],
                     "properties": {"decision_kind": {"enum": list(REG.DECISION_KINDS)}},
                     "oneOf": [_ref(f"decision.{k}") for k in REG.DECISION_KINDS]}
    # -- policies: scope and effect, then one definition per policy kind ----------
    d["scope.source_root"] = {"type": "object", "required": ["root_key"],
                              "properties": {"root_key": _ref("nonblank_text"), "root_path": {"type": ["string", "null"]}}}
    d["scope.folder_subtree"] = {"type": "object", "required": ["path_key"],
                                 "properties": {"path_key": _ref("nonblank_text"), "path": {"type": ["string", "null"]}}}
    d["effect.none"] = {"description": "No parameters (an object's other keys are not read).",
                        "type": ["object", "null"]}
    d["effect.prefer_folder_subtree"] = {
        "description": "Optionally 'over' one other folder: over_path_key (non-blank text) and its display text over.",
        "type": ["object", "null"],
        "properties": {"over_path_key": {"anyOf": [{"type": "null"}, {"const": ""}, _ref("nonblank_text")]}},
        "if": {"type": "object", "required": ["over_path_key"],
               "properties": {"over_path_key": {"type": "string", "minLength": 1}}},
        "then": {"properties": {"over": {"type": ["string", "null"]}}}}
    for p in REG.POLICY_KINDS.values():
        d[f"policy.{p.key}"] = {
            "title": p.label, "description": p.description, "type": "object",
            "required": ["policy_kind", "scope"],
            "properties": {"policy_kind": {"const": p.key}, "scope": _ref(f"scope.{p.scope_kind}"),
                           "effect": _ref("effect.prefer_folder_subtree" if p.key == "prefer_folder_subtree"
                                          else "effect.none"),
                           "status": {"enum": ["active", "retired"]}},
            "x-fo-effect": p.effect, "x-fo-resolution-class": p.resolution_class}
    d["policy_version"] = {"description": "A recorded policy version: p3_policy.kind with the version's scope, "
                                          "effect and status.",
                           "type": "object", "required": ["policy_kind", "scope"],
                           "properties": {"policy_kind": {"enum": list(REG.POLICY_KINDS)}},
                           "oneOf": [_ref(f"policy.{k}") for k in REG.POLICY_KINDS]}
    return d


def schema() -> dict:
    return {
        "$schema": DIALECT,
        "$id": SCHEMA_ID,
        "version": REG.REGISTRY_VERSION,
        "title": "The File Organizer -- Phase 3 rule registry",
        "description": ("The decision kinds and policy kinds Phase 3 records, as rule definitions: what each may target "
                        "and the shape of its value, scope and effect. Generated from Scripts\\Phase3\\registry.py by "
                        "Scripts\\Phase3\\registry_schema.py -- do not edit. An instance is one recorded decision or "
                        "policy version; checks that need the evidence (group or pair membership, a question's field, "
                        "the policy an override names) are the store's, not this schema's."),
        "oneOf": [_ref("decision"), _ref("policy_version")],
        "$defs": _defs(),
    }


def schema_bytes() -> bytes:
    return (json.dumps(schema(), indent=1, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def write(path=None) -> Path:
    path = Path(path) if path is not None else SCHEMA_PATH
    path.write_bytes(schema_bytes())
    return path


def decision_instance(kind, target_kind, value, origin_kind=None) -> dict:
    """A decision as the schema reads it."""
    out = {"decision_kind": kind, "target_kind": target_kind, "value": value}
    if origin_kind is not None:
        out["origin_kind"] = origin_kind
    return out


def policy_instance(kind, scope, effect=None, status=None) -> dict:
    """A policy version as the schema reads it."""
    out = {"policy_kind": kind, "scope": scope}
    if effect is not None:
        out["effect"] = effect
    if status is not None:
        out["status"] = status
    return out
