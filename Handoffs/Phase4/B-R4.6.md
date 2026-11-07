# Research brief B-R4.6: OneDrive Files On-Demand, acting without hydrating

**For:** `fo-researcher` (sonnet) · **From:** the Architect, 2026-11-01 · **Covers:** R4.6
**Report to:** `C:\FileOrganizerTesting\Docs\Research\phase_4\R4\R4.6_report.md`. Target 2,500-4,000 words.
Budget: ~20 web fetches.

## Context (do not re-research)
Axiom 14: cloud-only content must never be silently hydrated (downloaded). Phase 1 already detects placeholders
by attributes without reading content (`Scripts\Database\win_meta.py` lines ~105-290: `RECALL_ON_OPEN`
0x40000, `RECALL_ON_DATA_ACCESS` 0x400000, `PINNED` 0x80000, `UNPINNED` 0x100000; cloud reparse tags are walked,
not treated as links). Read those lines. The P3 closeout keeps "anything on a cloud-only file until it is local"
blocked. The product is Python on Windows 10/11, OneDrive personal and business.

## Questions
1. **Which operations hydrate?** For each, from a normal process: enumerating a folder; `os.stat` /
   `GetFileAttributesExW`; `CreateFileW` with `FILE_READ_ATTRIBUTES` only; with `DELETE`; with
   `FILE_FLAG_OPEN_REPARSE_POINT`; reading `FileIdInfo`; rename within the same folder; move to another folder
   **inside** the sync root; move **out** of the sync root on the same volume; delete; sending to the Recycle
   Bin; hashing. Label each verified / reported / untested.
2. **Can a process refuse hydration for itself?** `CfSetProcessHydrationPolicy`? `RtlSetProcessPlaceholderCompatibilityMode`
   (`PHCM_DISGUISE_PLACEHOLDER`, `PHCM_EXPOSE_PLACEHOLDERS`) and what it actually changes; any documented
   per-process block; the former "Block apps from downloading" setting. Callable from Python via `ctypes`?
3. **State reading.** `CfGetPlaceholderStateFromAttributeTag` / `CfGetPlaceholderStateFromFindData` vs the raw
   attribute bits: which states can the bits alone not distinguish (in sync, partial, pinned-but-not-yet-local)?
   Is "full file present locally" reliably knowable without opening content?
4. **What a rename or move does to sync.** A rename inside the sync root: does OneDrive rename the cloud item
   (keeping version history and sharing links) or delete-and-upload? What happens if the client is paused,
   signed out, or offline at the time? Known conflict behaviours ("-PCNAME" copies).
5. **Known Files On-Demand pitfalls** for file managers: Storage Sense dehydrating files between preflight and
   action; "Always keep on this device" folders; Known Folder Move (Desktop, Documents, Pictures redirected into
   OneDrive), so that a "normal" user folder may be a sync root.
6. **How to detect a sync root** for a given path (registry `SyncRootManager`, `StorageProvider` APIs, the
   `CfGetSyncRootInfoByPath` call) from Python without admin.

## Deliverables in the report
- A per-operation table (hydrates? / changes cloud state? / safe for Phase 4 v1?).
- A recommendation for v1: what is allowed on a fully local file inside a sync root, what is blocked.
- **A hand-probe list for Thomas** (he runs it himself, on one throwaway file in his OneDrive set to "Free up
  space"): each step written in plain words, with what to look at (the cloud/tick icon, file size on disk) and
  what each result means. No probe may touch any file other than that throwaway file.
