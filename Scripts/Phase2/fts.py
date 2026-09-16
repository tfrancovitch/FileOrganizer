"""Literal full-text index over already-collected Phase 1 evidence artifacts.

No source file is opened by this module.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from . import VERSION
from .core import fingerprint, utc_now

FTS_KEY="literal_extracted_text/v1"


class FtsUnavailable(RuntimeError): pass


def extraction_signature(conn):
    """What the index is built from: the extracted texts, and nothing else.

    The index maps each distinct text to its extracted_content rows; which
    file currently holds a text is decided at query time, against
    file_state. So a re-scan, a fingerprinting run or an analyzer run
    changes nothing the index holds. The wider `core.evidence_signature`
    (every run, every hash, every observation) marked it stale after any
    of those, and the next search rebuilt it in silence -- seconds on a
    fixture, minutes inside one click on a large project. Only extraction
    adding rows, or a new core, is a reason to rebuild.
    """
    parts={}
    for name,sql in {
        "extracted_max":"SELECT COALESCE(MAX(extracted_content_id),0) FROM extracted_content",
        "extracted_rows":"SELECT COUNT(*) FROM extracted_content WHERE status='extracted'",
    }.items():
        try: parts[name]=conn.execute(sql).fetchone()[0]
        except sqlite3.OperationalError: parts[name]=None
    parts["phase2"]=VERSION
    return fingerprint(parts)


def index_is_current(conn):
    """True when a valid index exists and was built from the extraction as it
    stands now. Reads only, so the summary can ask on any connection."""
    try:
        row=conn.execute("SELECT status,input_signature FROM p2_derived_index WHERE derived_index_key=?",(FTS_KEY,)).fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(row and row["status"]=="valid" and row["input_signature"]==extraction_signature(conn))


class FtsManager:
    def __init__(self,conn,project_dir):
        self.conn=conn; self.project_dir=Path(project_dir).resolve()

    def supported(self):
        try:
            self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.__p2_fts_probe USING fts5(body)")
            self.conn.execute("DROP TABLE temp.__p2_fts_probe")
            return True
        except Exception:
            return False

    def _valid(self):
        sig=extraction_signature(self.conn)
        row=self.conn.execute("SELECT status,input_signature FROM p2_derived_index WHERE derived_index_key=?",(FTS_KEY,)).fetchone()
        if row and row["status"]=="valid" and row["input_signature"]!=sig:
            self.conn.execute("UPDATE p2_derived_index SET status='stale',notes=? WHERE derived_index_key=?",("Text was extracted after the index was built.",FTS_KEY)); self.conn.commit()
            return False
        return bool(row and row["status"]=="valid" and row["input_signature"]==sig)

    def ensure(self,cancel_check=None):
        if self._valid(): return
        self.rebuild(cancel_check=cancel_check)

    def rebuild(self,cancel_check=None):
        if not self.supported(): raise FtsUnavailable("This SQLite runtime does not provide FTS5")
        # P1-CORR-001 gate: storage identity must no longer be the misleading B6.2 default.
        bad=self.conn.execute(
            "SELECT COUNT(*) FROM extracted_content WHERE status='extracted' AND artifact_exists=1 AND "
            "(text_sha256 IS NULL OR storage_mode<>'content_addressed')"
        ).fetchone()[0]
        if bad:
            raise FtsUnavailable(
                f"{bad} extracted-text row(s) lack trustworthy content-addressed identity. "
                "Run the P2.9.1 schema correction/backfill or re-run content extraction before building FTS."
            )
        sig=extraction_signature(self.conn)
        self.conn.execute(
            "INSERT INTO p2_derived_index(derived_index_key,index_kind,definition_version,status,engine_version) VALUES(?,?,?,?,?) "
            "ON CONFLICT(derived_index_key) DO UPDATE SET status='building',definition_version=excluded.definition_version,engine_version=excluded.engine_version,notes=NULL",
            (FTS_KEY,"fts5","1","building",VERSION)); self.conn.commit()
        indexed=0; missing=0; bytes_read=0
        try:
            self.conn.execute("DROP TABLE IF EXISTS p2_fts_text")
            self.conn.execute("CREATE VIRTUAL TABLE p2_fts_text USING fts5(body, content='')")
            self.conn.execute("DELETE FROM p2_fts_text_map")
            seen=set(); rowid=0
            rows=self.conn.execute(
                """
                SELECT ec.extracted_content_id,ec.text_sha256,ec.extracted_relpath,ec.artifact_bytes,r.run_folder
                  FROM extracted_content ec
                  JOIN analyzer_result ar ON ar.analyzer_result_id=ec.analyzer_result_id
                  JOIN analyzer_run rr ON rr.analyzer_run_id=ar.analyzer_run_id
                  JOIN run r ON r.run_id=rr.run_id
                 WHERE ec.status='extracted' AND ec.artifact_exists=1
                   AND ec.text_sha256 IS NOT NULL AND ec.storage_mode='content_addressed'
                   AND ec.extracted_relpath IS NOT NULL AND r.run_folder IS NOT NULL
                 ORDER BY ec.extracted_content_id
                """)
            for ec in rows:
                if ec["text_sha256"] in seen: continue
                if cancel_check and cancel_check(): raise InterruptedError("FTS build cancelled")
                runs_root=(self.project_dir/"Runs").resolve()
                run_root=(runs_root/ec["run_folder"]).resolve()
                try:
                    run_root.relative_to(runs_root)
                except ValueError:
                    missing+=1; continue
                path=(run_root/Path(ec["extracted_relpath"])).resolve()
                try:
                    path.relative_to(run_root)
                except ValueError:
                    missing+=1; continue
                if not path.is_file(): missing+=1; continue
                try: text=path.read_text(encoding="utf-8")
                except (OSError,UnicodeError): missing+=1; continue
                rowid+=1; seen.add(ec["text_sha256"]); bytes_read+=len(text.encode("utf-8"))
                self.conn.execute("INSERT INTO p2_fts_text(rowid,body) VALUES(?,?)",(rowid,text))
                self.conn.execute(
                    "INSERT INTO p2_fts_text_map(fts_rowid,text_sha256,extracted_relpath,artifact_bytes,built_from_extracted_content_id) VALUES(?,?,?,?,?)",
                    (rowid,ec["text_sha256"],ec["extracted_relpath"],ec["artifact_bytes"],ec["extracted_content_id"]))
                indexed+=1
                if indexed%500==0: self.conn.commit()
            self.conn.execute(
                "UPDATE p2_derived_index SET status='valid',built_utc=?,input_signature=?,row_count=?,storage_bytes=?,notes=? WHERE derived_index_key=?",
                (utc_now(),sig,indexed,None,f"{missing} referenced evidence artifact(s) unavailable; {bytes_read} UTF-8 bytes indexed",FTS_KEY))
            self.conn.commit(); return {"indexed":indexed,"missing_artifacts":missing,"bytes_read":bytes_read}
        except Exception as exc:
            try:
                self.conn.rollback(); self.conn.execute("DROP TABLE IF EXISTS p2_fts_text")
                self.conn.execute("DELETE FROM p2_fts_text_map")
                self.conn.execute("UPDATE p2_derived_index SET status='failed',notes=? WHERE derived_index_key=?",(str(exc)[:1000],FTS_KEY)); self.conn.commit()
            except Exception: pass
            raise

    @staticmethod
    def match_expression(mode,text):
        text=str(text).replace('"',' ')
        terms=[t for t in text.split() if t]
        if not terms: raise ValueError("Empty literal-text query")
        if mode=="phrase": return '"'+' '.join(terms)+'"'
        if mode=="all_terms": return ' '.join('"'+t+'"' for t in terms)
        if mode=="any_terms": return ' OR '.join('"'+t+'"' for t in terms)
        if mode=="prefix": return ' '.join('"'+t+'"*' for t in terms)
        return '"'+terms[0]+'"' if len(terms)==1 else ' '.join('"'+t+'"' for t in terms)

    def exists_sql_for_current_file(self,mode,text):
        self.ensure()
        match=self.match_expression(mode,text)
        sql="""EXISTS (
            SELECT 1 FROM p2_fts_text ft
            JOIN p2_fts_text_map fm ON fm.fts_rowid=ft.rowid
            JOIN extracted_content xec ON xec.text_sha256=fm.text_sha256
            JOIN analyzer_result xar ON xar.analyzer_result_id=xec.analyzer_result_id
            JOIN analyzer_run xrr ON xrr.analyzer_run_id=xar.analyzer_run_id
            JOIN analyzer xa ON xa.analyzer_id=xrr.analyzer_id AND xa.analyzer_key='content_extraction'
            JOIN file_observation xfo ON xfo.file_observation_id=xar.file_observation_id
            WHERE p2_fts_text MATCH ? AND xfo.file_path_id=fs.file_path_id
              AND xar.file_observation_id=fs.current_observation_id
              AND xec.status='extracted'
              AND NOT EXISTS (
                  SELECT 1 FROM analyzer_result xn
                  JOIN analyzer_run xnr ON xnr.analyzer_run_id=xn.analyzer_run_id
                  WHERE xn.file_observation_id=xar.file_observation_id
                    AND xnr.analyzer_id=xrr.analyzer_id
                    AND (xn.analyzed_utc>xar.analyzed_utc OR
                         (xn.analyzed_utc=xar.analyzed_utc AND xn.analyzer_result_id>xar.analyzer_result_id))
              )
        )"""
        return sql,[match]
