"""Tests fuer die WebSocket-Reconnect Backoff-Logik."""

import unittest

from spooly_bridge.__main__ import _naechster_backoff_delay, RECONNECT_START_DELAY


class TestBackoffDelay(unittest.TestCase):
    def test_verdoppelt_den_delay(self):
        self.assertEqual(_naechster_backoff_delay(5), 10)
        self.assertEqual(_naechster_backoff_delay(10), 20)
        self.assertEqual(_naechster_backoff_delay(20), 40)
        self.assertEqual(_naechster_backoff_delay(40), 80)

    def test_deckelt_bei_max_delay(self):
        # 80 * 2 = 160, aber das Maximum liegt bei 120 Sekunden
        self.assertEqual(_naechster_backoff_delay(80), 120)

    def test_bleibt_am_maximum(self):
        self.assertEqual(_naechster_backoff_delay(120), 120)

    def test_null_bleibt_null(self):
        # 0 ist der "verbunden"-Sentinel und darf nie zu einem positiven
        # Delay hochwachsen — sonst wuerde der Reconnect-Zustand nie starten.
        self.assertEqual(_naechster_backoff_delay(0), 0)

    def test_reale_sequenz_ab_start(self):
        # Spiegelt die tatsaechliche Progression der Reconnect-Schleife:
        # ab RECONNECT_START_DELAY wiederholt verdoppeln bis zum Cap.
        delays = []
        d = RECONNECT_START_DELAY
        for _ in range(7):
            delays.append(d)
            d = _naechster_backoff_delay(d)
        self.assertEqual(delays, [5, 10, 20, 40, 80, 120, 120])


if __name__ == "__main__":
    unittest.main()
