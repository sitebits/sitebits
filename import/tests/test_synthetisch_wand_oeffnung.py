"""Punktmengen des synthetischen Prüfpakets „Wand mit Öffnung“ (ohne PDAL-Lauf)."""

from pathlib import Path

import numpy as np
import pytest
import synthetisch_wand_oeffnung as s

ABSTAND = 0.02  # grober als im Paket (5 mm), genügt für die Geometrie und hält den Test schnell
AUGE = np.array(s.SCANNER_POSE)


@pytest.fixture(scope="module")
def flaechen():
    return s.erzeuge_flaechen(ABSTAND)


def test_seed_fest(flaechen):
    wieder = s.erzeuge_flaechen(ABSTAND)
    for name, pts in flaechen.items():
        assert np.array_equal(pts, wieder[name])


def test_oeffnung_ist_leer(flaechen):
    w = flaechen["wand"]
    assert len(w) > 0
    assert np.allclose(w[:, 0], s.WAND_X)
    assert not s._in_oeffnung(w[:, 1], w[:, 2]).any()
    # Rund um die Öffnung sind Punkte da, die Wand ist also nicht einfach leer
    rand = (np.abs(w[:, 1]) < 0.8) & (w[:, 2] > 0.6) & (w[:, 2] < 2.4)
    assert rand.sum() > 0


def test_sichtbare_flaeche_ist_groesstenteils_durch_die_oeffnung_zu_sehen(flaechen):
    p = flaechen["sichtbar"]
    t = (s.WAND_X - AUGE[0]) / (p[:, 0] - AUGE[0])
    y = AUGE[1] + t * (p[:, 1] - AUGE[1])
    z = AUGE[2] + t * (p[:, 2] - AUGE[2])
    sichtbar = s._in_oeffnung(y, z)
    assert sichtbar.mean() > 0.9
    mitte = (np.abs(p[:, 1]) < 0.5) & (np.abs(p[:, 2] - 1.5) < 0.5)
    assert sichtbar[mitte].all()


def test_verdeckte_flaeche_nicht_durch_die_oeffnung_sichtbar(flaechen):
    p = flaechen["verdeckt"]
    assert np.allclose(p[:, 0], s.VERDECKT_X)
    # Der Strahl vom Scanner zum Punkt schneidet die Wandebene bei t
    t = (s.WAND_X - AUGE[0]) / (p[:, 0] - AUGE[0])
    y = AUGE[1] + t * (p[:, 1] - AUGE[1])
    z = AUGE[2] + t * (p[:, 2] - AUGE[2])
    assert not s._in_oeffnung(y, z).any()
    # und trifft die Wand innerhalb ihrer Ausdehnung, ist dort also wirklich verdeckt
    assert ((np.abs(y) <= 3) & (z >= 0) & (z <= 3)).all()


def test_boden_und_lokale_koordinaten(flaechen):
    b = flaechen["boden"]
    assert np.allclose(b[:, 2], 0.0)
    assert b[:, 0].min() >= -0.01 and b[:, 0].max() <= 5.01
    lokal = s.scan_lokal(flaechen)
    assert lokal.shape == (sum(len(v) for v in flaechen.values()), 3)
    assert np.allclose(lokal[0], flaechen["wand"][0] - AUGE)


def test_vorhandenes_ziel_wird_verweigert(tmp_path, capsys):
    assert s.main(["--ziel", str(tmp_path)]) == 2
    assert "nichts überschrieben" in capsys.readouterr().err


def _ohne_e57(monkeypatch):
    monkeypatch.setattr(s, "_fixture_writer", lambda: lambda *a, **k: None)


def test_gescheiterter_lauf_entfernt_das_eigene_teilpaket(tmp_path, monkeypatch):
    import scanview_import.cli as cli

    ziel = tmp_path / "paket"

    def scheitert(argv):
        (ziel / "teil").mkdir(parents=True)
        return 3

    _ohne_e57(monkeypatch)
    monkeypatch.setattr(cli, "main", scheitert)
    assert s.main(["--ziel", str(ziel), "--abstand", "0.5"]) == 3
    assert not ziel.exists()


def test_vorhandenes_ziel_bleibt_unberuehrt(tmp_path, monkeypatch):
    import scanview_import.cli as cli

    ziel = tmp_path / "paket"
    ziel.mkdir()
    (ziel / "behalten.txt").write_text("x", encoding="utf-8")
    aufrufe: list[object] = []
    _ohne_e57(monkeypatch)
    monkeypatch.setattr(cli, "main", lambda argv: aufrufe.append(argv) or 1)
    assert s.main(["--ziel", str(ziel)]) == 2
    assert (ziel / "behalten.txt").exists()
    assert aufrufe == []


def test_fixture_pfad_wird_angehaengt_nicht_vorangestellt(monkeypatch):
    import sys

    tests = str(Path(s.__file__).resolve().parent.parent / "tests")
    rest = [p for p in sys.path if p != tests]
    monkeypatch.setattr(sys, "path", [*rest])
    s._fixture_writer()
    assert sys.path[: len(rest)] == rest
    assert sys.path[-1] == tests
