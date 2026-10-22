import sys

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

import app_paths
from gui import theme

# A packaged build ships no data folders; create them before anything looks
# for a file inside one.
app_paths.ensure_data_dirs()

from gui.gui import MainWindow
from inference.data_logger import DataLogger
from inference.polynomial_engine import (PolynomialEngine, TARGET_FORCE,
                                         TARGET_DISPLACEMENT)
from inference.sensor_binding import (BindingStore, SensorCatalog, POSITIONS,
                                      display_position)
from inference.simulation_source import SimulatedSensorSource
from inference.teensy_serial import TeensySerialSource

GRID_COLUMNS = 4

# Identifies the program to the Windows shell. Without it the taskbar groups
# the window under whatever launched it -- python.exe from a checkout -- and
# shows that program's icon instead of this one.
APP_USER_MODEL_ID = "AhmetColakoglu.SensorTrackingStudio"


def apply_app_identity(app):
    """
    Give the application its icon and its own taskbar identity.

    The .exe carries an icon for Explorer, but that is a different thing from
    the icon on the window and in the taskbar: those come from Qt, and Qt uses
    a default until it is told otherwise.

    Returns the QIcon (possibly null) so the main window can set it on itself
    too: QApplication.setWindowIcon() is meant to cover every window, but the
    taskbar button reads the top-level window's own icon (WM_GETICON), and in
    a frozen build that can still be unset at the moment Windows first asks
    for it -- the window is created and shown before Qt has finished wiring
    the application-level default through. Setting it on both is what
    actually gets the icon into the taskbar reliably.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                APP_USER_MODEL_ID)
        except (AttributeError, OSError) as exc:
            print(f"[App]: could not set the taskbar identity: {exc}")

    path = app_paths.icon_file()
    if not path:
        print("[App]: application icon not found; using the default.")
        return QIcon()

    icon = QIcon(path)
    app.setWindowIcon(icon)
    return icon


def pin_taskbar_icon(window):
    """
    Force the taskbar button's icon with the native Win32 API.

    Qt's own icon propagation -- QApplication.setWindowIcon and
    QMainWindow.setWindowIcon, both already applied before this runs -- sets
    the title-bar icon reliably, but on some Windows builds the taskbar
    button still shows Qt's generic fallback regardless: a long-standing gap
    between Qt's icon handling and how the shell reads WM_GETICON for the
    taskbar specifically. Sending WM_SETICON straight to the native window
    handle, with icons loaded by the shell's own LoadImageW at the exact
    sizes the taskbar and title bar ask for, sidesteps whatever Qt is or is
    not doing and is the fix that actually sticks.
    """
    if sys.platform != "win32":
        return
    path = app_paths.icon_file()
    if not path:
        return

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = wintypes.HICON
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
            ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SendMessageW.restype = ctypes.c_ssize_t
        user32.SendMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        WM_SETICON = 0x0080
        ICON_SMALL, ICON_BIG = 0, 1
        SM_CXICON, SM_CYICON = 11, 12
        SM_CXSMICON, SM_CYSMICON = 49, 50

        hwnd = int(window.winId())

        for icon_type, cx_metric, cy_metric in (
                (ICON_SMALL, SM_CXSMICON, SM_CYSMICON),
                (ICON_BIG, SM_CXICON, SM_CYICON)):
            size = user32.GetSystemMetrics(cx_metric), user32.GetSystemMetrics(cy_metric)
            # HICON restype is c_void_p under the hood, which ctypes returns
            # as a plain int (or None for NULL) rather than a wrapped object.
            hicon = user32.LoadImageW(None, path, IMAGE_ICON, *size, LR_LOADFROMFILE)
            if hicon:
                user32.SendMessageW(hwnd, WM_SETICON, icon_type, hicon)
    except OSError as exc:
        print(f"[App]: could not set the native taskbar icon: {exc}")


class SensorCalibApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        icon = apply_app_identity(self.app)

        # 1. Math engine, loaded from the calibration output
        self.engine = PolynomialEngine()

        # 2. Data logger, writing into the 'outputs' folder
        self.logger = DataLogger()

        # 3. User interface
        self.window = MainWindow()
        if not icon.isNull():
            # Belt and suspenders with the app-level icon set above: the
            # taskbar button reads the window's own icon, and setting it here
            # too is what makes it show up reliably instead of the generic one.
            self.window.setWindowIcon(icon)

        # 4. Data sources, created on demand
        self.serial_source = None
        self.sim_source = None

        # 5. Which sensor sits in which socket
        self.bindings = BindingStore().load()
        self.catalog = SensorCatalog(fit_source=self.bindings.fit_source,
                                     degree=self.bindings.fit_degree)
        self.engine.set_degree(self.bindings.fit_degree)
        self.engine.set_fit_source(self.bindings.fit_source)

        self.build_cards()
        self.publish_fit_sources()

        # --- Signal connections ---
        self.window.connect_requested.connect(self.start_streaming)
        self.window.disconnect_requested.connect(self.stop_streaming)
        self.window.tare_requested.connect(self.handle_tare)
        self.window.volume_changed.connect(self.logger.set_volume)
        self.window.record_requested.connect(self.handle_record_toggle)
        self.window.simulation_requested.connect(self.handle_simulation_toggle)
        self.window.bindings_changed.connect(self.handle_bindings_changed)
        self.window.phase_mode_changed.connect(self.handle_phase_mode_changed)
        self.window.fit_source_changed.connect(self.handle_fit_source_changed)
        self.window.fit_degree_changed.connect(self.handle_fit_degree_changed)

    # --- sockets and cards -------------------------------------------------

    def build_cards(self):
        """
        One card per bound socket, not one per calibrated sensor.

        There are 37 calibrated sensors and 12 sockets, so cards follow the
        mapping the operator saved. Cards are keyed by socket because that is
        what the array reports; the sensor bound to it is shown underneath.
        """
        self.window.clear_sensor_cards()

        bound = self.bindings.bound_positions()
        if not bound:
            self.window.dashboard_page.show_placeholder(
                "No sensors selected yet.\n\n"
                "Open the Setup page, apply a set or pick sensors socket by "
                "socket, then save the selection."
            )
            self.window.set_overlay_series([])
            self.window.set_hint("No sockets bound.\nStart on the Setup page.")
            return

        series = []
        for index, position in enumerate(bound):
            sensor_id = self.bindings.sensor_for(position)
            self.window.add_sensor_card(
                position,
                display_position(position),
                index // GRID_COLUMNS,
                index % GRID_COLUMNS,
                subtitle=sensor_id,
            )
            series.append((position, display_position(position), sensor_id))

        self.window.set_overlay_series(series)
        # The recording's frame layout is the bound sockets, in the order the
        # cards are laid out, so a socket that goes quiet leaves a gap in its
        # own row rather than shifting everything after it.
        self.logger.set_sockets(bound)
        self._configure_sockets(bound)
        self.window.set_hint(f"{len(bound)} of {len(POSITIONS)} sockets bound"
                             + (f"\nSet: {self.bindings.set_name}"
                                if self.bindings.set_name else ""))

    def _configure_sockets(self, positions):
        """
        Tell the UI what each socket can do, and apply the saved phase modes.

        Which modes are on offer comes from the sensor's own equations: a
        sensor fitted with only a loading curve cannot be read as unloading.
        The Y ladder is capped by the calibrated maximum for the same reason.
        """
        largest = {target: 0.0 for target in self.engine.targets}

        for position in positions:
            sensor_id = self.bindings.sensor_for(position)
            modes = self.engine.available_phases(sensor_id)

            saved = self.bindings.phase_mode_for(position)
            mode = saved if saved in modes else modes[0]
            self.engine.set_phase_mode(sensor_id, mode)

            maxima = {target: self.engine.max_for(sensor_id, target)
                      for target in self.engine.targets}
            for target, maximum in maxima.items():
                largest[target] = max(largest[target], maximum)

            self.window.configure_socket(position, modes, mode, maxima)

        self.window.set_available_targets(self.engine.targets)
        self.window.set_overlay_max_force(largest)

    def publish_fit_sources(self):
        """Tell the setup page which equations this file offers."""
        self.window.set_fit_degrees(self.engine.available_degrees(),
                                    self.engine.degree)
        self.window.set_fit_sources(self.engine.available_fit_sources(),
                                    self.engine.fit_source)

    def handle_fit_degree_changed(self, degree):
        """
        A different polynomial was chosen. Every coefficient changes with it.
        """
        self.engine.set_degree(degree)
        self.catalog.set_degree(degree)
        self.bindings.fit_degree = int(degree)
        if not self.bindings.is_empty():
            self.bindings.save()

        self.build_cards()
        self.publish_fit_sources()
        print(f"[App]: reading degree-{degree} equations "
              f"({len(self.engine.get_active_sensors())} sensors).")

    def handle_fit_source_changed(self, source):
        """
        The operator chose a different fit. Reload and rebuild.

        Changing this changes every coefficient, so the cards are rebuilt from
        scratch rather than left showing values from the previous model.
        """
        self.engine.set_fit_source(source)
        self.catalog.set_fit_source(source)
        self.bindings.fit_source = source
        if not self.bindings.is_empty():
            self.bindings.save()

        self.build_cards()
        print(f"[App]: reading the {source} fit "
              f"({len(self.engine.get_active_sensors())} sensors).")

    def handle_phase_mode_changed(self, position, mode):
        """A socket's curve was chosen, from a card or an apply-to-all control."""
        sensor_id = self.bindings.sensor_for(position)
        if not sensor_id:
            return

        self.engine.set_phase_mode(sensor_id, mode)
        self.bindings.set_phase_mode(position, mode)
        self.bindings.save()

    def handle_bindings_changed(self):
        """Reload everything that depends on the mapping or the equations."""
        self.engine.load_models()
        self.bindings = BindingStore().load()
        self.catalog = SensorCatalog(fit_source=self.bindings.fit_source,
                                     degree=self.bindings.fit_degree)
        self.engine.set_degree(self.bindings.fit_degree)
        self.engine.set_fit_source(self.bindings.fit_source)
        self.build_cards()
        self.publish_fit_sources()

        problems = self.bindings.validate(self.catalog)
        if problems:
            print("[App]: bindings needing attention:")
            for problem in problems:
                print(f"  - {problem}")

        print(f"[App]: {len(self.bindings.bound_positions())} sockets bound.")

    def sensor_for_event(self, evt) -> tuple[str, str]:
        """
        Resolve an incoming packet to (socket, sensor_id).

        The array reports a socket. An unbound socket still returns its own
        label so the raw value is logged rather than dropped; it simply has no
        model to convert with.
        """
        position = evt.get("short_id") or ""
        return position, (self.bindings.sensor_for(position) or "")

    # --- streaming ---------------------------------------------------------

    def start_streaming(self, port_name):
        """Opens the serial connection and begins streaming."""
        try:
            self.serial_source = TeensySerialSource(
                callback=self.process_incoming_data,
                port=port_name,
                baud=115200
            )
            self.serial_source.status.connect(lambda msg: print(f"[Serial]: {msg}"))
            self.serial_source.start()

            # create_new_session is not called here, to avoid redundant CSV files
            self.window.update_connection_state(True)
            print(f"[App]: Connected to {port_name}")
        except Exception as e:
            print(f"[Error]: Connection failed: {e}")

    def stop_streaming(self):
        """Stops the serial port and closes any active logging session."""
        if self.serial_source:
            self.serial_source.stop()
            self.logger.stop_session()  # Close CSV if it was recording
            self.serial_source = None

        self.window.update_connection_state(False)
        print("[App]: Disconnected.")

    def handle_simulation_toggle(self, activate: bool):
        """Starts or stops the simulated data source (no hardware needed)."""
        if activate:
            if not self.engine.get_active_sensors():
                print("[Sim]: No sensor models loaded, cannot simulate.")
                return

            bound = {p: self.bindings.sensor_for(p)
                     for p in self.bindings.bound_positions()}
            if not bound:
                print("[Sim]: No sockets bound, choose sensors on the Setup page.")
                return

            self.sim_source = SimulatedSensorSource(
                callback=self.process_incoming_data,
                engine=self.engine,
                interval_ms=40,  # 25 Hz per socket
                bindings=bound,
            )
            self.sim_source.status.connect(lambda msg: print(f"[Sim]: {msg}"))
            self.sim_source.start()

            self.window.update_simulation_state(True)
            print("[App]: Simulation mode ON.")
        else:
            if self.sim_source:
                self.sim_source.stop()
                self.sim_source = None

            self.logger.stop_session()  # Close CSV if it was recording
            self.window.update_simulation_state(False)
            print("[App]: Simulation mode OFF.")

    def handle_record_toggle(self, is_recording):
        """Controls logging sessions. Triggered by the Record button."""
        if is_recording:
            self.logger.create_new_session()
        else:
            self.logger.stop_session()
            print("[App]: Recording session saved to 'outputs' folder.")

    def handle_tare(self):
        """Uses the current raw values as the zero reference for every card."""
        print("[App]: Taring all sensors...")
        for position, card in self.window.cards.items():
            sensor_id = self.bindings.sensor_for(position)
            if sensor_id and hasattr(card, 'last_raw'):
                self.engine.set_tare(sensor_id, card.last_raw)
        print("[App]: Tare process completed.")

    def process_incoming_data(self, evt):
        """
        Called for every incoming packet: predict, log, and update the UI.
        """
        position, sensor_id = self.sensor_for_event(evt)
        raw_bz = evt.get("raw_bz")

        # 1. Convert the raw value, when the socket has a sensor bound to it.
        #    Both targets come from one call so the hysteresis phase is
        #    resolved once: asking twice could land them on opposite curves.
        values = self.engine.predict_all(sensor_id, raw_bz) if sensor_id else {}

        # 2. Log. Unbound sockets are logged too, with an empty sensor id, so
        #    no raw data is lost while the mapping is still being set up.
        self.logger.log(sensor_id, position, raw_bz,
                        values.get(TARGET_FORCE, 0.0),
                        values.get(TARGET_DISPLACEMENT, 0.0))

        # 3. Update the card and the overlay trace
        self.window.update_sensor_value(position, raw_bz, values)

    def run(self):
        """Shows the window and starts the event loop."""
        self.window.show()
        # Only meaningful once the native window handle exists, which show()
        # guarantees; see pin_taskbar_icon for why Qt's own icon calls are
        # not enough on their own.
        pin_taskbar_icon(self.window)
        sys.exit(self.app.exec())


if __name__ == "__main__":
    studio_app = SensorCalibApp()
    studio_app.run()
