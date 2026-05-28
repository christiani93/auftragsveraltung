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
from models.users import find_user

bp = Blueprint("zeit", __name__)


# ----- Pausen-Logik ----------------------------------------------------------

def _hhmm_zu_minuten(value: str) -> int:
    """'HH:MM' -> Minuten seit 00:00. Liefert 0 bei kaputtem Format."""
    try:
        h, m = value.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return 0


def _bloecke_zu_minuten(arbeitszeiten: list[dict]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for b in arbeitszeiten or []:
        v = _hhmm_zu_minuten(b.get("von", ""))
        bi = _hhmm_zu_minuten(b.get("bis", ""))
        if bi > v:
            out.append((v, bi))
    out.sort()
    return out


def _pausen_aus_bloecken(bloecke_min: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Liefert die Pausen zwischen aufeinanderfolgenden Arbeitsbloecken."""
    pausen: list[tuple[int, int]] = []
    for i in range(len(bloecke_min) - 1):
        ende_a = bloecke_min[i][1]
        start_b = bloecke_min[i + 1][0]
        if start_b > ende_a:
            pausen.append((ende_a, start_b))
    return pausen


def netto_dauer_h(start: datetime, ende: datetime, arbeitszeiten: list[dict]) -> float:
    """Effektive Arbeitsdauer in Stunden zwischen start und ende, abzueglich der
    geplanten Pausen, die in den Zeitraum fallen.

    - Wenn start/ende ueber Mitternacht gehen, wird konservativ einfach die
      Brutto-Dauer zurueckgegeben (Pausen-Abzug greift nur fuer Buchungen innerhalb
      eines Kalendertages).
    - Pausen werden pro Kalendertag berechnet (geplant aus Arbeitsbloecken).
    """
    brutto_min = (ende - start).total_seconds() / 60.0
    if brutto_min <= 0:
        return 0.0
    # Nur Bloecke innerhalb desselben Tages mit Pausen-Abzug bedienen
    if start.date() != ende.date():
        return round(brutto_min / 60.0, 2)
    pausen = _pausen_aus_bloecken(_bloecke_zu_minuten(arbeitszeiten))
    start_min = start.hour * 60 + start.minute + start.second / 60.0
    ende_min = ende.hour * 60 + ende.minute + ende.second / 60.0
    pause_min = 0.0
    for p_von, p_bis in pausen:
        ueberlapp = max(0.0, min(ende_min, p_bis) - max(start_min, p_von))
        pause_min += ueberlapp
    netto = max(0.0, brutto_min - pause_min)
    return round(netto / 60.0, 2)


def _arbeitszeiten_von(username: str) -> list[dict]:
    user = find_user(username)
    if not user:
        return []
    return user.arbeitszeiten


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


def _stempelung_abschliessen(aktive: dict, taetigkeit_override: str = "", notizen: str = "") -> tuple[float, float]:
    """Erzeugt eine Zeitbuchung aus der aktiven Stempelung, loescht die Stempelung.

    Liefert (brutto_h, netto_h) — netto = brutto minus geplante Pausen aus den
    Arbeitszeiten des Mitarbeiters.
    """
    start = _parse_dt(aktive.get("start", ""))
    ende = datetime.now()
    if ende <= start:
        # System-Uhr-Sprung: minimale Dauer einsetzen
        ende = start + timedelta(minutes=1)
    brutto_min = (ende - start).total_seconds() / 60.0
    brutto_h = round(brutto_min / 60.0, 2)
    arbeitszeiten = _arbeitszeiten_von(aktive.get("mitarbeiter") or "")
    netto_h = netto_dauer_h(start, ende, arbeitszeiten)
    pause_h = round(brutto_h - netto_h, 2)

    taetigkeit = taetigkeit_override.strip() or aktive.get("taetigkeit", "")

    zeitbuchungen.create({
        "auftrag_id": aktive.get("auftrag_id") or "",
        "datum": start.date().isoformat(),
        "mitarbeiter": aktive.get("mitarbeiter") or current_user.username,
        "von_zeit": start.strftime("%H:%M"),
        "bis_zeit": ende.strftime("%H:%M"),
        "dauer_h": netto_h,
        "brutto_h": brutto_h,
        "pause_h_abgezogen": pause_h,
        "taetigkeit": taetigkeit,
        "notizen": notizen.strip(),
        "via_stempelung": True,
    })
    stempelungen.delete(aktive["id"])
    return brutto_h, netto_h


def _validierter_auftrag(auftrag_id: str) -> dict | None:
    """Liefert den Auftrag, wenn er existiert und der User ihn sehen darf — sonst None."""
    if not auftrag_id:
        return None
    a = auftraege.get(auftrag_id)
    if not a or not _darf_auftrag_sehen(a):
        return None
    return a


@bp.route("/stempel/start", methods=["POST"])
@login_required
def stempel_start():
    """Stempelt ein. auftrag_id ist optional — am Tagesbeginn kann ohne Auftrag begonnen werden."""
    auftrag_id = request.form.get("auftrag_id", "").strip()
    if auftrag_id and not _validierter_auftrag(auftrag_id):
        flash("Auftrag nicht gefunden oder keine Berechtigung.", "warning")
        return redirect(request.referrer or url_for("zeit.heute"))

    if aktive_stempelung_von(current_user.username):
        flash("Du bist bereits eingestempelt — nutze 'Auftrag wechseln' oder 'Ausstempeln'.", "warning")
        return redirect(url_for("zeit.heute"))

    stempelungen.create({
        "mitarbeiter": current_user.username,
        "mitarbeiter_name": current_user.name,
        "auftrag_id": auftrag_id,
        "start": datetime.now().isoformat(timespec="seconds"),
        "taetigkeit": request.form.get("taetigkeit", "").strip(),
    })
    if auftrag_id:
        flash("Eingestempelt.", "success")
    else:
        flash("Eingestempelt — Auftrag kannst du in der Zeiterfassung nachtraeglich zuordnen.", "success")
    return redirect(url_for("zeit.heute"))


@bp.route("/stempel/wechsel", methods=["POST"])
@login_required
def stempel_wechsel():
    """Schliesst die aktuelle Stempelung als Zeitbuchung ab und startet eine neue.

    Wenn nichts laeuft, wird einfach gestartet.
    """
    neuer_auftrag_id = request.form.get("auftrag_id", "").strip()
    if neuer_auftrag_id and not _validierter_auftrag(neuer_auftrag_id):
        flash("Auftrag nicht gefunden oder keine Berechtigung.", "warning")
        return redirect(request.referrer or url_for("zeit.heute"))

    aktive = aktive_stempelung_von(current_user.username)
    taetigkeit_neu = request.form.get("taetigkeit", "").strip()

    if aktive:
        # Alte Stempelung abschliessen (mit ihrer eigenen Taetigkeit) und neue starten
        brutto_h, netto_h = _stempelung_abschliessen(aktive)
        meldung_alt = f"Umgestempelt — vorheriger Block: {netto_h} h"
        if brutto_h != netto_h:
            meldung_alt += f" (brutto {brutto_h} h, Pause -{round(brutto_h - netto_h, 2)} h)"
        flash(meldung_alt + ".", "info")

    stempelungen.create({
        "mitarbeiter": current_user.username,
        "mitarbeiter_name": current_user.name,
        "auftrag_id": neuer_auftrag_id,
        "start": datetime.now().isoformat(timespec="seconds"),
        "taetigkeit": taetigkeit_neu,
    })
    flash("Neuer Block laeuft.", "success")
    return redirect(url_for("zeit.heute"))


@bp.route("/stempel/stop", methods=["POST"])
@login_required
def stempel_stop():
    aktive = aktive_stempelung_von(current_user.username)
    if not aktive:
        flash("Keine laufende Stempelung.", "warning")
        return redirect(request.referrer or url_for("zeit.heute"))

    brutto_h, netto_h = _stempelung_abschliessen(
        aktive,
        taetigkeit_override=request.form.get("taetigkeit", ""),
        notizen=request.form.get("notizen", ""),
    )
    if brutto_h != netto_h:
        flash(
            f"Ausgestempelt — {netto_h} h gebucht (brutto {brutto_h} h, Pause -{round(brutto_h - netto_h, 2)} h).",
            "success",
        )
    else:
        flash(f"Ausgestempelt — {netto_h} h gebucht.", "success")
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


@bp.route("/stempel/auftrag-zuordnen", methods=["POST"])
@login_required
def stempel_auftrag_zuordnen():
    """Aendert den Auftrag der laufenden Stempelung OHNE umzustempeln (keine neue Buchung).

    Praktisch, wenn man am Tagesstart ohne Auftrag eingestempelt hat und
    nachtraeglich noch denselben Block einem Auftrag zuordnen will.
    """
    aktive = aktive_stempelung_von(current_user.username)
    if not aktive:
        flash("Keine laufende Stempelung.", "warning")
        return redirect(url_for("zeit.heute"))
    neuer_auftrag_id = request.form.get("auftrag_id", "").strip()
    if neuer_auftrag_id and not _validierter_auftrag(neuer_auftrag_id):
        flash("Auftrag nicht gefunden oder keine Berechtigung.", "warning")
        return redirect(url_for("zeit.heute"))
    stempelungen.update(aktive["id"], {"auftrag_id": neuer_auftrag_id})
    flash("Auftrag fuer laufenden Block zugeordnet.", "success")
    return redirect(url_for("zeit.heute"))


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

    # Laufende Stempelungen (fuer Anzeige) + eigene (fuer Stempel-Karte oben)
    eigene_aktive = aktive_stempelung_von(current_user.username)
    if current_user.sieht_alle_auftraege:
        laufend = alle_aktiven_stempelungen()
    else:
        laufend = [eigene_aktive] if eigene_aktive else []

    jetzt = datetime.now()
    laufend_aufbereitet = []
    for s in laufend:
        start = _parse_dt(s.get("start", ""))
        a = auftraege_idx.get(s.get("auftrag_id") or "")
        brutto_h = round((jetzt - start).total_seconds() / 3600.0, 2)
        netto_h = netto_dauer_h(start, jetzt, _arbeitszeiten_von(s.get("mitarbeiter") or ""))
        laufend_aufbereitet.append({
            "s": s,
            "auftrag": a,
            "kunde": kunden_idx.get(a.get("kunde_id")) if a else None,
            "start_hm": start.strftime("%H:%M"),
            "brutto_h_live": brutto_h,
            "netto_h_live": netto_h,
        })

    # Auftragsliste fuer die Dropdowns (nur sichtbare, sortiert: offene/in_arbeit zuerst)
    sichtbare_auftraege = sorted(
        [a for a in auftraege.list() if _darf_auftrag_sehen(a) and a.get("status") != "erledigt"],
        key=lambda a: (a.get("status") != "in_arbeit", a.get("status") != "offen", -1 * len(a.get("erteilungsdatum", ""))),
    )
    # Mit Kunden-Name fuer schoenere Anzeige
    auftrag_optionen = []
    for a in sichtbare_auftraege:
        k = kunden_idx.get(a.get("kunde_id"))
        auftrag_optionen.append({
            "id": a["id"],
            "label": (f"{k['name']}: " if k else "") + (a.get("titel") or "—"),
        })

    eigene_stempelung_aufbereitet = None
    if eigene_aktive:
        start = _parse_dt(eigene_aktive.get("start", ""))
        a = auftraege_idx.get(eigene_aktive.get("auftrag_id") or "")
        eigene_stempelung_aufbereitet = {
            "s": eigene_aktive,
            "auftrag": a,
            "kunde": kunden_idx.get(a.get("kunde_id")) if a else None,
            "start_hm": start.strftime("%H:%M"),
            "brutto_h_live": round((jetzt - start).total_seconds() / 3600.0, 2),
            "netto_h_live": netto_dauer_h(start, jetzt, current_user.arbeitszeiten),
        }

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
        eigene_stempelung=eigene_stempelung_aufbereitet,
        auftrag_optionen=auftrag_optionen,
        prev_tag=prev_tag,
        next_tag=next_tag,
        heute_iso=date.today().isoformat(),
    )
