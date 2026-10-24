"""CLI-Vertrag: CRS-Abbruch vor Schreiben, vorhandene Pakete, erreichbare Stufen."""

import json

import pytest

from scanview_import.cli import main
from tests.test_kameras import camera_file


def package(tmp_path, *, schema=2, wkt="EPSG:32632", datum="ellipsoidisch"):
    pkg = tmp_path / "paket"
    pkg.mkdir()
    crs = {"wkt": wkt, "verticalDatum": datum}
    manifest = (
        {
            "schemaVersion": 2,
            "frame": {"crs": crs, "originWorld": [100, 200, 0], "unit": "m"},
            "layers": [{"id": "punkte", "kind": "pointcloud"}],
        }
        if schema == 2
        else {
            "schemaVersion": 1,
            "crs": dict(crs, unit="m"),
            "originWorld": [100, 200, 0],
            "cloud": {},
        }
    )
    (pkg / "project.json").write_text(json.dumps(manifest))
    return pkg


@pytest.mark.parametrize("schema", [1, 2])
def test_cli_import(schema, tmp_path, capsys):
    source = camera_file(tmp_path)
    pkg = package(tmp_path, schema=schema)
    assert (
        main(["panos-kameras", "--kameras", str(source), str(pkg), "--levels", "16,32,64"]) == 0
    )
    out = json.loads((pkg / "stations.json").read_text())
    station = out["stations"][0]
    assert station["pose"]["translation"] == [100, 200, 30]  # kein zweiter Ursprung-Abzug
    assert station["panorama"]["levels"] == [16, 32]
    assert not list(pkg.rglob("64.jpg"))
    report = json.loads(capsys.readouterr().out)
    assert report["exported"] == ["Luft / Kamera A"]
    assert report["skipped"] == [{"label": "Kamera B", "reason": "nicht ausgerichtet"}]


@pytest.mark.parametrize(
    "problem,message",
    [
        ("epsg", "CRS"),
        ("height", "Höhensystem"),
        ("unknown_height", "Höhensystem"),
        ("empty", "keine Kamera"),
        ("missing_package", "project.json"),
        ("levels", "Stufen"),
        ("no_levels", "Stufe"),
        ("wkt_nested", "CRS"),
    ],
)
def test_cli_rejects_before_writing(problem, message, tmp_path, capsys):
    source = camera_file(tmp_path)
    pkg = package(
        tmp_path,
        wkt="EPSG:32633"
        if problem == "epsg"
        else 'PROJCRS["Test",BASEGEOGCRS["X",ID["EPSG",32632]]]'
        if problem == "wkt_nested"
        else "EPSG:32632",
        datum="anderes"
        if problem == "height"
        else None
        if problem == "unknown_height"
        else "ellipsoidisch",
    )
    if problem == "empty":
        data = json.loads(source.read_text())
        data["cameras"] = []
        source.write_text(json.dumps(data))
    if problem == "missing_package":
        pkg = tmp_path / "nicht-vorhanden"
    before = (pkg / "project.json").read_bytes() if pkg.exists() else None
    args = ["panos-kameras", "--kameras", str(source), str(pkg)]
    args += ["--levels", "0,32"] if problem == "levels" else ["--levels", "64"]
    assert main(args) == 1
    assert message in capsys.readouterr().err
    assert not (pkg / "stations.json").exists()
    if before is not None:
        assert (pkg / "project.json").read_bytes() == before


def test_explicit_unknown_height_and_wkt_epsg(tmp_path):
    source = camera_file(tmp_path)
    pkg = package(
        tmp_path,
        datum=None,
        wkt='PROJCRS["Synthetisch",BASEGEOGCRS["Basis",ID["EPSG",4326]],ID["EPSG",32632]]',
    )
    assert (
        main(
            [
                "panos-kameras",
                "--kameras",
                str(source),
                str(pkg),
                "--levels",
                "32",
                "--hoehensystem",
                "ellipsoidisch",
            ]
        )
        == 0
    )
    manifest = json.loads((pkg / "project.json").read_text())
    assert manifest["frame"]["crs"]["verticalDatum"] == "ellipsoidal-etrs89"
    assert manifest["frame"]["originWorld"] == [100, 200, 0]


def test_mixed_sizes_skipped_camera_is_reported(tmp_path, capsys):
    from PIL import Image

    source = camera_file(tmp_path)
    data = json.loads(source.read_text())
    small = tmp_path / "klein.png"
    Image.new("RGB", (32, 16), "green").save(small)
    data["cameras"].append(
        dict(data["cameras"][0], label="Kleine Kamera", imagePath=str(small), width=32, height=16)
    )
    source.write_text(json.dumps(data))
    pkg = package(tmp_path)
    assert main(["panos-kameras", "--kameras", str(source), str(pkg), "--levels", "32"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["exported"] == ["Luft / Kamera A"]
    assert report["skipped"][-1] == {
        "label": "Luft / Kleine Kamera",
        "reason": "keine erreichbare Stufe",
    }
    assert len(json.loads((pkg / "stations.json").read_text())["stations"]) == 1
