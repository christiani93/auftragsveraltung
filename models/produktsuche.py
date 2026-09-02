"""EAN-/Barcode-Produktsuche (Best-Effort).

Fragt oeffentliche Barcode-Datenbanken nach einem gescannten EAN ab, um die
Artikel-Bezeichnung vorzuschlagen. WICHTIG: oeffentliche DBs kennen vor allem
Konsumgueter — Elektro-Fachhandelsartikel (Wuerth/Elektro-Material) sind dort
haeufig NICHT hinterlegt. Kein Treffer ist also normal; der User erfasst dann
manuell.

Reine Standardbibliothek (urllib) -> laeuft auch auf HostPoint (keine nativen
Wheels). Alle Netzwerkfehler werden geschluckt, damit die Erfassung nie blockt.

Provider-Reihenfolge:
  1. UPCitemdb (Trial-Endpoint, ohne API-Key, ~100 Abfragen/Tag/IP)
  2. OpenGTINDB (nur wenn OPENGTINDB_QUERYID gesetzt ist — kostenlose Registrierung)

Beide sind ueber Env-Vars abschaltbar/konfigurierbar, damit spaeter ein besserer
(ggf. kostenpflichtiger) Dienst eingehaengt werden kann.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

_TIMEOUT = 6  # Sekunden pro Anbieter
_UA = "Auftragsverwaltung-Lagermodul/1.0 (+Produktsuche)"


def _http_get(url: str, headers: Optional[dict] = None) -> Optional[str]:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        return None


def _clean(code: str) -> str:
    return "".join(ch for ch in (code or "") if ch.isalnum())


def _von_upcitemdb(code: str) -> Optional[dict]:
    base = os.environ.get("UPCITEMDB_URL", "https://api.upcitemdb.com/prod/trial/lookup")
    url = f"{base}?upc={urllib.parse.quote(code)}"
    body = _http_get(url)
    if not body:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    items = data.get("items") or []
    if not items:
        return None
    it = items[0]
    titel = (it.get("title") or "").strip()
    marke = (it.get("brand") or "").strip()
    if not titel:
        return None
    return {
        "bezeichnung": titel,
        "marke": marke,
        "quelle": "UPCitemdb",
    }


def _von_opengtindb(code: str) -> Optional[dict]:
    queryid = os.environ.get("OPENGTINDB_QUERYID")
    if not queryid:
        return None
    url = (
        "https://opengtindb.org/?"
        + urllib.parse.urlencode({"ean": code, "cmd": "query", "queryid": queryid})
    )
    body = _http_get(url)
    if not body:
        return None
    # Antwortformat: key=value je Zeile, u.a. 'error=0', 'name=...', 'vendor=...'
    felder = {}
    for zeile in body.splitlines():
        if "=" in zeile:
            k, _, v = zeile.partition("=")
            felder[k.strip()] = v.strip()
    if felder.get("error") not in ("0", None):
        return None
    titel = (felder.get("name") or felder.get("detailname") or "").strip()
    if not titel:
        return None
    return {
        "bezeichnung": titel,
        "marke": (felder.get("vendor") or "").strip(),
        "quelle": "OpenGTINDB",
    }


def ean_lookup(code: str) -> Optional[dict]:
    """Sucht Produktdaten zu einem EAN/Barcode. Gibt bei Treffer ein Dict
    {'bezeichnung', 'marke', 'quelle'} zurueck, sonst None. Wirft nie."""
    clean = _clean(code)
    if len(clean) < 6:  # zu kurz fuer einen sinnvollen EAN
        return None
    if os.environ.get("PRODUKTSUCHE_AKTIV", "1") != "1":
        return None
    for provider in (_von_upcitemdb, _von_opengtindb):
        try:
            treffer = provider(clean)
        except Exception:
            treffer = None
        if treffer:
            return treffer
    return None
