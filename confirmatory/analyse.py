"""Seed-level estimands for the confirmatory phase of the unified study."""
import argparse
import csv
import json
from pathlib import Path
import numpy as np
from scipy.stats import t as student_t
from aedge.analysis import boot_ci, signflip_p, holm
from .campaign import ROOT, jobs, job_id

def summary(d):
    d=np.asarray(d,float);n=len(d);mu=float(d.mean());sd=float(d.std(ddof=1));se=sd/np.sqrt(n)
    q=student_t.ppf(.975,n-1);q90=student_t.ppf(.95,n-1)
    lo,hi=boot_ci(d,seed=53137)
    p=signflip_p(d,seed=47183)
    return dict(n=n,mean=mu,sd=sd,se=float(se),lo=float(mu-q*se),hi=float(mu+q*se),
                lo90=float(mu-q90*se),hi90=float(mu+q90*se),bootstrap_lo=lo,bootstrap_hi=hi,p=p,
                wins=int(sum(d>1e-10)),ties=int(sum(abs(d)<=1e-10)),losses=int(sum(d< -1e-10)))

def tost(d,margin=1.5):
    s=summary(d);se=s["se"];mu=s["mean"];df=s["n"]-1
    if se==0:
        p=0. if abs(mu)<margin else 1.
    else:
        p=float(max(student_t.sf((mu+margin)/se,df),student_t.cdf((mu-margin)/se,df)))
    return dict(**s,margin=margin,p_tost=p,equivalent=p<.05)

def read_rows(out):
    rows=list(csv.DictReader((out/"runs.csv").open()))
    for r in rows:
        for k,v in r.items():
            try:r[k]=float(v)
            except (ValueError,TypeError):pass
    expected={job_id(j)+"_"+m for j in jobs() for m in j["methods"]}
    ids=[r["run_id"] for r in rows]
    assert len(ids)==len(set(ids)) and set(ids)==expected,"Incomplete or duplicated design"
    return rows

def analyse(out):
    rows=read_rows(out)
    idx={(r["stage"],int(r["seed"]),int(r["thermal"]),r["network"],int(r["outage_start"]),int(r["outage_duration"]),int(r["iterations"]),r["method"]):r for r in rows}
    def vector(net,weights,metric="priority_served_pct",thermal=(0,1),seeds=range(9001,9041),stage="C1",start=None,duration=None,it=30):
        start=120 if start is None and net=="N4" else (start or 0)
        duration=120 if duration is None and net=="N4" else (duration or 0)
        return np.array([np.mean([sum(w*idx[stage,s,h,net,start,duration,it,m][metric] for m,w in weights.items()) for h in thermal]) for s in seeds])
    P={"CENTRAL_FOG":.5,"CENTRAL_FOG_FB":.5,"CENTRAL":-.5,"CENTRAL_FB":-.5}
    A={"CENTRAL_FB":.5,"CENTRAL_FOG_FB":.5,"CENTRAL":-.5,"CENTRAL_FOG":-.5}
    I={"CENTRAL_FOG_FB":1,"CENTRAL_FOG":-1,"CENTRAL_FB":-1,"CENTRAL":1}
    definitions=[("F1_placement_N4","N4",P),("F2_autonomy_N3","N3",A),("F3_interaction_N4","N4",I)]
    confirm={name:summary(vector(net,w)) for name,net,w in definitions}
    for key,p in zip(confirm,holm([v["p"] for v in confirm.values()])):confirm[key]["p_holm"]=p
    eq={"Q5_hybrid_vs_fog_N4":tost(vector("N4",{"HYBRID":1,"CENTRAL_FOG":-1}))}
    q5_by_bundle={
        label:tost(vector("N4",{"HYBRID":1,"CENTRAL_FOG":-1},thermal=(thermal,)))
        for label,thermal in (("benign",0),("stress",1))
    }
    q5_power={"margin_pp":1.5,"observed_sd_pp":eq["Q5_hybrid_vs_fog_N4"]["sd"],
              "observed_effect_pp":eq["Q5_hybrid_vs_fog_N4"]["mean"],
              "interpretation":"n=40 was not power-justified; the equivalence result is descriptive and bundle-dependent"}
    descr={}
    for net in ("N1","N3","N4"):
        for m in ("CENTRAL","CENTRAL_FB","CENTRAL_FOG","CENTRAL_FOG_FB","HYBRID","GREEDY_FOG","STATIC","GREEDY_FB"):
            descr[net+"|"+m]={metric:summary(vector(net,{m:1},metric)) for metric in
                             ["priority_served_pct","priority_handled_pct","urgent_served_pct","travel_min","quarantines","center_p95_ms","agent_p95_ms","bytes_sent","releases"]}
    contrasts={}
    for net in ("N1","N3","N4"):
        for name,w in [("placement",P),("autonomy",A),("interaction",I),("hybrid_minus_cloud",{"HYBRID":1,"CENTRAL":-1}),("hybrid_minus_fog",{"HYBRID":1,"CENTRAL_FOG":-1})]:
            contrasts[net+"|"+name]=summary(vector(net,w))
    sensitivity={}
    for start,duration in [(120,30),(120,60),(120,120),(120,180),(60,120),(180,120)]:
        stage="C1" if (start,duration)==(120,120) else "C2"
        for m in ("CENTRAL_FOG","HYBRID"):
            sensitivity[f"outage|{start}|{duration}|{m}"]=summary(vector("N4",{m:1,"CENTRAL":-1},thermal=(1,),seeds=range(9001,9021),stage=stage,start=start,duration=duration))
        sensitivity[f"outage|{start}|{duration}|GREEDY_FOG"]=summary(
            vector("N4",{"GREEDY_FOG":1,"CENTRAL":-1},thermal=(1,),
                   seeds=range(9001,9021),stage=stage,start=start,duration=duration))
    budget={}
    fallback_contrasts={}
    for start,duration in [(120,30),(120,60),(120,120),(120,180),(60,120),(180,120)]:
        stage="C1" if (start,duration)==(120,120) else "C2"
        for name,w in (("greedy_fb_minus_cloud",{"GREEDY_FB":1,"CENTRAL":-1}),
                       ("greedy_fb_minus_fog",{"GREEDY_FB":1,"CENTRAL_FOG":-1}),
                       ("hybrid_minus_greedy_fb",{"HYBRID":1,"GREEDY_FB":-1}),
                       ("hybrid_minus_fog",{"HYBRID":1,"CENTRAL_FOG":-1})):
            fallback_contrasts[f"{start}|{duration}|{name}"]=summary(vector(
                "N4",w,thermal=(1,),seeds=range(9001,9021),stage=stage,start=start,duration=duration))
    weak_placement={net:summary(vector(net,{"GREEDY_FOG":1,"STATIC":-1})) for net in ("N1","N3","N4")}
    for it in (5,30,100):
        stage="C1" if it==30 else "C3"
        for net in ("N1","N4"):
            for m in ("CENTRAL_FOG","HYBRID"):
                budget[f"{it}|{net}|{m}"]=summary(vector(net,{m:1,"CENTRAL":-1},thermal=(1,),seeds=range(9001,9021),stage=stage,it=it))
    secondary={name:summary(vector(net,w,metric="priority_handled_pct")) for name,net,w in definitions}
    audit={k:int(sum(r[k] for r in rows)) for k in ["violations","token_overlaps","unauthorized_services","cold_after_quarantine","not_home","late_returns","alns_timeouts","unfinished_served","quarantines"]}
    result=dict(runs=len(rows),stages={s:sum(r["stage"]==s for r in rows) for s in ("C1","C2","C3")},
                independent_seeds=40,confirmatory=confirm,equivalence=eq,equivalence_by_bundle=q5_by_bundle,
                equivalence_design_note=q5_power,descriptive=descr,exploratory=contrasts,
                outage_sensitivity=sensitivity,budget_sensitivity=budget,handling_sensitivity=secondary,integrity=audit,
                exploratory_fallback_contrasts=fallback_contrasts,exploratory_weak_policy_placement=weak_placement,
                analysis_plan={"primary_superiority":["F1","F2","F3"],"primary_equivalence":"E3 (code key Q5)",
                               "additional_policy_and_bundle_analyses":"exploratory; shared campaign seeds",
                               "external_preregistration":False})
    (out/"analysis.json").write_text(json.dumps(result,indent=2)+"\n")
    return result

if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("--out",type=Path,default=ROOT/"campaigns/confirmatory/results");a=ap.parse_args()
    r=analyse(a.out);print(json.dumps({k:v for k,v in r.items() if k in ["runs","stages","confirmatory","equivalence","integrity"]},indent=2))
