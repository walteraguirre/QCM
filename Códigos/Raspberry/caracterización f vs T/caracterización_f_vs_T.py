# qcm_temperature_characterization.py — Raspberry Pi Pico
#
# Caracterización térmica de un cristal QCM
#
# Comandos:
#   START_1_NTEMP   -> 1 cristal, sin termistores
#   START_1_TEMP    -> 1 cristal, 1 termistor
#   START_1_2TEMP   -> 1 cristal, 2 termistores
#   STOP            -> detiene la medición
#
# Salida CSV:
#
# START_1_NTEMP
# timestamp_s,f_sensor
#
# START_1_TEMP
# timestamp_s,f_sensor,temp1_c
#
# START_1_2TEMP
# timestamp_s,f_sensor,temp1_c,temp2_c

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

MAX_COUNT   = const((1 << 32) - 1)

# Frecuencia de referencia del clock del RP2040.
# Ajustar luego de calibrar el contador si corresponde.
REF_FREQ_HZ = 125_000_000.0


# =============================================================================
# TERMISTORES — MODELO STEINHART-HART
# =============================================================================
#
# NTC 100 kOhm
#
# Divisor resistivo:
#
#        3.3 V
#          |
#       R_FIJA
#          |
#          +------ ADC
#          |
#         NTC
#          |
#         GND
#
# Modelo utilizado:
#
#       1/T = A + B*ln(R) + C*[ln(R)]^3
#
# donde:
#   T = temperatura absoluta [K]
#   R = resistencia del termistor [ohm]
#
# Los coeficientes actuales son PROVISORIOS para las pruebas.
# Para la caracterización final deberán reemplazarse por los
# obtenidos experimentalmente para cada termistor.


# -----------------------------------------------------------------------------
# Coeficientes Steinhart-Hart — Termistor 1
# -----------------------------------------------------------------------------

SH_A_1 = 1.129148e-3
SH_B_1 = 2.34125e-4
SH_C_1 = 8.76741e-8


# -----------------------------------------------------------------------------
# Coeficientes Steinhart-Hart — Termistor 2
# -----------------------------------------------------------------------------

SH_A_2 = 1.129148e-3
SH_B_2 = 2.34125e-4
SH_C_2 = 8.76741e-8


# Resistencia fija del divisor
R_FIJA = 100_000.0

# ADC RP2040
ADC_MAX = 65535
VCC = 3.3

# Cantidad de muestras para promedio de temperatura
TEMP_SAMPLES = 20

# Espera entre muestras
TEMP_SAMPLE_DELAY_MS = 10


# =============================================================================
# COMANDOS
# =============================================================================

VALID_CMDS = (
    "START_1_NTEMP",
    "START_1_TEMP",
    "START_1_2TEMP",
)


# =============================================================================
# PROGRAMAS PIO
# =============================================================================

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


# =============================================================================
# FUNCIONES DE FRECUENCIA
# =============================================================================

def init_channel(input_pin, gate_pin, pulse_fin_pin, gate_time):

    gate_pin.value(1)
    pulse_fin_pin.value(1)

    sm_g = rp2.StateMachine(
        0,
        gate,
        freq=125_000_000,
        in_base=input_pin,
        sideset_base=gate_pin
    )

    sm_c = rp2.StateMachine(
        1,
        clock_count,
        freq=125_000_000,
        in_base=gate_pin,
        jmp_pin=pulse_fin_pin
    )

    sm_p = rp2.StateMachine(
        2,
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


def calc_freq(raw_clk, raw_pulse):

    clk = 2 * (MAX_COUNT - raw_clk + 1)
    pul = (MAX_COUNT - raw_pulse) + 1

    if clk > 0:
        return pul * (REF_FREQ_HZ / clk)

    return 0.0


def stop_channel(sm_g, sm_c, sm_p):

    sm_g.active(0)
    sm_c.active(0)
    sm_p.active(0)


# =============================================================================
# FUNCIONES DE TEMPERATURA
# =============================================================================

def adc_to_resistance(raw):
    """
    Convierte la lectura ADC en la resistencia del termistor.

    Configuración del divisor:

        3.3 V
          |
        R_FIJA
          |
          +------ ADC
          |
         NTC
          |
         GND
    """

    # Evita divisiones por cero en los extremos del ADC
    raw = max(1, min(raw, ADC_MAX - 1))

    # ADC -> tensión
    v = raw / ADC_MAX * VCC

    # Tensión -> resistencia del NTC
    r_ntc = R_FIJA * v / (VCC - v)

    return r_ntc


def steinhart_hart_temperature(r_ntc, A, B, C):
    """
    Convierte la resistencia del termistor en temperatura
    utilizando el modelo de Steinhart-Hart de tres coeficientes.

        1/T = A + B*ln(R) + C*[ln(R)]^3

    Retorna temperatura en grados Celsius.
    """

    ln_r = math.log(r_ntc)

    inv_t = (
        A
        + B * ln_r
        + C * (ln_r ** 3)
    )

    temperature_k = 1.0 / inv_t

    return temperature_k - 273.15


def adc_to_temperature_1(raw):
    """
    Conversión ADC -> temperatura para el termistor 1.
    """

    r_ntc = adc_to_resistance(raw)

    return steinhart_hart_temperature(
        r_ntc,
        SH_A_1,
        SH_B_1,
        SH_C_1
    )


def adc_to_temperature_2(raw):
    """
    Conversión ADC -> temperatura para el termistor 2.
    """

    r_ntc = adc_to_resistance(raw)

    return steinhart_hart_temperature(
        r_ntc,
        SH_A_2,
        SH_B_2,
        SH_C_2
    )


def read_temperature_1():
    """
    Lee solamente el termistor 1.
    """

    total = 0

    for _ in range(TEMP_SAMPLES):

        total += ADC_THERMISTOR_1.read_u16()

        time.sleep_ms(TEMP_SAMPLE_DELAY_MS)

    raw = total // TEMP_SAMPLES

    return adc_to_temperature_1(raw)


def read_temperatures_2():
    """
    Lee ambos termistores dentro del mismo ciclo de muestreo.

    Los dos ADC se muestrean consecutivamente para minimizar
    la diferencia temporal entre ambas mediciones.
    """

    total_1 = 0
    total_2 = 0

    for _ in range(TEMP_SAMPLES):

        total_1 += ADC_THERMISTOR_1.read_u16()
        total_2 += ADC_THERMISTOR_2.read_u16()

        time.sleep_ms(TEMP_SAMPLE_DELAY_MS)

    raw_1 = total_1 // TEMP_SAMPLES
    raw_2 = total_2 // TEMP_SAMPLES

    temp_1 = adc_to_temperature_1(raw_1)
    temp_2 = adc_to_temperature_2(raw_2)

    return temp_1, temp_2


# =============================================================================
# PINES
# =============================================================================

# Señal del oscilador QCM
PIN_SENSOR = Pin(
    15,
    Pin.IN,
    Pin.PULL_UP
)

# Pines utilizados por el contador recíproco
PIN_GATE = Pin(
    14,
    Pin.OUT
)

PIN_PULSE_FIN = Pin(
    13,
    Pin.OUT
)


# -----------------------------------------------------------------------------
# Termistores
# -----------------------------------------------------------------------------

# Termistor 1
# GP26 = ADC0
ADC_THERMISTOR_1 = ADC(26)

# Termistor 2
# GP27 = ADC1
ADC_THERMISTOR_2 = ADC(27)


# =============================================================================
# TIEMPO DE GATE
# =============================================================================

GATE_TIME = 125_000_000      # aproximadamente 1 segundo


# =============================================================================
# PROGRAMA PRINCIPAL
# =============================================================================

print("QCM caracterizacion termica listo.")
print("Esperando comando...")

while True:

    # =========================================================================
    # ESPERA DE COMANDO
    # =========================================================================

    cmd = sys.stdin.readline().strip()

    if cmd not in VALID_CMDS:

        print(
            "ERROR: comando desconocido '{}'.".format(cmd)
        )

        print(
            "# Comandos validos: {}".format(
                ", ".join(VALID_CMDS)
            )
        )

        continue


    # =========================================================================
    # DETERMINAR CANTIDAD DE TERMISTORES
    # =========================================================================

    if cmd == "START_1_NTEMP":

        n_temp = 0

    elif cmd == "START_1_TEMP":

        n_temp = 1

    elif cmd == "START_1_2TEMP":

        n_temp = 2


    print("OK:{}".format(cmd))

    print(
        "# Modo: 1 cristal | Termistores: {} | gate=1s".format(
            n_temp
        )
    )


    # =========================================================================
    # CABECERA CSV
    # =========================================================================

    if n_temp == 0:

        print(
            "timestamp_s,f_sensor"
        )

    elif n_temp == 1:

        print(
            "timestamp_s,f_sensor,temp1_c"
        )

    elif n_temp == 2:

        print(
            "timestamp_s,f_sensor,temp1_c,temp2_c"
        )


    # =========================================================================
    # BUFFER DE FRECUENCIA
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
    # INICIALIZACIÓN DEL CANAL QCM
    # =========================================================================

    sm_gate, sm_clk, sm_pulse = init_channel(
        PIN_SENSOR,
        PIN_GATE,
        PIN_PULSE_FIN,
        GATE_TIME
    )


    # =========================================================================
    # INTERRUPCIÓN DEL CONTADOR
    # =========================================================================

    def handler(sm):

        if not ready[0]:

            # Recarga tiempo de gate
            sm.put(GATE_TIME)
            sm.exec("pull()")

            # Recupera resultados
            data[0] = sm_clk.get()
            data[1] = sm_pulse.get()

            ready[0] = 1


    sm_gate.irq(handler)


    # =========================================================================
    # LOOP DE MEDICIÓN
    # =========================================================================

    t0 = time.time()

    while True:

        # ---------------------------------------------------------------------
        # Chequeo no bloqueante del comando STOP
        # ---------------------------------------------------------------------

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
                    sm_gate,
                    sm_clk,
                    sm_pulse
                )

                print("# Medicion detenida.")
                print("QCM caracterizacion termica listo.")
                print("Esperando comando...")

                break


        # ---------------------------------------------------------------------
        # Nueva medición de frecuencia disponible
        # ---------------------------------------------------------------------

        if ready[0]:

            ts = time.time() - t0

            f_sensor = calc_freq(
                data[0],
                data[1]
            )


            # =================================================================
            # SIN TERMISTORES
            # =================================================================

            if n_temp == 0:

                print(
                    "{:.1f},{:.3f}".format(
                        ts,
                        f_sensor
                    )
                )


            # =================================================================
            # UN TERMISTOR
            # =================================================================

            elif n_temp == 1:

                temp_1 = read_temperature_1()

                print(
                    "{:.1f},{:.3f},{:.2f}".format(
                        ts,
                        f_sensor,
                        temp_1
                    )
                )


            # =================================================================
            # DOS TERMISTORES
            # =================================================================

            elif n_temp == 2:

                temp_1, temp_2 = read_temperatures_2()

                print(
                    "{:.1f},{:.3f},{:.2f},{:.2f}".format(
                        ts,
                        f_sensor,
                        temp_1,
                        temp_2
                    )
                )


            # Permite recibir la próxima medición
            ready[0] = 0