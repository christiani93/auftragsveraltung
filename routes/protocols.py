"""Messprotokolle — Erfassung der Messwerte zur späteren SiNa-Erstellung."""
from __future__ import annotations

from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from models.repos import (
    IO_OPTIONEN,
    MESSPUNKT_FELDER,
    anlagen,
    anlagenteile,
    anlagenteile_fuer_anlage,
    auftraege,
    kunden,
    messgeraete,
    messprotokolle,
)

bp = Blueprint("protocols", __name__)


def _messpunkt_aus_teil(teil: dict, datum: str) -> dict:
    """Erzeugt eine vorausgefüllte Messpunkt-Zeile aus einem Anlagenteil."""
    installation = teil.get("bezeichnung", "")
    if teil.get("beschreibung"):
        installation = f"{installation} – {teil['beschreibung']}"
    return {
        "datum": datum,
        "installation": installation,
        "kabel": teil.get("kabel", ""),
        "sicherungsnr": teil.get("sicherungsnr", ""),
        "sicherungstyp": teil.get("sicherungstyp", ""),
        "fi_typ_ma": teil.get("fi_typ_ma", ""),
        "sichtkontrolle": "",
        "schutzleiter": "",
        "ausloesezeit_ms": "",
        "r_iso_mohm": "",
        "ik_ende_a": "",
        "drehrichtung": "",
        "pruefer": "",
        "bemerkung": "",
    }


def _parse_messpunkte(form) -> list[dict]:
    """Liest die Messpunkt-Zeilen aus dem Formular.

    Alle Felder werden als gleichlange Listen erwartet (messpunkt_<feldname>).
    Leere Zeilen werden verworfen.
    """
    feldnamen = [f["name"] for f in MESSPUNKT_FELDER]
    spalten = {name: form.getlist(f"messpunkt_{name}") for name in feldnamen}
    anzahl = max((len(v) for v in spalten.values()), default=0)
    punkte: list[dict] = []
    for i in range(anzahl):
        zeile = {name: (spalten[name][i] if i < len(spalten[name]) else "").strip() for name in feldnamen}
        if any(v for v in zeile.values()):
            punkte.append(zeile)
    return punkte


@bp.route("/")
def list_protocols():
    alle = messprotokolle.list()
    alle.sort(key=lambda p: p.get("datum", ""), reverse=True)
    anlagen_index = {a["id"]: a for a in anlagen.list()}
    kunden_index = {k["id"]: k for k in kunden.list()}
    rows = []
    for p in alle:
        a = anlagen_index.get(p.get("anlage_id"))
        k = kunden_index.get(a.get("kunde_id")) if a else None
        rows.append({"protokoll": p, "anlage": a, "kunde": k})
    return render_template("protocols/list.html", rows=rows)


@bp.route("/neu", methods=["GET", "POST"])
def new_protocol():
    anlage_id = request.values.get("anlage_id", "")
    auftrag_id = request.values.get("auftrag_id", "")
    anlage = anlagen.get(anlage_id) if anlage_id else None
    auftrag = auftraege.get(auftrag_id) if auftrag_id else None

    if request.method == "POST":
        anlage_id = request.form.get("anlage_id", "")
        anlage = anlagen.get(anlage_id)
        if not anlage:
            flash("Bitte eine Anlage wählen.", "warning")
            return redirect(url_for("protocols.new_protocol"))

        data = {
            "anlage_id": anlage_id,
            "auftrag_id": request.form.get("auftrag_id", "").strip() or None,
            "anlagenteil_id": request.form.get("anlagenteil_id", "").strip() or None,
            "datum": request.form.get("datum", "").strip() or date.today().isoformat(),
            "monteur": request.form.get("monteur", "").strip(),
            "messgeraet_id": request.form.get("messgeraet_id", "").strip() or None,
            "bemerkungen": request.form.get("bemerkungen", "").strip(),
            "messungen": _parse_messpunkte(request.form),
        }
        if not data["messungen"]:
            flash("Mindestens ein Messpunkt muss erfasst werden.", "warning")
            return render_template(
                "protocols/edit.html",
                protokoll=data, anlage=anlage, auftrag=auftrag,
                alle_anlagen=anlagen.list(),
                alle_messgeraete=messgeraete.list(),
                teile_der_anlage=anlagenteile_fuer_anlage(anlage_id) if anlage else [],
                messpunkt_felder=MESSPUNKT_FELDER, io_optionen=IO_OPTIONEN, neu=True,
            )
        record = messprotokolle.create(data)
        flash("Messprotokoll gespeichert.", "success")
        return redirect(url_for("protocols.detail", protokoll_id=record["id"]))

    # Vorausfüllen: wenn Auftrag mitgegeben, eine Zeile pro betroffenem Teil
    # dieser Anlage anlegen mit allen statischen Daten.
    datum_heute = date.today().isoformat()
    if auftrag and anlage:
        teile_idx = {t["id"]: t for t in anlagenteile.list()}
        relevante_teile = [
            teile_idx[tid] for tid in auftrag.get("anlagenteil_ids", [])
            if tid in teile_idx and teile_idx[tid].get("anlage_id") == anlage_id
        ]
        messungen = [_messpunkt_aus_teil(t, datum_heute) for t in relevante_teile]
        if not messungen:
            messungen = [{} for _ in range(3)]
        protokoll = {
            "datum": datum_heute,
            "auftrag_id": auftrag_id,
            "bemerkungen": auftrag.get("titel", ""),
            "messungen": messungen,
        }
    else:
        protokoll = {"datum": datum_heute, "messungen": [{} for _ in range(3)]}

    return render_template(
        "protocols/edit.html",
        protokoll=protokoll,
        anlage=anlage, auftrag=auftrag,
        alle_anlagen=anlagen.list(),
        alle_messgeraete=messgeraete.list(),
        teile_der_anlage=anlagenteile_fuer_anlage(anlage_id) if anlage else [],
        messpunkt_felder=MESSPUNKT_FELDER, io_optionen=IO_OPTIONEN, neu=True,
    )


@bp.route("/<protokoll_id>")
def detail(protokoll_id: str):
    p = messprotokolle.get(protokoll_id)
    if not p:
        abort(404)
    anlage = anlagen.get(p.get("anlage_id"))
    kunde = kunden.get(anlage.get("kunde_id")) if anlage else None
    geraet = messgeraete.get(p.get("messgeraet_id")) if p.get("messgeraet_id") else None
    auftrag = auftraege.get(p.get("auftrag_id")) if p.get("auftrag_id") else None
    return render_template(
        "protocols/detail.html",
        protokoll=p, anlage=anlage, kunde=kunde, geraet=geraet, auftrag=auftrag,
        messpunkt_felder=MESSPUNKT_FELDER,
    )


def _gruppen_spans():
    """Liefert für die Tabellen-Header die Gruppen mit Colspans."""
    result = []
    for f in MESSPUNKT_FELDER:
        if result and result[-1]["name"] == f["gruppe"]:
            result[-1]["span"] += 1
        else:
            result.append({"name": f["gruppe"], "span": 1})
    return result


@bp.app_context_processor
def _inject_messpunkt_groups():
    return {"messpunkt_gruppen": _gruppen_spans()}


@bp.route("/<protokoll_id>/bearbeiten", methods=["GET", "POST"])
def edit_protocol(protokoll_id: str):
    p = messprotokolle.get(protokoll_id)
    if not p:
        abort(404)
    anlage = anlagen.get(p.get("anlage_id"))

    if request.method == "POST":
        data = {
            "anlage_id": request.form.get("anlage_id", p["anlage_id"]),
            "anlagenteil_id": request.form.get("anlagenteil_id", "").strip() or None,
            "datum": request.form.get("datum", "").strip() or p.get("datum"),
            "monteur": request.form.get("monteur", "").strip(),
            "messgeraet_id": request.form.get("messgeraet_id", "").strip() or None,
            "bemerkungen": request.form.get("bemerkungen", "").strip(),
            "messungen": _parse_messpunkte(request.form),
        }
        messprotokolle.update(protokoll_id, data)
        flash("Messprotokoll aktualisiert.", "success")
        return redirect(url_for("protocols.detail", protokoll_id=protokoll_id))

    return render_template(
        "protocols/edit.html",
        protokoll=p, anlage=anlage,
        alle_anlagen=anlagen.list(),
        alle_messgeraete=messgeraete.list(),
        teile_der_anlage=anlagenteile_fuer_anlage(anlage["id"]) if anlage else [],
        messpunkt_felder=MESSPUNKT_FELDER, io_optionen=IO_OPTIONEN, neu=False,
    )


@bp.route("/<protokoll_id>/loeschen", methods=["POST"])
def delete_protocol(protokoll_id: str):
    p = messprotokolle.get(protokoll_id)
    if not p:
        abort(404)
    anlage_id = p.get("anlage_id")
    messprotokolle.delete(protokoll_id)
    flash("Messprotokoll gelöscht.", "info")
    return redirect(url_for("installations.detail", anlage_id=anlage_id) if anlage_id else url_for("protocols.list_protocols"))
