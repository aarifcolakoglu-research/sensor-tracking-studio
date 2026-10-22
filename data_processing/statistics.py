import pandas as pd
import numpy as np
import os
import sys

_SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SOURCE_ROOT not in sys.path:
    sys.path.insert(0, _SOURCE_ROOT)

import app_paths


def calculate_detailed_errors():
    # --- PROJECT PATHS ---
    data_path = app_paths.data_path("datasets", "metadata_fz_median.csv")
    fit_path = app_paths.equations_file()

    # Reports live in their own folder
    report_dir = app_paths.reports_dir()
    os.makedirs(report_dir, exist_ok=True)
    report_file = os.path.join(report_dir, "sensor_error_report.csv")

    if not os.path.exists(data_path) or not os.path.exists(fit_path):
        print("ERROR: Required source files (median or equations) not found.")
        return

    df = pd.read_csv(data_path)
    fit_df = pd.read_csv(fit_path)

    # Compatibility with older files: identity was one column, no fit_scope.
    for frame in (df, fit_df):
        if "source_file" not in frame.columns:
            frame["source_file"] = frame["sensor_id"]

    # Combined-fit rows (cycle_id=0) have no match in the per-cycle median
    # data; this report measures per-cycle error, so they are excluded.
    if "fit_scope" in fit_df.columns:
        fit_df = fit_df[fit_df["fit_scope"] == "single"]

    # The file holds a fit of each degree per curve. Report both, labelled:
    # without the column the two would appear as duplicate rows for the same
    # sensor and cycle, with no way to tell which polynomial they describe.
    if "degree" not in fit_df.columns:
        fit_df = fit_df.assign(degree=2)

    error_results = []

    # 1. MODEL FITTING ERRORS (RMSE, MAE)
    for _, row in fit_df.iterrows():
        # Filter using the column names equations.py writes.
        # source_file is part of the key: when a sensor has two measurements,
        # the error is not computed against points from the wrong one.
        mask = (df['sensor_id'] == row['sensor_id']) & \
               (df['source_file'] == row['source_file']) & \
               (df['cycle_id'] == row['cycle_id']) & \
               (df['Load/Unload'] == row['phase'])
        subset = df[mask]

        if subset.empty:
            continue

        x = subset['raw_bz_median'].values
        y_true = subset['fz_median'].values

        # Prediction from the equation: fz = a*x^2 + b*x + c
        cubic = row.get('coeff_x3', 0.0) or 0.0
        y_pred = (cubic * (x ** 3) + row['coeff_a'] * (x ** 2)
                  + row['coeff_b'] * x + row['intercept_c'])

        mae = np.mean(np.abs(y_true - y_pred))
        rmse = np.sqrt(np.mean((y_true - y_pred) ** 2))

        error_results.append({
            "sensor_id": row['sensor_id'],
            "source_file": row['source_file'],
            "degree": int(row['degree']),
            "cycle_id": row['cycle_id'],
            "phase": row['phase'],
            "RMSE": rmse,
            "MAE": mae,
            "R2": row['r2_score']
        })

    error_df = pd.DataFrame(error_results)

    # 2. HYSTERESIS ERROR (the gap between Loading and Unloading)
    hysteresis_results = []
    for (s_id, src, c_id), group in df.groupby(['sensor_id', 'source_file', 'cycle_id']):
        fz_min, fz_max = group['fz_median'].min(), group['fz_median'].max()
        fsr = fz_max - fz_min  # Full Scale Range
        if fsr == 0:
            continue

        loading = group[group['Load/Unload'] == 'Loading'][['pz_step', 'fz_median']]
        unloading = group[group['Load/Unload'] == 'Unloading'][['pz_step', 'fz_median']]

        # Line up matching pz_step values
        merged = pd.merge(loading, unloading, on='pz_step', suffixes=('_load', '_unload'))

        if not merged.empty:
            # Absolute difference between the two directions
            abs_diff = np.abs(merged['fz_median_load'] - merged['fz_median_unload'])
            max_h_err = (abs_diff.max() / fsr) * 100
            avg_h_err = (abs_diff.mean() / fsr) * 100

            hysteresis_results.append({
                "sensor_id": s_id,
                "source_file": src,
                "cycle_id": c_id,
                "Avg_Hysteresis_Error_%": avg_h_err,
                "Max_Hysteresis_Error_%": max_h_err
            })

    h_df = pd.DataFrame(hysteresis_results)

    # 3. MERGE AND SAVE
    if not h_df.empty:
        final_report = pd.merge(error_df, h_df,
                                on=['sensor_id', 'source_file', 'cycle_id'], how='left')
    else:
        final_report = error_df

    try:
        final_report.to_csv(report_file, index=False)
        print("-" * 60)
        print(f"SUCCESS: report written -> {report_file}")
    except PermissionError:
        print(f"ERROR: '{report_file}' is locked by another program (Excel?). Please close it.")
        return

    # Print the summary report
    print("")
    print(">>> SENSOR PERFORMANCE SUMMARY (mean values) <<<")
    summary = final_report.groupby(['degree', 'sensor_id']).agg({
        'R2': 'mean',
        'RMSE': 'mean',
        'Avg_Hysteresis_Error_%': 'mean'
    }).rename(columns={'R2': 'Avg_R2', 'RMSE': 'Avg_RMSE'})

    print(summary.to_string())

if __name__ == "__main__":
    calculate_detailed_errors()