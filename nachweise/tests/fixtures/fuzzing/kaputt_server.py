"""Absichtlich kaputter API-Dienst für die Negativkontrolle `fuzzing-serverfehler`: nur
Standardbibliothek, nur 127.0.0.1. Er beschreibt sich per OpenAPI unter `/api/v1/openapi.json`
(mit denselben Anmeldeschemata wie der echte Dienst) und hat genau eine Operation, die immer mit
500 antwortet (`GET /api/v1/kaputt`), und eine gesunde (`GET /api/v1/gut`). Ohne Anmeldung
(genau das Test-Cookie oder der Test-Token) gibt es 401, andere Methoden bekommen 405 mit
`Allow` — damit blockiert allein die kaputte Operation.

Aufruf: `python kaputt_server.py <port> <cookie> <token>` (Testwerte, keine echten Zugänge);
Port 0 wählt das Betriebssystem, der tatsächliche Port steht als erste Zeile auf stdout."""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_ANTWORT = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
_FEHLER = {"type": "object", "properties": {"fehler": {"type": "string"}}}


def _operation(zusammenfassung: str) -> dict[str, object]:
    return {"summary": zusammenfassung, "responses": {
        "200": {"description": "ok", "content": {"application/json": {"schema": _ANTWORT}}},
        "401": {"description": "nicht angemeldet",
                "content": {"application/json": {"schema": _FEHLER}}}}}


SCHEMA = {
    "openapi": "3.1.0",
    "info": {"title": "kaputt", "version": "0"},
    "paths": {"/api/v1/gut": {"get": _operation("gesund")},
              "/api/v1/kaputt": {"get": _operation("antwortet immer mit 500")}},
    "components": {"securitySchemes": {
        "bearerAuth": {"type": "http", "scheme": "bearer"},
        "cookieAuth": {"type": "apiKey", "in": "cookie", "name": "__Host-scanview"}}},
    "security": [{"bearerAuth": []}, {"cookieAuth": []}],
}


class _Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, daten: object) -> None:
        roh = json.dumps(daten).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(roh)))
        self.end_headers()
        self.wfile.write(roh)

    def _angemeldet(self) -> bool:
        cookie, token = self.server.anmeldung  # type: ignore[attr-defined]
        return (self.headers.get("Authorization") == f"Bearer {token}"
                or f"__Host-scanview={cookie}" in self.headers.get("Cookie", "").split("; "))

    def _nicht_erlaubt(self) -> None:
        roh = b'{"fehler": "Methode nicht erlaubt"}'
        self.send_response(405)
        self.send_header("Allow", "GET")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(roh)))
        self.end_headers()
        self.wfile.write(roh)

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = _nicht_erlaubt
    do_HEAD = do_QUERY = _nicht_erlaubt

    def do_GET(self) -> None:
        pfad = self.path.split("?", 1)[0]
        if not self._angemeldet():
            self._json(401, {"fehler": "nicht angemeldet"})
        elif pfad == "/api/v1/openapi.json":
            self._json(200, SCHEMA)
        elif pfad == "/api/v1/gut":
            self._json(200, {"ok": True})
        elif pfad == "/api/v1/kaputt":
            self._json(500, {"fehler": "absichtlich kaputt"})
        else:
            self._json(404, {"fehler": "nicht gefunden"})

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), _Handler)
    server.anmeldung = (sys.argv[2], sys.argv[3])  # type: ignore[attr-defined]
    print(server.server_address[1], flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
