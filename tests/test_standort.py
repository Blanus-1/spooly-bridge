"""Tests fuer die standort-bewusste Installation (v1.5.0).

Die Bridge leitet Config-, Log-, Watchdog- und Autostart-Pfade aus ihrem
eigenen Installations-Verzeichnis her (Eltern des Pakets) statt aus
Path.home(). Beim klassischen Layout (~/spooly_bridge) aendert sich dadurch
nichts; liegt das Paket auf einer persistenten Partition (z.B. /userdata,
das Paxx-Wipes von /root ueberlebt), wandern alle Dateien automatisch mit.
"""

import logging
import os
import tempfile
import unittest
from unittest import mock

from spooly_bridge.__main__ import (
    _autostart_selbstheilung,
    _basis_verzeichnis,
    _install_metadaten_ermitteln,
    _watchdog_script_inhalt,
)
from spooly_bridge.uploader import SpoolyUploader

log = logging.getLogger("spooly-bridge-test")


class TestBasisVerzeichnis(unittest.TestCase):
    def test_basis_ist_eltern_des_pakets(self):
        basis = _basis_verzeichnis("/userdata/spooly_bridge/spooly_bridge/__main__.py")
        self.assertEqual(basis, "/userdata/spooly_bridge")

    def test_klassisches_layout_ergibt_home(self):
        basis = _basis_verzeichnis("/root/spooly_bridge/__main__.py")
        self.assertEqual(basis, "/root")

    def test_site_packages_faellt_auf_home_zurueck(self):
        pfad = "/usr/lib/python3.11/site-packages/spooly_bridge/__main__.py"
        self.assertEqual(_basis_verzeichnis(pfad), str(os.path.expanduser("~")))

    def test_dist_packages_faellt_auf_home_zurueck(self):
        pfad = "/usr/lib/python3/dist-packages/spooly_bridge/__main__.py"
        self.assertEqual(_basis_verzeichnis(pfad), str(os.path.expanduser("~")))

    def test_default_zeigt_auf_echtes_paket(self):
        # Ohne Argument: Eltern des echten Pakets in diesem Repo
        basis = _basis_verzeichnis()
        self.assertTrue(os.path.isdir(os.path.join(basis, "spooly_bridge")))


class TestInstallMetadaten(unittest.TestCase):
    def test_buildroot_mit_initd_autostart(self):
        with mock.patch("spooly_bridge.__main__._ist_systemd_system", return_value=False), \
             mock.patch("spooly_bridge.__main__._basis_verzeichnis", return_value="/userdata/spooly_bridge"), \
             mock.patch("os.path.exists", side_effect=lambda p: p == "/etc/init.d/S99spoolybridge"):
            meta = _install_metadaten_ermitteln()
        self.assertEqual(meta["install_path"], "/userdata/spooly_bridge")
        self.assertEqual(meta["install_method"], "initd")
        self.assertEqual(meta["os_family"], "buildroot")
        self.assertFalse(meta["is_paxx"])

    def test_paxx_marker_wird_erkannt(self):
        with mock.patch("spooly_bridge.__main__._ist_systemd_system", return_value=False), \
             mock.patch("os.path.exists", side_effect=lambda p: p == "/oem/paxx"):
            meta = _install_metadaten_ermitteln()
        self.assertTrue(meta["is_paxx"])
        self.assertEqual(meta["install_method"], "watchdog-only")

    def test_systemd_system_meldet_debian(self):
        with mock.patch("spooly_bridge.__main__._ist_systemd_system", return_value=True), \
             mock.patch("os.path.exists", side_effect=lambda p: p == "/etc/systemd/system/spooly-bridge.service"):
            meta = _install_metadaten_ermitteln()
        self.assertEqual(meta["os_family"], "debian")
        self.assertEqual(meta["install_method"], "systemd")


class TestSelbstheilungNutztBasis(unittest.TestCase):
    def test_watchdog_landet_im_basis_verzeichnis(self):
        """Bei einer /userdata-Installation muss die Selbstheilung den
        Watchdog dort erneuern - nicht im fluechtigen Home."""
        with tempfile.TemporaryDirectory() as basis:
            config_pfad = os.path.join(basis, ".spooly-bridge.json")
            with mock.patch("spooly_bridge.__main__._ist_systemd_system", return_value=False), \
                 mock.patch("spooly_bridge.__main__._basis_verzeichnis", return_value=basis), \
                 mock.patch("spooly_bridge.__main__.OEM_VERZEICHNIS", os.path.join(basis, "kein-oem")), \
                 mock.patch("spooly_bridge.__main__.INITD_VERZEICHNIS", os.path.join(basis, "kein-initd")), \
                 mock.patch("spooly_bridge.__main__.RCS_PFAD", os.path.join(basis, "kein-rcs")):
                _autostart_selbstheilung(config_pfad, log)

            script = os.path.join(basis, "start-bridge.sh")
            self.assertTrue(os.path.exists(script))
            with open(script) as f:
                inhalt = f.read()
            self.assertIn(f"cd {basis}", inhalt)
            self.assertIn(f"--config {config_pfad}", inhalt)


class TestWatchdogMitBasis(unittest.TestCase):
    def test_userdata_basis_in_home_und_cd(self):
        inhalt = _watchdog_script_inhalt(
            basis="/userdata/spooly_bridge",
            python="/usr/bin/python3",
            config_pfad="/userdata/spooly_bridge/.spooly-bridge.json",
        )
        self.assertIn("export HOME=/userdata/spooly_bridge\n", inhalt)
        # cd steht IN der Schleife mit Fallback: /userdata kann beim Boot
        # spaeter mounten, dann wartet der Watchdog statt endlos mit cwd=/
        # zu scheitern
        self.assertIn("cd /userdata/spooly_bridge || { sleep 10; continue; }", inhalt)
        self.assertIn("--config /userdata/spooly_bridge/.spooly-bridge.json", inhalt)


class TestHeartbeatMetadaten(unittest.TestCase):
    def test_metadaten_landen_im_payload(self):
        uploader = SpoolyUploader("https://localhost/api", "spooly_br_test")
        with mock.patch.object(uploader, "_post", return_value={"success": True}) as post:
            uploader.heartbeat(
                drucker_name="U1",
                install_metadaten={
                    "install_path": "/userdata/spooly_bridge",
                    "install_method": "initd",
                    "os_family": "buildroot",
                    "is_paxx": True,
                },
            )
        payload = post.call_args.args[1]
        self.assertEqual(payload["install_path"], "/userdata/spooly_bridge")
        self.assertEqual(payload["install_method"], "initd")
        self.assertEqual(payload["os_family"], "buildroot")
        self.assertTrue(payload["is_paxx"])

    def test_ohne_metadaten_bleibt_payload_wie_bisher(self):
        uploader = SpoolyUploader("https://localhost/api", "spooly_br_test")
        with mock.patch.object(uploader, "_post", return_value={"success": True}) as post:
            uploader.heartbeat(drucker_name="U1")
        payload = post.call_args.args[1]
        self.assertNotIn("install_path", payload)
        self.assertNotIn("is_paxx", payload)


if __name__ == "__main__":
    unittest.main()
