"""Tests fuer den Live-Stand: Takt, Push-Body, 404-Pause, next_seconds, Vorschaubild."""

import json
import logging
import os
import unittest
from unittest import mock

from spooly_bridge.__main__ import (
    LiveTakt,
    _next_seconds,
)
from spooly_bridge.moonraker import MoonrakerPoller
from spooly_bridge.uploader import SpoolyUploader

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "live_push_u1.json")


def _status(zustand="printing", kopf="extruder", datei="benchy.gcode"):
    return {
        "print_stats": {"state": zustand, "filename": datei},
        "toolhead": {"extruder": kopf},
    }


class Uhr:
    def __init__(self):
        self.jetzt = 1000.0

    def __call__(self):
        return self.jetzt

    def weiter(self, sekunden):
        self.jetzt += sekunden


class TaktBasis(unittest.TestCase):
    def setUp(self):
        self.uhr = Uhr()
        self.poller = mock.Mock()
        self.poller.stand.return_value = _status()
        self.poller.datei_metadaten.return_value = {}
        self.uploader = mock.Mock()
        self.uploader.letzter_status = 200
        self.uploader.live_senden.return_value = {"success": True, "next_seconds": 30}
        self.takt = LiveTakt(self.poller, self.uploader, logging.getLogger("test"), uhr=self.uhr)

    def lauf(self, sekunden, schritt=1):
        """Die Schleife tickt jede Sekunde."""
        for _ in range(sekunden):
            self.uhr.weiter(schritt)
            self.takt.tick()

    @property
    def gesendet(self):
        return self.uploader.live_senden.call_count


class TestTakt(TaktBasis):
    def test_nach_dem_start_einmal_senden(self):
        self.takt.tick()
        self.assertEqual(self.gesendet, 1)

    def test_druck_liest_alle_5_sekunden(self):
        self.takt.tick()
        self.lauf(20)
        # 1 Abfrage beim Start, danach alle 5 s
        self.assertEqual(self.poller.stand.call_count, 1 + 4)

    def test_druck_sendet_im_takt_von_next_seconds(self):
        self.uploader.live_senden.return_value = {"success": True, "next_seconds": 45}
        self.takt.tick()
        self.lauf(40)
        self.assertEqual(self.gesendet, 1)
        self.lauf(10)
        self.assertEqual(self.gesendet, 2)

    def test_kopfwechsel_sendet_sofort(self):
        self.takt.tick()
        self.poller.stand.return_value = _status(kopf="extruder2")
        self.lauf(5)
        self.assertEqual(self.gesendet, 2)

    def test_dateiwechsel_sendet_sofort(self):
        self.takt.tick()
        self.poller.stand.return_value = _status(datei="anderes.gcode")
        self.lauf(5)
        self.assertEqual(self.gesendet, 2)

    def test_zustandswechsel_sendet_sofort(self):
        self.takt.tick()
        self.poller.stand.return_value = _status(zustand="paused")
        self.lauf(5)
        self.assertEqual(self.gesendet, 2)

    def test_ruhe_liest_alle_30_sekunden_und_sendet_nicht(self):
        self.poller.stand.return_value = _status(zustand="standby", datei="")
        self.takt.tick()
        self.lauf(120)
        self.assertEqual(self.gesendet, 1)
        self.assertEqual(self.poller.stand.call_count, 1 + 4)

    def test_ende_des_drucks_wird_noch_gemeldet(self):
        self.takt.tick()
        self.poller.stand.return_value = _status(zustand="complete")
        self.lauf(5)
        self.assertEqual(self.gesendet, 2)
        self.lauf(100)
        self.assertEqual(self.gesendet, 2)

    def test_ohne_moonraker_wird_nichts_gesendet(self):
        self.poller.stand.return_value = None
        self.lauf(60)
        self.assertEqual(self.gesendet, 0)

    def test_fehler_im_takt_wirft_nicht(self):
        self.poller.stand.side_effect = RuntimeError("kaputt")
        self.takt.tick()
        self.assertEqual(self.gesendet, 0)

    def test_fehlschlag_wird_nach_30_sekunden_wiederholt(self):
        self.uploader.live_senden.return_value = None
        self.takt.tick()
        self.assertEqual(self.gesendet, 1)
        self.lauf(25)
        self.assertEqual(self.gesendet, 1)
        self.uploader.live_senden.return_value = {"success": True, "next_seconds": 30}
        self.lauf(10)
        self.assertEqual(self.gesendet, 2)

    def test_404_pausiert_30_minuten(self):
        self.uploader.live_senden.return_value = None
        self.uploader.letzter_status = 404
        self.takt.tick()
        self.assertEqual(self.gesendet, 1)
        self.lauf(29 * 60)
        self.assertEqual(self.gesendet, 1)
        self.lauf(2 * 60)
        self.assertEqual(self.gesendet, 2)


class TestNextSeconds(unittest.TestCase):
    def test_klemmt_auf_10_bis_300(self):
        self.assertEqual(_next_seconds({"next_seconds": 1}), 10)
        self.assertEqual(_next_seconds({"next_seconds": 9999}), 300)
        self.assertEqual(_next_seconds({"next_seconds": 60}), 60)

    def test_standard_30(self):
        self.assertEqual(_next_seconds({}), 30)
        self.assertEqual(_next_seconds({"next_seconds": "bald"}), 30)
        self.assertEqual(_next_seconds({"next_seconds": None}), 30)


class TestDateiInfos(TaktBasis):
    def test_infos_einmal_je_datei_und_bild_nur_beim_ersten_push(self):
        self.poller.datei_metadaten.return_value = {"estimated_time": 3600}
        self.poller.thumbnail_laden.return_value = "data:image/png;base64,AAAA"
        self.takt.tick()
        self.lauf(40)
        self.assertEqual(self.poller.datei_metadaten.call_count, 1)
        self.poller.datei_metadaten.assert_called_with("benchy.gcode", sperren=False)
        erster, zweiter = self.uploader.live_senden.call_args_list[:2]
        self.assertEqual(erster[1]["cover"], "data:image/png;base64,AAAA")
        self.assertEqual(erster[1]["estimated_time"], 3600)
        self.assertIsNone(zweiter[1]["cover"])
        self.assertEqual(zweiter[1]["estimated_time"], 3600)

    def test_bild_bleibt_offen_wenn_der_push_scheitert(self):
        self.poller.datei_metadaten.return_value = {"estimated_time": 60}
        self.poller.thumbnail_laden.return_value = "data:image/png;base64,AAAA"
        self.uploader.live_senden.return_value = None
        self.takt.tick()
        self.uploader.live_senden.return_value = {"success": True, "next_seconds": 30}
        self.lauf(35)
        self.assertEqual(self.uploader.live_senden.call_args[1]["cover"], "data:image/png;base64,AAAA")

    def test_zu_grosses_bild_wird_nicht_geschickt(self):
        self.poller.datei_metadaten.return_value = {"estimated_time": 60}
        self.poller.thumbnail_laden.return_value = "data:image/png;base64," + "A" * (150 * 1024)
        self.takt.tick()
        self.assertIsNone(self.uploader.live_senden.call_args[1]["cover"])

    def test_inline_bild_ohne_praefix_wird_zur_data_uri(self):
        self.poller.datei_metadaten.return_value = {"estimated_time": 60}
        self.poller.thumbnail_laden.return_value = "AAAA"
        self.takt.tick()
        self.assertEqual(self.uploader.live_senden.call_args[1]["cover"], "data:image/png;base64,AAAA")

    def test_bild_wird_mit_max_breite_400_angefragt(self):
        self.poller.datei_metadaten.return_value = {"estimated_time": 60}
        self.poller.thumbnail_laden.return_value = None
        self.takt.tick()
        self.assertEqual(self.poller.thumbnail_laden.call_args[1], {"max_breite": 400})

    def test_ohne_metadaten_wird_es_spaeter_nochmal_versucht(self):
        self.takt.tick()
        self.lauf(100)
        self.assertEqual(self.poller.datei_metadaten.call_count, 1)
        self.lauf(300)
        self.assertEqual(self.poller.datei_metadaten.call_count, 2)


class TestThumbnailAuswahl(unittest.TestCase):
    def test_groesstes_bild_bis_400_breit(self):
        poller = MoonrakerPoller("http://localhost:7125")
        meta = {"thumbnails": [
            {"width": 32, "height": 32, "data": "klein"},
            {"width": 300, "height": 300, "data": "mittel"},
            {"width": 600, "height": 600, "data": "gross"},
        ]}
        self.assertEqual(poller.thumbnail_laden(meta, max_breite=400), "mittel")
        self.assertEqual(poller.thumbnail_laden(meta), "gross")

    def test_nur_zu_grosse_bilder_ergeben_nichts(self):
        poller = MoonrakerPoller("http://localhost:7125")
        meta = {"thumbnails": [{"width": 600, "height": 600, "data": "gross"}]}
        self.assertIsNone(poller.thumbnail_laden(meta, max_breite=400))


class TestPushBody(unittest.TestCase):
    def test_body_entspricht_der_fixture(self):
        with open(FIXTURE, encoding="utf-8") as f:
            fixture = json.load(f)
        erwartet = {k: v for k, v in fixture.items() if not k.startswith("_")}

        # Moonraker liefert result.status, der Rest ist Spoolys Sache
        moonraker = MoonrakerPoller("http://localhost:7125")
        antwort = mock.MagicMock()
        antwort.read.return_value = json.dumps({"result": {"status": fixture["status"]}}).encode()
        antwort.status = 200
        antwort.__enter__.return_value = antwort
        antwort.__exit__.return_value = False
        with mock.patch("spooly_bridge.moonraker.urlopen", return_value=antwort) as mu:
            status = moonraker.stand()
        url = mu.call_args[0][0].full_url
        self.assertIn("/printer/objects/query?print_task_config&print_stats&virtual_sdcard"
                      "&toolhead=extruder&display_status", url)

        poller = mock.Mock()
        poller.stand.return_value = status
        poller.datei_metadaten.return_value = {"estimated_time": 3600}
        poller.thumbnail_laden.return_value = None
        uploader = SpoolyUploader("https://spooly.eu/api", erwartet["bridge_api_key"])
        takt = LiveTakt(poller, uploader, logging.getLogger("test"), uhr=Uhr())

        antwort_spooly = mock.MagicMock()
        antwort_spooly.read.return_value = b'{"success": true, "next_seconds": 30}'
        antwort_spooly.status = 200
        antwort_spooly.__enter__.return_value = antwort_spooly
        antwort_spooly.__exit__.return_value = False
        with mock.patch("spooly_bridge.uploader.urlopen", return_value=antwort_spooly) as up:
            takt.tick()
        anfrage = up.call_args[0][0]
        self.assertTrue(anfrage.full_url.endswith("/klipper/push/live"))
        self.assertEqual(anfrage.get_header("X-spooly-bridge-key"), erwartet["bridge_api_key"])
        self.assertEqual(json.loads(anfrage.data), erwartet)


if __name__ == "__main__":
    unittest.main()
