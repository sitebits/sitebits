"""Schmaler HTTP-Client für die scanview-API v1 mit persönlichem Token (Bearer).

Sicherheitsregeln:
- Der Tokenwert erscheint nie in Meldungen, Ausnahmen oder `repr` — auch nicht, wenn der Server
  ihn zitieren sollte oder die Verbindung scheitert (`raise … from None`, keine Kette).
- Es wird nie ein Cookie gesendet: Set-Cookie-Antworten werden verworfen, Weiterleitungen nicht
  verfolgt (der Bearer-Header soll nur an die konfigurierte Adresse gehen).
- Jede Antwort und jeder Konfliktstand (409) läuft durch `Datenschutz.filtern` (Befund B-TE-M3):
  ohne ausdrückliches `voll` keine Klarnamen anderer Konten und keine Freitexte. Ohne Angabe
  ist der Client datensparsam.
"""

from __future__ import annotations

from typing import Any

import httpx

from .datenschutz import Datenschutz

ZEITLIMIT_S = 30.0
_ERSATZ = "[Token]"
WEITERLEITUNG = "Weiterleitung – SCANVIEW_URL prüfen (genaue Adresse, https)."  # noqa: RUF001


class ApiFehler(Exception):
    """HTTP-Fehler der API. `status` 0 = keine Antwort (Verbindung, Zeitlimit).

    `text` ist der `fehler`-Text der Antwort (nie Header, nie der Tokenwert); `aktuell` der
    mitgelieferte aktuelle Stand bei 409.
    """

    def __init__(self, status: int, text: str, aktuell: Any = None) -> None:
        super().__init__(status, text)
        self.status = status
        self.text = text
        self.aktuell = aktuell

    def __str__(self) -> str:
        return f"HTTP {self.status}: {self.text}" if self.status else self.text


def _ohne_token(wert: Any, token: str) -> Any:
    """Ersetzt den Tokenwert in allen Texten einer JSON-Struktur (Verteidigung in der Tiefe)."""
    if isinstance(wert, str):
        return wert.replace(token, _ERSATZ)
    if isinstance(wert, list):
        return [_ohne_token(v, token) for v in wert]
    if isinstance(wert, dict):
        return {k: _ohne_token(v, token) for k, v in wert.items()}
    return wert


class Client:
    """`Client(url, token, transport=None, datenschutz=None)`; `get/post/patch/delete` liefern das
    JSON der Antwort (None bei leerem Körper) und werfen `ApiFehler`. `datenschutz=None` =
    datensparsam mit zufälligem Salz."""

    __slots__ = ("_http", "_schutz", "_token")

    def __init__(self, url: str, token: str,
                 transport: httpx.BaseTransport | None = None,
                 datenschutz: Datenschutz | None = None) -> None:
        self._token = token
        self._schutz = Datenschutz() if datenschutz is None else datenschutz
        self._http = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            transport=transport,
            timeout=ZEITLIMIT_S,
            follow_redirects=False,
            # Keine Proxy-Einstellungen aus der Umgebung (HTTP(S)_PROXY, ALL_PROXY, .netrc):
            # der Bearer-Token soll nur an SCANVIEW_URL gehen, nie an einen Proxy.
            trust_env=False,
        )

    @property
    def datenschutz(self) -> Datenschutz:
        return self._schutz

    def __repr__(self) -> str:
        return f"<scanview_mcp.api.Client {self._http.base_url}>"

    def get(self, pfad: str, **params: Any) -> Any:
        return self._anfrage("GET", pfad, params=params)

    def post(self, pfad: str, json: Any) -> Any:
        return self._anfrage("POST", pfad, json=json)

    def patch(self, pfad: str, json: Any) -> Any:
        return self._anfrage("PATCH", pfad, json=json)

    def delete(self, pfad: str, json: Any = None, **params: Any) -> Any:
        return self._anfrage("DELETE", pfad, json=json, params=params)

    def _anfrage(self, methode: str, pfad: str, *, json: Any = None,
                 params: dict[str, Any] | None = None) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        # Kein Cookie senden, auch keines, das eine frühere Antwort gesetzt hat.
        self._http.cookies.clear()
        try:
            r = self._http.request(methode, pfad, json=json, params=params or None)
        except httpx.TimeoutException:
            raise ApiFehler(0, "Zeitlimit überschritten – Server antwortet nicht.") from None  # noqa: RUF001
        except httpx.HTTPError as e:
            # Nur der Klassenname: Meldungen von httpx können URL/Header-Teile enthalten.
            art = type(e).__name__
            raise ApiFehler(
                0, f"Server nicht erreichbar ({art}) – Adresse in SCANVIEW_URL prüfen.",  # noqa: RUF001
            ) from None
        finally:
            self._http.cookies.clear()
        if r.status_code >= 400:
            raise self._fehler(r)
        if r.is_redirect or 300 <= r.status_code < 400:
            raise ApiFehler(r.status_code, WEITERLEITUNG)
        if not r.content:
            return None
        try:
            daten = r.json()
        except ValueError:
            raise ApiFehler(r.status_code, "Antwort ist kein JSON.") from None
        return self._schutz.filtern(_ohne_token(daten, self._token))

    def _fehler(self, r: httpx.Response) -> ApiFehler:
        text = "Keine Fehlerbeschreibung vom Server."
        aktuell: Any = None
        try:
            koerper = r.json()
        except ValueError:
            koerper = None
        if isinstance(koerper, dict):
            if isinstance(koerper.get("fehler"), str):
                text = koerper["fehler"]
            aktuell = koerper.get("aktuell")
        return ApiFehler(r.status_code, _ohne_token(text, self._token),
                         self._schutz.filtern(_ohne_token(aktuell, self._token)))
