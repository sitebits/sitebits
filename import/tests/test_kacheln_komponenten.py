"""Bauteilerhalt bei gemeinsamem, knappem Dreiecksbudget."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from scanview_import.kacheln import _komponenten, _lod_fehler, _vereinfachen, _zusammen
from scanview_import.mesh_source import Mesh, read_mesh
from scanview_import.mesh_surface import SurfaceIndex
from tests.test_kacheln import quelle


def bauteile(tmp_path: Path) -> Mesh:
    mesh = read_mesh(quelle(tmp_path / "quelle", n=12), unit="m", up="z")
    positions = mesh.positions - mesh.positions.mean(axis=0)
    positions[:, 2] = 0
    return _zusammen([
        replace(mesh, positions=positions * size + [offset, 0, 0])
        for size, offset in [(4, 0), (1, 20), (0.3, 40)]
    ])


@pytest.mark.parametrize("budget", [4, 6, 10])
def test_keine_grosse_komponente_verloren(tmp_path: Path, budget: int) -> None:
    mesh = bauteile(tmp_path)
    stats = {}
    reduced, _ = _vereinfachen(mesh, budget, budget, messung=stats)
    assert 0 < len(reduced.triangles) <= budget
    centers = reduced.positions[reduced.triangles].mean(axis=1)
    for offset in (0, 20, 40):
        assert np.any(np.abs(centers[:, 0] - offset) < 5)
    forward, _, reverse = _lod_fehler(mesh, reduced, {})
    exact = SurfaceIndex(reduced.positions, reduced.triangles).distance(mesh.positions).max()
    assert reverse >= exact - 1e-12
    assert reverse < 2  # Negativkontrolle: der alte Bauteilverlust liegt bei 19,3 m.
    assert forward < 1


def test_unmoeglicher_bauteilerhalt_lehnt_ab(tmp_path: Path) -> None:
    mesh = bauteile(tmp_path)
    with pytest.raises(ValueError, match="3 Bauteile nicht erreichbar"):
        _vereinfachen(mesh, 2)


def test_uv_naehte_trennen_keine_bauteile(tmp_path: Path) -> None:
    mesh = bauteile(tmp_path)
    split = replace(
        mesh, positions=mesh.positions[mesh.triangles].reshape(-1, 3),
        triangles=np.arange(mesh.triangles.size).reshape(-1, 3),
        uv=mesh.uv[mesh.triangles].reshape(-1, 2),
    )
    labels, sizes = _komponenten(split)
    assert len(np.unique(labels)) == 3
    assert np.sort(sizes) == pytest.approx(np.sqrt(2) * np.array([0.6, 2, 8]))
    reduced, _ = _vereinfachen(split, 6)
    assert len(reduced.triangles) <= 6
    assert len(np.unique(_komponenten(reduced)[0])) == 3


def test_rueckwaertsschranke_gegen_exakten_rechteckverlust(tmp_path: Path) -> None:
    mesh = bauteile(tmp_path)
    keep = mesh.positions[mesh.triangles].mean(axis=1)[:, 0] < 30
    reduced = replace(mesh, triangles=mesh.triangles[keep], materials=mesh.materials[keep])
    # Drittes Rechteck [39,7;40,3] liegt neben [19;21]. Der weiteste Punkt
    # hat exakt 19,3 m Abstand; y liegt vollständig innerhalb des zweiten Rechtecks.
    exact = SurfaceIndex(reduced.positions, reduced.triangles).distance(mesh.positions).max()
    assert exact == pytest.approx(19.3)
    forward, _, reverse = _lod_fehler(mesh, reduced, {})
    assert forward < 0.2
    assert exact <= reverse < exact + 0.2


def test_kleine_splitter_ohne_mindestbudget(tmp_path: Path) -> None:
    mesh = bauteile(tmp_path)
    labels, _ = _komponenten(mesh)
    positions = mesh.positions.copy()
    vertices = np.unique(mesh.triangles[labels == 2])
    positions[vertices] = (positions[vertices] - [40, 0, 0]) * 1e-4 + [40, 0, 0]
    mesh = replace(mesh, positions=positions)
    stats = {}
    reduced, _ = _vereinfachen(mesh, 2, messung=stats)
    assert len(reduced.triangles) <= 2
    assert stats["protectedComponents"] == 2
    centers = reduced.positions[reduced.triangles].mean(axis=1)
    assert np.any(centers[:, 0] < 5)
    assert np.any(np.abs(centers[:, 0] - 20) < 2)


def test_geschlossene_bauteile_bleiben_bei_prune_erhalten(tmp_path: Path) -> None:
    mesh = bauteile(tmp_path)
    octahedron = np.concatenate([np.eye(3), -np.eye(3)])
    faces = np.array([
        [0, 1, 2], [1, 3, 2], [3, 4, 2], [4, 0, 2],
        [1, 0, 5], [3, 1, 5], [4, 3, 5], [0, 4, 5],
    ], dtype=np.uint32)
    mesh = replace(
        mesh, positions=np.concatenate([octahedron * scale + [x, 0, 0]
                                        for scale, x in [(4, 0), (1, 20), (0.3, 40)]]),
        triangles=np.concatenate([faces + i * 6 for i in range(3)]),
        uv=None, materials=np.zeros(24, dtype=np.int32),
    )
    reduced, _ = _vereinfachen(mesh, 6)
    assert len(reduced.triangles) <= 6
    centers = reduced.positions[reduced.triangles].mean(axis=1)
    for offset in (0, 20, 40):
        assert np.any(np.abs(centers[:, 0] - offset) < 5)


def test_verbindungsdreieck_zaehlt_nicht_als_bauteilerhalt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scanview_import.kacheln as module
    from scanview_import.mesh_stages import compact

    mesh = bauteile(tmp_path)
    original = module._vereinfachen_kandidat
    first = True

    def clustering(part, *args, **kwargs):
        nonlocal first
        if first:
            first = False
            faces = np.array([[mesh.triangles[i, 0] for i in (0, 288, 576)]])
            kwargs["messung"]["simplificationStage"] = "sloppy"
            return compact(mesh, faces, np.zeros(1, dtype=np.int32)), 0.0
        return original(part, *args, **kwargs)

    monkeypatch.setattr(module, "_vereinfachen_kandidat", clustering)
    stats = {}
    reduced, _ = _vereinfachen(mesh, 6, messung=stats)
    assert stats["simplificationStage"] == "components"
    triangles = reduced.positions[reduced.triangles]
    assert np.all(np.ptp(triangles[:, :, 0], axis=1) < 10)
    assert len(np.unique(_komponenten(reduced)[0])) == 3


def test_leere_kandidaten_behalten_quellflaeche_ohne_erfundene_schaetzung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scanview_import.kacheln as module

    def leer(*args, **kwargs):
        raise ValueError("Dreiecksbudget nicht erreichbar")

    monkeypatch.setattr(module, "_vereinfachen_kandidat", leer)
    mesh = bauteile(tmp_path)
    stats = {}
    reduced, error = _vereinfachen(mesh, 6, messung=stats)
    assert len(reduced.triangles) == stats["representativeComponents"] == 3
    assert len(np.unique(_komponenten(reduced)[0])) == 3
    assert error == 0  # Kein meshopt-Ergebnis; die Flächenschranke misst separat.
    _, _, reverse = _lod_fehler(mesh, reduced, {})
    actual = SurfaceIndex(reduced.positions, reduced.triangles).distance(mesh.positions).max()
    assert reverse >= actual
    assert reverse > 1
