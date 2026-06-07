"""Login/Logout + User-Verwaltung (nur für Admins)."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from models.users import (
    USER_ROLE_LABEL,
    USER_ROLES,
    create_user,
    delete_user,
    find_user,
    list_users,
    set_password,
    set_role,
)

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("kontrolle.dashboard"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = find_user(username)
        if user and user.check_password(password):
            login_user(user, remember=True)
            next_url = request.args.get("next") or url_for("kontrolle.dashboard")
            return redirect(next_url)
        error = "Benutzername oder Passwort falsch."
    return render_template("auth/login.html", error=error)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Abgemeldet.", "info")
    return redirect(url_for("auth.login"))


# ----- User-Verwaltung (nur Admin) -----

def _require_admin():
    if not current_user.is_authenticated or not current_user.is_admin:
        abort(403)


@bp.route("/users")
@login_required
def user_list():
    _require_admin()
    return render_template(
        "auth/users.html",
        users=list_users(),
        roles=USER_ROLES,
        role_label=USER_ROLE_LABEL,
    )


@bp.route("/users/neu", methods=["POST"])
@login_required
def new_user_route():
    _require_admin()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    name = request.form.get("name", "").strip()
    role = request.form.get("role", "monteur")
    if not username or not password:
        flash("Username und Passwort erforderlich.", "warning")
        return redirect(url_for("auth.user_list"))
    try:
        create_user(username, password, name=name, role=role)
        flash(f"User „{username}“ angelegt.", "success")
    except ValueError as e:
        flash(str(e), "warning")
    return redirect(url_for("auth.user_list"))


@bp.route("/users/<username>/rolle", methods=["POST"])
@login_required
def change_role_route(username: str):
    _require_admin()
    if username == current_user.username:
        flash("Du kannst deine eigene Rolle nicht ändern (Lockout-Schutz).", "warning")
        return redirect(url_for("auth.user_list"))
    neue_rolle = request.form.get("role", "")
    try:
        if set_role(username, neue_rolle):
            flash(f"Rolle für „{username}“ ist jetzt: {USER_ROLE_LABEL.get(neue_rolle, neue_rolle)}.", "success")
        else:
            flash("User nicht gefunden.", "warning")
    except ValueError as e:
        flash(str(e), "warning")
    return redirect(url_for("auth.user_list"))


@bp.route("/users/<username>/passwort", methods=["POST"])
@login_required
def change_password_route(username: str):
    # Admin kann jeden, User nur sich selbst
    is_self_change = current_user.username == username
    if not current_user.is_admin and not is_self_change:
        abort(403)
    new_password = request.form.get("new_password", "")
    if len(new_password) < 6:
        flash("Passwort muss mindestens 6 Zeichen haben.", "warning")
    elif set_password(username, new_password):
        # Self-change loescht die Pflicht-Flag (User hat selbst gesetzt),
        # Admin-change auf fremde User aktiviert sie (Ersatzpasswort).
        flash(f"Passwort für „{username}“ geändert.", "success")
    else:
        flash("User nicht gefunden.", "warning")
    if is_self_change:
        return redirect(url_for("kontrolle.dashboard"))
    return redirect(url_for("auth.user_list"))


@bp.route("/users/<username>/ersatzpasswort", methods=["POST"])
@login_required
def reset_password_route(username: str):
    """Admin vergibt ein Ersatzpasswort an einen User. Der User wird beim naechsten
    Login zwingend auf die Profil-Seite umgeleitet, bis er es selbst aendert."""
    _require_admin()
    if current_user.username == username:
        flash("Setze für dich selbst kein Ersatzpasswort — nutze die normale Passwort-Aenderung.", "warning")
        return redirect(url_for("auth.user_list"))
    new_password = request.form.get("new_password", "")
    if len(new_password) < 6:
        flash("Ersatzpasswort muss mindestens 6 Zeichen haben.", "warning")
    elif set_password(username, new_password, force_change_on_next_login=True):
        flash(f"Ersatzpasswort für „{username}“ vergeben — der User muss es beim nächsten Login ändern.", "success")
    else:
        flash("User nicht gefunden.", "warning")
    return redirect(url_for("auth.user_list"))


@bp.route("/users/<username>/loeschen", methods=["POST"])
@login_required
def delete_user_route(username: str):
    _require_admin()
    if username == current_user.username:
        flash("Du kannst dich nicht selbst löschen.", "warning")
    elif delete_user(username):
        flash(f"User „{username}“ gelöscht.", "info")
    else:
        flash("User nicht gefunden.", "warning")
    return redirect(url_for("auth.user_list"))


@bp.route("/profil")
@login_required
def profil():
    return render_template("auth/profil.html")
