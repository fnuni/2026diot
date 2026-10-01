# Parameter provenance and interpretation

This file is the single parameter account for the study. “Documented” means taken from the cited source; “derived” means computed from a documented value with a stated model; “declared” means an author assumption exposed to sensitivity where possible.

| Parameter | Value | Status | Source / rationale |
|---|---|---|---|
| Vaccine storage range | 2-8 °C; excursion quarantines carrier | documented | CDC Vaccine Storage and Handling Toolkit |
| PQS short-range cold life | at least 15 h at +43 °C (to +10 °C) | documented | WHO/PQS/E004/VC02.1 (2018) |
| Compartment heat capacity `C_c` | 3000 J/K (PQS), 2000 J/K (bag) | declared | exposed in `campaigns/sensitivity/thermal.json` |
| Coolant mass | 1.5 kg (PQS), 0.5 kg (bag) | declared | latent heat of ice 334 kJ/kg |
| Conductance ratio `UA_p/UA_w` | 5 | declared | ratios 3, 4, 5 and 8 are analysed; ratio 3 is incompatible with the fitted target |
| Reserve | 10% of latent reserve | declared | explicit safety reserve |
| Non-qualified bag | 3 h cold life at +43 °C | declared stress class | 20% of the PQS minimum |
| Lid opening | 60 s at 2 W/K per vaccination | declared | separation follows from the simulated 25-min vaccination activity, not from a general routing constraint |
| Sensor | exact in simulation; 0.5 °C deployment margin analysed | declared sensitivity | a physical device must supply a justified accuracy bound |
| Outdoor temperature | April-like 8.0/19.3 °C; July-like 18.7/32.3 °C | declared illustrative inputs | magnitude motivated by ISPRA normals; not traced station observations |
| Cabin / indoor | 20-26 °C; `20 + 0.5(T_out-20)` clipped to 20-30 °C | declared | carrier is taken indoors |
| Shift | 360 min; at most 45 min overtime | declared | morning home-care shift |
| Demand | 70% booked; 30% same day; 40% of same-day urgent | declared | urgent window 90 min; routine dynamic window 180 min |
| Travel | 3 min + `1.3 * distance / 40 km/h`; lognormal noise 0.15 | declared | Euclidean network with circuity |
| Load | `N = round(rho K 360 / 42)` | derived | 42-min planning workload per visit |
| ALNS | 30 iterations/event; 400 morning | declared | standard destroy-repair family, not a new optimiser |
| Local autonomy | after 5 min without contact | declared | vehicles retain the complete received plan |
| Market mode | 2-min bid window; reannounce after 10 min | declared | switching costs are reported through releases and outage-duration curves |
| N4 | fog ingress and token registry remain available; fog-cloud path unavailable for minutes 120-240 | declared topology | not a fog outage or cloud-ingress experiment |

## Thermal qualifications

- The bound is conditional on the reported parameters and assumptions. It is not device qualification or regulatory certification.
- At `UA_p/UA_w = 4` the warm-side bound exceeds 8 °C for the PQS cell at `C_c = 2000 J/K`; at ratio 3 the target cannot be calibrated and the code raises `ValueError`.
- A bounded sensor error `epsilon` requires `theta_ub + epsilon <= 8 °C`. The 0.5 °C sensitivity is reported separately.
- The single-sortie proof begins from the stated initial condition. Re-applying the remaining-energy inequality at an intermediate epoch is a planning guard, not a proved recursive-feasibility result.
- The simulated warm-season ambient never exercises the lower-temperature/freezing side. No cold-season claim is made.

## Statistical qualifications

The seed is the unit of inference. S1-S5 are the screening phase; C1-C3 are the confirmatory phase with separate seeds and a completed-service endpoint. The phases are not pooled. The Q5 sample size was computationally fixed rather than power-justified, and equivalence fails in the thermal-stress bundle.

Sources: [WHO carrier specification](https://extranet.who.int/pqweb/key-resources/documents/pqs-performance-specification-e004vc021-vaccine-carrier-freeze-prevention) and [CDC toolkit](https://www.cdc.gov/vaccines/hcp/downloads/storage-handling-toolkit.pdf).
