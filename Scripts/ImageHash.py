#!/usr/bin/env python3
"""
ImageHash.py
Part of: The File Organizer
Version: 2.2.1

Purpose:
    Computes pHash, aHash, and dHash (perceptual hashes) for EVERY non-RAW
    image file in the inventory -- not just files flagged as potential
    duplicates. Perceptual hashing is meant to catch visually similar
    images (resized, recompressed, cropped, screenshotted) that would
    never share an exact file size or content hash, so it deliberately
    covers the whole image population, not just the exact-duplicate
    candidates from Scripts 2-4.

    Each image is opened and decoded ONCE, then all three hash algorithms
    run against that same in-memory image -- no repeated disk reads or
    re-decoding per hash type.

    - aHash (average hash)   : fastest, simplest, most sensitive to small
                                edits (crops, watermarks, minor color shifts)
    - pHash (perceptual hash): frequency-domain (DCT) analysis, more robust
                                to those small edits than aHash
    - dHash (difference hash): fast like aHash, generally more robust to
                                small changes while staying cheap to compute

    Having all three side by side supports a "2-of-3 agree" comparison
    strategy later, rather than trusting a single algorithm's blind spots.

    RAW camera formats (CR2, NEF, ARW, DNG, RAF, ORF, RW2, etc.) are
    explicitly excluded -- Pillow can't decode most of them without extra
    libraries, and they aren't meaningfully comparable via these
    perceptual-hash algorithms anyway.

Scaling features (v2.0.0):
    - CHECKPOINT/RESUME: results are written to a small checkpoint file as
      hashing proceeds. If interrupted and re-run with the same --output
      path, already-hashed files are skipped. The checkpoint is removed
      on a clean finish.
    - CLOUD-FILE SAFETY CHECK: if the input CSV has an IsOfflineOrCloud
      column (the run's inventory CSV does), warns before hashing any
      cloud-only file, since opening it forces a full download.

Usage:
    Recommended -- from the run's inventory CSV (covers every image file in
    the inventory, keeps DB_ID linkage):

        python ImageHash.py --csv "C:\\path\\to\\DuplicateHashInventory.csv" --output "C:\\path\\to\\ImageHashes.csv"

    Standalone -- scan a folder directly (no DB_ID linkage):

        python ImageHash.py --folder "C:\\path\\to\\images" --output "C:\\path\\to\\ImageHashes.csv"

    Optional flags:
        --report "C:\\path\\to\\ImageHashReport.txt"   write a summary report
        --force                                        hash cloud-only files, no prompt
        --skip-cloud-only                               always skip cloud-only files, no prompt
        --hash-size 8                                   hash size (default 8 -> 64-bit hash)

Requires:
    pip install Pillow imagehash
    (optional, for HEIC/HEIF support) pip install pillow-heif
"""

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from file_organizer_common import RAW_EXTENSIONS, to_long_path

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow is not installed. Run: pip install Pillow", file=sys.stderr)
    sys.exit(1)

try:
    import imagehash
except ImportError:
    print("ERROR: imagehash is not installed. Run: pip install imagehash", file=sys.stderr)
    sys.exit(1)

# Optional HEIC/HEIF support -- without this package, .heic/.heif files
# will simply fail to open and get logged as errors, everything else works.
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".jfif", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".webp", ".ico", ".heic", ".heif", ".avif", ".jp2",
}


def find_image_rows_from_csv(csv_path):
    """Read the run's inventory CSV (or any CSV with DB_ID/FileName/Path columns)
    and return every non-RAW image row -- regardless of duplicate status --
    plus a count of RAW files seen and skipped."""
    rows = []
    raw_count = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            path = row.get("Path", "")
            ext = Path(path).suffix.lower()
            if ext in RAW_EXTENSIONS:
                raw_count += 1
                continue
            if ext in IMAGE_EXTENSIONS:
                rows.append({
                    "DB_ID": row.get("DB_ID", ""),
                    "FileName": row.get("FileName", Path(path).name),
                    "Path": path,
                    "IsOfflineOrCloud": row.get("IsOfflineOrCloud", "False"),
                })
    return rows, raw_count


def find_image_rows_from_folder(folder_path):
    """Recursively scan a folder for non-RAW image files. No DB_ID
    available in this mode. Returns rows plus a count of RAW files
    seen and skipped."""
    rows = []
    raw_count = 0
    for p in Path(folder_path).rglob("*"):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in RAW_EXTENSIONS:
            raw_count += 1
            continue
        if ext in IMAGE_EXTENSIONS:
            rows.append({
                "DB_ID": "",
                "FileName": p.name,
                "Path": str(p),
                "IsOfflineOrCloud": "False",
            })
    return rows, raw_count


def get_key(row):
    """Resume/checkpoint key -- DB_ID when available (--csv mode), else
    the file path itself (--folder mode, where there's no DB_ID)."""
    return row["DB_ID"] if row["DB_ID"] else row["Path"]


def compute_hashes(path, hash_size=8):
    """Open the image once; compute all three perceptual hashes from that
    single decode."""
    with Image.open(to_long_path(path)) as img:
        width, height = img.size
        img_format = img.format

        p_hash = imagehash.phash(img, hash_size=hash_size)
        a_hash = imagehash.average_hash(img, hash_size=hash_size)
        d_hash = imagehash.dhash(img, hash_size=hash_size)

    return {
        "pHash": str(p_hash),
        "aHash": str(a_hash),
        "dHash": str(d_hash),
        "Width": width,
        "Height": height,
        "Format": img_format,
        "Error": "",
    }


def format_bytes(n):
    units = ["B", "KB", "MB", "GB", "TB"]
    n = float(n)
    for unit in units:
        if n < 1024 or unit == units[-1]:
            return f"{n:.2f} {unit}"
        n /= 1024


CHECKPOINT_FIELDS = ["Key", "pHash", "aHash", "dHash", "Width", "Height", "Format", "Error"]
OUTPUT_FIELDS = ["DB_ID", "FileName", "Path", "pHash", "aHash", "dHash", "Width", "Height", "Format", "Error"]


def load_checkpoint(checkpoint_path):
    done = {}
    if checkpoint_path.exists():
        with open(checkpoint_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                done[row["Key"]] = row
    return done


def append_checkpoint(checkpoint_path, buffer, file_exists):
    if not buffer:
        return file_exists
    mode = "a" if file_exists else "w"
    try:
        with open(checkpoint_path, mode, newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CHECKPOINT_FIELDS)
            if not file_exists:
                writer.writeheader()
            writer.writerows(buffer)
        # Only clear on a confirmed-successful write -- a failed write
        # (e.g. a transient file lock from cloud-sync software like
        # OneDrive) leaves the buffer intact, so these rows are retried
        # on the next flush instead of being silently lost.
        buffer.clear()
        return True
    except OSError as e:
        print(f"  WARNING: Checkpoint write failed, will retry: {e}")
        return file_exists


def main():
    parser = argparse.ArgumentParser(description="Compute pHash, aHash, and dHash for every non-RAW image file.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", help="Path to the run's inventory CSV (or any CSV with DB_ID, FileName, Path columns)")
    source.add_argument("--folder", help="Path to a folder to scan directly for images (no DB_ID linkage)")
    parser.add_argument("--output", required=True, help="Path to write the output CSV")
    parser.add_argument("--report", help="Optional path to write a summary report .txt")
    parser.add_argument("--hash-size", type=int, default=8, help="Hash size (default: 8, giving a 64-bit hash)")
    parser.add_argument("--force", action="store_true", help="Hash cloud-only files without prompting")
    parser.add_argument("--skip-cloud-only", action="store_true", help="Always skip cloud-only files, no prompt")
    args = parser.parse_args()

    if args.csv:
        rows, raw_count = find_image_rows_from_csv(args.csv)
    else:
        rows, raw_count = find_image_rows_from_folder(args.folder)

    total_found = len(rows)
    if total_found == 0:
        print(f"No non-RAW image files found. ({raw_count} RAW file(s) seen and skipped.)")
        return

    output_path = Path(args.output)
    checkpoint_path = output_path.with_suffix(".checkpoint.csv")
    pause_flag_path = output_path.parent.parent / "Logs" / "pause_requested.flag"

    # --- Resume from checkpoint, if one exists ---
    checkpoint_data = load_checkpoint(checkpoint_path)
    if checkpoint_data:
        print(f"Found an existing checkpoint -- resuming an interrupted run.")
        print(f"  {len(checkpoint_data)} files already hashed previously; skipping those.")
        print()

    rows_to_process = [r for r in rows if get_key(r) not in checkpoint_data]

    # --- Cloud-file safety check ---
    cloud_rows = [r for r in rows_to_process if r.get("IsOfflineOrCloud") == "True"]
    skipped_cloud_keys = set()

    if cloud_rows:
        print(f"WARNING: {len(cloud_rows)} of {len(rows_to_process)} files to hash are cloud-only")
        print("(not fully downloaded locally). Opening them forces a full download.")
        print()

        if args.skip_cloud_only:
            print("Skipping cloud-only files (--skip-cloud-only specified).")
            skipped_cloud_keys = {get_key(r) for r in cloud_rows}
            rows_to_process = [r for r in rows_to_process if get_key(r) not in skipped_cloud_keys]
        elif args.force:
            print("Proceeding to hash cloud-only files (--force specified).")
        else:
            answer = input("Continue and hash these cloud-only files now? [Y] Yes  [N] No, skip them (default is Y): ")
            if answer.strip().lower().startswith("n"):
                print("Skipping cloud-only files for this run.")
                skipped_cloud_keys = {get_key(r) for r in cloud_rows}
                rows_to_process = [r for r in rows_to_process if get_key(r) not in skipped_cloud_keys]
        print()

    total_to_process = len(rows_to_process)
    print(f"Hashing {total_to_process} file(s)...")

    buffer = []
    checkpoint_exists = checkpoint_path.exists()
    error_count = 0
    start_time = time.time()
    last_print_time = start_time
    FLUSH_BATCH_SIZE = 25
    FLUSH_INTERVAL_SECONDS = 3

    for i, row in enumerate(rows_to_process, start=1):
        key = get_key(row)
        try:
            hashes = compute_hashes(row["Path"], hash_size=args.hash_size)
            buffer.append({"Key": key, **hashes})
        except Exception as e:
            error_count += 1
            buffer.append({
                "Key": key, "pHash": "", "aHash": "", "dHash": "",
                "Width": "", "Height": "", "Format": "", "Error": str(e),
            })

        now = time.time()
        if len(buffer) >= FLUSH_BATCH_SIZE or (now - last_print_time) >= FLUSH_INTERVAL_SECONDS:
            checkpoint_exists = append_checkpoint(checkpoint_path, buffer, checkpoint_exists)

            if pause_flag_path.exists():
                try:
                    pause_flag_path.unlink()
                except OSError:
                    pass
                print("\nPaused by user request. Progress has been saved -- resume anytime.")
                sys.exit(2)

        if i % 50 == 0 or (now - last_print_time) >= 3 or i == total_to_process:
            elapsed = now - start_time
            print(f"  {i}/{total_to_process} processed ({elapsed:.1f}s elapsed)")
            last_print_time = now

    checkpoint_exists = append_checkpoint(checkpoint_path, buffer, checkpoint_exists)

    # --- Reload the full checkpoint (resumed + new) and build final output ---
    all_hashed = load_checkpoint(checkpoint_path)

    results = []
    for row in rows:
        key = get_key(row)
        if key in skipped_cloud_keys:
            results.append({**{k: row[k] for k in ("DB_ID", "FileName", "Path")},
                             "pHash": "", "aHash": "", "dHash": "",
                             "Width": "", "Height": "", "Format": "",
                             "Error": "SkippedCloudOnly"})
        elif key in all_hashed:
            h = all_hashed[key]
            results.append({
                "DB_ID": row["DB_ID"], "FileName": row["FileName"], "Path": row["Path"],
                "pHash": h["pHash"], "aHash": h["aHash"], "dHash": h["dHash"],
                "Width": h["Width"], "Height": h["Height"], "Format": h["Format"],
                "Error": h["Error"],
            })
        else:
            # Wasn't hashed and wasn't skipped -- shouldn't normally happen,
            # but preserved so no row silently disappears
            results.append({**{k: row[k] for k in ("DB_ID", "FileName", "Path")},
                             "pHash": "", "aHash": "", "dHash": "",
                             "Width": "", "Height": "", "Format": "",
                             "Error": "NotProcessed"})

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(results)

    # Clean finish -- remove the checkpoint
    try:
        if checkpoint_path.exists():
            checkpoint_path.unlink()
    except Exception:
        pass

    elapsed = time.time() - start_time
    succeeded = sum(1 for r in results if r["Error"] == "" and r["pHash"])
    skipped_cloud_final = sum(1 for r in results if r["Error"] == "SkippedCloudOnly")

    print()
    print(f"Done. {succeeded} succeeded, {error_count} failed this run, {skipped_cloud_final} skipped (cloud-only).")
    print(f"RAW files seen and intentionally skipped: {raw_count}")
    print(f"Output written to: {output_path}")
    print(f"Elapsed: {elapsed:.1f}s")

    # --- Optional report ---
    if args.report:
        report_lines = []
        report_lines.append("=" * 70)
        report_lines.append(" THE FILE ORGANIZER -- IMAGE HASH REPORT")
        report_lines.append(f" Generated : {time.strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append("=" * 70)
        report_lines.append("")
        report_lines.append("SUMMARY")
        report_lines.append(f"  Non-RAW image files found : {total_found}")
        report_lines.append(f"  RAW files skipped (by design): {raw_count}")
        report_lines.append(f"  Hashed this run           : {total_to_process}")
        report_lines.append(f"  Succeeded                 : {succeeded}")
        report_lines.append(f"  Errors                    : {error_count}")
        if skipped_cloud_final:
            report_lines.append(f"  Skipped (cloud-only)      : {skipped_cloud_final}")
        report_lines.append(f"  Processing time            : {elapsed:.1f}s")
        report_lines.append("")
        report_lines.append("NEXT STEP")
        report_lines.append("  ImageHashes.csv now has pHash/aHash/dHash for every non-RAW image.")
        report_lines.append("  A future comparison pass can use these to find visually similar")
        report_lines.append("  images that don't share an exact file size or content hash.")
        report_lines.append("=" * 70)

        with open(args.report, "w", encoding="utf-8") as f:
            f.write("\n".join(report_lines))
        print(f"Report written to: {args.report}")

    if error_count > 0:
        error_log = output_path.with_suffix(".errors.txt")
        with open(error_log, "w", encoding="utf-8") as f:
            for r in results:
                if r["Error"] and r["Error"] not in ("SkippedCloudOnly", "NotProcessed"):
                    f.write(f"{r['Path']} -- {r['Error']}\n")
        print(f"Errors logged to: {error_log}")


if __name__ == "__main__":
    main()
