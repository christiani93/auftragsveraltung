"""Lagermaterial: eigener Lagerbestand (unabhaengig von Auftraegen).

Artikel werden per Barcode/E-Nummer eingescannt, einem Lagerort zugeordnet und
koennen fuer eine Bestellliste (mit gedruckten Barcodes) markiert werden. Der
Scan-Eingang funktioniert mit einem USB-Handscanner (Tastatur-Emulation: Code +
Enter) und optional per Kamera (JS) auf Tablet/Handy.

Bearbeiten/Loeschen/Bestand aendern ist fuer alle Modul-Nutzer erlaubt.
"""
from __future__ import annotations

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from models.produktsuche import ean_lookup
from models.repos import (
    lager_lieferanten_liste,
    lagerartikel,
    lagerartikel_by_barcode,
    lagerartikel_nachzubestellen,
    lagerartikel_sortiert,
    lagerorte_liste,
)

bp = Blueprint("lager", __name__)


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

    vorbefuellt = {"einheit": "Stk", "barcode": (request.args.get("barcode") or "").strip()}
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
    lagerartikel.update(artikel_id, {"bestand": max(0, neu_wert)})
    return redirect(request.referrer or url_for("lager.liste"))


@bp.route("/<artikel_id>/nachbestellen", methods=["POST"])
@login_required
def nachbestellen_toggle(artikel_id: str):
    artikel = lagerartikel.get(artikel_id)
    if not artikel:
        abort(404)
    lagerartikel.update(artikel_id, {"nachbestellen": not artikel.get("nachbestellen")})
    return redirect(request.referrer or url_for("lager.liste"))


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
