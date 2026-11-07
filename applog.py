"""Log dell'applicazione: terminale/journalctl + vista Console dentro l'app (sola lettura).

Cattura: i messaggi di logging ("albert.*"), print() e traceback (stdout/stderr) e i messaggi interni di Qt.
"""
import logging
import sys
import time
from collections import deque

from PySide6.QtCore import QObject, QtMsgType, Signal, qInstallMessageHandler


class LogBus(QObject):
    line = Signal(str, str)                 # (livello, testo)

    def __init__(self):
        super().__init__()
        self.history = deque(maxlen=2000)   # chi apre la console dopo vede comunque le righe precedenti

    def push(self, level, text):
        self.history.append((level, text))
        self.line.emit(level, text)


class QtHandler(logging.Handler):
    def __init__(self, bus):
        super().__init__()
        self.bus = bus

    def emit(self, record):
        t = time.strftime("%H:%M:%S", time.localtime(record.created))
        self.bus.push(record.levelname, f"{t} [{record.name.split('.')[-1]}] {record.getMessage()}")


class Tee:
    """Sostituisce sys.stdout/sys.stderr: scrive sull'originale e manda le righe alla console."""

    def __init__(self, orig, level, bus):
        self.orig, self.level, self.bus, self.buf = orig, level, bus, ""

    def write(self, s):
        if self.orig:
            self.orig.write(s)
        self.buf += s
        *lines, self.buf = self.buf.split("\n")
        for ln in lines:
            if ln.strip():
                self.bus.push(self.level, ln.rstrip())
        return len(s)

    def flush(self):
        if self.orig:
            self.orig.flush()

    def isatty(self):
        return False

    def __getattr__(self, name):
        return getattr(self.orig, name)


def setup() -> LogBus:
    bus = LogBus()
    root = logging.getLogger("albert")
    root.setLevel(logging.DEBUG)
    root.propagate = False
    root.addHandler(QtHandler(bus))
    if sys.__stderr__:                      # terminale / tmux / journalctl
        sh = logging.StreamHandler(sys.__stderr__)
        sh.setLevel(logging.INFO)
        sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
        root.addHandler(sh)
    sys.stdout = Tee(sys.__stdout__, "INFO", bus)
    sys.stderr = Tee(sys.__stderr__, "ERROR", bus)

    qlog = logging.getLogger("albert.qt")
    levels = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
              QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
              QtMsgType.QtFatalMsg: logging.CRITICAL}
    qInstallMessageHandler(lambda mt, ctx, msg: qlog.log(levels.get(mt, logging.INFO), msg))
    return bus
