"""Benachrichtigungen: In-App-Liste (Glocke) + Web-Push-Subscription."""
from __future__ import annotations

from flask import Blueprint, abort, jsonify, redirect, request, render_template, url_for
from flask_login import current_user, login_required

from models.push import vapid_public_key
from models.repos import (
    benachrichtigungen,
    benachrichtigungen_fuer,
    push_subscription_entfernen,
    push_subscription_speichern,
)

bp = Blueprint("benachrichtigungen", __name__)


@bp.route("/")
@login_required
def liste():
    eintraege = benachrichtigungen_fuer(current_user.username)
    return render_template(
        "benachrichtigungen/liste.html",
        eintraege=eintraege,
        vapid_public_key=vapid_public_key(),
    )


@bp.route("/<b_id>/oeffnen")
@login_required
def oeffnen(b_id: str):
    b = benachrichtigungen.get(b_id)
    if not b or b.get("user") != current_user.username:
        abort(404)
    if not b.get("gelesen"):
        benachrichtigungen.update(b_id, {"gelesen": True})
    if b.get("auftrag_id"):
        return redirect(url_for("auftraege.detail", auftrag_id=b["auftrag_id"]))
    return redirect(url_for("benachrichtigungen.liste"))


@bp.route("/alle-gelesen", methods=["POST"])
@login_required
def alle_gelesen():
    for b in benachrichtigungen_fuer(current_user.username):
        if not b.get("gelesen"):
            benachrichtigungen.update(b["id"], {"gelesen": True})
    return redirect(url_for("benachrichtigungen.liste"))


@bp.route("/push/subscribe", methods=["POST"])
@login_required
def push_subscribe():
    sub = request.get_json(silent=True)
    if not sub or not sub.get("endpoint"):
        return jsonify({"ok": False, "error": "Ungültige Subscription"}), 400
    push_subscription_speichern(current_user.username, sub)
    return jsonify({"ok": True})


@bp.route("/push/abmelden", methods=["POST"])
@login_required
def push_abmelden():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint", "")
    if endpoint:
        push_subscription_entfernen(endpoint)
    return jsonify({"ok": True})
