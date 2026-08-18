"""
Calibracion Steinhart-Hart para termistor NTC 100k (Raspberry Pi Pico)
------------------------------------------------------------------------
Este script te guia para tomar 3 mediciones a temperaturas conocidas
(usando un termometro de referencia) y calcula los coeficientes
A, B, C de Steinhart-Hart automaticamente.

COMO USARLO:
1. Conecta el termistor como siempre (ver esquema del script principal).
2. Corre este archivo desde Thonny (Run > Run current script), con la
   Shell/consola visible.
3. Cuando el termistor este estable a una temperatura conocida (por
   ejemplo, en un vaso con hielo y agua, esperando ~2-3 min a que se
   estabilice), fijate la temperatura con tu termometro de referencia
   y escribila cuando el script te la pida.
4. Repeti para 3 temperaturas bien distintas entre si (ej: 0 C, 20-25 C
   ambiente, 45-50 C agua tibia). Cuanto mas separadas, mejor calibracion.
5. Al final el script te imprime SH_A, SH_B, SH_C listos para copiar
   al script principal (termistor_pico.py).

Nota: mientras tomas cada punto, el script muestra la lectura de
voltaje en vivo cada 1 segundo para que puedas confirmar que ya se
estabilizo antes de aceptar el punto.
"""

from machine import ADC, Pin
import utime
import math

# ---------- Configuracion (debe coincidir con el script principal) ----------
ADC_PIN = 26
R_FIJO = 100_000
VCC = 3.3
TERMISTOR_ABAJO = True
# ------------------------------------------------------------------------

adc = ADC(Pin(ADC_PIN))


def leer_voltaje_promedio(muestras=20, espera_ms=50):
    """Promedia varias lecturas para reducir ruido."""
    total = 0
    for _ in range(muestras):
        total += adc.read_u16()
        utime.sleep_ms(espera_ms)
    promedio = total / muestras
    return (promedio / 65535) * VCC


def voltaje_a_resistencia(voltaje):
    if TERMISTOR_ABAJO:
        return R_FIJO * (voltaje / (VCC - voltaje))
    else:
        return R_FIJO * ((VCC - voltaje) / voltaje)


def steinhart_hart_coeffs(t1_c, r1, t2_c, r2, t3_c, r3):
    T1, T2, T3 = t1_c + 273.15, t2_c + 273.15, t3_c + 273.15
    L1, L2, L3 = math.log(r1), math.log(r2), math.log(r3)
    Y1, Y2, Y3 = 1 / T1, 1 / T2, 1 / T3
    gamma2 = (Y2 - Y1) / (L2 - L1)
    gamma3 = (Y3 - Y1) / (L3 - L1)
    C = ((gamma3 - gamma2) / (L3 - L2)) * (1 / (L1 + L2 + L3))
    B = gamma2 - C * (L1 ** 2 + L1 * L2 + L2 ** 2)
    A = Y1 - (B + L1 ** 2 * C) * L1
    return A, B, C


def tomar_punto(numero):
    print("\n--- Punto de calibracion {} ---".format(numero))
    print("Estabiliza el termistor a una temperatura conocida.")
    print("Mientras esperas, podes ver el voltaje en vivo (Ctrl+C para pasar a ingresar el dato cuando este estable).")
    try:
        while True:
            v = leer_voltaje_promedio(muestras=5, espera_ms=100)
            print("Voltaje actual: {:.4f} V".format(v))
            utime.sleep(1)
    except KeyboardInterrupt:
        pass

    v_final = leer_voltaje_promedio(muestras=30, espera_ms=50)
    r_final = voltaje_a_resistencia(v_final)
    print("Voltaje final (promediado): {:.4f} V  ->  R = {:.1f} ohm".format(v_final, r_final))

    temp_str = input("Ingresa la temperatura de referencia en C para este punto: ")
    temp = float(temp_str)
    return temp, r_final


def main():
    print("=== Calibracion Steinhart-Hart ===")
    print("Vas a tomar 3 puntos a temperaturas distintas.")
    print("Sugerencia: hielo+agua (~0 C), ambiente (~20-25 C), agua tibia (~45-50 C)\n")

    t1, r1 = tomar_punto(1)
    t2, r2 = tomar_punto(2)
    t3, r3 = tomar_punto(3)

    A, B, C = steinhart_hart_coeffs(t1, r1, t2, r2, t3, r3)

    print("\n=== Resultado ===")
    print("Punto 1: {:.2f} C  ->  {:.1f} ohm".format(t1, r1))
    print("Punto 2: {:.2f} C  ->  {:.1f} ohm".format(t2, r2))
    print("Punto 3: {:.2f} C  ->  {:.1f} ohm".format(t3, r3))
    print()
    print("Copia estas lineas al script principal (termistor_pico.py):")
    print("SH_A = {}".format(A))
    print("SH_B = {}".format(B))
    print("SH_C = {}".format(C))


main()