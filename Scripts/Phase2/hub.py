r"""The project summary, and the three doors that lead to it.

The query interface is the hub, not the destination. A person may arrive with
nothing but an inventory, look around, and pull in more evidence when a
question needs it. So the landing view for a project is a summary -- the
numbers, from `capability.project_summary()` -- with, in the first column of
every line a run would change, the button that runs it; and where nothing
would change, a word that says so.

Two views live here:

  show_hub(app)     the project summary
  show_doors(app)   after a Pre-Scan: three choices, none of them final

Every button starts its run at once. The estimate is measured as the first
step of the run and shown there; only a run longer than
`runner.CAUTION_SECONDS` asks before beginning. Both are the user's second
real-use note: a screen between the click and the work is a step, not a
safeguard, and an eight-minute scan does not need one.
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


# ---------------------------------------------------------------------------
# The project summary
# ---------------------------------------------------------------------------

def show_hub(app):
    """Project <name>: the numbers, the buckets, the text state.

    Layout, from the user's notes: the first column is either a button that
    runs the thing, or a word that says it is done -- so it is never unclear
    which line a button belongs to. Then the label, then the value.
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
    grid.columnconfigure(2, weight=1)
    row = [0]

    def line(label, value, action=None, status=None, note=None, bold=False, link=None):
        """One line: [button | status word] label value. `link` makes the
        label clickable -- a bucket name opens the Files page on that bucket."""
        if action:
            ttk.Button(grid, text=action[0], width=18, command=action[1],
                       state="normal" if can_run else "disabled").grid(row=row[0], column=0, sticky="w", pady=2, padx=(0, 12))
        else:
            ttk.Label(grid, text=status or "", width=18, foreground="#1f6244",
                      font=("Segoe UI", 9, "bold")).grid(row=row[0], column=0, sticky="w", pady=2, padx=(0, 12))
        if link:
            lbl = ttk.Label(grid, text=label, width=22, foreground="#1a4d8f", cursor="hand2",
                            font=("Segoe UI", 9, "underline"))
            lbl.bind("<Button-1>", lambda e, cmd=link: cmd())
            lbl.grid(row=row[0], column=1, sticky="w", pady=2)
        else:
            ttk.Label(grid, text=label, width=22, foreground="#444").grid(row=row[0], column=1, sticky="w", pady=2)
        ttk.Label(grid, text=value, font=("Segoe UI", 10, "bold") if bold else ("Segoe UI", 10),
                  wraplength=700, justify="left").grid(row=row[0], column=2, sticky="w", pady=2)
        if note:
            row[0] += 1
            ttk.Label(grid, text=note, foreground="#7a5700", wraplength=700, justify="left").grid(
                row=row[0], column=2, sticky="w")
        row[0] += 1

    def heading(text, extra=None):
        ttk.Label(grid, text=text, font=("Segoe UI", 11, "bold")).grid(row=row[0], column=1, columnspan=2, sticky="w", pady=(14, 4))
        if extra:
            extra.grid(row=row[0], column=0, sticky="w", pady=(14, 2), padx=(0, 12))
        row[0] += 1

    def act(capability):
        if capability is None or not capability.action:
            return None
        return (capability.action, lambda c=capability: _act(app, c))

    # -- totals ----------------------------------------------------------
    inv = summary["inventory"]
    files_text = f"{summary['present']:,} files   ({_human_bytes(summary['bytes'])})"
    if inv is not None and inv.state != cap.AVAILABLE and inv.counts.get("incomplete_roots"):
        files_text = "at least " + files_text
    line("Source folder(s)", "\n".join(summary["roots"]) or "—")
    line("Total files", files_text, act(inv), status="SCANNED",
         note=None if inv is None or inv.state == cap.AVAILABLE else inv.detail, bold=True)

    ident = summary["identity"]
    ic = (ident.counts if ident else {}) or {}
    if ident is None or ident.state == cap.UNAVAILABLE:
        fp_text = "none yet"
    elif ic.get("identified", 0) >= summary["present"]:
        fp_text = f"{ic.get('identified', 0):,} files   ({_human_bytes(summary['identified_bytes'])})"
    else:
        fp_text = (f"{ic.get('identified', 0):,} of {summary['present']:,} files   "
                   f"({_human_bytes(summary['identified_bytes'])} of {_human_bytes(summary['bytes'])})")
        if ic.get("size_unique") and not ic.get("no_verdict"):
            fp_text += f"; the other {ic['size_unique']:,} proven unique by size without being opened"
    line("Fingerprints", fp_text, act(ident), status="COMPLETE",
         note=ident.detail if ident is not None and ident.state == cap.PARTIAL and ic.get("no_verdict") else None, bold=True)

    dup = summary["duplicates"]
    if dup is None or dup.state == cap.UNAVAILABLE:
        dup_text = "not yet determined -- needs fingerprints"
    elif summary["groups"] == 0:
        dup_text = "none found" + ("" if dup.state == cap.AVAILABLE else " so far")
    else:
        dup_text = f"{summary['members']:,} files, {summary['groups']:,} groups"
        if summary["reclaimable_bytes"] is not None:
            dup_text += f", {_human_bytes(summary['reclaimable_bytes'])} reclaimable"
        if dup.state != cap.AVAILABLE:
            dup_text += " -- so far"
    dup_action = act(dup) if (dup is not None and dup.action and (ident is None or dup.action != ident.action)) else None
    line("Duplicates", dup_text, dup_action,
         status=("COMPLETE" if dup is not None and dup.state == cap.AVAILABLE else "PARTIAL" if dup is not None and dup.state == cap.PARTIAL else ""),
         note=dup.detail if dup is not None and dup.state == cap.PARTIAL else None, bold=True)

    # -- files by type -----------------------------------------------------
    pending = [b for b in summary["buckets"] if b["files"] and b["action"]]
    analyze_all = None
    if pending and can_run:
        keys = [b["key"] for b in pending]
        analyze_all = ttk.Button(grid, text="Analyze all", width=18,
                                 command=lambda keys=keys: app.start_run(
                                     RunRequest(ANALYSIS, "Analyze all", analyzer_keys=keys)))
    heading("Files by type", extra=analyze_all)
    shown = 0
    for b in summary["buckets"]:
        if not b["files"]:
            continue
        shown += 1
        done = b["analysed"]
        value = f"{b['files']:,} files   ({_human_bytes(b['bytes'])})"
        if 0 < done < b["files"]:
            value += f"   {done:,} analysed"
        if b.get("failed"):
            value += f"   {b['failed']:,} could not be analyzed"
        action = (b["action"], lambda k=b["key"], lbl=b["label"]: app.start_run(
            RunRequest(ANALYSIS, f"Analyze: {lbl}", analyzer_keys=[k]))) if b["action"] else None
        line(b["label"], value, action, status="ANALYZED" if not b["action"] else "",
             link=lambda exts=b["extensions"], lbl=b["label"]: app.show_files_for_extensions(exts, lbl))
    if summary["other"]:
        line("Other", f"{summary['other']:,} files   ({_human_bytes(summary['other_bytes'])})   no analyzer handles these types",
             link=lambda: app.show_files_for_extensions(summary["all_bucket_extensions"], "Other", exclude=True))
    if summary.get("failed_files"):
        # The user's question: "16 analyzer failures -- what failures?" Files,
        # with the reason each one carries, one click away.
        per = ", ".join(f"{b['failed']:,} {b['label'].lower()}" for b in summary["buckets"] if b.get("failed"))
        line("Could not be analyzed", f"{summary['failed_files']:,} files   ({per})",
             action=("Show which", app.show_files_for_failures))
    if not shown and not summary["other"]:
        line("Files by type", "nothing inventoried yet")

    # -- text ----------------------------------------------------------------
    heading("Text")
    ext = summary["extraction"]
    srch = summary["search"]
    if summary["extractable"] == 0 and summary["present"]:
        text_line = "no file is in a format the product can extract text from"
        text_status = ""
    elif summary["text_state"] == "not extracted":
        text_line = f"not extracted   ({summary['extractable']:,} files could be)"
        text_status = ""
    elif summary["text_state"] == "indexed":
        text_line = f"extracted and indexed   ({summary['extracted']:,} of {summary['extractable']:,} extracted, {summary['indexed']:,} indexed)"
        text_status = "INDEXED"
    else:
        text_line = f"extracted, not indexed   ({summary['extracted']:,} of {summary['extractable']:,} extracted)"
        text_status = "EXTRACTED"
    text_action = act(ext) if (ext is not None and ext.action) else act(srch)
    text_note = None
    if ext is not None and ext.state != cap.AVAILABLE and ext.action and ext.counts.get("extracted"):
        text_note = ext.detail
    elif srch is not None and srch.counts.get("unsupported") and summary["extractable"]:
        text_note = (f"{srch.counts['unsupported']:,} files are in formats this product cannot extract text "
                     "from, so a search will never match inside them.")
    line("Text", text_line, text_action, status=text_status, note=text_note)

    if not can_run:
        ttk.Label(body, style="Warn.TLabel",
                  text="This project is not under the application's Projects folder, so runs cannot be "
                       "started from here. Everything already collected can still be queried.").pack(fill="x", pady=(12, 0))

    # -- what next: explore, or collect more --------------------------------
    doors = ttk.Frame(body)
    doors.pack(fill="x", pady=(18, 0))
    ttk.Button(doors, text="Explore the files", command=app.show_files).pack(side="left")
    ttk.Button(doors, text="Reports", command=app.show_reports).pack(side="left", padx=(6, 0))
    if ident is None or ident.state != cap.AVAILABLE:
        # The doors offer fingerprinting; once every file is fingerprinted
        # there is nothing behind them, so the button goes away.
        ttk.Button(doors, text="Collect more evidence...", command=lambda: show_doors(app),
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
    """One capability's button: the run that would move it forward. Starts at once."""
    kind = ACTION_KIND.get(capability.action)
    if kind is None:
        return
    if kind == ANALYSIS:
        if capability.key.startswith("analyze."):
            keys = [capability.key.split(".", 1)[1]]
        elif capability.key == "extraction":
            keys = ["content_extraction"]
        else:
            return
        app.start_run(RunRequest(ANALYSIS, _analysis_title(keys), analyzer_keys=keys))
        return
    if kind == PRESCAN:
        app.start_run(RunRequest(PRESCAN, "Pre-Scan", after="doors"))
    elif kind == DUPLICATES:
        app.start_run(RunRequest(DUPLICATES, "Find My Duplicates"))
    elif kind == FINGERPRINT:
        app.start_run(RunRequest(FINGERPRINT, "Full Fingerprinting"))
    elif kind == INDEX_TEXT:
        app.start_run(RunRequest(INDEX_TEXT, "Index extracted text"))


def _analysis_title(keys):
    import fo_analyzers
    labels = [fo_analyzers.SPEC_BY_KEY[k].label for k in keys if k in fo_analyzers.SPEC_BY_KEY]
    return "Analyze: " + ", ".join(labels) if labels else "Analyze"


# ---------------------------------------------------------------------------
# The three doors
# ---------------------------------------------------------------------------

def show_doors(app):
    """After the Pre-Scan. Three columns: the name, the estimate, the button.

    The copy that used to fill each door is now one line at the foot, because
    the user asked for the screen to be clean. The line stays because the
    distinction is genuinely counterintuitive: the difference between the
    first two doors is not thoroughness.
    """
    app.clear()
    settings = load_settings(app.project_dir)
    files = settings.get("LastPreliminaryFileCount")
    total_bytes = settings.get("LastPreliminaryTotalBytes") or 0
    app.header("What next?", None)
    if isinstance(files, int):
        ttk.Label(app.content, text=f"{files:,} files, {_human_bytes(total_bytes)}.",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 16))

    dup_estimate = settings.get("DuplicateRunEstimateText") or "measured when it starts"
    full_estimate = settings.get("FullRunEstimateText") or "measured when it starts"

    doors = ttk.Frame(app.content)
    doors.pack(fill="x")
    doors.columnconfigure((0, 1, 2), weight=1, uniform="door")
    columns = [
        ("Find My Duplicates", f"Estimated time: {dup_estimate}", "Begin Scan",
         lambda: app.start_run(RunRequest(DUPLICATES, "Find My Duplicates"))),
        ("Full Fingerprinting", f"Estimated time: {full_estimate}", "Begin Scan",
         lambda: app.start_run(RunRequest(FINGERPRINT, "Full Fingerprinting"))),
        ("Go to the project", "No further work", "Go to the project", app.show_hub),
    ]
    for col, (title, estimate, button, command) in enumerate(columns):
        ttk.Label(doors, text=title, font=("Segoe UI", 12, "bold")).grid(row=0, column=col, sticky="w", padx=(0, 24), pady=(0, 6))
        ttk.Label(doors, text=estimate).grid(row=1, column=col, sticky="w", padx=(0, 24), pady=(0, 10))
        ttk.Button(doors, text=button, command=command).grid(row=2, column=col, sticky="w", padx=(0, 24))

    ttk.Label(app.content, foreground="#666", wraplength=900, justify="left",
              text="Find My Duplicates answers the duplicate question completely by opening only files that "
                   "share a size with another. Full Fingerprinting also gives every file a verifiable identity, "
                   "for verifying moves and detecting changes later. The difference is not thoroughness. "
                   "All three stay available from the project page.").pack(anchor="w", pady=(24, 0))


# ---------------------------------------------------------------------------
# Estimates on demand (used by the runner's estimating step)
# ---------------------------------------------------------------------------

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
