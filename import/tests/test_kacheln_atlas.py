"""Blockmaße, echte Kantenpaare und zellgetrennte vektorisierte Randfüllung."""

import io
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from scipy.ndimage import map_coordinates

from scanview_import.kacheln.backen import _plan, _randfuellen, _zellen, backen
from scanview_import.kacheln.texturen import BildCache, Insel, packplan, zuschneiden
from tests.test_kacheln import fake_ktx
from tests.test_kacheln_backen import inselquelle


def quadrate(tmp_path: Path, n: int = 12):
    base = inselquelle(tmp_path, 1)
    x, y = np.meshgrid(np.arange(n + 1), np.arange(n + 1))
    positions = np.column_stack([x.ravel(), y.ravel(), np.zeros(x.size)]) / n
    v = (np.arange(n)[:, None] * (n + 1) + np.arange(n)).ravel()
    faces = np.concatenate([np.column_stack([v, v + 1, v + n + 2]),
                            np.column_stack([v, v + n + 2, v + n + 1])])
    return replace(base, positions=positions, triangles=faces, uv=positions[:, :2],
                   materials=np.zeros(len(faces), dtype=np.int32))


@pytest.mark.parametrize("extent,density", [(1, 5), (0, 0), (0.01, 1), (1, 17)])
def test_innere_seiten_blockmasse(extent: float, density: float) -> None:
    sizes, offsets, page = _plan(np.array([[extent, extent]]), density)
    assert all(s > 0 and s % 4 == 0 for s in page)
    assert np.all(offsets + sizes <= page)
    # Kein Resampling und keine neu erzwungene Zweierpotenz.
    if density == 17:
        assert page == (20, 20)
        np.testing.assert_array_equal(sizes, [[19, 19]])


def test_ein_texel_inhalt_blatt_und_innen(tmp_path: Path) -> None:
    mesh = inselquelle(tmp_path, 1)
    texture = tmp_path / "ein.png"
    Image.new("RGB", (1, 1), (25, 100, 220)).save(texture)
    mesh = replace(mesh, textures=[texture], uv=np.zeros_like(mesh.uv))
    cache = BildCache()
    try:
        for fn in (backen, zuschneiden):
            output, encoded = (fn(mesh, mesh, 20, cache=cache) if fn is backen
                               else fn(mesh, cache=cache))
            with Image.open(io.BytesIO(encoded[0])) as image:
                assert all(s % 4 == 0 for s in image.size)
                sample = output.uv[output.triangles[0]].mean(axis=0) * image.size
                np.testing.assert_array_equal(np.asarray(image)[int(sample[1]), int(sample[0])],
                                              [25, 100, 220])
            assert np.isfinite(output.uv).all()
        # Auch eine explizite ungepolsterte 1x1-Insel erhält eine 4x4-Seite.
        island = Insel(mesh, texture, np.ones(2), np.zeros(2), np.ones(2), polster=0)
        assert packplan([island])[0]["size"] == (4, 4)
    finally:
        cache.schliessen()


def test_randfuellung_ohne_fremdfarbe() -> None:
    sizes = np.array([[6, 6], [6, 6], [9, 5]])
    pixels = np.zeros((117, 3), dtype=np.uint8)
    mask = np.zeros(117, dtype=bool)
    anchors = [7, 70, 116]
    colors = [[255, 0, 0], [0, 255, 0], [0, 0, 255]]
    pixels[anchors] = colors
    mask[anchors] = True
    _randfuellen(pixels, mask, sizes)
    for region, color in zip(np.split(pixels, [36, 72]), colors, strict=True):
        np.testing.assert_array_equal(np.unique(region, axis=0), [color])


def test_paarung_echte_kanten_und_material(tmp_path: Path) -> None:
    mesh = quadrate(tmp_path, 1)
    faces, cells, uv, extents, pairs = _zellen(mesh.positions, mesh.triangles, mesh.materials)
    assert pairs == 1 and cells[0] == cells[1] and extents.shape == (1, 2)
    # Die Diagonale verbindet exakt die zwei tatsächlich gemeinsamen Indizes.
    for face, mapped in zip(faces, uv, strict=True):
        diagonal = face[np.all(mapped == [0, 0], axis=1) | np.all(mapped == [1, 1], axis=1)]
        np.testing.assert_array_equal(np.sort(diagonal), [0, 3])
    before, after = mesh.positions[mesh.triangles], mesh.positions[faces]
    np.testing.assert_allclose(np.cross(after[:, 1] - after[:, 0], after[:, 2] - after[:, 0]),
                               np.cross(before[:, 1] - before[:, 0], before[:, 2] - before[:, 0]))
    for positions, triangles, materials in [
        (mesh.positions, mesh.triangles, np.array([0, 1])),
        (mesh.positions[mesh.triangles].reshape(-1, 3), np.arange(6).reshape(2, 3), mesh.materials),
        (mesh.positions * [1, 0.01, 1], mesh.triangles, mesh.materials),
        (mesh.positions, np.concatenate([mesh.triangles, mesh.triangles[:1]]), np.zeros(3)),
    ]:
        assert _zellen(positions, triangles, materials)[-1] == 0


def test_paarung_laengste_kante_zuerst() -> None:
    positions = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [2, 1, 0], [0, 1, 0]])
    faces = np.array([[0, 1, 2], [0, 2, 4], [1, 3, 2]])
    _, cells, _, _, pairs = _zellen(positions, faces, np.zeros(3))
    assert pairs == 1 and cells[0] == cells[1] and cells[0] != cells[2]


def test_paarfuellgrad_und_seitenflaeche(tmp_path: Path) -> None:
    mesh = quadrate(tmp_path)
    cache = BildCache()
    stats = {}
    try:
        _, encoded = backen(mesh, mesh, 20, cache=cache, messung=stats)
    finally:
        cache.schliessen()
    assert stats["pairs"] == len(mesh.triangles) // 2 and stats["unpaired"] == 0
    assert stats["contentFill"] >= 0.3
    # Alte Mindestzelle: 6x6 pro Dreieck; sogar gegenüber der idealen Packung ≤60 %.
    assert stats["pageTexels"] <= 0.6 * len(mesh.triangles) * 36
    assert stats["bakePaddedTexels"] == stats["pairs"] * 36
    with Image.open(io.BytesIO(encoded[0])) as image:
        assert stats["pageTexels"] == image.width * image.height


@pytest.mark.parametrize("shift", [0.0, 0.002])
def test_innentexel_bisherige_farben(tmp_path: Path, shift: float) -> None:
    mesh = inselquelle(tmp_path, 16)
    cache = BildCache()
    try:
        reduced = replace(mesh, positions=mesh.positions + [0, 0, shift], uv=mesh.uv * 0)
        result, encoded = backen(reduced, mesh, 20, cache=cache)
    finally:
        cache.schliessen()
    with Image.open(io.BytesIO(encoded[0])) as image, Image.open(mesh.textures[0]) as source:
        page = np.asarray(image)
        pixels = np.asarray(source)
        for face in result.triangles:
            uv = result.uv[face] * image.size
            lo, hi = np.floor(uv.min(axis=0)).astype(int), np.ceil(uv.max(axis=0)).astype(int)
            x, y = np.meshgrid(np.arange(lo[0], hi[0]), np.arange(lo[1], hi[1]))
            points = np.column_stack([x.ravel() + 0.5, y.ravel() + 0.5])
            basis = np.stack([uv[1] - uv[0], uv[2] - uv[0]], axis=1)
            vw = np.linalg.solve(basis, (points - uv[0]).T).T
            weights = np.column_stack([1 - vw.sum(axis=1), vw])
            hit = np.all(weights >= -1e-12, axis=1)
            assert hit.any()
            world = weights[hit] @ result.positions[face]
            original_uv = world[:, :2] * 0.8 + 0.1
            expected = np.column_stack([
                map_coordinates(pixels[:, :, c], [original_uv[:, 1] * source.height - 0.5,
                                                  original_uv[:, 0] * source.width - 0.5],
                                order=1, mode="grid-wrap", prefilter=False)
                for c in range(3)
            ])
            actual = page[y.ravel()[hit], x.ravel()[hit]]
            assert np.abs(actual.astype(int) - expected.astype(int)).max() <= 1


@pytest.mark.parametrize("invalid_input", [True, False])
def test_packschritt_verweigert_blockverletzung(tmp_path: Path, monkeypatch, invalid_input: bool):
    from scanview_import.kacheln import paket

    mesh = inselquelle(tmp_path, 1)
    image = Image.new("RGB", (1, 1) if invalid_input else (8, 8))
    data = io.BytesIO()
    image.save(data, format="PNG")
    mesh = replace(mesh, textures=[data.getvalue()])
    called = []

    def encoder(texture, maximum, encoding):
        called.append(True)
        # Ungültige echte Headermaße, obwohl das vorgeschaltete PNG gültig ist.
        data = io.BytesIO()
        Image.new("RGB", (6, 8)).save(data, format="PNG")
        return fake_ktx(data.getvalue(), maximum, encoding)

    monkeypatch.setattr(paket, "encode_texture", encoder)
    packer = paket.Packschritt(tmp_path / "paket", None, None)
    with pytest.raises(ValueError, match="Vielfache von 4"):
        packer.kachel(mesh, "kachel.glb", np.zeros(3), True)
    assert bool(called) is not invalid_input
    assert not list((tmp_path / "paket").rglob("*.glb"))


def test_backrand_bleibt_eigene_materialfarbe(tmp_path: Path) -> None:
    mesh = inselquelle(tmp_path, 2)
    textures = []
    colors = [[240, 10, 20], [10, 240, 20]]
    for i, color in enumerate(colors):
        path = tmp_path / f"farbe-{i}.png"
        Image.new("RGB", (8, 8), tuple(color)).save(path)
        textures.append(path)
    mesh = replace(mesh, textures=textures, materials=np.array([0, 1]))
    cache = BildCache()
    try:
        result, encoded = backen(mesh, mesh, 20, cache=cache)
    finally:
        cache.schliessen()
    with Image.open(io.BytesIO(encoded[0])) as image:
        for face, color in zip(result.triangles, colors, strict=True):
            uv = result.uv[face] * image.size
            lo = np.rint(uv.min(axis=0) - 1).astype(int)
            hi = np.rint(uv.max(axis=0) + 1).astype(int)
            cell = np.asarray(image)[lo[1]:hi[1], lo[0]:hi[0]]
            assert cell.shape == (6, 6, 3)
            np.testing.assert_array_equal(np.unique(cell.reshape(-1, 3), axis=0), [color])
