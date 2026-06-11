"""Revisionen — geplante Wartungs-/Inspektionsphasen pro Kunde.

Eine Revision ist ein 'Grossauftrag' fuer einen Kunden — typischerweise
die zweiwoechigen Betriebsferien — in dem mehrere Aufträge gebuendelt
werden. Pro Revision gibt es eine ToDo-Liste, eine Liste zugeordneter
Aufträge und eine Liste beteiligter Mitarbeiter.

Alle eingeloggten User koennen Revisionen anlegen, aendern, ToDos
verwalten und beteiligte Mitarbeiter eintragen. Beteiligte Mitarbeiter
sehen die in der Revision gebuendelten Aufträge, auch wenn diese ihnen
nicht direkt zugewiesen sind. Auftraege in einer Revision sind aus der
normalen Auftrags-Hauptliste ausgeblendet und nur ueber die Revision
selbst sichtbar.
"""
from __future__ import annotations

import uuid
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from models.repos import (
    AUFTRAG_STATUS_LABEL,
    REVISION_STATUS,
    REVISION_STATUS_LABEL,
    auftraege_in_revision,
    kunden,
    revisionen,
    revisionen_fuer_kunde,
)
from models.users import list_users

bp = Blueprint("revisionen", __name__)


def _form_to_revision(form, kunde_id: str) -> dict:
    status = form.get("status", "geplant")
    if status not in REVISION_STATUS:
        status = "geplant"
    # Mitarbeiter-Liste: nur existierende Usernames akzeptieren (Form-Manipulations-Schutz)
    erlaubte = {u.username for u in list_users()}
    mitarbeiter = [m for m in form.getlist("mitarbeiter") if m in erlaubte]
    return {
        "kunde_id": kunde_id,
        "titel": form.get("titel", "").strip(),
        "von": form.get("von", "").strip() or None,
        "bis": form.get("bis", "").strip() or None,
        "status": status,
        "mitarbeiter": mitarbeiter,
        "notizen": form.get("notizen", "").strip(),
    }


@bp.route("/")
@login_required
def list_revisionen():
    alle = revisionen.list()
    kunden_idx = {k["id"]: k for k in kunden.list()}
    status_filter = request.args.get("status", "").strip()
    if status_filter in REVISION_STATUS:
        alle = [r for r in alle if r.get("status") == status_filter]
    # Sortierung: laufende zuerst, dann geplante (nach von asc), dann abgeschlossene (nach von desc)
    def _sort_key(r):
        status = r.get("status", "geplant")
        order = {"laeuft": 0, "geplant": 1, "abgeschlossen": 2}.get(status, 3)
        return (order, r.get("von") or "9999-12-31")
    alle.sort(key=_sort_key)
    rows = []
    for r in alle:
        rows.append({
            "r": r,
            "kunde": kunden_idx.get(r.get("kunde_id")),
            "anzahl_auftraege": len(auftraege_in_revision(r["id"])),
            "anzahl_todos_offen": sum(1 for t in (r.get("todos") or []) if not t.get("erledigt")),
            "anzahl_todos_total": len(r.get("todos") or []),
        })
    return render_template(
        "revisionen/list.html",
        rows=rows,
        status_label=REVISION_STATUS_LABEL,
        status_optionen=REVISION_STATUS,
        status_filter=status_filter,
    )


@bp.route("/kunden/<kunde_id>/neu", methods=["GET", "POST"])
@login_required
def new_revision(kunde_id: str):
    kunde = kunden.get(kunde_id)
    if not kunde:
        abort(404)
    if request.method == "POST":
        data = _form_to_revision(request.form, kunde_id)
        if not data["titel"]:
            flash("Titel ist erforderlich.", "warning")
            return render_template(
                "revisionen/edit.html",
                revision=data, neu=True, kunde=kunde,
                status_optionen=REVISION_STATUS, status_label=REVISION_STATUS_LABEL,
                alle_user=list_users(),
            )
        data["todos"] = []
        record = revisionen.create(data)
        flash(f"Revision „{record['titel']}“ angelegt.", "success")
        return redirect(url_for("revisionen.detail", revision_id=record["id"]))
    # GET: leeres Formular, Standard-Titel mit Jahr
    default = {
        "titel": f"Revision {date.today().year}",
        "status": "geplant",
    }
    return render_template(
        "revisionen/edit.html",
        revision=default, neu=True, kunde=kunde,
        status_optionen=REVISION_STATUS, status_label=REVISION_STATUS_LABEL,
        alle_user=list_users(),
    )


@bp.route("/<revision_id>")
@login_required
def detail(revision_id: str):
    rev = revisionen.get(revision_id)
    if not rev:
        abort(404)
    kunde = kunden.get(rev.get("kunde_id"))
    zugeordnete = sorted(
        auftraege_in_revision(revision_id),
        key=lambda a: (a.get("status") == "erledigt", a.get("status") == "abgerechnet",
                       a.get("zu_erledigen_bis") or a.get("termin") or "9999"),
    )
    return render_template(
        "revisionen/detail.html",
        revision=rev,
        kunde=kunde,
        auftraege=zugeordnete,
        status_label=REVISION_STATUS_LABEL,
        status_optionen=REVISION_STATUS,
        auftrag_status_label=AUFTRAG_STATUS_LABEL,
        darf_aendern=True,
        alle_user=list_users(),
    )


@bp.route("/<revision_id>/bearbeiten", methods=["GET", "POST"])
@login_required
def edit_revision(revision_id: str):
    rev = revisionen.get(revision_id)
    if not rev:
        abort(404)
    kunde = kunden.get(rev.get("kunde_id"))
    if request.method == "POST":
        data = _form_to_revision(request.form, rev["kunde_id"])
        if not data["titel"]:
            flash("Titel ist erforderlich.", "warning")
            return render_template(
                "revisionen/edit.html",
                revision={**rev, **data}, neu=False, kunde=kunde,
                status_optionen=REVISION_STATUS, status_label=REVISION_STATUS_LABEL,
                alle_user=list_users(),
            )
        revisionen.update(revision_id, data)
        flash("Revision gespeichert.", "success")
        return redirect(url_for("revisionen.detail", revision_id=revision_id))
    return render_template(
        "revisionen/edit.html",
        revision=rev, neu=False, kunde=kunde,
        status_optionen=REVISION_STATUS, status_label=REVISION_STATUS_LABEL,
        alle_user=list_users(),
    )


@bp.route("/<revision_id>/status", methods=["POST"])
@login_required
def set_status(revision_id: str):
    rev = revisionen.get(revision_id)
    if not rev:
        abort(404)
    neuer_status = request.form.get("status", "")
    if neuer_status not in REVISION_STATUS:
        flash("Ungültiger Status.", "warning")
    else:
        revisionen.update(revision_id, {"status": neuer_status})
        flash(f"Status: {REVISION_STATUS_LABEL[neuer_status]}.", "success")
    return redirect(request.referrer or url_for("revisionen.detail", revision_id=revision_id))


@bp.route("/<revision_id>/loeschen", methods=["POST"])
@login_required
def delete_revision(revision_id: str):
    rev = revisionen.get(revision_id)
    if not rev:
        abort(404)
    # Aufträge bleiben bestehen — nur die revision_id-Verknuepfung wird entfernt.
    from models.repos import auftraege as _auftraege
    geloest = 0
    for a in auftraege_in_revision(revision_id):
        _auftraege.update(a["id"], {"revision_id": None})
        geloest += 1
    revisionen.delete(revision_id)
    msg = f"Revision „{rev.get('titel') or ''}“ gelöscht"
    if geloest:
        msg += f" — {geloest} Auftrag/Auftraege wurden entkoppelt (bleiben erhalten)"
    flash(msg + ".", "info")
    kunde_id = rev.get("kunde_id")
    if kunde_id:
        return redirect(url_for("customers.detail", kunde_id=kunde_id))
    return redirect(url_for("revisionen.list_revisionen"))


# ----- ToDos --------------------------------------------------------------

@bp.route("/<revision_id>/todo/neu", methods=["POST"])
@login_required
def add_todo(revision_id: str):
    rev = revisionen.get(revision_id)
    if not rev:
        abort(404)
    text = request.form.get("text", "").strip()
    if not text:
        flash("ToDo-Text ist erforderlich.", "warning")
        return redirect(url_for("revisionen.detail", revision_id=revision_id))
    todos = list(rev.get("todos") or [])
    todos.append({
        "id": uuid.uuid4().hex[:12],
        "text": text,
        "erledigt": False,
    })
    revisionen.update(revision_id, {"todos": todos})
    return redirect(url_for("revisionen.detail", revision_id=revision_id))


@bp.route("/<revision_id>/todo/<todo_id>/toggle", methods=["POST"])
@login_required
def toggle_todo(revision_id: str, todo_id: str):
    rev = revisionen.get(revision_id)
    if not rev:
        abort(404)
    todos = list(rev.get("todos") or [])
    for t in todos:
        if t.get("id") == todo_id:
            t["erledigt"] = not t.get("erledigt", False)
            break
    revisionen.update(revision_id, {"todos": todos})
    return redirect(url_for("revisionen.detail", revision_id=revision_id))


@bp.route("/<revision_id>/todo/<todo_id>/loeschen", methods=["POST"])
@login_required
def delete_todo(revision_id: str, todo_id: str):
    rev = revisionen.get(revision_id)
    if not rev:
        abort(404)
    todos = [t for t in (rev.get("todos") or []) if t.get("id") != todo_id]
    revisionen.update(revision_id, {"todos": todos})
    return redirect(url_for("revisionen.detail", revision_id=revision_id))
