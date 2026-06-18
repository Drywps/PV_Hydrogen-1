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

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
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
kwh_per_kg_h2 = 52
water_liters_per_kg_h2 = 9
electricity_price_sell = 0.08
hydrogen_sale_price = 6
pem_capex_per_kw = 1000
project_lifetime_years = 15
opex_fraction = 0.03  # should be re-examined
grid_limits_mw = [8, 7.5, 7, 6.5, 6, 5.5, 5, 4.5, 4, 3.5, 3, 2.5, 2]
pem_capex_scenarios = [700, 1000, 1300]
pem_sizes_mw = [0.5, 1, 2, 3, 3.5, 4, 4.5, 5]

hydrogen_price_scenarios = [4, 6, 8, 10, 12, 13, 14, 15, 16]
battery_round_trip_efficiency = 0.90
h2_lower_heating_value_kwh_per_kg = 33.33
discount_rate = 0.08


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

def calculate_pv_metrics(df):
    annual_energy_mwh = df["P"].sum() / 1_000_000
    capacity_factor = annual_energy_mwh / (10 * 8760)
    annual_energy_kwh = annual_energy_mwh * 1000
    hydrogen_kg = annual_energy_kwh / kwh_per_kg_h2
    annual_water_liters = hydrogen_kg * water_liters_per_kg_h2
    annual_water_m3 = annual_water_liters / 1000
    return (annual_energy_mwh, capacity_factor, hydrogen_kg, annual_water_m3)


# =========================================
# CURTAILMENT FUNCTIONS
# =========================================

def calculate_hourly_curtailment(df, grid_limit_mw):
    grid_limit_w = grid_limit_mw * 1_000_000
    curtailed_power_w = (df["P"] - grid_limit_w).clip(lower=0)
    curtailed_energy_mwh = curtailed_power_w.sum() / 1_000_000
    return curtailed_power_w, curtailed_energy_mwh

# =========================================
# PEM FUNCTIONS
# =========================================

def calculate_monthly_hydrogen(df):
    monthly_h2_kg = (df.groupby("month")["pem_input_w"].sum() / 1_000_000 * 1000 / kwh_per_kg_h2)
    return monthly_h2_kg

def analyze_pem_size(df, pem_size_mw, pem_capex_per_kw):
    pem_size_w = pem_size_mw * 1_000_000
    pem_size_kw = pem_size_mw * 1_000
    pem_capex_eur = pem_size_kw * pem_capex_per_kw
    pem_input_w = df["curtailed_power_w"].clip(upper=pem_size_w)
    pem_energy_mwh = pem_input_w.sum() / 1_000_000
    h2_kg = pem_energy_mwh * 1000 / kwh_per_kg_h2
    utilization = pem_energy_mwh / (pem_size_mw * 8760)
    one_year_capex_intensity = pem_capex_eur / h2_kg
    return (pem_energy_mwh, h2_kg, utilization, pem_capex_eur, one_year_capex_intensity)

def calculate_selected_pem_input(df, selected_pem_mw):
    selected_pem_w = selected_pem_mw * 1_000_000
    pem_input_w = df["curtailed_power_w"].clip(upper=selected_pem_w)
    selected_h2_kg = pem_input_w.sum() / 1_000_000 * 1000 / kwh_per_kg_h2
    return pem_input_w, selected_h2_kg


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
       a simple sum of years.
       At 8% discount rate over 15 years:
         - Annuity factor = 8.56  (vs simple sum = 15.0)
         - Ratio = 15.0 / 8.56 = 1.75
       This means the CAPEX component of LCOH is understated by ~75%.
       Estimated impact on base case (1 MW, 1000 €/kW, 15,141 kg/year):
         Simple LCOH   ~6.38 €/kg
         Discounted LCOH (CAPEX component corrected) ~8.5-9.5 €/kg
         Underestimation: approximately +2 to +3 €/kg

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


def calculate_lcoh_for_capex_scenarios(selected_pem_mw, annual_h2_kg, pem_capex_scenarios):
    lcoh_results = []
    for capex_per_kw in pem_capex_scenarios:
        pem_capex_eur = selected_pem_mw * 1000 * capex_per_kw
        annual_opex = pem_capex_eur * opex_fraction
        lcoh = calculate_simple_lcoh(pem_capex_eur, annual_opex, annual_h2_kg, project_lifetime_years)
        lcoh_results.append(lcoh)
    return lcoh_results


def calculate_lcoh_vs_grid_limit(df, grid_limits_mw, selected_pem_mw):
    results = []
    for grid_limit_mw in grid_limits_mw:
        curtailed_power_w, curtailed_energy_mwh = calculate_hourly_curtailment(df, grid_limit_mw)
        pem_input_w = curtailed_power_w.clip(upper=selected_pem_mw * 1_000_000)
        annual_h2_kg = pem_input_w.sum() / 1_000_000 * 1000 / kwh_per_kg_h2
        pem_capex_eur = selected_pem_mw * 1000 * pem_capex_per_kw
        annual_opex = pem_capex_eur * opex_fraction
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
        captured_energy_mwh = pem_input_w.sum() / 1_000_000
        h2_kg = captured_energy_mwh * 1000 / kwh_per_kg_h2
        annual_revenue = h2_kg * hydrogen_sale_price
        annual_cashflow_for_npv = annual_revenue - annual_opex
        npv = calculate_npv(pem_capex_eur, annual_cashflow_for_npv, discount_rate, project_lifetime_years)
        npv_grid_results.append(npv)
    return npv_grid_results


def calculate_npv_heatmap_data(df, grid_limits_mw, hydrogen_price_scenarios, selected_pem_mw,
        annual_opex, pem_capex_eur, discount_rate, project_lifetime_years, kwh_per_kg_h2):
    # annual_cashflow = revenue - opex only (CAPEX handled as lump sum in calculate_npv)
    npv_matrix = []
    for grid_limit in grid_limits_mw:
        row = []
        curtailed_power_w = (df["P"] - grid_limit * 1_000_000).clip(lower=0)
        pem_input_w = curtailed_power_w.clip(upper=selected_pem_mw * 1_000_000)
        captured_energy_mwh = pem_input_w.sum() / 1_000_000
        h2_kg = captured_energy_mwh * 1000 / kwh_per_kg_h2
        for h2_price in hydrogen_price_scenarios:
            annual_revenue = h2_kg * h2_price
            annual_cashflow_for_npv = annual_revenue - annual_opex
            npv = calculate_npv(pem_capex_eur, annual_cashflow_for_npv, discount_rate, project_lifetime_years)
            row.append(npv)
        npv_matrix.append(row)
    npv_df = pd.DataFrame(npv_matrix, index=grid_limits_mw, columns=hydrogen_price_scenarios)
    return npv_df


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

def plot_lcoh_vs_grid_limit(grid_limits_mw, lcoh_grid_sensitivity):
    fig_number, fig_title = next_fig("LCOH vs Grid Export Limit")
    plt.figure(figsize=(8, 5))
    plt.plot(grid_limits_mw, lcoh_grid_sensitivity, marker="o")
    plt.title(fig_title)
    plt.xlabel("Grid Limit (MW)")
    plt.ylabel("LCOH (€/kg H2)")
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
# =========================================
# MAIN EXECUTION
# =========================================

df = load_pvgis_data(filename)

# ===== PV SYSTEM ANALYSIS =====
(annual_energy_mwh, capacity_factor, hydrogen_kg, annual_water_m3) = calculate_pv_metrics(df)
print(f"Annual Energy (MWh): {annual_energy_mwh:.2f}")
print(f"Capacity Factor (%): {capacity_factor * 100:.2f}")
print(f"Annual Hydrogen Production (kg): {hydrogen_kg:.0f}")
print(f"Annual Water Consumption (m3): {annual_water_m3:.2f}")

print(f"Rows: {len(df)}")
print(f"Total PV Energy (W): {df['P'].sum():.0f}")

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
    h2_from_grid_limit_kg = curtailed_mwh * 1000 / kwh_per_kg_h2
    print(f"  Grid limit: {grid_limit_mw} MW | Curtailed: {curtailed_mwh:.1f} MWh | H2 potential: {h2_from_grid_limit_kg:.0f} kg/year")

# ===== ELECTROLYZER OPERATING HOURS ANALYSIS =====
pem_size_w = selected_pem_mw * 1_000_000
print("\nElectrolyzer Operating Hours Analysis:")
for grid_limit_mw in grid_limits_mw:
    curtailed_power_w, _ = calculate_hourly_curtailment(df, grid_limit_mw)
    pem_input_w = curtailed_power_w.clip(upper=pem_size_w)
    operating_hours = (pem_input_w > 0).sum()
    full_load_hours = pem_input_w.sum() / pem_size_w
    print(f"  Grid limit: {grid_limit_mw} MW | Operating hours: {operating_hours} h | Full-load hours: {full_load_hours:.1f} h")

# ===== RESULTS TABLE =====
results_table["H2 (kg/year)"] = h2_results
results_table["Utilization (%)"] = utilization_results
print(results_table.to_string(index=False))

# ====== BATTERY VS HYDROGEN COMPARISON =====
battery_recovered_mwh = selected_curtailed_mwh * battery_round_trip_efficiency
h2_energy_mwh = selected_h2_kg * h2_lower_heating_value_kwh_per_kg / 1000
battery_energy_retention = battery_recovered_mwh / selected_curtailed_mwh
hydrogen_energy_retention = h2_energy_mwh / selected_curtailed_mwh

print(f"Battery recovered energy (MWh): {battery_recovered_mwh:.2f}")
print(f"Hydrogen stored energy (MWh LHV): {h2_energy_mwh:.2f}")
print(f"Battery energy retention (%): {battery_energy_retention * 100:.1f}")
print(f"Hydrogen energy retention (%): {hydrogen_energy_retention * 100:.1f}")

# ===== ECONOMIC ANALYSIS =====
curtailment_loss_eur = selected_curtailed_mwh * 1000 * electricity_price_sell
hydrogen_revenue_eur = selected_h2_kg * hydrogen_sale_price

pem_capex_eur = selected_pem_mw * 1000 * pem_capex_per_kw

# Annualized Capital Cost (ACC) using Capital Recovery Factor (CRF).
# CRF converts a lump-sum CAPEX into equivalent uniform annual payments
# at the given discount rate, equivalent to a loan repayment schedule.
#
# CRF = r(1+r)^n / ((1+r)^n - 1)
# CRF(8%, 15yr) = 0.08 x 1.08^15 / (1.08^15 - 1) = 0.1168
#
# ACC = CAPEX x CRF
# ACC = 1,000,000 x 0.1168 = 116,830 €/year
#
# Compare with naive straight-line: CAPEX / n = 1,000,000 / 15 = 66,667 €/year
# Straight-line understates true annual capital burden by ~75%.
#
# Used for reporting (simple_net_hydrogen_value) and LCOH only.
# Do NOT pass annualized_pem_capex into annual_cashflow_for_npv —
# CAPEX is handled as a lump sum at year 0 inside calculate_npv().
crf = (discount_rate * (1 + discount_rate) ** project_lifetime_years) / \
      ((1 + discount_rate) ** project_lifetime_years - 1)
annualized_pem_capex = pem_capex_eur * crf

annual_opex = pem_capex_eur * opex_fraction

# Reporting only — not used in NPV calculation
simple_net_hydrogen_value = hydrogen_revenue_eur - annualized_pem_capex
net_hydrogen_value_with_opex = hydrogen_revenue_eur - annualized_pem_capex - annual_opex

# LCOH
simple_lcoh = calculate_simple_lcoh(pem_capex_eur, annual_opex, selected_h2_kg, project_lifetime_years)
lcoh_capex_sensitivity = calculate_lcoh_for_capex_scenarios(selected_pem_mw, selected_h2_kg, pem_capex_scenarios)
lcoh_grid_sensitivity = calculate_lcoh_vs_grid_limit(df, grid_limits_mw, selected_pem_mw)

# ===== BENCHMARK COMPARISON =====
# Curtailment-to-H2 LCOH is compared against literature values
# for dedicated grid-connected green hydrogen production (PV + PEM).
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

print(f"Curtailment electricity value loss (€): {curtailment_loss_eur:,.0f}")
print(f"Hydrogen revenue (€): {hydrogen_revenue_eur:,.0f}")
print(f"Annualized PEM CAPEX / ACC (€): {annualized_pem_capex:,.0f}  [CRF={crf:.4f}]")
print(f"Simple net hydrogen value (€): {simple_net_hydrogen_value:,.0f}")
print(f"Annual OPEX (€): {annual_opex:,.0f}")
print(f"Net hydrogen value with OPEX (€): {net_hydrogen_value_with_opex:,.0f}")
print(f"Simple LCOH (€/kg H2): {simple_lcoh:.2f}")

print("LCOH CAPEX Sensitivity:")
for capex, lcoh in zip(pem_capex_scenarios, lcoh_capex_sensitivity):
    print(f"  CAPEX {capex} €/kW -> LCOH {lcoh:.2f} €/kg H2")

print("\nLCOH Grid Limit Sensitivity:")
for grid_limit, lcoh in zip(grid_limits_mw, lcoh_grid_sensitivity):
    print(f"  Grid limit {grid_limit} MW -> LCOH {lcoh:.2f} €/kg H2")

print(f"Curtailment-to-H2 LCOH (this model):            {simple_lcoh:.2f} €/kg H2")
print(f"Dedicated PV-to-H2 LCOH (literature benchmark): {benchmark_dedicated_lcoh_low:.1f} - {benchmark_dedicated_lcoh_high:.1f} €/kg H2")
print(f"Note: curtailment LCOH reflects low utilization ({utilization_results[1]:.2f}%), not low electricity cost")

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
    df, grid_limits_mw, hydrogen_price_scenarios, selected_pem_mw,
    annual_opex, pem_capex_eur, discount_rate, project_lifetime_years, kwh_per_kg_h2)
print(npv_df.round(0))


# =========================================
# PLOT EXECUTION
# =========================================

plot_hourly_pv_output(df)
plot_two_day_pv_output(df)
pem_vs_hydrogen(pem_sizes_results, h2_results)
pem_vs_utilization(pem_sizes_results, utilization_results)

capex_cols = ["PEM Size (MW)"] + [col for col in results_table.columns if "CAPEX" in col]
plot_capex_intensity(results_table[capex_cols])

monthly_h2_kg = calculate_monthly_hydrogen(df)
plot_monthly_hydrogen(monthly_h2_kg)

plot_lcoh_vs_grid_limit(grid_limits_mw, lcoh_grid_sensitivity)
plot_npv_vs_hydrogen_price(hydrogen_price_scenarios, npv_results)
plot_npv_vs_grid_limit(grid_limits_mw, npv_grid_results)
plot_npv_heatmap(npv_df)


# =========================================
# FINAL ENGINEERING CONCLUSIONS
# =========================================

print("\n--- FINAL ENGINEERING CONCLUSIONS ---")
print(f"Selected system: {selected_pem_mw} MW PEM | {selected_grid_limit_mw} MW grid limit")
print("Hydrogen production saturates above approximately 2 MW PEM.")
print("PEM oversizing beyond 2 MW significantly reduces utilization.")
print("Battery storage retains more energy than hydrogen.")
print("Hydrogen offers long-duration and sector-coupling advantages.")
print("Hydrogen-from-curtailment is technically feasible but economically marginal under moderate curtailment.")


# =========================================
# MODEL LIMITATIONS
# =========================================
#
# This model assumes a constant PEM electrolyzer specific energy consumption
# of 52 kWh/kg H2.
#
# It does not yet model:
# - PEM partial-load efficiency
# - stack degradation
# - stack replacement
# - hydrogen compression
# - hydrogen storage
# - real Cyprus electricity market prices
# - dynamic dispatch based on MCP / DAM prices
#
# Therefore, this is a first-order techno-economic model,
# not a full engineering design or investment-grade feasibility study.


# =========================================
# KEY FINDINGS
# =========================================
#
# 10 MWp PV plant, Cyprus
# Selected Case: 1 MW PEM | 6 MW Grid Export Limit
#
# Annual H2 Production: 15.1 tonnes/year
#   (Note: 17.9 t/year is the curtailment ceiling reached at PEM >= 2 MW,
#    not the output of the selected 1 MW system)
#
# PEM Utilization: 8.99%  (1,127 operating hours/year)
#   This is the critical economic driver — extremely low asset utilization.
#
# Simple LCOH: 6.38 €/kg H2
# (Undiscounted lower bound — see calculate_simple_lcoh docstring for breakdown)
# True LCOH would likely be higher after discounting,
# stack replacement, degradation, compression and storage.
#
# NPV @ 6 €/kg H2: -479,165 €
#
# Break-even H2 price: ~9.5 €/kg
#   (Between 8 €/kg: NPV -219,959 and 10 €/kg: NPV +39,248)
#
# Battery energy retention: 90%
# Hydrogen energy retention: 54%
#
# Hydrogen-from-curtailment is technically feasible but not economically viable under base-case assumptions.
#
# /// END ///
