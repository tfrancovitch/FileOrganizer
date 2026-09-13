r"""What can this project actually answer, and what would it take to answer more?

The P2.11 acceptance reported "27 of 31 reports passed" against a project that had
been inventoried and nothing else. It was true and meaningless: fifteen of those
passes returned zero rows because there was no evidence to find. Nothing in the
product said so, and nothing had to -- each report was answering correctly that it
had found nothing.

This module is the answer to that. It reports, per capability, whether the project
can answer that kind of question, and if not, exactly which stage would fix it.

Every count here is a fact already in the database. Applicable-file counts come
from the analyzers' own declared extension sets, so "PDFs: 1,240 files" is
measured rather than estimated.

Nothing here opens a source file or modifies anything.
"""
from __future__ import annotations

AVAILABLE = "available"
PARTIAL = "partial"
UNAVAILABLE = "unavailable"

#: Stage a user would run to move a capability forward. These are the labels the
#: interface shows on the button, not internal stage keys.
RUN_PRESCAN = "Pre-Scan"
RUN_DUPLICATES = "Find My Duplicates"
RUN_FINGERPRINT = "Full Fingerprinting"
RUN_ANALYZE = "Analyze"
RUN_EXTRACT = "Extract text"
RUN_INDEX = "Index text"


class Capability:
    """One thing the project either can or cannot tell you.

    `state` is available / partial / unavailable. `detail` is a sentence for a
    person, written to say what IS known rather than to scold about what is not.
    `action` names the run that would improve it, or None when nothing would.
    """

    __slots__ = ("key", "label", "state", "detail", "action", "counts")

    def __init__(self, key, label, state, detail, action=None, **counts):
        self.key = key
        self.label = label
        self.state = state
        self.detail = detail
        self.action = action
        self.counts = counts

    def as_dict(self):
        return {"key": self.key, "label": self.label, "state": self.state,
                "detail": self.detail, "action": self.action, "counts": self.counts}

    def __repr__(self):
        return f"<Capability {self.key} {self.state}>"


def _scalar(conn, sql, args=(), default=0):
    try:
        row = conn.execute(sql, args).fetchone()
        return default if row is None or row[0] is None else row[0]
    except Exception:
        return default


def _incomplete_root_count(conn):
    """Active source roots whose LATEST scan did not run to completion.

    The same reading coverage_summary() makes for the evidence strip: a
    latest scan that is not completed / completed_with_warnings, or whose
    root was not available, leaves that root's inventory incomplete.
    """
    try:
        from .coverage import coverage_summary
        summary = coverage_summary(conn)
    except Exception:
        return 0
    return sum(1 for r in summary.get("root_coverage") or []
               if r.get("status") not in ("completed", "completed_with_warnings")
               or r.get("root_availability") not in (None, "available"))


def _analyzer_extension_map():
    """analyzer key -> (label, frozenset of extensions it applies to).

    Read from the engine's own adapters so this cannot drift from what a run
    would actually process.
    """
    try:
        import fo_analyzer_engine
    except ImportError:
        return {}
    out = {}
    for adapter in getattr(fo_analyzer_engine, "ADAPTERS", ()):
        exts = getattr(adapter, "declared_extensions", None)
        if exts:
            out[adapter.key] = (adapter.label, frozenset(e.lower() for e in exts))
    return out


def _counts_by_extension(conn):
    """extension -> (file count, logical bytes) over CURRENT present files."""
    rows = conn.execute(
        "SELECT LOWER(COALESCE(fp.extension_key,'')), COUNT(*), COALESCE(SUM(fs.size_bytes),0) "
        "  FROM file_state fs JOIN file_path fp ON fp.file_path_id=fs.file_path_id "
        " WHERE fs.state='present' GROUP BY 1").fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def _sum_over(ext_counts, exts):
    """(files, bytes) of the extensions in `exts`."""
    files = sum(c for e, (c, _b) in ext_counts.items() if e in exts)
    size = sum(b for e, (_c, b) in ext_counts.items() if e in exts)
    return files, size


def _analyzed_counts(conn):
    """analyzer key -> how many CURRENT file locations it has a result for."""
    try:
        rows = conn.execute(
            "SELECT a.analyzer_key, COUNT(DISTINCT fo.file_path_id) "
            "  FROM analyzer_result ar "
            "  JOIN analyzer_run rr ON rr.analyzer_run_id=ar.analyzer_run_id "
            "  JOIN analyzer a ON a.analyzer_id=rr.analyzer_id "
            "  JOIN file_observation fo ON fo.file_observation_id=ar.file_observation_id "
            "  JOIN file_state fs ON fs.file_path_id=fo.file_path_id "
            "                    AND fs.current_observation_id=ar.file_observation_id "
            " GROUP BY a.analyzer_key").fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


def current_duplicate_counts(conn):
    """(groups, files in them, reclaimable bytes or None) for the CURRENT state.

    Counted from the Phase 2 current-duplicate projection -- current files
    sharing a content identity -- never from `duplicate_group`, which keeps one
    row per group PER RUN and so triples after a stopped run, a complete run
    and a fingerprinting run of the same files. The projection needs a
    writable connection to rebuild; on a read-only one the latest run's
    groups stand in, with reclaimable bytes unknown rather than guessed.
    """
    try:
        from .derived import ensure_duplicate_projection
        ensure_duplicate_projection(conn)
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(location_count),0), COALESCE(SUM(reclaimable_bytes),0) "
            "FROM p2_current_duplicate_summary").fetchone()
        return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)
    except Exception:
        pass
    latest = "(SELECT MAX(duplicate_run_id) FROM duplicate_run)"
    groups = _scalar(conn, f"SELECT COUNT(*) FROM duplicate_group WHERE duplicate_run_id={latest}")
    members = _scalar(conn, f"SELECT COALESCE(SUM(member_count),0) FROM duplicate_group WHERE duplicate_run_id={latest}")
    return int(groups or 0), int(members or 0), None


def _failed_counts(conn):
    """analyzer key -> files whose CURRENT attempt by that analyzer ended in error."""
    try:
        rows = conn.execute(
            """
            SELECT a.analyzer_key, COUNT(DISTINCT fs.file_path_id)
              FROM analyzer_result ar
              JOIN analyzer_run rr ON rr.analyzer_run_id=ar.analyzer_run_id
              JOIN analyzer a ON a.analyzer_id=rr.analyzer_id
              JOIN file_state fs ON fs.current_observation_id=ar.file_observation_id
             WHERE ar.status='error'
               AND NOT EXISTS (
                   SELECT 1 FROM analyzer_result n
                   JOIN analyzer_run nr ON nr.analyzer_run_id=n.analyzer_run_id
                   JOIN analyzer na ON na.analyzer_id=nr.analyzer_id
                   WHERE n.file_observation_id=ar.file_observation_id
                     AND na.analyzer_key=a.analyzer_key
                     AND (n.analyzed_utc>ar.analyzed_utc
                          OR (n.analyzed_utc=ar.analyzed_utc AND n.analyzer_result_id>ar.analyzer_result_id)))
             GROUP BY a.analyzer_key""").fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


def project_capabilities(conn):
    """Everything this project can and cannot currently answer.

    Ordered the way a person meets them: what is here, what is identical, what is
    inside, what can be searched.
    """
    caps = []
    present = _scalar(conn, "SELECT COUNT(*) FROM file_state WHERE state='present'")
    inaccessible = _scalar(conn, "SELECT COUNT(*) FROM file_state WHERE state='inaccessible'")

    # --- inventory ---------------------------------------------------------
    if present == 0:
        caps.append(Capability(
            "inventory", "What is here", UNAVAILABLE,
            "Nothing has been scanned yet.", RUN_PRESCAN, present=0))
        return caps

    # Whether the latest walk of every root actually finished. A stopped
    # walk is recorded as an interrupted scan; an unreachable root as
    # missing. Either way the files not reached are simply absent from the
    # inventory -- not marked missing, not counted -- and the only honest
    # reading of the count above is "at least".
    incomplete_roots = _incomplete_root_count(conn)

    if incomplete_roots:
        caps.append(Capability(
            "inventory", "What is here", PARTIAL,
            f"At least {present:,} files. The latest walk of {incomplete_roots:,} source "
            f"folder(s) did not finish (stopped, or the folder was unreachable), so "
            f"files it never reached are not in this inventory at all.",
            RUN_PRESCAN, present=present, inaccessible=inaccessible,
            incomplete_roots=incomplete_roots))
    elif inaccessible:
        caps.append(Capability(
            "inventory", "What is here", PARTIAL,
            f"{present:,} files. {inaccessible:,} paths could not be read, so every "
            f"answer below covers only what was reachable.",
            RUN_PRESCAN, present=present, inaccessible=inaccessible))
    else:
        caps.append(Capability(
            "inventory", "What is here", AVAILABLE,
            f"{present:,} files, complete coverage.",
            None, present=present, inaccessible=0))

    # --- content identity --------------------------------------------------
    identified = _scalar(
        conn, "SELECT COUNT(*) FROM file_state WHERE state='present' AND content_id IS NOT NULL")
    size_unique = _scalar(
        conn, "SELECT COUNT(*) FROM file_state WHERE state='present' AND hash_status='size_unique'")

    # A file with neither a fingerprint nor a size-unique proof has NO verdict.
    # Why it has none matters: a run that was stopped can be run again; a
    # file that could not be read cannot be fixed by a button. The duplicate
    # question is only "fully answered" when this number is zero -- the
    # earlier wording said so whenever anything at all had been fingerprinted,
    # which after a stopped run would have been exactly the overstatement
    # this module exists to prevent.
    no_verdict = max(0, present - identified - size_unique)
    unexamined = _scalar(
        conn, "SELECT COUNT(*) FROM file_state WHERE state='present' AND content_id IS NULL "
              "AND (hash_status IS NULL OR hash_status IN ('not_attempted','unresolved'))")
    unreadable = _scalar(
        conn, "SELECT COUNT(*) FROM file_state WHERE state='present' AND content_id IS NULL "
              "AND hash_status IN ('error','skipped_cloud_only')")

    def _no_verdict_sentence():
        parts = []
        if unexamined:
            parts.append(f"{unexamined:,} were never examined (a run was stopped, or "
                         f"they were added since)")
        if unreadable:
            parts.append(f"{unreadable:,} could not be read")
        return (f"{no_verdict:,} files have no verdict: " + "; ".join(parts) + ". "
                if parts else f"{no_verdict:,} files have no verdict. ")

    if identified == 0:
        caps.append(Capability(
            "identity", "Which files are identical", UNAVAILABLE,
            "No file has been fingerprinted, so duplicates cannot be found at all.",
            RUN_DUPLICATES, identified=0, total=present))
    elif no_verdict:
        caps.append(Capability(
            "identity", "Which files are identical", PARTIAL,
            f"{identified:,} of {present:,} files fingerprinted and {size_unique:,} "
            f"proven unique by size. " + _no_verdict_sentence() +
            "The duplicate question is not fully answered.",
            RUN_DUPLICATES if unexamined else None,
            identified=identified, total=present, size_unique=size_unique,
            no_verdict=no_verdict, unexamined=unexamined, unreadable=unreadable))
    elif identified < present:
        # size_unique is a POSITIVE finding: proven not-a-duplicate without being
        # opened. Saying "only 40 of 240 fingerprinted" without that context reads
        # as a gap when the duplicate question is in fact fully answered.
        caps.append(Capability(
            "identity", "Which files are identical", PARTIAL,
            f"{identified:,} of {present:,} files fingerprinted. The other "
            f"{size_unique:,} have a size no other file shares, so they cannot be "
            f"duplicates — the duplicate question is fully answered. Fingerprint "
            f"everything only if you also want verifiable identity for moves or "
            f"change detection.",
            RUN_FINGERPRINT, identified=identified, total=present, size_unique=size_unique))
    else:
        caps.append(Capability(
            "identity", "Which files are identical", AVAILABLE,
            f"Every one of {present:,} files has a verifiable content fingerprint.",
            None, identified=identified, total=present))

    # --- duplicates --------------------------------------------------------
    groups, members, _reclaimable = current_duplicate_counts(conn)
    if identified == 0:
        caps.append(Capability(
            "duplicates", "Duplicates and reclaimable space", UNAVAILABLE,
            "Needs fingerprinting first.", RUN_DUPLICATES, groups=0))
    elif no_verdict:
        # Groups found are real -- their members are byte-identical -- but
        # there may be more groups, or more members, among the unexamined.
        found = (f"{groups:,} duplicate groups covering {members:,} files found so far"
                 if groups else "No duplicates found so far")
        caps.append(Capability(
            "duplicates", "Duplicates and reclaimable space", PARTIAL,
            f"{found}; {no_verdict:,} files have no verdict, so there may be more.",
            RUN_DUPLICATES if unexamined else None,
            groups=groups, members=members, no_verdict=no_verdict))
    elif groups == 0:
        caps.append(Capability(
            "duplicates", "Duplicates and reclaimable space", AVAILABLE,
            "No duplicates found.", None, groups=0))
    else:
        caps.append(Capability(
            "duplicates", "Duplicates and reclaimable space", AVAILABLE,
            f"{groups:,} duplicate groups covering {members:,} files.",
            None, groups=groups, members=members))

    # --- per-bucket analysis ----------------------------------------------
    ext_counts = _counts_by_extension(conn)
    analyzed = _analyzed_counts(conn)
    for key, (label, exts) in sorted(_analyzer_extension_map().items()):
        if key == "content_extraction":
            continue                      # reported separately, below
        applicable, _applicable_bytes = _sum_over(ext_counts, exts)
        if applicable == 0:
            continue                      # a bucket with no files is not a gap
        done = analyzed.get(key, 0)
        short = label.replace(" Analysis", "")
        if done == 0:
            state, detail = UNAVAILABLE, f"{applicable:,} files, none analysed yet."
        elif done < applicable:
            # "have current analysis" rather than "were analysed": a file can hold
            # a result from an earlier observation, which is not the same as its
            # current state having been examined. Claiming the stronger thing
            # would be exactly the kind of overstatement this module exists to stop.
            state = PARTIAL
            detail = (f"{done:,} of {applicable:,} files have current analysis; "
                      f"{applicable - done:,} do not.")
        else:
            state, detail = AVAILABLE, f"All {applicable:,} files analysed."
        caps.append(Capability(
            f"analyze.{key}", short, state, detail,
            None if state == AVAILABLE else RUN_ANALYZE,
            applicable=applicable, analyzed=done))

    # --- text extraction and search ---------------------------------------
    extract = _analyzer_extension_map().get("content_extraction")
    extractable = _sum_over(ext_counts, extract[1])[0] if extract else 0
    extracted = _scalar(
        conn, "SELECT COUNT(*) FROM extracted_content WHERE status='extracted'")
    unsupported = present - extractable

    if extractable == 0:
        detail = "No file in this project is in a format the product can extract text from."
        caps.append(Capability("extraction", "Text extracted", UNAVAILABLE, detail,
                               None, extractable=0))
    elif extracted == 0:
        caps.append(Capability(
            "extraction", "Text extracted", UNAVAILABLE,
            f"{extractable:,} files could have their text extracted; none have been.",
            RUN_EXTRACT, extractable=extractable, extracted=0))
    else:
        # Five outcomes, not two. A file that failed to parse will fail again, so
        # offering "Extract text" for it would be a button that cannot help --
        # only genuinely unattempted files justify the action. A file with no
        # extension that turned out to be a picture or a program is "not a
        # document" -- nothing went wrong with it, so it is not counted as
        # unreadable; a cloud-only file was never opened, by rule.
        failed = _scalar(conn, "SELECT COUNT(*) FROM extracted_content WHERE status='error'")
        empty = _scalar(conn, "SELECT COUNT(*) FROM extracted_content WHERE status='empty'")
        not_documents = _scalar(conn, """
            SELECT COUNT(*) FROM extracted_content ec
              JOIN analyzer_result ar ON ar.analyzer_result_id=ec.analyzer_result_id
             WHERE ec.status='skipped' AND ar.status='not_processed'""")
        cloud_only = _scalar(conn, """
            SELECT COUNT(*) FROM extracted_content ec
              JOIN analyzer_result ar ON ar.analyzer_result_id=ec.analyzer_result_id
             WHERE ec.status='skipped' AND ar.status='skipped_cloud_only'""")
        attempted = extracted + failed + empty + not_documents + cloud_only
        unattempted = max(0, extractable - attempted)
        parts = [f"{extracted:,} of {extractable:,} extracted"]
        if failed:
            parts.append(f"{failed:,} could not be read")
        if empty:
            parts.append(f"{empty:,} held no text")
        if not_documents:
            parts.append(f"{not_documents:,} not documents (no extension, and not text inside)")
        if cloud_only:
            parts.append(f"{cloud_only:,} cloud-only, never opened")
        if unattempted:
            parts.append(f"{unattempted:,} not attempted")
        state = AVAILABLE if unattempted == 0 else PARTIAL
        caps.append(Capability(
            "extraction", "Text extracted", state, ", ".join(parts) + ".",
            RUN_EXTRACT if unattempted else None,
            extractable=extractable, extracted=extracted,
            failed=failed, empty=empty, not_documents=not_documents,
            cloud_only=cloud_only, unattempted=unattempted))

    indexed = _scalar(conn, "SELECT COUNT(*) FROM p2_fts_text_map")
    if extracted == 0:
        search_state, search_detail, action = UNAVAILABLE, "Nothing has been extracted to search.", RUN_EXTRACT
    elif indexed == 0:
        search_state, search_detail, action = UNAVAILABLE, (
            f"{extracted:,} extracted documents are not yet indexed."), RUN_INDEX
    else:
        search_state, action = AVAILABLE, None
        search_detail = f"{indexed:,} distinct documents indexed."
    # The honest part: say what search cannot see, because a zero-result search
    # otherwise reads as "not there" rather than "never looked".
    if unsupported > 0 and extractable > 0:
        search_detail += (f" {unsupported:,} files are in formats this product cannot "
                          f"extract text from, so a search will never match inside them.")
    caps.append(Capability(
        "search", "Search inside files", search_state, search_detail, action,
        indexed=indexed, extracted=extracted, unsupported=unsupported))

    return caps


def summarise(caps):
    """One line per capability, for a console or a log."""
    mark = {AVAILABLE: "yes", PARTIAL: "part", UNAVAILABLE: "no"}
    width = max((len(c.label) for c in caps), default=0)
    return "\n".join(
        f"  {mark[c.state]:>4}  {c.label:<{width}}  {c.detail}"
        + (f"   [{c.action}]" if c.action else "")
        for c in caps)


# ---------------------------------------------------------------------------
# The project summary -- what the landing page shows
# ---------------------------------------------------------------------------

#: Bucket labels for the summary, in the order the analyzers run. "Other" is
#: everything no analyzer claims, so the buckets always add up to the total.
BUCKET_LABELS = {
    "image": "Images", "raw_image": "RAW camera images", "pdf": "PDFs",
    "office": "Office documents", "audio": "Audio", "video": "Video",
    "text": "Text / Markdown", "archive": "Archives",
}


def project_summary(conn):
    """The numbers a project summary page shows, every one a fact in the database.

    Built on project_capabilities() so the summary and the capability actions
    cannot disagree, plus the counts a person asked for that capabilities do
    not carry: total size, every bucket's file count whether or not it has
    been analysed, and "other" -- files no analyzer handles.
    """
    caps = {c.key: c for c in project_capabilities(conn)}
    present = _scalar(conn, "SELECT COUNT(*) FROM file_state WHERE state='present'")
    total_bytes = _scalar(conn, "SELECT COALESCE(SUM(size_bytes),0) FROM file_state WHERE state='present'")
    roots = [r[0] for r in conn.execute(
        "SELECT root_path FROM source_root WHERE project_id=1 AND is_active=1 ORDER BY root_ordinal, root_path")]

    ext_counts = _counts_by_extension(conn)
    analyzed = _analyzed_counts(conn)
    failed = _failed_counts(conn)
    ext_map = _analyzer_extension_map()
    buckets = []
    claimed = 0
    claimed_bytes = 0
    for key in BUCKET_LABELS:
        entry = ext_map.get(key)
        if entry is None:
            continue
        _label, exts = entry
        # Image excludes RAW the way the engine does: RAW files belong to the
        # RAW bucket even though both adapters could open them.
        if key == "image" and "raw_image" in ext_map:
            exts = exts - ext_map["raw_image"][1]
        files, size = _sum_over(ext_counts, exts)
        claimed += files
        claimed_bytes += size
        cap = caps.get(f"analyze.{key}")
        buckets.append({"key": key, "label": BUCKET_LABELS[key], "files": files, "bytes": size,
                        "analysed": analyzed.get(key, 0) if files else 0,
                        "failed": failed.get(key, 0) if files else 0,
                        "extensions": sorted(exts),
                        "action": cap.action if cap else None,
                        "state": cap.state if cap else None})
    other = max(0, present - claimed)
    other_bytes = max(0, total_bytes - claimed_bytes)
    # The buckets are the analyzers that describe a file. Extraction reads
    # text from formats no bucket claims (CSV, JSON, HTML, RTF), so "no
    # analyzer handles these types" is only half true of "Other": say how
    # many of them can still have their text extracted.
    bucket_exts = set().union(*(set(b["extensions"]) for b in buckets)) if buckets else set()
    extract_entry = ext_map.get("content_extraction")
    other_extractable = _sum_over(ext_counts, extract_entry[1] - bucket_exts)[0] if extract_entry else 0
    identified_bytes = _scalar(
        conn, "SELECT COALESCE(SUM(size_bytes),0) FROM file_state WHERE state='present' AND content_id IS NOT NULL")

    identity = caps.get("identity")
    duplicates = caps.get("duplicates")
    extraction = caps.get("extraction")
    search = caps.get("search")
    groups, members, reclaimable = current_duplicate_counts(conn)

    ex = (extraction.counts if extraction else {}) or {}
    if not extraction or extraction.state == UNAVAILABLE and not ex.get("extracted"):
        text_state = "not extracted"
    elif search and search.state == AVAILABLE:
        text_state = "indexed"
    else:
        text_state = "extracted, not indexed"

    # Files whose current attempt by ANY analyzer failed: distinct files, so a
    # file two analyzers choked on counts once, as the person would count it.
    failed_files = _scalar(
        conn, """
        SELECT COUNT(DISTINCT fs.file_path_id)
          FROM analyzer_result ar
          JOIN analyzer_run rr ON rr.analyzer_run_id=ar.analyzer_run_id
          JOIN analyzer a ON a.analyzer_id=rr.analyzer_id
          JOIN file_state fs ON fs.current_observation_id=ar.file_observation_id
         WHERE ar.status='error'
           AND NOT EXISTS (
               SELECT 1 FROM analyzer_result n
               JOIN analyzer_run nr ON nr.analyzer_run_id=n.analyzer_run_id
               JOIN analyzer na ON na.analyzer_id=nr.analyzer_id
               WHERE n.file_observation_id=ar.file_observation_id
                 AND na.analyzer_key=a.analyzer_key
                 AND (n.analyzed_utc>ar.analyzed_utc
                      OR (n.analyzed_utc=ar.analyzed_utc AND n.analyzer_result_id>ar.analyzer_result_id)))""")

    return {
        "present": present, "bytes": total_bytes, "roots": roots,
        "failed_files": failed_files,
        "all_bucket_extensions": sorted(bucket_exts),
        "other_extractable": other_extractable,
        "inventory": caps.get("inventory"),
        "identity": identity, "duplicates": duplicates,
        "groups": groups, "members": members, "reclaimable_bytes": reclaimable,
        "buckets": buckets, "other": other, "other_bytes": other_bytes,
        "identified_bytes": identified_bytes,
        "extraction": extraction, "search": search,
        "text_state": text_state,
        "extractable": ex.get("extractable", 0), "extracted": ex.get("extracted", 0),
        "indexed": (search.counts.get("indexed", 0) if search else 0),
    }
