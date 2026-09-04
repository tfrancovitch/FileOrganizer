<#
    Test-Installation.ps1
    Part of: The File Organizer
    Version: 4.0.0 (B6.1)

    Verifies the shipped SOURCE_SHA256.csv manifest rather than maintaining
    a second hand-written file roster. For every manifested payload file it
    verifies presence, byte length, SHA-256, and syntax where applicable.

    SOURCE_SHA256.csv intentionally does not contain itself: changing the
    manifest would otherwise change its own hash recursively. The manifest's
    presence and non-empty structure are checked separately.
#>

$RootFolder = Split-Path $PSScriptRoot -Parent
$ManifestPath = Join-Path $RootFolder "SOURCE_SHA256.csv"
$ReportPath = Join-Path $PSScriptRoot "InstallationCheckReport.txt"

$PythonCmd = $null
if (Get-Command python -ErrorAction SilentlyContinue) { $PythonCmd = "python" }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $PythonCmd = "py" }

function Test-PowerShellSyntax {
    param([string]$Path)
    $ParseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $Path, [ref]$null, [ref]$ParseErrors) | Out-Null
    return ($ParseErrors.Count -eq 0)
}

function Test-PythonSyntax {
    param([string]$Path, [string]$PythonCmd)
    if (-not $PythonCmd) { return $null }
    $Escaped = $Path -replace "'", "\\'"
    & $PythonCmd -c "import ast; ast.parse(open(r'$Escaped', encoding='utf-8-sig').read())" 2>$null
    return ($LASTEXITCODE -eq 0)
}

$Results = [System.Collections.Generic.List[object]]::new()
$Fatal = 0

Write-Host "Checking installation manifest..." -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $ManifestPath)) {
    Write-Host "  SOURCE_SHA256.csv  MISSING" -ForegroundColor Red
    exit 1
}

try {
    $Manifest = @(Import-Csv -LiteralPath $ManifestPath)
} catch {
    Write-Host "  SOURCE_SHA256.csv  INVALID: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

if ($Manifest.Count -eq 0) {
    Write-Host "  SOURCE_SHA256.csv  EMPTY" -ForegroundColor Red
    exit 1
}

foreach ($Entry in $Manifest) {
    $Rel = [string]$Entry.RelativePath
    $ExpectedHash = ([string]$Entry.SHA256).ToUpperInvariant()
    $ExpectedBytes = 0L
    $ByteOk = [Int64]::TryParse([string]$Entry.Bytes, [ref]$ExpectedBytes)
    $FullPath = Join-Path $RootFolder ($Rel -replace '[\\/]', [IO.Path]::DirectorySeparatorChar)
    $Status = "OK"

    if (-not (Test-Path -LiteralPath $FullPath -PathType Leaf)) {
        $Status = "MISSING"
    } elseif (-not $ByteOk) {
        $Status = "MANIFEST BYTE COUNT INVALID"
    } else {
        $Item = Get-Item -LiteralPath $FullPath
        if ($Item.Length -ne $ExpectedBytes) {
            $Status = "SIZE MISMATCH (expected $ExpectedBytes, got $($Item.Length))"
        } else {
            try {
                $ActualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $FullPath).Hash.ToUpperInvariant()
                if ($ActualHash -ne $ExpectedHash) {
                    $Status = "SHA256 MISMATCH"
                }
            } catch {
                $Status = "HASH ERROR: $($_.Exception.Message)"
            }
        }
    }

    if ($Status -eq "OK") {
        $Ext = [IO.Path]::GetExtension($FullPath).ToLowerInvariant()
        if ($Ext -eq ".ps1") {
            if (-not (Test-PowerShellSyntax -Path $FullPath)) { $Status = "SYNTAX ERROR" }
        } elseif ($Ext -eq ".py" -or $Ext -eq ".pyw") {
            $Parsed = Test-PythonSyntax -Path $FullPath -PythonCmd $PythonCmd
            if ($Parsed -eq $false) { $Status = "SYNTAX ERROR" }
            elseif ($null -eq $Parsed) { $Status = "OK (hash/size; Python unavailable for parse)" }
        }
    }

    if ($Status -notlike "OK*") { $Fatal += 1 }
    $Color = if ($Status -like "OK*") { "Green" } else { "Red" }
    Write-Host ("  {0,-48} {1}" -f $Rel, $Status) -ForegroundColor $Color
    $Results.Add([PSCustomObject]@{ RelativePath=$Rel; Status=$Status })
}

$Lines = [System.Collections.Generic.List[string]]::new()
$Lines.Add("=" * 78)
$Lines.Add(" THE FILE ORGANIZER -- INSTALLATION CHECK REPORT")
$Lines.Add(" Generated : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$Lines.Add(" Root      : $RootFolder")
$Lines.Add(" Manifest  : SOURCE_SHA256.csv ($($Manifest.Count) payload files)")
$Lines.Add("=" * 78)
foreach ($r in $Results) { $Lines.Add(("  {0,-48} {1}" -f $r.RelativePath, $r.Status)) }
$Lines.Add("")
$Lines.Add("SUMMARY")
$Lines.Add("  Manifested payload files : $($Manifest.Count)")
$Lines.Add("  Problems                 : $Fatal")
$Lines.Add("=" * 78)
$Lines | Set-Content -LiteralPath $ReportPath -Encoding UTF8

Write-Host ""
if ($Fatal -eq 0) {
    Write-Host "Installation check complete -- manifest verified." -ForegroundColor Green
    exit 0
}
Write-Host "Installation check complete -- $Fatal problem(s) found." -ForegroundColor Red
exit 1
