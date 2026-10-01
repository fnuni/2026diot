"""Check the reported-seed raw sample against distributed C1 rows."""
import csv
import gzip
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from aedge.common import sha
from aedge.metrics import stable_view, reduce
from confirmatory.campaign import served_metrics

sample=ROOT/'examples/replay/reported_C1'
saved=json.loads((sample/'scenario.json').read_text())
reported={r['run_id']:r for r in csv.DictReader((ROOT/'campaigns/confirmatory/results/runs.csv').open())}
rows=list(csv.DictReader((sample/'reported_rows.csv').open()))
for r in rows:
    with gzip.open(sample/(r['method']+'.jsonl.gz'),'rt') as f:
        log=[json.loads(line) for line in f]
    expected=reported[r['run_id']]
    assert sha(stable_view(log))==r['trajectory_sha256']==expected['trajectory_sha256']
    metrics={**reduce(saved['scenario'],log,saved['config']),**served_metrics(saved['scenario'],log)}
    for key in ('priority_served_pct','priority_handled_pct','violations','token_overlaps','unauthorized_services','unfinished_served'):
        assert abs(float(metrics[key])-float(expected[key]))<1e-9, (r['run_id'],key)
print(f'{len(rows)} reported C1 raw trajectories and endpoints verified (seed 9001, stress, N4)')
