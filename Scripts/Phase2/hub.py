r"""The hub, and the three doors that lead to it.

The query interface is the hub, not the destination. A person may arrive with
nothing but an inventory, look around, and pull in more evidence when a
question needs it. So the landing view is not a summary of what the project
knows -- it is a statement of what the project can and cannot ANSWER, from
`capability.project_capabilities()`, with a button on every gap that a run
would close.

Two views live here:

  show_hub(app)     the landing view: capabilities, each with its action
  show_doors(app)   after a Pre-Scan: three choices, none of them final

Every button that starts work goes through `confirm_and_run()`, which shows
the estimate first. Work is blocking by decision, and with blocking work the
estimate is a safety feature: a person committing to a run they cannot
interact with must know what they are committing to.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, messagebox

from . import capability as cap
from .runner import (RunRequest, PRESCAN, DUPLICATES, FINGERPRINT, ANALYSIS,
                     INDEX_TEXT, load_settings)

#: Capability action label -> run kind. The labels are what the buttons say.
ACTION_KIND = {
    cap.RUN_PRESCAN: PRESCAN,
    cap.RUN_DUPLICATES: DUPLICATES,
    cap.RUN_FINGERPRINT: FINGERPRINT,
    cap.RUN_ANALYZE: ANALYSIS,
    cap.RUN_EXTRACT: ANALYSIS,
    cap.RUN_INDEX: INDEX_TEXT,
}

STATE_MARK = {cap.AVAILABLE: ("Yes", "#1f6244"),
              cap.PARTIAL: ("Partly", "#7a5700"),
              cap.UNAVAILABLE: ("No", "#8a1c1c")}


# ---------------------------------------------------------------------------
# The hub
# ---------------------------------------------------------------------------

def show_hub(app):
    """The project summary: the numbers, then the buckets, then the text state.

    The user's note on the first version: a page headed "What this project
    can answer" reads as if something were answering questions, and it is not.
    So this is a summary -- Project <name>, totals, files by type, text -- with
    a small button only where a run would change a number.
    """
    app.clear()
    try:
        summary = cap.project_summary(app.conn)
    except Exception as exc:                                    # noqa: BLE001
        messagebox.showerror("Project", str(exc), parent=app)
        return
    name = app.info.get("name", app.project_dir.name)
    app.header(f"Project {name}", None)
    can_run = app.can_run()

    body = ttk.Frame(app.content)
    body.pack(fill="both", expand=True)
    grid = ttk.Frame(body)
    grid.pack(fill="x")
    grid.columnconfigure(1, weight=1)
    row = [0]

    def line(label, value, action=None, note=None, bold=False):
        ttk.Label(grid, text=label, width=26, foreground="#444").grid(row=row[0], column=0, sticky="nw", pady=3)
        ttk.Label(grid, text=value, font=("Segoe UI", 10, "bold") if bold else ("Segoe UI", 10),
                  wraplength=640, justify="left").grid(row=row[0], column=1, sticky="nw", pady=3)
        if action:
            ttk.Button(grid, text=action[0], width=20, command=action[1],
                       state="normal" if can_run else "disabled").grid(row=row[0], column=2, sticky="ne", pady=1, padx=(12, 0))
        if note:
            row[0] += 1
            ttk.Label(grid, text=note, foreground="#7a5700", wraplength=640, justify="left").grid(
                row=row[0], column=1, sticky="nw")
        row[0] += 1

    def heading(text):
        ttk.Label(grid, text=text, font=("Segoe UI", 11, "bold")).grid(row=row[0], column=0, columnspan=3, sticky="w", pady=(14, 4))
        row[0] += 1

    def act(capability):
        if capability is None or not capability.action:
            return None
        return (capability.action, lambda c=capability: _act(app, c))

    # -- totals ----------------------------------------------------------
    inv = summary["inventory"]
    files_text = f"{summary['present']:,}"
    if inv is not None and inv.state != cap.AVAILABLE:
        files_text = ("at least " if inv.counts.get("incomplete_roots") else "") + files_text
    line("Source folder(s)", "\n".join(summary["roots"]) or "—")
    line("Total files", files_text + f"   ({_human_bytes(summary['bytes'])})", act(inv),
         note=None if inv is None or inv.state == cap.AVAILABLE else inv.detail, bold=True)

    ident = summary["identity"]
    ic = (ident.counts if ident else {}) or {}
    if ident is None or ident.state == cap.UNAVAILABLE:
        fp_text = f"0 of {summary['present']:,} files -- no fingerprinting run yet"
    else:
        fp_text = f"{ic.get('identified', 0):,} of {ic.get('total', summary['present']):,} files have a content fingerprint"
        if ic.get("size_unique"):
            fp_text += f"; {ic['size_unique']:,} proven unique by size without being opened"
    line("Fingerprints", fp_text, act(ident),
         note=ident.detail if ident is not None and ident.state == cap.PARTIAL and ic.get("no_verdict") else None, bold=True)

    dup = summary["duplicates"]
    if dup is None or dup.state == cap.UNAVAILABLE:
        dup_text = "not yet determined -- needs fingerprints"
    elif summary["groups"] == 0:
        dup_text = "none found" + ("" if dup.state == cap.AVAILABLE else " so far")
    else:
        dup_text = f"{summary['groups']:,} groups, {summary['members']:,} files"
        if summary["reclaimable_bytes"] is not None:
            dup_text += f", {_human_bytes(summary['reclaimable_bytes'])} reclaimable"
        if dup.state != cap.AVAILABLE:
            dup_text += " -- so far"
    line("Duplicates", dup_text, act(dup) if (dup is not None and dup.action and (ident is None or dup.action != ident.action)) else None,
         note=dup.detail if dup is not None and dup.state == cap.PARTIAL else None, bold=True)

    # -- files by type -----------------------------------------------------
    heading("Files by type")
    shown = 0
    for b in summary["buckets"]:
        if not b["files"]:
            continue
        shown += 1
        done = b["analysed"]
        if done == 0:
            state = "not analysed"
        elif done < b["files"]:
            state = f"{done:,} analysed"
        else:
            state = "analysed"
        action = (b["action"], lambda k=b["key"], lbl=b["label"]: confirm_and_run(
            app, RunRequest(ANALYSIS, f"Analyze: {lbl}", analyzer_keys=[k]))) if b["action"] else None
        line(b["label"], f"{b['files']:,}   ({state})", action)
    if summary["other"]:
        line("Other", f"{summary['other']:,}   (no analyzer handles these types)")
    if not shown and not summary["other"]:
        line("Files by type", "nothing inventoried yet")

    # -- text ----------------------------------------------------------------
    heading("Text")
    ext = summary["extraction"]
    srch = summary["search"]
    if summary["extractable"] == 0 and summary["present"]:
        text_line = "no file is in a format the product can extract text from"
    elif summary["text_state"] == "not extracted":
        text_line = f"not extracted   ({summary['extractable']:,} files could be)"
    elif summary["text_state"] == "indexed":
        text_line = f"extracted and indexed   ({summary['extracted']:,} of {summary['extractable']:,} extracted, {summary['indexed']:,} indexed)"
    else:
        text_line = f"extracted, not indexed   ({summary['extracted']:,} of {summary['extractable']:,} extracted)"
    text_action = act(ext) if (ext is not None and ext.action) else act(srch)
    text_note = None
    if ext is not None and ext.state != cap.AVAILABLE and ext.action:
        text_note = ext.detail
    elif srch is not None and srch.counts.get("unsupported") and summary["extractable"]:
        text_note = (f"{srch.counts['unsupported']:,} files are in formats this product cannot extract text "
                     "from, so a search will never match inside them.")
    line("Text", text_line, text_action, note=text_note)

    if not can_run:
        ttk.Label(body, style="Warn.TLabel",
                  text="This project is not under the application's Projects folder, so runs cannot be "
                       "started from here. Everything already collected can still be queried.").pack(fill="x", pady=(12, 0))

    # -- what next -----------------------------------------------------------
    doors = ttk.Frame(body)
    doors.pack(fill="x", pady=(18, 0))
    ttk.Button(doors, text="Collect more evidence...", command=lambda: show_doors(app),
               state="normal" if can_run else "disabled").pack(side="left")
    ttk.Button(doors, text="Choose what to analyze...",
               command=lambda: show_analyze(app),
               state="normal" if can_run else "disabled").pack(side="left", padx=(6, 0))
    ttk.Button(doors, text="Open project folder",
               command=lambda: _open_folder(app.project_dir)).pack(side="left", padx=(6, 0))
    ttk.Label(doors, text="Logs, run records and reports live in the project folder.",
              foreground="#666").pack(side="left", padx=12)


def _open_folder(path):
    try:
        os.startfile(str(path))
    except OSError as exc:
        messagebox.showerror("Open folder", str(exc))


def _act(app, capability):
    """One capability's button: the run that would move it forward."""
    kind = ACTION_KIND.get(capability.action)
    if kind is None:
        return
    if kind == ANALYSIS:
        # "Analyze" on a bucket row runs that bucket; "Extract text" runs the
        # extraction analyzer. Both are analyzers with a key.
        if capability.key.startswith("analyze."):
            keys = [capability.key.split(".", 1)[1]]
        elif capability.key == "extraction":
            keys = ["content_extraction"]
        else:
            show_analyze(app)
            return
        confirm_and_run(app, RunRequest(ANALYSIS, _analysis_title(keys),
                                        analyzer_keys=keys))
        return
    if kind == PRESCAN:
        confirm_and_run(app, RunRequest(PRESCAN, "Pre-Scan", after="doors"))
        return
    if kind == DUPLICATES:
        confirm_and_run(app, RunRequest(DUPLICATES, "Find My Duplicates"))
        return
    if kind == FINGERPRINT:
        confirm_and_run(app, RunRequest(FINGERPRINT, "Full Fingerprinting"))
        return
    if kind == INDEX_TEXT:
        confirm_and_run(app, RunRequest(INDEX_TEXT, "Index extracted text"))


def _analysis_title(keys):
    import fo_analyzers
    labels = [fo_analyzers.SPEC_BY_KEY[k].label for k in keys if k in fo_analyzers.SPEC_BY_KEY]
    return "Analyze: " + ", ".join(labels) if labels else "Analyze"


# ---------------------------------------------------------------------------
# The three doors
# ---------------------------------------------------------------------------

def show_doors(app):
    """After the Pre-Scan. Three choices, and the copy matters.

    The difference between the first two is NOT thoroughness. Find My
    Duplicates finds every duplicate there is. Full Fingerprinting
    additionally gives non-duplicates an identity. People assume the first
    is the cheap-and-worse option, and it is not, so the screen says so.
    """
    app.clear()
    settings = load_settings(app.project_dir)
    files = settings.get("LastPreliminaryFileCount")
    total_bytes = settings.get("LastPreliminaryTotalBytes") or 0
    candidates = settings.get("LastPotentialDuplicatesCandidateCount")
    errors = settings.get("LastPreliminaryErrorCount") or 0

    app.header("What next?",
               "The Pre-Scan already answers what is here, how big, how old and of what "
               "type. None of these choices is final -- all three stay available from "
               "the hub.")

    facts = ttk.Frame(app.content)
    facts.pack(fill="x", pady=(0, 12))
    for label, value in (
            ("Files", f"{files:,}" if isinstance(files, int) else (files or "?")),
            ("Total size", _human_bytes(total_bytes)),
            ("Files sharing a size with another", f"{candidates:,}"
             if isinstance(candidates, int) else (candidates or "?")),
            ("Could not be read", f"{errors:,}")):
        row = ttk.Frame(facts)
        row.pack(fill="x")
        ttk.Label(row, text=label + ":", width=36, foreground="#666").pack(side="left")
        ttk.Label(row, text=str(value), font=("Segoe UI", 9, "bold")).pack(side="left")

    # A project whose Pre-Scan never calibrated has no stored estimate; the
    # screen before the run measures one regardless, so say so rather than
    # "unavailable".
    dup_estimate = settings.get("DuplicateRunEstimateText") or "measured on the next screen"
    full_estimate = settings.get("FullRunEstimateText") or "measured on the next screen"

    doors = ttk.Frame(app.content)
    doors.pack(fill="both", expand=True)
    doors.columnconfigure((0, 1, 2), weight=1, uniform="door")

    _door(doors, 0, "Find My Duplicates",
          "Answers the duplicate question completely.\n\n"
          "Opens only files that share a size with another file. A file with a size "
          "nothing else has cannot be a duplicate, so it is never opened -- that is a "
          "finding, not a gap.",
          f"Estimated time: {dup_estimate}",
          lambda: confirm_and_run(app, RunRequest(DUPLICATES, "Find My Duplicates")))
    _door(doors, 1, "Full Fingerprinting",
          "Also finds every duplicate -- and gives EVERY file a verifiable content "
          "identity.\n\n"
          "That identity is what later lets a file be verified after a move, and a "
          "change be detected on a re-scan. The difference from the first door is not "
          "thoroughness; it is whether non-duplicates get an identity too.",
          f"Estimated time: {full_estimate}",
          lambda: confirm_and_run(app, RunRequest(FINGERPRINT, "Full Fingerprinting")))
    _door(doors, 2, "Go to the project",
          "Everything the Pre-Scan already knows: files, folders, sizes, ages, types, "
          "largest files and folders, what could not be read.\n\n"
          "Come back for the other two whenever a question needs them.",
          "No further work",
          lambda: app.show_hub())

    ttk.Label(app.content, foreground="#666", justify="left",
              text="Estimates are deliberately conservative, from a sample of this "
                   "project's own files. A run that finishes early is a pleasant "
                   "surprise; one that overruns is a broken promise.").pack(anchor="w", pady=(10, 0))


def _door(parent, column, title, body, estimate, command):
    box = ttk.LabelFrame(parent, text=title, padding=12)
    box.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
    ttk.Label(box, text=body, wraplength=280, justify="left").pack(anchor="w", fill="x")
    ttk.Label(box, text=estimate, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(10, 6))
    ttk.Button(box, text=title, command=command).pack(anchor="w")


# ---------------------------------------------------------------------------
# Choose what to analyze -- inside the hub, where the question arises
# ---------------------------------------------------------------------------

def show_analyze(app):
    """The buckets: what exists, whether it has been done, and a button.

    Buckets, not finer filtering (decision 9): an analyzer run takes analyzer
    keys and no file filter. Extraction and indexing are separately
    deferrable (decision 10) and appear as their own rows.
    """
    import fo_analyzers

    app.clear()
    app.header("Choose what to analyze",
               "Each bucket is one analyzer. Counts are measured from the inventory, "
               "not estimated. Nothing here needs fingerprinting first.")
    try:
        caps = {c.key: c for c in cap.project_capabilities(app.conn)}
    except Exception as exc:                                    # noqa: BLE001
        messagebox.showerror("Analyze", str(exc), parent=app)
        return

    rows = ttk.Frame(app.content)
    rows.pack(fill="x")
    rows.columnconfigure(2, weight=1)
    chosen = {}
    shown = 0
    for spec in fo_analyzers.ANALYZER_SPECS:
        c = caps.get("extraction" if spec.key == "content_extraction" else f"analyze.{spec.key}")
        if c is None:
            continue                       # no files of this kind: not a gap
        var = tk.BooleanVar(value=(c.state != cap.AVAILABLE))
        chosen[spec.key] = var
        ttk.Checkbutton(rows, variable=var).grid(row=shown, column=0, sticky="w", pady=3)
        ttk.Label(rows, text=spec.label, width=26, font=("Segoe UI", 10, "bold")).grid(
            row=shown, column=1, sticky="w", pady=3)
        ttk.Label(rows, text=c.detail, wraplength=640, justify="left").grid(
            row=shown, column=2, sticky="w", pady=3)
        shown += 1

    if not shown:
        ttk.Label(app.content, text="No file in this project is of a kind any analyzer "
                                    "handles.").pack(anchor="w", pady=12)
    search = caps.get("search")
    if search is not None:
        ttk.Separator(app.content).pack(fill="x", pady=10)
        line = ttk.Frame(app.content)
        line.pack(fill="x")
        ttk.Label(line, text="Search inside files", width=28,
                  font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Label(line, text=search.detail, wraplength=640, justify="left").pack(side="left")
        if search.action == cap.RUN_INDEX:
            ttk.Button(line, text=cap.RUN_INDEX, command=lambda: confirm_and_run(
                app, RunRequest(INDEX_TEXT, "Index extracted text"))).pack(side="right")

    controls = ttk.Frame(app.content)
    controls.pack(fill="x", pady=(16, 0))

    def run_chosen():
        keys = [k for k, v in chosen.items() if v.get()]
        if not keys:
            messagebox.showinfo("Analyze", "Nothing is ticked.", parent=app)
            return
        confirm_and_run(app, RunRequest(ANALYSIS, _analysis_title(keys), analyzer_keys=keys))

    ttk.Button(controls, text="Run the ticked buckets", command=run_chosen,
               state="normal" if (shown and app.can_run()) else "disabled").pack(side="left")
    ttk.Button(controls, text="Back to the project", command=app.show_hub).pack(side="left", padx=6)


# ---------------------------------------------------------------------------
# The estimate, then the run
# ---------------------------------------------------------------------------

def confirm_and_run(app, request):
    """Show what the run will do and how long it should take; then run it.

    The estimate is computed on a worker thread against its own read-only
    connection, because for analysis it opens a bounded sample of real files
    with the real analyzers, and that can take a few seconds.
    """
    import threading

    app.clear()
    app.header(request.title, "Before it starts: what it will do, and roughly how long.")
    status = tk.StringVar(value="Measuring a small sample to estimate this run...")
    ttk.Label(app.content, textvariable=status, wraplength=900, justify="left").pack(
        anchor="w", pady=(0, 10))
    box = tk.Text(app.content, height=14, width=100, state="disabled", font=("Consolas", 9))
    box.pack(fill="x")
    controls = ttk.Frame(app.content)
    controls.pack(anchor="w", pady=(12, 0))
    start = ttk.Button(controls, text="Start", state="disabled",
                       command=lambda: app.start_run(request))
    start.pack(side="left")
    ttk.Button(controls, text="Back", command=app.show_hub).pack(side="left", padx=6)

    result = {}

    def measure():
        try:
            result["text"], result["estimate"] = describe_run(app, request)
        except Exception as exc:                                # noqa: BLE001
            result["text"] = f"The estimate could not be computed: {exc}\n\nThe run can still start."
            result["estimate"] = None

    def wait():
        if "text" not in result:
            app.after(120, wait)
            return
        request.estimate = result["estimate"]
        status.set("Nothing below modifies a source file. Cancel is available throughout; "
                   "everything already done is kept if you stop.")
        box.configure(state="normal")
        box.insert("1.0", result["text"])
        box.configure(state="disabled")
        start.configure(state="normal")

    threading.Thread(target=measure, daemon=True).start()
    app.after(120, wait)


def describe_run(app, request):
    """(text for the person, estimate dict or None) for a request."""
    from .core import connect
    import fo_estimates

    if request.kind == PRESCAN:
        roots = request.source_roots or (load_settings(app.project_dir).get("SourceRoots") or [])
        text = ("Pre-Scan: four stages, one click.\n\n"
                "  1. Walk the folder(s) recording name, size, dates, attributes and type.\n"
                "     Reads no file contents. Typically 800-1,200 files per second.\n"
                "  2. Record the walk in the project database.\n"
                "  3. Group files by size; a size nothing else has cannot be a duplicate.\n"
                "  4. Read a small sample (up to 15 files, 50 MB) to estimate fingerprinting.\n\n"
                "Folders:\n" + "\n".join(f"  {r}" for r in roots) +
                "\n\nThe walk's length depends on how many files there are, which is what "
                "the walk finds out. Stop it and the files walked so far are kept, marked "
                "as an incomplete inventory.")
        return text, None

    conn = connect(app.project_dir, write=False)
    try:
        settings = load_settings(app.project_dir)
        if request.kind in (DUPLICATES, FINGERPRINT):
            key = "DuplicateRunEstimateText" if request.kind == DUPLICATES else "FullRunEstimateText"
            estimate = settings.get(key)
            n_total = settings.get("LastPreliminaryFileCount")
            n_cand = settings.get("LastPotentialDuplicatesCandidateCount")
            if not estimate or not isinstance(n_cand, int):
                # A project whose Pre-Scan never calibrated (an older project,
                # or one whose estimate stage failed): measure now, the same
                # way the Pre-Scan's fourth stage does, from the same sample.
                values = hash_estimate_now(conn, settings)
                if values:
                    estimate = values[key]
                    n_total = values["_total_files"]
                    n_cand = values["_candidate_files"]
            estimate = estimate or "unavailable"
            if request.kind == DUPLICATES:
                text = (f"Find My Duplicates opens only the {n_cand:,} files that share a size "
                        f"with another file (of {n_total:,}), hashes the first "
                        f"{fo_estimates_window_kb()} KB of each, and fully hashes only the "
                        f"groups that still match.\n\n"
                        f"Estimated time: {estimate}\n\n"
                        "Measured on this project's own files during the Pre-Scan, then made "
                        "deliberately pessimistic. Includes hashing, database persistence and "
                        "export.") if isinstance(n_cand, int) and isinstance(n_total, int) else (
                    f"Estimated time: {estimate}")
            else:
                text = (f"Full Fingerprinting opens and fully hashes every one of "
                        f"{n_total:,} files.\n\nEstimated time: {estimate}\n\n"
                        "Measured on this project's own files during the Pre-Scan, then made "
                        "deliberately pessimistic. Includes hashing, database persistence and "
                        "export.") if isinstance(n_total, int) else f"Estimated time: {estimate}"
            return text, {"text": estimate}

        if request.kind == ANALYSIS:
            breakdown = fo_estimates.estimate_analysis(conn, request.analyzer_keys)
            return fo_estimates.describe_analysis(breakdown), breakdown

        if request.kind == INDEX_TEXT:
            breakdown = fo_estimates.estimate_indexing(conn)
            return fo_estimates.describe_indexing(breakdown), breakdown
    finally:
        conn.close()
    return "No estimate is available for this run.", None


def fo_estimates_window_kb():
    import fo_hash_engine
    return fo_hash_engine.DEFAULT_PARTIAL_HASH_BYTES // 1024


def hash_estimate_now(conn, settings):
    """Calibrate the two hash estimates from a sample, without writing anything.

    fo_estimates.calibrate() is the Pre-Scan's own fourth stage; run here it
    reads the same bounded sample (up to 15 files, 50 MB) and returns the
    same values, and nothing is written to settings.json -- that stamp
    belongs to a recorded run. Returns None when there is no scan to sample.
    """
    import fo_estimates
    scan_ids = [r[0] for r in conn.execute(
        "SELECT inventory_scan_id FROM inventory_scan WHERE status IN "
        "('completed','completed_with_warnings') AND run_id=(SELECT MAX(run_id) FROM "
        "inventory_scan WHERE status IN ('completed','completed_with_warnings'))")]
    if not scan_ids:
        return None
    try:
        return fo_estimates.calibrate(conn, scan_ids, drive_type=settings.get("TargetDriveType"))
    except Exception:                                           # noqa: BLE001
        return None


def _human_bytes(value):
    if value is None:
        return "Unknown"
    n = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{int(n):,} B"
        n /= 1024
