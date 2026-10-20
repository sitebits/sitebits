"""Vertrag MCP-Server ↔ API v1 (siehe ADR 0008 — OpenAPI ist die Quelle der Wahrheit).

Die Werkzeuge in mcp-server/ sind von Hand geschrieben; ihre Endpunkt-Tabelle
(`mcp-server/src/scanview_mcp/endpunkte.json`) muss gegen die echte OpenAPI der App bestehen:
jeder Eintrag existiert mit genau dieser Methode, und keiner liegt auf einem Pfad, den ein
Bearer-Token nicht erreicht (Verwaltung, Statistik, Konto).
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from scanview_server.web import bearer_pfad

ENDPUNKTE = (Path(__file__).resolve().parent.parent.parent
             / "mcp-server" / "src" / "scanview_mcp" / "endpunkte.json")
VERBOTEN = ("/api/v1/admin", "/api/v1/statistik", "/api/v1/konto")


def _tabelle() -> list[dict[str, str]]:
    daten: list[dict[str, str]] = json.loads(ENDPUNKTE.read_text(encoding="utf-8"))
    return daten


def test_endpunkte_tabelle_vorhanden_und_eindeutig() -> None:
    tabelle = _tabelle()
    assert len(tabelle) == 21
    namen = [e["werkzeug"] for e in tabelle]
    assert len(set(namen)) == len(namen)


def test_jeder_mcp_endpunkt_existiert_in_der_openapi(client: TestClient) -> None:
    pfade = client.app.openapi()["paths"]  # type: ignore[attr-defined]
    for e in _tabelle():
        pfad, methode = e["pfad"], e["methode"].lower()
        assert pfad in pfade, f"{e['werkzeug']}: {pfad} fehlt in der OpenAPI"
        assert methode in pfade[pfad], f"{e['werkzeug']}: {e['methode']} {pfad} fehlt"


def test_kein_mcp_endpunkt_ausserhalb_des_token_bereichs() -> None:
    for e in _tabelle():
        pfad = e["pfad"]
        assert not pfad.startswith(VERBOTEN), f"{e['werkzeug']}: {pfad} ist nicht für Tokens"
        # Platzhalter durch echte Werte ersetzt — so prüft bearer_pfad es zur Laufzeit.
        konkret = pfad.replace("{pid}", "ost").replace("{eid}", "P-1")
        assert bearer_pfad(konkret), f"{e['werkzeug']}: {pfad} nimmt keinen Bearer an"
