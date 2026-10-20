"""Mesh-Ebene atomar vorbereiten und erst nach bestandenem verify veröffentlichen."""

from __future__ import annotations

import copy
import json
import os
import resource
import shutil
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from . import hoehen, lage
from .convert import _sha256
from .geometry import compute_origin
from .manifest_io import cloud_of, origin_of, read_manifest
from .mesh_gltf import write_glb
from .mesh_preview import vertex_preview
from .mesh_quantization import quantize_positions
from .mesh_source import Mesh, read_mesh
from .mesh_stages import degenerate_mask, remove_degenerate, simplify_with_report
from .mesh_textures import encode_texture
from .mesh_verify import verify_mesh
from .point_source import _same_crs

# Mobilstufe für Handys: Safari auf dem iPhone beendet den Tab an einer Vollstufe mit
# 4096-px-UASTC-Texturen. Gleiches Format wie model.glb, aber ETC1S und höchstens 2048 px.
MOBIL_MAX_PX = 2048
MOBIL_KODIERUNG = "etc1s"
MOBIL_WARNGRENZE_BYTES = 100 * 1024 * 1024
MOBIL_URL = "layers/mesh/mobile.glb"
MOBIL_DREIECKE = 400000


def stage_mesh(
    mesh: Mesh, target: int, origin: np.ndarray, lock_border: bool = False
) -> tuple[Mesh, dict[str, Any], dict[str, Any]]:
    """Auf ``target`` verkleinern (0 = alle Dreiecke); das Ziel nach Entartungsbereinigung und
    Quantisierung nachführen."""
    goal = target
    for _ in range(6):
        candidate, info = simplify_with_report(mesh, goal, lock_border)
        candidate, removal = remove_degenerate(candidate)
        quantized_positions, _, _, _ = quantize_positions(candidate.positions, origin)
        mask, _ = degenerate_mask(quantized_positions[:, :3], candidate.triangles)
        delivered = len(candidate.triangles) - int(mask.sum())
        effective = min(target, len(mesh.triangles)) if target else delivered
        if (
            target == 0
            or target >= len(mesh.triangles)
            or abs(delivered / effective - 1) <= 0.05
        ):
            info.update(ziel=target, erreicht=delivered, zielErreicht=True, grund=None)
            return candidate, info, removal
        if not info["zielErreicht"] or goal >= len(mesh.triangles):
            info.update(ziel=target, erreicht=delivered, zielErreicht=False)
            return candidate, info, removal
        goal = min(len(mesh.triangles), max(goal + 1, goal + target - delivered))
    raise ValueError(f"Dreiecksziel {target} nach Entartungsbereinigung nicht erreichbar")


def write_stage(
    mesh: Mesh, path: Path, origin: np.ndarray, max_px: int, encoding: str
) -> tuple[list[dict[str, Any]], dict[str, Any], float]:
    """Texturen kodieren und die Stufe schreiben: (Texturberichte, Quantisierungsbereinigung,
    Positionsschritt)."""
    encoded, reports = {}, []
    for material in np.unique(mesh.materials):
        texture = mesh.textures[int(material)]
        if texture is not None:
            data, report = encode_texture(texture, max_px, encoding)
            encoded[int(material)] = data
            reports.append(report)
    quant_removal: dict[str, Any] = {}
    if path.exists() or path.is_symlink():
        # Nie in eine vorhandene Datei schreiben: sie könnte ein Hardlink auf ein Paket sein.
        path.unlink()
    spacing = write_glb(mesh, path, origin, encoded, quant_removal)
    return reports, quant_removal, spacing


def mobil_ziel(mobil_dreiecke: int, voll_dreiecke: int) -> int:
    """Die Mobilstufe hat nie mehr Dreiecke als die Vollstufe (Vollstufe 0 = ungekürzt)."""
    return min(mobil_dreiecke, voll_dreiecke) if voll_dreiecke else mobil_dreiecke


def mobil_felder(
    layer: dict[str, Any],
    info: dict[str, Any],
    removal: dict[str, Any],
    step: float,
    reports: list[dict[str, Any]],
    ziel: int,
    size: int,
) -> None:
    """Datenvertrag der Mobilstufe in die Mesh-Ebene schreiben (der Viewer lädt sie auf Touch)."""
    layer["mobileUrl"] = MOBIL_URL
    layer["mobileSizeBytes"] = size
    layer["mobileTriangleCount"] = info["erreicht"]
    layer.setdefault("stufen", {})["mobile"] = info
    layer.setdefault("positionStepsM", {})["mobile"] = step
    layer.setdefault("degenerateRemoval", {})["mobile"] = removal
    layer.setdefault("textures", {}).setdefault("stages", {})["mobile"] = reports
    layer.setdefault("sourceOptions", {})["mobilDreiecke"] = ziel
    if size > MOBIL_WARNGRENZE_BYTES:
        print(
            f"WARNUNG Mobilstufe: {size / 2**20:.0f} MiB > 100 MiB — auf Handys evtl. zu groß",
            file=sys.stderr,
        )


def prepare_layer(
    source: Mesh,
    work: Path,
    origin: np.ndarray,
    dreiecke: int,
    vorschau_dreiecke: int,
    toleranz_m: float,
    mobil_dreiecke: int = 0,
) -> dict[str, Any]:
    t0 = time.monotonic()
    dest = work / "layers/mesh"
    dest.mkdir(parents=True)
    clean_source, source_removal = remove_degenerate(source)

    full, full_info, full_removal = stage_mesh(clean_source, dreiecke, origin)
    preview, preview_info, preview_removal = stage_mesh(
        vertex_preview(clean_source), vorschau_dreiecke, origin
    )
    removals: dict[str, Any] = {
        "source": source_removal,
        "model": {"simplification": full_removal},
        "preview": {"simplification": preview_removal},
    }
    # (Name, Mesh, Stufenbericht, Texturgröße, Kodierung); 0 Mobildreiecke = keine Mobilstufe.
    stages: list[tuple[str, Mesh, dict[str, Any], int, str]] = [
        ("model", full, full_info, 4096, "uastc"),
        ("preview", preview, preview_info, 1024, "etc1s"),
    ]
    mobil = mobil_ziel(mobil_dreiecke, dreiecke)
    if mobil_dreiecke:
        # Ränder gesperrt: keine Risse zwischen Texturseiten, Ausdehnung bleibt (mesh_stages).
        mobile, mobile_info, mobile_removal = stage_mesh(
            clean_source, mobil, origin, lock_border=True
        )
        removals["mobile"] = {"simplification": mobile_removal}
        stages.append(("mobile", mobile, mobile_info, MOBIL_MAX_PX, MOBIL_KODIERUNG))
    if not preview_info["zielErreicht"]:
        raise ValueError(preview_info["grund"])
    for name, _, info, _, _ in stages:
        if not info["zielErreicht"]:
            print(
                f"WARNUNG Stufe {name}: {info['grund']} — kleinste nahterhaltende Stufe verwendet",
                file=sys.stderr,
            )
    texture_stats: dict[str, list[dict[str, Any]]] = {}
    position_steps: dict[str, float] = {}
    for name, mesh, info, max_px, encoding in stages:
        reports, quant_removal, spacing = write_stage(
            mesh, dest / f"{name}.glb", origin, max_px, encoding
        )
        texture_stats[name] = reports
        removals[name]["quantization"] = quant_removal
        info["erreicht"] = len(mesh.triangles) - quant_removal["removed"]
        position_steps[name] = spacing
    step = position_steps["model"]
    src = source.source
    lo, hi = full.positions.min(axis=0).tolist(), full.positions.max(axis=0).tolist()
    flat_stats = [r for stage in texture_stats.values() for r in stage]
    layer: dict[str, Any] = {
        "id": "mesh",
        "kind": "mesh",
        "title": src.path.stem,
        "format": "gltf",
        "url": "layers/mesh/model.glb",
        "previewUrl": "layers/mesh/preview.glb",
        "previewColor": "vertex",
        "triangleCount": full_info["erreicht"],
        "degenerateRemoval": removals,
        "vertexCount": len(full.positions),
        "boundsWorld": [lo, hi],
        "upAxisSource": src.up,
        "upDeclaredBy": "format"
        if src.format in ("gltf", "glb")
        else (
            "sidecar" if src.path.with_name(src.path.name + ".scanview.json").exists() else "cli"
        ),
        "positionStepM": step,
        "positionStepsM": position_steps,
        "geometryCompression": "EXT_meshopt_compression",
        "source": {
            "filename": src.path.name,
            "sha256": _sha256(src.path),
            "sizeBytes": src.path.stat().st_size,
            "producer": None,
        },
        "sourceOptions": {
            "crs": src.crs,
            "unit": src.unit,
            "up": src.up,
            "dreiecke": dreiecke,
            "vorschauDreiecke": vorschau_dreiecke,
            "toleranzM": toleranz_m,
        },
        "stufen": {"model": full_info, "preview": preview_info},
        "textures": {
            "count": len(texture_stats["model"]),
            "maxPx": max((max(r["width"], r["height"]) for r in texture_stats["model"]), default=0),
            "encoding": "ktx2-uastc" if texture_stats["model"] else "none",
            "stages": texture_stats,
        },
        "provenance": {
            "kind": "other",
            "interpolated": True,
            "scaleVerified": False,
            "scaleSource": f"unit:{src.unit_from}",
        },
        "frameCheck": None,
        "verify": {"ok": False, "report": "layers/mesh/conversion-report.json"},
        "conversion": {
            "seconds": time.monotonic() - t0,
            "containerPeakRssMb": max((r["peakRssMb"] for r in flat_stats), default=0),
            "containerSeconds": sum(r["seconds"] for r in flat_stats),
            "hostPeakRssMb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        },
    }
    if mobil_dreiecke:
        mobil_felder(
            layer,
            mobile_info,
            removals["mobile"],
            position_steps["mobile"],
            texture_stats["mobile"],
            mobil,
            (dest / "mobile.glb").stat().st_size,
        )
    return layer


# Ausdehnungstoleranz höchstens 0,5 m: Ein fehlender Gebäudeteil verändert die Ausdehnung um Meter.
AUSDEHNUNG_MAX_MM = 500.0


def _options(
    dreiecke: int, vorschau_dreiecke: int, toleranz: float, mobil_dreiecke: int = 0
) -> None:
    ziele_ok = min(dreiecke, vorschau_dreiecke, mobil_dreiecke) >= 0
    if not ziele_ok or not np.isfinite(toleranz) or toleranz <= 0:
        raise ValueError("Ungültiges Dreiecksziel oder --toleranz (mm)")


def positions_hoehe(positions: np.ndarray, crs: str | None,
                    quelle: str | None, ziel: str | None) -> np.ndarray:
    """Gemeinsame Höhenbehandlung für Mesh und Gaussian-Mittelpunkte."""
    if quelle is None or ziel is None or quelle == ziel:
        return positions
    if crs is None:
        raise ValueError("Höhenumrechnung braucht ein Koordinatensystem (--crs)")
    return hoehen.positionen_umrechnen(positions, crs=crs, quelle=quelle, ziel=ziel)


def _mesh_hoehe(source: Mesh, quelle: str | None, ziel: str | None) -> Mesh:
    return replace(source, positions=positions_hoehe(source.positions, source.source.crs,
                                                    quelle, ziel))


def convert_mesh(
    path: Path,
    pkg: Path,
    crs: str | None = None,
    unit: str | None = None,
    up: str | None = None,
    dreiecke: int = 1000000,
    vorschau_dreiecke: int = 200000,
    toleranz: float = 10,
    hoehe: tuple[str | None, str | None] = (None, None),
    mobil_dreiecke: int = MOBIL_DREIECKE,
) -> dict[str, Any]:
    _options(dreiecke, vorschau_dreiecke, toleranz, mobil_dreiecke)
    if pkg.exists():
        raise FileExistsError(f"Paket existiert bereits: {pkg}")
    quelle, ziel = hoehen.festlegen(*hoehe)
    source = _mesh_hoehe(read_mesh(path, crs, unit, up), quelle, ziel)
    lo, hi = source.positions.min(axis=0), source.positions.max(axis=0)
    origin = np.asarray(compute_origin(lo.tolist(), hi.tolist()), dtype=np.float64)
    pkg.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".mesh-", dir=pkg.parent))
    try:
        layer = prepare_layer(source, work, origin, dreiecke, vorschau_dreiecke,
                              toleranz / 1000, mobil_dreiecke)
        if quelle is not None and ziel is not None:
            layer["sourceOptions"]["hoehe"] = hoehen.quellblock(quelle, ziel)
        manifest = {
            "schemaVersion": 2,
            "projectId": pkg.name,
            "revision": 1,
            "title": source.source.path.stem,
            "frame": {
                "crs": {
                    "wkt": source.source.crs,
                    "verticalDatum": ziel,
                    "declaredBy": source.source.crs_from,
                },
                "unit": "m",
                "unitDeclaredBy": source.source.unit_from,
                "originWorld": origin.tolist(),
            },
            "layers": [layer],
            "initialView": {
                "target": ((lo + hi) / 2).tolist(),
                "distance": float(np.linalg.norm(hi - lo)),
            },
        }
        report = verify_mesh(path, work, source, manifest)
        if not report["ok"]:
            raise ValueError(f"Mesh-verify fehlgeschlagen: {json.dumps(report)}")
        layer["verify"]["ok"] = True
        (work / "project.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False)
        )
        if pkg.exists():
            raise FileExistsError(f"Paket inzwischen angelegt: {pkg}")
        os.rename(work, pkg)
        return manifest
    finally:
        if work.exists():
            shutil.rmtree(work)


def _add_hoehe(
    hoehe: tuple[str | None, str | None], paket: str | None
) -> tuple[str | None, str | None]:
    """Beim Ergänzen bestimmt das Paket das Ziel (Entscheidung 0015)."""
    quelle_arg, ziel_arg = hoehe
    if quelle_arg is None:
        if ziel_arg is not None:
            raise ValueError("Zielhöhensystem ohne Quellhöhensystem: --quell-hoehensystem fehlt")
        return None, None
    ziel = hoehen.normiere(paket)
    if ziel is None:
        raise ValueError("Paket-Höhensystem unbekannt; Mesh-Höhen nicht umrechenbar")
    if ziel_arg is not None and hoehen.normiere(ziel_arg) != ziel:
        raise ValueError("Zielhöhensystem widerspricht dem Paket")
    quelle, _ = hoehen.festlegen(quelle_arg, ziel)
    return quelle, ziel


def add_mesh(
    path: Path,
    pkg: Path,
    same_frame: bool = False,
    crs: str | None = None,
    unit: str | None = None,
    up: str | None = None,
    dreiecke: int = 1000000,
    vorschau_dreiecke: int = 200000,
    toleranz: float = 10,
    hoehe: tuple[str | None, str | None] = (None, None),
    rahmen_grenzen: tuple[float, float] | None = None,
    rahmen_ohne_klassen: tuple[int, ...] = (),
    ausdehnung_toleranz: float | None = None,
    mobil_dreiecke: int = MOBIL_DREIECKE,
) -> dict[str, Any]:
    from .mesh_frame import frame_check, grenzen_pruefen

    _options(dreiecke, vorschau_dreiecke, toleranz, mobil_dreiecke)
    grenzen_pruefen(rahmen_grenzen)
    if ausdehnung_toleranz is not None and not 0 < ausdehnung_toleranz <= AUSDEHNUNG_MAX_MM:
        raise ValueError(f"--ausdehnung-toleranz muss in (0, {AUSDEHNUNG_MAX_MM:g}] mm liegen")
    if not same_frame:
        raise ValueError("Mesh hinzufügen benötigt --same-frame")
    raw_manifest = (pkg / "project.json").read_bytes()
    original = read_manifest(pkg)
    if original.get("schemaVersion") != 2:
        raise ValueError("add benötigt Schema 2; Paket zuerst explizit migrieren")
    if any(lay.get("kind") == "mesh" or lay.get("id") == "mesh" for lay in original["layers"]):
        raise ValueError("Mesh-Ebene existiert bereits")
    if (pkg / "layers/mesh").exists():
        raise FileExistsError("layers/mesh existiert bereits")
    cloud = cloud_of(original)
    quelle, ziel = _add_hoehe(hoehe, original["frame"]["crs"].get("verticalDatum"))
    source = _mesh_hoehe(read_mesh(path, crs, unit, up), quelle, ziel)
    # Wie bei Fotos und Panoramen: erst Höhe, dann die Lagematrix der Wolke (frameCheck belegt es).
    lagematrix = lage.aus_paket(cloud)
    if lagematrix is not None:
        source = replace(source, positions=lage.wende_an(source.positions, lagematrix))
    if not _same_crs(source.source.crs, original["frame"]["crs"]["wkt"]):
        raise ValueError("Mesh-CRS widerspricht dem Paketrahmen")
    if original["frame"]["unit"] != "m":
        raise ValueError("Paketeinheit muss metrisch sein")
    declared_unit = cloud.get("sourceOptions", {}).get("unit", "m")
    if declared_unit != source.source.unit:
        raise ValueError("Quelleneinheiten von Mesh und Punktwolke widersprechen sich")
    origin = np.array(origin_of(original), dtype=np.float64)
    work = Path(tempfile.mkdtemp(prefix=".mesh-", dir=pkg))
    try:
        layer = prepare_layer(source, work, origin, dreiecke, vorschau_dreiecke,
                              toleranz / 1000, mobil_dreiecke)
        if quelle is not None and ziel is not None:
            layer["sourceOptions"]["hoehe"] = hoehen.quellblock(quelle, ziel)
        if lagematrix is not None:
            layer["sourceOptions"]["lage"] = copy.deepcopy(cloud["sourceOptions"]["lage"])
        if ausdehnung_toleranz is not None:
            # Eigene, ausdrücklich gesetzte Ausdehnungsgrenze; vermerkt, verify liest sie.
            layer["sourceOptions"]["ausdehnungToleranzM"] = ausdehnung_toleranz / 1000
        manifest = copy.deepcopy(original)
        manifest["layers"].append(layer)
        rahmen = frame_check(
            pkg, cloud, work / layer["url"], origin, rahmen_grenzen, rahmen_ohne_klassen
        )
        # Beide Tore rechnen, dann einmal mit beiden Berichten abbrechen (ein Lauf, alle Zahlen).
        # verify ohne frameCheck in der Ebene: die Wolke nicht ein zweites Mal lesen.
        report = verify_mesh(path, work, source, manifest, point_pkg=pkg)
        layer["frameCheck"] = report["frameCheck"] = rahmen
        bericht = work / "layers/mesh/conversion-report.json"
        bericht.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
        fehler = [
            f"frameCheck fehlgeschlagen: {rahmen}" if not rahmen["ok"] else "",
            f"Mesh-verify fehlgeschlagen: {json.dumps(report['stages'])}"
            if not report["ok"]
            else "",
        ]
        # Stufenzahlen immer mitgeben: der Arbeitsordner wird beim Abbruch gelöscht.
        felder = ("counts", "stageToSource", "sourceToStage", "boundsMaxMm", "sizeBytes", "ok")
        stufen = {k: {m: v.get(m) for m in felder} for k, v in report["stages"].items()}
        fehler.append("Stufen: " + json.dumps(stufen) if any(fehler) and report["ok"] else "")
        if any(fehler):
            raise ValueError(" | ".join(f for f in fehler if f))
        layer["verify"]["ok"] = True
        if (pkg / "project.json").read_bytes() != raw_manifest:
            raise ValueError("Paketmanifest während add geändert; keine Veröffentlichung")
        (pkg / "layers").mkdir(exist_ok=True)
        manifest_tmp = work / "project.json"
        manifest_tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False))
        os.rename(work / "layers/mesh", pkg / "layers/mesh")
        os.replace(manifest_tmp, pkg / "project.json")
        return manifest
    finally:
        shutil.rmtree(work)
