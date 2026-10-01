"""Physical ground truth: vehicle activities, realised travel/service times, carrier physics.

The world never plans; it executes commands issued by vehicle actors, enforces
nothing beyond physics, and records every attempted action so that safety
violations (double service, service outside windows, cold administration from
a quarantined carrier, service without token) are detected by the reducer.
"""
from __future__ import annotations

from .climate import AmbientModel
from .thermal import CarrierPhysics


class VehicleBody:
    def __init__(self, team: int, hub_node: int):
        self.team = team
        self.node = hub_node
        self.phase = "idle"      # idle | travel | wait | service | swap
        self.until = 0
        self.target = None
        self.leg = None
        self.serving = None
        self.lid_pending = False
        self.doses = 0
        self.quarantined = False
        self.carrier_id = 0
        self.events = []


class World:
    def __init__(self, model, cfg: dict, carrier_cls, log):
        self.m = model
        self.cfg = cfg
        self.log = log
        th = model.sc["thermal"]
        self.ambient = AmbientModel(cfg, th["day"], model.horizon)
        cc = cfg["carrier"]
        self.cap = cc["capacity_doses"]
        self.swap_min = cc["swap_min"]
        self.lo, self.hi = cc["theta_low_c"], cc["theta_high_c"]
        self.theta0 = cc["theta_init_c"]
        self.h0 = cc["initial_melt_fraction"] * carrier_cls.L
        self.lid_s = cc["lid_open_s"]
        self.no_show_min = cfg["demand"]["no_show_min"]
        self.bodies = []
        self.carriers = []
        for k, tm in enumerate(model.teams):
            b = VehicleBody(k, model.hub_node(k))
            b.doses = self.cap
            self.bodies.append(b)
            self.carriers.append(CarrierPhysics(carrier_cls.scaled(tm["ua_factor"]), self.theta0, self.h0))
        self.outcome = {}   # r -> (outcome, team, t)
        self.stats = [dict(max=-1e9, min=1e9, out_min=0, field_min=0) for _ in range(model.K)]
        for k in range(model.K):
            log.append(dict(ev="carrier_new", t=0, team=k, carrier=0, doses=self.cap))

    # ------------------------------------------------------------ observation
    def observe(self, t: int, k: int) -> dict:
        b = self.bodies[k]
        ev = b.events
        b.events = []
        return dict(t=t, phase=b.phase, node=b.node, until=b.until, target=b.target,
                    theta=round(self.carriers[k].theta, 5), doses=b.doses, quarantined=b.quarantined, events=ev)

    # ------------------------------------------------------------ commands
    def command(self, t: int, k: int, action):
        if action is None:
            return
        b = self.bodies[k]
        kind = action[0]
        if b.phase != "idle":
            self.log.append(dict(ev="violation", t=t, team=k, kind="busy_command", action=list(action)))
            return
        R = self.m.R
        if kind == "goto":
            dest = action[1]
            if dest == b.node:
                return
            dur = self.m.true_travel(k, b.node, dest, t)
            b.leg = (b.node, dest, t, dur)
            b.phase, b.until, b.target = "travel", t + dur, dest
            self.log.append(dict(ev="travel", t=t, team=k, frm=b.node, to=dest, dur=dur))
        elif kind == "wait":
            until = action[1]
            if until > t:
                b.phase, b.until = "wait", until
                self.log.append(dict(ev="wait", t=t, team=k, node=b.node, until=until))
        elif kind == "serve":
            r = action[1]
            req = R[r]
            token = action[2] if len(action) > 2 else None
            ok = b.node == r and req["a"] <= t <= req["b"]
            prior = self.outcome.get(r)
            if prior is not None:
                self.log.append(dict(ev="violation", t=t, team=k, kind="double_service", r=r, prior=list(prior)))
            if not ok:
                self.log.append(dict(ev="violation", t=t, team=k, kind="window_or_position", r=r))
            if req["skill"] == 1 and not self.m.teams[k]["advanced"]:
                self.log.append(dict(ev="violation", t=t, team=k, kind="skill", r=r))
            if req["no_show"]:
                dur = self.no_show_min
                outcome = "absent"
            else:
                dur = req["service_true"]
                outcome = "served"
                if req["cold"]:
                    if b.quarantined or b.doses < req["doses"]:
                        self.log.append(dict(ev="violation", t=t, team=k, kind="cold_unavailable", r=r))
                    b.doses -= req["doses"]
                    b.lid_pending = True
            self.outcome[r] = (outcome, k, t)
            b.phase, b.until, b.serving = "service", t + dur, (r, outcome)
            self.log.append(dict(ev="service_start", t=t, team=k, r=r, outcome=outcome, dur=dur, token=token,
                                 carrier=b.carrier_id, theta=round(self.carriers[k].theta, 5)))
        elif kind == "swap":
            if b.node != self.m.hub_node(k):
                self.log.append(dict(ev="violation", t=t, team=k, kind="swap_off_hub"))
                return
            b.phase, b.until = "swap", t + self.swap_min
            self.log.append(dict(ev="swap_start", t=t, team=k, carrier=b.carrier_id, doses_returned=b.doses,
                                 quarantined=b.quarantined, latent=round(self.carriers[k].latent_remaining_j(), 3),
                                 theta=round(self.carriers[k].theta, 5)))

    # ------------------------------------------------------------ physics
    def _phase_ambient(self, b, t):
        hub = self.m.hub_node(b.team)
        if b.phase == "service":
            return self.ambient.ambient("service", t)
        if b.node == hub and b.phase in ("idle", "swap"):
            return self.ambient.ambient("hub", t)
        return self.ambient.ambient("drive", t)

    def advance(self, t: int):
        for k, b in enumerate(self.bodies):
            car = self.carriers[k]
            at_hub = b.node == self.m.hub_node(k) and b.phase in ("idle", "swap")
            if not at_hub:
                lid = self.lid_s if b.lid_pending else 0.0
                car.step_minute(self._phase_ambient(b, t), lid)
                b.lid_pending = False
                st = self.stats[k]
                st["max"] = max(st["max"], car.theta)
                st["min"] = min(st["min"], car.theta)
                st["field_min"] += 1
                if car.theta > self.hi or car.theta < self.lo:
                    st["out_min"] += 1
                if not b.quarantined and (car.theta > self.hi or car.theta < self.lo):
                    b.quarantined = True
                    self.log.append(dict(ev="quarantine", t=t + 1, team=k, carrier=b.carrier_id,
                                         theta=round(car.theta, 5), wasted=b.doses))
                if (t + 1) % 5 == 0:
                    self.log.append(dict(ev="theta", t=t + 1, team=k, carrier=b.carrier_id, theta=round(car.theta, 4),
                                         latent=round(car.latent_remaining_j(), 1)))
            tn = t + 1
            if b.phase != "idle" and b.until <= tn:
                if b.phase == "travel":
                    frm, to, t_dep, dur = b.leg
                    b.node = to
                    inc = self.m.incident
                    seen = None
                    if inc and (self.m.in_zone[frm] or self.m.in_zone[to]):
                        if inc["start"] <= t_dep < inc["end"]:
                            seen = "active"
                        elif t_dep >= inc["end"]:
                            seen = "clear"
                    desc = None
                    if seen:
                        desc = dict(x=inc["x"], y=inc["y"], radius=inc["radius"], factor=inc["factor"])
                    b.events.append(("arrive", to, frm, t_dep, dur, seen, desc))
                    self.log.append(dict(ev="arrive", t=tn, team=k, node=to))
                elif b.phase == "service":
                    r, outcome = b.serving
                    b.events.append(("service_end", r, outcome))
                    self.log.append(dict(ev="service_end", t=tn, team=k, r=r, outcome=outcome))
                    b.serving = None
                elif b.phase == "swap":
                    self._close_carrier(k, tn)
                    b.carrier_id += 1
                    b.doses = self.cap
                    b.quarantined = False
                    self.carriers[k] = CarrierPhysics(car.cls, self.theta0, self.h0)
                    b.events.append(("swap_end",))
                    self.log.append(dict(ev="carrier_new", t=tn, team=k, carrier=b.carrier_id, doses=self.cap))
                elif b.phase == "wait":
                    b.events.append(("wait_end",))
                b.phase = "idle"
                b.target = None

    def _close_carrier(self, k: int, t: int):
        st = self.stats[k]
        car = self.carriers[k]
        b = self.bodies[k]
        self.log.append(dict(ev="carrier_end", t=t, team=k, carrier=b.carrier_id, theta_max=round(st["max"], 4),
                             theta_min=round(st["min"], 4), out_min=st["out_min"], field_min=st["field_min"],
                             latent_left=round(car.latent_remaining_j(), 1), quarantined=b.quarantined,
                             doses_left=b.doses))
        self.stats[k] = dict(max=-1e9, min=1e9, out_min=0, field_min=0)

    def finish(self, t: int):
        for k in range(self.m.K):
            self._close_carrier(k, t)
            b = self.bodies[k]
            self.log.append(dict(ev="final_state", t=t, team=k, node=b.node, phase=b.phase,
                                 at_hub=b.node == self.m.hub_node(k)))
