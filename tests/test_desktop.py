"""Tests fuer die Erkennung von Mac/Windows-Installationen.

Die Bridge gehoert auf den Drucker. Landet der Installations-Befehl auf einem
Mac oder PC (Nutzer kopiert ihn ins lokale Terminal statt in die SSH-Sitzung),
soll das klar gesagt werden, statt still in den Buildroot-Zweig zu laufen und
/etc/init.d zu suchen. Ausserdem meldet der Heartbeat jetzt, ob und wo die
Bridge ihren Moonraker erreicht.
"""

import unittest
from unittest import mock

import install
from spooly_bridge.__main__ import (
    MOONRAKER_DEFAULT_URL,
    _desktop_pruefung,
    _install_metadaten_ermitteln,
    _ist_desktop_system,
)
from spooly_bridge.uploader import SpoolyUploader

DRUCKER_URL = "http://192.168.1.50:7125"


class TestDesktopErkennung(unittest.TestCase):
    def test_mac_und_windows_sind_desktop(self):
        self.assertTrue(_ist_desktop_system("darwin"))
        self.assertTrue(_ist_desktop_system("win32"))

    def test_linux_ist_kein_desktop(self):
        self.assertFalse(_ist_desktop_system("linux"))


class TestDesktopPruefung(unittest.TestCase):
    def test_windows_wird_abgelehnt(self):
        self.assertIn("Windows", _desktop_pruefung(DRUCKER_URL, "win32"))

    def test_mac_ohne_drucker_adresse_bekommt_anleitung(self):
        self.assertIn("--moonraker-url", _desktop_pruefung(MOONRAKER_DEFAULT_URL, "darwin"))

    def test_mac_mit_drucker_adresse_darf_weiter(self):
        self.assertIsNone(_desktop_pruefung(DRUCKER_URL, "darwin"))

    def test_drucker_immer_ok(self):
        self.assertIsNone(_desktop_pruefung(MOONRAKER_DEFAULT_URL, "linux"))


class TestInstallMetadaten(unittest.TestCase):
    def test_mac_meldet_macos_ohne_autostart(self):
        with mock.patch("spooly_bridge.__main__.sys.platform", "darwin"):
            meta = _install_metadaten_ermitteln()
        self.assertEqual(meta["os_family"], "macos")
        self.assertEqual(meta["install_method"], "none")

    def test_windows_meldet_windows(self):
        with mock.patch("spooly_bridge.__main__.sys.platform", "win32"):
            meta = _install_metadaten_ermitteln()
        self.assertEqual(meta["os_family"], "windows")
        self.assertEqual(meta["install_method"], "none")

    def test_drucker_bleibt_wie_bisher(self):
        with mock.patch("spooly_bridge.__main__.sys.platform", "linux"), \
             mock.patch("spooly_bridge.__main__._ist_systemd_system", return_value=False), \
             mock.patch("spooly_bridge.__main__.os.path.exists", return_value=False):
            meta = _install_metadaten_ermitteln()
        self.assertEqual(meta["os_family"], "buildroot")
        self.assertEqual(meta["install_method"], "watchdog-only")


class TestInstallerVorpruefung(unittest.TestCase):
    # Der Ein-Befehl-Installer prueft VOR dem Download - niemand soll erst
    # Dateien ziehen und dann an derselben Stelle scheitern

    def test_windows_bricht_ab(self):
        self.assertIn("Windows", install.plattform_pruefen(None, "win32"))

    def test_mac_ohne_url_bricht_ab(self):
        self.assertIn("--moonraker-url", install.plattform_pruefen(None, "darwin"))

    def test_mac_mit_url_laeuft_weiter(self):
        self.assertIsNone(install.plattform_pruefen(DRUCKER_URL, "darwin"))

    def test_drucker_laeuft_weiter(self):
        self.assertIsNone(install.plattform_pruefen(None, "linux"))


class TestHeartbeatFelder(unittest.TestCase):
    def setUp(self):
        self.uploader = SpoolyUploader("https://spooly.test/api", "spooly_br_x")

    def test_moonraker_status_geht_mit(self):
        with mock.patch.object(self.uploader, "_post", return_value={"success": True}) as post:
            self.uploader.heartbeat(moonraker_url=DRUCKER_URL, moonraker_erreichbar=False)
        payload = post.call_args[0][1]
        self.assertEqual(payload["moonraker_url"], DRUCKER_URL)
        self.assertIs(payload["moonraker_reachable"], False)

    def test_ohne_angabe_keine_neuen_felder(self):
        # Aeltere Aufrufer (z.B. Tests, Installer-Vorlauf) bleiben kompatibel
        with mock.patch.object(self.uploader, "_post", return_value={"success": True}) as post:
            self.uploader.heartbeat()
        payload = post.call_args[0][1]
        self.assertNotIn("moonraker_url", payload)
        self.assertNotIn("moonraker_reachable", payload)


if __name__ == "__main__":
    unittest.main()
