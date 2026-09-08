# PV Curtailment-to-Hydrogen Techno-Economic Model

## Version 1.2

Python-based techno-economic model for investigating the use of curtailed photovoltaic electricity for green hydrogen production using a PEM electrolyzer.

The model represents a 10 MWp PV plant in Cyprus and evaluates how grid export constraints, PEM electrolyzer sizing, part-load operation and minimum-load requirements affect:

- curtailed PV energy
- hydrogen production
- PEM utilization
- curtailment recovery
- specific electricity consumption
- energy and exergy efficiency
- levelized cost of hydrogen
- project NPV

Version 1.2 extends the original model from a simplified constant-efficiency representation to a load-dependent PEM operating model.

---

## 1. Research Question

Can otherwise-curtailed PV electricity be economically converted into green hydrogen, and how should the PEM electrolyzer be sized relative to the magnitude and temporal distribution of curtailment?

The model focuses specifically on hydrogen production from curtailed PV electricity rather than assuming continuous dedicated renewable electricity supply.

This distinction is important because an electrolyzer supplied only by curtailment can experience low annual utilization and highly variable loading.

---

## 2. System Configuration

Base PV system:

- PV capacity: 10 MWp
- Location: Cyprus
- Annual PV generation: approximately 16.09 GWh
- PV capacity factor: approximately 18.36%
- Simulation resolution: hourly
- Simulation period: 8760 hours

Selected base case:

- PEM electrolyzer: 1 MW
- Grid export limit: 6 MW
- Curtailed PV energy: approximately 930.9 MWh/year
- Hydrogen production: approximately 14,619 kg/year
- Water consumption: approximately 131.6 m3/year

The grid export limit creates the curtailment available to the electrolyzer.

---

## 3. Curtailment Model

For each hourly timestep, PV generation above the grid export limit is classified as curtailed power.

Conceptually:

PV generation -> Grid export up to grid limit -> Excess PV -> PEM electrolyzer

The electrolyzer can only consume curtailed electricity subject to:

1. its rated power capacity
2. its minimum operating load

This allows the model to distinguish between technically available curtailment and curtailment that can actually be accepted by the electrolyzer.

---

## 4. PEM Part-Load Model

Version 1.2 replaces the original constant specific electricity consumption assumption with a load-dependent PEM model.

Hydrogen production therefore depends on electrolyzer load fraction.

The base model assumes:

- PEM minimum operating load: 15% of rated capacity
- load-dependent specific electricity consumption
- zero hydrogen production below the minimum operating threshold

For the selected 1 MW PEM / 6 MW grid-limit case:

- load-weighted exergy efficiency: approximately 61%
- energy-weighted gross specific electricity consumption: approximately 53.4 kWh/kg H2

This is more realistic than assuming identical conversion performance at every operating point.

---

## 5. PEM Sizing Study

PEM capacities investigated:

0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 4.0 and 5.0 MW.

The sizing analysis demonstrates an important trade-off.

A small electrolyzer:

- achieves relatively high utilization
- cannot absorb large curtailment peaks
- rejects energy above its rated capacity

A large electrolyzer:

- can absorb larger curtailment peaks
- operates at lower average load
- increasingly loses low-power curtailment events below its minimum operating threshold

Therefore, maximizing PEM capacity does not necessarily maximize useful curtailment recovery or hydrogen production.

---

## 6. Curtailment Recovery

For the base 6 MW grid-export limit, calculated curtailment recovery includes approximately:

| PEM Size | Curtailment Recovery |
|---:|---:|
| 0.1 MW | 11.8% |
| 0.5 MW | 52.6% |
| 1.0 MW | 83.9% |
| 1.5 MW | 94.6% |
| 2.0 MW | 96.4% |
| 2.5 MW | 95.6% |
| 3.0 MW | 92.3% |
| 4.0 MW | 85.3% |
| 5.0 MW | 76.1% |

Curtailment recovery peaks around 2 MW in the investigated case and subsequently decreases.

This non-monotonic behaviour is a central result of Version 1.2.

---

## 7. Rejected Curtailment Diagnostics

Version 1.2 explicitly divides unrecovered curtailment into two mechanisms:

### Rejected Above PEM Capacity

Occurs when:

curtailed power > PEM rated power

This dominates when the electrolyzer is undersized.

### Rejected Below Minimum Load

Occurs when:

curtailed power < minimum PEM operating power

This becomes increasingly important as PEM capacity increases because the absolute minimum operating power rises with electrolyzer size.

The diagnostic therefore explains why simply installing a larger PEM electrolyzer does not guarantee greater annual curtailment recovery.

---

## 8. Minimum-Load Sensitivity

The model tests alternative minimum operating thresholds:

- 5%
- 10%
- 15%
- 20%

The 15% threshold is used as the base-case modelling assumption.

The sensitivity analysis quantifies how electrolyzer turndown capability affects curtailment recovery across the full PEM sizing range.

This is particularly important for highly intermittent curtailment-driven operation.

---

## 9. Economic Model

The techno-economic analysis includes:

- PEM CAPEX
- annual OPEX
- project lifetime
- discount rate
- hydrogen selling price
- simplified LCOH
- annualized LCOH
- NPV

PEM CAPEX scenarios:

- €700/kW
- €1,000/kW
- €1,300/kW
- €1,970/kW

For the selected 1 MW PEM case at €1,000/kW:

- Simple LCOH: approximately €6.61/kg H2
- Simplified annualized LCOH: approximately €10.04/kg H2

The difference illustrates the economic penalty created by low electrolyzer utilization.

---

## 10. Grid Export Limit Sensitivity

Grid export limits from 8 MW down to 2 MW are investigated.

Reducing the grid export limit creates progressively more curtailed energy and therefore increases the number of hours during which the PEM electrolyzer can operate.

For the selected 1 MW PEM system:

| Grid Limit | Curtailed Energy | H2 Production |
|---:|---:|---:|
| 8 MW | 5.3 MWh | 98 kg/year |
| 7 MW | 143.6 MWh | 2,559 kg/year |
| 6 MW | 930.9 MWh | 14,619 kg/year |
| 5 MW | 2,335.8 MWh | 25,620 kg/year |
| 4 MW | 4,234.3 MWh | 34,485 kg/year |
| 3 MW | 6,520.7 MWh | 41,303 kg/year |
| 2 MW | 9,245.6 MWh | 49,384 kg/year |

This demonstrates that curtailment severity is one of the dominant drivers of electrolyzer utilization and hydrogen economics.

---

## 11. Battery vs Hydrogen Conversion Efficiency

A limited energy-conversion comparison is included using equal accepted electrical input.

For the selected case:

- common electrical input: approximately 780.8 MWh
- battery recovered energy at 90% assumed round-trip efficiency: approximately 702.7 MWh
- hydrogen stored energy: approximately 487.3 MWh LHV
- hydrogen conversion energy retention: approximately 62.4%

This is only an energy-conversion comparison.

It is not a full techno-economic comparison between battery storage and hydrogen.

Battery CAPEX, degradation, duration, cycling, dispatch value and grid-service revenues are intentionally excluded from Project 1 and are reserved for a separate storage comparison study.

---

## 12. Main Engineering Findings

The model produces four important conclusions.

### 1. Maximum electrolyzer size is not automatically optimal

Increasing PEM capacity initially increases recovered curtailment, but excessive sizing increases rejection of low-power curtailment events below the minimum operating threshold.

### 2. Hydrogen production and minimum LCOH are different objectives

The PEM size that maximizes annual hydrogen production does not necessarily minimize hydrogen production cost.

### 3. Utilization is critical

A curtailment-only electrolyzer can receive very cheap or otherwise-unused electricity while still producing expensive hydrogen because the capital-intensive electrolyzer operates for relatively few equivalent full-load hours.

### 4. PEM sizing is a multi-objective problem

A technically meaningful design must balance:

- hydrogen production
- curtailment recovery
- electrolyzer utilization
- CAPEX
- LCOH
- minimum-load behaviour

---

## 13. Current Model Limitations

The current model is intended as a first-order research and portfolio model rather than an investment-grade feasibility study.

Current limitations include:

- simplified literature-based PEM part-load curve
- no stack degradation
- no stack replacement
- no hydrogen compression
- no hydrogen storage system
- no detailed Balance-of-Plant model
- no fixed Balance-of-Plant cost scaling
- no economies of scale
- no dynamic start-up or shutdown model
- no real Cyprus electricity-market dispatch
- no Day-Ahead Market price optimization

Consequently, the calculated LCOH and NPV results should be interpreted as scenario outputs rather than project-development forecasts.

---

## 14. Version 1.2 Improvements

Compared with the initial model, Version 1.2 adds:

- load-dependent PEM specific electricity consumption
- explicit PEM minimum operating load
- minimum-load sensitivity from 5% to 20%
- expanded PEM sizing from 0.1 to 5 MW
- curtailment recovery analysis
- rejected-curtailment diagnostics
- separation of below-minimum-load and above-capacity losses
- load-weighted energy and exergy efficiency
- improved PEM utilization calculations
- annualized LCOH analysis
- expanded grid-limit sensitivity
- improved engineering interpretation of PEM oversizing

---

## 15. Planned Next Development

The next model extension will investigate a hybrid electrolyzer operating strategy.

Instead of operating exclusively during curtailment events, the electrolyzer may operate at a baseline load during non-curtailment periods and increase production when curtailed PV electricity becomes available.

This will allow investigation of the trade-off between:

- higher electrolyzer utilization
- purchased electricity cost
- curtailment recovery
- hydrogen production
- LCOH
- NPV

A subsequent extension will compare alternative electrolyzer technologies, including:

- PEM
- alkaline
- AEM
- SOEC

The comparison will focus on operating range, minimum load, efficiency, dynamic response and suitability for renewable-energy integration.

---

## 16. Repository Structure

```text
PV_Hydrogen_Project/
|
|-- main.py
|-- README.md
|-- LICENSE
|-- data/
|-- figures/

## 17. Requirements

Main Python packages:

pandas
numpy
matplotlib

Run the model using:

python main.py

---

## 18. Status

Current release: v1.2

Project status: active development.

Next milestone: Hybrid Operating Strategy Sensitivity.