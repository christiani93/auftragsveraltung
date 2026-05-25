@echo off
REM ===== Visual Studio Build Tools 2022 fuer Flutter Windows-Builds =====
REM
REM Bitte mit Rechtsklick "Als Administrator ausfuehren" starten.
REM
REM Dieses Skript:
REM 1. Deinstalliert eine eventuell vorhandene D:\VSBuildTools-Installation
REM 2. Installiert frisch auf C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools
REM
REM Dauer: 30-60 Minuten, ca. 5-7 GB Download.

setlocal
cd /d "%~dp0"

REM Admin-Check
net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo FEHLER: Dieses Skript braucht Admin-Rechte.
    echo Rechtsklick "Als Administrator ausfuehren" verwenden.
    echo.
    pause
    exit /b 1
)

set EXE=%LOCALAPPDATA%\VSBuildTools\vs_BuildTools.exe
if not exist "%EXE%" (
    echo Bootstrap fehlt, lade neu...
    if not exist "%LOCALAPPDATA%\VSBuildTools" mkdir "%LOCALAPPDATA%\VSBuildTools"
    powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://aka.ms/vs/17/release/vs_BuildTools.exe' -OutFile '%EXE%' -UseBasicParsing"
)

set INSTALLER=C:\Program Files (x86)\Microsoft Visual Studio\Installer\setup.exe

REM Schritt 1: Alte D:-Installation entfernen
if exist "D:\VSBuildTools" (
    echo.
    echo === Schritt 1: Alte Installation auf D: deinstallieren ===
    "%INSTALLER%" uninstall --installPath "D:\VSBuildTools" --passive --norestart --wait
    if errorlevel 1 (
        echo Hinweis: Uninstall meldete Fehler %errorlevel%. Versuche trotzdem weiter.
    )
    REM Reste loeschen falls noch was da ist
    if exist "D:\VSBuildTools" rmdir /S /Q "D:\VSBuildTools"
    echo Alte Installation entfernt.
) else (
    echo Schritt 1 uebersprungen ^(keine D:-Installation gefunden^).
)

REM Schritt 2: Frisch auf C: installieren
set INSTPATH=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools

echo.
echo === Schritt 2: Installation auf C: ===
echo Ziel: %INSTPATH%
echo Komponenten: C++ Workload + Windows 11 SDK + CMake + ATL
echo.

"%EXE%" ^
    --passive --wait --norestart --nocache ^
    --installPath "%INSTPATH%" ^
    --add Microsoft.VisualStudio.Workload.VCTools ^
    --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 ^
    --add Microsoft.VisualStudio.Component.Windows11SDK.22621 ^
    --add Microsoft.VisualStudio.Component.VC.CMake.Project ^
    --add Microsoft.VisualStudio.Component.VC.ATL ^
    --includeRecommended

if errorlevel 1 (
    echo.
    echo Install mit Fehler-Code %errorlevel% beendet.
    pause
    exit /b 1
)

echo.
echo === Verifikation ===
if exist "%INSTPATH%\VC\Tools\MSVC" (
    echo MSVC installiert in: %INSTPATH%\VC\Tools\MSVC
    dir /B "%INSTPATH%\VC\Tools\MSVC"
) else (
    echo HINWEIS: MSVC-Ordner fehlt — Installation hat moeglicherweise versagt.
)

echo.
echo Fertig. Claude kann jetzt auch den Windows-Build durchfuehren.
pause
