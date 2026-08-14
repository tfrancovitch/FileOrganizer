"""
file_organizer_common.py
Part of: The File Organizer
Version: 1.2.1

Shared helpers used by the type-specific analysis scripts (PDF, Office,
RAW images, Audio, Video). Centralizing this here means every type-specific
script gets the same checkpoint/resume behavior and cloud-file safety
check without re-implementing it in each one.

Not meant to be run directly -- imported by the *Analysis.py scripts that
sit next to it.
"""

import csv
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Common camera RAW extensions -- shared with ImageHash.py (which excludes
# these) and RawImageAnalysis.py (which specifically targets these), so the
# list only needs to be maintained in one place.
RAW_EXTENSIONS = {
    ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".dng",
    ".raf", ".orf", ".rw2", ".pef", ".srw", ".x3f", ".3fr", ".erf",
    ".kdc", ".mrw", ".raw", ".rwl", ".iiq",
}


def to_long_path(path_str):
    """Prefix an absolute Windows path with \\\\?\\ (or \\\\?\\UNC\\ for
    network paths) to bypass the 260-character MAX_PATH limit -- the
    identical technique used on the PowerShell side (see Common.ps1's
    ConvertTo-LongPath), which was confirmed working there via direct
    testing.

    IMPORTANT CAVEAT: unlike the PowerShell fix, this specific Python
    application (across pypdf/pdfplumber/python-docx/openpyxl/
    python-pptx/Pillow/exifread) has NOT yet been confirmed working
    against real long-path files -- the mechanism is sound (Windows
    honors \\\\?\\ at the Win32 API level regardless of which higher-level
    library calls into it), but each library's own path handling hasn't
    been individually verified. Worth watching closely in the first real
    test run after this change.

    No-op on non-Windows. Only apply immediately before a file-open call,
    not for display/storage -- paths in CSVs and reports should stay in
    their normal, human-readable form.
    """
    if sys.platform != "win32":
        return path_str
    if path_str.startswith("\\\\?\\"):
        return path_str
    if path_str.startswith("\\\\"):  # UNC path: \\server\share\... -> \\?\UNC\server\share\...
        return "\\\\?\\UNC\\" + path_str[2:]
    return "\\\\?\\" + path_str


def is_ffprobe_available():
    return shutil.which("ffprobe") is not None


def run_ffprobe(path):
    """Run ffprobe on a media file and return the parsed JSON as a dict.
    Raises RuntimeError if ffprobe isn't installed or fails on this file."""
    if not is_ffprobe_available():
        raise RuntimeError("ffprobe not found on PATH")

    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()[:200]}")
    return json.loads(result.stdout)


def parse_frame_rate(rate_str):
    """ffprobe reports frame rate as a fraction string like '30000/1001' --
    convert that to a plain decimal fps value."""
    if not rate_str or rate_str == "0/0":
        return ""
    try:
        num, denom = rate_str.split("/")
        num, denom = float(num), float(denom)
        if denom == 0:
            return ""
        return f"{num / denom:.2f}"
    except Exception:
        return ""


def format_bytes(n):
    units = ["B", "KB", "MB", "GB", "TB"]
    n = float(n)
    for unit in units:
        if n < 1024 or unit == units[-1]:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} {units[-1]}"


def find_rows_from_csv(csv_path, extensions, exclude_extensions=None):
    """Read the run's inventory CSV and return rows whose extension is in
    `extensions`. Rows whose extension is in `exclude_extensions` are
    counted separately (not included) so callers can report a "seen but
    skipped" count instead of those files silently vanishing."""
    rows = []
    excluded_count = 0
    exclude_extensions = exclude_extensions or set()
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            path = row.get("Path", "")
            ext = Path(path).suffix.lower()
            if ext in exclude_extensions:
                excluded_count += 1
                continue
            if ext in extensions:
                rows.append({
                    "DB_ID": row.get("DB_ID", ""),
                    "FileName": row.get("FileName", Path(path).name),
                    "Path": path,
                    "Length": row.get("Length", "0"),
                    "IsOfflineOrCloud": row.get("IsOfflineOrCloud", "False"),
                })
    return rows, excluded_count


def get_key(row):
    """Resume/checkpoint key -- DB_ID when available, else the path."""
    return row["DB_ID"] if row.get("DB_ID") else row["Path"]


def load_checkpoint(checkpoint_path):
    done = {}
    if checkpoint_path.exists():
        with open(checkpoint_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                done[row["Key"]] = row
    return done


def append_checkpoint(checkpoint_path, buffer, file_exists, fieldnames):
    if not buffer:
        return file_exists
    mode = "a" if file_exists else "w"
    try:
        with open(checkpoint_path, mode, newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerows(buffer)
        # Only clear on a confirmed-successful write -- a failed write
        # (e.g. a transient file lock from cloud-sync software like
        # OneDrive, which this toolkit may itself be running from) leaves
        # the buffer intact, so these rows are retried on the next flush
        # instead of being silently lost or crashing the whole run.
        # Found via real testing on the PowerShell side (PartialHash.ps1
        # v2.2.0) -- applied here proactively for the same risk.
        buffer.clear()
        return True
    except OSError as e:
        print(f"  WARNING: Checkpoint write failed, will retry: {e}")
        return file_exists


def check_cloud_files(rows_to_process, force, skip_cloud_only):
    """Warn before processing cloud-only files (returns rows still to
    process, plus the ones skipped, if any)."""
    cloud_rows = [r for r in rows_to_process if r.get("IsOfflineOrCloud") == "True"]
    if not cloud_rows:
        return rows_to_process, []

    total_bytes = sum(int(r.get("Length", 0) or 0) for r in cloud_rows)
    print(f"WARNING: {len(cloud_rows)} of {len(rows_to_process)} files to process are cloud-only")
    print("(not fully downloaded locally). Opening them forces a full download.")
    print(f"Estimated total download if all are processed: {format_bytes(total_bytes)}")
    print()

    skip = False
    if skip_cloud_only:
        print("Skipping cloud-only files (--skip-cloud-only specified).")
        skip = True
    elif force:
        print("Proceeding to process cloud-only files (--force specified).")
    else:
        answer = input("Continue and process these cloud-only files now? [Y] Yes  [N] No, skip them (default is Y): ")
        skip = answer.strip().lower().startswith("n")
        if skip:
            print("Skipping cloud-only files for this run.")

    if skip:
        skip_keys = {get_key(r) for r in cloud_rows}
        remaining = [r for r in rows_to_process if get_key(r) not in skip_keys]
        return remaining, cloud_rows
    return rows_to_process, []


def run_analysis(csv_path, output_path, report_path, extensions, checkpoint_fields,
                  analyze_fn, force=False, skip_cloud_only=False,
                  exclude_extensions=None, exclude_label="excluded",
                  report_title="ANALYSIS REPORT", extra_report_lines_fn=None):
    """
    Generic orchestrator shared by every type-specific analysis script:
    load candidate rows from the run's inventory CSV, resume from checkpoint,
    run the cloud-file safety check, call `analyze_fn(path) -> dict` for
    each remaining file (checkpointing as it goes), then write the final
    output CSV and an optional summary report.

    analyze_fn should return a dict keyed by checkpoint_fields (minus
    "Key" and "Error") on success; exceptions are caught automatically
    and recorded in the Error column.
    """
    output_path = Path(output_path)
    checkpoint_path = output_path.with_suffix(".checkpoint.csv")
    # Same Logs\ folder the PowerShell hash scripts use for their pause
    # flag -- derived from output_path (which lives in Inventory\) rather
    # than checkpoint_path's own folder, so every script (regardless of
    # language) checks the exact same file location.
    pause_flag_path = output_path.parent.parent / "Logs" / "pause_requested.flag"

    rows, excluded_count = find_rows_from_csv(csv_path, extensions, exclude_extensions)
    total_found = len(rows)
    if total_found == 0:
        if excluded_count:
            print(f"No matching files found. ({excluded_count} {exclude_label} seen and skipped.)")
        else:
            print("No matching files found.")
        return

    checkpoint_data = load_checkpoint(checkpoint_path)
    if checkpoint_data:
        print(f"Resuming -- {len(checkpoint_data)} files already processed previously.")

    rows_to_process = [r for r in rows if get_key(r) not in checkpoint_data]
    rows_to_process, skipped_cloud = check_cloud_files(rows_to_process, force, skip_cloud_only)
    skipped_keys = {get_key(r) for r in skipped_cloud}

    total_to_process = len(rows_to_process)
    print(f"Processing {total_to_process} file(s)...")

    buffer = []
    checkpoint_exists = checkpoint_path.exists()
    error_count = 0
    start_time = time.time()
    last_print = start_time

    empty_result = {k: "" for k in checkpoint_fields if k not in ("Key", "Error")}

    for i, row in enumerate(rows_to_process, start=1):
        key = get_key(row)
        try:
            result = analyze_fn(row["Path"])
            result.setdefault("Error", "")
            buffer.append({"Key": key, **result})
        except Exception as e:
            error_count += 1
            buffer.append({"Key": key, **empty_result, "Error": str(e)})

        now = time.time()
        if len(buffer) >= 25 or (now - last_print) >= 3:
            checkpoint_exists = append_checkpoint(checkpoint_path, buffer, checkpoint_exists, checkpoint_fields)

            # Cooperative pause: only checked right after a confirmed-clean
            # checkpoint flush, never mid-write. Same convention as the
            # PowerShell hash scripts -- exit code 2 means "paused", not
            # an error, and the flag is removed on our own exit so it
            # can't accidentally re-trigger on the very next run.
            if pause_flag_path.exists():
                try:
                    pause_flag_path.unlink()
                except OSError:
                    pass
                print("\nPaused by user request. Progress has been saved -- resume anytime.")
                sys.exit(2)
        if i % 50 == 0 or (now - last_print) >= 3 or i == total_to_process:
            print(f"  {i}/{total_to_process} processed ({now - start_time:.1f}s elapsed)")
            last_print = now

    checkpoint_exists = append_checkpoint(checkpoint_path, buffer, checkpoint_exists, checkpoint_fields)
    all_done = load_checkpoint(checkpoint_path)

    output_fields = ["DB_ID", "FileName", "Path"] + [k for k in checkpoint_fields if k != "Key"]
    results = []
    for row in rows:
        key = get_key(row)
        base = {"DB_ID": row["DB_ID"], "FileName": row["FileName"], "Path": row["Path"]}
        if key in skipped_keys:
            results.append({**base, **empty_result, "Error": "SkippedCloudOnly"})
        elif key in all_done:
            d = all_done[key]
            results.append({**base, **{k: d.get(k, "") for k in checkpoint_fields if k != "Key"}})
        else:
            results.append({**base, **empty_result, "Error": "NotProcessed"})

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_fields)
        writer.writeheader()
        writer.writerows(results)

    try:
        if checkpoint_path.exists():
            checkpoint_path.unlink()
    except Exception:
        pass

    elapsed = time.time() - start_time
    succeeded = sum(1 for r in results if not r["Error"])
    skipped_cloud_final = sum(1 for r in results if r["Error"] == "SkippedCloudOnly")

    print()
    print(f"Done. {succeeded} succeeded, {error_count} failed this run, {skipped_cloud_final} skipped (cloud-only).")
    if excluded_count:
        print(f"{exclude_label.capitalize()} seen and skipped: {excluded_count}")
    print(f"Output: {output_path}")
    print(f"Elapsed: {elapsed:.1f}s")

    if report_path:
        lines = [
            "=" * 70,
            f" THE FILE ORGANIZER -- {report_title}",
            f" Generated : {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 70, "",
            "SUMMARY",
            f"  Files found                : {total_found}",
            f"  Processed this run         : {total_to_process}",
            f"  Succeeded                  : {succeeded}",
            f"  Errors                     : {error_count}",
        ]
        if skipped_cloud_final:
            lines.append(f"  Skipped (cloud-only)       : {skipped_cloud_final}")
        if excluded_count:
            lines.append(f"  {exclude_label.capitalize()} seen and skipped : {excluded_count}")
        lines.append(f"  Processing time            : {elapsed:.1f}s")

        if extra_report_lines_fn:
            lines.append("")
            lines.extend(extra_report_lines_fn(results))

        lines.append("=" * 70)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"Report: {report_path}")

    if error_count > 0:
        error_log = output_path.with_suffix(".errors.txt")
        with open(error_log, "w", encoding="utf-8") as f:
            for r in results:
                if r["Error"] and r["Error"] not in ("SkippedCloudOnly", "NotProcessed"):
                    f.write(f"{r['Path']} -- {r['Error']}\n")
        print(f"Errors logged to: {error_log}")
