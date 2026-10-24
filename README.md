# Holo for Linux

Turn the desk around your laptop into tap-sensitive buttons. Tap near the
laptop, and a classifier listens through the built-in microphone, works out
which of four zones you tapped, and runs a command you assigned to that zone.

> **Status: experimental prototype.** It works well for one zone on the
> author's desk and less reliably for the others. See [Results](#results).
> No accuracy is promised on your hardware: calibrate and run `eval` yourself.

This is an independent Linux port of the idea behind
[JustinGamer191/Holo](https://github.com/JustinGamer191/Holo), a macOS app.
It is not affiliated with that project, and it does not contain its code. It
was written from that project's public README description of the pipeline,
with Claude's help. No code was copied.

## What it does

- Four zones: `left_rear`, `right_rear`, `left_front`, `right_front`
- Runs entirely locally: no network, no cloud, audio is never saved
- One Python file (`holo.py`), two dependencies (`numpy`, `sounddevice`)
- Optional, unproven background class: record typing, talking, and touches
  to try to reduce false triggers
- Optional, experimental stereo cues if your microphone really is stereo
- Runs at login as a systemd user service, or from a tray icon with the GUI
- A Qt graphical interface: pick the microphone, calibrate with a picture of
  the four zones, watch taps light up live, and choose what each zone does
- Each zone can run any shell command: screenshots, notifications, opening
  apps, copying text, and so on

## How it works

![Pipeline: microphone, onset detector, 90 ms window and gate, features, classifier, action](docs/pipeline.svg)

1. **Onset detection.** An adaptive noise floor is learned from the room. A
   block that is clearly louder than the floor starts a capture, with a short
   refractory period afterwards.
2. **Gating.** Clipped windows and sustained sounds (like speech) are rejected.
3. **Features.** 32 log-spaced spectral bands (100 Hz to 16 kHz, with the
   overall loudness removed) plus a 12-bin temporal envelope.
4. **Classifier.** A closed-form ridge regression over the four zones. A tap is
   rejected if the top score is too low, if the top two zones are too close,
   or if it is too far from any calibration tap.
5. **Action.** The zone's command from `actions.json` runs, with a per-zone
   cooldown.

Rejecting uncertain taps is deliberate: a wrong zone fires the wrong action,
while a rejection just does nothing.

## Results

Held-out eval accuracy on the author's laptop (Arch Linux, KDE Wayland,
PipeWire, microphone device #11) was **70%, 75%, 78%, 82%, and 82%** across runs.
Almost all misses were rejections (usually `low confidence`), with very few
wrong-zone predictions. Earlier live screenshots showed `right_front` recognized
21 of 21 taps; that observation is separate from held-out eval accuracy.

Lowering `MIN_SCORE` to 0.10 made results worse, including seven wrong-zone
predictions, so the default remains 0.30. Moving the laptop after calibration
invalidates the profile.

Known gaps:

- The leave-one-out score printed during calibration is always about 100% and
  does not predict real accuracy. Only `eval` is a real test.
- The background class and stereo cues are tested on synthetic audio only
  (`python -m unittest discover -s tests`). The real-world evidence for the
  background class is inconclusive; both features remain unverified on hardware:
  after adding background sounds, run `eval` and check real taps are still
  recognized, because a background class can also reject genuine taps.
- Stereo cues require two different channels; whether they improve accuracy is unverified.
- The systemd service under KDE and rendering of `docs/pipeline.svg` on GitHub
  have not been verified.

## Install

Works on any Linux distribution with PipeWire or PulseAudio. The installer
supports Arch, Debian/Ubuntu and Fedora out of the box:

```bash
git clone https://github.com/AYX71/holo-linux.git
cd holo-linux
./install.sh
```

It installs the system packages, creates a virtual environment inside the
project folder, and adds a `holo` command to `~/.local/bin`.

On other distributions, install Python 3, PortAudio and `notify-send` with your
package manager, then:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python holo.py devices
```

## Windows (experimental)

### Standalone GUI executable

The `Windows EXE` GitHub Actions workflow builds a single `Holo.exe` for
64-bit Windows with Python, Qt, NumPy, and the audio dependencies bundled.
After a successful build, open [Actions](https://github.com/AYX71/holo-linux/actions/workflows/windows-exe.yml),
select the latest successful run, and download **Holo-Windows-x64** under
Artifacts (GitHub sign-in required). Extract the ZIP, keep `Holo.exe` in a
permanent folder, and double-click it. No Python installation or terminal
commands are required to use the executable. Calibration is still required.

The workflow runs the tests on Windows and checks that the packaged GUI starts
and loads its audio dependency and action scripts. These checks do not test
microphone capture, zone accuracy, actions, or login behavior on real hardware.
An executable is available only after the workflow finishes successfully.
The executable is unsigned, so Windows may display a reputation warning.

To build it yourself on Windows with Python installed:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-build.txt
.\windows\build-exe.ps1
```

The output is `dist\Holo.exe`. The build script must run on Windows.

### Run from source

Windows support has **never been run on Windows**. The installer, microphone
capture, actions, GUI, and autostart are untested there until someone confirms
them. Linux tests of platform branches do not establish Windows compatibility.

Install Python 3 with the Python launcher or Python on PATH. In PowerShell,
from the project folder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Gui
.\holo.cmd devices
.\holo.cmd calibrate
.\holo.cmd eval
.\holo.cmd
```

The first command creates `.venv`, installs dependencies and the optional GUI,
and adds a Holo shortcut to the Start menu. Omit `-Gui` for CLI only. The next
commands list microphones, calibrate, measure held-out accuracy, and listen.
Keep the project folder in place; the shortcut and action presets refer to it.

- Enable microphone access for desktop apps in Windows privacy settings.
- Each microphone may appear several times, once per audio backend (MME,
  WASAPI, and others). The GUI includes backend names. Prefer the system
  default or MME first; WASAPI may refuse the required 48 kHz stream.
- Turn off audio enhancements/noise suppression if available, then recalibrate.
- Screenshot and notification presets use the bundled PowerShell scripts in
  `windows/`. Screenshots cover the virtual desktop and save to
  `Pictures\Screenshots`; Windows notification settings may suppress balloons.
- Data is in `%LOCALAPPDATA%\holo`, actions/settings in `%APPDATA%\holo`.
  Commands in `actions.json` use the Windows command shell.
- The GUI's start-at-login setting writes a UTF-16 `.vbs` launcher in the user
  Startup folder. It starts `Holo.exe` for a packaged app or `pythonw.exe` for
  a source install, hidden. Enable “Start listening when
  the window opens” if desired; stop any other Holo process first.
- There is no systemd service on Windows. Linux `.sh` installers are for Linux.

## Graphical interface

```bash
./install-gui.sh                 # installs PySide6 and an app-menu entry
.venv/bin/python holo_gui.py     # or open "Holo" from your application menu
```

If you use a different virtual environment, pass its Python:
`./install-gui.sh ~/.venv/bin/python`. On Arch you can instead run
`sudo pacman -S pyside6` and start `python holo_gui.py` from a venv that was
created with `--system-site-packages`.

The window has four tabs: **Live** (tap and see which zone lights up, with its
score), **Calibrate** (guided, one zone at a time), **Actions** (choose what
each zone does from presets such as screenshots, or type your own command) and
**Settings** (start at login in the tray). It reads and writes the same profile
and actions files as the command-line version.

Stop any other Holo process first (the `holo` command or the systemd service):
two programs cannot share the microphone cleanly.

The GUI is new. Its logic is covered by tests, but how it looks and behaves in
a real Qt session has had little testing. If it misbehaves, the command-line
version keeps working unchanged.

## Usage

After running the installer, `holo` is a shortcut for `python holo.py`: for
example `holo calibrate`, `holo eval`, and plain `holo` to run. If you did not
use the installer, replace `holo` with `python holo.py` inside the virtual
environment. Try it without `--device` first, which uses the system default
input.

```bash
python holo.py devices                 # list audio devices
python holo.py calibrate --device N    # tap each zone 15 times
python holo.py eval      --device N    # 40 fresh taps, shows accuracy
python holo.py run       --device N    # listen and run actions
```

Instead of `--device N` every time, you can set `export HOLO_DEVICE=N`.

Use a PipeWire or `default` input device (not a raw `hw:` device, which is
often busy) and check that it points at the built-in microphone with
`wpctl status`. Turn off any mic gain boost or auto-gain.

**Calibration tips**

- Put the laptop where it will stay. Moving it, or changing desks, means you
  need to recalibrate.
- Spread your taps over each zone's whole area and vary position and force a
  little. Ten identical taps make a profile that rejects real ones.
- Tap firmly and consistently, with a fingertip or knuckle, and pause between
  taps.

### Reducing false triggers

Calibration can optionally record 30 seconds of background sounds (the default
is no): type, talk, click the trackpad lightly, put things down on the desk,
but do not move the laptop and do not tap the zones. Holo
learns these as a "none" class to try to ignore similar sounds later. This is
opt-in and unproven: synthetic tests pass, but real-world evidence is inconclusive.
You can add more at any time without redoing the taps (needs a profile made with this
version):

```bash
holo negatives
holo eval        # confirm real taps are still recognized
```

If `eval` shows many real taps now rejected as `background sound`, record
fewer or more varied background sounds, or recalibrate and skip that step.

### Stereo (experimental)

Some laptops expose two microphones as two channels. Check yours:

```bash
holo stereo-check
```

If it says the channels differ, calibrate with `holo calibrate --channels 2`.
This adds left/right level differences and the inter-channel delay as features,
whose effect on real-world accuracy is unverified. If it says the channels are
identical, skip this.

### Run at login

```bash
./install-service.sh        # or ./install-service.sh 11 to pin a device number
systemctl --user status holo
journalctl --user -u holo -f
```

This needs systemd and a desktop that starts `graphical-session.target` (KDE
Plasma and GNOME do). If actions such as screenshots work in a terminal but not
from the service, the service may be missing your session's display variables.
Check `systemctl --user show-environment`. Run `systemctl --user stop holo`
before calibrating, since two programs cannot share the microphone cleanly.

## Actions

Create `~/.config/holo/actions.json`. Zones without an entry do nothing.

```json
{
  "right_front": "$HOME/.local/bin/holo-shot.sh"
}
```

Commands run through the shell, so only put commands you trust there.

**Example: screenshot with a notification (KDE/Spectacle)**

`~/.local/bin/holo-shot.sh`:

```sh
#!/bin/sh
f="$HOME/Pictures/Screenshots/holo-$(date +%s%N).png"
if spectacle -f -b -n -o "$f"; then
    notify-send -i camera-photo "Holo" "Screenshot saved: $(basename "$f")"
else
    notify-send -u critical "Holo" "Screenshot failed"
fi
```

Make it executable with `chmod +x`, and `mkdir -p ~/Pictures/Screenshots`.
Other tools: `grim` (Wayland), `gnome-screenshot`, `maim` or `scrot` (X11).

## Tuning

Constants at the top of `holo.py`:

| Constant | Effect |
| --- | --- |
| `MIN_SCORE` | Minimum classifier score. Lowering it lets uncertain taps through, and in testing those were mostly wrong |
| `MIN_MARGIN` | Required gap between best and second-best zone |
| `NOVELTY_FACTOR` | How far from a calibration tap is still accepted |
| `ONSET_RATIO` | How much louder than the room a sound must be to count |
| `SUSTAIN_RATIO` | Rejects speech-like, ringing sounds |
| `COOLDOWN` | Seconds before the same zone can fire again |

## Troubleshooting

- **Tests report three import errors, or `ModuleNotFoundError` for `numpy` or
  `sounddevice`**: the Python running them lacks dependencies. From the project
  folder on Linux, install them and run tests with the same Python:

  ```bash
  .venv/bin/python -m pip install -r requirements.txt
  .venv/bin/python -m unittest discover -s tests
  ```

  If `.venv` does not exist, first run `python -m venv .venv`. For the GUI also
  install `requirements-gui.txt`. On Windows use `.venv\Scripts\python.exe`
  in place of `.venv/bin/python`.
- **The GUI says it could not open the microphone**: another program is using
  it, or the chosen device is wrong. Try another entry in the Microphone list.
- **Nothing is detected**: check the default input with `wpctl status`, and
  that the mic is not muted.
- **Many `rejected: low confidence` lines**: recalibrate with more varied taps.
- **Many `rejected: background sound` lines on real taps**: the background class
  is too broad. Recalibrate and skip the background step, or record more
  varied background sounds.
- **No screenshot appears**: run your action script by hand first. On KDE
  Wayland, approve the screen-capture permission prompt once.

## Roadmap

- Measure the background class and stereo cues on real hardware
- Check and polish the GUI on real desktops (KDE, GNOME)
- The active ultrasonic probe from the original project
- Per-zone cooldowns and actions configurable from the config file

## Credits and license

Concept and pipeline description from
[JustinGamer191/Holo](https://github.com/JustinGamer191/Holo). Released under
the MIT license (see `LICENSE`).
