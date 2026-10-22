"""
Live dashboard page: connect to the rig, watch one card per bound socket,
tare, and record.

This is the original single-window UI, now one page of the navigation shell in
gui/gui.py. Behaviour is unchanged; what moved is where it lives.
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QPushButton, QLabel, QFrame, QLineEdit,
                             QSizePolicy)
from PyQt6.QtCore import Qt, pyqtSignal

from gui import y_axis
from gui.combo_box import ReadableComboBox, PortComboBox
from gui.flow_layout import GroupLayout

# Always show the whole rig as four columns by three rows.
GRID_COLUMNS = 4
from gui.sensor_card import SensorCard
from gui.theme import APP_COLORS as COLORS
from inference.polynomial_engine import (PHASE_LABELS, PHASE_MODES, PHASE_DESCRIPTIONS,
                                         PHASE_CONTROL_LABEL, TARGETS,
                                         DEFAULT_TARGET, target_label)
from inference.teensy_serial import available_port_infos, pick_default_port


class DashboardPage(QWidget):
    connect_requested = pyqtSignal(str)
    disconnect_requested = pyqtSignal()
    tare_requested = pyqtSignal()
    record_requested = pyqtSignal(bool)
    volume_changed = pyqtSignal(str)
    simulation_requested = pyqtSignal(bool)  # True = start, False = stop
    phase_mode_changed = pyqtSignal(str, str)  # (socket, mode)
    target_changed = pyqtSignal(str)           # force or displacement

    def __init__(self, parent=None):
        super().__init__(parent)

        self._apply_page_style()

        self.cards = {}
        self.is_connected = False
        self.is_recording = False
        self.is_simulating = False

        self.init_ui()
        self.refresh_ports()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        top_bar = self.top_bar = QFrame()
        top_bar.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS["panel_bg"]};
                border: 1px solid {COLORS["panel_border"]};
                border-radius: 6px;
            }}
        """)
        # The bar wraps rather than holding the window open at the sum of its
        # controls, so it gains a line as the window narrows instead of the
        # window refusing to narrow. Its height follows from that, so it can no
        # longer be fixed.
        top_bar.setSizePolicy(QSizePolicy.Policy.Preferred,
                              QSizePolicy.Policy.Minimum)
        groups = self.toolbar_layout = GroupLayout(top_bar, margin=6, h_spacing=16)
        self.toolbar_groups = []
        def make_group():
            group = QWidget()
            group.setStyleSheet("background: transparent; border: none;")
            row = QHBoxLayout(group)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)
            groups.addWidget(group)
            self.toolbar_groups.append(group)
            return row
        top_layout = make_group()

        top_layout.addWidget(QLabel("Teensy:"))

        self.port_combo = PortComboBox()
        self.port_combo.setFixedWidth(76)
        self.port_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {COLORS["input_bg"]};
                color: {COLORS["text"]};
                border: 1px solid {COLORS["input_border"]};
                padding: 5px;
                border-radius: 4px;
            }}

            QComboBox::drop-down {{
                border: none;
            }}

            QComboBox QAbstractItemView {{
                font-size: 13px;
                background-color: {COLORS["input_bg"]};
                color: {COLORS["text"]};
                selection-background-color: {COLORS["connect_bg"]};
                selection-color: white;
                border: 1px solid {COLORS["input_border"]};
            }}
        """)
        top_layout.addWidget(self.port_combo)

        self.btn_refresh = QPushButton("REFRESH")
        self.btn_refresh.setFixedWidth(70)
        self.btn_refresh.setStyleSheet(self.refresh_button_style())
        self.btn_refresh.clicked.connect(self.refresh_ports)
        top_layout.addWidget(self.btn_refresh)


        top_layout.addWidget(QLabel("Vol:"))

        self.volume_input = QLineEdit()
        self.volume_input.setPlaceholderText("Value")
        self.volume_input.setFixedWidth(58)
        self.volume_input.setText("0")
        self.volume_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLORS["input_bg"]};
                color: {COLORS["text"]};
                border: 1px solid {COLORS["input_border"]};
                padding: 5px;
                border-radius: 4px;
                font-weight: bold;
            }}

            QLineEdit::placeholder {{
                color: {COLORS["muted_text"]};
            }}
        """)
        top_layout.addWidget(self.volume_input)

        self.btn_set_volume = QPushButton("SET")
        self.btn_set_volume.setFixedWidth(32)
        self.btn_set_volume.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS["set_bg"]};
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 5px;
                border: none;
            }}

            QPushButton:hover {{
                background-color: {COLORS["set_hover"]};
            }}

            QPushButton:pressed {{
                background-color: {COLORS["set_pressed"]};
                padding-top: 7px;
                padding-left: 7px;
                padding-bottom: 3px;
                padding-right: 3px;
            }}
        """)
        self.btn_set_volume.clicked.connect(lambda: self.volume_changed.emit(self.volume_input.text()))
        top_layout.addWidget(self.btn_set_volume)

        self._add_all_controls(make_group())
        top_layout = make_group()

        # --- SIMULATION BUTTON ---
        self.btn_simulation = QPushButton("SIMULATION")
        # Compact widths leave room for every control on a laptop-sized row.
        self.btn_simulation.setFixedWidth(96)
        self.btn_simulation.setStyleSheet(self.sim_button_style(simulating=False))
        self.btn_simulation.clicked.connect(self.handle_simulation)
        top_layout.addWidget(self.btn_simulation)


        self.btn_tare = QPushButton("TARE ALL")
        self.btn_tare.setFixedWidth(82)
        self.btn_tare.setStyleSheet(self.tare_button_style())
        self.btn_tare.clicked.connect(lambda: self.tare_requested.emit())
        top_layout.addWidget(self.btn_tare)


        self.btn_record = QPushButton("START RECORD")
        # A minimum rather than a fixed width: "START RECORD" is the longest
        # label in the bar and did not fit 106px at any zoom, so it was drawn
        # elided. The minimum keeps the compact layout at rest and the button
        # takes the few extra pixels its own text needs.
        self.btn_record.setMinimumWidth(106)
        self.btn_record.setEnabled(False)
        self.btn_record.setStyleSheet(self.record_button_style(recording=False))
        self.btn_record.clicked.connect(self.handle_record)
        top_layout.addWidget(self.btn_record)


        self.btn_connect = QPushButton("CONNECT")
        self.btn_connect.setFixedWidth(96)
        self.btn_connect.setStyleSheet(self.connect_button_style(connected=False))
        self.btn_connect.clicked.connect(self.handle_connect)
        top_layout.addWidget(self.btn_connect)

        for group in self.toolbar_groups:
            for control in group.findChildren(QWidget):
                if isinstance(control, (QPushButton, QLabel, QLineEdit, ReadableComboBox)):
                    control.setFixedHeight(32)
                if isinstance(control, QLabel):
                    control.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(top_bar)

        # All twelve channels must remain visible together; the charts share
        # the available height instead of moving channels into a scroll area.
        holder = self.grid_holder = QWidget()
        holder.setStyleSheet(f"background-color: {COLORS['app_bg']};")
        self.grid_layout = QGridLayout(holder)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(8)
        for column in range(GRID_COLUMNS):
            self.grid_layout.setColumnStretch(column, 1)
        for row in range(3):
            self.grid_layout.setRowStretch(row, 1)
        main_layout.addWidget(holder, stretch=1)

        # How many cards are on a row right now; see _reflow_cards
        self._columns = GRID_COLUMNS

    def _add_all_controls(self, row):
        """
        Set the hysteresis curve and Y range for every card at once.

        Twelve cards means twelve pickers; when the whole rig should read the
        same way, setting them one by one is busywork. Changing a single card
        afterwards still works, so this is a shortcut, not a lock. Both sit in
        the top bar next to the other rig-wide controls.
        """
        # Keep the heading with its controls when the main toolbar wraps.
        self.all_label = QLabel("All sensors:")
        row.addWidget(self.all_label)

        # Twelve cards with their own tabs is a lot of switching when the whole
        # rig is being read the same way; the tabs stay for the exceptions.
        self.all_target_combo = ReadableComboBox()
        self.all_target_combo.setFixedWidth(132)
        self.all_target_combo.setToolTip("Which quantity every card shows")
        for target in TARGETS:
            self.all_target_combo.addItem(target_label(target), userData=target)
        self.all_target_combo.setCurrentIndex(
            self.all_target_combo.findData(DEFAULT_TARGET))
        self.all_target_combo.setStyleSheet(self._combo_style())
        self.all_target_combo.activated.connect(self._on_all_target)
        row.addWidget(self.all_target_combo)

        self.all_phase_combo = ReadableComboBox()
        self.all_phase_combo.setFixedWidth(122)
        self.all_phase_combo.setToolTip("\n".join(
            [f"{PHASE_CONTROL_LABEL} for every sensor:"] +
            [f"  {PHASE_LABELS[m]} - {PHASE_DESCRIPTIONS[m]}" for m in PHASE_MODES]))
        self.all_phase_combo.setPlaceholderText(PHASE_CONTROL_LABEL)
        for mode in PHASE_MODES:
            self.all_phase_combo.addItem(PHASE_LABELS[mode], userData=mode)
        self.all_phase_combo.setStyleSheet(self._combo_style())
        self.all_phase_combo.setCurrentIndex(-1)
        self.all_phase_combo.activated.connect(self._on_all_phase)
        row.addWidget(self.all_phase_combo)

        self.all_y_combo = ReadableComboBox()
        self.all_y_combo.setFixedWidth(128)
        self.all_y_combo.setSizeAdjustPolicy(ReadableComboBox.SizeAdjustPolicy.AdjustToContents)
        self.all_y_combo.setToolTip("Y-axis range for every chart")
        self.all_y_combo.setStyleSheet(self._combo_style())
        self.all_y_combo.currentIndexChanged.connect(self._on_all_y)
        self._fill_all_y_options()
        row.addWidget(self.all_y_combo)

    def _fill_all_y_options(self):
        """
        Refill the apply-to-all list in the shown target's unit.

        Sending "500 gf" to a chart drawn in millimetres would match nothing
        and quietly do nothing, which reads as a broken control.
        """
        target = self.all_target_combo.currentData() or DEFAULT_TARGET

        self._loading_all_y = True
        self.all_y_combo.clear()
        self.all_y_combo.addItem("Y range...", userData=None)
        for label, key in y_axis.options_for(None, target):
            self.all_y_combo.addItem(label, userData=key)
        self._loading_all_y = False

    def _on_all_target(self):
        target = self.all_target_combo.currentData()
        if target is None:
            return
        for card in self.cards.values():
            card.set_target(target)
        self._fill_all_y_options()
        self.target_changed.emit(target)

    def show_target(self, target):
        """Reflect a choice made elsewhere, without emitting again."""
        index = self.all_target_combo.findData(target)
        if index >= 0:
            self.all_target_combo.setCurrentIndex(index)
        for card in self.cards.values():
            card.set_target(target)
        self._fill_all_y_options()

    def _on_all_phase(self):
        mode = self.all_phase_combo.currentData()
        if mode is None:
            return
        for key, card in self.cards.items():
            card.set_phase_mode(mode)
            # The card was set silently, so report each change ourselves
            if card.phase_mode() == mode:
                self.phase_mode_changed.emit(key, mode)
        # This is an apply-to-all action; individual sensors can differ later.
        self.all_phase_combo.setCurrentIndex(-1)

    def _on_all_y(self):
        if getattr(self, "_loading_all_y", False):
            return
        key = self.all_y_combo.currentData()
        if key is None:
            return
        for card in self.cards.values():
            card.set_y_range(key)

    def refresh_button_style(self):
        return f"""
            QPushButton {{
                background-color: {COLORS["refresh_bg"]};
                color: {COLORS["refresh_text"]};
                font-weight: bold;
                border: 1px solid {COLORS["refresh_border"]};
                border-radius: 4px;
                padding: 5px;
            }}

            QPushButton:hover {{
                background-color: {COLORS["refresh_hover"]};
                border: 1px solid {COLORS["connect_bg"]};
            }}

            QPushButton:pressed {{
                background-color: {COLORS["refresh_pressed"]};
                padding-top: 7px;
                padding-left: 7px;
                padding-bottom: 3px;
                padding-right: 3px;
            }}

            QPushButton:disabled {{
                color: {COLORS["record_disabled_text"]};
                background-color: {COLORS["record_disabled_bg"]};
                border: 1px solid {COLORS["panel_border"]};
            }}
        """

    def tare_button_style(self):
        return f"""
            QPushButton {{
                background-color: {COLORS["tare_bg"]};
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
                color: {COLORS["tare_text"]};
                border: 1px solid {COLORS["tare_border"]};
            }}

            QPushButton:hover {{
                background-color: {COLORS["tare_hover"]};
                border: 1px solid {COLORS["tare_text"]};
            }}

            QPushButton:pressed {{
                background-color: {COLORS["tare_pressed"]};
                padding-top: 10px;
                padding-left: 10px;
                padding-bottom: 6px;
                padding-right: 6px;
            }}
        """

    def sim_button_style(self, simulating: bool):
        if simulating:
            bg = COLORS["sim_active_bg"]
            hover = COLORS["sim_active_hover"]
            pressed = COLORS["sim_active_pressed"]
        else:
            bg = COLORS["sim_bg"]
            hover = COLORS["sim_hover"]
            pressed = COLORS["sim_pressed"]

        return f"""
            QPushButton {{
                background-color: {bg};
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
                color: white;
                border: none;
            }}

            QPushButton:hover {{
                background-color: {hover};
            }}

            QPushButton:pressed {{
                background-color: {pressed};
                padding-top: 10px;
                padding-left: 10px;
                padding-bottom: 6px;
                padding-right: 6px;
            }}

            QPushButton:disabled {{
                color: {COLORS["sim_disabled_text"]};
                background-color: {COLORS["sim_disabled_bg"]};
                border: 1px solid {COLORS["panel_border"]};
            }}
        """

    def record_button_style(self, recording: bool):
        if recording:
            return """
                QPushButton {
                    background-color: #ff4444;
                    font-weight: bold;
                    padding: 8px;
                    border-radius: 4px;
                    color: white;
                    border: none;
                }

                QPushButton:hover {
                    background-color: #ff5555;
                }

                QPushButton:pressed {
                    background-color: #cc2222;
                    padding-top: 10px;
                    padding-left: 10px;
                    padding-bottom: 6px;
                    padding-right: 6px;
                }
            """

        return f"""
            QPushButton {{
                background-color: {COLORS["record_bg"]};
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
                color: {COLORS["record_text"]};
                border: 1px solid {COLORS["record_border"]};
            }}

            QPushButton:hover {{
                background-color: {COLORS["record_hover"]};
            }}

            QPushButton:pressed {{
                background-color: {COLORS["record_pressed"]};
                padding-top: 10px;
                padding-left: 10px;
                padding-bottom: 6px;
                padding-right: 6px;
            }}

            QPushButton:disabled {{
                color: {COLORS["record_disabled_text"]};
                background-color: {COLORS["record_disabled_bg"]};
                border: 1px solid {COLORS["panel_border"]};
            }}
        """

    def connect_button_style(self, connected: bool):
        bg = COLORS["disconnect_bg"] if connected else COLORS["connect_bg"]
        hover = COLORS["disconnect_bg"] if connected else COLORS["connect_hover"]
        pressed = COLORS["disconnect_pressed"] if connected else COLORS["connect_pressed"]

        return f"""
            QPushButton {{
                background-color: {bg};
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
                color: white;
                border: none;
            }}

            QPushButton:hover {{
                background-color: {hover};
            }}

            QPushButton:pressed {{
                background-color: {pressed};
                padding-top: 10px;
                padding-left: 10px;
                padding-bottom: 6px;
                padding-right: 6px;
            }}

            QPushButton:disabled {{
                color: {COLORS["record_disabled_text"]};
                background-color: {COLORS["record_disabled_bg"]};
                border: none;
            }}
        """

    def refresh_ports(self):
        self.port_combo.clear()
        ports = available_port_infos()

        for p in ports:
            self.port_combo.addItem(f"{p['device']} ({p['description']})", p['device'])

        default_port = pick_default_port()
        if default_port:
            index = self.port_combo.findData(default_port)
            if index >= 0:
                self.port_combo.setCurrentIndex(index)

    def handle_connect(self):
        if not self.is_connected:
            port = self.port_combo.currentData()
            if port:
                self.connect_requested.emit(port)
        else:
            self.disconnect_requested.emit()

    def handle_record(self):
        self.is_recording = not self.is_recording
        self.record_requested.emit(self.is_recording)

        if self.is_recording:
            self.btn_record.setText("STOP RECORD")
            self.btn_record.setStyleSheet(self.record_button_style(recording=True))
        else:
            self.btn_record.setText("START RECORD")
            self.btn_record.setStyleSheet(self.record_button_style(recording=False))

    def handle_simulation(self):
        """Toggles simulation mode on/off."""
        self.simulation_requested.emit(not self.is_simulating)

    def update_simulation_state(self, active: bool):
        """Updates UI according to simulation state (called by main.py)."""
        self.is_simulating = active
        self._update_card_expectations()

        # Recording is allowed during simulation as well
        self.btn_record.setEnabled(active or self.is_connected)

        if not active and self.is_recording:
            self.handle_record()

        if active:
            self.btn_simulation.setText("STOP SIM")
            self.btn_simulation.setStyleSheet(self.sim_button_style(simulating=True))
            # Block real hardware controls while simulating
            self.btn_connect.setEnabled(False)
            self.port_combo.setEnabled(False)
            self.btn_refresh.setEnabled(False)
        else:
            self.btn_simulation.setText("SIMULATION")
            self.btn_simulation.setStyleSheet(self.sim_button_style(simulating=False))
            self.btn_connect.setEnabled(True)
            self.port_combo.setEnabled(True)
            self.btn_refresh.setEnabled(True)

    def update_connection_state(self, connected: bool):
        self.is_connected = connected
        self._update_card_expectations()
        self.btn_record.setEnabled(connected or self.is_simulating)

        if not connected and self.is_recording:
            self.handle_record()

        if connected:
            self.btn_connect.setText("DISCONNECT")
            self.btn_connect.setStyleSheet(self.connect_button_style(connected=True))
            self.port_combo.setEnabled(False)
            self.btn_refresh.setEnabled(False)
            # Block simulation while real hardware is streaming
            self.btn_simulation.setEnabled(False)
        else:
            self.btn_connect.setText("CONNECT")
            self.btn_connect.setStyleSheet(self.connect_button_style(connected=False))
            self.port_combo.setEnabled(True)
            self.btn_refresh.setEnabled(True)
            self.btn_simulation.setEnabled(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def reflow(self):
        """Keep the four-column rig layout after resizing or rebuilding."""
        self._reflow_cards()

    def _reflow_cards(self, force=False):
        """Every rebuild uses the same socket order and the same four columns."""
        if not self.cards:
            return

        columns = GRID_COLUMNS
        if columns == self._columns and not force:
            return

        self._columns = columns
        for index, card in enumerate(self.cards.values()):
            self.grid_layout.addWidget(card, index // columns, index % columns)

        for column in range(GRID_COLUMNS):
            self.grid_layout.setColumnStretch(column, 1)

    def add_sensor_card(self, key, display_name, row, col, subtitle=""):
        """
        Add one card.

        `key` is what incoming samples are keyed by (the socket), `display_name`
        is what the operator reads on the card (the socket again), and
        `subtitle` names the sensor bound to it. Showing both matters: the
        socket alone does not say which physical sensor is being watched.
        """
        self._hide_placeholder()

        card = SensorCard(key, display_name, parent=self.grid_holder, subtitle=subtitle)
        card.phase_mode_changed.connect(self.phase_mode_changed)
        card.target_changed.connect(lambda _, t: self.target_changed.emit(t))
        card.set_target(self.all_target_combo.currentData())
        self.cards[key] = card
        # A card built while something is already acquiring must start judging
        # straight away, not wait for the next connect.
        card.set_expecting_data(bool(self.is_connected or self.is_simulating))
        # Each rebuild starts with an empty grid. Re-place every new card even
        # when the number of columns has not changed since the previous fit.
        self._reflow_cards(force=True)

    def _update_card_expectations(self):
        """Cards only flag a silent socket while something is acquiring."""
        expecting = bool(self.is_connected or self.is_simulating)
        for card in self.cards.values():
            card.set_expecting_data(expecting)

    def clear_sensor_cards(self):
        """Drop every card, for when the socket mapping changes."""
        for card in self.cards.values():
            self.grid_layout.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self.cards = {}
        self._columns = 0
        for column in range(GRID_COLUMNS):
            self.grid_layout.setColumnStretch(column, 1)

    def show_placeholder(self, text):
        """Explain an empty grid instead of leaving a blank page."""
        self._hide_placeholder()
        self.placeholder = QLabel(text)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setStyleSheet(
            f"color: {COLORS['muted_text']}; font-size: 15px;")
        self.grid_layout.addWidget(self.placeholder, 0, 0, 1, 4)

    def _hide_placeholder(self):
        existing = getattr(self, "placeholder", None)
        if existing is not None:
            self.grid_layout.removeWidget(existing)
            existing.setParent(None)
            existing.deleteLater()
            self.placeholder = None

    # --- theming -----------------------------------------------------------

    def apply_theme(self):
        """Re-apply every stylesheet on this page after a theme switch."""
        self._apply_page_style()
        self.top_bar.setStyleSheet(self._top_bar_style())
        self.all_phase_combo.setStyleSheet(self._combo_style())
        self.all_y_combo.setStyleSheet(self._combo_style())
        self.all_target_combo.setStyleSheet(self._combo_style())
        self.grid_holder.setStyleSheet(f"background-color: {COLORS['app_bg']};")
        self.port_combo.setStyleSheet(self._combo_style())
        self.volume_input.setStyleSheet(self._input_style())
        self.btn_set_volume.setStyleSheet(self._set_button_style())
        self.btn_refresh.setStyleSheet(self.refresh_button_style())
        self.btn_tare.setStyleSheet(self.tare_button_style())
        self.btn_simulation.setStyleSheet(self.sim_button_style(self.is_simulating))
        self.btn_record.setStyleSheet(self.record_button_style(self.is_recording))
        self.btn_connect.setStyleSheet(self.connect_button_style(self.is_connected))

        placeholder = getattr(self, "placeholder", None)
        if placeholder is not None:
            placeholder.setStyleSheet(
                f"color: {COLORS['muted_text']}; font-size: 15px;")

        for card in self.cards.values():
            card.apply_theme()

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

    def _top_bar_style(self):
        return f"""
            QFrame {{
                background-color: {COLORS["panel_bg"]};
                border: 1px solid {COLORS["panel_border"]};
                border-radius: 6px;
            }}
        """

    def _combo_style(self):
        return f"""
            QComboBox {{
                background-color: {COLORS["input_bg"]};
                color: {COLORS["text"]};
                border: 1px solid {COLORS["input_border"]};
                padding: 5px;
                border-radius: 4px;
            }}

            QComboBox::drop-down {{
                border: none;
            }}

            QComboBox QAbstractItemView {{
                font-size: 13px;
                background-color: {COLORS["input_bg"]};
                color: {COLORS["text"]};
                selection-background-color: {COLORS["connect_bg"]};
                selection-color: white;
                border: 1px solid {COLORS["input_border"]};
            }}
        """

    def _input_style(self):
        return f"""
            QLineEdit {{
                background-color: {COLORS["input_bg"]};
                color: {COLORS["text"]};
                border: 1px solid {COLORS["input_border"]};
                padding: 5px;
                border-radius: 4px;
                font-weight: bold;
            }}

            QLineEdit::placeholder {{
                color: {COLORS["muted_text"]};
            }}
        """

    def _set_button_style(self):
        return f"""
            QPushButton {{
                background-color: {COLORS["set_bg"]};
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 5px;
                border: none;
            }}

            QPushButton:hover {{
                background-color: {COLORS["set_hover"]};
            }}

            QPushButton:pressed {{
                background-color: {COLORS["set_pressed"]};
                padding-top: 7px;
                padding-left: 7px;
                padding-bottom: 3px;
                padding-right: 3px;
            }}
        """
