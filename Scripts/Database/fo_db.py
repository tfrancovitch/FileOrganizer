#!/usr/bin/env python3
r"""
fo_db.py
===================================================================
PRODUCTION CODE
The File Organizer -- B6.1 (A-F Reconciliation)
Module version: 1.5.0   Schema version: 7
====================================================================

The one and only module that opens the project SQLite database.

DESIGN RULES THIS MODULE ENFORCES
---------------------------------
1. Python is the sole SQLite writer. PowerShell never opens the
   database; it calls this module's CLI instead.

2. One project -> one project-local database. The `project` table
   is constrained to a single row, and open_project() refuses to
   open a database whose project_uid disagrees with the folder's
   project.json. A database copied in from another project is
   detected rather than silently trusted.

3. No cross-project behaviour. Nothing here opens two project
   databases, ATTACHes, or looks outside the project folder it was
   given.

4. Migrations are forward-only and numbered. A database newer than
   this build supports is refused, not opened read-write.

CHANGED IN BETA R2
------------------
APP_SCHEMA_VERSION is 2, so migration 002 (run, run_stage,
run_source_root, environment_snapshot, event) is applied on open. An
existing R1 database is migrated forward the first time it is opened,
inside one transaction, after a pre-migration backup -- exactly the
path the migration runner was built for in R1.

Nothing else in this module changed. Run and event writes live in
fo_runs.py, which operates on a connection this module hands it; the
decision about how a database is opened, which pragmas apply, and
whether this folder's database may be opened at all stays here.

CHANGED IN BETA R3
------------------
APP_SCHEMA_VERSION is 3, so migration 003 (inventory_scan, file_path,
file_observation) is applied on open. An existing R2 database is
migrated forward the first time it is opened, in one transaction, after
an automatic pre-migration backup.

Inventory reading and writing lives in fo_inventory.py, which operates
on a connection this module hands it.

CHANGED IN BETA R4
------------------
APP_SCHEMA_VERSION is 4, so migration 004 (content, hash_measurement,
duplicate_run, duplicate_group, duplicate_member) is applied on open.
An existing R3 database migrates forward on first open, in one
transaction, after an automatic pre-migration backup.

Hash and duplicate persistence lives in fo_hashes.py, which operates on
a connection this module hands it.

CHANGED IN BETA R5
------------------
APP_SCHEMA_VERSION is 5, so migration 005 (analyzer, analyzer_run,
analyzer_result, archive_member, extracted_content) is applied on open.
An existing R4 database migrates forward on first open, in one
transaction, after an automatic pre-migration backup.

Analyzer result persistence lives in fo_analyzers.py, which operates on
a connection this module hands it.

WHAT R5 STILL DOES NOT DO
-------------------------
No extracted-content STORAGE. extracted_content holds references and
metadata only; where the extracted text itself lives is R9's decision,
and this build must not pre-empt it.

SQLite is NOT authoritative for anything yet: every CSV and report is
produced exactly as in Alpha, and no downstream script reads the
database. Regenerating the exports from it is R6; making it the source
of truth is R7.

CLI
---
    python fo_db.py init-project     --project-dir <dir> --name <n> [--source-root <path>]...
    python fo_db.py open             --project-dir <dir>
    python fo_db.py verify           --project-dir <dir>
    python fo_db.py info             --project-dir <dir>
    python fo_db.py add-source-root  --project-dir <dir> --path <path> [--label <l>]
    python fo_db.py self-test        [--temp-dir <dir>]

Exit codes:
    0  success
    1  operation failed (see message)
    2  usage / environment error
    3  schema is newer than this build supports
    4  project identity mismatch (isolation guard tripped)
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODULE_VERSION = "1.5.0"
APP_VERSION = "B6.1"

#: Highest schema version this build understands. A database whose
#: user_version exceeds this is refused (see open_project).
#:
#: 1 -> Beta R1: schema_migration, app_meta, project, source_root
#: 2 -> Beta R2: environment_snapshot, run, run_stage, run_source_root,
#:               event
#: 3 -> Beta R3: inventory_scan, file_path, file_observation
#: 4 -> Beta R4: content, hash_measurement, duplicate_run,
#:               duplicate_group, duplicate_member
#: 5 -> Beta R5: analyzer, analyzer_run, analyzer_result,
#:               archive_member, extracted_content
#: B6.1 requires schema 7. Migration 006 introduced current-state separation;
#: migration 007 completes the A-F reconciliation/current projection. See the
#: two migration files and B5_A-F_RECONCILIATION.md for rationale.
APP_SCHEMA_VERSION = 7

PROJECT_JSON_NAME = "project.json"
PROJECT_JSON_SCHEMA = "fileorganizer.project/1"
DATABASE_DIR_NAME = "Database"
DATABASE_FILE_NAME = "FileOrganizer.db"
BACKUP_DIR_NAME = "Backups"
MIGRATIONS_DIR_NAME = "migrations"

#: Windows MAX_PATH headroom check. The database path is never opened
#: through the \\?\ prefix because sqlite3's behaviour with extended
#: paths is not something to depend on; instead we refuse to create a
#: database at a path close enough to the limit to be a problem.
MAX_SAFE_DB_PATH = 240


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class FoDbError(Exception):
    """Base class for every error this module raises."""
    exit_code = 1


class SchemaTooNewError(FoDbError):
    """The database was created by a newer build of the application."""
    exit_code = 3


class ProjectMismatchError(FoDbError):
    """project.json and the database disagree about which project this is."""
    exit_code = 4


class MigrationError(FoDbError):
    """A migration failed; the database was left at its previous version."""
    exit_code = 1


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def utc_now():
    """ISO-8601 UTC timestamp, millisecond precision, explicit Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def path_key(path):
    """Invariant-lowercase comparison key for a Windows path.

    Windows paths are case-insensitive, so C:\\Docs and c:\\docs are the
    same location and must not become two source_root rows. The display
    form is preserved separately in root_path.
    """
    return path.rstrip("\\/").lower()


def _migrations_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), MIGRATIONS_DIR_NAME)


def _is_network_path(path):
    """True for UNC paths and mapped network drives we can identify cheaply."""
    if path.startswith("\\\\"):
        return True
    if os.name == "nt" and len(path) > 2 and path[1] == ":":
        try:
            import ctypes
            # DRIVE_REMOTE == 4
            return ctypes.windll.kernel32.GetDriveTypeW("%s:\\" % path[0]) == 4
        except Exception:  # pragma: no cover -- diagnostic only, never fatal
            return False
    return False


# ---------------------------------------------------------------------------
# Project layout
# ---------------------------------------------------------------------------

class ProjectPaths(object):
    """Resolves the standard locations inside a project folder."""

    def __init__(self, project_dir):
        self.project_dir = os.path.abspath(project_dir)
        self.project_json = os.path.join(self.project_dir, PROJECT_JSON_NAME)
        self.database_dir = os.path.join(self.project_dir, DATABASE_DIR_NAME)
        self.database_file = os.path.join(self.database_dir, DATABASE_FILE_NAME)
        self.backup_dir = os.path.join(self.database_dir, BACKUP_DIR_NAME)
        # Legacy Alpha file. R1 neither reads nor writes it; recorded so
        # tooling can report coexistence. Legacy migration is not R1's job.
        self.legacy_settings = os.path.join(self.project_dir, "settings.json")

    def ensure_dirs(self):
        for d in (self.project_dir, self.database_dir, self.backup_dir):
            if not os.path.isdir(d):
                os.makedirs(d)


# ---------------------------------------------------------------------------
# Connection handling
# ---------------------------------------------------------------------------

def connect(db_path, create=False):
    """Open a connection with the project's standard pragmas applied.

    Pragma choices:
      foreign_keys = ON     referential integrity is worth the cost;
                            SQLite defaults it OFF, per connection.
      journal_mode = WAL    readers do not block the writer, and a
                            crash cannot leave a torn database.
                            EXCEPTION: WAL relies on shared memory that
                            is unreliable on network shares, so a
                            network-hosted database falls back to the
                            older rollback journal. (Relocating a
                            OneDrive-hosted database is a separate,
                            deferred decision -- not done here.)
      synchronous = NORMAL  safe against process death under WAL, and
                            avoids an fsync per commit.
      busy_timeout = 5000   safety net; the single-writer rule should
                            mean it never fires.
      temp_store = MEMORY   temp b-trees stay off disk.
    """
    if not create and not os.path.isfile(db_path):
        raise FoDbError("Database not found: %s" % db_path)

    conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("PRAGMA foreign_keys = ON")

    if _is_network_path(db_path):
        cur.execute("PRAGMA journal_mode = DELETE")
    else:
        cur.execute("PRAGMA journal_mode = WAL")

    cur.execute("PRAGMA synchronous = NORMAL")
    cur.execute("PRAGMA busy_timeout = 5000")
    cur.execute("PRAGMA temp_store = MEMORY")
    return conn


def get_user_version(conn):
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------

def discover_migrations(migrations_dir=None):
    """Return [(version, description, path)] sorted by version."""
    migrations_dir = migrations_dir or _migrations_dir()
    if not os.path.isdir(migrations_dir):
        raise FoDbError("Migrations folder not found: %s" % migrations_dir)

    found = []
    for name in sorted(os.listdir(migrations_dir)):
        if not name.lower().endswith(".sql"):
            continue
        stem = name[:-4]
        number, _, rest = stem.partition("_")
        if not number.isdigit():
            raise FoDbError(
                "Migration filename is not in NNN_description.sql form: %s" % name)
        found.append((int(number), rest.replace("_", " ") or stem,
                      os.path.join(migrations_dir, name)))

    found.sort(key=lambda item: item[0])

    expected = list(range(1, len(found) + 1))
    if [v for v, _d, _p in found] != expected:
        raise FoDbError(
            "Migration numbering must be contiguous from 001. Found: %s"
            % [v for v, _d, _p in found])
    return found


def backup_database(paths, reason):
    """Copy the database aside before a migration. Returns the backup path."""
    if not os.path.isfile(paths.database_file):
        return None
    if not os.path.isdir(paths.backup_dir):
        os.makedirs(paths.backup_dir)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    name = "FileOrganizer_%s_%s.db" % (stamp, reason)
    target = os.path.join(paths.backup_dir, name)

    # Use SQLite's own backup API rather than a file copy: it produces a
    # consistent snapshot including anything still sitting in the WAL.
    source = sqlite3.connect(paths.database_file)
    try:
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    return target


def apply_migrations(conn, paths, app_version=APP_VERSION, migrations_dir=None):
    """Apply every pending migration. Returns the list of versions applied.

    Each migration runs in ONE transaction covering the DDL, the
    schema_migration bookkeeping row, and the user_version bump, so a
    failure leaves the database exactly as it was.

    Note on executescript(): it commits any pending transaction before
    running, so the BEGIN must be inside the script text rather than
    issued beforehand. It does not commit at the end, which is what
    lets the bookkeeping below join the same transaction.
    """
    current = get_user_version(conn)

    if current > APP_SCHEMA_VERSION:
        raise SchemaTooNewError(
            "This database is at schema version %d, but this build of The File "
            "Organizer supports up to version %d.\n"
            "Refusing to open it. Update the application before using this project."
            % (current, APP_SCHEMA_VERSION))

    pending = [m for m in discover_migrations(migrations_dir) if m[0] > current]
    if not pending:
        return []

    if current > 0:
        backup_database(paths, "preMigration_v%d" % current)

    applied = []
    for version, description, sql_path in pending:
        if version > APP_SCHEMA_VERSION:
            break

        with open(sql_path, "r", encoding="utf-8-sig") as handle:
            sql = handle.read()

        started = time.time()
        cur = conn.cursor()
        try:
            cur.executescript("BEGIN IMMEDIATE;\n" + sql)
            duration_ms = int((time.time() - started) * 1000)
            cur.execute(
                "INSERT INTO schema_migration "
                "(version, description, applied_utc, app_version, duration_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (version, description, utc_now(), app_version, duration_ms))
            # PRAGMA does not accept a bound parameter; version is an int
            # produced by discover_migrations from a validated filename.
            cur.execute("PRAGMA user_version = %d" % version)
            conn.commit()
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            raise MigrationError(
                "Migration %03d (%s) failed and was rolled back. The database "
                "remains at schema version %d.\n  %s"
                % (version, description, get_user_version(conn), exc))
        applied.append(version)

    return applied


# ---------------------------------------------------------------------------
# project.json
# ---------------------------------------------------------------------------

def write_project_json(paths, project_uid, name, created_utc, app_version):
    """Write the bootstrap identity file.

    Bootstrap information ONLY. Mutable project state belongs in SQLite.
    This file exists so the application can identify a project folder
    without opening a database, and so a human can tell what a folder is.
    """
    payload = {
        "schema": PROJECT_JSON_SCHEMA,
        "project_uid": project_uid,
        "name": name,
        "created_utc": created_utc,
        "app_version_created": app_version,
        "database_relative_path": os.path.join(DATABASE_DIR_NAME, DATABASE_FILE_NAME),
    }
    with open(paths.project_json, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    return payload


def read_project_json(paths):
    if not os.path.isfile(paths.project_json):
        raise FoDbError("project.json not found at: %s" % paths.project_json)
    with open(paths.project_json, "r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if data.get("schema") != PROJECT_JSON_SCHEMA:
        raise FoDbError(
            "project.json declares schema '%s'; this build expects '%s'."
            % (data.get("schema"), PROJECT_JSON_SCHEMA))
    for field in ("project_uid", "name"):
        if not data.get(field):
            raise FoDbError("project.json is missing required field '%s'." % field)
    return data


# ---------------------------------------------------------------------------
# Project operations
# ---------------------------------------------------------------------------

def init_project(project_dir, name, source_roots=None, app_version=APP_VERSION,
                 project_uid=None):
    """Create the project-local database and bootstrap identity file.

    Safe to call on a project folder that already exists (the Alpha
    layout, with settings.json and Runs\\, is left completely alone).
    Refuses to overwrite an existing database.
    """
    paths = ProjectPaths(project_dir)

    if not os.path.isdir(paths.project_dir):
        raise FoDbError("Project folder does not exist: %s" % paths.project_dir)

    if len(paths.database_file) > MAX_SAFE_DB_PATH:
        raise FoDbError(
            "The database path is %d characters, which is too close to the "
            "Windows MAX_PATH limit to be safe:\n  %s\n"
            "Move the project to a shorter path."
            % (len(paths.database_file), paths.database_file))

    if os.path.isfile(paths.database_file):
        raise FoDbError(
            "A database already exists for this project:\n  %s\n"
            "Refusing to overwrite it." % paths.database_file)

    paths.ensure_dirs()

    created_utc = utc_now()
    project_uid = project_uid or str(uuid.uuid4())

    conn = connect(paths.database_file, create=True)
    try:
        apply_migrations(conn, paths, app_version=app_version)

        conn.execute(
            "INSERT INTO project (project_id, project_uid, name, created_utc, "
            "app_version_created) VALUES (1, ?, ?, ?, ?)",
            (project_uid, name, created_utc, app_version))

        for key, value in (
                ("database_uid", str(uuid.uuid4())),
                ("created_utc", created_utc),
                ("created_by_app_version", app_version),
                ("fo_db_module_version", MODULE_VERSION)):
            conn.execute(
                "INSERT INTO app_meta (key, value, updated_utc) VALUES (?, ?, ?)",
                (key, value, created_utc))

        for root in (source_roots or []):
            _insert_source_root(conn, root, label=None, added_utc=created_utc)

        conn.commit()
    except Exception:
        conn.close()
        # A half-created database is worse than none: remove it so the next
        # attempt starts clean. project.json has not been written yet.
        for leftover in (paths.database_file,
                         paths.database_file + "-wal",
                         paths.database_file + "-shm"):
            if os.path.isfile(leftover):
                try:
                    os.remove(leftover)
                except OSError:
                    pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass

    write_project_json(paths, project_uid, name, created_utc, app_version)
    return {"project_uid": project_uid, "database": paths.database_file,
            "created_utc": created_utc, "schema_version": APP_SCHEMA_VERSION}


def _insert_source_root(conn, root_path, label=None, added_utc=None):
    added_utc = added_utc or utc_now()
    key = path_key(root_path)
    existing = conn.execute(
        "SELECT source_root_id FROM source_root WHERE project_id = 1 AND root_path_key = ?",
        (key,)).fetchone()
    if existing:
        return existing["source_root_id"]
    cur = conn.execute(
        "INSERT INTO source_root (project_id, root_path, root_path_key, label, added_utc) "
        "VALUES (1, ?, ?, ?, ?)",
        (root_path.rstrip("\\/"), key, label, added_utc))
    return cur.lastrowid


def open_project(project_dir, app_version=APP_VERSION):
    """Open a project database, migrating it forward if it is behind.

    Enforces the isolation guard: the project_uid in the database must
    match the project_uid in project.json. A mismatch means a database
    from a different project is sitting in this folder, and the correct
    response is to stop, not to use it.

    Returns (connection, project_row). The caller closes the connection.
    """
    paths = ProjectPaths(project_dir)
    bootstrap = read_project_json(paths)

    if not os.path.isfile(paths.database_file):
        raise FoDbError(
            "project.json exists but the database is missing:\n  %s"
            % paths.database_file)

    conn = connect(paths.database_file)
    try:
        version = get_user_version(conn)
        if version > APP_SCHEMA_VERSION:
            raise SchemaTooNewError(
                "This project's database is at schema version %d, but this build "
                "supports up to version %d.\nRefusing to open it. Update The File "
                "Organizer before using this project."
                % (version, APP_SCHEMA_VERSION))

        apply_migrations(conn, paths, app_version=app_version)

        row = conn.execute("SELECT * FROM project WHERE project_id = 1").fetchone()
        if row is None:
            raise FoDbError(
                "The database has no project row. It is incomplete or was created "
                "by a different tool:\n  %s" % paths.database_file)

        if row["project_uid"] != bootstrap["project_uid"]:
            raise ProjectMismatchError(
                "PROJECT ISOLATION GUARD TRIPPED.\n"
                "  project.json says : %s (%s)\n"
                "  database says     : %s (%s)\n"
                "The database in this folder belongs to a different project. "
                "Refusing to open it. Nothing has been read from or written to it."
                % (bootstrap["project_uid"], bootstrap.get("name"),
                   row["project_uid"], row["name"]))

        return conn, row
    except Exception:
        conn.close()
        raise


def add_source_root(project_dir, root_path, label=None, app_version=APP_VERSION):
    conn, _project = open_project(project_dir, app_version=app_version)
    try:
        root_id = _insert_source_root(conn, root_path, label=label)
        conn.commit()
        return root_id
    finally:
        conn.close()


def list_source_roots(conn):
    return conn.execute(
        "SELECT source_root_id, root_path, label, added_utc, is_active "
        "FROM source_root WHERE project_id = 1 ORDER BY source_root_id").fetchall()


def verify(project_dir, app_version=APP_VERSION):
    """Run the R1 health checks. Returns a dict of results."""
    paths = ProjectPaths(project_dir)
    results = {
        "project_dir": paths.project_dir,
        "checks": [],
        "ok": True,
    }

    def record(name, ok, detail=""):
        results["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            results["ok"] = False

    record("project.json present", os.path.isfile(paths.project_json), paths.project_json)
    record("database present", os.path.isfile(paths.database_file), paths.database_file)
    if not results["ok"]:
        return results

    conn, project = open_project(project_dir, app_version=app_version)
    try:
        version = get_user_version(conn)
        record("schema version == %d" % APP_SCHEMA_VERSION,
               version == APP_SCHEMA_VERSION, "found %d" % version)

        integrity = conn.execute("PRAGMA integrity_check").fetchall()
        integrity_ok = len(integrity) == 1 and integrity[0][0] == "ok"
        record("integrity_check clean", integrity_ok,
               "ok" if integrity_ok else str([tuple(r) for r in integrity]))

        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        record("foreign_key_check clean", len(fk) == 0,
               "ok" if not fk else "%d violation(s)" % len(fk))

        fk_on = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
        record("foreign_keys enforced", fk_on == 1, "foreign_keys=%d" % fk_on)

        count = conn.execute("SELECT COUNT(*) AS n FROM project").fetchone()["n"]
        record("exactly one project row", count == 1, "found %d" % count)

        bootstrap = read_project_json(paths)
        record("project.json UID matches database",
               bootstrap["project_uid"] == project["project_uid"],
               project["project_uid"])

        roots = list_source_roots(conn)
        record("source_root table usable", True, "%d row(s)" % len(roots))

        migrations = conn.execute(
            "SELECT version, description, applied_utc FROM schema_migration "
            "ORDER BY version").fetchall()
        record("migration history recorded", len(migrations) >= 1,
               ", ".join("%03d %s" % (m["version"], m["description"]) for m in migrations))

        results["project"] = {k: project[k] for k in project.keys()}
        results["source_roots"] = [dict(r) for r in roots]
        results["schema_version"] = version
        results["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
        results["legacy_settings_present"] = os.path.isfile(paths.legacy_settings)
    finally:
        conn.close()

    return results


def info(project_dir):
    conn, project = open_project(project_dir)
    try:
        return {
            "project": {k: project[k] for k in project.keys()},
            "schema_version": get_user_version(conn),
            "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
            "source_roots": [dict(r) for r in list_source_roots(conn)],
            "migrations": [dict(r) for r in conn.execute(
                "SELECT * FROM schema_migration ORDER BY version").fetchall()],
            "app_meta": {r["key"]: r["value"] for r in conn.execute(
                "SELECT key, value FROM app_meta").fetchall()},
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Self-test -- exercises the module against a throwaway project
# ---------------------------------------------------------------------------

def self_test(temp_dir=None):
    """Create, migrate, verify, and tamper with a scratch project.

    This is a smoke test of the module itself, not of the application.
    It never touches a real project.
    """
    import tempfile

    base = temp_dir or tempfile.mkdtemp(prefix="fo_db_selftest_")
    made_temp = temp_dir is None
    failures = []

    def check(label, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        print("  [%s] %s%s" % (status, label, (" -- " + detail) if detail else ""))
        if not condition:
            failures.append(label)

    try:
        print("Self-test workspace: %s" % base)

        # --- create ----------------------------------------------------
        p1 = os.path.join(base, "Project-One")
        os.makedirs(p1)
        result = init_project(p1, "Project One",
                              source_roots=[r"C:\Users\test\Documents", r"E:\Photos"])
        check("init_project creates a database", os.path.isfile(
            ProjectPaths(p1).database_file))
        check("schema version is %d" % APP_SCHEMA_VERSION,
              result["schema_version"] == APP_SCHEMA_VERSION)

        v = verify(p1)
        for item in v["checks"]:
            check(item["check"], item["ok"], item["detail"])
        check("two source roots recorded", len(v["source_roots"]) == 2,
              "%d" % len(v["source_roots"]))

        # --- migration 002 landed (Beta R2) -----------------------------
        conn, _ = open_project(p1)
        try:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
        finally:
            conn.close()
        expected_v2 = {"environment_snapshot", "run", "run_stage",
                       "run_source_root", "event"}
        check("schema version 2 tables present", expected_v2 <= tables,
              ", ".join(sorted(expected_v2 - tables)) or "all present")
        expected_v3 = {"inventory_scan", "file_path", "file_observation"}
        check("schema version 3 tables present", expected_v3 <= tables,
              ", ".join(sorted(expected_v3 - tables)) or "all present")
        expected_v4 = {"content", "hash_measurement", "duplicate_run",
                       "duplicate_group", "duplicate_member"}
        check("schema version 4 tables present", expected_v4 <= tables,
              ", ".join(sorted(expected_v4 - tables)) or "all present")
        expected_v5 = {"analyzer", "analyzer_run", "analyzer_result",
                       "archive_member", "extracted_content"}
        check("schema version 5 tables present", expected_v5 <= tables,
              ", ".join(sorted(expected_v5 - tables)) or "all present")
        # R5 persists analyzer results. It must NOT have created anywhere
        # to put extracted text BODIES, or any authoritative-export
        # machinery -- those belong to R9 and R6, and an empty table
        # inside a clean integrity_check reads as more assurance than it
        # is. extracted_content is a reference table and is expected;
        # a store for the text itself is not.
        premature = {"extracted_text", "extracted_blob", "content_blob",
                     "content_store", "export_manifest", "image_hash"} & tables
        check("no R6/R9 storage tables created yet", not premature,
              ", ".join(sorted(premature)) or "none")

        # --- multiple roots, case-insensitive dedupe --------------------
        add_source_root(p1, r"c:\users\test\documents")
        conn, _ = open_project(p1)
        try:
            roots = list_source_roots(conn)
        finally:
            conn.close()
        check("duplicate root (different case) not re-added", len(roots) == 2,
              "%d" % len(roots))

        add_source_root(p1, r"D:\Scans", label="Scanner output")
        conn, _ = open_project(p1)
        try:
            roots = list_source_roots(conn)
        finally:
            conn.close()
        check("third distinct root added", len(roots) == 3, "%d" % len(roots))

        # --- refuse to overwrite ---------------------------------------
        try:
            init_project(p1, "Project One")
            check("refuses to overwrite an existing database", False)
        except FoDbError:
            check("refuses to overwrite an existing database", True)

        # --- isolation: two projects are independent --------------------
        p2 = os.path.join(base, "Project-Two")
        os.makedirs(p2)
        init_project(p2, "Project Two", source_roots=[r"F:\Music"])
        i1, i2 = info(p1), info(p2)
        check("projects have distinct UIDs",
              i1["project"]["project_uid"] != i2["project"]["project_uid"])
        check("project two sees only its own roots",
              len(i2["source_roots"]) == 1 and
              i2["source_roots"][0]["root_path"] == r"F:\Music")

        # --- isolation guard: swap the databases ------------------------
        swapped = os.path.join(base, "Project-Swapped")
        shutil.copytree(p1, swapped)
        shutil.copy2(ProjectPaths(p2).database_file,
                     ProjectPaths(swapped).database_file)
        for suffix in ("-wal", "-shm"):
            stale = ProjectPaths(swapped).database_file + suffix
            if os.path.isfile(stale):
                os.remove(stale)
        try:
            open_project(swapped)
            check("isolation guard rejects a foreign database", False)
        except ProjectMismatchError:
            check("isolation guard rejects a foreign database", True)

        # --- schema-too-new guard ---------------------------------------
        future = os.path.join(base, "Project-Future")
        shutil.copytree(p1, future)
        fconn = connect(ProjectPaths(future).database_file)
        try:
            fconn.execute("PRAGMA user_version = %d" % (APP_SCHEMA_VERSION + 99))
        finally:
            fconn.close()
        try:
            open_project(future)
            check("refuses a database newer than this build", False)
        except SchemaTooNewError:
            check("refuses a database newer than this build", True)

        # --- foreign keys actually enforced ------------------------------
        conn, _ = open_project(p1)
        try:
            conn.execute(
                "INSERT INTO source_root (project_id, root_path, root_path_key, added_utc) "
                "VALUES (99, 'X', 'x', ?)", (utc_now(),))
            conn.commit()
            check("foreign key violation rejected", False)
        except sqlite3.IntegrityError:
            check("foreign key violation rejected", True)
        finally:
            conn.close()

        # --- second project row rejected ---------------------------------
        conn, _ = open_project(p1)
        try:
            conn.execute(
                "INSERT INTO project (project_id, project_uid, name, created_utc, "
                "app_version_created) VALUES (2, 'x', 'Sneaky', ?, 'x')", (utc_now(),))
            conn.commit()
            check("second project row rejected", False)
        except sqlite3.IntegrityError:
            check("second project row rejected", True)
        finally:
            conn.close()

    finally:
        if made_temp and not failures:
            shutil.rmtree(base, ignore_errors=True)

    print("")
    if failures:
        print("SELF-TEST FAILED (%d): %s" % (len(failures), ", ".join(failures)))
        print("Workspace retained for inspection: %s" % base)
        return 1
    print("SELF-TEST PASSED")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_json(payload):
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="fo_db.py",
        description="The File Organizer -- project database foundation (R1).")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("init-project", help="Create the project-local database")
    p.add_argument("--project-dir", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--source-root", action="append", default=[],
                   help="May be given more than once")
    p.add_argument("--app-version", default=APP_VERSION)

    p = sub.add_parser("open", help="Open (and migrate forward) the database")
    p.add_argument("--project-dir", required=True)

    p = sub.add_parser("verify", help="Run the R1 health checks")
    p.add_argument("--project-dir", required=True)
    p.add_argument("--json", action="store_true", help="Machine-readable output")

    p = sub.add_parser("info", help="Show project, roots, migrations")
    p.add_argument("--project-dir", required=True)

    p = sub.add_parser("add-source-root", help="Add a source root to the project")
    p.add_argument("--project-dir", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--label")

    p = sub.add_parser("self-test", help="Smoke-test this module in a scratch folder")
    p.add_argument("--temp-dir")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    try:
        if args.command == "init-project":
            result = init_project(args.project_dir, args.name,
                                  source_roots=args.source_root,
                                  app_version=args.app_version)
            print("Project database created.")
            print("  Project UID    : %s" % result["project_uid"])
            print("  Database       : %s" % result["database"])
            print("  Schema version : %d" % result["schema_version"])
            return 0

        if args.command == "open":
            conn, project = open_project(args.project_dir)
            try:
                print("Opened '%s' (schema version %d)."
                      % (project["name"], get_user_version(conn)))
            finally:
                conn.close()
            return 0

        if args.command == "verify":
            results = verify(args.project_dir)
            if args.json:
                _print_json(results)
            else:
                print("R1 database verification: %s" % results["project_dir"])
                for item in results["checks"]:
                    print("  [%s] %-42s %s"
                          % ("PASS" if item["ok"] else "FAIL",
                             item["check"], item["detail"]))
                if "source_roots" in results:
                    print("  Source roots:")
                    for root in results["source_roots"]:
                        print("    %d  %s" % (root["source_root_id"], root["root_path"]))
                    print("  Journal mode: %s" % results.get("journal_mode"))
                    if results.get("legacy_settings_present"):
                        print("  Legacy settings.json present (Alpha pipeline unaffected).")
                print("")
                print("VERDICT: %s" % ("PASS" if results["ok"] else "FAIL"))
            return 0 if results["ok"] else 1

        if args.command == "info":
            _print_json(info(args.project_dir))
            return 0

        if args.command == "add-source-root":
            root_id = add_source_root(args.project_dir, args.path, label=args.label)
            print("Source root %d: %s" % (root_id, args.path))
            return 0

        if args.command == "self-test":
            return self_test(args.temp_dir)

    except FoDbError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return exc.exit_code
    except sqlite3.Error as exc:
        print("SQLITE ERROR: %s" % exc, file=sys.stderr)
        return 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
