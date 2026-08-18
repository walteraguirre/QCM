"""
Lectura de termistor NTC 100k en Raspberry Pi Pico (MicroPython)
-----------------------------------------------------------------
Circuito (divisor de tension):

  3.3V ---[R_FIJO 100k]---+---[Termistor 100k]--- GND
                            |
                           ADC (GP26 / pin 31)

Ajusta los valores de R_FIJO, R0, B y T0 segun el datasheet
de tu termistor especifico (suelen variar un poco entre marcas).
"""

from machine import ADC, Pin
import utime
import math

# ---------- Configuracion ----------
ADC_PIN = 26          # GP26 (ADC0). Tambien podes usar GP27 (ADC1) o GP28 (ADC2)
R_FIJO = 100_000       # Resistencia fija del divisor (ohms)
R0 = 100_000           # Resistencia nominal del termistor a T0
T0 = 25 + 273.15       # Temperatura nominal en Kelvin (25 C)
BETA = 3950             # Coeficiente Beta tipico para NTC 100k (revisa tu datasheet)
VCC = 3.3                # Voltaje de referencia del ADC

# El termistor puede estar arriba (entre VCC y el nodo) o abajo (entre el nodo y GND).
# En el esquema de arriba esta ABAJO, conectado a GND.
TERMISTOR_ABAJO = True

# ------------------------------------

adc = ADC(Pin(ADC_PIN))

def leer_voltaje():
    lectura = adc.read_u16()        # valor de 0 a 65535
    voltaje = (lectura / 65535) * VCC
    return voltaje

def calcular_temperatura(voltaje):
    if voltaje <= 0 or voltaje >= VCC:
        return None  # lectura invalida (circuito abierto o corto)

    if TERMISTOR_ABAJO:
        # Vout = VCC * R_term / (R_fijo + R_term)
        r_term = R_FIJO * (voltaje / (VCC - voltaje))
    else:
        # Termistor arriba: Vout = VCC * R_fijo / (R_fijo + R_term)
        r_term = R_FIJO * ((VCC - voltaje) / voltaje)

    # Ecuacion Beta (Steinhart-Hart simplificada)
    inv_t = (1 / T0) + (1 / BETA) * math.log(r_term / R0)
    temp_k = 1 / inv_t
    temp_c = temp_k - 273.15
    return temp_c

while True:
    v = leer_voltaje()
    t = calcular_temperatura(v)

    if t is not None:
        print("Voltaje: {:.3f} V  |  Temperatura: {:.2f} C".format(v, t))
    else:
        print("Lectura fuera de rango (revisa conexiones)")

    utime.sleep(1)