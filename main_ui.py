#!/usr/bin/env python3
"""Albert Assistant – versione Qt (PySide6) per il Raspberry Pi 5.

Un unico programma al posto di server.py + interfaccia web:
  - interfaccia touch a schermo intero (Benvenuto / Laboratori / Assistente / Controllo);
  - parla direttamente col Pico via USB seriale (pico/main.py resta invariato);
  - assistente: FAQ + Claude se c'è ANTHROPIC_API_KEY.

Avvio:   OPERATOR_PIN=4821 python3 main.py [--kiosk] [--sim]
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
import sys

from PySide6.QtWidgets import QApplication

import applog
from robot import OPERATOR_PIN, Robot
from classes.config import BASE_DIR
from classes.main_window import MainWindow
from classes.style import carica_stile


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
