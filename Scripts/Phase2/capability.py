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
    rows = conn.execute(
        "SELECT LOWER(COALESCE(fp.extension_key,'')), COUNT(*) "
        "  FROM file_state fs JOIN file_path fp ON fp.file_path_id=fs.file_path_id "
        " WHERE fs.state='present' GROUP BY 1").fetchall()
    return {r[0]: r[1] for r in rows}


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
    groups = _scalar(conn, "SELECT COUNT(*) FROM duplicate_group")
    members = _scalar(conn, "SELECT COUNT(*) FROM duplicate_member") if groups else 0
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
        applicable = sum(n for e, n in ext_counts.items() if e in exts)
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
    extractable = sum(n for e, n in ext_counts.items() if e in extract[1]) if extract else 0
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
        # Three outcomes, not two. A file that failed to parse will fail again, so
        # offering "Extract text" for it would be a button that cannot help --
        # only genuinely unattempted files justify the action.
        failed = _scalar(conn, "SELECT COUNT(*) FROM extracted_content "
                               "WHERE status NOT IN ('extracted','empty')")
        empty = _scalar(conn, "SELECT COUNT(*) FROM extracted_content WHERE status='empty'")
        attempted = extracted + failed + empty
        unattempted = max(0, extractable - attempted)
        parts = [f"{extracted:,} of {extractable:,} extracted"]
        if failed:
            parts.append(f"{failed:,} could not be read")
        if empty:
            parts.append(f"{empty:,} held no text")
        if unattempted:
            parts.append(f"{unattempted:,} not attempted")
        state = AVAILABLE if unattempted == 0 else PARTIAL
        caps.append(Capability(
            "extraction", "Text extracted", state, ", ".join(parts) + ".",
            RUN_EXTRACT if unattempted else None,
            extractable=extractable, extracted=extracted,
            failed=failed, empty=empty, unattempted=unattempted))

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
