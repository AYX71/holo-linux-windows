"""Platform branches are simulated on Linux; these do not prove Windows works."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import holo
import holo_gui_core as core

ROOT = Path(__file__).resolve().parent.parent


class PlatformTests(unittest.TestCase):
    def test_linux_dirs_and_empty_values(self):
        home = Path('/example')
        self.assertEqual(holo.app_dirs('linux', {}, home),
                         (home / '.local/share/holo', home / '.config/holo'))
        self.assertEqual(holo.app_dirs('linux', {'XDG_DATA_HOME': '', 'XDG_CONFIG_HOME': ''}, home),
                         holo.app_dirs('linux', {}, home))
        self.assertEqual(holo.app_dirs('linux', {'XDG_DATA_HOME': '/data', 'XDG_CONFIG_HOME': '/conf'}, home),
                         (Path('/data/holo'), Path('/conf/holo')))

    def test_windows_dirs(self):
        home = Path('/example')
        self.assertEqual(holo.app_dirs('win32', {'LOCALAPPDATA': '/local', 'APPDATA': '/roam'}, home),
                         (Path('/local/holo'), Path('/roam/holo')))
        self.assertEqual(holo.app_dirs('win32', {'LOCALAPPDATA': '', 'APPDATA': ''}, home),
                         (home / 'AppData/Local/holo', home / 'AppData/Roaming/holo'))

    def test_spawn_platform_flags(self):
        for platform in ('win32', 'linux'):
            with self.subTest(platform=platform), patch.object(holo.sys, 'platform', platform), patch.object(holo.subprocess, 'Popen') as popen:
                result = holo.spawn('echo hi')
                options = {'creationflags': 0x08000000} if platform == 'win32' else {}
                popen.assert_called_once_with('echo hi', shell=True, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL, **options)
                self.assertIs(result, popen.return_value)

    def test_run_action_uses_spawn_and_cooldown(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'actions.json'
            path.write_text('{"right_front": "echo hi"}')
            with patch.object(holo, 'ACTIONS', path), patch.object(holo, '_last_fired', {}), patch.object(holo, 'spawn') as spawn:
                holo.run_action('right_front')
                holo.run_action('right_front')
                spawn.assert_called_once_with('echo hi')

    def test_windows_presets_reference_existing_scripts(self):
        presets = core.presets_for('win32', ROOT)
        for name in ('holo-shot.ps1', 'holo-notify.ps1'):
            self.assertTrue((ROOT / 'windows' / name).is_file())
            self.assertTrue(any(str(ROOT / 'windows' / name) in cmd for cmd in presets.values()))
        self.assertTrue(any('start "" ' in cmd for cmd in presets.values()))
        self.assertTrue(any('SendKeys([char]179)' in cmd for cmd in presets.values()))
        self.assertTrue(any('LockWorkStation' in cmd for cmd in presets.values()))

    def test_vbs_quotes_and_pythonw(self):
        entry = core.vbs_entry(r'C:\My Tools\python.exe', r'C:\Holo Files\holo_gui.py')
        self.assertEqual(entry, 'CreateObject("WScript.Shell").Run """C:\\My Tools\\pythonw.exe"" ""C:\\Holo Files\\holo_gui.py"" --minimized", 0, False\r\n')
        self.assertIn('a""b', core.vbs_entry('python.exe', 'a"b'))

    def test_windows_startup_utf16_and_removal(self):
        with tempfile.TemporaryDirectory() as d, patch.object(core.sys, 'platform', 'win32'), patch.dict(os.environ, {'APPDATA': d}):
            expected = Path(d) / 'Microsoft/Windows/Start Menu/Programs/Startup/holo-gui.vbs'
            self.assertEqual(core.autostart_path(), expected)
            core.set_autostart(True, r'C:\Python\python.exe', r'C:\Holo\holo_gui.py')
            self.assertTrue(core.autostart_enabled())
            self.assertIn('pythonw.exe', expected.read_bytes().decode('utf-16'))
            core.set_autostart(False, '', '')
            self.assertFalse(expected.exists())

    def test_device_backend_identity(self):
        mme = core.device_key('Microphone', 'MME')
        wasapi = core.device_key('Microphone', 'Windows WASAPI')
        self.assertNotEqual(mme, wasapi)
        self.assertIn('MME', core.device_label(1, 'Microphone', 'MME'))
        self.assertEqual(core.choose_device([(1, mme), (2, wasapi)], wasapi), 2)

    def test_windows_scripts_ascii_crlf(self):
        scripts = list((ROOT / 'windows').glob('*.ps1')) + [ROOT / 'install.ps1', ROOT / 'holo.cmd']
        for path in scripts:
            with self.subTest(path=path):
                data = path.read_bytes()
                data.decode('ascii')
                self.assertIn(b'\r\n', data)
                self.assertNotIn(b'\n', data.replace(b'\r\n', b''))
                self.assertNotIn(b'\r', data.replace(b'\r\n', b''))


if __name__ == '__main__':
    unittest.main()
