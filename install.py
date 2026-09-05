#!/usr/bin/env python3
"""Spooly Bridge - Ein-Befehl-Installation.

Laedt die Bridge-Dateien von GitHub und startet danach die normale
Installation. Gedacht fuer den Aufruf direkt auf dem Drucker:

    wget -q -O- https://raw.githubusercontent.com/Blanus-1/spooly-bridge/main/install.py | python3 - --key spooly_br_XXX

Kommt ohne externe Abhaengigkeiten aus (nur Python-Standardbibliothek).
"""

import argparse
import os
import ssl
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError, HTTPError

GITHUB_RAW_BASE = "https://raw.githubusercontent.com/Blanus-1/spooly-bridge/main/spooly_bridge"

# Muss mit BRIDGE_DATEIEN in spooly_bridge/updater.py uebereinstimmen
DATEIEN = ["__init__.py", "__main__.py", "config.py", "moonraker.py", "uploader.py", "updater.py", "websocket_listener.py"]

TIMEOUT = 15


def url_fuer(dateiname):
    return f"{GITHUB_RAW_BASE}/{dateiname}"


def _lade_url(url):
    try:
        ctx = ssl.create_default_context()
        with urlopen(url, timeout=TIMEOUT, context=ctx) as antwort:
            return antwort.read().decode("utf-8")
    except (URLError, HTTPError, OSError):
        return None


def argumente_parsen(argv):
    parser = argparse.ArgumentParser(description="Spooly Bridge Installation")
    parser.add_argument("--key", "-k", required=True, help="Spooly Bridge API-Key")
    parser.add_argument("--spooly-url", "-s", default=None, help="Spooly API URL")
    parser.add_argument("--moonraker-url", "-m", default=None, help="Moonraker URL")
    return parser.parse_args(argv)


def plattform_pruefen(moonraker_url, plattform=sys.platform):
    """Meldung, wenn der Befehl auf einem Mac/PC statt auf dem Drucker laeuft.

    Gibt None zurueck, wenn die Installation weitergehen kann. Die Pruefung
    laeuft VOR dem Download, damit niemand erst Dateien zieht und dann an
    derselben Stelle scheitert.
    """
    if plattform.startswith("win"):
        return (
            "Windows wird von der Bridge nicht unterstuetzt. Bitte per SSH auf den\n"
            "Drucker (Raspberry Pi, Snapmaker U1, ...) und den Befehl dort ausfuehren."
        )
    if plattform == "darwin" and not moonraker_url:
        return (
            "Du bist auf einem Mac, nicht auf dem Drucker - hier gibt es keinen Moonraker.\n"
            "Empfohlen: per SSH auf den Drucker und den Befehl dort ausfuehren.\n"
            "Alternativ laeuft die Bridge auf diesem Mac (ohne Autostart, nur solange er\n"
            "wach ist), wenn du die Adresse des Druckers angibst:\n"
            "  --moonraker-url http://<IP-des-Druckers>:7125"
        )
    return None


def install_kommando(python, args):
    """Baut den Aufruf der eigentlichen Installation zusammen."""
    kommando = [python, "-m", "spooly_bridge", "--install", "--key", args.key]
    if args.spooly_url:
        kommando += ["--spooly-url", args.spooly_url]
    if args.moonraker_url:
        kommando += ["--moonraker-url", args.moonraker_url]
    return kommando


def dateien_herunterladen(ziel, lader=_lade_url):
    """Alle Bridge-Dateien laden und schreiben.

    Alles-oder-nichts: erst wenn alle Downloads erfolgreich sind, wird
    geschrieben. Bei einem Fehler bleibt eine bestehende Installation
    unangetastet.
    """
    inhalte = {}
    for name in DATEIEN:
        inhalt = lader(url_fuer(name))
        if inhalt is None:
            print("  FEHLER: %s konnte nicht geladen werden" % name)
            return False
        inhalte[name] = inhalt

    os.makedirs(ziel, exist_ok=True)
    for name, inhalt in inhalte.items():
        with open(os.path.join(ziel, name), "w", encoding="utf-8") as f:
            f.write(inhalt)
    return True


def main():
    args = argumente_parsen(sys.argv[1:])
    home = Path.home()
    ziel = home / "spooly_bridge"

    meldung = plattform_pruefen(args.moonraker_url)
    if meldung:
        print()
        print(meldung)
        print()
        sys.exit(1)

    print()
    print("Spooly Bridge wird heruntergeladen...")
    if not dateien_herunterladen(str(ziel)):
        print()
        print("Download fehlgeschlagen. Bitte Internetverbindung pruefen")
        print("und erneut versuchen.")
        sys.exit(1)
    print("  --> %d Dateien nach %s geladen" % (len(DATEIEN), ziel))

    # Ab hier uebernimmt die normale Installation (Verbindungstest,
    # Autostart, erster Sync)
    ergebnis = subprocess.run(install_kommando(sys.executable, args), cwd=str(home))
    sys.exit(ergebnis.returncode)


if __name__ == "__main__":
    main()
