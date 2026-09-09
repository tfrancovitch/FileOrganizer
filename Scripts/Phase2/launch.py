#!/usr/bin/env python3
"""Launch the Phase 2 Hybrid Evidence Explorer for one existing project."""
import os,sys
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"]="1"
THIS=Path(__file__).resolve().parent
SCRIPTS=THIS.parent
DB=SCRIPTS/"Database"
sys.path.insert(0,str(SCRIPTS)); sys.path.insert(0,str(DB))

if __name__=="__main__":
    if len(sys.argv)!=2:
        raise SystemExit("Usage: launch.py <project-directory>")
    project_dir=Path(sys.argv[1]).resolve()
    # Use the existing trusted project-isolation/migration boundary first.
    import fo_db
    conn,_project=fo_db.open_project(str(project_dir),app_version="P2.9.1")
    conn.close()
    from Phase2.gui import Phase2App
    app=Phase2App(project_dir); app.mainloop()
