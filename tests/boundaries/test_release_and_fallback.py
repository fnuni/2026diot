from unittest.mock import patch
import csv
from pathlib import Path

from aedge.release_integrity import digest, write_manifest
from aedge.actors import GreedyFallbackCenter, GlobalCenter, StaticCenter


def test_csv_integrity_survives_git_eol_normalisation(tmp_path):
    p = tmp_path / "runs.csv"
    p.write_bytes(b"seed,score\r\n9001,1.0\r\n")
    expected = digest(p)
    p.write_bytes(b"seed,score\n9001,1.0\n")
    assert digest(p) == expected
    p.write_bytes(b"seed,score\n9001,2.0\n")
    assert digest(p) != expected
    (tmp_path / ".gitignore").write_text("cache/\n")
    write_manifest(tmp_path)
    assert ".gitignore" not in (tmp_path / "RELEASE_MANIFEST.sha256").read_text()


def test_greedy_only_during_outage_and_global_on_recovery():
    # Dispatch routing is the scientific intervention: ALNS when up,
    # insertion when down; recovery explicitly triggers global repair.
    c = GreedyFallbackCenter.__new__(GreedyFallbackCenter)
    c.infra = {"cloud_up": True}
    c.cloud_was_up = True
    c.queue, c.triggers, c.logs = {}, set(), []
    with patch.object(GlobalCenter, "decide") as global_decide, patch.object(StaticCenter, "decide") as insert:
        c.decide(100)
        global_decide.assert_called_once_with(100)
        insert.assert_not_called()
        c.reg = type("Registry", (), {"holder": {}})()
        c.infra["cloud_up"] = False
        c.decide(120)
        insert.assert_called_once_with(c, 120)
        c.infra["cloud_up"] = True
        c.decide(150)
        assert ("cloud_restored", -1) in c.triggers
        assert global_decide.call_count == 2


def test_greedy_fallback_has_no_routine_policy_gap_in_reported_cells():
    root = Path(__file__).resolve().parents[2]
    rows = list(csv.DictReader((root / 'campaigns/confirmatory/results/runs.csv').open()))
    paired = {}
    for row in rows:
        if row['stage'] == 'C1' and row['network'] in ('N1', 'N3'):
            paired.setdefault(row['job_id'], {})[row['method']] = row
    assert len(paired) == 160
    for methods in paired.values():
        for key in ('priority_served_pct', 'priority_handled_pct', 'travel_min',
                    'quarantines', 'releases', 'bytes_sent'):
            assert methods['GREEDY_FB'][key] == methods['HYBRID'][key]
