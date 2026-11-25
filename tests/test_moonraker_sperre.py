"""Nur ein 404 sperrt Metadaten und Spoolman, ein Netzfehler nicht.

Vorher reichte ein einziges None (auch ein Timeout), und beides blieb bis
zum Neustart der Bridge aus.
"""

import io
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

from spooly_bridge.moonraker import MoonrakerPoller


def _http(code):
    return HTTPError("http://x", code, "fehler", {}, io.BytesIO(b""))


def _antwort(daten: bytes):
    antwort = mock.MagicMock()
    antwort.read.return_value = daten
    antwort.status = 200
    antwort.__enter__.return_value = antwort
    antwort.__exit__.return_value = False
    return antwort


class TestSperreNurBei404(unittest.TestCase):
    def setUp(self):
        self.poller = MoonrakerPoller("http://localhost:7125")

    def _lauf(self, aufruf, *antworten):
        with mock.patch("spooly_bridge.moonraker.urlopen", side_effect=list(antworten)) as u:
            ergebnis = [aufruf() for _ in antworten]
        return ergebnis, u.call_count

    def test_metadaten_netzfehler_wird_wiederholt(self):
        ergebnis, aufrufe = self._lauf(
            lambda: self.poller.datei_metadaten("a.gcode"),
            URLError("timeout"),
            _antwort(b'{"result": {"layer_height": 0.2}}'),
        )
        self.assertEqual(ergebnis, [{}, {"layer_height": 0.2}])
        self.assertEqual(aufrufe, 2)

    def test_metadaten_404_sperrt(self):
        _, aufrufe = self._lauf(lambda: self.poller.datei_metadaten("a.gcode"), _http(404))
        with mock.patch("spooly_bridge.moonraker.urlopen") as u:
            self.assertEqual(self.poller.datei_metadaten("b.gcode"), {})
        u.assert_not_called()
        self.assertEqual(aufrufe, 1)

    def test_spoolman_netzfehler_wird_wiederholt(self):
        ergebnis, aufrufe = self._lauf(
            self.poller.spoolman_spool,
            OSError("weg"),
            _antwort(b'{"result": {"spool_id": 7}}'),
        )
        self.assertEqual(ergebnis, [None, {"spool_id": 7}])
        self.assertEqual(aufrufe, 2)

    def test_spoolman_404_sperrt(self):
        self._lauf(self.poller.spoolman_spool, _http(404))
        with mock.patch("spooly_bridge.moonraker.urlopen") as u:
            self.assertIsNone(self.poller.spoolman_spool())
        u.assert_not_called()

    def test_server_fehler_sperrt_nicht(self):
        self._lauf(lambda: self.poller.datei_metadaten("a.gcode"), _http(500))
        with mock.patch(
            "spooly_bridge.moonraker.urlopen",
            return_value=_antwort(b'{"result": {"layer_height": 0.3}}'),
        ):
            self.assertEqual(self.poller.datei_metadaten("a.gcode"), {"layer_height": 0.3})


if __name__ == "__main__":
    unittest.main()
