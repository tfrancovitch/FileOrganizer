<#
    PreliminaryInventory.ps1
    Part of: The File Organizer
    Version: 1.1.1

    Purpose:
        Scans the project's TargetPath (read from settings.json), builds a
        PreliminaryInventory.csv of every file found, and writes a
        PreliminaryReport.txt summarizing the results. Creates a new,
        timestamped Run folder under the project's Runs\ folder for this
        scan's output.

    Usage:
        Run from inside a PROJECT's Scripts folder, with no arguments:
            .\PreliminaryInventory.ps1

    Requires:
        - The project must already exist (created via New-Project.ps1),
          so that settings.json and TargetPath are available.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$SettingsPath
)

# ----------------------------------------------------------------------------
# 0. Load settings.json (path supplied by the caller -- no longer inferred
#    from this script's own location, since scripts now run from a single
#    shared location rather than being copied per-project)
# ----------------------------------------------------------------------------
if (-not (Test-Path -LiteralPath $SettingsPath)) {
    Write-Host "ERROR: settings.json not found at:" -ForegroundColor Red
    Write-Host "  $SettingsPath" -ForegroundColor Red
    exit 1
}

$ProjectRoot = Split-Path $SettingsPath -Parent

try {
    $Settings = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
}
catch {
    Write-Host "ERROR: Could not read/parse settings.json. $_" -ForegroundColor Red
    exit 1
}

$TargetPath = $Settings.TargetPath

if ([string]::IsNullOrWhiteSpace($TargetPath) -or -not (Test-Path -LiteralPath $TargetPath -PathType Container)) {
    Write-Host "ERROR: TargetPath in settings.json is missing or no longer exists:" -ForegroundColor Red
    Write-Host "  $TargetPath" -ForegroundColor Red
    exit 1
}

# Common.ps1 provides Get-DriveType (drive-type detection) and
# Set-RunSettingsValue (generalized settings.json writer).
. (Join-Path $PSScriptRoot "Common.ps1")

# One-time, per-run drive type detection (SSD/HDD/Network/Unknown) -- a
# calibration signal for future time estimates, not a requirement. Never
# allowed to block the pipeline; Get-DriveType itself falls back to
# "Unknown" on any failure rather than throwing.
$DriveType = Get-DriveType -Path $TargetPath

# ----------------------------------------------------------------------------
# 1. Create a new timestamped Run folder
# ----------------------------------------------------------------------------
$RunTimestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$RunFolder    = Join-Path $ProjectRoot "Runs\$RunTimestamp"

$InventoryFolder = Join-Path $RunFolder "Inventory"
$ReportsFolder   = Join-Path $RunFolder "Reports"
$LogsFolder      = Join-Path $RunFolder "Logs"

New-Item -ItemType Directory -Path $InventoryFolder -Force | Out-Null
New-Item -ItemType Directory -Path $ReportsFolder   -Force | Out-Null
New-Item -ItemType Directory -Path $LogsFolder      -Force | Out-Null

$ErrorLogPath = Join-Path $LogsFolder "errors.txt"

Write-Host "Scanning: $TargetPath" -ForegroundColor Cyan
Write-Host "Run folder: $RunFolder" -ForegroundColor Cyan
Write-Host ""

# ----------------------------------------------------------------------------
# 2. Prepare tracking variables
# ----------------------------------------------------------------------------
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

$NextDBID = [int]$Settings.NextDBID
if (-not $NextDBID -or $NextDBID -lt 1) { $NextDBID = 1 }

$InventoryRows = [System.Collections.Generic.List[object]]::new()
$ErrorEntries  = [System.Collections.Generic.List[string]]::new()

$FileCount                   = 0
$EmptyFolderCount            = 0
$TrueLinkSkippedCount        = 0
$CloudPlaceholderFolderCount = 0

$TargetPathNormalized = (Resolve-Path -LiteralPath $TargetPath).Path.TrimEnd('\')

# ----------------------------------------------------------------------------
# 3. Helper functions
# ----------------------------------------------------------------------------
# Windows PowerShell 5.1's FileSystem provider can stop enumerating paths at
# the traditional MAX_PATH boundary even when individual files can be opened
# through the \\?\ extended-length namespace.  Preliminary Inventory must
# discover those files before the later hash stages can process them, so the
# directory walk uses the Unicode Win32 FindFirstFileW / FindNextFileW APIs
# directly.  Those APIs accept \\?\ paths regardless of the machine-wide
# LongPathsEnabled setting.
#
# IMPORTANT: the helper returns normal human-readable paths (C:\...), never
# \\?\ paths.  Extended prefixes are used only at the Win32 API boundary.
if (-not ("FileOrganizer.NativeLongPathEnumerator" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using FILETIME = System.Runtime.InteropServices.ComTypes.FILETIME;

namespace FileOrganizer
{
    public sealed class NativeFindEntry
    {
        public string Name { get; set; }
        public string FullName { get; set; }
        public bool IsDirectory { get; set; }
        public FileAttributes Attributes { get; set; }
        public long Length { get; set; }
        public DateTime CreationTime { get; set; }
        public DateTime LastWriteTime { get; set; }
        public DateTime LastAccessTime { get; set; }
        public uint ReparseTag { get; set; }

        public bool IsTrueLink
        {
            get
            {
                // IO_REPARSE_TAG_MOUNT_POINT covers junctions; the symbolic
                // link tag covers file/directory symlinks.  Cloud placeholder
                // tags are intentionally NOT treated as links, so they remain
                // eligible for recursion just as in the previous implementation.
                return ReparseTag == 0xA0000003u || ReparseTag == 0xA000000Cu;
            }
        }
    }

    public static class NativeLongPathEnumerator
    {
        private static readonly IntPtr INVALID_HANDLE_VALUE = new IntPtr(-1);
        private const int ERROR_FILE_NOT_FOUND = 2;
        private const int ERROR_PATH_NOT_FOUND = 3;
        private const int ERROR_NO_MORE_FILES = 18;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct WIN32_FIND_DATA
        {
            public FileAttributes dwFileAttributes;
            public FILETIME ftCreationTime;
            public FILETIME ftLastAccessTime;
            public FILETIME ftLastWriteTime;
            public uint nFileSizeHigh;
            public uint nFileSizeLow;
            public uint dwReserved0;
            public uint dwReserved1;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)]
            public string cFileName;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 14)]
            public string cAlternateFileName;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr FindFirstFileW(string lpFileName, out WIN32_FIND_DATA lpFindFileData);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool FindNextFileW(IntPtr hFindFile, out WIN32_FIND_DATA lpFindFileData);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool FindClose(IntPtr hFindFile);

        private static string ToExtendedPath(string path)
        {
            if (path.StartsWith(@"\\?\"))
                return path;
            if (path.StartsWith(@"\\"))
                return @"\\?\UNC\" + path.Substring(2);
            return @"\\?\" + path;
        }

        private static DateTime FileTimeToLocal(FILETIME value)
        {
            long raw = ((long)value.dwHighDateTime << 32) | (uint)value.dwLowDateTime;
            if (raw <= 0)
                return DateTime.MinValue;
            try
            {
                return DateTime.FromFileTimeUtc(raw).ToLocalTime();
            }
            catch (ArgumentOutOfRangeException)
            {
                return DateTime.MinValue;
            }
        }

        private static NativeFindEntry ConvertEntry(string directory, WIN32_FIND_DATA data)
        {
            bool isDirectory = (data.dwFileAttributes & FileAttributes.Directory) == FileAttributes.Directory;
            bool isReparse = (data.dwFileAttributes & FileAttributes.ReparsePoint) == FileAttributes.ReparsePoint;
            long length = isDirectory ? 0L : (((long)data.nFileSizeHigh << 32) | data.nFileSizeLow);
            string fullName = directory.TrimEnd('\\') + "\\" + data.cFileName;

            return new NativeFindEntry
            {
                Name = data.cFileName,
                FullName = fullName,
                IsDirectory = isDirectory,
                Attributes = data.dwFileAttributes,
                Length = length,
                CreationTime = FileTimeToLocal(data.ftCreationTime),
                LastWriteTime = FileTimeToLocal(data.ftLastWriteTime),
                LastAccessTime = FileTimeToLocal(data.ftLastAccessTime),
                ReparseTag = isReparse ? data.dwReserved0 : 0u
            };
        }

        public static List<NativeFindEntry> Enumerate(string directory)
        {
            List<NativeFindEntry> results = new List<NativeFindEntry>();
            string searchPath = ToExtendedPath(directory.TrimEnd('\\')) + "\\*";
            WIN32_FIND_DATA data;
            IntPtr handle = FindFirstFileW(searchPath, out data);

            if (handle == INVALID_HANDLE_VALUE)
            {
                int error = Marshal.GetLastWin32Error();
                // FindFirstFileW reports FILE_NOT_FOUND for an empty directory.
                if (error == ERROR_FILE_NOT_FOUND)
                    return results;
                if (error == ERROR_PATH_NOT_FOUND)
                    throw new DirectoryNotFoundException("Directory not found: " + directory);
                throw new Win32Exception(error, "Could not enumerate directory: " + directory);
            }

            try
            {
                while (true)
                {
                    if (data.cFileName != "." && data.cFileName != "..")
                        results.Add(ConvertEntry(directory, data));

                    if (!FindNextFileW(handle, out data))
                    {
                        int error = Marshal.GetLastWin32Error();
                        if (error == ERROR_NO_MORE_FILES)
                            break;
                        throw new Win32Exception(error, "Could not continue enumerating directory: " + directory);
                    }
                }
            }
            finally
            {
                FindClose(handle);
            }

            return results;
        }
    }
}
"@
}
function Test-HasAttribute {
    param($Attributes, [System.IO.FileAttributes]$Flag)
    return (($Attributes -band $Flag) -eq $Flag)
}

function Get-RelativeDepth {
    param([string]$FullPath, [string]$RootPath)
    $relative       = $FullPath.Substring($RootPath.Length).TrimStart('\')
    $parentRelative = Split-Path $relative -Parent
    if ([string]::IsNullOrEmpty($parentRelative)) { return 0 }
    return ($parentRelative -split '\\').Count
}

function Get-TopLevelFolder {
    param([string]$FullPath, [string]$RootPath)
    $relative = $FullPath.Substring($RootPath.Length).TrimStart('\')
    $parts    = $relative -split '\\'
    if ($parts.Count -le 1) { return "(root)" }
    return $parts[0]
}

function Format-Bytes {
    param([double]$Bytes)
    $units = "B", "KB", "MB", "GB", "TB"
    $index = 0
    while ($Bytes -ge 1024 -and $index -lt $units.Count - 1) {
        $Bytes /= 1024
        $index++
    }
    return "{0:N2} {1}" -f $Bytes, $units[$index]
}

# ----------------------------------------------------------------------------
# 4. Walk the folder tree manually (stack-based), skipping recursion into
#    reparse points (symlinks/junctions) to avoid infinite loops
# ----------------------------------------------------------------------------
$Stack = [System.Collections.Generic.Stack[string]]::new()
$Stack.Push($TargetPathNormalized)

$LastProgressUpdate = Get-Date

while ($Stack.Count -gt 0) {
    $CurrentDir = $Stack.Pop()

    try {
        # Do not use Get-ChildItem here. Under Windows PowerShell 5.1 it can
        # fail to enumerate children once the directory path crosses MAX_PATH.
        # The native helper uses FindFirstFileW/FindNextFileW with a \\?\ path
        # internally while returning ordinary paths for CSV/report output.
        $Items = [FileOrganizer.NativeLongPathEnumerator]::Enumerate($CurrentDir)
    }
    catch {
        $ErrorEntries.Add("DIRECTORY ACCESS ERROR: $CurrentDir -- $($_.Exception.Message)")
        continue
    }

    if ($Items.Count -eq 0) {
        $EmptyFolderCount++
        continue
    }

    foreach ($Item in $Items) {
        try {
            $IsReparse = Test-HasAttribute -Attributes $Item.Attributes -Flag ([System.IO.FileAttributes]::ReparsePoint)

            if ($Item.IsDirectory) {
                # The native enumerator exposes the underlying reparse tag.
                # Only genuine symlinks/junctions are excluded from recursion;
                # cloud placeholder folders remain recursable, preserving the
                # previous OneDrive-aware behavior.
                if ($Item.IsTrueLink) {
                    $TrueLinkSkippedCount++
                }
                else {
                    if ($IsReparse) {
                        $CloudPlaceholderFolderCount++
                    }
                    $Stack.Push($Item.FullName)
                }
                continue
            }

            # --- It's a file: build its inventory row ---
            $FileCount++

            $IsOffline = Test-HasAttribute -Attributes $Item.Attributes -Flag ([System.IO.FileAttributes]::Offline)
            $Depth     = Get-RelativeDepth -FullPath $Item.FullName -RootPath $TargetPathNormalized

            $Row = [PSCustomObject]@{
                DB_ID            = $NextDBID
                FileName         = $Item.Name
                Extension        = [System.IO.Path]::GetExtension($Item.Name)
                Directory        = $Item.FullName.Substring(0, $Item.FullName.LastIndexOf('\'))
                Path             = $Item.FullName
                Length           = $Item.Length
                CreationTime     = $Item.CreationTime
                LastWriteTime    = $Item.LastWriteTime
                LastAccessTime   = $Item.LastAccessTime
                Attributes       = $Item.Attributes.ToString()
                IsReparsePoint   = $IsReparse
                IsOfflineOrCloud = $IsOffline
                Depth            = $Depth
                PathLength       = $Item.FullName.Length
            }

            $InventoryRows.Add($Row)
            $NextDBID++

            # Time-based progress update (not per-file, to avoid slowing the scan)
            if (((Get-Date) - $LastProgressUpdate).TotalMilliseconds -ge 200) {
                Write-Progress -Activity "Scanning $TargetPathNormalized" `
                    -Status "$FileCount files found so far... ($([math]::Round($Stopwatch.Elapsed.TotalSeconds,1))s elapsed)"
                $LastProgressUpdate = Get-Date
            }
        }
        catch {
            $ErrorEntries.Add("FILE ERROR: $($Item.FullName) -- $($_.Exception.Message)")
        }
    }
}

Write-Progress -Activity "Scanning $TargetPathNormalized" -Completed
$Stopwatch.Stop()

# ----------------------------------------------------------------------------
# 5. Write error log (if any)
# ----------------------------------------------------------------------------
if ($ErrorEntries.Count -gt 0) {
    $ErrorEntries | Set-Content -LiteralPath $ErrorLogPath -Encoding UTF8
}

# ----------------------------------------------------------------------------
# 6. Export the CSV
# ----------------------------------------------------------------------------
$InventoryCsvPath = Join-Path $InventoryFolder "PreliminaryInventory.csv"
$InventoryRows | Export-Csv -LiteralPath $InventoryCsvPath -NoTypeInformation -Encoding UTF8

# ----------------------------------------------------------------------------
# 7. Compute report statistics
# ----------------------------------------------------------------------------
$TotalBytes = ($InventoryRows | Measure-Object -Property Length -Sum).Sum
if (-not $TotalBytes) { $TotalBytes = 0 }

$ByExtension = $InventoryRows | Group-Object Extension | ForEach-Object {
    [PSCustomObject]@{
        Extension = if ([string]::IsNullOrEmpty($_.Name)) { "(none)" } else { $_.Name }
        Count     = $_.Count
        TotalSize = ($_.Group | Measure-Object -Property Length -Sum).Sum
    }
} | Sort-Object TotalSize -Descending

$DepthGroups = $InventoryRows | Group-Object Depth | Sort-Object { [int]$_.Name }
$MaxDepth    = if ($InventoryRows.Count -gt 0) { ($InventoryRows | Measure-Object -Property Depth -Maximum).Maximum } else { 0 }

$EmptyFileCount = ($InventoryRows | Where-Object { $_.Length -eq 0 }).Count

$LargestFiles = $InventoryRows | Sort-Object Length -Descending | Select-Object -First 20

$TopLevelSizes = $InventoryRows |
    Group-Object { Get-TopLevelFolder -FullPath $_.Path -RootPath $TargetPathNormalized } |
    ForEach-Object {
        [PSCustomObject]@{
            Folder    = $_.Name
            TotalSize = ($_.Group | Measure-Object -Property Length -Sum).Sum
            FileCount = $_.Count
        }
    } | Sort-Object TotalSize -Descending | Select-Object -First 20

$DuplicateNameGroups    = $InventoryRows | Group-Object FileName | Where-Object { $_.Count -gt 1 }
$DuplicateNameFileCount = ($DuplicateNameGroups | Measure-Object -Property Count -Sum).Sum
if (-not $DuplicateNameFileCount) { $DuplicateNameFileCount = 0 }

$LongPathCount = ($InventoryRows | Where-Object { $_.PathLength -gt 260 }).Count

$HiddenCount  = ($InventoryRows | Where-Object { $_.Attributes -match "Hidden" }).Count
$SystemCount  = ($InventoryRows | Where-Object { $_.Attributes -match "System" }).Count
$OfflineCount = ($InventoryRows | Where-Object { $_.IsOfflineOrCloud }).Count

$Sizes      = $InventoryRows | ForEach-Object { $_.Length } | Sort-Object
$AvgSize    = if ($Sizes.Count -gt 0) { ($Sizes | Measure-Object -Average).Average } else { 0 }
$MedianSize = 0
if ($Sizes.Count -gt 0) {
    $mid = [math]::Floor($Sizes.Count / 2)
    if ($Sizes.Count % 2 -eq 0) {
        $MedianSize = ($Sizes[$mid - 1] + $Sizes[$mid]) / 2
    }
    else {
        $MedianSize = $Sizes[$mid]
    }
}

$OldestFile = $InventoryRows | Sort-Object LastWriteTime | Select-Object -First 1
$NewestFile = $InventoryRows | Sort-Object LastWriteTime -Descending | Select-Object -First 1

# Disk space info (best-effort; skip gracefully if it fails, e.g. network paths)
$DriveInfoText = "  Not available (could not determine drive information)"
try {
    $DriveRoot   = [System.IO.Path]::GetPathRoot($TargetPathNormalized)
    $Drive       = New-Object System.IO.DriveInfo($DriveRoot)
    $UsedPercent = if ($Drive.TotalSize -gt 0) { [math]::Round(($TotalBytes / $Drive.TotalSize) * 100, 2) } else { 0 }
    $DriveInfoText = @"
  Drive                       : $DriveRoot
  Total capacity              : $(Format-Bytes $Drive.TotalSize)
  Free space remaining        : $(Format-Bytes $Drive.AvailableFreeSpace)
  This inventory's size       : $(Format-Bytes $TotalBytes) ($UsedPercent% of drive)
"@
}
catch { }

# ----------------------------------------------------------------------------
# 8. Build the report text
# ----------------------------------------------------------------------------
$FilesPerSecond = if ($Stopwatch.Elapsed.TotalSeconds -gt 0) {
    [math]::Round($FileCount / $Stopwatch.Elapsed.TotalSeconds, 1)
}
else { $FileCount }

$ReportLines = [System.Collections.Generic.List[string]]::new()

$ReportLines.Add("=" * 70)
$ReportLines.Add(" THE FILE ORGANIZER -- PRELIMINARY REPORT")
$ReportLines.Add(" Project   : $($Settings.ProjectName)")
$ReportLines.Add(" Target    : $TargetPathNormalized")
$ReportLines.Add(" Run       : $RunTimestamp")
$ReportLines.Add(" Generated : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$ReportLines.Add("=" * 70)
$ReportLines.Add("")
$ReportLines.Add("SCAN SUMMARY")
$ReportLines.Add(("  Total files scanned      : {0:N0}" -f $FileCount))
$ReportLines.Add("  Total size                : $(Format-Bytes $TotalBytes) ($TotalBytes bytes)")
$ReportLines.Add("  Scan duration             : $($Stopwatch.Elapsed.ToString('hh\:mm\:ss'))")
$ReportLines.Add("  Files per second          : $FilesPerSecond")
$ReportLines.Add("")
$ReportLines.Add("FILE TYPE BREAKDOWN (by extension)")
foreach ($ext in $ByExtension) {
    $ReportLines.Add(("  {0,-14} {1,8:N0} files   {2}" -f $ext.Extension, $ext.Count, (Format-Bytes $ext.TotalSize)))
}
$ReportLines.Add("")
$ReportLines.Add("FOLDER STRUCTURE")
$ReportLines.Add("  Maximum folder depth      : $MaxDepth")
$ReportLines.Add("  Depth distribution:")
foreach ($d in $DepthGroups) {
    $ReportLines.Add(("    Depth {0,-4}: {1:N0} files" -f $d.Name, $d.Count))
}
$ReportLines.Add("  Empty folders found       : $EmptyFolderCount")
$ReportLines.Add("  Empty files found         : $EmptyFileCount")
$ReportLines.Add("")
$ReportLines.Add("SIZE STATISTICS")
$ReportLines.Add("  Average file size         : $(Format-Bytes $AvgSize)")
$ReportLines.Add("  Median file size          : $(Format-Bytes $MedianSize)")
$ReportLines.Add("  Largest files:")
$i = 1
foreach ($f in $LargestFiles) {
    $ReportLines.Add(("    {0,2}. {1}  ({2})" -f $i, $f.Path, (Format-Bytes $f.Length)))
    $i++
}
$ReportLines.Add("  Largest top-level folders:")
$i = 1
foreach ($tf in $TopLevelSizes) {
    $ReportLines.Add(("    {0,2}. {1}  ({2}, {3} files)" -f $i, $tf.Folder, (Format-Bytes $tf.TotalSize), $tf.FileCount))
    $i++
}
$ReportLines.Add("")
$ReportLines.Add("POTENTIAL DUPLICATE INDICATORS")
$ReportLines.Add("  Files sharing a filename with >=1 other file : $DuplicateNameFileCount (across $($DuplicateNameGroups.Count) distinct names)")
$ReportLines.Add("")
$ReportLines.Add("DATES (by Last Write Time)")
if ($OldestFile) { $ReportLines.Add("  Oldest file  : $($OldestFile.Path)  ($($OldestFile.LastWriteTime))") }
if ($NewestFile) { $ReportLines.Add("  Newest file  : $($NewestFile.Path)  ($($NewestFile.LastWriteTime))") }
$ReportLines.Add("")
$ReportLines.Add("SPECIAL CONDITIONS")
$ReportLines.Add("  Hidden files                              : $HiddenCount")
$ReportLines.Add("  System files                              : $SystemCount")
$ReportLines.Add("  Symlinks / junctions (not recursed)       : $TrueLinkSkippedCount")
$ReportLines.Add("  Cloud-sync placeholder folders (recursed) : $CloudPlaceholderFolderCount")
$ReportLines.Add("  Cloud-only / not locally available files  : $OfflineCount")
$ReportLines.Add("  Files with path length > 260 characters   : $LongPathCount")
$ReportLines.Add("")
$ReportLines.Add("ERRORS")
$ReportLines.Add("  Folders/files that could not be accessed  : $($ErrorEntries.Count)")
if ($ErrorEntries.Count -gt 0) {
    $ReportLines.Add("  (see Logs\errors.txt in this run folder for details)")
}
$ReportLines.Add("")
$ReportLines.Add("DISK SPACE")
$ReportLines.Add($DriveInfoText)
$ReportLines.Add("  Target drive type (detected)              : $DriveType")
$ReportLines.Add("=" * 70)

$ReportPath = Join-Path $ReportsFolder "PreliminaryReport.txt"
$ReportLines | Set-Content -LiteralPath $ReportPath -Encoding UTF8

# ----------------------------------------------------------------------------
# 9. Update settings.json
# ----------------------------------------------------------------------------
$Settings.NextDBID   = $NextDBID
$Settings.CurrentRun = $RunTimestamp

# Defensive: force RunHistory to be a real array before appending, in case
# it round-tripped through JSON as something else.
$ExistingHistory     = @($Settings.RunHistory)
$Settings.RunHistory = @($ExistingHistory + $RunTimestamp)

# Add LastPreliminaryScan if this settings.json predates the field
if (-not ($Settings.PSObject.Properties.Name -contains "LastPreliminaryScan")) {
    $Settings | Add-Member -MemberType NoteProperty -Name "LastPreliminaryScan" -Value $null
}
$Settings.LastPreliminaryScan = (Get-Date).ToString("o")

# Step 5: drive type detection result
if (-not ($Settings.PSObject.Properties.Name -contains "TargetDriveType")) {
    $Settings | Add-Member -MemberType NoteProperty -Name "TargetDriveType" -Value $null
}
$Settings.TargetDriveType = $DriveType

# Step 6: summary counts for the dashboard's post-pre-scan info screen --
# recorded here (not re-parsed from PreliminaryReport.txt later) so
# Dashboard.py has a reliable, structured source rather than scraping text.
foreach ($Field in @("LastPreliminaryFileCount", "LastPreliminaryErrorCount", "LastPreliminaryTotalBytes", "LastPreliminaryLongPathCount")) {
    if (-not ($Settings.PSObject.Properties.Name -contains $Field)) {
        $Settings | Add-Member -MemberType NoteProperty -Name $Field -Value $null
    }
}
$Settings.LastPreliminaryFileCount     = $FileCount
$Settings.LastPreliminaryErrorCount    = $ErrorEntries.Count
$Settings.LastPreliminaryTotalBytes    = $TotalBytes
$Settings.LastPreliminaryLongPathCount = $LongPathCount

$Settings | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $SettingsPath -Encoding UTF8

# ----------------------------------------------------------------------------
# 10. Finish up
# ----------------------------------------------------------------------------
Write-Host ""
Write-Host "Preliminary inventory complete." -ForegroundColor Green
Write-Host "  Files scanned : $FileCount"
Write-Host "  Total size    : $(Format-Bytes $TotalBytes)"
Write-Host "  Duration      : $($Stopwatch.Elapsed.ToString('hh\:mm\:ss'))"
Write-Host "  Run folder    : $RunFolder"
Write-Host ""

# Report is saved but not auto-opened -- see Dashboard.py for how reports are accessed.
