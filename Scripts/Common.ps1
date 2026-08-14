<#
    Common.ps1
    Part of: The File Organizer
    Version: 1.4.0

    Shared helper functions, dot-sourced by multiple scripts across the
    project -- the *Analysis.ps1 wrapper scripts (PDFAnalysis.ps1,
    OfficeAnalysis.ps1, etc.), and now also PartialHash.ps1/FullHash.ps1
    for the long-path helper below. Centralizes settings.json loading,
    run-folder resolution, Python environment checks, and long-path
    handling so each caller stays thin.

    Not meant to be run directly.
#>

# Prevent Python from writing __pycache__ folders into Scripts\. These
# scripts are short-lived, one-shot CLI runs -- bytecode caching's
# compile-time savings are negligible against the real work (hashing,
# extraction), so there's no cost to just not creating the cache.
# Dashboard.py already sets this too; setting it here as well means it's
# still covered if a *Analysis.ps1 wrapper is run directly, without going
# through the dashboard.
$env:PYTHONDONTWRITEBYTECODE = "1"

function ConvertTo-LongPath {
    <#
        Prefixes an absolute Windows path with \\?\ (or \\?\UNC\ for
        network paths) to bypass the 260-character MAX_PATH limit at the
        Win32 API level. No system-level change needed -- this works on
        any machine, which matters since we can't assume control over
        client machines' registry settings.

        Confirmed working via direct testing (see
        TFF_Test1_Findings_and_Takeaways.md, item 1, and the
        Generate-TestDataset.py validation that preceded this fix).

        Only apply this immediately before a file-OPEN call, not for
        display/storage -- paths in CSVs and reports should stay in
        their normal, human-readable form.
    #>
    param([Parameter(Mandatory = $true)][string]$Path)

    if ($Path.StartsWith("\\?\")) {
        return $Path
    }
    if ($Path.StartsWith("\\")) {
        # UNC path: \\server\share\... -> \\?\UNC\server\share\...
        return "\\?\UNC\" + $Path.Substring(2)
    }
    return "\\?\$Path"
}

function Get-RunContext {
    param([Parameter(Mandatory = $true)][string]$SettingsPath)

    if (-not (Test-Path -LiteralPath $SettingsPath)) {
        Write-Host "ERROR: settings.json not found at:" -ForegroundColor Red
        Write-Host "  $SettingsPath" -ForegroundColor Red
        exit 1
    }

    try {
        $Settings = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
    }
    catch {
        Write-Host "ERROR: Could not read/parse settings.json. $_" -ForegroundColor Red
        exit 1
    }

    if ([string]::IsNullOrWhiteSpace($Settings.CurrentRun)) {
        Write-Host "ERROR: No CurrentRun found in settings.json." -ForegroundColor Red
        Write-Host "Run the earlier pipeline scripts first (at least through Pre-Scan)." -ForegroundColor Red
        exit 1
    }

    $ProjectRoot     = Split-Path $SettingsPath -Parent
    $RunFolder       = Join-Path $ProjectRoot "Runs\$($Settings.CurrentRun)"
    $InventoryFolder = Join-Path $RunFolder "Inventory"
    $ReportsFolder   = Join-Path $RunFolder "Reports"

    if (-not (Test-Path -LiteralPath $RunFolder)) {
        Write-Host "ERROR: Run folder not found:" -ForegroundColor Red
        Write-Host "  $RunFolder" -ForegroundColor Red
        exit 1
    }

    New-Item -ItemType Directory -Path $InventoryFolder -Force | Out-Null
    New-Item -ItemType Directory -Path $ReportsFolder   -Force | Out-Null

    # Category scripts need SOME inventory CSV to read from, but they
    # compute their own metadata independently and don't actually require
    # hash/duplicate-status columns to function. Preference order:
    # Duplicate Run's output (the common path), then Full Run's output,
    # then Preliminary Inventory alone (base metadata only, no hash data)
    # as a last resort -- so a project that only did Pre-Scan, or ran
    # Full Run instead of Duplicate Run, doesn't leave every category
    # script broken. This is the fix for the gap flagged when Choose Run
    # Type was designed: choosing Full Run only used to break every
    # category script, since they all hard-required MasterInventory.csv
    # (now DuplicateHashInventory.csv) to exist specifically.
    $DuplicateHashCsvPath = Join-Path $InventoryFolder "DuplicateHashInventory.csv"
    $FullHashCsvPath      = Join-Path $InventoryFolder "FullHashInventory.csv"
    $PreliminaryCsvPath   = Join-Path $InventoryFolder "PreliminaryInventory.csv"

    if (Test-Path -LiteralPath $DuplicateHashCsvPath) {
        $InventoryCsvPath = $DuplicateHashCsvPath
    }
    elseif (Test-Path -LiteralPath $FullHashCsvPath) {
        $InventoryCsvPath = $FullHashCsvPath
    }
    elseif (Test-Path -LiteralPath $PreliminaryCsvPath) {
        $InventoryCsvPath = $PreliminaryCsvPath
    }
    else {
        Write-Host "ERROR: No inventory CSV found -- expected one of:" -ForegroundColor Red
        Write-Host "  $DuplicateHashCsvPath" -ForegroundColor Red
        Write-Host "  $FullHashCsvPath" -ForegroundColor Red
        Write-Host "  $PreliminaryCsvPath" -ForegroundColor Red
        Write-Host "Run at least Pre-Scan (PreliminaryInventory.ps1) first." -ForegroundColor Red
        exit 1
    }

    return [PSCustomObject]@{
        Settings         = $Settings
        SettingsPath     = $SettingsPath
        ProjectRoot      = $ProjectRoot
        RunFolder        = $RunFolder
        InventoryFolder  = $InventoryFolder
        ReportsFolder    = $ReportsFolder
        InventoryCsvPath = $InventoryCsvPath
    }
}

function Get-PythonCommand {
    if (Get-Command python -ErrorAction SilentlyContinue) { return "python" }
    if (Get-Command py -ErrorAction SilentlyContinue) { return "py" }
    Write-Host "ERROR: Python was not found on PATH." -ForegroundColor Red
    exit 1
}

function Test-FFprobeAvailable {
    if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: ffprobe was not found on PATH." -ForegroundColor Red
        Write-Host "ffprobe is part of ffmpeg (not a pip package). Install it via:" -ForegroundColor Red
        Write-Host "  winget install ffmpeg" -ForegroundColor Red
        Write-Host "  (or download from https://ffmpeg.org/download.html and add it to PATH)" -ForegroundColor Red
        exit 1
    }
}

function Test-PythonPackages {
    param(
        [Parameter(Mandatory = $true)][string]$PythonCmd,
        [Parameter(Mandatory = $true)][string[]]$ImportNames,
        [Parameter(Mandatory = $true)][string]$PipInstallHint
    )
    $ImportStatement = ($ImportNames -join ", ")
    & $PythonCmd -c "import $ImportStatement" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Required Python packages are missing." -ForegroundColor Red
        Write-Host "Run: pip install $PipInstallHint" -ForegroundColor Red
        exit 1
    }
}

function Set-RunSettingsValue {
    <#
        Generalizes the read-modify-write pattern Update-RunSettingsTimestamp
        already used for timestamps -- sets any field to any value, not just
        "now". Added for Step 5 (drive type) and Step 6 (pre-scan summary
        counts), which need to record several different kinds of values,
        not just timestamps.
    #>
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$FieldName,
        [Parameter(Mandatory = $true)]$Value
    )
    $Settings = $Context.Settings
    if (-not ($Settings.PSObject.Properties.Name -contains $FieldName)) {
        $Settings | Add-Member -MemberType NoteProperty -Name $FieldName -Value $null
    }
    $Settings.$FieldName = $Value
    $Settings | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Context.SettingsPath -Encoding UTF8
}

function Get-DriveType {
    <#
        Detects whether a target path lives on a local SSD, local HDD, or
        a network location -- a one-time, per-run signal meant as a
        calibration input for future time estimates. Not a precise
        measurement, just a coarse bucket.

        Deliberately defensive: storage detection can fail for many
        reasons (permissions, virtualized/cloud environments, older
        Windows versions) and this must never be allowed to block the
        actual pipeline. Falls back to "Unknown" on any failure, silently
        -- this is a nice-to-have signal, not a requirement.

        NOTE: written and reasoned through carefully, but not yet
        confirmed against a real Windows machine -- worth checking that
        it reports the expected type on the next real run.
    #>
    param([Parameter(Mandatory = $true)][string]$Path)

    try {
        $ResolvedPath = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path

        # UNC path (\\server\share\...) is unambiguously network.
        if ($ResolvedPath -match '^\\\\') {
            return "Network"
        }

        $DriveLetter = $ResolvedPath.Substring(0, 1)

        # A mapped drive letter can still point at a network location --
        # check DriveType via CIM before assuming it's local. DriveType 4
        # = Network, 3 = Local Disk (Win32_LogicalDisk).
        $LogicalDisk = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='${DriveLetter}:'" -ErrorAction Stop
        if ($LogicalDisk.DriveType -eq 4) {
            return "Network"
        }
        if ($LogicalDisk.DriveType -ne 3) {
            return "Unknown"
        }

        # Local disk -- trace drive letter -> partition -> disk -> physical
        # disk media type (SSD/HDD/Unspecified, per Get-PhysicalDisk).
        $Partition = Get-Partition -DriveLetter $DriveLetter -ErrorAction Stop
        $Disk = Get-Disk -Number $Partition.DiskNumber -ErrorAction Stop
        $PhysicalDisk = Get-PhysicalDisk -ErrorAction Stop | Where-Object { $_.DeviceId -eq $Disk.Number }

        switch ($PhysicalDisk.MediaType) {
            "SSD" { return "SSD" }
            "HDD" { return "HDD" }
            default { return "Unknown" }
        }
    }
    catch {
        return "Unknown"
    }
}

function Update-RunSettingsTimestamp {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$FieldName
    )
    Set-RunSettingsValue -Context $Context -FieldName $FieldName -Value (Get-Date).ToString("o")
}
