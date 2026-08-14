<#
    FullHashInventory.ps1
    Part of: The File Organizer
    Version: 1.0.0

    Purpose:
        "Full Run" -- computes a full SHA-256 hash for EVERY file in
        PreliminaryInventory.csv directly, with no tiering (no size-group
        pre-filter, no partial-hash pre-filter). This is deliberately
        different from the Duplicate Run pipeline (PartialHash.ps1 ->
        FullHash.ps1), which only hashes files that share a size with at
        least one other file. Full Run exists for cases where a complete,
        unconditional hash record of every file matters more than
        processing speed -- e.g. a forensic/evidentiary record where
        "we have a hash for every file, not just the ones that turned out
        to share a size" is the point, not an optimization detail.

        Because every file gets hashed regardless of whether anything
        else shares its size, this is simpler than FullHash.ps1 in one
        respect (no multi-tier reconciliation between a partial-hash pass
        and a full-hash pass) but naturally the more expensive of the two
        run types -- this is inherent to what it's for, not a performance
        bug to fix.

        Output columns mirror DuplicateHashInventory.csv's shape (every
        Preliminary Inventory column, plus FullHash/FinalStatus/
        DuplicateGroupID) so downstream category scripts and any future
        tooling can treat either pipeline's output the same way.

        FinalStatus values (simpler than the Duplicate Run pipeline's,
        since there's only one tier here):
          - UniqueByHash       : hashed successfully, no other file in
                                  this project shares this exact hash
          - ConfirmedDuplicate : hashed successfully, 2+ files share this
                                  exact hash
          - SkippedCloudOnly   : cloud-only file skipped this run
          - Error              : could not be hashed

    Scaling features (matching the Duplicate Run pipeline):
        - CHECKPOINT/RESUME: successfully-hashed files are written to a
          small checkpoint CSV in this run's Logs\ folder as hashing
          proceeds. If interrupted and re-run, only files not yet hashed
          are processed. Checkpoint reads use a dictionary keyed by
          DB_ID, which is naturally dedup-safe against any duplicate
          rows a retried/partial write might otherwise produce -- see
          PartialHash.ps1 v2.2.0 for the real test failure that first
          surfaced this class of issue.
        - Checkpoint writes only clear their buffer on a confirmed-
          successful write (same fix, same reasoning).
        - CLOUD-FILE SAFETY CHECK: warns before hashing any cloud-only
          file. Since Full Run touches every file in the project (not a
          narrowed candidate set), this can affect many more files than
          the equivalent check in FullHash.ps1 -- worth reading the
          warning carefully before proceeding on a cloud-synced folder.
        - Uses the confirmed-working \\?\ long-path prefix (Common.ps1's
          ConvertTo-LongPath) for files with paths over 260 characters.

    Usage:
        Run from inside a PROJECT's Scripts folder:
            .\FullHashInventory.ps1 -SettingsPath <path>
            .\FullHashInventory.ps1 -SettingsPath <path> -Force
            .\FullHashInventory.ps1 -SettingsPath <path> -SkipCloudOnly

    Requires:
        - PreliminaryInventory.ps1 must have already been run for this
          project's current run (Pre-Scan). Does NOT require
          PotentialDuplicates.ps1 or the Duplicate Run pipeline to have
          run -- Full Run is an independent path from Pre-Scan.

    Note:
        This script does NOT create a new Run folder. It writes into the
        SAME run folder as the earlier scripts in this scan session.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$SettingsPath,

    [switch]$Force,
    [switch]$SkipCloudOnly
)

# Common.ps1 provides ConvertTo-LongPath, used below to bypass the
# 260-character Windows MAX_PATH limit when opening files for hashing.
. (Join-Path $PSScriptRoot "Common.ps1")

# ----------------------------------------------------------------------------
# 0. Load settings.json
# ----------------------------------------------------------------------------
if (-not (Test-Path -LiteralPath $SettingsPath)) {
    Write-Host "ERROR: settings.json not found at:" -ForegroundColor Red
    Write-Host "  $SettingsPath" -ForegroundColor Red
    exit 1
}

$ProjectRoot = Split-Path $SettingsPath -Parent

try {
    $Settings = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
}
catch {
    Write-Host "ERROR: Could not read/parse settings.json. $_" -ForegroundColor Red
    exit 1
}

if ([string]::IsNullOrWhiteSpace($Settings.CurrentRun)) {
    Write-Host "ERROR: No CurrentRun found in settings.json." -ForegroundColor Red
    Write-Host "Run PreliminaryInventory.ps1 (Pre-Scan) first." -ForegroundColor Red
    exit 1
}

# ----------------------------------------------------------------------------
# 1. Locate the current run's folders and input file
# ----------------------------------------------------------------------------
$RunFolder       = Join-Path $ProjectRoot "Runs\$($Settings.CurrentRun)"
$InventoryFolder = Join-Path $RunFolder "Inventory"
$ReportsFolder   = Join-Path $RunFolder "Reports"
$LogsFolder      = Join-Path $RunFolder "Logs"

if (-not (Test-Path -LiteralPath $RunFolder)) {
    Write-Host "ERROR: Run folder not found:" -ForegroundColor Red
    Write-Host "  $RunFolder" -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Path $InventoryFolder -Force | Out-Null
New-Item -ItemType Directory -Path $ReportsFolder   -Force | Out-Null
New-Item -ItemType Directory -Path $LogsFolder      -Force | Out-Null

$PreliminaryCsvPath = Join-Path $InventoryFolder "PreliminaryInventory.csv"
$ErrorLogPath       = Join-Path $LogsFolder "errors_fullhashinventory.txt"
$CheckpointPath     = Join-Path $LogsFolder "checkpoint_fullhashinventory_raw.csv"

if (-not (Test-Path -LiteralPath $PreliminaryCsvPath)) {
    Write-Host "ERROR: PreliminaryInventory.csv not found at:" -ForegroundColor Red
    Write-Host "  $PreliminaryCsvPath" -ForegroundColor Red
    Write-Host "Run PreliminaryInventory.ps1 (Pre-Scan) first." -ForegroundColor Red
    exit 1
}

Write-Host "Reading: $PreliminaryCsvPath" -ForegroundColor Cyan
Write-Host ""

# ----------------------------------------------------------------------------
# 2. Helper functions
# ----------------------------------------------------------------------------
function Format-Bytes {
    param([double]$Bytes)
    $units = "B", "KB", "MB", "GB", "TB"
    $index = 0
    while ($Bytes -ge 1024 -and $index -lt $units.Count - 1) {
        $Bytes /= 1024
        $index++
    }
    return "{0:N2} {1}" -f $Bytes, $units[$index]
}

function ConvertTo-Int64Safe {
    param($Value)
    try { return [int64]$Value } catch { return 0 }
}

function Get-FullHash {
    param([string]$Path)
    # Identical technique to FullHash.ps1's Get-FullHash -- same
    # confirmed-working \\?\ prefix approach, not two different code
    # paths that might behave differently.
    $longPath = ConvertTo-LongPath -Path $Path
    $stream = [System.IO.File]::Open($longPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        try {
            $hashBytes = $sha256.ComputeHash($stream)
            return [BitConverter]::ToString($hashBytes) -replace '-', ''
        }
        finally {
            $sha256.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

function Group-ByKey {
    param($Rows, [scriptblock]$KeySelector)
    $Dict = [System.Collections.Generic.Dictionary[string, System.Collections.Generic.List[object]]]::new()
    foreach ($Row in $Rows) {
        $Key = & $KeySelector $Row
        if (-not $Dict.ContainsKey($Key)) {
            $Dict[$Key] = [System.Collections.Generic.List[object]]::new()
        }
        $Dict[$Key].Add($Row)
    }
    return $Dict
}

# ----------------------------------------------------------------------------
# 3. Load input -- every file from Pre-Scan, no tiering/pre-filtering
# ----------------------------------------------------------------------------
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

$PreliminaryRows = Import-Csv -LiteralPath $PreliminaryCsvPath

# ----------------------------------------------------------------------------
# 4. Checkpoint resume
# ----------------------------------------------------------------------------
$AlreadyProcessed = @{}   # DB_ID -> FullHash

if (Test-Path -LiteralPath $CheckpointPath) {
    Write-Host "Found an existing checkpoint -- resuming an interrupted Full Run." -ForegroundColor Yellow
    $CheckpointRows = Import-Csv -LiteralPath $CheckpointPath
    foreach ($r in $CheckpointRows) { $AlreadyProcessed[$r.DB_ID] = $r.FullHash }
    Write-Host "  $($CheckpointRows.Count) files already hashed previously; skipping those." -ForegroundColor Yellow
    Write-Host ""
}

$RowsToProcess = $PreliminaryRows | Where-Object { -not $AlreadyProcessed.ContainsKey($_.DB_ID) }

# ----------------------------------------------------------------------------
# 5. Cloud-file safety check -- every file in the project is in scope
#    here, not a narrowed candidate set, so this can be a much bigger
#    warning than the equivalent check in FullHash.ps1.
# ----------------------------------------------------------------------------
$SkippedCloudRows = @()

$CloudOnlyRows = $RowsToProcess | Where-Object { $_.IsOfflineOrCloud -eq "True" }

if ($CloudOnlyRows.Count -gt 0) {
    $CloudBytes = ($CloudOnlyRows | ForEach-Object { ConvertTo-Int64Safe $_.Length } | Measure-Object -Sum).Sum
    if (-not $CloudBytes) { $CloudBytes = 0 }

    Write-Host "WARNING: $($CloudOnlyRows.Count) of $($RowsToProcess.Count) files to hash are cloud-only" -ForegroundColor Yellow
    Write-Host "(not fully downloaded locally). Full Run hashes EVERY file in the project," -ForegroundColor Yellow
    Write-Host "not just duplicate candidates, so this can mean a much larger download than" -ForegroundColor Yellow
    Write-Host "the equivalent check for a Duplicate Run." -ForegroundColor Yellow
    Write-Host "Estimated total download if all are hashed: $(Format-Bytes $CloudBytes)" -ForegroundColor Yellow
    Write-Host ""

    if ($SkipCloudOnly) {
        Write-Host "Skipping cloud-only files (-SkipCloudOnly specified)." -ForegroundColor Yellow
        $SkippedCloudRows = $CloudOnlyRows
        $SkipIDs = @{}
        foreach ($r in $SkippedCloudRows) { $SkipIDs[$r.DB_ID] = $true }
        $RowsToProcess = $RowsToProcess | Where-Object { -not $SkipIDs.ContainsKey($_.DB_ID) }
    }
    elseif ($Force) {
        Write-Host "Proceeding to hash cloud-only files (-Force specified)." -ForegroundColor Yellow
    }
    else {
        $Answer = Read-Host "Continue and hash these cloud-only files now? [Y] Yes  [N] No, skip them (default is Y)"
        if ($Answer -match '^[Nn]') {
            Write-Host "Skipping cloud-only files for this run." -ForegroundColor Yellow
            $SkippedCloudRows = $CloudOnlyRows
            $SkipIDs = @{}
            foreach ($r in $SkippedCloudRows) { $SkipIDs[$r.DB_ID] = $true }
            $RowsToProcess = $RowsToProcess | Where-Object { -not $SkipIDs.ContainsKey($_.DB_ID) }
        }
    }
    Write-Host ""
}

# ----------------------------------------------------------------------------
# 6. Hash every remaining file, checkpointing as we go
# ----------------------------------------------------------------------------
$ErrorCount = 0
$Buffer     = [System.Collections.Generic.List[object]]::new()
$CheckpointFileExists = Test-Path -LiteralPath $CheckpointPath

$FlushBatchSize       = 25
$FlushIntervalSeconds = 3
$LastFlush = Get-Date

function Flush-Checkpoint {
    param($Buffer, $Path, [ref]$FileExists)
    if ($Buffer.Count -eq 0) { return }
    try {
        if ($FileExists.Value) {
            $Buffer | Export-Csv -LiteralPath $Path -NoTypeInformation -Encoding UTF8 -Append -ErrorAction Stop
        }
        else {
            $Buffer | Export-Csv -LiteralPath $Path -NoTypeInformation -Encoding UTF8 -ErrorAction Stop
            $FileExists.Value = $true
        }
        # Only clear on a confirmed-successful write -- see
        # PartialHash.ps1 v2.2.0 for the real test failure this fix
        # addresses (a OneDrive lock collision during testing).
        $Buffer.Clear()
    }
    catch {
        Write-Host "  WARNING: Checkpoint write failed, will retry: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

$TotalToProcess = $RowsToProcess.Count
$Processed      = 0
$BytesRead      = 0
$LastProgressUpdate = Get-Date

foreach ($Row in $RowsToProcess) {
    $Processed++

    try {
        $HashValue = Get-FullHash -Path $Row.Path
        $BytesRead += ConvertTo-Int64Safe $Row.Length

        $Buffer.Add([PSCustomObject]@{
            DB_ID    = $Row.DB_ID
            FullHash = $HashValue
        })
    }
    catch {
        $ErrorCount++
        Add-Content -LiteralPath $ErrorLogPath -Value "FULL HASH ERROR: $($Row.Path) -- $($_.Exception.Message)" -Encoding UTF8
    }

    if ($Buffer.Count -ge $FlushBatchSize -or ((Get-Date) - $LastFlush).TotalSeconds -ge $FlushIntervalSeconds) {
        Flush-Checkpoint -Buffer $Buffer -Path $CheckpointPath -FileExists ([ref]$CheckpointFileExists)
        $LastFlush = Get-Date
    }

    if ($TotalToProcess -gt 0 -and ((Get-Date) - $LastProgressUpdate).TotalMilliseconds -ge 200) {
        $PercentComplete = [math]::Round(($Processed / $TotalToProcess) * 100)
        Write-Progress -Activity "Computing full hashes (Full Run -- every file)" `
            -Status "$Processed of $TotalToProcess files this run ($([math]::Round($Stopwatch.Elapsed.TotalSeconds,1))s elapsed)" `
            -PercentComplete $PercentComplete
        $LastProgressUpdate = Get-Date
    }
}

Flush-Checkpoint -Buffer $Buffer -Path $CheckpointPath -FileExists ([ref]$CheckpointFileExists)
Write-Progress -Activity "Computing full hashes (Full Run -- every file)" -Completed

# ----------------------------------------------------------------------------
# 7. Read back the complete checkpoint (resumed + new) -> DB_ID => FullHash.
#    A dictionary keyed by DB_ID is naturally dedup-safe -- no separate
#    deduplication step needed here, unlike PartialHash.ps1's flat-list
#    read-back which required an explicit fix for this.
# ----------------------------------------------------------------------------
$FullHashByID = @{}
if (Test-Path -LiteralPath $CheckpointPath) {
    Import-Csv -LiteralPath $CheckpointPath | ForEach-Object { $FullHashByID[$_.DB_ID] = $_.FullHash }
}

# ----------------------------------------------------------------------------
# 8. Build the final per-DB_ID status map -- one tier only, so this is
#    simpler than FullHash.ps1's multi-tier reconciliation. Group every
#    successfully-hashed file directly by its full hash value.
# ----------------------------------------------------------------------------
$FinalByID = @{}
$NextDuplicateGroupID = 1
$ConfirmedGroupsForReport = [System.Collections.Generic.List[object]]::new()

$HashedRows = [System.Collections.Generic.List[object]]::new()
foreach ($Row in $PreliminaryRows) {
    if ($FullHashByID.ContainsKey($Row.DB_ID)) {
        $HashedRows.Add([PSCustomObject]@{
            DB_ID    = $Row.DB_ID
            Length   = $Row.Length
            Path     = $Row.Path
            FullHash = $FullHashByID[$Row.DB_ID]
        })
    }
}

$HashGroups = Group-ByKey -Rows $HashedRows -KeySelector { param($r) $r.FullHash }

foreach ($Key in $HashGroups.Keys) {
    $GroupRows = $HashGroups[$Key]

    if ($GroupRows.Count -eq 1) {
        $Row = $GroupRows[0]
        $FinalByID[$Row.DB_ID] = [PSCustomObject]@{
            FinalStatus = "UniqueByHash"; DuplicateGroupID = $null; FullHash = $Row.FullHash
        }
        continue
    }

    $GroupID = $NextDuplicateGroupID
    $NextDuplicateGroupID++

    $Size = ConvertTo-Int64Safe $GroupRows[0].Length
    $ConfirmedGroupsForReport.Add([PSCustomObject]@{
        GroupID          = $GroupID
        Size             = $Size
        Count            = $GroupRows.Count
        PotentialReclaim = $Size * ($GroupRows.Count - 1)
        Rows             = $GroupRows
    })

    foreach ($Row in $GroupRows) {
        $FinalByID[$Row.DB_ID] = [PSCustomObject]@{
            FinalStatus      = "ConfirmedDuplicate"
            DuplicateGroupID = $GroupID
            FullHash         = $Row.FullHash
        }
    }
}

# Cloud-skipped and errored rows
foreach ($Row in $SkippedCloudRows) {
    $FinalByID[$Row.DB_ID] = [PSCustomObject]@{
        FinalStatus = "SkippedCloudOnly"; DuplicateGroupID = $null; FullHash = $null
    }
}

$AccountedIDs = @{}
foreach ($k in $FinalByID.Keys) { $AccountedIDs[$k] = $true }

foreach ($Row in $PreliminaryRows) {
    if (-not $AccountedIDs.ContainsKey($Row.DB_ID)) {
        $FinalByID[$Row.DB_ID] = [PSCustomObject]@{
            FinalStatus = "Error"; DuplicateGroupID = $null; FullHash = $null
        }
    }
}

$Stopwatch.Stop()

# ----------------------------------------------------------------------------
# 9. Export FullHashInventory.csv, then clear the checkpoint (clean finish)
# ----------------------------------------------------------------------------
$FullHashInventoryRows = [System.Collections.Generic.List[object]]::new()

foreach ($Row in $PreliminaryRows) {
    $Final = $FinalByID[$Row.DB_ID]

    $FullHashInventoryRows.Add([PSCustomObject]@{
        DB_ID            = $Row.DB_ID
        FileName         = $Row.FileName
        Extension        = $Row.Extension
        Directory        = $Row.Directory
        Path             = $Row.Path
        Length           = $Row.Length
        CreationTime     = $Row.CreationTime
        LastWriteTime    = $Row.LastWriteTime
        LastAccessTime   = $Row.LastAccessTime
        Attributes       = $Row.Attributes
        IsReparsePoint   = $Row.IsReparsePoint
        IsOfflineOrCloud = $Row.IsOfflineOrCloud
        Depth            = $Row.Depth
        PathLength       = $Row.PathLength
        FullHash         = if ($Final) { $Final.FullHash } else { $null }
        FinalStatus      = if ($Final) { $Final.FinalStatus } else { "Error" }
        DuplicateGroupID = if ($Final) { $Final.DuplicateGroupID } else { $null }
    })
}

$FullHashInventoryCsvPath = Join-Path $InventoryFolder "FullHashInventory.csv"
$FullHashInventoryRows | Export-Csv -LiteralPath $FullHashInventoryCsvPath -NoTypeInformation -Encoding UTF8

try {
    if (Test-Path -LiteralPath $CheckpointPath) {
        Remove-Item -LiteralPath $CheckpointPath -Force
    }
}
catch {
    # Non-fatal -- leftover checkpoint just means a harmless resume-skip next time
}

# ----------------------------------------------------------------------------
# 10. Compute report statistics
# ----------------------------------------------------------------------------
$TotalFiles = $FullHashInventoryRows.Count
$TotalBytes = ($FullHashInventoryRows | ForEach-Object { ConvertTo-Int64Safe $_.Length } | Measure-Object -Sum).Sum
if (-not $TotalBytes) { $TotalBytes = 0 }

$StatusDict = Group-ByKey -Rows $FullHashInventoryRows -KeySelector { param($r) $r.FinalStatus }
$StatusCounts = foreach ($Key in $StatusDict.Keys) {
    [PSCustomObject]@{ Status = $Key; Count = $StatusDict[$Key].Count }
}

$ConfirmedGroupsRanked = $ConfirmedGroupsForReport | Sort-Object PotentialReclaim -Descending
$TotalConfirmedReclaim = ($ConfirmedGroupsRanked | Measure-Object -Property PotentialReclaim -Sum).Sum
if (-not $TotalConfirmedReclaim) { $TotalConfirmedReclaim = 0 }
$TotalRedundantFiles = ($ConfirmedGroupsRanked | ForEach-Object { $_.Count - 1 } | Measure-Object -Sum).Sum
if (-not $TotalRedundantFiles) { $TotalRedundantFiles = 0 }

# ----------------------------------------------------------------------------
# 11. Build the report text
# ----------------------------------------------------------------------------
$ReportLines = [System.Collections.Generic.List[string]]::new()

$ReportLines.Add("=" * 70)
$ReportLines.Add(" THE FILE ORGANIZER -- FULL HASH INVENTORY REPORT (Full Run)")
$ReportLines.Add(" Project   : $($Settings.ProjectName)")
$ReportLines.Add(" Run       : $($Settings.CurrentRun)")
$ReportLines.Add(" Generated : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$ReportLines.Add("=" * 70)
$ReportLines.Add("")
$ReportLines.Add("SUMMARY")
$ReportLines.Add(("  Total files                : {0:N0}" -f $TotalFiles))
$ReportLines.Add("  Total size                  : $(Format-Bytes $TotalBytes) ($TotalBytes bytes)")
$ReportLines.Add("")
$ReportLines.Add("HASH RESOLUTION SUMMARY")
foreach ($sc in $StatusCounts) {
    $ReportLines.Add(("  {0,-20}: {1:N0}" -f $sc.Status, $sc.Count))
}
$ReportLines.Add("")
$ReportLines.Add(("  Confirmed duplicate groups : {0:N0}" -f $ConfirmedGroupsRanked.Count))
$ReportLines.Add(("  Redundant files (all but one per group): {0:N0}" -f $TotalRedundantFiles))
$ReportLines.Add(("  Confirmed reclaimable space: {0}" -f (Format-Bytes $TotalConfirmedReclaim)))
$ReportLines.Add("")
$ReportLines.Add("PERFORMANCE")
$ReportLines.Add("  Files hashed this run       : $TotalToProcess")
$ReportLines.Add("  Bytes read                  : $(Format-Bytes $BytesRead)")
$ReportLines.Add("  Processing time             : $($Stopwatch.Elapsed.ToString('hh\:mm\:ss'))")
if ($ErrorCount -gt 0) {
    $ReportLines.Add("  Errors (could not be hashed): $ErrorCount (see Logs\errors_fullhashinventory.txt)")
}
$ReportLines.Add("")
$ReportLines.Add("CONFIRMED DUPLICATE GROUPS")
if ($ConfirmedGroupsRanked.Count -eq 0) {
    $ReportLines.Add("  (none)")
}
else {
    $MaxGroupsShown = 20
    $GroupsToShow = $ConfirmedGroupsRanked | Select-Object -First $MaxGroupsShown
    foreach ($g in $GroupsToShow) {
        $ReportLines.Add(("  Group {0,3} | {1} files x {2} = {3} reclaimable" -f $g.GroupID, $g.Count, (Format-Bytes $g.Size), (Format-Bytes $g.PotentialReclaim)))
        $ShownRows = $g.Rows | Select-Object -First 5
        foreach ($r in $ShownRows) {
            $ReportLines.Add("      - $($r.Path)")
        }
        if ($g.Rows.Count -gt 5) {
            $Remaining = $g.Rows.Count - 5
            $ReportLines.Add("      ... and $Remaining more (see FullHashInventory.csv, DuplicateGroupID = $($g.GroupID))")
        }
    }
    if ($ConfirmedGroupsRanked.Count -gt $MaxGroupsShown) {
        $RemainingGroups = $ConfirmedGroupsRanked.Count - $MaxGroupsShown
        $ReportLines.Add("")
        $ReportLines.Add("  ... and $RemainingGroups more group(s) not shown here (see FullHashInventory.csv for the full list)")
    }
}
$ReportLines.Add("=" * 70)

$ReportPath = Join-Path $ReportsFolder "FullHashInventoryReport.txt"
$ReportLines | Set-Content -LiteralPath $ReportPath -Encoding UTF8

# ----------------------------------------------------------------------------
# 12. Update settings.json
# ----------------------------------------------------------------------------
if (-not ($Settings.PSObject.Properties.Name -contains "LastFullHashInventoryScan")) {
    $Settings | Add-Member -MemberType NoteProperty -Name "LastFullHashInventoryScan" -Value $null
}
$Settings.LastFullHashInventoryScan = (Get-Date).ToString("o")

$Settings | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $SettingsPath -Encoding UTF8

# ----------------------------------------------------------------------------
# 13. Finish up
# ----------------------------------------------------------------------------
Write-Host ""
Write-Host "Full Hash Inventory complete." -ForegroundColor Green
Write-Host "  Confirmed duplicate groups : $($ConfirmedGroupsRanked.Count)"
Write-Host "  Redundant files            : $TotalRedundantFiles"
Write-Host "  Confirmed reclaimable space: $(Format-Bytes $TotalConfirmedReclaim)"
Write-Host "  Duration                   : $($Stopwatch.Elapsed.ToString('hh\:mm\:ss'))"
Write-Host ""

# Report is saved but not auto-opened -- see Dashboard.py for how reports are accessed.
