"""Two-node physical carrier model, calibration to PQS cold life, planning bound, observer.

State: compartment temperature theta (degC) and coolant specific enthalpy h (J/kg),
h=0 at fully frozen 0 degC, h=L at fully melted 0 degC.

    C_c dtheta/dt = G(t) (theta_a - theta) - UA_p (theta - theta_p(h))
    m_p dh/dt     = UA_p (theta - theta_p(h))

with G(t) = UA_w + UA_open during lid opening. UA_p = ratio * UA_w.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CarrierClass:
    name: str
    C_c: float
    m_p: float
    ua_w: float
    ua_p: float
    ua_open: float
    L: float
    c_ice: float
    c_water: float
    cold_life_43_h: float

    @property
    def tau_closed_s(self) -> float:
        return self.C_c / (self.ua_w + self.ua_p)

    def scaled(self, factor: float) -> "CarrierClass":
        return CarrierClass(self.name, self.C_c, self.m_p, self.ua_w * factor, self.ua_p * factor,
                            self.ua_open, self.L, self.c_ice, self.c_water, self.cold_life_43_h)


class CarrierPhysics:
    """True carrier dynamics (explicit Euler, 15-s sub-steps; stable since tau >> 15 s)."""

    SUBSTEP_S = 15.0

    def __init__(self, cls: CarrierClass, theta0: float, h0: float):
        self.cls = cls
        self.theta = theta0
        self.h = h0

    def pack_temperature(self) -> float:
        c = self.cls
        if self.h < 0.0:
            return self.h / c.c_ice
        if self.h <= c.L:
            return 0.0
        return (self.h - c.L) / c.c_water

    def latent_remaining_j(self) -> float:
        c = self.cls
        return max(0.0, min(c.L, c.L - self.h)) * c.m_p

    def step_minute(self, theta_a: float, lid_open_s: float = 0.0) -> None:
        c = self.cls
        n = int(60.0 / self.SUBSTEP_S)
        for s in range(n):
            open_now = (s * self.SUBSTEP_S) < lid_open_s
            g = c.ua_w + (c.ua_open if open_now else 0.0)
            tp = self.pack_temperature()
            q_in = g * (theta_a - self.theta)
            q_p = c.ua_p * (self.theta - tp)
            self.theta += self.SUBSTEP_S * (q_in - q_p) / c.C_c
            self.h += self.SUBSTEP_S * q_p / c.m_p


def cold_life_hours(cls: CarrierClass, ambient: float = 43.0, threshold: float = 10.0,
                    max_h: float = 60.0) -> float:
    """PQS-type test: container at ambient, conditioned coolant (h=0) loaded, lid closed;
    time until the compartment, after first cooling below threshold, reaches threshold."""
    phys = CarrierPhysics(cls, ambient, 0.0)
    cooled = False
    for minute in range(int(max_h * 60)):
        phys.step_minute(ambient)
        if phys.theta < threshold:
            cooled = True
        elif cooled and phys.theta >= threshold:
            return (minute + 1) / 60.0
    return float("inf")


def calibrate_class(cfg: dict, name: str) -> CarrierClass:
    """Fit ``UA_w`` to the declared +43 degC cold-life target.

    Calibration is accepted only when the target is bracketed. This avoids the
    failure mode in which a physically incompatible conductance ratio silently
    returned the upper bisection bound.
    """
    cc = cfg["carrier"]
    spec = cc["classes"][name]
    ratio = cc["ua_pack_to_wall_ratio"]

    def make(ua_w):
        return CarrierClass(name, spec["C_c"], spec["m_p"], ua_w, ratio * ua_w, cc["ua_open_w_per_k"],
                            cc["latent_j_per_kg"], cc["c_ice"], cc["c_water"], spec["cold_life_43_h"])

    lo, hi = 0.01, 20.0
    target = spec["cold_life_43_h"]
    life_lo = cold_life_hours(make(lo))
    life_hi = cold_life_hours(make(hi))
    if not (life_lo >= target >= life_hi):
        raise ValueError(
            f"cannot calibrate carrier {name!r}: target {target:g} h is not "
            f"bracketed by UA_w in [{lo:g}, {hi:g}] W/K "
            f"(cold lives {life_lo:g} h and {life_hi:g} h); check "
            "ua_pack_to_wall_ratio and the cold-life target"
        )
    for _ in range(60):
        mid = math.sqrt(lo * hi)
        life = cold_life_hours(make(mid))
        if life > target:
            lo = mid
        else:
            hi = mid
    return make(round(math.sqrt(lo * hi), 6))


class ThermalBound:
    """Conditional linear planning bound (plateau regime; Theorem 1).

    rate_w  : upper bound on coolant heat absorption with closed lid (W)
    impulse : upper bound on the heat admitted by one lid opening (J)
    theta_ub: upper bound on compartment temperature while latent heat remains
    """

    def __init__(self, cls: CarrierClass, cfg: dict, envelope_c: float):
        cc = cfg["carrier"]
        s_hi = cc["planning_ua_factor"]
        f_lo = cc["true_ua_factor_range"][0]
        self.cls = cls
        self.envelope = envelope_c
        ratio = cls.ua_w / (cls.ua_w + cls.ua_p)
        self.ratio = ratio
        self.theta_eq = max(cc["theta_init_c"], ratio * envelope_c)
        self.rate_w = s_hi * cls.ua_p * self.theta_eq
        self.impulse_j = cls.ua_open * envelope_c * cc["lid_open_s"]
        tau_slow = cls.C_c / (f_lo * (cls.ua_w + cls.ua_p))
        min_sep_s = 60.0 * cfg["demand"]["vaccination_fixed_min"] - cc["lid_open_s"]
        self.theta_ub = self.theta_eq + (self.impulse_j / cls.C_c) / (1.0 - math.exp(-min_sep_s / tau_slow))
        self.theta_high_c = cc["theta_high_c"]
        self.temperature_safe = self.theta_ub <= self.theta_high_c and ratio * cfg["climate"]["cabin_band_c"][0] >= cc["theta_low_c"]
        self.full_j = cls.m_p * cls.L * (1.0 - cc["initial_melt_fraction"])
        self.reserve_j = cc["reserve_fraction"] * self.full_j
        self.s_hi = s_hi

    def consumption(self, minutes: float, n_open: int) -> float:
        return self.rate_w * 60.0 * minutes + self.impulse_j * n_open

    def feasible(self, e_rem: float, minutes: float, n_open: int) -> bool:
        return e_rem - self.consumption(minutes, n_open) >= self.reserve_j - 1e-9

    def max_minutes(self, e_rem: float, n_open: int) -> float:
        return (e_rem - self.reserve_j - self.impulse_j * n_open) / (self.rate_w * 60.0)

    def safe_with_sensor_error(self, error_c: float) -> bool:
        """Return whether the warm-side margin survives bounded sensor error.

        The archived simulations use an exact virtual sensor. This helper makes
        the deployment margin explicit without changing their trajectories.
        """
        if error_c < 0:
            raise ValueError("sensor error bound must be non-negative")
        return self.theta_ub + error_c <= self.theta_high_c


class CarrierObserver:
    """On-board latent-heat estimator from the compartment sensor (conservative)."""

    def __init__(self, bound: ThermalBound):
        self.bound = bound
        self.e_est = bound.full_j

    def reset(self):
        self.e_est = self.bound.full_j

    def update(self, theta_meas: float, lid_open: bool):
        cls = self.bound.cls
        self.e_est -= self.bound.s_hi * cls.ua_p * max(theta_meas, 0.0) * 60.0
        if lid_open:
            self.e_est -= self.bound.impulse_j
