"""Re-execute a deterministic sample of delivered runs and compare trajectory hashes.

Usage: python -m aedge.check_repeat campaigns/screening/results [--every 32] [--stages S1,S2,S3,S5]
"""
from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
import time
from pathlib import Path

from .experiment import run_job, stage_jobs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("results", type=Path)
    ap.add_argument("--every", type=int, default=32)
    ap.add_argument("--stages", default="S1,S2,S3,S5")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--scratch", type=Path, default=Path("repeat_scratch"))
    a = ap.parse_args(argv)
    (a.scratch / "morning").mkdir(parents=True, exist_ok=True)
    (a.scratch / "events").mkdir(parents=True, exist_ok=True)
    ref = {}
    for p in a.results.glob("runs_*.csv"):
        with p.open() as fh:
            for r in csv.DictReader(fh):
                ref[r["run_id"]] = r["trajectory_sha256"]
    checked, equal, diffs = 0, 0, []
    t0 = time.perf_counter()
    for st in a.stages.split(","):
        for idx, (key, method, backend) in enumerate(stage_jobs(st)):
            if idx % a.every:
                continue
            row = run_job((key.__dict__, method, backend, str(a.scratch), False))
            checked += 1
            if ref.get(row["run_id"]) == row["trajectory_sha256"]:
                equal += 1
            else:
                diffs.append(row["run_id"])
    res = dict(platform=platform.platform(), python=sys.version.split()[0], checked=checked, equal=equal,
               different=diffs, seconds=round(time.perf_counter() - t0, 1))
    print(json.dumps(res, indent=1))
    if a.out:
        a.out.write_text(json.dumps(res, indent=1))
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
