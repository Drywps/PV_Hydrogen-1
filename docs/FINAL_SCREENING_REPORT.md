# PV-to-Hydrogen: final screening report

Release: v1.9.1-final-screening. Active PV input: PVSYST.

## Methodology and boundary

The study compares a fixed 1.60 MW PEM coupled to a 10 MWp PV
profile and a 6 MW export limit. Site coordinates are
35.141 N, 33.415 E. Strategy A uses only otherwise-curtailed PV. Strategy B gives
PV priority and conditionally imports the deficit to a requested baseline.
Strategy C adds a battery charged only from residual curtailed PV and discharged
only to unused PEM capacity. Each dispatch preserves PV, PEM-source and battery
energy balances and the 15% minimum PEM operating load.

PV inputs are hourly PVsyst TMY and PVGIS 2023. Strategy B/C use the supplied
17,230 TSOC half-hour DAM observations over 359 days, annualized by 365/359.
Hourly PV is held constant within each pair of market intervals. These weather
and price inputs are not a historically coincident operating year.

Primary H2 production uses the Tran load-dependent system SEC curve, with the
15% point explicitly extrapolated. EES is a separate gross-stack sensitivity
and voltage comparator, not experimental validation or an additional BoP loss.
Installed PEM CAPEX is 1,970 EUR/kW, fixed OPEX 3%/year, the horizon 15 years,
the real discount rate 8%, and the assumed H2 sale price 7 EUR/kg.

Beginning-of-life LCOH equals annualized initial CAPEX plus operating and energy
costs, divided by annual H2. NPV discounts H2 revenue minus costs and subtracts
initial CAPEX once. Exportable PV is charged its EAC monthly opportunity-cost
proxy. Curtailed PV has zero opportunity cost in the central case. Full PV-park
investment is outside the incremental PEM boundary.

## Results at beginning of life

| Case | H2 (kg/year) | Utilization (%) | LCOH (EUR/kg) | NPV (MEUR) |
| --- | --- | --- | --- | --- |
| A: Curtailment-only | 38,950 | 14.86 | 11.882 | -1.628 |
| B: Conditional economic selection | 107,825 | 41.33 | 8.099 | -1.014 |
| C: Best non-zero BESS diagnostic | 108,457 | 41.59 | 8.146 | -1.063 |

All three A/B/C screening NPVs are negative at the assumed offtake price.
The Strategy C row is a non-zero diagnostic, not an investment recommendation.

## Strategy B change and interpretation

| Case | Baseline (%) | Cap (EUR/MWh) | Grid (MWh/year) | LCOH (EUR/kg) |
| --- | --- | --- | --- | --- |
| Economic selection at fixed baseline | 20 | 75 | 0.476 | 8.099 |
| Original 20% grid-active comparator, not optimum | 20 | 200 | 53.118 | 8.113 |
| Raised 30% grid-active comparator, not optimum | 30 | 200 | 82.937 | 8.157 |
| PV-priority reference without grid service | 0 | Not applicable | 0.000 | 7.998 |

The tested-cap minimum is conditional on a 20% baseline and an existing import
service. Identical dispatch at nearby low caps makes its numerical threshold
weak evidence for a unique optimum. The raised 30%/200 EUR/MWh comparator uses
more grid energy without increasing the PEM rating or exceeding physical limits.
It is technically admissible in this model and economically worse than the
conditional economic selection. PV-priority operation without grid service is
also reported with zero import demand/fixed charges. Under these assumptions it
is preferable to retaining an import service for negligible annual imports.

The central variable adder is 42.51 EUR/MWh.
Only the regulated network benchmark changed, from 24.70 to 24.50 EUR/MWh,
using CERA 105/2026 as corrected by 117/2026. Supplier margin and imbalance
allowances remain assumptions. This 2026 benchmark is applied across the
observed window as a scenario, not as historical invoicing. Low/central/high
market-cost sensitivities retain separate commercial terms and do not replace
the central case to force grid use.

## Strategy C economic choice

The best central non-zero duration case is 268 kW /
134 kWh. It adds
632 kg H2/year with incremental
NPV -49,303 EUR. The CSV records
Overall economic choice = No BESS and
Value adding = NO. All tested non-zero battery economics
remain screening results and include no capacity-fade or replacement cost.

## Lifecycle sensitivity

| Strategy | Rate (microV/h) | Replacements | Lifecycle LCOH (EUR/kg) |
| --- | --- | --- | --- |
| A: Curtailment-only | 7 | 0 | 12.26 |
| A: Curtailment-only | 23 | 1 | 16.61 |
| A: Curtailment-only | 50 | 4 | 20.57 |
| B: PV-priority + grid-baseline | 7 | 1 | 9.16 |
| B: PV-priority + grid-baseline | 23 | 5 | 12.26 |
| B: PV-priority + grid-baseline | 50 | 13 | 16.50 |
| C: Strategy B + residual-curtailment BESS | 7 | 1 | 9.14 |
| C: Strategy B + residual-curtailment BESS | 23 | 5 | 12.22 |
| C: Strategy B + residual-curtailment BESS | 50 | 13 | 16.45 |

The 23 and 50 microV/h cases are linear stress tests, not calibrated MW-scale
lifetime predictions. A stack is exhausted at the first of 30,000 operating
hours or a 10% relative voltage/SEC rise. Replacement resets the stack only.
The existing terminal-retirement rule may stop production near the horizon
instead of installing a replacement. This is a modeling rule, not an optimized
replacement policy. BoP ageing is not quantified.

## Limitations and next evidence

- Import demand/fixed charges, supplier terms and offtake are unquoted assumptions.
- TMY/DAM clock alignment and hourly-to-half-hour PV mapping are approximations.
- Four isolated daytime source values are interpolated by an unconfirmed
  preprocessing assumption. Clipping alone adds 0.434 MWh/year and interpolation
  adds 23.682 MWh/year, giving prepared dispatch PV of 19,198.274 MWh/year.
  The original values and no-interpolation A/B sensitivity are exported.
- Minimum load, starts, standby, ramping and degradation need OEM calibration.
- Battery costs retain EUR2020 catalogue values without escalation. This mixes
  cost reference years and makes Strategy C screening economics optimistic.
- Battery fade, detailed HVAC, replacements, compression, H2 storage and transport
  are excluded. Water treatment/purchase costs are also outside the boundary.
- Tax, debt, finance structure, full PV CAPEX and certification are not modeled.

A commercial update requires an actual PPA, supplier tariff, PEM/BESS installed
quotes and warranties, H2 handling design and a secured offtake profile. These
gaps do not prevent completing a transparent portfolio screening release.

Sources: [TSOC DAM exports](https://tsoc.org.cy/competitive-electricity-market/dam-volume-prices-graph/),
[CERA 105/2026](https://www.cera.org.cy/el-gr/apofasis/details/apofasi-105-2026),
the references in README.md and results/tables/assumptions_register.csv.
Numerical evidence: final_screening_summary.csv, strategy_b_reporting_cases.csv,
strategy_c_bess_best_nonzero_case.csv and stack_lifecycle_sensitivity.csv.
