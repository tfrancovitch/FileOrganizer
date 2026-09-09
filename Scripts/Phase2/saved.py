"""Saved Query persistence for Phase 2."""
from __future__ import annotations

import json
import uuid

from .core import canonical_json, fingerprint, utc_now
from . import QUERY_SCHEMA, SEMANTIC_CONTRACT


class SavedQueryStore:
    def __init__(self, conn):
        self.conn = conn

    def create(self, name, query, description=None):
        uid="sq:"+str(uuid.uuid4())
        now=utc_now()
        cur=self.conn.execute(
            "INSERT INTO p2_saved_query(project_id,saved_query_uid,name,description,created_utc,updated_utc) VALUES(1,?,?,?,?,?)",
            (uid,name,description,now,now))
        saved_id=cur.lastrowid
        rev=self._insert_revision(saved_id,1,None,query)
        self.conn.commit()
        return {"saved_query_uid":uid,"saved_query_id":saved_id,**rev}

    def revise(self, saved_query_uid, query, description=None, name=None):
        row=self.conn.execute("SELECT * FROM p2_saved_query WHERE saved_query_uid=?",(saved_query_uid,)).fetchone()
        if not row: raise KeyError(saved_query_uid)
        latest=self.conn.execute(
            "SELECT * FROM p2_saved_query_revision WHERE saved_query_id=? ORDER BY revision_number DESC LIMIT 1",
            (row["saved_query_id"],)).fetchone()
        revno=(latest["revision_number"] if latest else 0)+1
        parent=latest["saved_query_revision_id"] if latest else None
        rev=self._insert_revision(row["saved_query_id"],revno,parent,query)
        self.conn.execute(
            "UPDATE p2_saved_query SET name=COALESCE(?,name),description=COALESCE(?,description),updated_utc=? WHERE saved_query_id=?",
            (name,description,utc_now(),row["saved_query_id"]))
        self.conn.commit(); return rev

    def _insert_revision(self,saved_id,revno,parent,query):
        if query.get("query_schema") != QUERY_SCHEMA or query.get("semantic_contract") != SEMANTIC_CONTRACT:
            raise ValueError("Saved query uses unsupported schema/semantic contract")
        payload=canonical_json(query); fp=fingerprint(query); now=utc_now()
        cur=self.conn.execute(
            "INSERT INTO p2_saved_query_revision(saved_query_id,revision_number,parent_revision_id,query_schema,semantic_contract,query_json,query_fingerprint,created_utc) VALUES(?,?,?,?,?,?,?,?)",
            (saved_id,revno,parent,QUERY_SCHEMA,SEMANTIC_CONTRACT,payload,fp,now))
        return {"saved_query_revision_id":cur.lastrowid,"revision_number":revno,"query_fingerprint":fp,"created_utc":now}

    def list(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT q.saved_query_uid,q.name,q.description,q.updated_utc,MAX(r.revision_number) revision_number "
            "FROM p2_saved_query q JOIN p2_saved_query_revision r ON r.saved_query_id=q.saved_query_id "
            "GROUP BY q.saved_query_id ORDER BY lower(q.name),q.saved_query_id")]

    def get_latest(self, saved_query_uid):
        row=self.conn.execute(
            "SELECT r.*,q.saved_query_uid,q.name,q.description FROM p2_saved_query q JOIN p2_saved_query_revision r ON r.saved_query_id=q.saved_query_id "
            "WHERE q.saved_query_uid=? ORDER BY r.revision_number DESC LIMIT 1",(saved_query_uid,)).fetchone()
        if not row: raise KeyError(saved_query_uid)
        d=dict(row); d["query"]=json.loads(d.pop("query_json")); return d

    def get_revision(self, revision_ref):
        # accepts integer id or 'sqrev:<id>' for P2.4-style refs
        try:
            rid=int(str(revision_ref).split(":")[-1])
        except Exception:
            raise KeyError(revision_ref)
        row=self.conn.execute("SELECT * FROM p2_saved_query_revision WHERE saved_query_revision_id=?",(rid,)).fetchone()
        if not row: raise KeyError(revision_ref)
        d=dict(row); d["query"]=json.loads(d.pop("query_json")); return d

    def delete(self,saved_query_uid):
        self.conn.execute("DELETE FROM p2_saved_query WHERE saved_query_uid=?",(saved_query_uid,)); self.conn.commit()
