"""
Moonraker WebSocket-Listener fuer Echtzeit-Events.

Verbindet sich mit dem Moonraker WebSocket und reagiert sofort
auf abgeschlossene Druckjobs — kein Polling noetig.

Fallback: Wenn der WebSocket nicht verfuegbar ist, wird auf
intervallbasiertes Polling zurueckgefallen.

Sicherheit:
- Nur lokale Verbindung (localhost)
- Nur lesend (subscribe auf Events, keine Befehle)
- Kein Code wird ausgefuehrt
"""

import json
import logging
import socket
import time
import hashlib
import struct
import os
from typing import Optional, Callable
from urllib.parse import urlparse

log = logging.getLogger("spooly-bridge")

# Moonraker Events die uns interessieren
RELEVANTE_EVENTS = {"notify_history_changed"}


class MoonrakerWebSocket:
    """
    Minimaler WebSocket-Client fuer Moonraker Events.

    Nutzt nur die Python-Standardbibliothek (kein websocket-client o.ae.),
    damit keine externen Abhaengigkeiten noetig sind.
    """

    def __init__(self, moonraker_url: str):
        parsed = urlparse(moonraker_url)
        self.host = parsed.hostname or "localhost"
        self.port = parsed.port or 7125
        self.sock: Optional[socket.socket] = None
        self.verbunden = False

    def verbinden(self) -> bool:
        """WebSocket-Verbindung aufbauen (HTTP Upgrade Handshake)."""
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=10)
            self.sock.settimeout(1.0)  # Non-blocking reads mit 1s Timeout

            # WebSocket Handshake (RFC 6455)
            ws_key = os.urandom(16)
            import base64
            key_b64 = base64.b64encode(ws_key).decode()

            handshake = (
                f"GET /websocket HTTP/1.1\r\n"
                f"Host: {self.host}:{self.port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key_b64}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"\r\n"
            )
            self.sock.sendall(handshake.encode())

            # Antwort lesen (HTTP 101 Switching Protocols)
            antwort = b""
            while b"\r\n\r\n" not in antwort:
                teil = self.sock.recv(4096)
                if not teil:
                    break
                antwort += teil

            if b"101" not in antwort:
                log.debug("WebSocket Handshake fehlgeschlagen")
                self.trennen()
                return False

            self.verbunden = True

            # Moonraker Events abonnieren
            self._sende_json({
                "jsonrpc": "2.0",
                "method": "server.connection.identify",
                "params": {
                    "client_name": "spooly-bridge",
                    "version": "1.2.0",
                    "type": "other",
                },
                "id": 1,
            })

            log.info("WebSocket verbunden mit %s:%d", self.host, self.port)
            return True

        except (socket.error, OSError) as fehler:
            log.debug("WebSocket Verbindung fehlgeschlagen: %s", fehler)
            self.trennen()
            return False

    def trennen(self):
        """Verbindung schliessen."""
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None
        self.verbunden = False

    def _sende_json(self, daten: dict):
        """JSON-Nachricht ueber WebSocket senden."""
        if not self.sock:
            return
        payload = json.dumps(daten).encode("utf-8")
        # WebSocket Frame: FIN + Text opcode, masked
        laenge = len(payload)
        mask_key = os.urandom(4)

        frame = bytearray()
        frame.append(0x81)  # FIN + Text

        if laenge < 126:
            frame.append(0x80 | laenge)  # MASK bit + length
        elif laenge < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack(">H", laenge))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack(">Q", laenge))

        frame.extend(mask_key)
        # Payload maskieren (RFC 6455)
        for i, byte in enumerate(payload):
            frame.append(byte ^ mask_key[i % 4])

        try:
            self.sock.sendall(frame)
        except (socket.error, OSError):
            self.verbunden = False

    def events_lesen(self, timeout: float = 1.0) -> list:
        """
        Eingehende Events lesen (nicht-blockierend).
        Gibt eine Liste von Event-Dicts zurueck.
        """
        if not self.sock or not self.verbunden:
            return []

        events = []
        try:
            daten = self.sock.recv(65536)
            if not daten:
                self.verbunden = False
                return []

            # WebSocket Frames parsen (vereinfacht — Moonraker sendet unmaskiert)
            pos = 0
            while pos < len(daten):
                if pos + 2 > len(daten):
                    break
                opcode = daten[pos] & 0x0F
                masked = bool(daten[pos + 1] & 0x80)
                laenge = daten[pos + 1] & 0x7F
                pos += 2

                if laenge == 126:
                    if pos + 2 > len(daten):
                        break
                    laenge = struct.unpack(">H", daten[pos:pos + 2])[0]
                    pos += 2
                elif laenge == 127:
                    if pos + 8 > len(daten):
                        break
                    laenge = struct.unpack(">Q", daten[pos:pos + 8])[0]
                    pos += 8

                if masked:
                    pos += 4  # Mask key ueberspringen

                if pos + laenge > len(daten):
                    break

                payload = daten[pos:pos + laenge]
                pos += laenge

                if opcode == 0x01:  # Text frame
                    try:
                        nachricht = json.loads(payload)
                        method = nachricht.get("method", "")
                        if method in RELEVANTE_EVENTS:
                            events.append(nachricht)
                    except (json.JSONDecodeError, ValueError):
                        pass
                elif opcode == 0x08:  # Close frame
                    self.verbunden = False
                    return events
                elif opcode == 0x09:  # Ping — mit Pong antworten
                    pong = bytearray([0x8A, 0x80, 0, 0, 0, 0])  # Pong, masked, empty
                    try:
                        self.sock.sendall(pong)
                    except Exception:
                        pass

        except socket.timeout:
            pass  # Normal — keine neuen Daten
        except (socket.error, OSError):
            self.verbunden = False

        return events

    def ist_job_fertig_event(self, event: dict) -> bool:
        """Prueft ob ein Event ein abgeschlossener Druckjob ist."""
        params = event.get("params", [])
        if not params:
            return False
        # notify_history_changed sendet: {"action": "finished", "job": {...}}
        if isinstance(params, list) and len(params) > 0:
            aktion = params[0] if isinstance(params[0], dict) else {}
            return aktion.get("action") in ("finished",)
        return False
