#!/bin/bash
# Taegliches Backup der Auftragsverwaltung-Daten.
# Wird per Cron-Job aufgerufen, siehe deploy/install-cron.sh
#
# Manuell aufrufen:
#   ~/apps/auftragsverwaltung/deploy/backup.sh

set -euo pipefail

# Konfiguration
APP_DIR="${APP_DIR:-$HOME/apps/auftragsverwaltung}"
DATA_DIR="${AUFTRAGSVERWALTUNG_DATA_DIR:-$HOME/apps/auftragsverwaltung_data}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/apps/auftragsverwaltung_backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

LOG_DIR="$APP_DIR/logs"
LOG_FILE="$LOG_DIR/backup.log"

mkdir -p "$BACKUP_DIR" "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "=== Backup-Start ==="
log "Quelle: $DATA_DIR"
log "Ziel:   $BACKUP_DIR"

if [ ! -d "$DATA_DIR" ]; then
    log "FEHLER: Datenverzeichnis fehlt: $DATA_DIR"
    exit 1
fi

# Backup-Dateiname mit Datum/Uhrzeit
STAMP=$(date '+%Y-%m-%d_%H%M%S')
ARCHIVE="$BACKUP_DIR/auftragsverwaltung_data_${STAMP}.tar.gz"

# Archiv erstellen — nur JSON-Files, keine temp-Dateien oder Locks
tar -czf "$ARCHIVE" \
    --exclude="*.tmp" \
    --exclude=".secret_key" \
    -C "$(dirname "$DATA_DIR")" \
    "$(basename "$DATA_DIR")"

SIZE=$(du -h "$ARCHIVE" | cut -f1)
log "Backup erstellt: $(basename "$ARCHIVE") ($SIZE)"

# Alte Backups aufraeumen — aelter als RETENTION_DAYS Tage
DELETED=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name "auftragsverwaltung_data_*.tar.gz" -mtime "+$RETENTION_DAYS" -print -delete | wc -l)
if [ "$DELETED" -gt 0 ]; then
    log "$DELETED alte Backup(s) geloescht (aelter als $RETENTION_DAYS Tage)"
fi

# Statistik: wieviele Backups noch da
COUNT=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name "auftragsverwaltung_data_*.tar.gz" | wc -l)
log "Aktuell $COUNT Backup-Archiv(e) gespeichert"

log "=== Backup-Ende ==="
