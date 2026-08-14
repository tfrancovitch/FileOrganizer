<#
    New-Project.ps1
    Part of: The File Organizer
    Version: 2.1.1

    Purpose:
        Creates a new Project under The_File_Organizer\Projects\ and
        writes an initial settings.json. Scripts are NOT copied into the
        project -- every pipeline script lives only in the master
        The_File_Organizer\Scripts\ folder and is told which project to
        operate on via a -SettingsPath parameter (see Common.ps1's
        Get-RunContext and each script's own param block).

    Usage:
        .\New-Project.ps1 "D:\Photos"

    Notes:
        - This script now lives in The_File_Organizer\Scripts\ alongside
          every other script (the project root holds only
          TheFileOrganizer.bat and README.txt).
        - It will prompt you for a Project Name after you press Enter.
        - Leaving the Project Name blank will auto-generate
          "New-Project", "New-Project (2)", "New-Project (3)", etc.
#>

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$TargetPath,

    # Optional. If supplied, skips the interactive "Project Name" prompt --
    # this is what lets the Dashboard drive this script as a background
    # subprocess without ever hanging on Read-Host waiting for input that
    # will never come. Interactive CLI use (no second argument) behaves
    # exactly as before.
    [Parameter(Position = 1)]
    [string]$ProjectName
)

# ----------------------------------------------------------------------------
# 0. Setup / constants
# ----------------------------------------------------------------------------
$ToolVersion = "2.1.0"

# This script now lives in The_File_Organizer\Scripts\, so the true
# project root is one level up.
$RootFolder     = Split-Path $PSScriptRoot -Parent
$ProjectsFolder = Join-Path $RootFolder "Projects"

# ----------------------------------------------------------------------------
# 1. Validate the target path
# ----------------------------------------------------------------------------
if (-not (Test-Path -LiteralPath $TargetPath -PathType Container)) {
    Write-Host "ERROR: The path '$TargetPath' does not exist or is not a folder/drive." -ForegroundColor Red
    exit 1
}

# Resolve to a full, unambiguous path (handles relative paths, trailing slashes, etc.)
$TargetPath = (Resolve-Path -LiteralPath $TargetPath).Path

# ----------------------------------------------------------------------------
# 2. Ensure the Projects folder exists
# ----------------------------------------------------------------------------
if (-not (Test-Path -LiteralPath $ProjectsFolder)) {
    New-Item -ItemType Directory -Path $ProjectsFolder | Out-Null
}

# ----------------------------------------------------------------------------
# 3. Prompt for a project name (unless already supplied via -ProjectName)
# ----------------------------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($ProjectName)) {
    $ProjectName = Read-Host "Project Name"
}

function Get-DefaultProjectName {
    param([string]$ProjectsFolder)

    $baseName = "New-Project"
    $candidate = $baseName
    $counter = 2

    while (Test-Path -LiteralPath (Join-Path $ProjectsFolder $candidate)) {
        $candidate = "$baseName ($counter)"
        $counter++
    }

    return $candidate
}

if ([string]::IsNullOrWhiteSpace($ProjectName)) {
    $ProjectName = Get-DefaultProjectName -ProjectsFolder $ProjectsFolder
    Write-Host "No name entered. Using default: '$ProjectName'" -ForegroundColor Yellow
}
else {
    # Trim whitespace, but preserve the name as typed otherwise
    $ProjectName = $ProjectName.Trim()
}

$ProjectFolder = Join-Path $ProjectsFolder $ProjectName

# If the user typed a name that already exists, stop rather than overwrite.
if (Test-Path -LiteralPath $ProjectFolder) {
    Write-Host "ERROR: A project named '$ProjectName' already exists at:" -ForegroundColor Red
    Write-Host "  $ProjectFolder" -ForegroundColor Red
    Write-Host "Please re-run and choose a different Project Name." -ForegroundColor Red
    exit 1
}

# ----------------------------------------------------------------------------
# 4. Create the project folder structure (settings.json + Runs\ only --
#    no Scripts\ folder; every script now runs from the master location)
# ----------------------------------------------------------------------------
Write-Host "Creating project '$ProjectName'..." -ForegroundColor Cyan

New-Item -ItemType Directory -Path $ProjectFolder | Out-Null
New-Item -ItemType Directory -Path (Join-Path $ProjectFolder "Runs") | Out-Null

# ----------------------------------------------------------------------------
# 5. Write settings.json
# ----------------------------------------------------------------------------
$Settings = [ordered]@{
    ProjectName   = $ProjectName
    ToolVersion   = $ToolVersion
    CreatedOn     = (Get-Date).ToString("o")
    TargetPath    = $TargetPath
    CurrentRun    = $null
    RunHistory    = @()
    NextDBID      = 1
    SchemaVersion = 1
}

$SettingsPath = Join-Path $ProjectFolder "settings.json"
$Settings | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $SettingsPath -Encoding UTF8

# ----------------------------------------------------------------------------
# 6. Done
# ----------------------------------------------------------------------------
Write-Host ""
Write-Host "Project created successfully." -ForegroundColor Green
Write-Host "  Project Name : $ProjectName"
Write-Host "  Project Path : $ProjectFolder"
Write-Host "  Target Path  : $TargetPath"
Write-Host ""
Write-Host "Next step: from the master Scripts folder, run PreliminaryInventory.ps1" -ForegroundColor Yellow
Write-Host "  cd `"$RootFolder\Scripts`""
Write-Host "  .\PreliminaryInventory.ps1 -SettingsPath `"$SettingsPath`""

