"""Die Antwortmodelle (`api_modelle`, nur Beschreibung in der OpenAPI) stimmen mit echten
Antworten überein. Eigene Felder streng: ein neues oder umbenanntes Feld lässt diesen Test
scheitern, bis Modell und OpenAPI-Datei nachgezogen sind."""
from __future__ import annotations

from typing import Any

import httpx
from conftest import csrf
from fadenwelt import BASIS as FADEN_BASIS
from fadenwelt import Welt as FadenWelt
from fadenwelt import welt as faden_welt
from jobswelt import BASIS, PRUEFEN, Welt, welt  # noqa: F401
from pydantic import BaseModel

from scanview_server import api_modelle as m

PROJEKT = "/api/v1/projekte/ost"
POI = {"title": "Riss", "category": "Mangel", "note": "", "position": [1.0, 2.0, 3.0],
       "station": None, "positionSource": {"source": "pointcloud", "layerId": "punkte"}}
STRECKE: dict[str, Any] = {"kind": "distance", "points": [[0, 0, 0], [3, 4, 0]]}

# Fixture unter zweitem Namen (beide Welten heißen `welt`).
faden = faden_welt


def _passt(modell: type[BaseModel], r: httpx.Response, status: int = 200) -> dict[str, Any]:
    assert r.status_code == status, r.text
    daten: dict[str, Any] = r.json()
    modell.model_validate(daten, strict=False)
    return daten


def test_sitzung_version_projekte(welt: Welt) -> None:  # noqa: F811
    for c in (welt.c["leitung"], welt.token("leitung")):
        _passt(m.Sitzung, c.get("/api/v1/sitzung"))
        _passt(m.Version, c.get("/api/v1/version"))
        liste = _passt(m.Projektliste, c.get("/api/v1/projekte"))
        assert [p["id"] for p in liste["projekte"]] == ["ost"]
        _passt(m.Projekt, c.get(PROJEKT))


def test_projekt_aendern_und_konflikt(welt: Welt) -> None:  # noqa: F811
    c = welt.c["leitung"]
    _passt(m.ProjektBasis, c.patch(PROJEKT, json={"titel": "Neu", "version": 0}, headers=csrf()))
    f = _passt(m.Fehler, c.patch(PROJEKT, json={"titel": "X", "version": 0}, headers=csrf()), 409)
    m.ProjektBasis.model_validate(f["aktuell"])


def test_fehlerform(welt: Welt) -> None:  # noqa: F811
    t = welt.token("leitung")
    _passt(m.Fehler, t.get("/api/v1/projekte/gibtesnicht"), 404)
    _passt(m.Fehler, welt.c["anonym"].get("/api/v1/projekte"), 401)
    _passt(m.Fehler, t.post(PROJEKT + "/pois", json={"daten": {"title": 1}}), 422)
    _passt(m.Fehler, welt.token("betr", "lesen").get(BASIS), 403)


def test_pois_und_messungen(welt: Welt) -> None:  # noqa: F811
    t = welt.token("leitung")
    p = _passt(m.Poi, t.post(PROJEKT + "/pois", json={"id": "P-1", "daten": POI}), 201)
    _passt(m.Poi, t.patch(PROJEKT + "/pois/P-1",
                          json={"daten": {**POI, "note": "n"}, "version": p["version"]}))
    assert _passt(m.PoiListe, t.get(PROJEKT + "/pois"))["pois"][0]["id"] == "P-1"
    _passt(m.Messung, t.post(PROJEKT + "/messungen", json={"daten": STRECKE}), 201)
    _passt(m.MessungListe, t.get(PROJEKT + "/messungen?vorschlaege=1"))
    # KI-Vorschlag: anlegen, annehmen (Mensch), Löschvorschlag (202) per Agent-Token.
    agent = welt.token("leitung", agent=True)
    v = _passt(m.Poi, agent.post(PROJEKT + "/pois", json={"id": "P-2", "daten": POI}), 201)
    assert v["pruefstand"] == "vorschlag"
    a = _passt(m.Poi, t.post(PROJEKT + "/pois/P-2/annehmen", json={"version": v["version"]}))
    r = agent.delete(PROJEKT + f"/pois/P-2?version={a['version']}")
    assert _passt(m.Poi, r, 202)["loeschenVorgeschlagen"] is not None


def test_poi_mit_befund_stand(faden: FadenWelt) -> None:
    pois = _passt(m.PoiListe, faden.c["leitung"].get(FADEN_BASIS + "/pois"))["pois"]
    assert pois and all(p["stand"] is not None for p in pois)


def test_auswertungen(welt: Welt) -> None:  # noqa: F811
    t = welt.token("leitung")
    a = _passt(m.AuswertungMitWorker, t.post(BASIS, json=PRUEFEN), 202)
    _passt(m.Auswertungsliste, t.get(BASIS))
    welt.rechnen()
    fertig = _passt(m.AuswertungMitWorker, t.get(f"{BASIS}/{a['id']}"))
    assert fertig["status"] == "fertig" and fertig["ergebnis"]
    _passt(m.Ergebnis, t.get(fertig["ergebnis"]))
    frei = welt.c["leitung"].post(f"{BASIS}/{a['id']}/freigabe", json={"freigegeben": True},
                                  headers=csrf())
    _passt(m.Auswertung, frei)


def test_strenge_greift() -> None:
    """Gegenprobe: ein unbekanntes Feld scheitert an den eigenen Modellen."""
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        m.Version.model_validate({"version": "1", "neu": True})
