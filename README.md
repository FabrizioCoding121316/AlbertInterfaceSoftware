# Albert Assistant – versione Qt (PySide6)

Sostituisce `server.py` + `index.html`: un solo programma Python con interfaccia Qt touch che parla
direttamente col Pico via USB seriale. Il firmware `pico/main.py` è **invariato**.

## Installazione (Raspberry Pi 5 / PC)
    pip install -r requirements.txt        # oppure: sudo apt install python3-pyside6.qtserialport ...
    sudo usermod -aG dialout $USER         # accesso a /dev/ttyACM0 (poi riaccedere)

## Avvio
    OPERATOR_PIN=4821 python3 albert_qt.py            # finestra
    OPERATOR_PIN=4821 python3 albert_qt.py --kiosk    # schermo intero, senza cursore
    python3 albert_qt.py --sim                        # senza Pico: robot simulato (PIN 1234 se non impostato)

Variabili: `PICO_PORT` (default /dev/ttyACM0), `OPERATOR_PIN`, `ANTHROPIC_API_KEY` (senza, risponde con le FAQ),
`ANTHROPIC_MODEL`.

## File
- `albert_qt.py` – interfaccia (volto animato, Laboratori, Assistente, Controllo con PIN) e avvio
- `robot.py` – seriale (QSerialPort), telemetria, watchdog guida, accesso operatore, simulazione
- `assistant.py` – FAQ + Claude (rete asincrona di Qt), aggancio `navigate_to()` per Nav2
- `pico/main.py` – firmware MicroPython (identico all'originale)
