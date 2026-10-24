"""Scanlose Punktformate im bestehenden LAZ → COPC-Ablauf."""

from __future__ import annotations

import dataclasses
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import laspy
import numpy as np

from . import hoehen, klassen, lage
from .geometry import compute_origin
from .manifest_io import cloud_of, to_v2, write_manifest
from .pdal_runner import (
    SCALE,
    RunStats,
    build_copc_pipeline,
    build_point_pipeline,
    combine_stats,
    run_pipeline,
    run_untwine,
)
from .point_source import PointSource, resolve_source
from .source_points import SourceStats, inspect_points
from .tiled_thinning import thin_tiles


def _klassen(
    source: PointSource, cloud_path: Path, max_points: int | None
) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    """F30: (zusätzliche Attribute, Felder für ``cloud``, Felder für ``sourceOptions``).

    Klassen und Echos gibt es nur in LAS/LAZ/COPC-Quellen. Die Quellstatistik zählt die ganze
    Datei und entfällt deshalb bei ``--max-points``.
    """
    if source.reader != "readers.las":
        return [], {}, {}
    ausgabe = klassen.zaehle(cloud_path)
    quelle = {} if max_points is not None else {"classes": klassen.zaehle(source.path).klassen}
    return ausgabe.attribute(), {"classes": ausgabe.klassen}, quelle


def convert_points(
    path: Path,
    out_dir: Path,
    crs: str | None,
    max_points: int | None = None,
    mode: str = "auto",
    unit: str | None = None,
    up: str = "z",
    columns: str | None = None,
    ausduennen: float | None = None,
    *,
    hoehe: tuple[str | None, str | None] = (None, None),
    lage_: lage.Lage | None = None,
    rgb_8bit: bool = False,
) -> dict[str, Any]:
    from .convert import CLOUD_NAME, _read_json_or_none, _sha256, carry_over_blocks, choose_mode

    if max_points is not None and max_points <= 0:
        raise ValueError("--max-points muss positiv sein")
    if path.resolve() == (out_dir / CLOUD_NAME).resolve():
        raise ValueError("Quelldatei darf nicht die COPC-Ausgabedatei sein")
    source = resolve_source(path, crs, unit, up, columns)
    info = inspect_points(source, max_points)
    if rgb_8bit:
        pruefe_rgb_8bit(source, info)
        info = dataclasses.replace(info, rgb_8bit=True)
    chosen = choose_mode(info.count, mode)
    offset = compute_origin(*info.bounds)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir = out_dir.resolve()
    if source.path.parent == out_dir:
        mounts, src = {out_dir: "/out"}, f"/out/{source.path.name}"
    else:
        mounts, src = {source.path.parent: "/src:ro", out_dir: "/out"}, f"/src/{source.path.name}"
    stage = Path(tempfile.mkdtemp(prefix="_staging-", dir=out_dir))
    try:
        rel = f"/out/{stage.name}"
        s1 = run_pipeline(
            build_point_pipeline(
                source,
                src,
                f"{rel}/points.laz",
                offset,
                max_points,
                preserve_coordinates=ausduennen is not None,
            ),
            mounts,
            stream=source.format != "pts",
        )
        if s1.returncode:
            raise RuntimeError(f"Punktimport fehlgeschlagen: {s1.stderr_tail}")
        if ausduennen is not None:
            thin_stats = thin_tiles(stage / "points.laz", stage / "thinned.laz", ausduennen)
            s1 = combine_stats([s1, thin_stats])
            (stage / "thinned.laz").replace(stage / "points.laz")
        if info.rgb_8bit:
            s1 = combine_stats([s1, _rgb_auf_16_bit(stage, rel, mounts)])
        _hoehe_und_lage_anpassen(stage, source.crs, hoehe, lage_)
        # F30: letzte Echos in UserData markieren (LAS-Quellen mit späteren Echos, UserData frei).
        letzte = source.reader == "readers.las" and klassen.letzte_markieren(
            stage / "points.laz", stage / "letzte.laz"
        )
        if letzte:
            (stage / "letzte.laz").replace(stage / "points.laz")
        with laspy.open(stage / "points.laz") as reader:
            expected_count = int(reader.header.point_count)
            expected_bounds = [reader.header.mins.tolist(), reader.header.maxs.tolist()]
        if chosen == "staged":
            (stage / "untwine").mkdir()
            s2 = run_untwine(f"{rel}/points.laz", f"/out/{CLOUD_NAME}", f"{rel}/untwine", mounts)
        else:
            s2 = run_pipeline(
                build_copc_pipeline(f"{rel}/points.laz", f"/out/{CLOUD_NAME}"), mounts
            )
        stats = combine_stats([s1, s2])
        if stats.returncode:
            raise RuntimeError(f"COPC-Konvertierung fehlgeschlagen: {stats.stderr_tail}")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    with laspy.open(out_dir / CLOUD_NAME) as f:
        count = int(f.header.point_count)
        lo, hi = f.header.mins.tolist(), f.header.maxs.tolist()
    if count != expected_count:
        raise RuntimeError(f"Punktbilanz: erwartet {expected_count}, geschrieben {count}")
    klassen_attrs, cloud_klassen, quell_klassen = _klassen(source, out_dir / CLOUD_NAME, max_points)
    identity = {
        "filename": source.path.name,
        "sha256": _sha256(source.path),
        "sizeBytes": source.path.stat().st_size,
        "format": source.format,
    }
    conversion = {"seconds": stats.seconds, "peakRssMb": stats.peak_rss_mb, "mode": chosen}
    m = to_v2(
        {
            "schemaVersion": 1,
            "projectId": out_dir.name,
            "revision": 1,
            "source": identity,
            "crs": {
                "wkt": source.crs,
                "verticalDatum": hoehe[1],
                "unit": "m",
                "declaredBy": source.crs_from,
            },
            "originWorld": list(compute_origin(lo, hi)),
            "cloud": {
                "url": CLOUD_NAME,
                "pointCount": count,
                "boundsWorld": [lo, hi],
                "attributes": info.attributes
                + klassen_attrs
                + ([klassen.LETZTE_ATTRIBUT] if letzte else []),
                "scale": SCALE,
                "offset": list(offset),
                "readerPointCount": info.count,
                "invalidDropped": 0,
                "pointSourceIsScan": False,
            },
            "stations": {"url": "stations.json", "count": 0},
            "initialView": {
                "target": [(a + b) / 2 for a, b in zip(lo, hi, strict=True)],
                "distance": float(np.linalg.norm(np.subtract(hi, lo))),
            },
            "conversion": conversion,
        }
    )
    m["frame"]["unitDeclaredBy"] = source.unit_from
    cloud = cloud_of(m)
    cloud["provenance"] = {
        "kind": "other",
        "interpolated": False,
        "scaleVerified": False,
        "scaleSource": f"unit:{source.unit_from}",
    }
    source_block = {
        **identity,
        "crs": source.crs,
        "crsDeclaredBy": source.crs_from,
        "unit": source.unit,
        "unitDeclaredBy": source.unit_from,
        "up": source.up,
        "columns": list(source.columns),
        "maxPoints": max_points,
    }
    source_block["ausduennen"] = ausduennen
    if hoehe[0] is not None:
        source_block["hoehe"] = hoehen.quellblock(hoehe[0], hoehe[1] or hoehe[0])
    if lage_ is not None:
        source_block["lage"] = lage.quellblock(lage_)
    source_block["expectedPointCount"] = expected_count
    source_block.update(quell_klassen)
    cloud.update(cloud_klassen)
    cloud["expectedBoundsWorld"] = expected_bounds
    cloud["sourceOptions"] = source_block
    cloud["thinningDropped"] = info.count - count
    m = carry_over_blocks(_read_json_or_none(out_dir / "project.json"), m, out_dir)
    write_manifest(out_dir, m)
    (out_dir / "inventory.json").write_text(
        json.dumps({"source": source_block, **info.as_json(), "scans": [], "images": []}, indent=2)
    )
    (out_dir / "conversion-report.json").write_text(
        json.dumps(
            {
                "source": source_block,
                "points": {"raw": info.count, "thinned": expected_count, "written": count},
                "conversion": conversion,
                "thinning": {"cellM": ausduennen, "dropped": info.count - expected_count},
                "ok": False,
            },
            indent=2,
            allow_nan=False,
        )
    )
    return m


def pruefe_rgb_8bit(source: PointSource, info: SourceStats) -> None:
    """`--rgb-8bit` nur für LAS/LAZ mit Farbe, und nur wenn wirklich jeder Wert ≤ 255 ist.

    LAS kennt nur 16-bit-Farbe; ODM und andere schreiben trotzdem 0 bis 255 hinein. Aus den Zahlen
    wird das nicht geraten (Angabe des Menschen), aber geprüft: PDAL verwirft eine Zuweisung über
    65535 still, statt abzubrechen — ein Wert über 255 ergäbe sonst falsche Farben ohne Meldung.
    """
    if source.reader != "readers.las" or "rgb" not in info.attributes:
        raise ValueError("--rgb-8bit gilt nur für LAS/LAZ mit Farbe")
    with laspy.open(source.path) as reader:
        for chunk in reader.chunk_iterator(5_000_000):
            groesster = max(int(chunk.red.max()), int(chunk.green.max()), int(chunk.blue.max()))
            if groesster > 255:
                raise ValueError(
                    f"--rgb-8bit: Farbwert {groesster} über 255 — die Datei hat 16-bit-Farbe"
                )


def _rgb_auf_16_bit(stage: Path, rel: str, mounts: dict[Path, str]) -> RunStats:
    # readers.ply deklariert uchar als uint8: assign *257 würde dort überlaufen.
    # LAZ ist die Typgrenze; readers.las liest Red/Green/Blue danach als uint16.
    pipe: list[dict[str, Any]] = [
        {"type": "readers.las", "filename": f"{rel}/points.laz"},
        {
            "type": "filters.assign",
            "value": ["Red = Red * 257", "Green = Green * 257", "Blue = Blue * 257"],
        },
        {
            "type": "writers.las",
            "filename": f"{rel}/rgb.laz",
            "compression": True,
            "minor_version": 4,
            "dataformat_id": 7,
            "forward": "all",
        },
    ]
    rgb_stats = run_pipeline(pipe, mounts, stream=True)
    if rgb_stats.returncode:
        raise RuntimeError(f"RGB-Skalierung fehlgeschlagen: {rgb_stats.stderr_tail}")
    (stage / "rgb.laz").replace(stage / "points.laz")
    return rgb_stats


def _hoehe_und_lage_anpassen(
    stage: Path,
    crs: str | None,
    hoehe: tuple[str | None, str | None],
    lage_: lage.Lage | None,
) -> None:
    quelle, ziel = hoehe
    umrechnen = quelle is not None and ziel is not None and quelle != ziel
    if not umrechnen and lage_ is None:
        return
    if umrechnen and crs is None:
        raise ValueError("Höhenumrechnung braucht ein Koordinatensystem (--crs)")
    lage.las_anpassen(
        stage / "points.laz",
        stage / "angepasst.laz",
        crs=crs,
        hoehe=(quelle, ziel) if umrechnen else None,  # type: ignore[arg-type]
        matrix=lage_.matrix if lage_ else None,
    )
    (stage / "angepasst.laz").replace(stage / "points.laz")
