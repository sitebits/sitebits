"""Verweise im Paket prüfen, bevor ``verify`` eine Datei öffnet (ADR 0016, Nachtrag 2026-11).

Ein Verweis ist ein paketrelativer Pfad: im Manifest (``url``, ``previewUrl``, ``mobileUrl`` der
Ebenen, ``verify.report``, ``topview.url``, Schema 1 ``cloud.url``/``stations.url``), in
``stations.json`` (Würfelseiten ``panorama.faces`` und das Stufenmuster ``panorama.levelFile``,
``{level}`` durch eine Ziffer ersetzt), in glTF-Dateien (``uri`` von Buffern und Bildern,
``data:`` ausgenommen) und in ``tileset.json`` (``content.uri``/``contents[].uri``, bei
.glb/.gltf-Inhalten auch deren Buffer und Bilder, relativ zur Kachel).

Zulässig ist nur, was auch die Plattform zur Laufzeit annimmt (``teilen._paketpfad``): ASCII
aus ``A-Z, a-z, 0-9 und . _ - /``, relativ, ohne leere, ``.``- oder ``..``-Segmente und ohne
Segmente mit führendem Punkt. Abgelehnt wird damit alles Absolute (``/…``, ``C:…``, ``http:…``),
alles mit ``..`` und alles Kodierte (``%2e``, ``%2f``, Backslash, NUL und andere Steuerzeichen,
Zeichen, die sich unter Unicode-Normalisierung ändern, sonstiges Nicht-ASCII). Der Laufzeitfilter
der Plattform bleibt als zweite Linie bestehen.
"""

from __future__ import annotations

import json
import re
import struct
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any

#: Erlaubte Zeichen eines Verweises (wie ``teilen._REST_RE`` der Plattform).
_ZEICHEN = re.compile(r"[A-Za-z0-9._/-]+")
#: Felder einer Ebene (bzw. der Schema-1-Blöcke), die auf eine Datei im Paket zeigen.
_EBENEN_FELDER = ("url", "previewUrl", "mobileUrl")
#: Obergrenze für den JSON-Teil einer glTF-Datei und für Index-Dateien (wie die Plattform).
_JSON_MAX = 64 * 1024 * 1024
#: Verschachtelte .gltf-Verweise höchstens so tief verfolgen (wie die Plattform).
_GLTF_TIEFE = 4


class VerweisUnzulaessig(ValueError):
    """Ein Verweis im Paket ist absolut, enthält ``..`` oder ist kodiert."""


def _grund(ref: str) -> str | None:
    """Grund der Ablehnung oder None, wenn der Verweis zulässig ist."""
    if not ref:
        return "leer"
    if any(ord(z) < 32 or ord(z) == 127 for z in ref):
        return "kodiert: Steuerzeichen"
    if unicodedata.normalize("NFKC", ref) != ref:
        return "kodiert: ändert sich unter Unicode-Normalisierung"
    if "%" in ref:
        return "kodiert: Prozentkodierung"
    if "\\" in ref:
        return "kodiert: Backslash"
    if ref.startswith("/") or ":" in ref:
        return "absolut"
    segmente = ref.split("/")
    if any(s in ("", ".", "..") for s in segmente):
        return "verlässt das Paket: leeres, '.'- oder '..'-Segment"
    if not _ZEICHEN.fullmatch(ref):
        return "kodiert: Zeichen außerhalb von A-Z, a-z, 0-9 und . _ - /"
    if any(s.startswith(".") for s in segmente):
        return "Segment mit führendem Punkt"
    return None


def pruefe_verweis(ref: object, wo: str) -> str:
    """Gibt den zulässigen Verweis zurück, sonst ``VerweisUnzulaessig``."""
    if not isinstance(ref, str):
        raise VerweisUnzulaessig(f"Verweis unzulässig (kein Text) in {wo}")
    grund = _grund(ref)
    if grund is not None:
        raise VerweisUnzulaessig(f"Verweis unzulässig ({grund}) in {wo}: {ascii(ref)[:120]}")
    return ref


def _relativ(basis: str, ref: str) -> str:
    """Verweis `ref` aus der Datei `basis` (paketrelativ) als paketrelativer Pfad; geprüft ist
    `ref` schon, `..` kann also nicht vorkommen."""
    ordner = basis.rsplit("/", 1)[0] if "/" in basis else ""
    return f"{ordner}/{ref}" if ordner else ref


def _json_datei(datei: Path) -> Any:
    """JSON einer Index-Datei oder None (fehlt, zu groß, kaputt: das melden andere Prüfungen)."""
    try:
        if not datei.is_file() or datei.stat().st_size > _JSON_MAX:
            return None
        return json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _gltf_json(datei: Path) -> Any:
    """JSON-Teil einer .glb- oder .gltf-Datei oder None."""
    if datei.suffix.lower() == ".gltf":
        return _json_datei(datei)
    try:
        with datei.open("rb") as f:
            kopf = f.read(20)
            if len(kopf) < 20 or kopf[:4] != b"glTF":
                return None
            laenge, art = struct.unpack("<I4s", kopf[12:20])
            if art != b"JSON" or laenge > _JSON_MAX:
                return None
            return json.loads(f.read(laenge).decode("utf-8"))
    except (OSError, ValueError, struct.error):
        return None


def _manifest_verweise(m: dict[str, Any]) -> Iterator[tuple[str, object]]:
    """(Ort, Verweis) aller Dateiverweise im Manifest."""
    bloecke: list[tuple[str, Any]] = [
        (f"layers[{i}]", ebene) for i, ebene in enumerate(m.get("layers") or [])
    ]
    bloecke += [(name, m.get(name)) for name in ("cloud", "stations", "topview")]
    for ort, block in bloecke:
        if not isinstance(block, dict):
            continue
        for feld in _EBENEN_FELDER:
            if feld in block:
                yield f"project.json {ort}.{feld}", block[feld]
        pruefung = block.get("verify")
        if isinstance(pruefung, dict) and "report" in pruefung:
            yield f"project.json {ort}.verify.report", pruefung["report"]


def _gltf_verweise(pkg: Path, pfad: str, tiefe: int = 0) -> None:
    if tiefe > _GLTF_TIEFE:
        raise VerweisUnzulaessig(f"Verweis unzulässig (glTF-Verweise tiefer als {_GLTF_TIEFE} "
                                 f"Ebenen) in {pfad}")
    daten = _gltf_json(pkg / pfad)
    if not isinstance(daten, dict):
        return
    for feld in ("buffers", "images"):
        eintraege = daten.get(feld)
        for i, e in enumerate(eintraege if isinstance(eintraege, list) else []):
            uri = e.get("uri") if isinstance(e, dict) else None
            if uri is None or (isinstance(uri, str) and uri.startswith("data:")):
                continue
            ziel = _relativ(pfad, pruefe_verweis(uri, f"{pfad} {feld}[{i}].uri"))
            if ziel.lower().endswith((".gltf", ".glb")):
                _gltf_verweise(pkg, ziel, tiefe + 1)


def _tile_verweise(pkg: Path, pfad: str, kachel: Any) -> None:
    """Inhaltsverweise einer Kachel und aller Kinder (iterativ, Tiefe beliebig). Zeigt ein Inhalt
    auf eine .glb/.gltf, werden auch deren Buffer- und Bildverweise geprüft, relativ zur Kachel
    (wie die Plattform: ``teilen._tileset_dateien`` → ``_gltf_dateien``)."""
    offen = [kachel]
    while offen:
        k = offen.pop()
        if not isinstance(k, dict):
            continue
        inhalte = [k.get("content")] + list(k.get("contents") or [])
        for inhalt in inhalte:
            if isinstance(inhalt, dict) and "uri" in inhalt:
                ziel = _relativ(pfad, pruefe_verweis(inhalt["uri"], f"{pfad} content.uri"))
                if ziel.lower().endswith((".gltf", ".glb")):
                    _gltf_verweise(pkg, ziel)
        kinder = k.get("children")
        offen.extend(kinder if isinstance(kinder, list) else [])


def _stations_verweise(pfad: str, daten: Any) -> None:
    stationen = daten.get("stations") if isinstance(daten, dict) else None
    for i, s in enumerate(stationen if isinstance(stationen, list) else []):
        pano = s.get("panorama") if isinstance(s, dict) else None
        seiten = pano.get("faces") if isinstance(pano, dict) else None
        for seite, ref in (seiten.items() if isinstance(seiten, dict) else []):
            pruefe_verweis(ref, f"{pfad} stations[{i}].panorama.faces.{seite}")
        if isinstance(pano, dict) and "levelFile" in pano:
            _level_file(pano["levelFile"], f"{pfad} stations[{i}].panorama.levelFile")


def _level_file(ref: object, wo: str) -> None:
    """``levelFile`` ist ein Muster mit ``{level}`` (Plattform: ``freigabe._LEVEL_FILE_RE``);
    geprüft wird es mit einer Ziffer eingesetzt — nach derselben Regel wie jeder Verweis."""
    if not isinstance(ref, str):
        pruefe_verweis(ref, wo)
        return
    grund = _grund(ref.replace("{level}", "1"))
    if grund is not None:
        raise VerweisUnzulaessig(f"Verweis unzulässig ({grund}) in {wo}: {ascii(ref)[:120]}")


def pruefe_paket(pkg: Path, m: dict[str, Any]) -> None:
    """Alle Verweise des Pakets prüfen; der erste unzulässige bricht mit
    ``VerweisUnzulaessig`` ab. Dateien werden erst nach geprüftem Verweis geöffnet."""
    for wo, ref in _manifest_verweise(m):
        pruefe_verweis(ref, wo)
    for ebene in m.get("layers") or []:
        if not isinstance(ebene, dict):
            continue
        art = ebene.get("kind")
        if art == "mesh":
            for feld in _EBENEN_FELDER:
                if isinstance(ebene.get(feld), str):
                    _gltf_verweise(pkg, ebene[feld])
        elif art == "mesh-tiles" and isinstance(ebene.get("url"), str):
            tileset = _json_datei(pkg / ebene["url"])
            if isinstance(tileset, dict):
                _tile_verweise(pkg, ebene["url"], tileset.get("root"))
        elif art == "panoramas" and isinstance(ebene.get("url"), str):
            _stations_verweise(ebene["url"], _json_datei(pkg / ebene["url"]))
    stations = m.get("stations")
    if isinstance(stations, dict) and isinstance(stations.get("url"), str):
        _stations_verweise(stations["url"], _json_datei(pkg / stations["url"]))
