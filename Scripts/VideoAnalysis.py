#!/usr/bin/env python3
"""
VideoAnalysis.py
Part of: The File Organizer
Version: 1.0.1

Extracts technical metadata (via ffprobe) for every video file in
the run's inventory CSV: duration, bitrate, video codec, resolution, frame
rate, and audio codec (if a video's audio track has one).

Requires:
    ffprobe on PATH (part of ffmpeg -- not a pip package):
        winget install ffmpeg
        (or download from https://ffmpeg.org/download.html and add to PATH)
    (no extra pip packages needed -- json/subprocess are stdlib)

Usage:
    python VideoAnalysis.py --csv DuplicateHashInventory.csv --output VideoInventory.csv --report VideoReport.txt
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from file_organizer_common import run_analysis, run_ffprobe, is_ffprobe_available, parse_frame_rate

EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg", ".3gp"}
CHECKPOINT_FIELDS = ["Key", "DurationSeconds", "Bitrate", "VideoCodec", "Width", "Height",
                      "FrameRate", "AudioCodec", "Error"]


def analyze_video(path):
    probe = run_ffprobe(path)
    fmt = probe.get("format", {})
    video_stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), {})

    return {
        "DurationSeconds": fmt.get("duration", ""),
        "Bitrate": fmt.get("bit_rate", ""),
        "VideoCodec": video_stream.get("codec_name", ""),
        "Width": video_stream.get("width", ""),
        "Height": video_stream.get("height", ""),
        "FrameRate": parse_frame_rate(video_stream.get("r_frame_rate", "")),
        "AudioCodec": audio_stream.get("codec_name", ""),
    }


def report_extra(results):
    durations = [float(r["DurationSeconds"]) for r in results
                 if r.get("DurationSeconds") and not r["Error"]]
    total_hours = sum(durations) / 3600 if durations else 0

    by_codec = {}
    by_resolution = {}
    for r in results:
        c = r.get("VideoCodec") or "Unknown"
        by_codec[c] = by_codec.get(c, 0) + 1
        if r.get("Width") and r.get("Height"):
            res = f"{r['Width']}x{r['Height']}"
            by_resolution[res] = by_resolution.get(res, 0) + 1

    lines = [f"  Total video duration        : {total_hours:.1f} hours", "  By codec:"]
    for c, n in sorted(by_codec.items(), key=lambda x: -x[1]):
        lines.append(f"    {c:<12}: {n}")
    lines.append("  By resolution:")
    for res, n in sorted(by_resolution.items(), key=lambda x: -x[1]):
        lines.append(f"    {res:<12}: {n}")
    return lines


def main():
    parser = argparse.ArgumentParser(description="Extract technical metadata for every video file in the inventory.")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-cloud-only", action="store_true")
    args = parser.parse_args()

    if not is_ffprobe_available():
        print("ERROR: ffprobe not found on PATH. Install ffmpeg (winget install ffmpeg) and retry.", file=sys.stderr)
        sys.exit(1)

    run_analysis(
        csv_path=args.csv, output_path=args.output, report_path=args.report,
        extensions=EXTENSIONS, checkpoint_fields=CHECKPOINT_FIELDS, analyze_fn=analyze_video,
        force=args.force, skip_cloud_only=args.skip_cloud_only,
        report_title="VIDEO ANALYSIS REPORT", extra_report_lines_fn=report_extra,
    )


if __name__ == "__main__":
    main()
