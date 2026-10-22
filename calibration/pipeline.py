"""
Calibration pipeline: from raw data to equations in one call.

Three stages run in order and each stage output is written to disk:

    input_raw_data/*.csv
        |  stage 1: step / cycle / phase labelling
        v
    datasets/metadata_fz.csv                (~1 GB for 37 sensors)
        |  stage 2: per-step min/max/median/mean
        v
    datasets/metadata_fz_median.csv         (~1.5 MB)
        |  stage 3: polynomial fit
        v
    datasets/sensor_fit_equations.csv       (~100 KB)

Intermediate outputs are kept on purpose: the publication work reads the
per-step distribution and the between-cycle spread from those files. Every
stage also leaves a timestamped copy under `datasets/archive/`, so an earlier
run is never overwritten. Only the newest few copies are kept; see
calibration/archive.py.

Usage:
    python -m calibration.pipeline            # whole pipeline
    python -m calibration.pipeline --from 2   # start at stage 2
"""

from __future__ import annotations

import os
import sys
import time
from typing import Callable, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app_paths import datasets_dir, raw_data_dir

DATASETS_DIR = datasets_dir()
RAW_DIR = raw_data_dir()

STAGE_NAMES = {
    1: "Labelling raw data (step / cycle / phase)",
    2: "Computing per-step statistics (median)",
    3: "Fitting and saving polynomial equations",
}

# Reports progress to the UI: (stage_no, total_stages, message, fraction).
# `fraction` is how far through that stage we are, 0..1, or None when the stage
# gives no finer signal than having started.
ProgressCallback = Callable[[int, int, str, object], None]


class CalibrationError(RuntimeError):
    """Raised when a pipeline stage cannot complete."""


def _report(progress: Optional[ProgressCallback], stage: int, message: str,
            fraction=None) -> None:
    if progress is not None:
        progress(stage, len(STAGE_NAMES), message, fraction)
    else:
        print(f"[{stage}/{len(STAGE_NAMES)}] {message}")


def run_calibration(
    start_stage: int = 1,
    debounce_n: int = 25,
    degrees=None,
    progress: Optional[ProgressCallback] = None,
) -> dict:
    """
    Run the calibration pipeline and return a summary.

    When `progress` is given it is called at the start of each stage; the
    one-button control in the GUI uses it to update its status text. Without
    it, progress goes to stdout.

    Returns: sensors, measurements, equation_rows, r2_mean, r2_min, elapsed_s,
    outputs.
    """
    if not os.path.isdir(RAW_DIR):
        raise CalibrationError(f"Raw data directory missing: {RAW_DIR}")

    raw_files = [f for f in os.listdir(RAW_DIR) if f.lower().endswith(".csv")]
    if not raw_files and start_stage == 1:
        raise CalibrationError(f"No CSV found in {RAW_DIR}. Put the raw data there first.")

    started = time.perf_counter()

    if start_stage <= 1:
        _report(progress, 1, f"{STAGE_NAMES[1]} - {len(raw_files)} files", 0.0)

        def on_file(done, total, name):
            _report(progress, 1, f"{done} of {total} files - {name}",
                    done / total if total else None)

        from data_processing.metadata_fz_v2 import FzMetadataGenerator
        FzMetadataGenerator(debounce_n=debounce_n).generate_metadata(progress=on_file)
        _require(os.path.join(DATASETS_DIR, "metadata_fz.csv"), 1)

    if start_stage <= 2:
        _report(progress, 2, STAGE_NAMES[2], 0.0)
        from data_processing.metadata_fz_median import generate_median_metadata
        generate_median_metadata()
        _require(os.path.join(DATASETS_DIR, "metadata_fz_median.csv"), 2)

    _report(progress, 3, STAGE_NAMES[3], 0.0)
    from calibration.equations import fit_sensor_models
    fit_df = fit_sensor_models(**({"degrees": degrees} if degrees else {}))
    if fit_df is None or fit_df.empty:
        raise CalibrationError("Stage 3 produced no equations.")

    combined = fit_df[fit_df["fit_scope"] == "combined"]
    summary = {
        "sensors": int(fit_df["sensor_id"].nunique()),
        "measurements": int(fit_df["source_file"].nunique()),
        "equation_rows": int(len(fit_df)),
        "r2_mean": float(combined["r2_score"].mean()),
        "r2_min": float(combined["r2_score"].min()),
        "elapsed_s": time.perf_counter() - started,
        "outputs": {
            "metadata": os.path.join(DATASETS_DIR, "metadata_fz.csv"),
            "median": os.path.join(DATASETS_DIR, "metadata_fz_median.csv"),
            "equations": os.path.join(DATASETS_DIR, "sensor_fit_equations.csv"),
        },
    }

    _report(progress, len(STAGE_NAMES),
            f"Done: {summary['sensors']} sensors, "
            f"{summary['equation_rows']} equation rows, "
            f"{summary['elapsed_s']:.0f} s", 1.0)

    return summary


def _require(path: str, stage: int) -> None:
    if not os.path.exists(path):
        raise CalibrationError(f"Stage {stage} did not produce its output: {path}")


def _parse_args(argv: list[str]) -> int:
    start = 1
    if "--from" in argv:
        i = argv.index("--from")
        if i + 1 >= len(argv):
            raise SystemExit("--from expects a stage number (1, 2 or 3)")
        start = int(argv[i + 1])
        if start not in STAGE_NAMES:
            raise SystemExit(f"Invalid stage: {start}. Must be 1, 2 or 3.")
    return start


if __name__ == "__main__":
    try:
        result = run_calibration(start_stage=_parse_args(sys.argv[1:]))
    except CalibrationError as exc:
        print(f"\nCALIBRATION FAILED: {exc}")
        raise SystemExit(1)

    print("\n" + "=" * 60)
    print("CALIBRATION COMPLETE")
    print("=" * 60)
    print(f"  Sensors           : {result['sensors']}")
    print(f"  Measurement files : {result['measurements']}")
    print(f"  Equation rows     : {result['equation_rows']}")
    print(f"  Mean R2 (combined): {result['r2_mean']:.6f}")
    print(f"  Min R2 (combined) : {result['r2_min']:.6f}")
    print(f"  Elapsed           : {result['elapsed_s']:.0f} s")
    for label, path in result["outputs"].items():
        if not os.path.exists(path):     # stages skipped with --from
            continue
        size_mb = os.path.getsize(path) / 1e6
        print(f"  {label:<18}: {os.path.basename(path)} ({size_mb:.1f} MB)")
