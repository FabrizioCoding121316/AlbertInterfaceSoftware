"""Thread della telecamera: riconoscimento volti e direzione di movimento."""
import logging
import os
import pickle
from collections import deque
from datetime import datetime

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from classes.config import (DB_FILE, HISTORY, IMG_DIR, MAX_ENCODINGS, MOVE_PX, SCALE, TOLERANCE, ZOOM_PX)

try:                                    # riconoscimento facciale: facoltativo
    import cv2
    import face_recognition
    import numpy as np
    HAVE_VISION = True
except ImportError:
    HAVE_VISION = False

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
