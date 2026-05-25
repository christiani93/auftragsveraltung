#!/bin/bash
# Stellt ein Backup wieder her.
# Aufruf: ./deploy/restore.sh <backup-datei.tar.gz>
# Beispiel: ./deploy/restore.sh ~/apps/auftragsverwaltung_backups/auftragsverwaltung_data_2026-05-25_030000.tar.gz

set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Aufruf: $0 <backup-datei.tar.gz>"
    echo ""
    echo "Verfuegbare Backups:"
    ls -lh "${HOME}/apps/auftragsverwaltung_backups/" 2>/dev/null || echo "(keine vorhanden)"
    exit 1
fi

ARCHIVE="$1"
if [ ! -f "$ARCHIVE" ]; then
    echo "FEHLER: Backup-Datei nicht gefunden: $ARCHIVE"
    exit 1
fi

DATA_DIR="${AUFTRAGSVERWALTUNG_DATA_DIR:-$HOME/apps/auftragsverwaltung_data}"
PARENT="$(dirname "$DATA_DIR")"

echo "ACHTUNG: Aktuelle Daten in $DATA_DIR werden ueberschrieben!"
echo "Backup-Datei: $ARCHIVE"
read -p "Wirklich fortfahren? (ja/nein) " ANSWER
[ "$ANSWER" = "ja" ] || { echo "Abgebrochen."; exit 1; }

# Aktuelle Daten zur Sicherheit nochmal kurz wegsichern
SAFETY="$HOME/apps/auftragsverwaltung_backups/before_restore_$(date '+%Y-%m-%d_%H%M%S').tar.gz"
if [ -d "$DATA_DIR" ]; then
    tar -czf "$SAFETY" -C "$PARENT" "$(basename "$DATA_DIR")"
    echo "Sicherheitskopie der aktuellen Daten: $SAFETY"
    rm -rf "$DATA_DIR"
fi

# Restore
tar -xzf "$ARCHIVE" -C "$PARENT"
echo "Wiederherstellung erfolgreich: $DATA_DIR"

# Service neu starten (falls supervisord laeuft)
if command -v supervisorctl >/dev/null 2>&1; then
    supervisorctl restart auftragsverwaltung 2>&1 || echo "Service-Restart hat nicht geklappt — manuell pruefen."
fi
