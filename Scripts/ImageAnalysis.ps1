<#
    ImageAnalysis.ps1
    Part of: The File Organizer
    Version: 1.1.1

    Purpose:
        Orchestrates ImageHash.py against EVERY non-RAW image file in the
        current run's inventory CSV -- not just files flagged as
        potential duplicates. The run's inventory CSV already contains every
        file regardless of duplicate status, so pointing at it (rather
        than a candidates-only CSV) is what gives full image coverage.

        This script itself does no hashing -- it locates the right files,
        checks that Python and its dependencies are available, invokes
        ImageHash.py with the correct paths for this run, and opens the
        resulting report.

    Usage:
        Run from inside a PROJECT's Scripts folder, with no arguments:
            .\ImageAnalysis.ps1

        Optional flags (passed through to ImageHash.py):
            .\ImageAnalysis.ps1 -Force            # hash cloud-only images, no prompt
            .\ImageAnalysis.ps1 -SkipCloudOnly    # always skip cloud-only images, no prompt
            .\ImageAnalysis.ps1 -HashSize 16      # larger hash size (default 8)

    Requires:
        - FullHash.ps1 must have already been run for this project's
          current run, so the run's inventory CSV exists.
        - Python 3, with: pip install Pillow imagehash
          (optional, for HEIC/HEIF support: pip install pillow-heif)

    Note:
        This script does NOT create a new Run folder. It writes into the
        SAME run folder as the earlier scripts in this scan session.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$SettingsPath,

    [switch]$Force,
    [switch]$SkipCloudOnly,
    [int]$HashSize = 8
)

# ----------------------------------------------------------------------------
# 0-1. Load settings.json and resolve this run's folders/input CSV via the
#      shared Get-RunContext (previously duplicated inline here, before
#      Common.ps1 existed -- refactored so the DuplicateHashInventory.csv
#      rename and PreliminaryInventory.csv fallback only need to live in
#      one place, not two).
# ----------------------------------------------------------------------------
. (Join-Path $PSScriptRoot "Common.ps1")

$Context         = Get-RunContext -SettingsPath $SettingsPath
$Settings        = $Context.Settings
$InventoryFolder = $Context.InventoryFolder
$ReportsFolder   = $Context.ReportsFolder

$MasterInventoryCsvPath = $Context.InventoryCsvPath

$ImageHashPyPath = Join-Path $PSScriptRoot "ImageHash.py"

if (-not (Test-Path -LiteralPath $ImageHashPyPath)) {
    Write-Host "ERROR: ImageHash.py not found at:" -ForegroundColor Red
    Write-Host "  $ImageHashPyPath" -ForegroundColor Red
    Write-Host "Make sure ImageHash.py is in the same Scripts folder as this script." -ForegroundColor Red
    exit 1
}

# ----------------------------------------------------------------------------
# 2. Locate Python and verify dependencies
# ----------------------------------------------------------------------------
$PythonCmd = $null
if (Get-Command python -ErrorAction SilentlyContinue) { $PythonCmd = "python" }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $PythonCmd = "py" }

if (-not $PythonCmd) {
    Write-Host "ERROR: Python was not found on PATH." -ForegroundColor Red
    Write-Host "Install Python, then: pip install Pillow imagehash" -ForegroundColor Red
    exit 1
}

& $PythonCmd -c "import PIL, imagehash" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Required Python packages are missing." -ForegroundColor Red
    Write-Host "Run: pip install Pillow imagehash" -ForegroundColor Red
    exit 1
}

# ----------------------------------------------------------------------------
# 3. Invoke ImageHash.py against the run's inventory CSV (every non-RAW image)
# ----------------------------------------------------------------------------
$ImageHashesCsvPath   = Join-Path $InventoryFolder "ImageHashes.csv"
$ImageHashReportPath  = Join-Path $ReportsFolder "ImageHashReport.txt"

$PythonArgs = @(
    "--csv", $MasterInventoryCsvPath,
    "--output", $ImageHashesCsvPath,
    "--report", $ImageHashReportPath,
    "--hash-size", $HashSize
)
if ($Force)         { $PythonArgs += "--force" }
if ($SkipCloudOnly) { $PythonArgs += "--skip-cloud-only" }

Write-Host "Running ImageHash.py on every non-RAW image in the run's inventory CSV..." -ForegroundColor Cyan
Write-Host ""

& $PythonCmd $ImageHashPyPath @PythonArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: ImageHash.py exited with an error (code $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}

# ----------------------------------------------------------------------------
# 4. Update settings.json
# ----------------------------------------------------------------------------
if (-not ($Settings.PSObject.Properties.Name -contains "LastImageHashScan")) {
    $Settings | Add-Member -MemberType NoteProperty -Name "LastImageHashScan" -Value $null
}
$Settings.LastImageHashScan = (Get-Date).ToString("o")

$Settings | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $SettingsPath -Encoding UTF8

# ----------------------------------------------------------------------------
# 5. Finish up
# ----------------------------------------------------------------------------
Write-Host ""
Write-Host "Image analysis complete." -ForegroundColor Green
Write-Host "  Output : $ImageHashesCsvPath"
Write-Host ""

    # Report is saved but not auto-opened -- see Dashboard.py for how reports are accessed.
