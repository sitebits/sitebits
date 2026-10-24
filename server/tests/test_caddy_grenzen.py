"""Körpergrenzen in `deploy/platform/scanview.caddy` passen zu dem, was der Dienst annimmt (Drift).

Caddy beantwortet einen zu großen Körper selbst mit einem leeren 413; der Dienst sieht die Anfrage
nie. Liegt die Grenze unter dem, was Schema bzw. Protokoll erlauben, scheitern gültige Anfragen
still (Befund B-FUZZ-5: Upload-Ankündigung mit 1 MB gegen ein Schema-Maximum von ≈ 12,8 MiB).
Liegt sie weit darüber, ist die äußere Wand wirkungslos. Geprüft wird deshalb: Grenze ≥ Maximum
und höchstens 1 MiB darüber; die Ausnahmen sind aus der allgemeinen API-Grenze herausgenommen."""
from __future__ import annotations

import json
import re
from pathlib import Path

from scanview_server import uploads as U
from scanview_server.routen_uploads import UploadIn

CADDY = Path(__file__).resolve().parents[2] / "deploy" / "platform" / "scanview.caddy"
MIB = 1024 * 1024
_EINHEIT = {"KB": 1000, "MB": 1000**2, "KIB": 1024, "MIB": MIB}


def _api_block() -> str:
    text = CADDY.read_text(encoding="utf-8")
    start = text.index("handle /api/* {")
    ende = text.index("\n}\n", start)
    return text[start:ende]


def _grenzen(block: str) -> dict[str, int]:
    """`request_body @name { max_size X }` → {name: Bytes}."""
    grenzen: dict[str, int] = {}
    for name, zahl, einheit in re.findall(
            r"request_body @(\w+) \{\s*max_size (\d+)([A-Za-z]+)\s*\}", block):
        grenzen[name] = int(zahl) * _EINHEIT[einheit.upper()]
    return grenzen


def _matcher(block: str, name: str) -> str:
    treffer = re.search(r"@" + name + r" \{\n(.*?)\n\t\}", block, re.S)
    assert treffer, name
    return treffer.group(1)


def ankuendigung_maximum() -> int:
    """Größter Körper, den `UploadIn` erlaubt: maxItems Einträge mit längstem Pfad und größter
    Dateigröße, serialisiert wie `json.dumps` (mit Leerzeichen, die längere Form)."""
    schema = UploadIn.model_json_schema()
    anzahl = schema["properties"]["dateien"]["maxItems"]
    felder = schema["$defs"]["DateiIn"]["properties"]
    eintrag = {"pfad": "a" * felder["pfad"]["maxLength"],
               "groesse": int(felder["groesse"]["maximum"])}
    return len(json.dumps({"dateien": [eintrag] * anzahl}).encode("utf-8"))


def test_ankuendigung_grenze_passt_zum_schema() -> None:
    block = _api_block()
    m = _matcher(block, "upload_ankuendigung")
    assert "method POST" in m and "path /api/v1/uploads\n" in m + "\n"
    grenze = _grenzen(block)["upload_ankuendigung"]
    maximum = ankuendigung_maximum()
    assert maximum <= grenze <= maximum + MIB, (maximum, grenze)


def test_stueck_grenze_passt_zum_protokoll() -> None:
    grenze = _grenzen(_api_block())["upload_stueck"]
    assert U.STUECK < grenze <= U.STUECK + MIB, (U.STUECK, grenze)


def test_ausnahmen_nicht_unter_der_allgemeinen_grenze() -> None:
    block = _api_block()
    grenzen = _grenzen(block)
    assert set(grenzen) == {"upload_stueck", "upload_ankuendigung", "api_klein"}
    assert grenzen["api_klein"] == 1000**2
    klein = _matcher(block, "api_klein")
    for name in ("upload_stueck", "upload_ankuendigung"):
        bedingungen = [z.strip() for z in _matcher(block, name).splitlines()]
        nicht = re.search(r"not \{\n" + r"\n".join(r"\s*" + re.escape(b) for b in bedingungen)
                          + r"\n\s*\}", klein)
        assert nicht, f"@api_klein nimmt @{name} nicht aus"
