
## Albert Interface Software

Software creato con la libreria `PySide6` (QT) per il robot che fa parte del progetto del **Laboratorio di Robotica** dell'[IIS Albert Einstein di Vimercate](https://einsteinvimercate.edu.it/) creato dagli studenti, questo software ha delle funzionalità come una interfaccia interattiva con una faccina che segue a colui che la guarda, in più ha delle opzioni di assitente inteligente con AI e una parte di controllo manuale per gli operatori.
## Prequisiti
- Python: **versione 3.14**  o superiore
- Speech Dispatcher
- Espeak Ng   
## Installazione (Raspberry Pi 5 / PC)

    pip install -r requirements.txt        # oppure: sudo apt install python3-pyside6.qtserialport ...
    sudo usermod -aG dialout $USER         # accesso a /dev/ttyACM0 (poi riaccedere)

    git clone https://github.com/FabrizioCoding121316/AlbertInterfaceSoftware.git # Clona la repositori
Consiglio: Per prima cosa [ creare un virtual enviroment di **Python**](https://docs.python.org/it/3/tutorial/venv.html)

## Avvio
    #Per avviare assieme al robot e con un pin scelto
    OPERATOR_PIN=4821 python3 albert_qt.py
    #Senza pin, pin di default (1234)
    python3 albert_qt.py

    # L'uso della variabile OPERATOR_PIN viene spiegato dopo

    #Dopo python3 albert_qt.py si possono aggiungere
    #le seguenti opzioni:
    # --sim                     nessun Pico: robot simulato
    # --kiosk                   schermo intero senza cursore
    # --no-camera               non avvia la telecamera (gli occhi seguono il cursore)
    # --no-salva-volti          riconosce i volti solo in memoria: niente database né foto su disco





Variabili: `PICO_PORT` (default /dev/ttyACM0), `OPERATOR_PIN` (serve a mettere il pin dell'operatore, accetta solo interi), `ANTHROPIC_API_KEY` (senza, risponde con le FAQ),
`ANTHROPIC_MODEL`(Modello usato per l'assistente).

## File
- `albert_qt.py` – interfaccia (volto animato, Laboratori, Assistente, Controllo con PIN) e avvio
- `robot.py` – seriale (QSerialPort), telemetria, watchdog guida, accesso operatore, simulazione
- `assistant.py` – FAQ + Claude (rete asincrona di Qt), aggancio `navigate_to()` per Nav2
- `pico/main.py` – firmware MicroPython (identico all'originale)
## Creatori

- [@FabrizioCoding121316](https://github.com/FabrizioCoding121316)


## Documentation

- [Documentation](https://linktodocumentation)


## Ringraziamenti
Ringraziamo a [@Annoiw38](https://github.com/Annoiw38) per l'uso di una versione del suo media Player per creare la classe MainWindow del programma.
## Screenshots & Video

[![Watch the video](https://img.youtube.com/vi/mfeCNVGbGb0/hqdefault.jpg)](https://www.youtube.com/watch?v=mfeCNVGbGb0)   

