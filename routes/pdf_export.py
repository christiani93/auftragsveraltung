"""PDF-Export via WeasyPrint. Rendert Jinja-Templates direkt nach PDF."""
from __future__ import annotations

from datetime import date
from io import BytesIO

from flask import Blueprint, abort, render_template, send_file

from models.repos import (
    MESSPUNKT_FELDER,
    anlagen,
    anlagen_fuer_kunde,
    auftraege,
    auftraege_fuer_kunde,
    kunden,
    messgeraete,
    messprotokolle,
    zeitbuchungen_fuer_auftrag,
    zeitsumme_h,
)

bp = Blueprint("pdf", __name__)


def _render_pdf(html: str, filename: str):
    """Rendert HTML via WeasyPrint zu PDF und liefert als Download."""
    from weasyprint import HTML
    buf = BytesIO()
    HTML(string=html, base_url=".").write_pdf(buf)
    buf.seek(0)
    return send_file(
        buf, mimetype="application/pdf",
        as_attachment=True, download_name=filename,
    )


@bp.route("/auftraege/kunde/<kunde_id>")
def auftraege_pro_kunde(kunde_id: str):
    """Alle Aufträge eines Kunden als PDF, mit Zeitbuchungen."""
    kunde = kunden.get(kunde_id)
    if not kunde:
        abort(404)
    kunde_auftraege = sorted(
        auftraege_fuer_kunde(kunde_id),
        key=lambda a: a.get("erteilungsdatum", ""),
        reverse=True,
    )
    teile_idx = {a["id"]: a for a in anlagen.list()}
    rows = []
    for a in kunde_auftraege:
        eintraege = zeitbuchungen_fuer_auftrag(a["id"])
        rows.append({
            "auftrag": a,
            "zeitbuchungen": eintraege,
            "zeitsumme": zeitsumme_h(a["id"]),
            "anzahl_teile": len(a.get("anlagenteil_ids") or []),
        })

    html = render_template(
        "pdf/auftraege_kunde.html",
        kunde=kunde,
        rows=rows,
        erstellt_am=date.today().isoformat(),
        gesamtstunden=sum(r["zeitsumme"] for r in rows),
    )
    safe_name = kunde["name"].replace(" ", "_").replace("/", "_")
    return _render_pdf(html, f"Auftragsliste_{safe_name}_{date.today().isoformat()}.pdf")


@bp.route("/messprotokoll/<protokoll_id>")
def messprotokoll_einzeln(protokoll_id: str):
    """Ein einzelnes Messprotokoll als PDF (SiNa-Vorbereitung)."""
    p = messprotokolle.get(protokoll_id)
    if not p:
        abort(404)
    anlage = anlagen.get(p.get("anlage_id"))
    kunde = kunden.get(anlage["kunde_id"]) if anlage else None
    geraet = messgeraete.get(p.get("messgeraet_id")) if p.get("messgeraet_id") else None
    auftrag = auftraege.get(p.get("auftrag_id")) if p.get("auftrag_id") else None

    html = render_template(
        "pdf/messprotokoll.html",
        protokoll=p, anlage=anlage, kunde=kunde, geraet=geraet, auftrag=auftrag,
        messpunkt_felder=MESSPUNKT_FELDER,
        erstellt_am=date.today().isoformat(),
    )
    return _render_pdf(html, f"Messprotokoll_{protokoll_id}_{p.get('datum', '')}.pdf")
