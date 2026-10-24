"""Smoke test for holo_gui.py: drives MainWindow through a calibration and a
live tap without a microphone.

If PySide6 is installed this uses the real Qt (offscreen). If not, it uses a
stand-in for the Qt classes. The stand-in only catches mistakes in Holo's own
code (typos, wrong variable names, logic errors), NOT mistakes in how Qt is
used or how the window looks. Only a real run can show those.
"""
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import holo  # noqa: E402
import holo_gui_core as core  # noqa: E402
import test_synthetic as T  # noqa: E402

try:
    import PySide6  # noqa: F401
    REAL_QT = True
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
except ImportError:
    REAL_QT = False


def _install_fake_qt():
    class _Any:
        def __init__(self, *a, **k):
            pass

        def __call__(self, *a, **k):
            return _Any()

        def __getattr__(self, name):
            return _Any()

        def __or__(self, other):
            return self

    class _Meta(type):
        def __getattr__(cls, name):
            return _Any()

    class _Bound:
        def __init__(self):
            self.slots = []

        def connect(self, fn):
            self.slots.append(fn)

        def emit(self, *a):
            for fn in self.slots:
                fn(*a)

    class Signal:
        def __init__(self, *a):
            pass

        def __set_name__(self, owner, name):
            self.name = "_sig_" + name

        def __get__(self, obj, owner=None):
            if obj is None:
                return self
            return obj.__dict__.setdefault(self.name, _Bound())

    class _Base(metaclass=_Meta):
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            return _Any()

        def sender(self):
            return None

    def cls(name, **attrs):
        return type(name, (_Base,), attrs)

    core_mod = types.ModuleType("PySide6.QtCore")
    core_mod.Signal = Signal
    core_mod.Slot = lambda *a: lambda fn: fn
    for n in ("QRectF", "Qt", "QThread", "QTimer"):
        setattr(core_mod, n, cls(n))
    gui_mod = types.ModuleType("PySide6.QtGui")
    for n in ("QAction", "QColor", "QFont", "QIcon", "QPainter", "QPalette", "QPen", "QPixmap"):
        setattr(gui_mod, n, cls(n))
    w_mod = types.ModuleType("PySide6.QtWidgets")
    for n in ("QApplication", "QCheckBox", "QComboBox", "QGridLayout", "QHBoxLayout",
              "QLabel", "QLineEdit", "QMainWindow", "QMenu", "QMessageBox",
              "QPlainTextEdit", "QProgressBar", "QPushButton", "QTabWidget",
              "QVBoxLayout", "QWidget"):
        setattr(w_mod, n, cls(n))
    w_mod.QSystemTrayIcon = cls("QSystemTrayIcon",
                                isSystemTrayAvailable=staticmethod(lambda: False))
    pkg = types.ModuleType("PySide6")
    pkg.QtCore, pkg.QtGui, pkg.QtWidgets = core_mod, gui_mod, w_mod
    sys.modules.update({"PySide6": pkg, "PySide6.QtCore": core_mod,
                        "PySide6.QtGui": gui_mod, "PySide6.QtWidgets": w_mod})


class _DummyWorker:
    def stop(self):
        pass


class GuiFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env_patch = patch.dict(os.environ, {"APPDATA": cls.tmp.name})
        cls.env_patch.start()
        t = pathlib.Path(cls.tmp.name)
        cls.saved = (holo.DATA_DIR, holo.CONF_DIR, holo.PROFILE, holo.ACTIONS,
                     os.environ.get("XDG_CONFIG_HOME"))
        holo.DATA_DIR, holo.CONF_DIR = t / "data", t / "conf"
        holo.PROFILE, holo.ACTIONS = holo.DATA_DIR / "profile.json", holo.CONF_DIR / "actions.json"
        os.environ["XDG_CONFIG_HOME"] = str(t / "xdg")
        if REAL_QT:
            from PySide6.QtWidgets import QApplication
            cls.app = QApplication.instance() or QApplication([])
        else:
            _install_fake_qt()
        import holo_gui
        cls.gui = holo_gui
        quiet = types.SimpleNamespace(information=lambda *a, **k: None,
                                      warning=lambda *a, **k: None)
        holo_gui.QMessageBox = quiet       # a real modal dialog would hang the test
        devices = types.SimpleNamespace(query_devices=lambda: [], query_hostapis=lambda: [])
        with patch.dict(sys.modules, {"sounddevice": devices}):
            cls.win = holo_gui.MainWindow()
        cls.win.worker, cls.win.worker_channels = _DummyWorker(), 1

    @classmethod
    def tearDownClass(cls):
        (holo.DATA_DIR, holo.CONF_DIR, holo.PROFILE, holo.ACTIONS, xdg) = cls.saved
        if xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = xdg
        cls.env_patch.stop()
        cls.tmp.cleanup()

    def test_1_taps_before_calibration_do_not_crash(self):
        self.win.on_detected(T.one_window(T.resonant_tap("left_rear")))
        self.assertIsNone(self.win.model)

    def test_2_calibration_saves_a_profile(self):
        w = self.win
        w.start_calibration()
        self.assertIsNotNone(w.session)
        # taps before pressing Ready are ignored
        w.on_detected(T.one_window(T.resonant_tap(holo.ZONES[0])))
        self.assertEqual(len(w.session.samples[holo.ZONES[0]]), 0)
        for z in holo.ZONES:
            w.calibration_ready()
            for _ in range(holo.CAL_PER_ZONE):
                w.on_detected(T.one_window(T.resonant_tap(z)))
        self.assertIsNone(w.session)
        self.assertIsNotNone(w.model)
        self.assertTrue(holo.PROFILE.exists())

    def test_3_live_tap_runs_the_zone_action(self):
        w = self.win
        w._set_action("right_front", "holo-test-action")
        with patch.object(holo, "spawn") as spawn:
            for _ in range(6):
                holo._last_fired.clear()
                w.on_detected(T.one_window(T.resonant_tap("right_front")))
                if spawn.called:
                    break
            spawn.assert_called_with("holo-test-action")

    def test_4_speech_like_sound_is_ignored(self):
        w = self.win
        w._set_action("left_rear", "holo-test-action")
        t = np.arange(holo.WIN) / holo.SR
        tone = (0.2 * np.sin(2 * np.pi * 400 * t)).astype("float32")
        self.assertIsNotNone(holo.gate(tone))
        holo._last_fired.clear()
        with patch.object(holo, "spawn") as spawn:
            w.on_detected(tone)
            spawn.assert_not_called()

    def test_5_actions_persist_and_autostart_toggles(self):
        w = self.win
        w._set_action("left_front", "echo hi")
        self.assertEqual(core.load_actions()["left_front"], "echo hi")
        w._set_autostart(True)
        self.assertTrue(core.autostart_enabled())
        w._set_autostart(False)
        self.assertFalse(core.autostart_enabled())

    def test_6_microphone_failure_is_handled(self):
        w = self.win
        w.worker = _DummyWorker()
        w.on_failed("boom")
        self.assertIsNone(w.worker)
        w.worker, w.worker_channels = _DummyWorker(), 1

    def test_7_test_button_uses_shared_spawn(self):
        self.win._set_action("right_front", "echo test")
        with patch.object(holo, "spawn") as spawn:
            self.win.test_action("right_front")
            spawn.assert_called_once_with("echo test")

    def test_8_windows_devices_keep_backend_and_selection(self):
        class Box:
            def __init__(self):
                self.items = []

            def blockSignals(self, value):
                pass

            def clear(self):
                self.items.clear()

            def addItem(self, label, data):
                self.items.append((label, data))

            def findData(self, data):
                return next(i for i, (_, value) in enumerate(self.items) if value == data)

            def setCurrentIndex(self, index):
                self.index = index

        devices = [{"name": "Mic", "hostapi": i, "max_input_channels": 1}
                   for i in range(2)]
        sd = types.SimpleNamespace(query_devices=lambda: devices,
                                   query_hostapis=lambda: [{"name": "MME"}, {"name": "WASAPI"}])
        box = Box()
        window = types.SimpleNamespace(device_box=box, settings=core.Settings(), log=lambda s: None)
        window.settings.set("device_name", core.device_key("Mic", "WASAPI"))
        with patch.object(sys, "platform", "win32"), patch.dict(sys.modules, {"sounddevice": sd}):
            self.gui.MainWindow.populate_devices(window)
        self.assertEqual(window._device, 1)
        self.assertEqual(window._devices[1], "Mic [WASAPI]")
        self.assertEqual(box.items[1], ("Mic [MME]  (#0)", 0))
        self.assertEqual(box.index, 2)

    def test_9_sensitive_setting_persists_and_restarts_capture(self):
        with patch.object(self.win, 'start_worker') as start:
            self.win._set_sensitivity('Sensitive')
            self.assertEqual(core.Settings().get('sensitivity'), 'Sensitive')
            start.assert_called_once_with(1)

    def test_audio_failure_closes_stream(self):
        listener = Mock()
        listener.read_block.side_effect = RuntimeError('disconnected')
        worker = self.gui.AudioWorker(None, 1, 1.5)
        failures = []
        worker.failed.connect(failures.append)
        with patch.object(holo, 'Listener', return_value=listener) as create:
            worker.run()
        create.assert_called_once_with(None, 1, 1.5)
        self.assertEqual(failures, ['disconnected'])
        listener.stream.close.assert_called_once()

    def test_audio_start_failure_closes_stream(self):
        listener = Mock()
        listener.stream.start.side_effect = RuntimeError('busy')
        worker = self.gui.AudioWorker(None, 1)
        failures = []
        worker.failed.connect(failures.append)
        with patch.object(holo, 'Listener', return_value=listener):
            worker.run()
        self.assertEqual(failures, ['busy'])
        listener.stream.close.assert_called_once()

    def test_audio_without_callbacks_reports_failure(self):
        import queue
        listener = Mock()
        listener.read_block.side_effect = queue.Empty
        worker = self.gui.AudioWorker(None, 1)
        failures = []
        worker.failed.connect(failures.append)
        with patch.object(holo, 'Listener', return_value=listener), patch.object(self.gui.time, 'monotonic', side_effect=[0, 0, 4]):
            worker.run()
        self.assertIn('No audio received', failures[0])
        listener.stream.close.assert_called_once()

    def test_slow_stopping_thread_is_retained_and_not_replaced(self):
        stuck = types.SimpleNamespace(stop=lambda: False)
        with patch.object(self.win, 'worker', stuck), patch.object(self.gui, 'AudioWorker') as create:
            self.assertFalse(self.win.start_worker(1))
            self.assertIs(self.win.worker, stuck)
            create.assert_not_called()

    def test_stale_tap_cannot_be_added_to_calibration(self):
        session = core.CalibrationSession(holo.ZONES, 3)
        session.begin_zone()
        window = T.one_window(T.resonant_tap('left_rear'))
        with patch.object(self.win, 'session', session), patch.object(self.gui.time, 'monotonic', return_value=10):
            self.win.on_detected(window, captured_at=8)
        self.assertEqual(session.samples['left_rear'], [])

    @unittest.skipUnless(REAL_QT, 'Requires real Qt sender identity')
    def test_signal_from_an_old_worker_is_ignored(self):
        old_worker = self.gui.AudioWorker(None, 1)
        old_worker.detected.connect(self.win.on_detected)
        window = T.one_window(T.resonant_tap('right_front'))
        with patch.object(self.win.model, 'predict') as predict:
            old_worker.detected.emit(window, self.gui.time.monotonic())
            predict.assert_not_called()


if __name__ == "__main__":
    unittest.main()
