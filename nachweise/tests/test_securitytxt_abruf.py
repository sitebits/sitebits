"""Abruf der security.txt (`securitytxt.abrufen`): Status, Content-Type, Größe, Weiterleitung.
Ohne Netz und ohne gpg: ein Attrappen-Öffner bzw. ein lokaler HTTP-Server."""

from __future__ import annotations

import http.server
import threading
import urllib.error
import urllib.request
from email.message import Message

import pytest

from scanview_nachweise import securitytxt
from scanview_nachweise.fehler import NachweisFehler

URL = "https://scanview.example.invalid/.well-known/security.txt"


class _Antwort:
    """Minimale Antwort eines `urllib`-Öffners (Kontextmanager mit Status, Kopfzeilen, Rumpf)."""

    def __init__(self, daten: bytes, *, status: int = 200,
                 content_type: str | None = "text/plain; charset=utf-8") -> None:
        self.status = status
        self.headers = Message()
        if content_type is not None:
            self.headers["Content-Type"] = content_type
        self._daten = daten

    def __enter__(self) -> _Antwort:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, n: int = -1) -> bytes:
        return self._daten if n < 0 else self._daten[:n]


def _oeffner(monkeypatch: pytest.MonkeyPatch, antwort: _Antwort | Exception) -> None:
    class Oeffner:
        def open(self, *_args: object, **_kwargs: object) -> _Antwort:
            if isinstance(antwort, Exception):
                raise antwort
            return antwort

    monkeypatch.setattr(securitytxt.urllib.request, "build_opener", lambda *_a: Oeffner())


def test_abrufen_liefert_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _oeffner(monkeypatch, _Antwort("Contact: ä\n".encode()))
    assert securitytxt.abrufen(URL) == "Contact: ä\n"


@pytest.mark.parametrize("status", [204, 206])
def test_abrufen_verlangt_status_200(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    _oeffner(monkeypatch, _Antwort(b"x", status=status))
    with pytest.raises(NachweisFehler) as e:
        securitytxt.abrufen(URL)
    assert e.value.regel == "securitytxt-abruf"


def test_abrufen_ohne_content_type_ist_fehler(monkeypatch: pytest.MonkeyPatch) -> None:
    _oeffner(monkeypatch, _Antwort(b"x", content_type=None))
    with pytest.raises(NachweisFehler) as e:
        securitytxt.abrufen(URL)
    assert e.value.regel == "securitytxt-abruf"


def test_abrufen_falscher_typ(monkeypatch: pytest.MonkeyPatch) -> None:
    _oeffner(monkeypatch, _Antwort(b"<html>", content_type="text/html"))
    with pytest.raises(NachweisFehler) as e:
        securitytxt.abrufen(URL)
    assert e.value.regel == "securitytxt-abruf" and "text/html" in e.value.text


def test_abrufen_groesse_grenze(monkeypatch: pytest.MonkeyPatch) -> None:
    _oeffner(monkeypatch, _Antwort(b"a" * securitytxt._MAX_BYTES))
    assert len(securitytxt.abrufen(URL)) == securitytxt._MAX_BYTES
    _oeffner(monkeypatch, _Antwort(b"a" * (securitytxt._MAX_BYTES + 1)))
    with pytest.raises(NachweisFehler) as e:
        securitytxt.abrufen(URL)
    assert e.value.regel == "securitytxt-abruf"


def test_abrufen_kein_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    _oeffner(monkeypatch, _Antwort(b"\xff\xfe"))
    with pytest.raises(NachweisFehler) as e:
        securitytxt.abrufen(URL)
    assert e.value.regel == "securitytxt-abruf"


def test_abrufen_http_fehler(monkeypatch: pytest.MonkeyPatch) -> None:
    _oeffner(monkeypatch, urllib.error.HTTPError(URL, 404, "nicht gefunden", Message(), None))
    with pytest.raises(NachweisFehler) as e:
        securitytxt.abrufen(URL)
    assert e.value.regel == "securitytxt-abruf" and "404" in e.value.text


def test_abrufen_oeffner_folgt_keiner_weiterleitung() -> None:
    """Der echte Öffner mit `_KeineUmleitung` macht aus einer Weiterleitung einen HTTP-Fehler
    (lokaler Server ohne TLS: geprüft wird nur der Öffner, nicht die https-Vorgabe)."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("Location", "/anderswo")
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return None

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        oeffner = urllib.request.build_opener(securitytxt._KeineUmleitung)
        with pytest.raises(urllib.error.HTTPError) as e:
            oeffner.open(f"http://127.0.0.1:{server.server_port}/", timeout=5)
        assert e.value.code == 302
    finally:
        server.shutdown()
        server.server_close()
