"""
How well the fitted curves hold up at low force.

R2 is computed over the whole range, where a sensor reaches around 1000 gf, so
an error of 20 gf near zero barely moves it. A curve can therefore report
R2 = 0.996 and still be wrong by a fifth of a gram-force reading at the bottom
of its range - which is where a light touch is measured.

This script reports, per sensor and phase:

  * the offset at the sensor's own zero: what the equation predicts at the raw
    value recorded under no load. Ideally zero.
  * the worst error over the low region, taken as every calibration step below
    10% of that sensor's full scale.
  * the same figures for two alternatives, so the cost of the quadratic is
    measured rather than assumed: a cubic, and a quadratic anchored at the
    sensor's own zero - fitted in (raw - raw_at_zero) with no intercept, so it
    passes through no-load by construction.

  * the error over the whole range for each, because a curve that fixes the
    bottom by spoiling the top is not an improvement.

Writes reports/low_end_accuracy.csv and prints a summary.

    python data_processing/low_end_accuracy.py
"""

import os
import sys

import numpy as np
import pandas as pd

_SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SOURCE_ROOT not in sys.path:
    sys.path.insert(0, _SOURCE_ROOT)

import app_paths

GF_PER_N = 101.97

# What counts as the low region: steps below this share of full scale
LOW_REGION = 0.10


def _fit(x, y, degree):
    """Least squares polynomial, highest power first, as numpy returns it."""
    return np.polyfit(x, y, degree)


def _fit_anchored(x, y, x_zero, degree):
    """
    Least squares through the origin in (x - x_zero).

    No constant term, so the curve gives exactly zero at no load. The offset
    the plain fits leave there is the single largest low-force error, and it
    is the one thing about the curve that is known in advance.
    """
    u = x - x_zero
    design = np.vstack([u ** power for power in range(1, degree + 1)]).T
    coeffs, *_ = np.linalg.lstsq(design, y, rcond=None)
    return coeffs


def _anchored_predict(x, x_zero, coeffs):
    u = x - x_zero
    return sum(c * u ** (i + 1) for i, c in enumerate(coeffs))


def _errors(x, y, coeffs, low_mask):
    """(offset at the lowest point, worst error over the low region) in gf."""
    predicted = np.polyval(coeffs, x)
    residual = (predicted - y) * GF_PER_N

    offset = residual[np.argmin(x)]
    worst = residual[low_mask][np.argmax(np.abs(residual[low_mask]))] if low_mask.any() else 0.0
    return float(offset), float(worst)


def analyse():
    median_file = app_paths.data_path("datasets", "metadata_fz_median.csv")
    if not os.path.exists(median_file):
        print(f"ERROR: {median_file} not found. Run the calibration first.")
        return None

    med = pd.read_csv(median_file)
    rows = []

    # The combined fit pools every cycle, so the points are grouped the same way
    for (sensor_id, source_file, phase), grp in med.groupby(
            ["sensor_id", "source_file", "Load/Unload"], sort=True):

        x = grp["raw_bz_median"].to_numpy(dtype=float)
        y = grp["fz_median"].to_numpy(dtype=float)
        if len(x) < 4:
            continue

        full_scale = y.max()
        low_mask = y < full_scale * LOW_REGION

        quad = _fit(x, y, 2)
        cubic = _fit(x, y, 3)
        anchored = _fit_anchored(x, y, x.min(), 2)

        q_offset, q_worst = _errors(x, y, quad, low_mask)
        c_offset, c_worst = _errors(x, y, cubic, low_mask)

        a_res = (_anchored_predict(x, x.min(), anchored) - y) * GF_PER_N
        a_worst = float(a_res[low_mask][np.argmax(np.abs(a_res[low_mask]))]) if low_mask.any() else 0.0

        rmse = lambda r: float(np.sqrt(np.mean(r ** 2)))
        q_rmse = rmse((np.polyval(quad, x) - y) * GF_PER_N)
        c_rmse = rmse((np.polyval(cubic, x) - y) * GF_PER_N)
        a_rmse = rmse(a_res)

        rows.append({
            "sensor_id": sensor_id,
            "set_id": grp["set_id"].iloc[0] if "set_id" in grp else "",
            "phase": phase,
            "full_scale_gf": full_scale * GF_PER_N,
            "raw_at_zero": x.min(),
            "n_points": len(x),
            "low_steps": int(low_mask.sum()),
            "quad_zero_offset_gf": q_offset,
            "quad_worst_low_gf": q_worst,
            "quad_zero_pct_fs": q_offset / (full_scale * GF_PER_N) * 100,
            "cubic_zero_offset_gf": c_offset,
            "cubic_worst_low_gf": c_worst,
            "cubic_zero_pct_fs": c_offset / (full_scale * GF_PER_N) * 100,
            "anchored_zero_offset_gf": 0.0,
            "anchored_worst_low_gf": a_worst,
            "quad_rmse_gf": q_rmse,
            "cubic_rmse_gf": c_rmse,
            "anchored_rmse_gf": a_rmse,
        })

    report = pd.DataFrame(rows).sort_values(["set_id", "sensor_id", "phase"])

    report_file = os.path.join(app_paths.reports_dir(), "low_end_accuracy.csv")
    os.makedirs(app_paths.reports_dir(), exist_ok=True)
    report.to_csv(report_file, index=False)

    _summarise(report, report_file)
    return report


def _summarise(report, report_file):
    loading = report[report["phase"] == "Loading"]

    print("=" * 74)
    print("ZERO OFFSET AND LOW-END ERROR")
    print("=" * 74)
    print(f"{len(report)} curves over {report['sensor_id'].nunique()} sensors, "
          f"low region = below {LOW_REGION:.0%} of full scale")

    print("\nQuadratic, offset at the sensor's own zero (gf), Loading curve:")
    _distribution(loading["quad_zero_offset_gf"])

    print("\nQuadratic, worst error over the low region (gf), Loading curve:")
    _distribution(loading["quad_worst_low_gf"].abs())

    print("\nCubic on the same points, offset at zero (gf):")
    _distribution(loading["cubic_zero_offset_gf"])

    print("\nCubic, worst error over the low region (gf):")
    _distribution(loading["cubic_worst_low_gf"].abs())

    print("\nQuadratic anchored at the sensor's zero, worst low-end error (gf):")
    _distribution(loading["anchored_worst_low_gf"].abs())

    print("\nError over the whole range, RMSE (gf):")
    for name, column in (("quadratic", "quad_rmse_gf"),
                         ("cubic", "cubic_rmse_gf"),
                         ("anchored", "anchored_rmse_gf")):
        values = loading[column]
        print(f"    {name:<10} median {values.median():>6.1f}   "
              f"max {values.max():>6.1f}")

    for name, column in (("Cubic", "cubic_worst_low_gf"),
                         ("Anchored quadratic", "anchored_worst_low_gf")):
        better = (loading[column].abs() < loading["quad_worst_low_gf"].abs()).sum()
        print(f"\n{name} is closer at the low end for "
              f"{better} of {len(loading)} sensors.")

    print("\nWorst ten by low-end error (Loading, quadratic):")
    worst = loading.reindex(
        loading["quad_worst_low_gf"].abs().sort_values(ascending=False).index).head(10)
    print(f"{'sensor':<14}{'FS gf':>8}{'zero gf':>10}{'worst gf':>10}"
          f"{'% FS':>8}{'cubic worst':>13}")
    for _, r in worst.iterrows():
        print(f"{r.sensor_id:<14}{r.full_scale_gf:>8.0f}{r.quad_zero_offset_gf:>10.1f}"
              f"{r.quad_worst_low_gf:>10.1f}{abs(r.quad_worst_low_gf)/r.full_scale_gf*100:>8.1f}"
              f"{r.cubic_worst_low_gf:>13.1f}")

    print(f"\nFull table: {report_file}")


def _distribution(series):
    print(f"    min {series.min():>7.1f}   median {series.median():>7.1f}   "
          f"mean {series.mean():>7.1f}   max {series.max():>7.1f}")


if __name__ == "__main__":
    analyse()
