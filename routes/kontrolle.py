"""Übersicht: offene Kontrollen nach Kunden + aktive Revisionen + Eckdaten."""
from __future__ import annotations

from datetime import date

from flask import Blueprint, abort, render_template

from models.repos import (
    KONTROLL_STATUS_LABEL,
    REVISION_STATUS_LABEL,
    anlagenteile,
    auftraege_in_revision,
    dashboard_data,
    kontroll_uebersicht_fuer_kunde,
    kunden,
    leistungsschalter,
    revisionen,
    wartung_status,
)

bp = Blueprint("kontrolle", __name__)


@bp.route("/")
def dashboard():
    data = dashboard_data()

    # Aktive Revisionen (laufend zuerst, dann geplant, abgeschlossene weg)
    kunden_idx = {k["id"]: k for k in kunden.list()}
    rev_rows = []
    for r in revisionen.list():
        if r.get("status") == "abgeschlossen":
            continue
        rev_rows.append({
            "r": r,
            "kunde": kunden_idx.get(r.get("kunde_id")),
            "anzahl_auftraege": len(auftraege_in_revision(r["id"])),
            "anzahl_todos_offen": sum(1 for t in (r.get("todos") or []) if not t.get("erledigt")),
            "anzahl_todos_total": len(r.get("todos") or []),
        })
    rev_rows.sort(key=lambda row: (
        {"laeuft": 0, "geplant": 1}.get(row["r"].get("status", "geplant"), 2),
        row["r"].get("von") or "9999-12-31",
    ))

    # Leistungsschalter-Wartungen, die dieses Jahr anstehen (inkl. überfälliger
    # aus Vorjahren — die müssen ebenfalls noch erledigt werden).
    jahr = date.today().year
    teile_idx = {t["id"]: t for t in anlagenteile.list()}
    wartung_rows = []
    for ls in leistungsschalter.list():
        st = wartung_status(ls)
        if not st["naechste"]:
            continue
        if date.fromisoformat(st["naechste"]).year <= jahr:
            wartung_rows.append({
                "ls": ls,
                "kunde": kunden_idx.get(ls.get("kunde_id")),
                "anlagenteil": teile_idx.get(ls.get("anlagenteil_id")),
                **st,
            })
    wartung_rows.sort(key=lambda r: r["naechste"])

    return render_template(
        "kontrolle/dashboard.html",
        revision_rows=rev_rows,
        revision_status_label=REVISION_STATUS_LABEL,
        kontroll_status_label=KONTROLL_STATUS_LABEL,
        wartung_rows=wartung_rows,
        wartung_jahr=jahr,
        **data,
    )


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
