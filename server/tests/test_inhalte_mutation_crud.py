"""Mutationstests für inhalte.py: CRUD-Operationen und Lebenszyklus.

Deckt anlegen, aendern, loeschen, liste, _select, Eintrag, Konflikt,
Protokollierung, Versionierung und optimistische Sperre ab.
"""

from __future__ import annotations

import dataclasses
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException

from scanview_server import db, inhalte, protokoll
from scanview_server.web import Konflikt, Ungueltig

GUT_POI: dict[str, Any] = {
    "title": "Hauptportal",
    "category": "Eingang",
    "note": "Notiz",
    "position": [1.0, 2.0, 3.0],
    "station": None,
}

GUT_MESSUNG: dict[str, Any] = {
    "kind": "distance",
    "points": [[0.0, 0.0, 0.0], [1.0, 1.0, 0.0]],
}

GUT_FLAECHE: dict[str, Any] = {
    "kind": "area",
    "points": [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [2.0, 2.0, 0.0]],
    "areaM2": 2.0,
    "planarityM": 0.01,
}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    # Nutzer für ForeignKey und Join anlegen
    c.execute(
        "INSERT INTO users (id, email, name, is_admin, created_at) "
        "VALUES (1, 'u1@test.de', 'Nutzer Eins', 0, 1000)"
    )
    c.execute(
        "INSERT INTO users (id, email, name, is_admin, created_at) "
        "VALUES (2, 'u2@test.de', 'Nutzer Zwei', 0, 1000)"
    )
    return c


# --- Eintrag Dataclass und als_json ---


def test_eintrag_dataclass_und_als_json() -> None:
    daten = {"title": "Test", "position": [1.0, 2.0, 3.0]}
    e = inhalte.Eintrag(
        id="P-123",
        daten=daten,
        version=2,
        paket_revision=5,
        erstellt_von=1,
        erstellt_am=1000,
        geaendert_von=2,
        geaendert_von_name="Nutzer Zwei",
        geaendert_am=2000,
        erstellt_von_name="Nutzer Eins",
    )
    assert e.id == "P-123"
    assert e.daten == daten
    assert e.version == 2
    assert e.paket_revision == 5
    assert e.erstellt_von == 1
    assert e.erstellt_am == 1000
    assert e.geaendert_von == 2
    assert e.geaendert_von_name == "Nutzer Zwei"
    assert e.geaendert_am == 2000
    assert e.erstellt_von_name == "Nutzer Eins"

    # Dataclass ist frozen
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.version = 3  # type: ignore[misc]

    j = e.als_json()
    assert j == {
        "id": "P-123",
        "version": 2,
        "daten": daten,
        "paketRevision": 5,
        "erstelltVon": 1,
        "erstelltAm": 1000,
        "geaendertVon": 2,
        "geaendertVonName": "Nutzer Zwei",
        "geaendertAm": 2000,
        "erstelltVonName": "Nutzer Eins",
        "herkunft": "mensch",
        "pruefstand": "gueltig",
        "loeschenVorgeschlagen": None,
    }

    # Default für erstellt_von_name ist None
    e_def = inhalte.Eintrag(
        id="P-def",
        daten=daten,
        version=1,
        paket_revision=None,
        erstellt_von=None,
        erstellt_am=100,
        geaendert_von=None,
        geaendert_von_name=None,
        geaendert_am=100,
    )
    assert e_def.erstellt_von_name is None
    assert e_def.als_json()["erstelltVonName"] is None


# --- _select und ungültige Tabellen ---


def test_select_ungueltige_tabelle() -> None:
    with pytest.raises(ValueError) as exc_info:
        inhalte._select("unbekannt")  # type: ignore[arg-type]
    assert "unbekannt" in str(exc_info.value)


# --- liste(...) Abfragen, Sortierung, Filterung ---


def test_liste_leer_und_filter_projekt_und_loeschung(conn: sqlite3.Connection) -> None:
    assert inhalte.liste(conn, "pois", "projekt_a") == []
    assert inhalte.liste(conn, "messungen", "projekt_a") == []

    # Zwei POIs in projekt_a, einer in projekt_b
    inhalte.anlegen(conn, "pois", "projekt_a", "P-1", GUT_POI, nutzer_id=1, revision=1, now=200)
    inhalte.anlegen(conn, "pois", "projekt_a", "P-2", GUT_POI, nutzer_id=1, revision=1, now=100)
    inhalte.anlegen(conn, "pois", "projekt_b", "P-3", GUT_POI, nutzer_id=1, revision=1, now=50)

    # Sortierung nach erstellt_am, dann id
    l_a = inhalte.liste(conn, "pois", "projekt_a")
    assert len(l_a) == 2
    assert [x.id for x in l_a] == ["P-2", "P-1"]  # P-2 war now=100, P-1 war now=200

    # Messungen sind getrennt von POIs
    assert inhalte.liste(conn, "messungen", "projekt_a") == []

    # Nutzer-Name über LEFT JOIN
    assert l_a[0].erstellt_von_name == "Nutzer Eins"
    assert l_a[0].geaendert_von_name == "Nutzer Eins"

    # Soft-Löschen: taucht nicht mehr in liste() auf
    inhalte.loeschen(conn, "pois", "projekt_a", "P-2", version=1, nutzer_id=2, now=300)
    l_a_nach_del = inhalte.liste(conn, "pois", "projekt_a")
    assert len(l_a_nach_del) == 1
    assert l_a_nach_del[0].id == "P-1"


def test_liste_sortierung_bei_gleichem_zeitstempel(conn: sqlite3.Connection) -> None:
    # Gleicher Zeitstempel: Sortierung nach id aufsteigend
    inhalte.anlegen(conn, "pois", "p", "P-B", GUT_POI, nutzer_id=None, revision=None, now=100)
    inhalte.anlegen(conn, "pois", "p", "P-A", GUT_POI, nutzer_id=None, revision=None, now=100)
    inhalte.anlegen(conn, "pois", "p", "P-C", GUT_POI, nutzer_id=None, revision=None, now=100)

    eintraege = inhalte.liste(conn, "pois", "p")
    assert [x.id for x in eintraege] == ["P-A", "P-B", "P-C"]


# --- anlegen(...) ID-Generierung und Validierung ---


def test_anlegen_automatische_id_generierung(conn: sqlite3.Connection) -> None:
    poi = inhalte.anlegen(conn, "pois", "p", None, GUT_POI, nutzer_id=1, revision=None)
    assert poi.id.startswith("P-")
    assert len(poi.id) == 14  # P- + 12 Hex-Zeichen
    assert re.fullmatch(r"P-[0-9a-f]{12}", poi.id)

    mess = inhalte.anlegen(conn, "messungen", "p", None, GUT_MESSUNG, nutzer_id=1, revision=None)
    assert mess.id.startswith("M-")
    assert len(mess.id) == 14
    assert re.fullmatch(r"M-[0-9a-f]{12}", mess.id)


def test_anlegen_id_regex_grenzen(conn: sqlite3.Connection) -> None:
    # 1 Zeichen erlaubt
    e1 = inhalte.anlegen(conn, "pois", "p", "X", GUT_POI, nutzer_id=None, revision=None)
    assert e1.id == "X"

    # 64 Zeichen erlaubt
    id64 = "a" * 64
    e64 = inhalte.anlegen(conn, "pois", "p", id64, GUT_POI, nutzer_id=None, revision=None)
    assert e64.id == id64

    # 65 Zeichen nicht erlaubt
    with pytest.raises(Ungueltig, match=r"^Ungültige ID") as exc_info:
        inhalte.anlegen(conn, "pois", "p", "a" * 65, GUT_POI, nutzer_id=None, revision=None)
    assert (
        str(exc_info.value)
        == "Ungültige ID (erlaubt: Buchstaben, Ziffern, _ und -, bis 64 Zeichen)."
    )

    # Ungültige Zeichen
    for bad_id in ["", "p.1", "p/1", "p 1", "p:1", "p#1"]:
        with pytest.raises(Ungueltig, match=r"^Ungültige ID"):
            inhalte.anlegen(conn, "pois", "p", bad_id, GUT_POI, nutzer_id=None, revision=None)


# --- anlegen(...) Konflikt bei aktiver ID ---


def test_anlegen_id_bereits_vergeben_gibt_konflikt(conn: sqlite3.Connection) -> None:
    inhalte.anlegen(conn, "pois", "p", "P-1", GUT_POI, nutzer_id=1, revision=1, now=100)
    with pytest.raises(Konflikt) as exc_info:
        inhalte.anlegen(conn, "pois", "p", "P-1", GUT_POI, nutzer_id=2, revision=2, now=200)
    assert exc_info.value.text == inhalte.ID_VERGEBEN
    assert exc_info.value.aktuell["id"] == "P-1"
    assert exc_info.value.aktuell["version"] == 1


# --- anlegen(...) Wiederherstellen gelöschter Einträge ---


def test_anlegen_wiederherstellen_geloeschter_eintrag(conn: sqlite3.Connection) -> None:
    alt = inhalte.anlegen(conn, "pois", "p", "P-1", GUT_POI, nutzer_id=1, revision=1, now=100)
    assert alt.version == 1
    assert alt.erstellt_am == 100
    assert alt.erstellt_von == 1

    inhalte.loeschen(conn, "pois", "p", "P-1", version=1, nutzer_id=1, now=200)

    # Wiederherstellen mit neuerem Stand
    neu_daten = {**GUT_POI, "title": "Neuer Titel"}
    wieder = inhalte.anlegen(
        conn,
        "pois",
        "p",
        "P-1",
        neu_daten,
        nutzer_id=2,
        revision=2,
        now=300,
        kanal="cli",
        token_name="tok",
    )
    assert wieder.id == "P-1"
    assert wieder.version == 3  # anlegen=1, loeschen=2, wiederherstellen=3
    assert wieder.erstellt_am == 100  # Erstellt-Daten bleiben erhalten
    assert wieder.erstellt_von == 1
    assert wieder.geaendert_am == 300
    assert wieder.geaendert_von == 2
    assert wieder.paket_revision == 2
    assert wieder.daten["title"] == "Neuer Titel"

    # Protokoll-Prüfung
    ereignisse = protokoll.lesen(conn, project_id="p")
    assert ereignisse[0].art == "poi.wiederhergestellt"
    assert ereignisse[0].objekt_id == "P-1"
    assert ereignisse[0].vorher["title"] == "Hauptportal"
    assert ereignisse[0].nachher["title"] == "Neuer Titel"
    assert ereignisse[0].kanal == "cli"
    assert ereignisse[0].token == "tok"
    assert ereignisse[0].zeit == 300


# --- anlegen(...) Obergrenze ANZAHL_MAX ---


def test_anlegen_hoechstgrenze_anzahl_max(conn: sqlite3.Connection) -> None:
    # Setze POI-Tabelle direkt auf 5000 aktive Zeilen
    for i in range(5000):
        conn.execute(
            "INSERT INTO pois (project_id, id, daten, erstellt_am, geaendert_am, version) "
            "VALUES ('p', ?, '{}', 100, 100, 1)",
            (f"P-{i}",),
        )
    with pytest.raises(Ungueltig, match=r"^Höchstens 5000 POIs je Projekt\.$") as exc_info:
        inhalte.anlegen(conn, "pois", "p", "P-NEU", GUT_POI, nutzer_id=None, revision=None)
    assert str(exc_info.value) == "Höchstens 5000 POIs je Projekt."

    # Messungen haben dieselbe Obergrenze
    for i in range(5000):
        conn.execute(
            "INSERT INTO messungen (project_id, id, daten, erstellt_am, geaendert_am, version) "
            "VALUES ('p', ?, '{}', 100, 100, 1)",
            (f"M-{i}",),
        )
    with pytest.raises(Ungueltig, match=r"^Höchstens 5000 Messungen je Projekt\.$") as exc_info:
        inhalte.anlegen(conn, "messungen", "p", "M-NEU", GUT_MESSUNG, nutzer_id=None, revision=None)
    assert str(exc_info.value) == "Höchstens 5000 Messungen je Projekt."


# --- aendern(...) Erfolgs- und Fehlerfälle ---


def test_aendern_erfolg_und_protokoll(conn: sqlite3.Connection) -> None:
    inhalte.anlegen(conn, "pois", "p", "P-1", GUT_POI, nutzer_id=1, revision=1, now=100)
    geandert = inhalte.aendern(
        conn,
        "pois",
        "p",
        "P-1",
        {"title": "Aktualisiert"},
        version=1,
        nutzer_id=2,
        kanal="api",
        token_name="t1",
        now=200,
    )
    assert geandert.version == 2
    assert geandert.daten["title"] == "Aktualisiert"
    assert geandert.erstellt_am == 100
    assert geandert.erstellt_von == 1
    assert geandert.geaendert_am == 200
    assert geandert.geaendert_von == 2

    # Protokoll
    e = protokoll.lesen(conn, project_id="p")[0]
    assert e.art == "poi.geaendert"
    assert e.objekt_id == "P-1"
    assert e.vorher["title"] == "Hauptportal"
    assert e.nachher["title"] == "Aktualisiert"
    assert e.kanal == "api"
    assert e.token == "t1"
    assert e.zeit == 200


def test_aendern_leer_oder_kein_dict(conn: sqlite3.Connection) -> None:
    inhalte.anlegen(conn, "pois", "p", "P-1", GUT_POI, nutzer_id=1, revision=1)
    for bad_patch in [{}, None, "string", 123, []]:
        with pytest.raises(Ungueltig, match=r"^Keine Änderung angegeben\.$") as exc_info:
            inhalte.aendern(conn, "pois", "p", "P-1", bad_patch, version=1, nutzer_id=1)  # type: ignore[arg-type]
        assert str(exc_info.value) == "Keine Änderung angegeben."


def test_aendern_nicht_gefunden_404(conn: sqlite3.Connection) -> None:
    # Nicht vorhanden
    with pytest.raises(StarletteHTTPException) as exc_info:
        inhalte.aendern(conn, "pois", "p", "P-FEHLT", {"title": "X"}, version=1, nutzer_id=1)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == inhalte.NICHT_GEFUNDEN

    # Soft-gelöscht
    inhalte.anlegen(conn, "pois", "p", "P-1", GUT_POI, nutzer_id=1, revision=1)
    inhalte.loeschen(conn, "pois", "p", "P-1", version=1, nutzer_id=1)
    with pytest.raises(StarletteHTTPException) as exc_info:
        inhalte.aendern(conn, "pois", "p", "P-1", {"title": "X"}, version=2, nutzer_id=1)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == inhalte.NICHT_GEFUNDEN


def test_aendern_versionskonflikt_409(conn: sqlite3.Connection) -> None:
    inhalte.anlegen(conn, "pois", "p", "P-1", GUT_POI, nutzer_id=1, revision=1)
    with pytest.raises(Konflikt) as exc_info:
        inhalte.aendern(conn, "pois", "p", "P-1", {"title": "X"}, version=99, nutzer_id=1)
    assert exc_info.value.aktuell["id"] == "P-1"
    assert exc_info.value.aktuell["version"] == 1


# --- aendern(...) Messungen: Messart und Flächenwerte Zusammenführung ---


def test_aendern_messung_art_nicht_aenderbar(conn: sqlite3.Connection) -> None:
    inhalte.anlegen(conn, "messungen", "p", "M-1", GUT_MESSUNG, nutzer_id=1, revision=1)

    # Gleiche Messart mitschicken ist erlaubt
    inhalte.aendern(conn, "messungen", "p", "M-1", {"kind": "distance"}, version=1, nutzer_id=1)

    # Andere Messart wird abgelehnt
    with pytest.raises(Ungueltig, match=r"^Die Messart lässt sich nicht ändern\.$") as exc_info:
        inhalte.aendern(conn, "messungen", "p", "M-1", {"kind": "polyline"}, version=2, nutzer_id=1)
    assert str(exc_info.value) == "Die Messart lässt sich nicht ändern."


def test_aendern_flaeche_punkte_verwerfen_alte_werte(conn: sqlite3.Connection) -> None:
    inhalte.anlegen(conn, "messungen", "p", "M-F", GUT_FLAECHE, nutzer_id=1, revision=1)

    # Neue Eckpunkte ohne Flächenwerte: beide alten Werte werden verworfen
    m2 = inhalte.aendern(
        conn,
        "messungen",
        "p",
        "M-F",
        {"points": [[0, 0, 0], [3, 0, 0], [3, 3, 0]]},
        version=1,
        nutzer_id=1,
    )
    assert "areaM2" not in m2.daten and "planarityM" not in m2.daten

    # Wieder Flächenwerte setzen
    inhalte.aendern(
        conn,
        "messungen",
        "p",
        "M-F",
        {"areaM2": 4.5, "planarityM": 0.03},
        version=2,
        nutzer_id=1,
    )

    # Neue Eckpunkte nur mit areaM2: veraltetes planarityM wird verworfen
    m4 = inhalte.aendern(
        conn,
        "messungen",
        "p",
        "M-F",
        {"points": [[0, 0, 0], [4, 0, 0], [4, 4, 0]], "areaM2": 8.0},
        version=3,
        nutzer_id=1,
    )
    assert m4.daten["areaM2"] == 8.0
    assert "planarityM" not in m4.daten

    # Nur planarityM ändern ohne points: areaM2 bleibt erhalten
    m5 = inhalte.aendern(
        conn,
        "messungen",
        "p",
        "M-F",
        {"planarityM": 0.05},
        version=4,
        nutzer_id=1,
    )
    assert m5.daten["areaM2"] == 8.0
    assert m5.daten["planarityM"] == 0.05


# --- loeschen(...) ---


def test_loeschen_erfolg_und_protokoll(conn: sqlite3.Connection) -> None:
    inhalte.anlegen(conn, "pois", "p", "P-1", GUT_POI, nutzer_id=1, revision=1, now=100)
    inhalte.loeschen(
        conn, "pois", "p", "P-1", version=1, nutzer_id=2, kanal="web", token_name=None, now=250
    )

    # In DB als gelöscht markiert
    row = conn.execute("SELECT * FROM pois WHERE project_id='p' AND id='P-1'").fetchone()
    assert row["geloescht_am"] == 250
    assert row["geaendert_von"] == 2
    assert row["geaendert_am"] == 250
    assert row["version"] == 2

    # Protokoll
    e = protokoll.lesen(conn, project_id="p")[0]
    assert e.art == "poi.geloescht"
    assert e.objekt_id == "P-1"
    assert e.vorher["title"] == "Hauptportal"
    assert e.nachher is None
    assert e.zeit == 250
    assert e.nutzer_id == 2


def test_loeschen_nicht_gefunden_oder_schon_geloescht(conn: sqlite3.Connection) -> None:
    with pytest.raises(StarletteHTTPException) as exc_info:
        inhalte.loeschen(conn, "pois", "p", "GIBTS-NICHT", version=1, nutzer_id=1)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == inhalte.NICHT_GEFUNDEN

    inhalte.anlegen(conn, "pois", "p", "P-1", GUT_POI, nutzer_id=1, revision=1)
    inhalte.loeschen(conn, "pois", "p", "P-1", version=1, nutzer_id=1)

    # Zweites Löschen gibt 404
    with pytest.raises(StarletteHTTPException) as exc_info:
        inhalte.loeschen(conn, "pois", "p", "P-1", version=2, nutzer_id=1)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == inhalte.NICHT_GEFUNDEN


def test_loeschen_versionskonflikt_409(conn: sqlite3.Connection) -> None:
    inhalte.anlegen(conn, "pois", "p", "P-1", GUT_POI, nutzer_id=1, revision=1)
    with pytest.raises(Konflikt) as exc_info:
        inhalte.loeschen(conn, "pois", "p", "P-1", version=99, nutzer_id=1)
    assert exc_info.value.aktuell["id"] == "P-1"
    assert exc_info.value.aktuell["version"] == 1


def test_dump_format_in_datenbank(conn: sqlite3.Connection) -> None:
    d = {"title": "Tür & Wand", "note": "Hällö", "position": [1.0, 2.0, 3.0]}
    inhalte.anlegen(conn, "pois", "p", "P-DUMP", d, nutzer_id=1, revision=1)
    raw = conn.execute("SELECT daten FROM pois WHERE id='P-DUMP'").fetchone()[0]
    # ensure_ascii=False garantiert echte UTF-8-Zeichen statt \\u00fc
    assert "Tür & Wand" in raw
    assert "\\u00fc" not in raw
    assert "Hällö" in raw
    assert "\\u00e4" not in raw
    # separators=(',', ':') garantiert kompakte Trenner ohne Leerzeichen
    assert '":' in raw
    assert '": ' not in raw
    assert ',"' in raw
    assert ', "' not in raw
