"""Exportmathematik und API-Stub: Metashape ist hier nicht installiert."""

from types import SimpleNamespace

import numpy as np
import pytest
from metashape.kameras_exportieren import _tie_points, export_cameras, rotation_from_axes
from scipy.spatial.transform import Rotation


def test_orthonormalize_grid_axes():
    expected = Rotation.from_euler("zyx", [1.5, 15, 30], degrees=True).as_matrix()
    distorted = expected @ np.array([[1.01, 0.002, 0.001], [0, 0.99, 0.003], [0, 0, 1.02]])
    result = np.array(rotation_from_axes(distorted.T.tolist()))
    np.testing.assert_allclose(result.T @ result, np.eye(3), atol=1e-14)
    assert np.linalg.det(result) == pytest.approx(1)
    np.testing.assert_allclose(result, expected, atol=1e-14)


@pytest.mark.parametrize(
    "axes",
    [
        [[0, 0, 0], [0, 1, 0], [0, 0, 1]],
        [[1, 0, 0], [2, 0, 0], [0, 0, 1]],
        [[1, 0, 0], [0, 1, 0], [0, 0, -1]],
        [[float("nan"), 0, 0], [0, 1, 0], [0, 0, 1]],
    ],
)
def test_invalid_axes(axes):
    with pytest.raises(ValueError):
        rotation_from_axes(axes)


class Matrix:
    def __init__(self, matrix):
        self.matrix = np.array(matrix)

    def __mul__(self, other):
        return Matrix(self.matrix @ other.matrix)

    def mulp(self, point):
        return (self.matrix @ np.r_[point, 1])[:3]


class CRS:
    def __init__(self, definition):
        self.definition = definition
        self.geoccs = self

    def localframe(self, point):
        pytest.fail("localframe darf nicht benutzt werden")

    @staticmethod
    def transform(point, source, target):
        # Projektion mit Gitterdrehung und asymmetrischer Skalierung.
        angle = np.deg2rad(1.5)
        r = Rotation.from_euler("z", angle).as_matrix()
        return r @ np.diag([1.001, 1.001, 1.0]) @ point


def stub(tmp_path):
    path = tmp_path / "original.jpg"
    path.write_bytes(b"synthetischer Stub, Bildpixel werden beim Export nicht gelesen")

    def camera(label, group="Boden", aligned=True, spherical=True):
        return SimpleNamespace(
            label=label,
            group=SimpleNamespace(label=group),
            transform=Matrix(np.eye(4)) if aligned else None,
            photo=SimpleNamespace(path=str(path)),
            sensor=SimpleNamespace(
                type="spherical" if spherical else "frame", width=128, height=64
            ),
        )

    chunk = SimpleNamespace(
        crs=CRS("source"),
        transform=SimpleNamespace(matrix=Matrix(np.eye(4))),
        cameras=[
            camera("A"),
            camera("B", aligned=False),
            camera("C", spherical=False),
            camera("D", group="Luft"),
        ],
    )
    api = SimpleNamespace(
        CoordinateSystem=CRS,
        Vector=lambda v: np.array(v),
        Sensor=SimpleNamespace(Type=SimpleNamespace(Spherical="spherical")),
    )
    return chunk, api


def test_export_stub_grid_rotation_and_selection(tmp_path):
    chunk, api = stub(tmp_path)
    export = export_cameras(chunk, 32632, "ellipsoidisch", groups=["Boden"], api=api)
    assert [c["label"] for c in export["cameras"]] == ["A"]
    assert {c["label"] for c in export["skipped"]} == {"B", "C", "D"}
    assert export["crs"] == {"epsg": 32632, "verticalDatum": "ellipsoidisch"}
    expected = Rotation.from_euler("z", 1.5, degrees=True).as_matrix()
    np.testing.assert_allclose(export["cameras"][0]["rotation"], expected, atol=1e-14)
    assert export["cameras"][0]["imagePath"].startswith("/")


def test_no_aligned_spherical_cameras(tmp_path):
    chunk, api = stub(tmp_path)
    export = export_cameras(chunk, 32632, "ellipsoidisch", groups=["fehlend"], api=api)
    assert export["cameras"] == []
    assert len(export["skipped"]) == 4


def test_scaled_chunk_and_transformed_camera_position(tmp_path):
    chunk, api = stub(tmp_path)
    transform = np.eye(4)
    transform[:3, :3] *= 0.001
    transform[:3, 3] = [1_000_000, 2_000_000, 3_000_000]
    chunk.transform.matrix = Matrix(transform)
    camera = np.eye(4)
    camera[:3, :3] = Rotation.from_euler("z", 60, degrees=True).as_matrix()
    camera[:3, 3] = [1000, 2000, 3000]
    chunk.cameras[0].transform = Matrix(camera)
    export = export_cameras(chunk, 32632, "ellipsoidisch", groups=["Boden"], api=api)
    result = export["cameras"][0]
    expected_position = CRS.transform(np.array([1_000_001, 2_000_002, 3_000_003]), chunk.crs, None)
    np.testing.assert_allclose(result["position"], expected_position, atol=1e-8)
    np.testing.assert_allclose(
        result["rotation"], Rotation.from_euler("z", 61.5, degrees=True).as_matrix(), atol=1e-7
    )


# --- Rahmenkameras (formatVersion 2) ---------------------------------------------------------

FRAME_CALIB = dict(
    f=1000.0, cx=3.5, cy=-2.0, k1=0.1, k2=-0.05, k3=0.01, k4=0.0, p1=1e-4, p2=-2e-4, b1=0.5, b2=0.1
)


def _stub_project(calib, width, height):
    """Metashape-Vorwärtsmodell im Stub (Kamera-KS: X rechts, Y unten, Z vorn)."""

    def project(point):
        x, y = point[0] / point[2], point[1] / point[2]
        r2 = x * x + y * y
        radial = 1 + calib["k1"] * r2 + calib["k2"] * r2**2 + calib["k3"] * r2**3
        xd = x * radial + calib["p1"] * (r2 + 2 * x * x) + 2 * calib["p2"] * x * y
        yd = y * radial + calib["p2"] * (r2 + 2 * y * y) + 2 * calib["p1"] * x * y
        u = width / 2 + calib["cx"] + xd * calib["f"] + xd * calib["b1"] + yd * calib["b2"]
        v = height / 2 + calib["cy"] + yd * calib["f"]
        return np.array([u, v])

    return project


def frame_stub(tmp_path, *, rolling="disabled", calibration=True):
    chunk, api = stub(tmp_path)
    api.Sensor.Type.Frame = "frame"
    api.Shutter = SimpleNamespace(Model=SimpleNamespace(Disabled="disabled", Full="full"))
    sensor = SimpleNamespace(
        key=7,
        label="FC8482",
        type="frame",
        width=800,
        height=600,
        calibration=SimpleNamespace(width=800, height=600, **FRAME_CALIB) if calibration else None,
        rolling_shutter=rolling,
    )
    project = _stub_project(FRAME_CALIB, 800, 600)
    cameras = []
    for i, label in enumerate(["F1", "F2", "F3"]):
        transform = np.eye(4)
        transform[:3, 3] = [i * 2.0, 0, 0]
        camera = SimpleNamespace(
            label=label,
            group=SimpleNamespace(label="Nadir"),
            transform=Matrix(transform),
            photo=chunk.cameras[0].photo,
            sensor=sensor,
        )

        def cam_project(point, transform=transform):
            local = np.linalg.inv(transform) @ np.r_[point, 1]
            return project(local[:3])

        camera.project = cam_project
        cameras.append(camera)
    chunk.cameras = [chunk.cameras[0], *cameras]
    points = [
        SimpleNamespace(coord=np.array([x, 1.0, 10.0, 1.0]), valid=True, track_id=t)
        for t, x in enumerate(np.linspace(-3, 5, 12))
    ]
    points.append(SimpleNamespace(coord=np.array([0, 0, 10.0, 1.0]), valid=False, track_id=99))
    by_label = {
        cam.label: [
            SimpleNamespace(coord=cam.project(p.coord[:3]), track_id=p.track_id) for p in points
        ]
        for cam in cameras
    }

    class Projections:
        """Wie Metashape: Zugriff `projections[camera]`."""

        def __getitem__(self, camera):
            return by_label[camera.label]

    chunk.tie_points = SimpleNamespace(points=points, projections=Projections())
    return chunk, api, cameras


def test_frame_cameras_exported_with_adjusted_calibration(tmp_path):
    chunk, api, _ = frame_stub(tmp_path)
    export = export_cameras(chunk, 32632, "ellipsoidisch", api=api)
    assert export["formatVersion"] == 2
    frames = [c for c in export["cameras"] if c["kind"] == "frame"]
    assert [c["label"] for c in frames] == ["F1", "F2", "F3"]
    assert all(c["sensor"] == 7 for c in frames)
    assert [c["kind"] for c in export["cameras"] if c["label"] == "A"] == ["spherical"]
    (sensor,) = export["sensors"]
    assert sensor["id"] == 7 and sensor["type"] == "frame"
    assert (sensor["width"], sensor["height"]) == (800, 600)
    assert sensor["calibration"] == FRAME_CALIB | {"p3": 0.0, "p4": 0.0}


def test_reference_pairs_come_from_camera_project(tmp_path):
    chunk, api, _ = frame_stub(tmp_path)
    sensor = export_cameras(chunk, 32632, "ellipsoidisch", api=api)["sensors"][0]
    pairs = sensor["referencePairs"]
    # Raster ±0,8 und Randring: je Ecke und Kantenmitte der äußerste Strahl, dessen Pixel im
    # Bild liegt.
    assert len(pairs) == 20 + 8
    rand = pairs[20:]
    rand_px = [
        min(p["pixel"][0], 800 - p["pixel"][0], p["pixel"][1], 600 - p["pixel"][1]) for p in rand
    ]
    assert max(rand_px) <= 0.1 * 300  # alle nah an einer Bildkante
    f = FRAME_CALIB["f"]
    assert max(abs(p["ray"][0]) for p in pairs) >= 0.85 * 400 / f
    project = _stub_project(FRAME_CALIB, 800, 600)
    for pair in pairs:
        x, y = pair["ray"]
        np.testing.assert_allclose(pair["pixel"], project(np.array([x, y, 1.0]) * 10), atol=1e-9)
        assert 0 <= pair["pixel"][0] <= 800 and 0 <= pair["pixel"][1] <= 600


def test_randring_bleibt_im_bild(tmp_path):
    # Starke Verzeichnung schiebt Randstrahlen aus dem Bild: der Ring rückt nach innen, nie hinaus.
    chunk, api, cameras = frame_stub(tmp_path)
    for cam in cameras:
        innen = cam.project

        def gestreckt(point, innen=innen):
            u, v = innen(point)
            return np.array([400 + (u - 400) * 1.1, 300 + (v - 300) * 1.1])

        cam.project = gestreckt
    pairs = export_cameras(chunk, 32632, "ellipsoidisch", api=api)["sensors"][0]["referencePairs"]
    assert len(pairs) >= 20
    assert all(0 <= p["pixel"][0] <= 800 and 0 <= p["pixel"][1] <= 600 for p in pairs)


def test_rolling_shutter_and_missing_calibration_are_skipped(tmp_path):
    chunk, api, _ = frame_stub(tmp_path, rolling="full")
    export = export_cameras(chunk, 32632, "ellipsoidisch", api=api)
    reasons = {s["label"]: s["reason"] for s in export["skipped"]}
    assert reasons["F1"] == "Rolling-Shutter-Kompensation nicht unterstützt"
    assert export["sensors"] == []
    chunk, api, _ = frame_stub(tmp_path, calibration=False)
    export = export_cameras(chunk, 32632, "ellipsoidisch", api=api)
    reasons = {s["label"]: s["reason"] for s in export["skipped"]}
    assert reasons["F2"] == "keine angepasste Kalibrierung"


def test_tie_points_and_projections_exported(tmp_path):
    chunk, api, cameras = frame_stub(tmp_path)
    export = export_cameras(chunk, 32632, "ellipsoidisch", api=api)
    # 12 gültige Punkte, der ungültige fehlt; Weltkoordinaten über CRS.transform.
    assert len(export["tiePoints"]) == 12
    np.testing.assert_allclose(
        export["tiePoints"][0], CRS.transform(np.array([-3.0, 1.0, 10.0]), None, None), atol=1e-9
    )
    projections = export["tieProjections"]
    assert {p["camera"] for p in projections} == {"F1", "F2", "F3"}
    first = next(p for p in projections if p["camera"] == "F2")
    np.testing.assert_allclose(first["pixel"], cameras[1].project(np.array([-3.0, 1.0, 10.0])))
    np.testing.assert_allclose(first["world"], export["tiePoints"][0], atol=1e-9)


def test_punkte_im_unendlichen_werden_uebersprungen(tmp_path):
    # Metashape führt gültige Tie Points mit w = 0 (Punkt im Unendlichen; in echten Projekten
    # Millionen). Ohne Weltkoordinate: nicht exportieren und nicht durch 0 teilen.
    chunk, api, _ = frame_stub(tmp_path)
    chunk.tie_points.points.append(
        SimpleNamespace(coord=np.array([1.0, 2.0, 3.0, 0.0]), valid=True, track_id=50)
    )
    export = export_cameras(chunk, 32632, "ellipsoidisch", api=api)
    assert len(export["tiePoints"]) == 12
    assert all(np.isfinite(export["tiePoints"]).all(axis=1))


def test_nur_die_stichprobe_wird_umgerechnet(tmp_path):
    # Die KBS-Umrechnung ist in Metashape teuer (Minuten bei Millionen Tie Points): nur Punkte der
    # Stichprobe und der gezogenen Projektionen umrechnen, nicht alle.
    chunk, _, cameras = frame_stub(tmp_path)
    aufrufe: list[tuple[float, ...]] = []

    def world(p):
        aufrufe.append(tuple(p))
        return [p[0] + 1000.0, p[1], p[2]]

    punkte, projektionen = _tie_points(chunk, cameras, world, 3, 1, 2)
    assert len(punkte) == 3 and len(projektionen) == 2
    assert len(aufrufe) == len(set(aufrufe)) <= 3 + 2
    # Projektionen tragen dieselbe Umrechnung wie die Punkte.
    for p in projektionen:
        assert p["world"][0] > 900


def test_kinds_filter_and_caps(tmp_path):
    chunk, api, _ = frame_stub(tmp_path)
    export = export_cameras(
        chunk,
        32632,
        "ellipsoidisch",
        kinds={"frame"},
        max_tie_points=5,
        max_projection_cameras=2,
        max_projections=3,
        api=api,
    )
    assert {c["kind"] for c in export["cameras"]} == {"frame"}
    assert len(export["tiePoints"]) == 5
    assert {p["camera"] for p in export["tieProjections"]} <= {"F1", "F2", "F3"}
    assert len({p["camera"] for p in export["tieProjections"]}) == 2
    assert len(export["tieProjections"]) == 6


def test_projektionen_je_gruppe(tmp_path):
    """Kleine Gruppen bekommen eigene Projektionen, sonst scheitert der Import mit --gruppe."""
    chunk, api, cameras = frame_stub(tmp_path)
    cameras[2].group = SimpleNamespace(label="Fassade")
    export = export_cameras(
        chunk, 32632, "ellipsoidisch", kinds={"frame"}, max_projection_cameras=1, api=api
    )
    gruppen = {p["camera"] for p in export["tieProjections"]}
    assert "F3" in gruppen  # einzige Kamera der Gruppe Fassade
    assert len(gruppen) == 2  # je Gruppe höchstens eine Kamera
