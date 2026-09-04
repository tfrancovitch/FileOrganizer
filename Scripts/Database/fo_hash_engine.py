#!/usr/bin/env python3
r"""
fo_hash_engine.py
===================================================================
PRODUCTION CODE
The File Organizer -- Version Beta, B3 (Python Hash & Duplicate Engine)
Module version: 1.0.0   Requires schema version: 5
===================================================================

The hashing and duplicate-identification engine, in Python. This is
what B3 puts in the active path in place of PotentialDuplicates.ps1,
PartialHash.ps1, FullHash.ps1 and FullHashInventory.ps1.

WHAT THIS MODULE IS AND IS NOT

It is the ALGORITHM: size-candidate selection, the partial-hash pass,
escalation to full SHA-256, duplicate-group determination, and the
exhaustive pass. It decides nothing about storage and touches no
database -- fo_hash_records.py does that, and fo_hash_reports.py
renders the legacy reports.

That separation is not tidiness for its own sake. It is what lets the
whole decision layer be exercised against the accepted R6 artifacts
with no filesystem at all (see Testing/B3-Tools), which is the only
way to prove group-for-group agreement on a machine that does not have
the controlled suite mounted.

THE ALGORITHM IS R6'S, REPRODUCED -- NOT REDESIGNED
---------------------------------------------------
Every rule below was read out of the accepted PowerShell and then
verified by replaying the accepted artifacts through this code. None
of it was inferred from a design document.

  1. SIZE CANDIDATES  (PotentialDuplicates.ps1)
     Group every inventoried file by exact byte length in order of
     first appearance. Discard groups of one. Rank the rest by
     potential reclaim -- size x (count - 1) -- DESCENDING, with a
     STABLE sort so ties keep first-appearance order, which is what
     PowerShell's Sort-Object does. Number them 1..N. That number is
     SizeGroupID.

  2. PARTIAL HASH  (PartialHash.ps1)
     SHA-256 over the first min(window, filesize) bytes; window is
     65536 by default. Re-group candidates by (SizeGroupID,
     PartialHash) in first-appearance order and assign a status:

         RuledOut            alone in its bucket -- not a duplicate
         ConfirmedDuplicate  bucket of 2+ AND every member fits inside
                             the window, so the partial hash IS the
                             whole-file hash
         NeedsFullHash       bucket of 2+ but at least one member is
                             larger than the window

     Buckets of 2+ get a PartialHashGroupID, numbered 1..N in
     first-appearance order. RuledOut rows get none.

  3. FULL HASH  (FullHash.ps1)
     Only NeedsFullHash files are fully hashed. They are re-grouped by
     (PartialHashGroupID, FullHash); a bucket of one is
     RuledOutByFullHash, a bucket of 2+ is a confirmed duplicate group.

     DuplicateGroupID numbering matters and is not arbitrary: groups
     already settled at the partial stage are numbered FIRST, in
     PartialHashGroupID order, and only then the full-hash groups, in
     first-appearance order. Reproducing the accepted IDs depends on
     that order.

  4. FINAL MERGE
     Every inventoried file gets a FinalStatus. Files that were never
     candidates are UniqueBySize.

  5. EXHAUSTIVE  (FullHashInventory.ps1)
     A separate workflow: full SHA-256 for EVERY inventoried file, no
     tiering, grouped by full hash alone. Its status vocabulary is
     deliberately different (UniqueByHash, not UniqueBySize) because a
     file that is unique here has actually been hashed, and one that is
     unique in step 4 has not.

DETERMINISTIC ORDER, NOT DICTIONARY LUCK
----------------------------------------
R6's group numbering fell out of .NET Dictionary enumeration order.
Depending on that would be depending on an implementation detail of
another runtime. Instead this engine processes candidates in
(SizeGroupID, DB_ID) order at every stage -- which is the order the
accepted artifacts are actually in, and which reproduces every
accepted SizeGroupID, PartialHashGroupID and DuplicateGroupID exactly.
Determinism here is a property of the code, not of the CLR.

WHOLE-FILE IDENTITY
-------------------
A partial hash establishes complete content identity ONLY when the
file fits entirely inside the partial window. That judgement is not
made here twice: it is made in fo_hashes.classify_hash, which R4
wrote, and this module defers to it. For a file larger than the
window, partial equality is a screening result and never proof.

MEMORY
------
File bodies are never held. Both digests stream in bounded chunks and
the engine holds one small record per file -- path, size, two hex
digests -- so a million-file project is a million small records, not a
million file bodies. Nothing here accumulates bytes.
"""

import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fo_hashes                                                # noqa: E402
import win_meta                                                 # noqa: E402


#: PartialHash.ps1's default -PartialHashByteCount. Carried as a
#: parameter everywhere rather than assumed, and recorded per
#: measurement, so a future change cannot silently reinterpret old rows.
DEFAULT_PARTIAL_HASH_BYTES = fo_hashes.DEFAULT_PARTIAL_HASH_BYTES

#: Streaming read size for the full-hash pass. Bounded and constant:
#: the point of streaming is that peak memory does not depend on file
#: size, and a "read it all if it looks small enough" shortcut would
#: quietly give that up.
CHUNK_BYTES = 1024 * 1024

# Status vocabularies. Spelled exactly as R6 spells them, because they
# are written into artifacts the rest of the pipeline reads.
STATUS_RULED_OUT = "RuledOut"
STATUS_CONFIRMED = "ConfirmedDuplicate"
STATUS_NEEDS_FULL = "NeedsFullHash"
STATUS_SKIPPED_CLOUD = "SkippedCloudOnly"
STATUS_ERROR = "Error"

FINAL_UNIQUE_BY_SIZE = "UniqueBySize"
FINAL_RULED_OUT_PARTIAL = "RuledOutByPartialHash"
FINAL_RULED_OUT_FULL = "RuledOutByFullHash"
FINAL_CONFIRMED = "ConfirmedDuplicate"
FINAL_UNIQUE_BY_HASH = "UniqueByHash"


class HashEngineError(Exception):
    """The engine could not run at all. Never raised for one bad file."""


# ---------------------------------------------------------------------------
# The file surface
# ---------------------------------------------------------------------------

class FileReader(object):
    r"""The only part of the engine that touches the filesystem.

    Isolated deliberately. Everything above this class is arithmetic on
    digests and can be tested without a disk; everything Windows-shaped
    -- the \\?\ prefix, sharing mode, streaming -- is here, in two
    methods, where it can be replaced wholesale by a test double.

    SOURCE SAFETY. Both methods open for reading and nothing else. No
    write, no rename, no move, no delete, no attribute change, no
    timestamp restoration. Reading content may cause Windows to update
    LastAccessTime; that is the operating system's doing and B3 does
    not attempt to undo it, because writing a timestamp back would
    itself be the source mutation this rule exists to prevent.
    """

    def __init__(self, chunk_bytes=CHUNK_BYTES):
        self.chunk_bytes = int(chunk_bytes)

    def open_path(self, path):
        r"""The path actually handed to the OS.

        The \\?\ prefix is applied HERE and nowhere else, immediately
        before the open, exactly as R6's Common.ps1 requires. It never
        reaches a record, a report or the database -- the caller keeps
        holding the ordinary path and this return value is not stored.

        THE NON-WINDOWS BRANCH IS VERIFICATION SCAFFOLDING. Paths are
        stored with backslash separators, because that is what Windows
        uses and what fo_exports._full_path rebuilds. On a verification
        machine those paths cannot be opened, so the separator is
        translated -- but only after confirming the literal path does
        not exist, so a genuine filename containing a backslash is
        never silently rewritten. This branch cannot run on Windows and
        therefore cannot affect accepted behaviour; what it buys is the
        ability to exercise the whole stack, from SQLite through to the
        exports, over real bytes rather than only over a test double.
        """
        if sys.platform == "win32":
            return win_meta.to_extended_path(path)
        text = str(path)
        if "\\" in text and not os.path.exists(text):
            return text.replace("\\", "/")
        return text

    def partial_digest(self, path, window):
        r"""SHA-256 over the first `window` bytes, or the whole file if
        it is smaller.

        R6 computes min(window, stream.Length) and hashes what it
        actually read. Reading up to `window` bytes and hashing the
        result is the same thing, and it is the same thing in the cases
        that matter: an empty file hashes the empty string, and a file
        that shrank between inventory and hashing is measured at its
        CURRENT length, which is what R6 does because it asks the
        stream, not the inventory.

        Returns (uppercase hex digest, bytes actually read).
        """
        digest = hashlib.sha256()
        remaining = int(window)
        read_total = 0
        with open(self.open_path(path), "rb") as handle:
            while remaining > 0:
                block = handle.read(min(self.chunk_bytes, remaining))
                if not block:
                    break
                digest.update(block)
                read_total += len(block)
                remaining -= len(block)
        return digest.hexdigest().upper(), read_total

    def full_digest(self, path):
        """Streamed SHA-256 over the whole file.

        Bounded chunks, never `handle.read()` with no argument: the
        largest object alive at any moment is one chunk, whatever the
        file's size.

        Returns (uppercase hex digest, bytes read).
        """
        digest = hashlib.sha256()
        read_total = 0
        with open(self.open_path(path), "rb") as handle:
            while True:
                block = handle.read(self.chunk_bytes)
                if not block:
                    break
                digest.update(block)
                read_total += len(block)
        return digest.hexdigest().upper(), read_total


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

class FileEntry(object):
    """One inventoried file, as the engine needs it.

    `key` is whatever the caller uses to identify a file -- the
    file_observation_id in production. The engine never interprets it,
    which is what keeps the engine free of the database.
    """

    __slots__ = ("key", "db_id", "path", "size", "is_offline_or_cloud")

    def __init__(self, key, db_id, path, size, is_offline_or_cloud=False):
        self.key = key
        self.db_id = db_id
        self.path = path
        self.size = int(size or 0)
        self.is_offline_or_cloud = bool(is_offline_or_cloud)


class HashResult(object):
    """What the engine concluded about one file.

    A failed file carries error_kind/error_message and NO digest.
    There is no partial credit: an invented or truncated SHA-256 that
    looked like a real one would be worse than an honest gap, because
    it would be indistinguishable from a real one downstream.
    """

    __slots__ = ("key", "db_id", "path", "size", "is_offline_or_cloud",
                 "size_group_id", "partial_hash", "partial_group_id",
                 "partial_status", "full_hash", "final_status",
                 "duplicate_group_id", "needed_full_hash",
                 "error_kind", "error_message")

    def __init__(self, entry):
        self.key = entry.key
        self.db_id = entry.db_id
        self.path = entry.path
        self.size = entry.size
        self.is_offline_or_cloud = entry.is_offline_or_cloud
        self.size_group_id = None
        self.partial_hash = None
        self.partial_group_id = None
        self.partial_status = None
        self.full_hash = None
        self.final_status = None
        self.duplicate_group_id = None
        self.needed_full_hash = None
        self.error_kind = None
        self.error_message = None

    @property
    def failed(self):
        return self.error_kind is not None

    def fail(self, kind, message):
        self.error_kind = kind
        self.error_message = " ".join(str(message).split())
        self.partial_hash = None
        self.full_hash = None
        return self


class DuplicateGroup(object):
    """A confirmed duplicate group, as the reports and the DB need it."""

    __slots__ = ("group_id", "size", "members", "confirmed_at", "sha256")

    def __init__(self, group_id, size, members, confirmed_at, sha256=None):
        self.group_id = group_id
        self.size = int(size or 0)
        self.members = members
        self.confirmed_at = confirmed_at        # "PartialHash" | "FullHash"
        self.sha256 = sha256

    @property
    def count(self):
        return len(self.members)

    @property
    def redundant(self):
        return max(self.count - 1, 0)

    @property
    def reclaimable(self):
        return self.size * self.redundant


class EngineOutcome(object):
    """Everything one engine run concluded, ready to persist and report."""

    def __init__(self, mode, partial_hash_bytes):
        self.mode = mode                        # "selective" | "exhaustive"
        self.partial_hash_bytes = partial_hash_bytes
        self.results = []                       # HashResult, inventory order
        self.groups = []                        # DuplicateGroup
        self.size_group_count = 0
        self.candidate_count = 0
        self.bytes_read = 0
        self.partial_hashed = 0
        self.full_hashed = 0
        self.errors = []                        # (path, kind, message)

    # -- derived counts, computed rather than tallied ------------------
    #
    # Every number a report or an acceptance run quotes is derived from
    # self.results here, in one place. Counters incremented at three
    # call sites drift; a query over the finished state cannot.

    def count_final(self, status):
        return sum(1 for r in self.results if r.final_status == status)

    def count_partial_status(self, status):
        return sum(1 for r in self.results if r.partial_status == status)

    @property
    def needs_full_hash_count(self):
        return self.count_partial_status(STATUS_NEEDS_FULL)

    @property
    def ruled_out_full_count(self):
        return self.count_final(FINAL_RULED_OUT_FULL)

    @property
    def confirmed_file_count(self):
        return self.count_final(FINAL_CONFIRMED)

    @property
    def confirmed_group_count(self):
        return len(self.groups)

    @property
    def redundant_file_count(self):
        return sum(g.redundant for g in self.groups)

    @property
    def reclaimable_bytes(self):
        return sum(g.reclaimable for g in self.groups)

    @property
    def error_count(self):
        return sum(1 for r in self.results if r.failed)

    def summary(self):
        return {
            "mode": self.mode,
            "inventory_files": len(self.results),
            "candidates": self.candidate_count,
            "size_groups": self.size_group_count,
            "needs_full_hash": self.needs_full_hash_count,
            "ruled_out_full": self.ruled_out_full_count,
            "confirmed_groups": self.confirmed_group_count,
            "confirmed_files": self.confirmed_file_count,
            "redundant_files": self.redundant_file_count,
            "reclaimable_bytes": self.reclaimable_bytes,
            "partial_hashed": self.partial_hashed,
            "full_hashed": self.full_hashed,
            "bytes_read": self.bytes_read,
            "errors": self.error_count,
        }


# ---------------------------------------------------------------------------
# Stage 1 -- size candidates
# ---------------------------------------------------------------------------

def select_size_candidates(entries):
    r"""Group by exact size; return (ordered candidates, group count).

    Reproduces PotentialDuplicates.ps1. Two details carry the whole
    result and neither is incidental:

    FIRST-APPEARANCE grouping. Group-Object yields groups in the order
    their first member appeared, so the tie-break below is anchored to
    inventory order rather than to hash order.

    A STABLE rank. Sort-Object is stable, so groups with equal
    potential reclaim keep that first-appearance order. Python's sorted
    is stable too, which is why this is a one-line translation and not
    a reimplementation. Get this wrong and every SizeGroupID after the
    first tie shifts, and with it every downstream ID.
    """
    order = []
    by_size = {}
    for entry in entries:
        bucket = by_size.get(entry.size)
        if bucket is None:
            bucket = by_size[entry.size] = []
            order.append(entry.size)
        bucket.append(entry)

    duplicates = [(size, by_size[size]) for size in order if len(by_size[size]) > 1]
    ranked = sorted(duplicates, key=lambda kv: kv[0] * (len(kv[1]) - 1),
                    reverse=True)

    candidates = []
    for group_id, (_size, members) in enumerate(ranked, start=1):
        for entry in members:
            candidates.append((group_id, entry))
    return candidates, len(ranked)


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

class HashEngine(object):
    """Runs the selective or exhaustive pipeline over an inventory."""

    def __init__(self, reader=None, partial_hash_bytes=DEFAULT_PARTIAL_HASH_BYTES,
                 progress=None, logger=None, should_continue=None,
                 skip_cloud_only=True):
        self.reader = reader if reader is not None else FileReader()
        self.partial_hash_bytes = int(partial_hash_bytes)
        self.progress = progress
        self.logger = logger
        #: B6.2 P4b -- when True (the default, matching the analyzer
        #: engine), a file the inventory marked is_offline_or_cloud is
        #: NEVER opened: opening a cloud-only placeholder makes the OS
        #: download it, and this is a read-only tool. Such a file is
        #: recorded with hash_status 'skipped_cloud_only' and no digest.
        self.skip_cloud_only = bool(skip_cloud_only)
        #: Not used in B3 and deliberately present. B3 does not
        #: implement pause/resume but is required not to make it
        #: harder; a per-file check between two files is the cheapest
        #: seam that keeps that promise, and leaving it here costs one
        #: branch. Nothing in B3 passes it.
        self.should_continue = should_continue

    # -- plumbing ------------------------------------------------------

    def _log(self, severity, message):
        if self.logger is not None:
            try:
                self.logger(severity, message)
            except Exception:                                   # noqa: BLE001
                pass

    def _report_progress(self, stage, done, total):
        if self.progress is not None:
            try:
                self.progress(stage, done, total)
            except Exception:                                   # noqa: BLE001
                pass

    @staticmethod
    def _classify_error(exc):
        """Name the failure without inventing a taxonomy.

        R6 records one line per failed file and distinguishes nothing
        finer, so these kinds exist to make the SAME distinctions the
        operating system already makes -- and no others. B3 is
        explicitly not allowed to mint error categories for tidiness.
        """
        if isinstance(exc, FileNotFoundError):
            return "FILE MISSING"
        if isinstance(exc, PermissionError):
            return "ACCESS DENIED"
        if isinstance(exc, IsADirectoryError):
            return "NOT A FILE"
        if isinstance(exc, OSError):
            return "IO ERROR"
        return "HASH ERROR"

    def _skip_cloud_only(self, results):
        r"""B6.2 P4b. Mark every cloud-only file 'SkippedCloudOnly' and
        return the set of keys to leave out of the hashing stages.

        Opening one would make the OS hydrate it (a network download and
        an on-disk change) -- unacceptable for a read-only inventory. The
        file still gets a hash_measurement row, with no digest and a
        status that says plainly why.
        """
        skipped = set()
        if not self.skip_cloud_only:
            return skipped
        for result in results:
            if result.is_offline_or_cloud:
                result.final_status = STATUS_SKIPPED_CLOUD
                skipped.add(result.key)
        if skipped:
            self._log("INFO", "%d cloud-only file(s) were left unread to "
                              "avoid triggering a download." % len(skipped))
        return skipped

    def _hash_one(self, result, mode):
        """Hash one file. Returns True on success, False on failure.

        One bad file never propagates: it is recorded on its own
        HashResult and the caller moves on, which is R6's behaviour and
        the reason a single access-denied file does not discard the
        other four thousand results.
        """
        try:
            if mode == "partial":
                digest, read = self.reader.partial_digest(
                    result.path, self.partial_hash_bytes)
                result.partial_hash = digest
            else:
                digest, read = self.reader.full_digest(result.path)
                result.full_hash = digest
            return True, read
        except Exception as exc:                                # noqa: BLE001
            kind = self._classify_error(exc)
            result.fail(kind, getattr(exc, "strerror", None) or str(exc))
            self._log("WARNING", "%s: %s -- %s"
                      % (kind, result.path, result.error_message))
            return False, 0

    # -- selective (Duplicate Run) -------------------------------------

    def run_selective(self, entries):
        r"""The Duplicate Run pipeline: size -> partial -> full.

        `entries` is consumed once, in inventory order, and materialised
        as HashResult records -- one small object per file, no bodies.
        """
        entries = list(entries)
        outcome = EngineOutcome("selective", self.partial_hash_bytes)
        results = [HashResult(e) for e in entries]
        by_key = {r.key: r for r in results}
        outcome.results = results

        # --- B6.2 P4b: cloud-only files are set aside before anything
        #     opens them, and never enter the candidate set ---
        cloud_skip = self._skip_cloud_only(results)
        if cloud_skip:
            entries = [e for e in entries if e.key not in cloud_skip]

        # --- stage 1: size candidates ---
        candidates, group_count = select_size_candidates(entries)
        outcome.size_group_count = group_count
        outcome.candidate_count = len(candidates)
        for group_id, entry in candidates:
            by_key[entry.key].size_group_id = group_id

        # Candidate order for every later stage: (SizeGroupID, DB_ID).
        # Fixed here, once, so no stage can quietly disagree about it.
        ordered = sorted((by_key[e.key] for _g, e in candidates),
                         key=lambda r: (r.size_group_id, r.db_id))

        # --- stage 2: partial hash ---
        total = len(ordered)
        for index, result in enumerate(ordered, start=1):
            ok, read = self._hash_one(result, "partial")
            if ok:
                outcome.partial_hashed += 1
                outcome.bytes_read += read
            else:
                outcome.errors.append((result.path, result.error_kind,
                                       result.error_message))
            if index % 200 == 0 or index == total:
                self._report_progress("partial_hash", index, total)

        self._assign_partial_groups(ordered)

        # --- stage 3: full hash, only where the algorithm demands it ---
        needs_full = [r for r in ordered
                      if r.partial_status == STATUS_NEEDS_FULL]
        total = len(needs_full)
        for index, result in enumerate(needs_full, start=1):
            ok, read = self._hash_one(result, "full")
            if ok:
                outcome.full_hashed += 1
                outcome.bytes_read += read
            else:
                outcome.errors.append((result.path, result.error_kind,
                                       result.error_message))
            if index % 25 == 0 or index == total:
                self._report_progress("full_hash", index, total)

        # --- stage 4: final verdicts and groups ---
        outcome.groups = self._finalize_selective(ordered, results)
        return outcome

    def _assign_partial_groups(self, ordered):
        """Bucket candidates by (SizeGroupID, PartialHash) and assign
        PartialHashGroupID plus the partial-stage status.

        A file that failed to hash is excluded from bucketing entirely
        -- it has no partial hash, so it cannot be shown to match
        anything, and bucketing it under a None key would invent a
        group of "files we could not read", which R6 does not do and
        which would be a false duplicate claim.
        """
        buckets = {}
        for result in ordered:
            if result.failed or result.partial_hash is None:
                result.partial_status = STATUS_ERROR
                continue
            key = (result.size_group_id, result.partial_hash)
            buckets.setdefault(key, []).append(result)

        next_group_id = 1
        for _key, members in buckets.items():           # insertion order
            if len(members) == 1:
                members[0].partial_status = STATUS_RULED_OUT
                members[0].partial_group_id = None
                members[0].needed_full_hash = 0
                continue
            # The whole-file rule: the partial hash counts as complete
            # identity only if EVERY member fits inside the window. One
            # member larger than the window and the entire bucket must
            # go to the full-hash stage -- partial equality on a larger
            # file is never proof of content equality.
            within_window = all(m.size <= self.partial_hash_bytes
                                for m in members)
            status = STATUS_CONFIRMED if within_window else STATUS_NEEDS_FULL
            group_id = next_group_id
            next_group_id += 1
            for member in members:
                member.partial_group_id = group_id
                member.partial_status = status
                member.needed_full_hash = 1 if status == STATUS_NEEDS_FULL else 0

    def _finalize_selective(self, ordered, all_results):
        """Assign FinalStatus and DuplicateGroupID.

        ORDER OF NUMBERING IS THE CONTRACT. Groups settled at the
        partial stage are numbered first, in PartialHashGroupID order,
        then the full-hash groups in first-appearance order. This is
        what FullHash.ps1 does (its section 8a runs before its 8c) and
        reproducing the accepted DuplicateGroupID values depends on it.
        """
        groups = []
        next_group_id = 1

        # 8a -- already whole-file confirmed at the partial stage.
        confirmed_buckets = {}
        for result in ordered:
            if result.partial_status == STATUS_CONFIRMED:
                confirmed_buckets.setdefault(result.partial_group_id,
                                             []).append(result)
        for _pgid, members in confirmed_buckets.items():
            group_id = next_group_id
            next_group_id += 1
            for member in members:
                member.final_status = FINAL_CONFIRMED
                member.duplicate_group_id = group_id
            groups.append(DuplicateGroup(
                group_id, members[0].size, members, "PartialHash",
                sha256=members[0].partial_hash))

        # 8b -- ruled out at the partial stage.
        for result in ordered:
            if result.partial_status == STATUS_RULED_OUT:
                result.final_status = FINAL_RULED_OUT_PARTIAL

        # 8c -- narrow the escalated files by full hash.
        sub_buckets = {}
        for result in ordered:
            if (result.partial_status == STATUS_NEEDS_FULL
                    and not result.failed and result.full_hash):
                sub_buckets.setdefault(
                    (result.partial_group_id, result.full_hash), []).append(result)
        for _key, members in sub_buckets.items():
            if len(members) == 1:
                members[0].final_status = FINAL_RULED_OUT_FULL
                members[0].duplicate_group_id = None
                continue
            group_id = next_group_id
            next_group_id += 1
            for member in members:
                member.final_status = FINAL_CONFIRMED
                member.duplicate_group_id = group_id
            groups.append(DuplicateGroup(
                group_id, members[0].size, members, "FullHash",
                sha256=members[0].full_hash))

        # 8d -- anything escalated that never got a verdict failed.
        for result in ordered:
            if result.final_status is None:
                result.final_status = STATUS_ERROR

        # The merge: every non-candidate was never a duplicate suspect.
        for result in all_results:
            if result.final_status is None:
                result.final_status = FINAL_UNIQUE_BY_SIZE
        return groups

    # -- exhaustive (Full Run) -----------------------------------------

    def run_exhaustive(self, entries):
        """The Full Run pipeline: full SHA-256 for every file, no tiering.

        Deliberately not the selective pipeline with the filters
        removed. Its purpose is a complete hash record, so it does no
        size pre-filtering, and its vocabulary says so: UniqueByHash
        means "hashed, and nothing matched", which is a stronger claim
        than UniqueBySize and must not be spelled the same way.
        """
        entries = list(entries)
        outcome = EngineOutcome("exhaustive", self.partial_hash_bytes)
        results = [HashResult(e) for e in entries]
        outcome.results = results

        # B6.2 P4b -- cloud-only files are never opened, even by a Full Run.
        cloud_skip = self._skip_cloud_only(results)

        hashable = [r for r in results if r.key not in cloud_skip]
        total = len(hashable)
        for index, result in enumerate(hashable, start=1):
            ok, read = self._hash_one(result, "full")
            if ok:
                outcome.full_hashed += 1
                outcome.bytes_read += read
            else:
                outcome.errors.append((result.path, result.error_kind,
                                       result.error_message))
            if index % 200 == 0 or index == total:
                self._report_progress("full_hash", index, total)

        buckets = {}
        for result in results:
            if result.final_status == STATUS_SKIPPED_CLOUD:
                continue
            if result.failed or not result.full_hash:
                result.final_status = STATUS_ERROR
                continue
            buckets.setdefault(result.full_hash, []).append(result)

        groups = []
        next_group_id = 1
        for _digest, members in buckets.items():
            if len(members) == 1:
                members[0].final_status = FINAL_UNIQUE_BY_HASH
                continue
            group_id = next_group_id
            next_group_id += 1
            for member in members:
                member.final_status = FINAL_CONFIRMED
                member.duplicate_group_id = group_id
            groups.append(DuplicateGroup(
                group_id, members[0].size, members, "FullHash",
                sha256=members[0].full_hash))

        outcome.groups = groups
        outcome.candidate_count = 0
        outcome.size_group_count = 0
        return outcome


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def content_identity(result, partial_hash_bytes):
    r"""The whole-file SHA-256 this result establishes, if any.

    Delegates to fo_hashes.classify_hash rather than repeating its
    rule. There is exactly one place in this codebase that decides when
    a partial hash is complete identity, and this is not it -- which is
    the point. Returns (sha256 or None, identity_source or None,
    partial_covers_file flag or None).
    """
    return fo_hashes.classify_hash(result.full_hash, result.partial_hash,
                                   result.size, partial_hash_bytes)
