"""
Converts raw sensor values into force using the fitted polynomials.

A sensor has two curves, not one. Loading and unloading follow measurably
different paths: across the 37 calibrated sensors the hysteresis error runs
7-10% of full scale, far above the fit error, so which curve is applied is a
real choice rather than a detail.

Three modes per sensor:

    loading     always the loading curve (what the app used to do)
    unloading   always the unloading curve
    auto        follow the direction of travel, loading while the raw value
                rises and unloading while it falls

`auto` is the physically honest one but needs a dead band. Raw noise is around
10-15 counts over a span of roughly 8000, so without one the direction would
flip on noise alone and the reading would jump between two curves that differ
by most of a newton. The band is sized from the sensor's own calibrated span.

The same raw value yields two quantities, each with its own curves: the force
on the sensor, and how far the indenter travelled into the elastomer. Neither
can be derived from the other, and both were measured in the same calibration
sweep.

Three other things select which equation is read: the target, the degree of the
polynomial, and which cycles it was fitted over. The calibration writes every
combination, so all three are a choice made here rather than a re-run.

    fz = x3*bz^3 + a*bz^2 + b*bz + c

x3 is zero on the degree-2 rows, which is what an equations file written before
degree 3 existed produces when it is loaded. A file written before the second
target existed is read as force throughout.

The hysteresis mode is shared between the targets deliberately: the direction
of travel is one physical fact about the sensor, so one dead band and one piece
of direction state serve both.
"""

import os
import sys

import pandas as pd

# Running this file directly (its self-test at the bottom) puts this folder on
# sys.path rather than the project root, and app_paths lives at the root.
_SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SOURCE_ROOT not in sys.path:
    sys.path.insert(0, _SOURCE_ROOT)

from app_paths import equations_file

PHASE_LOADING = "loading"
PHASE_UNLOADING = "unloading"
PHASE_AUTO = "auto"
PHASE_MODES = (PHASE_LOADING, PHASE_UNLOADING, PHASE_AUTO)

# Shared by the dashboard and overlay. Spell out the calibration load
# direction so that understanding the choice does not depend on a small icon.
# Keep the labels short enough for the overlay's twelve-column strip.
PHASE_LABELS = {
    PHASE_LOADING: "Rising load",
    PHASE_UNLOADING: "Falling load",
    PHASE_AUTO: "Auto select",
}

# Title for the control that selects between them
PHASE_CONTROL_LABEL = "Hysteresis curve"

# Full explanations for the compact labels, shown in picker tooltips.
PHASE_DESCRIPTIONS = {
    PHASE_LOADING: "use the curve calibrated with increasing load (Loading)",
    PHASE_UNLOADING: "use the curve calibrated with decreasing load (Unloading)",
    PHASE_AUTO: "automatically switch between the two curves using the "
                "raw sensor signal's direction",
}


DEFAULT_PHASE_MODE = PHASE_LOADING

# Which fit the engine reads. The calibration writes two kinds of row: one per
# cycle, and one pooling every cycle's median points.
#
# "combined" is the default because it is fitted over about 95 points instead
# of 19, so it is less sensitive to any single cycle. The per-cycle fits are
# there for repeatability analysis, and for the case where one cycle is known
# to be the trustworthy one - the first is often still settling.
FIT_COMBINED = "combined"
DEFAULT_FIT_SOURCE = FIT_COMBINED

# Degree of the polynomial read from the equations file.
#
# 2 is the default because it is what every measurement so far has been taken
# with; changing the default would silently change readings already recorded
# against it. Degree 3 fits the same points more closely - across the 37
# sensors it roughly halves the error over the whole range and at the low end -
# but it also leaves the calibrated range faster at the top, so it is offered
# rather than imposed.
# What the equation predicts. Both come out of the same calibration sweep and
# both carry their own loading and unloading curves: displacement turned out to
# have nearly as much hysteresis as force, 6.7% of full scale against 8.8%.
TARGET_FORCE = "force"
TARGET_DISPLACEMENT = "displacement"
TARGETS = (TARGET_FORCE, TARGET_DISPLACEMENT)
DEFAULT_TARGET = TARGET_FORCE

# What each target is called on screen. "Displacement" on its own is geometric
# and says nothing about what moved: this is how far the calibration indenter
# had travelled into the elastomer, so it is named for that. The key stays
# `displacement` because it is written into every equations file and recording.
TARGET_LABELS = {
    TARGET_FORCE: "Force",
    TARGET_DISPLACEMENT: "Indentation depth",
}

# Unit the engine returns for each, and what a reading is shown in
TARGET_UNITS = {
    TARGET_FORCE: "N",
    TARGET_DISPLACEMENT: "mm",
}

# How far the indenter can physically go. The elastomer is 3 mm thick and the
# magnet inside it about 0.8 mm, so a little over 2 mm of it can be compressed;
# calibration reaches 1.8 mm. A reading past 2 mm is not a deeper indentation,
# it is the polynomial leaving the range it was fitted on, and it is reported
# as the ceiling rather than as a number nothing could have produced.
#
# There is no equivalent for force: the sensor is not bounded that way.
TARGET_CEILINGS = {
    TARGET_DISPLACEMENT: 2.0,
}


def target_label(target) -> str:
    return TARGET_LABELS.get(target, str(target))


DEGREE_QUADRATIC = 2
DEGREE_CUBIC = 3
DEFAULT_DEGREE = DEGREE_QUADRATIC

DEGREE_LABELS = {
    DEGREE_QUADRATIC: "Quadratic (degree 2)",
    DEGREE_CUBIC: "Cubic (degree 3)",
}


def degree_label(degree) -> str:
    return DEGREE_LABELS.get(int(degree), f"Degree {degree}")


def evaluate(coefficients, raw_bz):
    """fz from (x3, a, b, c). One definition, so no caller can drop a term."""
    x3, a, b, c = coefficients
    return (x3 * raw_bz ** 3) + (a * raw_bz ** 2) + (b * raw_bz) + c


def fit_source_label(source) -> str:
    """How a fit source reads in the interface."""
    if source == FIT_COMBINED:
        return "Combined (all cycles)"
    return f"Cycle {source} only"


def select_fit_rows(df, fit_source, degree=DEFAULT_DEGREE, target=None):
    """
    The rows of an equations file that belong to one fit source and degree.

    Shared rather than written once per caller: the engine and the catalogue
    each had their own copy, the catalogue's was never taught about the
    setting, and so the R2 shown beside a measurement went on describing the
    combined fit while the readings came from a cycle.

    A file written before these columns existed is read as it was meant to be:
    without `target` every row is force, without `degree` every row is degree 2,
    and without `fit_scope` the only rows are per-cycle and the original choice
    applies, cycle 3.
    """
    if target is not None:
        if "target" in df.columns:
            df = df[df["target"] == target]
        elif target != TARGET_FORCE:
            # The file predates the second target and has nothing to offer.
            return df.iloc[0:0]

    if "degree" in df.columns:
        df = df[df["degree"] == int(degree)]
    elif int(degree) != DEGREE_QUADRATIC:
        # The file has no cubic rows to give.
        return df.iloc[0:0]

    if "fit_scope" not in df.columns:
        return df[df["cycle_id"] == 3]

    if fit_source == FIT_COMBINED:
        return df[df["fit_scope"] == "combined"]

    return df[(df["fit_scope"] == "single") & (df["cycle_id"] == int(fit_source))]

# Direction dead band, as a fraction of the sensor's calibrated raw span.
# 0.5% of a ~8000 count span is ~40 counts, comfortably above the 10-15 counts
# of noise seen within a single calibration step.
DIRECTION_BAND_FRACTION = 0.005


def _bounded(target: str, value: float) -> float:
    """
    Hold a reading inside what the sensor can physically do.

    Negative is clamped for both targets: the rig only presses, so neither a
    negative force nor a negative indentation is meaningful. Indentation is
    also capped at TARGET_CEILINGS, because past that the elastomer has run
    out of thickness and the number is extrapolation, not measurement.
    """
    value = max(0.0, value)
    ceiling = TARGET_CEILINGS.get(target)
    return min(value, ceiling) if ceiling else value


class PolynomialEngine:
    """
    Raw (bz) to force (fz), with tare and per-sensor phase handling.

    Reads the combined-fit rows the calibration produces: one equation per
    sensor and phase, fitted over the median points of every cycle. Falls back
    to the older per-cycle layout when handed an equations file that predates
    fit_scope.
    """

    def __init__(self, equation_file=None):
        self.equation_file = equation_file or equations_file()

        # sensor_id -> target -> {"Loading": (x3, a, b, c), "Unloading": ...}
        self.models = {}
        # (sensor_id, target) -> tare offset, in that target's unit
        self.offsets = {}
        # sensor_id -> one of PHASE_MODES
        self.phase_modes = {}
        # sensor_id -> {"fz_max_n", "pz_max_mm", "raw_min", "raw_max", "band"}
        self.ranges = {}

        # Which rows load_models reads; see FIT_COMBINED and DEFAULT_DEGREE.
        self.fit_source = DEFAULT_FIT_SOURCE
        self.degree = DEFAULT_DEGREE
        # Targets the loaded file actually carries
        self.targets = [DEFAULT_TARGET]

        # Direction tracking for the auto mode
        self._last_raw = {}
        self._direction = {}

        self.load_models()

    # --- loading -----------------------------------------------------------

    def load_models(self):
        """Read the equations file, keeping any phase modes already chosen."""
        previous_modes = dict(self.phase_modes)

        self.models = {}
        self.offsets = {}
        self.ranges = {}
        self._last_raw = {}
        self._direction = {}

        if not os.path.exists(self.equation_file):
            print(f"WARNING: Equations file not found: {self.equation_file}")
            self.phase_modes = {}
            return

        try:
            df = pd.read_csv(self.equation_file)
            self.targets = self._available_targets(df)
            rows = self._select_rows(df)
            if rows.empty:
                print(f"WARNING: no rows for degree {self.degree} / fit source "
                      f"'{self.fit_source}'; falling back to the degree-"
                      f"{DEFAULT_DEGREE} combined fit.")
                self.degree = DEFAULT_DEGREE
                self.fit_source = FIT_COMBINED
                rows = self._select_rows(df)

            for _, row in rows.iterrows():
                sensor_id = str(row["sensor_id"])
                target = str(row.get("target", TARGET_FORCE) or TARGET_FORCE)
                phase = str(row["phase"])

                by_target = self.models.setdefault(sensor_id, {})
                by_target.setdefault(target, {})[phase] = (
                    float(row.get("coeff_x3", 0.0) or 0.0),
                    float(row["coeff_a"]),
                    float(row["coeff_b"]),
                    float(row["intercept_c"]),
                )
                self.offsets[(sensor_id, target)] = 0.0
                self._store_range(sensor_id, row)

            # A sensor still selected in the UI keeps its mode across a reload
            self.phase_modes = {
                s: previous_modes.get(s, DEFAULT_PHASE_MODE) for s in self.models
            }

            print(f"Engine Ready: {len(self.models)} sensors loaded "
                  f"({', '.join(sorted(self.targets))}), degree {self.degree}, "
                  f"{self.fit_source} fit.")

        except Exception as e:
            print(f"Error loading equations: {e}")

    def _select_rows(self, df: pd.DataFrame) -> pd.DataFrame:
        """Every target's rows for the selected degree and fit source."""
        return select_fit_rows(df, self.fit_source, self.degree)

    @staticmethod
    def _available_targets(df) -> list:
        """What the file predicts. An older file predicts force and nothing else."""
        if "target" not in df.columns:
            return [TARGET_FORCE]
        present = {str(t) for t in df["target"].unique()}
        return [t for t in TARGETS if t in present] or [TARGET_FORCE]

    def set_fit_source(self, source) -> None:
        """
        Read the models from a different fit and reload.

        Tare offsets do not survive: they are a force computed through the old
        coefficients, and keeping them would apply one curve's zero to another
        curve's readings.
        """
        if source == self.fit_source:
            return
        self.fit_source = source
        self.load_models()

    def set_degree(self, degree) -> None:
        """
        Read a different polynomial degree and reload.

        Like the fit source, this replaces every coefficient, so tare offsets
        are dropped: an offset is a force computed through the coefficients
        being replaced.
        """
        degree = int(degree)
        if degree == self.degree:
            return
        self.degree = degree
        self.load_models()

    def available_degrees(self) -> list:
        """Degrees this equations file holds, lowest first."""
        if not os.path.exists(self.equation_file):
            return [DEFAULT_DEGREE]

        try:
            df = pd.read_csv(self.equation_file, usecols=["degree"])
        except (ValueError, OSError):
            # No degree column: everything in the file is degree 2.
            return [DEGREE_QUADRATIC]

        return sorted(int(d) for d in df["degree"].unique())

    def available_fit_sources(self) -> list:
        """
        What the current equations file can offer, combined first.

        Read from the file rather than assumed, because a file produced by an
        older pipeline has no combined rows and a partial run may not have all
        five cycles.
        """
        if not os.path.exists(self.equation_file):
            return [FIT_COMBINED]

        try:
            df = pd.read_csv(self.equation_file,
                             usecols=["fit_scope", "cycle_id", "degree"])
            df = df[df["degree"] == int(self.degree)]
        except (ValueError, OSError):
            try:
                df = pd.read_csv(self.equation_file,
                                 usecols=["fit_scope", "cycle_id"])
            except (ValueError, OSError):
                return [FIT_COMBINED]

        sources = []
        if "fit_scope" in df.columns and (df["fit_scope"] == "combined").any():
            sources.append(FIT_COMBINED)

        singles = df[df["fit_scope"] == "single"] if "fit_scope" in df.columns else df
        sources += sorted(int(c) for c in singles["cycle_id"].unique() if int(c) > 0)

        return sources or [FIT_COMBINED]

    def _store_range(self, sensor_id: str, row) -> None:
        """Remember the calibrated span, used for Y limits and the dead band."""
        if sensor_id in self.ranges:
            return

        try:
            raw_min = float(row["raw_bz_min"])
            raw_max = float(row["raw_bz_max"])
            fz_max = float(row["fz_max_n"])
            pz_max = float(row.get("pz_max_mm", 0.0) or 0.0)
        except (KeyError, TypeError, ValueError):
            # Older equations file: no range columns. A zero band makes the
            # auto mode react to any change, which is the safer default when
            # the span is unknown.
            self.ranges[sensor_id] = {"fz_max_n": 0.0, "pz_max_mm": 0.0,
                                      "raw_min": 0.0, "raw_max": 0.0,
                                      "band": 0.0}
            return

        self.ranges[sensor_id] = {
            "fz_max_n": fz_max,
            "pz_max_mm": pz_max,
            "raw_min": raw_min,
            "raw_max": raw_max,
            "band": abs(raw_max - raw_min) * DIRECTION_BAND_FRACTION,
        }

    # --- configuration -----------------------------------------------------

    def set_phase_mode(self, sensor_id: str, mode: str) -> None:
        """Choose which curve this sensor is read with."""
        if mode not in PHASE_MODES:
            return
        if sensor_id in self.models:
            self.phase_modes[sensor_id] = mode
            self._direction.pop(sensor_id, None)

    def set_all_phase_modes(self, mode: str) -> None:
        for sensor_id in self.models:
            self.set_phase_mode(sensor_id, mode)

    def get_phase_mode(self, sensor_id: str) -> str:
        return self.phase_modes.get(sensor_id, DEFAULT_PHASE_MODE)

    def available_phases(self, sensor_id: str) -> list:
        """
        Modes this sensor can actually offer.

        A sensor fitted with only one curve cannot do unloading or auto, and
        offering them would produce readings from a curve that is not there.
        """
        curves = self.models.get(sensor_id, {}).get(DEFAULT_TARGET, {})
        if len(curves) > 1:
            return list(PHASE_MODES)
        return [PHASE_LOADING]

    def available_targets(self) -> list:
        """What the loaded equations file can predict."""
        return list(self.targets)

    def curve(self, sensor_id: str, phase: str = "Loading",
              target: str = DEFAULT_TARGET):
        """
        Coefficients (x3, a, b, c) for one curve, or None when there is no model.

        Falls back to whichever curve the sensor does have, so a single-phase
        calibration still yields a usable model.
        """
        curves = self.models.get(sensor_id, {}).get(target)
        if not curves:
            return None
        return curves.get(phase) or next(iter(curves.values()))

    def max_force(self, sensor_id: str) -> float:
        """Calibrated maximum in newtons, 0.0 when the file does not say."""
        return self.ranges.get(sensor_id, {}).get("fz_max_n", 0.0)

    def max_displacement(self, sensor_id: str) -> float:
        """Calibrated maximum indentation in mm, 0.0 when the file does not say."""
        return self.ranges.get(sensor_id, {}).get("pz_max_mm", 0.0)

    def max_for(self, sensor_id: str, target: str = DEFAULT_TARGET) -> float:
        """The calibrated maximum for either target, in that target's unit."""
        if target == TARGET_DISPLACEMENT:
            return self.max_displacement(sensor_id)
        return self.max_force(sensor_id)

    # --- tare --------------------------------------------------------------

    def set_tare(self, sensor_id: str, current_raw_bz: float):
        """
        Treat the current raw value as zero for this sensor.

        Every target is zeroed together: they describe one state of the sensor,
        so zeroing the force while leaving the displacement reading standing
        would be describing that state two different ways.
        """
        if sensor_id not in self.models:
            return

        readings = []
        for target in self.models[sensor_id]:
            self.offsets[(sensor_id, target)] = 0.0
            value = self.predict(sensor_id, current_raw_bz, target)
            self.offsets[(sensor_id, target)] = value
            readings.append(f"{value:.4f} {TARGET_UNITS.get(target, '')}")

        print(f"Tare Set -> {sensor_id}: {', '.join(readings)} set as offset.")

    # --- prediction --------------------------------------------------------

    def predict(self, sensor_id: str, raw_bz: float,
                target: str = DEFAULT_TARGET) -> float:
        """
        The target's value for a raw reading, less this sensor's tare.

        The result is held inside what the sensor can physically do: see
        _bounded.
        """
        curves = self.models.get(sensor_id, {}).get(target)
        if not curves or raw_bz is None:
            return 0.0

        phase = self._phase_for(sensor_id, raw_bz)
        coefficients = curves.get(phase) or next(iter(curves.values()))

        value = evaluate(coefficients, raw_bz)
        value -= self.offsets.get((sensor_id, target), 0.0)

        return _bounded(target, value)

    def predict_all(self, sensor_id: str, raw_bz: float) -> dict:
        """
        Every target at once, for logging.

        The recording holds both whatever the interface is showing: what was
        measured should not depend on which tab happened to be open.

        The phase is resolved once here. Calling predict per target would move
        the direction tracker forward once per target and could land them on
        different curves for the same sample.
        """
        curves = self.models.get(sensor_id)
        if not curves or raw_bz is None:
            return {target: 0.0 for target in self.targets}

        phase = self._phase_for(sensor_id, raw_bz)

        values = {}
        for target, by_phase in curves.items():
            coefficients = by_phase.get(phase) or next(iter(by_phase.values()))
            value = evaluate(coefficients, raw_bz)
            value -= self.offsets.get((sensor_id, target), 0.0)
            values[target] = _bounded(target, value)

        return values

    def invert(self, sensor_id: str, value: float, phase: str = "Loading",
               target: str = DEFAULT_TARGET):
        """
        The raw value that would produce this force, or None without a model.

        Found by bisection over the sensor's calibrated range rather than
        algebraically: a cubic has no tidy inverse, and staying inside the
        calibrated range is the right behaviour for both degrees - outside it
        the curve is extrapolating and the answer would be meaningless.
        """
        coefficients = self.curve(sensor_id, phase, target)
        span = self.ranges.get(sensor_id)
        if coefficients is None or not span or span["raw_max"] <= span["raw_min"]:
            return None

        low, high = span["raw_min"], span["raw_max"]
        if evaluate(coefficients, low) > evaluate(coefficients, high):
            # Reading falls as raw rises; bisection below assumes the opposite.
            low, high = high, low

        for _ in range(60):
            middle = (low + high) / 2.0
            if evaluate(coefficients, middle) < value:
                low = middle
            else:
                high = middle

        return (low + high) / 2.0

    def _phase_for(self, sensor_id: str, raw_bz: float) -> str:
        """Which curve to use for this sample."""
        mode = self.phase_modes.get(sensor_id, DEFAULT_PHASE_MODE)

        if mode == PHASE_LOADING:
            return "Loading"
        if mode == PHASE_UNLOADING:
            return "Unloading"

        return self._tracked_direction(sensor_id, raw_bz)

    def _tracked_direction(self, sensor_id: str, raw_bz: float) -> str:
        """
        Direction of travel, held steady through noise.

        The reference only moves once the raw value has travelled past the dead
        band, so a sensor sitting still keeps whichever curve it last used
        instead of flipping between the two.
        """
        previous = self._last_raw.get(sensor_id)
        direction = self._direction.get(sensor_id, "Loading")

        if previous is None:
            self._last_raw[sensor_id] = raw_bz
            self._direction[sensor_id] = direction
            return direction

        band = self.ranges.get(sensor_id, {}).get("band", 0.0)
        delta = raw_bz - previous

        if delta > band:
            direction = "Loading"
            self._last_raw[sensor_id] = raw_bz
        elif delta < -band:
            direction = "Unloading"
            self._last_raw[sensor_id] = raw_bz

        self._direction[sensor_id] = direction
        return direction

    def get_active_sensors(self):
        """Sensor ids that have a model loaded."""
        return list(self.models.keys())


if __name__ == "__main__":
    engine = PolynomialEngine()
    sensors = engine.get_active_sensors()
    if not sensors:
        raise SystemExit("No models loaded.")

    test_id = sensors[0]
    print(f"\nSensor: {test_id}")
    print(f"  degree     : {engine.degree} of {engine.available_degrees()}")
    print(f"  targets    : {engine.available_targets()}")
    print(f"  curves     : {sorted(engine.models[test_id][TARGET_FORCE])}")
    print(f"  modes       : {engine.available_phases(test_id)}")
    print(f"  max force  : {engine.max_force(test_id):.2f} N")
    print(f"  max indent : {engine.max_displacement(test_id):.2f} mm")
    print(f"  dead band  : {engine.ranges[test_id]['band']:.0f} raw counts")

    for mode in engine.available_phases(test_id):
        engine.set_phase_mode(test_id, mode)
        print(f"\n  mode = {mode}")
        for raw in (8000, 10000, 12000, 10000, 8000):
            both = engine.predict_all(test_id, raw)
            print(f"    raw {raw:>6} -> {both.get('force', 0):6.3f} N   "
                  f"{both.get('displacement', 0):6.3f} mm")
