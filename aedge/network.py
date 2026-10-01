"""Message-level network emulation in virtual minutes.

Star topology: dispatch centre <-> each vehicle over one cellular link.
Per-minute two-state link process (common to all methods), optional global
backhaul outage, application-level at-least-once delivery (lost transmissions
are retried at the next minute, occasional duplicates), latest-value
coalescing for telemetry. Nothing is dropped silently: every message is either
delivered or still queued at the horizon.
"""
from __future__ import annotations

from .common import keyed_uniform

CENTER = "C"


def vehicle_id(k: int) -> str:
    return f"V{k}"


class Link:
    def __init__(self, team: int, up: list):
        self.team = team
        self.up = up

    def is_up(self, t: int) -> bool:
        return self.up[t] if t < len(self.up) else self.up[-1]


class Network:
    def __init__(self, sc: dict, center_host: str = "cloud"):
        self.seed = sc["seed"]
        net = sc["network"]
        self.center_host = center_host
        self.cloud_outage = net.get("cloud_outage")
        self.p_loss = net["p_loss"]
        self.p_dup = net["p_dup"]
        self.links = [Link(k, up) for k, up in enumerate(sc["links"])]
        self.queues = {}  # (src, dst) -> list of entries
        self.stats = dict(sent=0, bytes=0, delivered=0, retries=0, duplicates=0, coalesced=0,
                          max_delay=0, delay_sum=0)
        self.counter = 0

    def _team(self, src: str, dst: str) -> int:
        v = src if src != CENTER else dst
        return int(v[1:])

    def send(self, t: int, src: str, dst: str, payload: bytes, coalesce: str | None = None):
        q = self.queues.setdefault((src, dst), [])
        if coalesce is not None:
            before = len(q)
            q[:] = [e for e in q if e["coalesce"] != coalesce]
            self.stats["coalesced"] += before - len(q)
        self.counter += 1
        q.append(dict(payload=payload, t_sent=t, ready=t, attempt=0, mid=self.counter, coalesce=coalesce, dup=False))
        self.stats["sent"] += 1
        self.stats["bytes"] += len(payload)

    def deliver(self, t: int, direction: str) -> dict:
        """direction 'up' (vehicle->centre) or 'down' (centre->vehicle); returns dst -> [payload]."""
        out = {}
        for (src, dst), q in sorted(self.queues.items()):
            if (direction == "up") != (dst == CENTER):
                continue
            k = self._team(src, dst)
            if not self.links[k].is_up(t) or not self.center_reachable(t):
                continue
            keep = []
            for e in q:
                if e["ready"] > t:
                    keep.append(e)
                    continue
                if keyed_uniform(self.seed, "loss", src, dst, e["mid"], e["attempt"]) < self.p_loss:
                    e["attempt"] += 1
                    e["ready"] = t + 1
                    self.stats["retries"] += 1
                    keep.append(e)
                    continue
                out.setdefault(dst, []).append(e["payload"])
                self.stats["delivered"] += 1
                delay = t - e["t_sent"]
                self.stats["delay_sum"] += delay
                self.stats["max_delay"] = max(self.stats["max_delay"], delay)
                if not e["dup"] and keyed_uniform(self.seed, "dup", src, dst, e["mid"]) < self.p_dup:
                    d = dict(e)
                    d["dup"] = True
                    d["ready"] = t + 1
                    keep.append(d)
                    self.stats["duplicates"] += 1
            self.queues[(src, dst)] = keep
        return out

    def center_reachable(self, t: int) -> bool:
        """A cloud-hosted centre is unreachable during a cloud/backhaul outage; a fog node is not."""
        if self.center_host != "cloud" or not self.cloud_outage:
            return True
        return not (self.cloud_outage[0] <= t < self.cloud_outage[1])

    def pending(self) -> int:
        return sum(len(q) for q in self.queues.values())
