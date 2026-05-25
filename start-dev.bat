@echo off
REM Schnellstart ohne Setup-Pruefungen.
cd /d "%~dp0"

set VENV_DIR=%LOCALAPPDATA%\Auftragsverwaltung\venv
set PYTHONPYCACHEPREFIX=%LOCALAPPDATA%\Auftragsverwaltung\pycache
set AUFTRAGSVERWALTUNG_DEBUG=1

if not exist "%VENV_DIR%" (
    echo Bitte zuerst start.bat ausfuehren.
    pause
    exit /b 1
)

call "%VENV_DIR%\Scripts\activate.bat"
python app.py
pause
