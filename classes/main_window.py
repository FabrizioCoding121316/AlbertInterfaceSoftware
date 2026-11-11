"""Finestra principale di Albert."""
import logging
import os

from PySide6.QtCore import QEvent, QRect, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLineEdit,
                               QPushButton, QScrollArea, QSizePolicy, QSlider, QStackedWidget,
                               QVBoxLayout, QWidget)

import assistant as asst
from robot import Robot
from classes.config import (AMBER, CYAN, DB_FILE, GREEN, IMG_DIR, LABS, MUTE, PANEL, P_CHAT, P_CTL, P_HOME,
                    P_LABS, P_ROBOT, RED)
from classes.utils import label
from classes.face_widget import Face
from classes.console_view import ConsoleView
from classes.face_worker import FaceWorker, HAVE_VISION

try:
    from PySide6.QtTextToSpeech import QTextToSpeech
except ImportError:                     # la sintesi vocale è facoltativa
    QTextToSpeech = None


# ============================================================ finestra principale
class MainWindow(QWidget):
    TIMEOUT_MS = 10_000 #10 secondi
    #             ^
    #          Secondi

    #Eventi di interazione
    EVENT_INTPUT = {QEvent.Type.MouseButtonPress,QEvent.Type.MouseButtonDblClick,QEvent.Type.Wheel,QEvent.Type.KeyPress,QEvent.Type.TouchBegin}
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
        #Questa parte del codice servirebbe per mettere u timer per rimettetre la schermata del robot
        self.timer_inpt = QTimer(self)
        self.timer_inpt.setSingleShot(True)
        self.timer_inpt.setInterval(self.TIMEOUT_MS)
        self.timer_inpt.timeout.connect(self._on_inactive)
        self.timer_inpt.start()
        # Filtro eventi su tutta l'applicazione
        QApplication.instance().installEventFilter(self)

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

    def _build_home(self) -> QWidget:
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

    def _build_face_only(self) -> QWidget:
        w = QWidget(); l = QHBoxLayout(w); l.setContentsMargins(28, 22, 28, 22)
        f = self._new_face()
        l.addWidget(f, 1)
        self._face_only_page = w
        #w.installEventFilter(self)
        #f.installEventFilter(self)
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

    def _on_inactive(self):
        logging.getLogger("albert.ui").info("Time out finito")
        if self.hold.isActive():
            self.timer_inpt.start()
            return
        if self.stack.currentIndex() != P_ROBOT:
            self.go(P_ROBOT)
        

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
        ev_type = ev.type()
        if ev_type in self.EVENT_INTPUT:
            self.timer_inpt.start()
        if (ev_type == QEvent.Type.MouseButtonPress
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
