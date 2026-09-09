#!/usr/bin/env python3
r"""Build a large corpus for Phase 2 scale testing, with known-answer ground truth.

The P2.5 benchmark and the P2.10 million-row validation were measured against
synthetically injected rows -- the P2.10 result records build_seconds: 9.78 for
a million "files", which is row insertion, not a pipeline run. This builds real
files on real NTFS and puts them through the real Phase 1 pipeline, so report
timings reflect the schema, the indexes and the disk as a user would meet them.

Files are small by design: the goal is row count, not bytes. Duplicates are
planted from a fixed pool so group count and reclaimable bytes stay exactly
known at any N, and every unique file embeds its own index so accidental
content collisions cannot inflate the duplicate population.

Usage:
    python Scripts/p2_scale_corpus.py --root C:\FOTest\P2Scale --files 100000
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 20260909
NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)

# Weighted so facet reports have a realistic long tail rather than a flat split.
EXTENSIONS = [(".txt", 40), (".dat", 18), (".log", 14), (".json", 10),
              (".csv", 6), (".md", 5), (".xml", 4), (".bin", 3)]

DUP_POOL = 200          # distinct duplicated contents -> exactly this many groups
DUP_COPIES = 5          # copies of each -> DUP_COPIES-1 reclaimable per group
YEARS_SPREAD = 12


def weighted_extensions(rng, n):
    pool = [ext for ext, weight in EXTENSIONS for _ in range(weight)]
    return [rng.choice(pool) for _ in range(n)]


def build(root: Path, n_files: int) -> dict:
    rng = random.Random(SEED)
    corpus = root / "Corpus"
    if corpus.exists():
        print(f"  removing existing {corpus} ...")
        shutil.rmtree(corpus)
    corpus.mkdir(parents=True)

    # Fixed duplicate pool: content is deterministic and provably distinct.
    dup_payloads = [
        (f"DUPLICATE POOL ENTRY {i}\n".encode() + bytes(rng.getrandbits(8) for _ in range(512)))
        for i in range(DUP_POOL)
    ]
    dup_digests = [hashlib.sha256(p).hexdigest() for p in dup_payloads]
    n_dup_files = DUP_POOL * DUP_COPIES
    if n_dup_files > n_files:
        raise SystemExit(f"--files must be at least {n_dup_files}")

    by_ext: dict[str, dict] = {}
    total_bytes = 0
    older_than_5y = 0
    exts = weighted_extensions(rng, n_files)

    # A realistic tree: departments -> years -> batches, with roughly
    # FILES_PER_FOLDER files in each leaf. Fan-out matters more than depth here:
    # a corpus with one file per folder makes folder aggregation look trivially
    # cheap and is not what the recursive folder reports have to handle.
    files_per_folder = 25

    def folder_for(i: int) -> Path:
        f = i // files_per_folder
        return (corpus / f"dept_{f % 40:02d}"
                / f"year_{2014 + (f // 40) % YEARS_SPREAD}"
                / f"batch_{f // (40 * YEARS_SPREAD):03d}")

    t0 = time.perf_counter()
    dup_slots = set(rng.sample(range(n_files), n_dup_files))
    dup_iter = iter(sorted(dup_slots))
    dup_assignment = {slot: idx // DUP_COPIES for idx, slot in enumerate(dup_iter)}

    made_dirs: set[Path] = set()
    for i in range(n_files):
        folder = folder_for(i)
        if folder not in made_dirs:
            folder.mkdir(parents=True, exist_ok=True)
            made_dirs.add(folder)

        if i in dup_assignment:
            payload = dup_payloads[dup_assignment[i]]
            ext = ".txt"
            name = f"shared_{dup_assignment[i]:04d}_copy{i:07d}{ext}"
        else:
            ext = exts[i]
            # Embedding the index guarantees uniqueness, so the only duplicate
            # groups in this corpus are the ones planted above.
            body = f"record {i} unique-token-{i:08d}\n".encode()
            body += bytes(rng.getrandbits(8) for _ in range(rng.randint(120, 3000)))
            payload = body
            name = f"file_{i:07d}{ext}"

        path = folder / name
        path.write_bytes(payload)

        age_days = rng.randint(1, YEARS_SPREAD * 365)
        stamp = (NOW - timedelta(days=age_days)).timestamp()
        os.utime(path, (stamp, stamp))

        size = len(payload)
        total_bytes += size
        slot = by_ext.setdefault(ext, {"count": 0, "bytes": 0})
        slot["count"] += 1
        slot["bytes"] += size
        if age_days > 5 * 365:
            older_than_5y += 1

        if (i + 1) % 20000 == 0:
            print(f"  {i + 1:,} / {n_files:,} files  ({time.perf_counter() - t0:.0f}s)")

    elapsed = time.perf_counter() - t0
    reclaimable = sum(len(p) * (DUP_COPIES - 1) for p in dup_payloads)

    return {
        "generator": "p2_scale_corpus.py",
        "seed": SEED,
        "generated_utc": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "build_seconds": round(elapsed, 1),
        "totals": {
            "file_count": n_files,
            "logical_bytes": total_bytes,
            "distinct_extensions": len(by_ext),
            "folder_count": len(made_dirs),
        },
        "by_extension": dict(sorted(by_ext.items())),
        "duplicates": {
            "group_count": DUP_POOL,
            "copies_per_group": DUP_COPIES,
            "member_count": n_dup_files,
            "total_reclaimable_bytes": reclaimable,
            "pool_digests": dup_digests[:5],
        },
        "age": {
            "cutoff_utc": (NOW - timedelta(days=5 * 365)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "modified_before_cutoff": older_than_5y,
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=r"C:\FOTest\P2Scale")
    ap.add_argument("--files", type=int, default=100000)
    args = ap.parse_args()

    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    truth = build(root, args.files)
    (root / "GROUND_TRUTH.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")

    t, d = truth["totals"], truth["duplicates"]
    print(f"\ncorpus            : {root / 'Corpus'}")
    print(f"files             : {t['file_count']:,}")
    print(f"logical bytes     : {t['logical_bytes']:,}  ({t['logical_bytes'] / 2**30:.2f} GiB)")
    print(f"folders           : {t['folder_count']:,}")
    print(f"extensions        : {t['distinct_extensions']}")
    print(f"duplicate groups  : {d['group_count']} x {d['copies_per_group']} copies "
          f"= {d['member_count']:,} members")
    print(f"reclaimable bytes : {d['total_reclaimable_bytes']:,}")
    print(f"older than 5y     : {truth['age']['modified_before_cutoff']:,}")
    print(f"build seconds     : {truth['build_seconds']}")


if __name__ == "__main__":
    main()
