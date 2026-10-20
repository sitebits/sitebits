import json

import laspy
import numpy as np
import pye57
import pytest

from scanview_import.convert import convert
from scanview_import.manifest_io import cloud_of, read_manifest, write_manifest
from scanview_import.verify import CELL_SIZE_M, QUERY_MARGIN_M, _sample_distances, verify


def _strip_copc_vlrs(las: laspy.LasData) -> None:
    """laspy verweigert das Zurückschreiben eines COPC-VLR/-EVLR
    (``CopcInfoVlr``/``CopcHierarchyVlr.record_data_bytes``: "Writing COPC is not
    supported") — vor jedem ``.write()`` einer zuvor gelesenen COPC müssen sie entfernt
    werden. Das Ergebnis ist danach eine reine (nicht mehr COPC-gültige) LAZ-Datei."""
    for vlr in [v for v in las.vlrs if getattr(v, "user_id", None) == "copc"]:
        las.vlrs.remove(vlr)
    for vlr in [v for v in (las.evlrs or []) if getattr(v, "user_id", None) == "copc"]:
        las.evlrs.remove(vlr)


@pytest.mark.docker
def test_verify_ok_on_fixture(two_scan_e57, tmp_path):
    pkg = tmp_path / "pkg"
    convert(two_scan_e57.path, pkg, None, None)
    r = verify(two_scan_e57.path, pkg, samples=500)
    assert r["ok"] is True
    assert r["pointBalance"]["invalidDropped"] == 14
    assert r["samples"]["maxErrorM"] <= 0.0015
    assert r["scansRepresented"]["found"] == 2
    assert json.loads((pkg / "conversion-report.json").read_text()) == r


@pytest.mark.docker
@pytest.mark.parametrize("shift", [0.05, 1000.0])
def test_verify_detects_tampered_cloud(two_scan_e57, tmp_path, shift):
    pkg = tmp_path / "pkg"
    convert(two_scan_e57.path, pkg, None, None)
    las = laspy.read(pkg / "cloud.copc.laz")
    las.x = las.x + shift  # absichtlich verschoben
    _strip_copc_vlrs(las)
    las.write(pkg / "cloud.copc.laz")

    r = verify(two_scan_e57.path, pkg, samples=500)
    assert r["samples"]["ok"] is False
    assert r["ok"] is False
    if shift > QUERY_MARGIN_M:
        # Fallback-Query findet für keinen Sample-Punkt mehr einen Nachbarn (alle Abstände
        # unendlich) — darf nicht als Infinity/NaN im JSON landen (allow_nan=False in
        # verify() würde sonst laut brechen), sondern muss als null auftauchen.
        assert r["samples"]["n"] > 0
        assert r["samples"]["maxErrorM"] is None
        assert r["samples"]["p99ErrorM"] is None
    assert json.loads((pkg / "conversion-report.json").read_text()) == r


@pytest.mark.docker
def test_verify_detects_points_silently_missing_from_copc(two_scan_e57, tmp_path):
    """Task-4-Concern 6: gemischte Quellen könnten gültige Punkte still verlieren, ohne
    dass `readerPointCount - pointCount == invalidDropped` (Manifest-Eigenarithmetik) das
    je bemerken würde — beide Werte stammen aus demselben, dann falschen Header. Nur der
    Abgleich gegen die aus dem E57 unabhängig gezählten gültigen Punkte deckt das auf."""
    pkg = tmp_path / "pkg"
    convert(two_scan_e57.path, pkg, None, None)
    copc = pkg / "cloud.copc.laz"
    las = laspy.read(copc)
    assert las.header.point_count == 1986
    las.points = las.points[10:]  # 10 Punkte "verschwinden" aus der COPC
    _strip_copc_vlrs(las)
    las.write(copc)

    m = read_manifest(pkg)
    cloud = cloud_of(m)
    cloud["pointCount"] = 1976
    cloud["invalidDropped"] = 2000 - 1976
    write_manifest(pkg, m)

    r = verify(two_scan_e57.path, pkg, samples=500)
    assert r["pointBalance"]["invalidDropped"] == 24
    assert r["pointBalance"]["ok"] is False
    assert r["ok"] is False


@pytest.mark.docker
def test_verify_flags_unscaled_8bit_rgb(two_scan_e57, tmp_path):
    """Convert() skaliert 8-bit-RGB x257 auf 16 bit. Regression, falls das mal
    ausbleibt (z. B. künftiger Refactor) — verify muss das an der COPC selbst erkennen, nicht
    nur am Fixture-Wissen, denn genau dieser Fall (unskaliertes 8-bit-RGB in 16-bit-Feldern)
    hat den Web-Viewer nahezu schwarz rendern lassen (Task-9-Befund)."""
    pkg = tmp_path / "pkg"
    convert(two_scan_e57.path, pkg, None, None)  # skaliert korrekt (siehe test_convert.py)
    las = laspy.read(pkg / "cloud.copc.laz")
    las.red = las.red // 257  # simuliert eine Regression: wieder unskaliertes 8-bit-RGB
    las.green = las.green // 257
    las.blue = las.blue // 257
    _strip_copc_vlrs(las)
    las.write(pkg / "cloud.copc.laz")

    r = verify(two_scan_e57.path, pkg, samples=500)
    assert r["attributes"]["rgbMax"] <= 255
    assert r["attributes"]["ok"] is False
    assert r["ok"] is False


@pytest.mark.docker
def test_verify_attributes_ok_when_rgb_correctly_scaled(two_scan_e57, tmp_path):
    pkg = tmp_path / "pkg"
    convert(two_scan_e57.path, pkg, None, None)
    r = verify(two_scan_e57.path, pkg, samples=500)
    assert r["attributes"]["rgbMax"] > 255
    assert r["attributes"]["ok"] is True


def test_sample_distances_query_boxes_are_bounded_independent_of_sample_extent():
    """Review-Fix: eine einzelne Bbox über eine weit gestreute Stichprobe (wie sie ein
    ortsweiter Scan mit ~1 Mrd. Punkten liefert) würde beim COPC-Query fast die ganze Site
    treffen. `_sample_distances` muss stattdessen in festen Kacheln queryen — die Boxgröße
    darf nicht von der Ausdehnung der Stichprobe abhängen. Kein Docker nötig: reine
    Geometrie-Funktion, `query_near` wird durch eine Spy-Funktion ersetzt."""
    boxes: list[tuple[np.ndarray, np.ndarray]] = []

    def spy_query_near(lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        boxes.append((np.array(lo), np.array(hi)))
        # kein Nachbar nötig, es geht nur um die Boxgröße
        return np.empty((0, 3)), np.empty(0, dtype=np.uint16)

    rng = np.random.default_rng(0)
    # 200 Punkte über 100 m gestreut — eine Sample-Bbox über alle wäre ~100 m groß.
    pick = rng.uniform(0.0, 100.0, size=(200, 3))
    dist, own = _sample_distances(pick, spy_query_near)

    assert len(boxes) > 1  # tatsächlich mehrere Kacheln, nicht eine große Bbox
    max_edge = CELL_SIZE_M + 2 * QUERY_MARGIN_M
    for lo, hi in boxes:
        edge = hi - lo
        assert np.allclose(edge, max_edge), f"Kachel-Box {lo}..{hi} weicht von der festen Größe ab"
    # ohne Nachbarn bleibt jede Kachel unendlich (unverändertes Verhalten ggü. vorher)
    assert np.all(np.isinf(dist))
    assert len(dist) == len(pick)
    assert own is None  # ohne own_id keine Eigen-Scan-Abstände


@pytest.mark.docker
def test_verify_handles_zero_scans_without_crashing(two_scan_e57, tmp_path, monkeypatch):
    """Ein E57 ohne Scans darf nicht mit einer ZeroDivisionError abbrechen
    (`samples // scan_count`) und keine Vacuous-Truth in `scansRepresented` liefern
    (`found == scan_count` wäre bei 0 == 0 sonst fälschlich True)."""
    pkg = tmp_path / "pkg"
    convert(two_scan_e57.path, pkg, None, None)
    monkeypatch.setattr(pye57.E57, "scan_count", property(lambda self: 0))

    r = verify(two_scan_e57.path, pkg, samples=500)
    assert r["scansRepresented"] == {"expected": 0, "found": 0, "ok": False}
    assert r["samples"]["ok"] is False
    assert r["ok"] is False
    assert json.loads((pkg / "conversion-report.json").read_text()) == r


# --- PointSourceId-Prüfung ---------------------------------------------------------------------


def test_sample_distances_own_id_only_considers_points_of_that_scan():
    near = np.array([[0.0, 0.0, 0.0], [0.5, 0.0, 0.0]])
    ids = np.array([2, 1], dtype=np.uint16)

    def query(lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return near, ids

    pick = np.array([[0.0, 0.0, 0.0]])
    dist, own = _sample_distances(pick, query, own_id=1)
    assert dist[0] == 0.0
    assert own is not None and own[0] == pytest.approx(0.5)
    _, own3 = _sample_distances(pick, query, own_id=3)
    assert own3 is not None and np.isinf(own3[0])


@pytest.mark.docker
def test_verify_checks_point_source_ids(two_scan_e57, tmp_path):
    pkg = tmp_path / "pkg"
    convert(two_scan_e57.path, pkg, None, None)
    r = verify(two_scan_e57.path, pkg, samples=500)
    ps = r["pointSource"]
    assert ps["checked"] is True
    assert ps["zeroIds"] == 0
    assert ps["expectedPerScan"] == ps["foundPerScan"] == [993, 993]
    assert ps["countsOk"] is True
    assert ps["samplesOwnScanMaxErrorM"] <= 0.0015
    assert ps["ok"] is True
    assert r["ok"] is True


def _rewrite_ids(pkg, fn) -> None:
    las = laspy.read(pkg / "cloud.copc.laz")
    las.point_source_id = fn(np.asarray(las.point_source_id).copy())
    _strip_copc_vlrs(las)
    las.write(pkg / "cloud.copc.laz")


@pytest.mark.docker
def test_verify_detects_swapped_scan_ids(two_scan_e57, tmp_path):
    # Beide Scans haben 993 gültige Punkte — die Zählung bleibt gleich, nur die Stichprobe
    # (nächster Punkt mit der EIGENEN ID) deckt die Vertauschung auf.
    pkg = tmp_path / "pkg"
    convert(two_scan_e57.path, pkg, None, None)
    _rewrite_ids(pkg, lambda ids: 3 - ids)
    r = verify(two_scan_e57.path, pkg, samples=500)
    assert r["pointSource"]["countsOk"] is True
    assert r["pointSource"]["ok"] is False
    assert r["samples"]["ok"] is True  # Geometrie stimmt weiterhin
    assert r["ok"] is False
    assert json.loads((pkg / "conversion-report.json").read_text()) == r


@pytest.mark.docker
def test_verify_detects_shifted_scan_boundary(two_scan_e57, tmp_path):
    # Grenze um 10 Punkte verschoben (typischer Fehler bei falscher Rohpunktzahl).
    pkg = tmp_path / "pkg"
    convert(two_scan_e57.path, pkg, None, None)

    def shift(ids: np.ndarray) -> np.ndarray:
        ids[np.flatnonzero(ids == 2)[:10]] = 1
        return ids

    _rewrite_ids(pkg, shift)
    r = verify(two_scan_e57.path, pkg, samples=500)
    assert r["pointSource"]["foundPerScan"] == [1003, 983]
    assert r["pointSource"]["countsOk"] is False
    assert r["pointSource"]["ok"] is False
    assert r["ok"] is False


@pytest.mark.docker
def test_verify_detects_unassigned_points_when_flag_set(two_scan_e57, tmp_path):
    pkg = tmp_path / "pkg"
    convert(two_scan_e57.path, pkg, None, None)

    def zero_some(ids: np.ndarray) -> np.ndarray:
        ids[:3] = 0
        return ids

    _rewrite_ids(pkg, zero_some)
    r = verify(two_scan_e57.path, pkg, samples=500)
    assert r["pointSource"]["zeroIds"] == 3
    assert r["pointSource"]["ok"] is False
    assert r["ok"] is False


@pytest.mark.docker
def test_verify_old_package_without_flag_is_not_checked_but_ok(two_scan_e57, tmp_path):
    """Pakete ohne Scan-Zuordnung bleiben gültig (Viewer warnt) — verify meldet
    „nicht geprüft“, bricht aber nicht."""
    pkg = tmp_path / "pkg"
    convert(two_scan_e57.path, pkg, None, None)
    _rewrite_ids(pkg, lambda ids: np.zeros_like(ids))
    m = read_manifest(pkg)
    del cloud_of(m)["pointSourceIsScan"]
    write_manifest(pkg, m)
    r = verify(two_scan_e57.path, pkg, samples=500)
    assert r["pointSource"]["checked"] is False
    assert r["pointSource"]["ok"] is True
    assert r["pointSource"]["zeroIds"] == 1986
    assert r["ok"] is True
