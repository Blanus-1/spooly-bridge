"""Tests fuer den Adresswechsel zwischen Test- und Produktiv-Spooly.

Zieht ein Konto um (dev.spooly.eu -> spooly.eu), lehnt die alte Adresse den
Key mit 401 ab. Die Bridge fragt dann einmal bei der anderen bekannten Adresse
nach und bleibt dort, wenn der Heartbeat klappt - ohne Neuinstallation.
"""

import json
import logging
import os
import tempfile
import unittest
from unittest import mock

from spooly_bridge.__main__ import _andere_spooly_adresse, _sende_heartbeat, _spooly_umzug
from spooly_bridge.config import BridgeConfig, lade_config, speichere_config

LOG = logging.getLogger("test")


class FakeUploader:
    """Antwortet je Adresse mit einem festen Ergebnis und merkt sich die Aufrufe."""

    def __init__(self, basis_url, antworten):
        self.basis_url = basis_url
        self.antworten = antworten
        self.letzter_status = None
        self.aufrufe = []

    def heartbeat(self, **_kwargs):
        self.aufrufe.append(self.basis_url)
        status, ergebnis = self.antworten.get(self.basis_url, (None, None))
        self.letzter_status = status
        return ergebnis


class FakePoller:
    basis_url = "http://localhost:7125"

    def drucker_info(self):
        return {"hostname": "lava"}

    def erreichbarkeit(self):
        return True, "ready"


class TestAndereAdresse(unittest.TestCase):
    def test_paar_in_beide_richtungen(self):
        self.assertEqual(_andere_spooly_adresse("https://dev.spooly.eu/api"), "https://spooly.eu/api")
        self.assertEqual(_andere_spooly_adresse("https://spooly.eu/api/"), "https://dev.spooly.eu/api")

    def test_eigene_url_bleibt_unberuehrt(self):
        self.assertIsNone(_andere_spooly_adresse("https://spooly.meinserver.de/api"))
        self.assertIsNone(_andere_spooly_adresse("http://localhost:8001/api"))


class TestUmzug(unittest.TestCase):
    def setUp(self):
        self.pfad = os.path.join(tempfile.mkdtemp(), ".spooly-bridge.json")
        speichere_config(BridgeConfig(api_key="spooly_br_K", spooly_url="https://dev.spooly.eu/api"), self.pfad)

    def test_key_gilt_auf_live_bridge_wechselt_und_speichert(self):
        up = FakeUploader("https://dev.spooly.eu/api", {
            "https://dev.spooly.eu/api": (401, None),
            "https://spooly.eu/api": (200, {"success": True}),
        })
        with mock.patch("spooly_bridge.__main__._install_metadaten_ermitteln", return_value={}):
            self.assertTrue(_sende_heartbeat(FakePoller(), up, LOG, self.pfad))
        self.assertEqual(up.basis_url, "https://spooly.eu/api")
        self.assertEqual(up.aufrufe, ["https://dev.spooly.eu/api", "https://spooly.eu/api"])
        self.assertEqual(lade_config(self.pfad).spooly_url, "https://spooly.eu/api")
        # Der Key bleibt in der Config erhalten
        with open(self.pfad) as f:
            self.assertEqual(json.load(f)["api_key"], "spooly_br_K")

    def test_key_gilt_nirgends_bridge_bleibt(self):
        up = FakeUploader("https://dev.spooly.eu/api", {
            "https://dev.spooly.eu/api": (401, None),
            "https://spooly.eu/api": (401, None),
        })
        self.assertIsNone(_spooly_umzug(up, lambda: up.heartbeat(), self.pfad, LOG))
        self.assertEqual(up.basis_url, "https://dev.spooly.eu/api")
        self.assertEqual(lade_config(self.pfad).spooly_url, "https://dev.spooly.eu/api")

    def test_anderer_fehler_loest_keinen_wechsel_aus(self):
        # 403 (Integration nicht freigeschaltet) oder Netzfehler sind kein Umzug
        for status in (403, 429, None):
            up = FakeUploader("https://dev.spooly.eu/api", {"https://dev.spooly.eu/api": (status, None)})
            with mock.patch("spooly_bridge.__main__._install_metadaten_ermitteln", return_value={}):
                self.assertFalse(_sende_heartbeat(FakePoller(), up, LOG, self.pfad))
            self.assertEqual(up.aufrufe, ["https://dev.spooly.eu/api"])
        self.assertEqual(lade_config(self.pfad).spooly_url, "https://dev.spooly.eu/api")

    def test_eigene_url_fragt_nirgends_sonst_nach(self):
        up = FakeUploader("https://spooly.meinserver.de/api", {"https://spooly.meinserver.de/api": (401, None)})
        self.assertIsNone(_spooly_umzug(up, lambda: up.heartbeat(), self.pfad, LOG))
        self.assertEqual(up.aufrufe, [])


if __name__ == "__main__":
    unittest.main()
