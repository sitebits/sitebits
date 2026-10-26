"""Flächenschranken und Texturdiagnose an kleinen synthetischen Netzen."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from scanview_import.kacheln import _fehlergrenze, _lod_fehler, kacheln
from scanview_import.mesh_source import read_mesh
from tests.test_kacheln import quelle


def dreieck(tmp_path: Path, size: float = 1.0):
    path = tmp_path / "dreieck.obj"
    path.write_text(f"v 0 0 0\nv {size} 0 0\nv 0 {size} 0\nf 1 2 3\n")
    return read_mesh(path, unit="m", up="z")


@pytest.mark.parametrize("offset", [0.0, 0.05])
def test_grosses_dreieck_ohne_radiusstrafe(tmp_path: Path, offset: float) -> None:
    mesh = dreieck(tmp_path, 2.0)
    reduced = replace(mesh, positions=mesh.positions + [0, 0, offset])
    stats = {}
    bound, measured = _fehlergrenze(mesh, reduced, rho=0.01, messung=stats)
    old = np.linalg.norm(mesh.positions - mesh.positions.mean(axis=0), axis=1).max() + offset
    assert offset <= bound <= offset + 0.01 + 1e-12
    assert measured == pytest.approx(offset)
    assert bound < old / 10
    assert stats["surfaceBoundCappedTriangles"] == 0
    assert stats["surfaceBoundQueryPoints"] == 1 + stats["surfaceBoundMaxSubdivision"] ** 2


@pytest.mark.parametrize(("subdivisions", "budget"), [(4, 1000), (256, 10), (256, 1)])
def test_deckel_bleibt_konservativ(tmp_path: Path, subdivisions: int, budget: int) -> None:
    mesh = dreieck(tmp_path, 57)
    reduced = replace(mesh, positions=mesh.positions + [0, 0, 0.05])
    stats = {}
    bound, _ = _fehlergrenze(
        mesh, reduced, rho=0.01, max_unterteilung=subdivisions,
        max_pruefpunkte=budget, messung=stats,
    )
    radius = np.linalg.norm(mesh.positions - mesh.positions.mean(axis=0), axis=1).max()
    assert bound == pytest.approx(0.05 + radius / stats["surfaceBoundMaxSubdivision"])
    assert bound >= 0.05
    assert stats["surfaceBoundQueryPoints"] <= budget
    assert stats["surfaceBoundCappedTriangles"] == 1


def test_gemischte_unterteilungen_und_degenerierte_flaeche(tmp_path: Path) -> None:
    mesh = dreieck(tmp_path)
    positions = np.concatenate([mesh.positions * scale + [3 * i, 0, 0]
                                for i, scale in enumerate([0, 0.001, 0.1, 2])])
    mesh = replace(mesh, positions=positions, triangles=np.arange(12).reshape(-1, 3),
                   materials=np.zeros(4, dtype=np.int32))
    stats = {}
    bound, _ = _fehlergrenze(mesh, mesh, rho=0.01, messung=stats)
    assert bound <= 0.01 + 1e-12
    assert 4 < stats["surfaceBoundQueryPoints"] <= 1_000_000


def test_texturdichte_und_warnung_im_log(tmp_path: Path) -> None:
    path = quelle(tmp_path / "quelle", n=48)
    result = kacheln(path, tmp_path / "ausgabe", max_dreiecke=3000, blatt_dreiecke=2500)
    inner = [m for m in result["measurements"] if m["phase"] == "kachel" and not m["leaf"]]
    assert inner and inner[0]["triangles"] > 1000
    for entry in inner:
        assert entry["textureTexelsPerM"] > 0
        assert entry["surfaceBoundQueryPoints"] <= 1_000_000
        assert entry["surfaceBoundSeconds"] > 0
        assert entry["surfaceErrorBoundM"] < 0.1
        if any(min(p["size"]) < 256 for p in entry["texturePages"]):
            assert any(m["phase"] == "warnung" and m["uri"] == entry["uri"]
                       for m in result["measurements"])


def test_verschwundene_duenne_struktur(tmp_path: Path) -> None:
    from scanview_import.kacheln import _lod_fehler

    base = dreieck(tmp_path)
    # Ein dünner, senkrechter Streifen verschwindet vollständig aus der Darstellung.
    positions = np.concatenate([base.positions, [[0, 0, 0], [0.001, 0, 0], [0, 0, 2]]])
    source = replace(base, positions=positions, triangles=np.array([[0, 1, 2], [3, 4, 5]]),
                     materials=np.zeros(2, dtype=np.int32))
    stats = {}
    forward, _, reverse = _lod_fehler(source, base, stats)
    assert forward < 0.02
    assert 2 <= reverse < 2.1
    assert stats["sourceErrorBoundM"] == reverse
    assert max(forward, reverse) >= 2  # Wahrer Hausdorff-Abstand der Spitze zur Ebene.


def test_geometric_error_ignoriert_meshopt_schaetzung(tmp_path: Path, monkeypatch) -> None:
    import scanview_import.kacheln as module

    original = module._vereinfachen

    def grobe_schaetzung(*args, **kwargs):
        reduced, _ = original(*args, **kwargs)
        return reduced, 50.0

    monkeypatch.setattr(module, "_vereinfachen", grobe_schaetzung)
    path = quelle(tmp_path / "quelle", n=12)
    result = kacheln(path, tmp_path / "ausgabe", max_dreiecke=180, blatt_dreiecke=150)
    inner = [m for m in result["measurements"] if m["phase"] == "kachel" and not m["leaf"]]
    assert inner
    for entry in inner:
        assert entry["resultErrorM"] == 50
        assert 0 < entry["geometricError"] < 0.2
        assert entry["geometricError"] >= max(
            entry["surfaceErrorBoundM"], entry["sourceErrorBoundM"],
        )
        assert entry["simplificationStage"] in ("lock", "prune", "sloppy")


@pytest.mark.parametrize("error", [0.5, 1.0, 2.0])
def test_texturdichte_aus_bildfehler(tmp_path: Path, error: float) -> None:
    from scanview_import.kacheln.texturen import BildCache, zuschneiden

    mesh = read_mesh(quelle(tmp_path / "quelle", n=4), unit="m", up="z")
    stats = {}
    cache = BildCache(1024**2)
    try:
        zuschneiden(mesh, error, cache=cache, messung=stats)
    finally:
        cache.schliessen()
    assert stats["textureTexelsPerM"] == pytest.approx(8 / error)
    assert all(max(p["size"]) <= 4096 for p in stats["texturePages"])


def test_adaptives_budget_kleine_kachel(tmp_path: Path) -> None:
    from scanview_import.kacheln import _lod_fehler

    mesh = read_mesh(quelle(tmp_path / "quelle", n=48), unit="m", up="z")
    # Größere metrische Ausdehnung bei identischer, kleiner Dreieckszahl.
    mesh = replace(mesh, positions=(mesh.positions - mesh.positions.mean(axis=0)) * 40)
    stats = {}
    forward, _, reverse = _lod_fehler(mesh, mesh, stats)
    assert 0 < max(forward, reverse) < 1
    assert stats["surfaceBoundRhoM"] > 0.01
    assert stats["surfaceBoundQueryPoints"] <= 200_000
    assert stats["reverseSurfaceBoundQueryPoints"] <= 200_000
    assert stats["surfaceBoundCappedTriangles"] == 0
    assert stats["reverseSurfaceBoundCappedTriangles"] == 0
    assert stats["bidirectionalBoundSeconds"] < 15


def test_radiusklassen_finden_grosse_flaeche_trotz_naher_splitter(tmp_path: Path) -> None:
    from scanview_import.kacheln import _ObereDistanz
    from scanview_import.mesh_surface import SurfaceIndex

    base = dreieck(tmp_path, 100)
    # Große Fläche enthält den Punkt, 16 nahe Mittelpunkte liegen oberhalb davon.
    small = np.array([[[0, 0, 1], [0.01, 0, 1], [0, 0.01, 1]]])
    triangles = np.concatenate([base.positions[base.triangles], np.repeat(small, 16, axis=0)])
    mesh = replace(base, positions=triangles.reshape(-1, 3),
                   triangles=np.arange(51).reshape(-1, 3), materials=np.zeros(17, dtype=np.int32))
    points = np.array([[0.001, 0.001, 0]])
    exact = SurfaceIndex(mesh.positions, mesh.triangles).distance(points)
    upper = _ObereDistanz(mesh).distance(points)
    assert exact[0] < 1e-12
    assert upper[0] == pytest.approx(exact[0], abs=1e-12)
    assert upper[0] >= exact[0] - 1e-12


@pytest.mark.parametrize(("maximum", "stage"), [(60, "lock"), (20, "relax")])
def test_meshopt_stufe_und_metrische_skalierung(tmp_path: Path, maximum: int, stage: str) -> None:
    from scanview_import.kacheln import _vereinfachen

    mesh = read_mesh(quelle(tmp_path / "quelle", n=12), unit="m", up="z")
    local = mesh.positions - mesh.positions.mean(axis=0)
    errors = []
    for scale in (1, 10):
        stats = {}
        reduced, error = _vereinfachen(
            replace(mesh, positions=local * scale), 20, maximum, messung=stats,
        )
        assert stats["simplificationStage"] == stage
        assert 0 < len(reduced.triangles) <= maximum
        errors.append(error)
    assert errors[1] == pytest.approx(10 * errors[0], rel=1e-4)


def test_sloppy_null_trotz_randverlust(tmp_path: Path) -> None:
    from scanview_import.kacheln import _vereinfachen
    from scanview_import.mesh_surface import SurfaceIndex

    mesh = read_mesh(quelle(tmp_path / "quelle", n=12), unit="m", up="z")
    positions = mesh.positions[mesh.triangles].reshape(-1, 3).copy()
    positions -= positions.mean(axis=0)
    positions[:, 2] = 0
    # Eigene Grafikvertices je Dreieck: Clustering kann den ebenen Rand verlieren.
    mesh = replace(mesh, positions=positions, uv=None,
                   triangles=np.arange(len(positions)).reshape(-1, 3))
    import meshoptimizer

    from scanview_import.mesh_stages import compact

    destination = np.empty(mesh.triangles.size, dtype=np.uint32)
    measured = np.zeros(1, dtype=np.float32)
    count = meshoptimizer.simplify_sloppy(
        destination, mesh.triangles.ravel().astype(np.uint32),
        mesh.positions.astype(np.float32), target_index_count=60,
        target_error=1, result_error=measured,
    )
    faces = destination[:count].reshape(-1, 3)
    reduced = compact(mesh, faces, np.zeros(len(faces), dtype=np.int32))
    assert measured[0] == 0
    actual = SurfaceIndex(reduced.positions, reduced.triangles).distance(mesh.positions).max()
    assert actual > 0.45
    # 288 geometrische Dreiecke berühren sich an ihren Positionen und sind somit
    # ein Bauteil, auch wenn jedes Dreieck eigene Grafikvertices hat.
    protected, _ = _vereinfachen(mesh, 20, 20)
    assert 0 < len(protected.triangles) <= 20


def test_rueckwaerts_kindflaeche_groesser_als_punktbudget(tmp_path: Path) -> None:
    # Die Kindfläche (bis zu acht volle Kachelbudgets) darf das Prüfpunktbudget übersteigen.
    mesh = dreieck(tmp_path)
    positions = np.concatenate([mesh.positions + [2 * i, 0, 0] for i in range(40)])
    children = replace(mesh, positions=positions, triangles=np.arange(120).reshape(-1, 3),
                       materials=np.zeros(40, dtype=np.int32))
    reduced = replace(children, triangles=children.triangles[:10],
                      materials=children.materials[:10])
    stats = {}
    forward, _, reverse = _lod_fehler(children, reduced, stats, max_pruefpunkte=20)
    # Vorwärts ist wegen des kleinen Budgets gedeckelt: grob, aber unter dem Verlust.
    assert forward < 1.0 < 2.0 <= reverse
    assert stats["sourceErrorBoundM"] == reverse


def test_dichte_ignoriert_splitter(tmp_path: Path) -> None:
    from scanview_import.kacheln.texturen import _dichte

    mesh = dreieck(tmp_path)
    # 1-m-Dreieck mit 100 Texel/m; dazu ein Splitter mit µm-Kante, der einen Texel überspannt.
    positions = np.array(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [5, 0, 0], [5 + 1e-6, 0, 0], [5, 1e-6, 0]]
    )
    uv = np.array([[0, 0], [100, 0], [0, 100], [200, 0], [201, 0], [200, 1e-6]]) / 256
    sliver = replace(mesh, positions=positions.astype(float), uv=uv,
                     triangles=np.arange(6).reshape(-1, 3), materials=np.zeros(2, dtype=np.int32))
    assert _dichte(sliver, np.array([256, 256])) == pytest.approx(100, rel=1e-3)
