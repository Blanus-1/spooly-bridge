# Spooly Bridge

> **Early Beta:** Dieses Projekt befindet sich in aktiver Entwicklung. Feedback und Fehlerberichte sind willkommen!

Verbindet deinen **Klipper/Moonraker** 3D-Drucker automatisch mit Spooly.

Die Bridge läuft als kleines Script neben deiner Moonraker-Instanz und sendet abgeschlossene Druckjobs automatisch an dein Spooly-Konto, ganz ohne Port-Forwarding, Cloudflare Tunnel oder Aufwand.

## Funktionen

- **Echtzeit-Import:** Jobs werden sofort erkannt wenn ein Druck endet (WebSocket)
- Fallback auf 5-Minuten-Polling wenn WebSocket nicht verfügbar
- G-Code Metadaten (Filament-Typ, Gewicht, Thumbnail)
- Spoolman-Integration (wenn installiert)
- Automatische Updates (steuerbar in Spooly)
- Keine externen Abhängigkeiten (nur Python-Standardbibliothek)
- Läuft auf Raspberry Pi, Snapmaker U1 und anderen Klipper-Hosts, auch in Docker

## Voraussetzungen

- **Python 3.8+** auf dem Drucker (bei Raspberry Pi und Snapmaker ab Werk vorhanden)
- **SSH-Zugang** zum Drucker (beim Snapmaker U1 muss SSH erst am Display aktiviert werden, siehe [Snapmaker U1: SSH und Persistenz](#snapmaker-u1-ssh-und-persistenz))

Mehr nicht. Kein Git, kein Kopieren vom PC, die Bridge lädt sich selbst herunter.

**Wichtig:** Der Installations-Befehl gehört in die SSH-Sitzung auf dem Drucker, nicht ins Terminal deines Macs oder PCs. Führst du ihn trotzdem auf einem Mac aus, sagt dir die Bridge das und bricht ab. Wer sie bewusst auf dem Mac betreiben will, gibt die Adresse des Druckers mit (`--moonraker-url http://DRUCKER_IP:7125`); sie läuft dann ohne Autostart und nur, solange der Mac wach ist. Windows wird nicht unterstützt.

## Installation (Schritt für Schritt)

### Schritt 1: API-Key in Spooly generieren

1. Öffne die Spooly Einstellungen unter **dev.spooly.eu** (Beta-Testumgebung)
2. Scrolle zu **Klipper / Moonraker**
3. Klappe **"Spooly Bridge"** auf
4. Klicke **"API-Key generieren"**
5. Kopiere den Key (sieht aus wie `spooly_br_xxxxxxxxxxxx`)

### Schritt 2: Per SSH auf den Drucker und installieren

```bash
ssh BENUTZER@DRUCKER_IP
```

Ersetze:
- `BENUTZER` mit dem SSH-Benutzernamen (meistens `pi` oder `root`)
- `DRUCKER_IP` mit der IP-Adresse deines Druckers

Dann die Bridge herunterladen und einrichten. Der Snapmaker U1 (und andere Drucker mit busybox) haben kein `curl`, und ihr `wget` kann kein HTTPS — darum den Installer mit `python3` laden (das auf jedem Klipper-System ohnehin vorhanden ist):

```bash
python3 -c "import urllib.request; open('/tmp/install.py','wb').write(urllib.request.urlopen('https://raw.githubusercontent.com/Blanus-1/spooly-bridge/main/install.py').read())"
python3 /tmp/install.py --key DEIN_API_KEY --spooly-url https://dev.spooly.eu/api
```

Falls dein System `curl` oder ein HTTPS-fähiges `wget` hat, geht es auch in einer Zeile:

```bash
wget -q -O- https://raw.githubusercontent.com/Blanus-1/spooly-bridge/main/install.py | python3 - --key DEIN_API_KEY --spooly-url https://dev.spooly.eu/api
```

> **Wichtig:** Der Parameter `--spooly-url https://dev.spooly.eu/api` ist während der Beta-Phase nötig. Sobald die Integration offiziell veröffentlicht wird, entfällt dieser Parameter.

### Was du nach der Installation sehen solltest

```
==================================================
  Spooly Bridge v1.4.0 - Installation
==================================================

[1/4] Moonraker pruefen...
  --> Moonraker gefunden: voron24 (Klipper v0.12.0)

[2/4] Spooly-Verbindung testen...
  --> Spooly verbunden! API-Key gueltig.

[3/4] Autostart einrichten...
  --> Systemd-Service eingerichtet (startet automatisch)

[4/4] Erster Sync...
  --> 12 Druckjob(s) gefunden!

==================================================
  Installation abgeschlossen!
==================================================

  Moonraker:   verbunden (voron24)
  Spooly:      verbunden
  Autostart:   eingerichtet
  Druckjobs:   12 gefunden

  Logs:        tail -f /home/pi/bridge.log
  Entfernen:   python3 -m spooly_bridge --uninstall
```

Wenn alle vier Schritte mit `-->` angezeigt werden, ist die Bridge fertig eingerichtet.

### Alternative: Docker

```bash
docker run -d \
  --name spooly-bridge \
  --restart unless-stopped \
  --network host \
  -e SPOOLY_KEY=DEIN_API_KEY \
  -e SPOOLY_URL=https://dev.spooly.eu/api \
  blanus1/spooly-bridge
```

## Snapmaker U1: SSH und Persistenz

Der U1 ist ein Sonderfall: SSH ist ab Werk aus, und das System verwirft Änderungen beim Neustart, solange die Persistenz nicht aktiviert ist. Beides ist schnell erledigt.

### SSH aktivieren

1. Am Drucker-Display: **Settings > Maintenance > Root Access**
2. Die Bedingungen bis zum Ende lesen und akzeptieren, dann **Open** wählen
3. Vom PC aus verbinden:

```bash
ssh root@DRUCKER_IP
```

Das Standard-Passwort ist `snapmaker`. Details dazu beschreibt die [U1-Firmware-Doku](https://snapmakeru1-extended-firmware.pages.dev/ssh_access).

**Sicherheit:** SSH gibt vollen Zugriff auf den Drucker. Nur im eigenen, vertrauenswürdigen Netzwerk aktivieren, das Standard-Passwort nach dem ersten Login ändern (`passwd`) und Root Access wieder schließen, wenn du ihn nicht brauchst. Achtung: Ein geändertes Passwort überlebt den Neustart nur, wenn die Persistenz (nächster Abschnitt) aktiv ist.

### Persistenz (erledigt die Installation automatisch)

Der U1 setzt das Verzeichnis `/etc` bei jedem Neustart zurück, und genau dort liegt der Autostart der Bridge (`/etc/init.d/S99spoolybridge`). Damit er den Neustart übersteht, muss die Datei `/oem/.debug` existieren. Die Installation erkennt den U1 und legt diese Datei selbst an, du musst dich also um nichts kümmern.

Nur falls die Installation die Warnung `/oem/.debug konnte nicht angelegt werden` zeigt (etwa weil sie nicht als `root` läuft), die Datei einmal von Hand anlegen und die Installation danach wiederholen:

```bash
touch /oem/.debug
```

Hintergrund in der [Persistenz-Doku](https://snapmakeru1-extended-firmware.pages.dev/data_persistence).

### Nach jedem Firmware-Update neu installieren

Firmware-Updates des U1 entfernen alle persistierten Änderungen und löschen auch `/oem/.debug`. Nach einem Update deshalb einfach den Installationsbefehl aus Schritt 2 erneut ausführen, die Installation legt `/oem/.debug` dabei automatisch wieder an. Dein API-Key aus Spooly bleibt gültig und kann wiederverwendet werden.

## Fehlerbehebung

### "Moonraker nicht erreichbar"

- Prüfe ob Moonraker läuft: `curl http://localhost:7125/printer/info`
- Falls anderer Port: `--moonraker-url http://localhost:ANDERER_PORT`
- Falls anderer Rechner: `--moonraker-url http://DRUCKER_IP:7125`

### "Spooly nicht erreichbar oder API-Key ungültig"

- Prüfe deine Internetverbindung: `ping spooly.eu`
- Generiere einen neuen API-Key in Spooly (Einstellungen, Klipper, Bridge)
- Prüfe ob der Key richtig kopiert wurde (beginnt mit `spooly_br_`)

### "Download fehlgeschlagen" bei der Installation

- Prüfe ob der Drucker Internet hat: `ping github.com`
- Bei Firmen-Netzwerken: Firewall/Proxy kann raw.githubusercontent.com blockieren

### Bridge läuft nach Drucker-Neustart nicht mehr

- Installation einfach nochmal ausführen (Befehl aus Schritt 2), das repariert auch den Autostart
- Auf dem Snapmaker U1 prüfen: `ls /etc/init.d/S99spoolybridge` muss existieren
- Snapmaker U1: prüfen ob `/oem/.debug` existiert (`ls /oem/.debug`). Fehlt die Datei, verwirft der Drucker den Autostart bei jedem Neustart, siehe [Snapmaker U1: SSH und Persistenz](#snapmaker-u1-ssh-und-persistenz). Nach einem Firmware-Update ist sie immer weg.

### "Permission denied" beim SSH

- Prüfe Benutzername und Passwort
- Bei Raspberry Pi: Standard ist `pi` / `raspberry`
- Bei Snapmaker U1: Standard ist `root` / `snapmaker`, SSH muss vorher am Display aktiviert werden (siehe [Snapmaker U1: SSH und Persistenz](#snapmaker-u1-ssh-und-persistenz))

### Bridge läuft aber keine Jobs in Spooly

- Prüfe die Logs: `tail -20 ~/bridge.log`
- Starte die Bridge manuell mit Debug-Modus:
  ```bash
  python3 -m spooly_bridge --key DEIN_KEY --debug
  ```

## Parameter

| Parameter | Standard | Beschreibung |
|-----------|----------|-------------|
| `--key` / `-k` | (keiner) | Spooly API-Key (Pflicht) |
| `--moonraker-url` / `-m` | `http://localhost:7125` | Moonraker URL |
| `--spooly-url` / `-s` | `https://api.spooly.eu/api` | Spooly API |
| `--intervall` / `-i` | `300` | Polling-Intervall in Sekunden (Fallback) |
| `--install` | (keiner) | Installieren mit Verbindungstest + Autostart |
| `--uninstall` | (keiner) | Komplett deinstallieren |
| `--debug` | (keiner) | Ausführliche Logausgabe |

## Deinstallation

```bash
ssh BENUTZER@DRUCKER_IP
python3 -m spooly_bridge --uninstall
```

Entfernt alles: Service, Konfiguration, Logs, Autostart-Einträge.

## Unterstützte Drucker

| Drucker | Klipper | Moonraker | Getestet |
|---------|---------|-----------|----------|
| Snapmaker U1 | Ab Werk | Ab Werk | Ja |
| Voron (alle) | Selbst installiert | Selbst installiert | (offen) |
| Ender 3 + Klipper | Selbst geflasht | Selbst installiert | (offen) |
| Prusa MK3 + Klipper | Selbst geflasht | Selbst installiert | (offen) |
| QIDI (X-Plus 3, etc.) | Ab Werk | Ab Werk | (offen) |

## Wie es funktioniert

```
Moonraker (localhost:7125)          Spooly Cloud (spooly.eu)
    |                                       |
    v                                       |
Spooly Bridge                               |
    |                                       |
    +-- WebSocket: Job-Events ---------->   |
    |   (sofortige Erkennung)               |
    |                                       |
    +-- Alle 4 Min: Heartbeat ---------->   |
    |   (Lebenszeichen + Diagnose)          |
    |                                       |
    +-- POST /klipper/push/jobs -------->   |
        (nur wenn neuer Job fertig)         |
```

## Sicherheit

- **Keine eingehenden Ports:** Die Bridge öffnet keine Ports. Alle Verbindungen gehen nur nach außen.
- **Nur lesende Zugriffe:** Moonraker wird nur gelesen, nie beschrieben oder gesteuert.
- **HTTPS erzwungen:** Der API-Key wird immer verschlüsselt über HTTPS gesendet.
- **API-Key sicher gespeichert:** Lokal in `~/.spooly-bridge.json` mit Berechtigungen `600`.
- **Keine externen Abhängigkeiten:** Nur Python-Standardbibliothek.
- **Open Source:** Der komplette Quellcode ist öffentlich einsehbar.
- **Keine Telemetrie:** Diagnosedaten werden nur mit ausdrücklicher Einwilligung gesendet.

## Einstellungen in Spooly

In den Spooly-Einstellungen unter Klipper/Moonraker, Bereich Bridge:

- **Automatische Updates:** Bridge aktualisiert sich selbst (Standard: an)
- **Diagnosedaten:** Verbindungsinfos zur Fehleranalyse senden (Standard: aus)

## Lizenz

GPL v3, siehe [LICENSE](LICENSE)
