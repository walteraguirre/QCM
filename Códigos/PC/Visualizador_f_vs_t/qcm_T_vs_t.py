# qcm_2temp_vacuum_gui.py — PC
# GUI for monitoring two thermistors vs time during vacuum tests.
#
# Installation:
#   pip install PyQt5 pyserial pyqtgraph
#
# Accepted serial formats:
#   timestamp_s,temp1_c,temp2_c
# or
#   timestamp_s,f_sensor_hz,temp1_c,temp2_c
#
# Commands sent to the Raspberry Pi Pico:
#   START_1_2TEMP
#   STOP

import sys
import threading
import serial
import serial.tools.list_ports
from collections import deque
import csv
import os
from datetime import datetime
import statistics

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QGroupBox, QStatusBar,
    QCheckBox, QLineEdit, QFrame, QSpinBox, QFormLayout
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QFont

import pyqtgraph as pg


# ─── Configuration ────────────────────────────────────────────────────────────
BAUD_RATE = 115200
RAW_BUFFER_SIZE = 50000

STATE_IDLE = "idle"
STATE_MEAS = "measuring"


# ─── Signals ──────────────────────────────────────────────────────────────────
class SerialSignals(QObject):
    data_received = pyqtSignal(str)
    status_msg = pyqtSignal(str)
    connected = pyqtSignal(bool)


# ─── Serial reader thread ─────────────────────────────────────────────────────
class SerialReader(threading.Thread):

    def __init__(self, port, signals):
        super().__init__(daemon=True)
        self.port = port
        self.signals = signals
        self.ser = None
        self._stop_event = threading.Event()

    def send(self, cmd):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write((cmd + "\n").encode())
            except serial.SerialException:
                pass

    def stop(self):
        self._stop_event.set()

        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except serial.SerialException:
            pass

    def run(self):
        try:
            self.ser = serial.Serial(
                self.port,
                BAUD_RATE,
                timeout=1
            )

            self.signals.connected.emit(True)
            self.signals.status_msg.emit(f"Connected to {self.port}")

        except serial.SerialException as e:
            self.signals.status_msg.emit(f"Connection error: {e}")
            self.signals.connected.emit(False)
            return

        while not self._stop_event.is_set():
            try:
                line = (
                    self.ser.readline()
                    .decode("utf-8", errors="ignore")
                    .strip()
                )

                if not line:
                    continue

                if (
                    line.startswith("#")
                    or line.startswith("QCM")
                    or line.startswith("OK")
                ):
                    self.signals.status_msg.emit(
                        line.lstrip("#").strip()
                    )

                elif (
                    "," in line
                    and not line.lower().startswith("timestamp")
                ):
                    self.signals.data_received.emit(line)

            except serial.SerialException:
                self.signals.status_msg.emit("Connection lost.")
                self.signals.connected.emit(False)
                break


# ─── Main window ──────────────────────────────────────────────────────────────
class TwoTempVacuumWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle(
            "QCM — Two-Thermistor Vacuum Temperature Monitor"
        )
        self.setMinimumSize(1100, 760)

        self.reader = None
        self.signals = SerialSignals()
        self.state = STATE_IDLE

        # Raw data
        self.time_data = deque(maxlen=RAW_BUFFER_SIZE)
        self.temp1_data = deque(maxlen=RAW_BUFFER_SIZE)
        self.temp2_data = deque(maxlen=RAW_BUFFER_SIZE)

        self.t0 = None

        # CSV
        self.csv_file = None
        self.csv_writer = None
        self.csv_filename = None

        self._build_ui()
        self._connect_signals()
        self._refresh_ports()

        self.ui_timer = QTimer()
        self.ui_timer.setInterval(500)
        self.ui_timer.timeout.connect(self._update_ui)
        self.ui_timer.start()

    # ─── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):

        pg.setConfigOption("background", "#1e1e2e")
        pg.setConfigOption("foreground", "#cdd6f4")

        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        # ── Connection + control ──────────────────────────────────────────────
        top_row = QHBoxLayout()

        conn_box = QGroupBox("Connection")
        conn_lay = QHBoxLayout(conn_box)

        conn_lay.addWidget(QLabel("Port:"))

        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(240)
        conn_lay.addWidget(self.port_combo)

        self.btn_refresh = QPushButton("↻")
        self.btn_refresh.setFixedWidth(34)
        self.btn_refresh.clicked.connect(self._refresh_ports)
        conn_lay.addWidget(self.btn_refresh)

        self.btn_connect = QPushButton("Connect")
        self.btn_connect.clicked.connect(self._toggle_connect)
        conn_lay.addWidget(self.btn_connect)

        top_row.addWidget(conn_box, stretch=2)

        ctrl_box = QGroupBox("Control")
        ctrl_lay = QVBoxLayout(ctrl_box)

        self.btn_start = QPushButton("▶  Start acquisition")
        self.btn_start.setFixedHeight(38)
        self.btn_start.setEnabled(False)
        self.btn_start.clicked.connect(self._toggle_measurement)

        ctrl_lay.addWidget(self.btn_start)
        top_row.addWidget(ctrl_box, stretch=1)

        root.addLayout(top_row)

        # ── CSV ───────────────────────────────────────────────────────────────
        csv_box = QGroupBox("Data logging")
        csv_lay = QHBoxLayout(csv_box)

        self.chk_csv = QCheckBox("Save every sample to CSV")
        self.chk_csv.setChecked(False)
        self.chk_csv.toggled.connect(self._on_csv_toggle)
        csv_lay.addWidget(self.chk_csv)

        csv_lay.addSpacing(10)
        csv_lay.addWidget(QLabel("File name:"))

        self.txt_csv_name = QLineEdit()
        self.txt_csv_name.setPlaceholderText("e.g. vacuum_test_01")
        self.txt_csv_name.setMinimumWidth(240)
        self.txt_csv_name.setEnabled(False)
        csv_lay.addWidget(self.txt_csv_name)

        csv_lay.addWidget(QLabel(".csv"))

        self.lbl_csv_status = QLabel("—")
        self.lbl_csv_status.setStyleSheet(
            "color: #6c7086; font-size: 10px;"
        )
        csv_lay.addWidget(self.lbl_csv_status)
        csv_lay.addStretch()

        root.addWidget(csv_box)

        # ── Plot settings ─────────────────────────────────────────────────────
        plot_settings_box = QGroupBox("Plot settings")
        plot_settings_lay = QFormLayout(plot_settings_box)

        self.spin_visible_minutes = QSpinBox()
        self.spin_visible_minutes.setRange(1, 1440)
        self.spin_visible_minutes.setValue(30)
        self.spin_visible_minutes.setSuffix(" min")
        self.spin_visible_minutes.setToolTip(
            "Only affects the visible time range. "
            "All acquired data remain stored in memory/CSV."
        )

        plot_settings_lay.addRow(
            "Visible time window:",
            self.spin_visible_minutes
        )

        root.addWidget(plot_settings_box)

        # ── Live values ───────────────────────────────────────────────────────
        values_box = QGroupBox("Live temperatures")
        values_lay = QHBoxLayout(values_box)

        self.lbl_temp1 = self._make_value_box(
            "T1 — QCM / sensor",
            "—",
            "#fab387"
        )

        self.lbl_temp2 = self._make_value_box(
            "T2 — Reference / ambient",
            "—",
            "#89b4fa"
        )

        self.lbl_delta_temp = self._make_value_box(
            "ΔT = T1 − T2",
            "—",
            "#94e2d5"
        )

        self.lbl_elapsed = self._make_value_box(
            "Elapsed time",
            "—",
            "#cba6f7"
        )

        for i, widget in enumerate([
            self.lbl_temp1[0],
            self.lbl_temp2[0],
            self.lbl_delta_temp[0],
            self.lbl_elapsed[0]
        ]):
            values_lay.addWidget(widget)

            if i < 3:
                sep = QFrame()
                sep.setFrameShape(QFrame.VLine)
                sep.setStyleSheet("color: #313244;")
                values_lay.addWidget(sep)

        root.addWidget(values_box)

        # ── Simple statistics ─────────────────────────────────────────────────
        stats_box = QGroupBox("Current-run statistics")
        stats_lay = QHBoxLayout(stats_box)

        self.lbl_t1_mean = self._make_value_box(
            "Mean T1",
            "—",
            "#fab387"
        )

        self.lbl_t2_mean = self._make_value_box(
            "Mean T2",
            "—",
            "#89b4fa"
        )

        self.lbl_dt_mean = self._make_value_box(
            "Mean ΔT",
            "—",
            "#94e2d5"
        )

        self.lbl_t1_range = self._make_value_box(
            "T1 range",
            "—",
            "#f9e2af"
        )

        self.lbl_t2_range = self._make_value_box(
            "T2 range",
            "—",
            "#f9e2af"
        )

        self.lbl_samples = self._make_value_box(
            "Samples",
            "—",
            "#a6e3a1"
        )

        stat_widgets = [
            self.lbl_t1_mean[0],
            self.lbl_t2_mean[0],
            self.lbl_dt_mean[0],
            self.lbl_t1_range[0],
            self.lbl_t2_range[0],
            self.lbl_samples[0]
        ]

        for i, widget in enumerate(stat_widgets):
            stats_lay.addWidget(widget)

            if i < len(stat_widgets) - 1:
                sep = QFrame()
                sep.setFrameShape(QFrame.VLine)
                sep.setStyleSheet("color: #313244;")
                stats_lay.addWidget(sep)

        root.addWidget(stats_box)

        # ── Temperature plot ──────────────────────────────────────────────────
        self.plot = pg.PlotWidget(
            title="Temperature vs time"
        )

        self.plot.showGrid(
            x=True,
            y=True,
            alpha=0.2
        )

        self.plot.setLabel(
            "bottom",
            "Elapsed time",
            units="min"
        )

        self.plot.setLabel(
            "left",
            "Temperature",
            units="°C"
        )

        self.plot.addLegend()

        self.curve_t1 = self.plot.plot(
            [],
            [],
            pen=pg.mkPen("#fab387", width=2),
            name="T1 — QCM / sensor"
        )

        self.curve_t2 = self.plot.plot(
            [],
            [],
            pen=pg.mkPen("#89b4fa", width=2),
            name="T2 — Reference / ambient"
        )

        root.addWidget(self.plot, stretch=1)

        # ── Status ────────────────────────────────────────────────────────────
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Not connected.")

    def _make_value_box(self, title, value, color):

        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 4, 8, 4)

        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet(
            f"color:{color}; font-size:10px;"
        )

        value_label = QLabel(value)
        value_label.setAlignment(Qt.AlignCenter)
        value_label.setFont(
            QFont("Courier New", 12, QFont.Bold)
        )
        value_label.setStyleSheet(
            f"color:{color};"
        )

        lay.addWidget(title_label)
        lay.addWidget(value_label)

        return box, value_label

    # ─── Signals ──────────────────────────────────────────────────────────────
    def _connect_signals(self):

        self.signals.data_received.connect(self._on_data)

        self.signals.status_msg.connect(
            lambda msg: self.status_bar.showMessage(msg)
        )

        self.signals.connected.connect(
            self._on_connection_change
        )

    # ─── Ports ────────────────────────────────────────────────────────────────
    def _refresh_ports(self):

        self.port_combo.clear()

        ports = serial.tools.list_ports.comports()
        pico_port = None

        for p in ports:

            self.port_combo.addItem(
                f"{p.device}  —  {p.description}",
                p.device
            )

            if p.vid == 0x2E8A:
                pico_port = p.device

        if pico_port:

            idx = self.port_combo.findData(pico_port)

            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)

            self.status_bar.showMessage(
                f"Pico detected on {pico_port}"
            )

        elif ports:

            self.status_bar.showMessage(
                "Pico not detected automatically — "
                "select the port manually."
            )

        else:

            self.status_bar.showMessage(
                "No serial ports found."
            )

    # ─── Connection ───────────────────────────────────────────────────────────
    def _toggle_connect(self):

        if self.reader and self.reader.is_alive():

            if self.state == STATE_MEAS:
                self.reader.send("STOP")

            self.reader.stop()
            self.reader = None

            self.btn_connect.setText("Connect")
            self.btn_start.setEnabled(False)

            self._stop_measurement()

        else:

            port = self.port_combo.currentData()

            if not port:
                self.status_bar.showMessage(
                    "Select a serial port first."
                )
                return

            self.reader = SerialReader(
                port,
                self.signals
            )

            self.reader.start()

    def _on_connection_change(self, connected):

        if connected:

            self.btn_connect.setText("Disconnect")
            self.btn_start.setEnabled(True)

        else:

            self.btn_connect.setText("Connect")
            self.btn_start.setEnabled(False)
            self._stop_measurement()

    # ─── Measurement control ──────────────────────────────────────────────────
    def _toggle_measurement(self):

        if self.state == STATE_IDLE:
            self._start_measurement()

        else:

            if self.reader:
                self.reader.send("STOP")

            self._stop_measurement()

    def _start_measurement(self):

        if not self.reader:
            return

        self.time_data.clear()
        self.temp1_data.clear()
        self.temp2_data.clear()

        self.t0 = None

        self.curve_t1.setData([], [])
        self.curve_t2.setData([], [])

        self.state = STATE_MEAS

        # Keep this command if your current Pico firmware already uses it.
        self.reader.send("START_1_2TEMP")

        self._open_csv()

        self.btn_start.setText("⏹  Stop acquisition")

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

        self.chk_csv.setEnabled(False)
        self.txt_csv_name.setEnabled(False)

        self.status_bar.showMessage(
            "Acquisition started — monitoring T1 and T2."
        )

    def _stop_measurement(self):

        if self.state == STATE_IDLE:
            return

        self.state = STATE_IDLE

        self._close_csv()

        self.btn_start.setText("▶  Start acquisition")
        self.btn_start.setStyleSheet("")

        self.chk_csv.setEnabled(True)
        self.txt_csv_name.setEnabled(
            self.chk_csv.isChecked()
        )

        self.status_bar.showMessage(
            "Acquisition stopped."
        )

    # ─── Incoming data ────────────────────────────────────────────────────────
    def _on_data(self, line):

        if self.state != STATE_MEAS:
            return

        parts = [p.strip() for p in line.split(",")]

        try:
            # Supported formats:
            #
            # 3 columns:
            # timestamp_s,temp1_c,temp2_c
            #
            # 4+ columns:
            # timestamp_s,f_sensor_hz,temp1_c,temp2_c
            #
            if len(parts) == 3:
                timestamp = float(parts[0])
                temp1 = float(parts[1])
                temp2 = float(parts[2])

            elif len(parts) >= 4:
                timestamp = float(parts[0])
                temp1 = float(parts[2])
                temp2 = float(parts[3])

            else:
                return

            if self.t0 is None:
                self.t0 = timestamp

            elapsed_s = timestamp - self.t0

            self.time_data.append(elapsed_s)
            self.temp1_data.append(temp1)
            self.temp2_data.append(temp2)

            self._write_csv_sample(
                elapsed_s,
                temp1,
                temp2
            )

        except (ValueError, IndexError):
            pass

    # ─── UI update ────────────────────────────────────────────────────────────
    def _update_ui(self):

        if not self.time_data:
            return

        t_s = list(self.time_data)
        t_min = [x / 60.0 for x in t_s]

        t1 = list(self.temp1_data)
        t2 = list(self.temp2_data)

        # Live values
        self.lbl_temp1[1].setText(
            f"{t1[-1]:.3f} °C"
        )

        self.lbl_temp2[1].setText(
            f"{t2[-1]:.3f} °C"
        )

        delta_t = t1[-1] - t2[-1]

        self.lbl_delta_temp[1].setText(
            f"{delta_t:+.3f} °C"
        )

        elapsed = t_s[-1]

        if elapsed < 60:
            elapsed_text = f"{elapsed:.1f} s"
        elif elapsed < 3600:
            elapsed_text = f"{elapsed / 60.0:.2f} min"
        else:
            elapsed_text = f"{elapsed / 3600.0:.2f} h"

        self.lbl_elapsed[1].setText(
            elapsed_text
        )

        # Statistics for full current run
        t1_mean = statistics.fmean(t1)
        t2_mean = statistics.fmean(t2)
        dt_mean = t1_mean - t2_mean

        t1_range = max(t1) - min(t1)
        t2_range = max(t2) - min(t2)

        self.lbl_t1_mean[1].setText(
            f"{t1_mean:.3f} °C"
        )

        self.lbl_t2_mean[1].setText(
            f"{t2_mean:.3f} °C"
        )

        self.lbl_dt_mean[1].setText(
            f"{dt_mean:+.3f} °C"
        )

        self.lbl_t1_range[1].setText(
            f"{t1_range:.3f} °C"
        )

        self.lbl_t2_range[1].setText(
            f"{t2_range:.3f} °C"
        )

        self.lbl_samples[1].setText(
            str(len(t_s))
        )

        # Plot
        self.curve_t1.setData(
            t_min,
            t1
        )

        self.curve_t2.setData(
            t_min,
            t2
        )

        visible_min = float(
            self.spin_visible_minutes.value()
        )

        if t_min[-1] > visible_min:
            self.plot.setXRange(
                t_min[-1] - visible_min,
                t_min[-1],
                padding=0
            )
        else:
            self.plot.setXRange(
                0,
                max(visible_min, t_min[-1] + 0.1),
                padding=0
            )

    # ─── CSV ──────────────────────────────────────────────────────────────────
    def _on_csv_toggle(self, checked):

        self.txt_csv_name.setEnabled(checked)

        if not checked:
            self.lbl_csv_status.setText("—")
            self.lbl_csv_status.setStyleSheet(
                "color: #6c7086; font-size: 10px;"
            )

    def _open_csv(self):

        if not self.chk_csv.isChecked():
            self.csv_filename = None
            return

        name = self.txt_csv_name.text().strip()

        if not name:
            name = (
                "qcm_2temp_vacuum_"
                + datetime.now().strftime(
                    "%Y%m%d_%H%M%S"
                )
            )

            self.txt_csv_name.setText(name)

        filename = f"{name}.csv"

        base, ext = os.path.splitext(filename)
        counter = 1

        while os.path.exists(filename):
            filename = f"{base}_{counter}{ext}"
            counter += 1

        try:
            self.csv_filename = filename

            self.csv_file = open(
                filename,
                "w",
                newline="",
                encoding="utf-8"
            )

            self.csv_writer = csv.writer(
                self.csv_file
            )

            self.csv_writer.writerow([
                "# Two-thermistor vacuum temperature test — "
                + datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            ])

            self.csv_writer.writerow([
                "# T1 = QCM / sensor temperature"
            ])

            self.csv_writer.writerow([
                "# T2 = reference / ambient temperature"
            ])

            self.csv_writer.writerow([
                "elapsed_s",
                "temp1_c",
                "temp2_c",
                "delta_temp_c"
            ])

            self.csv_file.flush()

            self.lbl_csv_status.setText(
                f"✓ Saving to {filename}"
            )

            self.lbl_csv_status.setStyleSheet(
                "color: #a6e3a1; font-size: 10px;"
            )

        except OSError as e:

            self.csv_file = None
            self.csv_writer = None
            self.csv_filename = None

            self.lbl_csv_status.setText(
                f"✗ Error: {e}"
            )

            self.lbl_csv_status.setStyleSheet(
                "color: #f38ba8; font-size: 10px;"
            )

    def _write_csv_sample(
        self,
        elapsed_s,
        temp1,
        temp2
    ):

        if self.csv_writer is None:
            return

        self.csv_writer.writerow([
            f"{elapsed_s:.3f}",
            f"{temp1:.5f}",
            f"{temp2:.5f}",
            f"{temp1 - temp2:.5f}"
        ])

        self.csv_file.flush()

    def _close_csv(self):

        if self.csv_file:

            self.csv_file.close()
            self.csv_file = None
            self.csv_writer = None

            self.lbl_csv_status.setText(
                "✓ File saved and closed"
            )

            self.lbl_csv_status.setStyleSheet(
                "color: #6c7086; font-size: 10px;"
            )

    # ─── Window close ─────────────────────────────────────────────────────────
    def closeEvent(self, event):

        if self.reader:

            if self.state == STATE_MEAS:
                self.reader.send("STOP")

            self.reader.stop()

        self._close_csv()

        event.accept()


# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    win = TwoTempVacuumWindow()
    win.show()

    sys.exit(app.exec_())
