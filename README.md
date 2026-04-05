# Spooly Bridge

> **Early Beta** — Dieses Projekt befindet sich in aktiver Entwicklung. Feedback und Fehlerberichte sind willkommen!

Verbindet deinen **Klipper/Moonraker** 3D-Drucker automatisch mit Spooly.

Die Bridge laeuft als kleines Script neben deiner Moonraker-Instanz und sendet abgeschlossene Druckjobs automatisch an dein Spooly-Konto — ohne Port-Forwarding, ohne Cloudflare Tunnel, ohne Aufwand.

## Funktionen

- **Echtzeit-Import:** Jobs werden sofort erkannt wenn ein Druck endet (WebSocket)
- Fallback auf 5-Minuten-Polling wenn WebSocket nicht verfuegbar
- G-Code Metadaten (Filament-Typ, Gewicht, Thumbnail)
- Spoolman-Integration (wenn installiert)
- Automatische Updates (steuerbar in Spooly)
- Keine externen Abhaengigkeiten (nur Python-Standardbibliothek)
- Laeuft auf Raspberry Pi, Snapmaker U1, Desktop, Docker

## Voraussetzungen

- **Python 3.8+** auf dem Drucker (bei Raspberry Pi und Snapmaker ab Werk vorhanden)
- **Git** auf deinem PC (nur zum Herunterladen, nicht auf dem Drucker noetig)
- **SSH-Zugang** zum Drucker

### Git installieren (falls nicht vorhanden)

**Windows:** [git-scm.com/download/win](https://git-scm.com/download/win) herunterladen und installieren. Danach **Git Bash** oder **PowerShell** nutzen.

**macOS:**
```bash
# Git wird beim ersten Aufruf automatisch installiert
git --version
```

**Linux:**
```bash
sudo apt install git
```

## Installation (Schritt fuer Schritt)

### Schritt 1: API-Key in Spooly generieren

1. Oeffne die Spooly Einstellungen unter **dev.spooly.eu** (Beta-Testumgebung)
2. Scrolle zu **Klipper / Moonraker**
3. Klappe **"Spooly Bridge"** auf
4. Klicke **"API-Key generieren"**
5. Kopiere den Key (sieht aus wie `spooly_br_xxxxxxxxxxxx`)

### Schritt 2: Bridge herunterladen und auf den Drucker kopieren

**Auf deinem PC** (nicht auf dem Drucker):

```bash
git clone https://github.com/Blanus-1/spooly-bridge.git
scp -r spooly-bridge/spooly_bridge BENUTZER@DRUCKER_IP:~/spooly_bridge/
```

Ersetze:
- `BENUTZER` mit dem SSH-Benutzernamen (meistens `pi` oder `root`)
- `DRUCKER_IP` mit der IP-Adresse deines Druckers

### Schritt 3: Bridge installieren

Per SSH auf den Drucker verbinden und den Installationsbefehl ausfuehren:

```bash
ssh BENUTZER@DRUCKER_IP
cd ~ && python3 -m spooly_bridge --install --key DEIN_API_KEY --spooly-url https://dev.spooly.eu/api
```

> **Wichtig:** Der Parameter `--spooly-url https://dev.spooly.eu/api` ist waehrend der Beta-Phase noetig. Sobald die Integration offiziell veroeffentlicht wird, entfaellt dieser Parameter.

### Was du nach der Installation sehen solltest

```
==================================================
  Spooly Bridge v1.2.0 — Installation
==================================================

[1/4] Moonraker pruefen...
  --> Moonraker gefunden: voron24 (Klipper v0.12.0)

[2/4] Spooly-Verbindung testen...
  --> Spooly verbunden! API-Key gueltig.

[3/4] Autostart einrichten...
  --> Start-Script eingerichtet (startet automatisch)

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

Wenn alles vier Schritte mit `-->` angezeigt werden, ist die Bridge fertig eingerichtet.

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

## Fehlerbehebung

### "Moonraker nicht erreichbar"

- Pruefe ob Moonraker laeuft: `curl http://localhost:7125/printer/info`
- Falls anderer Port: `--moonraker-url http://localhost:ANDERER_PORT`
- Falls anderer Rechner: `--moonraker-url http://DRUCKER_IP:7125`

### "Spooly nicht erreichbar oder API-Key ungueltig"

- Pruefe deine Internetverbindung: `ping spooly.eu`
- Generiere einen neuen API-Key in Spooly (Einstellungen → Klipper → Bridge)
- Pruefe ob der Key richtig kopiert wurde (beginnt mit `spooly_br_`)

### "git: command not found" (auf deinem PC)

- Windows: Installiere Git von [git-scm.com](https://git-scm.com/download/win)
- macOS: Fuehre `xcode-select --install` aus
- Linux: `sudo apt install git`

### "scp: command not found" (auf deinem PC)

- Windows: Nutze **PowerShell** (nicht CMD) — `scp` ist dort eingebaut
- Alternativ: [WinSCP](https://winscp.net) als grafisches Tool nutzen

### "Permission denied" beim SCP oder SSH

- Pruefe Benutzername und Passwort
- Bei Raspberry Pi: Standard ist `pi` / `raspberry`
- Bei Snapmaker U1: Standard ist `root`

### Bridge laeuft aber keine Jobs in Spooly

- Pruefe die Logs: `tail -20 ~/bridge.log`
- Starte die Bridge manuell mit Debug-Modus:
  ```bash
  python3 -m spooly_bridge --key DEIN_KEY --debug
  ```

## Parameter

| Parameter | Standard | Beschreibung |
|-----------|----------|-------------|
| `--key` / `-k` | — | Spooly API-Key (Pflicht) |
| `--moonraker-url` / `-m` | `http://localhost:7125` | Moonraker URL |
| `--spooly-url` / `-s` | `https://api.spooly.eu/api` | Spooly API |
| `--intervall` / `-i` | `300` | Polling-Intervall in Sekunden (Fallback) |
| `--install` | — | Installieren mit Verbindungstest + Autostart |
| `--uninstall` | — | Komplett deinstallieren |
| `--debug` | — | Ausfuehrliche Logausgabe |

## Deinstallation

```bash
ssh BENUTZER@DRUCKER_IP
python3 -m spooly_bridge --uninstall
```

Entfernt alles: Service, Konfiguration, Logs, Autostart-Eintraege.

## Unterstuetzte Drucker

| Drucker | Klipper | Moonraker | Getestet |
|---------|---------|-----------|----------|
| Snapmaker U1 | Ab Werk | Ab Werk | Ja |
| Voron (alle) | Selbst installiert | Selbst installiert | — |
| Ender 3 + Klipper | Selbst geflasht | Selbst installiert | — |
| Prusa MK3 + Klipper | Selbst geflasht | Selbst installiert | — |
| QIDI (X-Plus 3, etc.) | Ab Werk | Ab Werk | — |

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
    +-- Alle 5 Min: Heartbeat ---------->   |
    |   (Lebenszeichen + Diagnose)          |
    |                                       |
    +-- POST /klipper/push/jobs -------->   |
        (nur wenn neuer Job fertig)         |
```

## Sicherheit

- **Keine eingehenden Ports:** Die Bridge oeffnet keine Ports. Alle Verbindungen gehen nur nach aussen.
- **Nur lesende Zugriffe:** Moonraker wird nur gelesen, nie beschrieben oder gesteuert.
- **HTTPS erzwungen:** API-Key wird immer verschluesselt ueber HTTPS gesendet.
- **API-Key sicher gespeichert:** Lokal in `~/.spooly-bridge.json` mit Berechtigungen `600`.
- **Keine externen Abhaengigkeiten:** Nur Python-Standardbibliothek.
- **Open Source:** Der komplette Quellcode ist oeffentlich einsehbar.
- **Kein Telemetrie:** Diagnosedaten werden nur mit ausdruecklicher Einwilligung gesendet.

## Einstellungen in Spooly

In den Spooly-Einstellungen unter Klipper/Moonraker → Bridge:

- **Automatische Updates:** Bridge aktualisiert sich selbst (Standard: an)
- **Diagnosedaten:** Verbindungsinfos zur Fehleranalyse senden (Standard: aus)

## Lizenz

GPL v3 — siehe [LICENSE](LICENSE)
