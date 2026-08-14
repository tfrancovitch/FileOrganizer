#!/usr/bin/env python3
"""
RawImageAnalysis.py
Part of: The File Organizer
Version: 1.1.1

Extracts EXIF metadata from camera RAW files in the run's inventory CSV
(CR2, NEF, ARW, DNG, RAF, ORF, RW2, and others -- see RAW_EXTENSIONS in
file_organizer_common.py). These are deliberately excluded from
ImageHash.py's perceptual hashing, since Pillow can't decode most RAW
formats without extra libraries, and full RAW decoding is expensive.

This script instead reads the EXIF header directly (via exifread) --
much cheaper than decoding the actual image data, and gives useful
identifying information: camera make/model, exposure settings, and
capture date.

Requires:
    pip install exifread

Usage:
    python RawImageAnalysis.py --csv DuplicateHashInventory.csv --output RawImageInventory.csv --report RawImageReport.txt
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from file_organizer_common import run_analysis, RAW_EXTENSIONS, to_long_path

try:
    import exifread
except ImportError:
    print("ERROR: exifread is not installed. Run: pip install exifread", file=sys.stderr)
    sys.exit(1)

CHECKPOINT_FIELDS = ["Key", "CameraMake", "CameraModel", "ExposureTime",
                      "FNumber", "ISO", "FocalLength", "DateTimeOriginal", "Error"]


def analyze_raw(path):
    with open(to_long_path(path), "rb") as f:
        tags = exifread.process_file(f, details=False)

    def tag(name):
        return str(tags[name]) if name in tags else ""

    return {
        "CameraMake": tag("Image Make"),
        "CameraModel": tag("Image Model"),
        "ExposureTime": tag("EXIF ExposureTime"),
        "FNumber": tag("EXIF FNumber"),
        "ISO": tag("EXIF ISOSpeedRatings"),
        "FocalLength": tag("EXIF FocalLength"),
        "DateTimeOriginal": tag("EXIF DateTimeOriginal"),
    }


def report_extra(results):
    by_camera = {}
    for r in results:
        cam = f"{r.get('CameraMake', '').strip()} {r.get('CameraModel', '').strip()}".strip() or "Unknown"
        by_camera[cam] = by_camera.get(cam, 0) + 1
    lines = ["  By camera:"]
    for cam, c in sorted(by_camera.items(), key=lambda x: -x[1])[:10]:
        lines.append(f"    {cam:<30}: {c}")
    return lines


def main():
    parser = argparse.ArgumentParser(description="Extract EXIF metadata from RAW camera image files.")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-cloud-only", action="store_true")
    args = parser.parse_args()

    run_analysis(
        csv_path=args.csv, output_path=args.output, report_path=args.report,
        extensions=RAW_EXTENSIONS, checkpoint_fields=CHECKPOINT_FIELDS, analyze_fn=analyze_raw,
        force=args.force, skip_cloud_only=args.skip_cloud_only,
        report_title="RAW IMAGE ANALYSIS REPORT", extra_report_lines_fn=report_extra,
    )


if __name__ == "__main__":
    main()
