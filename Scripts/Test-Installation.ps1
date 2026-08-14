<#
    Test-Installation.ps1
    Part of: The File Organizer
    Version: 3.2.0

    Purpose:
        Verifies that every Phase 1 file is present, non-empty, and
        syntactically valid: the 26 scripts in Scripts\ (this script's
        own folder), plus the two files at the true project root
        (TheFileOrganizer.bat, the double-click launcher, and
        README.txt). Written with zip/unzip distribution in mind: a
        truncated or corrupted unzip can produce a file that "exists"
        but is empty or broken, which a simple Test-Path check alone
        wouldn't catch.

        There is no per-project check here -- projects don't have their
        own copy of the scripts, every script runs from this one master
        Scripts\ folder for every project, so checking this folder IS
        checking what every project will use.

        Files are validated by PARSING (not executing) them:
          - .ps1 files : [System.Management.Automation.Language.Parser]
          - .py/.pyw   : Python's ast.parse()
          - README.txt : existence/non-empty only (nothing to parse)
        Parsing catches truncation/corruption without ever running the
        script's actual logic.

    Usage:
        .\Test-Installation.ps1

    Note:
        This is a one-time/occasional verification script, not a
        pipeline stage -- it lives in The_File_Organizer\Scripts\
        alongside every other script, and is run automatically by
        Dashboard.py on startup (before Install-Dependencies.ps1).
#>

# ----------------------------------------------------------------------------
# 0. Locate Python (needed to syntax-check .py/.pyw files) and the root
# ----------------------------------------------------------------------------
$RootFolder = Split-Path $PSScriptRoot -Parent

$PythonCmd = $null
if (Get-Command python -ErrorAction SilentlyContinue) { $PythonCmd = "python" }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $PythonCmd = "py" }

if (-not $PythonCmd) {
    Write-Host "WARNING: Python not found on PATH -- .py/.pyw files will be checked for" -ForegroundColor Yellow
    Write-Host "presence/size only, not syntax validity." -ForegroundColor Yellow
    Write-Host ""
}

# ----------------------------------------------------------------------------
# 1. File manifest -- every file Phase 1 depends on
# ----------------------------------------------------------------------------
$Manifest = @(
    # -- Root (true project root, one level up from Scripts\) --
    [PSCustomObject]@{ Name = "TheFileOrganizer.bat";     Location = "Root";    Type = "Text" }
    [PSCustomObject]@{ Name = "README.txt";                Location = "Root";    Type = "Text" }

    # -- Scripts\ (this script's own folder) --
    [PSCustomObject]@{ Name = "Common.ps1";               Location = "Scripts"; Type = "PowerShell" }
    [PSCustomObject]@{ Name = "file_organizer_common.py"; Location = "Scripts"; Type = "Python" }
    [PSCustomObject]@{ Name = "New-Project.ps1";          Location = "Scripts"; Type = "PowerShell" }
    [PSCustomObject]@{ Name = "Install-Dependencies.ps1"; Location = "Scripts"; Type = "PowerShell" }
    [PSCustomObject]@{ Name = "Dashboard.py";             Location = "Scripts"; Type = "Python" }
    [PSCustomObject]@{ Name = "PreliminaryInventory.ps1"; Location = "Scripts"; Type = "PowerShell" }
    [PSCustomObject]@{ Name = "PotentialDuplicates.ps1";  Location = "Scripts"; Type = "PowerShell" }
    [PSCustomObject]@{ Name = "PartialHash.ps1";          Location = "Scripts"; Type = "PowerShell" }
    [PSCustomObject]@{ Name = "FullHash.ps1";             Location = "Scripts"; Type = "PowerShell" }
    [PSCustomObject]@{ Name = "FullHashInventory.ps1";    Location = "Scripts"; Type = "PowerShell" }
    [PSCustomObject]@{ Name = "TimeEstimates.ps1";        Location = "Scripts"; Type = "PowerShell" }
    [PSCustomObject]@{ Name = "ImageHash.py";             Location = "Scripts"; Type = "Python" }
    [PSCustomObject]@{ Name = "ImageAnalysis.ps1";        Location = "Scripts"; Type = "PowerShell" }
    [PSCustomObject]@{ Name = "PDFAnalysis.py";           Location = "Scripts"; Type = "Python" }
    [PSCustomObject]@{ Name = "PDFAnalysis.ps1";          Location = "Scripts"; Type = "PowerShell" }
    [PSCustomObject]@{ Name = "OfficeAnalysis.py";        Location = "Scripts"; Type = "Python" }
    [PSCustomObject]@{ Name = "OfficeAnalysis.ps1";       Location = "Scripts"; Type = "PowerShell" }
    [PSCustomObject]@{ Name = "RawImageAnalysis.py";      Location = "Scripts"; Type = "Python" }
    [PSCustomObject]@{ Name = "RawImageAnalysis.ps1";     Location = "Scripts"; Type = "PowerShell" }
    [PSCustomObject]@{ Name = "AudioAnalysis.py";         Location = "Scripts"; Type = "Python" }
    [PSCustomObject]@{ Name = "AudioAnalysis.ps1";        Location = "Scripts"; Type = "PowerShell" }
    [PSCustomObject]@{ Name = "VideoAnalysis.py";         Location = "Scripts"; Type = "Python" }
    [PSCustomObject]@{ Name = "VideoAnalysis.ps1";        Location = "Scripts"; Type = "PowerShell" }
    [PSCustomObject]@{ Name = "TextFileAnalysis.py";      Location = "Scripts"; Type = "Python" }
    [PSCustomObject]@{ Name = "TextFileAnalysis.ps1";     Location = "Scripts"; Type = "PowerShell" }
    [PSCustomObject]@{ Name = "ArchiveAnalysis.py";       Location = "Scripts"; Type = "Python" }
    [PSCustomObject]@{ Name = "ArchiveAnalysis.ps1";      Location = "Scripts"; Type = "PowerShell" }
    [PSCustomObject]@{ Name = "ContentExtraction.py";     Location = "Scripts"; Type = "Python" }
    [PSCustomObject]@{ Name = "ContentExtraction.ps1";    Location = "Scripts"; Type = "PowerShell" }
)

# ----------------------------------------------------------------------------
# 2. Validation helpers
# ----------------------------------------------------------------------------
function Test-PowerShellSyntax {
    param([string]$Path)
    $ParseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$null, [ref]$ParseErrors) | Out-Null
    return ($ParseErrors.Count -eq 0)
}

function Test-PythonSyntax {
    param([string]$Path, [string]$PythonCmd)
    # Parse (not compile/execute) via ast.parse -- catches truncation/corruption
    # without creating __pycache__ clutter the way py_compile would.
    $EscapedPath = $Path -replace "'", "\'"
    & $PythonCmd -c "import ast; ast.parse(open(r'$EscapedPath', encoding='utf-8', errors='replace').read())" 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Test-ManifestFile {
    param([string]$FullPath, [string]$Type, [string]$PythonCmd)

    if (-not (Test-Path -LiteralPath $FullPath)) {
        return "MISSING"
    }

    $Item = Get-Item -LiteralPath $FullPath
    if ($Item.Length -eq 0) {
        return "EMPTY (likely a truncated copy/unzip)"
    }

    if ($Type -eq "PowerShell") {
        if (-not (Test-PowerShellSyntax -Path $FullPath)) {
            return "SYNTAX ERROR (likely corrupted)"
        }
    }
    elseif ($Type -eq "Python") {
        if ($PythonCmd) {
            if (-not (Test-PythonSyntax -Path $FullPath -PythonCmd $PythonCmd)) {
                return "SYNTAX ERROR (likely corrupted)"
            }
        }
        else {
            return "OK (size only -- Python unavailable for syntax check)"
        }
    }
    # Type "Text" (e.g. README.txt) -- nothing to parse, existence/size is enough

    return "OK"
}

# ----------------------------------------------------------------------------
# 3. Check every file (root launcher/README + everything in Scripts\)
# ----------------------------------------------------------------------------
$AllResults = [System.Collections.Generic.List[object]]::new()

Write-Host "Checking installation..." -ForegroundColor Cyan
foreach ($Entry in $Manifest) {
    $BaseFolder = if ($Entry.Location -eq "Root") { $RootFolder } else { $PSScriptRoot }
    $FullPath   = Join-Path $BaseFolder $Entry.Name

    $Status = Test-ManifestFile -FullPath $FullPath -Type $Entry.Type -PythonCmd $PythonCmd

    $Color = if ($Status -eq "OK") { "Green" } elseif ($Status -like "OK*") { "Yellow" } else { "Red" }
    Write-Host ("  {0,-26} {1}" -f $Entry.Name, $Status) -ForegroundColor $Color

    $AllResults.Add([PSCustomObject]@{
        Group    = "Master Template"
        Location = $Entry.Location
        Name     = $Entry.Name
        Status   = $Status
    })
}
Write-Host ""

# ----------------------------------------------------------------------------
# 4. Build the report
# ----------------------------------------------------------------------------
$ReportPath = Join-Path $PSScriptRoot "InstallationCheckReport.txt"
$Lines = [System.Collections.Generic.List[string]]::new()

$Lines.Add("=" * 70)
$Lines.Add(" THE FILE ORGANIZER -- INSTALLATION CHECK REPORT")
$Lines.Add(" Generated : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$Lines.Add(" Root      : $RootFolder")
$Lines.Add("=" * 70)
$Lines.Add("")

foreach ($Group in ($AllResults | Group-Object Group)) {
    $Lines.Add($Group.Name.ToUpper())
    foreach ($r in $Group.Group) {
        $Lines.Add(("  {0,-26} {1}" -f $r.Name, $r.Status))
    }
    $Lines.Add("")
}

$OkCount      = ($AllResults | Where-Object { $_.Status -eq "OK" -or $_.Status -like "OK*" }).Count
$MissingCount = ($AllResults | Where-Object { $_.Status -eq "MISSING" }).Count
$EmptyCount   = ($AllResults | Where-Object { $_.Status -like "EMPTY*" }).Count
$SyntaxCount  = ($AllResults | Where-Object { $_.Status -like "SYNTAX ERROR*" }).Count

$Lines.Add("SUMMARY")
$Lines.Add("  Files checked         : $($AllResults.Count)")
$Lines.Add("  OK                    : $OkCount")
$Lines.Add("  Missing               : $MissingCount")
$Lines.Add("  Empty (corrupted?)    : $EmptyCount")
$Lines.Add("  Syntax errors         : $SyntaxCount")
$Lines.Add("=" * 70)

$Lines | Set-Content -LiteralPath $ReportPath -Encoding UTF8

# ----------------------------------------------------------------------------
# 5. Finish up
# ----------------------------------------------------------------------------
$TotalProblems = $MissingCount + $EmptyCount + $SyntaxCount

Write-Host ""
if ($TotalProblems -eq 0) {
    Write-Host "Installation check complete -- everything OK." -ForegroundColor Green
}
else {
    Write-Host "Installation check complete -- $TotalProblems problem(s) found." -ForegroundColor Red
}
Write-Host "  Files checked : $($AllResults.Count)"
Write-Host "  Missing       : $MissingCount"
Write-Host "  Empty         : $EmptyCount"
Write-Host "  Syntax errors : $SyntaxCount"
Write-Host "  Report        : $ReportPath"
Write-Host ""

# Report is saved but not auto-opened -- see Dashboard.py for how reports are accessed.

if ($TotalProblems -gt 0) { exit 1 } else { exit 0 }
