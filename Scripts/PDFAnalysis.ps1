<#
    PDFAnalysis.ps1
    Part of: The File Organizer
    Version: 1.1.1

    Orchestrates PDFAnalysis.py against every PDF file in the current
    run's inventory CSV.

    Usage:
        .\PDFAnalysis.ps1
        .\PDFAnalysis.ps1 -Force
        .\PDFAnalysis.ps1 -SkipCloudOnly

    Requires:
        pip install pypdf pdfplumber
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
Test-PythonPackages -PythonCmd $PythonCmd -ImportNames @("pypdf", "pdfplumber") -PipInstallHint "pypdf pdfplumber"

$ScriptPath = Join-Path $PSScriptRoot "PDFAnalysis.py"
if (-not (Test-Path -LiteralPath $ScriptPath)) {
    Write-Host "ERROR: PDFAnalysis.py not found at: $ScriptPath" -ForegroundColor Red
    exit 1
}

$OutputPath = Join-Path $Context.InventoryFolder "PDFInventory.csv"
$ReportPath = Join-Path $Context.ReportsFolder "PDFReport.txt"

$PyArgs = @("--csv", $Context.InventoryCsvPath, "--output", $OutputPath, "--report", $ReportPath)
if ($Force)         { $PyArgs += "--force" }
if ($SkipCloudOnly) { $PyArgs += "--skip-cloud-only" }

Write-Host "Running PDFAnalysis.py..." -ForegroundColor Cyan
Write-Host ""

& $PythonCmd $ScriptPath @PyArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: PDFAnalysis.py exited with an error (code $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}

Update-RunSettingsTimestamp -Context $Context -FieldName "LastPDFAnalysisScan"

Write-Host ""
Write-Host "PDF analysis complete." -ForegroundColor Green
Write-Host "  Output : $OutputPath"
Write-Host ""

# Report is saved but not auto-opened -- see Dashboard.py for how reports are accessed.
