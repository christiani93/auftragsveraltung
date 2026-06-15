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
    anlagenteile,
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
        "anlagenteil_id": form.get("anlagenteil_id", "").strip() or None,
        "einbauort": form.get("einbauort", "").strip(),
        "hersteller": form.get("hersteller", "").strip(),
        "typ": form.get("typ", "").strip(),
        "seriennr": form.get("seriennr", "").strip(),
        "letzte_wartung": form.get("letzte_wartung", "").strip(),
        "intervall_jahre": form.get("intervall_jahre", "").strip() or str(WARTUNG_INTERVALL_JAHRE_DEFAULT),
        "notizen": form.get("notizen", "").strip(),
    }


def _mit_hierarchie(data: dict) -> dict:
    """Ist ein Anlagenteil gewaehlt, werden Anlage und Kunde daraus abgeleitet —
    so ist der Leistungsschalter eindeutig auf Kunde + Anlagenteil heruntergebrochen."""
    if data.get("anlagenteil_id"):
        teil = anlagenteile.get(data["anlagenteil_id"])
        if teil:
            anlage = anlagen.get(teil.get("anlage_id")) if teil.get("anlage_id") else None
            if anlage:
                data["anlage_id"] = anlage["id"]
                if anlage.get("kunde_id"):
                    data["kunde_id"] = anlage["kunde_id"]
    return data


def _teile_optionen() -> list:
    """Alle Anlagenteile, voll qualifiziert (Kunde · Anlage › [Typ] Bezeichnung)."""
    kidx = {k["id"]: k for k in kunden.list()}
    aidx = {a["id"]: a for a in anlagen.list()}
    opts = []
    for t in anlagenteile.list():
        a = aidx.get(t.get("anlage_id"))
        k = kidx.get(a.get("kunde_id")) if a else None
        label = (f"{k['name'] if k else '—'} · {a['bezeichnung'] if a else '—'} "
                 f"› [{t.get('typ', '')}] {t.get('bezeichnung', '')}")
        opts.append({
            "id": t["id"], "label": label,
            "anlage_id": t.get("anlage_id") or "",
            "kunde_id": (a.get("kunde_id") if a else "") or "",
        })
    opts.sort(key=lambda o: o["label"].lower())
    return opts


def _edit_context(schalter: dict, neu: bool, **extra) -> dict:
    return dict(
        schalter=schalter, neu=neu,
        alle_kunden=sorted(kunden.list(), key=lambda k: k.get("name", "").lower()),
        alle_anlagen=sorted(anlagen.list(), key=lambda a: a.get("bezeichnung", "").lower()),
        teile_optionen=_teile_optionen(),
        default_intervall=WARTUNG_INTERVALL_JAHRE_DEFAULT,
        **extra,
    )


@bp.route("/")
@login_required
def list_schalter():
    kunden_idx = {k["id"]: k for k in kunden.list()}
    anlagen_idx = {a["id"]: a for a in anlagen.list()}
    teile_idx = {t["id"]: t for t in anlagenteile.list()}
    order = {"ueberfaellig": 0, "bald": 1, "ok": 2, "unbekannt": 3}

    # Nach Kunde gruppieren, innerhalb nach Dringlichkeit
    gruppen: dict = {}
    for ls in leistungsschalter.list():
        st = wartung_status(ls)
        kunde = kunden_idx.get(ls.get("kunde_id"))
        row = {
            "ls": ls,
            "kunde": kunde,
            "anlage": anlagen_idx.get(ls.get("anlage_id")),
            "anlagenteil": teile_idx.get(ls.get("anlagenteil_id")),
            **st,
        }
        key = kunde["name"] if kunde else "Ohne Kunde"
        gruppen.setdefault(key, []).append(row)

    gruppen_list = []
    for name in sorted(gruppen, key=lambda n: (n == "Ohne Kunde", n.lower())):
        rows = sorted(gruppen[name], key=lambda r: (order.get(r["status"], 9), r["naechste"] or "9999-12-31", r["ls"].get("bezeichnung", "").lower()))
        gruppen_list.append({
            "kunde": name,
            "rows": rows,
            "faellig": sum(1 for r in rows if r["status"] in ("ueberfaellig", "bald")),
        })
    anzahl_faellig = sum(g["faellig"] for g in gruppen_list)
    return render_template("leistungsschalter/list.html", gruppen=gruppen_list, anzahl_faellig=anzahl_faellig, heute=date.today().isoformat())


@bp.route("/neu", methods=["GET", "POST"])
@login_required
def new_schalter():
    if request.method == "POST":
        data = _mit_hierarchie(_form_to_schalter(request.form))
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
        data = _mit_hierarchie(_form_to_schalter(request.form))
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
