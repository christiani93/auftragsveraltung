from __future__ import annotations

import uuid
from datetime import date, datetime
from pathlib import Path

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from werkzeug.utils import secure_filename

import config
from models.repos import (
    ANLAGENTEIL_TYPEN,
    KONTROLL_STATUS,
    KONTROLL_STATUS_LABEL,
    SPANNUNG_LABEL,
    SPANNUNG_TYPEN,
    anlagen,
    anlagen_fuer_kunde,
    anlagenteile,
    anlagenteile_fuer_anlage,
    baue_aufbau_baum,
    fi_erforderlich,
    kunden,
    messprotokolle_fuer_anlage,
    moegliche_eltern,
)

bp = Blueprint("installations", __name__)

# ---- Datei-Anhang fuer Anlagenteile (Foto / Legende / Schema bei Verteilungen) ----

from PIL import Image, ImageOps

# HEIC/HEIF (iPhone-Fotos) ueber pillow-heif — wenn die Lib fehlt, gehts ohne.
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    _HEIF_OK = True
except Exception:  # pragma: no cover — best effort, kein hard fail
    _HEIF_OK = False

TEIL_DATEI_KATEGORIEN = {"foto": "Foto", "legende": "Legende", "schema": "Schema"}
ERLAUBTE_TEIL_BILD_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"}
ERLAUBTE_TEIL_EXTS = ERLAUBTE_TEIL_BILD_EXTS | {".pdf"}
MAX_TEIL_DATEI_BYTES = 25 * 1024 * 1024  # 25 MB Original — Bilder werden verkleinert
MAX_KANTE_PX = 1920                       # max. laengste Kante in der gespeicherten Version
JPEG_QUALITY = 82


def _teil_dateien_dir(teil_id: str) -> Path:
    d = config.DATA_DIR / "anlagenteil_dateien" / teil_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _teil_datei_speichern(stream, datei_id: str, original_ext: str, ziel_dir: Path) -> tuple[Path, str, int]:
    """Speichert eine hochgeladene Datei und liefert (Pfad, MIME, Groesse).

    Bilder werden nach EXIF gerade gedreht und auf MAX_KANTE_PX verkleinert;
    PDFs (Schema/Legende) werden unveraendert abgelegt.
    """
    if original_ext == ".pdf":
        ziel = ziel_dir / f"{datei_id}.pdf"
        stream.seek(0)
        ziel.write_bytes(stream.read())
        return ziel, "application/pdf", ziel.stat().st_size
    img = Image.open(stream)
    img = ImageOps.exif_transpose(img)  # Handy-Fotos automatisch ausrichten
    img.thumbnail((MAX_KANTE_PX, MAX_KANTE_PX), Image.LANCZOS)
    behalte_png = original_ext == ".png" and img.mode in ("RGBA", "LA", "P")
    if behalte_png:
        ziel = ziel_dir / f"{datei_id}.png"
        img.save(ziel, "PNG", optimize=True)
        mime = "image/png"
    else:
        if img.mode != "RGB":
            img = img.convert("RGB")
        ziel = ziel_dir / f"{datei_id}.jpg"
        img.save(ziel, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
        mime = "image/jpeg"
    return ziel, mime, ziel.stat().st_size


def _form_to_anlage(form) -> dict:
    return {
        "kunde_id": form.get("kunde_id", "").strip(),
        "bezeichnung": form.get("bezeichnung", "").strip(),
        "standort": form.get("standort", "").strip(),
        "baujahr": form.get("baujahr", "").strip(),
        "naechste_periodische_kontrolle": form.get("naechste_periodische_kontrolle", "").strip(),
        "notizen": form.get("notizen", "").strip(),
    }


def _form_to_teil(form) -> dict:
    return {
        "anlage_id": form.get("anlage_id", "").strip(),
        "parent_id": form.get("parent_id", "").strip() or None,
        "typ": form.get("typ", "").strip() or "Sonstiges",
        "bezeichnung": form.get("bezeichnung", "").strip(),
        "beschreibung": form.get("beschreibung", "").strip(),
        "spannung": form.get("spannung", "").strip() or None,
        "leistung_kw": form.get("leistung_kw", "").strip() or None,
        "stromstaerke_a": form.get("stromstaerke_a", "").strip() or None,
        "gemessen_ik_a": form.get("gemessen_ik_a", "").strip() or None,
        # Technische Daten — für Messprotokoll-Vorausfüllung
        "kabel": form.get("kabel", "").strip(),
        "sicherungsnr": form.get("sicherungsnr", "").strip(),
        "sicherungstyp": form.get("sicherungstyp", "").strip(),
        "fi_typ_ma": form.get("fi_typ_ma", "").strip(),
        # Kontroll-Status — neue Anlagenteile gelten als geprueft (Bestand);
        # kontrollpflichtig werden sie erst, wenn ein Messprotokoll erstellt wird.
        "kontroll_status": form.get("kontroll_status", "geprueft"),
        "letzte_kontrolle": form.get("letzte_kontrolle", "").strip(),
        "kontrolleur": form.get("kontrolleur", "").strip(),
        "notizen": form.get("notizen", "").strip(),
    }


def _teil_edit_context(teil: dict, anlage: dict, neu: bool, **extra) -> dict:
    """Kontextdaten für das Anlagenteil-Edit-Template."""
    ausgenommen = teil.get("id") if not neu else None
    return dict(
        teil=teil, anlage=anlage, neu=neu,
        typen=ANLAGENTEIL_TYPEN,
        status_optionen=KONTROLL_STATUS, status_label=KONTROLL_STATUS_LABEL,
        spannung_typen=SPANNUNG_TYPEN,
        eltern_optionen=moegliche_eltern(anlage["id"], ausgenommen) if anlage else [],
        fi_erforderlich=fi_erforderlich(teil.get("typ")),
        teil_datei_kategorien=TEIL_DATEI_KATEGORIEN,
        **extra,
    )


@bp.route("/")
def list_installations():
    alle = anlagen.list()
    kunden_index = {k["id"]: k for k in kunden.list()}
    rows = [{"anlage": a, "kunde": kunden_index.get(a.get("kunde_id"))} for a in alle]
    rows.sort(key=lambda r: ((r["kunde"]["name"] if r["kunde"] else "").lower(), r["anlage"]["bezeichnung"].lower()))
    return render_template("installations/list.html", rows=rows)


@bp.route("/neu", methods=["GET", "POST"])
def new_installation():
    if request.method == "POST":
        data = _form_to_anlage(request.form)
        if not data["bezeichnung"] or not data["kunde_id"]:
            flash("Kunde und Bezeichnung sind erforderlich.", "warning")
            return render_template(
                "installations/edit.html",
                anlage=data, neu=True, alle_kunden=sorted(kunden.list(), key=lambda k: k["name"].lower()),
            )
        record = anlagen.create(data)
        flash(f"Anlage „{record['bezeichnung']}“ angelegt.", "success")
        return redirect(url_for("installations.detail", anlage_id=record["id"]))

    vorgewaehlt = request.args.get("kunde_id", "")
    return render_template(
        "installations/edit.html",
        anlage={"kunde_id": vorgewaehlt}, neu=True,
        alle_kunden=sorted(kunden.list(), key=lambda k: k["name"].lower()),
    )


@bp.route("/<anlage_id>")
def detail(anlage_id: str):
    anlage = anlagen.get(anlage_id)
    if not anlage:
        abort(404)
    kunde = kunden.get(anlage.get("kunde_id")) if anlage.get("kunde_id") else None
    teile = anlagenteile_fuer_anlage(anlage_id)
    teile_by_id = {t["id"]: t for t in teile}
    teile.sort(key=lambda t: (t.get("typ", ""), t.get("bezeichnung", "")))
    protokolle = messprotokolle_fuer_anlage(anlage_id)
    protokolle.sort(key=lambda p: p.get("datum", ""), reverse=True)
    return render_template(
        "installations/detail.html",
        anlage=anlage, kunde=kunde, teile=teile, teile_by_id=teile_by_id,
        protokolle=protokolle,
        kontroll_status_label=KONTROLL_STATUS_LABEL,
        spannung_label=SPANNUNG_LABEL,
    )


@bp.route("/<anlage_id>/aufbau")
def aufbau(anlage_id: str):
    anlage = anlagen.get(anlage_id)
    if not anlage:
        abort(404)
    kunde = kunden.get(anlage.get("kunde_id")) if anlage.get("kunde_id") else None
    roots = baue_aufbau_baum(anlage_id)
    return render_template(
        "installations/aufbau.html",
        anlage=anlage, kunde=kunde, roots=roots,
        spannung_label=SPANNUNG_LABEL,
    )


@bp.route("/<anlage_id>/bearbeiten", methods=["GET", "POST"])
def edit_installation(anlage_id: str):
    anlage = anlagen.get(anlage_id)
    if not anlage:
        abort(404)
    if request.method == "POST":
        data = _form_to_anlage(request.form)
        if not data["bezeichnung"] or not data["kunde_id"]:
            flash("Kunde und Bezeichnung sind erforderlich.", "warning")
            return render_template(
                "installations/edit.html",
                anlage={**anlage, **data}, neu=False,
                alle_kunden=sorted(kunden.list(), key=lambda k: k["name"].lower()),
            )
        anlagen.update(anlage_id, data)
        flash("Anlage gespeichert.", "success")
        return redirect(url_for("installations.detail", anlage_id=anlage_id))
    return render_template(
        "installations/edit.html",
        anlage=anlage, neu=False,
        alle_kunden=sorted(kunden.list(), key=lambda k: k["name"].lower()),
    )


@bp.route("/<anlage_id>/loeschen", methods=["POST"])
def delete_installation(anlage_id: str):
    anlage = anlagen.get(anlage_id)
    if not anlage:
        abort(404)
    for teil in anlagenteile_fuer_anlage(anlage_id):
        anlagenteile.delete(teil["id"])
    anlagen.delete(anlage_id)
    flash("Anlage gelöscht.", "info")
    kunde_id = anlage.get("kunde_id")
    return redirect(url_for("customers.detail", kunde_id=kunde_id) if kunde_id else url_for("installations.list_installations"))


# ----- Anlagenteile -----------------------------------------------------------

@bp.route("/<anlage_id>/teil/neu", methods=["GET", "POST"])
def new_teil(anlage_id: str):
    anlage = anlagen.get(anlage_id)
    if not anlage:
        abort(404)
    if request.method == "POST":
        data = _form_to_teil(request.form)
        data["anlage_id"] = anlage_id
        if not data["bezeichnung"]:
            flash("Bezeichnung ist erforderlich.", "warning")
            return render_template("installations/teil_edit.html", **_teil_edit_context(data, anlage, neu=True))
        anlagenteile.create(data)
        flash(f"Anlagenteil „{data['bezeichnung']}“ erfasst.", "success")
        return redirect(url_for("installations.detail", anlage_id=anlage_id))

    vorgewaehlt_parent = request.args.get("parent_id", "")
    return render_template(
        "installations/teil_edit.html",
        **_teil_edit_context(
            {"anlage_id": anlage_id, "kontroll_status": "geprueft", "parent_id": vorgewaehlt_parent},
            anlage, neu=True,
        ),
    )


@bp.route("/teil/<teil_id>/bearbeiten", methods=["GET", "POST"])
def edit_teil(teil_id: str):
    teil = anlagenteile.get(teil_id)
    if not teil:
        abort(404)
    anlage = anlagen.get(teil.get("anlage_id"))
    if request.method == "POST":
        data = _form_to_teil(request.form)
        data["anlage_id"] = teil["anlage_id"]
        if not data["bezeichnung"]:
            flash("Bezeichnung ist erforderlich.", "warning")
            return render_template(
                "installations/teil_edit.html",
                **_teil_edit_context({**teil, **data}, anlage, neu=False),
            )
        anlagenteile.update(teil_id, data)
        flash("Anlagenteil gespeichert.", "success")
        return redirect(url_for("installations.detail", anlage_id=teil["anlage_id"]))
    return render_template("installations/teil_edit.html", **_teil_edit_context(teil, anlage, neu=False))


@bp.route("/teil/<teil_id>/status", methods=["POST"])
def set_teil_status(teil_id: str):
    """Schnellaktion: Status eines Anlagenteils setzen (für Kontroll-Übersicht)."""
    teil = anlagenteile.get(teil_id)
    if not teil:
        abort(404)
    neuer_status = request.form.get("status", "")
    if neuer_status not in KONTROLL_STATUS:
        flash("Ungültiger Status.", "warning")
    else:
        update = {"kontroll_status": neuer_status}
        if neuer_status == "geprueft":
            update["letzte_kontrolle"] = request.form.get("datum") or date.today().isoformat()
            kontrolleur = request.form.get("kontrolleur", "").strip()
            if kontrolleur:
                update["kontrolleur"] = kontrolleur
        anlagenteile.update(teil_id, update)
        flash(f"Status aktualisiert: {KONTROLL_STATUS_LABEL[neuer_status]}.", "success")
    return redirect(request.referrer or url_for("installations.detail", anlage_id=teil["anlage_id"]))


@bp.route("/teil/<teil_id>/loeschen", methods=["POST"])
def delete_teil(teil_id: str):
    teil = anlagenteile.get(teil_id)
    if not teil:
        abort(404)
    anlage_id = teil["anlage_id"]
    # Datei-Anhaenge (Foto/Legende/Schema) mitloeschen
    datei_dir = config.DATA_DIR / "anlagenteil_dateien" / teil_id
    if datei_dir.exists():
        for f in datei_dir.iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        try:
            datei_dir.rmdir()
        except OSError:
            pass
    anlagenteile.delete(teil_id)
    flash("Anlagenteil gelöscht.", "info")
    return redirect(url_for("installations.detail", anlage_id=anlage_id))


@bp.route("/teil/<teil_id>/datei/<kategorie>", methods=["POST"])
def upload_teil_datei(teil_id: str, kategorie: str):
    """Foto / Legende / Schema zu einem Anlagenteil (Verteilung) hochladen."""
    teil = anlagenteile.get(teil_id)
    if not teil:
        abort(404)
    if kategorie not in TEIL_DATEI_KATEGORIEN:
        abort(404)
    files = request.files.getlist("dateien")
    if not files or all(not f.filename for f in files):
        flash("Keine Datei ausgewählt.", "warning")
        return redirect(url_for("installations.edit_teil", teil_id=teil_id))

    anhang = list(teil.get("anhang") or [])
    erfolgreich = 0
    fehler: list[str] = []
    for f in files:
        if not f or not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in ERLAUBTE_TEIL_EXTS:
            fehler.append(f"{f.filename}: Format nicht unterstützt (Bild oder PDF)")
            continue
        f.stream.seek(0, 2)
        size_orig = f.stream.tell()
        f.stream.seek(0)
        if size_orig == 0:
            fehler.append(f"{f.filename}: leere Datei")
            continue
        if size_orig > MAX_TEIL_DATEI_BYTES:
            fehler.append(f"{f.filename}: zu groß (max {MAX_TEIL_DATEI_BYTES // (1024*1024)} MB)")
            continue
        if ext in (".heic", ".heif") and not _HEIF_OK:
            fehler.append(f"{f.filename}: HEIC/HEIF wird vom Server nicht unterstützt — bitte als JPG oder PDF.")
            continue
        datei_id = uuid.uuid4().hex[:12]
        try:
            ziel, mime, size_neu = _teil_datei_speichern(f.stream, datei_id, ext, _teil_dateien_dir(teil_id))
        except Exception as e:
            fehler.append(f"{f.filename}: konnte nicht verarbeitet werden ({type(e).__name__}).")
            continue
        anhang.append({
            "id": datei_id,
            "kategorie": kategorie,
            "dateiname": ziel.name,
            "original_name": secure_filename(f.filename) or ziel.name,
            "mime": mime,
            "groesse": size_neu,
            "groesse_original": size_orig,
            "hochgeladen_am": datetime.now().isoformat(timespec="seconds"),
            "hochgeladen_von": getattr(current_user, "username", "") or "",
        })
        erfolgreich += 1

    if erfolgreich:
        anlagenteile.update(teil_id, {"anhang": anhang})
        flash(f"{erfolgreich} Datei(en) hochgeladen ({TEIL_DATEI_KATEGORIEN[kategorie]}).", "success")
    for msg in fehler:
        flash(msg, "warning")
    return redirect(url_for("installations.edit_teil", teil_id=teil_id))


@bp.route("/teil/<teil_id>/datei/<datei_id>/anzeigen")
def show_teil_datei(teil_id: str, datei_id: str):
    teil = anlagenteile.get(teil_id)
    if not teil:
        abort(404)
    datei = next((d for d in (teil.get("anhang") or []) if d.get("id") == datei_id), None)
    if not datei:
        abort(404)
    pfad = _teil_dateien_dir(teil_id) / datei["dateiname"]
    if not pfad.exists():
        abort(404)
    return send_file(str(pfad), mimetype=datei.get("mime") or "application/octet-stream")


@bp.route("/teil/<teil_id>/datei/<datei_id>/loeschen", methods=["POST"])
def delete_teil_datei(teil_id: str, datei_id: str):
    teil = anlagenteile.get(teil_id)
    if not teil:
        abort(404)
    anhang = list(teil.get("anhang") or [])
    datei = next((d for d in anhang if d.get("id") == datei_id), None)
    if not datei:
        abort(404)
    pfad = _teil_dateien_dir(teil_id) / datei["dateiname"]
    try:
        if pfad.exists():
            pfad.unlink()
    except OSError:
        pass
    anhang = [d for d in anhang if d.get("id") != datei_id]
    anlagenteile.update(teil_id, {"anhang": anhang})
    flash("Datei gelöscht.", "info")
    return redirect(url_for("installations.edit_teil", teil_id=teil_id))
