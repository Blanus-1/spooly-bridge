"""Tests fuer den Autostart-Mechanismus auf Buildroot-Systemen (Snapmaker U1).

Hintergrund: busybox init startet Boot-Prozesse mit HOME=/ und cwd=/.
Die Bridge sucht ihre Config ueber Path.home() und fand sie beim Boot
deshalb nicht ("Kein API-Key" -> exit 1). Ausserdem blockierte die
Watchdog-Schleife seit v1.3.6 das rcS-Script, weil sie nie returnt.

Neue Loesung: eigenes /etc/init.d/S99spoolybridge das den Watchdog per
start-stop-daemon in den Hintergrund schickt, plus Watchdog-Script das
HOME und --config explizit setzt.
"""

import os
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from spooly_bridge.__main__ import (
    _watchdog_script_inhalt,
    _autostart_script_inhalt,
    _systemd_service_inhalt,
    _rcs_altlasten_entfernen,
    _u1_persistenz_aktivieren,
    PIDFILE_PFAD,
)


class TestWatchdogScript(unittest.TestCase):
    def setUp(self):
        self.inhalt = _watchdog_script_inhalt(
            basis="/root",
            python="/usr/bin/python3",
            config_pfad="/root/.spooly-bridge.json",
        )

    def test_shebang_ist_sh(self):
        # /bin/bash existiert auf Buildroot nicht zwingend
        self.assertTrue(self.inhalt.startswith("#!/bin/sh\n"))

    def test_setzt_home_explizit(self):
        # busybox init startet Boot-Prozesse mit HOME=/ - ohne Export
        # findet Path.home() die Config nicht
        self.assertIn("export HOME=/root", self.inhalt)

    def test_wechselt_ins_arbeitsverzeichnis(self):
        # cwd beim Boot ist / - python3 -m spooly_bridge braucht das
        # Modul-Verzeichnis im Pfad
        self.assertIn("cd /root", self.inhalt)

    def test_uebergibt_config_pfad_explizit(self):
        self.assertIn("--config /root/.spooly-bridge.json", self.inhalt)

    def test_watchdog_schleife_mit_pause(self):
        self.assertIn("while true", self.inhalt)
        self.assertIn("sleep 10", self.inhalt)

    def test_rein_ascii(self):
        self.inhalt.encode("ascii")


class TestAutostartScript(unittest.TestCase):
    def setUp(self):
        self.inhalt = _autostart_script_inhalt(script_pfad="/root/start-bridge.sh")

    def test_shebang_ist_sh(self):
        self.assertTrue(self.inhalt.startswith("#!/bin/sh\n"))

    def test_startet_im_hintergrund(self):
        # -b ist entscheidend: ohne Hintergrund haengt rcS beim Boot
        # fuer immer in der Watchdog-Schleife
        self.assertIn("start-stop-daemon -S -b -m -p", self.inhalt)

    def test_referenziert_watchdog_script(self):
        self.assertIn("/root/start-bridge.sh", self.inhalt)

    def test_nutzt_pidfile(self):
        self.assertIn(PIDFILE_PFAD, self.inhalt)

    def test_stop_beendet_watchdog_und_bridge(self):
        # -K killt nur die Watchdog-Schleife (per Pidfile), der Python-
        # Prozess braucht ein eigenes pkill
        self.assertIn("start-stop-daemon -K -p", self.inhalt)
        self.assertIn('pkill -f "start-bridge.sh"', self.inhalt)
        self.assertIn('pkill -f "spooly_bridge --config"', self.inhalt)
        self.assertIn('pkill -f "spooly_bridge$"', self.inhalt)

    def test_stop_trifft_nie_install_oder_uninstall(self):
        # Ein breites pkill -f "spooly_bridge" wuerde auch laufende
        # --install/--uninstall Aufrufe treffen (deren Cmdline enthaelt
        # das Muster) - deshalb nur die praezisen Laufzeit-Patterns
        self.assertNotIn('pkill -f "spooly_bridge"\n', self.inhalt)
        self.assertNotIn("pkill -f spooly_bridge", self.inhalt)

    def test_stop_killt_watchdog_vor_bridge(self):
        # Sonst startet der Watchdog die Bridge sofort wieder
        self.assertLess(
            self.inhalt.index('pkill -f "start-bridge.sh"'),
            self.inhalt.index('pkill -f "spooly_bridge --config"'),
        )

    def test_kein_killall(self):
        # killall python3 wuerde auch Klipper und Moonraker treffen
        self.assertNotIn("killall", self.inhalt)

    def test_unterstuetzt_start_stop_restart(self):
        for aktion in ["start)", "stop)", "restart)"]:
            self.assertIn(aktion, self.inhalt)

    def test_rein_ascii(self):
        self.inhalt.encode("ascii")


class TestSystemdService(unittest.TestCase):
    def setUp(self):
        self.inhalt = _systemd_service_inhalt(
            basis="/home/pi",
            python="/usr/bin/python3",
            config_pfad="/home/pi/.spooly-bridge.json",
        )

    def test_uebergibt_config_pfad_explizit(self):
        # Systemd-Units laufen ohne gesetztes HOME - Path.home() wuerde
        # auf den passwd-Eintrag von root zeigen, nicht auf den User der
        # die Bridge installiert hat
        self.assertIn("--config /home/pi/.spooly-bridge.json", self.inhalt)

    def test_startet_automatisch_neu(self):
        self.assertIn("Restart=always", self.inhalt)

    def test_wartet_auf_netzwerk(self):
        self.assertIn("After=network-online.target", self.inhalt)

    def test_arbeitsverzeichnis_gesetzt(self):
        self.assertIn("WorkingDirectory=/home/pi", self.inhalt)

    def test_rein_ascii(self):
        self.inhalt.encode("ascii")


class TestRcsAltlastenEntfernen(unittest.TestCase):
    RCS_FABRIK = (
        "#!/bin/sh\n"
        "for i in /etc/init.d/S??* ;do\n"
        "    [ ! -f \"$i\" ] && continue\n"
        "    $i start\n"
        "done\n"
    )

    def test_entfernt_alten_append_eintrag(self):
        inhalt = self.RCS_FABRIK + "/root/start-bridge.sh\n"
        bereinigt = _rcs_altlasten_entfernen(inhalt)
        self.assertEqual(bereinigt, self.RCS_FABRIK)

    def test_entfernt_nohup_variante(self):
        # Aeltere Installationen (vor v1.3.6) haengten teils andere
        # Aufruf-Formen an
        inhalt = self.RCS_FABRIK + "nohup /bin/sh /root/start-bridge.sh &\n"
        bereinigt = _rcs_altlasten_entfernen(inhalt)
        self.assertEqual(bereinigt, self.RCS_FABRIK)

    def test_laesst_fabrik_zeilen_unangetastet(self):
        bereinigt = _rcs_altlasten_entfernen(self.RCS_FABRIK)
        self.assertEqual(bereinigt, self.RCS_FABRIK)

    def test_idempotent(self):
        inhalt = self.RCS_FABRIK + "/root/start-bridge.sh\n"
        einmal = _rcs_altlasten_entfernen(inhalt)
        zweimal = _rcs_altlasten_entfernen(einmal)
        self.assertEqual(einmal, zweimal)


class TestU1Persistenz(unittest.TestCase):
    """Tests fuer das automatische Anlegen von /oem/.debug.

    Ohne diese Datei verwirft der Snapmaker U1 das Verzeichnis /etc bei
    jedem Neustart - und damit den Autostart. Frueher ein manueller Schritt
    (touch /oem/.debug), jetzt von der Installation erledigt.
    """

    def test_legt_marker_datei_an(self):
        with tempfile.TemporaryDirectory() as tmp:
            pfad = os.path.join(tmp, ".debug")
            self.assertTrue(_u1_persistenz_aktivieren(pfad))
            self.assertTrue(os.path.exists(pfad))

    def test_idempotent_bei_vorhandener_datei(self):
        # Reinstallation oder zuvor manuell angelegt: kein Fehler, kein
        # Ueberschreiben des Inhalts
        with tempfile.TemporaryDirectory() as tmp:
            pfad = os.path.join(tmp, ".debug")
            with open(pfad, "w") as f:
                f.write("vorher")
            self.assertTrue(_u1_persistenz_aktivieren(pfad))
            with open(pfad) as f:
                self.assertEqual(f.read(), "vorher")

    def test_false_wenn_nicht_schreibbar(self):
        # Elternverzeichnis existiert nicht -> kann nicht angelegt werden.
        # Der Aufrufer gibt dann den manuellen Befehl als Fallback aus.
        pfad = os.path.join("/oem-gibt-es-nicht-12345", ".debug")
        self.assertFalse(_u1_persistenz_aktivieren(pfad))


class TestBootSimulation(unittest.TestCase):
    """Simuliert die Boot-Umgebung von busybox init: HOME=/, cwd=/.

    Der Python-Interpreter wird durch ein Stub-Script ersetzt, das die
    tatsaechliche Umgebung beim Aufruf protokolliert.
    """

    def test_bridge_bekommt_korrektes_home_und_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "root")
            os.makedirs(home)
            protokoll = os.path.join(tmp, "aufruf.log")

            # Stub statt python3: protokolliert HOME, cwd und Argumente
            stub = os.path.join(tmp, "python3-stub")
            with open(stub, "w") as f:
                f.write(
                    "#!/bin/sh\n"
                    f"echo \"HOME=$HOME CWD=$(pwd) ARGS=$*\" > {protokoll}\n"
                )
            os.chmod(stub, 0o755)

            config_pfad = os.path.join(home, ".spooly-bridge.json")
            script = os.path.join(tmp, "start-bridge.sh")
            with open(script, "w") as f:
                f.write(_watchdog_script_inhalt(home, stub, config_pfad))
            os.chmod(script, 0o755)

            # Boot-Umgebung: leeres Environment bis auf HOME=/ wie bei
            # busybox init; timeout beendet die Endlos-Schleife
            subprocess.run(
                ["timeout", "1", "/usr/bin/env", "-i", "HOME=/", "/bin/sh", script],
                cwd="/",
                capture_output=True,
            )

            self.assertTrue(os.path.exists(protokoll), "Stub wurde nie aufgerufen")
            with open(protokoll) as f:
                zeile = f.read().strip()

            self.assertIn(f"HOME={home}", zeile)
            self.assertIn(f"CWD={home}", zeile)
            self.assertIn(f"--config {config_pfad}", zeile)
            self.assertIn("-m spooly_bridge", zeile)


if __name__ == "__main__":
    unittest.main()


class TestSelbstheilung(unittest.TestCase):
    """Tests fuer die Autostart-Selbstheilung im laufenden Betrieb.

    Eine laufende Bridge stellt bei jedem Heartbeat sicher, dass der
    naechste Boot funktioniert: /oem/.debug vorhanden, Watchdog- und
    init.d-Script aktuell, rcS-Altlasten weg. Damit heilen sich auch
    Alt-Installationen (vor v1.4.0) nach dem Auto-Update selbst - ohne
    dass der Nutzer neu installieren muss.
    """

    def setUp(self):
        import spooly_bridge.__main__ as m
        self.m = m
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "root")
        self.oem = os.path.join(self.tmp.name, "oem")
        self.initd = os.path.join(self.tmp.name, "etc", "init.d")
        os.makedirs(self.home)
        os.makedirs(self.oem)
        os.makedirs(self.initd)
        self.config_pfad = os.path.join(self.home, ".spooly-bridge.json")

        # Modul-Konstanten und Umgebung auf das Sandbox-Dateisystem umbiegen
        self.patches = [
            mock.patch.object(m, "OEM_VERZEICHNIS", self.oem),
            mock.patch.object(m, "OEM_DEBUG_PFAD", os.path.join(self.oem, ".debug")),
            mock.patch.object(m, "INITD_VERZEICHNIS", self.initd),
            mock.patch.object(m, "AUTOSTART_SCRIPT_PFAD", os.path.join(self.initd, "S99spoolybridge")),
            mock.patch.object(m, "RCS_PFAD", os.path.join(self.initd, "rcS")),
            mock.patch.object(m, "_ist_systemd_system", return_value=False),
            mock.patch.object(m, "_basis_verzeichnis", return_value=self.home),
            mock.patch.dict(os.environ, {"HOME": self.home}),
        ]
        for p in self.patches:
            p.start()
        self.log = mock.Mock()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_stellt_alles_her_wenn_nichts_da_ist(self):
        # Worst Case: U1 nach Firmware-Update, nichts mehr da ausser der
        # laufenden Bridge selbst
        self.m._autostart_selbstheilung(self.config_pfad, self.log)
        self.assertTrue(os.path.exists(os.path.join(self.oem, ".debug")))
        self.assertTrue(os.path.exists(os.path.join(self.home, "start-bridge.sh")))
        self.assertTrue(os.path.exists(os.path.join(self.initd, "S99spoolybridge")))
        self.log.info.assert_called_once()

    def test_idempotent_wenn_alles_intakt(self):
        # Zweiter Lauf direkt nach dem ersten: nichts zu tun, kein Log-Spam
        self.m._autostart_selbstheilung(self.config_pfad, self.log)
        self.log.reset_mock()
        self.m._autostart_selbstheilung(self.config_pfad, self.log)
        self.log.info.assert_not_called()

    def test_erneuert_veraltetes_watchdog_script(self):
        # Alte Fassung ohne HOME-Export (v1.3.x) muss ersetzt werden
        script = os.path.join(self.home, "start-bridge.sh")
        with open(script, "w") as f:
            f.write("#!/bin/sh\nwhile true; do python3 -m spooly_bridge; done\n")
        self.m._autostart_selbstheilung(self.config_pfad, self.log)
        with open(script) as f:
            self.assertIn("export HOME", f.read())

    def test_raeumt_rcs_altlast_weg(self):
        rcs = os.path.join(self.initd, "rcS")
        with open(rcs, "w") as f:
            f.write("#!/bin/sh\necho boot\n/bin/sh /root/start-bridge.sh\n")
        self.m._autostart_selbstheilung(self.config_pfad, self.log)
        with open(rcs) as f:
            self.assertNotIn("start-bridge.sh", f.read())

    def test_systemd_systeme_bleiben_unangetastet(self):
        with mock.patch.object(self.m, "_ist_systemd_system", return_value=True):
            self.m._autostart_selbstheilung(self.config_pfad, self.log)
        self.assertFalse(os.path.exists(os.path.join(self.home, "start-bridge.sh")))

    def test_wurzelverzeichnis_als_basis_macht_nichts(self):
        # Absurde Basis (Paket direkt unter /): nichts ins Wurzelverzeichnis
        # schreiben. Seit v1.5.0 haengt die Selbstheilung nicht mehr an HOME
        # und funktioniert damit auch in der busybox-Boot-Umgebung (HOME=/).
        with mock.patch.object(self.m, "_basis_verzeichnis", return_value="/"):
            self.m._autostart_selbstheilung(self.config_pfad, self.log)
        self.assertFalse(os.path.exists(os.path.join(self.initd, "S99spoolybridge")))
