"""Tests fuer den Boot-Zuender auf dem Snapmaker U1.

Hintergrund (am echten Geraet bewiesen, 06.09.2026): rcS startet mit
`for i in /etc/init.d/S??*`. Die Shell expandiert diesen Glob EINMAL, bevor
die Schleife laeuft - und damit bevor S01aoverlayfs den Overlay per pivot_root
einhaengt. Ein neu angelegtes Script ist zu dem Zeitpunkt unsichtbar und steht
nie in der Liste, egal wie es heisst. Der Autostart lief deshalb auf keinem
Original-U1 jemals an.

Der Ausweg: eine Startzeile in ein VORHANDENES Firmware-Script schreiben. Dessen
Name steht im Glob, und `$i start` loest den Pfad erst zur Ausfuehrungszeit auf,
wenn der Overlay bereits aktiv ist - also laeuft die ergaenzte Fassung.
"""

import os
import subprocess
import tempfile
import unittest

from spooly_bridge.__main__ import (
    AUTOSTART_SCRIPT_PFAD,
    BOOT_ZUENDER_MARKER,
    _boot_zuender_entfernen,
    _boot_zuender_setzen,
    _boot_zuender_ziel,
)

FIRMWARE_SCRIPT = '#!/bin/sh\n# Firmware-Script\ncase "$1" in\n  start) echo gestartet ;;\nesac\nexit $?\n'


class TestZielwahl(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _anlegen(self, *namen):
        for n in namen:
            with open(os.path.join(self.tmp, n), "w") as f:
                f.write(FIRMWARE_SCRIPT)

    def test_nimmt_das_alphabetisch_letzte_firmware_script(self):
        self._anlegen("S01mount", "S60klipper", "S99input-event-daemon")
        self.assertEqual(
            _boot_zuender_ziel(self.tmp), os.path.join(self.tmp, "S99input-event-daemon")
        )

    def test_ignoriert_das_eigene_script(self):
        """Sonst haengt sich die Bridge an sich selbst und nichts startet."""
        self._anlegen("S60klipper", os.path.basename(AUTOSTART_SCRIPT_PFAD))
        self.assertEqual(_boot_zuender_ziel(self.tmp), os.path.join(self.tmp, "S60klipper"))

    def test_ignoriert_rcS_und_rcK(self):
        self._anlegen("S60klipper", "rcS", "rcK")
        self.assertEqual(_boot_zuender_ziel(self.tmp), os.path.join(self.tmp, "S60klipper"))

    def test_none_wenn_nichts_da(self):
        self.assertIsNone(_boot_zuender_ziel(self.tmp))
        self.assertIsNone(_boot_zuender_ziel(os.path.join(self.tmp, "gibtsnicht")))


class TestSetzenUndEntfernen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ziel = os.path.join(self.tmp, "S99input-event-daemon")
        with open(self.ziel, "w") as f:
            f.write(FIRMWARE_SCRIPT)
        os.chmod(self.ziel, 0o755)

    def _inhalt(self):
        with open(self.ziel) as f:
            return f.read()

    def test_zeile_steht_vor_dem_exit(self):
        """Ans Dateiende gehaengt wuerde sie nie erreicht - das Script endet mit exit."""
        _boot_zuender_setzen(self.ziel)
        inhalt = self._inhalt()
        self.assertIn(BOOT_ZUENDER_MARKER, inhalt)
        self.assertLess(inhalt.index(AUTOSTART_SCRIPT_PFAD), inhalt.index("exit $?"))

    def test_shebang_bleibt_erste_zeile(self):
        _boot_zuender_setzen(self.ziel)
        self.assertTrue(self._inhalt().startswith("#!/bin/sh\n"))

    def test_idempotent(self):
        self.assertTrue(_boot_zuender_setzen(self.ziel))
        nach_erstem = self._inhalt()
        self.assertFalse(_boot_zuender_setzen(self.ziel))
        self.assertEqual(self._inhalt(), nach_erstem)
        self.assertEqual(nach_erstem.count(BOOT_ZUENDER_MARKER), 1)

    def test_entfernen_stellt_das_original_her(self):
        original = self._inhalt()
        _boot_zuender_setzen(self.ziel)
        self.assertTrue(_boot_zuender_entfernen(self.ziel))
        self.assertEqual(self._inhalt(), original)
        self.assertFalse(_boot_zuender_entfernen(self.ziel))

    def test_script_bleibt_syntaktisch_gueltig(self):
        _boot_zuender_setzen(self.ziel)
        self.assertEqual(subprocess.run(["sh", "-n", self.ziel]).returncode, 0)

    def test_original_funktion_bleibt_erhalten(self):
        _boot_zuender_setzen(self.ziel)
        ergebnis = subprocess.run(["sh", self.ziel, "start"], capture_output=True, text=True)
        self.assertIn("gestartet", ergebnis.stdout)

    def test_zuender_nur_bei_start(self):
        """Bei stop/restart darf die Bridge nicht mitgestartet werden."""
        _boot_zuender_setzen(self.ziel)
        with open(self.ziel) as f:
            zeile = [z for z in f if AUTOSTART_SCRIPT_PFAD in z and not z.startswith("#")][0]
        self.assertIn('"$1" = start', zeile)


class TestBootVerhaltenU1(unittest.TestCase):
    """Stellt den U1-Boot nach: Glob vor Overlay-Mount."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.initd = os.path.join(self.tmp, "initd")
        os.makedirs(self.initd)
        self.beweis = os.path.join(self.tmp, "bridge-lief")
        for n in ("S01mount", "S99input-event-daemon"):
            p = os.path.join(self.initd, n)
            with open(p, "w") as f:
                f.write(FIRMWARE_SCRIPT)
            os.chmod(p, 0o755)
        # das Script, das die Bridge startet - existiert nur im "Overlay"
        self.eigenes = os.path.join(self.initd, "S99spoolybridge")
        self.versteck = os.path.join(self.tmp, "S99spoolybridge.nochnichtda")

    def _eigenes_anlegen(self):
        with open(self.eigenes, "w") as f:
            f.write('#!/bin/sh\n[ "$1" = start ] && touch %s\nexit 0\n' % self.beweis)
        os.chmod(self.eigenes, 0o755)

    def _boot(self):
        """rcS-Nachbau: Glob VOR dem Overlay, Schleife danach."""
        if os.path.exists(self.beweis):
            os.remove(self.beweis)
        skript = (
            'set -- %s/S??*\n'          # Glob expandiert: Overlay noch nicht da
            'sh -c "$OVERLAY_MOUNT"\n'  # jetzt wird der Overlay eingeblendet
            'for i in "$@"; do [ -f "$i" ] && "$i" start; done\n'
        ) % self.initd
        subprocess.run(["sh", "-c", skript], env={**os.environ, "OVERLAY_MOUNT": self.overlay_mount})
        return os.path.exists(self.beweis)

    def test_neues_script_startet_nie(self):
        """Der Befund vom echten Geraet: eigenes Script wird nie aufgerufen."""
        self.overlay_mount = "true"
        self._eigenes_anlegen()          # liegt schon vor dem Glob da...
        os.rename(self.eigenes, self.versteck)
        self.overlay_mount = "mv %s %s" % (self.versteck, self.eigenes)
        self.assertFalse(self._boot(), "Script aus dem Overlay darf im Glob nicht auftauchen")

    def test_mit_zuender_startet_die_bridge(self):
        """Der Fix: die Startzeile steckt in einem Script, das im Glob steht."""
        self._eigenes_anlegen()
        os.rename(self.eigenes, self.versteck)
        self.overlay_mount = "mv %s %s" % (self.versteck, self.eigenes)
        ziel = _boot_zuender_ziel(self.initd)
        self.assertTrue(ziel.endswith("S99input-event-daemon"))
        _boot_zuender_setzen(ziel, autostart_pfad=self.eigenes)
        self.assertTrue(self._boot(), "Zuender muss die Bridge starten")


if __name__ == "__main__":
    unittest.main()
