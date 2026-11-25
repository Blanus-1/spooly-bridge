"""Tests fuer den WLAN-Abgleich auf dem Snapmaker U1.

Hintergrund (Firmware 1.5.1 und 2.0.0 disassembliert, 21.09.2026): Die
Oberflaeche startet das WLAN mit /etc/wpa_supplicant.conf, sobald /oem/.debug
beim Boot existiert, sonst mit ihrer eigenen Datei unter printer_data/gui.
Die Bridge legt /oem/.debug fuer den Autostart an - ohne Abgleich steht in /etc
nach dem ersten Neustart nur die Werks-Vorlage, und das WLAN ist weg.
"""

import os
import tempfile
import unittest

from unittest import mock

from spooly_bridge.__main__ import _wlan_abgleichen, _wlan_aktive_config, _wlan_config_spiegeln

VORLAGE = "ctrl_interface=/var/run/wpa_supplicant\nap_scan=1\nupdate_config=1\n"
MIT_NETZ = VORLAGE + '\nnetwork={\n\tssid="Werkstatt"\n\tpsk="geheim"\n}\n'


class TestAktiveConfig(unittest.TestCase):
    def setUp(self):
        self.proc = tempfile.mkdtemp()

    def _prozess(self, pid, *argv):
        os.makedirs(os.path.join(self.proc, pid))
        with open(os.path.join(self.proc, pid, "cmdline"), "wb") as f:
            f.write(b"\0".join(a.encode() for a in argv) + b"\0")

    def test_liest_config_der_rk_wifi_instanz(self):
        # So startet librkwifibt: "wpa_supplicant -B -i wlan0 -c  %s -ddd"
        self._prozess("812", "wpa_supplicant", "-B", "-i", "wlan0", "-c", "/tmp/gui.conf", "-ddd")
        self._prozess("1", "init")
        self.assertEqual(_wlan_aktive_config(self.proc), os.path.realpath("/tmp/gui.conf"))

    def test_kein_wpa_supplicant(self):
        self._prozess("1", "init")
        self.assertIsNone(_wlan_aktive_config(self.proc))

    def test_zwei_instanzen_mit_verschiedenen_configs_bleibt_offen(self):
        self._prozess("10", "/usr/sbin/wpa_supplicant", "-c", "/tmp/a.conf")
        self._prozess("11", "wpa_supplicant", "-c", "/tmp/b.conf")
        self.assertIsNone(_wlan_aktive_config(self.proc))


class TestSpiegeln(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.gui = os.path.join(tmp, "gui.conf")
        self.etc = os.path.join(tmp, "etc.conf")

    def _schreiben(self, pfad, inhalt, modus=0o644):
        with open(pfad, "w") as f:
            f.write(inhalt)
        os.chmod(pfad, modus)

    def _lesen(self, pfad):
        with open(pfad) as f:
            return f.read()

    def test_installations_sitzung_rettet_netz_nach_etc(self):
        # Der gemeldete Fehler: Netz nur in der GUI-Datei
        self._schreiben(self.gui, MIT_NETZ)
        self._schreiben(self.etc, VORLAGE)
        self.assertTrue(_wlan_config_spiegeln(self.gui, (self.gui, self.etc)))
        self.assertEqual(self._lesen(self.etc), MIT_NETZ)

    def test_debug_sitzung_haelt_gui_datei_aktuell(self):
        # Neues Netz im Debug-Modus landet in /etc; ohne Abgleich waere es nach
        # einem Firmware-Update (loescht /oem/.debug) wieder weg
        self._schreiben(self.gui, VORLAGE, 0o600)
        self._schreiben(self.etc, MIT_NETZ)
        self.assertTrue(_wlan_config_spiegeln(self.etc, (self.gui, self.etc)))
        self.assertEqual(self._lesen(self.gui), MIT_NETZ)
        self.assertEqual(os.stat(self.gui).st_mode & 0o777, 0o600)

    def test_leere_vorlage_ueberschreibt_nie_ein_netz(self):
        # Boot-Moment: ifup-Instanz laeuft noch mit der frischen Vorlage in /etc
        self._schreiben(self.gui, MIT_NETZ)
        self._schreiben(self.etc, VORLAGE)
        self.assertFalse(_wlan_config_spiegeln(self.etc, (self.gui, self.etc)))
        self.assertEqual(self._lesen(self.gui), MIT_NETZ)

    def test_auskommentiertes_beispiel_zaehlt_nicht_als_netz(self):
        self._schreiben(self.gui, MIT_NETZ)
        self._schreiben(self.etc, VORLAGE + "#network={\n#\tssid=\"beispiel\"\n#}\n")
        self.assertFalse(_wlan_config_spiegeln(self.etc, (self.gui, self.etc)))
        self.assertEqual(self._lesen(self.gui), MIT_NETZ)

    def test_gleicher_inhalt_schreibt_nicht(self):
        self._schreiben(self.gui, MIT_NETZ)
        self._schreiben(self.etc, MIT_NETZ)
        self.assertFalse(_wlan_config_spiegeln(self.gui, (self.gui, self.etc)))

    def test_fremde_oder_unbekannte_config_wird_ignoriert(self):
        self._schreiben(self.gui, MIT_NETZ)
        self._schreiben(self.etc, VORLAGE)
        self.assertFalse(_wlan_config_spiegeln(None, (self.gui, self.etc)))
        self.assertFalse(_wlan_config_spiegeln("/tmp/fremd.conf", (self.gui, self.etc)))
        self.assertEqual(self._lesen(self.etc), VORLAGE)

    def test_bytes_ausserhalb_von_utf8_werden_exakt_kopiert(self):
        roh = VORLAGE.encode() + b'network={\n\tssid="Caf\xe9"\n\tpsk="\xff\xfe"\n}\n'
        with open(self.gui, "wb") as f:
            f.write(roh)
        self._schreiben(self.etc, VORLAGE)
        self.assertTrue(_wlan_config_spiegeln(self.gui, (self.gui, self.etc)))
        with open(self.etc, "rb") as f:
            self.assertEqual(f.read(), roh)

    def test_keine_tmp_datei_bleibt_liegen(self):
        self._schreiben(self.gui, MIT_NETZ)
        self._schreiben(self.etc, VORLAGE)
        _wlan_config_spiegeln(self.gui, (self.gui, self.etc))
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.gui))), ["etc.conf", "gui.conf"])

    def test_fehlendes_ziel_wird_nicht_angelegt(self):
        self._schreiben(self.etc, MIT_NETZ)
        self.assertFalse(_wlan_config_spiegeln(self.etc, (self.gui, self.etc)))
        self.assertFalse(os.path.exists(self.gui))


class TestAbgleichStopptBridgeNie(unittest.TestCase):
    def test_unerwarteter_fehler_wird_geschluckt(self):
        log = mock.Mock()
        with mock.patch("spooly_bridge.__main__._wlan_aktive_config", side_effect=ValueError("kaputt")):
            self.assertFalse(_wlan_abgleichen(log))
        log.debug.assert_called_once()


if __name__ == "__main__":
    unittest.main()
