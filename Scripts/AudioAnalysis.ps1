<#
    AudioAnalysis.ps1
    Part of: The File Organizer
    Version: 1.1.1

    Orchestrates AudioAnalysis.py against every audio file in the current
    run's inventory CSV.

    Usage:
        .\AudioAnalysis.ps1
        .\AudioAnalysis.ps1 -Force
        .\AudioAnalysis.ps1 -SkipCloudOnly

    Requires:
        pip install mutagen
        ffprobe on PATH (part of ffmpeg): winget install ffmpeg
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
Test-PythonPackages -PythonCmd $PythonCmd -ImportNames @("mutagen") -PipInstallHint "mutagen"

$ScriptPath = Join-Path $PSScriptRoot "AudioAnalysis.py"
if (-not (Test-Path -LiteralPath $ScriptPath)) {
    Write-Host "ERROR: AudioAnalysis.py not found at: $ScriptPath" -ForegroundColor Red
    exit 1
}

$OutputPath = Join-Path $Context.InventoryFolder "AudioInventory.csv"
$ReportPath = Join-Path $Context.ReportsFolder "AudioReport.txt"

$PyArgs = @("--csv", $Context.InventoryCsvPath, "--output", $OutputPath, "--report", $ReportPath)
if ($Force)         { $PyArgs += "--force" }
if ($SkipCloudOnly) { $PyArgs += "--skip-cloud-only" }

Write-Host "Running AudioAnalysis.py..." -ForegroundColor Cyan
Write-Host ""

& $PythonCmd $ScriptPath @PyArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: AudioAnalysis.py exited with an error (code $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}

Update-RunSettingsTimestamp -Context $Context -FieldName "LastAudioAnalysisScan"

Write-Host ""
Write-Host "Audio analysis complete." -ForegroundColor Green
Write-Host "  Output : $OutputPath"
Write-Host ""

# Report is saved but not auto-opened -- see Dashboard.py for how reports are accessed.
