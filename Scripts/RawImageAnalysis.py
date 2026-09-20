#!/usr/bin/env python3
"""
RawImageAnalysis.py
Part of: The File Organizer
Version: 1.1.1

Extracts EXIF metadata from camera RAW files selected by the database-backed analyzer engine
(CR2, NEF, ARW, DNG, RAF, ORF, RW2, and others -- see RAW_EXTENSIONS in
file_organizer_common.py). These are deliberately excluded from
ImageHash.py's perceptual hashing, since Pillow can't decode most RAW
formats without extra libraries, and full RAW decoding is expensive.

This script instead reads the EXIF header directly (via exifread) --
much cheaper than decoding the actual image data, and gives useful
identifying information: camera make/model, exposure settings, and
capture date. Four containers exifread does not know (Fujifilm RAF,
Minolta MRW, Canon CR3, Sigma X3F) carry their EXIF in an embedded TIFF
or JPEG; the reader lifts that out and hands it to exifread.

Requires:
    pip install exifread

"""

import io
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from file_organizer_common import RAW_EXTENSIONS, to_long_path

try:
    import exifread
except ImportError:
    print("ERROR: exifread is not installed. Run: pip install exifread", file=sys.stderr)
    sys.exit(1)

CHECKPOINT_FIELDS = ["Key", "CameraMake", "CameraModel", "ExposureTime",
                      "FNumber", "ISO", "FocalLength", "DateTimeOriginal", "Error"]


_CANON_CR3_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")
_EMBED_CAP = 16 * 1024 * 1024   # the most the reader lifts out of a container


def _bmff_boxes(buf, start, end):
    """(type, payload_offset, payload_end) for each ISO BMFF box in buf[start:end]."""
    i = start
    while i + 8 <= end:
        size, kind = struct.unpack_from(">I4s", buf, i)
        header = 8
        if size == 1 and i + 16 <= end:
            size = struct.unpack_from(">Q", buf, i + 8)[0]
            header = 16
        elif size == 0:
            size = end - i
        if size < header:
            return
        yield kind, i + header, min(i + size, end)
        i += size


def _exif_streams(f):
    """The byte streams exifread should read. TIFF-based RAWs (CR2, NEF, ARW,
    DNG, ORF, RW2, PEF, ...) and JPEG-wrapped ones are the file itself. Four
    formats wrap their EXIF in a container exifread does not know, and for
    those the reader lifts out the embedded TIFF or JPEG that carries it:
      RAF -- Fujifilm: a JPEG preview at the offset the header holds at 84..87
      MRW -- Minolta: the TTW block, a bare TIFF, inside the MRM header
      CR3 -- Canon: the CMT1 (IFD0) and CMT2 (Exif IFD) boxes, bare TIFFs,
             inside the Canon uuid box of moov
      X3F -- Sigma: the first JPEG-format image section in the directory at
             the end of the file, a JPEG preview with an EXIF APP1 segment
    """
    head = f.read(16)
    f.seek(0)
    if head.startswith(b"FUJIFILMCCD-RAW"):
        f.seek(84)
        offset, length = struct.unpack(">II", f.read(8))
        f.seek(offset)
        yield io.BytesIO(f.read(min(length, _EMBED_CAP)))
    elif head.startswith(b"\x00MRM"):
        end = 8 + struct.unpack(">I", head[4:8])[0]
        i = 8
        while i + 8 <= end:
            f.seek(i)
            kind, length = struct.unpack(">4sI", f.read(8))
            if kind == b"\x00TTW":
                yield io.BytesIO(f.read(min(length, _EMBED_CAP)))
                break
            i += 8 + length
    elif head[4:12] == b"ftypcrx ":
        buf = f.read(_EMBED_CAP)      # moov comes first; mdat is never needed
        for kind, start, end in _bmff_boxes(buf, 0, len(buf)):
            if kind != b"moov":
                continue
            for kind2, start2, end2 in _bmff_boxes(buf, start, end):
                if kind2 == b"uuid" and buf[start2:start2 + 16] == _CANON_CR3_UUID:
                    for kind3, start3, end3 in _bmff_boxes(buf, start2 + 16, end2):
                        if kind3 in (b"CMT1", b"CMT2"):
                            yield io.BytesIO(buf[start3:end3])
            break
    elif head.startswith(b"FOVb"):
        f.seek(-4, 2)
        directory = struct.unpack("<I", f.read(4))[0]
        f.seek(directory)
        if f.read(4) == b"SECd":
            f.seek(directory + 8)
            count = struct.unpack("<I", f.read(4))[0]
            entries = [struct.unpack("<II4s", f.read(12)) for _ in range(min(count, 64))]
            for offset, length, kind in entries:
                if kind in (b"IMAG", b"IMA2") and length > 28:
                    f.seek(offset + 28)          # SECi header: signature, version, type, format, columns, rows, row size
                    if f.read(2) == b"\xff\xd8":
                        f.seek(offset + 28)
                        yield io.BytesIO(f.read(min(length - 28, _EMBED_CAP)))
                        break
    else:
        yield f


def analyze_raw(path):
    tags = {}
    with open(to_long_path(path), "rb") as f:
        for stream in _exif_streams(f):
            for name, value in exifread.process_file(stream, details=False).items():
                tags.setdefault(name, value)

    def tag(name):
        # A bare Exif IFD (the CR3's CMT2 box) names its tags "Image X".
        for key in ("EXIF " + name, "Image " + name):
            if key in tags:
                return str(tags[key])
        return ""

    return {
        "CameraMake": tag("Make"),
        "CameraModel": tag("Model"),
        "ExposureTime": tag("ExposureTime"),
        "FNumber": tag("FNumber"),
        "ISO": tag("ISOSpeedRatings"),
        "FocalLength": tag("FocalLength"),
        "DateTimeOriginal": tag("DateTimeOriginal"),
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
