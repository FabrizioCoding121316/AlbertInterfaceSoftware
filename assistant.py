"""Assistente: FAQ locali + Gemini (se c'è GEMINI_API_KEY).

L'assistente NON ha alcun riferimento al robot: informa e dialoga, non guida.
"""
import json
import logging
import os
import re

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

SYSTEM = ("Sei Albert, il robot assistente della scuola, costruito dagli studenti di Einstein Lab Robotics. "
          "Rispondi in italiano, in modo breve e cordiale (massimo 3 frasi). Parli di scuola, laboratori, indirizzi "
          "(Elettronica, Informatica, LSA) e Open Day. Se non sai una cosa, dillo e consiglia la segreteria. "
          "Puoi solo informare e dialogare: non puoi muovere il robot né cambiare i limiti di sicurezza.")

FAQ = [
    (r"indirizz|corso|elettronic|informatic|lsa|scienze",
     "Nella scuola trovi Elettronica, Informatica e LSA (liceo scienze applicate). Posso accompagnarti nei laboratori di ognuno."),
    (r"open ?day|visita|giro",
     "All’Open Day puoi visitare i laboratori con me: scegli le tappe nella schermata Laboratori."),
    (r"chi sei|albert|robot|come funzion",
     "Sono Albert, un robot costruito dagli studenti. Mi muovo su due ruote motrici e vedo gli ostacoli con sensori."),
]
log = logging.getLogger("albert.assistant")
FALLBACK = "Non sono sicuro di saperlo. Per questa domanda chiedi a un docente o alla segreteria."


def faq_answer(text: str) -> str:
    for pattern, answer in FAQ:
        if re.search(pattern, text, re.I):
            return answer
    return FALLBACK


class Assistant(QObject):
    answered = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.net = QNetworkAccessManager(self)

    def ask(self, text: str):
        text = text[:300]
        key = os.getenv("GEMINI_API_KEY")
        log.info("domanda di %d caratteri, risposta da %s", len(text), "Gemini" if key else "FAQ")
        if not key:
            self.answered.emit(faq_answer(text))
            return
        model = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        req = QNetworkRequest(QUrl(url))
        req.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        req.setRawHeader(b"x-goog-api-key", key.encode())
        req.setTransferTimeout(15000)
        body = json.dumps({
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.7},
        })
        reply = self.net.post(req, body.encode())
        reply.finished.connect(lambda r=reply, t=text: self._done(r, t))

    def _done(self, reply: QNetworkReply, text: str):
        try:
            if reply.error() != QNetworkReply.NoError:
                raise RuntimeError(reply.errorString())
            data = json.loads(bytes(reply.readAll()).decode())
            parts = data["candidates"][0]["content"]["parts"]
            answer = "".join(p.get("text", "") for p in parts).strip()
            if not answer:
                raise RuntimeError("risposta vuota")
        except Exception as e:
            log.warning("Gemini non raggiungibile (%s): uso le FAQ", e)
            answer = faq_answer(text)     # senza rete o oltre quota si ripiega sulle FAQ
        finally:
            reply.deleteLater()
        self.answered.emit(answer)


def navigate_to(dest: str):
    """AGGANCIO PER LA NAVIGAZIONE AUTONOMA (ROS 2 / Nav2, step successivo).
    Per ora non muove il robot: restituisce un messaggio onesto al visitatore."""
    return False, "Un operatore ti accompagnerà: la guida autonoma sarà attivata in uno dei prossimi passi."
