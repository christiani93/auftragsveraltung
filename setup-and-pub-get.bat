@echo off
REM ===== Setup + Pub Get + Diagnose, alles geloggt =====
REM Macht:
REM   1) Pruefung was bereits auf C: ist
REM   2) Copy aus Claudes Sandbox falls noetig
REM   3) flutter pub get
REM   4) Alles geloggt nach dist\setup-log.txt

setlocal enabledelayedexpansion
cd /d "%~dp0"

set "LOGDIR=%~dp0dist"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\setup-log.txt"

REM Log neu starten
echo ===== Setup-Log %DATE% %TIME% ===== > "%LOG%"

call :run "1) Vor dem Copy: was ist im echten C: vorhanden?"
call :run "   Flutter:   " & if exist "%LOCALAPPDATA%\Flutter\flutter\bin\flutter.bat" (call :run "   OK") else (call :run "   FEHLT")
call :run "   Android:   " & if exist "%LOCALAPPDATA%\Android\Sdk\cmdline-tools\latest\bin\sdkmanager.bat" (call :run "   OK") else (call :run "   FEHLT")
call :run "   OpenJDK:   " & if exist "%LOCALAPPDATA%\OpenJDK\jdk-17.0.13+11\bin\java.exe" (call :run "   OK") else (call :run "   FEHLT")

REM Sandbox suchen
set "SANDBOX="
for /D %%S in ("D:\WpSystem\*") do (
    if exist "%%S\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local" (
        set "SANDBOX=%%S\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local"
    )
)
call :run ""
call :run "2) Sandbox-Pfad: %SANDBOX%"
if "%SANDBOX%"=="" (
    call :run "FEHLER: Sandbox nicht gefunden — Test, ob D:\WpSystem existiert:"
    if exist "D:\WpSystem" (call :run "  D:\WpSystem existiert, aber Package-Family nicht gefunden") else (call :run "  D:\WpSystem fehlt komplett")
    goto :showlog
)

call :run ""
call :run "3) Robocopy Sandbox -> echtes C:..."
echo --- Flutter --- >> "%LOG%"
robocopy "%SANDBOX%\Flutter" "%LOCALAPPDATA%\Flutter" /MIR /R:1 /W:1 /MT:8 /NP /NFL /NDL >> "%LOG%" 2>&1
echo (Flutter robocopy exit: %errorlevel%) >> "%LOG%"
echo --- Android --- >> "%LOG%"
robocopy "%SANDBOX%\Android" "%LOCALAPPDATA%\Android" /MIR /R:1 /W:1 /MT:8 /NP /NFL /NDL >> "%LOG%" 2>&1
echo (Android robocopy exit: %errorlevel%) >> "%LOG%"
echo --- OpenJDK --- >> "%LOG%"
robocopy "%SANDBOX%\OpenJDK" "%LOCALAPPDATA%\OpenJDK" /MIR /R:1 /W:1 /MT:8 /NP /NFL /NDL >> "%LOG%" 2>&1
echo (OpenJDK robocopy exit: %errorlevel%) >> "%LOG%"

call :run ""
call :run "4) Nach Copy: Verifikation"
if exist "%LOCALAPPDATA%\Flutter\flutter\bin\flutter.bat" (call :run "   Flutter:   OK") else (call :run "   Flutter:   FEHLT — Abbruch")
if exist "%LOCALAPPDATA%\Android\Sdk\cmdline-tools\latest\bin\sdkmanager.bat" (call :run "   Android:   OK") else (call :run "   Android:   FEHLT — Abbruch")
if exist "%LOCALAPPDATA%\OpenJDK\jdk-17.0.13+11\bin\java.exe" (call :run "   OpenJDK:   OK") else (call :run "   OpenJDK:   FEHLT — Abbruch")

if not exist "%LOCALAPPDATA%\Flutter\flutter\bin\flutter.bat" goto :showlog
if not exist "%LOCALAPPDATA%\Android\Sdk\cmdline-tools\latest\bin\sdkmanager.bat" goto :showlog
if not exist "%LOCALAPPDATA%\OpenJDK\jdk-17.0.13+11\bin\java.exe" goto :showlog

call :run ""
call :run "5) flutter pub get"
set JAVA_HOME=%LOCALAPPDATA%\OpenJDK\jdk-17.0.13+11
set ANDROID_SDK_ROOT=%LOCALAPPDATA%\Android\Sdk
set ANDROID_HOME=%ANDROID_SDK_ROOT%
set JAVA_TOOL_OPTIONS=-Djavax.net.ssl.trustStoreType=WINDOWS-ROOT
set PATH=%LOCALAPPDATA%\Flutter\flutter\bin;%JAVA_HOME%\bin;%ANDROID_SDK_ROOT%\platform-tools;%PATH%

cd /d C:\Users\chris\Dev\auftragsverwaltung_flutter
flutter pub get >> "%LOG%" 2>&1
echo (pub get exit: %errorlevel%) >> "%LOG%"

:showlog
echo. >> "%LOG%"
echo ===== Ende %DATE% %TIME% ===== >> "%LOG%"

echo.
echo ======================================================
echo  Log gespeichert: %LOG%
echo  Bitte Claude den Inhalt zeigen (oder einfach sagen
echo  "setup-log ist da") — er liest die Datei selbst.
echo ======================================================
echo.
type "%LOG%"
echo.
pause
goto :eof

:run
echo %~1
echo %~1 >> "%LOG%"
goto :eof
