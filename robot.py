"""Ponte verso il Raspberry Pi Pico (USB seriale) con modalità simulazione.
    QUESTO FILE è UN ESEMPIO,NON DOVREBBE ESSERE PRESO COME MODELLO
    LE CONNESSIONI VERRANNO DOPO LA CREAZIONE DEL ROBOT
Protocollo a righe di testo (vedi pico/main.py):
  V <sx> <dx>  /  S  /  R  /  L 3        ->  Pico
  T bat_mV d1 d2 d3 rotta velocità arresto modo encSx encDx   <-  Pico
"""
import logging
import math
import os
import secrets
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtSerialPort import QSerialPort

PICO_PORT = os.getenv("PICO_PORT", "/dev/ttyACM0")
OPERATOR_PIN = os.getenv("OPERATOR_PIN", "1234")
BAT_EMPTY_MV, BAT_FULL_MV = 30000, 42000   # pacco hoverboard 10S tipico: DA VERIFICARE
DRIVE_TIMEOUT = 0.4                         # s senza comandi -> stop
LINK_TIMEOUT = 2.0                          # s senza telemetria -> link perso
MODES = ("fermo", "movimento", "arresto")
log = logging.getLogger("albert.robot")


class Robot(QObject):
    stateChanged = Signal()      # telemetria aggiornata
    operatorChanged = Signal(bool)
    traffic = Signal(str)        # righe TX/RX grezze, per la console

    def __init__(self, simulate=False, parent=None):
        super().__init__(parent)
        self.sim = simulate
        self.operator = False
        self.state = dict(link=simulate, bat=100.0, dist=[400, 400, 400], hdg=0.0,
                          speed=0.0, estop=False, mode="fermo", enc=[0, 0])
        self._last_drive = 0.0
        self._last_rx = 0.0
        self._last_v = ""
        self._open_err = ""
        self._buf = b""
        # stato della simulazione
        self._cmd = (0, 0)
        self._latched = False

        self.port = QSerialPort(PICO_PORT, self)
        self.port.setBaudRate(115200)
        self.port.readyRead.connect(self._on_read)
        self.port.errorOccurred.connect(self._on_error)

        self._tick = QTimer(self, interval=100)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()
        self._reconnect = QTimer(self, interval=2000)
        self._reconnect.timeout.connect(self._try_open)
        if not simulate:
            self._reconnect.start()
            self._try_open()

    # ---------------------------------------------------------------- accesso
    def login(self, pin: str) -> bool:
        ok = secrets.compare_digest(str(pin), OPERATOR_PIN)
        self.operator = ok
        if ok:
            log.info("accesso operatore")
        else:
            log.warning("tentativo di accesso con PIN errato")
        self.operatorChanged.emit(ok)
        return ok

    def logout(self):
        log.info("uscita dalla modalità operatore")
        self.operator = False
        self.drive_raw(0, 0)
        self.operatorChanged.emit(False)

    # ---------------------------------------------------------------- comandi
    def drive(self, v, w):
        """Solo operatori. v: avanti/indietro, w: rotazione (positivo = destra), in [-1, 1]."""
        if self.operator:
            self.drive_raw(v, w)

    def drive_raw(self, v, w):
        v, w = max(-1, min(1, float(v))), max(-1, min(1, float(w)))
        left, right = v + w, v - w
        m = max(1.0, abs(left), abs(right))
        self._last_drive = time.monotonic()
        cmd = f"V {int(left / m * 100)} {int(right / m * 100)}"
        if cmd != self._last_v:                    # si registrano solo i cambi, non i 10 comandi al secondo
            self._last_v = cmd
            log.info("guida: %s", cmd[2:])
        self._send(cmd)

    def stop(self):            # sempre consentito: è un comando sicuro
        self._send("S")

    def rearm(self):           # solo operatori
        if self.operator:
            self._send("R")

    def led_talk(self):
        self._send("L 3")

    def _send(self, line):
        self.traffic.emit("TX " + line)
        if line[0] == "S":
            log.warning("STOP software")
        elif line[0] == "R":
            log.info("riarmo dopo lo STOP")
        elif line[0] == "L":
            log.debug("LED: sto parlando")
        if self.sim:
            self._sim_handle(line)
        elif self.port.isOpen():
            self.port.write((line + "\n").encode())
        elif line[0] != "V":
            log.error("comando %s NON inviato: Pico non collegato", line)

    # ---------------------------------------------------------------- seriale
    def _try_open(self):
        if self.port.isOpen():
            return
        if self.port.open(QSerialPort.ReadWrite):
            self.port.setDataTerminalReady(True)   # il CDC di MicroPython lo richiede
            self._last_rx = time.monotonic()
            self._buf = b""
            self._open_err = ""
            self.state["link"] = True
            log.info("Pico collegato su %s", PICO_PORT)
            self.stateChanged.emit()
        else:
            err = self.port.errorString()
            if err != self._open_err:              # un solo messaggio finché l'errore non cambia
                self._open_err = err
                log.warning("porta %s non apribile: %s", PICO_PORT, err)

    def _close(self):
        if self.port.isOpen():
            self.port.close()
        if self.state["link"]:
            log.warning("link col Pico perso")
            self.state["link"] = False
            self.stateChanged.emit()

    def _on_error(self, err):
        if err not in (QSerialPort.NoError,) and self.port.isOpen():
            self._close()

    def _on_read(self):
        self._buf += bytes(self.port.readAll())
        *lines, self._buf = self._buf.split(b"\n")
        for raw in lines:
            self._parse(raw.decode(errors="ignore").split())
        self._buf = self._buf[-512:]

    def _parse(self, f):
        # T bat_mv d1 d2 d3 hdg spd estop mode encL encR
        if not f:
            return
        if len(f) < 11 or f[0] != "T":
            log.info("pico: %s", " ".join(f))      # print/errori del firmware MicroPython
            return
        prev = (self.state["mode"], self.state["estop"])
        try:
            mv = int(f[1])
            pct = (mv - BAT_EMPTY_MV) / (BAT_FULL_MV - BAT_EMPTY_MV) * 100
            self.state.update(bat=max(0, min(100, pct)), dist=[int(f[2]), int(f[3]), int(f[4])],
                              hdg=float(f[5]), speed=float(f[6]), estop=f[7] == "1",
                              mode=MODES[int(f[8])], enc=[int(f[9]), int(f[10])])
        except (ValueError, IndexError):
            return
        self.traffic.emit("RX " + " ".join(f))
        self._log_changes(prev)
        self._last_rx = time.monotonic()
        self.stateChanged.emit()

    def _log_changes(self, prev):
        mode, estop = self.state["mode"], self.state["estop"]
        if mode != prev[0]:
            log.info("stato: %s → %s", prev[0], mode)
        if estop != prev[1]:
            log.warning("pulsante di emergenza fisico: %s", "PREMUTO" if estop else "rilasciato")

    # ---------------------------------------------------------------- timer
    def _on_tick(self):
        now = time.monotonic()
        # watchdog dei comandi di guida: nessun comando recente -> V 0 0
        if self._last_drive and now - self._last_drive > DRIVE_TIMEOUT:
            self._last_drive = 0
            if self._last_v != "V 0 0":
                log.warning("watchdog guida: comandi interrotti, fermo")
                self._last_v = "V 0 0"
            self._send("V 0 0")
        if self.sim:
            self._sim_step()
        elif self.port.isOpen() and now - self._last_rx > LINK_TIMEOUT:
            self._close()

    # ---------------------------------------------------------------- simulazione
    def _sim_handle(self, line):
        p = line.split()
        if p[0] == "V" and len(p) == 3 and not self._latched:
            self._cmd = (int(p[1]), int(p[2]))
        elif p[0] == "S":
            self._latched, self._cmd = True, (0, 0)
        elif p[0] == "R":
            self._latched = False

    def _sim_step(self):
        s, (l, r) = self.state, self._cmd
        prev = (s["mode"], s["estop"])
        s["speed"] = s["speed"] * 0.7 + (l + r) / 2 * 0.3
        s["hdg"] = (s["hdg"] + (l - r) / 2 * 0.05) % 360
        s["bat"] = max(5.0, s["bat"] - 0.003)
        t = time.time()
        s["dist"] = [int(120 + 70 * math.sin(t / 2.5 + i * 2)) for i in (1, 2, 3)]
        s["mode"] = "arresto" if self._latched else ("movimento" if abs(s["speed"]) > 1 else "fermo")
        self._log_changes(prev)
        self.traffic.emit("RX T %d %d %d %d %d %d %d %d %d %d" % (
            int(30000 + s["bat"] * 120), *s["dist"], int(s["hdg"]), int(s["speed"]), 0, MODES.index(s["mode"]), 0, 0))
        self.stateChanged.emit()

    def shutdown(self):
        log.info("chiusura: robot fermo")
        self.drive_raw(0, 0)
        self.stop()
        if self.port.isOpen():
            self.port.flush()
            self.port.close()
