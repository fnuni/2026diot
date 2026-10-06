"""Scenario generation with load and size controlled independently.

Area scales with the fleet (constant area per team); the number of requests
follows the nominal load rho = N * w / (K * shift). Environmental factors
(traffic incident, thermal day/carrier class) and link schedules are generated
from keyed randomness, so that factor levels are masks over one realization and
link schedules are common to every control method (paired design).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .common import keyed_rng, keyed_uniform, keyed_normal, lognormal_mean_one, sha


@dataclass(frozen=True)
class ScenarioKey:
    seed: int
    teams: int
    load: float
    traffic: bool
    thermal: bool
    network: str
    availability: float | None = None
    mean_outage: float | None = None
    ua_misspec: float | None = None

    def label(self) -> str:
        net = self.network if self.availability is None else f"A{self.availability:.2f}M{self.mean_outage:g}"
        mis = "" if self.ua_misspec is None else f"_U{self.ua_misspec:g}"
        return (f"K{self.teams}_R{self.load:.2f}_T{int(self.traffic)}_H{int(self.thermal)}_{net}{mis}_S{self.seed}")


class ScenarioGenerator:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    # ------------------------------------------------------------------ demand
    def _requests(self, seed: int, teams: int, load: float, side: float, towns):
        g, d = self.cfg["geography"], self.cfg["demand"]
        shift = self.cfg["time"]["shift_min"]
        n = int(round(load * teams * shift / d["mean_workload_per_visit_min"]))
        rng = keyed_rng(seed, teams, load, "requests")
        out = []
        for i in range(n):
            if rng.random() < g["town_share"]:
                cx, cy = towns[rng.randrange(len(towns))]
                x = min(max(cx + rng.gauss(0.0, g["town_sigma_km"]), 0.0), side)
                y = min(max(cy + rng.gauss(0.0, g["town_sigma_km"]), 0.0), side)
            else:
                x, y = rng.uniform(0.0, side), rng.uniform(0.0, side)
            static = rng.random() < d["static_share"]
            cold = rng.random() < d["cold_share"]
            if static:
                release = 0
                a = rng.choice(d["static_slot_starts"])
                b = min(a + d["static_slot_width"], d["latest_start_cap"])
                priority = 2 if rng.random() < d["static_high_priority_share"] else 1
                urgent = False
            else:
                lo, hi = d["dynamic_release_min"]
                release = rng.randint(lo, hi)
                urgent = rng.random() < d["urgent_share_of_dynamic"]
                a = release
                b = min(release + (d["urgent_window"] if urgent else d["routine_dynamic_window"]), d["latest_start_cap"])
                if b <= a:
                    b = a + 30
                priority = 3 if urgent else 2
            skill = 0
            if not cold and rng.random() < d["advanced_share_of_nursing"]:
                skill = 1
            if cold:
                expected = d["vaccination_fixed_min"] + d["vaccination_extra_mean_min"]
                z = rng.gauss(0.0, 1.0)
                true = d["vaccination_fixed_min"] + d["vaccination_extra_mean_min"] * lognormal_mean_one(d["vaccination_extra_sigma_log"], z)
                doses = 2 if rng.random() < d["double_dose_share"] else 1
            else:
                expected = d["nursing_mean_min"]
                sig = math.sqrt(math.log(1.0 + d["nursing_cv"] ** 2))
                true = d["nursing_mean_min"] * lognormal_mean_one(sig, rng.gauss(0.0, 1.0))
                doses = 0
            no_show = rng.random() < d["no_show_prob"]
            out.append(dict(id=i, x=round(x, 4), y=round(y, 4), static=static, release=release, a=a, b=b,
                            cold=cold, doses=doses, skill=skill, priority=priority, urgent=urgent,
                            service_expected=int(round(expected)),
                            service_true=max(5, int(math.ceil(true))), no_show=no_show))
        return out

    # ------------------------------------------------------------------ links
    def link_schedule(self, seed: int, team: int, horizon: int, availability: float, mean_outage: float,
                      global_outage) -> list:
        """Minute-resolution two-state (Gilbert-Elliott type) connectivity process."""
        up = [True] * (horizon + 1)
        if availability < 1.0 and mean_outage > 0.0:
            mean_up = availability * mean_outage / (1.0 - availability)
            p_down = 1.0 / mean_up
            p_up = 1.0 / mean_outage
            state = keyed_uniform(seed, team, "link0") < availability
            for t in range(horizon + 1):
                up[t] = state
                u = keyed_uniform(seed, team, "link", t)
                state = (u >= p_down) if state else (u < p_up)
        if global_outage:
            for t in range(global_outage[0], min(global_outage[1], horizon + 1)):
                up[t] = False
        return up

    # ------------------------------------------------------------------ build
    def build(self, key: ScenarioKey) -> dict:
        cfg = self.cfg
        g = cfg["geography"]
        K = key.teams
        side = math.sqrt(K * g["area_per_team_km2"])
        horizon = cfg["time"]["shift_min"] + cfg["time"]["max_overtime_min"] + cfg["time"]["tail_min"]
        rng = keyed_rng(key.seed, K, "geometry")
        towns = [(rng.uniform(0.1 * side, 0.9 * side), rng.uniform(0.1 * side, 0.9 * side))
                 for _ in range(g["towns_per_team"] * K)]
        requests = self._requests(key.seed, K, key.load, side, towns)
        n_hubs = max(1, K // cfg["geography"]["teams_per_hub"])
        cols = int(math.ceil(math.sqrt(n_hubs)))
        rows = int(math.ceil(n_hubs / cols))
        hubs = []
        for h in range(n_hubs):
            r, c = divmod(h, cols)
            hubs.append(dict(id=h, x=round((c + 0.5) * side / cols, 4), y=round((r + 0.5) * side / rows, 4)))
        lo, hi = cfg["carrier"]["true_ua_factor_range_stress" if key.thermal else "true_ua_factor_range"]
        if key.ua_misspec is not None:
            lo, hi = key.ua_misspec, key.ua_misspec
        teams = [dict(id=k, hub=k % n_hubs, advanced=(k % 2 == 0),
                      ua_factor=round(lo + (hi - lo) * keyed_uniform(key.seed, K, k, "ua"), 6)) for k in range(K)]
        tr = cfg["traffic"]
        incident = None
        if key.traffic:
            cx, cy = towns[int(keyed_uniform(key.seed, K, "incident") * len(towns))]
            incident = dict(x=round(cx, 4), y=round(cy, 4), radius=round(tr["incident_radius_share"] * side, 4),
                            factor=tr["incident_factor"], start=tr["incident_window"][0], end=tr["incident_window"][1])
        thermal = dict(day="JULY" if key.thermal else "APRIL", carrier="NQ_BAG" if key.thermal else "PQS_SR")
        if key.availability is None:
            prof = cfg["network"]["profiles"][key.network]
            availability, mean_outage = prof["availability"], prof["mean_outage_min"]
            p_loss, p_dup, cloud = prof["p_loss"], prof["p_dup"], prof["cloud_outage"]
        else:
            availability, mean_outage = key.availability, key.mean_outage
            base = cfg["network"]["profiles"]["N2"]
            p_loss, p_dup, cloud = base["p_loss"], base["p_dup"], None
        links = [self.link_schedule(key.seed, k, horizon, availability, mean_outage, None) for k in range(K)]
        sc = dict(key=key.__dict__, label=key.label(), seed=key.seed, side_km=round(side, 4), horizon=horizon,
                  shift=cfg["time"]["shift_min"], max_overtime=cfg["time"]["max_overtime_min"],
                  requests=requests, hubs=hubs, teams=teams, towns=[[round(x, 4), round(y, 4)] for x, y in towns],
                  incident=incident, thermal=thermal,
                  network=dict(availability=availability, mean_outage=mean_outage, p_loss=p_loss, p_dup=p_dup,
                               cloud_outage=cloud),
                  links=links)
        sc["static_sha256"] = sha(dict(requests=requests, hubs=hubs, teams=teams, thermal=thermal))
        return sc


class ScenarioModel:
    """Fast read-only view with precomputed geometry."""

    def __init__(self, sc: dict, cfg: dict):
        self.sc = sc
        self.cfg = cfg
        self.R = sc["requests"]
        self.N = len(self.R)
        self.hubs = sc["hubs"]
        self.teams = sc["teams"]
        self.K = len(self.teams)
        self.shift = sc["shift"]
        self.max_ot = sc["max_overtime"]
        self.horizon = sc["horizon"]
        g = cfg["geography"]
        pts = [(r["x"], r["y"]) for r in self.R] + [(h["x"], h["y"]) for h in self.hubs]
        self.n_nodes = len(pts)
        self.pts = pts
        per_km = g["circuity"] / g["speed_km_per_min"]
        over = g["leg_overhead_min"]
        self.base = [[0.0 if i == j else over + per_km * math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
                      for j in range(len(pts))] for i in range(len(pts))]
        inc = sc["incident"]
        self.incident = inc
        if inc:
            self.in_zone = [math.hypot(p[0] - inc["x"], p[1] - inc["y"]) <= inc["radius"] for p in pts]
        else:
            self.in_zone = [False] * len(pts)
        self.noise_sigma = g["travel_noise_sigma_log"]

    def hub_node(self, team: int) -> int:
        return self.N + self.teams[team]["hub"]

    def true_travel(self, team: int, i: int, j: int, t_dep: int) -> int:
        if i == j:
            return 0
        f = 1.0
        inc = self.incident
        if inc and inc["start"] <= t_dep < inc["end"] and (self.in_zone[i] or self.in_zone[j]):
            f = inc["factor"]
        z = keyed_normal(self.sc["seed"], "leg", team, i, j)
        return int(math.ceil(self.base[i][j] * lognormal_mean_one(self.noise_sigma, z) * f))
