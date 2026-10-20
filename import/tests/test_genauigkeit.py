"""Genauigkeitsnachweis (F15 Teil 1): genauigkeit.json prüfen, bewerten, ins Paket schreiben."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from scanview_import import cli, genauigkeit, hoehen
from scanview_import.manifest_io import read_manifest

POTSDAM = (368000.0, 5807000.0)


def _marker(
    name: str, rolle: str, fehler: tuple[float, float, float], i: int = 0
) -> dict[str, Any]:
    soll = [POTSDAM[0] + 10 * i, POTSDAM[1] + 5 * i, 80.0 + i]
    return {
        "name": name,
        "role": rolle,
        "reference": soll,
        "referenceAccuracy": [0.01, 0.01, 0.02],
        "estimated": [s + f for s, f in zip(soll, fehler, strict=True)],
        "images": 6,
    }


def _daten(**ueber: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "formatVersion": 1,
        "crs": {"epsg": 25833, "verticalDatum": "dhhn2016"},
        "source": {
            "software": "Agisoft Metashape Professional",
            "version": "2.3.1",
            "created": "2026-10-09T20:00:00Z",
            "projectSha256": "a" * 64,
        },
        "markers": [
            _marker("P1", "control", (0.002, -0.001, 0.003), 0),
            _marker("K1", "check", (0.03, 0.04, 0.0), 1),
            _marker("K2", "check", (-0.03, -0.04, 0.02), 2),
        ],
        "scalebars": [
            {
                "name": "A-B",
                "role": "check",
                "reference": 50.0,
                "referenceAccuracy": 0.02,
                "estimated": 50.03,
            }
        ],
        "cameraGroups": [
            {
                "name": "Nadir",
                "n": 276,
                "xyMedian": 2.0,
                "xy95": 4.1,
                "dzMedian": -0.36,
                "dz95": 1.2,
            }
        ],
        "report": None,
    }
    d.update(ueber)
    return d


def _schreibe(tmp_path: Path, daten: dict[str, Any], name: str = "genauigkeit.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(daten))
    return p


def _paket(tmp_path: Path, datum: str | None = "dhhn2016", wkt: str = "EPSG:25833") -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    prov = {
        "kind": "other",
        "interpolated": False,
        "scaleVerified": False,
        "scaleSource": "unit:cli",
    }
    m = {
        "schemaVersion": 2,
        "projectId": "pkg",
        "revision": 1,
        "title": "pkg",
        "frame": {
            "crs": {"wkt": wkt, "verticalDatum": datum, "declaredBy": "cli"},
            "unit": "m",
            "unitDeclaredBy": "cli",
            "originWorld": [368000.0, 5807000.0, 0.0],
        },
        "layers": [
            {"id": "cloud", "kind": "pointcloud", "provenance": dict(prov)},
            {"id": "mesh-1", "kind": "mesh", "provenance": dict(prov)},
            {"id": "stations", "kind": "panoramas"},
        ],
        "initialView": {"target": [0, 0, 0], "distance": 10},
    }
    (pkg / "project.json").write_text(json.dumps(m))
    return pkg


# --- Laden und Prüfen -------------------------------------------------------------------------


def test_lade_gueltig(tmp_path: Path) -> None:
    exp = genauigkeit.lade(_schreibe(tmp_path, _daten()))
    assert exp.epsg == 25833
    assert [p.name for p in exp.punkte] == ["P1", "K1", "K2"]
    assert exp.strecken[0].rolle == "check"


def _kaputt(d: dict[str, Any], pfad: str, wert: Any) -> dict[str, Any]:
    d = copy.deepcopy(d)
    ziel: Any = d
    teile = pfad.split(".")
    for t in teile[:-1]:
        ziel = ziel[int(t)] if t.isdigit() else ziel[t]
    letzter = teile[-1]
    if wert is _LOESCHEN:
        del ziel[int(letzter) if letzter.isdigit() else letzter]
    else:
        ziel[int(letzter) if letzter.isdigit() else letzter] = wert
    return d


_LOESCHEN = object()


@pytest.mark.parametrize(
    ("pfad", "wert", "meldung"),
    [
        ("formatVersion", 2, "formatVersion"),
        ("crs.epsg", "25833", "epsg"),
        ("crs.epsg", True, "epsg"),
        ("markers.0.role", "pass", "role"),
        ("markers.1.estimated", [1.0, math.inf, 2.0], "endlich"),
        ("markers.1.reference", [1.0, 2.0], "drei"),
        ("markers.0.name", "x" * 201, "Name"),
        ("markers.0.images", -1, "images"),
        ("scalebars.0.referenceAccuracy", None, "Kontrollstrecke"),
        ("scalebars.0.reference", 0.0, "positiv"),
        ("source.projectSha256", "zz", "projectSha256"),
        ("unbekannt", 1, "unbekannt"),
        ("markers.0.extra", 1, "unbekannt"),
        ("cameraGroups.0.n", 0, "n"),
    ],
)
def test_lade_lehnt_ab(tmp_path: Path, pfad: str, wert: Any, meldung: str) -> None:
    with pytest.raises(ValueError, match=meldung):
        genauigkeit.lade(_schreibe(tmp_path, _kaputt(_daten(), pfad, wert)))


def test_lade_begrenzt_markerzahl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(genauigkeit, "MAX_EINTRAEGE", 2)
    with pytest.raises(ValueError, match="höchstens 2"):
        genauigkeit.lade(_schreibe(tmp_path, _daten()))


def test_lade_doppelte_namen(tmp_path: Path) -> None:
    d = _daten()
    d["markers"][1]["name"] = "P1"
    with pytest.raises(ValueError, match="doppelt"):
        genauigkeit.lade(_schreibe(tmp_path, d))


# --- Bewerten ---------------------------------------------------------------------------------


def test_kontrollpunkte_ergeben_geprueft(tmp_path: Path) -> None:
    acc = genauigkeit.bewerte(genauigkeit.lade(_schreibe(tmp_path, _daten())))
    a = acc["absolute"]
    assert a["status"] == "checked"
    assert a["n"] == 2
    # K1 (0,03 / 0,04 / 0), K2 (-0,03 / -0,04 / 0,02); Passpunkt P1 zählt nicht mit.
    assert a["rmse"]["x"] == pytest.approx(0.03)
    assert a["rmse"]["y"] == pytest.approx(0.04)
    assert a["rmse"]["xy"] == pytest.approx(0.05)
    assert a["rmse"]["z"] == pytest.approx(math.sqrt(0.0002))
    assert a["max"]["xy"] == pytest.approx(0.05)
    assert a["max"]["z"] == pytest.approx(0.02)
    p1 = next(p for p in acc["points"] if p["name"] == "P1")
    assert p1["role"] == "control"
    assert p1["error"] == pytest.approx([0.002, -0.001, 0.003])


def test_nur_passpunkte_und_gnss_sind_nur_ausgleich(tmp_path: Path) -> None:
    d = _daten(markers=[_marker("P1", "control", (0.01, 0.0, 0.0))])
    acc = genauigkeit.bewerte(genauigkeit.lade(_schreibe(tmp_path, d)))
    a = acc["absolute"]
    assert a["status"] == "adjustedOnly"
    assert "rmse" not in a  # kein ±-Wert aus Ausgleichsresiduen
    assert a["controlResiduals"]["n"] == 1
    assert a["cameraGroups"][0]["name"] == "Nadir"


def test_nur_gnss_ist_nur_ausgleich(tmp_path: Path) -> None:
    acc = genauigkeit.bewerte(genauigkeit.lade(_schreibe(tmp_path, _daten(markers=[]))))
    assert acc["absolute"]["status"] == "adjustedOnly"
    assert acc["absolute"]["controlResiduals"] is None


def test_ohne_alles_kein_nachweis(tmp_path: Path) -> None:
    d = _daten(markers=[], scalebars=[], cameraGroups=[])
    acc = genauigkeit.bewerte(genauigkeit.lade(_schreibe(tmp_path, d)))
    assert acc["absolute"]["status"] == "none"
    assert acc["scale"]["status"] == "none"


def test_marker_ohne_schaetzung_zaehlt_nicht(tmp_path: Path) -> None:
    d = _daten()
    d["markers"][2]["estimated"] = None
    acc = genauigkeit.bewerte(genauigkeit.lade(_schreibe(tmp_path, d)))
    assert acc["absolute"]["n"] == 1
    assert acc["absolute"]["withoutEstimate"] == 1


@pytest.mark.parametrize(
    ("strecken", "status"),
    [
        pytest.param([("check", 50.03, 0.02)], "checked", id="innerhalb-3-sigma"),
        pytest.param([("check", 50.07, 0.02)], "deviates", id="ausserhalb"),
        pytest.param([("check", 50.03, 0.02), ("check", 50.2, 0.02)], "deviates", id="eine-raus"),
        pytest.param([("control", 50.0, 0.02)], "set", id="nur-passstrecke"),
        pytest.param([("control", 50.0, None), ("check", 50.01, 0.02)], "checked", id="beide"),
    ],
)
def test_massstab(
    tmp_path: Path, strecken: list[tuple[str, float, float | None]], status: str
) -> None:
    sb = [
        {"name": f"S{i}", "role": r, "reference": 50.0, "referenceAccuracy": g, "estimated": ist}
        for i, (r, ist, g) in enumerate(strecken)
    ]
    acc = genauigkeit.bewerte(genauigkeit.lade(_schreibe(tmp_path, _daten(scalebars=sb))))
    assert acc["scale"]["status"] == status
    if status in ("checked", "deviates"):
        assert acc["scale"]["maxErrorPct"] > 0


def test_streckenfehler_in_prozent(tmp_path: Path) -> None:
    acc = genauigkeit.bewerte(genauigkeit.lade(_schreibe(tmp_path, _daten())))
    s = acc["distances"][0]
    assert s["error"] == pytest.approx(0.03)
    assert s["errorPct"] == pytest.approx(0.06)


# --- Ins Paket schreiben ----------------------------------------------------------------------


def test_importiere_schreibt_alle_messbaren_ebenen(tmp_path: Path) -> None:
    pkg = _paket(tmp_path)
    acc = genauigkeit.importiere(pkg, _schreibe(tmp_path, _daten()))
    m = read_manifest(pkg)
    cloud, mesh, stations = m["layers"]
    for layer in (cloud, mesh):
        prov = layer["provenance"]
        assert prov["accuracy"] == acc
        assert prov["scaleVerified"] is True
        assert prov["scaleSource"] == "strecke:A-B"
        assert prov["kind"] == "other"  # bestehende Felder bleiben
    assert "provenance" not in stations
    kopie = json.loads((pkg / "genauigkeit.json").read_text())
    assert kopie["formatVersion"] == 1


def test_importiere_nur_gewaehlte_ebene(tmp_path: Path) -> None:
    pkg = _paket(tmp_path)
    genauigkeit.importiere(pkg, _schreibe(tmp_path, _daten()), ebenen=["mesh-1"])
    cloud, mesh, _ = read_manifest(pkg)["layers"]
    assert "accuracy" not in cloud["provenance"]
    assert "accuracy" in mesh["provenance"]
    with pytest.raises(ValueError, match="Ebene"):
        genauigkeit.importiere(pkg, _schreibe(tmp_path, _daten()), ebenen=["stations"])


def test_ohne_massstab_bleibt_scale_verified(tmp_path: Path) -> None:
    pkg = _paket(tmp_path)
    genauigkeit.importiere(pkg, _schreibe(tmp_path, _daten(scalebars=[])))
    prov = read_manifest(pkg)["layers"][0]["provenance"]
    assert prov["scaleVerified"] is False
    assert prov["scaleSource"] == "unit:cli"


def test_crs_widerspruch_bricht_ab(tmp_path: Path) -> None:
    pkg = _paket(tmp_path, wkt="EPSG:25832")
    vorher = (pkg / "project.json").read_text()
    with pytest.raises(ValueError, match="CRS"):
        genauigkeit.importiere(pkg, _schreibe(tmp_path, _daten()))
    assert (pkg / "project.json").read_text() == vorher
    assert not (pkg / "genauigkeit.json").exists()


def test_ellipsoidisch_in_nhn_paket_wird_umgerechnet(tmp_path: Path) -> None:
    pkg = _paket(tmp_path)
    d = _daten()
    d["crs"]["verticalDatum"] = "ellipsoidisch"
    acc = genauigkeit.importiere(pkg, _schreibe(tmp_path, d))
    k1 = next(p for p in acc["points"] if p["name"] == "K1")
    soll = d["markers"][1]["reference"]
    nhn = hoehen.rechne_um(
        np.array([soll[0]]),
        np.array([soll[1]]),
        np.array([soll[2]]),
        crs=25833,
        quelle=hoehen.ELLIPSOIDISCH,
        ziel=hoehen.DHHN2016,
    )
    assert k1["reference"][2] == pytest.approx(float(nhn[0]), abs=1e-6)
    assert 39.0 < soll[2] - k1["reference"][2] < 41.5
    # Abweichung bleibt gleich: gleiches Gitter, Soll und Ist nur 5 cm auseinander.
    assert k1["error"] == pytest.approx([0.03, 0.04, 0.0], abs=0.001)
    assert acc["heightConversion"] == {"from": "ellipsoidal-etrs89", "to": "dhhn2016"}


def test_unbekanntes_paket_hoehensystem_bricht_ab(tmp_path: Path) -> None:
    pkg = _paket(tmp_path, datum=None)
    with pytest.raises(ValueError, match="Höhensystem"):
        genauigkeit.importiere(pkg, _schreibe(tmp_path, _daten()))


def test_bericht_nur_name_und_pruefsumme(tmp_path: Path) -> None:
    pkg = _paket(tmp_path)
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    d = _daten(report={"filename": "report.pdf", "sha256": sha})
    acc = genauigkeit.importiere(pkg, _schreibe(tmp_path, d), bericht=pdf)
    assert acc["report"] == {"filename": "report.pdf", "sha256": sha}
    assert not any(p.suffix == ".pdf" for p in pkg.iterdir())


def test_bericht_mit_falscher_pruefsumme_bricht_ab(tmp_path: Path) -> None:
    pkg = _paket(tmp_path)
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 anders")
    d = _daten(report={"filename": "report.pdf", "sha256": "b" * 64})
    with pytest.raises(ValueError, match="Prüfsumme"):
        genauigkeit.importiere(pkg, _schreibe(tmp_path, d), bericht=pdf)


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pkg = _paket(tmp_path)
    datei = _schreibe(tmp_path, _daten())
    assert cli.main(["genauigkeit", "--datei", str(datei), str(pkg)]) == 0
    assert json.loads(capsys.readouterr().out)["absolute"]["status"] == "checked"
    falsch = _paket(tmp_path / "zwei", wkt="EPSG:25832")
    assert cli.main(["genauigkeit", "--datei", str(datei), str(falsch)]) == 1
    assert "Genauigkeit-Import abgebrochen" in capsys.readouterr().err
