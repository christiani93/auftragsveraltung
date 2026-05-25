#!/usr/bin/env bash
# Aktualisiert die Auftragsverwaltung auf dem Server: git pull + venv-Refresh + Service-Restart.
# Aufruf: ./deploy/update.sh

set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== Sync mit origin/main (force) ==="
git fetch origin
git reset --hard origin/main
chmod +x deploy/*.sh 2>/dev/null || true

echo "=== Venv-Pruefung ==="
if [ ! -d ".venv" ]; then
    echo "Lege .venv an..."
    python3 -m venv .venv
fi
# shellcheck source=/dev/null
. .venv/bin/activate

echo "=== Pip-Upgrade + Requirements ==="
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

echo "=== Service neu starten ==="
# HostPoint FlexServer: supervisord-Config liegt unter ~/.services/supervisord/
HOSTPOINT_SUPERVISORD_CONF="$HOME/.services/supervisord/hostpoint.conf"
if [ -f "$HOSTPOINT_SUPERVISORD_CONF" ]; then
    supervisorctl -c "$HOSTPOINT_SUPERVISORD_CONF" restart auftragsverwaltung
    sleep 1
    supervisorctl -c "$HOSTPOINT_SUPERVISORD_CONF" status auftragsverwaltung
elif command -v systemctl >/dev/null && systemctl is-enabled auftragsverwaltung >/dev/null 2>&1; then
    sudo systemctl restart auftragsverwaltung
    sleep 1
    sudo systemctl status auftragsverwaltung --no-pager | head -10
elif command -v supervisorctl >/dev/null 2>&1; then
    # Generischer Fallback (interaktiver Login-Shell-Mechanismus)
    bash -lc 'supervisorctl restart auftragsverwaltung' || \
        echo "Hinweis: Restart fehlgeschlagen. Manuell pruefen."
else
    echo "Kein bekannter Service-Manager gefunden — bitte manuell neu starten."
fi

echo
echo "Done."
