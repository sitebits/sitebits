"""Metashape-Export des Genauigkeitsnachweises mit API-Stub (ohne installiertes Metashape)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from metashape.genauigkeit_exportieren import _quantil, export_genauigkeit

from scanview_import import genauigkeit

FIDUCIAL = object()


class Matrix:
    """Intern → geozentrisch: Maßstab 1,001 und Verschiebung (wie ein Chunk-Transform)."""

    def mulp(self, p: Any) -> list[float]:
        return [1.001 * v + o for v, o in zip(p, (368000.0, 5807000.0, 0.0), strict=True)]


class Api:
    Vector = list
    Marker = SimpleNamespace(Type=SimpleNamespace(Fiducial=FIDUCIAL))
    app = SimpleNamespace(document=None)

    class CoordinateSystem:
        def __init__(self, definition: str) -> None:
            self.definition = definition

        @staticmethod
        def transform(p: Any, quelle: Any, ziel: Any) -> list[float]:
            return list(p)  # Stub: alle Systeme deckungsgleich


def _marker(
    label: str, ref: list[float] | None, enabled: bool, pos: list[float] | None, typ: Any = None
) -> Any:
    reference = SimpleNamespace(location=ref, accuracy=None, enabled=enabled)
    return SimpleNamespace(
        label=label, reference=reference, position=pos, projections={1: 0, 2: 0, 3: 0}, type=typ
    )


def _kamera(gruppe: str, ref: list[float], center: list[float] | None) -> Any:
    return SimpleNamespace(
        group=SimpleNamespace(label=gruppe),
        transform=object(),
        center=center,
        reference=SimpleNamespace(location=ref, accuracy=None, enabled=True),
    )


def _chunk() -> Any:
    m1 = _marker("P1", [368001.001, 5807001.001, 1.001], True, [1.0, 1.0, 1.0])
    k1 = _marker("K1", [368010.0, 5807000.0, 0.0], False, [10.0, 0.05, 0.0])
    ohne = _marker("K2", [368020.0, 5807000.0, 0.0], False, None)
    keine_ref = _marker("frei", None, False, [3.0, 3.0, 3.0])
    fid = _marker("fid", [1.0, 1.0, 1.0], True, [1.0, 1.0, 1.0], FIDUCIAL)
    strecke = SimpleNamespace(
        label="A-B",
        point0=m1,
        point1=k1,
        reference=SimpleNamespace(distance=9.0, accuracy=0.02, enabled=False),
    )
    ohne_soll = SimpleNamespace(
        label="leer",
        point0=m1,
        point1=k1,
        reference=SimpleNamespace(distance=None, accuracy=None, enabled=False),
    )
    kameras = [
        _kamera("Nadir", [368000.0, 5807000.0, 50.0], [3.0, 4.0, 49.0]),  # xy 5,005, dz -0,951
        _kamera("Nadir", [368000.0, 5807000.0, 50.0], [0.0, 0.0, 50.0]),  # xy 0, dz +0,05
        _kamera("Fassade", [368000.0, 5807000.0, 0.0], None),  # nicht ausgerichtet
    ]
    return SimpleNamespace(
        crs=SimpleNamespace(geoccs="geo"),
        transform=SimpleNamespace(matrix=Matrix()),
        markers=[m1, k1, ohne, keine_ref, fid],
        scalebars=[strecke, ohne_soll],
        cameras=kameras,
        marker_location_accuracy=[0.005, 0.005, 0.01],
        scalebar_accuracy=0.001,
    )


def test_export_rollen_und_koordinaten() -> None:
    d = export_genauigkeit(_chunk(), 25833, "ellipsoidisch", api=Api)
    namen = {m["name"]: m for m in d["markers"]}
    assert set(namen) == {"P1", "K1", "K2"}  # ohne Referenz und Fiducial fehlen
    assert namen["P1"]["role"] == "control"
    assert namen["K1"]["role"] == "check"
    assert namen["K1"]["estimated"] == pytest.approx([368010.01, 5807000.05005, 0.0])
    assert namen["K2"]["estimated"] is None
    assert namen["K1"]["referenceAccuracy"] == [0.005, 0.005, 0.01]  # Chunk-Standard
    assert namen["K1"]["images"] == 3


def test_export_strecke_aus_geozentrischen_endpunkten() -> None:
    d = export_genauigkeit(_chunk(), 25833, "ellipsoidisch", api=Api)
    (s,) = d["scalebars"]  # ohne Sollwert fehlt
    intern = ((10 - 1) ** 2 + (0.05 - 1) ** 2 + 1) ** 0.5
    assert s["estimated"] == pytest.approx(1.001 * intern)
    assert s["role"] == "check"
    assert s["referenceAccuracy"] == 0.02


def test_pass_strecke_wird_control() -> None:
    d = export_genauigkeit(_chunk(), 25833, "ellipsoidisch", pass_strecken=["A-B"], api=Api)
    assert d["scalebars"][0]["role"] == "control"
    with pytest.raises(ValueError, match="unbekannt"):
        export_genauigkeit(_chunk(), 25833, "ellipsoidisch", pass_strecken=["X"], api=Api)


def test_kameragruppen() -> None:
    (g,) = export_genauigkeit(_chunk(), 25833, "ellipsoidisch", api=Api)["cameraGroups"]
    assert g["name"] == "Nadir"
    assert g["n"] == 2
    assert g["xyMedian"] == pytest.approx((0 + 5.005) / 2)
    assert g["dzMedian"] == pytest.approx((-0.951 + 0.05) / 2)


def test_quantil_wie_numpy() -> None:
    werte = [3.0, 1.0, 2.0, 10.0]
    assert _quantil(werte, 0.5) == pytest.approx(2.5)
    assert _quantil(werte, 0.95) == pytest.approx(8.95)


def test_ungueltiger_chunk_oder_epsg() -> None:
    with pytest.raises(ValueError, match="Chunk"):
        export_genauigkeit(None, 25833, "ellipsoidisch", api=Api)
    with pytest.raises(ValueError, match="EPSG"):
        export_genauigkeit(_chunk(), 0, "ellipsoidisch", api=Api)


def test_rundlauf_mit_importer(tmp_path: Path) -> None:
    d = export_genauigkeit(_chunk(), 25833, "ellipsoidisch", api=Api)
    d["source"] = {
        "software": "Agisoft Metashape Professional",
        "version": "2.3.1",
        "created": "2026-10-09T20:00:00Z",
        "projectSha256": "c" * 64,
    }
    d["report"] = None
    p = tmp_path / "genauigkeit.json"
    p.write_text(json.dumps(d, allow_nan=False))
    acc = genauigkeit.bewerte(genauigkeit.lade(p))
    assert acc["absolute"]["status"] == "checked"
    assert acc["absolute"]["withoutEstimate"] == 1
    assert acc["scale"]["status"] in ("checked", "deviates")
