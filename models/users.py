"""User-Modell + Auth-Helpers."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .modules import MODULE, STANDARD_MODULE_KEYS
from .storage import JsonStore

users_store = JsonStore("users.json")

USER_ROLES = ("admin", "projektleiter", "monteur")

USER_ROLE_LABEL = {
    "admin": "Admin",
    "projektleiter": "Projektleiter",
    "monteur": "Monteur",
}


def _normalize_role(role: Optional[str]) -> str:
    """Akzeptiert sowohl neue als auch alte Rollen-Bezeichnungen."""
    if role in USER_ROLES:
        return role
    # Legacy: alte 'user'-Rolle wird wie 'monteur' behandelt
    if role == "user":
        return "monteur"
    return "monteur"


class User(UserMixin):
    """Flask-Login-User. id ist der Username (Strings reichen)."""

    def __init__(self, data: Dict[str, Any]):
        self._data = data

    @property
    def id(self) -> str:  # type: ignore[override]
        # Flask-Login-Identitaet = Username (im Session-Cookie). NICHT verwechseln
        # mit record_id (UUID), die JsonStore zum Adressieren von Records benoetigt.
        return self._data["username"]

    def get_id(self) -> str:  # Flask-Login API
        return self.id

    @property
    def record_id(self) -> str:
        """UUID-Schluessel des Records im JsonStore — fuer update/delete-Aufrufe."""
        return self._data["id"]

    @property
    def username(self) -> str:
        return self._data["username"]

    @property
    def name(self) -> str:
        return self._data.get("name") or self.username

    @property
    def role(self) -> str:
        return _normalize_role(self._data.get("role"))

    @property
    def role_label(self) -> str:
        return USER_ROLE_LABEL.get(self.role, self.role)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_projektleiter(self) -> bool:
        return self.role == "projektleiter"

    @property
    def is_monteur(self) -> bool:
        return self.role == "monteur"

    @property
    def sieht_alle_auftraege(self) -> bool:
        """Admin + Projektleiter sehen alles. Monteur nur eigene + unzugewiesene."""
        return self.role in ("admin", "projektleiter")

    @property
    def darf_auftrag_loeschen(self) -> bool:
        """Admin + Projektleiter duerfen Auftraege loeschen."""
        return self.role in ("admin", "projektleiter")

    @property
    def projektleiter(self) -> str:
        """Zugeordneter Projektleiter (Username). Nur fuer Monteure relevant."""
        return (self._data.get("projektleiter") or "").strip()

    @property
    def team_leiter(self):
        """Der Projektleiter, dessen Team dieser User angehoert — massgeblich fuer
        die Auftrags-Sichtbarkeit. Projektleiter fuehren ihr eigenes Team (self),
        Monteur -> zugeordneter PL, Admin -> None (sieht alles)."""
        if self.is_admin:
            return None
        if self.is_projektleiter:
            return self.username
        return self.projektleiter or None

    @property
    def module_zugriff(self) -> set:
        """Freigeschaltete Module. Ohne explizite Konfiguration -> Standardmodule
        (rueckwaertskompatibel; bestehende User behalten alle heutigen Funktionen)."""
        gespeichert = self._data.get("module")
        if isinstance(gespeichert, list):
            return set(gespeichert)
        return set(STANDARD_MODULE_KEYS)

    def darf_modul(self, key: str) -> bool:
        """Admin darf alles; sonst nur freigeschaltete Module."""
        if self.is_admin:
            return True
        return key in self.module_zugriff

    @property
    def ist_vermietung_verwalter(self) -> bool:
        """Verwalter darf im Vermietungs-Modul ausleihen/zurücknehmen und
        Maschinen/Mitarbeiter verwalten. Admin ist immer Verwalter."""
        if self.is_admin:
            return True
        return bool(self._data.get("vermietung_verwalter"))

    @property
    def passwort_aendern_pflicht(self) -> bool:
        """True wenn der User sein Passwort beim naechsten Login zwingend aendern muss
        (z.B. weil Admin ihm ein Ersatz-Passwort vergeben hat)."""
        return bool(self._data.get("passwort_aendern_pflicht"))

    def check_password(self, password: str) -> bool:
        h = self._data.get("password_hash")
        if not h:
            return False
        return check_password_hash(h, password)


def find_user(username: str) -> Optional[User]:
    if not username:
        return None
    for data in users_store.list():
        if data.get("username", "").lower() == username.lower():
            return User(data)
    return None


def create_user(username: str, password: str, name: str = "", role: str = "monteur",
                force_change_on_next_login: bool = False) -> User:
    if find_user(username):
        raise ValueError(f"User „{username}“ existiert bereits.")
    role = _normalize_role(role)
    if role not in USER_ROLES:
        raise ValueError(f"Ungültige Rolle: {role}")
    data = {
        "username": username,
        "name": name or username,
        "role": role,
        "password_hash": generate_password_hash(password),
        "passwort_aendern_pflicht": bool(force_change_on_next_login),
    }
    users_store.create(data)
    return User(data)


def set_password(username: str, new_password: str, force_change_on_next_login: bool = False) -> bool:
    """Setzt das Passwort. Wenn force_change_on_next_login=True (Admin-Reset),
    wird das Flag passwort_aendern_pflicht gesetzt — der User wird beim naechsten
    Login auf die Profil-Seite gezwungen, bis er es selbst aendert."""
    user = find_user(username)
    if not user:
        return False
    updates = {
        "password_hash": generate_password_hash(new_password),
        "passwort_aendern_pflicht": bool(force_change_on_next_login),
    }
    users_store.update(user.record_id, updates)
    return True


def set_role(username: str, new_role: str) -> bool:
    user = find_user(username)
    if not user:
        return False
    role = _normalize_role(new_role)
    if role not in USER_ROLES:
        raise ValueError(f"Ungültige Rolle: {new_role}")
    users_store.update(user.record_id, {"role": role})
    return True


def set_module(username: str, module_keys) -> bool:
    """Setzt die freigeschalteten Module eines Users (nur gültige Keys)."""
    user = find_user(username)
    if not user:
        return False
    gueltig = [k for k in (module_keys or []) if k in MODULE]
    users_store.update(user.record_id, {"module": gueltig})
    return True


def set_vermietung_verwalter(username: str, wert: bool) -> bool:
    user = find_user(username)
    if not user:
        return False
    users_store.update(user.record_id, {"vermietung_verwalter": bool(wert)})
    return True


def list_projektleiter() -> list:
    return [u for u in list_users() if u.role == "projektleiter"]


def set_projektleiter(username: str, pl_username: str) -> bool:
    """Ordnet einen Monteur einem Projektleiter zu (leer = keiner)."""
    user = find_user(username)
    if not user:
        return False
    users_store.update(user.record_id, {"projektleiter": (pl_username or "").strip()})
    return True


def list_monteure() -> list[User]:
    """Alle User die als Monteur arbeiten können — fuer Auftrag-Zuweisung."""
    return [u for u in list_users() if u.role in ("monteur", "projektleiter", "admin")]


def ensure_initial_admin() -> Optional[str]:
    """Falls keine User existieren: erstellt einen Admin aus Env-Vars oder mit Default.

    Liefert das initiale Passwort zurück (zum Anzeigen im Log) wenn neu erzeugt,
    sonst None.

    Race-sicher zwischen mehreren gunicorn-Workern: nutzt eine Marker-Datei,
    die mit O_CREAT|O_EXCL atomar belegt wird. Nur der Worker, der den Marker
    setzen konnte, darf den Bootstrap durchführen — die anderen sehen den
    Marker schon existieren und tun nichts. Ohne diesen Schutz haben in der
    Vergangenheit mehrere Worker parallel je einen Admin angelegt.
    """
    import config
    marker = config.DATA_DIR / ".admin_bootstrap.done"
    # Schnellpfad: schon User vorhanden → sicherstellen, dass Marker existiert, fertig.
    if users_store.list():
        if not marker.exists():
            marker.parent.mkdir(parents=True, exist_ok=True)
            try:
                marker.write_text("ok\n", encoding="utf-8")
            except OSError:
                pass
        return None
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # Anderer Worker hat schon (oder ist gerade dabei) — wir nicht.
        return None
    try:
        os.write(fd, b"ok\n")
    finally:
        os.close(fd)
    # Nach dem Claim nochmal prüfen, falls zwischenzeitlich jemand User angelegt hat.
    if users_store.list():
        return None
    username = os.environ.get("AUFTRAGSVERWALTUNG_ADMIN_USER", "admin")
    password = os.environ.get("AUFTRAGSVERWALTUNG_ADMIN_PASSWORD") or _random_password()
    create_user(username, password, name="Administrator", role="admin")
    return password


def _random_password(length: int = 16) -> str:
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


# Public-Wrapper damit Routes nicht auf den underscore-Namen zugreifen muessen.
def generate_initial_password(length: int = 12) -> str:
    return _random_password(length)


def list_users() -> list[User]:
    return [User(d) for d in users_store.list()]


def migrate_legacy_roles() -> int:
    """Migriert alte 'user'-Rolle auf 'monteur'. Liefert Anzahl geänderter Einträge.

    Race-sicher zwischen gunicorn-Workern via Marker-Datei (O_CREAT|O_EXCL).
    Nur ein Worker fuehrt die Migration durch; die anderen sehen den Marker
    und skippen sofort.
    """
    import config
    marker = config.DATA_DIR / ".roles_migrated.done"
    if marker.exists():
        return 0
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return 0
    try:
        os.write(fd, b"ok\n")
    finally:
        os.close(fd)
    count = 0
    for record in users_store.list():
        if record.get("role") == "user":
            users_store.update(record["id"], {"role": "monteur"})
            count += 1
    return count


def delete_user(username: str) -> bool:
    user = find_user(username)
    if not user:
        return False
    users_store.delete(user.record_id)
    return True
