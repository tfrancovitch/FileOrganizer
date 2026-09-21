# The File Organizer

**A Windows desktop tool that builds a complete, trustworthy inventory of your
files, tells you which ones are genuine duplicates, and answers questions about
what is inside them.**

Phases 1 and 2 are **observational**. They look, measure, read and record. They
never rename, move, delete or edit anything you point them at, and never open a
cloud-only file. Phase 3 (B8 onward) **decides**: it records which copies to
keep, as an inspectable record of what should happen. Carrying decisions out is
a later phase; nothing in B8 changes a file either.

---

## B9 — Phase 3, Build 3: review routing

B9 tells you where each duplicate group's attention is owed. On the Decide
page every group's Status is now its route — **Conflict**, **Needs
revalidation** (a decision's evidence moved since it was made), **Blocked**
(the evidence cannot support a safe decision yet), **Deferred**, **Ready for
plan**, **Resolved**, or the ordinary queue — with every condition that
applies listed beside it, and the **Show** box filters by route or by lens.
**Defer…** now takes a return trigger (a date, an evidence change, the
source coming back, fingerprints becoming current); **Skip** (key `s`) is a
different thing and only records that you passed by; **Restore** brings a
parked group back; **Confirm decisions** re-records decisions whose evidence
moved. A detector runs when the page opens and after a scan or fingerprint
run, recording what it found — it never changes a decision. An empty queue
says *Currently clear*: new evidence can reopen it. `CHANGELOG-B9.md` has the
detail; `PHASE3_BUILD3_HANDBACK.md` reports against the Build 3 handoff.

## B8 — Phase 3 begins: duplicate decisions

B8 is the first Phase 3 build (Builds 1–2 of the Phase 3 plan: core
persistence and exact-duplicate decisions). A project now has a **Decide**
page: every current exact-duplicate group with what Phase 2 knows about it
(copies, physical copies, hard-link aliases, size, roots) and what you have
decided (Keep, Keep all, Set canonical, Mark redundant, Defer). Policies
protect a source root or folder, or prefer / avoid one for the
recommendation. Protection is a hard constraint: a protected copy cannot be
marked redundant until you override the protection in its own dialog, with
a reason. Every decision is a record; changing your mind is a new record;
Undo withdraws — nothing is ever deleted, in the project or on disk. The
project summary shows how much of the duplicate question is decided and how
much space a plan could count on. `CHANGELOG-B8.md` has the detail;
`PHASE3_HANDBACK.md` reports against the Phase 3 handoff.

## B7.2 — the adversarial round closed

B7.2 fixes the six defects the Master Matrix corpus had recorded as DEFECT
cases: files under an unlistable folder marked missing, a file symbolic link
hashed as its target, a file rewritten between listing and hashing recorded
as an inconsistent pair, a garbage PDF date failing the whole analysis, a
colourised log refused as binary, and a binary archive member decoded as
text. Each case now asserts what the matrix expects. One stays by decision
(A-014, the case-only pair). `CHANGELOG-B7.2.md` has the detail; defects
46–51 in `..\PROJECT_PLAN.md` §4. With it, Phase 2's last part — the
adversarial round (P2.13) — is done.

## B7.1 — the first fix build of the adversarial round

B7.1 fixes what the P2 stress test — the first project with two source
roots, the test corpus beside the real OneDrive corpus — found: the second
root's ingest marked every file of the first root missing, so every later
pass ran over half the project while every report said COMPLETED. Fixing
that uncovered twelve more, from a duplicate stage that always said "no
applicable files" to re-scans that exported three-row CSVs, a time
estimate thirteen times the run and a New Project form that hid the folders
it had added. `CHANGELOG-B7.1.md` lists them; they are defects 33–45 in
`..\PROJECT_PLAN.md` §4.

## B7 — Phase 2, entering adversarial testing

B7 is Phase 1 (Observe) and Phase 2 (Understand) as one product with one
version. It adds the one window and the project summary, the Files page with
sorting, filtering, columns and export, 31 standard reports and saved queries,
text extraction from 113 formats with OCR of scans, honest measured estimates,
a Cancel that keeps what it did, and — on every done line of the summary — a
link to do it again after the folder changed. `CHANGELOG-B7.md` has the whole
of it; `..\PROJECT_PLAN.md` is the single source of truth for where the
project stands.

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
- Records your decisions about duplicate copies — keepers, the canonical,
  redundant candidates, protection — as an append-only, attributable record
  (Phase 3, from B8)

## What it deliberately does not do

Deleting duplicates, moving files, renaming, reorganising folders, tagging, and
AI-assisted sorting are **later phases**. Phase 1 exists so that when those
arrive, they act on facts that were established carefully; Phase 3 exists so
that they act on decisions a person made and can inspect first. B8 records
decisions and never acts on them.

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
| PDF | `pypdf`, `pdfplumber`; `pypdfium2` (installed with pdfplumber) reads text 25-30x faster |
| Office | `python-docx`, `openpyxl`, `python-pptx`, `olefile` |
| Text extraction | the PDF and Office packages, `chardet`; `xlrd` for `.xls` text; `winocr` for OCR of scans (Windows' own engine); `py7zr` for the documents inside `.7z`. Everything else — legacy Office, RTF, HTML, OpenDocument, EPUB, email, code, zips, WordPerfect, OneNote — needs nothing extra |
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

One window. `TheFileOrganizer.bat` runs the startup checks, then opens with
nothing selected: **Open Project** and **New Project** are the first two buttons
in the side panel, **Options** and **Exit** the last two. Every button that does
work starts at once: the run measures its own estimate first and shows it, asks
"Begin now?" only if that estimate is over 15 minutes, owns the window while it
runs, and has a **Cancel** that stops between files and keeps everything already
done.

### 1. Create a project

Press **New Project**, pick the folder you want to inventory, name the project
(or leave it blank to auto-name), and press **Create project and run the
Pre-Scan**.

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

### 3. The three doors

After the Pre-Scan, three choices — none of them final; all three stay
available from the project page. Each shows its estimate; **Begin Scan** begins.

**Find My Duplicates** — answers the duplicate question *completely*. Only
files that share an exact size can possibly be duplicates, so only those are
opened. Small files are settled with a single 64 KB read; larger ones are
escalated to a full SHA-256 only when their first 64 KB already match. A file
whose size nothing else has is proven not a duplicate without ever being
opened — that is a finding, not a gap.

**Full Fingerprinting** — also finds every duplicate, and gives *every* file a
verifiable content identity. That identity is what later lets a file be
verified after a move, and a change be detected on a re-scan. The difference
from the first door is not thoroughness.

**Go to the project** — everything the Pre-Scan already knows.

> A partial hash is treated as complete content identity **only** when the
> whole file fits inside the 64 KB window. For anything larger, matching first
> bytes is a screening result and never proof.

### 4. The project summary

The landing page for an open project: source folders, total files and size,
fingerprints, duplicates and reclaimable space, files by type with how many
have been analysed, and the text state (not extracted / extracted / index out
of date / indexed). A button sits in the first column of any line a run would
change; where the work is done, the word that says so (SCANNED, COMPLETE,
ANALYZED, INDEXED) sits there instead, with a link beside it to do it again:
**Scan again** after the folder changed (new files observed, vanished ones
marked missing, everything already collected kept), **Fingerprint again** to
re-read every file, **Analyze again** on a bucket, **Extract again** after a
change on the Options page. A re-scan makes the summary say plainly what it no
longer knows — a file that changed has no current fingerprint or analysis until
the run that examines it again.

**Files** lists every current file: click a heading to sort, right-click one to
filter by that column or choose columns, page with Previous/Next, and export
everything the question covers to CSV. **Reports** are the 31 standard
questions that ship with the product; a **saved query** is one you composed on
the Files page and named. Both re-run against current evidence, and both
export.

The summary's first column is the action: **Analyze** on a bucket that needs
it, or the word ANALYZED; **Analyze all** runs every bucket that needs it, text
extraction excluded, because extracting and indexing are the slow ones and each
can be skipped. Each analyzer runs independently — one failing does not stop
the others, and a bucket with no applicable files is a success, not an error.

### 4a. Decide (from B8)

**Decide** in the side panel, **Review duplicates** on the summary, or
**Review this duplicate group** in a file's Details pane. The left list is
every current exact-duplicate group: copies, physical copies (a hard link is
one physical copy under two names), aliases, size, the potential reclaim
Phase 2 measured and the reclaim a plan could count on now, roots, the
group's **route** (its Status — see below), the conditions behind it, and
the canonical. Click a heading to sort. **Show** picks a route — Queue (the
default: groups awaiting an ordinary decision), Conflicts & Exceptions,
Needs Revalidation, Blocked by Evidence, Deferred / Snoozed, Ready for Plan,
Resolved, All — or a lens over the groups: High Reclaim, Cross-Root, Unknown
Physical Identity, Hard-Link Aliases, Filename Divergence, Extension
Divergence, Policy Tie. When the Queue is empty the page says *Currently
clear* — not complete, because a rescan can reopen it. The right side is
the selected group: each copy's status — Protected, Keeper, Redundant
candidate, Undecided — with what decided it, the route's explanation in
file terms, and the actions:

| Action | What it records |
|---|---|
| **Keep** | this copy stays (a hard constraint) |
| **Keep all** | every copy of the group stays; zero reclaim is a valid answer |
| **Set canonical** | which copy represents the group — always a keeper, never "the original" |
| **Mark redundant** | this copy is a candidate for a later removal plan |
| **Defer…** | "not now", with a return trigger: indefinitely, until a date, until the evidence changes, until the source is available, until fingerprints are current |
| **Restore** | (on a parked group) back to the queue by hand |
| **Skip** | key `s`: records that you passed by, changes nothing, moves to the next group |
| **Confirm decisions** | (when the evidence moved) re-records the drifted decisions on the evidence as it is now |
| **Override protection…** | its own dialog: names the protection rule, needs a reason |
| **Undo selected** | withdraws a decision; the record stays, marked withdrawn |

A group's route is derived every time the page draws, from the evidence and
the record, in this order of precedence: a **Conflict** (a redundant mark on
a protected copy, a canonical that is no longer a keeper, every copy marked
redundant, or — as an exception — two policies at one tier that disagree)
comes first; then **Needs revalidation** — a decision whose copies have been
observed again since, or whose group gained or lost a member (the decision
itself is untouched; confirm it, or undo it); then **Blocked** — physical
identity unknown, a fingerprint that predates the copy's current
observation, or a root whose latest walk was interrupted or unavailable;
then **Deferred**; then **Ready for plan** and **Resolved**; otherwise the
ordinary queue. A detector runs when the page opens and after a Scan again,
Find My Duplicates or Full Fingerprinting run, and records what it found as
routing events (never a decision): what changed, what is blocked and why,
and when a snooze elapsed.

**Policies…** protect a source root or a folder (a hard constraint — a
protected copy cannot be marked redundant until the protection is overridden
for that copy), or prefer / avoid a root or folder. Preferences only shape
the recommendation shown as "(policy)"; they never decide for you. Retiring
a policy is a new version; its history stays. **Export journal…** writes the
whole decision record as text into the project's `Exports\` folder.

A group's redundant candidates count towards plan-eligible reclaim only when
the group has no conflict — a redundant mark on a protected copy, a copy that
is both canonical and redundant, or every copy marked redundant (the product
never removes the last copy). The page says which.

### Stopping a run

Cancel is honoured between files, never during one, so nothing is left
half-written. What was done is kept and recorded; what was not is spelled
out — the summary says "at least N files" after a stopped walk, "not fully
answered" after stopped fingerprinting, "N analysed" of M after a stopped
bucket. Running the stage again starts from its first file.

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
- **No pause/resume.** A run can be stopped and keeps what it did, but running
  the stage again restarts from its first file.
- **Cloud-only files are never opened.** A OneDrive placeholder is recorded
  from its directory entry and skipped by every stage that reads bytes, with
  `skipped_cloud_only` written down; nothing is downloaded.
- **ffprobe is separate.** Audio and video need FFmpeg installed and on PATH.
- **Legacy Office formats.** `.doc`, `.xls` and `.ppt` are read for their
  text (straight from the OLE2 container; Excel through `xlrd`) but the Office
  *analyzer* describes only the modern XML formats.
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
| Decision record | tables `p3_*` in the project database (decisions, policies, review events); `Exports\DecisionJournal.txt` when exported |
| Application log | `Logs\app.log` |

To back up a project, copy its whole folder. To move the app, move the folder —
nothing is written to the registry or to `AppData`.

See **`ARCHITECTURE.md`** for how it works internally.
