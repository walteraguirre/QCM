
# qcm_dual_channel.py — Raspberry Pi Pico
#
# Comandos:
#   START_1_NTEMP  →  1 cristal, sin termistor
#   START_1_TEMP   →  1 cristal, con termistor
#   START_2_NTEMP  →  2 cristales, sin termistor
#   START_2_TEMP   →  2 cristales, con termistor
#   STOP           →  detiene la medición
#
# Temperatura:
#   Termistor 34
#   Modelo Steinhart-Hart calibrado
#   NTC conectado a GP26 (ADC0)
#
# Salida: CSV por puerto serial

from micropython import const
import rp2
from rp2 import PIO, asm_pio
from machine import Pin, ADC
import uarray as array
import time
import sys
import math
import select


# ─── Constantes ───────────────────────────────────────────────

MAX_COUNT = const((1 << 32) - 1)

REF_FREQ_HZ = 125_000_000.0

GATE_TIME = 125_000_000


# ─── Termistor 34 — Steinhart-Hart ────────────────────────────

SH_A = 1.9648148223e-05
SH_B = 3.0869406086e-04
SH_C = -1.3403841252e-07

# Divisor resistivo:
#
# 3.3 V
#   |
# R_FIJA = 100k
#   |
#   +------ GP26 (ADC0)
#   |
# NTC 34
#   |
#  GND

R_FIJA = 100_000.0

ADC_MAX = 65535

TEMP_SAMPLES = 20
TEMP_DELAY_MS = 10


VALID_CMDS = (
    "START_1_NTEMP",
    "START_1_TEMP",
    "START_2_NTEMP",
    "START_2_TEMP"
)


# ─── Programas PIO ───────────────────────────────────────────

@asm_pio(sideset_init=PIO.OUT_HIGH)
def gate():

    mov(x, osr)

    wait(0, pin, 0)
    wait(1, pin, 0)

    label("loopstart")

    jmp(x_dec, "loopstart").side(0)

    wait(0, pin, 0)
    wait(1, pin, 0).side(1)

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
    wait(0, pin, 0).side(0)

    label("counter")

    wait(0, pin, 1)
    wait(1, pin, 1)

    jmp(pin, "output")
    jmp(x_dec, "counter")

    label("output")

    mov(isr, x).side(1)
    push()

    irq(block, 5)


# ─── Inicialización de canales ───────────────────────────────

def init_channel(
    pio_id,
    input_pin,
    gate_pin,
    pulse_fin_pin,
    gate_time
):

    base = pio_id * 4

    gate_pin.value(1)
    pulse_fin_pin.value(1)

    sm_g = rp2.StateMachine(
        base + 0,
        gate,
        freq=125_000_000,
        in_base=input_pin,
        sideset_base=gate_pin
    )

    sm_c = rp2.StateMachine(
        base + 1,
        clock_count,
        freq=125_000_000,
        in_base=gate_pin,
        jmp_pin=pulse_fin_pin
    )

    sm_p = rp2.StateMachine(
        base + 2,
        pulse_count,
        freq=125_000_000,
        in_base=gate_pin,
        sideset_base=pulse_fin_pin,
        jmp_pin=gate_pin
    )

    sm_g.put(gate_time)
    sm_g.exec("pull()")

    sm_c.put(MAX_COUNT)
    sm_c.exec("pull()")

    sm_p.put(MAX_COUNT - 1)
    sm_p.exec("pull()")

    sm_c.active(1)
    sm_p.active(1)
    sm_g.active(1)

    return sm_g, sm_c, sm_p


# ─── Cálculo de frecuencia ────────────────────────────────────

def calc_freq(raw_clk, raw_pulse):

    clk = 2 * (MAX_COUNT - raw_clk + 1)

    pul = (MAX_COUNT - raw_pulse) + 1

    if clk > 0:
        return pul * (REF_FREQ_HZ / clk)

    return 0.0


# ─── Detener canal ────────────────────────────────────────────

def stop_channel(sm_g, sm_c, sm_p):

    sm_g.active(0)
    sm_c.active(0)
    sm_p.active(0)


# ─── Temperatura — Termistor 34 ───────────────────────────────

def read_temperature():
    """
    Lee el termistor 34 conectado a GP26.

    Utiliza:
      - Divisor resistivo con R fija de 100k
      - Promedio de 20 muestras ADC
      - Modelo Steinhart-Hart calibrado

    Devuelve temperatura en grados Celsius.
    """

    total = 0

    for _ in range(TEMP_SAMPLES):

        total += ADC_THERMISTOR.read_u16()

        time.sleep_ms(TEMP_DELAY_MS)

    raw = total / TEMP_SAMPLES

    # Evitar división por cero
    raw = max(1, min(raw, ADC_MAX - 1))

    # Resistencia del NTC
    # R fija arriba, NTC abajo

    r_ntc = R_FIJA * raw / (ADC_MAX - raw)

    # Steinhart-Hart

    ln_r = math.log(r_ntc)

    inv_t = (
        SH_A
        + SH_B * ln_r
        + SH_C * (ln_r ** 3)
    )

    if inv_t <= 0:
        return float("nan")

    temp_kelvin = 1.0 / inv_t

    temp_celsius = temp_kelvin - 273.15

    return temp_celsius


# ─── Pines ────────────────────────────────────────────────────

PIN_SENSOR = Pin(
    15,
    Pin.IN,
    Pin.PULL_UP
)

PIN_REF = Pin(
    10,
    Pin.IN,
    Pin.PULL_UP
)

PIN_GATE_A = Pin(
    14,
    Pin.OUT
)

PIN_PULSE_FIN_A = Pin(
    13,
    Pin.OUT
)

PIN_GATE_B = Pin(
    9,
    Pin.OUT
)

PIN_PULSE_FIN_B = Pin(
    8,
    Pin.OUT
)

# Termistor 34

ADC_THERMISTOR = ADC(26)


# ─── Programa principal ──────────────────────────────────────

print("QCM listo. Esperando comando...")


while True:

    # Espera comando desde la PC

    cmd = sys.stdin.readline().strip()

    if cmd not in VALID_CMDS:

        print(
            f"ERROR: comando desconocido '{cmd}'."
        )

        print(
            f"# Comandos validos: {', '.join(VALID_CMDS)}"
        )

        continue

    dual = "2" in cmd

    temp_en = (
        "TEMP" in cmd
        and "NTEMP" not in cmd
    )

    print(f"OK:{cmd}")

    print(
        f"# Modo: {'2 cristales' if dual else '1 cristal'} | "
        f"Termistor: {'si' if temp_en else 'no'} | "
        f"gate=1s"
    )

    if temp_en:
        print("# Temperatura: Termistor 34 / Steinhart-Hart")


    # ─── Cabecera CSV ────────────────────────────────────────

    if dual and temp_en:

        print(
            "timestamp_s,f_sensor,f_ref,delta_f,temp_c"
        )

    elif dual and not temp_en:

        print(
            "timestamp_s,f_sensor,f_ref,delta_f"
        )

    elif not dual and temp_en:

        print(
            "timestamp_s,f_sensor,temp_c"
        )

    else:

        print(
            "timestamp_s,f_sensor"
        )


    # ─── Buffers ─────────────────────────────────────────────

    data_a = array.array("I", [0, 0])
    ready_a = array.array("b", [0])

    data_b = array.array("I", [0, 0])
    ready_b = array.array("b", [0])


    # ─── Canal A — Sensor ────────────────────────────────────

    sm_gate_a, sm_clk_a, sm_pulse_a = init_channel(
        0,
        PIN_SENSOR,
        PIN_GATE_A,
        PIN_PULSE_FIN_A,
        GATE_TIME
    )


    def handler_a(sm):

        if not ready_a[0]:

            sm.put(GATE_TIME)
            sm.exec("pull()")

            data_a[0] = sm_clk_a.get()
            data_a[1] = sm_pulse_a.get()

            ready_a[0] = 1


    sm_gate_a.irq(handler_a)


    # ─── Canal B — Referencia ────────────────────────────────

    if dual:

        sm_gate_b, sm_clk_b, sm_pulse_b = init_channel(
            1,
            PIN_REF,
            PIN_GATE_B,
            PIN_PULSE_FIN_B,
            GATE_TIME
        )


        def handler_b(sm):

            if not ready_b[0]:

                sm.put(GATE_TIME)
                sm.exec("pull()")

                data_b[0] = sm_clk_b.get()
                data_b[1] = sm_pulse_b.get()

                ready_b[0] = 1


        sm_gate_b.irq(handler_b)


    # ─── Loop de medición ────────────────────────────────────

    t0 = time.time()


    while True:

        # Comandos entrantes

        r, _, _ = select.select(
            [sys.stdin],
            [],
            [],
            0
        )

        if r:

            new_cmd = sys.stdin.readline().strip()

            if new_cmd == "STOP":

                stop_channel(
                    sm_gate_a,
                    sm_clk_a,
                    sm_pulse_a
                )

                if dual:

                    stop_channel(
                        sm_gate_b,
                        sm_clk_b,
                        sm_pulse_b
                    )

                print("# Medicion detenida.")

                print(
                    "QCM listo. Esperando comando..."
                )

                break


        # ─── Medición de un cristal ──────────────────────────

        if not dual and ready_a[0]:

            ts = time.time() - t0

            f_s = calc_freq(
                data_a[0],
                data_a[1]
            )

            # Liberar buffer antes de leer temperatura
            ready_a[0] = 0

            if temp_en:

                temp = read_temperature()

                print(
                    f"{ts:.1f},"
                    f"{f_s:.3f},"
                    f"{temp:.2f}"
                )

            else:

                print(
                    f"{ts:.1f},"
                    f"{f_s:.3f}"
                )


        # ─── Medición de dos cristales ───────────────────────

        elif dual and ready_a[0] and ready_b[0]:

            ts = time.time() - t0

            f_s = calc_freq(
                data_a[0],
                data_a[1]
            )

            f_r = calc_freq(
                data_b[0],
                data_b[1]
            )

            df = f_s - f_r

            # Liberar buffers antes de leer temperatura
            ready_a[0] = 0
            ready_b[0] = 0

            if temp_en:

                temp = read_temperature()

                print(
                    f"{ts:.1f},"
                    f"{f_s:.3f},"
                    f"{f_r:.3f},"
                    f"{df:.3f},"
                    f"{temp:.2f}"
                )

            else:

                print(
                    f"{ts:.1f},"
                    f"{f_s:.3f},"
                    f"{f_r:.3f},"
                    f"{df:.3f}"
                )

