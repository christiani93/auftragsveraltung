@echo off
REM ===== SDKs von D: nach C: zurueckverschieben =====
REM
REM ALS ADMIN AUSFUEHREN (Rechtsklick "Als Administrator ausfuehren")
REM weil takeown/icacls Admin-Rechte brauchen, um die Datei-Ownerschaft
REM von Claudes Sandbox-Identitaet auf dich umzustellen.

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

echo === Schritt 1: Ownership der D:\Tools-Files an dich uebertragen ===
takeown /F "D:\Tools" /R /D Y /SKIPSL >nul 2>&1
echo (takeown fertig)

echo.
echo === Schritt 2: Vollzugriff fuer dich gewaehren ===
icacls "D:\Tools" /grant "%USERNAME%:(OI)(CI)F" /T /C /Q >nul 2>&1
echo (icacls fertig)

echo.
echo === Schritt 3: Robocopy D:\Tools -^> C:\Users\chris\AppData\Local ===

echo.
echo --- Flutter ---
robocopy "D:\Tools\Flutter" "%LOCALAPPDATA%\Flutter" /MIR /R:2 /W:2 /MT:8 /NP /NFL /NDL
if errorlevel 8 ( echo FEHLER bei Flutter robocopy=%errorlevel% ) else ( rmdir /S /Q "D:\Tools\Flutter" 2>nul & echo OK )

echo.
echo --- Android ---
robocopy "D:\Tools\Android" "%LOCALAPPDATA%\Android" /MIR /R:2 /W:2 /MT:8 /NP /NFL /NDL
if errorlevel 8 ( echo FEHLER bei Android robocopy=%errorlevel% ) else ( rmdir /S /Q "D:\Tools\Android" 2>nul & echo OK )

echo.
echo --- OpenJDK ---
robocopy "D:\Tools\OpenJDK" "%LOCALAPPDATA%\OpenJDK" /MIR /R:2 /W:2 /MT:8 /NP /NFL /NDL
if errorlevel 8 ( echo FEHLER bei OpenJDK robocopy=%errorlevel% ) else ( rmdir /S /Q "D:\Tools\OpenJDK" 2>nul & echo OK )

echo.
echo === Aufraeumen ===
rmdir "D:\Tools" 2>nul
rmdir "D:\Dev" 2>nul

echo.
echo === Verifikation ===
if exist "%LOCALAPPDATA%\Flutter\flutter\bin\flutter.bat" (echo OK Flutter ist da) else (echo FEHLT Flutter)
if exist "%LOCALAPPDATA%\Android\Sdk\cmdline-tools\latest\bin\sdkmanager.bat" (echo OK Android-SDK ist da) else (echo FEHLT Android-SDK)
if exist "%LOCALAPPDATA%\OpenJDK\jdk-17.0.13+11\bin\java.exe" (echo OK OpenJDK ist da) else (echo FEHLT OpenJDK)

echo.
echo Falls alles OK: schreib Claude "SDKs sind durch", dann macht er weiter.
pause
