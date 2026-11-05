"""Lieferschein-Parser — extrahiert Artikelpositionen aus PDF-Lieferscheinen.

Aktuell unterstuetzt: Elektro-Material AG (EM) und Wuerth AG. Weitere Lieferanten
koennen als zusaetzliche parse_*-Funktion ergaenzt werden.

Wichtige Eigenheiten des EM-Formats (siehe Beispiel LS 2201171):
- Pro Position eine Kopfzeile:
    <pos> <EM-Artikel-Nr> <E-Nummer 3 3 3> <bestellt> <liefermenge> [<gebinde-nr> <gebinde-text>]
  Anker ist die E-Nummer (drei Dreiergruppen). Fehlt die Liefermenge > 0, fehlt
  auch die Gebinde-Angabe (Rueckstand).
- Danach eine oder mehr Textzeilen (Artikeltext). Die ERSTE Textzeile endet mit
  <Warengruppe dd.dd> <Einheit>. Weitere Zeilen sind reiner Beschreibungstext.
- Optional eine Zeile "Rueckstand: <menge> <einheit> geplanter Termin: <dd.mm.yy>".
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


# Kopfzeile einer Position. Die E-Nummer (3x3 Ziffern) ist der stabile Anker;
# der abschliessende $ + der strikte Schwanz (zwei Mengen + optional Gebinde)
# sorgen dafuer, dass Regex-Backtracking die richtige E-Nummer waehlt, auch wenn
# der Artikelcode selbst ziffernhaltig ist.
_POS_RE = re.compile(
    r"^\s*(\d+)\s+"                      # 1: Position
    r"(.+?)\s+"                          # 2: EM-Artikel-Nummer (Code)
    r"(\d{3}\s\d{3}\s\d{3})\s+"          # 3: E-Nummer
    r"([\d.]+)\s+"                       # 4: Bestellt
    r"([\d.]+)"                          # 5: Liefermenge
    r"(?:\s+(\d{6}\.\d{4})\s+(.+))?"     # 6/7: Gebinde-Nr + Gebinde-Text (optional)
    r"\s*$"
)

# Warengruppe (dd.dd) + Einheit am Ende der ersten Textzeile.
_TEXT_META_RE = re.compile(r"^(.*?)\s+(\d{2}\.\d{2})\s+(\S+)\s*$")

_RUECKSTAND_RE = re.compile(
    r"R[uü]ckstand:\s*([\d.]+)\s+\S+\s+geplanter\s+Termin:\s*(\d{2}\.\d{2}\.\d{2})",
    re.IGNORECASE,
)

_KOPF_NR_RE = re.compile(r"Nr\.\s*(\d+)\s*/\s*(\d+)\s+vom\s+(\d{2}\.\d{2}\.\d{2})")

# Kopf-/Fusszeilen die auf Folgeseiten zwischen Positionen stehen und NICHT zum
# Artikeltext gehoeren. Werden innerhalb einer Position uebersprungen.
_SKIP_RE = re.compile(
    r"^(?:Lieferschein|Nr\.\s|Objekt:|EM-Artikel-Nummer|Artikeltext|Elektro-Material|www\.)",
    re.IGNORECASE,
)
# Gebinde-Summenzeile am Dokumentende, z.B.
# "kleine Tragtasche: 1 Diverses: 1 Karton: 2 Karton Nr.2: 1"
_GEBINDE_SUMME_RE = re.compile(r"^(?:\S[\S ]*?:\s*\d+)(?:\s+\S[\S ]*?:\s*\d+)+\s*$")


def _to_float(text: str) -> Optional[float]:
    try:
        return float(text.replace(",", "."))
    except (ValueError, AttributeError):
        return None


def _dd_mm_yy_to_iso(text: str) -> Optional[str]:
    """'20.07.26' -> '2026-07-20'. Zweistelliges Jahr -> 2000+."""
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{2})$", text or "")
    if not m:
        return None
    tag, monat, jahr = m.groups()
    return f"20{jahr}-{monat}-{tag}"


def _extract_text(pdf_source) -> str:
    """Extrahiert den Text aus einem PDF (Pfad oder file-like/BytesIO).
    Nutzt pypdf (pure Python — keine nativen Abhaengigkeiten, HostPoint-tauglich)."""
    from pypdf import PdfReader
    reader = PdfReader(pdf_source)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def parse_em_lieferschein(pdf_path: str) -> Dict[str, Any]:
    """Parst einen Elektro-Material-AG-Lieferschein.

    Liefert {"lieferschein_nr", "lieferschein_datum" (ISO), "positionen": [...]}.
    Jede Position: position, em_artikel_nr, e_nummer, artikeltext, warengruppe,
    einheit, menge_bestellt, menge_geliefert, gebinde, ruckstand, ruckstand_termin.
    """
    text = _extract_text(pdf_path)
    zeilen = [z.rstrip() for z in text.splitlines()]

    lieferschein_nr = ""
    lieferschein_datum = None
    positionen: List[Dict[str, Any]] = []
    aktuell: Optional[Dict[str, Any]] = None
    meta_gesetzt = False  # Warengruppe/Einheit der ersten Textzeile schon erfasst?

    def _abschliessen():
        if aktuell is not None:
            aktuell["artikeltext"] = " ".join(aktuell["_textteile"]).strip()
            del aktuell["_textteile"]
            positionen.append(aktuell)

    for zeile in zeilen:
        if not lieferschein_nr:
            mk = _KOPF_NR_RE.search(zeile)
            if mk:
                lieferschein_nr = f"{mk.group(1)} / {mk.group(2)}"
                lieferschein_datum = _dd_mm_yy_to_iso(mk.group(3))

        m = _POS_RE.match(zeile)
        if m:
            _abschliessen()
            gebinde = ""
            if m.group(7):
                gebinde = m.group(7).strip()
            aktuell = {
                "position": int(m.group(1)),
                "em_artikel_nr": m.group(2).strip(),
                "e_nummer": m.group(3),
                "menge_bestellt": _to_float(m.group(4)),
                "menge_geliefert": _to_float(m.group(5)),
                "gebinde": gebinde,
                "warengruppe": "",
                "einheit": "",
                "ruckstand": None,
                "ruckstand_termin": None,
                "_textteile": [],
            }
            meta_gesetzt = False
            continue

        if aktuell is None:
            continue  # Kopf-/Fusszeilen vor der ersten Position ignorieren

        # Seiten-Kopf/-Fuss zwischen Positionen: nicht in den Artikeltext ziehen.
        if _SKIP_RE.match(zeile) or _GEBINDE_SUMME_RE.match(zeile):
            continue

        mr = _RUECKSTAND_RE.search(zeile)
        if mr:
            aktuell["ruckstand"] = _to_float(mr.group(1))
            aktuell["ruckstand_termin"] = _dd_mm_yy_to_iso(mr.group(2))
            continue

        # Textzeile: erste Zeile traegt Warengruppe + Einheit am Ende.
        if not meta_gesetzt:
            mt = _TEXT_META_RE.match(zeile)
            if mt:
                aktuell["_textteile"].append(mt.group(1).strip())
                aktuell["warengruppe"] = mt.group(2)
                aktuell["einheit"] = mt.group(3)
                meta_gesetzt = True
                continue
        # Weitere reine Beschreibungszeile
        if zeile.strip():
            aktuell["_textteile"].append(zeile.strip())

    _abschliessen()
    return {
        "lieferschein_nr": lieferschein_nr,
        "lieferschein_datum": lieferschein_datum,
        "positionen": positionen,
    }


# ----- Wuerth AG --------------------------------------------------------------
#
# Eigenheiten des Wuerth-Formats (siehe Beispiel LS 8173918160):
# - Jede Position ist ein Zeilen-Block. Davor stehen drei reine Ziffernzeilen
#   (Pos. Lief / Pos. Auftrag / Ihre Pos), dann die Artikelnummer-Zeile.
# - Die Artikelnummer-Zeile besteht NUR aus Ziffern und Leerzeichen, z.B.
#   "09035  25 068  5400". Das letzte Token ist die VE (Verpackungseinheit),
#   davor steht die eigentliche Artikelnummer (ggf. mit Binnen-Leerzeichen).
# - Danach 1-3 Textzeilen (Bezeichnung + Kurzcode + evtl. Herkunft), dann die
#   statistische Warennummer (>=6 Ziffern), dann "<Menge> <Einheit>" (z.B.
#   "1'000 ST") und "<Gewicht> KG".
# - Der Abschnitt "Wir bedauern ... nicht lieferbar" listet Rueckstaende
#   (NICHT gelieferte Mengen) — der wird NICHT mitgezaehlt.

_WUERTH_MARKER_RE = re.compile(r"w[üue]rth", re.IGNORECASE)
_WUERTH_LSNR_RE = re.compile(r"Lieferungsnummer\s+(\d+)")
_WUERTH_DATUM_RE = re.compile(r"Erstellung\s+Lieferschein\s+(\d{2})\.(\d{2})\.(\d{4})")
# Ab hier nicht mehr weiterzaehlen: Dokument-Ende bzw. Rueckstands-Abschnitt.
_WUERTH_STOP_RE = re.compile(r"Gesamtgewicht|nicht\s+lieferbar|bedauern", re.IGNORECASE)

_ZIFFERN_ZEILE_RE = re.compile(r"^\d[\d ]*\d$")   # nur Ziffern + Leerzeichen, >=2 Zeichen
_INT_ZEILE_RE = re.compile(r"^\d+$")
_WARENNR_RE = re.compile(r"^\d{6,}$")             # statistische Warennummer (8-stellig)
# "<Menge> <Einheit>" am Zeilenende, z.B. "1'000 ST" / "12 ST".
_MENGE_ZEILE_RE = re.compile(r"^([\d'’.  ]+?)\s+([A-Za-zÄÖÜ]+)$")
# Gewichtszeile beendet die Position, z.B. "0.437 KG" oder "5.7 KG UN 1950"
# (Gefahrgut-Zusatz am Ende). Beginnt mit Zahl, gefolgt von "KG".
_GEWICHT_ZEILE_RE = re.compile(r"^[\d'’. ][\d'’.  ]*\s+KG\b", re.IGNORECASE)


def _wuerth_menge(text: str) -> Optional[float]:
    """'1'000' / '1’000' / '125' -> float. Schweizer Tausender-Apostroph weg."""
    bereinigt = (text or "").replace("'", "").replace("’", "").replace(" ", "").strip()
    return _to_float(bereinigt)


def _dd_mm_yyyy_to_iso(tag: str, monat: str, jahr: str) -> str:
    return f"{jahr}-{monat}-{tag}"


def parse_wuerth_lieferschein(pdf_source) -> Dict[str, Any]:
    """Parst einen Wuerth-Lieferschein.

    Liefert {"lieferant": "Würth", "lieferschein_nr", "lieferschein_datum" (ISO),
    "positionen": [...]}. Jede Position: artikelnummer (ohne VE), ve, bezeichnung,
    kurzcode, menge (geliefert), einheit.
    """
    text = _extract_text(pdf_source)
    zeilen = [z.strip() for z in text.splitlines()]
    n = len(zeilen)

    lieferschein_nr = ""
    lieferschein_datum: Optional[str] = None
    positionen: List[Dict[str, Any]] = []

    i = 0
    while i < n:
        zeile = zeilen[i]
        if not lieferschein_nr:
            m = _WUERTH_LSNR_RE.search(zeile)
            if m:
                lieferschein_nr = m.group(1)
        if lieferschein_datum is None:
            m = _WUERTH_DATUM_RE.search(zeile)
            if m:
                lieferschein_datum = _dd_mm_yyyy_to_iso(*m.groups())

        if _WUERTH_STOP_RE.search(zeile):
            break

        # Artikelnummer-Zeile erkennen: nur Ziffern/Leerzeichen, >=2 Tokens,
        # davor eine reine Ziffernzeile (Ihre Pos.), danach Text (Bezeichnung).
        if (_ZIFFERN_ZEILE_RE.match(zeile) and len(zeile.split()) >= 2
                and i > 0 and _INT_ZEILE_RE.match(zeilen[i - 1])
                and i + 1 < n and re.search(r"[A-Za-zÄÖÜ]", zeilen[i + 1])):
            tokens = zeile.split()
            ve = tokens[-1]
            artikelnummer = " ".join(tokens[:-1])

            desc: List[str] = []
            menge: Optional[float] = None
            einheit = ""
            j = i + 1
            while j < n:
                lj = zeilen[j]
                if _WUERTH_STOP_RE.search(lj):
                    break
                if _WARENNR_RE.match(lj):
                    j += 1
                    continue
                if _GEWICHT_ZEILE_RE.match(lj):
                    j += 1  # Gewichtszeile -> Position endet
                    break
                mm = _MENGE_ZEILE_RE.match(lj)
                if mm and menge is None and mm.group(2).upper() != "KG":
                    menge = _wuerth_menge(mm.group(1))
                    einheit = mm.group(2)
                    j += 1
                    continue
                # sonst: Beschreibungstext
                if lj:
                    desc.append(lj)
                j += 1

            positionen.append({
                "artikelnummer": artikelnummer,
                "ve": ve,
                "bezeichnung": desc[0] if desc else "",
                "kurzcode": " ".join(desc[1:]).strip(),
                "menge": menge,
                "einheit": einheit or "ST",
            })
            i = j
            continue
        i += 1

    return {
        "lieferant": "Würth",
        "lieferschein_nr": lieferschein_nr,
        "lieferschein_datum": lieferschein_datum,
        "positionen": positionen,
    }


# ----- Einheitlicher Import fuer das Lager-Modul ------------------------------

def normalisiere_bestellnr(wert: Optional[str]) -> str:
    """Vergleichsschluessel fuer Bestell-/Artikelnummern: nur Alphanumerik,
    kleingeschrieben (Leerzeichen, Punkte, Bindestriche egal). So matcht
    '09035 25 068' == '0903525068' == '09035-25-068'."""
    if not wert:
        return ""
    return re.sub(r"[^0-9a-z]", "", str(wert).lower())


def erkenne_lieferant(pdf_source) -> str:
    """Best-Effort-Erkennung des Lieferanten anhand des PDF-Texts.
    Rueckgabe: 'wuerth' | 'em' | '' (unbekannt). pdf_source wird einmal gelesen;
    file-like Objekte bitte danach zuruecksetzen (seek(0))."""
    text = _extract_text(pdf_source)
    if _WUERTH_MARKER_RE.search(text) or "Lieferungsnummer" in text:
        return "wuerth"
    if re.search(r"Elektro-?Material|EM-Artikel-Nummer", text, re.IGNORECASE):
        return "em"
    return ""


def parse_lieferschein_fuers_lager(pdf_source, lieferant_hint: str = "") -> Dict[str, Any]:
    """Parst einen Lieferschein (Wuerth oder EM) in eine einheitliche Form fuers
    Lager-Modul:

        {"lieferant", "nummer", "datum" (ISO|None), "positionen": [
            {"bestellnummer", "bezeichnung", "menge", "einheit"} ]}

    'menge' ist die GELIEFERTE Menge (Rueckstaende zaehlen nicht). pdf_source darf
    ein Pfad oder BytesIO sein; bei BytesIO wird fuer die Erkennung zurueckgespult.
    """
    hint = (lieferant_hint or "").strip().lower()
    if hint not in ("wuerth", "würth", "em"):
        hint = erkenne_lieferant(pdf_source)
        if hasattr(pdf_source, "seek"):
            pdf_source.seek(0)
    hint = "wuerth" if hint in ("wuerth", "würth") else hint

    if hint == "em":
        roh = parse_em_lieferschein(pdf_source)
        positionen = [{
            "bestellnummer": (p.get("e_nummer") or "").strip(),
            "bezeichnung": (p.get("artikeltext") or "").strip(),
            "menge": p.get("menge_geliefert"),
            "einheit": (p.get("einheit") or "").strip(),
        } for p in roh.get("positionen", [])]
        return {
            "lieferant": "Elektro-Material AG",
            "nummer": roh.get("lieferschein_nr") or "",
            "datum": roh.get("lieferschein_datum"),
            "positionen": [p for p in positionen if p["bestellnummer"] or p["bezeichnung"]],
        }

    # Default/Fallback: Wuerth
    roh = parse_wuerth_lieferschein(pdf_source)
    positionen = [{
        "bestellnummer": (p.get("artikelnummer") or "").strip(),
        "bezeichnung": (" ".join(x for x in (p.get("bezeichnung"), p.get("kurzcode")) if x)).strip(),
        "menge": p.get("menge"),
        "einheit": (p.get("einheit") or "").strip(),
    } for p in roh.get("positionen", [])]
    return {
        "lieferant": roh.get("lieferant") or "Würth",
        "nummer": roh.get("lieferschein_nr") or "",
        "datum": roh.get("lieferschein_datum"),
        "positionen": [p for p in positionen if p["bestellnummer"] or p["bezeichnung"]],
    }
