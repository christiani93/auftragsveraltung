"""User-Modell + Auth-Helpers."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .storage import JsonStore

users_store = JsonStore("users.json")

USER_ROLES = ("admin", "user")


class User(UserMixin):
    """Flask-Login-User. id ist der Username (Strings reichen)."""

    def __init__(self, data: Dict[str, Any]):
        self._data = data

    @property
    def id(self) -> str:  # type: ignore[override]
        return self._data["username"]

    def get_id(self) -> str:  # Flask-Login API
        return self.id

    @property
    def username(self) -> str:
        return self._data["username"]

    @property
    def name(self) -> str:
        return self._data.get("name") or self.username

    @property
    def role(self) -> str:
        return self._data.get("role", "user")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

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


def create_user(username: str, password: str, name: str = "", role: str = "user") -> User:
    if find_user(username):
        raise ValueError(f"User „{username}“ existiert bereits.")
    if role not in USER_ROLES:
        raise ValueError(f"Ungültige Rolle: {role}")
    data = {
        "username": username,
        "name": name or username,
        "role": role,
        "password_hash": generate_password_hash(password),
    }
    users_store.create(data)
    return User(data)


def set_password(username: str, new_password: str) -> bool:
    user = find_user(username)
    if not user:
        return False
    users_store.update(user.id, {"password_hash": generate_password_hash(new_password)})
    return True


def ensure_initial_admin() -> Optional[str]:
    """Falls keine User existieren: erstellt einen Admin aus Env-Vars oder mit Default.

    Liefert das initiale Passwort zurück (zum Anzeigen im Log) wenn neu erzeugt,
    sonst None.
    """
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


def list_users() -> list[User]:
    return [User(d) for d in users_store.list()]


def delete_user(username: str) -> bool:
    user = find_user(username)
    if not user:
        return False
    users_store.delete(user.id)
    return True
