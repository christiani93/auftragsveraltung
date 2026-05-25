"""Aufträge — vom Kunden erteilte Arbeiten mit betroffenen Anlagenteilen."""
from __future__ import annotations

import mimetypes
import uuid
from datetime import date, datetime
from pathlib import Path

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from werkzeug.utils import secure_filename

import config
from models.repos import (
    AUFTRAG_STATUS,
    AUFTRAG_STATUS_LABEL,
    anlagen,
    anlagen_fuer_kunde,
    anlagen_ids_im_auftrag,
    anlagenteile,
    anlagenteile_fuer_anlage,
    auftraege,
    dauer_aus_zeitspanne,
    kunden,
    zeitbuchungen,
    zeitbuchungen_fuer_auftrag,
    zeitsumme_h,
)

bp = Blueprint("auftraege", __name__)

# ---- Bilder-Upload ----------------------------------------------------------

ERLAUBTE_BILD_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"}
MAX_BILD_BYTES = 15 * 1024 * 1024  # 15 MB pro Datei (Phone-Fotos sind oft groß)


def _bilder_dir(auftrag_id: str) -> Path:
    d = config.DATA_DIR / "auftrag_bilder" / auftrag_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ext_ok(filename: str) -> bool:
    return Path(filename).suffix.lower() in ERLAUBTE_BILD_EXTS


def _form_to_auftrag(form) -> dict:
    return {
        "kunde_id": form.get("kunde_id", "").strip(),
        "titel": form.get("titel", "").strip(),
        "beschreibung": form.get("beschreibung", "").strip(),
        "erteilungsdatum": form.get("erteilungsdatum", "").strip() or date.today().isoformat(),
        "erteilt_von": form.get("erteilt_von", "").strip(),
        "erteilt_von_telefon": form.get("erteilt_von_telefon", "").strip(),
        "anlagenteil_ids": form.getlist("anlagenteil_ids"),
        "status": form.get("status", "offen") if form.get("status") in AUFTRAG_STATUS else "offen",
        "erledigt_am": form.get("erledigt_am", "").strip() or None,
        "notizen": form.get("notizen", "").strip(),
    }


def _teile_strukturiert(kunde_id: str):
    """Liefert pro Anlage die Anlagenteile, gruppiert für die Checkbox-Auswahl."""
    result = []
    for a in sorted(anlagen_fuer_kunde(kunde_id), key=lambda x: x["bezeichnung"].lower()):
        teile = sorted(
            anlagenteile_fuer_anlage(a["id"]),
            key=lambda t: (t.get("typ", ""), t.get("bezeichnung", "")),
        )
        result.append({"anlage": a, "teile": teile})
    return result


@bp.route("/")
def list_auftraege():
    alle = sorted(auftraege.list(), key=lambda a: a.get("erteilungsdatum", ""), reverse=True)
    kunden_idx = {k["id"]: k for k in kunden.list()}
    rows = [{"auftrag": a, "kunde": kunden_idx.get(a.get("kunde_id"))} for a in alle]
    return render_template(
        "auftraege/list.html", rows=rows, status_label=AUFTRAG_STATUS_LABEL,
    )


@bp.route("/neu", methods=["GET", "POST"])
def new_auftrag():
    kunde_id = request.values.get("kunde_id", "")

    if request.method == "POST":
        data = _form_to_auftrag(request.form)
        if not data["kunde_id"] or not data["titel"]:
            flash("Kunde und Titel sind erforderlich.", "warning")
            kunde = kunden.get(data["kunde_id"]) if data["kunde_id"] else None
            return render_template(
                "auftraege/edit.html",
                auftrag=data, neu=True,
                alle_kunden=sorted(kunden.list(), key=lambda k: k["name"].lower()),
                kunde=kunde,
                anlagen_mit_teilen=_teile_strukturiert(data["kunde_id"]) if data["kunde_id"] else [],
                status_optionen=AUFTRAG_STATUS, status_label=AUFTRAG_STATUS_LABEL,
            )
        record = auftraege.create(data)
        flash(f"Auftrag „{record['titel']}“ angelegt.", "success")
        return redirect(url_for("auftraege.detail", auftrag_id=record["id"]))

    kunde = kunden.get(kunde_id) if kunde_id else None
    return render_template(
        "auftraege/edit.html",
        auftrag={
            "kunde_id": kunde_id,
            "erteilungsdatum": date.today().isoformat(),
            "status": "offen",
            "anlagenteil_ids": [],
        },
        neu=True,
        alle_kunden=sorted(kunden.list(), key=lambda k: k["name"].lower()),
        kunde=kunde,
        anlagen_mit_teilen=_teile_strukturiert(kunde_id) if kunde_id else [],
        status_optionen=AUFTRAG_STATUS, status_label=AUFTRAG_STATUS_LABEL,
    )


@bp.route("/<auftrag_id>")
def detail(auftrag_id: str):
    auftrag = auftraege.get(auftrag_id)
    if not auftrag:
        abort(404)
    kunde = kunden.get(auftrag.get("kunde_id"))
    teile_idx = {t["id"]: t for t in anlagenteile.list()}
    anlagen_idx = {a["id"]: a for a in anlagen.list()}
    betroffene = []
    for tid in auftrag.get("anlagenteil_ids", []):
        t = teile_idx.get(tid)
        if t:
            betroffene.append({"teil": t, "anlage": anlagen_idx.get(t.get("anlage_id"))})
    # Anlagen-IDs für "Messprotokoll erstellen"-Dropdown
    anlage_ids = anlagen_ids_im_auftrag(auftrag)
    auftrag_anlagen = [anlagen_idx[aid] for aid in anlage_ids if aid in anlagen_idx]
    eintraege = zeitbuchungen_fuer_auftrag(auftrag_id)
    return render_template(
        "auftraege/detail.html",
        auftrag=auftrag, kunde=kunde, betroffene=betroffene,
        auftrag_anlagen=auftrag_anlagen,
        zeitbuchungen=eintraege,
        zeitsumme=zeitsumme_h(eintraege),
        today_iso=date.today().isoformat(),
        status_label=AUFTRAG_STATUS_LABEL,
    )


@bp.route("/<auftrag_id>/bearbeiten", methods=["GET", "POST"])
def edit_auftrag(auftrag_id: str):
    auftrag = auftraege.get(auftrag_id)
    if not auftrag:
        abort(404)
    if request.method == "POST":
        data = _form_to_auftrag(request.form)
        if not data["kunde_id"] or not data["titel"]:
            flash("Kunde und Titel sind erforderlich.", "warning")
            return render_template(
                "auftraege/edit.html",
                auftrag={**auftrag, **data}, neu=False,
                alle_kunden=sorted(kunden.list(), key=lambda k: k["name"].lower()),
                kunde=kunden.get(data["kunde_id"]) if data["kunde_id"] else None,
                anlagen_mit_teilen=_teile_strukturiert(data["kunde_id"]) if data["kunde_id"] else [],
                status_optionen=AUFTRAG_STATUS, status_label=AUFTRAG_STATUS_LABEL,
            )
        auftraege.update(auftrag_id, data)
        flash("Auftrag gespeichert.", "success")
        return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id))
    return render_template(
        "auftraege/edit.html",
        auftrag=auftrag, neu=False,
        alle_kunden=sorted(kunden.list(), key=lambda k: k["name"].lower()),
        kunde=kunden.get(auftrag.get("kunde_id")),
        anlagen_mit_teilen=_teile_strukturiert(auftrag.get("kunde_id", "")),
        status_optionen=AUFTRAG_STATUS, status_label=AUFTRAG_STATUS_LABEL,
    )


@bp.route("/<auftrag_id>/status", methods=["POST"])
def set_status(auftrag_id: str):
    auftrag = auftraege.get(auftrag_id)
    if not auftrag:
        abort(404)
    neuer_status = request.form.get("status", "")
    if neuer_status not in AUFTRAG_STATUS:
        flash("Ungültiger Status.", "warning")
    else:
        update = {"status": neuer_status}
        if neuer_status == "erledigt" and not auftrag.get("erledigt_am"):
            update["erledigt_am"] = date.today().isoformat()
        auftraege.update(auftrag_id, update)
        flash(f"Status: {AUFTRAG_STATUS_LABEL[neuer_status]}.", "success")
    return redirect(request.referrer or url_for("auftraege.detail", auftrag_id=auftrag_id))


@bp.route("/<auftrag_id>/zeit/neu", methods=["POST"])
def add_zeitbuchung(auftrag_id: str):
    auftrag = auftraege.get(auftrag_id)
    if not auftrag:
        abort(404)
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
        return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id))

    zeitbuchungen.create({
        "auftrag_id": auftrag_id,
        "datum": request.form.get("datum", "").strip() or date.today().isoformat(),
        "mitarbeiter": request.form.get("mitarbeiter", "").strip(),
        "von_zeit": von,
        "bis_zeit": bis,
        "dauer_h": dauer,
        "taetigkeit": request.form.get("taetigkeit", "").strip(),
        "notizen": request.form.get("notizen", "").strip(),
    })
    flash(f"{dauer} h erfasst.", "success")
    return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id))


@bp.route("/zeit/<zeitbuchung_id>/loeschen", methods=["POST"])
def delete_zeitbuchung(zeitbuchung_id: str):
    z = zeitbuchungen.get(zeitbuchung_id)
    if not z:
        abort(404)
    auftrag_id = z.get("auftrag_id")
    zeitbuchungen.delete(zeitbuchung_id)
    flash("Zeitbuchung gelöscht.", "info")
    return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id) if auftrag_id else url_for("auftraege.list_auftraege"))


@bp.route("/<auftrag_id>/loeschen", methods=["POST"])
def delete_auftrag(auftrag_id: str):
    # Nur Admins duerfen Auftraege loeschen — verhindert versehentlichen Datenverlust
    if not getattr(current_user, "is_admin", False):
        flash("Nur Admins dürfen Aufträge löschen.", "danger")
        return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id))
    auftrag = auftraege.get(auftrag_id)
    if not auftrag:
        abort(404)
    # Zugehoerige Zeitbuchungen mitloeschen
    geloeschte_zb = 0
    for z in zeitbuchungen_fuer_auftrag(auftrag_id):
        zeitbuchungen.delete(z["id"])
        geloeschte_zb += 1
    # Bilderordner aufraeumen
    geloeschte_bilder = 0
    bild_dir = config.DATA_DIR / "auftrag_bilder" / auftrag_id
    if bild_dir.exists():
        for f in bild_dir.iterdir():
            try:
                f.unlink()
                geloeschte_bilder += 1
            except OSError:
                pass
        try:
            bild_dir.rmdir()
        except OSError:
            pass
    auftraege.delete(auftrag_id)
    parts = [f"Auftrag „{auftrag['titel']}“ gelöscht"]
    if geloeschte_zb:
        parts.append(f"{geloeschte_zb} Zeitbuchung(en)")
    if geloeschte_bilder:
        parts.append(f"{geloeschte_bilder} Bild(er)")
    flash(" — ".join(parts) + ".", "info")
    return redirect(url_for("auftraege.list_auftraege"))


# ---- Bilder-Routen ----------------------------------------------------------

@bp.route("/<auftrag_id>/bild/neu", methods=["POST"])
def upload_bild(auftrag_id: str):
    auftrag = auftraege.get(auftrag_id)
    if not auftrag:
        abort(404)
    files = request.files.getlist("bilder")
    if not files or all(not f.filename for f in files):
        flash("Keine Datei ausgewählt.", "warning")
        return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id))

    beschreibung = request.form.get("beschreibung", "").strip()
    bilder = list(auftrag.get("bilder") or [])
    erfolgreich = 0
    fehler: list[str] = []

    for f in files:
        if not f or not f.filename:
            continue
        if not _ext_ok(f.filename):
            fehler.append(f"{f.filename}: Format nicht unterstützt")
            continue
        # Groesse pruefen
        f.stream.seek(0, 2)
        size = f.stream.tell()
        f.stream.seek(0)
        if size == 0:
            fehler.append(f"{f.filename}: leere Datei")
            continue
        if size > MAX_BILD_BYTES:
            fehler.append(f"{f.filename}: zu groß (max {MAX_BILD_BYTES // (1024*1024)} MB)")
            continue

        bild_id = uuid.uuid4().hex[:12]
        ext = Path(f.filename).suffix.lower()
        dateiname = f"{bild_id}{ext}"
        ziel = _bilder_dir(auftrag_id) / dateiname
        f.save(str(ziel))
        bilder.append({
            "id": bild_id,
            "dateiname": dateiname,
            "original_name": secure_filename(f.filename) or dateiname,
            "beschreibung": beschreibung,
            "mime": mimetypes.guess_type(dateiname)[0] or "application/octet-stream",
            "groesse": size,
            "hochgeladen_am": datetime.now().isoformat(timespec="seconds"),
            "hochgeladen_von": getattr(current_user, "username", "") or "",
        })
        erfolgreich += 1

    if erfolgreich:
        auftraege.update(auftrag_id, {"bilder": bilder})
        flash(f"{erfolgreich} Bild(er) hochgeladen.", "success")
    for msg in fehler:
        flash(msg, "warning")
    return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id))


@bp.route("/<auftrag_id>/bild/<bild_id>")
def show_bild(auftrag_id: str, bild_id: str):
    auftrag = auftraege.get(auftrag_id)
    if not auftrag:
        abort(404)
    bild = next((b for b in (auftrag.get("bilder") or []) if b.get("id") == bild_id), None)
    if not bild:
        abort(404)
    pfad = _bilder_dir(auftrag_id) / bild["dateiname"]
    if not pfad.exists():
        abort(404)
    return send_file(str(pfad), mimetype=bild.get("mime") or "application/octet-stream")


@bp.route("/<auftrag_id>/bild/<bild_id>/loeschen", methods=["POST"])
def delete_bild(auftrag_id: str, bild_id: str):
    auftrag = auftraege.get(auftrag_id)
    if not auftrag:
        abort(404)
    bilder = list(auftrag.get("bilder") or [])
    bild = next((b for b in bilder if b.get("id") == bild_id), None)
    if not bild:
        abort(404)
    pfad = _bilder_dir(auftrag_id) / bild["dateiname"]
    try:
        if pfad.exists():
            pfad.unlink()
    except OSError:
        pass
    bilder = [b for b in bilder if b.get("id") != bild_id]
    auftraege.update(auftrag_id, {"bilder": bilder})
    flash("Bild gelöscht.", "info")
    return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id))
