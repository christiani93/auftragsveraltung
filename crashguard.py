"""crashguard — Drop-in Crash-/Fehler-Erfassung fuer Python/Flask-Projekte.

Ein einziges Modul, das in jedes Projekt kopiert wird. Aufruf:

    import crashguard
    crashguard.install(project="AgilitySoftware")   # Stufe 1 (Crash) + 3 (ERROR-Logs)
    crashguard.init_flask(app)                        # Stufe 2 (500er im Request)

Erfasst werden:
  - Stufe 1: unbehandelte Exceptions (sys.excepthook), Thread-Crashes
             (threading.excepthook) und native Abstuerze (faulthandler).
  - Stufe 2: 500er-Fehler in Flask-Requests (got_request_exception).
  - Stufe 3: alles, was via logging auf Level ERROR/CRITICAL geloggt wird.

Transport: Jeder Report wird IMMER zuerst lokal geschrieben (offline-sicher),
danach best-effort per HTTP an den Collector geschickt. Schlaegt das fehl
(offline), bleibt der Report in einer Retry-Queue und wird beim naechsten
erfolgreichen Versand nachgereicht. Der Versand laeuft in einem Hintergrund-
Thread — crashguard blockiert oder crasht die Host-App nie.

Konfiguration via install()-Argumenten ODER Umgebungsvariablen:
  CRASHGUARD_URL             Collector-Basis-URL, z.B. https://admin.z-b.tech
  CRASHGUARD_TOKEN           Bearer-Token fuer den Collector
  CRASHGUARD_URL_FALLBACK    optionaler Zweit-Collector (z.B. https://z-b.tech),
                             wird nur genutzt wenn der primaere Versand scheitert
  CRASHGUARD_TOKEN_FALLBACK  Token fuer den Fallback (Default: CRASHGUARD_TOKEN)
  CRASHGUARD_DIR             lokales Verzeichnis (Default: ~/.crashguard/<project>)
  CRASHGUARD_DISABLE         Killswitch: '1'/'true'/'yes'/'on' deaktiviert crashguard
                             komplett (keine Hooks, kein Schreiben) — fuer DEV.

Killswitch: Ist CRASHGUARD_DISABLE gesetzt (oder install(disable=True)), wird
install() zum vollstaendigen No-op — es werden keine Hooks installiert, nichts
geschrieben, nichts versendet. So stoert crashguard in der DEV-Umgebung weder
Debugger noch Tests.
"""

from __future__ import annotations

import os
import sys
import json
import time
import uuid
import socket
import logging
import threading
import traceback
import subprocess
from datetime import datetime, timezone

__all__ = ["install", "init_flask", "report_manual"]

_VERSION = "1.1.0"
_cfg: dict = {}
_installed = False
_disabled = False
_lock = threading.Lock()

_TRUTHY = {"1", "true", "yes", "on"}


def _is_truthy(val) -> bool:
    return str(val).strip().lower() in _TRUTHY if val is not None else False


# ── interne Helfer ───────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_commit(cwd: str | None) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd or os.getcwd(),
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except Exception:
        pass
    return None


def _local_dir() -> str:
    return _cfg["local_dir"]


def _queue_dir() -> str:
    return os.path.join(_local_dir(), "queue")


def _ensure_dirs() -> None:
    os.makedirs(_queue_dir(), exist_ok=True)


def _write_local(rep: dict) -> str | None:
    """Schreibt den Report als JSON in das lokale Verzeichnis. Gibt den Pfad
    zurueck. Faengt alle Fehler — crashguard darf nie selbst crashen."""
    try:
        _ensure_dirs()
        fname = f"{rep['ts'].replace(':', '').replace('.', '')}_{rep['id']}.json"
        path = os.path.join(_local_dir(), fname)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        return path
    except Exception:
        return None


def _ship_to(url: str, token: str | None, rep: dict, timeout: float) -> bool:
    import urllib.request
    endpoint = url.rstrip("/") + "/api/crash"
    body = json.dumps(rep, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _ship(rep: dict, timeout: float = 8) -> bool:
    """Schickt EINEN Report. Probiert primaeren Collector, bei Fehlschlag den
    Fallback (falls konfiguriert). True, sobald ein Ziel HTTP 2xx liefert."""
    targets = _cfg.get("targets") or []
    for t in targets:
        if t.get("url") and _ship_to(t["url"], t.get("token"), rep, timeout):
            return True
    return False


def _flush_queue(timeout: float = 8) -> None:
    """Versucht alle in der Queue liegenden Reports nachzureichen. Erfolgreich
    zugestellte werden aus der Queue entfernt; beim ersten Fehler wird
    abgebrochen (weiterhin offline -> Rest beim naechsten Mal)."""
    try:
        qdir = _queue_dir()
        if not os.path.isdir(qdir):
            return
        for name in sorted(os.listdir(qdir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(qdir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    rep = json.load(f)
            except Exception:
                continue
            if _ship(rep, timeout=timeout):
                try:
                    os.remove(path)
                except Exception:
                    pass
            else:
                break
    except Exception:
        pass


def _enqueue(rep: dict) -> None:
    try:
        _ensure_dirs()
        path = os.path.join(_queue_dir(), f"{rep['ts'].replace(':', '').replace('.', '')}_{rep['id']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)
    except Exception:
        pass


def _dispatch(rep: dict, sync: bool = False) -> None:
    """Durabel zustellen: erst Archiv-Kopie + Queue-Kopie schreiben (offline-
    sicher, ueberlebt einen sofortigen Prozess-Tod), dann zustellen.

    sync=True (fatale Crashes): synchron mit kurzem Timeout senden, damit der
    Report noch vor dem Prozess-Ende rausgeht — schlaegt es fehl, bleibt er in
    der Queue und wird beim naechsten Start nachgereicht.
    sync=False: im Hintergrund-Thread, blockiert die Host-App nicht."""
    _write_local(rep)   # dauerhaftes lokales Archiv
    _enqueue(rep)       # Pending-Kopie bis Zustellung bestaetigt

    if sync:
        _flush_queue(timeout=4)
    else:
        threading.Thread(target=_flush_queue, name="crashguard-ship", daemon=True).start()


def _build(kind: str, exc: BaseException | None = None,
           message: str | None = None, context: dict | None = None) -> dict:
    tb = None
    etype = None
    if exc is not None:
        etype = type(exc).__name__
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return {
        "id": uuid.uuid4().hex[:12],
        "ts": _now(),
        "project": _cfg.get("project", "unknown"),
        "kind": kind,                      # crash | thread | flask_500 | log_error | manual
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "git": _cfg.get("git"),
        "exc_type": etype,
        "message": message or (str(exc) if exc else None),
        "traceback": tb,
        "context": context or {},
        "crashguard_version": _VERSION,
    }


def _emit(kind: str, exc=None, message=None, context=None, sync=False) -> None:
    if _disabled:
        return
    try:
        _dispatch(_build(kind, exc=exc, message=message, context=context), sync=sync)
    except Exception:
        pass  # niemals die Host-App stoeren


# ── Hooks ──────────────────────────────────────────────────────────────────────

def _excepthook(exc_type, exc_value, exc_tb):
    # Normales Beenden (Ctrl-C, sys.exit) ist kein Crash.
    if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        _emit("crash", exc=exc_value, sync=True)
    _prev_excepthook(exc_type, exc_value, exc_tb)


def _threadhook(args):
    if not issubclass(args.exc_type, (KeyboardInterrupt, SystemExit)):
        _emit("thread", exc=args.exc_value, sync=True,
              context={"thread": getattr(args.thread, "name", None)})
    if _prev_threadhook:
        _prev_threadhook(args)


class _ErrorLogHandler(logging.Handler):
    """Faengt ERROR/CRITICAL-Logs als 'Verschlucker' (Stufe 3) ab."""
    def emit(self, record: logging.LogRecord) -> None:
        if record.name == "crashguard":
            return
        exc = record.exc_info[1] if record.exc_info else None
        _emit("log_error", exc=exc, message=record.getMessage(),
              context={"logger": record.name, "level": record.levelname})


_prev_excepthook = sys.excepthook
_prev_threadhook = None


# ── oeffentliche API ─────────────────────────────────────────────────────────

def install(project: str, collector_url: str | None = None,
            token: str | None = None, local_dir: str | None = None,
            repo_dir: str | None = None, capture_logs: bool = True,
            disable: bool = False) -> None:
    """Installiert die Crash-Hooks. Mehrfach-Aufruf ist unschaedlich.

    Killswitch: disable=True oder CRASHGUARD_DISABLE=1 macht install() zum
    vollstaendigen No-op (keine Hooks, kein Schreiben/Versand) — fuer DEV."""
    global _installed, _disabled, _prev_excepthook, _prev_threadhook

    if disable or _is_truthy(os.environ.get("CRASHGUARD_DISABLE")):
        _disabled = True
        return

    with _lock:
        _cfg["project"] = project
        primary_url = collector_url or os.environ.get("CRASHGUARD_URL") or None
        primary_tok = token or os.environ.get("CRASHGUARD_TOKEN") or None
        fb_url = os.environ.get("CRASHGUARD_URL_FALLBACK") or None
        fb_tok = os.environ.get("CRASHGUARD_TOKEN_FALLBACK") or primary_tok
        targets = []
        if primary_url:
            targets.append({"url": primary_url, "token": primary_tok})
        if fb_url:
            targets.append({"url": fb_url, "token": fb_tok})
        _cfg["targets"] = targets
        _cfg["local_dir"] = (
            local_dir or os.environ.get("CRASHGUARD_DIR")
            or os.path.join(os.path.expanduser("~"), ".crashguard", project)
        )
        _cfg["git"] = _git_commit(repo_dir)

        if _installed:
            return

        _ensure_dirs()

        _prev_excepthook = sys.excepthook
        sys.excepthook = _excepthook

        if hasattr(threading, "excepthook"):
            _prev_threadhook = threading.excepthook
            threading.excepthook = _threadhook

        try:
            import faulthandler
            if not faulthandler.is_enabled():
                faulthandler.enable()
        except Exception:
            pass

        if capture_logs:
            h = _ErrorLogHandler(level=logging.ERROR)
            logging.getLogger().addHandler(h)

        _installed = True

    # Beim Start liegengebliebene Reports nachreichen (Hintergrund).
    threading.Thread(target=_flush_queue, name="crashguard-flush", daemon=True).start()


def init_flask(app) -> None:
    """Registriert einen Flask-Errorhandler (Stufe 2: 500er im Request)."""
    try:
        from flask import got_request_exception, request

        def _on_exc(sender, exception, **extra):
            try:
                ctx = {"path": request.path, "method": request.method}
            except Exception:
                ctx = {}
            _emit("flask_500", exc=exception, context=ctx)

        got_request_exception.connect(_on_exc, app, weak=False)
    except Exception:
        pass


def report_manual(message: str, context: dict | None = None) -> None:
    """Manuell einen Report ausloesen (z.B. an einer kritischen Stelle)."""
    _emit("manual", message=message, context=context)
