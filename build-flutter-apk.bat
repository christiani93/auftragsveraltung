@echo off
REM Baut die Flutter-APK fuer Android und kopiert sie nach OneDrive\dist\.
REM Setzt alle Env-Vars die Flutter/Gradle brauchen.

setlocal
set JAVA_HOME=D:\Tools\OpenJDK\jdk-17.0.13+11
set ANDROID_SDK_ROOT=D:\Tools\Android\Sdk
set ANDROID_HOME=%ANDROID_SDK_ROOT%
set JAVA_TOOL_OPTIONS=-Djavax.net.ssl.trustStoreType=WINDOWS-ROOT
set GRADLE_OPTS=-Djavax.net.ssl.trustStoreType=WINDOWS-ROOT
set PATH=D:\Tools\Flutter\flutter\bin;%JAVA_HOME%\bin;%ANDROID_SDK_ROOT%\platform-tools;%PATH%

cd /d D:\Dev\auftragsverwaltung_flutter

echo === flutter pub get ===
flutter pub get
if errorlevel 1 (
    echo FEHLER bei pub get
    pause
    exit /b 1
)

echo.
echo === flutter build apk --release ===
flutter build apk --release
if errorlevel 1 (
    echo FEHLER beim Build
    pause
    exit /b 1
)

echo.
echo === APK kopieren nach OneDrive\dist ===
set SRC=D:\Dev\auftragsverwaltung_flutter\build\app\outputs\flutter-apk\app-release.apk
set DST=C:\Users\chris\OneDrive\Code\Auftragsverwaltung\dist\Auftragsverwaltung.apk
if not exist "%~dp0dist" mkdir "%~dp0dist"
copy /Y "%SRC%" "%DST%"

echo.
echo === Fertig ===
echo APK liegt unter: %DST%
echo Du kannst sie ueber OneDrive aufs Handy holen und installieren.
echo.
pause
