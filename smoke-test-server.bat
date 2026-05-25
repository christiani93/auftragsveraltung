@echo off
REM Installiert die neuen Python-Packages und startet die Flask-App lokal.
REM Beim ersten Lauf werden Flask-Login + WeasyPrint + Gunicorn installiert.

setlocal
cd /d "%~dp0"

echo === Installiere Python-Packages ===
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo FEHLER bei pip install
    pause
    exit /b 1
)

echo.
echo === Flask-App startet ===
echo Beim ersten Start wird ein Admin-Account angelegt.
echo Passwort steht dann im Konsolen-Output, BITTE NOTIEREN!
echo.
echo URL:  http://localhost:5000
echo Stop: Strg+C
echo.

python app.py

pause
