
# ============================================================
# QCM - Medicion autonoma
# Raspberry Pi Pico / MicroPython
#
# Cristal: GP15
# Termistor 34: GP26
#
# Salida:
#   - USB serial
#   - Archivo mediciones.csv
#
# Inicio automatico, sin GUI
# ============================================================

from micropython import const
import rp2
from rp2 import PIO, asm_pio
from machine import Pin, ADC
import uarray as array
import time
import math
import os


# ============================================================
# CONFIGURACION
# ============================================================

MAX_COUNT = const((1 << 32) - 1)

REF_FREQ_HZ = 125_000_000.0
GATE_TIME = 125_000_000

ARCHIVO = "mediciones.csv"

# Guardar en memoria cada N mediciones
# 10 = aproximadamente cada 10 segundos
GUARDAR_CADA = 1

# Termistor 34 - Steinhart-Hart
SH_A = 1.9648148223e-05
SH_B = 3.0869406086e-04
SH_C = -1.3403841252e-07

R_FIJA = 100_000.0
ADC_MAX = 65535

TEMP_SAMPLES = 20
TEMP_DELAY_MS = 10


# ============================================================
# PINES
# ============================================================

PIN_SENSOR = Pin(15, Pin.IN, Pin.PULL_UP)

PIN_GATE = Pin(14, Pin.OUT)
PIN_PULSE_FIN = Pin(13, Pin.OUT)

ADC_THERMISTOR = ADC(26)


# ============================================================
# PROGRAMAS PIO
# ============================================================

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


# ============================================================
# INICIALIZAR CANAL
# ============================================================

def init_channel():

    PIN_GATE.value(1)
    PIN_PULSE_FIN.value(1)

    sm_g = rp2.StateMachine(
        0,
        gate,
        freq=125_000_000,
        in_base=PIN_SENSOR,
        sideset_base=PIN_GATE
    )

    sm_c = rp2.StateMachine(
        1,
        clock_count,
        freq=125_000_000,
        in_base=PIN_GATE,
        jmp_pin=PIN_PULSE_FIN
    )

    sm_p = rp2.StateMachine(
        2,
        pulse_count,
        freq=125_000_000,
        in_base=PIN_GATE,
        sideset_base=PIN_PULSE_FIN,
        jmp_pin=PIN_GATE
    )

    sm_g.put(GATE_TIME)
    sm_g.exec("pull()")

    sm_c.put(MAX_COUNT)
    sm_c.exec("pull()")

    sm_p.put(MAX_COUNT - 1)
    sm_p.exec("pull()")

    sm_c.active(1)
    sm_p.active(1)
    sm_g.active(1)

    return sm_g, sm_c, sm_p


# ============================================================
# CALCULO DE FRECUENCIA
# ============================================================

def calc_freq(raw_clk, raw_pulse):

    clk = 2 * (MAX_COUNT - raw_clk + 1)
    pul = (MAX_COUNT - raw_pulse) + 1

    if clk > 0:
        return pul * (REF_FREQ_HZ / clk)

    return 0.0


# ============================================================
# TEMPERATURA - TERMISTOR 34
# ============================================================

def read_temperature():

    total = 0

    for _ in range(TEMP_SAMPLES):
        total += ADC_THERMISTOR.read_u16()
        time.sleep_ms(TEMP_DELAY_MS)

    raw = total / TEMP_SAMPLES

    raw = max(1, min(raw, ADC_MAX - 1))

    # Divisor:
    # 3.3 V -> R fija -> GP26 -> NTC -> GND

    r_ntc = R_FIJA * raw / (ADC_MAX - raw)

    ln_r = math.log(r_ntc)

    inv_t = (
        SH_A
        + SH_B * ln_r
        + SH_C * ln_r**3
    )

    if inv_t <= 0:
        return float("nan")

    return 1.0 / inv_t - 273.15


# ============================================================
# PREPARAR ARCHIVO CSV
# ============================================================

def preparar_csv():

    # No borrar mediciones anteriores.
    # Si el archivo no existe, crear cabecera.

    try:
        os.stat(ARCHIVO)
    except OSError:
        with open(ARCHIVO, "w") as f:
            f.write(
                "tiempo_s,frecuencia_hz,temperatura_c\n"
            )


# ============================================================
# PROGRAMA PRINCIPAL
# ============================================================

def main():

    print("Iniciando QCM autonoma...")

    preparar_csv()

    data = array.array("I", [0, 0])
    ready = array.array("b", [0])

    sm_gate, sm_clk, sm_pulse = init_channel()

    # Interrupcion al finalizar cada ventana PIO
    def handler(sm):

        if not ready[0]:

            sm.put(GATE_TIME)
            sm.exec("pull()")

            data[0] = sm_clk.get()
            data[1] = sm_pulse.get()

            ready[0] = 1

    sm_gate.irq(handler)

    t0 = time.ticks_ms()

    contador = 0

    archivo = open(ARCHIVO, "a")

    print("tiempo_s,frecuencia_hz,temperatura_c")

    try:

        while True:

            if ready[0]:

                # Copiar datos de frecuencia
                raw_clk = data[0]
                raw_pulse = data[1]

                ready[0] = 0

                # Tiempo desde inicio
                tiempo_s = (
                    time.ticks_diff(
                        time.ticks_ms(), t0
                    ) / 1000.0
                )

                frecuencia = calc_freq(
                    raw_clk,
                    raw_pulse
                )

                temperatura = read_temperature()

                linea = "{:.3f},{:.3f},{:.2f}".format(
                    tiempo_s,
                    frecuencia,
                    temperatura
                )

                # Mostrar por serial
                print(linea)

                # Guardar en CSV
                archivo.write(linea + "\n")

                contador += 1

                # Escritura periodica a memoria
                if contador >= GUARDAR_CADA:
                    archivo.flush()
                    contador = 0

            else:
                time.sleep_ms(1)

    except KeyboardInterrupt:

        print("Medicion detenida.")

    finally:

        archivo.flush()
        archivo.close()

        sm_gate.active(0)
        sm_clk.active(0)
        sm_pulse.active(0)


# ============================================================
# INICIO AUTOMATICO
# ============================================================

main()
