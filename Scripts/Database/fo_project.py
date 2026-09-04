#!/usr/bin/env python3
r"""
fo_project.py
===================================================================
PRODUCTION CODE
The File Organizer -- Phase 1 Release Candidate
Module version: 1.0.0   Requires schema version: 5
===================================================================

Creates a project: the folder, its settings.json, and its
project-local SQLite database.

WHY THIS IS PYTHON

New-Project.ps1 created the folder and settings.json, then shelled out
to python.exe to run fo_db.py, because Python is the sole database
writer. That made project creation

    Dashboard (Python) -> PowerShell -> Python

for work that has no PowerShell-specific content: make two
directories, write a JSON file, call a function that was already
Python. The bounce cost an interpreter launch, a second place for the
settings shape to be defined, and a failure mode -- "PowerShell ran but
python.exe was not found" -- that cannot occur when the caller IS
Python.

It also made the database step DELIBERATELY NON-FATAL, because a
PowerShell script could not be sure Python existed. That reasoning
expired: Phase 1's runtime is Python throughout, so a project without
a database is no longer a working project, and this module reports the
failure instead of quietly producing one.

WHAT IS PRESERVED EXACTLY

The user-visible result is unchanged: same folder layout
(settings.json + Runs\), same settings.json keys in the same order,
same ISO-8601 CreatedOn, same UTF-8 encoding, same default-name rule.
An existing project folder is never overwritten, and fo_db.init_project
still refuses to overwrite an existing database.

Scripts are NOT copied into the project. Every project runs from the
one master installation, which is what makes upgrading the toolkit a
single-folder operation.
"""

import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fo_db                                                    # noqa: E402
import win_meta                                                 # noqa: E402


#: Written into settings.json. Matches New-Project.ps1's $ToolVersion.
TOOL_VERSION = "2.0.0"

#: The Alpha settings.json shape, key order included. The order is not
#: cosmetic -- a diff of two projects created a year apart should show
#: what differs, not that the writer reordered its keys.
SETTINGS_KEY_ORDER = ("ProjectName", "ToolVersion", "CreatedOn", "SourceRoots",
                      "TargetPath", "CurrentRun", "RunHistory", "NextDBID",
                      "SchemaVersion")

#: TargetPath is written as SourceRoots[0] and is a COMPATIBILITY MIRROR,
#: not the source of truth.
#:
#: SourceRoots is authoritative. TargetPath remains because R3's
#: accepted inventory layer resolves a project's identity through it
#: (fo_inventory), and a handful of accepted helpers still read it.
#: Removing it would mean editing accepted R3/R4/R5 persistence code
#: inside what is a consolidation revision, which is a larger change
#: than the problem justifies.
#:
#: Nothing may WRITE TargetPath expecting it to be read back as the
#: project's scope. A single-root project sees identical values in both
#: fields; a multi-root project sees the first root in TargetPath and
#: the whole truth in SourceRoots.

_INVALID = re.compile(r'[<>:"/\\|?*]')


class ProjectError(Exception):
    """Project creation failed. Carries a message meant for the user."""


def default_project_name(target_path):
    r"""The suggested name for a project over `target_path`.

    Mirrors New-Project.ps1's Get-DefaultProjectName: the leaf folder
    name, or the drive letter for a drive root, with characters Windows
    forbids in a folder name replaced by underscores.
    """
    text = (target_path or "").rstrip("\\/")
    if not text:
        return "Project"
    if re.fullmatch(r"[A-Za-z]:", text):
        return "%s_Drive" % text[0].upper()
    leaf = os.path.basename(text) or text
    leaf = _INVALID.sub("_", leaf).strip(" .")
    return leaf or "Project"


def unique_project_folder(projects_dir, name):
    """`name`, or name_2, name_3 ... if that folder already exists."""
    candidate = os.path.join(projects_dir, name)
    if not os.path.exists(candidate):
        return candidate, name
    index = 2
    while True:
        alternative = "%s_%d" % (name, index)
        candidate = os.path.join(projects_dir, alternative)
        if not os.path.exists(candidate):
            return candidate, alternative
        index += 1


def build_settings(project_name, source_roots, created_on=None):
    """The initial settings.json content, in the accepted key order."""
    values = {
        "ProjectName": project_name,
        "ToolVersion": TOOL_VERSION,
        "CreatedOn": (created_on or datetime.now().astimezone()).isoformat(),
        "SourceRoots": list(source_roots),
        "TargetPath": source_roots[0],
        "CurrentRun": None,
        "RunHistory": [],
        "NextDBID": 1,
        #: Deliberately 1, not 5. This is the SETTINGS file's own format
        #: version, which has not changed since Alpha -- it is not the
        #: database schema version, which fo_db owns and reports
        #: separately. Conflating the two is how a v5 database once
        #: announced itself as v2.
        "SchemaVersion": 1,
    }
    return {key: values[key] for key in SETTINGS_KEY_ORDER}


def write_settings(settings_path, settings):
    """UTF-8, indented, with a trailing newline."""
    with open(settings_path, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2)
        handle.write("\n")
    return settings_path


def normalize_roots(source_roots):
    r"""Validate and de-duplicate the roots for a new project.

    Rejects zero roots, non-existent folders, and equivalent duplicates.
    "Equivalent" is decided by the same path_key normalisation the
    database uses for source_root identity, so C:\Photos and
    C:\photos\ are one root here exactly as they would be one row
    there -- catching it at creation is far kinder than discovering it
    as a mysteriously doubled inventory.

    Returns the accepted roots in the order given.
    """
    if not source_roots:
        raise ProjectError("At least one source folder is required.")

    accepted = []
    seen = {}
    for raw in source_roots:
        text = (raw or "").strip()
        if not text:
            continue
        if not os.path.isdir(text):
            raise ProjectError("This source folder does not exist:\n  %s" % text)
        key = fo_db.path_key(win_meta.normalize_root(text))
        if key in seen:
            raise ProjectError(
                "These two source folders are the same location:\n"
                "  %s\n  %s" % (seen[key], text))
        seen[key] = text
        accepted.append(win_meta.normalize_root(text))

    if not accepted:
        raise ProjectError("At least one source folder is required.")
    return accepted


def create_project(projects_dir, project_name, source_roots, app_version=None):
    r"""Create one project. Returns a summary dict.

    Order matters and is the same order New-Project.ps1 used: folder,
    then settings.json, then the database. A caller that fails partway
    leaves a folder a human can inspect rather than a half-initialised
    database.

    Raises ProjectError with a message meant for the user; the caller
    decides how to display it.
    """
    if not project_name or not project_name.strip():
        raise ProjectError("A project name is required.")
    if isinstance(source_roots, str):
        # One root passed as a bare string stays the ordinary case.
        source_roots = [source_roots]
    roots = normalize_roots(source_roots)

    project_name = _INVALID.sub("_", project_name.strip()).strip(" .")
    if not project_name:
        raise ProjectError("That project name contains no usable characters.")

    os.makedirs(projects_dir, exist_ok=True)
    project_folder, project_name = unique_project_folder(projects_dir,
                                                         project_name)

    os.makedirs(project_folder)
    os.makedirs(os.path.join(project_folder, "Runs"))

    settings = build_settings(project_name, roots)
    settings_path = write_settings(
        os.path.join(project_folder, "settings.json"), settings)

    try:
        result = fo_db.init_project(
            project_folder, project_name, source_roots=roots,
            **({"app_version": app_version} if app_version else {}))
    except Exception as exc:                                    # noqa: BLE001
        raise ProjectError(
            "The project folder and settings were created, but its database "
            "could not be initialised:\n  %s\n\n%s: %s"
            % (project_folder, type(exc).__name__, exc))

    # fo_db is the authority on the schema version it just applied.
    # Restating a number here is how a v5 database once announced
    # itself as v2.
    schema_version = (result or {}).get("schema_version")

    return {"project_name": project_name, "project_folder": project_folder,
            "settings_path": settings_path, "source_roots": roots,
            "target_path": roots[0], "schema_version": schema_version}
