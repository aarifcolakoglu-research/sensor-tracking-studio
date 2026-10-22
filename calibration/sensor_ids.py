"""
Sensor identity parsing from raw data filenames.

A raw filename is built from these parts:

    2026-01-31-21-53-30_C_Z1p8_H10_C5_R20_S1_MatC_C4
    |----- collected_at -----|--- conditions ---|-set_id-|-pos-|

Only `set_id` + `position` distinguish one sensor from another. The test
conditions in the middle (`C_Z1p8_H10_C5_R20`) are identical across all 37
files today, so they stay out of the identity, but they are kept in the
`conditions` field in case data is later collected under other conditions.

Short identity (`sensor_id`) = "S1_MatC_C4", unique across all 37 files.
When a sensor is measured more than once the same `sensor_id` appears in
several files; the field that separates those measurements is `source_file`.
"""

from __future__ import annotations

import os
import re
from datetime import datetime

# 2026-01-31-21-53-30_C_Z1p8_H10_C5_R20_S1_MatC_C4
_RAW_NAME_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})"
    r"_(?P<conditions>.+)"
    r"_(?P<set_id>S\d+_Mat[A-Za-z0-9]+)"
    r"_(?P<position>[A-Za-z]\d+)$"
)

_TS_FORMAT = "%Y-%m-%d-%H-%M-%S"

UNKNOWN_SET = "unknown"


def normalize_position(position: str) -> str:
    """
    Reduce position labels to a single form.

    The Teensy reports a socket as "C-1" while filenames carry "C4"; both end
    up as "C1" / "C4".
    """
    return re.sub(r"[^A-Za-z0-9]", "", str(position)).upper()


def display_position(position: str) -> str:
    """Format for the UI: "C1" -> "C-1"."""
    p = normalize_position(position)
    m = re.match(r"^([A-Z]+)(\d+)$", p)
    return f"{m.group(1)}-{m.group(2)}" if m else p


def parse_raw_filename(path_or_name: str) -> dict:
    """
    Parse a raw data filename.

    The returned dict always carries these keys:
        source_file  : full filename without extension (identifies a measurement)
        sensor_id    : short identity, "S1_MatC_C4"
        set_id       : grouping label, "S1_MatC"
        position     : normalized position, "C4"
        conditions   : test condition text, or ""
        collected_at : ISO 8601 string, or "" when unparsable
        parsed       : whether the pattern matched

    When the pattern does not match, the whole filename becomes `sensor_id`.
    An unrecognised naming scheme therefore does not break the pipeline; such
    a file simply cannot be grouped.
    """
    stem = os.path.splitext(os.path.basename(str(path_or_name)))[0]
    m = _RAW_NAME_RE.match(stem)

    if not m:
        return {
            "source_file": stem,
            "sensor_id": stem,
            "set_id": UNKNOWN_SET,
            "position": normalize_position(stem.split("_")[-1]),
            "conditions": "",
            "collected_at": "",
            "parsed": False,
        }

    set_id = m.group("set_id")
    position = normalize_position(m.group("position"))

    return {
        "source_file": stem,
        "sensor_id": f"{set_id}_{position}",
        "set_id": set_id,
        "position": position,
        "conditions": m.group("conditions"),
        "collected_at": _parse_timestamp(m.group("ts")),
        "parsed": True,
    }


def _parse_timestamp(ts: str) -> str:
    try:
        return datetime.strptime(ts, _TS_FORMAT).isoformat()
    except ValueError:
        return ""


def sensor_id_from_path(path_or_name: str) -> str:
    """Shortcut returning only the short identity."""
    return parse_raw_filename(path_or_name)["sensor_id"]


def scan_raw_directory(raw_dir: str) -> list[dict]:
    """
    Parse every CSV in the raw data directory.

    Returns a list sorted by collection time, oldest first. The "newest wins"
    default used when picking between repeat measurements relies on this order.
    """
    if not os.path.isdir(raw_dir):
        return []

    records = []
    for name in sorted(os.listdir(raw_dir)):
        if not name.lower().endswith(".csv"):
            continue
        info = parse_raw_filename(name)
        info["path"] = os.path.join(raw_dir, name)
        records.append(info)

    records.sort(key=lambda r: (r["collected_at"], r["source_file"]))
    return records


def group_by_set(records: list[dict]) -> dict[str, list[dict]]:
    """Group parsed records by set label, for the set picker in the UI."""
    groups: dict[str, list[dict]] = {}
    for rec in records:
        groups.setdefault(rec["set_id"], []).append(rec)
    for items in groups.values():
        items.sort(key=lambda r: r["position"])
    return groups


def latest_measurement_per_sensor(records: list[dict]) -> dict[str, dict]:
    """
    Pick the most recently collected measurement for each `sensor_id`.

    This is the default when repeat measurements exist; the user can choose a
    different one from the UI.
    """
    latest: dict[str, dict] = {}
    for rec in records:  # scan_raw_directory already sorts oldest to newest
        latest[rec["sensor_id"]] = rec
    return latest


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app_paths import raw_data_dir
    recs = scan_raw_directory(raw_data_dir())

    print(f"{len(recs)} raw files parsed\n")
    for set_id, items in sorted(group_by_set(recs).items()):
        positions = " ".join(i["position"] for i in items)
        print(f"  {set_id:<10} {len(items):>2} sensors  ->  {positions}")

    unparsed = [r for r in recs if not r["parsed"]]
    if unparsed:
        print(f"\nWARNING: {len(unparsed)} filenames could not be parsed:")
        for r in unparsed:
            print(f"  {r['source_file']}")

    seen: dict[str, int] = {}
    for r in recs:
        seen[r["sensor_id"]] = seen.get(r["sensor_id"], 0) + 1
    repeats = {k: v for k, v in seen.items() if v > 1}
    print(f"\nUnique sensors: {len(seen)}")
    print(f"Repeat-measured sensors: {len(repeats)}" + (f" -> {repeats}" if repeats else ""))
