"""
Konfigurationsverwaltung fuer die Spooly Bridge.

Die Konfiguration wird in ~/.spooly-bridge.json gespeichert.
CLI-Argumente haben immer Vorrang vor der gespeicherten Konfiguration.
"""

import json
import logging
import os
import stat
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("spooly-bridge")


@dataclass
class BridgeConfig:
    """Konfiguration der Bridge."""
    api_key: Optional[str] = None
    moonraker_url: str = "http://localhost:7125"
    spooly_url: str = "https://api.spooly.eu/api"
    intervall: int = 60  # Sekunden zwischen Sync-Zyklen


def lade_config(pfad: str) -> BridgeConfig:
    """Konfiguration aus JSON-Datei laden. Gibt Standardwerte zurueck wenn nicht vorhanden."""
    try:
        with open(pfad, "r") as f:
            daten = json.load(f)
        return BridgeConfig(
            api_key=daten.get("api_key"),
            moonraker_url=daten.get("moonraker_url", "http://localhost:7125"),
            spooly_url=daten.get("spooly_url", "https://api.spooly.eu/api"),
            intervall=daten.get("intervall", 60),
        )
    except FileNotFoundError:
        return BridgeConfig()
    except (json.JSONDecodeError, PermissionError) as fehler:
        log.warning("Konfiguration konnte nicht geladen werden: %s", fehler)
        return BridgeConfig()


def speichere_config(config: BridgeConfig, pfad: str):
    """
    Konfiguration als JSON speichern.

    Sicherheit: Die Datei bekommt Berechtigungen 600 (nur Besitzer kann lesen/schreiben),
    da sie den API-Key enthaelt.
    """
    daten = asdict(config)

    try:
        with open(pfad, "w") as f:
            json.dump(daten, f, indent=2)

        # Dateiberechtigungen einschraenken (nur Besitzer)
        os.chmod(pfad, stat.S_IRUSR | stat.S_IWUSR)  # 600
        log.debug("Konfiguration gespeichert: %s (Berechtigungen: 600)", pfad)
    except OSError as fehler:
        log.warning("Konfiguration konnte nicht gespeichert werden: %s", fehler)
