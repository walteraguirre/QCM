# qcm_dual_channel.py  —  Raspberry Pi Pico
# Comandos:
#   START_1_NTEMP  →  1 cristal,  sin termistor
#   START_1_TEMP   →  1 cristal,  con termistor
#   START_2_NTEMP  →  2 cristales, sin termistor
#   START_2_TEMP   →  2 cristales, con termistor
#   STOP           →  detiene la medición
# Manda una línea CSV por segundo.

from micropython import const
import rp2
from rp2 import PIO, asm_pio
from machine import Pin, ADC
import uarray as array
import time
import sys
import math
import select

# ─── Constantes ───────────────────────────────────────────────────────────────
MAX_COUNT   = const((1 << 32) - 1)
REF_FREQ_HZ = 125_000_000.0   # ajustar tras calibración

# Termistor NTC 100kΩ — divisor resistivo con R_fija = 100kΩ
NTC_B   = 3950
NTC_R0  = 100_000
NTC_T0  = 298.15
R_FIJA  = 100_000
ADC_MAX = 65535

VALID_CMDS = ("START_1_NTEMP", "START_1_TEMP", "START_2_NTEMP", "START_2_TEMP")

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
    """Lee NTC 100kΩ en divisor resistivo en GP26. Devuelve °C."""
    raw   = ADC_THERMISTOR.read_u16()
    raw   = max(1, min(raw, ADC_MAX - 1))
    v     = raw / ADC_MAX * 3.3
    r_ntc = R_FIJA * v / (3.3 - v)
    tk    = 1.0 / (1.0/NTC_T0 + math.log(r_ntc / NTC_R0) / NTC_B)
    return tk - 273.15

# ─── Pines ────────────────────────────────────────────────────────────────────
PIN_SENSOR      = Pin(15, Pin.IN, Pin.PULL_UP)
PIN_REF         = Pin(10, Pin.IN, Pin.PULL_UP)
PIN_GATE_A      = Pin(14, Pin.OUT)
PIN_PULSE_FIN_A = Pin(13, Pin.OUT)
PIN_GATE_B      = Pin(9,  Pin.OUT)
PIN_PULSE_FIN_B = Pin(8,  Pin.OUT)
ADC_THERMISTOR  = ADC(26)

GATE_TIME = 125_000_000   # 1 segundo

# ─── Programa principal ───────────────────────────────────────────────────────
print("QCM listo. Esperando comando...")

while True:
    # ── Espera comando de la PC (bloqueante) ──────────────────────────────────
    cmd = sys.stdin.readline().strip()

    if cmd not in VALID_CMDS:
        print(f"ERROR: comando desconocido '{cmd}'.")
        print(f"# Comandos validos: {', '.join(VALID_CMDS)}")
        continue

    dual = "2" in cmd
    temp_en = "TEMP" in cmd and "NTEMP" not in cmd
    print(f"OK:{cmd}")
    print(f"# Modo: {'2 cristales' if dual else '1 cristal'} | "
          f"Termistor: {'si' if temp_en else 'no'} | gate=1s")

    # ── Cabecera CSV ──────────────────────────────────────────────────────────
    if dual and temp_en:
        print("timestamp_s,f_sensor,f_ref,delta_f,temp_c")
    elif dual and not temp_en:
        print("timestamp_s,f_sensor,f_ref,delta_f")
    elif not dual and temp_en:
        print("timestamp_s,f_sensor,temp_c")
    else:
        print("timestamp_s,f_sensor")

    # ── Buffers ───────────────────────────────────────────────────────────────
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
    t0 = time.time()

    while True:
        # Chequeo no bloqueante de comandos entrantes
        r, _, _ = select.select([sys.stdin], [], [], 0)
        if r:
            new_cmd = sys.stdin.readline().strip()
            if new_cmd == "STOP":
                stop_channel(sm_gate_a, sm_clk_a, sm_pulse_a)
                if dual:
                    stop_channel(sm_gate_b, sm_clk_b, sm_pulse_b)
                print("# Medicion detenida.")
                print("QCM listo. Esperando comando...")
                break

        # ── Publica datos cuando hay medición lista ────────────────────────────
        if not dual and ready_a[0]:
            ts  = time.time() - t0
            f_s = calc_freq(data_a[0], data_a[1])
            if temp_en:
                print(f"{ts:.1f},{f_s:.3f},{read_temperature():.2f}")
            else:
                print(f"{ts:.1f},{f_s:.3f}")
            ready_a[0] = 0

        elif dual and ready_a[0] and ready_b[0]:
            ts  = time.time() - t0
            f_s = calc_freq(data_a[0], data_a[1])
            f_r = calc_freq(data_b[0], data_b[1])
            df  = f_s - f_r
            if temp_en:
                print(f"{ts:.1f},{f_s:.3f},{f_r:.3f},{df:.3f},{read_temperature():.2f}")
            else:
                print(f"{ts:.1f},{f_s:.3f},{f_r:.3f},{df:.3f}")
            ready_a[0] = 0
            ready_b[0] = 0
