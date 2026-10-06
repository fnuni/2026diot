"""Supplementary ordering must be stable, numeric and grouped by condition."""
import re

from scripts.make_supplementary import (
    CONTROLLER_ORDER, DISPLAY_ORDER, build, budget_key,
    network_controller_key, outage_row_key, scale_key, thermal_key,
)


def test_network_then_manuscript_controller_order():
    pairs = [("N1|STATIC", {}), ("N0|HYBRID_EAGER", {}),
             ("N0|STATIC", {}), ("N0|HYBRID", {})]
    assert [key for key, _ in sorted(pairs, key=network_controller_key)] == [
        "N0|STATIC", "N0|HYBRID", "N0|HYBRID_EAGER", "N1|STATIC"]


def test_numeric_condition_order():
    assert scale_key(("4|0.9|N4|HYBRID", {})) < scale_key(("16|0.9|N1|STATIC", {}))
    assert thermal_key(("1.6|N1|CENTRAL", {})) < thermal_key(("2.0|N1|CENTRAL", {}))
    assert budget_key(("5|N4|HYBRID", {})) < budget_key(("100|N1|CENTRAL", {}))
    assert outage_row_key(["60", "180", "Greedy-FB$^*$"]) < outage_row_key(["120", "30", "Central@fog"])
    assert CONTROLLER_ORDER["HYBRID"] < CONTROLLER_ORDER["HYBRID_EAGER"]


def test_generated_screening_tables_are_grouped():
    tables = re.findall(r"\\begin\{longtable\}.*?\\end\{longtable\}", build(), re.S)
    for table_number in (1, 3, 4):
        rows = [line for line in tables[table_number - 1].splitlines()
                if re.match(r"N\d+ & ", line)]
        keys = [(line.split(" & ")[0], DISPLAY_ORDER[line.split(" & ")[1]])
                for line in rows]
        assert len(keys) == 40
        assert keys == sorted(keys)
    rows = [line for line in tables[5].splitlines()
            if re.match(r"\d+/[\d.]+/N\d+ & ", line)]
    keys = [(int(fleet), float(load), network, DISPLAY_ORDER[line.split(" & ")[1]])
            for line in rows
            for fleet, load, network in [line.split(" & ")[0].split("/")]]
    assert len(keys) == 96
    assert keys == sorted(keys)
