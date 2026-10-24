Holo for Windows (experimental)

Unzip the download, keep Holo.exe in a permanent folder, and double-click it.
Python and pip are not required. No administrator rights are needed.
Enable microphone access for desktop apps in Windows privacy settings.
Choose System default or an MME microphone, then open Calibrate.
Calibration on your own desk is required; moving the laptop invalidates it.
Use Live to check zone detection, then configure Actions.

Quiet taps missed? Open Settings and choose Tap sensitivity: Sensitive.
Try Very sensitive only if needed. It can also detect more typing/noise.
Keep quiet for a second at startup, then calibrate with normal taps.
If Live says "low confidence", the tap was heard: recalibrate instead.
The microphone status reports silence, clipping, and interrupted audio.

Settings can enable start at login. Keep the executable in the same location
or disable and re-enable that setting after moving it.
Data: %LOCALAPPDATA%\holo
Settings: %APPDATA%\holo

The executable is unsigned. Windows may show a security/reputation warning.
Windows microphone capture, actions, and login behavior still require testing
on real Windows hardware. Automated build checks do not prove accuracy.
Reported Linux held-out accuracy was 70-82%; Windows accuracy is unknown.

Source and MIT license: https://github.com/AYX71/holo-linux
Built with Python, NumPy, sounddevice/PortAudio, PySide6/Qt, and PyInstaller.
Independent implementation from JustinGamer191/Holo's public README idea;
no code was copied. Developed with Claude's help.
