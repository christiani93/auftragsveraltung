"""Stempelung (Komm/Geh) + Tagesansicht der Zeiterfassung.

Stempeln Start (optional mit Auftrag) → Eintrag in stempelung.json.
Stempeln Stop → Eintrag wird in eine Zeitbuchung (zeitbuchungen.json)
ueberfuehrt, mit Datum + Von/Bis + Dauer (brutto, kein Pausen-Abzug).

Admin kann fuer andere Mitarbeiter stempeln (Form-Feld 'fuer_mitarbeiter').
Monteur stempelt immer fuer sich selbst.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from models.repos import (
    UNPRODUKTIV_KATEGORIEN,
    aktive_stempelung_von,
    alle_aktiven_stempelungen,
    auftrag_bei_zeitbuchung_aktualisieren,
    auftraege,
    dauer_aus_zeitspanne,
    ist_mitarbeiter_in_revision,
    kunden,
    stempelungen,
    zeitbuchungen,
    zeitbuchungen_am_tag,
)
from models.users import find_user, list_users

bp = Blueprint("zeit", __name__)


def _darf_auftrag_sehen(auftrag: dict) -> bool:
    if not current_user.is_authenticated:
        return False
    if current_user.sieht_alle_auftraege:
        return True
    if ist_mitarbeiter_in_revision(auftrag.get("revision_id"), current_user.username):
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


def _uhrzeit_auf_datum(zeit_str: str, basis: datetime) -> datetime | None:
    """Parst 'HH:MM' und legt es auf das Datum von basis. None bei ungueltig/leer.

    Wird fuer manuell angepasste Block-Startzeiten verwendet — exakt, ohne
    Viertelstunden-Rundung, weil der User die echte Zeit kennt.
    """
    zeit_str = (zeit_str or "").strip()
    if not zeit_str:
        return None
    try:
        t = datetime.strptime(zeit_str, "%H:%M").time()
    except ValueError:
        return None
    return basis.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)


# Quantisierung der Stempel-Zeitpunkte auf Viertelstunden mit Toleranz-Filter:
# - Einstempeln (Start): 5 Min vor und 10 Min nach Viertelstunden-Punkt T → T.
#   D.h. :11..:14 rundet auf nächstes T, :00..:10 auf voriges T.
# - Ausstempeln (Ende): 10 Min vor und 5 Min nach T → T.
#   D.h. :06..:14 rundet auf nächstes T, :00..:05 auf voriges T.
# - Umstempeln: EIN Zeitpunkt fuer Ende_alt und Start_neu — gleicher Wert, kein
#   Gap und keine Ueberlappung. Wir nehmen den Ausstempel-Filter dafuer.
QUANTUM_MINUTEN = 15


def _abrunden_voriges_q(dt: datetime) -> datetime:
    return dt.replace(minute=(dt.minute // QUANTUM_MINUTEN) * QUANTUM_MINUTEN, second=0, microsecond=0)


def _aufrunden_naechstes_q(dt: datetime, offset: int) -> datetime:
    """Hebt dt um (QUANTUM_MINUTEN - offset) Minuten an. Setzt Sekunden/Microsekunden auf 0."""
    return (dt + timedelta(minutes=QUANTUM_MINUTEN - offset)).replace(second=0, microsecond=0)


def _runde_einstempel(dt: datetime) -> datetime:
    """5 Min vor / 10 Min nach Viertelstunde → Viertelstunde."""
    offset = dt.minute % QUANTUM_MINUTEN
    if offset > 10:  # 11..14 → aufrunden (in der 5-Min-Vor-Zone des naechsten T)
        return _aufrunden_naechstes_q(dt, offset)
    return _abrunden_voriges_q(dt)


def _runde_ausstempel(dt: datetime) -> datetime:
    """10 Min vor / 5 Min nach Viertelstunde → Viertelstunde."""
    offset = dt.minute % QUANTUM_MINUTEN
    if offset > 5:  # 6..14 → aufrunden (in der 10-Min-Vor-Zone des naechsten T)
        return _aufrunden_naechstes_q(dt, offset)
    return _abrunden_voriges_q(dt)


def _validierter_auftrag(auftrag_id: str) -> dict | None:
    if not auftrag_id:
        return None
    a = auftraege.get(auftrag_id)
    if not a or not _darf_auftrag_sehen(a):
        return None
    return a


def _ziel_mitarbeiter() -> tuple[str, str]:
    """Liefert (username, anzeigename) — der User, fuer den die Stempel-Aktion gilt.

    Admin darf via Form-Feld 'fuer_mitarbeiter' fuer jemand anderen stempeln,
    alle anderen Rollen stempeln nur fuer sich selbst.
    """
    if current_user.is_admin:
        gewuenscht = request.form.get("fuer_mitarbeiter", "").strip()
        if gewuenscht and gewuenscht != current_user.username:
            u = find_user(gewuenscht)
            if u:
                return u.username, u.name
    return current_user.username, current_user.name


def _stempelung_abschliessen(aktive: dict, ende_dt: datetime, taetigkeit_override: str = "", notizen: str = "") -> float:
    """Erzeugt eine Zeitbuchung aus der aktiven Stempelung, loescht die Stempelung.

    Der Caller liefert den Ende-Zeitpunkt — beim normalen Ausstempeln ist das
    _runde_ausstempel(now), beim Umstempeln derselbe Wert wie der Start des
    Folge-Blocks (kein Gap/Ueberlappung).
    Liefert die gebuchte Dauer in Stunden (brutto, kein Pausen-Abzug).
    """
    start = _parse_dt(aktive.get("start", ""))
    ende = ende_dt
    if ende <= start:
        ende = start + timedelta(minutes=QUANTUM_MINUTEN)
    dauer_min = (ende - start).total_seconds() / 60.0
    dauer_h = round(dauer_min / 60.0, 2)

    unproduktiv = bool(aktive.get("unproduktiv"))
    kategorie = aktive.get("kategorie", "") if unproduktiv else ""
    taetigkeit = taetigkeit_override.strip() or aktive.get("taetigkeit", "") or (kategorie if unproduktiv else "")

    zeitbuchungen.create({
        "auftrag_id": aktive.get("auftrag_id") or "",
        "datum": start.date().isoformat(),
        "mitarbeiter": aktive.get("mitarbeiter") or current_user.username,
        "von_zeit": start.strftime("%H:%M"),
        "bis_zeit": ende.strftime("%H:%M"),
        "dauer_h": dauer_h,
        "taetigkeit": taetigkeit,
        "notizen": notizen.strip(),
        "via_stempelung": True,
        "unproduktiv": unproduktiv,
        "kategorie": kategorie,
    })
    auftrag_bei_zeitbuchung_aktualisieren(
        aktive.get("auftrag_id") or "",
        aktive.get("mitarbeiter") or current_user.username,
    )
    stempelungen.delete(aktive["id"])
    return dauer_h


@bp.route("/stempel/start", methods=["POST"])
@login_required
def stempel_start():
    """Stempelt ein. auftrag_id optional — Auftrag kann nachtraeglich zugeordnet werden."""
    auftrag_id = request.form.get("auftrag_id", "").strip()
    if auftrag_id and not _validierter_auftrag(auftrag_id):
        flash("Auftrag nicht gefunden oder keine Berechtigung.", "warning")
        return redirect(request.referrer or url_for("zeit.heute"))

    ziel_user, ziel_name = _ziel_mitarbeiter()

    if aktive_stempelung_von(ziel_user):
        flash(f"{ziel_name} ist bereits eingestempelt — nutze 'Auftrag wechseln' oder 'Ausstempeln'.", "warning")
        return redirect(url_for("zeit.heute"))

    # Optionale manuelle Startzeit (z.B. nachtraegliches Einstempeln). Exakt,
    # ohne Rundung; Zukunft wird auf 'jetzt gerundet' zurueckgesetzt.
    jetzt = datetime.now()
    manuell = _uhrzeit_auf_datum(request.form.get("start_zeit", ""), jetzt)
    start_dt = manuell if (manuell and manuell <= jetzt) else _runde_einstempel(jetzt)

    # Ohne Auftrag kann direkt unproduktiv gestempelt werden (Kategorie waehlen).
    kategorie = request.form.get("kategorie", "").strip() if not auftrag_id else ""
    stempel = {
        "mitarbeiter": ziel_user,
        "mitarbeiter_name": ziel_name,
        "auftrag_id": auftrag_id,
        "start": start_dt.isoformat(timespec="seconds"),
        "taetigkeit": request.form.get("taetigkeit", "").strip(),
    }
    if kategorie:
        stempel["unproduktiv"] = True
        stempel["kategorie"] = kategorie
    stempelungen.create(stempel)
    wer = "Eingestempelt" if ziel_user == current_user.username else f"{ziel_name} eingestempelt"
    if auftrag_id:
        flash(f"{wer}.", "success")
    elif kategorie:
        flash(f"{wer} — unproduktiv ({kategorie}).", "success")
    else:
        flash(f"{wer} — Auftrag kannst du nachtraeglich zuordnen.", "success")
    return redirect(url_for("zeit.heute"))


def _markiere_auftrag_erledigt(auftrag_id: str) -> bool:
    """Setzt einen Auftrag auf 'erledigt' (erledigt_am = heute, falls leer).
    Liefert True, wenn ein Auftrag geaendert wurde."""
    if not auftrag_id:
        return False
    a = auftraege.get(auftrag_id)
    if not a or a.get("status") == "erledigt":
        return False
    update = {"status": "erledigt"}
    if not a.get("erledigt_am"):
        update["erledigt_am"] = date.today().isoformat()
    auftraege.update(auftrag_id, update)
    return True


@bp.route("/stempel/wechsel", methods=["POST"])
@login_required
def stempel_wechsel():
    """Schliesst die aktuelle Stempelung als Zeitbuchung ab und startet eine neue."""
    neuer_auftrag_id = request.form.get("auftrag_id", "").strip()
    if neuer_auftrag_id and not _validierter_auftrag(neuer_auftrag_id):
        flash("Auftrag nicht gefunden oder keine Berechtigung.", "warning")
        return redirect(request.referrer or url_for("zeit.heute"))

    ziel_user, ziel_name = _ziel_mitarbeiter()
    aktive = aktive_stempelung_von(ziel_user)
    taetigkeit_neu = request.form.get("taetigkeit", "").strip()
    alt_auftrag_id = aktive.get("auftrag_id") if aktive else None

    # Wechsel: EIN Zeitpunkt fuer Ende_alt und Start_neu — kein Gap/Ueberlappung.
    wechsel_zp = _runde_ausstempel(datetime.now())

    if aktive:
        dauer_h = _stempelung_abschliessen(aktive, wechsel_zp)
        praefix = "Umgestempelt" if ziel_user == current_user.username else f"{ziel_name} umgestempelt"
        flash(f"{praefix} — vorheriger Block: {dauer_h} h.", "info")
        if request.form.get("auftrag_erledigt") and _markiere_auftrag_erledigt(alt_auftrag_id):
            flash("Vorheriger Auftrag als erledigt markiert.", "info")

    kategorie_neu = request.form.get("kategorie", "").strip() if not neuer_auftrag_id else ""
    neuer_stempel = {
        "mitarbeiter": ziel_user,
        "mitarbeiter_name": ziel_name,
        "auftrag_id": neuer_auftrag_id,
        "start": wechsel_zp.isoformat(timespec="seconds"),
        "taetigkeit": taetigkeit_neu,
    }
    if kategorie_neu:
        neuer_stempel["unproduktiv"] = True
        neuer_stempel["kategorie"] = kategorie_neu
    stempelungen.create(neuer_stempel)
    flash("Neuer Block laeuft." + (f" Unproduktiv ({kategorie_neu})." if kategorie_neu else ""), "success")
    return redirect(url_for("zeit.heute"))


@bp.route("/stempel/stop", methods=["POST"])
@login_required
def stempel_stop():
    ziel_user, ziel_name = _ziel_mitarbeiter()
    aktive = aktive_stempelung_von(ziel_user)
    if not aktive:
        flash(f"Keine laufende Stempelung fuer {ziel_name}.", "warning")
        return redirect(request.referrer or url_for("zeit.heute"))

    alt_auftrag_id = aktive.get("auftrag_id")
    dauer_h = _stempelung_abschliessen(
        aktive,
        _runde_ausstempel(datetime.now()),
        taetigkeit_override=request.form.get("taetigkeit", ""),
        notizen=request.form.get("notizen", ""),
    )
    praefix = "Ausgestempelt" if ziel_user == current_user.username else f"{ziel_name} ausgestempelt"
    flash(f"{praefix} — {dauer_h} h gebucht.", "success")
    if request.form.get("auftrag_erledigt") and _markiere_auftrag_erledigt(alt_auftrag_id):
        flash("Auftrag als erledigt markiert.", "info")
    return redirect(url_for("zeit.heute"))


@bp.route("/stempel/abbrechen", methods=["POST"])
@login_required
def stempel_abbrechen():
    """Laufende Stempelung verwerfen — ohne Zeitbuchung anzulegen."""
    ziel_user, ziel_name = _ziel_mitarbeiter()
    aktive = aktive_stempelung_von(ziel_user)
    if aktive:
        stempelungen.delete(aktive["id"])
        praefix = "Stempelung verworfen" if ziel_user == current_user.username else f"Stempelung von {ziel_name} verworfen"
        flash(f"{praefix} — keine Zeit gebucht.", "info")
    return redirect(request.referrer or url_for("zeit.heute"))


@bp.route("/stempel/auftrag-zuordnen", methods=["POST"])
@login_required
def stempel_auftrag_zuordnen():
    """Aendert den Auftrag der laufenden Stempelung OHNE eine Buchung zu erzeugen."""
    ziel_user, _ = _ziel_mitarbeiter()
    aktive = aktive_stempelung_von(ziel_user)
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


@bp.route("/stempel/start-anpassen", methods=["POST"])
@login_required
def stempel_start_anpassen():
    """Korrigiert die Startzeit des laufenden Blocks (ohne Buchung zu erzeugen)."""
    ziel_user, _ = _ziel_mitarbeiter()
    aktive = aktive_stempelung_von(ziel_user)
    if not aktive:
        flash("Keine laufende Stempelung.", "warning")
        return redirect(url_for("zeit.heute"))
    alt_start = _parse_dt(aktive.get("start", ""))
    neu_start = _uhrzeit_auf_datum(request.form.get("start_zeit", ""), alt_start)
    if not neu_start:
        flash("Ungültige Uhrzeit.", "warning")
        return redirect(url_for("zeit.heute"))
    if neu_start > datetime.now():
        flash("Startzeit darf nicht in der Zukunft liegen.", "warning")
        return redirect(url_for("zeit.heute"))
    stempelungen.update(aktive["id"], {"start": neu_start.isoformat(timespec="seconds")})
    flash(f"Startzeit angepasst auf {neu_start.strftime('%H:%M')}.", "success")
    return redirect(url_for("zeit.heute"))


@bp.route("/unproduktiv/neu", methods=["POST"])
@login_required
def unproduktiv_neu():
    """Erfasst unproduktive Zeit (ohne Auftrag) mit Kategorie — direkt als
    Zeitbuchung. Dauer aus Von/Bis oder direkt in Stunden."""
    ziel_user, ziel_name = _ziel_mitarbeiter()
    datum = request.form.get("datum", "").strip() or date.today().isoformat()
    von = request.form.get("von_zeit", "").strip() or None
    bis = request.form.get("bis_zeit", "").strip() or None
    dauer_str = request.form.get("dauer_h", "").strip()
    dauer = None
    if dauer_str:
        try:
            dauer = round(float(dauer_str.replace(",", ".")), 2)
        except ValueError:
            dauer = None
    if dauer is None and von and bis:
        dauer = dauer_aus_zeitspanne(von, bis)
    if not dauer or dauer <= 0:
        flash("Bitte Stunden direkt eintragen oder gültige Von/Bis-Zeiten angeben.", "warning")
        return redirect(url_for("zeit.heute", datum=datum))

    kategorie = request.form.get("kategorie", "").strip()
    zeitbuchungen.create({
        "auftrag_id": "",
        "datum": datum,
        "mitarbeiter": ziel_user,
        "von_zeit": von,
        "bis_zeit": bis,
        "dauer_h": dauer,
        "taetigkeit": kategorie or "Unproduktiv",
        "notizen": request.form.get("notizen", "").strip(),
        "unproduktiv": True,
        "kategorie": kategorie,
    })
    flash(f"{dauer} h unproduktive Zeit erfasst{(' — ' + kategorie) if kategorie else ''}.", "success")
    return redirect(url_for("zeit.heute", datum=datum))


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

    # Wer wird in der Stempel-Karte oben angezeigt? Admin kann via ?stempel_fuer=...
    # einen anderen Mitarbeiter waehlen; default = self.
    stempel_fuer = current_user.username
    if current_user.is_admin:
        gewuenscht = request.args.get("stempel_fuer", "").strip()
        if gewuenscht:
            u = find_user(gewuenscht)
            if u:
                stempel_fuer = u.username
    stempel_fuer_user = find_user(stempel_fuer)
    stempel_fuer_name = stempel_fuer_user.name if stempel_fuer_user else stempel_fuer

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
            continue
        sichtbare.append({
            "z": z,
            "auftrag": a,
            "kunde": kunden_idx.get(a.get("kunde_id")) if a else None,
        })

    # Gruppieren: Mitarbeiter -> Kunde -> Auftrag (Hierarchie fuer Tagesblick)
    pro_mitarbeiter: dict[str, dict] = {}
    for row in sichtbare:
        mit = row["z"].get("mitarbeiter") or ""
        if mit not in pro_mitarbeiter:
            pro_mitarbeiter[mit] = {"username": mit, "summe": 0.0, "unproduktiv": 0.0, "_kunden": {}}
        try:
            dauer = float(row["z"].get("dauer_h") or 0)
        except (TypeError, ValueError):
            dauer = 0.0
        pro_mitarbeiter[mit]["summe"] += dauer
        if row["z"].get("unproduktiv"):
            pro_mitarbeiter[mit]["unproduktiv"] += dauer
        kbuckets = pro_mitarbeiter[mit]["_kunden"]
        kunde_id = row["kunde"]["id"] if row["kunde"] else ""
        if kunde_id not in kbuckets:
            kbuckets[kunde_id] = {"kunde": row["kunde"], "summe": 0.0, "_auftraege": {}}
        kb = kbuckets[kunde_id]
        kb["summe"] += dauer
        auftrag_id = row["auftrag"]["id"] if row["auftrag"] else ""
        if auftrag_id not in kb["_auftraege"]:
            kb["_auftraege"][auftrag_id] = {"auftrag": row["auftrag"], "summe": 0.0, "eintraege": []}
        ab = kb["_auftraege"][auftrag_id]
        ab["summe"] += dauer
        ab["eintraege"].append(row)

    def _von_bis(rows):
        v = [r["z"].get("von_zeit") for r in rows if r["z"].get("von_zeit")]
        b = [r["z"].get("bis_zeit") for r in rows if r["z"].get("bis_zeit")]
        return (min(v) if v else None, max(b) if b else None)

    # _kunden/_auftraege-Dicts zu sortierten Listen mit Von/Bis ausrechnen
    for daten in pro_mitarbeiter.values():
        daten["summe"] = round(daten["summe"], 2)
        daten["unproduktiv"] = round(daten["unproduktiv"], 2)
        kunden_liste = []
        for kb in daten["_kunden"].values():
            alle_eintraege_k = [r for ab in kb["_auftraege"].values() for r in ab["eintraege"]]
            kb_von, kb_bis = _von_bis(alle_eintraege_k)
            kb["von"], kb["bis"] = kb_von, kb_bis
            kb["summe"] = round(kb["summe"], 2)
            auftraege_liste = []
            for ab in kb["_auftraege"].values():
                ab["eintraege"].sort(key=lambda r: r["z"].get("von_zeit") or "")
                ab_von, ab_bis = _von_bis(ab["eintraege"])
                ab["von"], ab["bis"] = ab_von, ab_bis
                ab["summe"] = round(ab["summe"], 2)
                auftraege_liste.append(ab)
            auftraege_liste.sort(key=lambda ab: ab["von"] or "")
            kb["auftraege"] = auftraege_liste
            del kb["_auftraege"]
            kunden_liste.append(kb)
        kunden_liste.sort(key=lambda kb: kb["von"] or "")
        daten["kunden"] = kunden_liste
        del daten["_kunden"]

    # Aktive Stempelung des "gewaehlten" Users (Karte oben)
    aktive_fuer_karte = aktive_stempelung_von(stempel_fuer)

    # Andere laufende Stempelungen (Anzeige fuer Admin/Projektleiter)
    if current_user.sieht_alle_auftraege:
        alle_laufend = alle_aktiven_stempelungen()
    else:
        alle_laufend = [aktive_fuer_karte] if aktive_fuer_karte else []

    jetzt = datetime.now()
    laufend_aufbereitet = []
    for s in alle_laufend:
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

    # Aktueller Kunde = Kunde der laufenden Stempelung (falls vorhanden) — dessen
    # Auftraege erscheinen in der Auswahl zuoberst.
    aktueller_kunde_id = None
    if aktive_fuer_karte:
        a_akt = auftraege_idx.get(aktive_fuer_karte.get("auftrag_id") or "")
        if a_akt:
            aktueller_kunde_id = a_akt.get("kunde_id")

    def _auftrag_sort_key(a):
        k = kunden_idx.get(a.get("kunde_id"))
        ist_aktueller = 0 if (aktueller_kunde_id and a.get("kunde_id") == aktueller_kunde_id) else 1
        return (ist_aktueller, (k["name"].lower() if k else "￿"), (a.get("titel") or "").lower())

    # Auftragsliste fuer die Dropdowns (sichtbare, ohne 'erledigt' und 'abgerechnet'):
    # aktueller Kunde zuoberst, dann nach Kunde, dann nach Auftragstitel.
    sichtbare_auftraege = sorted(
        [a for a in auftraege.list()
         if _darf_auftrag_sehen(a) and a.get("status") not in ("erledigt", "abgerechnet")],
        key=_auftrag_sort_key,
    )
    auftrag_optionen = []
    for a in sichtbare_auftraege:
        k = kunden_idx.get(a.get("kunde_id"))
        auftrag_optionen.append({
            "id": a["id"],
            "label": (f"{k['name']}: " if k else "") + (a.get("titel") or "—"),
        })

    # Karten-Daten fuer den gewaehlten Stempel-User
    eigene_stempelung_aufbereitet = None
    if aktive_fuer_karte:
        start = _parse_dt(aktive_fuer_karte.get("start", ""))
        a = auftraege_idx.get(aktive_fuer_karte.get("auftrag_id") or "")
        eigene_stempelung_aufbereitet = {
            "s": aktive_fuer_karte,
            "auftrag": a,
            "kunde": kunden_idx.get(a.get("kunde_id")) if a else None,
            "start_hm": start.strftime("%H:%M"),
            "dauer_h_live": round((jetzt - start).total_seconds() / 3600.0, 2),
        }

    # Mitarbeiter-Liste fuer Admin-Auswahl (nur Admin)
    alle_user = list_users() if current_user.is_admin else []

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
        stempel_fuer=stempel_fuer,
        stempel_fuer_name=stempel_fuer_name,
        ist_fremd_stempelung=(stempel_fuer != current_user.username),
        alle_user=alle_user,
        prev_tag=prev_tag,
        next_tag=next_tag,
        heute_iso=date.today().isoformat(),
        unproduktiv_kategorien=UNPRODUKTIV_KATEGORIEN,
    )
