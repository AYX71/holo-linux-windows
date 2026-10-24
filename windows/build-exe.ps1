$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
# Run with a Python environment containing requirements-build.txt.
python -m PyInstaller --noconfirm --clean --onefile --windowed --noupx --name Holo --collect-all _sounddevice_data --add-data "windows/holo-shot.ps1:windows" --add-data "windows/holo-notify.ps1:windows" --add-data "LICENSE:." holo_gui.py
if ($LASTEXITCODE -ne 0) { throw "Building Holo.exe failed." }
Write-Host "Built dist/Holo.exe. Test on Windows before distributing as verified."
