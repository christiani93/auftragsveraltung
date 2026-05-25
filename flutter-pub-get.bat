@echo off
REM Minimal-Pub-Get mit Logging. Vorher bitte alle anderen CMD/Bat-Fenster schliessen.

cd /d "%~dp0"
set "LOGDIR=%~dp0dist"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\pub-get-log.txt"

REM Frischen Log starten
echo Pub-Get-Log %DATE% %TIME% > "%LOG%"

REM Env-Vars
set JAVA_HOME=%LOCALAPPDATA%\OpenJDK\jdk-17.0.13+11
set ANDROID_SDK_ROOT=%LOCALAPPDATA%\Android\Sdk
set ANDROID_HOME=%ANDROID_SDK_ROOT%
set JAVA_TOOL_OPTIONS=-Djavax.net.ssl.trustStoreType=WINDOWS-ROOT
set PATH=%LOCALAPPDATA%\Flutter\flutter\bin;%JAVA_HOME%\bin;%ANDROID_SDK_ROOT%\platform-tools;%PATH%

cd /d C:\Users\chris\Dev\auftragsverwaltung_flutter

echo Wo flutter.bat ist: >> "%LOG%"
where flutter.bat >> "%LOG%" 2>&1

echo. >> "%LOG%"
echo === flutter --version === >> "%LOG%"
flutter --version >> "%LOG%" 2>&1

echo. >> "%LOG%"
echo === flutter pub get === >> "%LOG%"
flutter pub get >> "%LOG%" 2>&1
set PUBEXIT=%errorlevel%
echo (Exit %PUBEXIT%) >> "%LOG%"

echo.
echo ==========================================
echo  Log: %LOG%
echo  pub get Exit-Code: %PUBEXIT%
echo ==========================================
echo.
type "%LOG%"
echo.
pause
