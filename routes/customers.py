from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from models.repos import (
    AUFTRAG_STATUS_LABEL,
    REVISION_STATUS_LABEL,
    anlagen_fuer_kunde,
    auftrag_sichtbar_fuer,
    auftraege_fuer_kunde,
    auftraege_in_revision,
    ist_mitarbeiter_in_revision,
    kunden,
    revisionen_fuer_kunde,
    todo_hinzufuegen,
    todo_loeschen,
    todo_toggle,
)


def _darf_auftrag_sehen(auftrag: dict) -> bool:
    return auftrag_sichtbar_fuer(auftrag, current_user)

bp = Blueprint("customers", __name__)


def _form_to_kontaktpersonen(form) -> list[dict]:
    namen = form.getlist("kontakt_name[]")
    funktionen = form.getlist("kontakt_funktion[]")
    telefone = form.getlist("kontakt_telefon[]")
    emails = form.getlist("kontakt_email[]")
    result: list[dict] = []
    for i, name in enumerate(namen):
        name = (name or "").strip()
        funktion = (funktionen[i] if i < len(funktionen) else "").strip()
        telefon = (telefone[i] if i < len(telefone) else "").strip()
        email = (emails[i] if i < len(emails) else "").strip()
        if not (name or funktion or telefon or email):
            continue
        result.append({
            "name": name,
            "funktion": funktion,
            "telefon": telefon,
            "email": email,
        })
    return result


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
        "kontaktpersonen": _form_to_kontaktpersonen(form),
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
        [a for a in auftraege_fuer_kunde(kunde_id)
         if _darf_auftrag_sehen(a) and not a.get("revision_id")],
        key=lambda a: (a.get("status") != "offen", a.get("status") != "in_arbeit", a.get("erteilungsdatum", "")),
    )
    # Revisionen mit Counts vorbereiten
    rev_rows = []
    for r in revisionen_fuer_kunde(kunde_id):
        rev_rows.append({
            "r": r,
            "anzahl_auftraege": len(auftraege_in_revision(r["id"])),
            "anzahl_todos_offen": sum(1 for t in (r.get("todos") or []) if not t.get("erledigt")),
            "anzahl_todos_total": len(r.get("todos") or []),
        })
    return render_template(
        "customers/detail.html",
        kunde=kunde,
        anlagen=anlagen_fuer_kunde(kunde_id),
        auftraege=auftraege_dieses_kunden,
        auftrag_status_label=AUFTRAG_STATUS_LABEL,
        revisionen=rev_rows,
        revision_status_label=REVISION_STATUS_LABEL,
        darf_revision_anlegen=current_user.is_authenticated,
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


@bp.route("/<kunde_id>/todo/neu", methods=["POST"])
def add_todo(kunde_id: str):
    if not kunden.get(kunde_id):
        abort(404)
    if not todo_hinzufuegen(kunden, kunde_id, request.form.get("text", "")):
        flash("ToDo-Text ist erforderlich.", "warning")
    return redirect(url_for("customers.detail", kunde_id=kunde_id))


@bp.route("/<kunde_id>/todo/<todo_id>/toggle", methods=["POST"])
def toggle_todo(kunde_id: str, todo_id: str):
    if not kunden.get(kunde_id):
        abort(404)
    todo_toggle(kunden, kunde_id, todo_id)
    return redirect(url_for("customers.detail", kunde_id=kunde_id))


@bp.route("/<kunde_id>/todo/<todo_id>/loeschen", methods=["POST"])
def delete_todo(kunde_id: str, todo_id: str):
    if not kunden.get(kunde_id):
        abort(404)
    todo_loeschen(kunden, kunde_id, todo_id)
    return redirect(url_for("customers.detail", kunde_id=kunde_id))


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
