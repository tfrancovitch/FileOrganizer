<#
    VideoAnalysis.ps1
    Part of: The File Organizer
    Version: 1.1.1

    Orchestrates VideoAnalysis.py against every video file in the current
    run's inventory CSV.

    Usage:
        .\VideoAnalysis.ps1
        .\VideoAnalysis.ps1 -Force
        .\VideoAnalysis.ps1 -SkipCloudOnly

    Requires:
        ffprobe on PATH (part of ffmpeg): winget install ffmpeg
        (no extra pip packages needed)
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$SettingsPath,

    [switch]$Force,
    [switch]$SkipCloudOnly
)

. (Join-Path $PSScriptRoot "Common.ps1")

$Context   = Get-RunContext -SettingsPath $SettingsPath
Test-FFprobeAvailable
$PythonCmd = Get-PythonCommand

$ScriptPath = Join-Path $PSScriptRoot "VideoAnalysis.py"
if (-not (Test-Path -LiteralPath $ScriptPath)) {
    Write-Host "ERROR: VideoAnalysis.py not found at: $ScriptPath" -ForegroundColor Red
    exit 1
}

$OutputPath = Join-Path $Context.InventoryFolder "VideoInventory.csv"
$ReportPath = Join-Path $Context.ReportsFolder "VideoReport.txt"

$PyArgs = @("--csv", $Context.InventoryCsvPath, "--output", $OutputPath, "--report", $ReportPath)
if ($Force)         { $PyArgs += "--force" }
if ($SkipCloudOnly) { $PyArgs += "--skip-cloud-only" }

Write-Host "Running VideoAnalysis.py..." -ForegroundColor Cyan
Write-Host ""

& $PythonCmd $ScriptPath @PyArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: VideoAnalysis.py exited with an error (code $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}

Update-RunSettingsTimestamp -Context $Context -FieldName "LastVideoAnalysisScan"

Write-Host ""
Write-Host "Video analysis complete." -ForegroundColor Green
Write-Host "  Output : $OutputPath"
Write-Host ""

# Report is saved but not auto-opened -- see Dashboard.py for how reports are accessed.
