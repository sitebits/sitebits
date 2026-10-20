"""GET /api/v1/projekte/{pid}/paket: Paketinhalt für Agenten, Fotos-Ebene nur
für Rollen, die die Fotos laden dürften."""

import json

from fadenwelt import BASIS, Welt, welt  # noqa: F401  (Fixture)


def _paket_mit_fotos(w: Welt) -> None:
    d = w.c["bear"].app.state.settings.data_dir / "ost"  # type: ignore[attr-defined]
    m = json.loads((d / "project.json").read_text())
    m["frame"]["crs"] = {"wkt": "EPSG:25833", "verticalDatum": "dhhn2016", "declaredBy": "cli"}
    m["frame"]["undulation"] = {"atOriginM": 40.163, "maxDeviationM": 0.004, "grid": "GCG2016"}
    m["layers"][0]["classes"] = {"2": 1000, "6": 234}
    m["layers"][0]["attributes"] = ["classification", "returns"]
    m["layers"].append({"id": "fotos", "kind": "images", "title": "Fotos", "url": "images.json",
                        "count": 12})
    (d / "project.json").write_text(json.dumps(m))
    w.c["bear"].app.state.katalog.laden()  # type: ignore[attr-defined]


def test_paket_lesen_inhalt(welt: Welt) -> None:  # noqa: F811 — Fixture
    _paket_mit_fotos(welt)
    p = welt.c["bear"].get(BASIS + "/paket").json()
    assert p["einheit"] == "[m, ETRS89/UTM33, NHN]"
    assert p["hoehensystem"] == "dhhn2016"
    assert p["undulation"]["atOriginM"] == 40.163
    wolke = p["ebenen"][0]
    assert wolke["art"] == "pointcloud" and wolke["classes"] == {"2": 1000, "6": 234}
    assert "url" not in wolke
    assert [e["art"] for e in p["ebenen"]][-1] == "images"
    assert p["genauigkeit"][0]["zeilen"] == ["kein Genauigkeitsnachweis"]


def test_fotos_ebene_fehlt_fuer_betrachter_ohne_freigabe(welt: Welt) -> None:  # noqa: F811
    _paket_mit_fotos(welt)
    arten = [e["art"] for e in welt.c["betr"].get(BASIS + "/paket").json()["ebenen"]]
    assert "images" not in arten and "pointcloud" in arten
    assert welt.c["fremd"].get(BASIS + "/paket").status_code == 404
