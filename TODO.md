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

## Lagermaterial — Verbrauch & Bestell-Workflow (2026-11-05, deployt)

Grosse Erweiterung in dieser Session (Commits bis `c61af5e`):
- **Lieferschein-Import = Wareneingang**: PDF (Würth + EM) einlesen → Verbrauchs-
  Historie (`lagerlieferscheine.json`) UND Bestand buchen (fehlende Artikel werden
  angelegt, Lagerort zuweisbar falls leer). Würth-Parser neu in
  `models/lieferschein.py`.
- **Verbrauchs-Übersicht** `/lager/verbrauch`: Ø-Monatsverbrauch (Einkauf≈Verbrauch),
  voraussichtlicher Bestand (rechnerisch, nicht destruktiv; `bestand_stand`-Stichtag),
  „reicht noch X Tage", **Bestellvorschlag** (erst ab 2 Lieferungen), **Bestands-
  kontrolle**-Gruppe (unsicher + letzte Lieferung ≥14 Tage). EAN + Ist-Bestand
  inline editierbar, Kamera-Scan (html5-qrcode).
- Status-Felder: `kein_ean`, `ausgelaufen`, `bestellt`/`bestellt_am`. „Artikel ohne
  EAN"-Liste `/lager/ohne-ean`. „Bestellt/unterwegs" verhindert Mehrfachbestellung,
  wird beim Wareneingang auto-aufgehoben.

## Lagermaterial — Bestellvorschlag EAN-Gruppierung + Lieferzeit (2026-11-05)

- **Bestellvorschlag in zwei Gruppen**: „mit EAN (scannbar)" mit gerendertem
  CODE128-Barcode (JsBarcode) zum Scannen mit der Würth-App, und „ohne EAN
  (manuell)" mit Artikelnummer als Text (im Shop über Notebook eintippen).
- **Abhaken**: Checkbox je Zeile (+ Kopf-Checkbox „alle"), Buttons „Markierte als
  bestellt" (→ unterwegs) und „Markierte auf Bestellliste". `bestellen_markieren`
  liest die Menge jetzt per `menge_<id>` (robust gegen abgewählte Zeilen).
- **Lieferzeit pro Artikel gemessen**: beim Wareneingang / „✓ erhalten" wird
  `Lieferdatum − bestellt_am` berechnet und als `lieferzeit_historie` (+ gleitender
  Schnitt `lieferzeit_tage`) am Artikel gespeichert (`lieferzeit_update` in repos,
  0–90 Tage plausibel, letzte 10 Messungen). `_projektion` nutzt den gemessenen
  Artikel-Wert; wo keiner existiert, greift der globale Standard (Query-Param
  `lieferzeit`, Default **2** Tage = obere Grenze von „1–2 Tage je nach Bestellzeit").
  Bestellschwelle = Rhythmus + Lieferzeit (früher bestellen); Menge deckt den
  Verbrauch während der Lieferzeit mit. Anzeige je Zeile (⏱ X Tg. ✓/Std.).
- **EAN nachtragen im Vorschlag**: „ohne EAN"-Gruppe hat wieder EAN-Scan (Handscanner
  + Kamera) und „∅ kein EAN"-Button; „kein EAN" auch in der Bestandskontrolle ergänzt.
- **Artikel-Detail**: Verbrauchsübersicht 1/3/6/12 Monate (kumulativ, aus
  Lieferscheinen) + gemessene Lieferzeit (`lagerartikel_verbrauch_perioden`).
- **Reihenfolge** der Karten: Bestellvorschlag → Bestandskontrolle → Unterwegs.
- **Verbrauch = Nachbestell-Modell** (`_verbrauch_kennzahlen`): Ø-Verbrauch =
  (Gesamtmenge − Menge der **1. Lieferung**) ÷ Spanne (erste..letzte Lieferung).
  Die 1. Lieferung ist Grundbestand (z.B. nie erfasster Restbestand vor August) und
  zählt NICHT als Verbrauch; die gerade gelieferte letzte Menge bläht den Schnitt
  nicht mehr auf. Braucht automatisch ≥2 Lieferungen. `lagerlieferschein_verbrauch`
  liefert dafür `erste_menge`. Wirkt überall (Bestellvorschlag, Übersicht, Statistik).
- **Liefereinheit (LE) / LE-Zählung**: Feld `liefereinheit` (LE = Stück pro
  Bestelleinheit) pro Artikel, **manuell** gepflegt (NICHT das letzte Token der
  Würth-Artikelnummer-Zeile – das ist NICHT die Packungs-/Bestellgröße!). Ist-Bestand
  in LE zählbar (Edit-Formular JS + Bestandskontrolle `pakete`→×LE via `bestand_aendern`).
  Bestellvorschlag zeigt die Menge als **x × LE** (auf ganze LE aufgerundet, auch für
  die Bestellliste); fehlt die LE, wird sie im Vorschlag inline abgefragt
  (`liefereinheit_setzen`). Verbrauchsrate bleibt stückbasiert → LE verfälscht sie nicht.
- **Statistik** `/lager/statistik` (`lager_top_verbrauch_pro_lagerort`): Top-Verbrauch
  (Ø/Monat) je Lagerort, Zeitfenster + Top-N wählbar, Balken je Artikel. Lagerort je
  Gruppe direkt umbenennen/zusammenführen (`lagerort_umbenennen`, setzt alle Artikel
  des Orts um) mit Vorschlagsliste bestehender Orte. Hinweis: Ø/Monat kann bei kurzer
  Historie stark überhöht wirken (gleiche Schätzlogik wie Verbrauchs-Übersicht, „?"=unsicher).

**Offen / Ideen (optional, auf User-Wunsch):**
- „überfällig"-Marker für `bestellt`-Artikel ab X Tagen unterwegs.
- Dashboard-Kachel „X nachzubestellen" / Bestell-Historie (aus altem TODO).
- **User-Aufgabe (kein Code):** einmalige physische Inventur der aktiv genutzten
  Artikel — Lieferscheine erst ab Anfang August eingelesen, Restbestand davor nicht
  erfasst → gespeicherter/voraussichtlicher Bestand tendenziell zu tief, bis gezählt.
  Bohrer D3,5mm (`Spiralbohrer…D3,5MM`, Art `0625250350 961`) hat noch keinen EAN.

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
