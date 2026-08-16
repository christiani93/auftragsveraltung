"""Lieferschein-Parser — extrahiert Artikelpositionen aus PDF-Lieferscheinen.

Aktuell unterstuetzt: Elektro-Material AG (EM). Weitere Lieferanten koennen als
zusaetzliche parse_*-Funktion ergaenzt werden.

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
