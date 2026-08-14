<#
    PartialHash.ps1
    Part of: The File Organizer
    Version: 2.2.0

    Purpose:
        Reads the current run's PotentialDuplicates.csv and computes a
        SHA-256 hash over just the first N bytes of each candidate file
        (default 64KB). Files are re-grouped within their existing
        SizeGroupID by this partial hash:

          - RuledOut          : partial hash didn't match anyone else in
                                 the size group -- not a duplicate after all.
          - ConfirmedDuplicate: every file in the group is <= the byte
                                 window, so the "partial" hash IS a full-file
                                 hash -- these are fully confirmed, no need
                                 to wait for Script 4.
          - NeedsFullHash     : group still matches, but at least one member
                                 is bigger than the byte window -- only
                                 Script 4 (full hash) can confirm these.
          - SkippedCloudOnly  : cloud-only file that was skipped this run
                                 (see cloud-file safety check below).

    Scaling features (v2.0.0):
        - CHECKPOINT/RESUME: successfully-hashed files are written to a
          small checkpoint CSV in this run's Logs\ folder as hashing
          proceeds. If this script is interrupted and re-run, it picks up
          where it left off instead of re-hashing everything. The
          checkpoint file is deleted automatically on a clean finish.
        - CLOUD-FILE SAFETY CHECK: before hashing, warns if any files to
          be hashed are cloud-only (e.g. OneDrive Files On-Demand
          placeholders), since even a partial hash forces a full download.
          Prompts for confirmation unless -Force or -SkipCloudOnly is used.
        - Dictionary-based grouping instead of Group-Object, for speed at
          high file counts.

    v2.1.0: files with paths over 260 characters are now handled
    correctly (previously failed with "Could not find a part of the
    path"). Uses the \\?\ extended-length prefix (see Common.ps1's
    ConvertTo-LongPath), confirmed working via direct testing before
    being applied here.

    Usage:
        Run from inside a PROJECT's Scripts folder:
            .\PartialHash.ps1
            .\PartialHash.ps1 -PartialHashByteCount 131072
            .\PartialHash.ps1 -Force            # hash cloud-only files, no prompt
            .\PartialHash.ps1 -SkipCloudOnly    # always skip cloud-only files, no prompt

    Requires:
        - PotentialDuplicates.ps1 must have already been run for this
          project's current run.

    Note:
        This script does NOT create a new Run folder. It writes into the
        SAME run folder as the earlier scripts in this scan session.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$SettingsPath,

    [int]$PartialHashByteCount = 65536,   # 64 KB default read window
    [switch]$Force,                        # hash cloud-only files without prompting
    [switch]$SkipCloudOnly                 # always skip cloud-only files, no prompt
)

# Common.ps1 provides ConvertTo-LongPath, used below to bypass the
# 260-character Windows MAX_PATH limit when opening files for hashing.
. (Join-Path $PSScriptRoot "Common.ps1")

# ----------------------------------------------------------------------------
# 0. Load settings.json (path supplied by the caller -- no longer inferred
#    from this script's own location, since scripts now run from a single
#    shared location rather than being copied per-project)
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
    Write-Host "Run PreliminaryInventory.ps1 and PotentialDuplicates.ps1 first." -ForegroundColor Red
    exit 1
}

# ----------------------------------------------------------------------------
# 1. Locate the current run's folders and input files
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

$PotentialDuplicatesCsvPath = Join-Path $InventoryFolder "PotentialDuplicates.csv"
$PreliminaryCsvPath         = Join-Path $InventoryFolder "PreliminaryInventory.csv"
$ErrorLogPath               = Join-Path $LogsFolder "errors_partialhash.txt"
$CheckpointPath             = Join-Path $LogsFolder "checkpoint_partialhash_raw.csv"

if (-not (Test-Path -LiteralPath $PotentialDuplicatesCsvPath)) {
    Write-Host "ERROR: PotentialDuplicates.csv not found at:" -ForegroundColor Red
    Write-Host "  $PotentialDuplicatesCsvPath" -ForegroundColor Red
    Write-Host "Run PotentialDuplicates.ps1 first." -ForegroundColor Red
    exit 1
}

Write-Host "Reading: $PotentialDuplicatesCsvPath" -ForegroundColor Cyan
Write-Host "Partial hash window: $PartialHashByteCount bytes" -ForegroundColor Cyan
Write-Host ""

# ----------------------------------------------------------------------------
# 2. Load inputs
# ----------------------------------------------------------------------------
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

$CandidateRows = Import-Csv -LiteralPath $PotentialDuplicatesCsvPath

if ($CandidateRows.Count -eq 0) {
    Write-Host "PotentialDuplicates.csv is empty -- nothing to hash." -ForegroundColor Yellow
    exit 0
}

# Join back to PreliminaryInventory.csv (by DB_ID) to pull in IsOfflineOrCloud
# for reporting and the cloud-file safety check, without carrying that
# column through every intermediate CSV.
$PrelimByID = @{}
if (Test-Path -LiteralPath $PreliminaryCsvPath) {
    Import-Csv -LiteralPath $PreliminaryCsvPath | ForEach-Object {
        $PrelimByID[$_.DB_ID] = $_
    }
}

# ----------------------------------------------------------------------------
# 3. Helper functions
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

function Get-PartialHash {
    param(
        [string]$Path,
        [int]$ByteCount
    )
    # Long-path fix: prefix with \\?\ before opening, so files with paths
    # over 260 characters can be read. $Path itself stays unprefixed --
    # only used here, right before the actual file-open call.
    $longPath = ConvertTo-LongPath -Path $Path
    $stream = [System.IO.File]::Open($longPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        $bytesToRead = [Math]::Min($ByteCount, $stream.Length)
        $buffer = New-Object byte[] $bytesToRead
        $bytesRead = 0
        while ($bytesRead -lt $bytesToRead) {
            $read = $stream.Read($buffer, $bytesRead, $bytesToRead - $bytesRead)
            if ($read -eq 0) { break }
            $bytesRead += $read
        }
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        try {
            $hashBytes = $sha256.ComputeHash($buffer, 0, $bytesRead)
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

# ----------------------------------------------------------------------------
# 4. Checkpoint resume -- skip anything already successfully hashed
# ----------------------------------------------------------------------------
$AlreadyProcessed = @{}

if (Test-Path -LiteralPath $CheckpointPath) {
    Write-Host "Found an existing checkpoint -- resuming an interrupted partial-hash pass." -ForegroundColor Yellow
    $CheckpointRows = Import-Csv -LiteralPath $CheckpointPath
    foreach ($r in $CheckpointRows) { $AlreadyProcessed[$r.DB_ID] = $true }
    Write-Host "  $($CheckpointRows.Count) files already hashed previously; skipping those." -ForegroundColor Yellow
    Write-Host ""
}

$RowsToProcess = $CandidateRows | Where-Object { -not $AlreadyProcessed.ContainsKey($_.DB_ID) }

# ----------------------------------------------------------------------------
# 5. Cloud-file safety check (only applies to files we're about to hash now)
# ----------------------------------------------------------------------------
$SkippedCloudRows = @()

$CloudOnlyRows = $RowsToProcess | Where-Object {
    $Prelim = $PrelimByID[$_.DB_ID]
    $Prelim -and $Prelim.IsOfflineOrCloud -eq "True"
}

if ($CloudOnlyRows.Count -gt 0) {
    $CloudBytes = ($CloudOnlyRows | ForEach-Object { ConvertTo-Int64Safe $_.Length } | Measure-Object -Sum).Sum
    if (-not $CloudBytes) { $CloudBytes = 0 }

    Write-Host "WARNING: $($CloudOnlyRows.Count) of $($RowsToProcess.Count) files to hash are cloud-only" -ForegroundColor Yellow
    Write-Host "(not fully downloaded locally -- likely OneDrive Files On-Demand placeholders)." -ForegroundColor Yellow
    Write-Host "Hashing them, even partially, forces a full download of each file." -ForegroundColor Yellow
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
# 6. Hash each remaining candidate file, checkpointing as we go
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
        # Only clear on a confirmed-successful write. A failed write (e.g.
        # a transient file lock from cloud-sync software) leaves the
        # buffer intact, so these rows get retried on the next flush
        # instead of being silently discarded.
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
    $Length = ConvertTo-Int64Safe $Row.Length

    $PrelimMatch      = $PrelimByID[$Row.DB_ID]
    $IsOfflineOrCloud = if ($PrelimMatch) { $PrelimMatch.IsOfflineOrCloud } else { $null }

    try {
        $HashValue = Get-PartialHash -Path $Row.Path -ByteCount $PartialHashByteCount
        $BytesRead += [Math]::Min($Length, $PartialHashByteCount)

        $Buffer.Add([PSCustomObject]@{
            DB_ID            = $Row.DB_ID
            FileName         = $Row.FileName
            Directory        = $Row.Directory
            Path             = $Row.Path
            Length           = $Length
            SizeGroupID      = $Row.SizeGroupID
            PartialHash      = $HashValue
            IsOfflineOrCloud = $IsOfflineOrCloud
        })
    }
    catch {
        $ErrorCount++
        Add-Content -LiteralPath $ErrorLogPath -Value "HASH ERROR: $($Row.Path) -- $($_.Exception.Message)" -Encoding UTF8
    }

    if ($Buffer.Count -ge $FlushBatchSize -or ((Get-Date) - $LastFlush).TotalSeconds -ge $FlushIntervalSeconds) {
        Flush-Checkpoint -Buffer $Buffer -Path $CheckpointPath -FileExists ([ref]$CheckpointFileExists)
        $LastFlush = Get-Date
    }

    if ($TotalToProcess -gt 0 -and ((Get-Date) - $LastProgressUpdate).TotalMilliseconds -ge 200) {
        $PercentComplete = [math]::Round(($Processed / $TotalToProcess) * 100)
        Write-Progress -Activity "Computing partial hashes" `
            -Status "$Processed of $TotalToProcess files this run ($([math]::Round($Stopwatch.Elapsed.TotalSeconds,1))s elapsed)" `
            -PercentComplete $PercentComplete
        $LastProgressUpdate = Get-Date
    }
}

Flush-Checkpoint -Buffer $Buffer -Path $CheckpointPath -FileExists ([ref]$CheckpointFileExists)
Write-Progress -Activity "Computing partial hashes" -Completed

# ----------------------------------------------------------------------------
# 7. Read back the complete checkpoint (resumed + new) as the full hash set
# ----------------------------------------------------------------------------
$AllHashedRows = @()
if (Test-Path -LiteralPath $CheckpointPath) {
    $RawCheckpointRows = Import-Csv -LiteralPath $CheckpointPath

    # Defensive deduplication by DB_ID -- guards against duplicate rows
    # that could otherwise result from a retried/partial checkpoint write
    # (e.g. a transient file lock from cloud-sync software). A dictionary
    # keyed by DB_ID naturally collapses duplicates; since they should
    # only ever be identical copies of the same hash result, keeping the
    # last one loses nothing.
    $DedupedRows = [ordered]@{}
    foreach ($Row in $RawCheckpointRows) {
        $DedupedRows[$Row.DB_ID] = $Row
    }
    $AllHashedRows = $DedupedRows.Values
}

# ----------------------------------------------------------------------------
# 8. Re-group by (SizeGroupID + PartialHash) using a Dictionary for speed,
#    and assign Status
# ----------------------------------------------------------------------------
$GroupDict = [System.Collections.Generic.Dictionary[string, System.Collections.Generic.List[object]]]::new()

foreach ($Row in $AllHashedRows) {
    $Key = "$($Row.SizeGroupID)|$($Row.PartialHash)"
    if (-not $GroupDict.ContainsKey($Key)) {
        $GroupDict[$Key] = [System.Collections.Generic.List[object]]::new()
    }
    $GroupDict[$Key].Add($Row)
}

$OutputRows = [System.Collections.Generic.List[object]]::new()
$NextPartialHashGroupID = 1

$ConfirmedGroups = [System.Collections.Generic.List[object]]::new()
$NeedsHashGroups = [System.Collections.Generic.List[object]]::new()

foreach ($Key in $GroupDict.Keys) {
    $GroupRows = $GroupDict[$Key]

    if ($GroupRows.Count -eq 1) {
        $Row = $GroupRows[0]
        $OutputRows.Add([PSCustomObject]@{
            DB_ID               = $Row.DB_ID
            FileName            = $Row.FileName
            Directory           = $Row.Directory
            Path                = $Row.Path
            Length              = $Row.Length
            SizeGroupID         = $Row.SizeGroupID
            PartialHash         = $Row.PartialHash
            PartialHashGroupID  = $null
            Status              = "RuledOut"
            IsOfflineOrCloud    = $Row.IsOfflineOrCloud
        })
        continue
    }

    $AllWithinWindow = -not ($GroupRows | Where-Object { (ConvertTo-Int64Safe $_.Length) -gt $PartialHashByteCount })
    $Status = if ($AllWithinWindow) { "ConfirmedDuplicate" } else { "NeedsFullHash" }

    $GroupID = $NextPartialHashGroupID
    $NextPartialHashGroupID++

    $Size = ConvertTo-Int64Safe $GroupRows[0].Length
    $GroupSummary = [PSCustomObject]@{
        GroupID          = $GroupID
        Size             = $Size
        Count            = $GroupRows.Count
        PotentialReclaim = $Size * ($GroupRows.Count - 1)
        Rows             = $GroupRows
    }

    if ($Status -eq "ConfirmedDuplicate") { $ConfirmedGroups.Add($GroupSummary) }
    else { $NeedsHashGroups.Add($GroupSummary) }

    foreach ($Row in $GroupRows) {
        $OutputRows.Add([PSCustomObject]@{
            DB_ID               = $Row.DB_ID
            FileName            = $Row.FileName
            Directory           = $Row.Directory
            Path                = $Row.Path
            Length              = $Row.Length
            SizeGroupID         = $Row.SizeGroupID
            PartialHash         = $Row.PartialHash
            PartialHashGroupID  = $GroupID
            Status              = $Status
            IsOfflineOrCloud    = $Row.IsOfflineOrCloud
        })
    }
}

# Rows that errored this run (not checkpointed, so they won't appear in
# $AllHashedRows) -- preserved here so no DB_ID silently disappears.
# Anything not successfully hashed AND not deliberately skipped as
# cloud-only must have errored.
$HashedIDs = @{}
foreach ($r in $AllHashedRows) { $HashedIDs[$r.DB_ID] = $true }

$SkippedIDs = @{}
foreach ($r in $SkippedCloudRows) { $SkippedIDs[$r.DB_ID] = $true }

$ErrorRows = $CandidateRows | Where-Object {
    (-not $HashedIDs.ContainsKey($_.DB_ID)) -and (-not $SkippedIDs.ContainsKey($_.DB_ID))
}

foreach ($Row in $ErrorRows) {
    $OutputRows.Add([PSCustomObject]@{
        DB_ID               = $Row.DB_ID
        FileName            = $Row.FileName
        Directory           = $Row.Directory
        Path                = $Row.Path
        Length              = $Row.Length
        SizeGroupID         = $Row.SizeGroupID
        PartialHash         = $null
        PartialHashGroupID  = $null
        Status              = "Error"
        IsOfflineOrCloud    = $null
    })
}

foreach ($Row in $SkippedCloudRows) {
    $PrelimMatch = $PrelimByID[$Row.DB_ID]
    $OutputRows.Add([PSCustomObject]@{
        DB_ID               = $Row.DB_ID
        FileName            = $Row.FileName
        Directory           = $Row.Directory
        Path                = $Row.Path
        Length              = $Row.Length
        SizeGroupID         = $Row.SizeGroupID
        PartialHash         = $null
        PartialHashGroupID  = $null
        Status              = "SkippedCloudOnly"
        IsOfflineOrCloud    = if ($PrelimMatch) { $PrelimMatch.IsOfflineOrCloud } else { "True" }
    })
}

$Stopwatch.Stop()

# ----------------------------------------------------------------------------
# 9. Export PartialHashCandidates.csv, then clear the checkpoint (clean finish)
# ----------------------------------------------------------------------------
$OutputCsvPath = Join-Path $InventoryFolder "PartialHashCandidates.csv"
$OutputRows | Export-Csv -LiteralPath $OutputCsvPath -NoTypeInformation -Encoding UTF8

try {
    if (Test-Path -LiteralPath $CheckpointPath) {
        Remove-Item -LiteralPath $CheckpointPath -Force
    }
}
catch {
    # Non-fatal -- leftover checkpoint file just means a harmless resume-skip next time
}

# ----------------------------------------------------------------------------
# 10. Compute report statistics
# ----------------------------------------------------------------------------
$RuledOutCount    = ($OutputRows | Where-Object { $_.Status -eq "RuledOut" }).Count
$ConfirmedCount   = ($OutputRows | Where-Object { $_.Status -eq "ConfirmedDuplicate" }).Count
$NeedsHashCount   = ($OutputRows | Where-Object { $_.Status -eq "NeedsFullHash" }).Count
$SkippedCount     = ($OutputRows | Where-Object { $_.Status -eq "SkippedCloudOnly" }).Count
$CloudFilesHashed = ($AllHashedRows | Where-Object { $_.IsOfflineOrCloud -eq "True" }).Count

$ConfirmedReclaim = ($ConfirmedGroups | Measure-Object -Property PotentialReclaim -Sum).Sum
if (-not $ConfirmedReclaim) { $ConfirmedReclaim = 0 }

$NeedsHashReclaim = ($NeedsHashGroups | Measure-Object -Property PotentialReclaim -Sum).Sum
if (-not $NeedsHashReclaim) { $NeedsHashReclaim = 0 }

$RankedConfirmed = $ConfirmedGroups | Sort-Object PotentialReclaim -Descending
$RankedNeedsHash = $NeedsHashGroups | Sort-Object PotentialReclaim -Descending

$Throughput = if ($Stopwatch.Elapsed.TotalSeconds -gt 0) { $BytesRead / $Stopwatch.Elapsed.TotalSeconds } else { 0 }

# ----------------------------------------------------------------------------
# 11. Build the report text
# ----------------------------------------------------------------------------
function Add-GroupListing {
    param($Lines, $Groups, [int]$MaxGroups = 20)

    foreach ($g in ($Groups | Select-Object -First $MaxGroups)) {
        $Lines.Add(("  Group {0,3} | {1} files x {2} = up to {3} reclaimable" -f $g.GroupID, $g.Count, (Format-Bytes $g.Size), (Format-Bytes $g.PotentialReclaim)))
        $ShownRows = $g.Rows | Select-Object -First 5
        foreach ($r in $ShownRows) {
            $Lines.Add("      - $($r.Path)")
        }
        if ($g.Rows.Count -gt 5) {
            $Remaining = $g.Rows.Count - 5
            $Lines.Add("      ... and $Remaining more (see PartialHashCandidates.csv, PartialHashGroupID = $($g.GroupID))")
        }
    }
}

$ReportLines = [System.Collections.Generic.List[string]]::new()

$ReportLines.Add("=" * 70)
$ReportLines.Add(" THE FILE ORGANIZER -- PARTIAL HASH REPORT")
$ReportLines.Add(" Project   : $($Settings.ProjectName)")
$ReportLines.Add(" Run       : $($Settings.CurrentRun)")
$ReportLines.Add(" Generated : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$ReportLines.Add("=" * 70)
$ReportLines.Add("")
$ReportLines.Add("SUMMARY")
$ReportLines.Add(("  Candidate files (total)            : {0:N0}" -f $CandidateRows.Count))
$ReportLines.Add(("  Hashed this run                    : {0:N0}" -f $TotalToProcess))
$ReportLines.Add(("  Ruled out (not actually duplicates): {0:N0}" -f $RuledOutCount))
$ReportLines.Add(("  Confirmed duplicates (fully hashed): {0:N0} files, {1} groups, {2} reclaimable" -f $ConfirmedCount, $RankedConfirmed.Count, (Format-Bytes $ConfirmedReclaim)))
$ReportLines.Add(("  Still needs full hash (Script 4)   : {0:N0} files, {1} groups, up to {2} reclaimable" -f $NeedsHashCount, $RankedNeedsHash.Count, (Format-Bytes $NeedsHashReclaim)))
if ($SkippedCount -gt 0) {
    $ReportLines.Add(("  Skipped (cloud-only, not hashed)   : {0:N0}" -f $SkippedCount))
}
if ($ErrorCount -gt 0) {
    $ReportLines.Add(("  Errors (could not be hashed)       : {0:N0} (see Logs\errors_partialhash.txt)" -f $ErrorCount))
}
$ReportLines.Add(("  Cloud-only files hashed (may have triggered a download): {0:N0}" -f $CloudFilesHashed))
$ReportLines.Add("")
$ReportLines.Add("PERFORMANCE")
$ReportLines.Add("  Bytes read      : $(Format-Bytes $BytesRead)")
$ReportLines.Add("  Processing time : $($Stopwatch.Elapsed.ToString('hh\:mm\:ss'))")
$ReportLines.Add("  Throughput      : $(Format-Bytes $Throughput)/sec")
$ReportLines.Add("")
$ReportLines.Add("CONFIRMED DUPLICATE GROUPS (fully hashed -- no full hash needed)")
if ($RankedConfirmed.Count -eq 0) { $ReportLines.Add("  (none)") }
else { Add-GroupListing -Lines $ReportLines -Groups $RankedConfirmed }
$ReportLines.Add("")
$ReportLines.Add("GROUPS STILL NEEDING A FULL HASH (hand off to Script 4)")
if ($RankedNeedsHash.Count -eq 0) { $ReportLines.Add("  (none)") }
else { Add-GroupListing -Lines $ReportLines -Groups $RankedNeedsHash }
$ReportLines.Add("")
$ReportLines.Add("NEXT STEP")
$ReportLines.Add("  Run the full-hash script next -- it only needs to process files")
$ReportLines.Add("  marked Status = NeedsFullHash in PartialHashCandidates.csv.")
$ReportLines.Add("=" * 70)

$ReportPath = Join-Path $ReportsFolder "PartialHashReport.txt"
$ReportLines | Set-Content -LiteralPath $ReportPath -Encoding UTF8

# ----------------------------------------------------------------------------
# 12. Update settings.json
# ----------------------------------------------------------------------------
if (-not ($Settings.PSObject.Properties.Name -contains "LastPartialHashScan")) {
    $Settings | Add-Member -MemberType NoteProperty -Name "LastPartialHashScan" -Value $null
}
$Settings.LastPartialHashScan = (Get-Date).ToString("o")

$Settings | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $SettingsPath -Encoding UTF8

# ----------------------------------------------------------------------------
# 13. Finish up
# ----------------------------------------------------------------------------
Write-Host ""
Write-Host "Partial hash scan complete." -ForegroundColor Green
Write-Host "  Ruled out            : $RuledOutCount"
Write-Host "  Confirmed duplicates : $ConfirmedCount files ($(Format-Bytes $ConfirmedReclaim) reclaimable)"
Write-Host "  Still needs full hash: $NeedsHashCount files"
if ($SkippedCount -gt 0) { Write-Host "  Skipped (cloud-only) : $SkippedCount" }
Write-Host "  Duration             : $($Stopwatch.Elapsed.ToString('hh\:mm\:ss'))"
Write-Host ""

# Report is saved but not auto-opened -- see Dashboard.py for how reports are accessed.
