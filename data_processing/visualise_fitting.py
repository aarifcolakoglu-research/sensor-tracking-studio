import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os
import sys

_SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SOURCE_ROOT not in sys.path:
    sys.path.insert(0, _SOURCE_ROOT)

import app_paths


def visualize_sensor_fitting(save_plots: bool = True, show_plots: bool = False,
                             degree: int = 2):
    """
    Plot the median points and the fitted curves for every measurement.

    Per-cycle fits are drawn as thin dashed lines and the combined fit (the
    equation used for live measurement) as a thick line, so how closely the two
    agree can be read straight off the plot.

    `degree` picks which polynomial to draw. The equations file holds one of
    each, and without choosing, whichever happened to sort first would be
    plotted with nothing on the figure to say which it was.
    """
    # --- PROJECT PATHS ---
    data_path = app_paths.data_path("datasets", "metadata_fz_median.csv")
    fit_path = app_paths.equations_file()

    # Where the plots are saved
    plots_dir = app_paths.visualizations_dir()

    if not os.path.exists(data_path) or not os.path.exists(fit_path):
        print("ERROR: Required CSV files not found. Produce the equations first.")
        return

    if save_plots:
        os.makedirs(plots_dir, exist_ok=True)

    df = pd.read_csv(data_path)
    fit_df = pd.read_csv(fit_path)

    # Compatibility with older files: identity was one column, no fit_scope.
    for frame in (df, fit_df):
        if "source_file" not in frame.columns:
            frame["source_file"] = frame["sensor_id"]
    if "fit_scope" not in fit_df.columns:
        fit_df["fit_scope"] = "single"
    if "degree" in fit_df.columns:
        fit_df = fit_df[fit_df["degree"] == int(degree)]

    # When a sensor has several measurements, the collection date goes into the
    # filename so the plots do not overwrite each other.
    measurements_per_sensor = df.groupby("sensor_id")["source_file"].nunique()

    # One figure per measurement
    for (s_id, src), s_data in df.groupby(["sensor_id", "source_file"], sort=True):
        s_fits = fit_df[(fit_df["sensor_id"] == s_id) & (fit_df["source_file"] == src)]

        plt.figure(figsize=(12, 7))

        for c_id in sorted(s_data["cycle_id"].unique()):
            for phase in ["Loading", "Unloading"]:
                subset = s_data[(s_data["cycle_id"] == c_id) &
                                (s_data["Load/Unload"] == phase)]
                if subset.empty:
                    continue

                # Circle marker for Loading, square for Unloading
                plt.scatter(subset["raw_bz_median"], subset["fz_median"],
                            label=f"Cycle {c_id} {phase}", alpha=0.5, s=30,
                            marker='o' if phase == "Loading" else 's')

                # Per-cycle fit curve (thin dashed)
                row = s_fits[(s_fits["cycle_id"] == c_id) &
                             (s_fits["phase"] == phase) &
                             (s_fits["fit_scope"] == "single")]
                if not row.empty and len(subset) > 1:
                    _plot_curve(row.iloc[0], subset["raw_bz_median"],
                                linestyle="--", linewidth=1.0, color="black", alpha=0.35)

        # Combined fit (the equation used live) - thick and coloured
        for phase, color in (("Loading", "tab:red"), ("Unloading", "tab:blue")):
            row = s_fits[(s_fits["fit_scope"] == "combined") & (s_fits["phase"] == phase)]
            phase_data = s_data[s_data["Load/Unload"] == phase]
            if row.empty or len(phase_data) < 2:
                continue
            r2 = row.iloc[0]["r2_score"]
            _plot_curve(row.iloc[0], phase_data["raw_bz_median"],
                        linestyle="-", linewidth=2.2, color=color, alpha=0.9,
                        label=f"Combined {phase} (R2={r2:.5f})")

        plt.title(f"Sensor ID: {s_id}    |    Measurement: {src}\n"
                  f"Raw BZ vs FZ (median points, degree-{degree} fits)")
        plt.xlabel("Raw BZ (ADC / Baseline Corrected)")
        plt.ylabel("Reference Force (FZ)")
        plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize='small')
        plt.grid(True, linestyle=':', alpha=0.6)
        plt.tight_layout()

        if save_plots:
            name = s_id if measurements_per_sensor.get(s_id, 1) == 1 else f"{s_id}__{src[:19]}"
            save_path = os.path.join(plots_dir, f"plot_d{degree}_{name}.png")
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
            print(f"  [SAVED] {os.path.basename(save_path)}")

        if show_plots:
            plt.show()

        plt.close()

    if save_plots:
        print("-" * 60)
        print(f"SUCCESS: all plots written -> {plots_dir}")


def _plot_curve(fit_row, x_values, **plot_kwargs):
    """Draw a smooth curve from one fit row: fz = a*bz^2 + b*bz + c."""
    x3 = fit_row.get("coeff_x3", 0.0) or 0.0
    a = fit_row["coeff_a"]
    b = fit_row["coeff_b"]
    c = fit_row["intercept_c"]
    x_range = np.linspace(x_values.min(), x_values.max(), 100)
    plt.plot(x_range,
             x3 * (x_range ** 3) + a * (x_range ** 2) + b * x_range + c,
             **plot_kwargs)


if __name__ == "__main__":
    # With 37 sensors show_plots=True opens 37 windows, so the default is to
    # save only. Pass show_plots=True to inspect them one by one.
    visualize_sensor_fitting(save_plots=True, show_plots=False, degree=2)
