#!/usr/bin/env python3
r"""Launch the Dashboard.

    launch.py                    the start screen: open a project, or create one
    launch.py <project-directory> straight into that project's hub

Opening a project goes through fo_db.open_project() first -- the trusted
isolation-guard and migration boundary -- and closes that connection before
the window opens its own. Same order the window itself uses for every run.
"""
import os,sys
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"]="1"
THIS=Path(__file__).resolve().parent
SCRIPTS=THIS.parent
DB=SCRIPTS/"Database"
sys.path.insert(0,str(SCRIPTS)); sys.path.insert(0,str(DB))

if __name__=="__main__":
    if len(sys.argv)>2:
        raise SystemExit("Usage: launch.py [<project-directory>]")
    project_dir=Path(sys.argv[1]).resolve() if len(sys.argv)==2 else None
    if project_dir is not None:
        # Use the existing trusted project-isolation/migration boundary first.
        import fo_db
        conn,_project=fo_db.open_project(str(project_dir),app_version="P2.9.1")
        conn.close()
    from Phase2.gui import Phase2App
    app=Phase2App(project_dir); app.mainloop()
