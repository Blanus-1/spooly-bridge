"""Tests fuer lan_ip und ssh_server im Heartbeat."""

import os
import tempfile
import unittest
from unittest import mock

from spooly_bridge.__main__ import _lan_ip, _ssh_server_info


class TestLanIp(unittest.TestCase):
    def test_fehler_ergibt_none(self):
        with mock.patch("spooly_bridge.__main__.socket.socket", side_effect=OSError("kein Netz")):
            self.assertIsNone(_lan_ip())

    def test_connect_fehler_ergibt_none(self):
        sock = mock.Mock()
        sock.connect.side_effect = OSError("unreachable")
        with mock.patch("spooly_bridge.__main__.socket.socket", return_value=sock):
            self.assertIsNone(_lan_ip())
        sock.close.assert_called_once()

    def test_liefert_die_adresse(self):
        sock = mock.Mock()
        sock.getsockname.return_value = ("192.168.1.50", 41234)
        with mock.patch("spooly_bridge.__main__.socket.socket", return_value=sock):
            self.assertEqual(_lan_ip(), "192.168.1.50")
        sock.connect.assert_called_once_with(("8.8.8.8", 80))


class TestSshServer(unittest.TestCase):
    def test_host_keys_und_version(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("dropbear_ed25519_host_key", "dropbear_rsa_host_key", "authorized_keys"):
                open(os.path.join(d, name), "w").close()
            lauf = mock.Mock(stderr=b"Dropbear v2022.83\nzweite Zeile", stdout=b"")
            with mock.patch("spooly_bridge.__main__.shutil.which", return_value="/usr/sbin/dropbear"), \
                    mock.patch("spooly_bridge.__main__.subprocess.run", return_value=lauf):
                info = _ssh_server_info(d)
        self.assertEqual(info, {"ssh_server": {
            "host_keys": ["dropbear_ed25519_host_key", "dropbear_rsa_host_key"],
            "version": "Dropbear v2022.83",
        }})

    def test_ohne_dropbear_nichts(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("spooly_bridge.__main__.shutil.which", return_value=None):
                self.assertIsNone(_ssh_server_info(os.path.join(d, "gibt-es-nicht")))

    def test_fehler_beim_aufruf_bleibt_still(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("spooly_bridge.__main__.shutil.which", return_value="/x/dropbear"), \
                    mock.patch("spooly_bridge.__main__.subprocess.run", side_effect=OSError("nein")):
                self.assertIsNone(_ssh_server_info(d))


if __name__ == "__main__":
    unittest.main()
