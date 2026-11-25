"""Mehrfarb-Listen in den rohen Metadaten werden nicht mehr bei 500 Zeichen abgeschnitten."""

import unittest

from spooly_bridge.__main__ import _roh_metadaten


class TestRohMetadaten(unittest.TestCase):
    def test_filament_liste_bleibt_bis_4000(self):
        farben = ";".join(["#AABBCC"] * 300)  # gut 2000 Zeichen
        raw = _roh_metadaten({"filament_colour": farben, "filament_type": "PLA;" * 100})
        self.assertEqual(raw["filament_colour"], farben)
        self.assertEqual(raw["filament_type"], "PLA;" * 100)

    def test_filament_ueber_4000_wird_gekuerzt(self):
        raw = _roh_metadaten({"filament_weight": "1;" * 3000})
        self.assertEqual(len(raw["filament_weight"]), 4000 + len("...[truncated]"))

    def test_andere_texte_bei_500(self):
        raw = _roh_metadaten({"slicer_version": "x" * 900, "kurz": "ok"})
        self.assertEqual(raw["slicer_version"], "x" * 500 + "...[truncated]")
        self.assertEqual(raw["kurz"], "ok")

    def test_thumbnails_nur_als_zahl(self):
        raw = _roh_metadaten({"thumbnails": [{"data": "AAAA"}, {"data": "BBBB"}]})
        self.assertEqual(raw["thumbnails"], "[2 thumbnails]")


if __name__ == "__main__":
    unittest.main()
