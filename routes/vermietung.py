"""Vermietung (intern): Maschinen-Verfügbarkeit + eigene Mitarbeiter-/Ausleiher-
Liste. Ausleihen/Zurücknehmen und Verwaltung sind nur für Verwalter (und Admin);
normale Modul-Nutzer sehen die Verfügbarkeit.
"""
from __future__ import annotations

from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from datetime import datetime

from models.repos import (
    RESERVATION_STATUS_LABEL,
    VERMIET_STATUS,
    VERMIET_STATUS_LABEL,
    mieter,
    mieter_sortiert,
    mietmaschinen,
    mietreservationen,
    reservation_konflikt,
)

bp = Blueprint("vermietung", __name__)


def _require_verwalter():
    if not current_user.ist_vermietung_verwalter:
        abort(403)


# ----- Übersicht --------------------------------------------------------------

@bp.route("/")
@login_required
def liste():
    mieter_idx = {m["id"]: m for m in mieter.list()}
    order = {"ausgeliehen": 0, "wartung": 1, "verfuegbar": 2}
    maschinen = sorted(
        mietmaschinen.list(),
        key=lambda x: (order.get(x.get("status"), 9), x.get("bezeichnung", "").lower()),
    )
    rows = [{"m": x, "mieter": mieter_idx.get(x.get("mieter_id"))} for x in maschinen]
    anzahl_ausgeliehen = sum(1 for x in maschinen if x.get("status") == "ausgeliehen")
    return render_template(
        "vermietung/liste.html",
        rows=rows,
        alle_mieter=mieter_sortiert(),
        status_label=VERMIET_STATUS_LABEL,
        heute=date.today().isoformat(),
        anzahl_ausgeliehen=anzahl_ausgeliehen,
        ist_verwalter=current_user.ist_vermietung_verwalter,
    )


# ----- Ausleihen / Zurücknehmen (Verwalter) -----------------------------------

@bp.route("/maschine/<maschine_id>/ausleihen", methods=["POST"])
@login_required
def ausleihen(maschine_id: str):
    _require_verwalter()
    m = mietmaschinen.get(maschine_id)
    if not m:
        abort(404)
    if m.get("status") != "verfuegbar":
        flash("Maschine ist nicht verfügbar.", "warning")
        return redirect(url_for("vermietung.liste"))
    mieter_id = request.form.get("mieter_id", "").strip()
    if not mieter_id or not mieter.get(mieter_id):
        flash("Bitte einen Mitarbeiter wählen.", "warning")
        return redirect(url_for("vermietung.liste"))
    mietmaschinen.update(maschine_id, {
        "status": "ausgeliehen",
        "mieter_id": mieter_id,
        "ausgeliehen_seit": request.form.get("datum", "").strip() or date.today().isoformat(),
    })
    flash("Maschine als ausgeliehen erfasst.", "success")
    return redirect(url_for("vermietung.liste"))


@bp.route("/maschine/<maschine_id>/zurueck", methods=["POST"])
@login_required
def zurueck(maschine_id: str):
    _require_verwalter()
    m = mietmaschinen.get(maschine_id)
    if not m:
        abort(404)
    mietmaschinen.update(maschine_id, {"status": "verfuegbar", "mieter_id": None, "ausgeliehen_seit": None})
    flash("Rückgabe bestätigt — Maschine wieder verfügbar.", "success")
    return redirect(url_for("vermietung.liste"))


# ----- Maschinen-Stammdaten (Verwalter) ---------------------------------------

def _form_to_maschine(form) -> dict:
    status = form.get("status", "verfuegbar")
    return {
        "bezeichnung": form.get("bezeichnung", "").strip(),
        "inventarnr": form.get("inventarnr", "").strip(),
        "kategorie": form.get("kategorie", "").strip(),
        "status": status if status in VERMIET_STATUS else "verfuegbar",
        "notizen": form.get("notizen", "").strip(),
    }


@bp.route("/maschine/neu", methods=["GET", "POST"])
@login_required
def neue_maschine():
    _require_verwalter()
    if request.method == "POST":
        data = _form_to_maschine(request.form)
        if not data["bezeichnung"]:
            flash("Bezeichnung ist erforderlich.", "warning")
            return render_template("vermietung/maschine_edit.html", maschine=data, neu=True, status_label=VERMIET_STATUS_LABEL, status_optionen=VERMIET_STATUS)
        record = mietmaschinen.create(data)
        flash(f"Maschine {record['bezeichnung']} angelegt.", "success")
        return redirect(url_for("vermietung.liste"))
    return render_template("vermietung/maschine_edit.html", maschine={"status": "verfuegbar"}, neu=True, status_label=VERMIET_STATUS_LABEL, status_optionen=VERMIET_STATUS)


@bp.route("/maschine/<maschine_id>/bearbeiten", methods=["GET", "POST"])
@login_required
def edit_maschine(maschine_id: str):
    _require_verwalter()
    m = mietmaschinen.get(maschine_id)
    if not m:
        abort(404)
    if request.method == "POST":
        data = _form_to_maschine(request.form)
        if not data["bezeichnung"]:
            flash("Bezeichnung ist erforderlich.", "warning")
            return render_template("vermietung/maschine_edit.html", maschine={**m, **data}, neu=False, status_label=VERMIET_STATUS_LABEL, status_optionen=VERMIET_STATUS)
        # Wird der Status weg von 'ausgeliehen' gesetzt, Mieter-Zuordnung loeschen
        if data["status"] != "ausgeliehen":
            data["mieter_id"] = None
            data["ausgeliehen_seit"] = None
        mietmaschinen.update(maschine_id, data)
        flash("Maschine gespeichert.", "success")
        return redirect(url_for("vermietung.liste"))
    return render_template("vermietung/maschine_edit.html", maschine=m, neu=False, status_label=VERMIET_STATUS_LABEL, status_optionen=VERMIET_STATUS)


@bp.route("/maschine/<maschine_id>/loeschen", methods=["POST"])
@login_required
def delete_maschine(maschine_id: str):
    _require_verwalter()
    if not mietmaschinen.get(maschine_id):
        abort(404)
    mietmaschinen.delete(maschine_id)
    flash("Maschine gelöscht.", "info")
    return redirect(url_for("vermietung.liste"))


# ----- Mitarbeiter/Ausleiher-Liste (Verwalter) --------------------------------

@bp.route("/mitarbeiter")
@login_required
def mitarbeiter_liste():
    _require_verwalter()
    return render_template("vermietung/mieter.html", alle_mieter=mieter_sortiert())


@bp.route("/mitarbeiter/neu", methods=["POST"])
@login_required
def neuer_mitarbeiter():
    _require_verwalter()
    name = request.form.get("name", "").strip()
    if not name:
        flash("Name ist erforderlich.", "warning")
        return redirect(url_for("vermietung.mitarbeiter_liste"))
    mieter.create({
        "name": name,
        "notiz": request.form.get("notiz", "").strip(),
        # Fuer spaetere Verknuepfung mit einem Login-Account
        "user_username": request.form.get("user_username", "").strip(),
    })
    flash(f"Mitarbeiter {name} hinzugefügt.", "success")
    return redirect(url_for("vermietung.mitarbeiter_liste"))


@bp.route("/mitarbeiter/<mieter_id>/loeschen", methods=["POST"])
@login_required
def delete_mitarbeiter(mieter_id: str):
    _require_verwalter()
    if not mieter.get(mieter_id):
        abort(404)
    # Maschinen freigeben, die diesem Mitarbeiter zugeordnet sind
    for x in mietmaschinen.list():
        if x.get("mieter_id") == mieter_id:
            mietmaschinen.update(x["id"], {"status": "verfuegbar", "mieter_id": None, "ausgeliehen_seit": None})
    mieter.delete(mieter_id)
    flash("Mitarbeiter gelöscht.", "info")
    return redirect(url_for("vermietung.mitarbeiter_liste"))


# ----- Reservationen (mit Datumsprüfung + Anfrage-Workflow) --------------------

@bp.route("/reservationen")
@login_required
def reservationen():
    maschinen_idx = {m["id"]: m for m in mietmaschinen.list()}
    mieter_idx = {m["id"]: m for m in mieter.list()}

    def _aufbereiten(r):
        return {
            "r": r,
            "maschine": maschinen_idx.get(r.get("maschine_id")),
            "mieter": mieter_idx.get(r.get("mieter_id")),
        }

    alle = [_aufbereiten(r) for r in mietreservationen.list()]
    offen = sorted([a for a in alle if a["r"].get("status") == "angefragt"],
                   key=lambda a: a["r"].get("von", ""))
    bestaetigt = sorted([a for a in alle if a["r"].get("status") == "bestaetigt"],
                        key=lambda a: a["r"].get("von", ""))
    erledigt = sorted([a for a in alle if a["r"].get("status") in ("abgelehnt", "storniert")],
                      key=lambda a: a["r"].get("von", ""), reverse=True)
    return render_template(
        "vermietung/reservationen.html",
        offen=offen, bestaetigt=bestaetigt, erledigt=erledigt,
        maschinen=sorted(mietmaschinen.list(), key=lambda x: x.get("bezeichnung", "").lower()),
        alle_mieter=mieter_sortiert(),
        status_label=RESERVATION_STATUS_LABEL,
        heute=date.today().isoformat(),
        ist_verwalter=current_user.ist_vermietung_verwalter,
    )


@bp.route("/reservationen/neu", methods=["POST"])
@login_required
def reservation_neu():
    maschine_id = request.form.get("maschine_id", "").strip()
    mieter_id = request.form.get("mieter_id", "").strip()
    von = request.form.get("von", "").strip()
    bis = request.form.get("bis", "").strip()
    if not (maschine_id and mietmaschinen.get(maschine_id)):
        flash("Bitte eine Maschine wählen.", "warning")
        return redirect(url_for("vermietung.reservationen"))
    if not (mieter_id and mieter.get(mieter_id)):
        flash("Bitte einen Mitarbeiter wählen.", "warning")
        return redirect(url_for("vermietung.reservationen"))
    if not von or not bis or von > bis:
        flash("Bitte gültigen Zeitraum (von ≤ bis) angeben.", "warning")
        return redirect(url_for("vermietung.reservationen"))

    # Datumsprüfung gegen bestätigte Reservationen — kein Doppelbuchen.
    konflikt = reservation_konflikt(maschine_id, von, bis)
    if konflikt:
        flash(f"Zeitraum kollidiert mit einer bestätigten Reservation ({konflikt.get('von')} – {konflikt.get('bis')}).", "warning")
        return redirect(url_for("vermietung.reservationen"))

    # Verwalter-Anfragen sind direkt bestätigt, sonst 'angefragt'.
    verwalter = current_user.ist_vermietung_verwalter
    mietreservationen.create({
        "maschine_id": maschine_id,
        "mieter_id": mieter_id,
        "von": von,
        "bis": bis,
        "zweck": request.form.get("zweck", "").strip(),
        "status": "bestaetigt" if verwalter else "angefragt",
        "angefragt_von": current_user.username,
        "angefragt_am": datetime.now().isoformat(timespec="seconds"),
        "entschieden_von": current_user.username if verwalter else "",
    })
    flash("Reservation bestätigt." if verwalter else "Reservationsanfrage gesendet — der Verwalter entscheidet.", "success")
    return redirect(url_for("vermietung.reservationen"))


@bp.route("/reservationen/<res_id>/bestaetigen", methods=["POST"])
@login_required
def reservation_bestaetigen(res_id: str):
    _require_verwalter()
    r = mietreservationen.get(res_id)
    if not r:
        abort(404)
    konflikt = reservation_konflikt(r.get("maschine_id"), r.get("von", ""), r.get("bis", ""), ignore_id=res_id)
    if konflikt:
        flash(f"Kann nicht bestätigen — kollidiert mit bestätigter Reservation ({konflikt.get('von')} – {konflikt.get('bis')}).", "warning")
        return redirect(url_for("vermietung.reservationen"))
    mietreservationen.update(res_id, {
        "status": "bestaetigt",
        "entschieden_von": current_user.username,
        "entschieden_am": datetime.now().isoformat(timespec="seconds"),
    })
    flash("Reservation bestätigt.", "success")
    return redirect(url_for("vermietung.reservationen"))


@bp.route("/reservationen/<res_id>/ablehnen", methods=["POST"])
@login_required
def reservation_ablehnen(res_id: str):
    _require_verwalter()
    r = mietreservationen.get(res_id)
    if not r:
        abort(404)
    mietreservationen.update(res_id, {
        "status": "abgelehnt",
        "entschieden_von": current_user.username,
        "entschieden_am": datetime.now().isoformat(timespec="seconds"),
    })
    flash("Reservationsanfrage abgelehnt.", "info")
    return redirect(url_for("vermietung.reservationen"))


@bp.route("/reservationen/<res_id>/stornieren", methods=["POST"])
@login_required
def reservation_stornieren(res_id: str):
    r = mietreservationen.get(res_id)
    if not r:
        abort(404)
    # Eigene Anfrage stornieren, oder Verwalter storniert beliebige.
    if not current_user.ist_vermietung_verwalter and r.get("angefragt_von") != current_user.username:
        abort(403)
    mietreservationen.update(res_id, {"status": "storniert"})
    flash("Reservation storniert.", "info")
    return redirect(url_for("vermietung.reservationen"))
