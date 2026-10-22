"""
Single source of truth for colours, switchable at runtime.

Three modes, chosen from View > Theme:

    light  light chrome, light cards
    dark   dark chrome, dark cards
    mix    dark chrome, light cards  (the default)

`mix` is not a compromise between the other two, it is a deliberate layout:
dark window furniture around light cards makes the twelve charts read as the
content and everything else as background. Within each mode the surfaces are
toned rather than flat, so panels, cards and charts stay distinguishable
instead of merging into one slab of colour.

Switching is live. APP_COLORS, CARD_COLORS and PLOT_COLORS are mutated in
place rather than rebound, so every module that did `from gui.theme import
APP_COLORS as COLORS` keeps looking at the current palette. Widgets still have
to re-apply their stylesheets afterwards; each page has an apply_theme() for
that.
"""

from __future__ import annotations

import json
import os

THEME_MODES = ("light", "dark", "mix")
DEFAULT_MODE = "mix"

# Interface zoom, applied live to widget and chart metrics
SCALE_STEPS = (70, 80, 90, 100, 110, 125, 150)
DEFAULT_SCALE = 100

from app_paths import data_path

_SETTINGS_FILE = data_path("config", "ui_settings.json")


# --- window chrome: navigation, panels, buttons, inputs --------------------

_APP = {
    "light": {
        # Toned, not flat white: the page sits slightly below the panels so
        # panel edges read without relying on the border alone.
        "app_bg": "#eef1f5",
        "panel_bg": "#ffffff",
        "panel_border": "#d0d7de",
        "text": "#111111",
        "muted_text": "#5a6472",
        "input_bg": "#ffffff",
        "input_border": "#c8d0d8",

        "nav_bg": "#e3e8ee",
        "nav_border": "#ccd4dd",
        "nav_text": "#333c46",
        "nav_hover": "#d4dce6",
        "nav_active_bg": "#0078d7",
        "nav_active_text": "#ffffff",

        "refresh_bg": "#e9eef5",
        "refresh_hover": "#dde7f3",
        "refresh_pressed": "#cfd8e3",
        "refresh_text": "#111111",
        "refresh_border": "#b8c2cc",

        "tare_bg": "#fff8e1",
        "tare_hover": "#ffefb3",
        "tare_pressed": "#e6c75f",
        "tare_text": "#8a6400",
        "tare_border": "#d6b656",

        "record_bg": "#fff0f0",
        "record_hover": "#ffe0e0",
        "record_pressed": "#e7b8b8",
        "record_text": "#d00000",
        "record_border": "#e0b0b0",
        "record_disabled_text": "#999999",
        "record_disabled_bg": "#e6e9ed",

        "connect_bg": "#0078d7",
        "connect_hover": "#0086f0",
        "connect_pressed": "#005a9e",

        "disconnect_bg": "#d83b01",
        "disconnect_pressed": "#a83200",

        "set_bg": "#008000",
        "set_hover": "#00a000",
        "set_pressed": "#006000",

        "sim_bg": "#8e44ad",
        "sim_hover": "#9b59b6",
        "sim_pressed": "#6c3483",
        "sim_active_bg": "#e67e22",
        "sim_active_hover": "#f39c12",
        "sim_active_pressed": "#ca6f1e",
        "sim_disabled_text": "#999999",
        "sim_disabled_bg": "#e6e9ed",

        "ok_text": "#1a7f37",
        "warn_text": "#9a6700",
        "error_text": "#d00000",

        "chip_bg": "#e3e8ee",
        "chip_border": "#c8d0d8",
        "chip_text": "#333c46",
        "chip_checked_text": "#ffffff",

        "menu_bg": "#ffffff",
        "menu_text": "#111111",
        "menu_hover": "#dde7f3",

        "scroll_handle": "#c3ccd6",
        "scroll_handle_hover": "#a8b4c1",
    },

    "dark": {
        # Three tones: navigation sits below the page, panels above it.
        "app_bg": "#151719",
        "panel_bg": "#1e2124",
        "panel_border": "#32373d",
        "text": "#f0f2f4",
        "muted_text": "#9aa4b0",
        "input_bg": "#272b30",
        "input_border": "#3a4047",

        "nav_bg": "#0f1113",
        "nav_border": "#2a2f34",
        "nav_text": "#c3cad2",
        "nav_hover": "#22262a",
        "nav_active_bg": "#0078d7",
        "nav_active_text": "#ffffff",

        "refresh_bg": "#3a4047",
        "refresh_hover": "#474e57",
        "refresh_pressed": "#2a2f34",
        "refresh_text": "#f0f2f4",
        "refresh_border": "#525a63",

        "tare_bg": "#3d3a2c",
        "tare_hover": "#4b4736",
        "tare_pressed": "#2e2c22",
        "tare_text": "#ffcc00",
        "tare_border": "#5a5440",

        "record_bg": "#2e2426",
        "record_hover": "#3d2f31",
        "record_pressed": "#241c1e",
        "record_text": "#ff6b6b",
        "record_border": "#4a3a3c",
        "record_disabled_text": "#5c646d",
        "record_disabled_bg": "#1a1d20",

        "connect_bg": "#0078d7",
        "connect_hover": "#0086f0",
        "connect_pressed": "#005a9e",

        "disconnect_bg": "#d83b01",
        "disconnect_pressed": "#a83200",

        "set_bg": "#0f7a34",
        "set_hover": "#139342",
        "set_pressed": "#0b5c27",

        "sim_bg": "#8e44ad",
        "sim_hover": "#9b59b6",
        "sim_pressed": "#6c3483",
        "sim_active_bg": "#e67e22",
        "sim_active_hover": "#f39c12",
        "sim_active_pressed": "#ca6f1e",
        "sim_disabled_text": "#5c646d",
        "sim_disabled_bg": "#1a1d20",

        "ok_text": "#3fb950",
        "warn_text": "#d29922",
        "error_text": "#ff6b6b",

        "chip_bg": "#272b30",
        "chip_border": "#3a4047",
        "chip_text": "#c3cad2",
        "chip_checked_text": "#111111",

        "menu_bg": "#1e2124",
        "menu_text": "#f0f2f4",
        "menu_hover": "#32373d",

        "scroll_handle": "#3f464e",
        "scroll_handle_hover": "#525b65",
    },
}


# --- sensor cards and their charts -----------------------------------------

_CARD = {
    "light": {
        "card_bg": "#ffffff",
        "card_border": "#d0d7de",
        # A touch below the card, so the plotting area reads as inset
        "chart_bg": "#fafbfc",
        "label_text": "#111111",
        "title": "#0062ad",
        "raw": "#5a6472",
        "force": "#00733f",
        "gram": "#9a6700",
        "value_box_bg": "#f2f5f8",
        "value_box_border": "#d8dee4",
        "line": "#00733f",
        "tick": "#5a6472",
        "grid": "#dde3e9",
        "spine": "#c8d0d8",
        "grid_alpha": 0.7,
        # A socket that has stopped sending. Amber, not red: the rig is fine,
        # one input is not.
        "fault": "#b54708",
        "fault_bg": "#fff4e5",
    },

    "dark": {
        "card_bg": "#1e2124",
        "card_border": "#32373d",
        # A touch below the card, same relationship as the light variant
        "chart_bg": "#17191c",
        "label_text": "#f0f2f4",
        "title": "#4cc2ff",
        "raw": "#8b949e",
        "force": "#00ff7f",
        "gram": "#ffcc00",
        "value_box_bg": "#17191c",
        "value_box_border": "#32373d",
        "line": "#00ff7f",
        "tick": "#8b949e",
        "grid": "#2e3338",
        "spine": "#3a4047",
        "grid_alpha": 0.5,
        "fault": "#ffb454",
        "fault_bg": "#3a2a12",
    },
}


# Which palette each mode draws from
_MODE_SOURCES = {
    "light": {"app": "light", "card": "light"},
    "dark": {"app": "dark", "card": "dark"},
    "mix": {"app": "dark", "card": "light"},
}


# --- overlay series colours -------------------------------------------------
# Grouped by socket row, so a trace's family says which row it is on: A warm,
# B green, C cool. Within a family the four step through lightness and a little
# hue, which is what keeps them apart where four shades of one hue would not be.
#
# Two sets, because the chart background follows the card, not the window: the
# light set would be washed out on black and the dark set illegible on white.

_PLOT = {
    # Readable on a near-white chart
    "light": {
        "A": ["#f59f00", "#e8590c", "#e03131", "#a61e4d"],
        "B": ["#82c91e", "#37b24d", "#0ca678", "#0b7285"],
        "C": ["#4dabf7", "#1c7ed6", "#5f3dc4", "#9c36b5"],
    },
    # Readable on a near-black chart
    "dark": {
        "A": ["#ffd43b", "#ff922b", "#ff6b6b", "#f06595"],
        "B": ["#a9e34b", "#51cf66", "#12b886", "#63e6be"],
        "C": ["#74c0fc", "#4c6ef5", "#9775fa", "#da77f2"],
    },
}

_ROWS = ("A", "B", "C")


def _flatten(families: dict) -> list:
    return [colour for row in _ROWS for colour in families[row]]


# Mutated in place by set_theme, never rebound: importers hold these objects.
APP_COLORS: dict = {}
CARD_COLORS: dict = {}
PLOT_COLORS: list = []
PLOT_FAMILIES: dict = {}

_current_mode = DEFAULT_MODE
_current_scale = DEFAULT_SCALE


def current_mode() -> str:
    return _current_mode


def current_scale() -> int:
    return _current_scale


def set_scale(percent: int) -> int:
    """Store the interface scale, clamped to a step the menu offers."""
    global _current_scale
    _current_scale = percent if percent in SCALE_STEPS else DEFAULT_SCALE
    return _current_scale


def scrollbar_style() -> str:
    """
    Scrollbars that match the rest of the window.

    Qt's default scrollbars are drawn by the platform style and look pasted on
    against a themed window, which shows wherever a scroll area appears.
    """
    return f"""
            QScrollBar:horizontal {{
                background: transparent;
                height: 10px;
                margin: 0px;
            }}

            QScrollBar:vertical {{
                background: transparent;
                width: 10px;
                margin: 0px;
            }}

            QScrollBar::handle:horizontal, QScrollBar::handle:vertical {{
                background: {APP_COLORS["scroll_handle"]};
                border-radius: 5px;
            }}

            QScrollBar::handle:horizontal {{
                min-width: 32px;
            }}

            QScrollBar::handle:vertical {{
                min-height: 32px;
            }}

            QScrollBar::handle:hover {{
                background: {APP_COLORS["scroll_handle_hover"]};
            }}

            QScrollBar::add-line, QScrollBar::sub-line {{
                width: 0px;
                height: 0px;
            }}

            QScrollBar::add-page, QScrollBar::sub-page {{
                background: transparent;
            }}
    """


def set_theme(mode: str) -> str:
    """
    Switch palettes in place and return the mode actually applied.

    An unknown name falls back to the default rather than raising, so a
    hand-edited settings file cannot stop the app from starting.
    """
    global _current_mode

    if mode not in _MODE_SOURCES:
        mode = DEFAULT_MODE
    _current_mode = mode

    sources = _MODE_SOURCES[mode]

    APP_COLORS.clear()
    APP_COLORS.update(_APP[sources["app"]])

    CARD_COLORS.clear()
    CARD_COLORS.update(_CARD[sources["card"]])

    PLOT_COLORS[:] = _flatten(_PLOT[sources["card"]])
    PLOT_FAMILIES.clear()
    PLOT_FAMILIES.update(_PLOT[sources["card"]])

    return mode


def series_color(index: int) -> str:
    """Colour for the n-th plotted sensor, wrapping past the end of the list."""
    return PLOT_COLORS[index % len(PLOT_COLORS)]


def socket_color(position: str, fallback_index: int = 0) -> str:
    """
    Colour for a socket, from its own label rather than its place in a list.

    A socket keeps its colour however many are bound: with only B-1 plugged in
    it is still the first green, not the first colour of the whole palette.
    Anything that does not look like a socket falls back to the flat list.
    """
    text = "".join(ch for ch in str(position) if ch.isalnum()).upper()
    row, number = text[:1], text[1:]

    if row in PLOT_FAMILIES and number.isdigit():
        family = PLOT_FAMILIES[row]
        return family[(int(number) - 1) % len(family)]

    return series_color(fallback_index)


# --- persistence ------------------------------------------------------------

def _read_settings() -> dict:
    try:
        with open(_SETTINGS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_saved_mode() -> str:
    """Read the stored theme; fall back to the default on anything unexpected."""
    mode = _read_settings().get("theme", DEFAULT_MODE)
    return mode if mode in THEME_MODES else DEFAULT_MODE


def load_saved_scale() -> int:
    """Read the stored interface scale, ignoring anything off the ladder."""
    try:
        scale = int(_read_settings().get("scale", DEFAULT_SCALE))
    except (TypeError, ValueError):
        return DEFAULT_SCALE
    return scale if scale in SCALE_STEPS else DEFAULT_SCALE


def save_settings() -> None:
    """Remember theme and scale for the next run. Failing to save is not fatal."""
    try:
        os.makedirs(os.path.dirname(_SETTINGS_FILE), exist_ok=True)
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump({"theme": _current_mode, "scale": _current_scale}, fh, indent=2)
            fh.write("\n")
    except OSError as exc:
        print(f"WARNING: could not save display settings: {exc}")


# Apply the stored theme at import, before any widget is built.
set_theme(load_saved_mode())
set_scale(load_saved_scale())
