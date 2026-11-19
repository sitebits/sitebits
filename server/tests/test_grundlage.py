"""Grundlage je Eintrag: worauf die Koordinaten beruhen, lesend abgeleitet aus Punktquellen,
Herkunft, Kanal der letzten Geometrie-Änderung und Genauigkeit der Ebenen. Über ein Token (Skript
oder KI-Agent) kann niemand in die Wolke klicken; Punktquellen sind dort Behauptungen und machen
nichts „gemessen“."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import csrf
from fadenwelt import BASIS, Welt, welt  # noqa: F401

from scanview_server import genauigkeit, inhalte
from scanview_server.genauigkeit import EbeneGenauigkeit

BELEGT = EbeneGenauigkeit(id="punkte", titel="Punktwolke", art="pointcloud", zeilen=(),
                          hinweise=(), werte={"lage": {"status": "none"},
                                              "massstab": {"status": "checked"}},
                          massstab_belegt=True)
NUR_GESETZT = EbeneGenauigkeit(id="gesetzt", titel="Mesh", art="mesh", zeilen=(), hinweise=(),
                               werte={"lage": {"status": "none"}, "massstab": {"status": "set"}})
MODELL = EbeneGenauigkeit(id="mesh", titel="Mesh", art="mesh", zeilen=(),
                          hinweise=(f"{genauigkeit.MODELLMASS}Drohne)",))
QUELLE = {"source": "pointcloud", "layerId": "punkte"}
STRECKE = {"kind": "distance", "points": [[0, 0, 0], [1, 0, 0]]}

AUS_WOLKE = {**STRECKE, "pointSources": [QUELLE, QUELLE]}
FAELLE: list[tuple[str, dict[str, Any], str, str | None, str]] = [
    ("strecke_aus_der_wolke", AUS_WOLKE, "mensch", "web", "gemessen"),
    ("strecke_per_skript_token", AUS_WOLKE, "mensch", "api", "angegeben"),
    ("strecke_ohne_kanal", AUS_WOLKE, "mensch", None, "angegeben"),
    ("ki_behauptet_quellen", AUS_WOLKE, "ki", "api", "angegeben"),
    ("strecke_ohne_quellen", STRECKE, "mensch", "web", "angegeben"),
    ("strecke_eine_quelle_fehlt", {**STRECKE, "pointSources": [QUELLE, None]}, "mensch", "web",
     "angegeben"),
    ("strecke_zu_wenige_quellen", {**STRECKE, "pointSources": [QUELLE]}, "mensch", "web",
     "angegeben"),
    ("ebene_modellmass",
     {**STRECKE, "pointSources": [QUELLE, {"source": "mesh", "layerId": "mesh"}]}, "mensch",
     "web", "ungeprueft"),
    ("ebene_massstab_nur_gesetzt",
     {**STRECKE, "pointSources": [QUELLE, {"source": "mesh", "layerId": "gesetzt"}]}, "mensch",
     "web", "ungeprueft"),
    ("ebene_nicht_mehr_im_paket",
     {**STRECKE, "pointSources": [QUELLE, {"source": "pointcloud", "layerId": "alt"}]},
     "mensch", "web", "unbekannt"),
    ("poi_aus_der_wolke", {"position": [1, 2, 3], "positionSource": QUELLE}, "mensch", "web",
     "gemessen"),
    ("poi_getippt", {"position": [1, 2, 3]}, "mensch", "web", "angegeben"),
]


@pytest.mark.parametrize(("daten", "herkunft", "kanal", "erwartet"), [f[1:] for f in FAELLE],
                         ids=[f[0] for f in FAELLE])
def test_grundlage(daten: dict[str, Any], herkunft: str, kanal: str | None,
                   erwartet: str) -> None:
    assert genauigkeit.grundlage(daten, herkunft, (BELEGT, NUR_GESETZT, MODELL), kanal) == erwartet
    assert erwartet in genauigkeit.GRUNDLAGEN


def test_api_liefert_grundlage_nach_kanal_und_herkunft(welt: Welt) -> None:  # noqa: F811
    # Die Testebene „punkte“ hat keinen Genauigkeitsnachweis: aus dem Viewer „ungeprueft“.
    gemessen = {**STRECKE, "pointSources": [QUELLE, QUELLE]}
    r = welt.post("bear", BASIS + "/messungen", {"id": "M-1", "daten": gemessen})
    assert r.status_code == 201 and r.json()["grundlage"] == "ungeprueft", r.text
    skript = welt.token("bear", "schreiben")
    r = skript.post(BASIS + "/messungen", json={"id": "M-S", "daten": gemessen})
    assert r.status_code == 201 and r.json()["grundlage"] == "angegeben", r.text
    agent = welt.token("bear", "schreiben", agent=True)
    r = agent.post(BASIS + "/messungen", json={"id": "M-KI", "daten": gemessen})
    assert r.status_code == 201 and r.json()["grundlage"] == "angegeben", r.text
    liste = {m["id"]: m["grundlage"] for m in
             welt.c["bh"].get(BASIS + "/messungen?vorschlaege=1").json()["messungen"]}
    assert liste == {"M-1": "ungeprueft", "M-S": "angegeben", "M-KI": "angegeben"}
    export = welt.c["bh"].get(BASIS + "/export.json").json()
    assert export["grundlage"]["messungen"] == {"M-1": "ungeprueft", "M-S": "angegeben"}
    assert set(export["grundlage"]["pois"]) == {p["id"] for p in export["pois"]}
    assert not any("grundlage" in e for e in (*export["pois"], *export["messungen"]))


def test_titel_per_token_aendert_die_grundlage_nicht(welt: Welt) -> None:  # noqa: F811
    gemessen = {**STRECKE, "pointSources": [QUELLE, QUELLE]}
    assert welt.post("bear", BASIS + "/messungen", {"id": "M-1", "daten": gemessen}).status_code \
        == 201
    poi = {"title": "T", "category": "Mangel", "note": "", "position": [1.0, 2.0, 3.0],
           "station": None, "positionSource": QUELLE}
    assert welt.post("bear", BASIS + "/pois", {"id": "P-W", "daten": poi}).status_code == 201
    skript = welt.token("bear", "schreiben")
    r = skript.patch(BASIS + "/pois/P-W", json={"daten": {"title": "neu"}, "version": 1})
    assert r.status_code == 200 and r.json()["grundlage"] == "ungeprueft", r.text
    r = skript.patch(BASIS + "/pois/P-W", json={"daten": {"position": [9.0, 9.0, 9.0],
                                                          "positionSource": QUELLE},
                                                "version": 2})
    assert r.status_code == 200 and r.json()["grundlage"] == "angegeben", r.text
    befunde = welt.c["bh"].get(BASIS + "/befunde").json()["befunde"]
    assert {b["poi"]["id"]: b["poi"]["grundlage"] for b in befunde}["P-W"] == "angegeben"


def test_pruef_und_loeschschritte_aendern_die_grundlage_nicht(welt: Welt) -> None:  # noqa: F811
    gemessen = {**STRECKE, "pointSources": [QUELLE, QUELLE]}
    assert welt.post("bear", BASIS + "/messungen", {"id": "M-1", "daten": gemessen}).status_code \
        == 201
    agent = welt.token("bear", "schreiben", agent=True)
    assert agent.delete(BASIS + "/messungen/M-1?version=1").status_code == 202
    assert welt.post("bh", BASIS + "/messungen/M-1/verwerfen", {"version": 2}).status_code == 200
    assert agent.post(BASIS + "/messungen", json={"id": "M-KI", "daten": gemessen}).status_code \
        == 201
    assert welt.post("bh", BASIS + "/messungen/M-KI/annehmen", {"version": 1}).status_code == 200
    liste = {m["id"]: m["grundlage"] for m in
             welt.c["bh"].get(BASIS + "/messungen").json()["messungen"]}
    assert liste == {"M-1": "ungeprueft", "M-KI": "angegeben"}
    assert inhalte.geometrie_kanaele(welt.conn(), "messungen", "ost") == {"M-1": "web",
                                                                          "M-KI": "api"}


def test_massstab_belegt_aus_accuracy_oder_scale_verified() -> None:
    def ebene(prov: dict[str, Any]) -> EbeneGenauigkeit:
        [e] = genauigkeit.ebenen([{"id": "e", "kind": "pointcloud", "provenance": prov}])
        return e

    assert ebene({"scaleVerified": True}).massstab_belegt
    assert not ebene({"scaleVerified": False, "scaleSource": "Drohne"}).massstab_belegt
    assert not ebene({}).massstab_belegt


def test_wiederherstellen_per_token_zaehlt_auch_bei_gleicher_geometrie(
        welt: Welt) -> None:  # noqa: F811
    gemessen = {**STRECKE, "pointSources": [QUELLE, QUELLE]}
    assert welt.post("bear", BASIS + "/messungen", {"id": "M-1", "daten": gemessen}).status_code \
        == 201
    r = welt.c["bear"].delete(BASIS + "/messungen/M-1?version=1", headers=csrf())
    assert r.status_code == 204, r.text
    skript = welt.token("bear", "schreiben")
    r = skript.post(BASIS + "/messungen", json={"id": "M-1", "daten": gemessen})
    assert r.status_code == 201 and r.json()["grundlage"] == "angegeben", r.text


def test_einzelabfrage_passt_zur_liste(welt: Welt) -> None:  # noqa: F811
    gemessen = {**STRECKE, "pointSources": [QUELLE, QUELLE]}
    welt.post("bear", BASIS + "/messungen", {"id": "M-1", "daten": gemessen})
    welt.token("bear", "schreiben").post(BASIS + "/messungen",
                                         json={"id": "M-2", "daten": gemessen})
    welt.post("leitung", BASIS + "/pois", {"id": "P-2", "daten": {
        "title": "T", "category": "Mangel", "note": "", "position": [1.0, 2.0, 3.0],
        "station": None, "positionSource": QUELLE}})
    for tabelle in ("pois", "messungen"):
        alle = inhalte.geometrie_kanaele(welt.conn(), tabelle, "ost")
        assert alle
        for eid, kanal in alle.items():
            assert inhalte.geometrie_kanaele(welt.conn(), tabelle, "ost", eid) == {eid: kanal}
