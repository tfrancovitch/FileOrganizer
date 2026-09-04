#!/usr/bin/env python3
r"""
fo_state.py
===================================================================
PRODUCTION CODE
The File Organizer -- B6 (Current State / History Separation)
Module version: 1.1.0   Requires schema version: 7
===================================================================

The current-state projection, and the boundary between CURRENT truth
and HISTORICAL record.

WHY THIS MODULE EXISTS
----------------------
B5-E.F002 measured the defect this module is the answer to. On an
unchanged 20,000-file corpus, duplicate-query latency rose from 8.9 ms
to 61.1 ms across ten runs -- not because anything about the files had
changed, but because the query spanned all `hash_measurement` history
with no run filter. The product's central answer got slower every time
the product was used.

That is not a slow query. It is a missing concept. B4.5 had a
historical record and treated it as if it were the current state,
because it had nothing else to treat as the current state.

B6 separates them:

    file_state          WHAT IS TRUE NOW
                        One row per location. Never grows with run
                        count. Every current-state question reads
                        only this.

    file_observation    WHAT WAS TRUE, AND WHEN IT CHANGED
                        Append-only change history. Read when someone
                        asks a historical question, and at no other
                        time.

    hash_measurement    WHAT A PARTICULAR RUN MEASURED
                        Provenance. Never scanned to answer "are these
                        files duplicates today?"

THE STALENESS INVARIANT -- THE MOST IMPORTANT THING HERE
--------------------------------------------------------
B5-H asks whether the product can silently create a false record or
mix incompatible file states. A current-state table makes that risk
sharper, not softer: it is now possible to hold a content hash from
run 3 next to an observation from run 7 and present the pair as a fact
about the disk today.

So content identity on file_state is authoritative ONLY when

    file_state.content_observation_id == file_state.current_observation_id

If a file was hashed, then modified, then re-scanned, the new
observation supersedes the old one and the two ids diverge. The hash
is then visibly stale rather than quietly wrong.

`current_duplicate_sets()` enforces that equality inside the SQL, so a
stale hash cannot enter a current duplicate group even by accident.
This is deliberately not a convention the callers are asked to
remember -- B5-F's finding on `legacy_db_id` is what happens when an
invariant lives in a comment.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not hash, walk, export or analyse. It projects and it
compacts. Keeping it free of I/O is what lets the invariants above be
tested without a filesystem.
"""

import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


#: States a location can be in. See migration 006 for the full note on
#: why 'unverified' is separate from 'missing'.
STATE_PRESENT = "present"
STATE_INACCESSIBLE = "inaccessible"
STATE_MISSING = "missing"
STATE_UNVERIFIED = "unverified"

#: How an observation differs from the one it supersedes.
CHANGE_FIRST_SEEN = "first_seen"
CHANGE_MODIFIED = "modified"
CHANGE_REAPPEARED = "reappeared"
CHANGE_VANISHED = "vanished"
CHANGE_INACCESSIBLE = "inaccessible"
CHANGE_VERIFIED = "verified"

#: The fields whose change constitutes a new historical observation.
#: NOT accessed_utc: B5-F.F009 established that product reads can move it.
#: Physical object identity *is* included when available: a path reused by
#: a different file must not inherit the prior observation merely because size
#: and mtime happen to match.
CHANGE_FIELDS = ("size_bytes", "created_utc", "modified_utc", "attributes",
                 "is_reparse_point", "reparse_tag", "is_offline_or_cloud",
                 "volume_serial", "file_index", "hard_link_count",
                 "allocated_size_bytes")


def utc_now():
    """Real UTC, ISO-8601, explicit Z. Not local time wearing a Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

def get_policy(conn, key, default=None):
    """Read one app_meta policy value."""
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?",
                       (key,)).fetchone()
    return row[0] if row else default


def set_policy(conn, key, value):
    conn.execute(
        "INSERT INTO app_meta (key, value, updated_utc) VALUES (?, ?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
        "updated_utc = excluded.updated_utc",
        (key, str(value), utc_now()))


def history_mode(conn):
    """'changes' (default) or 'full' -- B4.5's every-file-every-run behaviour.

    'full' is retained as an explicit opt-in rather than deleted. A
    user who genuinely wants a per-run snapshot of an unchanged corpus
    should be able to have one; what B5-E.F001 objected to was getting
    it without asking and without a way to stop.
    """
    value = (get_policy(conn, "history.mode", "changes") or "changes").lower()
    return value if value in ("changes", "full") else "changes"


def retention_runs(conn):
    """How many runs of history to keep. 0 means keep everything."""
    try:
        return max(0, int(get_policy(conn, "history.retention_runs", "0") or 0))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Presentation ordering -- deterministic, and NOT identity
# ---------------------------------------------------------------------------

def sort_key_for(relative_path):
    r"""The deterministic presentation key for one relative path.

    B5-F.F001's finding is that `os.scandir()` encounter order was
    being promoted into persisted identifiers. The fix is not to sort
    the walk and call the result an identity -- a sorted walk is still
    a walk, and a file added tomorrow still shifts every number after
    it.

    The fix is that presentation order must be a function of the DATA.
    This key is derived from the path alone, so it is identical on
    every machine, in every filesystem order, on every run, and it does
    not need the walk to have happened in any particular sequence.

    Separators are normalised to '\x00' so that a directory sorts
    before its own children's names -- 'a\\b.txt' before 'a-b.txt',
    which is what a user reading a tree expects and what a naive
    string sort gets wrong.
    """
    text = (relative_path or "").replace("/", "\\").lower()
    return text.replace("\\", "\x00")


def assign_root_ordinals(conn, project_id=1):
    r"""Number the project's roots deterministically.

    B5-F.F008: root configuration order changed identifiers. It should
    not, because the order roots happen to sit in settings.json is a
    presentation preference, not a semantic fact about the project.

    Ordinals are assigned by root_path_key, so adding a root inserts it
    where it belongs alphabetically rather than appending it, and
    reordering the list in the UI changes nothing that gets persisted.
    """
    rows = conn.execute(
        "SELECT source_root_id, root_path_key FROM source_root "
        "WHERE project_id = ? ORDER BY root_path_key, source_root_id",
        (project_id,)).fetchall()
    for ordinal, row in enumerate(rows, start=1):
        conn.execute("UPDATE source_root SET root_ordinal = ? "
                     "WHERE source_root_id = ?", (ordinal, row[0]))
    return len(rows)


# ---------------------------------------------------------------------------
# The projection
# ---------------------------------------------------------------------------

class StateProjector(object):
    r"""Maintains file_state as a scan streams observations past it.

    Used as a filter in the ingestion path: the ingestor hands each
    observed record here, and this decides whether the record is new,
    changed or unchanged. Only new and changed records become
    file_observation rows; unchanged ones refresh verified_utc and are
    counted.

    MEMORY. The projector holds one small tuple per location IN THE
    ROOT CURRENTLY BEING SCANNED, loaded once per scan rather than
    queried per file. At a million files that is roughly 80-120 MB of
    tuples against roughly a million individual SELECTs, and B5-E's
    whole complaint about B4.5's export path was that it chose the
    expensive side of exactly this trade without needing to. Here the
    cheap side is also the bounded side: `prime()` takes a root at a
    time, so peak is bounded by the largest single root, not by the
    project.
    """

    def __init__(self, conn, run_id, project_id=1):
        self.conn = conn
        self.run_id = run_id
        self.project_id = project_id
        #: file_path_id -> (state, obs_id, size, modified, attrs,
        #:                  reparse, offline)
        self._current = {}
        #: file_path_ids seen by this scan, for vanished-detection.
        self._seen = set()
        self.new_count = 0
        self.changed_count = 0
        self.unchanged_count = 0
        self.vanished_count = 0
        self._roots_primed = set()

    # -- priming ------------------------------------------------------

    def prime(self, source_root_id):
        """Load current state for one root. Idempotent."""
        if source_root_id in self._roots_primed:
            return
        self._roots_primed.add(source_root_id)
        for row in self.conn.execute(
                "SELECT file_path_id, state, current_observation_id, "
                "       size_bytes, created_utc, modified_utc, attributes, "
                "       is_reparse_point, reparse_tag, is_offline_or_cloud, "
                "       volume_serial, file_index, hard_link_count, "
                "       allocated_size_bytes "
                "FROM file_state WHERE source_root_id = ?", (source_root_id,)):
            self._current[row[0]] = tuple(row[1:])

    # -- classification ----------------------------------------------

    def classify(self, file_path_id, entry):
        r"""Decide what this observation is. Returns a change_kind or None.

        None means unchanged under history.mode='changes'. The current-state
        projection is still refreshed with this scan's metadata and ordinal.
        """
        self._seen.add(file_path_id)
        prior = self._current.get(file_path_id)

        if prior is None or prior[1] is None:
            self.new_count += 1
            return CHANGE_FIRST_SEEN

        prior_state = prior[0]
        if entry.get("status") == "inaccessible":
            if prior_state == STATE_INACCESSIBLE:
                self.unchanged_count += 1
                return None
            self.changed_count += 1
            return CHANGE_INACCESSIBLE

        if prior_state in (STATE_MISSING, STATE_UNVERIFIED, STATE_INACCESSIBLE):
            self.changed_count += 1
            return CHANGE_REAPPEARED

        if self._differs(prior, entry):
            self.changed_count += 1
            return CHANGE_MODIFIED

        self.unchanged_count += 1
        return None

    @staticmethod
    def _differs(prior, entry):
        """Compare stable change signals; LastAccessTime is intentionally absent."""
        (_state, _obs, size, created, modified, attributes, reparse,
         reparse_tag, offline, volume_serial, file_index, hard_links,
         allocated) = prior

        # Physical identity is a powerful replacement detector, but only
        # compare a component when both observations actually know it.
        physical_changed = False
        for old, new in ((volume_serial, entry.get("volume_serial")),
                         (file_index, entry.get("file_index"))):
            if old is not None and new is not None and str(old) != str(new):
                physical_changed = True
                break

        return (
            size != entry.get("size_bytes")
            or (created or "") != (entry.get("created_utc") or "")
            or (modified or "") != (entry.get("modified_utc") or "")
            or (attributes or "") != (entry.get("attributes") or "")
            or _norm_flag(reparse) != _norm_flag(entry.get("is_reparse_point"))
            or _norm_int(reparse_tag) != _norm_int(entry.get("reparse_tag"))
            or _norm_flag(offline) != _norm_flag(entry.get("is_offline_or_cloud"))
            or physical_changed
            or _known_int_changed(hard_links, entry.get("hard_link_count"))
            or _known_int_changed(allocated, entry.get("allocated_size_bytes"))
        )

    def prior_observation_id(self, file_path_id):
        prior = self._current.get(file_path_id)
        return prior[1] if prior else None

    # -- writing ------------------------------------------------------

    @staticmethod
    def _payload_row(r, project_id, run_id, now):
        return (
            r["file_path_id"], project_id, r["source_root_id"], r["state"],
            r.get("observation_id"), r.get("scan_id"), run_id,
            r.get("legacy_db_id"), r.get("size_bytes"),
            r.get("created_local_naive"), r.get("modified_local_naive"),
            r.get("accessed_local_naive"), r.get("created_utc"),
            r.get("modified_utc"), r.get("accessed_utc"),
            r.get("utc_offset_minutes"), r.get("timestamp_model"),
            r.get("attributes"), r.get("is_reparse_point"),
            r.get("reparse_tag"), r.get("is_offline_or_cloud"),
            r.get("depth"), r.get("path_length"), r.get("volume_serial"),
            r.get("file_index"), r.get("hard_link_count"),
            r.get("allocated_size_bytes"), r.get("created_time_state"),
            r.get("modified_time_state"), r.get("accessed_time_state"),
            r.get("first_seen_utc") or now, now, now)

    def upsert(self, rows, now=None):
        r"""Write full current-state rows for changed/new locations."""
        if not rows:
            return 0
        now = now or utc_now()
        payload = [self._payload_row(r, self.project_id, self.run_id, now)
                   for r in rows]
        self.conn.executemany(
            "INSERT INTO file_state ("
            " file_path_id, project_id, source_root_id, state,"
            " current_observation_id, current_scan_id, current_run_id,"
            " current_legacy_db_id, size_bytes, created_local_naive,"
            " modified_local_naive, accessed_local_naive, created_utc,"
            " modified_utc, accessed_utc, utc_offset_minutes, timestamp_model,"
            " attributes, is_reparse_point, reparse_tag, is_offline_or_cloud,"
            " depth, path_length, volume_serial, file_index, hard_link_count,"
            " allocated_size_bytes, created_time_state, modified_time_state,"
            " accessed_time_state, first_seen_utc, verified_utc, state_changed_utc) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(file_path_id) DO UPDATE SET "
            " state=excluded.state,"
            " current_observation_id=COALESCE(excluded.current_observation_id,file_state.current_observation_id),"
            " current_scan_id=excluded.current_scan_id, current_run_id=excluded.current_run_id,"
            " current_legacy_db_id=excluded.current_legacy_db_id,"
            " size_bytes=excluded.size_bytes, created_local_naive=excluded.created_local_naive,"
            " modified_local_naive=excluded.modified_local_naive,"
            " accessed_local_naive=excluded.accessed_local_naive,"
            " created_utc=excluded.created_utc, modified_utc=excluded.modified_utc,"
            " accessed_utc=excluded.accessed_utc, utc_offset_minutes=excluded.utc_offset_minutes,"
            " timestamp_model=excluded.timestamp_model, attributes=excluded.attributes,"
            " is_reparse_point=excluded.is_reparse_point, reparse_tag=excluded.reparse_tag,"
            " is_offline_or_cloud=excluded.is_offline_or_cloud, depth=excluded.depth,"
            " path_length=excluded.path_length, volume_serial=excluded.volume_serial,"
            " file_index=excluded.file_index, hard_link_count=excluded.hard_link_count,"
            " allocated_size_bytes=excluded.allocated_size_bytes,"
            " created_time_state=excluded.created_time_state,"
            " modified_time_state=excluded.modified_time_state,"
            " accessed_time_state=excluded.accessed_time_state,"
            " verified_utc=excluded.verified_utc,"
            " state_changed_utc=CASE WHEN file_state.state<>excluded.state "
            " THEN excluded.state_changed_utc ELSE file_state.state_changed_utc END",
            payload)
        return len(payload)

    def touch_unchanged(self, rows, now=None):
        r"""Refresh current projection for unchanged locations.

        Historical observation identity is deliberately retained; current scan
        metadata/order is not. This is the integration boundary B6 originally
        missed: unchanged files remain current hash/analyzer inputs without
        manufacturing a duplicate history row.
        """
        if not rows:
            return 0
        now = now or utc_now()
        payload = []
        for r in rows:
            payload.append((
                r.get("scan_id"), self.run_id, r.get("legacy_db_id"),
                r.get("size_bytes"), r.get("created_local_naive"),
                r.get("modified_local_naive"), r.get("accessed_local_naive"),
                r.get("created_utc"), r.get("modified_utc"), r.get("accessed_utc"),
                r.get("utc_offset_minutes"), r.get("timestamp_model"),
                r.get("attributes"), r.get("is_reparse_point"), r.get("reparse_tag"),
                r.get("is_offline_or_cloud"), r.get("depth"), r.get("path_length"),
                r.get("volume_serial"), r.get("file_index"), r.get("hard_link_count"),
                r.get("allocated_size_bytes"), r.get("created_time_state"),
                r.get("modified_time_state"), r.get("accessed_time_state"),
                now, r["file_path_id"]))
        self.conn.executemany(
            "UPDATE file_state SET current_scan_id=?, current_run_id=?, "
            "current_legacy_db_id=?, size_bytes=?, created_local_naive=?, "
            "modified_local_naive=?, accessed_local_naive=?, created_utc=?, "
            "modified_utc=?, accessed_utc=?, utc_offset_minutes=?, timestamp_model=?, "
            "attributes=?, is_reparse_point=?, reparse_tag=?, is_offline_or_cloud=?, "
            "depth=?, path_length=?, volume_serial=?, file_index=?, hard_link_count=?, "
            "allocated_size_bytes=?, created_time_state=?, modified_time_state=?, "
            "accessed_time_state=?, verified_utc=?, state='present' "
            "WHERE file_path_id=?", payload)
        return len(payload)

    # -- vanished detection -------------------------------------------

    def mark_vanished(self, source_root_id, scan_id, now=None):
        r"""Locations this COMPLETED scan did not see become 'missing'.

        THE PRECONDITION IS THE POINT. This must only ever be called
        for a scan that ran to completion over a root that was actually
        available. An interrupted scan has gaps that belong to the
        scan, not to the disk, and B5-H is explicit that a missing root
        must not read as an empty one. The caller enforces it; this
        method refuses to guess.

        Returns the number of locations moved to 'missing'.
        """
        now = now or utc_now()
        seen = self._seen
        moved = 0
        batch = []
        for row in self.conn.execute(
                "SELECT file_path_id FROM file_state "
                "WHERE source_root_id = ? AND state IN ('present', 'inaccessible')",
                (source_root_id,)):
            if row[0] not in seen:
                batch.append(row[0])
        if not batch:
            return 0
        self.conn.executemany(
            "UPDATE file_state SET state = 'missing', verified_utc = ?, "
            "state_changed_utc = ?, current_scan_id = ?, current_run_id = ? "
            "WHERE file_path_id = ?",
            [(now, now, scan_id, self.run_id, fid) for fid in batch])
        moved = len(batch)
        self.vanished_count += moved
        return moved


def _norm_flag(value):
    if value is None:
        return None
    return 1 if value else 0

def _norm_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value

def _known_int_changed(old, new):
    if old is None or new is None:
        return False
    return _norm_int(old) != _norm_int(new)


# ---------------------------------------------------------------------------
# Attaching content identity
# ---------------------------------------------------------------------------

def attach_content(conn, run_id, project_id=1):
    r"""Attach this run's current hash facts to file_state.

    B6.1 preserves *why* content identity is absent. A selective run can
    intentionally leave a size-unique file unhashed; that is not the same as
    failed, skipped, or never attempted. hash_observation_id carries the same
    staleness rule as content_observation_id.
    """
    conn.execute(
        "UPDATE file_state SET "
        " hash_observation_id=current_observation_id, hash_run_id=?,"
        " hash_measurement_mode=(SELECT h.measurement_mode FROM hash_measurement h "
        "   WHERE h.run_id=? AND h.file_observation_id=file_state.current_observation_id "
        "   ORDER BY h.hash_measurement_id DESC LIMIT 1),"
        " hash_status=(SELECT h.hash_status FROM hash_measurement h "
        "   WHERE h.run_id=? AND h.file_observation_id=file_state.current_observation_id "
        "   ORDER BY h.hash_measurement_id DESC LIMIT 1) "
        "WHERE file_state.project_id=? AND EXISTS (SELECT 1 FROM hash_measurement h "
        " WHERE h.run_id=? AND h.file_observation_id=file_state.current_observation_id)",
        (run_id,run_id,run_id,project_id,run_id))

    cur = conn.execute(
        "UPDATE file_state SET "
        " content_id=(SELECT h.content_id FROM hash_measurement h "
        "  WHERE h.run_id=? AND h.file_observation_id=file_state.current_observation_id "
        "    AND h.content_id IS NOT NULL ORDER BY h.hash_measurement_id DESC LIMIT 1),"
        " content_observation_id=current_observation_id, content_run_id=? "
        "WHERE file_state.project_id=? AND EXISTS (SELECT 1 FROM hash_measurement h "
        " WHERE h.run_id=? AND h.file_observation_id=file_state.current_observation_id "
        "   AND h.content_id IS NOT NULL)",
        (run_id,run_id,project_id,run_id))
    return cur.rowcount


# ---------------------------------------------------------------------------
# The current-state questions
# ---------------------------------------------------------------------------

def _current_duplicate_sql(stream=False):
    return (
        "WITH eligible AS ("
        " SELECT fs.*, CASE WHEN fs.volume_serial IS NOT NULL AND fs.file_index IS NOT NULL "
        " THEN fs.volume_serial || ':' || fs.file_index ELSE NULL END AS physical_key "
        " FROM file_state fs WHERE fs.project_id=? AND fs.state='present' "
        " AND fs.content_id IS NOT NULL "
        " AND fs.content_observation_id=fs.current_observation_id), "
        "grouped AS ("
        " SELECT content_id, COUNT(*) AS location_count, "
        " COUNT(DISTINCT source_root_id) AS root_count, "
        " COUNT(physical_key) AS known_physical_locations, "
        " COUNT(DISTINCT physical_key) AS known_physical_copies "
        " FROM eligible GROUP BY content_id HAVING COUNT(*)>=?) "
        "SELECT g.content_id,c.sha256,c.size_bytes,c.identity_source,g.location_count,g.root_count,"
        " CASE WHEN g.known_physical_locations=g.location_count THEN g.known_physical_copies ELSE NULL END AS physical_copy_count,"
        " CASE WHEN g.known_physical_locations=g.location_count THEN g.location_count-g.known_physical_copies ELSE NULL END AS hard_link_alias_count,"
        " CASE WHEN g.known_physical_locations=g.location_count THEN 1 ELSE 0 END AS physical_identity_complete,"
        " CASE WHEN g.known_physical_locations=g.location_count "
        " THEN (g.known_physical_copies-1)*COALESCE(c.size_bytes,0) ELSE NULL END AS reclaimable_bytes "
        "FROM grouped g JOIN content c ON c.content_id=g.content_id "
        "ORDER BY c.size_bytes DESC,c.sha256 ASC,g.content_id ASC")

def current_duplicate_sets(conn, min_locations=2, project_id=1):
    r"""Current duplicate content groups with physical-copy semantics.

    LocationCount counts paths. PhysicalCopyCount counts distinct filesystem
    objects when identity is available for every path. ReclaimableBytes is NULL
    when physical identity is incomplete rather than over-claiming reclaimable
    storage. Hard-linked aliases therefore do not inflate the reclaim estimate.
    """
    return conn.execute(_current_duplicate_sql(),
                        (project_id,min_locations)).fetchall()

def iter_current_duplicate_sets(conn, min_locations=2, project_id=1):
    """Streaming current duplicate groups; same truth semantics as above."""
    return conn.execute(_current_duplicate_sql(stream=True),
                        (project_id,min_locations))

def current_locations_for_content(conn, content_id):
    """Where content currently sits. Deterministically ordered."""
    return conn.execute(
        "SELECT sr.root_ordinal, sr.root_path, fp.relative_path, "
        "       fp.file_path_id, fs.size_bytes "
        "FROM file_state fs "
        "JOIN file_path fp   ON fp.file_path_id = fs.file_path_id "
        "JOIN source_root sr ON sr.source_root_id = fs.source_root_id "
        "WHERE fs.content_id = ? AND fs.state = 'present' "
        "  AND fs.content_observation_id = fs.current_observation_id "
        "ORDER BY sr.root_ordinal, fp.path_sort_key, fp.file_path_id",
        (content_id,)).fetchall()


def stale_content_count(conn, project_id=1):
    r"""Locations whose stored content identity no longer describes them.

    Exposed rather than hidden. B5-I asks for a clear trust boundary
    after partial work: a user who re-scanned but did not re-hash
    should be able to see that some hashes now describe superseded
    observations, instead of discovering it through a wrong answer.
    """
    row = conn.execute(
        "SELECT COUNT(*) FROM file_state "
        "WHERE project_id = ? AND content_id IS NOT NULL "
        "  AND state = 'present' "
        "  AND (content_observation_id IS NULL "
        "       OR content_observation_id <> current_observation_id)",
        (project_id,)).fetchone()
    return row[0] if row else 0


def state_summary(conn, project_id=1):
    """Counts by state, plus the staleness figure. For the dashboard."""
    summary = {STATE_PRESENT: 0, STATE_INACCESSIBLE: 0,
               STATE_MISSING: 0, STATE_UNVERIFIED: 0}
    for row in conn.execute(
            "SELECT state, COUNT(*) FROM file_state WHERE project_id = ? "
            "GROUP BY state", (project_id,)):
        summary[row[0]] = row[1]
    summary["stale_content"] = stale_content_count(conn, project_id)
    summary["total"] = sum(summary[k] for k in
                           (STATE_PRESENT, STATE_INACCESSIBLE,
                            STATE_MISSING, STATE_UNVERIFIED))
    return summary


# ---------------------------------------------------------------------------
# History compaction
# ---------------------------------------------------------------------------

def compact_history(conn, keep_runs=None, project_id=1, dry_run=False):
    r"""Drop observation history older than the retention window.

    Theme 2 requires history retention to be an explicit product
    contract. This is the mechanism; `history.retention_runs` is the
    contract, and 0 -- keep everything -- remains the default, because
    silently deleting a user's record would be a worse defect than the
    one it fixes.

    WHAT IS NEVER DROPPED, whatever the setting says:

      * any observation that is some location's current_observation_id;
      * any observation referenced by a duplicate_member;
      * any observation that a surviving observation supersedes, where
        dropping it would break the chain.

    The first two are correctness. The third is honesty: a history with
    a hole in the middle that still reads as continuous is worse than
    no history, because a consumer cannot tell it is looking at one.
    Compaction therefore removes a contiguous OLDEST prefix and records
    the boundary, so what survives is a complete record of a shorter
    period rather than a partial record of a long one.
    """
    keep = retention_runs(conn) if keep_runs is None else int(keep_runs)
    if keep <= 0:
        return {"removed": 0, "kept_runs": 0, "boundary_run_id": None,
                "skipped": "retention disabled"}

    runs = [r[0] for r in conn.execute(
        "SELECT run_id FROM run WHERE project_id = ? "
        "ORDER BY started_utc DESC, run_id DESC", (project_id,)).fetchall()]
    if len(runs) <= keep:
        return {"removed": 0, "kept_runs": len(runs), "boundary_run_id": None}

    keep_ids = set(runs[:keep])
    boundary = runs[keep - 1]

    # Scans belonging to runs outside the window. One query, not one
    # per scan -- compaction runs on the largest databases by
    # definition, so it is the last place to put an N+1.
    doomed_scans = [r[0] for r in conn.execute(
        "SELECT inventory_scan_id FROM inventory_scan "
        "WHERE project_id = ? AND run_id NOT IN (%s)"
        % ",".join("?" * len(keep_ids)),
        [project_id] + sorted(keep_ids)).fetchall()]
    if not doomed_scans:
        return {"removed": 0, "kept_runs": len(keep_ids),
                "boundary_run_id": boundary}

    # The three protections from the docstring, expressed once and
    # shared by the count and the delete so they cannot drift apart.
    protection = (
        " AND file_observation_id NOT IN "
        "     (SELECT current_observation_id FROM file_state "
        "       WHERE current_observation_id IS NOT NULL)"
        " AND file_observation_id NOT IN "
        "     (SELECT content_observation_id FROM file_state "
        "       WHERE content_observation_id IS NOT NULL)"
        " AND file_observation_id NOT IN "
        "     (SELECT file_observation_id FROM duplicate_member)"
        " AND file_observation_id NOT IN "
        "     (SELECT supersedes_observation_id FROM file_observation "
        "       WHERE supersedes_observation_id IS NOT NULL"
        "         AND inventory_scan_id NOT IN (%(scans)s))")

    scans_ph = ",".join("?" * len(doomed_scans))
    where = ("WHERE inventory_scan_id IN (%s)" % scans_ph) + (
        protection % {"scans": scans_ph})
    params = doomed_scans + doomed_scans

    removable = conn.execute(
        "SELECT COUNT(*) FROM file_observation " + where, params).fetchone()[0]

    if dry_run:
        return {"removed": removable, "kept_runs": len(keep_ids),
                "boundary_run_id": boundary, "dry_run": True}

    conn.execute("DELETE FROM file_observation " + where, params)
    set_policy(conn, "history.compacted_boundary_run_id", boundary)
    set_policy(conn, "history.compacted_utc", utc_now())
    return {"removed": removable, "kept_runs": len(keep_ids),
            "boundary_run_id": boundary}


def history_footprint(conn, project_id=1):
    """What history currently costs. For the dashboard and for B5-I."""
    return {
        "observations": conn.execute(
            "SELECT COUNT(*) FROM file_observation").fetchone()[0],
        "locations": conn.execute(
            "SELECT COUNT(*) FROM file_path WHERE project_id = ?",
            (project_id,)).fetchone()[0],
        "measurements": conn.execute(
            "SELECT COUNT(*) FROM hash_measurement").fetchone()[0],
        "runs": conn.execute(
            "SELECT COUNT(*) FROM run WHERE project_id = ?",
            (project_id,)).fetchone()[0],
        "retention_runs": retention_runs(conn),
        "history_mode": history_mode(conn),
    }
