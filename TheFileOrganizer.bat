@echo off
setlocal enabledelayedexpansion

rem ============================================================
rem  TheFileOrganizer.bat
rem  Part of: The File Organizer
rem
rem  The single double-click entry point. Works even on a
rem  completely fresh Windows install with nothing else set up:
rem  if Python isn't found (or is older than 3.9), this downloads
rem  and silently installs it -- per-user, no administrator
rem  rights needed -- before launching the dashboard. If Python
rem  is already present, it skips straight to launching.
rem
rem  A .bat file was used instead of a compiled .exe or a .pyw
rem  specifically because it needs zero dependencies to run at
rem  all (cmd.exe ships on every Windows install) and can do real
rem  work (checking for / installing Python) before anything else
rem  needs to exist -- a .pyw can't do that, since it needs
rem  Python to already exist just to be interpreted.
rem
rem  NOTE: the Python version/URL below should be updated
rem  periodically -- check https://www.python.org/downloads/
rem  for the current stable release.
rem ============================================================

set "ROOT=%~dp0"
set "DASHBOARD=%ROOT%Scripts\Dashboard.py"

rem ------------------------------------------------------------
rem 1. Is a suitable Python (3.9+) already on PATH?
rem ------------------------------------------------------------
where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>nul
    if !ERRORLEVEL! EQU 0 (
        goto :launch
    )
)

rem ------------------------------------------------------------
rem 2. Not found, or too old -- download and install silently.
rem ------------------------------------------------------------
echo ============================================================
echo  Python was not found on this computer.
echo  Downloading and installing it now -- this is a one-time
echo  setup step and may take a minute. An internet connection
echo  is required for this step.
echo ============================================================
echo.

set "PYURL=https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
set "INSTALLER=%TEMP%\python-installer.exe"

where curl >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    curl -L -o "%INSTALLER%" "%PYURL%"
) else (
    rem Fallback for older Windows builds without curl built in
    powershell -NoProfile -Command "Invoke-WebRequest -Uri '%PYURL%' -OutFile '%INSTALLER%'"
)

if not exist "%INSTALLER%" (
    echo.
    echo ERROR: Could not download the Python installer.
    echo Please install Python manually from https://python.org, then run this again.
    pause
    exit /b 1
)

echo Installing Python for the current user (no administrator rights needed)...
"%INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
del "%INSTALLER%" >nul 2>nul

rem Locate the just-installed copy directly by folder, rather than relying
rem on the "python" command working immediately -- PATH changes made by
rem the installer don't take effect in this already-running window.
set "PYTHON_DIR="
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do set "PYTHON_DIR=%%D"

if not defined PYTHON_DIR (
    echo.
    echo ERROR: Python installation did not complete successfully.
    echo Please install Python manually from https://python.org, then run this again.
    pause
    exit /b 1
)

echo Python installed successfully.
echo.

if exist "%PYTHON_DIR%\pythonw.exe" (
    start "" "%PYTHON_DIR%\pythonw.exe" "%DASHBOARD%"
) else (
    start "" "%PYTHON_DIR%\python.exe" "%DASHBOARD%"
)
exit /b 0

rem ------------------------------------------------------------
rem 3. Launch (Python already present and new enough)
rem ------------------------------------------------------------
:launch
where pythonw >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    start "" pythonw "%DASHBOARD%"
) else (
    python "%DASHBOARD%"
)
exit /b 0
