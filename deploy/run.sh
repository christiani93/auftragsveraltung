#!/bin/bash
# Wrapper-Script fuer supervisord (HostPoint hpservices):
# - laedt Env-Vars aus .env
# - exec'd gunicorn (process replacement, damit supervisord den richtigen PID kennt)
set -e
cd "$(dirname "$0")/.."

# .env laden falls vorhanden
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . .env
    set +a
fi

# Default-Datenordner falls in .env nicht gesetzt
: "${AUFTRAGSVERWALTUNG_DATA_DIR:=$HOME/apps/auftragsverwaltung_data}"
export AUFTRAGSVERWALTUNG_DATA_DIR

mkdir -p "$AUFTRAGSVERWALTUNG_DATA_DIR"

exec .venv/bin/gunicorn -c deploy/gunicorn.conf.py "app:create_app()"
