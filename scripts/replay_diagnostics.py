"""Observational mode-switching and thermal-reflex replay diagnostics.

Replays Hybrid trajectories without changing actors or decisions and
checks their archived hashes. Counts are descriptive, not a causal mediation
analysis. A reported C1 seed is archived for independent raw-log replay.
"""
from __future__ import annotations
import argparse
import csv
import gzip
import json
import multiprocessing as mp
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aedge.common import canonical, sha
from aedge.metrics import stable_view
from aedge.runtime import InProcessRuntime, morning_plan
from confirmatory.campaign import jobs, job_id, prepare, served_metrics


def one(args):
    j, expected, replay_dir = args
    cfg, sc = prepare(j)
    plan = morning_plan(sc, cfg)
    log = InProcessRuntime(sc, cfg, "HYBRID", plan).run()
    rid = job_id(j) + "_HYBRID"
    actual = sha(stable_view(log))
    assert actual == expected, (rid, "trajectory changed")
    w = sc["network"]["cloud_outage"]
    start, end = w if w else (0, 0)
    counts = Counter()
    for e in log:
        if e["ev"] == "thermal_alert":
            counts["thermal_alerts"] += 1
        if e["ev"] == "agent_decision" and e["reason"] == "thermal_reflex":
            counts["connected_thermal_reflex"] += 1
        if e["ev"] == "release":
            phase = "during" if w and start <= e["t"] < end else "after" if w and e["t"] >= end else "before"
            counts["releases_" + phase] += 1
            if e["reason"] == "dropped":
                counts["market_dropped"] += 1
        if e["ev"] == "degrade_to_market":
            counts["market_entries"] += 1
        if e["ev"] == "actor_summary" and "token_log" in e:
            for kind, t, *_ in e["token_log"]:
                if kind == "revoke" and w and end <= t < end + 10:
                    counts["recovery_revokes_first_10min"] += 1
    keys = ("thermal_alerts", "connected_thermal_reflex", "releases_before", "releases_during",
            "releases_after", "market_dropped", "market_entries", "recovery_revokes_first_10min")
    row = dict(run_id=rid, seed=j["seed"], stage=j["stage"], network=j["network"], thermal=int(j["thermal"]),
               outage_start=start, outage_duration=end-start, trajectory_sha256=actual,
               **{k: counts[k] for k in keys})
    if j["stage"] == "C1" and j["seed"] == 9001 and j["thermal"] and j["network"] == "N4":
        out = Path(replay_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "scenario.json").write_text(canonical(dict(job=j, config=cfg, scenario=sc, morning=plan)) + "\n")
        samples = []
        from aedge.metrics import reduce
        for method in j["methods"]:
            events = log if method == "HYBRID" else InProcessRuntime(sc, cfg, method, plan).run()
            with gzip.GzipFile(filename=str(out / (method + ".jsonl.gz")), mode="wb", mtime=0) as f:
                f.write(("\n".join(canonical(e) for e in events) + "\n").encode())
            samples.append(dict(run_id=job_id(j)+"_"+method, method=method, seed=j["seed"],
                                trajectory_sha256=sha(stable_view(events)), **served_metrics(sc, events),
                                **reduce(sc, events, cfg)))
        with (out / "reported_rows.csv").open("w", newline="") as f:
            writer=csv.DictWriter(f,fieldnames=list(samples[0]),lineterminator="\n")
            writer.writeheader();writer.writerows(samples)
    return row


def main():
    ap=argparse.ArgumentParser();ap.add_argument("--workers",type=int,default=4)
    ap.add_argument("--out", type=Path, default=ROOT / "reproduced_diagnostics")
    args=ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows=list(csv.DictReader((ROOT/"campaigns/confirmatory/results/runs.csv").open()))
    hashes={r["run_id"]:r["trajectory_sha256"] for r in rows}
    planned=[j for j in jobs() if j["stage"] in ("C1","C2")]
    with mp.get_context("spawn").Pool(args.workers) as pool:
        out=list(pool.imap_unordered(one,[(j,hashes[job_id(j)+"_HYBRID"],str(args.out / "reported_C1")) for j in planned]))
    target=args.out / "hybrid_diagnostics.csv"
    with target.open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(out[0]),lineterminator="\n")
        writer.writeheader();writer.writerows(sorted(out,key=lambda r:r["run_id"]))
    print(f"{len(out)} unchanged Hybrid hashes; diagnostics written to {target}")


if __name__ == "__main__":
    main()
