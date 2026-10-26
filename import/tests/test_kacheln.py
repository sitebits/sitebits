"""Synthetische Netze gegen den unveränderten externen Kachelvertrag."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from spike.source_mesh import SourceMesh
from spike.tiles_contract import check_tileset, load_tileset

from scanview_import.cli import main
from scanview_import.kacheln import kacheln
from scanview_import.mesh_source import read_mesh


def quelle(folder: Path, n: int = 24, curved: bool = False) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    x, y = np.meshgrid(np.linspace(-1, 1, n + 1), np.linspace(-1, 1, n + 1))
    z = np.sqrt(4 - x * x - y * y) if curved else 0.04 * np.sin(x * 4) * np.cos(y * 3)
    vertices = np.column_stack([x.ravel(), y.ravel(), z.ravel()]) + [365_000, 5_806_000, 70]
    uv = np.column_stack([(x.ravel() + 1) / 2, (y.ravel() + 1) / 2])
    indices = np.arange((n + 1) ** 2).reshape(n + 1, n + 1)
    a, b = indices[:-1, :-1].ravel(), indices[:-1, 1:].ravel()
    c, d = indices[1:, :-1].ravel(), indices[1:, 1:].ravel()
    faces = np.concatenate([np.column_stack([a, b, d]), np.column_stack([a, d, c])])
    path = folder / "quelle.obj"
    with path.open("w") as f:
        f.write("mtllib quelle.mtl\n")
        for v in vertices:
            f.write("v " + " ".join(map(str, v)) + "\n")
        for t in uv:
            f.write("vt " + " ".join(map(str, t)) + "\n")
        f.write("usemtl oberflaeche\n")
        for tri in faces + 1:
            f.write("f " + " ".join(f"{i}/{i}" for i in tri) + "\n")
    (folder / "quelle.mtl").write_text("newmtl oberflaeche\nmap_Kd farbe.png\n")
    pixels = np.zeros((64, 64, 3), dtype=np.uint8)
    pixels[:, :, 0] = np.arange(64)[None, :] * 4
    pixels[:, :, 1] = np.arange(64)[:, None] * 4
    Image.fromarray(pixels).save(folder / "farbe.png")
    return path


def pruefen(path: Path, output: Path) -> dict:
    mesh = read_mesh(path, unit="m", up="z")
    uv = mesh.uv.copy()
    uv[:, 1] = 1 - uv[:, 1]
    source = SourceMesh(
        mesh.positions, mesh.triangles, uv[mesh.triangles], path.parent / "farbe.png"
    )
    return check_tileset(output / "tileset.json", source, samples=1200, seed=42)


@pytest.mark.parametrize("curved", [False, True])
def test_vertrag(tmp_path: Path, curved: bool) -> None:
    path = quelle(tmp_path / "quelle", curved=curved)
    output = tmp_path / "ausgabe"
    kacheln(path, output, max_dreiecke=400, blatt_dreiecke=180, crs="EPSG:25833", hoehe="DHHN2016")
    report = pruefen(path, output)
    for criterion in ("K2", "K3", "K4", "K7", "K8", "K9", "K10", "K11", "K12", "K13"):
        assert report[criterion]["ok"] is True, (criterion, report[criterion])
    assert report["K14"]["ran"] is False
    raw, tiles = load_tileset(output / "tileset.json")
    assert max(len(t.faces) for t in tiles) <= 400
    assert sum(len(t.faces) for t in tiles if t.is_leaf) == 24 * 24 * 2
    assert raw["extras"]["coordinateReferenceSystem"] == "EPSG:25833"
    assert raw["extras"]["heightSystem"] == "DHHN2016"

    def walk(node: dict) -> None:
        assert node["refine"] == "REPLACE"
        for child in node.get("children", []):
            assert node["geometricError"] > child["geometricError"]
            walk(child)

    walk(raw["root"])
    assert not list(output.rglob("*.png"))  # Bilder sind vollständig im GLB eingebettet.


def test_cli_und_einzelblatt(tmp_path: Path) -> None:
    path = quelle(tmp_path / "quelle", n=4)
    output = tmp_path / "ausgabe"
    assert (
        main(["kacheln", str(path), str(output), "--crs", "EPSG:25833", "--hoehe", "DHHN2016"]) == 0
    )
    raw = json.loads((output / "tileset.json").read_text())
    assert raw["root"]["geometricError"] == 0
    assert pruefen(path, output)["K2"]["ok"] is True
    before = (output / "tileset.json").read_bytes()
    assert main(["kacheln", str(path), str(output)]) == 1
    assert (output / "tileset.json").read_bytes() == before


@pytest.mark.parametrize(("maximum", "leaf"), [(0, 1), (100001, 40000), (100, 101)])
def test_budget_ablehnen(tmp_path: Path, maximum: int, leaf: int) -> None:
    with pytest.raises(ValueError, match="Erforderlich"):
        kacheln(
            tmp_path / "fehlt.obj", tmp_path / "ausgabe", max_dreiecke=maximum, blatt_dreiecke=leaf
        )


def test_rand_und_materialien(tmp_path: Path) -> None:
    from scanview_import.kacheln import _vereinfachen

    path = quelle(tmp_path / "quelle", n=32)
    mesh = read_mesh(path, unit="m", up="z")
    centers = mesh.positions[mesh.triangles].mean(axis=1)
    mesh.materials[centers[:, 0] > 365000] = 0
    reduced, error = _vereinfachen(mesh, 800)
    assert len(reduced.triangles) <= 800
    assert error > 0
    assert set(reduced.materials) == {0, 1}

    def edges(m):
        result = {}
        for material in np.unique(m.materials):
            faces = m.triangles[m.materials == material]
            all_edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
            unique, counts = np.unique(np.sort(all_edges, axis=1), axis=0, return_counts=True)
            result[int(material)] = {
                tuple(sorted(tuple(v) for v in m.positions[edge])) for edge in unique[counts == 1]
            }
        return result

    assert edges(mesh) == edges(reduced)


def test_randbudget_mit_ersatzstufe_ohne_sperre(tmp_path: Path) -> None:
    """Gesperrte Ränder allein verfehlen das Budget; die Ersatzstufen halten es ein."""
    from scanview_import.kacheln import _vereinfachen

    path = quelle(tmp_path / "quelle", n=8)
    mesh = read_mesh(path, unit="m", up="z")
    reduced, error = _vereinfachen(mesh, 4)
    assert 0 < len(reduced.triangles) <= 4
    assert error >= 0.0


def test_unmoegliches_budget(tmp_path: Path) -> None:
    from scanview_import.kacheln import _vereinfachen

    path = quelle(tmp_path / "quelle", n=4)
    mesh = read_mesh(path, unit="m", up="z")
    with pytest.raises(ValueError, match="nicht erreichbar"):
        _vereinfachen(mesh, 0)


def test_blattdreiecke_eindeutig(tmp_path: Path) -> None:
    path = quelle(tmp_path / "quelle", n=16)
    output = tmp_path / "ausgabe"
    kacheln(path, output, max_dreiecke=300, blatt_dreiecke=100)
    mesh = read_mesh(path, unit="m", up="z")
    _, tiles = load_tileset(output / "tileset.json")

    def canonical(vertices, faces):
        return sorted(tuple(sorted(tuple(np.round(v, 6)) for v in tri)) for tri in vertices[faces])

    source = canonical(mesh.positions, mesh.triangles)
    leaves = sorted(
        tri for tile in tiles if tile.is_leaf for tri in canonical(tile.vertices, tile.faces)
    )
    assert source == leaves


def test_texturgrenze_wird_wiederhergestellt(tmp_path: Path, monkeypatch) -> None:
    from scanview_import.kacheln.texturen import oeffnen

    source = tmp_path / "farbe.png"
    Image.new("RGB", (64, 64)).save(source)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with oeffnen(source) as image:
        assert image.size == (64, 64)
    assert Image.MAX_IMAGE_PIXELS == 100
    with pytest.raises(OSError):
        oeffnen(tmp_path / "fehlt.png")
    assert Image.MAX_IMAGE_PIXELS == 100


def test_mehrere_texturseiten(tmp_path: Path) -> None:
    path = quelle(tmp_path / "quelle", n=8)
    mtl = path.parent / "quelle.mtl"
    with mtl.open("a") as f:
        f.write("newmtl zweites\nmap_Kd andere.png\n")
    Image.new("RGB", (64, 64), "blue").save(path.parent / "andere.png")
    lines = path.read_text().splitlines()
    first_face = next(i for i, line in enumerate(lines) if line.startswith("f "))
    lines.insert(first_face + 60, "usemtl zweites")
    path.write_text("\n".join(lines) + "\n")
    output = tmp_path / "ausgabe"
    kacheln(path, output, max_dreiecke=200, blatt_dreiecke=80)
    from scanview_import.mesh_gltf import read_gltf
    from scanview_import.mesh_source import resolve_mesh_source

    _, tiles = load_tileset(output / "tileset.json")
    total = 0
    for tile in tiles:
        if not tile.is_leaf:
            continue
        mesh = read_gltf(resolve_mesh_source(output / tile.tile_path))
        total += len(mesh.triangles)
        assert all(isinstance(t, bytes) for t in mesh.textures if t is not None)
    assert total == 128
    assert not list(output.rglob("*.png"))


def test_ausschnitt_pixel_uv_und_polster(tmp_path: Path) -> None:
    import io
    from dataclasses import replace

    from scanview_import.kacheln.texturen import zuschneiden
    from scanview_import.mesh_textures import sample_rgb

    path = quelle(tmp_path / "quelle", n=4)
    mesh = read_mesh(path, unit="m", up="z")
    mesh = replace(mesh, uv=mesh.uv * [0.25, 0.5] + [0.2, 0.3])
    output, textures = zuschneiden(mesh)
    with Image.open(io.BytesIO(textures[0])) as crop:
        assert crop.size == (32, 64)
        source = Image.open(path.parent / "farbe.png")
        np.testing.assert_array_equal(sample_rgb(crop, output.uv), sample_rgb(source, mesh.uv))
        # Linkes Polster enthält die echten Quellnachbarn; kein schwarzer Leerraum.
        np.testing.assert_array_equal(np.asarray(crop)[0, 0], np.asarray(source)[15, 8])
    np.testing.assert_array_equal(mesh.positions, output.positions)
    np.testing.assert_array_equal(mesh.triangles, output.triangles)


@pytest.mark.parametrize("materials", [4, 20])
def test_viele_materialien_mit_geteilten_vertices(tmp_path: Path, materials: int) -> None:
    import io
    from dataclasses import replace

    from scanview_import.kacheln.texturen import zuschneiden
    from scanview_import.mesh_textures import sample_rgb

    path = quelle(tmp_path / "quelle", n=8)
    mesh = read_mesh(path, unit="m", up="z")
    paths = []
    for i in range(materials):
        texture = tmp_path / f"seite-{i}.png"
        Image.new("RGB", (32, 24), (i * 10, 100, 200)).save(texture)
        paths.append(texture)
    ids = np.arange(len(mesh.triangles)) % materials
    mesh = replace(mesh, materials=ids, textures=paths)
    output, textures = zuschneiden(mesh)
    assert len(textures) == 1
    # Die neue Packung vereinigt Materialien; Zuordnung über Welt-Dreiecke prüfen.
    source_faces = mesh.positions[mesh.triangles]
    with Image.open(io.BytesIO(textures[0])) as crop:
        assert max(crop.size) <= 4096
        assert len(output.triangles) == len(mesh.triangles)
        for face in output.triangles:
            original = np.flatnonzero(np.all(source_faces == output.positions[face], axis=(1, 2)))
            assert len(original) == 1
            color = sample_rgb(crop, output.uv[face].mean(axis=0, keepdims=True))[0]
            np.testing.assert_array_equal(color, [int(ids[original[0]]) * 10, 100, 200])


def test_mehrseiten_erhalten_flaeche_texeldichte_und_farbe(tmp_path: Path, monkeypatch) -> None:
    import io

    from scanview_import.kacheln import texturen
    from scanview_import.mesh_surface import SourceMesh, SurfaceIndex
    from scanview_import.mesh_textures import sample_rgb

    path = quelle(tmp_path / "quelle", n=4)
    mesh = read_mesh(path, unit="m", up="z")
    monkeypatch.setattr(texturen, "SEITE", 24)
    output, textures = texturen.zuschneiden(mesh, maximum=32)
    assert len(textures) == 9
    a = mesh.positions[mesh.triangles]
    b = output.positions[output.triangles]

    def area(tri):
        return np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) / 2

    assert area(b).sum() == pytest.approx(area(a).sum(), rel=1e-8)
    samples = SourceMesh(output.positions, output.triangles, None, None).sample_surface(1000, 42)[0]
    assert SurfaceIndex(mesh.positions, mesh.triangles).distance(samples).max() < 1e-8
    source = Image.open(path.parent / "farbe.png")

    def cross2(a, b):
        return a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]

    for material, data in textures.items():
        ids = output.triangles[output.materials == material]
        uv = output.uv[ids]
        with Image.open(io.BytesIO(data)) as crop:
            assert max(crop.size) <= 32
            # Die ursprünglichen UV ergeben sich bei diesem Gitter aus XY.
            world = output.positions[ids].mean(axis=1)
            original_uv = (world[:, :2] - [365000, 5806000] + 1) / 2
            original_uv[:, 1] = 1 - original_uv[:, 1]
            np.testing.assert_allclose(
                sample_rgb(crop, uv.mean(axis=1)), sample_rgb(source, original_uv), atol=4
            )
            uv_area = np.abs(cross2(uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0])) / 2
            density = uv_area * crop.width * crop.height / area(output.positions[ids])
            # Originale 64²-Textur über 2 mal 2 m; Relief verlängert nur die Oberfläche.
            original = (
                np.abs(
                    cross2(
                        (output.positions[ids][:, 1, :2] - output.positions[ids][:, 0, :2]) / 2,
                        (output.positions[ids][:, 2, :2] - output.positions[ids][:, 0, :2]) / 2,
                    )
                )
                / 2
                * 64**2
                / area(output.positions[ids])
            )
            np.testing.assert_allclose(density, original, rtol=1e-8)


def test_lod_textur_passt_zum_fehler(tmp_path: Path) -> None:
    import io

    from scanview_import.kacheln.texturen import zuschneiden

    mesh = read_mesh(quelle(tmp_path / "quelle", n=4), unit="m", up="z")
    leaf, full = zuschneiden(mesh)
    # Acht Texel pro Fehlerlänge: 2 m ergeben dieselbe Reduktion wie zuvor 0,25 m.
    inner, small = zuschneiden(mesh, error=2.0)
    assert Image.open(io.BytesIO(small[0])).width < Image.open(io.BytesIO(full[0])).width
    np.testing.assert_array_equal(inner.positions, leaf.positions)
    np.testing.assert_array_equal(inner.triangles, leaf.triangles)
    image = Image.open(io.BytesIO(small[0]))
    # Zusätzliche echte Nachbarn bleiben auch nach Verkleinerung mindestens 2 px breit.
    pixel = inner.uv * image.size
    assert pixel.min() >= 2
    assert np.all(pixel.max(axis=0) <= np.asarray(image.size) - 2)


def fake_ktx(texture, max_px, encoding):
    """Nur der Encoder wird ersetzt. Container, meshopt und Quantisierung laufen echt."""
    import io
    import struct

    from scanview_import.mesh_textures import KTX_MAGIC, ktx_info

    image = Image.open(io.BytesIO(texture))
    data = bytearray(96)
    data[:12] = KTX_MAGIC
    struct.pack_into("<9I", data, 12, 0, 1, image.width, image.height, 0, 0, 1, 1, 0)
    struct.pack_into("<2I", data, 48, 80, 16)
    data[92] = 166
    assert max(image.size) <= max_px and encoding == "uastc"
    return bytes(data), ktx_info(bytes(data))


def test_packen_paket_prueffassung_und_negative_pruefung(tmp_path: Path, monkeypatch) -> None:
    import copy

    import pygltflib as g

    from scanview_import.kacheln import paket
    from scanview_import.manifest_io import read_manifest
    from scanview_import.mesh_verify import verify_mesh

    monkeypatch.setattr(paket, "encode_texture", fake_ktx)
    path = quelle(tmp_path / "quelle", n=12)
    output = tmp_path / "ausgabe"
    args = [
        "kacheln",
        str(path),
        str(output),
        "--packen",
        "--jobs",
        "1",
        "--crs",
        "EPSG:25833",
        "--hoehe",
        "DHHN2016",
        "--max-dreiecke",
        "200",
        "--blatt-dreiecke",
        "80",
    ]
    assert main(args) == 0
    assert pruefen(path, output / "prueffassung")["K9"]["ok"] is True
    pkg = output / "paket"
    manifest = read_manifest(pkg)
    layer = manifest["layers"][0]
    assert layer["triangleCount"] == 288
    assert layer["kind"] == "mesh-tiles"
    assert layer["provenance"]["interpolated"] is True
    assert verify_mesh(path, pkg)["ok"] is True
    assert main(["verify", str(path), str(pkg)]) == 0
    assert not list(pkg.rglob("*.png"))
    for p in pkg.rglob("*.glb"):
        doc = g.GLTF2().load(str(p))
        assert "EXT_meshopt_compression" in doc.extensionsRequired
        assert "KHR_mesh_quantization" in doc.extensionsRequired
        assert all(i.mimeType == "image/ktx2" and i.bufferView is not None for i in doc.images)
        assert all(not i.uri for i in doc.images)
    for field, value, match in [
        ("boundsWorld", [[0, 0, 0], [1, 1, 1]], "boundsWorld"),
        ("triangleCount", 10, "Blattdreieckszahl"),
        ("url", "../tileset.json", "Paketpfad"),
        ("positionStepM", 0.1, "Positionsschrittweite"),
    ]:
        bad = copy.deepcopy(manifest)
        bad["layers"][0][field] = value
        with pytest.raises(ValueError, match=match):
            paket.pruefen(pkg, bad)
    with monkeypatch.context() as patch:
        original = paket.ktx_info

        def falsche_kanten(data):
            return {**original(data), "width": 6}

        patch.setattr(paket, "ktx_info", falsche_kanten)
        with pytest.raises(ValueError, match="Vielfache von 4"):
            paket.pruefen(pkg)
    ts_path = pkg / layer["url"]
    ts = json.loads(ts_path.read_text())
    ts["root"]["content"]["uri"] = "tileset.json"
    ts_path.write_text(json.dumps(ts))
    with pytest.raises(ValueError, match="Paketpfad"):
        paket.pruefen(pkg)


def test_packfehler_veroeffentlicht_kein_manifest(tmp_path: Path, monkeypatch) -> None:
    from scanview_import.kacheln import paket

    def kaputt(*args):
        raise RuntimeError("Synthetischer Encoderfehler")

    monkeypatch.setattr(paket, "encode_texture", kaputt)
    path = quelle(tmp_path / "quelle", n=4)
    output = tmp_path / "ausgabe"
    assert (
        main(
            [
                "kacheln",
                str(path),
                str(output),
                "--packen",
                "--jobs",
                "1",
                "--crs",
                "EPSG:25833",
                "--hoehe",
                "DHHN2016",
            ]
        )
        == 1
    )
    assert not (output / "paket/project.json").exists()
    assert (output / "prueffassung/tileset.json").exists()
    assert pruefen(path, output / "prueffassung")["K2"]["ok"] is True


def test_packen_braucht_rahmen_vor_ausgabe(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="benötigt --crs und --hoehe"):
        kacheln(tmp_path / "fehlt.obj", tmp_path / "ausgabe", packen=True)
    assert not (tmp_path / "ausgabe").exists()


@pytest.mark.docker
def test_packen_echtes_ktx2_mit_rueckleseprobe(tmp_path: Path) -> None:
    from scanview_import.mesh_source import read_mesh
    from scanview_import.mesh_textures import decode_texture, ktx_info, sample_rgb

    path = quelle(tmp_path / "quelle", n=4)
    output = tmp_path / "ausgabe"
    kacheln(path, output, packen=True, crs="EPSG:25833", hoehe="DHHN2016")
    packed = read_mesh(output / "paket/layers/kacheln/kachel-000000.glb")
    reference = read_mesh(output / "prueffassung/kachel-000000.glb")
    used = [t for t in packed.textures if t is not None]
    original = [t for t in reference.textures if t is not None]
    for data, png in zip(used, original, strict=True):
        info = ktx_info(data)
        assert info["encoding"] == "ktx2-uastc" and info["levels"] > 1
        uv = np.random.default_rng(42).random((1000, 2))
        assert (
            np.abs(
                sample_rgb(decode_texture(data), uv) - sample_rgb(decode_texture(png), uv)
            ).mean()
            < 10
        )


def test_echte_4096_grenze_mit_schmaler_textur(tmp_path: Path) -> None:
    import io

    from scanview_import.kacheln.texturen import zuschneiden

    path = quelle(tmp_path / "quelle", n=2)
    Image.new("RGB", (4100, 8), "orange").save(path.parent / "farbe.png")
    mesh = read_mesh(path, unit="m", up="z")
    output, textures = zuschneiden(mesh)
    assert len(textures) == 1  # Beide geschnittenen Streifen passen zusammen auf eine Seite.
    assert len(output.triangles) > len(mesh.triangles)
    assert all(max(Image.open(io.BytesIO(data)).size) <= 4096 for data in textures.values())
    # Auch ein einzelnes Dreieck kann viele Seiten benötigen: Teilung ist dann keine
    # Option, weitere Seiten erhalten trotzdem seine komplette Oberfläche.
    from dataclasses import replace

    single = replace(mesh, triangles=mesh.triangles[:1], materials=mesh.materials[:1])
    page_mesh, pages = zuschneiden(single)
    assert len(pages) >= 1
    tri = page_mesh.positions[page_mesh.triangles]
    source = single.positions[single.triangles]

    def area(t):
        return np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1).sum()

    assert area(tri) == pytest.approx(area(source), rel=1e-8)


def test_uv_entartung_auf_seitengrenze_ohne_doppelte_flaeche(tmp_path: Path) -> None:
    from dataclasses import replace

    from scanview_import.kacheln.texturen import _seite

    mesh = read_mesh(quelle(tmp_path / "quelle", n=2), unit="m", up="z")
    uv = mesh.uv.copy()
    uv[:, 0] = 0.5
    mesh = replace(mesh, uv=uv)
    assert _seite(mesh, np.array([0, 0]), np.array([0.5, 1]), np.array([False, True])) is None
    page = _seite(mesh, np.array([0.5, 0]), np.array([1, 1]), np.array([True, True]))
    assert page is not None
    assert len(page.triangles) == len(mesh.triangles)


def test_uv_wiederholung_und_schmaler_polsterrand(tmp_path: Path) -> None:
    import io
    from dataclasses import replace

    from scanview_import.kacheln.texturen import zuschneiden
    from scanview_import.mesh_textures import sample_rgb

    mesh = read_mesh(quelle(tmp_path / "quelle", n=4), unit="m", up="z")
    mesh = replace(mesh, uv=mesh.uv * [0.1, 0.1] + [-0.05, 0.95])
    output, textures = zuschneiden(mesh)
    with Image.open(io.BytesIO(textures[0])) as crop:
        source = Image.open(mesh.textures[1])
        rng = np.random.default_rng(42)
        # Gleiche baryzentrische Abtastpunkte prüfen auch das Überqueren von UV=0/1.
        weights = rng.dirichlet([1, 1, 1], len(mesh.triangles))
        src_uv = (mesh.uv[mesh.triangles] * weights[:, :, None]).sum(axis=1)
        dst_uv = (output.uv[output.triangles] * weights[:, :, None]).sum(axis=1)
        np.testing.assert_array_equal(sample_rgb(source, src_uv), sample_rgb(crop, dst_uv))


def test_untexturiertes_paket_ohne_encoder(tmp_path: Path) -> None:
    from scanview_import.mesh_verify import verify_mesh

    path = quelle(tmp_path / "quelle", n=4)
    path.write_text(
        "\n".join(
            line
            for line in path.read_text().splitlines()
            if not line.startswith(("mtllib", "usemtl"))
        )
    )
    output = tmp_path / "ausgabe"
    kacheln(
        path,
        output,
        packen=True,
        crs="EPSG:25833",
        hoehe="DHHN2016",
        max_dreiecke=50,
        blatt_dreiecke=10,
    )
    assert verify_mesh(path, output / "paket")["ok"] is True
    other = tmp_path / "andere.obj"
    other.write_text(path.read_text() + "\n# andere Quelle\n")
    report = verify_mesh(other, output / "paket")
    assert report["sourceIdentityOk"] is False
    assert report["ok"] is False


def _mit_splitter(tmp_path: Path):
    from dataclasses import replace

    mesh = read_mesh(quelle(tmp_path / "quelle", n=12), unit="m", up="z")
    corner = mesh.positions[0] + [0.3, 0.3, 0.0]
    sliver = corner + np.array([[0, 0, 0], [1e-6, 0, 0], [0, 1e-6, 0]])
    base = len(mesh.positions)
    return replace(
        mesh,
        positions=np.vstack([mesh.positions, sliver]),
        uv=np.vstack([mesh.uv, np.full((3, 2), 0.5)]),
        triangles=np.vstack([mesh.triangles, [[base, base + 1, base + 2]]]).astype(np.uint32),
        materials=np.append(mesh.materials, mesh.materials[0]),
        # Der Packschritt liest Kacheln mit eingebetteten Bildern (Bytes), nicht Pfade.
        textures=[t.read_bytes() if isinstance(t, Path) else t for t in mesh.textures],
    )


def test_packen_entfernt_nur_splitter_unter_dem_raster(tmp_path: Path, monkeypatch) -> None:
    from scanview_import.kacheln import paket

    monkeypatch.setattr(paket, "encode_texture", fake_ktx)
    mesh = _mit_splitter(tmp_path)
    packer = paket.Packschritt(tmp_path / "paket", "EPSG:25833", "DHHN2016")
    packer.kachel(mesh, "kachel-000000.glb", mesh.positions.mean(axis=0), False)
    report = packer.reports["kachel-000000.glb"]
    assert report["removal"]["removed"] == 1
    assert packer.counts["kachel-000000.glb"] == len(mesh.triangles) - 1


def test_packen_fehlendes_flaechendreieck_bricht_ab(tmp_path: Path, monkeypatch) -> None:
    from dataclasses import replace

    from scanview_import.kacheln import paket

    monkeypatch.setattr(paket, "encode_texture", fake_ktx)
    original = paket.read_gltf

    def ohne_dreieck(source):
        mesh = original(source)
        return replace(mesh, triangles=mesh.triangles[1:], materials=mesh.materials[1:])

    monkeypatch.setattr(paket, "read_gltf", ohne_dreieck)
    mesh = _mit_splitter(tmp_path)
    packer = paket.Packschritt(tmp_path / "paket", "EPSG:25833", "DHHN2016")
    with pytest.raises(ValueError, match="Geometrievertrag"):
        packer.kachel(mesh, "kachel-000000.glb", mesh.positions.mean(axis=0), False)
