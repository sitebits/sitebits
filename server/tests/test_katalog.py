import json
from pathlib import Path

import pytest
from pakete import lege_paket

from scanview_server.katalog import PROJECT_ID_RE, Katalog


def test_karte_aus_schema2(tmp_path: Path) -> None:
    lege_paket(tmp_path, "musterhalle-nord-v2", titel="Musterhalle Nord")
    k = Katalog(tmp_path)
    k.laden()
    karte = k.get("musterhalle-nord-v2")
    assert karte is not None
    assert karte.titel == "Musterhalle Nord"
    assert karte.punkte == 1234
    assert karte.standpunkte == 45
    assert karte.ausdehnung == pytest.approx((40.5, 30.25, 12.0))
    assert karte.crs == "lokal"
    assert karte.scandatum is None
    assert karte.draufsicht is True
    assert karte.groesse_bytes >= 1000


def test_crs_name_aus_wkt(tmp_path: Path) -> None:
    lege_paket(tmp_path, "thf", crs_wkt='PROJCS["DHDN / Soldner Berlin",GEOGCS["DHDN"]]')
    k = Katalog(tmp_path)
    k.laden()
    assert k.get("thf").crs == "DHDN / Soldner Berlin"  # type: ignore[union-attr]


def test_katalog_ueberspringt_kaputte_pakete(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    lege_paket(tmp_path, "gut")
    lege_paket(tmp_path, "alt", schema=1)
    lege_paket(tmp_path, "anders", ordner="falscher-ordner")
    kaputt = tmp_path / "kaputt"
    kaputt.mkdir()
    (kaputt / "project.json").write_text("{nicht json", encoding="utf-8")
    (tmp_path / "Grossbuchstaben").mkdir()
    (tmp_path / "_refs").mkdir()
    k = Katalog(tmp_path)
    k.laden()
    assert [x.id for x in k.alle()] == ["gut"]
    text = caplog.text
    assert "alt" in text and "Schema" in text
    assert "falscher-ordner" in text
    assert "kaputt" in text


def test_symlink_im_paket_wird_abgelehnt(tmp_path: Path) -> None:
    d = lege_paket(tmp_path, "mitlink")
    (d / "panos").symlink_to(tmp_path)
    k = Katalog(tmp_path)
    k.laden()
    assert k.get("mitlink") is None


def test_laden_ist_wiederholbar(tmp_path: Path) -> None:
    k = Katalog(tmp_path)
    k.laden()
    assert k.alle() == []
    lege_paket(tmp_path, "neu")
    k.laden()
    assert [x.id for x in k.alle()] == ["neu"]


def _project_json(d: Path) -> dict:  # type: ignore[type-arg]
    return json.loads((d / "project.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _project_json_schreiben(d: Path, m: dict) -> None:  # type: ignore[type-arg]
    (d / "project.json").write_text(json.dumps(m), encoding="utf-8")


def test_katalog_ueberspringt_paket_mit_kaputtem_crs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    lege_paket(tmp_path, "gut2")
    d = lege_paket(tmp_path, "kaputtes-crs")
    m = _project_json(d)
    m["frame"]["crs"] = "kaputt"
    _project_json_schreiben(d, m)
    k = Katalog(tmp_path)
    k.laden()
    assert k.get("kaputtes-crs") is None
    assert k.get("gut2") is not None
    assert "kaputtes-crs" in caplog.text


def test_katalog_ueberspringt_paket_mit_kaputtem_boundsworld(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    lege_paket(tmp_path, "gut3")
    d = lege_paket(tmp_path, "kaputte-ausdehnung")
    m = _project_json(d)
    for layer in m["layers"]:
        if layer.get("kind") == "pointcloud":
            layer["boundsWorld"] = [0, 1]
    _project_json_schreiben(d, m)
    k = Katalog(tmp_path)
    k.laden()
    assert k.get("kaputte-ausdehnung") is None
    assert k.get("gut3") is not None
    assert "kaputte-ausdehnung" in caplog.text


def test_project_id_re_lehnt_zeilenumbruch_ab() -> None:
    assert PROJECT_ID_RE.fullmatch("abc\n") is None


def test_revision_aus_project_json(tmp_path: Path) -> None:
    lege_paket(tmp_path, "rev")
    k = Katalog(tmp_path)
    k.laden()
    assert k.get("rev").revision == 1  # type: ignore[union-attr]


def test_katalog_ueberspringt_zu_grosse_zahl_ohne_andere_pakete_zu_verlieren(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    lege_paket(tmp_path, "gut")
    d = lege_paket(tmp_path, "zahlueberlauf")
    manifest = d / "project.json"
    daten = json.loads(manifest.read_text())
    daten["layers"][0]["boundsWorld"][1][0] = 10 ** 1000
    manifest.write_text(json.dumps(daten))
    katalog = Katalog(tmp_path)
    katalog.laden()
    assert [karte.id for karte in katalog.alle()] == ["gut"]
    assert "zahlueberlauf" in caplog.text and "OverflowError" in caplog.text
