# qcm_dual_channel.py  —  Raspberry Pi Pico
# Espera un comando de la PC por serial antes de arrancar:
#   'START_1\n'  →  modo un cristal  (solo canal A, pin GP15)
#   'START_2\n'  →  modo dos cristales (canal A en GP15, canal B en GP10)
# Manda una línea CSV por segundo.

from micropython import const
import rp2
from rp2 import PIO, asm_pio
from machine import Pin, ADC
import uarray as array
import time
import sys
import math

# ─── Constantes ───────────────────────────────────────────────────────────────
MAX_COUNT   = const((1 << 32) - 1)
REF_FREQ_HZ = 125_000_000.0   # ajustar tras calibración

# Termistor NTC 100kΩ — divisor resistivo con R_fija = 100kΩ a 3.3V
# Ecuación de Steinhart-Hart simplificada (beta)
NTC_B    = 3950      # coeficiente B del termistor
NTC_R0   = 100_000   # resistencia nominal a 25°C (Ω)
NTC_T0   = 298.15    # temperatura nominal en Kelvin (25°C)
R_FIJA   = 100_000   # resistencia fija del divisor (Ω) — igual al NTC nominal
ADC_MAX  = 65535     # resolución del ADC del RP2040 (16 bit)

# ─── PIO programs ─────────────────────────────────────────────────────────────

@asm_pio(sideset_init=PIO.OUT_HIGH)
def gate():
    mov(x, osr)
    wait(0, pin, 0)
    wait(1, pin, 0)
    label("loopstart")
    jmp(x_dec, "loopstart") .side(0)
    wait(0, pin, 0)
    wait(1, pin, 0) .side(1)
    irq(block, 0)
    wait(1, irq, 4)
    wait(1, irq, 5)

@asm_pio()
def clock_count():
    mov(x, osr)
    wait(1, pin, 0)
    wait(0, pin, 0)
    label("counter")
    jmp(pin, "output")
    jmp(x_dec, "counter")
    label("output")
    mov(isr, x)
    push()
    irq(block, 4)

@asm_pio(sideset_init=PIO.OUT_HIGH)
def pulse_count():
    mov(x, osr)
    wait(1, pin, 0)
    wait(0, pin, 0) .side(0)
    label("counter")
    wait(0, pin, 1)
    wait(1, pin, 1)
    jmp(pin, "output")
    jmp(x_dec, "counter")
    label("output")
    mov(isr, x) .side(1)
    push()
    irq(block, 5)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def init_channel(pio_id, input_pin, gate_pin, pulse_fin_pin, gate_time):
    base = pio_id * 4
    gate_pin.value(1)
    pulse_fin_pin.value(1)

    sm_g = rp2.StateMachine(base+0, gate,        freq=125_000_000, in_base=input_pin,  sideset_base=gate_pin)
    sm_c = rp2.StateMachine(base+1, clock_count, freq=125_000_000, in_base=gate_pin,   jmp_pin=pulse_fin_pin)
    sm_p = rp2.StateMachine(base+2, pulse_count, freq=125_000_000, in_base=gate_pin,   sideset_base=pulse_fin_pin, jmp_pin=gate_pin)

    sm_g.put(gate_time);   sm_g.exec("pull()")
    sm_c.put(MAX_COUNT);   sm_c.exec("pull()")
    sm_p.put(MAX_COUNT-1); sm_p.exec("pull()")

    sm_c.active(1)
    sm_p.active(1)
    sm_g.active(1)
    return sm_g, sm_c, sm_p

def calc_freq(raw_clk, raw_pulse):
    clk = 2 * (MAX_COUNT - raw_clk + 1)
    pul = (MAX_COUNT - raw_pulse) + 1
    return pul * (REF_FREQ_HZ / clk) if clk > 0 else 0.0

def stop_channel(sm_g, sm_c, sm_p):
    sm_g.active(0)
    sm_c.active(0)
    sm_p.active(0)

def read_temperature():
    """
    Lee el NTC 100kΩ conectado en divisor resistivo a GP26 (ADC0).
    Circuito:
        3.3V ── R_FIJA (100kΩ) ──┬── GP26 (ADC)
                                  │
                                 NTC
                                  │
                                GND
    Devuelve temperatura en °C.
    """
    raw  = ADC_THERMISTOR.read_u16()          # 0–65535
    # Evitar división por cero si el ADC lee 0 o máximo
    raw  = max(1, min(raw, ADC_MAX - 1))
    # Tensión en el nodo del divisor
    v    = raw / ADC_MAX * 3.3
    # Resistencia del NTC a partir del divisor
    r_ntc = R_FIJA * v / (3.3 - v)
    # Ecuación beta de Steinhart-Hart
    temp_k = 1.0 / (1.0/NTC_T0 + math.log(r_ntc / NTC_R0) / NTC_B)
    return temp_k - 273.15                    # Kelvin → Celsius

# ─── Pines ────────────────────────────────────────────────────────────────────
PIN_SENSOR        = Pin(15, Pin.IN, Pin.PULL_UP)
PIN_REF           = Pin(10, Pin.IN, Pin.PULL_UP)
PIN_GATE_A        = Pin(14, Pin.OUT)
PIN_PULSE_FIN_A   = Pin(13, Pin.OUT)
PIN_GATE_B        = Pin(9,  Pin.OUT)
PIN_PULSE_FIN_B   = Pin(8,  Pin.OUT)
ADC_THERMISTOR    = ADC(26)     # GP26 = ADC0 — conectar el divisor resistivo aquí

GATE_TIME = 125_000_000   # 1 segundo

# ─── Programa principal ───────────────────────────────────────────────────────
print("QCM listo. Esperando comando (START_1 o START_2)...")

while True:
    # ── Espera comando de la PC (bloqueante) ──────────────────────────────────
    cmd = sys.stdin.readline().strip()

    if cmd not in ("START_1", "START_2"):
        print(f"ERROR: comando desconocido '{cmd}'. Usa START_1 o START_2.")
        continue

    dual = (cmd == "START_2")
    print(f"OK:{cmd}")
    print(f"# Modo: {'2 cristales' if dual else '1 cristal'}, gate=1s")

    # ── Cabecera CSV ──────────────────────────────────────────────────────────
    if dual:
        print("timestamp_s,f_sensor,f_ref,delta_f,temp_c")
    else:
        print("timestamp_s,f_sensor,temp_c")

    # ── Buffers compartidos con los IRQ handlers ───────────────────────────────
    data_a  = array.array("I", [0, 0])
    ready_a = array.array("b", [0])
    data_b  = array.array("I", [0, 0])
    ready_b = array.array("b", [0])

    # ── Canal A — sensor ──────────────────────────────────────────────────────
    sm_gate_a, sm_clk_a, sm_pulse_a = init_channel(
        0, PIN_SENSOR, PIN_GATE_A, PIN_PULSE_FIN_A, GATE_TIME
    )

    def handler_a(sm):
        if not ready_a[0]:
            sm.put(GATE_TIME)
            sm.exec("pull()")
            data_a[0] = sm_clk_a.get()
            data_a[1] = sm_pulse_a.get()
            ready_a[0] = 1

    sm_gate_a.irq(handler_a)

    # ── Canal B — referencia (solo modo dual) ─────────────────────────────────
    if dual:
        sm_gate_b, sm_clk_b, sm_pulse_b = init_channel(
            1, PIN_REF, PIN_GATE_B, PIN_PULSE_FIN_B, GATE_TIME
        )

        def handler_b(sm):
            if not ready_b[0]:
                sm.put(GATE_TIME)
                sm.exec("pull()")
                data_b[0] = sm_clk_b.get()
                data_b[1] = sm_pulse_b.get()
                ready_b[0] = 1

        sm_gate_b.irq(handler_b)

    # ── Loop de medición ──────────────────────────────────────────────────────
    import select
    t0 = time.time()

    while True:
        # Chequeo no bloqueante de comandos entrantes (ej: STOP)
        r, _, _ = select.select([sys.stdin], [], [], 0)
        if r:
            new_cmd = sys.stdin.readline().strip()
            if new_cmd == "STOP":
                stop_channel(sm_gate_a, sm_clk_a, sm_pulse_a)
                if dual:
                    stop_channel(sm_gate_b, sm_clk_b, sm_pulse_b)
                print("# Medicion detenida.")
                print("QCM listo. Esperando comando (START_1 o START_2)...")
                break  # vuelve al loop externo

        # Publica datos cuando hay una medición lista
        if not dual and ready_a[0]:
            ts   = time.time() - t0
            f_s  = calc_freq(data_a[0], data_a[1])
            temp = read_temperature()
            print(f"{ts:.1f},{f_s:.3f},{temp:.2f}")
            ready_a[0] = 0

        elif dual and ready_a[0] and ready_b[0]:
            ts   = time.time() - t0
            f_s  = calc_freq(data_a[0], data_a[1])
            f_r  = calc_freq(data_b[0], data_b[1])
            df   = f_s - f_r
            temp = read_temperature()
            print(f"{ts:.1f},{f_s:.3f},{f_r:.3f},{df:.3f},{temp:.2f}")
            ready_a[0] = 0
            ready_b[0] = 0
