"""Caricamento dello stylesheet .qss con i colori da file .conf."""
import configparser
import re

from PySide6.QtWidgets import QApplication


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
