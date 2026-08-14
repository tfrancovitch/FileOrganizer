#!/usr/bin/env python3
r"""
Part of: The File Organizer
Version: 3.2.1

A lightweight Tkinter dashboard for The File Organizer. This is a thin
orchestration layer over the existing PowerShell/Python scripts -- it
does not reimplement any inventory/analysis logic itself. It just
launches the right script, in the right order, in the right folder, and
shows stage-level progress while doing so.

Design choices:
  - Tkinter, not WPF: the lightest GUI toolkit available, ships with
    Python (no install needed), and Pillow -- already a hard dependency
    via ImageHash.py -- will power a future image-comparison view
    without adding anything new.
  - Stage-level progress only ("Step 4 of 9"), not per-file progress --
    far cheaper to implement and run. PowerShell's Write-Progress also
    doesn't surface cleanly through a captured subprocess anyway, so
    fine-grained progress would need rework of multiple scripts for
    little practical benefit.
  - Every pipeline script is invoked as an unmodified subprocess, always
    from the single master Scripts\ folder -- projects no longer get
    their own copy of the scripts (see New-Project.ps1 v2.0.0). Each
    script is told which project to operate on via -SettingsPath,
    rather than inferring it from its own location.
  - This file itself now lives IN Scripts\, alongside every other
    script -- the project root holds only TheFileOrganizer.bat (the
    double-click launcher) and README.txt. On startup, before showing
    the main menu, it runs Test-Installation.ps1 (inward: is the
    toolkit itself intact?) then Install-Dependencies.ps1 (outward:
    does this machine have what the toolkit needs?) -- cheap in the
    steady state, and exactly the right moment to catch a real problem.

Requires:
    Nothing beyond a standard Python install (tkinter is stdlib).

Usage:
    Normally launched via double-clicking TheFileOrganizer.bat at the
    project root, which just runs this file. Can also be run directly:
        python Dashboard.py
"""

import json
import os

# Prevent Python from writing __pycache__ folders into Scripts\ at all.
# These scripts are short-lived, one-shot CLI runs -- the compile-time
# savings bytecode caching provides is negligible against the real work
# (hashing, extraction), so there's no cost to just not creating them.
# Set here so every subprocess this launches (the *.ps1 wrappers, which
# in turn launch the *.py category scripts) inherits it automatically.
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

SCRIPTS_DIR                 = Path(__file__).resolve().parent
ROOT_DIR                    = SCRIPTS_DIR.parent
PROJECTS_DIR                = ROOT_DIR / "Projects"
NEW_PROJECT_SCRIPT          = SCRIPTS_DIR / "New-Project.ps1"
TEST_INSTALLATION_SCRIPT    = SCRIPTS_DIR / "Test-Installation.ps1"
INSTALL_DEPENDENCIES_SCRIPT = SCRIPTS_DIR / "Install-Dependencies.ps1"

# The core duplicate-detection chain -- ALWAYS runs, never optional. It's
# this project's primary objective, and every category script below
# requires the run's inventory CSV, which only exists once this completes.
# Third element: does this script support the -SkipCloudOnly switch?
# (PreliminaryInventory/PotentialDuplicates don't hash anything, so they
# were never given that parameter -- passing it would error out.)
PRE_SCAN_STAGES = [
    ("PreliminaryInventory.ps1", "Preliminary Inventory",        False),
    ("PotentialDuplicates.ps1",  "Finding Potential Duplicates", False),
]

# NOTE: eventually this becomes one of two choices (Duplicate Run / Full
# Run) once Step 9 (Choose Run Type) is built. For now this is the only
# path forward from the Pre-Scan summary screen -- Step 9 will replace
# the "Continue" button below with a real choice, not change what
# actually runs today.
DUPLICATE_HASH_STAGES = [
    ("PartialHash.ps1", "Partial Hash Pass", True),
    ("FullHash.ps1",    "Full Hash Pass",    True),
]

FULL_RUN_STAGES = [
    ("FullHashInventory.ps1", "Full Hash Inventory (every file)", True),
]

# Optional category stages -- shown as checkboxes, run only if checked.
# All of these support -SkipCloudOnly.
CATEGORY_STAGES = [
    ("ImageAnalysis.ps1",     "Images",                "Perceptual hashing for photos/images"),
    ("PDFAnalysis.ps1",       "PDFs",                  "Page count, metadata, encryption status"),
    ("OfficeAnalysis.ps1",    "Office Documents",      "Word / Excel / PowerPoint properties"),
    ("RawImageAnalysis.ps1",  "RAW Camera Images",     "EXIF data from CR2/NEF/ARW/DNG/etc."),
    ("AudioAnalysis.ps1",     "Audio Files",           "Technical + tag metadata (needs ffprobe)"),
    ("VideoAnalysis.ps1",     "Video Files",           "Technical metadata (needs ffprobe)"),
    ("TextFileAnalysis.ps1",  "Text / Markdown Notes", "Word counts, Obsidian tags/links"),
    ("ArchiveAnalysis.ps1",   "Archives (.zip/.7z)",   "Catalogs archive contents"),
    ("ContentExtraction.ps1", "Document Text Content", "Full text extraction, for future search"),
]

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_powershell(script_name, cwd, extra_args=None, timeout=None):
    """Run a PowerShell script as a subprocess and wait for it to finish.
    Returns (success: bool, stdout: str, stderr: str, returncode: int).
    No console window flashes open, since these are launched from a GUI
    app, not a terminal.

    returncode is exposed specifically so callers running a pausable
    script (the hash stages, category stages) can distinguish exit code
    2 ("paused by user request", not an error) from exit code 1 (a real
    failure) -- success alone can't tell those apart, since both are
    "not 0". Callers that don't care can ignore the 4th value.

    timeout (seconds), if given, turns a hang into a clear error instead
    of the dashboard appearing to freeze forever -- used for the
    project-creation call specifically, since that operation is only
    ever file/folder work and should always be fast; a hang there
    almost always means an unexpected interactive prompt, not real work."""
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_name]
    if extra_args:
        cmd.extend(extra_args)
    try:
        result = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            creationflags=_NO_WINDOW, timeout=timeout,
        )
        return result.returncode == 0, result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return False, "", (
            f"Timed out after {timeout}s -- the script may be waiting on "
            f"unexpected input rather than doing real work."
        ), -1
    except Exception as e:
        return False, "", str(e), -1


def get_default_project_name():
    """Mirrors New-Project.ps1's own Get-DefaultProjectName logic exactly,
    so the dashboard can always supply a concrete name up front and never
    rely on that script's Read-Host fallback (which would hang forever
    when run as a non-interactive subprocess)."""
    base_name = "New-Project"
    candidate = base_name
    counter = 2
    while (PROJECTS_DIR / candidate).exists():
        candidate = f"{base_name} ({counter})"
        counter += 1
    return candidate


def get_project_names():
    if not PROJECTS_DIR.exists():
        return []
    return sorted(p.name for p in PROJECTS_DIR.iterdir() if p.is_dir())


def get_settings_path(project_name):
    """Every script now takes this path explicitly via -SettingsPath,
    rather than inferring which project to use from its own location."""
    return PROJECTS_DIR / project_name / "settings.json"


def load_settings(project_name):
    settings_path = get_settings_path(project_name)
    if not settings_path.exists():
        return None
    # utf-8-sig strips a leading BOM if present, and behaves identically
    # to plain utf-8 if not -- Windows PowerShell 5.1's `-Encoding UTF8`
    # always writes a BOM (unlike PowerShell 7), so settings.json needs
    # to be read this way regardless of which script last wrote it.
    with open(settings_path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def format_bytes_display(n):
    """Human-readable byte count for the pre-scan summary screen."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


class Dashboard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("The File Organizer")
        self.geometry("620x720")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.current_project = None
        self.progress_queue = queue.Queue()
        self.scan_thread = None  # tracks the currently running scan thread, if any -- see on_close
        self.scan_is_pausable = False

        self.container = ttk.Frame(self)
        self.container.pack(fill="both", expand=True)

        self.show_startup_check_screen()

    def report_callback_exception(self, exc_type, exc_value, exc_traceback):
        # Tkinter's default behavior for an exception raised inside a
        # button/callback is to print a traceback -- but this dashboard
        # normally launches via pythonw (no console window) once Python
        # is already installed, so that traceback goes nowhere visible.
        # A broken button can fail with literally zero feedback. This is
        # a standard Tkinter override hook: it fires automatically for
        # any callback exception, so every button gets this coverage
        # without needing to be individually wrapped in try/except.
        import traceback
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        print(detail)  # still useful if run via `python Dashboard.py` with a console attached
        messagebox.showerror(
            "Unexpected Error",
            f"Something went wrong:\n\n{exc_value}\n\n"
            f"(Full details were also printed to the console, if one is attached.)",
        )

    def clear_container(self):
        for widget in self.container.winfo_children():
            widget.destroy()

    def on_close(self):
        """Handles the window's X button / Alt+F4 / any WM_DELETE_WINDOW
        close. If a pausable scan is actively running, this pauses it
        properly and WAITS for that to actually happen before the window
        closes -- writing the pause flag alone isn't enough, since the
        background thread is a daemon thread that Python kills without
        ceremony on process exit, but the real work is a separate CHILD
        PROCESS (the PowerShell/Python subprocess actually hashing
        files). On Windows, child processes generally keep running as
        orphans when their parent exits unless something explicitly
        waits for them to stop -- so closing without waiting could leave
        a scan silently running in the background with no window and no
        way to know it's still going."""
        if self.scan_thread and self.scan_thread.is_alive():
            if not self.scan_is_pausable:
                # Pre-Scan/calibration ("create" mode) has no
                # checkpoint/pause support to wait for -- waiting here
                # would just hang the close for however long it takes to
                # finish naturally (a few minutes on real data so far).
                # Honest choice instead of a fake graceful pause.
                close_anyway = messagebox.askyesno(
                    "Scan in progress",
                    "The initial project scan is still running and can't be "
                    "safely paused mid-way.\n\n"
                    "Close anyway? It will need to be started over next time, "
                    "since this stage doesn't save partial progress.\n\n"
                    "Choose 'No' to let it finish first.",
                    icon="warning",
                )
                if close_anyway:
                    self.destroy()
                return
            self._request_pause()
            self._wait_for_pause_then_close()
            return
        self.destroy()

    def _wait_for_pause_then_close(self):
        """Waits briefly for the running script to notice the pause flag
        and exit cleanly at its next checkpoint (normally a few seconds),
        showing a clear message rather than letting the window appear to
        hang. If it's still running after a generous wait -- most likely
        because it's in the middle of one unusually large/slow file --
        offers an honest choice rather than silently either abandoning a
        background process or freezing indefinitely."""
        self.clear_container()
        ttk.Label(self.container, text="Pausing before close...",
                  font=("Segoe UI", 12, "bold")).pack(pady=(80, 10))
        ttk.Label(
            self.container,
            text="Waiting for the current step to reach its next\nsafe checkpoint (usually just a few seconds).",
            font=("Segoe UI", 9), justify="center",
        ).pack()
        self.update_idletasks()  # render the message now, before the blocking wait below

        self.scan_thread.join(timeout=15)

        if self.scan_thread.is_alive():
            close_anyway = messagebox.askyesno(
                "Still finishing",
                "The current file is taking longer than usual to reach a "
                "safe pausing point.\n\n"
                "Close anyway? A background process may keep running "
                "briefly if you do.\n\n"
                "Choose 'No' to keep waiting.",
                icon="warning",
            )
            if close_anyway:
                self.destroy()
                return
            self._wait_for_pause_then_close()
            return

        self.destroy()

    # ------------------------------------------------------------------
    # Screen: Startup Checks (Test-Installation, then Install-Dependencies)
    # ------------------------------------------------------------------
    def show_startup_check_screen(self):
        self.clear_container()
        frame = self.container

        ttk.Label(frame, text="The File Organizer", font=("Segoe UI", 16, "bold")).pack(pady=(60, 10))
        self.startup_status_var = tk.StringVar(value="Checking installation...")
        ttk.Label(frame, textvariable=self.startup_status_var, font=("Segoe UI", 10)).pack(pady=(0, 20))

        self.startup_progress = ttk.Progressbar(frame, orient="horizontal", length=380, mode="indeterminate")
        self.startup_progress.pack(pady=10)
        self.startup_progress.start(12)

        thread = threading.Thread(target=self._run_startup_checks, daemon=True)
        thread.start()
        self.after(150, self._poll_startup_queue)

    def _poll_startup_queue(self):
        try:
            while True:
                item = self.progress_queue.get_nowait()
                kind = item[0]
                if kind == "startup_status":
                    self.startup_status_var.set(item[1])
                elif kind == "startup_ok":
                    self.startup_progress.stop()
                    self.show_main_menu()
                    return
                elif kind == "startup_warning":
                    self.startup_progress.stop()
                    proceed = messagebox.askyesno(
                        "Setup check found a problem",
                        item[1] + "\n\nContinue anyway?",
                    )
                    if proceed:
                        self.show_main_menu()
                    else:
                        self.destroy()
                    return
        except queue.Empty:
            pass
        self.after(150, self._poll_startup_queue)

    def _run_startup_checks(self):
        # 1. Inward check first -- is the toolkit itself intact? No point
        #    checking the environment for scripts that are missing/corrupt.
        self.progress_queue.put(("startup_status", "Checking installation (Test-Installation.ps1)..."))
        success, stdout, stderr, returncode = run_powershell(
            str(TEST_INSTALLATION_SCRIPT), cwd=SCRIPTS_DIR, timeout=60)
        if not success:
            self.progress_queue.put(("startup_warning", (
                "Test-Installation.ps1 found a problem with the toolkit's own files "
                "(missing, empty, or corrupted). See InstallationCheckReport.txt in "
                "the Scripts folder for details."
            )))
            return

        # 2. Outward check -- does this machine have what the toolkit needs?
        self.progress_queue.put(("startup_status", "Checking dependencies (Install-Dependencies.ps1)..."))
        success, stdout, stderr, returncode = run_powershell(
            str(INSTALL_DEPENDENCIES_SCRIPT), cwd=SCRIPTS_DIR, timeout=300)
        if not success:
            self.progress_queue.put(("startup_warning", (
                "Install-Dependencies.ps1 found a required package or tool that "
                "couldn't be installed. See DependencyCheckReport.txt in the "
                "Scripts folder for details."
            )))
            return

        self.progress_queue.put(("startup_ok",))

    # ------------------------------------------------------------------
    # Screen: Main Menu
    # ------------------------------------------------------------------
    def show_main_menu(self):
        self.clear_container()
        frame = self.container

        ttk.Label(frame, text="The File Organizer", font=("Segoe UI", 18, "bold")).pack(pady=(50, 10))
        ttk.Label(frame, text="Inventory. Organize. De-duplicate.", font=("Segoe UI", 10)).pack(pady=(0, 40))

        ttk.Button(frame, text="New Project", width=32, command=self.show_new_project).pack(pady=8)
        ttk.Button(frame, text="Continue Project", width=32, command=self.show_continue_project).pack(pady=8)
        ttk.Button(frame, text="Exit", width=32, command=self.destroy).pack(pady=8)

    # ------------------------------------------------------------------
    # Screen: New Project
    # ------------------------------------------------------------------
    def show_new_project(self):
        self.clear_container()
        frame = self.container

        ttk.Label(frame, text="New Project", font=("Segoe UI", 14, "bold")).pack(pady=(20, 20))

        path_frame = ttk.Frame(frame)
        path_frame.pack(fill="x", padx=30, pady=5)
        ttk.Label(path_frame, text="Folder to inventory:").pack(anchor="w")

        path_row = ttk.Frame(path_frame)
        path_row.pack(fill="x", pady=4)
        self.target_path_var = tk.StringVar()
        ttk.Entry(path_row, textvariable=self.target_path_var, width=45).pack(
            side="left", fill="x", expand=True)
        ttk.Button(path_row, text="Browse...", command=self.browse_folder).pack(side="left", padx=(6, 0))

        name_frame = ttk.Frame(frame)
        name_frame.pack(fill="x", padx=30, pady=(15, 5))
        ttk.Label(name_frame, text="Project name (optional -- leave blank to auto-name):").pack(anchor="w")
        self.project_name_var = tk.StringVar()
        ttk.Entry(name_frame, textvariable=self.project_name_var, width=45).pack(fill="x", pady=4)

        button_row = ttk.Frame(frame)
        button_row.pack(pady=35)
        ttk.Button(button_row, text="< Back", command=self.show_main_menu).pack(side="left", padx=5)
        ttk.Button(button_row, text="Create Project & Start Inventory",
                   command=self.start_new_project).pack(side="left", padx=5)

    def browse_folder(self):
        folder = filedialog.askdirectory(title="Select folder to inventory")
        if folder:
            self.target_path_var.set(folder)

    def start_new_project(self):
        target_path = self.target_path_var.get().strip()
        if not target_path:
            messagebox.showwarning("Missing folder", "Please browse to a folder to inventory first.")
            return
        project_name = self.project_name_var.get().strip()
        self.show_progress_screen(mode="create", target_path=target_path, project_name=project_name)

    # ------------------------------------------------------------------
    # Screen: Continue Project (minimal for now -- full search comes
    # later, once results are consolidated into a queryable database)
    # ------------------------------------------------------------------
    def show_continue_project(self):
        self.clear_container()
        frame = self.container

        ttk.Label(frame, text="Continue Project", font=("Segoe UI", 14, "bold")).pack(pady=(20, 10))

        projects = get_project_names()
        if not projects:
            ttk.Label(frame, text="No projects found yet.").pack(pady=20)
        else:
            ttk.Label(frame, text="Select a project:").pack(pady=(10, 4))
            self.project_listbox = tk.Listbox(frame, height=8, width=45)
            for p in projects:
                self.project_listbox.insert("end", p)
            self.project_listbox.pack(pady=4)

            ttk.Label(
                frame,
                text="(Full search is coming in a future update. Resume Scan and\n"
                     "View Duplicates are shown below but not yet functional --\n"
                     "greyed out deliberately, rather than clickable but broken.)",
                font=("Segoe UI", 8), foreground="gray", justify="center",
            ).pack(pady=(8, 0))

        button_row = ttk.Frame(frame)
        button_row.pack(pady=25)
        ttk.Button(button_row, text="< Back", command=self.show_main_menu).pack(side="left", padx=5)
        if projects:
            ttk.Button(button_row, text="Resume Scan", command=self.resume_scan_for_selected_project).pack(
                side="left", padx=5)
            ttk.Button(button_row, text="View Reports", command=self.view_selected_project_reports).pack(
                side="left", padx=5)
            ttk.Button(button_row, text="Review Reports", command=self.open_selected_project_reports).pack(
                side="left", padx=5)
            ttk.Button(button_row, text="Continue Analysis", command=self.resume_selected_project).pack(
                side="left", padx=5)
            ttk.Button(button_row, text="View Duplicates", state="disabled").pack(side="left", padx=5)

    def _get_selected_project(self):
        selection = self.project_listbox.curselection()
        if not selection:
            messagebox.showinfo("No selection", "Select a project first.")
            return None
        return self.project_listbox.get(selection[0])

    def view_selected_project_reports(self):
        """Opens the inline report viewer for a project selected from the
        Continue Project list -- same viewer the completion screen uses,
        just reached from a different entry point (an existing project
        rather than one that just finished a run)."""
        project_name = self._get_selected_project()
        if not project_name:
            return
        self.current_project = project_name
        self.show_report_viewer()

    def open_selected_project_reports(self):
        project_name = self._get_selected_project()
        if not project_name:
            return
        settings = load_settings(project_name)
        if not settings or not settings.get("CurrentRun"):
            messagebox.showwarning("No run found", "This project doesn't have a completed run yet.")
            return
        reports_folder = PROJECTS_DIR / project_name / "Runs" / settings["CurrentRun"] / "Reports"
        if reports_folder.exists():
            os.startfile(str(reports_folder))
        else:
            messagebox.showwarning("Not found", f"Reports folder not found:\n{reports_folder}")

    def resume_selected_project(self):
        project_name = self._get_selected_project()
        if not project_name:
            return
        settings = load_settings(project_name)
        # Checking LastFullHashScan specifically, not just CurrentRun --
        # since Step 6, CurrentRun gets set right after Pre-Scan alone,
        # before duplicate-hash detection has run. Using the older,
        # broader check here would jump straight to category selection
        # on a project that was only Pre-Scanned, skipping duplicate
        # detection entirely.
        if not settings or not settings.get("LastFullHashScan"):
            messagebox.showwarning(
                "No run found",
                "This project doesn't have a completed duplicate-detection run yet.\n"
                "Use 'New Project' to run the initial inventory first.",
            )
            return
        # Assumes duplicate-hash detection already completed successfully
        # for this project -- jumps straight to picking categories to
        # (re)run.
        self.current_project = project_name
        self.show_category_selection()

    def _detect_resumable_mode(self, project_name):
        """Checks for a leftover checkpoint file from an interrupted run
        to determine what can be resumed. Checks the three core hash
        paths first; if none of those left a checkpoint behind, falls
        back to category-stage resumption using the selection persisted
        by _persist_category_selection and the completion tracking kept
        up to date by _mark_category_completed as each category finishes
        -- deliberately not inferring completion from output-file
        existence, since that would mean hardcoding and trusting an
        exact output filename for all 9 category scripts; Dashboard.py
        already knows firsthand which ones it just ran successfully.

        Returns (mode, stages_to_run) -- stages_to_run is None for the
        two hash modes (they take no extra args) and the remaining
        (script, label, desc) tuples for "categories". Returns
        (None, None) if nothing is resumable."""
        settings = load_settings(project_name)
        if not settings or not settings.get("CurrentRun"):
            return None, None

        run_folder = PROJECTS_DIR / project_name / "Runs" / settings["CurrentRun"]
        logs_folder = run_folder / "Logs"
        inventory_folder = run_folder / "Inventory"

        if logs_folder.exists():
            if (logs_folder / "checkpoint_fullhashinventory_raw.csv").exists():
                return "full_run", None
            if (logs_folder / "checkpoint_partialhash_raw.csv").exists():
                return "duplicate_hash", None
            if (logs_folder / "checkpoint_fullhash_raw.csv").exists():
                return "duplicate_hash", None

        if inventory_folder.exists() and list(inventory_folder.glob("*.checkpoint.csv")):
            selected = settings.get("LastCategorySelection") or []
            completed = set(settings.get("LastCategoryCompleted") or [])
            remaining = [
                (script, label, desc) for (script, label, desc) in CATEGORY_STAGES
                if script in selected and script not in completed
            ]
            if remaining:
                return "categories", remaining

        return None, None

    def resume_scan_for_selected_project(self):
        project_name = self._get_selected_project()
        if not project_name:
            return
        mode, stages_to_run = self._detect_resumable_mode(project_name)
        if not mode:
            messagebox.showinfo(
                "Nothing to resume",
                "No paused or interrupted scan was found for this project.",
            )
            return
        self.current_project = project_name
        if mode == "categories":
            self.show_progress_screen(mode=mode, stages_to_run=stages_to_run)
        else:
            self.show_progress_screen(mode=mode)

    # ------------------------------------------------------------------
    # Screen: Progress (core chain, then optionally category stages)
    # ------------------------------------------------------------------
    def show_progress_screen(self, mode, target_path=None, project_name=None, stages_to_run=None):
        self.clear_container()
        frame = self.container

        ttk.Label(frame, text="Working...", font=("Segoe UI", 14, "bold")).pack(pady=(25, 10))

        self.stage_label_var = tk.StringVar(value="Starting...")
        ttk.Label(frame, textvariable=self.stage_label_var, font=("Segoe UI", 10)).pack(pady=(0, 12))

        self.progress_bar = ttk.Progressbar(frame, orient="horizontal", length=460, mode="determinate")
        self.progress_bar.pack(pady=8)

        self.log_text = tk.Text(frame, height=12, width=64, state="disabled", font=("Consolas", 9))
        self.log_text.pack(pady=15, padx=20)

        if mode == "create":
            thread = threading.Thread(
                target=self._run_new_project_then_core, args=(target_path, project_name), daemon=True)
        elif mode == "duplicate_hash":
            thread = threading.Thread(
                target=self._run_duplicate_hash_stages, args=(), daemon=True)
        elif mode == "full_run":
            thread = threading.Thread(
                target=self._run_full_run_stage, args=(), daemon=True)
        else:  # mode == "categories"
            thread = threading.Thread(
                target=self._run_category_stages, args=(stages_to_run,), daemon=True)
        thread.start()
        self.scan_thread = thread

        # Pause is only offered for modes that actually check for it --
        # Pre-Scan + calibration ("create") are fast enough (a few
        # minutes at most on real data so far) that pausing them wasn't
        # scoped; the hash/category stages are the genuinely
        # long-running ones this exists for. scan_is_pausable is tracked
        # alongside the thread so on_close (closing the window mid-scan)
        # knows whether waiting for a clean pause even makes sense here,
        # or whether it would just hang the close for however long "create"
        # takes to finish on its own -- there's no checkpoint/pause support
        # in PreliminaryInventory.ps1/PotentialDuplicates.ps1 to wait for.
        self.scan_is_pausable = mode in ("duplicate_hash", "full_run", "categories")
        if self.scan_is_pausable:
            ttk.Button(frame, text="Pause", command=self._request_pause).pack(pady=(0, 10))

        self.after(150, self._poll_progress_queue)

    def _request_pause(self):
        """Writes the pause flag file the running script is polling for
        at its own checkpoint-flush points -- see PartialHash.ps1 etc.
        Safe to call from the main thread even while a background thread
        is blocked inside subprocess.run(), since this only touches the
        filesystem, not the subprocess itself."""
        settings = load_settings(self.current_project) or {}
        run = settings.get("CurrentRun")
        if not run:
            return
        logs_folder = PROJECTS_DIR / self.current_project / "Runs" / run / "Logs"
        try:
            logs_folder.mkdir(parents=True, exist_ok=True)
            (logs_folder / "pause_requested.flag").touch()
            self._log("Pause requested -- will stop at the next checkpoint (may take a few seconds).")
        except OSError as e:
            messagebox.showwarning("Could not pause", f"Could not write the pause flag:\n{e}")

    def _log(self, message):
        self.progress_queue.put(("log", message))

    def _stage(self, text, fraction):
        self.progress_queue.put(("stage", text, fraction))

    def _done(self, next_screen):
        self.progress_queue.put(("done", next_screen))

    def _error(self, message):
        self.progress_queue.put(("error", message))

    def _paused(self):
        self.progress_queue.put(("paused", None))

    def _poll_progress_queue(self):
        try:
            while True:
                item = self.progress_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.log_text.configure(state="normal")
                    self.log_text.insert("end", item[1] + "\n")
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                elif kind == "stage":
                    self.stage_label_var.set(item[1])
                    self.progress_bar["value"] = item[2]
                elif kind == "done":
                    if item[1] == "categories":
                        self.show_category_selection()
                    elif item[1] == "choose_run_type":
                        self.show_choose_run_type()
                    else:
                        self.show_completion_screen()
                    return
                elif kind == "error":
                    messagebox.showerror("Error", item[1])
                    self.show_main_menu()
                    return
                elif kind == "paused":
                    self.show_paused_screen()
                    return
        except queue.Empty:
            pass
        self.after(150, self._poll_progress_queue)

    def show_paused_screen(self):
        """Shown when a running script exits with code 2 (paused by user
        request) rather than 0 (success) or 1 (a real error) -- a
        distinct screen, not an error dialog, since nothing went wrong."""
        self.clear_container()
        frame = self.container

        ttk.Label(frame, text="Paused", font=("Segoe UI", 16, "bold")).pack(pady=(60, 10))
        ttk.Label(
            frame,
            text=f"Project: {self.current_project}\n\nProgress has been saved. You can resume this scan\nanytime from Continue Project.",
            font=("Segoe UI", 10), justify="center",
        ).pack(pady=(0, 35))

        ttk.Button(frame, text="Back to Main Menu", width=32, command=self.show_main_menu).pack(pady=6)

    def _run_new_project_then_core(self, target_path, project_name):
        total_steps = 1 + len(PRE_SCAN_STAGES) + 1  # +1 project creation, +1 calibration
        step = 0

        self._stage(f"Step {step + 1} of {total_steps}: Creating project...", (step / total_steps) * 100)
        self._log(f"Creating project for: {target_path}")

        # Always resolve to a concrete, non-empty name BEFORE calling the
        # script -- never let New-Project.ps1 fall back to its own
        # Read-Host prompt, which would hang forever with no console
        # attached to answer it.
        if not project_name:
            project_name = get_default_project_name()
            self._log(f"No name entered -- using default: {project_name}")

        args = [target_path, project_name]

        success, stdout, stderr, returncode = run_powershell(
            str(NEW_PROJECT_SCRIPT), cwd=SCRIPTS_DIR, extra_args=args, timeout=120)
        if not success:
            self._error(f"Failed to create project.\n\n{stderr[:500]}")
            return

        created_name = project_name
        self.current_project = created_name
        self._log(f"Project created: {created_name}")
        step += 1

        settings_path = str(get_settings_path(created_name))

        for script_name, label, supports_skip_cloud in PRE_SCAN_STAGES:
            self._stage(f"Step {step + 1} of {total_steps}: {label}...", (step / total_steps) * 100)
            self._log(f"Running {script_name}...")

            extra_args = ["-SettingsPath", settings_path]
            if supports_skip_cloud:
                extra_args.append("-SkipCloudOnly")

            success, stdout, stderr, returncode = run_powershell(script_name, cwd=SCRIPTS_DIR, extra_args=extra_args)
            if not success:
                self._error(f"{label} failed -- stopping here since later steps depend on it.\n\n{stderr[:500]}")
                return
            self._log("  done.")
            step += 1

        # Calibration folded directly into this same sequence, rather than
        # behind a separate "Continue" click. A vague button leading to a
        # second screen that LOOKED like active scanning (same progress UI
        # as a real multi-hour run) was genuinely alarming with no way to
        # tell "this is a 2-second sample" from "this is starting the big
        # scan". One continuous, clearly-labeled sequence with nothing
        # ambiguous in between fixes that directly.
        self._stage(f"Step {step + 1} of {total_steps}: Preparing time estimates...", (step / total_steps) * 100)
        self._log("Running TimeEstimates.ps1...")
        success, stdout, stderr, returncode = run_powershell(
            "TimeEstimates.ps1", cwd=SCRIPTS_DIR, extra_args=["-SettingsPath", settings_path])
        if not success:
            self._log(f"  WARNING: Calibration failed, proceeding without estimates: {stderr[:200]}")
        else:
            self._log("  done.")

        self._stage("Pre-Scan complete.", 100)
        self._done("choose_run_type")

    def _run_duplicate_hash_stages(self):
        """Runs PartialHash.ps1 + FullHash.ps1 on the current project --
        one of the two choices on the Pre-Scan / Choose Run Type screen."""
        settings_path = str(get_settings_path(self.current_project))
        total_steps = len(DUPLICATE_HASH_STAGES)
        step = 0

        for script_name, label, supports_skip_cloud in DUPLICATE_HASH_STAGES:
            self._stage(f"Step {step + 1} of {total_steps}: {label}...", (step / total_steps) * 100)
            self._log(f"Running {script_name}...")

            extra_args = ["-SettingsPath", settings_path]
            if supports_skip_cloud:
                extra_args.append("-SkipCloudOnly")

            success, stdout, stderr, returncode = run_powershell(script_name, cwd=SCRIPTS_DIR, extra_args=extra_args)
            if returncode == 2:
                self._paused()
                return
            if not success:
                self._error(f"{label} failed -- stopping here since later steps depend on it.\n\n{stderr[:500]}")
                return
            self._log("  done.")
            step += 1

        self._stage("Duplicate detection complete.", 100)
        self._done("categories")

    def _run_full_run_stage(self):
        """Runs FullHashInventory.ps1 -- the Full Run path from Choose Run
        Type, parallel to _run_duplicate_hash_stages for Duplicate Run."""
        settings_path = str(get_settings_path(self.current_project))
        total_steps = len(FULL_RUN_STAGES)
        step = 0

        for script_name, label, supports_skip_cloud in FULL_RUN_STAGES:
            self._stage(f"Step {step + 1} of {total_steps}: {label}...", (step / total_steps) * 100)
            self._log(f"Running {script_name}...")

            extra_args = ["-SettingsPath", settings_path]
            if supports_skip_cloud:
                extra_args.append("-SkipCloudOnly")

            success, stdout, stderr, returncode = run_powershell(script_name, cwd=SCRIPTS_DIR, extra_args=extra_args)
            if returncode == 2:
                self._paused()
                return
            if not success:
                self._error(f"{label} failed.\n\n{stderr[:500]}")
                return
            self._log("  done.")
            step += 1

        self._stage("Full Run complete.", 100)
        self._done("categories")

    def _run_category_stages(self, stages_to_run):
        settings_path = str(get_settings_path(self.current_project))
        total_steps = len(stages_to_run)

        if total_steps == 0:
            self._done("complete")
            return

        for i, (script_name, label, _desc) in enumerate(stages_to_run):
            self._stage(f"Step {i + 1} of {total_steps}: {label}...", (i / total_steps) * 100)
            self._log(f"Running {script_name}...")

            success, stdout, stderr, returncode = run_powershell(
                script_name, cwd=SCRIPTS_DIR, extra_args=["-SettingsPath", settings_path, "-SkipCloudOnly"])
            if returncode == 2:
                # Unlike a per-category failure (which correctly continues
                # to the next one, since categories are independent), a
                # pause is a whole-session signal -- stop here, not just
                # skip to the next category.
                self._paused()
                return
            if not success:
                # Category stages are independent of each other -- one
                # failing shouldn't stop the rest from running.
                self._log(f"  WARNING: {label} failed -- continuing with remaining categories.")
                self._log(f"  {stderr[:300]}")
            else:
                self._log("  done.")
                self._mark_category_completed(script_name)

        self._stage("All selected categories complete.", 100)
        self._done("complete")

    # ------------------------------------------------------------------
    # Screen: Pre-Scan Summary
    # ------------------------------------------------------------------
    def open_prescan_reports(self):
        """Opens both Pre-Scan reports (PreliminaryReport.txt and
        PotentialDuplicatesReport.txt) -- "Pre-Scan" is explicitly both
        scripts together (Step 6), so both get opened, not just one.
        Any failure here surfaces as a real dialog automatically via
        report_callback_exception, not silently."""
        settings = load_settings(self.current_project) or {}
        run = settings.get("CurrentRun")
        if not run:
            messagebox.showwarning("Not found", "Could not locate this project's reports.")
            return
        reports_folder = PROJECTS_DIR / self.current_project / "Runs" / run / "Reports"
        report_paths = [
            reports_folder / "PreliminaryReport.txt",
            reports_folder / "PotentialDuplicatesReport.txt",
        ]
        opened_any = False
        missing = []
        for report_path in report_paths:
            if report_path.exists():
                os.startfile(str(report_path))
                opened_any = True
            else:
                missing.append(report_path.name)
        if not opened_any:
            messagebox.showwarning("Not found", f"Reports not found in:\n{reports_folder}")
        elif missing:
            messagebox.showinfo("Partially found", f"Opened what was available. Missing: {', '.join(missing)}")

    # ------------------------------------------------------------------
    # Screen: Pre-Scan Complete + Choose Run Type (one screen -- no
    # intermediate "Continue" step between seeing the numbers and making
    # the actual choice; see _run_new_project_then_core for why)
    # ------------------------------------------------------------------
    def show_choose_run_type(self):
        self.clear_container()
        frame = self.container

        settings = load_settings(self.current_project) or {}

        ttk.Label(frame, text="Pre-Scan Complete", font=("Segoe UI", 13, "bold")).pack(pady=(12, 2))
        ttk.Label(frame, text=f"Project: {self.current_project}",
                  font=("Segoe UI", 9), foreground="gray").pack(pady=(0, 8))

        info_frame = ttk.Frame(frame)
        info_frame.pack(padx=30, fill="x")

        def add_row(label, value):
            row = ttk.Frame(info_frame)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=label, width=30, anchor="w").pack(side="left")
            ttk.Label(row, text=str(value), font=("Segoe UI", 9, "bold")).pack(side="left")

        file_count = settings.get("LastPreliminaryFileCount")
        error_count = settings.get("LastPreliminaryErrorCount") or 0
        total_bytes = settings.get("LastPreliminaryTotalBytes") or 0
        long_path_count = settings.get("LastPreliminaryLongPathCount") or 0
        drive_type = settings.get("TargetDriveType") or "Unknown"
        candidate_count = settings.get("LastPotentialDuplicatesCandidateCount")
        group_count = settings.get("LastPotentialDuplicatesGroupCount")
        max_reclaim = settings.get("LastPotentialDuplicatesMaxReclaim") or 0

        total_size_display = format_bytes_display(total_bytes)
        max_reclaim_display = format_bytes_display(max_reclaim)
        file_count_display = f"{file_count:,}" if isinstance(file_count, int) else (file_count or "?")
        candidate_count_display = f"{candidate_count:,}" if isinstance(candidate_count, int) else (candidate_count or "?")
        group_count_display = f"{group_count:,}" if isinstance(group_count, int) else (group_count or "?")

        add_row("Files scanned:", file_count_display)
        add_row("Total size:", total_size_display)
        add_row("Drive type (detected):", drive_type)
        add_row("Errors (couldn't access):", error_count)
        add_row("Long paths (>260 chars):", long_path_count)
        add_row("Potential duplicate candidates:", candidate_count_display)
        add_row("Size-groups:", group_count_display)
        add_row("Max reclaimable space:", max_reclaim_display)

        if error_count:
            ttk.Label(
                frame,
                text=f"({error_count} file(s) could not be accessed -- see PreliminaryReport.txt for details)",
                font=("Segoe UI", 8), foreground="gray", justify="center",
            ).pack(pady=(6, 0))

        def copy_summary():
            # ttk.Label text isn't selectable/copyable by the OS the way
            # an Entry or Text widget's content is -- this button exists
            # specifically so the numbers on this screen can be copied
            # out (e.g. to paste elsewhere), without redesigning the
            # whole screen around a text widget just for this.
            summary_text = (
                f"Pre-Scan Complete -- Project: {self.current_project}\n"
                f"Files scanned: {file_count_display}\n"
                f"Total size: {total_size_display}\n"
                f"Drive type (detected): {drive_type}\n"
                f"Errors (couldn't access): {error_count}\n"
                f"Long paths (>260 chars): {long_path_count}\n"
                f"Potential duplicate candidates: {candidate_count_display}\n"
                f"Size-groups: {group_count_display}\n"
                f"Max reclaimable space: {max_reclaim_display}\n"
            )
            self.clipboard_clear()
            self.clipboard_append(summary_text)

        small_button_row = ttk.Frame(frame)
        small_button_row.pack(pady=(8, 4))
        ttk.Button(small_button_row, text="Copy Summary to Clipboard", command=copy_summary).pack(side="left", padx=4)
        ttk.Button(small_button_row, text="Pre-Scan Report", command=self.open_prescan_reports).pack(side="left", padx=4)

        ttk.Separator(frame, orient="horizontal").pack(fill="x", padx=30, pady=10)

        ttk.Label(frame, text="Choose Run Type", font=("Segoe UI", 12, "bold")).pack(pady=(0, 2))
        dup_estimate = settings.get("DuplicateRunEstimateText") or "estimate unavailable"
        full_estimate = settings.get("FullRunEstimateText") or "estimate unavailable"
        ttk.Label(
            frame,
            text="Estimates are deliberately conservative, based on a live sample of\n"
                 "this run's own data -- biased toward taking longer than expected.",
            font=("Segoe UI", 8), foreground="gray", justify="center",
        ).pack(pady=(0, 8))

        option_frame = ttk.Frame(frame)
        option_frame.pack(padx=30, pady=2, fill="x")

        dup_box = ttk.LabelFrame(option_frame, text="Duplicate Run")
        dup_box.pack(fill="x", pady=5, ipady=3)
        ttk.Label(
            dup_box,
            text="Partial + full hash, only on files sharing a size with\nanother file. The standard, faster path.",
            font=("Segoe UI", 9), justify="left",
        ).pack(padx=10, pady=(5, 3), anchor="w")
        ttk.Label(dup_box, text=f"Estimated time: {dup_estimate}", font=("Segoe UI", 9, "bold")).pack(padx=10, anchor="w")
        ttk.Button(
            dup_box, text="Run Duplicate Run",
            command=lambda: self.show_progress_screen(mode="duplicate_hash"),
        ).pack(padx=10, pady=(5, 6), anchor="w")

        full_box = ttk.LabelFrame(option_frame, text="Full Run")
        full_box.pack(fill="x", pady=5, ipady=3)
        ttk.Label(
            full_box,
            text="Full hash on EVERY file, no tiering -- a complete,\nunconditional hash record. Slower, more thorough.",
            font=("Segoe UI", 9), justify="left",
        ).pack(padx=10, pady=(5, 3), anchor="w")
        ttk.Label(full_box, text=f"Estimated time: {full_estimate}", font=("Segoe UI", 9, "bold")).pack(padx=10, anchor="w")
        ttk.Button(
            full_box, text="Run Full Run",
            command=lambda: self.show_progress_screen(mode="full_run"),
        ).pack(padx=10, pady=(5, 6), anchor="w")

        ttk.Button(frame, text="< Back to Main Menu", command=self.show_main_menu).pack(pady=10)

    # ------------------------------------------------------------------
    # Screen: Category Selection
    # ------------------------------------------------------------------
    def show_category_selection(self):
        self.clear_container()
        frame = self.container

        ttk.Label(frame, text="Choose What to Analyze", font=("Segoe UI", 14, "bold")).pack(pady=(20, 4))
        ttk.Label(frame, text="Uncheck anything you don't need this time to save time.",
                  font=("Segoe UI", 9), foreground="gray").pack(pady=(0, 15))

        self.category_vars = {}
        checklist_frame = ttk.Frame(frame)
        checklist_frame.pack(padx=30, fill="x")

        for script_name, label, desc in CATEGORY_STAGES:
            var = tk.BooleanVar(value=True)
            self.category_vars[script_name] = var
            row = ttk.Frame(checklist_frame)
            row.pack(fill="x", pady=2)
            ttk.Checkbutton(row, text=label, variable=var, width=20).pack(side="left")
            ttk.Label(row, text=desc, font=("Segoe UI", 8), foreground="gray").pack(side="left", padx=(10, 0))

        button_row = ttk.Frame(frame)
        button_row.pack(pady=25)
        ttk.Button(button_row, text="Skip All / Finish", command=lambda: self.run_selected_categories([])).pack(
            side="left", padx=5)
        ttk.Button(button_row, text="Run Selected", command=self.run_selected_categories_from_checkboxes).pack(
            side="left", padx=5)

    def run_selected_categories_from_checkboxes(self):
        selected = [
            (script, label, desc) for (script, label, desc) in CATEGORY_STAGES
            if self.category_vars[script].get()
        ]
        self.run_selected_categories(selected)

    def run_selected_categories(self, stages_to_run):
        # Persist what was actually selected, and reset completion
        # tracking to empty -- this is always a FRESH start (whether from
        # "Run Selected" or "Skip All/Finish"), not a resume, so any
        # stale tracking from a previous, different selection must not
        # carry over. Resume Scan reads these two fields back later to
        # know exactly what was originally chosen and what already
        # finished, without needing to guess at each category's output
        # filename.
        self._persist_category_selection(stages_to_run)
        self.show_progress_screen(mode="categories", stages_to_run=stages_to_run)

    def _persist_category_selection(self, stages_to_run):
        settings = load_settings(self.current_project) or {}
        if not settings.get("CurrentRun"):
            return
        settings["LastCategorySelection"] = [s[0] for s in stages_to_run]
        settings["LastCategoryCompleted"] = []
        settings_path = get_settings_path(self.current_project)
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)

    def _mark_category_completed(self, script_name):
        """Records that this category script finished successfully, so
        Resume Scan can skip it later if a LATER category in the same
        selection gets paused or interrupted -- avoids wastefully
        redoing already-finished work. Matters most for
        ContentExtraction, by far the slowest category -- redoing hours
        of already-complete work because a fast category paused
        afterward would be a real, not theoretical, cost."""
        settings = load_settings(self.current_project) or {}
        completed = settings.get("LastCategoryCompleted") or []
        if script_name not in completed:
            completed.append(script_name)
        settings["LastCategoryCompleted"] = completed
        settings_path = get_settings_path(self.current_project)
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)

    # ------------------------------------------------------------------
    # Screen: Completion
    # ------------------------------------------------------------------
    def show_completion_screen(self):
        self.clear_container()
        frame = self.container

        ttk.Label(frame, text="All Done!", font=("Segoe UI", 16, "bold")).pack(pady=(60, 10))
        ttk.Label(frame, text=f"Project: {self.current_project}", font=("Segoe UI", 10)).pack(pady=(0, 35))

        ttk.Button(frame, text="View Reports", width=32, command=self.show_report_viewer).pack(pady=6)
        ttk.Button(frame, text="Open Reports Folder", width=32, command=self.open_current_reports).pack(pady=6)
        ttk.Button(frame, text="Back to Main Menu", width=32, command=self.show_main_menu).pack(pady=6)

    def open_current_reports(self):
        settings = load_settings(self.current_project)
        if not settings or not settings.get("CurrentRun"):
            messagebox.showwarning("Not found", "Could not locate the reports folder.")
            return
        reports_folder = PROJECTS_DIR / self.current_project / "Runs" / settings["CurrentRun"] / "Reports"
        if reports_folder.exists():
            os.startfile(str(reports_folder))
        else:
            messagebox.showwarning("Not found", f"Reports folder not found:\n{reports_folder}")

    # ------------------------------------------------------------------
    # Screen: Report Viewer (Step 11 -- reports are saved and openable
    # externally already; this adds viewing them without leaving the app)
    # ------------------------------------------------------------------
    def show_report_viewer(self):
        self.clear_container()
        frame = self.container

        settings = load_settings(self.current_project) or {}
        run = settings.get("CurrentRun")
        if not run:
            messagebox.showwarning("Not found", "Could not locate this project's reports.")
            self.show_main_menu()
            return

        reports_folder = PROJECTS_DIR / self.current_project / "Runs" / run / "Reports"
        if not reports_folder.exists():
            messagebox.showwarning("Not found", f"Reports folder not found:\n{reports_folder}")
            self.show_main_menu()
            return

        report_files = sorted(reports_folder.glob("*.txt"))

        ttk.Label(frame, text="View Reports", font=("Segoe UI", 14, "bold")).pack(pady=(15, 4))
        ttk.Label(frame, text=f"Project: {self.current_project}",
                  font=("Segoe UI", 9), foreground="gray").pack(pady=(0, 10))

        if not report_files:
            ttk.Label(frame, text="No reports found yet for this run.").pack(pady=20)
            ttk.Button(frame, text="< Back to Main Menu", command=self.show_main_menu).pack(pady=15)
            return

        self.report_listbox = tk.Listbox(frame, height=10, width=50)
        for rf in report_files:
            self.report_listbox.insert("end", rf.name)
        self.report_listbox.pack(pady=4)

        def open_selected():
            selection = self.report_listbox.curselection()
            if not selection:
                messagebox.showinfo("No selection", "Select a report first.")
                return
            selected_name = self.report_listbox.get(selection[0])
            self.show_report_content(reports_folder / selected_name)

        button_row = ttk.Frame(frame)
        button_row.pack(pady=20)
        ttk.Button(button_row, text="< Back to Main Menu", command=self.show_main_menu).pack(side="left", padx=5)
        ttk.Button(button_row, text="View Selected Report", command=open_selected).pack(side="left", padx=5)

    def show_report_content(self, report_path):
        """Displays a single report's text content inline. Uses a Text
        widget rather than a Label -- unlike ttk.Label (see the Pre-Scan
        Complete screen's "Copy Summary" button, added specifically to
        work around this), Text widget content IS natively selectable
        and copyable by the user, so no separate copy button is needed
        here."""
        self.clear_container()
        frame = self.container

        ttk.Label(frame, text=report_path.name, font=("Segoe UI", 12, "bold")).pack(pady=(10, 6))

        text_frame = ttk.Frame(frame)
        text_frame.pack(fill="both", expand=True, padx=15, pady=5)

        scrollbar = ttk.Scrollbar(text_frame, orient="vertical")
        text_widget = tk.Text(text_frame, wrap="word", yscrollcommand=scrollbar.set, font=("Consolas", 9))
        scrollbar.config(command=text_widget.yview)
        scrollbar.pack(side="right", fill="y")
        text_widget.pack(side="left", fill="both", expand=True)

        try:
            content = report_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            content = f"Could not read report:\n{e}"

        text_widget.insert("1.0", content)
        text_widget.config(state="disabled")  # read-only, but still selectable/copyable

        button_row = ttk.Frame(frame)
        button_row.pack(pady=10)
        ttk.Button(button_row, text="< Back to Report List", command=self.show_report_viewer).pack(side="left", padx=5)
        ttk.Button(button_row, text="Open in Notepad",
                   command=lambda: os.startfile(str(report_path))).pack(side="left", padx=5)


if __name__ == "__main__":
    app = Dashboard()
    app.mainloop()
