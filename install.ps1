param([switch]$Gui)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
# Use the Python launcher when available, otherwise Python on PATH.
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -m venv .venv
} else {
    & python -m venv .venv
}
if ($LASTEXITCODE -ne 0) { throw "Could not create the Python environment." }
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Could not upgrade pip." }
& $python -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Could not install dependencies." }
if ($Gui) {
    & $python -m pip install -r (Join-Path $PSScriptRoot "requirements-gui.txt")
    if ($LASTEXITCODE -ne 0) { throw "Could not install the GUI." }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Programs")) "Holo.lnk"))
    $shortcut.TargetPath = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
    $shortcut.Arguments = '"' + (Join-Path $PSScriptRoot "holo_gui.py") + '"'
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.Description = "Tap the desk to trigger actions"
    $shortcut.Save()
}
Write-Host "Installed. Windows support is experimental and untested."
Write-Host "Next: .\holo.cmd devices, .\holo.cmd calibrate, .\holo.cmd eval"
if ($Gui) { Write-Host "Open Holo from the Start menu for the GUI." }
