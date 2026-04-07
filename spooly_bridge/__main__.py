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


def main():
    parser = argparse.ArgumentParser(
        description="Spooly Bridge — Verbindet Klipper/Moonraker mit Spooly"
    )
    parser.add_argument("--key", "-k", help="Spooly Bridge API-Key")
    parser.add_argument("--moonraker-url", "-m", default="http://localhost:7125", help="Moonraker URL")
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

    # Log-Rotation: max 500 KB, 1 Backup — verhindert Speicher-Overflow auf Druckern
    # mit begrenztem Speicher (z.B. Snapmaker Buildroot)
    log = logging.getLogger("spooly-bridge")
    log.setLevel(log_level)
    formatter = logging.Formatter(log_format, datefmt=log_datefmt)

    # Konsole (fuer systemd journald oder interaktive Nutzung)
    konsole = logging.StreamHandler()
    konsole.setFormatter(formatter)
    log.addHandler(konsole)

    # Datei mit Rotation (nur wenn nicht ueber systemd gestartet)
    log_pfad = os.path.join(str(Path.home()), "bridge.log")
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

    config_pfad = args.config or str(Path.home() / ".spooly-bridge.json")
    config = lade_config(config_pfad)

    if args.key:
        config.api_key = args.key
    if args.moonraker_url != "http://localhost:7125":
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
        _install(config, log)
        return

    # Update-Check beim Start
    try:
        from spooly_bridge.updater import update_pruefen_und_ausfuehren
        ergebnis = update_pruefen_und_ausfuehren(erlaubt=True)
        if ergebnis.get("aktualisiert"):
            log.info("Bridge aktualisiert — starte neu...")
            os.execv(sys.executable, [sys.executable, '-m', 'spooly_bridge'] + sys.argv[1:])
    except Exception as fehler:
        log.debug("Update-Check uebersprungen: %s", fehler)

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
    ws_modus = _starte_websocket_modus(poller, uploader, config, log, lambda: laeuft)
    if not ws_modus:
        log.info("Fallback auf Polling-Modus (alle %d Sekunden)", config.intervall)
        _starte_polling_modus(poller, uploader, config, log, lambda: laeuft)

    log.info("Bridge beendet.")


# ── WebSocket-Modus (On-Demand, kein Polling) ──────────────

def _starte_websocket_modus(poller, uploader, config, log, laeuft_fn) -> bool:
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

    log.info("WebSocket-Modus aktiv — Jobs werden sofort erkannt")

    letzter_heartbeat = 0
    heartbeat_intervall = 300     # Heartbeat alle 5 Minuten

    # Erster Sync: bestehende neue Jobs holen
    _sync_neue_jobs(poller, uploader, log)

    while laeuft_fn():
        jetzt = time.time()

        # WebSocket Events lesen
        try:
            events = ws.events_lesen(timeout=1.0)
            for event in events:
                if ws.ist_job_fertig_event(event):
                    log.info("Druckjob abgeschlossen — synchronisiere...")
                    time.sleep(2)  # Kurz warten bis Moonraker Historie aktualisiert hat
                    _sync_neue_jobs(poller, uploader, log)
        except Exception as fehler:
            log.debug("WebSocket Fehler: %s", fehler)

        # Verbindung verloren? Neu verbinden oder auf Polling wechseln
        if not ws.verbunden:
            log.warning("WebSocket-Verbindung verloren — versuche Neuverbindung...")
            time.sleep(5)
            if not ws.verbinden():
                log.warning("Neuverbindung fehlgeschlagen — wechsle auf Polling")
                ws.trennen()
                _starte_polling_modus(poller, uploader, config, log, laeuft_fn)
                return True  # Polling hat uebernommen

        # Periodischer Heartbeat (alle 5 Min, prueft auch force_reimport)
        if jetzt - letzter_heartbeat >= heartbeat_intervall:
            if _sende_heartbeat(poller, uploader, log):
                letzter_heartbeat = jetzt

            # Update-Check zusammen mit dem Heartbeat
            try:
                from spooly_bridge.updater import update_pruefen_und_ausfuehren
                ergebnis = update_pruefen_und_ausfuehren(erlaubt=True)
                if ergebnis.get("aktualisiert"):
                    log.info("Bridge aktualisiert — starte neu...")
                    ws.trennen()
                    os.execv(sys.executable, [sys.executable, '-m', 'spooly_bridge'] + sys.argv[1:])
            except Exception:
                pass

    ws.trennen()
    return True


# ── Polling-Modus (Fallback) ───────────────────────

def _starte_polling_modus(poller, uploader, config, log, laeuft_fn):
    """Klassischer Polling-Modus als Fallback wenn WebSocket nicht verfuegbar."""

    while laeuft_fn():
        _sende_heartbeat(poller, uploader, log)
        _sync_neue_jobs(poller, uploader, log)

        # Update-Check bei jedem Zyklus (ein kleiner GET auf GitHub Releases)
        try:
            from spooly_bridge.updater import update_pruefen_und_ausfuehren
            ergebnis = update_pruefen_und_ausfuehren(erlaubt=True)
            if ergebnis.get("aktualisiert"):
                log.info("Bridge aktualisiert — starte neu...")
                os.execv(sys.executable, [sys.executable, '-m', 'spooly_bridge'] + sys.argv[1:])
        except Exception:
            pass

        # Warten (abbrechbar)
        for _ in range(config.intervall):
            if not laeuft_fn():
                break
            time.sleep(1)


# ── Gemeinsame Sync-Funktionen ─────────────────────

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
            log.info("  Thumbnail-Check: %s — keine Thumbnails in Metadaten", dateiname)
            continue

        # Struktur loggen damit wir sehen was der Drucker liefert
        erstes = thumbs[0] if thumbs else {}
        felder = list(erstes.keys()) if isinstance(erstes, dict) else []
        log.info(
            "  Thumbnail-Check: %s — %d Thumbnail(s), Felder: %s",
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

    # Heartbeat auch ohne Moonraker-Antwort senden — Spooly soll wissen
    # dass die Bridge laeuft, auch wenn Moonraker noch hochfaehrt
    ergebnis = uploader.heartbeat(
        drucker_name=drucker_info.get("hostname", "Klipper") if drucker_info else "Klipper",
        firmware=drucker_info.get("software_version") if drucker_info else None,
    )

    if ergebnis and ergebnis.get("force_reimport"):
        log.info("Re-Import von Spooly angefordert — lokalen Cache geleert")
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
        log.warning("Spooly nicht erreichbar — naechster Versuch spaeter")
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


# ── Install / Uninstall ────────────────────────────

def _run(cmd):
    try:
        subprocess.run(cmd, shell=True, check=False, capture_output=True)
    except Exception:
        pass


def _install(config, log):
    home = str(Path.home())
    python = sys.executable

    print()
    print("=" * 50)
    print("  Spooly Bridge v%s — Installation" % __version__)
    print("=" * 50)
    print()

    # ── Schritt 1: Moonraker pruefen ────────────────────
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

    # ── Schritt 2: Spooly-Verbindung testen ─────────────
    print("[2/4] Spooly-Verbindung testen...")
    uploader = SpoolyUploader(config.spooly_url, config.api_key)
    spooly_ok = False
    heartbeat = uploader.heartbeat(
        drucker_name=drucker_info.get("hostname", "Klipper") if drucker_info else "Klipper",
        firmware=drucker_info.get("software_version") if drucker_info else None,
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
        return

    # ── Schritt 3: Autostart einrichten ───────────────
    print("[3/4] Autostart einrichten...")
    has_systemd = os.path.exists("/usr/bin/systemctl") or os.path.exists("/bin/systemctl")

    if has_systemd:
        service = (
            "[Unit]\nDescription=Spooly Bridge\nAfter=network-online.target\n"
            "Wants=network-online.target\n\n[Service]\n"
            f"WorkingDirectory={home}\nExecStart={python} -m spooly_bridge\n"
            "Restart=always\nRestartSec=10\n\n[Install]\nWantedBy=multi-user.target\n"
        )
        with open("/etc/systemd/system/spooly-bridge.service", "w") as f:
            f.write(service)
        _run("systemctl daemon-reload")
        _run("systemctl enable spooly-bridge")
        _run("systemctl start spooly-bridge")
        print("  --> Systemd-Service eingerichtet (startet automatisch)")
    else:
        # Log-Rotation uebernimmt die Bridge selbst — stdout nur fuer Startfehler
        start_script = f"#!/bin/sh\ncd {home} && nohup {python} -m spooly_bridge > /dev/null 2>&1 &\n"
        script_pfad = os.path.join(home, "start-bridge.sh")
        with open(script_pfad, "w") as f:
            f.write(start_script)
        os.chmod(script_pfad, 0o755)
        init_pfad = "/etc/init.d/rcS"
        if os.path.exists(init_pfad):
            with open(init_pfad, "r") as f:
                inhalt = f.read()
            if script_pfad not in inhalt:
                with open(init_pfad, "a") as f:
                    f.write(f"{script_pfad}\n")
        subprocess.Popen(["/bin/sh", script_pfad])
        print("  --> Start-Script eingerichtet (startet automatisch)")

    # ── Schritt 4: Erster Sync ────────────────────
    print("[4/4] Erster Sync...")
    neue_jobs = poller.neue_jobs() if drucker_info else []
    if neue_jobs:
        print("  --> %d Druckjob(s) gefunden!" % len(neue_jobs))
    else:
        print("  --> Keine neuen Druckjobs (alles aktuell)")

    # ── Zusammenfassung ───────────────────────────────
    print()
    print("=" * 50)
    print("  Installation abgeschlossen!")
    print("=" * 50)
    print()
    print("  Moonraker:   %s" % (("verbunden (%s)" % drucker_info.get("hostname", "")) if drucker_info else "nicht erreichbar"))
    print("  Spooly:      verbunden")
    print("  Autostart:   eingerichtet")
    print("  Druckjobs:   %d gefunden" % len(neue_jobs))
    print()
    if has_systemd:
        print("  Status:      systemctl status spooly-bridge")
        print("  Logs:        journalctl -u spooly-bridge -f")
    else:
        print("  Logs:        tail -f %s/bridge.log" % home)
    print("  Entfernen:   python3 -m spooly_bridge --uninstall")
    print()


def _uninstall(log):
    home = str(Path.home())
    log.info("Deinstalliere Spooly Bridge...")
    if os.path.exists("/etc/systemd/system/spooly-bridge.service"):
        _run("systemctl stop spooly-bridge")
        _run("systemctl disable spooly-bridge")
        os.remove("/etc/systemd/system/spooly-bridge.service")
        _run("systemctl daemon-reload")
        log.info("  Systemd-Service entfernt")
    _run("pkill -f spooly_bridge")
    for name in ["start-bridge.sh", ".spooly-bridge.json", "bridge.log"]:
        pfad = os.path.join(home, name)
        if os.path.exists(pfad):
            os.remove(pfad)
            log.info("  %s entfernt", name)
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
    modul = os.path.join(home, "spooly_bridge")
    if os.path.isdir(modul):
        shutil.rmtree(modul)
        log.info("  Bridge-Dateien entfernt")
    log.info("Spooly Bridge deinstalliert.")


if __name__ == "__main__":
    main()
