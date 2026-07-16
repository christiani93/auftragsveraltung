"""Auftragsverwaltung — Flask-App für Elektroinstallateur-Alltag.

Start lokal:
    python app.py

Start in Production (HostPoint o.ae.):
    gunicorn -w 4 -b 0.0.0.0:8000 "app:create_app()"
"""
from __future__ import annotations

import os
import secrets
import socket

from flask import Flask, g, redirect, send_from_directory, url_for
from flask_login import LoginManager

import config
from routes.auftraege import bp as auftraege_bp
from routes.auth import bp as auth_bp
from routes.benachrichtigungen import bp as benachrichtigungen_bp
from routes.customers import bp as customers_bp
from routes.installations import bp as installations_bp
from routes.kontrolle import bp as kontrolle_bp
from routes.leistungsschalter import bp as leistungsschalter_bp
from routes.messgeraete import bp as messgeraete_bp
from routes.pdf_export import bp as pdf_bp
from routes.protocols import bp as protocols_bp
from routes.revisionen import bp as revisionen_bp
from routes.zeit import bp as zeit_bp


def _load_or_create_secret_key() -> str:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Env-Var hat Vorrang in Production
    env_key = os.environ.get("AUFTRAGSVERWALTUNG_SECRET_KEY")
    if env_key:
        return env_key
    keyfile = config.DATA_DIR / ".secret_key"
    if keyfile.exists():
        return keyfile.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    keyfile.write_text(key, encoding="utf-8")
    return key


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(config.BUNDLE_DIR / "templates"),
        static_folder=str(config.BUNDLE_DIR / "static"),
    )
    app.config["FIRMA_NAME"] = config.FIRMA_NAME
    # Bilder-Uploads: bis zu 60 MB pro Request (mehrere Fotos auf einmal)
    app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024

    # crashguard: Crash-/Fehler-Erfassung (URL+Token via CRASHGUARD_URL/_TOKEN env;
    # ohne gesetzte Env nur lokales Schreiben, kein Versand).
    try:
        import sys as _cg_sys
        _cg_root = os.path.dirname(os.path.abspath(__file__))
        if _cg_root not in _cg_sys.path:
            _cg_sys.path.insert(0, _cg_root)
        import crashguard
        crashguard.install(project="Auftragsverwaltung", repo_dir=_cg_root)
        crashguard.init_flask(app)
        crashguard.install_feedback(app, project="Auftragsverwaltung")
    except Exception:
        pass
    app.secret_key = _load_or_create_secret_key()
    # Session-Cookie: secure nur im HTTPS-Production-Betrieb
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("AUFTRAGSVERWALTUNG_HTTPS_ONLY") == "1"
    app.config["REMEMBER_COOKIE_DURATION"] = 60 * 60 * 24 * 30  # 30 Tage

    # Flask-Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Bitte melde dich an."
    login_manager.login_message_category = "info"

    from models.users import find_user, ensure_initial_admin, migrate_legacy_roles

    @login_manager.user_loader
    def load_user(user_id):
        return find_user(user_id)

    # Idempotente Daten-Migrationen (laufen race-frei pro Worker, JsonStore.update ist atomar)
    migrate_legacy_roles()

    # Initial-Admin anlegen wenn noch keine User existieren
    initial_pw = ensure_initial_admin()
    if initial_pw:
        print()
        print("=" * 60)
        print("  Erster Login angelegt:")
        print(f"    Username : {os.environ.get('AUFTRAGSVERWALTUNG_ADMIN_USER', 'admin')}")
        print(f"    Passwort : {initial_pw}")
        print("  BITTE NACH ERSTEM LOGIN PASSWORT ÄNDERN!")
        print("=" * 60)
        print()

    app.register_blueprint(auth_bp, url_prefix="")
    app.register_blueprint(customers_bp, url_prefix="/kunden")
    app.register_blueprint(installations_bp, url_prefix="/anlagen")
    app.register_blueprint(protocols_bp, url_prefix="/messprotokolle")
    app.register_blueprint(kontrolle_bp, url_prefix="/kontrolle")
    app.register_blueprint(messgeraete_bp, url_prefix="/messgeraete")
    app.register_blueprint(leistungsschalter_bp, url_prefix="/leistungsschalter")
    app.register_blueprint(auftraege_bp, url_prefix="/auftraege")
    app.register_blueprint(zeit_bp, url_prefix="/zeit")
    app.register_blueprint(revisionen_bp, url_prefix="/revisionen")
    app.register_blueprint(pdf_bp, url_prefix="/pdf")
    app.register_blueprint(benachrichtigungen_bp, url_prefix="/benachrichtigungen")

    from models.repos import fi_erforderlich
    app.jinja_env.globals["fi_erforderlich"] = fi_erforderlich

    @app.template_filter("mitarbeiter_name")
    def _mitarbeiter_name(username):
        """Username -> Anzeigename. Cache pro Request via flask.g, damit users.json
        nicht pro Tabellen-Zeile neu gelesen wird. Fallback auf den uebergebenen Wert,
        damit Legacy-Freitext-Eintraege (z.B. 'Braunschweiler') nicht verschwinden."""
        if not username:
            return ""
        cache = getattr(g, "_mitarbeiter_name_cache", None)
        if cache is None:
            from models.users import list_users
            cache = {u.username: u.name for u in list_users()}
            g._mitarbeiter_name_cache = cache
        return cache.get(username, username)

    @app.template_filter("datetime_ch")
    def _datetime_ch(value):
        """ISO-Datum/-Datetime -> Schweizer Format. '2026-06-23T09:30' ->
        '23.06.2026 09:30', '2026-06-23' -> '23.06.2026'. Leer -> ''."""
        if not value:
            return ""
        s = str(value).strip()
        datum, _, zeit = s.partition("T")
        teile = datum.split("-")
        out = f"{teile[2]}.{teile[1]}.{teile[0]}" if len(teile) == 3 else datum
        if zeit:
            out += " " + zeit[:5]
        return out

    @app.before_request
    def require_login():
        from flask import flash, request
        from flask_login import current_user
        # Diese Pfade brauchen keinen Login
        public_endpoints = {"auth.login", "service_worker", "static"}
        if request.endpoint in public_endpoints:
            return None
        if request.endpoint and request.endpoint.startswith("static"):
            return None
        # crashguard /feedback (Bug/Wunsch-Meldung) ist bewusst public
        if request.endpoint and request.endpoint.startswith("_crashguard_feedback_"):
            return None
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.path))
        # Hat der Admin ein Ersatzpasswort vergeben, zwingen wir den User auf
        # die Profil-Seite, bis er es selbst aendert.
        if getattr(current_user, "passwort_aendern_pflicht", False):
            erlaubt = {"auth.profil", "auth.change_password_route", "auth.logout"}
            if request.endpoint not in erlaubt:
                return redirect(url_for("auth.profil"))
        # Modul-Zugriffssteuerung: gehoert der Blueprint zu einem Modul, das der
        # User nicht freigeschaltet hat -> zurueck aufs Dashboard.
        from models.modules import BLUEPRINT_MODULE
        bp_name = (request.endpoint or "").split(".")[0]
        modul = BLUEPRINT_MODULE.get(bp_name)
        if modul and not current_user.darf_modul(modul):
            flash("Kein Zugriff auf diesen Bereich.", "warning")
            return redirect(url_for("kontrolle.dashboard"))
        return None

    @app.route("/")
    def index():
        return redirect(url_for("kontrolle.dashboard"))

    @app.route("/hilfe")
    def hilfe():
        from flask import render_template as _rt
        return _rt("hilfe.html")

    @app.route("/sw.js")
    def service_worker():
        response = send_from_directory(app.static_folder, "sw.js")
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.context_processor
    def inject_globals():
        from flask_login import current_user
        daten = {"firma_name": config.FIRMA_NAME, "benachrichtigungen_ungelesen": 0, "vapid_public_key": ""}
        if getattr(current_user, "is_authenticated", False):
            from models.push import vapid_public_key
            from models.repos import anzahl_ungelesen
            try:
                daten["benachrichtigungen_ungelesen"] = anzahl_ungelesen(current_user.username)
                daten["vapid_public_key"] = vapid_public_key()
            except Exception:
                pass
        return daten

    return app


def _lan_ip() -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def _print_zugriffs_urls() -> None:
    lan = _lan_ip()
    print()
    print("=" * 60)
    print("  Auftragsverwaltung läuft. Zugriff über:")
    print(f"    Lokal :  http://localhost:{config.PORT}")
    if lan:
        print(f"    WLAN  :  http://{lan}:{config.PORT}")
    print(f"  Daten :  {config.DATA_DIR}")
    print(f"  Stop  :  Strg+C")
    print("=" * 60)
    print()


if __name__ == "__main__":
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _print_zugriffs_urls()
    app = create_app()
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG, use_reloader=False)
