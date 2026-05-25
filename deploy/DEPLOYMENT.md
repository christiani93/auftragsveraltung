# Deployment-Playbook — Auftragsverwaltung auf HostPoint FlexServer

Dieses Playbook dokumentiert konkret, **wie genau** die Auftragsverwaltung
auf den HostPoint FlexServer deployt wurde — inklusive aller Stolperfallen,
die wir auf dem Weg gelöst haben. Es ergänzt das allgemeine Playbook
`~/.claude/playbooks/hostpoint-flask-deploy.md` mit den projekt-spezifischen
Details (Pfade, Subdomain, Service-Name, Backup-Strategie).

> Wenn du das Setup auf einem neuen Server wiederholen willst → diese Datei
> Schritt für Schritt durcharbeiten. Wenn dich nur eine bestimmte Operation
> interessiert (Restart, Backup einspielen, neue Subdomain) → Inhaltsverzeichnis.

---

## Inhalt

1. [Eckdaten & Architektur](#1-eckdaten--architektur)
2. [Server-Erstinstallation](#2-server-erstinstallation)
3. [SSH-Zugang von Windows aus](#3-ssh-zugang-von-windows-aus)
4. [Erstes Deployment](#4-erstes-deployment)
5. [Subdomain + Reverse-Proxy einrichten](#5-subdomain--reverse-proxy-einrichten)
6. [supervisord-Service](#6-supervisord-service)
7. [Backup einrichten](#7-backup-einrichten)
8. [Updates deployen](#8-updates-deployen)
9. [Troubleshooting / Known Quirks](#9-troubleshooting--known-quirks)
10. [Disaster-Recovery](#10-disaster-recovery)

---

## 1. Eckdaten & Architektur

| Schlüssel              | Wert                                                    |
|------------------------|---------------------------------------------------------|
| Hoster                 | HostPoint FlexServer (Schweiz)                          |
| SSH-Account            | `xahizivi@xahizivi.myhostpoint.ch`                      |
| Subdomain (Production) | `auftrage.xahizivi.myhostpoint.ch` (Auto-SSL)           |
| Service-Name           | `auftragsverwaltung` (supervisord)                      |
| App-Code               | `~/apps/auftragsverwaltung/` (Git-Repo)                 |
| Daten                  | `~/apps/auftragsverwaltung_data/` (JSON + Bilder)       |
| Backups                | `~/apps/auftragsverwaltung_backups/` (tar.gz, 30 Tage)  |
| Repo                   | `https://github.com/christiani93/auftragsveraltung.git` |
| Internal-Port          | `127.0.0.1:8815` (Gunicorn, hinter HostPoint-Reverse-Proxy) |

Architekturskizze:

```
 Internet (HTTPS)
        │
        ▼
 HostPoint Reverse-Proxy + Auto-SSL
 (auftrage.xahizivi.myhostpoint.ch)
        │
        ▼  127.0.0.1:8815
 Gunicorn (2-4 gthread-Workers)
        │
        ▼
 Flask-App (create_app)
        │
        ▼
 JSON-Files + Bilder in ~/apps/auftragsverwaltung_data/
```

Warum supervisord + nicht systemd? Auf HostPoint FlexServer hat der User keinen
`sudo`-Zugriff. HostPoint bietet stattdessen `hpservices` als User-Mode-Service-
Manager, der intern supervisord verwendet. Config liegt unter
`~/.services/supervisord/hostpoint.conf` (wichtig für den `-c`-Flag!).

---

## 2. Server-Erstinstallation

**Vorausgesetzt:** FlexServer ist gebucht, SSH-Zugang im HostPoint-Panel
aktiviert (eigenen Public-Key hochgeladen), User-Login funktioniert.

```bash
# Auf dem Server: Basisstruktur anlegen
mkdir -p ~/apps ~/apps/auftragsverwaltung_data ~/apps/auftragsverwaltung_backups

# Python-Version prüfen (HostPoint hat ggf. python3.11 als Default)
python3 --version    # idealerweise >= 3.10

# hpservices supervisord initialisieren (falls noch nicht passiert)
hpservices supervisord init
# → erzeugt ~/.services/supervisord/hostpoint.conf + startet den User-supervisord
```

Wenn `hpservices` noch nie aufgerufen wurde, ist die Config noch nicht da —
deshalb **vor** dem Service-Anlegen einmal `init` ausführen.

---

## 3. SSH-Zugang von Windows aus

### Quirk: Korrupte SSH-MACs im Schweizer Netz

Standard-SSH-Verbindung Windows ↔ HostPoint scheitert mit:

```
client_input_packet: Corrupted MAC on input
ssh_dispatch_run_fatal: Connection to ... port 22: message authentication code incorrect
```

**Ursache:** Manche Schweizer ISPs (vermutlich Stateful-Inspection) zerschießen
EtM-MACs bei längeren Paketen. **Fix:** alte E&M-MACs erzwingen.

### .claude/ssh_config (projekt-lokal, gitignored)

```ssh
Host hostpoint
    HostName xahizivi.myhostpoint.ch
    User xahizivi
    Port 22
    IdentityFile .claude/hostpoint_deploy
    IdentitiesOnly yes
    MACs hmac-sha2-256,hmac-sha2-512
    ServerAliveInterval 30
    ServerAliveCountMax 6
```

**Aufruf:**

```bash
ssh -F .claude/ssh_config hostpoint "uptime"
```

### Permission-Rule für Claude

In `.claude/settings.json` muss Claude den SSH-Befehl ohne Rückfrage ausführen
dürfen. Folgende Allow-Rules sind eingetragen (Auto-Mode würde sonst blocken):

```json
{
  "permissions": {
    "allow": [
      "Bash(ssh -F .claude/ssh_config hostpoint *)",
      "Bash(ssh -F .claude/ssh_config hostpoint:*)"
    ]
  }
}
```

Die `.claude/settings.json` kann der Auto-Mode-Classifier **nicht** selbst
ändern (Self-Modification-Block) — du musst sie manuell anlegen.

---

## 4. Erstes Deployment

```bash
# Auf dem Server:
cd ~/apps
git clone https://github.com/christiani93/auftragsveraltung.git auftragsverwaltung
cd auftragsverwaltung

# Venv + Dependencies
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# Deploy-Scripts ausführbar machen
chmod +x deploy/*.sh

# Env-Konfig anlegen — KEIN git, manuell auf Server!
cp deploy/.env.example .env
nano .env
```

### .env auf dem Server

```bash
# Pflicht
AUFTRAGSVERWALTUNG_SECRET_KEY=<32-byte-hex, einmal generieren: python -c "import secrets; print(secrets.token_hex(32))">
AUFTRAGSVERWALTUNG_ADMIN_USER=admin
AUFTRAGSVERWALTUNG_ADMIN_PASSWORD=<starkes Initial-Passwort>
AUFTRAGSVERWALTUNG_HTTPS_ONLY=1

# Optional (Defaults siehe deploy/.env.example)
AUFTRAGSVERWALTUNG_DATA_DIR=/home/xahizivi/apps/auftragsverwaltung_data
AUFTRAGSVERWALTUNG_FIRMA="Christian Iannicelli"     # ← Werte mit Leerzeichen IMMER quoten!
AUFTRAGSVERWALTUNG_BIND=127.0.0.1:8815
```

> **Quirk:** Werte mit Leerzeichen ohne Quotes ergeben in `.env` einen
> Bash-Fehler beim Laden in `deploy/run.sh` (`Bern-Mittelland: command not found`).
> → Immer Anführungszeichen verwenden.

### Smoke-Test (vor supervisord)

```bash
./deploy/run.sh
# → läuft im Foreground. http://localhost:8815 ansurfen (lokal) bzw. SSH-Tunnel.
# Strg+C zum Beenden.
```

Wenn die App hier sauber startet → weiter mit Service-Einrichtung.

---

## 5. Subdomain + Reverse-Proxy einrichten

Im HostPoint-Kundenpanel:

1. **Domains → Subdomain hinzufügen:** `auftrage.xahizivi.myhostpoint.ch`
2. **SSL aktivieren** (HostPoint macht Let's-Encrypt automatisch).
3. **Reverse-Proxy aktivieren** für die Subdomain mit Ziel `http://127.0.0.1:8815`
   (oder welcher Port in `.env` als `AUFTRAGSVERWALTUNG_BIND` steht).
4. Setting `X-Forwarded-Proto` muss vom Proxy gesetzt werden — Flask vertraut
   dem via `AUFTRAGSVERWALTUNG_HTTPS_ONLY=1` (setzt `SESSION_COOKIE_SECURE`).

---

## 6. supervisord-Service

```bash
# Service anlegen
hpservices supervisord add auftragsverwaltung

# Service-Config bearbeiten (HostPoint legt nur eine Stub-Datei an)
nano ~/.services/supervisord/auftragsverwaltung/service.conf
# → Inhalt aus deploy/supervisor.conf reinkopieren (siehe Repo)

# supervisord neu laden
supervisorctl -c ~/.services/supervisord/hostpoint.conf update

# Status prüfen
supervisorctl -c ~/.services/supervisord/hostpoint.conf status auftragsverwaltung
# → sollte RUNNING zeigen

# Logs anschauen bei Problemen
tail -f ~/apps/auftragsverwaltung/logs/stdout.log
tail -f ~/apps/auftragsverwaltung/logs/stderr.log
```

### Quirk: supervisorctl ohne `-c` schlägt fehl

Wenn du `supervisorctl restart auftragsverwaltung` ohne den `-c`-Flag laufen
lässt, bekommst du:

```
unix:///var/run/supervisor/supervisor.sock no such file
```

Das System-supervisord ist nicht installiert (kein sudo) — wir verwenden das
User-Mode-supervisord von HostPoint, dessen Socket woanders liegt. Lösung:
**immer den Pfad zur Config angeben.** Genau das macht `deploy/update.sh` für
dich automatisch.

---

## 7. Backup einrichten

```bash
# Auf dem Server, einmalig:
./deploy/install-cron.sh
# → richtet Cron-Job ein: täglich um 03:00 wird DATA_DIR als tar.gz gesichert,
#   Backups älter als 30 Tage werden gelöscht.

# Verifizieren:
crontab -l
# → enthält:  0 3 * * * /home/xahizivi/apps/auftragsverwaltung/deploy/backup.sh >/dev/null 2>&1

# Manuell ein Backup testen:
./deploy/backup.sh
ls -lh ~/apps/auftragsverwaltung_backups/
```

Das tar.gz packt **das gesamte DATA_DIR** ein — inklusive `messprotokolle.json`,
`auftraege.json`, `kunden.json`, **und** den Bilder-Ordner `auftrag_bilder/`.

---

## 8. Updates deployen

### Von Windows aus (Standard-Workflow)

```bash
# Lokal
git add . && git commit -m "..."
git push

# Auf den Server (Claude macht das selbständig)
ssh -F .claude/ssh_config hostpoint "cd apps/auftragsverwaltung && ./deploy/update.sh"
```

`deploy/update.sh` macht:

1. `git fetch + git reset --hard origin/main` (kein `git pull`, weil lokale
   Änderungen auf dem Server sonst mergen würden → diese verwerfen wir bewusst,
   der Server ist eine **Kopie**, kein Arbeitsplatz)
2. `pip install -r requirements.txt` (idempotent, schnell wenn nichts neues)
3. `supervisorctl -c ~/.services/supervisord/hostpoint.conf restart auftragsverwaltung`

Wenn der Restart fehlschlägt → in `~/apps/auftragsverwaltung/logs/stderr.log`
schauen. Häufigste Ursachen sind Tippfehler in `.env`, fehlende Dependency,
oder ein Syntax-Fehler im neuen Code.

---

## 9. Troubleshooting / Known Quirks

| Symptom                                       | Ursache                                       | Fix                                                |
|-----------------------------------------------|-----------------------------------------------|----------------------------------------------------|
| `Corrupted MAC on input` beim SSH             | Schweizer ISP zerschießt EtM-MACs             | `MACs hmac-sha2-256,hmac-sha2-512` in ssh_config   |
| `unix:///var/run/supervisor/...` not found    | System-supervisord nicht da, kein sudo        | `supervisorctl -c ~/.services/supervisord/hostpoint.conf ...` |
| `.env: Bern-Mittelland: command not found`    | Wert mit Leerzeichen nicht gequotet           | In `.env` **alle** Werte mit Leerzeichen quoten    |
| `Your local changes would be overwritten`     | `git pull` auf Server fand untracked changes  | `update.sh` nutzt jetzt `git reset --hard`         |
| Login klappt lokal, scheitert auf Server      | `SESSION_COOKIE_SECURE=1` aber HTTP statt HTTPS | `AUFTRAGSVERWALTUNG_HTTPS_ONLY=1` nur setzen wenn Reverse-Proxy SSL macht |
| `Could not resolve hostname github.com-...`   | falscher SSH-Alias verwendet                  | Klon-URL ist `github.com`, nicht ein selbst erstellter Alias |
| PDF-Export bricht ab                          | WeasyPrint braucht System-Libs (Cairo, Pango) | HostPoint hat diese vorinstalliert; sonst `apt install libpango-1.0-0 libpangoft2-1.0-0` |
| Service startet kurz, beendet sich wieder     | gunicorn crasht beim Boot (siehe stderr.log)  | Meist Python-ImportError oder `.env`-Problem       |
| Bilder-Upload schlägt mit 413 fehl            | Nginx-Limit oder MAX_CONTENT_LENGTH zu klein  | App: 60 MB; HostPoint-Proxy: prüfen ob client_max_body_size das überdeckt |

---

## 10. Disaster-Recovery

### Daten verloren / kaputt → Backup einspielen

```bash
# Auf dem Server:
ls -lh ~/apps/auftragsverwaltung_backups/
# Backup-Datei aussuchen, dann:
./deploy/restore.sh ~/apps/auftragsverwaltung_backups/auftragsverwaltung_data_2026-05-24_030000.tar.gz
# → fragt nochmal nach Bestätigung
# → packt vorher die aktuellen (kaputten) Daten als Sicherheitskopie weg
# → entpackt das Backup in DATA_DIR
# → restartet den Service
```

### Komplett neu aufsetzen (Server-Verlust)

1. Server neu provisionieren, SSH einrichten
2. Schritte 2–7 dieses Playbooks durchgehen
3. Letztes lokales Backup (oder von einem anderen Backup-Standort) auf den
   Server kopieren: `scp -F .claude/ssh_config <backup>.tar.gz hostpoint:apps/auftragsverwaltung_backups/`
4. `./deploy/restore.sh ~/apps/auftragsverwaltung_backups/<backup>.tar.gz`

### App will gar nicht mehr starten

```bash
# Status checken
supervisorctl -c ~/.services/supervisord/hostpoint.conf status auftragsverwaltung

# Letzte Fehler
tail -50 ~/apps/auftragsverwaltung/logs/stderr.log

# Manuell starten um Fehler im Klartext zu sehen
cd ~/apps/auftragsverwaltung
./deploy/run.sh
# (Strg+C wenn fertig — dann supervisord wieder übernehmen lassen)
```

---

## Anhang: Wichtige Dateien im Repo

| Datei                              | Zweck                                                  |
|------------------------------------|--------------------------------------------------------|
| `deploy/run.sh`                    | Wrapper: lädt .env, exec'd Gunicorn                    |
| `deploy/gunicorn.conf.py`          | Gunicorn-Config (Workers, Timeouts, Bind)              |
| `deploy/supervisor.conf`           | Template für supervisord-Service-Config                |
| `deploy/update.sh`                 | Git-pull + venv-Refresh + Service-Restart              |
| `deploy/backup.sh`                 | tar.gz-Backup mit 30-Tage-Rotation                     |
| `deploy/restore.sh`                | Backup einspielen + Sicherheitskopie der alten Daten   |
| `deploy/install-cron.sh`           | Backup-Cron-Job einrichten (idempotent)                |
| `deploy/.env.example`              | Template für `.env` (NIE in git: echte .env ignorieren) |
| `.claude/ssh_config`               | SSH-Config inkl. MAC-Override (gitignored)             |
| `.claude/hostpoint_deploy`         | SSH-Privatekey für Claude-Deploys (gitignored)         |
| `.gitignore`                       | Schließt `.env`, `.claude/`, `data/`, `logs/` aus      |

**Niemals committen:** `.env`, alles in `.claude/`, `data/`.
