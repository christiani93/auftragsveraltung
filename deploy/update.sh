#!/usr/bin/env bash
# Aktualisiert die Auftragsverwaltung auf dem Server: git pull + venv-Refresh + Service-Restart.
# Aufruf: ./deploy/update.sh

set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== git pull ==="
git pull --ff-only

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
if command -v systemctl >/dev/null && systemctl is-enabled auftragsverwaltung >/dev/null 2>&1; then
    sudo systemctl restart auftragsverwaltung
    sleep 1
    sudo systemctl status auftragsverwaltung --no-pager | head -10
else
    echo "systemctl nicht da oder Service nicht enabled — bitte manuell neu starten."
fi

echo
echo "Done."
