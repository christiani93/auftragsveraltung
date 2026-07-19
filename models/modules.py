"""Modul-Registry für die Zugriffssteuerung.

Ein Modul bündelt einen oder mehrere Blueprints zu einem Funktionsbereich, den
ein Admin pro Benutzer freischalten kann. Blueprints, die in KEINEM Modul
stehen (z.B. Dashboard, Auth, Benachrichtigungen, PDF), sind immer erreichbar.
"""
from __future__ import annotations

MODULE = {
    "auftraege":  {"label": "Aufträge",         "blueprints": ("auftraege",)},
    "zeit":       {"label": "Zeiterfassung",    "blueprints": ("zeit",)},
    "kunden":     {"label": "Kunden & Anlagen", "blueprints": ("customers", "revisionen", "installations", "leistungsschalter")},
    "pruefung":   {"label": "Kontrolle (Messprotokolle & Messgeräte)", "blueprints": ("protocols", "messgeraete")},
    "vermietung": {"label": "Verleih",          "blueprints": ("vermietung",)},
}

# Diese Module sind default aktiv (bestehende User ohne explizite Konfiguration
# behalten so alle heutigen Funktionen). 'vermietung' ist bewusst NICHT dabei —
# es muss vom Admin freigeschaltet werden.
STANDARD_MODULE_KEYS = ("auftraege", "zeit", "kunden", "pruefung")

# Blueprint-Name -> Modul-Key
BLUEPRINT_MODULE = {bp: key for key, m in MODULE.items() for bp in m["blueprints"]}
