import time

from PyQt6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QButtonGroup, QSizePolicy)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from gui import y_axis
from gui.combo_box import ReadableComboBox
from gui.theme import CARD_COLORS as COLORS, APP_COLORS as APP
from inference.polynomial_engine import (PHASE_LABELS, PHASE_DESCRIPTIONS,
                                         PHASE_MODES, DEFAULT_PHASE_MODE,
                                         PHASE_CONTROL_LABEL, TARGETS,
                                         TARGET_FORCE, TARGET_DISPLACEMENT,
                                         DEFAULT_TARGET, target_label)


# How often the chart repaints, in milliseconds.
#
# NOT on every sample. A matplotlib redraw of one card costs about 20 ms, and
# the serial source polls at 100 Hz: redrawing per sample asks for ~2000 ms of
# rendering per second of data from a single card, and around four times that
# with four sockets filled. The GUI thread cannot catch up, so clicks and
# keystrokes queue behind a permanent backlog of repaints - which is what made
# typing into the volume box crawl whenever sensors were connected.
#
# The overlay page has always worked this way (overlay_page.REDRAW_INTERVAL_MS);
# the cards had not. 100 ms is 10 Hz, well above what an eye reads as live.
REDRAW_INTERVAL_MS = 100

# A socket is called faulty once this long has passed with nothing from it.
# Generous on purpose: the simulation runs at 25 Hz and the serial poll at
# 100 Hz, so two seconds of silence is a real fault, not a slow moment.
STALE_AFTER_S = 2.0


class SensorCard(QFrame):
    """One socket: live value, rolling chart, and its own phase and Y choices."""

    phase_mode_changed = pyqtSignal(str, str)  # (card key, mode)
    target_changed = pyqtSignal(str, str)      # (card key, target)

    def __init__(self, sensor_id, display_name, parent=None, subtitle=""):
        super().__init__(parent)
        self.sensor_id = sensor_id
        self.display_name = display_name
        # Which physical sensor is bound to this socket. The socket label alone
        # does not say that, and mixing the two up is easy on a 12-slot rig.
        self.subtitle = subtitle
        self.max_points = 100
        self.last_raw = 0.0  # Crucial for Tare functionality

        # Which quantity the chart and the big number show. Both are recorded
        # whichever is displayed; this only decides what is on screen.
        self.target = DEFAULT_TARGET

        # A rolling window per target, so switching tabs shows the history that
        # was already gathered rather than starting from an empty chart.
        self.history = {t: [0.0] * self.max_points for t in TARGETS}

        # Y-axis choice, remembered per target: gram-force and millimetres are
        # not interchangeable, so one shared setting would be meaningless.
        self.y_choice = {t: (y_axis.MODE_DEFAULT, None) for t in TARGETS}
        self.maximum = {t: 0.0 for t in TARGETS}

        self._loading = False
        self.init_ui()

    @property
    def force_history(self):
        """The active target's rolling window."""
        return self.history[self.target]

    @property
    def y_mode(self):
        return self.y_choice[self.target][0]

    @property
    def y_value(self):
        return self.y_choice[self.target][1]

    def init_ui(self):
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._apply_frame_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(3)

        # --- HEADER PANEL ---
        header_layout = QHBoxLayout()
        header_layout.setSpacing(3)

        # Socket label (A-1, A-2, ...)
        self.title_label = QLabel(self.display_name)

        self.raw_label = QLabel("R: 0")
        self.value_label = QLabel("0.00 N")
        self.gram_label = QLabel("0 g")

        # Give the label a parent before making it visible; otherwise Qt
        # briefly shows it as a separate window while the card is being built.
        self.subtitle_label = QLabel(self.subtitle, self)
        self.subtitle_label.setVisible(bool(self.subtitle))

        # Shown only when the socket stops reporting; invisible and costing
        # nothing the rest of the time.
        self.fault_label = QLabel("NO DATA", self)
        self.fault_label.setVisible(False)
        self._faulted = False
        self._last_sample_ts = None
        # Cards are built from the saved bindings at start-up, long before
        # anything is acquiring. Judging silence before then would put NO DATA
        # on all twelve of them while the rig simply sits idle.
        self._expecting = False

        self._apply_label_styles()

        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.subtitle_label)
        header_layout.addWidget(self.fault_label)
        header_layout.addStretch()
        header_layout.addWidget(self.raw_label)
        header_layout.addWidget(self.value_label)
        header_layout.addWidget(self.gram_label)
        layout.addLayout(header_layout)
        layout.addLayout(self._build_tabs())

        # --- CHART AREA ---
        self.fig = Figure(figsize=(4, 2), dpi=80, layout="constrained")
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setMinimumHeight(24)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

        # --- what the repainting costs, and how it is kept down -------------
        # A sample marks the chart dirty; the timer is what repaints it. True
        # at birth so the first tick paints the empty trace.
        self._dirty = True
        # Constrained layout re-solves the axes position on every draw, about
        # 10 of the ~18 ms one cost. The solution only changes with the card's
        # size, so it runs for one draw after a resize and is off in between.
        self._relayout = True
        # Everything except the trace, cached as a bitmap, so a new sample
        # costs only drawing the line (~0.2 ms against ~7 ms for the lot).
        self._bg = None
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setInterval(REDRAW_INTERVAL_MS)
        self._redraw_timer.timeout.connect(self._redraw_if_dirty)
        self._redraw_timer.start()

        self.ax = self.fig.add_subplot(111)
        self.line, = self.ax.plot(self.force_history, lw=1.5)
        # Drawn by hand on top of a cached background - see _redraw_if_dirty.
        self.line.set_animated(True)

        self.ax.set_xlim(0, self.max_points - 1)
        self.ax.set_ylim(-0.05, 1.0)
        self.ax.tick_params(axis="both", labelsize=7)

        self._apply_chart_style()

        layout.addWidget(self.canvas, stretch=1)

        layout.addLayout(self._build_controls())

    def minimumSizeHint(self):
        # Do not let an individual sensor's changing value resize the window.
        return QSize(0, 100)

    def _build_tabs(self) -> QHBoxLayout:
        """Force and indentation depth, above the chart."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        self.tabs = {}

        for target in TARGETS:
            tab = QPushButton(target_label(target))
            tab.setToolTip(target_label(target))
            tab.setCheckable(True)
            tab.setChecked(target == self.target)
            tab.setFixedHeight(22)
            tab.setCursor(Qt.CursorShape.PointingHandCursor)
            tab.clicked.connect(lambda _, t=target: self._on_tab(t))
            self.tab_group.addButton(tab)
            row.addWidget(tab)
            self.tabs[target] = tab

        row.addStretch()
        self._apply_tab_styles()
        return row

    def set_target(self, target, emit=False):
        """Show a different quantity. The history of both is kept either way."""
        if target not in TARGETS or target == self.target:
            return

        self.target = target
        for name, tab in self.tabs.items():
            tab.setChecked(name == target)

        self._fill_y_options()
        self.line.set_ydata(self.history[target])
        self._refresh_value_labels()
        self._apply_y_limits(force=True)
        self._invalidate_background()

        if emit:
            self.target_changed.emit(self.sensor_id, target)

    def set_available_targets(self, targets):
        """
        Hide a tab the equations file cannot serve.

        A file written before displacement was fitted has only force, and a tab
        that reads zero for every sample would look like a dead sensor.
        """
        for name, tab in self.tabs.items():
            tab.setVisible(name in targets)
        if self.target not in targets and targets:
            self.set_target(targets[0])

    def _on_tab(self, target):
        self.set_target(target, emit=True)

    def _build_controls(self) -> QHBoxLayout:
        """Per-card phase and Y-range pickers, under the chart."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.phase_combo = ReadableComboBox()
        self.phase_combo.setFixedHeight(22)
        self.phase_combo.setSizeAdjustPolicy(ReadableComboBox.SizeAdjustPolicy.AdjustToContents)
        for mode in PHASE_MODES:
            self.phase_combo.addItem(PHASE_LABELS[mode], userData=mode)
        self.phase_combo.setCurrentIndex(self.phase_combo.findData(DEFAULT_PHASE_MODE))
        self.phase_combo.setToolTip("\n".join(
            [f"{PHASE_CONTROL_LABEL}:"] +
            [f"  {PHASE_LABELS[m]} - {PHASE_DESCRIPTIONS[m]}" for m in PHASE_MODES]))
        self.phase_combo.currentIndexChanged.connect(self._on_phase_changed)

        self.y_combo = ReadableComboBox()
        self.y_combo.setFixedHeight(22)
        self.y_combo.setSizeAdjustPolicy(ReadableComboBox.SizeAdjustPolicy.AdjustToContents)
        self.y_combo.setToolTip("Y-axis range")
        self.y_combo.currentIndexChanged.connect(self._on_y_changed)
        self._fill_y_options()

        row.addWidget(self.phase_combo, stretch=3)
        row.addWidget(self.y_combo, stretch=2)

        self._apply_control_styles()
        return row

    # --- phase -------------------------------------------------------------

    def set_phase_options(self, modes, current=None):
        """
        Limit the picker to the modes this sensor can serve.

        A sensor fitted with one curve cannot do unloading or auto; offering
        them would read from a curve that is not there.
        """
        self._loading = True
        self.phase_combo.clear()
        for mode in modes:
            self.phase_combo.addItem(PHASE_LABELS[mode], userData=mode)

        index = self.phase_combo.findData(current or DEFAULT_PHASE_MODE)
        self.phase_combo.setCurrentIndex(max(0, index))
        self.phase_combo.setEnabled(len(modes) > 1)
        self._loading = False

    def set_phase_mode(self, mode):
        """Set the mode without emitting, used by the apply-to-all control."""
        index = self.phase_combo.findData(mode)
        if index < 0:
            return
        self._loading = True
        self.phase_combo.setCurrentIndex(index)
        self._loading = False

    def phase_mode(self):
        return self.phase_combo.currentData()

    def _on_phase_changed(self):
        if not self._loading:
            self.phase_mode_changed.emit(self.sensor_id, self.phase_combo.currentData())

    # --- y axis ------------------------------------------------------------

    def set_maximum(self, target, maximum):
        """Calibrated maximum for a target, which decides its fixed ladder."""
        self.maximum[target] = maximum or 0.0
        if target == self.target:
            self._fill_y_options()

    def set_max_force(self, max_force_n):
        """Kept for callers that only know about force."""
        self.set_maximum(TARGET_FORCE, max_force_n)

    def _fill_y_options(self):
        target = self.target
        previous = y_axis.key_for(*self.y_choice[target])

        self._loading = True
        self.y_combo.clear()
        for label, key in y_axis.options_for(self.maximum[target], target):
            self.y_combo.addItem(label, userData=key)

        index = self.y_combo.findData(previous)
        self.y_combo.setCurrentIndex(index if index >= 0 else 0)
        self._loading = False

        self.y_choice[target] = y_axis.parse_key(self.y_combo.currentData())
        self._apply_y_limits(force=True)

    def set_y_range(self, key):
        """
        Set the range without emitting, used by the apply-to-all control.

        A range above this sensor's ladder is simply not offered here, so the
        card keeps what it had rather than showing a ceiling it cannot reach.
        """
        index = self.y_combo.findData(key)
        if index < 0:
            return
        self._loading = True
        self.y_combo.setCurrentIndex(index)
        self._loading = False
        self.y_choice[self.target] = y_axis.parse_key(key)
        self._apply_y_limits(force=True)

    def y_range_key(self):
        return y_axis.key_for(*self.y_choice[self.target])

    def _on_y_changed(self):
        key = self.y_combo.currentData()
        if key is None:
            return
        self.y_choice[self.target] = y_axis.parse_key(key)
        if not self._loading:
            self._apply_y_limits(force=True)

    def _apply_y_limits(self, force=False):
        """Returns True when the axis moved and the canvas needs a full draw."""
        _, top = self.ax.get_ylim()
        mode, value = self.y_choice[self.target]
        limits = y_axis.limits_for(mode, value, max(self.history[self.target]),
                                   top, self.target)
        if limits is None:
            return False

        self.ax.set_ylim(*limits)
        if force:
            self._invalidate_background()
        return True

    # --- theming -----------------------------------------------------------

    def _apply_tab_styles(self):
        for target, tab in self.tabs.items():
            tab.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: {COLORS["raw"]};
                    border: none;
                    border-bottom: 2px solid transparent;
                    padding: 2px 10px;
                    font-size: 12px;
                    font-weight: bold;
                }}

                QPushButton:hover {{
                    color: {COLORS["label_text"]};
                }}

                QPushButton:pressed {{
                    color: {COLORS["title"]};
                    padding: 4px 8px 0px 12px;
                }}

                QPushButton:checked {{
                    color: {COLORS["title"]};
                    border-bottom: 2px solid {COLORS["title"]};
                }}
            """)

    def apply_theme(self):
        """
        Re-colour an existing card after a theme switch.

        Stylesheets and matplotlib artists both hold resolved colours, so a
        palette change is not picked up on its own; each has to be re-applied.
        """
        self._apply_frame_style()
        self._apply_label_styles()
        self._apply_tab_styles()
        self._apply_control_styles()
        self._apply_chart_style()
        self._invalidate_background()

    def _apply_frame_style(self):
        self.setStyleSheet(f"""
            SensorCard {{
                background-color: {COLORS["card_bg"]};
                border: 1px solid {COLORS["card_border"]};
                border-radius: 8px;
            }}

            QLabel {{
                color: {COLORS["label_text"]};
                font-family: 'Segoe UI', sans-serif;
                font-size: 13px;
                background-color: transparent;
            }}
        """)

    def _apply_label_styles(self):
        value_box = f"""
            background-color: {COLORS["value_box_bg"]};
            border: 1px solid {COLORS["value_box_border"]};
            border-radius: 4px;
            padding: 3px 2px;
        """

        self.title_label.setStyleSheet(f"""
            font-size: 15px;
            font-weight: bold;
            color: {COLORS["title"]};
            padding: 1px;
            background-color: transparent;
        """)

        self.subtitle_label.setStyleSheet(f"""
            font-size: 10px;
            color: {COLORS["raw"]};
            padding: 1px;
            background-color: transparent;
        """)

        self.raw_label.setStyleSheet(f"""
            font-size: 11px;
            color: {COLORS["raw"]};
            {value_box}
        """)

        reading = COLORS["raw"] if self._faulted else COLORS["force"]
        self.value_label.setStyleSheet(f"""
            font-size: 15px;
            font-weight: bold;
            color: {reading};
            {value_box}
        """)

        self.fault_label.setStyleSheet(f"""
            font-size: 10px;
            font-weight: bold;
            color: {COLORS["fault"]};
            background-color: {COLORS["fault_bg"]};
            border: 1px solid {COLORS["fault"]};
            border-radius: 3px;
            padding: 1px 4px;
        """)

        self.gram_label.setStyleSheet(f"""
            font-size: 13px;
            font-weight: bold;
            color: {COLORS["gram"]};
            {value_box}
        """)

    def _apply_control_styles(self):
        style = f"""
            QComboBox {{
                background-color: {APP["input_bg"]};
                color: {APP["text"]};
                border: 1px solid {APP["input_border"]};
                border-radius: 3px;
                padding: 1px 4px;
                font-size: 11px;
            }}

            QComboBox::drop-down {{
                border: none;
                width: 14px;
            }}

            QComboBox QAbstractItemView {{
                font-size: 13px;
                background-color: {APP["input_bg"]};
                color: {APP["text"]};
                selection-background-color: {APP["connect_bg"]};
                selection-color: white;
                border: 1px solid {APP["input_border"]};
            }}

            QComboBox:disabled {{
                color: {APP["muted_text"]};
            }}
        """
        self.phase_combo.setStyleSheet(style)
        self.y_combo.setStyleSheet(style)

    def set_plot_scale(self, factor):
        self._plot_scale = factor
        self.ax.tick_params(axis="both", labelsize=7 * factor)
        self.ax.xaxis.offsetText.set_fontsize(7 * factor)
        self.ax.yaxis.offsetText.set_fontsize(7 * factor)
        self._relayout = True
        self._invalidate_background()

    def _apply_chart_style(self):
        self.fig.set_facecolor(COLORS["card_bg"])
        self.ax.set_facecolor(COLORS["chart_bg"])
        self.line.set_color(COLORS["line"])
        self.ax.tick_params(axis="both", colors=COLORS["tick"],
                            labelsize=7 * getattr(self, "_plot_scale", 1.0))
        self.ax.grid(True, color=COLORS["grid"], linestyle="--",
                     linewidth=0.5, alpha=COLORS["grid_alpha"])

        for spine in self.ax.spines.values():
            spine.set_edgecolor(COLORS["spine"])

    def update_data(self, raw_val, values):
        """
        One sample. `values` is every target the engine produced.

        Both histories advance whichever tab is showing, so switching tabs
        reveals what was already happening rather than an empty chart. A single
        float is still accepted, and read as force.
        """
        self.last_raw = raw_val  # Store the latest raw value for taring
        self._last_sample_ts = time.monotonic()
        if self._faulted:
            self._set_faulted(False)

        if not isinstance(values, dict):
            values = {TARGET_FORCE: values}

        for target, history in self.history.items():
            history.pop(0)
            history.append(max(0.0, values.get(target, 0.0)))

        self._latest = values
        self._refresh_value_labels()
        self.raw_label.setText(f"R: {int(raw_val)}")

        self._dirty = True

    def _redraw_if_dirty(self):
        """Repaint at most once per interval, and only when on screen.

        Every matplotlib call lives here, not in update_data. Handing the line
        new data marks the figure stale, and a stale figure is re-rendered in
        full by the next paint event from any source - so leaving set_ydata on
        the sample path would keep paying the ~20 ms render however carefully
        the draw call itself was throttled.

        A card on a hidden page still keeps its history up to date; it just
        does not pay to render it until the operator looks at it.
        """
        self._check_stale()
        if not self._dirty or not self.isVisible():
            return
        self._dirty = False

        self.line.set_ydata(self.history[self.target])
        rescaled = self._apply_y_limits()

        # The cached background is only good while the axes stay put.
        if rescaled or self._relayout or self._bg is None:
            self._full_draw()
        else:
            self.canvas.restore_region(self._bg)
            self.ax.draw_artist(self.line)
            self.canvas.blit(self.ax.bbox)

    def set_expecting_data(self, expecting: bool):
        """Told when acquisition starts and stops.

        While acquiring, a socket that says nothing is a fault - including one
        that never said anything, which is what an unplugged or missing sensor
        looks like. While idle, nothing is expected and nothing is flagged.
        """
        if expecting == self._expecting:
            return
        self._expecting = expecting
        # Start the clock now, so a slow first sample is not called a fault.
        self._last_sample_ts = time.monotonic() if expecting else None
        if not expecting and self._faulted:
            self._set_faulted(False)

    def _check_stale(self):
        """Flag a socket that has gone quiet.

        Only ever reports; the chart keeps whatever history it had rather than
        being fed zeroes, so a dropped cable leaves a flat trailing line and a
        badge, not a fake reading.
        """
        if not self._expecting or self._last_sample_ts is None:
            return
        stale = (time.monotonic() - self._last_sample_ts) > STALE_AFTER_S
        if stale != self._faulted:
            self._set_faulted(stale)

    def _set_faulted(self, faulted: bool):
        self._faulted = faulted
        self.fault_label.setVisible(faulted)
        self._apply_label_styles()

    def is_faulted(self) -> bool:
        """True while this socket is not reporting."""
        return self._faulted

    def _full_draw(self):
        """Render the chart and re-cache the background the blits sit on.

        The trace is `animated`, so canvas.draw() paints everything but it -
        which is exactly the background wanted - and the line goes on top by
        hand afterwards.
        """
        self._settle_layout()
        self.canvas.draw()
        self._bg = self.canvas.copy_from_bbox(self.ax.bbox)
        self.ax.draw_artist(self.line)
        self.canvas.blit(self.ax.bbox)

    def _invalidate_background(self):
        """For anything that changes the chart other than a new sample."""
        self._bg = None
        self._dirty = True

    def _settle_layout(self):
        """Solve the figure layout once after a resize, then leave it alone."""
        self.fig.set_layout_engine("constrained" if self._relayout else "none")
        self._relayout = False

    def resizeEvent(self, event):
        # A new size needs the layout solved again, and makes the cached
        # background the wrong shape.
        self._relayout = True
        self._invalidate_background()
        super().resizeEvent(event)

    def _refresh_value_labels(self):
        """
        The two readouts, in whatever units the shown target is read in.

        Force is given in newtons and grams, indentation in millimetres and
        microns: the second figure is the one an operator compares against a
        specification, and it differs per quantity.
        """
        latest = getattr(self, "_latest", {})
        value = max(0.0, latest.get(self.target, self.history[self.target][-1]))

        if self.target == TARGET_DISPLACEMENT:
            self.value_label.setText(f"{value:.3f} mm")
            self.gram_label.setText(f"{int(value * 1000)} um")
        else:
            self.value_label.setText(f"{value:.2f} N")
            self.gram_label.setText(f"{int(value * 101.97)} g")
