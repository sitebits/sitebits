"""Sichtschicht nur nach Rahmenbeleg veröffentlichen; Quelle niemals überschreiben."""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import warnings
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from . import hoehen
from .convert import _sha256
from .manifest_io import cloud_of, origin_of, read_manifest
from .mesh_convert import _add_hoehe, positions_hoehe
from .mesh_frame import sample_cloud
from .point_source import _same_crs
from .splat_cameras import camera_pairs, world_centers
from .splat_frame import Similarity, fit_similarity, transform_gaussians
from .splat_rad import build_rad
from .splat_source import MAX_SPLATS, Gaussians, read_gaussians, write_gaussians
from .splat_spz import FRACTIONAL_BITS, POSITION_TOLERANCE_M, write_spz


def check_frame(
    pkg: Path, cloud: dict[str, Any], g: Gaussians, limits_mm: tuple[float, float] = (5, 25),
    *, footprint_slack_m: float = 0,
) -> dict[str, Any]:
    g50, g95 = limits_mm
    if not (np.isfinite(limits_mm).all() and 0 < g50 <= 50 and g50 <= g95 <= 300):
        raise ValueError("Rahmengrenze: 0 < p50 ≤ 50 mm, p50 ≤ p95 ≤ 300 mm")
    path = (pkg / cloud["url"]).resolve()
    if not path.is_relative_to(pkg.resolve()):
        raise ValueError("frameCheck: Wolkenpfad außerhalb Paket")
    centers = g.centers[g.opacities >= 0.5]
    if len(centers) < 1000:
        raise ValueError("frameCheck: mindestens 1000 deckende Gaussians (Opacity ≥ 0,5)")
    # Nur Wolkenpunkte in belegten 1-m-Grundrisszellen, keine fremde Geländeabdeckung.
    origin = centers.min(0)
    # Encoding error can move a boundary point across a cell; only cover that measured bound.
    occupied = {
        tuple(cell)
        for dx in (-footprint_slack_m, footprint_slack_m)
        for dy in (-footprint_slack_m, footprint_slack_m)
        for cell in np.floor(centers[:, :2] - origin[:2] + [dx, dy]).astype(int)
    }
    cloud_points = sample_cloud(path, count=2_000_000)
    cells = np.floor(cloud_points[:, :2] - origin[:2]).astype(int)
    mask = np.array([tuple(cell) in occupied for cell in cells])
    points = cloud_points[mask]
    if len(points) < 1000:
        raise ValueError("frameCheck: weniger als 1000 Wolkenpunkte im Splat-Footprint")
    rng = np.random.default_rng(42)
    selected = centers[rng.choice(len(centers), min(100000, len(centers)), replace=False)]
    distances = cKDTree(points - origin).query(selected - origin, k=1)[0]
    p50, p95 = np.quantile(distances, [0.5, 0.95])
    return {
        "againstLayer": cloud["id"],
        "samples": len(selected),
        "cloudSamples": len(points),
        "within": "gaussianFootprint1m",
        "minOpacity": 0.5,
        "footprintSlackM": footprint_slack_m,
        "seed": 42,
        "p50M": float(p50),
        "p95M": float(p95),
        "maxM": float(distances.max()),
        "limitsM": {"p50": g50 / 1000, "p95": g95 / 1000},
        "raised": limits_mm != (5, 25),
        "ok": bool(p50 * 1000 <= g50 and p95 * 1000 <= g95),
    }


def _start_camera(manifest: dict[str, Any], g: Gaussians, path: Path | None) -> dict[str, str]:
    if path is None or "initialView" in manifest:
        return {}
    centers = np.array(list(world_centers(path).values()))
    if not len(centers):
        raise ValueError("Startkamera: keine Weltzentren vorhanden")
    position = np.median(centers, axis=0)
    target = g.centers.mean(0)
    distance = float(np.linalg.norm(position - target))
    if distance <= 0:
        raise ValueError("Startkamera fällt mit Schwerpunkt zusammen")
    manifest["initialView"] = {"position": position.tolist(),
                               "target": target.tolist(), "distance": distance}
    return {"initialCameraPolicy": "median-world-centers"}


def _deliver(output: Path, g: Gaussians, origin: np.ndarray, fmt: str
             ) -> tuple[Gaussians, float, float]:
    if fmt == "spz":
        write_spz(output, g, origin)
    elif fmt == "ply":
        write_gaussians(output, g, origin)
    else:
        raise ValueError("Ausgabeformat muss spz oder ply sein; RAD-Encoder nicht eingerichtet")
    delivered = read_gaussians(output)
    delivered = replace(delivered, centers=delivered.centers + origin)
    error = float(np.linalg.norm(delivered.centers - g.centers, axis=1).max())
    tolerance = float(POSITION_TOLERANCE_M) if fmt == "spz" else 0.001
    if error > tolerance:
        raise ValueError(f"Splat-Ausgabe: Positionsfehler über {tolerance} m")
    return delivered, error, tolerance


def _align_leaves(g: Gaussians, centers: np.ndarray, origin: np.ndarray) -> Gaussians:
    """Restore verified source order so opacity filtering applies to the same leaves."""
    expected = (g.centers - origin).astype(np.float32)
    order = np.lexsort(expected.T[::-1])
    actual_order = np.lexsort((centers - origin).T[::-1])
    aligned = np.empty_like(g.centers)
    aligned[order] = centers[actual_order]
    return replace(g, centers=aligned)


def _output_format(g: Gaussians, fmt: str, tool: Path | None) -> str:
    if fmt == "auto":
        fmt = "rad" if len(g.centers) > 1_000_000 and tool else "spz"
        if len(g.centers) > 1_000_000 and tool is None:
            warnings.warn("build-lod fehlt: SPZ bleibt auf 1 Mio. begrenzt; "
                          "--build-lod/SCANVIEW_BUILD_LOD setzen", stacklevel=2)
    if fmt != "rad" and len(g.centers) > 1_000_000:
        raise ValueError("Ohne RAD sind maximal 1.000.000 Gaussians für den Viewer erlaubt")
    if fmt == "rad" and tool is None:
        raise ValueError("RAD benötigt --build-lod oder SCANVIEW_BUILD_LOD")
    if fmt not in ("rad", "spz", "ply"):
        raise ValueError("Ausgabeformat muss auto/rad/spz/ply sein")
    return fmt


def add_splats(
    path: Path,
    pkg: Path,
    *,
    same_frame: bool = False,
    colmap: Path | None = None,
    cameras: Path | None = None,
    crs: str | None = None,
    hoehe: tuple[str | None, str | None] = (None, None),
    limits_mm: tuple[float, float] = (5, 25),
    camera_limit_mm: float = 25,
    initial: bool = False,
    output_format: str = "auto",
    build_lod: Path | None = None,
    start_cameras: Path | None = None,
) -> dict[str, Any]:
    if same_frame == (colmap is not None or cameras is not None):
        raise ValueError("Genau einen Rahmenweg wählen: --same-frame oder beide Kameradateien")
    if not same_frame and (colmap is None or cameras is None):
        raise ValueError("COLMAP und Weltkameras gemeinsam angeben")
    before = (pkg / "project.json").read_bytes()
    original = read_manifest(pkg)
    if original.get("schemaVersion") != 2 or original["frame"]["unit"] != "m":
        raise ValueError("Splats benötigen metrisches Schema-2-Paket")
    if any(lay["id"] == "splats" for lay in original["layers"]) or (pkg / "layers/splats").exists():
        raise ValueError("Splat-Ebene existiert bereits")
    cloud = cloud_of(original)  # Ohne Wolke kein Deckungsnachweis, deshalb kein add.
    g = read_gaussians(path)
    tool = build_lod or (Path(os.environ["SCANVIEW_BUILD_LOD"])
                         if os.environ.get("SCANVIEW_BUILD_LOD") else None)
    output_format = _output_format(g, output_format, tool)
    source_degree = g.sh_degree
    options: dict[str, Any] = {
        "unit": "m",
        "shDegreeSource": source_degree,
        "shDegreeDelivered": source_degree,
        "shPolicy": "rotate-and-preserve",
        "maxSplats": MAX_SPLATS if output_format == "rad" else 1_000_000,
    }
    if same_frame:
        if not _same_crs(crs, original["frame"]["crs"]["wkt"]):
            raise ValueError("Splat-CRS widerspricht Paketrahmen")
        quelle, ziel = _add_hoehe(hoehe, original["frame"]["crs"].get("verticalDatum"))
        g = replace(g, centers=positions_hoehe(g.centers, crs, quelle, ziel))
        if quelle is not None and ziel is not None:
            options["hoehe"] = hoehen.quellblock(quelle, ziel)
        block = cloud.get("sourceOptions", {}).get("lage")
        if block:
            m = np.asarray(block["matrix"], dtype=float)
            if (
                m.shape != (4, 4)
                or not np.isfinite(m).all()
                or not np.allclose(m[3], [0, 0, 0, 1])
                or not np.allclose(m[:3, :3].T @ m[:3, :3], np.eye(3), atol=1e-6)
                or not np.isclose(np.linalg.det(m[:3, :3]), 1, atol=1e-6)
            ):
                raise ValueError("Paket-Lagematrix ungültig")
            g = transform_gaussians(g, Similarity(1, m[:3, :3], m[:3, 3]))
            options["lage"] = copy.deepcopy(block)
        alignment: dict[str, Any] = {"method": "same-frame", "ok": True}
    else:
        assert colmap is not None and cameras is not None
        local, world, labels = camera_pairs(colmap, cameras)
        transform, alignment = fit_similarity(local, world, labels, camera_limit_mm)
        g = transform_gaussians(g, transform)
    origin = np.asarray(origin_of(original), dtype=float)
    work = Path(tempfile.mkdtemp(prefix=".splats-", dir=pkg))
    try:
        dest = work / "layers/splats"
        dest.mkdir(parents=True)
        rad: dict[str, Any] = {}
        if output_format == "rad":
            assert tool is not None
            source_check = check_frame(pkg, cloud, g, limits_mm)
            if not source_check["ok"]:
                raise ValueError(f"frameCheck vor build-lod fehlgeschlagen: {source_check}")
            centers, rad = build_rad(dest, g, origin, tool)
            output = dest / "model-lod.rad"
            error, tolerance = rad["positionMaxM"], rad["positionToleranceM"]
            delivered = _align_leaves(g, centers, origin)
        else:
            output = dest / f"model.{output_format}"
            delivered, error, tolerance = _deliver(output, g, origin, output_format)
        check = check_frame(pkg, cloud, delivered, limits_mm, footprint_slack_m=tolerance)
        if not check["ok"]:
            raise ValueError(f"frameCheck fehlgeschlagen: {check}")
        layer = {
            "id": "splats",
            "kind": "splats",
            "title": "Sichtschicht",
            "format": output_format,
            "url": f"layers/splats/{output.name}",
            "splatCount": len(g.centers),
            "sizeBytes": rad.get("sizeBytes", output.stat().st_size),
            "shDegree": delivered.sh_degree,
            "chunkCount": rad.get("chunkCount", 1),
            **({"lodMethod": rad["lodMethod"], "buildLod": rad["buildLod"]} if rad else {}),
            "boundsWorld": [delivered.centers.min(0).tolist(), delivered.centers.max(0).tolist()],
            "sourceOptions": options,
            "alignment": alignment,
            "frameCheck": check,
            "source": {"filename": path.name, "sha256": _sha256(path)},
            "verify": {"ok": True, "report": "layers/splats/conversion-report.json"},
        }
        report = {
            "ok": True,
            "positionMaxM": error,
            "positionToleranceM": tolerance,
            "positionStepM": 2**-FRACTIONAL_BITS if output_format == "spz" else None,
            "shDegree": delivered.sh_degree,
            "alignment": alignment,
            "frameCheck": check,
            "shPolicy": "rotate-and-preserve",
            "format": output_format,
            "streaming": output_format == "rad",
            **rad,
        }
        manifest = copy.deepcopy(original)
        manifest["layers"].append(layer)
        manifest["revision"] += 1
        options.update(_start_camera(manifest, g, start_cameras))
        report["initialCameraPolicy"] = options.get("initialCameraPolicy")
        if initial:
            view = manifest.setdefault(
                "initialView",
                {
                    "target": ((g.centers.min(0) + g.centers.max(0)) / 2).tolist(),
                    "distance": float(np.linalg.norm(np.ptp(g.centers, axis=0))),
                },
            )
            view["layers"] = [
                "splats",
                *[
                    layer["id"]
                    for layer in manifest["layers"]
                    if layer["kind"] in ("images", "panoramas")
                ],
            ]
        (dest / "conversion-report.json").write_text(json.dumps(report, indent=2, allow_nan=False))
        pending = work / "project.json"
        pending.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False))
        if (pkg / "project.json").read_bytes() != before:
            raise ValueError("Paket während add-splats geändert")
        (pkg / "layers").mkdir(exist_ok=True)
        os.rename(dest, pkg / "layers/splats")
        os.replace(pending, pkg / "project.json")
        return manifest
    finally:
        shutil.rmtree(work)
