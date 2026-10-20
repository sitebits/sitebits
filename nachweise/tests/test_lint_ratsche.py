import json
from pathlib import Path

import pytest

from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.lint_ratsche import pruefe_teil, validiere, vergleiche


def test_gleichstand_und_senkung() -> None:
    basis = {"src/a.py": {"C901": 2}}
    assert vergleiche(basis, basis) == []
    assert vergleiche(basis, {"src/a.py": {"C901": 1}}) == [
        "Basis senken: src/a.py: C901 2 → 1"]
    assert vergleiche(basis, {}) == ["Basis senken: src/a.py: C901 2 → 0"]


@pytest.mark.parametrize("ist", [
    {"src/a.py": {"C901": 3}}, {"src/b.py": {"C901": 1}},
    {"src/a.py": {"BLE001": 1}},
])
def test_anstieg_neue_datei_neue_regel(ist: dict[str, dict[str, int]]) -> None:
    with pytest.raises(NachweisFehler, match="neue Funde"):
        vergleiche({"src/a.py": {"C901": 2}}, ist)


@pytest.mark.parametrize("basis", [None, [], {"../a.py": {"C901": 1}},
                                      {"a.py": {"C901": True}}, {"a.py": {"F401": 1}},
                                      {"a.py": {"C901": -1}}, {"a.py": {} }])
def test_kaputte_basis(basis: object) -> None:
    with pytest.raises(NachweisFehler, match="kaputte Basis"):
        validiere(basis)


def _projekt(p: Path) -> list[str]:
    (p / "pyproject.toml").write_text('[tool.ruff.lint]\nselect = ["E", "F"]\n')
    return [str(Path(__file__).resolve().parents[1] / ".venv/bin/ruff")]


def test_echte_ruff_probe_und_update_nie_hoeher(tmp_path: Path) -> None:
    befehl = _projekt(tmp_path)
    basis = tmp_path / "ruff-basis.json"
    basis.write_text('{}\n')
    (tmp_path / "neu.py").write_text('try:\n    int("x")\nexcept Exception:\n    print("x")\n')
    with pytest.raises(NachweisFehler, match="BLE001"):
        pruefe_teil(tmp_path, befehl=befehl, aktualisieren=True)
    assert json.loads(basis.read_text()) == {}
    basis.write_text('{"neu.py": {"BLE001": 1}}\n')
    assert pruefe_teil(tmp_path, befehl=befehl) == []
    (tmp_path / "neu.py").write_text('try:\n    int("x")\nexcept ValueError:\n    print("x")\n')
    assert pruefe_teil(tmp_path, befehl=befehl, aktualisieren=True)
    assert json.loads(basis.read_text()) == {}


def test_harte_regel_bleibt_rot(tmp_path: Path) -> None:
    befehl = _projekt(tmp_path)
    (tmp_path / "ruff-basis.json").write_text('{}\n')
    (tmp_path / "neu.py").write_text('import os\n')
    with pytest.raises(NachweisFehler, match="harte Ruff-Funde"):
        pruefe_teil(tmp_path, befehl=befehl)


def test_basis_fehlt_oder_unlesbar(tmp_path: Path) -> None:
    befehl = _projekt(tmp_path)
    for inhalt in [None, '{kaputt']:
        if inhalt is not None:
            (tmp_path / "ruff-basis.json").write_text(inhalt)
        with pytest.raises(NachweisFehler, match="Basis nicht lesbar"):
            pruefe_teil(tmp_path, befehl=befehl)


def test_werkzeugfehler_ist_keine_leere_fundliste(tmp_path: Path) -> None:
    (tmp_path / "ruff-basis.json").write_text('{}\n')
    with pytest.raises(NachweisFehler, match="Ruff nicht auswertbar"):
        pruefe_teil(tmp_path, befehl=["/nicht-vorhanden/ruff"])
