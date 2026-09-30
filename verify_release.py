"""Verify the published PVSYST screening release after running main.py."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
TABLES = ROOT / "results" / "tables"


def check(condition, message):
    if not bool(condition):
        raise AssertionError(message)


def read_table(name):
    return pd.read_csv(TABLES / name)


def main():
    checks = []
    manifest = json.loads((ROOT / "data" / "input_manifest.json").read_text())
    for item in manifest["inputs"]:
        source = ROOT / item["path"]
        if not source.is_file():
            raise FileNotFoundError(
                f"Required local input missing: {source}. See README.md reproduction instructions."
            )
        actual = hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest()
        check(actual == item["sha256"], "Input hash differs: " + item["path"])
    dam = pd.read_excel(
        ROOT / "data" / "DSMK_DAM_2025-10-01_to_2026-09-24.xlsx",
        sheet_name="DAM_Data",
    )
    timestamps = pd.to_datetime(dam["Timestamp"])
    check(len(dam) == 17230 and timestamps.nunique() == 17230, "DAM coverage/uniqueness")
    check(timestamps.dt.normalize().nunique() == 359, "DAM day coverage")
    check(timestamps.min() == pd.Timestamp("2025-10-01"), "DAM start")
    check(timestamps.max() == pd.Timestamp("2026-09-24 23:30"), "DAM end")
    checks.append("PASS: four input hashes and 17,230 unique DAM intervals over 359 days")

    pv = read_table("pv_source_comparison_summary.csv")
    pvsyst = pv[pv["PV Source"] == "PVsyst TMY 5.3"].iloc[0]
    raw = pd.read_csv(ROOT / "data" / "PVsyst_TMY_5.3.csv", skiprows=10)
    ac = pd.to_numeric(raw["E_Grid"], errors="coerce").dropna()
    check(len(ac) == 8760, "PVsyst hourly coverage")
    expected_pv_mwh = ac.clip(lower=0).sum() / 1000
    repairs = read_table("pvsyst_source_repairs.csv")
    expected_pv_mwh += (
        repairs["Assumed repaired power (W)"]
        - repairs["Original power (W)"].clip(lower=0)
    ).sum() / 1e6
    check(np.isclose(pvsyst["Annual PV Energy (MWh)"], expected_pv_mwh), "PV unit conversion")
    checks.append("PASS: 8,760-hour PVSYST input, clipping and disclosed source repairs")

    summary = read_table("final_screening_summary.csv")
    b_cases = read_table("strategy_b_reporting_cases.csv")
    sweep = read_table("strategy_b_dam_price_cap_sensitivity.csv")
    check(np.allclose(
        sweep["Total PEM Energy (MWh)"],
        sweep["PV Energy to PEM (MWh)"] + sweep["Purchased Energy (MWh)"],
        atol=1e-8, rtol=0,
    ), "Strategy B source-energy balance")
    check(sweep["Purchased Energy (MWh)"].ge(0).all(), "Negative grid imports")
    check(sweep["Utilization (%)"].between(0, 100).all(), "Utilization bounds")
    check(np.allclose(
        sweep["Utilization (%)"],
        100 * sweep["Total PEM Energy (MWh)"] / (1.6 * 8760),
    ), "Utilization uses a different energy/time basis")
    fixed = sweep[np.isclose(sweep["Baseline Load (%)"], 20)]
    economic = b_cases[b_cases["Case"] == "Economic selection at fixed baseline"].iloc[0]
    check(np.isclose(economic["LCOH (€/kg H2)"], fixed["LCOH (€/kg H2)"].min()),
          "Headline cap is not minimum LCOH at fixed baseline")
    raised = b_cases[b_cases["Case"].str.startswith("Raised 30%")].iloc[0]
    original = b_cases[b_cases["Case"].str.startswith("Original 20%")].iloc[0]
    check(np.isclose(raised["Contracted Import Capacity (kW)"], 480), "Raised baseline capacity")
    check(raised["Purchased Energy (MWh)"] > original["Purchased Energy (MWh)"],
          "Raised grid-active comparator does not increase import")
    checks.append("PASS: Strategy B energy, bounds, conditional selection and raised baseline")

    pv_only = b_cases[b_cases["Case"] == "PV-priority reference without grid service"].iloc[0]
    for column in ["Purchased Energy (MWh)", "Contracted Import Capacity (kW)",
                   "Demand Charge (€/year)", "Fixed Supply + Metering Charge (€/year)"]:
        check(np.isclose(pv_only[column], 0), "PV-only reference retains import charge/energy")
    checks.append("PASS: PV-priority reference avoids import-service charges")

    annuity = sum(1 / 1.08 ** year for year in range(1, 16))
    capex = 1600 * 1970
    for _, row in b_cases.iterrows():
        h2 = row["H2 (kg/year)"]
        cost = row["Total Annual Operating + Energy Cost (€/year)"]
        expected_lcoh = (capex / annuity + cost) / h2
        expected_npv = -capex + annuity * (7 * h2 - cost)
        check(np.isclose(row["LCOH (€/kg H2)"], expected_lcoh), "B LCOH cash-flow boundary")
        check(np.isclose(row["NPV (€)"], expected_npv, atol=0.01), "B NPV cash-flow boundary")
    checks.append("PASS: discounted LCOH/NPV cash flows without double-counted CAPEX")

    c = read_table("strategy_c_bess_best_nonzero_case.csv").iloc[0]
    all_c = read_table("strategy_c_bess_economic_sensitivity.csv")
    central_c = all_c[
        (all_c["Configuration type"] == "268 kW duration sensitivity")
        & np.isclose(all_c["CAPEX scale factor"], 1.25)
        & np.isclose(all_c["Residual-curtailment opportunity cost (EUR/MWh)"], 0)
    ]
    check(np.isclose(
        c["Incremental NPV vs Strategy B (EUR)"],
        central_c["Incremental NPV vs Strategy B (EUR)"].max(),
    ), "Non-zero BESS case selection")
    expected_value_adding = c["Incremental NPV vs Strategy B (EUR)"] >= 0
    check(c["Value adding"] == ("YES" if expected_value_adding else "NO"), "BESS value label")
    check(c["Overall economic choice"] == (c["Configuration"] if expected_value_adding else "No BESS"),
          "BESS overall economic choice")
    check(np.isclose(
        c["Combined Strategy C NPV (EUR)"],
        economic["NPV (€)"] + c["Incremental NPV vs Strategy B (EUR)"],
    ), "Combined BESS/PEM NPV")
    expected_increment = -c["BESS CAPEX (EUR2020)"] + annuity * (
        7 * c["Incremental H2 vs Strategy B (kg/year)"]
        - c["BESS fixed OPEX (EUR2020/year)"]
        - c["Battery opportunity cost (EUR/year)"]
    )
    check(np.isclose(c["Incremental NPV vs Strategy B (EUR)"], expected_increment), "BESS cash flow")
    physical = read_table("strategy_c_bess_physical_sensitivity.csv")
    check(np.allclose(physical["Initial cyclic SOC (kWh)"], physical["Final cyclic SOC (kWh)"],
                      atol=1e-5), "Non-cyclic battery SOC")
    check(physical["Battery losses incl. year-boundary SOC (MWh/year)"].ge(-1e-8).all(),
          "Negative battery losses")
    check(not (TABLES / "strategy_c_bess_selected_design.csv").exists(), "Obsolete BESS CSV remains")
    checks.append("PASS: BESS choice labels, incremental economics, losses and cyclic SOC")

    lifecycle = read_table("stack_lifecycle_sensitivity.csv")
    check(len(lifecycle) == 9 and lifecycle["Strategy"].nunique() == 3, "Lifecycle matrix incomplete")
    for _, rows in lifecycle.groupby("Strategy"):
        check(set(rows["Degradation rate (microV/h)"]) == {7, 23, 50}, "Lifecycle rate coverage")
    check(lifecycle["Replacement count"].ge(0).all(), "Negative replacements")
    check(lifecycle["Model validity warning"].notna().all(), "Missing lifecycle validity warning")
    checks.append("PASS: all nine lifecycle cases and explicit validity warnings")

    report = (ROOT / "docs" / "FINAL_SCREENING_REPORT.md").read_text()
    for _, row in summary.iterrows():
        check(f"{row['H2 (kg/year)']:,.0f}" in report, "Report H2 differs from CSV")
        check(f"{row['LCOH (EUR/kg)']:.3f}" in report, "Report LCOH differs from CSV")
    check("Value adding = NO" in report, "Report omits No-BESS decision")
    checks.append("PASS: final report matches central result CSVs")

    result = "\n".join(["v1.9.1-final-screening", *checks,
                        "Internal consistency verified. Experimental/OEM validation is outside this check."]) + "\n"
    (ROOT / "results" / "verification.txt").write_text(result, encoding="utf-8")
    print(result, end="")


if __name__ == "__main__":
    main()
