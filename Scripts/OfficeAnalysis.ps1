<#
    OfficeAnalysis.ps1
    Part of: The File Organizer
    Version: 1.1.1

    Orchestrates OfficeAnalysis.py against every .docx/.xlsx/.pptx file in
    the current run's inventory CSV. Legacy .doc/.xls/.ppt files are
    detected and counted but not analyzed (see OfficeAnalysis.py header).

    Usage:
        .\OfficeAnalysis.ps1
        .\OfficeAnalysis.ps1 -Force
        .\OfficeAnalysis.ps1 -SkipCloudOnly

    Requires:
        pip install python-docx openpyxl python-pptx
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
Test-PythonPackages -PythonCmd $PythonCmd -ImportNames @("docx", "openpyxl", "pptx") -PipInstallHint "python-docx openpyxl python-pptx"

$ScriptPath = Join-Path $PSScriptRoot "OfficeAnalysis.py"
if (-not (Test-Path -LiteralPath $ScriptPath)) {
    Write-Host "ERROR: OfficeAnalysis.py not found at: $ScriptPath" -ForegroundColor Red
    exit 1
}

$OutputPath = Join-Path $Context.InventoryFolder "OfficeInventory.csv"
$ReportPath = Join-Path $Context.ReportsFolder "OfficeReport.txt"

$PyArgs = @("--csv", $Context.InventoryCsvPath, "--output", $OutputPath, "--report", $ReportPath)
if ($Force)         { $PyArgs += "--force" }
if ($SkipCloudOnly) { $PyArgs += "--skip-cloud-only" }

Write-Host "Running OfficeAnalysis.py..." -ForegroundColor Cyan
Write-Host ""

& $PythonCmd $ScriptPath @PyArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: OfficeAnalysis.py exited with an error (code $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}

Update-RunSettingsTimestamp -Context $Context -FieldName "LastOfficeAnalysisScan"

Write-Host ""
Write-Host "Office file analysis complete." -ForegroundColor Green
Write-Host "  Output : $OutputPath"
Write-Host ""

# Report is saved but not auto-opened -- see Dashboard.py for how reports are accessed.
