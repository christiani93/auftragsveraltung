@echo off
REM Defensiv: alles geloggt, mehrere Pauses, kein vorzeitiges Schliessen
setlocal enabledelayedexpansion

set "LOGDIR=%~dp0dist"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\build-all-log.txt"

echo Build-All-Log %DATE% %TIME% > "%LOG%"
echo. >> "%LOG%"

echo === Step 0: Env-Vars setzen ===
echo === Step 0: Env-Vars setzen === >> "%LOG%"
set "JAVA_HOME=%LOCALAPPDATA%\OpenJDK\jdk-17.0.13+11"
set "ANDROID_SDK_ROOT=%LOCALAPPDATA%\Android\Sdk"
set "ANDROID_HOME=%ANDROID_SDK_ROOT%"
set "JAVA_TOOL_OPTIONS=-Djavax.net.ssl.trustStoreType=WINDOWS-ROOT"
set "GRADLE_OPTS=-Djavax.net.ssl.trustStoreType=WINDOWS-ROOT"
set "PATH=%LOCALAPPDATA%\Flutter\flutter\bin;%JAVA_HOME%\bin;%ANDROID_SDK_ROOT%\platform-tools;%PATH%"

echo JAVA_HOME=%JAVA_HOME% >> "%LOG%"
echo ANDROID_SDK_ROOT=%ANDROID_SDK_ROOT% >> "%LOG%"
echo. >> "%LOG%"

set "PROJ=C:\Users\chris\Dev\auftragsverwaltung_flutter"
if not exist "%PROJ%" (
    echo FEHLER: Projekt-Pfad existiert nicht: %PROJ%
    echo FEHLER: Projekt-Pfad existiert nicht: %PROJ% >> "%LOG%"
    pause
    exit /b 1
)

echo === Step 1: cd ins Projekt ===
echo === Step 1: cd ins Projekt === >> "%LOG%"
cd /d "%PROJ%"
cd >> "%LOG%"
echo. >> "%LOG%"

echo === Step 2: flutter --version ===
echo === Step 2: flutter --version === >> "%LOG%"
call flutter --version >> "%LOG%" 2>&1
if errorlevel 1 (
    echo FEHLER: flutter --version fehlgeschlagen, sieh ins Log
    echo FEHLER: flutter --version fehlgeschlagen >> "%LOG%"
    type "%LOG%"
    pause
    exit /b 1
)

echo === Step 3: flutter pub get ===
echo === Step 3: flutter pub get === >> "%LOG%"
call flutter pub get >> "%LOG%" 2>&1
if errorlevel 1 (
    echo FEHLER: pub get fehlgeschlagen
    echo Letzte Zeilen aus dem Log:
    powershell -NoProfile -Command "Get-Content '%LOG%' -Tail 20"
    pause
    exit /b 1
)
echo pub get OK.

echo === Step 4: flutter build apk --release ===
echo === Step 4: flutter build apk --release === >> "%LOG%"
call flutter build apk --release >> "%LOG%" 2>&1
if errorlevel 1 (
    echo FEHLER: APK-Build fehlgeschlagen
    powershell -NoProfile -Command "Get-Content '%LOG%' -Tail 30"
    pause
    exit /b 1
)
echo APK OK.

echo === Step 5: flutter build windows --release ===
echo === Step 5: flutter build windows --release === >> "%LOG%"
call flutter build windows --release >> "%LOG%" 2>&1
if errorlevel 1 (
    echo FEHLER: Windows-Build fehlgeschlagen
    powershell -NoProfile -Command "Get-Content '%LOG%' -Tail 30"
    pause
    exit /b 1
)
echo Windows OK.

echo === Step 6: Artefakte kopieren ===
echo === Step 6: Artefakte kopieren === >> "%LOG%"
copy /Y "build\app\outputs\flutter-apk\app-release.apk" "%LOGDIR%\Auftragsverwaltung.apk" >> "%LOG%"
if exist "%LOGDIR%\Auftragsverwaltung-Windows" rmdir /S /Q "%LOGDIR%\Auftragsverwaltung-Windows"
robocopy "build\windows\x64\runner\Release" "%LOGDIR%\Auftragsverwaltung-Windows" /E /R:1 /W:1 /NP /NFL /NDL >> "%LOG%" 2>&1
if exist "%LOGDIR%\Auftragsverwaltung-Windows.zip" del "%LOGDIR%\Auftragsverwaltung-Windows.zip"
powershell -NoProfile -Command "Compress-Archive -Path 'build\windows\x64\runner\Release\*' -DestinationPath '%LOGDIR%\Auftragsverwaltung-Windows.zip'"

echo.
echo ============================================
echo  FERTIG. Artefakte in %LOGDIR%
echo  Log: %LOG%
echo ============================================
echo.
pause
