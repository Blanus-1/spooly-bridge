"""
Einstiegspunkt fuer die Spooly Bridge.

Nutzung:
    spooly-bridge --key DEIN_API_KEY
    spooly-bridge --install --key DEIN_API_KEY
    spooly-bridge --uninstall
"""

import argparse
import logging
import os
import signal
import subprocess
import shutil
import sys
import time
from pathlib import Path

from spooly_bridge import __version__
from spooly_bridge.config import BridgeConfig, lade_config, speichere_config
from spooly_bridge.moonraker import MoonrakerPoller
from spooly_bridge.uploader import SpoolyUploader


def _basis_verzeichnis(modul_pfad: str = None) -> str:
    """Verzeichnis, in dem die Bridge installiert ist (Eltern des Pakets).

    Beim klassischen Layout liegt das Paket unter ~/spooly_bridge - die Basis
    ist dann $HOME und alles verhaelt sich wie in frueheren Versionen. Der
    Spooly-Installer legt das Paket auf Druckern mit fluechtigem Home (Paxx
    setzt /root beim Boot zurueck) stattdessen z.B. unter
    /userdata/spooly_bridge/spooly_bridge ab - Config, Log, Watchdog und
    Autostart wandern dann automatisch mit auf die persistente Partition.

    pip-Installationen (site-packages) verhalten sich weiter wie bisher:
    dort gehoeren Config und Log ins Home, nicht neben den Paket-Code.
    """
    basis = Path(modul_pfad or __file__).resolve().parent.parent
    if basis.name in ("site-packages", "dist-packages"):
        return str(Path.home())
    return str(basis)


def main():
    parser = argparse.ArgumentParser(
        description="Spooly Bridge - Verbindet Klipper/Moonraker mit Spooly"
    )
    parser.add_argument("--key", "-k", help="Spooly Bridge API-Key")
    parser.add_argument("--moonraker-url", "-m", default=MOONRAKER_DEFAULT_URL, help="Moonraker URL")
    parser.add_argument("--spooly-url", "-s", default="https://api.spooly.eu/api", help="Spooly API URL")
    parser.add_argument("--intervall", "-i", type=int, default=300, help="Polling-Intervall in Sekunden (Standard: 300)")
    parser.add_argument("--config", "-c", type=str, default=None, help="Pfad zur Konfigurationsdatei")
    parser.add_argument("--version", "-v", action="version", version=f"spooly-bridge {__version__}")
    parser.add_argument("--debug", action="store_true", help="Ausfuehrliche Logausgabe")
    parser.add_argument("--install", action="store_true", help="Als Autostart-Service einrichten")
    parser.add_argument("--uninstall", action="store_true", help="Komplett deinstallieren")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    log_format = "%(asctime)s [%(levelname)s] %(message)s"
    log_datefmt = "%H:%M:%S"

    # Log-Rotation: max 500 KB, 1 Backup - verhindert Speicher-Overflow auf Druckern
    # mit begrenztem Speicher (z.B. Snapmaker Buildroot)
    log = logging.getLogger("spooly-bridge")
    log.setLevel(log_level)
    formatter = logging.Formatter(log_format, datefmt=log_datefmt)

    # Konsole (fuer systemd journald oder interaktive Nutzung)
    konsole = logging.StreamHandler()
    konsole.setFormatter(formatter)
    log.addHandler(konsole)

    # Datei mit Rotation (nur wenn nicht ueber systemd gestartet)
    log_pfad = os.path.join(_basis_verzeichnis(), "bridge.log")
    try:
        from logging.handlers import RotatingFileHandler
        datei_handler = RotatingFileHandler(
            log_pfad, maxBytes=512_000, backupCount=1, encoding="utf-8"
        )
        datei_handler.setFormatter(formatter)
        log.addHandler(datei_handler)
    except Exception:
        pass  # Falls Datei nicht schreibbar (z.B. read-only Dateisystem)

    if args.uninstall:
        _uninstall(log)
        return

    config_pfad = args.config or os.path.join(_basis_verzeichnis(), ".spooly-bridge.json")
    config = lade_config(config_pfad)

    if args.key:
        config.api_key = args.key
    if args.moonraker_url != MOONRAKER_DEFAULT_URL:
        config.moonraker_url = args.moonraker_url
    if args.spooly_url != "https://api.spooly.eu/api":
        config.spooly_url = args.spooly_url
    if args.intervall != 300:
        config.intervall = args.intervall

    if not config.api_key:
        log.error("Kein API-Key! Nutze --key DEIN_KEY oder trage ihn in %s ein.", config_pfad)
        sys.exit(1)

    speichere_config(config, config_pfad)

    if args.install:
        _install(config, log, config_pfad)
        return

    # Update-Check beim Start
    try:
        from spooly_bridge.updater import update_pruefen_und_ausfuehren
        ergebnis = update_pruefen_und_ausfuehren(erlaubt=True)
        if ergebnis.get("aktualisiert"):
            log.info("Bridge aktualisiert - starte neu...")
            os.execv(sys.executable, [sys.executable, '-m', 'spooly_bridge'] + sys.argv[1:])
    except Exception as fehler:
        log.debug("Update-Check uebersprungen: %s", fehler)

    # Autostart-Selbstheilung: repariert kaputte Alt-Installationen und
    # verlorene U1-Persistenz, solange die Bridge noch laeuft
    _autostart_selbstheilung(config_pfad, log)

    # Komponenten initialisieren
    poller = MoonrakerPoller(config.moonraker_url)
    uploader = SpoolyUploader(config.spooly_url, config.api_key)

    log.info("Spooly Bridge v%s gestartet", __version__)
    log.info("  Moonraker:  %s", config.moonraker_url)
    log.info("  Spooly:     %s", config.spooly_url)
    log.info("  API-Key:    %s...%s", config.api_key[:12], config.api_key[-4:])

    # Sofort Heartbeat senden damit Spooly weiss dass die Bridge laeuft
    _sende_heartbeat(poller, uploader, log)

    # Thumbnail-Verfuegbarkeit pruefen (lokal, einmalig beim Start)
    _pruefe_thumbnails(poller, log)

    # Graceful Shutdown
    laeuft = True

    def _stop(sig, frame):
        nonlocal laeuft
        log.info("Beende Bridge...")
        laeuft = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    # WebSocket oder Polling?
    ws_modus = _starte_websocket_modus(poller, uploader, config, log, lambda: laeuft, config_pfad)
    if not ws_modus:
        log.info("Fallback auf Polling-Modus (alle %d Sekunden)", config.intervall)
        _starte_polling_modus(poller, uploader, config, log, lambda: laeuft, config_pfad)

    log.info("Bridge beendet.")


# -- WebSocket-Modus (On-Demand, kein Polling) --------------

# Reconnect-Backoff: 5s -> 10s -> 20s -> 40s -> 80s -> 120s (gedeckelt)
RECONNECT_START_DELAY = 5
RECONNECT_MAX_DELAY = 120


def _naechster_backoff_delay(aktueller_delay: int, max_delay: int = RECONNECT_MAX_DELAY) -> int:
    """Verdoppelt den Wartezeit-Delay fuer den naechsten Reconnect-Versuch,
    gedeckelt auf max_delay Sekunden."""
    return min(aktueller_delay * 2, max_delay)


def _starte_websocket_modus(poller, uploader, config, log, laeuft_fn, config_pfad=None) -> bool:
    """Versucht WebSocket-Verbindung aufzubauen. Gibt False zurueck wenn nicht moeglich."""
    try:
        from spooly_bridge.websocket_listener import MoonrakerWebSocket
    except ImportError:
        return False

    ws = MoonrakerWebSocket(config.moonraker_url)
    if not ws.verbinden():
        log.info("WebSocket nicht verfuegbar")
        ws.trennen()
        return False

    log.info("WebSocket-Modus aktiv - Jobs werden sofort erkannt")

    letzter_heartbeat = 0
    # Heartbeat alle 4 Minuten - bewusst unter der 5-Minuten-Offline-Schwelle
    # des Backends, damit ein laufender Reconnect-Versuch den Heartbeat nicht
    # ueber die Schwelle drueckt und die Bridge faelschlich offline erscheint.
    heartbeat_intervall = 240

    # Reconnect-Zustand: reconnect_delay == 0 heisst "aktuell verbunden"
    reconnect_delay = 0
    naechster_reconnect = 0.0

    # Erster Sync: bestehende neue Jobs holen
    _sync_neue_jobs(poller, uploader, log)

    while laeuft_fn():
        jetzt = time.time()

        if ws.verbunden:
            # WebSocket Events lesen
            try:
                events = ws.events_lesen(timeout=1.0)
                for event in events:
                    if ws.ist_job_fertig_event(event):
                        log.info("Druckjob abgeschlossen - synchronisiere...")
                        time.sleep(2)  # Kurz warten bis Moonraker Historie aktualisiert hat
                        _sync_neue_jobs(poller, uploader, log)
            except Exception as fehler:
                log.debug("WebSocket Fehler: %s", fehler)

        # Verbindung verloren? Mit wachsendem Backoff neu verbinden, statt
        # dauerhaft auf den langsamen Polling-Modus zu degradieren. Der
        # Heartbeat unten laeuft waehrenddessen weiter, damit Spooly die
        # Bridge nicht faelschlich als offline anzeigt. Sobald Moonraker
        # wieder da ist, geht es automatisch zurueck in den Event-Modus.
        if not ws.verbunden:
            if reconnect_delay == 0:
                log.warning("WebSocket-Verbindung verloren - versuche Neuverbindung...")
                reconnect_delay = RECONNECT_START_DELAY
                naechster_reconnect = jetzt  # erster Versuch sofort

            if jetzt >= naechster_reconnect:
                if ws.verbinden():
                    log.info("WebSocket wieder verbunden")
                    reconnect_delay = 0
                    _sync_neue_jobs(poller, uploader, log)  # verpasste Jobs nachholen
                else:
                    log.warning(
                        "Neuverbindung fehlgeschlagen - naechster Versuch in %ds",
                        reconnect_delay,
                    )
                    naechster_reconnect = jetzt + reconnect_delay
                    reconnect_delay = _naechster_backoff_delay(reconnect_delay)
            else:
                time.sleep(1)  # nicht busy-warten bis zum naechsten Versuch

        # Periodischer Heartbeat (alle 5 Min, prueft auch force_reimport)
        if jetzt - letzter_heartbeat >= heartbeat_intervall:
            if _sende_heartbeat(poller, uploader, log):
                letzter_heartbeat = jetzt

            # Update-Check zusammen mit dem Heartbeat
            try:
                from spooly_bridge.updater import update_pruefen_und_ausfuehren
                ergebnis = update_pruefen_und_ausfuehren(erlaubt=True)
                if ergebnis.get("aktualisiert"):
                    log.info("Bridge aktualisiert - starte neu...")
                    ws.trennen()
                    os.execv(sys.executable, [sys.executable, '-m', 'spooly_bridge'] + sys.argv[1:])
            except Exception:
                pass

            # Autostart im gleichen Rhythmus intakt halten (Firmware-Updates
            # koennen /oem/.debug im laufenden Betrieb entfernen)
            if config_pfad:
                _autostart_selbstheilung(config_pfad, log)

    ws.trennen()
    return True


# -- Polling-Modus (Fallback) -----------------------

def _starte_polling_modus(poller, uploader, config, log, laeuft_fn, config_pfad=None):
    """Klassischer Polling-Modus als Fallback wenn WebSocket nicht verfuegbar."""

    while laeuft_fn():
        _sende_heartbeat(poller, uploader, log)
        _sync_neue_jobs(poller, uploader, log)

        # Update-Check bei jedem Zyklus (ein kleiner GET auf GitHub Releases)
        try:
            from spooly_bridge.updater import update_pruefen_und_ausfuehren
            ergebnis = update_pruefen_und_ausfuehren(erlaubt=True)
            if ergebnis.get("aktualisiert"):
                log.info("Bridge aktualisiert - starte neu...")
                os.execv(sys.executable, [sys.executable, '-m', 'spooly_bridge'] + sys.argv[1:])
        except Exception:
            pass

        # Autostart im gleichen Rhythmus intakt halten
        if config_pfad:
            _autostart_selbstheilung(config_pfad, log)

        # Warten (abbrechbar)
        for _ in range(config.intervall):
            if not laeuft_fn():
                break
            time.sleep(1)


# -- Gemeinsame Sync-Funktionen ---------------------

def _pruefe_thumbnails(poller, log):
    """Prueft beim Start ob Thumbnails geladen werden koennen (rein lokale Diagnose)."""
    jobs = poller.job_historie(limit=3)
    if not jobs:
        return

    for job in jobs[:3]:
        dateiname = job.get("filename", "?")
        # Inline-Metadaten pruefen (Snapmaker liefert die direkt im Job)
        meta = job.get("metadata", {})
        thumbs = meta.get("thumbnails", [])

        if not meta and not thumbs:
            # Separaten Metadata-Endpoint versuchen
            meta = poller.datei_metadaten(dateiname) if dateiname != "?" else {}
            thumbs = meta.get("thumbnails", [])

        if not thumbs:
            log.info("  Thumbnail-Check: %s - keine Thumbnails in Metadaten", dateiname)
            continue

        # Struktur loggen damit wir sehen was der Drucker liefert
        erstes = thumbs[0] if thumbs else {}
        felder = list(erstes.keys()) if isinstance(erstes, dict) else []
        log.info(
            "  Thumbnail-Check: %s - %d Thumbnail(s), Felder: %s",
            dateiname, len(thumbs), felder
        )

        # Versuch laden
        ergebnis = poller.thumbnail_laden(meta)
        if ergebnis:
            log.info("  Thumbnail-Check: Laden erfolgreich (%d KB)", len(ergebnis) // 1024)
        else:
            log.warning("  Thumbnail-Check: Laden fehlgeschlagen")
        break  # Ein Test reicht


def _sende_heartbeat(poller, uploader, log):
    """Heartbeat an Spooly senden. Gibt True zurueck wenn erfolgreich.

    Prueft auch ob Spooly einen Re-Import anfordert (force_reimport).
    Falls ja, wird der lokale Cache geleert und alle Jobs nochmal gesendet.
    """
    drucker_info = poller.drucker_info()

    # Heartbeat auch ohne Moonraker-Antwort senden - Spooly soll wissen
    # dass die Bridge laeuft, auch wenn Moonraker noch hochfaehrt. Ob und wo
    # Moonraker erreicht wurde geht mit, damit Spooly eine Bridge ohne
    # Drucker-Kontakt (falsche URL, falscher Rechner) auch so anzeigen kann.
    ergebnis = uploader.heartbeat(
        drucker_name=drucker_info.get("hostname", "Klipper") if drucker_info else "Klipper",
        firmware=drucker_info.get("software_version") if drucker_info else None,
        install_metadaten=_install_metadaten_ermitteln(),
        moonraker_url=poller.basis_url,
        moonraker_erreichbar=drucker_info is not None,
    )

    if ergebnis and ergebnis.get("force_reimport"):
        log.info("Re-Import von Spooly angefordert - lokalen Cache geleert")
        poller.gesendete_jobs_zuruecksetzen()
        # Sofort alle Jobs nochmal senden
        _sync_neue_jobs(poller, uploader, log)

    return ergebnis is not None


def _sync_neue_jobs(poller, uploader, log):
    """Neue Jobs an Spooly senden."""
    neue_jobs = poller.neue_jobs()
    if not neue_jobs:
        log.debug("Keine neuen Jobs")
        return

    log.info("%d neue(r) Job(s) gefunden", len(neue_jobs))

    # Spoolman einmal pro Zyklus abfragen (nicht pro Job)
    spoolman = poller.spoolman_spool()

    aufbereitete_jobs = []
    for job in neue_jobs:
        dateiname = job.get("filename", "")

        # Metadaten: zuerst aus dem Job-History-Eintrag lesen (Snapmaker liefert inline),
        # Fallback auf separaten Metadata-Endpoint (Standard-Moonraker)
        inline_meta = job.get("metadata", {})
        if inline_meta:
            metadaten = inline_meta
        else:
            metadaten = poller.datei_metadaten(dateiname) if dateiname else {}

        # Thumbnail: aus Metadaten laden (groesstes Bild als Base64)
        thumbnail_b64 = poller.thumbnail_laden(metadaten) if metadaten.get("thumbnails") else None

        aufbereitete_meta = _metadaten_aufbereiten(metadaten)
        if thumbnail_b64:
            aufbereitete_meta["thumbnails"] = [thumbnail_b64]

        # Rohe G-Code-Metadaten mitschicken (ohne Thumbnails/Bilder)
        # Dient zur Analyse welche Felder der Drucker tatsaechlich liefert
        raw_meta = {}
        for k, v in metadaten.items():
            if k == "thumbnails":
                raw_meta[k] = f"[{len(v)} thumbnails]" if isinstance(v, list) else str(v)[:100]
            elif isinstance(v, str) and len(v) > 500:
                raw_meta[k] = v[:500] + "...[truncated]"
            else:
                raw_meta[k] = v

        aufbereitete_jobs.append({
            "job_id": str(job.get("job_id", "")),
            "filename": dateiname,
            "status": job.get("status", "unknown"),
            "start_time": job.get("start_time"),
            "end_time": job.get("end_time"),
            "print_duration": job.get("print_duration"),
            "filament_used_mm": job.get("filament_used"),
            "metadata": aufbereitete_meta,
            "spoolman": _spoolman_aufbereiten(spoolman),
            "raw_gcode_metadata": raw_meta if raw_meta else None,
        })

    ergebnis = uploader.jobs_senden(
        jobs=aufbereitete_jobs,
        drucker_name=poller.drucker_info().get("hostname", "Klipper") if poller.drucker_info() else "Klipper",
    )

    if ergebnis and ergebnis.get("success"):
        imported = ergebnis.get("imported", 0)
        if imported > 0:
            log.info("%d neue(r) Job(s) an Spooly gesendet (%.1fg)", imported, ergebnis.get("total_grams", 0))
        else:
            log.debug("Alle Jobs bereits in Spooly")
        for job in aufbereitete_jobs:
            poller.job_als_gesendet_markieren(job["job_id"])
    elif ergebnis is None:
        log.warning("Spooly nicht erreichbar - naechster Versuch spaeter")
    else:
        log.warning("Spooly-Fehler: %s", ergebnis)


def _metadaten_aufbereiten(meta: dict) -> dict:
    if not meta:
        return {}
    thumbnails = []
    rohe_thumbnails = meta.get("thumbnails", [])
    if rohe_thumbnails:
        groesstes = max(
            rohe_thumbnails,
            key=lambda t: (t.get("width", 0) or 0) * (t.get("height", 0) or 0),
            default=None,
        )
        if groesstes and groesstes.get("data"):
            thumbnails.append(groesstes["data"])
    return {
        "filament_name": meta.get("filament_name"),
        "filament_type": meta.get("filament_type"),
        "filament_total_mm": meta.get("filament_total"),
        "filament_weight_total_g": meta.get("filament_weight_total"),
        "filament_colors": meta.get("filament_colors"),
        "nozzle_temps": meta.get("nozzle_temps") or meta.get("nozzle_temperature"),
        "bed_temp": meta.get("bed_temp") or meta.get("bed_temperature"),
        "layer_height": meta.get("layer_height"),
        "object_height": meta.get("object_height"),
        "thumbnails": thumbnails,
    }


def _spoolman_aufbereiten(spoolman_daten) -> dict:
    if not spoolman_daten:
        return {}
    if isinstance(spoolman_daten, dict):
        return {
            "spool_id": spoolman_daten.get("spool_id") or spoolman_daten.get("id"),
            "filament_id": spoolman_daten.get("filament_id"),
            "remaining_weight": spoolman_daten.get("remaining_weight"),
        }
    if isinstance(spoolman_daten, int):
        return {"spool_id": spoolman_daten}
    return {}


# -- Install / Uninstall ----------------------------

PIDFILE_PFAD = "/var/run/spoolybridge.pid"
AUTOSTART_SCRIPT_PFAD = "/etc/init.d/S99spoolybridge"
SYSTEMD_UNIT_PFAD = "/etc/systemd/system/spooly-bridge.service"
OEM_DEBUG_PFAD = "/oem/.debug"
OEM_VERZEICHNIS = "/oem"
PAXX_MARKER_PFAD = "/oem/paxx"
INITD_VERZEICHNIS = "/etc/init.d"
RCS_PFAD = "/etc/init.d/rcS"


def _install_metadaten_ermitteln() -> dict:
    """Install-Metadaten fuer den Heartbeat.

    Spooly leitet daraus ab, ob diese Installation einen Neustart ueberlebt,
    und zeigt Nutzern mit instabilem Pfad (z.B. /root auf Paxx-Firmware,
    die /root beim Boot zuruecksetzt) eine Migrations-Warnung in der UI.
    """
    if _ist_desktop_system():
        # Mac/PC: die Bridge richtet dort keinen Autostart ein
        os_family = "macos" if sys.platform == "darwin" else "windows"
        install_method = "none"
    elif _ist_systemd_system():
        os_family = "debian"
        install_method = "systemd" if os.path.exists(SYSTEMD_UNIT_PFAD) else "none"
    else:
        os_family = "buildroot"
        install_method = "initd" if os.path.exists(AUTOSTART_SCRIPT_PFAD) else "watchdog-only"
    return {
        # Kuerzen auf das Backend-Limit: ein ueberlanger Pfad darf nicht
        # den gesamten Heartbeat mit 422 scheitern lassen
        "install_path": _basis_verzeichnis()[:300],
        "install_method": install_method,
        "os_family": os_family,
        "is_paxx": os.path.exists(PAXX_MARKER_PFAD),
    }


def _u1_persistenz_aktivieren(oem_debug_pfad: str = OEM_DEBUG_PFAD) -> bool:
    """Aktiviert auf dem Snapmaker U1 die Persistenz von /etc.

    Der U1 setzt /etc bei jedem Neustart zurueck - und genau dort liegt der
    Autostart (/etc/init.d/S99spoolybridge). Die Firmware behaelt die
    Aenderungen nur, solange die Datei /oem/.debug existiert. Frueher musste
    der Nutzer sie vor der Installation von Hand anlegen (touch /oem/.debug);
    diese Funktion uebernimmt das jetzt automatisch.

    /oem ueberlebt den Neustart immer, deshalb genuegt es die Marker-Datei
    einmal anzulegen. Erst ein Firmware-Update entfernt sie wieder - dann
    setzt die naechste Installation sie erneut.

    Gibt True zurueck wenn die Datei existiert (neu angelegt oder schon da),
    False wenn sie nicht geschrieben werden konnte. Ob ueberhaupt ein U1
    vorliegt, prueft der Aufrufer ueber das Verzeichnis /oem.
    """
    if os.path.exists(oem_debug_pfad):
        return True
    try:
        Path(oem_debug_pfad).touch()
        return True
    except (PermissionError, OSError):
        return False


def _ist_systemd_system() -> bool:
    return os.path.exists("/usr/bin/systemctl") or os.path.exists("/bin/systemctl")


MOONRAKER_DEFAULT_URL = "http://localhost:7125"


def _ist_desktop_system(plattform: str = None) -> bool:
    """Mac oder Windows-PC statt Drucker.

    Dort gibt es weder systemd noch init.d, und die Bridge sucht Moonraker
    per Default unter localhost - auf einem Desktop laeuft da nichts.
    """
    plattform = plattform or sys.platform
    return plattform == "darwin" or plattform.startswith("win")


def _desktop_pruefung(moonraker_url: str, plattform: str = None):
    """Meldung (str) fuer Installationen auf Mac/Windows, None wenn nichts dagegen spricht.

    Die Bridge gehoert auf den Drucker. Auf einem Mac laeuft sie zur Not
    gegen einen Moonraker im Netz (--moonraker-url), aber ohne Autostart und
    nur solange der Rechner wach ist. Windows wird nicht unterstuetzt: kein
    /bin/sh, kein nohup, und der Neustart nach einem Update (os.execv)
    funktioniert dort nicht.
    """
    plattform = plattform or sys.platform
    if not _ist_desktop_system(plattform):
        return None
    if plattform.startswith("win"):
        return (
            "  Windows wird von der Bridge nicht unterstuetzt.\n"
            "  Bitte per SSH auf den Drucker (Raspberry Pi, Snapmaker U1, ...)\n"
            "  und den Installations-Befehl dort ausfuehren."
        )
    if moonraker_url.rstrip("/") == MOONRAKER_DEFAULT_URL:
        return (
            "  Du installierst auf einem Mac, nicht auf dem Drucker. Dort gibt es\n"
            "  keinen Moonraker unter %s.\n"
            "\n"
            "  Zwei Wege:\n"
            "  1) Empfohlen: per SSH auf den Drucker und dort denselben Befehl ausfuehren.\n"
            "  2) Bridge auf diesem Mac betreiben (ohne Autostart, nur solange der Mac\n"
            "     wach ist): denselben Befehl wiederholen und die Adresse des Druckers\n"
            "     angeben, z.B. --moonraker-url http://192.168.1.50:7125" % MOONRAKER_DEFAULT_URL
        )
    return None


def _datei_sicherstellen(pfad: str, soll_inhalt: str, modus: int = 0o755) -> bool:
    """Schreibt die Datei neu, wenn sie fehlt oder vom Soll-Inhalt abweicht.

    Gibt True zurueck wenn geschrieben wurde. Der Inhaltsvergleich sorgt
    dafuer, dass auch veraltete Fassungen (z.B. ein Watchdog-Script ohne
    HOME-Export aus v1.3.x) auf den aktuellen Stand kommen.
    """
    try:
        with open(pfad, "r") as f:
            if f.read() == soll_inhalt:
                return False
    except (FileNotFoundError, OSError):
        pass
    with open(pfad, "w") as f:
        f.write(soll_inhalt)
    os.chmod(pfad, modus)
    return True


def _autostart_selbstheilung(config_pfad: str, log) -> None:
    """Repariert den Autostart einer laufenden Bridge - ohne Neuinstallation.

    Laeuft beim Start und danach im Heartbeat-Rhythmus mit. Hintergrund:
    Installationen vor v1.4.0 haben einen kaputten rcS-Eintrag statt des
    init.d-Scripts, und auf dem Snapmaker U1 raeumen Firmware-Updates die
    Persistenz-Marker-Datei /oem/.debug wieder weg. Beides fiel bisher erst
    beim naechsten Neustart auf - dann war die Bridge tot und konnte sich
    nicht mehr selbst helfen. Solange sie noch laeuft, stellt diese Funktion
    deshalb alles wieder her, was der naechste Boot braucht.

    Auf systemd-Systemen (Raspberry Pi etc.) passiert bewusst nichts: dort
    kuemmert sich systemd um Neustarts und die Unit liegt nicht in /etc.
    Auf einem Mac/PC gibt es nichts zu reparieren, dort existiert kein Autostart.
    Alle Reparaturen sind idempotent; geloggt wird nur, wenn wirklich etwas
    repariert wurde.
    """
    if _ist_systemd_system() or _ist_desktop_system():
        return

    # Basis statt Path.home(): bei Installationen auf persistenter Partition
    # (z.B. /userdata) muessen Watchdog und Autostart dort erneuert werden,
    # nicht im fluechtigen Home
    basis = _basis_verzeichnis()
    if basis in ("", "/"):
        # Boot-Umgebung ohne brauchbare Basis - hier nichts anfassen,
        # sonst landen die Scripts im Wurzelverzeichnis
        return

    repariert = []
    try:
        # 1) U1-Persistenz: ohne /oem/.debug verwirft die Firmware /etc
        #    beim naechsten Neustart (Firmware-Updates loeschen die Datei)
        if os.path.isdir(OEM_VERZEICHNIS) and not os.path.exists(OEM_DEBUG_PFAD):
            if _u1_persistenz_aktivieren(OEM_DEBUG_PFAD):
                repariert.append("/oem/.debug neu angelegt")
            else:
                log.warning(
                    "Selbstheilung: /oem/.debug fehlt und laesst sich nicht anlegen - "
                    "der Autostart ueberlebt den naechsten Neustart nicht"
                )

        # 2) Watchdog-Script aktuell halten (alte Fassungen ohne HOME-Export
        #    scheiterten beim Boot)
        script_pfad = os.path.join(basis, "start-bridge.sh")
        if _datei_sicherstellen(script_pfad, _watchdog_script_inhalt(basis, sys.executable, config_pfad)):
            repariert.append("Watchdog-Script erneuert")

        # 3) init.d-Script fuer den Boot
        if os.path.isdir(INITD_VERZEICHNIS) and shutil.which("start-stop-daemon"):
            if _datei_sicherstellen(AUTOSTART_SCRIPT_PFAD, _autostart_script_inhalt(script_pfad)):
                repariert.append("init.d-Autostart erneuert")

        # 4) rcS-Altlasten frueherer Versionen entfernen (blockierten den Boot)
        if os.path.exists(RCS_PFAD):
            with open(RCS_PFAD, "r") as f:
                inhalt = f.read()
            bereinigt = _rcs_altlasten_entfernen(inhalt)
            if bereinigt != inhalt:
                with open(RCS_PFAD, "w") as f:
                    f.write(bereinigt)
                repariert.append("rcS-Altlast entfernt")
    except (PermissionError, OSError) as fehler:
        log.debug("Selbstheilung uebersprungen: %s", fehler)
        return

    if repariert:
        log.info("Autostart repariert: %s", ", ".join(repariert))


def _run(cmd):
    try:
        subprocess.run(cmd, shell=True, check=False, capture_output=True)
    except Exception:
        pass


def _pkill(muster):
    # Bewusst ohne Shell: bei shell=True wuerde die sh-c-Cmdline selbst
    # das Muster enthalten und pkill die eigene Parent-Shell treffen
    try:
        subprocess.run(["pkill", "-f", muster], check=False, capture_output=True)
    except Exception:
        pass


def _watchdog_script_inhalt(basis: str, python: str, config_pfad: str) -> str:
    """Watchdog-Script: haelt die Bridge am Laufen, auch nach Crash oder Update.

    HOME und --config sind explizit gesetzt, weil busybox init Boot-Prozesse
    mit HOME=/ und cwd=/ startet. Ohne das findet die Bridge beim Boot weder
    ihr Modul noch ihre Config und beendet sich sofort wieder. Als
    Arbeitsverzeichnis dient die Installations-Basis (dort liegt das Paket),
    beim klassischen Layout ist das weiterhin $HOME.
    """
    return (
        "#!/bin/sh\n"
        "# Spooly Bridge Watchdog: startet die Bridge neu wenn sie crasht\n"
        "# oder sich nach einem Auto-Update beendet hat.\n"
        f"export HOME={basis}\n"
        "while true; do\n"
        "  # cd in der Schleife: liegt die Basis auf einer eigenen Partition\n"
        "  # (z.B. /userdata), kann sie beim Boot noch fehlen - dann warten\n"
        f"  cd {basis} || {{ sleep 10; continue; }}\n"
        f"  {python} -m spooly_bridge --config {config_pfad}\n"
        "  sleep 10\n"
        "done\n"
    )


def _autostart_script_inhalt(script_pfad: str) -> str:
    """Init-Script fuer /etc/init.d/ auf Buildroot-Systemen (Snapmaker U1).

    Wird beim Boot von rcS mit "start" aufgerufen. Der Watchdog MUSS in den
    Hintergrund (-b), weil rcS alle Scripts synchron abarbeitet - sonst
    haengt der Boot in der Endlos-Schleife fest.
    """
    return (
        "#!/bin/sh\n"
        "# Spooly Bridge Autostart (von der Installation eingerichtet)\n"
        "\n"
        f"PIDFILE={PIDFILE_PFAD}\n"
        f"SCRIPT={script_pfad}\n"
        "\n"
        "case \"$1\" in\n"
        "    start)\n"
        "        # Nicht aufgeben wenn das Watchdog-Script (noch) fehlt: liegt\n"
        "        # es auf einer eigenen Partition (z.B. /userdata), kann die\n"
        "        # beim Boot spaeter mounten als rcS laeuft. Im Hintergrund\n"
        "        # bis zu 60s darauf warten - rcS blockiert das nicht.\n"
        "        start-stop-daemon -S -b -m -p \"$PIDFILE\" -x /bin/sh -- -c \"n=0; while [ ! -x $SCRIPT ] && [ \\$n -lt 30 ]; do sleep 2; n=\\$((n+1)); done; [ -x $SCRIPT ] && exec /bin/sh $SCRIPT\"\n"
        "        ;;\n"
        "    stop)\n"
        "        # Erst den Watchdog stoppen, sonst startet er die Bridge\n"
        "        # sofort wieder. Die pkill-Muster sind bewusst praezise:\n"
        "        # ein breites \"spooly_bridge\" wuerde auch laufende\n"
        "        # --install/--uninstall Aufrufe treffen.\n"
        "        [ -f \"$PIDFILE\" ] && start-stop-daemon -K -p \"$PIDFILE\" 2>/dev/null\n"
        "        rm -f \"$PIDFILE\"\n"
        "        pkill -f \"start-bridge.sh\" 2>/dev/null\n"
        "        pkill -f \"spooly_bridge --config\" 2>/dev/null\n"
        "        pkill -f \"spooly_bridge$\" 2>/dev/null\n"
        "        ;;\n"
        "    restart)\n"
        "        \"$0\" stop\n"
        "        sleep 1\n"
        "        \"$0\" start\n"
        "        ;;\n"
        "    *)\n"
        "        echo \"Usage: $0 {start|stop|restart}\"\n"
        "        ;;\n"
        "esac\n"
        "\n"
        "exit 0\n"
    )


def _systemd_service_inhalt(basis: str, python: str, config_pfad: str) -> str:
    """Systemd-Unit fuer Debian/Raspberry-Pi-Systeme.

    --config explizit, weil systemd-Units ohne HOME laufen und Path.home()
    dann auf den passwd-Eintrag von root zeigt statt auf den User der die
    Bridge installiert hat. WorkingDirectory ist die Installations-Basis,
    damit python -m das Paket findet.
    """
    return (
        "[Unit]\n"
        "Description=Spooly Bridge\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        f"WorkingDirectory={basis}\n"
        f"ExecStart={python} -m spooly_bridge --config {config_pfad}\n"
        "Restart=always\n"
        "RestartSec=10\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _rcs_altlasten_entfernen(inhalt: str) -> str:
    """Entfernt von frueheren Versionen angehaengte Start-Zeilen aus rcS.

    Aeltere Installationen haengten den Start-Aufruf direkt an
    /etc/init.d/rcS an. Seit v1.3.6 blockierte das den Boot, weil die
    Watchdog-Schleife nie returnt - der Autostart laeuft jetzt ueber ein
    eigenes init.d-Script.
    """
    zeilen = inhalt.splitlines(keepends=True)
    return "".join(z for z in zeilen if "start-bridge.sh" not in z)


def _install(config, log, config_pfad):
    """Autostart einrichten und Bridge starten.

    config_pfad kommt vom Aufrufer durchgereicht (main() hat die Config dort
    bereits gespeichert) - Watchdog und systemd-Unit muessen auf exakt
    dieselbe Datei zeigen, sonst laeuft die Bridge nach dem Boot mit einer
    anderen Config als bei der Installation.
    """
    basis = _basis_verzeichnis()
    python = sys.executable
    ist_desktop = _ist_desktop_system()

    print()
    print("=" * 50)
    print("  Spooly Bridge v%s - Installation" % __version__)
    print("=" * 50)
    print()

    # Mac/Windows statt Drucker: ohne Drucker-Adresse geht es nicht weiter
    meldung = _desktop_pruefung(config.moonraker_url)
    if meldung:
        print(meldung)
        print()
        print("  Installation abgebrochen.")
        print()
        sys.exit(1)
    if ist_desktop:
        print("  Hinweis: Du installierst auf einem Mac, nicht auf dem Drucker.")
        print("  Die Bridge fragt Moonraker unter %s ab, laeuft nur solange" % config.moonraker_url)
        print("  der Mac wach ist und startet nach einem Neustart nicht von selbst.")
        print("  Empfohlen ist die Installation direkt auf dem Drucker (per SSH).")
        print()

    # -- Schritt 1: Moonraker pruefen --------------------
    print("[1/4] Moonraker pruefen...")
    poller = MoonrakerPoller(config.moonraker_url)
    drucker_info = poller.drucker_info()
    if drucker_info:
        name = drucker_info.get("hostname", "Unbekannt")
        fw = drucker_info.get("software_version", "?")
        print("  --> Moonraker gefunden: %s (Klipper %s)" % (name, fw))
    else:
        print("  --> WARNUNG: Moonraker nicht erreichbar (%s)" % config.moonraker_url)
        print("      Die Bridge wird trotzdem installiert und verbindet sich spaeter.")
        print()

    # -- Schritt 2: Spooly-Verbindung testen -------------
    print("[2/4] Spooly-Verbindung testen...")
    uploader = SpoolyUploader(config.spooly_url, config.api_key)
    spooly_ok = False
    heartbeat = uploader.heartbeat(
        drucker_name=drucker_info.get("hostname", "Klipper") if drucker_info else "Klipper",
        firmware=drucker_info.get("software_version") if drucker_info else None,
        install_metadaten=_install_metadaten_ermitteln(),
        moonraker_url=poller.basis_url,
        moonraker_erreichbar=drucker_info is not None,
    )
    if heartbeat and heartbeat.get("success"):
        spooly_ok = True
        print("  --> Spooly verbunden! API-Key gueltig.")
    else:
        print()
        print("  --> FEHLER: Spooly nicht erreichbar oder API-Key ungueltig!")
        print()
        print("      Moegliche Ursachen:")
        print("      - API-Key falsch kopiert (muss mit spooly_br_ beginnen)")
        print("      - Keine Internetverbindung")
        print("      - Falsche Spooly-URL (nutze --spooly-url)")
        print()
        print("  Installation abgebrochen. Bitte behebe das Problem und versuche es erneut.")
        print()
        sys.exit(1)

    # -- Schritt 3: Autostart einrichten ---------------
    print("[3/4] Autostart einrichten...")
    has_systemd = _ist_systemd_system()

    if ist_desktop:
        # Kein Autostart auf dem Mac. Die Watchdog-Schleife laeuft im
        # Hintergrund weiter, wenn das Terminal zugeht, und startet die
        # Bridge nach Crash oder Update neu - bis zum naechsten Neustart.
        script_pfad = os.path.join(basis, "start-bridge.sh")
        with open(script_pfad, "w") as f:
            f.write(_watchdog_script_inhalt(basis, python, config_pfad))
        os.chmod(script_pfad, 0o755)
        subprocess.Popen(
            ["nohup", "/bin/sh", script_pfad],
            stdout=open(os.devnull, "w"), stderr=open(os.devnull, "w"),
            start_new_session=True,
        )
        print("  --> Kein Autostart auf dem Mac. Die Bridge laeuft jetzt im Hintergrund")
        print("      bis zum naechsten Neustart, danach von Hand starten:")
        print("      /bin/sh %s &" % script_pfad)
    elif has_systemd:
        try:
            with open(SYSTEMD_UNIT_PFAD, "w") as f:
                f.write(_systemd_service_inhalt(basis, python, config_pfad))
        except PermissionError:
            print("  --> FEHLER: Keine Berechtigung fuer /etc/systemd/system/")
            print("      Bitte die Installation mit sudo bzw. als root ausfuehren.")
            sys.exit(1)
        _run("systemctl daemon-reload")
        _run("systemctl enable spooly-bridge")
        _run("systemctl restart spooly-bridge")
        print("  --> Systemd-Service eingerichtet (startet automatisch)")
    else:
        # Snapmaker U1: /etc (und damit der gleich geschriebene Autostart)
        # ueberlebt einen Neustart nur, wenn /oem/.debug existiert. Das
        # zuerst erledigen, bevor irgendetwas nach /etc geschrieben wird.
        if os.path.isdir("/oem"):
            if _u1_persistenz_aktivieren():
                print("  --> Snapmaker U1 erkannt: Persistenz aktiviert (/oem/.debug)")
            else:
                print("  --> WARNUNG: Snapmaker U1 erkannt, aber /oem/.debug konnte")
                print("      nicht angelegt werden. Ohne diese Datei ist der Autostart")
                print("      nach dem naechsten Neustart weg. Bitte von Hand anlegen:")
                print("      touch /oem/.debug")

        # Watchdog-Schleife: startet die Bridge automatisch neu wenn sie crasht
        # oder nach einem Auto-Update (os.execv) den Prozess ersetzt hat.
        script_pfad = os.path.join(basis, "start-bridge.sh")
        with open(script_pfad, "w") as f:
            f.write(_watchdog_script_inhalt(basis, python, config_pfad))
        os.chmod(script_pfad, 0o755)

        # Altlast aufraeumen: fruehere Versionen haengten den Start-Aufruf
        # direkt an rcS an - das funktionierte beim Boot nicht (HOME=/)
        # und blockierte rcS in der Watchdog-Schleife
        rcs_pfad = RCS_PFAD
        if os.path.exists(rcs_pfad):
            try:
                with open(rcs_pfad, "r") as f:
                    inhalt = f.read()
                bereinigt = _rcs_altlasten_entfernen(inhalt)
                if bereinigt != inhalt:
                    with open(rcs_pfad, "w") as f:
                        f.write(bereinigt)
            except (PermissionError, OSError):
                pass

        autostart_ok = False
        if os.path.isdir("/etc/init.d") and shutil.which("start-stop-daemon"):
            try:
                with open(AUTOSTART_SCRIPT_PFAD, "w") as f:
                    f.write(_autostart_script_inhalt(script_pfad))
                os.chmod(AUTOSTART_SCRIPT_PFAD, 0o755)
                autostart_ok = True
            except (PermissionError, OSError):
                pass

        if autostart_ok:
            # Ueber denselben Weg starten wie beim Boot - so fallen
            # Probleme schon bei der Installation auf, nicht erst beim
            # naechsten Neustart des Druckers
            _run(f"{AUTOSTART_SCRIPT_PFAD} stop")
            _run(f"{AUTOSTART_SCRIPT_PFAD} start")
            print("  --> Autostart eingerichtet (%s)" % AUTOSTART_SCRIPT_PFAD)
        else:
            subprocess.Popen(
                ["nohup", "/bin/sh", script_pfad],
                stdout=open(os.devnull, "w"), stderr=open(os.devnull, "w"),
                start_new_session=True,
            )
            print("  --> WARNUNG: Autostart konnte nicht eingerichtet werden")
            print("      (kein systemd und kein beschreibbares /etc/init.d gefunden)")
            print("      Die Bridge laeuft jetzt, muss aber nach einem Neustart")
            print("      manuell gestartet werden: /bin/sh %s &" % script_pfad)

    # -- Schritt 4: Erster Sync --------------------
    print("[4/4] Erster Sync...")
    neue_jobs = poller.neue_jobs() if drucker_info else []
    if neue_jobs:
        print("  --> %d Druckjob(s) gefunden!" % len(neue_jobs))
    else:
        print("  --> Keine neuen Druckjobs (alles aktuell)")

    # -- Zusammenfassung -------------------------------
    print()
    print("=" * 50)
    print("  Installation abgeschlossen!")
    print("=" * 50)
    print()
    print("  Moonraker:   %s" % (("verbunden (%s)" % drucker_info.get("hostname", "")) if drucker_info else "nicht erreichbar"))
    print("  Spooly:      verbunden")
    print("  Autostart:   %s" % ("keiner (Mac) - laeuft bis zum naechsten Neustart" if ist_desktop else "eingerichtet"))
    print("  Druckjobs:   %d gefunden" % len(neue_jobs))
    print()
    if has_systemd:
        print("  Status:      systemctl status spooly-bridge")
        print("  Logs:        journalctl -u spooly-bridge -f")
    else:
        print("  Logs:        tail -f %s/bridge.log" % basis)
    print("  Entfernen:   python3 -m spooly_bridge --uninstall")
    print()


def _uninstall(log):
    basis = _basis_verzeichnis()
    home = str(Path.home())
    log.info("Deinstalliere Spooly Bridge...")
    if os.path.exists(SYSTEMD_UNIT_PFAD):
        _run("systemctl stop spooly-bridge")
        _run("systemctl disable spooly-bridge")
        os.remove(SYSTEMD_UNIT_PFAD)
        _run("systemctl daemon-reload")
        log.info("  Systemd-Service entfernt")

    # Laufende Prozesse beenden. Die Muster sind bewusst praezise: ein
    # breites "spooly_bridge" wuerde diesen Uninstall-Prozess selbst
    # treffen (die eigene Cmdline enthaelt das Muster).
    _pkill("start-bridge.sh")
    _pkill("spooly_bridge --config")
    _pkill("spooly_bridge$")

    if os.path.exists(AUTOSTART_SCRIPT_PFAD):
        try:
            os.remove(AUTOSTART_SCRIPT_PFAD)
            log.info("  Autostart-Script entfernt")
        except PermissionError:
            log.warning("  Konnte %s nicht entfernen", AUTOSTART_SCRIPT_PFAD)
    if os.path.exists(PIDFILE_PFAD):
        try:
            os.remove(PIDFILE_PFAD)
        except OSError:
            pass

    # Dateien an der Installations-Basis entfernen; Home zusaetzlich
    # abraeumen, falls dort noch Altlasten einer frueheren Installation
    # liegen (vor v1.5.0 lag alles in $HOME)
    verzeichnisse = [basis] if basis == home else [basis, home]
    for verzeichnis in verzeichnisse:
        for name in ["start-bridge.sh", ".spooly-bridge.json", "bridge.log"]:
            pfad = os.path.join(verzeichnis, name)
            if os.path.exists(pfad):
                os.remove(pfad)
                log.info("  %s entfernt", pfad)
    init_pfad = "/etc/init.d/rcS"
    if os.path.exists(init_pfad):
        try:
            with open(init_pfad, "r") as f:
                zeilen = f.readlines()
            neue = [z for z in zeilen if "spooly_bridge" not in z and "start-bridge" not in z]
            if len(neue) < len(zeilen):
                with open(init_pfad, "w") as f:
                    f.writelines(neue)
                log.info("  Autostart-Eintrag entfernt")
        except PermissionError:
            log.warning("  Konnte init.d nicht bearbeiten")
    for verzeichnis in verzeichnisse:
        modul = os.path.join(verzeichnis, "spooly_bridge")
        if os.path.isdir(modul):
            shutil.rmtree(modul)
            log.info("  Bridge-Dateien entfernt (%s)", modul)
    log.info("Spooly Bridge deinstalliert.")


if __name__ == "__main__":
    main()
