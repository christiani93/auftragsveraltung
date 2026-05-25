"""JSON-Storage für die Auftragsverwaltung.

Jede Entität wird in einer eigenen JSON-Datei im Datenordner abgelegt.
Schreibvorgänge laufen atomar (temp + os.replace), damit OneDrive nie
eine halb-geschriebene Datei sieht und auch bei Stromausfall nichts
korrupt zurückbleibt.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import config


_LOCKS: Dict[str, threading.Lock] = {}


def _lock_for(name: str) -> threading.Lock:
    if name not in _LOCKS:
        _LOCKS[name] = threading.Lock()
    return _LOCKS[name]


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"Nicht serialisierbar: {type(value).__name__}")


class JsonStore:
    """Generischer Repository über einer JSON-Datei mit Liste von Records."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.path: Path = config.DATA_DIR / filename
        self._lock = _lock_for(filename)

    def _read_all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError(f"{self.filename}: Wurzel muss eine Liste sein")
        return data

    def _write_all(self, records: Iterable[Dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(list(records), f, ensure_ascii=False, indent=2, default=_json_default)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return self._read_all()

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        for record in self.list():
            if record.get("id") == record_id:
                return record
        return None

    def create(self, record: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            records = self._read_all()
            if "id" not in record or not record["id"]:
                record["id"] = uuid.uuid4().hex[:12]
            record.setdefault("erstellt_am", datetime.now().isoformat(timespec="seconds"))
            records.append(record)
            self._write_all(records)
        return record

    def update(self, record_id: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            records = self._read_all()
            for i, existing in enumerate(records):
                if existing.get("id") == record_id:
                    existing.update(changes)
                    existing["geaendert_am"] = datetime.now().isoformat(timespec="seconds")
                    records[i] = existing
                    self._write_all(records)
                    return existing
        return None

    def delete(self, record_id: str) -> bool:
        with self._lock:
            records = self._read_all()
            remaining = [r for r in records if r.get("id") != record_id]
            if len(remaining) == len(records):
                return False
            self._write_all(remaining)
            return True

    def filter(self, **criteria: Any) -> List[Dict[str, Any]]:
        return [r for r in self.list() if all(r.get(k) == v for k, v in criteria.items())]
