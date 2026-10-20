"""project.json Schema 1 und 2 — Lesen, Schreiben, Zugriffe, Migration.

Pakete werden nie stillschweigend überschrieben: ``migrate`` sichert ``project.v1.json`` und
bricht ab, wenn es die Sicherung schon gibt.

Schema 1 hat eine feste ``cloud``/``stations``-Struktur; Schema 2 trägt beliebige ``layers[]``.
Alle Zugriffshelfer hier unterscheiden strukturell (Feld ``layers`` vorhanden?), nicht über das
``schemaVersion``-Feld — so funktionieren sie auch für die im Import-Code selbst gebauten
Zwischenstände (z. B. ``convert()`` vor ``to_v2``), die ``schemaVersion`` schon tragen, aber noch
kein ``layers`` haben.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

SCHEMA_VERSION = 2
CLOUD_LAYER_ID = "punkte"
STATIONS_LAYER_ID = "standpunkte"
IMAGES_LAYER_ID = "fotos"

MANIFEST_NAME = "project.json"
V1_BACKUP_NAME = "project.v1.json"

# Alle Felder, die Schema 1 oben kennt (Vorgabe) — alles andere lehnt ``to_v2`` ab,
# statt es stillschweigend zu verwerfen.
_KNOWN_V1_TOP_FIELDS = {
    "schemaVersion",
    "projectId",
    "revision",
    "source",
    "crs",
    "originWorld",
    "cloud",
    "stations",
    "initialView",
    "conversion",
    "topview",
}


def read_manifest(pkg: Path) -> dict[str, Any]:
    """Liest ``<pkg>/project.json``, egal ob Schema 1 oder 2."""
    return cast(dict[str, Any], json.loads((pkg / MANIFEST_NAME).read_text()))


def write_manifest(pkg: Path, m: dict[str, Any]) -> None:
    """Schreibt ``<pkg>/project.json``. ``allow_nan=False`` als Netz (wie ``verify``s Report):
    nicht-endliche Werte sollen laut brechen statt als ungültiges JSON zu landen.

    Schema 2: ``frame.undulation`` (Zweithöhe, F23) wird hier für jeden Importweg aus Höhensystem,
    Koordinatensystem und Ausdehnung neu bestimmt und fällt weg, wenn das nicht (mehr) geht."""
    frame = m.get("frame")
    if isinstance(frame, dict):
        from . import hoehen  # pyproj erst, wenn ein Schema-2-Paket geschrieben wird

        u = hoehen.undulation(frame, m.get("layers") or [])
        if u is None:
            frame.pop("undulation", None)
        else:
            frame["undulation"] = u
    (pkg / MANIFEST_NAME).write_text(json.dumps(m, indent=2, allow_nan=False))


def cloud_of(m: dict[str, Any]) -> dict[str, Any]:
    """Veränderbarer Verweis auf die primäre Punktwolken-Ebene: Schema 1 ``m["cloud"]``,
    Schema 2 die erste Ebene mit ``kind: "pointcloud"``. ``KeyError``, wenn keine da ist."""
    if "layers" in m:
        for layer in m["layers"]:
            if isinstance(layer, dict) and layer.get("kind") == "pointcloud":
                return layer
        raise KeyError("keine Punktwolken-Ebene in layers")
    cloud = m.get("cloud")
    if not isinstance(cloud, dict):
        raise KeyError("keine Punktwolken-Ebene (Feld cloud fehlt)")
    return cloud


def origin_of(m: dict[str, Any]) -> list[float]:
    """Weltursprung: Schema 1 ``m["originWorld"]``, Schema 2 ``m["frame"]["originWorld"]``."""
    if "frame" in m:
        return list(m["frame"]["originWorld"])
    return list(m["originWorld"])


def set_stations(m: dict[str, Any], url: str, count: int) -> None:
    """Standpunkte setzen: Schema 1 ``m["stations"]``; Schema 2 legt die Ebene ``standpunkte``
    an/aktualisiert sie, ``count == 0`` entfernt sie. Baut ``layers`` immer neu auf (kein
    In-place-``layers[:] = …``) — ein Aufrufer, der vorher ``dict(m)`` kopiert hat, behält damit
    seine eigene, unveränderte ``layers``-Liste."""
    if "layers" in m:
        layers = [
            lay
            for lay in m["layers"]
            if not (isinstance(lay, dict) and lay.get("id") == STATIONS_LAYER_ID)
        ]
        if count > 0:
            # Die tatsächliche ID der primären Punktwolken-Ebene (cloud_of), nicht die feste
            # Konstante CLOUD_LAYER_ID — set_stations läuft immer über ein Schema-2-Manifest, in dem
            # die Punktwolke schon existiert (convert() ruft to_v2 vorher auf).
            layers.append(
                {
                    "id": STATIONS_LAYER_ID,
                    "kind": "panoramas",
                    "title": "Standpunkte",
                    "url": url,
                    "count": count,
                    "pointcloudLayer": cloud_of(m)["id"],
                }
            )
        m["layers"] = layers
        return
    m["stations"] = {"url": url, "count": count}


def set_images(m: dict[str, Any], url: str, count: int) -> None:
    """Ebene ``fotos`` (kind ``images``) setzen oder mit ``count == 0`` entfernen; nur Schema 2."""
    if "layers" not in m:
        raise ValueError("Orientierte Fotos brauchen ein Schema-2-Paket (scanview-import migrate)")
    layers = [
        lay
        for lay in m["layers"]
        if not (isinstance(lay, dict) and lay.get("id") == IMAGES_LAYER_ID)
    ]
    if count > 0:
        layers.append(
            {"id": IMAGES_LAYER_ID, "kind": "images", "title": "Fotos", "url": url, "count": count}
        )
    m["layers"] = layers


def to_v2(v1: dict[str, Any]) -> dict[str, Any]:
    """Schema 1 → Schema 2 (rein, mutiert ``v1`` nicht). Muss ``manifest-v2-migrated.json``
    exakt aus ``manifest-v1.json`` treffen (Golden-Fixture, geteilt mit dem Web-Viewer)."""
    for k in v1:
        if k not in _KNOWN_V1_TOP_FIELDS:
            raise ValueError(f"Unbekanntes Feld in Schema 1: {k}")

    crs = v1["crs"]
    frame: dict[str, Any] = {
        "crs": {
            "wkt": crs["wkt"],
            "verticalDatum": crs["verticalDatum"],
            "declaredBy": crs["declaredBy"],
        },
        "unit": crs["unit"],
        "unitDeclaredBy": crs["declaredBy"],
        "originWorld": list(v1["originWorld"]),
    }

    cloud_layer: dict[str, Any] = {
        "id": CLOUD_LAYER_ID,
        "kind": "pointcloud",
        "title": "Punktwolke",
        "source": {**v1["source"], "producer": None},
        **v1["cloud"],
        "provenance": {
            "kind": "scan",
            "interpolated": False,
            "scaleVerified": True,
            "scaleSource": "scanner",
        },
    }
    if "conversion" in v1:
        cloud_layer["conversion"] = v1["conversion"]

    layers: list[dict[str, Any]] = [cloud_layer]
    stations = v1.get("stations")
    if isinstance(stations, dict) and stations.get("count", 0) > 0:
        layers.append(
            {
                "id": STATIONS_LAYER_ID,
                "kind": "panoramas",
                "title": "Standpunkte",
                "url": stations["url"],
                "count": stations["count"],
                "pointcloudLayer": CLOUD_LAYER_ID,
            }
        )

    out: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "projectId": v1["projectId"],
        "revision": v1["revision"],
        "title": v1["projectId"],
        "frame": frame,
        "layers": layers,
        "initialView": v1["initialView"],
    }
    if "conversion" in v1:
        out["conversion"] = v1["conversion"]
    if "topview" in v1:
        out["topview"] = v1["topview"]
    return out


def migrate(pkg: Path) -> str:
    """Migriert ``<pkg>/project.json`` von Schema 1 auf 2: sichert zuerst ``project.v1.json``
    (``open(..., "xb")`` — bricht mit ``FileExistsError`` ab, wenn die Sicherung schon existiert,
    ohne project.json anzufassen), schreibt project.json danach atomar (``project.json.tmp`` +
    ``os.replace``). Schon Schema 2 → No-op, ``"schon Schema 2"``. ``revision`` bleibt
    unverändert (Inhalt gleich, POI-Dateien passen weiter).

    Nur ``schemaVersion == 1`` wird migriert (alles andere, auch fehlend, ist ein Fehler,
    ``2`` ist der einzige Sonderfall, der als No-op durchgeht). Die Sicherung ist eine reine
    Byte-Kopie der Originaldatei (``raw`` direkt geschrieben, kein Re-Serialisieren über
    ``json.dump`` — das würde z. B. Nicht-ASCII-Titel escapen und wäre nicht mehr bytegleich).
    ``v2`` wird VOR jedem Schreiben vollständig serialisiert (``to_v2`` plus ``json.dumps`` mit
    ``allow_nan=False``): schlägt das fehl (unbekanntes Feld, NaN/Inf im Manifest), darf weder die
    Sicherung noch project.json angefasst worden sein."""
    raw = (pkg / MANIFEST_NAME).read_bytes()
    m = json.loads(raw)
    schema = m.get("schemaVersion")
    if schema == 2:
        return "schon Schema 2"
    if schema != 1:
        raise ValueError(f"project.json: schemaVersion {schema!r} nicht migrierbar (erwartet 1).")

    # Vollständig serialisieren, BEVOR irgendetwas geschrieben wird — schlägt das fehl
    # (unbekanntes Feld in to_v2, NaN/Inf über allow_nan=False), darf project.v1.json noch nicht
    # liegen, sonst meldet ein Folgeversuch fälschlich "Sicherung existiert schon", obwohl noch
    # nichts migriert wurde.
    v2_text = json.dumps(to_v2(m), indent=2, allow_nan=False)

    backup = pkg / V1_BACKUP_NAME
    with backup.open("xb") as f:
        f.write(raw)

    tmp = pkg / (MANIFEST_NAME + ".tmp")
    tmp.write_text(v2_text)
    os.replace(tmp, pkg / MANIFEST_NAME)
    return "migriert"


def start_sichtbar(pkg: Path, ebene: str, an: bool) -> dict[str, Any]:
    """Setzt ``startVisible`` einer Ebene (Viewer-Voreinstellung ohne ``ly=``): ``an`` entfernt das
    Feld (Standard sichtbar), sonst ``false``. Nur Schema 2; unbekannte Ebene → ``KeyError``."""
    m = read_manifest(pkg)
    if m.get("schemaVersion") != 2:
        raise ValueError("startVisible braucht Schema 2; Paket zuerst migrieren")
    treffer = [lay for lay in m["layers"] if isinstance(lay, dict) and lay.get("id") == ebene]
    if not treffer:
        raise KeyError(ebene)
    if an:
        treffer[0].pop("startVisible", None)
    else:
        treffer[0]["startVisible"] = False
    write_manifest(pkg, m)
    return {"ebene": ebene, "startVisible": an}
