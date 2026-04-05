"""
Spooly API-Uploader.

Sendet Druckjob-Daten und Heartbeats an die Spooly Cloud API.

Sicherheit:
- HTTPS wird fuer Produktiv-URLs erzwungen
- API-Key wird nie geloggt (nur maskiert)
- Keine sensiblen Daten in Fehlermeldungen
- Timeout auf allen Verbindungen
"""

import json
import logging
from typing import Optional, List, Dict, Any
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

log = logging.getLogger("spooly-bridge")

# Timeout fuer HTTPS-Anfragen an Spooly (Sekunden)
TIMEOUT = 30


class SpoolyUploader:
    """Sendet Daten an die Spooly Cloud API."""

    def __init__(self, spooly_url: str, api_key: str):
        self.basis_url = spooly_url.rstrip("/")
        self.api_key = api_key

        # HTTPS erzwingen fuer Produktiv-URLs (API-Key wird im Body gesendet!)
        if "spooly.eu" in self.basis_url and not self.basis_url.startswith("https://"):
            korrigiert = self.basis_url.replace("http://", "https://", 1)
            log.warning("HTTP erkannt — erzwinge HTTPS: %s", korrigiert)
            self.basis_url = korrigiert

        # Warnung bei komplett unbekannten URLs ohne HTTPS
        if not self.basis_url.startswith("https://") and "localhost" not in self.basis_url:
            log.warning(
                "Verbindung ohne HTTPS! Der API-Key wird unverschluesselt gesendet. "
                "Nur fuer lokale Tests verwenden."
            )

    def _post(self, pfad: str, daten: dict) -> Optional[dict]:
        """HTTP POST an Spooly API. Gibt die Antwort als dict zurueck."""
        url = f"{self.basis_url}{pfad}"
        body = json.dumps(daten).encode("utf-8")

        anfrage = Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(anfrage, timeout=TIMEOUT) as antwort:
                return json.loads(antwort.read())
        except HTTPError as fehler:
            status = fehler.code
            if status == 401:
                log.error("API-Key ungueltig oder abgelaufen! Bitte in Spooly neu generieren.")
            elif status == 429:
                log.warning("Spooly Rate-Limit erreicht — naechster Versuch im naechsten Zyklus")
            else:
                log.warning("Spooly API-Fehler %d bei %s", status, pfad)
            return None
        except (URLError, OSError) as fehler:
            log.warning("Spooly nicht erreichbar: %s", type(fehler).__name__)
            return None
        except (json.JSONDecodeError, ValueError):
            log.warning("Spooly ungueltige Antwort bei %s", pfad)
            return None

    def heartbeat(
        self,
        drucker_name: str = "Klipper",
        drucker_id: str = None,
        firmware: str = None,
    ) -> Optional[dict]:
        """
        Heartbeat an Spooly senden.
        Gibt die Antwort zurueck (enthaelt ggf. Update-Infos und Diagnose-Einstellungen).
        """
        from spooly_bridge import __version__
        ergebnis = self._post("/klipper/bridge/heartbeat", {
            "bridge_api_key": self.api_key,
            "printer_name": drucker_name,
            "printer_id": drucker_id,
            "firmware_version": firmware,
            "bridge_version": __version__,
        })
        return ergebnis

    def jobs_senden(
        self,
        jobs: List[dict],
        drucker_name: str = "Klipper",
        drucker_id: str = None,
        firmware: str = None,
    ) -> Optional[dict]:
        """
        Abgeschlossene Druckjobs an Spooly senden.

        Gibt das Ergebnis zurueck:
        {"success": True, "imported": N, "total_grams": X.X}
        """
        if not jobs:
            return {"success": True, "imported": 0}

        ergebnis = self._post("/klipper/push/jobs", {
            "bridge_api_key": self.api_key,
            "printer_name": drucker_name,
            "printer_id": drucker_id,
            "firmware_version": firmware,
            "jobs": jobs,
        })

        return ergebnis
