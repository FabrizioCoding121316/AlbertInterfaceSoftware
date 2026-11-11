"""Costanti, colori e dati statici di Albert."""
from pathlib import Path

BG, PANEL, PANEL2, LINE = "#0c1a2e", "#152a45", "#1f3a5d", "#2a4a72"
CYAN, AMBER, RED, GREEN, TEXT, MUTE = "#3ed6d6", "#ffb04d", "#ff5d5d", "#4fd69c", "#eef4fb", "#9db3cc"

BASE_DIR = Path(__file__).resolve().parent.parent
DB_FILE = str(BASE_DIR / "faces_database.pkl")
IMG_DIR = str(BASE_DIR / "faces_img")
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
