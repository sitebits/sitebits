"""G7: Vorschläge an POIs und Messungen (inhalte-Ebene)."""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException

from scanview_server import db, inhalte
from scanview_server.web import Konflikt, Ungueltig

POI: dict[str, Any] = {"title": "Riss", "category": "Mangel", "note": "",
                       "position": [1.0, 2.0, 3.0], "station": None}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "ki.db")
    db.migrate(c)
    for nid in (1, 2):
        c.execute("INSERT INTO users (id, email, name, created_at) VALUES (?,?,?,0)",
                  (nid, f"n{nid}@x.de", f"N{nid}"))
    return c


def _arten(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT art FROM ereignisse ORDER BY id")]


def _neu(conn: sqlite3.Connection, *, ki: bool, nutzer: int = 1) -> inhalte.Eintrag:
    return inhalte.anlegen(conn, "pois", "ost", "P-1", inhalte.pruefen("pois", POI),
                           nutzer_id=nutzer, revision=None, kanal="api", ki=ki, now=100)


def test_agent_legt_vorschlag_an(conn: sqlite3.Connection) -> None:
    e = _neu(conn, ki=True)
    assert (e.herkunft, e.pruefstand) == ("ki", "vorschlag")
    assert e.als_json()["pruefstand"] == "vorschlag"
    assert _arten(conn) == ["poi.vorgeschlagen"]


def test_sichtbarkeit(conn: sqlite3.Connection) -> None:
    _neu(conn, ki=True, nutzer=1)
    ids = lambda s: [e.id for e in inhalte.liste(conn, "pois", "ost", sicht=s)]  # noqa: E731
    assert ids(inhalte.NUR_GUELTIG) == []
    assert ids(inhalte.Vorschlagssicht(alle=False, nutzer_id=2)) == []
    assert ids(inhalte.Vorschlagssicht(alle=False, nutzer_id=1)) == ["P-1"]
    assert ids(inhalte.Vorschlagssicht(alle=True, nutzer_id=2)) == ["P-1"]


def test_agent_aendert_gueltiges_nicht(conn: sqlite3.Connection) -> None:
    e = _neu(conn, ki=False)
    with pytest.raises(StarletteHTTPException) as fehler:
        inhalte.aendern(conn, "pois", "ost", "P-1", {"title": "neu"}, e.version + 5,
                        nutzer_id=1, ki=True)
    assert fehler.value.status_code == 403  # 403 vor 409 (falsche Version)


def test_agent_aendert_eigenen_vorschlag(conn: sqlite3.Connection) -> None:
    e = _neu(conn, ki=True)
    e2 = inhalte.aendern(conn, "pois", "ost", "P-1", {"title": "Riss 2"}, e.version,
                         nutzer_id=1, ki=True)
    assert (e2.pruefstand, e2.daten["title"]) == ("vorschlag", "Riss 2")
    with pytest.raises(StarletteHTTPException) as fehler:
        inhalte.aendern(conn, "pois", "ost", "P-1", {"title": "x"}, e2.version,
                        nutzer_id=2, ki=True)
    assert fehler.value.status_code == 403
    assert _arten(conn)[-1] == "poi.vorschlag_geaendert"


def test_mensch_aendert_vorschlag_bleibt_vorschlag(conn: sqlite3.Connection) -> None:
    e = _neu(conn, ki=True)
    e2 = inhalte.aendern(conn, "pois", "ost", "P-1", {"title": "geprüft"}, e.version,
                         nutzer_id=2)
    assert e2.pruefstand == "vorschlag"
    assert _arten(conn)[-1] == "poi.vorschlag_geaendert"


def test_annehmen(conn: sqlite3.Connection) -> None:
    e = _neu(conn, ki=True)
    with pytest.raises(Konflikt):
        inhalte.annehmen(conn, "pois", "ost", "P-1", e.version + 1, nutzer_id=2,
                         kanal="web", token_name=None)
    a = inhalte.annehmen(conn, "pois", "ost", "P-1", e.version, nutzer_id=2, kanal="web",
                         token_name=None)
    assert a is not None and (a.pruefstand, a.herkunft) == ("gueltig", "ki")
    ev = conn.execute("SELECT art, nutzer_id, nachher FROM ereignisse ORDER BY id DESC").fetchone()
    assert (ev[0], ev[1], json.loads(ev[2])["title"]) == ("poi.angenommen", 2, "Riss")
    with pytest.raises(Ungueltig):
        inhalte.annehmen(conn, "pois", "ost", "P-1", a.version, nutzer_id=2, kanal="web",
                         token_name=None)


def test_verwerfen_macht_unsichtbar(conn: sqlite3.Connection) -> None:
    e = _neu(conn, ki=True)
    assert inhalte.verwerfen(conn, "pois", "ost", "P-1", e.version, nutzer_id=2,
                             kanal="web", token_name=None) is None
    assert inhalte.liste(conn, "pois", "ost",
                         sicht=inhalte.Vorschlagssicht(alle=True, nutzer_id=1)) == []
    with pytest.raises(StarletteHTTPException) as fehler:
        inhalte.aendern(conn, "pois", "ost", "P-1", {"title": "x"}, e.version + 1, nutzer_id=2)
    assert fehler.value.status_code == 404


def test_loeschvorschlag(conn: sqlite3.Connection) -> None:
    e = _neu(conn, ki=False)
    inhalte.loeschen(conn, "pois", "ost", "P-1", e.version, nutzer_id=1, ki=True, now=200)
    alle = inhalte.Vorschlagssicht(alle=True, nutzer_id=2)
    [g] = inhalte.liste(conn, "pois", "ost", sicht=alle)
    assert (g.pruefstand, g.loeschen_vorgeschlagen) == ("gueltig", 200)
    v = inhalte.verwerfen(conn, "pois", "ost", "P-1", g.version, nutzer_id=2, kanal="web",
                          token_name=None)
    assert v is not None and v.loeschen_vorgeschlagen is None
    inhalte.loeschen(conn, "pois", "ost", "P-1", v.version, nutzer_id=1, ki=True, now=300)
    [g] = inhalte.liste(conn, "pois", "ost", sicht=alle)
    assert inhalte.annehmen(conn, "pois", "ost", "P-1", g.version, nutzer_id=2, kanal="web",
                            token_name=None) is None
    assert inhalte.liste(conn, "pois", "ost") == []
    assert _arten(conn) == ["poi.angelegt", "poi.loeschen_vorgeschlagen",
                            "poi.loeschen_verworfen", "poi.loeschen_vorgeschlagen",
                            "poi.geloescht"]


def test_agent_loescht_eigenen_vorschlag(conn: sqlite3.Connection) -> None:
    e = _neu(conn, ki=True)
    inhalte.loeschen(conn, "pois", "ost", "P-1", e.version, nutzer_id=1, ki=True)
    assert _arten(conn)[-1] == "poi.vorschlag_geloescht"
    assert inhalte.liste(conn, "pois", "ost",
                         sicht=inhalte.Vorschlagssicht(alle=True, nutzer_id=1)) == []


def test_anlegen_auf_verworfene_id(conn: sqlite3.Connection) -> None:
    e = _neu(conn, ki=True)
    inhalte.verwerfen(conn, "pois", "ost", "P-1", e.version, nutzer_id=2, kanal="web",
                      token_name=None)
    neu = inhalte.anlegen(conn, "pois", "ost", "P-1",
                          inhalte.pruefen("pois", {**POI, "title": "B"}),
                          nutzer_id=2, revision=None, kanal="web", now=300)
    assert (neu.pruefstand, neu.herkunft, neu.daten["title"]) == ("gueltig", "mensch", "B")
    # Wie neu angelegt: neuer Ersteller, kein verworfener Inhalt im Protokoll.
    assert (neu.erstellt_von, neu.erstellt_am) == (2, 300)
    ev = conn.execute("SELECT art, vorher FROM ereignisse ORDER BY id DESC").fetchone()
    assert (ev[0], ev[1]) == ("poi.angelegt", None)


def test_agent_loescht_fremden_vorschlag_nicht(conn: sqlite3.Connection) -> None:
    e = _neu(conn, ki=True, nutzer=1)
    with pytest.raises(StarletteHTTPException) as fehler:
        inhalte.loeschen(conn, "pois", "ost", "P-1", e.version, nutzer_id=2, ki=True)
    assert fehler.value.status_code == 403


def test_mensch_loescht_vorschlag(conn: sqlite3.Connection) -> None:
    e = _neu(conn, ki=True, nutzer=1)
    assert inhalte.loeschen(conn, "pois", "ost", "P-1", e.version, nutzer_id=2) is None
    assert _arten(conn)[-1] == "poi.vorschlag_geloescht"


def test_pruefen_ohne_vorschlag_422_erst_nach_version(conn: sqlite3.Connection) -> None:
    e = _neu(conn, ki=False)
    for f in (inhalte.annehmen, inhalte.verwerfen):
        with pytest.raises(Konflikt):
            f(conn, "pois", "ost", "P-1", e.version + 1, nutzer_id=2, kanal="web",
              token_name=None)
        with pytest.raises(Ungueltig):
            f(conn, "pois", "ost", "P-1", e.version, nutzer_id=2, kanal="web", token_name=None)


def test_rueckmelder_prueft_nur_eigene(conn: sqlite3.Connection) -> None:
    rm = {**POI, "category": inhalte.RUECKMELDUNG}
    e = inhalte.anlegen(conn, "pois", "ost", "P-1", inhalte.pruefen("pois", rm), nutzer_id=1,
                        revision=None, kanal="api", ki=True, eigen=inhalte.Eigentum(1))
    with pytest.raises(StarletteHTTPException) as fehler:
        inhalte.annehmen(conn, "pois", "ost", "P-1", e.version, nutzer_id=2, kanal="web",
                         token_name=None, eigen=inhalte.Eigentum(2))
    assert fehler.value.status_code == 403
    a = inhalte.annehmen(conn, "pois", "ost", "P-1", e.version, nutzer_id=1, kanal="web",
                         token_name=None, eigen=inhalte.Eigentum(1))
    assert a is not None and a.pruefstand == "gueltig"


def test_verworfene_zaehlen_nicht_zur_obergrenze(conn: sqlite3.Connection,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inhalte, "ANZAHL_MAX", 1)
    e = _neu(conn, ki=True)
    with pytest.raises(Ungueltig):
        inhalte.anlegen(conn, "pois", "ost", "P-2", inhalte.pruefen("pois", POI), nutzer_id=2,
                        revision=None)
    inhalte.verwerfen(conn, "pois", "ost", "P-1", e.version, nutzer_id=2, kanal="web",
                      token_name=None)
    assert inhalte.anlegen(conn, "pois", "ost", "P-2", inhalte.pruefen("pois", POI),
                           nutzer_id=2, revision=None).id == "P-2"


def test_messung_vorschlag(conn: sqlite3.Connection) -> None:
    strecke = inhalte.pruefen("messungen", {"kind": "distance", "points": [[0, 0, 0], [1, 0, 0]]})
    e = inhalte.anlegen(conn, "messungen", "ost", "M-1", strecke, nutzer_id=1, revision=None,
                        kanal="api", ki=True)
    assert (e.herkunft, e.pruefstand) == ("ki", "vorschlag")
    assert inhalte.liste(conn, "messungen", "ost") == []
    a = inhalte.annehmen(conn, "messungen", "ost", "M-1", e.version, nutzer_id=2, kanal="web",
                         token_name=None)
    assert a is not None and [x.id for x in inhalte.liste(conn, "messungen", "ost")] == ["M-1"]
    assert _arten(conn) == ["messung.vorgeschlagen", "messung.angenommen"]


def test_agent_vorschlag_auf_geloeschter_id_gehoert_dem_agenten(conn: sqlite3.Connection) -> None:
    e = _neu(conn, ki=False, nutzer=1)
    inhalte.loeschen(conn, "pois", "ost", "P-1", e.version, nutzer_id=1)
    v = inhalte.anlegen(conn, "pois", "ost", "P-1", inhalte.pruefen("pois", POI), nutzer_id=2,
                        revision=None, kanal="api", ki=True, now=500)
    assert (v.erstellt_von, v.erstellt_am, v.pruefstand) == (2, 500, "vorschlag")
    assert inhalte.liste(conn, "pois", "ost", sicht=inhalte.Vorschlagssicht(False, 1)) == []
    assert [x.id for x in inhalte.liste(conn, "pois", "ost",
                                        sicht=inhalte.Vorschlagssicht(False, 2))] == ["P-1"]
    v2 = inhalte.aendern(conn, "pois", "ost", "P-1", {"title": "neu"}, v.version, nutzer_id=2,
                         ki=True)
    assert v2.daten["title"] == "neu"


def test_loeschvorschlag_nur_in_pruefersicht(conn: sqlite3.Connection) -> None:
    e = _neu(conn, ki=False)
    inhalte.loeschen(conn, "pois", "ost", "P-1", e.version, nutzer_id=2, ki=True, now=200)
    [g] = inhalte.liste(conn, "pois", "ost")
    assert g.loeschen_vorgeschlagen is None and g.als_json()["loeschenVorgeschlagen"] is None
    [g] = inhalte.liste(conn, "pois", "ost", sicht=inhalte.Vorschlagssicht(False, 2))
    assert g.loeschen_vorgeschlagen is None
    [g] = inhalte.liste(conn, "pois", "ost", sicht=inhalte.Vorschlagssicht(True, 3))
    assert g.loeschen_vorgeschlagen == 200
