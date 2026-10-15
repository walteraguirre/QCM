# qcm_ft_2temp_gui.py — PC
# GUI for QCM frequency vs temperature characterization
#
# Installation:
#   pip install PyQt5 pyserial pyqtgraph
#
# Expected serial format from the Raspberry Pi Pico:
#   timestamp_s,f_sensor,temp1_c,temp2_c
#
# Commands sent to the Pico:
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
    QSpinBox, QFormLayout, QCheckBox, QLineEdit, QFrame
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QFont

import pyqtgraph as pg


# ─── Configuration ────────────────────────────────────────────────────────────
BAUD_RATE = 115200

RAW_BUFFER_SIZE = 20000

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

                self.signals.status_msg.emit(
                    "Connection lost."
                )

                self.signals.connected.emit(False)

                break


# ─── Main window ──────────────────────────────────────────────────────────────
class QCMFTWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle(
            "QCM — Frequency vs Temperature Characterization"
        )

        self.setMinimumSize(1150, 850)

        self.reader = None
        self.signals = SerialSignals()

        self.state = STATE_IDLE


        # ──────────────────────────────────────────────────────────────────────
        # RAW DATA
        # ──────────────────────────────────────────────────────────────────────

        self.time_data = deque(maxlen=RAW_BUFFER_SIZE)

        self.freq_data = deque(maxlen=RAW_BUFFER_SIZE)

        # Thermistor 1 → QCM / crystal temperature
        self.temp1_data = deque(maxlen=RAW_BUFFER_SIZE)

        # Thermistor 2 → ambient / reference temperature
        self.temp2_data = deque(maxlen=RAW_BUFFER_SIZE)


        # ──────────────────────────────────────────────────────────────────────
        # ACCEPTED CHARACTERIZATION POINTS
        # ──────────────────────────────────────────────────────────────────────

        self.point_numbers = []

        self.point_temp1 = []
        self.point_temp2 = []

        self.point_freq = []

        self.point_temp1_std = []
        self.point_temp2_std = []

        self.point_freq_std = []

        self.point_freq_drift = []

        self.point_temp1_drift = []
        self.point_temp2_drift = []

        self.point_n = []


        # CSV
        self.csv_file = None
        self.csv_writer = None
        self.csv_filename = None


        # Latest rolling statistics
        self.current_stats = None


        self._build_ui()

        self._connect_signals()

        self._refresh_ports()


        self.ui_timer = QTimer()

        self.ui_timer.setInterval(500)

        self.ui_timer.timeout.connect(
            self._update_ui
        )

        self.ui_timer.start()


    # ─── UI ───────────────────────────────────────────────────────────────────
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

        self.setCentralWidget(central)


        root = QVBoxLayout(central)

        root.setSpacing(8)

        root.setContentsMargins(
            12, 12, 12, 12
        )


        # ──────────────────────────────────────────────────────────────────────
        # CONNECTION
        # ──────────────────────────────────────────────────────────────────────

        top_row = QHBoxLayout()


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
            32
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


        # ──────────────────────────────────────────────────────────────────────
        # SETTINGS
        # ──────────────────────────────────────────────────────────────────────

        settings_box = QGroupBox(
            "Characterization settings"
        )

        settings_lay = QFormLayout(
            settings_box
        )


        self.spin_window = QSpinBox()

        self.spin_window.setRange(
            5,
            600
        )

        self.spin_window.setValue(
            60
        )

        self.spin_window.setSuffix(
            " s"
        )

        self.spin_window.setToolTip(
            "When Add measurement is pressed, "
            "the samples from the latest time window are averaged."
        )


        settings_lay.addRow(
            "Averaging window:",
            self.spin_window
        )


        top_row.addWidget(
            settings_box
        )


        # ──────────────────────────────────────────────────────────────────────
        # CONTROL
        # ──────────────────────────────────────────────────────────────────────

        ctrl_box = QGroupBox(
            "Control"
        )

        ctrl_lay = QVBoxLayout(
            ctrl_box
        )


        self.btn_start = QPushButton(
            "▶  Start acquisition"
        )

        self.btn_start.setFixedHeight(
            38
        )

        self.btn_start.setEnabled(
            False
        )

        self.btn_start.clicked.connect(
            self._toggle_measurement
        )


        ctrl_lay.addWidget(
            self.btn_start
        )


        top_row.addWidget(
            ctrl_box
        )


        root.addLayout(
            top_row
        )


        # ──────────────────────────────────────────────────────────────────────
        # CSV
        # ──────────────────────────────────────────────────────────────────────

        csv_box = QGroupBox(
            "Save accepted points"
        )

        csv_lay = QHBoxLayout(
            csv_box
        )


        self.chk_csv = QCheckBox(
            "Save to CSV"
        )

        self.chk_csv.setChecked(
            False
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
            "e.g. crystal_01_f_vs_t"
        )

        self.txt_csv_name.setMinimumWidth(
            240
        )

        self.txt_csv_name.setEnabled(
            False
        )


        csv_lay.addWidget(
            self.txt_csv_name
        )


        csv_lay.addWidget(
            QLabel(".csv")
        )


        self.lbl_csv_status = QLabel(
            "—"
        )

        self.lbl_csv_status.setStyleSheet(
            "color: #6c7086; font-size: 10px;"
        )


        csv_lay.addWidget(
            self.lbl_csv_status
        )


        csv_lay.addStretch()


        root.addWidget(
            csv_box
        )


        # ──────────────────────────────────────────────────────────────────────
        # LIVE VALUES
        # ──────────────────────────────────────────────────────────────────────

        values_box = QGroupBox(
            "Live values"
        )

        values_lay = QHBoxLayout(
            values_box
        )


        self.lbl_freq = self._make_value_box(
            "Current frequency",
            "—",
            "#89b4fa"
        )


        self.lbl_temp1 = self._make_value_box(
            "T1 — Crystal / QCM",
            "—",
            "#fab387"
        )


        self.lbl_temp2 = self._make_value_box(
            "T2 — Ambient",
            "—",
            "#f9e2af"
        )


        self.lbl_delta_temp = self._make_value_box(
            "ΔT = T1 − T2",
            "—",
            "#94e2d5"
        )


        self.lbl_samples = self._make_value_box(
            "Samples in window",
            "—",
            "#a6e3a1"
        )


        widgets = [
            self.lbl_freq[0],
            self.lbl_temp1[0],
            self.lbl_temp2[0],
            self.lbl_delta_temp[0],
            self.lbl_samples[0]
        ]


        for i, widget in enumerate(widgets):

            values_lay.addWidget(
                widget
            )

            if i < len(widgets) - 1:

                sep = QFrame()

                sep.setFrameShape(
                    QFrame.VLine
                )

                sep.setStyleSheet(
                    "color: #313244;"
                )

                values_lay.addWidget(
                    sep
                )


        root.addWidget(
            values_box
        )


        # ──────────────────────────────────────────────────────────────────────
        # STATISTICS
        # ──────────────────────────────────────────────────────────────────────

        stats_box = QGroupBox(
            "Statistics of the latest averaging window"
        )

        stats_lay = QHBoxLayout(
            stats_box
        )


        self.lbl_f_mean = self._make_value_box(
            "Mean frequency",
            "—",
            "#89b4fa"
        )

        self.lbl_f_std = self._make_value_box(
            "σ frequency",
            "—",
            "#f38ba8"
        )

        self.lbl_t1_mean = self._make_value_box(
            "Mean T1",
            "—",
            "#fab387"
        )

        self.lbl_t1_std = self._make_value_box(
            "σ T1",
            "—",
            "#f9e2af"
        )

        self.lbl_t2_mean = self._make_value_box(
            "Mean T2",
            "—",
            "#f9e2af"
        )

        self.lbl_t2_std = self._make_value_box(
            "σ T2",
            "—",
            "#f9e2af"
        )

        self.lbl_f_drift = self._make_value_box(
            "Frequency drift",
            "—",
            "#cba6f7"
        )

        self.lbl_t1_drift = self._make_value_box(
            "T1 drift",
            "—",
            "#94e2d5"
        )

        self.lbl_t2_drift = self._make_value_box(
            "T2 drift",
            "—",
            "#94e2d5"
        )


        stat_widgets = [
            self.lbl_f_mean[0],
            self.lbl_f_std[0],
            self.lbl_t1_mean[0],
            self.lbl_t1_std[0],
            self.lbl_t2_mean[0],
            self.lbl_t2_std[0],
            self.lbl_f_drift[0],
            self.lbl_t1_drift[0],
            self.lbl_t2_drift[0]
        ]


        for i, widget in enumerate(stat_widgets):

            stats_lay.addWidget(
                widget
            )

            if i < len(stat_widgets) - 1:

                sep = QFrame()

                sep.setFrameShape(
                    QFrame.VLine
                )

                sep.setStyleSheet(
                    "color: #313244;"
                )

                stats_lay.addWidget(
                    sep
                )


        root.addWidget(
            stats_box
        )


        # ──────────────────────────────────────────────────────────────────────
        # POINT CONTROLS
        # ──────────────────────────────────────────────────────────────────────

        point_box = QGroupBox(
            "Characterization points"
        )

        point_lay = QHBoxLayout(
            point_box
        )


        self.btn_add_point = QPushButton(
            "●  Add measurement"
        )

        self.btn_add_point.setFixedHeight(
            45
        )

        self.btn_add_point.setEnabled(
            False
        )

        self.btn_add_point.setStyleSheet("""
            QPushButton {
                background:#40a02b;
                color:white;
                border-radius:6px;
                font-weight:bold;
                font-size:14px;
            }

            QPushButton:hover {
                background:#5dbc3f;
            }

            QPushButton:disabled {
                background:#45475a;
                color:#6c7086;
            }
        """)

        self.btn_add_point.clicked.connect(
            self._add_measurement
        )


        point_lay.addWidget(
            self.btn_add_point,
            stretch=3
        )


        self.btn_delete_point = QPushButton(
            "↶  Delete last point"
        )

        self.btn_delete_point.setFixedHeight(
            45
        )

        self.btn_delete_point.setEnabled(
            False
        )

        self.btn_delete_point.clicked.connect(
            self._delete_last_point
        )


        point_lay.addWidget(
            self.btn_delete_point,
            stretch=1
        )


        self.lbl_point_count = QLabel(
            "Accepted points: 0"
        )

        self.lbl_point_count.setAlignment(
            Qt.AlignCenter
        )

        self.lbl_point_count.setMinimumWidth(
            150
        )


        point_lay.addWidget(
            self.lbl_point_count
        )


        root.addWidget(
            point_box
        )


        # ──────────────────────────────────────────────────────────────────────
        # SCATTER
        # ──────────────────────────────────────────────────────────────────────

        self.plot = pg.PlotWidget(
            title="Crystal frequency vs QCM temperature (T1)"
        )

        self.plot.showGrid(
            x=True,
            y=True,
            alpha=0.2
        )

        self.plot.setLabel(
            "bottom",
            "QCM temperature T1",
            units="°C"
        )

        self.plot.setLabel(
            "left",
            "Frequency",
            units="Hz"
        )


        self.scatter = self.plot.plot(
            [],
            [],
            pen=None,
            symbol="o",
            symbolSize=10,
            symbolBrush="#89b4fa",
            symbolPen=pg.mkPen(
                "#cdd6f4",
                width=1
            )
        )


        root.addWidget(
            self.plot,
            stretch=1
        )


        # ──────────────────────────────────────────────────────────────────────
        # LAST POINT
        # ──────────────────────────────────────────────────────────────────────

        self.lbl_last_point = QLabel(
            "Last accepted point: —"
        )

        self.lbl_last_point.setAlignment(
            Qt.AlignCenter
        )

        self.lbl_last_point.setStyleSheet(
            "background:#181825; "
            "color:#cdd6f4; "
            "padding:7px; "
            "border-radius:4px;"
        )


        root.addWidget(
            self.lbl_last_point
        )


        # ──────────────────────────────────────────────────────────────────────
        # STATUS
        # ──────────────────────────────────────────────────────────────────────

        self.status_bar = QStatusBar()

        self.setStatusBar(
            self.status_bar
        )

        self.status_bar.showMessage(
            "Not connected."
        )


    def _make_value_box(
        self,
        title,
        value,
        color
    ):

        box = QWidget()

        lay = QVBoxLayout(box)

        lay.setContentsMargins(
            8, 4, 8, 4
        )


        title_label = QLabel(
            title
        )

        title_label.setAlignment(
            Qt.AlignCenter
        )

        title_label.setStyleSheet(
            f"color:{color}; font-size:10px;"
        )


        value_label = QLabel(
            value
        )

        value_label.setAlignment(
            Qt.AlignCenter
        )

        value_label.setFont(
            QFont(
                "Courier New",
                12,
                QFont.Bold
            )
        )

        value_label.setStyleSheet(
            f"color:{color};"
        )


        lay.addWidget(
            title_label
        )

        lay.addWidget(
            value_label
        )


        return (
            box,
            value_label
        )


    # ─── Signals ──────────────────────────────────────────────────────────────
    def _connect_signals(self):

        self.signals.data_received.connect(
            self._on_data
        )

        self.signals.status_msg.connect(
            lambda msg:
            self.status_bar.showMessage(msg)
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
                "Pico not detected automatically — "
                "select the port manually."
            )


        else:

            self.status_bar.showMessage(
                "No serial ports found."
            )


    # ─── Connection ───────────────────────────────────────────────────────────
    def _toggle_connect(self):

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


    def _on_connection_change(
        self,
        connected
    ):

        if connected:

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

            self._stop_measurement()


    # ─── Measurement control ──────────────────────────────────────────────────
    def _toggle_measurement(self):

        if self.state == STATE_IDLE:

            self._start_measurement()


        else:

            if self.reader:
                self.reader.send(
                    "STOP"
                )

            self._stop_measurement()


    def _start_measurement(self):

        if not self.reader:
            return


        # Clear raw buffers
        self.time_data.clear()
        self.freq_data.clear()

        self.temp1_data.clear()
        self.temp2_data.clear()

        self.current_stats = None


        # Clear accepted points
        self.point_numbers.clear()

        self.point_temp1.clear()
        self.point_temp2.clear()

        self.point_freq.clear()

        self.point_temp1_std.clear()
        self.point_temp2_std.clear()

        self.point_freq_std.clear()

        self.point_freq_drift.clear()

        self.point_temp1_drift.clear()
        self.point_temp2_drift.clear()

        self.point_n.clear()


        self.scatter.setData(
            [],
            []
        )


        self.lbl_point_count.setText(
            "Accepted points: 0"
        )

        self.lbl_last_point.setText(
            "Last accepted point: —"
        )


        self.state = STATE_MEAS


        # IMPORTANT:
        # 1 crystal + 2 thermistors
        self.reader.send(
            "START_1_2TEMP"
        )


        self._open_csv()


        self.btn_start.setText(
            "⏹  Stop acquisition"
        )

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


        self.btn_add_point.setEnabled(
            False
        )

        self.btn_delete_point.setEnabled(
            False
        )


        self.chk_csv.setEnabled(
            False
        )

        self.txt_csv_name.setEnabled(
            False
        )

        self.spin_window.setEnabled(
            False
        )


        self.status_bar.showMessage(
            "Acquisition started — "
            "waiting for enough samples to fill the averaging window."
        )


    def _stop_measurement(self):

        if self.state == STATE_IDLE:
            return


        self.state = STATE_IDLE


        self._close_csv()


        self.btn_start.setText(
            "▶  Start acquisition"
        )

        self.btn_start.setStyleSheet(
            ""
        )


        self.btn_add_point.setEnabled(
            False
        )

        self.btn_delete_point.setEnabled(
            bool(self.point_numbers)
        )


        self.chk_csv.setEnabled(
            True
        )

        self.txt_csv_name.setEnabled(
            self.chk_csv.isChecked()
        )

        self.spin_window.setEnabled(
            True
        )


        self.status_bar.showMessage(
            "Acquisition stopped."
        )


    # ─── Incoming serial data ─────────────────────────────────────────────────
    def _on_data(self, line):

        if self.state != STATE_MEAS:
            return


        parts = [
            p.strip()
            for p in line.split(",")
        ]


        try:

            # Expected:
            #
            # timestamp_s,
            # f_sensor,
            # temp1_c,
            # temp2_c

            if len(parts) < 4:
                return


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


            self.time_data.append(
                timestamp
            )

            self.freq_data.append(
                frequency
            )

            self.temp1_data.append(
                temp1
            )

            self.temp2_data.append(
                temp2
            )


        except (
            ValueError,
            IndexError
        ):

            pass


    # ─── Rolling window statistics ────────────────────────────────────────────
    def _get_window_stats(self):

        if len(self.time_data) < 2:
            return None


        t_all = list(
            self.time_data
        )

        f_all = list(
            self.freq_data
        )

        temp1_all = list(
            self.temp1_data
        )

        temp2_all = list(
            self.temp2_data
        )


        latest_t = t_all[-1]

        window_s = float(
            self.spin_window.value()
        )

        lower_t = (
            latest_t
            - window_s
        )


        indices = [
            i
            for i, t in enumerate(t_all)
            if t >= lower_t
        ]


        if len(indices) < 2:
            return None


        t = [
            t_all[i]
            for i in indices
        ]

        f = [
            f_all[i]
            for i in indices
        ]

        temp1 = [
            temp1_all[i]
            for i in indices
        ]

        temp2 = [
            temp2_all[i]
            for i in indices
        ]


        time_span = (
            t[-1]
            - t[0]
        )


        # Means
        f_mean = statistics.fmean(
            f
        )

        temp1_mean = statistics.fmean(
            temp1
        )

        temp2_mean = statistics.fmean(
            temp2
        )


        # Standard deviations
        f_std = statistics.stdev(
            f
        )

        temp1_std = statistics.stdev(
            temp1
        )

        temp2_std = statistics.stdev(
            temp2
        )


        # Linear slopes
        f_slope = self._linear_slope(
            t,
            f
        )

        temp1_slope = self._linear_slope(
            t,
            temp1
        )

        temp2_slope = self._linear_slope(
            t,
            temp2
        )


        return {

            "n": len(t),

            "time_span": time_span,

            "window_complete":
                time_span >= 0.90 * window_s,

            "f_mean":
                f_mean,

            "f_std":
                f_std,

            "temp1_mean":
                temp1_mean,

            "temp1_std":
                temp1_std,

            "temp2_mean":
                temp2_mean,

            "temp2_std":
                temp2_std,

            "delta_temp_mean":
                temp1_mean - temp2_mean,

            "f_drift_hz_min":
                f_slope * 60.0,

            "temp1_drift_c_min":
                temp1_slope * 60.0,

            "temp2_drift_c_min":
                temp2_slope * 60.0
        }


    @staticmethod
    def _linear_slope(
        x,
        y
    ):

        n = len(x)

        if n < 2:
            return 0.0


        x0 = x[0]

        xr = [
            xi - x0
            for xi in x
        ]


        x_mean = statistics.fmean(
            xr
        )

        y_mean = statistics.fmean(
            y
        )


        numerator = sum(
            (xi - x_mean)
            *
            (yi - y_mean)

            for xi, yi
            in zip(xr, y)
        )


        denominator = sum(
            (xi - x_mean) ** 2
            for xi in xr
        )


        if denominator == 0:
            return 0.0


        return (
            numerator
            / denominator
        )


    # ─── UI update ────────────────────────────────────────────────────────────
    def _update_ui(self):

        # ── Latest instantaneous values ───────────────────────────────────────

        if self.freq_data:

            self.lbl_freq[1].setText(
                f"{self.freq_data[-1]:,.3f} Hz"
            )

        else:

            self.lbl_freq[1].setText(
                "—"
            )


        if self.temp1_data:

            self.lbl_temp1[1].setText(
                f"{self.temp1_data[-1]:.2f} °C"
            )

        else:

            self.lbl_temp1[1].setText(
                "—"
            )


        if self.temp2_data:

            self.lbl_temp2[1].setText(
                f"{self.temp2_data[-1]:.2f} °C"
            )

        else:

            self.lbl_temp2[1].setText(
                "—"
            )


        if (
            self.temp1_data
            and self.temp2_data
        ):

            delta_t = (
                self.temp1_data[-1]
                - self.temp2_data[-1]
            )

            self.lbl_delta_temp[1].setText(
                f"{delta_t:+.2f} °C"
            )

        else:

            self.lbl_delta_temp[1].setText(
                "—"
            )


        # ── Rolling statistics ────────────────────────────────────────────────

        stats = self._get_window_stats()

        self.current_stats = stats


        if stats is None:

            self.lbl_samples[1].setText(
                "—"
            )

            self.lbl_f_mean[1].setText(
                "—"
            )

            self.lbl_f_std[1].setText(
                "—"
            )

            self.lbl_t1_mean[1].setText(
                "—"
            )

            self.lbl_t1_std[1].setText(
                "—"
            )

            self.lbl_t2_mean[1].setText(
                "—"
            )

            self.lbl_t2_std[1].setText(
                "—"
            )

            self.lbl_f_drift[1].setText(
                "—"
            )

            self.lbl_t1_drift[1].setText(
                "—"
            )

            self.lbl_t2_drift[1].setText(
                "—"
            )


            self.btn_add_point.setEnabled(
                False
            )

            return


        self.lbl_samples[1].setText(
            str(stats["n"])
        )


        self.lbl_f_mean[1].setText(
            f'{stats["f_mean"]:,.3f} Hz'
        )

        self.lbl_f_std[1].setText(
            f'{stats["f_std"]:.3f} Hz'
        )


        self.lbl_t1_mean[1].setText(
            f'{stats["temp1_mean"]:.3f} °C'
        )

        self.lbl_t1_std[1].setText(
            f'{stats["temp1_std"]:.3f} °C'
        )


        self.lbl_t2_mean[1].setText(
            f'{stats["temp2_mean"]:.3f} °C'
        )

        self.lbl_t2_std[1].setText(
            f'{stats["temp2_std"]:.3f} °C'
        )


        self.lbl_f_drift[1].setText(
            f'{stats["f_drift_hz_min"]:+.3f} Hz/min'
        )

        self.lbl_t1_drift[1].setText(
            f'{stats["temp1_drift_c_min"]:+.4f} °C/min'
        )

        self.lbl_t2_drift[1].setText(
            f'{stats["temp2_drift_c_min"]:+.4f} °C/min'
        )


        self.btn_add_point.setEnabled(
            self.state == STATE_MEAS
            and stats["window_complete"]
        )


        if (
            self.state == STATE_MEAS
            and not stats["window_complete"]
        ):

            remaining = max(
                0.0,
                self.spin_window.value()
                - stats["time_span"]
            )

            self.status_bar.showMessage(
                "Filling averaging window — "
                f"approximately {remaining:.0f} s remaining."
            )


    # ─── Add point ────────────────────────────────────────────────────────────
    def _add_measurement(self):

        stats = self._get_window_stats()


        if (
            stats is None
            or not stats["window_complete"]
        ):

            self.status_bar.showMessage(
                "Not enough data yet to fill "
                "the selected averaging window."
            )

            return


        point = (
            len(self.point_numbers)
            + 1
        )


        self.point_numbers.append(
            point
        )


        self.point_temp1.append(
            stats["temp1_mean"]
        )

        self.point_temp2.append(
            stats["temp2_mean"]
        )

        self.point_freq.append(
            stats["f_mean"]
        )


        self.point_temp1_std.append(
            stats["temp1_std"]
        )

        self.point_temp2_std.append(
            stats["temp2_std"]
        )

        self.point_freq_std.append(
            stats["f_std"]
        )


        self.point_freq_drift.append(
            stats["f_drift_hz_min"]
        )

        self.point_temp1_drift.append(
            stats["temp1_drift_c_min"]
        )

        self.point_temp2_drift.append(
            stats["temp2_drift_c_min"]
        )


        self.point_n.append(
            stats["n"]
        )


        self._update_scatter()


        self.lbl_point_count.setText(
            f"Accepted points: "
            f"{len(self.point_numbers)}"
        )


        self.lbl_last_point.setText(
            f'Point #{point}:   '
            f'T1 = {stats["temp1_mean"]:.3f} '
            f'± {stats["temp1_std"]:.3f} °C   |   '
            f'T2 = {stats["temp2_mean"]:.3f} '
            f'± {stats["temp2_std"]:.3f} °C   |   '
            f'ΔT = {stats["delta_temp_mean"]:+.3f} °C   |   '
            f'f = {stats["f_mean"]:,.3f} '
            f'± {stats["f_std"]:.3f} Hz'
        )


        self.btn_delete_point.setEnabled(
            True
        )


        self._write_csv_point(
            point,
            stats
        )


        self.status_bar.showMessage(
            f'Point #{point} added — '
            f'T1={stats["temp1_mean"]:.3f} °C, '
            f'T2={stats["temp2_mean"]:.3f} °C, '
            f'f={stats["f_mean"]:,.3f} Hz'
        )


    # ─── Delete point ─────────────────────────────────────────────────────────
    def _delete_last_point(self):

        if not self.point_numbers:
            return


        deleted_point = (
            self.point_numbers[-1]
        )


        self.point_numbers.pop()

        self.point_temp1.pop()
        self.point_temp2.pop()

        self.point_freq.pop()

        self.point_temp1_std.pop()
        self.point_temp2_std.pop()

        self.point_freq_std.pop()

        self.point_freq_drift.pop()

        self.point_temp1_drift.pop()
        self.point_temp2_drift.pop()

        self.point_n.pop()


        self._update_scatter()


        self.lbl_point_count.setText(
            f"Accepted points: "
            f"{len(self.point_numbers)}"
        )


        self.btn_delete_point.setEnabled(
            bool(self.point_numbers)
        )


        if self.point_numbers:

            i = -1

            delta_t = (
                self.point_temp1[i]
                - self.point_temp2[i]
            )


            self.lbl_last_point.setText(
                f'Point #{self.point_numbers[i]}:   '
                f'T1 = {self.point_temp1[i]:.3f} '
                f'± {self.point_temp1_std[i]:.3f} °C   |   '
                f'T2 = {self.point_temp2[i]:.3f} '
                f'± {self.point_temp2_std[i]:.3f} °C   |   '
                f'ΔT = {delta_t:+.3f} °C   |   '
                f'f = {self.point_freq[i]:,.3f} '
                f'± {self.point_freq_std[i]:.3f} Hz'
            )

        else:

            self.lbl_last_point.setText(
                "Last accepted point: —"
            )


        if self.csv_filename:
            self._rewrite_csv()


        self.status_bar.showMessage(
            f"Point #{deleted_point} deleted."
        )


    # ─── Scatter plot ─────────────────────────────────────────────────────────
    def _update_scatter(self):

        # X axis = T1 (QCM/crystal temperature)
        # Y axis = crystal frequency

        self.scatter.setData(
            self.point_temp1,
            self.point_freq,
            pen=None,
            symbol="o",
            symbolSize=10,
            symbolBrush="#89b4fa",
            symbolPen=pg.mkPen(
                "#cdd6f4",
                width=1
            )
        )


    # ─── CSV ──────────────────────────────────────────────────────────────────
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

            self.lbl_csv_status.setStyleSheet(
                "color: #6c7086; "
                "font-size: 10px;"
            )


    def _open_csv(self):

        if not self.chk_csv.isChecked():

            self.csv_filename = None

            return


        name = (
            self.txt_csv_name
            .text()
            .strip()
        )


        if not name:

            name = (
                "qcm_f_vs_t_"
                + datetime.now().strftime(
                    "%Y%m%d_%H%M%S"
                )
            )

            self.txt_csv_name.setText(
                name
            )


        filename = (
            f"{name}.csv"
        )


        base, ext = os.path.splitext(
            filename
        )


        counter = 1

        while os.path.exists(
            filename
        ):

            filename = (
                f"{base}_{counter}{ext}"
            )

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


            self._write_csv_header()

            self.csv_file.flush()


            self.lbl_csv_status.setText(
                f"✓ Saving to {filename}"
            )

            self.lbl_csv_status.setStyleSheet(
                "color: #a6e3a1; "
                "font-size: 10px;"
            )


        except OSError as e:

            self.csv_file = None

            self.csv_writer = None

            self.csv_filename = None


            self.lbl_csv_status.setText(
                f"✗ Error: {e}"
            )

            self.lbl_csv_status.setStyleSheet(
                "color: #f38ba8; "
                "font-size: 10px;"
            )


    def _write_csv_header(self):

        self.csv_writer.writerow([
            "# QCM frequency vs temperature characterization — "
            + datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        ])


        self.csv_writer.writerow([
            f"# averaging_window_s="
            f"{self.spin_window.value()}"
        ])


        self.csv_writer.writerow([
            "# T1 = QCM/crystal temperature"
        ])

        self.csv_writer.writerow([
            "# T2 = ambient/reference temperature"
        ])


        self.csv_writer.writerow([
            "point",

            "temp1_mean_c",
            "temp1_std_c",

            "temp2_mean_c",
            "temp2_std_c",

            "delta_temp_c",

            "f_mean_hz",
            "f_std_hz",

            "f_drift_hz_min",

            "temp1_drift_c_min",
            "temp2_drift_c_min",

            "n_samples"
        ])


    def _write_csv_point(
        self,
        point,
        stats
    ):

        if self.csv_writer is None:
            return


        self.csv_writer.writerow([

            point,

            f'{stats["temp1_mean"]:.5f}',
            f'{stats["temp1_std"]:.5f}',

            f'{stats["temp2_mean"]:.5f}',
            f'{stats["temp2_std"]:.5f}',

            f'{stats["delta_temp_mean"]:.5f}',

            f'{stats["f_mean"]:.5f}',
            f'{stats["f_std"]:.5f}',

            f'{stats["f_drift_hz_min"]:.6f}',

            f'{stats["temp1_drift_c_min"]:.6f}',
            f'{stats["temp2_drift_c_min"]:.6f}',

            stats["n"]
        ])


        self.csv_file.flush()


    def _rewrite_csv(self):

        if not self.csv_filename:
            return


        try:

            if self.csv_file:
                self.csv_file.close()


            self.csv_file = open(
                self.csv_filename,
                "w",
                newline="",
                encoding="utf-8"
            )


            self.csv_writer = csv.writer(
                self.csv_file
            )


            self._write_csv_header()


            for i in range(
                len(self.point_numbers)
            ):

                delta_t = (
                    self.point_temp1[i]
                    - self.point_temp2[i]
                )


                self.csv_writer.writerow([

                    self.point_numbers[i],

                    f"{self.point_temp1[i]:.5f}",
                    f"{self.point_temp1_std[i]:.5f}",

                    f"{self.point_temp2[i]:.5f}",
                    f"{self.point_temp2_std[i]:.5f}",

                    f"{delta_t:.5f}",

                    f"{self.point_freq[i]:.5f}",
                    f"{self.point_freq_std[i]:.5f}",

                    f"{self.point_freq_drift[i]:.6f}",

                    f"{self.point_temp1_drift[i]:.6f}",
                    f"{self.point_temp2_drift[i]:.6f}",

                    self.point_n[i]
                ])


            self.csv_file.flush()


        except OSError as e:

            self.status_bar.showMessage(
                f"CSV rewrite error: {e}"
            )


    def _close_csv(self):

        if self.csv_file:

            self.csv_file.close()

            self.csv_file = None

            self.csv_writer = None


            self.lbl_csv_status.setText(
                "✓ File saved and closed"
            )

            self.lbl_csv_status.setStyleSheet(
                "color: #6c7086; "
                "font-size: 10px;"
            )


    # ─── Window close ─────────────────────────────────────────────────────────
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


# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":

    app = QApplication(
        sys.argv
    )

    app.setStyle(
        "Fusion"
    )


    win = QCMFTWindow()

    win.show()


    sys.exit(
        app.exec_()
    )