"""Run with QT_QPA_PLATFORM=offscreen for a headless layout regression check."""

import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QSignalBlocker
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionComboBox, QScrollArea

import main
from gui import theme
from inference.sensor_binding import BindingStore
from inference.polynomial_engine import TARGET_DISPLACEMENT, TARGET_FORCE


def settle_layout():
    for _ in range(5):
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()


class ResponsiveWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # Qt's Windows offscreen plugin does not discover the system fonts.
        if os.name == "nt" and not QFontDatabase.families():
            for name in ("segoeui.ttf", "segoeuib.ttf", "seguisym.ttf"):
                QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / name))
            cls.app.setFont(QFont("Segoe UI", 9))
        cls.save_patch = patch.object(BindingStore, "save", return_value="test-only")
        cls.save_patch.start()
        with patch.object(main, "QApplication", return_value=cls.app):
            cls.studio = main.SensorCalibApp()
        cls.window = cls.studio.window
        cls.window.show()
        settle_layout()
        cls.startup_size = cls.window.size()
        cls.startup_frame = cls.window.frameGeometry()

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.window.deleteLater()
        settle_layout()
        cls.save_patch.stop()

    def assert_grid(self):
        page = self.window.dashboard_page
        locations = [page.grid_layout.getItemPosition(page.grid_layout.indexOf(card))[:2]
                     for card in page.cards.values()]
        self.assertEqual(locations, [divmod(i, page._columns) for i in range(len(locations))])

    def assert_combo_text_fits(self, combo):
        option = QStyleOptionComboBox()
        combo.initStyleOption(option)
        text_area = combo.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option,
            QStyle.SubControl.SC_ComboBoxEditField, combo)
        text = option.currentText or combo.placeholderText()
        self.assertLessEqual(combo.fontMetrics().horizontalAdvance(text), text_area.width(), text)

    def test_initial_window_fits_available_screen(self):
        self.assertTrue(self.window.screen().availableGeometry().contains(self.startup_frame))

    def test_fit_changes_keep_size_and_rebuild_the_current_grid(self):
        setup = self.window.setup_page
        if setup.degree_combo.count() < 2 or not self.window.cards:
            self.skipTest("Requires the calibrated dataset and saved sensor bindings")
        for width, height in ((1280, 680), (960, 600), (720, 540)):
            self.window.resize(width, height)
            self.window.show_page(0)
            settle_layout()
            before = self.window.size()
            self.assertEqual((before.width(), before.height()), (width, height))
            for combo, selection in ((setup.degree_combo, 3), (setup.fit_source_combo, 5),
                                     (setup.degree_combo, 2), (setup.fit_source_combo, "combined")):
                index = combo.findData(selection)
                self.assertGreaterEqual(index, 0)
                combo.setCurrentIndex(index)
                combo.activated.emit(index)
                settle_layout()
                self.assertEqual(self.window.size(), before)
                self.assert_grid()
                for page in (1, 2, 0):
                    self.window.show_page(page)
                    settle_layout()
                    self.assertEqual(self.window.size(), before)
                    self.assert_grid()

    def test_full_names_readings_and_plot_labels_survive_resizing(self):
        if not self.window.cards:
            self.skipTest("Requires saved sensor bindings")
        for mode in ("light", "dark", "mix"):
            theme.set_theme(mode)
            self.window.dashboard_page.apply_theme()
            self.window.overlay_page.apply_theme()
            for width, height in ((1920, 1080), (1440, 860), (1280, 680)):
                self.window.resize(width, height)
                for target in (TARGET_FORCE, TARGET_DISPLACEMENT):
                    self.window.set_target(target)
                    self.window.show_page(1)
                    settle_layout()
                    dash = self.window.dashboard_page
                    self.assertEqual(dash.findChildren(QScrollArea), [])
                    self.assertEqual(dash._columns, 4)
                    self.assert_grid()
                    self.assert_combo_text_fits(dash.all_target_combo)
                    for key, card in self.window.cards.items():
                        self.window.update_sensor_value(key, 12345, {TARGET_FORCE: 12.5, TARGET_DISPLACEMENT: 2.0})
                    settle_layout()
                    for card in self.window.cards.values():
                        self.assertEqual(card.tabs[TARGET_DISPLACEMENT].text(), "Indentation depth")
                        self.assertTrue(dash.grid_holder.rect().contains(card.geometry()))
                        self.assertGreaterEqual(card.canvas.height(), 50)
                        self.assert_combo_text_fits(card.phase_combo)
                        self.assert_combo_text_fits(card.y_combo)
                        for label in (card.title_label, card.subtitle_label,
                                      card.raw_label, card.value_label, card.gram_label):
                            self.assertGreaterEqual(label.width(), label.sizeHint().width(), label.text())
                    self.window.show_page(2)
                    settle_layout()
                    overlay = self.window.overlay_page
                    self.assertEqual(overlay.chip_scroll.horizontalScrollBar().maximum(), 0)
                    for entry in overlay.series.values():
                        self.assertGreaterEqual(entry["chip"].width(), entry["chip"].sizeHint().width())
                        self.assert_combo_text_fits(entry["combo"])
                    overlay.canvas.draw()
                    label_box = overlay.ax.xaxis.label.get_window_extent(overlay.canvas.get_renderer())
                    self.assertGreaterEqual(label_box.y0, 0)
                    self.assertLessEqual(label_box.y1, overlay.fig.bbox.height)

    def test_port_picker_shows_short_name_but_retains_device_details(self):
        from gui.pages.dashboard_page import DashboardPage
        page = DashboardPage()
        ports = [{"device": "COM5", "description": "Teensy USB serial device"},
                 {"device": "COM9", "description": "Second Teensy USB serial device"}]
        with patch("gui.pages.dashboard_page.available_port_infos", return_value=ports), \
             patch("gui.pages.dashboard_page.pick_default_port", return_value="COM5"):
            page.refresh_ports()
        combo = page.port_combo
        for port in ports:
            combo.setCurrentIndex(combo.findData(port["device"]))
            option = QStyleOptionComboBox()
            combo.initStyleOption(option)
            self.assertEqual(option.currentText, port["device"])
            self.assertIn(port["description"], combo.itemText(combo.currentIndex()))
            self.assert_combo_text_fits(combo)
        selected = []
        # This standalone page is not wired to the hardware controller.
        page.connect_requested.connect(selected.append)
        page.handle_connect()
        self.assertEqual(selected, ["COM9"])
        page.deleteLater()

    def test_compact_toolbar_and_uniform_setup_headers(self):
        self.window.resize(1280, 680)
        self.window.show_page(1)
        settle_layout()
        page = self.window.dashboard_page
        self.assertLessEqual(page.top_bar.height(), 60)
        centres = [widget.mapTo(page.top_bar, widget.rect().center()).y()
                   for widget in (page.port_combo, page.all_target_combo, page.btn_connect)]
        self.assertLessEqual(max(centres) - min(centres), 4)
        for card in page.cards.values():
            centres = [label.mapTo(card, label.rect().center()).y()
                       for label in (card.title_label, card.subtitle_label,
                                     card.raw_label, card.value_label, card.gram_label)]
            self.assertLessEqual(max(centres) - min(centres), 2)
        headers = self.window.setup_page.column_headers
        self.assertEqual(len({label.height() for label in headers}), 1)
        self.assertGreaterEqual(headers[0].height(), 40)

    def test_toolbar_groups_wrap_whole_and_share_spacing(self):
        page = self.window.dashboard_page
        for width in (1440, 1280, 960, 720):
            self.window.resize(width, 680)
            self.window.show_page(1)
            settle_layout()
            for group in page.toolbar_groups:
                self.assertTrue(page.top_bar.rect().contains(group.geometry()))
                centres = []
                for i in range(group.layout().count()):
                    control = group.layout().itemAt(i).widget()
                    self.assertEqual(control.height(), 32)
                    self.assertTrue(group.rect().contains(control.geometry()))
                    centres.append(control.geometry().center().y())
                self.assertEqual(len(set(centres)), 1)
            if width >= 1280:
                first, middle, last = (g.geometry() for g in page.toolbar_groups)
                self.assertEqual(first.top(), last.top())
                self.assertLessEqual(abs((middle.left() - first.right()) -
                                         (last.left() - middle.right())), 1)
            else:
                self.assertGreater(page.toolbar_groups[-1].y(), page.toolbar_groups[0].y())

    def test_setup_actions_stay_visible_when_resized_and_maximized(self):
        page = self.window.setup_page
        self.window.show_page(0)
        for width, height in ((1440, 860), (1280, 680), (960, 600), (720, 540)):
            self.window.resize(width, height)
            settle_layout()
            self.assertEqual(self.window.size().width(), width)
            self.assertEqual(self.window.size().height(), height)
            for button in (page.btn_save, page.btn_calibrate, page.btn_apply_set, page.btn_clear):
                top_left = button.mapTo(self.window, QPoint(0, 0))
                rect = button.rect().translated(top_left)
                self.assertTrue(self.window.rect().contains(rect), button.text())
        self.window.showMaximized()
        settle_layout()
        for button in (page.btn_save, page.btn_calibrate):
            self.assertTrue(self.window.rect().contains(
                button.rect().translated(button.mapTo(self.window, QPoint(0, 0)))))
        self.window.showNormal()

    def test_live_scale_preserves_session_and_round_trips(self):
        page = self.window.dashboard_page
        with patch.object(theme, 'save_settings'):
            self.window.set_scale(100)
            self.window.resize(1280, 680)
            settle_layout()
            size = self.window.size()
            cards = dict(page.cards)
            base_width = page.volume_input.width()
            self.studio.handle_simulation_toggle(True)
            source = self.studio.sim_source
            try:
                for percent in (150, 70, 125, 100, 150, 100):
                    self.window.set_scale(percent)
                    settle_layout()
                    self.assertIs(self.studio.sim_source, source)
                    self.assertEqual(page.cards, cards)
                    self.assertEqual(page.volume_input.width(), round(base_width * percent / 100))
                    self.assertEqual(page.btn_connect.height(), round(32 * percent / 100))
                    self.assertEqual(self.window.size(), size)
                    for index in (0, 1, 2):
                        self.window.show_page(index)
                        settle_layout()
                        self.assertEqual(self.window.size(), size)
                    # Measure a stable visible frame; hidden cards may have
                    # just received a wider number before their next layout.
                    with QSignalBlocker(source._timer):
                        self.window.show_page(1)
                        settle_layout()
                        for card in page.cards.values():
                            self.assertGreaterEqual(card.canvas.height(), 45)
                            for label in (card.title_label, card.subtitle_label, card.raw_label,
                                          card.value_label, card.gram_label):
                                self.assertGreaterEqual(label.width(), label.sizeHint().width(),
                                                        f'{percent}%: {label.text()}')
                    self.assertTrue(self.window.scale_actions[percent].isChecked())
                self.window.set_scale(125)
                self.window.set_theme('light')
                settle_layout()
                self.assertEqual(page.volume_input.width(), round(base_width * 1.25))
                self.assertIn('font-size: 16px', self.window.setup_page.stat_labels[0].styleSheet())
                self.studio.handle_simulation_toggle(False)
                self.window.show_page(1)
                settle_layout()
                style = next(iter(page.cards.values())).value_label.styleSheet()
                self.studio.build_cards()
                settle_layout()
                self.assertEqual(next(iter(page.cards.values())).value_label.styleSheet(), style)
            finally:
                self.studio.handle_simulation_toggle(False)
                self.window.set_theme('mix')
                self.window.set_scale(100)
                settle_layout()


if __name__ == "__main__":
    unittest.main()
