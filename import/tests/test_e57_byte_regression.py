"""E57-Datenregression gemäß PF-C-Claude-1, unabhängig von COPC-Schreibreihenfolge."""

import json

import laspy
import numpy as np
import pytest

from scanview_import.convert import convert


def stable_manifest(path):
    m = json.loads(path.read_text())
    for conv in [m["conversion"], m["layers"][0]["conversion"]]:
        # Laufzeit/RSS sind Laufmessungen, keine deterministischen Produktmetadaten.
        conv.pop("seconds", None)
        conv.pop("peakRssMb", None)
    for layer in m["layers"]:
        if layer.get("kind") == "pointcloud":
            # Nur der Hash der COPC-Ausgabe entfällt. source.sha256 bleibt geprüft.
            layer.pop("sha256", None)
    return json.dumps(m, indent=2).encode()


def assert_same_cloud(a, b):
    assert a.header.point_count == b.header.point_count == len(a.points) == len(b.points)
    np.testing.assert_array_equal(a.header.mins, b.header.mins)
    np.testing.assert_array_equal(a.header.maxs, b.header.maxs)
    np.testing.assert_array_equal(a.header.scales, b.header.scales)
    np.testing.assert_array_equal(a.header.offsets, b.header.offsets)
    assert a.points.array.dtype == b.points.array.dtype
    # Alle Dimensionen sortieren: auch bei gleichen XYZ bleiben Attributzuordnung und
    # Multiplizitäten geprüft; kein set(), das doppelte Datensätze verlieren würde.
    fields = list(a.points.array.dtype.names)
    np.testing.assert_array_equal(
        np.sort(a.points.array, order=fields), np.sort(b.points.array, order=fields)
    )


@pytest.mark.docker
@pytest.mark.parametrize("color_limits", [False, True])
@pytest.mark.parametrize("mode", ["direct", "staged"])
def test_e57_data_stability(two_scan_e57, two_scan_e57_color_limits, tmp_path, color_limits, mode):
    source = two_scan_e57_color_limits.path if color_limits else two_scan_e57.path
    a, b = tmp_path / "a" / "pkg", tmp_path / "b" / "pkg"
    convert(source, a, None, mode=mode)
    convert(source, b, None, mode=mode)
    assert (a / "inventory.json").read_bytes() == (b / "inventory.json").read_bytes()
    assert stable_manifest(a / "project.json") == stable_manifest(b / "project.json")
    assert_same_cloud(laspy.read(a / "cloud.copc.laz"), laspy.read(b / "cloud.copc.laz"))


def cloud_fixture():
    header = laspy.LasHeader(point_format=7, version="1.4")
    cloud = laspy.LasData(header)
    # Gleiche XYZ mit verschiedenen Attributen: Paarung muss trotzdem stimmen.
    cloud.x, cloud.y, cloud.z = [1, 1, 2], [2, 2, 3], [3, 3, 4]
    cloud.red = [257, 514, 65535]
    cloud.green = [65535, 0, 257]
    cloud.intensity = [10, 20, 30]
    cloud.point_source_id = [1, 2, 2]
    cloud.gps_time = [10.25, 20.5, 30.75]
    cloud.update_header()
    return cloud


def test_cloud_comparison_accepts_reordering():
    a, b = cloud_fixture(), cloud_fixture()
    b.points = b.points[[2, 1, 0]]
    assert_same_cloud(a, b)


@pytest.mark.parametrize("dimension", ["red", "green", "intensity", "point_source_id", "gps_time"])
def test_cloud_comparison_rejects_attribute_changes(dimension):
    a, b = cloud_fixture(), cloud_fixture()
    b[dimension][1] += 1
    with pytest.raises(AssertionError):
        assert_same_cloud(a, b)


def test_cloud_comparison_rejects_changed_point_with_same_bounds():
    a, b = cloud_fixture(), cloud_fixture()
    b.x = [1, 1.5, 2]
    b.update_header()
    with pytest.raises(AssertionError):
        assert_same_cloud(a, b)


def test_cloud_comparison_rejects_changed_multiplicity():
    a, b = cloud_fixture(), cloud_fixture()
    b.points = b.points[[0, 0, 2]]
    b.update_header()
    with pytest.raises(AssertionError):
        assert_same_cloud(a, b)


def test_cloud_comparison_rejects_missing_point():
    a, b = cloud_fixture(), cloud_fixture()
    b.points = b.points[:2]
    b.update_header()
    with pytest.raises(AssertionError):
        assert_same_cloud(a, b)


def test_cloud_comparison_rejects_changed_bounds():
    a, b = cloud_fixture(), cloud_fixture()
    b.header.maxs[0] += 1
    with pytest.raises(AssertionError):
        assert_same_cloud(a, b)


def test_manifest_normalization_preserves_source_and_conversion_contract(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    m = {
        "conversion": {"seconds": 1, "peakRssMb": 10, "mode": "direct"},
        "layers": [
            {
                "kind": "pointcloud",
                "sha256": "copc-a",
                "source": {"sha256": "source-a"},
                "conversion": {"seconds": 1, "peakRssMb": 10, "mode": "direct"},
            }
        ],
    }
    a.write_text(json.dumps(m))
    m["conversion"].update(seconds=2, peakRssMb=20)
    m["layers"][0]["conversion"].update(seconds=2, peakRssMb=20)
    m["layers"][0]["sha256"] = "copc-b"
    b.write_text(json.dumps(m))
    assert stable_manifest(a) == stable_manifest(b)
    m["layers"][0]["source"]["sha256"] = "source-b"
    b.write_text(json.dumps(m))
    assert stable_manifest(a) != stable_manifest(b)
    m["layers"][0]["source"]["sha256"] = "source-a"
    m["conversion"]["mode"] = "staged"
    b.write_text(json.dumps(m))
    assert stable_manifest(a) != stable_manifest(b)
