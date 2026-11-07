# Albert Assistant – firmware Raspberry Pi Pico (MicroPython)
#
# Riceve comandi dal Raspberry Pi 5 via USB seriale (una riga per comando):
#   V <sx> <dx>   velocità ruote da -100 a 100 (percentuale del limite)
#   S             STOP software (con blocco finché non arriva R)
#   R             riarma dopo uno STOP
#   L 3           LED "sto parlando" per 3 secondi
# e risponde ogni 100 ms con la telemetria:
#   T bat_mV d_sx d_centro d_dx rotta_gradi velocità arresto_fisico modo encSx encDx
#   (modo: 0 fermo, 1 movimento, 2 arresto)
#
# Sicurezza gestita QUI, indipendentemente dal Pi 5:
#   limite di velocità, rampa, watchdog dei comandi (400 ms), stop per ostacoli,
#   watchdog hardware. L'arresto di emergenza fisico deve togliere potenza ai
#   driver con un contattore/relè propri: il Pico lo legge solo per informare.
#
# COLLEGAMENTI (tutti modificabili qui sotto – da verificare sul vostro hardware):
#   Driver BLDC sx: PWM GP2, DIR GP3, ENABLE GP4   | dx: PWM GP6, DIR GP7, ENABLE GP8
#   Hall encoder sx/dx: GP10 / GP11
#   Ultrasuoni HC-SR04 (sx, centro, dx): TRIG/ECHO GP12/13, GP14/15, GP16/17
#     (l'ECHO è a 5 V: serve un partitore o un level shifter verso i 3,3 V del Pico!)
#   Pulsante di emergenza (contatto NC verso GND): GP18
#   IMU MPU6050: SDA GP20, SCL GP21   | LED RGB WS2812 (8 led): GP22
#   Tensione batteria: GP27 tramite partitore (vedi VDIV)

import sys, time, math, select
from machine import Pin, PWM, I2C, ADC, WDT, time_pulse_us
import neopixel

# ---- Configurazione -------------------------------------------------------
MAX_PCT = 35        # limite di velocità assoluto (% della potenza del driver)
RAMP = 4            # variazione massima per ciclo da 20 ms
CMD_TIMEOUT = 400   # ms senza comandi -> stop
STOP_CM, SLOW_CM = 30, 80
VDIV = 13.8         # rapporto del partitore sulla batteria (da misurare)
N_LED = 8

class Motor:
    def __init__(self, pwm, direction, enable, invert=False):
        self.pwm = PWM(Pin(pwm)); self.pwm.freq(15000); self.pwm.duty_u16(0)
        self.dir = Pin(direction, Pin.OUT, value=0)
        self.en = Pin(enable, Pin.OUT, value=0)
        self.invert = invert
    def set(self, pct):
        if pct == 0:
            self.pwm.duty_u16(0); self.en.value(0); return
        self.en.value(1)
        self.dir.value((pct < 0) != self.invert)
        self.pwm.duty_u16(int(abs(pct) / 100 * 65535))

class Sonar:
    def __init__(self, trig, echo):
        self.trig = Pin(trig, Pin.OUT, value=0); self.echo = Pin(echo, Pin.IN); self.cm = 400
    def read(self):
        self.trig.value(1); time.sleep_us(10); self.trig.value(0)
        us = time_pulse_us(self.echo, 1, 12000)
        self.cm = 400 if us < 0 else min(400, int(us * 0.0343 / 2))

motors = (Motor(2, 3, 4), Motor(6, 7, 8, invert=True))   # il motore destro è montato specchiato
sonars = (Sonar(12, 13), Sonar(14, 15), Sonar(16, 17))
estop_pin = Pin(18, Pin.IN, Pin.PULL_UP)                  # NC: cavo staccato = emergenza
vbat = ADC(27)
leds = neopixel.NeoPixel(Pin(22), N_LED)

enc = [0, 0]
def _irq(i):
    def f(_): enc[i] += 1
    return f
_hall = [Pin(10, Pin.IN, Pin.PULL_UP), Pin(11, Pin.IN, Pin.PULL_UP)]
for i, p in enumerate(_hall):
    p.irq(trigger=Pin.IRQ_RISING, handler=_irq(i))

# ---- IMU (solo giroscopio Z per la rotta) ---------------------------------
imu_ok, gbias, heading = False, 0.0, 0.0
try:
    i2c = I2C(0, sda=Pin(20), scl=Pin(21), freq=400000)
    i2c.writeto_mem(0x68, 0x6B, b"\x00")
    imu_ok = True
except Exception:
    pass

def gyro_z():
    d = i2c.readfrom_mem(0x68, 0x47, 2)
    v = (d[0] << 8) | d[1]
    return (v - 65536 if v > 32767 else v) / 131.0

if imu_ok:   # calibrazione a robot fermo
    try:
        gbias = sum(gyro_z() or 0 for _ in range(100) if time.sleep_ms(5) is None) / 100
    except Exception:
        imu_ok = False

# ---- LED ------------------------------------------------------------------
talk_until = 0
def paint(mode, now):
    k = 0.35 + 0.35 * math.sin(now / 450)            # respiro
    if mode == 2:   c = (255, 0, 0) if (now // 250) % 2 else (40, 0, 0)
    elif talk_until > now: c = (0, int(120 + 100 * math.sin(now / 90)), 60)
    elif mode == 1: c = (255, 150, 0)
    else:           c = (int(20 * k), int(210 * k), int(210 * k))
    for i in range(N_LED): leds[i] = c
    leds.write()

# ---- Ciclo principale -----------------------------------------------------
poll = select.poll(); poll.register(sys.stdin, select.POLLIN)
buf, tgt, cur = "", [0, 0], [0.0, 0.0]
last_cmd, latched = 0, False
last_tel = last_loop = last_gyro = time.ticks_ms()
sonar_i = 0
wdt = WDT(timeout=2000)

def handle(line):
    global tgt, last_cmd, latched, talk_until
    p = line.split()
    if not p: return
    if p[0] == "V" and len(p) == 3 and not latched:
        tgt = [max(-MAX_PCT, min(MAX_PCT, int(p[1]) * MAX_PCT // 100)),
               max(-MAX_PCT, min(MAX_PCT, int(p[2]) * MAX_PCT // 100))]
        last_cmd = time.ticks_ms()
    elif p[0] == "S": latched, tgt = True, [0, 0]
    elif p[0] == "R" and estop_pin.value() == 0: latched = False
    elif p[0] == "L": talk_until = time.ticks_add(time.ticks_ms(), 3000)

while True:
    wdt.feed()
    now = time.ticks_ms()
    while poll.poll(0):
        ch = sys.stdin.read(1)
        if ch == "\n":
            try: handle(buf.strip())
            except ValueError: pass
            buf = ""
        else:
            buf += ch

    if time.ticks_diff(now, last_loop) < 20: continue
    last_loop = now

    sonars[sonar_i].read(); sonar_i = (sonar_i + 1) % 3     # un sensore per ciclo
    if imu_ok:
        try:
            dt = time.ticks_diff(now, last_gyro) / 1000; last_gyro = now
            heading = (heading + (gyro_z() - gbias) * dt) % 360   # si sposta nel tempo: senza magnetometro è normale
        except Exception: pass

    estop = estop_pin.value() == 1
    timeout = time.ticks_diff(now, last_cmd) > CMD_TIMEOUT
    if estop: latched = True
    if latched or timeout:
        tgt, cur = [0, 0], [0.0, 0.0]
    else:
        # rallenta e poi blocca l'avanzamento se c'è un ostacolo davanti
        front = min(s.cm for s in sonars)
        scale = max(0.0, min(1.0, (front - STOP_CM) / (SLOW_CM - STOP_CM)))
        fwd = (tgt[0] + tgt[1]) / 2
        goal = [t - fwd * (1 - scale) for t in tgt] if fwd > 0 else tgt
        for i in (0, 1):
            cur[i] += max(-RAMP, min(RAMP, goal[i] - cur[i]))
    for i in (0, 1):
        motors[i].set(int(cur[i]))

    mode = 2 if latched else (1 if abs(cur[0]) + abs(cur[1]) > 1 else 0)
    paint(mode, now)

    if time.ticks_diff(now, last_tel) >= 100:
        last_tel = now
        mv = int(vbat.read_u16() / 65535 * 3300 * VDIV)
        print("T %d %d %d %d %d %d %d %d %d %d %d" % (
            mv, sonars[0].cm, sonars[1].cm, sonars[2].cm, int(heading),
            int((cur[0] + cur[1]) / 2 / MAX_PCT * 100), int(estop), mode, enc[0], enc[1]))
