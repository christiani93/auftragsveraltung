"""Web-Push (VAPID) — Schluesselverwaltung + Versand via pywebpush.

VAPID-Schluessel werden einmalig erzeugt und im Daten-Verzeichnis persistiert
(vapid_private.pem). Der oeffentliche Schluessel (application server key) wird
dem Browser fuer die Subscription uebergeben.
"""
from __future__ import annotations

import base64
import json

import config

# Kontakt fuer VAPID-Claims (Pflicht, aber inhaltlich unkritisch)
VAPID_SUBJECT = "mailto:admin@z-b.tech"

_public_key_cache: str | None = None


def _pem_path():
    return config.DATA_DIR / "vapid_private.pem"


def _ensure_keys() -> str:
    """Stellt sicher, dass ein VAPID-Schluesselpaar existiert; liefert den
    oeffentlichen Schluessel (base64url, unkomprimierter EC-Punkt) fuer den Browser."""
    global _public_key_cache
    if _public_key_cache:
        return _public_key_cache
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    p = _pem_path()
    if p.exists():
        private_key = serialization.load_pem_private_key(p.read_bytes(), password=None)
    else:
        private_key = ec.generate_private_key(ec.SECP256R1())
        pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(pem)
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    _public_key_cache = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return _public_key_cache


def vapid_public_key() -> str:
    try:
        return _ensure_keys()
    except Exception:
        return ""


def send_push_to_user(username: str, title: str, body: str, url: str = "/") -> int:
    """Sendet eine Push-Nachricht an alle Subscriptions eines Users.
    Tote Subscriptions (404/410) werden entfernt. Liefert Anzahl Zustellungen.
    Fehler werden geschluckt (Push darf nie den Aufruf-Fluss brechen)."""
    try:
        from pywebpush import WebPushException, webpush
        from models.repos import push_subscription_entfernen, push_subscriptions_fuer
    except Exception:
        return 0
    try:
        _ensure_keys()
    except Exception:
        return 0
    payload = json.dumps({"title": title, "body": body, "url": url})
    zugestellt = 0
    for s in push_subscriptions_fuer(username):
        sub = s.get("subscription")
        if not sub:
            continue
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=str(_pem_path()),
                vapid_claims={"sub": VAPID_SUBJECT},
            )
            zugestellt += 1
        except WebPushException as e:
            resp = getattr(e, "response", None)
            if resp is not None and getattr(resp, "status_code", None) in (404, 410):
                push_subscription_entfernen(s.get("endpoint", ""))
        except Exception:
            pass
    return zugestellt
