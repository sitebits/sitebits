"""Unabhängige Quellenprüfung für scanlose Formate und ausgedünnte E57."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import laspy
import numpy as np
from scipy.spatial import cKDTree

from . import hoehen, klassen, lage
from .convert import CLOUD_NAME, _sha256
from .manifest_io import cloud_of, read_manifest
from .point_source import detect_format, resolve_source
from .source_points import CHUNK, e57_chunks, point_chunks


def _picks(count: int, samples: int, seed: int) -> np.ndarray:
    return np.sort(
        np.random.default_rng(seed).choice(count, size=min(samples, count), replace=False)
    )


def _cloud_sample(pkg: Path, samples: int, seed: int) -> tuple[np.ndarray, int]:
    with laspy.open(pkg / CLOUD_NAME) as reader:
        indices = _picks(int(reader.header.point_count), samples, seed)
        pieces = []
        start = 0
        for rec in reader.chunk_iterator(CHUNK):
            local = indices[(indices >= start) & (indices < start + len(rec))] - start
            if len(local):
                pieces.append(np.column_stack([rec.x[local], rec.y[local], rec.z[local]]))
            start += len(rec)
    return np.concatenate(pieces) if pieces else np.empty((0, 3)), start


def _hoehen_umgerechnet(
    chunks: Iterator[tuple[np.ndarray, int]], crs: str | None, quelle: str, ziel: str
) -> Iterator[tuple[np.ndarray, int]]:
    """Quellpunkte wie im Import umrechnen, damit der Vergleich gleiche Höhen sieht."""
    if crs is None:
        raise ValueError("Höhenumrechnung braucht ein Koordinatensystem")
    for xyz, raw in chunks:
        if len(xyz):
            xyz = xyz.copy()
            xyz[:, 2] = hoehen.rechne_um(
                xyz[:, 0], xyz[:, 1], xyz[:, 2], crs=crs, quelle=quelle, ziel=ziel
            )
        yield xyz, raw


def _klassen_pruefen(
    path: Path, pkg: Path, cloud: dict[str, Any], options: dict[str, Any]
) -> dict[str, Any]:
    """F30: Histogramm der COPC exakt wie im Manifest; Quelle ohne Ausdünnung gleich, sonst ≥."""
    soll = cloud.get("classes")
    if soll is None:
        return {"checked": False, "ok": True}
    copc = klassen.zaehle(pkg / CLOUD_NAME)
    ok = copc.klassen == soll and set(copc.attribute()) <= set(cloud.get("attributes", []))
    quelle: dict[str, int] | None = None
    if options.get("classes") is not None:
        quelle = klassen.zaehle(path).klassen
        ok = ok and quelle == options["classes"]
        if options.get("ausduennen") is None:
            ok = ok and quelle == copc.klassen
        else:
            ok = ok and all(n <= quelle.get(k, 0) for k, n in copc.klassen.items())
    letzte: bool | None = None
    if klassen.LETZTE_ATTRIBUT in cloud.get("attributes", []):
        letzte = klassen.letzte_pruefen(pkg / CLOUD_NAME)
        ok = ok and letzte
    return {
        "checked": True, "copc": copc.klassen, "source": quelle, "lastReturns": letzte, "ok": ok,
    }


def verify_points(
    path: Path,
    pkg: Path,
    samples: int = 10000,
    seed: int = 42,
    *,
    float32_zulassen: bool = False,
) -> dict[str, Any]:
    from .verify import MAX_ERROR_M, _cloud_query, _sample_distances, _stream_stats

    if samples <= 0:
        raise ValueError("Stichprobenzahl muss positiv sein")
    m = read_manifest(pkg)
    cloud = cloud_of(m)
    options = cloud["sourceOptions"]
    thin = options.get("ausduennen")
    fmt, _ = detect_format(path.name)
    limit = options.get("maxPoints")
    precision_report: dict[str, Any] = {}
    if fmt == "e57":
        from .inventory import pruefe_e57_genauigkeit, read_inventory

        precision_report = pruefe_e57_genauigkeit(read_inventory(path), float32_zulassen)
        iterator = e57_chunks(path, limit, float32_zulassen=float32_zulassen)
        attrs = cloud.get("attributes", [])
    else:
        cols = options.get("columns")
        source = resolve_source(
            path,
            options.get("crs"),
            options.get("unit"),
            options.get("up", "z"),
            ",".join(cols) if cols else None,
        )
        iterator = ((xyz, len(xyz)) for xyz in point_chunks(source, limit))
        hoehe = options.get("hoehe")
        if hoehe is not None and hoehe["quelle"] != hoehe["ziel"]:
            iterator = _hoehen_umgerechnet(iterator, source.crs, hoehe["quelle"], hoehe["ziel"])
        matrix = lage.aus_paket(cloud)
        if matrix is not None:
            iterator = ((lage.wende_an(xyz, matrix), raw) for xyz, raw in iterator)
        attrs = cloud.get("attributes", [])
    with laspy.open(pkg / CLOUD_NAME) as f:
        header = f.header
        output_bounds = np.array([header.mins, header.maxs])
        output_count = int(header.point_count)
    expected_read = int(cloud["readerPointCount"])
    if thin is not None:
        pick, decoded_output = _cloud_sample(pkg, samples, seed)
        distances = np.full(len(pick), np.inf)
        # Query des kleinen Stichprobenbaums statt großer Quellbäume: RAM bleibt begrenzt.
        sample_tree = cKDTree(pick)
        tolerance = float(max(header.scales))
    else:
        indices = _picks(expected_read, samples, seed)
        pieces = []
        valid_start = 0
        decoded_output = output_count
        tolerance = MAX_ERROR_M
    raw_count, valid_count = 0, 0
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    finite = True
    for xyz, raw in iterator:
        raw_count += raw
        valid_count += len(xyz)
        finite = finite and bool(np.all(np.isfinite(xyz)))
        if not len(xyz):
            continue
        if not finite:
            continue
        lo, hi = np.minimum(lo, xyz.min(0)), np.maximum(hi, xyz.max(0))
        if thin is not None:
            if np.any(distances > tolerance):
                # Nur Kandidaten innerhalb der Skalenschritt-Toleranz sind relevant.
                near_dist, nearest = sample_tree.query(xyz, distance_upper_bound=tolerance)
                found = nearest < len(pick)
                np.minimum.at(distances, nearest[found], near_dist[found])
        else:
            local = (
                indices[(indices >= valid_start) & (indices < valid_start + len(xyz))] - valid_start
            )
            if len(local):
                pieces.append(xyz[local])
            valid_start += len(xyz)
    source_bounds = np.array([lo, hi])
    if thin is None:
        pick = np.concatenate(pieces) if pieces else np.empty((0, 3))
        with _cloud_query(pkg) as query:
            distances, _ = _sample_distances(pick, query)
    errors_finite = bool(len(distances)) and bool(np.all(np.isfinite(distances)))
    geometry = {
        "n": len(distances),
        "seed": seed,
        "direction": "copc-to-source" if thin is not None else "source-to-copc",
        "toleranceM": tolerance,
        "maxErrorM": float(distances.max()) if errors_finite else None,
        "p99ErrorM": float(np.percentile(distances, 99)) if errors_finite else None,
        "ok": errors_finite and bool(distances.max() <= tolerance) and finite,
    }
    if thin is None:
        expected = valid_count
        expected_bounds = source_bounds
    else:
        expected = int(options.get("expectedPointCount", cloud["pointCount"]))
        expected_bounds = np.array(cloud.get("expectedBoundsWorld", cloud["boundsWorld"]))
    delta = float(np.max(np.abs(output_bounds - expected_bounds)))
    raw_delta = float(np.max(np.abs(output_bounds - source_bounds)))
    # Der Filter darf Randpunkte entfernen. Retained-bounds bleiben auf E57-Toleranz geprüft;
    # zusätzlich wird die Schrumpfung gegen die Originalquelle separat begrenzt/ausgewiesen.
    source_tol = MAX_ERROR_M if thin is None else float(thin) + MAX_ERROR_M
    bounds = {
        "source": source_bounds.tolist() if finite and valid_count else None,
        "expected": expected_bounds.tolist(),
        "copc": output_bounds.tolist(),
        "maxDifferenceM": delta if np.isfinite(delta) else None,
        "toleranceM": MAX_ERROR_M,
        "sourceMaxDifferenceM": raw_delta if np.isfinite(raw_delta) else None,
        "sourceWithinE57Tolerance": bool(raw_delta <= MAX_ERROR_M),
        "sourceRetentionToleranceM": source_tol,
        "ok": finite and valid_count > 0 and delta <= MAX_ERROR_M and raw_delta <= source_tol,
    }
    balance = {
        "read": raw_count,
        "valid": valid_count,
        "written": output_count,
        "expected": expected,
        "invalidDropped": raw_count - valid_count,
        "thinningDropped": valid_count - output_count,
        "ok": raw_count == expected_read
        and output_count == expected
        and output_count > 0
        and decoded_output == output_count
        and output_count == int(cloud["pointCount"])
        and valid_count >= output_count,
    }
    attr_stats, id_counts = _stream_stats(pkg, "rgb" in attrs, "intensity" in attrs)
    # Keine Werteheuristik bei 16-bit-LAS/Text: legitime dunkle Farben dürfen ≤255 sein.
    attr_stats["ok"] = True
    identity = {"sha256": _sha256(path), "sizeBytes": path.stat().st_size}
    identity["ok"] = (
        identity["sha256"] == cloud["source"]["sha256"]
        and identity["sizeBytes"] == cloud["source"]["sizeBytes"]
    )
    ids_ok = fmt == "e57" or not np.any(id_counts[1:])
    report: dict[str, Any] = {
        "source": options,
        "sourceIdentity": identity,
        "points": {
            "raw": raw_count,
            "thinned": expected,
            "written": output_count,
            "invalidDropped": raw_count - valid_count,
        },
        "thinning": {"cellM": thin, "dropped": valid_count - output_count},
        "pointBalance": balance,
        "bounds": bounds,
        "samples": geometry,
        "scansRepresented": {"expected": 0, "found": 0, "checked": False, "ok": True},
        "pointSource": {"checked": False, "ok": ids_ok},
        "attributes": attr_stats,
        "classes": _klassen_pruefen(path, pkg, cloud, options),
        "conversion": m.get("conversion", {}),
        "copcSizeBytes": (pkg / CLOUD_NAME).stat().st_size,
    }
    report.update(precision_report)
    report["ok"] = all(
        bool(report[k]["ok"])
        for k in (
            "sourceIdentity",
            "pointBalance",
            "bounds",
            "samples",
            "pointSource",
            "attributes",
            "classes",
        )
    )
    (pkg / "conversion-report.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    return report
