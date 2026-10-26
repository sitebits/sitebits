"""Mobilstufe (mobile.glb): beim Import und nachträglich aus model.glb (Befehl mobilstufe)."""

import hashlib
import io
import json
import os

import numpy as np
import pytest
from PIL import Image

from scanview_import import mesh_mobil
from scanview_import.mesh_convert import convert_mesh
from scanview_import.mesh_mobil import mobilstufe
from scanview_import.mesh_source import read_mesh
from scanview_import.mesh_verify import mobil_warnungen, verify_mesh


def sphere(path, nlat=24, nlon=48, radius=0.1):
    lines = []
    for j in range(nlat + 1):
        theta = np.pi * j / nlat
        for i in range(nlon):
            phi = 2 * np.pi * i / nlon
            lines.append(
                f"v {radius * np.sin(theta) * np.cos(phi)} "
                f"{radius * np.sin(theta) * np.sin(phi)} {radius * np.cos(theta)}"
            )
    for j in range(nlat):
        for i in range(nlon):
            a = j * nlon + i + 1
            b = j * nlon + (i + 1) % nlon + 1
            lines.extend([f"f {a} {b} {a + nlon}", f"f {b} {b + nlon} {a + nlon}"])
    path.write_text("\n".join(lines) + "\n")
    return path


def textured_cube(tmp_path, width=3072, height=96):
    """Würfel mit breiter Textur: zeigt die 2048-px-Grenze ohne große Bilder."""
    from gltf_fixture import box_source_mesh_data

    vertices, faces, uv = box_source_mesh_data()
    p = tmp_path / "cube.obj"
    lines = ["mtllib cube.mtl", "usemtl stone"]
    lines += ["v " + " ".join(str(x) for x in v) for v in vertices]
    lines += ["vt " + " ".join(str(x) for x in u) for face_uv in uv for u in face_uv]
    for i, face in enumerate(faces):
        lines.append("f " + " ".join(f"{ix + 1}/{i * 3 + j + 1}" for j, ix in enumerate(face)))
    p.write_text("\n".join(lines) + "\n")
    (tmp_path / "cube.mtl").write_text("newmtl stone\nmap_Kd wide.png\n")
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    arr[..., 0] = np.linspace(0, 255, width, dtype=np.uint8)[None, :]
    arr[..., 1] = np.linspace(255, 0, height, dtype=np.uint8)[:, None]
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(buf, format="PNG")
    (tmp_path / "wide.png").write_bytes(buf.getvalue())
    return p


def zwei_materialien(path, n=40, zahn=0.0):
    """Gewellte Fläche aus zwei Materialhälften, die sich die mittlere Vertexspalte teilen (wie
    die Texturseiten eines Photogrammetrie-Meshes). ``zahn`` zieht die Ecke bei x-max hinaus."""
    (path.parent / "zwei.mtl").write_text("newmtl a\nnewmtl b\n")
    lines = ["mtllib zwei.mtl"]
    for j in range(n + 1):
        for i in range(n + 1):
            x, y = i / n * 10, j / n * 10
            if i == n and j == n // 2:
                x += zahn
            lines.append(f"v {x} {y} {0.05 * np.sin(x * 1.7) * np.cos(y * 1.3)}")
    for name, spalten in (("a", range(n // 2)), ("b", range(n // 2, n))):
        lines.append(f"usemtl {name}")
        for j in range(n):
            for i in spalten:
                a = j * (n + 1) + i + 1
                lines += [f"f {a} {a + 1} {a + n + 1}", f"f {a + 1} {a + n + 2} {a + n + 1}"]
    path.write_text("\n".join(lines) + "\n")
    return path


def offene_kanten(mesh):
    e = np.sort(np.r_[mesh.triangles[:, [0, 1]], mesh.triangles[:, [1, 2]],
                      mesh.triangles[:, [2, 0]]], axis=1)
    kanten, anzahl = np.unique(e, axis=0, return_counts=True)
    return kanten[anzahl == 1]


def test_glb_primitive_teilen_vertices(tmp_path):
    """Regression: je Material ein Primitive auf einem Vertexpuffer wurde je Primitive komplett
    kopiert (20 Materialien = 20-fache Vertexzahl, ~6 GB beim Lesen eines echten Pakets) und die
    Nähte zwischen den Materialien zerfielen in offene Ränder."""
    import pygltflib as g

    src = zwei_materialien(tmp_path / "zwei.obj")
    pkg = tmp_path / "pkg"
    layer = mesh_layer(convert_mesh(src, pkg, unit="m", dreiecke=0, mobil_dreiecke=0))
    doc = g.GLTF2().load(str(pkg / layer["url"]))
    assert len(doc.meshes[0].primitives) == 2
    model = read_mesh(pkg / layer["url"])
    position = doc.meshes[0].primitives[0].attributes.POSITION
    assert len(model.positions) == doc.accessors[position].count
    # Offene Kanten nur am Außenrand, keine auf der Materialgrenze x = 5.
    origin = np.asarray(json.loads((pkg / "project.json").read_text())["frame"]["originWorld"])
    x = model.positions[offene_kanten(model)][:, :, 0] + origin[0]
    assert not np.any(np.all(np.isclose(x, 5.0, atol=1e-3), axis=1))


def test_mobilstufe_naht_zwischen_materialien_bleibt_geschlossen(tmp_path):
    """Regression: aus model.glb abgeleitet, riss die Stufe an den Materialgrenzen auf (Lücken bis
    in den Meterbereich, Ausdehnung verloren) — die Grenze war nach dem Lesen ein offener Rand."""
    src = zwei_materialien(tmp_path / "zwei.obj", zahn=0.8)
    quelle = tmp_path / "quelle"
    convert_mesh(src, quelle, unit="m", dreiecke=0, mobil_dreiecke=0)
    m = mobilstufe(quelle, tmp_path / "ziel", 1600)
    mobile = read_mesh(tmp_path / "ziel" / mesh_layer(m)["mobileUrl"])
    x = mobile.positions[offene_kanten(mobile)][:, :, 0] + m["frame"]["originWorld"][0]
    assert not np.any(np.all(np.isclose(x, 5.0, atol=1e-3), axis=1))
    stage = json.loads((tmp_path / "ziel/layers/mesh/conversion-report.json").read_text())[
        "stages"]["mobile"]
    # Gesperrter Außenrand: die Zahnspitze (Ausdehnung) bleibt exakt erhalten.
    assert stage["boundsMaxMm"] < 1
    assert stage["sourceToStage"]["maxMm"] < 100


def tree_hashes(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def mesh_layer(manifest):
    return next(lay for lay in manifest["layers"] if lay["kind"] == "mesh")


def test_import_mobilstufe_vertrag(tmp_path):
    src = sphere(tmp_path / "s.obj")
    pkg = tmp_path / "pkg"
    m = convert_mesh(src, pkg, unit="m", dreiecke=1000, vorschau_dreiecke=300, mobil_dreiecke=500)
    layer = mesh_layer(m)
    assert layer["mobileUrl"] == "layers/mesh/mobile.glb"
    assert layer["mobileSizeBytes"] == (pkg / "layers/mesh/mobile.glb").stat().st_size
    assert abs(layer["mobileTriangleCount"] / 500 - 1) <= 0.05
    assert layer["mobileTriangleCount"] == layer["stufen"]["mobile"]["erreicht"]
    assert layer["stufen"]["mobile"]["zielErreicht"] is True
    assert layer["positionStepsM"]["mobile"] > 0
    assert set(layer["degenerateRemoval"]["mobile"]) == {"simplification", "quantization"}
    assert layer["textures"]["stages"]["mobile"] == []
    assert layer["sourceOptions"]["mobilDreiecke"] == 500
    assert layer["verify"]["ok"] is True
    # Gleicher Paketursprung wie model.glb: Positionen liegen auf der Kugel.
    mobile = read_mesh(pkg / layer["mobileUrl"])
    r = np.linalg.norm(mobile.positions + m["frame"]["originWorld"], axis=1)
    assert np.abs(r - 0.1).max() < 0.002
    report = json.loads((pkg / layer["verify"]["report"]).read_text())
    stage = report["stages"]["mobile"]
    assert stage["ok"] and stage["targetOk"]
    assert stage["stageToSource"]["p95Mm"] < 10
    assert report["warnungen"] == []
    again = verify_mesh(src, pkg)
    assert again["ok"] and "mobile" in again["stages"]


def test_import_ohne_mobilstufe(tmp_path):
    src = sphere(tmp_path / "s.obj")
    pkg = tmp_path / "pkg"
    layer = mesh_layer(
        convert_mesh(src, pkg, unit="m", dreiecke=1000, vorschau_dreiecke=300, mobil_dreiecke=0)
    )
    assert not any(k.startswith("mobile") for k in layer)
    for feld in ("stufen", "positionStepsM", "degenerateRemoval"):
        assert "mobile" not in layer[feld]
    assert "mobile" not in layer["textures"]["stages"]
    assert not (pkg / "layers/mesh/mobile.glb").exists()
    assert "mobile" not in verify_mesh(src, pkg)["stages"]


def test_mobilstufe_nie_groesser_als_vollstufe(tmp_path):
    src = sphere(tmp_path / "s.obj")
    layer = mesh_layer(
        convert_mesh(src, tmp_path / "pkg", unit="m", dreiecke=600, vorschau_dreiecke=300,
                     mobil_dreiecke=5000)
    )
    assert layer["sourceOptions"]["mobilDreiecke"] == 600
    assert layer["mobileTriangleCount"] <= layer["triangleCount"] * 1.05


def test_negatives_mobilziel_abgelehnt(tmp_path):
    with pytest.raises(ValueError):
        convert_mesh(sphere(tmp_path / "s.obj"), tmp_path / "pkg", unit="m", mobil_dreiecke=-1)


def test_warnung_ueber_100_mib():
    assert mobil_warnungen({"model": {"sizeBytes": 500 * 2**20}}) == []
    assert mobil_warnungen({"mobile": {"sizeBytes": 100 * 2**20}}) == []
    assert mobil_warnungen({"mobile": {"sizeBytes": 100 * 2**20 + 1}})


def ohne_mobil(tmp_path):
    src = sphere(tmp_path / "s.obj")
    pkg = tmp_path / "quelle"
    convert_mesh(src, pkg, unit="m", dreiecke=1000, vorschau_dreiecke=300, mobil_dreiecke=0)
    # Binärdatei (wie Punktwolke/Bilder): wird verlinkt, nicht kopiert.
    (pkg / "gross.bin").write_bytes(os.urandom(4096))
    return src, pkg


def test_mobilstufe_nachtraeglich_quelle_unberuehrt(tmp_path):
    src, quelle = ohne_mobil(tmp_path)
    vorher = tree_hashes(quelle)
    vorher_rev = json.loads((quelle / "project.json").read_text())["revision"]
    ziel = tmp_path / "ziel"
    m = mobilstufe(quelle, ziel, 500)

    assert tree_hashes(quelle) == vorher
    assert not (quelle / "layers/mesh/mobile.glb").exists()
    assert m["revision"] == vorher_rev + 1
    assert json.loads((ziel / "project.json").read_text()) == m
    # Unverändert und groß: dieselbe Inode. Geändert oder JSON: eigene Inode.
    assert (ziel / "gross.bin").stat().st_ino == (quelle / "gross.bin").stat().st_ino
    for name in ("project.json", "layers/mesh/conversion-report.json"):
        assert (ziel / name).stat().st_ino != (quelle / name).stat().st_ino
    assert (quelle / "project.json").stat().st_nlink == 1
    # Restliche Paketdateien unverändert übernommen.
    nachher = tree_hashes(ziel)
    for name, h in vorher.items():
        if name not in ("project.json", "layers/mesh/conversion-report.json"):
            assert nachher[name] == h, name

    layer = mesh_layer(m)
    assert layer["mobileUrl"] == "layers/mesh/mobile.glb"
    assert layer["mobileSizeBytes"] == (ziel / "layers/mesh/mobile.glb").stat().st_size
    assert abs(layer["mobileTriangleCount"] / 500 - 1) <= 0.05
    assert layer["sourceOptions"]["mobilDreiecke"] == 500
    assert layer["stufen"]["mobile"]["abgeleitetAus"] == "layers/mesh/model.glb"
    for feld in ("positionStepsM", "degenerateRemoval"):
        assert "mobile" in layer[feld]
    assert layer["textures"]["stages"]["mobile"] == []

    report = json.loads((ziel / "layers/mesh/conversion-report.json").read_text())
    stage = report["stages"]["mobile"]
    assert stage["ok"] and stage["referenz"] == "layers/mesh/model.glb"
    assert {"p50Mm", "p95Mm"} <= set(stage["stageToSource"])
    assert stage["stageToSource"]["p95Mm"] < 10
    assert report["mobilstufe"]["referenz"] == "layers/mesh/model.glb"
    assert "stages" in report and "model" in report["stages"]
    assert not list(tmp_path.glob(".ziel-mobil-*"))
    # Mit Originalquelle lässt sich das abgeleitete Paket voll nachprüfen.
    assert verify_mesh(src, ziel)["ok"]


def test_mobilstufe_ziel_existiert(tmp_path):
    _, quelle = ohne_mobil(tmp_path)
    (tmp_path / "ziel").mkdir()
    with pytest.raises(FileExistsError):
        mobilstufe(quelle, tmp_path / "ziel")


def test_mobilstufe_doppelt_abgelehnt(tmp_path):
    src = sphere(tmp_path / "s.obj")
    quelle = tmp_path / "quelle"
    convert_mesh(src, quelle, unit="m", dreiecke=1000, vorschau_dreiecke=300, mobil_dreiecke=500)
    with pytest.raises(ValueError, match="bereits"):
        mobilstufe(quelle, tmp_path / "ziel")
    assert not (tmp_path / "ziel").exists()


def test_mobilstufe_fehlschlag_hinterlaesst_nichts(tmp_path, monkeypatch):
    _, quelle = ohne_mobil(tmp_path)
    vorher = tree_hashes(quelle)
    real = mesh_mobil.measure_stage

    def rot(*args, **kwargs):
        return real(*args, **kwargs) | {"ok": False}

    monkeypatch.setattr(mesh_mobil, "measure_stage", rot)
    with pytest.raises(ValueError, match="Prüfung fehlgeschlagen"):
        mobilstufe(quelle, tmp_path / "ziel", 500)
    assert not (tmp_path / "ziel").exists()
    assert not list(tmp_path.glob(".ziel-mobil-*"))
    assert tree_hashes(quelle) == vorher
    assert (quelle / "gross.bin").stat().st_nlink == 1


def test_mobilstufe_aus_symlink_paket(tmp_path):
    _, quelle = ohne_mobil(tmp_path)
    verweise = tmp_path / "verweise"
    verweise.mkdir()
    for eintrag in quelle.iterdir():
        if eintrag.name != "project.json":
            (verweise / eintrag.name).symlink_to(eintrag)
    (verweise / "project.json").write_bytes((quelle / "project.json").read_bytes())
    ziel = tmp_path / "ziel"
    mobilstufe(verweise, ziel, 500)
    assert not any(p.is_symlink() for p in ziel.rglob("*"))
    assert (ziel / "gross.bin").stat().st_ino == (quelle / "gross.bin").stat().st_ino
    assert (ziel / "layers/mesh/model.glb").read_bytes() == (
        quelle / "layers/mesh/model.glb"
    ).read_bytes()


def test_cli_mobilstufe(tmp_path, capsys):
    from scanview_import.cli import main

    _, quelle = ohne_mobil(tmp_path)
    ziel = tmp_path / "ziel"
    assert main(["mobilstufe", str(quelle), str(ziel), "--mobil-dreiecke", "500"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mobileUrl"] == "layers/mesh/mobile.glb" and out["revision"] == 2
    assert main(["mobilstufe", str(quelle), str(ziel)]) == 1


# Echte KTX2-Kodierung/Dekodierung braucht Docker; separat im Docker-Gate ausführen.
@pytest.mark.docker
def test_texturierte_mobilstufe_etc1s_2048(tmp_path):
    """Echter KTX-Container: Import mit Mobilstufe und nachträgliche Ableitung aus UASTC."""
    from scanview_import.mesh_textures import ktx_info

    src = textured_cube(tmp_path)
    pkg = tmp_path / "pkg"
    layer = mesh_layer(convert_mesh(src, pkg, unit="m", dreiecke=0))
    mobil = layer["textures"]["stages"]["mobile"]
    assert [(r["width"], r["encoding"]) for r in mobil] == [(2048, "ktx2-etc1s")]
    assert layer["textures"]["stages"]["model"][0]["width"] == 3072
    textures = [t for t in read_mesh(pkg / layer["mobileUrl"]).textures if t is not None]
    assert ktx_info(textures[0])["encoding"] == "ktx2-etc1s"
    report = json.loads((pkg / layer["verify"]["report"]).read_text())
    assert report["stages"]["mobile"]["textures"]["ok"]
    assert report["stages"]["mobile"]["textures"]["meanAbsDeltaRGB"][0] < 10

    quelle = tmp_path / "ohne"
    convert_mesh(src, quelle, unit="m", dreiecke=0, mobil_dreiecke=0)
    m = mobilstufe(quelle, tmp_path / "abgeleitet")
    abgeleitet = mesh_layer(m)
    reports = abgeleitet["textures"]["stages"]["mobile"]
    assert [(r["width"], r["encoding"]) for r in reports] == [(2048, "ktx2-etc1s")]
    rep = json.loads((tmp_path / "abgeleitet/layers/mesh/conversion-report.json").read_text())
    assert rep["stages"]["mobile"]["textures"]["ok"]
    assert rep["stages"]["mobile"]["textures"]["meanAbsDeltaRGB"][0] < 10
