from __future__ import annotations

import os
import glob
from typing import List, Tuple, Dict

import numpy as np
import pandas as pd

# When run directly (python data_processing/metadata_fz_v2.py) sys.path[0] is
# this folder; add the project root so package imports resolve.
import sys
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app_paths import archive_dir, datasets_dir, raw_data_dir
from calibration.archive import archive_dataframe
from calibration.sensor_ids import parse_raw_filename

STEP_MAX = 1.8
STEP_SIZE = 0.1

# Debounce: merge tiny step-jitter segments (1-25 samples) into their neighbours
DEFAULT_DEBOUNCE_N = 25  # ~0.25s @100Hz; kills the 21-22 sample artefacts


def snap_pz_to_grid(pz: pd.Series, step: float = STEP_SIZE, step_max: float = STEP_MAX) -> pd.Series:
    """
    Robust grid snap:
      pz_step = round(pz/step)*step  (float-safe)
      clip to [0, step_max]
      normalize -0.0 -> 0.0
    """
    x = pd.to_numeric(pz, errors="coerce").astype(float)
    s = np.round(x.to_numpy() / step) * step
    s = np.clip(s, 0.0, step_max)
    s = np.round(s, 1)  # force 1 decimal representation
    s[s == -0.0] = 0.0
    return pd.Series(s, index=pz.index, name="pz_step")


def run_length_encode(arr: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Returns:
      values, starts, lengths
    """
    n = len(arr)
    if n == 0:
        return np.array([]), np.array([]), np.array([])

    # indices where value changes
    change = np.where(~np.isclose(arr[1:], arr[:-1]))[0] + 1
    starts = np.r_[0, change]
    ends = np.r_[change, n]
    lengths = ends - starts
    values = arr[starts]
    return values, starts, lengths


def debounce_steps(pz_step: pd.Series, min_len: int = DEFAULT_DEBOUNCE_N) -> pd.Series:
    """
    Merge short segments (< min_len) into neighbors.
    """
    arr = pz_step.to_numpy(dtype=float)
    if len(arr) == 0:
        return pz_step

    values, starts, lengths = run_length_encode(arr)
    if len(values) <= 1:
        return pz_step

    v = values.tolist()
    s = starts.tolist()
    l = lengths.tolist()

    def rebuild_series(vv: List[float], ss: List[int], ll: List[int]) -> np.ndarray:
        out = np.empty(len(arr), dtype=float)
        for val, st, ln in zip(vv, ss, ll):
            out[st:st + ln] = val
        return out

    changed = True
    while changed:
        changed = False
        idx_short = None
        for i in range(len(v)):
            if l[i] < min_len and len(v) > 1:
                idx_short = i
                break
        if idx_short is None:
            break

        i = idx_short
        left = i - 1 if i - 1 >= 0 else None
        right = i + 1 if i + 1 < len(v) else None

        if left is None and right is None:
            break

        if left is None:
            target = right
        elif right is None:
            target = left
        else:
            target = left if l[left] >= l[right] else right

        if target == left:
            l[left] += l[i]
            del v[i], s[i], l[i]
        else:
            s[right] = s[i]
            l[right] += l[i]
            del v[i], s[i], l[i]

        arr2 = rebuild_series(v, s, l)
        values2, starts2, lengths2 = run_length_encode(arr2)
        v = values2.tolist()
        s = starts2.tolist()
        l = lengths2.tolist()
        changed = True

    debounced = np.empty(len(arr), dtype=float)
    for val, st, ln in zip(v, s, l):
        debounced[st:st + ln] = val

    return pd.Series(debounced, index=pz_step.index, name=pz_step.name)


def assign_cycle_ids_from_steps(pz_step: pd.Series) -> Tuple[pd.Series, int]:
    """
    Assign cycle_id by scanning RLE segments.
    """
    arr = pz_step.to_numpy(dtype=float)
    n = len(arr)
    if n == 0:
        return pd.Series([], dtype=int, name="cycle_id"), 0

    values, starts, lengths = run_length_encode(arr)
    seg_count = len(values)

    cycle = np.zeros(n, dtype=int)
    current_cycle = 1

    zero_seg_idx = [i for i, val in enumerate(values) if np.isclose(val, 0.0)]
    first_zero = zero_seg_idx[0] if zero_seg_idx else None
    last_zero = zero_seg_idx[-1] if zero_seg_idx else None

    for i in range(seg_count):
        val = float(values[i])
        st = int(starts[i])
        ln = int(lengths[i])
        en = st + ln

        if np.isclose(val, 0.0) and first_zero is not None and last_zero is not None:
            if i == first_zero:
                cycle[st:en] = current_cycle
            elif i == last_zero:
                cycle[st:en] = current_cycle
            else:
                half = ln // 2
                mid = st + half
                cycle[st:mid] = current_cycle
                cycle[mid:en] = current_cycle + 1
                current_cycle += 1
        else:
            cycle[st:en] = current_cycle

    total_cycles = int(cycle.max()) if n > 0 else 0
    return pd.Series(cycle, index=pz_step.index, name="cycle_id"), total_cycles


def assign_load_unload(pz_step: pd.Series, cycle_id: pd.Series, step_max: float = STEP_MAX) -> pd.Series:
    """
    Mark Loading/Unloading for each cycle.
    """
    load_unload = pd.Series(index=pz_step.index, dtype=object, name="Load/Unload")

    for cid, idx in cycle_id.groupby(cycle_id).groups.items():
        idx = list(idx)
        steps = pz_step.loc[idx].to_numpy(dtype=float)
        hit = np.where(np.isclose(steps, step_max))[0]
        if hit.size == 0:
            load_unload.loc[idx] = "Loading"
            continue

        first = int(hit[0])
        j = first
        n = len(steps)
        while j < n and np.isclose(steps[j], step_max):
            j += 1
        load_unload.loc[idx[:j]] = "Loading"
        load_unload.loc[idx[j:]] = "Unloading"

    return load_unload


class FzMetadataGenerator:
    """
    STEP METADATA GENERATOR (Optimized for Sensor_Calib_Studio structure)
    """

    def __init__(self, debounce_n: int = DEFAULT_DEBOUNCE_N):
        # --- PROJECT PATHS ---
        # Input: the input_raw_data folder
        self.input_dir = raw_data_dir()

        # Output: the datasets folder
        self.output_dir = datasets_dir()
        self.archive_dir = archive_dir()

        # Final, large metadata file
        self.output_file = os.path.join(self.output_dir, "metadata_fz.csv")

        self.keep_columns = ["real_time", "pz", "fz", "raw_bz"]
        self.debounce_n = int(debounce_n)

    def _process_one_file(self, file_path: str) -> pd.DataFrame:
        df = pd.read_csv(file_path)
        missing = [c for c in self.keep_columns if c not in df.columns]
        if missing:
            raise ValueError(f"Missing columns {missing}")

        df = df[self.keep_columns].copy()
        df["pz_step"] = snap_pz_to_grid(df["pz"])

        if self.debounce_n > 1:
            df["pz_step"] = debounce_steps(df["pz_step"], min_len=self.debounce_n)

        cycle_id, total_cycles = assign_cycle_ids_from_steps(df["pz_step"])
        df.insert(0, "cycle_id", cycle_id.astype(int))
        df.insert(1, "Load/Unload", assign_load_unload(df["pz_step"], df["cycle_id"]))

        # Identity: short `sensor_id` (S1_MatC_C4) plus `source_file`, which
        # separates measurements. If a sensor is measured twice, sensor_id
        # repeats but source_file does not; later stages group by both.
        info = parse_raw_filename(file_path)
        df.insert(0, "source_file", info["source_file"])
        df.insert(0, "sensor_id", info["sensor_id"])

        df = df[["sensor_id", "source_file", "cycle_id", "Load/Unload",
                 "real_time", "pz", "pz_step", "fz", "raw_bz"]]
        print(f"    {info['sensor_id']:<12} rows={len(df)} | cycles={total_cycles} "
              f"| debounce={self.debounce_n}")
        return df

    def generate_metadata(self, progress=None) -> None:
        """
        Label every raw file.

        `progress(done, total, name)` is called as each file is finished. This
        stage reads gigabytes and takes most of the calibration's running time,
        so it is the one worth reporting file by file; a bar that only moves
        three times would sit still for two minutes.
        """
        csv_files = sorted(glob.glob(os.path.join(self.input_dir, "*.csv")))

        print(f"Scanning Input: {self.input_dir}")
        print(f"Found {len(csv_files)} files.")

        all_frames: List[pd.DataFrame] = []

        for index, f in enumerate(csv_files, start=1):
            name = os.path.basename(f)
            try:
                print(f"  [PROCESSING] {name}")
                df = self._process_one_file(f)
                all_frames.append(df)
            except Exception as e:
                print(f"  [ERROR] {name}: {e}")

            if progress is not None:
                progress(index, len(csv_files), name)

        if not all_frames:
            print("No data processed. Check if input_raw_data folder contains CSV files.")
            return

        if progress is not None:
            progress(len(csv_files), len(csv_files), "writing the labelled data")

        master_df = pd.concat(all_frames, ignore_index=True)

        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.archive_dir, exist_ok=True)

        # Main output: datasets/metadata_fz.csv
        master_df.to_csv(self.output_file, index=False)

        # Timestamped archive copy; older copies beyond the retention limit
        # are pruned, since this file is around 1 GB per run.
        archive_dataframe(master_df, self.archive_dir, "metadata_fz")

        print("-" * 60)
        print(f"SUCCESS: {self.output_file} created.")
        print(f"Unique sensors processed: {master_df['sensor_id'].nunique()}")
        print("Done.")


if __name__ == "__main__":
    # Scans input_raw_data and writes the result into the datasets folder.
    FzMetadataGenerator(debounce_n=25).generate_metadata()