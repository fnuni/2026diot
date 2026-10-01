# Edge coordination under infrastructure outages

Software and synthetic data accompanying Francesco Nucci and Gabriele Papadia, *What Makes Edge Coordination Resilient? A Negative Result on Vehicle Autonomy in Home-Healthcare IoT*.

The study isolates decision timing, coordinator placement, vehicle fallback and coordination policy in a deterministic healthcare-IoT simulator. All records are synthetic; no clinical or personal data are included.

## Study design and findings

| Campaign | Seeds | Evaluations | Endpoint |
|---|---|---:|---|
| Screening S1–S5 | 8001–8040 | 10,192 | PWH: priority-weighted on-time request handling |
| Completed-service C1–C3 | 9001–9040 | 2,660 | PWS: priority-weighted completed on-time service |

Controller–scenario evaluations are paired and dependent, not independent observations. Seeds are the inference units. The campaigns use distinct seeds and different endpoints and are not pooled. campaigns/ANALYSIS_PLAN.md identifies the primary hypothesis families and the exploratory analyses; the protocol descriptions are not an external preregistration.

The completed-service factorial yields a fog-placement gain of 5.76 PWS percentage points [4.89, 6.63] under N4 and a vehicle-fallback change of −0.08 [−0.45, 0.28] under N3. Pooled Hybrid–fog equivalence is narrow and does not hold in the exploratory thermal-stress analysis.

GREEDY_FB uses cloud event-driven ALNS while reachable, greedy fog insertion only during the outage, and global repair on recovery. Registry, assignments, plans, guards and vehicle classes persist. Connected vehicles retain global mode; Hybrid changes to auctions and market-mode local planning. Their contrast is between complete fallback policies, not auction messaging alone.

For 20 stress seeds with outage onset at minute 120, Hybrid–Greedy-FB is −0.95 [−2.19, 0.29], +0.45 [−0.86, 1.76], +2.84 [1.80, 3.87] and +5.80 [4.21, 7.40] points at durations 30, 60, 120 and 180 minutes (exploratory, unadjusted 95% paired t intervals). Hybrid–fog is −1.80 at 120 minutes and −2.72 at 180 minutes.

GREEDY_FOG is an all-day insertion policy. Its placement contrast against STATIC is zero under N1/N3 and +3.38 [2.59, 4.17] points under N4. Its contrast against cloud ALNS is not an isolated placement effect.

## Quick start

Python 3.10 or later. The simulator uses the standard library; analysis and tests require requirements.txt.

~~~sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m aedge.release_integrity
python -m pytest -q tests
python scripts/check_analyses.py
python scripts/check_replay.py
~~~

The manifest hashes CSV content after CRLF-to-LF normalisation; other included files are hashed byte-for-byte. Mutable .gitignore, Git metadata and regenerated working directories are excluded. Changing a CSV value still fails the check. The checksum verifies content, not preregistration.

check_analyses.py recomputes all analysis layers from distributed run tables in temporary directories and compares their JSON fields. check_replay.py validates eight reported C1 trajectories and service/safety endpoints against the campaign table.

## Layout

| Path | Content |
|---|---|
| aedge/, confirmatory/ | Simulator, controllers and completed-service analysis |
| campaigns/screening/ | S1–S5 run tables, design, analysis and verification |
| campaigns/confirmatory/ | C1–C3 run table, design, analysis and audit |
| campaigns/ANALYSIS_PLAN.md | Primary families and exploratory analyses |
| campaigns/sensitivity/ | Thermal sensitivities and Hybrid replay diagnostics |
| examples/replay/reported_C1/ | Seed 9001, stress, N4: scenario and eight raw trajectories |
| scripts/, tests/ | Reproduction, manuscript material generation and regression checks |

Development seeds 7001–7020 and smoke seed 7101 are excluded from reported inference.

## Reproduction

Write new simulations to a new directory. Trajectory hashes exclude host timing; wall-clock values are not reproducible numerical endpoints.

~~~sh
python -m aedge.experiment --stage S1 --output reproduced_screening --workers 4
python -m confirmatory.campaign --out reproduced_completed_service --workers 4
python -m confirmatory.analyse --out reproduced_completed_service
python scripts/replay_diagnostics.py --out reproduced_diagnostics --workers 4
python scripts/thermal_sensitivity.py --out reproduced_thermal.json
python scripts/make_screening_macros.py --out generated_screening.tex
python scripts/make_manuscript_macros.py --out generated_completed_service.tex
python scripts/make_supplementary.py --out manuscript_material
~~~

The diagnostic command verifies all 340 C1/C2 Hybrid trajectory hashes and counts thermal alerts/reflex decisions, market entries, voluntary releases and recovery revoke attempts. It writes new diagnostics and raw samples to the specified output directory without overwriting the distributed evidence. These are observational diagnostics, not a causal mediation analysis.

## Guarantees and limits

- Classical deliberative agents, not learned or generative agents.
- N4 removes only the district–cloud path. Fog request ingress, registry and N1-quality vehicle access remain available; it is not a radio or fog outage.
- Fencing safety requires one coordinator and retained state. It is not consensus, failover, crash recovery, authentication or guaranteed liveness.
- Thermal guarantees are conditional on declared physical inputs, ambient bounds, opening separation and sensing. Calibration fails if its target is not bracketed. No physical-device qualification or cold-season freeze-risk validation is claimed.
- Timings are measured on a reference host, not fog hardware. Payloads exclude radio/security overhead.

Source code is MIT licensed (LICENSE); synthetic data and documentation are CC BY 4.0 (LICENSE-DATA.md). Citation metadata are in CITATION.cff. AI-assisted tools supported checks and editing; the authors are responsible for the scientific content.
