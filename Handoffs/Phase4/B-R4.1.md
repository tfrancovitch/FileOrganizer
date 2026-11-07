# Research brief B-R4.1: the same-volume move/rename primitive, identity, environment blockers

**For:** `fo-researcher` (sonnet) · **From:** the Architect, 2026-11-01 · **Covers:** R4.1, R4.5, R4.10
**Report to:** `C:\FileOrganizerTesting\Docs\Research\phase_4\R4\R4.1_report.md` (format in your agent file;
end with the source ledger). Target length: 3,000-5,000 words. Budget: stop at ~25 web fetches and report.

## Context (do not re-research)
The product is Python 3 on Windows (stdlib + `ctypes`; no .NET). The 2026-09 corpus
(`Docs\Research\phase_4\Phase_4_Research_Results.md`) already covers the general model. Read only its sections
"`MoveFileEx`: capabilities and limitations" (lines ~1282-1333), "Case-only and Unicode renames" (~1393-1402),
"Python `shutil.move()` race conditions" (~1667-1735). Build on them; do not repeat them.
Phase 4 v1 supports **single files, same volume, local NTFS fixed drives**, with collision policy
`FAIL_ON_CONFLICT`. The identity Phase 1 stores is volume serial + 64-bit file index + size + times + attributes
+ reparse tag + hard-link count.

## Questions
1. **No-overwrite guarantee.** For `os.rename`, `os.replace`, `MoveFileExW` (with and without
   `MOVEFILE_REPLACE_EXISTING`, `MOVEFILE_WRITE_THROUGH`) and `SetFileInformationByHandle` with
   `FileRenameInfo` / `FileRenameInfoEx` (flags `FILE_RENAME_FLAG_REPLACE_IF_EXISTS`,
   `FILE_RENAME_FLAG_POSIX_SEMANTICS`): is "fail if the destination exists" checked atomically by the file
   system, or is there a window? Exact error codes.
2. **Handle-bound rename.** Can we open the source once (`CreateFileW` with `DELETE | FILE_READ_ATTRIBUTES`,
   a share mode that denies other writers, `FILE_FLAG_OPEN_REPARSE_POINT`), read its identity from that handle
   (`GetFileInformationByHandleEx`: `FileIdInfo`, `FileBasicInfo`, `FileStandardInfo`), then rename *through
   the same handle*? This would close the gap between checking and acting. Minimum Windows version; what share
   modes other programs need for this to succeed; does opening this way trigger OneDrive hydration (cross-check
   with brief B-R4.6, mark untested)?
3. **Case-only renames** (`report.pdf` → `Report.pdf`): which of the calls above do it directly on NTFS, on
   folders with per-directory case sensitivity enabled, on exFAT/FAT32? Is a two-step rename via a temporary
   name ever needed, and what is the crash risk of the middle state?
4. **What survives a same-volume rename:** file ID, creation/modified/accessed times, attributes, ACL, alternate
   data streams, hard links, reparse data. Cite.
5. **NTFS tunneling.** When does a newly created or renamed-in file inherit the creation time / short name of a
   file recently removed from that name? Default window and registry controls. Does it affect an *undo* that
   moves a file back to its old path, or a quarantine restore?
6. **Long paths and names:** `\\?\` prefix behaviour with each call; `LongPathsEnabled`; Python's own
   long-path support; reserved names, trailing dots and spaces.
7. **Identity strength (R4.5):** 64-bit `nFileIndex` vs 128-bit `FileIdInfo` on NTFS and ReFS; is the volume
   serial stable across reboots, reformat, VSS snapshots, disk cloning? Can a file ID be reused after delete?
   Which fields can be read without opening file content (directory enumeration vs handle)?
8. **Environment blockers (R4.10):** Defender Controlled Folder Access (does a rename count as a change; which
   error a blocked `python.exe` gets; can it be detected before acting, e.g. via `Get-MpPreference` or the
   event log, without admin?); antivirus, Search Indexer and thumbnail cache holding handles (sharing
   violation patterns); read-only / system / hidden attributes and rename.

## Deliverables in the report
- A table: call × (atomic no-overwrite, case-only, handle-bound, long path, error codes, min Windows).
- **One recommended primitive**, with its exact call sequence.
- **A probe list**: the empirical tests B22's first unit must run in a sandbox to confirm each claim you could
  not verify from documents (each probe: setup, action, expected result, what a different result would mean).
