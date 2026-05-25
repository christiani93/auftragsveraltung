"""Konfiguration der Auftragsverwaltung.

Der Datenordner liegt standardmässig direkt im Projektverzeichnis (\\data),
das im OneDrive-Ordner liegt — so synchronisiert OneDrive die JSON-Dateien
automatisch auf alle Geräte.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Bei PyInstaller-Bundle (--onefile) liegen die entpackten Ressourcen in
# sys._MEIPASS, das Exe selbst aber bei sys.executable. Daten gehören neben
# das Exe, nicht in den Temp-Ordner.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
else:
    BASE_DIR = Path(__file__).resolve().parent
    BUNDLE_DIR = BASE_DIR

DATA_DIR = Path(os.environ.get("AUFTRAGSVERWALTUNG_DATA_DIR", BASE_DIR / "data"))

HOST = os.environ.get("AUFTRAGSVERWALTUNG_HOST", "0.0.0.0")
PORT = int(os.environ.get("AUFTRAGSVERWALTUNG_PORT", "5000"))
DEBUG = os.environ.get("AUFTRAGSVERWALTUNG_DEBUG", "1") == "1"

FIRMA_NAME = os.environ.get("AUFTRAGSVERWALTUNG_FIRMA", "")

KONTROLL_INTERVALL_MONATE_DEFAULT = 6
