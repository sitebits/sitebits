"""API-Vertrag des Befund-Fadens (Plan §API): Faden, Einträge, Zurückziehen, Befundliste,
Stand an /pois, Projektkarte."""

from typing import Any

from conftest import csrf
from fadenwelt import BASIS, FADEN, INTERN, Welt, welt  # noqa: F401  (Fixture)


def test_faden_lesen_vertrag(welt: Welt) -> None:  # noqa: F811 — Fixture
    r = welt.c["bh"].get(FADEN)
    assert r.status_code == 200
    d = r.json()
    assert set(d) == {"stand", "eintraege", "rechte"}
    assert d["stand"] == {"status": "offen", "zustaendig": None, "frist": None, "version": 1,
                          "ueberfaellig": False}
    e = d["eintraege"][0]
    assert set(e) == {"schluessel", "art", "text", "wert", "intern", "erstellt_von", "erstellt_am",
                      "kanal", "zurueckgezogen_am", "herkunft", "bezug"}
    assert e["text"] == INTERN and e["intern"] is True
    assert e["erstellt_von"] == {"id": welt.id["bh"], "name": "Name bh"}
    assert d["rechte"] == {"kommentieren": True, "status": ["in_arbeit", "erledigt", "abgelehnt"],
                           "zustaendig": True, "frist": True, "intern": True}


def test_eintrag_anlegen_201_und_stand(welt: Welt) -> None:  # noqa: F811 — Fixture
    r = welt.post("bear", FADEN, {"art": "status", "wert": "in_arbeit", "version": 1})
    assert r.status_code == 201, r.text
    assert set(r.json()) == {"eintrag", "stand"}
    assert r.json()["stand"]["status"] == "in_arbeit" and r.json()["stand"]["version"] == 2
    assert r.json()["stand"]["ueberfaellig"] is False
    assert r.json()["eintrag"]["art"] == "status" and r.json()["eintrag"]["kanal"] == "web"
    r = welt.post("bear", FADEN, {"art": "frist", "wert": "2026-11-30", "version": 1})
    assert r.status_code == 409
    assert r.json()["aktuell"] == {"status": "in_arbeit", "zustaendig": None, "frist": None,
                                   "version": 2}
    r = welt.post("bear", FADEN, {"art": "frist", "wert": "2026-11-30"})
    assert r.status_code == 422


def test_zurueckziehen_vertrag(welt: Welt) -> None:  # noqa: F811 — Fixture
    pfad = f"{FADEN}/{welt.oeffentlich_id}/zurueckziehen"
    assert welt.post("bh", pfad).status_code == 403
    r = welt.post("bear", pfad)
    assert r.status_code == 200 and set(r.json()) == {"eintrag"}
    assert r.json()["eintrag"]["text"] is None and r.json()["eintrag"]["zurueckgezogen_am"]
    texte = [e["text"] for e in welt.c["betr"].get(FADEN).json()["eintraege"]]
    assert texte == [None]
    assert welt.post("bear", f"{FADEN}/0000000000000000/zurueckziehen").status_code == 404


def test_pois_liefern_stand_nur_mit_schalter(welt: Welt) -> None:  # noqa: F811 — Fixture
    poi = welt.c["betr"].get(BASIS + "/pois").json()["pois"][0]
    assert poi["stand"] == {"status": "offen", "zustaendig": None, "frist": None, "version": 1}
    welt.sql("UPDATE projekte SET befund_faeden=0")
    assert "stand" not in welt.c["betr"].get(BASIS + "/pois").json()["pois"][0]


def test_befundliste_vertrag(welt: Welt) -> None:  # noqa: F811 — Fixture
    zust = {"gewerk_id": welt.gewerk["Dach"], "nutzer_id": None}
    assert welt.post("leitung", FADEN, {"art": "zustaendig", "wert": zust,
                                        "version": 1}).status_code == 201
    assert welt.post("leitung", FADEN, {"art": "frist", "wert": "2020-01-01",
                                        "version": 2}).status_code == 201
    r = welt.c["rueck"].get(BASIS + "/befunde", params={"zustaendig": "gewerk",
                                                         "frist": "ueberfaellig"})
    assert r.status_code == 200
    d = r.json()
    assert d["zaehler"] == {"ueberfaellig_meine": 1}
    b = d["befunde"][0]
    assert set(b) == {"poi", "stand", "ueberfaellig", "letzter_eintrag_am"}
    assert b["poi"]["id"] == "P-1" and b["ueberfaellig"] is True and b["stand"]["frist"]
    assert welt.c["rueck"].get(BASIS + "/befunde", params={"status": "erledigt"}).json()[
        "befunde"] == []
    assert welt.c["rueck"].get(BASIS + "/befunde", params={"status": "quatsch"}).status_code == 422
    assert welt.c["rueck"].get(BASIS + "/befunde", params={"frist": "morgen"}).status_code == 422


def test_projektkarte_zaehlt_meine_ueberfaelligen(welt: Welt) -> None:  # noqa: F811 — Fixture
    zust = {"gewerk_id": welt.gewerk["Dach"], "nutzer_id": welt.id["rueck"]}
    welt.post("leitung", FADEN, {"art": "zustaendig", "wert": zust, "version": 1})
    welt.post("leitung", FADEN, {"art": "frist", "wert": "2020-01-01", "version": 2})

    def karte(wer: str) -> dict[str, Any]:
        k: dict[str, Any] = next(p for p in welt.c[wer].get("/api/v1/projekte").json()[
            "projekte"] if p["id"] == "ost")
        return k

    assert karte("rueck")["befund_faeden"] is True and karte("rueck")["ueberfaellig_meine"] == 1
    assert karte("bear")["ueberfaellig_meine"] == 1        # über das Gewerk Dach
    assert karte("bh")["ueberfaellig_meine"] == 0
    assert karte("rueck")["einstellungen"]["befundFaeden"] is True


def test_schalter_aus_404(welt: Welt) -> None:  # noqa: F811 — Fixture
    welt.sql("UPDATE projekte SET befund_faeden=0")
    c = welt.c["leitung"]
    assert c.get(FADEN).status_code == 404
    assert c.get(BASIS + "/befunde").status_code == 404
    assert welt.post("leitung", FADEN, {"art": "kommentar", "text": "x"}).status_code == 404
    pfad = f"{FADEN}/{welt.oeffentlich_id}/zurueckziehen"
    assert welt.post("bear", pfad).status_code == 404
    assert welt.post("leitung", BASIS + "/pois", {"id": "P-2", "daten": {
        "title": "x", "position": [0, 0, 0]}}).status_code == 201
    assert c.patch(BASIS + "/pois/P-2", headers=csrf(),
                   json={"daten": {"title": "y"}, "version": 1}).status_code == 200
    assert welt.sql("SELECT COUNT(*) FROM befund_eintraege WHERE poi_id='P-2'")[0][0] == 0


def test_markierung_ueber_api(welt: Welt) -> None:  # noqa: F811 — Fixture
    r = welt.c["bear"].patch(BASIS + "/pois/P-1", headers=csrf(),
                             json={"daten": {"note": "neu"}, "version": 1})
    assert r.status_code == 200
    e = welt.c["betr"].get(FADEN).json()["eintraege"][-1]
    assert e["art"] == "markierung" and e["wert"] == {"vorher": {"note": ""},
                                                      "nachher": {"note": "neu"}}


def test_openapi_kennt_faden_wege(welt: Welt) -> None:  # noqa: F811 — Fixture
    pfade = welt.c["bear"].get("/api/v1/openapi.json").json()["paths"]
    for p in ("/api/v1/projekte/{pid}/pois/{eid}/faden",
              "/api/v1/projekte/{pid}/pois/{eid}/faden/{schluessel}/zurueckziehen",
              "/api/v1/projekte/{pid}/befunde"):
        assert p in pfade


def test_schalter_in_einstellungen_nur_leitung(welt: Welt) -> None:  # noqa: F811 — Fixture
    pfad = BASIS + "/einstellungen"
    assert welt.c["bh"].patch(pfad, headers=csrf(), json={"befundFaeden": False}).status_code == 403
    r = welt.c["leitung"].patch(pfad, headers=csrf(), json={"befundFaeden": False})
    assert r.status_code == 200 and r.json()["einstellungen"]["befundFaeden"] is False
    assert welt.c["bh"].get(FADEN).status_code == 404
    ev = welt.sql("SELECT vorher, nachher FROM ereignisse WHERE art='einstellung.geaendert'")
    assert '"befundFaeden":true' in ev[-1]["vorher"] and '"befundFaeden":false' in ev[-1]["nachher"]
    assert welt.c["leitung"].patch(pfad, headers=csrf(),
                                   json={"befundFaeden": "ja"}).status_code == 422
    r = welt.c["leitung"].patch(pfad, headers=csrf(), json={"befundFaeden": True})
    assert r.status_code == 200 and welt.c["bh"].get(FADEN).status_code == 200


def test_gewerk_loeschen_409_solange_offene_befunde(welt: Welt) -> None:  # noqa: F811 — Fixture
    dach = welt.gewerk["Dach"]
    welt.post("leitung", FADEN, {"art": "zustaendig", "version": 1,
                                 "wert": {"gewerk_id": dach, "nutzer_id": welt.id["rueck"]}})
    pfad = f"{BASIS}/gewerke/{dach}"
    r = welt.c["leitung"].delete(pfad, headers=csrf())
    assert r.status_code == 409 and "1 Befunde" in r.json()["fehler"]
    assert welt.stand()["zustaendig"]["gewerk_id"] == dach
    welt.post("bear", FADEN, {"art": "status", "wert": "erledigt", "version": 2})
    welt.post("leitung", FADEN, {"art": "status", "wert": "abgeschlossen", "version": 3})
    assert welt.c["leitung"].delete(pfad, headers=csrf()).status_code == 409  # auch geprüft
    welt.post("leitung", FADEN, {"art": "zustaendig", "wert": None, "version": 4})
    assert welt.c["leitung"].delete(pfad, headers=csrf()).status_code == 204
