"""
Which physical sensor sits in which socket, and which measurement to use.

The Teensy reports a socket ("A-1"), not a sensor. It cannot know what is
plugged in. There are 12 sockets but 37 calibrated sensors, so the app has to
be told the mapping before it can convert raw values into force:

    A1 -> S1_MatC_A1,  A2 -> S1_MatC_A2,  ...,  C4 -> S1_MatC_C4

`SensorCatalog` lists what the calibration produced, grouped by set label so a
whole set can be applied in one click. `BindingStore` remembers the operator's
choice in `config/selected_sensors.json`, which is machine-specific and stays
out of the repository.

A binding records the sensor, the measurement it came from, and which
calibration curve to read it with. The measurement pins the equation to a
specific run when a sensor has been calibrated more than once; the phase mode
picks between the loading and unloading curves, which differ by 7-10% of full
scale and so cannot be left to chance.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Iterable, Optional

from inference.polynomial_engine import (DEFAULT_PHASE_MODE, DEFAULT_FIT_SOURCE,
                                        DEFAULT_DEGREE, TARGET_FORCE,
                                        TARGET_DISPLACEMENT, TARGETS,
                                        fit_source_label, select_fit_rows,
                                        target_label)

from app_paths import data_path, equations_file

# The 12 sockets on the force array, in grid order
POSITIONS: list[str] = [f"{row}{i}" for row in ("A", "B", "C") for i in range(1, 5)]

DEFAULT_EQUATION_FILE = equations_file()
DEFAULT_BINDING_FILE = data_path("config", "selected_sensors.json")


class Measurement:
    """One calibration run of one sensor."""

    def __init__(self, sensor_id: str, source_file: str, set_id: str,
                 position: str, collected_at: str):
        self.sensor_id = sensor_id
        self.source_file = source_file
        self.set_id = set_id
        self.position = position
        self.collected_at = collected_at
        # target -> phase -> fit quality, for the selected fit and degree.
        # Filled in by the catalog, which knows which fit is being described.
        self.r2: dict[str, dict[str, float]] = {}

    def worst_r2(self, target: str = TARGET_FORCE) -> float:
        """
        Worst phase decides: a model is only as good as its weaker curve.

        0.0 when this target was never fitted, which an older equations file
        is the usual reason for.
        """
        scores = self.r2.get(target)
        return min(scores.values()) if scores else 0.0

    def has(self, target: str) -> bool:
        return bool(self.r2.get(target))

    def label(self) -> str:
        """
        What the date picker shows: when this measurement was taken.

        The quality figures used to be appended here. They now have a column
        each, because there are two of them and a single worst-of figure hid
        which quantity was the weaker one.
        """
        return self.date_label()

    def date_label(self) -> str:
        return self.collected_at[:10] if self.collected_at else "unknown date"

    def time_label(self) -> str:
        """Date and time, for a sensor measured twice on the same day."""
        stamp = self.collected_at.replace("T", " ")
        return stamp[:16] if stamp else "unknown date"

    def __repr__(self) -> str:
        return f"<Measurement {self.sensor_id} {self.collected_at[:10]}>"


class SensorCatalog:
    """Everything the calibration produced, ready to be offered in the UI."""

    def __init__(self, equation_file: Optional[str] = None,
                 fit_source=DEFAULT_FIT_SOURCE, degree=DEFAULT_DEGREE):
        self.equation_file = equation_file or DEFAULT_EQUATION_FILE
        # Which fit the R2 and the measurement labels describe. It has to be
        # the one the engine is reading, or the page reports one model's
        # quality while the cards show another model's numbers.
        self.fit_source = fit_source
        self.degree = int(degree)
        # Targets the file carries; the setup page shows a column for each
        self.targets: list[str] = [TARGET_FORCE]
        # sensor_id -> list of Measurement, newest first
        self._by_sensor: dict[str, list[Measurement]] = {}
        # fit source -> median points behind one curve of that fit
        self._points: dict = {}
        self.load()

    def load(self) -> None:
        import pandas as pd

        self._by_sensor = {}

        if not os.path.exists(self.equation_file):
            print(f"WARNING: equations file not found: {self.equation_file}")
            return

        df = pd.read_csv(self.equation_file)

        self._points = self._count_points(df, self.degree)

        # Every target, for the selected fit and degree. The setup page shows
        # a quality figure per target, so both have to survive the read; which
        # rows those are still depends on the cycle and the degree, exactly as
        # the readings themselves do.
        df = select_fit_rows(df, self.fit_source, self.degree)
        self.targets = self._targets_in(df)

        for (sensor_id, source_file), grp in df.groupby(["sensor_id", "source_file"]):
            first = grp.iloc[0]
            m = Measurement(
                sensor_id=str(sensor_id),
                source_file=str(source_file),
                set_id=str(first.get("set_id", "unknown")),
                position=str(first.get("position", "")),
                collected_at=str(first.get("collected_at", "")),
            )
            for _, r in grp.iterrows():
                target = str(r.get("target", TARGET_FORCE) or TARGET_FORCE)
                m.r2.setdefault(target, {})[str(r["phase"])] = float(r["r2_score"])
            self._by_sensor.setdefault(m.sensor_id, []).append(m)

        # Newest measurement first, so index 0 is the default choice
        for items in self._by_sensor.values():
            items.sort(key=lambda m: m.collected_at, reverse=True)

    @staticmethod
    def _targets_in(df) -> list:
        """The targets these rows carry, in the order the interface shows them."""
        if "target" not in df.columns:
            return [TARGET_FORCE]
        present = {str(t) for t in df["target"].unique()}
        return [t for t in TARGETS if t in present] or [TARGET_FORCE]

    @staticmethod
    def _count_points(df, degree=DEFAULT_DEGREE) -> dict:
        """
        How many median points sit behind one curve of each fit.

        Read from the file rather than assumed: a partial calibration, or a
        different number of steps, would make any hard-coded figure wrong.
        """
        if "n_points" not in df.columns or "fit_scope" not in df.columns:
            return {}

        # The point count is a property of the data behind a fit, so it is the
        # same for both degrees; take one so the medians are not doubled.
        if "degree" in df.columns:
            df = df[df["degree"] == int(degree)]
        if "target" in df.columns:
            df = df[df["target"] == TARGET_FORCE]

        points = {}
        combined = df[df["fit_scope"] == "combined"]
        if not combined.empty:
            points["combined"] = int(combined["n_points"].median())

        singles = df[df["fit_scope"] == "single"]
        for cycle, rows in singles.groupby("cycle_id"):
            points[int(cycle)] = int(rows["n_points"].median())

        return points

    def set_fit_source(self, fit_source) -> None:
        """Describe a different fit, reloading what was read from the file."""
        if fit_source == self.fit_source:
            return
        self.fit_source = fit_source
        self.load()

    def set_degree(self, degree) -> None:
        """Describe a different polynomial degree, reloading from the file."""
        if int(degree) == self.degree:
            return
        self.degree = int(degree)
        self.load()

    def quality_label(self, target: str = TARGET_FORCE) -> str:
        """Names the fit the R2 range belongs to, so the figure is not ambiguous."""
        fit = fit_source_label(self.fit_source).split(" (")[0]
        return f"{fit} {target_label(target).lower()} R2"

    def points_for(self, fit_source) -> int:
        """Median points behind one curve of that fit, 0 when unknown."""
        return self._points.get(fit_source, 0)

    def r2_range(self, target: str = TARGET_FORCE):
        """(lowest, highest) R2 for one target, or None with nothing loaded."""
        scores = [m.worst_r2(target) for items in self._by_sensor.values()
                  for m in items if m.has(target)]
        return (min(scores), max(scores)) if scores else None

    def repeat_count(self) -> int:
        """Sensors that have been calibrated more than once."""
        return sum(1 for items in self._by_sensor.values() if len(items) > 1)

    def sensor_ids(self) -> list[str]:
        return sorted(self._by_sensor)

    def sets(self) -> dict[str, list[str]]:
        """Set label -> sensor ids in that set, ordered by socket position."""
        groups: dict[str, list[str]] = {}
        for sensor_id, items in self._by_sensor.items():
            groups.setdefault(items[0].set_id, []).append(sensor_id)
        for ids in groups.values():
            ids.sort(key=lambda s: _position_sort_key(self.position_of(s)))
        return groups

    def measurements(self, sensor_id: str) -> list[Measurement]:
        """All measurements of a sensor, newest first."""
        return list(self._by_sensor.get(sensor_id, []))

    def default_measurement(self, sensor_id: str) -> Optional[Measurement]:
        """
        The measurement used unless the operator picks another one.

        Newest wins. R2 is shown next to each option in the picker so a weaker
        recent run can be spotted and overridden.
        """
        items = self._by_sensor.get(sensor_id)
        return items[0] if items else None

    def find_measurement(self, sensor_id: str, source_file: str) -> Optional[Measurement]:
        for m in self._by_sensor.get(sensor_id, []):
            if m.source_file == source_file:
                return m
        return None

    def position_of(self, sensor_id: str) -> str:
        items = self._by_sensor.get(sensor_id)
        return items[0].position if items else ""


class BindingStore:
    """
    The socket-to-sensor mapping, remembered between runs.

    Stored as `config/selected_sensors.json` (which sensors are plugged into
    the rig right now). The repository ships the mapping used in the helmet
    study.
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path or DEFAULT_BINDING_FILE
        self.set_name: str = ""
        self.saved_at: str = ""
        # Which fit the engine reads; a property of how this rig is read, so it
        # is kept with the bindings rather than with the display settings.
        self.fit_source = DEFAULT_FIT_SOURCE
        self.fit_degree = DEFAULT_DEGREE
        # position -> {"sensor_id": ..., "source_file": ..., "phase_mode": ...}
        self.bindings: dict[str, dict[str, str]] = {}

    def load(self) -> "BindingStore":
        if not os.path.exists(self.path):
            return self

        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"WARNING: could not read {self.path}: {exc}")
            return self

        self.set_name = str(data.get("set_name", ""))
        self.saved_at = str(data.get("saved_at", ""))
        self.fit_source = _parse_fit_source(data.get("fit_source", DEFAULT_FIT_SOURCE))
        self.fit_degree = _parse_degree(data.get("fit_degree", DEFAULT_DEGREE))
        self.bindings = {}
        for position, value in (data.get("bindings") or {}).items():
            pos = _normalize(position)
            if isinstance(value, dict) and value.get("sensor_id"):
                self.bindings[pos] = {
                    "sensor_id": str(value["sensor_id"]),
                    "source_file": str(value.get("source_file", "")),
                    "phase_mode": str(value.get("phase_mode", DEFAULT_PHASE_MODE)),
                }
            elif isinstance(value, str) and value:
                # Tolerate a simpler hand-written form: "A1": "S1_MatC_A1"
                self.bindings[pos] = {"sensor_id": value, "source_file": "",
                                      "phase_mode": DEFAULT_PHASE_MODE}

        return self

    def save(self) -> str:
        self.saved_at = datetime.now().isoformat(timespec="seconds")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

        ordered = sorted(self.bindings, key=_position_sort_key)
        payload = {
            "saved_at": self.saved_at,
            "set_name": self.set_name,
            "fit_source": self.fit_source,
            "fit_degree": self.fit_degree,
            "bindings": {p: self.bindings[p] for p in ordered},
        }
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")

        return self.path

    def bind(self, position: str, sensor_id: str, source_file: str = "",
             phase_mode: str = DEFAULT_PHASE_MODE) -> None:
        self.bindings[_normalize(position)] = {
            "sensor_id": sensor_id,
            "source_file": source_file,
            "phase_mode": phase_mode,
        }

    def set_phase_mode(self, position: str, mode: str) -> None:
        """Remember which curve this socket is read with."""
        entry = self.bindings.get(_normalize(position))
        if entry is not None:
            entry["phase_mode"] = mode

    def phase_mode_for(self, position: str) -> str:
        entry = self.bindings.get(_normalize(position))
        return entry.get("phase_mode", DEFAULT_PHASE_MODE) if entry else DEFAULT_PHASE_MODE

    def unbind(self, position: str) -> None:
        self.bindings.pop(_normalize(position), None)

    def clear(self) -> None:
        self.bindings = {}
        self.set_name = ""

    def sensor_for(self, position: str) -> Optional[str]:
        entry = self.bindings.get(_normalize(position))
        return entry["sensor_id"] if entry else None

    def source_for(self, position: str) -> str:
        entry = self.bindings.get(_normalize(position))
        return entry.get("source_file", "") if entry else ""

    def bound_positions(self) -> list[str]:
        return sorted(self.bindings, key=_position_sort_key)

    def is_empty(self) -> bool:
        return not self.bindings

    def apply_set(self, catalog: SensorCatalog, set_id: str,
                  positions: Iterable[str] = POSITIONS) -> list[str]:
        """
        Fill every socket from one set in a single step.

        A set is named like "S1_MatC" and its sensors are "S1_MatC_A1" and so
        on, so each socket maps onto the sensor of the same position. Returns
        the positions that had no matching sensor.
        """
        missing = []
        for position in positions:
            sensor_id = f"{set_id}_{_normalize(position)}"
            measurement = catalog.default_measurement(sensor_id)
            if measurement is None:
                missing.append(position)
                self.unbind(position)
                continue
            # Keep whatever curve this socket was already set to; applying a
            # set changes which sensor is there, not how it is read.
            self.bind(position, sensor_id, measurement.source_file,
                      self.phase_mode_for(position))

        self.set_name = set_id
        return missing

    def validate(self, catalog: SensorCatalog) -> list[str]:
        """
        Report bindings the current equations file cannot satisfy.

        Called after a recalibration: if raw data was renamed or removed, the
        stale bindings are named rather than failing silently at runtime.
        """
        problems = []
        for position in self.bound_positions():
            sensor_id = self.bindings[position]["sensor_id"]
            source_file = self.bindings[position].get("source_file", "")

            if not catalog.measurements(sensor_id):
                problems.append(f"{position}: no equations for '{sensor_id}'")
            elif source_file and catalog.find_measurement(sensor_id, source_file) is None:
                problems.append(
                    f"{position}: measurement '{source_file}' of '{sensor_id}' is gone"
                )
        return problems


def _parse_degree(value):
    """Only the degrees the calibration writes; anything else is the default."""
    try:
        degree = int(value)
    except (TypeError, ValueError):
        return DEFAULT_DEGREE
    return degree if degree in (2, 3) else DEFAULT_DEGREE


def _parse_fit_source(value):
    """Accept the string "combined" or a cycle number, however it was written."""
    if isinstance(value, int):
        return value
    text = str(value).strip()
    return int(text) if text.isdigit() else DEFAULT_FIT_SOURCE


def _normalize(position: str) -> str:
    """Turn "A-1" or "a1" into "A1"."""
    return "".join(ch for ch in str(position) if ch.isalnum()).upper()


def _position_sort_key(position: str) -> tuple:
    p = _normalize(position)
    return (p[:1], int(p[1:]) if p[1:].isdigit() else 0)


def display_position(position: str) -> str:
    """Format a socket for the UI: "A1" -> "A-1"."""
    p = _normalize(position)
    return f"{p[:1]}-{p[1:]}" if len(p) > 1 else p


if __name__ == "__main__":
    catalog = SensorCatalog()
    print(f"Sensors with equations: {len(catalog.sensor_ids())}\n")

    for set_id, ids in sorted(catalog.sets().items()):
        print(f"  {set_id:<10} {len(ids):>2} sensors -> "
              f"{' '.join(catalog.position_of(i) for i in ids)}")

    repeats = {s: len(catalog.measurements(s)) for s in catalog.sensor_ids()
               if len(catalog.measurements(s)) > 1}
    print(f"\nSensors with repeat measurements: {len(repeats)}")
    for sensor_id, count in repeats.items():
        print(f"  {sensor_id}: {count} measurements")
        for m in catalog.measurements(sensor_id):
            quality = "  ".join(f"{t} R2 {m.worst_r2(t):.4f}"
                                for t in catalog.targets if m.has(t))
            print(f"      {m.label()}  {quality}  <- {m.source_file}")

    store = BindingStore()
    sets = sorted(catalog.sets())
    if sets:
        missing = store.apply_set(catalog, sets[0])
        print(f"\nApplied set '{sets[0]}', unmatched sockets: {missing or 'none'}")
        for position in store.bound_positions():
            print(f"  {display_position(position)} -> {store.sensor_for(position)}")
