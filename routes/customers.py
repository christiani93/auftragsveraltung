from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from models.repos import (
    AUFTRAG_STATUS_LABEL,
    anlagen_fuer_kunde,
    auftraege_fuer_kunde,
    kunden,
)

bp = Blueprint("customers", __name__)


def _form_to_kunde(form) -> dict:
    return {
        "name": form.get("name", "").strip(),
        "adresse": form.get("adresse", "").strip(),
        "plz": form.get("plz", "").strip(),
        "ort": form.get("ort", "").strip(),
        "telefon": form.get("telefon", "").strip(),
        "email": form.get("email", "").strip(),
        "ist_stammkunde": form.get("ist_stammkunde") == "on",
        "kontroll_intervall_monate": int(form.get("kontroll_intervall_monate") or 6),
        "notizen": form.get("notizen", "").strip(),
    }


@bp.route("/")
def list_customers():
    alle = sorted(kunden.list(), key=lambda k: k["name"].lower())
    return render_template("customers/list.html", kunden=alle)


@bp.route("/neu", methods=["GET", "POST"])
def new_customer():
    if request.method == "POST":
        data = _form_to_kunde(request.form)
        if not data["name"]:
            flash("Name ist erforderlich.", "warning")
            return render_template("customers/edit.html", kunde=data, neu=True)
        record = kunden.create(data)
        flash(f"Kunde „{record['name']}“ angelegt.", "success")
        return redirect(url_for("customers.detail", kunde_id=record["id"]))
    return render_template("customers/edit.html", kunde={}, neu=True)


@bp.route("/<kunde_id>")
def detail(kunde_id: str):
    kunde = kunden.get(kunde_id)
    if not kunde:
        abort(404)
    auftraege_dieses_kunden = sorted(
        auftraege_fuer_kunde(kunde_id),
        key=lambda a: (a.get("status") != "offen", a.get("status") != "in_arbeit", a.get("erteilungsdatum", "")),
    )
    return render_template(
        "customers/detail.html",
        kunde=kunde,
        anlagen=anlagen_fuer_kunde(kunde_id),
        auftraege=auftraege_dieses_kunden,
        auftrag_status_label=AUFTRAG_STATUS_LABEL,
    )


@bp.route("/<kunde_id>/bearbeiten", methods=["GET", "POST"])
def edit_customer(kunde_id: str):
    kunde = kunden.get(kunde_id)
    if not kunde:
        abort(404)
    if request.method == "POST":
        data = _form_to_kunde(request.form)
        if not data["name"]:
            flash("Name ist erforderlich.", "warning")
            return render_template("customers/edit.html", kunde={**kunde, **data}, neu=False)
        kunden.update(kunde_id, data)
        flash("Änderungen gespeichert.", "success")
        return redirect(url_for("customers.detail", kunde_id=kunde_id))
    return render_template("customers/edit.html", kunde=kunde, neu=False)


@bp.route("/<kunde_id>/loeschen", methods=["POST"])
def delete_customer(kunde_id: str):
    kunde = kunden.get(kunde_id)
    if not kunde:
        abort(404)
    if anlagen_fuer_kunde(kunde_id):
        flash("Kunde hat noch Anlagen — bitte zuerst entfernen.", "warning")
        return redirect(url_for("customers.detail", kunde_id=kunde_id))
    kunden.delete(kunde_id)
    flash(f"Kunde „{kunde['name']}“ gelöscht.", "info")
    return redirect(url_for("customers.list_customers"))
