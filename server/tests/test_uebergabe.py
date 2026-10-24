"""`scanview-uebergabe`: Übergabe → Eingang nur mit neuen Inodes, ohne Links und Sonderdateien;
Meldungen und Aufräumen."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scanview_worker import uebergabe as UE


@pytest.fixture
def orte(tmp_path: Path) -> dict[str, Path]:
    o = {n: tmp_path / n for n in ("uebergabe", "meldungen", "eingang")}
    for p in o.values():
        p.mkdir()
    return o


def _paket(o: dict[str, Path], pid: str = "neu") -> Path:
    p = o["uebergabe"] / pid
    (p / "layers" / "splats").mkdir(parents=True)
    (p / "project.json").write_text(json.dumps({"schemaVersion": 2, "projectId": pid}))
    (p / "layers" / "splats" / "model.spz").write_bytes(b"\x1f\x8b\x08" + b"x" * 5000)
    return p


def _kopieren(o: dict[str, Path], pid: str = "neu") -> None:
    UE.kopieren(pid, uebergabe=o["uebergabe"], eingang=o["eingang"])


def test_kopie_mit_neuen_inodes(orte: dict[str, Path]) -> None:
    q = _paket(orte)
    _kopieren(orte)
    z = orte["eingang"] / "neu"
    for rel in ("project.json", "layers/splats/model.spz"):
        assert (z / rel).read_bytes() == (q / rel).read_bytes()
        assert (z / rel).stat().st_ino != (q / rel).stat().st_ino
        assert (z / rel).stat().st_mode & 0o777 == 0o600
    assert z.stat().st_mode & 0o777 == 0o700
    assert (q / "project.json").exists()  # Quelle bleibt (der Worker räumt sie)


@pytest.mark.parametrize("art", ["symlink", "hardlink", "fifo", "name"])
def test_abgelehnt_und_eingang_bleibt_leer(orte: dict[str, Path], art: str) -> None:
    q = _paket(orte)
    if art == "symlink":
        (q / "layers" / "link").symlink_to("/etc/passwd")
    elif art == "hardlink":
        os.link(q / "project.json", orte["uebergabe"] / "zweiter")
    elif art == "fifo":
        os.mkfifo(q / "rohr")
    else:
        (q / "a b").write_text("x")
    with pytest.raises(UE.Abgelehnt):
        _kopieren(orte)
    assert list(orte["eingang"].iterdir()) == []


def test_paket_selbst_als_symlink(orte: dict[str, Path], tmp_path: Path) -> None:
    (tmp_path / "anderswo").mkdir()
    (orte["uebergabe"] / "neu").symlink_to(tmp_path / "anderswo")
    with pytest.raises(UE.Abgelehnt):
        _kopieren(orte)
    assert list(orte["eingang"].iterdir()) == []


def test_belegter_eingang_bleibt_unberuehrt(orte: dict[str, Path]) -> None:
    _paket(orte)
    (orte["eingang"] / "neu").mkdir()
    (orte["eingang"] / "neu" / "fremd").write_text("x")
    with pytest.raises(UE.Abgelehnt):
        _kopieren(orte)
    assert (orte["eingang"] / "neu" / "fremd").read_text() == "x"


@pytest.mark.parametrize("pid", ["../x", "Neu", "-x", "a/b", "", "x" * 65, "neu\n"])
def test_ungueltige_id(orte: dict[str, Path], pid: str) -> None:
    with pytest.raises(UE.Abgelehnt):
        _kopieren(orte, pid)
    with pytest.raises(UE.Abgelehnt):
        UE.melden(pid, "OK", meldungen=orte["meldungen"])


def test_melden_und_aufraeumen(orte: dict[str, Path]) -> None:
    _paket(orte, "bleibt")
    UE.melden("bleibt", "OK", meldungen=orte["meldungen"])
    UE.melden("weg", "VORHANDEN", meldungen=orte["meldungen"])
    assert (orte["meldungen"] / "bleibt").read_text() == "OK\n"
    assert (orte["meldungen"] / "weg").stat().st_mode & 0o777 == 0o640
    with pytest.raises(UE.Abgelehnt):
        UE.melden("x", "ok; rm", meldungen=orte["meldungen"])
    (orte["eingang"] / "rest").mkdir()
    UE.aufraeumen(uebergabe=orte["uebergabe"], meldungen=orte["meldungen"],
                  eingang=orte["eingang"])
    assert sorted(p.name for p in orte["meldungen"].iterdir()) == ["bleibt"]
    assert list(orte["eingang"].iterdir()) == []


def test_kommandozeile(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                       orte: dict[str, Path]) -> None:
    assert UE.main([]) == 2
    assert UE.main(["kopieren"]) == 2
    assert UE.main(["kopieren", "neu", "--x"]) == 2
    monkeypatch.setattr(UE, "UEBERGABE", orte["uebergabe"])
    monkeypatch.setattr(UE, "EINGANG", orte["eingang"])
    assert UE.main(["kopieren", "fehlt"]) == 1
    assert "fehlt oder ist kein Ordner" in capsys.readouterr().err
    _paket(orte)
    monkeypatch.setattr(UE, "kopieren", lambda pid: UE.__dict__["_id"](pid))
    assert UE.main(["kopieren", "neu"]) == 0
