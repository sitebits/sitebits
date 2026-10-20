"""Mutationstests für inhalte.py: Validierung und Normalisierung.

Deckt alle Prüfregeln von poi_pruefen, messung_pruefen und pruefen ab,
inklusive Grenzwerte, Typen und exakte Fehlermeldungen.
"""

from __future__ import annotations

from typing import Any

import pytest

from scanview_server import inhalte
from scanview_server.web import Ungueltig

GUT_POI: dict[str, Any] = {
    "title": "Hauptportal",
    "category": "Eingang",
    "note": "Zweiflügelige Holztür",
    "position": [10.5, 20.25, -3.75],
    "station": "ST-01_a",
}

GUT_STRECKE: dict[str, Any] = {
    "kind": "distance",
    "points": [[0.0, 0.0, 0.0], [3.0, 4.0, 0.0]],
}

GUT_FLAECHE: dict[str, Any] = {
    "kind": "area",
    "points": [[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [4.0, 3.0, 0.0]],
    "areaM2": 6.0,
    "planarityM": 0.02,
}


# --- POI Datenobjekt und Unbekannte Felder ---


def test_poi_pruefen_erfordert_dict() -> None:
    for ungueltig in [None, "kein-dict", 123, 4.5, True, False, [1, 2, 3], (1, 2)]:
        with pytest.raises(Ungueltig, match=r"^POI-Daten müssen ein Objekt sein\.$") as exc_info:
            inhalte.poi_pruefen(ungueltig)
        assert str(exc_info.value) == "POI-Daten müssen ein Objekt sein."


def test_poi_pruefen_unbekanntes_feld() -> None:
    d = dict(GUT_POI)
    d["extra_feld"] = "unerlaubt"
    with pytest.raises(Ungueltig, match=r"^Unbekanntes Feld in den POI-Daten\.$") as exc_info:
        inhalte.poi_pruefen(d)
    assert str(exc_info.value) == "Unbekanntes Feld in den POI-Daten."


# --- POI Titel ---


def test_poi_titel_pflicht_und_typen() -> None:
    d = dict(GUT_POI)
    del d["title"]
    with pytest.raises(Ungueltig, match=r"^Titel fehlt\.$"):
        inhalte.poi_pruefen(d)

    d = dict(GUT_POI)
    d["title"] = None
    with pytest.raises(Ungueltig, match=r"^Titel fehlt\.$"):
        inhalte.poi_pruefen(d)

    for wert in [123, 0, 1.5, True, False, [], {}]:
        d = dict(GUT_POI)
        d["title"] = wert
        with pytest.raises(Ungueltig, match=r"^Titel muss Text sein\.$") as exc_info:
            inhalte.poi_pruefen(d)
        assert str(exc_info.value) == "Titel muss Text sein."


def test_poi_titel_leerer_text() -> None:
    for leer in ["", " ", "   ", "\t", "\n", " \t \n "]:
        d = dict(GUT_POI)
        d["title"] = leer
        with pytest.raises(Ungueltig, match=r"^Titel fehlt\.$") as exc_info:
            inhalte.poi_pruefen(d)
        assert str(exc_info.value) == "Titel fehlt."


def test_poi_titel_grenzlaenge_200() -> None:
    d = dict(GUT_POI)
    d["title"] = "A" * 200
    res = inhalte.poi_pruefen(d)
    assert res["title"] == "A" * 200

    d["title"] = "  " + ("B" * 200) + "  "
    res = inhalte.poi_pruefen(d)
    assert res["title"] == "B" * 200

    d["title"] = "A" * 201
    with pytest.raises(Ungueltig, match=r"^Titel länger als 200 Zeichen\.$") as exc_info:
        inhalte.poi_pruefen(d)
    assert str(exc_info.value) == "Titel länger als 200 Zeichen."


# --- POI Kategorie ---


def test_poi_kategorie_typen_und_leer() -> None:
    d = dict(GUT_POI)
    d["category"] = None
    res = inhalte.poi_pruefen(d)
    assert res["category"] == ""

    del d["category"]
    res = inhalte.poi_pruefen(d)
    assert res["category"] == ""

    d = dict(GUT_POI)
    d["category"] = "   "
    res = inhalte.poi_pruefen(d)
    assert res["category"] == ""

    for ungueltig in [123, 0, 4.2, True, False, [], {}]:
        d = dict(GUT_POI)
        d["category"] = ungueltig
        with pytest.raises(Ungueltig, match=r"^Kategorie muss Text sein\.$") as exc_info:
            inhalte.poi_pruefen(d)
        assert str(exc_info.value) == "Kategorie muss Text sein."


def test_poi_kategorie_grenzlaenge_60() -> None:
    d = dict(GUT_POI)
    d["category"] = "K" * 60
    res = inhalte.poi_pruefen(d)
    assert res["category"] == "K" * 60

    d["category"] = "  " + ("K" * 60) + "  "
    res = inhalte.poi_pruefen(d)
    assert res["category"] == "K" * 60

    d["category"] = "K" * 61
    with pytest.raises(Ungueltig, match=r"^Kategorie länger als 60 Zeichen\.$") as exc_info:
        inhalte.poi_pruefen(d)
    assert str(exc_info.value) == "Kategorie länger als 60 Zeichen."


def test_poi_kategorie_nfc_normalisierung() -> None:
    # NFD: "u" + Combining Diaeresis (U+0308) -> NFC: "ü" (U+00FC)
    nfd = "Ru\u0308ckmeldung"
    d = dict(GUT_POI)
    d["category"] = nfd
    res = inhalte.poi_pruefen(d)
    assert res["category"] == "Rückmeldung"
    assert res["category"] != nfd


# --- POI Notiz ---


def test_poi_notiz_typen_und_leer() -> None:
    d = dict(GUT_POI)
    d["note"] = None
    res = inhalte.poi_pruefen(d)
    assert res["note"] == ""

    del d["note"]
    res = inhalte.poi_pruefen(d)
    assert res["note"] == ""

    d = dict(GUT_POI)
    d["note"] = "   \n\t  "
    res = inhalte.poi_pruefen(d)
    assert res["note"] == ""

    for ungueltig in [123, 0, 5.5, True, False, [], {}]:
        d = dict(GUT_POI)
        d["note"] = ungueltig
        with pytest.raises(Ungueltig, match=r"^Notiz muss Text sein\.$") as exc_info:
            inhalte.poi_pruefen(d)
        assert str(exc_info.value) == "Notiz muss Text sein."


def test_poi_notiz_grenzlaenge_2000() -> None:
    d = dict(GUT_POI)
    d["note"] = "N" * 2000
    res = inhalte.poi_pruefen(d)
    assert res["note"] == "N" * 2000

    d["note"] = "  " + ("N" * 2000) + "  "
    res = inhalte.poi_pruefen(d)
    assert res["note"] == "N" * 2000

    d["note"] = "N" * 2001
    with pytest.raises(Ungueltig, match=r"^Notiz länger als 2000 Zeichen\.$") as exc_info:
        inhalte.poi_pruefen(d)
    assert str(exc_info.value) == "Notiz länger als 2000 Zeichen."


# --- POI Standpunkt (station) ---


def test_poi_standpunkt_gueltig() -> None:
    d = dict(GUT_POI)
    d["station"] = None
    assert inhalte.poi_pruefen(d)["station"] is None

    del d["station"]
    assert inhalte.poi_pruefen(d)["station"] is None

    # 1 Zeichen
    d["station"] = "A"
    assert inhalte.poi_pruefen(d)["station"] == "A"

    # 32 Zeichen (Maximum)
    max_st = "A1_-" * 8  # 32 Zeichen
    assert len(max_st) == 32
    d["station"] = max_st
    assert inhalte.poi_pruefen(d)["station"] == max_st


def test_poi_standpunkt_ungueltig() -> None:
    for bad in ["", " ", "A" * 33, "S 1", "S.1", "S/1", "S#1", 123, True, False, [], {}]:
        d = dict(GUT_POI)
        d["station"] = bad
        with pytest.raises(Ungueltig, match=r"^Ungültiger Standpunkt\.$") as exc_info:
            inhalte.poi_pruefen(d)
        assert str(exc_info.value) == "Ungültiger Standpunkt."


# --- POI Position (Punkt) ---


def test_poi_position_gueltig_konvertierung_in_float() -> None:
    d = dict(GUT_POI)
    d["position"] = [1, 2, -3]
    res = inhalte.poi_pruefen(d)
    assert res["position"] == [1.0, 2.0, -3.0]
    assert all(isinstance(v, float) for v in res["position"])


def test_poi_position_ungueltige_strukturen() -> None:
    for p in [
        None,
        "1,2,3",
        (1.0, 2.0, 3.0),
        [],
        [1.0],
        [1.0, 2.0],
        [1.0, 2.0, 3.0, 4.0],
        [True, 1.0, 2.0],
        [1.0, False, 2.0],
        [1.0, 2.0, True],
        [float("nan"), 1.0, 2.0],
        [1.0, float("inf"), 2.0],
        [1.0, 2.0, float("-inf")],
        ["1", 2.0, 3.0],
        [1.0, None, 3.0],
    ]:
        d = dict(GUT_POI)
        d["position"] = p
        with pytest.raises(
            Ungueltig,
            match=r"^Position braucht drei endliche Zahlen \(Weltkoordinaten in m\)\.$",
        ) as exc_info:
            inhalte.poi_pruefen(d)
        assert (
            str(exc_info.value) == "Position braucht drei endliche Zahlen (Weltkoordinaten in m)."
        )


# --- Messung Datenobjekt und Unbekannte Felder ---


def test_messung_pruefen_erfordert_dict() -> None:
    for ungueltig in [None, "kein-dict", 123, 4.5, True, False, [1, 2, 3], (1, 2)]:
        with pytest.raises(Ungueltig, match=r"^Messdaten müssen ein Objekt sein\.$") as exc_info:
            inhalte.messung_pruefen(ungueltig)
        assert str(exc_info.value) == "Messdaten müssen ein Objekt sein."


def test_messung_pruefen_unbekanntes_feld() -> None:
    d = dict(GUT_STRECKE)
    d["farbe"] = "rot"
    with pytest.raises(Ungueltig, match=r"^Unbekanntes Feld in den Messdaten\.$") as exc_info:
        inhalte.messung_pruefen(d)
    assert str(exc_info.value) == "Unbekanntes Feld in den Messdaten."


# --- Messart (kind) ---


def test_messung_unbekannte_messart() -> None:
    for k in [None, 123, True, False, "", "DISTANCE", "circle", "flaeche", "linie"]:
        d = {"kind": k, "points": [[0, 0, 0], [1, 1, 1]]}
        with pytest.raises(
            Ungueltig,
            match=r"^Unbekannte Messart \(erlaubt: distance, polyline, area, height\)\.$",
        ) as exc_info:
            inhalte.messung_pruefen(d)
        assert (
            str(exc_info.value) == "Unbekannte Messart (erlaubt: distance, polyline, area, height)."
        )


# --- Messung Eckpunktzahlen je Art ---


def test_messung_distance_eckpunkte() -> None:
    # distance braucht genau 2
    for anz in [0, 1, 3, 4]:
        d = {"kind": "distance", "points": [[float(i), 0.0, 0.0] for i in range(anz)]}
        with pytest.raises(
            Ungueltig, match=r"^Messart distance braucht 2 Eckpunkte\.$"
        ) as exc_info:
            inhalte.messung_pruefen(d)
        assert str(exc_info.value) == "Messart distance braucht 2 Eckpunkte."

    d = {"kind": "distance", "points": "keine-liste"}
    with pytest.raises(Ungueltig, match=r"^Messart distance braucht 2 Eckpunkte\.$"):
        inhalte.messung_pruefen(d)

    d_ok = {"kind": "distance", "points": [[0, 0, 0], [1, 2, 3]]}
    res = inhalte.messung_pruefen(d_ok)
    assert res["kind"] == "distance"
    assert res["points"] == [[0.0, 0.0, 0.0], [1.0, 2.0, 3.0]]


def test_messung_height_eckpunkte() -> None:
    # height braucht genau 2
    for anz in [0, 1, 3]:
        d = {"kind": "height", "points": [[float(i), 0.0, 0.0] for i in range(anz)]}
        with pytest.raises(Ungueltig, match=r"^Messart height braucht 2 Eckpunkte\.$") as exc_info:
            inhalte.messung_pruefen(d)
        assert str(exc_info.value) == "Messart height braucht 2 Eckpunkte."

    d_ok = {"kind": "height", "points": [[0, 0, 0], [0, 0, 5]]}
    res = inhalte.messung_pruefen(d_ok)
    assert res["kind"] == "height"
    assert res["points"] == [[0.0, 0.0, 0.0], [0.0, 0.0, 5.0]]


def test_messung_polyline_eckpunkte() -> None:
    # polyline braucht 2 bis 1000
    for anz in [0, 1]:
        d = {"kind": "polyline", "points": [[float(i), 0.0, 0.0] for i in range(anz)]}
        with pytest.raises(
            Ungueltig, match=r"^Messart polyline braucht 2 bis 1000 Eckpunkte\.$"
        ) as exc_info:
            inhalte.messung_pruefen(d)
        assert str(exc_info.value) == "Messart polyline braucht 2 bis 1000 Eckpunkte."

    d_1001 = {"kind": "polyline", "points": [[float(i), 0.0, 0.0] for i in range(1001)]}
    with pytest.raises(
        Ungueltig, match=r"^Messart polyline braucht 2 bis 1000 Eckpunkte\.$"
    ) as exc_info:
        inhalte.messung_pruefen(d_1001)
    assert str(exc_info.value) == "Messart polyline braucht 2 bis 1000 Eckpunkte."

    # Grenzwerte 2 und 1000 erlaubt
    d_2 = {"kind": "polyline", "points": [[0, 0, 0], [1, 1, 1]]}
    assert len(inhalte.messung_pruefen(d_2)["points"]) == 2

    d_1000 = {"kind": "polyline", "points": [[float(i), 0.0, 0.0] for i in range(1000)]}
    assert len(inhalte.messung_pruefen(d_1000)["points"]) == 1000


def test_messung_area_eckpunkte() -> None:
    # area braucht 3 bis 1000
    for anz in [0, 1, 2]:
        d = {"kind": "area", "points": [[float(i), 0.0, 0.0] for i in range(anz)]}
        with pytest.raises(
            Ungueltig, match=r"^Messart area braucht 3 bis 1000 Eckpunkte\.$"
        ) as exc_info:
            inhalte.messung_pruefen(d)
        assert str(exc_info.value) == "Messart area braucht 3 bis 1000 Eckpunkte."

    d_1001 = {"kind": "area", "points": [[float(i), 0.0, 0.0] for i in range(1001)]}
    with pytest.raises(
        Ungueltig, match=r"^Messart area braucht 3 bis 1000 Eckpunkte\.$"
    ) as exc_info:
        inhalte.messung_pruefen(d_1001)
    assert str(exc_info.value) == "Messart area braucht 3 bis 1000 Eckpunkte."

    # Grenzwerte 3 und 1000 erlaubt
    d_3 = {"kind": "area", "points": [[0, 0, 0], [1, 0, 0], [1, 1, 0]]}
    assert len(inhalte.messung_pruefen(d_3)["points"]) == 3

    d_1000 = {"kind": "area", "points": [[float(i), 0.0, 0.0] for i in range(1000)]}
    assert len(inhalte.messung_pruefen(d_1000)["points"]) == 1000


# --- Eckpunkt-Nummerierung in Fehlermeldung ---


def test_messung_eckpunkt_nummerierung_in_meldung() -> None:
    d1 = {"kind": "area", "points": [[True, 0, 0], [1, 0, 0], [1, 1, 0]]}
    with pytest.raises(
        Ungueltig,
        match=r"^Eckpunkt 1 braucht drei endliche Zahlen \(Weltkoordinaten in m\)\.$",
    ) as exc_info:
        inhalte.messung_pruefen(d1)
    assert str(exc_info.value) == "Eckpunkt 1 braucht drei endliche Zahlen (Weltkoordinaten in m)."

    d2 = {"kind": "area", "points": [[0, 0, 0], [1, "x", 0], [1, 1, 0]]}
    with pytest.raises(
        Ungueltig,
        match=r"^Eckpunkt 2 braucht drei endliche Zahlen \(Weltkoordinaten in m\)\.$",
    ) as exc_info:
        inhalte.messung_pruefen(d2)
    assert str(exc_info.value) == "Eckpunkt 2 braucht drei endliche Zahlen (Weltkoordinaten in m)."

    d3 = {"kind": "area", "points": [[0, 0, 0], [1, 0, 0], [1, 1]]}
    with pytest.raises(
        Ungueltig,
        match=r"^Eckpunkt 3 braucht drei endliche Zahlen \(Weltkoordinaten in m\)\.$",
    ) as exc_info:
        inhalte.messung_pruefen(d3)
    assert str(exc_info.value) == "Eckpunkt 3 braucht drei endliche Zahlen (Weltkoordinaten in m)."


# --- Flächenwerte (areaM2, planarityM) ---


def test_flaechenwerte_nur_bei_kind_area() -> None:
    for art in ["distance", "polyline", "height"]:
        punkte = [[0, 0, 0], [1, 1, 1]]
        with pytest.raises(Ungueltig, match=r"^areaM2 gibt es nur bei Flächen\.$") as exc_info:
            inhalte.messung_pruefen({"kind": art, "points": punkte, "areaM2": 5.0})
        assert str(exc_info.value) == "areaM2 gibt es nur bei Flächen."

        with pytest.raises(Ungueltig, match=r"^planarityM gibt es nur bei Flächen\.$") as exc_info:
            inhalte.messung_pruefen({"kind": art, "points": punkte, "planarityM": 0.1})
        assert str(exc_info.value) == "planarityM gibt es nur bei Flächen."


def test_flaechenwerte_gueltig_und_optional() -> None:
    basis = {"kind": "area", "points": [[0, 0, 0], [1, 0, 0], [1, 1, 0]]}
    # Ohne Flächenwerte
    res = inhalte.messung_pruefen(basis)
    assert "areaM2" not in res and "planarityM" not in res

    # Mit None (ignoriert)
    res_none = inhalte.messung_pruefen({**basis, "areaM2": None, "planarityM": None})
    assert "areaM2" not in res_none and "planarityM" not in res_none

    # Mit 0 (Grenzwert ab 0)
    res_null = inhalte.messung_pruefen({**basis, "areaM2": 0, "planarityM": 0.0})
    assert res_null["areaM2"] == 0.0 and isinstance(res_null["areaM2"], float)
    assert res_null["planarityM"] == 0.0 and isinstance(res_null["planarityM"], float)

    # Mit positiven Werten
    res_pos = inhalte.messung_pruefen({**basis, "areaM2": 15, "planarityM": 0.005})
    assert res_pos["areaM2"] == 15.0 and isinstance(res_pos["areaM2"], float)
    assert res_pos["planarityM"] == 0.005 and isinstance(res_pos["planarityM"], float)


def test_flaechenwerte_ungueltig() -> None:
    basis = {"kind": "area", "points": [[0, 0, 0], [1, 0, 0], [1, 1, 0]]}
    for bad in [
        -0.0001,
        -1,
        -5.0,
        True,
        False,
        "10",
        float("nan"),
        float("inf"),
        float("-inf"),
        [],
        {},
    ]:
        with pytest.raises(
            Ungueltig, match=r"^areaM2 muss eine endliche Zahl ab 0 sein\.$"
        ) as exc_info:
            inhalte.messung_pruefen({**basis, "areaM2": bad})
        assert str(exc_info.value) == "areaM2 muss eine endliche Zahl ab 0 sein."

        with pytest.raises(
            Ungueltig, match=r"^planarityM muss eine endliche Zahl ab 0 sein\.$"
        ) as exc_info:
            inhalte.messung_pruefen({**basis, "planarityM": bad})
        assert str(exc_info.value) == "planarityM muss eine endliche Zahl ab 0 sein."


# --- pruefen Weiche ---


def test_pruefen_weiche() -> None:
    poi_res = inhalte.pruefen("pois", GUT_POI)
    assert poi_res["title"] == "Hauptportal"
    assert "kind" not in poi_res

    mess_res = inhalte.pruefen("messungen", GUT_STRECKE)
    assert mess_res["kind"] == "distance"
    assert "title" not in mess_res
