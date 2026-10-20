"""Impressum und Datenschutzhinweise je Instanz: nur die Verlinkung (den Rechtstext stellt der
Betreiber), Einstellungen aus der Umgebung, strenge Prüfung beim Start (SystemExit), Auslieferung
über den öffentlichen Endpunkt /api/barrierefreiheit (die Anmeldeseite braucht die Links)."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterator

import pytest
from conftest import ORIGIN
from fastapi.testclient import TestClient

from scanview_server import rechtliches as rl
from scanview_server.config import Settings
from scanview_server.web import create_app

IMPRESSUM = "https://stelle.example.org/impressum"
DATENSCHUTZ = "/datenschutz.html"


def _umgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in {"SCANVIEW_DATA": "/d", "SCANVIEW_STATE": "/s", "SCANVIEW_ORIGIN": ORIGIN,
                 "SCANVIEW_RP_ID": "x"}.items():
        monkeypatch.setenv(k, v)
    for k in ("SCANVIEW_IMPRESSUM_URL", "SCANVIEW_DATENSCHUTZ_URL",
              "SCANVIEW_BARRIEREFREIHEIT_KONTAKT", "SCANVIEW_BARRIEREFREIHEIT_ERKLAERUNG"):
        monkeypatch.delenv(k, raising=False)


# --- Prüfung ------------------------------------------------------------------------------------

@pytest.mark.parametrize("wert", [
    "https://stelle.example.org",
    "https://stelle.example.org/impressum",
    "https://stelle.example.org:8443/recht/impressum?sprache=de#oben",
    "/impressum",
    "/recht/datenschutz.html#viewer",
    "/",
])
def test_gueltige_links(wert: str) -> None:
    assert rl.pruefe_link("SCANVIEW_IMPRESSUM_URL", wert) == wert


@pytest.mark.parametrize("wert", [
    "http://stelle.example.org/impressum",          # kein https
    "javascript:alert(1)",
    "mailto:recht@stelle.example.org",
    "data:text/html,x",
    "https://nutzer:passwort@stelle.example.org/",  # Zugangsdaten
    "https://nutzer@stelle.example.org/",
    "https://",
    "https:///pfad",
    "//boese.example/impressum",                    # protokollrelativ = fremde Origin
    "/\\boese.example/impressum",                   # Browser lesen /\ wie //
    "https://stelle.example.org\\@boese.example/",
    "impressum.html",                               # relativ, hängt von der Seite ab
    "/impressum mit leerzeichen",
    "/impressum\n",
    "/imp\x00ressum",
    "/impressüm",                                   # nur druckbares ASCII
    "/" + "a" * rl.MAX_LAENGE,
])
def test_ungueltige_links(wert: str) -> None:
    with pytest.raises(rl.KonfigFehler, match="SCANVIEW_IMPRESSUM_URL"):
        rl.pruefe_link("SCANVIEW_IMPRESSUM_URL", wert)


# --- Umgebung -----------------------------------------------------------------------------------

def test_ohne_einstellung_warnt_der_start_und_laeuft(monkeypatch: pytest.MonkeyPatch,
                                                    caplog: pytest.LogCaptureFixture) -> None:
    _umgebung(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="scanview.rechtliches"):
        s = Settings.from_env()
    assert s.rechtliches == rl.Rechtliches()
    text = caplog.text
    assert "SCANVIEW_IMPRESSUM_URL" in text
    assert "SCANVIEW_DATENSCHUTZ_URL" in text


def test_einstellung_aus_der_umgebung(monkeypatch: pytest.MonkeyPatch,
                                      caplog: pytest.LogCaptureFixture) -> None:
    _umgebung(monkeypatch)
    monkeypatch.setenv("SCANVIEW_IMPRESSUM_URL", f" {IMPRESSUM} ")
    monkeypatch.setenv("SCANVIEW_DATENSCHUTZ_URL", DATENSCHUTZ)
    with caplog.at_level(logging.WARNING, logger="scanview.rechtliches"):
        r = Settings.from_env().rechtliches
    assert r == rl.Rechtliches(impressum=IMPRESSUM, datenschutz=DATENSCHUTZ)
    assert "SCANVIEW_IMPRESSUM_URL" not in caplog.text
    assert "SCANVIEW_DATENSCHUTZ_URL" not in caplog.text


@pytest.mark.parametrize("variable", ["SCANVIEW_IMPRESSUM_URL", "SCANVIEW_DATENSCHUTZ_URL"])
def test_ungueltige_einstellung_beendet_den_start(monkeypatch: pytest.MonkeyPatch,
                                                 variable: str) -> None:
    _umgebung(monkeypatch)
    monkeypatch.setenv(variable, "//boese.example/")
    with pytest.raises(SystemExit, match=variable):
        Settings.from_env()


# --- Endpunkt -----------------------------------------------------------------------------------

def test_endpunkt_ohne_einstellung(client: TestClient) -> None:
    d = client.get("/api/barrierefreiheit").json()
    assert d["impressum"] is None
    assert d["datenschutz"] is None


@pytest.fixture
def client_mit(settings: Settings) -> Iterator[TestClient]:
    r = rl.Rechtliches(impressum=IMPRESSUM, datenschutz=DATENSCHUTZ)
    app = create_app(dataclasses.replace(settings, rechtliches=r))
    with TestClient(app, base_url=ORIGIN) as c:
        yield c


def test_endpunkt_mit_einstellung_ohne_anmeldung(client_mit: TestClient) -> None:
    r = client_mit.get("/api/barrierefreiheit")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-store"
    d = r.json()
    assert d["impressum"] == IMPRESSUM
    assert d["datenschutz"] == DATENSCHUTZ
