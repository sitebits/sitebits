"""Mutationstests für inhalte.py: Rückmelder- und Eigentumsregeln.

Deckt _eigen, _ist_rueckmeldung, _gespeichert_rueckmeldung, _kategorie,
RUECKMELDUNGEN_MAX (100), Selbstlöschungsprüfung und Moderationskonflikte ab.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException

from scanview_server import db, inhalte
from scanview_server.web import Konflikt, Ungueltig

POI_RUECKMELDUNG: dict[str, Any] = {
    "title": "Schaden an Tür",
    "category": inhalte.RUECKMELDUNG,
    "note": "Schloss klemmt",
    "position": [1.0, 2.0, 3.0],
    "station": None,
}

POI_BEFUND: dict[str, Any] = {
    "title": "Befund Fassade",
    "category": "Befund",
    "note": "Riss im Mauerwerk",
    "position": [1.0, 2.0, 3.0],
    "station": None,
}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "test_rueckmelder.db")
    db.migrate(c)
    c.execute(
        "INSERT INTO users (id, email, name, is_admin, created_at) "
        "VALUES (1, 'r1@test.de', 'Rückmelder 1', 0, 1000)"
    )
    c.execute(
        "INSERT INTO users (id, email, name, is_admin, created_at) "
        "VALUES (2, 'r2@test.de', 'Rückmelder 2', 0, 1000)"
    )
    c.execute(
        "INSERT INTO users (id, email, name, is_admin, created_at) "
        "VALUES (99, 'mod@test.de', 'Moderator', 0, 1000)"
    )
    return c


# --- Eigentum Dataclass ---


def test_eigentum_dataclass() -> None:
    e = inhalte.Eigentum(nutzer_id=42)
    assert e.nutzer_id == 42


# --- _eigen: Fremde Einträge und Einträge ohne Urheber ---


def test_eigen_anlegen_fremde_id_gibt_403(conn: sqlite3.Connection) -> None:
    # Eintrag von Nutzer 1 angelegt
    inhalte.anlegen(conn, "pois", "p", "P-1", POI_RUECKMELDUNG, nutzer_id=1, revision=None)

    # Nutzer 2 (Rückmelder) versucht dieselbe ID anzulegen -> 403 vor 409
    with pytest.raises(StarletteHTTPException) as exc_info:
        inhalte.anlegen(
            conn,
            "pois",
            "p",
            "P-1",
            POI_RUECKMELDUNG,
            nutzer_id=2,
            revision=None,
            eigen=inhalte.Eigentum(nutzer_id=2),
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == inhalte.NUR_EIGENE

    # Eigener Nutzer (Nutzer 1) mit gleicher ID bekommt 409 (ID schon vergeben)
    with pytest.raises(Konflikt) as exc_info_k:
        inhalte.anlegen(
            conn,
            "pois",
            "p",
            "P-1",
            POI_RUECKMELDUNG,
            nutzer_id=1,
            revision=None,
            eigen=inhalte.Eigentum(nutzer_id=1),
        )
    assert exc_info_k.value.text == inhalte.ID_VERGEBEN


def test_eigen_anlegen_eintrag_ohne_urheber_ist_nie_eigen(conn: sqlite3.Connection) -> None:
    # Eintrag ohne Urheber (erstellt_von = None, z.B. CLI)
    inhalte.anlegen(conn, "pois", "p", "P-CLI", POI_RUECKMELDUNG, nutzer_id=None, revision=None)

    # Rückmelder 1 darf ihn weder anlegen (kollidieren), ändern noch löschen
    with pytest.raises(StarletteHTTPException) as exc_info_anlegen:
        inhalte.anlegen(
            conn,
            "pois",
            "p",
            "P-CLI",
            POI_RUECKMELDUNG,
            nutzer_id=1,
            revision=None,
            eigen=inhalte.Eigentum(nutzer_id=1),
        )
    assert exc_info_anlegen.value.status_code == 403
    assert exc_info_anlegen.value.detail == inhalte.NUR_EIGENE

    with pytest.raises(StarletteHTTPException) as exc_info_aendern:
        inhalte.aendern(
            conn,
            "pois",
            "p",
            "P-CLI",
            {"note": "neu"},
            version=1,
            nutzer_id=1,
            eigen=inhalte.Eigentum(nutzer_id=1),
        )
    assert exc_info_aendern.value.status_code == 403
    assert exc_info_aendern.value.detail == inhalte.NUR_EIGENE

    with pytest.raises(StarletteHTTPException) as exc_info_del:
        inhalte.loeschen(
            conn,
            "pois",
            "p",
            "P-CLI",
            version=1,
            nutzer_id=1,
            eigen=inhalte.Eigentum(nutzer_id=1),
        )
    assert exc_info_del.value.status_code == 403
    assert exc_info_del.value.detail == inhalte.NUR_EIGENE


def test_eigen_aendern_und_loeschen_fremder_eintrag(conn: sqlite3.Connection) -> None:
    inhalte.anlegen(conn, "pois", "p", "P-1", POI_RUECKMELDUNG, nutzer_id=1, revision=None)

    # Nutzer 2 (Rückmelder) darf nicht ändern
    with pytest.raises(StarletteHTTPException) as exc_info_aendern:
        inhalte.aendern(
            conn,
            "pois",
            "p",
            "P-1",
            {"note": "neu"},
            version=1,
            nutzer_id=2,
            eigen=inhalte.Eigentum(nutzer_id=2),
        )
    assert exc_info_aendern.value.status_code == 403
    assert exc_info_aendern.value.detail == inhalte.NUR_EIGENE

    # Nutzer 2 darf nicht löschen
    with pytest.raises(StarletteHTTPException) as exc_info_del:
        inhalte.loeschen(
            conn,
            "pois",
            "p",
            "P-1",
            version=1,
            nutzer_id=2,
            eigen=inhalte.Eigentum(nutzer_id=2),
        )
    assert exc_info_del.value.status_code == 403
    assert exc_info_del.value.detail == inhalte.NUR_EIGENE

    # Nutzer 1 (Eigentümer) darf ändern und löschen
    inhalte.aendern(
        conn,
        "pois",
        "p",
        "P-1",
        {"note": "eigen"},
        version=1,
        nutzer_id=1,
        eigen=inhalte.Eigentum(nutzer_id=1),
    )
    inhalte.loeschen(
        conn,
        "pois",
        "p",
        "P-1",
        version=2,
        nutzer_id=1,
        eigen=inhalte.Eigentum(nutzer_id=1),
    )


# --- _kategorie: Erfordert exakt "Rückmeldung" ---


def test_kategorie_anlegen_und_aendern_nur_rueckmeldung(conn: sqlite3.Connection) -> None:
    # Ohne Eigentum (Bearbeiter/Admin) darf jede Kategorie gesetzt werden
    p_adm = inhalte.anlegen(
        conn, "pois", "p", "P-ADM", POI_BEFUND, nutzer_id=99, revision=None, eigen=None
    )
    assert p_adm.daten["category"] == "Befund"

    # Rückmelder mit anderer Kategorie scheitert beim Anlegen
    with pytest.raises(
        Ungueltig, match=r"^Rückmelder legen nur Einträge der Kategorie „Rückmeldung“ an\.$"
    ) as exc_info:
        inhalte.anlegen(
            conn,
            "pois",
            "p",
            "P-R1",
            POI_BEFUND,
            nutzer_id=1,
            revision=None,
            eigen=inhalte.Eigentum(nutzer_id=1),
        )
    assert str(exc_info.value) == inhalte.NUR_RUECKMELDUNG

    # Rückmelder mit Kategorie mit Leerzeichen (nicht exakt "Rückmeldung") scheitert
    mit_leerzeichen = {**POI_RUECKMELDUNG, "category": "  Rückmeldung  "}
    with pytest.raises(
        Ungueltig, match=r"^Rückmelder legen nur Einträge der Kategorie „Rückmeldung“ an\.$"
    ) as exc_info:
        inhalte.anlegen(
            conn,
            "pois",
            "p",
            "P-R2",
            mit_leerzeichen,
            nutzer_id=1,
            revision=None,
            eigen=inhalte.Eigentum(nutzer_id=1),
        )
    assert str(exc_info.value) == inhalte.NUR_RUECKMELDUNG

    # NFD wird zu NFC normalisiert und akzeptiert
    nfd_daten = {**POI_RUECKMELDUNG, "category": "Ru\u0308ckmeldung"}
    neu = inhalte.anlegen(
        conn,
        "pois",
        "p",
        "P-R3",
        nfd_daten,
        nutzer_id=1,
        revision=None,
        eigen=inhalte.Eigentum(nutzer_id=1),
    )
    assert neu.daten["category"] == "Rückmeldung"

    # Beim Ändern: Versuch, die Kategorie auf etwas anderes zu ändern, scheitert
    with pytest.raises(
        Ungueltig, match=r"^Rückmelder legen nur Einträge der Kategorie „Rückmeldung“ an\.$"
    ) as exc_info:
        inhalte.aendern(
            conn,
            "pois",
            "p",
            "P-R3",
            {"category": "Befund"},
            version=1,
            nutzer_id=1,
            eigen=inhalte.Eigentum(nutzer_id=1),
        )
    assert str(exc_info.value) == inhalte.NUR_RUECKMELDUNG


# --- Wiederherstellen durch Rückmelder: nur selbst gelöschte ---


def test_wiederherstellen_nur_selbst_geloeschte(conn: sqlite3.Connection) -> None:
    # Rückmelder 1 legt P-1 an
    inhalte.anlegen(
        conn,
        "pois",
        "p",
        "P-1",
        POI_RUECKMELDUNG,
        nutzer_id=1,
        revision=None,
        eigen=inhalte.Eigentum(nutzer_id=1),
    )

    # Moderator (Nutzer 99) löscht P-1
    inhalte.loeschen(conn, "pois", "p", "P-1", version=1, nutzer_id=99, eigen=None)

    # Rückmelder 1 versucht P-1 wiederherzustellen -> 403 NUR_SELBST_GELOESCHTE
    with pytest.raises(StarletteHTTPException) as exc_info:
        inhalte.anlegen(
            conn,
            "pois",
            "p",
            "P-1",
            POI_RUECKMELDUNG,
            nutzer_id=1,
            revision=None,
            eigen=inhalte.Eigentum(nutzer_id=1),
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == inhalte.NUR_SELBST_GELOESCHTE

    # Wenn Rückmelder 1 selbst gelöscht hätte:
    # Neuer Eintrag P-2 von Rückmelder 1
    inhalte.anlegen(
        conn,
        "pois",
        "p",
        "P-2",
        POI_RUECKMELDUNG,
        nutzer_id=1,
        revision=None,
        eigen=inhalte.Eigentum(nutzer_id=1),
    )
    # Rückmelder 1 löscht selbst
    inhalte.loeschen(
        conn,
        "pois",
        "p",
        "P-2",
        version=1,
        nutzer_id=1,
        eigen=inhalte.Eigentum(nutzer_id=1),
    )
    # Rückmelder 1 stellt selbst wieder her -> erfolgreich
    wieder = inhalte.anlegen(
        conn,
        "pois",
        "p",
        "P-2",
        POI_RUECKMELDUNG,
        nutzer_id=1,
        revision=None,
        eigen=inhalte.Eigentum(nutzer_id=1),
    )
    assert wieder.version == 3


# --- _gespeichert_rueckmeldung: Gespeicherte Kategorie muss Rückmeldung sein ---


def test_gespeicherte_kategorie_andere_ist_nur_loeschbar(conn: sqlite3.Connection) -> None:
    # Nutzer 1 war früher Bearbeiter und hat Befund angelegt
    inhalte.anlegen(
        conn, "pois", "p", "P-BEFUND", POI_BEFUND, nutzer_id=1, revision=None, eigen=None
    )

    # Nutzer 1 ist nun Rückmelder. Ändern scheitert, selbst der Versuch,
    # den Eintrag zur Rückmeldung zu machen.
    with pytest.raises(
        Ungueltig, match=r"^Dieser Eintrag hat nicht \(mehr\) die Kategorie „Rückmeldung“"
    ) as exc_info:
        inhalte.aendern(
            conn,
            "pois",
            "p",
            "P-BEFUND",
            {"note": "Update"},
            version=1,
            nutzer_id=1,
            eigen=inhalte.Eigentum(nutzer_id=1),
        )
    assert str(exc_info.value) == inhalte.NUR_LOESCHBAR

    with pytest.raises(
        Ungueltig, match=r"^Dieser Eintrag hat nicht \(mehr\) die Kategorie „Rückmeldung“"
    ) as exc_info:
        inhalte.aendern(
            conn,
            "pois",
            "p",
            "P-BEFUND",
            {"category": inhalte.RUECKMELDUNG},
            version=1,
            nutzer_id=1,
            eigen=inhalte.Eigentum(nutzer_id=1),
        )
    assert str(exc_info.value) == inhalte.NUR_LOESCHBAR

    # Löschen durch den Rückmelder bleibt aber erlaubt!
    inhalte.loeschen(
        conn,
        "pois",
        "p",
        "P-BEFUND",
        version=1,
        nutzer_id=1,
        eigen=inhalte.Eigentum(nutzer_id=1),
    )

    # Wiederherstellen scheitert ebenfalls (war ein Befund)
    with pytest.raises(
        Ungueltig, match=r"^Dieser Eintrag hat nicht \(mehr\) die Kategorie „Rückmeldung“"
    ) as exc_info:
        inhalte.anlegen(
            conn,
            "pois",
            "p",
            "P-BEFUND",
            POI_RUECKMELDUNG,
            nutzer_id=1,
            revision=None,
            eigen=inhalte.Eigentum(nutzer_id=1),
        )
    assert str(exc_info.value) == inhalte.NUR_LOESCHBAR


# --- RUECKMELDUNGEN_MAX = 100 ---


def test_rueckmeldungen_max_obergrenze(conn: sqlite3.Connection) -> None:
    # Lege 100 aktive Rückmeldungen für Nutzer 1 an
    for i in range(100):
        conn.execute(
            "INSERT INTO pois (project_id, id, daten, erstellt_von, geaendert_von, "
            "erstellt_am, geaendert_am, version) VALUES ('p', ?, '{}', 1, 1, 100, 100, 1)",
            (f"P-R-{i}",),
        )
    # Nutzer 2 hat 5 eigene Einträge
    for i in range(5):
        conn.execute(
            "INSERT INTO pois (project_id, id, daten, erstellt_von, geaendert_von, "
            "erstellt_am, geaendert_am, version) VALUES ('p', ?, '{}', 2, 2, 100, 100, 1)",
            (f"P-R2-{i}",),
        )

    # Nutzer 1 stößt an die 100er Grenze
    with pytest.raises(
        Ungueltig, match=r"^Höchstens 100 eigene Rückmeldungen je Projekt\.$"
    ) as exc_info:
        inhalte.anlegen(
            conn,
            "pois",
            "p",
            "P-R-101",
            POI_RUECKMELDUNG,
            nutzer_id=1,
            revision=None,
            eigen=inhalte.Eigentum(nutzer_id=1),
        )
    assert str(exc_info.value) == "Höchstens 100 eigene Rückmeldungen je Projekt."

    # Nutzer 2 kann problemlos anlegen (hat erst 5)
    neu_r2 = inhalte.anlegen(
        conn,
        "pois",
        "p",
        "P-R2-NEU",
        POI_RUECKMELDUNG,
        nutzer_id=2,
        revision=None,
        eigen=inhalte.Eigentum(nutzer_id=2),
    )
    assert neu_r2.id == "P-R2-NEU"

    # In anderem Projekt kann Nutzer 1 anlegen
    neu_p2 = inhalte.anlegen(
        conn,
        "pois",
        "anderes_projekt",
        "P-R-ANDERS",
        POI_RUECKMELDUNG,
        nutzer_id=1,
        revision=None,
        eigen=inhalte.Eigentum(nutzer_id=1),
    )
    assert neu_p2.id == "P-R-ANDERS"

    # Wenn Nutzer 1 eine eigene Rückmeldung löscht, kann er wieder anlegen
    conn.execute("UPDATE pois SET geloescht_am=200 WHERE project_id='p' AND id='P-R-0'")
    neu_nach_del = inhalte.anlegen(
        conn,
        "pois",
        "p",
        "P-R-WIEDER-FREI",
        POI_RUECKMELDUNG,
        nutzer_id=1,
        revision=None,
        eigen=inhalte.Eigentum(nutzer_id=1),
    )
    assert neu_nach_del.id == "P-R-WIEDER-FREI"
