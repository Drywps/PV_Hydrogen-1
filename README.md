# Curtailment-to-Hydrogen Techno-Economic Model for Cyprus

PV curtailment in Cyprus escalated sharply from 3.3% in 2022 to 13.4% in 2023, 29% in 2024, and a record 47% in 2025 (Livera et al., 2026), indicating that flexibility solutions such as batteries, demand response, and hydrogen storage will become increasingly important.

This project evaluates whether curtailed photovoltaic electricity from a 10 MWp PV plant in Cyprus can be converted into green hydrogen using PEM electrolysis. 

## Model Scope

The model uses hourly PVGIS production data and applies a grid export limit to estimate curtailed electricity. The curtailed energy is then routed to a PEM electrolyzer, and the resulting hydrogen production, utilization, LCOH and NPV are calculated.

## How to run
pip install -r requirements.txt
python main.py

## PVGIS data
latitude 35.141, longitude 33.415, 10,000 kWp, slope 28°, azimuth 0°, system losses 14%
source: https://re.jrc.ec.europa.eu/pvg_tools/en/tools.html
Download the hourly time series using the parameters above, then place the resulting CSV file in the same folder as main.py.

## Base Case

- PV plant size: 10 MWp
- Location: Cyprus
- Grid export limit: 6 MW
- PEM electrolyzer size: 1 MW
- PEM CAPEX: 1,000 €/kW
- Electrolyzer consumption: 52 kWh/kg H₂
- Water consumption: 9 L/kg H₂
- Project lifetime: 15 years
- Discount rate: 8%
- OPEX: 3% of PEM CAPEX/year
- Hydrogen sale price: 6 €/kg

## Key Results

- Annual PV generation: 16,085.69 MWh
- Annual H₂ production, selected case: 15.1 tonnes/year (17.9 t/year ceiling reached at PEM ≥ 2 MW)
- PEM utilization: 8.99% , (1,127 operating hours/year)
- Simple LCOH: 6.38 €/kg H₂ , (undiscounted)
- Discounted LCOH: 9.70 €/kg H₂ , (8%, 15 yr)
- NPV at 6 €/kg H₂: -€479,165
- Break-even H₂ price: approximately €9.5/kg
- Hydrogen production saturates above approximately 2 MW PEM
- Battery energy retention: 90%
- Hydrogen energy retention: 54%


The discounted LCOH is now a direct model output (`calculate_discounted_lcoh()`),
using the same Capital Recovery Factor already applied elsewhere in the model
for NPV reporting — not a separate discounting method bolted on for LCOH alone.
Earlier versions of this project only estimated the discounted figure by hand
(8.5–9.5 €/kg); the implemented calculation puts it slightly higher, at 9.70
€/kg, which is a useful reminder that back-of-envelope corrections are a
starting point, not a substitute for running the actual number.

**Bottom line: hydrogen-from-curtailment is technically feasible but
economically marginal under moderate curtailment**, and notably more marginal
once discounting is applied properly.

## Figures

**Hydrogen production saturates above ~2 MW PEM.** Beyond this point, the
electrolyser is oversized relative to available curtailed power, and
utilization drops sharply for no further production gain.

![PEM size vs hydrogen production](figures/figure03_pem_vs_h2.png)

**LCOH falls fast as the grid export limit tightens** (more curtailed
electricity available to the electrolyser), but the gap between simple and
discounted LCOH widens at every point — discounting is not a minor correction,
it changes the economic picture materially across the whole sensitivity range.

![LCOH vs grid export limit, simple and discounted](figures/figure07_lcoh_vs_grid_limit.png)

**NPV is negative across most of the low-curtailment / low-H2-price region**,
and only turns positive with tighter grid limits and higher hydrogen prices —
both of which push the system further from a "using otherwise-wasted
electricity" framing and closer to a dedicated-production framing.

![NPV heatmap across grid limit and hydrogen price](figures/figure10_npv_heatmap.png)

**Utilization collapses as PEM size increases past the curtailment ceiling**,
which is the core economic driver of this model — a large asset sitting idle
most of the year, not the price of the input electricity.

![PEM size vs utilization](figures/figure04_pem_vs_utilization.png)

Additional figures (monthly production seasonality, CAPEX intensity, NPV vs
hydrogen price, NPV vs grid limit, raw hourly/two-day PV output) are in
`figures/`.


## Main Conclusion

Hydrogen-from-curtailment is technically feasible but economically marginal under the base-case assumptions. The main weakness is not the availability of solar energy, but the low utilization of the electrolyzer when it operates only on curtailed electricity.



## Model Limitations

This is a first-order techno-economic model. It does not yet include:

- PEM partial-load efficiency
- Stack degradation
- Stack replacement
- Hydrogen compression
- Hydrogen storage
- Real Cyprus electricity market prices
- Dynamic dispatch using MCP / DAM price signals

This study evaluates a representative 10 MWp PV plant rather than the entire Cyprus power system.

## Note on PEM vs AEM

This model uses **PEM electrolysis** as the baseline technology, chosen
because its cost and performance parameters are well documented in the
literature cited above. It does not currently model **AEM electrolysis**,
which is a closer match to some of the technologies used in system-level
green hydrogen research groups. A PEM-to-AEM cost and efficiency comparison is
a natural extension of this model — see Future Work.

## Future Work

The next model upgrades are:

1. PEM vs AEM electrolysis comparison
2. Add PEM stack replacement
3. Add hydrogen compression and storage
4. Add real Cyprus electricity market price signals
5. Add PEM partial-load efficiency using literature-based curves
6. Compare PEM electrolysis against the storage capacities recommended by TSOC:
- 80 MW / 240 MWh
- 200 MW / 400 MWh


## References

- IEA (2024), *Global Hydrogen Review 2024*, IEA, Paris.
  https://www.iea.org/reports/global-hydrogen-review-2024
  — used for the dedicated PV-to-H2 LCOH benchmark range (3.5–6.0 €/kg, Southern Europe / MENA) referenced in the benchmark comparison.

- IRENA, *Green Hydrogen Cost Reduction* and related electrolyser cost analyses. 
  https://www.irena.org
  — used for electrolyser CAPEX scenario ranges (700–1,300 €/kW).

- PVGIS (Joint Research Centre), hourly PV generation time series for 35.141°N, 33.415°E (Cyprus), crystalline silicon, 10 MWp, 28° tilt.
  https://re.jrc.ec.europa.eu/pvg_tools/en/

- Livera, A.; Herodotou, P.; Marangis, D.; Makrides, G.; Georghiou, G.E. (2026),
  "Case Study of a Photovoltaic (PV)-Powered, Battery-Integrated System in
  Cyprus," Energies, 19(10), 2402. https://doi.org/10.3390/en19102402
— source of the Cyprus RES curtailment trajectory (3.3% in 2022 to 47% in 2025) cited above.
