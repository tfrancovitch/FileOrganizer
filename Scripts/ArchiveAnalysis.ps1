<#
    ArchiveAnalysis.ps1
    Part of: The File Organizer
    Version: 1.1.1

    Orchestrates ArchiveAnalysis.py against every .zip/.7z file in the
    current run's inventory CSV. Produces two CSVs: ArchiveInventory
    (one row per archive) and ArchiveContents (one row per file inside
    each archive).

    Usage:
        .\ArchiveAnalysis.ps1
        .\ArchiveAnalysis.ps1 -Force
        .\ArchiveAnalysis.ps1 -SkipCloudOnly

    Requires:
        .zip works out of the box (standard library).
        .7z needs: pip install py7zr (optional -- if missing, any .7z
        files found are logged as individual errors, everything else
        still processes normally).
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

$ScriptPath = Join-Path $PSScriptRoot "ArchiveAnalysis.py"
if (-not (Test-Path -LiteralPath $ScriptPath)) {
    Write-Host "ERROR: ArchiveAnalysis.py not found at: $ScriptPath" -ForegroundColor Red
    exit 1
}

$OutputPath         = Join-Path $Context.InventoryFolder "ArchiveInventory.csv"
$ContentsOutputPath = Join-Path $Context.InventoryFolder "ArchiveContents.csv"
$ReportPath         = Join-Path $Context.ReportsFolder "ArchiveReport.txt"

$PyArgs = @(
    "--csv", $Context.InventoryCsvPath,
    "--output", $OutputPath,
    "--contents-output", $ContentsOutputPath,
    "--report", $ReportPath
)
if ($Force)         { $PyArgs += "--force" }
if ($SkipCloudOnly) { $PyArgs += "--skip-cloud-only" }

Write-Host "Running ArchiveAnalysis.py..." -ForegroundColor Cyan
Write-Host ""

& $PythonCmd $ScriptPath @PyArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: ArchiveAnalysis.py exited with an error (code $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}

Update-RunSettingsTimestamp -Context $Context -FieldName "LastArchiveAnalysisScan"

Write-Host ""
Write-Host "Archive analysis complete." -ForegroundColor Green
Write-Host "  Archives : $OutputPath"
Write-Host "  Contents : $ContentsOutputPath"
Write-Host ""

# Report is saved but not auto-opened -- see Dashboard.py for how reports are accessed.
