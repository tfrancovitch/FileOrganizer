THE FILE ORGANIZER
===================

WHAT THIS IS
-------------
A tool to inventory the files in a folder or drive, identify duplicates,
and help you decide what to keep, rename, or reorganize.


REQUIREMENTS
-------------
- Windows 10 or 11
- Internet access the first time you run it -- to install Python (if
  not already present), a few Python packages, and ffmpeg


FIRST-TIME SETUP
------------------
1. Double-click TheFileOrganizer.bat

2. If Python isn't already installed on this computer, it will be
   downloaded and installed automatically (for the current user only --
   no administrator rights needed). This is a one-time step and may
   take a minute or two. If Python is already installed, this step is
   skipped entirely and the dashboard opens right away.

3. The very first time, Windows may show a security warning on some of
   the files, since they were downloaded rather than installed
   normally. Choose "Run once", or right-click the file and choose
   Properties > Unblock, then try again.

4. The dashboard automatically checks that everything is installed
   correctly and offers to install anything missing (Python packages,
   ffmpeg). This takes a few seconds once everything's already in
   place, and only takes longer the very first time.


USING IT
---------
- Double-click TheFileOrganizer.bat any time to open the dashboard.
- Choose "New Project" to inventory a new folder or drive, or
  "Resume Project" to revisit one you've already scanned.
- Reports and detailed inventories are saved inside:
    Projects\<project name>\Runs\<timestamp>\Reports\
    Projects\<project name>\Runs\<timestamp>\Inventory\


FOLDER LAYOUT
--------------
TheFileOrganizer.bat   <- double-click this to start
README.txt             <- this file
Scripts\               <- every script the program uses (the dashboard
                          runs these for you -- no need to touch them
                          directly)
Projects\              <- created automatically; one folder per
                          project you scan, holding its settings and
                          results


TROUBLESHOOTING
-----------------
- Toolkit file problem (missing/corrupted script)?
    See Scripts\InstallationCheckReport.txt

- Missing dependency (Python package or ffmpeg)?
    See Scripts\DependencyCheckReport.txt

- Either check can also be run by hand from a PowerShell window, from
  inside the Scripts folder:
    .\Test-Installation.ps1
    .\Install-Dependencies.ps1

