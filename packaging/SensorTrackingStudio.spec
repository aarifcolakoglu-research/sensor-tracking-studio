# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build for Sensor Tracking Studio.

Produces  dist/SensorTrackingStudio/SensorTrackingStudio.exe  - a one-folder
Windows application that runs with no Python installed and no PyCharm:

    cd <project root>
    pyinstaller packaging\\SensorTrackingStudio.spec --noconfirm

The data folders (input_raw_data, datasets, config, outputs, reports,
visualizations) are created next to the .exe on first run by app_paths, so the
whole dist\\SensorTrackingStudio folder can be copied anywhere and stays
self-contained. They are deliberately not bundled: they hold the operator's raw
calibration data and this machine's socket bindings, not program files.
"""

import os

from PyInstaller.utils.hooks import collect_submodules

# SPECPATH is injected by PyInstaller and holds the folder of THIS spec file.
# Building relative to it means the build works whichever directory you run
# pyinstaller from.
PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# ---------------------------------------------------------------------------
# Show a console window behind the GUI?
#
#   True  - the [App]/[Sim]/[Serial] messages and the calibration progress
#           appear in a terminal, as they do in PyCharm. Useful while
#           commissioning, and while a two-minute calibration runs.
#   False - GUI only, the way a finished Windows program looks.
# ---------------------------------------------------------------------------
SHOW_CONSOLE = False

hiddenimports = (
    # pyserial finds its Windows port enumeration at runtime
    collect_submodules("serial")
    + [
        "serial.tools.list_ports",
        "serial.tools.list_ports_windows",
        # The charts: matplotlib picks its backend by name, so nothing in the
        # dependency graph points at this module.
        "matplotlib.backends.backend_qtagg",
        "matplotlib.backends.backend_agg",
    ]
)

a = Analysis(
    [os.path.join(PROJECT_ROOT, "main.py")],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=[(os.path.join(SPECPATH, "app_icon.ico"), ".")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Only things nothing in the dependency graph can legitimately need.
    #
    # matplotlib is NOT excluded: every chart in this application is drawn
    # with it. scikit-learn and scipy are
    # likewise required - the calibration fits its polynomials with them, and
    # that runs from the Setup page rather than offline.
    excludes=[
        "PyQt5", "PySide2", "PySide6", "tkinter",
        "IPython", "notebook", "jupyter", "pytest", "sphinx",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SensorTrackingStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=SHOW_CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(SPECPATH, "app_icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SensorTrackingStudio",
)
