#!/bin/bash
# Richtet den Cron-Job fuer das taegliche Backup ein.
# Aufruf einmalig: ./deploy/install-cron.sh
# Re-Aufruf ist sicher: idempotent (legt nicht doppelt an).

set -euo pipefail

BACKUP_CMD="$HOME/apps/auftragsverwaltung/deploy/backup.sh"
CRON_LINE="0 3 * * * $BACKUP_CMD >/dev/null 2>&1"

if ! crontab -l 2>/dev/null | grep -Fq "$BACKUP_CMD"; then
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    echo "Cron-Job angelegt: taeglich um 03:00"
    echo "  $CRON_LINE"
else
    echo "Cron-Job bereits vorhanden — nichts zu tun."
fi

echo
echo "Aktuelle Crontab:"
crontab -l 2>/dev/null | grep -v '^#' | grep -v '^$' || echo "(leer)"
