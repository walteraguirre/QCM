# qcm_gui.py  —  PC
# Interfaz gráfica PyQt5 para controlar la QCM y mostrar mediciones en tiempo real.
#
# Instalación:
#   pip install PyQt5 pyserial pyqtgraph
#
# Uso:
#   python qcm_gui.py

import sys
import threading
import serial
import serial.tools.list_ports
from collections import deque

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QGroupBox, QStatusBar, QSizePolicy
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QFont, QColor
import pyqtgraph as pg

# ─── Configuración ────────────────────────────────────────────────────────────
BAUD_RATE  = 115200
MAX_POINTS = 120   # últimos 120 segundos visibles

# ─── Señales para pasar datos del hilo serial al hilo de la GUI ───────────────
class SerialSignals(QObject):
    data_received = pyqtSignal(str)   # línea CSV completa
    status_msg    = pyqtSignal(str)   # mensajes de estado/debug
    connected     = pyqtSignal(bool)  # conexión establecida / perdida

# ─── Hilo lector serial ───────────────────────────────────────────────────────
class SerialReader(threading.Thread):
    def __init__(self, port, signals):
        super().__init__(daemon=True)
        self.port    = port
        self.signals = signals
        self.ser     = None
        self._stop   = threading.Event()

    def send(self, cmd):
        """Manda un comando a la Pico."""
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
            self.signals.status_msg.emit(f"Conectado a {self.port}")
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
                self.signals.status_msg.emit("Conexión perdida.")
                self.signals.connected.emit(False)
                break

# ─── Ventana principal ────────────────────────────────────────────────────────
class QCMWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QCM — Monitor de frecuencia")
        self.setMinimumSize(900, 650)

        self.reader  = None
        self.signals = SerialSignals()
        self.dual    = False

        # Buffers de datos
        self.t_data    = deque(maxlen=MAX_POINTS)
        self.fs_data   = deque(maxlen=MAX_POINTS)
        self.fr_data   = deque(maxlen=MAX_POINTS)
        self.df_data   = deque(maxlen=MAX_POINTS)
        self.temp_data = deque(maxlen=MAX_POINTS)

        self._build_ui()
        self._connect_signals()
        self._refresh_ports()

        # Timer para refrescar los gráficos
        self.plot_timer = QTimer()
        self.plot_timer.setInterval(1000)
        self.plot_timer.timeout.connect(self._update_plots)

    # ── Construcción de la UI ─────────────────────────────────────────────────
    def _build_ui(self):
        pg.setConfigOption("background", "#1e1e2e")
        pg.setConfigOption("foreground", "#cdd6f4")

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(10)
        root.setContentsMargins(12, 12, 12, 12)

        # ── Panel de control ──────────────────────────────────────────────────
        ctrl_box = QGroupBox("Control")
        ctrl_box.setStyleSheet("QGroupBox { font-weight: bold; }")
        ctrl_layout = QHBoxLayout(ctrl_box)

        # Puerto serie
        ctrl_layout.addWidget(QLabel("Puerto:"))
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(140)
        ctrl_layout.addWidget(self.port_combo)

        self.btn_refresh = QPushButton("↻")
        self.btn_refresh.setFixedWidth(32)
        self.btn_refresh.setToolTip("Refrescar puertos")
        self.btn_refresh.clicked.connect(self._refresh_ports)
        ctrl_layout.addWidget(self.btn_refresh)

        self.btn_connect = QPushButton("Conectar")
        self.btn_connect.clicked.connect(self._toggle_connect)
        ctrl_layout.addWidget(self.btn_connect)

        ctrl_layout.addSpacing(20)

        # Modo de medición
        ctrl_layout.addWidget(QLabel("Modo:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["1 cristal  (solo sensor)", "2 cristales (sensor + referencia)"])
        self.mode_combo.setMinimumWidth(220)
        ctrl_layout.addWidget(self.mode_combo)

        ctrl_layout.addSpacing(20)

        # Botón inicio/stop
        self.btn_start = QPushButton("▶  Iniciar medición")
        self.btn_start.setEnabled(False)
        self.btn_start.setFixedHeight(36)
        self.btn_start.setStyleSheet("""
            QPushButton { background:#40a02b; color:white; border-radius:6px; font-weight:bold; font-size:13px; }
            QPushButton:hover { background:#5dbc3f; }
            QPushButton:disabled { background:#45475a; color:#6c7086; }
        """)
        self.btn_start.clicked.connect(self._toggle_measurement)
        ctrl_layout.addWidget(self.btn_start)

        ctrl_layout.addStretch()
        root.addWidget(ctrl_box)

        # ── Indicadores numéricos ─────────────────────────────────────────────
        num_box = QGroupBox("Valores actuales")
        num_layout = QHBoxLayout(num_box)

        self.lbl_fs   = self._make_value_label("f sensor",      "—", "#89b4fa")
        self.lbl_fr   = self._make_value_label("f referencia",  "—", "#a6e3a1")
        self.lbl_df   = self._make_value_label("Δf",            "—", "#f38ba8")
        self.lbl_temp = self._make_value_label("Temperatura",   "—", "#fab387")

        num_layout.addWidget(self.lbl_fs[0])
        num_layout.addWidget(self.lbl_fr[0])
        num_layout.addWidget(self.lbl_df[0])
        num_layout.addWidget(self.lbl_temp[0])
        root.addWidget(num_box)

        # ── Gráficos ──────────────────────────────────────────────────────────
        self.plot_fs = self._make_plot("f sensor (Hz)",          "#89b4fa")
        self.plot_fr = self._make_plot("f referencia (Hz)",      "#a6e3a1")
        self.plot_df = self._make_plot("Δf = f_sensor − f_ref (Hz)", "#f38ba8", zero_line=True)

        root.addWidget(self.plot_fs[0])
        root.addWidget(self.plot_fr[0])
        root.addWidget(self.plot_df[0])

        # ── Barra de estado ───────────────────────────────────────────────────
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Sin conectar.")

    def _make_value_label(self, title, value, color):
        """Crea un widget con título + valor grande."""
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 4, 8, 4)

        lbl_title = QLabel(title)
        lbl_title.setAlignment(Qt.AlignCenter)
        lbl_title.setStyleSheet(f"color: {color}; font-size: 11px;")

        lbl_val = QLabel(value)
        lbl_val.setAlignment(Qt.AlignCenter)
        lbl_val.setFont(QFont("Courier New", 16, QFont.Bold))
        lbl_val.setStyleSheet(f"color: {color};")

        lay.addWidget(lbl_title)
        lay.addWidget(lbl_val)
        return box, lbl_val

    def _make_plot(self, title, color, zero_line=False):
        """Crea un PlotWidget con estilo oscuro."""
        pw = pg.PlotWidget(title=title)
        pw.setMaximumHeight(180)
        pw.showGrid(x=True, y=True, alpha=0.2)
        pw.setLabel("bottom", "tiempo (s)")
        pw.setLabel("left", "Hz")
        curve = pw.plot(pen=pg.mkPen(color=color, width=2))
        if zero_line:
            pw.addLine(y=0, pen=pg.mkPen(color="#6c7086", style=Qt.DashLine))
        return pw, curve

    # ── Señales ───────────────────────────────────────────────────────────────
    def _connect_signals(self):
        self.signals.data_received.connect(self._on_data)
        self.signals.status_msg.connect(lambda m: self.status_bar.showMessage(m))
        self.signals.connected.connect(self._on_connection_change)

    # ── Puertos serie ─────────────────────────────────────────────────────────
    def _refresh_ports(self):
        self.port_combo.clear()
        ports = serial.tools.list_ports.comports()
        pico_port = None
        for p in ports:
            self.port_combo.addItem(f"{p.device}  —  {p.description}", p.device)
            if p.vid == 0x2E8A:   # VID de Raspberry Pi
                pico_port = p.device
        if pico_port:
            # Selecciona la Pico automáticamente si la detecta
            idx = self.port_combo.findData(pico_port)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
            self.status_bar.showMessage(f"Pico detectada en {pico_port}")
        elif ports:
            self.status_bar.showMessage("Pico no detectada — seleccioná el puerto manualmente.")
        else:
            self.status_bar.showMessage("No se encontraron puertos serie.")

    # ── Conexión serial ───────────────────────────────────────────────────────
    def _toggle_connect(self):
        if self.reader and self.reader.is_alive():
            self.reader.stop()
            self.reader = None
            self.btn_connect.setText("Conectar")
            self.btn_start.setEnabled(False)
        else:
            port = self.port_combo.currentData()
            if not port:
                self.status_bar.showMessage("Seleccioná un puerto primero.")
                return
            self.reader = SerialReader(port, self.signals)
            self.reader.start()

    def _on_connection_change(self, ok):
        if ok:
            self.btn_connect.setText("Desconectar")
            self.btn_start.setEnabled(True)
        else:
            self.btn_connect.setText("Conectar")
            self.btn_start.setEnabled(False)
            self._set_measuring(False)

    # ── Inicio / stop de medición ─────────────────────────────────────────────
    def _toggle_measurement(self):
        if self.btn_start.text().startswith("▶"):
            # Limpiar datos anteriores
            self.t_data.clear(); self.fs_data.clear()
            self.fr_data.clear(); self.df_data.clear()

            self.dual = (self.mode_combo.currentIndex() == 1)
            cmd = "START_2" if self.dual else "START_1"
            self.reader.send(cmd)
            self._set_measuring(True)
        else:
            self.reader.send("STOP")
            self._set_measuring(False)

    def _set_measuring(self, measuring):
        if measuring:
            self.btn_start.setText("⏹  Detener medición")
            self.btn_start.setStyleSheet("""
                QPushButton { background:#d20f39; color:white; border-radius:6px; font-weight:bold; font-size:13px; }
                QPushButton:hover { background:#e8305a; }
            """)
            self.mode_combo.setEnabled(False)
            self.plot_timer.start()
        else:
            self.btn_start.setText("▶  Iniciar medición")
            self.btn_start.setStyleSheet("""
                QPushButton { background:#40a02b; color:white; border-radius:6px; font-weight:bold; font-size:13px; }
                QPushButton:hover { background:#5dbc3f; }
                QPushButton:disabled { background:#45475a; color:#6c7086; }
            """)
            self.mode_combo.setEnabled(True)
            self.plot_timer.stop()

    # ── Datos entrantes ───────────────────────────────────────────────────────
    def _on_data(self, line):
        parts = line.split(",")
        try:
            if not self.dual and len(parts) == 3:
                # timestamp_s, f_sensor, temp_c
                ts, fs, temp = float(parts[0]), float(parts[1]), float(parts[2])
                self.t_data.append(ts)
                self.fs_data.append(fs)
                self.temp_data.append(temp)

            elif self.dual and len(parts) == 5:
                # timestamp_s, f_sensor, f_ref, delta_f, temp_c
                ts, fs, fr, df, temp = (float(x) for x in parts)
                self.t_data.append(ts)
                self.fs_data.append(fs)
                self.fr_data.append(fr)
                self.df_data.append(df)
                self.temp_data.append(temp)
        except ValueError:
            pass

    # ── Refresco de gráficos ──────────────────────────────────────────────────
    def _update_plots(self):
        t = list(self.t_data)
        if not t:
            return

        fs = list(self.fs_data)
        self.plot_fs[1].setData(t, fs)
        self.lbl_fs[1].setText(f"{fs[-1]:,.3f} Hz" if fs else "—")

        if self.dual:
            fr = list(self.fr_data)
            df = list(self.df_data)
            self.plot_fr[1].setData(t, fr)
            self.plot_df[1].setData(t, df)
            self.lbl_fr[1].setText(f"{fr[-1]:,.3f} Hz" if fr else "—")
            self.lbl_df[1].setText(f"{df[-1]:+.3f} Hz" if df else "—")
        else:
            self.plot_fr[1].setData([], [])
            self.plot_df[1].setData([], [])
            self.lbl_fr[1].setText("—")
            self.lbl_df[1].setText("—")

        # Temperatura — siempre visible independientemente del modo
        temp = list(self.temp_data)
        self.lbl_temp[1].setText(f"{temp[-1]:.2f} °C" if temp else "—")

    def closeEvent(self, event):
        if self.reader:
            self.reader.stop()
        event.accept()

# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = QCMWindow()
    win.show()
    sys.exit(app.exec_())
