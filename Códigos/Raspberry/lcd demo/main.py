# ============================================================
# IMPORTS
# ============================================================

import math
import time
import rp2
import uarray as array

from rp2 import PIO, asm_pio
from micropython import const
from machine import Pin, I2C, ADC
from pico_i2c_lcd import I2cLcd



# ============================================================
# CONSTANTES QCM
# ============================================================

MAX_COUNT = const((1 << 32) - 1)

# Frecuencia de referencia del RP2040
REF_FREQ_HZ = 125_000_000.0

# Propiedades del cuarzo
RHO_QUARTZ = 2648.0       # kg/m3
MU_QUARTZ = 2.947e10      # N/m2

# Ventana de medicion: 1 segundo
GATE_TIME = 125_000_000


# ============================================================
# TERMISTOR NTC
# ============================================================

NTC_B = 3950
NTC_R0 = 100_000
NTC_T0 = 298.15           # 25 °C en Kelvin

R_FIJA = 100_000
ADC_MAX = 65535


# ============================================================
# PINES QCM
# ============================================================

# Entrada de frecuencia
PIN_SENSOR = Pin(15, Pin.IN, Pin.PULL_UP)
PIN_REF    = Pin(10, Pin.IN, Pin.PULL_UP)

# Pines auxiliares PIO - Canal A
PIN_GATE_A      = Pin(14, Pin.OUT)
PIN_PULSE_FIN_A = Pin(13, Pin.OUT)

# Pines auxiliares PIO - Canal B
PIN_GATE_B      = Pin(9, Pin.OUT)
PIN_PULSE_FIN_B = Pin(8, Pin.OUT)

# Termistor
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


# ============================================================
# INICIALIZAR CANAL DE MEDICION
# ============================================================

def init_channel(pio_id, input_pin, gate_pin, pulse_fin_pin):

    base = pio_id * 4

    gate_pin.value(1)
    pulse_fin_pin.value(1)

    # --------------------------------------------------------
    # State Machine: Gate
    # --------------------------------------------------------

    sm_g = rp2.StateMachine(
        base + 0,
        gate,
        freq=125_000_000,
        in_base=input_pin,
        sideset_base=gate_pin
    )

    # --------------------------------------------------------
    # State Machine: contador de reloj
    # --------------------------------------------------------

    sm_c = rp2.StateMachine(
        base + 1,
        clock_count,
        freq=125_000_000,
        in_base=gate_pin,
        jmp_pin=pulse_fin_pin
    )

    # --------------------------------------------------------
    # State Machine: contador de pulsos
    # --------------------------------------------------------

    sm_p = rp2.StateMachine(
        base + 2,
        pulse_count,
        freq=125_000_000,
        in_base=gate_pin,
        sideset_base=pulse_fin_pin,
        jmp_pin=gate_pin
    )

    # --------------------------------------------------------
    # Cargar contadores
    # --------------------------------------------------------

    sm_g.put(GATE_TIME)
    sm_g.exec("pull()")

    sm_c.put(MAX_COUNT)
    sm_c.exec("pull()")

    sm_p.put(MAX_COUNT - 1)
    sm_p.exec("pull()")

    # --------------------------------------------------------
    # Activar State Machines
    # --------------------------------------------------------

    sm_c.active(1)
    sm_p.active(1)
    sm_g.active(1)

    return sm_g, sm_c, sm_p


# ============================================================
# CALCULAR FRECUENCIA
# ============================================================

def calc_freq(raw_clk, raw_pulse):

    clk_count = 2 * (MAX_COUNT - raw_clk + 1)

    pulse_count_value = (
        MAX_COUNT - raw_pulse
    ) + 1

    if clk_count <= 0:
        return 0.0

    frequency = (
        pulse_count_value
        * (REF_FREQ_HZ / clk_count)
    )

    return frequency


# ============================================================
# TEMPERATURA
# ============================================================

def read_temperature():

    total = 0

    muestras = 10

    # --------------------------------------------------------
    # Promedio ADC
    # --------------------------------------------------------

    for _ in range(muestras):

        total += ADC_THERMISTOR.read_u16()

        time.sleep_ms(2)

    raw = total // muestras

    # Evitar divisiones problematicas
    raw = max(
        1,
        min(raw, ADC_MAX - 1)
    )

    # --------------------------------------------------------
    # ADC -> tension
    # --------------------------------------------------------

    v = (
        raw
        / ADC_MAX
        * 3.3
    )

    # --------------------------------------------------------
    # Tension -> resistencia NTC
    #
    # R fija arriba a 3.3 V
    # NTC abajo a GND
    # --------------------------------------------------------

    r_ntc = (
        R_FIJA
        * v
        / (3.3 - v)
    )

    # --------------------------------------------------------
    # Modelo Beta
    # --------------------------------------------------------

    tk = 1.0 / (
        1.0 / NTC_T0
        +
        math.log(
            r_ntc / NTC_R0
        ) / NTC_B
    )

    return tk - 273.15


# ============================================================
# CALCULO DE ESPESOR - SAUERBREY
# ============================================================

def calculate_thickness(df):

    # Todavia no hay frecuencia de referencia
    if f0_real is None:

        return 0.0

    # --------------------------------------------------------
    # Constante Sauerbrey
    # --------------------------------------------------------

    Cf = (
        math.sqrt(
            RHO_QUARTZ
            * MU_QUARTZ
        )
        /
        (2 * f0_real**2)
    )

    # --------------------------------------------------------
    # Cambio de masa por unidad de area
    # kg/m2
    # --------------------------------------------------------

    dm = -Cf * df

    # --------------------------------------------------------
    # Espesor
    # --------------------------------------------------------

    thickness_m = (
        dm / density
    )

    # metros -> nanometros
    thickness_nm = (
        thickness_m * 1e9
    )

    return thickness_nm


# ============================================================
# VARIABLES DE MEDICION
# ============================================================

# Valores que va a mostrar el LCD

frequency_sensor = 0.0
frequency_ref = 0.0

delta_f = 0.0
thickness = 0.0
temperature = 0.0


# ============================================================
# CALIBRACION INICIAL
# ============================================================

CALIB_SAMPLES = 30

calibrating = False
calib_count = 0
calib_sum = 0.0

f0_real = None


# Estado de la medicion
measurement_running = False


# ============================================================
# STATE MACHINES
# ============================================================

sm_gate_a = None
sm_clk_a = None
sm_pulse_a = None

sm_gate_b = None
sm_clk_b = None
sm_pulse_b = None


# ============================================================
# BUFFERS
# ============================================================

data_a = array.array(
    "I",
    [0, 0]
)

ready_a = array.array(
    "b",
    [0]
)


data_b = array.array(
    "I",
    [0, 0]
)

ready_b = array.array(
    "b",
    [0]
)


# ============================================================
# INICIAR MEDICION
# ============================================================

def start_measurement():

    global measurement_running

    global sm_gate_a
    global sm_clk_a
    global sm_pulse_a

    global sm_gate_b
    global sm_clk_b
    global sm_pulse_b

    global f0_real

    global calibrating
    global calib_count
    global calib_sum


    # --------------------------------------------------------
    # Reset
    # --------------------------------------------------------

    ready_a[0] = 0
    ready_b[0] = 0

    f0_real = None

    # --------------------------------------------------------
    # Iniciar calibracion
    # --------------------------------------------------------

    calibrating = True
    calib_count = 0
    calib_sum = 0.0


    # ========================================================
    # CANAL A - SENSOR
    # ========================================================

    sm_gate_a, sm_clk_a, sm_pulse_a = init_channel(
        0,
        PIN_SENSOR,
        PIN_GATE_A,
        PIN_PULSE_FIN_A
    )


    def handler_a(sm):

        if not ready_a[0]:

            sm.put(GATE_TIME)
            sm.exec("pull()")

            data_a[0] = sm_clk_a.get()
            data_a[1] = sm_pulse_a.get()

            ready_a[0] = 1


    sm_gate_a.irq(handler_a)


    # ========================================================
    # CANAL B - REFERENCIA
    # ========================================================

    if num_crystals == 2:

        sm_gate_b, sm_clk_b, sm_pulse_b = init_channel(
            1,
            PIN_REF,
            PIN_GATE_B,
            PIN_PULSE_FIN_B
        )


        def handler_b(sm):

            if not ready_b[0]:

                sm.put(GATE_TIME)
                sm.exec("pull()")

                data_b[0] = sm_clk_b.get()
                data_b[1] = sm_pulse_b.get()

                ready_b[0] = 1


        sm_gate_b.irq(handler_b)


    measurement_running = True

# ============================================================
# DETENER MEDICION
# ============================================================

def stop_measurement():

    global measurement_running
    global f0_real

    global sm_gate_a
    global sm_clk_a
    global sm_pulse_a

    global sm_gate_b
    global sm_clk_b
    global sm_pulse_b
    global calibrating
    global calib_count
    global calib_sum

    # ========================================================
    # DETENER CANAL A
    # ========================================================

    if sm_gate_a is not None:

        sm_gate_a.active(0)
        sm_clk_a.active(0)
        sm_pulse_a.active(0)


    # ========================================================
    # DETENER CANAL B
    # ========================================================

    if sm_gate_b is not None:

        sm_gate_b.active(0)
        sm_clk_b.active(0)
        sm_pulse_b.active(0)


    # ========================================================
    # RESET
    # ========================================================

    calibrating = False
    calib_count = 0
    calib_sum = 0.0

    f0_real = None
    ready_a[0] = 0
    ready_b[0] = 0


    # Borrar referencias anteriores

    sm_gate_a = None
    sm_clk_a = None
    sm_pulse_a = None

    sm_gate_b = None
    sm_clk_b = None
    sm_pulse_b = None
    
    measurement_running = False


# ============================================================
# ACTUALIZAR MEDICION
# ============================================================

def update_measurement():

    global frequency_sensor
    global frequency_ref

    global delta_f
    global thickness
    global temperature

    global f0_real
    global calibrating
    global calib_count
    global calib_sum


    # Si no estamos midiendo, no hacer nada

    if not measurement_running:

        return


    if num_crystals == 1:

        if ready_a[0]:

            # ====================================================
            # CALCULAR FRECUENCIA
            # ====================================================

            frequency_sensor = calc_freq(
                data_a[0],
                data_a[1]
            )


            # ====================================================
            # CALIBRACION INICIAL
            # ====================================================

            if calibrating:

                calib_sum += frequency_sensor
                calib_count += 1


                # -----------------------------------------------
                # Mostrar progreso
                # -----------------------------------------------

                if screen == "measurement":

                    line(
                        "Calibrando...",
                        0
                    )

                    line(
                        "Muestra {}/{}".format(
                            calib_count,
                            CALIB_SAMPLES
                        ),
                        1
                    )


                print(
                    "CAL {}/{} | f={:.3f} Hz".format(
                        calib_count,
                        CALIB_SAMPLES,
                        frequency_sensor
                    )
                )


                # -----------------------------------------------
                # Termino calibracion
                # -----------------------------------------------

                if calib_count >= CALIB_SAMPLES:

                    f0_real = (
                        calib_sum
                        / CALIB_SAMPLES
                    )

                    frequency_ref = f0_real

                    calibrating = False

                    delta_f = 0.0
                    thickness = 0.0


                    print(
                        "CALIBRACION COMPLETA | "
                        "f0={:.3f} Hz".format(
                            f0_real
                        )
                    )


                    if screen == "measurement":

                        line(
                            "Calibracion OK",
                            0
                        )

                        line(
                            "f0:{:.0f} Hz".format(
                                f0_real
                            ),
                            1
                        )


                    # Mostrar resultado brevemente
                    time.sleep_ms(1000)


            # ====================================================
            # MEDICION NORMAL
            # ====================================================

            else:

                frequency_ref = f0_real


                # ------------------------------------------------
                # Delta f
                # ------------------------------------------------

                delta_f = (
                    frequency_sensor
                    - f0_real
                )


                # ------------------------------------------------
                # Espesor
                # ------------------------------------------------

                thickness = calculate_thickness(
                    delta_f
                )


                # ------------------------------------------------
                # Temperatura
                # ------------------------------------------------

                if use_temperature:

                    temperature = read_temperature()


                # ------------------------------------------------
                # Debug
                # ------------------------------------------------

                print(
                    "f={:.3f} Hz | "
                    "f0={:.3f} Hz | "
                    "df={:.3f} Hz | "
                    "d={:.4f} nm".format(
                        frequency_sensor,
                        f0_real,
                        delta_f,
                        thickness
                    )
                )


            # ====================================================
            # LIBERAR BUFFER
            # ====================================================

            ready_a[0] = 0

    # ========================================================
    # MODO 2 CRISTALES
    # ========================================================

    else:

        # Necesitamos una medicion de ambos canales

        if ready_a[0] and ready_b[0]:

            # ------------------------------------------------
            # FRECUENCIA SENSOR
            # ------------------------------------------------

            frequency_sensor = calc_freq(
                data_a[0],
                data_a[1]
            )


            # ------------------------------------------------
            # FRECUENCIA REFERENCIA
            # ------------------------------------------------

            frequency_ref = calc_freq(
                data_b[0],
                data_b[1]
            )


            # ------------------------------------------------
            # DELTA F
            # ------------------------------------------------

            delta_f = (
                frequency_sensor
                - frequency_ref
            )


            # ------------------------------------------------
            # f0 temporal
            #
            # Esto tambien lo mejoraremos con calibracion.
            # ------------------------------------------------

            if f0_real is None:

                f0_real = frequency_sensor


            # ------------------------------------------------
            # ESPESOR
            # ------------------------------------------------

            thickness = calculate_thickness(
                delta_f
            )


            # ------------------------------------------------
            # TEMPERATURA
            # ------------------------------------------------

            if use_temperature:

                temperature = read_temperature()


            # ------------------------------------------------
            # Liberar buffers
            # ------------------------------------------------

            ready_a[0] = 0
            ready_b[0] = 0


            # ------------------------------------------------
            # DEBUG SERIAL
            # ------------------------------------------------

            print(
                "f_sensor={:.3f} Hz | "
                "f_ref={:.3f} Hz | "
                "df={:.3f} Hz | "
                "d={:.4f} nm".format(
                    frequency_sensor,
                    frequency_ref,
                    delta_f,
                    thickness
                )
            )


    # ========================================================
    # ACTUALIZAR LCD
    # ========================================================

    if screen == "measurement" and not calibrating:

        show_measurement()


# ============================================================
# LCD 16x2
# ============================================================
#
# SDA -> GP20
# SCL -> GP21
#

i2c = I2C(
    0,
    sda=Pin(20),
    scl=Pin(21),
    freq=400000
)

I2C_ADDR = 0x27

lcd = I2cLcd(i2c, I2C_ADDR, 2, 16)


# ============================================================
# ENCODER ROTATIVO
# ============================================================
#
# CLK -> GP18
# DT  -> GP16
# SW  -> GP17
# +   -> 3V3
# GND -> GND
#

clk = Pin(18, Pin.IN, Pin.PULL_UP)
dt  = Pin(16, Pin.IN, Pin.PULL_UP)
sw  = Pin(17, Pin.IN, Pin.PULL_UP)


# ============================================================
# PARAMETROS DEL SISTEMA
# ============================================================
#
# Estos son los parametros que luego utilizara el QCM.
#

density = 2700          # kg/m3
sensor_area = 1.327e-4  # m2

num_crystals = 1
use_temperature = True


# ============================================================
# ESTADO DE LA INTERFAZ
# ============================================================

screen = "main"

main_index = 0
config_index = 0

# Pagina actual dentro de Medicion
measurement_page = 0


main_options = [
    "Medicion",
    "Configuracion"
]


config_options = [
    "Densidad",
    "Area sensor",
    "Cristales",
    "Temperatura",
    "Volver"
]


# ============================================================
# FUNCIONES LCD
# ============================================================

def line(texto, fila):
    """
    Escribe una linea completa del LCD.

    El LCD tiene 16 caracteres.
    Se agregan espacios manualmente porque algunas versiones
    de MicroPython no implementan str.ljust().
    """

    texto = str(texto)

    texto = texto[:16]

    texto = texto + " " * (16 - len(texto))

    lcd.move_to(0, fila)
    lcd.putstr(texto)


# ============================================================
# MENU PRINCIPAL
# ============================================================

def show_main():

    if main_index == 0:

        line("> Medicion", 0)
        line("  Configuracion", 1)

    else:

        line("  Medicion", 0)
        line("> Configuracion", 1)


# ============================================================
# MENU CONFIGURACION
# ============================================================

def show_config():

    current = config_options[config_index]

    # Mostrar opcion actual arriba
    line("> " + current, 0)

    # Mostrar la siguiente abajo
    if config_index < len(config_options) - 1:

        next_option = config_options[config_index + 1]

        line("  " + next_option, 1)

    else:

        line("", 1)


# ============================================================
# EDITAR DENSIDAD
# ============================================================

def show_density():

    line("Densidad:", 0)

    texto = "> {} kg/m3".format(density)

    line(texto, 1)


# ============================================================
# EDITAR AREA
# ============================================================

def show_area():

    line("Area sensor:", 0)

    texto = "> {:.3e} m2".format(sensor_area)

    line(texto, 1)


# ============================================================
# EDITAR NUMERO DE CRISTALES
# ============================================================

def show_crystals():

    line("Cristales:", 0)

    if num_crystals == 1:

        line("> 1 cristal", 1)

    else:

        line("> 2 cristales", 1)


# ============================================================
# EDITAR TEMPERATURA
# ============================================================

def show_temperature():

    line("Temperatura:", 0)

    if use_temperature:

        line("> Activada", 1)

    else:

        line("> Desactivada", 1)


# ============================================================
# PANTALLAS DE MEDICION
# ============================================================

def show_measurement():

    # --------------------------------------------------------
    # PAGINA 0
    #
    # Delta f + espesor
    # --------------------------------------------------------

    if measurement_page == 0:

        line(
            "df: {:+.2f} Hz".format(delta_f),
            0
        )

        line(
            "d:  {:.3f} nm".format(thickness),
            1
        )


    # --------------------------------------------------------
    # PAGINA 1
    #
    # Frecuencia + temperatura
    # --------------------------------------------------------

    elif measurement_page == 1:

        line(
            "f: {:.0f} Hz".format(frequency_sensor),
            0
        )

        if use_temperature:

            line(
                "T: {:.1f} C".format(temperature),
                1
            )

        else:

            line(
                "T: desactivada",
                1
            )


    # --------------------------------------------------------
    # PAGINA 2
    #
    # Configuracion resumida
    # --------------------------------------------------------

    elif measurement_page == 2:

        if num_crystals == 1:

            line(
                "Modo: 1 cristal",
                0
            )

        else:

            line(
                "Modo: 2 cristal",
                0
            )

        line(
            "rho: {} kg/m3".format(density),
            1
        )


# ============================================================
# ACTUALIZAR DISPLAY
# ============================================================

def update_display():

    if screen == "main":

        show_main()


    elif screen == "config":

        show_config()


    elif screen == "density":

        show_density()


    elif screen == "area":

        show_area()


    elif screen == "crystals":

        show_crystals()


    elif screen == "temperature":

        show_temperature()


    elif screen == "measurement":

        show_measurement()


# ============================================================
# EVENTO: GIRAR ENCODER
# ============================================================

def rotate(direction):

    global main_index
    global config_index

    global measurement_page

    global density
    global sensor_area
    global num_crystals
    global use_temperature


    # ========================================================
    # MENU PRINCIPAL
    # ========================================================

    if screen == "main":

        main_index += direction

        if main_index > 1:
            main_index = 0

        if main_index < 0:
            main_index = 1


    # ========================================================
    # MENU CONFIGURACION
    # ========================================================

    elif screen == "config":

        config_index += direction

        if config_index >= len(config_options):
            config_index = 0

        if config_index < 0:
            config_index = len(config_options) - 1


    # ========================================================
    # EDITAR DENSIDAD
    # ========================================================

    elif screen == "density":

        # Paso de 10 kg/m3

        density += direction * 10

        # Limites razonables

        if density < 100:
            density = 100

        if density > 25000:
            density = 25000


    # ========================================================
    # EDITAR AREA
    # ========================================================

    elif screen == "area":

        # Cambio de 1 mm2
        #
        # 1 mm2 = 1e-6 m2

        sensor_area += direction * 1e-6

        if sensor_area < 1e-6:
            sensor_area = 1e-6

        if sensor_area > 1:
            sensor_area = 1


    # ========================================================
    # EDITAR NUMERO DE CRISTALES
    # ========================================================

    elif screen == "crystals":

        if direction != 0:

            if num_crystals == 1:

                num_crystals = 2

            else:

                num_crystals = 1


    # ========================================================
    # ACTIVAR / DESACTIVAR TEMPERATURA
    # ========================================================

    elif screen == "temperature":

        if direction != 0:

            use_temperature = not use_temperature


    # ========================================================
    # PAGINAS DE MEDICION
    # ========================================================

    elif screen == "measurement":

        measurement_page += direction

        if measurement_page > 2:
            measurement_page = 0

        if measurement_page < 0:
            measurement_page = 2


    update_display()


# ============================================================
# EVENTO: APRETAR ENCODER
# ============================================================

def press():

    global screen
    global measurement_page


    # ========================================================
    # MENU PRINCIPAL
    # ========================================================

    if screen == "main":

        # ----------------------------------------------------
        # MEDICION
        # ----------------------------------------------------

        if main_index == 0:

            measurement_page = 0

            screen = "measurement"
            
            start_measurement()


        # ----------------------------------------------------
        # CONFIGURACION
        # ----------------------------------------------------

        elif main_index == 1:

            screen = "config"


    # ========================================================
    # MENU CONFIGURACION
    # ========================================================

    elif screen == "config":

        # ----------------------------------------------------
        # DENSIDAD
        # ----------------------------------------------------

        if config_index == 0:

            screen = "density"


        # ----------------------------------------------------
        # AREA
        # ----------------------------------------------------

        elif config_index == 1:

            screen = "area"


        # ----------------------------------------------------
        # CRISTALES
        # ----------------------------------------------------

        elif config_index == 2:

            screen = "crystals"


        # ----------------------------------------------------
        # TEMPERATURA
        # ----------------------------------------------------

        elif config_index == 3:

            screen = "temperature"


        # ----------------------------------------------------
        # VOLVER
        # ----------------------------------------------------

        elif config_index == 4:

            screen = "main"


    # ========================================================
    # CONFIRMAR DENSIDAD
    # ========================================================

    elif screen == "density":

        screen = "config"


    # ========================================================
    # CONFIRMAR AREA
    # ========================================================

    elif screen == "area":

        screen = "config"


    # ========================================================
    # CONFIRMAR CRISTALES
    # ========================================================

    elif screen == "crystals":

        screen = "config"


    # ========================================================
    # CONFIRMAR TEMPERATURA
    # ========================================================

    elif screen == "temperature":

        screen = "config"


    # ========================================================
    # SALIR DE MEDICION
    # ========================================================

    elif screen == "measurement":
        
        stop_measurement()
        
        screen = "main"


    lcd.clear()

    update_display()


# ============================================================
# PANTALLA DE INICIO
# ============================================================

lcd.clear()

line("  QCM MONITOR", 0)
line("  Iniciando...", 1)

time.sleep(1.5)

lcd.clear()

show_main()


# ============================================================
# VARIABLES DEL ENCODER
# ============================================================

last_clk = clk.value()
last_sw = sw.value()

last_rotation = 0
last_press = 0


# ============================================================
# LOOP PRINCIPAL
# ============================================================

while True:

    # ========================================================
    # GIRO DEL ENCODER
    # ========================================================

    current_clk = clk.value()

    if current_clk != last_clk:

        # Solo contamos el flanco descendente
        if current_clk == 0:

            now = time.ticks_ms()

            if time.ticks_diff(
                now,
                last_rotation
            ) > 5:

                # --------------------------------------------
                # Determinar direccion
                # --------------------------------------------

                if dt.value() != current_clk:

                    direction = 1

                else:

                    direction = -1


                rotate(direction)

                last_rotation = now


        last_clk = current_clk


    # ========================================================
    # PULSADOR DEL ENCODER
    # ========================================================

    current_sw = sw.value()

    # Detectamos flanco 1 -> 0

    if current_sw == 0 and last_sw == 1:

        now = time.ticks_ms()

        if time.ticks_diff(
            now,
            last_press
        ) > 250:

            press()

            last_press = now


    last_sw = current_sw

    update_measurement()
    
    time.sleep_ms(1)