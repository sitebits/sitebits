"""Synthetische Metashape-Kameras, ohne Kunden- oder Metashape-Daten."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scanview_import.metashape import read_cameras


def camera_file(tmp_path: Path) -> Path:
    image = tmp_path / "kugel.png"
    Image.new("RGB", (128, 64), "red").save(image)
    path = tmp_path / "kameras.json"
    path.write_text(
        json.dumps(
            {
                "formatVersion": 1,
                "crs": {"epsg": 32632, "verticalDatum": "ellipsoidisch"},
                "cameras": [
                    {
                        "label": "Kamera A",
                        "group": "Luft",
                        "imagePath": str(image),
                        "width": 128,
                        "height": 64,
                        "position": [100, 200, 30],
                        "rotation": np.eye(3).tolist(),
                    }
                ],
                "skipped": [{"label": "Kamera B", "reason": "nicht ausgerichtet"}],
            }
        )
    )
    return path


def test_camera_format(tmp_path):
    export = read_cameras(camera_file(tmp_path))
    assert export.epsg == 32632
    assert export.vertical_datum == "ellipsoidisch"
    assert export.cameras[0].position == (100, 200, 30)
    assert export.cameras[0].group == "Luft"
    assert len(export.skipped) == 1


@pytest.mark.parametrize(
    "mutation,message",
    [
        ("version", "Format-Version"),
        ("missing", "Bild fehlt"),
        ("relative", "absolut"),
        ("rotation", "orthonormal"),
        ("reflection", "orthonormal"),
        ("nan", "endlich"),
        ("dimensions", "Bildmaße"),
        ("epsg", "EPSG"),
        ("datum", "Höhensystem"),
    ],
)
def test_invalid_camera_format(tmp_path, mutation, message):
    path = camera_file(tmp_path)
    data = json.loads(path.read_text())
    camera = data["cameras"][0]
    if mutation == "version":
        data["formatVersion"] = 99
    elif mutation == "missing":
        camera["imagePath"] = str(tmp_path / "fehlt.jpg")
    elif mutation == "relative":
        camera["imagePath"] = "kugel.png"
    elif mutation == "rotation":
        camera["rotation"][0][0] = 2
    elif mutation == "reflection":
        camera["rotation"][0][0] = -1
    elif mutation == "nan":
        camera["position"][0] = float("nan")
    elif mutation == "dimensions":
        camera["width"] = 126
    elif mutation == "epsg":
        data["crs"]["epsg"] = True
    else:
        data["crs"]["verticalDatum"] = ""
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=message):
        read_cameras(path)


def test_adapter_world_poses_and_projection(tmp_path):
    from scipy.spatial.transform import Rotation

    from scanview_import.geometry import cube_face_for, quat_to_matrix
    from scanview_import.metashape import metashape_station_sources

    path = camera_file(tmp_path)
    data = json.loads(path.read_text())
    # Bekannte Drehung um Welt-Z, nicht die Identität als einzige Probe.
    rotation = Rotation.from_euler("z", 90, degrees=True).as_matrix()
    data["cameras"][0]["rotation"] = rotation.tolist()
    path.write_text(json.dumps(data))
    sources = metashape_station_sources(path)
    station = sources[0]
    assert station.name == "Luft / Kamera A"
    assert station.point_source_id is None
    np.testing.assert_allclose(
        quat_to_matrix(station.pose.rotation_wxyz), rotation @ np.diag([1, -1, -1]), atol=1e-14
    )
    for face, index in station.faces.items():
        im = station.images[index]
        assert cube_face_for(im.pose.rotation_wxyz, station.pose.rotation_wxyz) == face
        assert im.pose.translation == (100, 200, 30)
        assert im.width == im.height == 32
    # +Z in Metashape ist Bildmitte: in der Naht die nz-Seite.
    im = station.images[station.faces["nz"]]
    world = np.array([100, 200, 30]) + rotation @ np.array([0.25, -0.125, 1.0])
    camera = quat_to_matrix(im.pose.rotation_wxyz).T @ (world - im.pose.translation)
    assert camera[2] < 0
    u = im.principal_point[0] + im.focal_length / im.pixel_width * camera[0] / -camera[2]
    v = im.principal_point[1] - im.focal_length / im.pixel_height * camera[1] / -camera[2]
    np.testing.assert_allclose([u, v], [19.5, 13.5])
    assert station.read_image(im).size == (32, 32)


def test_adapter_reads_lazily_and_keeps_camera_binding(tmp_path, monkeypatch):
    from scanview_import.metashape import metashape_station_sources

    path = camera_file(tmp_path)
    data = json.loads(path.read_text())
    second = tmp_path / "zweite.png"
    Image.new("RGB", (128, 64), "blue").save(second)
    row = dict(data["cameras"][0], label="Kamera C", group="", imagePath=str(second))
    data["cameras"].append(row)
    path.write_text(json.dumps(data))
    # Reader validation darf Metadaten öffnen; keine Pixel vor read_image laden.
    export = read_cameras(path)
    original = Image.open
    calls = []

    def opened(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(Image, "open", opened)
    sources = metashape_station_sources(export)
    assert calls == []
    assert sources[0].read_image(sources[0].images[0]).getpixel((16, 16)) == (255, 0, 0)
    assert sources[1].read_image(sources[1].images[0]).getpixel((16, 16)) == (0, 0, 255)
    assert sources[0].scan_guid != sources[1].scan_guid
    assert sources[1].name == "Kamera C"


def test_invalid_numeric_content_has_german_message(tmp_path):
    path = camera_file(tmp_path)
    data = json.loads(path.read_text())
    data["cameras"][0]["rotation"][0][0] = "kein Wert"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Zahlen"):
        read_cameras(path)


def test_duplicate_camera_identity_is_rejected(tmp_path):
    path = camera_file(tmp_path)
    data = json.loads(path.read_text())
    data["cameras"].append(data["cameras"][0])
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="doppelt"):
        read_cameras(path)


def test_large_panorama_is_read_with_own_limit(tmp_path, monkeypatch):
    # Pillows Grenze kleiner als das Bild: der Adapter liest trotzdem und stellt sie wieder her.
    path = camera_file(tmp_path)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    assert len(read_cameras(path).cameras) == 1
    assert Image.MAX_IMAGE_PIXELS == 1000


def test_panorama_over_own_limit_is_rejected(tmp_path, monkeypatch):
    import scanview_import.metashape as metashape

    path = camera_file(tmp_path)
    monkeypatch.setattr(metashape, "PANORAMA_MAX_PIXELS", 1000)
    with pytest.raises(ValueError, match="zu groß"):
        read_cameras(path)
