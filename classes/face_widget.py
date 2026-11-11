"""Il volto di Albert: occhi che seguono la persona."""
import random

from PySide6.QtCore import QPointF, Qt, QRectF, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from classes.config import AMBER, BG, CYAN, RED


# ============================================================ il volto di Albert
class Face(QWidget):
    """Occhi che seguono la persona vista dalla telecamera (o il cursore, senza telecamera), battito di
    ciglia, colore in base allo stato."""

    def __init__(self):
        super().__init__()
        self.mode = "fermo"
        self.off = QPointF(0, 0)
        self.target = QPointF(0, 0)
        self.blink = 0.0       # 0 = aperti, 1 = chiusi
        self._blink_dir = 0
        self.follow_mouse = True   # False quando la telecamera è attiva
        self.setMinimumSize(360, 360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        t = QTimer(self, interval=33)
        t.timeout.connect(self._anim)
        t.start()
        self._next_blink()

    def set_person(self, nx, ny, presente):
        """nx, ny in [-1, 1]: posizione del volto più vicino nell'immagine (0,0 = centro)."""
        self.target = QPointF(-nx * 28, ny * 18) if presente else QPointF(0, 0)

    def set_mode(self, mode):
        if mode != self.mode:
            self.mode = mode
            self.update()

    def _next_blink(self):
        QTimer.singleShot(int(2500 + random.random() * 3000), self._start_blink)

    def _start_blink(self):
        self._blink_dir = 1
        self._next_blink()

    def _anim(self):
        if self.follow_mouse and self.isVisible():
            p = self.mapFromGlobal(QCursor.pos())
            w, h = max(1, self.width()), max(1, self.height())
            dx = max(-1, min(1, (p.x() - w / 2) / w * 2))
            dy = max(-1, min(1, (p.y() - h / 2) / h * 2))
            self.target = QPointF(dx * 9, dy * 7)
        self.off += (self.target - self.off) * 0.3
        if self._blink_dir:
            self.blink += 0.35 * self._blink_dir
            if self.blink >= 1:
                self.blink, self._blink_dir = 1.0, -1
            elif self.blink <= 0:
                self.blink, self._blink_dir = 0.0, 0
        if self.isVisible():
            self.update()

    def paintEvent(self, _):
        color = QColor({"movimento": AMBER, "arresto": RED}.get(self.mode, CYAN))
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height())
        s = side / 200
        p.translate(self.width() / 2 - 150 * s, self.height() / 2 - 150 * s)
        p.scale(s, s)   
        p.setBrush(QColor("#1b3352")); p.setPen(QPen(QColor(CYAN), 2))
        #p.drawEllipse(QPointF(150, 150), 140, 140)
        p.setBrush(QColor(BG)); p.setPen(QPen(QColor("#2d7fd8"), 2))
        #p.drawEllipse(QPointF(150, 150), 116, 116)
        p.setBrush(color); p.setPen(Qt.NoPen)
        ry = 13 * (1 - 0.92 * self.blink)
        for cx in (108, 192):
            w, h = 26,2*ry
            rect = QRectF(cx -13 + self.off.x(), 128 -ry +self.off.y(),w,h)
            p.drawRoundedRect(rect, 6, 6)
            #p.drawEllipse(QPointF(cx + self.off.x(), 128 + self.off.y()), 13, ry)
        pen = QPen(color, 5); pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen); p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        if self.mode == "arresto":
            path.moveTo(118, 196); path.lineTo(182, 196)
        else:
            path.moveTo(100, 188); path.quadTo(150, 204, 200, 188)
        p.drawPath(path)
