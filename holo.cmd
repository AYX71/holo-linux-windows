@echo off
setlocal
set "HOLO_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%HOLO_PY%" (
    echo Run install.ps1 first.
    exit /b 1
)
if "%~1"=="" (
    "%HOLO_PY%" "%~dp0holo.py" run
) else (
    "%HOLO_PY%" "%~dp0holo.py" %*
)
exit /b %errorlevel%
