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

Supported runtime:
    This module provides the in-process per-file hash function consumed by
    the database-backed analyzer engine. The retired standalone CSV/folder
    checkpoint pipeline was removed in B6.1.

Requires:
    pip install Pillow imagehash
    (optional, for HEIC/HEIF support) pip install pillow-heif
"""

import sys
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
        "HashSize": hash_size,
        "Error": "",
    }
