"""Rahmenkameras aus kameras.json v2: Leser, Sensoren, Referenzpaare, Panoramen-Filter."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scanview_import.metashape import read_cameras, read_frame_cameras
from scanview_import.verzeichnung import Kalibrierung, verzeichnen

CALIB = {"f": 300.0, "cx": 2.0, "cy": -1.0, "k1": 0.05, "k2": -0.01, "k3": 0.0, "k4": 0.0,
         "p1": 2e-3, "p2": -1e-3, "b1": 0.0, "b2": 0.0}


def _paare(width: int, height: int, calib: dict) -> list[dict]:
    k = Kalibrierung.aus_export(width, height, calib)
    xs, ys = np.meshgrid(np.linspace(-0.4, 0.4, 5), np.linspace(-0.3, 0.3, 4))
    u, v = verzeichnen(k, xs.ravel(), ys.ravel())
    return [{"ray": [float(a), float(b)], "pixel": [float(p), float(q)]}
            for a, b, p, q in zip(xs.ravel(), ys.ravel(), u, v, strict=True)]


def export_v2(tmp_path: Path) -> Path:
    frame = tmp_path / "F1.jpg"
    Image.new("RGB", (240, 160), "green").save(frame)
    hoch = tmp_path / "F2.jpg"
    Image.new("RGB", (160, 240), "green").save(hoch)
    kugel = tmp_path / "kugel.png"
    Image.new("RGB", (128, 64), "red").save(kugel)
    data = {
        "formatVersion": 2,
        "crs": {"epsg": 32633, "verticalDatum": "DHHN2016"},
        "cameras": [
            {"label": "P1", "group": "Pano", "kind": "spherical", "imagePath": str(kugel),
             "width": 128, "height": 64, "position": [0, 0, 0], "rotation": np.eye(3).tolist()},
            {"label": "F1", "group": "Nadir", "kind": "frame", "sensor": 0, "imagePath": str(frame),
             "width": 240, "height": 160, "position": [10, 20, 30],
             "rotation": np.eye(3).tolist()},
            {"label": "F2", "group": "Fassade", "kind": "frame", "sensor": 1,
             "imagePath": str(hoch), "width": 160, "height": 240, "position": [11, 20, 30],
             "rotation": np.eye(3).tolist()},
        ],
        "sensors": [
            {"id": 0, "label": "quer", "type": "frame", "width": 240, "height": 160,
             "calibration": CALIB, "referencePairs": _paare(240, 160, CALIB)},
            {"id": 1, "label": "hoch", "type": "frame", "width": 160, "height": 240,
             "calibration": CALIB, "referencePairs": _paare(160, 240, CALIB)},
        ],
        "tiePoints": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
        "tieProjections": [{"camera": "F1", "pixel": [120.5, 80.5], "world": [1.0, 2.0, 3.0]}],
        "skipped": [],
    }
    path = tmp_path / "kameras.json"
    path.write_text(json.dumps(data))
    return path


def test_frames_with_sensors_and_tie_points(tmp_path):
    export = read_frame_cameras(export_v2(tmp_path))
    assert [c.label for c in export.cameras] == ["F1", "F2"]
    assert export.cameras[1].sensor_id == 1
    assert export.sensors[0].kalibrierung.f == 300.0
    assert export.sensors[1].kalibrierung.width == 160
    assert export.tie_points.shape == (2, 3)
    assert export.tie_projections[0].camera == "F1"
    assert export.max_referenz_abweichung_px <= 1e-9
    assert {s["label"] for s in export.skipped} == {"P1"}


def test_panorama_reader_takes_only_spherical_from_v2(tmp_path):
    export = read_cameras(export_v2(tmp_path))
    assert [c.label for c in export.cameras] == ["P1"]


def _mutate(tmp_path, change):
    path = export_v2(tmp_path)
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))
    return path


@pytest.mark.parametrize(
    "change,message",
    [
        (lambda d: d["sensors"][0]["calibration"].update(p1=CALIB["p2"], p2=CALIB["p1"]),
         "Referenzpaar"),
        (lambda d: d["sensors"][0].update(referencePairs=[]), "Referenzpaare"),
        (lambda d: d["cameras"][1].update(sensor=9), "Sensor 9"),
        (lambda d: d["cameras"][1].update(width=200), "Sensor"),
        (lambda d: d["sensors"][0]["calibration"].pop("k4"), "unvollständig"),
        (lambda d: d["sensors"][0]["calibration"].update(p3=1e-4), r"Sensor \d+ .*p3/p4"),
        (lambda d: d["sensors"][0].update(type="fisheye"), "fisheye"),
        (lambda d: d["cameras"][2].update(label="F1"), r"F1: Label in mehreren Gruppen.*Tie-Point"),
        (lambda d: d["tiePoints"].append([1, 2]), "Tie Point"),
        (lambda d: d.update(formatVersion=1), "formatVersion 2"),
    ],
)
def test_invalid_frame_exports(tmp_path, change, message):
    with pytest.raises(ValueError, match=message):
        read_frame_cameras(_mutate(tmp_path, change))


def test_image_size_must_match_file(tmp_path):
    path = export_v2(tmp_path)
    Image.new("RGB", (100, 100)).save(tmp_path / "F1.jpg")
    with pytest.raises(ValueError, match="Bildmaße"):
        read_frame_cameras(path)


def test_no_frame_cameras_is_an_error(tmp_path):
    path = _mutate(tmp_path, lambda d: d.update(cameras=d["cameras"][:1]))
    with pytest.raises(ValueError, match="keine Rahmenkameras"):
        read_frame_cameras(path)
