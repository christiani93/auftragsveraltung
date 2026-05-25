@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM ===== Auftragsverwaltung - Entwicklungs-Start =====
REM Venv und __pycache__ liegen AUSSERHALB von OneDrive
REM (LocalAppData wird von OneDrive nicht synchronisiert).

set VENV_DIR=%LOCALAPPDATA%\Auftragsverwaltung\venv
set PYTHONPYCACHEPREFIX=%LOCALAPPDATA%\Auftragsverwaltung\pycache

where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo FEHLER: Python wurde nicht gefunden.
    echo Bitte Python 3.10+ von https://www.python.org installieren.
    echo.
    pause
    exit /b 1
)

REM Hinweis falls alter .venv noch im OneDrive-Ordner liegt
if exist .venv (
    echo.
    echo HINWEIS: Es liegt ein alter .venv-Ordner im Projektverzeichnis ^(OneDrive^).
    echo Dieser wird nicht mehr verwendet, OneDrive synchronisiert ihn aber laufend.
    echo.
    echo Soll der alte .venv-Ordner jetzt geloescht werden? ^(J/N^)
    set /p LOESCHEN=
    if /i "!LOESCHEN!"=="J" (
        rmdir /s /q .venv
        echo Alter .venv geloescht.
        echo.
    )
)

if not exist "%VENV_DIR%" (
    echo Lege virtuelle Python-Umgebung an ^(einmalig^) unter:
    echo   %VENV_DIR%
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo FEHLER beim Anlegen der virtuellen Umgebung.
        pause
        exit /b 1
    )
)

call "%VENV_DIR%\Scripts\activate.bat"

if not exist "%VENV_DIR%\.installed" (
    echo Installiere Abhaengigkeiten...
    python -m pip install --upgrade pip --quiet
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo FEHLER bei pip install.
        pause
        exit /b 1
    )
    echo. > "%VENV_DIR%\.installed"
)

REM Browser nach kurzem Delay oeffnen
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:5000"

python app.py

pause
