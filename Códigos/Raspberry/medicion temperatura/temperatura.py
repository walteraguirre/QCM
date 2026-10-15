from machine import ADC
import time
import math


# ============================================================
# ADC
# ============================================================

ntc1 = ADC(26)   # GP26 = ADC0 -> Termistor T12
ntc2 = ADC(27)   # GP27 = ADC1 -> Termistor T34


# ============================================================
# PARÁMETROS DEL DIVISOR
# ============================================================

R_FIJA = 100_000.0

ADC_MAX = 65535.0
VCC = 3.3


# ============================================================
# COEFICIENTES STEINHART-HART
# ============================================================

# Termistor 1 — T12 — GP26
SH_A_1 = 1.2962790321e-03
SH_B_1 = 1.1750245787e-04
SH_C_1 = 4.7006761239e-07

# Termistor 2 — T34 — GP27
SH_A_2 = 1.9648148223e-05
SH_B_2 = 3.0869406086e-04
SH_C_2 = -1.3403841252e-07


# ============================================================
# CONFIGURACIÓN
# ============================================================

ARCHIVO_CSV = "temperaturas.csv"

INTERVALO = 1.0      # segundos entre mediciones


# ============================================================
# FUNCIÓN TEMPERATURA
# ============================================================

def leer_ntc(adc, A, B, C):

    raw = adc.read_u16()

    # Tensión medida
    voltaje = raw / ADC_MAX * VCC

    # Evitar valores inválidos
    if voltaje <= 0 or voltaje >= VCC:
        return raw, voltaje, None, None

    # Divisor:
    #
    # 3.3V --- R_FIJA --- ADC --- NTC --- GND
    #
    r_ntc = R_FIJA * voltaje / (VCC - voltaje)

    # --------------------------------------------------------
    # Modelo Steinhart-Hart
    #
    # 1/T = A + B*ln(R) + C*[ln(R)]^3
    # --------------------------------------------------------

    ln_r = math.log(r_ntc)

    inv_t = (
        A
        + B * ln_r
        + C * ln_r**3
    )

    temperatura_k = 1.0 / inv_t
    temperatura_c = temperatura_k - 273.15

    return raw, voltaje, r_ntc, temperatura_c


# ============================================================
# CREAR CSV
# ============================================================

archivo = open(ARCHIVO_CSV, "w")

archivo.write(
    "tiempo_s,temp1_c,temp2_c,adc1,adc2,voltaje1_v,voltaje2_v\n"
)

archivo.flush()


# ============================================================
# INICIO DE MEDICIÓN
# ============================================================

t0 = time.ticks_ms()

print("==========================================")
print("Medición de temperatura iniciada")
print("Modelo: Steinhart-Hart")
print("GP26 -> T12")
print("GP27 -> T34")
print("Archivo:", ARCHIVO_CSV)
print("Presionar STOP en Thonny para finalizar")
print("==========================================")


# ============================================================
# LOOP
# ============================================================

try:

    while True:

        # ----------------------------------------------------
        # Tiempo transcurrido
        # ----------------------------------------------------

        tiempo_s = time.ticks_diff(
            time.ticks_ms(),
            t0
        ) / 1000.0


        # ----------------------------------------------------
        # Leer NTC
        # ----------------------------------------------------

        raw1, v1, r1, t1 = leer_ntc(
            ntc1,
            SH_A_1,
            SH_B_1,
            SH_C_1
        )

        raw2, v2, r2, t2 = leer_ntc(
            ntc2,
            SH_A_2,
            SH_B_2,
            SH_C_2
        )


        # ----------------------------------------------------
        # Mostrar en Thonny
        # ----------------------------------------------------

        print("==========================================")
        print("Tiempo: {:.1f} s".format(tiempo_s))


        if t1 is not None:

            print(
                "GP26 T12 | ADC: {:5d} | V: {:.3f} V | "
                "R: {:.1f} kΩ | T: {:.2f} °C".format(
                    raw1,
                    v1,
                    r1 / 1000,
                    t1
                )
            )

        else:

            print("GP26 T12 | Lectura fuera de rango")


        if t2 is not None:

            print(
                "GP27 T34 | ADC: {:5d} | V: {:.3f} V | "
                "R: {:.1f} kΩ | T: {:.2f} °C".format(
                    raw2,
                    v2,
                    r2 / 1000,
                    t2
                )
            )

        else:

            print("GP27 T34 | Lectura fuera de rango")


        # ----------------------------------------------------
        # GUARDAR EN CSV
        # ----------------------------------------------------

        if t1 is not None and t2 is not None:

            linea = (
                "{:.3f},{:.3f},{:.3f},{},{},{:.4f},{:.4f}\n"
                .format(
                    tiempo_s,
                    t1,
                    t2,
                    raw1,
                    raw2,
                    v1,
                    v2
                )
            )

            archivo.write(linea)
            archivo.flush()


        # ----------------------------------------------------
        # Esperar siguiente medición
        # ----------------------------------------------------

        time.sleep(INTERVALO)


# ============================================================
# FINALIZACIÓN
# ============================================================

except KeyboardInterrupt:

    print("")
    print("==========================================")
    print("Medición detenida")
    print("Archivo guardado:", ARCHIVO_CSV)
    print("==========================================")


finally:

    archivo.close()