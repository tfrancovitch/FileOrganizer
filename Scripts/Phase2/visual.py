r"""Visual candidate pairs -- Phase 2 evidence from the image analyzer's hashes (B11).

The image analyzer (ImageHash.py) has always stored one perceptual hash per
image -- pHash, aHash and dHash, 64 bits each at the analyzer's hash size
8 -- in `analyzer_result.detail_json`. Until this build nothing compared
them: the product knew each image's hash and no pair of images that looked
alike. Phase 3's Image Game reviews PAIRS, so this module produces them, as
evidence, on the Phase 2 side of the line (Phase 2 finds; Phase 3 decides).

What a candidate pair is: two current images whose pHash values differ in
at most MAX_DISTANCE of their bits (Hamming distance). The measurement is
reported as it is -- "Hamming distance 6 of 64 bits" -- with a tier word
beside it, never as a percentage that would pretend to be a probability.
aHash and dHash distances are recorded beside it as supporting measurements.

How pairs are found without comparing every image with every other: the
pigeonhole principle. Cut each 64-bit hash into MAX_DISTANCE + 1 segments;
two hashes within MAX_DISTANCE bits of each other must agree exactly on at
least one segment (each differing bit spoils at most one segment). So only
images that share a segment value are compared, bucket by bucket, and a
pair is counted in the first segment it shares, once. The work grows with
the number of images that genuinely look alike, not with the square of the
collection (the bucketed approach the research names, the one Czkawka
uses). A crowd of near-identical images -- blank frames, a burst of
screenshots -- is bounded: each image keeps at most NEIGHBOR_CAP nearest
look-alikes (nearest by distance, then nearest in folder order), and the
generation records how many images the cap trimmed.

Stable identity: a pair IS its two file locations (`visual_candidate`
UNIQUE(a_file_path_id, b_file_path_id)). A later generation that finds the
same two locations again updates that row -- the same candidate_id -- so a
decision recorded against it keeps pointing at it. A pair a generation no
longer finds is retired (`current` = 0), never deleted.

Nothing here opens a source file: the hashes are already in the database.
Cloud-only files were never hashed (the analyzer skips them) and so are
never paired.

When it runs: at the end of every image analysis (RunCoordinator.run_analyzers,
inside the Image Analysis stage), and on demand when the Similar Images page
finds the pairs older than the newest image analysis (`ensure_candidates`) --
a project analyzed by an earlier build gets its pairs the first time the
page opens, with no re-analysis.
"""
from __future__ import annotations

import json
import time

from . import VERSION

METHOD = "phash"
GENERATOR_VERSION = "fileorganizer.visual-candidates.phash/1"
#: The Hamming-distance threshold on 64-bit pHash. Recompressed, resized and
#: lightly edited copies of one photograph typically land within a few bits;
#: unrelated photographs average about half the bits (32).
MAX_DISTANCE = 8
#: Nearest look-alikes kept per image; a crowd larger than this is trimmed and counted.
NEIGHBOR_CAP = 10

#: Tier words for a pHash distance (64-bit). Labels, not probabilities.
TIERS = ((2, "Near-identical"), (5, "Strong near-copy"), (MAX_DISTANCE, "Possible near-copy"))
STRONG_DISTANCE = 5

METHOD_WORDS = {"phash": "Perceptual hash (pHash)", "sscd": "Copy-detection descriptor (SSCD)"}


def tier(distance, bits=64):
    """The tier word for a distance, scaled when the hash is not 64 bits."""
    if distance is None:
        return ""
    scale = (bits or 64) / 64.0
    for limit, word in TIERS:
        if distance <= limit * scale:
            return word
    return "Beyond the candidate threshold"


def describe(method, distance=None, bits=None, ahash=None, dhash=None, score=None):
    """One line naming the method and its own measurement -- never a unified %."""
    name = METHOD_WORDS.get(method, method or "unknown method")
    if distance is not None:
        text = f"{name} -- Hamming distance {distance} of {bits or 64} bits ({tier(distance, bits)})"
        extra = []
        if ahash is not None:
            extra.append(f"aHash {ahash}/{bits or 64}")
        if dhash is not None:
            extra.append(f"dHash {dhash}/{bits or 64}")
        if extra:
            text += "; " + ", ".join(extra)
        return text
    if score is not None:
        return f"{name} -- score {score} (the method's own scale)"
    return name


# ---------------------------------------------------------------------------
# Reading the hashes
# ---------------------------------------------------------------------------

#: Each present file's CURRENT image hash: the newest image-analyzer attempt
#: on its current observation (the latest-attempt rule), when that attempt
#: analyzed it. A file observed again since it was analyzed has no current
#: hash and is not paired until it is analyzed again.
_CURRENT_HASHES_SQL = """
SELECT fs.file_path_id, ar.file_observation_id, ar.analyzer_result_id, ar.detail_json,
       sr.root_ordinal, fp.path_sort_key
  FROM analyzer_result ar
  JOIN analyzer_run rr ON rr.analyzer_run_id = ar.analyzer_run_id
  JOIN analyzer a ON a.analyzer_id = rr.analyzer_id AND a.analyzer_key = 'image'
  JOIN file_state fs ON fs.current_observation_id = ar.file_observation_id AND fs.state = 'present'
  JOIN file_path fp ON fp.file_path_id = fs.file_path_id
  JOIN source_root sr ON sr.source_root_id = fs.source_root_id
 WHERE ar.status = 'analyzed'
   AND NOT EXISTS (
       SELECT 1 FROM analyzer_result n
         JOIN analyzer_run nr ON nr.analyzer_run_id = n.analyzer_run_id
         JOIN analyzer na ON na.analyzer_id = nr.analyzer_id
        WHERE n.file_observation_id = ar.file_observation_id
          AND na.analyzer_key = 'image'
          AND (n.analyzed_utc > ar.analyzed_utc
               OR (n.analyzed_utc = ar.analyzed_utc AND n.analyzer_result_id > ar.analyzer_result_id)))
"""


def _hex(value):
    s = str(value or "").strip().lower()
    if not s:
        return None, None
    try:
        return int(s, 16), len(s) * 4
    except ValueError:
        return None, None


def current_hashes(conn):
    """[(file_path_id, phash, bits, ahash, dhash, observation_id, result_id, order)] and the
    number of analyzed images whose hash could not be read."""
    items, skipped = [], 0
    for r in conn.execute(_CURRENT_HASHES_SQL):
        try:
            detail = json.loads(r["detail_json"] or "{}")
        except ValueError:
            detail = {}
        p, bits = _hex(detail.get("pHash"))
        if p is None:
            skipped += 1
            continue
        a, abits = _hex(detail.get("aHash"))
        d, dbits = _hex(detail.get("dHash"))
        items.append((int(r["file_path_id"]), p, bits, a if abits == bits else None, d if dbits == bits else None,
                      int(r["file_observation_id"]), int(r["analyzer_result_id"]),
                      (int(r["root_ordinal"] or 0), str(r["path_sort_key"] or ""), int(r["file_path_id"]))))
    items.sort(key=lambda x: x[7])
    return items, skipped


# ---------------------------------------------------------------------------
# Finding pairs: the pure part
# ---------------------------------------------------------------------------

def _segments(bits, count):
    """`count` contiguous bit ranges covering `bits`, as (shift, mask)."""
    count = max(1, min(count, bits))
    base, extra = divmod(bits, count)
    out, start = [], 0
    for i in range(count):
        width = base + (1 if i < extra else 0)
        out.append((start, (1 << width) - 1))
        start += width
    return out


def _numpy():
    """numpy, when it is there (imagehash needs it, so it is wherever hashes
    were made); the pure-Python loop below gives the same answer without it."""
    try:
        import numpy
        return numpy
    except Exception:                                               # noqa: BLE001
        return None


_POP8 = None


def _popcount(np, values):
    """Bits set in each uint64, vectorized."""
    global _POP8
    if hasattr(np, "bitwise_count"):
        return np.bitwise_count(values)
    if _POP8 is None:
        _POP8 = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)
    return _POP8[values.view(np.uint8).reshape(values.shape + (8,))].sum(axis=-1)


def _bucket_hits(np, hashes, bucket, max_distance):
    """(x, y, d) for positions x < y in `bucket` within max_distance bits."""
    out = []
    k = len(bucket)
    if np is None or k < 48:
        for x in range(k):
            hx = hashes[bucket[x]]
            for y in range(x + 1, k):
                d = (hx ^ hashes[bucket[y]]).bit_count()
                if d <= max_distance:
                    out.append((x, y, d))
        return out
    arr = np.array([hashes[i] for i in bucket], dtype=np.uint64)
    for start in range(0, k - 1, 1024):
        rows = arr[start:start + 1024]
        dist = _popcount(np, rows[:, None] ^ arr[None, :])
        xs, ys = np.nonzero(dist <= max_distance)
        for x, y in zip(xs.tolist(), ys.tolist()):
            x += start
            if y > x:
                out.append((x, y, int(dist[x - start, y])))
    return out


def find_pairs(items, max_distance=MAX_DISTANCE, cap=NEIGHBOR_CAP):
    """Every pair of items whose pHash values are within `max_distance` bits,
    trimmed to each item's `cap` nearest look-alikes.

    `items`: [(id, phash, bits, ...)] in display order (the order breaks
    ties: a crowd of equal distances keeps its nearest neighbours in folder
    order). Only hashes of the same width are compared. Returns
    ([(i, j, distance)] with i < j as indexes into items, capped_count).
    """
    found = []
    by_bits = {}
    for idx, it in enumerate(items):
        by_bits.setdefault(it[2], []).append(idx)
    for bits, members in by_bits.items():
        np = _numpy() if bits <= 64 else None
        hashes = {idx: items[idx][1] for idx in members}
        segs = _segments(bits, max_distance + 1)
        keys = {idx: tuple((items[idx][1] >> shift) & mask for shift, mask in segs) for idx in members}
        for s in range(len(segs)):
            buckets = {}
            for idx in members:
                buckets.setdefault(keys[idx][s], []).append(idx)
            for bucket in buckets.values():
                if len(bucket) < 2:
                    continue
                for x, y, d in _bucket_hits(np, hashes, bucket, max_distance):
                    i, j = bucket[x], bucket[y]
                    ki, kj = keys[i], keys[j]
                    # Counted in the first segment the two share, once.
                    if any(ki[t] == kj[t] for t in range(s)):
                        continue
                    found.append((i, j, d) if i < j else (j, i, d))
    if not found:
        return [], 0
    neighbours = {}
    for i, j, d in found:
        neighbours.setdefault(i, []).append((d, abs(i - j), j))
        neighbours.setdefault(j, []).append((d, abs(i - j), i))
    capped = 0
    keep = set()
    for i, lst in neighbours.items():
        lst.sort()
        if len(lst) > cap:
            capped += 1
        for _d, _gap, j in lst[:cap]:
            keep.add((min(i, j), max(i, j)))
    pairs = sorted((i, j, d) for i, j, d in found if (i, j) in keep)
    return pairs, capped


# ---------------------------------------------------------------------------
# Writing them: the evidence rows
# ---------------------------------------------------------------------------

def _utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def image_watermark(conn):
    """The newest image-analyzer result id: pairs older than it are out of date."""
    row = conn.execute(
        "SELECT COALESCE(MAX(ar.analyzer_result_id), 0) FROM analyzer_result ar "
        "  JOIN analyzer_run rr ON rr.analyzer_run_id = ar.analyzer_run_id "
        "  JOIN analyzer a ON a.analyzer_id = rr.analyzer_id AND a.analyzer_key = 'image'").fetchone()
    return int(row[0] or 0)


def generate_candidates(conn, run_id=None, max_distance=MAX_DISTANCE, cap=NEIGHBOR_CAP):
    """Compare every current image hash with its look-alikes and record the
    pairs -- one generation, one transaction. Returns the generation's counts.
    Raises on a database error (the transaction is rolled back; nothing is
    half-written)."""
    started = _utc_now()
    t0 = time.monotonic()
    watermark = image_watermark(conn)
    items, skipped = current_hashes(conn)
    pairs, capped = find_pairs(items, max_distance, cap)
    bits_seen = sorted({it[2] for it in items}) or [64]
    if conn.in_transaction:
        conn.commit()
    conn.execute("SAVEPOINT visual_candidates")
    try:
        cur = conn.execute(
            "INSERT INTO visual_candidate_generation(project_id, run_id, method, hash_bits, max_distance, neighbor_cap, "
            "generator_version, started_utc, status, analysis_watermark) VALUES(1,?,?,?,?,?,?,?,'running',?)",
            (run_id, METHOD, bits_seen[-1], max_distance, cap, GENERATOR_VERSION, started, watermark))
        gen = int(cur.lastrowid)
        existing = {(int(r[0]), int(r[1])) for r in conn.execute(
            "SELECT a_file_path_id, b_file_path_id FROM visual_candidate")}
        new = 0
        rows = []
        for i, j, d in pairs:
            a, b = items[i], items[j]
            if a[0] > b[0]:
                a, b = b, a
            if (a[0], b[0]) not in existing:
                new += 1
            ad = (a[3] ^ b[3]).bit_count() if a[3] is not None and b[3] is not None else None
            dd = (a[4] ^ b[4]).bit_count() if a[4] is not None and b[4] is not None else None
            rows.append((a[0], b[0], METHOD, a[2], d, ad, dd, a[5], b[5], a[6], b[6], gen, gen))
        conn.executemany(
            "INSERT INTO visual_candidate(project_id, a_file_path_id, b_file_path_id, method, hash_bits, distance, "
            "ahash_distance, dhash_distance, a_observation_id, b_observation_id, a_analyzer_result_id, b_analyzer_result_id, "
            "first_generation_id, last_generation_id, current) VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,1) "
            "ON CONFLICT(a_file_path_id, b_file_path_id) DO UPDATE SET method=excluded.method, hash_bits=excluded.hash_bits, "
            "distance=excluded.distance, ahash_distance=excluded.ahash_distance, dhash_distance=excluded.dhash_distance, "
            "a_observation_id=excluded.a_observation_id, b_observation_id=excluded.b_observation_id, "
            "a_analyzer_result_id=excluded.a_analyzer_result_id, b_analyzer_result_id=excluded.b_analyzer_result_id, "
            "last_generation_id=excluded.last_generation_id, current=1", rows)
        retired = conn.execute(
            "UPDATE visual_candidate SET current = 0 WHERE current = 1 AND last_generation_id <> ?", (gen,)).rowcount
        elapsed = round(time.monotonic() - t0, 3)
        counts = {"generation_id": gen, "images_considered": len(items), "images_skipped": skipped,
                  "pairs_current": len(rows), "pairs_new": new, "pairs_retired": int(retired or 0),
                  "capped_images": capped, "analysis_watermark": watermark, "elapsed_sec": elapsed,
                  "max_distance": max_distance, "neighbor_cap": cap}
        conn.execute(
            "UPDATE visual_candidate_generation SET finished_utc=?, status='completed', images_considered=?, images_skipped=?, "
            "pairs_current=?, pairs_new=?, pairs_retired=?, capped_images=?, detail_json=? WHERE generation_id=?",
            (_utc_now(), len(items), skipped, len(rows), new, int(retired or 0), capped,
             json.dumps({"elapsed_sec": elapsed, "product_version": VERSION}, sort_keys=True), gen))
        conn.execute("RELEASE SAVEPOINT visual_candidates")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT visual_candidates")
        conn.execute("RELEASE SAVEPOINT visual_candidates")
        raise
    if conn.in_transaction:
        conn.commit()
    return counts


def status(conn):
    """What the project's pairs rest on: the latest completed generation,
    whether newer image analysis exists, and the counts."""
    try:
        gen = conn.execute("SELECT * FROM visual_candidate_generation WHERE status='completed' "
                           "ORDER BY generation_id DESC LIMIT 1").fetchone()
    except Exception:                                               # noqa: BLE001 -- a pre-B11 database
        return {"available": False, "out_of_date": False, "generation": None, "watermark": 0, "images": 0}
    watermark = image_watermark(conn)
    images = conn.execute(
        "SELECT COUNT(*) FROM analyzer_result ar JOIN analyzer_run rr ON rr.analyzer_run_id = ar.analyzer_run_id "
        "  JOIN analyzer a ON a.analyzer_id = rr.analyzer_id AND a.analyzer_key = 'image' "
        "  JOIN file_state fs ON fs.current_observation_id = ar.file_observation_id AND fs.state = 'present' "
        " WHERE ar.status = 'analyzed'").fetchone()[0]
    out_of_date = bool(watermark) and (gen is None or int(gen["analysis_watermark"] or 0) < watermark
                                       or gen["generator_version"] != GENERATOR_VERSION)
    current = conn.execute("SELECT COUNT(*) FROM visual_candidate WHERE current = 1").fetchone()[0]
    return {"available": True, "out_of_date": out_of_date, "generation": dict(gen) if gen else None,
            "watermark": watermark, "images": int(images or 0), "pairs_current": int(current or 0)}


def ensure_candidates(conn, run_id=None):
    """Generate when the pairs are older than the newest image analysis (or
    were never generated). Returns the new generation's counts, or None when
    the pairs were already current."""
    st = status(conn)
    if st["available"] and st["out_of_date"]:
        return generate_candidates(conn, run_id=run_id)
    return None


# ---------------------------------------------------------------------------
# Reading facts for the evidence panel
# ---------------------------------------------------------------------------

def location_facts(conn, file_path_id):
    """What the database knows about one image location: its path, size,
    modified time, state, whether it is cloud-only, its content identity and
    whether that identity is current, and the image analyzer's resolution and
    format on its current observation. None for an unknown id."""
    row = conn.execute(
        "SELECT fs.file_path_id, fs.state, fs.size_bytes, fs.modified_utc, fs.is_offline_or_cloud, fs.content_id, "
        "       fs.content_observation_id, fs.current_observation_id, fp.relative_path, sr.root_path, sr.root_path_key "
        "  FROM file_state fs JOIN file_path fp ON fp.file_path_id = fs.file_path_id "
        "  JOIN source_root sr ON sr.source_root_id = fs.source_root_id WHERE fs.file_path_id = ?",
        (int(file_path_id),)).fetchone()
    if row is None:
        return None
    facts = dict(row)
    facts["path"] = str(row["root_path"]).rstrip("\\/") + "\\" + str(row["relative_path"])
    facts["content_current"] = row["content_id"] is not None and row["content_observation_id"] == row["current_observation_id"]
    image = conn.execute(
        "SELECT ar.width_px, ar.height_px, ar.detail_json, ar.status FROM analyzer_result ar "
        "  JOIN analyzer_run rr ON rr.analyzer_run_id = ar.analyzer_run_id "
        "  JOIN analyzer a ON a.analyzer_id = rr.analyzer_id AND a.analyzer_key = 'image' "
        " WHERE ar.file_observation_id = ? ORDER BY ar.analyzed_utc DESC, ar.analyzer_result_id DESC LIMIT 1",
        (row["current_observation_id"],)).fetchone() if row["current_observation_id"] is not None else None
    facts["width_px"] = image["width_px"] if image else None
    facts["height_px"] = image["height_px"] if image else None
    try:
        detail = json.loads(image["detail_json"] or "{}") if image else {}
    except ValueError:
        detail = {}
    facts["format"] = detail.get("Format") or None
    facts["image_analysis"] = image["status"] if image else None
    return facts
