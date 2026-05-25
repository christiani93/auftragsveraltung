@echo off
REM Kopiert Claudes Sandbox-Files (D:\WpSystem\...\Claude_pzs8sxrjxfjjc\LocalCache\Local)
REM auf den echten C:\Users\chris\AppData\Local Pfad, damit deine normale Shell die Tools findet.
REM
REM Doppelklick. Kein Admin noetig (kopiert nur in dein eigenes Userprofil).

setlocal enabledelayedexpansion

REM Sandbox-Wurzel via Pattern finden — Package-Family ist konstant
set "SANDBOX="
for /D %%S in ("D:\WpSystem\*") do (
    if exist "%%S\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local" (
        set "SANDBOX=%%S\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local"
    )
)

if "%SANDBOX%"=="" (
    echo FEHLER: Sandbox-Pfad nicht gefunden unter D:\WpSystem\*
    pause
    exit /b 1
)

echo Sandbox-Quelle: %SANDBOX%
echo.

echo === Robocopy Flutter ===
robocopy "%SANDBOX%\Flutter" "%LOCALAPPDATA%\Flutter" /MIR /R:1 /W:1 /MT:8 /NP /NFL /NDL
if errorlevel 8 echo WARN: Flutter robocopy exit %errorlevel%

echo.
echo === Robocopy Android ===
robocopy "%SANDBOX%\Android" "%LOCALAPPDATA%\Android" /MIR /R:1 /W:1 /MT:8 /NP /NFL /NDL
if errorlevel 8 echo WARN: Android robocopy exit %errorlevel%

echo.
echo === Robocopy OpenJDK ===
robocopy "%SANDBOX%\OpenJDK" "%LOCALAPPDATA%\OpenJDK" /MIR /R:1 /W:1 /MT:8 /NP /NFL /NDL
if errorlevel 8 echo WARN: OpenJDK robocopy exit %errorlevel%

echo.
echo === Verifikation ===
if exist "%LOCALAPPDATA%\Flutter\flutter\bin\flutter.bat" (echo OK Flutter) else (echo FEHLT Flutter)
if exist "%LOCALAPPDATA%\Android\Sdk\cmdline-tools\latest\bin\sdkmanager.bat" (echo OK Android-SDK) else (echo FEHLT Android-SDK)
if exist "%LOCALAPPDATA%\OpenJDK\jdk-17.0.13+11\bin\java.exe" (echo OK OpenJDK) else (echo FEHLT OpenJDK)

echo.
echo Wenn alles OK: nochmal flutter-pub-get.bat probieren.
pause
