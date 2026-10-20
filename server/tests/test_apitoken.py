import sqlite3
from pathlib import Path

import pytest

from scanview_server import apitoken, db, konten, protokoll, verwaltung


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "a.db")
    db.migrate(c)
    return c


def _konto(conn: sqlite3.Connection, email: str) -> konten.Nutzer:
    t = konten.einladung_anlegen(conn, email=email, name="T", grants={})
    return konten.einladung_einloesen(conn, t, lambda _c, _u: None)


@pytest.fixture
def nutzer(conn: sqlite3.Connection) -> konten.Nutzer:
    return _konto(conn, "t@b.de")


@pytest.fixture
def admin(conn: sqlite3.Connection) -> konten.Nutzer:
    return _konto(conn, "admin@b.de")


def test_erzeugen_und_pruefen(conn: sqlite3.Connection, nutzer: konten.Nutzer) -> None:
    roh, info = apitoken.erzeugen(conn, nutzer.id, "Claude Desktop", "lesen", 30, now=1000)
    assert roh.startswith("sv_") and len(roh) > 40
    unverschluesselt = conn.execute(
        "SELECT count(*) FROM api_tokens WHERE token_hash=?", (roh,),
    ).fetchone()[0]
    assert unverschluesselt == 0  # gespeichert wird nur der Hash, nie der Wert selbst
    t = apitoken.pruefen(conn, roh, now=1001)
    assert t is not None
    assert t.nutzer.id == nutzer.id
    assert t.umfang == "lesen"
    assert t.name == "Claude Desktop"
    assert info.laeuft_ab == 1000 + 30 * 86400
    assert info.zuletzt_benutzt is None


def test_wert_nie_im_protokoll(conn: sqlite3.Connection, nutzer: konten.Nutzer) -> None:
    roh, _ = apitoken.erzeugen(conn, nutzer.id, "x", "schreiben", now=1000)
    zeile = conn.execute(
        "SELECT token_hash FROM api_tokens WHERE user_id=?", (nutzer.id,),
    ).fetchone()
    h = zeile["token_hash"]
    dump = "\n".join(str(tuple(r)) for r in conn.execute("SELECT * FROM ereignisse"))
    assert roh not in dump
    assert roh[3:] not in dump
    assert h not in dump
    assert h[:16] not in dump
    assert "token.erstellt" in dump


@pytest.mark.parametrize(
    "fall",
    ["abgelaufen", "widerrufen", "gesperrt", "anonymisiert", "unbekannt", "leer", "ohne_praefix"],
)
def test_pruefen_lehnt_ab(
    conn: sqlite3.Connection, nutzer: konten.Nutzer, admin: konten.Nutzer, fall: str,
) -> None:
    roh, info = apitoken.erzeugen(conn, nutzer.id, "t", "lesen", 1, now=1000)
    now = 1001
    if fall == "abgelaufen":
        now = 1000 + 86400 + 1
    if fall == "widerrufen":
        assert apitoken.widerrufen(conn, nutzer.id, info.id, von=nutzer.id, kanal="web")
    if fall == "gesperrt":
        # now= konsistent zur simulierten Zeitlinie des Tokens (erzeugt bei now=1000) durchreichen
        # — sonst würde alle_widerrufen mit echter Wanduhrzeit prüfen, ob der (winzige simulierte)
        # laeuft_ab noch in der Zukunft liegt, und den Token fälschlich für schon abgelaufen halten.
        verwaltung.sperren(conn, nutzer.id, von=admin.id, kanal="cli", now=now)
        # Entsperren hebt nur die Kontosperre auf, nicht den Widerruf des Tokens.
        verwaltung.entsperren(conn, nutzer.id, von=admin.id, kanal="cli", now=now)
    if fall == "anonymisiert":
        verwaltung.anonymisieren(conn, nutzer.id, von=admin.id, kanal="cli")
    if fall == "unbekannt":
        roh = "sv_" + "A" * 43
    if fall == "leer":
        roh = ""
    if fall == "ohne_praefix":
        roh = roh[3:]
    assert apitoken.pruefen(conn, roh, now=now) is None


def test_grenzen(conn: sqlite3.Connection, nutzer: konten.Nutzer) -> None:
    for bad in ["", "x" * 61, "a\nb"]:
        with pytest.raises(ValueError):
            apitoken.erzeugen(conn, nutzer.id, bad, "lesen")
    for tage in [0, 366]:
        with pytest.raises(ValueError):
            apitoken.erzeugen(conn, nutzer.id, "t", "lesen", tage)
    with pytest.raises(ValueError):
        apitoken.erzeugen(conn, nutzer.id, "t", "admin")
    for i in range(apitoken.MAX_GUELTIG):
        apitoken.erzeugen(conn, nutzer.id, f"t{i}", "lesen")
    with pytest.raises(ValueError):
        apitoken.erzeugen(conn, nutzer.id, "zu viel", "lesen")
    anzahl = conn.execute(
        "SELECT COUNT(*) FROM api_tokens WHERE user_id=?", (nutzer.id,),
    ).fetchone()[0]
    assert anzahl == apitoken.MAX_GUELTIG  # der 11. Versuch hat nichts geschrieben (Transaktion)


def test_fremdes_token_nicht_widerrufbar(
    conn: sqlite3.Connection, nutzer: konten.Nutzer, admin: konten.Nutzer,
) -> None:
    _, info = apitoken.erzeugen(conn, nutzer.id, "t", "lesen")
    assert apitoken.widerrufen(conn, admin.id, info.id, von=admin.id, kanal="web") is False
    assert apitoken.widerrufen(conn, nutzer.id, 999999, von=nutzer.id, kanal="web") is False


def test_widerrufen_protokolliert_ohne_wert(
    conn: sqlite3.Connection, nutzer: konten.Nutzer,
) -> None:
    _, info = apitoken.erzeugen(conn, nutzer.id, "Mein Skript", "schreiben", now=1000)
    assert apitoken.widerrufen(conn, nutzer.id, info.id, von=nutzer.id, kanal="web") is True
    assert apitoken.widerrufen(conn, nutzer.id, info.id, von=nutzer.id, kanal="web") is False
    token_ereignisse = [e for e in reversed(protokoll.lesen(conn)) if e.art.startswith("token.")]
    [erstellt_e, widerrufen_e] = token_ereignisse
    assert erstellt_e.art == "token.erstellt"
    assert widerrufen_e.art == "token.widerrufen"
    assert widerrufen_e.nutzer_id == nutzer.id
    assert widerrufen_e.project_id is None
    assert widerrufen_e.nachher == {
        "name": "Mein Skript", "umfang": "schreiben", "laeuft_ab": info.laeuft_ab,
    }


def test_abmelden_ueberall_widerruft(
    conn: sqlite3.Connection, nutzer: konten.Nutzer, admin: konten.Nutzer,
) -> None:
    roh, _ = apitoken.erzeugen(conn, nutzer.id, "t", "lesen")
    verwaltung.abmelden_ueberall(conn, nutzer.id, von=admin.id, kanal="cli")
    assert apitoken.pruefen(conn, roh) is None


def test_sperren_widerruft(
    conn: sqlite3.Connection, nutzer: konten.Nutzer, admin: konten.Nutzer,
) -> None:
    roh, _ = apitoken.erzeugen(conn, nutzer.id, "t", "lesen")
    verwaltung.sperren(conn, nutzer.id, von=admin.id, kanal="cli")
    assert apitoken.pruefen(conn, roh) is None
    # Entsperren allein macht den Token nicht wieder gültig — sperren muss ihn WIDERRUFEN
    # (widerrufen_am), nicht nur das Konto sperren (disabled), sonst würde dieser Test auch ohne
    # apitoken.alle_widerrufen in verwaltung.sperren grün bleiben.
    verwaltung.entsperren(conn, nutzer.id, von=admin.id, kanal="cli")
    assert apitoken.pruefen(conn, roh) is None


def test_zuletzt_benutzt_hoechstens_minuetlich(
    conn: sqlite3.Connection, nutzer: konten.Nutzer,
) -> None:
    roh, _info = apitoken.erzeugen(conn, nutzer.id, "t", "lesen", now=1000)
    apitoken.pruefen(conn, roh, now=1010)
    apitoken.pruefen(conn, roh, now=1030)
    assert apitoken.liste(conn, nutzer.id, now=1030)[0].zuletzt_benutzt == 1010
    apitoken.pruefen(conn, roh, now=1071)
    assert apitoken.liste(conn, nutzer.id, now=1071)[0].zuletzt_benutzt == 1071


def test_liste_neueste_zuerst(conn: sqlite3.Connection, nutzer: konten.Nutzer) -> None:
    apitoken.erzeugen(conn, nutzer.id, "a", "lesen", 90, now=1000)
    apitoken.erzeugen(conn, nutzer.id, "b", "lesen", 90, now=1001)
    namen = [t.name for t in apitoken.liste(conn, nutzer.id, now=1002)]
    assert namen == ["b", "a"]


def test_liste_ohne_widerrufene_und_abgelaufene(
    conn: sqlite3.Connection, nutzer: konten.Nutzer,
) -> None:
    apitoken.erzeugen(conn, nutzer.id, "gueltig", "lesen", 1, now=87400)
    apitoken.erzeugen(conn, nutzer.id, "abgelaufen", "lesen", 1, now=1000)
    _, widerrufen_info = apitoken.erzeugen(conn, nutzer.id, "widerrufen", "lesen", 1, now=87400)
    apitoken.widerrufen(conn, nutzer.id, widerrufen_info.id, von=nutzer.id, kanal="web")
    namen = {t.name for t in apitoken.liste(conn, nutzer.id, now=87401)}
    assert namen == {"gueltig"}


def test_alle_widerrufen_zaehlt_und_markiert_nur_gueltige(
    conn: sqlite3.Connection, nutzer: konten.Nutzer,
) -> None:
    """`tokensWiderrufen` soll „waren gültig, sind es jetzt nicht mehr“ bedeuten —
    ein bereits abgelaufener Token wird nicht mitgezählt und auch nicht angefasst (widerrufen_am
    bleibt NULL; er ist über `pruefen`/`liste` ohnehin schon unbrauchbar)."""
    apitoken.erzeugen(conn, nutzer.id, "abgelaufen", "lesen", 1, now=1000)  # laeuft_ab=87400
    apitoken.erzeugen(conn, nutzer.id, "gueltig", "lesen", 1, now=87400)  # laeuft_ab=173800
    anzahl = apitoken.alle_widerrufen(conn, nutzer.id, now=87401)
    assert anzahl == 1
    rows = {
        r["name"]: r["widerrufen_am"] for r in conn.execute(
            "SELECT name, widerrufen_am FROM api_tokens WHERE user_id=?", (nutzer.id,),
        )
    }
    assert rows == {"abgelaufen": None, "gueltig": 87401}


# Als Codepunkte statt Literale: unsichtbare Zeichen gehören nicht in den Quelltext.
@pytest.mark.parametrize("codepunkt", [
    0x202E,  # RIGHT-TO-LEFT OVERRIDE (Cf, „Trojan Source“)
    0x2066,  # LEFT-TO-RIGHT ISOLATE (Cf)
    0x200B,  # ZERO WIDTH SPACE (Cf)
    0xFEFF,  # BOM / ZERO WIDTH NO-BREAK SPACE (Cf)
    0x2028,  # LINE SEPARATOR (Zl)
    0x2029,  # PARAGRAPH SEPARATOR (Zp)
    0x0085,  # NEXT LINE (Cc, C1)
    0x009B,  # CSI (Cc, C1)
    0x001B,  # ESC (Cc, C0)
])
def test_name_ohne_unicode_steuer_und_formatzeichen(
    conn: sqlite3.Connection, nutzer: konten.Nutzer, codepunkt: int,
) -> None:
    """Befund B-TE-N2: Kategorien Cc, Cf, Zl, Zp abgelehnt — sonst ließen sich
    Token-Namen in Portal, Verlauf und Terminal optisch verfälschen."""
    with pytest.raises(ValueError):
        apitoken.erzeugen(conn, nutzer.id, f"Laptop{chr(codepunkt)}gnal", "lesen")
    assert apitoken.liste(conn, nutzer.id) == []


def test_name_mit_umlauten_und_sonderzeichen_erlaubt(
    conn: sqlite3.Connection, nutzer: konten.Nutzer,
) -> None:
    _roh, info = apitoken.erzeugen(conn, nutzer.id, "Büro-Skript (Größe) ß/é №1 ✓", "lesen")
    assert info.name == "Büro-Skript (Größe) ß/é №1 ✓"


def test_agent_merkmal(conn: sqlite3.Connection, nutzer: konten.Nutzer) -> None:
    """G7: Token „für einen KI-Agenten“ — Merkmal in Info, Prüfung, Liste und Protokoll."""
    roh, info = apitoken.erzeugen(conn, nutzer.id, "Claude", "schreiben", agent=True, now=1000)
    assert info.agent is True
    t = apitoken.pruefen(conn, roh, now=1001)
    assert t is not None and t.agent is True
    roh2, info2 = apitoken.erzeugen(conn, nutzer.id, "Skript", "schreiben", now=1002)
    t2 = apitoken.pruefen(conn, roh2, now=1003)
    assert info2.agent is False and t2 is not None and t2.agent is False
    assert [i.agent for i in apitoken.liste(conn, nutzer.id, now=1004)] == [False, True]
    erstellt = [e for e in reversed(protokoll.lesen(conn)) if e.art == "token.erstellt"]
    assert [e.nachher["agent"] for e in erstellt] == [True, False]
