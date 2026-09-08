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

#This project evaluates the feasibility of converting
#curtailed photovoltaic electricity into green hydrogen
#using PEM electrolysis.

#The model combines:
#- PVGIS hourly production data
#- Grid export constraints
#- Curtailment estimation
#- PEM electrolyzer sizing
#- LCOH calculations
#- NPV analysis
#- Sensitivity studies


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

#PVGIS
filename = os.path.join(BASE_DIR, "Timeseries_35.141_33.415_SA3_10000kWp_crystSi_14_28deg_0deg_2023_2023.csv")
#
#kwh_per_kg_h2 = 52
water_liters_per_kg_h2 = 9
#electricity_price_sell = 0.08
hydrogen_sale_price = 6
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

hydrogen_price_scenarios = [4, 6, 8, 10, 12, 13, 14, 15, 16]
battery_round_trip_efficiency = 0.90
h2_lower_heating_value_kwh_per_kg = 33.33
discount_rate = 0.08
###


# =========================================
# DATA LOADING FUNCTIONS
# =========================================

def load_pvgis_data(filename):
    df = pd.read_csv(filename, skiprows=10)
    df["P"] = pd.to_numeric(df["P"], errors="coerce")
    df = df.dropna(subset=["P"])
    df["time"] = pd.to_datetime(df["time"], format="%Y%m%d:%H%M")
    df["month"] = df["time"].dt.month
    return df


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
    """
    For each candidate PEM size, compute what fraction of the SELECTED
    base-case curtailed energy (df["curtailed_power_w"], already fixed by
    the chosen grid export limit) the PEM can actually absorb, applying
    the same 15% minimum-load cutoff used elsewhere in the model.

    NOTE: this uses the curtailment profile for the base-case grid limit
    only. It answers "how well does PEM size X recover the curtailment we
    already have", not "how curtailment changes with grid limit".
    """
    curtailment_recovery_results = []

    for pem_size_mw in pem_sizes_mw:

        pem_capacity_kw = pem_size_mw * 1000
        pem_energy_used_kwh = 0.0

        for curtailed_power_w in df["curtailed_power_w"]:

            curtailed_power_kw = curtailed_power_w / 1000
            pem_power_kw = min(curtailed_power_kw, pem_capacity_kw)
            load_fraction = pem_power_kw / pem_capacity_kw if pem_capacity_kw > 0 else 0.0

            if load_fraction < MIN_PEM_LOAD_FRACTION:
                pem_power_used_kw = 0.0
            else:
                pem_power_used_kw = pem_power_kw

            # Hourly timestep = 1 hour, so kW == kWh contribution per row
            pem_energy_used_kwh += pem_power_used_kw

        pem_energy_used_mwh = pem_energy_used_kwh / 1000

        recovery_percent = (
            pem_energy_used_mwh
            / selected_curtailed_mwh
            * 100
        )

        curtailment_recovery_results.append(recovery_percent)

    return curtailment_recovery_results


def diagnose_pem_curtailment(df, pem_sizes_mw):
    """
    For each candidate PEM size, split the SELECTED base-case curtailed
    energy (df["curtailed_power_w"], same source as everywhere else in
    the model) into:
      - Accepted: energy the PEM actually converts
      - Rejected Below Min Load: curtailed power present but below the
        MIN_PEM_LOAD_FRACTION cutoff, so the PEM stays off
      - Rejected Above PEM Capacity: curtailed power exceeding the PEM's
        rated capacity, so the excess cannot be absorbed

    Uses the same MIN_PEM_LOAD_FRACTION as calculate_hydrogen_from_pem_input
    and calculate_curtailment_recovery_vs_pem_size, so all three stay
    numerically consistent with each other.
    """

    results = []

    curtailed_w = df["curtailed_power_w"].values

    for pem_mw in pem_sizes_mw:

        pem_capacity_w = pem_mw * 1_000_000
        min_power_w = MIN_PEM_LOAD_FRACTION * pem_capacity_w

        accepted_w = np.where(
            curtailed_w >= min_power_w,
            np.minimum(curtailed_w, pem_capacity_w),
            0.0
        )

        below_min_w = np.where(
            (curtailed_w > 0) & (curtailed_w < min_power_w),
            curtailed_w,
            0.0
        )

        above_capacity_w = np.where(
            curtailed_w > pem_capacity_w,
            curtailed_w - pem_capacity_w,
            0.0
        )

        results.append({
            "PEM Size (MW)": pem_mw,
            "Accepted (MWh)": accepted_w.sum() / 1_000_000,
            "Rejected Below Min Load (MWh)": below_min_w.sum() / 1_000_000,
            "Rejected Above PEM Capacity (MWh)": above_capacity_w.sum() / 1_000_000
        })

    return pd.DataFrame(results)


def minimum_load_sensitivity(df, pem_sizes_mw, min_load_fractions):
    """
    Evaluate how the electrolyzer minimum-load constraint affects
    recovery of curtailed PV energy, for the SAME base-case curtailment
    profile (df["curtailed_power_w"]) used everywhere else in the model.

    Returns a dictionary:
        results[min_load_fraction] = [recovery % for each PEM size]

    min_load_fractions has no default value here on purpose: the natural
    default (MIN_LOAD_SENSITIVITY) is defined later in this file, and a
    default argument using a name that doesn't exist yet at function
    definition time raises NameError immediately, before the function is
    ever called. Pass MIN_LOAD_SENSITIVITY explicitly from Main Execution.
    """

    curtailed_power_w = df["curtailed_power_w"].to_numpy()

    total_curtailed_energy_mwh = (
        curtailed_power_w.sum() / 1e6
    )

    results = {}

    for min_load_fraction in min_load_fractions:

        recovery_values = []

        for pem_size_mw in pem_sizes_mw:

            pem_capacity_w = pem_size_mw * 1e6
            minimum_power_w = min_load_fraction * pem_capacity_w

            accepted_energy_wh = 0.0

            for curtailed_w in curtailed_power_w:

                if curtailed_w < minimum_power_w:
                    accepted_w = 0.0
                else:
                    accepted_w = min(curtailed_w, pem_capacity_w)

                accepted_energy_wh += accepted_w

            accepted_energy_mwh = accepted_energy_wh / 1e6

            if total_curtailed_energy_mwh > 0:
                recovery_percent = (
                    accepted_energy_mwh
                    / total_curtailed_energy_mwh
                    * 100
                )
            else:
                recovery_percent = 0.0

            recovery_values.append(recovery_percent)

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
def analyze_pem_size(df, pem_size_mw, pem_capex_per_kw):

    pem_size_w = pem_size_mw * 1_000_000
    pem_size_kw = pem_size_mw * 1_000

    pem_capex_eur = pem_size_kw * pem_capex_per_kw

    pem_input_w = df["curtailed_power_w"].clip(
        upper=pem_size_w
    )

    load_fraction = pem_input_w / pem_size_w

    active = load_fraction >= MIN_PEM_LOAD_FRACTION

    pem_power_used_w = np.where(
    active,
    pem_input_w,
    0.0
    )

    pem_energy_mwh = pem_power_used_w.sum() / 1_000_000
    _, h2_kg = calculate_hydrogen_from_pem_input(
        pem_input_w.values,
        pem_size_w
    )

    utilization = (
        pem_energy_mwh /
        (pem_size_mw * 8760)
    )

    one_year_capex_intensity = (
        pem_capex_eur / h2_kg
        if h2_kg > 0
        else np.inf
    )

    return (
        pem_energy_mwh,
        h2_kg,
        utilization,
        pem_capex_eur,
        one_year_capex_intensity
    )
###
def calculate_selected_pem_input(df, selected_pem_mw):

    selected_pem_w = selected_pem_mw * 1_000_000

    pem_input_w = df["curtailed_power_w"].clip(
        upper=selected_pem_w
    )

    _, selected_h2_kg = calculate_hydrogen_from_pem_input(
        pem_input_w.values,
        selected_pem_w
    )

    return pem_input_w, selected_h2_kg
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
        annual_opex, pem_capex_eur, discount_rate, project_lifetime_years):
    # annual_cashflow = revenue - opex only (CAPEX handled as lump sum in calculate_npv)
    npv_results = []
    for h2_price in hydrogen_price_scenarios:
        annual_revenue = selected_h2_kg * h2_price
        annual_cashflow_for_npv = annual_revenue - annual_opex
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


def calculate_discounted_lcoh_for_capex_scenarios(selected_pem_mw, annual_h2_kg,
        pem_capex_scenarios, discount_rate, project_lifetime_years):
    lcoh_results = []
    for capex_per_kw in pem_capex_scenarios:
        pem_capex_eur = selected_pem_mw * 1000 * capex_per_kw
        annual_opex = pem_capex_eur * pem_opex_fraction
        lcoh = calculate_discounted_lcoh(pem_capex_eur, annual_opex, annual_h2_kg,
                discount_rate, project_lifetime_years)
        lcoh_results.append(lcoh)
    return lcoh_results


def calculate_discounted_lcoh_vs_grid_limit(df, grid_limits_mw, selected_pem_mw,
        discount_rate, project_lifetime_years):
    results = []
    for grid_limit_mw in grid_limits_mw:
        curtailed_power_w, curtailed_energy_mwh = calculate_hourly_curtailment(df, grid_limit_mw)
        pem_input_w = curtailed_power_w.clip(upper=selected_pem_mw * 1_000_000)
        #annual_h2_kg = pem_input_w.sum() / 1_000_000 * 1000 / kwh_per_kg_h2
        pem_size_w = selected_pem_mw * 1_000_000
        _, annual_h2_kg = calculate_hydrogen_from_pem_input(pem_input_w.values, pem_size_w)
        pem_capex_eur = selected_pem_mw * 1000 * pem_capex_per_kw
        annual_opex = pem_capex_eur * pem_opex_fraction
        lcoh = calculate_discounted_lcoh(pem_capex_eur, annual_opex, annual_h2_kg,
                discount_rate, project_lifetime_years)
        results.append(lcoh)
    return results


def calculate_lcoh_for_capex_scenarios(selected_pem_mw, annual_h2_kg, pem_capex_scenarios):
    lcoh_results = []
    for capex_per_kw in pem_capex_scenarios:
        pem_capex_eur = selected_pem_mw * 1000 * capex_per_kw
        annual_opex = pem_capex_eur * pem_opex_fraction
        lcoh = calculate_simple_lcoh(pem_capex_eur, annual_opex, annual_h2_kg, project_lifetime_years)
        lcoh_results.append(lcoh)
    return lcoh_results


def calculate_lcoh_vs_grid_limit(df, grid_limits_mw, selected_pem_mw):
    results = []
    for grid_limit_mw in grid_limits_mw:
        curtailed_power_w, curtailed_energy_mwh = calculate_hourly_curtailment(df, grid_limit_mw)
        pem_input_w = curtailed_power_w.clip(upper=selected_pem_mw * 1_000_000)
        #annual_h2_kg = pem_input_w.sum() / 1_000_000 * 1000 / kwh_per_kg_h2
        pem_size_w = selected_pem_mw * 1_000_000
        _, annual_h2_kg = calculate_hydrogen_from_pem_input(pem_input_w.values, pem_size_w )
        pem_capex_eur = selected_pem_mw * 1000 * pem_capex_per_kw
        annual_opex = pem_capex_eur * pem_opex_fraction
        lcoh = calculate_simple_lcoh(pem_capex_eur, annual_opex, annual_h2_kg, project_lifetime_years)
        results.append(lcoh)
    return results


def calculate_npv_grid_sensitivity(df, grid_limits_mw, selected_pem_mw, hydrogen_sale_price,
        annual_opex, pem_capex_eur, discount_rate, project_lifetime_years):
    # annual_cashflow = revenue - opex only (CAPEX handled as lump sum in calculate_npv)
    npv_grid_results = []
    for grid_limit in grid_limits_mw:
        curtailed_power_w, _ = calculate_hourly_curtailment(df, grid_limit)
        pem_input_w = curtailed_power_w.clip(upper=selected_pem_mw * 1_000_000)
        _, h2_kg = calculate_hydrogen_from_pem_input(pem_input_w.values, selected_pem_mw * 1_000_000)
        annual_revenue = h2_kg * hydrogen_sale_price
        annual_cashflow_for_npv = annual_revenue - annual_opex
        npv = calculate_npv(pem_capex_eur, annual_cashflow_for_npv, discount_rate, project_lifetime_years)
        npv_grid_results.append(npv)
    return npv_grid_results


def calculate_npv_heatmap_data(
    df,
    grid_limits_mw,
    hydrogen_price_scenarios,
    selected_pem_mw,
    annual_opex,
    pem_capex_eur,
    discount_rate,
    project_lifetime_years
):
    # annual_cashflow = revenue - opex only (CAPEX handled as lump sum in calculate_npv)
    npv_matrix = []
    for grid_limit in grid_limits_mw:
        row = []
        curtailed_power_w = (df["P"] - grid_limit * 1_000_000).clip(lower=0)
        pem_input_w = curtailed_power_w.clip(upper=selected_pem_mw * 1_000_000)
        _, h2_kg = calculate_hydrogen_from_pem_input(pem_input_w.values, selected_pem_mw * 1_000_000)

        for h2_price in hydrogen_price_scenarios:
            annual_revenue = h2_kg * h2_price
            annual_cashflow_for_npv = annual_revenue - annual_opex
            npv = calculate_npv(pem_capex_eur, annual_cashflow_for_npv, discount_rate, project_lifetime_years)
            row.append(npv)
        npv_matrix.append(row)
    npv_df = pd.DataFrame(npv_matrix, index=grid_limits_mw, columns=hydrogen_price_scenarios)
    return npv_df
###
def calculate_lcoh_opex_sensitivity(
    pem_size_mw,
    pem_capex_per_kw,
    opex_scenarios_per_kw_year,
    annual_h2_kg,
    discount_rate,
    project_lifetime_years
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
            annualized_capex + annual_opex
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


            if h2_kg_year > 0:
                annualized_lcoh = (
                    annualized_capex + annual_opex
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
    plt.annotate(
    "Higher PEM capacity increases\nCAPEX faster than H2 output",
    xy=(2.0, 25),
    xytext=(2.8, 72),
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
    ))
    
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
def plot_curtailment_recovery_vs_pem_size(pem_sizes_mw, curtailment_recovery_results):
    fig_number, fig_title = next_fig("Curtailment Recovery vs PEM Size")
    plt.figure(figsize=(10, 6))

    plt.plot(
        pem_sizes_mw,
        curtailment_recovery_results,
        marker="o"
    )

    plt.xlabel("PEM Size (MW)")
    plt.ylabel("Curtailment Recovery (%)")
    plt.title(fig_title)

    plt.grid(True)
    plt.tight_layout()
    plt.savefig(
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_curtailment_recovery_vs_pem_size.png"),
        dpi=300,
        bbox_inches="tight"
    )
    plt.show()

###
def plot_curtailment_diagnostics(curtailment_diagnostics):
    """
    Plots the two rejection components from diagnose_pem_curtailment()
    against PEM size: energy rejected because it falls below the PEM's
    minimum load, and energy rejected because it exceeds PEM capacity.
    """
    fig_number, fig_title = next_fig("Rejected Curtailment Breakdown vs PEM Size")
    plt.figure(figsize=(10, 6))

    plt.plot(
        curtailment_diagnostics["PEM Size (MW)"],
        curtailment_diagnostics["Rejected Below Min Load (MWh)"],
        marker="o",
        label="Rejected Below Minimum Load"
    )

    plt.plot(
        curtailment_diagnostics["PEM Size (MW)"],
        curtailment_diagnostics["Rejected Above PEM Capacity (MWh)"],
        marker="s",
        label="Rejected Above PEM Capacity"
    )

    plt.xlabel("PEM Size (MW)")
    plt.ylabel("Rejected Curtailed Energy (MWh/year)")
    plt.title(fig_title)
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(
        os.path.join(FIGURES_DIR, f"figure{fig_number:02d}_rejected_curtailment_breakdown.png"),
        dpi=300,
        bbox_inches="tight"
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
# MAIN EXECUTION
# =========================================

df = load_pvgis_data(filename)

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
selected_pem_mw = 1
selected_grid_limit_mw = 6

df["curtailed_power_w"], selected_curtailed_mwh = calculate_hourly_curtailment(df, selected_grid_limit_mw)
df["pem_input_w"], selected_h2_kg = calculate_selected_pem_input(df, selected_pem_mw)

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
print(f"PEM Size: {selected_pem_mw} MW")
print(f"Grid Export Limit: {selected_grid_limit_mw} MW")
print(f"Curtailed PV Energy: {selected_curtailed_mwh:.1f} MWh/year")
print(f"Hydrogen Production: {selected_h2_kg:.0f} kg/year")
print(f"Water Consumption: {selected_water_m3:.1f} m3/year")

# ===== CURTAILMENT DIAGNOSTICS: ACCEPTED / REJECTED BREAKDOWN =====
curtailment_diagnostics = diagnose_pem_curtailment(df, pem_sizes_mw)
print("\nCurtailment Diagnostics:")
print(curtailment_diagnostics.to_string(index=False))

print("\nRejected Curtailment Breakdown:")
for _, row in curtailment_diagnostics.iterrows():
    total_rejected = (
        row["Rejected Below Min Load (MWh)"]
        + row["Rejected Above PEM Capacity (MWh)"]
    )
    print(
        f"PEM {row['PEM Size (MW)']:.2f} MW | "
        f"Below minimum load: {row['Rejected Below Min Load (MWh)']:.1f} MWh | "
        f"Above PEM capacity: {row['Rejected Above PEM Capacity (MWh)']:.1f} MWh | "
        f"Total rejected: {total_rejected:.1f} MWh"
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

# ====== GRID LIMIT SENSITIVITY ======
print("\nGrid Limit Sensitivity:")
for grid_limit_mw in grid_limits_mw:
    curtailed_power_w, curtailed_mwh = calculate_hourly_curtailment(df, grid_limit_mw)
    #h2_from_grid_limit_kg = curtailed_mwh * 1000 / kwh_per_kg_h2
    pem_input_w = curtailed_power_w.clip(upper=selected_pem_mw * 1_000_000)
    _, h2_from_grid_limit_kg = calculate_hydrogen_from_pem_input(pem_input_w.values, selected_pem_mw * 1_000_000)
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
    curtailed_power_w, _ = calculate_hourly_curtailment(df, grid_limit_mw)
    pem_input_w = curtailed_power_w.clip(upper=pem_size_w)
    load_fraction = pem_input_w / pem_size_w

    active = load_fraction >= MIN_PEM_LOAD_FRACTION

    pem_power_used_w = np.where(
    active,
    pem_input_w,
    0.0
    )

    operating_hours = active.sum()

    full_load_hours = pem_power_used_w.sum() / pem_size_w
    print(f"  Grid limit: {grid_limit_mw} MW | Operating hours: {operating_hours} h | Full-load hours: {full_load_hours:.1f} h")
###
lcoh_opex_sensitivity = calculate_lcoh_opex_sensitivity(
    selected_pem_mw,
    pem_capex_per_kw,
    pem_opex_scenarios_per_kw_year,
    selected_h2_kg,
    discount_rate,
    project_lifetime_years
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
)

# LCOH
simple_lcoh = calculate_simple_lcoh(
    pem_capex_eur,
    annual_opex,
    selected_h2_kg,
    project_lifetime_years
)

discounted_lcoh = calculate_discounted_lcoh(
    pem_capex_eur,
    annual_opex,
    selected_h2_kg,
    discount_rate,
    project_lifetime_years
)

# CAPEX sensitivity
lcoh_capex_sensitivity = calculate_lcoh_for_capex_scenarios(
    selected_pem_mw,
    selected_h2_kg,
    pem_capex_scenarios
)

discounted_lcoh_capex_sensitivity = (
    calculate_discounted_lcoh_for_capex_scenarios(
        selected_pem_mw,
        selected_h2_kg,
        pem_capex_scenarios,
        discount_rate,
        project_lifetime_years
    )
)

# Grid-limit sensitivity
lcoh_grid_sensitivity = calculate_lcoh_vs_grid_limit(
    df,
    grid_limits_mw,
    selected_pem_mw
)

discounted_lcoh_grid_sensitivity = (
    calculate_discounted_lcoh_vs_grid_limit(
        df,
        grid_limits_mw,
        selected_pem_mw,
        discount_rate,
        project_lifetime_years
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
# This model only covers curtailment-to-H2 economics.
# A dedicated system would require a separate full techno-economic model
# with matched PEM sizing, grid connection costs, and offtake assumptions.
benchmark_dedicated_lcoh_low  = 3.5   # €/kg, southern Europe literature
benchmark_dedicated_lcoh_high = 6.0   # €/kg, southern Europe literature



print(f"Hydrogen revenue (€): {hydrogen_revenue_eur:,.0f}")
print(f"Annualized PEM CAPEX / ACC (€): {annualized_pem_capex:,.0f}  [CRF={crf:.4f}]")
print(f"Simple net hydrogen value (€): {simple_net_hydrogen_value:,.0f}")
print(f"Annual OPEX (€): {annual_opex:,.0f}")
print(f"Net hydrogen value with OPEX (€): {net_hydrogen_value_with_opex:,.0f}")
print(f"Simple LCOH (€/kg H2): {simple_lcoh:.2f}")


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

# utilization_results is indexed by pem_sizes_mw order, not by MW value.
# Look up the entry that actually corresponds to the selected base-case PEM
# size, rather than assuming a fixed list position.
selected_index = pem_sizes_mw.index(selected_pem_mw)
selected_utilization = utilization_results[selected_index]
print(f"Note: curtailment LCOH reflects low utilization ({selected_utilization:.2f}%), not low electricity cost")

# ===== NPV ANALYSIS =====
# annual_cashflow_for_npv = revenue - opex only.
# CAPEX is deducted as lump sum at year 0 inside calculate_npv().
# annualized_pem_capex must NOT appear here.
annual_cashflow_for_npv = hydrogen_revenue_eur - annual_opex

npv = calculate_npv(pem_capex_eur, annual_cashflow_for_npv, discount_rate, project_lifetime_years)
print(f"NPV (€): {npv:,.0f}")

# ===== HYDROGEN PRICE SENSITIVITY =====
npv_results = calculate_npv_price_sensitivity(
    hydrogen_price_scenarios, selected_h2_kg,
    annual_opex, pem_capex_eur, discount_rate, project_lifetime_years)

print("\nHydrogen Price Sensitivity:")
for h2_price, npv_result in zip(hydrogen_price_scenarios, npv_results):
    print(f"H2 price {h2_price} €/kg -> NPV €{npv_result:,.0f}")

# ===== NPV GRID LIMIT SENSITIVITY =====
npv_grid_results = calculate_npv_grid_sensitivity(
    df, grid_limits_mw, selected_pem_mw,
    hydrogen_sale_price, annual_opex, pem_capex_eur,
    discount_rate, project_lifetime_years)

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
    project_lifetime_years
)
print(npv_df.round(0))


# =========================================
# PLOT EXECUTION
# =========================================

plot_hourly_pv_output(df)
plot_two_day_pv_output(df)
pem_vs_hydrogen(pem_sizes_results, h2_results)
pem_vs_utilization(pem_sizes_results, utilization_results)

plot_annualized_lcoh_vs_pem_size(results_table)

plot_curtailment_recovery_vs_pem_size(pem_sizes_mw, curtailment_recovery_results)
plot_curtailment_diagnostics(curtailment_diagnostics)

capex_cols = ["PEM Size (MW)"] + [col for col in results_table.columns if "CAPEX" in col]
plot_capex_intensity(results_table[capex_cols])

monthly_h2_kg = calculate_monthly_hydrogen(df, selected_pem_mw)
plot_monthly_hydrogen(monthly_h2_kg)

plot_lcoh_vs_grid_limit(grid_limits_mw, lcoh_grid_sensitivity, discounted_lcoh_grid_sensitivity)
plot_npv_vs_hydrogen_price(hydrogen_price_scenarios, npv_results)
plot_npv_vs_grid_limit(grid_limits_mw, npv_grid_results)
plot_npv_heatmap(npv_df)
plot_minimum_load_sensitivity(pem_sizes_mw, min_load_sensitivity_results)


# =========================================
# FINAL ENGINEERING CONCLUSIONS
# =========================================

print(
    "Hydrogen production reaches a maximum at approximately 2.5 MW PEM "
    "within the investigated sizing range."
)

print(
    "Maximum hydrogen production does not coincide with minimum hydrogen cost."
)

print(
    "Smaller PEM systems achieve higher utilization and lower simplified "
    "annualized LCOH, but recover a substantially smaller fraction of the "
    "available curtailed energy."
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
# - partial-load curve is simplified and literature-based
# - no stack degradation
# - no stack replacement
# - no hydrogen compression
# - no hydrogen storage
# - no real Cyprus electricity market prices
# - no dynamic dispatch based on MCP / DAM prices
#
# Therefore, this is a first-order techno-economic model,
# not a full engineering design or investment-grade feasibility study.


# =========================================
# KEY FINDINGS
# =========================================

# 10 MWp PV plant, Cyprus
# Selected case: 1 MW PEM | 6 MW grid export limit

# Annual H2 production, selected case: 14.62 tonnes/year
# Maximum H2 production among tested PEM sizes:
# approximately 17.94 tonnes/year at 2 MW PEM

# PEM utilization, selected case: 8.91%
# Operating hours: 1,044 h/year
# Equivalent full-load hours: 780.8 h/year

# Simple LCOH: 6.61 €/kg H2
# Discounted LCOH: 10.04 €/kg H2

# NPV @ 6 €/kg H2: -505,996 €

# Break-even H2 sale price:
# approximately 10.04 €/kg under the current assumptions

# Battery round-trip energy retention: 90.0%
# Hydrogen conversion energy retention, LHV: 62.4%
# Hydrogen exergy efficiency: 61.0%


# Hydrogen production peaks near 2 MW PEM under the selected
# curtailment profile. Further PEM oversizing reduces annual H2
# production primarily because the 15% minimum-load threshold
# increases in absolute power terms, causing more curtailed-power
# hours to fall below the electrolyzer operating range.

# Hydrogen-from-curtailment is technically feasible but
# not economically viable under the selected base-case assumptions.
#
# /// END ///
