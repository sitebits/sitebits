"""Messung gegen geschriebene Stufen: unabhängiges Rücklesen, kein Manifest-Vertrauen."""

from __future__ import annotations

import json
import os
import resource
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import numpy as np
from scipy.spatial import cKDTree

from . import hoehen, lage
from .manifest_io import cloud_of, origin_of, read_manifest
from .mesh_preview import color_report
from .mesh_source import Mesh, read_mesh
from .mesh_stages import degenerate_mask
from .mesh_surface import SourceMesh, SurfaceIndex
from .mesh_textures import decode_texture, ktx_info, sample_rgb


def surface(mesh: Mesh) -> SourceMesh:
    return SourceMesh(mesh.positions, mesh.triangles, None, None)


def stats_mm(distances: np.ndarray) -> dict[str, float]:
    return dict(
        zip(
            ("p50Mm", "p95Mm", "maxMm"),
            (np.quantile(distances, [0.5, 0.95, 1]) * 1000).tolist(),
            strict=True,
        )
    )


def controls(mesh: Mesh) -> np.ndarray:
    p = mesh.positions
    extremes = [int(f(p[:, axis])) for axis in range(3) for f in (np.argmin, np.argmax)]
    random = np.random.default_rng(42).integers(0, len(p), 10).tolist()
    return cast(np.ndarray, p[extremes + random])


def mesh_counts(mesh: Mesh) -> dict[str, int]:
    mask, _ = degenerate_mask(mesh.positions, mesh.triangles)
    return {
        "triangleCount": len(mesh.triangles),
        "vertexCount": mesh.source_vertex_count or len(mesh.positions),
        "graphicsVertexCount": len(mesh.positions),
        "degenerateTriangles": int(mask.sum()),
    }


def measure_stage(
    source: Mesh,
    output: Mesh,
    tolerance_m: float,
    exact: bool,
    target: int,
    erreicht_ausgewiesen: int | None = None,
    removed: int = 0,
    bounds_tolerance_m: float | None = None,
) -> dict[str, Any]:
    a, b = surface(source), surface(output)
    forward = SurfaceIndex(output.positions, output.triangles).distance(
        a.sample_surface(10000, 42)[0]
    )
    reverse = SurfaceIndex(source.positions, source.triangles).distance(
        b.sample_surface(10000, 42)[0]
    )
    # Vertexprüfung misst echte Vertexlagen (keine nächstliegende Dreiecksfläche).
    ctrl = cKDTree(output.positions).query(controls(source))[0]
    bounds_source = np.array([source.positions.min(axis=0), source.positions.max(axis=0)])
    bounds_output = np.array([output.positions.min(axis=0), output.positions.max(axis=0)])
    bound_error = float(np.max(np.abs(bounds_source - bounds_output)))
    source_degenerate = mesh_counts(source)["degenerateTriangles"]
    source_valid = len(source.triangles) - source_degenerate
    effective = min(target, source_valid) if target else source_valid
    if target == 0 or target >= source_valid:
        effective -= removed - source_degenerate
    target_ok = (
        len(output.triangles) == effective
        if target == 0 or target >= source_valid
        else abs(len(output.triangles) / effective - 1) <= 0.05
    )
    # Ziel unter Erhalt der UV-Nähte nicht erreichbar: zulässig nur mit genau der im Manifest
    # ausgewiesenen Dreieckszahl (mesh_stages.simplify_with_report); ein ignoriertes Ziel fällt auf.
    target_declared = (
        not target_ok
        and erreicht_ausgewiesen is not None
        and len(output.triangles) == erreicht_ausgewiesen
    )
    target_ok = target_ok or target_declared
    result = {
        "counts": mesh_counts(output),
        "sourceToStage": stats_mm(forward),
        "stageToSource": stats_mm(reverse),
        "samplesPerDirection": 10000,
        "seed": 42,
        "controls": {
            "count": 16,
            "maxMm": float(ctrl.max() * 1000),
            "required": exact,
            "ok": bool(ctrl.max() <= 0.001),
        },
        "boundsMaxMm": bound_error * 1000,
        "boundsLimitMm": (tolerance_m if bounds_tolerance_m is None else bounds_tolerance_m) * 1000,
        "target": target,
        "targetOk": target_ok,
        "targetUnreachableDeclared": target_declared,
    }
    result["ok"] = bool(
        target_ok
        and mesh_counts(output)["degenerateTriangles"] == 0
        and np.quantile(reverse, 0.95) <= tolerance_m
        and bound_error <= (tolerance_m if bounds_tolerance_m is None else bounds_tolerance_m)
        and (not exact or ctrl.max() <= 0.001)
    )
    return result


def texture_report(source: Mesh, output: Mesh, max_px: int, encoding: str) -> dict[str, Any]:
    src_used = [source.textures[int(i)] for i in np.unique(source.materials)]
    out_used = [output.textures[int(i)] for i in np.unique(output.materials)]
    expected = [t for t in src_used if t is not None]
    encoded = [t for t in out_used if t is not None]
    infos, deltas = [], []
    rng = np.random.default_rng(42)
    uv = rng.random((1000, 2))
    for a, b in zip(expected, encoded, strict=False):
        data = b.read_bytes() if isinstance(b, Path) else b
        info = ktx_info(data)
        infos.append(info)
        delta = np.abs(sample_rgb(decode_texture(a), uv) - sample_rgb(decode_texture(b), uv))
        deltas.append(float(delta.mean()))
    ok = len(expected) == len(encoded) and all(
        max(i["width"], i["height"]) <= max_px and i["encoding"] == encoding and i["levels"] >= 1
        for i in infos
    )
    return {
        "count": len(encoded),
        "expectedCount": len(expected),
        "images": infos,
        "uvSamplesPerTexture": 1000,
        "seed": 42,
        "meanAbsDeltaRGB": deltas,
        "ok": bool(ok),
    }


MOBIL_WARNGRENZE_BYTES = 100 * 1024 * 1024


def verify_stage(
    source: Mesh,
    pkg: Path,
    layer: dict[str, Any],
    origin: np.ndarray,
    name: str,
    url: str,
    target: int,
    max_px: int,
    encoding: str,
) -> dict[str, Any]:
    """Eine geschriebene Stufe unabhängig zurücklesen und gegen die Quelle messen."""
    opts = layer["sourceOptions"]
    output = read_mesh(pkg / url)
    output.positions += origin
    stufe = layer.get("stufen", {}).get(name, {})
    ausgewiesen = stufe.get("erreicht") if stufe.get("zielErreicht") is False else None
    removal = layer.get("degenerateRemoval", {})
    stage_removal = removal.get(name, {})
    removed = removal.get("source", {}).get("removed", 0) + sum(
        r["removed"] for r in stage_removal.values()
    )
    rep = measure_stage(
        source,
        output,
        opts["toleranzM"],
        target == 0,
        target,
        ausgewiesen,
        removed,
        opts.get("ausdehnungToleranzM"),
    )
    rep["degenerateRemoval"] = stage_removal
    rep["positionStepM"] = layer.get("positionStepsM", {}).get(name)

    if name == "preview" and layer.get("previewColor") == "vertex":
        rep["colors"] = color_report(source, output)
        rep["textures"] = {"count": 0, "ok": rep["colors"]["ok"]}
    else:
        rep["textures"] = texture_report(source, output, max_px, encoding)
    rep["sizeBytes"] = (pkg / url).stat().st_size
    # Die Vorschau wird gemessen, das geometrische Toleranztor gilt Voll- und Mobilstufe.
    if name == "preview":
        rep["ok"] = bool(
            rep["targetOk"] and rep["textures"]["ok"] and rep["counts"]["degenerateTriangles"] == 0
        )
    else:
        rep["ok"] = bool(rep["ok"] and rep["textures"]["ok"])
    return rep


def mobil_warnungen(stages: dict[str, Any]) -> list[str]:
    """Warnung, kein Fehler: eine große Mobilstufe lädt, ist aber auf Handys riskant."""
    mobile = stages.get("mobile")
    if mobile is None or mobile["sizeBytes"] <= MOBIL_WARNGRENZE_BYTES:
        return []
    return [f"Mobilstufe {mobile['sizeBytes'] / 2**20:.0f} MiB > 100 MiB"]


def write_report(pkg: Path, rep: dict[str, Any]) -> None:
    """Bericht ersetzen statt überschreiben: eine Hardlink-Kopie des Pakets bleibt unberührt."""
    ziel = pkg / "layers/mesh/conversion-report.json"
    tmp = ziel.with_name(f".{ziel.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rep, indent=2, ensure_ascii=False, allow_nan=False))
    os.replace(tmp, ziel)


def verify_mesh(
    path: Path,
    pkg: Path,
    source: Mesh | None = None,
    manifest: dict[str, Any] | None = None,
    point_pkg: Path | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    m = manifest if manifest is not None else read_manifest(pkg)
    if any(lay["kind"] == "mesh-tiles" for lay in m["layers"]):
        from .convert import _sha256
        from .kacheln.paket import pruefen

        rep_tiles = pruefen(pkg, m)
        tiles_layer = next(lay for lay in m["layers"] if lay["kind"] == "mesh-tiles")
        rep_tiles["sourceIdentityOk"] = _sha256(path) == tiles_layer["source"]["sha256"]
        rep_tiles["ok"] = bool(rep_tiles["ok"] and rep_tiles["sourceIdentityOk"])
        return rep_tiles
    layer = next(lay for lay in m["layers"] if lay["kind"] == "mesh")
    opts = layer["sourceOptions"]
    if source is None:
        source = read_mesh(path, opts["crs"], opts["unit"], opts["up"])
        hoehe = opts.get("hoehe")
        if hoehe is not None and hoehe["quelle"] != hoehe["ziel"]:
            source = replace(
                source,
                positions=hoehen.positionen_umrechnen(
                    source.positions, crs=opts["crs"], quelle=hoehe["quelle"], ziel=hoehe["ziel"]
                ),
            )
        lagematrix = lage.aus_paket({"sourceOptions": opts})
        if lagematrix is not None:
            source = replace(source, positions=lage.wende_an(source.positions, lagematrix))
    origin = np.asarray(origin_of(m), dtype=np.float64)
    stage_specs = [
        ("model", layer["url"], opts["dreiecke"], 4096, "ktx2-uastc"),
        ("preview", layer["previewUrl"], opts["vorschauDreiecke"], 1024, "ktx2-etc1s"),
    ]
    if layer.get("mobileUrl"):
        stage_specs.append(
            ("mobile", layer["mobileUrl"], opts.get("mobilDreiecke", 0), 2048, "ktx2-etc1s")
        )
    stages = {
        name: verify_stage(source, pkg, layer, origin, name, url, target, max_px, encoding)
        for name, url, target, max_px, encoding in stage_specs
    }
    from .convert import _sha256

    identity_ok = _sha256(path) == layer["source"]["sha256"]
    rep = {
        "sourceIdentityOk": identity_ok,
        "ok": bool(identity_ok and all(s["ok"] for s in stages.values())),
        "source": mesh_counts(source),
        "degenerateRemoval": layer.get("degenerateRemoval"),
        "stages": stages,
        "frameCheck": layer.get("frameCheck"),
        "conversion": layer["conversion"],
        "verifySeconds": time.monotonic() - t0,
        "hostPeakRssMb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "layerSizeBytes": sum(s["sizeBytes"] for s in stages.values()),
        "warnungen": mobil_warnungen(stages),
    }
    if layer.get("frameCheck") is not None:
        from .mesh_frame import frame_check

        # Grenzen aus der Ebene: eine beim add angehobene Grenze gilt auch beim Nachprüfen.
        grenzen = layer["frameCheck"].get("limitsM")
        rep["frameCheck"] = frame_check(
            point_pkg or pkg,
            cloud_of(m),
            pkg / layer["url"],
            origin,
            None if grenzen is None else (grenzen["p50"], grenzen["p95"]),
            tuple(layer["frameCheck"].get("excludedClasses", ())),
        )
        rep["ok"] = bool(rep["ok"] and rep["frameCheck"]["ok"])
    rep["verifySeconds"] = time.monotonic() - t0
    write_report(pkg, rep)
    return rep
