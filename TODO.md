# Auftragsverwaltung — Offene Punkte

> Persistente ToDo-Liste fuer dieses Projekt. Wird beim Wechsel ins Projekt von
> Claude gelesen. Bei Aenderungen manuell aktuell halten.

Stand: 2026-09-02

Keine offenen Punkte.

## Neu (2026-09-02): Lagermaterial-Modul

**Zweck:** Firmen-Lagerverwaltung wird selbst übernommen, weil der Lieferant
seit ~1 Jahr die Bestell-/Lagerlisten nicht mehr liefert. Ziel = eigene
Barcode-Lagerlisten erstellen und ausdrucken. UNABHÄNGIG vom auftragsbezogenen
`material` (Rapport/Lieferscheine).

- Neues Modul `lager` (opt-in, wie Verleih; Admin sieht es automatisch).
  Blueprint `routes/lager.py`, Templates `templates/lager/`, Store
  `lagerartikel.json`, Helfer in `models/repos.py`.
- Funktionen: Scannen (USB-Handscanner Keyboard-Wedge + optional Kamera via
  html5-qrcode), Artikel-Stammdaten mit Lagerort/Lieferant (Autocomplete),
  Bestand +/- Schnellaktion, Nachbestell-Flag + Mindestbestand-Schwelle.
- Datenmodell Lagerartikel: `barcode` (gescannter EAN der Verpackung, Scan-
  Schlüssel), `lieferant` (Würth / Elektro-Material AG, Autocomplete inkl.
  Standard-Lieferanten), `bestellnummer` (E-Nr bzw. Würth-Artikel-Nr = womit
  real bestellt wird), `lagerort`, `bestand`, `mindestbestand`, `bestellmenge`,
  `nachbestellen`, `einheit`, `notizen`.
- Erfassung: Chris scannt alle Artikel selbst am Regal (EAN) und ergänzt
  Lieferant + Bestellnummer + Lagerort.
- Zwei Druckansichten mit clientseitig gerenderten Barcodes (JsBarcode CODE128,
  keine Server-Dependency). **Gedruckter Barcode = gescannter EAN** (damit die
  eigene Liste wieder abscannbar ist → Artikel im eigenen System finden);
  Lieferant + Bestellnummer stehen als **Text** daneben für die reale Bestellung:
  - **Lagerliste (Katalog)** `/lager/lagerliste` — ALLE Artikel nach Lagerort,
    je Barcode + „Bestellen"-Spalte zum Aushängen. Ersatz für Lieferanten-Liste.
  - **Bestellliste** `/lager/bestellliste` — nur markierte/knappe Artikel,
    nach Lieferant, konkrete Einzelbestellung.
- Lookup (`/lager/nach-barcode`) matcht EAN UND Bestellnummer, leerzeichen-tolerant.
- Bestellliste zeigt Spalten Bestand / Mindestbestand / Bestellmenge getrennt.
- EAN-Produktsuche (`models/produktsuche.py`, Route `/lager/produktdaten`):
  Best-Effort-Lookup öffentlicher DBs (UPCitemdb Trial ohne Key; optional
  OpenGTINDB via Env `OPENGTINDB_QUERYID`) → schlägt Bezeichnung im Formular vor
  (Button + Auto-Versuch beim Anlegen). Fachhandelsartikel (Würth/EM) meist NICHT
  in öffentlichen DBs → kein Treffer ist normal, Fallback manuelle Erfassung.
  Env-Schalter `PRODUKTSUCHE_AKTIV=0` deaktiviert. Alle Netzfehler geschluckt.
- Noch offen/Ideen: CSV-Import für Erstbestückung, Bestell-Historie,
  Dashboard-Kachel „X nachzubestellen".

## Erledigt (zur Erinnerung, kann bei Bedarf geloescht werden)

- Crashguard-Rollout: `.env` auf dem Server gesetzt, Deploy-Skript nutzt das
  Playbook-Restart-Pattern (`supervisorctl -c ~/.services/supervisord/hostpoint.conf`).
- Stempelung-System, Auftrag-Status "Abgerechnet" (Archiv), Bilder-Verkleinerung,
  Zeitbuchung-Mitarbeiter-Dropdown — alle laut Code-Gegenpruefung vorhanden.
- Teams (Projektleiter/Monteur-Zuordnung fuer Auftraege + Kunden, Checkboxen,
  Mehrfachauswahl), Zeiterfassung-Wochenuebersicht, Rapport-Uebersicht mit
  Teil-/Komplettabrechnung (personenbezogen fuer Zeit, tagesbezogen), Material-
  verwaltung mit Lieferschein-Import (Elektro-Material AG), Auftrag-Detailseite
  in Zeit/Material-Unterseiten aufgeteilt.

## Architektur-Notiz

- **Eigenstaendig** — NICHT vom AdminPortal verwaltet, bewusst eigene Grund-URL `auftrage.xahizivi.myhostpoint.ch` (NICHT im z-b.tech-Cluster)
- Port 8815
- Deploy folgt dem Playbook-Pattern aus `hostpoint-flask-deploy.md` (eigene `supervisord.conf`)
- Flutter-Builds vorhanden (`build-flutter-*.bat`) → mobile Hybrid-Strategie (separates Thema, nicht Teil dieser TODO)
