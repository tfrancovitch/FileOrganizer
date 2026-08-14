<#
    PotentialDuplicates.ps1
    Part of: The File Organizer
    Version: 1.1.0

    Purpose:
        Reads the current run's PreliminaryInventory.csv and groups files by
        size (Length) -- a free, no-scanning pre-filter for duplicate
        detection. Files that are unique in size are excluded entirely.
        Files sharing a size with >=1 other file are written to
        potentialduplicates.csv, tagged with a SizeGroupID, ready for the
        partial-hash pass (Script 3).

    Usage:
        Run from inside a PROJECT's Scripts folder, with no arguments:
            .\PotentialDuplicates.ps1

    Requires:
        - PreliminaryInventory.ps1 must have already been run for this
          project (settings.json must have a valid CurrentRun).

    Note:
        This script does NOT create a new Run folder. It writes into the
        SAME run folder that PreliminaryInventory.ps1 just created, since
        it's a continuation of the same scan session.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$SettingsPath
)

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
    Write-Host "Run PreliminaryInventory.ps1 first." -ForegroundColor Red
    exit 1
}

# ----------------------------------------------------------------------------
# 1. Locate the current run's folders and the Preliminary Inventory
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

if (-not (Test-Path -LiteralPath $PreliminaryCsvPath)) {
    Write-Host "ERROR: PreliminaryInventory.csv not found at:" -ForegroundColor Red
    Write-Host "  $PreliminaryCsvPath" -ForegroundColor Red
    exit 1
}

Write-Host "Reading: $PreliminaryCsvPath" -ForegroundColor Cyan
Write-Host ""

# ----------------------------------------------------------------------------
# 2. Load the inventory and prep
# ----------------------------------------------------------------------------
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

$Rows = Import-Csv -LiteralPath $PreliminaryCsvPath

if ($Rows.Count -eq 0) {
    Write-Host "The Preliminary Inventory is empty -- nothing to group." -ForegroundColor Yellow
    exit 0
}

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

$TotalFileCount = $Rows.Count
$TotalBytes     = ($Rows | ForEach-Object { ConvertTo-Int64Safe $_.Length } | Measure-Object -Sum).Sum
if (-not $TotalBytes) { $TotalBytes = 0 }

# ----------------------------------------------------------------------------
# 3. Group by file size (Length)
# ----------------------------------------------------------------------------
$AllSizeGroups = $Rows | Group-Object { ConvertTo-Int64Safe $_.Length }

$DuplicateCandidateGroups = $AllSizeGroups | Where-Object { $_.Count -gt 1 }
$UniqueBySizeGroups       = $AllSizeGroups | Where-Object { $_.Count -eq 1 }

$UniqueBySizeCount = $UniqueBySizeGroups.Count

# Rank groups by potential reclaimable space: (size) x (count - 1)
$RankedGroups = $DuplicateCandidateGroups | ForEach-Object {
    $Size = ConvertTo-Int64Safe $_.Name
    [PSCustomObject]@{
        Size              = $Size
        Count             = $_.Count
        PotentialReclaim  = $Size * ($_.Count - 1)
        Rows              = $_.Group
    }
} | Sort-Object PotentialReclaim -Descending

# ----------------------------------------------------------------------------
# 4. Assign SizeGroupID and build output rows
# ----------------------------------------------------------------------------
$OutputRows = [System.Collections.Generic.List[object]]::new()
$GroupID = 1

foreach ($Group in $RankedGroups) {
    foreach ($Row in $Group.Rows) {
        $OutputRows.Add([PSCustomObject]@{
            DB_ID       = $Row.DB_ID
            FileName    = $Row.FileName
            Directory   = $Row.Directory
            Path        = $Row.Path
            Length      = $Row.Length
            SizeGroupID = $GroupID
        })
    }
    $GroupID++
}

$Stopwatch.Stop()

# ----------------------------------------------------------------------------
# 5. Export potentialduplicates.csv
# ----------------------------------------------------------------------------
$OutputCsvPath = Join-Path $InventoryFolder "PotentialDuplicates.csv"
$OutputRows | Export-Csv -LiteralPath $OutputCsvPath -NoTypeInformation -Encoding UTF8

# ----------------------------------------------------------------------------
# 6. Compute report statistics
# ----------------------------------------------------------------------------
$CandidateFileCount = $OutputRows.Count
$CandidateBytes     = ($OutputRows | ForEach-Object { ConvertTo-Int64Safe $_.Length } | Measure-Object -Sum).Sum
if (-not $CandidateBytes) { $CandidateBytes = 0 }

$PercentFilesInvolved = if ($TotalFileCount -gt 0) { [math]::Round(($CandidateFileCount / $TotalFileCount) * 100, 1) } else { 0 }
$PercentBytesInvolved = if ($TotalBytes -gt 0) { [math]::Round(($CandidateBytes / $TotalBytes) * 100, 1) } else { 0 }

$TotalPotentialReclaim = ($RankedGroups | Measure-Object -Property PotentialReclaim -Sum).Sum
if (-not $TotalPotentialReclaim) { $TotalPotentialReclaim = 0 }

$TopGroups = $RankedGroups | Select-Object -First 20

# ----------------------------------------------------------------------------
# 7. Build the report text
# ----------------------------------------------------------------------------
$ReportLines = [System.Collections.Generic.List[string]]::new()

$ReportLines.Add("=" * 70)
$ReportLines.Add(" THE FILE ORGANIZER -- POTENTIAL DUPLICATES REPORT")
$ReportLines.Add(" Project   : $($Settings.ProjectName)")
$ReportLines.Add(" Run       : $($Settings.CurrentRun)")
$ReportLines.Add(" Generated : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$ReportLines.Add("=" * 70)
$ReportLines.Add("")
$ReportLines.Add("SUMMARY")
$ReportLines.Add(("  Total files in inventory        : {0:N0}" -f $TotalFileCount))
$ReportLines.Add(("  Files unique by size (excluded)  : {0:N0}" -f $UniqueBySizeCount))
$ReportLines.Add(("  Files with a size-match (kept)   : {0:N0} ({1}% of files, {2}% of bytes)" -f $CandidateFileCount, $PercentFilesInvolved, $PercentBytesInvolved))
$ReportLines.Add(("  Distinct size-groups found       : {0:N0}" -f $RankedGroups.Count))
$ReportLines.Add("  Max theoretical reclaimable space: $(Format-Bytes $TotalPotentialReclaim)")
$ReportLines.Add("    (assumes every file in every group turns out to be a true")
$ReportLines.Add("     duplicate, keeping only one copy -- Script 3/4 will confirm)")
$ReportLines.Add("  Processing time                  : $($Stopwatch.Elapsed.ToString('hh\:mm\:ss'))")
$ReportLines.Add("")
$ReportLines.Add("TOP SIZE-GROUPS (by potential reclaimable space)")
$i = 1
foreach ($g in $TopGroups) {
    $ReportLines.Add(("  Group {0,3} | {1} files x {2} = up to {3} reclaimable" -f $i, $g.Count, (Format-Bytes $g.Size), (Format-Bytes $g.PotentialReclaim)))
    $ShownRows = $g.Rows | Select-Object -First 5
    foreach ($r in $ShownRows) {
        $ReportLines.Add("      - $($r.Path)")
    }
    if ($g.Rows.Count -gt 5) {
        $Remaining = $g.Rows.Count - 5
        $ReportLines.Add("      ... and $Remaining more (see PotentialDuplicates.csv, SizeGroupID = $i)")
    }
    $i++
}
$ReportLines.Add("")
$ReportLines.Add("NEXT STEP")
$ReportLines.Add("  These are SIZE matches only -- not yet confirmed duplicates.")
$ReportLines.Add("  Run the next script (partial-hash pass) to narrow these down.")
$ReportLines.Add("=" * 70)

$ReportPath = Join-Path $ReportsFolder "PotentialDuplicatesReport.txt"
$ReportLines | Set-Content -LiteralPath $ReportPath -Encoding UTF8

# ----------------------------------------------------------------------------
# 8. Update settings.json (timestamp only -- no new DB IDs assigned here)
# ----------------------------------------------------------------------------
if (-not ($Settings.PSObject.Properties.Name -contains "LastPotentialDuplicatesScan")) {
    $Settings | Add-Member -MemberType NoteProperty -Name "LastPotentialDuplicatesScan" -Value $null
}
$Settings.LastPotentialDuplicatesScan = (Get-Date).ToString("o")

# Step 6: summary counts for the dashboard's post-pre-scan info screen.
foreach ($Field in @("LastPotentialDuplicatesCandidateCount", "LastPotentialDuplicatesGroupCount", "LastPotentialDuplicatesMaxReclaim")) {
    if (-not ($Settings.PSObject.Properties.Name -contains $Field)) {
        $Settings | Add-Member -MemberType NoteProperty -Name $Field -Value $null
    }
}
$Settings.LastPotentialDuplicatesCandidateCount = $CandidateFileCount
$Settings.LastPotentialDuplicatesGroupCount     = $RankedGroups.Count
$Settings.LastPotentialDuplicatesMaxReclaim     = $TotalPotentialReclaim

$Settings | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $SettingsPath -Encoding UTF8

# ----------------------------------------------------------------------------
# 9. Finish up
# ----------------------------------------------------------------------------
Write-Host ""
Write-Host "Potential duplicates scan complete." -ForegroundColor Green
Write-Host "  Size-groups found  : $($RankedGroups.Count)"
Write-Host "  Files involved     : $CandidateFileCount of $TotalFileCount"
Write-Host "  Max reclaimable    : $(Format-Bytes $TotalPotentialReclaim)"
Write-Host "  Duration           : $($Stopwatch.Elapsed.ToString('hh\:mm\:ss'))"
Write-Host ""

# Report is saved but not auto-opened -- see Dashboard.py for how reports are accessed.
