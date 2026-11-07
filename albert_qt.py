#!/usr/bin/env python3
"""Albert Assistant – versione Qt (PySide6) per il Raspberry Pi 5.

Un unico programma al posto di server.py + interfaccia web:
  - interfaccia touch a schermo intero (Benvenuto / Laboratori / Assistente / Controllo);
  - parla direttamente col Pico via USB seriale (pico/main.py resta invariato);
  - assistente: FAQ + Claude se c'è ANTHROPIC_API_KEY.

Avvio:   OPERATOR_PIN=4821 python3 albert_qt.py [--kiosk] [--sim]
  --sim     nessun Pico: robot simulato (PIN operatore da OPERATOR_PIN, default 1234)
  --kiosk   schermo intero senza cursore
  --no-camera        non avvia la telecamera (gli occhi seguono il cursore)
  --no-salva-volti   riconosce i volti solo in memoria: niente database né foto su disco

ATTENZIONE – dati biometrici: il riconoscimento facciale salva i volti delle persone (foto in faces_img/ e
codifiche in faces_database.pkl). Dopo le prove cancella la cartella e il file. Con il pubblico (soprattutto
minorenni) servono informativa e consenso: in quel caso avvia con --no-salva-volti.

Sicurezza (come nella versione web):
  - i visitatori non guidano: solo gli operatori con PIN;  STOP è sempre accettato;
  - l'assistente AI non conosce il robot;  il Pico applica da solo limiti, watchdog e stop ostacoli;
  - l'arresto di emergenza fisico resta indipendente da questo software.
"""
import logging
import os
import pickle
import random
import sys
import re
import configparser
from collections import deque
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import QPointF, Qt, QThread, QTimer, Signal, QRectF,QRect, QEvent
from PySide6.QtGui import (QColor, QCursor, QImage, QKeyEvent, QPainter, QPainterPath, QPen, QTextCharFormat,
                           QTextCursor)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
                               QSlider, QStackedWidget, QVBoxLayout, QWidget)

import applog
import assistant as asst
from robot import OPERATOR_PIN, Robot

try:
    from PySide6.QtTextToSpeech import QTextToSpeech
except ImportError:                     # la sintesi vocale è facoltativa
    QTextToSpeech = None

try:                                    # riconoscimento facciale: facoltativo
    import cv2
    import face_recognition
    import numpy as np
    HAVE_VISION = True
except ImportError:
    HAVE_VISION = False

BG, PANEL, PANEL2, LINE = "#0c1a2e", "#152a45", "#1f3a5d", "#2a4a72"
CYAN, AMBER, RED, GREEN, TEXT, MUTE = "#3ed6d6", "#ffb04d", "#ff5d5d", "#4fd69c", "#eef4fb", "#9db3cc"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "faces_database.pkl")
IMG_DIR = os.path.join(BASE_DIR, "faces_img")
TOLERANCE = 0.6       # più basso = riconoscimento più severo
MAX_ENCODINGS = 10    # codifiche conservate per persona
SCALE = 4             # il frame è ridotto di 4 volte prima dell'analisi
HISTORY = 8           # campioni usati per stimare la direzione
MOVE_PX = 40          # spostamento minimo (px) per destra/sinistra/su/giù
ZOOM_PX = 20          # variazione minima di larghezza per "si avvicina/si allontana"
P_ROBOT, P_HOME, P_LABS, P_CHAT, P_CTL = range(5)    # pagine dello stack

LABS = [
    ("robotica", "Laboratorio di robotica", "Dove è nato Albert: robot, sensori e prove di guida."),
    ("elettronica", "Laboratorio di elettronica", "Circuiti, schede di controllo e misure."),
    ("informatica", "Laboratorio di informatica", "Programmazione, reti e intelligenza artificiale."),
    ("scienze", "Laboratorio di scienze", "Esperimenti per il liceo scienze applicate."),
    ("ingresso", "Ingresso e segreteria", "Informazioni e iscrizioni."),
]

BASE_DIR = Path(__file__).resolve().parent


def label(text="", name=None, wrap=False):
    l = QLabel(text)
    if name:
        l.setObjectName(name)
    l.setWordWrap(wrap)
    return l


# ============================================================ rilevatore di volti (telecamera)
log_vision = logging.getLogger("albert.vision")


class FaceWorker(QThread):
    """Thread che legge la telecamera, riconosce i volti e stima verso dove si muove ciascuno."""
    frame_ready = Signal(QImage)                   # frame già disegnato (solo se want_frames = True)
    person_seen = Signal(int, str, bool)           # id, nome, è_nuova
    direction_changed = Signal(int, str, str)      # id, nome, direzione (solo quando cambia)
    person_position = Signal(float, float, bool)   # x, y in [-1, 1] (0,0 = centro), c'è_qualcuno

    def __init__(self, camera_index=0, save_faces=True):
        super().__init__()
        self.camera_index = camera_index
        self.save_faces = save_faces
        self.want_frames = False   # l'anteprima non è mostrata: non si spreca CPU a convertire i frame
        self._running = True
        self._present = False
        self.db = self._load_db()
        self.tracks = {}           # id -> deque di (cx, cy, larghezza)
        self.last_direction = {}   # id -> ultima direzione emessa

    # ---------- database ----------
    def _load_db(self):
        if self.save_faces and os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, "rb") as f:
                    db = pickle.load(f)
                log_vision.info("database volti caricato: %d persone", len(db))
                return db
            except Exception as e:
                log_vision.error("database volti illeggibile (%s): riparto vuoto", e)
        return {}

    def _save_db(self):
        if self.save_faces:
            with open(DB_FILE, "wb") as f:
                pickle.dump(self.db, f)

    def _next_id(self):
        return max(self.db.keys(), default=0) + 1

    def _find_match(self, encoding):
        best_pid, best_dist = None, 1.0
        for pid, data in self.db.items():
            dists = face_recognition.face_distance(data["encodings"], encoding)
            d = float(np.min(dists))
            if d <= TOLERANCE and d < best_dist:
                best_pid, best_dist = pid, d
        if best_pid is None:
            return None, None
        return best_pid, self.db[best_pid]["name"]

    def _save_new_face(self, pid, encoding, frame, box):
        top, right, bottom, left = [v * SCALE for v in box]
        name = f"Persona #{pid}"
        self.db[pid] = {"name": name, "encodings": [encoding], "first_seen": datetime.now().isoformat()}
        if self.save_faces:
            crop = frame[max(top, 0):bottom, max(left, 0):right]
            if crop.size:
                os.makedirs(IMG_DIR, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                cv2.imwrite(os.path.join(IMG_DIR, f"{name}_{stamp}.jpg"), crop)
            self._save_db()
        return name

    # ---------- direzione ----------
    def _update_direction(self, pid, box):
        """Confronta la posizione più vecchia e la più recente del volto di questa persona.
        Destra/sinistra/su/giù sono riferiti all'immagine così com'è mostrata."""
        top, right, bottom, left = [v * SCALE for v in box]
        cx, cy, w = (left + right) / 2, (top + bottom) / 2, right - left

        hist = self.tracks.setdefault(pid, deque(maxlen=HISTORY))
        hist.append((cx, cy, w))
        if len(hist) < HISTORY:
            return "fermo"

        dx = hist[-1][0] - hist[0][0]
        dy = hist[-1][1] - hist[0][1]
        dw = hist[-1][2] - hist[0][2]

        parti = []
        if dx > MOVE_PX:
            parti.append("destra")
        elif dx < -MOVE_PX:
            parti.append("sinistra")
        if dy > MOVE_PX:
            parti.append("giu")
        elif dy < -MOVE_PX:
            parti.append("su")
        if dw > ZOOM_PX:
            parti.append("si avvicina")
        elif dw < -ZOOM_PX:
            parti.append("si allontana")
        return " + ".join(parti) or "fermo"

    # ---------- loop ----------
    def run(self):
        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            log_vision.error("telecamera %s non apribile: riconoscimento volti disattivato", self.camera_index)
            return
        log_vision.info("telecamera %s aperta", self.camera_index)
        process_this_frame = True
        last_results = []   # [(box, testo)]
        fails = 0

        while self._running:
            ok, frame = cap.read()
            if not ok:
                fails += 1
                if fails == 100:
                    log_vision.warning("la telecamera non restituisce immagini")
                self.msleep(10)
                continue
            fails = 0

            if process_this_frame:
                small = cv2.resize(frame, (0, 0), fx=1 / SCALE, fy=1 / SCALE)
                rgb_small = np.ascontiguousarray(small[:, :, ::-1])

                locations = face_recognition.face_locations(rgb_small)
                encodings = face_recognition.face_encodings(rgb_small, locations)

                # posizione del volto più grande (la persona più vicina), normalizzata in [-1, 1]
                if locations:
                    top, right, bottom, left = max(locations, key=lambda f: (f[2] - f[0]) * (f[1] - f[3]))
                    sh, sw = small.shape[:2]
                    nx = ((left + right) / 2) / sw * 2 - 1
                    ny = ((top + bottom) / 2) / sh * 2 - 1
                    self.person_position.emit(nx, ny, True)
                else:
                    self.person_position.emit(0.0, 0.0, False)
                if bool(locations) != self._present:           # si registra solo quando cambia
                    self._present = bool(locations)
                    log_vision.info("volto in vista" if self._present else "nessun volto in vista")

                last_results = []
                visti = set()
                for box, enc in zip(locations, encodings):
                    pid, name = self._find_match(enc)
                    is_new = name is None

                    if is_new:
                        pid = self._next_id()
                        name = self._save_new_face(pid, enc, frame, box)
                        log_vision.info("nuova persona: %s", name)
                    elif len(self.db[pid]["encodings"]) < MAX_ENCODINGS:
                        self.db[pid]["encodings"].append(enc)

                    direzione = self._update_direction(pid, box)
                    visti.add(pid)

                    self.person_seen.emit(pid, name, is_new)
                    if self.last_direction.get(pid) != direzione:
                        self.last_direction[pid] = direzione
                        self.direction_changed.emit(pid, name, direzione)
                        log_vision.info("%s: %s", name, direzione)

                    last_results.append((box, f"{name} - {direzione}"))

                # chi non si vede più perde la cronologia
                for pid in list(self.tracks):
                    if pid not in visti:
                        del self.tracks[pid]
                        self.last_direction.pop(pid, None)

            process_this_frame = not process_this_frame

            if self.want_frames:
                for (top, right, bottom, left), testo in last_results:
                    cv2.rectangle(frame, (left * SCALE, top * SCALE), (right * SCALE, bottom * SCALE), (0, 255, 0), 2)
                    cv2.putText(frame, testo, (left * SCALE, top * SCALE - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                self.frame_ready.emit(QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy())

        cap.release()
        self._save_db()
        log_vision.info("telecamera chiusa")

    def stop(self):
        self._running = False
        self.wait(5000)


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


# ============================================================ finestra principale
class MainWindow(QWidget):
    def __init__(self, robot: Robot, bus, kiosk=False, camera=True, save_faces=True):
        super().__init__()
        self.bus = bus
        self.faces = []            # tutti i volti di Albert (pagina Robot + Benvenuto)
        self.worker = None
        self.setObjectName("root")
        self.setWindowTitle("Albert – Einstein Lab Robotics")
        self.robot = robot
        self.pin = ""
        self.pin_locked = False
        self.hold = QTimer(self, interval=100)      # ripete il comando di guida finché si tiene premuto
        self.hold.timeout.connect(self._repeat_drive)
        self.hold_cmd = (0, 0)
        self.assistant = asst.Assistant(self)
        self.assistant.answered.connect(self._on_answer)
        self.tts = None
        if QTextToSpeech is not None:
            try:
                self.tts = QTextToSpeech(self)
                from PySide6.QtCore import QLocale
                self.tts.setLocale(QLocale("it_IT"))
            except Exception:
                self.tts = None

        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(self._build_header())
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_face_only())    # P_ROBOT
        self.stack.addWidget(self._build_home())         # P_HOME
        self.stack.addWidget(self._build_labs())         # P_LABS
        self.stack.addWidget(self._build_chat())         # P_CHAT
        self.stack.addWidget(self._build_ctl())          # P_CTL
        root.addWidget(self.stack, 1)
        self.nav = self._build_nav()
        root.addWidget(self.nav)

        robot.stateChanged.connect(self.refresh)
        robot.operatorChanged.connect(lambda _: self.refresh())
        if kiosk:
            self.setCursor(Qt.BlankCursor)
        for b in self.findChildren(QPushButton):
            b.setFocusPolicy(Qt.NoFocus)
        self.setFocusPolicy(Qt.StrongFocus)
        self._start_vision(camera, save_faces)
        self.go(P_ROBOT)
        self.refresh()

    # ---------------------------------------------------------- costruzione
    def _build_header(self):
        h = QFrame(); h.setObjectName("header")
        l = QHBoxLayout(h); l.setContentsMargins(20, 10, 20, 10); l.setSpacing(16)
        l.addWidget(label("Albert", "brand"))
        l.addWidget(label("Einstein Lab Robotics", "brandSmall"))
        l.addStretch(1)
        self.lbl_link, self.lbl_bat = label("", "chip"), label("Batteria –", "chip")
        l.addWidget(self.lbl_link); l.addWidget(self.lbl_bat)
        return h

    def _build_nav(self):
        n = QFrame(); n.setObjectName("nav")
        l = QHBoxLayout(n); l.setContentsMargins(16, 10, 16, 12); l.setSpacing(10)
        self.nav_group = QButtonGroup(self); self.nav_group.setExclusive(True)
        for i, name in enumerate(("Robot", "Benvenuto", "Laboratori", "Assistente", "Controllo")):
            b = QPushButton(name); b.setObjectName("navbtn"); b.setCheckable(True)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.clicked.connect(lambda _=False, i=i: self.go(i))
            self.nav_group.addButton(b, i); l.addWidget(b)
        return n

    def _build_home(self):
        w = QWidget(); l = QHBoxLayout(w); l.setContentsMargins(28, 22, 28, 22); l.setSpacing(28)
        l.addWidget(self._new_face(), 1)
        col = QVBoxLayout(); col.setSpacing(12); col.addStretch(1)
        col.addWidget(label("Ciao, sono Albert.", "h1", True))
        col.addWidget(label("Ti accompagno nella scuola e rispondo alle tue domande. Da dove vuoi iniziare?", "lead", True))
        col.addSpacing(10)
        for text, dest, main in (("Scopri i laboratori", P_LABS, True), ("Parla con me", P_CHAT, False),
                                 ("Accompagnami in una visita", P_LABS, False)):
            b = QPushButton(text); b.setMinimumHeight(64); b.setMaximumWidth(440)
            if main:
                b.setObjectName("main")
            b.clicked.connect(lambda _=False, d=dest: self.go(d))
            col.addWidget(b)
        col.addStretch(1)
        l.addLayout(col, 1)
        return w

    def _new_face(self):
        f = Face()
        self.faces.append(f)
        return f

    def _build_face_only(self):
        w = QWidget(); l = QHBoxLayout(w); l.setContentsMargins(28, 22, 28, 22)
        f = self._new_face()
        l.addWidget(f, 1)
        self._face_only_page = w
        w.installEventFilter(self)
        f.installEventFilter(self)
        return w

    def _start_vision(self, camera, save_faces):
        log = logging.getLogger("albert.vision")
        if not camera:
            log.info("telecamera disattivata (--no-camera): gli occhi seguono il cursore")
            return
        if not HAVE_VISION:
            log.warning("opencv/face_recognition non installati: gli occhi seguono il cursore")
            return
        if save_faces:
            log.warning("salvataggio volti ATTIVO: foto in %s, database in %s (cancellali dopo le prove)", IMG_DIR, DB_FILE)
        self.worker = FaceWorker(int(os.getenv("ALBERT_CAMERA", "0")), save_faces)
        self.worker.person_position.connect(self._on_person)
        self.worker.finished.connect(self._vision_stopped)
        for f in self.faces:
            f.follow_mouse = False
        self.worker.start()

    def _on_person(self, nx, ny, presente):
        for f in self.faces:
            f.set_person(nx, ny, presente)

    def _vision_stopped(self):          # telecamera non apribile o thread terminato: si torna al cursore
        for f in self.faces:
            f.follow_mouse = True

    def stop_vision(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()

    def _build_labs(self):
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(28, 22, 28, 12); l.setSpacing(8)
        l.addWidget(label("Dove vuoi andare?", "h2"))
        self.lbl_goto = label("", "goto", True)
        l.addWidget(self.lbl_goto)
        area = QScrollArea(); area.setWidgetResizable(True); area.setFrameShape(QFrame.NoFrame)
        inner = QWidget(); grid = QGridLayout(inner); grid.setSpacing(12); grid.setContentsMargins(0, 0, 8, 0)
        colors = (AMBER, GREEN, CYAN)
        for i, (lid, name, desc) in enumerate(LABS):
            card = QFrame(); card.setObjectName("card")
            card.setStyleSheet(f"QFrame#card{{border-left:6px solid {colors[i % 3]};}}")
            cl = QVBoxLayout(card); cl.setContentsMargins(20, 16, 20, 16); cl.setSpacing(6)
            t = label(name); t.setStyleSheet("font-size:20px;font-weight:700;")
            d = label(desc, wrap=True); d.setStyleSheet(f"color:{MUTE};font-size:16px;")
            b = QPushButton("Accompagnami qui"); b.setObjectName("lab")
            b.clicked.connect(lambda _=False, lid=lid, name=name: self._goto(lid, name))
            cl.addWidget(t); cl.addWidget(d); cl.addWidget(b, 0, Qt.AlignLeft)
            grid.addWidget(card, i // 2, i % 2)
        grid.setRowStretch(len(LABS) // 2 + 1, 1)
        area.setWidget(inner)
        l.addWidget(area, 1)
        return w

    def _build_chat(self):
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(28, 22, 28, 12); l.setSpacing(12)
        self.log_area = QScrollArea(); self.log_area.setWidgetResizable(True); self.log_area.setFrameShape(QFrame.NoFrame)
        inner = QWidget(); self.log = QVBoxLayout(inner); self.log.setSpacing(10); self.log.addStretch(1)
        self.log_area.setWidget(inner)
        self.log_area.verticalScrollBar().rangeChanged.connect(
            lambda _a, mx: self.log_area.verticalScrollBar().setValue(mx))
        l.addWidget(self.log_area, 1)
        chips = QHBoxLayout(); chips.setSpacing(10)
        for q in ("Quali indirizzi ci sono?", "Come funziona l’Open Day?", "Chi sei?"):
            b = QPushButton(q); b.setObjectName("chipbtn")
            b.clicked.connect(lambda _=False, q=q: self.ask(q))
            chips.addWidget(b)
        chips.addStretch(1)
        l.addLayout(chips)
        row = QHBoxLayout(); row.setSpacing(10)
        self.chat_in = QLineEdit(); self.chat_in.setPlaceholderText("Scrivi una domanda"); self.chat_in.setMaxLength(300)
        self.chat_in.returnPressed.connect(lambda: self.ask(self.chat_in.text(), clear=True))
        self.btn_send = QPushButton("Invia"); self.btn_send.setObjectName("send")
        self.btn_send.clicked.connect(lambda: self.ask(self.chat_in.text(), clear=True))
        row.addWidget(self.chat_in, 1); row.addWidget(self.btn_send)
        l.addLayout(row)
        l.addWidget(label("Albert risponde alle domande ma non può muovere il robot.", "note"))
        self._chat_started = False
        return w

    def _build_ctl(self):
        w = QWidget(); self.ctl_lay = QVBoxLayout(w); self.ctl_lay.setContentsMargins(28, 16, 28, 12)
        # --- PIN
        self.gate = QWidget(); g = QVBoxLayout(self.gate); g.setAlignment(Qt.AlignCenter); g.setSpacing(8)
        g.addWidget(label("Area riservata agli operatori", "h2"), 0, Qt.AlignCenter)
        g.addWidget(label("Inserisci il codice per usare il controllo manuale.", "lead"), 0, Qt.AlignCenter)
        self.lbl_dots = label("○ ○ ○ ○"); self.lbl_dots.setStyleSheet("font-size:36px;letter-spacing:8px;")
        self.lbl_dots.setAlignment(Qt.AlignCenter); self.lbl_dots.setMinimumHeight(60)
        g.addWidget(self.lbl_dots)
        pad = QGridLayout(); pad.setSpacing(10)
        for i, k in enumerate("123456789C0⌫"):
            b = QPushButton(k); b.setObjectName("pad"); b.setFixedSize(76, 66)
            b.clicked.connect(lambda _=False, k=k: self._pin_key(k))
            pad.addWidget(b, i // 3, i % 3)
        pw = QWidget(); pw.setLayout(pad)
        g.addWidget(pw, 0, Qt.AlignCenter)
        # --- pannello operatore
        drive_page = QWidget(); p = QHBoxLayout(drive_page); p.setSpacing(32); p.setContentsMargins(0, 0, 0, 0)
        dp = QGridLayout(); dp.setSpacing(10)
        for txt, v, wv, r, c in (("▲", 1, 0, 0, 1), ("◀", 0, -1, 1, 0), ("▼", -1, 0, 1, 1), ("▶", 0, 1, 1, 2)):
            b = QPushButton(txt); b.setObjectName("dp"); b.setFixedSize(92, 92)
            b.pressed.connect(lambda v=v, wv=wv: self._drive_press(v, wv))
            b.released.connect(self._drive_release)
            dp.addWidget(b, r, c)
        dw = QWidget(); dw.setLayout(dp)
        p.addWidget(dw, 0, Qt.AlignCenter)
        info = QVBoxLayout(); info.setSpacing(10)
        self.stats = {}
        for key, name in (("mode", "Stato"), ("speed", "Velocità"), ("hdg", "Direzione"), ("es", "Pulsante di emergenza fisico")):
            f = QFrame(); f.setObjectName("stat"); r = QHBoxLayout(f); r.setContentsMargins(18, 8, 18, 8)
            v = label("–"); v.setStyleSheet("font-weight:800;")
            r.addWidget(label(name)); r.addStretch(1); r.addWidget(v)
            self.stats[key] = v; info.addWidget(f)
        self.bars = []
        for name in ("Sinistra", "Centro", "Destra"):
            r = QHBoxLayout(); r.setSpacing(10)
            n = label(name, "note"); n.setFixedWidth(70)
            bar = QFrame(); bar.setFixedHeight(14); bar.setStyleSheet(f"background:{PANEL};border-radius:7px;")
            fill = QFrame(bar); fill.setGeometry(0, 0, 0, 14)
            val = label("", "note"); val.setFixedWidth(64)
            r.addWidget(n); r.addWidget(bar, 1); r.addWidget(val)
            info.addLayout(r); self.bars.append((bar, fill, val))
        r = QHBoxLayout(); r.addWidget(label("Velocità"))
        self.slider = QSlider(Qt.Horizontal); self.slider.setRange(10, 100); self.slider.setValue(50)
        self.slider.setMinimumHeight(40)
        r.addWidget(self.slider, 1); info.addLayout(r)
        r = QHBoxLayout()
        self.btn_rearm = QPushButton("Riarma dopo l’arresto"); self.btn_rearm.clicked.connect(self.robot.rearm)
        out = QPushButton("Esci dalla modalità operatore"); out.clicked.connect(self._logout)
        r.addWidget(self.btn_rearm); r.addWidget(out); info.addLayout(r)
        info.addWidget(label("STOP ferma il robot via software. L’arresto di emergenza fisico resta indipendente dall’interfaccia.", "warn", True))
        info.addStretch(1)
        p.addLayout(info, 1)
        # --- operatore: due viste, "Guida" e "Console" (sola lettura)
        self.panel = QWidget(); pl = QVBoxLayout(self.panel); pl.setContentsMargins(0, 0, 0, 0); pl.setSpacing(10)
        self.ctl_pages = QStackedWidget()
        self.ctl_pages.addWidget(drive_page)
        self.console = ConsoleView(self.bus, self.robot)
        self.ctl_pages.addWidget(self.console)
        seg = QHBoxLayout(); seg.setSpacing(8)
        grp = QButtonGroup(self); grp.setExclusive(True)
        for i, name in enumerate(("Guida", "Console")):
            b = QPushButton(name); b.setObjectName("seg"); b.setCheckable(True); b.setChecked(i == 0)
            b.clicked.connect(lambda _=False, i=i: self._ctl_page(i))
            grp.addButton(b, i); seg.addWidget(b)
        seg.addStretch(1)
        pl.addLayout(seg); pl.addWidget(self.ctl_pages, 1)
        self.ctl_lay.addWidget(self.gate); self.ctl_lay.addWidget(self.panel)
        return w

    def _ctl_page(self, i):
        self._drive_release()
        self.ctl_pages.setCurrentIndex(i)

    def eventFilter(self, obj, ev):
        if (ev.type() == QEvent.MouseButtonPress
                and self.stack.currentIndex() == P_ROBOT
                and (obj is self._face_only_page or obj.parent() is self._face_only_page)):
            self.go(P_HOME)
            return True
        return super().eventFilter(obj, ev)

    # ---------------------------------------------------------- navigazione
    def go(self, i):
        self._drive_release()
        self.stack.setCurrentIndex(i)
        self.nav_group.button(i).setChecked(True)
        self.nav.setVisible(i != P_ROBOT)
        if i == P_CHAT and not self._chat_started:
            self._chat_started = True
            start_text= "Chiedimi degli indirizzi di studio,dell’Open Day o di me."
            self._on_answer(start_text)
        self.refresh()

    # ---------------------------------------------------------- laboratori / chat
    def _goto(self, lid, name):
        ok, msg = asst.navigate_to(lid)      # aggancio per Nav2: per ora non muove il robot
        self.lbl_goto.setText(msg)

    def _add_msg(self, who, text):
        m = label(text, who, True)
        m.setTextInteractionFlags(Qt.TextSelectableByMouse)
        m.ensurePolished()                      # applica lo stylesheet, così il font è quello vero
        padx, pady = 40, 28                     # padding orizzontale e verticale totale dello stylesheet: regolali
        maxw = max(300, int(self.log_area.viewport().width() * 0.7))
        fm = m.fontMetrics()
        # larghezza: stretta per testi corti, massimo 70% per quelli lunghi
        w = min(maxw, fm.horizontalAdvance(text) + padx)
        # altezza: misura il testo con il word wrap alla larghezza utile
        r = fm.boundingRect(QRect(0, 0, w - padx, 100000), Qt.TextWordWrap, text)
        h = r.height() + pady
        m.setFixedSize(w, h)
        self.log.insertWidget(self.log.count() - 1, m, 0, Qt.AlignRight if who == "me" else Qt.AlignLeft)

    def ask(self, text, clear=False):
        text = text.strip()
        if not text or not self.btn_send.isEnabled():
            return
        if clear:
            self.chat_in.clear()
        self._add_msg("me", text)
        self.btn_send.setEnabled(False)       # una domanda alla volta
        self.assistant.ask(text)

    def _on_answer(self, text):
        self._add_msg("bot", text)
        self.btn_send.setEnabled(True)
        self.robot.led_talk()
        if self.tts:
            self.tts.stop(); self.tts.say(text)

    # ---------------------------------------------------------- controllo operatori
    def _pin_key(self, k):
        if self.pin_locked:
            return
        if k == "C":
            self.pin = ""
        elif k == "⌫":
            self.pin = self.pin[:-1]
        else:
            self.pin += k
        if len(self.pin) >= 4:
            ok = self.robot.login(self.pin)
            self.pin = ""
            if not ok:
                self.pin_locked = True                       # rallenta i tentativi
                self.lbl_dots.setText("✕ ✕ ✕ ✕"); self.lbl_dots.setStyleSheet(f"font-size:36px;letter-spacing:8px;color:{RED};")
                QTimer.singleShot(1000, self._pin_unlock)
        self.refresh()

    def _pin_unlock(self):
        self.pin_locked = False
        self.lbl_dots.setStyleSheet("font-size:36px;letter-spacing:8px;")
        self.refresh()

    def _logout(self):
        self._drive_release()
        self.robot.logout()

    def _drive_press(self, v, w):
        self.hold_cmd = (v, w)
        self._repeat_drive()
        self.hold.start()

    def _repeat_drive(self):
        k = self.slider.value() / 100
        self.robot.drive(self.hold_cmd[0] * k, self.hold_cmd[1] * k)

    def _drive_release(self):
        if self.hold.isActive():
            self.hold.stop()
            self.robot.drive(0, 0)

    def _on_stop(self):
        self._drive_release()
        self.robot.stop()

    KEYS = {Qt.Key_Up: (1, 0), Qt.Key_Down: (-1, 0), Qt.Key_Left: (0, -1), Qt.Key_Right: (0, 1)}

    def keyPressEvent(self, e: QKeyEvent):
        if (e.key() in self.KEYS and not e.isAutoRepeat() and self.stack.currentIndex() == P_CTL
                and self.robot.operator and self.ctl_pages.currentIndex() == 0):
            self._drive_press(*self.KEYS[e.key()])
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e: QKeyEvent):
        if e.key() in self.KEYS and not e.isAutoRepeat():
            self._drive_release()
        else:
            super().keyReleaseEvent(e)

    # ---------------------------------------------------------- aggiornamento interfaccia
    def refresh(self):
        s, r = self.robot.state, self.robot
        if r.sim:
            txt, ok = "Simulazione", False
        else:
            txt, ok = ("Collegato al robot", True) if s["link"] else ("Robot non collegato", False)
        self.lbl_link.setText("● " + txt)
        self.lbl_link.setStyleSheet(f"color:{GREEN if ok else AMBER};")
        self.lbl_bat.setText(f"Batteria {round(s['bat'])}%")
        for f in self.faces:
            f.set_mode(s["mode"])
        op = r.operator
        self.gate.setVisible(not op); self.panel.setVisible(op)
        if not self.pin_locked:
            self.lbl_dots.setText("●" * len(self.pin) if self.pin else "○ ○ ○ ○")
        self.stats["mode"].setText(s["mode"].capitalize())
        self.stats["speed"].setText(f"{round(abs(s['speed']))}%")
        self.stats["hdg"].setText(f"{round(s['hdg'])}°")
        self.stats["es"].setText("Premuto" if s["estop"] else "Libero")
        self.btn_rearm.setVisible(s["mode"] == "arresto")
        if op and self.stack.currentIndex() == P_CTL:
            for d, (bar, fill, val) in zip(s["dist"], self.bars):
                color = RED if d < 30 else AMBER if d < 80 else GREEN
                fill.setGeometry(0, 0, int(bar.width() * min(100, d / 2) / 100), 14)
                if fill.property("c") != color:
                    fill.setProperty("c", color)
                    fill.setStyleSheet(f"background:{color};border-radius:7px;")
                val.setText(f"{d} cm")

    def closeEvent(self, e):
        self.stop_vision()
        self.robot.shutdown()      # nessuno al controllo: il robot sta fermo
        super().closeEvent(e)


def carica_stile(app: QApplication, qss_path: str, conf_path: str) -> None:
    conf = configparser.ConfigParser()
    conf.read(conf_path, encoding="utf-8")
    colori = dict(conf["colors"]) if "colors" in conf else {}

    with open(qss_path, encoding="utf-8") as f:
        qss = f.read()

    hex_ok = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

    def sostituisci(m: re.Match) -> str:
        nome = m.group(1)
        valore = colori.get(nome)
        if valore is None or not hex_ok.match(valore):
            return m.group(0)
        return valore

    qss = re.sub(r"@([A-Za-z_][A-Za-z0-9_]*)", sostituisci, qss)
    app.setStyleSheet(qss)

def main():
    app = QApplication(sys.argv)
    carica_stile(app,(BASE_DIR/"styles/main_style.qss"),(BASE_DIR/"styles/themecolor.conf"))
    bus = applog.setup()
    log = logging.getLogger("albert.app")
    if "--sim" in sys.argv:
        log.info("modalità simulazione: nessun Pico collegato")
    if OPERATOR_PIN == "1234":
        log.warning("PIN operatore di default: imposta OPERATOR_PIN prima di usare il robot con il pubblico")
    robot = Robot(simulate="--sim" in sys.argv)
    win = MainWindow(robot, bus, kiosk="--kiosk" in sys.argv, camera="--no-camera" not in sys.argv,
                     save_faces="--no-salva-volti" not in sys.argv)
    app.aboutToQuit.connect(win.stop_vision)
    app.aboutToQuit.connect(robot.shutdown)
    if "--kiosk" in sys.argv:
        win.showFullScreen()
    else:
        win.resize(1100, 680); win.show()
    log.info("interfaccia avviata")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()