"""
Auto-Updater fuer die Spooly Bridge.

Prueft ob eine neuere Version auf GitHub verfuegbar ist und
aktualisiert die lokalen Dateien automatisch.

Sicherheit:
- Ladt nur von der offiziellen GitHub-URL
- Prueft ob der Download erfolgreich war bevor Dateien ersetzt werden
- Kein Code wird ausgefuehrt — nur Dateien werden heruntergeladen
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional
from urllib.request import urlopen
from urllib.error import URLError, HTTPError
import ssl

log = logging.getLogger("spooly-bridge")

# Offizielle Download-URL (nur von hier werden Updates geladen)
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/Blanus-1/spooly-bridge/main/spooly_bridge"
VERSION_URL = f"{GITHUB_RAW_BASE}/__init__.py"

# Dateien die aktualisiert werden
BRIDGE_DATEIEN = ["__init__.py", "__main__.py", "config.py", "moonraker.py", "uploader.py", "updater.py", "websocket_listener.py"]

TIMEOUT = 15


def _ssl_context():
    """SSL-Context mit Zertifikatspruefung (niemals deaktivieren!)."""
    return ssl.create_default_context()


def _download(url: str) -> Optional[str]:
    """Datei von URL herunterladen. Gibt Inhalt als String zurueck oder None."""
    try:
        ctx = _ssl_context()
        with urlopen(url, timeout=TIMEOUT, context=ctx) as antwort:
            return antwort.read().decode("utf-8")
    except (URLError, HTTPError, OSError) as fehler:
        log.debug("Download fehlgeschlagen (%s): %s", url, fehler)
        return None


def aktuelle_version() -> str:
    """Lokale Version auslesen."""
    from spooly_bridge import __version__
    return __version__


def neueste_version_pruefen() -> Optional[str]:
    """Neueste Version von GitHub abfragen. Gibt Versionsnummer zurueck oder None."""
    inhalt = _download(VERSION_URL)
    if not inhalt:
        return None

    # Version aus __init__.py extrahieren: __version__ = "1.0.0"
    for zeile in inhalt.splitlines():
        if zeile.startswith("__version__"):
            try:
                return zeile.split('"')[1]
            except (IndexError, ValueError):
                pass
    return None


def versionen_vergleichen(lokal: str, remote: str) -> bool:
    """Gibt True zurueck wenn die Remote-Version neuer ist."""
    try:
        lokal_teile = [int(t) for t in lokal.split(".")]
        remote_teile = [int(t) for t in remote.split(".")]
        return remote_teile > lokal_teile
    except (ValueError, AttributeError):
        return False


def update_ausfuehren() -> bool:
    """
    Alle Bridge-Dateien von GitHub herunterladen und lokal ersetzen.

    Sicherheitskonzept:
    - Alle Dateien werden zuerst komplett heruntergeladen
    - Erst wenn ALLE Downloads erfolgreich sind, werden die lokalen Dateien ersetzt
    - Bei einem Fehler bleibt die alte Version erhalten
    """
    # Bridge-Verzeichnis finden
    bridge_pfad = Path(__file__).parent

    # Phase 1: Alle Dateien herunterladen (in den Speicher, noch nichts schreiben)
    neue_dateien = {}
    for dateiname in BRIDGE_DATEIEN:
        url = f"{GITHUB_RAW_BASE}/{dateiname}"
        inhalt = _download(url)
        if inhalt is None:
            log.warning("Update abgebrochen: %s konnte nicht geladen werden", dateiname)
            return False
        neue_dateien[dateiname] = inhalt

    # Phase 2: Nur wenn alle Downloads OK sind, Dateien ersetzen
    for dateiname, inhalt in neue_dateien.items():
        ziel = bridge_pfad / dateiname
        try:
            with open(ziel, "w", encoding="utf-8") as f:
                f.write(inhalt)
        except OSError as fehler:
            log.warning("Update: Konnte %s nicht schreiben: %s", dateiname, fehler)
            return False

    return True


def update_pruefen_und_ausfuehren(erlaubt: bool = True) -> dict:
    """
    Hauptfunktion: Prueft ob ein Update verfuegbar ist und fuehrt es aus.

    Args:
        erlaubt: Ob Auto-Update vom User erlaubt wurde (Spooly-Setting)

    Returns:
        {"update_verfuegbar": bool, "neue_version": str, "aktualisiert": bool}
    """
    lokal = aktuelle_version()
    remote = neueste_version_pruefen()

    if not remote:
        return {"update_verfuegbar": False, "neue_version": None, "aktualisiert": False}

    ist_neuer = versionen_vergleichen(lokal, remote)

    if not ist_neuer:
        return {"update_verfuegbar": False, "neue_version": remote, "aktualisiert": False}

    log.info("Update verfuegbar: v%s → v%s", lokal, remote)

    if not erlaubt:
        log.info("Auto-Update ist deaktiviert — Update muss manuell installiert werden")
        return {"update_verfuegbar": True, "neue_version": remote, "aktualisiert": False}

    # Update durchfuehren
    erfolg = update_ausfuehren()

    if erfolg:
        log.info("Bridge auf v%s aktualisiert — Neustart empfohlen", remote)
    else:
        log.warning("Update auf v%s fehlgeschlagen — alte Version bleibt aktiv", remote)

    return {"update_verfuegbar": True, "neue_version": remote, "aktualisiert": erfolg}
