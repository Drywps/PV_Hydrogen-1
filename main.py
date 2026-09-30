#STRUCTURE
# 0. Description
# 1. Imports
# 2. Configuration & Assumptions
# 3. Data Loading Functions
# 4. PV Analysis Functions
# 5. Curtailment Functions
# 6. PEM Functions
# 7. Economic Functions
# 8. Plotting Functions
# 9. Main Execution
#10. Plot Execution
#11. Conclusions



# =========================================
# Description
# =========================================

#This project evaluates PV-to-hydrogen operation under three dispatch strategies:
#(A) curtailment-only PEM operation, with no grid electricity and no displacement
#    of otherwise-exportable PV, and
#(B) PV-priority + grid-baseline operation, where available PV supplies the PEM
#    first and grid electricity only fills the deficit to the selected baseline.
#(C) Strategy B plus a small LFP battery charged only from residual curtailed PV
#    and discharged only to the PEM. Battery grid arbitrage and export are excluded.
#All strategies use PEM electrolysis and preserve an explicit power balance.

#The model combines:
#- PVGIS 2023 hourly production data and PVsyst TMY hourly AC output
#- Grid export constraints
#- Curtailment estimation
#- PEM electrolyzer sizing
#- LCOH calculations
#- NPV analysis
#- Sensitivity studies

# Version history:
#   v1.2 - PEM sizing + minimum-load sensitivity (curtailment-only operation)
#   v1.3 - Two dispatch strategies:
#          STRATEGY A: only otherwise-curtailed PV supplies the PEM.
#          STRATEGY B: curtailed PV supplies the PEM first; grid electricity
#          fills only the deficit required to reach the selected baseline.
#          Electricity-price sensitivity is applied only to purchased grid energy.
#          Otherwise-exportable PV diverted to PEM carries a month-specific 2023
#          opportunity cost; curtailed PV carries zero opportunity cost.
#   v1.4 - PVsyst integration and PVGIS-vs-PVsyst validation
#          Adds a selectable PV data source, detailed PVsyst hourly AC output,
#          source-comparison metrics/figures, and preserves the v1.3 PEM/economic model.
#   v1.5 - MW-scale PEM system SEC update
#          Replaces the earlier Crespi-based gross SEC curve with the 1.25 MW
#          system-level SEC curve reported by Tran et al. (2026), Table 6.
#          The 15% SEC point is retained as an explicit linear extrapolation
#          from the 25%-35% Tran data; minimum PEM load remains 15%.
#          Restores v1.4-style results/figures and results/tables output structure
#          and reintroduces multi-objective fine PEM sizing / Pareto exports.
#          Final reporting reference is assigned dynamically from the
#          multi-objective balanced design.
#   v1.6 - Literature-bounded stack lifecycle screening
#          Adds operating-hour and relative voltage/SEC end-of-life triggers,
#          discounted stack-replacement cash flows, stack-only performance
#          reset, and explicit retention of the Balance-of-Plant state.
#   v1.7 - Cyprus DAM-linked Strategy B
#          Replaces the primary fixed-price Strategy B design point with
#          30-minute Day-Ahead Market prices published by TSOC for the
#          359-day period 2025-10-01 to 2026-09-24. PV remains first priority;
#          grid top-up to the selected baseline is allowed only below a tested
#          all-in price cap. Results are explicitly annualised by 365/359.
#   v1.8 - Assumption consolidation and scope control
#          Records the fixed PV site, adopts 10 L/kg H2 Siemens water use,
#          moves the central installed PEM CAPEX to 1,970 EUR/kW, retains
#          1,000 EUR/kW only as a low-cost sensitivity, and removes the
#          out-of-scope battery energy-retention calculation.
#   v1.9 - Fixed design point and battery-flexibility diagnostic
#          Locks all reporting to the 1.60 MW conceptual PEM design, keeps the
#          multi-objective optimum as a diagnostic only, updates the export-price
#          proxy to the supplied 2025-2026 EAC 11-kV data, and adds Strategy C.
#          Strategy C tests 268 kW LFP storage at 0.5, 1, 2 and 4 hours. It is a
#          screening diagnostic, not a bankable battery investment model.
#   v1.9.1-final-screening - Economic-selection and reporting correction
#          Selects the Strategy B headline cap automatically by minimum LCOH at
#          the fixed 20% baseline, retains 200 EUR/MWh only as a labeled
#          grid-active comparator, and distinguishes the best non-zero BESS case
#          from the true economic choice of no battery when every increment is
#          negative.
#          Refines tested import caps, adds a 30% grid-active baseline comparator
#          and a PV-priority reference without grid service, updates the network
#          benchmark to CERA 105/2026 as corrected by 117/2026, and fixes half-hour
#          representative-day energy accounting.

# =========================================
# IMPORTS
# =========================================

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import atexit
import io
import os
import sys
from datetime import datetime

MODEL_VERSION = "v1.9.1-final-screening"


def save_figure_png(figure, filename, **kwargs):
    """Commit complete PNG bytes atomically, including on managed filesystems."""
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", **kwargs)
    payload = buffer.getvalue()
    if payload[-12:] != b"\x00\x00\x00\x00IEND\xaeB`\x82":
        raise ValueError(f"Incomplete PNG render: {filename}")
    temporary = filename + ".tmp"
    with open(temporary, "wb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, filename)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, "results")
FIGURES_DIR = os.path.join(RESULTS_DIR, "figures")
TABLES_DIR = os.path.join(RESULTS_DIR, "tables")
os.makedirs(FIGURES_DIR, exist_ok=True)
os.makedirs(TABLES_DIR, exist_ok=True)


# =========================================
# TERMINAL OUTPUT CAPTURE
# =========================================

class TeeStream:
    """Write the same text to the terminal and to a UTF-8 text file."""

    def __init__(self, terminal_stream, log_stream):
        self.terminal_stream = terminal_stream
        self.log_stream = log_stream

    def write(self, text):
        self.terminal_stream.write(text)
        self.log_stream.write(text)
        self.log_stream.flush()
        return len(text)

    def flush(self):
        self.terminal_stream.flush()
        self.log_stream.flush()

    def isatty(self):
        return self.terminal_stream.isatty()

    @property
    def encoding(self):
        return getattr(self.terminal_stream, "encoding", "utf-8")


TERMINAL_OUTPUT_PATH = os.path.join(BASE_DIR, "terminal_output.txt")
_original_stdout = sys.stdout
_original_stderr = sys.stderr
_terminal_log_file = open(
    TERMINAL_OUTPUT_PATH,
    mode="w",
    encoding="utf-8",
    buffering=1
)

sys.stdout = TeeStream(_original_stdout, _terminal_log_file)
sys.stderr = TeeStream(_original_stderr, _terminal_log_file)


def close_terminal_output_file():
    """Restore the original streams and safely close the terminal log."""
    if sys.stdout is not _original_stdout:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            sys.stdout = _original_stdout
            sys.stderr = _original_stderr

    if not _terminal_log_file.closed:
        _terminal_log_file.close()


atexit.register(close_terminal_output_file)

print("=" * 80)
print("PV-HYDROGEN MODEL, COMPLETE TERMINAL OUTPUT")
print(f"Model version: {MODEL_VERSION}")
print(f"Run started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Output file: {TERMINAL_OUTPUT_PATH}")
print("=" * 80)

# =========================================
# CONFIGURATION & ASSUMPTIONS
# =========================================

# Fixed project site used by the PVGIS/PVsyst resource inputs. These values are
# metadata for traceability; they do not replace the coordinates embedded in
# the source files.
SITE_LATITUDE_DEG = 35.141
SITE_LONGITUDE_DEG = 33.415
SITE_ELEVATION_M = 142
PVGIS_RADIATION_DATABASE = "PVGIS-SARAH3"

# PV data sources
PVGIS_FILENAME = os.path.join(
    BASE_DIR,
    "data",
    "PVGIS_2023.csv"
)

PVSYST_FILENAME = os.path.join(
    BASE_DIR,
    "data",
    "PVsyst_TMY_5.3.csv"
)

EES_PERFORMANCE_MAP_PATH = os.path.join(
    BASE_DIR,
    "ees",
    "PEM_EES_performance_map.csv"
)

# Official TSOC / DSMK Day-Ahead Market export assembled from the weekly
# spreadsheets published at:
# https://tsoc.org.cy/competitive-electricity-market/dam-volume-prices-graph/
# The file contains 17,230 published 30-minute observations covering 359 days.
# Two civil-clock intervals are absent on 2026-03-29 in the official export.
DAM_PRICE_FILENAME_CANDIDATES = [
    os.path.join(BASE_DIR, "DSMK_DAM_2025-10-01_to_2026-09-24.xlsx"),
    os.path.join(BASE_DIR, "data", "DSMK_DAM_2025-10-01_to_2026-09-24.xlsx"),
]
DAM_OBSERVED_DAYS = 359
DAM_ANNUALIZATION_FACTOR = 365.0 / DAM_OBSERVED_DAYS
DAM_INTERVAL_HOURS = 0.5

# v1.4 default: use PVsyst as the active production profile for the full
# downstream PEM/curtailment/economic analysis. Set the environment variable
# PV_DATA_SOURCE=PVGIS to reproduce the v1.3 PV source with the same code.
PV_DATA_SOURCE = os.getenv("PV_DATA_SOURCE", "PVSYST").strip().upper()
if PV_DATA_SOURCE not in {"PVGIS", "PVSYST"}:
    raise ValueError("PV_DATA_SOURCE must be either 'PVGIS' or 'PVSYST'.")
#
# OEM-confirmed public plant-water comparator from Siemens Energy. This is
# water consumption, not a feed-water-quality specification and not an RO/EDI
# design basis. The earlier 9 L/kg stoichiometric screening value is retired.
water_liters_per_kg_h2 = 10.0
# Published 2025-2026 EAC RES purchase-price proxy used to value electricity
# that could have been exported but is instead diverted to the electrolyser.
#
# Connection level selected for this generic project: 11 kV (Medium Voltage).
# Source units were euro-cent/kWh and are converted here to EUR/MWh:
#   1 euro-cent/kWh = 10 EUR/MWh.
#
# IMPORTANT:
# - This is NOT a Cyprus DAM price.
# - It is used only as a historical monthly opportunity-cost proxy for
#   otherwise-exportable PV energy.
# - Curtailed PV has zero export opportunity cost.
PV_EXPORT_PRICE_PROXY_11KV_EUR_PER_MWH = {
    1: 107.96,   # January 2026
    2: 107.43,   # February 2026
    3: 107.43,   # March 2026
    4: 107.43,   # April 2026
    5: 107.43,   # May 2026
    6: 107.43,   # June 2026
    7: 107.30,   # July 2026
    8: 107.30,   # August 2026
    9: 107.30,   # September 2026
    10: 107.96,  # October 2025 proxy from supplied EAC record
    11: 107.96,  # November 2025 proxy from supplied EAC record
    12: 107.96,  # December 2025 proxy from supplied EAC record
}

# Legacy compatibility scalar only. Current calculations use the
# monthly series above.
pv_export_price_eur_per_mwh = float(
    sum(PV_EXPORT_PRICE_PROXY_11KV_EUR_PER_MWH.values())
    / len(PV_EXPORT_PRICE_PROXY_11KV_EUR_PER_MWH)
)

reference_european_green_h2_price_eur_per_kg = 7.0
hydrogen_sale_price = reference_european_green_h2_price_eur_per_kg
project_lifetime_years = 15
grid_limits_mw = [8, 7.5, 7, 6.5, 6, 5.5, 5, 4.5, 4, 3.5, 3, 2.5, 2]
###
# PEM economic assumptions
# Central installed-cost screening case. The former 1,000 EUR/kW value is kept
# only as a low-cost OEM comparator in the sensitivity, because a public 5 MW
# package price cannot be transferred directly to a conceptual 1.60 MW plant.
pem_capex_per_kw = 1970
pem_opex_fraction = 0.03

# Literature benchmark, European Hydrogen Observatory 2024
eho_pem_capex_per_kw = 1970
eho_pem_opex_per_kw_year = 64
pem_opex_scenarios_per_kw_year = [30, 50, 64]
# The central case uses the 1,970 EUR/kW European installed-cost benchmark.
# DOE's 1,500/2,000/2,500 USD/kW range is retained in the assumptions register
# as a separate currency benchmark and is not mixed directly into EUR cash flow.

# CAPEX sensitivity scenarios
pem_capex_scenarios = [1000, 1500, 1970, 2500]
###

pem_sizes_mw = [
    0.1, 0.2, 0.3, 0.4, 0.5,
    0.6, 0.8, 1.0, 1.2, 1.5,
    2.0, 2.5, 3.0, 4.0, 5.0
]

hydrogen_price_scenarios = [2, 3, 4, 6, 8, 10]
h2_lower_heating_value_kwh_per_kg = 33.33
discount_rate = 0.08
###

# =========================================
# v1.3 CONFIGURATION: HYBRID OPERATING STRATEGY SENSITIVITY
# =========================================
#
# Roadmap: v1.2 (PEM sizing + minimum load) -> v1.3 (operating strategy) -> v1.4 (PVsyst integration / PV-source validation)
#
# Strategy B offers available PV to the PEM first. Grid electricity fills only
# the deficit to the requested baseline. Displaced PV export is valued through
# an explicit opportunity cost. A zero-baseline Strategy B is intentionally absent.

# Baseline load as a fraction of PEM rated capacity.
baseline_load_fractions = [0.20, 0.30, 0.40,
    0.50, 0.60, 0.80, 1.00]

# Legacy fixed-price sensitivity retained for internal comparison only.
electricity_price_scenarios_eur_per_mwh = list(range(0, 301, 10))

# Primary Strategy B control. The threshold is an ALL-IN variable import-price
# cap, not a raw DAM cap. Grid purchase is allowed when DAM price plus the
# supplier, aggregator, network and levy adder is below the selected cap.
dam_dispatch_price_caps_eur_per_mwh = [
    0.0, 25.0, 50.0, 75.0, 100.0, 110.0, 120.0, 130.0,
    140.0, 150.0, 160.0, 170.0, 180.0, 190.0,
    200.0, 225.0, 250.0, 275.0, 300.0,
]

# Generic screening assumptions for direct market participation through a
# supplier / aggregator at Medium Voltage. These are NOT a supplier quotation.
# The 24.50 EUR/MWh network/system benchmark is CERA 105/2026, corrected by
# 117/2026: MV transmission 9.90 + MV distribution 12.10 + TSO 2.50 +
# ancillary-services tariff 0.00 EUR/MWh. This replaces the legacy 2022 total
# of 24.70. The 2026 benchmark is applied to the whole observed market window
# as a screening scenario, not a reconstruction of historical invoices.
# Supplier/imbalance terms remain assumptions; the zero regulated ancillary
# tariff does not remove imbalance exposure or possible market-settlement costs.
supplier_aggregator_margin_eur_per_mwh = 5.00
imbalance_allowance_eur_per_mwh = 7.50
regulated_network_system_eur_per_mwh = 9.90 + 12.10 + 2.50 + 0.00
pso_charge_eur_per_mwh = 0.51
res_ee_fund_charge_eur_per_mwh = 5.00
grid_variable_adder_eur_per_mwh = (
    supplier_aggregator_margin_eur_per_mwh
    + imbalance_allowance_eur_per_mwh
    + regulated_network_system_eur_per_mwh
    + pso_charge_eur_per_mwh
    + res_ee_fund_charge_eur_per_mwh
)

# Capacity and fixed-charge assumptions for screening only. Strategy B imports
# only the shortfall to baseline, so contracted import capacity is modelled as
# baseline times PEM capacity, not the full PEM rating.
grid_demand_charge_eur_per_kw_year = 25.0
grid_fixed_supply_metering_eur_per_year = 3000.0

# VAT is excluded because it is assumed recoverable by the project company.
# Collateral is treated as working capital, not annual OPEX, until contract
# terms define the amount, duration and financing cost.
recoverable_vat_rate = 0.19
take_or_pay_minimum_mwh_per_year = 0.0
collateral_annual_cost_eur = 0.0
half_hourly_flexible_interruption_allowed = True

market_cost_screening_scenarios = [
    {"Scenario": "Low", "Variable adder (EUR/MWh)": 30.0,
     "Demand charge (EUR/kW-year)": 0.0, "Fixed charge (EUR/year)": 1500.0},
    {"Scenario": "Central", "Variable adder (EUR/MWh)": grid_variable_adder_eur_per_mwh,
     "Demand charge (EUR/kW-year)": grid_demand_charge_eur_per_kw_year,
     "Fixed charge (EUR/year)": grid_fixed_supply_metering_eur_per_year},
    {"Scenario": "High", "Variable adder (EUR/MWh)": 60.0,
     "Demand charge (EUR/kW-year)": 50.0, "Fixed charge (EUR/year)": 6000.0},
]
###
# =========================================
# CYPRUS 2023 INDUSTRIAL ELECTRICITY BENCHMARK
# =========================================

# Generic large industrial PEM consumer assumed at MV/HV level.
#
# CERA reports separate commercial/industrial tariffs for:
#   Tariff 40 = Medium Voltage
#   Tariff 50 = High Voltage
#
# For this generic techno-economic study, a representative midpoint
# between MV and HV industrial electricity prices is used instead of
# modelling a specific supplier or bilateral electricity contract.
#
# This is NOT a Cyprus DAM price.
# The competitive Cyprus Day-Ahead Market was not operational in 2023.
#
# Representative 2023 benchmark:
cyprus_2023_industrial_price_eur_per_mwh = 270.0

# Representative day used for dispatch visualization.
# Change this string to inspect another day.
# Use "AUTO" to select the day with the highest gross no-PEM curtailment.
# Replace with e.g. "2023-05-15" to force a specific day.
dispatch_plot_date = "AUTO"
dispatch_plot_hybrid_baseline_fraction = 0.20

# Specific hybrid design point used for final reporting.
# Sensitivity plots still evaluate all baseline and electricity-price scenarios.
hybrid_design_baseline_fraction = 0.20
hybrid_design_electricity_price_eur_per_mwh = cyprus_2023_industrial_price_eur_per_mwh
# A 200 EUR/MWh cap is retained only as an illustrative grid-active comparator.
# The headline Strategy B cap is selected automatically from the tested caps by
# minimum LCOH at the fixed 20% baseline. This prevents the reporting case from
# being mislabeled as an optimum merely because it shows more grid operation.
hybrid_grid_active_comparator_cap_eur_per_mwh = 200.0
# Raised from 20% to 30% only in the separately labeled grid-active comparator.
# The 1.60 MW rating and 15% minimum are unchanged. More imported energy is not
# evidence of better economics, so the headline economic case remains separate.
hybrid_grid_active_comparator_baseline_fraction = 0.30

# =========================================
# v1.9 STRATEGY C: SMALL BESS FLEXIBILITY DIAGNOSTIC
# =========================================
# User-defined quotation basis. 268 kW is deliberately retained even though
# the PEM design is fixed at 1.60 MW. It equals 16.75% of PEM rating and can
# therefore exceed the model's 15% minimum-load threshold when discharging alone.
BESS_TARGET_POWER_KW = 268.0
BESS_DURATION_HOURS = [0.5, 1.0, 2.0, 4.0]
BESS_ENERGY_KWH = [BESS_TARGET_POWER_KW * duration for duration in BESS_DURATION_HOURS]

# Danish Energy Agency 2025 utility-scale LFP/NMC catalogue inputs. Cost values
# are EUR2020 and the source reference unit is 3 MWh / 1.5 MW, so applying them
# to this sub-MWh C&I study requires an explicit small-project multiplier.
BESS_AC_ROUND_TRIP_EFFICIENCY = 0.91
BESS_STORAGE_LOSS_FRACTION_PER_DAY = 0.001
BESS_TECHNICAL_LIFETIME_YEARS = 15
BESS_CYCLE_LIFE = 5_000.0
BESS_ENERGY_COMPONENT_EUR2020_PER_MWH = 95_000.0
BESS_POWER_COMPONENT_EUR2020_PER_MW = 86_000.0
BESS_OTHER_PROJECT_COST_EUR2020_PER_MWH = 150_000.0
BESS_SMALL_PROJECT_MULTIPLIERS = [1.00, 1.25, 1.50]
BESS_CENTRAL_SMALL_PROJECT_MULTIPLIER = 1.25
BESS_FIXED_OPEX_FRACTION = 0.029

# Residual curtailment is assigned zero value only in the central uncompensated
# case. The 50 and 107.96 EUR/MWh cases expose the dependence on the actual PPA
# or curtailment-compensation regime. 107.96 EUR/MWh is an EAC 11-kV proxy, not
# a guaranteed payment for this PV park's curtailed energy.
BESS_CURTAILMENT_OPPORTUNITY_COSTS_EUR_PER_MWH = [0.0, 50.0, 107.96]
BESS_CENTRAL_CURTAILMENT_OPPORTUNITY_COST_EUR_PER_MWH = 0.0

# Public Huawei comparator. This is a technical mapping only, not a price quote.
HUAWEI_LUNA_CABINET_ENERGY_KWH = 241.0
HUAWEI_LUNA_CABINET_POWER_KW = 108.0
HUAWEI_LUNA_CABINET_MAX_RTE = 0.913
HUAWEI_LUNA_CABINET_STANDBY_KW = 0.150
HUAWEI_LUNA_COMPARATOR_CABINETS = 3

# =========================================
# PROJECT 1.6 LITERATURE-BASED DYNAMIC / DEGRADATION SCREENING
# =========================================
# Sources used directly in the screening calculations:
#
# [DYN-1] Sayed-Ahmed, H., Toldy, A.I., Santasalo-Aarnio, A. (2024),
# "Dynamic operation of proton exchange membrane electrolyzers - Critical
# review", Renewable and Sustainable Energy Reviews 189, 113883.
# DOI: 10.1016/j.rser.2023.113883
# Reported technology-level ranges: PEM warm start <10 s; cold start 5-10 min.
#
# [DEG-1] Zerrougui, I., Li, Z., Hissel, D. (2025),
# "Toward optimal operations of long-lifetime PEM electrolysis: Degradation
# mechanisms, modeling, diagnostics, and control", International Journal of
# Hydrogen Energy 193, 152297. DOI: 10.1016/j.ijhydene.2025.152297
# Literature examples used as voltage-rise sensitivity cases:
# 7 microV/h (low), 23 microV/h (1 A/cm2 example), 50 microV/h (3 A/cm2 example).
# These are NOT calibrated degradation rates for the modeled 1.60 MW stack.
#
# [DYN-2] Badgett, A., Pivovar, B., Ruth, M. (2022), "Operating strategies for
# dispatchable PEM electrolyzers that enable low-cost hydrogen production",
# International Conference on Electrolysis 2021 / NREL presentation.
# Qualitative use: standby raises energy/H2 cost but can avoid on/off cycling.
#
# [DEG-2] Yang, J. et al. (2026), "Linking wind-power fluctuation to degradation
# of PEM water electrolyzer", Applied Energy 424, 128458.
# DOI: 10.1016/j.apenergy.2026.128458
# Accelerated cell-test rates are cited for context only and are deliberately
# NOT transferred directly to this MW-scale annual model.
#
# [DYN-3] Endrodi, B. et al. (2025), "Challenges and Opportunities of the
# Dynamic Operation of PEM Water Electrolyzers", Energies 18, 2154.
# DOI: 10.3390/en18092154
# Qualitative use: dynamic operation, shutdown/start-up and plant-level control
# require system-specific validation.

PEM_WARM_START_MAX_SECONDS = 10.0
PEM_COLD_START_MIN_MINUTES = 5.0
PEM_COLD_START_MAX_MINUTES = 10.0

PEM_VOLTAGE_DEGRADATION_SCENARIOS_UV_PER_H = {
    "Low literature case": 7.0,
    "Central literature screening": 23.0,
    "High linear stress test": 50.0,
}
PEM_CENTRAL_LITERATURE_SCENARIO = "Central literature screening"

# [LIFE-1] Danish Energy Agency / DNV, Technology Data for Renewable Fuels,
# data sheet "80 PEMEC 10 MW (Off. cent.)", current-value column:
#   - stack replacement frequency: 30,000 operating hours
#   - steady-state degradation: 0.3333 % per 1,000 operating hours
#   - electrolyser-stack cost component: 530.33 EUR/kW input
# The catalogue explicitly states that installed stack-replacement costs are
# not included. The cost component is therefore a transparent proxy, not an
# OEM quotation; labour, recommissioning and replacement downtime are omitted.
# The same source states that starts, prolonged standby and steep ramps add
# equivalent load hours, but gives no conversion factors. They are reported
# separately and are not monetised or converted into invented EFLH penalties.
PEM_STACK_REPLACEMENT_HOURS = 30_000.0
PEM_STEADY_DEGRADATION_PCT_PER_1000_H = 0.3333333333333333
PEM_STACK_COST_PROXY_EUR_PER_KW = 530.3326398018484

# [LIFE-2] Arnold et al. (2025), "Cost-optimized replacement strategies for
# water electrolysis systems affected by degradation": several cited studies
# and DOE use a 10% cell-voltage increase as technical EOL. The paper stresses
# that this is not universal. Its own economic base case gives 20% and 7 years,
# but those values are scenario-specific and are retained only as a comparison.
PEM_TECHNICAL_EOL_RELATIVE_VOLTAGE_SEC_RISE_PCT = 10.0
PEM_ECONOMIC_REFERENCE_SEC_RISE_PCT = 20.0

# Selected design points for reporting / plots
selected_pem_mw = 1.60
full_curtailment_benchmark_range_mw = (2.0, 2.5)

# =========================================
# DATA LOADING FUNCTIONS
# =========================================

def load_pvgis_data(filename):
    """Load the original PVGIS 2023 hourly PV output. PVGIS P is in watts."""
    df = pd.read_csv(filename, skiprows=10)
    df["P"] = pd.to_numeric(df["P"], errors="coerce")
    df = df.dropna(subset=["P"]).copy()
    df["time"] = pd.to_datetime(df["time"], format="%Y%m%d:%H%M")
    df["month"] = df["time"].dt.month
    df["source"] = "PVGIS 2023 (SARAH3)"

    if len(df) != 8760:
        raise ValueError(f"PVGIS dataset should contain 8760 hourly rows; found {len(df)}.")
    if (df["P"] < -1e-9).any():
        raise ValueError("Negative PVGIS PV power detected.")

    return df.reset_index(drop=True)


def load_pvsyst_data(filename, calendar_year=2023):
    """
    Load PVsyst hourly ASCII/CSV output and convert E_Grid from kW to W.

    PVsyst TMY uses a generic calendar year (1990 in the exported file). For
    downstream monthly opportunity-cost accounting, the month/day/hour are
    mapped onto calendar year 2023. This does NOT turn the TMY into 2023 weather;
    it only provides a consistent non-leap calendar index.

    Small negative night-time E_Grid values represent inverter/system night
    consumption. They are retained in P_raw_w for audit, while the PV production
    signal P is clipped at zero before entering PV-to-H2 dispatch.
    """
    df = pd.read_csv(filename, skiprows=10, encoding="utf-8-sig")
    df.columns = [str(c).strip() for c in df.columns]

    required = {"date", "E_Grid"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"PVsyst CSV missing required columns: {sorted(missing)}")

    # The second row after the header contains units; coercion removes it cleanly.
    df["E_Grid"] = pd.to_numeric(df["E_Grid"], errors="coerce")
    df = df.dropna(subset=["E_Grid"]).copy()
    parsed = pd.to_datetime(df["date"].astype(str).str.strip(), format="%d/%m/%y %H:%M")
    df["time"] = parsed.map(lambda x: x.replace(year=calendar_year))

    # Preserve all exported diagnostic variables as numeric where present.
    for col in ["GlobInc", "GlobEff", "EArray", "PR", "GlobHor", "T_Amb", "TArray"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["P_raw_w"] = df["E_Grid"].to_numpy(dtype=float) * 1000.0
    df["P"] = np.maximum(df["P_raw_w"].to_numpy(dtype=float), 0.0)

    # Retain the recovered model's isolated-dropout interpolation assumption.
    # A zero bracketed by two substantial output hours is inferred to be missing.
    # This inference is not confirmed by PVsyst or an observed outage log.
    # All affected records and energy adjustments are disclosed in the audit
    # table; the original input CSV remains unchanged.
    previous_power = df["P"].shift(1)
    next_power = df["P"].shift(-1)
    isolated_dropout = (
        (df["P"] <= 1e-9)
        & (previous_power > 100_000.0)
        & (next_power > 100_000.0)
    )
    df["P_source_repaired"] = isolated_dropout
    df.loc[isolated_dropout, "P"] = (
        previous_power[isolated_dropout] + next_power[isolated_dropout]
    ) / 2.0
    df["month"] = df["time"].dt.month
    df["source"] = "PVsyst TMY 5.3"

    if len(df) != 8760:
        raise ValueError(f"PVsyst dataset should contain 8760 hourly rows; found {len(df)}.")
    if df["P"].isna().any():
        raise ValueError("NaN values detected in PVsyst PV production after parsing.")

    return df.reset_index(drop=True)


def load_pv_sources():
    """Load and validate both v1.4 PV production sources."""
    if not os.path.exists(PVGIS_FILENAME):
        raise FileNotFoundError(f"PVGIS input not found: {PVGIS_FILENAME}")
    if not os.path.exists(PVSYST_FILENAME):
        raise FileNotFoundError(f"PVsyst input not found: {PVSYST_FILENAME}")

    pvgis_df = load_pvgis_data(PVGIS_FILENAME)
    pvsyst_df = load_pvsyst_data(PVSYST_FILENAME)
    return pvgis_df, pvsyst_df


def calculate_pv_opportunity_cost_proxy(df, lost_export_w):
    """
    Calculate annual PV-export opportunity cost using the actual month
    of each PV timestep and the supplied 2025-2026 EAC 11-kV RES
    purchase-price proxy.

    Opportunity cost is applied ONLY to otherwise-exportable PV displaced
    by PEM consumption. Curtailed PV therefore carries zero opportunity cost.
    """
    lost_export_w = np.asarray(lost_export_w, dtype=float)

    assert len(lost_export_w) == len(df), \
        "Lost-export series length does not match PV dataframe."
    assert np.all(lost_export_w >= -1e-9), \
        "Negative lost-export power detected."

    month_array = df["month"].to_numpy(dtype=int)
    price_eur_per_mwh = pd.Series(month_array).map(
        PV_EXPORT_PRICE_PROXY_11KV_EUR_PER_MWH
    ).to_numpy(dtype=float)

    assert not np.isnan(price_eur_per_mwh).any(), \
        "Missing monthly PV export opportunity-cost proxy."

    # PV input is hourly. W x 1 h / 1e6 = MWh.
    hourly_lost_export_mwh = lost_export_w / 1_000_000.0
    hourly_cost_eur = hourly_lost_export_mwh * price_eur_per_mwh

    detail = pd.DataFrame({
        "Month": month_array,
        "Lost Export Energy (MWh)": hourly_lost_export_mwh,
        "Opportunity Cost (€)": hourly_cost_eur,
    })

    monthly = (
        detail.groupby("Month", as_index=False)
        .agg({
            "Lost Export Energy (MWh)": "sum",
            "Opportunity Cost (€)": "sum",
        })
    )
    monthly["PV Export Price Proxy (€/MWh)"] = monthly["Month"].map(
        PV_EXPORT_PRICE_PROXY_11KV_EUR_PER_MWH
    )

    total_cost_eur = float(hourly_cost_eur.sum())

    assert total_cost_eur >= -1e-6, \
        "Annual PV opportunity cost must not be negative."

    return total_cost_eur, monthly


# =========================================
# PV ANALYSIS FUNCTIONS
# =========================================

#
def calculate_pv_metrics(df):
    annual_energy_mwh = df["P"].sum() / 1_000_000
    capacity_factor = annual_energy_mwh / (10 * 8760)

    return annual_energy_mwh, capacity_factor
#

# =========================================
# CURTAILMENT FUNCTIONS
# =========================================

def calculate_hourly_curtailment(df, grid_limit_mw):
    grid_limit_w = grid_limit_mw * 1_000_000
    curtailed_power_w = (df["P"] - grid_limit_w).clip(lower=0)
    curtailed_energy_mwh = curtailed_power_w.sum() / 1_000_000
    return curtailed_power_w, curtailed_energy_mwh


def calculate_curtailment_recovery_vs_pem_size(df, pem_sizes_mw, selected_curtailed_mwh):
    """Fraction of gross no-PEM curtailment avoided/absorbed by the non-hybrid PEM strategy."""
    gross_curt_w = np.maximum(df["P"].to_numpy(dtype=float) - selected_grid_limit_mw * 1e6, 0.0)
    gross_curt_mwh = gross_curt_w.sum() / 1e6
    results = []
    for pem_size_mw in pem_sizes_mw:
        op = simulate_nonhybrid_operation(df, pem_size_mw, selected_grid_limit_mw)
        residual_mwh = op["residual_curtailment_w"].sum() / 1e6
        recovered_mwh = max(gross_curt_mwh - residual_mwh, 0.0)
        results.append(recovered_mwh / gross_curt_mwh * 100 if gross_curt_mwh > 0 else 0.0)
    return results


def diagnose_pem_curtailment(df, pem_sizes_mw):
    """Split gross no-PEM curtailment into avoided and residual curtailment."""
    results = []
    gross_no_pem_w = np.maximum(
        df["P"].to_numpy(dtype=float) - selected_grid_limit_mw * 1e6, 0.0
    )
    gross_no_pem_mwh = gross_no_pem_w.sum() / 1e6

    for pem_mw in pem_sizes_mw:
        op = simulate_nonhybrid_operation(df, pem_mw, selected_grid_limit_mw)
        residual_mwh = op["residual_curtailment_w"].sum() / 1e6
        avoided_mwh = max(gross_no_pem_mwh - residual_mwh, 0.0)
        results.append({
            "PEM Size (MW)": pem_mw,
            "Gross Curtailment without PEM (MWh)": gross_no_pem_mwh,
            "Avoided Curtailment (MWh)": avoided_mwh,
            "Residual Curtailment (MWh)": residual_mwh,
        })

    return pd.DataFrame(results)

def minimum_load_sensitivity(df, pem_sizes_mw, min_load_fractions):
    """Curtailment recovery sensitivity using the non-hybrid dispatch for each minimum load."""
    gross_no_pem_w = np.maximum(df["P"].to_numpy(dtype=float) - selected_grid_limit_mw * 1e6, 0.0)
    gross_no_pem_mwh = gross_no_pem_w.sum() / 1e6
    results = {}
    for min_load_fraction in min_load_fractions:
        recovery_values = []
        for pem_size_mw in pem_sizes_mw:
            op = simulate_nonhybrid_operation(
                df, pem_size_mw, selected_grid_limit_mw, min_load_fraction=min_load_fraction
            )
            residual_mwh = op["residual_curtailment_w"].sum() / 1e6
            recovered_mwh = max(gross_no_pem_mwh - residual_mwh, 0.0)
            recovery_values.append(
                recovered_mwh / gross_no_pem_mwh * 100 if gross_no_pem_mwh > 0 else 0.0
            )
        results[min_load_fraction] = recovery_values
    return results


# =========================================
# PEM FUNCTIONS
# =========================================

def calculate_monthly_hydrogen(df, selected_pem_mw):

    pem_size_w = selected_pem_mw * 1_000_000

    hourly_h2_kg, _ = calculate_hydrogen_from_pem_input(
        df["pem_input_w"].values,
        pem_size_w
    )

    df_temp = df.copy()
    df_temp["hourly_h2_kg"] = hourly_h2_kg

    monthly_h2_kg = (
        df_temp.groupby("month")["hourly_h2_kg"].sum()
    )

    return monthly_h2_kg

#
def simulate_nonhybrid_operation(df, pem_size_mw, grid_limit_mw, min_load_fraction=None):
    """
    Strategy A: strict curtailment-only PEM dispatch.

    Strategy:
      1. PV export has priority up to the grid export limit.
      2. Only PV above that limit is available to the PEM.
      3. The PEM operates only when curtailed PV can meet its physical minimum.
      4. No grid electricity is purchased and exportable PV is never displaced.
    """
    if min_load_fraction is None:
        min_load_fraction = MIN_PEM_LOAD_FRACTION

    pv_w = df["P"].to_numpy(dtype=float)
    pem_capacity_w = pem_size_mw * 1_000_000
    grid_limit_w = grid_limit_mw * 1_000_000
    min_power_w = min_load_fraction * pem_capacity_w

    potential_curtailment_w = np.maximum(pv_w - grid_limit_w, 0.0)
    candidate_pem_w = np.minimum(potential_curtailment_w, pem_capacity_w)
    active = candidate_pem_w >= min_power_w - 1e-9
    pem_power_w = np.where(active, candidate_pem_w, 0.0)
    pv_to_pem_w = pem_power_w.copy()

    pv_export_w = np.minimum(pv_w, grid_limit_w)
    residual_curtailment_w = potential_curtailment_w - pv_to_pem_w

    # Counterfactual export without the electrolyser. This is the correct
    # reference for the opportunity cost of diverting otherwise-saleable PV.
    export_without_pem_w = np.minimum(pv_w, grid_limit_w)
    lost_export_w = np.maximum(export_without_pem_w - pv_export_w, 0.0)

    assert np.all(pv_export_w <= export_without_pem_w + 1e-9), \
        "Actual PV export exceeds no-PEM counterfactual export."

    hourly_h2_kg, annual_h2_kg = calculate_hydrogen_from_pem_input(
        pem_power_w, pem_capacity_w
    )

    # Physical checks.
    assert np.all(pv_to_pem_w >= -1e-9)
    assert np.all(pv_export_w >= -1e-9)
    assert np.all(residual_curtailment_w >= -1e-9)
    assert np.all(pem_power_w <= pem_capacity_w + 1e-9)
    assert np.allclose(
        pv_w,
        pv_to_pem_w + pv_export_w + residual_curtailment_w,
        atol=1e-6
    ), "Non-hybrid PV energy balance failed."

    total_pem_energy_mwh = pem_power_w.sum() / 1_000_000
    utilization_pct = (
        total_pem_energy_mwh / (pem_size_mw * 8760) * 100
        if pem_size_mw > 0 else 0.0
    )

    return {
        "pv_generation_mwh": pv_w.sum() / 1_000_000,
        "pv_export_mwh": pv_export_w.sum() / 1_000_000,
        "residual_curtailment_mwh": residual_curtailment_w.sum() / 1_000_000,
        "pv_to_pem_w": pv_to_pem_w,
        "baseline_pv_to_pem_w": np.zeros_like(pv_w),
        "curtailment_boost_w": pv_to_pem_w,
        "pem_power_w": pem_power_w,
        "pv_export_w": pv_export_w,
        "potential_curtailment_w": potential_curtailment_w,
        "residual_curtailment_w": residual_curtailment_w,
        "export_without_pem_w": export_without_pem_w,
        "lost_export_w": lost_export_w,
        "lost_export_mwh": lost_export_w.sum() / 1_000_000,
        "hourly_h2_kg": hourly_h2_kg,
        "annual_h2_kg": annual_h2_kg,
        "total_pem_energy_mwh": total_pem_energy_mwh,
        "utilization_pct": utilization_pct,
        "operating_hours": int(active.sum()),
        "off_hours": int((~active).sum()),
        "starts": int(np.count_nonzero(active & ~np.r_[False, active[:-1]])),
        "stops": int(np.count_nonzero((~active) & np.r_[False, active[:-1]])),
        "below_minimum_resource_hours": int(
            ((potential_curtailment_w > 0.0) & ~active).sum()
        ),
        "full_load_hours": pem_power_w.sum() / pem_capacity_w,
    }


def analyze_pem_size(df, pem_size_mw, pem_capex_per_kw):
    """Evaluate PEM sizing under Strategy A, strict curtailment-only dispatch."""
    op = simulate_nonhybrid_operation(df, pem_size_mw, selected_grid_limit_mw)

    pem_size_kw = pem_size_mw * 1000
    pem_capex_eur = pem_size_kw * pem_capex_per_kw
    pem_energy_mwh = op["total_pem_energy_mwh"]
    h2_kg = op["annual_h2_kg"]
    utilization = op["utilization_pct"] / 100.0

    one_year_capex_intensity = (
        pem_capex_eur / h2_kg if h2_kg > 0 else np.inf
    )

    return (
        pem_energy_mwh,
        h2_kg,
        utilization,
        pem_capex_eur,
        one_year_capex_intensity
    )


def calculate_selected_pem_input(df, selected_pem_mw):
    """Selected Strategy A base case, using strict curtailment-only operation."""
    op = simulate_nonhybrid_operation(df, selected_pem_mw, selected_grid_limit_mw)
    return pd.Series(op["pem_power_w"], index=df.index), op["annual_h2_kg"]


# =========================================
# v1.3 HYBRID OPERATING STRATEGY FUNCTIONS
# =========================================

def simulate_hybrid_operation(df, pem_size_mw, baseline_load_fraction, grid_limit_mw=None):
    """
    Strategy B: PV-priority + grid-baseline PEM dispatch.

    Dispatch priority:
      1. Available PV is offered to the PEM first, up to rated power.
      2. Grid electricity fills only the deficit to the selected baseline.
      3. Grid electricity never pushes the PEM above the baseline by itself.
      4. Remaining PV is exported up to the grid limit and any excess is curtailed.

    Zero baseline is deliberately excluded, so a duplicate PV-only case
    cannot enter the Strategy B sensitivity.
    """
    if grid_limit_mw is None:
        grid_limit_mw = selected_grid_limit_mw

    pv_w = df["P"].to_numpy(dtype=float)
    pem_capacity_w = pem_size_mw * 1_000_000
    grid_limit_w = grid_limit_mw * 1_000_000
    min_pem_power_w = MIN_PEM_LOAD_FRACTION * pem_capacity_w
    baseline_power_w = baseline_load_fraction * pem_capacity_w

    if not (MIN_PEM_LOAD_FRACTION <= baseline_load_fraction <= 1.0):
        raise ValueError(
            "Strategy B baseline_load_fraction must be between the PEM "
            "minimum-load fraction and 1.0."
        )

    gross_curtailment_without_pem_w = np.maximum(pv_w - grid_limit_w, 0.0)
    pv_to_pem_w = np.minimum(pv_w, pem_capacity_w)

    # Grid can only fill a deficit to the requested baseline. A requested
    # baseline below the physical PEM minimum does not trigger grid operation.
    grid_to_pem_w = np.maximum(baseline_power_w - pv_to_pem_w, 0.0)
    grid_to_pem_w = np.minimum(
        grid_to_pem_w,
        np.maximum(pem_capacity_w - pv_to_pem_w, 0.0)
    )

    target_pem_power_w = pv_to_pem_w + grid_to_pem_w

    active = target_pem_power_w >= min_pem_power_w - 1e-9
    actual_power_used_w = np.where(active, target_pem_power_w, 0.0)
    pv_to_pem_w = np.where(active, pv_to_pem_w, 0.0)
    grid_to_pem_w = np.where(active, grid_to_pem_w, 0.0)

    pv_remaining_w = np.maximum(pv_w - pv_to_pem_w, 0.0)
    pv_export_w = np.minimum(pv_remaining_w, grid_limit_w)
    residual_curtailment_w = np.maximum(pv_remaining_w - grid_limit_w, 0.0)

    # Counterfactual export without the electrolyser. Only PV that would
    # otherwise have been exported carries an opportunity cost.
    export_without_pem_w = np.minimum(pv_w, grid_limit_w)
    lost_export_w = np.maximum(export_without_pem_w - pv_export_w, 0.0)
    avoided_curtailment_w = np.maximum(
        gross_curtailment_without_pem_w - residual_curtailment_w,
        0.0
    )
    assert np.allclose(
        pv_to_pem_w,
        lost_export_w + avoided_curtailment_w,
        atol=1e-6
    ), "Strategy B PV-to-PEM accounting split failed."

    # Physical / dispatch assertions.
    assert np.all(pv_to_pem_w >= -1e-9), "Negative PV-to-PEM flow detected."
    assert np.all(grid_to_pem_w >= -1e-9), "Negative grid purchase detected."
    assert np.all(actual_power_used_w <= pem_capacity_w + 1e-9), "PEM capacity exceeded."
    assert np.all(pv_to_pem_w <= pv_w + 1e-9), "PEM uses more PV than generated."
    assert np.all(pv_export_w <= export_without_pem_w + 1e-9), \
        "Actual PV export exceeds no-PEM counterfactual export."
    assert np.allclose(
        actual_power_used_w,
        pv_to_pem_w + grid_to_pem_w,
        atol=1e-6
    ), "Hybrid PEM source balance failed."
    assert np.allclose(
        pv_w,
        pv_to_pem_w + pv_export_w + residual_curtailment_w,
        atol=1e-6
    ), "Hybrid PV energy balance failed."

    hourly_h2_kg, annual_h2_kg = calculate_hydrogen_from_pem_input(
        actual_power_used_w, pem_capacity_w
    )

    pv_energy_to_pem_mwh = pv_to_pem_w.sum() / 1_000_000
    purchased_energy_mwh = grid_to_pem_w.sum() / 1_000_000
    total_pem_energy_mwh = actual_power_used_w.sum() / 1_000_000
    residual_curtailment_mwh = residual_curtailment_w.sum() / 1_000_000

    utilization_pct = (
        total_pem_energy_mwh / (pem_size_mw * 8760) * 100
        if pem_size_mw > 0 else 0.0
    )

    share_pem_energy_from_pv_pct = (
        pv_energy_to_pem_mwh / total_pem_energy_mwh * 100
        if total_pem_energy_mwh > 0 else np.nan
    )

    return {
        "pv_generation_mwh": pv_w.sum() / 1_000_000,
        "pv_export_mwh": pv_export_w.sum() / 1_000_000,
        "annual_h2_kg": annual_h2_kg,
        "hourly_h2_kg": hourly_h2_kg,
        "operating_hours": int(active.sum()),
        "off_hours": int((~active).sum()),
        "starts": int(np.count_nonzero(active & ~np.r_[False, active[:-1]])),
        "stops": int(np.count_nonzero((~active) & np.r_[False, active[:-1]])),
        "grid_only_hours": int(((grid_to_pem_w > 0.0) & (pv_to_pem_w <= 1e-9)).sum()),
        "curtailed_pv_hours": int((avoided_curtailment_w > 0.0).sum()),
        "full_load_hours": actual_power_used_w.sum() / pem_capacity_w,
        "utilization_pct": utilization_pct,
        "pv_energy_to_pem_mwh": pv_energy_to_pem_mwh,
        "purchased_energy_mwh": purchased_energy_mwh,
        "total_pem_energy_mwh": total_pem_energy_mwh,
        "share_pem_energy_from_pv_pct": share_pem_energy_from_pv_pct,
        "residual_curtailment_mwh": residual_curtailment_mwh,
        "lost_export_mwh": lost_export_w.sum() / 1_000_000,
        "export_without_pem_w": export_without_pem_w,
        "lost_export_w": lost_export_w,
        "pv_to_pem_w": pv_to_pem_w,
        "purchased_w": grid_to_pem_w,
        "pem_power_w": actual_power_used_w,
        "pv_export_w": pv_export_w,
        "residual_curtailment_w": residual_curtailment_w,
        "gross_curtailment_without_pem_w": gross_curtailment_without_pem_w,
        "avoided_curtailment_w": avoided_curtailment_w,
        "exportable_pv_to_pem_w": lost_export_w,
    }


def hybrid_strategy_sensitivity(
    df,
    selected_pem_mw,
    baseline_load_fractions,
    electricity_prices_eur_per_mwh,
    pem_capex_eur,
    fixed_annual_opex_eur,
    discount_rate,
    project_lifetime_years,
    hydrogen_sale_price,
    pv_export_price_eur_per_mwh
):
    """
    Runs simulate_hybrid_operation once per baseline load fraction (the
    physical operation does not depend on electricity price), then
    layers each tested electricity price on top to compute the
    resulting OPEX, LCOH, and NPV.

    fixed_annual_opex_eur is the existing CAPEX-based OPEX (pem_opex_fraction
    of pem_capex_eur), unrelated to purchased electricity. Purchased
    electricity cost and month-specific lost-PV-export opportunity cost are
    added on top. The scalar pv_export_price_eur_per_mwh argument is retained
    only for backward compatibility and is not used for current calculations.

    Returns a long-format DataFrame, one row per (baseline load, price) combination.
    """

    operation_cache = {
        baseline: simulate_hybrid_operation(df, selected_pem_mw, baseline)
        for baseline in baseline_load_fractions
    }

    records = []

    for baseline in baseline_load_fractions:
        op = operation_cache[baseline]

        for price in electricity_prices_eur_per_mwh:

            electricity_cost_eur = op["purchased_energy_mwh"] * price
            opportunity_cost_eur, _ = calculate_pv_opportunity_cost_proxy(
                df, op["lost_export_w"]
            )
            total_annual_cost = (
                fixed_annual_opex_eur
                + electricity_cost_eur
                + opportunity_cost_eur
            )

            if op["annual_h2_kg"] > 0:
                lcoh = calculate_discounted_lcoh(
                    pem_capex_eur, total_annual_cost, op["annual_h2_kg"],
                    discount_rate, project_lifetime_years
                )
            else:
                lcoh = np.nan

            annual_revenue = op["annual_h2_kg"] * hydrogen_sale_price
            annual_cashflow_for_npv = annual_revenue - total_annual_cost
            npv = calculate_npv(
                pem_capex_eur, annual_cashflow_for_npv,
                discount_rate, project_lifetime_years
            )

            records.append({
                "Baseline Load (%)": baseline * 100,
                "Electricity Price (€/MWh)": price,
                "H2 (kg/year)": op["annual_h2_kg"],
                "Utilization (%)": op["utilization_pct"],
                "Operating Hours (h)": op["operating_hours"],
                "Off Hours (h)": op["off_hours"],
                "Starts": op["starts"],
                "Stops": op["stops"],
                "Grid-Only Hours (h)": op["grid_only_hours"],
                "Curtailed-PV Hours (h)": op["curtailed_pv_hours"],
                "Full-Load Hours (h)": op["full_load_hours"],
                "PV Energy to PEM (MWh)": op["pv_energy_to_pem_mwh"],
                "Curtailed PV to PEM (MWh)": op["avoided_curtailment_w"].sum() / 1_000_000,
                "Exportable PV to PEM (MWh)": op["exportable_pv_to_pem_w"].sum() / 1_000_000,
                "Purchased Energy (MWh)": op["purchased_energy_mwh"],
                "Total PEM Energy (MWh)": op["total_pem_energy_mwh"],
                "Share PEM Energy from PV (%)": op["share_pem_energy_from_pv_pct"],
                "Residual Curtailment (MWh)": op["residual_curtailment_mwh"],
                "PV Generation (MWh)": op["pv_generation_mwh"],
                "PV Export (MWh)": op["pv_export_mwh"],
                "Lost Export Energy (MWh)": op["lost_export_mwh"],
                "PV Opportunity Cost (€/year)": opportunity_cost_eur,
                "Electricity Expenditure (€/year)": electricity_cost_eur,
                "Total Annual Operating + Energy Cost (€/year)": total_annual_cost,
                "LCOH (€/kg H2)": lcoh,
                "NPV (€)": npv,
            })

    return pd.DataFrame(records)

###
def calculate_cyprus_2023_industrial_benchmark(
    df,
    selected_pem_mw,
    baseline_load_fractions,
    industrial_price_eur_per_mwh,
    pem_capex_eur,
    fixed_annual_opex_eur,
    discount_rate,
    project_lifetime_years,
    hydrogen_sale_price,
    pv_export_price_eur_per_mwh
):
    """
    Evaluate the hybrid PEM operating strategy using a representative
    Cyprus 2023 industrial electricity-purchase benchmark.

    The PEM is treated as a generic large MV/HV industrial consumer.

    IMPORTANT:
    This electricity price is not a historical DAM clearing price.
    It is a representative industrial electricity-cost benchmark.
    """

    results = hybrid_strategy_sensitivity(
        df=df,
        selected_pem_mw=selected_pem_mw,
        baseline_load_fractions=baseline_load_fractions,
        electricity_prices_eur_per_mwh=[industrial_price_eur_per_mwh],
        pem_capex_eur=pem_capex_eur,
        fixed_annual_opex_eur=fixed_annual_opex_eur,
        discount_rate=discount_rate,
        project_lifetime_years=project_lifetime_years,
        hydrogen_sale_price=hydrogen_sale_price,
        pv_export_price_eur_per_mwh=pv_export_price_eur_per_mwh
    )

    results["Scenario"] = "Cyprus 2023 MV/HV industrial benchmark"

    return results
###
###


# =========================================
# v1.7 CYPRUS DAM-LINKED STRATEGY B
# =========================================

def resolve_dam_price_file():
    """Return the first configured TSOC DAM workbook that exists."""
    for candidate in DAM_PRICE_FILENAME_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    raise FileNotFoundError(
        "Cyprus DAM workbook not found. Place "
        "DSMK_DAM_2025-10-01_to_2026-09-24.xlsx either beside main.py "
        "or inside the data folder."
    )


def load_cyprus_dam_prices(path):
    """Load and validate the official 30-minute TSOC DAM price series."""
    dam = pd.read_excel(path, sheet_name="DAM_Data", engine="openpyxl")
    required = {"Timestamp", "DAM_Price_EUR_per_MWh"}
    missing = required - set(dam.columns)
    if missing:
        raise ValueError(f"DAM workbook missing columns: {sorted(missing)}")

    dam = dam.copy()
    dam["Timestamp"] = pd.to_datetime(dam["Timestamp"], errors="coerce")
    dam["DAM_Price_EUR_per_MWh"] = pd.to_numeric(
        dam["DAM_Price_EUR_per_MWh"], errors="coerce"
    )
    if dam[["Timestamp", "DAM_Price_EUR_per_MWh"]].isna().any().any():
        raise ValueError("DAM workbook contains invalid timestamps or prices.")
    dam = dam.sort_values("Timestamp").drop_duplicates(
        subset=["Timestamp", "DAM_Price_EUR_per_MWh"], keep="first"
    ).reset_index(drop=True)

    if dam["Timestamp"].duplicated().any():
        raise ValueError("DAM workbook has conflicting prices for one timestamp.")
    if len(dam) != 17_230 or dam["Timestamp"].dt.normalize().nunique() != DAM_OBSERVED_DAYS:
        raise ValueError("DAM coverage differs from the configured 359-day screening window.")

    if dam.empty:
        raise ValueError("DAM workbook contains no valid observations.")
    if (dam["DAM_Price_EUR_per_MWh"] < 0).any():
        print("WARNING: Negative DAM prices are present and retained as published.")

    dam["All_in_Grid_Price_EUR_per_MWh"] = (
        dam["DAM_Price_EUR_per_MWh"] + grid_variable_adder_eur_per_mwh
    )
    return dam


def map_hourly_pv_to_dam_intervals(pv_df, dam_df):
    """
    Map the 8,760-hour PV/TMY profile to each TSOC half-hour observation.

    Each hourly PV power value is held constant over its :00 and :30 market
    intervals. Energy is calculated later using the explicit 0.5-hour step.
    """
    pv = pv_df.copy()
    if "time" not in pv.columns or "P" not in pv.columns:
        raise ValueError("PV dataframe must contain time and P.")
    pv["time"] = pd.to_datetime(pv["time"], errors="coerce")
    pv["month"] = pv["time"].dt.month
    pv["day"] = pv["time"].dt.day
    pv["hour"] = pv["time"].dt.hour
    if pv.duplicated(["month", "day", "hour"]).any():
        raise ValueError("PV profile has duplicate month/day/hour keys.")

    lookup = {
        (int(row.month), int(row.day), int(row.hour)): float(row.P)
        for row in pv[["month", "day", "hour", "P"]].itertuples(index=False)
    }
    result = dam_df.copy()
    keys = list(zip(
        result["Timestamp"].dt.month,
        result["Timestamp"].dt.day,
        result["Timestamp"].dt.hour,
    ))
    result["P"] = [lookup.get(key, np.nan) for key in keys]
    if result["P"].isna().any():
        missing_keys = result.loc[result["P"].isna(), "Timestamp"].head(10)
        raise ValueError(
            "PV mapping failed for DAM timestamps: "
            + ", ".join(missing_keys.astype(str))
        )
    result["month"] = result["Timestamp"].dt.month
    result["day"] = result["Timestamp"].dt.day
    result["hour"] = result["Timestamp"].dt.hour
    return result


def simulate_hybrid_dam_operation(
    dam_dispatch_df,
    pem_size_mw,
    baseline_load_fraction,
    dam_price_cap_eur_per_mwh,
    grid_limit_mw=None,
):
    """
    Strategy B with PV priority and conditional 30-minute grid top-up.

    Grid power fills only the shortfall to the requested baseline and only
    when the all-in variable import price is at or below the selected cap.
    The 359-day observed result is retained and an explicit 365/359 factor is
    used for annual economic metrics.
    """
    if grid_limit_mw is None:
        grid_limit_mw = selected_grid_limit_mw
    if not (MIN_PEM_LOAD_FRACTION <= baseline_load_fraction <= 1.0):
        raise ValueError("DAM Strategy B baseline is outside physical limits.")

    dt = DAM_INTERVAL_HOURS
    annual_factor = DAM_ANNUALIZATION_FACTOR
    pv_w = dam_dispatch_df["P"].to_numpy(dtype=float)
    dam_price = dam_dispatch_df["DAM_Price_EUR_per_MWh"].to_numpy(dtype=float)
    all_in_price = dam_dispatch_df[
        "All_in_Grid_Price_EUR_per_MWh"
    ].to_numpy(dtype=float)
    months = dam_dispatch_df["Timestamp"].dt.month.to_numpy(dtype=int)

    pem_capacity_w = pem_size_mw * 1_000_000.0
    grid_limit_w = grid_limit_mw * 1_000_000.0
    minimum_w = MIN_PEM_LOAD_FRACTION * pem_capacity_w
    baseline_w = baseline_load_fraction * pem_capacity_w

    gross_curtailment_w = np.maximum(pv_w - grid_limit_w, 0.0)
    pv_to_pem_w = np.minimum(pv_w, pem_capacity_w)
    grid_allowed = all_in_price <= dam_price_cap_eur_per_mwh + 1e-12
    grid_to_pem_w = np.where(
        grid_allowed,
        np.maximum(baseline_w - pv_to_pem_w, 0.0),
        0.0,
    )
    grid_to_pem_w = np.minimum(
        grid_to_pem_w, np.maximum(pem_capacity_w - pv_to_pem_w, 0.0)
    )

    target_w = pv_to_pem_w + grid_to_pem_w
    active = target_w >= minimum_w - 1e-9
    pem_power_w = np.where(active, target_w, 0.0)
    pv_to_pem_w = np.where(active, pv_to_pem_w, 0.0)
    grid_to_pem_w = np.where(active, grid_to_pem_w, 0.0)

    pv_remaining_w = np.maximum(pv_w - pv_to_pem_w, 0.0)
    pv_export_w = np.minimum(pv_remaining_w, grid_limit_w)
    residual_curtailment_w = np.maximum(pv_remaining_w - grid_limit_w, 0.0)
    export_without_pem_w = np.minimum(pv_w, grid_limit_w)
    lost_export_w = np.maximum(export_without_pem_w - pv_export_w, 0.0)
    avoided_curtailment_w = np.maximum(
        gross_curtailment_w - residual_curtailment_w, 0.0
    )

    assert np.allclose(
        pem_power_w, pv_to_pem_w + grid_to_pem_w, atol=1e-6
    )
    assert np.allclose(
        pv_w, pv_to_pem_w + pv_export_w + residual_curtailment_w, atol=1e-6
    )
    assert np.all(grid_to_pem_w[~grid_allowed] <= 1e-9)

    h2_rate_kg_per_h, _ = calculate_hydrogen_from_pem_input(
        pem_power_w, pem_capacity_w
    )
    interval_h2_kg = h2_rate_kg_per_h * dt

    def observed_mwh(power_w):
        return float(np.sum(power_w) * dt / 1_000_000.0)

    observed_h2_kg = float(interval_h2_kg.sum())
    annual_h2_kg = observed_h2_kg * annual_factor
    purchased_mwh_observed = observed_mwh(grid_to_pem_w)
    purchased_mwh_annual = purchased_mwh_observed * annual_factor
    electricity_cost_observed = float(np.sum(
        grid_to_pem_w / 1_000_000.0 * dt * all_in_price
    ))
    electricity_cost_annual = electricity_cost_observed * annual_factor

    export_prices = np.array(
        [PV_EXPORT_PRICE_PROXY_11KV_EUR_PER_MWH[int(month)] for month in months],
        dtype=float,
    )
    opportunity_cost_observed = float(np.sum(
        lost_export_w / 1_000_000.0 * dt * export_prices
    ))
    opportunity_cost_annual = opportunity_cost_observed * annual_factor

    annual_total_pem_mwh = observed_mwh(pem_power_w) * annual_factor
    annual_pv_to_pem_mwh = observed_mwh(pv_to_pem_w) * annual_factor
    annual_hours = DAM_OBSERVED_DAYS * 24.0 * annual_factor
    utilization_pct = annual_total_pem_mwh / (pem_size_mw * annual_hours) * 100.0
    starts = int(np.count_nonzero(active & ~np.r_[False, active[:-1]]))
    stops = int(np.count_nonzero((~active) & np.r_[False, active[:-1]]))

    return {
        "observed_days": DAM_OBSERVED_DAYS,
        "annualization_factor": annual_factor,
        "price_cap_eur_per_mwh": dam_price_cap_eur_per_mwh,
        "mean_dam_price_eur_per_mwh": float(np.mean(dam_price)),
        "mean_all_in_price_eur_per_mwh": float(np.mean(all_in_price)),
        "purchased_weighted_price_eur_per_mwh": (
            electricity_cost_observed / purchased_mwh_observed
            if purchased_mwh_observed > 0 else np.nan
        ),
        "annual_h2_kg": annual_h2_kg,
        "observed_h2_kg": observed_h2_kg,
        "hourly_h2_kg": interval_h2_kg * annual_factor,
        "pem_power_w": pem_power_w,
        "pv_to_pem_w": pv_to_pem_w,
        "purchased_w": grid_to_pem_w,
        "pv_export_w": pv_export_w,
        "residual_curtailment_w": residual_curtailment_w,
        "lost_export_w": lost_export_w,
        "avoided_curtailment_w": avoided_curtailment_w,
        "exportable_pv_to_pem_w": lost_export_w,
        "pv_generation_mwh": observed_mwh(pv_w) * annual_factor,
        "pv_export_mwh": observed_mwh(pv_export_w) * annual_factor,
        "pv_energy_to_pem_mwh": annual_pv_to_pem_mwh,
        "purchased_energy_mwh": purchased_mwh_annual,
        "total_pem_energy_mwh": annual_total_pem_mwh,
        "residual_curtailment_mwh": observed_mwh(residual_curtailment_w) * annual_factor,
        "lost_export_mwh": observed_mwh(lost_export_w) * annual_factor,
        "curtailed_pv_to_pem_mwh": observed_mwh(avoided_curtailment_w) * annual_factor,
        "electricity_cost_eur": electricity_cost_annual,
        "opportunity_cost_eur": opportunity_cost_annual,
        "utilization_pct": utilization_pct,
        "share_pem_energy_from_pv_pct": (
            annual_pv_to_pem_mwh / annual_total_pem_mwh * 100.0
            if annual_total_pem_mwh > 0 else np.nan
        ),
        "operating_hours": float(active.sum() * dt),
        "off_hours": float((~active).sum() * dt),
        "starts": starts,
        "stops": stops,
        "grid_only_hours": float(
            np.sum((grid_to_pem_w > 0.0) & (pv_to_pem_w <= 1e-9)) * dt
        ),
        "curtailed_pv_hours": float(np.sum(avoided_curtailment_w > 0.0) * dt),
        "full_load_hours": float(np.sum(pem_power_w / pem_capacity_w) * dt * annual_factor),
        "active": active,
        "grid_allowed": grid_allowed,
    }


def dam_hybrid_strategy_sensitivity(
    dam_dispatch_df,
    selected_pem_mw,
    baseline_fractions,
    price_caps,
    pem_capex_eur,
    fixed_annual_opex_eur,
    discount_rate,
    project_lifetime_years,
    hydrogen_sale_price,
    demand_charge_eur_per_kw_year=None,
    fixed_supply_metering_eur_per_year=None,
):
    """Economic sensitivity for DAM-linked Strategy B."""
    if demand_charge_eur_per_kw_year is None:
        demand_charge_eur_per_kw_year = grid_demand_charge_eur_per_kw_year
    if fixed_supply_metering_eur_per_year is None:
        fixed_supply_metering_eur_per_year = grid_fixed_supply_metering_eur_per_year
    records = []
    operation_cache = {}
    for baseline in baseline_fractions:
        for price_cap in price_caps:
            op = simulate_hybrid_dam_operation(
                dam_dispatch_df,
                selected_pem_mw,
                baseline,
                price_cap,
            )
            operation_cache[(baseline, price_cap)] = op
            contracted_import_capacity_kw = selected_pem_mw * 1000.0 * baseline
            demand_charge = (
                contracted_import_capacity_kw
                * demand_charge_eur_per_kw_year
            )
            total_annual_cost = (
                fixed_annual_opex_eur
                + op["electricity_cost_eur"]
                + op["opportunity_cost_eur"]
                + demand_charge
                + fixed_supply_metering_eur_per_year
                + collateral_annual_cost_eur
            )
            lcoh = calculate_discounted_lcoh(
                pem_capex_eur,
                total_annual_cost,
                op["annual_h2_kg"],
                discount_rate,
                project_lifetime_years,
            )
            annual_cashflow = (
                op["annual_h2_kg"] * hydrogen_sale_price - total_annual_cost
            )
            scenario_npv = calculate_npv(
                pem_capex_eur, annual_cashflow, discount_rate, project_lifetime_years
            )
            records.append({
                "Baseline Load (%)": baseline * 100.0,
                "Electricity Price (€/MWh)": float(price_cap),
                "All-in Import Price Cap (€/MWh)": float(price_cap),
                "Purchased Weighted Price (€/MWh)": op["purchased_weighted_price_eur_per_mwh"],
                "H2 (kg/year)": op["annual_h2_kg"],
                "Observed H2 in 359 days (kg)": op["observed_h2_kg"],
                "Utilization (%)": op["utilization_pct"],
                "Operating Hours in 359 Days (h)": op["operating_hours"],
                "Off Hours in 359 Days (h)": op["off_hours"],
                "Starts in 359 Days": op["starts"],
                "Stops in 359 Days": op["stops"],
                "Grid-Only Hours in 359 Days (h)": op["grid_only_hours"],
                "Curtailed-PV Hours in 359 Days (h)": op["curtailed_pv_hours"],
                "Annualized Full-Load Hours (h/year)": op["full_load_hours"],
                "PV Energy to PEM (MWh)": op["pv_energy_to_pem_mwh"],
                "Curtailed PV to PEM (MWh)": op["curtailed_pv_to_pem_mwh"],
                "Exportable PV to PEM (MWh)": op["lost_export_mwh"],
                "Purchased Energy (MWh)": op["purchased_energy_mwh"],
                "Total PEM Energy (MWh)": op["total_pem_energy_mwh"],
                "Share PEM Energy from PV (%)": op["share_pem_energy_from_pv_pct"],
                "Residual Curtailment (MWh)": op["residual_curtailment_mwh"],
                "PV Generation (MWh)": op["pv_generation_mwh"],
                "PV Export (MWh)": op["pv_export_mwh"],
                "Lost Export Energy (MWh)": op["lost_export_mwh"],
                "PV Opportunity Cost (€/year)": op["opportunity_cost_eur"],
                "Electricity Expenditure (€/year)": op["electricity_cost_eur"],
                "Contracted Import Capacity (kW)": contracted_import_capacity_kw,
                "Demand Charge (€/year)": demand_charge,
                "Fixed Supply + Metering Charge (€/year)": fixed_supply_metering_eur_per_year,
                "Collateral Financing Cost (€/year)": collateral_annual_cost_eur,
                "Total Annual Operating + Energy Cost (€/year)": total_annual_cost,
                "LCOH (€/kg H2)": lcoh,
                "NPV (€)": scenario_npv,
                "Observed Days": DAM_OBSERVED_DAYS,
                "Annualization Factor": DAM_ANNUALIZATION_FACTOR,
            })
    return pd.DataFrame(records), operation_cache


# Economic assumptions
# ====== ECONOMIC ASSUMPTIONS ======


# =========================================
# ECONOMIC FUNCTIONS
# =========================================

def calculate_npv(initial_capex, annual_cashflow, discount_rate, project_lifetime_years):
    # annual_cashflow must be (revenue - opex) only.
    # Do NOT include annualized CAPEX here — initial_capex is already
    # deducted as a lump sum at year 0. Including CAPEX in annual_cashflow
    # would double-count it.
    npv = -initial_capex
    for year in range(1, project_lifetime_years + 1):
        npv += annual_cashflow / ((1 + discount_rate) ** year)
    return npv


def calculate_npv_price_sensitivity(hydrogen_price_scenarios, selected_h2_kg,
        annual_opex, annual_opportunity_cost_eur, pem_capex_eur,
        discount_rate, project_lifetime_years):
    # annual_cashflow = revenue - opex only (CAPEX handled as lump sum in calculate_npv)
    npv_results = []
    for h2_price in hydrogen_price_scenarios:
        annual_revenue = selected_h2_kg * h2_price
        annual_cashflow_for_npv = annual_revenue - annual_opex - annual_opportunity_cost_eur
        npv = calculate_npv(pem_capex_eur, annual_cashflow_for_npv, discount_rate, project_lifetime_years)
        npv_results.append(npv)
    return npv_results


def calculate_simple_lcoh(pem_capex_eur, annual_opex, annual_h2_kg, project_lifetime_years):
    """
    Simple (undiscounted) LCOH calculation.

    ASSUMPTIONS & LIMITATIONS:

    1. NO DISCOUNTING:
       Future costs are not discounted to present value.
       A proper discounted LCOH uses an annuity factor instead of
       a simple sum of years. See calculate_discounted_lcoh() below,
       which is now implemented and used alongside this function
       throughout the model.
       At 8% discount rate over 15 years:
         - Annuity factor = 8.56  (vs simple sum = 15.0)
         - Ratio = 15.0 / 8.56 = 1.75
       This means the CAPEX component of this simple LCOH is
       understated relative to the discounted version.

    2. NO LIFECYCLE EFFECTS INSIDE THIS FUNCTION:
       This legacy screening metric assumes constant annual H2 and excludes
       stack replacements. Use simulate_stack_lifecycle() for the documented
       degradation, replacement and discounted design-point calculation.

    Use this function only as an undiscounted comparison metric, not for an
    investment decision.
    """
    total_lifetime_cost = pem_capex_eur + annual_opex * project_lifetime_years
    total_lifetime_h2 = annual_h2_kg * project_lifetime_years
    lcoh = total_lifetime_cost / total_lifetime_h2
    return lcoh


def calculate_discounted_lcoh(pem_capex_eur, annual_opex, annual_h2_kg,
        discount_rate, project_lifetime_years):
    """
    Discounted LCOH, replacing the estimate previously carried only in the
    calculate_simple_lcoh() docstring.

    Standard discounted-cost-stream LCOH definition (constant annual OPEX
    and constant annual H2 output):

        LCOH = (CAPEX + OPEX * AF) / (H2_annual * AF)

    where AF is the annuity factor, AF = 1 / CRF. Dividing numerator and
    denominator by AF collapses this to:

        LCOH = (CAPEX * CRF + OPEX) / H2_annual
             = (Annualized CAPEX + OPEX) / H2_annual

    i.e. the same Capital Recovery Factor already used elsewhere in this
    script to annualize CAPEX for NPV reporting. This keeps the discounting
    treatment consistent across the whole model instead of introducing a
    second, separate discounting method just for LCOH.
    """
    crf = (discount_rate * (1 + discount_rate) ** project_lifetime_years) / \
          ((1 + discount_rate) ** project_lifetime_years - 1)
    annualized_capex = pem_capex_eur * crf
    lcoh = (annualized_capex + annual_opex) / annual_h2_kg
    return lcoh



def calculate_break_even_hydrogen_price(
    pem_capex_eur, annual_operating_and_energy_cost_eur, annual_h2_kg,
    discount_rate, project_lifetime_years
):
    """
    Constant real H2 selling price (EUR/kg) that gives NPV = 0 when annual
    H2 output and annual operating/energy cost are assumed constant. Under
    those assumptions it is numerically equal to the annualized LCOH.
    """
    if annual_h2_kg <= 0:
        return np.nan
    crf = (discount_rate * (1 + discount_rate) ** project_lifetime_years) / (
        (1 + discount_rate) ** project_lifetime_years - 1
    )
    annualized_capex_eur = pem_capex_eur * crf
    return (annualized_capex_eur + annual_operating_and_energy_cost_eur) / annual_h2_kg

def calculate_discounted_lcoh_for_capex_scenarios(selected_pem_mw, annual_h2_kg,
        pem_capex_scenarios, discount_rate, project_lifetime_years,
        annual_opportunity_cost_eur=0.0):
    lcoh_results = []
    for capex_per_kw in pem_capex_scenarios:
        pem_capex_eur = selected_pem_mw * 1000 * capex_per_kw
        annual_opex = pem_capex_eur * pem_opex_fraction
        lcoh = calculate_discounted_lcoh(
            pem_capex_eur, annual_opex + annual_opportunity_cost_eur, annual_h2_kg,
            discount_rate, project_lifetime_years
        )
        lcoh_results.append(lcoh)
    return lcoh_results


def calculate_discounted_lcoh_vs_grid_limit(df, grid_limits_mw, selected_pem_mw,
        discount_rate, project_lifetime_years, pv_export_price_eur_per_mwh):
    results = []
    for grid_limit_mw in grid_limits_mw:
        op = simulate_nonhybrid_operation(df, selected_pem_mw, grid_limit_mw)
        annual_h2_kg = op["annual_h2_kg"]
        pem_capex_eur = selected_pem_mw * 1000 * pem_capex_per_kw
        annual_opex = pem_capex_eur * pem_opex_fraction
        opportunity_cost_eur, _ = calculate_pv_opportunity_cost_proxy(
            df, op["lost_export_w"]
        )
        results.append(calculate_discounted_lcoh(
            pem_capex_eur, annual_opex + opportunity_cost_eur, annual_h2_kg,
            discount_rate, project_lifetime_years
        ))
    return results


def calculate_lcoh_for_capex_scenarios(selected_pem_mw, annual_h2_kg, pem_capex_scenarios,
        annual_opportunity_cost_eur=0.0):
    lcoh_results = []
    for capex_per_kw in pem_capex_scenarios:
        pem_capex_eur = selected_pem_mw * 1000 * capex_per_kw
        annual_opex = pem_capex_eur * pem_opex_fraction
        lcoh = calculate_simple_lcoh(
            pem_capex_eur, annual_opex + annual_opportunity_cost_eur, annual_h2_kg,
            project_lifetime_years
        )
        lcoh_results.append(lcoh)
    return lcoh_results


def calculate_lcoh_vs_grid_limit(df, grid_limits_mw, selected_pem_mw, pv_export_price_eur_per_mwh):
    results = []
    for grid_limit_mw in grid_limits_mw:
        op = simulate_nonhybrid_operation(df, selected_pem_mw, grid_limit_mw)
        annual_h2_kg = op["annual_h2_kg"]
        pem_capex_eur = selected_pem_mw * 1000 * pem_capex_per_kw
        annual_opex = pem_capex_eur * pem_opex_fraction
        opportunity_cost_eur, _ = calculate_pv_opportunity_cost_proxy(
            df, op["lost_export_w"]
        )
        results.append(calculate_simple_lcoh(
            pem_capex_eur, annual_opex + opportunity_cost_eur, annual_h2_kg,
            project_lifetime_years
        ))
    return results


def calculate_npv_grid_sensitivity(df, grid_limits_mw, selected_pem_mw, hydrogen_sale_price,
        annual_opex, pem_capex_eur, discount_rate, project_lifetime_years,
        pv_export_price_eur_per_mwh):
    npv_grid_results = []
    for grid_limit in grid_limits_mw:
        op = simulate_nonhybrid_operation(df, selected_pem_mw, grid_limit)
        annual_revenue = op["annual_h2_kg"] * hydrogen_sale_price
        opportunity_cost_eur, _ = calculate_pv_opportunity_cost_proxy(
            df, op["lost_export_w"]
        )
        annual_cashflow_for_npv = annual_revenue - annual_opex - opportunity_cost_eur
        npv_grid_results.append(calculate_npv(
            pem_capex_eur, annual_cashflow_for_npv, discount_rate, project_lifetime_years
        ))
    return npv_grid_results


def calculate_npv_heatmap_data(
    df, grid_limits_mw, hydrogen_price_scenarios, selected_pem_mw,
    annual_opex, pem_capex_eur, discount_rate, project_lifetime_years,
    pv_export_price_eur_per_mwh
):
    npv_matrix = []
    for grid_limit in grid_limits_mw:
        op = simulate_nonhybrid_operation(df, selected_pem_mw, grid_limit)
        opportunity_cost_eur, _ = calculate_pv_opportunity_cost_proxy(
            df, op["lost_export_w"]
        )
        row = []
        for h2_price in hydrogen_price_scenarios:
            annual_revenue = op["annual_h2_kg"] * h2_price
            annual_cashflow_for_npv = annual_revenue - annual_opex - opportunity_cost_eur
            row.append(calculate_npv(
                pem_capex_eur, annual_cashflow_for_npv, discount_rate, project_lifetime_years
            ))
        npv_matrix.append(row)
    return pd.DataFrame(npv_matrix, index=grid_limits_mw, columns=hydrogen_price_scenarios)
###
def calculate_lcoh_opex_sensitivity(
    pem_size_mw,
    pem_capex_per_kw,
    opex_scenarios_per_kw_year,
    annual_h2_kg,
    discount_rate,
    project_lifetime_years,
    annual_opportunity_cost_eur=0.0
):
    pem_size_kw = pem_size_mw * 1000
    pem_capex_eur = pem_size_kw * pem_capex_per_kw

    crf = (
        discount_rate * (1 + discount_rate) ** project_lifetime_years
        / ((1 + discount_rate) ** project_lifetime_years - 1)
    )

    annualized_capex = pem_capex_eur * crf

    results = []

    for opex_per_kw_year in opex_scenarios_per_kw_year:

        annual_opex = (
            pem_size_kw * opex_per_kw_year
        )

        discounted_lcoh = (
            annualized_capex + annual_opex + annual_opportunity_cost_eur
        ) / annual_h2_kg

        results.append(discounted_lcoh)

    return results


def calculate_bess_screening_capex(power_kw, energy_kwh, small_project_multiplier):
    """Return a transparent DEA-based BESS screening CAPEX in EUR2020."""
    power_mw = float(power_kw) / 1000.0
    energy_mwh = float(energy_kwh) / 1000.0
    reference_cost_eur2020 = (
        energy_mwh * BESS_ENERGY_COMPONENT_EUR2020_PER_MWH
        + power_mw * BESS_POWER_COMPONENT_EUR2020_PER_MW
        + energy_mwh * BESS_OTHER_PROJECT_COST_EUR2020_PER_MWH
    )
    return reference_cost_eur2020 * float(small_project_multiplier)


def simulate_strategy_c_bess_operation(
    dam_dispatch_df,
    strategy_b_operation,
    pem_size_mw,
    bess_power_kw,
    bess_energy_kwh,
    round_trip_efficiency=BESS_AC_ROUND_TRIP_EFFICIENCY,
    storage_loss_fraction_per_day=BESS_STORAGE_LOSS_FRACTION_PER_DAY,
    standby_power_kw=0.0,
):
    """Simulate Strategy C as a narrow residual-curtailment BESS diagnostic.

    The battery charges only from Strategy B residual curtailed PV. It never
    charges from the grid, never exports, and never displaces PV export. It
    discharges only into unused PEM headroom. If the PEM is otherwise off, the
    battery must be capable of lifting it above the 15% minimum-load threshold.

    A cyclic warm-up is used to reduce the arbitrary year-boundary SOC effect.
    Battery capacity fade, replacement and temperature derating are not applied,
    because no project quotation or applicable OEM retention curve is available.
    """
    if bess_power_kw <= 0.0 or bess_energy_kwh <= 0.0:
        raise ValueError("BESS power and energy must be positive.")
    if not (0.0 < round_trip_efficiency <= 1.0):
        raise ValueError("BESS round-trip efficiency must be in (0, 1].")

    dt = DAM_INTERVAL_HOURS
    annual_factor = DAM_ANNUALIZATION_FACTOR
    n = len(dam_dispatch_df)
    base_pem_w = np.asarray(strategy_b_operation["pem_power_w"], dtype=float)
    base_pv_to_pem_w = np.asarray(strategy_b_operation["pv_to_pem_w"], dtype=float)
    base_grid_to_pem_w = np.asarray(strategy_b_operation["purchased_w"], dtype=float)
    base_residual_w = np.asarray(
        strategy_b_operation["residual_curtailment_w"], dtype=float
    )
    if any(len(values) != n for values in (
        base_pem_w, base_pv_to_pem_w, base_grid_to_pem_w, base_residual_w
    )):
        raise ValueError("Strategy B and DAM arrays do not have matching lengths.")

    pem_capacity_w = float(pem_size_mw) * 1_000_000.0
    minimum_pem_w = MIN_PEM_LOAD_FRACTION * pem_capacity_w
    bess_power_w = float(bess_power_kw) * 1000.0
    energy_capacity_kwh = float(bess_energy_kwh)
    charge_efficiency = np.sqrt(float(round_trip_efficiency))
    discharge_efficiency = charge_efficiency
    interval_storage_factor = (
        (1.0 - float(storage_loss_fraction_per_day)) ** (dt / 24.0)
    )

    def dispatch_once(initial_soc_kwh, collect):
        soc_kwh = min(max(float(initial_soc_kwh), 0.0), energy_capacity_kwh)
        charge_w = np.zeros(n)
        discharge_w = np.zeros(n)
        soc_trace_kwh = np.zeros(n)
        storage_loss_kwh = np.zeros(n)
        standby_loss_kwh = np.zeros(n)

        for i in range(n):
            soc_before_storage = soc_kwh
            soc_kwh *= interval_storage_factor
            storage_loss_kwh[i] = soc_before_storage - soc_kwh

            # Charge has priority whenever residual curtailed PV exists. This
            # prevents simultaneous charge and discharge and avoids grid charge.
            if base_residual_w[i] > 1e-9:
                room_at_ac_input_kwh = max(
                    energy_capacity_kwh - soc_kwh, 0.0
                ) / charge_efficiency
                max_charge_w_from_room = room_at_ac_input_kwh / dt * 1000.0
                charge_w[i] = min(
                    base_residual_w[i], bess_power_w, max_charge_w_from_room
                )
                soc_kwh += charge_w[i] / 1000.0 * dt * charge_efficiency
            else:
                pem_headroom_w = max(pem_capacity_w - base_pem_w[i], 0.0)
                max_discharge_w_from_soc = (
                    soc_kwh * discharge_efficiency / dt * 1000.0
                )
                proposed_discharge_w = min(
                    bess_power_w, pem_headroom_w, max_discharge_w_from_soc
                )
                if (
                    base_pem_w[i] <= 1e-9
                    and proposed_discharge_w < minimum_pem_w - 1e-9
                ):
                    proposed_discharge_w = 0.0
                discharge_w[i] = proposed_discharge_w
                soc_kwh -= (
                    discharge_w[i] / 1000.0 * dt / discharge_efficiency
                )

            # The Huawei value is a standby-power ceiling, not a measured HVAC
            # trace. It is applied only when the battery is idle and has energy.
            if (
                charge_w[i] <= 1e-9
                and discharge_w[i] <= 1e-9
                and standby_power_kw > 0.0
                and soc_kwh > 0.0
            ):
                standby_energy_kwh = min(float(standby_power_kw) * dt, soc_kwh)
                soc_kwh -= standby_energy_kwh
                standby_loss_kwh[i] = standby_energy_kwh

            soc_kwh = min(max(soc_kwh, 0.0), energy_capacity_kwh)
            soc_trace_kwh[i] = soc_kwh

        if collect:
            return {
                "charge_w": charge_w,
                "discharge_w": discharge_w,
                "soc_kwh": soc_trace_kwh,
                "storage_loss_kwh": storage_loss_kwh,
                "standby_loss_kwh": standby_loss_kwh,
                "final_soc_kwh": soc_kwh,
            }
        return soc_kwh

    # Recycle the year-end SOC into the next pass. Saturation at full or empty
    # normally makes the periodic state converge rapidly for this dispatch rule.
    initial_soc_kwh = 0.5 * energy_capacity_kwh
    for _ in range(12):
        next_soc_kwh = dispatch_once(initial_soc_kwh, collect=False)
        if abs(next_soc_kwh - initial_soc_kwh) <= 1e-6:
            break
        initial_soc_kwh = next_soc_kwh
    dispatch = dispatch_once(initial_soc_kwh, collect=True)

    charge_w = dispatch["charge_w"]
    discharge_w = dispatch["discharge_w"]
    strategy_c_pem_w = base_pem_w + discharge_w
    residual_after_bess_w = base_residual_w - charge_w
    assert np.all(residual_after_bess_w >= -1e-6), \
        "BESS charge exceeds residual curtailed PV."
    assert np.all(strategy_c_pem_w <= pem_capacity_w + 1e-6), \
        "Strategy C exceeds PEM rated capacity."
    assert np.all((charge_w <= 1e-9) | (discharge_w <= 1e-9)), \
        "Simultaneous BESS charge and discharge detected."

    pv_w = dam_dispatch_df["P"].to_numpy(dtype=float)
    pv_export_w = np.asarray(strategy_b_operation["pv_export_w"], dtype=float)
    assert np.allclose(
        pv_w,
        base_pv_to_pem_w + pv_export_w + charge_w + residual_after_bess_w,
        atol=1e-5,
    ), "Strategy C PV balance failed."
    assert np.allclose(
        strategy_c_pem_w,
        base_pv_to_pem_w + base_grid_to_pem_w + discharge_w,
        atol=1e-5,
    ), "Strategy C PEM source balance failed."

    h2_rate_kg_per_h, _ = calculate_hydrogen_from_pem_input(
        strategy_c_pem_w, pem_capacity_w
    )
    interval_h2_kg = h2_rate_kg_per_h * dt

    def annualized_mwh(power_w):
        return float(np.sum(power_w) * dt / 1_000_000.0 * annual_factor)

    observed_charge_kwh = float(np.sum(charge_w) / 1000.0 * dt)
    observed_discharge_kwh = float(np.sum(discharge_w) / 1000.0 * dt)
    observed_soc_change_kwh = dispatch["final_soc_kwh"] - initial_soc_kwh
    observed_total_loss_kwh = (
        observed_charge_kwh - observed_discharge_kwh - observed_soc_change_kwh
    )
    if observed_total_loss_kwh < -1e-5:
        raise AssertionError("BESS annual stored-energy balance failed.")

    annual_h2_kg = float(interval_h2_kg.sum() * annual_factor)
    annual_charge_mwh = observed_charge_kwh / 1000.0 * annual_factor
    annual_discharge_mwh = observed_discharge_kwh / 1000.0 * annual_factor
    annual_total_loss_mwh = max(observed_total_loss_kwh, 0.0) / 1000.0 * annual_factor
    annual_pem_energy_mwh = annualized_mwh(strategy_c_pem_w)
    equivalent_full_cycles_per_year = (
        annual_discharge_mwh / (energy_capacity_kwh / 1000.0)
    )
    active = strategy_c_pem_w >= minimum_pem_w - 1e-9

    return {
        "configuration_power_kw": float(bess_power_kw),
        "configuration_energy_kwh": energy_capacity_kwh,
        "round_trip_efficiency": float(round_trip_efficiency),
        "initial_soc_kwh": initial_soc_kwh,
        "final_soc_kwh": dispatch["final_soc_kwh"],
        "battery_charge_w": charge_w,
        "battery_to_pem_w": discharge_w,
        "battery_soc_kwh": dispatch["soc_kwh"],
        "pem_power_w": strategy_c_pem_w,
        "pv_to_pem_w": base_pv_to_pem_w,
        "purchased_w": base_grid_to_pem_w,
        "pv_export_w": pv_export_w,
        "residual_curtailment_w": residual_after_bess_w,
        "lost_export_w": np.asarray(strategy_b_operation["lost_export_w"], dtype=float),
        "hourly_h2_kg": interval_h2_kg * annual_factor,
        "annual_h2_kg": annual_h2_kg,
        "incremental_h2_kg": annual_h2_kg - strategy_b_operation["annual_h2_kg"],
        "battery_charge_mwh": annual_charge_mwh,
        "battery_discharge_mwh": annual_discharge_mwh,
        "battery_loss_mwh": annual_total_loss_mwh,
        "captured_residual_curtailment_mwh": annual_charge_mwh,
        "residual_curtailment_mwh": annualized_mwh(residual_after_bess_w),
        "total_pem_energy_mwh": annual_pem_energy_mwh,
        "utilization_pct": annual_pem_energy_mwh / (pem_size_mw * 8760.0) * 100.0,
        "equivalent_full_cycles_per_year": equivalent_full_cycles_per_year,
        "cycle_life_years_at_dispatch": (
            BESS_CYCLE_LIFE / equivalent_full_cycles_per_year
            if equivalent_full_cycles_per_year > 0 else np.inf
        ),
        "operating_hours": float(active.sum() * dt),
        "starts": int(np.count_nonzero(active & ~np.r_[False, active[:-1]])),
        "stops": int(np.count_nonzero((~active) & np.r_[False, active[:-1]])),
        "pv_generation_mwh": strategy_b_operation["pv_generation_mwh"],
        "pv_export_mwh": strategy_b_operation["pv_export_mwh"],
        "lost_export_mwh": strategy_b_operation["lost_export_mwh"],
        "purchased_energy_mwh": strategy_b_operation["purchased_energy_mwh"],
    }


def build_strategy_c_bess_sensitivity(
    dam_dispatch_df,
    strategy_b_operation,
    strategy_b_design_row,
    pem_size_mw,
    pem_capex_eur,
):
    """Build technical and economic Strategy C screening tables."""
    configurations = [
        {
            "Configuration": f"Idealized {duration:g} h",
            "Configuration type": "268 kW duration sensitivity",
            "Power (kW)": BESS_TARGET_POWER_KW,
            "Energy (kWh)": energy_kwh,
            "AC round-trip efficiency (%)": BESS_AC_ROUND_TRIP_EFFICIENCY * 100.0,
            "Standby power (kW)": 0.0,
            "OEM mapping": "No, idealized screening size",
        }
        for duration, energy_kwh in zip(BESS_DURATION_HOURS, BESS_ENERGY_KWH)
    ]
    huawei_power_kw = (
        HUAWEI_LUNA_COMPARATOR_CABINETS * HUAWEI_LUNA_CABINET_POWER_KW
    )
    huawei_energy_kwh = (
        HUAWEI_LUNA_COMPARATOR_CABINETS * HUAWEI_LUNA_CABINET_ENERGY_KWH
    )
    configurations.append({
        "Configuration": "Huawei LUNA2000-241-2S1 x3 comparator",
        "Configuration type": "Public OEM technical comparator",
        "Power (kW)": huawei_power_kw,
        "Energy (kWh)": huawei_energy_kwh,
        "AC round-trip efficiency (%)": HUAWEI_LUNA_CABINET_MAX_RTE * 100.0,
        "Standby power (kW)": (
            HUAWEI_LUNA_COMPARATOR_CABINETS * HUAWEI_LUNA_CABINET_STANDBY_KW
        ),
        "OEM mapping": "Yes, technical only, no project quotation",
    })

    physical_records = []
    economic_records = []
    operation_cache = {}
    for configuration in configurations:
        op = simulate_strategy_c_bess_operation(
            dam_dispatch_df=dam_dispatch_df,
            strategy_b_operation=strategy_b_operation,
            pem_size_mw=pem_size_mw,
            bess_power_kw=configuration["Power (kW)"],
            bess_energy_kwh=configuration["Energy (kWh)"],
            round_trip_efficiency=(
                configuration["AC round-trip efficiency (%)"] / 100.0
            ),
            standby_power_kw=configuration["Standby power (kW)"],
        )
        operation_cache[configuration["Configuration"]] = op
        duration_hours = configuration["Energy (kWh)"] / configuration["Power (kW)"]
        physical_records.append({
            **configuration,
            "Nominal duration (h)": duration_hours,
            "Residual curtailment captured (MWh/year)": op[
                "captured_residual_curtailment_mwh"
            ],
            "Battery discharge to PEM (MWh/year)": op["battery_discharge_mwh"],
            "Battery losses incl. year-boundary SOC (MWh/year)": op[
                "battery_loss_mwh"
            ],
            "Equivalent full cycles (cycles/year)": op[
                "equivalent_full_cycles_per_year"
            ],
            "Implied years to 5,000-cycle comparator": op[
                "cycle_life_years_at_dispatch"
            ],
            "Incremental H2 vs Strategy B (kg/year)": op["incremental_h2_kg"],
            "Strategy C H2 (kg/year)": op["annual_h2_kg"],
            "Strategy C PEM utilization (%)": op["utilization_pct"],
            "Initial cyclic SOC (kWh)": op["initial_soc_kwh"],
            "Final cyclic SOC (kWh)": op["final_soc_kwh"],
        })

        for capex_multiplier in BESS_SMALL_PROJECT_MULTIPLIERS:
            bess_capex_eur = calculate_bess_screening_capex(
                configuration["Power (kW)"],
                configuration["Energy (kWh)"],
                capex_multiplier,
            )
            bess_opex_eur = bess_capex_eur * BESS_FIXED_OPEX_FRACTION
            for curtailment_value in BESS_CURTAILMENT_OPPORTUNITY_COSTS_EUR_PER_MWH:
                annual_battery_opportunity_cost_eur = (
                    op["battery_charge_mwh"] * curtailment_value
                )
                incremental_revenue_eur = op["incremental_h2_kg"] * hydrogen_sale_price
                incremental_annual_cashflow_eur = (
                    incremental_revenue_eur
                    - bess_opex_eur
                    - annual_battery_opportunity_cost_eur
                )
                incremental_npv_eur = calculate_npv(
                    bess_capex_eur,
                    incremental_annual_cashflow_eur,
                    discount_rate,
                    project_lifetime_years,
                )
                combined_annual_cost_eur = (
                    strategy_b_design_row[
                        "Total Annual Operating + Energy Cost (€/year)"
                    ]
                    + bess_opex_eur
                    + annual_battery_opportunity_cost_eur
                )
                combined_capex_eur = pem_capex_eur + bess_capex_eur
                combined_lcoh = calculate_discounted_lcoh(
                    combined_capex_eur,
                    combined_annual_cost_eur,
                    op["annual_h2_kg"],
                    discount_rate,
                    project_lifetime_years,
                )
                combined_npv_eur = (
                    strategy_b_design_row["NPV (€)"] + incremental_npv_eur
                )
                cycle_limit_within_horizon = (
                    op["cycle_life_years_at_dispatch"] < project_lifetime_years
                )
                warning = (
                    "SCREENING ONLY: no OEM quote, capacity-fade curve, HVAC trace "
                    "or battery replacement cost. "
                    + (
                        "The 5,000-cycle comparator is reached inside the horizon."
                        if cycle_limit_within_horizon
                        else "The 5,000-cycle comparator is not reached inside the horizon."
                    )
                )
                economic_records.append({
                    "Configuration": configuration["Configuration"],
                    "Configuration type": configuration["Configuration type"],
                    "Power (kW)": configuration["Power (kW)"],
                    "Energy (kWh)": configuration["Energy (kWh)"],
                    "Nominal duration (h)": duration_hours,
                    "CAPEX scale factor": capex_multiplier,
                    "BESS CAPEX (EUR2020)": bess_capex_eur,
                    "BESS fixed OPEX (EUR2020/year)": bess_opex_eur,
                    "Residual-curtailment opportunity cost (EUR/MWh)": curtailment_value,
                    "Battery opportunity cost (EUR/year)": annual_battery_opportunity_cost_eur,
                    "Incremental H2 revenue (EUR/year)": incremental_revenue_eur,
                    "Incremental NPV vs Strategy B (EUR)": incremental_npv_eur,
                    "Combined Strategy C NPV (EUR)": combined_npv_eur,
                    "Combined Strategy C discounted LCOH (EUR/kg H2)": combined_lcoh,
                    "Equivalent full cycles (cycles/year)": op[
                        "equivalent_full_cycles_per_year"
                    ],
                    "Implied years to 5,000-cycle comparator": op[
                        "cycle_life_years_at_dispatch"
                    ],
                    "Incremental H2 vs Strategy B (kg/year)": op[
                        "incremental_h2_kg"
                    ],
                    "Strategy C H2 (kg/year)": op["annual_h2_kg"],
                    "Strategy C PEM utilization (%)": op["utilization_pct"],
                    "Model validity warning": warning,
                })

    return (
        pd.DataFrame(physical_records),
        pd.DataFrame(economic_records),
        operation_cache,
    )


def build_assumptions_register():
    """Return the auditable model-assumption register written by every run."""
    columns = [
        "ID", "Category", "Assumption / parameter", "Central value", "Unit",
        "Classification", "Source / basis", "Source locator",
        "Sensitivity / alternatives", "Material limitation or action",
    ]
    rows = [
        ["A01", "PV", "Primary PV dispatch profile", "PVsyst TMY 5.3 hourly AC output", "", "Model input", "User PVsyst simulation", "data/PVsyst_TMY_5.3.csv", "PVGIS 2023 comparison", "TMY is not time-coincident with 2025-2026 DAM prices"],
        ["A02", "PV", "PV nominal size", 10.0, "MWp", "Model input", "User PVsyst/PVGIS definition", "Project input files", "", "Confirm final DC and AC ratings"],
        ["A03", "Grid", "Selected export limit", 6.0, "MW", "Study assumption", "Project curtailment screening", "main.py", "2-8 MW", "Not a confirmed point-of-connection limit"],
        ["A04", "Market", "DAM observations", "2025-10-01 to 2026-09-24", "359 days", "Official measured source", "TSOC/DSMK weekly exports", "DSMK_DAM_2025-10-01_to_2026-09-24.xlsx", "Annualized by 365/359", "Two nominal half-hours are absent and are not fabricated"],
        ["A05", "Market", "DAM time resolution", 0.5, "h", "Official measured source", "TSOC/DSMK", "DAM workbook", "Fixed", "One observed market year is not a multi-year price forecast"],
        ["A06", "PEM", "Reporting PEM capacity", 1.60, "MW", "Fixed study design point", "User project decision", "main.py", "0.1-3.0 MW diagnostic sweep", "The dynamic balanced optimum is diagnostic and does not overwrite 1.60 MW"],
        ["A07", "PEM", "Minimum operating load", 15, "% rated power", "Literature/model assumption", "Tran system curve plus explicit 15% extrapolation", "main.py", "5%, 10%, 15%, 20%", "Must be replaced by selected OEM guarantee"],
        ["A08", "PEM", "Beginning-of-life SEC", "Load-dependent", "kWh/kg H2", "Literature-based model", "Tran et al. (2026), Table 6", "main.py", "EES gross-stack boundary comparison", "15% point is extrapolated and system boundaries differ"],
        ["A09", "PEM", "Water consumption", 10.0, "L/kg H2", "OEM public comparator", "Siemens Energy", "Supplied Siemens material", "", "Not a feed-water specification or treatment design"],
        ["A10", "PEM", "Ramp-rate constraint", "Not imposed", "", "Omitted by design", "Public OEM ramps are faster than 30-60 minute timestep", "README.md", "", "Representative-day smoothing is display-only"],
        ["A11", "Economics", "Installed PEM CAPEX", 1970, "EUR/kW", "Published benchmark, central screening", "European Hydrogen Observatory 2024", "README.md", "1000, 1500, 1970, 2500 EUR/kW", "Not a project quotation"],
        ["A12", "Economics", "Fixed PEM OPEX", 3.0, "% initial CAPEX/year", "Screening assumption", "Project assumption", "main.py", "30, 50, 64 EUR/kW-year", "Not an OEM service agreement"],
        ["A13", "Finance", "Real discount rate", 8.0, "%", "Screening assumption", "Generic pre-feasibility hurdle rate", "main.py", "Sensitivity required", "Not a project WACC"],
        ["A14", "Finance", "Economic horizon", 15, "years", "Study assumption", "Project definition", "main.py", "", "Not an OEM plant-life guarantee"],
        ["A15", "Revenue", "Hydrogen selling price", 7.0, "EUR/kg H2", "Screening assumption", "European green-hydrogen reference", "main.py", "2, 3, 4, 6, 8, 10 EUR/kg", "No signed offtake agreement"],
        ["A16", "Revenue", "Exportable-PV opportunity cost", "Monthly 2025-2026 EAC 11-kV proxy", "EUR/MWh", "Published regime-specific proxy", "EAC Energy Purchase from RES", "Supplied EAC PDF", "Replace with actual PPA", "Not a generic entitlement for curtailed energy or the project's confirmed tariff"],
        ["A17", "Market", "Variable adder above DAM", grid_variable_adder_eur_per_mwh, "EUR/MWh", "Calculated screening value", "5.00 + 7.50 + 24.50 + 0.51 + 5.00", "main.py; CERA 105/2026 corrected by 117/2026", "30-60 EUR/MWh", "2026 network benchmark applied across the price window, not historical billing; commercial terms are assumptions"],
        ["A18", "Market", "Demand charge", 25.0, "EUR/kW-year", "Generic screening assumption", "No supplier quotation", "main.py", "0-50 EUR/kW-year", "Connection and contract specific"],
        ["A19", "Market", "Fixed supply and metering", 3000.0, "EUR/year", "Generic screening assumption", "No supplier quotation", "main.py", "1500-6000 EUR/year", "Connection and contract specific"],
        ["A20", "Degradation", "Voltage-rise screening rates", "7, 23, 50", "microV/h", "Literature screening", "Zerrougui, Li and Hissel (2025)", "DOI 10.1016/j.ijhydene.2025.152297", "", "Not calibrated to a commercial 1.60 MW stack"],
        ["A21", "Degradation", "Technical EOL relative voltage/SEC rise", 10.0, "%", "Literature comparator", "Arnold et al. (2025) and cited DOE practice", "DOI 10.1016/j.ecmx.2025.101261", "20% economic comparator", "Not a universal OEM threshold"],
        ["A22", "Degradation", "Operating-hour replacement trigger", 30000, "h", "Catalogue comparator", "Danish Energy Agency / DNV", "80 PEMEC 10 MW data sheet", "Siemens 80,000 EOH comparator", "Conservative comparator, not a warranty"],
        ["A23", "Degradation", "Stack replacement-cost proxy", 530.33, "EUR/kW input", "Catalogue proxy", "Danish Energy Agency / DNV", "80 PEMEC 10 MW data sheet", "", "Excludes labour, downtime and recommissioning"],
        ["A24", "Battery", "Strategy C discharge power", 268.0, "kW AC", "User-defined quotation basis", "User BESS enquiry", "main.py", "Huawei x3 comparator 324 kW", "Not optimized continuously"],
        ["A25", "Battery", "Strategy C energy capacities", "134, 268, 536, 1072", "kWh", "Duration sensitivity", "0.5, 1, 2 and 4 h at 268 kW", "main.py", "Huawei x3 comparator 723 kWh", "Idealized sizes may not match standard cabinets"],
        ["A26", "Battery", "Battery charging source", "Residual curtailed PV only", "", "Scope constraint", "User project decision", "main.py", "", "No grid charging and no displacement of exportable PV"],
        ["A27", "Battery", "Battery discharge sink", "PEM only", "", "Scope constraint", "User project decision", "main.py", "", "No grid export, revenue stacking or market arbitrage"],
        ["A28", "Battery", "AC round-trip efficiency", 91.0, "%", "Published catalogue comparator", "Danish Energy Agency 2025", "Lithium-ion battery data sheet", "Huawei maximum 91.3%", "Actual efficiency depends on C-rate, temperature and auxiliaries"],
        ["A29", "Battery", "Stored-energy loss", 0.1, "%/day", "Published catalogue comparator", "Danish Energy Agency 2025", "Lithium-ion battery data sheet", "Huawei standby <=0.150 kW per cabinet", "No measured Cyprus HVAC trace"],
        ["A30", "Battery", "Technical lifetime", 15, "years", "Published catalogue comparator", "Danish Energy Agency 2025", "Lithium-ion battery data sheet", "8-30 year uncertainty", "Calendar ageing depends strongly on temperature and SOC"],
        ["A31", "Battery", "Cycle-life comparator", 5000, "full cycles", "Published catalogue comparator", "Danish Energy Agency 2025", "Lithium-ion battery data sheet", "OEM warranty checks reported separately", "Not a project warranty"],
        ["A32", "Battery", "Reference BESS CAPEX components", "95k/MWh energy + 86k/MW power + 150k/MWh project", "EUR2020", "Published utility-scale comparator", "Danish Energy Agency 2025", "3 MWh / 1.5 MW reference unit", "Scale multiplier 1.00, 1.25, 1.50", "Sub-MWh C&I extrapolation is a major uncertainty"],
        ["A33", "Battery", "Central small-project CAPEX multiplier", 1.25, "factor", "Screening assumption", "Applied because study is below catalogue reference scale", "main.py", "1.00 and 1.50", "Must be replaced by Cyprus EPC quotation"],
        ["A34", "Battery", "Fixed BESS OPEX", 2.9, "% BESS CAPEX/year", "Published catalogue comparator", "Danish Energy Agency 2025 note N", "Lithium-ion battery data sheet", "", "Depends on power-to-energy ratio and service scope"],
        ["A35", "Battery", "Residual-curtailment opportunity cost", 0.0, "EUR/MWh", "Central regime assumption", "Uncompensated curtailment case", "main.py", "50 and 107.96 EUR/MWh", "Actual PPA or compensation regime is unresolved"],
        ["A36", "Battery", "Capacity fade and replacement", "Excluded from headline NPV", "", "Known omission", "No applicable Huawei retention curve or EPC quote", "README.md", "Cycle-limit diagnostic only", "Strategy C NPV is screening-only and optimistic"],
        ["A37", "System boundary", "Hydrogen compression and storage", "Excluded", "", "Known omission", "Outside electrolyser-gate scope", "README.md", "", "Reported LCOH is not delivered-hydrogen cost"],
        ["A38", "Water", "Water-treatment CAPEX/OPEX", "Excluded", "", "Known omission", "No site sample and no selected OEM feed-water limits", "README.md", "", "10 L/kg is consumption, not treatment design"],
    ]
    return pd.DataFrame(rows, columns=columns)


def build_oem_technical_comparison():
    """Return PEM OEM evidence without inventing unavailable lifetime data."""
    return pd.DataFrame([
        {
            "OEM": "ITM Power", "Product": "NEPTUNE V", "Technology": "PEM",
            "Rated capacity (MW)": 5.0, "System SEC at full load (kWh/kg H2)": 55.9,
            "Operating range / minimum load": "12.5-100%",
            "Ramp / startup": "10%/s up, 50%/s down",
            "Hydrogen pressure": "30 barg", "Water requirement": "Potable input, purification included",
            "Published stack-life evidence": "Not published",
            "Public price evidence": "EUR 5 million under ITM standard terms",
            "Model use": "SEC and flexibility comparator, 1000 EUR/kW low-cost sensitivity only",
            "Source": "Neptune_V_Spec_Sheet_2_8_a3f68d4e43.pdf",
        },
        {
            "OEM": "Nel Hydrogen", "Product": "MC250", "Technology": "PEM",
            "Rated capacity (MW)": 1.25, "System SEC at full load (kWh/kg H2)": 59.0,
            "Operating range / minimum load": "10-100% automatic",
            "Ramp / startup": "<15 s minimum-to-full, <=7.4%/s",
            "Hydrogen pressure": "30 barg", "Water requirement": "Not used as model input",
            "Published stack-life evidence": "Not published",
            "Public price evidence": "Not published",
            "Model use": "Closest public commercial power-rating comparator",
            "Source": "MC-Series_PD-0600-0136-Rev-J.pdf",
        },
        {
            "OEM": "Nel Hydrogen", "Product": "MC500", "Technology": "PEM",
            "Rated capacity (MW)": 2.50, "System SEC at full load (kWh/kg H2)": 57.3,
            "Operating range / minimum load": "10-100% automatic",
            "Ramp / startup": "<15 s minimum-to-full, <=7.4%/s",
            "Hydrogen pressure": "30 barg", "Water requirement": "Not used as model input",
            "Published stack-life evidence": "Not published",
            "Public price evidence": "Not published",
            "Model use": "Upper commercial-size comparator",
            "Source": "MC-Series_PD-0600-0136-Rev-J.pdf",
        },
        {
            "OEM": "Siemens Energy", "Product": "Elyzer P-300", "Technology": "PEM",
            "Rated capacity (MW)": 17.5, "System SEC at full load (kWh/kg H2)": np.nan,
            "Operating range / minimum load": "40% per single stack",
            "Ramp / startup": "<1 min startup, up to 10%/s",
            "Hydrogen pressure": "Customized", "Water requirement": "Approximately 10 L/kg H2",
            "Published stack-life evidence": "Design optimized for 80,000 EOH, not a warranty",
            "Public price evidence": "Not published",
            "Model use": "Water, dynamics and lifetime comparator only",
            "Source": "Supplied Siemens Energy electrolyser material",
        },
    ])


def build_bess_oem_comparison():
    """Return public BESS specifications and warranty evidence used as comparators."""
    return pd.DataFrame([
        {
            "OEM": "Huawei", "Product": "LUNA2000-241-2S1", "Chemistry": "LFP",
            "Energy per cabinet (kWh)": 241.0, "Power per cabinet (kW)": 108.0,
            "AC round-trip efficiency (%)": "91.3 maximum",
            "Standby / auxiliary evidence": "Standby <=0.150 kW; auxiliary supply requirement <=5 kW",
            "Temperature evidence": "-30 to 55 C, derating above 50 C",
            "Warranty evidence": "Supplied 2025 policy does not list this later 241 model",
            "Model use": "Three-cabinet 723 kWh / 324 kW technical comparator, no OEM price",
            "Source": "HUAWEI LUNA2000-241-2S1 Datasheet and supplied warranty policy",
        },
        {
            "OEM": "Sungrow", "Product": "PowerStack", "Chemistry": "LFP",
            "Energy per cabinet (kWh)": 229.0, "Power per cabinet (kW)": 110.0,
            "AC round-trip efficiency (%)": "Not used as central input",
            "Standby / auxiliary evidence": "No absolute HVAC trace in supplied datasheet",
            "Temperature evidence": "-30 to 50 C, derating above 45 C",
            "Warranty evidence": "Premium floor 65% until 2,000 cycles or 5 years at specified conditions",
            "Model use": "Cross-OEM warranty stress comparator only",
            "Source": "Supplied PowerStack datasheet and 2023 warranty",
        },
        {
            "OEM": "BYD", "Product": "Battery-Box Commercial", "Chemistry": "LFP",
            "Energy per cabinet (kWh)": np.nan, "Power per cabinet (kW)": np.nan,
            "AC round-trip efficiency (%)": "Not used as central input",
            "Standby / auxiliary evidence": "Not used",
            "Temperature evidence": "Cycle allowance falls with higher temperature",
            "Warranty evidence": "At least 70% usable energy, 3,000 cycles at 30-45 C or 10 years",
            "Model use": "Hot-climate cross-OEM warranty comparator only",
            "Source": "Supplied BYD Battery-Box Commercial warranty letter",
        },
    ])


def write_reference_tables():
    """Write assumptions and OEM evidence beside the generated result tables."""
    build_assumptions_register().to_csv(
        os.path.join(TABLES_DIR, "assumptions_register.csv"), index=False
    )
    build_oem_technical_comparison().to_csv(
        os.path.join(TABLES_DIR, "oem_technical_comparison.csv"), index=False
    )
    build_bess_oem_comparison().to_csv(
        os.path.join(TABLES_DIR, "bess_oem_comparison.csv"), index=False
    )


###


#
# =========================================
# EXERGY ANALYSIS FUNCTIONS (load-weighted)
# =========================================

EX_H2_KWH_PER_KG = 32.56  # kWh/kg, standard chemical exergy of H2(g) (Szargut et al., 1988)



# =========================================
# PEM PART-LOAD PERFORMANCE
# =========================================

# Base-case minimum operating load retained from Project 1.1-1.4.
MIN_PEM_LOAD_FRACTION = 0.15

# Existing minimum-load sensitivity settings are retained for backward
# compatibility with earlier project outputs; they are not used to define
# the v1.5 base-case SEC curve.
MIN_LOAD_SENSITIVITY = (0.05, 0.10, 0.15, 0.20)

# MW-scale PEM SYSTEM specific electricity consumption curve.
#
# Source for 25%-100%:
# Tran et al. (2026), "Hydrogen production system scaling using a
# high-fidelity simulation-optimization framework",
# Energy Conversion and Management 357, 121416, Table 6.
#
# IMPORTANT:
# - These are SYSTEM-level SEC values, including modeled Balance-of-Plant
#   energy consumption.
# - The published Tran data cover 25%-100% load.
# - The 15% point (47.9 kWh/kg) is NOT a published Tran value.
#   It is an explicit linear extrapolation from the published
#   25% = 48.9 and 35% = 49.9 kWh/kg points.
# - Therefore the 15% point is a modelling assumption and should be reported
#   as extrapolated rather than experimentally validated.
#
# Linear extrapolation:
# SEC_15 = 48.9 - (49.9 - 48.9) / (0.35 - 0.25) * (0.25 - 0.15)
#        = 47.9 kWh/kg H2

TRAN_SYSTEM_LOAD_FRACTIONS = np.array([
    0.15,
    0.25,
    0.35,
    0.50,
    0.65,
    0.75,
    0.85,
    1.00
])

TRAN_SYSTEM_SEC_KWH_PER_KG = np.array([
    47.9,  # extrapolated modelling assumption
    48.9,  # Tran et al. (2026), Table 6
    49.9,
    51.2,
    52.2,
    52.9,
    53.4,
    54.0
])


_EES_PERFORMANCE_MAP = None


def load_ees_performance_map(path=EES_PERFORMANCE_MAP_PATH):
    """Load and validate the EES gross-stack PEM performance map."""
    ees_map = pd.read_csv(path, encoding="utf-8-sig")
    ees_map.columns = ees_map.columns.str.strip()
    # Accept both the normalized project schema and the original EES export
    # supplied by the user. This prevents a harmless header-format difference
    # from stopping the entire techno-economic run.
    column_aliases = {
        "Load": "load_fraction",
        "J input": "j_A_cm2",
        "v cell": "V_cell",
        "SEC gross": "SEC_kWh_kg",
        "eta LVH": "eta_LHV",
        "eta LHV": "eta_LHV",
        "Q stack": "heat_kW",
    }
    ees_map = ees_map.rename(columns=column_aliases)
    if "load_fraction" in ees_map.columns:
        load_text = ees_map["load_fraction"].astype(str).str.strip()
        is_percent = load_text.str.endswith("%")
        numeric_load = pd.to_numeric(
            load_text.str.rstrip("%"), errors="coerce"
        ).astype(float)
        numeric_load.loc[is_percent] = numeric_load.loc[is_percent] / 100.0
        ees_map["load_fraction"] = numeric_load
    required = {"load_fraction", "SEC_kWh_kg", "eta_LHV", "V_cell"}
    missing = required.difference(ees_map.columns)
    if missing:
        raise ValueError(f"EES performance map missing columns: {sorted(missing)}")
    for column in required:
        ees_map[column] = pd.to_numeric(ees_map[column], errors="coerce")
    ees_map = (
        ees_map.dropna(subset=list(required))
        .sort_values("load_fraction")
        .drop_duplicates("load_fraction")
        .reset_index(drop=True)
    )
    if ees_map.empty:
        raise ValueError("EES performance map contains no valid numeric rows.")
    if ees_map["load_fraction"].min() > MIN_PEM_LOAD_FRACTION + 1e-9:
        raise ValueError("EES map does not cover the configured PEM minimum load.")
    if ees_map["load_fraction"].max() < 1.0 - 1e-9:
        raise ValueError("EES map does not cover full PEM load.")
    if (ees_map["SEC_kWh_kg"] <= 0).any():
        raise ValueError("EES SEC values must be positive.")
    return ees_map


def get_ees_performance_map():
    global _EES_PERFORMANCE_MAP
    if _EES_PERFORMANCE_MAP is None:
        _EES_PERFORMANCE_MAP = load_ees_performance_map()
    return _EES_PERFORMANCE_MAP


def calculate_start_time_bounds(starts):
    """
    Technology-level start-duration bounds from [DYN-1].

    The model cannot identify whether an individual start is warm or cold.
    Therefore it reports two bounding cases rather than applying an invented
    startup-energy penalty.
    """
    starts = int(starts)
    return {
        "all_warm_start_upper_bound_h": (
            starts * PEM_WARM_START_MAX_SECONDS / 3600.0
        ),
        "all_cold_start_lower_bound_h": (
            starts * PEM_COLD_START_MIN_MINUTES / 60.0
        ),
        "all_cold_start_upper_bound_h": (
            starts * PEM_COLD_START_MAX_MINUTES / 60.0
        ),
    }


def calculate_degradation_screening(
    strategy_name,
    pem_power_w,
    pem_size_w,
    project_years=project_lifetime_years,
    interval_hours=1.0,
    annualization_factor=1.0,
):
    """
    Literature-bounded voltage-degradation screening based on [DEG-1].

    It converts constant voltage-rise rates into annual and project-end voltage
    rise using actual PEM-on hours. Relative SEC rise is approximated by
    delta_V / load-weighted EES cell voltage. This is a sensitivity diagnostic,
    not a lifetime prediction or a stack-replacement model.
    """
    pem_power_w = np.asarray(pem_power_w, dtype=float)
    load_fraction = pem_power_w / float(pem_size_w)
    active = load_fraction >= MIN_PEM_LOAD_FRACTION
    operating_hours = float(active.sum() * interval_hours * annualization_factor)

    ees_map = get_ees_performance_map()
    active_load = np.clip(
        load_fraction[active],
        MIN_PEM_LOAD_FRACTION,
        1.0
    )
    if operating_hours > 0:
        cell_voltage = np.interp(
            active_load,
            ees_map["load_fraction"].to_numpy(dtype=float),
            ees_map["V_cell"].to_numpy(dtype=float),
        )
        weights = pem_power_w[active]
        weighted_cell_voltage_v = float(np.average(cell_voltage, weights=weights))
    else:
        weighted_cell_voltage_v = np.nan

    records = []
    for scenario, rate_uv_per_h in PEM_VOLTAGE_DEGRADATION_SCENARIOS_UV_PER_H.items():
        annual_voltage_rise_v = rate_uv_per_h * operating_hours / 1_000_000.0
        project_voltage_rise_v = annual_voltage_rise_v * project_years
        annual_relative_sec_rise_pct = (
            annual_voltage_rise_v / weighted_cell_voltage_v * 100.0
            if weighted_cell_voltage_v > 0 else np.nan
        )
        project_relative_sec_rise_pct = (
            project_voltage_rise_v / weighted_cell_voltage_v * 100.0
            if weighted_cell_voltage_v > 0 else np.nan
        )
        records.append({
            "Strategy": strategy_name,
            "Scenario": scenario,
            "Rate (microV/h)": rate_uv_per_h,
            "PEM-on Hours (h/year)": operating_hours,
            "Load-weighted EES Cell Voltage (V)": weighted_cell_voltage_v,
            "Annual Voltage Rise (mV/year)": annual_voltage_rise_v * 1000.0,
            "Approx. Annual SEC Rise (%)": annual_relative_sec_rise_pct,
            f"Uncapped Linear {project_years}-Year Voltage Rise (V)": project_voltage_rise_v,
            f"Uncapped Linear {project_years}-Year SEC Rise (%)": project_relative_sec_rise_pct,
            "Replacement / Nonlinear Model Required": (
                "YES" if project_relative_sec_rise_pct > 20.0 else "REVIEW"
            ),
        })
    return pd.DataFrame(records)


def simulate_stack_lifecycle(
    strategy_name,
    hourly_bol_h2_kg,
    pem_power_w,
    pem_size_mw,
    annual_operating_cost_eur,
    initial_system_capex_eur,
    discount_rate=discount_rate,
    project_years=project_lifetime_years,
    interval_hours=1.0,
    active_time_scale=1.0,
    degradation_rate_uv_per_h=None,
    reference_cell_voltage_v=None,
    degradation_scenario="DEA/DNV steady-state comparator",
):
    """Screen stack degradation, replacement timing and discounted economics.

    The annual hourly dispatch is repeated for every project year. Stack SEC is
    assumed to rise linearly with active operating hours using the DEA/DNV
    steady-state rate. At each active hour hydrogen output is reduced by
    1 / (1 + relative SEC rise). A stack replacement is triggered by the first
    available quantitative criterion:

      1. 30,000 accumulated stack operating hours [LIFE-1], or
      2. 10% relative cell-voltage / SEC rise benchmark [LIFE-2].

    Absolute maximum cell voltage, hydrogen crossover, pressure/safety limits
    and OEM efficiency warranties are intentionally not evaluated because no
    stack-specific limits are available. The 20% economic threshold from
    [LIFE-2] is reported as a study-specific comparator, not used as a universal
    trigger. Replacement resets only stack degradation and stack hours. No BoP
    ageing rate is available, so BoP state is retained and explicitly reported
    as unquantified rather than being reset to new.
    """
    hourly_bol_h2_kg = np.asarray(hourly_bol_h2_kg, dtype=float)
    pem_power_w = np.asarray(pem_power_w, dtype=float)
    if len(hourly_bol_h2_kg) != len(pem_power_w) or len(pem_power_w) == 0:
        raise ValueError("Lifecycle H2 and power profiles must have equal non-zero length.")
    effective_interval_hours = float(interval_hours) * float(active_time_scale)
    if effective_interval_hours <= 0.0:
        raise ValueError("Lifecycle interval duration must be positive.")

    active = pem_power_w > 0.0
    if degradation_rate_uv_per_h is None:
        degradation_fraction_per_hour = (
            PEM_STEADY_DEGRADATION_PCT_PER_1000_H / 100.0 / 1000.0
        )
        effective_relative_sec_rise_pct_per_1000_h = (
            PEM_STEADY_DEGRADATION_PCT_PER_1000_H
        )
    else:
        if reference_cell_voltage_v is None or reference_cell_voltage_v <= 0.0:
            raise ValueError(
                "A positive reference cell voltage is required for a microV/h lifecycle scenario."
            )
        degradation_fraction_per_hour = (
            float(degradation_rate_uv_per_h) * 1e-6
            / float(reference_cell_voltage_v)
        )
        effective_relative_sec_rise_pct_per_1000_h = (
            degradation_fraction_per_hour * 1000.0 * 100.0
        )
    hours_to_relative_eol = (
        PEM_TECHNICAL_EOL_RELATIVE_VOLTAGE_SEC_RISE_PCT / 100.0
        / degradation_fraction_per_hour
    )
    effective_trigger_hours = min(
        PEM_STACK_REPLACEMENT_HOURS,
        hours_to_relative_eol,
    )
    stack_replacement_cost_eur = (
        pem_size_mw * 1000.0 * PEM_STACK_COST_PROXY_EUR_PER_KW
    )

    stack_hours = 0.0
    bop_age_hours = 0.0
    replacement_events = []
    annual_records = []
    discounted_h2_kg = 0.0
    discounted_operating_cost_eur = 0.0
    discounted_replacement_cost_eur = 0.0
    terminal_retirement_applied = False
    terminal_retirement_time_years = np.nan
    first_eol_time_years = np.nan
    annual_active_hours = float(np.count_nonzero(active)) * effective_interval_hours
    retired = False

    for year in range(1, project_years + 1):
        annual_h2_degraded_kg = 0.0
        for interval_index in range(len(active)):
            if retired:
                continue
            if active[interval_index]:
                # Replace immediately before the first hour that would breach
                # the earliest quantified EOL criterion.
                if stack_hours + effective_interval_hours > effective_trigger_hours + 1e-12:
                    event_time_years = (year - 1) + interval_index / len(active)
                    if np.isnan(first_eol_time_years):
                        first_eol_time_years = event_time_years
                    remaining_current_year_hours = float(
                        np.count_nonzero(active[interval_index:])
                    ) * effective_interval_hours
                    remaining_project_active_hours = (
                        remaining_current_year_hours
                        + (project_years - year) * annual_active_hours
                    )
                    # Horizon rule. Do not install a complete replacement when
                    # less than one full stack-life remains inside the study
                    # period. Retire the exhausted stack instead, without
                    # inventing a terminal salvage value.
                    if remaining_project_active_hours < effective_trigger_hours:
                        retired = True
                        terminal_retirement_applied = True
                        terminal_retirement_time_years = event_time_years
                        continue
                    discounted_cost = stack_replacement_cost_eur / (
                        (1.0 + discount_rate) ** event_time_years
                    )
                    replacement_events.append({
                        "Strategy": strategy_name,
                        "Replacement number": len(replacement_events) + 1,
                        "Project time (years)": event_time_years,
                        "Completed stack operating hours": stack_hours,
                        "Trigger": "first of 30,000 h or 10% voltage/SEC rise",
                        "Undiscounted replacement cost (EUR)": stack_replacement_cost_eur,
                        "Discounted replacement cost (EUR)": discounted_cost,
                    })
                    discounted_replacement_cost_eur += discounted_cost
                    stack_hours = 0.0

                relative_sec_rise = stack_hours * degradation_fraction_per_hour
                annual_h2_degraded_kg += (
                    hourly_bol_h2_kg[interval_index] / (1.0 + relative_sec_rise)
                )
                stack_hours += effective_interval_hours
                bop_age_hours += effective_interval_hours

        discount_factor = (1.0 + discount_rate) ** year
        discounted_h2_kg += annual_h2_degraded_kg / discount_factor
        discounted_operating_cost_eur += annual_operating_cost_eur / discount_factor
        annual_records.append({
            "Strategy": strategy_name,
            "Year": year,
            "H2 with stack degradation (kg)": annual_h2_degraded_kg,
            "End-year stack operating hours": stack_hours,
            "End-year relative SEC rise (%)": (
                stack_hours * degradation_fraction_per_hour * 100.0
            ),
            "Cumulative BoP active hours, retained (h)": bop_age_hours,
        })

    discounted_total_cost_eur = (
        initial_system_capex_eur
        + discounted_operating_cost_eur
        + discounted_replacement_cost_eur
    )
    if np.isnan(first_eol_time_years) and annual_active_hours > 0.0:
        # Report the projected first EOL even when it lies beyond the study
        # horizon. This is an operating-hours projection, not a warranty.
        first_eol_time_years = effective_trigger_hours / annual_active_hours
    lifecycle_lcoh_eur_per_kg = (
        discounted_total_cost_eur / discounted_h2_kg
        if discounted_h2_kg > 0 else np.inf
    )
    discounted_revenue_eur = discounted_h2_kg * hydrogen_sale_price
    lifecycle_npv_eur = (
        discounted_revenue_eur
        - discounted_operating_cost_eur
        - discounted_replacement_cost_eur
        - initial_system_capex_eur
    )

    summary = {
        "Strategy": strategy_name,
        "Degradation scenario": degradation_scenario,
        "Degradation rate (microV/h)": degradation_rate_uv_per_h,
        "Reference cell voltage (V)": reference_cell_voltage_v,
        "Effective relative SEC rise per 1,000 h (%)": (
            effective_relative_sec_rise_pct_per_1000_h
        ),
        "First EOL time (project years)": first_eol_time_years,
        "Quantified EOL trigger hours": effective_trigger_hours,
        "Technical voltage/SEC threshold (%)": (
            PEM_TECHNICAL_EOL_RELATIVE_VOLTAGE_SEC_RISE_PCT
        ),
        "Economic-study comparator threshold (%)": (
            PEM_ECONOMIC_REFERENCE_SEC_RISE_PCT
        ),
        "Replacement cost proxy (EUR/kW)": PEM_STACK_COST_PROXY_EUR_PER_KW,
        "Replacement count": len(replacement_events),
        "Discounted replacement cost (EUR)": discounted_replacement_cost_eur,
        "Discounted degraded H2 (kg)": discounted_h2_kg,
        "Lifecycle-adjusted discounted LCOH (EUR/kg)": lifecycle_lcoh_eur_per_kg,
        "Lifecycle-adjusted NPV (EUR)": lifecycle_npv_eur,
        "Absolute voltage limit evaluated": "NO - OEM data required",
        "Hydrogen crossover limit evaluated": "NO - OEM data required",
        "Pressure/safety limit evaluated": "NO - OEM data required",
        "BoP reset after stack replacement": "NO",
        "BoP ageing quantified": "NO - source data unavailable",
        "Terminal retirement instead of end-horizon replacement": (
            "YES" if terminal_retirement_applied else "NO"
        ),
        "Terminal retirement project time (years)": terminal_retirement_time_years,
    }
    return summary, pd.DataFrame(annual_records), pd.DataFrame(replacement_events)


def pem_specific_consumption(load_fraction, model="TRAN"):
    """
    Return PEM system specific electricity consumption [kWh/kg H2]
    as a function of electrolyzer load fraction.

    Published system-level points from Tran et al. (2026) are used from
    25% to 100% load. The 15% point is an explicitly labelled linear
    extrapolation used to preserve the Project 1.1-1.4 base-case
    minimum-load assumption.

    Values below 15% load are returned as NaN because the PEM is assumed
    not to operate below the base-case minimum load.
    """

    load_fraction = np.asarray(load_fraction, dtype=float)

    sec = np.full(load_fraction.shape, np.nan)

    active = load_fraction >= MIN_PEM_LOAD_FRACTION

    model = model.strip().upper()
    if model == "TRAN":
        map_load = TRAN_SYSTEM_LOAD_FRACTIONS
        map_sec = TRAN_SYSTEM_SEC_KWH_PER_KG
    elif model == "EES":
        ees_map = get_ees_performance_map()
        map_load = ees_map["load_fraction"].to_numpy(dtype=float)
        map_sec = ees_map["SEC_kWh_kg"].to_numpy(dtype=float)
    else:
        raise ValueError("model must be either 'TRAN' or 'EES'.")

    sec[active] = np.interp(
        np.clip(load_fraction[active], MIN_PEM_LOAD_FRACTION, 1.0),
        map_load,
        map_sec
    )

    return sec


def calculate_hydrogen_from_pem_input(pem_input_w, pem_size_w, model="TRAN"):
    """
    Calculate hourly and annual hydrogen production from available
    PEM electrical input.

    The electrolyzer operates only when available power is at least
    15% of rated PEM power.
    """

    pem_input_w = np.asarray(pem_input_w, dtype=float)

    load_fraction = pem_input_w / pem_size_w

    active = load_fraction >= MIN_PEM_LOAD_FRACTION

    # Electricity actually accepted by the PEM.
    # Below minimum load, the electrolyzer is OFF.
    pem_power_used_w = np.where(
        active,
        pem_input_w,
        0.0
    )

    spec_consumption = pem_specific_consumption(load_fraction, model=model)

    hourly_energy_kwh = pem_power_used_w / 1000.0

    hourly_h2_kg = np.zeros_like(hourly_energy_kwh)

    hourly_h2_kg[active] = (
        hourly_energy_kwh[active]
        / spec_consumption[active]
    )

    annual_h2_kg = hourly_h2_kg.sum()

    return hourly_h2_kg, annual_h2_kg

###
def calculate_weighted_exergy_efficiency(pem_input_w, pem_size_w):
    """
    Load-weighted exergy and LHV efficiency using the same
    minimum-load constraint and partial-load SEC curve as the
    hydrogen-production model.
    """

    pem_input_w = np.asarray(pem_input_w, dtype=float)

    load_fraction = pem_input_w / pem_size_w
    active = load_fraction >= MIN_PEM_LOAD_FRACTION

    pem_power_used_w = np.where(
        active,
        pem_input_w,
        0.0
    )

    spec_consumption = pem_specific_consumption(load_fraction)

    hourly_energy_kwh = pem_power_used_w / 1000.0

    hourly_h2_kg = np.zeros_like(hourly_energy_kwh)

    hourly_h2_kg[active] = (
        hourly_energy_kwh[active]
        / spec_consumption[active]
    )

    annual_h2_kg = hourly_h2_kg.sum()
    annual_electrical_input_kwh = hourly_energy_kwh.sum()

    exergy_out_kwh = annual_h2_kg * EX_H2_KWH_PER_KG

    exergy_efficiency = (
        exergy_out_kwh / annual_electrical_input_kwh
        if annual_electrical_input_kwh > 0
        else 0.0
    )

    lhv_kwh_per_kg = 33.33

    energy_efficiency_lhv = (
        annual_h2_kg * lhv_kwh_per_kg
        / annual_electrical_input_kwh
        if annual_electrical_input_kwh > 0
        else 0.0
    )

    energy_weighted_avg_spec_consumption = (
        annual_electrical_input_kwh / annual_h2_kg
        if annual_h2_kg > 0
        else np.nan
    )

    return (
        annual_h2_kg,
        exergy_efficiency,
        energy_efficiency_lhv,
        energy_weighted_avg_spec_consumption
    )
#


# =========================================
# v1.5 MULTI-OBJECTIVE PEM SIZING
# =========================================

def build_multiobjective_pem_sizing_table(
    df,
    grid_limit_mw,
    capex_per_kw,
    hydrogen_price_eur_per_kg,
    discount_rate,
    project_lifetime_years,
    start_mw=0.10,
    stop_mw=3.00,
    step_mw=0.01
):
    """
    Fine PEM-size sweep using the current v1.5 dispatch and Tran-based
    system SEC curve.

    Objectives:
      - maximize curtailment recovery
      - minimize discounted LCOH
      - maximize NPV at the reference H2 price
    """
    sizes = np.round(np.arange(start_mw, stop_mw + step_mw / 2, step_mw), 2)

    gross_curt_w = np.maximum(
        df["P"].to_numpy(dtype=float) - grid_limit_mw * 1e6,
        0.0
    )
    gross_curt_mwh = gross_curt_w.sum() / 1e6

    records = []

    for pem_mw in sizes:
        op = simulate_nonhybrid_operation(df, pem_mw, grid_limit_mw)

        pem_capex_eur = pem_mw * 1000 * capex_per_kw
        annual_opex_eur = pem_capex_eur * pem_opex_fraction
        opportunity_cost_eur, _ = calculate_pv_opportunity_cost_proxy(
            df, op["lost_export_w"]
        )

        annual_h2_kg = op["annual_h2_kg"]
        residual_curt_mwh = op["residual_curtailment_w"].sum() / 1e6
        avoided_curt_mwh = max(gross_curt_mwh - residual_curt_mwh, 0.0)

        recovery_pct = (
            avoided_curt_mwh / gross_curt_mwh * 100
            if gross_curt_mwh > 0 else 0.0
        )

        discounted_lcoh = calculate_discounted_lcoh(
            pem_capex_eur,
            annual_opex_eur + opportunity_cost_eur,
            annual_h2_kg,
            discount_rate,
            project_lifetime_years
        )

        annual_revenue_eur = annual_h2_kg * hydrogen_price_eur_per_kg
        annual_cashflow_eur = (
            annual_revenue_eur
            - annual_opex_eur
            - opportunity_cost_eur
        )

        npv_eur = calculate_npv(
            pem_capex_eur,
            annual_cashflow_eur,
            discount_rate,
            project_lifetime_years
        )

        records.append({
            "PEM Size (MW)": pem_mw,
            "Curtailment Recovery (%)": recovery_pct,
            "Discounted LCOH (EUR/kg H2)": discounted_lcoh,
            "NPV (EUR)": npv_eur,
            "H2 (kg/year)": annual_h2_kg,
            "Utilization (%)": op["utilization_pct"],
            "Avoided Curtailment (MWh)": avoided_curt_mwh,
            "Residual Curtailment (MWh)": residual_curt_mwh,
            "Lost Export Energy (MWh)": op["lost_export_mwh"],
            "PV Opportunity Cost (EUR/year)": opportunity_cost_eur
        })

    table = pd.DataFrame(records)

    # Pareto-efficient candidates for:
    # maximize recovery, minimize LCOH, maximize NPV.
    vals = table[
        ["Curtailment Recovery (%)",
         "Discounted LCOH (EUR/kg H2)",
         "NPV (EUR)"]
    ].to_numpy(dtype=float)

    pareto_mask = np.ones(len(table), dtype=bool)

    for i in range(len(table)):
        if not pareto_mask[i]:
            continue

        ri, li, ni = vals[i]

        dominates_i = (
            (vals[:, 0] >= ri) &
            (vals[:, 1] <= li) &
            (vals[:, 2] >= ni) &
            (
                (vals[:, 0] > ri) |
                (vals[:, 1] < li) |
                (vals[:, 2] > ni)
            )
        )

        if dominates_i.any():
            pareto_mask[i] = False

    table["Pareto Efficient"] = pareto_mask

    # Equal-weight normalized distance to ideal point.
    def norm01(series, higher_is_better=True):
        s = np.asarray(series, dtype=float)
        span = s.max() - s.min()
        if span <= 0:
            return np.ones_like(s)
        z = (s - s.min()) / span
        return z if higher_is_better else 1.0 - z

    recovery_score = norm01(table["Curtailment Recovery (%)"], True)
    lcoh_score = norm01(table["Discounted LCOH (EUR/kg H2)"], False)
    npv_score = norm01(table["NPV (EUR)"], True)

    distance = np.sqrt(
        ((1.0 - recovery_score) ** 2
         + (1.0 - lcoh_score) ** 2
         + (1.0 - npv_score) ** 2) / 3.0
    )

    table["Balanced Distance to Ideal"] = distance
    table["Balanced Compromise"] = False

    balanced_idx = int(np.argmin(distance))
    table.loc[balanced_idx, "Balanced Compromise"] = True

    return table


def summarize_multiobjective_sizing(table):
    balanced = table.loc[table["Balanced Compromise"]].iloc[0]
    min_lcoh = table.loc[table["Discounted LCOH (EUR/kg H2)"].idxmin()]
    max_npv = table.loc[table["NPV (EUR)"].idxmax()]

    reached = table[table["Curtailment Recovery (%)"] >= 99.9]
    first_999 = reached.iloc[0] if not reached.empty else None

    return balanced, min_lcoh, max_npv, first_999


def plot_multiobjective_pem_sizing(table):
    fig_number, fig_title = next_fig("Multi-Objective PEM Sizing")

    fig, ax1 = plt.subplots(figsize=(10, 6))

    ax1.plot(
        table["PEM Size (MW)"],
        table["Curtailment Recovery (%)"],
        label="Curtailment recovery (%)"
    )
    ax1.set_xlabel("PEM Size (MW)")
    ax1.set_ylabel("Curtailment Recovery (%)")
    ax1.grid(True)

    ax2 = ax1.twinx()
    ax2.plot(
        table["PEM Size (MW)"],
        table["Discounted LCOH (EUR/kg H2)"],
        label="Discounted LCOH (€/kg H2)",
        linestyle="--"
    )
    ax2.set_ylabel("Discounted LCOH (€/kg H2)")

    balanced = table.loc[table["Balanced Compromise"]].iloc[0]
    ax1.scatter(
        [balanced["PEM Size (MW)"]],
        [balanced["Curtailment Recovery (%)"]],
        s=90,
        zorder=5,
        label="Balanced compromise (diagnostic)"
    )

    ax1.set_title(fig_title)
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="best")

    plt.tight_layout()
    save_figure_png(plt.gcf(), 
        os.path.join(
            FIGURES_DIR,
            f"figure{fig_number:02d}_multiobjective_pem_sizing.png"
        ),
        dpi=300,
        bbox_inches="tight"
    )
    plt.show()


# =========================================
# PLOTTING FUNCTIONS
# =========================================


fig_counter = [0]  
def next_fig(title):
    fig_counter[0] += 1
    return fig_counter[0], f"Figure {fig_counter[0]}: {title}"
    #return f"Figure {fig_counter[0]}: {title}"


def plot_hourly_pv_output(df):
    fig_number, fig_title = next_fig("Hourly PV Power Output")
    plt.figure(figsize=(12, 5))
    plt.plot(df["P"])
    plt.title(fig_title)
    plt.xlabel("Hour")
    plt.ylabel("PV Power (W)")
    save_figure_png(plt.gcf(), os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_hourly_pv_output.png"), dpi=300, bbox_inches="tight")
    plt.show()

def plot_two_day_pv_output(df):
    fig_number, fig_title = next_fig("2-Day PV Output")
    plt.figure(figsize=(12, 5))
    plt.plot(df["P"][0:48])
    plt.title(fig_title)
    plt.xlabel("Hour")
    plt.ylabel("PV Power (W)")
    save_figure_png(plt.gcf(), os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_two_day_pv_output.png"), dpi=300, bbox_inches="tight")
    plt.show()

def pem_vs_hydrogen(pem_sizes_results, h2_results):
    fig_number, fig_title = next_fig("PEM Size vs Hydrogen Production")
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(pem_sizes_results, h2_results, marker="o")
    ax.set_title(fig_title)
    ax.set_xlabel("PEM Size (MW)")
    ax.set_ylabel("Hydrogen Production (kg/year)")
    ax.grid(True)
    fig.tight_layout()
    save_figure_png(fig, 
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_pem_vs_h2.png"),
        dpi=300,
        bbox_inches="tight",
    )
    plt.close(fig)

def pem_vs_utilization(pem_sizes_results, utilization_results):
    fig_number, fig_title = next_fig("PEM Size vs Utilization")
    plt.figure(figsize=(8, 5))
    plt.plot(pem_sizes_results, utilization_results, marker="o")
    plt.title(fig_title)
    plt.xlabel("PEM Size (MW)")
    plt.ylabel("Utilization (%)")
    plt.grid()
    save_figure_png(plt.gcf(), os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_pem_vs_utilization.png"), dpi=300, bbox_inches="tight")
    plt.show()
###
def plot_annualized_lcoh_vs_pem_size(results_table):

    fig_number, fig_title = next_fig(
        "Annualized LCOH vs PEM Size"
    )

    project_lifetime = project_lifetime_years

    crf = (
        discount_rate
        * (1 + discount_rate) ** project_lifetime
        / ((1 + discount_rate) ** project_lifetime - 1)
    )

    plt.figure(figsize=(10, 6))

    for capex_per_kw in pem_capex_scenarios:

        lcoh_values = []

        for _, row in results_table.iterrows():

            pem_mw = row["PEM Size (MW)"]
            h2_kg_year = row["H2 (kg/year)"]

            pem_kw = pem_mw * 1000

            total_capex = pem_kw * capex_per_kw
            annualized_capex = total_capex * crf
            annual_opex = total_capex * pem_opex_fraction
            row_op = simulate_nonhybrid_operation(
                df, pem_mw, selected_grid_limit_mw
            )
            annual_opportunity_cost, _ = calculate_pv_opportunity_cost_proxy(
                df, row_op["lost_export_w"]
            )

            if h2_kg_year > 0:
                annualized_lcoh = (
                    annualized_capex
                    + annual_opex
                    + annual_opportunity_cost
                ) / h2_kg_year
            else:
                annualized_lcoh = np.nan

            lcoh_values.append(annualized_lcoh)

        plt.plot(
            results_table["PEM Size (MW)"],
            lcoh_values,
            marker="o",
            label=f"CAPEX {capex_per_kw} €/kW"
        )

    plt.xlabel("PEM Size (MW)")
    plt.ylabel("Simplified Annualized LCOH (€/kg H2)")
    plt.title(fig_title)
    plt.grid(True)
    plt.legend()
    # Keep the explanatory annotation fully inside the axes.
    # The arrow points to the higher-PEM region while the text position is
    # expressed in axes-fraction coordinates, so it remains in bounds even
    # when LCOH values change after economic-assumption updates.
    y_anchor = np.interp(
        2.0,
        results_table["PEM Size (MW)"],
        lcoh_values
    )
    plt.annotate(
        "Higher PEM capacity increases\nCAPEX faster than H2 output",
        xy=(2.0, y_anchor),
        xycoords="data",
        xytext=(0.58, 0.78),
        textcoords="axes fraction",
        arrowprops=dict(
            arrowstyle="->",
            connectionstyle="arc3,rad=0.05"
        ),
        fontsize=9,
        ha="left",
        va="center",
        bbox=dict(
            boxstyle="round,pad=0.3",
            facecolor="white",
            alpha=0.8
        )
    )
    
    plt.tight_layout()

    save_figure_png(plt.gcf(), 
        os.path.join(
            FIGURES_DIR,
            f"figure{fig_number:02d}_annualized_lcoh_vs_pem_size.png"
        ),
        dpi=300,
        bbox_inches="tight"
    )

    plt.show()


###
def plot_curtailment_recovery_vs_pem_size(pem_sizes_mw, curtailment_recovery_results):
    """Plot recovery curve and explicitly mark the two engineering design points."""
    fig_number, fig_title = next_fig("Curtailment Recovery vs PEM Size")
    fig, ax = plt.subplots(figsize=(10, 6))

    x = np.asarray(pem_sizes_mw, dtype=float)
    y = np.asarray(curtailment_recovery_results, dtype=float)
    ax.plot(x, y, marker="o", linewidth=1.8, label="Curtailment recovery")

    # Fixed reporting point evaluated directly, not interpolated from a coarse sweep.
    fixed_op = simulate_nonhybrid_operation(df, selected_pem_mw, selected_grid_limit_mw)
    gross_mwh = np.maximum(df["P"].to_numpy() - selected_grid_limit_mw * 1e6, 0).sum() / 1e6
    recovery_ref = 100.0 * (gross_mwh - fixed_op["residual_curtailment_mwh"]) / gross_mwh
    ax.scatter([selected_pem_mw], [recovery_ref], s=90, zorder=5)
    ax.annotate(
        f"Fixed reporting design\n{selected_pem_mw:.2f} MW, {recovery_ref:.1f}% recovery",
        xy=(selected_pem_mw, recovery_ref),
        xytext=(selected_pem_mw + 0.35, max(recovery_ref - 12, 5)),
        arrowprops=dict(arrowstyle="->"), fontsize=9,
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.85)
    )

    # Design point 2: smallest tested PEM size in the 2.0-2.5 MW benchmark
    # range that reaches >=99% recovery. If neither does, show the better one.
    candidates = [v for v in full_curtailment_benchmark_range_mw if v in x]
    candidate_pairs = [(v, float(y[np.where(x == v)[0][0]])) for v in candidates]
    qualifying = [(v, r) for v, r in candidate_pairs if r >= 99.0]
    benchmark_mw, benchmark_recovery = (qualifying[0] if qualifying else max(candidate_pairs, key=lambda z: z[1]))
    ax.scatter([benchmark_mw], [benchmark_recovery], s=90, zorder=5)
    ax.annotate(
        f"High-recovery diagnostic\n{benchmark_mw:.1f} MW, {benchmark_recovery:.1f}% recovery",
        xy=(benchmark_mw, benchmark_recovery), xytext=(benchmark_mw + 0.85, max(benchmark_recovery - 22, 5)),
        arrowprops=dict(arrowstyle="->"), fontsize=9,
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.85)
    )

    ax.axhline(95, linestyle="--", linewidth=1.0, alpha=0.65, label="95% recovery")
    ax.axhline(99, linestyle=":", linewidth=1.0, alpha=0.65, label="99% recovery")
    ax.set_xlabel("PEM Size (MW)")
    ax.set_ylabel("Curtailment Recovery (%)")
    ax.set_ylim(0, 102)
    ax.set_title(fig_title)
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    save_figure_png(fig, 
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_curtailment_recovery_vs_pem_size.png"),
        dpi=300, bbox_inches="tight"
    )
    plt.show()

###
def plot_curtailment_diagnostics(curtailment_diagnostics):
    """Plot avoided and residual PV curtailment under non-hybrid dispatch."""
    fig_number, fig_title = next_fig("Curtailment Avoided and Residual vs PEM Size")
    plt.figure(figsize=(10, 6))

    plt.plot(
        curtailment_diagnostics["PEM Size (MW)"],
        curtailment_diagnostics["Avoided Curtailment (MWh)"],
        marker="o",
        label="Avoided curtailment"
    )
    plt.plot(
        curtailment_diagnostics["PEM Size (MW)"],
        curtailment_diagnostics["Residual Curtailment (MWh)"],
        marker="s",
        label="Residual curtailment"
    )

    plt.xlabel("PEM Size (MW)")
    plt.ylabel("PV Energy (MWh/year)")
    plt.title(fig_title)
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    save_figure_png(plt.gcf(), 
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_curtailment_avoided_residual.png"),
        dpi=300, bbox_inches="tight"
    )
    plt.show()


###
def plot_capex_intensity(results_table):
    fig_number, fig_title = next_fig("PEM Size vs One-Year CAPEX Intensity")
    plt.figure(figsize=(8, 5))
    for col in results_table.columns[1:]:
        plt.plot(results_table["PEM Size (MW)"], results_table[col], marker="o", label=col)
    plt.title(fig_title)
    plt.xlabel("PEM Size (MW)")
    plt.ylabel("One-Year CAPEX Intensity (€/kg H2)")
    plt.legend()
    plt.grid()
    save_figure_png(plt.gcf(), os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_capex_intensity.png"), dpi=300, bbox_inches="tight")
    plt.show()

def plot_monthly_hydrogen(monthly_h2_kg):
    fig_number, fig_title = next_fig("Monthly Hydrogen Production")
    plt.figure(figsize=(10, 5))
    plt.plot(monthly_h2_kg.index, monthly_h2_kg.values, marker="o")
    plt.title(fig_title)
    plt.xlabel("Month")
    plt.ylabel("Hydrogen Production (kg)")
    plt.grid()
    save_figure_png(plt.gcf(), os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_monthly_hydrogen.png"), dpi=300, bbox_inches="tight")
    plt.show()

def plot_lcoh_vs_grid_limit(grid_limits_mw, lcoh_grid_sensitivity, discounted_lcoh_grid_sensitivity=None):
    fig_number, fig_title = next_fig("LCOH vs Grid Export Limit")
    plt.figure(figsize=(8, 5))
    plt.plot(grid_limits_mw, lcoh_grid_sensitivity, marker="o", label="Simple (undiscounted) LCOH")
    if discounted_lcoh_grid_sensitivity is not None:
        plt.plot(grid_limits_mw, discounted_lcoh_grid_sensitivity, marker="s", label="Simplified annualized LCOH")
    plt.title(fig_title)
    plt.xlabel("Grid Limit (MW)")
    plt.ylabel("LCOH (€/kg H2)")
    plt.legend()
    plt.grid()
    plt.gca().invert_xaxis()
    plt.yscale("log")
    save_figure_png(plt.gcf(), os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_lcoh_vs_grid_limit.png"), dpi=300, bbox_inches="tight")
    plt.show()

def plot_npv_vs_hydrogen_price(hydrogen_price_scenarios, npv_results):
    fig_number, fig_title = next_fig("NPV vs Hydrogen Sale Price")
    npv_millions = [v / 1_000_000 for v in npv_results] #alter y-axis magnitude
    plt.figure(figsize=(8, 5))
    plt.plot(hydrogen_price_scenarios, npv_millions, marker="o") #alter y-axis magnitude
    plt.axhline(y=0, linestyle="--")
    plt.title(fig_title)
    plt.xlabel("Hydrogen Price (€/kg)")
    plt.ylabel("NPV (M€)")
    plt.grid()
    save_figure_png(plt.gcf(), os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_npv_vs_h2_price.png"), dpi=300, bbox_inches="tight")
    plt.show()

def plot_npv_vs_grid_limit(grid_limits_mw, npv_grid_results):
    fig_number, fig_title = next_fig("NPV vs Grid Export Limit")
    plt.figure(figsize=(8, 5))
    plt.plot(grid_limits_mw, npv_grid_results, marker="o")
    plt.axhline(y=0, linestyle="--")
    plt.gca().invert_xaxis()
    plt.title(fig_title)
    plt.xlabel("Grid Limit (MW)")
    plt.ylabel("NPV (€)")
    plt.grid()
    save_figure_png(plt.gcf(), os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_npv_vs_grid_limit.png"), dpi=300, bbox_inches="tight")
    plt.show()

def plot_npv_heatmap(npv_df):
    fig_number, fig_title = next_fig("NPV Heatmap")
    plt.figure(figsize=(10, 6))
    plt.imshow(npv_df, aspect="auto")
    plt.colorbar(label="NPV (€)")
    plt.xticks(range(len(npv_df.columns)), npv_df.columns)
    plt.yticks(range(len(npv_df.index)), npv_df.index)
    plt.xlabel("Hydrogen Price (€/kg)")
    plt.ylabel("Grid Limit (MW)")
    plt.title(fig_title)
    save_figure_png(plt.gcf(), os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_npv_heatmap.png"), dpi=300, bbox_inches="tight")
    plt.show()
#
def plot_minimum_load_sensitivity(pem_sizes_mw, sensitivity_results):
    """
    Plots curtailment recovery (%) vs PEM size, one line per tested
    minimum-load fraction, so the base-case 15% assumption can be
    compared against alternative electrolyzer minimum-load specs.
    """
    fig_number, fig_title = next_fig("Minimum-Load Sensitivity vs PEM Size")
    plt.figure(figsize=(12, 7))

    for min_load_fraction, recovery_values in sensitivity_results.items():
        plt.plot(
            pem_sizes_mw,
            recovery_values,
            marker="o",
            label=f"Minimum load = {min_load_fraction * 100:.0f}%"
        )

    plt.xlabel("PEM Size (MW)")
    plt.ylabel("Curtailment Recovery (%)")
    plt.title(fig_title)
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    save_figure_png(plt.gcf(), 
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_minimum_load_sensitivity.png"),
        dpi=300,
        bbox_inches="tight"
    )
    plt.show()
#

# =========================================
# v1.3 HYBRID OPERATING STRATEGY PLOTS
# =========================================

def plot_h2_vs_baseline_load(hybrid_summary):
    """
    H2 production vs baseline load, one point per baseline load fraction.
    Independent of electricity price, so uses the price=0 rows (any
    single price would give the same H2 values).
    """
    fig_number, fig_title = next_fig("Hydrogen Production vs Baseline Load")
    subset = hybrid_summary[
        hybrid_summary["Electricity Price (€/MWh)"] == hybrid_summary["Electricity Price (€/MWh)"].iloc[0]
    ].sort_values("Baseline Load (%)")

    plt.figure(figsize=(9, 6))
    plt.plot(subset["Baseline Load (%)"], subset["H2 (kg/year)"], marker="o")
    plt.xlabel("Baseline Load (% of PEM capacity)")
    plt.ylabel("Hydrogen Production (kg/year)")
    plt.title(fig_title)
    plt.grid(True)
    plt.tight_layout()
    save_figure_png(plt.gcf(), 
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_h2_vs_baseline_load.png"),
        dpi=300,
        bbox_inches="tight"
    )
    plt.show()


def plot_utilization_vs_baseline_load(hybrid_summary):
    fig_number, fig_title = next_fig("PEM Utilization vs Baseline Load")
    subset = hybrid_summary[
        hybrid_summary["Electricity Price (€/MWh)"] == hybrid_summary["Electricity Price (€/MWh)"].iloc[0]
    ].sort_values("Baseline Load (%)")

    plt.figure(figsize=(9, 6))
    plt.plot(subset["Baseline Load (%)"], subset["Utilization (%)"], marker="o")
    plt.xlabel("Baseline Load (% of PEM capacity)")
    plt.ylabel("Utilization (%)")
    plt.title(fig_title)
    plt.grid(True)
    plt.tight_layout()
    save_figure_png(plt.gcf(), 
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_utilization_vs_baseline_load.png"),
        dpi=300,
        bbox_inches="tight"
    )
    plt.show()


def plot_purchased_electricity_vs_baseline_load(hybrid_summary):
    fig_number, fig_title = next_fig("Purchased Electricity vs Baseline Load")
    subset = hybrid_summary[
        hybrid_summary["Electricity Price (€/MWh)"] == hybrid_summary["Electricity Price (€/MWh)"].iloc[0]
    ].sort_values("Baseline Load (%)")

    plt.figure(figsize=(9, 6))
    plt.plot(subset["Baseline Load (%)"], subset["Purchased Energy (MWh)"], marker="o", label="Purchased")
    plt.plot(subset["Baseline Load (%)"], subset["PV Energy to PEM (MWh)"], marker="s", label="PV to PEM")
    plt.xlabel("Baseline Load (% of PEM capacity)")
    plt.ylabel("Annual Electricity (MWh)")
    plt.title(fig_title)
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    save_figure_png(plt.gcf(), 
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_purchased_electricity_vs_baseline_load.png"),
        dpi=300,
        bbox_inches="tight"
    )
    plt.show()


def plot_lcoh_vs_baseline_load_multi_price(hybrid_summary, reference_lcoh=None):
    """
    LCOH vs baseline load, one line per tested electricity price.
    reference_lcoh, if given, is drawn as a horizontal dashed line
    marking the non-hybrid base-case LCOH, so the hybrid strategy can
    be visually checked against the v1.2 baseline.
    """
    is_dam_cap = "All-in Import Price Cap (€/MWh)" in hybrid_summary.columns
    qualifier = "All-in Import Price Cap" if is_dam_cap else "Electricity Price"
    fig_number, fig_title = next_fig(
        f"LCOH vs Grid-Supported Baseline Load (by {qualifier})"
    )
    fig = plt.figure(figsize=(12, 7.6))

    for price in sorted(hybrid_summary["Electricity Price (€/MWh)"].unique()):
        subset = hybrid_summary[
            hybrid_summary["Electricity Price (€/MWh)"] == price
        ].sort_values("Baseline Load (%)")
        plt.plot(
            subset["Baseline Load (%)"],
            subset["LCOH (€/kg H2)"],
            marker="o",
            label=f"{price:g} €/MWh cap" if is_dam_cap else f"{price:g} €/MWh"
        )

    reference_handle = None
    if reference_lcoh is not None:
        # Legend-only comparator. Do not draw the distant reference value on
        # the axes because it compresses the Strategy B sensitivity curves.
        reference_handle = Line2D(
            [0], [0], linestyle="--", color="black",
            label=f"non-hybrid base case ({reference_lcoh:.2f} €/kg)"
        )

    hybrid_baseline_ticks = sorted(hybrid_summary["Baseline Load (%)"].unique())
    hybrid_baseline_labels = [
        f"{v:g}"
        for v in hybrid_baseline_ticks
    ]
    plt.xticks(hybrid_baseline_ticks, hybrid_baseline_labels)
    plt.xlabel("Grid-Supported Baseline Load (% of PEM capacity)")
    plt.ylabel("Discounted LCOH (€/kg H2)")
    plt.title(fig_title)
    plt.grid(True)

    # The electricity-price sensitivity contains many series. A conventional
    # Keep the legend below the axes, but close enough to read together with
    # the graph. Three columns prevent the price-cap labels becoming a tall,
    # detached block.
    handles, labels = plt.gca().get_legend_handles_labels()
    if reference_handle is not None:
        handles.append(reference_handle)
        labels.append(reference_handle.get_label())
    plt.gca().legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.16),
        ncol=3,
        fontsize=10,
        frameon=True,
        columnspacing=1.8,
        handlelength=2.5,
        handletextpad=0.7,
        borderpad=0.8,
        labelspacing=0.65
    )
    fig.subplots_adjust(bottom=0.27)
    save_figure_png(plt.gcf(), 
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_lcoh_vs_baseline_load.png"),
        dpi=300,
        bbox_inches="tight"
    )
    plt.show()


def plot_npv_vs_baseline_load_multi_price(hybrid_summary, reference_npv=None):
    is_dam_cap = "All-in Import Price Cap (€/MWh)" in hybrid_summary.columns
    qualifier = "All-in Import Price Cap" if is_dam_cap else "Electricity Price"
    fig_number, fig_title = next_fig(
        f"NPV vs Grid-Supported Baseline Load (by {qualifier})"
    )
    fig = plt.figure(figsize=(12, 7.6))

    for price in sorted(hybrid_summary["Electricity Price (€/MWh)"].unique()):
        subset = hybrid_summary[
            hybrid_summary["Electricity Price (€/MWh)"] == price
        ].sort_values("Baseline Load (%)")
        plt.plot(
            subset["Baseline Load (%)"],
            subset["NPV (€)"] / 1_000_000,
            marker="o",
            label=f"{price:g} €/MWh cap" if is_dam_cap else f"{price:g} €/MWh"
        )

    # All tested Strategy B NPVs are negative. Omitting the distant zero line
    # keeps the sensitivity differences readable without changing the data.

    reference_handle = None
    if reference_npv is not None:
        reference_handle = Line2D(
            [0], [0], linestyle="--", color="black",
            label=f"non-hybrid base case ({reference_npv/1_000_000:.2f} M€)"
        )

    hybrid_baseline_ticks = sorted(hybrid_summary["Baseline Load (%)"].unique())
    hybrid_baseline_labels = [
        f"{v:g}"
        for v in hybrid_baseline_ticks
    ]
    plt.xticks(hybrid_baseline_ticks, hybrid_baseline_labels)
    plt.xlabel("Grid-Supported Baseline Load (% of PEM capacity)")
    plt.ylabel("NPV (M€)")
    plt.title(fig_title)
    plt.grid(True)

    # Keep the large sensitivity legend outside the data region and arrange it
    # in three columns at the bottom of the figure.
    handles, labels = plt.gca().get_legend_handles_labels()
    if reference_handle is not None:
        handles.append(reference_handle)
        labels.append(reference_handle.get_label())
    plt.gca().legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.16),
        ncol=3,
        fontsize=10,
        frameon=True,
        columnspacing=1.8,
        handlelength=2.5,
        handletextpad=0.7,
        borderpad=0.8,
        labelspacing=0.65
    )
    fig.subplots_adjust(bottom=0.27)
    save_figure_png(plt.gcf(), 
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_npv_vs_baseline_load.png"),
        dpi=300,
        bbox_inches="tight"
    )
    plt.show()


def plot_hybrid_strategy_heatmap(hybrid_summary, baseline_load_fractions, electricity_prices_eur_per_mwh, reference_lcoh=None):
    """
    Heatmap of discounted LCOH (€/kg H2) across baseline load and electricity price.
    Every cell is labelled numerically. The minimum-LCOH cell in each
    electricity-price column is marked with a star and a rectangular outline.
    """
    is_dam_cap = "All-in Import Price Cap (€/MWh)" in hybrid_summary.columns
    fig_number, fig_title = next_fig(
        "Market-Linked Strategy B LCOH Heatmap" if is_dam_cap
        else "Hybrid Strategy LCOH Heatmap"
    )

    pivot = hybrid_summary.pivot(
        index="Baseline Load (%)",
        columns="Electricity Price (€/MWh)",
        values="LCOH (€/kg H2)"
    ).reindex(
        index=[b * 100 for b in baseline_load_fractions],
        columns=electricity_prices_eur_per_mwh
    )

    fig, ax = plt.subplots(figsize=(13, 7))
    # Intuitive economic scale, green is lower LCOH, red is higher LCOH.
    image = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn_r")
    fig.colorbar(image, ax=ax, label="Discounted LCOH (€/kg H2)")

    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([f"{v:g}" for v in pivot.columns])
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([
        f"{v:.0f}"
        for v in pivot.index
    ])
    ax.set_xlabel(
        "Grid-Import Dispatch Threshold: DAM + Variable Adder (€/MWh)" if is_dam_cap
        else "Electricity Price (€/MWh)"
    )
    ax.set_ylabel("Grid-Supported Baseline Load (%)")

    # Numeric value in every cell
    for row_i in range(pivot.shape[0]):
        for col_i in range(pivot.shape[1]):
            value = pivot.iloc[row_i, col_i]
            if np.isfinite(value):
                ax.text(col_i, row_i, f"{value:.2f}", ha="center", va="center", fontsize=8)

    # Mark the minimum LCOH in every electricity-price column
    for col_i, column in enumerate(pivot.columns):
        column_values = pivot[column]
        if column_values.notna().any():
            min_baseline = column_values.idxmin()
            row_i = pivot.index.get_loc(min_baseline)
            ax.text(
                col_i, row_i - 0.28, "★",
                ha="center", va="center", fontsize=11, color="black"
            )

    title = fig_title
    if reference_lcoh is not None:
        title += f"\n(non-hybrid base case reference: {reference_lcoh:.2f} €/kg H2)"
    ax.set_title(title)

    plt.tight_layout()
    save_figure_png(plt.gcf(), 
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_strategy_b_heatmap.png"),
        dpi=300,
        bbox_inches="tight"
    )
    plt.show()


def plot_strategy_c_bess_sensitivity(physical_results, economic_results):
    """Plot the narrow Strategy C duration diagnostic without dual y-axes."""
    fig_number, fig_title = next_fig(
        "Strategy C Residual-Curtailment BESS Duration Sensitivity"
    )
    physical = physical_results[
        physical_results["Configuration type"] == "268 kW duration sensitivity"
    ].sort_values("Nominal duration (h)")
    economic = economic_results[
        (economic_results["Configuration type"] == "268 kW duration sensitivity")
        & np.isclose(
            economic_results["CAPEX scale factor"],
            BESS_CENTRAL_SMALL_PROJECT_MULTIPLIER,
        )
        & np.isclose(
            economic_results[
                "Residual-curtailment opportunity cost (EUR/MWh)"
            ],
            BESS_CENTRAL_CURTAILMENT_OPPORTUNITY_COST_EUR_PER_MWH,
        )
    ].sort_values("Nominal duration (h)")

    fig, axes = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    axes[0].plot(
        physical["Nominal duration (h)"],
        physical["Incremental H2 vs Strategy B (kg/year)"],
        marker="o",
        color="tab:blue",
        linewidth=2.2,
    )
    axes[0].set_ylabel("Incremental H2 (kg/year)")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(
        economic["Nominal duration (h)"],
        economic["Incremental NPV vs Strategy B (EUR)"] / 1_000_000.0,
        marker="o",
        color="tab:red",
        linewidth=2.2,
        label="Central screening case",
    )
    axes[1].axhline(0.0, color="black", linestyle="--", linewidth=1.2)
    best_row = economic.loc[
        economic["Incremental NPV vs Strategy B (EUR)"].idxmax()
    ]
    axes[1].scatter(
        [best_row["Nominal duration (h)"]],
        [best_row["Incremental NPV vs Strategy B (EUR)"] / 1_000_000.0],
        marker="*", s=180, color="gold", edgecolor="black",
        label="Best tested non-zero BESS case",
        zorder=5,
    )
    axes[1].set_xlabel("Nominal BESS duration at 268 kW (h)")
    axes[1].set_ylabel("Incremental NPV vs Strategy B (MEUR)")
    axes[1].grid(True, alpha=0.3)
    axes[1].legend(loc="best")
    fig.suptitle(
        f"{fig_title}\n"
        "Central case: 1.25 small-project CAPEX factor, zero value for residual curtailment"
    )
    fig.tight_layout()
    save_figure_png(fig, 
        os.path.join(
            FIGURES_DIR,
            f"figure{fig_number:02d}_strategy_c_bess_duration_sensitivity.png",
        ),
        dpi=300,
        bbox_inches="tight",
    )
    plt.show()


def resolve_dispatch_plot_date(df, grid_limit_mw, requested_date="AUTO"):
    """Return requested date or automatically choose the day with maximum gross no-PEM curtailment."""
    if str(requested_date).upper() != "AUTO":
        return str(requested_date)

    temp = df[["time", "P"]].copy()
    temp["date"] = temp["time"].dt.strftime("%Y-%m-%d")
    temp["gross_curtailment_w"] = np.maximum(
        temp["P"].to_numpy(dtype=float) - grid_limit_mw * 1_000_000, 0.0
    )
    daily = temp.groupby("date")["gross_curtailment_w"].sum()
    if daily.empty:
        raise ValueError("Cannot determine an extreme-curtailment day from the PV dataset.")
    return str(daily.idxmax())


def plot_daily_dispatch(df, selected_pem_mw, selected_grid_limit_mw, date_string,
                        hybrid_baseline_fraction=0.20, strategy="hybrid",
                        dam_dispatch_df=None, dam_operation=None,
                        dispatch_case_note=None):
    """
    Representative-day dispatch plot.

    Visual convention requested for Figures 17 and 18:
      - PV generation: thick ORANGE line.
      - In both figures, the orange PV line is interrupted only when residual
        curtailment is actually executed after PEM absorption.
      - During those intervals it is replaced by a RED dotted segment, with
        no orange line underneath.
      - Grid electricity supplied to PEM: thick GREY line.
      - Hydrogen energy output: thick BLUE line.
      - PEM electricity use is shown as filled areas from the x-axis:
            orange = PV supplied to PEM,
            grey   = grid electricity supplied to PEM.
      - PEM rated capacity and requested baseline are retained on the PEM/H2 axis.

    Figures 17 and 18 place PV, PEM electrical input and H2 energy-equivalent
    output on one common MW axis.
    """
    use_dam_dispatch = (
        strategy == "hybrid" and dam_dispatch_df is not None
        and dam_operation is not None
    )
    source_df = dam_dispatch_df if use_dam_dispatch else df
    time_column = "Timestamp" if use_dam_dispatch else "time"
    day_mask = source_df[time_column].dt.strftime("%Y-%m-%d") == date_string
    day_df = source_df.loc[day_mask].copy()
    if day_df.empty:
        raise ValueError(f"No PV records found for {date_string}.")

    if use_dam_dispatch:
        positions = np.flatnonzero(day_mask.to_numpy())
        op = {
            key: np.asarray(dam_operation[key])[positions]
            for key in (
                "pv_to_pem_w", "purchased_w", "residual_curtailment_w",
                "pem_power_w", "hourly_h2_kg"
            )
        }
        op["gross_curtailment_without_pem_w"] = np.maximum(
            day_df["P"].to_numpy(dtype=float)
            - selected_grid_limit_mw * 1_000_000.0,
            0.0,
        )
        baseline_w = hybrid_baseline_fraction * selected_pem_mw * 1_000_000
        title_suffix = "Strategy B: PV-Priority + DAM-Conditional Grid Baseline"
        if dispatch_case_note:
            title_suffix += f" ({dispatch_case_note})"
        pv_to_pem_w = op["pv_to_pem_w"]
        grid_to_pem_w = op["purchased_w"]
        gross_curtailment_w = op["gross_curtailment_without_pem_w"]
        residual_curtailment_w = op["residual_curtailment_w"]
        pem_power_w = op["pem_power_w"]
        interval_hours = DAM_INTERVAL_HOURS

    elif strategy == "hybrid":
        op = simulate_hybrid_operation(
            day_df,
            selected_pem_mw,
            hybrid_baseline_fraction,
            selected_grid_limit_mw
        )
        baseline_w = hybrid_baseline_fraction * selected_pem_mw * 1_000_000
        title_suffix = "Strategy B: Curtailed PV + Grid Baseline"
        pv_to_pem_w = op["pv_to_pem_w"]
        grid_to_pem_w = op["purchased_w"]
        gross_curtailment_w = op["gross_curtailment_without_pem_w"]
        residual_curtailment_w = op["residual_curtailment_w"]
        pem_power_w = op["pem_power_w"]
        interval_hours = 1.0

    elif strategy == "nonhybrid":
        op = simulate_nonhybrid_operation(
            day_df,
            selected_pem_mw,
            selected_grid_limit_mw
        )
        baseline_w = 0.0
        title_suffix = "Strategy A: Curtailment-Only"
        pv_to_pem_w = op["pv_to_pem_w"]
        grid_to_pem_w = np.zeros_like(pv_to_pem_w)
        gross_curtailment_w = op["potential_curtailment_w"]
        residual_curtailment_w = op.get(
            "residual_curtailment_w",
            np.maximum(
                day_df["P"].to_numpy(dtype=float)
                - pv_to_pem_w
                - selected_grid_limit_mw * 1_000_000,
                0.0
            )
        )
        pem_power_w = op["pem_power_w"]
        interval_hours = 1.0

    else:
        raise ValueError("strategy must be 'hybrid' or 'nonhybrid'.")

    timestamps = day_df[time_column]
    hours = (
        timestamps.dt.hour.to_numpy(dtype=float)
        + timestamps.dt.minute.to_numpy(dtype=float) / 60.0
    )
    pv_mw = day_df["P"].to_numpy(dtype=float) / 1_000_000
    pv_to_pem_mw = np.asarray(pv_to_pem_w, dtype=float) / 1_000_000
    grid_to_pem_mw = np.asarray(grid_to_pem_w, dtype=float) / 1_000_000
    pem_total_mw = np.asarray(pem_power_w, dtype=float) / 1_000_000
    gross_curtailment_mw = np.asarray(gross_curtailment_w, dtype=float) / 1_000_000
    residual_curtailment_mw = np.asarray(residual_curtailment_w, dtype=float) / 1_000_000

    # Hourly H2 mass (kg/h for a 1-hour timestep) -> average chemical power (MW, LHV).
    h2_mass_per_interval = np.asarray(op["hourly_h2_kg"], dtype=float)
    if use_dam_dispatch:
        h2_mass_per_interval = h2_mass_per_interval / DAM_ANNUALIZATION_FACTOR
    h2_lhv_mw = (
        h2_mass_per_interval / interval_hours
        * h2_lower_heating_value_kwh_per_kg
        / 1000.0
    )

    def curve_with_vertical_zero_transitions(x_values, y_values, step_hours):
        """
        Return plotting arrays containing only positive operating segments.

        Each segment starts with a vertical rise from zero and ends with a
        vertical fall to zero. NaN separators suppress the horizontal zero
        line between operating periods. The source data remain unchanged.
        """
        x_values = np.asarray(x_values, dtype=float)
        y_values = np.asarray(y_values, dtype=float)
        positive = np.isfinite(y_values) & (y_values > 1e-9)

        if not positive.any():
            return np.array([np.nan]), np.array([np.nan])

        padded = np.concatenate(([False], positive, [False]))
        starts = np.flatnonzero((~padded[:-1]) & padded[1:])
        ends = np.flatnonzero(padded[:-1] & (~padded[1:])) - 1

        plot_x = []
        plot_y = []
        for run_number, (start_i, end_i) in enumerate(zip(starts, ends)):
            if run_number > 0:
                plot_x.append(np.nan)
                plot_y.append(np.nan)

            # Duplicate the first x-coordinate to create a vertical start.
            plot_x.extend([x_values[start_i], x_values[start_i]])
            plot_y.extend([0.0, y_values[start_i]])

            for point_i in range(start_i + 1, end_i + 1):
                plot_x.append(x_values[point_i])
                plot_y.append(y_values[point_i])

            # Hold the final interval value to its right boundary, then drop
            # vertically to zero at that same boundary.
            end_x = (
                x_values[end_i + 1]
                if end_i + 1 < len(x_values)
                else x_values[end_i] + step_hours
            )
            plot_x.extend([end_x, end_x])
            plot_y.extend([y_values[end_i], 0.0])

        return np.asarray(plot_x), np.asarray(plot_y)

    grid_line_hours, grid_line_display_mw = curve_with_vertical_zero_transitions(
        hours, grid_to_pem_mw, interval_hours
    )
    pv_input_line_hours, pv_input_display_mw = curve_with_vertical_zero_transitions(
        hours, pv_to_pem_mw, interval_hours
    )
    pem_line_hours, pem_line_display_mw = curve_with_vertical_zero_transitions(
        hours, pem_total_mw, interval_hours
    )
    h2_line_hours, h2_line_display_mw = curve_with_vertical_zero_transitions(
        hours, h2_lhv_mw, interval_hours
    )

    # The dispatch calculation correctly treats each source PV value as an
    # hourly-average value held across its two market intervals. For Figure 18
    # only, linearly interpolate between the :00 hourly anchors so the plotted
    # PV trace does not imply artificial physical steps. This display-only
    # curve does not alter PEM dispatch, H2 production, energy or economics.
    pv_display_mw = pv_mw.copy()
    pv_display_correction_note = None
    if use_dam_dispatch:
        hourly_anchor_mask = timestamps.dt.minute.to_numpy() == 0
        anchor_hours = hours[hourly_anchor_mask]
        anchor_power = pv_mw[hourly_anchor_mask].copy()
        # Explicit user-requested display correction for the same illustrative
        # day. Keep the original time-series and dispatch calculations intact.
        # Replace only the marked noon dip using its immediate hourly neighbours.
        if date_string == "2025-10-20":
            noon_indices = np.flatnonzero(np.isclose(anchor_hours, 12.0))
            if len(noon_indices) == 1:
                i = int(noon_indices[0])
                if 0 < i < len(anchor_power) - 1 and anchor_power[i] < min(
                    anchor_power[i - 1], anchor_power[i + 1]
                ):
                    original_noon_mw = float(anchor_power[i])
                    anchor_power[i] = np.interp(
                        anchor_hours[i],
                        [anchor_hours[i - 1], anchor_hours[i + 1]],
                        [anchor_power[i - 1], anchor_power[i + 1]],
                    )
                    pv_display_correction_note = (
                        "PV display correction: noon dip interpolated from 11:00 and 13:00; "
                        "calculations retain original input."
                    )
                    print(
                        f"Figure 18 PV display correction [{date_string}, 12:00]: "
                        f"{original_noon_mw:.3f} -> {anchor_power[i]:.3f} MW. "
                        "Original PV input and dispatch unchanged."
                    )
        if len(anchor_hours) >= 2:
            pv_display_mw = np.interp(hours, anchor_hours, anchor_power)

    # ACTUAL curtailment means PV still rejected after the PEM has absorbed
    # all power permitted by its dispatch and capacity constraints. Do not use
    # gross_curtailment_mw here, because that is the counterfactual curtailment
    # that would have occurred without PEM absorption.
    actual_curtailment_mask = residual_curtailment_mw > 1e-9
    gross_curtailment_mask = gross_curtailment_mw > 1e-9

    # Add one adjacent sample at every transition only to join the orange and
    # red segments visually. The logical curtailment test remains strictly the
    # residual-curtailment mask above.
    curtailment_display_mask = actual_curtailment_mask.copy()
    if actual_curtailment_mask.any():
        curtailment_display_mask[1:] |= actual_curtailment_mask[:-1]
        curtailment_display_mask[:-1] |= actual_curtailment_mask[1:]

    curtailed_pv_overlay_mw = np.where(
        curtailment_display_mask, pv_display_mw, np.nan
    )

    # Counterfactual no-PEM overlay. Plot it at the PV-curve elevation rather
    # than near zero so its duration can be compared directly with the red
    # residual-curtailment segment. This curve marks WHEN gross curtailment
    # would occur, not the curtailed-power magnitude.
    gross_display_mask = gross_curtailment_mask.copy()
    if gross_curtailment_mask.any():
        gross_display_mask[1:] |= gross_curtailment_mask[:-1]
        gross_display_mask[:-1] |= gross_curtailment_mask[1:]
    gross_curtailed_pv_overlay_mw = np.where(
        gross_display_mask, pv_display_mw, np.nan
    )

    # Orange outside actual curtailment, red dotted during actual curtailment.
    # No continuous orange curve remains underneath the red segment.
    pv_line_display_mw = np.where(
        ~actual_curtailment_mask,
        pv_display_mw,
        np.nan
    )

    fig_number, fig_title = next_fig(f"{title_suffix} on {date_string}")
    fig, ax_pv = plt.subplots(figsize=(12, 6))
    # All plotted quantities are expressed in MW.
    # Use one common y-axis for both Figures 17 and 18.
    ax_h2 = ax_pv
    common_dispatch_axis = True

    # ---------------------------------------------------------
    # LEFT AXIS, PV-side quantities
    # ---------------------------------------------------------
    pv_line, = ax_pv.plot(
        hours,
        pv_line_display_mw,
        color="tab:orange",
        linewidth=3.0,
        label="PV generation (MW)",
        zorder=5
    )

    curtailment_line, = ax_pv.plot(
        hours,
        curtailed_pv_overlay_mw,
        color="tab:red",
        linestyle=":",
        linewidth=3.5,
        label="PV generation during actual curtailment",
        zorder=7
    )

    # Counterfactual no-PEM curtailment period, drawn along the PV curve for a
    # direct duration comparison with the red actual-curtailment segment.
    gross_curtailment_line, = ax_pv.plot(
        hours,
        gross_curtailed_pv_overlay_mw,
        color="0.45",
        linestyle="--",
        linewidth=2.8,
        label="PV generation during curtailment without PEM",
        zorder=6,
    )

    # ---------------------------------------------------------
    # LEFT AXIS, electrical power quantities. Plotting PV generation and PEM
    # electrical input on the same MW axis prevents a visually misleading
    # comparison caused by two independently scaled y-axes.
    # ---------------------------------------------------------
    # Filled PEM-use areas. PV is the first layer from the x-axis. Grid top-up,
    # when present, is stacked immediately above it so total filled height equals
    # total PEM electrical input.
    pv_fill = ax_pv.fill_between(
        pv_input_line_hours,
        0,
        pv_input_display_mw,
        color="tab:orange",
        alpha=0.28,
        label="PEM input from PV (display-interpolated)",
        zorder=1
    )

    grid_fill = ax_pv.fill_between(
        hours,
        pv_to_pem_mw,
        pv_to_pem_mw + grid_to_pem_mw,
        where=grid_to_pem_mw > 1e-12,
        step="post",
        interpolate=False,
        color="0.55",
        alpha=0.38,
        label="PEM input from grid",
        zorder=2
    )

    # Thick grey grid line, exactly the purchased-grid contribution to PEM.
    grid_line, = ax_pv.plot(
        grid_line_hours,
        grid_line_display_mw,
        color="0.35",
        linewidth=3.2,
        label="Grid power to PEM (MW)",
        zorder=8
    )

    # Total PEM input is kept as a thin neutral boundary, not a dominant curve.
    pem_boundary, = ax_pv.plot(
        pem_line_hours,
        pem_line_display_mw,
        color="0.20",
        linewidth=1.2,
        alpha=0.75,
        label="Total PEM electrical input (MW, display-interpolated)",
        zorder=6
    )

    # Hydrogen chemical-energy output. Figure 18 uses the same MW axis as PV
    # and PEM input, eliminating the visual distortion from independent scales.
    h2_line, = ax_h2.plot(
        h2_line_hours,
        h2_line_display_mw,
        color="tab:blue",
        linewidth=3.2,
        label="H2 energy output (MW, LHV)",
        zorder=9
    )

    rated_line = ax_pv.axhline(
        selected_pem_mw,
        color="0.25",
        linestyle="--",
        linewidth=1.5,
        label=f"PEM rated capacity ({selected_pem_mw:.2f} MW)",
        zorder=4
    )

    baseline_line = ax_pv.axhline(
        baseline_w / 1_000_000,
        color="0.40",
        linestyle=":",
        linewidth=1.5,
        label=f"Requested baseline ({baseline_w/1_000_000:.2f} MW)",
        zorder=4
    )

    # ---------------------------------------------------------
    # AXES / TITLES / LIMITS
    # ---------------------------------------------------------
    ax_pv.set_xlim(0, 23)
    ax_pv.set_xticks(range(0, 24, 2))
    ax_pv.set_xlabel("Hour of day")
    ax_pv.set_ylabel("Power / H2 Energy-Equivalent Output (MW)")

    ax_pv.set_ylim(bottom=0)
  

    ax_pv.set_title(
        f"{fig_title}\n"
        f"(PEM size = {selected_pem_mw:.2f} MW, "
        f"baseline = {baseline_w/1_000_000:.2f} MW)"
    )
    if pv_display_correction_note:
        fig.text(0.5, 0.005, pv_display_correction_note, ha="center", fontsize=8)

    ax_pv.grid(True, alpha=0.25)

    # One combined legend, ordered by physical meaning.
    handles = [
        pv_line,
        curtailment_line,
        gross_curtailment_line,
        pv_fill,
    ]
    if strategy == "hybrid":
        handles += [grid_fill, grid_line]
    handles += [
        pem_boundary,
        h2_line,
        rated_line,
        baseline_line,
    ]
    labels = [h.get_label() for h in handles]
    ax_pv.legend(
        handles,
        labels,
        loc="upper left",
        bbox_to_anchor=(0.01, 0.99),
        borderaxespad=0.0,
        fontsize=7.8 if strategy == "hybrid" else 8.5,
        ncol=4 if strategy == "hybrid" else 3,
        frameon=True,
        framealpha=0.92,
    )

    # Dispatch diagnostics printed to console. These are useful for checking that
    # the visualized areas correspond to the physical hourly power balance.
    pv_to_pem_day_mwh = float(np.nansum(pv_to_pem_mw) * interval_hours)
    grid_to_pem_day_mwh = float(np.nansum(grid_to_pem_mw) * interval_hours)
    pem_day_mwh = float(np.nansum(pem_total_mw) * interval_hours)
    gross_curt_day_mwh = float(np.nansum(gross_curtailment_mw) * interval_hours)
    residual_curt_day_mwh = float(np.nansum(residual_curtailment_mw) * interval_hours)
    h2_day_kg = float(np.nansum(h2_mass_per_interval))

    assert np.all(pv_to_pem_mw >= -1e-9), "Negative PV-to-PEM power detected."
    assert np.all(grid_to_pem_mw >= -1e-9), "Negative grid-to-PEM power detected."
    assert np.all(pem_total_mw <= selected_pem_mw + 1e-9), "PEM rated capacity exceeded."
    assert np.allclose(
        pem_total_mw,
        pv_to_pem_mw + grid_to_pem_mw,
        atol=1e-7
    ), "PEM input does not equal PV contribution plus grid contribution."

    print(
        f"Figure {fig_number} dispatch check [{strategy}, {date_string}]: "
        f"PV->PEM={pv_to_pem_day_mwh:.2f} MWh, "
        f"Grid->PEM={grid_to_pem_day_mwh:.2f} MWh, "
        f"PEM input={pem_day_mwh:.2f} MWh, "
        f"gross curtailment={gross_curt_day_mwh:.2f} MWh, "
        f"residual curtailment={residual_curt_day_mwh:.2f} MWh, "
        f"H2={h2_day_kg:.1f} kg"
    )

    fig.tight_layout(rect=(0, 0.035 if pv_display_correction_note else 0, 1, 1))
    output_strategy_label = "strategy_b" if strategy == "hybrid" else "strategy_a"
    save_figure_png(fig, 
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_{output_strategy_label}_daily_dispatch.png"),
        dpi=300,
        bbox_inches="tight"
    )
    plt.close(fig)

def calculate_optimal_baseline_by_electricity_price(hybrid_summary):
    """
    For each tested electricity price, identify both:
      1. the baseline load that minimizes discounted LCOH, and
      2. the baseline load that maximizes NPV.

    This is calculated directly from hybrid_summary, so the table and
    the heatmap/curves cannot drift apart because of hard-coded values.
    """
    records = []

    for price in sorted(hybrid_summary["Electricity Price (€/MWh)"].unique()):
        subset = hybrid_summary[
            hybrid_summary["Electricity Price (€/MWh)"] == price
        ].copy()

        lcoh_row = subset.loc[subset["LCOH (€/kg H2)"].idxmin()]
        npv_row = subset.loc[subset["NPV (€)"].idxmax()]

        records.append({
            "Price (€/MWh)": price,
            "LCOH-optimal baseline (%)": lcoh_row["Baseline Load (%)"],
            "Minimum LCOH (€/kg H2)": lcoh_row["LCOH (€/kg H2)"],
            "NPV at LCOH optimum (€)": lcoh_row["NPV (€)"],
            "NPV-optimal baseline (%)": npv_row["Baseline Load (%)"],
            "Maximum NPV (€)": npv_row["NPV (€)"],
            "LCOH at NPV optimum (€/kg H2)": npv_row["LCOH (€/kg H2)"],
        })

    return pd.DataFrame(records)


def build_pv_source_comparison(pvgis_df, pvsyst_df, grid_limit_mw, pem_size_mw):
    """
    Compare PVGIS and PVsyst with the SAME downstream non-hybrid PEM model.

    This is a model-source comparison, not an hour-by-hour weather validation:
    PVGIS is calendar-year 2023 while the PVsyst profile is based on PVGIS TMY 5.3.
    """
    records = []
    for label, source_df in [("PVGIS 2023", pvgis_df), ("PVsyst TMY 5.3", pvsyst_df)]:
        annual_pv_mwh = source_df["P"].sum() / 1e6
        gross_curt_w = np.maximum(source_df["P"].to_numpy(dtype=float) - grid_limit_mw * 1e6, 0.0)
        gross_curt_mwh = gross_curt_w.sum() / 1e6

        op = simulate_nonhybrid_operation(source_df, pem_size_mw, grid_limit_mw)
        recovered_mwh = max(gross_curt_mwh - op["residual_curtailment_w"].sum() / 1e6, 0.0)
        recovery_pct = recovered_mwh / gross_curt_mwh * 100 if gross_curt_mwh > 0 else 0.0

        pem_capex_eur = pem_size_mw * 1000 * pem_capex_per_kw
        annual_fixed_opex_eur = pem_capex_eur * pem_opex_fraction
        opportunity_cost_eur, _ = calculate_pv_opportunity_cost_proxy(
            source_df, op["lost_export_w"]
        )
        lcoh = calculate_discounted_lcoh(
            pem_capex_eur,
            annual_fixed_opex_eur + opportunity_cost_eur,
            op["annual_h2_kg"],
            discount_rate,
            project_lifetime_years,
        )
        annual_revenue_eur = op["annual_h2_kg"] * hydrogen_sale_price
        npv = calculate_npv(
            pem_capex_eur,
            annual_revenue_eur - annual_fixed_opex_eur - opportunity_cost_eur,
            discount_rate,
            project_lifetime_years,
        )

        records.append({
            "PV Source": label,
            "Annual PV Energy (MWh)": annual_pv_mwh,
            "PV Capacity Factor (%)": annual_pv_mwh / (10 * 8760) * 100,
            "Gross Curtailment @ 6 MW (MWh)": gross_curt_mwh,
            "Curtailment Recovery (%)": recovery_pct,
            "H2 (kg/year)": op["annual_h2_kg"],
            "PEM Utilization (%)": op["utilization_pct"],
            "Lost Export (MWh)": op["lost_export_mwh"],
            "PV Opportunity Cost (EUR/year)": opportunity_cost_eur,
            "Discounted LCOH (EUR/kg H2)": lcoh,
            "NPV @ reference H2 price (EUR)": npv,
        })

    return pd.DataFrame(records)


def plot_pv_source_monthly_comparison(pvgis_df, pvsyst_df):
    fig_number, fig_title = next_fig("Monthly PV Energy: PVGIS 2023 vs PVsyst TMY 5.3")
    monthly_pvgis = pvgis_df.groupby("month")["P"].sum() / 1e6
    monthly_pvsyst = pvsyst_df.groupby("month")["P"].sum() / 1e6
    months = np.arange(1, 13)

    plt.figure(figsize=(10, 5.5))
    plt.plot(months, monthly_pvgis.reindex(months), marker="o", label="PVGIS 2023")
    plt.plot(months, monthly_pvsyst.reindex(months), marker="o", label="PVsyst TMY 5.3")
    plt.xlabel("Month")
    plt.ylabel("PV Energy (MWh/month)")
    plt.title(fig_title)
    plt.xticks(months)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    save_figure_png(plt.gcf(), 
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_pvgis_vs_pvsyst_monthly_energy.png"),
        dpi=300, bbox_inches="tight"
    )
    plt.show()


def plot_pv_source_power_duration(pvgis_df, pvsyst_df):
    fig_number, fig_title = next_fig("PV Power Duration: PVGIS 2023 vs PVsyst TMY 5.3")
    pvgis_mw = np.sort(pvgis_df["P"].to_numpy(dtype=float) / 1e6)[::-1]
    pvsyst_mw = np.sort(pvsyst_df["P"].to_numpy(dtype=float) / 1e6)[::-1]
    exceedance = np.arange(1, len(pvgis_mw) + 1) / len(pvgis_mw) * 100

    plt.figure(figsize=(10, 5.5))
    plt.plot(exceedance, pvgis_mw, label="PVGIS 2023")
    plt.plot(exceedance, pvsyst_mw, label="PVsyst TMY 5.3")
    plt.axhline(selected_grid_limit_mw, linestyle="--", linewidth=1.2, label=f"Grid export limit ({selected_grid_limit_mw:.0f} MW)")
    plt.xlabel("Hours exceeded (% of year)")
    plt.ylabel("PV AC Power (MW)")
    plt.title(fig_title)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    save_figure_png(plt.gcf(), 
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_pvgis_vs_pvsyst_power_duration.png"),
        dpi=300, bbox_inches="tight"
    )
    plt.show()


def write_final_screening_report(summary, b_cases, c_case, lifecycle):
    """Write a concise report from the same numeric records as the release CSVs."""
    def markdown_table(columns, rows):
        return "\n".join([
            "| " + " | ".join(columns) + " |",
            "| " + " | ".join("---" for _ in columns) + " |",
            *["| " + " | ".join(str(value) for value in row) + " |" for row in rows],
        ])

    summary_table = markdown_table(
        ["Case", "H2 (kg/year)", "Utilization (%)", "LCOH (EUR/kg)", "NPV (MEUR)"],
        [
            [row["Case"], f"{row['H2 (kg/year)']:,.0f}",
             f"{row['Utilization (%)']:.2f}", f"{row['LCOH (EUR/kg)']:.3f}",
             f"{row['NPV (EUR)'] / 1e6:.3f}"]
            for _, row in summary.iterrows()
        ],
    )
    b_table = markdown_table(
        ["Case", "Baseline (%)", "Cap (EUR/MWh)", "Grid (MWh/year)", "LCOH (EUR/kg)"],
        [
            [row["Case"], f"{row['Baseline Load (%)']:.0f}",
             (f"{row['All-in Import Price Cap (€/MWh)']:.0f}"
              if pd.notna(row["All-in Import Price Cap (€/MWh)"]) else "Not applicable"),
             f"{row['Purchased Energy (MWh)']:.3f}", f"{row['LCOH (€/kg H2)']:.3f}"]
            for _, row in b_cases.iterrows()
        ],
    )
    lifecycle_table = markdown_table(
        ["Strategy", "Rate (microV/h)", "Replacements", "Lifecycle LCOH (EUR/kg)"],
        [
            [row["Strategy"], f"{row['Degradation rate (microV/h)']:.0f}",
             int(row["Replacement count"]), f"{row['Lifecycle LCOH (EUR/kg H2)']:.2f}"]
            for _, row in lifecycle.iterrows()
        ],
    )
    report = f"""# PV-to-Hydrogen: final screening report

Release: {MODEL_VERSION}. Active PV input: {PV_DATA_SOURCE}.

## Methodology and boundary

The study compares a fixed {selected_pem_mw:.2f} MW PEM coupled to a 10 MWp PV
profile and a {selected_grid_limit_mw:.0f} MW export limit. Site coordinates are
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

{summary_table}

All three A/B/C screening NPVs are negative at the assumed offtake price.
The Strategy C row is a non-zero diagnostic, not an investment recommendation.

## Strategy B change and interpretation

{b_table}

The tested-cap minimum is conditional on a 20% baseline and an existing import
service. Identical dispatch at nearby low caps makes its numerical threshold
weak evidence for a unique optimum. The raised 30%/200 EUR/MWh comparator uses
more grid energy without increasing the PEM rating or exceeding physical limits.
It is technically admissible in this model and economically worse than the
conditional economic selection. PV-priority operation without grid service is
also reported with zero import demand/fixed charges. Under these assumptions it
is preferable to retaining an import service for negligible annual imports.

The central variable adder is {grid_variable_adder_eur_per_mwh:.2f} EUR/MWh.
Only the regulated network benchmark changed, from 24.70 to 24.50 EUR/MWh,
using CERA 105/2026 as corrected by 117/2026. Supplier margin and imbalance
allowances remain assumptions. This 2026 benchmark is applied across the
observed window as a scenario, not as historical invoicing. Low/central/high
market-cost sensitivities retain separate commercial terms and do not replace
the central case to force grid use.

## Strategy C economic choice

The best central non-zero duration case is {c_case['Power (kW)']:.0f} kW /
{c_case['Energy (kWh)']:.0f} kWh. It adds
{c_case['Incremental H2 vs Strategy B (kg/year)']:.0f} kg H2/year with incremental
NPV {c_case['Incremental NPV vs Strategy B (EUR)']:,.0f} EUR. The CSV records
Overall economic choice = {c_case['Overall economic choice']} and
Value adding = {c_case['Value adding']}. All tested non-zero battery economics
remain screening results and include no capacity-fade or replacement cost.

## Lifecycle sensitivity

{lifecycle_table}

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
"""
    docs_dir = os.path.join(BASE_DIR, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    with open(os.path.join(docs_dir, "FINAL_SCREENING_REPORT.md"), "w", encoding="utf-8") as report_file:
        report_file.write(report)


#
# =========================================
# MAIN EXECUTION
# =========================================

pvgis_df, pvsyst_df = load_pv_sources()
pv_source_frames = {"PVGIS": pvgis_df, "PVSYST": pvsyst_df}
df = pv_source_frames[PV_DATA_SOURCE].copy()

dam_price_path = resolve_dam_price_file()
dam_prices_df = load_cyprus_dam_prices(dam_price_path)
dam_dispatch_df = map_hourly_pv_to_dam_intervals(df, dam_prices_df)
write_reference_tables()

market_cost_assumptions_df = pd.DataFrame(market_cost_screening_scenarios)
market_cost_assumptions_df["Use"] = [
    "Sensitivity lower bound", "Primary screening case", "Sensitivity upper bound"
]
market_cost_assumptions_df.to_csv(
    os.path.join(TABLES_DIR, "strategy_b_market_cost_assumptions.csv"),
    index=False,
)

print("=" * 72)
print("PV DATA SOURCE")
print(f"Active source: {PV_DATA_SOURCE}")
print(
    f"Site: {SITE_LATITUDE_DEG:.3f} deg latitude, "
    f"{SITE_LONGITUDE_DEG:.3f} deg longitude, "
    f"{SITE_ELEVATION_M:.0f} m elevation"
)
print(f"PVGIS radiation database: {PVGIS_RADIATION_DATABASE}")
print("PVGIS source: calendar-year 2023 / PVGIS-SARAH3")
print("PVsyst source: PVGIS TMY 5.3 / detailed PVsyst system simulation")
print("NOTE: PVGIS-vs-PVsyst is a source/model comparison, not same-weather validation.")
if PV_DATA_SOURCE == "PVSYST":
    pvsyst_net_mwh = pvsyst_df["P_raw_w"].sum() / 1e6
    pvsyst_clipped_mwh = pvsyst_df["P_raw_w"].clip(lower=0).sum() / 1e6
    pvsyst_dispatch_mwh = pvsyst_df["P"].sum() / 1e6
    print(f"PVsyst raw net E_Grid: {pvsyst_net_mwh:.3f} MWh/year")
    print(f"PVsyst clipped, before source repairs: {pvsyst_clipped_mwh:.3f} MWh/year")
    print(f"Night-consumption clipping adjustment: {pvsyst_clipped_mwh - pvsyst_net_mwh:.3f} MWh/year")
    print(f"Assumed isolated-dropout repair energy: {pvsyst_dispatch_mwh - pvsyst_clipped_mwh:.3f} MWh/year")
    print(f"PVsyst prepared PV dispatch input: {pvsyst_dispatch_mwh:.3f} MWh/year")
    print("Source repairs are inferred interpolation assumptions, not verified measurements.")
    pv_repairs = pvsyst_df.loc[
        pvsyst_df["P_source_repaired"], ["time", "P_raw_w", "P"]
    ].rename(columns={"P_raw_w": "Original power (W)", "P": "Assumed repaired power (W)"})
    pv_repairs.to_csv(os.path.join(TABLES_DIR, "pvsyst_source_repairs.csv"), index=False)
print("=" * 72)

# ===== PV SYSTEM ANALYSIS =====
annual_energy_mwh, capacity_factor = calculate_pv_metrics(df)

print(f"Annual PV Energy (MWh): {annual_energy_mwh:.2f}")
print(f"PV Capacity Factor (%): {capacity_factor * 100:.2f}")
#

print(f"Rows: {len(df)}")
print("-" * 72)
print("CYPRUS DAY-AHEAD MARKET DATA")
print(f"Source workbook: {dam_price_path}")
print(f"Published 30-minute observations: {len(dam_dispatch_df):,}")
print(f"Observed period: {dam_dispatch_df['Timestamp'].min()} to {dam_dispatch_df['Timestamp'].max()}")
print(f"Observed days used: {DAM_OBSERVED_DAYS}")
print(f"Annualization factor: {DAM_ANNUALIZATION_FACTOR:.6f} = 365/359")
print(f"Mean published DAM price: {dam_dispatch_df['DAM_Price_EUR_per_MWh'].mean():.2f} €/MWh")
print("Market regime: direct DAM participation through supplier/aggregator")
print(f"Central variable adder above DAM: {grid_variable_adder_eur_per_mwh:.2f} €/MWh")
print(f"Generic demand charge: {grid_demand_charge_eur_per_kw_year:.2f} €/kW-year")
print(f"Fixed supply + metering: {grid_fixed_supply_metering_eur_per_year:.0f} €/year")
print(
    "CAUTION: supplier margin, imbalance, demand and fixed charges are generic "
    "screening assumptions, not a binding Cyprus supplier quotation."
)
print("-" * 72)

# ====== PEM OPTIMIZATION =====
print(f"PEM Scenarios (MW): {pem_sizes_mw}")
print(f"PEM CAPEX Scenarios (€/kW): {pem_capex_scenarios}")

pem_sizes_results = []
h2_results = []
utilization_results = []

# ====== GRID LIMIT & CURTAILMENT SETUP =====
selected_grid_limit_mw = 6

# v1.4 source comparison at the fixed engineering design point.
# Both sources use the same grid-export constraint and configured reference PEM model.
pv_source_comparison = build_pv_source_comparison(
    pvgis_df, pvsyst_df, selected_grid_limit_mw, selected_pem_mw
)
pv_source_comparison.to_csv(
    os.path.join(TABLES_DIR, "pv_source_comparison_summary.csv"), index=False
)
print("\nPVGIS vs PVsyst reference comparison:")
print(pv_source_comparison.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))


# v1.5 fine multi-objective sizing using the current Tran-system SEC curve.
multiobjective_pem_sizing = build_multiobjective_pem_sizing_table(
    df=df,
    grid_limit_mw=selected_grid_limit_mw,
    capex_per_kw=pem_capex_per_kw,
    hydrogen_price_eur_per_kg=hydrogen_sale_price,
    discount_rate=discount_rate,
    project_lifetime_years=project_lifetime_years
)

pareto_pem_sizing = multiobjective_pem_sizing[
    multiobjective_pem_sizing["Pareto Efficient"]
].copy()

multiobjective_pem_sizing.to_csv(
    os.path.join(TABLES_DIR, "multiobjective_pem_sizing.csv"),
    index=False
)

pareto_pem_sizing.to_csv(
    os.path.join(TABLES_DIR, "pareto_pem_sizing.csv"),
    index=False
)

balanced_design, minimum_lcoh_design, maximum_npv_design, first_999_design = \
    summarize_multiobjective_sizing(multiobjective_pem_sizing)

print("\n=== v1.5 MULTI-OBJECTIVE PEM SIZING ===")
print(
    f"Balanced compromise: {balanced_design['PEM Size (MW)']:.2f} MW | "
    f"Recovery {balanced_design['Curtailment Recovery (%)']:.1f}% | "
    f"LCOH {balanced_design['Discounted LCOH (EUR/kg H2)']:.2f} €/kg | "
    f"NPV {balanced_design['NPV (EUR)']/1e6:.2f} M€"
)
print(
    f"Minimum-LCOH point: {minimum_lcoh_design['PEM Size (MW)']:.2f} MW | "
    f"{minimum_lcoh_design['Discounted LCOH (EUR/kg H2)']:.2f} €/kg"
)
print(
    f"Maximum-NPV point: {maximum_npv_design['PEM Size (MW)']:.2f} MW | "
    f"{maximum_npv_design['NPV (EUR)']/1e6:.2f} M€"
)
if first_999_design is not None:
    print(
        f"First fine-sweep size at ≥99.9% recovery: "
        f"{first_999_design['PEM Size (MW)']:.2f} MW"
    )
print(
    f"Pareto-efficient candidates: "
    f"{int(multiobjective_pem_sizing['Pareto Efficient'].sum())} "
    f"of {len(multiobjective_pem_sizing)}"
)

# Record the optimized balanced design as a diagnostic. The reporting design
# point remains the configured 1.60 MW value so every strategy, OEM comparison,
# lifecycle result and battery diagnostic uses one consistent capacity.
balanced_reference_mw = float(balanced_design["PEM Size (MW)"])


# Select PEM size automatically by MINIMUM opportunity-cost-adjusted LCOH
# under the non-hybrid strategy and base-case CAPEX assumption.
pem_sizing_lcoh_with_opportunity = []
for candidate_pem_mw in pem_sizes_mw:
    candidate_op = simulate_nonhybrid_operation(
        df, candidate_pem_mw, selected_grid_limit_mw
    )
    candidate_capex_eur = candidate_pem_mw * 1000 * pem_capex_per_kw
    candidate_fixed_opex_eur = candidate_capex_eur * pem_opex_fraction
    candidate_opportunity_cost_eur, _ = calculate_pv_opportunity_cost_proxy(
        df, candidate_op["lost_export_w"]
    )
    candidate_lcoh = calculate_discounted_lcoh(
        candidate_capex_eur,
        candidate_fixed_opex_eur + candidate_opportunity_cost_eur,
        candidate_op["annual_h2_kg"],
        discount_rate,
        project_lifetime_years
    )
    pem_sizing_lcoh_with_opportunity.append(candidate_lcoh)

# Engineering design selection: retain the fixed 1.60 MW reporting design.
# The balanced and purely economic optima remain diagnostics and do not silently
# overwrite the project definition.
economic_optimum_index = int(np.nanargmin(pem_sizing_lcoh_with_opportunity))
economic_optimum_pem_mw = float(pem_sizes_mw[economic_optimum_index])

print(
    f"Fixed reporting PEM design: {selected_pem_mw:.2f} MW"
)
print(
    f"Balanced multi-objective diagnostic: {balanced_reference_mw:.2f} MW"
)
print(
    f"Economic minimum-LCOH diagnostic: {economic_optimum_pem_mw:.2f} MW "
    f"({pem_sizing_lcoh_with_opportunity[economic_optimum_index]:.2f} €/kg H2)"
)

df["curtailed_power_w"], selected_curtailed_mwh = calculate_hourly_curtailment(df, selected_grid_limit_mw)
selected_nonhybrid_op = simulate_nonhybrid_operation(
    df, selected_pem_mw, selected_grid_limit_mw
)
# Selected Strategy A base case uses strict curtailment-only operation.
df["pem_input_w"] = pd.Series(selected_nonhybrid_op["pem_power_w"], index=df.index)
selected_h2_kg = selected_nonhybrid_op["annual_h2_kg"]
selected_lost_export_mwh = selected_nonhybrid_op["lost_export_mwh"]
selected_opportunity_cost_eur, selected_monthly_opportunity_cost = (
    calculate_pv_opportunity_cost_proxy(
        df, selected_nonhybrid_op["lost_export_w"]
    )
)

# ===== MINIMUM-LOAD SENSITIVITY (recovery vs PEM size, per min-load assumption) =====
min_load_sensitivity_results = minimum_load_sensitivity(
    df=df, pem_sizes_mw=pem_sizes_mw, min_load_fractions=MIN_LOAD_SENSITIVITY
)

# ===== CURTAILMENT RECOVERY VS PEM SIZE (base-case grid limit) =====
curtailment_recovery_results = calculate_curtailment_recovery_vs_pem_size(
    df, pem_sizes_mw, selected_curtailed_mwh
)

print("\nCurtailment Recovery vs PEM Size:")
for pem_size, recovery in zip(pem_sizes_mw, curtailment_recovery_results):
    print(
        f"PEM {pem_size:.1f} MW -> "
        f"{recovery:.1f}% of curtailed energy recovered"
    )

#
selected_water_m3 = (
    selected_h2_kg * water_liters_per_kg_h2 / 1000
)

print(f"\n--- SELECTED BASE CASE ---")
print(f"PEM Size: {selected_pem_mw:.2f} MW (fixed reporting design point)")
print(f"Grid Export Limit: {selected_grid_limit_mw} MW")
print(f"Curtailed PV Energy: {selected_curtailed_mwh:.1f} MWh/year")
print(f"Hydrogen Production: {selected_h2_kg:.0f} kg/year")
print(f"Water Consumption: {selected_water_m3:.1f} m3/year")

# ===== CURTAILMENT DIAGNOSTICS: ACCEPTED / REJECTED BREAKDOWN =====
curtailment_diagnostics = diagnose_pem_curtailment(df, pem_sizes_mw)
print("\nCurtailment Diagnostics:")
print(curtailment_diagnostics.to_string(index=False))

print("\nCurtailment Avoided / Residual by PEM Size:")
for _, row in curtailment_diagnostics.iterrows():
    print(
        f"PEM {row['PEM Size (MW)']:.2f} MW | "
        f"Avoided: {row['Avoided Curtailment (MWh)']:.1f} MWh | "
        f"Residual: {row['Residual Curtailment (MWh)']:.1f} MWh"
    )

#
# ===== EXERGY ANALYSIS (base case, load-weighted) =====
pem_size_w = selected_pem_mw * 1_000_000
annual_h2_kg_weighted, exergy_eff, energy_eff_lhv, avg_spec_consumption = \
    calculate_weighted_exergy_efficiency(df["pem_input_w"].values, pem_size_w)
print(f"\nExergy efficiency (load-weighted, base case): {exergy_eff*100:.1f}%")
print(
    f"Energy-weighted avg gross specific consumption: "
    f"{avg_spec_consumption:.1f} kWh/kg H2"
)
#

results_table = pd.DataFrame()
results_table["PEM Size (MW)"] = pem_sizes_mw
for capex_scenario in pem_capex_scenarios:
    capex_col = []
    for pem_size_mw in pem_sizes_mw:
        pem_energy_mwh, h2_kg, utilization, pem_capex_eur, one_year_capex_intensity = analyze_pem_size(
            df, pem_size_mw, capex_scenario)
        capex_col.append(one_year_capex_intensity)
        # H2 and utilization don't depend on CAPEX, collect only on first pass
        if capex_scenario == pem_capex_scenarios[0]:
            pem_sizes_results.append(pem_size_mw)
            h2_results.append(h2_kg)
            utilization_results.append(utilization * 100)
    results_table[f"CAPEX @{capex_scenario} €/kW"] = capex_col
print(results_table.to_string(index=False))

# ===== DYNAMIC PEM-SIZING OPTIMUM =====
# Used by assertions and final conclusions, never hard-coded.
max_h2_index = int(np.nanargmax(np.asarray(h2_results, dtype=float)))
max_h2_pem_mw = float(pem_sizes_results[max_h2_index])
max_h2_kg_year = float(h2_results[max_h2_index])

assert max_h2_pem_mw in pem_sizes_mw, \
    "FAILED: Maximum-H2 PEM size not found in tested PEM sizes."

assert len(pem_sizes_results) == len(h2_results), \
    "FAILED: PEM-size and H2-result arrays have different lengths."

# ====== GRID LIMIT SENSITIVITY ======
print("\nGrid Limit Sensitivity:")
for grid_limit_mw in grid_limits_mw:
    curtailed_power_w, curtailed_mwh = calculate_hourly_curtailment(df, grid_limit_mw)
    op_grid = simulate_nonhybrid_operation(df, selected_pem_mw, grid_limit_mw)
    h2_from_grid_limit_kg = op_grid["annual_h2_kg"]
    ###
    print(
    f"  Grid limit: {grid_limit_mw} MW "
    f"| Curtailed: {curtailed_mwh:.1f} MWh "
    f"| H2 production: {h2_from_grid_limit_kg:.0f} kg/year"
    )

# ===== ELECTROLYZER OPERATING HOURS ANALYSIS =====
pem_size_w = selected_pem_mw * 1_000_000
print("\nElectrolyzer Operating Hours Analysis:")
for grid_limit_mw in grid_limits_mw:
    op_grid = simulate_nonhybrid_operation(df, selected_pem_mw, grid_limit_mw)
    operating_hours = op_grid["operating_hours"]
    full_load_hours = op_grid["full_load_hours"]
    print(f"  Grid limit: {grid_limit_mw} MW | Operating hours: {operating_hours} h | Full-load hours: {full_load_hours:.1f} h")
###
lcoh_opex_sensitivity = calculate_lcoh_opex_sensitivity(
    selected_pem_mw,
    pem_capex_per_kw,
    pem_opex_scenarios_per_kw_year,
    selected_h2_kg,
    discount_rate,
    project_lifetime_years,
    selected_opportunity_cost_eur
)

print("\nLCOH OPEX Sensitivity:")

for opex, lcoh in zip(
    pem_opex_scenarios_per_kw_year,
    lcoh_opex_sensitivity
):
    print(
        f"OPEX {opex} €/kW/year "
        f"-> Discounted LCOH {lcoh:.2f} €/kg H2"
    )

###


###
# ===== RESULTS TABLE =====
results_table["H2 (kg/year)"] = h2_results
results_table["Utilization (%)"] = utilization_results
results_table["Lost Export Energy (MWh/year)"] = [
    simulate_nonhybrid_operation(df, pem_mw, selected_grid_limit_mw)["lost_export_mwh"]
    for pem_mw in pem_sizes_mw
]
results_table["PV Opportunity Cost (€/year)"] = [
    calculate_pv_opportunity_cost_proxy(
        df,
        simulate_nonhybrid_operation(
            df, pem_mw, selected_grid_limit_mw
        )["lost_export_w"]
    )[0]
    for pem_mw in pem_sizes_mw
]
print(results_table.to_string(index=False))

# ===== ECONOMIC ANALYSIS =====

# Annual hydrogen revenue
hydrogen_revenue_eur = (
    selected_h2_kg
    * hydrogen_sale_price
)

# PEM CAPEX
pem_capex_eur = (
    selected_pem_mw
    * 1000
    * pem_capex_per_kw
)

# Capital Recovery Factor
crf = (
    discount_rate
    * (1 + discount_rate) ** project_lifetime_years
    / (
        (1 + discount_rate) ** project_lifetime_years
        - 1
    )
)

# Annualized PEM CAPEX
annualized_pem_capex = (
    pem_capex_eur
    * crf
)

# Annual OPEX
annual_opex = (
    pem_capex_eur
    * pem_opex_fraction
)

# Reporting metrics
simple_net_hydrogen_value = (
    hydrogen_revenue_eur
    - annualized_pem_capex
)

net_hydrogen_value_with_opex = (
    hydrogen_revenue_eur
    - annualized_pem_capex
    - annual_opex
    - selected_opportunity_cost_eur
)

# LCOH
simple_lcoh = calculate_simple_lcoh(
    pem_capex_eur,
    annual_opex + selected_opportunity_cost_eur,
    selected_h2_kg,
    project_lifetime_years
)

discounted_lcoh = calculate_discounted_lcoh(
    pem_capex_eur,
    annual_opex + selected_opportunity_cost_eur,
    selected_h2_kg,
    discount_rate,
    project_lifetime_years
)

# CAPEX sensitivity
lcoh_capex_sensitivity = calculate_lcoh_for_capex_scenarios(
    selected_pem_mw,
    selected_h2_kg,
    pem_capex_scenarios,
    selected_opportunity_cost_eur
)

discounted_lcoh_capex_sensitivity = (
    calculate_discounted_lcoh_for_capex_scenarios(
        selected_pem_mw,
        selected_h2_kg,
        pem_capex_scenarios,
        discount_rate,
        project_lifetime_years,
        selected_opportunity_cost_eur
    )
)

# Grid-limit sensitivity
lcoh_grid_sensitivity = calculate_lcoh_vs_grid_limit(
    df,
    grid_limits_mw,
    selected_pem_mw,
    pv_export_price_eur_per_mwh
)

discounted_lcoh_grid_sensitivity = (
    calculate_discounted_lcoh_vs_grid_limit(
        df,
        grid_limits_mw,
        selected_pem_mw,
        discount_rate,
        project_lifetime_years,
        pv_export_price_eur_per_mwh
    )
)




# ===== BENCHMARK COMPARISON =====
# Curtailment-to-H2 LCOH is compared against literature values
# for renewable hydrogen produced via electrolysis directly
# connected to renewable electricity generation.
#
# Dedicated PV-to-H2 LCOH benchmark (literature, 2023-2024):
#   Southern Europe / MENA region: 3.5 - 6.0 €/kg H2
#   Sources: IRENA (2023), IEA Global Hydrogen Review (2024),
#            EU Hydrogen Backbone reports
#
# This is NOT calculated from this model.
# This model compares Strategy A curtailment-only dispatch with Strategy B
# PV-priority + grid-baseline dispatch. Strategy B may displace exportable PV,
# which is explicitly charged as an opportunity cost.
# A dedicated system would require a separate full techno-economic model
# with matched PEM sizing, grid connection costs, and offtake assumptions.
benchmark_dedicated_lcoh_low  = 3.5   # €/kg, southern Europe literature
benchmark_dedicated_lcoh_high = 6.0   # €/kg, southern Europe literature



print(f"Hydrogen revenue (€): {hydrogen_revenue_eur:,.0f}")
print(f"Annualized PEM CAPEX / ACC (€): {annualized_pem_capex:,.0f}  [CRF={crf:.4f}]")
print(f"Simple net hydrogen value (€): {simple_net_hydrogen_value:,.0f}")
print(f"Annual OPEX (€): {annual_opex:,.0f}")
print("PV export opportunity-cost basis: supplied 2025-2026 EAC RES purchase-price proxy at 11 kV")
print(
    "Monthly proxy range (€/MWh): "
    f"{min(PV_EXPORT_PRICE_PROXY_11KV_EUR_PER_MWH.values()):.2f} - "
    f"{max(PV_EXPORT_PRICE_PROXY_11KV_EUR_PER_MWH.values()):.2f}"
)
print("\nMonthly PV opportunity-cost breakdown:")
print(
    selected_monthly_opportunity_cost.to_string(
        index=False,
        formatters={
            "Lost Export Energy (MWh)": lambda x: f"{x:,.2f}",
            "Opportunity Cost (€)": lambda x: f"{x:,.0f}",
            "PV Export Price Proxy (€/MWh)": lambda x: f"{x:.2f}",
        }
    )
)
print(f"Lost export energy due to PEM (MWh/year): {selected_lost_export_mwh:.2f}")
print(f"PV opportunity cost (€/year): {selected_opportunity_cost_eur:,.0f}")
assert np.isclose(
    selected_monthly_opportunity_cost["Opportunity Cost (€)"].sum(),
    selected_opportunity_cost_eur,
    rtol=0,
    atol=1e-6
), "Monthly opportunity-cost breakdown does not reconcile to annual total."

print(f"Net hydrogen value with OPEX + opportunity cost (€): {net_hydrogen_value_with_opex:,.0f}")
print(f"Simple LCOH incl. PV opportunity cost (€/kg H2): {simple_lcoh:.2f}")


###
print(
    f"Curtailment-to-H2 simplified annualized LCOH: "
    f"{discounted_lcoh:.2f} €/kg H2"
)
###
print("LCOH CAPEX Sensitivity (simple / annualized):")

for capex, lcoh_s, lcoh_d in zip(
    pem_capex_scenarios,
    lcoh_capex_sensitivity,
    discounted_lcoh_capex_sensitivity
):
    print(
        f"CAPEX {capex} €/kW -> "
        f"Simple {lcoh_s:.2f} / "
        f"Annualized {lcoh_d:.2f} €/kg H2"
    )

print("LCOH Grid Limit Sensitivity (simple / annualized):")
for grid_limit, lcoh_s, lcoh_d in zip(grid_limits_mw, lcoh_grid_sensitivity, discounted_lcoh_grid_sensitivity):
    print(f"  Grid limit {grid_limit} MW -> Simple {lcoh_s:.2f} / Annualized {lcoh_d:.2f} €/kg H2")


print(f"Dedicated PV-to-H2 LCOH (literature benchmark): {benchmark_dedicated_lcoh_low:.1f} - {benchmark_dedicated_lcoh_high:.1f} €/kg H2")

# The optimized balanced design is not necessarily present in the
# coarse pem_sizes_mw sensitivity grid. Use the actual selected simulation
# output instead of indexing the coarse list by MW value.
selected_utilization = float(selected_nonhybrid_op["utilization_pct"])
print(f"Note: the Strategy A LCOH reflects strict curtailment-only dispatch and utilization ({selected_utilization:.2f}%).")

# ===== NPV ANALYSIS =====
# annual_cashflow_for_npv = revenue - opex only.
# CAPEX is deducted as lump sum at year 0 inside calculate_npv().
# annualized_pem_capex must NOT appear here.
annual_cashflow_for_npv = (
    hydrogen_revenue_eur
    - annual_opex
    - selected_opportunity_cost_eur
)

npv = calculate_npv(pem_capex_eur, annual_cashflow_for_npv, discount_rate, project_lifetime_years)
print(f"NPV (€): {npv:,.0f}")

# ===== PROJECT 1.6: TRAN SYSTEM MAP VS EES GROSS-STACK MAP =====
# This is a model-boundary sensitivity comparison, not experimental validation.
ees_hourly_h2_kg, ees_annual_h2_kg = calculate_hydrogen_from_pem_input(
    selected_nonhybrid_op["pem_power_w"],
    selected_pem_mw * 1_000_000,
    model="EES"
)
selected_pem_energy_kwh = selected_nonhybrid_op["total_pem_energy_mwh"] * 1000.0
ees_average_sec = (
    selected_pem_energy_kwh / ees_annual_h2_kg
    if ees_annual_h2_kg > 0 else np.nan
)
ees_lhv_efficiency = (
    ees_annual_h2_kg * h2_lower_heating_value_kwh_per_kg
    / selected_pem_energy_kwh
)
ees_exergy_efficiency = (
    ees_annual_h2_kg * EX_H2_KWH_PER_KG
    / selected_pem_energy_kwh
)
ees_lcoh = calculate_discounted_lcoh(
    pem_capex_eur,
    annual_opex + selected_opportunity_cost_eur,
    ees_annual_h2_kg,
    discount_rate,
    project_lifetime_years
)
ees_annual_cashflow = (
    ees_annual_h2_kg * hydrogen_sale_price
    - annual_opex
    - selected_opportunity_cost_eur
)
ees_npv = calculate_npv(
    pem_capex_eur,
    ees_annual_cashflow,
    discount_rate,
    project_lifetime_years
)
tran_average_sec = selected_pem_energy_kwh / selected_h2_kg
tran_lhv_efficiency = (
    selected_h2_kg * h2_lower_heating_value_kwh_per_kg
    / selected_pem_energy_kwh
)
tran_exergy_efficiency = (
    selected_h2_kg * EX_H2_KWH_PER_KG
    / selected_pem_energy_kwh
)
pem_model_comparison = pd.DataFrame([
    {
        "Model": "TRAN system-level",
        "Annual H2 (kg/year)": selected_h2_kg,
        "Average SEC (kWh/kg)": tran_average_sec,
        "LHV Efficiency (%)": tran_lhv_efficiency * 100,
        "Exergy Efficiency (%)": tran_exergy_efficiency * 100,
        "Discounted LCOH (EUR/kg H2)": discounted_lcoh,
        "NPV (EUR)": npv,
    },
    {
        "Model": "EES gross-stack",
        "Annual H2 (kg/year)": ees_annual_h2_kg,
        "Average SEC (kWh/kg)": ees_average_sec,
        "LHV Efficiency (%)": ees_lhv_efficiency * 100,
        "Exergy Efficiency (%)": ees_exergy_efficiency * 100,
        "Discounted LCOH (EUR/kg H2)": ees_lcoh,
        "NPV (EUR)": ees_npv,
    },
])
pem_model_comparison.to_csv(
    os.path.join(TABLES_DIR, "pem_model_comparison_tran_vs_ees.csv"),
    index=False
)
print("\n=== PROJECT 1.6: TRAN VS EES PERFORMANCE SENSITIVITY ===")
print(pem_model_comparison.round(2).to_string(index=False))
print(
    "Boundary warning: TRAN is system-level and EES is gross-stack. "
    "This comparison is a sensitivity test, not validation."
)

# =========================================
# v1.3 HYBRID OPERATING STRATEGY SENSITIVITY
# =========================================
# Reuses pem_capex_eur and annual_opex (fixed, CAPEX-based OPEX) from the
# base-case economic analysis above. discounted_lcoh and npv (both just
# computed for the selected non-hybrid base case) serve as the
# comparison reference. The hybrid 0% grid-baseline case is intentionally
# different because it follows all available PV up to PEM rated power.

hybrid_summary = hybrid_strategy_sensitivity(
    df,
    selected_pem_mw,
    baseline_load_fractions,
    electricity_price_scenarios_eur_per_mwh,
    pem_capex_eur,
    annual_opex,
    discount_rate,
    project_lifetime_years,
    hydrogen_sale_price,
    pv_export_price_eur_per_mwh
)

# Primary v1.7 Strategy B analysis. The older fixed-price matrix above is
# retained only as a transparent generic sensitivity. Final reporting and
# lifecycle calculations use this DAM-linked result.
dam_hybrid_summary, dam_hybrid_operation_cache = dam_hybrid_strategy_sensitivity(
    dam_dispatch_df=dam_dispatch_df,
    selected_pem_mw=selected_pem_mw,
    baseline_fractions=baseline_load_fractions,
    price_caps=dam_dispatch_price_caps_eur_per_mwh,
    pem_capex_eur=pem_capex_eur,
    fixed_annual_opex_eur=annual_opex,
    discount_rate=discount_rate,
    project_lifetime_years=project_lifetime_years,
    hydrogen_sale_price=hydrogen_sale_price,
)
dam_hybrid_summary.to_csv(
    os.path.join(TABLES_DIR, "strategy_b_dam_price_cap_sensitivity.csv"),
    index=False,
)
print("\n=== Strategy B: 30-minute Cyprus DAM-linked sensitivity ===")
print(dam_hybrid_summary.round(2).to_string(index=False))


###
cyprus_2023_benchmark_results = calculate_cyprus_2023_industrial_benchmark(
    df=df,
    selected_pem_mw=selected_pem_mw,
    baseline_load_fractions=baseline_load_fractions,
    industrial_price_eur_per_mwh=cyprus_2023_industrial_price_eur_per_mwh,
    pem_capex_eur=pem_capex_eur,
    fixed_annual_opex_eur=annual_opex,
    discount_rate=discount_rate,
    project_lifetime_years=project_lifetime_years,
    hydrogen_sale_price=hydrogen_sale_price,
    pv_export_price_eur_per_mwh=pv_export_price_eur_per_mwh
)
###
###
# ===== LEGACY STATIC STRATEGY B INTERNAL CHECKS =====
# Keep the calculations for regression checking, but do not mix their old
# fixed-price tables with the primary DAM-linked output.
_terminal_stdout_before_legacy = sys.stdout
sys.stdout = io.StringIO()

print("\n=== STRATEGY B DISPATCH SANITY CHECKS ===")

# Check 1: PEM source balance closes for every scenario.
energy_balance = (
    hybrid_summary["Total PEM Energy (MWh)"]
    - hybrid_summary["PV Energy to PEM (MWh)"]
    - hybrid_summary["Purchased Energy (MWh)"]
)
assert np.allclose(energy_balance, 0.0, atol=1e-8), \
    "FAILED: Hybrid PEM source balance does not close."
print("PEM source balance: PASS")

# Check 2: Purchased electricity is never negative.
assert (hybrid_summary["Purchased Energy (MWh)"] >= -1e-9).all(), \
    "FAILED: Negative grid electricity purchase detected."
print("Grid-purchase non-negativity: PASS")

# Check 3: The duplicate zero-baseline PV-only case is absent. Every Strategy B
# case has a physical baseline.
assert (hybrid_summary["Baseline Load (%)"] >= MIN_PEM_LOAD_FRACTION * 100).all(), \
    "FAILED: Strategy B contains a zero or sub-minimum baseline."
print("No duplicate zero-baseline case in Strategy B: PASS")

# Check 4: Physical metrics are bounded.
assert hybrid_summary["Utilization (%)"].between(0, 100 + 1e-8).all(), \
    "FAILED: Hybrid utilization outside 0-100%."
assert hybrid_summary["Share PEM Energy from PV (%)"].between(0, 100).all(), \
    "FAILED: PV share outside 0-100%."
assert (hybrid_summary["Residual Curtailment (MWh)"] >= -1e-9).all(), \
    "FAILED: Negative residual curtailment."
print("Physical bounds: PASS")

# Strategy A must not displace exports. Strategy B may do so, but every displaced
# MWh must be represented by a non-negative opportunity cost.
assert np.isclose(selected_nonhybrid_op["lost_export_mwh"], 0.0, atol=1e-8), \
    "FAILED: Strategy A displaces otherwise-exportable PV."
assert (hybrid_summary["Lost Export Energy (MWh)"] >= -1e-9).all(), \
    "FAILED: Strategy B has negative lost-export energy."
assert (hybrid_summary["PV Opportunity Cost (€/year)"] >= -1e-9).all(), \
    "FAILED: Strategy B has negative PV opportunity cost."
assert (hybrid_summary["Lost Export Energy (MWh)"] > 0.0).any(), \
    "FAILED: Strategy B does not reflect PV-priority export displacement."
print("Strategy A export protection and Strategy B opportunity-cost accounting: PASS")

# Check 5: Dispatch physics does not depend on the electricity-price scenario.
for baseline, group in hybrid_summary.groupby("Baseline Load (%)"):
    assert np.allclose(group["H2 (kg/year)"], group["H2 (kg/year)"].iloc[0]), \
        f"FAILED: H2 changes with price at baseline {baseline}%."
    assert np.allclose(group["Purchased Energy (MWh)"], group["Purchased Energy (MWh)"].iloc[0]), \
        f"FAILED: Purchased energy changes with price at baseline {baseline}%."
print("Price-independent physical dispatch: PASS")

print("ALL STRATEGY B DISPATCH SANITY CHECKS PASSED")
###



###

print("\n=== Strategy B: PV-Priority + Grid-Baseline Sensitivity ===")
print(hybrid_summary.round(2).to_string(index=False))

# ----- Where does hybrid beat the non-hybrid base case? -----
hybrid_summary["Better than non-hybrid base case?"] = hybrid_summary["LCOH (€/kg H2)"] < discounted_lcoh

print(
    f"\nStrategy B vs Strategy A reference (discounted LCOH = "
    f"{discounted_lcoh:.2f} €/kg H2):"
)
improved = hybrid_summary[
    (hybrid_summary["Better than non-hybrid base case?"])
].sort_values(["Baseline Load (%)", "Electricity Price (€/MWh)"])

if improved.empty:
    print(
        "  No tested Strategy B combination beats the Strategy A LCOH "
        "under current assumptions."
    )
else:
    for _, row in improved.iterrows():
        print(
            f"  Baseline {row['Baseline Load (%)']:.0f}% + "
            f"electricity at {row['Electricity Price (€/MWh)']:.0f} €/MWh -> "
            f"LCOH {row['LCOH (€/kg H2)']:.2f} €/kg H2 "
            f"(vs {discounted_lcoh:.2f} €/kg H2 reference)"
        )

###

print("\n=== Cyprus 2023 Industrial Electricity Benchmark ===")
print(
    f"Representative electricity purchase price: "
    f"{cyprus_2023_industrial_price_eur_per_mwh:.0f} €/MWh"
)

print(
    cyprus_2023_benchmark_results[
        [
            "Baseline Load (%)",
            "H2 (kg/year)",
            "Utilization (%)",
            "PV Energy to PEM (MWh)",
            "Purchased Energy (MWh)",
            "Lost Export Energy (MWh)",
            "PV Opportunity Cost (€/year)",
            "Electricity Expenditure (€/year)",
            "LCOH (€/kg H2)",
            "NPV (€)"
        ]
    ].round(2).to_string(index=False)
)
###
cyprus_lcoh_optimum = cyprus_2023_benchmark_results.loc[
    cyprus_2023_benchmark_results["LCOH (€/kg H2)"].idxmin()
]

cyprus_npv_optimum = cyprus_2023_benchmark_results.loc[
    cyprus_2023_benchmark_results["NPV (€)"].idxmax()
]

print("\nCyprus 2023 benchmark optimum:")

print(
    f"LCOH-optimal baseline: "
    f"{cyprus_lcoh_optimum['Baseline Load (%)']:.0f}% | "
    f"LCOH = {cyprus_lcoh_optimum['LCOH (€/kg H2)']:.2f} €/kg H2 | "
    f"NPV = €{cyprus_lcoh_optimum['NPV (€)']:,.0f}"
)

print(
    f"NPV-optimal baseline: "
    f"{cyprus_npv_optimum['Baseline Load (%)']:.0f}% | "
    f"LCOH = {cyprus_npv_optimum['LCOH (€/kg H2)']:.2f} €/kg H2 | "
    f"NPV = €{cyprus_npv_optimum['NPV (€)']:,.0f}"
)
###
# ===== OPTIMAL BASELINE BY ELECTRICITY PRICE =====
optimal_baseline_table = calculate_optimal_baseline_by_electricity_price(hybrid_summary)

print("\nOptimal PEM Baseline by Electricity Price:")
print(
    optimal_baseline_table.to_string(
        index=False,
        formatters={
            "Price (€/MWh)": lambda x: f"{x:.0f}",
            "LCOH-optimal baseline (%)": lambda x: f"{x:.0f}",
            "Minimum LCOH (€/kg H2)": lambda x: f"{x:.2f}",
            "NPV at LCOH optimum (€)": lambda x: f"{x:,.0f}",
            "NPV-optimal baseline (%)": lambda x: f"{x:.0f}",
            "Maximum NPV (€)": lambda x: f"{x:,.0f}",
            "LCOH at NPV optimum (€/kg H2)": lambda x: f"{x:.2f}",
        }
    )
)

# Cross-check: one optimum row must exist for every tested electricity price.
assert len(optimal_baseline_table) == len(electricity_price_scenarios_eur_per_mwh), \
    "FAILED: Optimal-baseline table does not cover every electricity-price scenario."

sys.stdout = _terminal_stdout_before_legacy
print("Legacy fixed-price Strategy B regression checks: PASS, tables suppressed")

# ===== HYDROGEN PRICE SENSITIVITY =====
npv_results = calculate_npv_price_sensitivity(
    hydrogen_price_scenarios, selected_h2_kg,
    annual_opex, selected_opportunity_cost_eur, pem_capex_eur,
    discount_rate, project_lifetime_years)

print("\nHydrogen Price Sensitivity:")
for h2_price, npv_result in zip(hydrogen_price_scenarios, npv_results):
    print(f"H2 price {h2_price} €/kg -> NPV €{npv_result:,.0f}")

# ===== NPV GRID LIMIT SENSITIVITY =====
npv_grid_results = calculate_npv_grid_sensitivity(
    df, grid_limits_mw, selected_pem_mw,
    hydrogen_sale_price, annual_opex, pem_capex_eur,
    discount_rate, project_lifetime_years,
    pv_export_price_eur_per_mwh
)

print("\nNPV Grid Limit Sensitivity:")
for grid_limit, npv_result in zip(grid_limits_mw, npv_grid_results):
    print(f"Grid limit {grid_limit} MW -> NPV €{npv_result:,.0f}")

# NPV Heatmap
npv_df = calculate_npv_heatmap_data(
    df,
    grid_limits_mw,
    hydrogen_price_scenarios,
    selected_pem_mw,
    annual_opex,
    pem_capex_eur,
    discount_rate,
    project_lifetime_years,
    pv_export_price_eur_per_mwh
)
print(npv_df.round(0))
###
###
assert (
    hybrid_summary["Share PEM Energy from PV (%)"]
    .between(0, 100)
    .all()
), "FAILED: Curtailed-energy share outside 0-100%."

###
###

# =========================================
# FINAL BASE CASE + STRATEGY B ECONOMIC SELECTION
# =========================================

strategy_b_fixed_baseline_rows = dam_hybrid_summary[
    np.isclose(
        dam_hybrid_summary["Baseline Load (%)"],
        hybrid_design_baseline_fraction * 100,
    )
].copy()
if strategy_b_fixed_baseline_rows.empty:
    raise ValueError(
        "The configured Strategy B baseline is absent from the DAM sensitivity."
    )

# Headline Strategy B result: minimum LCOH among the tested price caps at the
# fixed baseline. NPV optimum is also reported independently so the objectives
# cannot be conflated if they diverge in a future run.
hybrid_design = strategy_b_fixed_baseline_rows.loc[
    strategy_b_fixed_baseline_rows["LCOH (€/kg H2)"].idxmin()
]
strategy_b_npv_optimum = strategy_b_fixed_baseline_rows.loc[
    strategy_b_fixed_baseline_rows["NPV (€)"].idxmax()
]
strategy_b_low_cap_plateau = strategy_b_fixed_baseline_rows[
    strategy_b_fixed_baseline_rows["All-in Import Price Cap (€/MWh)"] <= 100.0
]
strategy_b_low_cap_lcoh_spread = float(
    strategy_b_low_cap_plateau["LCOH (€/kg H2)"].max()
    - strategy_b_low_cap_plateau["LCOH (€/kg H2)"].min()
)
hybrid_design_all_in_price_cap_eur_per_mwh = float(
    hybrid_design["All-in Import Price Cap (€/MWh)"]
)
selected_strategy_b_op = dam_hybrid_operation_cache[
    (hybrid_design_baseline_fraction, hybrid_design_all_in_price_cap_eur_per_mwh)
]

# Separate 200 EUR/MWh comparator with a raised 30% baseline. It is useful for
# illustrating grid use, but it is not called economically optimal.
strategy_b_grid_active_rows = dam_hybrid_summary[
    np.isclose(
        dam_hybrid_summary["Baseline Load (%)"],
        hybrid_grid_active_comparator_baseline_fraction * 100.0,
    )
    &
    np.isclose(
        dam_hybrid_summary["All-in Import Price Cap (€/MWh)"],
        hybrid_grid_active_comparator_cap_eur_per_mwh,
    )
]
if strategy_b_grid_active_rows.empty:
    raise ValueError(
        "The configured grid-active comparator cap is absent from the DAM sensitivity."
    )
strategy_b_grid_active_comparator = strategy_b_grid_active_rows.iloc[0]
strategy_b_grid_active_op = dam_hybrid_operation_cache[
    (
        hybrid_grid_active_comparator_baseline_fraction,
        hybrid_grid_active_comparator_cap_eur_per_mwh,
    )
]

# Retain the former 20%/200 comparator so changing the baseline can be assessed
# directly. Neither illustrative case overwrites the economic selection.
strategy_b_original_grid_comparator = strategy_b_fixed_baseline_rows[
    np.isclose(
        strategy_b_fixed_baseline_rows["All-in Import Price Cap (€/MWh)"],
        hybrid_grid_active_comparator_cap_eur_per_mwh,
    )
].iloc[0]

# PV-priority operation without an import service is a separate economic
# reference. It carries no invented import demand/fixed charges. It uses PV
# that would otherwise be exported, so it is distinct from Strategy A.
strategy_b_pv_only_op = simulate_hybrid_dam_operation(
    dam_dispatch_df, selected_pem_mw, hybrid_design_baseline_fraction, -np.inf,
)
assert np.isclose(strategy_b_pv_only_op["purchased_energy_mwh"], 0.0)
pv_only_annual_cost = annual_opex + strategy_b_pv_only_op["opportunity_cost_eur"]
strategy_b_pv_only_reference = {
    "Baseline Load (%)": 0.0,
    "All-in Import Price Cap (€/MWh)": np.nan,
    "Purchased Energy (MWh)": 0.0,
    "H2 (kg/year)": strategy_b_pv_only_op["annual_h2_kg"],
    "PV Energy to PEM (MWh)": strategy_b_pv_only_op["pv_energy_to_pem_mwh"],
    "Total PEM Energy (MWh)": strategy_b_pv_only_op["total_pem_energy_mwh"],
    "PV Opportunity Cost (€/year)": strategy_b_pv_only_op["opportunity_cost_eur"],
    "Utilization (%)": strategy_b_pv_only_op["utilization_pct"],
    "Contracted Import Capacity (kW)": 0.0,
    "Demand Charge (€/year)": 0.0,
    "Fixed Supply + Metering Charge (€/year)": 0.0,
    "Total Annual Operating + Energy Cost (€/year)": pv_only_annual_cost,
    "LCOH (€/kg H2)": calculate_discounted_lcoh(
        pem_capex_eur, pv_only_annual_cost, strategy_b_pv_only_op["annual_h2_kg"],
        discount_rate, project_lifetime_years,
    ),
    "NPV (€)": calculate_npv(
        pem_capex_eur,
        strategy_b_pv_only_op["annual_h2_kg"] * hydrogen_sale_price - pv_only_annual_cost,
        discount_rate, project_lifetime_years,
    ),
}

# Quantify the recovered interpolation assumption against the clipped original
# export, using the same design and selected B cap without re-optimization.
unrepaired_pv_df = pvsyst_df.copy()
unrepaired_pv_df["P"] = unrepaired_pv_df["P_raw_w"].clip(lower=0)
unrepaired_dam_df = map_hourly_pv_to_dam_intervals(unrepaired_pv_df, dam_prices_df)
unrepaired_a_op = simulate_nonhybrid_operation(
    unrepaired_pv_df, selected_pem_mw, selected_grid_limit_mw,
)
unrepaired_b_op = simulate_hybrid_dam_operation(
    unrepaired_dam_df, selected_pem_mw, hybrid_design_baseline_fraction,
    hybrid_design_all_in_price_cap_eur_per_mwh,
)
pv_preparation_records = []
for preparation, strategy, operation in [
    ("Prepared PV with inferred repairs", "A", simulate_nonhybrid_operation(pvsyst_df, selected_pem_mw, selected_grid_limit_mw)),
    ("Clipped original PV, no interpolation", "A", unrepaired_a_op),
    ("Prepared PV with inferred repairs", "B", simulate_hybrid_dam_operation(map_hourly_pv_to_dam_intervals(pvsyst_df, dam_prices_df), selected_pem_mw, hybrid_design_baseline_fraction, hybrid_design_all_in_price_cap_eur_per_mwh)),
    ("Clipped original PV, no interpolation", "B", unrepaired_b_op),
]:
    cost = annual_opex
    if strategy == "B":
        cost += (
            operation["electricity_cost_eur"] + operation["opportunity_cost_eur"]
            + hybrid_design["Demand Charge (€/year)"]
            + grid_fixed_supply_metering_eur_per_year + collateral_annual_cost_eur
        )
    pv_preparation_records.append({
        "PV preparation": preparation,
        "Strategy": strategy,
        "H2 (kg/year)": operation["annual_h2_kg"],
        "LCOH (EUR/kg H2)": calculate_discounted_lcoh(
            pem_capex_eur, cost, operation["annual_h2_kg"],
            discount_rate, project_lifetime_years,
        ),
        "NPV (EUR)": calculate_npv(
            pem_capex_eur, operation["annual_h2_kg"] * hydrogen_sale_price - cost,
            discount_rate, project_lifetime_years,
        ),
        "Scope": "Same PEM rating, tariff terms and selected B cap; no re-optimization",
    })
pd.DataFrame(pv_preparation_records).to_csv(
    os.path.join(TABLES_DIR, "pvsyst_preparation_sensitivity.csv"), index=False,
)

strategy_b_reporting_cases = pd.DataFrame(
    [
        {
            "Case": "Economic selection at fixed baseline",
            **hybrid_design.to_dict(),
        },
        {
            "Case": "Original 20% grid-active comparator, not optimum",
            **strategy_b_original_grid_comparator.to_dict(),
        },
        {
            "Case": "Raised 30% grid-active comparator, not optimum",
            **strategy_b_grid_active_comparator.to_dict(),
        },
        {
            "Case": "PV-priority reference without grid service",
            **strategy_b_pv_only_reference,
        },
    ]
)

# Test lower adders and the existing low/high contract assumptions explicitly,
# retaining the central case rather than tuning it to obtain more imports.
strategy_b_market_sensitivity_records = []
for market_scenario in market_cost_screening_scenarios:
    scenario_dispatch = dam_dispatch_df.copy()
    scenario_dispatch["All_in_Grid_Price_EUR_per_MWh"] = (
        scenario_dispatch["DAM_Price_EUR_per_MWh"]
        + market_scenario["Variable adder (EUR/MWh)"]
    )
    scenario_table, _ = dam_hybrid_strategy_sensitivity(
        scenario_dispatch, selected_pem_mw,
        [hybrid_design_baseline_fraction, hybrid_grid_active_comparator_baseline_fraction],
        dam_dispatch_price_caps_eur_per_mwh, pem_capex_eur, annual_opex,
        discount_rate, project_lifetime_years, hydrogen_sale_price,
        demand_charge_eur_per_kw_year=market_scenario["Demand charge (EUR/kW-year)"],
        fixed_supply_metering_eur_per_year=market_scenario["Fixed charge (EUR/year)"],
    )
    for _, scenario_row in scenario_table.iterrows():
        strategy_b_market_sensitivity_records.append({
            "Market cost scenario": market_scenario["Scenario"],
            "Variable adder (EUR/MWh)": market_scenario["Variable adder (EUR/MWh)"],
            **scenario_row.to_dict(),
        })
pd.DataFrame(strategy_b_market_sensitivity_records).to_csv(
    os.path.join(TABLES_DIR, "strategy_b_market_cost_sensitivity.csv"), index=False,
)
strategy_b_reporting_cases.to_csv(
    os.path.join(TABLES_DIR, "strategy_b_reporting_cases.csv"),
    index=False,
)

# Strategy C is deliberately narrower than a full battery investment case. It
# layers residual-curtailment storage onto the economically selected Strategy B
# point at the fixed 20% baseline,
# tests the four sizes used in the budgetary enquiry, and adds one public Huawei
# technical comparator. The central duration is selected by maximum incremental
# NPV within the four non-zero 268 kW cases. When every increment is negative,
# the true economic choice is no BESS and the selected non-zero case is reported
# only as the least-negative technical diagnostic.
(
    strategy_c_bess_physical,
    strategy_c_bess_economics,
    strategy_c_bess_operation_cache,
) = build_strategy_c_bess_sensitivity(
    dam_dispatch_df=dam_dispatch_df,
    strategy_b_operation=selected_strategy_b_op,
    strategy_b_design_row=hybrid_design,
    pem_size_mw=selected_pem_mw,
    pem_capex_eur=pem_capex_eur,
)
strategy_c_bess_physical.to_csv(
    os.path.join(TABLES_DIR, "strategy_c_bess_physical_sensitivity.csv"),
    index=False,
)
strategy_c_bess_economics.to_csv(
    os.path.join(TABLES_DIR, "strategy_c_bess_economic_sensitivity.csv"),
    index=False,
)
strategy_c_central_candidates = strategy_c_bess_economics[
    (strategy_c_bess_economics["Configuration type"] == "268 kW duration sensitivity")
    & np.isclose(
        strategy_c_bess_economics["CAPEX scale factor"],
        BESS_CENTRAL_SMALL_PROJECT_MULTIPLIER,
    )
    & np.isclose(
        strategy_c_bess_economics[
            "Residual-curtailment opportunity cost (EUR/MWh)"
        ],
        BESS_CENTRAL_CURTAILMENT_OPPORTUNITY_COST_EUR_PER_MWH,
    )
].copy()
if strategy_c_central_candidates.empty:
    raise ValueError("No central Strategy C BESS candidates were generated.")
strategy_c_design = strategy_c_central_candidates.loc[
    strategy_c_central_candidates["Incremental NPV vs Strategy B (EUR)"].idxmax()
]
strategy_c_best_nonzero_is_value_adding = bool(
    strategy_c_design["Incremental NPV vs Strategy B (EUR)"] >= 0.0
)
strategy_c_overall_economic_choice = (
    str(strategy_c_design["Configuration"])
    if strategy_c_best_nonzero_is_value_adding
    else "No BESS - retain Strategy B"
)
strategy_c_configuration = str(strategy_c_design["Configuration"])
selected_strategy_c_op = strategy_c_bess_operation_cache[strategy_c_configuration]
strategy_c_bess_capex_eur = float(strategy_c_design["BESS CAPEX (EUR2020)"])
strategy_c_bess_opex_eur = float(
    strategy_c_design["BESS fixed OPEX (EUR2020/year)"]
)
strategy_c_bess_opportunity_cost_eur = float(
    strategy_c_design["Battery opportunity cost (EUR/year)"]
)
strategy_c_total_annual_cost_eur = float(
    hybrid_design["Total Annual Operating + Energy Cost (€/year)"]
    + strategy_c_bess_opex_eur
    + strategy_c_bess_opportunity_cost_eur
)
strategy_c_total_capex_eur = pem_capex_eur + strategy_c_bess_capex_eur
strategy_c_design = strategy_c_design.copy()
strategy_c_design["Overall economic choice"] = (
    str(strategy_c_design["Configuration"])
    if strategy_c_best_nonzero_is_value_adding else "No BESS"
)
strategy_c_design["Value adding"] = (
    "YES" if strategy_c_best_nonzero_is_value_adding else "NO"
)
pd.DataFrame([strategy_c_design]).to_csv(
    os.path.join(TABLES_DIR, "strategy_c_bess_best_nonzero_case.csv"), index=False
)

# Literature-based dynamic-operation diagnostics. Startup durations are bounds
# because the hourly model does not resolve stack temperature or distinguish
# warm from cold starts. No startup-energy penalty is imposed without a
# stack-specific datasheet or measured auxiliary-power trace.
strategy_a_start_bounds = calculate_start_time_bounds(
    selected_nonhybrid_op["starts"]
)
strategy_b_start_bounds = calculate_start_time_bounds(
    selected_strategy_b_op["starts"]
)
strategy_c_start_bounds = calculate_start_time_bounds(
    selected_strategy_c_op["starts"]
)

degradation_screening = pd.concat(
    [
        calculate_degradation_screening(
            "A: Curtailment-only",
            selected_nonhybrid_op["pem_power_w"],
            selected_pem_mw * 1_000_000,
        ),
        calculate_degradation_screening(
            "B: PV-priority + grid-baseline",
            selected_strategy_b_op["pem_power_w"],
            selected_pem_mw * 1_000_000,
            interval_hours=DAM_INTERVAL_HOURS,
            annualization_factor=DAM_ANNUALIZATION_FACTOR,
        ),
        calculate_degradation_screening(
            "C: Strategy B + residual-curtailment BESS",
            selected_strategy_c_op["pem_power_w"],
            selected_pem_mw * 1_000_000,
            interval_hours=DAM_INTERVAL_HOURS,
            annualization_factor=DAM_ANNUALIZATION_FACTOR,
        ),
    ],
    ignore_index=True,
)
degradation_screening.to_csv(
    os.path.join(TABLES_DIR, "literature_degradation_screening.csv"),
    index=False,
)

# Lifecycle-adjusted design-point results. These do not overwrite the original
# one-year dispatch and pre-replacement sensitivities; they provide the missing
# 15-year stack replacement and degraded-output layer for Strategies A, B and C.
strategy_a_lifecycle, strategy_a_annual_lifecycle, strategy_a_replacements = (
    simulate_stack_lifecycle(
        strategy_name="A: Curtailment-only",
        hourly_bol_h2_kg=selected_nonhybrid_op["hourly_h2_kg"],
        pem_power_w=selected_nonhybrid_op["pem_power_w"],
        pem_size_mw=selected_pem_mw,
        annual_operating_cost_eur=(
            annual_opex + selected_opportunity_cost_eur
        ),
        initial_system_capex_eur=pem_capex_eur,
    )
)
strategy_b_lifecycle, strategy_b_annual_lifecycle, strategy_b_replacements = (
    simulate_stack_lifecycle(
        strategy_name="B: PV-priority + grid-baseline",
        hourly_bol_h2_kg=selected_strategy_b_op["hourly_h2_kg"],
        pem_power_w=selected_strategy_b_op["pem_power_w"],
        pem_size_mw=selected_pem_mw,
        annual_operating_cost_eur=hybrid_design[
            "Total Annual Operating + Energy Cost (€/year)"
        ],
        initial_system_capex_eur=pem_capex_eur,
        interval_hours=DAM_INTERVAL_HOURS,
        active_time_scale=DAM_ANNUALIZATION_FACTOR,
    )
)
strategy_c_lifecycle, strategy_c_annual_lifecycle, strategy_c_replacements = (
    simulate_stack_lifecycle(
        strategy_name="C: Strategy B + residual-curtailment BESS",
        hourly_bol_h2_kg=selected_strategy_c_op["hourly_h2_kg"],
        pem_power_w=selected_strategy_c_op["pem_power_w"],
        pem_size_mw=selected_pem_mw,
        annual_operating_cost_eur=strategy_c_total_annual_cost_eur,
        initial_system_capex_eur=strategy_c_total_capex_eur,
        interval_hours=DAM_INTERVAL_HOURS,
        active_time_scale=DAM_ANNUALIZATION_FACTOR,
        degradation_scenario=(
            "DEA/DNV PEM comparator; BESS fade and replacement excluded"
        ),
    )
)
lifecycle_summary = pd.DataFrame([
    strategy_a_lifecycle,
    strategy_b_lifecycle,
    strategy_c_lifecycle,
])
lifecycle_annual = pd.concat(
    [
        strategy_a_annual_lifecycle,
        strategy_b_annual_lifecycle,
        strategy_c_annual_lifecycle,
    ],
    ignore_index=True,
)
lifecycle_replacements = pd.concat(
    [strategy_a_replacements, strategy_b_replacements, strategy_c_replacements],
    ignore_index=True,
)
lifecycle_summary.to_csv(
    os.path.join(TABLES_DIR, "stack_lifecycle_summary.csv"), index=False
)
lifecycle_annual.to_csv(
    os.path.join(TABLES_DIR, "stack_lifecycle_annual.csv"), index=False
)
lifecycle_replacements.to_csv(
    os.path.join(TABLES_DIR, "stack_replacement_events.csv"), index=False
)

# Literature-rate lifecycle sensitivity. Unlike the separate screening table,
# every rate below is passed through the replacement and discounted-cash-flow
# model. The 23 and 50 microV/h cases remain linear stress tests, not validated
# forecasts for the selected MW-scale PEM system.
reference_voltage_by_strategy = (
    degradation_screening.groupby("Strategy")[
        "Load-weighted EES Cell Voltage (V)"
    ].first().to_dict()
)
lifecycle_sensitivity_records = []
for sensitivity_strategy, sensitivity_op, sensitivity_annual_cost, sensitivity_interval, sensitivity_scale in [
    (
        "A: Curtailment-only",
        selected_nonhybrid_op,
        annual_opex + selected_opportunity_cost_eur,
        1.0,
        1.0,
    ),
    (
        "B: PV-priority + grid-baseline",
        selected_strategy_b_op,
        hybrid_design["Total Annual Operating + Energy Cost (€/year)"],
        DAM_INTERVAL_HOURS,
        DAM_ANNUALIZATION_FACTOR,
    ),
    (
        "C: Strategy B + residual-curtailment BESS",
        selected_strategy_c_op,
        strategy_c_total_annual_cost_eur,
        DAM_INTERVAL_HOURS,
        DAM_ANNUALIZATION_FACTOR,
    ),
]:
    sensitivity_reference_voltage = reference_voltage_by_strategy[sensitivity_strategy]
    for sensitivity_scenario, sensitivity_rate in PEM_VOLTAGE_DEGRADATION_SCENARIOS_UV_PER_H.items():
        sensitivity_summary, _, _ = simulate_stack_lifecycle(
            strategy_name=sensitivity_strategy,
            hourly_bol_h2_kg=sensitivity_op["hourly_h2_kg"],
            pem_power_w=sensitivity_op["pem_power_w"],
            pem_size_mw=selected_pem_mw,
            annual_operating_cost_eur=sensitivity_annual_cost,
            initial_system_capex_eur=pem_capex_eur,
            interval_hours=sensitivity_interval,
            active_time_scale=sensitivity_scale,
            degradation_rate_uv_per_h=sensitivity_rate,
            reference_cell_voltage_v=sensitivity_reference_voltage,
            degradation_scenario=sensitivity_scenario,
        )
        is_linear_stress_test = sensitivity_rate >= 23.0
        model_warning = (
            "LINEAR STRESS TEST ONLY - nonlinear or OEM-calibrated PEM model required for final prediction"
            if is_linear_stress_test
            else "LINEAR LITERATURE SCREENING ONLY - nonlinear or OEM-calibrated PEM model required for final prediction"
        )
        if sensitivity_strategy.startswith("C:"):
            model_warning += (
                "; BESS capacity fade, thermal derating and replacement are also unquantified"
            )
        lifecycle_sensitivity_records.append({
            "Strategy": sensitivity_strategy,
            "Degradation scenario": sensitivity_scenario,
            "Degradation rate (microV/h)": sensitivity_rate,
            "Reference cell voltage (V)": sensitivity_reference_voltage,
            "Effective relative SEC rise per 1,000 h (%)": sensitivity_summary[
                "Effective relative SEC rise per 1,000 h (%)"
            ],
            "First EOL time (project years)": sensitivity_summary[
                "First EOL time (project years)"
            ],
            "Replacement count": sensitivity_summary["Replacement count"],
            "Terminal retirement": sensitivity_summary[
                "Terminal retirement instead of end-horizon replacement"
            ],
            "Terminal retirement time (project years)": sensitivity_summary[
                "Terminal retirement project time (years)"
            ],
            "Discounted replacement CAPEX (EUR)": sensitivity_summary[
                "Discounted replacement cost (EUR)"
            ],
            "Discounted hydrogen production (kg)": sensitivity_summary[
                "Discounted degraded H2 (kg)"
            ],
            "Lifecycle LCOH (EUR/kg H2)": sensitivity_summary[
                "Lifecycle-adjusted discounted LCOH (EUR/kg)"
            ],
            "Lifecycle NPV (EUR)": sensitivity_summary[
                "Lifecycle-adjusted NPV (EUR)"
            ],
            "Model validity warning": model_warning,
        })

stack_lifecycle_sensitivity = pd.DataFrame(lifecycle_sensitivity_records)
stack_lifecycle_sensitivity.to_csv(
    os.path.join(TABLES_DIR, "stack_lifecycle_sensitivity.csv"),
    index=False,
)

central_lifecycle_sensitivity = stack_lifecycle_sensitivity[
    stack_lifecycle_sensitivity["Degradation scenario"]
    == PEM_CENTRAL_LITERATURE_SCENARIO
].copy()

lifecycle_report_lines = [
    "STACK LIFECYCLE SENSITIVITY REPORT",
    "",
    "Central literature-screening scenario: 23 microV/h.",
    "The 23 and 50 microV/h scenarios are linear stress tests, not validated lifetime forecasts.",
    "A nonlinear or OEM-calibrated degradation model is required for final prediction.",
    "",
]
for report_strategy in stack_lifecycle_sensitivity["Strategy"].unique():
    report_rows = stack_lifecycle_sensitivity[
        stack_lifecycle_sensitivity["Strategy"] == report_strategy
    ]
    report_central = report_rows[
        report_rows["Degradation scenario"] == PEM_CENTRAL_LITERATURE_SCENARIO
    ].iloc[0]
    lifecycle_report_lines.extend([
        report_strategy,
        (
            f"Central result: LCOH {report_central['Lifecycle LCOH (EUR/kg H2)']:.2f} EUR/kg H2, "
            f"NPV {report_central['Lifecycle NPV (EUR)']/1e6:.2f} MEUR."
        ),
        (
            f"Lifecycle LCOH range: {report_rows['Lifecycle LCOH (EUR/kg H2)'].min():.2f} to "
            f"{report_rows['Lifecycle LCOH (EUR/kg H2)'].max():.2f} EUR/kg H2."
        ),
        "Replacement counts: " + ", ".join(
            f"{row['Degradation rate (microV/h)']:.0f} microV/h = {int(row['Replacement count'])}"
            for _, row in report_rows.iterrows()
        ),
        "",
    ])
with open(
    os.path.join(TABLES_DIR, "stack_lifecycle_sensitivity_report.txt"),
    "w",
    encoding="utf-8",
) as lifecycle_report_file:
    lifecycle_report_file.write("\n".join(lifecycle_report_lines))
hybrid_break_even_h2_price = calculate_break_even_hydrogen_price(
    pem_capex_eur=pem_capex_eur,
    annual_operating_and_energy_cost_eur=hybrid_design["Total Annual Operating + Energy Cost (€/year)"],
    annual_h2_kg=hybrid_design["H2 (kg/year)"],
    discount_rate=discount_rate,
    project_lifetime_years=project_lifetime_years
)
nonhybrid_break_even_h2_price = calculate_break_even_hydrogen_price(
    pem_capex_eur=pem_capex_eur,
    annual_operating_and_energy_cost_eur=annual_opex + selected_opportunity_cost_eur,
    annual_h2_kg=selected_h2_kg,
    discount_rate=discount_rate,
    project_lifetime_years=project_lifetime_years
)

# Compute recovery directly for the selected balanced design because a fine-sweep
# optimum is not necessarily part of the coarse pem_sizes_mw grid.
gross_no_pem_curtailment_w = np.maximum(
    df["P"].to_numpy(dtype=float) - selected_grid_limit_mw * 1e6,
    0.0
)
gross_no_pem_curtailment_mwh = gross_no_pem_curtailment_w.sum() / 1e6
selected_residual_curtailment_mwh = (
    selected_nonhybrid_op["residual_curtailment_w"].sum() / 1e6
)
selected_recovered_curtailment_mwh = max(
    gross_no_pem_curtailment_mwh - selected_residual_curtailment_mwh,
    0.0
)
selected_recovery_pct = (
    selected_recovered_curtailment_mwh / gross_no_pem_curtailment_mwh * 100
    if gross_no_pem_curtailment_mwh > 0 else 0.0
)
full_benchmark_candidates = [
    p for p in full_curtailment_benchmark_range_mw if p in pem_sizes_mw
]
full_benchmark_mw = next(
    (p for p in full_benchmark_candidates
     if curtailment_recovery_results[pem_sizes_mw.index(p)] >= 99.0),
    max(full_benchmark_candidates,
        key=lambda p: curtailment_recovery_results[pem_sizes_mw.index(p)])
)
full_benchmark_recovery_pct = float(
    curtailment_recovery_results[pem_sizes_mw.index(full_benchmark_mw)]
)

print("\n" + "=" * 72)
print("FINAL BASE CASE SUMMARY")
print("=" * 72)
print(f"PV plant capacity:                    10.0 MWp")
print(
    f"Project site:                         {SITE_LATITUDE_DEG:.3f}, "
    f"{SITE_LONGITUDE_DEG:.3f}, {SITE_ELEVATION_M:.0f} m"
)
print(f"Radiation database:                   {PVGIS_RADIATION_DATABASE}")
print(f"Grid export limit:                    {selected_grid_limit_mw:.1f} MW")
print(f"Selected PEM capacity:                {selected_pem_mw:.2f} MW")
print(f"Curtailment recovery:                 {selected_recovery_pct:.1f} %")
print(f"Near-full-curtailment benchmark:      {full_benchmark_mw:.1f} MW ({full_benchmark_recovery_pct:.1f} % recovery)")
print(f"Reference H2 selling price:           {hydrogen_sale_price:.2f} €/kg")
print(f"H2 price sensitivity:                 {hydrogen_price_scenarios} €/kg")
print("-")
print("LITERATURE-BASED DYNAMIC / DEGRADATION SCREENING")
print(
    "Sources: Sayed-Ahmed et al. (2024), DOI 10.1016/j.rser.2023.113883; "
    "Zerrougui et al. (2025), DOI 10.1016/j.ijhydene.2025.152297."
)
print(
    "Startup-duration and voltage-rise values are sensitivity bounds, not "
    "calibrated parameters for this 1.60 MW stack."
)
print(
    "Multi-year values are deliberately uncapped linear extrapolations. "
    "Large values indicate that stack replacement or a nonlinear degradation "
    "model is required; they are not physical end-of-life forecasts."
)
print(degradation_screening.round(3).to_string(index=False))
print("-")
print("STACK LIFECYCLE / REPLACEMENT SCREENING")
print(
    "DEA/DNV current-value inputs: 30,000 stack hours, 0.3333%/1,000 h "
    "steady-state degradation and 530.33 EUR/kW stack-cost proxy."
)
print(
    "Technical EOL comparator: 10% relative voltage/SEC rise. The 20% / "
    "7-year economic result from Arnold et al. is case-specific and is not "
    "used as a universal trigger."
)
print(
    "Replacement resets stack degradation only. BoP state is retained; BoP "
    "ageing is not quantified because no defensible rate is available."
)
print(lifecycle_summary.round(3).to_string(index=False))
print("-")
print("LITERATURE-RATE STACK LIFECYCLE SENSITIVITY")
print(
    stack_lifecycle_sensitivity[
        [
            "Strategy",
            "Degradation scenario",
            "Degradation rate (microV/h)",
            "Effective relative SEC rise per 1,000 h (%)",
            "First EOL time (project years)",
            "Replacement count",
            "Terminal retirement",
            "Lifecycle LCOH (EUR/kg H2)",
            "Lifecycle NPV (EUR)",
        ]
    ].round(3).to_string(index=False)
)
for printed_strategy in stack_lifecycle_sensitivity["Strategy"].unique():
    printed_rows = stack_lifecycle_sensitivity[
        stack_lifecycle_sensitivity["Strategy"] == printed_strategy
    ]
    printed_central = printed_rows[
        printed_rows["Degradation scenario"] == PEM_CENTRAL_LITERATURE_SCENARIO
    ].iloc[0]
    replacement_text = ", ".join(
        f"{row['Degradation rate (microV/h)']:.0f} microV/h: {int(row['Replacement count'])}"
        for _, row in printed_rows.iterrows()
    )
    print(f"{printed_strategy} central literature-screening result:")
    print(
        f"  {printed_central['Degradation rate (microV/h)']:.0f} microV/h, "
        f"lifecycle LCOH {printed_central['Lifecycle LCOH (EUR/kg H2)']:.2f} EUR/kg H2, "
        f"lifecycle NPV {printed_central['Lifecycle NPV (EUR)']/1e6:.2f} MEUR"
    )
    print(
        f"  Lifecycle LCOH sensitivity range: "
        f"{printed_rows['Lifecycle LCOH (EUR/kg H2)'].min():.2f}-"
        f"{printed_rows['Lifecycle LCOH (EUR/kg H2)'].max():.2f} EUR/kg H2"
    )
    print(f"  Replacements by scenario: {replacement_text}")
print(
    "WARNING: The 23 and 50 microV/h cases are linear stress tests, not "
    "validated MW-scale lifetime forecasts. A nonlinear or OEM-calibrated "
    "degradation model is required for a final prediction."
)
print("-")
print("ANNUAL ENERGY-BALANCE AUDIT")
for strategy_name, audited_op in (
    ("A", selected_nonhybrid_op),
    ("B", selected_strategy_b_op),
    ("C", selected_strategy_c_op),
):
    energy_scale = (
        DAM_INTERVAL_HOURS * DAM_ANNUALIZATION_FACTOR
        if strategy_name in {"B", "C"} else 1.0
    )
    battery_charge_mwh = (
        audited_op.get("battery_charge_w", np.zeros(1)).sum()
        * energy_scale / 1_000_000
    )
    battery_discharge_mwh = (
        audited_op.get("battery_to_pem_w", np.zeros(1)).sum()
        * energy_scale / 1_000_000
    )
    pv_balance_error_mwh = (
        audited_op["pv_generation_mwh"]
        - audited_op["pv_export_mwh"]
        - audited_op["pv_to_pem_w"].sum() * energy_scale / 1_000_000
        - battery_charge_mwh
        - audited_op["residual_curtailment_mwh"]
    )
    pem_source_error_mwh = (
        audited_op["pem_power_w"].sum() * energy_scale / 1_000_000
        - audited_op["pv_to_pem_w"].sum() * energy_scale / 1_000_000
        - audited_op.get("purchased_energy_mwh", 0.0)
        - battery_discharge_mwh
    )
    assert abs(pv_balance_error_mwh) <= 1e-8, \
        f"Strategy {strategy_name} annual PV balance failed."
    assert abs(pem_source_error_mwh) <= 1e-8, \
        f"Strategy {strategy_name} annual PEM source balance failed."
    print(
        f"Strategy {strategy_name}: PV error={pv_balance_error_mwh:.3e} MWh, "
        f"PEM-source error={pem_source_error_mwh:.3e} MWh, "
        f"lost export={audited_op['lost_export_mwh']:.3f} MWh, "
        f"BESS charge/discharge={battery_charge_mwh:.3f}/{battery_discharge_mwh:.3f} MWh"
    )
print("-")
strategy_a_lifecycle_sensitivity = stack_lifecycle_sensitivity[
    stack_lifecycle_sensitivity["Strategy"] == "A: Curtailment-only"
]
strategy_b_lifecycle_sensitivity = stack_lifecycle_sensitivity[
    stack_lifecycle_sensitivity["Strategy"] == "B: PV-priority + grid-baseline"
]
strategy_c_lifecycle_sensitivity = stack_lifecycle_sensitivity[
    stack_lifecycle_sensitivity["Strategy"]
    == "C: Strategy B + residual-curtailment BESS"
]
strategy_a_central_lifecycle = strategy_a_lifecycle_sensitivity[
    strategy_a_lifecycle_sensitivity["Degradation scenario"]
    == PEM_CENTRAL_LITERATURE_SCENARIO
].iloc[0]
strategy_b_central_lifecycle = strategy_b_lifecycle_sensitivity[
    strategy_b_lifecycle_sensitivity["Degradation scenario"]
    == PEM_CENTRAL_LITERATURE_SCENARIO
].iloc[0]
strategy_c_central_lifecycle = strategy_c_lifecycle_sensitivity[
    strategy_c_lifecycle_sensitivity["Degradation scenario"]
    == PEM_CENTRAL_LITERATURE_SCENARIO
].iloc[0]
print("STRATEGY A: CURTAILMENT-ONLY DESIGN")
print(f"Annual H2 production:                 {selected_h2_kg:,.0f} kg/year")
print(f"PEM utilization:                      {selected_utilization:.2f} %")
print(f"PEM-on hours:                         {selected_nonhybrid_op['operating_hours']} h/year")
print(f"Equivalent full-load hours:           {selected_nonhybrid_op['full_load_hours']:.1f} h/year")
print(f"Starts / stops:                       {selected_nonhybrid_op['starts']} / {selected_nonhybrid_op['stops']}")
print(
    "Start-time bounds, all warm / all cold: "
    f"<{strategy_a_start_bounds['all_warm_start_upper_bound_h']:.2f} h / "
    f"{strategy_a_start_bounds['all_cold_start_lower_bound_h']:.1f}-"
    f"{strategy_a_start_bounds['all_cold_start_upper_bound_h']:.1f} h/year"
)
print(f"Below-minimum curtailment hours:       {selected_nonhybrid_op['below_minimum_resource_hours']} h/year")
print(f"Discounted LCOH:                      {discounted_lcoh:.2f} €/kg H2")
print(f"DEA/DNV lifecycle comparator LCOH:    {strategy_a_lifecycle['Lifecycle-adjusted discounted LCOH (EUR/kg)']:.2f} €/kg H2")
print(f"Central literature-screening LCOH:    {strategy_a_central_lifecycle['Lifecycle LCOH (EUR/kg H2)']:.2f} €/kg H2 (23 microV/h linear stress test)")
print(f"Lifecycle sensitivity LCOH range:     {strategy_a_lifecycle_sensitivity['Lifecycle LCOH (EUR/kg H2)'].min():.2f}-{strategy_a_lifecycle_sensitivity['Lifecycle LCOH (EUR/kg H2)'].max():.2f} €/kg H2")
print(f"Break-even H2 price:                  {nonhybrid_break_even_h2_price:.2f} €/kg H2")
print(f"NPV @ {hydrogen_sale_price:.2f} €/kg H2:                  {npv/1e6:.2f} M€")
print(f"DEA/DNV lifecycle comparator NPV:     {strategy_a_lifecycle['Lifecycle-adjusted NPV (EUR)']/1e6:.2f} M€")
print(f"Central literature-screening NPV:     {strategy_a_central_lifecycle['Lifecycle NPV (EUR)']/1e6:.2f} M€")
print(
    "Stack replacements, 7/23/50 microV/h: "
    + "/".join(str(int(value)) for value in strategy_a_lifecycle_sensitivity["Replacement count"])
)
print(f"Economic result, central screening:   {'PROFITABLE' if strategy_a_central_lifecycle['Lifecycle NPV (EUR)'] >= 0 else 'NOT PROFITABLE'}")
print("-")
print("STRATEGY B: PV-PRIORITY + GRID-BASELINE ECONOMIC SELECTION")
print(f"PEM capacity:                         {selected_pem_mw:.2f} MW")
print(f"Requested baseline:                   {hybrid_design_baseline_fraction*100:.0f} % ({hybrid_design_baseline_fraction*selected_pem_mw:.2f} MW)")
print("Selection criterion:                  minimum LCOH among tested caps at fixed baseline")
print(f"All-in grid-import price cap:          {hybrid_design_all_in_price_cap_eur_per_mwh:.0f} €/MWh")
print(
    f"NPV-optimal tested cap:                "
    f"{strategy_b_npv_optimum['All-in Import Price Cap (€/MWh)']:.0f} €/MWh"
)
print(
    f"LCOH spread across 0-100 caps:         "
    f"{strategy_b_low_cap_lcoh_spread:.4f} €/kg H2 (economically flat plateau)"
)
print(
    f"Equivalent raw DAM threshold:          "
    f"{hybrid_design_all_in_price_cap_eur_per_mwh - grid_variable_adder_eur_per_mwh:.2f} €/MWh"
)
print(f"Variable grid-price adder:             {grid_variable_adder_eur_per_mwh:.2f} €/MWh (generic central case)")
print(f"Demand charge rate:                    {grid_demand_charge_eur_per_kw_year:.2f} €/kW-year")
print(
    f"Contracted import capacity:             "
    f"{hybrid_design['Contracted Import Capacity (kW)']:.0f} kW"
)
print(f"Fixed supply + metering:               {grid_fixed_supply_metering_eur_per_year:,.0f} €/year")
print("VAT treatment:                         excluded as recoverable input VAT")
print("Take-or-pay minimum:                   none assumed")
print("Half-hourly interruption:              allowed, imbalance exposure retained")
print(f"Purchased weighted electricity price: {hybrid_design['Purchased Weighted Price (€/MWh)']:.2f} €/MWh")
print(f"Observed DAM period:                   359 days, annualized by 365/359")
print(f"Annual H2 production:                 {hybrid_design['H2 (kg/year)']:,.0f} kg/year")
print(f"PEM utilization:                      {hybrid_design['Utilization (%)']:.2f} %")
print(f"PEM-on hours in observed period:      {selected_strategy_b_op['operating_hours']:.1f} h/359 days")
print(f"Equivalent full-load hours:           {selected_strategy_b_op['full_load_hours']:.1f} h/year")
print(f"Starts / stops:                       {selected_strategy_b_op['starts']} / {selected_strategy_b_op['stops']}")
print(
    "Start-time bounds, all warm / all cold: "
    f"<{strategy_b_start_bounds['all_warm_start_upper_bound_h']:.3f} h / "
    f"{strategy_b_start_bounds['all_cold_start_lower_bound_h']:.2f}-"
    f"{strategy_b_start_bounds['all_cold_start_upper_bound_h']:.2f} h/year"
)
print(f"Grid-only hours in observed period:   {selected_strategy_b_op['grid_only_hours']:.1f} h/359 days")
print(f"PV electricity to PEM:                {hybrid_design['PV Energy to PEM (MWh)']:.1f} MWh/year")
print(f"  of which curtailed PV:              {hybrid_design['Curtailed PV to PEM (MWh)']:.1f} MWh/year")
print(f"  of which exportable PV:             {hybrid_design['Exportable PV to PEM (MWh)']:.1f} MWh/year")
print(f"Purchased grid electricity:           {hybrid_design['Purchased Energy (MWh)']:.1f} MWh/year")
print(f"Discounted LCOH:                      {hybrid_design['LCOH (€/kg H2)']:.2f} €/kg H2")
print(f"DEA/DNV lifecycle comparator LCOH:    {strategy_b_lifecycle['Lifecycle-adjusted discounted LCOH (EUR/kg)']:.2f} €/kg H2")
print(f"Central literature-screening LCOH:    {strategy_b_central_lifecycle['Lifecycle LCOH (EUR/kg H2)']:.2f} €/kg H2 (23 microV/h linear stress test)")
print(f"Lifecycle sensitivity LCOH range:     {strategy_b_lifecycle_sensitivity['Lifecycle LCOH (EUR/kg H2)'].min():.2f}-{strategy_b_lifecycle_sensitivity['Lifecycle LCOH (EUR/kg H2)'].max():.2f} €/kg H2")
print(f"Break-even H2 price:                  {hybrid_break_even_h2_price:.2f} €/kg H2")
print(f"NPV @ {hydrogen_sale_price:.2f} €/kg H2:                  {hybrid_design['NPV (€)']/1e6:.2f} M€")
print(f"DEA/DNV lifecycle comparator NPV:     {strategy_b_lifecycle['Lifecycle-adjusted NPV (EUR)']/1e6:.2f} M€")
print(f"Central literature-screening NPV:     {strategy_b_central_lifecycle['Lifecycle NPV (EUR)']/1e6:.2f} M€")
print(
    "Stack replacements, 7/23/50 microV/h: "
    + "/".join(str(int(value)) for value in strategy_b_lifecycle_sensitivity["Replacement count"])
)
print(
    "Model validity warning:              23 and 50 microV/h are linear stress tests. "
    "Final prediction requires a nonlinear or OEM-calibrated model."
)
print(f"Economic result, central screening:   {'PROFITABLE' if strategy_b_central_lifecycle['Lifecycle NPV (EUR)'] >= 0 else 'NOT PROFITABLE'}")
print("-")
print("STRATEGY B ILLUSTRATIVE GRID-ACTIVE COMPARATOR - NOT THE OPTIMUM")
print(
    f"Raised requested baseline:             "
    f"{hybrid_grid_active_comparator_baseline_fraction * 100:.0f}% "
    f"({hybrid_grid_active_comparator_baseline_fraction * selected_pem_mw:.2f} MW)"
)
print(
    f"All-in price cap:                     "
    f"{hybrid_grid_active_comparator_cap_eur_per_mwh:.0f} €/MWh"
)
print(
    f"Purchased grid electricity:           "
    f"{strategy_b_grid_active_comparator['Purchased Energy (MWh)']:.1f} MWh/year"
)
print(
    f"Annual H2 production:                 "
    f"{strategy_b_grid_active_comparator['H2 (kg/year)']:,.0f} kg/year"
)
print(
    f"Discounted LCOH:                      "
    f"{strategy_b_grid_active_comparator['LCOH (€/kg H2)']:.3f} €/kg H2"
)
print(
    f"NPV @ {hydrogen_sale_price:.2f} €/kg H2:                  "
    f"{strategy_b_grid_active_comparator['NPV (€)']/1e6:.3f} M€"
)
print(
    f"LCOH penalty vs economic selection:   "
    f"{strategy_b_grid_active_comparator['LCOH (€/kg H2)'] - hybrid_design['LCOH (€/kg H2)']:+.3f} €/kg H2"
)
print(
    f"NPV change vs economic selection:     "
    f"{(strategy_b_grid_active_comparator['NPV (€)'] - hybrid_design['NPV (€)'])/1000:+,.1f} k€"
)
print(
    f"PV-priority without grid service:      "
    f"LCOH {strategy_b_pv_only_reference['LCOH (€/kg H2)']:.3f} EUR/kg, "
    f"NPV {strategy_b_pv_only_reference['NPV (€)']/1e6:.3f} MEUR"
)
print(
    "The fixed-baseline import-cap optimum is conditional on retaining the "
    "import service. Compare it with the PV-only reference before paying "
    "import-service demand and fixed charges."
)
print("-")
print("STRATEGY C: STRATEGY B + RESIDUAL-CURTAILMENT BESS DIAGNOSTIC")
print(f"Best tested non-zero BESS case:        {strategy_c_configuration}")
print(f"Overall economic choice:               {strategy_c_overall_economic_choice}")
print(f"Battery power / energy:                {strategy_c_design['Power (kW)']:.0f} kW / {strategy_c_design['Energy (kWh)']:.0f} kWh")
print(f"Central CAPEX scale factor:            {BESS_CENTRAL_SMALL_PROJECT_MULTIPLIER:.2f}")
print(f"BESS CAPEX screening value:            {strategy_c_bess_capex_eur:,.0f} EUR2020")
print(f"BESS fixed OPEX:                       {strategy_c_bess_opex_eur:,.0f} EUR2020/year")
print(f"Curtailment opportunity-cost case:     {BESS_CENTRAL_CURTAILMENT_OPPORTUNITY_COST_EUR_PER_MWH:.2f} EUR/MWh")
print(f"Residual curtailment charged:          {selected_strategy_c_op['battery_charge_mwh']:.1f} MWh/year")
print(f"Battery discharge to PEM:              {selected_strategy_c_op['battery_discharge_mwh']:.1f} MWh/year")
print(f"Battery losses:                        {selected_strategy_c_op['battery_loss_mwh']:.1f} MWh/year")
print(f"Equivalent full cycles:                {selected_strategy_c_op['equivalent_full_cycles_per_year']:.1f} cycles/year")
print(f"Implied 5,000-cycle life:              {selected_strategy_c_op['cycle_life_years_at_dispatch']:.1f} years")
print(f"Incremental H2 vs Strategy B:          {selected_strategy_c_op['incremental_h2_kg']:,.0f} kg/year")
print(f"Total Strategy C H2:                   {selected_strategy_c_op['annual_h2_kg']:,.0f} kg/year")
print(f"Strategy C PEM utilization:            {selected_strategy_c_op['utilization_pct']:.2f} %")
print(f"Combined discounted LCOH:              {strategy_c_design['Combined Strategy C discounted LCOH (EUR/kg H2)']:.2f} EUR/kg H2")
print(f"Incremental BESS NPV vs Strategy B:    {strategy_c_design['Incremental NPV vs Strategy B (EUR)']/1e6:.2f} MEUR")
print(f"Combined Strategy C NPV:               {strategy_c_design['Combined Strategy C NPV (EUR)']/1e6:.2f} MEUR")
print(f"DEA/DNV PEM lifecycle comparator LCOH: {strategy_c_lifecycle['Lifecycle-adjusted discounted LCOH (EUR/kg)']:.2f} EUR/kg H2")
print(f"Central literature-screening LCOH:     {strategy_c_central_lifecycle['Lifecycle LCOH (EUR/kg H2)']:.2f} EUR/kg H2")
print(f"Lifecycle sensitivity LCOH range:      {strategy_c_lifecycle_sensitivity['Lifecycle LCOH (EUR/kg H2)'].min():.2f}-{strategy_c_lifecycle_sensitivity['Lifecycle LCOH (EUR/kg H2)'].max():.2f} EUR/kg H2")
print(
    "Stack replacements, 7/23/50 microV/h: "
    + "/".join(str(int(value)) for value in strategy_c_lifecycle_sensitivity["Replacement count"])
)
print(
    "BESS validity warning:               Screening NPV excludes capacity fade, "
    "thermal derating, replacement and a Cyprus EPC quote."
)
print(
    "Economic result, BESS increment:      "
    + ("VALUE-ADDING" if strategy_c_best_nonzero_is_value_adding else "NOT VALUE-ADDING")
)
print("=" * 72)

# =========================================
# FINAL RELEASE TABLE AND REPORT
# =========================================

final_screening_summary = pd.DataFrame([
    {
        "Case": "A: Curtailment-only",
        "H2 (kg/year)": selected_h2_kg,
        "Utilization (%)": selected_nonhybrid_op["utilization_pct"],
        "LCOH (EUR/kg)": discounted_lcoh,
        "NPV (EUR)": npv,
        "Grid import (MWh/year)": 0.0,
    },
    {
        "Case": "B: Conditional economic selection",
        "H2 (kg/year)": hybrid_design["H2 (kg/year)"],
        "Utilization (%)": hybrid_design["Utilization (%)"],
        "LCOH (EUR/kg)": hybrid_design["LCOH (€/kg H2)"],
        "NPV (EUR)": hybrid_design["NPV (€)"],
        "Grid import (MWh/year)": hybrid_design["Purchased Energy (MWh)"],
    },
    {
        "Case": "C: Best non-zero BESS diagnostic",
        "H2 (kg/year)": selected_strategy_c_op["annual_h2_kg"],
        "Utilization (%)": selected_strategy_c_op["utilization_pct"],
        "LCOH (EUR/kg)": strategy_c_design["Combined Strategy C discounted LCOH (EUR/kg H2)"],
        "NPV (EUR)": strategy_c_design["Combined Strategy C NPV (EUR)"],
        "Grid import (MWh/year)": selected_strategy_c_op["purchased_energy_mwh"],
    },
])
final_screening_summary.to_csv(
    os.path.join(TABLES_DIR, "final_screening_summary.csv"), index=False,
)
write_final_screening_report(
    final_screening_summary, strategy_b_reporting_cases, strategy_c_design,
    stack_lifecycle_sensitivity,
)

# =========================================
# PLOT EXECUTION
# =========================================

# Remove stale PNGs from earlier runs so numbering and content match this run.
for filename_in_figures in os.listdir(FIGURES_DIR):
    if filename_in_figures.lower().endswith(".png"):
        os.remove(os.path.join(FIGURES_DIR, filename_in_figures))

# v1.4 PV-source validation figures. These are intentionally distribution/monthly
# comparisons because PVGIS is 2023 while PVsyst uses a TMY weather profile.
plot_pv_source_monthly_comparison(pvgis_df, pvsyst_df)
plot_pv_source_power_duration(pvgis_df, pvsyst_df)

pem_vs_hydrogen(pem_sizes_results, h2_results)
pem_vs_utilization(pem_sizes_results, utilization_results)
plot_multiobjective_pem_sizing(multiobjective_pem_sizing)

plot_annualized_lcoh_vs_pem_size(results_table)

plot_curtailment_recovery_vs_pem_size(pem_sizes_mw, curtailment_recovery_results)
plot_curtailment_diagnostics(curtailment_diagnostics)

capex_cols = ["PEM Size (MW)"] + [col for col in results_table.columns if "CAPEX" in col]
plot_capex_intensity(results_table[capex_cols])

monthly_h2_kg = calculate_monthly_hydrogen(df, selected_pem_mw)
plot_monthly_hydrogen(monthly_h2_kg)

plot_lcoh_vs_grid_limit(grid_limits_mw, lcoh_grid_sensitivity, discounted_lcoh_grid_sensitivity)
#plot_npv_vs_hydrogen_price(hydrogen_price_scenarios, npv_results) #unnecessary, just a straight line
plot_npv_vs_grid_limit(grid_limits_mw, npv_grid_results)
plot_npv_heatmap(npv_df)
# Minimum-load sensitivity plot removed from the main figure set.
# The 5%, 10%, 15% and 20% curves are nearly coincident over the relevant
# PEM-size range, so the figure adds little information. The sensitivity
# calculation is retained in the model for validation / tabular reporting.
# plot_minimum_load_sensitivity(pem_sizes_mw, min_load_sensitivity_results)

# ----- v1.3 Hybrid Operating Strategy plots -----
#plot_h2_vs_baseline_load(hybrid_summary)  #unnecessary, just a straight line
#plot_utilization_vs_baseline_load(hybrid_summary)  #unnecessary, just a straight line
#plot_purchased_electricity_vs_baseline_load(hybrid_summary) #unnecessary ignore
# Display six representative caps. The complete refined sweep remains in CSV.
displayed_strategy_b_caps = [0.0, 75.0, 150.0, 200.0, 250.0, 300.0]
displayed_strategy_b_summary = dam_hybrid_summary[
    dam_hybrid_summary["All-in Import Price Cap (€/MWh)"].isin(displayed_strategy_b_caps)
]
plot_lcoh_vs_baseline_load_multi_price(displayed_strategy_b_summary, reference_lcoh=discounted_lcoh)
plot_npv_vs_baseline_load_multi_price(displayed_strategy_b_summary, reference_npv=npv)
plot_hybrid_strategy_heatmap(
    dam_hybrid_summary, baseline_load_fractions, dam_dispatch_price_caps_eur_per_mwh,
    reference_lcoh=discounted_lcoh
)

# ----- Representative-day dispatch visualizations -----
selected_dispatch_plot_date = resolve_dispatch_plot_date(
    df, selected_grid_limit_mw, dispatch_plot_date
)
print(f"Representative dispatch day: {selected_dispatch_plot_date}")

# Figure 18 is explicitly an illustrative grid-active comparator at 200 EUR/MWh,
# not the economically selected Strategy B point. The economic selection uses
# almost no grid energy, so plotting it would not explain the conditional-grid
# control logic. Select the observed day with the greatest grid-only import in
# the comparator case and label the distinction in the figure title.
grid_only_import_by_date = pd.Series(
    np.where(
        np.asarray(strategy_b_grid_active_op["pv_to_pem_w"], dtype=float) <= 1e-9,
        np.asarray(strategy_b_grid_active_op["purchased_w"], dtype=float),
        0.0,
    ) * DAM_INTERVAL_HOURS / 1_000_000.0,
    index=dam_dispatch_df["Timestamp"],
).groupby(lambda timestamp: timestamp.strftime("%Y-%m-%d")).sum()
selected_dam_dispatch_day_grid_mwh = float(grid_only_import_by_date.max())
if selected_dam_dispatch_day_grid_mwh > 1e-9:
    selected_dam_dispatch_plot_date = str(grid_only_import_by_date.idxmax())
    print(
        f"Strategy B 200 EUR/MWh comparator maximum grid-only day: "
        f"{selected_dam_dispatch_plot_date} "
        f"({selected_dam_dispatch_day_grid_mwh:.3f} MWh observed)"
    )
else:
    # No genuine grid-only operation exists at the configured cap. Use the
    # same month/day as Strategy A instead of manufacturing a misleading case.
    selected_month_day = selected_dispatch_plot_date[5:]
    matching_dates = dam_dispatch_df.loc[
        dam_dispatch_df["Timestamp"].dt.strftime("%m-%d") == selected_month_day,
        "Timestamp",
    ].dt.strftime("%Y-%m-%d").drop_duplicates()
    if matching_dates.empty:
        raise ValueError(
            f"No DAM observation found for representative month/day {selected_month_day}."
        )
    selected_dam_dispatch_plot_date = str(matching_dates.iloc[0])
    print(
        "Strategy B grid-active comparator has no genuine grid-only operation "
        "at the configured "
        f"all-in cap. Figure 18 uses {selected_dam_dispatch_plot_date}."
    )

plot_daily_dispatch(
    df, selected_pem_mw, selected_grid_limit_mw, selected_dispatch_plot_date,
    hybrid_baseline_fraction=dispatch_plot_hybrid_baseline_fraction,
    strategy="nonhybrid"
)
plot_daily_dispatch(
    dam_dispatch_df, selected_pem_mw, selected_grid_limit_mw,
    selected_dam_dispatch_plot_date,
    hybrid_baseline_fraction=hybrid_grid_active_comparator_baseline_fraction,
    strategy="hybrid",
    dam_dispatch_df=dam_dispatch_df,
    dam_operation=strategy_b_grid_active_op,
    dispatch_case_note=(
        f"illustrative {hybrid_grid_active_comparator_baseline_fraction * 100:.0f}% "
        f"baseline, {hybrid_grid_active_comparator_cap_eur_per_mwh:.0f} EUR/MWh cap"
    ),
)
plot_strategy_c_bess_sensitivity(
    strategy_c_bess_physical,
    strategy_c_bess_economics,
)


# =========================================
# FINAL ENGINEERING CONCLUSIONS
# =========================================

print(
    f"Hydrogen production reaches a maximum of {max_h2_kg_year / 1000:.2f} tonnes/year "
    f"at approximately {max_h2_pem_mw:.2f} MW PEM within the investigated sizing range."
)

print(
    "Maximum hydrogen production does not coincide with minimum hydrogen cost."
)

print(
    "Smaller PEM systems can achieve higher utilization and lower simplified "
    "annualized LCOH, while larger systems can absorb more PV and reduce residual "
    "curtailment. The optimum therefore depends on the chosen objective."
)

print(
    "Within the investigated range, the minimum simplified annualized LCOH "
    "occurs at the smallest tested PEM size. This should not be interpreted "
    "as a universal economic optimum because fixed Balance-of-Plant costs "
    "and economies of scale are not represented."
)

print(
    "PEM sizing therefore represents a trade-off between hydrogen output, "
    "curtailment recovery, utilization, and hydrogen production cost."
)

# =========================================
# MODEL LIMITATIONS
# =========================================
#
# PEM hydrogen production is calculated using a load-dependent
# specific electricity consumption curve.
#
# Current limitations:
# - PVGIS uses calendar-year 2023 weather, while the PVsyst reference profile uses
#   PVGIS TMY 5.3; therefore their comparison is not same-weather model validation
# - PVsyst reference case currently excludes near-shading, soiling, ageing,
#   unavailability, auxiliary loads, and explicit MV/HV transformer losses
# - partial-load SEC curve is simplified and literature-based
# - stack degradation/replacement is a literature-bounded design-point
#   screening, not an OEM-warranted life prediction
# - no quantified start-stop, standby or ramp equivalent-life penalty
# - no OEM absolute voltage, crossover, pressure or safety replacement limits
# - no quantified Balance-of-Plant ageing or replacement downtime
# - no hydrogen compression
# - no hydrogen storage
# - auxiliary and Balance-of-Plant electricity are not decomposed separately;
#   they are represented only to the extent included in the system-level SEC map
# - the DAM series covers 359 days and is annualised; it is not a complete
#   multi-year market-price record
# - PV TMY generation and 2025-2026 DAM prices are not time-coincident weather
#   and market observations
# - the supplier/aggregator, imbalance, network and levy adder is a screening
#   assumption rather than a verified contract
# - PV export opportunity cost is represented using the supplied monthly
#   2025-2026 EAC 11-kV proxy rather than the project's actual PPA or market route
#
# Therefore, this is a first-order techno-economic screening model,
# not an investment-grade feasibility study.


# =========================================
# KEY FINDINGS
# =========================================
# Key numerical findings are printed dynamically above because the dispatch
# redesign changes annual H2 production, utilization, LCOH and NPV. Do not
# hard-code old v1.2 values here; use the current run output and saved figures.

# Strategies A, B and C are screening cases. Their economic viability depends
# strongly on grid-purchase price, hydrogen sale price, omitted BoP costs and,
# for Strategy C, project-specific BESS CAPEX and ageing evidence.
#
# /// END ///
