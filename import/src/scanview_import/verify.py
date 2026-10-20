"""Unabhängige Prüfung: E57 (pye57, Pose angewendet) gegen COPC (laspy).

Punktbilanz und Attributspannen kommen aus dem COPC-Header bzw. gestreamten Chunks
(kein ``laspy.read()`` der ganzen Datei — bei ~1 Mrd. Punkten wären das rund 30 GiB
RAM). Die Geometrie-Stichprobe liest pro Scan nur den zur
Sample-Bounding-Box passenden COPC-Ausschnitt (``CopcReader.query``); ist die COPC
ungültig (z. B. weil eine Manipulation sie zu reinem LAZ gemacht hat), fällt der Code
auf einen einmaligen vollen ``laspy.read()`` zurück.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import laspy
import numpy as np
import pye57
from scipy.spatial import cKDTree

from .convert import CLOUD_NAME
from .inventory import pruefe_e57_genauigkeit, read_inventory
from .manifest_io import cloud_of

# 1 mm Spezifikation + halber Quantisierungsschritt (0,5 mm) — wie TOL in test_convert.py.
MAX_ERROR_M = 0.0015
# Rand um jede Kachel-Bbox beim COPC-Query, damit Randpunkte nicht fehlen.
QUERY_MARGIN_M = 0.05
# Feste Kachelgröße für die COPC-Query je Cluster von Sample-Punkten (Review-Fix): eine
# einzelne Bbox über die ganze Stichprobe eines Scans würde auf ortsweiten TLS-Scans (45
# sich überlappende Scans, ~1 Mrd. Punkte gesamt) fast die ganze Site abdecken und damit
# pro Scan einen Near-Voll-Decode des Octrees samt großem cKDTree erzwingen. Mit einer
# festen Kachelgröße bleibt jede Query-Box unabhängig von der Sample-Ausdehnung auf
# (CELL_SIZE_M + 2 · QUERY_MARGIN_M) begrenzt.
CELL_SIZE_M = 1.0
# Chunkgröße beim Streamen der Attributspannen, um die ganze COPC nicht auf einmal zu laden.
ATTR_CHUNK = 5_000_000

# Liefert für eine Box (lo, hi) die nahen Cloud-Punkte (N, 3) und ihre PointSourceId (N,).
QueryNear = Callable[[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]


def _header_and_dims(pkg: Path) -> tuple[laspy.LasHeader, set[str]]:
    with laspy.open(pkg / CLOUD_NAME) as reader:
        header = reader.header
        return header, set(header.point_format.dimension_names)


def _stream_stats(
    pkg: Path, want_rgb: bool, want_intensity: bool
) -> tuple[dict[str, int | None], np.ndarray]:
    """Attributspannen und PointSourceId-Zählung per Chunk-Streaming — kein
    ``laspy.read()`` der ganzen Datei."""
    rgb_min: int | None = None
    rgb_max: int | None = None
    intensity_min: int | None = None
    intensity_max: int | None = None
    id_counts = np.zeros(1, dtype=np.int64)
    with laspy.open(pkg / CLOUD_NAME) as reader:
        while True:
            chunk = reader.read_points(ATTR_CHUNK)
            if len(chunk) == 0:
                break
            c = np.bincount(np.asarray(chunk.point_source_id))
            if len(c) > len(id_counts):
                id_counts = np.pad(id_counts, (0, len(c) - len(id_counts)))
            id_counts[: len(c)] += c
            if want_rgb:
                lo = int(min(chunk.red.min(), chunk.green.min(), chunk.blue.min()))
                hi = int(max(chunk.red.max(), chunk.green.max(), chunk.blue.max()))
                rgb_min = lo if rgb_min is None else min(rgb_min, lo)
                rgb_max = hi if rgb_max is None else max(rgb_max, hi)
            if want_intensity:
                lo_i = int(chunk.intensity.min())
                hi_i = int(chunk.intensity.max())
                intensity_min = lo_i if intensity_min is None else min(intensity_min, lo_i)
                intensity_max = hi_i if intensity_max is None else max(intensity_max, hi_i)
    attrs: dict[str, int | None] = {
        "rgbMin": rgb_min,
        "rgbMax": rgb_max,
        "intensityMin": intensity_min,
        "intensityMax": intensity_max,
    }
    return attrs, id_counts


@contextmanager
def _cloud_query(pkg: Path) -> Iterator[QueryNear]:
    """Liefert eine Funktion, die für eine Bounding-Box (lo, hi) die nahen Cloud-Punkte holt.

    Bevorzugt ``CopcReader.query`` je Scan (Speicher). Ist die Datei keine
    gültige COPC mehr (kein/inkonsistentes COPC-VLR), fällt der Code auf einen einmaligen
    vollen ``laspy.read()`` zurück und filtert danach im Speicher — für die Fixture-Größen
    unbedenklich; auf Echtdaten ist die COPC stets gültig.
    """
    try:
        reader = laspy.CopcReader.open(pkg / CLOUD_NAME)
    except (laspy.LaspyException, IndexError):
        reader = None
    if reader is None:
        las = laspy.read(pkg / CLOUD_NAME)
        full = np.column_stack([np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)])
        full_ids = np.asarray(las.point_source_id)

        def query_near_fallback(lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            mask = np.all((full >= lo) & (full <= hi), axis=1)
            return full[mask], full_ids[mask]

        yield query_near_fallback
        return

    with reader:

        def query_near_copc(lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            rec = reader.query(bounds=laspy.Bounds(mins=lo, maxs=hi))
            xyz = np.column_stack([np.asarray(rec.x), np.asarray(rec.y), np.asarray(rec.z)])
            return xyz, np.asarray(rec.point_source_id)

        yield query_near_copc


def _sample_distances(
    pick: np.ndarray,
    query_near: QueryNear,
    cell: float = CELL_SIZE_M,
    margin: float = QUERY_MARGIN_M,
    own_id: int | None = None,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Nächste-Nachbar-Abstände einer Stichprobe, gequeryt in festen Kacheln statt einer
    Bbox über die ganze Stichprobe (Review-Fix, siehe ``CELL_SIZE_M``).

    Je belegter Kachel (feste Grenzen aus dem Kachelindex, nicht die Min/Max der darin
    liegenden Punkte) genau eine Query mit fester Boxgröße ``cell + 2 · margin`` — die
    Kosten pro Query sind damit unabhängig davon, wie weit die Stichprobe insgesamt
    streut. Kacheln ohne Nachbarn liefern ``inf`` (unverändertes Verhalten ggü. vorher).

    Mit ``own_id`` zusätzlich die Abstände zum nächsten Punkt **mit dieser PointSourceId**
    (die Stichprobe eines Scans muss bei Punkten seiner eigenen ID liegen); sonst
    ist der zweite Rückgabewert ``None``.
    """
    out = np.full(len(pick), np.inf)
    own_out = np.full(len(pick), np.inf) if own_id is not None else None
    cell_index = np.floor(pick / cell).astype(np.int64)
    uniq_cells, inverse = np.unique(cell_index, axis=0, return_inverse=True)
    inverse = np.asarray(inverse).reshape(-1)
    for ci, cell_key in enumerate(uniq_cells):
        members = inverse == ci
        lo = cell_key.astype(np.float64) * cell - margin
        hi = (cell_key.astype(np.float64) + 1.0) * cell + margin
        near, ids = query_near(lo, hi)
        if len(near) == 0:
            continue  # bleibt inf für diese Kachel
        dist, _ = cKDTree(near).query(pick[members])
        out[members] = dist
        if own_out is not None:
            own = near[ids == own_id]
            if len(own):
                own_dist, _ = cKDTree(own).query(pick[members])
                own_out[members] = own_dist
    return out, own_out


def _scan_errors(
    e: pye57.E57,
    index: int,
    per_scan: int,
    rng: np.random.Generator,
    query_near: QueryNear,
    own_id: int | None = None,
) -> tuple[np.ndarray, np.ndarray | None, int]:
    """Fehlerabstände einer Zufallsstichprobe eines Scans (zu allen Punkten und, mit
    ``own_id``, zu den Punkten seiner PointSourceId); zusätzlich dessen gültige Punktzahl
    (für die unabhängige Punktbilanz, Task-4-Concern zu gemischten Quellen)."""
    d = e.read_scan(index, ignore_missing_fields=True, transform=True)
    pts = np.column_stack([d["cartesianX"], d["cartesianY"], d["cartesianZ"]])
    valid_count = len(pts)
    del d
    if valid_count == 0:
        return np.array([], dtype=np.float64), None, 0
    n = min(per_scan, valid_count)
    pick = pts[rng.choice(valid_count, size=n, replace=False)]
    del pts
    dist, own = _sample_distances(pick, query_near, own_id=own_id)
    return dist, own, valid_count


def _point_source_block(
    claimed: bool,
    id_counts: np.ndarray,
    valid_per_scan: list[int],
    own_errors: list[np.ndarray],
    complete: bool,
) -> dict[str, Any]:
    """Prüfung der Scan-Zuordnung (``PointSourceId = Scanindex + 1``).

    - kein Punkt mit ID 0 und keine ID über der Scanzahl;
    - je Scan: Punkte mit seiner ID == seine gültigen Punkte im E57 (nur ohne ``max_points``-
      Kürzung, dieselbe Bedingung wie die Punktbilanz; sonst ``countsOk = None``);
    - je Scan: jede Stichprobe hat einen Punkt **mit seiner eigenen ID** in ≤ ``MAX_ERROR_M``
      (deckt vertauschte IDs bei gleicher Zählung auf).
    Ohne Flag (Altpaket): ``checked = False``, ``ok = True``.
    """
    n_scans = len(valid_per_scan)
    found = [int(id_counts[i + 1]) if i + 1 < len(id_counts) else 0 for i in range(n_scans)]
    zero_ids = int(id_counts[0]) if len(id_counts) else 0
    beyond = int(id_counts[n_scans + 1 :].sum()) if len(id_counts) > n_scans + 1 else 0
    block: dict[str, Any] = {
        "checked": claimed,
        "zeroIds": zero_ids,
        "idsBeyondScanCount": beyond,
        "expectedPerScan": valid_per_scan,
        "foundPerScan": found,
        "countsOk": None,
        "samplesOwnScanMaxErrorM": None,
        "ok": True,
    }
    if not claimed:
        return block
    counts_ok = found == valid_per_scan if complete else None
    own = np.concatenate(own_errors) if own_errors else np.array([], dtype=np.float64)
    own_finite = bool(len(own)) and bool(np.all(np.isfinite(own)))
    block["countsOk"] = counts_ok
    block["samplesOwnScanMaxErrorM"] = float(own.max()) if own_finite else None
    block["ok"] = bool(
        n_scans > 0
        and zero_ids == 0
        and beyond == 0
        and counts_ok is not False
        and own_finite
        and own.max() <= MAX_ERROR_M
    )
    return block


def verify(
    e57: Path, pkg: Path, samples: int = 10000, seed: int = 42, *,
    float32_zulassen: bool = False,
) -> dict[str, Any]:
    manifest = json.loads((pkg / "project.json").read_text())
    cloud = cloud_of(manifest)
    if "sourceOptions" in cloud:
        from .point_verify import verify_points

        return verify_points(e57, pkg, samples, seed, float32_zulassen=float32_zulassen)
    header, dims = _header_and_dims(pkg)
    written = int(header.point_count)
    read = int(cloud["readerPointCount"])
    invalid_dropped = read - written

    # Quellfelder unabhängig aus dem E57-Inventar (nicht aus dem zu prüfenden Manifest).
    inv = read_inventory(e57)
    precision_report = pruefe_e57_genauigkeit(inv, float32_zulassen)
    src_fields = {f for s in inv.scans for f in s.fields}
    want_rgb = {"red", "green", "blue"} <= dims and "colorRed" in src_fields
    want_intensity = "intensity" in dims and "intensity" in src_fields
    attrs, id_counts = _stream_stats(pkg, want_rgb, want_intensity)
    # Nur Pakete mit dem Flag werden auf Scan-IDs geprüft (ohne Flag
    # gültig, der Viewer warnt).
    ids_claimed = cloud.get("pointSourceIsScan") is True

    e = pye57.E57(str(e57))
    try:
        scan_count = e.scan_count
        found = 0
        errors: list[np.ndarray] = []
        own_errors: list[np.ndarray] = []
        valid_per_scan: list[int] = []
        total_raw = 0
        total_valid = 0
        if scan_count == 0:
            # Kein Scan im E57 — nichts zu vergleichen. Explizit statt einer
            # ZeroDivisionError bei `samples // scan_count` weiter unten, und explizit
            # statt einer Vacuous-Truth in scansRepresented (`found == scan_count` wäre
            # bei 0 == 0 sonst fälschlich True). `errors` bleibt leer, `samples.ok` wird
            # dadurch bereits über den bestehenden Leer-Fall zu False.
            pass
        else:
            rng = np.random.default_rng(seed)
            per_scan = max(1, samples // scan_count)
            with _cloud_query(pkg) as query_near:
                for i in range(scan_count):
                    total_raw += int(e.get_header(i).point_count)
                    own_id = i + 1 if ids_claimed else None
                    err, own_err, valid_count = _scan_errors(
                        e, i, per_scan, rng, query_near, own_id
                    )
                    total_valid += valid_count
                    valid_per_scan.append(valid_count)
                    if valid_count == 0:
                        continue
                    errors.append(err)
                    if own_err is not None:
                        own_errors.append(own_err)
                    if np.median(err) <= MAX_ERROR_M:
                        found += 1
    finally:
        e.close()

    # Bilanz zusätzlich gegen die aus dem E57 gezählten gültigen Punkte prüfen — reine
    # Manifest-Rückrechnung (readerPointCount - pointCount) wäre eine Tautologie und
    # entdeckt z. B. still durchgelassene Invalid-Punkte bei gemischten Quellen nicht
    # (Task-4-Concern 6). Nur anwendbar, wenn kein `max_points` die Quelle gekürzt hat.
    balance_ok = invalid_dropped == cloud["invalidDropped"]
    if total_raw == read:
        balance_ok = balance_ok and written == total_valid
    point_balance = {
        "read": read,
        "written": written,
        "invalidDropped": invalid_dropped,
        "ok": balance_ok,
    }

    err_all = np.concatenate(errors) if errors else np.array([], dtype=np.float64)
    all_finite = bool(len(err_all)) and bool(np.all(np.isfinite(err_all)))
    samples_block: dict[str, Any] = {
        "n": len(err_all),
        "maxErrorM": float(err_all.max()) if all_finite else None,
        "p99ErrorM": float(np.percentile(err_all, 99)) if all_finite else None,
        "ok": bool(all_finite and err_all.max() <= MAX_ERROR_M),
    }
    # `scan_count > 0` verhindert eine Vacuous-Truth bei einem E57 ohne Scans (0 == 0).
    scans_represented = {
        "expected": scan_count,
        "found": found,
        "ok": found == scan_count and scan_count > 0,
    }

    point_source = _point_source_block(
        ids_claimed, id_counts, valid_per_scan, own_errors, complete=total_raw == read
    )

    # Quelle hat RGB, aber die COPC ist bei rgbMax <= 255 unskaliertes 8-bit-RGB
    # in 16-bit-Feldern — genau der Fehler, der den Web-Viewer nahezu schwarz rendern ließ
    # (Task-9-Befund). Ohne RGB in der Quelle (want_rgb False) bleibt der Check trivial wahr.
    attrs["ok"] = not (want_rgb and attrs["rgbMax"] is not None and attrs["rgbMax"] <= 255)

    report: dict[str, Any] = {
        "pointBalance": point_balance,
        "samples": samples_block,
        "scansRepresented": scans_represented,
        "attributes": attrs,
        "pointSource": point_source,
        "conversion": manifest.get("conversion", {}),
        "copcSizeBytes": (pkg / CLOUD_NAME).stat().st_size,
    }
    report.update(precision_report)
    report["ok"] = all(
        report[k]["ok"]
        for k in ("pointBalance", "samples", "scansRepresented", "attributes", "pointSource")
    )
    # allow_nan=False als Netz: nicht-endliche Werte sollen laut brechen statt als
    # ungültiges JSON (Infinity/NaN) in conversion-report.json bzw. beim Web-Viewer zu landen.
    (pkg / "conversion-report.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    return report
