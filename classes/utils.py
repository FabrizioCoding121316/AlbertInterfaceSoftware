"""Funzioni di utilità per l'interfaccia."""
from PySide6.QtWidgets import QLabel


def label(text="", name=None, wrap=False):
    l = QLabel(text)
    if name:
        l.setObjectName(name)
    l.setWordWrap(wrap)
    return l
