"""MCP-Werkzeuge gegen eine nachgebildete API v1 (httpx.MockTransport).

Geprüft wird, was das Werkzeug über die Leitung schickt (Methode, Pfad aus endpunkte.json,
Körper, Bearer ohne Cookie) und was die KI bei Fehlern zu lesen bekommt — nie den Tokenwert.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from mcp import Client as McpClient
from mcp.client.stdio import StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError

from scanview_mcp import api, server, werkzeuge
from scanview_mcp.datenschutz import AUSGEBLENDET

TOKEN = "sv_GEHEIM-token-wert_0123456789abcdefghijklmnop"
URL = "https://scanview.test"
PID = "ost"
EID = "P-1"
AID = "0123456789abcdef"
UID = "0123456789abcdef0123456789abcdef"

ENDPUNKTE: list[dict[str, str]] = json.loads(
    resources.files("scanview_mcp").joinpath("endpunkte.json").read_text(encoding="utf-8")
)
TABELLE = {e["werkzeug"]: e for e in ENDPUNKTE}

Handler = Callable[[httpx.Request], httpx.Response]


class Aufzeichnung:
    """MockTransport, der jede Anfrage mitschreibt und dem Server ein Cookie unterschiebt."""

    def __init__(self, antwort: Handler) -> None:
        self.anfragen: list[httpx.Request] = []
        self._antwort = antwort

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.anfragen.append(request)
        r = self._antwort(request)
        r.headers["Set-Cookie"] = "scanview_sitzung=fremd; Path=/"
        return r


def client_mit(antwort: Handler) -> tuple[api.Client, Aufzeichnung]:
    auf = Aufzeichnung(antwort)
    return api.Client(URL, TOKEN, transport=httpx.MockTransport(auf)), auf


def ok(daten: Any = None, status: int = 200) -> Handler:
    return lambda _r: httpx.Response(status, json={} if daten is None else daten)


def fehler(status: int, text: str, **mehr: Any) -> Handler:
    return lambda _r: httpx.Response(status, json={"dienst": "scanview", "fehler": text, **mehr})


def pfad(vorlage: str) -> str:
    return vorlage.replace("{pid}", PID).replace("{eid}", EID).replace("{aid}", AID)


POI_DATEN = {"title": "Schaden", "position": [1.5, -2.0, 3.25]}

# Beispielaufruf je Werkzeug: (Argumente, erwarteter JSON-Körper der letzten Anfrage oder None,
# erwartete Query-Parameter der letzten Anfrage).
AUFRUFE: dict[str, tuple[dict[str, Any], Any, dict[str, str]]] = {
    "projekte_auflisten": ({}, None, {}),
    "projekt_lesen": ({"projekt_id": PID}, None, {}),
    "beteiligte_lesen": ({"projekt_id": PID}, None, {}),
    "beschreibung_setzen": (
        {"projekt_id": PID, "beschreibung": "Neue Beschreibung"},
        {"beschreibung": "Neue Beschreibung", "version": 3}, {},
    ),
    "pois_lesen": ({"projekt_id": PID}, None, {"vorschlaege": "1"}),
    "poi_anlegen": (
        {"projekt_id": PID, "titel": "Schaden", "position": [1.5, -2.0, 3.25]},
        {"daten": POI_DATEN}, {},
    ),
    "poi_aendern": (
        {"projekt_id": PID, "poi_id": EID, "version": 2, "titel": "Neu"},
        {"daten": {"title": "Neu"}, "version": 2}, {},
    ),
    "messungen_lesen": ({"projekt_id": PID}, None, {"vorschlaege": "1"}),
    "messung_anlegen": (
        {"projekt_id": PID, "art": "distance", "punkte": [[0, 0, 0], [3, 4, 0]]},
        {"daten": {"kind": "distance", "points": [[0.0, 0.0, 0.0], [3.0, 4.0, 0.0]]}}, {},
    ),
    "protokoll_lesen": ({"projekt_id": PID, "vor": 17, "limit": 5}, None,
                        {"vor": "17", "limit": "5"}),
    "export": ({"projekt_id": PID}, None, {}),
    "faden_lesen": ({"projekt_id": PID, "poi_id": EID}, None, {}),
    "kommentar_anlegen": (
        {"projekt_id": PID, "poi_id": EID, "text": "Bitte prüfen"},
        {"art": "kommentar", "text": "Bitte prüfen", "intern": False}, {},
    ),
    "status_setzen": (
        {"projekt_id": PID, "poi_id": EID, "status": "erledigt", "version": 3},
        {"art": "status", "wert": "erledigt", "version": 3}, {},
    ),
    "poi_loeschen": ({"projekt_id": PID, "poi_id": EID, "version": 3}, None, {"version": "3"}),
    "messung_loeschen": ({"projekt_id": PID, "messung_id": EID, "version": 4}, None,
                         {"version": "4"}),
    "link_anlegen": (
        {"projekt_id": PID, "poi_id": EID, "url": "https://example.org/a", "titel": "Plan"},
        {"art": "link", "wert": {"url": "https://example.org/a", "titel": "Plan"},
         "intern": False}, {},
    ),
    "zustaendig_setzen": (
        {"projekt_id": PID, "poi_id": EID, "version": 3, "gewerk_id": 7, "nutzer_id": 12},
        {"art": "zustaendig", "wert": {"gewerk_id": 7, "nutzer_id": 12}, "version": 3}, {},
    ),
    "frist_setzen": (
        {"projekt_id": PID, "poi_id": EID, "version": 3, "frist": "2026-11-30"},
        {"art": "frist", "wert": "2026-11-30", "version": 3}, {},
    ),
    "gewerke_lesen": ({"projekt_id": PID}, None, {}),
    "paket_lesen": ({"projekt_id": PID}, None, {}),
    "freigaben_lesen": ({"projekt_id": PID, "pruefen": True}, None, {"pruefen": "true"}),
    # Eigene Tests unten (roher Körper aus einer Datei, Anhang-ID mit 32 Zeichen).
    "anhang_hochladen": ({}, None, {}),
    "anhang_stand": ({}, None, {}),
    "benachrichtigung_lesen": ({"projekt_id": PID, "poi_id": EID}, None, {}),
    "benachrichtigung_setzen": ({"projekt_id": PID, "poi_id": EID, "aus": True}, {"aus": True},
                                {}),
    "auswertung_starten": (
        {"projekt_id": PID, "typ": "auswerten_pruefen",
         "optionen": {"profil": "bestandserfassung"}},
        {"typ": "auswerten_pruefen", "optionen": {"profil": "bestandserfassung"}}, {},
    ),
    "auswertung_status": ({"projekt_id": PID}, None, {}),
    "auswertung_lesen": ({"projekt_id": PID, "auswertung_id": AID}, None, {}),
    "import_starten": (
        {"upload_id": UID, "auftragstyp": "neu_splats", "optionen": {"projekt": "neu"}},
        {"upload": UID, "typ": "neu_splats", "optionen": {"projekt": "neu"}}, {},
    ),
    "import_status": ({}, None, {}),
}


def standard_antwort(request: httpx.Request) -> httpx.Response:
    if request.method == "GET" and request.url.path == f"/api/v1/projekte/{PID}":
        return httpx.Response(200, json={"id": PID, "version": 3, "beschreibung": "alt"})
    return httpx.Response(200, json={"ok": True})


# --- Tabelle -------------------------------------------------------------------------------------

def test_endpunkte_tabelle_vollstaendig() -> None:
    assert len(ENDPUNKTE) == 31
    assert set(TABELLE) == set(werkzeuge.WERKZEUGE) == set(AUFRUFE)
    for e in ENDPUNKTE:
        assert set(e) == {"werkzeug", "methode", "pfad"}
        assert e["methode"] in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        assert e["pfad"].startswith(("/api/v1/projekte", "/api/v1/importe"))


# --- Leitung: Methode, Pfad, Körper, Bearer, kein Cookie -----------------------------------------

SONDERFAELLE = {"anhang_hochladen", "anhang_stand"}


@pytest.mark.parametrize("name", sorted(set(AUFRUFE) - SONDERFAELLE))
def test_werkzeug_ruft_genau_seinen_endpunkt(name: str) -> None:
    c, auf = client_mit(standard_antwort)
    argumente, koerper, query = AUFRUFE[name]
    werkzeuge.WERKZEUGE[name](c, **argumente)
    werkzeuge.WERKZEUGE[name](c, **argumente)  # zweimal: Set-Cookie darf nicht zurückkommen
    letzte = auf.anfragen[-1]
    assert letzte.method == TABELLE[name]["methode"]
    assert letzte.url.path == pfad(TABELLE[name]["pfad"])
    assert dict(letzte.url.params) == query
    if koerper is None:
        assert letzte.content == b""
    else:
        assert json.loads(letzte.content) == koerper
    for r in auf.anfragen:
        assert r.headers["Authorization"] == f"Bearer {TOKEN}"
        assert "cookie" not in r.headers
        assert r.url.host == "scanview.test"


def test_beschreibung_setzen_liest_erst_die_version() -> None:
    c, auf = client_mit(standard_antwort)
    werkzeuge.beschreibung_setzen(c, projekt_id=PID, beschreibung="x")
    assert [(r.method, r.url.path) for r in auf.anfragen] == [
        ("GET", f"/api/v1/projekte/{PID}"), ("PATCH", f"/api/v1/projekte/{PID}"),
    ]
    assert json.loads(auf.anfragen[1].content) == {"beschreibung": "x", "version": 3}


def test_poi_anlegen_mit_allen_feldern_und_id() -> None:
    c, auf = client_mit(ok({"id": "P-a"}, 201))
    werkzeuge.poi_anlegen(c, projekt_id=PID, titel="T", position=[1, 2, 3], kategorie="Riss",
                          notiz="Zeile 1\nZeile 2", standpunkt="S01", poi_id="P-a")
    assert json.loads(auf.anfragen[0].content) == {
        "id": "P-a",
        "daten": {"title": "T", "position": [1.0, 2.0, 3.0], "category": "Riss",
                  "note": "Zeile 1\nZeile 2", "station": "S01"},
    }


def test_poi_aendern_schickt_nur_genannte_felder() -> None:
    c, auf = client_mit(ok())
    werkzeuge.poi_aendern(c, projekt_id=PID, poi_id=EID, version=4, notiz="",
                          position=[0, 0, 1])
    assert json.loads(auf.anfragen[0].content) == {
        "daten": {"note": "", "position": [0.0, 0.0, 1.0]}, "version": 4,
    }


def test_poi_aendern_ohne_aenderung_fragt_nicht() -> None:
    c, auf = client_mit(ok())
    with pytest.raises(ToolError, match="Keine Änderung"):
        werkzeuge.poi_aendern(c, projekt_id=PID, poi_id=EID, version=4)
    assert auf.anfragen == []


def test_status_setzen_mit_begruendung_und_intern_kommentar() -> None:
    c, auf = client_mit(ok({"eintrag": {}, "stand": {}}, 201))
    werkzeuge.status_setzen(c, projekt_id=PID, poi_id=EID, status="offen", version=5,
                            begruendung="Wieder undicht")
    assert json.loads(auf.anfragen[-1].content) == {
        "art": "status", "wert": "offen", "version": 5, "text": "Wieder undicht"}
    werkzeuge.kommentar_anlegen(c, projekt_id=PID, poi_id=EID, text="Nachtrag", intern=True)
    assert json.loads(auf.anfragen[-1].content) == {
        "art": "kommentar", "text": "Nachtrag", "intern": True}


def test_status_setzen_409_verstaendlich() -> None:
    c, _ = client_mit(fehler(409, "Konflikt.", aktuell={"status": "erledigt", "version": 4}))
    with pytest.raises(ToolError) as e:
        werkzeuge.status_setzen(c, projekt_id=PID, poi_id=EID, status="in_arbeit", version=2)
    assert "neu lesen" in str(e.value) and "4" in str(e.value)


def test_protokoll_ohne_parameter() -> None:
    c, auf = client_mit(ok({"ereignisse": [], "weiter": None}))
    werkzeuge.protokoll_lesen(c, projekt_id=PID)
    assert dict(auf.anfragen[0].url.params) == {}


def test_antwort_wird_durchgereicht() -> None:
    c, _ = client_mit(ok({"projekte": [{"id": PID}]}))
    assert werkzeuge.projekte_auflisten(c) == {"projekte": [{"id": PID}]}


@pytest.mark.parametrize(("feld", "wert"), [
    ("projekt_id", "../admin"), ("projekt_id", "OST"), ("projekt_id", "a/b"),
    ("projekt_id", ""), ("poi_id", "P-1/../../x"), ("poi_id", "a" * 65), ("poi_id", "P 1"),
])
def test_ungueltige_ids_erreichen_nie_die_leitung(feld: str, wert: str) -> None:
    c, auf = client_mit(ok())
    argumente: dict[str, Any] = {"projekt_id": PID, "poi_id": EID, "version": 1, "titel": "x"}
    argumente[feld] = wert
    with pytest.raises(ToolError, match="ungültig"):
        werkzeuge.poi_aendern(c, **argumente)
    assert auf.anfragen == []


# --- Fehler: verständlich, ohne Token -----------------------------------------------------------

def test_409_bei_aenderung_verstaendlich() -> None:
    c, _ = client_mit(fehler(409, "Konflikt.", aktuell={"id": EID, "version": 7}))
    with pytest.raises(ToolError) as e:
        werkzeuge.poi_aendern(c, projekt_id=PID, poi_id=EID, version=2, titel="x")
    text = str(e.value)
    assert "Jemand anderes hat inzwischen geändert – bitte neu lesen" in text  # noqa: RUF001
    assert "7" in text


def test_409_bei_beschreibung_verstaendlich() -> None:
    def antwort(r: httpx.Request) -> httpx.Response:
        if r.method == "GET":
            return httpx.Response(200, json={"version": 1})
        return httpx.Response(409, json={"fehler": "Konflikt.", "aktuell": {"version": 2}})
    c, _ = client_mit(antwort)
    with pytest.raises(ToolError) as e:
        werkzeuge.beschreibung_setzen(c, projekt_id=PID, beschreibung="x")
    assert "Jemand anderes hat inzwischen geändert – bitte erneut aufrufen" in str(e.value)  # noqa: RUF001
    assert "neu lesen" not in str(e.value)


def test_409_beim_anlegen_nennt_vergebene_id() -> None:
    c, _ = client_mit(fehler(409, "Diese ID ist schon vergeben – aktueller Stand anbei.",  # noqa: RUF001
                             aktuell={"id": "P-a", "version": 1}))
    with pytest.raises(ToolError, match="ID ist schon vergeben"):
        werkzeuge.poi_anlegen(c, projekt_id=PID, titel="T", position=[0, 0, 0], poi_id="P-a")


@pytest.mark.parametrize(("status", "erwartet"), [
    (401, "Token"), (403, "Keine Berechtigung"), (404, "nicht gefunden"),
    (422, "Titel fehlt"), (429, "Zu viele Anfragen"), (500, "Serverfehler"),
])
def test_http_fehler_verstaendlich(status: int, erwartet: str) -> None:
    c, _ = client_mit(fehler(status, "Titel fehlt."))
    with pytest.raises(ToolError) as e:
        werkzeuge.pois_lesen(c, projekt_id=PID)
    assert erwartet.lower() in str(e.value).lower()
    assert str(status) in str(e.value)


def test_nicht_json_fehler() -> None:
    c, _ = client_mit(lambda _r: httpx.Response(502, text="<html>Bad Gateway</html>"))
    with pytest.raises(ToolError) as e:
        werkzeuge.projekte_auflisten(c)
    assert "502" in str(e.value)
    assert "<html>" not in str(e.value)


@pytest.mark.parametrize("status", [401, 403, 404, 409, 422, 500])
def test_token_nie_in_fehlermeldung_auch_wenn_server_es_zitiert(status: int) -> None:
    c, _ = client_mit(fehler(status, f"Token {TOKEN} ist kaputt", aktuell={"t": TOKEN}))
    with pytest.raises(ToolError) as e:
        werkzeuge.poi_aendern(c, projekt_id=PID, poi_id=EID, version=1, titel="x")
    assert TOKEN not in str(e.value)
    assert TOKEN not in repr(e.value)
    with pytest.raises(api.ApiFehler) as a:
        c.get(f"/api/v1/projekte/{PID}")
    assert a.value.status == status
    assert TOKEN not in str(a.value)
    assert TOKEN not in a.value.text


def test_api_fehler_traegt_fehlertext() -> None:
    c, _ = client_mit(fehler(422, "Titel fehlt."))
    with pytest.raises(api.ApiFehler) as a:
        c.post(f"/api/v1/projekte/{PID}/pois", {"daten": {}})
    assert (a.value.status, a.value.text) == (422, "Titel fehlt.")


def _verbindung_kaputt(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError(f"kaputt bei {request.url} mit {request.headers}", request=request)


def test_verbindungsfehler_ohne_token() -> None:
    c = api.Client(URL, TOKEN, transport=httpx.MockTransport(_verbindung_kaputt))
    with pytest.raises(api.ApiFehler) as a:
        c.get("/api/v1/projekte")
    assert TOKEN not in str(a.value)
    assert a.value.__cause__ is None
    assert a.value.__suppress_context__
    with pytest.raises(ToolError) as e:
        werkzeuge.projekte_auflisten(c)
    assert "nicht erreichbar" in str(e.value)
    assert TOKEN not in str(e.value)
    assert e.value.__cause__ is None
    assert e.value.__suppress_context__


def test_client_repr_ohne_token() -> None:
    c, _ = client_mit(ok())
    assert TOKEN not in repr(c)


# --- Umgebung ------------------------------------------------------------------------------------

@pytest.mark.parametrize(("umgebung", "variable"), [
    ({"SCANVIEW_TOKEN": TOKEN}, "SCANVIEW_URL"),
    ({"SCANVIEW_URL": URL}, "SCANVIEW_TOKEN"),
    ({"SCANVIEW_URL": "", "SCANVIEW_TOKEN": TOKEN}, "SCANVIEW_URL"),
    ({"SCANVIEW_URL": URL, "SCANVIEW_TOKEN": "GEHEIM-ohne-praefix"}, "SCANVIEW_TOKEN"),
    ({"SCANVIEW_URL": "http://example.com", "SCANVIEW_TOKEN": TOKEN}, "SCANVIEW_URL"),
    ({"SCANVIEW_URL": "ftp://scanview.test", "SCANVIEW_TOKEN": TOKEN}, "SCANVIEW_URL"),
])
def test_fehlende_oder_falsche_umgebung_exit_2(
    umgebung: dict[str, str], variable: str, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("SCANVIEW_URL", raising=False)
    monkeypatch.delenv("SCANVIEW_TOKEN", raising=False)
    for k, v in umgebung.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(SystemExit) as e:
        server.main()
    assert e.value.code == 2
    aus = capsys.readouterr()
    assert variable in aus.err
    assert aus.out == ""
    assert TOKEN not in aus.err
    assert "GEHEIM" not in aus.err


@pytest.mark.parametrize("url", [
    "https://scanview.example.de", "https://scanview.example.de/", "http://localhost:18090",
    "http://127.0.0.1:18090", "http://[::1]:18090",
])
def test_erlaubte_urls(url: str) -> None:
    assert server.umgebung_lesen({"SCANVIEW_URL": url, "SCANVIEW_TOKEN": TOKEN}) == (
        url.rstrip("/"), TOKEN,
    )


# --- MCP-Server: Werkzeugliste und Fehlerweg bis zur KI -----------------------------------------

def _texte(inhalt: Any) -> str:
    return " ".join(getattr(t, "text", "") for t in inhalt)


def test_mcp_werkzeugliste_in_process() -> None:
    c, _ = client_mit(standard_antwort)
    mcp = server.baue_server(c)

    async def ablauf() -> None:
        async with McpClient(mcp) as k:
            tools = (await k.list_tools()).tools
            assert sorted(t.name for t in tools) == sorted(TABELLE)
            beschreibung = {t.name: t.description or "" for t in tools}
            for name in ("beschreibung_setzen", "poi_anlegen", "poi_aendern", "messung_anlegen",
                         "kommentar_anlegen", "status_setzen"):
                assert "für alle Projektbeteiligten sichtbar" in beschreibung[name]
                assert "dauerhaft" in beschreibung[name]
            for name in ("poi_anlegen", "messung_anlegen"):
                assert "Meter" in beschreibung[name]
                assert "Weltkoordinaten" in beschreibung[name]

    anyio.run(ablauf)


def test_mcp_fehler_erreicht_die_ki_als_text() -> None:
    c, _ = client_mit(fehler(409, "Konflikt.", aktuell={"version": 9}))
    mcp = server.baue_server(c)

    async def ablauf() -> None:
        async with McpClient(mcp) as k:
            r = await k.call_tool("poi_aendern", {"projekt_id": PID, "poi_id": EID,
                                                  "version": 1, "titel": "x"})
            assert r.is_error
            assert "Jemand anderes hat inzwischen geändert" in _texte(r.content)
            ok_r = await k.call_tool("pois_lesen", {"projekt_id": PID})
            assert ok_r.is_error
            assert TOKEN not in _texte(ok_r.content)

    anyio.run(ablauf)


def test_mcp_erfolg_liefert_json() -> None:
    c, _ = client_mit(ok({"pois": [{"id": EID, "version": 1}]}))
    mcp = server.baue_server(c)

    async def ablauf() -> None:
        async with McpClient(mcp) as k:
            r = await k.call_tool("pois_lesen", {"projekt_id": PID})
            assert not r.is_error
            assert r.structured_content == {"pois": [{"id": EID, "version": 1}]}

    anyio.run(ablauf)


def _befehl() -> str:
    kandidat = Path(sys.executable).parent / "scanview-mcp"
    assert kandidat.exists(), "scanview-mcp nicht installiert (uv sync --locked)"
    return str(kandidat)


def test_stdio_rauchtest_tools_list() -> None:
    """Startet die Konsole `scanview-mcp` als Unterprozess (wie Claude Desktop) und fragt über
    stdio `tools/list` ab. Die Werkzeugliste braucht keine API — Port 1 ist absichtlich tot;
    ein Werkzeugaufruf zeigt dann den Verbindungsfehler ohne Token."""
    params = StdioServerParameters(
        command=_befehl(),
        env={"SCANVIEW_URL": "http://localhost:1", "SCANVIEW_TOKEN": TOKEN,
             "PATH": os.environ.get("PATH", "")},
    )

    async def ablauf() -> None:
        async with McpClient(params) as k:
            tools = (await k.list_tools()).tools
            assert len(tools) == 31
            assert sorted(t.name for t in tools) == sorted(TABELLE)
            r = await k.call_tool("projekte_auflisten", {})
            assert r.is_error
            text = _texte(r.content)
            assert "nicht erreichbar" in text
            assert TOKEN not in text

    anyio.run(ablauf)


def test_werkzeugnamen_passen_zum_muster() -> None:
    for name in TABELLE:
        assert re.fullmatch(r"[a-z_]{3,40}", name)


# --- Weiterleitungen und Proxy-Verhalten des HTTP-Clients ---------------------------------------

@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_weiterleitung_ist_ein_fehler(status: int) -> None:
    c, auf = client_mit(lambda _r: httpx.Response(
        status, headers={"Location": "https://anderswo.test/api/v1/projekte"}))
    with pytest.raises(api.ApiFehler) as a:
        c.get("/api/v1/projekte")
    assert a.value.status == status
    assert "Weiterleitung" in a.value.text and "SCANVIEW_URL" in a.value.text
    with pytest.raises(ToolError) as e:
        werkzeuge.projekte_auflisten(c)
    assert "Weiterleitung" in str(e.value)
    assert all(r.url.host == "scanview.test" for r in auf.anfragen)


def test_proxy_aus_der_umgebung_wird_ignoriert(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(k, "http://proxy.test:3128")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    # Gegenprobe: httpx mit trust_env=True würde den Proxy übernehmen (Mounts je Schema).
    assert httpx.Client()._mounts
    c = api.Client(URL, TOKEN)
    assert c._http.trust_env is False
    assert not c._http._mounts


def test_standpunkt_entfernen_schickt_null() -> None:
    c, auf = client_mit(ok())
    werkzeuge.poi_aendern(c, projekt_id=PID, poi_id=EID, version=3, standpunkt_entfernen=True)
    assert json.loads(auf.anfragen[0].content) == {"daten": {"station": None}, "version": 3}


def test_standpunkt_setzen_und_entfernen_zugleich_abgelehnt() -> None:
    c, auf = client_mit(ok())
    with pytest.raises(ToolError, match="nicht beides"):
        werkzeuge.poi_aendern(c, projekt_id=PID, poi_id=EID, version=3, standpunkt="S1",
                              standpunkt_entfernen=True)
    assert auf.anfragen == []


@pytest.mark.parametrize(("antwort", "erwartet"), [
    ([{"id": 1}], {"eintraege": [{"id": 1}]}), ("text", None), (None, None), (7, None),
])
def test_ergebnis_ohne_objekt_verstaendlich(antwort: Any, erwartet: Any) -> None:
    def h(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(204) if antwort is None else httpx.Response(200, json=antwort)
    c, _ = client_mit(h)
    mcp = server.baue_server(c)

    async def ablauf() -> None:
        async with McpClient(mcp) as k:
            r = await k.call_tool("pois_lesen", {"projekt_id": PID})
            if erwartet is None:
                assert r.is_error
                assert "Unerwartete Antwort" in _texte(r.content)
                assert "TypeError" not in _texte(r.content)
            else:
                assert not r.is_error
                assert r.structured_content == erwartet

    anyio.run(ablauf)


def test_quellen_werden_durchgereicht() -> None:
    source = {"source": "mesh", "layerId": "modell"}
    c, auf = client_mit(ok())
    werkzeuge.messung_anlegen(c, PID, "distance", [[0, 0, 0], [1, 0, 0]],
                             point_sources=[source, None])
    assert json.loads(auf.anfragen[-1].content)["daten"]["pointSources"] == [source, None]
    werkzeuge.poi_anlegen(c, PID, "Quelle", [1, 2, 3], position_source=source)
    assert json.loads(auf.anfragen[-1].content)["daten"]["positionSource"] == source
    werkzeuge.poi_aendern(c, PID, EID, 1, position_source=source)
    assert json.loads(auf.anfragen[-1].content)["daten"]["positionSource"] == source


def test_loeschen_bestaetigt_und_aufheben() -> None:
    c, auf = client_mit(lambda _r: httpx.Response(204))
    assert werkzeuge.poi_loeschen(c, projekt_id=PID, poi_id=EID, version=2) == {"geloescht": EID}
    # G7: 202 mit Eintrag = nur vorgeschlagen, nie als gelöscht melden.
    vorschlag = {"id": EID, "version": 3, "loeschenVorgeschlagen": 100}
    c, auf = client_mit(lambda _r: httpx.Response(202, json=vorschlag))
    for f in (werkzeuge.poi_loeschen, werkzeuge.messung_loeschen):
        ergebnis = f(c, PID, EID, 2)
        assert "geloescht" not in ergebnis
        assert ergebnis["loeschenVorgeschlagen"] == EID and ergebnis["eintrag"]["version"] == 3
    c, auf = client_mit(ok({"eintrag": {}, "stand": {}}, 201))
    werkzeuge.zustaendig_setzen(c, projekt_id=PID, poi_id=EID, version=5, gewerk_id=None)
    assert json.loads(auf.anfragen[-1].content) == {"art": "zustaendig", "wert": None, "version": 5}
    werkzeuge.frist_setzen(c, projekt_id=PID, poi_id=EID, version=6, frist=None)
    assert json.loads(auf.anfragen[-1].content) == {"art": "frist", "wert": None, "version": 6}
    with pytest.raises(ToolError, match="Gewerk"):
        werkzeuge.zustaendig_setzen(c, projekt_id=PID, poi_id=EID, version=5, gewerk_id=None,
                                    nutzer_id=3)
    with pytest.raises(ToolError):
        werkzeuge.link_anlegen(c, projekt_id=PID, poi_id=EID, url="https://x.org",
                               titel=AUSGEBLENDET)


def test_409_ohne_stand_nennt_servertext() -> None:
    """G7: gleicher Vorschlag schon offen — kein „jemand hat geändert“."""
    text = "Genau dieser Vorschlag ist schon offen – bitte die Entscheidung abwarten."  # noqa: RUF001
    c, _ = client_mit(fehler(409, text))
    with pytest.raises(ToolError) as e:
        werkzeuge.frist_setzen(c, projekt_id=PID, poi_id=EID, version=3, frist="2026-11-30")
    assert "schon offen" in str(e.value) and "geändert" not in str(e.value)


# --- Auswertungen ---------------------------------------------------------------------------------

ERGEBNIS = {
    "katalog": {"titel": "Bestandserfassung", "datei": "bestandserfassung.toml", "sha256": "ab"},
    "gesamt": {"urteil": "nicht erfüllt", "zaehlung": {"erfüllt": 1, "nicht erfüllt": 1}},
    "eingaenge_sha256": {"wolke/geheim.laz": "cd"},
    "versionen": {"python": "3.12"},
    "zeilen": [{"id": "L01", "anforderung": "Bezugssystem", "pruefart": "crs",
                "parameter": {"epsg": 25833}, "urteil": "erfüllt", "grund": "passt",
                "belege": ["wolke/a.laz"], "karten": ["L01-a.png"],
                "tabelle": [{"punkt": "K1", "dz": 0.01}]}],
}


def test_auswertung_lesen_liefert_nur_urteile_keine_rohdaten() -> None:
    c, auf = client_mit(ok(ERGEBNIS))
    e = werkzeuge.auswertung_lesen(c, PID, AID)
    assert auf.anfragen[-1].url.path == f"/api/v1/projekte/{PID}/auswertungen/{AID}/ergebnis"
    assert set(e) == {"katalog", "gesamt", "zeilen"}
    assert e["gesamt"]["urteil"] == "nicht erfüllt"
    assert e["zeilen"] == [{"id": "L01", "anforderung": "Bezugssystem", "pruefart": "crs",
                            "parameter": {"epsg": 25833}, "urteil": "erfüllt", "grund": "passt",
                            "belege": ["wolke/a.laz"]}]
    text = json.dumps(e)
    assert "geheim.laz" not in text and "tabelle" not in text and "karten" not in text


@pytest.mark.parametrize("aid", ["../x", "0123456789ABCDEF", "0123", "0123456789abcdef/../a", ""])
def test_auswertung_ids_werden_vor_dem_aufruf_geprueft(aid: str) -> None:
    c, auf = client_mit(ok())
    with pytest.raises(ToolError, match="Auswertungs-ID ungültig"):
        werkzeuge.auswertung_lesen(c, PID, aid)
    with pytest.raises(ToolError, match="Auswertungs-ID ungültig"):
        werkzeuge.auswertung_status(c, PID, aid)
    assert auf.anfragen == []


@pytest.mark.parametrize("typ", ["../x", "auswerten pruefen", "A", 3])
def test_auswertung_starten_prueft_typ_und_optionen(typ: Any) -> None:
    c, auf = client_mit(ok())
    with pytest.raises(ToolError, match="Auswertungstyp ungültig"):
        werkzeuge.auswertung_starten(c, PID, typ)
    with pytest.raises(ToolError, match="optionen muss ein Objekt"):
        falsch: Any = ["profil"]
        werkzeuge.auswertung_starten(c, PID, "auswerten_pruefen", falsch)
    assert auf.anfragen == []


def test_auswertung_status_filtert_eine_und_meldet_fehlende() -> None:
    liste = {"auswertungen": [{"id": AID, "status": "fertig"},
                              {"id": "f" * 16, "status": "wartend"}], "worker": "aktiv"}
    c, _auf = client_mit(ok(liste))
    assert werkzeuge.auswertung_status(c, PID, AID) == {"id": AID, "status": "fertig",
                                                         "worker": "aktiv"}
    assert werkzeuge.auswertung_status(c, PID) == liste
    with pytest.raises(ToolError, match="404"):
        werkzeuge.auswertung_status(c, PID, "e" * 16)


def test_auswertung_starten_meldet_serverabsage_verstaendlich() -> None:
    c, _auf = client_mit(fehler(422, "Unbekanntes Profil. Erlaubt: bestandserfassung."))
    with pytest.raises(ToolError, match="Unbekanntes Profil"):
        werkzeuge.auswertung_starten(c, PID, "auswerten_pruefen", {"profil": "x"})
    c, _auf = client_mit(fehler(403, "Auswertungen starten und verwalten nur Projektleitung."))
    with pytest.raises(ToolError, match="403"):
        werkzeuge.auswertung_starten(c, PID, "auswerten_pruefen")


def test_auswertung_status_datensparsam_pseudonymisiert_startende_person() -> None:
    liste = {"auswertungen": [{"id": AID, "erstelltVon": 7, "erstelltVonName": "Erika Muster"}],
             "worker": "aktiv"}
    c, _auf = client_mit(ok(liste))
    a = werkzeuge.auswertung_status(c, PID, AID)
    assert "Erika" not in json.dumps(a)
    assert a["erstelltVon"] == a["erstelltVonName"] and a["erstelltVon"].startswith("Konto-")


@pytest.mark.parametrize(("upload", "typ", "optionen"), [
    ("x" * 32, "neu_mesh", {"projekt": "neu"}),
    (UID, "neu_wolke", {"projekt": "neu"}),
    (UID, "auswerten_pruefen", {"projekt": "neu"}),
    (UID, "neu_mesh", {}),
    (UID, "neu_mesh", {"projekt": "../x"}),
    (UID, "neu_mesh", {"projekt": "neu", "datei": "/etc/passwd"}),
    (UID, "neu_mesh", {"projekt": "neu", "crs": 25833}),
])
def test_import_starten_prueft_vor_dem_aufruf(upload: str, typ: str, optionen: Any) -> None:
    c, auf = client_mit(standard_antwort)
    with pytest.raises(ToolError):
        werkzeuge.import_starten(c, upload, typ, optionen)
    assert auf.anfragen == []


def test_import_status_filtert_nach_projekt() -> None:
    liste = {"importe": [{"id": AID, "projekt": "neu"}, {"id": "e" * 16, "projekt": "alt"}],
             "worker": "aktiv"}
    c, _auf = client_mit(ok(liste))
    assert werkzeuge.import_status(c, "neu") == {"importe": [{"id": AID, "projekt": "neu"}],
                                                 "worker": "aktiv"}
    assert werkzeuge.import_status(c) == liste


def test_anhang_hochladen_roh_mit_typ_aus_dem_inhalt(tmp_path: Path) -> None:
    c, auf = client_mit(ok({"eintrag": {"art": "anhang"}}, 201))
    datei = tmp_path / "Riss 1.jpg"
    datei.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 10)
    werkzeuge.anhang_hochladen(c, projekt_id=PID, poi_id=EID, pfad=str(datei))
    r = auf.anfragen[-1]
    assert (r.method, r.url.path) == ("POST", f"/api/v1/projekte/{PID}/pois/{EID}/faden/anhaenge")
    assert r.headers["Content-Type"] == "image/jpeg" and r.content == datei.read_bytes()
    assert dict(r.url.params) == {"name": "Riss 1.jpg"}
    assert r.headers["Authorization"] == f"Bearer {TOKEN}" and "cookie" not in r.headers


@pytest.mark.parametrize("inhalt,text", [(b"<svg/>", "Nur JPEG"), (b"", "Nur JPEG")])
def test_anhang_hochladen_lehnt_ab(tmp_path: Path, inhalt: bytes, text: str) -> None:
    c, auf = client_mit(standard_antwort)
    datei = tmp_path / "x"
    datei.write_bytes(inhalt)
    with pytest.raises(ToolError, match=text):
        werkzeuge.anhang_hochladen(c, projekt_id=PID, poi_id=EID, pfad=str(datei))
    with pytest.raises(ToolError, match="nicht gefunden"):
        werkzeuge.anhang_hochladen(c, projekt_id=PID, poi_id=EID, pfad=str(tmp_path / "fehlt"))
    assert auf.anfragen == []


def test_anhang_stand_pfad_und_id() -> None:
    c, auf = client_mit(standard_antwort)
    werkzeuge.anhang_stand(c, projekt_id=PID, poi_id=EID, anhang_id=UID)
    erwartet = f"/api/v1/projekte/{PID}/pois/{EID}/faden/anhaenge/{UID}/stand"
    assert auf.anfragen[-1].url.path == erwartet
    with pytest.raises(ToolError):
        werkzeuge.anhang_stand(c, projekt_id=PID, poi_id=EID, anhang_id="../x")


def test_datenschutz_blendet_dateinamen_aus() -> None:
    from scanview_mcp.datenschutz import Datenschutz

    d = Datenschutz(voll=False, salz=b"s" * 32)
    aus = d.filtern({"art": "anhang", "wert": {"anhang": UID, "typ": "bild", "name": "Erika.jpg"},
                     "anhang": {"id": UID, "status": "bereit", "name": "Erika.jpg"}})
    assert aus["wert"] == {"anhang": UID, "typ": "bild", "name": AUSGEBLENDET}
    assert aus["anhang"]["name"] == AUSGEBLENDET and aus["anhang"]["status"] == "bereit"
