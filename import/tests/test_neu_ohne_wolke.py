"""Neues Paket ohne Punktwolke: Splats (SPZ/PLY/SPLAT) und Mesh (GLB/PLY ohne Textur).

Alles synthetisch. Der Mesh-Weg darf keinen Container starten: `subprocess.run` ist so ersetzt,
dass jeder Aufruf scheitert (der Import kennt sonst nur `docker` für Texturen)."""

from __future__ import annotations

import io
import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pygltflib as g
import pytest
from PIL import Image

from scanview_import.cli import main
from scanview_import.neu_ohne_wolke import KEINE_WOLKE, LOKAL, verify_splats
from scanview_import.splat_source import Gaussians, read_gaussians
from scanview_import.splat_spz import write_spz

RNG = np.random.default_rng(7)


def gaussians(n: int = 3000, versatz: tuple[float, float, float] = (0, 0, 0)) -> Gaussians:
    return Gaussians(
        RNG.uniform(-3, 3, (n, 3)) + np.asarray(versatz),
        np.full((n, 3), 0.02),
        np.tile([0.0, 0, 0, 1], (n, 1)),
        np.full(n, 0.8),
        RNG.uniform(0.1, 0.9, (n, 3)),
    )


def spz(path: Path, n: int = 3000) -> Path:
    write_spz(path, gaussians(n), np.zeros(3))
    return path


def splat_datei(path: Path, n: int) -> Path:
    dtype = np.dtype([("p", "<f4", (3,)), ("s", "<f4", (3,)), ("c", "u1", (4,)),
                      ("q", "u1", (4,))])
    r = np.zeros(n, dtype=dtype)
    r["p"] = RNG.uniform(-1, 1, (n, 3))
    r["s"] = 0.01
    r["c"] = [200, 100, 50, 230]
    r["q"] = [255, 128, 128, 128]
    r.tofile(path)
    return path


def glb(path: Path, *, textur: bool = False, uri: bool = False) -> Path:
    """Würfelartiger Körper (12 Dreiecke) mit Vertexfarben; optional mit eingebetteter Textur
    oder mit externem Puffer-Verweis."""
    pos = np.array([[x, y, z] for x in (0, 2) for y in (0, 1) for z in (0, 3)], dtype="<f4")
    idx = np.array([0, 1, 3, 0, 3, 2, 4, 6, 7, 4, 7, 5, 0, 4, 5, 0, 5, 1, 2, 3, 7, 2, 7, 6,
                    0, 2, 6, 0, 6, 4, 1, 5, 7, 1, 7, 3], dtype="<u4")
    farbe = np.tile(np.array([0.8, 0.3, 0.2, 1.0], dtype="<f4"), (8, 1))
    uv = np.tile(np.array([0.5, 0.5], dtype="<f4"), (8, 1))
    teile = [pos.tobytes(), idx.tobytes(), farbe.tobytes(), uv.tobytes()]
    png = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 200, 30)).save(png, format="PNG")
    if textur:
        teile.append(png.getvalue() + b"\0" * (-len(png.getvalue()) % 4))
    blob = b"".join(teile)
    views, off = [], 0
    for t in teile:
        views.append(g.BufferView(buffer=0, byteOffset=off, byteLength=len(t)))
        off += len(t)
    attrs = g.Attributes(POSITION=0, COLOR_0=2)
    if textur:
        attrs.TEXCOORD_0 = 3
    gltf = g.GLTF2(
        asset=g.Asset(version="2.0"), scene=0, scenes=[g.Scene(nodes=[0])], nodes=[g.Node(mesh=0)],
        meshes=[g.Mesh(primitives=[g.Primitive(attributes=attrs, indices=1, material=0)])],
        materials=[g.Material(pbrMetallicRoughness=g.PbrMetallicRoughness(
            baseColorTexture=g.TextureInfo(index=0) if textur else None))],
        buffers=[g.Buffer(byteLength=len(blob))],
        bufferViews=views,
        accessors=[
            g.Accessor(bufferView=0, componentType=g.FLOAT, count=8, type="VEC3",
                       min=pos.min(0).tolist(), max=pos.max(0).tolist()),
            g.Accessor(bufferView=1, componentType=g.UNSIGNED_INT, count=len(idx), type="SCALAR"),
            g.Accessor(bufferView=2, componentType=g.FLOAT, count=8, type="VEC4"),
            g.Accessor(bufferView=3, componentType=g.FLOAT, count=8, type="VEC2"),
        ],
        images=[g.Image(bufferView=4, mimeType="image/png")] if textur else [],
        textures=[g.Texture(source=0)] if textur else [],
    )
    if uri:
        # pygltflib bettet beim Speichern ein; den Verweis daher von Hand in den JSON-Teil.
        (path.parent / "extern.bin").write_bytes(blob)
        doc = json.loads(gltf.gltf_to_json())
        doc["buffers"] = [{"byteLength": len(blob), "uri": "extern.bin"}]
        roh = json.dumps(doc).encode()
        roh += b" " * (-len(roh) % 4)
        path.write_bytes(struct.pack("<4sII", b"glTF", 2, 20 + len(roh))
                         + struct.pack("<I4s", len(roh), b"JSON") + roh)
        return path
    gltf.set_binary_blob(blob)
    gltf.save_binary(str(path))
    return path


@pytest.fixture
def kein_container(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Jeder Prozessstart im Import scheitert laut — der Weg ohne Wolke braucht keinen."""
    import subprocess

    aufrufe: list[Any] = []

    def verboten(*args: Any, **kwargs: Any) -> Any:
        aufrufe.append(args)
        raise AssertionError(f"Prozessstart im Import ohne Container: {args!r}")

    monkeypatch.setattr(subprocess, "run", verboten)
    monkeypatch.setattr(subprocess, "Popen", verboten)
    monkeypatch.setenv("PATH", "/nonexistent")
    return aufrufe


def lauf(*argv: str | Path) -> int:
    return main([str(a) for a in argv])


def letzte_zeile(capsys: pytest.CaptureFixture[str]) -> str:
    return capsys.readouterr().err.strip().splitlines()[-1]


def test_spz_ohne_wolke_und_verify(tmp_path: Path, kein_container: list[Any]) -> None:
    quelle = spz(tmp_path / "quelle.spz")
    pkg = tmp_path / "neu-splats"
    assert lauf("neu-ohne-wolke", quelle, pkg, "--art", "splats", "--titel", "Sichtprobe") == 0
    m = json.loads((pkg / "project.json").read_text())
    assert m["schemaVersion"] == 2 and m["projectId"] == "neu-splats"
    assert m["title"] == "Sichtprobe"
    assert m["frame"]["crs"] == {"wkt": None, "verticalDatum": None, "declaredBy": LOKAL}
    [layer] = m["layers"]
    assert layer["kind"] == "splats" and layer["format"] == "spz"
    assert layer["frameCheck"] == KEINE_WOLKE
    assert layer["splatCount"] == 3000 and layer["chunkCount"] == 1
    assert m["initialView"]["layers"] == ["splats"]
    assert (pkg / layer["url"]).is_file()
    assert lauf("verify", quelle, pkg) == 0
    m = json.loads((pkg / "project.json").read_text())
    assert m["layers"][0]["verify"]["ok"] is True
    rep = json.loads((pkg / "layers/splats/conversion-report.json").read_text())
    assert rep["ok"] and rep["positions"]["maxM"] <= rep["positions"]["toleranceM"]
    assert kein_container == []
    # nichts liegt neben dem Paket herum
    assert sorted(p.name for p in tmp_path.iterdir()) == ["neu-splats", "quelle.spz"]


def test_verify_erkennt_manipulierte_ausgabe(tmp_path: Path) -> None:
    quelle = spz(tmp_path / "quelle.spz")
    pkg = tmp_path / "p"
    assert lauf("neu-ohne-wolke", quelle, pkg, "--art", "splats") == 0
    m = json.loads((pkg / "project.json").read_text())
    out = read_gaussians(pkg / "layers/splats/model.spz")
    verschoben = Gaussians(out.centers + [0.05, 0, 0], out.scales, out.quaternions,
                           out.opacities, out.colors)
    write_spz(pkg / "layers/splats/model.spz", verschoben, np.zeros(3))
    assert lauf("verify", quelle, pkg) == 1
    rep = verify_splats(quelle, pkg)
    assert rep["positions"]["ok"] is False and rep["source"]["ok"] is True
    # Andere Quelle (gleiche Anzahl) → Prüfsumme passt nicht
    andere = spz(tmp_path / "andere.spz")
    assert verify_splats(andere, pkg)["source"]["ok"] is False
    assert m["layers"][0]["sizeBytes"] > 0


def test_ply_splats_mit_crs_und_hoehe(tmp_path: Path) -> None:
    from scanview_import.splat_source import write_gaussians

    welt = gaussians(2500, versatz=(400000.0, 5800000.0, 40.0))
    quelle = tmp_path / "welt.ply"
    write_gaussians(quelle, welt, np.zeros(3))
    pkg = tmp_path / "welt"
    assert lauf("neu-ohne-wolke", quelle, pkg, "--art", "splats", "--crs", "EPSG:25833",
                "--quell-hoehensystem", "dhhn2016") == 0
    m = json.loads((pkg / "project.json").read_text())
    assert m["frame"]["crs"]["wkt"] == "EPSG:25833"
    assert m["frame"]["crs"]["declaredBy"] == "cli"
    assert m["frame"]["crs"]["verticalDatum"] == "dhhn2016"
    assert abs(m["frame"]["originWorld"][0] - 400000) < 10
    assert lauf("verify", quelle, pkg) == 0


def test_splat_format_ohne_kennung(tmp_path: Path) -> None:
    quelle = splat_datei(tmp_path / "a.splat", 2000)
    assert lauf("neu-ohne-wolke", quelle, tmp_path / "s", "--art", "splats") == 0
    assert lauf("verify", quelle, tmp_path / "s") == 0


def test_zu_viele_gaussians(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    quelle = splat_datei(tmp_path / "gross.splat", 1_000_001)
    assert lauf("neu-ohne-wolke", quelle, tmp_path / "g", "--art", "splats") == 2
    zeile = letzte_zeile(capsys)
    assert "1.000.000" in zeile and "LOD" in zeile
    assert str(tmp_path) not in zeile
    assert not (tmp_path / "g").exists()


def test_hoehe_ohne_crs_abgelehnt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    quelle = spz(tmp_path / "q.spz")
    assert lauf("neu-ohne-wolke", quelle, tmp_path / "x", "--art", "splats",
                "--quell-hoehensystem", "dhhn2016") == 2
    assert "Koordinatensystem" in letzte_zeile(capsys)


def test_ziel_existiert(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    quelle = spz(tmp_path / "q.spz")
    (tmp_path / "da").mkdir()
    assert lauf("neu-ohne-wolke", quelle, tmp_path / "da", "--art", "splats") == 2
    assert letzte_zeile(capsys) == "Abgelehnt: Zielpaket existiert bereits"


def test_falsche_endung_und_kaputte_datei(tmp_path: Path,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    obj = tmp_path / "m.obj"
    obj.write_text("v 0 0 0\n")
    assert lauf("neu-ohne-wolke", obj, tmp_path / "o", "--art", "mesh") == 2
    assert "glb" in letzte_zeile(capsys)
    kaputt = tmp_path / "k.spz"
    kaputt.write_bytes(b"\x1f\x8b\x08\x00kaputt")
    assert lauf("neu-ohne-wolke", kaputt, tmp_path / "k", "--art", "splats") == 2
    assert str(tmp_path) not in letzte_zeile(capsys)


def test_glb_ohne_textur(tmp_path: Path, kein_container: list[Any]) -> None:
    quelle = glb(tmp_path / "koerper.glb")
    pkg = tmp_path / "koerper"
    assert lauf("neu-ohne-wolke", quelle, pkg, "--art", "mesh", "--titel", "Körper") == 0
    m = json.loads((pkg / "project.json").read_text())
    assert m["title"] == "Körper"
    assert m["frame"]["crs"]["declaredBy"] == LOKAL
    [layer] = m["layers"]
    assert layer["kind"] == "mesh" and layer["verify"]["ok"] is True
    assert layer["provenance"]["scaleVerified"] is False
    assert layer["textures"]["count"] == 0
    assert not any(lay["kind"] == "pointcloud" for lay in m["layers"])
    assert lauf("verify", quelle, pkg) == 0
    assert kein_container == []


def test_ply_mesh_ohne_textur(tmp_path: Path, kein_container: list[Any]) -> None:
    quelle = tmp_path / "flaeche.ply"
    verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0.5)]
    body = b"".join(struct.pack("<fffBBB", *v, 200, 50, 50) for v in verts)
    body += b"".join(struct.pack("<BIII", 3, *f) for f in ((0, 1, 2), (1, 3, 2)))
    quelle.write_bytes(
        b"ply\nformat binary_little_endian 1.0\nelement vertex 4\nproperty float x\n"
        b"property float y\nproperty float z\nproperty uchar red\nproperty uchar green\n"
        b"property uchar blue\nelement face 2\nproperty list uchar uint vertex_indices\n"
        b"end_header\n" + body)
    pkg = tmp_path / "flaeche"
    assert lauf("neu-ohne-wolke", quelle, pkg, "--art", "mesh") == 0
    m = json.loads((pkg / "project.json").read_text())
    assert m["layers"][0]["provenance"]["scaleSource"] == "unit:cli"
    assert kein_container == []


def test_texturiertes_glb_abgelehnt(tmp_path: Path, kein_container: list[Any],
                                    capsys: pytest.CaptureFixture[str]) -> None:
    quelle = glb(tmp_path / "bunt.glb", textur=True)
    assert lauf("neu-ohne-wolke", quelle, tmp_path / "b", "--art", "mesh") == 2
    zeile = letzte_zeile(capsys)
    assert "Texturierte Meshes" in zeile and str(tmp_path) not in zeile
    assert not (tmp_path / "b").exists() and kein_container == []


def test_glb_mit_externem_verweis_abgelehnt(tmp_path: Path,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    quelle = glb(tmp_path / "verweis.glb", uri=True)
    assert lauf("neu-ohne-wolke", quelle, tmp_path / "v", "--art", "mesh") == 2
    assert "externen Verweisen" in letzte_zeile(capsys)


def test_splat_ply_ist_kein_mesh(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scanview_import.splat_source import write_gaussians

    quelle = tmp_path / "s.ply"
    write_gaussians(quelle, gaussians(1500), np.zeros(3))
    assert lauf("neu-ohne-wolke", quelle, tmp_path / "s", "--art", "mesh") == 2
    assert "ohne Dreiecke" in letzte_zeile(capsys)
