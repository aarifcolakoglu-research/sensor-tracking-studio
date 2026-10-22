"""
Application shell: a menu bar and the pages it switches between.

    Setup      calibration and the socket-to-sensor mapping
    Dashboard  one card per bound socket, tare and record
    Overlay    every sensor on one chart, toggled per sensor

The pages are reached from the menu bar itself rather than from a sidebar. A
sidebar costs horizontal space on every screen, and on a small one that is
space the twelve cards need; a row of menu entries costs nothing that was not
already spent.

On the right of the same bar, from the outer edge inwards: full screen, the
interface scale, and the theme. Each opens directly rather than sitting behind
a Settings entry and a View submenu - three clicks to change a colour, for a
menu bar that holds four things in total.

The window owns no controls of its own beyond those. It forwards the pages'
signals so the controller in main.py talks to one object, and routes each
incoming sample to both the dashboard card and the overlay trace.
"""

from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QPushButton,
                             QStackedWidget, QMenu, QApplication)
from PyQt6.QtGui import QActionGroup, QAction, QResizeEvent
from PyQt6.QtCore import Qt, pyqtSignal

from gui import icons, theme
from gui.live_scale import LiveScale
from gui.pages.dashboard_page import DashboardPage
from gui.pages.overlay_page import OverlayPage
from gui.pages.setup_page import SetupPage
from gui.theme import APP_COLORS as COLORS
from inference.polynomial_engine import TARGET_FORCE

PAGE_LABELS = ("Setup", "Dashboard", "Overlay")
PAGE_HINTS = (
    "Calibration and sensor selection",
    "Live cards, tare and record",
    "All sensors on one chart",
)


class MainWindow(QMainWindow):
    # Forwarded from the dashboard page, so main.py connects to one object
    connect_requested = pyqtSignal(str)
    disconnect_requested = pyqtSignal()
    tare_requested = pyqtSignal()
    record_requested = pyqtSignal(bool)
    volume_changed = pyqtSignal(str)
    simulation_requested = pyqtSignal(bool)  # True = start, False = stop

    # Raised by the setup page when the mapping or the equations change, so the
    # controller can rebuild the cards and reload the models.
    bindings_changed = pyqtSignal()

    # Raised when a socket's hysteresis curve is chosen, from a card or from
    # either of the apply-to-all controls.
    phase_mode_changed = pyqtSignal(str, str)  # (socket, mode)
    target_changed = pyqtSignal(str)           # force or displacement

    # Raised when a different fit is chosen on the setup page
    fit_source_changed = pyqtSignal(object)
    fit_degree_changed = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sensor Tracking Studio")
        self._open_at_a_size_that_fits()

        self.page_actions: list[QAction] = []
        self.theme_actions: dict[str, QAction] = {}
        self.scale_actions: dict[int, QAction] = {}

        self._apply_window_style()
        self.init_ui()
        self._build_menu()
        self._forward_signals()
        self.live_scale = LiveScale(self)

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.setup_page = SetupPage()
        self.dashboard_page = DashboardPage()
        self.overlay_page = OverlayPage()

        self.stack = QStackedWidget()
        self.stack.addWidget(self.setup_page)
        self.stack.addWidget(self.dashboard_page)
        self.stack.addWidget(self.overlay_page)

        layout.addWidget(self.stack, stretch=1)

        self.statusBar().setStyleSheet(self._status_style())

    # --- menu bar ----------------------------------------------------------

    def _build_menu(self):
        menu = self.menuBar()
        menu.setStyleSheet(self._menu_style())

        group = QActionGroup(self)
        group.setExclusive(True)

        for index, (label, hint) in enumerate(zip(PAGE_LABELS, PAGE_HINTS)):
            action = QAction(label, self, checkable=True)
            action.setToolTip(hint)
            action.triggered.connect(lambda _, i=index: self.show_page(i))
            group.addAction(action)
            menu.addAction(action)
            self.page_actions.append(action)

        menu.setCornerWidget(self._build_corner(), Qt.Corner.TopRightCorner)

        # Only now do the actions exist to be checked
        self.show_page(1)  # open on the dashboard, the page used most

    def _build_corner(self) -> QWidget:
        """Theme, scale and full screen, each one click from the bar."""
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")

        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 6, 0)
        row.setSpacing(2)

        self.btn_theme = QPushButton()
        self.btn_theme.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_theme.setToolTip("Window and card colours")
        self.theme_menu = QMenu(self)
        self._add_theme_actions(self.theme_menu)
        self.btn_theme.setMenu(self.theme_menu)
        row.addWidget(self.btn_theme)

        self.btn_scale = QPushButton()
        self.btn_scale.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scale.setToolTip("Size of the interface")
        self.scale_menu = QMenu(self)
        self._add_scale_actions(self.scale_menu)
        self.btn_scale.setMenu(self.scale_menu)
        row.addWidget(self.btn_scale)

        self.btn_fullscreen = QPushButton()
        self.btn_fullscreen.setCheckable(True)
        self.btn_fullscreen.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fullscreen.setToolTip("Full screen (F11)")
        self.btn_fullscreen.setShortcut("F11")
        self.btn_fullscreen.setFixedWidth(38)
        self.btn_fullscreen.clicked.connect(self.toggle_fullscreen)
        row.addWidget(self.btn_fullscreen)

        self._refresh_corner()
        return holder

    def _refresh_corner(self):
        """Re-label and re-colour the corner controls for the current settings."""
        labels = {"light": "Light", "dark": "Dark", "mix": "Mix"}
        self.btn_theme.setText(f"Theme: {labels.get(theme.current_mode(), '')}")
        self.btn_scale.setText(f"Scale: {theme.current_scale()}%")

        for button in (self.btn_theme, self.btn_scale):
            button.setStyleSheet(self._corner_button_style())

        colour = COLORS["nav_active_text"] if self.isFullScreen() else COLORS["nav_text"]

        corner = self.menuBar().cornerWidget()
        if corner is not None:
            # QMenuBar gives its corner widget the width it asked for when the
            # bar was last laid out, and nothing lays the bar out again when
            # only the zoom changes - the buttons then keep their old width and
            # draw "Theme: Mix" elided. Pinning the width the buttons now need,
            # and handing the bar a resize, makes it place them again.
            buttons = (self.btn_theme, self.btn_scale, self.btn_fullscreen)
            for button in buttons:
                # Re-polish first: a button measured before Qt has applied its
                # new stylesheet still reports the old font's width, which is
                # how a zoom step downwards ended up a few pixels short.
                button.style().unpolish(button)
                button.style().polish(button)
                button.updateGeometry()

            row = corner.layout()
            margins = row.contentsMargins()
            # A few pixels of slack: the stylesheet's font size and the
            # scaled widths round independently, and at 70% they landed a
            # pixel apart.
            corner.setFixedWidth(
                sum(button.sizeHint().width() for button in buttons)
                + row.spacing() * (len(buttons) - 1)
                + margins.left() + margins.right() + 6)
            bar = self.menuBar()
            bar.updateGeometry()
            QApplication.sendEvent(bar, QResizeEvent(bar.size(), bar.size()))

        self.btn_fullscreen.setIcon(
            icons.exit_fullscreen_icon(colour) if self.isFullScreen()
            else icons.fullscreen_icon(colour))
        self.btn_fullscreen.setStyleSheet(self._corner_button_style())

    def _add_theme_actions(self, menu: QMenu):
        menu.setStyleSheet(self._menu_style())

        labels = {
            "light": "Light",
            "dark": "Dark",
            "mix": "Mix  (dark frame, light cards)",
        }

        group = QActionGroup(self)
        group.setExclusive(True)

        for mode in theme.THEME_MODES:
            action = QAction(labels[mode], self, checkable=True)
            action.setChecked(mode == theme.current_mode())
            action.triggered.connect(lambda _, m=mode: self.set_theme(m))
            group.addAction(action)
            menu.addAction(action)
            self.theme_actions[mode] = action

    def _add_scale_actions(self, menu: QMenu):
        menu.setStyleSheet(self._menu_style())

        group = QActionGroup(self)
        group.setExclusive(True)

        for percent in theme.SCALE_STEPS:
            label = f"{percent}%" + ("   (default)" if percent == 100 else "")
            action = QAction(label, self, checkable=True)
            action.setChecked(percent == theme.current_scale())
            action.triggered.connect(lambda _, p=percent: self.set_scale(p))
            group.addAction(action)
            menu.addAction(action)
            self.scale_actions[percent] = action

    # --- actions -----------------------------------------------------------

    def show_page(self, index: int):
        self.stack.setCurrentIndex(index)
        for i, action in enumerate(self.page_actions):
            if action.isChecked() != (i == index):
                action.setChecked(i == index)

    def toggle_fullscreen(self, enabled=None):
        full = (not self.isFullScreen()) if enabled is None else bool(enabled)
        if full:
            self.showFullScreen()
        else:
            self.showNormal()
        self.btn_fullscreen.setChecked(full)
        self._refresh_corner()

    def set_theme(self, mode: str):
        """Switch palettes and repaint every page, then remember the choice."""
        applied = theme.set_theme(mode)
        theme.save_settings()

        self._apply_window_style()
        self.menuBar().setStyleSheet(self._menu_style())
        self.theme_menu.setStyleSheet(self._menu_style())
        self.scale_menu.setStyleSheet(self._menu_style())
        self.statusBar().setStyleSheet(self._status_style())
        self._refresh_corner()

        for page in (self.setup_page, self.dashboard_page, self.overlay_page):
            page.apply_theme()

        action = self.theme_actions.get(applied)
        if action is not None and not action.isChecked():
            action.setChecked(True)

    def set_scale(self, percent: int):
        """Apply zoom in place, preserving hardware, recordings and selections."""
        applied = theme.set_scale(percent)
        theme.save_settings()
        self.scale_actions[applied].setChecked(True)
        # Restyle first: the corner buttons are re-measured against the new
        # font size, and measuring them before it is applied leaves them at
        # the old width with their labels clipped.
        self.live_scale.apply()
        self._refresh_corner()

    # --- wiring ------------------------------------------------------------

    def _forward_signals(self):
        page = self.dashboard_page
        page.connect_requested.connect(self.connect_requested)
        page.disconnect_requested.connect(self.disconnect_requested)
        page.tare_requested.connect(self.tare_requested)
        page.record_requested.connect(self.record_requested)
        page.volume_changed.connect(self.volume_changed)
        page.simulation_requested.connect(self.simulation_requested)

        page.phase_mode_changed.connect(self._on_phase_mode_changed)
        self.overlay_page.phase_mode_changed.connect(self._on_phase_mode_changed)

        page.target_changed.connect(self._on_target_changed)
        self.overlay_page.target_changed.connect(self._on_target_changed)

        self.setup_page.bindings_changed.connect(self.bindings_changed)
        self.setup_page.calibration_finished.connect(self.bindings_changed)
        self.setup_page.fit_source_changed.connect(self.fit_source_changed)
        self.setup_page.fit_degree_changed.connect(self.fit_degree_changed)

    def _on_phase_mode_changed(self, key: str, mode: str):
        """
        Keep the two pages in step, then tell the controller once.

        The same socket has a picker on the dashboard card and another under
        its overlay chip; a change on either has to show on both.
        """
        card = self.dashboard_page.cards.get(key)
        if card is not None and card.phase_mode() != mode:
            card.set_phase_mode(mode)

        entry = self.overlay_page.series.get(key)
        if entry is not None and entry["combo"].currentData() != mode:
            self.overlay_page.set_phase_mode(key, mode)

        self.phase_mode_changed.emit(key, mode)

    def _open_at_a_size_that_fits(self):
        """
        Open at a comfortable size, or at the screen's size if that is smaller.

        1440x860 is the size this is laid out for, but it is wider than a small
        laptop, and a window that opens larger than the screen puts its own
        controls out of reach. Live zoom changes the contents, while this
        outer window stays within the available desktop.
        """
        preferred = (1440, 860)

        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            self.resize(*preferred)
            return

        # The available geometry, so the taskbar's strip is not counted as room
        area = screen.availableGeometry()
        width = min(preferred[0], int(area.width() * 0.96))
        height = min(preferred[1], int(area.height() * 0.94))

        self.resize(width, height)
        self.move(area.x() + (area.width() - width) // 2,
                  area.y() + (area.height() - height) // 2)

    def resizeEvent(self, event):
        """
        Let every page lay itself out again, not just the visible one.

        A page in a QStackedWidget is only resized when it is shown, so a
        hidden page keeps the layout it had when the window was wider - and
        because the window's minimum size is the largest of all three pages,
        that stale layout would go on holding the window open.
        """
        super().resizeEvent(event)
        if hasattr(self, "live_scale"):
            self.live_scale.schedule()
        for page in (self.setup_page, self.dashboard_page, self.overlay_page):
            page.reflow()

    def _on_target_changed(self, target: str):
        """
        Keep the dashboard and the overlay showing the same quantity.

        Switching one page and finding the other still on force is the kind of
        thing that gets a millimetre read as a newton.
        """
        self.dashboard_page.show_target(target)
        self.overlay_page.set_target(target)
        self.target_changed.emit(target)

    def set_target(self, target: str):
        """Set the shown quantity from the controller, without echoing back."""
        self.dashboard_page.show_target(target)
        self.overlay_page.set_target(target)

    def set_available_targets(self, targets):
        """Hide the tabs for anything this equations file cannot predict."""
        for card in self.dashboard_page.cards.values():
            card.set_available_targets(targets)
        self.overlay_page.set_available_targets(targets)

    # --- API used by the controller ----------------------------------------

    @property
    def cards(self) -> dict:
        return self.dashboard_page.cards

    def add_sensor_card(self, key, display_name, row, col, subtitle=""):
        self.dashboard_page.add_sensor_card(key, display_name, row, col, subtitle)

    def clear_sensor_cards(self):
        self.dashboard_page.clear_sensor_cards()

    def set_overlay_series(self, entries):
        self.overlay_page.set_series(entries)

    def configure_socket(self, key, phase_modes, phase_mode, maxima):
        """
        Tell both pages what this socket can do.

        The available modes come from the sensor's equations, and the maxima -
        one per target - decide how far each fixed Y ladder goes. A plain
        number is read as the force maximum.
        """
        if not isinstance(maxima, dict):
            maxima = {TARGET_FORCE: maxima}

        card = self.dashboard_page.cards.get(key)
        if card is not None:
            card.set_phase_options(phase_modes, phase_mode)
            for target, maximum in maxima.items():
                card.set_maximum(target, maximum)

        if key in self.overlay_page.series:
            self.overlay_page.set_phase_options(key, phase_modes, phase_mode)

    def set_overlay_max_force(self, maxima):
        """Largest calibrated maximum per target, for the shared chart."""
        if not isinstance(maxima, dict):
            maxima = {TARGET_FORCE: maxima}
        for target, maximum in maxima.items():
            self.overlay_page.set_maximum(target, maximum)

    def update_sensor_value(self, key, raw_value, values):
        """Route one sample to the card and the overlay trace."""
        card = self.dashboard_page.cards.get(key)
        if card is not None:
            card.update_data(raw_value, values)
        self.overlay_page.update_value(key, values)

    def update_connection_state(self, connected: bool):
        self.dashboard_page.update_connection_state(connected)

    def update_simulation_state(self, active: bool):
        self.dashboard_page.update_simulation_state(active)

    def set_fit_sources(self, sources, current):
        """Offer the fits the loaded equations file contains."""
        self.setup_page.set_fit_sources(sources, current)

    def set_fit_degrees(self, degrees, current):
        """Offer the polynomial degrees the loaded equations file contains."""
        self.setup_page.set_fit_degrees(degrees, current)

    def refresh_ports(self):
        self.dashboard_page.refresh_ports()

    def set_hint(self, text: str):
        """Rig status, in the status bar now that the sidebar is gone."""
        self.statusBar().showMessage(text.replace("\n", "     |     "))

    # --- styling -----------------------------------------------------------

    def _apply_window_style(self):
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {COLORS["app_bg"]};
            }}

            QWidget {{
                background-color: {COLORS["app_bg"]};
                color: {COLORS["text"]};
                font-family: 'Segoe UI', sans-serif;
                font-size: 13px;
            }}

            QLabel {{
                color: {COLORS["text"]};
                background-color: transparent;
            }}

            QToolTip {{
                background-color: {COLORS["menu_bg"]};
                color: {COLORS["menu_text"]};
                border: 1px solid {COLORS["panel_border"]};
                padding: 4px;
            }}

            {theme.scrollbar_style()}
        """)

    def _menu_style(self) -> str:
        return f"""
            QMenuBar {{
                background-color: {COLORS["nav_bg"]};
                color: {COLORS["nav_text"]};
                border-bottom: 1px solid {COLORS["nav_border"]};
                padding: 3px 4px;
            }}

            QMenuBar::item {{
                background: transparent;
                padding: 6px 16px;
                margin: 1px 2px;
                border-radius: 4px;
                font-weight: bold;
            }}

            QMenuBar::item:selected {{
                background-color: {COLORS["nav_hover"]};
            }}

            QMenuBar::item:checked {{
                background-color: {COLORS["nav_active_bg"]};
                color: {COLORS["nav_active_text"]};
            }}

            QMenu {{
                background-color: {COLORS["menu_bg"]};
                color: {COLORS["menu_text"]};
                border: 1px solid {COLORS["panel_border"]};
                padding: 4px;
            }}

            QMenu::item {{
                padding: 6px 26px 6px 20px;
                border-radius: 3px;
            }}

            QMenu::item:selected {{
                background-color: {COLORS["menu_hover"]};
            }}

            QMenu::separator {{
                height: 1px;
                background: {COLORS["panel_border"]};
                margin: 4px 8px;
            }}
        """

    def _corner_button_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: transparent;
                color: {COLORS["nav_text"]};
                border: 1px solid transparent;
                border-radius: 4px;
                padding: 5px 12px;
                font-size: 13px;
            }}

            QPushButton:hover {{
                background-color: {COLORS["nav_hover"]};
                border-color: {COLORS["nav_border"]};
            }}

            QPushButton:pressed {{
                background-color: {COLORS["nav_hover"]};
                border-color: {COLORS["nav_active_bg"]};
                padding: 7px 10px 3px 14px;
            }}

            QPushButton:checked {{
                background-color: {COLORS["nav_active_bg"]};
                color: {COLORS["nav_active_text"]};
            }}

            QPushButton::menu-indicator {{
                width: 0px;
            }}
        """

    def _status_style(self) -> str:
        return f"""
            QStatusBar {{
                background-color: {COLORS["nav_bg"]};
                color: {COLORS["muted_text"]};
                border-top: 1px solid {COLORS["nav_border"]};
            }}

            QStatusBar::item {{
                border: none;
            }}
        """
