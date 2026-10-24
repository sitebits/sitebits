"""Neues Schema-2-Paket nur aus einem Mesh oder aus Splats, ohne Punktwolke.

Ohne Wolke gibt es keinen Deckungs- oder Rahmennachweis: Splat-Ebenen tragen
``frameCheck: "keine Wolke"``, Meshes ``provenance.scaleVerified: false``; der Viewer zeigt
deshalb „Modellmaß“ statt eines belegten Maßes. Ohne Koordinatensystem gilt der Rahmen als
lokal (``declaredBy`` = ``LOKAL``).

Dieser Weg kommt ohne Container aus: Meshes nur als GLB oder PLY ohne Textur (die Textur-Kodierung
läuft im Container), GLB nur mit eingebetteten Puffern (keine externen Verweise), Splats höchstens
1 000 000 Gaussians als SPZ (größere bräuchten das LOD-Werkzeug).
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from . import hoehen
from .convert import _sha256
from .geometry import compute_origin

#: declaredBy des Rahmens ohne Koordinatensystem.
LOKAL = "keine Angabe (lokal, Modellmaß)"
#: frameCheck einer Splat-Ebene ohne Wolke (Viewer-Vertrag).
KEINE_WOLKE = "keine Wolke"
MAX_SPLATS_OHNE_LOD = 1_000_000
MESH_ENDUNGEN = (".glb", ".ply")
SPLAT_ENDUNGEN = (".ply", ".spz", ".splat")
TITEL_MAX = 120
TEXTUR_ABSAGE = (
    "Texturierte Meshes werden hier noch nicht angenommen (die Textur-Kodierung braucht "
    "einen eigenen Schritt) – bitte ein Mesh ohne Textur (Vertexfarben) oder Splats hochladen."  # noqa: RUF001
)


def _titel(titel: str | None, quelle: Path) -> str:
    t = (titel if titel is not None else quelle.stem).strip()
    if not t or len(t) > TITEL_MAX or not t.isprintable():
        raise ValueError(f"Titel muss 1 bis {TITEL_MAX} druckbare Zeichen haben")
    return t


def _ziel_frei(paket: Path) -> None:
    if paket.exists() or paket.is_symlink():
        raise ValueError("Zielpaket existiert bereits")


def glb_ohne_verweise(quelle: Path) -> None:
    """GLB darf nur eingebettete Puffer und Bilder haben: ein externer Verweis würde beim Lesen
    eine Datei neben der Quelle (oder darüber hinaus) öffnen und ins Paket übernehmen."""
    import pygltflib as g

    with quelle.open("rb") as f:
        if f.read(4) != b"glTF":
            raise ValueError("Keine GLB-Datei (Kennung glTF fehlt)")
    try:
        gltf = g.GLTF2().load(str(quelle))
    # pygltflib wirft je nach Defekt verschiedene Fehler (Kopf, JSON-Teil, Felder, Puffer).
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError,
            struct.error) as e:
        raise ValueError(f"GLB nicht lesbar ({type(e).__name__})") from None
    if gltf is None:
        raise ValueError("GLB nicht lesbar")
    if any(b.uri for b in gltf.buffers) or any(im.uri for im in gltf.images):
        raise ValueError("GLB mit externen Verweisen (uri) wird nicht angenommen – "  # noqa: RUF001
                         "nur eingebettete Daten")
    if gltf.images or gltf.textures or any(
            m.pbrMetallicRoughness is not None and m.pbrMetallicRoughness.baseColorTexture
            is not None for m in gltf.materials):
        raise ValueError(TEXTUR_ABSAGE)


def neu_mesh(
    quelle: Path, paket: Path, *, titel: str | None = None, crs: str | None = None,
    unit: str | None = None, hoehe: tuple[str | None, str | None] = (None, None),
) -> dict[str, Any]:
    from .manifest_io import read_manifest, write_manifest
    from .mesh_convert import convert_mesh
    from .mesh_source import is_mesh, read_mesh

    endung = quelle.suffix.lower()
    if endung not in MESH_ENDUNGEN:
        raise ValueError("Mesh nur als .glb oder .ply")
    t = _titel(titel, quelle)
    _ziel_frei(paket)
    if endung == ".glb":
        glb_ohne_verweise(quelle)
    else:
        if not is_mesh(quelle):
            raise ValueError("PLY ohne Dreiecke ist kein Mesh (Splats: --art splats)")
        # PLY erklärt keine Einheit; ohne Angabe gilt Meter (vermerkt als unit:cli).
        unit = unit or "m"
    # Erst lesen und auf Texturen prüfen, bevor irgendein Kodierschritt läuft.
    if any(tex is not None for tex in read_mesh(quelle, crs, unit).textures):
        raise ValueError(TEXTUR_ABSAGE)
    convert_mesh(quelle, paket, crs=crs, unit=unit, hoehe=hoehe)
    m = read_manifest(paket)
    m["title"] = t
    if m["frame"]["crs"]["wkt"] is None:
        m["frame"]["crs"]["declaredBy"] = LOKAL
    write_manifest(paket, m)
    return m


def neu_splats(
    quelle: Path, paket: Path, *, titel: str | None = None, crs: str | None = None,
    hoehe: tuple[str | None, str | None] = (None, None),
) -> dict[str, Any]:
    from .manifest_io import write_manifest
    from .mesh_convert import positions_hoehe
    from .splat_convert import _deliver
    from .splat_source import read_gaussians
    from .splat_spz import FRACTIONAL_BITS

    if quelle.suffix.lower() not in SPLAT_ENDUNGEN:
        raise ValueError("Splats nur als .ply, .spz oder .splat")
    t = _titel(titel, quelle)
    _ziel_frei(paket)
    quell_h, ziel_h = hoehen.festlegen(*hoehe)
    if quell_h is not None and crs is None:
        raise ValueError("Höhensystem nur zusammen mit einem Koordinatensystem (--crs)")
    g = read_gaussians(quelle)
    if len(g.centers) > MAX_SPLATS_OHNE_LOD:
        raise ValueError(f"Höchstens {MAX_SPLATS_OHNE_LOD:,} Gaussians ".replace(",", ".")
                         + "(größere Sichtschichten brauchen das LOD-Werkzeug, hier nicht "
                         "eingerichtet)")
    g = replace(g, centers=positions_hoehe(g.centers, crs, quell_h, ziel_h))
    lo, hi = g.centers.min(0), g.centers.max(0)
    origin = np.asarray(compute_origin(lo.tolist(), hi.tolist()), dtype=np.float64)
    options: dict[str, Any] = {
        "unit": "m", "crs": crs, "shDegreeSource": g.sh_degree,
        "shDegreeDelivered": g.sh_degree, "shPolicy": "preserve",
        "maxSplats": MAX_SPLATS_OHNE_LOD,
    }
    if quell_h is not None and ziel_h is not None:
        options["hoehe"] = hoehen.quellblock(quell_h, ziel_h)
    paket.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".splats-", dir=paket.parent))
    try:
        dest = work / "layers/splats"
        dest.mkdir(parents=True)
        output = dest / "model.spz"
        delivered, error, tolerance = _deliver(output, g, origin, "spz")
        layer: dict[str, Any] = {
            "id": "splats", "kind": "splats", "title": "Sichtschicht", "format": "spz",
            "url": "layers/splats/model.spz", "splatCount": len(g.centers),
            "sizeBytes": output.stat().st_size, "shDegree": delivered.sh_degree,
            "chunkCount": 1,
            "boundsWorld": [delivered.centers.min(0).tolist(), delivered.centers.max(0).tolist()],
            "sourceOptions": options,
            "frameCheck": KEINE_WOLKE,
            "source": {"filename": quelle.name, "sha256": _sha256(quelle),
                       "sizeBytes": quelle.stat().st_size},
            "verify": {"ok": False, "report": "layers/splats/conversion-report.json"},
        }
        report = {"ok": True, "positionMaxM": error, "positionToleranceM": tolerance,
                  "positionStepM": 2**-FRACTIONAL_BITS, "format": "spz",
                  "frameCheck": KEINE_WOLKE}
        (dest / "conversion-report.json").write_text(json.dumps(report, indent=2,
                                                                allow_nan=False))
        manifest: dict[str, Any] = {
            "schemaVersion": 2, "projectId": paket.name, "revision": 1, "title": t,
            "frame": {
                "crs": {"wkt": crs, "verticalDatum": ziel_h,
                        "declaredBy": "cli" if crs is not None else LOKAL},
                "unit": "m", "unitDeclaredBy": "keine Angabe" if crs is None else "cli",
                "originWorld": origin.tolist(),
            },
            "layers": [layer],
            "initialView": {"target": ((lo + hi) / 2).tolist(),
                            "distance": max(float(np.linalg.norm(hi - lo)), 1.0),
                            "layers": ["splats"]},
        }
        write_manifest(work, manifest)
        if paket.exists() or paket.is_symlink():
            raise ValueError("Zielpaket inzwischen angelegt")
        os.rename(work, paket)
        return manifest
    finally:
        if work.exists():
            shutil.rmtree(work)


def verify_splats(quelle: Path, paket: Path) -> dict[str, Any]:
    """Ausgelieferte Splats gegen die Quelle: gleiche Quelle (SHA-256), gleiche Anzahl und
    Reihenfolge, Positionsfehler höchstens die SPZ-Toleranz. Schreibt den Bericht ins Paket und
    setzt ``verify.ok`` der Ebene."""
    from .manifest_io import read_manifest, write_manifest
    from .mesh_convert import positions_hoehe
    from .splat_source import read_gaussians
    from .splat_spz import POSITION_TOLERANCE_M

    m = read_manifest(paket)
    layer = next(lay for lay in m["layers"] if lay.get("kind") == "splats")
    opts = layer.get("sourceOptions", {})
    url = (paket / layer["url"]).resolve()
    if not url.is_relative_to(paket.resolve()):
        raise ValueError("Splat-Datei außerhalb des Pakets")
    sha = _sha256(quelle)
    src = read_gaussians(quelle)
    hoehe = opts.get("hoehe")
    quell_h = hoehe.get("quelle") if isinstance(hoehe, dict) else None
    ziel_h = hoehe.get("ziel") if isinstance(hoehe, dict) else None
    src = replace(src, centers=positions_hoehe(src.centers, opts.get("crs"), quell_h, ziel_h))
    out = read_gaussians(url)
    origin = np.asarray(m["frame"]["originWorld"], dtype=np.float64)
    gleich_viele = len(out.centers) == len(src.centers) == layer["splatCount"]
    fehler = (float(np.linalg.norm(out.centers + origin - src.centers, axis=1).max())
              if gleich_viele else None)
    tol = float(POSITION_TOLERANCE_M)
    rep: dict[str, Any] = {
        "source": {"sha256": sha, "ok": sha == layer["source"]["sha256"]},
        "counts": {"source": len(src.centers), "delivered": len(out.centers),
                   "manifest": layer["splatCount"], "ok": gleich_viele},
        "positions": {"maxM": fehler, "toleranceM": tol,
                      "ok": fehler is not None and fehler <= tol},
        "shDegree": {"source": src.sh_degree, "delivered": out.sh_degree,
                     "ok": src.sh_degree == out.sh_degree},
        "frameCheck": layer.get("frameCheck"),
    }
    rep["ok"] = all(rep[k]["ok"] for k in ("source", "counts", "positions", "shDegree"))
    (paket / layer["verify"]["report"]).write_text(json.dumps(rep, indent=2, allow_nan=False))
    layer["verify"]["ok"] = rep["ok"]
    write_manifest(paket, m)
    return rep


def ist_splat_paket_ohne_wolke(paket: Path) -> bool:
    try:
        m = json.loads((paket / "project.json").read_text())
    except (OSError, ValueError):
        return False
    layers = m.get("layers") if isinstance(m, dict) else None
    if not isinstance(layers, list):
        return False
    kinds = [lay.get("kind") for lay in layers if isinstance(lay, dict)]
    return "pointcloud" not in kinds and "splats" in kinds
