"""Konkrete Repositories und Domain-Logik (Schweizer NIN/NIV-Vokabular).

Datenmodell-Übersicht
---------------------
Kunde
    id, name, adresse, plz, ort, telefon, email, notizen, teams,
    kontaktpersonen, todos

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
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
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
mietreservationen = JsonStore("mietreservationen.json")
material = JsonStore("material.json")
lagerartikel = JsonStore("lagerartikel.json")
lagerlieferscheine = JsonStore("lagerlieferscheine.json")

VERMIET_STATUS = ["verfuegbar", "ausgeliehen", "wartung"]
VERMIET_STATUS_LABEL = {
    "verfuegbar": "Verfügbar",
    "ausgeliehen": "Ausgeliehen",
    "wartung": "Nicht verfügbar",
}

RESERVATION_STATUS_LABEL = {
    "angefragt": "Angefragt",
    "bestaetigt": "Bestätigt",
    "abgelehnt": "Abgelehnt",
    "storniert": "Storniert",
}


def mieter_sortiert() -> List[Dict[str, Any]]:
    return sorted(mieter.list(), key=lambda m: (m.get("name", "").lower()))


# ----- ToDos (generisch: auf Records mit 'todos'-Liste) -----------------------

def todo_hinzufuegen(store, record_id: str, text: str) -> bool:
    rec = store.get(record_id)
    if not rec or not text.strip():
        return False
    todos = list(rec.get("todos") or [])
    todos.append({"id": uuid.uuid4().hex[:12], "text": text.strip(), "erledigt": False})
    store.update(record_id, {"todos": todos})
    return True


def todo_toggle(store, record_id: str, todo_id: str) -> bool:
    rec = store.get(record_id)
    if not rec:
        return False
    todos = list(rec.get("todos") or [])
    for t in todos:
        if t.get("id") == todo_id:
            t["erledigt"] = not t.get("erledigt", False)
            break
    store.update(record_id, {"todos": todos})
    return True


def todo_loeschen(store, record_id: str, todo_id: str) -> bool:
    rec = store.get(record_id)
    if not rec:
        return False
    store.update(record_id, {"todos": [t for t in (rec.get("todos") or []) if t.get("id") != todo_id]})
    return True


def _zeitraum_ueberlappt(a_von: str, a_bis: str, b_von: str, b_bis: str) -> bool:
    """Zwei Datums-Zeiträume überlappen (inklusive Randtage)."""
    return a_von <= b_bis and b_von <= a_bis


def _als_int(wert, default: int = 1) -> int:
    try:
        return int(wert)
    except (TypeError, ValueError):
        return default


def maschine_bestand(m: Dict[str, Any]) -> Dict[str, Any]:
    """Bestand/Verfügbarkeit einer Verleih-Position (mengenfähig).

    Liefert: anzahl (Gesamtbestand), verliehen, verfuegbar, wartung (bool),
    loans (Liste aktiver Ausleihen [{id, mieter_id, anzahl, seit}]).
    Beruecksichtigt Alt-Datensaetze (einzelne Ausleihe ueber status/mieter_id).
    """
    anzahl = max(1, _als_int(m.get("anzahl"), 1))
    loans = [dict(l) for l in (m.get("ausleihen") or [])]
    if not loans and m.get("status") == "ausgeliehen" and m.get("mieter_id"):
        loans = [{"id": "legacy", "mieter_id": m["mieter_id"], "anzahl": 1, "seit": m.get("ausgeliehen_seit", "")}]
    verliehen = sum(max(1, _als_int(l.get("anzahl"), 1)) for l in loans)
    wartung = bool(m.get("wartung")) or m.get("status") == "wartung"
    return {
        "anzahl": anzahl,
        "verliehen": verliehen,
        "verfuegbar": max(0, anzahl - verliehen),
        "wartung": wartung,
        "loans": loans,
    }


def reservation_konflikt(maschine_id: str, von: str, bis: str, benoetigt: int = 1, ignore_id: str = "") -> Optional[Dict[str, Any]]:
    """Prüft, ob im Zeitraum genug Bestand frei ist. Summiert die Mengen aller
    überlappenden BESTÄTIGTEN Reservationen; überschreitet (belegt + benoetigt)
    den Gesamtbestand, wird ein Konflikt-Dict {belegt, frei, total} geliefert."""
    m = mietmaschinen.get(maschine_id) or {}
    total = maschine_bestand(m)["anzahl"]
    belegt = 0
    for r in mietreservationen.list():
        if r.get("maschine_id") != maschine_id or r.get("status") != "bestaetigt":
            continue
        if ignore_id and r.get("id") == ignore_id:
            continue
        if _zeitraum_ueberlappt(von, bis, r.get("von", ""), r.get("bis", "")):
            belegt += max(1, _als_int(r.get("anzahl"), 1))
    if belegt + max(1, benoetigt) > total:
        return {"belegt": belegt, "frei": max(0, total - belegt), "total": total}
    return None


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
    "abgerechnet": "Rapportiert",
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


def eintrag_teams(record: Dict[str, Any]) -> List[str]:
    """Die Teams (PL-Usernames), denen ein Auftrag/Kunde zugeordnet ist.

    Kanonisch ist die Liste 'teams'. Für Alt-Datensätze ohne 'teams' greifen
    die früheren Einzelfelder ('projektleiter' + 'freigegeben_an_teams')."""
    teams = [(t or "").strip() for t in (record.get("teams") or [])]
    teams = [t for t in teams if t]
    if teams:
        return teams
    # Rückwärtskompatibilität: alte Einzelfelder
    legacy: List[str] = []
    prim = (record.get("projektleiter") or "").strip()
    if prim:
        legacy.append(prim)
    for t in (record.get("freigegeben_an_teams") or []):
        t = (t or "").strip()
        if t and t not in legacy:
            legacy.append(t)
    return legacy


def auftrag_sichtbar_fuer(auftrag: Dict[str, Any], user) -> bool:
    """Zentrale Sichtbarkeitsregel für Aufträge (Team-Modell).

    Admin sieht alles. Sonst sichtbar, wenn:
    - der User Mitarbeiter der zugehörigen Revision ist,
    - der Auftrag ihm zugewiesen oder zusätzlich freigegeben wurde, oder
    - der Auftrag einem Team des Users zugeordnet ist (user.team_leiter in teams).
    Alt-Aufträge OHNE Team: unzugewiesene bleiben (wie bisher) sichtbar.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_admin", False):
        return True
    username = user.username
    if ist_mitarbeiter_in_revision(auftrag.get("revision_id"), username):
        return True
    if username in (auftrag.get("freigegeben_an") or []):
        return True
    zug = (auftrag.get("zugewiesen_an") or "").strip()
    if zug and zug.lower() == username.lower():
        return True
    teams = eintrag_teams(auftrag)
    if teams:
        return getattr(user, "team_leiter", None) in teams
    # Alt-Auftrag ohne Team: unzugewiesene sichtbar
    return not zug


def kunde_teams(kunde: Dict[str, Any]) -> List[str]:
    """Alle Teams (PL-Usernames), die diesen Kunden sehen dürfen."""
    return eintrag_teams(kunde)


def kunde_sichtbar_fuer(kunde: Dict[str, Any], user) -> bool:
    """Zentrale Sichtbarkeitsregel für Kunden (Team-Modell mit Mehrfach-Freigabe).

    Admin sieht alles. Sonst sichtbar, wenn das Team des Users den Kunden besitzt
    oder dafür freigegeben wurde. Alt-Kunden OHNE Team bleiben für alle sichtbar.
    Fallback: Wer einen Auftrag dieses Kunden sehen darf, sieht auch den Kunden
    (verhindert tote Links bei team-übergreifend zugewiesenen/freigegebenen Aufträgen).
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_admin", False):
        return True
    teams = kunde_teams(kunde)
    if not teams:
        return True  # Alt-Kunde ohne Team: wie bisher für alle sichtbar
    team = getattr(user, "team_leiter", None)
    if team and team in teams:
        return True
    for a in auftraege_fuer_kunde(kunde.get("id")):
        if auftrag_sichtbar_fuer(a, user):
            return True
    return False


def sichtbare_kunden(user) -> List[Dict[str, Any]]:
    """Alle für den User sichtbaren Kunden (Team-gefiltert)."""
    return [k for k in kunden.list() if kunde_sichtbar_fuer(k, user)]


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


def auftrag_zeit_abrechnen(auftrag_id: str, bis_datum: Optional[str] = None,
                           abgerechnet_am: Optional[str] = None) -> int:
    """Markiert (noch nicht abgerechnete) Zeitbuchungen eines Auftrags als abgerechnet.

    bis_datum (ISO) = nur Buchungen mit datum <= bis_datum (Teilabrechnung);
    None = alle offenen Buchungen. Liefert die Anzahl neu markierter Buchungen.
    Darf von jedem gebucht werden, der den Auftrag sehen darf (typischerweise
    der Monteur selbst — er schreibt die Rapporte), nicht nur vom Projektleiter."""
    n = 0
    for z in zeitbuchungen_fuer_auftrag(auftrag_id):
        if z.get("abgerechnet"):
            continue
        if bis_datum and (z.get("datum") or "") > bis_datum:
            continue
        zeitbuchungen.update(z["id"], {"abgerechnet": True, "abgerechnet_am": abgerechnet_am})
        n += 1
    return n


def material_fuer_auftrag(auftrag_id: str) -> List[Dict[str, Any]]:
    """Alle Material-Positionen eines Auftrags, sortiert nach Lieferschein-Datum,
    dann Position, dann Erfassungszeit (manuell erfasste ans Ende)."""
    items = [m for m in material.list() if m.get("auftrag_id") == auftrag_id]
    items.sort(key=lambda m: (
        m.get("lieferschein_datum") or "9999-12-31",
        m.get("position") if m.get("position") is not None else 10**9,
        m.get("erstellt_am") or "",
    ))
    return items


def material_abrechnen(auftrag_id: str, bis_datum: Optional[str] = None,
                       abgerechnet_am: Optional[str] = None) -> int:
    """Markiert (noch offene) Material-Positionen eines Auftrags als abgerechnet.
    bis_datum (ISO) = nur Positionen mit lieferschein_datum <= bis_datum; None =
    alle offenen. Manuell erfasste ohne Datum zaehlen nur bei bis_datum=None mit.
    Liefert die Anzahl neu markierter Positionen."""
    n = 0
    for m in material_fuer_auftrag(auftrag_id):
        if m.get("abgerechnet"):
            continue
        if bis_datum:
            ld = m.get("lieferschein_datum") or ""
            if not ld or ld > bis_datum:
                continue
        material.update(m["id"], {"abgerechnet": True, "abgerechnet_am": abgerechnet_am})
        n += 1
    return n


# ----- Lagermaterial (Lagerbestand, unabhaengig von Auftraegen) --------------
#
# Ein Lagerartikel ist ein Stammartikel im eigenen Lager (nicht auftragsbezogen).
# Er wird per Barcode/E-Nummer eingescannt, einem Lagerort zugeordnet und kann
# fuer eine Bestellliste (mit gedruckten Barcodes) markiert werden.
#
# Felder:
#   id, bezeichnung, barcode (Scan-Schluessel, meist EAN), e_nummer,
#   em_artikel_nr, lagerort, lieferant, einheit, bestand (int),
#   mindestbestand (int, 0 = keine Schwelle), bestellmenge (Standard-Menge),
#   nachbestellen (bool, manuelles Flag), notizen, erstellt_am, geaendert_am

def lagerartikel_sortiert() -> List[Dict[str, Any]]:
    """Alle Lagerartikel, sortiert nach Lagerort, dann Bezeichnung."""
    items = lagerartikel.list()
    items.sort(key=lambda a: (
        (a.get("lagerort") or "￿").lower(),  # ohne Lagerort ans Ende
        (a.get("bezeichnung") or "").lower(),
    ))
    return items


def lagerartikel_by_barcode(code: str) -> Optional[Dict[str, Any]]:
    """Ersten Lagerartikel finden, dessen gescannter Barcode (EAN) ODER
    Bestellnummer (Leerzeichen-unabhaengig) dem Scan-Code entspricht. Der
    Handscanner liest am Regal den EAN der Verpackung -> Treffer ueber 'barcode'.
    None wenn nicht gefunden."""
    if not code:
        return None
    ziel = code.strip().replace(" ", "").lower()
    if not ziel:
        return None
    for a in lagerartikel.list():
        for feld in ("barcode", "bestellnummer"):
            wert = (a.get(feld) or "").strip().replace(" ", "").lower()
            if wert and wert == ziel:
                return a
    return None


def lagerorte_liste() -> List[str]:
    """Alle vergebenen Lagerorte (distinct, alphabetisch) fuer Autocomplete."""
    orte = {(a.get("lagerort") or "").strip() for a in lagerartikel.list()}
    return sorted((o for o in orte if o), key=str.lower)


# Haupt-Lieferanten des Betriebs (Vorschlaege im Autocomplete, auch wenn noch
# kein Artikel damit erfasst ist).
LAGER_STANDARD_LIEFERANTEN = ("Würth", "Elektro-Material AG")


def lager_lieferanten_liste() -> List[str]:
    """Alle Lieferanten (distinct, alphabetisch) fuer Autocomplete — Standard-
    Lieferanten immer dabei, plus alle bereits vergebenen."""
    lief = {(a.get("lieferant") or "").strip() for a in lagerartikel.list()}
    lief.update(LAGER_STANDARD_LIEFERANTEN)
    return sorted((l for l in lief if l), key=str.lower)


def lagerartikel_ohne_ean() -> List[Dict[str, Any]]:
    """Lagerartikel ohne erfassten Barcode/EAN — diese muessen noch am Regal
    eingescannt werden (betrifft v.a. per Wareneingang automatisch angelegte
    Artikel). Artikel, die bewusst als 'kein EAN vorhanden' markiert sind (z.B.
    Isolierbaender ohne Strichcode), werden ausgeblendet. Sortiert nach Lagerort,
    dann Bezeichnung."""
    items = [a for a in lagerartikel.list()
             if not (a.get("barcode") or "").strip() and not a.get("kein_ean")
             and not a.get("ausgelaufen")]
    items.sort(key=lambda a: (
        (a.get("lagerort") or "￿").lower(),
        (a.get("bezeichnung") or "").lower(),
    ))
    return items


def lagerartikel_nachzubestellen() -> List[Dict[str, Any]]:
    """Artikel, die bestellt werden sollten: entweder manuell mit 'nachbestellen'
    markiert, oder deren Bestand die Mindestschwelle (>0) erreicht/unterschreitet.
    Sortiert nach Lieferant, dann Bezeichnung (bestell-lieferantenweise)."""
    out = []
    for a in lagerartikel.list():
        if a.get("ausgelaufen"):
            continue  # Auslaufartikel werden nicht mehr bestellt
        if a.get("bestellt"):
            continue  # bereits bestellt (unterwegs) -> nicht erneut auflisten
        manuell = bool(a.get("nachbestellen"))
        mind = a.get("mindestbestand") or 0
        bestand = a.get("bestand")
        schwelle = bool(mind) and bestand is not None and bestand <= mind
        if manuell or schwelle:
            out.append(a)
    out.sort(key=lambda a: (
        (a.get("lieferant") or "￿").lower(),
        (a.get("bezeichnung") or "").lower(),
    ))
    return out


# ----- Lager-Lieferscheine / Verbrauchsstatistik ------------------------------
#
# Importierte Lieferscheine (Wuerth/EM) dienen als Verbrauchs-Historie: was ueber
# die Zeit eingekauft wird, entspricht im Mittel dem Verbrauch. Daraus schaetzen
# wir den durchschnittlichen Monatsverbrauch je Artikel und einen Bestellvorschlag
# fuer einen gewuenschten Bestellrhythmus (Standard: 14 Tage).
#
# lagerlieferscheine.json-Record:
#   id, lieferant, nummer, datum (ISO), erstellt_am,
#   positionen: [ {bestellnummer, bezeichnung, menge (float), einheit} ]

TAGE_PRO_MONAT = 30.44  # mittlere Monatslaenge (365.25 / 12)


def lagerlieferschein_speichern(lieferant: str, nummer: str, datum: Optional[str],
                                positionen: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Legt einen importierten Lieferschein (Verbrauchs-Historie) ab. Nur
    Positionen mit erfasster Menge > 0 werden gespeichert."""
    saubere = []
    for p in positionen:
        menge = p.get("menge")
        if menge is None or menge <= 0:
            continue
        saubere.append({
            "bestellnummer": (p.get("bestellnummer") or "").strip(),
            "bezeichnung": (p.get("bezeichnung") or "").strip(),
            "menge": float(menge),
            "einheit": (p.get("einheit") or "").strip(),
        })
    return lagerlieferscheine.create({
        "lieferant": (lieferant or "").strip(),
        "nummer": (nummer or "").strip(),
        "datum": datum or None,
        "positionen": saubere,
    })


def lagerlieferscheine_sortiert() -> List[Dict[str, Any]]:
    """Alle importierten Lieferscheine, neueste zuerst (nach Datum, dann Import)."""
    items = lagerlieferscheine.list()
    items.sort(key=lambda l: (
        l.get("datum") or "0000-00-00",
        l.get("erstellt_am") or "",
    ), reverse=True)
    return items


def lagerlieferschein_verbrauch(fenster_tage: int = 365) -> Dict[str, Dict[str, Any]]:
    """Aggregiert importierte Lieferschein-Positionen je normalisierter
    Bestellnummer innerhalb des Zeitfensters (Default: letzte 365 Tage).

    Rueckgabe: key -> {beispiel_nr, bezeichnung, einheit, total,
    anzahl_positionen, anzahl_lieferungen, erste (ISO), letzte (ISO)}.
    """
    from .lieferschein import normalisiere_bestellnr

    grenze = (date.today() - timedelta(days=fenster_tage)).isoformat()
    agg: Dict[str, Dict[str, Any]] = {}
    for ls in lagerlieferscheine.list():
        datum = ls.get("datum") or ""
        if datum and datum < grenze:
            continue
        ls_id = ls.get("id")
        for p in ls.get("positionen", []):
            key = normalisiere_bestellnr(p.get("bestellnummer"))
            if not key:
                continue
            menge = p.get("menge") or 0
            eintrag = agg.get(key)
            if eintrag is None:
                eintrag = {
                    "beispiel_nr": (p.get("bestellnummer") or "").strip(),
                    "bezeichnung": (p.get("bezeichnung") or "").strip(),
                    "einheit": (p.get("einheit") or "").strip(),
                    "total": 0.0,
                    "anzahl_positionen": 0,
                    "_lieferscheine": set(),
                    "erste": datum or None,
                    "letzte": datum or None,
                }
                agg[key] = eintrag
            eintrag["total"] += float(menge)
            eintrag["anzahl_positionen"] += 1
            if ls_id:
                eintrag["_lieferscheine"].add(ls_id)
            if p.get("bezeichnung"):
                eintrag["bezeichnung"] = p["bezeichnung"].strip()
            if datum:
                if not eintrag["erste"] or datum < eintrag["erste"]:
                    eintrag["erste"] = datum
                if not eintrag["letzte"] or datum > eintrag["letzte"]:
                    eintrag["letzte"] = datum
    for eintrag in agg.values():
        eintrag["anzahl_lieferungen"] = len(eintrag.pop("_lieferscheine"))
    return agg


def _verbrauch_kennzahlen(total: float, erste_iso: Optional[str],
                          anzahl_lieferungen: int, reichweite_tage: int) -> Dict[str, Any]:
    """Rechnet aus Gesamtmenge + erstem Lieferdatum den Ø-Monatsverbrauch sowie
    den Bedarf fuer 'reichweite_tage'. 'unsicher' = zu kurze/duenne Historie."""
    heute = date.today()
    span_tage = None
    monatsverbrauch = None
    if erste_iso:
        try:
            span_tage = (heute - date.fromisoformat(erste_iso)).days
        except ValueError:
            span_tage = None
    if span_tage and span_tage >= 1 and total > 0:
        monatsverbrauch = total / (span_tage / TAGE_PRO_MONAT)
    tagesverbrauch = (monatsverbrauch / TAGE_PRO_MONAT) if monatsverbrauch else None
    bedarf = (tagesverbrauch * reichweite_tage) if tagesverbrauch else None
    unsicher = bool(monatsverbrauch) and (
        (span_tage is not None and span_tage < 30) or anzahl_lieferungen < 2
    )
    return {
        "span_tage": span_tage,
        "monatsverbrauch": monatsverbrauch,
        "tagesverbrauch": tagesverbrauch,
        "bedarf": bedarf,
        "unsicher": unsicher,
    }


def _artikel_stand_datum(a: Dict[str, Any]) -> Optional[date]:
    """Stichtag des zuletzt bekannten Bestands: 'bestand_stand' (bei Wareneingang/
    Inventur gesetzt), sonst geaendert_am, sonst erstellt_am. Ab diesem Tag wird
    der geschaetzte Verbrauch rechnerisch abgezogen."""
    for feld in ("bestand_stand", "geaendert_am", "erstellt_am"):
        wert = a.get(feld)
        if wert:
            try:
                return date.fromisoformat(str(wert)[:10])
            except ValueError:
                continue
    return None


# Zielbestand nach einer Bestellung = Verbrauch fuer (reichweite * ZIEL_FAKTOR)
# Tage. Bei 14 Tagen Rhythmus hat man direkt nach dem Auffuellen ~28 Tage Vorrat
# und bestellt wieder, sobald die Reichweite unter 14 Tage faellt.
BESTELL_ZIEL_FAKTOR = 2

# Erst ab so vielen Lieferungen ist eine Verbrauchsrate aussagekraeftig genug fuer
# einen Bestellvorschlag — eine einzelne Lieferung ergibt keinen echten Verbrauch.
MIN_LIEFERUNGEN_BESTELLUNG = 2

# Artikel mit unsicherer Schaetzung, deren letzte Lieferung mindestens so viele
# Tage her ist, gehoeren in die Bestandskontrolle (physisch nachzaehlen).
BESTANDSKONTROLLE_TAGE = 14

# Pro-Artikel gemessene Lieferzeit: wie viele der letzten Mess-Werte (Bestell- bis
# Lieferdatum) fuer den gleitenden Schnitt behalten werden, und als plausibel
# akzeptierte Spanne (alles ausserhalb gilt als Datenfehler/vergessenes Flag).
LIEFERZEIT_HISTORIE_MAX = 10
LIEFERZEIT_MAX_TAGE = 90


def _gemessene_lieferzeit_tage(bestellt_am, geliefert_am) -> Optional[int]:
    """Tage zwischen Bestell- und Lieferdatum (0..LIEFERZEIT_MAX_TAGE), sonst None.
    None = nicht ermittelbar (fehlendes Datum, unplausibel/negativ)."""
    if not bestellt_am or not geliefert_am:
        return None
    try:
        b = date.fromisoformat(str(bestellt_am)[:10])
        g = date.fromisoformat(str(geliefert_am)[:10])
    except ValueError:
        return None
    tage = (g - b).days
    if 0 <= tage <= LIEFERZEIT_MAX_TAGE:
        return tage
    return None


def lieferzeit_update(artikel: Dict[str, Any], geliefert_am) -> Dict[str, Any]:
    """Aus 'bestellt_am' des Artikels + Lieferdatum die real gemessene Lieferzeit
    bilden und die Artikel-Updates (Historie + gleitender Schnitt 'lieferzeit_tage')
    zurueckgeben. Leeres Dict, wenn keine Messung moeglich ist. Wird beim
    Wareneingang / 'erhalten' aufgerufen, bevor 'bestellt_am' geloescht wird."""
    lz = _gemessene_lieferzeit_tage(artikel.get("bestellt_am"), geliefert_am)
    if lz is None:
        return {}
    hist = [int(x) for x in (artikel.get("lieferzeit_historie") or []) if isinstance(x, (int, float))]
    hist.append(lz)
    hist = hist[-LIEFERZEIT_HISTORIE_MAX:]
    return {
        "lieferzeit_historie": hist,
        "lieferzeit_tage": int(round(sum(hist) / len(hist))),
    }


def _projektion(a: Dict[str, Any], tagesverbrauch: Optional[float],
                reichweite_tage: int, anzahl_lieferungen: int = 0,
                lieferzeit_tage: int = 0) -> Dict[str, Any]:
    """Rechnerischer (voraussichtlicher) Bestand heute = zuletzt bekannter Bestand
    minus geschaetzter Verbrauch seit dem Stichtag. Nicht destruktiv — der
    gespeicherte Bestand bleibt unveraendert. Liefert zusaetzlich die verbleibende
    Reichweite in Tagen und — falls unter der Schwelle UND mit genug Lieferhistorie
    — einen Bestellvorschlag.

    Die Lieferzeit wird eingerechnet: frueh genug bestellen, damit die Ware
    ankommt, bevor der Bestand unter den gewuenschten Rhythmus faellt -> Schwelle =
    Rhythmus + Lieferzeit. Die Bestellmenge deckt zusaetzlich den Verbrauch
    waehrend der Lieferzeit (bis die Ware wirklich eintrifft).
    """
    # Pro-Artikel real gemessene Lieferzeit (Bestell- bis Lieferdatum) hat Vorrang
    # vor dem uebergebenen globalen Standard. Siehe 'lieferzeit_update'.
    art_lz = a.get("lieferzeit_tage")
    gemessen = isinstance(art_lz, (int, float)) and art_lz >= 0
    eff_lieferzeit = max(0, int(art_lz if gemessen else (lieferzeit_tage or 0)))
    bestand = a.get("bestand")
    stand = _artikel_stand_datum(a)
    tage_seit = max(0, (date.today() - stand).days) if stand else None
    voraussichtlich = None
    tage_rest = None
    if bestand is not None and tagesverbrauch:
        verbraucht = tagesverbrauch * (tage_seit or 0)
        voraussichtlich = max(0.0, bestand - verbraucht)
        tage_rest = voraussichtlich / tagesverbrauch
    schwelle_tage = reichweite_tage + eff_lieferzeit
    bestellen = (voraussichtlich is not None and tage_rest is not None
                 and tage_rest < schwelle_tage
                 and anzahl_lieferungen >= MIN_LIEFERUNGEN_BESTELLUNG)
    menge_vorschlag = None
    if bestellen:
        ziel = tagesverbrauch * reichweite_tage * BESTELL_ZIEL_FAKTOR
        # Bis die Ware eintrifft, wird waehrend der Lieferzeit weiter verbraucht.
        bestand_bei_ankunft = max(0.0, voraussichtlich - tagesverbrauch * eff_lieferzeit)
        menge_vorschlag = int(math.ceil(max(0.0, ziel - bestand_bei_ankunft)))
    return {
        "bestand_stand": stand.isoformat() if stand else None,
        "tage_seit_stand": tage_seit,
        "voraussichtlich": voraussichtlich,
        "tage_rest": tage_rest,
        "schwelle_tage": schwelle_tage,
        "lieferzeit_eff": eff_lieferzeit,
        "lieferzeit_gemessen": gemessen,
        "bestellen": bestellen,
        "menge_vorschlag": menge_vorschlag,
    }


def lagerartikel_verbrauch_perioden(artikel: Dict[str, Any],
                                    monate=(1, 3, 6, 12)) -> Dict[str, Any]:
    """Eingekaufte Menge dieses Artikels je rueckblickendem Zeitfenster (1/3/6/12
    Monate) aus den importierten Lieferscheinen = geschaetzter Verbrauch. Fenster
    sind kumulativ (12 Monate enthaelt die 6/3/1). Undatierte Lieferscheine zaehlen
    nicht mit. Rueckgabe: {"perioden": [{monate, total, anzahl}], "einheit"}."""
    from .lieferschein import normalisiere_bestellnr

    keys = set()
    for feld in ("bestellnummer", "barcode"):
        k = normalisiere_bestellnr(artikel.get(feld))
        if k:
            keys.add(k)
    heute = date.today()
    grenzen = {m: (heute - timedelta(days=round(m * TAGE_PRO_MONAT))).isoformat()
               for m in monate}
    summen = {m: 0.0 for m in monate}
    anzahl = {m: 0 for m in monate}
    einheit = (artikel.get("einheit") or "").strip()
    if keys:
        for ls in lagerlieferscheine.list():
            datum = ls.get("datum") or ""
            if not datum:
                continue
            for p in ls.get("positionen", []):
                if normalisiere_bestellnr(p.get("bestellnummer")) not in keys:
                    continue
                menge = float(p.get("menge") or 0)
                if p.get("einheit"):
                    einheit = p["einheit"].strip()
                for m in monate:
                    if datum >= grenzen[m]:
                        summen[m] += menge
                        anzahl[m] += 1
    return {
        "perioden": [{"monate": m, "total": summen[m], "anzahl": anzahl[m]}
                     for m in monate],
        "einheit": einheit,
    }


def lagerverbrauch_uebersicht(reichweite_tage: int = 14,
                              fenster_tage: int = 365,
                              lieferzeit_tage: int = 0) -> Dict[str, Any]:
    """Verbrauchs-Uebersicht: jeder Lagerartikel mit Ø-Monatsverbrauch, Bedarf und
    voraussichtlichem (rechnerischem) Bestand, plus Bestellvorschlag und
    Lieferpositionen ohne Artikel-Zuordnung.

    'lieferzeit_tage' wird in den Bestellvorschlag eingerechnet (frueher bestellen,
    mehr bestellen). Der Bestellvorschlag wird zusaetzlich in zwei Gruppen geteilt:
    Artikel mit EAN (per Barcode scannbar) und ohne EAN (Artikelnummer manuell).

    Rueckgabe: {"zeilen", "ohne_zuordnung", "anzahl_mit_daten", "bestellvorschlag",
    "bestellvorschlag_mit_ean", "bestellvorschlag_ohne_ean", ...}.
    Artikel mit Verbrauchsdaten stehen oben (hoechster Monatsverbrauch zuerst).
    """
    from .lieferschein import normalisiere_bestellnr

    LEERE_PROJEKTION = {
        "bestand_stand": None, "tage_seit_stand": None, "voraussichtlich": None,
        "tage_rest": None, "schwelle_tage": reichweite_tage,
        "lieferzeit_eff": lieferzeit_tage, "lieferzeit_gemessen": False,
        "bestellen": False, "menge_vorschlag": None,
    }

    stats = lagerlieferschein_verbrauch(fenster_tage)
    benutzte_keys: set = set()
    zeilen: List[Dict[str, Any]] = []
    for a in lagerartikel_sortiert():
        st = None
        for feld in ("bestellnummer", "barcode"):
            key = normalisiere_bestellnr(a.get(feld))
            if key and key in stats:
                st = stats[key]
                benutzte_keys.add(key)
                break
        if st:
            kennz = _verbrauch_kennzahlen(
                st["total"], st.get("erste"), st.get("anzahl_lieferungen", 0), reichweite_tage)
            proj = _projektion(a, kennz["tagesverbrauch"], reichweite_tage,
                               st.get("anzahl_lieferungen", 0), lieferzeit_tage)
            zeilen.append({
                "artikel": a,
                "hat_daten": True,
                "total": st["total"],
                "einheit": st.get("einheit") or a.get("einheit") or "",
                "anzahl_lieferungen": st.get("anzahl_lieferungen", 0),
                "erste": st.get("erste"),
                "letzte": st.get("letzte"),
                **kennz,
                **proj,
            })
        else:
            zeilen.append({
                "artikel": a, "hat_daten": False, "total": 0.0,
                "einheit": a.get("einheit") or "", "anzahl_lieferungen": 0,
                "erste": None, "letzte": None, "span_tage": None,
                "monatsverbrauch": None, "tagesverbrauch": None,
                "bedarf": None, "unsicher": False,
                **LEERE_PROJEKTION,
            })

    zeilen.sort(key=lambda z: (
        not z["hat_daten"],
        -(z["monatsverbrauch"] or 0),
        (z["artikel"].get("bezeichnung") or "").lower(),
    ))

    ohne_zuordnung: List[Dict[str, Any]] = []
    for key, st in stats.items():
        if key in benutzte_keys:
            continue
        kennz = _verbrauch_kennzahlen(
            st["total"], st.get("erste"), st.get("anzahl_lieferungen", 0), reichweite_tage)
        ohne_zuordnung.append({
            "bestellnummer": st.get("beispiel_nr") or "",
            "bezeichnung": st.get("bezeichnung") or "",
            "einheit": st.get("einheit") or "",
            "total": st["total"],
            "anzahl_lieferungen": st.get("anzahl_lieferungen", 0),
            "erste": st.get("erste"),
            "letzte": st.get("letzte"),
            **kennz,
        })
    ohne_zuordnung.sort(key=lambda z: -(z["monatsverbrauch"] or 0))

    # Bestellvorschlag: Artikel, deren voraussichtlicher Bestand unter die
    # gewuenschte Reichweite faellt — knappste zuerst.
    bestellvorschlag = [z for z in zeilen
                        if z.get("bestellen") and not z["artikel"].get("ausgelaufen")
                        and not z["artikel"].get("bestellt")]
    bestellvorschlag.sort(key=lambda z: (z["tage_rest"] if z["tage_rest"] is not None else 1e9))

    # Aufteilung fuer die Bestellung: mit EAN -> Barcode, scannbar (z.B. Wuerth-App);
    # ohne EAN -> nur Artikelnummer, manuell im Shop einzutippen.
    bestellvorschlag_mit_ean = [z for z in bestellvorschlag
                                if (z["artikel"].get("barcode") or "").strip()]
    bestellvorschlag_ohne_ean = [z for z in bestellvorschlag
                                 if not (z["artikel"].get("barcode") or "").strip()]

    # Bestandskontrolle: unsichere Schaetzung UND letzte Lieferung >= X Tage her
    # -> physisch nachzaehlen, damit die Projektion wieder stimmt.
    grenze_kontrolle = (date.today() - timedelta(days=BESTANDSKONTROLLE_TAGE)).isoformat()
    bestandskontrolle = [
        z for z in zeilen
        if z["hat_daten"] and z["unsicher"] and z.get("letzte")
        and z["letzte"] <= grenze_kontrolle
        and not z["artikel"].get("ausgelaufen")
    ]
    bestandskontrolle.sort(key=lambda z: (z.get("letzte") or ""))

    # Bestellt / unterwegs: als bestellt markierte Artikel (warten auf Lieferung).
    heute = date.today()
    bestellt_offen = []
    for z in zeilen:
        a = z["artikel"]
        if not a.get("bestellt"):
            continue
        tage_seit = None
        if a.get("bestellt_am"):
            try:
                tage_seit = (heute - date.fromisoformat(str(a["bestellt_am"])[:10])).days
            except ValueError:
                tage_seit = None
        bestellt_offen.append({
            "artikel": a,
            "bestellt_am": a.get("bestellt_am"),
            "tage_seit": tage_seit,
            "einheit": z.get("einheit") or a.get("einheit") or "",
        })
    bestellt_offen.sort(key=lambda b: (b["bestellt_am"] or ""))

    return {
        "zeilen": zeilen,
        "ohne_zuordnung": ohne_zuordnung,
        "anzahl_mit_daten": sum(1 for z in zeilen if z["hat_daten"]),
        "bestellvorschlag": bestellvorschlag,
        "bestellvorschlag_mit_ean": bestellvorschlag_mit_ean,
        "bestellvorschlag_ohne_ean": bestellvorschlag_ohne_ean,
        "bestandskontrolle": bestandskontrolle,
        "bestandskontrolle_tage": BESTANDSKONTROLLE_TAGE,
        "bestellt_offen": bestellt_offen,
        "lieferzeit_tage": lieferzeit_tage,
    }


def lager_top_verbrauch_pro_lagerort(fenster_tage: int = 365,
                                     top_n: int = 5) -> Dict[str, Any]:
    """Statistik: je Lagerort die Artikel mit dem hoechsten geschaetzten
    Ø-Monatsverbrauch (aus den Lieferscheinen im Zeitfenster). Auslaufartikel und
    Artikel ohne Verbrauchsdaten bleiben aussen vor. Lagerorte nach Gesamt-
    Monatsverbrauch absteigend (aktivste oben), 'Ohne Lagerort' ans Ende.

    Rueckgabe: {"gruppen": [{lagerort, artikel:[...], weitere, anzahl_artikel,
    gesamt_monat, max_monat}], "fenster_tage", "top_n"}.
    """
    from .lieferschein import normalisiere_bestellnr

    stats = lagerlieferschein_verbrauch(fenster_tage)
    gruppen_map: Dict[str, List[Dict[str, Any]]] = {}
    for a in lagerartikel_sortiert():
        if a.get("ausgelaufen"):
            continue
        st = None
        for feld in ("bestellnummer", "barcode"):
            key = normalisiere_bestellnr(a.get(feld))
            if key and key in stats:
                st = stats[key]
                break
        if not st:
            continue
        # Wie beim Bestellvorschlag: erst ab 2 Lieferungen ist die Verbrauchsrate
        # aussagekraeftig — eine einzelne (oft grosse) Lieferung verzerrt sonst.
        if st.get("anzahl_lieferungen", 0) < MIN_LIEFERUNGEN_BESTELLUNG:
            continue
        kennz = _verbrauch_kennzahlen(
            st["total"], st.get("erste"), st.get("anzahl_lieferungen", 0), 30)
        mv = kennz["monatsverbrauch"]
        if not mv:
            continue
        ort = (a.get("lagerort") or "").strip() or "Ohne Lagerort"
        gruppen_map.setdefault(ort, []).append({
            "id": a.get("id"),
            "bezeichnung": a.get("bezeichnung") or "",
            "einheit": st.get("einheit") or a.get("einheit") or "",
            "monatsverbrauch": mv,
            "total": st["total"],
            "anzahl_lieferungen": st.get("anzahl_lieferungen", 0),
            "unsicher": kennz["unsicher"],
        })

    gruppen: List[Dict[str, Any]] = []
    for ort, items in gruppen_map.items():
        items.sort(key=lambda e: -(e["monatsverbrauch"] or 0))
        gruppen.append({
            "lagerort": ort,
            "artikel": items[:top_n],
            "weitere": max(0, len(items) - top_n),
            "anzahl_artikel": len(items),
            "gesamt_monat": sum(e["monatsverbrauch"] or 0 for e in items),
            "max_monat": items[0]["monatsverbrauch"] if items else 0,
        })
    gruppen.sort(key=lambda g: (g["lagerort"] == "Ohne Lagerort",
                                -(g["gesamt_monat"] or 0)))
    return {"gruppen": gruppen, "fenster_tage": fenster_tage, "top_n": top_n}


def material_item_abrechnung_setzen(material_id: str, abgerechnet: bool,
                                    abgerechnet_am: Optional[str] = None):
    """Setzt/entfernt das Abgerechnet-Flag einer einzelnen Material-Position (Toggle)."""
    return material.update(material_id, {
        "abgerechnet": bool(abgerechnet),
        "abgerechnet_am": abgerechnet_am if abgerechnet else None,
    })


def auftrag_tag_abrechnung_setzen(auftrag_id: str, datum: str, abgerechnet: bool,
                                  abgerechnet_am: Optional[str] = None) -> int:
    """Setzt/entfernt das Abgerechnet-Flag fuer alle Buchungen eines bestimmten
    Tages eines Auftrags (Toggle / Undo). Liefert die Anzahl geaenderter Buchungen."""
    n = 0
    for z in zeitbuchungen_fuer_auftrag(auftrag_id):
        if (z.get("datum") or "") != datum:
            continue
        zeitbuchungen.update(z["id"], {
            "abgerechnet": bool(abgerechnet),
            "abgerechnet_am": abgerechnet_am if abgerechnet else None,
        })
        n += 1
    return n


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


def zeitbuchungen_im_zeitraum(von_iso: str, bis_iso: str) -> List[Dict[str, Any]]:
    """Alle Zeitbuchungen mit von_iso <= datum <= bis_iso (inklusive Grenzen).
    ISO-Datumsstrings sind lexikografisch vergleichbar."""
    return [
        z for z in zeitbuchungen.list()
        if von_iso <= (z.get("datum") or "") <= bis_iso
    ]


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

    def _last_und_beitrag(node: Dict[str, Any]) -> Optional[float]:
        """Nachgelagerte Last eines Knotens (kW) UND der Beitrag, den er an sein
        Elternteil weitergibt.

        Physik/NIN: Eine Verteilung bzw. Verteilleitung kann an die vorgelagerte
        Verteilung nie mehr weitergeben, als ihre eigene Vorsicherung (Bemessung)
        zulässt. Ist die nachgelagerte Summe grösser als die eigene Bemessung
        (= Überlast, >100 %), wird nach oben nur die Vorsicherung ("Eigene Last")
        weitergegeben. Am Knoten selbst wird die reale nachgelagerte Summe (und
        damit die Überlast) aber weiterhin angezeigt.

        Nur Anlagen- und Endstromkreise zählen mit ihrer eigenen Eingabe als
        Last; Verteilungen/Verteilstromkreise (und Erzeugerstromkreise) werden
        durchlaufen, aber ihre eigene Eingabe nicht als Last aufsummiert.

        Setzt am Knoten: summe_nachgelagert_kw, gleichzeitigkeit,
        belastung_prozent, ueberlastet. Rückgabe: Beitrag nach oben (kW) / None.
        """
        # Beiträge aller Kinder aufsummieren (jeder Beitrag ist ggf. schon
        # auf die Vorsicherung des Kindes gedeckelt).
        kinder_summe = 0.0
        any_value = False
        for child in node["children"]:
            beitrag = _last_und_beitrag(child)
            if beitrag is not None:
                kinder_summe += beitrag
                any_value = True

        summe = kinder_summe if any_value else None
        node["summe_nachgelagert_kw"] = summe

        # Eigene Bemessung/Vorsicherung in kW (aus kW- oder A-Eingabe)
        eigen_kw = node["anzeige"]["kw"]
        node["gleichzeitigkeit"] = (eigen_kw / summe) if (eigen_kw and summe) else None
        # Belastung = nachgelagerte Summe / eigene Vorsicherung (nur Verteiler-Knoten)
        typ = node["teil"].get("typ")
        ist_verteiler = typ in ANLAGENTEIL_TYP_VERTEILER
        node["belastung_prozent"] = (
            (summe / eigen_kw * 100.0) if (ist_verteiler and eigen_kw and summe) else None
        )
        node["ueberlastet"] = False

        if typ in ANLAGENTEIL_TYP_IST_LAST:
            # Endverbraucher: eigene erfasste Last zählt (plus evtl. Kinder).
            eigen_last = teil_last_kw(node["teil"])
            if eigen_last is None and not any_value:
                return None
            return (eigen_last or 0.0) + kinder_summe

        # Verteiler/Pass-Through: nur die nachgelagerte Last fliesst nach oben —
        # bei Überlast gedeckelt auf die eigene Vorsicherung.
        if ist_verteiler and eigen_kw and any_value and kinder_summe > eigen_kw:
            node["ueberlastet"] = True
            return eigen_kw
        return summe

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
        _last_und_beitrag(root)
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

    Gelistet wird jeder Kunde, bei dem tatsächlich noch etwas zu prüfen ist
    (mindestens ein Anlagenteil offen oder mit Mängel). Pro Kunde werden die
    konkreten Anlagenteile mitgeliefert, damit die Übersicht die offenen Punkte
    direkt gruppiert anzeigen kann.
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
        "auftraege_rapport_voll": auftraege_rapport_voll_anzahl(),
    }


def offene_rapport_tage_anzahl(auftrag_id: str) -> int:
    """Anzahl Tage mit noch nicht rapportierten (offenen) Zeitbuchungen eines
    Auftrags. Ab 7 ist ein physisches Rapport-Formular voll und sollte
    eingereicht werden (Teil-Rapport)."""
    return len({
        z.get("datum") for z in zeitbuchungen_fuer_auftrag(auftrag_id)
        if z.get("datum") and not z.get("abgerechnet")
    })


def auftrag_rapport_faellig(auftrag: Dict[str, Any]) -> bool:
    """Ob fuer diesen (noch nicht erledigten) Auftrag ein Teil-Rapport faellig
    ist — 7 oder mehr offene Buchungstage."""
    if auftrag.get("status") not in ("offen", "in_arbeit"):
        return False
    return offene_rapport_tage_anzahl(auftrag["id"]) >= 7


def auftraege_rapport_voll_anzahl() -> int:
    """Anzahl noch nicht erledigter Auftraege, die bereits ein volles 7-Tage-
    Rapport-Formular an offenen (noch nicht rapportierten) Buchungstagen haben —
    d.h. es sollte ein Rapport geschrieben/eingereicht werden, obwohl der
    Auftrag selbst weiterlaeuft."""
    return sum(1 for a in auftraege.list() if auftrag_rapport_faellig(a))
