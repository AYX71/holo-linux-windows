"""Qt-free logic for the Holo GUI, so it can be tested without a display."""
import json
import math
import ntpath
import os
import sys
from pathlib import Path

import holo

CUSTOM = "Custom command"
SENSITIVITIES = {"Less sensitive": 0.75, "Balanced": 1.0,
                 "Sensitive": 1.5, "Very sensitive": 2.0}

_SHOT = ('f="$HOME/Pictures/Screenshots/holo-$(date +%s%N).png"; '
         'mkdir -p "$HOME/Pictures/Screenshots"; ')
_SAVED = ' && notify-send -i camera-photo Holo "Screenshot saved"'

PRESETS = {
    "Nothing": "",
    "Screenshot + notification (KDE Spectacle)":
        _SHOT + 'spectacle -f -b -n -o "$f"' + _SAVED,
    "Screenshot + notification (GNOME)":
        _SHOT + 'gnome-screenshot -f "$f"' + _SAVED,
    "Screenshot + notification (grim, Wayland)":
        _SHOT + 'grim "$f"' + _SAVED,
    "Show a notification": 'notify-send Holo "Tap detected"',
    "Open a website (edit the address)": "xdg-open https://archlinux.org",
    "Play / pause media": "playerctl play-pause",
    "Lock the screen": "loginctl lock-session",
}


_LINUX_PRESETS = PRESETS


def presets_for(platform, base):
    if platform != "win32":
        return dict(_LINUX_PRESETS)
    scripts = Path(base) / "windows"

    def ps(name):
        return ('powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden '
                '-ExecutionPolicy Bypass -File "' + str(scripts / name) + '"')
    return {
        "Nothing": "",
        "Screenshot + notification": ps("holo-shot.ps1"),
        "Show a notification": ps("holo-notify.ps1"),
        "Open a website (edit the address)": 'start "" https://archlinux.org',
        "Play / pause media": 'powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -Command "(New-Object -ComObject WScript.Shell).SendKeys([char]179)"',
        "Lock the screen": "rundll32 user32.dll,LockWorkStation",
    }


def prepare_windows_scripts(base, data_dir):
    """Keep preset paths valid after a one-file executable deletes its temp dir."""
    target = Path(data_dir) / "bundled-actions"
    scripts = target / "windows"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("holo-shot.ps1", "holo-notify.ps1"):
        contents = (Path(base) / "windows" / name).read_bytes()
        path = scripts / name
        if not path.exists() or path.read_bytes() != contents:
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(contents)
            tmp.replace(path)
    return target


_PRESET_BASE = Path(__file__).resolve().parent
if sys.platform == "win32" and getattr(sys, "frozen", False):
    _PRESET_BASE = prepare_windows_scripts(_PRESET_BASE, holo.DATA_DIR)
PRESETS = presets_for(sys.platform, _PRESET_BASE)


def zone_title(zone):
    return zone.replace("_", " ").capitalize()


def preset_for(command):
    """Name of the preset whose command equals `command`, else CUSTOM."""
    for name, cmd in PRESETS.items():
        if cmd == (command or "").strip():
            return name
    return CUSTOM


# ---- files ---------------------------------------------------------------
def load_actions(path=None):
    path = Path(path) if path else holo.ACTIONS
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {z: c for z, c in data.items() if z in holo.ZONES and isinstance(c, str)}


def save_actions(actions, path=None):
    path = Path(path) if path else holo.ACTIONS
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {z: c.strip() for z, c in actions.items()
             if z in holo.ZONES and c and c.strip()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, indent=2))
    tmp.replace(path)


class Settings:
    """Small JSON settings file for the GUI."""

    def __init__(self, path=None):
        self.path = Path(path) if path else holo.CONF_DIR / "gui.json"
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2))


# ---- autostart (XDG autostart works on KDE, GNOME, XFCE, ...) --------------
def autostart_path():
    if sys.platform == "win32":
        base = holo.app_dirs("win32", os.environ, Path.home())[1].parent
        return base / "Microsoft/Windows/Start Menu/Programs/Startup/holo-gui.vbs"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / "holo-gui.desktop"


def _q(s):
    for ch in ("\\", '"', "$", "`"):
        s = s.replace(ch, "\\" + ch)
    return f'"{s}"'


def desktop_entry(python, script):
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Holo\n"
            "Comment=Tap the desk to trigger actions\n"
            f"Exec={_q(python)} {_q(script)} --minimized\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n")


def vbs_entry(python, script):
    # ntpath also permits testing Windows paths on Linux.
    if getattr(sys, "frozen", False):
        command = f'"{python}" --minimized'
    else:
        python = ntpath.join(ntpath.dirname(python), "pythonw.exe")
        command = f'"{python}" "{script}" --minimized'
    quoted = '"' + command.replace('"', '""') + '"'
    return 'CreateObject("WScript.Shell").Run ' + quoted + ', 0, False\r\n'


def set_autostart(enabled, python, script):
    path = autostart_path()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            path.write_bytes(vbs_entry(python, script).encode("utf-16"))
        else:
            path.write_text(desktop_entry(python, script))
    elif path.exists():
        path.unlink()
    return path


def autostart_enabled():
    return autostart_path().exists()


# ---- audio devices and level -----------------------------------------------
def input_devices(devices):
    """[(index, name)] for devices that can record."""
    return [(i, d["name"]) for i, d in enumerate(devices)
            if d.get("max_input_channels", 0) > 0]


def device_key(name, backend=None):
    return f"{name} [{backend}]" if backend else name


def device_label(index, name, backend=None):
    return f"{device_key(name, backend)}  (#{index})"


def choose_device(devs, saved_name=None):
    """Index to preselect: the saved one, else 'pipewire', else None (default)."""
    for i, name in devs:
        if saved_name and name == saved_name:
            return i
    for i, name in devs:
        if name == "pipewire":
            return i
    return None


def level_percent(rms, floor_db=-70.0, ceil_db=-10.0):
    db = 20 * math.log10(max(rms, 1e-9))
    return int(max(0, min(100, (db - floor_db) / (ceil_db - floor_db) * 100)))


# ---- calibration state machine ---------------------------------------------
class CalibrationSession:
    """Tracks which zone is being calibrated and the samples collected.

    The user presses "Ready" for each zone (begin_zone); until then taps are
    ignored, so moving a hand between zones doesn't pollute the data.
    """

    def __init__(self, zones, per_zone):
        self.zones = list(zones)
        self.per_zone = per_zone
        self.samples = {z: [] for z in self.zones}
        self.index = 0
        self.waiting = True

    @property
    def current(self):
        return self.zones[self.index]

    @property
    def zone_number(self):
        return self.index + 1

    def begin_zone(self):
        self.waiting = False

    def add(self, feature_vector):
        """Returns (status, zone, count). Status: ignored, accepted,
        zone_done, or all_done."""
        zone = self.current
        if self.waiting:
            return "ignored", zone, len(self.samples[zone])
        self.samples[zone].append(feature_vector)
        count = len(self.samples[zone])
        if count < self.per_zone:
            return "accepted", zone, count
        if self.index == len(self.zones) - 1:
            return "all_done", zone, count
        self.index += 1
        self.waiting = True
        return "zone_done", zone, count

    def dataset(self):
        X, y = [], []
        for z in self.zones:
            for f in self.samples[z]:
                X.append(f)
                y.append(z)
        return X, y
