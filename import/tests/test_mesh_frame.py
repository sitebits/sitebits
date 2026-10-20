import copy
import json

import laspy
import numpy as np
import pytest

from scanview_import.mesh_convert import add_mesh
from scanview_import.mesh_frame import frame_check
from scanview_import.mesh_gltf import write_glb
from scanview_import.mesh_source import read_mesh
from scanview_import.mesh_surface import SourceMesh


def package(tmp_path):
    from test_mesh_convert import asymmetric_mesh

    src = asymmetric_mesh(tmp_path / "a.obj")
    mesh = read_mesh(src, unit="m")
    points = SourceMesh(mesh.positions, mesh.triangles, None, None).sample_surface(1200, 42)[0]
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    h = laspy.LasHeader(point_format=7, version="1.4")
    h.scales = [0.0001] * 3
    h.offsets = [5800000, 400000, 10]
    las = laspy.LasData(h)
    las.x, las.y, las.z = points.T
    las.write(pkg / "cloud.las")
    m = {
        "schemaVersion": 2,
        "revision": 1,
        "projectId": "test",
        "frame": {"unit": "m", "crs": {"wkt": None}, "originWorld": [5800000, 400000, 11]},
        "layers": [
            {
                "id": "punkte",
                "kind": "pointcloud",
                "url": "cloud.las",
                "sourceOptions": {"unit": "m"},
            }
        ],
    }
    (pkg / "project.json").write_text(json.dumps(m))
    return src, pkg, m


def test_add_keeps_frame_and_cloud(tmp_path):
    src, pkg, before = package(tmp_path)
    cloud_bytes = (pkg / "cloud.las").read_bytes()
    after = add_mesh(src, pkg, same_frame=True, unit="m", dreiecke=0)
    assert after["frame"] == before["frame"]
    assert after["layers"][0] == before["layers"][0]
    assert (pkg / "cloud.las").read_bytes() == cloud_bytes
    check = after["layers"][1]["frameCheck"]
    assert check["ok"] is True
    assert check["samples"] == 1200
    assert check["p95M"] < 0.001
    with pytest.raises(ValueError, match="existiert"):
        add_mesh(src, pkg, same_frame=True, unit="m")


@pytest.mark.parametrize("failure", ["no_confirmation", "crs", "unit", "offset"])
def test_add_rejects_without_mutating_package(tmp_path, failure):
    src, pkg, _before = package(tmp_path)
    kwargs = dict(same_frame=True, unit="m", dreiecke=0)
    if failure == "no_confirmation":
        kwargs["same_frame"] = False
    elif failure == "crs":
        kwargs["crs"] = "EPSG:25833"
    elif failure == "unit":
        kwargs["unit"] = "mm"
    else:
        text = src.read_text().replace("5800000", "5800010").replace("5800001", "5800011")
        src.write_text(text)
    raw = (pkg / "project.json").read_bytes()
    with pytest.raises(ValueError):
        add_mesh(src, pkg, **kwargs)
    assert (pkg / "project.json").read_bytes() == raw
    assert not (pkg / "layers/mesh").exists()


def test_frame_check_measures_written_full_stage(tmp_path):
    src, pkg, m = package(tmp_path)
    mesh = read_mesh(src, unit="m")
    origin = np.asarray(m["frame"]["originWorld"])
    write_glb(mesh, pkg / "model.glb", origin, {})
    assert frame_check(pkg, m["layers"][0], pkg / "model.glb", origin)["ok"]
    shifted = copy.deepcopy(mesh)
    shifted.positions[:, 2] += 0.2
    write_glb(shifted, pkg / "model.glb", origin, {})
    assert not frame_check(pkg, m["layers"][0], pkg / "model.glb", origin)["ok"]


def _gedreht(tmp_path, mit_lage):
    """Paket, dessen Wolke beim Import starr bewegt wurde (0,6° um z am Schwerpunkt, dx/dy)."""
    src, pkg, m = package(tmp_path)
    las = laspy.read(pkg / "cloud.las")
    punkte = np.column_stack([las.x, las.y, las.z])
    drehpunkt = punkte.mean(axis=0)
    w = np.radians(0.6)
    r = np.array([[np.cos(w), -np.sin(w), 0], [np.sin(w), np.cos(w), 0], [0, 0, 1]])
    matrix = np.eye(4)
    matrix[:3, :3] = r
    matrix[:3, 3] = drehpunkt - r @ drehpunkt + [-1.34, -0.63, 0]
    las.x, las.y, las.z = (punkte @ r.T + matrix[:3, 3]).T
    las.write(pkg / "cloud.las")
    if mit_lage:
        m["layers"][0]["sourceOptions"]["lage"] = {
            "art": "transformation",
            "matrix": matrix.tolist(),
            "einheit": "m",
            "drehpunkt": drehpunkt.tolist(),
        }
    (pkg / "project.json").write_text(json.dumps(m))
    return src, pkg, m


def test_add_wendet_lage_des_pakets_an(tmp_path):
    from scanview_import.mesh_verify import verify_mesh

    src, pkg, m = _gedreht(tmp_path, mit_lage=True)
    after = add_mesh(src, pkg, same_frame=True, unit="m", dreiecke=0)
    mesh = after["layers"][1]
    assert mesh["frameCheck"]["ok"] is True
    assert mesh["frameCheck"]["p95M"] < 0.001
    assert mesh["sourceOptions"]["lage"] == m["layers"][0]["sourceOptions"]["lage"]
    # Eigenständiges verify liest die Quelle neu und muss dieselbe Lage anwenden.
    assert verify_mesh(src, pkg)["ok"] is True


def test_add_ohne_lagevermerk_scheitert_am_framecheck(tmp_path):
    src, pkg, _m = _gedreht(tmp_path, mit_lage=False)
    raw = (pkg / "project.json").read_bytes()
    with pytest.raises(ValueError, match="frameCheck"):
        add_mesh(src, pkg, same_frame=True, unit="m", dreiecke=0)
    assert (pkg / "project.json").read_bytes() == raw


def _teil_wolke(tmp_path, versatz=(0.0, 0.0, 0.0)):
    """Wolke = Mesh-Oberfläche + Gelände drumherum (Teil-Mesh wie ein Gebäude im Gelände)."""
    src, pkg, _m = package(tmp_path)
    mesh = read_mesh(src, unit="m")
    auf = SourceMesh(mesh.positions, mesh.triangles, None, None).sample_surface(3000, 7)[0]
    lo, hi = mesh.positions.min(axis=0), mesh.positions.max(axis=0)
    rng = np.random.default_rng(3)
    umgebung = rng.uniform(lo - (hi - lo) * 2, hi + (hi - lo) * 2, size=(30000, 3))
    # Gelände nur außerhalb des Mesh-Grundrisses (wie bei einer Mesh-Region mit Boden darin).
    draussen = np.any((umgebung[:, :2] < lo[:2] - 2) | (umgebung[:, :2] > hi[:2] + 2), axis=1)
    umgebung = umgebung[draussen]
    las = laspy.read(pkg / "cloud.las")
    pts = np.vstack([auf, umgebung]) + versatz
    las = laspy.LasData(las.header)
    las.x, las.y, las.z = pts.T
    las.write(pkg / "cloud.las")
    return src, pkg


def test_teil_mesh_nur_im_grundriss_gemessen(tmp_path):
    src, pkg = _teil_wolke(tmp_path)
    after = add_mesh(src, pkg, same_frame=True, unit="m", dreiecke=0)
    check = after["layers"][1]["frameCheck"]
    assert check["ok"] is True
    assert check["within"] == "meshFootprint1m"
    assert check["p95M"] < 0.001


def test_teil_mesh_versatz_faellt_auf(tmp_path):
    src, pkg = _teil_wolke(tmp_path, versatz=(0.0, 0.0, 0.05))
    with pytest.raises(ValueError, match="frameCheck"):
        add_mesh(src, pkg, same_frame=True, unit="m", dreiecke=0)


def test_mesh_neben_der_wolke_bricht_ab(tmp_path):
    src, pkg = _teil_wolke(tmp_path, versatz=(500.0, 0.0, 0.0))
    with pytest.raises(ValueError, match="Wolkenpunkte im Bereich"):
        add_mesh(src, pkg, same_frame=True, unit="m", dreiecke=0)


def test_rahmen_grenze_ausdruecklich_angehoben_und_beim_verify_beibehalten(tmp_path):
    from scanview_import.mesh_verify import verify_mesh

    src, pkg = _teil_wolke(tmp_path, versatz=(0.015, 0.015, 0.015))
    raw = (pkg / "project.json").read_bytes()
    with pytest.raises(ValueError, match="frameCheck"):
        add_mesh(src, pkg, same_frame=True, unit="m", dreiecke=0)
    assert (pkg / "project.json").read_bytes() == raw
    after = add_mesh(src, pkg, same_frame=True, unit="m", dreiecke=0, rahmen_grenzen=(0.02, 0.15))
    check = after["layers"][1]["frameCheck"]
    assert check["ok"] is True and check["raised"] is True
    assert check["limitsM"] == {"p50": 0.02, "p95": 0.15}
    assert check["defaultLimitsM"] == {"p50": 0.005, "p95": 0.025}
    # Eigenständiges verify nimmt die vermerkte Grenze, nicht den Standard.
    assert verify_mesh(src, pkg)["frameCheck"]["ok"] is True


@pytest.mark.parametrize("grenzen", [(0.0, 0.1), (0.06, 0.1), (0.02, 0.31), (0.03, 0.02)])
def test_rahmen_grenze_begrenzt(tmp_path, grenzen):
    src, pkg = _teil_wolke(tmp_path)
    with pytest.raises(ValueError, match="Rahmengrenze"):
        add_mesh(src, pkg, same_frame=True, unit="m", dreiecke=0, rahmen_grenzen=grenzen)


def test_grosser_versatz_scheitert_auch_mit_hoechstgrenze(tmp_path):
    src, pkg = _teil_wolke(tmp_path, versatz=(0.0, 0.0, 0.4))
    with pytest.raises(ValueError, match="frameCheck"):
        add_mesh(src, pkg, same_frame=True, unit="m", dreiecke=0, rahmen_grenzen=(0.05, 0.30))


def _mit_baeumen(tmp_path, versatz=(0.0, 0.0, 0.0)):
    """Teil-Wolke (Klasse 6) plus Baumpunkte (Klasse 5) über dem Grundriss, die im Mesh fehlen."""
    src, pkg = _teil_wolke(tmp_path, versatz)
    las = laspy.read(pkg / "cloud.las")
    alt = np.column_stack([las.x, las.y, las.z])
    mesh = read_mesh(src, unit="m")
    mlo, mhi = mesh.positions.min(axis=0), mesh.positions.max(axis=0)
    rng = np.random.default_rng(9)
    baeume = rng.uniform(mlo, mhi, size=(len(alt), 3))
    baeume[:, 2] = mhi[2] + rng.uniform(1, 3, len(alt))
    neu = laspy.LasData(las.header)
    pts = np.vstack([alt, baeume])
    neu.x, neu.y, neu.z = pts.T
    neu.classification = np.r_[np.full(len(alt), 6), np.full(len(baeume), 5)].astype(np.uint8)
    neu.write(pkg / "cloud.las")
    return src, pkg


def test_rahmen_ohne_klassen_klammert_vegetation_aus(tmp_path):
    from scanview_import.mesh_verify import verify_mesh

    src, pkg = _mit_baeumen(tmp_path)
    with pytest.raises(ValueError, match="frameCheck"):
        add_mesh(src, pkg, same_frame=True, unit="m", dreiecke=0)
    after = add_mesh(src, pkg, same_frame=True, unit="m", dreiecke=0, rahmen_ohne_klassen=(5,))
    check = after["layers"][1]["frameCheck"]
    assert check["ok"] is True and check["excludedClasses"] == [5]
    # Eigenständiges verify nimmt die vermerkten Klassen.
    assert verify_mesh(src, pkg)["frameCheck"]["ok"] is True


def test_rahmen_ohne_klassen_versatz_faellt_weiter_auf(tmp_path):
    src, pkg = _mit_baeumen(tmp_path, versatz=(0.03, 0.03, 0.03))
    with pytest.raises(ValueError, match="frameCheck"):
        add_mesh(src, pkg, same_frame=True, unit="m", dreiecke=0, rahmen_ohne_klassen=(5,))


def test_ohne_ausschluss_unveraendert(tmp_path):
    src, pkg = _teil_wolke(tmp_path)
    after = add_mesh(src, pkg, same_frame=True, unit="m", dreiecke=0)
    assert after["layers"][1]["frameCheck"]["excludedClasses"] == []


def test_ausdehnung_toleranz_getrennt_und_vermerkt(tmp_path):
    from dataclasses import replace

    from scanview_import.mesh_verify import measure_stage

    src, _pkg, _m = package(tmp_path)
    mesh = read_mesh(src, unit="m")
    # Eine Ecke 50 mm nach außen: Ausdehnung 50 mm größer, Fläche sonst gleich.
    pos = mesh.positions.copy()
    i = int(np.argmax(pos[:, 2]))
    pos[i, 2] += 0.05
    quelle = replace(mesh, positions=pos)
    streng = measure_stage(quelle, mesh, 0.01, False, 0)
    assert streng["boundsMaxMm"] > 49 and streng["ok"] is False
    eigen = measure_stage(quelle, mesh, 0.01, False, 0, bounds_tolerance_m=0.06)
    assert eigen["boundsLimitMm"] == 60 and eigen["ok"] is True


@pytest.mark.parametrize("wert", [0.0, -5.0, 501.0])
def test_ausdehnung_toleranz_begrenzt(tmp_path, wert):
    src, pkg = _teil_wolke(tmp_path)
    with pytest.raises(ValueError, match="ausdehnung-toleranz"):
        add_mesh(src, pkg, same_frame=True, unit="m", dreiecke=0, ausdehnung_toleranz=wert)


def test_ausdehnung_toleranz_im_paket_vermerkt(tmp_path):
    from scanview_import.mesh_verify import verify_mesh

    src, pkg = _teil_wolke(tmp_path)
    after = add_mesh(src, pkg, same_frame=True, unit="m", dreiecke=0, ausdehnung_toleranz=120)
    assert after["layers"][1]["sourceOptions"]["ausdehnungToleranzM"] == 0.12
    assert verify_mesh(src, pkg)["stages"]["model"]["boundsLimitMm"] == pytest.approx(120)
