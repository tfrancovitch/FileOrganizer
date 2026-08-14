#!/usr/bin/env python3
"""
AudioAnalysis.py
Part of: The File Organizer
Version: 1.0.1

Extracts technical metadata (via ffprobe) and tag metadata (via Mutagen)
for every audio file in the run's inventory CSV: duration, bitrate, codec,
sample rate, channel count, plus title/artist/album/year/track/genre tags.

Requires:
    pip install mutagen
    ffprobe on PATH (part of ffmpeg -- not a pip package):
        winget install ffmpeg
        (or download from https://ffmpeg.org/download.html and add to PATH)

Usage:
    python AudioAnalysis.py --csv DuplicateHashInventory.csv --output AudioInventory.csv --report AudioReport.txt
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from file_organizer_common import run_analysis, run_ffprobe, is_ffprobe_available

try:
    from mutagen import File as MutagenFile
except ImportError:
    print("ERROR: mutagen is not installed. Run: pip install mutagen", file=sys.stderr)
    sys.exit(1)

EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma", ".opus", ".aiff"}
CHECKPOINT_FIELDS = ["Key", "DurationSeconds", "Bitrate", "Codec", "SampleRate", "Channels",
                      "Title", "Artist", "Album", "Year", "TrackNumber", "Genre", "Error"]


def get_audio_tags(path):
    audio = MutagenFile(path, easy=True)
    if audio is None:
        return {}
    tags = {}
    for key in ("title", "artist", "album", "date", "tracknumber", "genre"):
        val = audio.get(key)
        tags[key] = val[0] if val else ""
    return tags


def analyze_audio(path):
    probe = run_ffprobe(path)
    fmt = probe.get("format", {})
    audio_stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), {})

    result = {
        "DurationSeconds": fmt.get("duration", ""),
        "Bitrate": fmt.get("bit_rate", ""),
        "Codec": audio_stream.get("codec_name", ""),
        "SampleRate": audio_stream.get("sample_rate", ""),
        "Channels": audio_stream.get("channels", ""),
        "Title": "", "Artist": "", "Album": "", "Year": "", "TrackNumber": "", "Genre": "",
    }

    try:
        tags = get_audio_tags(path)
        result["Title"] = tags.get("title", "")
        result["Artist"] = tags.get("artist", "")
        result["Album"] = tags.get("album", "")
        result["Year"] = tags.get("date", "")
        result["TrackNumber"] = tags.get("tracknumber", "")
        result["Genre"] = tags.get("genre", "")
    except Exception:
        pass  # tags are a bonus -- ffprobe's technical data is the core result

    return result


def report_extra(results):
    durations = [float(r["DurationSeconds"]) for r in results
                 if r.get("DurationSeconds") and not r["Error"]]
    total_hours = sum(durations) / 3600 if durations else 0

    by_codec = {}
    for r in results:
        c = r.get("Codec") or "Unknown"
        by_codec[c] = by_codec.get(c, 0) + 1

    lines = [f"  Total audio duration        : {total_hours:.1f} hours", "  By codec:"]
    for c, n in sorted(by_codec.items(), key=lambda x: -x[1]):
        lines.append(f"    {c:<12}: {n}")
    return lines


def main():
    parser = argparse.ArgumentParser(description="Extract technical + tag metadata for every audio file in the inventory.")
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
        extensions=EXTENSIONS, checkpoint_fields=CHECKPOINT_FIELDS, analyze_fn=analyze_audio,
        force=args.force, skip_cloud_only=args.skip_cloud_only,
        report_title="AUDIO ANALYSIS REPORT", extra_report_lines_fn=report_extra,
    )


if __name__ == "__main__":
    main()
