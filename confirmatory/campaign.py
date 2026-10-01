"""Orthogonal placement/autonomy experiment, fresh seeds, and prespecified sensitivities."""
from __future__ import annotations
import argparse
import csv
import gzip
import json
import multiprocessing as mp
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from aedge.common import canonical, load_config, sha
from aedge.actors import GlobalCenter, FallbackExecutor
from aedge.runtime import METHODS, InProcessRuntime, morning_plan
from aedge.scenario import ScenarioKey, ScenarioGenerator
from aedge.metrics import reduce, stable_view

ROOT = Path(__file__).resolve().parents[1]
METHODS["CENTRAL_FOG_FB"] = (GlobalCenter, FallbackExecutor, {"trigger": "event"}, "fog")
FACTORIAL = ["CENTRAL", "CENTRAL_FB", "CENTRAL_FOG", "CENTRAL_FOG_FB", "HYBRID"]
PLACEMENT = ["CENTRAL", "CENTRAL_FOG", "HYBRID"]
C1_METHODS = FACTORIAL + ["GREEDY_FOG"]
C2_METHODS = PLACEMENT + ["GREEDY_FOG"]

def jobs():
    out = []
    for seed in range(9001, 9041):
        for thermal in (False, True):
            for net in ("N1", "N3", "N4"):
                out.append(dict(stage="C1", seed=seed, thermal=thermal, network=net,
                                outage=None, iterations=30, methods=C1_METHODS))
    for seed in range(9001, 9021):
        for window in ([120,150], [120,180], [120,300], [60,180], [180,300]):
            out.append(dict(stage="C2", seed=seed, thermal=True, network="N4",
                            outage=window, iterations=30, methods=C2_METHODS))
        for it in (5, 100):
            for net in ("N1", "N4"):
                out.append(dict(stage="C3", seed=seed, thermal=True, network=net,
                                outage=None, iterations=it, methods=PLACEMENT))
    return out

def job_id(j):
    w = j["outage"]
    return f"{j['stage']}_S{j['seed']}_H{int(j['thermal'])}_{j['network']}_W{w[0] if w else 120}-{w[1] if w else 240}_I{j['iterations']}"

def prepare(j):
    cfg = load_config()
    cfg["control"]["alns_iterations_event"] = j["iterations"]
    if j["outage"] is not None:
        cfg["network"]["profiles"]["N4"]["cloud_outage"] = j["outage"]
    sc = ScenarioGenerator(cfg).build(ScenarioKey(j["seed"], 8, .9, True, j["thermal"], j["network"]))
    return cfg, sc

def served_metrics(sc, log):
    """Completed, present-patient visits whose START is on time; all demand in denominator."""
    starts = {e["r"]: e for e in log if e["ev"] == "service_start"}
    ends = {e["r"]: e for e in log if e["ev"] == "service_end" and e["outcome"] == "served"}
    R = sc["requests"]
    served = {r for r, e in ends.items() if r in starts and starts[r]["outcome"] == "served"
              and R[r]["a"] <= starts[r]["t"] <= R[r]["b"] and e["team"] == starts[r]["team"]}
    urgent = {r["id"] for r in R if r["urgent"]}
    dynamic = {r["id"] for r in R if not r["static"]}
    return dict(priority_served_pct=100*sum(R[r]["priority"] for r in served)/sum(r["priority"] for r in R),
                urgent_served_pct=100*len(served & urgent)/len(urgent) if urgent else None,
                dynamic_served_pct=100*len(served & dynamic)/len(dynamic) if dynamic else None,
                completed_served=len(served),
                unfinished_served=sum(e["outcome"] == "served" and r not in ends for r,e in starts.items()))

def write_csv(path, rows):
    with path.open("w", newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def run_scenario(args):
    j, out_path = args
    out = Path(out_path); jid=job_id(j)
    cfg, sc = prepare(j)
    plan = morning_plan(sc, cfg)
    (out/"scenarios"/f"{jid}.json").write_text(canonical(dict(job=j,config=cfg,scenario=sc,morning=plan)))
    rows=[]
    for method in j["methods"]:
        rid=f"{jid}_{method}"; t0=time.perf_counter()
        log=InProcessRuntime(sc,cfg,method,plan).run()
        elapsed=time.perf_counter()-t0
        metrics=reduce(sc,log,cfg)
        row=dict(run_id=rid,job_id=jid,stage=j["stage"],seed=j["seed"],thermal=int(j["thermal"]),
                 network=j["network"],outage_start=sc["network"]["cloud_outage"][0] if sc["network"]["cloud_outage"] else 0,
                 outage_duration=(sc["network"]["cloud_outage"][1]-sc["network"]["cloud_outage"][0]) if sc["network"]["cloud_outage"] else 0,
                 iterations=j["iterations"],method=method,scenario_sha256=sha(sc),config_sha256=sha(cfg),
                 morning_sha256=sha(plan),trajectory_sha256=sha(stable_view(log)),wall_s=elapsed,
                 **metrics,**served_metrics(sc,log))
        with (out/"events"/f"{rid}.jsonl.gz").open("wb") as raw:
            with gzip.GzipFile(fileobj=raw,mode="wb",mtime=0) as z:
                z.write(("\n".join(canonical(e) for e in log)+"\n").encode())
        rows.append(row)
    (out/"checkpoints"/f"{jid}.json").write_text(canonical(rows))
    return rows

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--out",type=Path,default=ROOT/"campaigns/confirmatory/results")
    ap.add_argument("--workers",type=int,default=4);ap.add_argument("--smoke",action="store_true")
    a=ap.parse_args()
    out=a.out.resolve()
    for name in ["events","scenarios","checkpoints"]:(out/name).mkdir(parents=True,exist_ok=True)
    planned=jobs()
    if a.smoke:
        planned=[dict(planned[0],seed=7101)] # reproducibility smoke only; excluded from reported results
    t0=time.perf_counter(); rows=[];todo=[]
    runs_path=out/"runs.csv"
    if runs_path.exists():
        with runs_path.open() as f:
            rows=list(csv.DictReader(f))
    existing={r["run_id"] for r in rows}
    for j in planned:
        missing=[m for m in j["methods"] if f"{job_id(j)}_{m}" not in existing]
        if missing:
            todo.append((dict(j,methods=missing),str(out)))
    start=datetime.now(timezone.utc).isoformat()
    print(f"{sum(len(j['methods']) for j in planned)} planned runs; "
          f"{sum(len(j['methods']) for j,_ in todo)} runs in {len(todo)} scenario jobs remaining",flush=True)
    with mp.get_context("spawn").Pool(a.workers) as pool:
        for k,result in enumerate(pool.imap_unordered(run_scenario,todo)):
            rows.extend(result)
            if (k+1)%10==0 or k+1==len(todo):
                write_csv(out/"runs.csv",sorted(rows,key=lambda r:r["run_id"]))
                print(f"{len(rows)} runs saved; elapsed {time.perf_counter()-t0:.1f}s",flush=True)
    write_csv(out/"runs.csv",sorted(rows,key=lambda r:r["run_id"]))
    (out/"environment.json").write_text(json.dumps(dict(python=sys.version,platform=platform.platform(),machine=platform.machine(),
                started_utc=start,finished_utc=datetime.now(timezone.utc).isoformat(),workers=a.workers,runs=len(rows),
                elapsed_s=time.perf_counter()-t0,smoke=a.smoke),indent=2)+"\n")

if __name__=="__main__":main()
