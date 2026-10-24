#!/usr/bin/env python3
"""Graphical interface for Holo for Linux (Qt / PySide6).

Run:  python holo_gui.py            (open the window)
      python holo_gui.py --minimized  (start in the tray, used for autostart)

It reuses the detector and classifier from holo.py and the same profile and
actions files as the command-line version, so both stay in sync.
"""
import queue
import json
import sys
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget)

import holo
import holo_gui_core as core


class AudioWorker(QThread):
    """Reads the microphone and reports levels and detected tap windows."""

    level = Signal(float)
    detected = Signal(object)
    ready = Signal()
    failed = Signal(str)

    def __init__(self, device, channels):
        super().__init__()
        self.device = device
        self.channels = channels
        self._running = True

    def run(self):
        try:
            ls = holo.Listener(self.device, self.channels)
            ls.stream.start()
        except Exception as e:                      # PortAudio errors vary
            self.failed.emit(str(e))
            return
        announced = False
        count = 0
        try:
            while self._running:
                try:
                    _, block = ls.q.get(timeout=0.2)
                except queue.Empty:
                    continue
                count += 1
                if count % 3 == 0:
                    self.level.emit(float(np.sqrt(np.mean(block ** 2))))
                w = ls.detector.feed(block)
                if not announced and ls.detector.seen > ls.detector.learn_blocks:
                    announced = True
                    self.ready.emit()
                if w is not None:
                    self.detected.emit(w)
        finally:
            ls.stream.stop()
            ls.stream.close()

    def stop(self):
        self._running = False
        self.wait(3000)


class ZoneWidget(QWidget):
    """Top-down picture of the laptop with the four tap zones around it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(380, 280)
        self.active = None
        self.target = None
        self.labels = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._clear)

    def flash(self, zone, text="", ms=800):
        self.active = zone
        self.labels = {zone: text} if text else {}
        self._timer.start(ms)
        self.update()

    def _clear(self):
        self.active = None
        self.labels = {}
        self.update()

    def set_target(self, zone):
        self.target = zone
        self.update()

    def zone_rects(self):
        w, h, g = float(self.width()), float(self.height()), 10.0
        cw, ch = (w - 3 * g) / 2, (h - 3 * g) / 2
        return {
            "left_rear": QRectF(g, g, cw, ch),
            "right_rear": QRectF(2 * g + cw, g, cw, ch),
            "left_front": QRectF(g, 2 * g + ch, cw, ch),
            "right_front": QRectF(2 * g + cw, 2 * g + ch, cw, ch),
        }

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        text_color = self.palette().color(QPalette.ColorRole.WindowText)
        resting = QColor(text_color)
        resting.setAlpha(28)
        outline = QColor(text_color)
        outline.setAlpha(90)
        for zone, rect in self.zone_rects().items():
            if zone == self.active:
                fill = QColor(34, 197, 94, 200)
            elif zone == self.target:
                fill = QColor(59, 130, 246, 110)
            else:
                fill = resting
            p.setBrush(fill)
            if zone == self.target:
                p.setPen(QPen(QColor(59, 130, 246), 3))
            else:
                p.setPen(QPen(outline, 1))
            p.drawRoundedRect(rect, 14, 14)
            label = core.zone_title(zone)
            if zone in self.labels:
                label += "\n" + self.labels[zone]
            p.setPen(text_color)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        w, h = float(self.width()), float(self.height())
        lap = QRectF(w * 0.35, h * 0.39, w * 0.30, h * 0.22)
        p.setBrush(QColor(100, 116, 139, 230))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(lap, 8, 8)
        p.setPen(QColor(255, 255, 255))
        p.drawText(lap, Qt.AlignmentFlag.AlignCenter, "Laptop")
        p.end()


def make_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(34, 197, 94))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(4, 4, 56, 56)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(34)
    p.setFont(font)
    p.setPen(QColor(255, 255, 255))
    p.drawText(QRectF(0, 0, 64, 64), Qt.AlignmentFlag.AlignCenter, "H")
    p.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    def __init__(self, start_hidden=False):
        super().__init__()
        self.setWindowTitle("Holo")
        self.resize(600, 680)
        self.settings = core.Settings()
        self.zone_actions = core.load_actions()
        self.zone_actions_enabled = True
        self.worker = None
        self.worker_channels = 1
        self.model = None
        self.profile_channels = 1
        self.session = None
        self._device = None
        self._devices = {}
        self._quitting = False
        self.tray = None

        self._build_ui()
        self.load_model()
        self.populate_devices()
        self._build_tray()
        if (start_hidden or self.settings.get("listen_on_start", False)) and self.model:
            self.start_worker(self.profile_channels)

    # ---- UI construction ----------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        top = QHBoxLayout()
        top.addWidget(QLabel("Microphone:"))
        self.device_box = QComboBox()
        self.device_box.setMinimumWidth(240)
        self.device_box.currentIndexChanged.connect(self.on_device_changed)
        top.addWidget(self.device_box, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.populate_devices)
        top.addWidget(refresh)
        self.listen_btn = QPushButton("Start listening")
        self.listen_btn.clicked.connect(self.toggle_listening)
        top.addWidget(self.listen_btn)
        root.addLayout(top)

        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setTextVisible(False)
        self.level_bar.setMaximumHeight(10)
        root.addWidget(self.level_bar)

        self.status = QLabel("Not listening.")
        root.addWidget(self.status)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_live_tab(), "Live")
        self.tabs.addTab(self._build_calibrate_tab(), "Calibrate")
        self.tabs.addTab(self._build_actions_tab(), "Actions")
        self.tabs.addTab(self._build_settings_tab(), "Settings")

    def _build_live_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.live_zones = ZoneWidget()
        v.addWidget(self.live_zones, 1)
        self.run_actions = QCheckBox("Run each zone's action when it is tapped")
        self.run_actions.setChecked(True)
        self.run_actions.toggled.connect(self._set_run_actions)
        v.addWidget(self.run_actions)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(300)
        self.log_box.setMaximumHeight(130)
        v.addWidget(self.log_box)
        return w

    def _build_calibrate_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.cal_text = QLabel(
            "Calibration teaches Holo what taps sound like on your desk.\n"
            f"You will tap each of the four zones {holo.CAL_PER_ZONE} times. "
            "Keep the laptop where it will stay, and don't move it afterwards.")
        self.cal_text.setWordWrap(True)
        v.addWidget(self.cal_text)
        self.cal_zones = ZoneWidget()
        v.addWidget(self.cal_zones, 1)
        self.cal_progress = QLabel("")
        v.addWidget(self.cal_progress)
        row = QHBoxLayout()
        self.cal_start = QPushButton("Start calibration")
        self.cal_start.clicked.connect(self.start_calibration)
        self.cal_ready = QPushButton("Ready")
        self.cal_ready.setEnabled(False)
        self.cal_ready.clicked.connect(self.calibration_ready)
        self.cal_cancel = QPushButton("Cancel")
        self.cal_cancel.setEnabled(False)
        self.cal_cancel.clicked.connect(self.cancel_calibration)
        for b in (self.cal_start, self.cal_ready, self.cal_cancel):
            row.addWidget(b)
        v.addLayout(row)
        return w

    def _build_actions_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("What should each zone do? Changes save automatically."))
        grid = QGridLayout()
        v.addLayout(grid)
        names = list(core.PRESETS) + [core.CUSTOM]
        self.action_boxes, self.action_edits = {}, {}
        for row, zone in enumerate(holo.ZONES):
            grid.addWidget(QLabel(core.zone_title(zone)), row * 2, 0)
            box = QComboBox()
            box.addItems(names)
            edit = QLineEdit(self.zone_actions.get(zone, ""))
            box.setCurrentText(core.preset_for(edit.text()))
            test = QPushButton("Test")
            box.activated.connect(lambda _i, z=zone: self._preset_chosen(z))
            edit.editingFinished.connect(lambda z=zone: self._set_action(z, self.action_edits[z].text()))
            test.clicked.connect(lambda _c=False, z=zone: self.test_action(z))
            grid.addWidget(box, row * 2, 1)
            grid.addWidget(test, row * 2, 2)
            grid.addWidget(edit, row * 2 + 1, 1, 1, 2)
            self.action_boxes[zone], self.action_edits[zone] = box, edit
        v.addWidget(QLabel("The box under each zone is the shell command that runs. "
                           "Pick a preset or type your own."))
        v.addStretch(1)
        return w

    def _build_settings_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.autostart_box = QCheckBox("Start Holo (in the tray) when I log in")
        self.autostart_box.setChecked(core.autostart_enabled())
        self.autostart_box.toggled.connect(self._set_autostart)
        v.addWidget(self.autostart_box)
        self.listen_box = QCheckBox("Start listening when the window opens")
        self.listen_box.setChecked(bool(self.settings.get("listen_on_start", False)))
        self.listen_box.toggled.connect(lambda on: self.settings.set("listen_on_start", bool(on)))
        v.addWidget(self.listen_box)
        v.addWidget(QLabel(f"Profile: {holo.PROFILE}"))
        v.addWidget(QLabel(f"Actions: {holo.ACTIONS}"))
        note = QLabel("If you also run the command-line version or the systemd "
                      "service, stop it first: two programs can't share the "
                      "microphone cleanly.")
        note.setWordWrap(True)
        v.addWidget(note)
        v.addStretch(1)
        return w

    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
        show = QAction("Show Holo", self)
        show.triggered.connect(self.show_window)
        toggle = QAction("Start / stop listening", self)
        toggle.triggered.connect(self.toggle_listening)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_app)
        for a in (show, toggle, quit_action):
            menu.addAction(a)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    # ---- small helpers --------------------------------------------------------
    def log(self, msg):
        self.log_box.appendPlainText(f"{time.strftime('%H:%M:%S')}  {msg}")

    def set_status(self, text):
        self.status.setText(text)

    def show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_window()

    def _set_run_actions(self, on):
        self.zone_actions_enabled = bool(on)

    # ---- profile and devices ------------------------------------------------------
    def load_model(self):
        try:
            self.model, self.profile_channels, _ = holo.load_profile()
        except (SystemExit, Exception):            # no or unreadable profile
            self.model, self.profile_channels = None, 1
        if self.model is None:
            self.set_status("No calibration yet. Open the Calibrate tab to begin.")

    def populate_devices(self):
        self.device_box.blockSignals(True)
        self.device_box.clear()
        self.device_box.addItem("System default", None)
        devs = []
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            devs = core.input_devices(devices)
            if sys.platform == "win32":
                backends = sd.query_hostapis()
                devs = [(i, core.device_key(name, backends[devices[i]["hostapi"]]["name"]))
                        for i, name in devs]
        except Exception as e:
            self.log(f"Could not list microphones: {e}")
        for index, name in devs:
            self.device_box.addItem(core.device_label(index, name), index)
        want = core.choose_device(devs, self.settings.get("device_name"))
        self._devices = dict(devs)
        self._device = want
        pos = self.device_box.findData(want) if want is not None else 0
        self.device_box.setCurrentIndex(max(pos, 0))
        self.device_box.blockSignals(False)

    def on_device_changed(self, _index=0):
        data = self.device_box.currentData()
        self._device = data if isinstance(data, int) else None
        self.settings.set("device_name", self._devices.get(self._device))
        if self.worker is not None:
            self.start_worker(self.worker_channels)

    # ---- listening ----------------------------------------------------------------
    def toggle_listening(self):
        if self.worker is not None:
            self.stop_worker()
            self.set_status("Not listening.")
        else:
            self.start_worker(self.profile_channels)

    def start_worker(self, channels):
        self.stop_worker()
        self.worker = AudioWorker(self._device, channels)
        self.worker_channels = channels
        self.worker.level.connect(self.on_level)
        self.worker.detected.connect(self.on_detected)
        self.worker.ready.connect(self.on_ready)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()
        self.listen_btn.setText("Stop listening")
        self.set_status("Learning the room noise... stay quiet for a second.")

    def stop_worker(self):
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.stop()
        self.listen_btn.setText("Start listening")
        self.level_bar.setValue(0)

    def on_ready(self):
        if self.session is not None:
            self.set_status("Listening. Calibrating...")
        elif self.model is None:
            self.set_status("Listening, but there is no calibration yet.")
        else:
            self.set_status("Listening. Tap a zone.")

    def on_failed(self, message):
        self.worker = None
        self.listen_btn.setText("Start listening")
        self.level_bar.setValue(0)
        self.set_status("Could not open the microphone.")
        QMessageBox.warning(self, "Microphone error",
                            f"Could not open the microphone:\n\n{message}\n\n"
                            "Try another device from the list, or check that "
                            "no other program (such as the holo service) is using it.")

    def on_level(self, rms):
        self.level_bar.setValue(core.level_percent(rms))

    def on_detected(self, window):
        if self.worker is None:
            return
        try:
            reason = holo.gate(window)
            if self.session is not None:
                self._calibration_tap(window, reason)
                return
            if reason:
                self.log(f"Ignored: {reason}")
                return
            if self.model is None:
                self.log("Heard a tap, but there is no calibration yet.")
                return
            zone, why, scores = self.model.predict(holo.features(window))
            if zone:
                score = float(scores[self.model.classes.index(zone)])
                self.live_zones.flash(zone, f"{score:.2f}")
                self.log(f"{core.zone_title(zone)}  (score {score:.2f})")
                if self.zone_actions_enabled:
                    holo.run_action(zone)
            else:
                self.log(f"Ignored tap: {why}")
        except Exception as e:                      # never let a slot kill the app
            self.log(f"Error: {e}")

    # ---- calibration ------------------------------------------------------------
    def start_calibration(self):
        if self.worker is None or self.worker_channels != 1:
            self.start_worker(1)
        self.session = core.CalibrationSession(holo.ZONES, holo.CAL_PER_ZONE)
        self.cal_start.setEnabled(False)
        self.cal_cancel.setEnabled(True)
        self._calibration_prompt()

    def _calibration_prompt(self):
        s = self.session
        self.cal_zones.set_target(s.current)
        self.cal_text.setText(
            f"Zone {s.zone_number} of {len(s.zones)}: {core.zone_title(s.current)}\n"
            f"Press Ready, then tap inside the blue area {s.per_zone} times. "
            "Spread your taps across it and pause briefly between taps.")
        self.cal_progress.setText(f"0 / {s.per_zone}")
        self.cal_ready.setEnabled(True)

    def calibration_ready(self):
        if self.session is None:
            return
        self.session.begin_zone()
        self.cal_ready.setEnabled(False)
        self.cal_text.setText(f"Tap the {core.zone_title(self.session.current)} "
                              "area (blue).")

    def _calibration_tap(self, window, reason):
        s = self.session
        if s.waiting:
            return
        if reason:
            self.cal_text.setText(f"That tap was skipped ({reason}). Tap again.")
            return
        status, zone, count = s.add(holo.features(window))
        self.cal_progress.setText(f"{count} / {s.per_zone}")
        self.cal_zones.flash(zone, "", 250)
        if status == "zone_done":
            self._calibration_prompt()
        elif status == "all_done":
            self._finish_calibration()

    def _finish_calibration(self):
        X, y = self.session.dataset()
        self.session = None
        acc, per = holo.leave_one_out(X, y)
        model = holo.Model().fit(X, y)
        holo.save_profile(model, 1, X, y)
        self.model, self.profile_channels = model, 1
        self._reset_calibration_ui()
        self.set_status("Listening. Tap a zone.")
        QMessageBox.information(
            self, "Calibration saved",
            f"Calibration saved. The taps agree with each other {acc:.0%} of the "
            "time, but that number is optimistic because they were all recorded "
            "in one session.\n\nThe real test is the Live tab: tap each zone and "
            "see which one lights up.")
        self.tabs.setCurrentIndex(0)

    def cancel_calibration(self):
        self.session = None
        self._reset_calibration_ui()

    def _reset_calibration_ui(self):
        self.cal_zones.set_target(None)
        self.cal_start.setEnabled(True)
        self.cal_ready.setEnabled(False)
        self.cal_cancel.setEnabled(False)
        self.cal_progress.setText("")
        self.cal_text.setText("Calibration teaches Holo what taps sound like on "
                              "your desk. Press Start calibration to redo it.")

    # ---- actions ------------------------------------------------------------------
    def _preset_chosen(self, zone):
        name = self.action_boxes[zone].currentText()
        if name == core.CUSTOM:
            return
        text = core.PRESETS[name]
        self.action_edits[zone].setText(text)
        self._set_action(zone, text)

    def _set_action(self, zone, text):
        self.zone_actions[zone] = text
        core.save_actions(self.zone_actions)
        if zone in self.action_boxes:
            self.action_boxes[zone].setCurrentText(core.preset_for(text))

    def test_action(self, zone):
        command = self.zone_actions.get(zone, "").strip()
        if not command:
            self.log(f"{core.zone_title(zone)} has no action to test.")
            return
        holo.spawn(command)
        self.log(f"Ran the {core.zone_title(zone)} action.")

    # ---- settings -------------------------------------------------------------------
    def _set_autostart(self, enabled):
        script = str(Path(__file__).resolve())
        core.set_autostart(bool(enabled), sys.executable, script)

    # ---- shutdown ------------------------------------------------------------------
    def closeEvent(self, event):
        if self.tray is not None and not self._quitting:
            event.ignore()
            self.hide()
        else:
            self.shutdown()
            event.accept()

    def shutdown(self):
        self.stop_worker()
        if self.tray is not None:
            self.tray.hide()

    def quit_app(self):
        self._quitting = True
        self.shutdown()
        QApplication.quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Holo")
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test-file":
        # Packaging check: load the bundled audio DLL and construct real widgets.
        # This does not open an input stream or claim hardware compatibility.
        import sounddevice
        scripts = core._PRESET_BASE / "windows"
        for name in ("holo-shot.ps1", "holo-notify.ps1"):
            if not (scripts / name).is_file():
                raise RuntimeError(f"Missing bundled script: {name}")
        win = MainWindow()
        win.shutdown()
        Path(sys.argv[2]).write_text(json.dumps({"ok": True, "audio_import": True,
                                              "gui_constructed": True}))
        return
    hidden = "--minimized" in sys.argv
    win = MainWindow(start_hidden=hidden)
    app.setQuitOnLastWindowClosed(win.tray is None)
    app.aboutToQuit.connect(win.shutdown)
    if not (hidden and win.tray is not None):
        win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
