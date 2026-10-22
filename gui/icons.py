"""
Small interface icons, painted rather than shipped.

Two reasons not to use a glyph font or an image file: the symbols that mean
"full screen" (U+26F6 and friends) are missing from common Windows fonts and
fall back to a box, and a bitmap would need one copy per theme. Painting them
means they take the current palette's colour and stay sharp at any interface
scale.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


def _canvas(size: int) -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pixmap, painter


def _pen(colour: str, size: int) -> QPen:
    pen = QPen(QColor(colour))
    pen.setWidthF(max(1.4, size * 0.085))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def fullscreen_icon(colour: str, size: int = 18) -> QIcon:
    """Four corner brackets pointing outwards: enter full screen."""
    pixmap, painter = _canvas(size)
    painter.setPen(_pen(colour, size))

    m = size * 0.18          # margin from the edge
    arm = size * 0.26        # length of each bracket arm
    right, bottom = size - m, size - m

    for x, y, dx, dy in ((m, m, 1, 1), (right, m, -1, 1),
                         (m, bottom, 1, -1), (right, bottom, -1, -1)):
        painter.drawLine(int(x), int(y), int(x + arm * dx), int(y))
        painter.drawLine(int(x), int(y), int(x), int(y + arm * dy))

    painter.end()
    return QIcon(pixmap)


def exit_fullscreen_icon(colour: str, size: int = 18) -> QIcon:
    """The same brackets turned inwards: leave full screen."""
    pixmap, painter = _canvas(size)
    painter.setPen(_pen(colour, size))

    inset = size * 0.34
    arm = size * 0.24
    far = size - inset

    for x, y, dx, dy in ((inset, inset, -1, -1), (far, inset, 1, -1),
                         (inset, far, -1, 1), (far, far, 1, 1)):
        painter.drawLine(int(x), int(y), int(x + arm * dx), int(y))
        painter.drawLine(int(x), int(y), int(x), int(y + arm * dy))

    painter.end()
    return QIcon(pixmap)


def chevron_icon(colour: str, size: int = 12) -> QIcon:
    """A small downward chevron, for buttons that open a menu."""
    pixmap, painter = _canvas(size)
    painter.setPen(_pen(colour, size + 4))

    box = QRectF(size * 0.22, size * 0.32, size * 0.56, size * 0.36)
    painter.drawLine(int(box.left()), int(box.top()),
                     int(box.center().x()), int(box.bottom()))
    painter.drawLine(int(box.center().x()), int(box.bottom()),
                     int(box.right()), int(box.top()))

    painter.end()
    return QIcon(pixmap)
