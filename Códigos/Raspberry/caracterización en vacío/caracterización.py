# =============================================================================
# qcm_vacuum_thermal.py — Raspberry Pi Pico
#
# Caracterización térmica QCM en vacío
#
# Hardware:
#   - 1 cristal QCM
#   - 2 termistores NTC 100k
#
# Comandos desde PC:
#   START
#   STOP
#
# Salida:
# timestamp_s,f_sensor,temp_1_c,temp_2_c
# =============================================================================

from micropython import const
import rp2
from rp2 import PIO, asm_pio
from machine import Pin, ADC
import uarray as array
import time
import sys
import math
import select


# =============================================================================
# CONSTANTES
# =============================================================================

MAX_COUNT = const((1 << 32) - 1)

# Frecuencia de referencia de las State Machines
REF_FREQ_HZ = 125_000_000.0


# =============================================================================
# TERMISTORES NTC
# =============================================================================

# NTC 100 kΩ @ 25 °C
NTC_B  = 3950.0
NTC_R0 = 100_000.0
NTC_T0 = 298.15          # 25 °C en Kelvin

# Resistencia fija del divisor
R_FIJA = 100_000.0

ADC_MAX = 65535.0
VCC     = 3.3

# Cantidad de muestras ADC a promediar
NTC_SAMPLES = 10


# =============================================================================
# PROGRAMAS PIO
# =============================================================================

@asm_pio(sideset_init=PIO.OUT_HIGH)
def gate():

    mov(x, osr)

    # Sincronización inicial
    wait(0, pin, 0)
    wait(1, pin, 0)

    # Ventana temporal
    label("loopstart")
    jmp(x_dec, "loopstart") .side(0)

    # Cierre de ventana
    wait(0, pin, 0)
    wait(1, pin, 0) .side(1)

    # Avisar al procesador
    irq(block, 0)

    # Esperar contadores
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


# =============================================================================
# FUNCIONES QCM
# =============================================================================

def init_channel(input_pin, gate_pin, pulse_fin_pin, gate_time):

    gate_pin.value(1)
    pulse_fin_pin.value(1)

    # -------------------------------------------------------------------------
    # SM0 — Gate
    # -------------------------------------------------------------------------

    sm_g = rp2.StateMachine(
        0,
        gate,
        freq=125_000_000,
        in_base=input_pin,
        sideset_base=gate_pin
    )

    # -------------------------------------------------------------------------
    # SM1 — Clock counter
    # -------------------------------------------------------------------------

    sm_c = rp2.StateMachine(
        1,
        clock_count,
        freq=125_000_000,
        in_base=gate_pin,
        jmp_pin=pulse_fin_pin
    )

    # -------------------------------------------------------------------------
    # SM2 — QCM pulse counter
    # -------------------------------------------------------------------------

    sm_p = rp2.StateMachine(
        2,
        pulse_count,
        freq=125_000_000,
        in_base=gate_pin,
        sideset_base=pulse_fin_pin,
        jmp_pin=gate_pin
    )

    # Cargar valores iniciales
    sm_g.put(gate_time)
    sm_g.exec("pull()")

    sm_c.put(MAX_COUNT)
    sm_c.exec("pull()")

    sm_p.put(MAX_COUNT - 1)
    sm_p.exec("pull()")

    # Activar
    sm_c.active(1)
    sm_p.active(1)
    sm_g.active(1)

    return sm_g, sm_c, sm_p


def calc_freq(raw_clk, raw_pulse):

    clk = 2 * (MAX_COUNT - raw_clk + 1)
    pul = (MAX_COUNT - raw_pulse) + 1

    if clk <= 0:
        return 0.0

    return pul * (REF_FREQ_HZ / clk)


def stop_channel(sm_g, sm_c, sm_p):

    sm_g.active(0)
    sm_c.active(0)
    sm_p.active(0)


# =============================================================================
# TEMPERATURA
# =============================================================================

def read_temperature(adc):
    """
    Lee un termistor NTC 100 kΩ.

    Divisor:

        3.3 V
          |
        R fija
        100 kΩ
          |
          +------ ADC
          |
        NTC
          |
         GND

    Se utiliza el modelo Beta.
    """

    total = 0

    # Promedio ADC
    for _ in range(NTC_SAMPLES):

        total += adc.read_u16()

        # Pausa muy pequeña entre muestras
        time.sleep_ms(2)

    raw = total / NTC_SAMPLES

    # Evitar extremos matemáticos
    if raw < 1:
        raw = 1

    elif raw > ADC_MAX - 1:
        raw = ADC_MAX - 1


    # -------------------------------------------------------------------------
    # Tensión ADC
    # -------------------------------------------------------------------------

    v = (raw / ADC_MAX) * VCC


    # -------------------------------------------------------------------------
    # Resistencia NTC
    #
    # Divisor:
    #
    # Vadc = Vcc * Rntc / (Rfija + Rntc)
    #
    # entonces:
    #
    # Rntc = Rfija * Vadc / (Vcc - Vadc)
    # -------------------------------------------------------------------------

    r_ntc = R_FIJA * v / (VCC - v)


    # -------------------------------------------------------------------------
    # Modelo Beta
    # -------------------------------------------------------------------------

    tk = 1.0 / (
        (1.0 / NTC_T0)
        +
        (math.log(r_ntc / NTC_R0) / NTC_B)
    )


    # Kelvin -> Celsius
    return tk - 273.15


# =============================================================================
# PINES
# =============================================================================

# QCM
PIN_SENSOR = Pin(
    15,
    Pin.IN,
    Pin.PULL_UP
)

# Pines auxiliares PIO
PIN_GATE = Pin(
    14,
    Pin.OUT
)

PIN_PULSE_FIN = Pin(
    13,
    Pin.OUT
)


# =============================================================================
# ADC — TERMISTORES
# =============================================================================

# NTC 1
# GP26 = ADC0
ADC_TEMP_1 = ADC(26)

# NTC 2
# GP27 = ADC1
ADC_TEMP_2 = ADC(27)


# =============================================================================
# CONFIGURACIÓN DE MEDICIÓN
# =============================================================================

# Aproximadamente 1 segundo
GATE_TIME = 125_000_000


# =============================================================================
# INICIO
# =============================================================================

print("QCM VACUUM THERMAL TEST")
print("# 1 QCM + 2 NTC 100k")
print("# Waiting for START command")


# =============================================================================
# PROGRAMA PRINCIPAL
# =============================================================================

while True:

    # -------------------------------------------------------------------------
    # Esperar START
    # -------------------------------------------------------------------------

    cmd = sys.stdin.readline().strip().upper()

    if cmd != "START":

        # Ignoramos líneas vacías
        if cmd:
            print("ERROR: comando desconocido")
            print("# Comandos validos: START")

        continue


    # =========================================================================
    # INICIO DE MEDICIÓN
    # =========================================================================

    print("OK:START")

    print("# Calibracion termica QCM en vacio")
    print("# QCM=GP15")
    print("# TEMP1=GP26/ADC0")
    print("# TEMP2=GP27/ADC1")
    print("# gate≈1s")

    # Exactamente lo que espera la GUI
    print(
        "timestamp_s,"
        "f_sensor,"
        "temp_1_c,"
        "temp_2_c"
    )


    # =========================================================================
    # BUFFERS
    # =========================================================================

    data = array.array(
        "I",
        [0, 0]
    )

    ready = array.array(
        "b",
        [0]
    )


    # =========================================================================
    # INICIALIZAR PIO
    # =========================================================================

    sm_gate, sm_clk, sm_pulse = init_channel(
        PIN_SENSOR,
        PIN_GATE,
        PIN_PULSE_FIN,
        GATE_TIME
    )


    # =========================================================================
    # INTERRUPCIÓN DE FIN DE MEDICIÓN
    # =========================================================================

    def handler(sm):

        # Leer resultados de las otras SM
        data[0] = sm_clk.get()
        data[1] = sm_pulse.get()

        # Indicar que hay una medición disponible
        ready[0] = 1

        # Preparar inmediatamente la siguiente ventana
        sm.put(GATE_TIME)
        sm.exec("pull()")


    sm_gate.irq(handler)


    # =========================================================================
    # REFERENCIA TEMPORAL
    # =========================================================================

    t0 = time.ticks_ms()


    # =========================================================================
    # LOOP DE ADQUISICIÓN
    # =========================================================================

    while True:

        # ---------------------------------------------------------------------
        # Revisar comandos provenientes de PC
        # ---------------------------------------------------------------------

        r, _, _ = select.select(
            [sys.stdin],
            [],
            [],
            0
        )


        if r:

            new_cmd = (
                sys.stdin
                .readline()
                .strip()
                .upper()
            )


            if new_cmd == "STOP":

                stop_channel(
                    sm_gate,
                    sm_clk,
                    sm_pulse
                )

                ready[0] = 0

                print("# Medicion detenida")
                print("# Waiting for START command")

                break


        # ---------------------------------------------------------------------
        # Procesar nueva medición
        # ---------------------------------------------------------------------

        if ready[0]:

            # Copiar primero los contadores
            raw_clk   = data[0]
            raw_pulse = data[1]

            # Liberar flag
            ready[0] = 0


            # -----------------------------------------------------------------
            # Tiempo
            # -----------------------------------------------------------------

            ts = (
                time.ticks_diff(
                    time.ticks_ms(),
                    t0
                )
                / 1000.0
            )


            # -----------------------------------------------------------------
            # Frecuencia QCM
            # -----------------------------------------------------------------

            f_sensor = calc_freq(
                raw_clk,
                raw_pulse
            )


            # -----------------------------------------------------------------
            # Temperaturas
            # -----------------------------------------------------------------

            temp_1 = read_temperature(
                ADC_TEMP_1
            )

            temp_2 = read_temperature(
                ADC_TEMP_2
            )


            # -----------------------------------------------------------------
            # SALIDA SERIAL
            #
            # La GUI espera exactamente:
            #
            # tiempo,frecuencia,T1,T2
            # -----------------------------------------------------------------

            print(
                "{:.3f},{:.3f},{:.3f},{:.3f}".format(
                    ts,
                    f_sensor,
                    temp_1,
                    temp_2
                )
            )