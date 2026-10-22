"""
Overlay page: every sensor on one chart, each in its own colour.

The toggle chips along the top switch a sensor's trace on and off, so a subset
can be compared without the other traces in the way. A sensor keeps its colour
whether or not it is currently shown, so the chips and the chart always agree.

Colours come from the socket: row A is warm, B green, C cool, and the four in
each row differ enough to tell apart. Which row a trace belongs to is then
readable from the chart without consulting the legend.

The chart redraws on a timer rather than on every sample. The array delivers
roughly 97 samples a second across 12 sockets, and redrawing 12 matplotlib
lines that often would spend all its time in the renderer.
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QPushButton, QLabel, QFrame, QSizePolicy,
                             QButtonGroup, QScrollArea)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from gui import y_axis
from gui.flow_layout import FlowLayout
from gui.combo_box import ReadableComboBox
from gui.theme import APP_COLORS as COLORS, CARD_COLORS as CHART, socket_color
from inference.polynomial_engine import (PHASE_LABELS, PHASE_DESCRIPTIONS,
                                         PHASE_MODES, DEFAULT_PHASE_MODE,
                                         PHASE_CONTROL_LABEL, TARGETS,
                                         TARGET_FORCE, TARGET_DISPLACEMENT,
                                         DEFAULT_TARGET, target_label)

# Samples held per trace; at ~8 Hz per sensor this is about a minute of history
HISTORY_POINTS = 500

# Chart refresh rate. Well below the data rate on purpose, see module docstring.
REDRAW_INTERVAL_MS = 100

# Strip geometry. The twelve columns share the width evenly, so a socket sits
# in the same place whatever it is called.
CHIP_HEIGHT = 40
CONTROL_HEIGHT = 26
BUTTON_WIDTH = 66

# How narrow a column may get before the strip stops shrinking. Twelve of these
# plus the buttons have to fit the smallest window the app is used on.
MIN_COLUMN_WIDTH = 58

# Margins, spacing and the scroll bar around the chip grid; taken off the window
# width before working out how many columns are left.
CHIP_BAR_CHROME = 60


class OverlayPage(QWidget):
    """All sensors on a single axes, with per-sensor visibility and phase."""

    phase_mode_changed = pyqtSignal(str, str)  # (socket, mode)
    target_changed = pyqtSignal(str)           # force or displacement

    def __init__(self, parent=None):
        super().__init__(parent)

        # key -> {"label", "color", "history", "line", "chip", "combo", "visible"}
        self.series: dict[str, dict] = {}
        # Sockets on a line right now; see _reflow_chips
        self._chip_columns = 0
        self._dirty = False
        self._loading = False

        # What the chart shows. One switch for the page: the traces share an
        # axes, and force and millimetres cannot sit on it together.
        self.target = DEFAULT_TARGET

        # Y-axis choice, kept per target for the same reason as on the cards
        self.y_choice = {t: (y_axis.MODE_DEFAULT, None) for t in TARGETS}
        self.maximum = {t: 0.0 for t in TARGETS}

        self._apply_page_style()
        self.init_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(REDRAW_INTERVAL_MS)
        self._timer.timeout.connect(self._redraw_if_dirty)
        self._timer.start()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        layout.addWidget(self._build_chip_bar())
        layout.addWidget(self._build_tabs())
        layout.addWidget(self._build_chart(), stretch=1)
        layout.addWidget(self._build_chart_controls())

    def _build_chip_bar(self) -> QFrame:
        """
        The sensor strip: one column per socket, all the same width.

        Laid out as a grid rather than a packed row so the columns share the
        width evenly up to the buttons on the right. A socket then sits in the
        same place whatever it is called.

        The strip wraps onto further lines as the window narrows - see
        _reflow_chips - and only scrolls if even one socket per line will not
        fit, so it never stops the window from being made smaller.

        The pickers carry the same wording as the dashboard, which is what the
        column headings used to stand in for; two names for one choice is worse
        than a long label.
        """
        bar = self.chip_bar = QFrame()
        bar._zoom_dynamic_dimensions = {"minimumHeight", "maximumHeight"}
        bar.setStyleSheet(self._panel_style())
        bar.setSizePolicy(QSizePolicy.Policy.Preferred,
                          QSizePolicy.Policy.Fixed)

        outer = QHBoxLayout(bar)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(8)

        holder = self.chip_holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        self.chip_grid = QGridLayout(holder)
        self.chip_grid.setContentsMargins(0, 0, 0, 0)
        self.chip_grid.setHorizontalSpacing(4)
        self.chip_grid.setVerticalSpacing(4)

        scroll = self.chip_scroll = QScrollArea()
        scroll._zoom_dynamic_dimensions = {"minimumHeight", "maximumHeight"}
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setMinimumWidth(MIN_COLUMN_WIDTH + 8)
        scroll.setWidget(holder)
        outer.addWidget(scroll, stretch=1)

        self.all_controls = self._build_all_controls()
        outer.addWidget(self.all_controls)
        return bar

    def _build_all_controls(self) -> QWidget:
        """
        Controls that act on every trace: visibility and the hysteresis curve.

        The curve picker is the same size as the twelve below the chips, so the
        strip reads as one table with a totals column rather than as two
        unrelated groups of controls.
        """
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")

        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(6)

        self.btn_all = QPushButton("ALL")
        self.btn_all.setFixedSize(BUTTON_WIDTH, CHIP_HEIGHT)
        self.btn_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_all.setStyleSheet(self._plain_button_style())
        self.btn_all.clicked.connect(lambda: self.set_all_visible(True))
        buttons.addWidget(self.btn_all)

        self.btn_none = QPushButton("NONE")
        self.btn_none.setFixedSize(BUTTON_WIDTH, CHIP_HEIGHT)
        self.btn_none.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_none.setStyleSheet(self._plain_button_style())
        self.btn_none.clicked.connect(lambda: self.set_all_visible(False))
        buttons.addWidget(self.btn_none)

        column.addLayout(buttons)

        self.all_phase_combo = ReadableComboBox()
        self.all_phase_combo.setFixedSize(BUTTON_WIDTH * 2 + 6, CONTROL_HEIGHT)
        self.all_phase_combo.setToolTip("\n".join(
            [f"{PHASE_CONTROL_LABEL} for every sensor:"] +
            [f"  {PHASE_LABELS[m]} - {PHASE_DESCRIPTIONS[m]}" for m in PHASE_MODES]))
        self.all_phase_combo.setPlaceholderText("Set hysteresis: all")
        for mode in PHASE_MODES:
            self.all_phase_combo.addItem(PHASE_LABELS[mode], userData=mode)
        self.all_phase_combo.setCurrentIndex(-1)
        self.all_phase_combo.activated.connect(self._on_all_phase)
        column.addWidget(self.all_phase_combo)

        self._apply_combo_styles()
        return holder

    def _build_tabs(self) -> QWidget:
        """Force and indentation depth, above the chart, for every trace."""
        holder = self.tab_bar = QWidget()
        holder.setStyleSheet("background: transparent;")
        holder.setFixedHeight(28)

        row = QHBoxLayout(holder)
        row.setContentsMargins(6, 0, 6, 0)
        row.setSpacing(4)

        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        self.tabs = {}

        for target in TARGETS:
            tab = QPushButton(target_label(target))
            tab.setCheckable(True)
            tab.setChecked(target == self.target)
            tab.setFixedHeight(26)
            tab.setMinimumWidth(110)
            tab.setCursor(Qt.CursorShape.PointingHandCursor)
            tab.clicked.connect(lambda _, t=target: self._on_tab(t))
            self.tab_group.addButton(tab)
            row.addWidget(tab)
            self.tabs[target] = tab

        row.addStretch()
        self._apply_tab_styles()
        return holder

    def set_target(self, target, emit=False):
        """Swap every trace to the other quantity."""
        if target not in TARGETS or target == self.target:
            return

        self.target = target
        for name, tab in self.tabs.items():
            tab.setChecked(name == target)

        for entry in self.series.values():
            entry["line"].set_ydata(entry["history"][target])

        self._fill_y_options()
        self._apply_chart_style()
        self._dirty = True
        self.canvas.draw_idle()

        if emit:
            self.target_changed.emit(target)

    def set_available_targets(self, targets):
        for name, tab in self.tabs.items():
            tab.setVisible(name in targets)
        if self.target not in targets and targets:
            self.set_target(targets[0])

    def _on_tab(self, target):
        self.set_target(target, emit=True)

    def _build_chart(self) -> QWidget:
        holder = self.chart_holder = QFrame()
        holder.setStyleSheet(f"""
            QFrame {{
                background-color: {CHART["card_bg"]};
                border: 1px solid {CHART["card_border"]};
                border-radius: 8px;
            }}
        """)
        holder.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        box = QVBoxLayout(holder)
        box.setContentsMargins(6, 6, 6, 4)
        box.setSpacing(2)

        self.fig = Figure(figsize=(10, 6), dpi=90, layout="constrained")
        self.canvas = FigureCanvas(self.fig)
        # Let the chart yield space before any controls leave a short screen.
        self.canvas.setMinimumHeight(80)

        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlim(0, HISTORY_POINTS - 1)
        self.ax.set_ylim(-0.05, 1.0)

        # Same data, two units: newtons on the left, gram-force on the right.
        # The rig is specified in gf and the equations are fitted in N, so
        # showing both saves converting in your head at the chart.
        self.ax_gf = self.ax.secondary_yaxis(
            "right", functions=(self._to_second_unit, self._from_second_unit))

        self._apply_chart_style()

        # The canvas takes every spare pixel; the controls keep their own
        # height instead of being stretched to fill the panel.
        box.addWidget(self.canvas, stretch=1)

        self.empty_label = QLabel("No sensors bound yet.\nChoose them on the Setup page.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet(f"color: {COLORS['muted_text']}; font-size: 14px;")
        self.empty_label.setWordWrap(True)
        box.addWidget(self.empty_label)

        return holder

    def _to_second_unit(self, value):
        """Engine unit to the right-hand axis: gram-force, or microns."""
        if self.target == TARGET_DISPLACEMENT:
            return y_axis.mm_to_um(value)
        return y_axis.newtons_to_gf(value)

    def _from_second_unit(self, value):
        if self.target == TARGET_DISPLACEMENT:
            return y_axis.um_to_mm(value)
        return y_axis.gf_to_newtons(value)

    def _build_chart_controls(self) -> QWidget:
        """
        Chart-wide controls, under the chart rather than in the top corner.

        The Y range belongs to the axes, not to any one sensor, so it sits with
        the chart where it is actually read. It lives on the page background
        rather than inside the chart panel: the panel follows the card palette,
        so in mix mode a control styled from the window palette ended up as
        pale grey text on a white panel.

        The readout beside it gives the span the choice currently works out to.
        "Default" on its own says nothing about where the axis will sit, and
        that is the first thing you want to know when comparing two runs.
        """
        holder = self.chart_controls = QWidget()
        holder.setStyleSheet("background: transparent;")
        holder.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        row = FlowLayout(holder)
        row.setContentsMargins(6, 2, 6, 2)
        row.setSpacing(10)

        self.y_label = QLabel("Y range")
        self.y_label.setStyleSheet(self._control_label_style())
        row.addWidget(self.y_label)

        self.y_combo = ReadableComboBox()
        self.y_combo.setFixedSize(190, 30)
        self.y_combo.setToolTip("Y-axis range for the chart")
        self.y_combo.currentIndexChanged.connect(self._on_y_changed)
        row.addWidget(self.y_combo)

        self.y_readout = QLabel("")
        self.y_readout.setStyleSheet(self._readout_style())
        row.addWidget(self.y_readout)

        row.addStretch()

        self.axis_hint = QLabel("")
        self.axis_hint.setStyleSheet(self._control_label_style())
        row.addWidget(self.axis_hint)

        self._fill_y_options()
        self.y_combo.setStyleSheet(self._combo_style(font_size=13))
        return holder

    def _update_y_readout(self) -> None:
        """Show the span the axis is on right now, in both of its units."""
        top = self.ax.get_ylim()[1]
        if self.target == TARGET_DISPLACEMENT:
            text = f"0 - {top:.3f} mm   /   0 - {y_axis.mm_to_um(top):.0f} um"
        else:
            text = f"0 - {top:.2f} N   /   0 - {y_axis.newtons_to_gf(top):.0f} gf"
        self.y_readout.setText(text)

    # --- series management -------------------------------------------------

    def set_series(self, entries) -> None:
        """
        Replace every trace.

        `entries` is [(key, socket_label, sensor_id), ...] in display order.
        The chip shows both, on two lines: the socket is where to look on the
        rig and the sensor id is what is plugged in, and confusing the two on a
        twelve-slot array is easy.
        """
        self.clear_series()

        for index, (key, socket_label, sensor_id) in enumerate(entries):
            # From the socket, not the position in this list: a socket keeps
            # its colour however many others happen to be bound.
            color = socket_color(key, index)
            full_label = f"{socket_label}  {sensor_id}"
            history = {t: [0.0] * HISTORY_POINTS for t in TARGETS}
            line, = self.ax.plot(history[self.target], color=color, lw=1.4,
                                 label=full_label)

            chip = self._make_chip(socket_label, sensor_id, color)
            chip.toggled.connect(lambda checked, k=key: self.set_visible(k, checked))

            combo = self._make_phase_combo()
            combo.currentIndexChanged.connect(
                lambda _, k=key: self._on_series_phase(k))

            # Placed by _reflow_chips, which decides how many fit on a line

            self.series[key] = {
                "label": full_label,
                "socket": socket_label,
                "sensor_id": sensor_id,
                "color": color,
                "history": history,
                "line": line,
                "chip": chip,
                "combo": combo,
                "visible": True,
            }

        # The chips are built without a place in the grid; this puts them in
        # one, at however many columns the current width allows.
        self._chip_columns = 0
        self._reflow_chips()

        self.empty_label.setVisible(not self.series)
        self.canvas.setVisible(bool(self.series))
        self._dirty = True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def reflow(self):
        """Lay the socket strip out for the window's current width."""
        self._reflow_chips()

    def _reflow_chips(self):
        """
        Lay the sockets out in as many columns as the width allows.

        Twelve across is the shape of the rig and is kept whenever it fits.
        Below that they wrap onto a second and third line rather than being
        squeezed past MIN_COLUMN_WIDTH, where the socket labels start to be
        clipped - and rather than holding the whole window open at twelve
        columns, which is what they used to do.
        """
        if not self.series:
            return

        spacing = self.chip_grid.horizontalSpacing()

        # Measured from the window, not from the grid's own geometry. The grid
        # is given the width its current column count demands, so asking it how
        # much room there is only ever confirms the layout it already has - the
        # strip would then never come down from twelve columns.
        available = (self.window().width()
                     - self.all_controls.sizeHint().width()
                     - CHIP_BAR_CHROME)
        # Measure the full two-line sensor name and every curve option. A
        # 58px floor fits the widget but cuts the actual text off.
        needed = MIN_COLUMN_WIDTH
        for entry in self.series.values():
            chip, combo = entry["chip"], entry["combo"]
            needed = max(needed, chip.sizeHint().width(),
                         max(combo.fontMetrics().horizontalAdvance(label)
                             for label in PHASE_LABELS.values()) + 24)
        for entry in self.series.values():
            entry["chip"].setMinimumWidth(needed)
            entry["combo"].setMinimumWidth(needed)

        capacity = max(1, (available + spacing) // (needed + spacing))
        lines = -(-len(self.series) // capacity)
        columns = -(-len(self.series) // lines)

        if columns == getattr(self, "_chip_columns", 0):
            self._resize_chip_bar(columns)
            return
        self._chip_columns = columns

        for index, entry in enumerate(self.series.values()):
            row, column = divmod(index, columns)
            self.chip_grid.addWidget(entry["chip"], row * 2, column)
            self.chip_grid.addWidget(entry["combo"], row * 2 + 1, column)

        for column in range(len(self.series)):
            self.chip_grid.setColumnStretch(column, 1 if column < columns else 0)

        self._resize_chip_bar(columns)

    def _resize_chip_bar(self, columns: int):
        """Give the strip the height the number of lines needs."""
        lines = -(-len(self.series) // columns) if columns else 1
        entry = next(iter(self.series.values()), None)
        line = ((entry["chip"].minimumHeight() + entry["combo"].minimumHeight()
                 if entry else CHIP_HEIGHT + CONTROL_HEIGHT)
                + self.chip_grid.verticalSpacing())
        height = lines * line + (lines - 1) * self.chip_grid.verticalSpacing()

        # On a short screen let the strip scroll, preserving chart space.
        visible_height = min(height, max(80, int(self.window().height() * 0.40)))
        self.chip_scroll.setFixedHeight(visible_height)
        # The panel adds its own margins around the strip, and the controls
        # column has to fit beside it whatever the strip is doing.
        self.chip_bar.setFixedHeight(
            max(visible_height, self.all_controls.sizeHint().height()) + 16)

    def clear_series(self) -> None:
        for index, entry in enumerate(self.series.values()):
            entry["line"].remove()
            entry["chip"].setParent(None)
            entry["combo"].setParent(None)
            entry["chip"].deleteLater()
            entry["combo"].deleteLater()
            self.chip_grid.setColumnStretch(index, 0)
        self.series = {}
        self._chip_columns = 0
        self._resize_chip_bar(0)
        self.ax.set_ylim(-0.05, 1.0)
        self.empty_label.setVisible(True)
        self.canvas.setVisible(False)
        self.canvas.draw_idle()

    def set_visible(self, key: str, visible: bool) -> None:
        entry = self.series.get(key)
        if entry is None or entry["visible"] == visible:
            return
        entry["visible"] = visible
        entry["line"].set_visible(visible)
        if entry["chip"].isChecked() != visible:
            entry["chip"].setChecked(visible)
        self._dirty = True

    def set_all_visible(self, visible: bool) -> None:
        for key in self.series:
            self.set_visible(key, visible)

    # --- phase -------------------------------------------------------------

    def set_phase_options(self, key: str, modes, current=None) -> None:
        """Limit one sensor's picker to the modes it can actually serve."""
        entry = self.series.get(key)
        if entry is None:
            return

        combo = entry["combo"]
        self._loading = True
        combo.clear()
        for mode in modes:
            combo.addItem(PHASE_LABELS[mode], userData=mode)
        index = combo.findData(current or DEFAULT_PHASE_MODE)
        combo.setCurrentIndex(max(0, index))
        combo.setEnabled(len(modes) > 1)
        self._loading = False

    def set_phase_mode(self, key: str, mode: str) -> None:
        """Reflect a mode chosen elsewhere, without emitting again."""
        entry = self.series.get(key)
        if entry is None:
            return
        index = entry["combo"].findData(mode)
        if index < 0:
            return
        self._loading = True
        entry["combo"].setCurrentIndex(index)
        self._loading = False

    def _on_series_phase(self, key: str) -> None:
        entry = self.series.get(key)
        if entry is None or self._loading:
            return
        self.phase_mode_changed.emit(key, entry["combo"].currentData())

    def _on_all_phase(self) -> None:
        mode = self.all_phase_combo.currentData()
        if mode is None:
            return
        for key, entry in self.series.items():
            self.set_phase_mode(key, mode)
            if entry["combo"].currentData() == mode:
                self.phase_mode_changed.emit(key, mode)
        self.all_phase_combo.setCurrentIndex(-1)

    # --- y axis ------------------------------------------------------------

    def set_maximum(self, target, maximum) -> None:
        """Largest calibrated maximum among the shown sensors, for one target."""
        self.maximum[target] = maximum or 0.0
        if target == self.target:
            self._fill_y_options()

    def set_max_force(self, max_force_n: float) -> None:
        self.set_maximum(TARGET_FORCE, max_force_n)

    def _fill_y_options(self) -> None:
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
        self._dirty = True
        self._update_y_readout()

    def set_y_range(self, key) -> None:
        index = self.y_combo.findData(key)
        if index >= 0:
            self.y_combo.setCurrentIndex(index)

    def _on_y_changed(self) -> None:
        key = self.y_combo.currentData()
        if key is None:
            return
        self.y_choice[self.target] = y_axis.parse_key(key)
        self._dirty = True
        self._update_y_readout()

    # --- live data ---------------------------------------------------------

    def update_value(self, key: str, values) -> None:
        """
        Append one sample for every target. Drawing happens on the timer.

        Both are kept whichever tab is showing, so switching reveals the
        history that was already gathered.
        """
        entry = self.series.get(key)
        if entry is None:
            return

        if not isinstance(values, dict):
            values = {TARGET_FORCE: values}

        for target, history in entry["history"].items():
            history.pop(0)
            history.append(max(0.0, values.get(target, 0.0)))

        self._dirty = True

    def reset_history(self) -> None:
        for entry in self.series.values():
            for history in entry["history"].values():
                history[:] = [0.0] * HISTORY_POINTS
        self._dirty = True

    def _redraw_if_dirty(self) -> None:
        if not self._dirty or not self.series or not self.isVisible():
            return
        self._dirty = False

        visible_max = 0.0
        for entry in self.series.values():
            history = entry["history"][self.target]
            entry["line"].set_ydata(history)
            if entry["visible"]:
                visible_max = max(visible_max, max(history))

        # Scale to what is actually on screen, so hiding the loud traces lets
        # the quiet ones fill the axes.
        _, ymax = self.ax.get_ylim()
        mode, value = self.y_choice[self.target]
        limits = y_axis.limits_for(mode, value, visible_max, ymax, self.target)
        if limits is not None:
            self.ax.set_ylim(*limits)
            self._update_y_readout()

        self._refresh_legend()
        self.canvas.draw_idle()

    def _refresh_legend(self) -> None:
        shown = [e for e in self.series.values() if e["visible"]]
        old = self.ax.get_legend()
        if old is not None:
            old.remove()
        if shown:
            self.ax.legend([e["line"] for e in shown], [e["label"] for e in shown],
                           loc="upper left", fontsize=8 * getattr(self, "_plot_scale", 1.0), ncol=2,
                           facecolor=CHART["card_bg"], edgecolor=CHART["spine"],
                           labelcolor=CHART["label_text"])

    # --- theming -----------------------------------------------------------

    def apply_theme(self):
        """
        Re-colour the page after a theme switch.

        Series colours change too: the chart background follows the card
        palette, and a palette readable on white is not readable on black.
        """
        self._apply_page_style()
        self.chip_bar.setStyleSheet(self._panel_style())
        self.chart_holder.setStyleSheet(self._chart_holder_style())
        self.btn_all.setStyleSheet(self._plain_button_style())
        self.btn_none.setStyleSheet(self._plain_button_style())
        self.empty_label.setStyleSheet(
            f"color: {COLORS['muted_text']}; font-size: 14px;")
        for label in (self.y_label, self.axis_hint):
            label.setStyleSheet(self._control_label_style())
        self.y_readout.setStyleSheet(self._readout_style())
        self.y_combo.setStyleSheet(self._combo_style(font_size=13))

        self._apply_combo_styles()
        self._apply_tab_styles()

        for index, (key, entry) in enumerate(self.series.items()):
            color = socket_color(key, index)
            entry["color"] = color
            entry["line"].set_color(color)
            entry["chip"].setStyleSheet(self._chip_style(color))
            entry["combo"].setStyleSheet(
                self._combo_style(font_size=11, arrow_width=8, horizontal_padding=2))

        self._apply_chart_style()
        self._dirty = True
        self.canvas.draw_idle()

    def _apply_page_style(self):
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {COLORS["app_bg"]};
                color: {COLORS["text"]};
                font-family: 'Segoe UI', sans-serif;
            }}

            QLabel {{
                color: {COLORS["text"]};
                background-color: transparent;
            }}
        """)

    def _apply_tab_styles(self):
        for tab in self.tabs.values():
            tab.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLORS["chip_bg"]};
                    color: {COLORS["muted_text"]};
                    border: 1px solid {COLORS["chip_border"]};
                    border-radius: 4px;
                    padding: 3px 14px;
                    font-size: 13px;
                    font-weight: bold;
                }}

                QPushButton:hover {{
                    color: {COLORS["text"]};
                }}

                QPushButton:pressed {{
                    background-color: {COLORS["chip_bg"]};
                    color: {COLORS["text"]};
                    padding: 5px 12px 1px 16px;
                }}

                QPushButton:checked {{
                    background-color: {COLORS["nav_active_bg"]};
                    color: {COLORS["nav_active_text"]};
                    border-color: {COLORS["nav_active_bg"]};
                }}
            """)

    def set_plot_scale(self, factor):
        self._plot_scale = factor
        self._apply_chart_style()
        self._refresh_legend()
        self.canvas.draw_idle()

    def _apply_chart_style(self):
        factor = getattr(self, "_plot_scale", 1.0)
        self.fig.set_facecolor(CHART["card_bg"])
        self.ax.set_facecolor(CHART["chart_bg"])
        self.ax.set_xlabel("Samples", color=CHART["tick"], fontsize=9 * factor)
        left, right = ((f"{target_label(self.target)} (mm)", f"{target_label(self.target)} (um)")
                       if self.target == TARGET_DISPLACEMENT
                       else ("Force (N)", "Force (gf)"))
        if factor > 1 and self.target == TARGET_DISPLACEMENT:
            # A zoomed, short chart cannot fit the long rotated title plus
            # its unit on one line. Keep both visible beside the axes.
            left, right = left.replace(" (", "\n("), right.replace(" (", "\n(")
        self.ax.set_ylabel(left, color=CHART["tick"], fontsize=9 * factor)
        self.ax.tick_params(axis="both", colors=CHART["tick"], labelsize=8 * factor)

        self.ax_gf.set_ylabel(right, color=CHART["tick"], fontsize=9 * factor)
        self.ax_gf.tick_params(axis="y", colors=CHART["tick"], labelsize=8 * factor)
        for spine in self.ax_gf.spines.values():
            spine.set_edgecolor(CHART["spine"])
        self.ax.grid(True, color=CHART["grid"], linestyle="--",
                     linewidth=0.5, alpha=CHART["grid_alpha"])
        for spine in self.ax.spines.values():
            spine.set_edgecolor(CHART["spine"])

    def _panel_style(self) -> str:
        return f"""
            QFrame {{
                background-color: {COLORS["panel_bg"]};
                border: 1px solid {COLORS["panel_border"]};
                border-radius: 6px;
            }}
        """

    def _chart_holder_style(self) -> str:
        return f"""
            QFrame {{
                background-color: {CHART["card_bg"]};
                border: 1px solid {CHART["card_border"]};
                border-radius: 8px;
            }}
        """

    # --- styling -----------------------------------------------------------

    def _make_chip(self, socket_label: str, sensor_id: str, color: str) -> QPushButton:
        chip = QPushButton(f"{socket_label}\n{sensor_id}")
        chip.setCheckable(True)
        chip.setChecked(True)
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.setFixedHeight(CHIP_HEIGHT)
        chip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # The strip measures the full label before choosing its column count.
        chip._zoom_dynamic_dimensions = {"minimumWidth"}
        chip.setMinimumWidth(MIN_COLUMN_WIDTH)
        chip.setToolTip(f"{socket_label}  {sensor_id}  -  click to show or hide")
        chip.setStyleSheet(self._chip_style(color))
        return chip

    def _make_phase_combo(self) -> ReadableComboBox:
        combo = ReadableComboBox()
        combo.setFixedHeight(CONTROL_HEIGHT)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # The strip determines the minimum from the labels instead of letting
        # an initial size hint lock it to twelve columns.
        combo.setSizeAdjustPolicy(
            ReadableComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(4)
        combo._zoom_dynamic_dimensions = {"minimumWidth"}
        combo.setMinimumWidth(MIN_COLUMN_WIDTH)

        combo.setToolTip("\n".join(
            [f"{PHASE_CONTROL_LABEL}:"] +
            [f"  {PHASE_LABELS[m]} - {PHASE_DESCRIPTIONS[m]}" for m in PHASE_MODES]))

        for mode in PHASE_MODES:
            combo.addItem(PHASE_LABELS[mode], userData=mode)
        combo.setCurrentIndex(combo.findData(DEFAULT_PHASE_MODE))
        # Compact text and arrow area keep all three labels visible in the strip.
        combo.setStyleSheet(
            self._combo_style(font_size=11, arrow_width=8, horizontal_padding=2))
        return combo

    def _apply_combo_styles(self) -> None:
        # The two controls are built in different parts of the page, so style
        # whichever already exists rather than depending on build order.
        combo = getattr(self, "all_phase_combo", None)
        if combo is not None:
            combo.setStyleSheet(self._combo_style())

    def _combo_style(self, font_size: int = 12, arrow_width: int = 14,
                     horizontal_padding: int = 6) -> str:
        return f"""
            QComboBox {{
                background-color: {COLORS["input_bg"]};
                color: {COLORS["text"]};
                border: 1px solid {COLORS["input_border"]};
                border-radius: 4px;
                padding: 1px {horizontal_padding}px;
                font-size: {font_size}px;
            }}

            QComboBox::drop-down {{
                border: none;
                width: {arrow_width}px;
            }}

            QComboBox QAbstractItemView {{
                font-size: 13px;
                background-color: {COLORS["input_bg"]};
                color: {COLORS["text"]};
                selection-background-color: {COLORS["connect_bg"]};
                selection-color: white;
                border: 1px solid {COLORS["input_border"]};
            }}

            QComboBox:disabled {{
                color: {COLORS["muted_text"]};
            }}
        """

    def _chip_style(self, color: str) -> str:
        """
        A channel tile rather than a pill.

        Unchecked keeps the sensor's colour as a left edge, so a hidden trace
        can still be matched to its line on the chart.
        """
        return f"""
            QPushButton {{
                background-color: {COLORS["chip_bg"]};
                color: {COLORS["chip_text"]};
                border: 1px solid {COLORS["chip_border"]};
                border-left: 3px solid {color};
                border-radius: 5px;
                padding: 2px 6px;
                font-size: 12px;
                font-weight: bold;
                text-align: center;
            }}

            QPushButton:hover {{
                border-color: {color};
                border-left: 3px solid {color};
            }}

            QPushButton:pressed {{
                border-color: {color};
                border-left: 3px solid {color};
                padding: 4px 4px 0px 8px;
            }}

            QPushButton:checked {{
                background-color: {color};
                color: {COLORS["chip_checked_text"]};
                border: 1px solid {color};
                border-left: 3px solid {color};
            }}
        """

    def _control_label_style(self) -> str:
        return f"color: {COLORS['muted_text']}; font-size: 13px;"

    def _readout_style(self) -> str:
        return (f"color: {COLORS['text']}; font-size: 13px; font-weight: bold; "
                f"font-family: 'Consolas', monospace;")

    def _plain_button_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: {COLORS["refresh_bg"]};
                color: {COLORS["refresh_text"]};
                border: 1px solid {COLORS["refresh_border"]};
                border-radius: 4px;
                padding: 6px;
                font-weight: bold;
                font-size: 12px;
            }}

            QPushButton:hover {{
                background-color: {COLORS["refresh_hover"]};
            }}

            QPushButton:pressed {{
                background-color: {COLORS["refresh_pressed"]};
                padding-top: 8px;
                padding-left: 8px;
                padding-bottom: 4px;
                padding-right: 4px;
            }}
        """
