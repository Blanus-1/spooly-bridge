# Spooly Bridge

Verbindet deinen Klipper-Drucker mit [Spooly](https://spooly.eu). Die Bridge läuft auf dem Drucker neben Moonraker und schickt jeden fertigen Druck automatisch an dein Spooly-Konto. Du brauchst dafür weder Port-Forwarding noch einen Tunnel.

> Die Klipper-Anbindung ist noch in der Beta. Feedback und Fehlerberichte gerne als Issue.

## Installation

Die Einrichtung startest du in Spooly: **Einstellungen → Drucker-Verbindungen → Klipper / Moonraker → Bridge einrichten**. Dort steht der fertige Befehl, dein Key ist schon eingesetzt.

### Snapmaker U1: vom Computer aus

1. Am Drucker den Root-Zugriff einschalten: **Wartung → Root-Zugriff** (englisch: Maintenance → Root Access).
2. In Spooly bei Schritt 2 „Vom Computer“ den Befehl kopieren und ausführen, am Mac oder unter Linux im Terminal, unter Windows in der PowerShell.
3. Fragt er nach dem Passwort, `snapmaker` eingeben. Beim Tippen erscheinen keine Zeichen, das ist normal.
4. Danach den Root-Zugriff am Drucker wieder ausschalten. Die Bridge braucht ihn nicht.

Der Befehl sucht den U1 in deinem Netzwerk, verbindet sich per SSH und installiert die Bridge auf dem Drucker. Auf dem Computer bleibt nichts zurück. So sieht er aus:

```bash
# Mac / Linux
curl -fsSL "https://spooly.eu/b?c=sh" | sh -s -- spooly_br_DEIN_KEY
```

```powershell
# Windows
$env:SPOOLY_KEY='spooly_br_DEIN_KEY'; irm 'https://spooly.eu/b?c=ps1' | iex
```

Wer vorher reinschauen will, öffnet die beiden Adressen einfach im Browser.

### Andere Klipper-Drucker (Raspberry Pi & Co.)

Per SSH auf den Drucker, in Spooly bei Schritt 2 „Auf dem Drucker“ wählen und den Befehl dort ausführen. Auf dem Raspberry Pi vorher `sudo -i`, weil der Autostart als systemd-Dienst eingerichtet wird.

```bash
python3 -c "import urllib.request; open('/tmp/spooly-install.py','wb').write(urllib.request.urlopen('https://spooly.eu/b').read())"
python3 /tmp/spooly-install.py --key spooly_br_DEIN_KEY
```

Voraussetzung ist Python 3.8 oder neuer, bei Klipper-Systemen ist das ab Werk dabei. Weitere Pakete braucht die Bridge nicht.

## Was danach passiert

- **Autostart:** Die Bridge startet nach jedem Neustart des Druckers von selbst.
- **Updates:** Neue Versionen installiert sie selbst, sie kommen direkt aus diesem Repository.
- **Umzug:** Wechselt dein Konto zwischen dev.spooly.eu und spooly.eu, findet die Bridge das neue Ziel ohne Neuinstallation.
- **Status:** In Spooly siehst du unter Schritt 3, ob die Bridge online ist, welche Version läuft und wo sie installiert ist.

## Snapmaker U1: was am Drucker geändert wird

- Die Installation legt `/oem/.debug` an. Ohne diese Datei verwirft der U1 bei jedem Neustart alle Änderungen an `/etc` und damit auch den Autostart ([Hintergrund](https://snapmakeru1-extended-firmware.pages.dev/data_persistence)).
- Der Start steht zusätzlich in einem vorhandenen Init-Skript, erkennbar an der Zeile mit `# spooly-bridge-autostart`. Ein neu angelegtes Skript würde der U1 beim Booten nie aufrufen.
- Mit `/oem/.debug` liest der U1 das WLAN aus `/etc/wpa_supplicant.conf` statt aus seiner eigenen Datei. Die Bridge hält beide gleich, damit das WLAN nach einem Neustart bleibt. Wer vor Version 1.5.4 installiert hat, musste es nach dem ersten Neustart einmal neu eingeben.
- Ein Firmware-Update macht das alles rückgängig. Danach den Befehl einfach noch einmal ausführen, der Key bleibt gültig.

## Wenn etwas nicht klappt

- **Bridge nach einem Neustart offline:** Befehl noch einmal ausführen, das repariert den Autostart.
- **SSH verweigert die Verbindung:** Ist der Root-Zugriff am Drucker an? Stimmt das Passwort?
- **Drucker wird nicht gefunden:** Computer und Drucker müssen im selben Netzwerk sein. Sonst fragt der Befehl nach der IP-Adresse, die steht in den WLAN-Einstellungen am Drucker oder im Router.
- **Log ansehen:** `bridge.log` im Installationsordner, auf dem U1 `/userdata/spooly_bridge/bridge.log`.

## Entfernen

Per SSH auf den Drucker und im Installationsordner (Spooly zeigt ihn unter Schritt 3):

```bash
cd /userdata/spooly_bridge
python3 -m spooly_bridge --uninstall
```

Das entfernt Autostart, Konfiguration und Log.

## Sicherheit und Daten

- Die Bridge öffnet keine Ports. Sie baut nur ausgehende HTTPS-Verbindungen zu Spooly und für Updates zu GitHub auf.
- Moonraker wird nur gelesen, nie gesteuert.
- Der Key liegt in `.spooly-bridge.json` im Installationsordner und ist nur für den Besitzer lesbar.
- Welche Daten übertragen werden, steht in der [Datenschutzerklärung von Spooly](https://spooly.eu/datenschutz), Abschnitt 8.8.

## Lizenz

GPL v3, siehe [LICENSE](LICENSE)
