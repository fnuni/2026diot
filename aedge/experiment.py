"""Screening-phase definition and parallel job execution.

Usage:
  python -m aedge.experiment --stage S1 --output reproduced_screening
  python -m aedge.experiment --stage S1 --shard 0/4 --output reproduced_screening
"""
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
from pathlib import Path

from .common import canonical, load_config, sha
from .metrics import reduce, stable_view
from .runtime import BACKENDS, METHODS, morning_plan
from .scenario import ScenarioGenerator, ScenarioKey

TEST_SEEDS = list(range(8001, 8041))


SCREENING_METHODS = ["STATIC", "PERIODIC", "CENTRAL", "CENTRAL_FB", "CENTRAL_FOG", "HYBRID", "HYBRID_EAGER", "EDGE_MARKET"]
CONNECTIVITY_METHODS = ["CENTRAL", "CENTRAL_FB", "HYBRID", "HYBRID_EAGER", "EDGE_MARKET"]
THERMAL_STRESS_METHODS = ["CENTRAL", "CENTRAL_FB", "HYBRID"]


def stage_jobs(stage: str):
    """Return (scenario, controller, backend) jobs for one study stage."""
    jobs = []
    if stage == "S1":  # main factorial: traffic x thermal x network x method
        for s in TEST_SEEDS:
            for tr in (False, True):
                for th in (False, True):
                    for net in ("N0", "N1", "N2", "N3", "N4"):
                        for m in SCREENING_METHODS:
                            jobs.append((ScenarioKey(s, 8, 0.9, tr, th, net), m, "inprocess"))
    elif stage == "S2":  # load and size controlled separately, density constant, combined environment
        for s in TEST_SEEDS[:20]:
            for net in ("N1", "N3", "N4"):
                for teams, load in ((8, 0.75), (8, 1.05), (4, 0.9), (16, 0.9)):
                    for m in SCREENING_METHODS:
                        jobs.append((ScenarioKey(s, teams, load, True, True, net), m, "inprocess"))
    elif stage == "S3":  # connectivity decision map (coverage-gap process swept)
        for s in TEST_SEEDS[:20]:
            for a in (0.95, 0.85, 0.75, 0.65, 0.55):
                for mo in (5.0, 15.0, 30.0):
                    for m in CONNECTIVITY_METHODS:
                        jobs.append((ScenarioKey(s, 8, 0.9, True, True, "SWEEP", a, mo), m, "inprocess"))
    elif stage == "S4":  # one OS process per actor: trajectory equivalence and footprint
        for s in TEST_SEEDS[:6]:
            for m in ("CENTRAL", "HYBRID"):
                jobs.append((ScenarioKey(s, 8, 0.9, True, True, "N3"), m, "multiprocess"))
    elif stage == "S5":  # violation of the carrier-model assumption (true UA factor fixed)
        for s in TEST_SEEDS[:20]:
            for mis in (1.0, 1.6, 2.0):
                for net in ("N1", "N3"):
                    for m in THERMAL_STRESS_METHODS:
                        jobs.append((ScenarioKey(s, 8, 0.9, False, True, net, None, None, mis), m, "inprocess"))
    else:
        raise ValueError(stage)
    return jobs


_MORNING = {}


def _morning(sc, cfg, cache_dir: Path):
    key = sha(dict(static=sc["static_sha256"], thermal=sc["thermal"], teams=len(sc["teams"])))
    if key in _MORNING:
        return _MORNING[key]
    path = cache_dir / f"morning_{key[:24]}.json"
    if path.exists():
        plan = json.loads(path.read_text())
    else:
        plan = morning_plan(sc, cfg)
        tmp = path.with_suffix(".tmp%d" % os.getpid())
        tmp.write_text(canonical(plan))
        os.replace(tmp, path)
    _MORNING[key] = plan
    return plan


def run_job(args):
    key_dict, method, backend, out_dir, save_log = args
    cfg = load_config()
    out = Path(out_dir)
    key = ScenarioKey(**key_dict)
    sc = ScenarioGenerator(cfg).build(key)
    mp_ = _morning(sc, cfg, out / "morning")
    t0 = time.perf_counter()
    log = BACKENDS[backend](sc, cfg, method, mp_).run()
    wall = time.perf_counter() - t0
    k = reduce(sc, log, cfg)
    run_id = f"{sc['label']}_{method}_{backend}"
    traj = sha(stable_view(log))
    if save_log:
        p = out / "events" / f"{run_id}.jsonl.gz"
        with p.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as f:
                for e in log:
                    f.write((canonical(e) + "\n").encode())
    row = dict(run_id=run_id, seed=key.seed, teams=key.teams, load=key.load, traffic=int(key.traffic),
               thermal=int(key.thermal), network=key.network, availability=sc["network"]["availability"],
               mean_outage=sc["network"]["mean_outage"], ua_misspec=key.ua_misspec, method=method, backend=backend,
               scenario_sha256=sha({kk: vv for kk, vv in sc.items() if kk != "links"}), trajectory_sha256=traj,
               wall_s=round(wall, 3), morning_unassigned=len(mp_["unassigned"]), **k)
    return row


def write_rows(path: Path, rows):
    if not rows:
        return
    keys = list(rows[0].keys())
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--no-logs", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    out = a.output
    (out / "events").mkdir(parents=True, exist_ok=True)
    (out / "morning").mkdir(parents=True, exist_ok=True)
    jobs = stage_jobs(a.stage)
    i, n = (int(x) for x in a.shard.split("/"))
    jobs = [j for idx, j in enumerate(jobs) if idx % n == i]
    if a.limit:
        jobs = jobs[: a.limit]
    done_path = out / f"runs_{a.stage}_{i}of{n}.csv"
    done = {}
    if done_path.exists():
        with done_path.open() as f:
            for row in csv.DictReader(f):
                done[row["run_id"]] = row
    todo = []
    for key, method, backend in jobs:
        rid = f"{key.label()}_{method}_{backend}"
        if rid not in done:
            todo.append((key.__dict__, method, backend, str(out), not a.no_logs))
    rows = list(done.values())
    t0 = time.perf_counter()
    print(f"stage {a.stage} shard {a.shard}: {len(jobs)} jobs, {len(todo)} to run", flush=True)
    ctx = mp.get_context("fork" if sys.platform.startswith("linux") else "spawn")
    # multiprocess-backend jobs spawn their own actors; run them sequentially
    seq = [j for j in todo if j[2] != "inprocess"]
    par = [j for j in todo if j[2] == "inprocess"]
    for j in seq:
        rows.append(run_job(j))
        write_rows(done_path, rows)
    if par:
        with ctx.Pool(a.workers) as pool:
            for c, row in enumerate(pool.imap_unordered(run_job, par, chunksize=1)):
                rows.append(row)
                if (c + 1) % 20 == 0 or c + 1 == len(par):
                    write_rows(done_path, rows)
                    print(f"{c + 1}/{len(par)} done, {time.perf_counter() - t0:.0f}s", flush=True)
    write_rows(done_path, rows)
    env = dict(python=sys.version, platform=platform.platform(), machine=platform.machine(), stage=a.stage,
               shard=a.shard, runs=len(rows), wall_s=round(time.perf_counter() - t0, 1),
               config_sha256=sha(load_config()))
    (out / f"environment_{a.stage}_{i}of{n}.json").write_text(json.dumps(env, indent=2))


if __name__ == "__main__":
    main()
