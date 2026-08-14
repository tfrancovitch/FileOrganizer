<#
    FullHash.ps1
    Part of: The File Organizer
    Version: 2.3.1

    Purpose:
        Reads the current run's PartialHashCandidates.csv. Only files marked
        Status = "NeedsFullHash" get a full SHA-256 hash computed (everything
        else was already resolved in Script 3). Re-groups those by full hash
        to separate true duplicates from partial-hash false positives.

        Then finalizes the run by merging EVERY file from
        PreliminaryInventory.csv (including ones excluded back at Script 2 as
        unique-by-size) into DuplicateHashInventory.csv, each tagged with a single
        FinalStatus and, where applicable, a DuplicateGroupID:

          - UniqueBySize          : excluded at Script 2, never a candidate
          - RuledOutByPartialHash : excluded at Script 3
          - RuledOutByFullHash    : matched on size + partial hash, but the
                                    full file content differs
          - ConfirmedDuplicate    : true, fully-confirmed duplicate (either
                                    confirmed already in Script 3 for small
                                    files, or here via full hash)
          - SkippedCloudOnly      : cloud-only file skipped this run
          - Error                 : could not be hashed at some stage

    Scaling features (v2.0.0):
        - CHECKPOINT/RESUME: successfully-hashed files are written to a
          small checkpoint CSV in this run's Logs\ folder as hashing
          proceeds. If interrupted and re-run, only files not yet hashed
          are processed. The checkpoint is deleted on a clean finish.
        - CLOUD-FILE SAFETY CHECK: warns before hashing any cloud-only
          file (a full hash reads the ENTIRE file, so this matters even
          more here than in the partial-hash pass). Same -Force /
          -SkipCloudOnly switches as PartialHash.ps1.
        - Dictionary-based grouping instead of Group-Object, for speed at
          high file counts (applies to both the duplicate-grouping logic
          and the master inventory's own report statistics).

    v2.1.0: files with paths over 260 characters are now handled
    correctly. Get-FullHash no longer uses the Get-FileHash cmdlet
    (its own long-path support was unverified) -- switched to a manual
    stream open using the \\?\ extended-length prefix (see Common.ps1's
    ConvertTo-LongPath), the same confirmed-working technique used in
    PartialHash.ps1.

    Usage:
        Run from inside a PROJECT's Scripts folder:
            .\FullHash.ps1
            .\FullHash.ps1 -Force            # hash cloud-only files, no prompt
            .\FullHash.ps1 -SkipCloudOnly    # always skip cloud-only files, no prompt

    Requires:
        - PartialHash.ps1 must have already been run for this project's
          current run.

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
    Write-Host "Run PreliminaryInventory.ps1, PotentialDuplicates.ps1, and PartialHash.ps1 first." -ForegroundColor Red
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

$PartialHashCsvPath = Join-Path $InventoryFolder "PartialHashCandidates.csv"
$PreliminaryCsvPath = Join-Path $InventoryFolder "PreliminaryInventory.csv"
$ErrorLogPath       = Join-Path $LogsFolder "errors_fullhash.txt"
$CheckpointPath     = Join-Path $LogsFolder "checkpoint_fullhash_raw.csv"

if (-not (Test-Path -LiteralPath $PartialHashCsvPath)) {
    Write-Host "ERROR: PartialHashCandidates.csv not found at:" -ForegroundColor Red
    Write-Host "  $PartialHashCsvPath" -ForegroundColor Red
    Write-Host "Run PartialHash.ps1 first." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path -LiteralPath $PreliminaryCsvPath)) {
    Write-Host "ERROR: PreliminaryInventory.csv not found at:" -ForegroundColor Red
    Write-Host "  $PreliminaryCsvPath" -ForegroundColor Red
    exit 1
}

Write-Host "Reading: $PartialHashCsvPath" -ForegroundColor Cyan
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
    # Switched from Get-FileHash to a manual stream open + hash, since
    # Get-FileHash's own long-path support isn't verified -- this way
    # both PartialHash.ps1 and FullHash.ps1 use the identical, confirmed-
    # working \\?\ prefix technique rather than trusting two different
    # code paths to behave the same way.
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
    # Dictionary-based grouping -- faster than Group-Object at high row counts.
    # $KeySelector is a scriptblock evaluated once per row.
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
# 3. Load inputs
# ----------------------------------------------------------------------------
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

$PartialRows     = Import-Csv -LiteralPath $PartialHashCsvPath
$PreliminaryRows = Import-Csv -LiteralPath $PreliminaryCsvPath

$NeedsFullHashRows = $PartialRows | Where-Object { $_.Status -eq "NeedsFullHash" }

# ----------------------------------------------------------------------------
# 4. Checkpoint resume -- skip anything already successfully hashed
#    (checkpoint stores just DB_ID -> FullHash; everything else is
#    reconstructed from PartialHashCandidates.csv, already in memory)
# ----------------------------------------------------------------------------
$AlreadyProcessed = @{}   # DB_ID -> FullHash

if (Test-Path -LiteralPath $CheckpointPath) {
    Write-Host "Found an existing checkpoint -- resuming an interrupted full-hash pass." -ForegroundColor Yellow
    $CheckpointRows = Import-Csv -LiteralPath $CheckpointPath
    foreach ($r in $CheckpointRows) { $AlreadyProcessed[$r.DB_ID] = $r.FullHash }
    Write-Host "  $($CheckpointRows.Count) files already hashed previously; skipping those." -ForegroundColor Yellow
    Write-Host ""
}

$RowsToProcess = $NeedsFullHashRows | Where-Object { -not $AlreadyProcessed.ContainsKey($_.DB_ID) }

# ----------------------------------------------------------------------------
# 5. Cloud-file safety check (IsOfflineOrCloud already carried through from
#    Script 3 -- no need to re-join to PreliminaryInventory.csv for this)
# ----------------------------------------------------------------------------
$SkippedCloudRows = @()

$CloudOnlyRows = $RowsToProcess | Where-Object { $_.IsOfflineOrCloud -eq "True" }

if ($CloudOnlyRows.Count -gt 0) {
    $CloudBytes = ($CloudOnlyRows | ForEach-Object { ConvertTo-Int64Safe $_.Length } | Measure-Object -Sum).Sum
    if (-not $CloudBytes) { $CloudBytes = 0 }

    Write-Host "WARNING: $($CloudOnlyRows.Count) of $($RowsToProcess.Count) files to hash are cloud-only" -ForegroundColor Yellow
    Write-Host "(not fully downloaded locally). A FULL hash reads the entire file, so this" -ForegroundColor Yellow
    Write-Host "will force a complete download of each one." -ForegroundColor Yellow
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
# 6. Full-hash each remaining file, checkpointing as we go
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
        # Only clear on a confirmed-successful write -- see PartialHash.ps1
        # v2.2.0 for why (a real OneDrive lock collision during testing).
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
        Write-Progress -Activity "Computing full hashes" `
            -Status "$Processed of $TotalToProcess files this run ($([math]::Round($Stopwatch.Elapsed.TotalSeconds,1))s elapsed)" `
            -PercentComplete $PercentComplete
        $LastProgressUpdate = Get-Date
    }
}

Flush-Checkpoint -Buffer $Buffer -Path $CheckpointPath -FileExists ([ref]$CheckpointFileExists)
Write-Progress -Activity "Computing full hashes" -Completed

# ----------------------------------------------------------------------------
# 7. Read back the complete checkpoint (resumed + new) -> DB_ID => FullHash
# ----------------------------------------------------------------------------
$FullHashByID = @{}
if (Test-Path -LiteralPath $CheckpointPath) {
    Import-Csv -LiteralPath $CheckpointPath | ForEach-Object { $FullHashByID[$_.DB_ID] = $_.FullHash }
}

# Reconstruct full rows (all original columns + FullHash) for every
# NeedsFullHash row that was successfully hashed (resumed or new)
$FullHashedRows = [System.Collections.Generic.List[object]]::new()
foreach ($Row in $NeedsFullHashRows) {
    if ($FullHashByID.ContainsKey($Row.DB_ID)) {
        $FullHashedRows.Add([PSCustomObject]@{
            DB_ID               = $Row.DB_ID
            FileName            = $Row.FileName
            Directory           = $Row.Directory
            Path                = $Row.Path
            Length              = $Row.Length
            SizeGroupID         = $Row.SizeGroupID
            PartialHash         = $Row.PartialHash
            PartialHashGroupID  = $Row.PartialHashGroupID
            FullHash            = $FullHashByID[$Row.DB_ID]
        })
    }
}

# ----------------------------------------------------------------------------
# 8. Build the final per-DB_ID status map
# ----------------------------------------------------------------------------
$FinalByID = @{}
$NextDuplicateGroupID = 1

$ConfirmedGroupsForReport = [System.Collections.Generic.List[object]]::new()

# --- 8a. Groups already fully confirmed back in Script 3 (small files) ---
$AlreadyConfirmed = $PartialRows | Where-Object { $_.Status -eq "ConfirmedDuplicate" }
$AlreadyConfirmedGroups = Group-ByKey -Rows $AlreadyConfirmed -KeySelector { param($r) $r.PartialHashGroupID }

foreach ($Key in $AlreadyConfirmedGroups.Keys) {
    $GroupRows = $AlreadyConfirmedGroups[$Key]
    $GroupID = $NextDuplicateGroupID
    $NextDuplicateGroupID++

    $Size = ConvertTo-Int64Safe $GroupRows[0].Length
    $ConfirmedGroupsForReport.Add([PSCustomObject]@{
        GroupID          = $GroupID
        Size             = $Size
        Count            = $GroupRows.Count
        PotentialReclaim = $Size * ($GroupRows.Count - 1)
        Rows             = $GroupRows
        ConfirmedAt      = "PartialHash"
    })

    foreach ($Row in $GroupRows) {
        $FinalByID[$Row.DB_ID] = [PSCustomObject]@{
            FinalStatus      = "ConfirmedDuplicate"
            DuplicateGroupID = $GroupID
            PartialHash      = $Row.PartialHash
            FullHash         = $null
        }
    }
}

# --- 8b. Rows ruled out, errored, or skipped back in Script 3 ---
foreach ($Row in ($PartialRows | Where-Object { $_.Status -eq "RuledOut" })) {
    $FinalByID[$Row.DB_ID] = [PSCustomObject]@{
        FinalStatus = "RuledOutByPartialHash"; DuplicateGroupID = $null
        PartialHash = $Row.PartialHash; FullHash = $null
    }
}
foreach ($Row in ($PartialRows | Where-Object { $_.Status -eq "Error" })) {
    $FinalByID[$Row.DB_ID] = [PSCustomObject]@{
        FinalStatus = "Error"; DuplicateGroupID = $null; PartialHash = $null; FullHash = $null
    }
}
foreach ($Row in ($PartialRows | Where-Object { $_.Status -eq "SkippedCloudOnly" })) {
    $FinalByID[$Row.DB_ID] = [PSCustomObject]@{
        FinalStatus = "SkippedCloudOnly"; DuplicateGroupID = $null; PartialHash = $null; FullHash = $null
    }
}

# --- 8c. Re-group the full-hash results (narrowing within each PartialHashGroupID) ---
$FullHashSubGroups = Group-ByKey -Rows $FullHashedRows -KeySelector { param($r) "$($r.PartialHashGroupID)|$($r.FullHash)" }

foreach ($Key in $FullHashSubGroups.Keys) {
    $GroupRows = $FullHashSubGroups[$Key]

    if ($GroupRows.Count -eq 1) {
        $Row = $GroupRows[0]
        $FinalByID[$Row.DB_ID] = [PSCustomObject]@{
            FinalStatus = "RuledOutByFullHash"; DuplicateGroupID = $null
            PartialHash = $Row.PartialHash; FullHash = $Row.FullHash
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
        ConfirmedAt      = "FullHash"
    })

    foreach ($Row in $GroupRows) {
        $FinalByID[$Row.DB_ID] = [PSCustomObject]@{
            FinalStatus      = "ConfirmedDuplicate"
            DuplicateGroupID = $GroupID
            PartialHash      = $Row.PartialHash
            FullHash         = $Row.FullHash
        }
    }
}

# --- 8d. Anything still unaccounted for among NeedsFullHash rows errored or was skipped ---
foreach ($Row in $SkippedCloudRows) {
    $FinalByID[$Row.DB_ID] = [PSCustomObject]@{
        FinalStatus = "SkippedCloudOnly"; DuplicateGroupID = $null
        PartialHash = $Row.PartialHash; FullHash = $null
    }
}

$AccountedIDs = @{}
foreach ($k in $FinalByID.Keys) { $AccountedIDs[$k] = $true }

foreach ($Row in $NeedsFullHashRows) {
    if (-not $AccountedIDs.ContainsKey($Row.DB_ID)) {
        $FinalByID[$Row.DB_ID] = [PSCustomObject]@{
            FinalStatus = "Error"; DuplicateGroupID = $null
            PartialHash = $Row.PartialHash; FullHash = $null
        }
    }
}

$Stopwatch.Stop()

# ----------------------------------------------------------------------------
# 9. Export DuplicateHashInventory.csv, then clear the checkpoint (clean finish)
# ----------------------------------------------------------------------------
$DuplicateHashRows = [System.Collections.Generic.List[object]]::new()

foreach ($Row in $PreliminaryRows) {
    $Final = $FinalByID[$Row.DB_ID]

    $DuplicateHashRows.Add([PSCustomObject]@{
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
        PartialHash      = if ($Final) { $Final.PartialHash } else { $null }
        FullHash         = if ($Final) { $Final.FullHash } else { $null }
        FinalStatus      = if ($Final) { $Final.FinalStatus } else { "UniqueBySize" }
        DuplicateGroupID = if ($Final) { $Final.DuplicateGroupID } else { $null }
    })
}

$DuplicateHashCsvPath = Join-Path $InventoryFolder "DuplicateHashInventory.csv"
$DuplicateHashRows | Export-Csv -LiteralPath $DuplicateHashCsvPath -NoTypeInformation -Encoding UTF8

try {
    if (Test-Path -LiteralPath $CheckpointPath) {
        Remove-Item -LiteralPath $CheckpointPath -Force
    }
}
catch {
    # Non-fatal -- leftover checkpoint just means a harmless resume-skip next time
}

# ----------------------------------------------------------------------------
# 10. Compute report statistics (recomputed independently, Dictionary-based)
# ----------------------------------------------------------------------------
$TotalFiles = $DuplicateHashRows.Count
$TotalBytes = ($DuplicateHashRows | ForEach-Object { ConvertTo-Int64Safe $_.Length } | Measure-Object -Sum).Sum
if (-not $TotalBytes) { $TotalBytes = 0 }

$StatusDict = Group-ByKey -Rows $DuplicateHashRows -KeySelector { param($r) $r.FinalStatus }
$StatusCounts = foreach ($Key in $StatusDict.Keys) {
    [PSCustomObject]@{ Status = $Key; Count = $StatusDict[$Key].Count }
}

$ExtensionDict = Group-ByKey -Rows $DuplicateHashRows -KeySelector { param($r) if ([string]::IsNullOrEmpty($r.Extension)) { "(none)" } else { $r.Extension } }
$ByExtension = foreach ($Key in $ExtensionDict.Keys) {
    $Group = $ExtensionDict[$Key]
    [PSCustomObject]@{
        Extension = $Key
        Count     = $Group.Count
        TotalSize = ($Group | ForEach-Object { ConvertTo-Int64Safe $_.Length } | Measure-Object -Sum).Sum
    }
}
$ByExtension = $ByExtension | Sort-Object TotalSize -Descending

$MaxDepth = if ($DuplicateHashRows.Count -gt 0) { ($DuplicateHashRows | ForEach-Object { [int]$_.Depth } | Measure-Object -Maximum).Maximum } else { 0 }
$EmptyFileCount = ($DuplicateHashRows | Where-Object { (ConvertTo-Int64Safe $_.Length) -eq 0 }).Count
$HiddenCount   = ($DuplicateHashRows | Where-Object { $_.Attributes -match "Hidden" }).Count
$SystemCount   = ($DuplicateHashRows | Where-Object { $_.Attributes -match "System" }).Count
$LongPathCount = ($DuplicateHashRows | Where-Object { [int]$_.PathLength -gt 260 }).Count

# Drift check against the Preliminary Inventory (should always match --
# same universe of files, just annotated)
$DriftFileCount = $TotalFiles - $PreliminaryRows.Count
$PrelimTotalBytes = ($PreliminaryRows | ForEach-Object { ConvertTo-Int64Safe $_.Length } | Measure-Object -Sum).Sum
$DriftBytes = $TotalBytes - $PrelimTotalBytes

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
$ReportLines.Add(" THE FILE ORGANIZER -- DUPLICATE HASH INVENTORY REPORT")
$ReportLines.Add(" Project   : $($Settings.ProjectName)")
$ReportLines.Add(" Run       : $($Settings.CurrentRun)")
$ReportLines.Add(" Generated : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$ReportLines.Add("=" * 70)
$ReportLines.Add("")
$ReportLines.Add("SCAN SUMMARY (recomputed independently from DuplicateHashInventory.csv)")
$ReportLines.Add(("  Total files              : {0:N0}" -f $TotalFiles))
$ReportLines.Add("  Total size                : $(Format-Bytes $TotalBytes) ($TotalBytes bytes)")
$ReportLines.Add("  Maximum folder depth      : $MaxDepth")
$ReportLines.Add("  Empty files               : $EmptyFileCount")
$ReportLines.Add("  Hidden files              : $HiddenCount")
$ReportLines.Add("  System files              : $SystemCount")
$ReportLines.Add("  Paths over 260 characters : $LongPathCount")
$ReportLines.Add("")
$ReportLines.Add("FILE TYPE BREAKDOWN (by extension)")
foreach ($ext in $ByExtension) {
    $ReportLines.Add(("  {0,-14} {1,8:N0} files   {2}" -f $ext.Extension, $ext.Count, (Format-Bytes $ext.TotalSize)))
}
$ReportLines.Add("")
$ReportLines.Add("DRIFT CHECK (vs. PreliminaryInventory.csv from this same run)")
$ReportLines.Add("  File count difference : $DriftFileCount")
$ReportLines.Add("  Byte count difference : $DriftBytes")
if ($DriftFileCount -ne 0 -or $DriftBytes -ne 0) {
    $ReportLines.Add("  ** Non-zero drift -- files may have changed since the Preliminary scan **")
}
$ReportLines.Add("")
$ReportLines.Add("DUPLICATE RESOLUTION SUMMARY")
foreach ($sc in $StatusCounts) {
    $ReportLines.Add(("  {0,-24}: {1:N0}" -f $sc.Status, $sc.Count))
}
$ReportLines.Add("")
$ReportLines.Add(("  Confirmed duplicate groups : {0:N0}" -f $ConfirmedGroupsRanked.Count))
$ReportLines.Add(("  Redundant files (all but one per group): {0:N0}" -f $TotalRedundantFiles))
$ReportLines.Add(("  Confirmed reclaimable space: {0}" -f (Format-Bytes $TotalConfirmedReclaim)))
$ReportLines.Add("")
$ReportLines.Add("PERFORMANCE")
$ReportLines.Add("  Files fully hashed this run : $TotalToProcess")
$ReportLines.Add("  Bytes read (full hash pass) : $(Format-Bytes $BytesRead)")
$ReportLines.Add("  Processing time             : $($Stopwatch.Elapsed.ToString('hh\:mm\:ss'))")
if ($ErrorCount -gt 0) {
    $ReportLines.Add("  Errors (could not be hashed): $ErrorCount (see Logs\errors_fullhash.txt)")
}
$ReportLines.Add("")
$ReportLines.Add("CONFIRMED DUPLICATE GROUPS (final)")
if ($ConfirmedGroupsRanked.Count -eq 0) {
    $ReportLines.Add("  (none)")
}
else {
    $MaxGroupsShown = 20
    $GroupsToShow = $ConfirmedGroupsRanked | Select-Object -First $MaxGroupsShown
    foreach ($g in $GroupsToShow) {
        $ReportLines.Add(("  Group {0,3} | {1} files x {2} = {3} reclaimable  [confirmed via: {4}]" -f $g.GroupID, $g.Count, (Format-Bytes $g.Size), (Format-Bytes $g.PotentialReclaim), $g.ConfirmedAt))
        $ShownRows = $g.Rows | Select-Object -First 5
        foreach ($r in $ShownRows) {
            $ReportLines.Add("      - $($r.Path)")
        }
        if ($g.Rows.Count -gt 5) {
            $Remaining = $g.Rows.Count - 5
            $ReportLines.Add("      ... and $Remaining more (see DuplicateHashInventory.csv, DuplicateGroupID = $($g.GroupID))")
        }
    }
    if ($ConfirmedGroupsRanked.Count -gt $MaxGroupsShown) {
        $RemainingGroups = $ConfirmedGroupsRanked.Count - $MaxGroupsShown
        $ReportLines.Add("")
        $ReportLines.Add("  ... and $RemainingGroups more group(s) not shown here (see DuplicateHashInventory.csv for the full list)")
    }
}
$ReportLines.Add("=" * 70)

$ReportPath = Join-Path $ReportsFolder "DuplicateHashInventoryReport.txt"
$ReportLines | Set-Content -LiteralPath $ReportPath -Encoding UTF8

# ----------------------------------------------------------------------------
# 12. Update settings.json
# ----------------------------------------------------------------------------
if (-not ($Settings.PSObject.Properties.Name -contains "LastFullHashScan")) {
    $Settings | Add-Member -MemberType NoteProperty -Name "LastFullHashScan" -Value $null
}
$Settings.LastFullHashScan = (Get-Date).ToString("o")

$Settings | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $SettingsPath -Encoding UTF8

# ----------------------------------------------------------------------------
# 13. Finish up
# ----------------------------------------------------------------------------
Write-Host ""
Write-Host "Master Inventory complete." -ForegroundColor Green
Write-Host "  Confirmed duplicate groups : $($ConfirmedGroupsRanked.Count)"
Write-Host "  Redundant files            : $TotalRedundantFiles"
Write-Host "  Confirmed reclaimable space: $(Format-Bytes $TotalConfirmedReclaim)"
Write-Host "  Duration                   : $($Stopwatch.Elapsed.ToString('hh\:mm\:ss'))"
Write-Host ""

# Report is saved but not auto-opened -- see Dashboard.py for how reports are accessed.
