"""Retests der Lock-Hebungen: jede Lockdatei trägt genau eine Version des Pakets, und sie liegt
hinter der Korrektur. Liest nur die Lockdateien im Repo (kein Netz, keine Umgebung)."""

import tomllib
from pathlib import Path

import pytest

_WURZEL = Path(__file__).resolve().parents[2]


def _lock_versionen(teil: str) -> dict[str, list[str]]:
    lock = tomllib.loads((_WURZEL / teil / "uv.lock").read_text("utf-8"))
    versionen: dict[str, list[str]] = {}
    for p in lock["package"]:
        versionen.setdefault(p["name"], []).append(p["version"])
    return versionen


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in text.split("."))


# GHSA-6q48-6c7w-9gw2 (kritisch, kompromittierte emSign-Zwischenzertifikate) betrifft
# certifi < 2026.10.19. Server: nur über httpx in der dev-Gruppe, nicht ausgeliefert;
# schemathesis: Werkzeug der Nachweiskette (Stufe fuzzing), nicht ausgeliefert.
@pytest.mark.parametrize("teil", ["server", "nachweise/werkzeuge/schemathesis"])
def test_certifi_hinter_ghsa_6q48(teil):
    (certifi,) = _lock_versionen(teil)["certifi"]
    assert _version(certifi) >= (2026, 10, 19), teil


# opentelemetry-api (transitiv über mcp im MCP-Server, ohne SDK): Ein-Paket-Hebung auf ≥ 1.45.1.
def test_opentelemetry_api_im_mcp_server_gehoben():
    (otel,) = _lock_versionen("mcp-server")["opentelemetry-api"]
    assert _version(otel) >= (1, 45, 1)
