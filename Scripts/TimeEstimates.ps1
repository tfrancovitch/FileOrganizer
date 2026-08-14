<#
    TimeEstimates.ps1
    Part of: The File Organizer
    Version: 1.0.0

    Purpose:
        Produces conservative time estimates for the Choose Run Type
        screen (Duplicate Run vs. Full Run), using live per-run
        calibration -- never cross-project historical data, per the
        litigation-discovery reproducibility requirement established
        early in this project.

        Mechanism: hashes a small real sample of files from THIS run's
        actual data, right now, to measure genuine throughput on this
        machine. That measured rate is then deliberately discounted (not
        used at face value) before being used to estimate the rest of
        the workload -- the explicit bias requirement is "rather be told
        30 minutes and have it take 15, than be told 15 and have it take
        30." A safety factor achieves this by assuming real throughput
        will be slower than the calibration sample measured, not by
        trying to predict the "true" time more precisely.

        Safety factors (starting points, not precisely derived --
        worth adjusting based on real-world experience over time,
        though never via cross-project history; any such adjustment
        would need to be a hardcoded constant shipped with the tool,
        identical on every machine, not learned/cached per-machine):
          - Local disk (SSD/HDD/Unknown) : assume 60% of calibrated speed
          - Network                      : assume 40% of calibrated speed
                                            (network conditions vary more
                                            unpredictably than local disk)

        Two estimates are produced:
          - Duplicate Run: based on PotentialDuplicates.csv's candidate
            byte total (only files sharing a size with another file get
            hashed in this pipeline)
          - Full Run: based on PreliminaryInventory.csv's total byte
            total (every file gets hashed)

        Both estimates get written to settings.json for the Choose Run
        Type screen to read and display -- calibration only needs to run
        once per Pre-Scan, not on every screen render.

    Usage:
        .\TimeEstimates.ps1 -SettingsPath <path>

    Requires:
        - PreliminaryInventory.ps1 (and ideally PotentialDuplicates.ps1)
          must have already run for this project's current run.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$SettingsPath,

    [int]$SampleFileCount = 15,
    [int64]$SampleByteCap = 52428800   # 50 MB -- keep calibration fast regardless of file sizes present
)

. (Join-Path $PSScriptRoot "Common.ps1")

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
    exit 1
}

$RunFolder       = Join-Path $ProjectRoot "Runs\$($Settings.CurrentRun)"
$InventoryFolder = Join-Path $RunFolder "Inventory"

$PreliminaryCsvPath = Join-Path $InventoryFolder "PreliminaryInventory.csv"
$CandidatesCsvPath  = Join-Path $InventoryFolder "PotentialDuplicates.csv"

if (-not (Test-Path -LiteralPath $PreliminaryCsvPath)) {
    Write-Host "ERROR: PreliminaryInventory.csv not found -- run Pre-Scan first." -ForegroundColor Red
    exit 1
}

function ConvertTo-Int64Safe {
    param($Value)
    try { return [int64]$Value } catch { return 0 }
}

function Format-Duration {
    param([double]$Seconds)
    if ($Seconds -lt 60) { return "under a minute" }
    $Minutes = [math]::Ceiling($Seconds / 60)
    if ($Minutes -lt 60) { return "~$Minutes min" }
    $Hours = [math]::Floor($Minutes / 60)
    $RemMinutes = $Minutes % 60
    if ($RemMinutes -eq 0) { return "~$Hours hr" }
    return "~$Hours hr $RemMinutes min"
}

# ----------------------------------------------------------------------------
# 1. Live calibration -- hash a small real sample, right now, on this run's
#    actual data. Deliberately skips cloud-only files (calibration should
#    measure local read speed, not trigger an unwanted download) and picks
#    a NON-EMPTY sample where possible, since a 0-byte file tells us
#    nothing about throughput.
# ----------------------------------------------------------------------------
$PreliminaryRows = Import-Csv -LiteralPath $PreliminaryCsvPath

$SampleCandidates = $PreliminaryRows |
    Where-Object { $_.IsOfflineOrCloud -ne "True" -and (ConvertTo-Int64Safe $_.Length) -gt 0 } |
    Select-Object -First $SampleFileCount

$CalibrationBytesRead = 0
$CalibrationStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$SampleFilesUsed = 0

foreach ($Row in $SampleCandidates) {
    if ($CalibrationBytesRead -ge $SampleByteCap) { break }
    try {
        $longPath = ConvertTo-LongPath -Path $Row.Path
        $stream = [System.IO.File]::Open($longPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        try {
            $sha256 = [System.Security.Cryptography.SHA256]::Create()
            try {
                [void]$sha256.ComputeHash($stream)
                $CalibrationBytesRead += ConvertTo-Int64Safe $Row.Length
                $SampleFilesUsed++
            }
            finally { $sha256.Dispose() }
        }
        finally { $stream.Dispose() }
    }
    catch {
        # A single unreadable sample file shouldn't break calibration --
        # just move on to the next candidate.
        continue
    }
}

$CalibrationStopwatch.Stop()
$CalibrationSeconds = [math]::Max($CalibrationStopwatch.Elapsed.TotalSeconds, 0.01)  # avoid divide-by-zero on a very fast/tiny sample

if ($SampleFilesUsed -eq 0 -or $CalibrationBytesRead -eq 0) {
    # No usable sample (e.g. every file is cloud-only or empty) -- fall
    # back to a deliberately conservative flat assumption rather than a
    # divide-by-zero or a wildly wrong number from zero real data.
    Write-Host "WARNING: No usable local sample for calibration -- using a conservative flat estimate." -ForegroundColor Yellow
    $MeasuredThroughputBytesPerSec = 5MB  # conservative flat assumption, not measured
}
else {
    $MeasuredThroughputBytesPerSec = $CalibrationBytesRead / $CalibrationSeconds
}

# ----------------------------------------------------------------------------
# 2. Apply the conservative safety factor -- deliberately assume real
#    throughput will be SLOWER than what calibration measured, per the
#    explicit bias requirement (over-estimate, not under-estimate).
# ----------------------------------------------------------------------------
$DriveType = $Settings.TargetDriveType
$SafetyFactor = if ($DriveType -eq "Network") { 0.4 } else { 0.6 }
$SafeThroughputBytesPerSec = $MeasuredThroughputBytesPerSec * $SafetyFactor

# ----------------------------------------------------------------------------
# 3. Compute both estimates
# ----------------------------------------------------------------------------
$TotalBytes = ($PreliminaryRows | ForEach-Object { ConvertTo-Int64Safe $_.Length } | Measure-Object -Sum).Sum
if (-not $TotalBytes) { $TotalBytes = 0 }

$CandidateBytes = 0
if (Test-Path -LiteralPath $CandidatesCsvPath) {
    $CandidateRows = Import-Csv -LiteralPath $CandidatesCsvPath
    $CandidateBytes = ($CandidateRows | ForEach-Object { ConvertTo-Int64Safe $_.Length } | Measure-Object -Sum).Sum
    if (-not $CandidateBytes) { $CandidateBytes = 0 }
}

$DuplicateRunSeconds = if ($SafeThroughputBytesPerSec -gt 0) { $CandidateBytes / $SafeThroughputBytesPerSec } else { 0 }
$FullRunSeconds      = if ($SafeThroughputBytesPerSec -gt 0) { $TotalBytes / $SafeThroughputBytesPerSec } else { 0 }

$DuplicateRunEstimateText = Format-Duration -Seconds $DuplicateRunSeconds
$FullRunEstimateText      = Format-Duration -Seconds $FullRunSeconds

# ----------------------------------------------------------------------------
# 4. Record to settings.json -- calibration runs once per Pre-Scan, not on
#    every screen render.
# ----------------------------------------------------------------------------
foreach ($Field in @("DuplicateRunEstimateText", "FullRunEstimateText", "CalibrationThroughputBytesPerSec", "CalibrationSafetyFactor", "CalibrationSampleFileCount")) {
    if (-not ($Settings.PSObject.Properties.Name -contains $Field)) {
        $Settings | Add-Member -MemberType NoteProperty -Name $Field -Value $null
    }
}
$Settings.DuplicateRunEstimateText          = $DuplicateRunEstimateText
$Settings.FullRunEstimateText               = $FullRunEstimateText
$Settings.CalibrationThroughputBytesPerSec  = [math]::Round($SafeThroughputBytesPerSec)
$Settings.CalibrationSafetyFactor           = $SafetyFactor
$Settings.CalibrationSampleFileCount        = $SampleFilesUsed

$Settings | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $SettingsPath -Encoding UTF8

Write-Host "Calibration complete." -ForegroundColor Green
Write-Host "  Sample: $SampleFilesUsed file(s), $([math]::Round($CalibrationBytesRead / 1MB, 1)) MB, $([math]::Round($CalibrationSeconds, 2))s"
Write-Host "  Measured throughput : $([math]::Round($MeasuredThroughputBytesPerSec / 1MB, 1)) MB/s"
Write-Host "  Safety-adjusted     : $([math]::Round($SafeThroughputBytesPerSec / 1MB, 1)) MB/s (factor: $SafetyFactor, drive type: $DriveType)"
Write-Host "  Duplicate Run estimate : $DuplicateRunEstimateText"
Write-Host "  Full Run estimate      : $FullRunEstimateText"
Write-Host ""
