"""
A layout whose row of controls wraps instead of holding the window open.

A QHBoxLayout reports the sum of its children as its minimum width, so a
toolbar of a dozen controls stops the window from ever being narrower than the
toolbar. On a small screen that shows as a window that cannot be shrunk, and
that springs back to its old width whenever anything re-runs the layout.

This one puts what fits on a line and moves the rest to the next, so the
minimum width is the widest single control rather than the sum of them all.
Nothing is squeezed, so no label is ever clipped: the bar grows a line instead.
"""

from PyQt6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QSizePolicy, QSpacerItem


class FlowLayout(QLayout):
    """Left-to-right, wrapping to a new line when the width runs out."""

    def __init__(self, parent=None, margin=0, h_spacing=8, v_spacing=6):
        super().__init__(parent)
        self._items = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(QMargins(margin, margin, margin, margin))

    # --- QLayout plumbing --------------------------------------------------

    def addItem(self, item):
        self._items.append(item)

    def addSpacing(self, width: int):
        """A gap between groups of controls, as QHBoxLayout.addSpacing gives."""
        self.addItem(QSpacerItem(width, 0, QSizePolicy.Policy.Fixed,
                                 QSizePolicy.Policy.Minimum))

    def addStretch(self, stretch: int = 0):
        """
        Accepted and ignored.

        A stretch pushes the rest of a row to the far edge, which means nothing
        once rows wrap - and callers converted from a QHBoxLayout have one.
        """

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    # --- geometry ----------------------------------------------------------

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._lay_out(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._lay_out(rect, apply=True)

    def sizeHint(self):
        """One line of everything, which is how the bar looks when it fits."""
        margins = self.contentsMargins()
        width = margins.left() + margins.right()
        height = 0
        for index, item in enumerate(self._items):
            if item.widget() is not None and item.widget().isHidden():
                continue
            hint = item.sizeHint()
            width += hint.width() + (self._h_spacing if index else 0)
            height = max(height, hint.height())
        return QSize(width, height + margins.top() + margins.bottom())

    def minimumSize(self):
        """
        The widest single control, not the sum of them.

        This is the whole point of the class: it is what lets the window be
        narrower than its toolbar.
        """
        size = QSize(0, 0)
        for item in self._items:
            if item.widget() is not None and item.widget().isHidden():
                continue
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(),
                            margins.top() + margins.bottom())

    def _lay_out(self, rect, apply: bool) -> int:
        """Place the items, or just measure how tall they would be."""
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(),
                             -margins.right(), -margins.bottom())

        x, y = area.x(), area.y()
        line_height = 0

        for item in self._items:
            if item.widget() is not None and item.widget().isHidden():
                continue
            hint = item.sizeHint().expandedTo(item.minimumSize())
            if item.hasHeightForWidth():
                width = max(item.minimumSize().width(), min(hint.width(), area.width()))
                hint = QSize(width, item.heightForWidth(width))
            next_x = x + hint.width()

            # Wrap, unless this is the first item on the line: something wider
            # than the whole bar still has to go somewhere.
            if next_x > area.right() + 1 and line_height > 0:
                x = area.x()
                y = y + line_height + self._v_spacing
                next_x = x + hint.width()
                line_height = 0

            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))

            x = next_x + self._h_spacing
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + margins.bottom()


class GroupLayout(FlowLayout):
    """Wrap whole groups, distributing spare width evenly between them."""

    def _lay_out(self, rect, apply):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(),
                             -margins.right(), -margins.bottom())
        rows, row, used = [], [], 0
        for index, item in enumerate(self._items):
            if item.widget() is not None and item.widget().isHidden():
                continue
            size = item.sizeHint().expandedTo(item.minimumSize())
            if row and used + self._h_spacing + size.width() > area.width():
                rows.append(row)
                row, used = [], 0
            used += (self._h_spacing if row else 0) + size.width()
            row.append((index, item, size))
        if row:
            rows.append(row)
        y = area.y()
        for row in rows:
            height = max(size.height() for _, _, size in row)
            spare = max(0, area.width() - sum(size.width() for _, _, size in row))
            gap = spare / (len(row) - 1) if len(row) > 1 else 0
            x = float(area.x())
            if len(row) == 1:
                x += spare * min(row[0][0], 2) / 2
            for _, item, size in row:
                if apply:
                    item.setGeometry(QRect(round(x), y + (height - size.height()) // 2,
                                           size.width(), size.height()))
                x += size.width() + gap
            y += height + self._v_spacing
        return y - rect.y() + margins.bottom() - (self._v_spacing if rows else 0)
