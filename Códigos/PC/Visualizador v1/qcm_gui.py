# qcm_gui.py  —  PC
# PyQt5 GUI for QCM with automatic f₀ calibration
#
# Installation:
#   pip install PyQt5 pyserial pyqtgraph
#
# Usage:
#   python qcm_gui.py

import sys
import threading
import serial
import serial.tools.list_ports
from collections import deque
import math

import csv
import os
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QGroupBox, QStatusBar,
    QDoubleSpinBox, QFormLayout, QFrame, QCheckBox, QLineEdit
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QFont
import pyqtgraph as pg

# ─── Configuration ────────────────────────────────────────────────────────────
BAUD_RATE      = 115200
MAX_POINTS            = 300   # visible points in the plots
CALIB_SAMPLES_SINGLE  = 30    # samples used to calibrate f₀ in single-crystal mode
CALIB_SECONDS_DUAL    = 5     # keeps the previous time-based calibration in dual mode

# Physical constants for AT-cut quartz
RHO_QUARTZ = 2648.0    # kg/m³
MU_QUARTZ  = 2.947e10  # N/m²

# Measurement states
STATE_IDLE   = "idle"
STATE_CALIB  = "calib"
STATE_MEAS   = "measuring"

# ─── Signals ──────────────────────────────────────────────────────────────────
class SerialSignals(QObject):
    data_received = pyqtSignal(str)
    status_msg    = pyqtSignal(str)
    connected     = pyqtSignal(bool)

# ─── Serial reader thread ─────────────────────────────────────────────────────
class SerialReader(threading.Thread):
    def __init__(self, port, signals):
        super().__init__(daemon=True)
        self.port    = port
        self.signals = signals
        self.ser     = None
        self._stop   = threading.Event()

    def send(self, cmd):
        if self.ser and self.ser.is_open:
            self.ser.write((cmd + "\n").encode())

    def stop(self):
        self._stop.set()
        if self.ser and self.ser.is_open:
            self.ser.close()

    def run(self):
        try:
            self.ser = serial.Serial(self.port, BAUD_RATE, timeout=1)
            self.signals.connected.emit(True)
            self.signals.status_msg.emit(f"Connected to {self.port}")
        except serial.SerialException as e:
            self.signals.status_msg.emit(f"Error: {e}")
            self.signals.connected.emit(False)
            return

        while not self._stop.is_set():
            try:
                line = self.ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                if line.startswith("#") or line.startswith("QCM") or line.startswith("OK"):
                    self.signals.status_msg.emit(line.lstrip("#").strip())
                elif "," in line and not line.startswith("timestamp"):
                    self.signals.data_received.emit(line)
            except serial.SerialException:
                self.signals.status_msg.emit("Connection lost.")
                self.signals.connected.emit(False)
                break

# ─── Main window ───────────────────────────────────────────────────────────────
class QCMWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QCM — Deposition Monitor")
        self.setMinimumSize(950, 780)

        self.reader   = None
        self.signals  = SerialSignals()
        self.dual     = False
        self.use_temp = False
        self.state    = STATE_IDLE

        # CSV
        self.csv_file   = None
        self.csv_writer = None

        # Calibration
        self.calib_samples = []   # f_sensor samples collected during calibration
        self.f0_real       = None # measured f₀ after calibration
        self.calib_count   = 0    # elapsed seconds during calibration

        # Measurement buffers
        self.t_data     = deque(maxlen=MAX_POINTS)
        self.fs_data    = deque(maxlen=MAX_POINTS)
        self.fr_data    = deque(maxlen=MAX_POINTS)
        self.df_data    = deque(maxlen=MAX_POINTS)
        self.thick_data = deque(maxlen=MAX_POINTS)
        self.temp_data  = deque(maxlen=MAX_POINTS)

        self._build_ui()
        self._connect_signals()
        self._refresh_ports()

        # Countdown timer used only for dual-mode calibration (1 tick/s)
        self.calib_timer = QTimer()
        self.calib_timer.setInterval(1000)
        self.calib_timer.timeout.connect(self._calib_tick)

        # Plot refresh timer
        self.plot_timer = QTimer()
        self.plot_timer.setInterval(1000)
        self.plot_timer.timeout.connect(self._update_plots)

    # ── UI construction ────────────────────────────────────────────────────────
    def _build_ui(self):
        pg.setConfigOption("background", "#1e1e2e")
        pg.setConfigOption("foreground", "#cdd6f4")

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        # ── Top row ────────────────────────────────────────────────────────────
        top_row = QHBoxLayout()

        # Connection
        conn_box = QGroupBox("Connection")
        conn_lay = QHBoxLayout(conn_box)
        conn_lay.addWidget(QLabel("Port:"))
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(200)
        conn_lay.addWidget(self.port_combo)
        self.btn_refresh = QPushButton("↻")
        self.btn_refresh.setFixedWidth(30)
        self.btn_refresh.clicked.connect(self._refresh_ports)
        conn_lay.addWidget(self.btn_refresh)
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.clicked.connect(self._toggle_connect)
        conn_lay.addWidget(self.btn_connect)
        top_row.addWidget(conn_box)

        # Parameters — only ρ and A (f₀ is measured automatically)
        param_box = QGroupBox("Material and crystal parameters")
        param_lay = QFormLayout(param_box)
        param_lay.setHorizontalSpacing(10)

        self.spin_rho = QDoubleSpinBox()
        self.spin_rho.setRange(100, 25000)
        self.spin_rho.setValue(2700)
        self.spin_rho.setDecimals(1)
        self.spin_rho.setMinimumWidth(150)
        param_lay.addRow("Material density (ρ) [kg/m³]:", self.spin_rho)

        self.spin_area = QDoubleSpinBox()
        self.spin_area.setRange(1e-6, 1)
        self.spin_area.setValue(1.327e-4)
        self.spin_area.setDecimals(6)
        self.spin_area.setSingleStep(1e-5)
        self.spin_area.setMinimumWidth(150)
        param_lay.addRow("Active crystal area (A) [m²]:", self.spin_area)

        # Measured f₀ — read-only, filled after calibration
        self.lbl_f0_val = QLabel("—")
        self.lbl_f0_val.setFont(QFont("Courier New", 10))
        self.lbl_f0_val.setStyleSheet("color: #a6e3a1;")
        param_lay.addRow("Measured f₀ (calibration) [Hz]:", self.lbl_f0_val)

        top_row.addWidget(param_box)

        # Control
        ctrl_box = QGroupBox("Control")
        ctrl_lay = QVBoxLayout(ctrl_box)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Crystals:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["1 crystal (sensor only)", "2 crystals (sensor + reference)"])
        self.mode_combo.setMinimumWidth(210)
        mode_row.addWidget(self.mode_combo)
        ctrl_lay.addLayout(mode_row)

        temp_row = QHBoxLayout()
        temp_row.addWidget(QLabel("Thermistor:"))
        self.temp_combo = QComboBox()
        self.temp_combo.addItems(["No thermistor", "With thermistor (NTC 100 kΩ)"])
        self.temp_combo.setMinimumWidth(210)
        temp_row.addWidget(self.temp_combo)
        ctrl_lay.addLayout(temp_row)

        # Calibration banner
        self.lbl_calib = QLabel("")
        self.lbl_calib.setAlignment(Qt.AlignCenter)
        self.lbl_calib.setStyleSheet(
            "background:#1e1e2e; color:#f9e2af; font-weight:bold;"
            "border-radius:4px; padding:4px;"
        )
        self.lbl_calib.hide()
        ctrl_lay.addWidget(self.lbl_calib)

        self.btn_start = QPushButton("▶  Start measurement")
        self.btn_start.setEnabled(False)
        self.btn_start.setFixedHeight(38)
        self._style_btn(STATE_IDLE)
        self.btn_start.clicked.connect(self._toggle_measurement)
        ctrl_lay.addWidget(self.btn_start)

        top_row.addWidget(ctrl_box)
        root.addLayout(top_row)

        # ── CSV saving panel ───────────────────────────────────────────────────
        csv_box = QGroupBox("Save data")
        csv_lay = QHBoxLayout(csv_box)

        self.chk_csv = QCheckBox("Save to CSV")
        self.chk_csv.setChecked(False)
        self.chk_csv.toggled.connect(self._on_csv_toggle)
        csv_lay.addWidget(self.chk_csv)

        csv_lay.addSpacing(10)
        csv_lay.addWidget(QLabel("File name:"))

        self.txt_csv_name = QLineEdit()
        self.txt_csv_name.setPlaceholderText("e.g. gold_measurement_01")
        self.txt_csv_name.setMinimumWidth(220)
        self.txt_csv_name.setEnabled(False)
        csv_lay.addWidget(self.txt_csv_name)

        csv_lay.addWidget(QLabel(".csv"))

        csv_lay.addSpacing(10)

        # File status indicator
        self.lbl_csv_status = QLabel("—")
        self.lbl_csv_status.setStyleSheet("color: #6c7086; font-size: 10px;")
        csv_lay.addWidget(self.lbl_csv_status)

        csv_lay.addStretch()
        root.addWidget(csv_box)
        val_box = QGroupBox("Current values")
        val_lay = QHBoxLayout(val_box)

        self.lbl_fs    = self._make_val_label("Sensor frequency",           "—", "#89b4fa")
        self.lbl_fr    = self._make_val_label("Reference frequency",       "—", "#a6e3a1")
        self.lbl_df    = self._make_val_label("Δf",                 "—", "#f38ba8")
        self.lbl_thick = self._make_val_label("Deposited thickness", "—", "#cba6f7")
        self.lbl_temp  = self._make_val_label("Temperature",        "—", "#fab387")

        for i, (w, _) in enumerate([self.lbl_fs, self.lbl_fr,
                                     self.lbl_df, self.lbl_thick, self.lbl_temp]):
            val_lay.addWidget(w)
            if i < 4:
                sep = QFrame()
                sep.setFrameShape(QFrame.VLine)
                sep.setStyleSheet("color: #313244;")
                val_lay.addWidget(sep)

        root.addWidget(val_box)

        # ── Plots ──────────────────────────────────────────────────────────────
        self.plot_thick = self._make_plot("Deposited thickness (nm)", "#cba6f7")
        self.plot_df    = self._make_plot("Δf = f_sensor − f_ref (Hz)", "#f38ba8",
                                          zero_line=True)

        root.addWidget(self.plot_thick[0], stretch=2)
        root.addWidget(self.plot_df[0],    stretch=1)

        # ── Status bar ─────────────────────────────────────────────────────────
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Not connected.")

    def _make_val_label(self, title, value, color):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 4, 10, 4)
        lt = QLabel(title)
        lt.setAlignment(Qt.AlignCenter)
        lt.setStyleSheet(f"color:{color}; font-size:10px;")
        lv = QLabel(value)
        lv.setAlignment(Qt.AlignCenter)
        lv.setFont(QFont("Courier New", 13, QFont.Bold))
        lv.setStyleSheet(f"color:{color};")
        lay.addWidget(lt)
        lay.addWidget(lv)
        return box, lv

    def _make_plot(self, title, color, zero_line=False):
        pw = pg.PlotWidget(title=title)
        pw.showGrid(x=True, y=True, alpha=0.2)
        pw.setLabel("bottom", "time (s)")
        curve = pw.plot(pen=pg.mkPen(color=color, width=2))
        if zero_line:
            pw.addLine(y=0, pen=pg.mkPen(color="#6c7086", style=Qt.DashLine))
        return pw, curve

    def _style_btn(self, state):
        if state == STATE_CALIB:
            self.btn_start.setStyleSheet("""
                QPushButton { background:#df8e1d; color:white; border-radius:6px;
                              font-weight:bold; font-size:13px; }
            """)
        elif state == STATE_MEAS:
            self.btn_start.setStyleSheet("""
                QPushButton { background:#d20f39; color:white; border-radius:6px;
                              font-weight:bold; font-size:13px; }
                QPushButton:hover { background:#e8305a; }
            """)
        else:
            self.btn_start.setStyleSheet("""
                QPushButton { background:#40a02b; color:white; border-radius:6px;
                              font-weight:bold; font-size:13px; }
                QPushButton:hover { background:#5dbc3f; }
                QPushButton:disabled { background:#45475a; color:#6c7086; }
            """)

    # ── Signals ───────────────────────────────────────────────────────────────
    def _connect_signals(self):
        self.signals.data_received.connect(self._on_data)
        self.signals.status_msg.connect(lambda m: self.status_bar.showMessage(m))
        self.signals.connected.connect(self._on_connection_change)

    # ── Ports ────────────────────────────────────────────────────────────────
    def _refresh_ports(self):
        self.port_combo.clear()
        ports = serial.tools.list_ports.comports()
        pico_port = None
        for p in ports:
            self.port_combo.addItem(f"{p.device}  —  {p.description}", p.device)
            if p.vid == 0x2E8A:
                pico_port = p.device
        if pico_port:
            idx = self.port_combo.findData(pico_port)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
            self.status_bar.showMessage(f"Pico detected on {pico_port}")
        elif ports:
            self.status_bar.showMessage("Pico not detected — select the port manually.")
        else:
            self.status_bar.showMessage("No serial ports found.")

    # ── Connection ───────────────────────────────────────────────────────────
    def _toggle_connect(self):
        if self.reader and self.reader.is_alive():
            self.reader.stop()
            self.reader = None
            self.btn_connect.setText("Connect")
            self.btn_start.setEnabled(False)
        else:
            port = self.port_combo.currentData()
            if not port:
                self.status_bar.showMessage("Select a port first.")
                return
            self.reader = SerialReader(port, self.signals)
            self.reader.start()

    def _on_connection_change(self, ok):
        if ok:
            self.btn_connect.setText("Disconnect")
            self.btn_start.setEnabled(True)
        else:
            self.btn_connect.setText("Connect")
            self.btn_start.setEnabled(False)
            self._enter_idle()

    # ── CSV ───────────────────────────────────────────────────────────────────
    def _on_csv_toggle(self, checked):
        self.txt_csv_name.setEnabled(checked)
        if not checked:
            self.lbl_csv_status.setText("—")
            self.lbl_csv_status.setStyleSheet("color: #6c7086; font-size: 10px;")

    def _open_csv(self):
        """Open the CSV file and write its header."""
        if not self.chk_csv.isChecked():
            return

        name = self.txt_csv_name.text().strip()
        if not name:
            # Automatic timestamp-based name if the field is empty
            name = f"qcm_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            self.txt_csv_name.setText(name)

        # Avoid overwriting — append a numeric suffix if the file exists
        filename = f"{name}.csv"
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(filename):
            filename = f"{base}_{counter}{ext}"
            counter += 1

        try:
            self.csv_file   = open(filename, "w", newline="", encoding="utf-8")
            self.csv_writer = csv.writer(self.csv_file)

            # Metadata header
            self.csv_writer.writerow([f"# QCM — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
            self.csv_writer.writerow([f"# f0_real={self.f0_real:.3f} Hz"])
            self.csv_writer.writerow([f"# material_density={self.spin_rho.value():.1f} kg/m3"])
            self.csv_writer.writerow([f"# mode={'dual' if self.dual else 'single'}"])

            # Column header
            cols = ["timestamp_s", "f_sensor_hz", "f_ref_hz", "delta_f_hz", "espesor_nm"]
            if self.use_temp:
                cols.append("temp_c")
            self.csv_writer.writerow(cols)
            self.csv_file.flush()

            self.lbl_csv_status.setText(f"✓ Saving to {filename}")
            self.lbl_csv_status.setStyleSheet("color: #a6e3a1; font-size: 10px;")
            self.status_bar.showMessage(f"CSV opened: {filename}")

        except OSError as e:
            self.lbl_csv_status.setText(f"✗ Error: {e}")
            self.lbl_csv_status.setStyleSheet("color: #f38ba8; font-size: 10px;")
            self.csv_file   = None
            self.csv_writer = None

    def _write_csv_row(self, ts, f_s, f_r, df, thickness, temp):
        """Write one data row to the CSV file."""
        if self.csv_writer is None:
            return
        row = [f"{ts:.1f}", f"{f_s:.3f}", f"{f_r:.3f}", f"{df:.3f}", f"{thickness:.6f}"]
        if self.use_temp and temp is not None:
            row.append(f"{temp:.2f}")
        self.csv_writer.writerow(row)
        self.csv_file.flush()   # write immediately instead of waiting for close

    def _close_csv(self):
        """Close the CSV file if it is open."""
        if self.csv_file:
            self.csv_file.close()
            self.csv_file   = None
            self.csv_writer = None
            self.lbl_csv_status.setText("✓ File saved and closed")
            self.lbl_csv_status.setStyleSheet("color: #6c7086; font-size: 10px;")

    # ── Sauerbrey ────────────────────────────────────────────────────────────
    def _calc_thickness(self, delta_f):
        """
        Ecuación de Sauerbrey:
            Δm [kg/m²] = -(√(ρq·μq) / (2·f₀²)) · Δf
            espesor [m] = Δm / ρ_material  →  convertir a nm

        Δf negativo (frecuencia baja → masa ganada) → espesor positivo.
        """
        f0  = self.f0_real
        rho = self.spin_rho.value()
        # Sauerbrey constant — units: kg/(m²·Hz)
        Cf  = math.sqrt(RHO_QUARTZ * MU_QUARTZ) / (2 * f0**2)
        dm  = -Cf * delta_f          # kg/m²  (positivo si Δf < 0)
        return (dm / rho) * 1e9      # meters → nanometers

    # ── State control ─────────────────────────────────────────────────────────
    def _toggle_measurement(self):
        if self.state == STATE_IDLE:
            self._enter_calib()
        else:
            # in both calibration and measurement, the button stops everything
            self.reader.send("STOP")
            self._enter_idle()

    def _enter_calib(self):
        """Start calibration. Single mode uses exactly 30 f_sensor samples."""
        self.state         = STATE_CALIB
        self.calib_samples = []
        self.calib_count   = 0
        self.f0_real       = None
        self.lbl_f0_val.setText("measuring…")

        self.dual     = (self.mode_combo.currentIndex() == 1)
        self.use_temp = (self.temp_combo.currentIndex() == 1)

        canal = "2" if self.dual else "1"
        therm = "TEMP" if self.use_temp else "NTEMP"
        self.reader.send(f"START_{canal}_{therm}")

        self._style_btn(STATE_CALIB)
        if self.dual:
            self.btn_start.setText(f"⏳  Calibrating… {CALIB_SECONDS_DUAL}s")
            self.lbl_calib.setText(
                f"⏳  Measuring clean-crystal f₀… ({CALIB_SECONDS_DUAL}s)"
            )
            self.calib_timer.start()
        else:
            self.btn_start.setText(
                f"⏳  Calibrating… 0/{CALIB_SAMPLES_SINGLE} samples"
            )
            self.lbl_calib.setText(
                f"⏳  Measuring clean-crystal f₀… 0/{CALIB_SAMPLES_SINGLE} samples"
            )

        self.lbl_calib.show()

        for w in [self.mode_combo, self.temp_combo, self.spin_rho, self.spin_area]:
            w.setEnabled(False)
        self.chk_csv.setEnabled(False)
        self.txt_csv_name.setEnabled(False)

    def _calib_tick(self):
        """Called every second during dual-mode calibration."""
        self.calib_count += 1
        remaining = CALIB_SECONDS_DUAL - self.calib_count
        self.btn_start.setText(
            f"⏳  Calibrating… {remaining}s" if remaining > 0 else "⏳  Calibrating…"
        )
        if self.calib_count >= CALIB_SECONDS_DUAL:
            self.calib_timer.stop()
            self._finish_calib()

    def _finish_calib(self):
        """Average calibration samples and enter measurement mode."""
        if self.calib_samples:
            self.f0_real = sum(self.calib_samples) / len(self.calib_samples)
            self.lbl_f0_val.setText(f"{self.f0_real:,.3f}")
            self.status_bar.showMessage(
                f"Calibration OK — f₀ = {self.f0_real:,.3f} Hz  "
                f"({len(self.calib_samples)} samples)"
            )
        else:
            # No llegaron datos durante la calibración
            self.status_bar.showMessage(
                "⚠ No data received during calibration — check the connection."
            )
            self._enter_idle()
            return

        # Clear measurement buffers and start plots
        self.state = STATE_MEAS
        self.t_data.clear(); self.fs_data.clear(); self.fr_data.clear()
        self.df_data.clear(); self.thick_data.clear(); self.temp_data.clear()

        # Open CSV if enabled
        self._open_csv()

        self.btn_start.setText("⏹  Stop measurement")
        self._style_btn(STATE_MEAS)
        self.lbl_calib.hide()
        self.plot_timer.start()

    def _enter_idle(self):
        self.state = STATE_IDLE
        self.calib_timer.stop()
        self.plot_timer.stop()
        self._close_csv()
        self.btn_start.setText("▶  Start measurement")
        self._style_btn(STATE_IDLE)
        self.lbl_calib.hide()
        for w in [self.mode_combo, self.temp_combo, self.spin_rho, self.spin_area]:
            w.setEnabled(True)
        # Re-enable CSV widgets
        self.chk_csv.setEnabled(True)
        if self.chk_csv.isChecked():
            self.txt_csv_name.setEnabled(True)

    # ── Datos entrantes ───────────────────────────────────────────────────────
    def _on_data(self, line):
        parts = line.split(",")
        try:
            f_s = float(parts[1])

            # ── During calibration: collect f_sensor samples ─────────────────
            if self.state == STATE_CALIB:
                self.calib_samples.append(f_s)

                # In single-crystal mode, f₀ is the average of exactly 30 samples.
                if not self.dual:
                    n = len(self.calib_samples)
                    self.btn_start.setText(
                        f"⏳  Calibrating… {n}/{CALIB_SAMPLES_SINGLE} samples"
                    )
                    self.lbl_calib.setText(
                        f"⏳  Measuring clean-crystal f₀… {n}/{CALIB_SAMPLES_SINGLE} samples"
                    )
                    if n >= CALIB_SAMPLES_SINGLE:
                        self.calib_samples = self.calib_samples[:CALIB_SAMPLES_SINGLE]
                        self._finish_calib()
                return

            # ── During measurement: calculate Δf and thickness ───────────────
            if self.state != STATE_MEAS or self.f0_real is None:
                return

            ts = float(parts[0])

            if self.dual and len(parts) >= 4:
                f_r  = float(parts[2])
                df   = float(parts[3])
                temp = float(parts[4]) if self.use_temp and len(parts) >= 5 else None
            else:
                f_r  = self.f0_real
                df   = f_s - self.f0_real
                temp = float(parts[2]) if self.use_temp and len(parts) >= 3 else None

            thickness = self._calc_thickness(df)

            self.t_data.append(ts)
            self.fs_data.append(f_s)
            self.fr_data.append(f_r)
            self.df_data.append(df)
            self.thick_data.append(thickness)
            if temp is not None:
                self.temp_data.append(temp)

            # Save to CSV if enabled
            self._write_csv_row(ts, f_s, f_r, df, thickness, temp)

        except (ValueError, IndexError):
            pass

    # ── Plot refresh ──────────────────────────────────────────────────────────
    def _update_plots(self):
        t = list(self.t_data)
        if not t:
            return

        fs    = list(self.fs_data)
        fr    = list(self.fr_data)
        df    = list(self.df_data)
        thick = list(self.thick_data)
        temp  = list(self.temp_data)

        self.lbl_fs[1].setText(   f"{fs[-1]:,.3f} Hz"    if fs    else "—")
        self.lbl_fr[1].setText(   f"{fr[-1]:,.3f} Hz"    if fr    else "—")
        self.lbl_df[1].setText(   f"{df[-1]:+.3f} Hz"    if df    else "—")
        self.lbl_thick[1].setText(f"{thick[-1]:.4f} nm"  if thick else "—")
        self.lbl_temp[1].setText( f"{temp[-1]:.2f} °C"   if temp  else "—")

        self.plot_thick[1].setData(t, thick)
        self.plot_df[1].setData(t, df)

    def closeEvent(self, event):
        if self.reader:
            self.reader.stop()
        self._close_csv()
        event.accept()

# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = QCMWindow()
    win.show()
    sys.exit(app.exec_())