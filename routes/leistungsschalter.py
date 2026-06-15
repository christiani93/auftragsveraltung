"""Leistungsschalter-Wartung — Wartung muss alle 5 Jahre durchgeführt werden.

Verwaltet Leistungsschalter (optional einem Kunden/einer Anlage zugeordnet) mit
letztem Wartungsdatum + Intervall. Die Übersicht zeigt Fälligkeiten (überfällig
zuerst). Mit „Wartung erfassen" wird das letzte Wartungsdatum gesetzt.
"""
from __future__ import annotations

from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from models.repos import (
    WARTUNG_INTERVALL_JAHRE_DEFAULT,
    anlagen,
    kunden,
    leistungsschalter,
    wartung_status,
)

bp = Blueprint("leistungsschalter", __name__)


def _form_to_schalter(form) -> dict:
    return {
        "bezeichnung": form.get("bezeichnung", "").strip(),
        "kunde_id": form.get("kunde_id", "").strip() or None,
        "anlage_id": form.get("anlage_id", "").strip() or None,
        "einbauort": form.get("einbauort", "").strip(),
        "hersteller": form.get("hersteller", "").strip(),
        "typ": form.get("typ", "").strip(),
        "seriennr": form.get("seriennr", "").strip(),
        "letzte_wartung": form.get("letzte_wartung", "").strip(),
        "intervall_jahre": form.get("intervall_jahre", "").strip() or str(WARTUNG_INTERVALL_JAHRE_DEFAULT),
        "notizen": form.get("notizen", "").strip(),
    }


def _edit_context(schalter: dict, neu: bool, **extra) -> dict:
    return dict(
        schalter=schalter, neu=neu,
        alle_kunden=sorted(kunden.list(), key=lambda k: k.get("name", "").lower()),
        alle_anlagen=sorted(anlagen.list(), key=lambda a: a.get("bezeichnung", "").lower()),
        default_intervall=WARTUNG_INTERVALL_JAHRE_DEFAULT,
        **extra,
    )


@bp.route("/")
@login_required
def list_schalter():
    kunden_idx = {k["id"]: k for k in kunden.list()}
    anlagen_idx = {a["id"]: a for a in anlagen.list()}
    rows = []
    for ls in leistungsschalter.list():
        st = wartung_status(ls)
        rows.append({
            "ls": ls,
            "kunde": kunden_idx.get(ls.get("kunde_id")),
            "anlage": anlagen_idx.get(ls.get("anlage_id")),
            **st,
        })
    order = {"ueberfaellig": 0, "bald": 1, "ok": 2, "unbekannt": 3}
    rows.sort(key=lambda r: (order.get(r["status"], 9), r["naechste"] or "9999-12-31", r["ls"].get("bezeichnung", "").lower()))
    anzahl_faellig = sum(1 for r in rows if r["status"] in ("ueberfaellig", "bald"))
    return render_template("leistungsschalter/list.html", rows=rows, anzahl_faellig=anzahl_faellig, heute=date.today().isoformat())


@bp.route("/neu", methods=["GET", "POST"])
@login_required
def new_schalter():
    if request.method == "POST":
        data = _form_to_schalter(request.form)
        if not data["bezeichnung"]:
            flash("Bezeichnung ist erforderlich.", "warning")
            return render_template("leistungsschalter/edit.html", **_edit_context(data, neu=True))
        record = leistungsschalter.create(data)
        flash(f"Leistungsschalter {record['bezeichnung']} angelegt.", "success")
        return redirect(url_for("leistungsschalter.list_schalter"))
    return render_template("leistungsschalter/edit.html", **_edit_context({"intervall_jahre": str(WARTUNG_INTERVALL_JAHRE_DEFAULT)}, neu=True))


@bp.route("/<schalter_id>/bearbeiten", methods=["GET", "POST"])
@login_required
def edit_schalter(schalter_id: str):
    schalter = leistungsschalter.get(schalter_id)
    if not schalter:
        abort(404)
    if request.method == "POST":
        data = _form_to_schalter(request.form)
        if not data["bezeichnung"]:
            flash("Bezeichnung ist erforderlich.", "warning")
            return render_template("leistungsschalter/edit.html", **_edit_context({**schalter, **data}, neu=False))
        leistungsschalter.update(schalter_id, data)
        flash("Leistungsschalter gespeichert.", "success")
        return redirect(url_for("leistungsschalter.list_schalter"))
    return render_template("leistungsschalter/edit.html", **_edit_context(schalter, neu=False))


@bp.route("/<schalter_id>/wartung", methods=["POST"])
@login_required
def wartung_erfassen(schalter_id: str):
    """Wartung durchgeführt: letztes Wartungsdatum setzen (Default heute)."""
    schalter = leistungsschalter.get(schalter_id)
    if not schalter:
        abort(404)
    datum = request.form.get("datum", "").strip() or date.today().isoformat()
    leistungsschalter.update(schalter_id, {"letzte_wartung": datum})
    flash(f"Wartung erfasst ({datum}). Nächste Wartung in {schalter.get('intervall_jahre') or WARTUNG_INTERVALL_JAHRE_DEFAULT} Jahren.", "success")
    return redirect(request.referrer or url_for("leistungsschalter.list_schalter"))


@bp.route("/<schalter_id>/loeschen", methods=["POST"])
@login_required
def delete_schalter(schalter_id: str):
    schalter = leistungsschalter.get(schalter_id)
    if not schalter:
        abort(404)
    leistungsschalter.delete(schalter_id)
    flash("Leistungsschalter gelöscht.", "info")
    return redirect(url_for("leistungsschalter.list_schalter"))
