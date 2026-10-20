import json
import math
import os

import laspy
import numpy as np
import pytest
from e57_fixture import write_fixture
from scipy.spatial import cKDTree

import scanview_import.convert as convert_mod
from scanview_import.convert import (
    DIRECT_MAX_POINTS,
    carry_over_blocks,
    choose_mode,
    convert,
    raw_counts_per_scan,
    rgb_needs_scale,
    tag_point_sources,
    world_bounds_of,
)
from scanview_import.geometry import compute_origin
from scanview_import.manifest_io import (
    cloud_of,
    origin_of,
    read_manifest,
    set_stations,
    write_manifest,
)
from scanview_import.model import Inventory, Pose, ScanInfo

_IDENTITY_POSE = Pose((0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0))


def _scan(
    fields: list[str], color_max: int | None, color_limits_max: int | None, n: int = 10
) -> ScanInfo:
    return ScanInfo(0, "{g}", "s", n, fields, _IDENTITY_POSE, color_max, color_limits_max)


def _inv(*scans: ScanInfo) -> Inventory:
    inv = Inventory(filename="x.e57", size_bytes=1, crs_wkt=None)
    inv.scans.extend(scans)
    return inv


# Toleranz 1,5 mm = 1 mm Vorgabe + halber Quantisierungsschritt (0,5 mm).
TOL = 0.0015


def _max_nn_dist(a: np.ndarray, b: np.ndarray, chunk: int = 256) -> float:
    """Größter Abstand eines Punkts aus a zu seinem nächsten Nachbarn in b (Brute Force)."""
    worst = 0.0
    for i in range(0, len(a), chunk):
        d = np.sqrt(((a[i : i + chunk, None, :] - b[None, :, :]) ** 2).sum(-1)).min(1)
        worst = max(worst, float(d.max()))
    return worst


def test_choose_mode():
    assert choose_mode(DIRECT_MAX_POINTS, "auto") == "direct"
    assert choose_mode(DIRECT_MAX_POINTS + 1, "auto") == "staged"
    assert choose_mode(10, "staged") == "staged"
    assert choose_mode(10**10, "direct") == "direct"
    with pytest.raises(ValueError):
        choose_mode(10, "fast")


def test_rgb_needs_scale_true_when_8bit_and_no_color_limits():
    # Pilothallen-Fall: colorRed 8-bit deklariert, keine colorLimits-Struktur — PDAL skaliert nicht
    # selbst, convert() muss es tun.
    inv = _inv(_scan(["colorRed"], color_max=255, color_limits_max=None))
    assert rgb_needs_scale(inv) is True


def test_rgb_needs_scale_false_when_color_limits_present():
    # Pilotkirchen-Fall: colorLimits vorhanden — PDALs readers.e57
    # skaliert 8-bit-RGB dort bereits selbst (empirisch verifiziert, Task-7-Fix-Report Runde
    # 1); eine eigene filters.assign-Stufe wäre bestenfalls redundant und verlässt sich sonst
    # auf PDALs unbelegtes Verwerfen von Overflow-Zuweisungen.
    inv = _inv(_scan(["colorRed"], color_max=255, color_limits_max=255))
    assert rgb_needs_scale(inv) is False


def test_rgb_needs_scale_false_without_rgb():
    inv = _inv(_scan(["cartesianX"], color_max=None, color_limits_max=None))
    assert rgb_needs_scale(inv) is False


def test_rgb_needs_scale_false_when_already_16bit():
    inv = _inv(_scan(["colorRed"], color_max=65535, color_limits_max=None))
    assert rgb_needs_scale(inv) is False


def test_rgb_needs_scale_false_on_mixed_scans():
    # Kein Raten bei gemischten Quellen (analog has_invalid) — sobald ein Scan mit colorRed
    # colorLimits hat oder schon 16-bit deklariert, wird nicht mehr global skaliert.
    inv = _inv(
        _scan(["colorRed"], color_max=255, color_limits_max=None),
        _scan(["colorRed"], color_max=255, color_limits_max=255),
    )
    assert rgb_needs_scale(inv) is False


def test_world_bounds_of_rotated_local_box():
    # Lokale Box [-5,5]x[-1,1]x[0,2], 90° um Z gedreht, verschoben nach (1010, 2000, 10):
    # lokal X → Welt Y, lokal Y → Welt -X.
    h = math.radians(90) / 2
    pose = Pose((1010.0, 2000.0, 10.0), (math.cos(h), 0.0, 0.0, math.sin(h)))
    lo, hi = world_bounds_of((-5.0, -1.0, 0.0), (5.0, 1.0, 2.0), pose)
    np.testing.assert_allclose(lo, [1009.0, 1995.0, 10.0], atol=1e-9)
    np.testing.assert_allclose(hi, [1011.0, 2005.0, 12.0], atol=1e-9)


@pytest.mark.docker
@pytest.mark.parametrize("mode", ["direct", "staged"])
def test_convert_two_scans_world_coords_once(two_scan_e57, tmp_path, mode):
    out = tmp_path / "pkg"
    manifest = convert(two_scan_e57.path, out, crs=None, mode=mode)
    las = laspy.read(out / "cloud.copc.laz")
    got = np.column_stack([las.x, las.y, las.z])
    exp = two_scan_e57.world_points_valid
    assert len(got) == len(exp) == 2000 - 14
    # Jede erwartete Weltkoordinate hat einen Partner ≤ TOL und umgekehrt (Reihenfolge egal).
    # Kein lexsort-Paarvergleich: Quantisierung auf 1 mm vertauscht Punkte mit fast
    # gleichem X, der Elementvergleich paart dann fremde Punkte (gemessen: 9,2 m).
    assert _max_nn_dist(exp, got) <= TOL
    assert _max_nn_dist(got, exp) <= TOL
    assert "Omit" not in las.point_format.dimension_names
    assert tuple(las.header.offsets) == (1005.0, 2000.0, 10.0)
    assert tuple(las.header.scales) == (0.001, 0.001, 0.001)
    # Container läuft als aufrufender Nutzer, nicht root
    assert (out / "cloud.copc.laz").stat().st_uid == os.getuid()
    assert [p.name for p in out.iterdir() if p.name.startswith("_")] == []
    with laspy.CopcReader.open(out / "cloud.copc.laz") as r:
        assert r.header.point_count == 2000 - 14
    m = json.loads((out / "project.json").read_text())
    assert m["schemaVersion"] == 2
    assert m["frame"]["crs"] == {"wkt": None, "verticalDatum": None, "declaredBy": "file"}
    assert m["frame"]["unit"] == "m"
    cloud = cloud_of(m)
    # originWorld = gerundeter Mittelpunkt der tatsächlichen Bounding-Box der Wolke
    assert origin_of(m) == list(compute_origin(exp.min(0), exp.max(0)))
    assert "offset" not in cloud  # LAS-Offset == originWorld, keine Abweichung
    assert cloud["url"] == "cloud.copc.laz"
    assert cloud["pointCount"] == 2000 - 14
    assert cloud["readerPointCount"] == 2000
    assert cloud["invalidDropped"] == 14
    assert cloud["scale"] == 0.001
    assert sorted(cloud["attributes"]) == ["intensity", "rgb"]
    lo, hi = cloud["boundsWorld"]
    np.testing.assert_allclose(lo, exp.min(0), atol=TOL)
    np.testing.assert_allclose(hi, exp.max(0), atol=TOL)
    assert [lay["id"] for lay in m["layers"]] == ["punkte"]  # keine Standpunkte, keine Ebene
    assert set(m["initialView"]) == {"target", "distance"}
    assert m["conversion"]["mode"] == mode
    assert m["conversion"]["seconds"] > 0
    assert m["conversion"]["peakRssMb"] > 0
    assert m == manifest
    inv = json.loads((out / "inventory.json").read_text())
    assert len(inv["scans"]) == 2


@pytest.mark.docker
@pytest.mark.parametrize("mode", ["direct", "staged"])
def test_convert_max_points_limits_reader(two_scan_e57, tmp_path, mode):
    # Erste 500 Punkte = Anfang von Scan 0, davon sind die ersten 7 ungültig.
    m = convert(two_scan_e57.path, tmp_path / "p", crs=None, max_points=500, mode=mode)
    cloud = cloud_of(m)
    assert cloud["readerPointCount"] == 500
    assert cloud["pointCount"] == 493
    assert cloud["invalidDropped"] == 7


def _assert_rgb_scaled_exactly_257x(pkg, fixture) -> None:
    """Prüft die x257-Zuordnung PRO PUNKT (per Weltposition gepaart, nicht nur
    min/max/Modulo über die ganze Wolke) — ein reiner min/max/mod-Check würde auch bestehen,
    wenn die Farben (z. B. durch einen Reihenfolge-Fehler) an die falschen Punkte gerieten.

    Fixture-Muster (e57_fixture.write_fixture): pro Scan rgb = arange(n) % 256, n=1000;
    two_scan_e57/two_scan_e57_color_limits (conftest.py) markieren je Scan die ersten 7 Punkte
    als ungültig (inv[:7]=2) — world_points_valid enthält nur die gültigen, in derselben
    Reihenfolge (local[ok]) und Scan-Reihenfolge wie hier nachgebildet.
    """
    las = laspy.read(pkg / "cloud.copc.laz")
    got_xyz = np.column_stack([las.x, las.y, las.z])

    per_scan_raw = (np.arange(1000) % 256)[7:]
    exp_red_raw = np.concatenate([per_scan_raw, per_scan_raw])
    exp_xyz = fixture.world_points_valid
    assert len(exp_red_raw) == len(exp_xyz) == 1986

    tree = cKDTree(got_xyz)
    dist, idx = tree.query(exp_xyz)
    assert float(dist.max()) <= TOL
    for channel in (las.red, las.green, las.blue):
        got = np.asarray(channel, dtype=np.int64)[idx]
        assert np.array_equal(got, exp_red_raw * 257)


@pytest.mark.docker
@pytest.mark.parametrize("mode", ["direct", "staged"])
def test_convert_scales_8bit_rgb_to_16bit(two_scan_e57, tmp_path, mode):
    # Fixture-RGB ist 0..255 (8 bit, siehe e57_fixture.py), KEINE colorLimits-
    # Struktur (wie Pilothalle) — muss von convert() selbst x257 auf den vollen 16-bit-Bereich
    # skaliert werden (LAS-/COPC-Konvention), sonst rendert Giro3D fast schwarz.
    pkg = tmp_path / "p"
    convert(two_scan_e57.path, pkg, crs=None, mode=mode)
    _assert_rgb_scaled_exactly_257x(pkg, two_scan_e57)


@pytest.mark.docker
@pytest.mark.parametrize("mode", ["direct", "staged"])
def test_convert_leaves_color_limits_rgb_to_pdal(two_scan_e57_color_limits, tmp_path, mode):
    """Mit einer colorLimits-Struktur (wie an der Pilotkirche real
    vorhanden) skaliert PDALs readers.e57 8-bit-RGB bereits selbst — convert() darf hier KEINE
    eigene filters.assign-Stufe mehr einfügen (die alte Fassung tat das trotzdem; korrekt nur
    durch PDALs unbelegtes Verwerfen überlaufender Zuweisungen, siehe rgb_needs_scale()-
    Docstring). Ergebnis muss trotzdem exakt x257 sein — von PDAL selbst geleistet."""
    pkg = tmp_path / "p"
    convert(two_scan_e57_color_limits.path, pkg, crs=None, mode=mode)
    _assert_rgb_scaled_exactly_257x(pkg, two_scan_e57_color_limits)


@pytest.mark.docker
def test_convert_cli_crs_is_recorded(two_scan_e57, tmp_path):
    m = convert(two_scan_e57.path, tmp_path / "p", crs="EPSG:25833")
    crs = m["frame"]["crs"]
    assert crs["wkt"] == "EPSG:25833" and crs["declaredBy"] == "cli"
    assert m["conversion"]["mode"] == "direct"  # auto bei 2000 Punkten


GEO = np.array([5_400_123.4, 5_650_987.6, 312.2])  # z. B. UTM-Rechts-/Hochwert


def _geo_fixture(path, local):
    # Georeferenzierte Einzelscan-Datei ohne cartesianBounds: Identitätspose bei (0,0,0),
    # Punkte liegen direkt in Weltkoordinaten. Pose ± 1 m ergäbe Offset (0,0,0) — dann
    # passt die Hochwert-Koordinate nicht mehr in int32 bei 1 mm (PDAL bricht ab).
    return write_fixture(
        path, [(Pose((0.0, 0.0, 0.0), (1, 0, 0, 0)), local, np.zeros(len(local), dtype=np.int64))]
    )


@pytest.mark.docker
@pytest.mark.parametrize("mode", ["direct", "staged"])
def test_convert_georeferenced_without_bounds(tmp_path, mode):
    local = np.random.default_rng(7).uniform(-5, 5, size=(500, 3)) + GEO
    fx = _geo_fixture(tmp_path / "geo.e57", local)
    m = convert(fx.path, tmp_path / "p", crs=None, mode=mode)
    exp = fx.world_points_valid
    assert origin_of(m) == list(compute_origin(exp.min(0), exp.max(0)))
    assert "offset" not in cloud_of(m)  # Stichprobe deckt alle Punkte ab → Offset == Ursprung
    las = laspy.read(tmp_path / "p" / "cloud.copc.laz")
    got = np.column_stack([las.x, las.y, las.z])
    assert len(got) == 500
    assert _max_nn_dist(exp, got) <= TOL and _max_nn_dist(got, exp) <= TOL


@pytest.mark.docker
def test_origin_is_bbox_centre_even_if_pre_estimate_is_off(tmp_path, monkeypatch):
    # Vorab-Schätzung aus nur 10 Punkten, die alle in einer Ecke (+4..+5 m) liegen:
    # Schätzung ≈ GEO + 4,5 m, echter Mittelpunkt ≈ GEO. originWorld muss der echte sein.
    monkeypatch.setattr(convert_mod, "SAMPLE_PER_SCAN", 10)
    rng = np.random.default_rng(11)
    corner = rng.uniform(4, 5, size=(10, 3))
    rest = rng.uniform(-5, 5, size=(490, 3))
    fx = _geo_fixture(tmp_path / "geo.e57", np.vstack([corner, rest]) + GEO)
    m = convert(fx.path, tmp_path / "p", crs=None, mode="direct")
    exp = fx.world_points_valid
    lo, hi = cloud_of(m)["boundsWorld"]
    origin = list(compute_origin(exp.min(0), exp.max(0)))
    assert origin_of(m) == origin == list(compute_origin(lo, hi))
    est = list(compute_origin(corner.min(0) + GEO, corner.max(0) + GEO))
    assert est != origin
    assert cloud_of(m)["offset"] == est  # tatsächlich genutzter LAS-Offset
    las = laspy.read(tmp_path / "p" / "cloud.copc.laz")
    assert list(las.header.offsets) == est
    got = np.column_stack([las.x, las.y, las.z])
    assert _max_nn_dist(exp, got) <= TOL and _max_nn_dist(got, exp) <= TOL


_BOUNDS = [[0.0, 0.0, 0.0], [10.0, 10.0, 10.0]]


def _manifests(tmp_path, new_bounds_delta: float = 0.0):
    """``old`` bleibt Schema 1 (ein Paket aus der Zeit vor diesem Task), ``new`` ist Schema 2
    (wie ``convert()`` es nach ``to_v2`` liefert) — ``carry_over_blocks`` muss beide lesen können
    (Schritt 3: ``cloud_of``). ``new_bounds_delta`` verschiebt jede Komponente von ``new``s
    ``boundsWorld`` (1 mm Toleranz je Komponente zwischen altem und neuem
    ``cloud.boundsWorld``)."""
    new_bounds = [[c + new_bounds_delta for c in corner] for corner in _BOUNDS]
    new = {
        "schemaVersion": 2,
        "layers": [
            {
                "id": "punkte",
                "kind": "pointcloud",
                "source": {"sha256": "abc"},
                "boundsWorld": new_bounds,
            },
        ],
    }
    old = {
        "source": {"sha256": "abc"},
        "cloud": {"boundsWorld": [list(corner) for corner in _BOUNDS]},
        "stations": {"url": "stations.json", "count": 45},
        "topview": {"url": "topview.png", "widthPx": 1},
    }
    (tmp_path / "stations.json").write_text("{}")
    (tmp_path / "topview.png").write_bytes(b"x")
    return old, new


def _standpunkte(m):
    return [lay for lay in m["layers"] if lay["id"] == "standpunkte"]


def test_carry_over_keeps_blocks_of_same_source(tmp_path):
    old, new = _manifests(tmp_path)
    out = carry_over_blocks(old, new, tmp_path)
    st = _standpunkte(out)
    assert st and st[0]["count"] == 45
    assert out["topview"]["url"] == "topview.png"


def test_carry_over_keeps_blocks_when_bounds_differ_within_tolerance(tmp_path):
    # 1 mm Toleranz je Komponente — 0,5 mm gilt noch als "gleich".
    old, new = _manifests(tmp_path, new_bounds_delta=0.0005)
    out = carry_over_blocks(old, new, tmp_path)
    st = _standpunkte(out)
    assert st and st[0]["count"] == 45
    assert out["topview"]["url"] == "topview.png"


def test_carry_over_drops_blocks_when_bounds_differ_beyond_tolerance(tmp_path):
    # Gleicher sha256, aber cloud.boundsWorld weicht (z. B. ein zweiter --max-points-
    # oder --mode-Lauf derselben Quelle mit anderem Ausschnitt) -> stations/topview wären für den
    # falschen Ausschnitt georeferenziert, deshalb gelten die frischen Werte.
    old, new = _manifests(tmp_path, new_bounds_delta=0.01)
    out = carry_over_blocks(old, new, tmp_path)
    assert _standpunkte(out) == []
    assert "topview" not in out


def test_carry_over_drops_blocks_when_old_manifest_has_no_bounds(tmp_path):
    # Defensiv: ohne belastbaren alten boundsWorld-Wert wird nicht geraten, ob er passt.
    old, new = _manifests(tmp_path)
    del old["cloud"]["boundsWorld"]
    out = carry_over_blocks(old, new, tmp_path)
    assert _standpunkte(out) == []
    assert "topview" not in out


def test_carry_over_drops_blocks_of_other_source(tmp_path):
    old, new = _manifests(tmp_path)
    new["layers"][0]["source"]["sha256"] = "zzz"
    out = carry_over_blocks(old, new, tmp_path)
    assert _standpunkte(out) == []
    assert "topview" not in out


def test_carry_over_drops_block_whose_file_is_gone(tmp_path):
    old, new = _manifests(tmp_path)
    (tmp_path / "topview.png").unlink()
    out = carry_over_blocks(old, new, tmp_path)
    assert "topview" not in out
    st = _standpunkte(out)
    assert st and st[0]["count"] == 45


def test_carry_over_without_old_manifest(tmp_path):
    _, new = _manifests(tmp_path)
    assert carry_over_blocks(None, new, tmp_path) is new


@pytest.mark.docker
def test_convert_twice_same_source_and_bounds_keeps_injected_blocks(two_scan_e57, tmp_path):
    """Integrationstest: ein zweiter convert() auf derselben Quelle
    liefert dieselben Bounds (deterministisch aus denselben Punkten) -> die von panos/overview
    geschriebenen Blöcke bleiben erhalten."""
    pkg = tmp_path / "p"
    convert(two_scan_e57.path, pkg, crs=None, mode="direct")
    manifest = read_manifest(pkg)
    set_stations(manifest, "stations.json", 2)
    manifest["topview"] = {"url": "topview.png", "widthPx": 42}
    write_manifest(pkg, manifest)
    (pkg / "stations.json").write_text("{}")
    (pkg / "topview.png").write_bytes(b"x")

    convert(two_scan_e57.path, pkg, crs=None, mode="direct")

    out = read_manifest(pkg)
    st = [lay for lay in out["layers"] if lay["id"] == "standpunkte"]
    assert st and st[0]["count"] == 2
    assert out["topview"]["url"] == "topview.png"


# --- PointSourceId = Scanindex + 1 -----------------------------------------------------------


def test_raw_counts_per_scan_follow_inventory_and_truncate_at_max_points():
    inv = _inv(_scan([], None, None, 1000), _scan([], None, None, 0), _scan([], None, None, 700))
    assert raw_counts_per_scan(inv, None) == [1000, 0, 700]
    assert raw_counts_per_scan(inv, 1500) == [1000, 0, 500]
    assert raw_counts_per_scan(inv, 500) == [500, 0, 0]
    assert raw_counts_per_scan(inv, 5000) == [1000, 0, 700]


def _raw_laz(path, n_per_scan: list[int], omit: np.ndarray | None, wkt: bool = False):
    """Stufe-1-Nachbau: LAZ 1.4 / Format 7 in Lesereihenfolge, optional mit ``Omit``."""
    hdr = laspy.LasHeader(point_format=7, version="1.4")
    hdr.scales = [0.001, 0.001, 0.001]
    hdr.offsets = [1000.0, 2000.0, 10.0]
    if omit is not None:
        hdr.add_extra_dim(laspy.ExtraBytesParams(name="Omit", type=np.uint8))
    if wkt:
        hdr.vlrs.append(laspy.vlrs.known.WktCoordinateSystemVlr('LOCAL_CS["test"]'))
        hdr.global_encoding.wkt = True
    n = sum(n_per_scan)
    rec = laspy.ScaleAwarePointRecord.zeros(n, header=hdr)
    rng = np.random.default_rng(5)
    rec.X = rng.integers(-5000, 5000, n)
    rec.Y = rng.integers(-5000, 5000, n)
    rec.Z = rng.integers(-5000, 5000, n)
    rec.intensity = np.arange(n) % 65535
    rec.red = (np.arange(n) * 7) % 65535
    rec.gps_time = np.arange(n) * 0.5
    if omit is not None:
        rec.Omit = omit
    with laspy.open(path, mode="w", header=hdr) as w:
        w.write_points(rec)
    return rec


@pytest.mark.parametrize("chunk", [3, 1_000_000])
def test_tag_point_sources_assigns_scan_ids_by_read_order_and_drops_invalid(tmp_path, chunk):
    counts = [5, 0, 4, 3]
    omit = np.zeros(12, dtype=np.uint8)
    omit[[1, 5, 11]] = 1  # Scan 0: Index 1; Scan 2: Index 5; Scan 3: Index 11
    src = _raw_laz(tmp_path / "raw.laz", counts, omit, wkt=True)
    written = tag_point_sources(
        tmp_path / "raw.laz", tmp_path / "t.laz", counts, drop_invalid=True, chunk=chunk
    )
    assert written == [4, 0, 3, 2]
    out = laspy.read(tmp_path / "t.laz")
    keep = omit == 0
    exp_ids = np.array([1] * 5 + [3] * 4 + [4] * 3)[keep]
    assert np.array_equal(np.asarray(out.point_source_id), exp_ids)
    # Rohwerte bitgleich übernommen (keine Neu-Quantisierung), Attribute mitgenommen.
    for dim in ("X", "Y", "Z", "intensity", "red", "gps_time"):
        assert np.array_equal(np.asarray(out[dim]), np.asarray(src[dim])[keep]), dim
    assert "Omit" not in out.point_format.dimension_names
    assert list(out.header.scales) == [0.001, 0.001, 0.001]
    assert list(out.header.offsets) == [1000.0, 2000.0, 10.0]
    assert out.header.point_count == 9
    # CRS-VLR und WKT-Bit bleiben erhalten (untwine/writers.copc übernehmen die SRS daraus).
    wkt = [v for v in out.header.vlrs if isinstance(v, laspy.vlrs.known.WktCoordinateSystemVlr)]
    assert [v.string for v in wkt] == ['LOCAL_CS["test"]']
    assert out.header.global_encoding.wkt


def test_tag_point_sources_without_omit_keeps_all(tmp_path):
    _raw_laz(tmp_path / "raw.laz", [2, 3], None)
    written = tag_point_sources(
        tmp_path / "raw.laz", tmp_path / "t.laz", [2, 3], drop_invalid=False
    )
    assert written == [2, 3]
    assert np.array_equal(laspy.read(tmp_path / "t.laz").point_source_id, [1, 1, 2, 2, 2])


def test_tag_point_sources_refuses_when_point_count_differs_from_inventory(tmp_path):
    # Nie raten: stimmt die Lesezahl nicht mit dem Inventar überein, sind die Scangrenzen
    # unbekannt → Abbruch statt still falsch zugeordneter Scan-IDs.
    _raw_laz(tmp_path / "raw.laz", [2, 3], None)
    with pytest.raises(RuntimeError, match="Scan-Zuordnung"):
        tag_point_sources(tmp_path / "raw.laz", tmp_path / "t.laz", [2, 4], drop_invalid=False)


def _ids_by_world_point(pkg, expected_xyz: np.ndarray) -> np.ndarray:
    las = laspy.read(pkg / "cloud.copc.laz")
    got = np.column_stack([las.x, las.y, las.z])
    dist, idx = cKDTree(got).query(expected_xyz)
    assert float(dist.max()) <= TOL
    return np.asarray(las.point_source_id)[idx]


@pytest.mark.docker
@pytest.mark.parametrize("mode", ["direct", "staged"])
def test_convert_writes_point_source_id_scan_index_plus_one(two_scan_e57, tmp_path, mode):
    pkg = tmp_path / "p"
    m = convert(two_scan_e57.path, pkg, crs=None, mode=mode)
    # world_points_valid: erst die 993 gültigen Punkte von Scan 0, dann die von Scan 1.
    ids = _ids_by_world_point(pkg, two_scan_e57.world_points_valid)
    assert np.array_equal(ids, np.repeat([1, 2], 993))
    las = laspy.read(pkg / "cloud.copc.laz")
    assert np.array_equal(np.bincount(las.point_source_id), [0, 993, 993])
    cloud = cloud_of(m)
    assert cloud["pointSourceIsScan"] is True
    assert cloud["pointCount"] == 1986
    assert [p.name for p in pkg.iterdir() if p.name.startswith("_")] == []


@pytest.mark.docker
@pytest.mark.parametrize("mode", ["direct", "staged"])
def test_convert_max_points_across_scan_boundary_keeps_ids(two_scan_e57, tmp_path, mode):
    # 1500 Rohpunkte = Scan 0 ganz (7 ungültig) + die ersten 500 von Scan 1 (7 ungültig).
    m = convert(two_scan_e57.path, tmp_path / "p", crs=None, max_points=1500, mode=mode)
    las = laspy.read(tmp_path / "p" / "cloud.copc.laz")
    assert np.array_equal(np.bincount(las.point_source_id), [0, 993, 493])
    cloud = cloud_of(m)
    assert cloud["readerPointCount"] == 1500
    assert cloud["pointSourceIsScan"] is True


@pytest.mark.docker
@pytest.mark.parametrize("mode", ["direct", "staged"])
def test_convert_survives_nan_coordinates_on_invalid_points(tmp_path, mode):
    """Ungültige Punkte laufen jetzt durch writers.las. Tragen sie NaN (laut E57 erlaubt:
    Koordinaten ungültiger Punkte sind bedeutungslos), brach PDAL ohne die Park-Stufe mit
    „Unable to convert scaled value (NaN) to int32“ ab (gemessen)."""
    local = np.random.default_rng(3).uniform(-4, 4, size=(300, 3))
    invalid = np.zeros(300, dtype=np.int64)
    invalid[:5] = 1
    local[:5] = np.nan
    fx = write_fixture(
        tmp_path / "nan.e57", [(Pose((100.0, 0.0, 0.0), (1, 0, 0, 0)), local, invalid)]
    )
    m = convert(fx.path, tmp_path / "p", crs=None, mode=mode)
    cloud = cloud_of(m)
    assert cloud["pointCount"] == 295
    assert cloud["invalidDropped"] == 5
    assert cloud["pointSourceIsScan"] is True
    las = laspy.read(tmp_path / "p" / "cloud.copc.laz")
    got = np.column_stack([las.x, las.y, las.z])
    assert _max_nn_dist(fx.world_points_valid, got) <= TOL
    assert _max_nn_dist(got, fx.world_points_valid) <= TOL
