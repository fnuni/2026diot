# Study protocol - confirmatory phase C1-C3

This phase uses seeds 9001-9040 and a completed-service endpoint (PWS) to test placement and vehicle fallback separately. The plan was recorded locally before these designated seeds were analysed, but it was not externally registered; the release manifest verifies package contents only.

- **C1:** thermal bundle x N1/N3/N4 x the 2x2 placement-by-fallback controllers, Hybrid and `GREEDY_FOG`; 1,440 runs.
- **C2:** five outage windows x Central, Central@fog, Hybrid and `GREEDY_FOG`; 400 runs on the first 20 seeds in thermal stress.
- **C3:** event-ALNS budgets 5 and 100 x N1/N4 x Central, Central@fog and Hybrid; 240 runs on the first 20 seeds.

The superiority family is F1 placement under N4, F2 fallback under N3 and F3 interaction under N4, using seed-level paired sign-flip tests with Holm adjustment. Q5 compares Hybrid with Central@fog using a +/-1.5 pp TOST; it is reported overall and by thermal bundle. The sample size is a computational design, not a formal power calculation. `GREEDY_FOG`, C2 and C3 are mechanism/sensitivity analyses and are not added to the confirmatory multiplicity family.
