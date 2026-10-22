"""
Where the application keeps its data.

Running from a checkout, that is the project folder. Running from a packaged
build it is the folder holding the .exe, which is not the same place:
PyInstaller puts the modules inside `_internal`, so anything deriving a path
from `__file__` lands in the bundle rather than beside the program. Every
module asks here instead, and the same code works both ways.

The layout is the same in both cases:

    input_raw_data/   raw calibration CSVs, the pipeline's input
    datasets/         pipeline output, including sensor_fit_equations.csv
    datasets/archive/ timestamped copies of each run
    config/           this machine's choices: socket bindings, theme, scale
    outputs/          recorded sessions
    reports/          error reports
    visualizations/   fitting plots

These directories hold the operator's own data, so a packaged build creates
them empty next to the .exe on first run rather than shipping them.
"""

from __future__ import annotations

import os
import sys

# Subdirectories created on first run
DATA_DIRS = (
    "input_raw_data",
    "datasets",
    os.path.join("datasets", "archive"),
    "config",
    "outputs",
    "reports",
    "visualizations",
)


def is_frozen() -> bool:
    """True when running from a PyInstaller build."""
    return getattr(sys, "frozen", False)


def app_root() -> str:
    """The folder the data directories live in."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_path(*parts: str) -> str:
    """Build a path inside the data root."""
    return os.path.join(app_root(), *parts)


def datasets_dir() -> str:
    return data_path("datasets")


def archive_dir() -> str:
    return data_path("datasets", "archive")


def raw_data_dir() -> str:
    return data_path("input_raw_data")


def config_dir() -> str:
    return data_path("config")


def outputs_dir() -> str:
    return data_path("outputs")


def reports_dir() -> str:
    return data_path("reports")


def visualizations_dir() -> str:
    return data_path("visualizations")


def equations_file() -> str:
    return data_path("datasets", "sensor_fit_equations.csv")


def icon_file() -> str:
    """
    The application icon, or "" when it cannot be found.

    Three places to look, because the file lives somewhere different in each
    situation: inside the bundle when frozen (PyInstaller unpacks datas into
    sys._MEIPASS), beside the .exe because install.ps1 copies it there for the
    shortcuts, and in packaging/ when running from a checkout.
    """
    candidates = []

    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(os.path.join(meipass, "app_icon.ico"))
        candidates.append(os.path.join(app_root(), "app_icon.ico"))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(here, "packaging", "app_icon.ico"))

    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


def ensure_data_dirs() -> None:
    """
    Create the data directories if they are missing.

    Called at startup so a fresh install has somewhere to write before the
    operator touches anything. Failing is not fatal: the app still runs, it
    just has nothing to read yet, and the pages say so.
    """
    for name in DATA_DIRS:
        try:
            os.makedirs(data_path(name), exist_ok=True)
        except OSError as exc:
            print(f"WARNING: could not create {name}: {exc}")


if __name__ == "__main__":
    print(f"frozen    : {is_frozen()}")
    print(f"app root  : {app_root()}")
    print(f"icon      : {icon_file() or '(not found)'}")
    for name in DATA_DIRS:
        path = data_path(name)
        print(f"  {'ok ' if os.path.isdir(path) else '-- '} {name}")
