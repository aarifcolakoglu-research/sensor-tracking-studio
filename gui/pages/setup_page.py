"""
Setup page: run the calibration, then say what is plugged into each socket.

Two panels. The left one runs the three-stage pipeline and reports what came
out of it. The right one is the socket-to-sensor mapping: pick a set to fill
all twelve at once, then override any socket that is plugged differently.

The mapping is what makes the rest of the app work. The Teensy reports a
socket, not a sensor, so without it there is no way to know which equation to
apply to "A-1".

Calibration runs on a worker thread. The stage that labels the raw data reads
about a gigabyte and takes a couple of minutes; running it on the UI thread
would freeze the window for that whole time.
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QPushButton, QLabel, QFrame, QScrollArea,
                             QSizePolicy, QProgressBar, QBoxLayout)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from gui.combo_box import ReadableComboBox
from gui.flow_layout import FlowLayout
from gui.theme import APP_COLORS as COLORS
import os

import app_paths
from calibration.pipeline import STAGE_NAMES
from calibration.sensor_ids import group_by_set, scan_raw_directory
from inference.polynomial_engine import (FIT_COMBINED, TARGETS, degree_label,
                                         fit_source_label, target_label)
from inference.sensor_binding import (BindingStore, SensorCatalog, POSITIONS,
                                      display_position)

UNBOUND = "-- empty --"
SUMMARY_ROW_SPACING = 11


class CalibrationWorker(QThread):
    """Runs the calibration pipeline off the UI thread."""

    progress = pyqtSignal(int, int, str, object)
    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def run(self):
        try:
            from calibration.pipeline import run_calibration
            summary = run_calibration(
                progress=lambda s, t, m, f: self.progress.emit(s, t, m, f))
            self.finished_ok.emit(summary)
        except Exception as exc:
            self.failed.emit(str(exc))


class SetupPage(QWidget):
    """Calibration control plus the socket-to-sensor mapping."""

    bindings_changed = pyqtSignal()
    calibration_finished = pyqtSignal()
    fit_source_changed = pyqtSignal(object)   # "combined" or a cycle number
    fit_degree_changed = pyqtSignal(int)      # 2 or 3

    def __init__(self, parent=None):
        super().__init__(parent)

        self.store = BindingStore().load()
        self.catalog = SensorCatalog(fit_source=self.store.fit_source,
                                     degree=self.store.fit_degree)
        self.worker = None

        # position -> the widgets of that row
        self.sensor_combos: dict[str, ReadableComboBox] = {}
        # Which calibration run each socket reads, by date
        self.date_combos: dict[str, ReadableComboBox] = {}
        # position -> target -> the R2 label for that column
        self.r2_labels: dict[str, dict[str, QLabel]] = {}
        self.notes: list[QLabel] = []
        self.panels: list[QFrame] = []
        self.column_headers: list[QLabel] = []
        self.stat_labels: list[QLabel] = []
        self._loading = False

        self._apply_page_style()
        self.init_ui()
        self.refresh_raw_summary()
        self.reload_catalog()

    def init_ui(self):
        layout = self.panel_layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        calibration = self._build_calibration_panel()
        scroll = self.calibration_scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(calibration)
        container = QWidget()
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(scroll, 1)
        layout.addWidget(container, 5)
        layout.addWidget(self._build_binding_panel(), 7)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def reflow(self):
        """Stack the panels on narrow screens; retain their readable widths."""
        direction = (QBoxLayout.Direction.TopToBottom if self.window().width() < 900
                     else QBoxLayout.Direction.LeftToRight)
        if self.panel_layout.direction() != direction:
            self.panel_layout.setDirection(direction)

    # --- calibration panel -------------------------------------------------

    def _build_calibration_panel(self) -> QFrame:
        panel = QWidget()
        sections = QVBoxLayout(panel)
        sections.setContentsMargins(0, 0, 0, 0)
        sections.setSpacing(10)

        def section(title):
            frame = self._panel()
            frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            self.panels.append(frame)
            box = QVBoxLayout(frame)
            box.setContentsMargins(16, 12, 16, 12)
            box.setSpacing(6)
            box.addWidget(self._heading(title))
            sections.addWidget(frame)
            return box

        box = section("Calibration")
        box.setSpacing(SUMMARY_ROW_SPACING)

        self.raw_summary = QLabel("")
        self.raw_summary.setWordWrap(True)
        self.raw_summary.setStyleSheet(self._status_style(COLORS["text"]))
        box.addWidget(self.raw_summary)

        box.addWidget(self._build_steps())

        self.progress_panel = self._build_progress_panel()
        self.progress_panel.setVisible(False)
        box.addWidget(self.progress_panel)

        self.btn_calibrate = QPushButton("RUN CALIBRATION")
        self.btn_calibrate.setFixedHeight(42)
        self.btn_calibrate.setStyleSheet(self._primary_button_style())
        self.btn_calibrate.clicked.connect(self.handle_calibrate)
        box.addWidget(self.btn_calibrate)

        self.calib_status = QLabel("")
        self.calib_status.setWordWrap(True)
        self.calib_status.setStyleSheet(self._status_style(COLORS["muted_text"]))
        self.calib_status.hide()
        box.addWidget(self.calib_status)

        box = section("Current equations")
        box.parentWidget().setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        summary_scroll = self.summary_scroll = QScrollArea()
        summary_scroll.setWidgetResizable(True)
        summary_scroll.setFrameShape(QFrame.Shape.NoFrame)
        summary_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        summary_scroll.setMinimumHeight(40)
        summary_scroll.setWidget(self._build_equations_summary())
        box.addWidget(summary_scroll, 1)

        box = section("Model source")
        box.addWidget(self._note(
            "Which equation the live readings are taken from. Both choices "
            "are produced by every calibration, so changing them costs "
            "nothing and needs no re-run."
        ))
        box.addWidget(self._build_model_source_rows())

        self.fit_source_note = QLabel("")
        self.fit_source_note.setWordWrap(True)
        self.fit_source_note.setStyleSheet(self._status_style(COLORS["muted_text"]))
        self.raw_summary.setStyleSheet(self._status_style(COLORS["text"]))
        self.progress_detail.setStyleSheet(self._status_style(COLORS["muted_text"]))
        self.progress_bar.setStyleSheet(self._progress_style())
        self._set_step_state(None)
        box.addWidget(self.fit_source_note)

        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(self._status_style(COLORS["warn_text"]))
        box.addWidget(self.warning_label)
        self.warning_label.hide()

        sections.setStretch(1, 1)
        return panel

    def _build_steps(self) -> QWidget:
        """
        The three stages, named, so the wait is legible while it happens.

        The stage that is running is marked; the ones behind it are ticked.
        Without this the button simply says RUNNING for two minutes and gives
        no sign of which part is slow or whether anything is wrong.
        """
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")

        box = QVBoxLayout(holder)
        box.setContentsMargins(2, 2, 2, 2)
        box.setSpacing(SUMMARY_ROW_SPACING)

        self.step_labels = {}
        for number in sorted(STAGE_NAMES):
            label = QLabel(f"{number}.  {STAGE_NAMES[number]}")
            label.setWordWrap(True)
            label.setStyleSheet(self._step_style(active=False, done=False))
            box.addWidget(label)
            self.step_labels[number] = label

        return holder

    def _build_progress_panel(self) -> QWidget:
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")

        box = QVBoxLayout(holder)
        box.setContentsMargins(2, 2, 2, 2)
        box.setSpacing(5)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(16)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setStyleSheet(self._progress_style())
        box.addWidget(self.progress_bar)

        self.progress_detail = QLabel("")
        self.progress_detail.setWordWrap(True)
        self.progress_detail.setStyleSheet(self._status_style(COLORS["muted_text"]))
        box.addWidget(self.progress_detail)

        return holder

    def refresh_raw_summary(self):
        """
        What is waiting in input_raw_data, before anything is run.

        The calibration reads whatever is in that folder, so how many files
        and how much data is the one thing worth knowing before pressing a
        button that takes minutes.
        """
        records = scan_raw_directory(app_paths.raw_data_dir())
        if not records:
            self.raw_summary.setText(
                "No raw data. Put the calibration CSVs in input_raw_data "
                "next to the application.")
            return

        total = sum(os.path.getsize(r["path"]) for r in records
                    if os.path.exists(r["path"]))
        sets = group_by_set(records)
        self.raw_summary.setText(
            f"{len(records)} raw files, {total / 1e9:.1f} GB, "
            f"{len(sets)} sets. Existing results are archived, not overwritten.")

    def _set_step_state(self, active_stage):
        """Mark where the pipeline is: earlier stages done, this one running."""
        for number, label in self.step_labels.items():
            done = active_stage is not None and number < active_stage
            running = number == active_stage
            mark = "OK" if done else ("->" if running else f"{number}. ")
            prefix = f"{mark}  " if done or running else mark
            label.setText(f"{prefix}{STAGE_NAMES[number]}")
            label.setStyleSheet(self._step_style(active=running, done=done))

    def _build_equations_summary(self) -> QWidget:
        """
        What the equations file holds, as figures rather than a sentence.

        The sets are what you pick from on the right, and the R2 range is the
        quickest way to notice that a re-calibration went wrong.
        """
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")

        grid = self.summary_grid = QGridLayout(holder)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(SUMMARY_ROW_SPACING)
        grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        return holder

    def _build_model_source_rows(self) -> QWidget:
        """
        Equation first, then which cycles it was fitted over.

        That order because the degree decides the shape of the curve and the
        cycle only which points it was fitted to; and because reading them the
        other way round invites the two to be confused.
        """
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")

        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)

        self.degree_label_widget = QLabel("Fit equation")
        self.degree_label_widget.setStyleSheet(self._stat_key_style())
        grid.addWidget(self.degree_label_widget, 0, 0)

        self.degree_combo = ReadableComboBox()
        self.degree_combo.setFixedHeight(28)
        self.degree_combo.setToolTip(
            "Degree of the polynomial fitted to the calibration points.\n"
            "Degree 2 is what every measurement so far has used.\n"
            "Degree 3 follows the same points more closely, at both ends.")
        self.degree_combo.setStyleSheet(self._combo_style())
        self.degree_combo.activated.connect(self._on_fit_degree)
        grid.addWidget(self.degree_combo, 0, 1)

        self.cycle_label_widget = QLabel("Cycles")
        self.cycle_label_widget.setStyleSheet(self._stat_key_style())
        grid.addWidget(self.cycle_label_widget, 1, 0)

        self.fit_source_combo = ReadableComboBox()
        self.fit_source_combo.setFixedHeight(28)
        self.fit_source_combo.setToolTip(
            "Which cycles the equation was fitted over. The combined fit uses "
            "every cycle's median points at once; the per-cycle fits are kept "
            "for repeatability analysis.")
        self.fit_source_combo.setStyleSheet(self._combo_style())
        self.fit_source_combo.activated.connect(self._on_fit_source)
        grid.addWidget(self.fit_source_combo, 1, 1)

        return holder

    def set_fit_degrees(self, degrees, current):
        """Offer the degrees this equations file actually contains."""
        self._loading = True
        self.degree_combo.clear()
        for degree in degrees:
            self.degree_combo.addItem(degree_label(degree), userData=int(degree))

        index = self.degree_combo.findData(int(current))
        self.degree_combo.setCurrentIndex(index if index >= 0 else 0)
        self._loading = False

    def _on_fit_degree(self):
        if self._loading:
            return

        degree = int(self.degree_combo.currentData())

        # Same trap as the fit source: this page holds its own copy of the
        # bindings file, and a stale value here would be written back over the
        # new one by the next save.
        self.store.fit_degree = degree

        self.catalog.set_degree(degree)
        self._fill_summary(self.catalog.sensor_ids(), self.catalog.sets())
        self._apply_store_to_widgets()
        self._report_problems()
        self._describe_fit_source()

        self.fit_degree_changed.emit(degree)

    def set_fit_sources(self, sources, current):
        """Offer the fits this equations file actually contains."""
        self._loading = True
        self.fit_source_combo.clear()
        for source in sources:
            self.fit_source_combo.addItem(fit_source_label(source), userData=source)

        index = self.fit_source_combo.findData(current)
        self.fit_source_combo.setCurrentIndex(index if index >= 0 else 0)
        self._loading = False
        self._describe_fit_source()

    def _on_fit_source(self):
        if self._loading:
            return

        source = self.fit_source_combo.currentData()
        self._describe_fit_source()

        # The page holds its own copy of the bindings file. Without this line
        # it keeps the fit source it was loaded with, and the next SAVE
        # SELECTION writes that stale value back over the new one - which then
        # comes round again as a reload and puts the engine back on the old
        # fit. That is why choosing a cycle appeared to change nothing.
        self.store.fit_source = source

        # The R2 beside each measurement belongs to a particular fit. Re-read
        # so the page reports the one the cards are now using; the bindings
        # themselves are untouched and are put straight back.
        self.catalog.set_fit_source(source)
        self._fill_summary(self.catalog.sensor_ids(), self.catalog.sets())
        self._apply_store_to_widgets()
        self._report_problems()

        self.fit_source_changed.emit(source)

    def _describe_fit_source(self):
        """Say how many points back the selected fit, from the file itself."""
        source = self.fit_source_combo.currentData()
        points = self._points_behind(source)
        if points:
            self.fit_source_note.setText(
                f"{points} median points per curve.")
        else:
            self.fit_source_note.setText("")

    def _points_behind(self, source) -> int:
        """Median points behind one curve of the selected fit, 0 if unknown."""
        return self.catalog.points_for(source)

    def handle_calibrate(self):
        if self.worker is not None and self.worker.isRunning():
            return

        self.btn_calibrate.setEnabled(False)
        self.btn_calibrate.setText("RUNNING...")
        self.calib_status.setStyleSheet(f"color: {COLORS['muted_text']}; font-size: 13px;")
        self.calib_status.show()
        self.calib_status.setText("Starting...")

        self.worker = CalibrationWorker(self)
        self.worker.progress.connect(self._on_calibration_progress)
        self.worker.finished_ok.connect(self._on_calibration_done)
        self.worker.failed.connect(self._on_calibration_failed)
        self.worker.start()

    def _on_calibration_progress(self, stage: int, total: int, message: str,
                                 fraction):
        """
        Move the bar across all three stages, not within one.

        A stage that reports no fraction counts as just started, so the bar
        never goes backwards when the pipeline moves on.
        """
        share = 0.0 if fraction is None else float(fraction)
        overall = ((stage - 1) + min(max(share, 0.0), 1.0)) / max(total, 1)

        self.progress_bar.setValue(int(overall * 1000))
        self.progress_detail.setText(message)
        self._set_step_state(stage)

    def _on_calibration_done(self, summary: dict):
        self._reset_calibrate_button()
        self.progress_bar.setValue(1000)
        self.progress_detail.setText("")
        self.progress_panel.setVisible(False)
        self._set_step_state(None)
        self.refresh_raw_summary()
        self.calib_status.setStyleSheet(f"color: {COLORS['ok_text']}; font-size: 13px;")
        self.calib_status.show()
        self.calib_status.setText(
            f"Done in {summary['elapsed_s']:.0f} s: {summary['sensors']} sensors, "
            f"{summary['equation_rows']} equation rows, "
            f"mean R2 {summary['r2_mean']:.4f} (lowest {summary['r2_min']:.4f})."
        )
        self.reload_catalog()
        self.calibration_finished.emit()

    def _on_calibration_failed(self, message: str):
        self._reset_calibrate_button()
        self.progress_panel.setVisible(False)
        self._set_step_state(None)
        self.calib_status.setStyleSheet(f"color: {COLORS['error_text']}; font-size: 13px;")
        self.calib_status.show()
        self.calib_status.setText(f"Calibration failed: {message}")

    def _reset_calibrate_button(self):
        self.btn_calibrate.setEnabled(True)
        self.btn_calibrate.setText("RUN CALIBRATION")
        self.worker = None

    # --- binding panel -----------------------------------------------------

    def _build_binding_panel(self) -> QFrame:
        panel = self._panel()
        box = QVBoxLayout(panel)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(6)

        self.panels.append(panel)
        box.addWidget(self._heading("Sensor selection"))
        box.addWidget(self._note(
            "The array reports a socket, not a sensor. Tell it which sensor is "
            "in each socket. Applying a set fills all twelve at once; change "
            "individual sockets afterwards if the rig is mixed."
        ))

        set_row = FlowLayout(h_spacing=6)
        set_row.addWidget(QLabel("Set:"))

        self.set_combo = ReadableComboBox()
        self.set_combo.setFixedWidth(160)
        self.set_combo.setStyleSheet(self._combo_style())
        set_row.addWidget(self.set_combo)

        self.btn_apply_set = QPushButton("APPLY SET")
        self.btn_apply_set.setFixedWidth(110)
        self.btn_apply_set.setStyleSheet(self._plain_button_style())
        self.btn_apply_set.clicked.connect(self.handle_apply_set)
        set_row.addWidget(self.btn_apply_set)

        self.btn_clear = QPushButton("CLEAR ALL")
        self.btn_clear.setFixedWidth(110)
        self.btn_clear.setStyleSheet(self._plain_button_style())
        self.btn_clear.clicked.connect(self.handle_clear)
        set_row.addWidget(self.btn_clear)

        set_row.addStretch()
        box.addLayout(set_row)

        scroll = self.binding_scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        self.binding_grid = QGridLayout(holder)
        self.binding_grid.setContentsMargins(0, 6, 0, 6)
        self.binding_grid.setHorizontalSpacing(10)
        self.binding_grid.setVerticalSpacing(4)
        self._build_binding_rows()
        scroll.setMinimumHeight(40)
        scroll.setWidget(holder)
        box.addWidget(scroll, stretch=1)

        save_row = QHBoxLayout()
        self.binding_status = QLabel("")
        # Wrapped, and free to be narrow: an unwrapped status line is as wide
        # as its longest message, and that width becomes a floor the whole
        # window cannot go under - one that moves as the text changes.
        self.binding_status.setWordWrap(True)
        self.binding_status.setMinimumWidth(1)
        self.binding_status.setStyleSheet(f"color: {COLORS['muted_text']}; font-size: 13px;")
        save_row.addWidget(self.binding_status, stretch=1)

        self.btn_save = QPushButton("SAVE SELECTION")
        self.btn_save.setFixedHeight(36)
        self.btn_save.setFixedWidth(170)
        self.btn_save.setStyleSheet(self._primary_button_style())
        self.btn_save.clicked.connect(self.handle_save)
        save_row.addWidget(self.btn_save)

        box.addLayout(save_row)
        return panel

    def _build_binding_rows(self):
        # The measurement used to be one column carrying a date and a single
        # worst-of R2. It is three now: the date is the thing being chosen,
        # and the two quality figures belong to different quantities, so one
        # number could not stand for both.
        headers = ["Socket", "Sensor", "Date"]
        headers += [f"{target_label(t)}\nR2" for t in TARGETS]
        for col, text in enumerate(headers):
            label = QLabel(text)
            # Fixed, and with a stretch row below: the combo boxes cannot grow
            # vertically, so without both of these every spare pixel in the
            # scroll area went to the only row that could - this one - and the
            # headings sat in a band taller than the twelve rows under them.
            # "Indentation depth R2" over a column of four-decimal numbers
            # would otherwise be the widest thing on the page, so it wraps.
            label.setWordWrap(False)
            label.setSizePolicy(QSizePolicy.Policy.Preferred,
                                QSizePolicy.Policy.Fixed)
            label.setStyleSheet(self._column_header_style())
            label.setMinimumSize(label.sizeHint())
            self.binding_grid.addWidget(label, 0, col)
            self.column_headers.append(label)

        # Single-line and two-line headings share a proper header band rather
        # than shrinking each label to a different height around its text.
        header_height = max(40, max(label.sizeHint().height() + 10
                                    for label in self.column_headers))
        column_alignment = (Qt.AlignmentFlag.AlignHCenter, Qt.AlignmentFlag.AlignLeft,
                            Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignHCenter,
                            Qt.AlignmentFlag.AlignHCenter)
        for column, label in enumerate(self.column_headers):
            label.setFixedHeight(header_height)
            label.setAlignment(column_alignment[column] | Qt.AlignmentFlag.AlignVCenter)

        for row, position in enumerate(POSITIONS, start=1):
            socket_label = QLabel(display_position(position))
            socket_label.setMinimumWidth(44)
            socket_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            socket_label.setStyleSheet("font-size: 14px; font-weight: bold;")
            self.binding_grid.addWidget(socket_label, row, 0)

            sensor_combo = ReadableComboBox()
            sensor_combo.setStyleSheet(self._combo_style())
            sensor_combo.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Fixed)
            sensor_combo.currentIndexChanged.connect(
                lambda _, p=position: self.handle_sensor_changed(p))
            self.binding_grid.addWidget(sensor_combo, row, 1)
            self.sensor_combos[position] = sensor_combo

            date_combo = ReadableComboBox()
            date_combo.setStyleSheet(self._combo_style())
            # A range rather than a fixed width: wide enough for a date at
            # rest, but able to give way when the window is narrow. The popup
            # is sized to its own content, so nothing is lost by the control
            # being small.
            date_combo.setSizeAdjustPolicy(ReadableComboBox.SizeAdjustPolicy.AdjustToContents)
            date_combo.setToolTip(
                "Which calibration run this socket is read with. Newest first; "
                "an earlier one can be chosen when a later run fitted worse.")
            date_combo.currentIndexChanged.connect(
                lambda _, p=position: self._on_date_changed(p))
            self.binding_grid.addWidget(date_combo, row, 2)
            self.date_combos[position] = date_combo

            # Read-only: these report the fit chosen above, they do not set it
            self.r2_labels[position] = {}
            for column, target in enumerate(TARGETS, start=3):
                value = QLabel("")
                value.setMinimumWidth(62)
                value.setAlignment(Qt.AlignmentFlag.AlignCenter)
                value.setToolTip(
                    f"Fit quality of this run's {target_label(target).lower()} "
                    f"curves, for the degree and cycles chosen on the left. "
                    f"The weaker of the loading and unloading curves is shown.")
                self.binding_grid.addWidget(value, row, column)
                self.r2_labels[position][target] = value

        # Everything the rows do not use goes here rather than into the headings
        self.binding_grid.setRowStretch(len(POSITIONS) + 1, 1)

    # --- state -------------------------------------------------------------

    def reload_catalog(self):
        """Re-read the equations file and rebuild every choice from it."""
        self.catalog = SensorCatalog(fit_source=self.catalog.fit_source,
                                     degree=self.catalog.degree)
        sensor_ids = self.catalog.sensor_ids()
        sets = self.catalog.sets()

        self._fill_summary(sensor_ids, sets)

        self._loading = True

        self.set_combo.clear()
        self.set_combo.addItems(sorted(sets))

        for position in POSITIONS:
            combo = self.sensor_combos[position]
            combo.clear()
            combo.addItem(UNBOUND)
            combo.addItems(sensor_ids)

        self._loading = False

        self._apply_store_to_widgets()
        self._report_problems()

    def _apply_store_to_widgets(self):
        """Push the saved bindings into the combo boxes."""
        self._loading = True
        for position in POSITIONS:
            sensor_id = self.store.sensor_for(position)
            combo = self.sensor_combos[position]

            index = combo.findText(sensor_id) if sensor_id else -1
            combo.setCurrentIndex(index if index >= 0 else 0)
            self._fill_dates(position, preferred=self.store.source_for(position))
        self._loading = False

        if self.store.set_name:
            index = self.set_combo.findText(self.store.set_name)
            if index >= 0:
                self.set_combo.setCurrentIndex(index)

        bound = len(self.store.bound_positions())
        self._set_status(f"{bound} of {len(POSITIONS)} sockets bound"
                         + (f", saved {self.store.saved_at[:16]}" if self.store.saved_at else ""),
                         COLORS["muted_text"])

    def _fill_dates(self, position: str, preferred: str = ""):
        """
        List the calibration runs of the sensor chosen for this socket.

        Newest first, so index 0 is the default, and any earlier run can be
        picked instead - the two R2 columns beside it say what that costs.
        """
        combo = self.date_combos[position]
        combo.clear()

        sensor_id = self._selected_sensor(position)
        if not sensor_id:
            combo.setEnabled(False)
            self._show_quality(position, None)
            return

        measurements = self.catalog.measurements(sensor_id)
        combo.setEnabled(len(measurements) > 0)

        # Two runs on one day are told apart by the time; showing the time
        # always would push a column of identical clock readings at the
        # operator for the one-run-per-sensor case that is normal today.
        dates = [m.date_label() for m in measurements]
        for m in measurements:
            label = m.time_label() if dates.count(m.date_label()) > 1 else m.date_label()
            combo.addItem(label, userData=m.source_file)

        if preferred:
            index = combo.findData(preferred)
            if index >= 0:
                combo.setCurrentIndex(index)

        self._refresh_quality(position)

    def _refresh_quality(self, position: str):
        """
        Put this row's fit quality beside the date it belongs to.

        Called whenever anything that decides which rows are read changes: the
        sensor, the date, the degree or the cycles. The figure has to describe
        the model the cards are actually reading, or it is worse than none.
        """
        sensor_id = self._selected_sensor(position)
        source_file = self.date_combos[position].currentData() or ""

        measurement = None
        if sensor_id:
            measurement = (self.catalog.find_measurement(sensor_id, source_file)
                           or self.catalog.default_measurement(sensor_id))

        self._show_quality(position, measurement)

    def _show_quality(self, position: str, measurement):
        for target, label in self.r2_labels[position].items():
            if measurement is None or not measurement.has(target):
                label.setText("")
                label.setStyleSheet(self._r2_style(None))
                continue

            score = measurement.worst_r2(target)
            label.setText(f"{score:.4f}")
            label.setStyleSheet(self._r2_style(score))

    def _r2_style(self, score) -> str:
        """
        Colour the weaker fits rather than leaving four decimals to be scanned.

        The thresholds are set where the residual stops being negligible for
        this rig: at R2 0.99 a curve is off by roughly a percent of full scale,
        which is about a gram on the light sensors.
        """
        if score is None:
            colour = COLORS["muted_text"]
        elif score >= 0.995:
            colour = COLORS["ok_text"]
        elif score >= 0.99:
            colour = COLORS["text"]
        else:
            colour = COLORS["error_text"]
        return f"color: {colour}; font-size: 13px; font-family: 'Consolas', monospace;"

    def _on_date_changed(self, position: str):
        if self._loading:
            return
        self._refresh_quality(position)
        self._mark_unsaved()

    def _selected_sensor(self, position: str) -> str:
        text = self.sensor_combos[position].currentText()
        return "" if text in ("", UNBOUND) else text

    def handle_sensor_changed(self, position: str):
        if self._loading:
            return
        self._fill_dates(position)
        self._mark_unsaved()

    def handle_apply_set(self):
        set_id = self.set_combo.currentText()
        if not set_id:
            return

        missing = self.store.apply_set(self.catalog, set_id)
        self._apply_store_to_widgets()

        if missing:
            self._set_status(
                f"Applied '{set_id}'. No sensor for: "
                f"{', '.join(display_position(p) for p in missing)}",
                COLORS["warn_text"])
        else:
            self._set_status(f"Applied '{set_id}' to all {len(POSITIONS)} sockets. "
                             f"Not saved yet.", COLORS["warn_text"])

    def handle_clear(self):
        self.store.clear()
        self._apply_store_to_widgets()
        self._set_status("Cleared. Not saved yet.", COLORS["warn_text"])

    def handle_save(self):
        self._collect_widgets_into_store()
        path = self.store.save()

        bound = len(self.store.bound_positions())
        self._set_status(f"Saved {bound} sockets to {path}", COLORS["ok_text"])
        self._report_problems()
        self.bindings_changed.emit()

    def _collect_widgets_into_store(self):
        # Phase modes are chosen on the other pages, not here; carry them over
        # so saving a socket change does not reset how it is read.
        previous_modes = {p: self.store.phase_mode_for(p)
                          for p in self.store.bound_positions()}

        self.store.clear()
        self.store.set_name = self.set_combo.currentText()

        for position in POSITIONS:
            sensor_id = self._selected_sensor(position)
            if not sensor_id:
                continue
            source_file = self.date_combos[position].currentData() or ""
            self.store.bind(position, sensor_id, source_file, previous_modes.get(position))

    def _report_problems(self):
        problems = self.store.validate(self.catalog)
        if problems:
            self.warning_label.show()
            self.warning_label.setText(
                "Bindings the current equations cannot satisfy:\n" +
                "\n".join(f"    {p}" for p in problems))
        else:
            self.warning_label.hide()
            self.warning_label.setText("")

    def _mark_unsaved(self):
        if not self._loading:
            self._set_status("Changed. Not saved yet.", COLORS["warn_text"])

    def _set_status(self, text: str, color: str):
        self.binding_status.setStyleSheet(f"color: {color}; font-size: 13px;")
        self.binding_status.setText(text)

    # --- styling -----------------------------------------------------------

    def _panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("setupPanel")
        panel.setStyleSheet(self._panel_style())
        return panel

    def _panel_style(self) -> str:
        return f"""
            QFrame#setupPanel {{
                background-color: {COLORS["panel_bg"]};
                border: 1px solid {COLORS["panel_border"]};
                border-radius: 6px;
            }}
        """

    def _fill_summary(self, sensor_ids, sets):
        """Rewrite the figures block for the equations file now loaded."""
        while self.summary_grid.count():
            item = self.summary_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.stat_labels = []

        if not sensor_ids:
            label = QLabel("No equations yet. Run the calibration.")
            label.setWordWrap(True)
            label.setStyleSheet(self._status_style(COLORS["muted_text"]))
            self.summary_grid.addWidget(label, 0, 0, 1, 2)
            self.stat_labels.append(label)
            return

        rows = [("Total sensors", f"{len(sensor_ids)}"),
                ("Sets", f"{len(sets)}")]
        rows += [(f"    {set_id}", f"{len(ids)} sensors")
                 for set_id, ids in sorted(sets.items())]

        # Name the fit and the quantity: the file holds several fits, and each
        # of them has an R2 per target.
        for target in self.catalog.targets:
            quality = self.catalog.r2_range(target)
            if quality:
                low, high = quality
                rows.append((self.catalog.quality_label(target),
                             f"{low:.4f} - {high:.4f}"))

        repeats = self.catalog.repeat_count()
        if repeats:
            rows.append(("Measured twice", f"{repeats} sensors"))

        for row, (name, value) in enumerate(rows):
            key = QLabel(name)
            # Keep each label on one line; only the value column takes the
            # width it needs. The panel body can scroll on a very small screen.
            key.setWordWrap(False)
            key.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            key.setMinimumWidth(1)
            key.setStyleSheet(self._stat_key_style())
            val = QLabel(value)
            val.setStyleSheet(self._stat_value_style())
            val.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            val.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
            self.summary_grid.addWidget(key, row, 0)
            self.summary_grid.addWidget(val, row, 1, Qt.AlignmentFlag.AlignLeft)
            self.stat_labels += [key, val]

    def _step_style(self, active: bool, done: bool) -> str:
        colour = (COLORS["text"] if active else
                  COLORS["ok_text"] if done else COLORS["muted_text"])
        weight = "bold" if active else "normal"
        return f"color: {colour}; font-size: 13px; font-weight: {weight};"

    def _progress_style(self) -> str:
        return f"""
            QProgressBar {{
                background-color: {COLORS["input_bg"]};
                border: 1px solid {COLORS["input_border"]};
                border-radius: 8px;
            }}

            QProgressBar::chunk {{
                background-color: {COLORS["connect_bg"]};
                border-radius: 7px;
            }}
        """

    def _column_header_style(self) -> str:
        return (f"color: {COLORS['muted_text']}; font-size: 12px; "
                f"font-weight: bold; letter-spacing: 0.5px;")

    def _stat_key_style(self) -> str:
        return f"color: {COLORS['muted_text']}; font-size: 13px;"

    def _stat_value_style(self) -> str:
        return f"color: {COLORS['text']}; font-size: 13px; font-weight: bold;"

    def _status_style(self, colour: str) -> str:
        return f"color: {colour}; font-size: 13px;"

    def _heading(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-size: 16px; font-weight: bold;")
        return label

    def _note(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {COLORS['muted_text']}; font-size: 13px;")
        self.notes.append(label)
        return label

    def _primary_button_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: {COLORS["connect_bg"]};
                color: white;
                font-weight: bold;
                border: none;
                border-radius: 4px;
                padding: 8px;
            }}

            QPushButton:hover {{
                background-color: {COLORS["connect_hover"]};
            }}

            QPushButton:pressed {{
                background-color: {COLORS["connect_pressed"]};
                padding-top: 10px;
                padding-left: 10px;
                padding-bottom: 6px;
                padding-right: 6px;
            }}

            QPushButton:disabled {{
                background-color: {COLORS["sim_disabled_bg"]};
                color: {COLORS["sim_disabled_text"]};
            }}
        """

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

    def _combo_style(self) -> str:
        return f"""
            QComboBox {{
                background-color: {COLORS["input_bg"]};
                color: {COLORS["text"]};
                border: 1px solid {COLORS["input_border"]};
                padding: 3px 5px;
                border-radius: 4px;
            }}

            QComboBox::drop-down {{
                border: none;
            }}

            QComboBox QAbstractItemView {{
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

    # --- theming -----------------------------------------------------------

    def apply_theme(self):
        """Re-apply every stylesheet on this page after a theme switch."""
        self._apply_page_style()

        for panel in self.panels:
            panel.setStyleSheet(self._panel_style())

        for note in self.notes:
            note.setStyleSheet(f"color: {COLORS['muted_text']}; font-size: 13px;")

        self.btn_calibrate.setStyleSheet(self._primary_button_style())
        self.btn_save.setStyleSheet(self._primary_button_style())
        for button in (self.btn_apply_set, self.btn_clear):
            button.setStyleSheet(self._plain_button_style())

        self.set_combo.setStyleSheet(self._combo_style())
        for position in POSITIONS:
            self._refresh_quality(position)

        for combo in list(self.sensor_combos.values()) + list(self.date_combos.values()):
            combo.setStyleSheet(self._combo_style())

        self.warning_label.setStyleSheet(self._status_style(COLORS["warn_text"]))
        self.fit_source_note.setStyleSheet(self._status_style(COLORS["muted_text"]))
        self.fit_source_combo.setStyleSheet(self._combo_style())

        for label in self.column_headers:
            label.setStyleSheet(self._column_header_style())
        for index, label in enumerate(self.stat_labels):
            label.setStyleSheet(self._stat_key_style() if index % 2 == 0
                                else self._stat_value_style())

        # Status labels carry a meaning-coloured style set at runtime, so the
        # current text keeps its colour role instead of reverting to neutral.
        self.calib_status.setStyleSheet(self._status_style(COLORS["muted_text"]))
        self.binding_status.setStyleSheet(self._status_style(COLORS["muted_text"]))

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
