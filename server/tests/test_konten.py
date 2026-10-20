import sqlite3
import threading
from pathlib import Path

import pytest

from scanview_server import apitoken, db, konten, statistik


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "k.db")
    db.migrate(c)
    return c


def noop(_c: sqlite3.Connection, _uid: int) -> None:
    pass


def test_einladung_einloesen_legt_nutzer_und_rechte_an(conn: sqlite3.Connection) -> None:
    t = konten.einladung_anlegen(conn, email="t@b.de", name="Tina",
                                  grants={"kirche": "betrachter"})
    assert konten.einladung_pruefen(conn, t) is not None
    n = konten.einladung_einloesen(conn, t, noop)
    assert n.email == "t@b.de" and not n.is_admin
    assert konten.rolle(conn, n, "kirche") == "betrachter"
    assert konten.rolle(conn, n, "ost") is None
    assert konten.einladung_pruefen(conn, t) is None


def test_pruefen_verbrennt_nichts(conn: sqlite3.Connection) -> None:
    t = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={})
    for _ in range(3):
        assert konten.einladung_pruefen(conn, t) is not None


def test_abgelaufen(conn: sqlite3.Connection) -> None:
    t = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={}, now=1000)
    assert konten.einladung_pruefen(conn, t, now=1000 + 7 * 86400 + 1) is None
    with pytest.raises(konten.EinladungUngueltig):
        konten.einladung_einloesen(conn, t, noop, now=1000 + 7 * 86400 + 1)


def test_einloesen_nur_einmal_auch_parallel(tmp_path: Path) -> None:
    c0 = db.connect(tmp_path / "p.db")
    db.migrate(c0)
    t = konten.einladung_anlegen(c0, email="p@b.de", name="P", grants={})
    ergebnisse: list[str] = []

    def lauf() -> None:
        c = db.connect(tmp_path / "p.db")
        try:
            konten.einladung_einloesen(c, t, noop)
            ergebnisse.append("ok")
        except konten.EinladungUngueltig:
            ergebnisse.append("abgelehnt")

    th = [threading.Thread(target=lauf) for _ in range(4)]
    for x in th:
        x.start()
    for x in th:
        x.join()
    assert sorted(ergebnisse) == ["abgelehnt"] * 3 + ["ok"]
    assert c0.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_fehler_im_callback_verbraucht_nicht(conn: sqlite3.Connection) -> None:
    t = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={})

    def kaputt(_c: sqlite3.Connection, _u: int) -> None:
        raise RuntimeError("Credential ungültig")

    with pytest.raises(RuntimeError):
        konten.einladung_einloesen(conn, t, kaputt)
    assert konten.einladung_pruefen(conn, t) is not None
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_zuruecksetzen_loescht_alte_zugaenge(conn: sqlite3.Connection) -> None:
    t = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={})
    n = konten.einladung_einloesen(conn, t, noop)
    s = konten.sitzung_anlegen(conn, n.id)
    t2 = konten.einladung_anlegen(
        conn, email="a@b.de", name="A", grants={}, purpose="zuruecksetzen",
    )
    n2 = konten.einladung_einloesen(conn, t2, noop)
    assert n2.id == n.id
    assert konten.sitzung_nutzer(conn, s) is None


def test_zuruecksetzen_widerruft_api_tokens(conn: sqlite3.Connection) -> None:
    """Ein zurückgesetztes Konto behält keine alten API-Tokens — sonst
    bliebe ein kompromittiertes Konto über einen Token weiter erreichbar."""
    t = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={})
    n = konten.einladung_einloesen(conn, t, noop)
    roh, _ = apitoken.erzeugen(conn, n.id, "Skript", "lesen")
    assert apitoken.pruefen(conn, roh) is not None
    t2 = konten.einladung_anlegen(
        conn, email="a@b.de", name="A", grants={}, purpose="zuruecksetzen",
    )
    konten.einladung_einloesen(conn, t2, noop)
    assert apitoken.pruefen(conn, roh) is None


def test_zuruecksetzen_ohne_konto_ist_fehler(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        konten.einladung_anlegen(conn, email="x@b.de", name="X", grants={}, purpose="zuruecksetzen")


def test_ungueltige_rolle(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        konten.einladung_anlegen(conn, email="x@b.de", name="X", grants={"p": "admin"})


def test_sitzung_leerlauf_und_absolut(conn: sqlite3.Connection) -> None:
    t = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={}, now=0)
    n = konten.einladung_einloesen(conn, t, noop, now=0)
    s = konten.sitzung_anlegen(conn, n.id, now=0)
    assert konten.sitzung_nutzer(conn, s, now=13 * 86400) is not None
    assert konten.sitzung_nutzer(conn, s, now=13 * 86400 + 14 * 86400 + 1) is None
    s2 = konten.sitzung_anlegen(conn, n.id, now=0)
    for tag in range(1, 61, 10):
        assert konten.sitzung_nutzer(conn, s2, now=tag * 86400) is not None
    assert konten.sitzung_nutzer(conn, s2, now=60 * 86400 + 1) is None


def test_gesperrter_nutzer_hat_keine_sitzung(conn: sqlite3.Connection) -> None:
    t = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={})
    n = konten.einladung_einloesen(conn, t, noop)
    s = konten.sitzung_anlegen(conn, n.id)
    conn.execute("UPDATE users SET disabled=1 WHERE id=?", (n.id,))
    assert konten.sitzung_nutzer(conn, s) is None


def test_admin_rolle(conn: sqlite3.Connection) -> None:
    t = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={}, is_admin=True)
    n = konten.einladung_einloesen(conn, t, noop)
    assert konten.rolle(conn, n, "irgendwas") == "admin"


def test_zweite_offene_neu_einladung_fuer_gleiche_email_wird_ungueltig(
    conn: sqlite3.Connection,
) -> None:
    """Zwei offene 'neu'-Einladungen für dieselbe E-Mail (auch anders geschrieben) können beide
    angelegt werden, solange noch kein Konto existiert. Die zweite Einlösung darf nach dem
    Konto-Anlegen durch die erste nicht mit einem DB-Fehler (UNIQUE-Constraint) durchfallen,
    sondern muss sauber als EinladungUngueltig abgelehnt werden."""
    t1 = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={})
    t2 = konten.einladung_anlegen(conn, email="A@B.de", name="A Zwei", grants={})
    konten.einladung_einloesen(conn, t1, noop)
    with pytest.raises(konten.EinladungUngueltig):
        konten.einladung_einloesen(conn, t2, noop)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_ungueltiges_token_zeichen_gibt_none_zurueck(conn: sqlite3.Connection) -> None:
    """Kein gültiger ASCII-Token (z. B. aus einem verstümmelten Cookie) → None statt Crash."""
    bad = "einladung-\udc80"
    assert konten.einladung_pruefen(conn, bad) is None
    with pytest.raises(konten.EinladungUngueltig):
        konten.einladung_einloesen(conn, bad, noop)


def test_aufraeumen_loescht_nur_veraltete_zeilen(conn: sqlite3.Connection) -> None:
    now = 10_000_000
    t = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={}, now=now)
    uid = konten.einladung_einloesen(conn, t, noop, now=now).id
    frisch = konten.sitzung_anlegen(conn, uid, now=now)
    konten.sitzung_anlegen(conn, uid, now=now - konten.ABSOLUT_S - 1)  # absolut abgelaufen
    conn.execute(  # im Leerlauf abgelaufen, absolut noch gültig
        "INSERT INTO sessions VALUES ('leer', ?, ?, ?, ?)",
        (uid, now - 20 * 86400, now - konten.LEERLAUF_S - 1, now + 86400),
    )
    alt_offen = konten.einladung_anlegen(conn, email="x@b.de", name="X", grants={},
                                         now=now - 40 * 86400)
    konten.einladung_anlegen(conn, email="y@b.de", name="Y", grants={},
                             now=now - 20 * 86400)  # abgelaufen, aber < 30 Tage her → bleibt
    conn.executemany(
        "INSERT INTO challenges (id, challenge, purpose, expires_at) VALUES (?,?,'anmelden',?)",
        [("alt", b"c", now - 1), ("neu", b"c", now + 300)],
    )
    alt = now - konten.DROSSEL_OFFEN_AUFBEWAHREN_S - 1
    conn.executemany(
        "INSERT INTO throttle (key, failures, locked_until, geaendert_am) VALUES (?,?,?,?)",
        [("vorbei", 0, now - 2 * 86400, now - 2 * 86400), ("gesperrt", 0, now + 60, now),
         ("gestern", 0, now - 3600, now - 3600), ("offen", 3, 0, now - 86400),
         # WB-N2: offene Fehlversuche, seit über 30 Tagen unverändert → weg
         ("offen-alt", 3, 0, alt),
         # aktive Sperre bleibt, auch wenn die Zeile alt ist
         ("gesperrt-alt", 0, now + 60, alt)],
    )
    geloescht = konten.aufraeumen(conn, now=now, lesezugriffe_tage=90)
    assert geloescht == {
        "sessions": 2, "invites": 1, "challenges": 1, "throttle_statistik": 0, "throttle": 2,
        "lesezugriffe": 0,
    }
    assert konten.sitzung_nutzer(conn, frisch, now=now) is not None
    assert konten.einladung_pruefen(conn, alt_offen, now=now - 40 * 86400) is None
    # Eingelöste Einladung (a@b.de) bleibt als Nachweis stehen.
    assert conn.execute("SELECT COUNT(*) FROM invites").fetchone()[0] == 2
    rest = {r[0] for r in conn.execute("SELECT key FROM throttle")}
    assert rest == {"gesperrt", "gestern", "offen", "gesperrt-alt"}


def test_aufraeumen_loescht_statistik_drosselzeilen_nach_fenster(
    conn: sqlite3.Connection,
) -> None:
    """`stat:`-Zeilen sollen nicht 30 Tage stehen bleiben, sondern schon
    sobald ihr Fenster (statistik.FENSTER_S) vorbei ist."""
    statistik.erlaubt(conn, 1, "p", now=1000)
    assert conn.execute(
        "SELECT COUNT(*) FROM throttle WHERE key LIKE 'stat:%'",
    ).fetchone()[0] == 1
    geloescht = konten.aufraeumen(conn, now=1000 + statistik.FENSTER_S + 1, lesezugriffe_tage=90)
    assert geloescht["throttle_statistik"] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM throttle WHERE key LIKE 'stat:%'",
    ).fetchone()[0] == 0


def test_konto_id_findet_case_insensitiv_und_getrimmt(conn: sqlite3.Connection) -> None:
    """Schreibende Zugriffsverwaltung (sperren/entsperren/Rechte/Widerrufen) läuft
    ausschließlich über `verwaltung.*` (protokolliert) — hier bleiben nur noch die Lesefunktionen,
    die `konten.py` dafür noch öffentlich anbietet (siehe test_verwaltung.py, test_cli.py)."""
    t = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={})
    n = konten.einladung_einloesen(conn, t, noop)
    assert konten.konto_id(conn, " A@B.de ") == n.id
    assert konten.konto_id(conn, "fremd@b.de") is None


def test_einladung_bekannt(conn: sqlite3.Connection) -> None:
    konten.einladung_anlegen(conn, email="neu@b.de", name="N", grants={})
    assert konten.einladung_bekannt(conn, "neu@b.de")
    assert konten.einladung_bekannt(conn, "NEU@b.de")
    assert not konten.einladung_bekannt(conn, "anders@b.de")
