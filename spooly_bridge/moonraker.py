"""
Moonraker REST-Poller.

Verbindet sich mit der lokalen Moonraker-Instanz und liest
Druckerinfos, Job-Historie und Spoolman-Daten aus.

Sicherheit:
- Verbindet sich nur zu der konfigurierten URL (Standard: localhost:7125)
- Keine Ausfuehrung von Befehlen ueber Moonraker
- Nur lesende Zugriffe (GET-Requests)
"""

import json
import logging
from typing import Optional, Dict, List, Any
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode, quote

log = logging.getLogger("spooly-bridge")

# Nur diese Job-Status werden an Spooly gemeldet
ABGESCHLOSSENE_STATUS = {"completed", "cancelled", "error", "klippy_shutdown"}

# Timeout fuer HTTP-Anfragen an Moonraker (Sekunden)
TIMEOUT = 15


class MoonrakerPoller:
    """Liest Daten von einer lokalen Moonraker-Instanz."""

    def __init__(self, basis_url: str = "http://localhost:7125"):
        self.basis_url = basis_url.rstrip("/")
        # IDs der bereits an Spooly gemeldeten Jobs (persistent im Speicher)
        self._gesendete_job_ids: set = set()
        # Cache fuer Features die nicht verfuegbar sind (z.B. Spoolman nicht installiert)
        self._nicht_verfuegbar: set = set()

    def _get(self, pfad: str, params: dict = None) -> Optional[Any]:
        """HTTP GET an Moonraker. Gibt das 'result'-Feld zurueck oder None bei Fehler."""
        url = f"{self.basis_url}{pfad}"
        if params:
            url += "?" + urlencode(params, quote_via=quote)

        anfrage = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(anfrage, timeout=TIMEOUT) as antwort:
                daten = json.loads(antwort.read())
                if isinstance(daten, dict) and "result" in daten:
                    return daten["result"]
                return daten
        except HTTPError as fehler:
            if fehler.code == 404:
                log.debug("Nicht verfuegbar: %s (404)", pfad)
            else:
                log.warning("Moonraker HTTP-Fehler %d: %s", fehler.code, pfad)
            return None
        except (URLError, OSError) as fehler:
            log.debug("Moonraker nicht erreichbar: %s", fehler)
            return None
        except (json.JSONDecodeError, ValueError) as fehler:
            log.warning("Moonraker ungueltige Antwort: %s", fehler)
            return None

    def drucker_info(self) -> Optional[dict]:
        """Druckerinfos abrufen (Name, Firmware, Status)."""
        return self._get("/printer/info")

    def job_historie(self, limit: int = 50) -> List[dict]:
        """Letzte Jobs aus der Moonraker-Historie."""
        ergebnis = self._get("/server/history/list", {"limit": limit, "order": "desc"})
        if not ergebnis or "jobs" not in ergebnis:
            return []
        return ergebnis["jobs"]

    def neue_jobs(self) -> List[dict]:
        """Nur noch nicht gemeldete, abgeschlossene Jobs zurueckgeben."""
        alle_jobs = self.job_historie()
        neue = []
        for job in alle_jobs:
            job_id = str(job.get("job_id", ""))
            status = job.get("status", "")
            if job_id in self._gesendete_job_ids:
                continue
            if status not in ABGESCHLOSSENE_STATUS:
                continue
            neue.append(job)
        return neue

    def job_als_gesendet_markieren(self, job_id: str):
        """Job-ID als gesendet markieren (wird nicht erneut uebertragen)."""
        self._gesendete_job_ids.add(str(job_id))
        # Speicher begrenzen
        if len(self._gesendete_job_ids) > 500:
            while len(self._gesendete_job_ids) > 400:
                self._gesendete_job_ids.pop()

    def datei_metadaten(self, dateiname: str) -> dict:
        """G-Code Metadaten einer Datei abrufen (Filament-Infos, Thumbnails)."""
        # Cache: Wenn Metadata-Endpoint nicht verfuegbar ist (z.B. Snapmaker),
        # nicht bei jedem Job erneut abfragen
        if "metadata" in self._nicht_verfuegbar:
            return {}
        ergebnis = self._get("/server/files/metadata", {"filename": dateiname})
        if ergebnis is None:
            self._nicht_verfuegbar.add("metadata")
            log.info("Metadaten-Endpoint nicht verfuegbar — wird fuer diesen Zyklus uebersprungen")
            return {}
        return ergebnis if isinstance(ergebnis, dict) else {}

    def spoolman_spool(self) -> Optional[Any]:
        """Aktuell aktiven Spoolman-Spool abrufen (falls installiert)."""
        # Cache: Wenn Spoolman nicht installiert ist, nicht bei jedem Job abfragen
        if "spoolman" in self._nicht_verfuegbar:
            return None
        ergebnis = self._get("/server/spoolman/spool_id")
        if ergebnis is None:
            self._nicht_verfuegbar.add("spoolman")
            log.info("Spoolman nicht installiert — wird fuer diese Sitzung uebersprungen")
            return None
        return ergebnis

    def thumbnail_laden(self, metadaten: dict) -> Optional[str]:
        """
        Groesstmoegliches Thumbnail aus den Metadaten als Base64 laden.
        Thumbnails liegen als Dateien unter .thumbs/ im gcodes-Root.
        """
        thumbnails = metadaten.get("thumbnails", [])
        if not thumbnails:
            return None

        # Groesstes Thumbnail waehlen
        groesstes = max(
            thumbnails,
            key=lambda t: (t.get("width", 0) or 0) * (t.get("height", 0) or 0),
            default=None,
        )
        if not groesstes:
            return None

        # Thumbnail hat entweder "data" (Base64 inline) oder "relative_path" (Datei)
        if groesstes.get("data"):
            return groesstes["data"]

        pfad = groesstes.get("relative_path")
        if not pfad:
            return None

        # Datei ueber Moonraker File-API laden
        url = f"{self.basis_url}/server/files/gcodes/{pfad}"
        try:
            import base64
            from urllib.request import urlopen, Request
            anfrage = Request(url, headers={"Accept": "image/png"})
            with urlopen(anfrage, timeout=TIMEOUT) as antwort:
                bild_bytes = antwort.read()
                b64 = base64.b64encode(bild_bytes).decode("ascii")
                return f"data:image/png;base64,{b64}"
        except Exception as fehler:
            log.debug("Thumbnail laden fehlgeschlagen: %s", fehler)
            return None

    def gesendete_jobs_zuruecksetzen(self):
        """Alle gesendeten Job-IDs vergessen — beim naechsten Zyklus werden alle Jobs erneut gepusht."""
        anzahl = len(self._gesendete_job_ids)
        self._gesendete_job_ids.clear()
        log.debug("%d gesendete Job-IDs zurueckgesetzt", anzahl)

    def zyklus_zuruecksetzen(self):
        """Cache fuer nicht-verfuegbare Endpoints zuruecksetzen (z.B. nach Neuinstallation)."""
        self._nicht_verfuegbar.clear()
