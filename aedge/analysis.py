"""Screening-phase analysis: integrity checks, tables, tests and LaTeX output.

Unit of inference: the test seed. For S1, each seed contributes the mean of the paired
method difference over the four environment cells (traffic x thermal), unless a cell is named.
"""
from __future__ import annotations

import csv
import glob
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from .common import load_config

CFG = load_config()
ST = CFG["statistics"]
LABEL = {"STATIC": "Static", "PERIODIC": "Periodic", "CENTRAL": "Central", "CENTRAL_FB": "Central+FB",
         "CENTRAL_FOG": "Central@fog", "HYBRID": "Hybrid", "HYBRID_EAGER": "Hybrid-eager",
         "EDGE_MARKET": "Edge-market"}
ORDER = ["STATIC", "PERIODIC", "CENTRAL", "CENTRAL_FB", "CENTRAL_FOG", "HYBRID", "HYBRID_EAGER", "EDGE_MARKET"]
NETS = ["N0", "N1", "N2", "N3", "N4"]
PRIMARY = "priority_handled_pct"

CONFIRMATORY = [  # (id, metric, network, a, b, description) superiority, two-sided, Holm
    ("H1", PRIMARY, "N4", "HYBRID", "CENTRAL", "cloud outage: fog-coordinated hybrid vs cloud central"),
    ("H2", PRIMARY, "N3", "HYBRID", "CENTRAL", "severe coverage gaps: bounded autonomy vs thin execution"),
    ("H3", PRIMARY, "N1", "CENTRAL", "PERIODIC", "update timing: event-driven vs 15-min periodic"),
    ("H4", PRIMARY, "N1", "HYBRID", "EDGE_MARKET", "global optimisation vs pure market coordination"),
]
EQUIVALENCE = [  # TOST via 90% bootstrap interval within +/- margin
    ("Q1", PRIMARY, "N1", "HYBRID", "CENTRAL", "no penalty under good connectivity"),
    ("Q2", PRIMARY, "N4", "HYBRID", "CENTRAL_FOG", "placement attribution under cloud outage"),
]


def load(out: Path, stage: str):
    rows = []
    for f in sorted(glob.glob(str(out / f"runs_{stage}_*.csv"))):
        with open(f) as fh:
            rows += list(csv.DictReader(fh))
    for r in rows:
        for k, v in list(r.items()):
            try:
                r[k] = float(v)
            except (TypeError, ValueError):
                pass
    return rows


def boot_ci(d, level=0.95, reps=ST["bootstrap_replicates"], seed=ST["bootstrap_seed"]):
    d = np.asarray(d, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(reps, len(d)))
    means = d[idx].mean(axis=1)
    a = (1 - level) / 2
    return float(np.quantile(means, a)), float(np.quantile(means, 1 - a))


def signflip_p(d, reps=ST["signflip_replicates"], seed=ST["signflip_seed"]):
    d = np.asarray(d, float)
    obs = abs(d.mean())
    rng = np.random.default_rng(seed)
    signs = rng.choice([-1.0, 1.0], size=(reps, len(d)))
    stat = np.abs((signs * d).mean(axis=1))
    return float((1 + np.sum(stat >= obs - 1e-12)) / (reps + 1))


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj = [0.0] * len(ps)
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = run
    return adj


def index(rows):
    d = {}
    for r in rows:
        d[(r["network"], r["method"], int(r["seed"]), int(r["traffic"]), int(r["thermal"]))] = r
    return d


def paired(d, metric, net, a, b, cells=None):
    """Per-seed mean over environment cells of (a - b)."""
    per = defaultdict(list)
    for (n, m, s, tr, th), r in d.items():
        if n != net or m != a:
            continue
        if cells is not None and (tr, th) not in cells:
            continue
        o = d.get((n, b, s, tr, th))
        if o is None or r[metric] in ("", None) or o[metric] in ("", None):
            continue
        per[s].append(r[metric] - o[metric])
    seeds = sorted(per)
    return np.array([np.mean(per[s]) for s in seeds]), seeds


def mean_sd(vals):
    vals = [v for v in vals if v not in ("", None) and not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return float("nan"), float("nan")
    return float(np.mean(vals)), float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0


def integrity(rows):
    keys = ["violations", "token_overlaps", "unauthorized_services", "cold_after_quarantine", "not_home",
            "late_returns", "alns_timeouts"]
    return {k: int(sum(r[k] for r in rows)) for k in keys} | dict(runs=len(rows),
            pending_messages_at_end=int(sum(r["msgs_pending_end"] for r in rows)))


def fmt(x, nd=2):
    return "--" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def analyse(out: Path, tex_dir: Path):
    tex_dir.mkdir(parents=True, exist_ok=True)
    res = {}
    # ------------------------------------------------------------------ S1
    e1 = load(out, "S1")
    d1 = index(e1)
    res["S1_integrity"] = integrity(e1)
    per = defaultdict(list)
    for r in e1:
        per[(r["network"], r["method"])].append(r)
    table = {}
    for (n, m), rs in per.items():
        table[(n, m)] = {k: mean_sd([x[k] for x in rs]) for k in
                         [PRIMARY, "handled_pct", "urgent_handled_pct", "dynamic_handled_pct", "travel_min",
                          "overtime_min_total", "quarantines", "wasted_doses", "swaps", "msgs_sent", "bytes_sent",
                          "center_p95_ms", "agent_p95_ms", "releases", "cold_handled_pct"]}
    res["S1_table"] = {f"{n}|{m}": {k: v for k, v in t.items()} for (n, m), t in table.items()}
    tests = []
    for hid, metric, net, a, b, desc in CONFIRMATORY:
        dd, seeds = paired(d1, metric, net, a, b)
        lo, hi = boot_ci(dd)
        tests.append(dict(id=hid, net=net, a=a, b=b, desc=desc, n=len(dd), mean=float(dd.mean()),
                          sd=float(dd.std(ddof=1)), ci_lo=lo, ci_hi=hi, p=signflip_p(dd)))
    for t, p in zip(tests, holm([t["p"] for t in tests])):
        t["p_holm"] = p
    res["confirmatory"] = tests
    eq = []
    for qid, metric, net, a, b, desc in EQUIVALENCE:
        dd, seeds = paired(d1, metric, net, a, b)
        lo, hi = boot_ci(dd, level=0.90)
        m_ = ST["equivalence_margin_pp"]
        eq.append(dict(id=qid, net=net, a=a, b=b, desc=desc, n=len(dd), mean=float(dd.mean()), ci90_lo=lo,
                       ci90_hi=hi, margin=m_, equivalent=bool(lo > -m_ and hi < m_)))
    res["equivalence"] = eq
    # all pairwise vs CENTRAL by network (exploratory)
    expl = []
    for net in NETS:
        for m in ORDER:
            if m == "CENTRAL":
                continue
            for metric in (PRIMARY, "urgent_handled_pct", "travel_min"):
                dd, _ = paired(d1, metric, net, m, "CENTRAL")
                if len(dd) == 0:
                    continue
                lo, hi = boot_ci(dd)
                expl.append(dict(net=net, method=m, metric=metric, mean=float(dd.mean()), ci_lo=lo, ci_hi=hi))
    res["S1_vs_central"] = expl
    # factorial effects (per method, pooled over networks): traffic, thermal, interaction on PRIMARY
    fac = []
    for m in ORDER:
        for metric in (PRIMARY, "travel_min", "swaps"):
            eff = defaultdict(list)
            for net in NETS:
                for s in sorted({k[2] for k in d1}):
                    try:
                        y = {(tr, th): d1[(net, m, s, tr, th)][metric] for tr in (0, 1) for th in (0, 1)}
                    except KeyError:
                        continue
                    eff[s].append(((y[(1, 0)] + y[(1, 1)] - y[(0, 0)] - y[(0, 1)]) / 2,
                                   (y[(0, 1)] + y[(1, 1)] - y[(0, 0)] - y[(1, 0)]) / 2,
                                   (y[(1, 1)] - y[(1, 0)] - y[(0, 1)] + y[(0, 0)])))
            arr = np.array([np.mean(v, axis=0) for s, v in sorted(eff.items())])
            if len(arr) == 0:
                continue
            row = dict(method=m, metric=metric)
            for j, name in enumerate(("traffic", "thermal", "interaction")):
                lo, hi = boot_ci(arr[:, j])
                row[name] = float(arr[:, j].mean())
                row[name + "_ci"] = [lo, hi]
            fac.append(row)
    res["factorial"] = fac
    # ------------------------------------------------------------------ S2
    e2 = load(out, "S2")
    if e2:
        res["S2_integrity"] = integrity(e2)
        g = defaultdict(list)
        for r in e2:
            g[(int(r["teams"]), r["load"], r["network"], r["method"])].append(r)
        res["S2_table"] = {f"{k[0]}|{k[1]}|{k[2]}|{k[3]}": dict(
            pwc=mean_sd([x[PRIMARY] for x in v]), handled=mean_sd([x["handled_pct"] for x in v]),
            urgent=mean_sd([x["urgent_handled_pct"] for x in v]), center_p95=mean_sd([x["center_p95_ms"] for x in v]),
            center_max=mean_sd([x["center_max_ms"] for x in v]), wall=mean_sd([x["wall_s"] for x in v]),
            N=mean_sd([x["N"] for x in v])) for k, v in g.items()}
        # paired Hybrid - Central per (teams, load, net)
        pe = []
        for teams, load_ in ((8, 0.75), (8, 1.05), (4, 0.9), (16, 0.9)):
            for net in ("N1", "N3"):
                for m in ("CENTRAL_FB", "HYBRID", "EDGE_MARKET", "PERIODIC"):
                    a = {int(r["seed"]): r[PRIMARY] for r in e2 if int(r["teams"]) == teams and r["load"] == load_
                         and r["network"] == net and r["method"] == m}
                    b = {int(r["seed"]): r[PRIMARY] for r in e2 if int(r["teams"]) == teams and r["load"] == load_
                         and r["network"] == net and r["method"] == "CENTRAL"}
                    dd = np.array([a[s] - b[s] for s in sorted(a) if s in b])
                    if len(dd):
                        lo, hi = boot_ci(dd)
                        pe.append(dict(teams=teams, load=load_, net=net, method=m, mean=float(dd.mean()), ci_lo=lo,
                                       ci_hi=hi))
        res["S2_paired"] = pe
    # ------------------------------------------------------------------ S3
    e3 = load(out, "S3")
    if e3:
        res["S3_integrity"] = integrity(e3)
        mp_ = []
        for a in (0.95, 0.85, 0.75, 0.65, 0.55):
            for mo in (5.0, 15.0, 30.0):
                for m in ("CENTRAL_FB", "HYBRID", "HYBRID_EAGER", "EDGE_MARKET"):
                    x = {int(r["seed"]): r[PRIMARY] for r in e3 if r["availability"] == a and r["mean_outage"] == mo
                         and r["method"] == m}
                    y = {int(r["seed"]): r[PRIMARY] for r in e3 if r["availability"] == a and r["mean_outage"] == mo
                         and r["method"] == "CENTRAL"}
                    dd = np.array([x[s] - y[s] for s in sorted(x) if s in y])
                    lo, hi = boot_ci(dd)
                    mp_.append(dict(availability=a, mean_outage=mo, method=m, mean=float(dd.mean()), ci_lo=lo,
                                    ci_hi=hi, central=float(np.mean(list(y.values())))))
        res["S3_map"] = mp_
    # ------------------------------------------------------------------ S4
    e4 = load(out, "S4")
    if e4:
        match = []
        for r in e4:
            key = ("N3", r["method"], int(r["seed"]), 1, 1)
            ref = d1.get(key)
            match.append(dict(seed=int(r["seed"]), method=r["method"],
                              equal=bool(ref is not None and ref["trajectory_sha256"] == r["trajectory_sha256"]),
                              wall_mp=r["wall_s"], wall_in=ref["wall_s"] if ref else None,
                              center_cpu=r["center_cpu_s"], vehicle_cpu=r["vehicle_cpu_s"],
                              bytes=r["bytes_sent"], msgs=r["msgs_sent"]))
        res["S4"] = match
    # ------------------------------------------------------------------ S5
    e5 = load(out, "S5")
    if e5:
        res["S5_integrity"] = integrity(e5)
        g = defaultdict(list)
        for r in e5:
            g[(r["ua_misspec"], r["network"], r["method"])].append(r)
        res["S5_table"] = {f"{k[0]}|{k[1]}|{k[2]}": dict(
            pwc=mean_sd([x[PRIMARY] for x in v]), cold=mean_sd([x["cold_handled_pct"] for x in v]),
            quarantines=mean_sd([x["quarantines"] for x in v]), wasted=mean_sd([x["wasted_doses"] for x in v]),
            out_min=mean_sd([x["out_of_range_min"] for x in v]), swaps=mean_sd([x["swaps"] for x in v]))
            for k, v in g.items()}
    (tex_dir / "analysis.json").write_text(json.dumps(res, indent=1, default=float))
    return res


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "campaigns/screening/results"
    analyse(out, out / "analysis")
