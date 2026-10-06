"""Actor-side public knowledge: map, revealed requests and believed travel matrices.

Actors never receive realised service times, no-show flags, link schedules,
true carrier parameters, the incident, or unreleased requests. Dynamic
requests are placeholders until a message reveals them.
"""
from __future__ import annotations

import math

from .scenario import ScenarioModel

PRIVATE_FIELDS = ("service_true", "no_show")


def public_scenario(sc: dict) -> dict:
    pub = {k: v for k, v in sc.items() if k not in ("links", "incident", "requests", "teams", "network")}
    pub["incident"] = None
    pub["links"] = []
    pub["teams"] = [{k: v for k, v in t.items() if k != "ua_factor"} for t in sc["teams"]]
    reqs = []
    for r in sc["requests"]:
        if r["static"]:
            reqs.append({k: v for k, v in r.items() if k not in PRIVATE_FIELDS})
        else:
            reqs.append(dict(id=r["id"], x=0.0, y=0.0, static=False, release=None, a=0, b=-1, cold=False,
                             doses=0, skill=0, priority=0, urgent=False, service_expected=0, placeholder=True))
    pub["requests"] = reqs
    return pub


def request_public(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in PRIVATE_FIELDS and k != "placeholder"}


class PublicView(ScenarioModel):
    def __init__(self, sc_public: dict, cfg: dict):
        super().__init__(sc_public, cfg)
        g = cfg["geography"]
        self.per_km = g["circuity"] / g["speed_km_per_min"]
        self.over = g["leg_overhead_min"]
        n = self.n_nodes
        self.exp0 = [[int(math.ceil(self.base[i][j])) if i != j else 0 for j in range(n)] for i in range(n)]
        self.exp1 = [row[:] for row in self.exp0]
        self.incident_desc = None
        self.known = {r["id"] for r in self.R if not r.get("placeholder")}

    def reveal(self, req: dict):
        i = req["id"]
        if i in self.known:
            return
        self.R[i] = dict(req)
        self.known.add(i)
        self.pts[i] = (req["x"], req["y"])
        n = self.n_nodes
        for j in range(n):
            d = 0.0 if i == j else self.over + self.per_km * math.hypot(self.pts[i][0] - self.pts[j][0],
                                                                         self.pts[i][1] - self.pts[j][1])
            self.base[i][j] = d
            self.base[j][i] = d
            e = int(math.ceil(d)) if i != j else 0
            self.exp0[i][j] = e
            self.exp0[j][i] = e
        self._refresh_zone_node(i)

    def _in_zone(self, p):
        inc = self.incident_desc
        return inc is not None and math.hypot(p[0] - inc["x"], p[1] - inc["y"]) <= inc["radius"]

    def _refresh_zone_node(self, i):
        n = self.n_nodes
        self.in_zone[i] = self._in_zone(self.pts[i])
        f = self.incident_desc["factor"] if self.incident_desc else 1.0
        for j in range(n):
            if i == j:
                continue
            z = self.in_zone[i] or self.in_zone[j]
            e = int(math.ceil(self.base[i][j] * f)) if z else self.exp0[i][j]
            self.exp1[i][j] = e
            self.exp1[j][i] = e

    def set_incident(self, desc: dict):
        if self.incident_desc == desc:
            return
        self.incident_desc = dict(desc)
        n = self.n_nodes
        self.in_zone = [self._in_zone(self.pts[i]) for i in range(n)]
        f = desc["factor"]
        for i in range(n):
            for j in range(n):
                z = i != j and (self.in_zone[i] or self.in_zone[j])
                self.exp1[i][j] = int(math.ceil(self.base[i][j] * f)) if z else self.exp0[i][j]

    def matrix(self, incident_active: bool):
        return self.exp1 if (incident_active and self.incident_desc) else self.exp0
