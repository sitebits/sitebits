"""Genauigkeit je Ebene (F15): dieselben Fälle wie web/tests/genauigkeit.test.ts, dazu die
Weitergabe in Karte, CSV, JSON, Mängelliste und Git-Export."""

import csv
import io
import json
import zipfile
from typing import Any

from fadenwelt import BASIS, Welt, welt  # noqa: F401  (Fixture)
from fastapi.testclient import TestClient
from pakete import lege_paket

from scanview_server import export, genauigkeit, gitexport, maengelliste
from scanview_server.inhalte import Eintrag
from scanview_server.katalog import Katalog
from scanview_server.routen_api import karte_json

PILOT: dict[str, Any] = {
    "formatVersion": 1,
    "absolute": {"status": "adjustedOnly", "controlResiduals": None,
                 "cameraGroups": [{"name": "Nadir", "n": 900}]},
    "scale": {"status": "set", "setFrom": ["A-B Laser"]},
    "points": [],
    "distances": [{"name": "A-B Laser", "role": "control", "errorPct": 0}],
    "source": {"software": "Photogrammetrie-Werkzeug", "version": "2.1.3",
               "created": "2026-10-11T07:00:00Z", "projectSha256": "ab" * 32, "crs": "EPSG:25833"},
    "report": {"filename": "report.pdf", "sha256": "cd" * 32},
}
PASS = {"n": 6, "rmse": {"x": 0.01, "y": 0.01, "z": 0.01}, "max": {"xy": 0.02, "z": 0.01}}
GEPRUEFT: dict[str, Any] = {
    **PILOT,
    "absolute": {"status": "checked", "n": 4, "rmse": {"x": 0.03, "y": 0.04, "z": 0.021},
                 "max": {"xy": 0.08, "z": 0.04}, "withoutEstimate": 0, "controlResiduals": PASS},
    "scale": {"status": "checked", "n": 1, "maxErrorPct": 0.05, "toleranceFactor": 3,
              "setFrom": ["A-B Laser"]},
    "distances": [*PILOT["distances"], {"name": "C-D", "role": "check", "errorPct": 0.05}],
    "report": None,
}


def test_pilot_gesetzt_und_nur_gnss_ohne_plusminus() -> None:
    z = genauigkeit.panel_zeilen(PILOT)
    assert z == [
        "Lage nicht unabhängig geprüft (nur Drohnen-GNSS)",
        "Maßstab aus 1 Strecke gesetzt (A-B Laser), nicht geprüft",
        "Quelle: Photogrammetrie-Werkzeug 2.1.3, 2026-10-11, Projekt abababab…",
        "Bericht: report.pdf (SHA-256 cdcdcdcd…)",
    ]
    assert "±" not in " ".join(z) and "RMSE" not in " ".join(z)


def test_kontrollpunkte_und_kontrollstrecke() -> None:
    assert genauigkeit.panel_zeilen(GEPRUEFT)[:2] == [
        "Lage geprüft an 4 Kontrollpunkten: RMSE Lage 5 cm, Höhe 2,1 cm (max. 8 cm / 4 cm)",
        "Maßstab geprüft: +0,05 % an 1 Kontrollstrecke",
    ]
    assert genauigkeit.messhinweise(GEPRUEFT) == ["Maßstab geprüft: +0,05 % an 1 Kontrollstrecke"]


def test_passpunkte_abweichung_und_kein_nachweis() -> None:
    pass_ = {**PILOT, "absolute": {"status": "adjustedOnly", "controlResiduals": PASS}}
    assert genauigkeit.panel_zeilen(pass_)[0] == (
        "Lage nicht unabhängig geprüft (nur Ausgleichsresiduen an 6 Passpunkten)")
    ab = {**GEPRUEFT, "scale": {"status": "deviates", "n": 2, "maxErrorPct": 0.4}}
    assert genauigkeit.panel_zeilen(ab)[1] == "Maßstab weicht ab: max. 0,40 % an 2 Kontrollstrecken"
    assert genauigkeit.panel_zeilen(None) == ["kein Genauigkeitsnachweis"]
    # Unlesbar zählt hier als fehlend (der Viewer lehnt das Paket ab, der Dienst läuft weiter).
    assert genauigkeit.panel_zeilen({**GEPRUEFT, "absolute": {"status": "checked"}}) == [
        "kein Genauigkeitsnachweis"]


def _ebenen() -> tuple[genauigkeit.EbeneGenauigkeit, ...]:
    return genauigkeit.ebenen([
        {"id": "w", "kind": "pointcloud", "title": "Wolke",
         "provenance": {"kind": "photogrammetry", "interpolated": False, "scaleVerified": True,
                        "scaleSource": "A-B Laser", "accuracy": PILOT}},
        {"id": "m", "kind": "mesh", "title": "",
         "provenance": {"kind": "photogrammetry", "interpolated": True, "scaleVerified": False,
                        "scaleSource": "unit:cli"}},
        {"id": "p", "kind": "panoramas"},
    ])


def test_ebenen_und_messhinweise_wie_viewer() -> None:
    e = _ebenen()
    assert [(g.id, g.titel, g.art) for g in e] == [("w", "Wolke", "pointcloud"), ("m", "m", "mesh")]
    assert e[1].zeilen == ("kein Genauigkeitsnachweis",)
    assert e[1].hinweise == ("Modellmaß — Maßstab nicht belegt (Quelle: unit:cli)",
                             "Modelloberfläche (teils interpoliert)")
    je = {g.id: g.hinweise for g in e}
    w = {"source": "pointcloud", "layerId": "w"}
    quellen = [w, w, None]
    assert genauigkeit.mess_hinweis(quellen, je) == (
        "Maßstab aus 1 Strecke gesetzt (A-B Laser), nicht geprüft · "
        "Lage nicht unabhängig geprüft (nur Drohnen-GNSS)")
    assert genauigkeit.mess_hinweis(None, je) == ""


def test_modellmass_aus_mehreren_ebenen_eine_meldung() -> None:
    je = {
        "a": ("Modellmaß — Maßstab nicht belegt (Quelle: unit:file)",),
        "b": ("Modellmaß — Maßstab nicht belegt (Quelle: unit:cli)",
              "Modelloberfläche (teils interpoliert)"),
    }
    quellen = [{"layerId": "a"}, {"layerId": "b"}, {"layerId": "a"}]
    assert genauigkeit.mess_hinweis(quellen, je) == (
        "Modellmaß — Maßstab nicht belegt (Quelle: unit:file, unit:cli) · "
        "Modelloberfläche (teils interpoliert)")


def _messung() -> Eintrag:
    daten = {"kind": "distance", "points": [[0, 0, 0], [3, 4, 0]],
             "pointSources": [{"source": "pointcloud", "layerId": "w"}] * 2}
    return Eintrag(id="M-1", daten=daten, version=1, paket_revision=1, erstellt_von=None,
                   erstellt_am=0, geaendert_von=None, geaendert_von_name="A", geaendert_am=0)


def test_csv_spalte_hinweis_und_json() -> None:
    e = _ebenen()
    text = export.csv_text([], [_messung()], hinweise={g.id: g.hinweise for g in e})
    zeilen = list(csv.reader(io.StringIO(text.lstrip("﻿")), delimiter=";"))
    assert zeilen[0][-1] == "Hinweis" and len(zeilen[1]) == len(zeilen[0])
    assert zeilen[1][-1].startswith("Maßstab aus 1 Strecke gesetzt")
    j = export.json_export("p", 1, "T", "", [], [_messung()], 0, ebenen=e)
    assert j["genauigkeit"][0] == {
        "ebene": "w", "titel": "Wolke", "art": "pointcloud", "zeilen": list(e[0].zeilen),
        "nachweis": {"lage": {"status": "adjustedOnly"},
                     "massstab": {"status": "set", "setFrom": ["A-B Laser"]}}}
    assert j["genauigkeit"][1]["nachweis"] is None


def test_nachweis_kennzahlen_nur_bei_kontrollpunkten() -> None:
    (g,) = genauigkeit.ebenen([{"id": "w", "kind": "pointcloud",
                                "provenance": {"accuracy": GEPRUEFT}}])
    assert g.werte == {"lage": {"status": "checked", "n": 4, "rmse": GEPRUEFT["absolute"]["rmse"],
                                "max": GEPRUEFT["absolute"]["max"]},
                       "massstab": {"status": "checked", "n": 1, "maxErrorPct": 0.05,
                                    "setFrom": ["A-B Laser"]}}


def test_maengelliste_nennt_genauigkeit_in_zeile_1() -> None:
    daten = maengelliste.xlsx([], "T", 1, 0, genauigkeit="Lage nicht unabhängig geprüft")
    with zipfile.ZipFile(io.BytesIO(daten)) as z:
        blatt = z.read("xl/worksheets/sheet1.xml").decode()
    assert "Genauigkeit" in blatt and "Lage nicht unabhängig geprüft" in blatt
    ohne = maengelliste.xlsx([], "T", 1, 0)
    with zipfile.ZipFile(io.BytesIO(ohne)) as z:
        assert "Genauigkeit" not in z.read("xl/worksheets/sheet1.xml").decode()


def test_katalog_karte_und_gitexport(client: TestClient) -> None:
    settings = client.app.state.settings  # type: ignore[attr-defined]
    d = lege_paket(settings.data_dir, "pilot")
    m = json.loads((d / "project.json").read_text())
    m["layers"][0]["provenance"] = {"kind": "photogrammetry", "interpolated": False,
                                    "scaleVerified": True, "scaleSource": "A-B",
                                    "accuracy": PILOT}
    (d / "project.json").write_text(json.dumps(m))
    k = Katalog(settings.data_dir)
    k.laden()
    karte = k.get("pilot")
    assert karte is not None and karte.genauigkeit[0].zeilen[0].startswith("Lage nicht unabhängig")
    assert karte_json(karte, None, "admin")["genauigkeit"][0]["art"] == "pointcloud"
    datei = gitexport._genauigkeit_datei(k, "pilot")
    assert datei is not None
    assert json.loads(datei)["ebenen"][0]["ebene"] == "punkte"
    assert gitexport._genauigkeit_datei(k, "gibt-es-nicht") is None
    assert gitexport._genauigkeit_datei(None, "pilot") is None


def test_projektroute_liefert_genauigkeit(welt: Welt) -> None:  # noqa: F811 — Fixture
    """Über HTTP wie für MCP `projekt_lesen` (reicht die Antwort unverändert durch)."""
    d = welt.c["bear"].app.state.settings.data_dir / "ost"  # type: ignore[attr-defined]
    m = json.loads((d / "project.json").read_text())
    m["layers"][0]["provenance"] = {"kind": "photogrammetry", "interpolated": False,
                                    "scaleVerified": True, "scaleSource": "A-B",
                                    "accuracy": GEPRUEFT}
    (d / "project.json").write_text(json.dumps(m))
    welt.c["bear"].app.state.katalog.laden()  # type: ignore[attr-defined]
    karte = welt.c["bear"].get(BASIS).json()
    assert karte["genauigkeit"][0]["nachweis"]["lage"]["n"] == 4
    assert karte["einheit"] == "[m, lokal]"  # Testpaket ohne Koordinatensystem
    assert karte["genauigkeit"][0]["zeilen"][1] == "Maßstab geprüft: +0,05 % an 1 Kontrollstrecke"


def test_maengelliste_kurztext_je_ebene_wenn_verschieden() -> None:
    from scanview_server.katalog import Karte
    from scanview_server.routen_export import _genauigkeit_kurz

    e = _ebenen()
    karte = Karte(id="p", titel="P", punkte=None, standpunkte=None, ausdehnung=None, crs="lokal",
                  scandatum=None, groesse_bytes=0, draufsicht=False, genauigkeit=e)
    assert _genauigkeit_kurz(karte) == (
        "Wolke: Lage nicht unabhängig geprüft (nur Drohnen-GNSS) · Maßstab aus 1 Strecke gesetzt "
        "(A-B Laser), nicht geprüft | m: kein Genauigkeitsnachweis")
    gleich = Karte(**{**karte.__dict__, "genauigkeit": (e[0], e[0])})
    assert _genauigkeit_kurz(gleich).startswith("Lage nicht unabhängig")
