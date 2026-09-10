#!/usr/bin/env python3
r"""Candidate-only physical-identity narrowing: correctness and cost.

The narrowing (P1-RESULTS.md section 6) skips the per-file `os.stat` that
resolves physical identity for files whose size is unique in the corpus. A
hardlink is two names for one file, so both names always report the same size;
a uniquely-sized file therefore cannot be one of a hardlinked pair inside the
scanned corpus, and statting it cannot change an answer.

The invariants that must hold, and that this asserts:

  1. Every hardlink the unnarrowed walk finds, the narrowed walk still finds.
  2. No file whose size collides with another loses identity.
  3. Identity is only ever lost on uniquely-sized files.
  4. The narrowed walk yields exactly the same set of files, sizes and paths.

It builds its own corpus with planted hardlinks and copies, so it needs no
external fixture. Run:  python Scripts/p1_identity_narrowing_check.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

FAILURES: list[str] = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + ("" if condition or not detail else f" -- {detail}"))
    if not condition:
        FAILURES.append(name)


def build_corpus(root: Path):
    """Planted hardlinks, real copies, unique sizes and colliding sizes."""
    root.mkdir(parents=True, exist_ok=True)
    planted_links = 0

    # Colliding sizes that are NOT hardlinks: real independent copies.
    for i in range(10):
        body = (f"copy-group-{i}-" + "x" * (200 + i)).encode()
        for side in ("a", "b"):
            (root / f"copy_{i}_{side}.txt").write_bytes(body)

    # Uniquely sized files: nothing else shares their length.
    for i in range(30):
        (root / f"unique_{i:03d}.txt").write_bytes(b"u" * (5000 + i * 37))

    # Real hardlinks. Windows needs mklink /H; fall back to os.link elsewhere.
    for i in range(6):
        target = root / f"linked_{i}_orig.bin"
        target.write_bytes(b"L" * (9000 + i * 11))
        alias = root / f"linked_{i}_alias.bin"
        try:
            if sys.platform == "win32":
                subprocess.run(["cmd", "/c", "mklink", "/H", str(alias), str(target)],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.link(target, alias)
            planted_links += 1
        except Exception:
            pass
    return planted_links


def main():
    import fo_scan

    tmp = Path(tempfile.mkdtemp(prefix="p1_narrowing_"))
    corpus = tmp / "Corpus"
    try:
        planted = build_corpus(corpus)
        print(f"corpus: {corpus}  ({planted} hardlink pair(s) planted)")
        if planted == 0:
            print("  NOTE: no hardlink could be created here; hardlink invariants "
                  "are vacuous in this environment.")

        full = list(fo_scan.scan(str(corpus), 1, None))
        filt = fo_scan.collision_sizes(str(corpus))
        narrow = list(fo_scan.scan(str(corpus), 1, None, identity_size_filter=filt))

        print(f"\nfiles: {len(full)}   colliding-size groups: {len(filt)}")
        resolved_full = sum(1 for r in full if r.file_index is not None)
        resolved_narrow = sum(1 for r in narrow if r.file_index is not None)
        saved = resolved_full - resolved_narrow
        print(f"per-file stats: {resolved_full} unnarrowed -> {resolved_narrow} narrowed "
              f"({saved} avoided, {100 * saved / max(1, resolved_full):.0f}%)\n")

        # 4. same corpus, same files
        check("narrowed walk yields the same file set",
              {r.path for r in full} == {r.path for r in narrow})
        check("narrowed walk yields the same sizes",
              {(r.path, r.size_bytes) for r in full} == {(r.path, r.size_bytes) for r in narrow})

        # 1. no hardlink lost
        links_full = {r.path for r in full if (r.hard_link_count or 0) > 1}
        links_narrow = {r.path for r in narrow if (r.hard_link_count or 0) > 1}
        check(f"every hardlink survives narrowing ({len(links_full)} found)",
              links_full == links_narrow,
              f"lost: {sorted(links_full - links_narrow)[:3]}")
        if planted:
            check("planted hardlinks were actually detected", len(links_full) >= planted,
                  f"expected >= {planted}, found {len(links_full)}")

        # 2 + 3. identity lost only where size is unique
        sizes: dict[int, int] = {}
        for r in full:
            sizes[r.size_bytes] = sizes.get(r.size_bytes, 0) + 1
        lost_colliding = [r.path for r in narrow
                          if r.file_index is None and sizes.get(r.size_bytes, 0) > 1]
        check("no colliding-size file loses identity", not lost_colliding,
              f"{len(lost_colliding)} did")
        gained = [r.path for r in narrow
                  if r.file_index is not None and sizes.get(r.size_bytes, 0) == 1]
        check("no uniquely-sized file is stat'd unnecessarily", not gained,
              f"{len(gained)} were")

        # The whole point: fewer stats.
        check("narrowing actually avoids work", saved > 0,
              "no stats avoided; the filter is not being applied")

        # Default must be unchanged.
        check("default (no filter) still resolves identity for every file",
              resolved_full == len(full), f"{resolved_full}/{len(full)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"FAILED -- {len(FAILURES)} check(s): " + ", ".join(FAILURES))
        return 1
    print("ALL NARROWING CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
