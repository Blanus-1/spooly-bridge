"""Tests fuer den Ein-Befehl-Installer (install.py im Repo-Root).

Der Installer wird per wget/curl von raw.githubusercontent geladen und
in python3 gepipet. Er laedt die Bridge-Dateien herunter und ruft danach
die normale Installation auf - git clone und scp auf dem PC entfallen.
"""

import contextlib
import io
import os
import tempfile
import unittest

import install
from spooly_bridge.updater import BRIDGE_DATEIEN, GITHUB_RAW_BASE


class TestDateiliste(unittest.TestCase):
    def test_identisch_mit_updater_liste(self):
        # Installer und Updater muessen dieselben Dateien kennen, sonst
        # fehlt nach einer Neuinstallation ein Modul
        self.assertEqual(install.DATEIEN, BRIDGE_DATEIEN)

    def test_url_zeigt_auf_offizielles_repo(self):
        url = install.url_fuer("__main__.py")
        self.assertEqual(url, f"{GITHUB_RAW_BASE}/__main__.py")
        self.assertTrue(url.startswith("https://"))


class TestArgumente(unittest.TestCase):
    def test_key_ist_pflicht(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                install.argumente_parsen([])

    def test_minimal_aufruf(self):
        args = install.argumente_parsen(["--key", "spooly_br_abc"])
        self.assertEqual(args.key, "spooly_br_abc")
        self.assertIsNone(args.spooly_url)
        self.assertIsNone(args.moonraker_url)

    def test_optionale_urls(self):
        args = install.argumente_parsen([
            "--key", "spooly_br_abc",
            "--spooly-url", "https://dev.spooly.eu/api",
            "--moonraker-url", "http://localhost:7126",
        ])
        self.assertEqual(args.spooly_url, "https://dev.spooly.eu/api")
        self.assertEqual(args.moonraker_url, "http://localhost:7126")


class TestInstallKommando(unittest.TestCase):
    def test_minimal(self):
        args = install.argumente_parsen(["--key", "spooly_br_abc"])
        kommando = install.install_kommando("/usr/bin/python3", args)
        self.assertEqual(
            kommando,
            ["/usr/bin/python3", "-m", "spooly_bridge", "--install",
             "--key", "spooly_br_abc"],
        )

    def test_reicht_optionale_urls_durch(self):
        args = install.argumente_parsen([
            "--key", "spooly_br_abc",
            "--spooly-url", "https://dev.spooly.eu/api",
        ])
        kommando = install.install_kommando("/usr/bin/python3", args)
        self.assertIn("--spooly-url", kommando)
        self.assertIn("https://dev.spooly.eu/api", kommando)
        self.assertNotIn("--moonraker-url", kommando)


class TestDownload(unittest.TestCase):
    def test_schreibt_alle_dateien(self):
        def fake_lader(url):
            return f"# Inhalt von {url}\n"

        with tempfile.TemporaryDirectory() as tmp:
            ziel = os.path.join(tmp, "spooly_bridge")
            ok = install.dateien_herunterladen(ziel, lader=fake_lader)
            self.assertTrue(ok)
            for name in install.DATEIEN:
                self.assertTrue(os.path.exists(os.path.join(ziel, name)))

    def test_alles_oder_nichts(self):
        # Wenn ein Download fehlschlaegt, darf keine halbe Installation
        # zurueckbleiben
        def kaputter_lader(url):
            if "uploader" in url:
                return None
            return "# ok\n"

        with tempfile.TemporaryDirectory() as tmp:
            ziel = os.path.join(tmp, "spooly_bridge")
            with contextlib.redirect_stdout(io.StringIO()):
                ok = install.dateien_herunterladen(ziel, lader=kaputter_lader)
            self.assertFalse(ok)
            self.assertFalse(os.path.exists(ziel))


if __name__ == "__main__":
    unittest.main()
