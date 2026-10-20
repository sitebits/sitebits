"""Schema 2 im Import: Golden-Fixtures teilt sich der Import mit dem
Viewer."""

import json
import shutil
from pathlib import Path

import pytest

from scanview_import.manifest_io import (
    STATIONS_LAYER_ID,
    cloud_of,
    migrate,
    origin_of,
    read_manifest,
    set_stations,
    to_v2,
    write_manifest,
)

FIX = Path(__file__).resolve().parents[2] / "web" / "tests" / "fixtures"
V1 = json.loads((FIX / "manifest-v1.json").read_text())
V2M = json.loads((FIX / "manifest-v2-migrated.json").read_text())


def test_to_v2_trifft_golden():
    assert to_v2(json.loads(json.dumps(V1))) == V2M


def test_to_v2_ohne_standpunkte_keine_ebene():
    v1 = json.loads(json.dumps(V1))
    v1["stations"]["count"] = 0
    assert [lay["id"] for lay in to_v2(v1)["layers"]] == ["punkte"]


def test_to_v2_unbekanntes_feld_bricht_ab():
    v1 = {**json.loads(json.dumps(V1)), "neu": 1}
    with pytest.raises(ValueError, match="Unbekanntes Feld in Schema 1: neu"):
        to_v2(v1)


def test_zugriffe_v1_und_v2_gleich():
    for m in (json.loads(json.dumps(V1)), json.loads(json.dumps(V2M))):
        assert cloud_of(m)["url"] == "cloud.copc.laz"
        assert origin_of(m) == [100.0, 200.0, 10.0]
        cloud_of(m)["pointSourceIsScan"] = False  # veränderbarer Verweis
        assert cloud_of(m)["pointSourceIsScan"] is False


def test_cloud_of_ohne_punktwolke():
    m = json.loads(json.dumps(V2M))
    m["layers"] = [lay for lay in m["layers"] if lay["kind"] != "pointcloud"]
    with pytest.raises(KeyError, match="keine Punktwolken-Ebene"):
        cloud_of(m)


def test_set_stations_v2_legt_an_aktualisiert_entfernt():
    m = json.loads(json.dumps(V2M))
    set_stations(m, "stations.json", 5)
    st = [lay for lay in m["layers"] if lay["id"] == "standpunkte"]
    assert st == [{"id": "standpunkte", "kind": "panoramas", "title": "Standpunkte",
                   "url": "stations.json", "count": 5, "pointcloudLayer": "punkte"}]
    set_stations(m, "stations.json", 0)
    assert all(lay["id"] != "standpunkte" for lay in m["layers"])


def test_set_stations_verweist_auf_tatsaechliche_punktwolken_id():
    """pointcloudLayer zeigt auf die tatsächliche ID der Punktwolken-Ebene (cloud_of), nicht auf
    die feste Konstante CLOUD_LAYER_ID — falls die Punktwolke je eine andere ID trägt."""
    m = json.loads(json.dumps(V2M))
    for lay in m["layers"]:
        if lay.get("kind") == "pointcloud":
            lay["id"] = "wolke"
    set_stations(m, "stations.json", 3)
    st = next(lay for lay in m["layers"] if lay["id"] == STATIONS_LAYER_ID)
    assert st["pointcloudLayer"] == "wolke"


def test_migrate_sichert_und_ist_idempotent(tmp_path: Path):
    pkg = tmp_path / "paket"
    pkg.mkdir()
    write_manifest(pkg, json.loads(json.dumps(V1)))
    assert migrate(pkg) == "migriert"
    assert json.loads((pkg / "project.v1.json").read_text()) == V1
    assert read_manifest(pkg) == V2M
    assert migrate(pkg) == "schon Schema 2"


def test_migrate_sichert_bytegleich_inkl_nicht_ascii_titel(tmp_path: Path):
    """Die Sicherung ist eine reine Byte-Kopie der Originaldatei (raw geschrieben, kein
    Re-Serialisieren über json.dump — das würde Nicht-ASCII-Zeichen per ensure_ascii=True als
    \\uXXXX escapen und wäre nicht mehr bytegleich zum Original)."""
    pkg = tmp_path / "paket"
    pkg.mkdir()
    v1 = {**json.loads(json.dumps(V1)), "projectId": "büro-nördlich-☺"}
    raw = json.dumps(v1, indent=2, ensure_ascii=False).encode("utf-8")
    (pkg / "project.json").write_bytes(raw)
    assert migrate(pkg) == "migriert"
    assert (pkg / "project.v1.json").read_bytes() == raw
    assert read_manifest(pkg)["projectId"] == "büro-nördlich-☺"


def test_migrate_nan_hinterlaesst_keine_sicherung_und_kein_project_json(tmp_path: Path):
    """v2 wird VOR jedem Schreiben vollständig serialisiert (allow_nan=False) — ein NaN im
    Manifest darf weder die Sicherung noch project.json anfassen."""
    pkg = tmp_path / "paket"
    pkg.mkdir()
    v1 = json.loads(json.dumps(V1))
    v1["initialView"]["distance"] = float("nan")
    original = json.dumps(v1, indent=2, allow_nan=True)
    (pkg / "project.json").write_text(original)
    with pytest.raises(ValueError):
        migrate(pkg)
    assert not (pkg / "project.v1.json").exists()
    assert (pkg / "project.json").read_text() == original


def test_migrate_ohne_schemaversion_bricht_ab_nichts_geschrieben(tmp_path: Path):
    """Nur schemaVersion == 1 wird migriert — fehlt das Feld, ist das ein Fehler, kein
    stillschweigendes "als Schema 1 behandeln"."""
    pkg = tmp_path / "paket"
    pkg.mkdir()
    v1 = {k: v for k, v in json.loads(json.dumps(V1)).items() if k != "schemaVersion"}
    write_manifest(pkg, v1)
    with pytest.raises(ValueError, match="schemaVersion"):
        migrate(pkg)
    assert not (pkg / "project.v1.json").exists()
    assert read_manifest(pkg) == v1


def test_migrate_schemaversion_3_bricht_ab_nichts_geschrieben(tmp_path: Path):
    """2 bleibt der einzige Sonderfall (No-op "schon Schema 2") — jeder andere Wert außer 1 ist
    ein Fehler."""
    pkg = tmp_path / "paket"
    pkg.mkdir()
    v1 = {**json.loads(json.dumps(V1)), "schemaVersion": 3}
    write_manifest(pkg, v1)
    with pytest.raises(ValueError, match="schemaVersion"):
        migrate(pkg)
    assert not (pkg / "project.v1.json").exists()
    assert read_manifest(pkg) == v1


def test_migrate_hinterlaesst_keine_sicherung_wenn_to_v2_fehlschlaegt(tmp_path: Path):
    """to_v2 validiert (unbekanntes Feld) — schlägt das fehl, darf project.v1.json nicht schon
    liegen: sonst meldet ein Folgeversuch fälschlich "Sicherung existiert schon", obwohl noch
    nichts migriert wurde."""
    pkg = tmp_path / "paket"
    pkg.mkdir()
    v1 = {**json.loads(json.dumps(V1)), "neu": 1}
    write_manifest(pkg, v1)
    with pytest.raises(ValueError, match="Unbekanntes Feld in Schema 1: neu"):
        migrate(pkg)
    assert not (pkg / "project.v1.json").exists()
    assert read_manifest(pkg) == v1


def test_migrate_ueberschreibt_keine_vorhandene_sicherung(tmp_path: Path):
    pkg = tmp_path / "paket"
    pkg.mkdir()
    write_manifest(pkg, json.loads(json.dumps(V1)))
    (pkg / "project.v1.json").write_text("{}")
    with pytest.raises(FileExistsError):
        migrate(pkg)
    assert read_manifest(pkg) == V1  # nichts verändert


def test_migrate_cli(tmp_path: Path):
    from scanview_import.cli import main

    pkg = tmp_path / "paket"
    pkg.mkdir()
    shutil.copy(FIX / "manifest-v1.json", pkg / "project.json")
    assert main(["migrate", str(pkg)]) == 0
    assert read_manifest(pkg)["schemaVersion"] == 2


def test_migrate_cli_bricht_mit_exit_1_ab_wenn_sicherung_schon_existiert(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    from scanview_import.cli import main

    pkg = tmp_path / "paket"
    pkg.mkdir()
    shutil.copy(FIX / "manifest-v1.json", pkg / "project.json")
    (pkg / "project.v1.json").write_text("{}")
    assert main(["migrate", str(pkg)]) == 1
    err = capsys.readouterr().err
    assert "existiert schon" in err
    assert "nichts verändert" in err
    assert read_manifest(pkg) == V1


def test_migrate_cli_meldet_valueerror_saeuberlich_exit_1_kein_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """cli.py: ValueError aus migrate() (z. B. falsche schemaVersion) wird als Meldung auf stderr
    gemeldet, nicht als Traceback — main() fängt sie ab, lässt sie nicht durch."""
    from scanview_import.cli import main

    pkg = tmp_path / "paket"
    pkg.mkdir()
    v1 = {**V1, "schemaVersion": 3}
    (pkg / "project.json").write_text(json.dumps(v1))
    assert main(["migrate", str(pkg)]) == 1
    out = capsys.readouterr()
    assert "Traceback" not in out.err
    assert "Migration abgebrochen" in out.err
    assert read_manifest(pkg) == v1


def test_migrate_cli_meldet_keyerror_saeuberlich_exit_1_kein_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """cli.py: KeyError aus migrate() (fehlendes Pflichtfeld in Schema 1, hier "crs") wird
    ebenso als Meldung auf stderr gemeldet, nicht als Traceback."""
    from scanview_import.cli import main

    pkg = tmp_path / "paket"
    pkg.mkdir()
    v1 = {k: v for k, v in V1.items() if k != "crs"}
    (pkg / "project.json").write_text(json.dumps(v1))
    assert main(["migrate", str(pkg)]) == 1
    out = capsys.readouterr()
    assert "Traceback" not in out.err
    assert "Migration abgebrochen" in out.err
    assert not (pkg / "project.v1.json").exists()
    assert read_manifest(pkg) == v1


def _paket_v2(tmp_path: Path) -> Path:
    from scanview_import.manifest_io import write_manifest
    write_manifest(tmp_path, {"schemaVersion": 2, "layers": [
        {"id": "punkte", "kind": "pointcloud"}, {"id": "mesh", "kind": "mesh"}]})
    return tmp_path


def test_start_sichtbar_setzt_und_entfernt_das_feld(tmp_path: Path) -> None:
    from scanview_import.manifest_io import read_manifest, start_sichtbar
    pkg = _paket_v2(tmp_path)
    assert start_sichtbar(pkg, "punkte", False) == {"ebene": "punkte", "startVisible": False}
    assert read_manifest(pkg)["layers"][0]["startVisible"] is False
    assert "startVisible" not in read_manifest(pkg)["layers"][1]
    start_sichtbar(pkg, "punkte", True)
    assert "startVisible" not in read_manifest(pkg)["layers"][0]


def test_start_sichtbar_unbekannte_ebene_und_schema1(tmp_path: Path) -> None:
    from scanview_import.manifest_io import start_sichtbar, write_manifest
    pkg = _paket_v2(tmp_path)
    with pytest.raises(KeyError):
        start_sichtbar(pkg, "gibtsnicht", False)
    write_manifest(pkg, {"schemaVersion": 1, "layers": []})
    with pytest.raises(ValueError, match="Schema 2"):
        start_sichtbar(pkg, "punkte", False)


def test_cli_start_sichtbar(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scanview_import.cli import main
    pkg = _paket_v2(tmp_path)
    assert main(["start-sichtbar", str(pkg), "punkte", "aus"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ebene": "punkte", "startVisible": False}
    assert main(["start-sichtbar", str(pkg), "nein", "aus"]) == 1
