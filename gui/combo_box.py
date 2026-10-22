"""Combo boxes with readable, consistently sized popup rows."""

from PyQt6.QtGui import QFontMetrics
from gui import theme
from PyQt6.QtWidgets import (QComboBox, QListView, QStyledItemDelegate,
                             QStyle, QStyleOptionComboBox, QStylePainter)


class _PopupRowDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        # Popup rows stay uniform even when the dashboard toolbar stretches
        # the closed control or Qt measures it before the layout is ready.
        size.setHeight(round(32 * theme.current_scale() / 100))
        return size


class ReadableComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Use the same item-view layout on every platform and in every theme.
        self.setView(QListView(self))
        self.setItemDelegate(_PopupRowDelegate(self))

    def showPopup(self):
        """
        Size the popup to its longest entry, not to the closed control.

        Qt matches the popup to the control's width, which truncates wherever
        the control is deliberately narrow - the measurement picker is fixed at
        190px, so "2026-02-02  (R2 0.9983)" arrived clipped in the one place
        the whole string matters.
        """
        metrics = QFontMetrics(self.view().font())
        widest = max((metrics.horizontalAdvance(self.itemText(i))
                      for i in range(self.count())), default=0)
        if widest:
            # Room for the row padding and a scrollbar, should one appear.
            self.view().setMinimumWidth(widest + 44)

        super().showPopup()


class PortComboBox(ReadableComboBox):
    """Show the port number when closed and full device details in the popup."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.currentIndexChanged.connect(lambda: self.setToolTip(self.currentText()))

    def initStyleOption(self, option):
        super().initStyleOption(option)
        if self.currentIndex() >= 0:
            option.currentText = str(self.currentData() or "")

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)
