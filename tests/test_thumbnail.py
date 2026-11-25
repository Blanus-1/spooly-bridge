"""Tests fuer das Laden von Job-Thumbnails ueber die Moonraker-File-API.

Der Pfad aus den Metadaten landet direkt in der URL. Ohne Kodierung
scheiterte urlopen bei jedem Dateinamen mit Leerzeichen an
"URL can't contain control characters" - auf Svens U1 blieben dadurch
23 von 29 importierten Jobs ohne Vorschaubild (06.09.2026).
"""

import base64
import unittest
from unittest import mock

from spooly_bridge.moonraker import MoonrakerPoller


def _antwort(bild_bytes):
    """Kontext-Manager-Attrappe fuer urlopen."""
    antwort = mock.MagicMock()
    antwort.read.return_value = bild_bytes
    antwort.__enter__.return_value = antwort
    antwort.__exit__.return_value = False
    return antwort


class TestThumbnailUrlKodierung(unittest.TestCase):
    def setUp(self):
        self.poller = MoonrakerPoller("http://localhost:7125")
        self.bild = b"\x89PNG\r\n\x1a\n" + b"x" * 200

    def _lade(self, relativer_pfad):
        metadaten = {
            "thumbnails": [
                {"width": 300, "height": 300, "relative_path": relativer_pfad}
            ]
        }
        with mock.patch(
            "spooly_bridge.moonraker.urlopen", return_value=_antwort(self.bild)
        ) as urlopen:
            ergebnis = self.poller.thumbnail_laden(metadaten)
        return ergebnis, urlopen.call_args[0][0].full_url

    def test_leerzeichen_wird_kodiert(self):
        """Der Fall, der auf echter Hardware fehlschlug."""
        ergebnis, url = self._lade(".thumbs/Flat Hinge Model 3_PLA-300x300.png")
        self.assertNotIn(" ", url)
        self.assertIn("Flat%20Hinge%20Model%203", url)
        self.assertTrue(ergebnis.startswith("data:image/png;base64,"))
        self.assertEqual(
            ergebnis, "data:image/png;base64," + base64.b64encode(self.bild).decode()
        )

    def test_umlaut_wird_kodiert(self):
        _, url = self._lade(".thumbs/Schiebetuer_Fuehrung_aeoeue.png")
        self.assertNotIn(" ", url)
        _, url_umlaut = self._lade(".thumbs/Kueche_\u00fcber.png")
        self.assertIn("%C3%BCber", url_umlaut)

    def test_schraegstrich_bleibt_trennzeichen(self):
        """quote darf / nicht kodieren, sonst zerfaellt der Pfad."""
        _, url = self._lade(".thumbs/unterordner/bild.png")
        self.assertIn("/gcodes/.thumbs/unterordner/bild.png", url)
        self.assertNotIn("%2F", url)

    def test_inline_base64_geht_ohne_http(self):
        """Hat das Thumbnail data, wird gar keine URL gebaut."""
        metadaten = {"thumbnails": [{"width": 300, "height": 300, "data": "AAAA"}]}
        with mock.patch("spooly_bridge.moonraker.urlopen") as urlopen:
            self.assertEqual(self.poller.thumbnail_laden(metadaten), "AAAA")
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
