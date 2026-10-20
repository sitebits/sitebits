"""API-Fuzzing (Schemathesis gegen eine Wegwerf-Instanz): Konfiguration, JUnit-Auswertung,
Abdeckung, Gate, Bereinigung der Berichte, Ports, Sitzungsprobe und die Container-Läufe."""

from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scanview_nachweise import fuzzing
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.werkzeuge import Werkzeug, lade_werkzeuge

WURZEL = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "fuzzing"
UV = "ghcr.io/astral-sh/uv@sha256:" + "d" * 64
COOKIE = "testcookie-ohne-bedeutung-123"
TOKEN = "testtoken-ohne-bedeutung-456"

GUELTIG = """
seed = 7
max_examples = 3

[pruefungen]
aktiv = ["not_a_server_error", "response_schema_conformance", "content_type_conformance",
         "response_headers_conformance", "negative_data_rejection", "missing_required_header",
         "unsupported_method", "use_after_free", "ensure_resource_availability", "ignored_auth",
         "allow_header_conformance"]

[pruefungen.aus]
status_code_conformance = "Fehlerstatus sind im Schema nicht dokumentiert (Testbegründung)"
positive_data_acceptance = "Dienst prüft strenger als das Schema (Testbegründung lang)"

[[ausgeschlossen]]
identitaet = "admin"
pfad_regex = '^/api/v1/admin/nutzer/\\{uid\\}/sperren$'
grund = "beendet die eigene Sitzung des Laufs (Testbegründung)"
"""


def _konfig(tmp_path: Path, text: str = GUELTIG) -> Path:
    pfad = tmp_path / "fuzzing.toml"
    pfad.write_text(text, encoding="utf-8")
    return pfad


def _werkzeuge() -> dict[str, Werkzeug]:
    return {"schemathesis": Werkzeug("schemathesis", UV, "4.27.5", "werkzeuge/schemathesis"),
            "caddy-test": Werkzeug("caddy-test", "caddy@sha256:" + "c" * 64, "2.11.4", None)}


# --- Konfiguration --------------------------------------------------------------------------------

def test_echte_konfig_laedt():
    k = fuzzing.lade_konfig(WURZEL / "nachweise" / fuzzing.KONFIG)
    assert k.seed == 20260928 and k.max_examples >= 1
    assert set(k.aktiv) | set(k.aus) == set(fuzzing.CHECKS)
    assert set(fuzzing.PFLICHT_CHECKS) <= set(k.aktiv)
    assert any(a.identitaet == "admin" for a in k.ausgeschlossen)


def test_konfig_laedt(tmp_path):
    k = fuzzing.lade_konfig(_konfig(tmp_path))
    assert (k.seed, k.max_examples) == (7, 3)
    assert k.ausgeschlossen[0].identitaet == "admin"
    assert k.aus["status_code_conformance"].startswith("Fehlerstatus")


@pytest.mark.parametrize(("alt", "neu"), [
    ('grund = "beendet die eigene Sitzung des Laufs (Testbegründung)"', 'grund = "zu kurz"'),
    ('grund = "beendet die eigene Sitzung des Laufs (Testbegründung)"\n', "\n"),
    ('identitaet = "admin"', 'identitaet = "gast"'),
    ("pfad_regex = '^/api/v1/admin/nutzer/\\{uid\\}/sperren$'", "pfad_regex = '(['"),
    ("seed = 7", 'seed = "7"'),
    ("max_examples = 3", "max_examples = 0"),
    ('"allow_header_conformance"]', '"allow_header_conformance", "erfunden"]'),
    ('"allow_header_conformance"]', "]"),
    ('"not_a_server_error", ', ""),
    ('positive_data_acceptance = "Dienst prüft strenger als das Schema (Testbegründung lang)"',
     'positive_data_acceptance = "kurz"'),
    ('positive_data_acceptance = "Dienst', 'ignored_auth = "Doppelt aktiv und aus, Testbegründung"'
                                          '\npositive_data_acceptance = "Dienst'),
    ("seed = 7", "seed = 7\nunbekannt = 1"),
])
def test_konfig_fehler_ist_ladefehler(tmp_path, alt, neu):
    assert alt in GUELTIG
    with pytest.raises(NachweisFehler) as e:
        fuzzing.lade_konfig(_konfig(tmp_path, GUELTIG.replace(alt, neu, 1)))
    assert e.value.regel == "fuzzing-konfig"


def test_konfig_pflichtcheck_nicht_abschaltbar(tmp_path):
    text = GUELTIG.replace('"ignored_auth",', "").replace(
        "[pruefungen.aus]", '[pruefungen.aus]\nignored_auth = "abgeschaltet, das darf nicht gehen"')
    with pytest.raises(NachweisFehler) as e:
        fuzzing.lade_konfig(_konfig(tmp_path, text))
    assert e.value.regel == "fuzzing-konfig" and "ignored_auth" in e.value.text


def test_konfig_fehlt(tmp_path):
    with pytest.raises(NachweisFehler) as e:
        fuzzing.lade_konfig(tmp_path / "fehlt.toml")
    assert e.value.regel == "fuzzing-konfig"


# --- Schema, Ausschlüsse -------------------------------------------------------------------------

SCHEMA: dict[str, Any] = {"paths": {
    "/api/v1/projekte": {"get": {}, "parameters": []},
    "/api/v1/admin/nutzer/{uid}/sperren": {"post": {}},
    "/api/v1/konto/tokens/{tid}": {"delete": {}, "summary": "x"},
    "/auth/abmelden": {"post": {}},
}}


def test_schema_operationen_nur_api_v1_und_methoden():
    assert fuzzing.schema_operationen(SCHEMA) == {
        "GET /api/v1/projekte", "POST /api/v1/admin/nutzer/{uid}/sperren",
        "DELETE /api/v1/konto/tokens/{tid}"}


def test_schema_operationen_kaputt():
    with pytest.raises(NachweisFehler) as e:
        fuzzing.schema_operationen({"paths": []})
    assert e.value.regel == "fuzzing-schema"


def test_ausgeschlossen_je_identitaet(tmp_path):
    k = fuzzing.lade_konfig(_konfig(tmp_path))
    ops = fuzzing.schema_operationen(SCHEMA)
    assert fuzzing.ausgeschlossen(ops, k, "admin") == {"POST /api/v1/admin/nutzer/{uid}/sperren"}
    assert fuzzing.ausgeschlossen(ops, k, "betrachter") == set()


def test_ausschluss_beide_gilt_fuer_jede_identitaet(tmp_path):
    k = fuzzing.lade_konfig(_konfig(tmp_path, GUELTIG.replace('"admin"', '"beide"')))
    ops = fuzzing.schema_operationen(SCHEMA)
    assert fuzzing.ausgeschlossen(ops, k, "betrachter") == {
        "POST /api/v1/admin/nutzer/{uid}/sperren"}


# --- JUnit-Auswertung ----------------------------------------------------------------------------

def _junit(*faelle: str, kopf: str = '<?xml version="1.0" encoding="utf-8"?>') -> str:
    return (f'{kopf}\n<testsuites><testsuite name="schemathesis">{"".join(faelle)}'
            "</testsuite></testsuites>")


GRUEN = '<testcase name="GET /api/v1/projekte" time="0.1"/>'
ROT = ('<testcase name="GET /api/v1/kaputt" time="0.1"><failure type="failure">1. Test Case ID: x'
       "\n\n- Server error\n\n[500] Internal Server Error</failure></testcase>")


def test_junit_gruen():
    geprueft, fehler = fuzzing.werte_junit_aus(_junit(GRUEN, '<testcase name="Stateful tests"/>'))
    assert geprueft == {"GET /api/v1/projekte"} and fehler == []


def test_junit_mit_fehler_blockiert():
    geprueft, fehler = fuzzing.werte_junit_aus(_junit(GRUEN, ROT))
    assert geprueft == {"GET /api/v1/projekte", "GET /api/v1/kaputt"}
    assert len(fehler) == 1 and fehler[0].startswith("GET /api/v1/kaputt: ")
    assert "Server error" in fehler[0]


def test_junit_absturz_und_stateful_fehler_zaehlen():
    absturz = ('<testcase name="POST /api/v1/x"><error type="error">Traceback: kaputt</error>'
               "</testcase>")
    stateful = '<testcase name="Stateful tests"><failure>use after free</failure></testcase>'
    geprueft, fehler = fuzzing.werte_junit_aus(_junit(absturz, stateful))
    assert geprueft == {"POST /api/v1/x"}
    assert [f.split(":")[0] for f in fehler] == ["POST /api/v1/x", "Stateful tests"]


def test_junit_uebersprungen_ist_nicht_geprueft():
    geprueft, fehler = fuzzing.werte_junit_aus(_junit(
        GRUEN, '<testcase name="GET /api/v1/b"><skipped>kein Beispiel</skipped></testcase>'))
    assert geprueft == {"GET /api/v1/projekte"} and fehler == []


@pytest.mark.parametrize("text", [
    "kein xml",
    _junit(GRUEN, kopf='<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]>'),
    "<andere/>",
])
def test_junit_kaputt(text):
    with pytest.raises(NachweisFehler) as e:
        fuzzing.werte_junit_aus(text)
    assert e.value.regel == "fuzzing-ausgabe"


# --- Gate ----------------------------------------------------------------------------------------

def _ergebnis(**kw: Any) -> fuzzing.FuzzErgebnis:
    werte: dict[str, Any] = {"identitaet": "admin", "operationen_schema": 4,
                             "operationen_geprueft": 3, "ausgeschlossen": 1, "fehler": []}
    werte.update(kw)
    return fuzzing.FuzzErgebnis(**werte)


def test_gate_frei():
    assert fuzzing.fuzzing_gate([_ergebnis(), _ergebnis(identitaet="betrachter",
                                                        ausgeschlossen=0, operationen_geprueft=4)]
                                ) == []


def test_gate_blockiert_je_fehler():
    blockiert = fuzzing.fuzzing_gate([_ergebnis(fehler=["GET /api/v1/kaputt: Server error"])])
    assert blockiert == ["admin: GET /api/v1/kaputt: Server error"]


def test_gate_abdeckung_zu_klein():
    with pytest.raises(NachweisFehler) as e:
        fuzzing.fuzzing_gate([_ergebnis(operationen_geprueft=2)])
    assert e.value.regel == "fuzzing-abdeckung" and "2 von 3" in e.value.text


def test_gate_schema_ohne_operationen():
    with pytest.raises(NachweisFehler) as e:
        fuzzing.fuzzing_gate([_ergebnis(operationen_schema=0, operationen_geprueft=0,
                                        ausgeschlossen=0)])
    assert e.value.regel == "fuzzing-abdeckung"


def test_gate_ohne_ergebnisse():
    with pytest.raises(NachweisFehler) as e:
        fuzzing.fuzzing_gate([])
    assert e.value.regel == "fuzzing-abdeckung"


# --- Bereinigung ---------------------------------------------------------------------------------

def test_bereinigung_text_har_junit(tmp_path):
    ordner = tmp_path / "aus"
    ordner.mkdir()
    (ordner / "a.xml").write_text(_junit(
        f'<testcase name="GET /x"><failure>curl -H "Cookie: __Host-scanview={COOKIE}"'
        f"</failure></testcase>"), encoding="utf-8")
    har = {"log": {"entries": [{"request": {"headers": [
        {"name": "Authorization", "value": f"Bearer {TOKEN}"},
        {"name": "Cookie", "value": f"__Host-scanview={COOKIE}"}]}}]}}
    (ordner / "b.har").write_text(json.dumps(har), encoding="utf-8")
    (ordner / "c.txt").write_text(f"{TOKEN} und {COOKIE}", encoding="utf-8")
    fuzzing.bereinige(ordner, [COOKIE, TOKEN])
    for datei in ordner.iterdir():
        text = datei.read_text(encoding="utf-8")
        assert COOKIE not in text and TOKEN not in text
        assert fuzzing.ENTFERNT in text
    json.loads((ordner / "b.har").read_text(encoding="utf-8"))
    fuzzing.werte_junit_aus((ordner / "a.xml").read_text(encoding="utf-8"))


def test_restgeheimnis_loescht_berichte(tmp_path):
    ordner = tmp_path / "aus"
    ordner.mkdir()
    # Nicht per Textersetzung erreichbar (UTF-16) — die Suche über die Bytes findet es trotzdem.
    (ordner / "a.bin").write_bytes(COOKIE.encode("utf-16-le"))
    (ordner / "b.xml").write_text("sauber", encoding="utf-8")
    fuzzing.bereinige(ordner, [COOKIE])
    with pytest.raises(NachweisFehler) as e:
        fuzzing.pruefe_geheimnisfrei(ordner, [COOKIE])
    assert e.value.regel == "fuzzing-geheimnis"
    assert COOKIE not in e.value.text
    assert list(ordner.iterdir()) == []


def test_geheimnisfrei_leere_werte_verboten(tmp_path):
    with pytest.raises(NachweisFehler):
        fuzzing.bereinige(tmp_path, [""])


# --- Ports ---------------------------------------------------------------------------------------

def test_port_belegt():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        belegt = s.getsockname()[1]
        with pytest.raises(NachweisFehler) as e:
            fuzzing.pruefe_ports_frei((belegt,))
    assert e.value.regel == "fuzzing-port" and str(belegt) in e.value.text


def test_port_mit_lauschendem_dienst_bleibt_belegt_auch_mit_reuseaddr():
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen()
        belegt = s.getsockname()[1]
        with pytest.raises(NachweisFehler) as e:
            fuzzing.pruefe_ports_frei((belegt,))
    assert e.value.regel == "fuzzing-port"


def test_port_frei():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        frei = s.getsockname()[1]
    fuzzing.pruefe_ports_frei((frei,))


def test_port_nach_beendeter_verbindung_ist_frei():
    """Fuzzing und Egress laufen in einer Release-Kette direkt nacheinander auf denselben Ports:
    Verbindungen der ersten Instanz stehen danach bis zu einer Minute im Zustand TIME_WAIT, der
    Port ist aber nicht belegt (uvicorn und Caddy binden mit SO_REUSEADDR)."""
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port)) as client:
            verbunden, _ = server.accept()
            verbunden.close()  # der Server schließt zuerst: TIME_WAIT auf dem Serverport
            client.recv(1)
    fuzzing.pruefe_ports_frei((port,))


def test_standardports_gemeinsam_mit_der_wegwerf_instanz():
    # 18095 gehört einem anderen lokalen Dienst; die Ports stehen einmal in werkzeuge.py (auch
    # für den Egress-Test gegen dieselbe Instanz).
    from scanview_nachweise import werkzeuge
    assert (werkzeuge.INSTANZ_CADDY_PORT, werkzeuge.INSTANZ_SERVER_PORT) == (18090, 18097)
    assert (fuzzing.CADDY_PORT, fuzzing.SERVER_PORT) == (18090, 18097)
    assert fuzzing.URL == "http://localhost:18090"


# --- Schemathesis-Konfiguration und Lauf ---------------------------------------------------------

def test_schemathesis_konfig_je_identitaet(tmp_path):
    k = fuzzing.lade_konfig(_konfig(tmp_path))
    admin = fuzzing.schemathesis_konfig(k, "admin", COOKIE)
    assert f'api_key = "{COOKIE}"' in admin and "[auth.openapi.cookieAuth]" in admin
    assert 'Origin = "http://localhost:18090"' in admin and "rate-limit" not in admin
    betrachter = fuzzing.schemathesis_konfig(k, "betrachter", TOKEN)
    assert f'bearer = "{TOKEN}"' in betrachter and "[auth.openapi.bearerAuth]" in betrachter
    assert 'rate-limit = "100/m"' in betrachter
    for text in (admin, betrachter):
        assert "[cache]\nenabled = false" in text
        assert "with-security-parameters = false" in text


@pytest.mark.parametrize("wert", ['a"b', "a\nb", "a${X}", ""])
def test_schemathesis_konfig_verweigert_seltsame_anmeldewerte(tmp_path, wert):
    k = fuzzing.lade_konfig(_konfig(tmp_path))
    with pytest.raises(NachweisFehler) as e:
        fuzzing.schemathesis_konfig(k, "admin", wert)
    assert e.value.regel == "fuzzing-anmeldung"


class _Http:
    """Attrappe für `fuzzing._http`: Antworten je (Methode, Pfad), Aufrufe mitgeschnitten."""

    def __init__(self, antworten: dict[tuple[str, str], tuple[int, dict[str, str], bytes]]):
        self.antworten = antworten
        self.aufrufe: list[tuple[str, str, dict[str, str]]] = []

    def __call__(self, methode: str, url: str, kopf: dict[str, str],
                 daten: bytes | None = None) -> tuple[int, Any, bytes]:
        pfad = url.split("//", 1)[1].split("/", 1)[1]
        self.aufrufe.append((methode, "/" + pfad, kopf))
        return self.antworten[(methode, "/" + pfad)]


def _instanz(tmp_path: Path) -> fuzzing.Testinstanz:
    ordner = tmp_path / "arbeit"
    ordner.mkdir()
    return fuzzing.Testinstanz(url=fuzzing.URL, admin_cookie=COOKIE, betrachter_token=TOKEN,
                               arbeitsordner=ordner)


def _nachweise_mit_venv(tmp_path: Path) -> Path:
    nachweise = tmp_path / "wt" / "nachweise"
    ordner = nachweise / "werkzeuge" / "schemathesis"
    (ordner / ".venv").mkdir(parents=True)
    (ordner / "uv.lock").write_text("lock", encoding="utf-8")
    w = _werkzeuge()["schemathesis"]
    (ordner / ".venv" / fuzzing.MARKE).write_text(
        json.dumps(fuzzing.schemathesis_marke(w, ordner), sort_keys=True), encoding="utf-8")
    return nachweise


def test_schemathesis_laufen_ohne_geheimnis_in_argv(tmp_path, monkeypatch):
    nachweise = _nachweise_mit_venv(tmp_path)
    schema = {"paths": {"/api/v1/projekte": {"get": {}},
                        "/api/v1/admin/nutzer/{uid}/sperren": {"post": {}}}}
    http = _Http({("GET", "/api/v1/openapi.json"): (200, {}, json.dumps(schema).encode())})
    monkeypatch.setattr(fuzzing, "_http", http)
    gesehen: dict[str, Any] = {}

    def laeufer(befehl: list[str]) -> subprocess.CompletedProcess[str]:
        gesehen["befehl"] = befehl
        konfig = next(a.split(":")[0] for a in befehl
                      if a.endswith(":/konfig/schemathesis.toml:ro"))
        gesehen["modus"] = stat.S_IMODE(os.stat(konfig).st_mode)
        gesehen["konfig"] = Path(konfig).read_text(encoding="utf-8")
        aus = next(a.split(":")[0] for a in befehl if a.endswith(":/aus:rw"))
        (Path(aus) / "junit.xml").write_text(_junit(
            GRUEN, f'<testcase name="X"><failure>Cookie: {COOKIE}</failure></testcase>'),
            encoding="utf-8")
        return subprocess.CompletedProcess(befehl, 1, "", "")

    inst = _instanz(tmp_path)
    k = fuzzing.lade_konfig(_konfig(tmp_path))
    ziel = tmp_path / "berichte" / "fuzzing-admin.xml"
    e = fuzzing.schemathesis_laufen(inst, "admin", k, _werkzeuge(), ziel, nachweise=nachweise,
                                    laeufer=laeufer)
    assert (e.operationen_schema, e.ausgeschlossen, e.operationen_geprueft) == (2, 1, 1)
    assert e.fehler and COOKIE not in "".join(e.fehler)
    # Anmeldung beim Schemaabruf, Wert nie auf der Befehlszeile
    assert http.aufrufe[0][2]["Cookie"] == f"__Host-scanview={COOKIE}"
    assert not any(COOKIE in a for a in gesehen["befehl"])
    assert gesehen["modus"] == 0o600 and COOKIE in gesehen["konfig"]
    befehl = gesehen["befehl"]
    assert befehl[befehl.index("--network") + 1] == "host"
    for flag, wert in (("--seed", "7"), ("--max-examples", "3"), ("--url", fuzzing.URL),
                       ("--include-path-regex", "^/api/v1/")):
        assert befehl[befehl.index(flag) + 1] == wert
    assert "not_a_server_error" in befehl[befehl.index("--checks") + 1]
    assert "status_code_conformance" not in befehl[befehl.index("--checks") + 1]
    assert befehl[befehl.index("--exclude-path-regex") + 1] == \
        "(?:^/api/v1/admin/nutzer/\\{uid\\}/sperren$)"
    # Geheimnisdatei ist weg, Bericht bereinigt abgelegt
    assert not list(inst.arbeitsordner.rglob("schemathesis.toml"))
    text = ziel.read_text(encoding="utf-8")
    assert COOKIE not in text and fuzzing.ENTFERNT in text


def test_schemathesis_laufen_ohne_junit_ist_absturz(tmp_path, monkeypatch):
    nachweise = _nachweise_mit_venv(tmp_path)
    schema = {"paths": {"/api/v1/projekte": {"get": {}}}}
    monkeypatch.setattr(fuzzing, "_http", _Http(
        {("GET", "/api/v1/openapi.json"): (200, {}, json.dumps(schema).encode())}))
    k = fuzzing.lade_konfig(_konfig(tmp_path))
    with pytest.raises(NachweisFehler) as e:
        fuzzing.schemathesis_laufen(
            _instanz(tmp_path), "betrachter", k, _werkzeuge(), tmp_path / "b.xml",
            nachweise=nachweise,
            laeufer=lambda b: subprocess.CompletedProcess(b, 2, "", f"Absturz {TOKEN}"))
    assert e.value.regel == "fuzzing-werkzeug"
    assert TOKEN not in e.value.text


def test_schemaabruf_ohne_anmeldung_ist_fehler(tmp_path, monkeypatch):
    nachweise = _nachweise_mit_venv(tmp_path)
    monkeypatch.setattr(fuzzing, "_http", _Http({("GET", "/api/v1/openapi.json"): (401, {}, b"")}))
    k = fuzzing.lade_konfig(_konfig(tmp_path))
    with pytest.raises(NachweisFehler) as e:
        fuzzing.schemathesis_laufen(_instanz(tmp_path), "admin", k, _werkzeuge(),
                                    tmp_path / "a.xml", nachweise=nachweise,
                                    laeufer=lambda b: pytest.fail("kein Lauf"))
    assert e.value.regel == "fuzzing-schema"


def test_schemathesis_nicht_bereitgestellt(tmp_path):
    k = fuzzing.lade_konfig(_konfig(tmp_path))
    with pytest.raises(NachweisFehler) as e:
        fuzzing.schemathesis_laufen(_instanz(tmp_path), "admin", k, _werkzeuge(),
                                    tmp_path / "a.xml", nachweise=tmp_path / "leer",
                                    laeufer=lambda b: pytest.fail("kein Lauf"))
    assert e.value.regel == "fuzzing-werkzeug"


def test_bereitstellen_ist_protokollierter_netzschritt(tmp_path):
    nachweise = tmp_path / "nachweise"
    ordner = nachweise / "werkzeuge" / "schemathesis"
    ordner.mkdir(parents=True)
    (ordner / "uv.lock").write_text("lock", encoding="utf-8")
    befehle: list[list[str]] = []

    def laeufer(befehl: list[str], regel: str) -> None:
        befehle.append(befehl)
        (ordner / ".venv").mkdir()

    schritte = fuzzing.schemathesis_bereitstellen(nachweise, _werkzeuge(), laeufer=laeufer)
    assert [s.befehl[:3] for s in schritte] == [["uv", "sync", "--locked"]]
    assert "--network" not in befehle[0]
    assert fuzzing.schemathesis_bereitstellen(nachweise, _werkzeuge(), laeufer=laeufer) == []


# --- Sitzungsprobe -------------------------------------------------------------------------------

def test_sitzungsprobe_gueltig(tmp_path, monkeypatch):
    http = _Http({("GET", "/api/v1/openapi.json"): (200, {}, b"{}")})
    monkeypatch.setattr(fuzzing, "_http", http)
    fuzzing.sitzungsprobe(_instanz(tmp_path))
    kopf = [a[2] for a in http.aufrufe]
    assert {"Cookie": f"__Host-scanview={COOKIE}"} in kopf
    assert {"Authorization": f"Bearer {TOKEN}"} in kopf


def test_sitzungsprobe_verloren(tmp_path, monkeypatch):
    antworten = iter([(200, {}, b"{}"), (401, {}, b"")])
    monkeypatch.setattr(fuzzing, "_http", lambda *a, **kw: next(antworten))
    with pytest.raises(NachweisFehler) as e:
        fuzzing.sitzungsprobe(_instanz(tmp_path))
    assert e.value.regel == "fuzzing-sitzung"
    assert "Anmeldung während des Laufs verloren" in e.value.text


# --- Start und Aufräumen der Instanz (Attrappen) -------------------------------------------------

class _Prozess:
    beendet = False

    def __init__(self, *a: object, **kw: object) -> None:
        _Prozess.beendet = False

    def poll(self) -> int | None:
        return 0 if _Prozess.beendet else None

    def terminate(self) -> None:
        _Prozess.beendet = True

    def wait(self, *a: object) -> int:
        return 0

    def kill(self) -> None:
        _Prozess.beendet = True


def _instanz_attrappen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *,
                       einloesen: Any = None, stop_fehler: bool = False) -> list[list[str]]:
    wt = tmp_path / "wt"
    (wt / "web" / "dist").mkdir(parents=True)
    (wt / "web" / "dist" / "portal.html").write_text("x", encoding="utf-8")
    (wt / "server").mkdir()
    (wt / "deploy" / "platform").mkdir(parents=True)
    for name in ("Caddyfile.lokal", "scanview.caddy"):
        (wt / "deploy" / "platform" / name).write_text("x", encoding="utf-8")
    befehle: list[list[str]] = []

    def ausfuehren(befehl: list[str], regel: str, **kw: object) -> subprocess.CompletedProcess[str]:
        befehle.append(befehl)
        if "stop" in befehl and stop_fehler:
            raise NachweisFehler("fuzzing-aufraeumen", "docker stop scheiterte")
        aus = "http://localhost:18090/einladung#t=einladung123\n" if "einladen" in befehl else ""
        return subprocess.CompletedProcess(befehl, 0, aus, "")

    monkeypatch.setattr(fuzzing, "pruefe_ports_frei", lambda ports: None)
    monkeypatch.setattr(fuzzing, "ausfuehren", ausfuehren)
    monkeypatch.setattr(fuzzing, "_warte_bereit", lambda *a: None)
    monkeypatch.setattr(fuzzing.subprocess, "Popen", _Prozess)
    monkeypatch.setattr(fuzzing, "_einloesen", einloesen or (lambda e: "cookiewert"))
    monkeypatch.setattr(fuzzing, "_api_token", lambda c: "tokenwert")
    return befehle


def _gestoppt(befehle: list[list[str]]) -> bool:
    return any(b[1:2] == ["stop"] and b[-1].startswith("scanview-fuzzing-") for b in befehle)


def test_start_scheitert_beim_einloesen_raeumt_auf(tmp_path, monkeypatch):
    def kaputt(einladung: str) -> str:
        raise NachweisFehler("fuzzing-instanz", "Einladung nicht eingelöst")

    befehle = _instanz_attrappen(tmp_path, monkeypatch, einloesen=kaputt)
    with pytest.raises(NachweisFehler) as e:
        fuzzing.starte_testinstanz(tmp_path / "wt", tmp_path / "arbeit", _werkzeuge())
    assert e.value.regel == "fuzzing-instanz"
    assert _Prozess.beendet and _gestoppt(befehle)
    caddy = next(b for b in befehle if "caddy" in b)
    assert caddy[caddy.index("--network") + 1] == "host" and "-d" in caddy


def test_ausnahme_im_lauf_raeumt_auf_und_bleibt_sichtbar(tmp_path, monkeypatch):
    befehle = _instanz_attrappen(tmp_path, monkeypatch)
    inst = fuzzing.starte_testinstanz(tmp_path / "wt", tmp_path / "arbeit", _werkzeuge())
    assert (inst.admin_cookie, inst.betrachter_token) == ("cookiewert", "tokenwert")
    assert len(inst.netzschritte) == 1
    with pytest.raises(RuntimeError), inst:
        raise RuntimeError("Lauf kaputt")
    assert _Prozess.beendet and _gestoppt(befehle)
    # Arbeitsordner bleibt für den Bericht
    assert (tmp_path / "arbeit" / "fuzzing" / "state").is_dir()


def test_aufraeumfehler_ohne_andere_ausnahme_wird_gemeldet(tmp_path, monkeypatch):
    _instanz_attrappen(tmp_path, monkeypatch, stop_fehler=True)
    inst = fuzzing.starte_testinstanz(tmp_path / "wt", tmp_path / "arbeit", _werkzeuge())
    with pytest.raises(NachweisFehler) as e, inst:
        pass
    assert e.value.regel == "fuzzing-aufraeumen"
    assert _Prozess.beendet


def test_start_ohne_web_dist(tmp_path, monkeypatch):
    _instanz_attrappen(tmp_path, monkeypatch)
    for datei in (tmp_path / "wt" / "web" / "dist").iterdir():
        datei.unlink()
    with pytest.raises(NachweisFehler) as e:
        fuzzing.starte_testinstanz(tmp_path / "wt", tmp_path / "arbeit", _werkzeuge())
    assert e.value.regel == "fuzzing-instanz" and "web/dist" in e.value.text


# --- Echte Läufe (Container) ---------------------------------------------------------------------

def _kaputt_starten() -> tuple[subprocess.Popen[str], int]:
    prozess = subprocess.Popen([sys.executable, str(FIXTURES / "kaputt_server.py"), "0",
                                COOKIE, TOKEN],
                               stdout=subprocess.PIPE, text=True)
    assert prozess.stdout is not None
    return prozess, int(prozess.stdout.readline())


@pytest.mark.docker
def test_echt_kaputter_dienst_blockiert(tmp_path):
    if shutil.which("docker") is None:
        pytest.fail("docker fehlt")
    werkzeuge = lade_werkzeuge(WURZEL / "nachweise" / "werkzeuge.json")
    fuzzing.schemathesis_bereitstellen(WURZEL / "nachweise", werkzeuge)
    prozess, port = _kaputt_starten()
    try:
        ordner = tmp_path / "arbeit"
        ordner.mkdir()
        inst = fuzzing.Testinstanz(url=f"http://127.0.0.1:{port}", admin_cookie=COOKIE,
                                   betrachter_token=TOKEN, arbeitsordner=ordner)
        k = fuzzing.lade_konfig(WURZEL / "nachweise" / fuzzing.KONFIG)
        e = fuzzing.schemathesis_laufen(inst, "admin", k, werkzeuge, tmp_path / "k.xml",
                                        nachweise=WURZEL / "nachweise")
    finally:
        prozess.terminate()
        prozess.wait(10)
    assert (e.operationen_schema, e.operationen_geprueft) == (2, 2)
    blockiert = fuzzing.fuzzing_gate([e])
    assert blockiert and all("GET /api/v1/kaputt" in b for b in blockiert), blockiert
    assert COOKIE not in (tmp_path / "k.xml").read_text(encoding="utf-8")


# --- CLI `pruefen fuzzing` -----------------------------------------------------------------------

@pytest.mark.parametrize(("blockiert", "code"), [([], 0), (["admin: GET /x: Server error"], 1)])
def test_cli_pruefen_fuzzing(tmp_path, monkeypatch, capsys, blockiert, code):
    from scanview_nachweise import cli
    gesehen: dict[str, Path] = {}

    def pruefen(worktree: Path, werkzeuge: Any, berichte: Path,
                arbeitsordner: Path) -> fuzzing.Ergebnis:
        gesehen.update(worktree=worktree, berichte=berichte, arbeitsordner=arbeitsordner)
        lauf = {"operationen_schema": 34, "operationen_geprueft": 32, "ausgeschlossen": 2,
                "fehler": blockiert, "hinweise": ["alt"]}
        return fuzzing.Ergebnis({"laeufe": {"admin": lauf}, "dauer_sekunden": 5,
                                 "blockierend": blockiert}, blockiert, [])

    monkeypatch.setattr(fuzzing, "pruefen", pruefen)
    arbeit = tmp_path / "arbeit"
    arbeit.mkdir()
    assert cli.main(["pruefen", "fuzzing", "--wurzel", str(WURZEL), "--arbeitsordner",
                     str(arbeit)]) == code
    aus = capsys.readouterr().out
    assert gesehen == {"worktree": WURZEL, "berichte": arbeit / "berichte",
                       "arbeitsordner": arbeit}
    assert "admin: 32 von 34 Operationen geprüft (2 begründet ausgeschlossen)" in aus
    assert "HINWEIS alt" in aus
    assert json.loads((arbeit / "fuzzing.json").read_text("utf-8"))["blockierend"] == blockiert
    assert ("BLOCKIERT admin: GET /x" in aus) is bool(blockiert)
