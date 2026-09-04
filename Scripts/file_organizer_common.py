"""
file_organizer_common.py
Part of: The File Organizer

Small low-level helpers shared by the supported in-process analyzer modules.
B6.1 removed the retired CSV/checkpoint analyzer orchestration; SQLite and
RunCoordinator are the only supported run/persistence path.
"""

import json
import shutil
import subprocess
import sys

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
