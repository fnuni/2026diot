# Study protocol - screening phase S1-S5

This phase maps mechanisms and operating regimes using seeds 8001-8040. The document records the analysis implemented by `aedge.analysis`; it is not an external preregistration or trusted timestamp.

- **S1:** traffic (off/on) x thermal bundle (benign/stress) x N0-N4 x eight controllers; 6,400 runs. Primary endpoint: priority-weighted on-time handling (PWH).
- **S2:** load/fleet cells x N1/N3/N4 x eight controllers; 1,920 runs. N4 is included in every load/fleet cell.
- **S3:** link availability x outage-duration sweep; 1,500 runs.
- **S4:** separate-process trajectory check; 12 runs.
- **S5:** conductance-assumption stress; 360 runs.

All controllers share requests, random streams, complete morning plans, ownership tokens, execution guards and the thermal observer. The main contrasts are Hybrid-Central under N4 and N3, Central-Periodic under N1, and Hybrid-Edge-market under N1. S2-S5 are sensitivity and mechanism analyses. No operational or clinical data enter the generator.
