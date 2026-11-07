# Research brief B-R4.2: where a removed file goes

**For:** `fo-researcher` (sonnet) · **From:** the Architect, 2026-11-01 · **Covers:** R4.2
**Report to:** `C:\FileOrganizerTesting\Docs\Research\phase_4\R4\R4.2_report.md`. Target 2,500-4,000 words.
Budget: ~20 web fetches. **Run after B-R4.6 if possible** and read its report first.

## Context (do not re-research)
"Remove Location" removes one location of content that survives elsewhere (a duplicate), never the last copy.
Axiom 11: every Phase 4 action is reversible. Axiom 14: never hydrate cloud content. `PROJECT_PLAN.md`
("Undo, when Phase 4 arrives") already holds: a programmatic delete bypasses the Recycle Bin, it is size-capped
not time-boxed, and it does not exist on network shares. The 2026-09 corpus rates same-volume app quarantine
"a strong initial design choice" (`Phase_4_Research_Results.md` line ~75, ~797). The product is Python on Windows.

## Questions
1. **Recycle Bin, from Python.** Sending a file there (`IFileOperation` with `FOFX_RECYCLEONDELETE`,
   `SHFileOperationW` with `FOF_ALLOWUNDO`, `send2trash`): is success reported reliably; what happens when the
   file is too big for the bin, on removable drives, network shares, or when the bin is disabled (the shell may
   silently delete permanently: confirm or refute, and how to detect it beforehand)?
2. **Restoring from the Recycle Bin by program.** Is there a documented API? (`$Recycle.Bin`, `$I`/`$R` files,
   the shell "undelete" verb through `Shell.Application` namespace 10.) How does the program find *its* item
   again? How reliable is restore after the user empties the bin, or the bin evicts old items?
3. **OneDrive.** Deleting inside a OneDrive folder: local Recycle Bin and OneDrive online recycle bin (retention
   periods, personal vs business). Does a delete hydrate a placeholder? Does *moving* a file to a quarantine
   folder **inside** the same sync root hydrate it or just rename it online? Moving it **out** of the sync root?
4. **App quarantine placement.** Options: (a) one folder per source root, at the root
   (`<root>\.FileOrganizer-Quarantine\`); (b) one per volume (`X:\.FileOrganizer-Quarantine\`); (c) inside the
   project folder. For each: same-volume guarantee, permissions (can a normal user create a folder at a drive
   root?), visibility to the person, sync effects inside OneDrive, risk the person deletes it, indexing by
   Windows Search, Defender Controlled Folder Access.
5. **Restore safety.** Restoring a quarantined file to its old path when that path is now occupied, or its
   folder was removed; tunneling (see B-R4.1).
6. **Retention.** What other tools do (e.g. backup and dedupe tools, photo managers): time-boxed purge, size
   caps, never-auto-purge. Cite two or three established tools' documentation.

## Deliverables in the report
- A comparison table: Recycle Bin vs quarantine, on restore by program, capacity, per drive type, OneDrive,
  axiom 11, axiom 14, what the person sees.
- **A recommendation** with a placement rule, a manifest outline (what each quarantine entry records), and a
  retention default.
- Questions only Thomas can answer, worded for a non-programmer.
