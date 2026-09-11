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

#This project evaluates PV-to-hydrogen operation under two dispatch strategies:
#(1) non-hybrid PV minimum-load operation with curtailment-responsive ramping, and
#(2) hybrid PV-priority operation with grid top-up only to the selected baseline.
#Both strategies use PEM electrolysis and preserve an explicit hourly power balance.

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
#          NON-HYBRID: PV sustains minimum PEM load where possible, then the PEM
#          ramps upward to absorb PV that would otherwise be curtailed.
#          HYBRID: PV has first priority up to PEM rated power; grid electricity
#          supplies only the deficit required to reach the selected baseline.
#          Electricity-price sensitivity is applied only to purchased grid energy.
#          Otherwise-exportable PV diverted to PEM carries a month-specific 2023
#          opportunity cost; curtailed PV carries zero opportunity cost.
#   v1.4 - PVsyst integration and PVGIS-vs-PVsyst validation
#          Adds a selectable PV data source, detailed PVsyst hourly AC output,
#          source-comparison metrics/figures, and preserves the v1.3 PEM/economic model.


# =========================================
# IMPORTS
# =========================================

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
#
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FIGURES_DIR = os.path.join(BASE_DIR, "figures")
os.makedirs(FIGURES_DIR, exist_ok=True)
#

# =========================================
# CONFIGURATION & ASSUMPTIONS
# =========================================

# PV data sources
PVGIS_FILENAME = os.path.join(
    BASE_DIR,
    "Timeseries_35.141_33.415_SA3_10000kWp_crystSi_14_28deg_0deg_2023_2023.csv"
)
PVSYST_FILENAME = os.path.join(
    BASE_DIR,
    "PV_Hydrogen_Cyprus_10MWp_Project_VC0_HourlyRes_0.CSV"
)

# v1.4 default: use PVsyst as the active production profile for the full
# downstream PEM/curtailment/economic analysis. Set the environment variable
# PV_DATA_SOURCE=PVGIS to reproduce the v1.3 PV source with the same code.
PV_DATA_SOURCE = os.getenv("PV_DATA_SOURCE", "PVSYST").strip().upper()
if PV_DATA_SOURCE not in {"PVGIS", "PVSYST"}:
    raise ValueError("PV_DATA_SOURCE must be either 'PVGIS' or 'PVSYST'.")
#
#kwh_per_kg_h2 = 52
water_liters_per_kg_h2 = 9
# Historical 2023 EAC RES purchase-price proxy used to value electricity
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
PV_EXPORT_PRICE_2023_EUR_PER_MWH = {
    1: 235.65,   # January
    2: 220.20,   # February
    3: 225.16,   # March
    4: 210.33,   # April
    5: 215.27,   # May
    6: 202.39,   # June
    7: 107.20,   # July
    8: 106.82,   # August
    9: 106.82,   # September
    10: 106.82,  # October
    11: 106.82,  # November
    12: 106.82,  # December
}

# Legacy compatibility scalar only. Current calculations use the
# monthly series above.
pv_export_price_eur_per_mwh = float(
    sum(PV_EXPORT_PRICE_2023_EUR_PER_MWH.values())
    / len(PV_EXPORT_PRICE_2023_EUR_PER_MWH)
)

reference_european_green_h2_price_eur_per_kg = 7.0
hydrogen_sale_price = reference_european_green_h2_price_eur_per_kg
project_lifetime_years = 15
grid_limits_mw = [8, 7.5, 7, 6.5, 6, 5.5, 5, 4.5, 4, 3.5, 3, 2.5, 2]
###
# PEM economic assumptions
pem_capex_per_kw = 1000
pem_opex_fraction = 0.03

# Literature benchmark, European Hydrogen Observatory 2024
eho_pem_capex_per_kw = 1970
eho_pem_opex_per_kw_year = 64
pem_opex_scenarios_per_kw_year = [30, 50, 64]
#The base-case PEM CAPEX is assumed at €1,000/kW, while sensitivity analysis includes the €1,970/kW European benchmark reported by the European Hydrogen Observatory.

# CAPEX sensitivity scenarios
pem_capex_scenarios = [700, 1000, 1300, 1970]
###

pem_sizes_mw = [
    0.1, 0.2, 0.3, 0.4, 0.5,
    0.6, 0.8, 1.0, 1.2, 1.5,
    2.0, 2.5, 3.0, 4.0, 5.0
]

hydrogen_price_scenarios = [2, 3, 4, 6, 8, 10]
battery_round_trip_efficiency = 0.90
h2_lower_heating_value_kwh_per_kg = 33.33
discount_rate = 0.08
###

# =========================================
# v1.3 CONFIGURATION: HYBRID OPERATING STRATEGY SENSITIVITY
# =========================================
#
# Roadmap: v1.2 (PEM sizing + minimum load) -> v1.3 (operating strategy) -> v1.4 (PVsyst integration / PV-source validation)
#
# v1.3 tests a HYBRID PV-priority strategy. Available PV is offered to the
# PEM first. Grid electricity only fills a deficit required to reach the
# requested baseline and never displaces available PV. A 0% baseline is
# therefore PV-only hybrid operation, not the non-hybrid reference case.

# Baseline load as a fraction of PEM rated capacity.
baseline_load_fractions = [0.0, 0.20, 0.30, 0.40,
    0.50, 0.60, 0.80, 1.00]

# Price paid for grid electricity purchased to sustain the baseline load, €/MWh.
# Curtailed PV energy remains free in all scenarios; only the purchased
# top-up electricity is costed at these prices.
electricity_price_scenarios_eur_per_mwh = [    0, 25, 50, 75, 100, 150, 200, 250, 270, 300]
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

# Selected design points for reporting / plots
legacy_selected_pem_mw = 1.5
selected_pem_mw = legacy_selected_pem_mw
full_curtailment_benchmark_range_mw = (2.0, 2.5)

# v1.4 multi-objective PEM sizing configuration.
# The final recommendation is not a universal optimum: it is the equal-weight
# normalized ideal-point compromise across curtailment recovery (maximize),
# discounted LCOH (minimize), and NPV at the reference H2 price (maximize).
MULTIOBJECTIVE_PEM_MIN_MW = 0.10
MULTIOBJECTIVE_PEM_MAX_MW = 3.00
MULTIOBJECTIVE_PEM_STEP_MW = 0.01
MULTIOBJECTIVE_WEIGHTS = {
    "recovery": 1.0 / 3.0,
    "lcoh": 1.0 / 3.0,
    "npv": 1.0 / 3.0,
}
FULL_RECOVERY_THRESHOLD_PCT = 99.9

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
    downstream monthly 2023 opportunity-cost accounting, the month/day/hour are
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


def calculate_2023_pv_opportunity_cost(df, lost_export_w):
    """
    Calculate annual PV-export opportunity cost using the actual month
    of each hourly PV timestep and the 2023 EAC 11-kV RES purchase-price
    proxy.

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
        PV_EXPORT_PRICE_2023_EUR_PER_MWH
    ).to_numpy(dtype=float)

    assert not np.isnan(price_eur_per_mwh).any(), \
        "Missing 2023 monthly PV export opportunity-cost price."

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
        PV_EXPORT_PRICE_2023_EUR_PER_MWH
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
    Non-hybrid PV-only PEM dispatch.

    Strategy:
      1. If PV can sustain the PEM physical minimum load, PV supplies that minimum.
      2. Remaining PV is offered to the grid up to the export limit.
      3. PV that would otherwise be curtailed is diverted to the PEM, causing the
         PEM to ramp above minimum load, up to rated capacity.
      4. No grid electricity is purchased.

    This is deliberately different from a pure curtailment-only electrolyzer.
    """
    if min_load_fraction is None:
        min_load_fraction = MIN_PEM_LOAD_FRACTION

    pv_w = df["P"].to_numpy(dtype=float)
    pem_capacity_w = pem_size_mw * 1_000_000
    grid_limit_w = grid_limit_mw * 1_000_000
    min_power_w = min_load_fraction * pem_capacity_w

    # PV supports minimum PEM load only when the physical minimum can be met.
    baseline_pv_to_pem_w = np.where(
        pv_w >= min_power_w,
        np.minimum(min_power_w, pem_capacity_w),
        0.0
    )

    remaining_after_baseline_w = np.maximum(pv_w - baseline_pv_to_pem_w, 0.0)

    # This is the PV that would be curtailed after minimum-load PEM consumption.
    potential_curtailment_w = np.maximum(
        remaining_after_baseline_w - grid_limit_w,
        0.0
    )

    curtailment_boost_w = np.minimum(
        potential_curtailment_w,
        np.maximum(pem_capacity_w - baseline_pv_to_pem_w, 0.0)
    )

    pv_to_pem_w = baseline_pv_to_pem_w + curtailment_boost_w
    pem_power_w = pv_to_pem_w.copy()

    pv_remaining_w = np.maximum(pv_w - pv_to_pem_w, 0.0)
    pv_export_w = np.minimum(pv_remaining_w, grid_limit_w)
    residual_curtailment_w = np.maximum(pv_remaining_w - grid_limit_w, 0.0)

    active = pem_power_w >= min_power_w - 1e-9
    pem_power_w = np.where(active, pem_power_w, 0.0)
    pv_to_pem_w = np.where(active, pv_to_pem_w, 0.0)

    # Recompute remaining flows after physical minimum-load enforcement.
    pv_remaining_w = np.maximum(pv_w - pv_to_pem_w, 0.0)
    pv_export_w = np.minimum(pv_remaining_w, grid_limit_w)
    residual_curtailment_w = np.maximum(pv_remaining_w - grid_limit_w, 0.0)

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
        "pv_to_pem_w": pv_to_pem_w,
        "baseline_pv_to_pem_w": baseline_pv_to_pem_w,
        "curtailment_boost_w": curtailment_boost_w,
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
        "full_load_hours": pem_power_w.sum() / pem_capacity_w,
    }


def analyze_pem_size(df, pem_size_mw, pem_capex_per_kw):
    """Evaluate PEM sizing under the non-hybrid PV minimum-load + curtailment-ramp strategy."""
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
    """Selected non-hybrid base case, using PV minimum-load operation plus curtailment ramping."""
    op = simulate_nonhybrid_operation(df, selected_pem_mw, selected_grid_limit_mw)
    return pd.Series(op["pem_power_w"], index=df.index), op["annual_h2_kg"]


# =========================================
# v1.3 HYBRID OPERATING STRATEGY FUNCTIONS
# =========================================

def simulate_hybrid_operation(df, pem_size_mw, baseline_load_fraction, grid_limit_mw=None):
    """
    Hybrid PV-priority PEM dispatch.

    Dispatch priority:
      1. All available PV is offered to the PEM first, up to PEM rated power.
      2. Grid electricity supplies only the deficit needed to reach the selected
         baseline operating level.
      3. Grid electricity never displaces available PV and never pushes the PEM
         above the selected baseline by itself.
      4. PV remaining after PEM consumption is exported up to the grid limit;
         any further PV is residual curtailment.

    baseline_load_fraction = 0 means PV-only hybrid operation, not the old
    curtailment-only v1.2 strategy.
    """
    if grid_limit_mw is None:
        grid_limit_mw = selected_grid_limit_mw

    pv_w = df["P"].to_numpy(dtype=float)
    pem_capacity_w = pem_size_mw * 1_000_000
    grid_limit_w = grid_limit_mw * 1_000_000
    min_pem_power_w = MIN_PEM_LOAD_FRACTION * pem_capacity_w
    baseline_power_w = baseline_load_fraction * pem_capacity_w

    # PV has absolute priority to the PEM.
    pv_to_pem_w = np.minimum(pv_w, pem_capacity_w)

    # Grid can only fill a deficit to the requested baseline. A requested
    # baseline below the physical PEM minimum does not trigger grid operation.
    effective_grid_baseline_w = (
        baseline_power_w if baseline_power_w >= min_pem_power_w else 0.0
    )
    grid_to_pem_w = np.maximum(effective_grid_baseline_w - pv_to_pem_w, 0.0)
    grid_to_pem_w = np.minimum(
        grid_to_pem_w,
        np.maximum(pem_capacity_w - pv_to_pem_w, 0.0)
    )

    target_pem_power_w = pv_to_pem_w + grid_to_pem_w

    # If neither PV nor grid support the physical minimum, PEM is off.
    active = target_pem_power_w >= min_pem_power_w - 1e-9
    actual_power_used_w = np.where(active, target_pem_power_w, 0.0)
    pv_to_pem_w = np.where(active, pv_to_pem_w, 0.0)
    grid_to_pem_w = np.where(active, grid_to_pem_w, 0.0)

    pv_remaining_w = np.maximum(pv_w - pv_to_pem_w, 0.0)
    pv_export_w = np.minimum(pv_remaining_w, grid_limit_w)
    residual_curtailment_w = np.maximum(pv_remaining_w - grid_limit_w, 0.0)

    # Counterfactual curtailment if no PEM consumed PV, useful for plotting
    # and quantifying curtailment avoided by the electrolyzer.
    gross_curtailment_without_pem_w = np.maximum(pv_w - grid_limit_w, 0.0)

    # Counterfactual export without the electrolyser. Only PV that would
    # otherwise have been exported carries an opportunity cost.
    export_without_pem_w = np.minimum(pv_w, grid_limit_w)
    lost_export_w = np.maximum(export_without_pem_w - pv_export_w, 0.0)

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
        "annual_h2_kg": annual_h2_kg,
        "hourly_h2_kg": hourly_h2_kg,
        "operating_hours": int(active.sum()),
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
            opportunity_cost_eur, _ = calculate_2023_pv_opportunity_cost(
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
                "Full-Load Hours (h)": op["full_load_hours"],
                "PV Energy to PEM (MWh)": op["pv_energy_to_pem_mwh"],
                "Purchased Energy (MWh)": op["purchased_energy_mwh"],
                "Total PEM Energy (MWh)": op["total_pem_energy_mwh"],
                "Share PEM Energy from PV (%)": op["share_pem_energy_from_pv_pct"],
                "Residual Curtailment (MWh)": op["residual_curtailment_mwh"],
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

    2. NO STACK REPLACEMENT:
       PEM stacks require replacement at approximately year 7-10,
       typically at 30-50% of initial CAPEX.
       Assuming replacement at year 8, cost = 40% of CAPEX:
         1 MW PEM @ 1000 €/kW: replacement cost = 400,000 €
         Undiscounted impact: +400,000 / (15,141 x 15) ~ +1.76 €/kg
         Discounted impact (@8%, year 8):              ~ +1.09 €/kg

    3. NO DEGRADATION:
       Constant annual H2 production assumed across all 15 years.
       Real PEM stacks lose ~0.5-1% efficiency per year,
       reducing H2 output over time and increasing effective LCOH.

    COMBINED LOWER-BOUND BIAS:
       This function likely understates true LCOH by 3-5 €/kg
       under base-case assumptions.
       Use as lower-bound screening estimate only,
       not for investment decisions.
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
        opportunity_cost_eur, _ = calculate_2023_pv_opportunity_cost(
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
        opportunity_cost_eur, _ = calculate_2023_pv_opportunity_cost(
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
        opportunity_cost_eur, _ = calculate_2023_pv_opportunity_cost(
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
        opportunity_cost_eur, _ = calculate_2023_pv_opportunity_cost(
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


###


#
# =========================================
# EXERGY ANALYSIS FUNCTIONS (load-weighted)
# =========================================

EX_H2_KWH_PER_KG = 32.56  # kWh/kg, standard chemical exergy of H2(g) (Szargut et al., 1988)



# =========================================
# PEM PART-LOAD PERFORMANCE
# =========================================

MIN_PEM_LOAD_FRACTION = 0.15

# Set of minimum-load fractions tested in the sensitivity study
# (minimum_load_sensitivity / plot_minimum_load_sensitivity).
# 0.15 is included so the sensitivity study can be directly compared
# against the base-case value used everywhere else in the model.
MIN_LOAD_SENSITIVITY = (0.05, 0.10, 0.15, 0.20)

# Representative gross PEM specific-consumption curve
# estimated from experimental data in:
# Crespi et al. (2023), Fig. 7(c).
#
# IMPORTANT:
# These values represent gross electrolyzer/stack performance,
# not full-system consumption including all Balance-of-Plant loads.

CRESPI_LOAD_FRACTIONS = np.array([
    0.15,
    0.25,
    0.40,
    0.60,
    0.80,
    1.00
])

CRESPI_GROSS_SEC_KWH_PER_KG = np.array([
    46.0,
    46.5,
    48.0,
    50.5,
    53.0,
    55.5
])


def pem_specific_consumption(load_fraction):
    """
    Return gross PEM specific electricity consumption [kWh/kg H2]
    as a function of electrolyzer load fraction.

    The empirical curve is used only within the approximately
    15-100% operating range represented by Crespi et al. (2023).

    Values below 15% load are returned as NaN because the PEM
    is assumed not to operate below the empirical minimum-load range.
    """

    load_fraction = np.asarray(load_fraction, dtype=float)

    sec = np.full(load_fraction.shape, np.nan)

    active = load_fraction >= MIN_PEM_LOAD_FRACTION

    sec[active] = np.interp(
        np.clip(load_fraction[active], MIN_PEM_LOAD_FRACTION, 1.0),
        CRESPI_LOAD_FRACTIONS,
        CRESPI_GROSS_SEC_KWH_PER_KG
    )

    return sec


def calculate_hydrogen_from_pem_input(pem_input_w, pem_size_w):
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

    spec_consumption = pem_specific_consumption(load_fraction)

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
# MULTI-OBJECTIVE PEM SIZING FUNCTIONS
# =========================================

def build_multiobjective_pem_sizing(
    df,
    grid_limit_mw,
    pem_min_mw=MULTIOBJECTIVE_PEM_MIN_MW,
    pem_max_mw=MULTIOBJECTIVE_PEM_MAX_MW,
    pem_step_mw=MULTIOBJECTIVE_PEM_STEP_MW,
    weights=MULTIOBJECTIVE_WEIGHTS,
):
    """
    Fine-grid PEM sizing across three competing objectives:
      - maximize gross-curtailment recovery,
      - minimize discounted LCOH,
      - maximize NPV at the reference H2 selling price.

    The Pareto frontier is identified without assigning weights. A single
    'balanced compromise' is then selected transparently using equal-weight
    normalized distance to the ideal point. Changing weights changes this
    recommendation, so it must not be interpreted as a unique physical optimum.
    """
    sizes = np.round(
        np.arange(pem_min_mw, pem_max_mw + pem_step_mw / 2, pem_step_mw), 2
    )
    gross_curt_w = np.maximum(
        df["P"].to_numpy(dtype=float) - grid_limit_mw * 1e6, 0.0
    )
    gross_curt_mwh = gross_curt_w.sum() / 1e6
    af = sum(1.0 / ((1.0 + discount_rate) ** y) for y in range(1, project_lifetime_years + 1))

    rows = []
    for pem_mw in sizes:
        op = simulate_nonhybrid_operation(df, float(pem_mw), grid_limit_mw)
        recovered_mwh = max(gross_curt_mwh - op["residual_curtailment_w"].sum() / 1e6, 0.0)
        recovery_pct = 100.0 * recovered_mwh / gross_curt_mwh if gross_curt_mwh > 0 else 0.0

        capex = float(pem_mw) * 1000.0 * pem_capex_per_kw
        fixed_opex = capex * pem_opex_fraction
        opportunity_cost, _ = calculate_2023_pv_opportunity_cost(df, op["lost_export_w"])
        annual_cost = fixed_opex + opportunity_cost
        lcoh = calculate_discounted_lcoh(
            capex, annual_cost, op["annual_h2_kg"], discount_rate, project_lifetime_years
        )
        annual_cashflow = op["annual_h2_kg"] * hydrogen_sale_price - annual_cost
        npv_value = -capex + annual_cashflow * af

        rows.append({
            "PEM Size (MW)": float(pem_mw),
            "Curtailment Recovery (%)": recovery_pct,
            "Residual Curtailment (MWh/year)": op["residual_curtailment_w"].sum() / 1e6,
            "H2 (kg/year)": op["annual_h2_kg"],
            "Utilization (%)": op["utilization_pct"],
            "Discounted LCOH (EUR/kg H2)": lcoh,
            "NPV (EUR)": npv_value,
            "PV Opportunity Cost (EUR/year)": opportunity_cost,
        })

    table = pd.DataFrame(rows)

    # Pareto efficiency: a point is dominated only if another point is at least
    # as good in all three objectives and strictly better in at least one.
    rec = table["Curtailment Recovery (%)"].to_numpy()
    lcoh = table["Discounted LCOH (EUR/kg H2)"].to_numpy()
    npv_values = table["NPV (EUR)"].to_numpy()
    pareto = np.ones(len(table), dtype=bool)
    for i in range(len(table)):
        dominates_i = (
            (rec >= rec[i] - 1e-12)
            & (lcoh <= lcoh[i] + 1e-12)
            & (npv_values >= npv_values[i] - 1e-9)
            & (
                (rec > rec[i] + 1e-12)
                | (lcoh < lcoh[i] - 1e-12)
                | (npv_values > npv_values[i] + 1e-9)
            )
        )
        dominates_i[i] = False
        if dominates_i.any():
            pareto[i] = False
    table["Pareto Efficient"] = pareto

    # Normalize each objective to [0, 1], where 1 is the ideal direction.
    def benefit_normalize(values):
        values = np.asarray(values, dtype=float)
        span = values.max() - values.min()
        return np.ones_like(values) if span <= 0 else (values - values.min()) / span

    def cost_normalize(values):
        values = np.asarray(values, dtype=float)
        span = values.max() - values.min()
        return np.ones_like(values) if span <= 0 else (values.max() - values) / span

    rec_score = benefit_normalize(rec)
    lcoh_score = cost_normalize(lcoh)
    npv_score = benefit_normalize(npv_values)

    w_rec = float(weights["recovery"])
    w_lcoh = float(weights["lcoh"])
    w_npv = float(weights["npv"])
    w_sum = w_rec + w_lcoh + w_npv
    w_rec, w_lcoh, w_npv = w_rec / w_sum, w_lcoh / w_sum, w_npv / w_sum

    distance = np.sqrt(
        w_rec * (1.0 - rec_score) ** 2
        + w_lcoh * (1.0 - lcoh_score) ** 2
        + w_npv * (1.0 - npv_score) ** 2
    )
    table["Ideal-Point Distance"] = distance

    # Select from the Pareto frontier only.
    pareto_table = table.loc[table["Pareto Efficient"]].copy()
    recommended_idx = pareto_table["Ideal-Point Distance"].idxmin()
    recommended = table.loc[recommended_idx].copy()

    full_recovery_rows = table[
        table["Curtailment Recovery (%)"] >= FULL_RECOVERY_THRESHOLD_PCT
    ]
    full_recovery = (
        full_recovery_rows.iloc[0].copy() if not full_recovery_rows.empty else table.iloc[-1].copy()
    )

    return table, pareto_table, recommended, full_recovery


def plot_multiobjective_pem_sizing(multiobjective_table, recommended):
    """Recovery-LCOH trade-off with NPV encoded by marker colour."""
    fig_number, fig_title = next_fig("Multi-Objective PEM Sizing: Recovery vs LCOH vs NPV")
    fig, ax = plt.subplots(figsize=(10, 6))

    sc = ax.scatter(
        multiobjective_table["Curtailment Recovery (%)"],
        multiobjective_table["Discounted LCOH (EUR/kg H2)"],
        c=multiobjective_table["NPV (EUR)"] / 1e6,
        s=24,
        alpha=0.65,
    )

    pareto = multiobjective_table[multiobjective_table["Pareto Efficient"]].sort_values(
        "Curtailment Recovery (%)"
    )
    ax.plot(
        pareto["Curtailment Recovery (%)"],
        pareto["Discounted LCOH (EUR/kg H2)"],
        linewidth=1.5,
        label="Pareto frontier",
    )

    ax.scatter(
        [recommended["Curtailment Recovery (%)"]],
        [recommended["Discounted LCOH (EUR/kg H2)"]],
        s=130,
        marker="*",
        zorder=5,
        label="Balanced compromise",
    )
    ax.annotate(
        f"{recommended['PEM Size (MW)']:.2f} MW\n"
        f"Recovery {recommended['Curtailment Recovery (%)']:.1f}%\n"
        f"LCOH {recommended['Discounted LCOH (EUR/kg H2)']:.2f} €/kg\n"
        f"NPV {recommended['NPV (EUR)']/1e6:.2f} M€",
        xy=(recommended["Curtailment Recovery (%)"], recommended["Discounted LCOH (EUR/kg H2)"]),
        xytext=(12, 18), textcoords="offset points",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.85),
        arrowprops=dict(arrowstyle="->"),
        fontsize=9,
    )

    cbar = fig.colorbar(sc, ax=ax)
    cbar.set_label("NPV (M€)")
    ax.set_xlabel("Curtailment Recovery (%)")
    ax.set_ylabel("Discounted LCOH (€/kg H2)")
    ax.set_title(fig_title)
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_multiobjective_pem_sizing.png"),
        dpi=300, bbox_inches="tight"
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
    plt.savefig(os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_hourly_pv_output.png"), dpi=300, bbox_inches="tight")
    plt.show()

def plot_two_day_pv_output(df):
    fig_number, fig_title = next_fig("2-Day PV Output")
    plt.figure(figsize=(12, 5))
    plt.plot(df["P"][0:48])
    plt.title(fig_title)
    plt.xlabel("Hour")
    plt.ylabel("PV Power (W)")
    plt.savefig(os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_two_day_pv_output.png"), dpi=300, bbox_inches="tight")
    plt.show()

def pem_vs_hydrogen(pem_sizes_results, h2_results):
    fig_number, fig_title = next_fig("PEM Size vs Hydrogen Production")
    plt.figure(figsize=(8, 5))
    plt.plot(pem_sizes_results, h2_results, marker="o")
    plt.title(fig_title)
    plt.xlabel("PEM Size (MW)")
    plt.ylabel("Hydrogen Production (kg/year)")
    plt.grid()
    plt.savefig(os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_pem_vs_h2.png"), dpi=300, bbox_inches="tight")
    plt.show()

def pem_vs_utilization(pem_sizes_results, utilization_results):
    fig_number, fig_title = next_fig("PEM Size vs Utilization")
    plt.figure(figsize=(8, 5))
    plt.plot(pem_sizes_results, utilization_results, marker="o")
    plt.title(fig_title)
    plt.xlabel("PEM Size (MW)")
    plt.ylabel("Utilization (%)")
    plt.grid()
    plt.savefig(os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_pem_vs_utilization.png"), dpi=300, bbox_inches="tight")
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
            annual_opportunity_cost, _ = calculate_2023_pv_opportunity_cost(
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

    plt.savefig(
        os.path.join(
            FIGURES_DIR,
            f"figure{fig_number:02d}_annualized_lcoh_vs_pem_size.png"
        ),
        dpi=300,
        bbox_inches="tight"
    )

    plt.show()


###
def plot_curtailment_recovery_vs_pem_size(
    pem_sizes_mw,
    curtailment_recovery_results,
    selected_design_mw=None,
    selected_design_recovery=None,
    full_recovery_mw=None,
    full_recovery_pct=None,
):
    """Plot recovery curve and mark the selected and fine-sweep recovery points."""
    fig_number, fig_title = next_fig("Curtailment Recovery vs PEM Size")
    fig, ax = plt.subplots(figsize=(10, 6))

    x = np.asarray(pem_sizes_mw, dtype=float)
    y = np.asarray(curtailment_recovery_results, dtype=float)
    ax.plot(x, y, marker="o", linewidth=1.8, label="Curtailment recovery")

    if selected_design_mw is not None and selected_design_recovery is not None:
        ax.scatter([selected_design_mw], [selected_design_recovery], s=90, zorder=5)
        ax.annotate(
            f"Multi-objective design\n{selected_design_mw:.2f} MW, "
            f"{selected_design_recovery:.1f}% recovery",
            xy=(selected_design_mw, selected_design_recovery),
            xytext=(selected_design_mw + 0.35, max(selected_design_recovery - 14, 5)),
            arrowprops=dict(arrowstyle="->"), fontsize=9,
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.85)
        )

    if full_recovery_mw is not None and full_recovery_pct is not None:
        ax.scatter([full_recovery_mw], [full_recovery_pct], s=90, zorder=5)
        ax.annotate(
            f"First fine-sweep size reaching ≥{FULL_RECOVERY_THRESHOLD_PCT:.1f}%\n"
            f"{full_recovery_mw:.2f} MW, {full_recovery_pct:.1f}% recovery",
            xy=(full_recovery_mw, full_recovery_pct),
            xytext=(full_recovery_mw + 0.55, max(full_recovery_pct - 22, 5)),
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
    fig.savefig(
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
    plt.savefig(
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
    plt.savefig(os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_capex_intensity.png"), dpi=300, bbox_inches="tight")
    plt.show()

def plot_monthly_hydrogen(monthly_h2_kg):
    fig_number, fig_title = next_fig("Monthly Hydrogen Production")
    print(monthly_h2_kg.to_string())
    plt.figure(figsize=(10, 5))
    plt.plot(monthly_h2_kg.index, monthly_h2_kg.values, marker="o")
    plt.title(fig_title)
    plt.xlabel("Month")
    plt.ylabel("Hydrogen Production (kg)")
    plt.grid()
    plt.savefig(os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_monthly_hydrogen.png"), dpi=300, bbox_inches="tight")
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
    plt.savefig(os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_lcoh_vs_grid_limit.png"), dpi=300, bbox_inches="tight")
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
    plt.savefig(os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_npv_vs_h2_price.png"), dpi=300, bbox_inches="tight")
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
    plt.savefig(os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_npv_vs_grid_limit.png"), dpi=300, bbox_inches="tight")
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
    plt.savefig(os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_npv_heatmap.png"), dpi=300, bbox_inches="tight")
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
    plt.savefig(
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
    plt.savefig(
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
    plt.savefig(
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
    plt.savefig(
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
    fig_number, fig_title = next_fig("LCOH vs Baseline Load (by Electricity Price)")
    plt.figure(figsize=(10, 6))

    for price in sorted(hybrid_summary["Electricity Price (€/MWh)"].unique()):
        subset = hybrid_summary[
            hybrid_summary["Electricity Price (€/MWh)"] == price
        ].sort_values("Baseline Load (%)")
        plt.plot(
            subset["Baseline Load (%)"],
            subset["LCOH (€/kg H2)"],
            marker="o",
            label=f"{price} €/MWh"
        )

    if reference_lcoh is not None:
        plt.axhline(
            y=reference_lcoh,
            linestyle="--",
            color="black",
            label=f"non-hybrid base case ({reference_lcoh:.2f} €/kg)"
        )

    plt.xlabel("Baseline Load (% of PEM capacity)")
    plt.ylabel("Discounted LCOH (€/kg H2)")
    plt.title(fig_title)
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_lcoh_vs_baseline_load.png"),
        dpi=300,
        bbox_inches="tight"
    )
    plt.show()


def plot_npv_vs_baseline_load_multi_price(hybrid_summary, reference_npv=None):
    fig_number, fig_title = next_fig("NPV vs Baseline Load (by Electricity Price)")
    plt.figure(figsize=(10, 6))

    for price in sorted(hybrid_summary["Electricity Price (€/MWh)"].unique()):
        subset = hybrid_summary[
            hybrid_summary["Electricity Price (€/MWh)"] == price
        ].sort_values("Baseline Load (%)")
        plt.plot(
            subset["Baseline Load (%)"],
            subset["NPV (€)"] / 1_000_000,
            marker="o",
            label=f"{price} €/MWh"
        )

    plt.axhline(y=0, linestyle=":", color="gray", label="NPV = 0")

    if reference_npv is not None:
        plt.axhline(
            y=reference_npv / 1_000_000,
            linestyle="--",
            color="black",
            label=f"Non-hybrid NPV = {reference_npv/1_000_000:+.2f} M€"
        )

    plt.xlabel("Baseline Load (% of PEM capacity)")
    plt.ylabel("NPV (M€)")
    plt.title(fig_title)
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(
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
    fig_number, fig_title = next_fig("Hybrid Strategy LCOH Heatmap")

    pivot = hybrid_summary.pivot(
        index="Baseline Load (%)",
        columns="Electricity Price (€/MWh)",
        values="LCOH (€/kg H2)"
    ).reindex(
        index=[b * 100 for b in baseline_load_fractions],
        columns=electricity_prices_eur_per_mwh
    )

    fig, ax = plt.subplots(figsize=(13, 7))
    image = ax.imshow(pivot.values, aspect="auto", cmap="viridis_r")
    fig.colorbar(image, ax=ax, label="Discounted LCOH (€/kg H2)")

    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([f"{v:g}" for v in pivot.columns])
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([f"{v:.0f}" for v in pivot.index])
    ax.set_xlabel("Electricity Price (€/MWh)")
    ax.set_ylabel("Baseline Load (%)")

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
    plt.savefig(
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_hybrid_strategy_heatmap.png"),
        dpi=300,
        bbox_inches="tight"
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
                        hybrid_baseline_fraction=0.20, strategy="hybrid"):
    """
    Representative-day dispatch plot.

    Visual convention requested for Figures 16 and 17:
      - PV generation: thick ORANGE line.
      - Hours with gross curtailment: RED dotted overlay on the PV curve.
      - Grid electricity supplied to PEM: thick GREY line.
      - Hydrogen energy output: thick BLUE line.
      - PEM electricity use is shown as filled areas from the x-axis:
            orange = PV supplied to PEM,
            grey   = grid electricity supplied to PEM.
      - PEM rated capacity and requested baseline are retained on the PEM/H2 axis.

    Left y-axis  : PV generation (MW).
    Right y-axis : PEM electrical input and H2 energy output (MW).
    """
    day_mask = df["time"].dt.strftime("%Y-%m-%d") == date_string
    day_df = df.loc[day_mask].copy()
    if day_df.empty:
        raise ValueError(f"No PV records found for {date_string}.")

    if strategy == "hybrid":
        op = simulate_hybrid_operation(
            day_df,
            selected_pem_mw,
            hybrid_baseline_fraction,
            selected_grid_limit_mw
        )
        baseline_w = hybrid_baseline_fraction * selected_pem_mw * 1_000_000
        title_suffix = "Hybrid PV-Priority Dispatch"
        pv_to_pem_w = op["pv_to_pem_w"]
        grid_to_pem_w = op["purchased_w"]
        gross_curtailment_w = op["gross_curtailment_without_pem_w"]
        residual_curtailment_w = op["residual_curtailment_w"]
        pem_power_w = op["pem_power_w"]

    elif strategy == "nonhybrid":
        op = simulate_nonhybrid_operation(
            day_df,
            selected_pem_mw,
            selected_grid_limit_mw
        )
        baseline_w = MIN_PEM_LOAD_FRACTION * selected_pem_mw * 1_000_000
        title_suffix = "Non-Hybrid PV Minimum-Load + Curtailment Ramping"
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

    else:
        raise ValueError("strategy must be 'hybrid' or 'nonhybrid'.")

    hours = day_df["time"].dt.hour.to_numpy()
    pv_mw = day_df["P"].to_numpy(dtype=float) / 1_000_000
    pv_to_pem_mw = np.asarray(pv_to_pem_w, dtype=float) / 1_000_000
    grid_to_pem_mw = np.asarray(grid_to_pem_w, dtype=float) / 1_000_000
    pem_total_mw = np.asarray(pem_power_w, dtype=float) / 1_000_000
    gross_curtailment_mw = np.asarray(gross_curtailment_w, dtype=float) / 1_000_000
    residual_curtailment_mw = np.asarray(residual_curtailment_w, dtype=float) / 1_000_000

    # Hourly H2 mass (kg/h for a 1-hour timestep) -> average chemical power (MW, LHV).
    h2_lhv_mw = (
        np.asarray(op["hourly_h2_kg"], dtype=float)
        * h2_lower_heating_value_kwh_per_kg
        / 1000.0
    )

    # Plot the red dotted curtailment marker only where curtailment exists.
    # It overlays the PV generation curve at those hours, so visually the orange
    # PV curve changes to a red dotted segment whenever the counterfactual plant
    # output would exceed the export limit.
    curtailment_mask = gross_curtailment_mw > 1e-9
    curtailed_pv_overlay_mw = np.where(curtailment_mask, pv_mw, np.nan)

    fig_number, fig_title = next_fig(f"{title_suffix} on {date_string}")
    fig, ax_pv = plt.subplots(figsize=(12, 6))
    ax_pem = ax_pv.twinx()

    # ---------------------------------------------------------
    # LEFT AXIS, PV-side quantities
    # ---------------------------------------------------------
    pv_line, = ax_pv.plot(
        hours,
        pv_mw,
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
        linewidth=3.0,
        label="PV generation during curtailment hours",
        zorder=7
    )

    # ---------------------------------------------------------
    # RIGHT AXIS, PEM input and H2 output
    # ---------------------------------------------------------
    # Filled PEM-use areas. PV is the first layer from the x-axis. Grid top-up,
    # when present, is stacked immediately above it so total filled height equals
    # total PEM electrical input.
    pv_fill = ax_pem.fill_between(
        hours,
        0,
        pv_to_pem_mw,
        color="tab:orange",
        alpha=0.28,
        label="PEM input from PV",
        zorder=1
    )

    grid_fill = ax_pem.fill_between(
        hours,
        pv_to_pem_mw,
        pv_to_pem_mw + grid_to_pem_mw,
        where=grid_to_pem_mw > 1e-12,
        interpolate=True,
        color="0.55",
        alpha=0.38,
        label="PEM input from grid",
        zorder=2
    )

    # Thick grey grid line, exactly the purchased-grid contribution to PEM.
    grid_line, = ax_pem.plot(
        hours,
        grid_to_pem_mw,
        color="0.35",
        linewidth=3.2,
        label="Grid power to PEM (MW)",
        zorder=8
    )

    # Total PEM input is kept as a thin neutral boundary, not a dominant curve.
    pem_boundary, = ax_pem.plot(
        hours,
        pem_total_mw,
        color="0.20",
        linewidth=1.2,
        alpha=0.75,
        label="Total PEM electrical input (MW)",
        zorder=6
    )

    # Thick blue H2 output curve.
    h2_line, = ax_pem.plot(
        hours,
        h2_lhv_mw,
        color="tab:blue",
        linewidth=3.2,
        label="H2 energy output (MW, LHV)",
        zorder=9
    )

    rated_line = ax_pem.axhline(
        selected_pem_mw,
        color="0.25",
        linestyle="--",
        linewidth=1.5,
        label=f"PEM rated capacity ({selected_pem_mw:.1f} MW)",
        zorder=4
    )

    baseline_line = ax_pem.axhline(
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
    ax_pv.set_ylabel("PV Power (MW)")
    ax_pem.set_ylabel("PEM Input / H2 Energy Output (MW)")

    ax_pv.set_ylim(bottom=0)
    right_axis_max = max(
        selected_pem_mw * 1.30,
        float(np.nanmax(pem_total_mw)) * 1.18 if len(pem_total_mw) else 0.0,
        float(np.nanmax(h2_lhv_mw)) * 1.25 if len(h2_lhv_mw) else 0.0,
        0.5
    )
    ax_pem.set_ylim(0, right_axis_max)

    ax_pv.set_title(
        f"{fig_title}\n"
        f"(PEM size = {selected_pem_mw:.1f} MW, "
        f"baseline = {baseline_w/1_000_000:.2f} MW)"
    )
    ax_pv.grid(True, alpha=0.25)

    # One combined legend, ordered by physical meaning.
    handles = [
        pv_line,
        curtailment_line,
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
    ax_pv.legend(handles, labels, loc="upper left", fontsize=8, ncol=2)

    # Dispatch diagnostics printed to console. These are useful for checking that
    # the visualized areas correspond to the physical hourly power balance.
    pv_to_pem_day_mwh = float(np.nansum(pv_to_pem_mw))
    grid_to_pem_day_mwh = float(np.nansum(grid_to_pem_mw))
    pem_day_mwh = float(np.nansum(pem_total_mw))
    gross_curt_day_mwh = float(np.nansum(gross_curtailment_mw))
    residual_curt_day_mwh = float(np.nansum(residual_curtailment_mw))
    h2_day_kg = float(np.nansum(op["hourly_h2_kg"]))

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

    fig.tight_layout()
    fig.savefig(
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_{strategy}_daily_dispatch.png"),
        dpi=300,
        bbox_inches="tight"
    )
    plt.show()

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
        opportunity_cost_eur, _ = calculate_2023_pv_opportunity_cost(
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
    plt.savefig(
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
    plt.savefig(
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_pvgis_vs_pvsyst_power_duration.png"),
        dpi=300, bbox_inches="tight"
    )
    plt.show()


#
# =========================================
# MAIN EXECUTION
# =========================================

pvgis_df, pvsyst_df = load_pv_sources()
pv_source_frames = {"PVGIS": pvgis_df, "PVSYST": pvsyst_df}
df = pv_source_frames[PV_DATA_SOURCE].copy()

print("=" * 72)
print("PV DATA SOURCE")
print(f"Active source: {PV_DATA_SOURCE}")
print("PVGIS source: calendar-year 2023 / PVGIS-SARAH3")
print("PVsyst source: PVGIS TMY 5.3 / detailed PVsyst system simulation")
print("NOTE: PVGIS-vs-PVsyst is a source/model comparison, not same-weather validation.")
if PV_DATA_SOURCE == "PVSYST":
    pvsyst_net_mwh = pvsyst_df["P_raw_w"].sum() / 1e6
    pvsyst_dispatch_mwh = pvsyst_df["P"].sum() / 1e6
    print(f"PVsyst raw net E_Grid: {pvsyst_net_mwh:.3f} MWh/year")
    print(f"PVsyst non-negative PV dispatch input: {pvsyst_dispatch_mwh:.3f} MWh/year")
    print(f"Night-consumption clipping adjustment: {pvsyst_dispatch_mwh - pvsyst_net_mwh:.3f} MWh/year")
print("=" * 72)

# ===== PV SYSTEM ANALYSIS =====
annual_energy_mwh, capacity_factor = calculate_pv_metrics(df)

print(f"Annual PV Energy (MWh): {annual_energy_mwh:.2f}")
print(f"PV Capacity Factor (%): {capacity_factor * 100:.2f}")
#

print(f"Rows: {len(df)}")

# ====== PEM OPTIMIZATION =====
print(f"PEM Scenarios (MW): {pem_sizes_mw}")
print(f"PEM CAPEX Scenarios (€/kW): {pem_capex_scenarios}")

pem_sizes_results = []
h2_results = []
utilization_results = []

# ====== GRID LIMIT & CURTAILMENT SETUP =====
selected_grid_limit_mw = 6

# v1.4 fine-grid, three-objective PEM sizing.
multiobjective_table, pareto_table, multiobjective_recommended, full_recovery_point = (
    build_multiobjective_pem_sizing(df, selected_grid_limit_mw)
)
multiobjective_table.to_csv(
    os.path.join(BASE_DIR, "multiobjective_pem_sizing.csv"), index=False
)
pareto_table.to_csv(
    os.path.join(BASE_DIR, "pareto_pem_sizing.csv"), index=False
)

selected_pem_mw = float(multiobjective_recommended["PEM Size (MW)"])
selected_pem_recovery_pct = float(multiobjective_recommended["Curtailment Recovery (%)"])
full_recovery_mw_fine = float(full_recovery_point["PEM Size (MW)"])
full_recovery_pct_fine = float(full_recovery_point["Curtailment Recovery (%)"])

# Single-objective diagnostics are retained to show why a multi-objective decision is needed.
economic_lcoh_row = multiobjective_table.loc[
    multiobjective_table["Discounted LCOH (EUR/kg H2)"].idxmin()
]
npv_optimum_row = multiobjective_table.loc[multiobjective_table["NPV (EUR)"].idxmax()]

print("\n=== v1.4 MULTI-OBJECTIVE PEM SIZING ===")
print(
    f"Balanced compromise: {selected_pem_mw:.2f} MW | "
    f"Recovery {selected_pem_recovery_pct:.1f}% | "
    f"LCOH {multiobjective_recommended['Discounted LCOH (EUR/kg H2)']:.2f} €/kg | "
    f"NPV {multiobjective_recommended['NPV (EUR)']/1e6:.2f} M€"
)
print(
    f"Minimum-LCOH point: {economic_lcoh_row['PEM Size (MW)']:.2f} MW | "
    f"{economic_lcoh_row['Discounted LCOH (EUR/kg H2)']:.2f} €/kg"
)
print(
    f"Maximum-NPV point: {npv_optimum_row['PEM Size (MW)']:.2f} MW | "
    f"{npv_optimum_row['NPV (EUR)']/1e6:.2f} M€"
)
print(
    f"First fine-sweep size at ≥{FULL_RECOVERY_THRESHOLD_PCT:.1f}% recovery: "
    f"{full_recovery_mw_fine:.2f} MW"
)
print(f"Pareto-efficient candidates: {len(pareto_table)} of {len(multiobjective_table)}")

# Source comparison now uses the multi-objective-selected PEM size.
pv_source_comparison = build_pv_source_comparison(
    pvgis_df, pvsyst_df, selected_grid_limit_mw, selected_pem_mw
)
pv_source_comparison.to_csv(
    os.path.join(BASE_DIR, "pv_source_comparison_summary.csv"), index=False
)
print("\nPVGIS vs PVsyst reference comparison:")
print(pv_source_comparison.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))

df["curtailed_power_w"], selected_curtailed_mwh = calculate_hourly_curtailment(df, selected_grid_limit_mw)
selected_nonhybrid_op = simulate_nonhybrid_operation(
    df, selected_pem_mw, selected_grid_limit_mw
)
# Selected non-hybrid base case now uses PV minimum-load operation plus curtailment ramping.
df["pem_input_w"] = pd.Series(selected_nonhybrid_op["pem_power_w"], index=df.index)
selected_h2_kg = selected_nonhybrid_op["annual_h2_kg"]
selected_lost_export_mwh = selected_nonhybrid_op["lost_export_mwh"]
selected_opportunity_cost_eur, selected_monthly_opportunity_cost = (
    calculate_2023_pv_opportunity_cost(
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
print(f"PEM Size: {selected_pem_mw:.2f} MW (multi-objective balanced compromise)")
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
    calculate_2023_pv_opportunity_cost(
        df,
        simulate_nonhybrid_operation(
            df, pem_mw, selected_grid_limit_mw
        )["lost_export_w"]
    )[0]
    for pem_mw in pem_sizes_mw
]
print(results_table.to_string(index=False))

###


# ====== BATTERY VS HYDROGEN CONVERSION-EFFICIENCY COMPARISON ======

# Use the SAME electrical input energy for both pathways.
# This isolates conversion efficiency from component sizing effects.

pem_size_w = selected_pem_mw * 1_000_000

load_fraction = df["pem_input_w"].values / pem_size_w
active = load_fraction >= MIN_PEM_LOAD_FRACTION

pem_power_used_w = np.where(
    active,
    df["pem_input_w"].values,
    0.0
)

accepted_electrical_energy_mwh = (
    pem_power_used_w.sum() / 1_000_000
)

# Battery pathway
battery_recovered_mwh = (
    accepted_electrical_energy_mwh
    * battery_round_trip_efficiency
)

# Hydrogen pathway
h2_energy_mwh = (
    selected_h2_kg
    * h2_lower_heating_value_kwh_per_kg
    / 1000
)

battery_energy_retention = (
    battery_recovered_mwh
    / accepted_electrical_energy_mwh
)

hydrogen_energy_retention = (
    h2_energy_mwh
    / accepted_electrical_energy_mwh
)

print(
    f"Common electrical input energy (MWh): "
    f"{accepted_electrical_energy_mwh:.2f}"
)

print(
    f"Battery recovered energy (MWh): "
    f"{battery_recovered_mwh:.2f}"
)

print(
    f"Hydrogen stored energy (MWh LHV): "
    f"{h2_energy_mwh:.2f}"
)

print(
    f"Battery round-trip energy retention (%): "
    f"{battery_energy_retention * 100:.1f}"
)

print(
    f"Hydrogen conversion energy retention (% LHV): "
    f"{hydrogen_energy_retention * 100:.1f}"
)

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
# This model now compares non-hybrid PV minimum-load dispatch and hybrid PV-priority dispatch.
# A dedicated system would require a separate full techno-economic model
# with matched PEM sizing, grid connection costs, and offtake assumptions.
benchmark_dedicated_lcoh_low  = 3.5   # €/kg, southern Europe literature
benchmark_dedicated_lcoh_high = 6.0   # €/kg, southern Europe literature



print(f"Hydrogen revenue (€): {hydrogen_revenue_eur:,.0f}")
print(f"Annualized PEM CAPEX / ACC (€): {annualized_pem_capex:,.0f}  [CRF={crf:.4f}]")
print(f"Simple net hydrogen value (€): {simple_net_hydrogen_value:,.0f}")
print(f"Annual OPEX (€): {annual_opex:,.0f}")
print("PV export opportunity-cost basis: monthly 2023 EAC RES purchase-price proxy at 11 kV")
print(
    "Monthly proxy range (€/MWh): "
    f"{min(PV_EXPORT_PRICE_2023_EUR_PER_MWH.values()):.2f} - "
    f"{max(PV_EXPORT_PRICE_2023_EUR_PER_MWH.values()):.2f}"
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

# Selected design may lie between the coarse plotting sizes, so use the
# directly simulated utilization instead of indexing pem_sizes_mw.
selected_utilization = selected_nonhybrid_op["utilization_pct"]
print(f"Note: the non-hybrid base-case LCOH reflects the selected dispatch and utilization ({selected_utilization:.2f}%).")

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
# ===== v1.3 HYBRID DISPATCH SANITY CHECKS =====

print("\n=== HYBRID DISPATCH SANITY CHECKS ===")

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

# Check 3: A 0% requested grid baseline must buy no grid electricity.
zero_baseline = hybrid_summary[hybrid_summary["Baseline Load (%)"] == 0]
assert np.allclose(zero_baseline["Purchased Energy (MWh)"], 0.0, atol=1e-8), \
    "FAILED: 0% hybrid baseline purchases grid electricity."
print("0% baseline grid purchase = 0: PASS")

# Check 4: Physical metrics are bounded.
assert hybrid_summary["Utilization (%)"].between(0, 100 + 1e-8).all(), \
    "FAILED: Hybrid utilization outside 0-100%."
assert hybrid_summary["Share PEM Energy from PV (%)"].between(0, 100).all(), \
    "FAILED: PV share outside 0-100%."
assert (hybrid_summary["Residual Curtailment (MWh)"] >= -1e-9).all(), \
    "FAILED: Negative residual curtailment."
print("Physical bounds: PASS")

# Opportunity-cost accounting: lost export must be non-negative, and only
# counterfactual export displaced by PEM consumption may be charged.
assert (hybrid_summary["Lost Export Energy (MWh)"] >= -1e-9).all(), \
    "FAILED: Negative lost-export energy."
assert (hybrid_summary["PV Opportunity Cost (€/year)"] >= -1e-9).all(), \
    "FAILED: Negative PV opportunity cost."
print("PV opportunity-cost accounting: PASS")

# Check 5: Dispatch physics does not depend on the electricity-price scenario.
for baseline, group in hybrid_summary.groupby("Baseline Load (%)"):
    assert np.allclose(group["H2 (kg/year)"], group["H2 (kg/year)"].iloc[0]), \
        f"FAILED: H2 changes with price at baseline {baseline}%."
    assert np.allclose(group["Purchased Energy (MWh)"], group["Purchased Energy (MWh)"].iloc[0]), \
        f"FAILED: Purchased energy changes with price at baseline {baseline}%."
print("Price-independent physical dispatch: PASS")

# Check 6: PV-priority hybrid at zero grid baseline should use at least as much
# annual PEM energy as the deliberately conservative non-hybrid strategy.
assert zero_baseline["Total PEM Energy (MWh)"].iloc[0] + 1e-8 >= accepted_electrical_energy_mwh, \
    "FAILED: PV-priority hybrid uses less PEM energy than non-hybrid base case."
print("Hybrid PV-priority dispatch dominates conservative non-hybrid energy use: PASS")

print("ALL HYBRID DISPATCH SANITY CHECKS PASSED")
###



###

print("\n=== v1.3 Hybrid Operating Strategy Sensitivity ===")
print(hybrid_summary.round(2).to_string(index=False))

# ----- Strategy comparison at zero grid baseline -----
# 0% hybrid baseline is PV-priority PV-only operation. It is intentionally
# different from the non-hybrid minimum-load + curtailment-ramping base case.
zero_baseline_rows = hybrid_summary[hybrid_summary["Baseline Load (%)"] == 0.0]
zero_baseline_h2 = zero_baseline_rows["H2 (kg/year)"].iloc[0]
zero_baseline_util = zero_baseline_rows["Utilization (%)"].iloc[0]

print(
    f"\nPV-only hybrid at 0% grid baseline vs non-hybrid base case:\n"
    f"  Hybrid H2 = {zero_baseline_h2:.2f} kg/year, non-hybrid H2 = {selected_h2_kg:.2f} kg/year\n"
    f"  Hybrid utilization = {zero_baseline_util:.2f}%, non-hybrid utilization = {selected_utilization:.2f}%"
)

# ----- Where does hybrid beat the non-hybrid base case? -----
hybrid_summary["Better than non-hybrid base case?"] = hybrid_summary["LCOH (€/kg H2)"] < discounted_lcoh

print(
    f"\nHybrid vs non-hybrid reference (discounted LCOH = "
    f"{discounted_lcoh:.2f} €/kg H2):"
)
improved = hybrid_summary[
    (hybrid_summary["Baseline Load (%)"] > 0) &
    (hybrid_summary["Better than non-hybrid base case?"])
].sort_values(["Baseline Load (%)", "Electricity Price (€/MWh)"])

if improved.empty:
    print(
        "  No tested (baseline load, electricity price) combination beats "
        "the non-hybrid base-case LCOH under current assumptions."
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
# FINAL BASE CASE + SPECIFIC HYBRID DESIGN POINT
# =========================================

hybrid_design_rows = hybrid_summary[
    np.isclose(hybrid_summary["Baseline Load (%)"], hybrid_design_baseline_fraction * 100)
    & np.isclose(
        hybrid_summary["Electricity Price (€/MWh)"],
        hybrid_design_electricity_price_eur_per_mwh
    )
]
if hybrid_design_rows.empty:
    raise ValueError(
        "Configured hybrid design point is not present in hybrid_summary. "
        "Add its baseline and electricity price to the sensitivity scenario lists."
    )

hybrid_design = hybrid_design_rows.iloc[0]
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

selected_recovery_pct = selected_pem_recovery_pct
full_benchmark_mw = full_recovery_mw_fine
full_benchmark_recovery_pct = full_recovery_pct_fine

print("\n" + "=" * 72)
print("FINAL BASE CASE SUMMARY")
print("=" * 72)
print(f"PV plant capacity:                    10.0 MWp")
print(f"Grid export limit:                    {selected_grid_limit_mw:.1f} MW")
print(f"Selected PEM capacity:                {selected_pem_mw:.1f} MW")
print(f"Curtailment recovery:                 {selected_recovery_pct:.1f} %")
print(f"First size at ≥99.9% recovery:         {full_benchmark_mw:.2f} MW ({full_benchmark_recovery_pct:.1f} % recovery)")
print(f"Reference H2 selling price:           {hydrogen_sale_price:.2f} €/kg")
print(f"H2 price sensitivity:                 {hydrogen_price_scenarios} €/kg")
print("-")
print("NON-HYBRID DESIGN")
print(f"Annual H2 production:                 {selected_h2_kg:,.0f} kg/year")
print(f"PEM utilization:                      {selected_utilization:.2f} %")
print(f"Discounted LCOH:                      {discounted_lcoh:.2f} €/kg H2")
print(f"Break-even H2 price:                  {nonhybrid_break_even_h2_price:.2f} €/kg H2")
print(f"NPV @ {hydrogen_sale_price:.2f} €/kg H2:                  {npv/1e6:.2f} M€")
print(f"Economic result:                      {'PROFITABLE' if npv >= 0 else 'NOT PROFITABLE'}")
print("-")
print("SPECIFIC HYBRID DESIGN POINT")
print(f"PEM capacity:                         {selected_pem_mw:.1f} MW")
print(f"Requested baseline:                   {hybrid_design_baseline_fraction*100:.0f} % ({hybrid_design_baseline_fraction*selected_pem_mw:.2f} MW)")
print(f"Grid electricity price assumption:    {hybrid_design_electricity_price_eur_per_mwh:.0f} €/MWh")
print(f"Annual H2 production:                 {hybrid_design['H2 (kg/year)']:,.0f} kg/year")
print(f"PEM utilization:                      {hybrid_design['Utilization (%)']:.2f} %")
print(f"PV electricity to PEM:                {hybrid_design['PV Energy to PEM (MWh)']:.1f} MWh/year")
print(f"Purchased grid electricity:           {hybrid_design['Purchased Energy (MWh)']:.1f} MWh/year")
print(f"Discounted LCOH:                      {hybrid_design['LCOH (€/kg H2)']:.2f} €/kg H2")
print(f"Break-even H2 price:                  {hybrid_break_even_h2_price:.2f} €/kg H2")
print(f"NPV @ {hydrogen_sale_price:.2f} €/kg H2:                  {hybrid_design['NPV (€)']/1e6:.2f} M€")
print(f"Economic result:                      {'PROFITABLE' if hybrid_design['NPV (€)'] >= 0 else 'NOT PROFITABLE'}")
print("=" * 72)

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

plot_annualized_lcoh_vs_pem_size(results_table)

plot_multiobjective_pem_sizing(multiobjective_table, multiobjective_recommended)
plot_curtailment_recovery_vs_pem_size(
    pem_sizes_mw, curtailment_recovery_results,
    selected_design_mw=selected_pem_mw,
    selected_design_recovery=selected_pem_recovery_pct,
    full_recovery_mw=full_recovery_mw_fine,
    full_recovery_pct=full_recovery_pct_fine,
)
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
plot_lcoh_vs_baseline_load_multi_price(hybrid_summary, reference_lcoh=discounted_lcoh)
plot_npv_vs_baseline_load_multi_price(hybrid_summary, reference_npv=npv)
plot_hybrid_strategy_heatmap(
    hybrid_summary, baseline_load_fractions, electricity_price_scenarios_eur_per_mwh,
    reference_lcoh=discounted_lcoh
)

# ----- Representative-day dispatch visualizations -----
selected_dispatch_plot_date = resolve_dispatch_plot_date(
    df, selected_grid_limit_mw, dispatch_plot_date
)
print(f"Representative dispatch day: {selected_dispatch_plot_date}")

plot_daily_dispatch(
    df, selected_pem_mw, selected_grid_limit_mw, selected_dispatch_plot_date,
    hybrid_baseline_fraction=dispatch_plot_hybrid_baseline_fraction,
    strategy="nonhybrid"
)
plot_daily_dispatch(
    df, selected_pem_mw, selected_grid_limit_mw, selected_dispatch_plot_date,
    hybrid_baseline_fraction=dispatch_plot_hybrid_baseline_fraction,
    strategy="hybrid"
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
# - no stack degradation
# - no stack replacement
# - no hydrogen compression
# - no hydrogen storage
# - no Balance-of-Plant electricity consumption
# - no historical hourly Cyprus electricity-price series
# - Cyprus 2023 electricity cost is represented by a static
#   MV/HV industrial benchmark rather than a time-resolved tariff
# - no dynamic price-responsive dispatch
# - PV export opportunity cost is represented using a monthly 2023
#   EAC RES purchase-price proxy rather than an hourly merchant-market price
#
# Therefore, this is a first-order techno-economic screening model,
# not an investment-grade feasibility study.


# =========================================
# KEY FINDINGS
# =========================================
# Key numerical findings are printed dynamically above because the dispatch
# redesign changes annual H2 production, utilization, LCOH and NPV. Do not
# hard-code old v1.2 values here; use the current run output and saved figures.

# The non-hybrid and hybrid dispatch strategies are screening cases.
# Their economic viability depends strongly on the value assigned to PV diverted
# from export, grid-purchase price, hydrogen sale price, and omitted BoP costs.
#
# /// END ///