"""Recompute thermal-bound sensitivity without running the dispatch simulator.

The grid exposes the two structural inputs highlighted in the manuscript:
``UA_p/UA_w`` and compartment heat capacity ``C_c``. Infeasible one-target
calibrations are recorded as errors instead of being converted into a bound.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aedge.climate import AmbientModel
from aedge.common import load_config
from aedge.thermal import ThermalBound, calibrate_class


def one(cfg: dict, carrier: str) -> dict:
    cls = calibrate_class(cfg, carrier)
    envelope = AmbientModel(cfg, "JULY", cfg["time"]["shift_min"] + cfg["time"]["max_overtime_min"]).envelope()
    bound = ThermalBound(cls, cfg, envelope)
    return {
        "ua_w_w_per_k": cls.ua_w,
        "ua_p_w_per_k": cls.ua_p,
        "ambient_envelope_c": envelope,
        "theta_ub_c": bound.theta_ub,
        "theta_ub_plus_0_5c_sensor_error": bound.theta_ub + 0.5,
        "max_minutes_no_openings": bound.max_minutes(bound.full_j, 0),
        "warm_side_safe": bound.temperature_safe,
        "warm_side_safe_with_0_5c_sensor_error": bound.safe_with_sensor_error(0.5),
    }


def analyse() -> dict:
    base = load_config()
    rows = []
    for ratio in (3.0, 4.0, 5.0, 8.0):
        for heat_capacity in (1000.0, 2000.0, 4000.0):
            for carrier in ("PQS_SR", "NQ_BAG"):
                cfg = copy.deepcopy(base)
                cfg["carrier"]["ua_pack_to_wall_ratio"] = ratio
                cfg["carrier"]["classes"][carrier]["C_c"] = heat_capacity
                row = {"carrier": carrier, "ua_pack_to_wall_ratio": ratio, "C_c_j_per_k": heat_capacity}
                try:
                    row.update(one(cfg, carrier))
                except ValueError as exc:
                    row["calibration_error"] = str(exc)
                rows.append(row)
    return {"status": "illustrative sensitivity; not device qualification", "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("campaigns/sensitivity/thermal.json"))
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result = analyse()
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {len(result['rows'])} sensitivity cells to {args.out}")


if __name__ == "__main__":
    main()
