<#
    TextFileAnalysis.ps1
    Part of: The File Organizer
    Version: 1.1.1

    Orchestrates TextFileAnalysis.py against every .txt/.md file in the
    current run's inventory CSV.

    Usage:
        .\TextFileAnalysis.ps1
        .\TextFileAnalysis.ps1 -Force
        .\TextFileAnalysis.ps1 -SkipCloudOnly

    Requires:
        pip install chardet
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
Test-PythonPackages -PythonCmd $PythonCmd -ImportNames @("chardet") -PipInstallHint "chardet"

$ScriptPath = Join-Path $PSScriptRoot "TextFileAnalysis.py"
if (-not (Test-Path -LiteralPath $ScriptPath)) {
    Write-Host "ERROR: TextFileAnalysis.py not found at: $ScriptPath" -ForegroundColor Red
    exit 1
}

$OutputPath = Join-Path $Context.InventoryFolder "TextFileInventory.csv"
$ReportPath = Join-Path $Context.ReportsFolder "TextFileReport.txt"

$PyArgs = @("--csv", $Context.InventoryCsvPath, "--output", $OutputPath, "--report", $ReportPath)
if ($Force)         { $PyArgs += "--force" }
if ($SkipCloudOnly) { $PyArgs += "--skip-cloud-only" }

Write-Host "Running TextFileAnalysis.py..." -ForegroundColor Cyan
Write-Host ""

& $PythonCmd $ScriptPath @PyArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: TextFileAnalysis.py exited with an error (code $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}

Update-RunSettingsTimestamp -Context $Context -FieldName "LastTextFileAnalysisScan"

Write-Host ""
Write-Host "Text file analysis complete." -ForegroundColor Green
Write-Host "  Output : $OutputPath"
Write-Host ""

# Report is saved but not auto-opened -- see Dashboard.py for how reports are accessed.
