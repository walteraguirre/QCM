"""
Lectura de termistor NTC 100k en Raspberry Pi Pico (MicroPython)
-----------------------------------------------------------------
Circuito (divisor de tension):

  3.3V ---[R_FIJO 100k]---+---[Termistor 100k]--- GND
                            |
                           ADC (GP26 / pin 31)

Usa la ecuacion de Steinhart-Hart completa (coeficientes A, B, C).
Ajusta R_FIJO y los coeficientes SH_A/SH_B/SH_C segun tu datasheet
o tu propia calibracion (ver sección CALIBRACION mas abajo).
"""

from machine import ADC, Pin
import utime
import math

# ---------- Configuracion ----------
ADC_PIN = 26          # GP26 (ADC0). Tambien podes usar GP27 (ADC1) o GP28 (ADC2)
R_FIJO = 100_000       # Resistencia fija del divisor (ohms)
VCC = 3.3                # Voltaje de referencia del ADC

# Coeficientes de Steinhart-Hart (reemplazar por los tuyos, ver notas abajo)
# Estos son valores de EJEMPLO tipicos para un NTC 100k generico.
# Si tenes datasheet con A/B/C especificos, usa esos.
# Si calibraste con 3 puntos, reemplaza estos por los que calculaste.
SH_A = 0.0011043022
SH_B = 0.00012107036
SH_C = 5.6613584e-07

# El termistor puede estar arriba (entre VCC y el nodo) o abajo (entre el nodo y GND).
# En el esquema de arriba esta ABAJO, conectado a GND.
TERMISTOR_ABAJO = True

# ------------------------------------

# ---------------------------------------------------------------
# CALIBRACION (opcional, para reemplazar SH_A / SH_B / SH_C de arriba)
# ---------------------------------------------------------------
# Si queres calcular tus propios coeficientes con 3 puntos reales:
# 1) Medi la resistencia del termistor (con un multimetro) a 3
#    temperaturas distintas y conocidas. Ej:
#      T1 = 0 C   (hielo derritiendose)  -> R1 medida
#      T2 = 25 C  (ambiente estable)     -> R2 medida
#      T3 = 50 C  (agua tibia, con cuidado) -> R3 medida
# 2) Corre esto UNA VEZ (por separado, en la PC con Python normal,
#    no hace falta en la Pico) para obtener A, B, C:
#
# import math
# def steinhart_hart_coeffs(t1_c, r1, t2_c, r2, t3_c, r3):
#     T1, T2, T3 = t1_c + 273.15, t2_c + 273.15, t3_c + 273.15
#     L1, L2, L3 = math.log(r1), math.log(r2), math.log(r3)
#     Y1, Y2, Y3 = 1 / T1, 1 / T2, 1 / T3
#     gamma2 = (Y2 - Y1) / (L2 - L1)
#     gamma3 = (Y3 - Y1) / (L3 - L1)
#     C = ((gamma3 - gamma2) / (L3 - L2)) * (1 / (L1 + L2 + L3))
#     B = gamma2 - C * (L1**2 + L1*L2 + L2**2)
#     A = Y1 - (B + L1**2 * C) * L1
#     return A, B, C
#
# print(steinhart_hart_coeffs(0, 32500, 25, 100000, 50, 36000))
#   (los valores de R son ejemplos, reemplazar por tus mediciones reales)
# ---------------------------------------------------------------

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

    # Ecuacion de Steinhart-Hart completa
    ln_r = math.log(r_term)
    inv_t = SH_A + SH_B * ln_r + SH_C * (ln_r ** 3)
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