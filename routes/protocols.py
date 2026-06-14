"""Messprotokolle — Erfassung der Messwerte zur späteren SiNa-Erstellung."""
from __future__ import annotations

from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from flask_login import current_user

from models.repos import (
    MESSPUNKT_FELDER,
    SELECT_OPTIONEN,
    anlagen,
    anlagenteile,
    anlagenteile_fuer_anlage,
    anlagenteile_mit_anhang,
    messpunkte_gruppiert_fuer_kunde,
    auftraege,
    kunden,
    messgeraete,
    messgeraete_fuer_user,
    messprotokolle,
)
from models.users import list_monteure


def _messgeraet_ids_of(protokoll: dict) -> list[str]:
    """Liefert die Liste der zugeordneten Messgeraet-IDs.
    Unterstützt sowohl das neue Multi-Field als auch das alte Single-Field
    (für Backward-Kompat).
    """
    ids = protokoll.get("messgeraet_ids") or []
    if not ids and protokoll.get("messgeraet_id"):
        ids = [protokoll["messgeraet_id"]]
    return [i for i in ids if i]

bp = Blueprint("protocols", __name__)


def _kunden_index() -> dict:
    return {k["id"]: k for k in kunden.list()}


def _anlagen_sortiert() -> list:
    """Anlagen sortiert nach Kundenname, dann Bezeichnung — fuer die Auswahl,
    damit gleichnamige Anlagen verschiedener Kunden unterscheidbar bleiben."""
    idx = _kunden_index()
    return sorted(
        anlagen.list(),
        key=lambda a: (
            (idx.get(a.get("kunde_id")) or {}).get("name", "").lower(),
            a.get("bezeichnung", "").lower(),
        ),
    )


def _messpunkt_aus_teil(teil: dict, datum: str, pruefer: str = "") -> dict:
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
        "pruefer": pruefer,
        "bemerkung": "",
    }


_UV_HINWEISE = ("uv", "verteil", "unterverteil", "hauptverteil", "tableau", "sak", "schaltgerät", "hv ")


def _typ_aus_installation(name: str) -> str:
    """Heuristik: UV/Verteilung erkennen am 'Installation / Ort'-Text, sonst
    Endstromkreis. Der Anwender kann den Typ nachträglich korrigieren."""
    low = name.lower()
    if any(h in low for h in _UV_HINWEISE):
        return "Verteilung"
    return "Endstromkreis mit Steckdosen"


def _teil_indizes(anlage_id: str) -> tuple[dict, dict]:
    """Indizes der Anlagenteile fuer das Matching von Messpunkten.

    by_sicherung: {(parent_id, sicherungsnr): teil} — primaeres, eindeutiges
        Kriterium (Sicherungsnummer im Kontext der jeweiligen Verteilung).
    by_name: {bezeichnung|'bezeichnung – beschreibung': teil} — Fallback.
    """
    by_sicherung: dict = {}
    by_name: dict = {}
    for t in anlagenteile_fuer_anlage(anlage_id):
        snr = (t.get("sicherungsnr") or "").strip().lower()
        if snr:
            by_sicherung.setdefault((t.get("parent_id"), snr), t)
        bez = (t.get("bezeichnung") or "").strip()
        if bez:
            by_name.setdefault(bez.lower(), t)
            if t.get("beschreibung"):
                by_name.setdefault(f"{bez} – {t['beschreibung']}".strip().lower(), t)
    return by_sicherung, by_name


def _finde_teil_fuer_messpunkt(m: dict, parent_id: str | None, by_sicherung: dict, by_name: dict) -> dict | None:
    """Ordnet einen Messpunkt einem bestehenden Anlagenteil zu — primaer ueber
    die Sicherungsnummer (im Kontext der Verteilung), sonst ueber den Namen."""
    snr = (m.get("sicherungsnr") or "").strip().lower()
    if snr:
        t = by_sicherung.get((parent_id, snr))
        if t:
            return t
    name = (m.get("installation") or "").strip().lower()
    return by_name.get(name) if name else None


def _aufbau_aus_messpunkten(anlage_id: str, messungen: list[dict], parent_id: str | None) -> tuple[int, int]:
    """Ergänzt den Anlagenaufbau aus den Messpunkten.

    - Bestehende Anlagenteile werden zuerst über die Sicherungsnummer (im
      Kontext der Verteilung) gematcht, sonst über den Namen — und nur in
      LEEREN technischen Feldern ergänzt, nie überschrieben.
    - Fehlende Anlagenteile werden angelegt; Typ aus 'Installation / Ort'
      (UV/Verteilung vs. Endstromkreis). Neue Teile hängen unter parent_id.
      Die Stromstärke (Schutzorgan-Nennstrom) wird als stromstaerke_a übernommen.
    Liefert (angelegt, aktualisiert). Idempotent.
    """
    by_sicherung, by_name = _teil_indizes(anlage_id)
    angelegt = aktualisiert = 0
    for m in messungen:
        name = (m.get("installation") or "").strip()
        if not name or name.lower() == "installation / ort":
            continue
        tech = {
            "kabel": (m.get("kabel") or "").strip(),
            "sicherungsnr": (m.get("sicherungsnr") or "").strip(),
            "sicherungstyp": (m.get("sicherungstyp") or "").strip(),
            "fi_typ_ma": (m.get("fi_typ_ma") or "").strip(),
            # Schutzorgan-Nennstrom als Stromstärke des Teils übernehmen
            "stromstaerke_a": (m.get("sicherung_a") or "").strip(),
        }
        bestehend = _finde_teil_fuer_messpunkt(m, parent_id, by_sicherung, by_name)
        if bestehend:
            updates = {k: v for k, v in tech.items() if v and not str(bestehend.get(k) or "").strip()}
            if updates:
                anlagenteile.update(bestehend["id"], updates)
                aktualisiert += 1
        else:
            neu = {
                "anlage_id": anlage_id,
                "parent_id": parent_id or None,
                "typ": _typ_aus_installation(name),
                "bezeichnung": name,
                "beschreibung": "",
                "spannung": None,
                "leistung_kw": None,
                # Aus einem Messprotokoll angelegt = frisch gemessen, also
                # kontrollpflichtig (nicht geprueft) — der Kontrolleur muss ran.
                "kontroll_status": "offen",
                "letzte_kontrolle": m.get("datum") or "",
                **tech,
            }
            created = anlagenteile.create(neu)
            snr = (created.get("sicherungsnr") or "").strip().lower()
            if snr:
                by_sicherung[(created.get("parent_id"), snr)] = created
            by_name[name.lower()] = created
            angelegt += 1
    return angelegt, aktualisiert


def _teile_als_geprueft_markieren(p: dict) -> int:
    """Setzt alle im Protokoll aufgeführten Anlagenteile (inkl. der Verteilung,
    auf der das Protokoll erfasst wurde) auf 'geprueft'. Zuordnung via
    Sicherungsnummer bzw. Name. Liefert die Anzahl gesetzter Teile."""
    anlage_id = p.get("anlage_id")
    if not anlage_id:
        return 0
    by_sicherung, by_name = _teil_indizes(anlage_id)
    parent_id = p.get("anlagenteil_id")
    datum = p.get("datum") or date.today().isoformat()
    kontrolleur = p.get("monteur") or ""
    gesehen: set = set()

    def _setze(teil_id: str) -> None:
        anlagenteile.update(teil_id, {
            "kontroll_status": "geprueft",
            "letzte_kontrolle": datum,
            "kontrolleur": kontrolleur,
        })

    for m in p.get("messungen", []):
        teil = _finde_teil_fuer_messpunkt(m, parent_id, by_sicherung, by_name)
        if teil and teil["id"] not in gesehen:
            gesehen.add(teil["id"])
            _setze(teil["id"])
    # Die Verteilung selbst (auf der erfasst wurde) ebenfalls als geprueft.
    if parent_id and parent_id not in gesehen:
        verteilung = anlagenteile.get(parent_id)
        if verteilung:
            gesehen.add(parent_id)
            _setze(parent_id)
    return len(gesehen)


def _markiere_kontrollpflichtig(data: dict) -> None:
    """Nach Messprotokoll-Erstellung: betroffene Anlagenteile von 'geprueft'
    auf 'offen' setzen — nur fuer Installationen, fuer die ein Messprotokoll
    erstellt wurde, muss der Kontrolleur ran. Betroffen = die Anlagenteile des
    verknuepften Auftrags (gleiche Anlage). Das Anlagenteil, AUF DEM das
    Protokoll eroeffnet wurde (die Verteilung), wird bewusst NICHT angefasst —
    es ist der Erfassungspunkt, nicht die gemessene Installation. Bereits
    'maengel'/'offen'-Teile bleiben unveraendert."""
    anlage_id = data.get("anlage_id")
    betroffen: set[str] = set()
    if data.get("auftrag_id"):
        auftrag = auftraege.get(data["auftrag_id"])
        if auftrag:
            for tid in auftrag.get("anlagenteil_ids", []):
                teil = anlagenteile.get(tid)
                if teil and teil.get("anlage_id") == anlage_id:
                    betroffen.add(tid)
    for tid in betroffen:
        teil = anlagenteile.get(tid)
        if teil and teil.get("kontroll_status") == "geprueft":
            anlagenteile.update(tid, {"kontroll_status": "offen"})


def _protokoll_datum(messungen: list[dict]) -> str:
    """Protokoll-Datum = frühestes Messpunkt-Datum. Ein Protokoll kann über
    mehrere Tage geführt werden; relevant ist der erste Tag. ISO-Daten sortieren
    chronologisch. Fallback heute, falls keine Zeile ein Datum hat."""
    daten = [m.get("datum") for m in messungen if m.get("datum")]
    return min(daten) if daten else date.today().isoformat()


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
        teil = anlagenteile.get(p.get("anlagenteil_id")) if p.get("anlagenteil_id") else None
        rows.append({"protokoll": p, "anlage": a, "kunde": k, "anlagenteil": teil})
    return render_template("protocols/list.html", rows=rows)


@bp.route("/uebersicht/kunde/<kunde_id>")
def messpunkte_kunde_ansicht(kunde_id: str):
    """Browser-Ansicht aller Messpunkte eines Kunden, gruppiert nach Anlagenteil.
    Im Browser drucken/als PDF speichern (Querformat via Druck-CSS) — fuer breite
    Tabellen zuverlaessiger als der direkte PDF-Download."""
    kunde = kunden.get(kunde_id)
    if not kunde:
        abort(404)
    gruppen, gesamt = messpunkte_gruppiert_fuer_kunde(kunde_id)
    return render_template(
        "protocols/messpunkte_kunde.html",
        kunde=kunde, gruppen=gruppen, gesamt=gesamt,
        messpunkt_felder=MESSPUNKT_FELDER,
    )


@bp.route("/neu", methods=["GET", "POST"])
def new_protocol():
    anlage_id = request.values.get("anlage_id", "")
    auftrag_id = request.values.get("auftrag_id", "")
    anlagenteil_id = request.values.get("anlagenteil_id", "")
    anlage = anlagen.get(anlage_id) if anlage_id else None
    auftrag = auftraege.get(auftrag_id) if auftrag_id else None
    teil = anlagenteile.get(anlagenteil_id) if anlagenteil_id else None
    if teil and not anlage:
        anlage = anlagen.get(teil.get("anlage_id"))
        anlage_id = anlage["id"] if anlage else anlage_id

    if request.method == "POST":
        anlage_id = request.form.get("anlage_id", "")
        anlage = anlagen.get(anlage_id)
        if not anlage:
            flash("Bitte eine Anlage wählen.", "warning")
            return redirect(url_for("protocols.new_protocol"))

        messungen = _parse_messpunkte(request.form)
        data = {
            "anlage_id": anlage_id,
            "auftrag_id": request.form.get("auftrag_id", "").strip() or None,
            "anlagenteil_id": request.form.get("anlagenteil_id", "").strip() or None,
            "datum": _protokoll_datum(messungen),
            "monteur": request.form.get("monteur", "").strip(),
            "messgeraet_ids": request.form.getlist("messgeraet_ids"),
            "bemerkungen": request.form.get("bemerkungen", "").strip(),
            "messungen": messungen,
        }
        if not data["messungen"]:
            flash("Mindestens ein Messpunkt muss erfasst werden.", "warning")
            return render_template(
                "protocols/edit.html",
                protokoll=data, anlage=anlage, auftrag=auftrag,
                alle_anlagen=_anlagen_sortiert(), kunden_index=_kunden_index(),
                alle_messgeraete=messgeraete_fuer_user(current_user.username, current_user.is_admin),
                teile_der_anlage=anlagenteile_fuer_anlage(anlage_id) if anlage else [],
                alle_monteure=list_monteure(), messpunkt_felder=MESSPUNKT_FELDER, select_optionen=SELECT_OPTIONEN, neu=True,
            )
        record = messprotokolle.create(data)
        _markiere_kontrollpflichtig(data)
        flash("Messprotokoll gespeichert.", "success")
        if request.form.get("aufbau_sync"):
            angelegt, aktualisiert = _aufbau_aus_messpunkten(anlage_id, messungen, data["anlagenteil_id"])
            if angelegt or aktualisiert:
                flash(f"Anlagenaufbau: {angelegt} Teil(e) angelegt, {aktualisiert} ergänzt.", "info")
        return redirect(url_for("protocols.detail", protokoll_id=record["id"]))

    # Vorausfüllen: wenn Auftrag mitgegeben, eine Zeile pro betroffenem Teil
    # dieser Anlage anlegen mit allen statischen Daten.
    datum_heute = date.today().isoformat()
    default_pruefer = current_user.name
    if auftrag and anlage:
        teile_idx = {t["id"]: t for t in anlagenteile.list()}
        relevante_teile = [
            teile_idx[tid] for tid in auftrag.get("anlagenteil_ids", [])
            if tid in teile_idx and teile_idx[tid].get("anlage_id") == anlage_id
        ]
        messungen = [_messpunkt_aus_teil(t, datum_heute, default_pruefer) for t in relevante_teile]
        if not messungen:
            messungen = [{"pruefer": default_pruefer, "datum": datum_heute} for _ in range(3)]
        protokoll = {
            "datum": datum_heute,
            "auftrag_id": auftrag_id,
            "monteur": default_pruefer,
            "bemerkungen": auftrag.get("titel", ""),
            "messungen": messungen,
        }
    elif teil and anlage:
        protokoll = {
            "datum": datum_heute,
            "anlagenteil_id": anlagenteil_id,
            "monteur": default_pruefer,
            "bemerkungen": "",
            "messungen": [_messpunkt_aus_teil(teil, datum_heute, default_pruefer)],
        }
    else:
        protokoll = {
            "datum": datum_heute,
            "monteur": default_pruefer,
            "messungen": [{"pruefer": default_pruefer} for _ in range(3)],
        }

    return render_template(
        "protocols/edit.html",
        protokoll=protokoll,
        anlage=anlage, auftrag=auftrag, vorausgewaehlter_teil=teil,
        alle_anlagen=_anlagen_sortiert(), kunden_index=_kunden_index(),
        alle_messgeraete=messgeraete_fuer_user(current_user.username, current_user.is_admin),
        teile_der_anlage=anlagenteile_fuer_anlage(anlage_id) if anlage else [],
        alle_monteure=list_monteure(), messpunkt_felder=MESSPUNKT_FELDER, select_optionen=SELECT_OPTIONEN, neu=True,
    )


@bp.route("/<protokoll_id>")
def detail(protokoll_id: str):
    p = messprotokolle.get(protokoll_id)
    if not p:
        abort(404)
    anlage = anlagen.get(p.get("anlage_id"))
    kunde = kunden.get(anlage.get("kunde_id")) if anlage else None
    ids = _messgeraet_ids_of(p)
    geraete = [messgeraete.get(gid) for gid in ids]
    geraete = [g for g in geraete if g]
    auftrag = auftraege.get(p.get("auftrag_id")) if p.get("auftrag_id") else None
    anlagenteil = anlagenteile.get(p.get("anlagenteil_id")) if p.get("anlagenteil_id") else None
    dokumentierte_teile = anlagenteile_mit_anhang(anlage["id"]) if anlage else []
    return render_template(
        "protocols/detail.html",
        protokoll=p, anlage=anlage, kunde=kunde, geraete=geraete, auftrag=auftrag,
        anlagenteil=anlagenteil,
        messpunkt_felder=MESSPUNKT_FELDER,
        dokumentierte_teile=dokumentierte_teile,
    )


@bp.route("/<protokoll_id>/geprueft", methods=["POST"])
def set_geprueft(protokoll_id: str):
    """Protokoll als geprüft markieren (oder Markierung entfernen). Beim Setzen
    werden die im Protokoll aufgeführten Anlagenteile auf 'geprueft' gesetzt."""
    p = messprotokolle.get(protokoll_id)
    if not p:
        abort(404)
    geprueft = request.form.get("geprueft") == "1"
    update = {"geprueft": geprueft}
    if geprueft:
        update["geprueft_am"] = date.today().isoformat()
        update["geprueft_von"] = current_user.name
        messprotokolle.update(protokoll_id, update)
        anzahl = _teile_als_geprueft_markieren(p)
        flash(f"Protokoll als geprüft markiert — {anzahl} Anlagenteil(e) auf 'geprüft' gesetzt.", "success")
    else:
        update["geprueft_am"] = None
        update["geprueft_von"] = None
        messprotokolle.update(protokoll_id, update)
        flash("Prüf-Markierung entfernt (Anlagenteile bleiben unverändert).", "info")
    return redirect(url_for("protocols.detail", protokoll_id=protokoll_id))


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
        messungen = _parse_messpunkte(request.form)
        data = {
            "anlage_id": request.form.get("anlage_id", p["anlage_id"]),
            "anlagenteil_id": request.form.get("anlagenteil_id", "").strip() or None,
            "datum": _protokoll_datum(messungen) if messungen else p.get("datum"),
            "monteur": request.form.get("monteur", "").strip(),
            "messgeraet_ids": request.form.getlist("messgeraet_ids"),
            "bemerkungen": request.form.get("bemerkungen", "").strip(),
            "messungen": messungen,
        }
        # Altes Single-Field beim Edit aufraeumen
        data["messgeraet_id"] = None
        messprotokolle.update(protokoll_id, data)
        flash("Messprotokoll aktualisiert.", "success")
        if request.form.get("aufbau_sync"):
            angelegt, aktualisiert = _aufbau_aus_messpunkten(data["anlage_id"], messungen, data["anlagenteil_id"])
            if angelegt or aktualisiert:
                flash(f"Anlagenaufbau: {angelegt} Teil(e) angelegt, {aktualisiert} ergänzt.", "info")
        return redirect(url_for("protocols.detail", protokoll_id=protokoll_id))

    # Vorausgewaehlt: bisher gewaehlte Messgeraete (auch aus altem messgeraet_id-Feld)
    p["messgeraet_ids"] = _messgeraet_ids_of(p)
    return render_template(
        "protocols/edit.html",
        protokoll=p, anlage=anlage,
        alle_anlagen=_anlagen_sortiert(), kunden_index=_kunden_index(),
        alle_messgeraete=messgeraete_fuer_user(current_user.username, current_user.is_admin),
        teile_der_anlage=anlagenteile_fuer_anlage(anlage["id"]) if anlage else [],
        alle_monteure=list_monteure(), messpunkt_felder=MESSPUNKT_FELDER, select_optionen=SELECT_OPTIONEN, neu=False,
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
