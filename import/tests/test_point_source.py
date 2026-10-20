import json

import laspy
import pytest

from scanview_import.point_source import detect_format, resolve_source


def ply(path, extra=""):
    path.write_text(
        "ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\n"
        "property float y\nproperty float z\n" + extra + "end_header\n1 2 3\n"
    )
    return path


@pytest.mark.parametrize(
    ("suffix", "reader"),
    [
        (".las", "las"),
        (".laz", "las"),
        (".copc.laz", "las"),
        (".ply", "ply"),
        (".pts", "pts"),
        (".xyz", "text"),
        (".txt", "text"),
        (".csv", "text"),
        (".e57", "e57"),
    ],
)
def test_detect(suffix, reader):
    assert detect_format("wolke" + suffix)[1] == "readers." + reader


def test_mesh_ply_refused(tmp_path):
    path = ply(tmp_path / "mesh.ply", "element face 1\nproperty list uchar int vertex_indices\n")
    with pytest.raises(ValueError, match="Mesh-PLY folgt mit Weg A"):
        resolve_source(path, unit="m")


def test_ply_requires_unit_and_remains_local(tmp_path):
    path = ply(tmp_path / "points.ply")
    with pytest.raises(ValueError, match="Einheit"):
        resolve_source(path)
    src = resolve_source(path, unit="mm", up="y")
    assert src.crs is None and src.unit == "mm" and src.up == "y"
    assert src.unit_from == "cli"


def test_sidecar_and_conflicts(tmp_path):
    path = ply(tmp_path / "points.ply")
    path.with_name(path.name + ".scanview.json").write_text(
        json.dumps({"crs": "EPSG:25833", "unit": "cm"})
    )
    src = resolve_source(path, crs="epsg:25833", unit="cm")
    assert src.crs_from == "sidecar" and src.unit_from == "sidecar"
    with pytest.raises(ValueError, match=r"CRS.*widersprechen"):
        resolve_source(path, crs="EPSG:25832")
    with pytest.raises(ValueError, match=r"Einheiten.*widersprechen"):
        resolve_source(path, unit="m")


def test_las_crs_file_and_wkt_equivalence(tmp_path):
    path = tmp_path / "points.las"
    h = laspy.LasHeader(point_format=7, version="1.4")
    wkt = 'PROJCS["UTM",GEOGCS["WGS84",AUTHORITY["EPSG","4326"]],AUTHORITY["EPSG","25833"]]'
    h.vlrs.append(laspy.vlrs.known.WktCoordinateSystemVlr(wkt))
    laspy.LasData(h).write(path)
    assert resolve_source(path, crs="EPSG:25833").crs_from == "file"
    with pytest.raises(ValueError, match=r"CRS.*widersprechen"):
        resolve_source(path, crs="EPSG:4326")
    with pytest.raises(ValueError, match=r"Einheiten.*widersprechen"):
        resolve_source(path, unit="ft")


def test_local_geokey(tmp_path):
    from laspy.vlrs.known import GeoAsciiParamsVlr, GeoKeyDirectoryVlr, GeoKeyEntryStruct

    path = tmp_path / "local.las"
    h = laspy.LasHeader(point_format=3, version="1.2")
    v = GeoKeyDirectoryVlr()
    key = GeoKeyEntryStruct()
    key.id, key.tiff_tag_location, key.count, key.value_offset = 3072, 0, 1, 32767
    v.geo_keys = [key]
    v.geo_keys_header.number_of_keys = 1
    citation = GeoAsciiParamsVlr()
    citation.strings = ["Local Coordinates"]
    h.vlrs.extend([v, citation])
    laspy.LasData(h).write(path)
    assert resolve_source(path).crs is None
    with pytest.raises(ValueError, match=r"CRS.*widersprechen"):
        resolve_source(path, crs="EPSG:25833")


def test_columns_required_and_validated(tmp_path):
    path = tmp_path / "p.csv"
    path.write_text("x,y,z,r,g,b\n1,2,3,4,5,6\n")
    with pytest.raises(ValueError, match="--columns"):
        resolve_source(path, unit="m")
    src = resolve_source(path, unit="m", columns="x,y,z,r,g,b")
    assert src.columns == ("X", "Y", "Z", "Red", "Green", "Blue")
    assert src.text_skip == 1 and src.separator == ","
    for columns in ["x,y", "x,y,z,z", "x,y,z,guess"]:
        with pytest.raises(ValueError, match="Spalten"):
            resolve_source(path, unit="m", columns=columns)


@pytest.mark.parametrize("value", [{}, {"unit": "yard"}, {"unit": 3}, {"unit": "m", "crs": 4}])
def test_bad_sidecar(tmp_path, value):
    path = ply(tmp_path / "points.ply")
    path.with_name(path.name + ".scanview.json").write_text(json.dumps(value))
    with pytest.raises(ValueError):
        resolve_source(path)


def test_unsupported_and_bad_ply(tmp_path):
    with pytest.raises(ValueError, match="PTX"):
        detect_format("p.ptx")
    path = tmp_path / "bad.ply"
    path.write_text("not a ply\n")
    with pytest.raises(ValueError, match="PLY"):
        resolve_source(path, unit="m")
