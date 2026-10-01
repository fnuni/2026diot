"""Documented ambient forcing for the carrier model.

Outdoor air temperature follows the daytime branch of the Parton and Logan
(1981) diurnal model, driven by declared illustrative extrema for Lecce.
Cabin and indoor temperatures are declared set-point bands (assumptions).
"""
from __future__ import annotations

import math


class ClimateDay:
    """Outdoor temperature for one representative day (Parton-Logan daytime branch)."""

    def __init__(self, cfg: dict, day: str):
        c = cfg["climate"]
        d = c["days"][day]
        self.name = day
        self.tmin, self.tmax, self.doy = d["tmin"], d["tmax"], d["doy"]
        self.lat = math.radians(c["latitude_deg"])
        self.lon = c["longitude_deg"]
        self.utc = c["utc_offset_h"]
        self.a, self.c = c["parton_logan_a_h"], c["parton_logan_c_h"]
        self.start_clock = cfg["time"]["shift_start_clock_h"]
        decl = math.radians(23.45) * math.sin(2.0 * math.pi * (284 + self.doy) / 365.0)
        self.day_length_h = 2.0 * math.degrees(math.acos(-math.tan(self.lat) * math.tan(decl))) / 15.0
        self.sunrise_solar_h = 12.0 - self.day_length_h / 2.0

    def solar_hour(self, t_min: float) -> float:
        clock = self.start_clock + t_min / 60.0
        return clock - self.utc + self.lon / 15.0

    def outdoor(self, t_min: float) -> float:
        m = self.solar_hour(t_min) - (self.sunrise_solar_h + self.c)
        y = self.day_length_h
        if m < 0.0:
            return self.tmin
        if m > y:
            m = y
        return self.tmin + (self.tmax - self.tmin) * math.sin(math.pi * m / (y + 2.0 * self.a))


class AmbientModel:
    """Carrier ambient temperature by operational phase."""

    DRIVE, SERVICE, HUB = "drive", "service", "hub"

    def __init__(self, cfg: dict, day: str, horizon: int):
        self.climate = ClimateDay(cfg, day)
        c = cfg["climate"]
        self.cabin_lo, self.cabin_hi = c["cabin_band_c"]
        self.in_base, self.in_gain = c["indoor_base_c"], c["indoor_gain"]
        self.in_lo, self.in_hi = c["indoor_band_c"]
        self.hub_c = c["hub_storage_c"]
        self.margin = c["envelope_margin_c"]
        self.horizon = horizon
        self._out = [self.climate.outdoor(t + 0.5) for t in range(horizon + 1)]

    @staticmethod
    def _clip(x, lo, hi):
        return lo if x < lo else hi if x > hi else x

    def outdoor(self, t: int) -> float:
        return self._out[min(max(t, 0), self.horizon)]

    def cabin(self, t: int) -> float:
        return self._clip(self.outdoor(t), self.cabin_lo, self.cabin_hi)

    def indoor(self, t: int) -> float:
        return self._clip(self.in_base + self.in_gain * (self.outdoor(t) - self.in_base), self.in_lo, self.in_hi)

    def ambient(self, phase: str, t: int) -> float:
        if phase == self.SERVICE:
            return self.indoor(t)
        if phase == self.HUB:
            return self.hub_c
        return self.cabin(t)

    def envelope(self) -> float:
        """Upper ambient envelope over the whole horizon (planning input)."""
        return max(max(self.cabin(t), self.indoor(t)) for t in range(self.horizon + 1)) + self.margin

    def trace(self):
        return dict(day=self.climate.name, tmin=self.climate.tmin, tmax=self.climate.tmax,
                    day_length_h=self.climate.day_length_h,
                    outdoor=[round(x, 4) for x in self._out])
