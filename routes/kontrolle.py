"""Kontroll-Übersicht: was steht beim nächsten Besuch des Elektrokontrolleurs an?"""
from __future__ import annotations

from flask import Blueprint, abort, render_template

from models.repos import (
    KONTROLL_STATUS_LABEL,
    dashboard_data,
    kontroll_uebersicht_fuer_kunde,
    kunden,
)

bp = Blueprint("kontrolle", __name__)


@bp.route("/")
def dashboard():
    data = dashboard_data()
    return render_template("kontrolle/dashboard.html", **data)


@bp.route("/kunde/<kunde_id>")
def kunde_uebersicht(kunde_id: str):
    kunde = kunden.get(kunde_id)
    if not kunde:
        abort(404)
    uebersicht = kontroll_uebersicht_fuer_kunde(kunde_id)
    return render_template(
        "kontrolle/kunde.html",
        kontroll_status_label=KONTROLL_STATUS_LABEL,
        **uebersicht,
    )
