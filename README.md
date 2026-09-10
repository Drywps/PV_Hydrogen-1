# PV-to-Hydrogen Curtailment Utilisation in Cyprus — v1.3

## Overview

This project evaluates the technical and economic use of photovoltaic (PV) electricity for green-hydrogen production in Cyprus, with particular emphasis on PV curtailment.

The model represents a **10 MWp PV plant**, an export-constrained grid connection and a **PEM electrolyser**. Hourly 2023 PV production data are used to compare two operating strategies:

1. **Non-hybrid curtailment-oriented dispatch** — PV-only PEM operation with minimum-load support and curtailment-responsive ramping.
2. **Hybrid PV-priority dispatch** — PV is supplied to the PEM first and grid electricity is used only to fill the deficit required to reach a selected baseline load.

The analysis combines hourly energy balances, PEM partial-load efficiency, curtailment recovery, hydrogen production, utilisation, LCOH, NPV, break-even hydrogen price and sensitivity studies.

---

## Research question

The project addresses the following question:

> Under Cyprus PV-curtailment conditions, what PEM electrolyser size and operating strategy provide a reasonable trade-off between curtailment recovery, electrolyser utilisation, hydrogen production and economic performance?

---

## System configuration

### PV plant

- Installed PV capacity: **10 MWp**
- Dataset: **PVGIS hourly production, 2023**
- Annual PV generation: approximately **16,086 MWh/year**
- PV capacity factor: approximately **18.36%**

### Grid

- Base-case export limit: **6 MW**
- Grid-export-limit sensitivity: **2–8 MW**
- Gross PV curtailment without PEM at the 6 MW limit: approximately **931 MWh/year**

### PEM electrolyser

- Selected curtailment-oriented design point: **1.5 MW**
- Near-full-curtailment benchmark: **2.0 MW**
- Minimum physical load: **15% of rated power**
- Base CAPEX: **1,000 EUR/kW**
- CAPEX sensitivity: **700, 1,000, 1,300 and 1,970 EUR/kW**
- Fixed OPEX base case: **3% of CAPEX/year**
- Project lifetime: **15 years**
- Discount rate: **8%**

---

## Operating strategies

### 1. Non-hybrid curtailment-oriented dispatch

The PEM operates only from PV electricity.

Hourly dispatch follows this sequence:

1. If PV can sustain the PEM physical minimum load, PV supplies that minimum load.
2. Remaining PV is exported to the grid up to the grid export limit.
3. PV that would otherwise be curtailed is diverted to the PEM.
4. The PEM ramps above minimum load to absorb this additional PV, up to rated capacity.
5. No grid electricity is purchased.

This strategy is deliberately different from a pure curtailment-only electrolyser because some otherwise-exportable PV may be used to sustain minimum PEM operation.

### 2. Hybrid PV-priority dispatch

The hybrid strategy gives PV absolute priority to the PEM.

Hourly dispatch follows this sequence:

1. Available PV is supplied to the PEM first, up to rated capacity.
2. Grid electricity supplies only the deficit required to reach the selected baseline load.
3. Grid electricity never displaces available PV.
4. Grid electricity never increases PEM power above the selected baseline by itself.
5. Remaining PV is exported up to the grid limit.
6. Any residual PV above the grid limit is curtailed.

The specific hybrid design point used for final reporting is:

- PEM capacity: **1.5 MW**
- Baseline load: **20% = 0.30 MW**
- Grid electricity price assumption: **270 EUR/MWh**
- PV priority: **yes**

The full hybrid sensitivity analysis evaluates multiple baseline loads and electricity-price scenarios.

---

## PEM efficiency model

Hydrogen production is calculated using a **load-dependent gross specific electricity-consumption (SEC) curve**, rather than a single constant kWh/kg value.

| PEM load | Gross SEC |
|---:|---:|
| 15% | 46.0 kWh/kg H2 |
| 25% | 46.5 kWh/kg H2 |
| 40% | 48.0 kWh/kg H2 |
| 60% | 50.5 kWh/kg H2 |
| 80% | 53.0 kWh/kg H2 |
| 100% | 55.5 kWh/kg H2 |

For the selected non-hybrid case, the load-weighted average gross SEC is approximately **49.0 kWh/kg H2**.

Other physical assumptions:

- Hydrogen LHV: **33.33 kWh/kg**
- Hydrogen exergy: **32.56 kWh/kg**
- Water consumption: **9 L/kg H2**

---

## Selected engineering design

The **1.5 MW PEM** is used as the main curtailment-oriented engineering design point.

At the 6 MW grid export limit:

- Gross curtailment without PEM: approximately **930.9 MWh/year**
- Avoided curtailment with 1.5 MW PEM: approximately **894.2 MWh/year**
- Residual curtailment: approximately **36.7 MWh/year**
- Curtailment recovery: approximately **96.1%**

A **2.0 MW PEM** is retained as a near-full-curtailment benchmark:

- Curtailment recovery: approximately **99.4%**
- Residual curtailment: approximately **5.3 MWh/year**

The 1.5 MW point is therefore an **engineering design choice**, not a mathematically optimised global optimum. It represents a compromise between curtailment recovery, hydrogen output, utilisation and capital intensity.

---

## Hydrogen price assumptions

Hydrogen sale-price sensitivity:

- **2 EUR/kg**
- **3 EUR/kg**
- **4 EUR/kg**
- **6 EUR/kg**
- **8 EUR/kg**
- **10 EUR/kg**

Reference European green-hydrogen price used for the main economic case:

**7 EUR/kg H2**

This is a modelling reference value and does not imply that Europe has a single uniform hydrogen market price.

---

## PV opportunity-cost treatment

The economic model distinguishes three electricity categories:

1. **Genuinely curtailed PV** — assigned zero export opportunity cost.
2. **Otherwise-exportable PV diverted to the PEM** — assigned an opportunity cost.
3. **Purchased grid electricity** — charged at the selected grid electricity price.

PV opportunity cost is calculated using a **month-specific 2023 Cyprus RES purchase-price proxy at 11 kV**.

The proxy ranges approximately from **106.82 to 235.65 EUR/MWh** during 2023.

This is not a Cyprus Day-Ahead Market price. It is used only as a historical proxy for the value of PV that could otherwise have been exported.

---

## Economic methodology

The model calculates:

- simple LCOH
- annualised/discounted LCOH
- NPV
- break-even hydrogen selling price
- CAPEX sensitivity
- OPEX sensitivity
- hydrogen-price sensitivity
- grid-export-limit sensitivity
- hybrid baseline sensitivity
- hybrid grid-electricity-price sensitivity

The annualised LCOH includes:

- annualised PEM CAPEX
- fixed annual OPEX
- PV export opportunity cost
- purchased grid electricity cost, where applicable

NPV is calculated using the initial PEM CAPEX as a year-0 investment and discounted annual operating cash flows. Annualised CAPEX is **not** double-counted inside NPV.

For the constant-output, constant-real-cost formulation used here, the break-even hydrogen selling price is the price that produces **NPV = 0** and is numerically equivalent to the annualised LCOH for the same scenario.

---

## Base-case results

### Non-hybrid design — 1.5 MW PEM

Approximate annual results:

- Hydrogen production: **31,488 kg/year**
- PEM utilisation: **11.74%**
- Curtailment recovery: **96.1%**
- Lost export energy due to PEM: **648.7 MWh/year**
- PV opportunity cost: approximately **105,172 EUR/year**
- Discounted LCOH: approximately **10.33 EUR/kg H2**
- NPV at **7 EUR/kg H2**: approximately **-0.90 MEUR**

Under the selected assumptions, the non-hybrid base case is therefore **not profitable at 7 EUR/kg H2**.

### Specific hybrid design point — 1.5 MW PEM, 20% baseline

The hybrid design point uses:

- PEM capacity: **1.5 MW**
- Baseline: **20% = 0.30 MW**
- Grid electricity price: **270 EUR/MWh**

The hybrid strategy materially increases PEM utilisation and annual hydrogen production because the PEM can remain active when PV alone is insufficient to maintain the requested baseline. Its economics, however, depend strongly on the cost of purchased electricity and the opportunity cost of diverted PV.

The model therefore treats the hybrid case as a **strategy sensitivity study**, not as an assumed superior operating mode.

---

## Main interpretation

The results demonstrate a clear engineering trade-off:

- Larger PEM systems capture more curtailed PV.
- Larger PEM systems operate at lower annual utilisation when supplied mainly from intermittent PV.
- CAPEX increases with PEM size faster than hydrogen production once most curtailment has already been captured.
- A 1.5 MW PEM captures most of the available curtailed energy in the base case.
- A 2.0 MW PEM captures almost all curtailment but provides only a small additional recovery benefit.
- Hybrid operation can substantially increase utilisation and hydrogen output.
- Hybrid economics deteriorate rapidly when grid electricity and foregone PV exports are expensive.

The purpose of the model is therefore **not to force a positive economic result**, but to identify the technical and economic conditions under which PV-to-hydrogen operation becomes attractive.

---

## Figure set

The final v1.3 figure set is renumbered consecutively from **Figure 1**.

1. **Figure 1 — PEM Size vs Hydrogen Production**
2. **Figure 2 — PEM Size vs Utilisation**
3. **Figure 3 — Annualised LCOH vs PEM Size**
4. **Figure 4 — Curtailment Recovery vs PEM Size**
5. **Figure 5 — Curtailment Avoided and Residual vs PEM Size**
6. **Figure 6 — PEM Size vs One-Year CAPEX Intensity**
7. **Figure 7 — Monthly Hydrogen Production**
8. **Figure 8 — LCOH vs Grid Export Limit**
9. **Figure 9 — NPV vs Grid Export Limit**
10. **Figure 10 — NPV Heatmap: Grid Limit vs Hydrogen Price**
11. **Figure 11 — Hybrid LCOH vs Baseline Load by Electricity Price**
12. **Figure 12 — Hybrid NPV vs Baseline Load by Electricity Price**
13. **Figure 13 — Hybrid Strategy LCOH Heatmap**
14. **Figure 14 — Non-Hybrid Representative-Day Dispatch**
15. **Figure 15 — Hybrid Representative-Day Dispatch**

The raw full-year PV trace and two-day PV trace are intentionally excluded from the final figure set because they add limited engineering value relative to the retained analyses.

### Dispatch-figure convention

For Figures 14 and 15:

- PV generation: **thick orange line**
- PV generation during curtailment hours: **red dotted overlay**
- PEM input from PV: **orange shaded area**
- PEM input from grid: **grey shaded area**
- grid contribution to PEM: **thick grey line**
- H2 energy output: **thick blue line**
- total PEM electrical input: neutral boundary line
- PEM rated capacity: dashed horizontal line
- requested baseline: dotted horizontal line

---

## Model validation and sanity checks

The code performs internal consistency checks for the hybrid dispatch model, including:

- PEM source-energy balance
- non-negative grid purchases
- zero grid purchase at a 0% requested baseline
- PEM physical operating bounds
- non-negative lost-export energy
- non-negative PV opportunity cost
- price-independent physical dispatch for a fixed baseline
- consistency between PV-to-PEM, grid-to-PEM and total PEM energy

The model stops with an assertion error if these conditions are violated.

---

## Model limitations

This is a **first-order techno-economic screening model**, not an investment-grade feasibility study.

Current limitations include:

- simplified literature-based PEM partial-load SEC curve
- no PEM stack degradation
- no stack replacement schedule
- no hydrogen compression model
- no hydrogen storage CAPEX/OPEX
- no hydrogen transport cost
- no detailed balance-of-plant parasitic electricity consumption
- no dynamic start-up/shutdown degradation
- no time-resolved Cyprus wholesale electricity-price series
- static industrial grid-price benchmark for the main hybrid design point
- PV export opportunity cost represented by a monthly 2023 RES purchase-price proxy rather than an hourly merchant-market price
- no dynamic price-responsive dispatch optimisation
- no economies of scale in PEM CAPEX
- no electrolyser-technology comparison in v1.3

---

## Planned v1.4

The next stage is intended to extend the project in two directions.

### 1. PVsyst integration

A 10 MWp Cyprus PV system will be modelled in **PVsyst** and its hourly output compared with the existing PVGIS dataset.

The same hydrogen model can then compare:

- annual PV yield
- capacity factor
- curtailed energy
- hydrogen production
- PEM utilisation
- LCOH
- NPV

for **PVGIS vs PVsyst** inputs.

### 2. Electrolyser-technology sensitivity

The model can then be extended to compare:

- PEM
- alkaline electrolysis (AEL)
- anion-exchange-membrane electrolysis (AEM)
- solid-oxide electrolysis (SOEC)

The comparison should use technology-specific assumptions for:

- minimum stable load
- specific electricity consumption
- CAPEX
- OPEX
- dynamic response
- start-up constraints
- degradation
- lifetime
- suitability for intermittent renewable operation

The v1.3 PEM model is intended to remain the frozen reference case against which later extensions are compared.

---

## Software

- **Python 3**
- **NumPy**
- **pandas**
- **Matplotlib**
- **PVGIS** hourly data

Planned extension:

- **PVsyst** for PV yield modelling and cross-validation

---

## Suggested repository structure

```text
PV_Hydrogen_Project/
│
├── README.md
├── main.py
├── requirements.txt
│
├── data/
│   └── Timeseries_35.141_33.415_SA3_10000kWp_crystSi_14_28deg_0deg_2023_2023.csv
│
└── figures/
    ├── figure01_pem_vs_h2.png
    ├── figure02_pem_vs_utilization.png
    ├── ...
    └── figure15_hybrid_daily_dispatch.png
```

---

## Running the model

Install the required Python packages:

```bash
pip install -r requirements.txt
```

Place the PVGIS CSV file in the project directory or update the input path in `main.py`.

Run:

```bash
python main.py
```

The model prints the main engineering and economic results to the console and saves the final figures in the `figures/` directory.

---

## Version history

### v1.3

- two dispatch strategies implemented
- PV-priority hybrid operation added
- month-specific PV export opportunity-cost accounting added
- hybrid baseline and electricity-price sensitivity added
- break-even hydrogen price added
- final base-case summary added
- representative-day dispatch plots added
- hydrogen-price sensitivity updated to 2, 3, 4, 6, 8 and 10 EUR/kg
- 7 EUR/kg H2 reference case adopted
- final figure set reduced and renumbered from Figure 1
- extensive dispatch sanity checks added

### v1.4 — planned

- PVsyst integration and PVGIS/PVsyst comparison
- electrolyser-technology sensitivity

---

## Status

**v1.3 is the stable PEM reference version of the project.**

It is intended as a transparent portfolio/research model for examining the interaction between PV curtailment, electrolyser sizing, operating strategy and hydrogen economics in Cyprus.
