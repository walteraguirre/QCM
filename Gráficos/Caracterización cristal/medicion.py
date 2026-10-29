import sys
import csv
from collections import deque
from pathlib import Path

import serial
from serial.tools import list_ports
from PyQt5 import QtCore, QtWidgets
import pyqtgraph as pg


class SerialWorker(QtCore.QThread):
    sample = QtCore.pyqtSignal(int, int)
    error = QtCore.pyqtSignal(str)

    def __init__(self, port, baud=115200):
        super().__init__()
        self.port = port
        self.baud = baud
        self.running = True
        self.ser = None

    def run(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.2)
            while self.running:
                raw = self.ser.readline().decode('ascii', errors='ignore').strip()
                if not raw or raw.startswith('tiempo_us'):
                    continue
                try:
                    t, adc = raw.split(',')
                    t, adc = int(t), int(adc)
                    if 0 <= adc <= 1023:
                        self.sample.emit(t, adc)
                except (ValueError, TypeError):
                    continue
        except serial.SerialException as exc:
            self.error.emit(str(exc))
        finally:
            if self.ser and self.ser.is_open:
                self.ser.close()

    def stop(self):
        self.running = False
        self.wait(1500)


class Monitor(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Monitor de señal | Arduino A0')
        self.resize(1050, 690)
        self.worker = None
        self.file = None
        self.writer = None
        self.times = deque()
        self.values = deque()
        self.total = 0
        self.minimum = None
        self.maximum = None
        self.latest = None
        self.origin_us = None

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)

        controls = QtWidgets.QHBoxLayout()
        self.port = QtWidgets.QComboBox()
        self.refresh_btn = QtWidgets.QPushButton('Actualizar puertos')
        self.refresh_btn.clicked.connect(self.refresh_ports)
        self.connect_btn = QtWidgets.QPushButton('Conectar')
        self.connect_btn.clicked.connect(self.toggle_connection)
        controls.addWidget(QtWidgets.QLabel('Puerto:'))
        controls.addWidget(self.port)
        controls.addWidget(self.refresh_btn)
        controls.addWidget(self.connect_btn)
        controls.addStretch()
        controls.addWidget(QtWidgets.QLabel('Ventana:'))
        self.window = QtWidgets.QComboBox()
        self.window.addItems(['5 s', '10 s', '30 s', '60 s'])
        self.window.setCurrentText('10 s')
        controls.addWidget(self.window)
        layout.addLayout(controls)

        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget(background='#101827')
        self.plot.setLabel('bottom', 'Tiempo desde primera muestra', units='s')
        self.plot.setLabel('left', 'ADC (crudo)')
        self.plot.setYRange(0, 1023, padding=0)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.showGrid(x=True, y=True, alpha=0.22)
        self.curve = self.plot.plot(pen=pg.mkPen('#42d6b3', width=1.6))
        layout.addWidget(self.plot, 1)

        stats = QtWidgets.QHBoxLayout()
        self.current_label = QtWidgets.QLabel('Actual: —')
        self.min_label = QtWidgets.QLabel('Mín: —')
        self.max_label = QtWidgets.QLabel('Máx: —')
        self.count_label = QtWidgets.QLabel('Muestras: 0')
        for w in (self.current_label, self.min_label, self.max_label, self.count_label):
            stats.addWidget(w)
        layout.addLayout(stats)

        recording = QtWidgets.QHBoxLayout()
        recording.addWidget(QtWidgets.QLabel('Nombre del archivo:'))
        self.filename = QtWidgets.QLineEdit('senal_planta.csv')
        recording.addWidget(self.filename, 1)
        self.start_btn = QtWidgets.QPushButton('Iniciar grabación')
        self.stop_btn = QtWidgets.QPushButton('Detener grabación')
        self.start_btn.clicked.connect(self.start_recording)
        self.stop_btn.clicked.connect(self.stop_recording)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        recording.addWidget(self.start_btn)
        recording.addWidget(self.stop_btn)
        layout.addLayout(recording)

        self.status = QtWidgets.QLabel('Desconectado')
        layout.addWidget(self.status)
        self.refresh_ports()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.update_plot)
        self.timer.start(40)  # ~25 actualizaciones de gráfico por segundo

    def refresh_ports(self):
        previous = self.port.currentText()
        self.port.clear()
        self.port.addItems([p.device for p in list_ports.comports()])
        if previous:
            idx = self.port.findText(previous)
            if idx >= 0:
                self.port.setCurrentIndex(idx)
        idx = self.port.findText('COM29')
        if idx >= 0 and not previous:
            self.port.setCurrentIndex(idx)

    def toggle_connection(self):
        if self.worker is not None:
            self.disconnect_serial()
            return
        port = self.port.currentText()
        if not port:
            QtWidgets.QMessageBox.warning(self, 'Puerto', 'Seleccioná un puerto serie.')
            return
        self.times.clear()
        self.values.clear()
        self.total = 0
        self.minimum = self.maximum = self.latest = self.origin_us = None
        self.worker = SerialWorker(port)
        self.worker.sample.connect(self.on_sample)
        self.worker.error.connect(self.on_error)
        self.worker.start()
        self.connect_btn.setText('Desconectar')
        self.start_btn.setEnabled(True)
        self.status.setText(f'Conectando a {port}... Esperando muestras')

    def on_error(self, message):
        self.status.setText('Error: ' + message)
        QtWidgets.QMessageBox.warning(self, 'Puerto serie', message)
        self.disconnect_serial()

    def disconnect_serial(self):
        self.stop_recording()
        worker = self.worker
        self.worker = None
        if worker:
            worker.stop()
            worker.deleteLater()
        self.connect_btn.setText('Conectar')
        self.start_btn.setEnabled(False)
        self.status.setText('Desconectado')

    def on_sample(self, t_us, adc):
        if self.origin_us is None:
            self.origin_us = t_us
        t_s = (t_us - self.origin_us) / 1_000_000.0
        self.times.append(t_s)
        self.values.append(adc)
        self.latest = adc
        self.total += 1
        self.minimum = adc if self.minimum is None else min(self.minimum, adc)
        self.maximum = adc if self.maximum is None else max(self.maximum, adc)
        if self.writer is not None:
            self.writer.writerow([t_us, adc])
            if self.total % 100 == 0:
                self.file.flush()
        window_seconds = int(self.window.currentText().split()[0])
        while self.times and t_s - self.times[0] > window_seconds:
            self.times.popleft()
            self.values.popleft()

    def update_plot(self):
        if not self.times:
            return
        self.curve.setData(list(self.times), list(self.values))
        window_seconds = int(self.window.currentText().split()[0])
        right = self.times[-1]
        self.plot.setXRange(max(0, right - window_seconds), max(window_seconds, right), padding=0)
        self.current_label.setText(f'Actual: {self.latest}')
        self.min_label.setText(f'Mín: {self.minimum}')
        self.max_label.setText(f'Máx: {self.maximum}')
        self.count_label.setText(f'Muestras: {self.total}')
        if self.worker and self.writer is None:
            self.status.setText('Recibiendo señal (sin grabar)')

    def start_recording(self):
        if self.worker is None or self.writer is not None:
            return
        name = self.filename.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, 'Nombre', 'Escribí un nombre para el CSV.')
            return
        path = Path(name).expanduser()
        if path.suffix.lower() != '.csv':
            path = path.with_suffix('.csv')
        path = path.resolve()
        if path.exists():
            answer = QtWidgets.QMessageBox.question(self, 'Archivo existente', f'{path.name} ya existe. ¿Sobrescribir?')
            if answer != QtWidgets.QMessageBox.Yes:
                return
        try:
            self.file = open(path, 'w', newline='', encoding='utf-8')
            self.writer = csv.writer(self.file)
            self.writer.writerow(['tiempo_us', 'adc'])
            self.file.flush()
        except OSError as exc:
            self.writer = None
            self.file = None
            QtWidgets.QMessageBox.critical(self, 'Error al guardar', str(exc))
            return
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.filename.setEnabled(False)
        self.status.setText(f'Grabando: {path}')

    def stop_recording(self):
        if self.file is not None:
            self.file.flush()
            self.file.close()
        self.file = None
        self.writer = None
        self.stop_btn.setEnabled(False)
        self.filename.setEnabled(True)
        self.start_btn.setEnabled(self.worker is not None)

    def closeEvent(self, event):
        self.disconnect_serial()
        event.accept()


if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle('Fusion')
    monitor = Monitor()
    monitor.show()
    sys.exit(app.exec_())
