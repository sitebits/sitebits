"""Kleiner SMTP-Server für Tests (nur Standardbibliothek, Loopback, ohne TLS).

Nimmt EHLO/HELO, MAIL, RCPT, DATA, RSET, NOOP, QUIT an und legt jede angenommene Nachricht mit
Envelope in `empfangen` ab. `stoerung`: None | "421" (Verbindung sofort abgewiesen, vorübergehend)
| "550" (Empfänger dauerhaft abgelehnt) | "nach_data" (Verbindung nach den Daten getrennt, ohne
Antwort)."""

from __future__ import annotations

import socketserver
import threading
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import SMTP
from typing import Any


@dataclass
class Empfangen:
    absender: str
    empfaenger: list[str]
    roh: bytes

    @property
    def nachricht(self) -> EmailMessage:
        m: Any = message_from_bytes(self.roh, policy=SMTP)
        return m  # type: ignore[no-any-return]


@dataclass
class Zustand:
    empfangen: list[Empfangen] = field(default_factory=list)
    stoerung: str | None = None
    verbindungen: int = 0


class _Handler(socketserver.StreamRequestHandler):
    server: _Server

    def _sag(self, zeile: str) -> None:
        self.wfile.write(zeile.encode() + b"\r\n")
        self.wfile.flush()

    def handle(self) -> None:
        z = self.server.zustand
        z.verbindungen += 1
        if z.stoerung == "421":
            self._sag("421 vorübergehend nicht verfügbar".encode("ascii", "replace").decode())
            return
        self._sag("220 test ESMTP")
        absender, empfaenger = "", []
        while True:
            zeile = self.rfile.readline()
            if not zeile:
                return
            befehl = zeile.decode("ascii", "replace").strip()
            gross = befehl.upper()
            if gross.startswith(("EHLO", "HELO")):
                self._sag("250-test")
                self._sag("250 8BITMIME")
            elif gross.startswith("MAIL FROM:"):
                absender, empfaenger = befehl[10:].strip("<> "), []
                self._sag("250 ok")
            elif gross.startswith("RCPT TO:"):
                if z.stoerung == "550":
                    self._sag("550 unbekannt")
                    continue
                empfaenger.append(befehl[8:].strip("<> "))
                self._sag("250 ok")
            elif gross == "DATA":
                self._sag("354 weiter")
                daten = b""
                while True:
                    z2 = self.rfile.readline()
                    if z2 in (b".\r\n", b".\n", b""):
                        break
                    daten += z2[1:] if z2.startswith(b"..") else z2
                if z.stoerung == "nach_data":
                    return
                z.empfangen.append(Empfangen(absender, empfaenger, daten))
                self._sag("250 angenommen")
            elif gross in ("RSET", "NOOP"):
                self._sag("250 ok")
            elif gross == "QUIT":
                self._sag("221 tschüss".encode("ascii", "replace").decode())
                return
            else:
                self._sag("502 nicht unterstützt")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    zustand: Zustand


class SmtpFake:
    def __init__(self) -> None:
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.zustand = Zustand()
        self._t = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def zustand(self) -> Zustand:
        return self._server.zustand

    def __enter__(self) -> SmtpFake:
        self._t.start()
        return self

    def __exit__(self, *_a: object) -> None:
        self._server.shutdown()
        self._server.server_close()
