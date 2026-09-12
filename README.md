# PV-to-Hydrogen Curtailment Utilisation in Cyprus — v1.4

## Overview

This project evaluates the technical and economic use of photovoltaic (PV) electricity for green-hydrogen production in Cyprus, with particular emphasis on PV curtailment, PEM electrolyser sizing and operating strategy.

The model represents a **10 MWp PV plant**, an export-constrained grid connection and a **PEM electrolyser**. Version 1.4 extends the original PVGIS-based model by integrating a detailed **PVsyst** simulation and a **multi-objective PEM sizing framework**.

Two PV input sources are retained:

1. **PVGIS 2023** — hourly PV production for the actual 2023 weather year.
2. **PVsyst TMY 5.3** — hourly AC output from a 10 MWp PVsyst system simulated with Typical Meteorological Year weather.

The model evaluates two operating strategies:

1. **Non-hybrid curtailment-oriented dispatch** — PV-only PEM operation with minimum-load support and curtailment-responsive ramping.
2. **Hybrid PV-priority dispatch** — PV is supplied to the PEM first and grid electricity is used only to fill the deficit required to reach a selected baseline load.

The analysis combines hourly energy balances, PEM partial-load efficiency, curtailment recovery, hydrogen production, utilisation, LCOH, NPV, break-even hydrogen price, PV-source comparison, multi-objective sizing and sensitivity studies.

---

## Research question

The project addresses the following question:

> Under Cyprus PV-curtailment conditions, what PEM electrolyser size and operating strategy provide a reasonable trade-off between curtailment recovery, electrolyser utilisation, hydrogen production and economic performance?

Version 1.4 adds a second question:

> How sensitive are the technical and economic conclusions to the PV generation dataset, and what PEM size emerges when curtailment recovery, LCOH and NPV are considered simultaneously rather than selecting a design point manually?

---

## What is new in v1.4

Version 1.4 introduces four major changes:

- **PVsyst integration** alongside the existing PVGIS 2023 dataset.
- **PVGIS–PVsyst source comparison** using monthly energy and power-duration analyses.
- **Fine-resolution multi-objective PEM sizing** from 0.10 to 3.00 MW in 0.01 MW increments.
- **Pareto-front analysis** using curtailment recovery, discounted LCOH and NPV as simultaneous objectives.

The previous v1.3 model remains the methodological basis for dispatch, PEM part-load behaviour and techno-economic accounting.

---

## System configuration

### PV plant

Nominal installed PV capacity:

- **10 MWp**

Two hourly PV datasets are included.

#### PVGIS 2023

- Source: PVGIS hourly time series
- Weather basis: actual calendar year **2023**
- Annual PV generation: approximately **16,086 MWh/year**
- Capacity factor: approximately **18.36%**

#### PVsyst TMY 5.3

- Software: **PVsyst 8**
- Meteorological source: **PVGIS TMY 5.3**
- PV technology: monofacial crystalline-silicon
- PV module: **Trina Solar TSM-DE20-600, 600 W**
- Inverter: **Sungrow SG350HX**
- DC capacity: approximately **10.005 MWp**
- AC inverter capacity: approximately **8.301 MWac**
- DC/AC ratio: approximately **1.205**
- Annual non-negative AC energy used by the dispatch model: approximately **19,175 MWh/year**
- Capacity factor relative to 10 MWp: approximately **21.89%**
- Maximum hourly AC output: approximately **8.45 MW**

Small negative night-time `E_Grid` values in the PVsyst export are clipped to zero for dispatch calculations. The raw values are retained for traceability. The clipping adjustment is approximately **0.43 MWh/year**, which is negligible relative to annual production.

### Important PV-source interpretation

The PVGIS and PVsyst datasets are **not a same-weather validation exercise**.

PVGIS represents the actual **2023 weather year**, whereas the PVsyst simulation uses a **Typical Meteorological Year (TMY)** dataset. Differences in annual yield therefore reflect both:

- meteorological-year differences, and
- modelling/system-representation differences.

The comparison is used as a **source/model sensitivity study**, not as proof that one dataset validates or invalidates the other.

### Grid

- Base-case export limit: **6 MW**
- Grid-export-limit sensitivity: **2–8 MW**

The export limit is treated as an exogenous representation of grid constraint severity. A lower export limit therefore represents more severe potential curtailment; it is not interpreted as an inherently desirable system condition.

### PEM electrolyser

- Technology: **PEM electrolysis**
- Minimum physical load: **15% of rated power**
- Base CAPEX: **1,000 EUR/kW**
- CAPEX sensitivity: **700, 1,000, 1,300 and 1,970 EUR/kW**
- Fixed OPEX base case: **3% of CAPEX/year**
- OPEX scenarios: **30, 50 and 64 EUR/kW/year**
- Project lifetime: **15 years**
- Discount rate: **8%**

---

## PV data-source selection

The code supports both PV datasets.

The current v1.4 reference configuration uses:

```text
PV_DATA_SOURCE = PVSYST
```

The source can also be switched to PVGIS, including through the environment-variable mechanism implemented in `main.py`.

This allows the same dispatch and economic model to be evaluated using either PV-generation source without changing the underlying PEM methodology.

For monthly economic accounting, the generic PVsyst TMY month/day/hour sequence is mapped onto a 2023 calendar solely so that the existing month-specific PV export opportunity-cost series can be applied consistently.

This calendar mapping **does not convert the TMY weather into actual 2023 weather**.

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

The counterfactual export without the PEM is:

```text
min(PV generation, grid export limit)
```

Lost export is calculated as the difference between this counterfactual export and actual export after PEM operation. Only this lost export receives a PV opportunity cost.

### 2. Hybrid PV-priority dispatch

The hybrid strategy gives PV absolute priority to the PEM.

Hourly dispatch follows this sequence:

1. Available PV is supplied to the PEM first, up to rated capacity.
2. Grid electricity supplies only the deficit required to reach the selected baseline load.
3. Grid electricity never displaces available PV.
4. Grid electricity never increases PEM power above the selected baseline by itself.
5. Remaining PV is exported up to the grid limit.
6. Any residual PV above the grid limit is curtailed.

The hybrid sensitivity analysis evaluates baseline fractions:

```text
0%, 20%, 30%, 40%, 50%, 60%, 80%, 100%
```

and grid-electricity prices:

```text
0, 25, 50, 75, 100, 150, 200, 250, 270, 300 EUR/MWh
```

The **0% hybrid baseline is not equivalent to the non-hybrid strategy**. It represents PV-priority PV-only operation, whereas the non-hybrid strategy first sustains minimum PEM load and then responds to curtailment.

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

The curve is interpolated between the specified operating points.

Other physical assumptions:

- Hydrogen LHV: **33.33 kWh/kg**
- Hydrogen exergy: **32.56 kWh/kg**
- Water consumption: **9 L/kg H2**
- Battery round-trip efficiency used for comparison: **90%**

---

## Multi-objective PEM sizing

Version 1.4 replaces the manually selected 1.5 MW reference size with a formal multi-objective sizing analysis.

### Search space

PEM capacity is swept from:

```text
0.10 MW to 3.00 MW
```

with:

```text
0.01 MW increments
```

This produces **291 candidate PEM sizes**.

### Objectives

Three objectives are evaluated simultaneously:

1. **Maximise curtailment recovery**
2. **Minimise discounted LCOH**
3. **Maximise NPV at the reference hydrogen selling price**

Pareto efficiency is identified directly, without imposing objective weights.

A single representative **balanced-compromise design** is then selected using equal-weight normalised distance to the ideal objective point.

This balanced design is a modelling choice for reporting purposes, **not a universal physical or economic optimum**. Different decision-maker priorities or objective weights can produce a different preferred PEM size.

### v1.4 multi-objective result

Using the active **PVsyst TMY** reference input:

- Balanced-compromise PEM size: **1.54 MW**
- Curtailment recovery: approximately **76.3%**
- Discounted LCOH: approximately **6.40 EUR/kg H2**
- NPV at 7 EUR/kg H2: approximately **+0.25 MEUR**
- Minimum-LCOH point: approximately **0.10 MW**
- Minimum LCOH: approximately **5.67 EUR/kg H2**
- Maximum-NPV point: approximately **1.13 MW**
- Maximum NPV: approximately **+0.29 MEUR**
- First PEM size reaching at least **99.9% curtailment recovery**: approximately **2.44 MW**
- Pareto-efficient candidates: **237 of 291**

The 1.54 MW point therefore represents a balanced trade-off rather than the minimum-cost, maximum-NPV or maximum-curtailment-recovery solution individually.

---

## PVGIS vs PVsyst comparison

The two PV sources produce materially different curtailment conditions at the same nominal 10 MWp PV capacity and 6 MW export limit.

For the earlier 1.5 MW PEM comparison:

| Metric | PVGIS 2023 | PVsyst TMY |
|---|---:|---:|
| Annual PV generation | 16,085.69 MWh | 19,174.59 MWh |
| PV capacity factor | 18.36% | 21.89% |
| Gross curtailment without PEM | 930.92 MWh | 2,662.82 MWh |
| Curtailment recovery | 96.06% | 74.84% |
| H2 production | 31,487.99 kg/year | 48,682.12 kg/year |
| PEM utilisation | 11.74% | 19.37% |
| Lost PV export | 648.68 MWh | 552.94 MWh |
| PV opportunity cost | 105,171.82 EUR/year | 90,017.78 EUR/year |
| Discounted LCOH | 10.33 EUR/kg | 6.37 EUR/kg |
| NPV at 7 EUR/kg | -0.90 MEUR | +0.26 MEUR |

These results demonstrate that the assumed PV-generation profile can materially change electrolyser utilisation, curtailment availability and project economics.

Because the weather bases differ, the table should be interpreted as **input-source sensitivity**, not controlled model validation.

---

## Hydrogen price assumptions

Hydrogen sale-price sensitivity:

- **2 EUR/kg**
- **3 EUR/kg**
- **4 EUR/kg**
- **6 EUR/kg**
- **8 EUR/kg**
- **10 EUR/kg**

Reference hydrogen selling price used for the main economic case:

**7 EUR/kg H2**

This is a modelling reference value and does not imply that Europe has a single uniform hydrogen market price.

---

## PV opportunity-cost treatment

The economic model distinguishes three electricity categories:

1. **Genuinely curtailed PV** — assigned zero export opportunity cost.
2. **Otherwise-exportable PV diverted to the PEM** — assigned an opportunity cost.
3. **Purchased grid electricity** — charged at the selected grid-electricity price.

PV opportunity cost is calculated using a **month-specific 2023 Cyprus RES purchase-price proxy at 11 kV**.

The monthly proxy used in the model is:

| Month | EUR/MWh |
|---|---:|
| January | 235.65 |
| February | 220.20 |
| March | 225.16 |
| April | 210.33 |
| May | 215.27 |
| June | 202.39 |
| July | 107.20 |
| August | 106.82 |
| September | 106.82 |
| October | 106.82 |
| November | 106.82 |
| December | 106.82 |

This is **not a Cyprus Day-Ahead Market price**. It is used as a historical proxy for the value of PV electricity that could otherwise have been exported.

The hybrid analysis additionally uses a generic **270 EUR/MWh** 2023 industrial grid-purchase benchmark as one reference scenario. It is not presented as a Day-Ahead Market price.

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
- PV-source sensitivity
- multi-objective PEM sizing

### Annualised LCOH

The discounted/annualised LCOH includes:

- annualised PEM CAPEX
- fixed annual OPEX
- purchased grid electricity cost, where applicable
- PV export opportunity cost

Conceptually:

```text
LCOH =
(CAPEX × CRF + annual fixed OPEX + annual grid cost + annual PV opportunity cost)
/
annual H2 production
```

### NPV

NPV is calculated using:

- PEM CAPEX as the initial year-0 investment
- annual hydrogen revenue
- fixed OPEX
- grid-purchase cost
- PV opportunity cost
- project discounting over the 15-year lifetime

Annualised CAPEX is **not** included again inside NPV.

For the constant-output, constant-real-cost formulation used here, the hydrogen selling price that produces **NPV = 0** is numerically equivalent to the annualised LCOH for the same scenario.

---

## Utilisation definition

PEM utilisation is calculated from annual electrical energy throughput relative to the energy that would be consumed if the electrolyser operated at rated power for all 8,760 hours:

```text
utilisation =
annual PEM electrical energy
/
(rated PEM power × 8,760 h)
```

It is therefore an energy-based equivalent full-load utilisation or capacity-factor metric.

It should not be confused with the separate count of physical operating hours.

---

## Main interpretation

Version 1.4 demonstrates several interacting engineering and economic effects:

- Increasing PEM size increases the ability to recover curtailed PV.
- The marginal curtailment-recovery benefit falls as PEM capacity becomes large enough to absorb most curtailment events.
- PEM utilisation depends strongly on the available PV profile and grid export constraint.
- The PEM size that minimises LCOH is not necessarily the size that maximises NPV.
- Neither of those sizes necessarily maximises curtailment recovery.
- The Pareto frontier makes these competing objectives explicit.
- PV-generation assumptions materially affect both curtailment availability and hydrogen economics.
- Hybrid operation can increase hydrogen output and PEM utilisation, but purchased electricity can rapidly worsen economics at high electricity prices.
- Foregone PV exports must be treated as an opportunity cost; otherwise hydrogen economics can appear artificially favourable.
- A technically attractive high-curtailment-recovery design is not automatically the economically preferred design.

The purpose of the model is therefore **not to force a positive economic result**, but to identify the technical and economic conditions under which PV-to-hydrogen operation becomes attractive.

---

## Figure set

Version 1.4 produces **18 final figures**:

1. **Figure 1 — Monthly PV Energy: PVGIS 2023 vs PVsyst TMY 5.3**
2. **Figure 2 — PV Power Duration: PVGIS vs PVsyst**
3. **Figure 3 — PEM Size vs Hydrogen Production**
4. **Figure 4 — PEM Size vs Utilisation**
5. **Figure 5 — Annualised LCOH vs PEM Size**
6. **Figure 6 — Multi-Objective PEM Sizing: Recovery vs LCOH vs NPV**
7. **Figure 7 — Curtailment Recovery vs PEM Size**
8. **Figure 8 — Curtailment Avoided and Residual vs PEM Size**
9. **Figure 9 — PEM Size vs One-Year CAPEX Intensity**
10. **Figure 10 — Monthly Hydrogen Production**
11. **Figure 11 — LCOH vs Grid Export Limit**
12. **Figure 12 — NPV vs Grid Export Limit**
13. **Figure 13 — NPV Heatmap: Grid Limit vs Hydrogen Price**
14. **Figure 14 — Hybrid LCOH vs Baseline Load by Electricity Price**
15. **Figure 15 — Hybrid NPV vs Baseline Load by Electricity Price**
16. **Figure 16 — Hybrid Strategy LCOH Heatmap**
17. **Figure 17 — Non-Hybrid Representative-Day Dispatch**
18. **Figure 18 — Hybrid Representative-Day Dispatch**

The figures are stored in:

```text
results/figures/
```

---

## Output tables

Version 1.4 also exports machine-readable result tables:

### `multiobjective_pem_sizing.csv`

Contains the fine PEM-size sweep and associated technical/economic objectives.

### `pareto_pem_sizing.csv`

Contains the Pareto-efficient PEM-sizing candidates.

### `pv_source_comparison_summary.csv`

Contains the summary comparison between PVGIS and PVsyst inputs.

The tables are stored in:

```text
results/tables/
```

---

## Scientific basis and data sources

The model distinguishes between **measured/software-generated input data**, **literature-based assumptions**, and **author-defined scenario assumptions**. This distinction is maintained to improve transparency and reproducibility.

| Model element | Basis / source |
|---|---|
| Hourly PV generation — actual 2023 weather | European Commission **PVGIS** hourly time-series data |
| Hourly PV generation — TMY system simulation | **PVsyst 8**, using PVGIS TMY 5.3 meteorological data and the system configuration documented in the repository |
| PEM partial-load gross SEC | Literature-based approximation informed by experimental PEM-electrolyser performance reported by **Crespi et al. (2023)** |
| Hydrogen LHV and water stoichiometry | Standard thermodynamic / electrochemical relations |
| PEM CAPEX and OPEX scenarios | Literature / European hydrogen-sector benchmarks and explicit scenario assumptions |
| Grid-export limits | Author-defined sensitivity scenarios representing different levels of grid constraint |
| PV export opportunity cost | Historical 2023 Cyprus RES purchase-price proxy used as an economic modelling assumption |
| Hybrid grid-electricity prices | Scenario sweep; 270 EUR/MWh retained as a representative 2023 industrial-price benchmark rather than a Day-Ahead Market price |
| Multi-objective weighting | Author-defined equal-weight normalised-distance criterion used only to select a representative balanced-compromise point |

Numerical assumptions that are not direct observations are therefore treated as **model inputs or scenarios rather than measured facts**. Sensitivity analysis is used where these assumptions can materially affect the conclusions.

### Key scientific reference for PEM part-load behaviour

Crespi, E., et al. (2023). *Experimental and theoretical evaluation of a 60 kW PEM electrolysis system for flexible dynamic operation*. **Energy Conversion and Management, 277**, 116622. https://doi.org/10.1016/j.enconman.2022.116622

The current Python implementation uses a simplified gross-SEC curve derived as an engineering approximation from published PEM part-load behaviour; it does **not** claim to reproduce the complete experimental system or its balance-of-plant performance.

### Planned EES electrochemical validation

A physics-based PEM model in **Engineering Equation Solver (EES)** is planned as the next validation layer. The EES model will independently calculate reversible/Nernst voltage, activation losses, ohmic losses, concentration losses, Faradaic hydrogen production, stack efficiency and heat generation. After calibration against peer-reviewed experimental data, an EES-derived PEM performance map will be compared with the current empirical SEC representation and subsequently integrated into the hourly Python simulation.

The intended modelling chain is:

```text
PVGIS / PVsyst -> Python hourly dispatch -> EES-validated PEM performance -> annual H2 -> sizing -> LCOH / NPV
```

This extension is intended to strengthen the connection between system-level techno-economic modelling and underlying PEM thermodynamics/electrochemistry.

---

## Model validation and sanity checks

The code performs internal consistency checks including:

- PEM source-energy balance
- non-negative grid purchases
- zero grid purchase at a 0% requested hybrid baseline
- PEM physical operating bounds
- non-negative lost-export energy
- non-negative PV opportunity cost
- price-independent physical dispatch for a fixed hybrid baseline
- consistency between PV-to-PEM, grid-to-PEM and total PEM energy

The model stops with an assertion error if these conditions are violated.

---

## Model limitations

This is a **first-order techno-economic screening model**, not an investment-grade feasibility study.

Current limitations include:

- simplified literature-based PEM partial-load SEC curve (physics-based EES validation planned)
- no PEM stack degradation
- no stack replacement schedule
- no hydrogen compression model
- no hydrogen storage CAPEX/OPEX
- no hydrogen transport cost
- no detailed balance-of-plant parasitic electricity model
- no dynamic start-up/shutdown degradation
- no hourly Cyprus wholesale electricity-price series
- static industrial grid-price benchmark for the main hybrid reference scenario
- monthly PV export opportunity-cost proxy rather than an hourly merchant-market price
- no dynamic price-responsive dispatch optimisation
- no economies of scale in PEM CAPEX
- no electrolyser-technology comparison
- no endogenous optimisation of the grid export limit
- no stochastic weather-year analysis
- PVGIS 2023 and PVsyst TMY are not based on identical weather years
- the equal-weight balanced multi-objective point reflects a modelling preference rather than a universal optimum

---

## Planned next development

Potential extensions include:

### Electrolyser-technology sensitivity

Compare:

- PEM
- alkaline electrolysis (AEL)
- anion-exchange-membrane electrolysis (AEM)
- solid-oxide electrolysis (SOEC)

using technology-specific assumptions for:

- minimum stable load
- specific electricity consumption
- CAPEX
- OPEX
- dynamic response
- start-up constraints
- degradation
- lifetime
- suitability for intermittent renewable operation

### Dynamic PEM operation

Future work can introduce:

- start-up and shutdown states
- hot standby
- degradation
- stack replacement
- dynamic efficiency
- transient renewable operation

### System-level optimisation

A later project stage can extend the model toward integrated:

```text
PV + Battery + Electrolyser + H2 Storage
```

dispatch and techno-economic optimisation.

---

## Software

- **Python 3**
- **NumPy**
- **pandas**
- **Matplotlib**
- **PVGIS**
- **PVsyst 8**
- **Engineering Equation Solver (EES)** — planned electrochemical validation layer

---

## Repository structure

```text
PV_Hydrogen-1/
│
├── README.md
├── main.py
├── requirements.txt
├── LICENSE
│
├── data/
│   ├── PVGIS_2023.csv
│   └── PVsyst_TMY_5.3.csv
│
├── docs/
│   ├── PVsyst_Report.pdf
│   └── PVsyst_Loss_Diagram.pdf
│
└── results/
    ├── figures/
    │   ├── figure01_pvgis_vs_pvsyst_monthly_energy.png
    │   ├── figure02_pvgis_vs_pvsyst_power_duration.png
    │   ├── figure03_pem_vs_h2.png
    │   ├── ...
    │   └── figure18_hybrid_daily_dispatch.png
    │
    └── tables/
        ├── multiobjective_pem_sizing.csv
        ├── pareto_pem_sizing.csv
        └── pv_source_comparison_summary.csv
```

---

## Running the model

Install the required Python packages:

```bash
pip install -r requirements.txt
```

The repository already contains the expected input files:

```text
data/PVGIS_2023.csv
data/PVsyst_TMY_5.3.csv
```

Run:

```bash
python main.py
```

The model prints the main engineering and economic results to the console and generates the analysis outputs used by the project.

The PV input source can be selected using the configuration in `main.py`. Version 1.4 uses the PVsyst dataset as the default reference input while retaining PVGIS for source sensitivity and comparison.

---

## Reproducibility notes

The repository includes:

- the two hourly PV input datasets
- the Python modelling code
- the PVsyst report
- the PVsyst loss diagram
- the full multi-objective sizing table
- the Pareto-efficient sizing table
- the PV-source comparison table
- the final figure set

This allows the principal v1.4 calculations and reported results to be reproduced from the repository.

---

## Version history

### v1.4

- integrated PVsyst hourly AC output
- retained PVGIS 2023 as a comparison dataset
- added PVGIS–PVsyst monthly-energy comparison
- added PV power-duration comparison
- added explicit TMY-versus-actual-year interpretation
- added fine PEM sizing sweep from 0.10 to 3.00 MW
- added three-objective PEM sizing
- added Pareto-front identification
- added equal-weight balanced-compromise design selection
- selected approximately 1.54 MW as the v1.4 balanced reference design under the active PVsyst case
- added ≥99.9% curtailment-recovery sizing benchmark
- added machine-readable sizing and source-comparison tables
- expanded final figure set to 18 figures
- reorganised repository outputs into `results/figures/` and `results/tables/`

### v1.3

- implemented two dispatch strategies
- added PV-priority hybrid operation
- added month-specific PV export opportunity-cost accounting
- added hybrid baseline and electricity-price sensitivity
- added break-even hydrogen price
- added representative-day dispatch plots
- adopted 7 EUR/kg H2 as the reference selling-price case
- added extensive dispatch sanity checks

### v1.2

- added PEM partial-load modelling
- added minimum-load sensitivity
- expanded techno-economic analysis

---

## Status

**v1.4 is the current stable version of the project.**

The project is intended as a transparent engineering and techno-economic portfolio/research model for examining the interaction between PV curtailment, electrolyser sizing, operating strategy and hydrogen economics in Cyprus.

The model deliberately exposes assumptions, sensitivities and trade-offs rather than presenting a single PEM size or operating strategy as universally optimal.
