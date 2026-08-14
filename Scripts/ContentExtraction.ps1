<#
    ContentExtraction.ps1
    Part of: The File Organizer
    Version: 1.1.1

    Orchestrates ContentExtraction.py against every content-bearing
    document (PDF, Word, PowerPoint, Excel, plain text, Markdown) in the
    current run's inventory CSV. This is the final Phase 1 script --
    every other script captured metadata ABOUT files; this captures the
    actual text content, which Phase 2 (content-based organization,
    near-duplicate matching) will build on.

    Usage:
        .\ContentExtraction.ps1
        .\ContentExtraction.ps1 -Force
        .\ContentExtraction.ps1 -SkipCloudOnly

    Requires:
        pip install pdfplumber python-docx openpyxl python-pptx chardet
        (all of these should already be installed if PDFAnalysis.ps1,
        OfficeAnalysis.ps1, and TextFileAnalysis.ps1 have been run)
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
Test-PythonPackages -PythonCmd $PythonCmd `
    -ImportNames @("pdfplumber", "docx", "openpyxl", "pptx", "chardet") `
    -PipInstallHint "pdfplumber python-docx openpyxl python-pptx chardet"

$ScriptPath = Join-Path $PSScriptRoot "ContentExtraction.py"
if (-not (Test-Path -LiteralPath $ScriptPath)) {
    Write-Host "ERROR: ContentExtraction.py not found at: $ScriptPath" -ForegroundColor Red
    exit 1
}

$OutputPath  = Join-Path $Context.InventoryFolder "ContentIndex.csv"
$ExtractPath = Join-Path $Context.InventoryFolder "ExtractedText"
$ReportPath  = Join-Path $Context.ReportsFolder "ContentExtractionReport.txt"

$PyArgs = @(
    "--csv", $Context.InventoryCsvPath,
    "--output", $OutputPath,
    "--extract-folder", $ExtractPath,
    "--report", $ReportPath
)
if ($Force)         { $PyArgs += "--force" }
if ($SkipCloudOnly) { $PyArgs += "--skip-cloud-only" }

Write-Host "Running ContentExtraction.py..." -ForegroundColor Cyan
Write-Host ""

& $PythonCmd $ScriptPath @PyArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: ContentExtraction.py exited with an error (code $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}

Update-RunSettingsTimestamp -Context $Context -FieldName "LastContentExtractionScan"

Write-Host ""
Write-Host "Content extraction complete." -ForegroundColor Green
Write-Host "  Index          : $OutputPath"
Write-Host "  Extracted text : $ExtractPath"
Write-Host ""

# Report is saved but not auto-opened -- see Dashboard.py for how reports are accessed.
