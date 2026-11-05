"""Lagermaterial: eigener Lagerbestand (unabhaengig von Auftraegen).

Artikel werden per Barcode/E-Nummer eingescannt, einem Lagerort zugeordnet und
koennen fuer eine Bestellliste (mit gedruckten Barcodes) markiert werden. Der
Scan-Eingang funktioniert mit einem USB-Handscanner (Tastatur-Emulation: Code +
Enter) und optional per Kamera (JS) auf Tablet/Handy.

Bearbeiten/Loeschen/Bestand aendern ist fuer alle Modul-Nutzer erlaubt.
"""
from __future__ import annotations

import io
import math
from datetime import date

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from models.lieferschein import normalisiere_bestellnr, parse_lieferschein_fuers_lager
from models.produktsuche import ean_lookup
from models.repos import (
    lager_lieferanten_liste,
    lagerartikel,
    lagerartikel_by_barcode,
    lagerartikel_nachzubestellen,
    lagerartikel_ohne_ean,
    lagerartikel_sortiert,
    lagerlieferschein_speichern,
    lagerlieferscheine,
    lagerlieferscheine_sortiert,
    lagerorte_liste,
    lagerverbrauch_uebersicht,
)

bp = Blueprint("lager", __name__)


def _als_float_oder_none(wert):
    """'1', '1.5', '2,0' -> float; leer/ungueltig -> None."""
    s = (wert or "").strip().replace("'", "").replace("’", "").replace(",", ".")
    if not s:
        return None
    try:
        return round(float(s), 3)
    except ValueError:
        return None


def _als_int(wert, default=0):
    try:
        return int(str(wert).strip())
    except (TypeError, ValueError):
        return default


def _als_int_oder_none(wert):
    s = (wert or "").strip() if isinstance(wert, str) else wert
    if s in ("", None):
        return None
    return _als_int(s, 0)


def _form_zu_artikel() -> dict:
    """Liest die gemeinsamen Artikel-Felder aus dem Formular."""
    return {
        "bezeichnung": (request.form.get("bezeichnung") or "").strip(),
        "barcode": (request.form.get("barcode") or "").strip(),
        "bestellnummer": (request.form.get("bestellnummer") or "").strip(),
        "lagerort": (request.form.get("lagerort") or "").strip(),
        "lieferant": (request.form.get("lieferant") or "").strip(),
        "einheit": (request.form.get("einheit") or "Stk").strip() or "Stk",
        "bestand": _als_int_oder_none(request.form.get("bestand")),
        "mindestbestand": _als_int(request.form.get("mindestbestand"), 0),
        "bestellmenge": _als_int_oder_none(request.form.get("bestellmenge")),
        "nachbestellen": request.form.get("nachbestellen") == "on",
        "notizen": (request.form.get("notizen") or "").strip(),
    }


# ----- Uebersicht -------------------------------------------------------------

@bp.route("/")
@login_required
def liste():
    suche = (request.args.get("q") or "").strip()
    nur_bestellen = request.args.get("bestellen") == "1"

    artikel = lagerartikel_sortiert()
    if suche:
        s = suche.lower()
        artikel = [
            a for a in artikel
            if s in (a.get("bezeichnung") or "").lower()
            or s in (a.get("barcode") or "").lower()
            or s in (a.get("bestellnummer") or "").lower()
            or s in (a.get("lagerort") or "").lower()
            or s in (a.get("lieferant") or "").lower()
        ]
    bestell_ids = {a["id"] for a in lagerartikel_nachzubestellen()}
    if nur_bestellen:
        artikel = [a for a in artikel if a["id"] in bestell_ids]

    # Nach Lagerort gruppieren (Reihenfolge bleibt: lagerartikel_sortiert)
    gruppen: list[tuple[str, list]] = []
    idx: dict[str, int] = {}
    for a in artikel:
        ort = (a.get("lagerort") or "").strip() or "Ohne Lagerort"
        if ort not in idx:
            idx[ort] = len(gruppen)
            gruppen.append((ort, []))
        gruppen[idx[ort]][1].append(a)

    return render_template(
        "lager/liste.html",
        gruppen=gruppen,
        anzahl=len(artikel),
        suche=suche,
        nur_bestellen=nur_bestellen,
        bestell_ids=bestell_ids,
        anzahl_bestellen=len(bestell_ids),
    )


# ----- Scannen ----------------------------------------------------------------

@bp.route("/scan")
@login_required
def scan():
    """Scan-Seite: Barcode eingeben/scannen -> Weiterleitung auf Artikel bzw.
    neues Formular. Reiner Sammelpunkt fuer den Handscanner."""
    return render_template("lager/scan.html")


@bp.route("/nach-barcode")
@login_required
def nach_barcode():
    """Sucht einen Artikel nach eingescanntem Code. Gefunden -> Bearbeiten,
    sonst -> neues Formular mit vorbefuelltem Barcode."""
    code = (request.args.get("code") or "").strip()
    if not code:
        flash("Kein Code eingegeben.", "warning")
        return redirect(url_for("lager.scan"))
    treffer = lagerartikel_by_barcode(code)
    if treffer:
        flash(f"Artikel gefunden: {treffer.get('bezeichnung') or code}", "success")
        return redirect(url_for("lager.bearbeiten", artikel_id=treffer["id"]))
    flash(f"Kein Artikel mit Code «{code}» — bitte neu anlegen.", "info")
    return redirect(url_for("lager.neu", barcode=code))


@bp.route("/produktdaten")
@login_required
def produktdaten():
    """Best-Effort-Produktsuche zu einem EAN (JSON). Fuer den 'Produktdaten
    suchen'-Button im Formular. Kein Treffer ist normal (Fachhandelsartikel)."""
    code = (request.args.get("code") or "").strip()
    if not code:
        return jsonify({"found": False, "grund": "kein Code"})
    treffer = ean_lookup(code)
    if not treffer:
        return jsonify({"found": False})
    return jsonify({"found": True, **treffer})


# ----- Anlegen / Bearbeiten ---------------------------------------------------

@bp.route("/neu", methods=["GET", "POST"])
@login_required
def neu():
    if request.method == "POST":
        daten = _form_zu_artikel()
        if not daten["bezeichnung"]:
            flash("Bezeichnung ist erforderlich.", "warning")
            return render_template(
                "lager/edit.html", neu=True, artikel=daten,
                lagerorte=lagerorte_liste(), lieferanten=lager_lieferanten_liste(),
            )
        neuer = lagerartikel.create(daten)
        flash("Artikel angelegt.", "success")
        return redirect(url_for("lager.bearbeiten", artikel_id=neuer["id"]))

    vorbefuellt = {
        "einheit": "Stk",
        "barcode": (request.args.get("barcode") or "").strip(),
        "bestellnummer": (request.args.get("bestellnummer") or "").strip(),
        "bezeichnung": (request.args.get("bezeichnung") or "").strip(),
    }
    return render_template(
        "lager/edit.html", neu=True, artikel=vorbefuellt,
        lagerorte=lagerorte_liste(), lieferanten=lager_lieferanten_liste(),
    )


@bp.route("/<artikel_id>/bearbeiten", methods=["GET", "POST"])
@login_required
def bearbeiten(artikel_id: str):
    artikel = lagerartikel.get(artikel_id)
    if not artikel:
        abort(404)
    if request.method == "POST":
        daten = _form_zu_artikel()
        if not daten["bezeichnung"]:
            flash("Bezeichnung ist erforderlich.", "warning")
        else:
            # Bestand im Formular geaendert (Inventur) -> Stichtag neu setzen
            if daten.get("bestand") != artikel.get("bestand"):
                daten["bestand_stand"] = date.today().isoformat()
            lagerartikel.update(artikel_id, daten)
            flash("Gespeichert.", "success")
            return redirect(url_for("lager.liste"))
        artikel = {**artikel, **daten}
    return render_template(
        "lager/edit.html", neu=False, artikel=artikel,
        lagerorte=lagerorte_liste(), lieferanten=lager_lieferanten_liste(),
    )


@bp.route("/<artikel_id>/loeschen", methods=["POST"])
@login_required
def loeschen(artikel_id: str):
    if not lagerartikel.get(artikel_id):
        abort(404)
    lagerartikel.delete(artikel_id)
    flash("Artikel gelöscht.", "success")
    return redirect(url_for("lager.liste"))


# ----- Schnellaktionen aus der Liste ------------------------------------------

@bp.route("/<artikel_id>/bestand", methods=["POST"])
@login_required
def bestand_aendern(artikel_id: str):
    """Bestand um +/- delta anpassen oder absolut setzen. Antwortet auf die
    Liste zurueck (mit erhaltener Suche/Filter)."""
    artikel = lagerartikel.get(artikel_id)
    if not artikel:
        abort(404)
    aktuell = artikel.get("bestand") or 0
    delta = request.form.get("delta")
    setzen = request.form.get("setzen")
    if delta is not None:
        neu_wert = aktuell + _als_int(delta, 0)
    elif setzen is not None:
        neu_wert = _als_int(setzen, aktuell)
    else:
        neu_wert = aktuell
    lagerartikel.update(artikel_id, {
        "bestand": max(0, neu_wert),
        "bestand_stand": date.today().isoformat(),
    })
    return redirect(request.referrer or url_for("lager.liste"))


@bp.route("/<artikel_id>/nachbestellen", methods=["POST"])
@login_required
def nachbestellen_toggle(artikel_id: str):
    artikel = lagerartikel.get(artikel_id)
    if not artikel:
        abort(404)
    lagerartikel.update(artikel_id, {"nachbestellen": not artikel.get("nachbestellen")})
    return redirect(request.referrer or url_for("lager.liste"))


# ----- Fehlende EANs (Barcode nachtragen) -------------------------------------

@bp.route("/ohne-ean")
@login_required
def ohne_ean():
    """Liste der Artikel ohne erfassten EAN/Barcode — am Regal einzuscannen.
    Nach Lagerort gruppiert; per Zeile direkt einscannbar."""
    artikel = lagerartikel_ohne_ean()
    gruppen: list[tuple[str, list]] = []
    idx: dict[str, int] = {}
    for a in artikel:
        ort = (a.get("lagerort") or "").strip() or "Ohne Lagerort"
        if ort not in idx:
            idx[ort] = len(gruppen)
            gruppen.append((ort, []))
        gruppen[idx[ort]][1].append(a)
    return render_template("lager/ohne_ean.html", gruppen=gruppen, anzahl=len(artikel))


@bp.route("/<artikel_id>/ean", methods=["POST"])
@login_required
def ean_setzen(artikel_id: str):
    """Setzt den gescannten EAN/Barcode eines Artikels (aus der 'ohne EAN'-Liste
    oder dem Bestellvorschlag). Scanner tippt Code + Enter -> speichert."""
    artikel = lagerartikel.get(artikel_id)
    if not artikel:
        abort(404)
    code = (request.form.get("barcode") or "").strip()
    if not code:
        flash("Kein Code eingescannt.", "warning")
    else:
        dublette = lagerartikel_by_barcode(code)
        if dublette and dublette["id"] != artikel_id:
            flash(f"Code «{code}» ist bereits bei «{dublette.get('bezeichnung')}» "
                  "hinterlegt — bitte prüfen.", "warning")
        else:
            lagerartikel.update(artikel_id, {"barcode": code})
            flash(f"EAN für «{artikel.get('bezeichnung')}» gespeichert.", "success")
    return redirect(request.referrer or url_for("lager.ohne_ean"))


# ----- Bestellliste (Druck) ---------------------------------------------------

@bp.route("/bestellliste")
@login_required
def bestellliste():
    """Druckansicht: alle nachzubestellenden Artikel, nach Lieferant gruppiert,
    mit clientseitig gerendertem Barcode (JsBarcode)."""
    artikel = lagerartikel_nachzubestellen()
    gruppen: list[tuple[str, list]] = []
    idx: dict[str, int] = {}
    for a in artikel:
        lief = (a.get("lieferant") or "").strip() or "Ohne Lieferant"
        if lief not in idx:
            idx[lief] = len(gruppen)
            gruppen.append((lief, []))
        gruppen[idx[lief]][1].append(a)
    return render_template("lager/bestellliste.html", gruppen=gruppen, anzahl=len(artikel))


@bp.route("/lagerliste")
@login_required
def lagerliste():
    """Druckansicht: kompletter Lager-Katalog (alle Artikel), nach Lagerort
    gruppiert, jeder Artikel mit gedrucktem Barcode zum Abscannen beim Bestellen.
    Das ist der Ersatz fuer die frueher vom Lieferanten gelieferte Lagerliste."""
    artikel = lagerartikel_sortiert()
    gruppen: list[tuple[str, list]] = []
    idx: dict[str, int] = {}
    for a in artikel:
        ort = (a.get("lagerort") or "").strip() or "Ohne Lagerort"
        if ort not in idx:
            idx[ort] = len(gruppen)
            gruppen.append((ort, []))
        gruppen[idx[ort]][1].append(a)
    return render_template("lager/lagerliste.html", gruppen=gruppen, anzahl=len(artikel))


# ----- Lieferschein-Import (Verbrauchs-Historie) ------------------------------

def _artikel_nach_bestellnr_map() -> dict:
    """{normalisierte Bestellnummer/Barcode -> Artikel} fuer die Zuordnung von
    Lieferschein-Positionen zu vorhandenen Lagerartikeln."""
    m: dict = {}
    for a in lagerartikel.list():
        for feld in ("bestellnummer", "barcode"):
            key = normalisiere_bestellnr(a.get(feld))
            if key:
                m.setdefault(key, a)
    return m


@bp.route("/lieferscheine")
@login_required
def lieferscheine():
    """Uebersicht der importierten Lieferscheine (Verbrauchs-Historie)."""
    return render_template(
        "lager/lieferscheine.html",
        lieferscheine=lagerlieferscheine_sortiert(),
        lieferanten=lager_lieferanten_liste(),
    )


@bp.route("/lieferschein/import", methods=["POST"])
@login_required
def lieferschein_import():
    """Lieferschein-PDF (Wuerth oder Elektro-Material) hochladen und parsen.
    Zeigt eine editierbare Vorschau; gespeichert wird erst nach Bestaetigung."""
    datei = request.files.get("lieferschein")
    lieferant_hint = (request.form.get("lieferant_hint") or "").strip()
    if not datei or not datei.filename:
        flash("Keine Datei gewählt.", "warning")
        return redirect(url_for("lager.lieferscheine"))
    if not datei.filename.lower().endswith(".pdf"):
        flash("Bitte ein PDF hochladen.", "warning")
        return redirect(url_for("lager.lieferscheine"))
    try:
        daten = io.BytesIO(datei.read())
        parsed = parse_lieferschein_fuers_lager(daten, lieferant_hint=lieferant_hint)
    except Exception as e:  # pragma: no cover — defensiv gegen kaputte PDFs
        flash(f"Lieferschein konnte nicht gelesen werden: {e}", "warning")
        return redirect(url_for("lager.lieferscheine"))

    positionen = parsed.get("positionen") or []
    if not positionen:
        flash("Keine Artikelpositionen erkannt. Ist es ein Würth- oder "
              "Elektro-Material-Lieferschein?", "warning")
        return redirect(url_for("lager.lieferscheine"))

    # Zuordnung zu bestehenden Lagerartikeln markieren (inkl. aktuellem Lagerort)
    artikel_map = _artikel_nach_bestellnr_map()
    for p in positionen:
        treffer = artikel_map.get(normalisiere_bestellnr(p.get("bestellnummer")))
        p["artikel_id"] = treffer["id"] if treffer else ""
        p["artikel_bezeichnung"] = treffer.get("bezeichnung") if treffer else ""
        p["artikel_lagerort"] = (treffer.get("lagerort") or "").strip() if treffer else ""

    nummer = parsed.get("nummer") or ""
    schon_importiert = bool(nummer) and any(
        (ls.get("nummer") or "") == nummer for ls in lagerlieferscheine.list())

    return render_template(
        "lager/lieferschein_import.html",
        lieferant=parsed.get("lieferant") or "",
        nummer=nummer,
        datum=parsed.get("datum") or "",
        positionen=positionen,
        lagerorte=lagerorte_liste(),
        schon_importiert=schon_importiert,
    )


def _ganzzahl_wenn_moeglich(wert: float):
    """1250.0 -> 1250 (int), 1.5 -> 1.5 (float). Haelt den Bestand sauber."""
    try:
        if float(wert).is_integer():
            return int(wert)
    except (TypeError, ValueError):
        pass
    return wert


@bp.route("/lieferschein/uebernehmen", methods=["POST"])
@login_required
def lieferschein_uebernehmen():
    """Verbucht die bestaetigten Positionen: Verbrauchs-Historie speichern und —
    wenn als Wareneingang gewuenscht — den Lagerbestand erhoehen (bzw. fehlende
    Artikel anlegen) sowie fehlende Lagerorte setzen."""
    lieferant = (request.form.get("lieferant") or "").strip()
    nummer = (request.form.get("nummer") or "").strip()
    datum = (request.form.get("datum") or "").strip() or None
    bestand_mitfuehren = request.form.get("bestand_mitfuehren") == "on"
    felder = {k: request.form.getlist(k + "[]") for k in (
        "bestellnummer", "bezeichnung", "menge", "einheit", "lagerort",
    )}
    uebernehmen = set(request.form.getlist("uebernehmen"))

    artikel_map = _artikel_nach_bestellnr_map()
    positionen = []
    eingang_gebucht = 0
    artikel_angelegt = 0
    lagerort_gesetzt = 0
    for i in range(len(felder["bestellnummer"])):
        if str(i) not in uebernehmen:
            continue
        bestellnummer = felder["bestellnummer"][i].strip()
        bezeichnung = felder["bezeichnung"][i].strip()
        menge = _als_float_oder_none(felder["menge"][i])
        einheit = felder["einheit"][i].strip()
        lagerort = (felder["lagerort"][i].strip()
                    if i < len(felder["lagerort"]) else "")
        positionen.append({
            "bestellnummer": bestellnummer,
            "bezeichnung": bezeichnung,
            "menge": menge,
            "einheit": einheit,
        })

        if not (bestand_mitfuehren and menge and menge > 0):
            continue
        # Stichtag des Bestands = Lieferdatum (sonst heute) -> ab da wird der
        # geschaetzte Verbrauch in der Projektion abgezogen.
        stand = datum or date.today().isoformat()
        key = normalisiere_bestellnr(bestellnummer)
        treffer = artikel_map.get(key) if key else None
        if treffer:
            neu_bestand = _ganzzahl_wenn_moeglich((treffer.get("bestand") or 0) + menge)
            updates = {"bestand": neu_bestand, "bestand_stand": stand}
            # Lagerort nur setzen, wenn noch keiner hinterlegt ist
            if lagerort and not (treffer.get("lagerort") or "").strip():
                updates["lagerort"] = lagerort
                lagerort_gesetzt += 1
            lagerartikel.update(treffer["id"], updates)
            treffer.update(updates)  # Map aktuell halten (gleiche Nr. mehrfach)
            eingang_gebucht += 1
        else:
            neu = lagerartikel.create({
                "bezeichnung": bezeichnung or bestellnummer or "Unbenannt",
                "barcode": "",
                "bestellnummer": bestellnummer,
                "lagerort": lagerort,
                "lieferant": lieferant,
                "einheit": einheit or "Stk",
                "bestand": _ganzzahl_wenn_moeglich(menge),
                "bestand_stand": stand,
                "mindestbestand": 0,
                "bestellmenge": None,
                "nachbestellen": False,
                "notizen": "",
            })
            if key:
                artikel_map[key] = neu
            artikel_angelegt += 1

    if not positionen:
        flash("Keine Position ausgewählt.", "warning")
        return redirect(url_for("lager.lieferscheine"))

    ls = lagerlieferschein_speichern(lieferant, nummer, datum, positionen)
    teile = [f"{len(ls.get('positionen', []))} Position(en) in die Verbrauchs-Historie"]
    if artikel_angelegt:
        teile.append(f"{artikel_angelegt} Artikel neu angelegt")
    if eingang_gebucht:
        teile.append(f"{eingang_gebucht}× Bestand erhöht")
    if lagerort_gesetzt:
        teile.append(f"{lagerort_gesetzt}× Lagerort gesetzt")
    flash(f"Lieferschein {nummer or '(ohne Nr.)'}: " + ", ".join(teile) + ".", "success")
    return redirect(url_for("lager.verbrauch"))


@bp.route("/lieferschein/<lieferschein_id>/loeschen", methods=["POST"])
@login_required
def lieferschein_loeschen(lieferschein_id: str):
    if not lagerlieferscheine.get(lieferschein_id):
        abort(404)
    lagerlieferscheine.delete(lieferschein_id)
    flash("Lieferschein gelöscht.", "success")
    return redirect(url_for("lager.lieferscheine"))


# ----- Verbrauchs-Uebersicht (Monatsverbrauch + Bestellvorschlag) -------------

@bp.route("/verbrauch")
@login_required
def verbrauch():
    """Durchschnittlicher Monatsverbrauch je Artikel (aus den importierten
    Lieferscheinen) und Bedarf fuer den gewuenschten Bestellrhythmus."""
    reichweite = _als_int(request.args.get("tage"), 14)
    if reichweite <= 0:
        reichweite = 14
    fenster = _als_int(request.args.get("fenster"), 365)
    daten = lagerverbrauch_uebersicht(reichweite_tage=reichweite, fenster_tage=fenster)
    return render_template(
        "lager/verbrauch.html",
        reichweite=reichweite,
        fenster=fenster,
        anzahl_lieferscheine=len(lagerlieferscheine.list()),
        anzahl_ohne_ean=len(lagerartikel_ohne_ean()),
        **daten,
    )


@bp.route("/verbrauch/uebernehmen", methods=["POST"])
@login_required
def verbrauch_uebernehmen():
    """Uebernimmt den errechneten Bedarf als Mindestbestand + Bestellmenge eines
    Artikels, damit der bestehende Nachbestell-Workflow (Bestellliste) greift.
    Mit artikel_id='__alle__' fuer alle uebergebenen Artikel auf einmal."""
    reichweite = _als_int(request.form.get("tage"), 14)
    ziel = request.form.get("artikel_id") or ""
    if ziel == "__alle__":
        paare = zip(request.form.getlist("alle_artikel_id[]"),
                    request.form.getlist("alle_bedarf[]"))
    else:
        paare = [(ziel, request.form.get("bedarf"))]
    anzahl = 0
    for artikel_id, bedarf_roh in paare:
        artikel_id = (artikel_id or "").strip()
        bedarf = _als_float_oder_none(bedarf_roh)
        if not artikel_id or not bedarf or bedarf <= 0:
            continue
        if not lagerartikel.get(artikel_id):
            continue
        menge = max(1, math.ceil(bedarf))
        lagerartikel.update(artikel_id, {"mindestbestand": menge, "bestellmenge": menge})
        anzahl += 1
    if anzahl:
        flash(f"Bedarf für {anzahl} Artikel als Mindestbestand & Bestellmenge "
              f"übernommen ({reichweite} Tage).", "success")
    else:
        flash("Nichts übernommen.", "warning")
    return redirect(url_for("lager.verbrauch", tage=reichweite))


@bp.route("/verbrauch/bestellen-markieren", methods=["POST"])
@login_required
def bestellen_markieren():
    """Setzt die Artikel aus dem Bestellvorschlag auf 'nachbestellen' (mit
    vorgeschlagener Bestellmenge) — sie erscheinen dann auf der druckbaren
    Bestellliste. artikel_id[] + menge[] parallele Listen."""
    ids = request.form.getlist("artikel_id[]")
    mengen = request.form.getlist("menge[]")
    anzahl = 0
    for i, artikel_id in enumerate(ids):
        artikel_id = (artikel_id or "").strip()
        if not artikel_id or not lagerartikel.get(artikel_id):
            continue
        menge = _als_float_oder_none(mengen[i]) if i < len(mengen) else None
        changes = {"nachbestellen": True}
        if menge and menge > 0:
            changes["bestellmenge"] = int(math.ceil(menge))
        lagerartikel.update(artikel_id, changes)
        anzahl += 1
    if anzahl:
        flash(f"{anzahl} Artikel für die Bestellliste markiert.", "success")
        return redirect(url_for("lager.bestellliste"))
    flash("Nichts markiert.", "warning")
    return redirect(url_for("lager.verbrauch"))
