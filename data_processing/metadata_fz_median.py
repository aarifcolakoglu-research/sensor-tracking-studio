import pandas as pd
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app_paths import archive_dir, data_path
from calibration.archive import archive_dataframe
from calibration.sensor_ids import parse_raw_filename


def generate_median_metadata():
    # --- PROJECT PATHS ---
    # Input: the output of the previous stage
    input_file = data_path("datasets", "metadata_fz.csv")

    # Output: the data the equation fitting stage consumes
    output_file = data_path("datasets", "metadata_fz_median.csv")
    archive_directory = archive_dir()

    if not os.path.exists(input_file):
        print(f"ERROR: {input_file} not found. Run metadata_fz_v2.py first.")
        return

    print(f"Reading: {input_file}")
    df = pd.read_csv(input_file)

    # Grouping keys.
    # source_file is part of the key: when a sensor has two measurements, the
    # two are summarised separately instead of being mixed together.
    group_cols = ["sensor_id", "source_file", "cycle_id", "Load/Unload", "pz_step"]
    if "source_file" not in df.columns:
        # Compatibility with an older metadata_fz.csv, where identity was one column.
        df["source_file"] = df["sensor_id"]

    # Columns the statistics are computed over.
    #
    # pz is the indenter's travel into the elastomer, and the second thing the
    # sensor is calibrated against. It is grouped by pz_step, the same value
    # snapped to the 0.1 mm grid; summarising the measured pz as well costs
    # nothing and keeps the fit on what the rig actually did. The two differ by
    # about a micron, so this changes no result - it just stops the fit
    # depending on a grouping key being the physical quantity.
    target_cols = ["fz", "pz", "raw_bz"]

    print("Computing statistics per step, preserving time order within Loading/Unloading...")

    # sort=False is critical: it keeps the order the steps were recorded in
    # (0.0 -> 1.8 -> 0.0) instead of sorting them numerically.
    summary_df = df.groupby(group_cols, sort=False)[target_cols].agg(['min', 'max', 'median', 'mean'])

    # Flatten the MultiIndex columns (fz_median, raw_bz_median, ...)
    summary_df.columns = [f"{col}_{stat}" for col, stat in summary_df.columns]

    # Turn the index (group_cols) back into ordinary columns
    summary_df = summary_df.reset_index()

    # Add identity fields for grouping and selection. This file is small
    # (~37 rows per sensor) so carrying them here is cheap; they are left out
    # of the 1 GB metadata_fz.csv on purpose.
    meta = summary_df["source_file"].drop_duplicates().to_frame()
    parsed = meta["source_file"].apply(parse_raw_filename)
    meta["set_id"] = [p["set_id"] for p in parsed]
    meta["position"] = [p["position"] for p in parsed]
    meta["collected_at"] = [p["collected_at"] for p in parsed]
    summary_df = summary_df.merge(meta, on="source_file", how="left")

    lead = ["sensor_id", "set_id", "position", "source_file", "collected_at"]
    summary_df = summary_df[lead + [c for c in summary_df.columns if c not in lead]]

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    summary_df.to_csv(output_file, index=False)
    archive_dataframe(summary_df, archive_directory, "metadata_fz_median")

    print("-" * 60)
    print(f"SUCCESS: {output_file} created.")
    print(f"Summary: {len(summary_df)} stable step records.")

    # Small preview to confirm the ordering
    print("\n[Processing order preview]")
    print(summary_df[["sensor_id", "cycle_id", "Load/Unload", "pz_step",
                      "fz_median", "raw_bz_median"]].head(20))
    print("")
    print(f"Unique sensors: {summary_df['sensor_id'].nunique()} | "
          f"measurement files: {summary_df['source_file'].nunique()}")


if __name__ == "__main__":
    generate_median_metadata()
