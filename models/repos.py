"""Konkrete Repositories und Domain-Logik (Schweizer NIN/NIV-Vokabular).

Datenmodell-Übersicht
---------------------
Kunde
    id, name, adresse, plz, ort, telefon, email, ist_stammkunde,
    kontroll_intervall_monate, notizen

Anlage (gehört zu einem Kunden)
    id, kunde_id, bezeichnung, standort, baujahr,
    naechste_periodische_kontrolle (Datum), notizen

Anlagenteil (gehört zu einer Anlage)
    id, anlage_id, parent_id (optional: übergeordnete Verteilung),
    typ ("Verteilung" | "Stromkreis" | ...), bezeichnung, beschreibung,
    spannung ("230V" | "400V"), leistung_kw, stromstaerke_a,
    gemessen_ik_a (Kurzschlussstrom an diesem Punkt, falls gemessen),
    kontroll_status ("offen" | "geprueft" | "maengel"),
    letzte_kontrolle (Datum), kontrolleur, notizen

Messgerät
    id, bezeichnung, hersteller, modell, seriennr, typ,
    kalibrierdatum, naechste_kalibrierung, notizen,
    owner (Username des Besitzers — User sieht nur eigene, Admin sieht alle)

Messprotokoll (gehört zu einer Anlage, optional zu einem Anlagenteil)
    id, anlage_id, anlagenteil_id, datum, monteur,
    messgeraet_id (verweist auf Messgerät-Stammdaten),
    bemerkungen, messungen: Liste von Messpunkten
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from .storage import JsonStore


# ----- Repositories -----------------------------------------------------------

kunden = JsonStore("kunden.json")
anlagen = JsonStore("anlagen.json")
anlagenteile = JsonStore("anlagenteile.json")
messprotokolle = JsonStore("messprotokolle.json")
messgeraete = JsonStore("messgeraete.json")
auftraege = JsonStore("auftraege.json")
zeitbuchungen = JsonStore("zeitbuchungen.json")
stempelungen = JsonStore("stempelung.json")
revisionen = JsonStore("revisionen.json")
leistungsschalter = JsonStore("leistungsschalter.json")
benachrichtigungen = JsonStore("benachrichtigungen.json")
push_subscriptions = JsonStore("push_subscriptions.json")
mietmaschinen = JsonStore("mietmaschinen.json")
mieter = JsonStore("mieter.json")

VERMIET_STATUS = ["verfuegbar", "ausgeliehen", "wartung"]
VERMIET_STATUS_LABEL = {
    "verfuegbar": "Verfügbar",
    "ausgeliehen": "Ausgeliehen",
    "wartung": "Nicht verfügbar",
}


def mieter_sortiert() -> List[Dict[str, Any]]:
    return sorted(mieter.list(), key=lambda m: (m.get("name", "").lower()))


# ----- Konstanten -------------------------------------------------------------

ANLAGENTEIL_TYPEN = [
    "Verteilung",
    "Verteilstromkreis",
    "Anlagenstromkreis",
    "Endstromkreis mit Steckdosen",
    "Endstromkreis ohne Steckdosen",
    "Erzeugerstromkreis",
]

# Diese Typen können andere Anlagenteile als Kinder haben (sind also Pfad-Elemente).
ANLAGENTEIL_TYP_KANN_PARENT = {
    "Verteilung",
    "Verteilstromkreis",
    "Anlagenstromkreis",
}

# Nur diese Typen zählen mit ihrer eigenen Eingabe als echte Last in der
# "Summe nachgelagert". Verteilungen/Verteilstromkreise sind reine Verteil-
# punkte (ihre eigene Eingabe ist nur eine Bemessung, keine Last) und
# Erzeugerstromkreise speisen ein — sie werden NICHT als Last gezählt.
ANLAGENTEIL_TYP_IST_LAST = {
    "Anlagenstromkreis",
    "Endstromkreis mit Steckdosen",
    "Endstromkreis ohne Steckdosen",
}

# Verteiler-Knoten: nie ein "Endpunkt" — sie verteilen weiter und zeigen daher
# immer die nachgelagerte Summe (auch wenn noch nichts darunter erfasst ist).
ANLAGENTEIL_TYP_VERTEILER = {
    "Verteilung",
    "Verteilstromkreis",
}


def fi_erforderlich(typ: Optional[str]) -> Optional[bool]:
    """Ist für diesen Anlagenteil-Typ nach NIN ein FI (RCD) vorgeschrieben?

    True  : FI-Pflicht (Endstromkreis mit Steckdosen)
    False : kein FI nötig (Endstromkreis ohne Steckdosen)
    None  : nicht eindeutig / abhängig vom konkreten Fall (Verteilungen etc.)
    """
    if typ == "Endstromkreis mit Steckdosen":
        return True
    if typ == "Endstromkreis ohne Steckdosen":
        return False
    return None

KONTROLL_STATUS = ["offen", "geprueft", "maengel"]
KONTROLL_STATUS_LABEL = {
    "offen": "Offen",
    "geprueft": "Geprüft",
    "maengel": "Mängel",
}

SPANNUNG_TYPEN = [
    ("230V", "230 V (1 Aussenleiter + N)"),
    ("400V", "400 V (3 Aussenleiter + N)"),
]
SPANNUNG_LABEL = {key: label for key, label in SPANNUNG_TYPEN}
SPANNUNG_VOLT = {"230V": 230.0, "400V": 400.0}

UNPRODUKTIV_KATEGORIEN = [
    "Büro / Administration",
    "Werkstatt / Lager",
    "Anfahrt / Rückfahrt",
    "Wartezeit",
    "Reinigung / Aufräumen",
    "Weiterbildung",
    "Sonstiges",
]

AUFTRAG_STATUS = ["offen", "in_arbeit", "erledigt", "abgerechnet"]
AUFTRAG_STATUS_LABEL = {
    "offen": "Offen",
    "in_arbeit": "In Arbeit",
    "erledigt": "Erledigt",
    "abgerechnet": "Abgerechnet",
}
# 'abgerechnet' = archiviert, standardmäßig in der Liste ausgeblendet
AUFTRAG_STATUS_ARCHIVIERT = {"abgerechnet"}

REVISION_STATUS = ["geplant", "laeuft", "abgeschlossen"]
REVISION_STATUS_LABEL = {
    "geplant": "Geplant",
    "laeuft": "Läuft",
    "abgeschlossen": "Abgeschlossen",
}

# Felder eines Messpunkts — orientiert am gewohnten NIN-Messprotokoll-Template.
# Spaltenstruktur: Datum | Installation | Kabel | Sicherung | Schutzorgan |
#                  Prüfungen obligatorisch | Prüfungen fakultativ | Prüfer
#
# `input` steuert das Eingabefeld: "text" = Freitext, "io" = i.O./n.i.O.-Auswahl
# `gruppe` ist nur für die Gruppen-Header in der Tabelle relevant.
MESSPUNKT_FELDER = [
    {"name": "datum",          "label": "Datum",                  "input": "text", "gruppe": "Datum",        "placeholder": "tt.mm.jjjj", "width": "100"},
    {"name": "installation",   "label": "Installation / Ort",     "input": "text", "gruppe": "Installation", "placeholder": "z.B. Bel. Komp. Raum", "width": "200"},
    {"name": "kabel",          "label": "Kabeltyp / Querschnitt", "input": "text", "gruppe": "Kabel",        "placeholder": "z.B. 3x1.5mm²", "width": "130"},
    {"name": "sicherungsnr",   "label": "Sicherungsnr.",          "input": "text", "gruppe": "Sicherung",    "placeholder": "z.B. F11", "width": "90"},
    {"name": "sicherungstyp",  "label": "LS / NH etc.",           "input": "text", "gruppe": "Schutzorgan",  "placeholder": "z.B. FI-LS", "width": "100"},
    {"name": "charakteristik", "label": "Char. [B/C/D]",          "input": "text", "gruppe": "Schutzorgan",  "placeholder": "z.B. C", "width": "70"},
    {"name": "sicherung_a",    "label": "Nennstrom [A]",          "input": "text", "gruppe": "Schutzorgan",  "placeholder": "z.B. 16", "width": "90"},
    {"name": "fi_typ_ma",      "label": "FI Typ / I∆n [mA]",      "input": "text", "gruppe": "Schutzorgan",  "placeholder": "z.B. 30mA", "width": "100"},
    {"name": "sichtkontrolle", "label": "Sichtkontrolle",         "input": "io",   "gruppe": "Obligatorisch", "width": "90"},
    {"name": "schutzleiter",   "label": "Schutzleiter OK?",       "input": "io",   "gruppe": "Obligatorisch", "width": "90"},
    {"name": "ausloesezeit_ms","label": "Auslösezeit [ms]",       "input": "text", "gruppe": "Obligatorisch", "placeholder": "z.B. 45", "width": "90"},
    {"name": "r_iso_mohm",     "label": "R Iso [MΩ]",             "input": "text", "gruppe": "Fakultativ",   "placeholder": "z.B. 843", "width": "90"},
    {"name": "ik_ende_a",      "label": "Ik Ende L-PE/L-N [A]",   "input": "text", "gruppe": "Fakultativ",   "placeholder": "wenn FI: L-N", "width": "110"},
    {"name": "drehrichtung",   "label": "Drehfeld",               "input": "drehfeld", "gruppe": "Fakultativ", "width": "100"},
    {"name": "pruefer",        "label": "Prüfer",                 "input": "text", "gruppe": "Unterschrift", "placeholder": "Name", "width": "140"},
    {"name": "bemerkung",      "label": "Bemerkung",              "input": "text", "gruppe": "Unterschrift", "width": "150"},
]

IO_OPTIONEN = ["", "i.O.", "n.i.O."]
DREHFELD_OPTIONEN = ["", "Links", "Rechts"]

# Auswahl-Optionen je Eingabe-Typ eines Messpunkt-Feldes (input == ...).
# Felder ohne Eintrag hier sind Freitext.
SELECT_OPTIONEN = {"io": IO_OPTIONEN, "drehfeld": DREHFELD_OPTIONEN}


# ----- Hilfsfunktionen --------------------------------------------------------

def parse_iso_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return date.fromisoformat(value)


def format_date_ch(value: Optional[str]) -> str:
    """Datum im Schweizer Format DD.MM.YYYY ausgeben."""
    d = parse_iso_date(value)
    return d.strftime("%d.%m.%Y") if d else ""


def anlagen_fuer_kunde(kunde_id: str) -> List[Dict[str, Any]]:
    return anlagen.filter(kunde_id=kunde_id)


# ----- Benachrichtigungen (In-App) -------------------------------------------

def benachrichtigung_erstellen(user: str, text: str, auftrag_id: str = "",
                               von: str = "", typ: str = "auftrag_zugewiesen") -> Optional[Dict[str, Any]]:
    """Legt eine Benachrichtigung fuer einen User an. user leer -> nichts."""
    if not user:
        return None
    return benachrichtigungen.create({
        "user": user,
        "typ": typ,
        "text": text,
        "auftrag_id": auftrag_id or "",
        "von": von or "",
        "gelesen": False,
        "erstellt_am": datetime.now().isoformat(timespec="seconds"),
    })


def benachrichtigungen_fuer(username: str) -> List[Dict[str, Any]]:
    eintraege = [b for b in benachrichtigungen.list() if b.get("user") == username]
    eintraege.sort(key=lambda b: b.get("erstellt_am", ""), reverse=True)
    return eintraege


def anzahl_ungelesen(username: str) -> int:
    return sum(1 for b in benachrichtigungen.list()
               if b.get("user") == username and not b.get("gelesen"))


# ----- Push-Subscriptions (Web Push) -----------------------------------------

def push_subscription_speichern(user: str, subscription: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Speichert eine Web-Push-Subscription fuer einen User (dedupe per endpoint)."""
    endpoint = (subscription or {}).get("endpoint")
    if not user or not endpoint:
        return None
    for s in push_subscriptions.list():
        if s.get("endpoint") == endpoint:
            return push_subscriptions.update(s["id"], {"user": user, "subscription": subscription})
    return push_subscriptions.create({
        "user": user,
        "endpoint": endpoint,
        "subscription": subscription,
        "erstellt_am": datetime.now().isoformat(timespec="seconds"),
    })


def push_subscription_entfernen(endpoint: str) -> None:
    for s in push_subscriptions.list():
        if s.get("endpoint") == endpoint:
            push_subscriptions.delete(s["id"])


def push_subscriptions_fuer(username: str) -> List[Dict[str, Any]]:
    return [s for s in push_subscriptions.list() if s.get("user") == username]


# ----- Leistungsschalter-Wartung (Intervall, Default 5 Jahre) -----------------

WARTUNG_INTERVALL_JAHRE_DEFAULT = 5


def _plus_jahre(d: date, jahre: int) -> date:
    try:
        return d.replace(year=d.year + jahre)
    except ValueError:  # 29. Februar -> 28.
        return d.replace(year=d.year + jahre, day=28)


def naechste_wartung(ls: Dict[str, Any]) -> Optional[str]:
    """Fälligkeitsdatum der nächsten Wartung (ISO) aus letzter Wartung +
    Intervall (Jahre). None, wenn keine letzte Wartung erfasst ist."""
    letzte = parse_iso_date(ls.get("letzte_wartung"))
    if not letzte:
        return None
    try:
        jahre = int(ls.get("intervall_jahre") or WARTUNG_INTERVALL_JAHRE_DEFAULT)
    except (TypeError, ValueError):
        jahre = WARTUNG_INTERVALL_JAHRE_DEFAULT
    return _plus_jahre(letzte, jahre).isoformat()


def wartung_status(ls: Dict[str, Any]) -> Dict[str, Any]:
    """Wartungsstatus eines Leistungsschalters:
    status = 'ueberfaellig' | 'bald' (<= 90 Tage) | 'ok' | 'unbekannt'."""
    nd = naechste_wartung(ls)
    if not nd:
        return {"status": "unbekannt", "naechste": None, "tage": None}
    naechste = date.fromisoformat(nd)
    tage = (naechste - date.today()).days
    if tage < 0:
        status = "ueberfaellig"
    elif tage <= 90:
        status = "bald"
    else:
        status = "ok"
    return {"status": status, "naechste": nd, "tage": tage}


def anlagenteile_fuer_anlage(anlage_id: str) -> List[Dict[str, Any]]:
    return anlagenteile.filter(anlage_id=anlage_id)


def messpunkte_gruppiert_fuer_kunde(kunde_id: str) -> tuple:
    """Alle Messpunkte eines Kunden, gruppiert nach Anlagenteil (Verteilung).

    Protokolle werden auf der Verteilung/dem Verteilstromkreis erfasst — diese
    bildet die Gruppe. Sortiert nach Anlage/Teil, Messpunkte je Gruppe nach
    Datum. Liefert (gruppen_list, gesamtzahl).
    """
    anlage_idx = {a["id"]: a for a in anlagen_fuer_kunde(kunde_id)}
    protokolle = [p for p in messprotokolle.list() if p.get("anlage_id") in anlage_idx]
    gruppen: Dict[Any, Dict[str, Any]] = {}
    for p in protokolle:
        teil = anlagenteile.get(p.get("anlagenteil_id")) if p.get("anlagenteil_id") else None
        anlage = anlage_idx.get(p.get("anlage_id"))
        key = ("teil", p["anlagenteil_id"]) if teil else ("anlage", p.get("anlage_id"))
        g = gruppen.setdefault(key, {"teil": teil, "anlage": anlage, "messungen": []})
        for m in p.get("messungen", []):
            g["messungen"].append(m)

    def _sort_key(item):
        g = item[1]
        a = g["anlage"] or {}
        t = g["teil"] or {}
        return (a.get("bezeichnung", "").lower(), t.get("typ", ""), t.get("bezeichnung", "").lower())

    gruppen_list = [g for _, g in sorted(gruppen.items(), key=_sort_key)]
    for g in gruppen_list:
        g["messungen"].sort(key=lambda m: m.get("datum", ""))
    gesamt = sum(len(g["messungen"]) for g in gruppen_list)
    return gruppen_list, gesamt


def anlagenteile_mit_anhang(anlage_id: str) -> List[Dict[str, Any]]:
    """Anlagenteile einer Anlage mit Datei-Anhaengen (Foto/Legende/Schema)."""
    teile = [t for t in anlagenteile_fuer_anlage(anlage_id) if t.get("anhang")]
    teile.sort(key=lambda t: (t.get("typ", ""), t.get("bezeichnung", "")))
    return teile


def messprotokolle_fuer_anlage(anlage_id: str) -> List[Dict[str, Any]]:
    return [m for m in messprotokolle.list() if m.get("anlage_id") == anlage_id]


def auftraege_fuer_kunde(kunde_id: str) -> List[Dict[str, Any]]:
    return [a for a in auftraege.list() if a.get("kunde_id") == kunde_id]


def revisionen_fuer_kunde(kunde_id: str) -> List[Dict[str, Any]]:
    eintraege = [r for r in revisionen.list() if r.get("kunde_id") == kunde_id]
    # Aktuelle/zukuenftige zuerst (nach Startdatum absteigend wenn vorhanden)
    eintraege.sort(key=lambda r: (r.get("status") == "abgeschlossen", r.get("von", "") or "9999"), reverse=False)
    return eintraege


def auftraege_in_revision(revision_id: str) -> List[Dict[str, Any]]:
    return [a for a in auftraege.list() if a.get("revision_id") == revision_id]


def ist_mitarbeiter_in_revision(revision_id: Optional[str], username: str) -> bool:
    """True wenn der Username in der Mitarbeiter-Liste der Revision steht."""
    if not revision_id or not username:
        return False
    rev = revisionen.get(revision_id)
    if not rev:
        return False
    liste = rev.get("mitarbeiter") or []
    uname_lc = username.lower()
    return any((m or "").lower() == uname_lc for m in liste)


def messgeraete_fuer_user(username: str, ist_admin: bool = False) -> List[Dict[str, Any]]:
    """Liefert die fuer den aktuellen User sichtbaren Messgeraete.
    Admin sieht alle, andere User nur ihre eigenen (owner == username) plus
    'verwaiste' Messgeraete ohne owner (Backward-Kompat mit alten Daten).
    """
    alle = messgeraete.list()
    if ist_admin:
        return alle
    return [m for m in alle if m.get("owner") == username or not m.get("owner")]


def auftraege_fuer_anlagenteil(teil_id: str) -> List[Dict[str, Any]]:
    return [a for a in auftraege.list() if teil_id in (a.get("anlagenteil_ids") or [])]


def anlagen_ids_im_auftrag(auftrag: Dict[str, Any]) -> List[str]:
    """Liefert die distinct Anlagen, zu denen die im Auftrag genannten Teile gehören."""
    teil_ids = set(auftrag.get("anlagenteil_ids") or [])
    if not teil_ids:
        return []
    teile = [t for t in anlagenteile.list() if t["id"] in teil_ids]
    return sorted({t.get("anlage_id") for t in teile if t.get("anlage_id")})


# ----- Zeiterfassung ---------------------------------------------------------

def dauer_aus_zeitspanne(von: Optional[str], bis: Optional[str]) -> Optional[float]:
    """Berechnet Stunden zwischen 'HH:MM' und 'HH:MM' (über Mitternacht nicht supported)."""
    if not von or not bis:
        return None
    try:
        vh, vm = (int(x) for x in von.split(":"))
        bh, bm = (int(x) for x in bis.split(":"))
    except (ValueError, AttributeError):
        return None
    minuten = (bh * 60 + bm) - (vh * 60 + vm)
    if minuten <= 0:
        return None
    return round(minuten / 60.0, 2)


def zeitbuchungen_fuer_auftrag(auftrag_id: str) -> List[Dict[str, Any]]:
    eintraege = [z for z in zeitbuchungen.list() if z.get("auftrag_id") == auftrag_id]
    eintraege.sort(key=lambda z: (z.get("datum", ""), z.get("von_zeit") or ""))
    return eintraege


def zeitsumme_h(eintraege: List[Dict[str, Any]]) -> float:
    total = 0.0
    for z in eintraege:
        d = _to_float(z.get("dauer_h"))
        if d:
            total += d
    return round(total, 2)


def auftrag_bei_zeitbuchung_aktualisieren(auftrag_id: str, mitarbeiter: str) -> None:
    """Auto-Hook nach einer Zeitbuchung: Auftrag in Arbeit setzen + zuteilen.

    Sobald Zeit auf einen Auftrag gebucht wird, soll der Auftrag laufen:
    - Status 'offen' -> 'in_arbeit' (bereits erledigte/abgerechnete bleiben unberuehrt)
    - ist noch niemand zugeteilt, wird der buchende Mitarbeiter eingetragen
      (eine bestehende Zuteilung wird NICHT ueberschrieben)
    Leere auftrag_id (z.B. Stempelung ohne Auftrag) wird ignoriert.
    """
    if not auftrag_id:
        return
    auftrag = auftraege.get(auftrag_id)
    if not auftrag:
        return
    update: Dict[str, Any] = {}
    if auftrag.get("status") == "offen":
        update["status"] = "in_arbeit"
    if mitarbeiter and not (auftrag.get("zugewiesen_an") or "").strip():
        update["zugewiesen_an"] = mitarbeiter
    if update:
        auftraege.update(auftrag_id, update)


# ----- Stempelung (laufende Zeit-Erfassung) ----------------------------------

def aktive_stempelung_von(username: str) -> Optional[Dict[str, Any]]:
    """Aktuell laufende Stempelung eines Mitarbeiters (max 1 pro User)."""
    if not username:
        return None
    for s in stempelungen.list():
        if (s.get("mitarbeiter") or "").lower() == username.lower():
            return s
    return None


def alle_aktiven_stempelungen() -> List[Dict[str, Any]]:
    return list(stempelungen.list())


def zeitbuchungen_am_tag(datum_iso: str) -> List[Dict[str, Any]]:
    """Alle abgeschlossenen Zeitbuchungen eines bestimmten Tages."""
    return [z for z in zeitbuchungen.list() if z.get("datum") == datum_iso]


# ----- Last-/Aufbau-Berechnung -----------------------------------------------

def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def strom_aus_leistung(leistung_kw: Optional[float], spannung: Optional[str]) -> Optional[float]:
    """Strom in A aus Leistung in kW und Spannungssystem (cos φ = 1, konservativ)."""
    p = _to_float(leistung_kw)
    if p is None or not spannung:
        return None
    if spannung == "230V":
        return p * 1000.0 / 230.0
    if spannung == "400V":
        return p * 1000.0 / (math.sqrt(3) * 400.0)
    return None


def leistung_aus_strom(strom_a: Optional[float], spannung: Optional[str]) -> Optional[float]:
    """Leistung in kW aus Strom in A und Spannungssystem (cos φ = 1)."""
    i = _to_float(strom_a)
    if i is None or not spannung:
        return None
    if spannung == "230V":
        return i * 230.0 / 1000.0
    if spannung == "400V":
        return i * math.sqrt(3) * 400.0 / 1000.0
    return None


def anzeige_last(teil: Dict[str, Any]) -> Dict[str, Any]:
    """Anzeige-Werte fuer Leistung (kW) und Strom (A) eines Anlagenteils.

    Zeigt beide Groessen. Fehlt eine, wird sie aus der anderen mit der
    angegebenen Spannung berechnet (cos φ = 1) und als 'berechnet' markiert.
    Ohne Spannung bleibt die fehlende Groesse None.
    """
    kw_in = _to_float(teil.get("leistung_kw"))
    a_in = _to_float(teil.get("stromstaerke_a"))
    spannung = teil.get("spannung")
    kw, a = kw_in, a_in
    kw_berechnet = False
    a_berechnet = False
    if kw_in is None and a_in is not None:
        kw = leistung_aus_strom(a_in, spannung)
        kw_berechnet = kw is not None
    if a_in is None and kw_in is not None:
        a = strom_aus_leistung(kw_in, spannung)
        a_berechnet = a is not None
    return {"kw": kw, "a": a, "kw_berechnet": kw_berechnet, "a_berechnet": a_berechnet}


def teil_last_kw(teil: Dict[str, Any]) -> Optional[float]:
    """Last eines Anlagenteils in kW — direkt erfasste Leistung,
    oder aus Stromstärke + Spannung umgerechnet. Nur wenn nötig.
    """
    leistung = _to_float(teil.get("leistung_kw"))
    if leistung is not None:
        return leistung
    strom = _to_float(teil.get("stromstaerke_a"))
    if strom is not None:
        return leistung_aus_strom(strom, teil.get("spannung"))
    return None


def baue_aufbau_baum(anlage_id: str) -> List[Dict[str, Any]]:
    """Baut die Aufbau-Hierarchie aller Anlagenteile einer Anlage als Forest.

    Jeder Knoten:
        teil: dict (Anlagenteil)
        children: list[Knoten]
        eingabe_kw: float|None      (was als Leistung kW eingetragen wurde, sonst None)
        eingabe_a:  float|None      (was als Stromstärke A eingetragen wurde, sonst None)
        summe_nachgelagert_kw: float|None
            (Summe aller Lasten DAHINTER in kW — ohne den eigenen Wert.
             Mischt Eingaben in A und kW: A-Werte werden mit der Spannung
             des jeweiligen Kindes auf kW umgerechnet, cos φ = 1.)
        effektiver_ikmax_a: float|None
        ikmax_geerbt_von: dict|None  (Vorfahre, falls geerbt)
    """
    teile = anlagenteile_fuer_anlage(anlage_id)

    knoten: Dict[str, Dict[str, Any]] = {
        t["id"]: {
            "teil": t,
            "children": [],
            "eingabe_kw": _to_float(t.get("leistung_kw")),
            "eingabe_a": _to_float(t.get("stromstaerke_a")),
            "anzeige": anzeige_last(t),
        }
        for t in teile
    }
    roots: List[Dict[str, Any]] = []
    for t in teile:
        parent_id = t.get("parent_id")
        if parent_id and parent_id in knoten:
            knoten[parent_id]["children"].append(knoten[t["id"]])
        else:
            roots.append(knoten[t["id"]])

    def _summe_nachgelagert(node: Dict[str, Any]) -> Optional[float]:
        """Summe der Lasten ALLER Nachfahren in kW — ohne diesen Knoten selbst.

        Nur Anlagen- und Endstromkreise zählen mit ihrer eigenen Eingabe als
        Last; Verteilungen/Verteilstromkreise (und Erzeugerstromkreise) werden
        durchlaufen, aber ihre eigene Eingabe nicht aufsummiert.
        """
        total_kw = 0.0
        any_value = False
        for child in node["children"]:
            if child["teil"].get("typ") in ANLAGENTEIL_TYP_IST_LAST:
                eigene = teil_last_kw(child["teil"])
                if eigene is not None:
                    total_kw += eigene
                    any_value = True
            sub = _summe_nachgelagert(child)
            if sub is not None:
                total_kw += sub
                any_value = True
        result = total_kw if any_value else None
        node["summe_nachgelagert_kw"] = result
        # Gleichzeitigkeitsfaktor = eigene Last / Summe nachgelagert (beide in kW)
        eigen_kw = node["anzeige"]["kw"]
        node["gleichzeitigkeit"] = (eigen_kw / result) if (eigen_kw and result) else None
        return result

    def _ikmax(node: Dict[str, Any], geerbt: Optional[Dict[str, Any]]) -> None:
        eigen_ik = _to_float(node["teil"].get("gemessen_ik_a"))
        if eigen_ik is not None:
            node["effektiver_ikmax_a"] = eigen_ik
            node["ikmax_geerbt_von"] = None
            quelle_fuer_kinder = node
        elif geerbt is not None:
            node["effektiver_ikmax_a"] = _to_float(geerbt["teil"].get("gemessen_ik_a"))
            node["ikmax_geerbt_von"] = geerbt["teil"]
            quelle_fuer_kinder = geerbt
        else:
            node["effektiver_ikmax_a"] = None
            node["ikmax_geerbt_von"] = None
            quelle_fuer_kinder = None
        for child in node["children"]:
            _ikmax(child, quelle_fuer_kinder)

    for root in roots:
        _summe_nachgelagert(root)
        _ikmax(root, None)

    return roots


def moegliche_eltern(anlage_id: str, ausgenommen_teil_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Anlagenteile, die als Parent in Frage kommen (Verteilung, Verteil- und Anlagenstromkreise).

    Schliesst den Teil selbst und alle seine Nachfahren aus, um Zyklen zu vermeiden.
    Endstromkreise sind nie Parent — sie sind Blätter.
    """
    teile = anlagenteile_fuer_anlage(anlage_id)
    container = [t for t in teile if t.get("typ") in ANLAGENTEIL_TYP_KANN_PARENT]
    if ausgenommen_teil_id is None:
        return sorted(container, key=lambda t: (t.get("typ", ""), t.get("bezeichnung", "")))

    children_by_parent: Dict[str, List[str]] = {}
    for t in teile:
        children_by_parent.setdefault(t.get("parent_id") or "", []).append(t["id"])

    verboten = {ausgenommen_teil_id}
    stack = list(children_by_parent.get(ausgenommen_teil_id, []))
    while stack:
        current = stack.pop()
        if current in verboten:
            continue
        verboten.add(current)
        stack.extend(children_by_parent.get(current, []))

    return sorted(
        [v for v in container if v["id"] not in verboten],
        key=lambda t: (t.get("typ", ""), t.get("bezeichnung", "")),
    )


def kontroll_uebersicht_fuer_kunde(kunde_id: str) -> Dict[str, Any]:
    """Aufbereitete Übersicht für den Elektrokontrolleur-Besuch."""
    kunde = kunden.get(kunde_id)
    if not kunde:
        return {}

    kunde_anlagen = anlagen_fuer_kunde(kunde_id)
    items: List[Dict[str, Any]] = []
    for anlage in kunde_anlagen:
        teile = anlagenteile_fuer_anlage(anlage["id"])
        for teil in teile:
            items.append({
                "anlage": anlage,
                "anlagenteil": teil,
                "status": teil.get("kontroll_status", "offen"),
                "letzte_kontrolle": teil.get("letzte_kontrolle"),
            })

    offen = [i for i in items if i["status"] == "offen"]
    maengel = [i for i in items if i["status"] == "maengel"]
    geprueft = [i for i in items if i["status"] == "geprueft"]

    return {
        "kunde": kunde,
        "anlagen": kunde_anlagen,
        "items": items,
        "offen": offen,
        "maengel": maengel,
        "geprueft": geprueft,
        "anzahl_total": len(items),
        "anzahl_offen": len(offen),
        "anzahl_maengel": len(maengel),
        "anzahl_geprueft": len(geprueft),
    }


def dashboard_data() -> Dict[str, Any]:
    """Übersicht der offenen Kontrollen (offen + Mängel), nach Kunden gruppiert.

    Nicht mehr auf Stammkunden beschränkt — gelistet wird jeder Kunde, bei dem
    tatsächlich noch etwas zu prüfen ist (mindestens ein Anlagenteil offen oder
    mit Mängel). Pro Kunde werden die konkreten Anlagenteile mitgeliefert, damit
    die Übersicht die offenen Punkte direkt gruppiert anzeigen kann.
    """
    rows: List[Dict[str, Any]] = []
    summe_offen = 0
    summe_maengel = 0
    for kunde in kunden.list():
        uebersicht = kontroll_uebersicht_fuer_kunde(kunde["id"])
        offen = uebersicht.get("offen", [])
        maengel = uebersicht.get("maengel", [])
        if not offen and not maengel:
            continue
        summe_offen += len(offen)
        summe_maengel += len(maengel)
        rows.append({
            "kunde": kunde,
            "offen": offen,
            "maengel": maengel,
            "anzahl_offen": len(offen),
            "anzahl_maengel": len(maengel),
            "anzahl_total": uebersicht.get("anzahl_total", 0),
        })
    rows.sort(key=lambda r: (-r["anzahl_maengel"], -r["anzahl_offen"], r["kunde"]["name"].lower()))
    return {
        "offene_rows": rows,
        "summe_offen": summe_offen,
        "summe_maengel": summe_maengel,
        "anzahl_kunden": len(kunden.list()),
        "anzahl_anlagen": len(anlagen.list()),
        "anzahl_messprotokolle": len(messprotokolle.list()),
        # Auftraege nach Status fuer die Dashboard-Uebersicht
        "anzahl_auftraege": sum(1 for a in auftraege.list() if a.get("status") not in ("erledigt", "abgerechnet")),
        "auftraege_offen": sum(1 for a in auftraege.list() if a.get("status") == "offen"),
        "auftraege_in_arbeit": sum(1 for a in auftraege.list() if a.get("status") == "in_arbeit"),
        "auftraege_erledigt": sum(1 for a in auftraege.list() if a.get("status") == "erledigt"),
    }
