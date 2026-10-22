"""
Y-axis range choices for the charts.

Three kinds of option:

    Default   what the charts have always done: grow past 90% of the current
              top, shrink below 30%, never go under 1 N. Stable to read
              because it does not move for every sample.
    Auto      track the data continuously. Follows small signals closely, but
              the axis is always in motion.
    Fixed     a gram-force ceiling, so two charts can be compared directly.

The fixed ladder stops at what the sensor was actually calibrated for. Offering
2000 gf on a sensor whose calibration tops out near 1100 gf would invite
reading a number the equation was never fitted for; the ladder is therefore cut
one step above each sensor's measured maximum.

There is a ladder per target. Force is read in gram-force and reaches about
1100 gf; indentation is read in millimetres and reaches 1.8 mm, so a ladder in
gf would be meaningless for it.
"""

from __future__ import annotations

# Newtons to gram-force
GF_PER_N = 101.97

# Millimetres to microns, for the overlay's second axis
UM_PER_MM = 1000.0

TARGET_FORCE = "force"
TARGET_DISPLACEMENT = "displacement"

MODE_DEFAULT = "default"
MODE_AUTO = "auto"
MODE_FIXED = "fixed"

# Coarser as the numbers grow, the way a range switch on an instrument steps.
# The top of the ladder stays in 100 gf steps rather than jumping 1000 -> 1500:
# the strongest calibrated sensor peaks at 1174 gf, and a 1500 gf ceiling would
# leave a fifth of the chart permanently empty.
FIXED_LADDER_GF = [100, 150, 200, 250, 300, 350, 400, 450, 500,
                   600, 700, 800, 900, 1000, 1100, 1200, 1300, 1400, 1500]

# Indentation, in millimetres. Finer at the bottom for the same reason: a light
# touch moves the elastomer a fraction of the calibrated 1.8 mm.
FIXED_LADDER_MM = [0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.75,
                   1.0, 1.25, 1.5, 1.75, 2.0]


def _spec(target):
    """(ladder, unit, how many of that unit per engine unit, label format)."""
    if target == TARGET_DISPLACEMENT:
        return FIXED_LADDER_MM, "mm", 1.0, lambda v: f"{v:g} mm"
    return FIXED_LADDER_GF, "gf", GF_PER_N, lambda v: f"{v:g} gf"


def unit_for(target) -> str:
    return _spec(target)[1]


def to_display(value, target) -> float:
    """From the engine's unit (N or mm) to the displayed one (gf or mm)."""
    return value * _spec(target)[2]


def from_display(value, target) -> float:
    """Back the other way."""
    return value / _spec(target)[2]


def newtons_to_gf(newtons: float) -> float:
    return newtons * GF_PER_N


def gf_to_newtons(gf: float) -> float:
    return gf / GF_PER_N


def mm_to_um(mm: float) -> float:
    return mm * UM_PER_MM


def um_to_mm(um: float) -> float:
    return um / UM_PER_MM


def ladder_for(maximum, target=TARGET_FORCE) -> list:
    """
    The fixed steps offered for a sensor, in the target's display unit.

    `maximum` is that sensor's calibrated maximum in the engine's unit, newtons
    or millimetres. The ladder keeps the first step above it and drops the rest,
    so every option on offer is one the calibration covers. An unknown maximum
    returns the whole ladder rather than an empty one.
    """
    ladder, _, factor, _ = _spec(target)

    if not maximum or maximum <= 0:
        return list(ladder)

    ceiling = maximum * factor
    kept = [step for step in ladder if step < ceiling]

    # Keep one step above the maximum so the peak is not clipped at the top
    remaining = [step for step in ladder if step >= ceiling]
    if remaining:
        kept.append(remaining[0])

    return kept or [ladder[0]]


def key_for(mode: str, value=None) -> str:
    """
    A combo-box key for a range choice.

    Plain strings, not tuples: QComboBox.findData compares its entries as
    QVariants and never matches a Python tuple, so a tuple key silently
    returns -1 and the choice is dropped on the floor.
    """
    return f"{MODE_FIXED}:{value:g}" if mode == MODE_FIXED and value else mode


def parse_key(key: str) -> tuple:
    """Turn a combo key back into (mode, value)."""
    if not key:
        return (MODE_DEFAULT, None)
    if key.startswith(MODE_FIXED + ":"):
        try:
            return (MODE_FIXED, float(key.split(":", 1)[1]))
        except ValueError:
            return (MODE_DEFAULT, None)
    return (key if key in (MODE_DEFAULT, MODE_AUTO) else MODE_DEFAULT, None)


def options_for(maximum, target=TARGET_FORCE) -> list[tuple[str, str]]:
    """Combo-box entries as (label, key) pairs, in the target's unit."""
    # Both modes scale automatically; distinguish the steadier scale from
    # the one that closely fits the visible data.
    entries: list[tuple[str, str]] = [
        ("Auto (stable)", key_for(MODE_DEFAULT)),
        ("Auto (fit to data)", key_for(MODE_AUTO)),
    ]
    _, _, _, label = _spec(target)
    entries += [(label(step), key_for(MODE_FIXED, step))
                for step in ladder_for(maximum, target)]
    return entries


def limits_for(mode: str, value, current_max_n: float, current_top_n: float,
               target=TARGET_FORCE) -> tuple | None:
    """
    Work out the new y limits, or None when the axis should stay as it is.

    `current_max_n` is the largest value on screen and `current_top_n` the
    axis top right now. Returning None for the common case is what keeps the
    chart from being redrawn on every sample.
    """
    if mode == MODE_FIXED and value:
        top = from_display(float(value), target)
        if abs(current_top_n - top) > 1e-6:
            return (-0.05 * top, top)
        return None

    if mode == MODE_AUTO:
        top = max(0.1, current_max_n * 1.15)
        # Ignore differences too small to see, otherwise the axis never settles
        if abs(current_top_n - top) > max(0.02, current_top_n * 0.05):
            return (-0.05 * top, top)
        return None

    # Default: the original behaviour, with a dead band between the thresholds
    if current_max_n > current_top_n * 0.9:
        top = current_max_n * 1.5
        return (-0.05, top)
    if current_max_n < current_top_n * 0.3 and current_top_n > 1.0:
        top = max(1.0, current_max_n * 1.5)
        return (-0.05, top)
    return None
