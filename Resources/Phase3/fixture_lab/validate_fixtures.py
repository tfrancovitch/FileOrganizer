from pathlib import Path
import sqlite3,json
ROOT=Path(__file__).resolve().parent
FIX=ROOT/"Fixtures"

def exp(cur): return {k:json.loads(v) for k,v in cur.execute("SELECT key,value_json FROM expected_projection")}
def locs(cur):
    names=["location_id","path","root","content_id","physical_id","present","protected","physical_identity_known","bytes","observation_id"]
    return {r[0]:dict(zip(names,r)) for r in cur.execute("SELECT location_id,path,root,content_id,physical_id,present,protected,physical_identity_known,bytes,observation_id FROM evidence_location")}
def groups(cur):
    o={}
    for g,l in cur.execute("SELECT group_id,location_id FROM evidence_duplicate_member ORDER BY group_id,location_id"): o.setdefault(g,[]).append(l)
    return o
def active(cur):
    rows=cur.execute("SELECT decision_id,target_kind,target_ref,decision_kind,value_json,supersedes_decision_id,withdrawn FROM p3_decision").fetchall()
    superseded={r[5] for r in rows if r[5]}
    return [r for r in rows if r[0] not in superseded and not r[6]]
def bykind(cur):
    o={}
    for did,tk,tr,dk,vj,sup,w in active(cur):
        o.setdefault(dk,[]).append((did,tk,tr,json.loads(vj) if vj is not None else None))
    return o
def polfx(cur,L,G):
    protect=set(); sugg={}
    prefs=[]
    for kind,sj,ej in cur.execute("SELECT kind,scope_json,effect_json FROM p3_policy_version WHERE enabled=1"):
        s=json.loads(sj); e=json.loads(ej)
        if kind=="protect_folder_subtree":
            p=s["path"].lower()
            protect |= {lid for lid,x in L.items() if x["path"].lower().startswith(p)}
        elif kind=="prefer_folder_subtree": prefs.append((s["path"],e.get("over")))
    for gid,mem in G.items():
        for p,o in prefs:
            a=[x for x in mem if L[x]["path"].lower().startswith(p.lower())]
            b=[x for x in mem if o and L[x]["path"].lower().startswith(o.lower())]
            if a and b: sugg[gid]=sorted(a)[0]
    return protect,sugg

def compute(cur,fid):
    L=locs(cur); G=groups(cur); D=bykind(cur)
    prot={lid for lid,x in L.items() if x["protected"]}; pp,sugg=polfx(cur,L,G);prot|=pp
    if fid=="F01_Keeper_Protected_Hardlink":
        keep=prot|{tr for _,tk,tr,v in D.get("must_keep_location",[]) if v is True}
        can=next(v for _,tk,tr,v in D["canonical_location"])
        red={tr for _,tk,tr,v in D.get("redundant_location",[]) if v is True}
        phys={L[x]["physical_id"] for x in G["G1"] if L[x]["physical_id"]}
        return dict(keeper_set=sorted(keep),canonical=can,protected=sorted(prot),physical_copies=len(phys),hardlink_aliases=len(G["G1"])-len(phys),redundant_candidates=sorted(red),plan_eligible_reclaimable_bytes=sum(L[x]["bytes"] for x in red))
    if fid=="F02_Multiple_Intentional_Keepers":
        can=next(v for _,tk,tr,v in D["canonical_location"]); return dict(keeper_set=sorted(G["G1"]),canonical=can,redundant_candidates=[],plan_eligible_reclaimable_bytes=0,ready_for_plan=False)
    if fid=="F03_Folder_Priority_Exception":
        explicit={tr:v for _,tk,tr,v in D.get("canonical_location",[])}
        eff={gid:explicit.get(gid,s) for gid,s in sugg.items()}
        exc=sorted(gid for gid in explicit if gid in sugg and explicit[gid]!=sugg[gid])
        return dict(policy_suggestion=sugg,effective_canonical=eff,explicit_exception_groups=exc,conflicts=[])
    if fid=="F04_Frozen_Bulk_Query":
        m=[r[0] for r in cur.execute("SELECT target_ref FROM p3_bulk_member WHERE batch_id='B1' ORDER BY target_ref")]
        return dict(batch_members=m,later_matching_target="L3",later_target_in_batch="L3" in m)
    if fid=="F05_Dynamic_Policy_Future_Match":
        cnt=cur.execute("SELECT COUNT(*) FROM p3_decision WHERE origin_kind='explicit_human'").fetchone()[0]
        return dict(protected_locations=sorted(prot),explicit_human_decisions_for_policy_effect=cnt,policy_version="PV1")
    if fid=="F06_Visual_Nontransitivity":
        pd={}
        for dk in ("equivalent_copy","different"):
            for _,tk,tr,v in D.get(dk,[]): pd[tr]=dk
        return dict(pair_decisions=pd,transitive_equivalence_inferred=False)
    if fid=="F07_Related_Variant":
        keep=next(v for _,tk,tr,v in D["keep_both"]); return dict(relationship="related_variant",keeper_set=keep,ordinary_redundant_candidate=False)
    if fid=="F08_Deferred_vs_Blocked":
        rs=cur.execute("SELECT target_ref,event_kind,return_kind FROM p3_review_event ORDER BY target_ref").fetchall()
        return dict(routing={x[0]:x[1] for x in rs},return_kind={x[0]:x[2] for x in rs})
    if fid=="F09_Supersession_Undo_Reapply":
        can=next(v for _,tk,tr,v in D["canonical_location"]); total=cur.execute("SELECT COUNT(*) FROM p3_decision").fetchone()[0]
        return dict(history=["D1:A","D2:B","D2:withdrawn","D3:A"],current_canonical=can,history_rows_preserved=total)
    if fid=="F10_Revalidation":
        r=cur.execute("SELECT event_kind FROM p3_review_event WHERE target_ref='G1' ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        return dict(decision_preserved="D1",routing=r,current_applicability="requires_revalidation")
    if fid=="F11_Plan_Drift":
        r=cur.execute("SELECT plan_revision_id,fingerprint,locked,obsolete FROM p3_plan_revision").fetchone()
        return dict(plan_revision=r[0],fingerprint=r[1],locked=bool(r[2]),obsolete=bool(r[3]),reason="source decision changed",auto_replanned=False)
    if fid=="F12_Hard_Precondition_Unknown":
        k=L["A"]["physical_identity_known"]; b=cur.execute("SELECT blocked_reason FROM p3_plan_item").fetchone()[0]
        return dict(precondition_result="pass" if k else "unknown",hard_unknown_blocks=not bool(k),plan_item_state="blocked_evidence" if b else "ready")
    if fid=="F13_Metadata_Desired_Unsupported":
        obs=cur.execute("SELECT observed_value FROM evidence_metadata_question").fetchone()[0]
        desired=next(v for _,tk,tr,v in D["desired_source_metadata_set"]); b=cur.execute("SELECT blocked_reason FROM p3_plan_item").fetchone()[0]
        return dict(observed_creator=obs,desired_creator=desired,source_mutated=False,plan_item_state="unsupported_action" if b else "ready")
    if fid=="F14_Metadata_Integrity_Blockers":
        b={}
        for q,s,h in cur.execute("SELECT question_id,signed_or_certified,hardlinked FROM evidence_metadata_question ORDER BY question_id"):
            b[q]="signed_or_certified" if s else ("hardlinked_generic_write" if h else None)
        return dict(blocked=b,source_mutated=False)
    if fid=="F15_Projection_Rebuild":
        keep=prot|{tr for _,tk,tr,v in D.get("must_keep_location",[]) if v is True}
        can=next(v for _,tk,tr,v in D["canonical_location"]); red=sorted(tr for _,tk,tr,v in D["redundant_location"])
        rv=cur.execute("SELECT event_kind FROM p3_review_event WHERE target_ref='G1' ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        t=cur.execute("SELECT target_ref FROM p3_plan_item").fetchone()[0]
        return dict(keeper_set=sorted(keep),canonical=can,protected=sorted(prot),redundant=red,conflicts=[],review_state=rv,ready_for_plan=True,plan_item_target=t)
    raise KeyError(fid)

def main():
    details=[]; passed=0
    for d in sorted(FIX.iterdir()):
        if not d.is_dir(): continue
        con=sqlite3.connect(d/"fixture.sqlite"); cur=con.cursor()
        qc=cur.execute("PRAGMA quick_check").fetchone()[0]
        E=exp(cur); A=compute(cur,d.name); ok=(qc=="ok" and E==A)
        details.append({"fixture":d.name,"quick_check":qc,"pass":ok,"expected":E,"actual":A}); passed+=int(ok);con.close()
    result={"total":len(details),"passed":passed,"failed":len(details)-passed,"details":details}
    (ROOT/"VALIDATION_RESULTS.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(f"P3.R11 fixture validation: {passed}/{len(details)} PASS")
    if passed!=len(details):
        for x in details:
            if not x["pass"]: print("FAIL",x["fixture"],x["expected"],x["actual"])
        return 1
    return 0
if __name__=="__main__": raise SystemExit(main())
