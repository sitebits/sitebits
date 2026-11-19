"""Anleitung für KI-Agenten: eine Quelle (docs/public/agenten-anleitung.md), wortgleich als
MCP-Ressource und im Skill des Clients; die Server-Hinweise verweisen darauf."""
from __future__ import annotations

from pathlib import Path

import anyio
import httpx
from mcp import Client as McpClient

from scanview_mcp import api, server

REPO = Path(__file__).resolve().parents[2]
QUELLE = (REPO / "docs/public/agenten-anleitung.md").read_text(encoding="utf-8")


def _server() -> object:
    c = api.Client("https://scanview.test", "sv_" + "t" * 40,
                   transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={})))
    return server.baue_server(c)


def test_paketkopie_ist_wortgleich_mit_der_quelle() -> None:
    assert server.anleitung() == QUELLE, (
        "mcp-server/src/scanview_mcp/agenten-anleitung.md aus docs/public kopieren")


def test_skill_enthaelt_die_anleitung_ab_den_grundregeln() -> None:
    skill = (REPO / "clients/sitebits/skill/SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: sitebits\n")
    rumpf = QUELLE[QUELLE.index("## Grundregeln"):]
    assert rumpf in skill, "Skill neu erzeugen: Anleitung ab „## Grundregeln“ einsetzen"


def test_ressource_ist_lesbar_und_hinweise_verweisen_darauf() -> None:
    mcp = _server()

    async def ablauf() -> None:
        async with McpClient(mcp) as k:  # type: ignore[arg-type]
            liste = (await k.list_resources()).resources
            assert [str(r.uri) for r in liste] == [server.ANLEITUNG_URI]
            inhalt = (await k.read_resource(server.ANLEITUNG_URI)).contents
            assert [getattr(t, "text", None) for t in inhalt] == [QUELLE]

    anyio.run(ablauf)
    assert server.ANLEITUNG_URI in server.HINWEISE
