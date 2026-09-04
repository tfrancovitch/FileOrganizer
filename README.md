# The File Organizer — Phase 1

**A Windows desktop tool that builds a complete, trustworthy inventory of your
files and tells you which ones are genuine duplicates.**

Phase 1 is **observational**. It looks, measures and records. It never renames,
moves, deletes or edits anything you point it at.

---


## B6.1 — A–F reconciliation

B6.1 is the reconciliation build produced from the first six B5 adversarial
attacks. B6 already addressed the largest scalability and determinism findings;
B6.1 folds Correctness, Completeness, Simplicity and Efficiency into the same
architecture instead of layering patches on top.

Headline changes beyond B6:

- **Current state is now the input contract for every downstream stage.** An
  unchanged file does not create another history row, but it still participates
  in hashing, analyzers, exports and estimates.
- **Location, physical object and content are distinct.** Physical identity,
  hard-link count, allocated size and reparse metadata are captured where the
  environment exposes them.
- **Selective no-hash is explicit.** `UniqueBySize` means intentionally not
  hashed, not failed or unknown.
- **The retired CSV/checkpoint analyzer pipeline is gone.** The database-backed
  in-process analyzer engine is the one execution architecture.
- **Known correctness defects are closed.** Scan/export extension semantics
  agree; unbound exports fail visibly; valid UTF-8 is decoded as UTF-8 before
  probabilistic fallback.
- **Measured avoidable work was removed where it did not buy another virtue.**
  Hash rows use one UPSERT, stage exports are scoped, and content lookup uses
  the project-scoped index.

Full finding-by-finding decisions:
[`B5_A-F_RECONCILIATION.md`](B5_A-F_RECONCILIATION.md).

Build changes and verification:
[`CHANGELOG-B6.1.md`](CHANGELOG-B6.1.md).

**Upgrading.** Existing B6 projects migrate automatically from schema 6 to
schema 7 after a pre-migration backup. Pre-B6 facts that cannot be reconstructed
truthfully remain NULL/unverified rather than being invented.

**Internal verification.** `python Scripts/b6_regression.py` runs the B6.1
regression suite. These measurements are build-machine evidence, **not native
Windows acceptance**. Windows/NTFS/long-path/cloud/network verification remains
a release gate.

## What it does

- Walks one or more source folders and records every file it finds
- Captures size, timestamps, attributes, path depth and Windows metadata
- Computes SHA-256 content identity
- Identifies duplicates as an **inventory fact** — by content, not by name or size
- Runs optional analyzers for images, PDFs, Office documents, RAW photos,
  audio, video, text and archives, and extracts document text to disk
- Stores everything in a project-local SQLite database
- Exports CSV inventories and readable reports

## What it deliberately does not do

Deleting duplicates, moving files, renaming, reorganising folders, tagging, and
AI-assisted sorting are **later phases**. Phase 1 exists so that when those
arrive, they act on facts that were established carefully.

---

## Requirements

> **Target operating contract, pending native-Windows acceptance.** The build
> has been internally verified off-Windows; the claims below are what B6.1 is
> designed to support and must be verified on Windows before production use.

| | |
|---|---|
| Operating system | Windows 10 or 11 |
| Python | **3.11 or newer** (installed automatically if missing) |
| PowerShell | 5.1, which ships with Windows |
| Disk | Roughly 1.7 GB per million files, for the database and exports (measured on real data; lighter on corpora of larger files) |

Long paths work **without** enabling `LongPathsEnabled` — paths beyond 260
characters are handled at the point of file access.

### Optional packages

Analyzers need third-party libraries. Without one, that category reports as
failed and everything else continues.

| Category | Packages |
|---|---|
| Images | `Pillow`, `imagehash`; `pillow-heif` for HEIC/HEIF |
| PDF | `pypdf`, `pdfplumber` |
| Office | `python-docx`, `openpyxl`, `python-pptx`, `olefile` |
| RAW photos | `exifread` |
| Audio | `mutagen` + **ffprobe** on PATH |
| Video | **ffprobe** on PATH |
| Text | `chardet` |
| Archives | ZIP uses stdlib; `py7zr` for `.7z` |

`Scripts\Install-Dependencies.ps1` installs the Python packages. **ffprobe**
comes with FFmpeg and must be installed separately.

---

## Installation

1. Unzip the folder anywhere you can write to — Documents is fine. Program
   Files is not recommended, because projects are created alongside the app.
2. Double-click **`TheFileOrganizer.bat`**.

That is the whole installation. The batch file finds Python, installs it
per-user if it is missing or too old, and launches the dashboard.

If something looks wrong, run the health check:

```
python Scripts\self_check.py
```

It reports your Python version, whether the window toolkit is present, which
analyzer packages are installed, whether ffprobe is on PATH, and whether a
database can actually be created on this machine.

---

## Using it

### 1. Create a project

Choose **Create a new project**, pick the folder you want to inventory, and
name it.

One folder is the ordinary case. If you want a single project to cover more
than one location — say `C:\\Documents` and `E:\\Photos` — browse to each and
press **Add**; **Remove** takes one back off the list. They become one project
with one database, and duplicates are found *across* them.

You get:

```
Projects\<YourProject>\
    settings.json              project settings
    project.json               project identity
    Database\FileOrganizer.db  the authoritative record
    Runs\                      one folder per run
```

**One project = one isolated database.** Projects never read each other's data,
and content identity is never merged across them.

### 2. Pre-Scan

Walks your source folder, records every file, groups files by exact size to
find duplicate *candidates*, and samples a few files to estimate how long the
next step will take.

Nothing is hashed in full yet.

### 3. Choose a run type

**Duplicate Run** — the normal choice. Only files that share an exact size can
possibly be duplicates, so only those are hashed. Small files are settled with
a single 64 KB read; larger ones are escalated to a full SHA-256 only when
their first 64 KB already match. Most projects hash a small fraction of their
bytes.

**Full Run** — a complete SHA-256 for *every* file. Slower, and the right choice
when you want a full content record rather than only the duplicates.

> A partial hash is treated as complete content identity **only** when the
> whole file fits inside the 64 KB window. For anything larger, matching first
> bytes is a screening result and never proof.

### 4. Analyzers (optional)

Pick any categories you want. Each runs independently — one failing does not
stop the others, and a category with no applicable files is a success, not an
error.

### 5. Results

Under `Projects\<YourProject>\Runs\<timestamp>\`:

| Folder | Contents |
|---|---|
| `Inventory\` | CSV exports — inventory, duplicates, per-analyzer results |
| `Reports\` | Readable summaries |
| `Logs\` | Per-file errors |

The database is the authoritative record. **Every CSV and report is rendered
from it**, so they can be regenerated and cannot silently disagree with it.

---

## Source safety

While inventorying, hashing and analysing, the program **never**:

- renames, moves or deletes a source file
- alters source contents, attributes or embedded metadata
- restores timestamps

Reading a file's contents may cause Windows to update its **LastAccessTime**.
That is the operating system's doing. The program does not attempt to put it
back, because writing a timestamp would itself be the modification this rule
exists to prevent.

Content extraction writes `.txt` files, but only into the run's own output
folder — never next to your originals.

---

## Known limitations

- **Windows only.** Paths, metadata and the long-path handling are Windows-specific.
- **No pause/resume.** A run can be stopped, but restarts from the beginning.
- **Cloud/offline files.** Hashing a placeholder may trigger a download. The
  program can skip cloud-only files where the workflow offers it.
- **ffprobe is separate.** Audio and video need FFmpeg installed and on PATH.
- **Legacy Office formats.** `.doc`, `.xls` and `.ppt` are detected but not
  deeply analysed; the modern XML formats are.
- **Analyzers depend on third-party libraries** and inherit their limits — a
  malformed PDF is reported as an error on that file, not repaired.
- **Duplicate groups are facts, not advice.** Phase 1 tells you what is
  identical. Deciding what to do about it is a later phase.

---

## Troubleshooting

**Nothing happens when I double-click the .bat**
Run it from a terminal to see the error, then `python Scripts\self_check.py`.

**"Python was not found"**
The batch file installs Python per-user. If it is blocked, install Python 3.11+
from python.org, ticking *Add Python to PATH*.

**A category reports as failed**
Almost always a missing package. `self_check.py` names it and the exact
`pip install` line.

**Audio or video always fails**
`ffprobe` is not on PATH. Install FFmpeg.

**A run stopped partway**
Its stages are recorded in the database with what failed and why. Re-run it;
existing results are not lost.

**Office analysis failed on old .doc / .xls / .ppt files**
Those are the legacy binary formats. They are detected and recorded, but not
deeply analysed — the modern XML formats (`.docx`, `.xlsx`, `.pptx`) are. An
error on one of these is expected, not a fault.

**Access denied on some files**
Recorded explicitly, per file, with no invented data, and the rest of the run
continues. Some system and cloud-only files simply cannot be read.

---

## Where things live

| | |
|---|---|
| Application | the folder you unzipped |
| Projects | `Projects\` beside the app |
| Database | `Projects\<name>\Database\FileOrganizer.db` |
| Run output | `Projects\<name>\Runs\<timestamp>\` |
| Application log | `Logs\app.log` |

To back up a project, copy its whole folder. To move the app, move the folder —
nothing is written to the registry or to `AppData`.

See **`ARCHITECTURE.md`** for how it works internally.
