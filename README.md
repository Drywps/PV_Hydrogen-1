# Curtailment-to-Hydrogen Techno-Economic Model for Cyprus

PV Curtailment ratio in Cyprus increased from 11.65% (2022) to 47.44% (2025), indicating that flexibility solutions such as batteries demand response, and hydrogen storage will become increasingly important.

This project evaluates whether curtailed photovoltaic electricity from a 10 MWp PV plant in Cyprus can be converted into green hydrogen using PEM electrolysis. 

## Model Scope

The model uses hourly PVGIS production data and applies a grid export limit to estimate curtailed electricity. The curtailed energy is then routed to a PEM electrolyzer, and the resulting hydrogen production, utilization, LCOH and NPV are calculated.

## How to run
pip install -r requirements.txt
python main.py

## PVGIS data
latitude 35.141, longitude 33.415, 10000 kWp, tilt 14°, azimuth 28°/0°
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
- Annual H₂ production, selected case: 15.1 tonnes/year
- PEM utilization: 8.99%
- Simple LCOH: 6.38 €/kg H₂
- NPV at 6 €/kg H₂: -€479,165
- Break-even H₂ price: approximately €9.5/kg
- Hydrogen production saturates above approximately 2 MW PEM
- Battery energy retention: 90%
- Hydrogen energy retention: 54%

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
- Full discounted LCOH

This study evaluates a representative 10 MWp PV plant rather than the entire Cyprus power system.

## Future Work

The next model upgrades are:

1. Add discounted LCOH calculation
2. Add PEM stack replacement
3. Add hydrogen compression and storage
4. Add real Cyprus electricity market price signals
5. Add PEM partial-load efficiency using literature-based curves
6. Compare PEM electrolysis against the storage capacities recommended by TSOC:
- 80 MW / 240 MWh
- 200 MW / 400 MWh