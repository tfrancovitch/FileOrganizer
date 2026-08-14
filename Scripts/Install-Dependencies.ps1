<#
    Install-Dependencies.ps1
    Part of: The File Organizer
    Version: 1.4.0

    Purpose:
        Checks which Python packages (and ffmpeg/ffprobe) required by The
        File Organizer's scripts are already installed, installs only
        what's missing, and writes a report showing what was already
        present vs. what got newly installed vs. anything that failed.

        Also unblocks every .ps1 file in Scripts\ (its own folder) plus
        the root .bat launcher, removing Windows' "downloaded from the
        internet" flag so manual/CLI use of any script never triggers the
        security warning prompt. (The Dashboard itself never needed this
        -- it already runs every script with -ExecutionPolicy Bypass,
        which suppresses that prompt for its own subprocess calls
        regardless. This step is purely for convenience when running
        scripts directly by hand.)

        Each package is verified by actually attempting to import it in
        Python -- not just by trusting pip's exit code -- both before AND
        after installing, so "confirmed installed" means it genuinely
        works, not just that pip finished without error.

    Usage:
        .\Install-Dependencies.ps1
        .\Install-Dependencies.ps1 -CheckOnly   # report status only, install/unblock nothing

    Note:
        This is a one-time machine-setup script, not a pipeline stage --
        it lives in The_File_Organizer\Scripts\ alongside every other
        script. It can't unblock ITSELF before its very first run,
        though -- that one time, you'll still need to answer the
        security prompt (or use -ExecutionPolicy Bypass) yourself. Every
        run after that, everything else stays unblocked.
#>

param(
    [switch]$CheckOnly
)

# ----------------------------------------------------------------------------
# 0a. Unblock every script this toolkit ships -- Scripts\ (this script's own
#     folder) plus the root .bat launcher one level up. A one-time fix for
#     the "security warning" prompt on manual/CLI use. Idempotent:
#     re-running this on already-unblocked files is a harmless no-op.
# ----------------------------------------------------------------------------
if (-not $CheckOnly) {
    $RootFolder = Split-Path $PSScriptRoot -Parent

    $UnblockedCount = 0

    $ScriptFiles = Get-ChildItem -LiteralPath $PSScriptRoot -Filter "*.ps1" -File
    foreach ($File in $ScriptFiles) {
        Unblock-File -LiteralPath $File.FullName
        $UnblockedCount++
    }

    $LauncherFiles = Get-ChildItem -LiteralPath $RootFolder -Filter "*.bat" -File -ErrorAction SilentlyContinue
    foreach ($File in $LauncherFiles) {
        Unblock-File -LiteralPath $File.FullName
        $UnblockedCount++
    }

    Write-Host "Unblocked $UnblockedCount file(s) (Scripts\ + the root launcher)." -ForegroundColor Cyan
    Write-Host ""
}

# ----------------------------------------------------------------------------
# 0b. Locate Python
# ----------------------------------------------------------------------------
$PythonCmd = $null
if (Get-Command python -ErrorAction SilentlyContinue) { $PythonCmd = "python" }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $PythonCmd = "py" }

if (-not $PythonCmd) {
    Write-Host "ERROR: Python was not found on PATH. Install Python first." -ForegroundColor Red
    exit 1
}

$PythonVersion = (& $PythonCmd --version) 2>&1
Write-Host "Using: $PythonCmd ($PythonVersion)" -ForegroundColor Cyan
if ($CheckOnly) {
    Write-Host "Mode: check only -- nothing will be installed or unblocked." -ForegroundColor Cyan
}
Write-Host ""

# ----------------------------------------------------------------------------
# 1. Package list -- every dependency across all Phase 1 scripts
# ----------------------------------------------------------------------------
$Packages = @(
    [PSCustomObject]@{ PipName = "Pillow";      ImportName = "PIL";          Optional = $false; UsedBy = "ImageHash.py" }
    [PSCustomObject]@{ PipName = "imagehash";   ImportName = "imagehash";    Optional = $false; UsedBy = "ImageHash.py" }
    [PSCustomObject]@{ PipName = "pypdf";       ImportName = "pypdf";        Optional = $false; UsedBy = "PDFAnalysis.py" }
    [PSCustomObject]@{ PipName = "pdfplumber";  ImportName = "pdfplumber";   Optional = $false; UsedBy = "PDFAnalysis.py, ContentExtraction.py" }
    [PSCustomObject]@{ PipName = "python-docx"; ImportName = "docx";         Optional = $false; UsedBy = "OfficeAnalysis.py, ContentExtraction.py" }
    [PSCustomObject]@{ PipName = "openpyxl";    ImportName = "openpyxl";     Optional = $false; UsedBy = "OfficeAnalysis.py, ContentExtraction.py" }
    [PSCustomObject]@{ PipName = "python-pptx"; ImportName = "pptx";         Optional = $false; UsedBy = "OfficeAnalysis.py, ContentExtraction.py" }
    [PSCustomObject]@{ PipName = "olefile";     ImportName = "olefile";      Optional = $false; UsedBy = "OfficeAnalysis.py (legacy .doc/.xls/.ppt metadata)" }
    [PSCustomObject]@{ PipName = "exifread";    ImportName = "exifread";     Optional = $false; UsedBy = "RawImageAnalysis.py" }
    [PSCustomObject]@{ PipName = "mutagen";     ImportName = "mutagen";      Optional = $false; UsedBy = "AudioAnalysis.py" }
    [PSCustomObject]@{ PipName = "chardet";     ImportName = "chardet";      Optional = $false; UsedBy = "TextFileAnalysis.py, ContentExtraction.py" }
    [PSCustomObject]@{ PipName = "py7zr";       ImportName = "py7zr";        Optional = $true;  UsedBy = "ArchiveAnalysis.py (.7z only)" }
    [PSCustomObject]@{ PipName = "pillow-heif"; ImportName = "pillow_heif";  Optional = $true;  UsedBy = "ImageHash.py (HEIC/HEIF only)" }
)

# ----------------------------------------------------------------------------
# 2. Check each package -- install if missing (unless -CheckOnly)
# ----------------------------------------------------------------------------
$Results = [System.Collections.Generic.List[object]]::new()

foreach ($Pkg in $Packages) {
    Write-Host -NoNewline "Checking $($Pkg.PipName)... "

    & $PythonCmd -c "import $($Pkg.ImportName)" 2>$null
    $WasInstalled = ($LASTEXITCODE -eq 0)

    $Result = [PSCustomObject]@{
        PipName             = $Pkg.PipName
        ImportName          = $Pkg.ImportName
        Optional            = $Pkg.Optional
        UsedBy              = $Pkg.UsedBy
        WasAlreadyInstalled = $WasInstalled
        InstallAttempted    = $false
        InstallSucceeded    = $false
        InstallOutput       = ""
        FinalStatus         = ""
    }

    if ($WasInstalled) {
        Write-Host "already installed" -ForegroundColor Green
        $Result.FinalStatus = "Already installed"
    }
    elseif ($CheckOnly) {
        Write-Host "missing (check-only mode)" -ForegroundColor Yellow
        $Result.FinalStatus = "Missing (not installed -- CheckOnly mode)"
    }
    else {
        Write-Host "missing, installing..." -ForegroundColor Yellow
        $Result.InstallAttempted = $true

        $InstallOutput = (& $PythonCmd -m pip install $Pkg.PipName 2>&1 | Out-String)

        # Re-check via an actual import rather than trusting pip's exit code
        & $PythonCmd -c "import $($Pkg.ImportName)" 2>$null
        $ImportNowWorks = ($LASTEXITCODE -eq 0)

        if ($ImportNowWorks) {
            Write-Host "  installed successfully" -ForegroundColor Green
            $Result.InstallSucceeded = $true
            $Result.FinalStatus = "Installed successfully"
        }
        else {
            Write-Host "  installation failed" -ForegroundColor Red
            $Result.FinalStatus = "Install failed"
            $Result.InstallOutput = $InstallOutput.Trim()
        }
    }

    $Results.Add($Result)
}

Write-Host ""

# ----------------------------------------------------------------------------
# 3. Check ffprobe (external tool, not a pip package)
# ----------------------------------------------------------------------------
Write-Host -NoNewline "Checking ffprobe (ffmpeg)... "
$FFprobeWasInstalled = [bool](Get-Command ffprobe -ErrorAction SilentlyContinue)
$FFprobeFinalStatus  = ""

if ($FFprobeWasInstalled) {
    Write-Host "already installed" -ForegroundColor Green
    $FFprobeFinalStatus = "Already installed"
}
elseif ($CheckOnly) {
    Write-Host "missing (check-only mode)" -ForegroundColor Yellow
    $FFprobeFinalStatus = "Missing (not installed -- CheckOnly mode). Manual install: winget install ffmpeg"
}
else {
    Write-Host "missing, attempting install via winget..." -ForegroundColor Yellow
    try {
        winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements | Out-Null
    }
    catch {
        # Fall through -- handled by the re-check below regardless of what happened
    }

    $FFprobeNowAvailable = [bool](Get-Command ffprobe -ErrorAction SilentlyContinue)
    if ($FFprobeNowAvailable) {
        Write-Host "  installed successfully" -ForegroundColor Green
        $FFprobeFinalStatus = "Installed successfully"
    }
    else {
        Write-Host "  installed, but not yet visible on PATH this session (or install failed)" -ForegroundColor Yellow
        $FFprobeFinalStatus = "Not confirmed -- if winget succeeded, restart your terminal and re-run this script to confirm. Otherwise, manually run: winget install ffmpeg"
    }
}

Write-Host ""

# ----------------------------------------------------------------------------
# 4. Build the report
# ----------------------------------------------------------------------------
$ReportPath = Join-Path $PSScriptRoot "DependencyCheckReport.txt"
$Lines = [System.Collections.Generic.List[string]]::new()

$Lines.Add("=" * 70)
$Lines.Add(" THE FILE ORGANIZER -- DEPENDENCY CHECK REPORT")
$Lines.Add(" Generated : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$Lines.Add(" Mode      : $(if ($CheckOnly) { 'Check only (nothing installed/unblocked)' } else { 'Check, install, and unblock' })")
$Lines.Add("=" * 70)
$Lines.Add("")
if (-not $CheckOnly) {
    $Lines.Add("SCRIPT UNBLOCKING")
    $Lines.Add("  Scripts unblocked (root + Scripts\) : $UnblockedCount")
    $Lines.Add("")
}
$Lines.Add("PYTHON PACKAGES")
$Lines.Add(("  {0,-14} {1,-10} {2,-38} {3}" -f "Package", "Type", "Used By", "Status"))
$Lines.Add("  " + ("-" * 95))
foreach ($r in $Results) {
    $TypeLabel = if ($r.Optional) { "Optional" } else { "Required" }
    $Lines.Add(("  {0,-14} {1,-10} {2,-38} {3}" -f $r.PipName, $TypeLabel, $r.UsedBy, $r.FinalStatus))
}
$Lines.Add("")
$Lines.Add("EXTERNAL TOOLS")
$Lines.Add(("  {0,-14} {1,-10} {2,-38} {3}" -f "ffprobe", "Required", "AudioAnalysis.py, VideoAnalysis.py", $FFprobeFinalStatus))
$Lines.Add("")

$AlreadyCount   = ($Results | Where-Object { $_.WasAlreadyInstalled }).Count
$InstalledCount = ($Results | Where-Object { $_.InstallSucceeded }).Count
$FailedCount    = ($Results | Where-Object { $_.InstallAttempted -and -not $_.InstallSucceeded }).Count

$Lines.Add("SUMMARY")
$Lines.Add("  Already installed        : $AlreadyCount")
$Lines.Add("  Newly installed          : $InstalledCount")
$Lines.Add("  Failed to install        : $FailedCount")
$Lines.Add("  ffprobe                  : $FFprobeFinalStatus")
$Lines.Add("")

$FailedPackages = $Results | Where-Object { $_.InstallAttempted -and -not $_.InstallSucceeded }
if ($FailedPackages.Count -gt 0) {
    $Lines.Add("INSTALL FAILURE DETAILS")
    foreach ($f in $FailedPackages) {
        $Lines.Add("  $($f.PipName):")
        $IndentedOutput = ($f.InstallOutput -replace "`n", "`n    ")
        $Lines.Add("    $IndentedOutput")
        $Lines.Add("")
    }
}

$Lines.Add("=" * 70)

$Lines | Set-Content -LiteralPath $ReportPath -Encoding UTF8

# ----------------------------------------------------------------------------
# 5. Finish up
# ----------------------------------------------------------------------------
Write-Host "Dependency check complete." -ForegroundColor Green
if (-not $CheckOnly) {
    Write-Host "  Scripts unblocked : $UnblockedCount"
}
Write-Host "  Already installed : $AlreadyCount"
Write-Host "  Newly installed   : $InstalledCount"
Write-Host "  Failed            : $FailedCount"
Write-Host "  Report            : $ReportPath"
Write-Host ""

# Report is saved but not auto-opened -- see Dashboard.py for how reports are accessed.

# ----------------------------------------------------------------------------
# 6. Exit code -- only a REQUIRED package failing to install is treated as
#    a hard failure. Optional packages (py7zr, pillow-heif) degrade
#    gracefully already (handled per-file by the scripts that use them),
#    and ffprobe missing only affects Audio/Video categories specifically
#    -- neither should block the whole dashboard from opening.
# ----------------------------------------------------------------------------
$RequiredFailures = $Results | Where-Object { $_.InstallAttempted -and -not $_.InstallSucceeded -and -not $_.Optional }
if ($RequiredFailures.Count -gt 0) {
    exit 1
}
exit 0
