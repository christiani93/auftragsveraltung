@echo off
setlocal
cd /d "%~dp0"

REM ===== Baut Auftragsverwaltung.exe mit PyInstaller =====
REM Temp-Build liegt ausserhalb von OneDrive,
REM nur das fertige Exe landet im dist\-Ordner neben dem Projekt.

set VENV_DIR=%LOCALAPPDATA%\Auftragsverwaltung\venv
set BUILD_DIR=%LOCALAPPDATA%\Auftragsverwaltung\build
set SPEC_DIR=%LOCALAPPDATA%\Auftragsverwaltung\spec
set PYTHONPYCACHEPREFIX=%LOCALAPPDATA%\Auftragsverwaltung\pycache

if not exist "%VENV_DIR%" (
    echo Bitte zuerst start.bat ausfuehren, damit die virtuelle Umgebung eingerichtet wird.
    pause
    exit /b 1
)

call "%VENV_DIR%\Scripts\activate.bat"

echo.
echo Installiere/aktualisiere PyInstaller...
python -m pip install --upgrade pyinstaller --quiet
if errorlevel 1 (
    echo FEHLER bei pip install pyinstaller
    pause
    exit /b 1
)

REM Alte Build-Artefakte bereinigen (ausserhalb OneDrive)
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%SPEC_DIR%" rmdir /s /q "%SPEC_DIR%"
if exist dist rmdir /s /q dist

echo.
echo Baue Auftragsverwaltung.exe ^(dauert eine Minute^)...
echo   Temp-Build:  %BUILD_DIR%
echo   Ergebnis:    %CD%\dist\Auftragsverwaltung.exe
echo.

pyinstaller ^
    --name Auftragsverwaltung ^
    --onefile ^
    --console ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --hidden-import jinja2 ^
    --hidden-import flask ^
    --workpath "%BUILD_DIR%" ^
    --specpath "%SPEC_DIR%" ^
    --distpath dist ^
    app.py

if errorlevel 1 (
    echo.
    echo Build fehlgeschlagen.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Fertig: %CD%\dist\Auftragsverwaltung.exe
echo.
echo   Das Exe kannst du direkt aus dem OneDrive-Ordner starten.
echo ============================================================
echo.
pause
