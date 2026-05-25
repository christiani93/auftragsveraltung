"""Messgeräte-Stammdaten — werden im Messprotokoll referenziert."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from models.repos import messgeraete

bp = Blueprint("messgeraete", __name__)


def _form_to_messgeraet(form) -> dict:
    return {
        "bezeichnung": form.get("bezeichnung", "").strip(),
        "hersteller": form.get("hersteller", "").strip(),
        "modell": form.get("modell", "").strip(),
        "seriennr": form.get("seriennr", "").strip(),
        "typ": form.get("typ", "").strip(),
        "kalibrierdatum": form.get("kalibrierdatum", "").strip(),
        "naechste_kalibrierung": form.get("naechste_kalibrierung", "").strip(),
        "notizen": form.get("notizen", "").strip(),
    }


@bp.route("/")
def list_devices():
    alle = sorted(messgeraete.list(), key=lambda m: m.get("bezeichnung", "").lower())
    return render_template("messgeraete/list.html", messgeraete=alle)


@bp.route("/neu", methods=["GET", "POST"])
def new_device():
    if request.method == "POST":
        data = _form_to_messgeraet(request.form)
        if not data["bezeichnung"]:
            flash("Bezeichnung ist erforderlich.", "warning")
            return render_template("messgeraete/edit.html", geraet=data, neu=True)
        record = messgeraete.create(data)
        flash(f"Messgerät „{record['bezeichnung']}“ angelegt.", "success")
        return redirect(url_for("messgeraete.list_devices"))
    return render_template("messgeraete/edit.html", geraet={}, neu=True)


@bp.route("/<geraet_id>/bearbeiten", methods=["GET", "POST"])
def edit_device(geraet_id: str):
    geraet = messgeraete.get(geraet_id)
    if not geraet:
        abort(404)
    if request.method == "POST":
        data = _form_to_messgeraet(request.form)
        if not data["bezeichnung"]:
            flash("Bezeichnung ist erforderlich.", "warning")
            return render_template("messgeraete/edit.html", geraet={**geraet, **data}, neu=False)
        messgeraete.update(geraet_id, data)
        flash("Messgerät gespeichert.", "success")
        return redirect(url_for("messgeraete.list_devices"))
    return render_template("messgeraete/edit.html", geraet=geraet, neu=False)


@bp.route("/<geraet_id>/loeschen", methods=["POST"])
def delete_device(geraet_id: str):
    geraet = messgeraete.get(geraet_id)
    if not geraet:
        abort(404)
    messgeraete.delete(geraet_id)
    flash("Messgerät gelöscht.", "info")
    return redirect(url_for("messgeraete.list_devices"))
