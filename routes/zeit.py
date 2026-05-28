"""Stempelung (Komm/Geh) + Tagesansicht der Zeiterfassung.

Stempeln Start (auf einem Auftrag) → Eintrag in stempelung.json.
Stempeln Stop → Eintrag wird in eine Zeitbuchung (zeitbuchungen.json)
ueberfuehrt, mit Datum + Von/Bis + Dauer.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from models.repos import (
    aktive_stempelung_von,
    alle_aktiven_stempelungen,
    auftraege,
    kunden,
    stempelungen,
    zeitbuchungen,
    zeitbuchungen_am_tag,
)

bp = Blueprint("zeit", __name__)


def _darf_auftrag_sehen(auftrag: dict) -> bool:
    if not current_user.is_authenticated:
        return False
    if current_user.sieht_alle_auftraege:
        return True
    zugewiesen = (auftrag.get("zugewiesen_an") or "").strip()
    if not zugewiesen:
        return True
    return zugewiesen.lower() == current_user.username.lower()


def _parse_dt(value: str) -> datetime:
    """ISO-Datetime parsen (mit Fallback auf jetzt)."""
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return datetime.now()


@bp.route("/stempel/start", methods=["POST"])
@login_required
def stempel_start():
    auftrag_id = request.form.get("auftrag_id", "").strip()
    auftrag = auftraege.get(auftrag_id) if auftrag_id else None
    if not auftrag:
        flash("Auftrag nicht gefunden.", "warning")
        return redirect(request.referrer or url_for("auftraege.list_auftraege"))
    if not _darf_auftrag_sehen(auftrag):
        abort(403)

    if aktive_stempelung_von(current_user.username):
        flash("Du hast bereits eine laufende Stempelung — bitte zuerst ausstempeln.", "warning")
        return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id))

    stempelungen.create({
        "mitarbeiter": current_user.username,
        "mitarbeiter_name": current_user.name,
        "auftrag_id": auftrag_id,
        "start": datetime.now().isoformat(timespec="seconds"),
        "taetigkeit": request.form.get("taetigkeit", "").strip(),
    })
    flash("Eingestempelt.", "success")
    return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id))


@bp.route("/stempel/stop", methods=["POST"])
@login_required
def stempel_stop():
    aktive = aktive_stempelung_von(current_user.username)
    if not aktive:
        flash("Keine laufende Stempelung.", "warning")
        return redirect(request.referrer or url_for("zeit.heute"))

    start = _parse_dt(aktive.get("start", ""))
    ende = datetime.now()
    if ende <= start:
        # Edge: System-Uhr-Sprung — minimale Dauer akzeptieren statt Fehler
        ende = start + timedelta(minutes=1)
    dauer_min = (ende - start).total_seconds() / 60.0
    dauer_h = round(dauer_min / 60.0, 2)

    # Zusatz-Taetigkeit aus Form (falls eingegeben) ueberschreibt die Start-Notiz
    taetigkeit = request.form.get("taetigkeit", "").strip() or aktive.get("taetigkeit", "")

    zeitbuchungen.create({
        "auftrag_id": aktive.get("auftrag_id"),
        "datum": start.date().isoformat(),
        "mitarbeiter": aktive.get("mitarbeiter") or current_user.username,
        "von_zeit": start.strftime("%H:%M"),
        "bis_zeit": ende.strftime("%H:%M"),
        "dauer_h": dauer_h,
        "taetigkeit": taetigkeit,
        "notizen": request.form.get("notizen", "").strip(),
        "via_stempelung": True,
    })
    stempelungen.delete(aktive["id"])
    flash(f"Ausgestempelt — {dauer_h} h gebucht.", "success")

    target = request.form.get("redirect_to", "")
    if target == "heute":
        return redirect(url_for("zeit.heute"))
    if aktive.get("auftrag_id"):
        return redirect(url_for("auftraege.detail", auftrag_id=aktive["auftrag_id"]))
    return redirect(url_for("zeit.heute"))


@bp.route("/stempel/abbrechen", methods=["POST"])
@login_required
def stempel_abbrechen():
    """Laufende Stempelung verwerfen — ohne Zeitbuchung anzulegen."""
    aktive = aktive_stempelung_von(current_user.username)
    if aktive:
        stempelungen.delete(aktive["id"])
        flash("Stempelung verworfen — keine Zeit gebucht.", "info")
    return redirect(request.referrer or url_for("zeit.heute"))


@bp.route("/")
@bp.route("/heute")
@login_required
def heute():
    datum_str = request.args.get("datum", "").strip()
    try:
        d = date.fromisoformat(datum_str) if datum_str else date.today()
    except ValueError:
        d = date.today()
    datum_iso = d.isoformat()

    eintraege = zeitbuchungen_am_tag(datum_iso)

    # Sichtbarkeitsfilter wie bei Aufträgen
    auftraege_idx = {a["id"]: a for a in auftraege.list()}
    kunden_idx = {k["id"]: k for k in kunden.list()}
    sichtbare: list[dict] = []
    for z in eintraege:
        a = auftraege_idx.get(z.get("auftrag_id") or "")
        if a and not _darf_auftrag_sehen(a):
            continue
        if (not current_user.sieht_alle_auftraege
                and (z.get("mitarbeiter") or "").lower() != current_user.username.lower()):
            # Monteur sieht nur eigene Zeitbuchungen (auch wenn Auftrag unzugewiesen)
            continue
        sichtbare.append({
            "z": z,
            "auftrag": a,
            "kunde": kunden_idx.get(a.get("kunde_id")) if a else None,
        })

    # Gruppieren pro Mitarbeiter
    pro_mitarbeiter: dict[str, dict] = {}
    for row in sichtbare:
        mit = row["z"].get("mitarbeiter") or "—"
        if mit not in pro_mitarbeiter:
            pro_mitarbeiter[mit] = {"name": mit, "eintraege": [], "summe": 0.0}
        pro_mitarbeiter[mit]["eintraege"].append(row)
        try:
            pro_mitarbeiter[mit]["summe"] += float(row["z"].get("dauer_h") or 0)
        except (TypeError, ValueError):
            pass

    for daten in pro_mitarbeiter.values():
        daten["eintraege"].sort(key=lambda r: r["z"].get("von_zeit") or "")
        daten["summe"] = round(daten["summe"], 2)

    # Laufende Stempelungen
    if current_user.sieht_alle_auftraege:
        laufend = alle_aktiven_stempelungen()
    else:
        eigene = aktive_stempelung_von(current_user.username)
        laufend = [eigene] if eigene else []

    laufend_aufbereitet = []
    jetzt = datetime.now()
    for s in laufend:
        start = _parse_dt(s.get("start", ""))
        a = auftraege_idx.get(s.get("auftrag_id") or "")
        dauer_h = round((jetzt - start).total_seconds() / 3600.0, 2)
        laufend_aufbereitet.append({
            "s": s,
            "auftrag": a,
            "kunde": kunden_idx.get(a.get("kunde_id")) if a else None,
            "start_hm": start.strftime("%H:%M"),
            "dauer_h_live": dauer_h,
        })

    gesamtsumme = round(sum(d["summe"] for d in pro_mitarbeiter.values()), 2)

    prev_tag = (d - timedelta(days=1)).isoformat()
    next_tag = (d + timedelta(days=1)).isoformat()

    return render_template(
        "zeit/heute.html",
        datum=datum_iso,
        datum_obj=d,
        ist_heute=(d == date.today()),
        pro_mitarbeiter=pro_mitarbeiter,
        gesamtsumme=gesamtsumme,
        laufend=laufend_aufbereitet,
        prev_tag=prev_tag,
        next_tag=next_tag,
        heute_iso=date.today().isoformat(),
    )
