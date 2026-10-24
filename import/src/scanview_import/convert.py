"""E57 → COPC + project.json (Schema 2) + inventory.json.

Drei Stufen:
1. ``pdal pipeline --stream`` E57 → LAZ 1.4 mit **allen** gelesenen Punkten in
   Lesereihenfolge, ungültige nur per ``Omit`` markiert (speicherbegrenzt).
2. ``tag_point_sources`` (laspy, chunkweise): ``PointSourceId = Scanindex + 1`` aus den
   Rohpunktzahlen des Inventars, ungültige Punkte verwerfen, ``Omit`` entfernen.
3. Nach Modus: ``direct`` = PDAL readers.las → writers.copc (hält die ganze
   Wolke im Speicher, gemessen: 200 Mio. Punkte → 9,5 GiB); ``staged`` = untwine LAZ → COPC
   (für große Quellen, Pilotkirche ~0,6 Mrd., Großprojekt ~1,9 Mrd.).
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

import laspy
import numpy as np
import pye57
from pye57 import libe57

from .geometry import apply_pose, compute_origin
from .inventory import inventory_to_json, pruefe_e57_genauigkeit, read_inventory
from .lage import Lage
from .manifest_io import STATIONS_LAYER_ID, cloud_of, set_stations, to_v2, write_manifest
from .model import Inventory, Pose, Vec3
from .pdal_runner import (
    INVALID_DIM,
    SCALE,
    RunStats,
    build_copc_pipeline,
    build_extract_pipeline,
    combine_stats,
    run_pipeline,
    run_untwine,
)

Mode = Literal["direct", "staged"]
# Bis hierher `direct` (Probe: 200 Mio. Punkte in 8,6 min / 9,5 GiB), darüber `staged`.
DIRECT_MAX_POINTS = 300_000_000
CLOUD_NAME = "cloud.copc.laz"


def rgb_needs_scale(inv: Inventory) -> bool:
    """Muss ``convert()`` 8-bit-RGB selbst auf 16 bit skalieren?

    Korrektur: PDALs ``readers.e57`` skaliert 8-bit-RGB bereits selbst auf 16 bit,
    wenn die Data3D-``colorLimits``-Struktur vorhanden ist — empirisch verifiziert (reines
    ``readers.e57`` → ``writers.text`` ohne jede ``filters.assign``-Stufe liefert an echten
    Pilotkirchen-Daten schon exakt x257-skalierte Werte, in ``--stream`` wie im Normalmodus; eine
    zusätzliche ``Green = Green * 2``-Kontrollstufe blieb dabei wirkungslos, weil PDAL eine
    Zuweisung, die den 16-bit-Zielbereich überschreitet, still verwirft statt zu clampen —
    genau das hatte die ursprüngliche ``Red = Red * 257``-Stufe für die Pilotkirche zu einem
    Zufalls-Treffer gemacht, keiner verlässlichen Skalierung). Ohne ``colorLimits`` (Pilothalle) tut
    PDAL das nicht — dort muss ``convert()`` selbst skalieren.

    True nur, wenn ALLE Scans mit ``colorRed`` diesen als 8-bit deklarieren (``color_max``)
    UND KEINE ``colorLimits``-Struktur haben — bei gemischten Quellen wird nicht geraten
    (analog ``has_invalid``; siehe Concern im Task-7-Report zu dieser bewussten Lücke).
    """
    color_scans = [s for s in inv.scans if "colorRed" in s.fields]
    return bool(color_scans) and all(
        s.color_max is not None and s.color_max <= 255 and s.color_limits_max is None
        for s in color_scans
    )


def choose_mode(total_points: int, mode: str) -> Mode:
    if mode == "direct":
        return "direct"
    if mode == "staged":
        return "staged"
    if mode == "auto":
        return "direct" if total_points <= DIRECT_MAX_POINTS else "staged"
    raise ValueError(f"Unbekannter Modus {mode!r} (erlaubt: auto, direct, staged)")


def _sha256(path: Path, chunk: int = 1 << 24) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def world_bounds_of(
    local_min: Vec3 | list[float], local_max: Vec3 | list[float], pose: Pose
) -> tuple[list[float], list[float]]:
    """Welt-Bounds einer lokalen Box: alle 8 Ecken mit der Pose transformieren.

    ``cartesianBounds`` einer E57 stehen laut Standard im lokalen KS des Scans.
    """
    lo, hi = np.asarray(local_min, dtype=np.float64), np.asarray(local_max, dtype=np.float64)
    corners = np.array(
        [
            [(lo, hi)[i][0], (lo, hi)[j][1], (lo, hi)[k][2]]
            for i in (0, 1)
            for j in (0, 1)
            for k in (0, 1)
        ]
    )
    w = apply_pose(corners, pose)
    return [float(v) for v in w.min(0)], [float(v) for v in w.max(0)]


# 1 mm Toleranz je Komponente zwischen altem und neuem cloud.boundsWorld (analog C2).
BOUNDS_TOL_M = 0.001


def _bounds_match(old_bounds: Any, new_bounds: Any, tol: float = BOUNDS_TOL_M) -> bool:
    """True nur, wenn beide Bounds vorhanden, gleich strukturiert (2 Ecken x 3 Komponenten) sind
    und sich jede Komponente um höchstens ``tol`` unterscheidet. Fehlt/verformt eine Seite,
    wird nicht geraten -> False."""
    if not isinstance(old_bounds, list) or not isinstance(new_bounds, list):
        return False
    try:
        for old_corner, new_corner in zip(old_bounds, new_bounds, strict=True):
            for a, b in zip(old_corner, new_corner, strict=True):
                if abs(float(a) - float(b)) > tol:
                    return False
    except (TypeError, ValueError):
        return False
    return True


def _source_sha256(m: dict[str, Any]) -> str | None:
    """SHA-256 der Quelle, gleich ob Schema 1 (``m["source"]``, Geschwisterfeld von ``cloud``)
    oder Schema 2 (in der Punktwolken-Ebene, ``cloud_of(m)["source"]`` — dort landet ``source``
    erst durch ``to_v2``)."""
    if "layers" in m:
        try:
            source = cloud_of(m).get("source")
        except KeyError:
            return None
    else:
        source = m.get("source")
    return source.get("sha256") if isinstance(source, dict) else None


def _panoramas_of(m: dict[str, Any]) -> dict[str, Any] | None:
    """Standpunkt-Block mit Inhalt (``count > 0``), gleich ob Schema 1 (``m["stations"]``)
    oder Schema 2 (Ebene ``standpunkte``); sonst ``None``."""
    if "layers" in m:
        for layer in m["layers"]:
            if isinstance(layer, dict) and layer.get("id") == STATIONS_LAYER_ID:
                return layer
        return None
    st = m.get("stations")
    return st if isinstance(st, dict) and st.get("count", 0) > 0 else None


def carry_over_blocks(old: dict[str, Any] | None, new: dict[str, Any], pkg: Path) -> dict[str, Any]:
    """Ein erneutes ``convert`` behält die Blöcke von ``panos``/``overview`` (Standpunkte,
    ``topview``), sofern die Quelle dieselbe ist (gleicher SHA-256) **und** die Wolke denselben
    Ausschnitt ergeben hat (``cloud.boundsWorld`` alt = neu, Toleranz 1 mm je Komponente
    — ein zweiter Lauf derselben Quelle mit anderem ``--max-points``/``--mode`` kann einen
    anderen Ausschnitt liefern, dessen Georeferenz nicht mehr zum alten ``topview`` passt). Sonst
    wären Standpunkte und Draufsicht veraltet oder falsch georeferenziert — dann gelten die
    frischen Werte (keine Standpunkte, kein ``topview``), und ``panos``/``overview`` müssen neu
    laufen.

    ``old`` kann Schema 1 oder 2 sein (ein Altpaket vor diesem Task, oder ein schon migriertes/
    neu geschriebenes); ``new`` ist immer Schema 2 (``convert()`` ruft ``to_v2`` vorher auf)."""
    if not old or _source_sha256(old) != _source_sha256(new):
        return new
    try:
        old_bounds = cloud_of(old).get("boundsWorld")
    except KeyError:
        old_bounds = None
    new_bounds = cloud_of(new).get("boundsWorld")
    if not _bounds_match(old_bounds, new_bounds):
        return new
    out = dict(new)
    old_panorama = _panoramas_of(old)
    if old_panorama and (pkg / str(old_panorama.get("url", ""))).is_file():
        set_stations(out, str(old_panorama["url"]), int(old_panorama["count"]))
    tv = old.get("topview")
    if isinstance(tv, dict) and (pkg / str(tv.get("url", ""))).is_file():
        out["topview"] = tv
    return out


def _read_json_or_none(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# Punkte je Scan für die Vorab-Schätzung, wenn cartesianBounds fehlen.
SAMPLE_PER_SCAN = 10_000


def _sample_world_bounds(
    imf: Any, node: Any, pose: Pose, n: int
) -> tuple[list[float], list[float]] | None:
    """Welt-Bounds der ersten ``n`` gültigen Punkte eines Scans (billig, auch bei 1 Mrd.)."""
    points = node["points"]
    proto = libe57.StructureNode(points.prototype())
    names = {proto.get(j).elementName() for j in range(proto.childCount())}
    if not {"cartesianX", "cartesianY", "cartesianZ"} <= names:
        return None
    n = min(n, int(points.childCount()))
    if n == 0:
        return None
    bufs = {k: np.zeros(n, dtype=np.float64) for k in ("cartesianX", "cartesianY", "cartesianZ")}
    if "cartesianInvalidState" in names:
        bufs["cartesianInvalidState"] = np.zeros(n, dtype=np.int64)
    sdbufs = libe57.VectorSourceDestBuffer()
    for k, v in bufs.items():
        # stride explizit (itemsize) — pye57 0.4.19 korrumpiert int64-Puffer sonst still.
        sdbufs.append(libe57.SourceDestBuffer(imf, k, v, n, True, True, v.itemsize))
    reader = points.reader(sdbufs)
    try:
        got = int(reader.read())
    finally:
        reader.close()
    xyz = np.column_stack([bufs[k][:got] for k in ("cartesianX", "cartesianY", "cartesianZ")])
    if "cartesianInvalidState" in bufs:
        xyz = xyz[bufs["cartesianInvalidState"][:got] == 0]
    if len(xyz) == 0:
        return None
    w = apply_pose(xyz, pose)
    return [float(v) for v in w.min(0)], [float(v) for v in w.max(0)]


def world_bounds(
    e57: Path, inv: Inventory, sample: int = SAMPLE_PER_SCAN, *, float32_zulassen: bool = False
) -> tuple[list[float], list[float]]:
    """Vorab-Schätzung der Welt-Bounds (nur für den LAS-Offset vor der Konvertierung).

    Je Scan: ``cartesianBounds`` (lokales KS, mit Pose transformiert); fehlen sie, die
    ersten ``sample`` gültigen Punkte; fehlen auch Kartesisch-Felder, Pose-Translation ± 1 m.
    Die Schätzung muss nur so gut sein, dass int32 bei 1 mm (± 2147 km) reicht —
    originWorld wird danach aus der fertigen Wolke bestimmt.
    """
    pruefe_e57_genauigkeit(inv, float32_zulassen)
    handle = pye57.E57(str(e57))  # Handle halten, sonst schließt GC die Datei vorzeitig
    try:
        imf = handle.image_file
        data3d = imf.root()["data3D"]
        mins, maxs = [], []
        keys = ("xMinimum", "yMinimum", "zMinimum", "xMaximum", "yMaximum", "zMaximum")
        for s in inv.scans:
            node = data3d[s.index]
            box: tuple[list[float], list[float]] | None = None
            if node.isDefined("cartesianBounds"):
                b = node["cartesianBounds"]
                if all(b.isDefined(k) for k in keys):
                    v = [float(b[k].value()) for k in keys]
                    box = world_bounds_of(v[:3], v[3:], s.pose)
            if box is None:
                box = _sample_world_bounds(imf, node, s.pose, sample)
            if box is None:
                t = s.pose.translation
                box = ([c - 1.0 for c in t], [c + 1.0 for c in t])
            mins.append(box[0])
            maxs.append(box[1])
    finally:
        handle.close()
    return [float(v) for v in np.min(mins, axis=0)], [float(v) for v in np.max(maxs, axis=0)]


def _crs_block(crs: str | None, inv: Inventory) -> dict[str, Any]:
    # CRS wird nie geraten: CLI vor Quelle, sonst null.
    if crs is not None:
        return {"wkt": crs, "verticalDatum": None, "unit": "m", "declaredBy": "cli"}
    return {"wkt": inv.crs_wkt, "verticalDatum": None, "unit": "m", "declaredBy": "file"}


# Punkte je Chunk beim Taggen (Stufe 2): ~5 Mio. x 38 Byte ≈ 190 MB je Puffer.
TAG_CHUNK = 5_000_000
# PointSourceId ist uint16, 0 bleibt „unbekannt“.
MAX_SCANS_WITH_ID = 65_535


def raw_counts_per_scan(inv: Inventory, max_points: int | None) -> list[int]:
    """Rohpunkte (inkl. ungültiger), die Stufe 1 je Scan liest: die Inventarzahlen in
    data3D-Reihenfolge, bei ``max_points`` nach den ersten N insgesamt abgeschnitten
    (readers.e57 ``count`` zählt Rohpunkte)."""
    left = max_points
    out: list[int] = []
    for s in inv.scans:
        c = s.point_count if left is None else min(s.point_count, left)
        out.append(c)
        if left is not None:
            left -= c
    return out


def tag_point_sources(
    src: Path, dst: Path, raw_counts: list[int], drop_invalid: bool, chunk: int = TAG_CHUNK
) -> list[int]:
    """Stufe 2: schreibt ``dst`` mit ``PointSourceId = Scanindex + 1`` und gibt die
    geschriebenen Punkte je Scan zurück.

    ``src`` enthält alle Rohpunkte in Lesereihenfolge (Stufe 1); Scan i belegt die Punkte
    ``sum(raw_counts[:i]) … sum(raw_counts[:i+1]) - 1``. Weicht die Punktzahl ab, sind die
    Grenzen unbekannt → Abbruch (nie raten). ``drop_invalid`` verwirft Punkte mit
    ``Omit != 0``; ``Omit`` landet nie im Ergebnis. X/Y/Z und alle übrigen Felder werden als
    Rohwerte kopiert (keine Neu-Quantisierung); Skalierung, Offset und VLRs (SRS) bleiben.
    """
    if len(raw_counts) > MAX_SCANS_WITH_ID:
        raise RuntimeError(
            f"Scan-Zuordnung: {len(raw_counts)} Scans passen nicht in PointSourceId (uint16)"
        )
    bounds = np.cumsum(np.asarray(raw_counts, dtype=np.int64))
    written = np.zeros(len(raw_counts), dtype=np.int64)
    with laspy.open(src) as reader:
        total = int(reader.header.point_count)
        if total != int(bounds[-1] if len(bounds) else 0):
            raise RuntimeError(
                f"Scan-Zuordnung: Stufe 1 lieferte {total} Punkte, das Inventar erwartet "
                f"{int(bounds[-1]) if len(bounds) else 0} — Scangrenzen unbekannt, Abbruch"
            )
        header = reader.header.copy()
        extra = list(header.point_format.extra_dimension_names)
        if extra:
            header.remove_extra_dims(extra)
        header.point_count = 0
        backend = laspy.LazBackend.detect_available()[0]
        with laspy.open(dst, mode="w", header=header, laz_backend=backend) as writer:
            start = 0
            for pts in reader.chunk_iterator(chunk):
                n = len(pts)
                ids = np.searchsorted(bounds, np.arange(start, start + n), side="right") + 1
                start += n
                keep = np.ones(n, dtype=bool)
                if drop_invalid:
                    keep = np.asarray(pts[INVALID_DIM]) == 0
                out = laspy.ScaleAwarePointRecord.zeros(int(keep.sum()), header=header)
                for name in out.array.dtype.names or ():
                    out.array[name] = pts.array[name][keep]
                kept_ids = ids[keep]
                out.array["point_source_id"] = kept_ids.astype(np.uint16)
                written += np.bincount(kept_ids - 1, minlength=len(raw_counts))[: len(written)]
                writer.write_points(out)
    return [int(v) for v in written]


def _run_stages(
    src: str,
    out_dir: Path,
    mounts: dict[Path, str],
    origin: Vec3,
    raw_counts: list[int],
    has_invalid: bool,
    rgb_is_8bit: bool,
    mode: Mode,
    max_points: int | None,
    ausduennen: float | None = None,
    thinning_result: dict[str, Any] | None = None,
) -> tuple[RunStats, list[int]]:
    """Stufen 1-3 (Modul-Docstring); gibt Laufzeitdaten und geschriebene Punkte je Scan zurück.

    ``RunStats.seconds`` summiert alle Stufen (Stufe 2 läuft auf dem Host, zählt also mit),
    ``peak_rss_mb`` bleibt der größte Container-Wert."""
    # Zwischenstand unter out_dir, damit er im selben Mount liegt; wird immer aufgeräumt.
    stage = Path(tempfile.mkdtemp(prefix="_staging-", dir=out_dir))
    try:
        rel = f"/out/{stage.name}"
        pipe = build_extract_pipeline(
            src, f"{rel}/raw.laz", origin, max_points, has_invalid, rgb_is_8bit
        )
        s1 = run_pipeline(pipe, mounts, stream=True)
        if s1.returncode != 0:
            return s1, []
        t0 = time.monotonic()
        per_scan = tag_point_sources(
            stage / "raw.laz", stage / "points.laz", raw_counts, has_invalid
        )
        (stage / "raw.laz").unlink()  # Platz frei machen, bevor untwine seine Temp-Dateien baut
        s2 = RunStats(time.monotonic() - t0, 0.0, 0, "")
        if ausduennen is not None:
            from .tiled_thinning import thin_tiles

            thin = thin_tiles(stage / "points.laz", stage / "thinned.laz", ausduennen)
            s2 = combine_stats([s2, thin])
            if thin.returncode:
                return combine_stats([s1, s2]), per_scan
            if thinning_result is not None:
                with laspy.open(stage / "thinned.laz") as reader:
                    thinning_result["expectedPointCount"] = int(reader.header.point_count)
                    thinning_result["expectedBoundsWorld"] = [
                        reader.header.mins.tolist(),
                        reader.header.maxs.tolist(),
                    ]
            (stage / "thinned.laz").replace(stage / "points.laz")
        if mode == "staged":
            (stage / "untwine").mkdir()
            s3 = run_untwine(f"{rel}/points.laz", f"/out/{CLOUD_NAME}", f"{rel}/untwine", mounts)
        else:
            s3 = run_pipeline(
                build_copc_pipeline(f"{rel}/points.laz", f"/out/{CLOUD_NAME}"), mounts
            )
        return combine_stats([s1, s2, s3]), per_scan
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def convert(
    e57: Path,
    out_dir: Path,
    crs: str | None,
    max_points: int | None = None,
    mode: str = "auto",
    *,
    unit: str | None = None,
    up: str = "z",
    columns: str | None = None,
    ausduennen: float | None = None,
    float32_zulassen: bool = False,
    rgb_8bit: bool = False,
    hoehe: tuple[str | None, str | None] = (None, None),
    lage: Lage | None = None,
) -> dict[str, Any]:
    """Schreibt ``cloud.copc.laz``, ``project.json`` (Schema 2) und ``inventory.json`` nach
    ``out_dir`` und gibt das Manifest zurück."""
    from .point_source import detect_format

    if ausduennen is not None and (not math.isfinite(ausduennen) or ausduennen <= 0):
        raise ValueError("Ausdünnung muss ein positiver, endlicher Abstand in Metern sein")
    if detect_format(e57.name)[0] != "e57":
        from .point_convert import convert_points

        return convert_points(
            e57, out_dir, crs, max_points, mode, unit, up, columns, ausduennen, hoehe=hoehe,
            lage_=lage, rgb_8bit=rgb_8bit,
        )
    if rgb_8bit:
        raise ValueError("--rgb-8bit gilt nur für LAS/LAZ; E57 deklariert seine Farbtiefe selbst")
    if lage is not None:
        raise ValueError("Lageanpassung für E57 ist noch nicht eingebaut")
    if hoehe[0] is not None and hoehe[0] != hoehe[1]:
        raise ValueError(
            "Höhenumrechnung für E57 ist noch nicht eingebaut — nur deklarieren "
            "(--ziel-hoehensystem gleich --quell-hoehensystem)"
        )
    if unit is not None or up != "z" or columns is not None:
        raise ValueError("--unit/--up/--columns sind nur für scanlose Punktformate zulässig")
    e57 = e57.resolve()
    inv = read_inventory(e57)
    precision_report = pruefe_e57_genauigkeit(inv, float32_zulassen)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir = out_dir.resolve()
    (out_dir / "inventory.json").write_text(json.dumps(inventory_to_json(inv), indent=2))

    total = sum(s.point_count for s in inv.scans)
    chosen = choose_mode(total, mode)
    lo, hi = world_bounds(e57, inv, SAMPLE_PER_SCAN, float32_zulassen=float32_zulassen)
    offset = compute_origin(lo, hi)  # nur LAS-Offset; originWorld folgt aus der Wolke
    has_invalid = all("cartesianInvalidState" in s.fields for s in inv.scans)
    # 8-bit-RGB (E57 0..255) wird im Import auf 16 bit skaliert, sonst rendert
    # der Web-Viewer nahezu schwarz (früherer Befund). Siehe rgb_needs_scale()-Docstring für
    # die Korrektur (colorLimits-Fall gehört PDAL selbst, nicht uns).
    rgb_is_8bit = rgb_needs_scale(inv)
    if e57.parent == out_dir:  # ein Host-Pfad, ein Mount
        mounts, src = {out_dir: "/out"}, f"/out/{e57.name}"
    else:
        mounts, src = {e57.parent: "/src:ro", out_dir: "/out"}, f"/src/{e57.name}"
    raw_counts = raw_counts_per_scan(inv, max_points)
    thinning_result: dict[str, Any] = {}
    stage_options: dict[str, Any] = {}
    if ausduennen is not None:
        stage_options = {"ausduennen": ausduennen, "thinning_result": thinning_result}
    stats, per_scan = _run_stages(
        src,
        out_dir,
        mounts,
        offset,
        raw_counts,
        has_invalid,
        rgb_is_8bit,
        chosen,
        max_points,
        **stage_options,
    )
    if stats.returncode != 0:
        raise RuntimeError(f"Konvertierung ({chosen}) fehlgeschlagen: {stats.stderr_tail}")

    with laspy.open(out_dir / CLOUD_NAME) as f:
        hdr = f.header
        point_count = int(hdr.point_count)
        bmin = [float(v) for v in hdr.mins]
        bmax = [float(v) for v in hdr.maxs]
        dims = set(hdr.point_format.dimension_names)
    # originWorld = gerundeter Mittelpunkt der tatsächlichen Bounding-Box (Binding Constraint).
    origin = compute_origin(bmin, bmax)
    read_total = total if max_points is None else min(total, max_points)
    # LAS-Format 7 hat RGB/Intensity immer — maßgeblich ist, ob die Quelle sie liefert.
    src_fields = {f for s in inv.scans for f in s.fields}
    attrs = [
        name
        for name, dim, field in (
            ("rgb", "red", "colorRed"),
            ("intensity", "intensity", "intensity"),
        )
        if dim in dims and field in src_fields
    ]
    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "projectId": out_dir.name,
        "revision": 1,
        "source": {"filename": e57.name, "sha256": _sha256(e57), "sizeBytes": e57.stat().st_size},
        "crs": _crs_block(crs, inv),
        "originWorld": list(origin),
        "cloud": {
            "url": CLOUD_NAME,
            "pointCount": point_count,
            "boundsWorld": [bmin, bmax],
            "attributes": attrs,
            "scale": SCALE,
            "readerPointCount": read_total,
            "invalidDropped": read_total
            - (sum(per_scan) if ausduennen is not None else point_count),
            # Jeder Punkt trägt PointSourceId = Scanindex + 1.
            "pointSourceIsScan": bool(inv.scans)
            and (ausduennen is not None or sum(per_scan) == point_count),
        },
        "stations": {"url": "stations.json", "count": 0},
        "initialView": {
            "target": [(a + b) / 2 for a, b in zip(bmin, bmax, strict=True)],
            "distance": float(np.linalg.norm(np.subtract(bmax, bmin))),
        },
        "conversion": {"seconds": stats.seconds, "peakRssMb": stats.peak_rss_mb, "mode": chosen},
    }
    manifest["conversion"].update(precision_report)
    if origin != offset:
        # Vorab-Schätzung wich ab: tatsächlich genutzten LAS-Offset festhalten. Noch Schema 1
        # (vor to_v2) — cloud_of(manifest) ist hier dasselbe wie manifest["cloud"].
        cloud_of(manifest)["offset"] = list(offset)
    manifest = to_v2(manifest)
    manifest = carry_over_blocks(_read_json_or_none(out_dir / "project.json"), manifest, out_dir)
    if ausduennen is not None:
        cloud = cloud_of(manifest)
        cloud["thinningDropped"] = sum(per_scan) - point_count
        source = {
            **cloud["source"],
            "format": "e57",
            "crs": crs,
            "unit": "m",
            "unitDeclaredBy": "file",
            "up": "z",
            "maxPoints": max_points,
            "ausduennen": ausduennen,
        }
        source["expectedPointCount"] = thinning_result["expectedPointCount"]
        cloud["expectedBoundsWorld"] = thinning_result["expectedBoundsWorld"]
        cloud["sourceOptions"] = source
        (out_dir / "conversion-report.json").write_text(
            json.dumps(
                {
                    "source": source,
                    "points": {
                        "raw": read_total,
                        "thinned": point_count,
                        "written": point_count,
                        "invalidDropped": read_total - sum(per_scan),
                    },
                    "thinning": {"cellM": ausduennen, "dropped": sum(per_scan) - point_count},
                    "conversion": manifest["conversion"],
                    "ok": False,
                },
                indent=2,
                allow_nan=False,
            )
        )
    if hoehe[0] is not None:
        manifest["frame"]["crs"]["verticalDatum"] = hoehe[0]
    write_manifest(out_dir, manifest)
    return manifest
