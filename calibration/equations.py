"""
Fit polynomial equations from the per-step median data.

Two kinds of row are written for every (sensor, measurement, phase):

  * `fit_scope="single"`   -> cycle_id 1..5, one fit per cycle. Needed to show
                              repeatability (the spread between cycles) in the
                              publication work.
  * `fit_scope="combined"` -> cycle_id 0, the median points of all cycles fitted
                              in one pool. This is the default used for live
                              measurement; seeing roughly 5x more points makes
                              it less noisy than any single cycle.

Each is fitted against two targets: the force the sensor was under, and how
far the indenter had travelled into the elastomer. They are different
functions of the same raw value - one cannot be derived from the other - and
each has its own hysteresis, so each gets its own loading and unloading curve.

Every one of those is fitted at both degree 2 and degree 3, and both are
written. Producing both costs a second and means the choice can be made, and
changed, without re-running a calibration that takes minutes - the same reason
the per-cycle fits are kept alongside the combined one.

The polynomial is

    fz = coeff_x3*bz^3 + coeff_a*bz^2 + coeff_b*bz + intercept_c

with coeff_x3 zero on the degree-2 rows. The three original names are kept as
they were so that a consumer written against the old file still reads the
degree-2 rows correctly.

cycle_id=0 is a deliberate choice: existing consumers (statistics.py,
visualise_fitting.py) find no cycle 0 in the median data, so they skip the
combined rows instead of breaking.
"""

import os
import sys

import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import PolynomialFeatures
from sklearn.metrics import r2_score

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app_paths import archive_dir, data_path
from calibration.archive import archive_dataframe
from calibration.sensor_ids import parse_raw_filename

# cycle_id value used by combined-fit rows
COMBINED_CYCLE_ID = 0

# Degrees fitted for every curve. Both are written; the application picks.
DEGREES = (2, 3)

# What the polynomial predicts from the raw value. Both are measured during the
# same calibration sweep, so both are fitted from the same points.
TARGET_FORCE = "force"
TARGET_DISPLACEMENT = "displacement"

# target -> (column in the median file, unit recorded alongside)
TARGETS = {
    TARGET_FORCE: ("fz_median", "N"),
    TARGET_DISPLACEMENT: ("pz_median", "mm"),
}

OUTPUT_COLUMNS = [
    "sensor_id", "set_id", "position", "source_file", "collected_at",
    "cycle_id", "fit_scope", "phase", "degree", "target", "unit",
    "coeff_x3", "coeff_a", "coeff_b", "intercept_c",
    "r2_score", "n_points",
    "fz_max_n", "pz_max_mm", "raw_bz_min", "raw_bz_max",
    "equation",
]


def _fit_polynomial(x, y, degree: int):
    """
    Fit fz as a polynomial in bz of the given degree.

    Returns (x3, a, b, c, r2) with x3 zero below degree 3, or None when there
    are fewer points than the degree needs.
    """
    if len(x) < degree + 1:
        return None

    X = x.reshape(-1, 1)
    X_poly = PolynomialFeatures(degree=degree).fit_transform(X)

    model = LinearRegression()
    model.fit(X_poly, y)
    r2 = r2_score(y, model.predict(X_poly))

    # PolynomialFeatures emits [bias, x, x^2, x^3]; the bias sits in
    # intercept_, so coef_[0] is always zero.
    coeffs = model.coef_
    x3 = coeffs[3] if degree >= 3 else 0.0
    a = coeffs[2] if degree >= 2 else 0.0
    b = coeffs[1]
    c = model.intercept_

    return float(x3), float(a), float(b), float(c), float(r2)


def _equation_text(x3, a, b, c) -> str:
    cubic = f"({x3:.2e})*bz^3 + " if x3 else ""
    return f"fz = {cubic}({a:.2e})*bz^2 + ({b:.2e})*bz + ({c:.4f})"


def _result_row(sensor_id, source_file, cycle_id, fit_scope, phase, degree,
                target, unit, fit, n_points):
    x3, a, b, c, r2 = fit
    return {
        "sensor_id": sensor_id,
        "source_file": source_file,
        "cycle_id": cycle_id,
        "fit_scope": fit_scope,
        "phase": phase,
        "degree": int(degree),
        "target": target,
        "unit": unit,
        "coeff_x3": x3,
        "coeff_a": a,
        "coeff_b": b,
        "intercept_c": c,
        "r2_score": r2,
        "n_points": int(n_points),
        "equation": _equation_text(x3, a, b, c),
    }


def fit_sensor_models(degrees=DEGREES, archive: bool = True) -> pd.DataFrame | None:
    input_file = data_path("datasets", "metadata_fz_median.csv")
    output_file = data_path("datasets", "sensor_fit_equations.csv")

    if not os.path.exists(input_file):
        print(f"ERROR: {input_file} not found. Run the earlier pipeline stages first.")
        return None

    df = pd.read_csv(input_file)

    # Compatibility with an older median file, where identity was one column.
    if "source_file" not in df.columns:
        df["source_file"] = df["sensor_id"]

    degrees = tuple(degrees)
    targets = {name: spec for name, spec in TARGETS.items()
               if spec[0] in df.columns}
    missing = sorted(set(TARGETS) - set(targets))
    if missing:
        print(f"WARNING: {', '.join(missing)} not in the median file; "
              f"re-run stage 2 to fit it.")

    print(f"Fitting degree {', '.join(str(d) for d in degrees)} against "
          f"{', '.join(sorted(targets))}...")

    results = []

    for target, (column, unit) in sorted(targets.items()):
        for degree in degrees:
            # 1) Per-cycle fits, for publication and auditing
            for (s_id, src, c_id, phase), grp in df.groupby(
                    ["sensor_id", "source_file", "cycle_id", "Load/Unload"],
                    sort=False):
                fit = _fit_polynomial(grp["raw_bz_median"].values,
                                      grp[column].values, degree)
                if fit is not None:
                    results.append(_result_row(s_id, src, c_id, "single", phase,
                                               degree, target, unit, fit, len(grp)))

            # 2) Combined fit, the default used for live measurement
            for (s_id, src, phase), grp in df.groupby(
                    ["sensor_id", "source_file", "Load/Unload"], sort=False):
                fit = _fit_polynomial(grp["raw_bz_median"].values,
                                      grp[column].values, degree)
                if fit is not None:
                    results.append(_result_row(s_id, src, COMBINED_CYCLE_ID,
                                               "combined", phase, degree,
                                               target, unit, fit, len(grp)))

    if not results:
        print("ERROR: No group could be fitted.")
        return None

    fit_df = pd.DataFrame(results)

    # Measured range per sensor and measurement. The engine needs it for two
    # things: capping the Y-axis choices at what the sensor was actually
    # calibrated for, and sizing the direction threshold used by the
    # hysteresis-aware phase mode. Carrying it here keeps the equations file
    # self-sufficient.
    position_column = "pz_median" if "pz_median" in df.columns else "pz_step"
    ranges = df.groupby(["sensor_id", "source_file"]).agg(
        fz_max_n=("fz_median", "max"),
        pz_max_mm=(position_column, "max"),
        raw_bz_min=("raw_bz_median", "min"),
        raw_bz_max=("raw_bz_median", "max"),
    ).reset_index()
    fit_df = fit_df.merge(ranges, on=["sensor_id", "source_file"], how="left")

    # Identity fields (set_id / position / collected_at) come from the filename
    meta = fit_df["source_file"].drop_duplicates().to_frame()
    parsed = meta["source_file"].apply(parse_raw_filename)
    meta["set_id"] = [p["set_id"] for p in parsed]
    meta["position"] = [p["position"] for p in parsed]
    meta["collected_at"] = [p["collected_at"] for p in parsed]
    fit_df = fit_df.merge(meta, on="source_file", how="left")

    fit_df = fit_df[OUTPUT_COLUMNS].sort_values(
        ["set_id", "position", "collected_at", "target", "degree",
         "cycle_id", "phase"],
        kind="stable",
    ).reset_index(drop=True)

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    fit_df.to_csv(output_file, index=False)

    if archive:
        archive_dataframe(fit_df, archive_dir(), "sensor_fit_equations")

    combined = fit_df[fit_df["fit_scope"] == "combined"]

    print("-" * 60)
    print(f"SUCCESS: equations written -> {output_file}")
    print(f"Sensors processed  : {fit_df['sensor_id'].nunique()}")
    print(f"Measurement files  : {fit_df['source_file'].nunique()}")
    print(f"Total rows         : {len(fit_df)} "
          f"(per-cycle {len(fit_df) - len(combined)}, combined {len(combined)})")
    for target in sorted(targets):
        for degree in degrees:
            rows = combined[(combined["degree"] == degree) &
                            (combined["target"] == target)]
            if rows.empty:
                continue
            print(f"  {target:<12} degree {degree}: mean R2 "
                  f"{rows['r2_score'].mean():.6f}, min {rows['r2_score'].min():.6f}")
    print(f"Force range        : {combined['fz_max_n'].min():.2f} - "
          f"{combined['fz_max_n'].max():.2f} N "
          f"({combined['fz_max_n'].min() * 101.97:.0f} - "
          f"{combined['fz_max_n'].max() * 101.97:.0f} gf)")
    print(f"Mean R2 (combined) : {combined['r2_score'].mean():.6f}")
    print(f"Min R2 (combined)  : {combined['r2_score'].min():.6f}")
    print("-" * 60)

    return fit_df


if __name__ == "__main__":
    fit_sensor_models()
