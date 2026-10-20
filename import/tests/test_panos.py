"""Tests für Würfelpanoramen je Standpunkt (`panos.build_panos`)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
from e57_fixture import FACE_COLORS, FACE_ROT, write_fixture
from PIL import Image

from scanview_import.geometry import VIEW_AXIS, cube_face_for, quat_to_matrix
from scanview_import.inventory import read_inventory
from scanview_import.model import ImageInfo, Pose
from scanview_import.panos import (
    FACES,
    LEVEL_FILE,
    STATIONS_SCHEMA_VERSION,
    StationSource,
    build_panos,
    face_levels,
    write_stations,
)

# Echte Referenz-E57 der Pilotkirche (45 Scans, 270 Bilder), nur lokal vorhanden: Pfad per Umgebung.
_REFERENZ_E57 = os.environ.get("SCANVIEW_REFERENZ_E57")
REFERENZ_E57 = Path(_REFERENZ_E57) if _REFERENZ_E57 else None

# Erwartete Weltblickrichtung je Würfelseite für die `cube_e57_rotated`-Fixture (Scan um +90°
# um Z gegiert): R_z(90°) = [[0,-1,0],[1,0,0],[0,0,1]] bildet lokal +X auf Welt +Y, +Y auf -X,
# +Z auf +Z ab (und die negativen Achsen entsprechend). Unabhängig von `cube_face_for` von Hand
# bestimmt, um diese Funktion nicht mit sich selbst zu prüfen
# (test_rotated_scan_faces_and_world_poses).
_WORLD_VIEW_DIR_AFTER_90DEG_YAW: dict[str, tuple[float, float, float]] = {
    "px": (0.0, 1.0, 0.0),
    "nx": (0.0, -1.0, 0.0),
    "py": (-1.0, 0.0, 0.0),
    "ny": (1.0, 0.0, 0.0),
    "pz": (0.0, 0.0, 1.0),
    "nz": (0.0, 0.0, -1.0),
}


def _new_pkg(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "project.json").write_text(
        json.dumps({"stations": {"url": "stations.json", "count": 0}})
    )
    return pkg


def test_faces_assigned_by_rotation_not_order(cube_e57, tmp_path):
    pkg = _new_pkg(tmp_path)
    st = build_panos(cube_e57.path, pkg, levels=(32, 64))
    s = st["stations"][0]
    assert s["id"] == "S001"
    assert s["panorama"]["kind"] == "cube"
    assert s["panorama"]["levels"] == [32, 64]
    for face, color in FACE_COLORS.items():
        img = Image.open(pkg / s["panorama"]["faces"][face] / "64.jpg").convert("RGB")
        px = img.getpixel((32, 32))
        assert all(abs(a - b) < 40 for a, b in zip(px, color, strict=True)), (face, px, color)
    assert json.loads((pkg / "project.json").read_text())["stations"]["count"] == 1


def test_scan_without_six_images_is_not_a_cube(two_scan_e57, tmp_path):
    pkg = _new_pkg(tmp_path)
    st = build_panos(two_scan_e57.path, pkg, levels=(32,))
    assert all(s["panorama"] is None for s in st["stations"])
    assert len(st["stations"]) == 2  # Standpunkte existieren trotzdem (Pose)


def test_levels_larger_than_source_are_skipped(cube_e57, tmp_path):
    pkg = _new_pkg(tmp_path)
    # Quellbilder sind 64x64 (siehe cube_e57-Fixture) — 128 muss ausgelassen werden.
    st = build_panos(cube_e57.path, pkg, levels=(32, 64, 128))
    s = st["stations"][0]
    assert s["panorama"]["levels"] == [32, 64]
    assert not (pkg / s["panorama"]["faces"]["pz"] / "128.jpg").exists()


def test_no_reachable_level_means_no_panorama(cube_e57, tmp_path):
    """D19: alle gewünschten Stufen größer als die 64-px-Quelle → kein Panorama (``None``),
    keine leeren Ordner, statt eines ``"cube"`` ohne Stufen im Vertrag für Scheibe 3."""
    pkg = _new_pkg(tmp_path)
    st = build_panos(cube_e57.path, pkg, levels=(128, 256))
    assert st["stations"][0]["panorama"] is None
    assert not (pkg / "panos" / "S001").exists()
    assert len(st["stations"][0]["images"]) == 6  # Bildposen bleiben trotzdem erhalten


def _img(width: int, height: int) -> ImageInfo:
    pose = Pose(translation=(0.0, 0.0, 0.0), rotation_wxyz=(1.0, 0.0, 0.0, 0.0))
    return ImageInfo(
        index=0, guid="g", kind="pinhole", width=width, height=height, pose=pose, scan_guid="s"
    )


def test_face_levels_uses_smaller_of_width_and_height():
    """D20: die Quellgröße berücksichtigt Breite und Höhe (nicht nur die Breite)."""
    faces = {f: _img(4096, 4096) for f in FACES}
    faces["pz"] = _img(4096, 1000)
    assert face_levels(faces, (512, 1024, 2048)) == (1000, [512])
    faces["nx"] = _img(300, 4096)
    assert face_levels(faces, (512, 1024)) == (300, [])


@pytest.mark.data
@pytest.mark.skipif(
    REFERENZ_E57 is None or not REFERENZ_E57.exists(),
    reason="Pilotdaten fehlen (SCANVIEW_REFERENZ_E57)",
)
def test_referenz_first_station_cube(tmp_path):
    """Datenbeweis auf der echten Referenz-E57 (Pilotkirche).

    Schreibt zusätzlich ``pkg/_check.json`` und ``pkg/panos/S001/_collage.jpg`` — mit
    ``--basetemp`` überlebt das Paket den Testlauf, sodass die Ergebnisse danach ins
    Abnahmeprotokoll (Slice 0.1) übernommen werden können (die Datei wird bewusst
    NICHT von diesem Test geschrieben, da spätere Läufe sie sonst überschreiben würden).
    Alle Messwerte werden vor den Assertions festgehalten, damit auch ein Fehlschlag
    auswertbar bleibt.
    """
    assert REFERENZ_E57 is not None
    pkg = tmp_path / "b"
    pkg.mkdir()
    (pkg / "project.json").write_text(json.dumps({"stations": {}}))
    st = build_panos(REFERENZ_E57, pkg, levels=(512,))
    stations = st["stations"]
    with_panorama = [s for s in stations if s["panorama"] is not None]

    s1 = stations[0]
    face_dirs = (s1["panorama"] or {}).get("faces", {})

    def _mean_brightness(face: str) -> float | None:
        p = pkg / face_dirs[face] / "512.jpg" if face in face_dirs else None
        if p is None or not p.exists():
            return None
        img = Image.open(p).convert("L")
        return float(np.asarray(img, dtype=np.float64).mean())

    # Welt-"oben"/"unten" unabhängig von der Scan-relativen Face-Zuordnung bestimmen:
    # Blickrichtung jedes Bildes direkt in der Weltpose auswerten (Bildposen sind im
    # Welt-KS) und die Bilder mit größter/kleinster Welt-Z-Komponente nehmen.
    #
    # Nicht über `guid` mit s1["images"] verknüpft: die Pilotkirche liefert für ALLE Bilder
    # eines Standpunkts denselben Platzhalter-Guid "0000000000000000" (nur der Scan-Guid
    # ist real) — ein `dict`, das über `guid` schlüsselt, kollabiert dadurch auf einen
    # einzigen Eintrag. Stattdessen wird die Würfelseite hier direkt (erneut) über
    # `cube_face_for` auf den unmittelbar identifizierten Bildposen berechnet.
    inv = read_inventory(REFERENZ_E57)
    scan1_pose = inv.scans[0].pose
    scan1_pinholes = [
        im for im in inv.images if im.scan_guid == inv.scans[0].guid and im.kind == "pinhole"
    ]
    world_z_component = {
        im.index: float((quat_to_matrix(im.pose.rotation_wxyz) @ VIEW_AXIS)[2])
        for im in scan1_pinholes
    }
    by_index = {im.index: im for im in scan1_pinholes}
    world_up_im = by_index[max(world_z_component, key=lambda i: world_z_component[i])]
    world_down_im = by_index[min(world_z_component, key=lambda i: world_z_component[i])]
    world_up_face = cube_face_for(world_up_im.pose.rotation_wxyz, scan1_pose.rotation_wxyz)
    world_down_face = cube_face_for(world_down_im.pose.rotation_wxyz, scan1_pose.rotation_wxyz)

    up_brightness = _mean_brightness(world_up_face)
    down_brightness = _mean_brightness(world_down_face)

    # Scan-Kippung: Winkel zwischen der (in Welt gedrehten) lokalen +Z-Achse des Scans
    # und der Welt-Hochachse (0,0,1) — belegt die "gelevelt"-Annahme mit einem Messwert,
    # statt sie nur zu behaupten.
    scan_r = quat_to_matrix(scan1_pose.rotation_wxyz)
    local_z_world = scan_r @ np.array([0.0, 0.0, 1.0])
    scan_tilt_deg = float(np.degrees(np.arccos(np.clip(local_z_world[2], -1.0, 1.0))))

    # Vorschau-Collage aus allen 6 Seiten (512 px), 3x2-Raster in fester Face-Reihenfolge
    # — immer erzeugen (auch wenn eine Seite fehlt/eine Assertion unten scheitert).
    order = ["px", "nx", "py", "ny", "pz", "nz"]
    size = 512
    collage = Image.new("RGB", (size * 3, size * 2), (32, 32, 32))
    for i, face in enumerate(order):
        p = pkg / face_dirs[face] / "512.jpg" if face in face_dirs else None
        if p is not None and p.exists():
            collage.paste(Image.open(p).convert("RGB"), ((i % 3) * size, (i // 3) * size))
    collage_dir = pkg / "panos" / "S001"
    collage_dir.mkdir(parents=True, exist_ok=True)
    collage_path = collage_dir / "_collage.jpg"
    collage.save(collage_path, "JPEG", quality=90)

    check = {
        "stationsTotal": len(stations),
        "stationsWithPanorama": len(with_panorama),
        "s1Faces": sorted(face_dirs.keys()),
        "worldUpFace": world_up_face,
        "worldDownFace": world_down_face,
        "upBrightness512Gray": up_brightness,
        "downBrightness512Gray": down_brightness,
        "scanTiltDeg": scan_tilt_deg,
        "collagePath": str(collage_path),
    }
    (pkg / "_check.json").write_text(json.dumps(check, indent=2))
    print(json.dumps(check, indent=2))  # -s beim Aufruf, damit das im Log landet

    assert s1["panorama"] is not None
    assert sorted(face_dirs.keys()) == sorted(["px", "nx", "py", "ny", "pz", "nz"])
    faces = [im["face"] for im in s1["images"]]
    assert sorted(faces) == sorted(["px", "nx", "py", "ny", "pz", "nz"])
    assert len(with_panorama) == len(stations), (
        f"{len(with_panorama)}/{len(stations)} Standpunkte mit Panorama"
    )
    assert world_up_face == "pz", check
    assert world_down_face == "nz", check
    assert up_brightness is not None and down_brightness is not None
    assert up_brightness > down_brightness, (up_brightness, down_brightness)


def test_stations_json_has_schema_version_and_level_file(cube_e57, tmp_path):
    pkg = _new_pkg(tmp_path)
    build_panos(cube_e57.path, pkg, levels=(32, 64))
    data = json.loads((pkg / "stations.json").read_text())
    assert data["schemaVersion"] == STATIONS_SCHEMA_VERSION == 2
    pano = data["stations"][0]["panorama"]
    assert pano["levelFile"] == LEVEL_FILE == "{level}.jpg"
    for face in FACES:
        for lv in pano["levels"]:
            assert (pkg / pano["faces"][face] / pano["levelFile"].format(level=lv)).is_file()


def test_face_images_point_to_the_image_of_that_face(cube_e57, tmp_path):
    pkg = _new_pkg(tmp_path)
    s = build_panos(cube_e57.path, pkg, levels=(32,))["stations"][0]
    fi = s["panorama"]["faceImages"]
    assert sorted(fi) == sorted(FACES)
    assert sorted(fi.values()) == list(range(6))
    for face, idx in fi.items():
        im = s["images"][idx]
        assert im["face"] == face
        assert im["pose"]["rotationQuat"] == [float(v) for v in FACE_ROT[face]]


def test_images_carry_pixel_dimensions_separately_from_pixel_pitch(cube_e57, tmp_path):
    pkg = _new_pkg(tmp_path)
    s = build_panos(cube_e57.path, pkg, levels=(32,))["stations"][0]
    for im in s["images"]:
        assert (im["imageWidth"], im["imageHeight"]) == (64, 64)
        # pixelWidth bleibt der E57-Pixelabstand in m, nicht die Pixelzahl.
        assert im["pixelWidth"] == pytest.approx(0.01 * 2 / 64)


def test_first_run_has_no_ground_z_and_rerun_keeps_it(cube_e57, tmp_path):
    pkg = _new_pkg(tmp_path)
    st = build_panos(cube_e57.path, pkg, levels=(32,))
    assert st["stations"][0]["groundZ"] is None
    data = json.loads((pkg / "stations.json").read_text())
    data["stations"][0]["groundZ"] = 0.25  # so schreibt es `overview`
    (pkg / "stations.json").write_text(json.dumps(data))
    st = build_panos(cube_e57.path, pkg, levels=(32,))
    assert st["stations"][0]["groundZ"] == 0.25


def test_ground_z_is_not_carried_over_when_scan_guid_at_that_id_changed(cube_e57, tmp_path):
    """Die Standpunkt-ID (``S001``) ist nur eine laufende Nummer der
    aktuellen Reihenfolge. Trägt der alte Eintrag mit derselben ID einen anderen ``scanGuid``
    (Scan-Menge/-Reihenfolge hat sich zwischen zwei Läufen geändert), darf ``groundZ`` nicht
    stillschweigend auf den falschen physischen Standpunkt übernommen werden."""
    pkg = _new_pkg(tmp_path)
    build_panos(cube_e57.path, pkg, levels=(32,))
    data = json.loads((pkg / "stations.json").read_text())
    data["stations"][0]["groundZ"] = 0.25
    data["stations"][0]["scanGuid"] = "{fremder-scan-aus-vorherigem-lauf}"
    (pkg / "stations.json").write_text(json.dumps(data))
    st = build_panos(cube_e57.path, pkg, levels=(32,))
    assert st["stations"][0]["groundZ"] is None


def test_rotated_scan_faces_and_world_poses(cube_e57_rotated, tmp_path):
    pkg = _new_pkg(tmp_path)
    s = build_panos(cube_e57_rotated.path, pkg, levels=(32,))["stations"][0]
    for face, color in FACE_COLORS.items():
        img = Image.open(pkg / s["panorama"]["faces"][face] / "32.jpg").convert("RGB")
        px = img.getpixel((16, 16))
        assert all(abs(a - b) < 40 for a, b in zip(px, color, strict=True)), (face, px)
        im = s["images"][s["panorama"]["faceImages"][face]]
        # Nicht über `cube_face_for` re-prüfen (zirkulär: dieselbe Funktion hat `face` schon
        # zugeordnet). Stattdessen die Weltblickrichtung direkt aus dem Quaternion berechnen und
        # gegen die für die bekannte Scan-Drehung (+90° um Z, Fixture `cube_e57_rotated`) konkret
        # erwartete Weltachse prüfen: eine Drehung um +90° um Z bildet lokal +X auf Welt +Y ab
        # (R_z(90°) = [[0,-1,0],[1,0,0],[0,0,1]]), entsprechend die übrigen Achsen.
        world_dir = quat_to_matrix(im["pose"]["rotationQuat"]) @ VIEW_AXIS
        assert np.allclose(world_dir, _WORLD_VIEW_DIR_AFTER_90DEG_YAW[face], atol=1e-9), (
            face,
            world_dir,
        )
        # Weltpose ≠ kanonische Seitenrotation: der Viewer muss die echte Bildpose nutzen.
        assert im["pose"]["rotationQuat"] != [float(v) for v in FACE_ROT[face]]


def _fake_images() -> list[ImageInfo]:
    return [
        ImageInfo(
            index=i,
            guid="0",
            kind="pinhole",
            width=64,
            height=64,
            pose=Pose((1.0, 2.0, 3.0), FACE_ROT[f]),
            scan_guid="s",
            focal_length=0.01,
            pixel_width=0.01 * 2 / 64,
            pixel_height=0.01 * 2 / 64,
            principal_point=(31.5, 31.5),
        )
        for i, f in enumerate(FACES)
    ]


def test_write_stations_accepts_any_adapter(tmp_path):
    """Adapter-Naht: eine Quelle ohne E57 (so wird ein NavVis-Adapter andocken)."""
    pkg = _new_pkg(tmp_path)
    src = StationSource(
        scan_guid="s",
        name="Fremdquelle",
        pose=Pose((1.0, 2.0, 3.0), (1.0, 0.0, 0.0, 0.0)),
        images=_fake_images(),
        faces={f: i for i, f in enumerate(FACES)},
        read_image=lambda im: Image.new("RGB", (64, 64), FACE_COLORS[FACES[im.index]]),
    )
    s = write_stations(pkg, [src], (32,))["stations"][0]
    assert s["panorama"]["faceImages"] == {f: i for i, f in enumerate(FACES)}
    px = Image.open(pkg / "panos/S001/nz/32.jpg").convert("RGB").getpixel((16, 16))
    assert all(abs(a - b) < 40 for a, b in zip(px, FACE_COLORS["nz"], strict=True))


def test_write_stations_rejects_incomplete_cube(tmp_path):
    pkg = _new_pkg(tmp_path)
    src = StationSource(
        scan_guid="s",
        name="kaputt",
        pose=Pose((0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0)),
        images=_fake_images(),
        faces={"px": 0},
        read_image=lambda im: Image.new("RGB", (64, 64)),
    )
    with pytest.raises(ValueError, match="unvollständig"):
        write_stations(pkg, [src], (32,))


# --- pointSourceId je Standpunkt --------------------------------------------------------------


def test_station_point_source_id_is_scan_index_plus_one(tmp_path):
    """Die Zahl, die convert als PointSourceId schreibt (Scanindex + 1, data3D-Reihenfolge),
    muss dieselbe sein, die der Standpunkt trägt — und S{n:03d} zählt in derselben
    Reihenfolge. Würfel absichtlich am zweiten Scan, damit eine Verwechslung auffällt."""
    order = ["nz", "px", "pz", "ny", "nx", "py"]
    pts = np.random.default_rng(9).uniform(-2, 2, size=(50, 3))
    fx = write_fixture(
        tmp_path / "two_cube.e57",
        [
            (Pose((0.0, 0.0, 1.5), (1, 0, 0, 0)), pts, np.zeros(50, dtype=np.int64)),
            (Pose((20.0, 0.0, 1.5), (1, 0, 0, 0)), pts, np.zeros(50, dtype=np.int64)),
        ],
        images=[(f, 1) for f in order],  # type: ignore[misc]
    )
    pkg = _new_pkg(tmp_path)
    stations = build_panos(fx.path, pkg, levels=(32,))["stations"]
    inv = read_inventory(fx.path)
    assert [s["id"] for s in stations] == ["S001", "S002"]
    for s in stations:
        psid = s["pointSourceId"]
        assert psid == int(s["id"][1:])
        assert inv.scans[psid - 1].guid == s["scanGuid"] == fx.scan_guids[psid - 1]
    assert stations[0]["panorama"] is None
    assert stations[1]["panorama"] is not None
    assert stations[1]["pose"]["translation"] == [20.0, 0.0, 1.5]


def test_write_stations_passes_adapter_point_source_id_through(tmp_path):
    pkg = _new_pkg(tmp_path)
    src = StationSource(
        scan_guid="s",
        name="Fremdquelle",
        pose=Pose((1.0, 2.0, 3.0), (1.0, 0.0, 0.0, 0.0)),
        images=[],
        faces={},
        read_image=lambda im: Image.new("RGB", (64, 64)),
    )
    assert write_stations(pkg, [src], (32,))["stations"][0]["pointSourceId"] is None
    src.point_source_id = 7
    assert write_stations(pkg, [src], (32,))["stations"][0]["pointSourceId"] == 7


@pytest.mark.docker
def test_points_with_station_point_source_id_are_that_scans_points(tmp_path):
    """Kette convert → panos: die Punkte mit der PointSourceId eines Standpunkts sind genau
    die Punkte seines Scans (Weltlage), nicht die eines anderen."""
    import laspy
    from scipy.spatial import cKDTree

    from scanview_import.convert import convert
    from scanview_import.geometry import apply_pose

    order = ["nz", "px", "pz", "ny", "nx", "py"]
    rng = np.random.default_rng(4)
    poses = [Pose((0.0, 0.0, 1.5), (1, 0, 0, 0)), Pose((20.0, 0.0, 1.5), (1, 0, 0, 0))]
    local = [rng.uniform(-2, 2, size=(80, 3)), rng.uniform(-2, 2, size=(60, 3))]
    fx = write_fixture(
        tmp_path / "chain.e57",
        [(poses[i], local[i], np.zeros(len(local[i]), dtype=np.int64)) for i in range(2)],
        images=[(f, 0) for f in order] + [(f, 1) for f in order],  # type: ignore[operator]
    )
    pkg = tmp_path / "pkg"
    convert(fx.path, pkg, crs=None)
    stations = build_panos(fx.path, pkg, levels=(32,))["stations"]
    las = laspy.read(pkg / "cloud.copc.laz")
    xyz = np.column_stack([las.x, las.y, las.z])
    ids = np.asarray(las.point_source_id)
    for i, st in enumerate(stations):
        own = xyz[ids == st["pointSourceId"]]
        exp = apply_pose(local[i], poses[i])
        assert len(own) == len(exp)
        d_exp, _ = cKDTree(own).query(exp)
        d_own, _ = cKDTree(exp).query(own)
        assert max(d_exp.max(), d_own.max()) <= 0.0015
