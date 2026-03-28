import math
import random

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QColor, QLinearGradient, QPainter, QPen
from PyQt5.QtWidgets import QWidget


class WaveWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._target_amp = 3.0
        self._amp = 3.0
        self._mode = "idle"
        self.setMinimumHeight(120)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        if mode == "wake":
            self._target_amp = 10.0
        elif mode == "listen":
            self._target_amp = 18.0
        elif mode == "talk":
            self._target_amp = 12.0
        else:
            self._target_amp = 3.0

    def _tick(self) -> None:
        self._phase += 0.35
        self._amp += (self._target_amp - self._amp) * 0.15
        self.update()

    def paintEvent(self, _event):
        w = self.width()
        h = self.height()
        mid = h // 2

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bg = QLinearGradient(0, 0, w, h)
        bg.setColorAt(0.0, QColor(14, 20, 31))
        bg.setColorAt(1.0, QColor(9, 14, 23))
        painter.fillRect(self.rect(), bg)

        if self._mode == "talk":
            line_primary = QColor(77, 255, 196)
            line_secondary = QColor(36, 182, 255)
        elif self._mode == "listen":
            line_primary = QColor(70, 226, 255)
            line_secondary = QColor(143, 117, 255)
        elif self._mode == "wake":
            line_primary = QColor(255, 220, 106)
            line_secondary = QColor(255, 137, 84)
        else:
            line_primary = QColor(114, 162, 205)
            line_secondary = QColor(60, 98, 134)

        points = []
        for x in range(0, w, 6):
            t = (x / max(w, 1)) * 2 * math.pi
            jitter = random.uniform(-1.3, 1.3)
            y = mid + math.sin((t * 3.2) + self._phase) * self._amp + jitter
            points.append((x, y))

        if len(points) > 1:
            painter.setPen(QPen(line_secondary, 4))
            for i in range(len(points) - 1):
                x1, y1 = points[i]
                x2, y2 = points[i + 1]
                painter.drawLine(int(x1), int(y1 + 4), int(x2), int(y2 + 4))

            painter.setPen(QPen(line_primary, 2))
            for i in range(len(points) - 1):
                x1, y1 = points[i]
                x2, y2 = points[i + 1]
                painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        painter.setPen(QPen(QColor(74, 96, 121), 1, Qt.DotLine))
        painter.drawLine(0, mid, w, mid)
