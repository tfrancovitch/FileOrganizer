#!/usr/bin/env python3
"""
AudioAnalysis.py
Part of: The File Organizer
Version: 1.0.1

Extracts technical metadata (via ffprobe) and tag metadata (via Mutagen)
for every audio file selected by the database-backed analyzer engine: duration, bitrate, codec,
sample rate, channel count, plus title/artist/album/year/track/genre tags.

Requires:
    pip install mutagen
    ffprobe on PATH (part of ffmpeg -- not a pip package):
        winget install ffmpeg
        (or download from https://ffmpeg.org/download.html and add to PATH)

"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from file_organizer_common import run_ffprobe, is_ffprobe_available

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
