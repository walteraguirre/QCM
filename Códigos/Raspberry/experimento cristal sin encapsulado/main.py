from micropython import const
import rp2
from rp2 import PIO, asm_pio
from machine import Pin
import uarray as array
import utime

# ─── Constantes globales ───────────────────────────────────────────────────────
MAX_COUNT    = const((1 << 32) - 1)
# Ajustá este valor midiendo tu Pico con una señal de referencia conocida.
# El valor por defecto asume un cristal ideal de 125 MHz.
REF_FREQ_HZ  = 125_000_000.0

# ─── PIO programs ─────────────────────────────────────────────────────────────

@asm_pio(sideset_init=PIO.OUT_HIGH)
def gate():
    """Genera la señal de gate y coordina el ciclo de medición."""
    mov(x, osr)           # carga el tiempo de gate (en ciclos de clock) desde OSR
    wait(0, pin, 0)       # espera flanco bajo de la entrada
    wait(1, pin, 0)       # espera flanco alto → detección de flanco ascendente
    label("loopstart")
    jmp(x_dec, "loopstart") .side(0)  # mantiene gate en bajo durante el tiempo programado
    wait(0, pin, 0)       # espera flanco bajo
    wait(1, pin, 0) .side(1)          # pone gate en alto en el siguiente flanco ascendente
    irq(block, 0)         # dispara IRQ 0 y espera que el handler la atienda
    wait(1, irq, 4)       # espera IRQ de la SM contadora de clock
    wait(1, irq, 5)       # espera IRQ de la SM contadora de pulsos

@asm_pio()
def clock_count():
    """Cuenta ciclos del clock interno mientras gate está en bajo."""
    mov(x, osr)           # carga valor máximo (2^32-1)
    wait(1, pin, 0)       # espera flanco de bajada
    wait(0, pin, 0)       # de la señal de gate
    label("counter")
    jmp(pin, "output")    # si gate vuelve a alto, sale
    jmp(x_dec, "counter") # decrementa x (cuenta cada 2 ciclos → multiplicar por 2)
    label("output")
    mov(isr, x)           # mueve el conteo al ISR
    push()                # envía dato al FIFO
    irq(block, 4)         # dispara IRQ 4 y espera que gate la libere

@asm_pio(sideset_init=PIO.OUT_HIGH)
def pulse_count():
    """Cuenta pulsos de la señal de entrada mientras gate está en bajo."""
    mov(x, osr)           # carga valor máximo
    wait(1, pin, 0)
    wait(0, pin, 0) .side(0)          # detecta flanco de bajada del gate
    label("counter")
    wait(0, pin, 1)       # espera flanco ascendente
    wait(1, pin, 1)       # de la señal de entrada
    jmp(pin, "output")    # si gate vuelve a alto, sale
    jmp(x_dec, "counter") # decrementa x contando pulsos
    label("output")
    mov(isr, x) .side(1)  # mueve conteo al ISR y pone pin en alto
    push()                # envía dato al FIFO
    irq(block, 5)         # dispara IRQ 5 y espera que gate la libere


# ─── Inicialización de State Machines ─────────────────────────────────────────

def init_sm(freq, input_pin, gate_pin, pulse_fin_pin, gate_time_cycles=None):
    """
    Inicializa y arranca las tres state machines.

    Parámetros:
        freq            : frecuencia de clock del PIO (Hz), normalmente 125_000_000
        input_pin       : Pin de entrada de la señal a medir
        gate_pin        : Pin de salida para la señal de gate
        pulse_fin_pin   : Pin auxiliar para sincronización entre SMs
        gate_time_cycles: tiempo de gate en ciclos de clock (default: freq // 1000 → 1 ms)
    """
    if gate_time_cycles is None:
        gate_time_cycles = freq // 1000  # 1 ms por defecto

    gate_pin.value(1)
    pulse_fin_pin.value(1)

    sm0 = rp2.StateMachine(0, gate,        freq=freq, in_base=input_pin,   sideset_base=gate_pin)
    sm1 = rp2.StateMachine(1, clock_count, freq=freq, in_base=gate_pin,    jmp_pin=pulse_fin_pin)
    sm2 = rp2.StateMachine(2, pulse_count, freq=freq, in_base=gate_pin,    sideset_base=pulse_fin_pin, jmp_pin=gate_pin)

    sm0.put(gate_time_cycles)
    sm0.exec("pull()")

    sm1.put(MAX_COUNT)
    sm1.exec("pull()")

    sm2.put(MAX_COUNT - 1)
    sm2.exec("pull()")

    sm1.active(1)
    sm2.active(1)
    sm0.active(1)

    return sm0, sm1, sm2


# ─── Programa principal ────────────────────────────────────────────────────────

if __name__ == "__main__":

    # ── Modo de prueba con PWM interno ────────────────────────────────────────
    TEST_MODE    = False
    PWM_PIN      = Pin(0)
    TEST_FREQ_HZ = 6_000_000  # frecuencia objetivo de prueba

    # ── Configuración ──────────────────────────────────────────────────────────
    PIO_FREQ       = 125_000_000          # frecuencia del PIO en Hz
    INPUT_PIN      = Pin(15, Pin.IN, Pin.PULL_UP)
    GATE_PIN       = Pin(14, Pin.OUT)
    PULSE_FIN_PIN  = Pin(13, Pin.OUT)

    # Tiempo de gate: se mantiene en 10 ms para tener buena resolución de medición.
    # (100 mediciones/seg internamente, aunque después solo guardemos 1 por segundo)
    GATE_TIME = PIO_FREQ // 100  # 10 ms

    # ── Configuración del logueo a archivo ─────────────────────────────────────
    DURACION_TOTAL_S      = 60      # duración total de la prueba (segundos)
    INTERVALO_GUARDADO_MS = 1000    # guardar un dato en el archivo cada 1000 ms
    ARCHIVO_SALIDA        = "medicion.txt"

    # ── Estado compartido entre IRQ y loop principal ───────────────────────────
    update_flag = False
    data = array.array("I", [0, 0])  # [clock_raw, pulse_raw]

    # Guardamos las muestras en RAM y recién escribimos el archivo al final.
    # Así evitamos escrituras a flash repetidas durante la prueba (menos
    # jitter, menos desgaste, y no dependemos de que el flush no se cuelgue).
    muestras = []

    def counter_handler(sm):
        """IRQ handler: lee los FIFOs y señala al loop principal."""
        global update_flag
        if not update_flag:
            # Recarga el gate para la siguiente medición
            sm.put(GATE_TIME)
            sm.exec("pull()")
            # Lee en orden: primero clock (SM1), luego pulsos (SM2)
            data[0] = sm1.get()
            data[1] = sm2.get()
            update_flag = True

    # ── Arranque del generador de prueba (si TEST_MODE está activo) ───────────
    if TEST_MODE:
        from machine import PWM
        pwm = PWM(PWM_PIN)
        pwm.freq(TEST_FREQ_HZ)
        pwm.duty_u16(32768)  # 50% duty cycle
        real_pwm_freq = pwm.freq()
        print(f"[TEST] PWM generando {real_pwm_freq} Hz en pin {PWM_PIN}")
        print(f"[TEST] Conectá un jumper: pin {PWM_PIN} → pin {INPUT_PIN}")
        print()

    # ── Arranque ───────────────────────────────────────────────────────────────
    sm0, sm1, sm2 = init_sm(
        PIO_FREQ,
        INPUT_PIN,
        GATE_PIN,
        PULSE_FIN_PIN,
        GATE_TIME,
    )
    sm0.irq(counter_handler)

    print("Contador de frecuencia iniciado")
    print(f"Tiempo de gate       : {GATE_TIME / PIO_FREQ * 1000:.1f} ms")
    print(f"Freq referencia      : {REF_FREQ_HZ:.1f} Hz")
    print(f"Duración de la prueba: {DURACION_TOTAL_S} s")
    print(f"Guardado a archivo   : cada {INTERVALO_GUARDADO_MS} ms → {ARCHIVO_SALIDA}")
    print("(la consola solo imprime 1 vez por segundo para no saturar el USB)")
    print("-" * 40)

    t_inicio          = utime.ticks_ms()
    t_ultimo_guardado = t_inicio
    i = 0

    try:
        while True:
            if update_flag:
                # ── Cálculo ────────────────────────────────────────────────────
                clock_cycles = 2 * (MAX_COUNT - data[0] + 1)
                pulse_cycles = (MAX_COUNT - data[1]) + 1

                if clock_cycles > 0:
                    frequency = pulse_cycles * (REF_FREQ_HZ / clock_cycles)
                else:
                    frequency = 0.0

                t_ahora        = utime.ticks_ms()
                t_transcurrido = utime.ticks_diff(t_ahora, t_inicio)

                # ── Guardado en RAM + salida por consola (solo 1 vez por segundo) ─
                if utime.ticks_diff(t_ahora, t_ultimo_guardado) >= INTERVALO_GUARDADO_MS:
                    muestras.append((t_transcurrido / 1000, frequency))

                    if TEST_MODE:
                        error_hz  = frequency - real_pwm_freq
                        error_ppm = (error_hz / real_pwm_freq) * 1e6
                        print(f"[{i:5d}]  freq={frequency:14.3f} Hz  error={error_hz:+.3f} Hz  ({error_ppm:+.2f} ppm)")
                    else:
                        print(f"[{i:5d}]  t={t_transcurrido/1000:6.2f}s  freq={frequency:14.3f} Hz")

                    t_ultimo_guardado = t_ahora
                    i += 1

                # ── Corte automático a los 60s ────────────────────────────────────
                if t_transcurrido >= DURACION_TOTAL_S * 1000:
                    break

                update_flag = False

    except Exception as e:
        print("-" * 40)
        print("¡Se produjo un error durante la medición!")
        import sys
        sys.print_exception(e)

    finally:
        sm0.active(0)
        sm1.active(0)
        sm2.active(0)

        # Escribimos todo el archivo de una sola vez al final
        with open(ARCHIVO_SALIDA, "w") as f:
            f.write("tiempo_s\tfrecuencia_hz\n")
            for t_s, freq in muestras:
                f.write(f"{t_s:.3f}\t{freq:.3f}\n")

        print("-" * 40)
        print(f"Medición finalizada. {len(muestras)} muestras guardadas en '{ARCHIVO_SALIDA}'")