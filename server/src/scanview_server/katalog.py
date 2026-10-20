"""Paketkatalog: findet Pakete unter data_dir und macht aus project.json (Schema 2) eine Karte.

Pakete sind fremde, schreibgeschützte Daten: nur lesen, strikt prüfen, bei Fehlern das Paket
auslassen und den Grund loggen — nie den Dienst stoppen. Symlinks sind verboten (Caddy liefert
unter data_dir aus, ein Link könnte die Wurzel verlassen).
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import genauigkeit, koordinaten

log = logging.getLogger("scanview.katalog")
PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_WKT_NAME_RE = re.compile(r'^\s*[A-Z_]+\[\s*"([^"]+)"')
# project.json ist ein kleines Manifest (echte Pakete: wenige KB); größere gar nicht erst lesen.
MAX_PROJECT_JSON = 5 * 1024 * 1024


@dataclass(frozen=True)
class Karte:
    id: str
    titel: str
    punkte: int | None
    standpunkte: int | None
    ausdehnung: tuple[float, float, float] | None
    crs: str
    scandatum: str | None
    groesse_bytes: int
    draufsicht: bool
    revision: int | None = None
    # Einheit der Koordinatenspalten in Exporten (koordinaten.einheit, F23).
    einheit: str = "[m]"
    # Genauigkeitsnachweis je Punktwolke/Mesh (genauigkeit.ebenen, F15).
    genauigkeit: tuple[genauigkeit.EbeneGenauigkeit, ...] = ()


class PaketFehler(Exception):
    pass


def _groesse_ohne_links(d: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(d, followlinks=False):
        for name in dirs + files:
            p = Path(root) / name
            if p.is_symlink():
                raise PaketFehler(f"Symlink {p.relative_to(d)} nicht erlaubt")
        for name in files:
            total += (Path(root) / name).stat().st_size
    return total


def _karte(d: Path) -> Karte:
    groesse = (d / "project.json").stat().st_size
    if groesse > MAX_PROJECT_JSON:
        raise PaketFehler(f"project.json zu groß ({groesse} Byte)")
    try:
        m: Any = json.loads((d / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PaketFehler(f"project.json nicht lesbar: {e}") from e
    if not isinstance(m, dict):
        raise PaketFehler("project.json ist kein Objekt")
    if m.get("schemaVersion") != 2:
        raise PaketFehler(f"Schema {m.get('schemaVersion')!r} statt 2 (erst migrieren)")
    pid = m.get("projectId")
    if not isinstance(pid, str) or not PROJECT_ID_RE.fullmatch(pid):
        raise PaketFehler(f"projectId {pid!r} ungültig")
    if pid != d.name:
        raise PaketFehler(f"Ordner {d.name!r} passt nicht zu projectId {pid!r}")
    layers_roh = m.get("layers")
    layers: list[Any] = layers_roh if isinstance(layers_roh, list) else []
    cloud = next((x for x in layers if isinstance(x, dict) and x.get("kind") == "pointcloud"), None)
    panos = next((x for x in layers if isinstance(x, dict) and x.get("kind") == "panoramas"), None)
    ausdehnung = None
    if cloud and isinstance(cloud.get("boundsWorld"), list) and len(cloud["boundsWorld"]) == 2:
        lo, hi = cloud["boundsWorld"]
        ausdehnung = (float(hi[0]) - float(lo[0]), float(hi[1]) - float(lo[1]),
                      float(hi[2]) - float(lo[2]))
    wkt = ((m.get("frame") or {}).get("crs") or {}).get("wkt")
    crs = "lokal"
    if isinstance(wkt, str) and wkt.strip():
        hit = _WKT_NAME_RE.match(wkt)
        crs = hit.group(1) if hit else "georeferenziert"
    titel_roh = m.get("title")
    titel = titel_roh if isinstance(titel_roh, str) and titel_roh.strip() else pid
    scandatum_roh = m.get("scanDate")
    scandatum = scandatum_roh if isinstance(scandatum_roh, str) else None
    topview = m.get("topview") if isinstance(m.get("topview"), dict) else None
    punkte = None
    if cloud and isinstance(cloud.get("pointCount"), int):
        punkte = int(cloud["pointCount"])
    standpunkte = None
    if panos and isinstance(panos.get("count"), int):
        standpunkte = int(panos["count"])
    revision_roh = m.get("revision")
    revision = (
        revision_roh if isinstance(revision_roh, int) and not isinstance(revision_roh, bool)
        else None
    )
    return Karte(
        id=pid,
        titel=titel,
        punkte=punkte,
        standpunkte=standpunkte,
        ausdehnung=ausdehnung,
        crs=crs,
        einheit=koordinaten.einheit((m.get("frame") or {}).get("crs")),
        scandatum=scandatum,
        groesse_bytes=_groesse_ohne_links(d),
        draufsicht=bool(topview) and (d / "topview.png").is_file(),
        revision=revision,
        genauigkeit=genauigkeit.ebenen(layers),
    )


class Katalog:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._karten: dict[str, Karte] = {}

    def laden(self) -> None:
        neu: dict[str, Karte] = {}
        for d in sorted(self.data_dir.iterdir()) if self.data_dir.is_dir() else []:
            if d.is_symlink() or not d.is_dir() or not PROJECT_ID_RE.fullmatch(d.name):
                if d.is_dir() and not d.name.startswith(("_", ".")):
                    log.warning("Paket %s übersprungen: Ordnername ungültig oder Symlink", d.name)
                continue
            if not (d / "project.json").is_file():
                continue
            try:
                neu[d.name] = _karte(d)
            except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError,
                    RecursionError, OverflowError, PaketFehler) as e:
                # Eingabe- und Dateifehler eines einzelnen Pakets (auch RecursionError aus tief
                # verschachteltem JSON, WB-N4) lässt nur dieses Paket aus — nie den Dienststart
                # oder das Neuladen scheitern.
                log.warning("Paket %s übersprungen: %s: %s", d.name, type(e).__name__, e)
        self._karten = neu
        log.info("Katalog: %d Pakete", len(neu))

    def get(self, pid: str) -> Karte | None:
        return self._karten.get(pid)

    def alle(self) -> list[Karte]:
        return sorted(self._karten.values(), key=lambda k: k.titel.lower())
