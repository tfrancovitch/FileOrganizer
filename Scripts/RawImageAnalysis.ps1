<#
    RawImageAnalysis.ps1
    Part of: The File Organizer
    Version: 1.1.1

    Orchestrates RawImageAnalysis.py against every camera RAW file (CR2,
    NEF, ARW, DNG, RAF, ORF, RW2, etc.) in the current run's
    the run's inventory CSV. These are the same files ImageHash.py
    intentionally excludes from perceptual hashing.

    Usage:
        .\RawImageAnalysis.ps1
        .\RawImageAnalysis.ps1 -Force
        .\RawImageAnalysis.ps1 -SkipCloudOnly

    Requires:
        pip install exifread
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$SettingsPath,

    [switch]$Force,
    [switch]$SkipCloudOnly
)

. (Join-Path $PSScriptRoot "Common.ps1")

$Context   = Get-RunContext -SettingsPath $SettingsPath
$PythonCmd = Get-PythonCommand
Test-PythonPackages -PythonCmd $PythonCmd -ImportNames @("exifread") -PipInstallHint "exifread"

$ScriptPath = Join-Path $PSScriptRoot "RawImageAnalysis.py"
if (-not (Test-Path -LiteralPath $ScriptPath)) {
    Write-Host "ERROR: RawImageAnalysis.py not found at: $ScriptPath" -ForegroundColor Red
    exit 1
}

$OutputPath = Join-Path $Context.InventoryFolder "RawImageInventory.csv"
$ReportPath = Join-Path $Context.ReportsFolder "RawImageReport.txt"

$PyArgs = @("--csv", $Context.InventoryCsvPath, "--output", $OutputPath, "--report", $ReportPath)
if ($Force)         { $PyArgs += "--force" }
if ($SkipCloudOnly) { $PyArgs += "--skip-cloud-only" }

Write-Host "Running RawImageAnalysis.py..." -ForegroundColor Cyan
Write-Host ""

& $PythonCmd $ScriptPath @PyArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: RawImageAnalysis.py exited with an error (code $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}

Update-RunSettingsTimestamp -Context $Context -FieldName "LastRawImageAnalysisScan"

Write-Host ""
Write-Host "RAW image analysis complete." -ForegroundColor Green
Write-Host "  Output : $OutputPath"
Write-Host ""

# Report is saved but not auto-opened -- see Dashboard.py for how reports are accessed.
