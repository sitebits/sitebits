"""Neue innere UV bei winzigen Quellinseln, Farbtransfer und Blattregression."""

import hashlib
import io
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import map_coordinates

from scanview_import.kacheln.backen import backen
from scanview_import.kacheln.projektion import _ObereDistanz
from scanview_import.kacheln.texturen import BildCache, _inseln, vorbereiten, zuschneiden
from scanview_import.mesh_source import read_mesh
from tests.test_kacheln import quelle


def inselquelle(tmp_path: Path, count: int = 2048):
    base = read_mesh(quelle(tmp_path / "basis", n=2), unit="m", up="z")
    side = int(np.ceil(np.sqrt(count)))
    offsets = np.column_stack([np.arange(count) % side, np.arange(count) // side]) / side
    positions = np.zeros((count, 3, 3))
    positions[:, :, :2] = offsets[:, None, :] + np.array([[0, 0], [0.8, 0], [0, 0.8]]) / side
    uv = positions[:, :, :2] * 0.8 + 0.1
    pixels = np.empty((512, 512, 3), dtype=np.uint8)
    pixels[:, :, 0] = 40 + np.arange(512)[None, :] // 3
    pixels[:, :, 1] = 40 + np.arange(512)[:, None] // 3
    pixels[:, :, 2] = 150
    texture = tmp_path / "verlauf.png"
    Image.fromarray(pixels).save(texture)
    return replace(base, positions=positions.reshape(-1, 3), uv=uv.reshape(-1, 2),
                   triangles=np.arange(count * 3, dtype=np.uint32).reshape(-1, 3),
                   materials=np.zeros(count, dtype=np.int32), textures=[texture])


def abtasten(image: Image.Image, uv: np.ndarray) -> np.ndarray:
    pixels = np.asarray(image)
    return np.column_stack([
        map_coordinates(pixels[:, :, channel].astype(float),
                        [uv[:, 1] * image.height - 0.5, uv[:, 0] * image.width - 0.5],
                        order=1, mode="grid-wrap", prefilter=False)
        for channel in range(3)
    ])


def test_viele_inseln_farbtreu_ohne_saum(tmp_path: Path) -> None:
    source = inselquelle(tmp_path)
    assert len(_inseln(source)) == 2048
    # Andere Quell-UV im reduzierten Mesh dürfen nicht direkt abgetastet werden.
    reduced = replace(source, positions=source.positions + [0, 0, 0.002], uv=source.uv * 0)
    stats = {}
    cache = BildCache()
    try:
        output, encoded = backen(reduced, source, 20, cache=cache, messung=stats)
    finally:
        cache.schliessen()
    assert len(encoded) == 1
    # 4x4-Zellen mit eigenem, dilatiertem Rand: Inhalt ≈ 8 von 36 Texeln, aber kein Fremdpolster.
    assert stats["contentFill"] >= 0.15
    assert stats["bakeTriangles"] == 2048
    assert stats["bakeTexels"] >= 2048 * 4**2 / 2
    assert stats["bakeSeconds"] > 0
    rng = np.random.default_rng(921)
    ids = rng.integers(0, 2048, 12000)
    weights = rng.dirichlet([1, 1, 1], len(ids))
    points = np.einsum("ij,ijk->ik", weights, output.positions[output.triangles[ids]])
    uv = np.einsum("ij,ijk->ik", weights, output.uv[output.triangles[ids]])
    expected_uv = points[:, :2] * 0.8 + 0.1
    with Image.open(source.textures[0]) as original, Image.open(io.BytesIO(encoded[0])) as image:
        assert max(image.size) <= 2048
        delta = np.abs(abtasten(image, uv) - abtasten(original, expected_uv))
        assert delta.mean() <= 8
        # Pixelzentren direkt an allen drei Rändern, einschließlich der Ecken.
        weights = np.concatenate([np.eye(3), [[0.5, 0.5, 0], [0, 0.5, 0.5], [0.5, 0, 0.5]]])
        edge_uv = np.einsum("ij,tjk->tik", weights, output.uv[output.triangles]).reshape(-1, 2)
        colors = abtasten(image, edge_uv)
        assert colors.min() >= 35
        assert np.all(colors[:, 2] == 150)


def test_plain_und_entartete_dreiecke(tmp_path: Path) -> None:
    source = inselquelle(tmp_path, 3)
    source = replace(source, materials=np.array([0, 1, 0]), textures=[source.textures[0], None])
    source.positions[6:] = source.positions[6]
    cache = BildCache()
    try:
        output, encoded = backen(source, source, 1, cache=cache)
        assert len(output.triangles) == 3
        assert len(encoded) == 1
        assert output.textures[1] is None
        assert np.isfinite(output.uv).all()
        plain = replace(source, materials=np.ones(3, dtype=np.int32))
        result, encoded = backen(plain, plain, 1, cache=cache)
        assert result is plain and not encoded
    finally:
        cache.schliessen()


def test_projektion_groessenklassen_und_fallback(tmp_path: Path) -> None:
    source = inselquelle(tmp_path, 40)
    source.positions[:3] = [[-20, -20, 0], [20, -20, 0], [0, 20, 0]]
    source.positions[3:, 2] = 1
    index = _ObereDistanz(source)
    points = np.array([[0, 0, 0.05], [0.1, 0.1, 0.01], [-19, -19, -0.1]])
    projected, faces = index.closest(points)
    np.testing.assert_allclose(projected[:, :2], points[:, :2], atol=1e-12)
    np.testing.assert_array_equal(projected[:, 2], 0)
    np.testing.assert_array_equal(faces, 0)
    # Gleich große Dreiecke, deren Mittelpunkte keine sichere Auswahl ergeben.
    source.positions[:] *= [1, 0.001, 1]
    points = source.positions[source.triangles].mean(axis=1) + [0, 0, 0.1]
    projected, faces = _ObereDistanz(source).closest(points)
    from scanview_import.mesh_surface import _closest_point_on_triangle

    tri = source.positions[source.triangles]
    all_closest = _closest_point_on_triangle(points[:, None, :], tri[:, 0], tri[:, 1], tri[:, 2])
    exact = np.linalg.norm(all_closest - points[:, None, :], axis=2).min(axis=1)
    np.testing.assert_allclose(np.linalg.norm(projected - points, axis=1), exact, atol=1e-12)


def test_blatt_bitgleich(tmp_path: Path) -> None:
    mesh = read_mesh(quelle(tmp_path / "quelle", n=4), unit="m", up="z")
    cache = BildCache()
    try:
        plan = vorbereiten(mesh, cache)
        output, encoded = zuschneiden(mesh, cache=cache, plan=plan)
    finally:
        cache.schliessen()
    with Image.open(io.BytesIO(encoded[0])) as image:
        assert hashlib.sha256(np.asarray(image).tobytes()).hexdigest() == (
            "3579c5c09042bba8152af71a7a2545846f010764b3b225f3340d9c6b0de6f33c"
        )
    assert hashlib.sha256(output.uv.tobytes()).hexdigest() == (
        "6377b8e286e73e8cc2f91d84c0b4f1b372238c1b3a2f89cabfb506324cee813f"
    )


def test_reduzierte_flaeche_ueber_vielen_quellinseln(tmp_path: Path) -> None:
    original = read_mesh(quelle(tmp_path / "quelle", n=32), unit="m", up="z")
    positions = original.positions[original.triangles].reshape(-1, 3) - [365000, 5806000, 70]
    positions[:, 2] = 0
    uv = (positions[:, :2] + 1) * 0.4 + 0.1
    # Getrennte UV trotz geometrisch gemeinsamer Kanten, glatte Farbe bis auf <1 Texel.
    uv += np.repeat(np.arange(2048), 3)[:, None] * 1e-9
    source = replace(original, positions=positions, uv=uv,
                     triangles=np.arange(positions.shape[0]).reshape(-1, 3),
                     materials=np.zeros(2048, dtype=np.int32),
                     textures=[original.textures[int(original.materials[0])]])
    assert len(_inseln(source)) == 2048
    reduced = replace(source,
                      positions=np.array([[-1, -1, 0.01], [1, -1, 0.01],
                                          [1, 1, 0.01], [-1, 1, 0.01]]),
                      triangles=np.array([[0, 1, 2], [0, 2, 3]]),
                      materials=np.zeros(2, dtype=np.int32), uv=np.zeros((4, 2)))
    cache = BildCache()
    stats = {}
    try:
        output, encoded = backen(reduced, source, 0.1, cache=cache, messung=stats)
    finally:
        cache.schliessen()
    weights = np.random.default_rng(21).dirichlet([1, 1, 1], 10000)
    ids = np.arange(len(weights)) % 2
    points = np.einsum("ij,ijk->ik", weights, output.positions[output.triangles[ids]])
    uv = np.einsum("ij,ijk->ik", weights, output.uv[output.triangles[ids]])
    with Image.open(source.textures[0]) as image, Image.open(io.BytesIO(encoded[0])) as baked:
        delta = np.abs(abtasten(baked, uv) - abtasten(image, (points[:, :2] + 1) * 0.4 + 0.1))
        assert delta.mean() <= 8
    assert stats["contentFill"] >= 0.3


def test_vertraege_mit_neuem_backpfad(tmp_path: Path) -> None:
    import json

    from spike.source_mesh import SourceMesh
    from spike.textur_mehrseitig import pruefen
    from spike.tiles_contract import check_tileset

    from scanview_import.kacheln import kacheln
    from tests.test_kacheln_inseln import verstreut

    path = verstreut(tmp_path / "quelle")
    output = tmp_path / "ausgabe"
    result = kacheln(path, output, blatt_dreiecke=24)
    inner = [m for m in result["measurements"] if m["phase"] == "kachel" and not m["leaf"]]
    assert inner and all(m["bakeTriangles"] > 0 for m in inner)
    assert all(m["contentFill"] >= 0.3 for m in inner)
    mesh = read_mesh(path, unit="m", up="z")
    geometry = check_tileset(output / "tileset.json",
                             SourceMesh(mesh.positions, mesh.triangles, None, None),
                             samples=1200, seed=42)
    for key in ("K2", "K3", "K4", "K6", "K7", "K8", "K10", "K11", "K13"):
        assert geometry[key]["ok"] is True, geometry[key]
    assert geometry["K5"]["sourceTriangles"] == geometry["K5"]["leafTriangles"]
    texture = pruefen(output / "tileset.json", path, samples=3000)
    assert texture["K9"]["ok"] is True
    assert texture["K12"]["ok"] is True
    (tmp_path / "geometrie.json").write_text(json.dumps(geometry, indent=2))
    (tmp_path / "textur.json").write_text(json.dumps(texture, indent=2))
