"""Tests for the Qt-free GUI logic."""
import os
import pathlib
import subprocess
import shutil
from unittest.mock import patch
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import holo  # noqa: E402
import holo_gui_core as core  # noqa: E402


class Presets(unittest.TestCase):
    @unittest.skipUnless(shutil.which("sh"), "POSIX shell unavailable")
    def test_every_preset_is_valid_shell(self):
        for name, cmd in core.presets_for("linux", ".").items():
            if cmd:
                r = subprocess.run(["sh", "-n", "-c", cmd], capture_output=True)
                self.assertEqual(r.returncode, 0, name)

    def test_preset_lookup(self):
        for name, cmd in core.PRESETS.items():
            self.assertEqual(core.preset_for(cmd), name)
        self.assertEqual(core.preset_for("echo hi"), core.CUSTOM)
        self.assertEqual(core.preset_for(None), "Nothing")

    def test_zone_title(self):
        self.assertEqual(core.zone_title("left_rear"), "Left rear")


class Files(unittest.TestCase):
    def test_actions_roundtrip_drops_empty_and_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "sub" / "actions.json"
            core.save_actions({"right_front": " echo hi ", "left_rear": "  ",
                               "bogus": "rm -rf /"}, p)
            self.assertEqual(core.load_actions(p), {"right_front": "echo hi"})

    def test_missing_or_broken_actions_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(core.load_actions(pathlib.Path(d) / "none.json"), {})
            bad = pathlib.Path(d) / "bad.json"
            bad.write_text("{not json")
            self.assertEqual(core.load_actions(bad), {})

    def test_settings_persist(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "gui.json"
            core.Settings(p).set("device_name", "pipewire")
            self.assertEqual(core.Settings(p).get("device_name"), "pipewire")
            self.assertEqual(core.Settings(p).get("missing", 5), 5)

    @patch.object(core.sys, "platform", "linux")
    def test_autostart_file(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = d
            try:
                self.assertFalse(core.autostart_enabled())
                path = core.set_autostart(True, "/opt/my venv/bin/python", "/home/u/holo_gui.py")
                self.assertTrue(core.autostart_enabled())
                text = path.read_text()
                self.assertIn('Exec="/opt/my venv/bin/python" "/home/u/holo_gui.py" --minimized', text)
                core.set_autostart(False, "x", "y")
                self.assertFalse(core.autostart_enabled())
            finally:
                if old is None:
                    del os.environ["XDG_CONFIG_HOME"]
                else:
                    os.environ["XDG_CONFIG_HOME"] = old

    def test_desktop_entry_escapes_quotes_and_dollars(self):
        self.assertIn('"a\\"b\\$c"', core.desktop_entry('a"b$c', "s"))


class Devices(unittest.TestCase):
    DEVS = [{"name": "HDA Analog", "max_input_channels": 2},
            {"name": "HDMI", "max_input_channels": 0},
            {"name": "pipewire", "max_input_channels": 128},
            {"name": "default", "max_input_channels": 128}]

    def test_filters_inputs(self):
        self.assertEqual(core.input_devices(self.DEVS),
                         [(0, "HDA Analog"), (2, "pipewire"), (3, "default")])

    def test_choice_order(self):
        devs = core.input_devices(self.DEVS)
        self.assertEqual(core.choose_device(devs, "default"), 3)
        self.assertEqual(core.choose_device(devs, None), 2)
        self.assertEqual(core.choose_device(devs, "gone"), 2)
        self.assertIsNone(core.choose_device([(0, "HDA Analog")], None))

    def test_level_percent_bounds(self):
        self.assertEqual(core.level_percent(0.0), 0)
        self.assertEqual(core.level_percent(1.0), 100)
        self.assertTrue(0 < core.level_percent(0.01) < 100)


class Calibration(unittest.TestCase):
    def test_flow(self):
        s = core.CalibrationSession(["a", "b"], 3)
        self.assertEqual(s.add("x")[0], "ignored")          # before Ready
        s.begin_zone()
        self.assertEqual([s.add(i)[0] for i in range(3)],
                         ["accepted", "accepted", "zone_done"])
        self.assertEqual(s.current, "b")
        self.assertEqual(s.add("y")[0], "ignored")          # waiting for Ready again
        s.begin_zone()
        self.assertEqual([s.add(i)[0] for i in range(3)],
                         ["accepted", "accepted", "all_done"])
        X, y = s.dataset()
        self.assertEqual(y, ["a"] * 3 + ["b"] * 3)
        self.assertEqual(len(X), 6)


if __name__ == "__main__":
    unittest.main()
