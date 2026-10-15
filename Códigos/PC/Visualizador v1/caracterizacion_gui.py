# =============================================================================
# qcm_vacuum_gui.py — PC
#
# Caracterización térmica de QCM en vacío
#
# Compatible con DOS formatos enviados por la Raspberry Pi Pico:
#
#   Con frecuencia:
#       timestamp_s,f_sensor,temp_1_c,temp_2_c
#
#   Sin frecuencia:
#       timestamp_s,temp_1_c,temp_2_c
#
# Comandos enviados a la Pico:
#   START
#   STOP
#
# Instalación:
#   pip install PyQt5 pyserial pyqtgraph
#
# =============================================================================

import sys
import threading
import serial
import serial.tools.list_ports

from collections import deque

import csv
import os
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QComboBox,
    QGroupBox,
    QStatusBar,
    QFrame,
    QCheckBox,
    QLineEdit
)

from PyQt5.QtCore import (
    Qt,
    QTimer,
    pyqtSignal,
    QObject
)

from PyQt5.QtGui import QFont

import pyqtgraph as pg


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

BAUD_RATE = 115200

# Cantidad máxima de puntos visibles
# El CSV guarda todos los puntos.
MAX_POINTS = 1800

STATE_IDLE = "idle"
STATE_MEAS = "measuring"


# =============================================================================
# SEÑALES
# =============================================================================

class SerialSignals(QObject):

    data_received = pyqtSignal(str)

    status_msg = pyqtSignal(str)

    connected = pyqtSignal(bool)


# =============================================================================
# LECTOR SERIAL
# =============================================================================

class SerialReader(threading.Thread):

    def __init__(self, port, signals):

        super().__init__(daemon=True)

        self.port = port

        self.signals = signals

        self.ser = None

        self._stop_event = threading.Event()


    # -------------------------------------------------------------------------
    # Enviar comando
    # -------------------------------------------------------------------------

    def send(self, cmd):

        if self.ser and self.ser.is_open:

            try:

                self.ser.write(
                    (cmd + "\n").encode()
                )

                self.ser.flush()

            except serial.SerialException:

                self.signals.status_msg.emit(
                    "Error sending command."
                )


    # -------------------------------------------------------------------------
    # Detener thread
    # -------------------------------------------------------------------------

    def stop(self):

        self._stop_event.set()

        if self.ser and self.ser.is_open:

            try:

                self.ser.close()

            except:

                pass


    # -------------------------------------------------------------------------
    # Thread serial
    # -------------------------------------------------------------------------

    def run(self):

        try:

            self.ser = serial.Serial(
                self.port,
                BAUD_RATE,
                timeout=1
            )

            self.signals.connected.emit(True)

            self.signals.status_msg.emit(
                f"Connected to {self.port}"
            )

        except serial.SerialException as e:

            self.signals.status_msg.emit(
                f"Connection error: {e}"
            )

            self.signals.connected.emit(False)

            return


        while not self._stop_event.is_set():

            try:

                line = (
                    self.ser
                    .readline()
                    .decode(
                        "utf-8",
                        errors="ignore"
                    )
                    .strip()
                )


                if not line:

                    continue


                # -------------------------------------------------------------
                # Mensajes informativos de la Pico
                # -------------------------------------------------------------

                if (
                    line.startswith("#")
                    or line.startswith("QCM")
                    or line.startswith("OK")
                    or line.startswith("ERROR")
                ):

                    self.signals.status_msg.emit(
                        line.lstrip("#").strip()
                    )


                # -------------------------------------------------------------
                # Ignorar encabezado CSV
                # -------------------------------------------------------------

                elif line.startswith("timestamp"):

                    continue


                # -------------------------------------------------------------
                # Datos
                # -------------------------------------------------------------

                elif "," in line:

                    self.signals.data_received.emit(
                        line
                    )


            except serial.SerialException:

                self.signals.status_msg.emit(
                    "Connection lost."
                )

                self.signals.connected.emit(False)

                break


# =============================================================================
# VENTANA PRINCIPAL
# =============================================================================

class QCMVacuumWindow(QMainWindow):

    def __init__(self):

        super().__init__()


        # ---------------------------------------------------------------------
        # Ventana
        # ---------------------------------------------------------------------

        self.setWindowTitle(
            "QCM — Vacuum Thermal Characterization"
        )

        self.setMinimumSize(
            1000,
            800
        )


        # ---------------------------------------------------------------------
        # Serial
        # ---------------------------------------------------------------------

        self.reader = None

        self.signals = SerialSignals()

        self.state = STATE_IDLE


        # ---------------------------------------------------------------------
        # CSV
        # ---------------------------------------------------------------------

        self.csv_file = None

        self.csv_writer = None


        # ---------------------------------------------------------------------
        # Buffers
        # ---------------------------------------------------------------------

        # Tiempo de temperatura
        self.t_data = deque(
            maxlen=MAX_POINTS
        )

        # Temperaturas
        self.temp1_data = deque(
            maxlen=MAX_POINTS
        )

        self.temp2_data = deque(
            maxlen=MAX_POINTS
        )


        # Frecuencia utiliza su propio vector temporal
        # porque puede no existir.

        self.freq_time_data = deque(
            maxlen=MAX_POINTS
        )

        self.freq_data = deque(
            maxlen=MAX_POINTS
        )


        # ---------------------------------------------------------------------
        # UI
        # ---------------------------------------------------------------------

        self._build_ui()

        self._connect_signals()

        self._refresh_ports()


        # ---------------------------------------------------------------------
        # Timer de actualización
        # ---------------------------------------------------------------------

        self.plot_timer = QTimer()

        self.plot_timer.setInterval(
            500
        )

        self.plot_timer.timeout.connect(
            self._update_plots
        )


# =============================================================================
# INTERFAZ
# =============================================================================

    def _build_ui(self):

        pg.setConfigOption(
            "background",
            "#1e1e2e"
        )

        pg.setConfigOption(
            "foreground",
            "#cdd6f4"
        )


        central = QWidget()

        self.setCentralWidget(
            central
        )


        root = QVBoxLayout(
            central
        )

        root.setSpacing(8)

        root.setContentsMargins(
            12,
            12,
            12,
            12
        )


        # =====================================================================
        # FILA SUPERIOR
        # =====================================================================

        top_row = QHBoxLayout()


        # ---------------------------------------------------------------------
        # CONEXIÓN
        # ---------------------------------------------------------------------

        conn_box = QGroupBox(
            "Connection"
        )

        conn_lay = QHBoxLayout(
            conn_box
        )


        conn_lay.addWidget(
            QLabel("Port:")
        )


        self.port_combo = QComboBox()

        self.port_combo.setMinimumWidth(
            220
        )

        conn_lay.addWidget(
            self.port_combo
        )


        self.btn_refresh = QPushButton(
            "↻"
        )

        self.btn_refresh.setFixedWidth(
            35
        )

        self.btn_refresh.clicked.connect(
            self._refresh_ports
        )

        conn_lay.addWidget(
            self.btn_refresh
        )


        self.btn_connect = QPushButton(
            "Connect"
        )

        self.btn_connect.clicked.connect(
            self._toggle_connect
        )

        conn_lay.addWidget(
            self.btn_connect
        )


        top_row.addWidget(
            conn_box
        )


        # ---------------------------------------------------------------------
        # CONTROL
        # ---------------------------------------------------------------------

        control_box = QGroupBox(
            "Measurement"
        )

        control_lay = QHBoxLayout(
            control_box
        )


        self.btn_start = QPushButton(
            "▶  Start measurement"
        )

        self.btn_start.setEnabled(
            False
        )

        self.btn_start.setFixedHeight(
            40
        )

        self._style_start_button()

        self.btn_start.clicked.connect(
            self._toggle_measurement
        )

        control_lay.addWidget(
            self.btn_start
        )


        top_row.addWidget(
            control_box
        )


        root.addLayout(
            top_row
        )


        # =====================================================================
        # CSV
        # =====================================================================

        csv_box = QGroupBox(
            "Save data"
        )

        csv_lay = QHBoxLayout(
            csv_box
        )


        self.chk_csv = QCheckBox(
            "Save to CSV"
        )

        self.chk_csv.setChecked(
            True
        )

        self.chk_csv.toggled.connect(
            self._on_csv_toggle
        )

        csv_lay.addWidget(
            self.chk_csv
        )


        csv_lay.addSpacing(
            10
        )


        csv_lay.addWidget(
            QLabel("File name:")
        )


        self.txt_csv_name = QLineEdit()

        self.txt_csv_name.setPlaceholderText(
            "e.g. vacuum_test_01"
        )

        self.txt_csv_name.setMinimumWidth(
            250
        )

        csv_lay.addWidget(
            self.txt_csv_name
        )


        csv_lay.addWidget(
            QLabel(".csv")
        )


        csv_lay.addSpacing(
            15
        )


        self.lbl_csv_status = QLabel(
            "—"
        )

        self.lbl_csv_status.setStyleSheet(
            "color:#6c7086; font-size:10px;"
        )

        csv_lay.addWidget(
            self.lbl_csv_status
        )


        csv_lay.addStretch()


        root.addWidget(
            csv_box
        )


        # =====================================================================
        # VALORES ACTUALES
        # =====================================================================

        values_box = QGroupBox(
            "Current values"
        )

        values_lay = QHBoxLayout(
            values_box
        )


        self.lbl_freq = self._make_value_label(
            "QCM frequency",
            "No signal",
            "#89b4fa"
        )


        self.lbl_temp1 = self._make_value_label(
            "Temperature 1",
            "—",
            "#fab387"
        )


        self.lbl_temp2 = self._make_value_label(
            "Temperature 2",
            "—",
            "#a6e3a1"
        )


        values = [

            self.lbl_freq,

            self.lbl_temp1,

            self.lbl_temp2

        ]


        for i, (widget, _) in enumerate(values):

            values_lay.addWidget(
                widget
            )

            if i < len(values) - 1:

                sep = QFrame()

                sep.setFrameShape(
                    QFrame.VLine
                )

                sep.setStyleSheet(
                    "color:#313244;"
                )

                values_lay.addWidget(
                    sep
                )


        root.addWidget(
            values_box
        )


        # =====================================================================
        # GRÁFICO FRECUENCIA
        # =====================================================================

        self.plot_freq = pg.PlotWidget(
            title="QCM frequency vs time"
        )


        self.plot_freq.showGrid(
            x=True,
            y=True,
            alpha=0.2
        )


        self.plot_freq.setLabel(
            "bottom",
            "Time",
            units="s"
        )


        self.plot_freq.setLabel(
            "left",
            "Frequency",
            units="Hz"
        )


        self.curve_freq = self.plot_freq.plot(

            pen=pg.mkPen(
                color="#89b4fa",
                width=2
            )

        )


        root.addWidget(
            self.plot_freq,
            stretch=2
        )


        # =====================================================================
        # GRÁFICO TEMPERATURA
        # =====================================================================

        self.plot_temp = pg.PlotWidget(
            title="Temperature vs time"
        )


        self.plot_temp.showGrid(
            x=True,
            y=True,
            alpha=0.2
        )


        self.plot_temp.setLabel(
            "bottom",
            "Time",
            units="s"
        )


        self.plot_temp.setLabel(
            "left",
            "Temperature",
            units="°C"
        )


        # IMPORTANTE:
        # agregar la leyenda ANTES de crear las curvas

        self.plot_temp.addLegend()


        self.curve_temp1 = self.plot_temp.plot(

            pen=pg.mkPen(
                color="#fab387",
                width=2
            ),

            name="NTC 1"

        )


        self.curve_temp2 = self.plot_temp.plot(

            pen=pg.mkPen(
                color="#a6e3a1",
                width=2
            ),

            name="NTC 2"

        )


        root.addWidget(
            self.plot_temp,
            stretch=2
        )


        # =====================================================================
        # STATUS BAR
        # =====================================================================

        self.status_bar = QStatusBar()

        self.setStatusBar(
            self.status_bar
        )


        self.status_bar.showMessage(
            "Not connected."
        )


# =============================================================================
# LABEL DE VALOR
# =============================================================================

    def _make_value_label(
        self,
        title,
        value,
        color
    ):

        box = QWidget()

        layout = QVBoxLayout(
            box
        )


        layout.setContentsMargins(
            10,
            4,
            10,
            4
        )


        label_title = QLabel(
            title
        )

        label_title.setAlignment(
            Qt.AlignCenter
        )

        label_title.setStyleSheet(
            f"color:{color}; font-size:10px;"
        )


        label_value = QLabel(
            value
        )

        label_value.setAlignment(
            Qt.AlignCenter
        )

        label_value.setFont(
            QFont(
                "Courier New",
                15,
                QFont.Bold
            )
        )

        label_value.setStyleSheet(
            f"color:{color};"
        )


        layout.addWidget(
            label_title
        )

        layout.addWidget(
            label_value
        )


        return box, label_value


# =============================================================================
# BOTÓN START
# =============================================================================

    def _style_start_button(self):

        if self.state == STATE_MEAS:

            self.btn_start.setStyleSheet("""
                QPushButton {
                    background:#d20f39;
                    color:white;
                    border-radius:6px;
                    font-weight:bold;
                    font-size:13px;
                }

                QPushButton:hover {
                    background:#e8305a;
                }
            """)

        else:

            self.btn_start.setStyleSheet("""
                QPushButton {
                    background:#40a02b;
                    color:white;
                    border-radius:6px;
                    font-weight:bold;
                    font-size:13px;
                }

                QPushButton:hover {
                    background:#5dbc3f;
                }

                QPushButton:disabled {
                    background:#45475a;
                    color:#6c7086;
                }
            """)


# =============================================================================
# SEÑALES
# =============================================================================

    def _connect_signals(self):

        self.signals.data_received.connect(
            self._on_data
        )

        self.signals.status_msg.connect(
            lambda m:
            self.status_bar.showMessage(m)
        )

        self.signals.connected.connect(
            self._on_connection_change
        )


# =============================================================================
# PUERTOS
# =============================================================================

    def _refresh_ports(self):

        self.port_combo.clear()


        ports = (
            serial.tools
            .list_ports
            .comports()
        )


        pico_port = None


        for p in ports:

            self.port_combo.addItem(

                f"{p.device} — {p.description}",

                p.device

            )


            # Raspberry Pi Pico USB VID
            if p.vid == 0x2E8A:

                pico_port = p.device


        if pico_port:

            idx = self.port_combo.findData(
                pico_port
            )

            if idx >= 0:

                self.port_combo.setCurrentIndex(
                    idx
                )


            self.status_bar.showMessage(
                f"Pico detected on {pico_port}"
            )


        elif ports:

            self.status_bar.showMessage(
                "Pico not automatically detected — select port manually."
            )


        else:

            self.status_bar.showMessage(
                "No serial ports found."
            )


# =============================================================================
# CONEXIÓN
# =============================================================================

    def _toggle_connect(self):

        # ---------------------------------------------------------------------
        # DESCONECTAR
        # ---------------------------------------------------------------------

        if (
            self.reader
            and self.reader.is_alive()
        ):

            if self.state == STATE_MEAS:

                self.reader.send(
                    "STOP"
                )


            self.reader.stop()

            self.reader = None


            self.btn_connect.setText(
                "Connect"
            )


            self.btn_start.setEnabled(
                False
            )


            self._enter_idle()


        # ---------------------------------------------------------------------
        # CONECTAR
        # ---------------------------------------------------------------------

        else:

            port = self.port_combo.currentData()


            if not port:

                self.status_bar.showMessage(
                    "Select a port first."
                )

                return


            self.reader = SerialReader(
                port,
                self.signals
            )


            self.reader.start()


    def _on_connection_change(
        self,
        ok
    ):

        if ok:

            self.btn_connect.setText(
                "Disconnect"
            )

            self.btn_start.setEnabled(
                True
            )


        else:

            self.btn_connect.setText(
                "Connect"
            )

            self.btn_start.setEnabled(
                False
            )

            self._enter_idle()


# =============================================================================
# START / STOP
# =============================================================================

    def _toggle_measurement(self):

        if not self.reader:

            return


        if self.state == STATE_IDLE:

            self._start_measurement()


        else:

            self.reader.send(
                "STOP"
            )

            self._enter_idle()


    def _start_measurement(self):

        # ---------------------------------------------------------------------
        # Limpiar datos anteriores
        # ---------------------------------------------------------------------

        self.t_data.clear()

        self.temp1_data.clear()

        self.temp2_data.clear()

        self.freq_time_data.clear()

        self.freq_data.clear()


        # ---------------------------------------------------------------------
        # Limpiar gráficos
        # ---------------------------------------------------------------------

        self.curve_freq.clear()

        self.curve_temp1.clear()

        self.curve_temp2.clear()


        # ---------------------------------------------------------------------
        # Resetear labels
        # ---------------------------------------------------------------------

        self.lbl_freq[1].setText(
            "No signal"
        )

        self.lbl_temp1[1].setText(
            "—"
        )

        self.lbl_temp2[1].setText(
            "—"
        )


        # ---------------------------------------------------------------------
        # Abrir CSV
        # ---------------------------------------------------------------------

        self._open_csv()


        # ---------------------------------------------------------------------
        # Estado
        # ---------------------------------------------------------------------

        self.state = STATE_MEAS


        self.btn_start.setText(
            "⏹  Stop measurement"
        )


        self._style_start_button()


        # ---------------------------------------------------------------------
        # Bloquear opciones CSV
        # ---------------------------------------------------------------------

        self.chk_csv.setEnabled(
            False
        )

        self.txt_csv_name.setEnabled(
            False
        )


        # ---------------------------------------------------------------------
        # Timer gráfico
        # ---------------------------------------------------------------------

        self.plot_timer.start()


        # ---------------------------------------------------------------------
        # START a la Pico
        # ---------------------------------------------------------------------

        self.reader.send(
            "START"
        )


        self.status_bar.showMessage(
            "Measurement started."
        )


    def _enter_idle(self):

        self.state = STATE_IDLE

        self.plot_timer.stop()

        self._close_csv()


        self.btn_start.setText(
            "▶  Start measurement"
        )


        self._style_start_button()


        self.chk_csv.setEnabled(
            True
        )


        self.txt_csv_name.setEnabled(
            self.chk_csv.isChecked()
        )


# =============================================================================
# DATOS ENTRANTES
# =============================================================================

    def _on_data(
        self,
        line
    ):

        if self.state != STATE_MEAS:

            return


        parts = line.split(",")


        try:

            # ================================================================
            # CASO 1
            #
            # FRECUENCIA + TEMPERATURAS
            #
            # timestamp,f_sensor,temp1,temp2
            #
            # Ejemplo:
            #
            # 10.0,7149125.000,24.53,23.91
            # ================================================================

            if len(parts) == 4:

                timestamp = float(
                    parts[0]
                )

                frequency = float(
                    parts[1]
                )

                temp1 = float(
                    parts[2]
                )

                temp2 = float(
                    parts[3]
                )


                # Guardar frecuencia con su propio timestamp

                self.freq_time_data.append(
                    timestamp
                )

                self.freq_data.append(
                    frequency
                )


                # Mostrar frecuencia

                self.lbl_freq[1].setText(
                    f"{frequency:,.3f} Hz"
                )


            # ================================================================
            # CASO 2
            #
            # SOLO TEMPERATURAS
            #
            # timestamp,temp1,temp2
            #
            # Ejemplo:
            #
            # 10.0,24.53,23.91
            # ================================================================

            elif len(parts) == 3:

                timestamp = float(
                    parts[0]
                )

                temp1 = float(
                    parts[1]
                )

                temp2 = float(
                    parts[2]
                )

                frequency = None


                self.lbl_freq[1].setText(
                    "No signal"
                )


            # ================================================================
            # FORMATO DESCONOCIDO
            # ================================================================

            else:

                self.status_bar.showMessage(
                    f"Invalid data: {line}"
                )

                return


        except ValueError:

            self.status_bar.showMessage(
                f"Could not parse: {line}"
            )

            return


        # =====================================================================
        # TEMPERATURAS
        #
        # ESTO SE EJECUTA HAYA O NO FRECUENCIA
        # =====================================================================

        self.t_data.append(
            timestamp
        )

        self.temp1_data.append(
            temp1
        )

        self.temp2_data.append(
            temp2
        )


        # =====================================================================
        # LABELS DE TEMPERATURA
        # =====================================================================

        self.lbl_temp1[1].setText(
            f"{temp1:.2f} °C"
        )

        self.lbl_temp2[1].setText(
            f"{temp2:.2f} °C"
        )


        # =====================================================================
        # CSV
        # =====================================================================

        self._write_csv_row(
            timestamp,
            frequency,
            temp1,
            temp2
        )


# =============================================================================
# ACTUALIZACIÓN DE GRÁFICOS
# =============================================================================

    def _update_plots(self):

        # =====================================================================
        # TEMPERATURA
        #
        # No depende de frecuencia.
        # =====================================================================

        if self.t_data:

            t = list(
                self.t_data
            )

            temp1 = list(
                self.temp1_data
            )

            temp2 = list(
                self.temp2_data
            )


            self.curve_temp1.setData(
                t,
                temp1
            )


            self.curve_temp2.setData(
                t,
                temp2
            )


        # =====================================================================
        # FRECUENCIA
        #
        # Solo se actualiza si llegaron datos de frecuencia.
        # =====================================================================

        if self.freq_data:

            t_freq = list(
                self.freq_time_data
            )

            freq = list(
                self.freq_data
            )


            self.curve_freq.setData(
                t_freq,
                freq
            )


# =============================================================================
# CSV
# =============================================================================

    def _on_csv_toggle(
        self,
        checked
    ):

        self.txt_csv_name.setEnabled(
            checked
        )

        if not checked:

            self.lbl_csv_status.setText(
                "—"
            )


# =============================================================================
# ABRIR CSV
# =============================================================================

    def _open_csv(self):

        if not self.chk_csv.isChecked():

            return


        name = (
            self.txt_csv_name
            .text()
            .strip()
        )


        if not name:

            name = (
                "qcm_vacuum_"
                + datetime.now().strftime(
                    "%Y%m%d_%H%M%S"
                )
            )

            self.txt_csv_name.setText(
                name
            )


        filename = f"{name}.csv"


        base, ext = os.path.splitext(
            filename
        )


        counter = 1


        # No sobreescribir archivos existentes

        while os.path.exists(
            filename
        ):

            filename = (
                f"{base}_{counter}{ext}"
            )

            counter += 1


        try:

            self.csv_file = open(
                filename,
                "w",
                newline="",
                encoding="utf-8"
            )


            self.csv_writer = csv.writer(
                self.csv_file
            )


            # -----------------------------------------------------------------
            # Metadata
            # -----------------------------------------------------------------

            self.csv_writer.writerow([
                "# QCM Vacuum Thermal Characterization"
            ])


            self.csv_writer.writerow([
                f"# Date={datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ])


            self.csv_writer.writerow([
                "# Crystal=1"
            ])


            self.csv_writer.writerow([
                "# Thermistors=2"
            ])


            self.csv_writer.writerow([
                "# Frequency may be empty when QCM signal is unavailable"
            ])


            # -----------------------------------------------------------------
            # Encabezado
            # -----------------------------------------------------------------

            self.csv_writer.writerow([

                "timestamp_s",

                "f_sensor_hz",

                "temp_1_c",

                "temp_2_c"

            ])


            self.csv_file.flush()


            self.lbl_csv_status.setText(
                f"✓ Saving to {filename}"
            )


            self.lbl_csv_status.setStyleSheet(
                "color:#a6e3a1; font-size:10px;"
            )


        except OSError as e:

            self.status_bar.showMessage(
                f"CSV error: {e}"
            )


            self.csv_file = None

            self.csv_writer = None


# =============================================================================
# ESCRIBIR CSV
# =============================================================================

    def _write_csv_row(
        self,
        timestamp,
        frequency,
        temp1,
        temp2
    ):

        if self.csv_writer is None:

            return


        # ---------------------------------------------------------------------
        # Frecuencia opcional
        # ---------------------------------------------------------------------

        if frequency is None:

            freq_text = ""

        else:

            freq_text = (
                f"{frequency:.3f}"
            )


        # ---------------------------------------------------------------------
        # Escribir fila
        # ---------------------------------------------------------------------

        self.csv_writer.writerow([

            f"{timestamp:.3f}",

            freq_text,

            f"{temp1:.3f}",

            f"{temp2:.3f}"

        ])


        # Flush para minimizar pérdida de datos
        self.csv_file.flush()


# =============================================================================
# CERRAR CSV
# =============================================================================

    def _close_csv(self):

        if self.csv_file:

            try:

                self.csv_file.close()

            except:

                pass


            self.csv_file = None

            self.csv_writer = None


            self.lbl_csv_status.setText(
                "✓ File saved and closed"
            )


            self.lbl_csv_status.setStyleSheet(
                "color:#6c7086; font-size:10px;"
            )


# =============================================================================
# CIERRE DE VENTANA
# =============================================================================

    def closeEvent(
        self,
        event
    ):

        if self.reader:

            if self.state == STATE_MEAS:

                self.reader.send(
                    "STOP"
                )


            self.reader.stop()


        self._close_csv()

        event.accept()


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":

    app = QApplication(
        sys.argv
    )


    app.setStyle(
        "Fusion"
    )


    window = QCMVacuumWindow()

    window.show()


    sys.exit(
        app.exec_()
    )