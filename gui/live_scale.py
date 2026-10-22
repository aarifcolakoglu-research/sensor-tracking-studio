"""Application zoom independent of the operating system's display scaling.

Keep logical 100% metrics so repeated changes never accumulate rounding error.
Style changes and newly built sensor cards are picked up on the next event turn.
No windows, data sources or recording sessions are recreated.
"""

import re

from PyQt6 import sip
from PyQt6.QtCore import QObject, QEvent, QTimer
from PyQt6.QtWidgets import QApplication, QWidget, QLayout, QGridLayout

from gui import theme


class LiveScale(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.busy = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.apply)
        QApplication.instance().installEventFilter(self)
        self.schedule()

    def schedule(self):
        if not self.busy and not self.timer.isActive():
            self.timer.start(0)

    def eventFilter(self, obj, event):
        if (not sip.isdeleted(self.window) and not self.busy and isinstance(obj, QWidget)
                and (obj is self.window or self.window.isAncestorOf(obj))
                and event.type() in (QEvent.Type.ChildPolished, QEvent.Type.StyleChange)):
            self.schedule()
        return False

    @staticmethod
    def base(obj, key, current):
        cache = getattr(obj, '_zoom_metrics', None)
        if cache is None:
            cache = obj._zoom_metrics = {}
        original, applied = cache.get(key, (current, current))
        # An application state/theme change supplied fresh 100% metrics.
        if current != applied:
            original = current
        cache[key] = (original, current)
        return original

    @staticmethod
    def remember(obj, key, original, applied):
        obj._zoom_metrics[key] = (original, applied)

    def apply(self):
        if self.busy or sip.isdeleted(self.window):
            return
        self.timer.stop()
        self.busy = True
        factor = theme.current_scale() / 100
        try:
            widgets = [self.window] + self.window.findChildren(QWidget)
            layouts = self.window.findChildren(QLayout)
            cards = list(self.window.dashboard_page.cards.values())
            card_widgets = {w for c in cards for w in [c] + c.findChildren(QWidget)}
            card_layouts = {l for c in cards for l in c.findChildren(QLayout)}
            self._apply_widgets([w for w in widgets if w not in card_widgets], factor)
            self._apply_layouts([l for l in layouts if l not in card_layouts], factor)

            # All twelve cards have to fit simultaneously. Increase their
            # density only when the requested zoom would otherwise clip their
            # readings/controls or consume the charts' entire height.
            dash = self.window.dashboard_page
            margins = dash.layout().contentsMargins()
            width = self.window.width() - margins.left() - margins.right()
            height = (self.window.height() - self.window.menuBar().sizeHint().height()
                      - self.window.statusBar().sizeHint().height()
                      - margins.top() - margins.bottom() - dash.layout().spacing()
                      - dash.toolbar_layout.heightForWidth(width))
            card_factor = min(factor, max(0.5, (width - 3 * dash.grid_layout.spacing()) / (4 * 330)),
                              max(0.5, (height - 2 * dash.grid_layout.spacing()) / (3 * 170)))
            self._apply_widgets(list(card_widgets), card_factor)
            self._apply_layouts(list(card_layouts), card_factor)
            for card in cards:
                card.set_plot_scale(card_factor)
            self.window.overlay_page.set_plot_scale(factor)
            for page in (self.window.setup_page, dash, self.window.overlay_page):
                page.reflow()
        finally:
            self.busy = False

    @staticmethod
    def pixels(value, factor):
        return max(1, round(value * factor)) if 0 < value < 16777215 else value

    def _apply_widgets(self, widgets, factor):
        for widget in widgets:
            style = self.base(widget, 'style', widget.styleSheet())
            scaled = re.sub(r'(\d+(?:\.\d+)?)px',
                            lambda m: f'{self.pixels(float(m[1]), factor)}px', style)
            if widget.styleSheet() != scaled:
                widget.setStyleSheet(scaled)
            self.remember(widget, 'style', style, scaled)
            if widget is self.window:
                continue
            dimensions, changed = {}, False
            for name in ('minimumWidth', 'minimumHeight', 'maximumWidth', 'maximumHeight'):
                current = getattr(widget, name)()
                if name in getattr(widget, '_zoom_dynamic_dimensions', ()):
                    dimensions[name] = current
                    continue
                original = self.base(widget, name, current)
                value = self.pixels(original, factor)
                dimensions[name] = value
                changed |= value != current
                self.remember(widget, name, original, value)
            if changed:
                # Snapshot both sides before changing a fixed width/height.
                widget.setMinimumSize(0, 0)
                widget.setMaximumSize(16777215, 16777215)
                widget.setMaximumSize(dimensions['maximumWidth'], dimensions['maximumHeight'])
                widget.setMinimumSize(dimensions['minimumWidth'], dimensions['minimumHeight'])
                widget.updateGeometry()

    def _apply_layouts(self, layouts, factor):
        for layout in layouts:
            margins = layout.contentsMargins()
            current = (margins.left(), margins.top(), margins.right(), margins.bottom())
            original = self.base(layout, 'margins', current)
            value = tuple(self.pixels(v, factor) for v in original)
            layout.setContentsMargins(*value)
            self.remember(layout, 'margins', original, value)
            names = ('horizontalSpacing', 'verticalSpacing') if isinstance(layout, QGridLayout) else ('spacing',)
            for name in names:
                original = self.base(layout, name, getattr(layout, name)())
                value = self.pixels(original, factor)
                getattr(layout, 'set' + name[0].upper() + name[1:])(value)
                self.remember(layout, name, original, value)
            for name in ('_h_spacing', '_v_spacing'):
                if hasattr(layout, name):
                    original = self.base(layout, name, getattr(layout, name))
                    value = self.pixels(original, factor)
                    setattr(layout, name, value)
                    self.remember(layout, name, original, value)
            layout.invalidate()
