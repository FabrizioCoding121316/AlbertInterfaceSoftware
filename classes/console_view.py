"""Console di sola lettura con log e traceback."""
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from classes.config import AMBER, CYAN, MUTE, RED, TEXT
from classes.utils import label


# ============================================================ console (sola lettura)
class ConsoleView(QWidget):
    """Mostra log, print e traceback del programma. Non accetta input: è solo per guardare."""
    COLORS = {"DEBUG": MUTE, "INFO": TEXT, "WARNING": AMBER, "ERROR": RED, "CRITICAL": RED, "TX": CYAN, "RX": MUTE}

    def __init__(self, bus, robot):
        super().__init__()
        self.paused, self.pending = False, []
        l = QVBoxLayout(self); l.setContentsMargins(0, 0, 0, 0); l.setSpacing(8)
        bar = QHBoxLayout(); bar.setSpacing(12)
        self.chk_traffic = QCheckBox("Traffico seriale (TX/RX)")
        self.btn_pause = QPushButton("Pausa"); self.btn_pause.setCheckable(True)
        self.btn_pause.setMinimumHeight(44); self.btn_pause.toggled.connect(self._set_paused)
        btn_clear = QPushButton("Pulisci"); btn_clear.setMinimumHeight(44); btn_clear.clicked.connect(self._clear)
        bar.addWidget(self.chk_traffic); bar.addStretch(1); bar.addWidget(self.btn_pause); bar.addWidget(btn_clear)
        l.addLayout(bar)
        self.view = QPlainTextEdit(); self.view.setObjectName("console")
        self.view.setReadOnly(True); self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.setMaximumBlockCount(2000)
        l.addWidget(self.view, 1)
        l.addWidget(label("Sola lettura: da qui non si possono inviare comandi al robot.", "note"))
        for level, text in bus.history:
            self._append(level, text)
        bus.line.connect(self._on_line)
        robot.traffic.connect(self._on_traffic)

    def _append(self, level, text):
        sb = self.view.verticalScrollBar()
        at_end = sb.value() >= sb.maximum() - 4          # se l'utente ha scorso in alto, non lo riportiamo giù
        cur = QTextCursor(self.view.document()); cur.movePosition(QTextCursor.End)
        fmt = QTextCharFormat(); fmt.setForeground(QColor(self.COLORS.get(level, TEXT)))
        if not self.view.document().isEmpty():
            cur.insertBlock()
        cur.insertText(text, fmt)
        if at_end:
            self.view.setTextCursor(cur); self.view.ensureCursorVisible()

    def _on_line(self, level, text):
        if self.paused:
            self.pending.append((level, text)); del self.pending[:-2000]
            self.btn_pause.setText(f"Riprendi ({len(self.pending)})")
        else:
            self._append(level, text)

    def _on_traffic(self, text):
        if self.chk_traffic.isChecked() and not self.paused:
            self._append(text[:2], text)

    def _set_paused(self, on):
        self.paused = on
        if not on:
            for level, text in self.pending:
                self._append(level, text)
            self.pending.clear()
            self.btn_pause.setText("Pausa")
        else:
            self.btn_pause.setText("Riprendi (0)")

    def _clear(self):
        self.view.clear(); self.pending.clear()
        if self.paused:
            self.btn_pause.setText("Riprendi (0)")
