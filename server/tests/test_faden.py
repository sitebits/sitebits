"""Modell und Regeln des Befund-Fadens (faden.py) direkt an der Datenbank; synthetische Daten."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from starlette.exceptions import HTTPException

from scanview_server import db, export, faden, inhalte, projektverwaltung, protokoll
from scanview_server.web import Konflikt, Ungueltig

POI = {"title": "Befund", "category": "Mangel", "note": "", "position": [0, 0, 0],
       "station": None}


def _sicht(rolle: str, nutzer: int = 1, *, gewerk: int | None = None,
           bhs: bool = False, intern: bool | None = None) -> faden.Sicht:
    b = bhs or rolle in ("admin", "projektleitung")
    return faden.Sicht(nutzer=nutzer, rolle=rolle, gewerk=gewerk, bauherrenseite=b,
                       intern=b if intern is None else intern)


ADMIN = _sicht("admin")
BEARBEITER = _sicht("bearbeiter")


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "x.db")
    db.migrate(c)
    for uid in (1, 2, 3):
        c.execute("INSERT INTO users (id, email, name, created_at) VALUES (?,?,?,0)",
                  (uid, f"u{uid}@example.test", f"Person {uid}"))
    projektverwaltung.einrichten(c, "ost", "bauprojekt", von=1, now=0)
    inhalte.anlegen(c, "pois", "ost", "P-1", dict(POI), nutzer_id=1, revision=1)
    return c


def _gewerk(c: sqlite3.Connection, name: str, bhs: bool) -> int:
    return projektverwaltung.gewerk_anlegen(c, "ost", name=name, kurz=name[:2],
                                            bauherrenseite=bhs, von=1).id


def _status(c: sqlite3.Connection, s: faden.Sicht, ziel: str, text: str | None = None) -> Any:
    v = faden.stand(c, "ost", "P-1")["version"]
    return faden.anlegen(c, "ost", "P-1", s, art="status", wert=ziel, version=v, text=text)


def test_einrichten_bauprojekt_schaltet_faeden_ein(conn: sqlite3.Connection) -> None:
    assert faden.aktiviert(conn, "ost")
    projektverwaltung.einrichten(conn, "west", "bestandsaufnahme", von=1)
    assert not faden.aktiviert(conn, "west")
    assert projektverwaltung.einstellungen(conn, "ost").befund_faeden is True


def test_kommentar_ohne_version_status_mit_version(conn: sqlite3.Connection) -> None:
    e, s = faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="kommentar", text=" Hallo ",
                         now=10)
    assert s["version"] == 1 and e["text"] == "Hallo" and e["erstellt_von"]["name"] == "Person 1"
    _, s = faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="status", wert="erledigt",
                         version=1)
    assert s["status"] == "erledigt" and s["version"] == 2
    with pytest.raises(Konflikt) as k:
        faden.anlegen(conn, "ost", "P-1", ADMIN, art="status", wert="abgeschlossen", version=1)
    assert k.value.aktuell["version"] == 2
    arten = [x.art for x in protokoll.lesen(conn, project_id="ost")]
    assert arten[:3] == ["eintrag.angelegt", "poi.status", "eintrag.angelegt"]


def test_protokoll_ohne_namen_und_mit_intern(conn: sqlite3.Connection) -> None:
    faden.anlegen(conn, "ost", "P-1", ADMIN, art="kommentar", text="Nachtrag", intern=True)
    e = protokoll.lesen(conn, project_id="ost", arten=("eintrag",))[0]
    assert e.objekt_id == "P-1/" + e.nachher["schluessel"]
    assert e.nachher["intern"] is True and e.nachher["erstellt_von"] == 1
    assert "Person 1" not in json.dumps(e.nachher)


def test_zurueckziehen_kommentar_und_link(conn: sqlite3.Connection) -> None:
    k, _ = faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="kommentar", text="Geheim")
    lnk, _ = faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="link",
                           wert={"url": "https://example.test/a", "titel": "Plan"})
    with pytest.raises(HTTPException) as x:
        faden.zurueckziehen(conn, "ost", "P-1", k["schluessel"], _sicht("bearbeiter", 2))
    assert x.value.status_code == 403
    faden.zurueckziehen(conn, "ost", "P-1", k["schluessel"], BEARBEITER, now=30)
    z = faden.zurueckziehen(conn, "ost", "P-1", lnk["schluessel"], BEARBEITER, now=31)
    assert z["wert"] is None and z["zurueckgezogen_am"] == 31
    zweimal = faden.zurueckziehen(conn, "ost", "P-1", k["schluessel"], BEARBEITER, now=99)
    assert zweimal["zurueckgezogen_am"] == 30
    gelesen = faden.lesen(conn, "ost", "P-1", intern=False)
    assert [e["text"] for e in gelesen] == [None, None] and gelesen[1]["wert"] is None
    alt = [e for e in protokoll.lesen(conn, project_id="ost") if e.art == "eintrag.angelegt"]
    assert {a.nachher["text"] for a in alt} >= {"Geheim"}
    zurueck = [e for e in protokoll.lesen(conn, project_id="ost")
               if e.art == "eintrag.zurueckgezogen"]
    assert len(zurueck) == 2 and all(e.nachher["text"] is None for e in zurueck)
    assert all(e.nachher["intern"] is False for e in zurueck)


def test_status_nicht_zurueckziehbar(conn: sqlite3.Connection) -> None:
    e, _ = _status(conn, BEARBEITER, "in_arbeit")
    with pytest.raises(Ungueltig, match="Kommentar oder Link"):
        faden.zurueckziehen(conn, "ost", "P-1", e["schluessel"], BEARBEITER)


def test_intern_nur_bauherrenseite(conn: sqlite3.Connection) -> None:
    faden.anlegen(conn, "ost", "P-1", ADMIN, art="kommentar", text="Intern", intern=True)
    assert faden.lesen(conn, "ost", "P-1", intern=False) == []
    assert len(faden.lesen(conn, "ost", "P-1", intern=True)) == 1
    with pytest.raises(HTTPException) as x:
        faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="kommentar", text="x", intern=True)
    assert x.value.status_code == 403
    lese = _sicht("projektleitung", intern=False)
    with pytest.raises(HTTPException):
        faden.anlegen(conn, "ost", "P-1", lese, art="kommentar", text="x", intern=True)


def test_sicht_bauherrenseite_aus_gewerk(conn: sqlite3.Connection) -> None:
    bh = _gewerk(conn, "Bauherr", True)
    dach = _gewerk(conn, "Dach", False)
    conn.execute("INSERT INTO project_roles (user_id, project_id, role, gewerk_id)"
                 " VALUES (2, 'ost', 'bearbeiter', ?), (3, 'ost', 'rueckmelder', ?)", (bh, bh))
    s2 = faden.sicht(conn, "ost", 2, "bearbeiter")
    assert s2.bauherrenseite and s2.intern and s2.gewerk == bh
    assert not faden.sicht(conn, "ost", 2, "bearbeiter", lese_token=True).intern
    s3 = faden.sicht(conn, "ost", 3, "rueckmelder")
    assert not s3.bauherrenseite and not s3.intern
    conn.execute("UPDATE project_roles SET gewerk_id=? WHERE user_id=2", (dach,))
    assert not faden.sicht(conn, "ost", 2, "bearbeiter").bauherrenseite


@pytest.mark.parametrize(("von", "rolle", "bhs", "ziele"), [
    ("offen", "bearbeiter", False, ["in_arbeit", "erledigt"]),
    ("offen", "bearbeiter", True, ["in_arbeit", "erledigt", "abgelehnt"]),
    ("in_arbeit", "bearbeiter", False, ["erledigt", "offen"]),
    ("erledigt", "bearbeiter", False, []),
    ("erledigt", "projektleitung", False, ["abgeschlossen", "offen"]),
    ("abgeschlossen", "admin", False, ["offen"]),
    ("abgelehnt", "bearbeiter", True, ["offen"]),
    ("offen", "betrachter", False, []),
])
def test_statusautomat_und_rechte(von: str, rolle: str, bhs: bool, ziele: list[str]) -> None:
    st = {"status": von, "zustaendig": None, "frist": None, "version": 1}
    assert faden.rechte(_sicht(rolle, bhs=bhs), st)["status"] == ziele


def test_rueckmelder_nur_wenn_zustaendig() -> None:
    st: dict[str, Any] = {"status": "offen", "zustaendig": None, "frist": None, "version": 1}
    r = _sicht("rueckmelder", 2, gewerk=7)
    assert faden.rechte(r, st) == {"kommentieren": True, "status": [], "zustaendig": False,
                                   "frist": False, "intern": False}
    st["zustaendig"] = {"gewerk_id": 7, "nutzer_id": None}
    assert faden.rechte(r, st)["status"] == ["in_arbeit", "erledigt"]
    st["zustaendig"] = {"gewerk_id": 8, "nutzer_id": 2}
    assert faden.rechte(r, st)["status"] == ["in_arbeit", "erledigt"]
    st["status"] = "erledigt"
    assert faden.rechte(r, st)["status"] == []


@pytest.mark.parametrize("kwargs", [
    {"art": "link", "wert": {"url": "javascript:alert(1)", "titel": "X"}},
    {"art": "link", "wert": {"url": "http://example.test", "titel": "X"}},
    {"art": "link", "wert": {"url": "https://nutzer@example.test", "titel": "X"}},
    {"art": "link", "wert": {"url": "https://example.test", "titel": "X", "mehr": 1}},
    {"art": "status", "wert": "abgelehnt", "version": 1},
    {"art": "status", "wert": "abgeschlossen", "version": 1},
    {"art": "status", "wert": "quatsch", "version": 1},
    {"art": "status", "wert": "erledigt"},
    {"art": "frist", "wert": "2026-02-30", "version": 1},
    {"art": "frist", "wert": "30.10.2026", "version": 1},
    {"art": "frist", "wert": None, "version": 1},
    {"art": "zustaendig", "wert": {"gewerk_id": 999, "nutzer_id": None}, "version": 1},
    {"art": "zustaendig", "wert": {"gewerk_id": True, "nutzer_id": None}, "version": 1},
    {"art": "kommentar", "text": "x" * 4001},
    {"art": "kommentar", "text": "a\x00b"},
    {"art": "kommentar"},
    {"art": "kommentar", "text": "x", "wert": 1},
    {"art": "markierung", "wert": {}},
    {"art": "status", "wert": "erledigt", "version": 1, "intern": True},
])
def test_grenzen_422_ohne_wirkung(conn: sqlite3.Connection, kwargs: dict[str, Any]) -> None:
    with pytest.raises(Ungueltig):
        faden.anlegen(conn, "ost", "P-1", ADMIN, **kwargs)
    assert faden.lesen(conn, "ost", "P-1", intern=True) == []
    assert faden.stand(conn, "ost", "P-1")["version"] == 1


def test_befundlink_dieser_instanz_erlaubt(conn: sqlite3.Connection) -> None:
    e, s = faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="link",
                         wert={"url": "/?p=ost#b=P-1&rev=3", "titel": "Stelle"})
    assert e["wert"]["url"] == "/?p=ost#b=P-1&rev=3" and s["version"] == 1


BEFUNDLINK_ECHT = ("/ansicht?p=/data/ost/#cam=-4.000,0.000,-0.500&tgt=1.000,1.500,-0.500"
                   "&m=0.000,0.000,0.000;1.000,0.000,0.000|2.000,0.000,0.000;3.000,0.000,0.000"
                   "&g=p:0,0,0;1,1,1&st=S001&b=P-1&rev=3&e=0123456789abcdef")


@pytest.mark.parametrize("url", [BEFUNDLINK_ECHT, "/ansicht?p=/data/ost/#b=P-1",
                                 "/?p=ost#b=P-1&rev=3"])
def test_relativer_befundlink_erlaubt(conn: sqlite3.Connection, url: str) -> None:
    e, _ = faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="link",
                         wert={"url": url, "titel": "Stelle"})
    assert e["wert"]["url"] == url


@pytest.mark.parametrize("url", [
    "//evil.test/x", "/\\evil", "/\\evil.test/x", "javascript:alert(1)", "/%2F%2Fevil",
    "/%2f%2fevil/ansicht", "/admin", "/ansicht/x", "/ansichten?p=x", "/ansicht ?p=x",
    "/ansicht?p=x#a b", "/ansicht?p=x#a\tb",
    "/ansicht?p=x\\y", "ansicht?p=x", "/ansicht?p=" + "x" * faden.URL_MAX,
])
def test_relativer_link_nur_viewer_dieser_instanz(conn: sqlite3.Connection, url: str) -> None:
    with pytest.raises(Ungueltig):
        faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="link",
                      wert={"url": url, "titel": "X"})


def test_begruendung_und_wiederoeffnen(conn: sqlite3.Connection) -> None:
    _status(conn, BEARBEITER, "erledigt")
    _status(conn, ADMIN, "abgeschlossen")
    with pytest.raises(Ungueltig, match="Begründung"):
        _status(conn, ADMIN, "offen")
    e, s = _status(conn, ADMIN, "offen", text="Nicht dicht")
    assert s["status"] == "offen" and e["text"] == "Nicht dicht" and e["wert"] == "offen"


def test_zustaendig_und_frist_setzen(conn: sqlite3.Connection) -> None:
    g = _gewerk(conn, "Dach", False)
    conn.execute("INSERT INTO project_roles (user_id, project_id, role, gewerk_id)"
                 " VALUES (2, 'ost', 'rueckmelder', ?)", (g,))
    z = {"gewerk_id": g, "nutzer_id": 2}
    _, s = faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="zustaendig", wert=z, version=1)
    assert s["zustaendig"] == z and s["version"] == 2
    with pytest.raises(Ungueltig, match="Keine Änderung"):
        faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="zustaendig", wert=z, version=2)
    with pytest.raises(Ungueltig, match="Person"):
        faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="zustaendig",
                      wert={"gewerk_id": g, "nutzer_id": 3}, version=2)
    _, s = faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="frist", wert="2026-10-08",
                         version=2)
    assert s["frist"] == "2026-10-08" and s["version"] == 3
    ev = next(e for e in protokoll.lesen(conn, project_id="ost") if e.art == "poi.frist")
    assert (ev.vorher, ev.nachher) == (None, "2026-10-08")
    _, s = faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="frist", wert=None, version=3)
    assert s["frist"] is None


def test_obergrenze_eintraege(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(faden, "EINTRAEGE_MAX", 2)
    faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="kommentar", text="1")
    faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="kommentar", text="2")
    with pytest.raises(Ungueltig):
        faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="kommentar", text="3")
    assert len(faden.lesen(conn, "ost", "P-1", intern=True)) == 2


def test_ueberfaellig_berliner_kalendertag() -> None:
    st = {"status": "offen", "frist": "2026-10-08"}
    assert not faden.ueberfaellig(st, faden.heute(datetime(2026, 10, 8, 21, 59, tzinfo=UTC)))
    assert faden.ueberfaellig(st, faden.heute(datetime(2026, 10, 8, 22, 0, tzinfo=UTC)))
    assert not faden.ueberfaellig({**st, "status": "erledigt"}, "2026-10-09")


def test_markierung_aendern_erzeugt_eintrag(conn: sqlite3.Connection) -> None:
    inhalte.aendern(conn, "pois", "ost", "P-1", {"title": "Neu"}, 1, nutzer_id=1)
    e = faden.lesen(conn, "ost", "P-1", intern=True)
    assert len(e) == 1 and e[0]["art"] == "markierung"
    assert e[0]["wert"] == {"vorher": {"title": "Befund"}, "nachher": {"title": "Neu"}}
    assert faden.stand(conn, "ost", "P-1")["version"] == 2


def test_markierung_ohne_aenderung_oder_schalter(conn: sqlite3.Connection) -> None:
    inhalte.aendern(conn, "pois", "ost", "P-1", {"title": "Befund"}, 1, nutzer_id=1)
    conn.execute("UPDATE projekte SET befund_faeden=0")
    inhalte.aendern(conn, "pois", "ost", "P-1", {"note": "Neu"}, 2, nutzer_id=1)
    assert conn.execute("SELECT COUNT(*) FROM befund_eintraege").fetchone()[0] == 0


def test_geloeschter_poi_blendet_faden_aus(conn: sqlite3.Connection) -> None:
    faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="kommentar", text="x")
    inhalte.loeschen(conn, "pois", "ost", "P-1", 1, nutzer_id=1)
    with pytest.raises(HTTPException) as x:
        faden.lesen(conn, "ost", "P-1", intern=True)
    assert x.value.status_code == 404
    assert faden.alle_faeden(conn, "ost", intern=True) == {}
    inhalte.anlegen(conn, "pois", "ost", "P-1", dict(POI), nutzer_id=1, revision=1)
    assert len(faden.lesen(conn, "ost", "P-1", intern=True)) == 1


def test_gewerk_loeschen_gesperrt_solange_offen(conn: sqlite3.Connection) -> None:
    g = _gewerk(conn, "Dach", False)
    faden.anlegen(conn, "ost", "P-1", ADMIN, art="zustaendig",
                  wert={"gewerk_id": g, "nutzer_id": None}, version=1)
    with pytest.raises(projektverwaltung.GewerkBelegt) as x:
        projektverwaltung.gewerk_loeschen(conn, "ost", g, von=1)
    assert x.value.anzahl == 1 and "1 Befunde" in str(x.value)
    _status(conn, ADMIN, "abgelehnt", text="kein Mangel")
    with pytest.raises(projektverwaltung.GewerkBelegt):    # auch abgeschlossen/abgelehnt
        projektverwaltung.gewerk_loeschen(conn, "ost", g, von=1)
    faden.anlegen(conn, "ost", "P-1", ADMIN, art="zustaendig", wert=None, version=3)
    projektverwaltung.gewerk_loeschen(conn, "ost", g, von=1)
    assert faden.stand(conn, "ost", "P-1")["zustaendig"] is None


def test_gewerk_an_geloeschtem_befund_sperrt_nicht(conn: sqlite3.Connection) -> None:
    g = _gewerk(conn, "Dach", False)
    faden.anlegen(conn, "ost", "P-1", ADMIN, art="zustaendig",
                  wert={"gewerk_id": g, "nutzer_id": None}, version=1)
    inhalte.loeschen(conn, "pois", "ost", "P-1", 2, nutzer_id=1)
    projektverwaltung.gewerk_loeschen(conn, "ost", g, von=1)
    assert conn.execute("SELECT zustaendig_gewerk_id FROM pois").fetchone()[0] is None


def _befunde(conn: sqlite3.Connection, s: faden.Sicht, tag: str = "2026-10-08",
             **f: Any) -> dict[str, Any]:
    return faden.befunde(conn, "ost", s, faden.Filter(**f), tag,
                         inhalte.liste(conn, "pois", "ost"))


def test_befundliste_sortierung_filter_zaehler(conn: sqlite3.Connection) -> None:
    g = _gewerk(conn, "Dach", False)
    for pid, frist in (("P-2", "2026-10-11"), ("P-3", "2026-10-07"), ("P-4", None)):
        inhalte.anlegen(conn, "pois", "ost", pid, {**POI, "title": pid}, nutzer_id=1,
                        revision=1)
        if frist:
            faden.anlegen(conn, "ost", pid, ADMIN, art="frist", wert=frist, version=1)
    faden.anlegen(conn, "ost", "P-3", ADMIN, art="zustaendig",
                  wert={"gewerk_id": g, "nutzer_id": None}, version=2)
    ich = _sicht("rueckmelder", 2, gewerk=g)
    alle = _befunde(conn, ich)
    assert [b["poi"]["id"] for b in alle["befunde"]] == ["P-3", "P-2", "P-1", "P-4"]
    assert [b["ueberfaellig"] for b in alle["befunde"]] == [True, False, False, False]
    assert alle["zaehler"] == {"ueberfaellig_meine": 1}
    assert [b["poi"]["id"] for b in _befunde(conn, ich, frist="woche")["befunde"]] == ["P-2"]
    assert [b["poi"]["id"] for b in _befunde(conn, ich, frist="ohne")["befunde"]] == [
        "P-1", "P-4"]
    assert [b["poi"]["id"] for b in _befunde(conn, ich, zustaendig="gewerk")["befunde"]] == [
        "P-3"]
    assert _befunde(conn, ich, zustaendig="ich")["befunde"] == []
    assert _befunde(conn, ich, kategorie="Frage")["befunde"] == []
    assert _befunde(conn, ich, status="erledigt")["befunde"] == []
    assert _befunde(conn, ADMIN)["zaehler"] == {"ueberfaellig_meine": 0}


def test_letzter_eintrag_ohne_interne(conn: sqlite3.Connection) -> None:
    faden.anlegen(conn, "ost", "P-1", ADMIN, art="kommentar", text="a", now=100)
    faden.anlegen(conn, "ost", "P-1", ADMIN, art="kommentar", text="b", intern=True, now=200)
    assert _befunde(conn, BEARBEITER)["befunde"][0]["letzter_eintrag_am"] == 100
    assert _befunde(conn, ADMIN)["befunde"][0]["letzter_eintrag_am"] == 200


def test_dazwischen_geloeschter_poi_ohne_fehler(conn: sqlite3.Connection) -> None:
    inhalte.anlegen(conn, "pois", "ost", "P-2", dict(POI), nutzer_id=1, revision=1)
    alt = inhalte.liste(conn, "pois", "ost")
    inhalte.loeschen(conn, "pois", "ost", "P-2", 1, nutzer_id=1)
    b = faden.befunde(conn, "ost", ADMIN, faden.Filter(), "2026-10-08", alt)
    assert [x["poi"]["id"] for x in b["befunde"]] == ["P-1"]
    j = export.json_export("ost", 1, "T", "", alt, [], 0, faden.staende(conn, "ost"), {})
    assert [p["id"] for p in j["pois"]] == ["P-1"]


def test_kommentar_mehrzeilig_aber_ohne_andere_steuerzeichen() -> None:
    assert faden._text("Zeile 1\r\nZeile 2\n") == "Zeile 1\nZeile 2"
    for falsch in ("a\tb", "a\x00b", "a\x7fb"):
        with pytest.raises(Ungueltig, match="Steuerzeichen"):
            faden._text(falsch)
    with pytest.raises(Ungueltig, match="fehlt"):
        faden._text(" \n ")
    with pytest.raises(Ungueltig, match="länger"):
        faden._text("x" * (faden.TEXT_MAX + 1))


def test_faden_stand_meldet_ueberfaellig_wie_die_liste(conn: sqlite3.Connection) -> None:
    jetzt = int(datetime(2026, 10, 8, 9, tzinfo=faden.BERLIN).timestamp())
    _, st = faden.anlegen(conn, "ost", "P-1", BEARBEITER, art="frist", wert="2026-10-07",
                          version=1, now=jetzt)
    assert st["ueberfaellig"] is True
    def am(tag: str) -> Any:
        return faden.faden(conn, "ost", "P-1", BEARBEITER, tag)["stand"]["ueberfaellig"]

    assert am("2026-10-08") is True
    assert am("2026-10-07") is False
    # Dieselbe Regel wie GET …/befunde: erledigt gemeldet ist nicht mehr überfällig.
    _, st = _status(conn, BEARBEITER, "erledigt")
    assert am("2026-10-08") is False
    assert "ueberfaellig" in st
