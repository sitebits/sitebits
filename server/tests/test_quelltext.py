"""Quelltext-Link (EUPL-1.2): Die Adresse ist eine Instanz-Einstellung (`SCANVIEW_QUELLTEXT_URL`)
und kommt über `/api/sitzung` ins Portal; ohne Einstellung liefert der Server `null` und das
Portal zeigt keinen Link. Keine Adresse steht fest im Code."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator

import pytest
from conftest import ORIGIN
from fastapi.testclient import TestClient
from nutzer import person

from scanview_server.config import Settings
from scanview_server.web import create_app

URL = "https://quelltext.example.org/scanview"


def _umgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in {"SCANVIEW_DATA": "/d", "SCANVIEW_STATE": "/s", "SCANVIEW_ORIGIN": ORIGIN,
                 "SCANVIEW_RP_ID": "x"}.items():
        monkeypatch.setenv(k, v)


def test_ohne_einstellung_kein_link(monkeypatch: pytest.MonkeyPatch) -> None:
    _umgebung(monkeypatch)
    monkeypatch.delenv("SCANVIEW_QUELLTEXT_URL", raising=False)
    assert Settings.from_env().quelltext_url is None
    monkeypatch.setenv("SCANVIEW_QUELLTEXT_URL", "  ")
    assert Settings.from_env().quelltext_url is None


def test_einstellung_wird_gelesen(monkeypatch: pytest.MonkeyPatch) -> None:
    _umgebung(monkeypatch)
    monkeypatch.setenv("SCANVIEW_QUELLTEXT_URL", f" {URL} ")
    assert Settings.from_env().quelltext_url == URL


@pytest.mark.parametrize("falsch", [
    "http://quelltext.example.org/x",   # kein HTTPS
    "javascript:alert(1)",               # kein Link, sondern Skript
    "https://",                          # ohne Host
    "https://a b.example/x",             # Leerzeichen
    "//quelltext.example.org/x",         # ohne Schema
])
def test_ungueltige_einstellung_bricht_start_ab(monkeypatch: pytest.MonkeyPatch,
                                                falsch: str) -> None:
    _umgebung(monkeypatch)
    monkeypatch.setenv("SCANVIEW_QUELLTEXT_URL", falsch)
    with pytest.raises(SystemExit):
        Settings.from_env()


def test_sitzung_ohne_quelltext_url(client: TestClient) -> None:
    _, c = person(client, "b@b.de")
    for pfad in ("/api/sitzung", "/api/v1/sitzung"):
        assert c.get(pfad).json()["quelltext_url"] is None


@pytest.fixture
def client_mit_url(settings: Settings) -> Iterator[TestClient]:
    app = create_app(dataclasses.replace(settings, quelltext_url=URL))
    with TestClient(app, base_url=ORIGIN) as c:
        yield c


def test_sitzung_mit_quelltext_url(client_mit_url: TestClient) -> None:
    _, c = person(client_mit_url, "b@b.de")
    daten = c.get("/api/sitzung").json()
    assert daten["quelltext_url"] == URL
    assert daten["nutzer"]["email"] == "b@b.de"
    # Ohne Sitzung gibt es auch die Adresse nicht (401 wie bisher).
    assert client_mit_url.get("/api/sitzung").status_code == 401
