#!/usr/bin/env python3
r"""
Part of: The File Organizer
Version: B6.1

Tkinter dashboard for the database-backed Phase 1 runtime.

Dashboard owns user interaction and stage-level progress. RunCoordinator owns
execution, persistence, run/stage state, logs, and database-backed engines for
inventory, hashing, duplicate detection and analyzers. PowerShell remains only
for Windows bootstrap/installation helpers and the double-click launch path;
it is not a parallel data-processing pipeline.

Historical stage keys such as ``PreliminaryInventory.ps1`` remain stable in
run history for compatibility, but the ``command`` field records the Python
engine that actually executed the work.

On startup the dashboard runs Test-Installation.ps1 (package integrity) and
Install-Dependencies.ps1 (runtime dependencies) before opening the main menu.

Requires: Python 3.11+ with tkinter.
"""

import json
import os

# Prevent Python from writing __pycache__ folders into the shipped Scripts\ tree.
# Runtime work is dominated by filesystem analysis, and keeping the installation
# byte-stable makes package-integrity checks and portable use simpler.
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

# RunCoordinator is the supported execution/persistence boundary. The import is
# defensive only so startup can report a useful diagnostic if the installation
# is damaged; there is no parallel Alpha/PowerShell data-processing fallback.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import RunCoordinator as fo_coordinator
except Exception as _coordinator_import_error:  # pragma: no cover
    fo_coordinator = None
    _COORDINATOR_IMPORT_ERROR = _coordinator_import_error
else:
    _COORDINATOR_IMPORT_ERROR = None

SCRIPTS_DIR                 = Path(__file__).resolve().parent
ROOT_DIR                    = SCRIPTS_DIR.parent
PROJECTS_DIR                = ROOT_DIR / "Projects"
TEST_INSTALLATION_SCRIPT    = SCRIPTS_DIR / "Test-Installation.ps1"
INSTALL_DEPENDENCIES_SCRIPT = SCRIPTS_DIR / "Install-Dependencies.ps1"

# Stable stage identities for the required pre-scan/duplicate chain. The names
# are historical keys in run records; they are not executable script paths.
# Downstream stages read SQLite current state, never an inventory CSV input.
PRE_SCAN_STAGES = [
    ("PreliminaryInventory.ps1", "Preliminary Inventory"),
    ("PotentialDuplicates.ps1",  "Finding Potential Duplicates"),
]


#: The stage key stays "PreliminaryInventory.ps1" even when the Python
#: engine runs it. It is the accepted identity in every R2-R6 run
#: record and the key STAGE_EXPECTED_OUTPUT and STAGE_ROLES are indexed
#: by; renaming it would change accepted run history to no purpose. The
#: stage's `command` records which engine actually executed, so the
#: record is unambiguous about what ran.
INVENTORY_STAGE_KEY = "PreliminaryInventory.ps1"



#: Stage keys stay as the PowerShell script names even when Python does
#: the work. They are the accepted identity in every R2-R6 run record
#: and the keys STAGE_EXPECTED_OUTPUT and STAGE_ROLES are indexed by.
#: Renaming them would rewrite accepted run history to no purpose; the
#: stage's `command` records which engine actually executed. Stage-key
#: cleanup belongs after the PowerShell runtime path is retired.
SIZE_CANDIDATE_STAGE_KEY = "PotentialDuplicates.ps1"
PARTIAL_HASH_STAGE_KEY   = "PartialHash.ps1"
FULL_HASH_STAGE_KEY      = "FullHash.ps1"
FULL_RUN_STAGE_KEY       = "FullHashInventory.ps1"
TIME_ESTIMATES_STAGE_KEY = "TimeEstimates.ps1"
NEW_PROJECT_STAGE_KEY    = "New-Project.ps1"

# Optional category stages are shown as checkboxes and run only when selected.
# Their stage keys remain historical `.ps1` names for run-history continuity;
# RunCoordinator executes the in-process Python analyzers.
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


# ----------------------------------------------------------------------
# Beta R2 -- application-level logging helpers
#
# Everything below degrades to a no-op when the coordinator modules are
# unavailable, so no call site needs to guard.
# ----------------------------------------------------------------------

def get_app_log():
    r"""The application log at TheFileOrganizer\Logs\app.log, or None.

    This is the log for failures that have no run and no project to
    attach themselves to -- which is exactly the class of failure that
    is hardest to explain after the fact.
    """
    if fo_coordinator is None:
        return None
    try:
        return fo_coordinator.fo_log.get_app_log(str(ROOT_DIR))
    except Exception:
        return None


def app_log_write(severity, message):
    log = get_app_log()
    if log is None:
        return False
    try:
        return log.log(severity, message)
    except Exception:
        return False


def make_coordinator(project_name=None):
    """A RunCoordinator for this project, or None if R2 is unavailable."""
    if fo_coordinator is None:
        return None
    try:
        return fo_coordinator.RunCoordinator(str(ROOT_DIR), project_name)
    except Exception as exc:
        app_log_write("ERROR", f"Could not create a run coordinator: {exc}")
        return None


class _NullStage:
    """Stand-in stage handle used when run recording is unavailable.

    Accepts every call a real StageHandle does and does nothing, so the
    scan methods below read identically whether or not R2's modules
    loaded. Without it, every stage would need a conditional wrapped
    around it and the Alpha control flow -- which must be preserved
    exactly -- would become hard to verify by eye.
    """

    run_stage_id = None
    status = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def record(self, *args, **kwargs):
        return None

    def record_skipped(self, *args, **kwargs):
        return None

    def record_failure(self, *args, **kwargs):
        return None

    def add_event(self, *args, **kwargs):
        return None


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

        # Beta R2: the coordinator for the scan currently running, so
        # on_close can record an abandoned run immediately rather than
        # leaving it for the next launch's reconciliation pass.
        self.active_coordinator = None

        app_log = get_app_log()
        if app_log is not None:
            app_log.session_start(
                fo_coordinator.APP_VERSION,
                extra={"App root": str(ROOT_DIR),
                       "Dashboard": "3.5.0"})
            if app_log.degraded:
                # Nothing can be done about it and it must not stop the
                # application -- but say so on the console, which is
                # attached when run via `python Dashboard.py`.
                print(f"WARNING: app.log is not writable: {app_log.degraded_reason}")
        elif _COORDINATOR_IMPORT_ERROR is not None:
            print(f"WARNING: run logging is unavailable: {_COORDINATOR_IMPORT_ERROR}")

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
        # Beta R2: an unhandled callback exception is an application-level
        # failure with no run to attach itself to. app.log is where those
        # go -- and it exists whether or not a project has been opened.
        app_log_write("CRITICAL", "Unhandled exception in a UI callback:\n" + detail)
        messagebox.showerror(
            "Unexpected Error",
            f"Something went wrong:\n\n{exc_value}\n\n"
            f"(Full details were also printed to the console, if one is attached.)",
        )

    def clear_container(self):
        for widget in self.container.winfo_children():
            widget.destroy()

    def _abandon_active_run(self, reason):
        """Beta R2: close an in-flight run record on the way out.

        Reconciliation on the next launch would catch this anyway, but
        recording it now means the database is accurate immediately
        instead of only after the application is next started.
        """
        coordinator = self.active_coordinator
        self.active_coordinator = None
        if coordinator is None:
            return
        try:
            coordinator.abandon(reason)
        except Exception as exc:
            app_log_write("WARNING", f"Could not close the in-flight run record: {exc}")

    def destroy(self):
        # Every close path in this class routes through destroy(), so
        # this is the one place that reliably sees the window going away
        # -- including the "close anyway" answers below.
        self._abandon_active_run(
            "The application was closed while this run was in progress.")
        log = get_app_log()
        if log is not None:
            try:
                log.session_end()
            except Exception:
                pass
        super().destroy()

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
        # 0. Beta R2: reconcile runs that a previous session left marked
        #    running. Done here, on the existing startup thread, because
        #    this screen is already the moment the application takes to
        #    check itself -- and "was the last session interrupted?" is
        #    the same kind of question as "is the toolkit intact?".
        #
        #    Each project database is opened on its own, in turn, through
        #    fo_db.open_project, so the isolation guard applies to every
        #    one and no project is ever open alongside another. This also
        #    applies the 001 -> 002 migration to existing projects on the
        #    first R2 launch.
        #
        #    Never fatal: a project that cannot be checked is logged and
        #    skipped, and startup carries on.
        if fo_coordinator is not None:
            self.progress_queue.put(("startup_status", "Checking for interrupted runs..."))
            try:
                results = fo_coordinator.reconcile_all_projects(str(ROOT_DIR))
                if results["runs_reconciled"]:
                    app_log_write("INFO", (
                        f"Startup reconciliation recorded "
                        f"{len(results['runs_reconciled'])} interrupted run(s)."))
            except Exception as exc:
                app_log_write("WARNING", f"Stale-run reconciliation failed: {exc}")

        # 1. Inward check first -- is the toolkit itself intact? No point
        #    checking the environment for scripts that are missing/corrupt.
        self.progress_queue.put(("startup_status", "Checking installation (Test-Installation.ps1)..."))
        success, stdout, stderr, returncode = run_powershell(
            str(TEST_INSTALLATION_SCRIPT), cwd=SCRIPTS_DIR, timeout=60)
        if not success:
            # An application-level failure, before any project or run
            # exists. app.log is the only place it can be recorded.
            app_log_write("ERROR", (
                f"Test-Installation.ps1 failed (exit {returncode}). "
                f"{(stderr or stdout or '').strip()[:800]}"))
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
            app_log_write("ERROR", (
                f"Install-Dependencies.ps1 failed (exit {returncode}). "
                f"{(stderr or stdout or '').strip()[:800]}"))
            self.progress_queue.put(("startup_warning", (
                "Install-Dependencies.ps1 found a required package or tool that "
                "couldn't be installed. See DependencyCheckReport.txt in the "
                "Scripts folder for details."
            )))
            return

        app_log_write("INFO", "Startup checks passed.")
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

        # B4.3: a project may cover more than one source folder. ONE
        # folder stays the ordinary case -- the list simply has one row
        # in it, and the user never has to think about the feature.
        path_frame = ttk.Frame(frame)
        path_frame.pack(fill="x", padx=30, pady=5)
        ttk.Label(path_frame, text="Folder(s) to inventory:").pack(anchor="w")

        path_row = ttk.Frame(path_frame)
        path_row.pack(fill="x", pady=4)
        self.target_path_var = tk.StringVar()
        ttk.Entry(path_row, textvariable=self.target_path_var, width=45).pack(
            side="left", fill="x", expand=True)
        ttk.Button(path_row, text="Browse...",
                   command=self.browse_folder).pack(side="left", padx=(6, 0))
        ttk.Button(path_row, text="Add",
                   command=self.add_source_folder).pack(side="left", padx=(6, 0))

        list_row = ttk.Frame(path_frame)
        list_row.pack(fill="x", pady=(4, 0))
        self.source_roots_list = tk.Listbox(list_row, height=4,
                                            selectmode="browse")
        self.source_roots_list.pack(side="left", fill="x", expand=True)
        ttk.Button(list_row, text="Remove",
                   command=self.remove_source_folder).pack(side="left",
                                                           padx=(6, 0),
                                                           anchor="n")
        ttk.Label(path_frame,
                  text="Add a second folder only if you want one project to "
                       "cover both.",
                  foreground="#666").pack(anchor="w", pady=(2, 0))
        self.source_roots = []

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

    def add_source_folder(self):
        r"""Move the typed/browsed folder into the list.

        Validated here rather than at creation time so a mistyped or
        unplugged folder is refused while the user is still looking at
        it, instead of after a project folder has been made.
        """
        folder = self.target_path_var.get().strip()
        if not folder:
            messagebox.showwarning("Nothing to add",
                                   "Browse to a folder first.")
            return
        ok, title, message = self.validate_target_folder(folder)
        if not ok:
            messagebox.showerror(title, message)
            return
        if self._equivalent_root_index(folder) is not None:
            messagebox.showwarning(
                "Already added",
                "That folder is already in the list:\n\n%s" % folder)
            return
        self.source_roots.append(folder)
        self.source_roots_list.insert("end", folder)
        self.target_path_var.set("")

    def remove_source_folder(self):
        selection = self.source_roots_list.curselection()
        if not selection:
            messagebox.showwarning("Nothing selected",
                                   "Select a folder in the list to remove it.")
            return
        index = selection[0]
        self.source_roots_list.delete(index)
        del self.source_roots[index]

    def _equivalent_root_index(self, folder):
        r"""Index of an already-added root equivalent to `folder`.

        Compared with the same normalisation the database uses for
        source_root identity, so C:\Photos and C:\photos\ are caught
        here as one folder -- which is far kinder than discovering it
        later as a doubled inventory.
        """
        import fo_db
        import win_meta
        key = fo_db.path_key(win_meta.normalize_root(folder))
        for index, existing in enumerate(self.source_roots):
            if fo_db.path_key(win_meta.normalize_root(existing)) == key:
                return index
        return None

    def _collect_source_roots(self):
        """The roots to create the project with, list plus typed entry."""
        roots = list(self.source_roots)
        typed = self.target_path_var.get().strip()
        if typed and self._equivalent_root_index(typed) is None:
            roots.append(typed)
        return roots

    def validate_target_folder(self, target_path):
        r"""Check the folder BEFORE anything is created. Returns
        (ok, title, message).

        Beta R5 cleanup. Previously a mistyped folder reached
        New-Project.ps1, which correctly refused it -- but the user saw
        only a generic "Failed to create project", which describes the
        symptom rather than the cause and offers nothing to act on.

        The two failure modes are told apart deliberately. "Not found"
        and "cannot be accessed" call for completely different responses
        from the user -- retype the path, versus check permissions or
        plug the drive in -- and reporting a permissions problem as a
        missing folder sends them to look for something that is right
        where they left it.

        This does NOT replace the PowerShell-side check in
        New-Project.ps1, which remains as defence in depth: the
        dashboard is not the only way that script can be invoked.
        """
        if not os.path.exists(target_path):
            return (False, "Error: Folder Not Found",
                    "The folder you selected could not be found. Please check "
                    "the folder location and try again.")
        if not os.path.isdir(target_path):
            return (False, "Error: Folder Not Found",
                    "The folder you selected could not be found. Please check "
                    "the folder location and try again.\n\n"
                    "(That location exists, but it is a file rather than a "
                    "folder.)")
        try:
            # Existence is not readability. A drive that is present but
            # locked, or a network share whose credentials have expired,
            # passes isdir() and then fails on the first real read --
            # which is exactly the case the generic error hid.
            os.scandir(target_path).close()
        except PermissionError:
            return (False, "Error: Folder Cannot Be Accessed",
                    "The folder was found, but it could not be opened. You may "
                    "not have permission to read it. Check the folder's "
                    "permissions, or choose a different folder, and try again.")
        except OSError as exc:
            return (False, "Error: Folder Cannot Be Accessed",
                    "The folder was found, but it could not be opened.\n\n"
                    f"{exc.strerror or exc}\n\n"
                    "If it is on a removable or network drive, check that the "
                    "drive is still connected, and try again.")
        return (True, None, None)

    def start_new_project(self):
        source_roots = self._collect_source_roots()
        if not source_roots:
            messagebox.showwarning("Missing folder",
                                   "Please browse to a folder to inventory first.")
            return
        target_path = source_roots[0]

        ok, title, message = (True, None, None)
        for candidate in source_roots:
            ok, title, message = self.validate_target_folder(candidate)
            if not ok:
                target_path = candidate
                break
        if not ok:
            # Return to the New Project screen with everything the user
            # typed still in place. No project folder, no database and no
            # processing run is created for what is only a typo: making
            # one would leave an empty project behind for the user to
            # find and wonder about later.
            messagebox.showerror(title, message)
            app_log_write("WARNING",
                          f"New project rejected before creation -- {title}: {target_path}")
            return

        project_name = self.project_name_var.get().strip()
        # B4.4: the WHOLE root list crosses to the worker, as an
        # immutable copy. Passing only source_roots[0] lost every root
        # after the first, and the worker then read a `source_roots`
        # name that existed nowhere in its scope -- a NameError on the
        # real project-creation path. A tuple, because the worker runs
        # on another thread and must not depend on GUI state that the
        # user can still be editing.
        self.show_progress_screen(mode="create", project_name=project_name,
                                  source_roots=tuple(source_roots))

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
    def show_progress_screen(self, mode, target_path=None, project_name=None,
                             stages_to_run=None, source_roots=None):
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
                target=self._run_new_project_then_core,
                args=(tuple(source_roots or ()), project_name), daemon=True)
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

    # ------------------------------------------------------------------
    # Beta R2 -- run/stage recording helpers
    #
    # These wrap the coordinator so the four scan methods below stay
    # readable and so a missing/failed coordinator is handled in exactly
    # one place. Every one of them is a no-op when coordinator is None.
    # ------------------------------------------------------------------
    def _begin_run(self, run_kind, project_name=None, run_folder=None,
                   tolerate_stage_failures=False):
        coordinator = make_coordinator(project_name or self.current_project)
        if coordinator is None:
            return None
        try:
            coordinator.begin_run(run_kind, run_folder=run_folder,
                                  tolerate_stage_failures=tolerate_stage_failures)
        except Exception as exc:
            app_log_write("ERROR", f"Could not begin a run record ({run_kind}): {exc}")
            return None
        self.active_coordinator = coordinator
        return coordinator

    def _finish_run(self, coordinator, status=None, notes=None):
        if coordinator is None:
            return
        try:
            coordinator.finish(status=status, notes=notes)
        except Exception as exc:
            app_log_write("ERROR", f"Could not close the run record: {exc}")
        finally:
            if self.active_coordinator is coordinator:
                self.active_coordinator = None

    @staticmethod
    def _stage_context(coordinator, script_name, label):
        """A stage handle, or a null object when there is no coordinator.

        Returning a stand-in rather than None keeps the scan methods free
        of `if coordinator:` around every line, which is what makes the
        R2 diff readable against the Alpha control flow it has to
        preserve exactly.
        """
        if coordinator is None:
            return _NullStage()
        try:
            return coordinator.stage(script_name, label)
        except Exception as exc:
            app_log_write("WARNING", f"Could not open a stage record for {script_name}: {exc}")
            return _NullStage()

    def _bind_current_run_folder(self, coordinator, project_name):
        """Attach the run to the Runs\\<timestamp> folder that
        PreliminaryInventory.ps1 just created.

        The folder name is read back from settings.json rather than
        chosen here: PreliminaryInventory.ps1 mints it and is a
        protected Alpha file, so the coordinator follows it rather than
        the other way round.
        """
        if coordinator is None:
            return None
        settings = load_settings(project_name) or {}
        run_folder = settings.get("CurrentRun")
        if not run_folder:
            return None
        try:
            return coordinator.bind_run_folder(run_folder)
        except Exception as exc:
            app_log_write("WARNING", f"Could not bind the run folder: {exc}")
            return None

    def _run_new_project_then_core(self, source_roots, project_name):
        r"""Create the project, then run the Pre-Scan.

        B4.4: takes the full root list as an ARGUMENT. It previously
        took a single target_path and then referred to `source_roots`,
        which was defined only in start_new_project -- a NameError that
        the enclosing except swallowed. Anything singular needed here is
        derived locally from the list rather than passed alongside it,
        so the two cannot disagree.
        """
        source_roots = list(source_roots or ())
        target_path = source_roots[0] if source_roots else None
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

        # Beta R2: this run begins BEFORE its project does. The
        # coordinator records the run and the project-creation stage in
        # memory and in app.log, then writes them to the project
        # database via attach_database() once there is one -- so a
        # creation failure is still recorded, which is precisely the
        # case where a record is most wanted.
        coordinator = self._begin_run("prescan", project_name=project_name)
        try:
            with self._stage_context(coordinator, NEW_PROJECT_STAGE_KEY,
                                     "Creating project") as stage:
                success, stdout, stderr, returncode = \
                    coordinator.create_project(str(PROJECTS_DIR), project_name,
                                               source_roots)
                stage.record(returncode, stdout, stderr,
                             status=None if success else "failed")
            if not success:
                app_log_write("ERROR", (
                    f"Project creation failed for '{project_name}' "
                    f"(exit {returncode}). {(stderr or '').strip()[:800]}"))
                self._finish_run(coordinator, status="failed",
                                 notes="Project creation failed.")
                self._error(f"Failed to create project.\n\n{stderr[:500]}")
                return

            created_name = project_name
            self.current_project = created_name
            self._log(f"Project created: {created_name}")
            step += 1

            if coordinator is not None:
                # The database exists now; replay the run and the stage
                # above into it.
                try:
                    coordinator.attach_database(created_name)
                except Exception as exc:
                    app_log_write("WARNING", f"Could not attach the run to the database: {exc}")

            settings_path = str(get_settings_path(created_name))

            for script_name, label in PRE_SCAN_STAGES:
                self._stage(f"Step {step + 1} of {total_steps}: {label}...", (step / total_steps) * 100)
                self._log(f"Running {script_name}...")

                # Every pre-scan stage is Python. The stage KEYS keep
                # their historical .ps1 names because they are stable
                # identifiers in run_stage, exports and run history.
                is_inventory = script_name == INVENTORY_STAGE_KEY
                is_candidates = script_name == SIZE_CANDIDATE_STAGE_KEY

                with self._stage_context(coordinator, script_name, label) as stage:
                    if is_candidates:
                        success, stdout, stderr, returncode = \
                            coordinator.run_size_candidates(settings_path)
                    elif is_inventory:
                        # Beta B2: the inventory runs in this process.
                        # No subprocess, no C# compiled at every scan,
                        # and no CSV parsed back to reach the database.
                        success, stdout, stderr, returncode = \
                            coordinator.run_inventory(settings_path)

                    # PreliminaryInventory mints the Runs\<timestamp>
                    # folder. Bind to it as soon as it exists, so this
                    # stage's captured output and every later stage's
                    # land in the run folder rather than being buffered.
                    # The Python engine has already bound it, and
                    # bind_run_folder is idempotent, so this stays
                    # correct for both engines.
                    if is_inventory:
                        self._bind_current_run_folder(coordinator, created_name)

                    stage.record(returncode, stdout, stderr,
                                 status=None if success else "failed")

                    # errors.txt is written by the inventory itself and
                    # is the only per-path error record the protected
                    # scripts produce. Reading it here turns it into
                    # structured events without touching that script.
                    if script_name == "PreliminaryInventory.ps1" and coordinator is not None:
                        try:
                            coordinator.ingest_errors_txt(stage)
                        except Exception as exc:
                            app_log_write("WARNING", f"Could not ingest errors.txt: {exc}")

                # Persist the inventory into SQLite as its own stage, so
                # that a database failure stays distinguishable from a
                # scan failure -- R3's decision, preserved.
                #
                # The rows were already written during the walk, so
                # there is nothing to re-read; this stage records what
                # that persistence did. A failure here is recorded and
                # does not stop the run.
                #
                # B4.3: this block used to branch on a `use_python_engine`
                # variable that B4.2 removed when it collapsed the
                # engine switch. The reference survived, so every
                # Pre-Scan raised NameError here -- swallowed by the
                # except below, which logged a warning and silently
                # skipped recording the ingest stage. The branch is gone
                # now because there is only one engine to record.
                if is_inventory and success and coordinator is not None:
                    self._log("Recording inventory in the project database...")
                    try:
                        outcome = coordinator.record_inventory_ingest() or {}
                        if outcome.get("status") not in (None, "failed",
                                                         "skipped"):
                            self._log(f"  {outcome.get('rows', 0)} file(s) "
                                      f"recorded in "
                                      f"{outcome.get('elapsed_sec', 0.0):.1f}s.")
                    except Exception as exc:
                        app_log_write("WARNING", f"Inventory ingestion error: {exc}")
                        self._log("  WARNING: inventory could not be recorded in the "
                                  "database; the scan itself is unaffected.")

                if not success:
                    self._finish_run(coordinator, status="failed",
                                     notes=f"{label} failed.")
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
            self._log("Preparing time estimates...")
            with self._stage_context(coordinator, TIME_ESTIMATES_STAGE_KEY,
                                     "Preparing time estimates") as stage:
                success, stdout, stderr, returncode = \
                    coordinator.run_time_estimates(settings_path)
                # Calibration is explicitly optional here -- the Alpha
                # flow proceeds without estimates. Recording it as a
                # failed stage would be accurate but would then make the
                # whole run report as failed, which it is not. It is a
                # stage that failed inside a run that succeeded.
                stage.record(returncode, stdout, stderr,
                             status=None if success else "failed",
                             note=None if success else "Optional stage; the run continued without time estimates.")
            if not success:
                self._log(f"  WARNING: Calibration failed, proceeding without estimates: {stderr[:200]}")
            else:
                self._log("  done.")

            self._stage("Pre-Scan complete.", 100)
            self._finish_run(coordinator,
                             status="completed_with_warnings" if not success else None)
            self._done("choose_run_type")
        finally:
            # Guarantees the run record is closed even if something above
            # raised: a run left marked running would otherwise be
            # reported as interrupted on the next launch, which would be
            # a lie about what happened.
            self._finish_run(coordinator)

    def _run_duplicate_hash_stages(self):
        """Runs PartialHash.ps1 + FullHash.ps1 on the current project --
        one of the two choices on the Pre-Scan / Choose Run Type screen."""
        settings_path = str(get_settings_path(self.current_project))
        # Beta R2: this run's folder already exists -- PreliminaryInventory.ps1
        # created it during the pre-scan -- so the coordinator can bind to it
        # up front rather than discovering it partway through.
        settings = load_settings(self.current_project) or {}
        coordinator = self._begin_run("duplicate_analysis",
                                      run_folder=settings.get("CurrentRun"))
        try:
            # One Python stage performs the whole partial -> escalate
            # -> confirm pass. Recorded under the historical
            # PartialHash.ps1 stage KEY: that name is a stable
            # identifier in run_stage, exports and run history, not a
            # reference to a file.
            self._stage("Step 1 of 1: Hashing and duplicate detection...", 0)
            self._log("Running the hash & duplicate engine...")
            with self._stage_context(coordinator, PARTIAL_HASH_STAGE_KEY,
                                     "Hash & Duplicate Pass") as stage:
                success, stdout, stderr, returncode = \
                    coordinator.run_duplicate_hash(settings_path)
                stage.record(returncode, stdout, stderr,
                             status=None if success else "failed")
            if not success:
                self._finish_run(coordinator, status="failed",
                                 notes="Hash & Duplicate Pass failed.")
                self._error("Duplicate detection failed.\n\n" + stderr[:500])
                return
            self._log("  done.")
            self._stage("Duplicate detection complete.", 100)
            self._finish_run(coordinator)
            self._done("categories")
        finally:
            self._finish_run(coordinator)

    def _run_full_run_stage(self):
        """Runs FullHashInventory.ps1 -- the Full Run path from Choose Run
        Type, parallel to _run_duplicate_hash_stages for Duplicate Run."""
        settings_path = str(get_settings_path(self.current_project))
        settings = load_settings(self.current_project) or {}
        coordinator = self._begin_run("exhaustive_identity",
                                      run_folder=settings.get("CurrentRun"))
        try:
            # The exhaustive pass. Recorded under the historical
            # FullHashInventory.ps1 stage key.
            self._stage("Step 1 of 1: Full Hash Inventory (every file)...", 0)
            self._log("Running the hash engine (exhaustive)...")
            with self._stage_context(coordinator, FULL_RUN_STAGE_KEY,
                                     "Full Hash Inventory (every file)") as stage:
                success, stdout, stderr, returncode = \
                    coordinator.run_full_hash_inventory(settings_path)
                stage.record(returncode, stdout, stderr,
                             status=None if success else "failed")
            if not success:
                self._finish_run(coordinator, status="failed",
                                 notes="Full Hash Inventory failed.")
                self._error("Full Run failed.\n\n" + stderr[:500])
                return
            self._log("  done.")
            self._stage("Full Run complete.", 100)
            self._finish_run(coordinator)
            self._done("categories")
        finally:
            self._finish_run(coordinator)

    def _run_category_stages(self, stages_to_run):
        settings_path = str(get_settings_path(self.current_project))
        total_steps = len(stages_to_run)

        if total_steps == 0:
            self._done("complete")
            return

        settings = load_settings(self.current_project) or {}
        # tolerate_stage_failures: one analyzer failing does not stop the
        # others and does not make the run a failure. The failed stage row
        # and its error events carry that detail; the run reports
        # completed_with_warnings. See RunCoordinator._roll_up_status.
        coordinator = self._begin_run("content_analysis",
                                      run_folder=settings.get("CurrentRun"),
                                      tolerate_stage_failures=True)
        try:
            # Categories the user did NOT select are recorded as skipped
            # rather than left absent. "Not run", "ran and found nothing"
            # and "ran and failed" are three different answers, and only
            # the first is invisible unless it is written down.
            selected = {script for script, _label, _desc in stages_to_run}
            if coordinator is not None:
                for script_name, label, _desc in CATEGORY_STAGES:
                    if script_name not in selected:
                        try:
                            coordinator.skip_stage(
                                script_name, label,
                                "Not selected for this analysis run.")
                        except Exception as exc:
                            app_log_write("WARNING",
                                          f"Could not record skipped stage {script_name}: {exc}")

            # One Python stage per selected category, so the accepted
            # per-category stage identity is preserved: the run_stage
            # rows, their keys and their order are unchanged.
            import fo_analyzers as _fo_analyzers
            analyzer_keys = {}
            for script_name, _label, _desc in stages_to_run:
                spec = _fo_analyzers.SPEC_BY_SCRIPT.get(script_name)
                if spec is not None:
                    analyzer_keys[script_name] = spec.key

            for i, (script_name, label, _desc) in enumerate(stages_to_run):
                self._stage(f"Step {i + 1} of {total_steps}: {label}...", (i / total_steps) * 100)
                self._log(f"Running {script_name}...")

                with self._stage_context(coordinator, script_name, label) as stage:
                    key = analyzer_keys[script_name]
                    success, stdout, stderr, returncode = \
                        coordinator.run_analyzers(
                            settings_path, [key],
                            stage_handles={key: stage.run_stage_id})
                    status = stage.record(returncode, stdout, stderr)

                if returncode == 2:
                    # Unlike a per-category failure (which correctly continues
                    # to the next one, since categories are independent), a
                    # pause is a whole-session signal -- stop here, not just
                    # skip to the next category.
                    #
                    # Ingest first. The categories that already finished
                    # produced complete, correct CSVs, and abandoning
                    # their results because a LATER category was paused
                    # would lose work that was genuinely done.
                    self._finish_run(coordinator, status="paused",
                                     notes="Paused at a checkpoint at the user's request.")
                    self._paused()
                    return
                if not success:
                    # Category stages are independent of each other -- one
                    # failing shouldn't stop the rest from running.
                    self._log(f"  WARNING: {label} failed -- continuing with remaining categories.")
                    self._log(f"  {stderr[:300]}")
                else:
                    if status == "no_applicable_files":
                        # Exited cleanly and produced no output CSV: this
                        # project has no files of that type. Reported to
                        # the user as done, recorded distinctly.
                        self._log("  done -- no files of this type were found.")
                    else:
                        self._log("  done.")
                    self._mark_category_completed(script_name)

            self._stage("All selected categories complete.", 100)
            # The analyzer runtime persisted directly; there is no CSV
            # ingest step on this path.
            self._finish_run(coordinator)
            self._done("complete")
        finally:
            self._finish_run(coordinator)

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
