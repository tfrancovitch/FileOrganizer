#!/usr/bin/env python3
"""
ArchiveAnalysis.py
Part of: The File Organizer
Version: 1.3.1

Catalogs the CONTENTS of every .zip/.7z archive in the run's inventory CSV --
purely an inventory of what's inside each archive. Comparing those
contents against files already inventoried elsewhere (to catch, say, a
zipped copy of something that also exists unzipped) is a Phase 2
comparison step and is NOT done here.

Produces two related CSVs, following the "each CSV is a table" pattern
used throughout this project:
  - ArchiveInventory.csv : one row per archive (DB_ID-keyed), with
    aggregate stats -- entry count, total size, compression ratio.
  - ArchiveContents.csv  : one row per file INSIDE each archive, linked
    back to its parent archive via ArchiveDB_ID.

This script has its own processing loop rather than using the shared
run_analysis() orchestrator, since its output shape (two files, a
variable number of child rows per archive) doesn't fit the one-row-per-
file pattern every other *Analysis.py script uses. It still reuses the
same low-level checkpoint and cloud-safety helpers.

Requires:
    .zip is handled by the standard library (no extra package needed)
    .7z requires: pip install py7zr (optional -- .zip works without it;
        if py7zr is missing, any .7z files encountered are logged as
        individual errors rather than blocking the whole run)

Usage:
    python ArchiveAnalysis.py --csv DuplicateHashInventory.csv --output ArchiveInventory.csv --contents-output ArchiveContents.csv --report ArchiveReport.txt
"""

import argparse
import csv
import shutil
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from file_organizer_common import (
    find_rows_from_csv, get_key, load_checkpoint, append_checkpoint,
    check_cloud_files, format_bytes, to_long_path,
)

try:
    import py7zr
    HAS_PY7ZR = True
except ImportError:
    HAS_PY7ZR = False

EXTENSIONS = {".zip", ".7z"}
AGG_CHECKPOINT_FIELDS = ["Key", "EntryCount", "TotalUncompressedSize",
                          "TotalCompressedSize", "CompressionRatioPercent", "Error"]
CONTENTS_FIELDS = ["ArchiveDB_ID", "ArchivePath", "EntryPath", "EntrySize", "EntryCompressedSize"]


def list_zip_entries(path):
    entries = []
    with zipfile.ZipFile(to_long_path(path)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            entries.append({
                "EntryPath": info.filename,
                "EntrySize": info.file_size,
                "EntryCompressedSize": info.compress_size,
            })
    return entries


def list_7z_entries(path):
    if not HAS_PY7ZR:
        raise RuntimeError("py7zr not installed -- run: pip install py7zr")
    entries = []
    with py7zr.SevenZipFile(to_long_path(path), mode="r") as z:
        for info in z.list():
            if info.is_directory:
                continue
            entries.append({
                "EntryPath": info.filename,
                "EntrySize": info.uncompressed or 0,
                "EntryCompressedSize": info.compressed or 0,
            })
    return entries


def analyze_archive(path):
    ext = Path(path).suffix.lower()
    if ext == ".zip":
        entries = list_zip_entries(path)
    elif ext == ".7z":
        entries = list_7z_entries(path)
    else:
        raise ValueError(f"Unsupported archive type: {ext}")

    total_uncompressed = sum(e["EntrySize"] for e in entries)
    total_compressed = sum(e["EntryCompressedSize"] for e in entries)
    ratio = (1 - (total_compressed / total_uncompressed)) * 100 if total_uncompressed > 0 else 0

    agg = {
        "EntryCount": str(len(entries)),
        "TotalUncompressedSize": str(total_uncompressed),
        "TotalCompressedSize": str(total_compressed),
        "CompressionRatioPercent": f"{ratio:.1f}",
    }
    return agg, entries


def main():
    parser = argparse.ArgumentParser(description="Catalog the contents of every .zip/.7z archive in the inventory.")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--output", required=True, help="Path for ArchiveInventory.csv (aggregate stats)")
    parser.add_argument("--contents-output", required=True, help="Path for ArchiveContents.csv (per-entry detail)")
    parser.add_argument("--report")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-cloud-only", action="store_true")
    args = parser.parse_args()

    rows, _ = find_rows_from_csv(args.csv, EXTENSIONS)
    total_found = len(rows)
    if total_found == 0:
        print("No archive files (.zip/.7z) found.")
        return

    output_path = Path(args.output)
    contents_path = Path(args.contents_output)
    checkpoint_path = output_path.with_suffix(".checkpoint.csv")
    contents_checkpoint_path = contents_path.with_suffix(".checkpoint.csv")
    pause_flag_path = output_path.parent.parent / "Logs" / "pause_requested.flag"

    checkpoint_data = load_checkpoint(checkpoint_path)
    if checkpoint_data:
        print(f"Resuming -- {len(checkpoint_data)} archives already processed previously.")

    rows_to_process = [r for r in rows if get_key(r) not in checkpoint_data]
    rows_to_process, skipped_cloud = check_cloud_files(rows_to_process, args.force, args.skip_cloud_only)
    skipped_keys = {get_key(r) for r in skipped_cloud}

    total_to_process = len(rows_to_process)
    print(f"Cataloging {total_to_process} archive(s)...")

    agg_buffer = []
    contents_buffer = []
    agg_checkpoint_exists = checkpoint_path.exists()
    contents_checkpoint_exists = contents_checkpoint_path.exists()
    error_count = 0
    start_time = time.time()

    for i, row in enumerate(rows_to_process, start=1):
        key = get_key(row)
        try:
            agg, entries = analyze_archive(row["Path"])
            agg_buffer.append({"Key": key, **agg, "Error": ""})
            for e in entries:
                contents_buffer.append({
                    "ArchiveDB_ID": row["DB_ID"], "ArchivePath": row["Path"],
                    "EntryPath": e["EntryPath"], "EntrySize": e["EntrySize"],
                    "EntryCompressedSize": e["EntryCompressedSize"],
                })
        except Exception as e:
            error_count += 1
            agg_buffer.append({"Key": key, "EntryCount": "", "TotalUncompressedSize": "",
                                "TotalCompressedSize": "", "CompressionRatioPercent": "", "Error": str(e)})

        # Flush after every archive (not batched) -- keeps the aggregate
        # and contents checkpoints in sync at archive boundaries, which is
        # what makes resuming safe here.
        agg_checkpoint_exists = append_checkpoint(checkpoint_path, agg_buffer, agg_checkpoint_exists, AGG_CHECKPOINT_FIELDS)
        if contents_buffer:
            mode = "a" if contents_checkpoint_exists else "w"
            try:
                with open(contents_checkpoint_path, mode, newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=CONTENTS_FIELDS)
                    if not contents_checkpoint_exists:
                        writer.writeheader()
                    writer.writerows(contents_buffer)
                # Only clear/mark-exists on a confirmed-successful write --
                # see file_organizer_common.py's append_checkpoint for why
                # (a real OneDrive lock collision found during testing).
                contents_checkpoint_exists = True
                contents_buffer.clear()
            except OSError as e:
                print(f"  WARNING: Contents checkpoint write failed, will retry: {e}")

        # Checked only after BOTH checkpoints are confirmed flushed
        # together -- this is what makes resuming safe here, so pausing
        # can't happen at a point where they'd be out of sync.
        if pause_flag_path.exists():
            try:
                pause_flag_path.unlink()
            except OSError:
                pass
            print("\nPaused by user request. Progress has been saved -- resume anytime.")
            sys.exit(2)

        print(f"  {i}/{total_to_process} processed ({time.time() - start_time:.1f}s elapsed)")

    all_done = load_checkpoint(checkpoint_path)

    output_fields = ["DB_ID", "FileName", "Path"] + [k for k in AGG_CHECKPOINT_FIELDS if k != "Key"]
    results = []
    for row in rows:
        key = get_key(row)
        base = {"DB_ID": row["DB_ID"], "FileName": row["FileName"], "Path": row["Path"]}
        if key in skipped_keys:
            results.append({**base, "EntryCount": "", "TotalUncompressedSize": "",
                             "TotalCompressedSize": "", "CompressionRatioPercent": "", "Error": "SkippedCloudOnly"})
        elif key in all_done:
            d = all_done[key]
            results.append({**base, **{k: d.get(k, "") for k in AGG_CHECKPOINT_FIELDS if k != "Key"}})
        else:
            results.append({**base, "EntryCount": "", "TotalUncompressedSize": "",
                             "TotalCompressedSize": "", "CompressionRatioPercent": "", "Error": "NotProcessed"})

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_fields)
        writer.writeheader()
        writer.writerows(results)

    if contents_checkpoint_path.exists():
        shutil.copy(contents_checkpoint_path, contents_path)
    else:
        with open(contents_path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CONTENTS_FIELDS).writeheader()

    for p in (checkpoint_path, contents_checkpoint_path):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass

    elapsed = time.time() - start_time
    succeeded = sum(1 for r in results if not r["Error"])
    total_entries = sum(int(r["EntryCount"]) for r in results if r.get("EntryCount"))

    print()
    print(f"Done. {succeeded} succeeded, {error_count} failed. Total entries cataloged: {total_entries}")
    print(f"Output: {output_path}")
    print(f"Contents: {contents_path}")
    print(f"Elapsed: {elapsed:.1f}s")

    if args.report:
        total_uncompressed = sum(int(r["TotalUncompressedSize"]) for r in results if r.get("TotalUncompressedSize"))
        total_compressed = sum(int(r["TotalCompressedSize"]) for r in results if r.get("TotalCompressedSize"))
        overall_savings_pct = (1 - (total_compressed / total_uncompressed)) * 100 if total_uncompressed > 0 else 0
        lines = [
            "=" * 70,
            " THE FILE ORGANIZER -- ARCHIVE ANALYSIS REPORT",
            f" Generated : {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 70, "",
            "SUMMARY",
            f"  Archives found              : {total_found}",
            f"  Processed this run          : {total_to_process}",
            f"  Succeeded                   : {succeeded}",
            f"  Errors                      : {error_count}",
            f"  Total entries cataloged     : {total_entries}",
            f"  Total compressed size       : {format_bytes(total_compressed)}",
            f"  Total uncompressed content  : {format_bytes(total_uncompressed)}",
            f"  Overall space saved         : {format_bytes(total_uncompressed - total_compressed)} ({overall_savings_pct:.1f}%)",
            f"  Processing time             : {elapsed:.1f}s",
            "",
            "NOTE",
            "  This is a catalog only -- comparing archive contents against",
            "  files already inventoried elsewhere (e.g. a zipped copy of",
            "  something that also exists unzipped) is a Phase 2 step.",
            "=" * 70,
        ]
        with open(args.report, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"Report: {args.report}")

    if error_count > 0:
        error_log = output_path.with_suffix(".errors.txt")
        with open(error_log, "w", encoding="utf-8") as f:
            for r in results:
                if r["Error"] and r["Error"] not in ("SkippedCloudOnly", "NotProcessed"):
                    f.write(f"{r['Path']} -- {r['Error']}\n")
        print(f"Errors logged to: {error_log}")


if __name__ == "__main__":
    main()
