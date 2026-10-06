"""Independent log reducer: KPIs and safety invariants recomputed from events only."""
from __future__ import annotations

import statistics

STABLE_OMIT = {"seconds", "cpu_s", "decisions", "peak_rss", "backend"}


def stable_view(log):
    out = []
    for e in log:
        out.append({k: v for k, v in e.items() if k not in STABLE_OMIT})
    return out


def _pct(a, b):
    return 100.0 * a / b if b else None


def _q(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))
    return xs[i]


def token_safety(log, n_requests: int):
    """Theorem 3 check: holding intervals of each request never overlap across vehicles,
    and every service start occurs while the serving vehicle holds the token."""
    intervals = {}
    end_t = max(e["t"] for e in log)
    for e in log:
        if e["ev"] == "actor_summary" and "wallet" in e:
            k = int(e["actor"][1:])
            open_ = {}
            for kind, t, r, v in e["wallet"]:
                if kind == "acquire":
                    open_[(r, v)] = t
                elif kind in ("release", "finish") and (r, v) in open_:
                    intervals.setdefault(r, []).append((open_.pop((r, v)), t, k, v))
            for (r, v), t0 in open_.items():
                intervals.setdefault(r, []).append((t0, end_t, k, v))
    overlaps = 0
    for r, iv in intervals.items():
        iv.sort()
        for i in range(len(iv)):
            for j in range(i + 1, len(iv)):
                a, b = iv[i], iv[j]
                if a[2] != b[2] and b[0] < a[1]:
                    overlaps += 1
    unauthorized = 0
    for e in log:
        if e["ev"] == "service_start":
            ok = any(k == e["team"] and t0 <= e["t"] <= t1 and v == e["token"]
                     for t0, t1, k, v in intervals.get(e["r"], []))
            unauthorized += 0 if ok else 1
    return overlaps, unauthorized


def reduce(sc: dict, log: list, cfg: dict) -> dict:
    R = sc["requests"]
    N = len(R)
    shift = sc["shift"]
    limit = shift + sc["max_overtime"]
    starts = {}
    violations = {}
    for e in log:
        if e["ev"] == "service_start":
            if e["r"] in starts:
                violations["double_service"] = violations.get("double_service", 0) + 1
            starts[e["r"]] = e
        elif e["ev"] == "violation":
            violations[e["kind"]] = violations.get(e["kind"], 0) + 1
    for r, e in starts.items():
        req = R[r]
        if not (req["a"] <= e["t"] <= req["b"]):
            violations["window_recheck"] = violations.get("window_recheck", 0) + 1
    served = [r for r, e in starts.items() if e["outcome"] == "served"]
    absent = [r for r, e in starts.items() if e["outcome"] == "absent"]
    handled = set(served) | set(absent)
    prio_tot = sum(r["priority"] for r in R)
    dyn = [r["id"] for r in R if not r["static"]]
    urg = [r["id"] for r in R if r["urgent"]]
    sta = [r["id"] for r in R if r["static"]]
    cold = [r["id"] for r in R if r["cold"]]
    travel = sum(e["dur"] for e in log if e["ev"] == "travel")
    # overtime from final return to hub
    last_hub_arrival = {}
    hub_nodes = {k: N + t["hub"] for k, t in enumerate(sc["teams"])}
    for e in log:
        if e["ev"] == "arrive" and e["node"] == hub_nodes[e["team"]]:
            last_hub_arrival[e["team"]] = e["t"]
    finals = {e["team"]: e for e in log if e["ev"] == "final_state"}
    not_home = sum(1 for e in finals.values() if not e["at_hub"])
    ot = [max(0, last_hub_arrival.get(k, 0) - shift) for k in range(len(sc["teams"]))]
    late_return = sum(1 for k in range(len(sc["teams"])) if last_hub_arrival.get(k, 0) > limit)
    quar = [e for e in log if e["ev"] == "quarantine"]
    cend = [e for e in log if e["ev"] == "carrier_end"]
    swaps = sum(1 for e in log if e["ev"] == "swap_start")
    ns = next(e for e in log if e["ev"] == "network_stats")
    summaries = [e for e in log if e["ev"] == "actor_summary"]
    cen = next(e for e in summaries if e["actor"] == "C")
    veh = [e for e in summaries if e["actor"] != "C"]
    vdec = [x for e in veh for x in e["decisions"]]
    overlaps, unauthorized = token_safety(log, N)
    cdec = [e for e in log if e["ev"] == "center_decision"]
    releases = sum(1 for e in log if e["ev"] == "release")
    cold_admin_bad = sum(1 for e in starts.values() if e["outcome"] == "served" and R[e["r"]]["cold"]
                         and any(q["team"] == e["team"] and q["carrier"] == e["carrier"] and q["t"] <= e["t"] for q in quar))
    out = dict(
        N=N, served=len(served), absent=len(absent), handled=len(handled), missed=N - len(handled),
        handled_pct=_pct(len(handled), N),
        priority_handled_pct=_pct(sum(R[r]["priority"] for r in handled), prio_tot),
        dynamic_handled_pct=_pct(sum(1 for r in dyn if r in handled), len(dyn)),
        urgent_handled_pct=_pct(sum(1 for r in urg if r in handled), len(urg)),
        static_handled_pct=_pct(sum(1 for r in sta if r in handled), len(sta)),
        cold_handled_pct=_pct(sum(1 for r in cold if r in handled), len(cold)),
        travel_min=travel, travel_per_handled=(travel / len(handled)) if handled else None,
        overtime_min_total=sum(ot), overtime_min_max=max(ot) if ot else 0, late_returns=late_return,
        not_home=not_home, swaps=swaps, quarantines=len(quar),
        warm_quarantines=sum(1 for q in quar if q["theta"] > 8.0), cold_quarantines=sum(1 for q in quar if q["theta"] < 2.0),
        wasted_doses=sum(q["wasted"] for q in quar),
        out_of_range_min=sum(e["out_min"] for e in cend), theta_max=max((e["theta_max"] for e in cend if e["field_min"] > 0), default=None),
        theta_min=min((e["theta_min"] for e in cend if e["field_min"] > 0), default=None),
        msgs_sent=ns["sent"], bytes_sent=ns["bytes"], retries=ns["retries"], duplicates=ns["duplicates"],
        max_msg_delay=ns["max_delay"], mean_msg_delay=(ns["delay_sum"] / ns["delivered"]) if ns["delivered"] else 0.0,
        msgs_pending_end=ns["pending"],
        center_cpu_s=cen["cpu_s"], vehicle_cpu_s=sum(e["cpu_s"] for e in veh),
        center_decisions=len(cen["decisions"]), center_p50_ms=1000 * _q(cen["decisions"], 0.5),
        center_p95_ms=1000 * _q(cen["decisions"], 0.95), center_max_ms=1000 * max(cen["decisions"], default=0.0),
        agent_decisions=len(vdec), agent_p95_ms=1000 * _q(vdec, 0.95), agent_max_ms=1000 * max(vdec, default=0.0),
        alns_timeouts=sum(1 for e in cdec if e.get("timed_out")),
        releases=releases,
        token_overlaps=overlaps, unauthorized_services=unauthorized, cold_after_quarantine=cold_admin_bad,
        violations=sum(violations.values()), violation_kinds=",".join(f"{k}:{v}" for k, v in sorted(violations.items())),
    )
    return out
