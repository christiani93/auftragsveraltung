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
    AUFTRAG_STATUS_ARCHIVIERT,
    AUFTRAG_STATUS_LABEL,
    aktive_stempelung_von,
    anlagen,
    anlagen_fuer_kunde,
    anlagen_ids_im_auftrag,
    anlagenteile,
    anlagenteile_fuer_anlage,
    auftrag_bei_zeitbuchung_aktualisieren,
    auftrag_sichtbar_fuer,
    auftraege,
    auftraege_fuer_kunde,
    benachrichtigung_erstellen,
    dauer_aus_zeitspanne,
    ist_mitarbeiter_in_revision,
    kunden,
    revisionen,
    revisionen_fuer_kunde,
    todo_hinzufuegen,
    todo_loeschen,
    todo_toggle,
    zeitbuchungen,
    zeitbuchungen_fuer_auftrag,
    zeitsumme_h,
)
from models.users import find_user, list_monteure, list_projektleiter, list_users

bp = Blueprint("auftraege", __name__)

# ---- Bilder-Upload ----------------------------------------------------------

from PIL import Image, ImageOps

# HEIC/HEIF (iPhone-Fotos) ueber pillow-heif — wenn die Lib fehlt, gehts ohne.
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    _HEIF_OK = True
except Exception:  # pragma: no cover — best effort, kein hard fail
    _HEIF_OK = False

ERLAUBTE_BILD_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"}
MAX_BILD_BYTES = 25 * 1024 * 1024  # 25 MB Original — wird beim Speichern verkleinert
MAX_KANTE_PX = 1920                # max. laengste Kante in der gespeicherten Version
JPEG_QUALITY = 82                  # Qualitaet fuer JPEG-Re-Komprimierung


def _bilder_dir(auftrag_id: str) -> Path:
    d = config.DATA_DIR / "auftrag_bilder" / auftrag_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ext_ok(filename: str) -> bool:
    return Path(filename).suffix.lower() in ERLAUBTE_BILD_EXTS


def _bild_speichern_verarbeitet(stream, bild_id: str, original_ext: str, ziel_dir: Path) -> tuple[Path, str, int]:
    """Liest das hochgeladene Bild, dreht es nach EXIF gerade, verkleinert es auf
    MAX_KANTE_PX laengste Kante und speichert es. Liefert (Pfad, MIME, Groesse).

    PNGs mit Alpha-Kanal werden als PNG behalten (Screenshots/Diagramme), alles
    andere wird als progressive JPEG gespeichert — auch HEIC/HEIF vom iPhone.
    Liefert kein Bild zurueck wenn das Decoding fehlschlaegt (Exception nach oben).
    """
    img = Image.open(stream)
    img = ImageOps.exif_transpose(img)  # Handy-Fotos automatisch ausrichten
    img.thumbnail((MAX_KANTE_PX, MAX_KANTE_PX), Image.LANCZOS)

    behalte_png = original_ext.lower() == ".png" and img.mode in ("RGBA", "LA", "P")
    if behalte_png:
        dateiname = f"{bild_id}.png"
        ziel = ziel_dir / dateiname
        img.save(ziel, "PNG", optimize=True)
        mime = "image/png"
    else:
        if img.mode != "RGB":
            img = img.convert("RGB")
        dateiname = f"{bild_id}.jpg"
        ziel = ziel_dir / dateiname
        img.save(ziel, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
        mime = "image/jpeg"
    return ziel, mime, ziel.stat().st_size


def _form_to_auftrag(form) -> dict:
    return {
        "kunde_id": form.get("kunde_id", "").strip(),
        "titel": form.get("titel", "").strip(),
        "beschreibung": form.get("beschreibung", "").strip(),
        "erteilungsdatum": form.get("erteilungsdatum", "").strip() or date.today().isoformat(),
        "erteilt_von": form.get("erteilt_von", "").strip(),
        "erteilt_von_telefon": form.get("erteilt_von_telefon", "").strip(),
        "anlagenteil_ids": form.getlist("anlagenteil_ids"),
        "zugewiesen_an": form.get("zugewiesen_an", "").strip(),
        # Team-Zuordnung (Projektleiter, dem der Auftrag gehoert)
        "projektleiter": form.get("projektleiter", "").strip(),
        # Zusaetzlich freigegebene Monteure (sehen den Auftrag ebenfalls)
        "freigegeben_an": [u.strip() for u in form.getlist("freigegeben_an") if u.strip()],
        "status": form.get("status", "offen") if form.get("status") in AUFTRAG_STATUS else "offen",
        "erledigt_am": form.get("erledigt_am", "").strip() or None,
        "zu_erledigen_bis": form.get("zu_erledigen_bis", "").strip() or None,
        "termin": form.get("termin", "").strip() or None,
        "termin_datum": form.get("termin_datum", "").strip() or None,
        "revision_id": form.get("revision_id", "").strip() or None,
        "notizen": form.get("notizen", "").strip(),
    }


def _auftrag_team_setzen(data: dict) -> dict:
    """Legt das Team (projektleiter) des Auftrags fest. Monteur -> sein PL
    (Formularwert egal). Projektleiter -> sich selbst (falls leer). Admin -> frei."""
    if current_user.is_monteur:
        data["projektleiter"] = current_user.team_leiter or data.get("projektleiter") or ""
    elif current_user.is_projektleiter and not data.get("projektleiter"):
        data["projektleiter"] = current_user.username
    return data


def _benachrichtige_zuweisung(auftrag: dict, alt_zugewiesen: str = "") -> None:
    """Benachrichtigt den zugewiesenen Mitarbeiter, wenn die Zuweisung NEU auf
    ihn gesetzt wurde (und er nicht selbst der Zuweisende ist)."""
    neu = (auftrag.get("zugewiesen_an") or "").strip()
    if not neu or neu == (alt_zugewiesen or "").strip():
        return
    if neu == getattr(current_user, "username", None):
        return  # sich selbst nicht benachrichtigen
    von = getattr(current_user, "name", "") or getattr(current_user, "username", "")
    text = f"Dir wurde der Auftrag „{auftrag.get('titel') or '—'}“ zugewiesen (von {von})."
    benachrichtigung_erstellen(
        user=neu,
        text=text,
        auftrag_id=auftrag.get("id", ""),
        von=getattr(current_user, "username", ""),
    )
    # Web-Push (best effort — darf das Speichern nie brechen)
    try:
        from models.push import send_push_to_user
        send_push_to_user(neu, "Neuer Auftrag", text,
                          url=url_for("auftraege.detail", auftrag_id=auftrag.get("id", "")))
    except Exception:
        pass


def _darf_auftrag_sehen(auftrag: dict) -> bool:
    """Team-basierte Sichtbarkeit — siehe repos.auftrag_sichtbar_fuer."""
    return auftrag_sichtbar_fuer(auftrag, current_user)


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


@bp.route("/kunde/<kunde_id>/liste")
def kunde_liste(kunde_id: str):
    """Browser-Ansicht der Auftragsliste eines Kunden (drucken / als PDF speichern).
    Ohne Stunden/Zeitbuchungen und ohne Unterschriftsfeld."""
    kunde = kunden.get(kunde_id)
    if not kunde:
        abort(404)
    auftraege_liste = sorted(
        auftraege_fuer_kunde(kunde_id),
        key=lambda a: a.get("erteilungsdatum", ""),
        reverse=True,
    )
    return render_template(
        "auftraege/kunde_liste.html",
        kunde=kunde, auftraege=auftraege_liste,
        status_label=AUFTRAG_STATUS_LABEL,
    )


@bp.route("/")
def list_auftraege():
    archiv_anzeigen = request.args.get("archiv") == "1"
    revisionen_anzeigen = request.args.get("revisionen") == "1"
    alle = sorted(auftraege.list(), key=lambda a: a.get("erteilungsdatum", ""), reverse=True)
    sichtbar_alle = [a for a in alle if _darf_auftrag_sehen(a)]
    # Default: in Revisionen gebuendelte Auftraege ausblenden (sind 'Grossauftrag' der Revision)
    if not revisionen_anzeigen:
        sichtbar_alle = [a for a in sichtbar_alle if not a.get("revision_id")]
    # Status-Filter (offen/in_arbeit/erledigt/abgerechnet) — zeigt bei Auswahl
    # genau diesen Status (auch archivierte wie 'abgerechnet').
    status_filter = request.args.get("status", "").strip()
    if status_filter and status_filter in AUFTRAG_STATUS:
        sichtbar = [a for a in sichtbar_alle if a.get("status") == status_filter]
    elif archiv_anzeigen:
        sichtbar = sichtbar_alle
    else:
        sichtbar = [a for a in sichtbar_alle if a.get("status") not in AUFTRAG_STATUS_ARCHIVIERT]
    anzahl_archiviert = sum(1 for a in sichtbar_alle if a.get("status") in AUFTRAG_STATUS_ARCHIVIERT)
    anzahl_in_revision = sum(1 for a in alle if a.get("revision_id") and _darf_auftrag_sehen(a))

    # Filter nach zugewiesenem Mitarbeiter (nur fuer Admin/Projektleiter sinnvoll).
    # '__none__' = nicht zugewiesene Auftraege.
    zugewiesen_filter = request.args.get("zugewiesen", "").strip()
    if zugewiesen_filter and current_user.sieht_alle_auftraege:
        if zugewiesen_filter == "__none__":
            sichtbar = [a for a in sichtbar if not (a.get("zugewiesen_an") or "").strip()]
        else:
            sichtbar = [a for a in sichtbar if a.get("zugewiesen_an") == zugewiesen_filter]

    # Sortierung wie in der Dashboard-Übersicht: Termin (bzw. datum-only Termin)
    # zuerst, dann Fälligkeit, dann Erstellungsdatum. `sichtbar` ist bereits nach
    # Erstellungsdatum absteigend vorsortiert; sorted() ist stabil -> Ties behalten
    # diese Reihenfolge.
    def _termin_sort(a):
        termin = a.get("termin") or a.get("termin_datum") or "9999-99-99"
        frist = a.get("zu_erledigen_bis") or "9999-99-99"
        return (termin, frist)
    sichtbar = sorted(sichtbar, key=_termin_sort)

    kunden_idx = {k["id"]: k for k in kunden.list()}
    rev_idx = {r["id"]: r for r in revisionen.list()}
    rows = [{
        "auftrag": a,
        "kunde": kunden_idx.get(a.get("kunde_id")),
        "revision": rev_idx.get(a.get("revision_id") or ""),
    } for a in sichtbar]
    return render_template(
        "auftraege/list.html",
        rows=rows,
        status_label=AUFTRAG_STATUS_LABEL,
        gefiltert=not current_user.sieht_alle_auftraege,
        anzahl_total=len(alle),
        anzahl_sichtbar=len(sichtbar),
        anzahl_archiviert=anzahl_archiviert,
        archiv_anzeigen=archiv_anzeigen,
        anzahl_in_revision=anzahl_in_revision,
        revisionen_anzeigen=revisionen_anzeigen,
        zeigt_zuweisung=current_user.sieht_alle_auftraege,
        monteure=list_monteure() if current_user.sieht_alle_auftraege else [],
        zugewiesen_filter=zugewiesen_filter,
        status_optionen=AUFTRAG_STATUS,
        status_filter=status_filter,
    )


@bp.route("/neu", methods=["GET", "POST"])
def new_auftrag():
    kunde_id = request.values.get("kunde_id", "")
    vor_revision_id = request.values.get("revision_id", "")

    if request.method == "POST":
        data = _form_to_auftrag(request.form)
        if not data["titel"]:
            flash("Titel ist erforderlich.", "warning")
            kunde = kunden.get(data["kunde_id"]) if data["kunde_id"] else None
            return render_template(
                "auftraege/edit.html",
                auftrag=data, neu=True,
                alle_kunden=sorted(kunden.list(), key=lambda k: k["name"].lower()),
                kunde=kunde,
                anlagen_mit_teilen=_teile_strukturiert(data["kunde_id"]) if data["kunde_id"] else [],
                status_optionen=AUFTRAG_STATUS, status_label=AUFTRAG_STATUS_LABEL,
                monteure=list_monteure(), alle_projektleiter=list_projektleiter(),
                kunde_revisionen=revisionen_fuer_kunde(data["kunde_id"]) if data["kunde_id"] else [],
            )
        data = _auftrag_team_setzen(data)
        record = auftraege.create(data)
        _benachrichtige_zuweisung(record, alt_zugewiesen="")
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
            "revision_id": vor_revision_id,
        },
        neu=True,
        alle_kunden=sorted(kunden.list(), key=lambda k: k["name"].lower()),
        kunde=kunde,
        anlagen_mit_teilen=_teile_strukturiert(kunde_id) if kunde_id else [],
        status_optionen=AUFTRAG_STATUS, status_label=AUFTRAG_STATUS_LABEL,
        monteure=list_monteure(), alle_projektleiter=list_projektleiter(),
        kunde_revisionen=revisionen_fuer_kunde(kunde_id) if kunde_id else [],
    )


@bp.route("/<auftrag_id>")
def detail(auftrag_id: str):
    auftrag = auftraege.get(auftrag_id)
    if not auftrag:
        abort(404)
    if not _darf_auftrag_sehen(auftrag):
        abort(403)
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
    aktive_stempelung = aktive_stempelung_von(current_user.username) if current_user.is_authenticated else None
    # Mitarbeiter-Auswahl-Liste — nur fuer Admin/Projektleiter; Monteur stempelt nur fuer sich.
    moegliche_mitarbeiter = list_users() if current_user.sieht_alle_auftraege else []
    zugeordnete_revision = revisionen.get(auftrag.get("revision_id")) if auftrag.get("revision_id") else None
    return render_template(
        "auftraege/detail.html",
        auftrag=auftrag, kunde=kunde, betroffene=betroffene,
        auftrag_anlagen=auftrag_anlagen,
        zeitbuchungen=eintraege,
        zeitsumme=zeitsumme_h(eintraege),
        today_iso=date.today().isoformat(),
        status_label=AUFTRAG_STATUS_LABEL,
        aktive_stempelung=aktive_stempelung,
        moegliche_mitarbeiter=moegliche_mitarbeiter,
        zugeordnete_revision=zugeordnete_revision,
    )


@bp.route("/<auftrag_id>/bearbeiten", methods=["GET", "POST"])
def edit_auftrag(auftrag_id: str):
    auftrag = auftraege.get(auftrag_id)
    if not auftrag:
        abort(404)
    if not _darf_auftrag_sehen(auftrag):
        abort(403)
    if request.method == "POST":
        data = _form_to_auftrag(request.form)
        if not data["titel"]:
            flash("Titel ist erforderlich.", "warning")
            return render_template(
                "auftraege/edit.html",
                auftrag={**auftrag, **data}, neu=False,
                alle_kunden=sorted(kunden.list(), key=lambda k: k["name"].lower()),
                kunde=kunden.get(data["kunde_id"]) if data["kunde_id"] else None,
                anlagen_mit_teilen=_teile_strukturiert(data["kunde_id"]) if data["kunde_id"] else [],
                status_optionen=AUFTRAG_STATUS, status_label=AUFTRAG_STATUS_LABEL,
                monteure=list_monteure(), alle_projektleiter=list_projektleiter(),
                kunde_revisionen=revisionen_fuer_kunde(data["kunde_id"]) if data["kunde_id"] else [],
            )
        alt_zugewiesen = auftrag.get("zugewiesen_an") or ""
        # Bestehendes Team beibehalten, wenn kein neues gesetzt wird
        if not data.get("projektleiter"):
            data["projektleiter"] = auftrag.get("projektleiter") or ""
        data = _auftrag_team_setzen(data)
        auftraege.update(auftrag_id, data)
        _benachrichtige_zuweisung({**auftrag, **data, "id": auftrag_id}, alt_zugewiesen=alt_zugewiesen)
        flash("Auftrag gespeichert.", "success")
        return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id))
    return render_template(
        "auftraege/edit.html",
        auftrag=auftrag, neu=False,
        alle_kunden=sorted(kunden.list(), key=lambda k: k["name"].lower()),
        kunde=kunden.get(auftrag.get("kunde_id")),
        anlagen_mit_teilen=_teile_strukturiert(auftrag.get("kunde_id", "")),
        status_optionen=AUFTRAG_STATUS, status_label=AUFTRAG_STATUS_LABEL,
        monteure=list_monteure(), alle_projektleiter=list_projektleiter(),
        kunde_revisionen=revisionen_fuer_kunde(auftrag.get("kunde_id", "")),
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

    # Mitarbeiter: Admin/Projektleiter darf beliebig waehlen, Monteur immer self
    if current_user.sieht_alle_auftraege:
        mitarbeiter = request.form.get("mitarbeiter", "").strip()
    else:
        mitarbeiter = current_user.username

    zeitbuchungen.create({
        "auftrag_id": auftrag_id,
        "datum": request.form.get("datum", "").strip() or date.today().isoformat(),
        "mitarbeiter": mitarbeiter,
        "von_zeit": von,
        "bis_zeit": bis,
        "dauer_h": dauer,
        "taetigkeit": request.form.get("taetigkeit", "").strip(),
        "notizen": request.form.get("notizen", "").strip(),
    })
    auftrag_bei_zeitbuchung_aktualisieren(auftrag_id, mitarbeiter)
    flash(f"{dauer} h erfasst.", "success")
    return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id))


@bp.route("/zeit/<zeitbuchung_id>/mitarbeiter", methods=["POST"])
def set_zeitbuchung_mitarbeiter(zeitbuchung_id: str):
    """Aendert nur den Mitarbeiter einer bestehenden Zeitbuchung — nur Admin/Projektleiter."""
    if not current_user.sieht_alle_auftraege:
        abort(403)
    z = zeitbuchungen.get(zeitbuchung_id)
    if not z:
        abort(404)
    neuer = request.form.get("mitarbeiter", "").strip()
    # Validieren: leer ist OK (zurueck auf 'nicht zugeordnet'), sonst muss User existieren
    if neuer and not find_user(neuer):
        flash(f"Mitarbeiter „{neuer}“ nicht gefunden.", "warning")
        return redirect(url_for("auftraege.detail", auftrag_id=z.get("auftrag_id")) if z.get("auftrag_id") else url_for("auftraege.list_auftraege"))
    zeitbuchungen.update(zeitbuchung_id, {"mitarbeiter": neuer})
    flash("Mitarbeiter zugewiesen." if neuer else "Mitarbeiter entfernt.", "success")
    return redirect(url_for("auftraege.detail", auftrag_id=z.get("auftrag_id")) if z.get("auftrag_id") else url_for("auftraege.list_auftraege"))


@bp.route("/zeit/<zeitbuchung_id>/bearbeiten", methods=["GET", "POST"])
def edit_zeitbuchung(zeitbuchung_id: str):
    """Editiert alle Felder einer Zeitbuchung. Datum/Von/Bis/Dauer/Taetigkeit/
    Notizen + Mitarbeiter (Admin/Projektleiter) + Pause (von/bis)."""
    z = zeitbuchungen.get(zeitbuchung_id)
    if not z:
        abort(404)
    auftrag = auftraege.get(z.get("auftrag_id") or "")
    if auftrag and not _darf_auftrag_sehen(auftrag):
        abort(403)

    if request.method == "POST":
        # Pflichtfelder: Datum. Dauer-Quelle wird explizit gewaehlt:
        # 'vonbis' = aus den Stempelzeiten berechnen, 'manuell' = Stunden-Feld.
        datum = request.form.get("datum", "").strip() or date.today().isoformat()
        von = request.form.get("von_zeit", "").strip() or None
        bis = request.form.get("bis_zeit", "").strip() or None
        # Modus-Default rueckwaertskompatibel: ohne explizite Wahl wie bisher
        # (manuelle Dauer bevorzugt, sonst Von/Bis).
        dauer_modus = request.form.get("dauer_modus", "")
        dauer_str = request.form.get("dauer_h", "").strip()
        dauer = None
        if dauer_modus == "vonbis":
            if not (von and bis):
                flash("Für 'aus Von/Bis' bitte Von und Bis angeben.", "warning")
                return redirect(url_for("auftraege.edit_zeitbuchung", zeitbuchung_id=zeitbuchung_id))
            dauer = dauer_aus_zeitspanne(von, bis)
        elif dauer_modus == "manuell":
            if dauer_str:
                try:
                    dauer = round(float(dauer_str.replace(",", ".")), 2)
                except ValueError:
                    dauer = None
        else:
            # Kein Modus uebergeben (Alt-Form): manuell bevorzugt, sonst Von/Bis
            if dauer_str:
                try:
                    dauer = round(float(dauer_str.replace(",", ".")), 2)
                except ValueError:
                    dauer = None
            if dauer is None and von and bis:
                dauer = dauer_aus_zeitspanne(von, bis)
        if not dauer or dauer <= 0:
            flash("Bitte Stunden oder gueltige Von/Bis-Zeiten angeben.", "warning")
            return redirect(url_for("auftraege.edit_zeitbuchung", zeitbuchung_id=zeitbuchung_id))

        # Mitarbeiter: Admin/PL darf aendern, Monteur behaelt den bestehenden Wert
        if current_user.sieht_alle_auftraege:
            mitarbeiter = request.form.get("mitarbeiter", "").strip()
        else:
            mitarbeiter = z.get("mitarbeiter") or ""

        # Auftrag aendern/zuordnen (leer = ohne Auftrag). Validieren: muss sichtbar sein.
        neuer_auftrag_id = request.form.get("auftrag_id", "").strip()
        if neuer_auftrag_id:
            neuer_auftrag = auftraege.get(neuer_auftrag_id)
            if not neuer_auftrag or not _darf_auftrag_sehen(neuer_auftrag):
                flash("Auftrag nicht gefunden oder keine Berechtigung.", "warning")
                return redirect(url_for("auftraege.edit_zeitbuchung", zeitbuchung_id=zeitbuchung_id))

        # Pause: von/bis aus Form (beide leer = keine Pause)
        p_von = request.form.get("pause_von", "").strip()
        p_bis = request.form.get("pause_bis", "").strip()
        pause_h = None
        pause_setzen = False
        if p_von and p_bis:
            pv = _hhmm_to_minutes(p_von)
            pb = _hhmm_to_minutes(p_bis)
            bv = _hhmm_to_minutes(von) if von else None
            bb = _hhmm_to_minutes(bis) if bis else None
            if pv is None or pb is None or pb <= pv:
                flash("Pause-Zeit ungueltig — Pause wird ignoriert.", "warning")
            elif bv is None or bb is None or pv < bv or pb > bb:
                flash("Pause muss innerhalb des Buchungs-Zeitfensters liegen — Pause wird ignoriert.", "warning")
            else:
                pause_h = round((pb - pv) / 60.0, 2)
                pause_setzen = True

        # Brutto/Netto-Berechnung: gegebene Dauer ist brutto (vor Pause)
        if pause_setzen:
            brutto_h = round(float(dauer), 2)
            netto_h = round(max(0.0, brutto_h - pause_h), 2)
            updates = {
                "datum": datum, "mitarbeiter": mitarbeiter,
                "von_zeit": von, "bis_zeit": bis,
                "dauer_h": netto_h, "brutto_h": brutto_h,
                "pause_von": p_von, "pause_bis": p_bis, "pause_h_abgezogen": pause_h,
                "taetigkeit": request.form.get("taetigkeit", "").strip(),
                "notizen": request.form.get("notizen", "").strip(),
            }
        else:
            # keine Pause -> Pause-Felder loeschen, brutto_h ebenfalls
            updates = {
                "datum": datum, "mitarbeiter": mitarbeiter,
                "von_zeit": von, "bis_zeit": bis,
                "dauer_h": round(float(dauer), 2),
                "brutto_h": None,
                "pause_von": None, "pause_bis": None, "pause_h_abgezogen": None,
                "taetigkeit": request.form.get("taetigkeit", "").strip(),
                "notizen": request.form.get("notizen", "").strip(),
            }
        updates["auftrag_id"] = neuer_auftrag_id or None
        zeitbuchungen.update(zeitbuchung_id, updates)
        flash("Zeitbuchung gespeichert.", "success")
        if neuer_auftrag_id:
            return redirect(url_for("auftraege.detail", auftrag_id=neuer_auftrag_id))
        return redirect(url_for("zeit.heute"))

    moegliche_mitarbeiter = list_users() if current_user.sieht_alle_auftraege else []
    # Auswahl sichtbarer Auftraege (Kunde: Titel) zum Umzuordnen
    kunden_idx = {k["id"]: k for k in kunden.list()}
    auftrag_optionen = []
    for a in auftraege.list():
        if not _darf_auftrag_sehen(a):
            continue
        k = kunden_idx.get(a.get("kunde_id"))
        auftrag_optionen.append({"id": a["id"], "label": (f"{k['name']}: " if k else "") + (a.get("titel") or "—")})
    auftrag_optionen.sort(key=lambda o: o["label"].lower())
    return render_template(
        "auftraege/zeit_edit.html",
        z=z, auftrag=auftrag,
        moegliche_mitarbeiter=moegliche_mitarbeiter,
        auftrag_optionen=auftrag_optionen,
    )


def _hhmm_to_minutes(value: str) -> int | None:
    try:
        h, m = value.split(":")
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


@bp.route("/zeit/<zeitbuchung_id>/pause", methods=["POST"])
def set_pause(zeitbuchung_id: str):
    """Fuegt einer bestehenden Zeitbuchung eine Pause (von/bis) hinzu. Die
    dauer_h wird um die Pausen-Dauer reduziert; brutto_h und pause_h_abgezogen
    werden zur Dokumentation mitgespeichert (ueberschreiben evtl. bestehende
    Pause-Werte, max. 1 Pause pro Buchung)."""
    z = zeitbuchungen.get(zeitbuchung_id)
    if not z:
        abort(404)
    auftrag = auftraege.get(z.get("auftrag_id") or "")
    if auftrag and not _darf_auftrag_sehen(auftrag):
        abort(403)

    von_zeit = z.get("von_zeit")
    bis_zeit = z.get("bis_zeit")
    if not von_zeit or not bis_zeit:
        flash("Pause nur bei Buchungen mit Von/Bis-Zeit moeglich.", "warning")
        return redirect(url_for("auftraege.detail", auftrag_id=z.get("auftrag_id")) if z.get("auftrag_id") else url_for("auftraege.list_auftraege"))

    p_von = request.form.get("pause_von", "").strip()
    p_bis = request.form.get("pause_bis", "").strip()
    pv_min = _hhmm_to_minutes(p_von)
    pb_min = _hhmm_to_minutes(p_bis)
    bv_min = _hhmm_to_minutes(von_zeit)
    bb_min = _hhmm_to_minutes(bis_zeit)
    if pv_min is None or pb_min is None:
        flash("Pause-Zeit ungueltig (HH:MM erwartet).", "warning")
        return redirect(url_for("auftraege.detail", auftrag_id=z.get("auftrag_id")))
    if pb_min <= pv_min:
        flash("Pause-Ende muss nach dem Pause-Beginn liegen.", "warning")
        return redirect(url_for("auftraege.detail", auftrag_id=z.get("auftrag_id")))
    if bv_min is None or bb_min is None or pv_min < bv_min or pb_min > bb_min:
        flash(f"Pause muss innerhalb der Buchungs-Zeit ({von_zeit}–{bis_zeit}) liegen.", "warning")
        return redirect(url_for("auftraege.detail", auftrag_id=z.get("auftrag_id")))

    pause_h = round((pb_min - pv_min) / 60.0, 2)
    # Brutto = was VOR diesem Pause-Eingriff galt; falls schon eine Pause war,
    # ist brutto_h der gespeicherte Wert, sonst die aktuelle dauer_h.
    brutto_h = float(z.get("brutto_h") if z.get("brutto_h") is not None else z.get("dauer_h") or 0)
    netto_h = round(max(0.0, brutto_h - pause_h), 2)
    zeitbuchungen.update(zeitbuchung_id, {
        "pause_von": p_von,
        "pause_bis": p_bis,
        "pause_h_abgezogen": pause_h,
        "brutto_h": round(brutto_h, 2),
        "dauer_h": netto_h,
    })
    flash(f"Pause {p_von}–{p_bis} ({pause_h} h) abgezogen — Buchung jetzt {netto_h} h netto.", "success")
    return redirect(url_for("auftraege.detail", auftrag_id=z.get("auftrag_id")))


@bp.route("/zeit/<zeitbuchung_id>/pause/loeschen", methods=["POST"])
def delete_pause(zeitbuchung_id: str):
    """Entfernt die Pause einer Zeitbuchung und setzt dauer_h zurueck auf brutto."""
    z = zeitbuchungen.get(zeitbuchung_id)
    if not z:
        abort(404)
    auftrag = auftraege.get(z.get("auftrag_id") or "")
    if auftrag and not _darf_auftrag_sehen(auftrag):
        abort(403)
    if z.get("pause_von") is None and z.get("pause_h_abgezogen") is None:
        flash("Keine Pause gesetzt.", "warning")
        return redirect(url_for("auftraege.detail", auftrag_id=z.get("auftrag_id")))
    brutto_h = z.get("brutto_h")
    if brutto_h is None:
        # Fallback: dauer + abgezogene Pause
        brutto_h = (z.get("dauer_h") or 0) + (z.get("pause_h_abgezogen") or 0)
    zeitbuchungen.update(zeitbuchung_id, {
        "pause_von": None,
        "pause_bis": None,
        "pause_h_abgezogen": None,
        "brutto_h": None,
        "dauer_h": round(float(brutto_h), 2),
    })
    flash("Pause entfernt.", "info")
    return redirect(url_for("auftraege.detail", auftrag_id=z.get("auftrag_id")))


@bp.route("/zeit/<zeitbuchung_id>/loeschen", methods=["POST"])
def delete_zeitbuchung(zeitbuchung_id: str):
    z = zeitbuchungen.get(zeitbuchung_id)
    if not z:
        abort(404)
    auftrag_id = z.get("auftrag_id")
    datum = z.get("datum")
    zeitbuchungen.delete(zeitbuchung_id)
    flash("Zeitbuchung gelöscht.", "info")
    if auftrag_id:
        return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id))
    return redirect(url_for("zeit.heute", datum=datum) if datum else url_for("zeit.heute"))


@bp.route("/<auftrag_id>/todo/neu", methods=["POST"])
def add_todo(auftrag_id: str):
    a = auftraege.get(auftrag_id)
    if not a:
        abort(404)
    if not _darf_auftrag_sehen(a):
        abort(403)
    if not todo_hinzufuegen(auftraege, auftrag_id, request.form.get("text", "")):
        flash("ToDo-Text ist erforderlich.", "warning")
    return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id))


@bp.route("/<auftrag_id>/todo/<todo_id>/toggle", methods=["POST"])
def toggle_todo(auftrag_id: str, todo_id: str):
    a = auftraege.get(auftrag_id)
    if not a:
        abort(404)
    if not _darf_auftrag_sehen(a):
        abort(403)
    todo_toggle(auftraege, auftrag_id, todo_id)
    return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id))


@bp.route("/<auftrag_id>/todo/<todo_id>/loeschen", methods=["POST"])
def delete_todo(auftrag_id: str, todo_id: str):
    a = auftraege.get(auftrag_id)
    if not a:
        abort(404)
    if not _darf_auftrag_sehen(a):
        abort(403)
    todo_loeschen(auftraege, auftrag_id, todo_id)
    return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id))


@bp.route("/<auftrag_id>/person/neu", methods=["POST"])
def add_person(auftrag_id: str):
    """Fügt eine nicht im System erfasste Person zum Auftrag hinzu (Freitext)."""
    a = auftraege.get(auftrag_id)
    if not a:
        abort(404)
    if not _darf_auftrag_sehen(a):
        abort(403)
    name = request.form.get("name", "").strip()
    if name:
        personen = list(a.get("weitere_personen") or [])
        personen.append(name)
        auftraege.update(auftrag_id, {"weitere_personen": personen})
    return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id))


@bp.route("/<auftrag_id>/person/<int:idx>/loeschen", methods=["POST"])
def delete_person(auftrag_id: str, idx: int):
    a = auftraege.get(auftrag_id)
    if not a:
        abort(404)
    if not _darf_auftrag_sehen(a):
        abort(403)
    personen = list(a.get("weitere_personen") or [])
    if 0 <= idx < len(personen):
        personen.pop(idx)
        auftraege.update(auftrag_id, {"weitere_personen": personen})
    return redirect(url_for("auftraege.detail", auftrag_id=auftrag_id))


@bp.route("/<auftrag_id>/loeschen", methods=["POST"])
def delete_auftrag(auftrag_id: str):
    # Admin + Projektleiter duerfen Auftraege loeschen — verhindert versehentlichen Datenverlust durch Monteure
    if not getattr(current_user, "darf_auftrag_loeschen", False):
        flash("Nur Admin oder Projektleiter dürfen Aufträge löschen.", "danger")
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
        # Original-Groesse pruefen
        f.stream.seek(0, 2)
        size_orig = f.stream.tell()
        f.stream.seek(0)
        if size_orig == 0:
            fehler.append(f"{f.filename}: leere Datei")
            continue
        if size_orig > MAX_BILD_BYTES:
            fehler.append(f"{f.filename}: zu groß (max {MAX_BILD_BYTES // (1024*1024)} MB)")
            continue
        original_ext = Path(f.filename).suffix.lower()
        if original_ext in (".heic", ".heif") and not _HEIF_OK:
            fehler.append(f"{f.filename}: HEIC/HEIF wird vom Server nicht unterstützt — bitte als JPG hochladen.")
            continue

        bild_id = uuid.uuid4().hex[:12]
        try:
            ziel, mime, size_neu = _bild_speichern_verarbeitet(
                f.stream, bild_id, original_ext, _bilder_dir(auftrag_id)
            )
        except Exception as e:  # PIL kann Datei nicht lesen / kein Speicher / ...
            fehler.append(f"{f.filename}: Bild konnte nicht verarbeitet werden ({type(e).__name__}).")
            continue

        bilder.append({
            "id": bild_id,
            "dateiname": ziel.name,
            "original_name": secure_filename(f.filename) or ziel.name,
            "beschreibung": beschreibung,
            "mime": mime,
            "groesse": size_neu,
            "groesse_original": size_orig,
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
