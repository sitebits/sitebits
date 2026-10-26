"""Verstreute synthetische UV-Inseln gegen unabhängige Vertragsprüfer."""

import io
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from spike.source_mesh import SourceMesh
from spike.textur_mehrseitig import pruefen
from spike.tiles_contract import check_tileset

from scanview_import.cli import main
from scanview_import.kacheln import kacheln
from scanview_import.kacheln.texturen import BildCache, _inseln, packplan, vorbereiten, zuschneiden
from scanview_import.mesh_source import read_mesh
from scanview_import.mesh_textures import sample_rgb
from tests.test_kacheln import quelle


def verstreut(folder: Path) -> Path:
    folder.mkdir()
    lines = ["mtllib quelle.mtl"]
    materials = []
    for page in range(3):
        pixels = np.zeros((512, 512, 3), dtype=np.uint8)
        pixels[:, :, 0] = np.arange(512)[None, :] // 2
        pixels[:, :, 1] = np.arange(512)[:, None] // 2
        pixels[:, :, 2] = page * 70
        Image.fromarray(pixels).save(folder / f"farbe-{page}.png")
        materials.extend([f"newmtl m{page}", f"map_Kd farbe-{page}.png"])
        for i in range(16):
            x, y = i % 4 * 128 + 20, i // 4 * 128 + 20
            number = page * 16 + i
            for dx, dy in [(0, 0), (1, 0), (1, 1), (0, 1)]:
                lines.append(f"v {number * 2 + dx} {dy} 0")
                lines.append(f"vt {(x + dx * 56) / 512} {(y + dy * 56) / 512}")
            base = number * 4 + 1
            lines.extend(
                [
                    f"usemtl m{page}",
                    f"f {base}/{base} {base + 1}/{base + 1} {base + 2}/{base + 2}",
                    f"f {base}/{base} {base + 2}/{base + 2} {base + 3}/{base + 3}",
                ]
            )
    (folder / "quelle.mtl").write_text("\n".join(materials) + "\n")
    path = folder / "quelle.obj"
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.mark.parametrize("leaf", [24, 40000])
def test_verstreute_seiten_vertrag_und_fuellung(tmp_path: Path, leaf: int) -> None:
    path = verstreut(tmp_path / "quelle")
    output = tmp_path / "ausgabe"
    result = kacheln(path, output, blatt_dreiecke=leaf)
    stats = [m for m in result["measurements"] if m["phase"] == "kachel" and m["leaf"]]
    assert sum(m["islands"] for m in stats) == 48
    assert len(stats) == (4 if leaf == 24 else 1)
    for entry in stats:
        assert len(entry["texturePages"]) == 1
        assert entry["texturePages"][0]["fill"] > 0.5
        assert entry["texturePages"][0]["contentFill"] > 0.5
    report = pruefen(output / "tileset.json", path, samples=3000)
    assert report["K9"]["ok"] is True
    assert report["K9"]["maxPagesPerLeaf"] == 1
    assert report["K9"]["minMedianRatio"] == pytest.approx(1, rel=1e-6)
    assert report["K12"]["ok"] is True
    assert report["K12"]["maxMeanAbsDeltaRgb"] <= 1
    mesh = read_mesh(path, unit="m", up="z")
    source = SourceMesh(mesh.positions, mesh.triangles, None, None)
    geometry = check_tileset(output / "tileset.json", source, samples=1000, seed=42)
    for key in ("K2", "K3", "K4", "K8", "K10"):
        assert geometry[key]["ok"] is True, geometry[key]
    (tmp_path / "textur-pruefer.json").write_text(json.dumps(report, indent=2))
    (tmp_path / "geometrie-pruefer.json").write_text(json.dumps(geometry, indent=2))


def test_inselecken_und_uv_naht(tmp_path: Path) -> None:
    mesh = read_mesh(verstreut(tmp_path / "quelle"), unit="m", up="z")
    one = replace(mesh, triangles=mesh.triangles[:2], materials=mesh.materials[:2])
    assert len(_inseln(one)) == 1
    split = replace(
        one,
        positions=one.positions[one.triangles].reshape(-1, 3),
        uv=one.uv[one.triangles].reshape(-1, 2),
        triangles=np.arange(6, dtype=np.uint32).reshape(2, 3),
    )
    assert len(_inseln(split)) == 1
    uv = split.uv.copy()
    uv[3:] += 0.5
    assert len(_inseln(replace(split, uv=uv))) == 2


def test_grenzueberschreitende_kleine_insel_bleibt_ganz(tmp_path: Path) -> None:
    mesh = read_mesh(verstreut(tmp_path / "quelle"), unit="m", up="z")
    mesh = replace(
        mesh, triangles=mesh.triangles[:2], materials=mesh.materials[:2], uv=mesh.uv + [0.94, 0]
    )
    output, encoded = zuschneiden(mesh)
    assert len(output.triangles) == 2 and len(encoded) == 1
    weights = np.random.default_rng(42).dirichlet([1, 1, 1], 2)
    src_uv = np.einsum("ij,ijk->ik", weights, mesh.uv[mesh.triangles])
    dst_uv = np.einsum("ij,ijk->ik", weights, output.uv[output.triangles])
    with Image.open(mesh.textures[int(mesh.materials[0])]) as source:
        with Image.open(io.BytesIO(encoded[0])) as target:
            np.testing.assert_array_equal(sample_rgb(source, src_uv), sample_rgb(target, dst_uv))


def test_cache_dekodiert_einmal_und_verwirft_lru(tmp_path: Path, monkeypatch) -> None:
    mesh = read_mesh(verstreut(tmp_path / "quelle"), unit="m", up="z")
    calls = []
    original = Image.Image.convert

    def convert(self, *args, **kwargs):
        if getattr(self, "filename", None):
            calls.append(str(self.filename))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "convert", convert)
    cache = BildCache(3 * 512 * 512 * 3)
    try:
        zuschneiden(mesh, cache=cache)
        zuschneiden(mesh, cache=cache)
        assert len(calls) == 3
        assert cache.bytes == cache.grenze
        paths = [p for p in mesh.textures if isinstance(p, Path)]
        small = BildCache(512 * 512 * 3)
        with small.bild(paths[0]):
            pass
        with small.bild(paths[1]):
            pass
        assert list(small.bilder) == [paths[1]]
        assert small.bytes <= small.grenze
        small.schliessen()
        assert small.bytes == 0
        disabled = BildCache(0)
        with disabled.bild(paths[0]):
            pass
        assert not disabled.bilder and disabled.bytes == 0
    finally:
        cache.schliessen()
    assert not cache.bilder
    with pytest.raises(ValueError, match="mindestens 0"):
        BildCache(-1)


def test_blatt_texel_erzwingt_teilung_und_loggt_ausnahme(tmp_path: Path) -> None:
    path = quelle(tmp_path / "quelle", n=56)
    Image.new("RGB", (256, 256), "orange").save(path.parent / "farbe.png")
    output = tmp_path / "ausgabe"
    assert (
        main(
            [
                "kacheln",
                str(path),
                str(output),
                "--blatt-texel",
                "128",
                "--blatt-dreiecke",
                "10000",
                "--textur-cache-gib",
                "0.001",
            ]
        )
        == 0
    )
    log = json.loads((output / "kacheln-log.json").read_text())
    leaves = [m for m in log["measurements"] if m["phase"] == "kachel" and m["leaf"]]
    assert len(leaves) == 2
    assert all(m["triangles"] < 6000 and m["textureBudgetExceeded"] for m in leaves)
    assert all(m["textureBudgetException"] == "unter 6000 Dreiecken" for m in leaves)
    assert sum(m["triangles"] for m in leaves) == 56 * 56 * 2
    report = pruefen(output / "tileset.json", path, samples=1000)
    assert report["K9"]["ok"] and report["K12"]["ok"]
    mesh = read_mesh(path, unit="m", up="z")
    geometry = check_tileset(
        output / "tileset.json",
        SourceMesh(mesh.positions, mesh.triangles, None, None),
        samples=1000,
    )
    for key in ("K2", "K3", "K4", "K8", "K10"):
        assert geometry[key]["ok"] is True, geometry[key]
    (tmp_path / "textur-pruefer.json").write_text(json.dumps(report, indent=2))
    (tmp_path / "geometrie-pruefer.json").write_text(json.dumps(geometry, indent=2))


@pytest.mark.parametrize("edge", [0, 15, 127, 8192])
def test_ungueltiges_texelbudget_vor_eingabe(tmp_path: Path, edge: int) -> None:
    with pytest.raises(ValueError, match="Zweierpotenz"):
        kacheln(tmp_path / "fehlt.obj", tmp_path / "ausgabe", blatt_texel=edge)
    assert not (tmp_path / "ausgabe").exists()


def test_lod_nachbarpolster_und_gemeinsamer_massstab(tmp_path: Path) -> None:
    mesh = read_mesh(verstreut(tmp_path / "quelle"), unit="m", up="z")
    cache = BildCache()
    try:
        islands, _ = vorbereiten(mesh, cache, error=0.5)
        assert len(islands) == 48
        assert all(i.polster * i.faktor >= 2 for i in islands)
        assert len({i.faktor for i in islands}) == 1
        full, large = zuschneiden(mesh, cache=cache)
        reduced, small = zuschneiden(mesh, error=0.5, cache=cache)
        assert len(small) == 1
        assert len(reduced.triangles) == len(full.triangles)
        with Image.open(io.BytesIO(large[0])) as a, Image.open(io.BytesIO(small[0])) as b:
            assert b.width * b.height < a.width * a.height
    finally:
        cache.schliessen()


def test_pruefer_erkennt_falsche_dichte_und_farbe(tmp_path: Path) -> None:
    path = verstreut(tmp_path / "quelle")
    output = tmp_path / "ausgabe"
    kacheln(path, output)
    # Gegenproben ändern ausschließlich die unabhängige Quelle nach dem Export.
    for page in path.parent.glob("farbe-*.png"):
        with Image.open(page) as image:
            enlarged = image.resize((1024, 1024))
        enlarged.save(page)
    report = pruefen(output / "tileset.json", path, samples=100)
    assert report["K9"]["ok"] is False
    for page in path.parent.glob("farbe-*.png"):
        Image.new("RGB", (512, 512), "magenta").save(page)
    report = pruefen(output / "tileset.json", path, samples=100)
    assert report["K12"]["ok"] is False


def test_sehr_grosser_lod_fehler_bleibt_begrenzt(tmp_path: Path) -> None:
    mesh = read_mesh(verstreut(tmp_path / "quelle"), unit="m", up="z")
    cache = BildCache(4 * 512 * 512 * 3)
    try:
        islands, _ = vorbereiten(mesh, cache, error=10000)
        assert len({i.faktor for i in islands}) == 1
        result, encoded = zuschneiden(mesh, error=10000, cache=cache)
        assert len(result.triangles) == len(mesh.triangles)
        assert len(encoded) == 1
        with Image.open(io.BytesIO(encoded[0])) as image:
            assert max(image.size) <= 64
        assert cache.bytes <= cache.grenze
    finally:
        cache.schliessen()


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "1e308"])
def test_cache_parameter_abgelehnt_vor_ausgabe(tmp_path: Path, value: str, capsys) -> None:
    output = tmp_path / "ausgabe"
    assert (
        main(["kacheln", str(tmp_path / "fehlt.obj"), str(output), "--textur-cache-gib", value])
        == 1
    )
    assert "endliche Zahl ab 0" in capsys.readouterr().err
    assert not output.exists()


def test_mehrseiten_minimiert_auch_fruehe_schmale_seite(tmp_path: Path) -> None:
    mesh = read_mesh(verstreut(tmp_path / "quelle"), unit="m", up="z")
    cache = BildCache()
    try:
        islands, _ = vorbereiten(mesh, cache)
        tall = replace(islands[0], low=np.array([0, 0]), high=np.array([8, 56]))
        wide = replace(islands[1], low=np.array([0, 0]), high=np.array([56, 1]))
        pages = packplan([tall, wide], maximum=64)
        assert [p["size"] for p in pages] == [(16, 64), (64, 16)]
        assert [i for p in pages for i, _ in p["items"]] == [0, 1]
    finally:
        cache.schliessen()
