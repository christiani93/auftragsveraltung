@echo off
REM Baut die Flutter Windows-Exe und schreibt das Log nach dist\build-windows.log.
REM Setzt alle Env-Vars die Flutter/Gradle brauchen.

setlocal
set JAVA_HOME=D:\Tools\OpenJDK\jdk-17.0.13+11
set ANDROID_SDK_ROOT=D:\Tools\Android\Sdk
set ANDROID_HOME=%ANDROID_SDK_ROOT%
set JAVA_TOOL_OPTIONS=-Djavax.net.ssl.trustStoreType=WINDOWS-ROOT
set GRADLE_OPTS=-Djavax.net.ssl.trustStoreType=WINDOWS-ROOT
set PATH=D:\Tools\Flutter\flutter\bin;%JAVA_HOME%\bin;%ANDROID_SDK_ROOT%\platform-tools;%PATH%

cd /d D:\Dev\auftragsverwaltung_flutter

set LOGDIR=%~dp0dist
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set LOG=%LOGDIR%\build-windows.log

echo === flutter doctor ===   >  "%LOG%"
flutter doctor -v             >> "%LOG%" 2>&1

echo.                          >> "%LOG%"
echo === flutter config (Windows enable) === >> "%LOG%"
flutter config --enable-windows-desktop >> "%LOG%" 2>&1

echo.                          >> "%LOG%"
echo === flutter create --platforms=windows . === >> "%LOG%"
flutter create --platforms=windows . >> "%LOG%" 2>&1

echo.                          >> "%LOG%"
echo === flutter pub get === >> "%LOG%"
flutter pub get >> "%LOG%" 2>&1

echo.                          >> "%LOG%"
echo === flutter build windows --release === >> "%LOG%"
flutter build windows --release >> "%LOG%" 2>&1

echo.                          >> "%LOG%"
echo === Ausgabe-Verzeichnis === >> "%LOG%"
dir build\windows\x64\runner\Release  >> "%LOG%" 2>&1

echo.
echo Fertig. Log liegt unter:
echo   %LOG%
echo.
type "%LOG%" | findstr /B /C:"[" /C:"X" /C:"!" /C:"Built" /C:"Error" /C:"FAILED" /C:"Doctor"
echo.
echo (Vollstaendiges Log oben anschauen oder mir Bescheid geben.)
pause
