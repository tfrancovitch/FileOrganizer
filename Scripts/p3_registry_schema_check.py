r"""B19 -- the rule registry as JSON Schema (the R1-R2 evaluation's P0 item).

    py -3 Scripts\p3_registry_schema_check.py            everything (section 6 runs every Phase 3 suite)
    py -3 Scripts\p3_registry_schema_check.py --quick    sections 1-5 only
    py -3 Scripts\p3_registry_schema_check.py --write    regenerate Resources\Phase3\registry.schema.json

The code registry (Phase3\registry.py) stays the source; Phase3\
registry_schema.py generates Resources\Phase3\registry.schema.json from it.
This suite checks the two cannot drift apart and that the schema says exactly
what the code accepts -- with an INDEPENDENT validator, the `jsonschema`
package (draft 2020-12). The registry's own gates are what is under test; they
are never used to decide what the schema should say. `jsonschema` is this
suite's dependency only: the product never imports it (checked below).

  1. The committed schema: byte-identical to a fresh generation (the
     staleness check), deterministic, valid against the draft 2020-12
     metaschema, carrying $id and the registry version, one definition per
     decision kind and per policy kind; registry_digest() covers its bytes.
  2. The whitespace list the patterns spell out is the running Python's.
  3. The cross-check, both ways: for every decision kind x every target kind x
     a corpus of adversarial and seeded-random values, and every policy kind x
     scopes x effects, and every semantic field x values -- the code accepts
     it if and only if the schema does. A case either way is a failure, listed.
     Every refusal is a ValueError (a TypeError would be a sloppy refusal).
  4. Normalization: what the code stores from an accepted value is accepted
     again, by both, unchanged.
  5. The fixture lab (F01-F16): every decision and policy version the
     product's loader reads validates.
  6. The suites: every Phase 3 suite runs in a subprocess with a capture hook
     on the store's insert points (_decision_row, record_override,
     create_policy, _new_version); every decision and policy version any of
     them recorded validates -- each desired metadata value against its
     question's field too -- and every decision kind and policy kind was
     recorded at least once.
"""
from __future__ import annotations

import json
import os
import random
import runpy
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
from Phase3 import registry_schema as RS                            # noqa: E402

RESULTS = []
CAPTURE_ENV = "FO_P3_SCHEMA_CAPTURE"


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def _validator_class():
    """Draft 2020-12 plus the one annotation keyword the schema defines (B20):
    x-fo-max-utf16-units -- {"max": n[, "split": sep]}, a string's length in UTF-16 code
    units (each part between separators on its own). Written here from the schema's own
    sentence (registry_schema.UTF16_KEYWORD), independently of registry.py / organize.py."""
    import jsonschema
    from jsonschema import exceptions

    def utf16(_validator, spec, instance, _schema):
        if not isinstance(instance, str):
            return
        parts = instance.split(spec["split"]) if spec.get("split") else [instance]
        for part in parts:
            if len(part.encode("utf-16-le", "surrogatepass")) // 2 > spec["max"]:
                yield exceptions.ValidationError(f"more than {spec['max']} UTF-16 code units")
                return
    return jsonschema.validators.extend(jsonschema.Draft202012Validator, {RS.UTF16_KEYWORD: utf16})


def _validator(root_ref=None):
    doc = RS.schema()
    if root_ref is not None:
        doc = {"$schema": doc["$schema"], "$id": doc["$id"] + ":" + root_ref, "$defs": doc["$defs"],
               "$ref": f"#/$defs/{root_ref}"}
    return _validator_class()(doc)


def _committed():
    raw = RS.SCHEMA_PATH.read_bytes()
    return raw, _validator_class()(json.loads(raw.decode("ascii")))


# -- 1. the committed schema ----------------------------------------------------------------

def test_committed():
    section("1. The committed schema (Resources\\Phase3\\registry.schema.json)")
    import hashlib
    import jsonschema
    check("the schema file exists", RS.SCHEMA_PATH.is_file(), str(RS.SCHEMA_PATH))
    if not RS.SCHEMA_PATH.is_file():
        return
    raw = RS.SCHEMA_PATH.read_bytes()
    fresh = RS.schema_bytes()
    check("staleness: the committed bytes are exactly a fresh generation from registry.py "
          "(regenerate with --write after a registry change)", raw == fresh,
          f"committed {hashlib.sha256(raw).hexdigest()[:16]} vs generated {hashlib.sha256(fresh).hexdigest()[:16]}")
    check("generation is deterministic: twice in a row, and in a fresh interpreter, the same bytes",
          RS.schema_bytes() == fresh and subprocess.run(
              [sys.executable, "-c", "import sys; sys.path.insert(0, r'%s'); from Phase3 import registry_schema as S; "
               "sys.stdout.buffer.write(S.schema_bytes())" % SCRIPTS], capture_output=True).stdout == fresh)
    check("the bytes are ASCII JSON with sorted keys, LF line ends and one trailing newline",
          raw.endswith(b"\n") and not raw.endswith(b"\n\n") and b"\r" not in raw
          and raw == (json.dumps(json.loads(raw), indent=1, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii"))
    doc = json.loads(raw)
    try:
        jsonschema.Draft202012Validator.check_schema(doc)
        meta_ok, why = True, ""
    except jsonschema.SchemaError as exc:                            # pragma: no cover - a failure path
        meta_ok, why = False, str(exc)[:200]
    check("valid against the draft 2020-12 metaschema", meta_ok and doc.get("$schema") == RS.DIALECT, why)
    check("it carries its $id and version: the registry's contract version (fileorganizer.p3.registry/3, B20)",
          doc.get("$id") == RS.SCHEMA_ID and doc.get("version") == REG.REGISTRY_VERSION == "fileorganizer.p3.registry/3")
    defs = doc.get("$defs", {})
    check(f"one definition per decision kind ({len(REG.DECISION_KINDS)}), naming its targets and its value schema",
          all(f"decision.{k}" in defs and defs[f"decision.{k}"]["properties"]["target_kind"]["enum"] == sorted(s.target_kinds)
              and defs[f"decision.{k}"]["properties"]["value"] == {"$ref": f"#/$defs/value.{s.value_kind}"}
              for k, s in REG.DECISION_KINDS.items())
          and {k[len("decision."):] for k in defs if k.startswith("decision.")} == set(REG.DECISION_KINDS))
    check(f"one definition per policy kind ({len(REG.POLICY_KINDS)}), naming its scope and effect schemas",
          all(f"policy.{k}" in defs and defs[f"policy.{k}"]["properties"]["scope"] == {"$ref": f"#/$defs/scope.{s.scope_kind}"}
              for k, s in REG.POLICY_KINDS.items())
          and {k[len("policy."):] for k in defs if k.startswith("policy.")} == set(REG.POLICY_KINDS))
    check("one value shape per semantic metadata field (for a desired value, checked against its question's field)",
          {k[len("semantic_field."):] for k in defs if k.startswith("semantic_field.")} == set(REG.METADATA_FIELDS))
    # The digest covers the schema's bytes: the same registry with other schema
    # bytes gives another digest.
    base = REG.registry_digest()
    original = RS.schema_bytes
    try:
        RS.schema_bytes = lambda: original() + b" "
        moved = REG.registry_digest()
    finally:
        RS.schema_bytes = original
    check("registry_digest() covers the schema's bytes (one byte more, another digest; restored, the same)",
          moved != base and REG.registry_digest() == base and len(base) == 64)
    runtime = [p for p in (SCRIPTS / "Phase3").glob("*.py")] + [p for p in (SCRIPTS / "Phase2").glob("*.py")] + \
              [p for p in (SCRIPTS / "Database").glob("*.py")] + [SCRIPTS / "Dashboard.py", SCRIPTS / "RunCoordinator.py"]
    import re
    imports = re.compile(r"^\s*(?:import|from)\s+jsonschema\b", re.M)
    importers = [p.name for p in runtime if imports.search(p.read_text(encoding="utf-8", errors="replace"))]
    check("the runtime does not import jsonschema (no product module has an import statement for it)", not importers,
          ", ".join(importers))
    probe = subprocess.run([sys.executable, "-c",
                            "import sys; sys.path.insert(0, r'%s'); sys.path.insert(0, r'%s'); "
                            "import Phase3.registry as R, Phase3.registry_schema, Phase3.store, Phase3.plans, Phase3.review, "
                            "Phase3.scopes, Phase2.gui; R.registry_digest(); print('jsonschema' in sys.modules)"
                            % (SCRIPTS, SCRIPTS / "Database")], capture_output=True, text=True)
    check("...and importing the product (registry, store, plans, the Decide page, the window) and computing the digest "
          "loads no jsonschema module", probe.returncode == 0 and probe.stdout.strip() == "False",
          (probe.stdout + probe.stderr)[-300:])


# -- 2. whitespace -----------------------------------------------------------------------------

def test_whitespace():
    section("2. The whitespace the patterns spell out")
    running = tuple(c for c in range(0x110000) if chr(c).isspace())
    check(f"PY_WHITESPACE is exactly the running Python's str.isspace() set ({len(running)} code points; "
          f"Python {sys.version.split()[0]})", tuple(sorted(RS.PY_WHITESPACE)) == running,
          f"differs: {sorted(set(running) ^ set(RS.PY_WHITESPACE))[:10]}")
    import re
    check("U+001C..U+001F are whitespace to str.strip() (and so to the patterns), U+180E is not",
          all(chr(c).isspace() for c in range(0x1C, 0x20)) and not "\u180e".isspace()
          and re.search(RS.NON_WS, "\x1c\x1d") is None and re.search(RS.NON_WS, "\u180e") is not None)


# -- 3. the cross-check ------------------------------------------------------------------------

_WS_SAMPLES = ["", " ", "\t", "\n", "\x0b", "\x0c", "\r", "\x1c", "\x1f", "\x85", "\xa0", "\u1680", "\u2000", "\u200a",
               "\u2028", "\u2029", "\u202f", "\u205f", "\u3000", "\u180e", "\u200b", "\ufeff"]
_ATOMS = [None, True, False, 0, 1, -1, 7, 1.0, 1.5, -0.0, 12.0, 1e20, "", " ", "a", " a ", "a\n", "\n", "1", " 1", "1 ",
          "12", "3", "L1", "L2", "P1", "PV1", "\x1c", "\u180e", "\u3000a", "a\u3000", "None", "true", "\u00e9", "e\u0301",
          [], {}, [1], ["1"], ("A", "B")]


def _random_text(rng, n=None):
    alphabet = ["a", "b", "Z", "0", "7", "-", ":", "T", "\\", ".", "\u00e9", "\u4e2d"] + _WS_SAMPLES[1:]
    n = rng.randint(0, 8) if n is None else n
    return "".join(rng.choice(alphabet) for _ in range(n))


def _random_value(rng, depth=0):
    r = rng.random()
    if r < 0.30:
        return _random_text(rng)
    if r < 0.40:
        return rng.choice([None, True, False])
    if r < 0.52:
        return rng.choice([rng.randint(-3, 30), float(rng.randint(0, 5)), rng.random()])
    if r < 0.78 and depth < 2:
        return [_random_value(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    if depth < 2:
        keys = ["policy_id", "policy_version_id", "field", "value", "root_key", "path_key", "x"]
        return {rng.choice(keys): _random_value(rng, depth + 1) for _ in range(rng.randint(0, 3))}
    return _random_text(rng)


def _dates():
    out = ["2024-02-29", "2023-02-29", "1900-02-29", "2000-02-29", "2400-02-29", "2100-02-29", "0000-01-01", "0001-01-01",
           "9999-12-31", "0004-02-29", "0000-02-29", "2026-13-01", "2026-00-10", "2026-04-31", "2026-04-30", "2026-06-31",
           "2026-01-00", "2026-01-32", "2026-01-01T24:00", "2026-01-01T23:59", "2026-01-01T23:59:59", "2026-01-01T23:59:60",
           "2026-01-01T23:60", "2026-01-01T10:00Z", "2026-01-01T10:00:00Z", "2026-01-01T10:00+05:60", "2026-01-01T10:00+23:59",
           "2026-01-01T10:00-23:59", "2026-01-01T10:00-24:00", "2026-01-01T10:00+05:30", "2026-01-01T10:00+0530",
           " 2026-01-01 ", "2026-01-01\n", "\n2026-01-01", "\u3000 2026-01-01\x1c", "\u0662\u0660\u0662\u0666-01-01",
           "2026-1-01", "2026-01-01T10", "20260101", "2026-01-01T10:00Zx", "2026-01-01 10:00", "2026-01-01t10:00",
           "2026-02-28T00:00:00-00:00", "2026-02-29", "1600-02-29", "1700-02-29", "2026-01-01T\u0661\u0660:00"]
    return out


def _texts():
    base = "a" * (REG.TEXT_MAX - 2)
    return ["x" * REG.TEXT_MAX, "x" * (REG.TEXT_MAX + 1), " " + "x" * REG.TEXT_MAX + " ", "\n" + "x" * REG.TEXT_MAX + "\x1c",
            "x" + " " * (REG.TEXT_MAX - 2) + "x", "x" + " " * (REG.TEXT_MAX - 1) + "x", "a" + base + "a",
            "a" + base + "aa", "\u3000" * 5000 + "y", "y" + "\u3000" * 5000]


def _organize_values():
    """(B20) Values for desired_location and desired_name: legal and illegal names, folders,
    lengths in UTF-16 units (a non-BMP character is two), device names with and without an
    extension, rooted paths, '..', ids of every type, every on_collision."""
    names = ["a.txt", "A", "a b", "a.b.c", "x" * 255, "x" * 256, "\U0001f600" * 127, "\U0001f600" * 128, "\U0001f600" * 127 + "x",
             "\U0001f600" * 127 + "xx", "", " ", ".", "..", "a.", "a ", " a", "a b.", "CON", "con", "Con.txt", "NUL.tar.gz", "nul .txt",
             "aux", "AUX.", "COM1", "com9.txt", "COM0", "COM\u00b9", "LPT\u00b2.log", "LPT\u00b3", "COM10", "CONX", "XCON", "CON x.txt",
             "a|b", "a:b", "a*b", "a?b", 'a"b', "a<b", "a>b", "a/b", "a\\b", "a\x01b", "a\x1fb", "a\x7fb", "a\nb", "\u00e9", "e\u0301",
             "\u4e2d\u6587.pdf", "report.", "report", "a\ud800b", "\u3000a", "a\u3000"]
    folders = ["", "Docs", "Docs\\2024", "Docs\\aux", "Docs\\AUX.old", "aux", "a\\b\\c\\d", "x" * 255, "x" * 256, "ok\\" + "x" * 256,
               "x" * 255 + "\\" + "y" * 255, "\U0001f600" * 128, "\\", "\\Docs", "Docs\\", "Docs\\\\x", "C:", "C:\\Docs", "c:Docs",
               "\\\\srv\\share", "..", "..\\..", "Docs\\..", ".\\Docs", "Docs\\.", "a/b", "Docs\\a|b", "Docs\\NUL", "Docs\\x.", "Docs\\x ",
               " Docs", "\u00e9\\e\u0301", 5, None, True, [], ["Docs"], {}]
    out = []
    for rid in [1, 2, 12, 1.0, 1.5, 0, -1, True, False, None, "1", "", [], {}, 10 ** 12]:
        out.append({"destination_root_id": rid, "destination_folder": "Docs", "on_collision": "fail"})
    for folder in folders:
        out.append({"destination_root_id": 1, "destination_folder": folder, "on_collision": "fail"})
    for oc in ["fail", "FAIL", "skip", "replace", "", None, True, [], "rename", "create_unique_name"]:
        out.append({"destination_root_id": 1, "destination_folder": "Docs", "on_collision": oc})
        out.append({"name": "a.txt", "on_collision": oc, "extension_change_confirmed": False})
    for name in names:
        for conf in (True, False):
            out.append({"name": name, "on_collision": "fail", "extension_change_confirmed": conf})
    for conf in [0, 1, "true", "", None, [], {}, "false"]:
        out.append({"name": "a.txt", "on_collision": "fail", "extension_change_confirmed": conf})
    out += [{"destination_root_id": 1, "destination_folder": "Docs"}, {"destination_root_id": 1, "on_collision": "fail"},
            {"destination_folder": "Docs", "on_collision": "fail"}, {"name": "a.txt"}, {"name": "a.txt", "on_collision": "fail"},
            {"destination_root_id": 1, "destination_folder": "Docs", "on_collision": "fail", "name": "a.txt"},
            {"name": "a.txt", "on_collision": "fail", "extension_change_confirmed": False, "extra": 1}, "Docs", "a.txt"]
    return out


def _value_corpus(seed):
    rng = random.Random(seed)
    values = list(_ATOMS) + list(_WS_SAMPLES) + _dates() + _texts()
    ids = ["1", "2", "12", "L1", "L2", " 1", "1 ", "", " ", "\x1c", 1, 2, None, True]
    values += [[a, b] for a in ids for b in ids] + [["1", "2", "3"], ["1"], ("1", "2"), ["b", "a"]]
    for pid in [1, 2, "1", "P1", " P1 ", "", " ", None, True, False, 1.0, 1.5, [], {}, "\x1c", 0, -3]:
        values.append({"policy_id": pid, "policy_version_id": 2})
        values.append({"policy_id": 1, "policy_version_id": pid})
    values += [{"policy_id": 1}, {"policy_version_id": 1}, {"policy_id": 1, "policy_version_id": 2, "extra": None}]
    for scope in ["removal", "organize", "", "Organize", " organize", None, True, 1, [], {}, "delete"]:
        values.append({"policy_id": 1, "policy_version_id": 2, "action_scope": scope})
    values += _organize_values()
    for field in list(REG.PROJECT_METADATA_FIELDS) + ["creator", "", "x", 1, None, ["note"]]:
        for v in [None, "x", " x ", "", " ", "\x1c", ["a"], [], ["a", " "], ["a", "a"], "low", "high", "urgent", 1, True,
                  "x" * (REG.TEXT_MAX + 1), " " + "x" * REG.TEXT_MAX + " "]:
            values.append({"field": field, "value": v})
    values += [{"field": "note"}, {"value": "x"}, {"field": "note", "value": "x", "extra": 1}, {}]
    values += [_random_value(rng) for _ in range(500)]
    values += [[_random_text(rng) for _ in range(rng.randint(0, 3))] for _ in range(150)]
    # The domain is JSON values -- what value_json can hold (a tuple is stored as an array).
    return [json.loads(json.dumps(v)) for v in values]


def _code_accepts(fn, value):
    """(accepted, normalized, refusal exception type)"""
    try:
        return True, fn(value), None
    except ValueError:
        return False, None, None
    except Exception as exc:                                        # noqa: BLE001
        return False, None, type(exc).__name__


def test_cross_check():
    section("3. The cross-check, both ways: the code accepts it if and only if the schema does")
    from Phase3.registry import METADATA_QUESTION
    targets = list(REG.TARGET_KINDS) + [METADATA_QUESTION, "folder_scope"]
    root = _validator()
    values = _value_corpus(19)
    # A decision is valid when its target is one the kind takes AND its value
    # passes the kind's value gate; the code checks the two apart
    # (DecisionKind.target_kinds, validate_value -- one gate per value_kind)
    # and so does the schema (the target enum, $defs/value.<value_kind>). So:
    # (a) every value against every value gate, (b) every kind against every
    # target, (c) the composition through the root schema on a sample.
    mismatches, sloppy, n, accepted = [], [], 0, 0
    kinds_seen = {k: [0, 0] for k in REG.DECISION_KINDS}
    by_value_kind = {}
    for kind, spec in REG.DECISION_KINDS.items():
        by_value_kind.setdefault(spec.value_kind, spec)
    gate_ok = {}
    for vk, spec in by_value_kind.items():
        sub = _validator(f"value.{vk}")
        for i, v in enumerate(values):
            ok, _norm, exc = _code_accepts(spec.validate_value, v)
            gate_ok[(vk, i)] = ok
            if exc:
                sloppy.append((vk, repr(v)[:60], exc))
            n += 1
            accepted += ok
            if ok != sub.is_valid(v):
                mismatches.append((vk, repr(v)[:80], "code accepts, schema rejects" if ok else "schema accepts, code refuses"))
    check(f"decision values: {n:,} cases ({len(by_value_kind)} value gates x {len(values):,} values; {accepted:,} "
          f"accepted) -- no case the code accepts and the schema rejects, or the reverse",
          not mismatches, f"{len(mismatches)} mismatch(es), e.g. {mismatches[:3]}")
    tm = []
    for kind, spec in REG.DECISION_KINDS.items():
        sample = next(v for i, v in enumerate(values) if gate_ok[(spec.value_kind, i)])
        sub = _validator(f"decision.{kind}")
        for tk in targets:
            if (tk in spec.target_kinds) != sub.is_valid(RS.decision_instance(kind, tk, sample)):
                tm.append((kind, tk))
    check(f"decision targets: every kind x {len(targets)} targets -- the schema permits exactly the targets the registry "
          "permits", not tm, str(tm[:3]))
    rng = random.Random(7)
    sample_values = [values[i] for i in sorted(rng.sample(range(len(values)), 60))]
    for vk in by_value_kind:                    # and, for each gate, a value it accepts
        sample_values.append(next(v for i, v in enumerate(values) if gate_ok[(vk, i)]))
    cm, cn = [], 0
    for kind, spec in REG.DECISION_KINDS.items():
        for tk in targets:
            for v in sample_values:
                ok, _norm, _exc = _code_accepts(spec.validate_value, v)
                code = ok and tk in spec.target_kinds
                kinds_seen[kind][0 if code else 1] += 1
                cn += 1
                if code != root.is_valid(RS.decision_instance(kind, tk, v)):
                    cm.append((kind, tk, repr(v)[:80]))
    check(f"decisions through the root schema: {cn:,} cases ({len(REG.DECISION_KINDS)} kinds x {len(targets)} targets x "
          f"{len(sample_values)} sampled values) -- the whole document agrees with the code", not cm, str(cm[:3]))
    check("every decision kind was both accepted and refused somewhere in the corpus (the cross-check reached each gate)",
          all(a and r for a, r in kinds_seen.values()), str({k: v for k, v in kinds_seen.items() if not (v[0] and v[1])}))
    check("every refusal is a ValueError (no gate fails with another exception)", not sloppy, str(sloppy[:3]))
    check("an unknown decision kind or origin is refused by the schema",
          not root.is_valid(RS.decision_instance("delete_file", REG.LOCATION, True))
          and not root.is_valid(RS.decision_instance("must_keep_location", REG.LOCATION, True, origin_kind="machine"))
          and root.is_valid(RS.decision_instance("must_keep_location", REG.LOCATION, True, origin_kind="bulk_explicit_human")))

    # Policies: kinds x scopes x effects.
    rng = random.Random(23)
    keys = ["c:\\docs", " c:\\docs ", "", " ", "\x1c", None, 5, True, [], {}, "d:\\"]
    scopes = []
    for k in keys:
        for shown in [None, "C:\\Docs", "", 5, [], "\u3000"]:
            scopes.append({"root_key": k, "root_path": shown})
            scopes.append({"path_key": k, "path": shown})
            scopes.append({"root_key": k, "path_key": k, "root_path": shown, "path": shown, "x": 1})
    scopes += [{"root_key": "c:\\x"}, {"path_key": "c:\\x"}, {}, None, "c:\\x", [], 5] + [_random_value(rng) for _ in range(120)]
    effects = [None, {}, 0, "", [], False, {"x": 1}, "c:\\x", 5]
    for ok_ in ["c:\\dl", " c:\\dl ", "", " ", None, 5, True, [], "\x1c"]:
        for over in [None, "Downloads", "", 5, [], True]:
            effects.append({"over_path_key": ok_, "over": over})
        effects.append({"over_path_key": ok_})
    effects += [{"over": "Downloads"}, {"over": 5}] + [_random_value(rng) for _ in range(40)]
    # Every scope with a valid effect, every effect with a valid scope, and the
    # two crossed on a sample -- the scope and effect gates are independent.
    pm, pn, pacc, psloppy = [], 0, 0, []
    rng2 = random.Random(29)
    for kind, spec in REG.POLICY_KINDS.items():
        good_scope = {"root_key": "c:\\docs"} if spec.scope_kind == REG.SOURCE_ROOT else {"path_key": "c:\\docs"}
        cases = [(sc, None) for sc in scopes] + [(good_scope, ef) for ef in effects]
        cases += [(rng2.choice(scopes), rng2.choice(effects)) for _ in range(300)]
        for sc, ef in cases:
            s_ok, _s, s_exc = _code_accepts(spec.validate_scope, sc)
            e_ok, _e, e_exc = _code_accepts(spec.validate_effect, ef)
            for exc in (s_exc, e_exc):
                if exc:
                    psloppy.append((kind, repr(sc)[:40], repr(ef)[:40], exc))
            code = s_ok and e_ok
            schema = root.is_valid(RS.policy_instance(kind, sc, ef) if ef is not None else RS.policy_instance(kind, sc))
            pn += 1
            pacc += code
            if code != schema:
                pm.append((kind, repr(sc)[:60], repr(ef)[:60], "code accepts, schema rejects" if code else "schema accepts, code refuses"))
    check(f"policies: {pn:,} cases ({len(REG.POLICY_KINDS)} kinds; {len(scopes)} scopes, {len(effects)} effects, 300 crossed; "
          f"{pacc:,} accepted) -- no mismatch either way", not pm, f"{len(pm)} mismatch(es), e.g. {pm[:3]}")
    check("every policy refusal is a ValueError", not psloppy, str(psloppy[:3]))
    check("a policy version's status is active or retired; an unknown policy kind is refused",
          root.is_valid(RS.policy_instance("protect_folder_subtree", {"path_key": "c:\\x"}, {}, "retired"))
          and not root.is_valid(RS.policy_instance("protect_folder_subtree", {"path_key": "c:\\x"}, {}, "paused"))
          and not root.is_valid(RS.policy_instance("delete_folder", {"path_key": "c:\\x"}, {})))

    # Semantic fields: a desired value's shape per field (checked with the question known).
    fm, fn, facc = [], 0, 0
    for field, spec in REG.METADATA_FIELDS.items():
        sub = _validator(f"semantic_field.{field}")
        for v in values:
            ok, _norm, exc = _code_accepts(lambda x: REG.validate_field_value(spec, x), v)
            schema = sub.is_valid(v)
            fn += 1
            facc += ok
            if ok != schema:
                fm.append((field, repr(v)[:80], "code accepts, schema rejects" if ok else "schema accepts, code refuses"))
    check(f"semantic fields: {fn:,} cases ({len(REG.METADATA_FIELDS)} fields x {len(values):,} values; {facc:,} accepted, "
          f"dates included: leap days, 24:00, 23:59:60, +05:60, non-ASCII digits) -- no mismatch either way",
          not fm, f"{len(fm)} mismatch(es), e.g. {fm[:3]}")


# -- 4. normalization ---------------------------------------------------------------------------

def test_normalization():
    section("4. Normalization: what the code stores is accepted again, unchanged")
    root = _validator()
    bad = []
    for kind, spec in REG.DECISION_KINDS.items():
        for v in _value_corpus(41):
            ok, norm, _exc = _code_accepts(spec.validate_value, v)
            if not ok:
                continue
            again_ok, again, _ = _code_accepts(spec.validate_value, norm)
            tk = sorted(spec.target_kinds)[0]
            if not (again_ok and again == norm and root.is_valid(RS.decision_instance(kind, tk, json.loads(json.dumps(norm))))):
                bad.append((kind, repr(v)[:60], repr(norm)[:60]))
    check("every accepted decision value, as stored (and through JSON, as value_json holds it), is accepted by the code "
          "and the schema, and normalizes to itself", not bad, str(bad[:3]))
    bad = []
    for kind, spec in REG.POLICY_KINDS.items():
        for sc in ({"root_key": " c:\\a ", "root_path": None}, {"path_key": " c:\\a ", "path": "C:\\A"}, {"root_key": "c:\\a"},
                   {"path_key": "c:\\a"}):
            for ef in (None, {}, {"over_path_key": " c:\\b ", "over": None}, {"over_path_key": ""}):
                s_ok, s, _ = _code_accepts(spec.validate_scope, sc)
                e_ok, e, _ = _code_accepts(spec.validate_effect, ef)
                if not (s_ok and e_ok):
                    continue
                if not (spec.validate_scope(s) == s and spec.validate_effect(e) == e
                        and root.is_valid(RS.policy_instance(kind, s, e, "active"))):
                    bad.append((kind, sc, ef))
    check("every accepted policy scope and effect, as stored, is accepted by both and normalizes to itself", not bad, str(bad[:3]))


# -- 5. the fixture lab ------------------------------------------------------------------------

def test_fixtures():
    section("5. The fixture lab: every decision and policy version the product's loader reads")
    import p3_fixture_check as FX
    root = _validator()
    fixtures = sorted(d for d in FX.FIXTURES.iterdir() if d.is_dir() and (d / "fixture.sqlite").is_file())
    bad, nd, npv = [], 0, 0
    for d in fixtures:
        ev, _oracle, _history = FX.load_fixture(d / "fixture.sqlite")
        for x in list(ev.decisions) + list(ev.metadata.decisions):
            nd += 1
            if not root.is_valid(RS.decision_instance(x.kind, x.target_kind, x.value, x.origin_kind)):
                bad.append((d.name, x.decision_id, x.kind, repr(x.value)[:40]))
        for p in ev.policies:
            npv += 1
            if not root.is_valid(RS.policy_instance(p.kind, p.scope, p.effect, "active" if p.active else "retired")):
                bad.append((d.name, p.policy_version_id, p.kind, repr(p.scope)[:40]))
    check(f"{len(fixtures)} fixtures: {nd} decisions and {npv} policy versions, every one valid", len(fixtures) == 16 and not bad,
          str(bad[:3]))


# -- 6. the suites, captured -------------------------------------------------------------------

SUITES = ("p3_regression.py", "p3_fixture_check.py", "p3_visual_check.py", "p3_visual_bulk_check.py", "p3_survey_check.py",
          "p3_refresh_check.py", "p3_plan_check.py", "p3_metadata_check.py", "p3_metadata_view_check.py",
          "p3_encryption_check.py", "p3_revalidation_check.py", "p3_closeout_check.py", "p3_fingerprint_check.py",
          "p3_relabel_check.py", "p3_dangling_check.py", "p3_organize_check.py")


_CAPTURED = []


def flush_capture(out_path):
    """Write the buffered rows (called after the suite, when no guard is active)."""
    with open(out_path, "a", encoding="utf-8") as fh:
        fh.writelines(_CAPTURED)
    del _CAPTURED[:]


def _install_capture(out_path):
    """Wrap the store's four insert points; append each recorded row, as the
    schema reads it, to out_path (one JSON object per line)."""
    from Phase3 import store as S
    cls = S.DecisionStore

    # (B20 task 0b, the owner's decision) The rows are BUFFERED and written by
    # flush_capture() once the suite has finished, outside every guard: a write
    # during a guarded section was recorded by p3_regression.WriteGuard in eight suites.
    def emit(obj):
        _CAPTURED.append(json.dumps(obj, sort_keys=True, default=str) + "\n")

    def decision(conn, decision_id):
        r = conn.execute("SELECT decision_kind, target_kind, value_json, origin_kind, evidence_binding_json FROM p3_decision "
                         "WHERE decision_id = ?", (int(decision_id),)).fetchone()
        if r is not None:
            binding = json.loads(r[4] or "{}")
            emit({"type": "decision", "instance": RS.decision_instance(r[0], r[1], json.loads(r[2]), r[3]),
                  "semantic_field": binding.get("semantic_field") if isinstance(binding, dict) else None})

    def policy(conn, version_id):
        r = conn.execute("SELECT p.kind, v.scope_json, v.effect_json, v.status FROM p3_policy_version v JOIN p3_policy p "
                         "ON p.policy_id = v.policy_id WHERE v.policy_version_id = ?", (int(version_id),)).fetchone()
        if r is not None:
            emit({"type": "policy", "instance": RS.policy_instance(r[0], json.loads(r[1]), json.loads(r[2] or "null"), r[3])})

    orig_row, orig_override = cls._decision_row, cls.record_override
    orig_create, orig_version = cls.create_policy, cls._new_version

    def _decision_row(self, *a, **k):
        out = orig_row(self, *a, **k)
        decision(self.conn, out[0])
        return out

    def record_override(self, *a, **k):
        out = orig_override(self, *a, **k)
        decision(self.conn, out["decision_id"])
        return out

    def create_policy(self, *a, **k):
        out = orig_create(self, *a, **k)
        policy(self.conn, out["policy_version_id"])
        return out

    def _new_version(self, *a, **k):
        out = orig_version(self, *a, **k)
        policy(self.conn, out["policy_version_id"])
        return out
    cls._decision_row, cls.record_override = _decision_row, record_override
    cls.create_policy, cls._new_version = create_policy, _new_version


def capture_run(suite, out_path):
    """Child mode: run one suite as __main__ with the capture hook installed."""
    _install_capture(out_path)
    sys.argv = [str(SCRIPTS / suite)]
    code = 0
    try:
        runpy.run_path(str(SCRIPTS / suite), run_name="__main__")
    except SystemExit as exc:
        code = int(exc.code or 0)
    finally:
        flush_capture(out_path)
    return code


def test_suites():
    section("6. The suites: every decision and policy version they record")
    import time
    root = _validator()
    fields = {f: _validator(f"semantic_field.{f}") for f in REG.METADATA_FIELDS}
    tmp = Path(tempfile.mkdtemp(prefix="p3schema_"))
    rows, codes = [], {}
    for suite in SUITES:
        if not (SCRIPTS / suite).is_file():
            codes[suite] = "missing"
            continue
        out = tmp / (suite + ".jsonl")
        t0 = time.perf_counter()
        p = subprocess.run([sys.executable, str(Path(__file__)), "--capture-run", suite, str(out)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(SCRIPTS.parent))
        codes[suite] = p.returncode
        got = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()] if out.is_file() else []
        rows += [(suite, r) for r in got]
        print(f"      {suite}: exit {p.returncode}, {len(got):,} recorded rows, {time.perf_counter() - t0:.0f} s")
    check("every Phase 3 suite ran to exit 0 under the capture hook", all(c == 0 for c in codes.values()),
          str({s: c for s, c in codes.items() if c != 0}))
    bad, desired = [], 0
    for suite, r in rows:
        inst = r["instance"]
        if not root.is_valid(inst):
            bad.append((suite, r["type"], repr(inst)[:90]))
        if r["type"] == "decision" and inst["decision_kind"] == "desired_source_metadata_set":
            desired += 1
            f = r.get("semantic_field")
            if f not in fields or not fields[f].is_valid(inst["value"]):
                bad.append((suite, "desired value vs its field", f, repr(inst["value"])[:60]))
    decisions = [r for _s, r in rows if r["type"] == "decision"]
    policies = [r for _s, r in rows if r["type"] == "policy"]
    check(f"{len(decisions):,} recorded decisions and {len(policies):,} policy versions, every one valid under the committed "
          f"schema ({desired:,} desired values also valid for their question's field)", not bad and decisions and policies,
          f"{len(bad)} invalid, e.g. {bad[:3]}")
    kinds = {r["instance"]["decision_kind"] for r in decisions}
    pkinds = {r["instance"]["policy_kind"] for r in policies}
    check("every decision kind and every policy kind was recorded by some suite (the capture covers the registry)",
          kinds == set(REG.DECISION_KINDS) and pkinds == set(REG.POLICY_KINDS),
          f"never recorded: {sorted(set(REG.DECISION_KINDS) - kinds) + sorted(set(REG.POLICY_KINDS) - pkinds)}")
    raw, committed = _committed()
    check("the committed file (not only the generated dict) validates the same rows",
          all(committed.is_valid(r["instance"]) for _s, r in rows))
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def test_capture_guarded(tmp=None):
    """(B20 task 0b) The capture hook must not write while a suite's WriteGuard is
    active: p3_plan_check (guarded sections) run under it exits 0 with its own
    write-guard checks green, and the rows reach the file."""
    section("5b. The capture hook buffers: a guarded suite stays green under it")
    import time
    d = Path(tmp) if tmp is not None else Path(tempfile.mkdtemp(prefix="p3schema_cap_"))
    out = d / "plan.jsonl"
    t0 = time.perf_counter()
    p = subprocess.run([sys.executable, str(Path(__file__)), "--capture-run", "p3_plan_check.py", str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(SCRIPTS.parent))
    rows = len(out.read_text(encoding="utf-8").splitlines()) if out.is_file() else 0
    check("p3_plan_check under the capture hook exits 0 (its 'no Python-level write outside the project' guard check is green)",
          p.returncode == 0, f"exit {p.returncode}: {p.stdout[-300:]}")
    check("...and the buffered rows were written after the suite", rows > 0, f"{rows} rows in {time.perf_counter() - t0:.0f} s")
    if tmp is None:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def main(argv):
    if argv[:1] == ["--capture-run"]:
        return capture_run(argv[1], argv[2])
    if argv[:1] == ["--write"]:
        print(f"wrote {RS.write()}")
        return 0
    test_committed()
    test_whitespace()
    test_cross_check()
    test_normalization()
    test_fixtures()
    test_capture_guarded()
    if "--quick" not in argv:
        test_suites()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 REGISTRY SCHEMA: {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
