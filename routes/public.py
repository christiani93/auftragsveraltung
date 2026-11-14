"""Oeffentliche, unauthentifizierte Seiten — z.B. QR-Code an einer Verteilung.

Kein Login nötig (siehe app.py require_login(), Whitelist für "public.*").
Zugriff ausschliesslich über den unerratbaren public_token eines Anlagenteils,
NIE über die interne teil_id — sonst liesse sich durch Durchprobieren von IDs
Dokumentation fremder Anlagen abrufen.
"""
from __future__ import annotations

from flask import Blueprint, abort, render_template, send_file

import config
from models.repos import anlagen, anlagenteile

bp = Blueprint("public", __name__)


def _teil_by_token(token: str) -> dict | None:
    if not token:
        return None
    treffer = anlagenteile.filter(public_token=token)
    return treffer[0] if treffer else None


@bp.route("/v/<token>")
def verteilung(token: str):
    teil = _teil_by_token(token)
    if not teil:
        abort(404)
    anlage = anlagen.get(teil.get("anlage_id"))
    anhang = teil.get("anhang") or []
    return render_template(
        "public/verteilung.html",
        teil=teil,
        anlage=anlage,
        schema=[d for d in anhang if d.get("kategorie") == "schema"],
        legende=[d for d in anhang if d.get("kategorie") == "legende"],
        foto=[d for d in anhang if d.get("kategorie") == "foto"],
        token=token,
    )


@bp.route("/v/<token>/datei/<datei_id>")
def verteilung_datei(token: str, datei_id: str):
    teil = _teil_by_token(token)
    if not teil:
        abort(404)
    datei = next((d for d in (teil.get("anhang") or []) if d.get("id") == datei_id), None)
    if not datei:
        abort(404)
    pfad = config.DATA_DIR / "anlagenteil_dateien" / teil["id"] / datei["dateiname"]
    if not pfad.exists():
        abort(404)
    return send_file(str(pfad), mimetype=datei.get("mime") or "application/octet-stream")
