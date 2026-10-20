import sqlite3
from pathlib import Path

import pytest

from scanview_server import apitoken, db, konten, passwort, projektverwaltung, protokoll, verwaltung
from scanview_server.tokens import hash_token


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "v.db")
    db.migrate(c)
    return c


def _konto(conn: sqlite3.Connection, email: str = "t@b.de",
           grants: dict[str, str] | None = None) -> int:
    t = konten.einladung_anlegen(conn, email=email, name="T", grants=grants or {})
    return konten.einladung_einloesen(conn, t, lambda _c, _u: None).id


def test_einladen_protokolliert_ohne_personendaten(conn: sqlite3.Connection) -> None:
    token = verwaltung.einladen(conn, email="neu@b.de", name="Neu Nutzer",
                                grants={"p1": "bearbeiter"}, von=None, kanal="cli")
    [e] = protokoll.lesen(conn)
    assert (e.art, e.kanal, e.nutzer_id) == ("einladung.angelegt", "cli", None)
    assert e.objekt_id == verwaltung.einladung_id(hash_token(token))
    assert e.nachher == {"admin": False, "rechte": {"p1": "bearbeiter"}, "zweck": "neu"}
    roh = " ".join(str(v) for r in conn.execute("SELECT * FROM ereignisse") for v in tuple(r))
    for geheim in (token, hash_token(token), "neu@b.de", "Neu Nutzer"):
        assert geheim not in roh


def test_einladen_fehler_schreibt_nichts(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        verwaltung.einladen(conn, email="kein-at", name="X", grants={}, von=None, kanal="cli")
    assert conn.execute("SELECT COUNT(*) FROM ereignisse").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM invites").fetchone()[0] == 0


def test_sperren_ist_atomar_mit_protokoll(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid = _konto(conn)
    s = konten.sitzung_anlegen(conn, uid)

    def kaputt(*_a: object, **_k: object) -> int:
        raise RuntimeError("Protokoll nicht schreibbar")

    monkeypatch.setattr(verwaltung.protokoll, "ereignis", kaputt)
    with pytest.raises(RuntimeError):
        verwaltung.sperren(conn, uid, von=None, kanal="cli")
    assert konten.sitzung_nutzer(conn, s) is not None  # nichts gesperrt, Sitzung lebt
    monkeypatch.undo()
    assert verwaltung.sperren(conn, uid, von=None, kanal="cli") == 1
    assert konten.sitzung_nutzer(conn, s) is None
    e = protokoll.lesen(conn)[0]
    assert (e.art, e.objekt_id, e.nachher) == (
        "konto.gesperrt", str(uid), {"sitzungenBeendet": 1, "tokensWiderrufen": 0},
    )


def test_sperren_und_abmelden_ueberall_widerrufen_tokens(conn: sqlite3.Connection) -> None:
    uid = _konto(conn)
    roh, _ = apitoken.erzeugen(conn, uid, "Skript", "lesen")
    assert verwaltung.sperren(conn, uid, von=None, kanal="cli") == 0
    assert apitoken.pruefen(conn, roh) is None
    e = protokoll.lesen(conn)[0]
    assert e.nachher == {"sitzungenBeendet": 0, "tokensWiderrufen": 1}

    uid2 = _konto(conn, "z@b.de")
    roh2, _ = apitoken.erzeugen(conn, uid2, "Skript", "lesen")
    assert verwaltung.abmelden_ueberall(conn, uid2, von=None, kanal="cli") == 0
    assert apitoken.pruefen(conn, roh2) is None
    e2 = protokoll.lesen(conn)[0]
    assert (e2.art, e2.nachher) == (
        "konto.abgemeldet", {"sitzungenBeendet": 0, "tokensWiderrufen": 1},
    )


def test_entsperren_abmelden_rechte_entziehen_protokolliert(conn: sqlite3.Connection) -> None:
    uid = _konto(conn, grants={"p1": "betrachter", "p2": "bearbeiter"})
    konten.sitzung_anlegen(conn, uid)
    verwaltung.entsperren(conn, uid, von=None, kanal="cli")
    assert verwaltung.abmelden_ueberall(conn, uid, von=None, kanal="cli") == 1
    assert verwaltung.rechte_entziehen(conn, uid, "p1", von=None, kanal="cli") == 1
    assert verwaltung.rechte_entziehen(conn, uid, None, von=None, kanal="cli") == 1
    assert verwaltung.rechte_entziehen(conn, uid, None, von=None, kanal="cli") == 0
    folge = [(e.art, e.project_id, e.vorher) for e in reversed(protokoll.lesen(conn))]
    assert folge == [
        ("konto.angelegt", None, None),
        ("konto.entsperrt", None, None),
        ("konto.abgemeldet", None, None),
        ("recht.entzogen", "p1", "betrachter"),
        ("recht.entzogen", "p2", "bearbeiter"),
    ]


def test_einladungen_widerrufen_je_einladung_ein_ereignis(conn: sqlite3.Connection) -> None:
    t = verwaltung.einladen(conn, email="w@b.de", name="W", grants={}, von=None, kanal="cli")
    assert verwaltung.einladungen_widerrufen(conn, "w@b.de", von=None, kanal="cli") == 1
    assert konten.einladung_pruefen(conn, t) is None
    e = protokoll.lesen(conn)[0]
    assert e.art == "einladung.widerrufen"
    assert e.objekt_id == verwaltung.einladung_id(hash_token(t))
    assert verwaltung.einladungen_widerrufen(conn, "w@b.de", von=None, kanal="cli") == 0


def _alles_als_text(conn: sqlite3.Connection) -> str:
    tabellen = [str(r[0]) for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
    )]
    return " ".join(
        str(v) for t in tabellen for r in conn.execute(f"SELECT * FROM {t}") for v in tuple(r)
    )


def _konto_mit_allem(conn: sqlite3.Connection) -> tuple[int, str]:
    t = konten.einladung_anlegen(conn, email="Anna.Muster@b.de", name="Anna Muster",
                                 grants={"p1": "bearbeiter", "p2": "betrachter"})
    uid = konten.einladung_einloesen(conn, t, lambda _c, _u: None).id
    konten.sitzung_anlegen(conn, uid)
    conn.execute("INSERT INTO passwords (user_id, hash, updated_at) VALUES (?, 'h', 0)", (uid,))
    conn.execute("INSERT INTO passkeys (id, user_id, public_key, sign_count, created_at)"
                 " VALUES ('pk1', ?, x'00', 0, 0)", (uid,))
    schl = passwort.schluessel("anna.muster@b.de")
    conn.execute("INSERT INTO throttle (key, failures, locked_until, geaendert_am)"
                 " VALUES (?, 2, 0, 0)", (schl,))
    verwaltung.einladen(conn, email="anna.muster@b.de", name="Anna Muster", grants={},
                        zuruecksetzen=True, von=None, kanal="cli")
    # Bewusst kein Personenbezug im Token-Namen (anders als bei den echten Feldern oben): der
    # Name landet auch im (unveränderlichen) Protokoll-Ereignis token.erstellt und würde dort die
    # Anonymisierung überdauern (offene Entscheidung, wie Freitext in POI-Notizen).
    apitoken.erzeugen(conn, uid, "Mein Skript", "lesen")
    return uid, schl


def test_anonymisieren_entfernt_alle_personendaten(conn: sqlite3.Connection) -> None:
    uid, schl = _konto_mit_allem(conn)
    andere = _konto(conn, "bleibt@b.de", {"p1": "betrachter"})
    z = verwaltung.anonymisieren(conn, uid, von=None, kanal="cli")
    assert z == {"sitzungen": 1, "passkeys": 1, "passwort": 1, "tokens": 1, "rechte": 2,
                 "einladungenGeloescht": 1, "einladungenGeschwaerzt": 1}
    row = conn.execute("SELECT name, email, disabled, is_admin FROM users WHERE id=?",
                       (uid,)).fetchone()
    assert tuple(row) == (f"Gelöschtes Konto {uid}", f"geloescht-{uid}@invalid", 1, 0)
    text = _alles_als_text(conn).lower()
    for spur in ("anna", "muster", schl):
        assert spur not in text
    for tabelle in ("sessions", "passkeys", "passwords", "project_roles", "api_tokens"):
        assert conn.execute(f"SELECT COUNT(*) FROM {tabelle} WHERE user_id=?",
                            (uid,)).fetchone()[0] == 0
    # Andere Konten bleiben unberührt, das Protokoll nennt nur Anzahlen.
    assert conn.execute("SELECT role FROM project_roles WHERE user_id=?",
                        (andere,)).fetchone()[0] == "betrachter"
    e = protokoll.lesen(conn)[0]
    assert (e.art, e.kanal, e.objekt_id, e.nachher) == ("konto.anonymisiert", "cli", str(uid), z)
    assert e.nutzer_name is None
    # Anmelden mit der alten Adresse findet kein Konto mehr.
    assert konten.konto_id(conn, "anna.muster@b.de") is None


def test_anonymisieren_ist_atomar(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid, _ = _konto_mit_allem(conn)
    vorher = _alles_als_text(conn)

    def kaputt(*_a: object, **_k: object) -> int:
        raise RuntimeError("Protokoll nicht schreibbar")

    monkeypatch.setattr(verwaltung.protokoll, "ereignis", kaputt)
    with pytest.raises(RuntimeError):
        verwaltung.anonymisieren(conn, uid, von=None, kanal="cli")
    assert _alles_als_text(conn) == vorher


def test_anonymisieren_unbekannte_id(conn: sqlite3.Connection) -> None:
    with pytest.raises(LookupError):
        verwaltung.anonymisieren(conn, 999, von=None, kanal="cli")
    assert conn.execute("SELECT COUNT(*) FROM ereignisse").fetchone()[0] == 0


def test_anonymisiertes_konto_ist_endgueltig(conn: sqlite3.Connection) -> None:
    uid, _ = _konto_mit_allem(conn)
    verwaltung.anonymisieren(conn, uid, von=None, kanal="cli")
    assert verwaltung.ist_anonymisiert(conn, uid)
    ersatz = f"geloescht-{uid}@invalid"
    vorher = conn.execute("SELECT COUNT(*) FROM ereignisse").fetchone()[0]
    for versuch in (
        lambda: verwaltung.entsperren(conn, uid, von=None, kanal="cli"),
        lambda: verwaltung.recht_setzen(conn, uid, "p1", "betrachter", von=None, kanal="cli"),
        lambda: verwaltung.einladen(conn, email=ersatz, name="X", grants={}, zuruecksetzen=True,
                                    von=None, kanal="cli"),
        lambda: verwaltung.einladen(conn, email=ersatz, name="X", grants={}, von=None,
                                    kanal="cli"),
        lambda: verwaltung.anonymisieren(conn, uid, von=None, kanal="cli"),
    ):
        with pytest.raises(verwaltung.KontoAnonymisiert, match="Konto wurde anonymisiert"):
            versuch()
    assert conn.execute("SELECT COUNT(*) FROM ereignisse").fetchone()[0] == vorher
    assert conn.execute("SELECT disabled FROM users WHERE id=?", (uid,)).fetchone()[0] == 1
    # Entziehen (harmlos) geht; die frühere Adresse darf ein NEUES Konto bekommen.
    assert verwaltung.recht_setzen(conn, uid, "p1", None, von=None, kanal="cli") is None
    verwaltung.einladen(conn, email="anna.muster@b.de", name="Anna Neu", grants={}, von=None,
                        kanal="cli")
    assert verwaltung.anonymisierte_ids(conn) == {uid}


@pytest.mark.parametrize("als_leitung", [False, True])
def test_anonymisiertes_konto_kann_nicht_nur_gewerk_aendern(
    conn: sqlite3.Connection, als_leitung: bool,
) -> None:
    uid = _konto(conn, grants={"p1": "bearbeiter"})
    gewerk = projektverwaltung.gewerk_anlegen(
        conn, "p1", name="Testgewerk", kurz="TG", bauherrenseite=False, von=uid,
    ).id
    verwaltung.anonymisieren(conn, uid, von=None, kanal="cli")
    vorher = _alles_als_text(conn)
    # Anonymisieren entfernt alle Rollen: auch eine reine Gewerkänderung wird abgewiesen.
    with pytest.raises(LookupError, match="nicht beteiligt"):
        verwaltung.beteiligter_setzen(
            conn, uid, "p1", "bearbeiter", gewerk,
            als_leitung=als_leitung, von=uid, kanal="web",
        )
    assert _alles_als_text(conn) == vorher


@pytest.mark.parametrize("als_leitung", [False, True])
def test_aktives_konto_kann_nur_gewerk_aendern(
    conn: sqlite3.Connection, als_leitung: bool,
) -> None:
    uid = _konto(conn, grants={"p1": "bearbeiter"})
    gewerk = projektverwaltung.gewerk_anlegen(
        conn, "p1", name="Testgewerk", kurz="TG", bauherrenseite=False, von=uid,
    ).id
    assert verwaltung.beteiligter_setzen(
        conn, uid, "p1", "bearbeiter", gewerk,
        als_leitung=als_leitung, von=uid, kanal="web",
    ) == "bearbeiter"
    row = conn.execute("SELECT role, gewerk_id FROM project_roles WHERE user_id=?", (uid,))
    assert tuple(row.fetchone()) == ("bearbeiter", gewerk)
    e = protokoll.lesen(conn)[0]
    assert (e.art, e.vorher, e.nachher) == ("beteiligter.gewerk", None, gewerk)


@pytest.mark.parametrize("rand", [" ", "\t\n", "\u00a0"])
def test_einladung_suche_und_widerruf_normalisieren_email_rand(
    conn: sqlite3.Connection, rand: str,
) -> None:
    email = "Test@example.org"
    token = verwaltung.einladen(
        conn, email=f"{rand}{email}{rand}", name="Test", grants={}, von=None, kanal="cli",
    )
    anderer = verwaltung.einladen(
        conn, email="anders@example.org", name="Anderer", grants={}, von=None, kanal="cli",
    )
    assert konten.einladung_bekannt(conn, f"{rand}{email}{rand}")
    assert verwaltung.einladungen_widerrufen(
        conn, f"{rand}{email}{rand}", von=None, kanal="cli",
    ) == 1
    assert konten.einladung_pruefen(conn, token) is None
    assert konten.einladung_pruefen(conn, anderer) is not None
    assert verwaltung.einladungen_widerrufen(conn, email, von=None, kanal="cli") == 0
    e = protokoll.lesen(conn)[0]
    assert (e.art, e.objekt_id) == (
        "einladung.widerrufen", verwaltung.einladung_id(hash_token(token)),
    )


def test_konto_suche_normalisiert_email_rand(conn: sqlite3.Connection) -> None:
    uid = _konto(conn, email="  Test@example.org  ")
    assert konten.konto_id(conn, "\t Test@example.org \n") == uid
    assert konten.konto_id(conn, "anders@example.org") is None
