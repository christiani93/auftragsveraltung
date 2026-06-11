"""Messgeräte-Stammdaten — werden im Messprotokoll referenziert.

User sehen nur ihre eigenen Messgeräte. Admin sieht alle.
"""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from models.repos import messgeraete, messgeraete_fuer_user
from models.users import list_users

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


def _user_darf_geraet_aendern(geraet: dict) -> bool:
    """User darf eigene Geraete und alte ownerlose Geraete editieren; Admin alles."""
    if current_user.is_admin:
        return True
    if not geraet.get("owner"):
        return True
    return geraet.get("owner") == current_user.username


@bp.route("/")
def list_devices():
    sichtbar = messgeraete_fuer_user(current_user.username, current_user.is_admin)
    sichtbar = sorted(sichtbar, key=lambda m: m.get("bezeichnung", "").lower())
    return render_template(
        "messgeraete/list.html",
        messgeraete=sichtbar,
        is_admin=current_user.is_admin,
    )


@bp.route("/neu", methods=["GET", "POST"])
def new_device():
    if request.method == "POST":
        data = _form_to_messgeraet(request.form)
        if not data["bezeichnung"]:
            flash("Bezeichnung ist erforderlich.", "warning")
            return render_template("messgeraete/edit.html", geraet=data, neu=True, is_admin=current_user.is_admin)
        # Owner = aktueller User (Admin kann spaeter umtragen)
        data["owner"] = current_user.username
        record = messgeraete.create(data)
        flash(f"Messgerät „{record['bezeichnung']}“ angelegt.", "success")
        return redirect(url_for("messgeraete.list_devices"))
    return render_template("messgeraete/edit.html", geraet={}, neu=True, is_admin=current_user.is_admin)


@bp.route("/<geraet_id>/bearbeiten", methods=["GET", "POST"])
def edit_device(geraet_id: str):
    geraet = messgeraete.get(geraet_id)
    if not geraet:
        abort(404)
    if not _user_darf_geraet_aendern(geraet):
        abort(403)
    if request.method == "POST":
        data = _form_to_messgeraet(request.form)
        if not data["bezeichnung"]:
            flash("Bezeichnung ist erforderlich.", "warning")
            return render_template(
                "messgeraete/edit.html",
                geraet={**geraet, **data}, neu=False, is_admin=current_user.is_admin,
                alle_user=list_users(),
            )
        # Admin darf owner umtragen (auch auf leer = ohne Besitzer), normale User nicht
        if current_user.is_admin:
            data["owner"] = request.form.get("owner", "").strip()
        messgeraete.update(geraet_id, data)
        flash("Messgerät gespeichert.", "success")
        return redirect(url_for("messgeraete.list_devices"))
    return render_template(
        "messgeraete/edit.html",
        geraet=geraet, neu=False, is_admin=current_user.is_admin,
        alle_user=list_users(),
    )


@bp.route("/<geraet_id>/loeschen", methods=["POST"])
def delete_device(geraet_id: str):
    geraet = messgeraete.get(geraet_id)
    if not geraet:
        abort(404)
    if not _user_darf_geraet_aendern(geraet):
        abort(403)
    messgeraete.delete(geraet_id)
    flash("Messgerät gelöscht.", "info")
    return redirect(url_for("messgeraete.list_devices"))
