# The File Organizer

**A Windows desktop tool that builds a complete, trustworthy inventory of your
files, tells you which ones are genuine duplicates, and answers questions about
what is inside them.**

Phases 1 and 2 are **observational**. They look, measure, read and record. They
never rename, move, delete or edit anything you point them at, and never open a
cloud-only file. Phase 3 (B8 onward) **decides**: it records which copies to
keep, as an inspectable record of what should happen. Carrying decisions out is
a later phase; nothing in B8–B18 changes a file either — not even the plan B15 builds, or
the metadata you ask for on the Metadata page.

---

## B18 — Phase 3: the closeout round

- **Re-analysing your files no longer sends your metadata decisions back for
  review.** A decision returns to the Metadata queue only when what you
  decided about changed: the file's content, the field's value, or whether it
  can be read. When several do need another look, the queue's **Confirm...**
  re-records the same decisions on the evidence as it is now, in one batch you
  can undo.
- The **Analyze** stage now records whether a PDF or an Office document is
  **encrypted**, including old `.doc`, `.xls` and `.ppt` files (read from
  their headers only). An encrypted file is blocked from any metadata write.
- The **Decide** page can **Export current state...**: a spreadsheet of what
  your decisions say now, each line with the decisions it rests on.
- A plan's fingerprint is now written down exactly
  (`Docs\Specifications\PLAN_FINGERPRINT_SPEC.md`), so the phase that acts on
  a plan can check it without this program's code.

Still nothing changes a file. `CHANGELOG-B18.md` has the detail;
`PHASE3_B18_HANDBACK.md` reports against the B18 handoff;
`Docs\Handoffs\P3_CLOSEOUT.md` is the Phase 3 closeout assessment (a draft for
your decision).

## B17 — Phase 3: the Metadata page

**Metadata** in the side panel opens the **Metadata Review queue**: the
metadata questions that need you. **Metadata for this file** (in a file's
Details) and an Update Source Metadata step on the Plan page open one file's
metadata. There you can:

- see each field as the project's evidence reads it;
- record what you want (a value, a clear, or *leave unchanged*), or defer it;
- undo;
- keep the project's own tags, category, note, priority and owner for the file.

None of this reads or writes the file.

The **Analyze** stage now also records whether a PDF or an Office document
carries a digital signature. A signed, certified, encrypted or
password-protected file is blocked from any metadata write. Metadata steps in
a plan also note when a file would leave its duplicate group, and warn when
the same plan removes that file. `CHANGELOG-B17.md` has the detail;
`PHASE3_B17_HANDBACK.md` reports against the B17 handoff.

## B16 — Phase 3, Build 9: project metadata and desired source metadata

B16 is the **record-keeping** half of metadata. It is not an editor, and
no screen for it ships this round. What it adds:

- **What each file's metadata says now**, with an honest state: a value;
  *no value*; *unknown* (never read); *unsupported* (nothing reads that field
  for that kind of file); *unavailable* (not present, or cloud-only); *stale*
  (the file changed since); *conflicting* (two readings disagree, both shown,
  no winner picked). It reads what Phase 1/2's analyzers already recorded:
  PDF and Office title, author and creation date; RAW capture date; audio title.
- **What you want a field to say** later (*set*, *clear*, or *leave unchanged*).
  It is a decision like any other: attributed, superseded by a later one, never
  edited. A desired value becomes an **Update Source Metadata** step in a plan,
  and it stays **blocked**: no metadata writer exists, and none is certified in
  this build. A signed or certified file, or a hard-linked one, is blocked on its
  own, with or without a plan.
- **The project's own metadata** (tags, a category, a note, an owner, a review
  priority, why a file is kept). It is stored in the File Organizer project,
  never written to the file, and never a plan step.

`CHANGELOG-B16.md` has the detail; `PHASE3_B16_HANDBACK.md` reports against
the Build 9 handoff.

## B15 — Phase 3, Build 8: the Plan Builder

The **Plan** page turns the decisions you have already made into a plan:
*remove this copy*, *keep that one (and why)*. It shows what each step
relies on, what the project's evidence says about it, and what blocks it.
Then you can freeze it with a fingerprint. It carries nothing out and never
reads or changes a file. A locked plan records what you reviewed, not the
state of the disk, and whatever carries it out one day must check the files
again first. Where your decisions disagree about an image (one pair gives it
up while another keeps it), the plan shows that removal as **blocked** and
names both decisions. It never picks a side. If a decision a locked plan
rests on changes later, the plan is marked **obsolete**, with the reason; it
is never quietly rewritten. `CHANGELOG-B15.md` has the detail;
`PHASE3_B15_HANDBACK.md` reports against the Build 8 handoff.

## B14 — Phase 3 maintenance: a faster Similar Images page

Once a project holds tens of thousands of decisions (a few big bulk batches
do that), every answer on the **Similar Images** page had slowed to several
seconds. B14 makes the page read only what each answer changed: on a copy of
a 41,000-file collection with 76,000 decisions, an answer now redraws in about
a quarter of a second (was 3.2 s), a Survey Mode group action in 0.6 s (was
3.7 s), and the page opens in 4.7 s (was 8.8 s). What the page shows is
unchanged — checked, after every step, against loading everything afresh.
Also: the fixture lab gains **F16**, the "A~B and B~C, but no A~C" shape the
research uses to state that nothing is inferred between two images that are
not a candidate pair. `CHANGELOG-B14.md` has the detail;
`PHASE3_B14_HANDBACK.md` reports against the handoff.

## B13 — Phase 3, Build 7: Survey Mode

Some look-alikes come in small crowds — three to eight copies of one picture
scattered over several folders. **Survey Mode…** on the **Similar Images**
page shows the crowd the pair in view belongs to, all at once: tiles with
linked zoom, and per image **Keeper**, **Canonical**, **Select**,
**Compare** (the page's side-by-side view) and **Exclude** (out of the view
only — it records nothing). Four group controls — **Keep All**, **Mark
Remaining Equivalent**, **Mark Selected as Related Variant**, **Defer
Group** — each preview first and then record, pair by pair, on the
candidate pairs among the images in view; nothing is ever recorded between
two images that are not a candidate pair, and a decision you already made
is left alone. A crowd larger than eight is reported (its size and folders),
not squeezed in. Also in B13: a scan or a re-hash between a duplicate-group
bulk preview and its commit now refuses the commit, as it already did for
pairs. `CHANGELOG-B13.md` has the detail; `PHASE3_BUILD7_HANDBACK.md` reports
against the Build 7 handoff.

## B12 — Phase 3, Build 6: bulk review of similar images

A folder of variations — many images from one prompt or one shoot — makes
thousands of look-alike pairs, too many to answer one at a time. B12 lets
you answer them once: on the **Similar Images** page, **Bulk action…**
applies *Related variant, keep both*, *Equivalent copy, keep both* or
*Different* to the pairs listed now — all of them, those with both images
in one folder (subfolders included or not), or the ones you checked —
after a **preview** that says, pair by pair, what would be recorded, what
already holds, what an earlier decision of yours keeps, what is in
conflict and what the evidence blocks. Each pair gets exactly what one key
would record on it. The batch covers the pairs listed now; a picture added
later is **not** swept in — it waits in the queue for you. **Batches…**
undoes a whole batch. `CHANGELOG-B12.md` has the detail;
`PHASE3_BUILD6_HANDBACK.md` reports against the Build 6 handoff.

## B11 — Phase 3, Build 5: similar images, one pair at a time

The image analyzer has always stored a perceptual hash for every picture;
B11 compares them. At the end of every image analysis the hashes are
paired — two images whose pHash values differ in at most 8 of their 64
bits become a **candidate pair** — and a pair keeps its identity (its two
files) through every later analysis, so a pair you have answered never
comes back as new. The side panel's **Similar Images** page (and **Compare
images** on the summary) shows one pair at a time: two large previews with
linked zoom and pan, Fit and 100%, each image's resolution, format, size,
dates and path, and the measurement named for what it is ("Perceptual
hash — Hamming distance 4 of 64 bits"), never a percentage. You say what
the two are to each other — **Equivalent** (prefer left, prefer right or
keep both), **Related variant** (keep both: a crop or an edit is not a
copy), **Different**, or **Defer** — mostly with one key, and **U** undoes
the last answer. Each answer is about that pair only: nothing is inferred
for a third image. Byte-identical pairs stay out of the queue (the
duplicate review answers them) but can still be opened and compared.
`CHANGELOG-B11.md` has the detail; `PHASE3_BUILD5_HANDBACK.md` reports
against the Build 5 handoff.

## B10 — Phase 3, Build 4: bulk decisions and the policy workflow

B10 lets one decision reach many groups at once, safely. The Decide page's
group list has a **check column** (click it, or press Space); once
something is checked, **Bulk action…** appears. It offers Keep all, Accept
the recommendation (the policy-suggested canonical, with the other undecided
copies marked redundant), Keep copies under a folder, Mark copies under a
folder redundant, and Defer — over **N checked groups**, or over the **N
current matches** of the Show filter (frozen at commit: a later match is
not included), or, for the folder actions, as **a reusable policy instead**,
which is the opposite thing and is named as such. Nothing is recorded until
you **Preview**: the preview says how many would receive a decision, how
many already satisfy it, how many carry an explicit decision it preserves,
how many would conflict and how many the evidence blocks — with examples —
and the commit records exactly that. **Batches…** lists every batch with
what became of each member and undoes a whole batch in one click. **Add
policy** now previews what the policy covers and changes today and says
plainly that future matches will be evaluated against it, before it can be
created. `CHANGELOG-B10.md` has the detail; `PHASE3_BUILD4_HANDBACK.md`
reports against the Build 4 handoff.

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
  (Phase 3, from B8); routes each group's attention (from B9); applies one
  decision to many groups after a preview, as a batch you can undo whole
  (from B10)
- Compares every analyzed picture with its look-alikes by perceptual hash and
  records, pair by pair, what you say they are to each other (from B11) — or,
  after a preview, over a whole folder of them at once (from B12)

## What it deliberately does not do

Deleting duplicates, moving files, renaming, reorganising folders, tagging, and
AI-assisted sorting are **later phases**. Phase 1 exists so that when those
arrive, they act on facts that were established carefully; Phase 3 exists so
that they act on decisions a person made and can inspect first. Phase 3
records decisions and never acts on them.

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
a policy is a new version; its history stays. Adding a policy previews
first: what it covers now (every file under the root or folder, and how
many of those are in duplicate groups), what changes today, and the
statement that **future matching evidence will be evaluated against it** —
a file that appears there later is covered too, with no decision recorded
for it. **Export journal…** writes the whole decision record as text into
the project's `Exports\` folder.

**Bulk (from B10).** Check groups in the list (click the first column, or
press Space on the focused row; **Check all shown** / **Clear**); the
letter keys still act on the selected row only. **Bulk action…** appears
once something is checked:

| Action | What it records, per group in scope |
|---|---|
| **Keep all copies** | Keep all — unless the group carries a redundant mark, which is preserved |
| **Accept the recommendation** | the policy-suggested copy as canonical, and (unless switched off) the other undecided copies as redundant candidates; protected and kept copies stay |
| **Keep copies under a folder** | Keep on every copy under the folder |
| **Mark copies under a folder redundant** | a redundant mark on every copy under the folder — never on a protected copy, a kept copy, the canonical, or the last copy of a group |
| **Defer** | a deferral with one return trigger, on every group |

The scope is **N checked groups** (the default), **N current matches** of
the Show filter — frozen when you commit; a group that comes to match the
filter later is not included — or, for the folder actions, **a reusable
policy instead** (Protect folder / Avoid folder), which affects future
matches too. **Preview** before anything is recorded: how many would
receive a decision, already satisfy it, carry an explicit decision the
batch preserves (a batch never overwrites one), would conflict (not
recorded; those need you), or are blocked by the evidence — with examples.
The commit records exactly what the preview showed, as one batch; the
window says what was recorded and what was left as exceptions.
**Batches…** (also on the summary) lists every batch, its members and what
became of each, and **Undo batch** withdraws everything the batch recorded
that still stands — a decision you have since changed by hand is left alone.

A group's redundant candidates count towards plan-eligible reclaim only when
the group has no conflict — a redundant mark on a protected copy, a copy that
is both canonical and redundant, or every copy marked redundant (the product
never removes the last copy). The page says which.

### 4b. Similar images (from B11)

**Where the pairs come from.** Analyzing **Pictures** stores a perceptual
hash per image; at the end of that analysis the program compares them and
records every pair within 8 of 64 bits (pHash) — no image is opened for
this, and a cloud-only picture was never hashed. A crowd of near-identical
images keeps each image's 10 nearest look-alikes and says how many images
it trimmed. A project analyzed before B11 gets its pairs the first time the
page opens. **Find similar images again** compares afresh on request.

**The page.** The list on the left is the chosen **Show**: the **Visual
Similarity Review** (pairs no answer has settled, the closest first), the
same routes the duplicate review has — Conflicts & Exceptions, Needs
Revalidation, Blocked by Evidence, Deferred / Snoozed, Ready for Plan,
Resolved — and lenses: Strong near-copies (within 5 bits), Across folders
(where copies kept twice usually are — a folder of generated variations is
a crowd of look-alikes inside one folder), Cross-root pairs, Exact copies
(compare on request), All pairs. A long list shows 500
at a time around the pair in view; **N** / **P** move through all of it. On
the right: the two images, linked (zoom with the wheel or + / −, drag to
pan — both sides show the same region of their own image, so a resized copy
lines up; **K** unlinks, **Y** re-syncs), **F** fit, **H** 100% (actual
pixels, full resolution), **X** swaps the sides (presentation only). Under
each image: its name, folder, resolution, format, size, modified time and
the capture date the file itself records.

| Key | Records |
|---|---|
| **A** / **L** | Equivalent, prefer left / right — two decisions: the relationship, and which copy the pair keeps (the other becomes a redundant candidate) |
| **E** | Equivalent, keep both |
| **V** | Related variant, keep both — never ordinary redundant-copy cleanup |
| **D** | Different — a false positive for this pair only (any retention on it is withdrawn) |
| **[** / **]** | the left / right image as canonical (with Keep both: the representative) |
| **W** | Defer… with a return trigger (including *until a preview is possible*) — or Restore |
| **S** | Skip — an audit row, nothing else |
| **U** or Ctrl+Z | undo the last answer (its decisions withdrawn; nothing it replaced comes back) |

After an answer the page moves to the next pair. Progress is the record
itself: close the window and the queue is where you left it.

**What it will not do.** Nothing is inferred along a chain: if A~B and B~C
are equivalent, nothing is said about A~C — and if one pair keeps an image
another pair gives up, the first pair is in **conflict** until you decide
the missing pair or keep both. A protected image or one you explicitly
kept cannot be the one a pair gives up (a conflict, not an effect). A
cloud-only image is never downloaded to be shown; the pair is blocked
until it is local. No image is deleted, moved or edited from here.

**Exact copies.** Byte-identical pictures are exact duplicates: their pair
is set aside (the Duplicate decisions page answers them). Show *Exact
copies* — or **Compare images…** on the Duplicate decisions page — opens
them side by side anyway; **[** / **]** there set the exact-duplicate
group's canonical.

**Bulk (from B12).** Check pairs in the list (click the first column, or
Space on the focused row; **Check all shown** / **Clear**) — the letter keys
still act on the pair in view only. **Bulk action…** offers:

| Action | What each pair in scope receives |
|---|---|
| **Related variant, keep both** | what **V** records: related variant + keep both |
| **Equivalent copy, keep both** | what **E** records: equivalent copy + keep both |
| **Different** | what **D** records: different |

Apply to: the **N checked pairs**, the **N pairs listed now** (the Show
list), or the pairs listed now **with both images in a folder** — type it,
**Browse…**, or **This pair's folder**; *Include subfolders* is on by
default (off: both images directly in that folder). Exact copies are never
a bulk population. **Preview** first: how many pairs would receive the
decision, already say it, carry an earlier decision of yours that the batch
keeps (another relationship, a preferred copy, a retention row, a
deferral), are in conflict (for example the chain guard), or are blocked
(an image cloud-only, changed since it was compared, or a decision whose
evidence moved) — with examples; **Save the preview list…** writes every
pair and its reason to a CSV in the project's `Exports\` folder. **Record**
writes exactly the preview — and refuses if anything was recorded, re-analyzed
or rescanned since, asking for a fresh preview. The batch is frozen: a pair
found later is not in it. **Batches…** lists every batch (pairs named as
*a.jpg ~ b.jpg*) and undoes a whole batch; a pair you have changed by hand
since keeps your change.

**Survey Mode (from B13).** **Survey Mode…** opens the cluster of the pair in
view: the images a chain of still-open pairs in the current Show list joins
together. It shows clusters of 3–8 images; a pair joined to no other is an
*isolated pair* (answer it here), and a cluster of more than eight is
reported with its size and folders (answer it pair by pair, or with Bulk
action… where it sits in one folder). The window's first line says how many
of each the Show list holds. Click a tile to make it **Active**; the mouse
wheel and a drag zoom and pan every tile together (untick *Link zoom and pan*
to move one). Per image:

| Control | What it does |
|---|---|
| **Keeper** | this image is kept by Mark Remaining Equivalent — never the one a pair gives up |
| **Canonical** | the preferred copy: Mark Remaining Equivalent prefers it on each pair that includes it |
| **Select** | the image(s) Mark Selected as Related Variant is about |
| **Compare** | the Active image and this one, side by side on the page (no decision is offered if they are not a candidate pair) |
| **Exclude** | takes the image out of the view and out of the group controls — **records nothing** |

Group controls — each shows a preview (what would be recorded, what already
holds, what an earlier decision of yours keeps, what is in conflict or
blocked), then **Record**:

| Control | Records, on each candidate pair among the images in view |
|---|---|
| **Keep All** | Keep both (the relationship stays each pair's own question) |
| **Mark Remaining Equivalent** | Equivalent copy + Keep both — or, with a Canonical, the Canonical preferred on its pairs (unless the other image is a Keeper). A pair already answered is left as it is |
| **Mark Selected as Related Variant** | Related variant + Keep both on the pairs joining a selected image to one **not** selected; a pair between two selected images is left alone |
| **Defer Group** | a deferral (with the return you choose) on each pair not yet answered |

**Undo the last group action** (or **Batches…**) undoes it as a whole;
**Previous / Next cluster** move through the list. The cluster itself is
never recorded: every decision names one pair.

### 4c. Plan (from B15)

**Plan** in the navigation. The first line says how many duplicate groups and
similar-image pairs your decisions have made ready for a plan (a copy to
remove, a copy to keep), and how many you deferred (left out).

**Build a new revision** turns them into proposed logical actions:

| Action | Means |
|---|---|
| **Remove location** | this copy should no longer exist -- *how* is not decided here |
| **Preserve (no action)** | this copy stays, and why: a keeper, protected, the group's canonical, preferred by a pair |
| **Update source metadata** (from B16) | a field of this file should say X, or have no value -- from a desired-metadata decision; always blocked for now (no certified writer exists; a signed or hard-linked file is blocked either way) |
| Move / Rename location | in the vocabulary; no decision asks for them yet |

Each revision is **previewed** against the project's evidence (nothing on
disk is read). Every step lists what was checked now:
- **The decisions:** they are current, and no conflict stands.
- **The evidence:** the fingerprints are current and the copy was present at
  the last scan.
- **Retention:** a keeper is decided and present; the copy is not protected
  (or its protection was explicitly overridden); and **no other decision keeps
  it**.
- **Counts:** enough copies would remain.

It also lists what only a live check could answer later (the file still
exists as observed, its bytes still match, permissions, a way back), which is
not checked here. A step is **Ready**, **Blocked** (with the check that
failed), **Blocked (unknown evidence)** (a required check could not be
answered from the evidence -- unknown never counts as a pass), or
**Preserved**. The preview shows **Plan-Eligible Reclaimable Bytes** (after
your keepers, protections and decisions) beside Phase 2's **Potentially
Reclaimable Exact-Duplicate Bytes** (before any decision): two different
numbers under two names.

**Lock revision** freezes it: the decisions and evidence must be where the
preview found them (otherwise build again), and the revision gets a SHA-256
fingerprint and becomes immutable. Building again makes the next revision;
**Compare with previous** says what changed. When a decision, a policy or the
evidence a locked revision rests on changes, the revision shows **Obsolete**
with the reason, and is never rewritten. A change elsewhere in the project
does not touch it.

### 4d. Metadata (from B16; the page from B17)

**Metadata** in the side panel opens the **Metadata Review queue**. It lists
the metadata questions that need you:
- a field you named and have not decided;
- a decision whose file has changed since;
- a deferral that has come back;
- two readings of a field that disagree.

Filter it by field, kind of file, value state or blocker. When nothing needs
you, it says **Currently clear**. Open a row, or use **Metadata for this file**
in a file's Details, or open it from a plan's Update Source Metadata step.

**One file's page** lists every field. For each one:
- what the file says (or *no value*, *unknown*, *unsupported*, *unavailable*,
  *stale*, *conflicting* -- both readings shown, no winner picked);
- what you want;
- the write capability and the blockers: no certified writer, signed or
  certified, encrypted, hard-linked, cloud-only;
- the review state;
- the plan step it would make.

The source section says so: *stored in the source file; saving records what
you want; it creates a Phase 4 plan item and never edits the file.*

The actions on a field:
- **Save desired value** (typed: a date is YYYY-MM-DD; keywords separated
  by ;). Invalid input is refused with the reason.
- **Clear...** Asks first. It is never the default, least of all for an
  *unknown* field.
- **Leave source unchanged.**
- **Defer...** Until a date, or until the file changes.
- **Undo.**

**Project metadata** (tags, category, note, review priority, retention
rationale, owner) is *stored in the File Organizer project -- never written to
the file*, and is never a plan step. The history lists every metadata
decision on the file.

*Leave unchanged* and project metadata never appear as plan steps; the plan
counts them. A field you chose to leave unchanged comes back for review only
when the file itself changes.

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
- **Similar images are found by perceptual hash only (B11).** It finds
  recompressed, resized, recoloured and lightly edited copies; a crop or a
  heavy edit is usually too different for it. A folder of generated
  variations (a batch of AI images from one prompt) can yield thousands of
  look-alike pairs: B12's bulk action answers a folder of them at once, and
  Survey Mode (B13) a crowd of 3–8 across folders; a crowd of more than
  eight across folders still takes one pair at a time.

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
| Decision record | tables `p3_*` in the project database (decisions, policies, review events, bulk batches); `Exports\DecisionJournal.txt` when exported |
| Application log | `Logs\app.log` |

To back up a project, copy its whole folder. To move the app, move the folder —
nothing is written to the registry or to `AppData`.

See **`ARCHITECTURE.md`** for how it works internally.
