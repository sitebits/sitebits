"""/api/v1/konto/* — nur Cookie (ADR 0008), nie Bearer.

Anlegen/Auflisten/Widerrufen eigener API-Tokens (routen_konto.py) und die
Git-Namens-Einwilligung (ADR 0011, users.git_name_zeigen)."""

from __future__ import annotations

import sqlite3

import pytest
from conftest import ORIGIN, csrf
from fastapi.testclient import TestClient
from nutzer import person

from scanview_server import apitoken, db, konten, protokoll, verwaltung

TOKENS = "/api/v1/konto/tokens"
KONTO = "/api/v1/konto"


def _conn(c: TestClient) -> sqlite3.Connection:
    return db.connect(c.app.state.settings.db_path)  # type: ignore[attr-defined]


def _anon(c: TestClient) -> TestClient:
    """Frischer Client ohne Cookie auf derselben App (wie test_api_bearer.anon)."""
    return TestClient(c.app, base_url=ORIGIN)


@pytest.fixture
def nutzer(client: TestClient) -> tuple[konten.Nutzer, TestClient]:
    return person(client, "t@test")


# --- Tokens: anlegen/auflisten/widerrufen -------------------------------------------------------


def test_anlegen_auflisten_widerrufen(nutzer: tuple[konten.Nutzer, TestClient]) -> None:
    _n, c = nutzer
    r = c.post(TOKENS, headers=csrf(), json={"name": "Claude Desktop", "umfang": "lesen",
                                              "tage": 30})
    assert r.status_code == 201, r.text
    d = r.json()
    roh = d["token"]
    assert roh.startswith("sv_") and len(roh) > 40
    info = d["info"]
    assert set(info) == {"id", "name", "umfang", "erstelltAm", "laeuftAb", "zuletztBenutzt",
                         "agent"}
    assert info["name"] == "Claude Desktop" and info["umfang"] == "lesen"
    assert info["zuletztBenutzt"] is None

    r = c.get(TOKENS)
    assert r.status_code == 200
    liste = r.json()["tokens"]
    assert len(liste) == 1 and liste[0]["id"] == info["id"]
    # Der Wert (auch nicht sein Präfix-freier Rest) steht nirgends in der Liste.
    dump = str(r.json())
    assert roh not in dump and roh[3:] not in dump

    r = c.delete(f"{TOKENS}/{info['id']}", headers=csrf())
    assert r.status_code == 204
    assert c.get(TOKENS).json()["tokens"] == []


def test_anlegen_ohne_csrf_403(nutzer: tuple[konten.Nutzer, TestClient]) -> None:
    _n, c = nutzer
    r = c.post(TOKENS, json={"name": "x", "umfang": "lesen", "tage": 30})
    assert r.status_code == 403


def test_widerrufen_ohne_csrf_403(nutzer: tuple[konten.Nutzer, TestClient]) -> None:
    n, c = nutzer
    conn = _conn(c)
    try:
        _roh, info = apitoken.erzeugen(conn, n.id, "t", "lesen")
    finally:
        conn.close()
    assert c.delete(f"{TOKENS}/{info.id}").status_code == 403


def test_anlegen_ungueltig_422(nutzer: tuple[konten.Nutzer, TestClient]) -> None:
    _n, c = nutzer
    r = c.post(TOKENS, headers=csrf(), json={"name": "", "umfang": "lesen", "tage": 30})
    assert r.status_code == 422
    assert isinstance(r.json()["fehler"], str) and r.json()["fehler"]
    r = c.post(TOKENS, headers=csrf(), json={"name": "t", "umfang": "admin", "tage": 30})
    assert r.status_code == 422
    r = c.post(TOKENS, headers=csrf(), json={"name": "t", "umfang": "lesen", "tage": 0})
    assert r.status_code == 422


def test_elfter_token_422(nutzer: tuple[konten.Nutzer, TestClient]) -> None:
    _n, c = nutzer
    for i in range(apitoken.MAX_GUELTIG):
        r = c.post(TOKENS, headers=csrf(), json={"name": f"t{i}", "umfang": "lesen", "tage": 30})
        assert r.status_code == 201
    r = c.post(TOKENS, headers=csrf(), json={"name": "zu viel", "umfang": "lesen", "tage": 30})
    assert r.status_code == 422
    assert len(c.get(TOKENS).json()["tokens"]) == apitoken.MAX_GUELTIG


def test_fremdes_oder_unbekanntes_token_loeschen_404(client: TestClient) -> None:
    _a, ca = person(client, "a@test")
    b, _cb = person(client, "b@test")
    conn = _conn(client)
    try:
        _roh, info = apitoken.erzeugen(conn, b.id, "t", "lesen")
    finally:
        conn.close()
    assert ca.delete(f"{TOKENS}/{info.id}", headers=csrf()).status_code == 404
    assert ca.delete(f"{TOKENS}/999999", headers=csrf()).status_code == 404


def test_token_id_grenzen_422(nutzer: tuple[konten.Nutzer, TestClient]) -> None:
    """Token-ID muss in [1, SQLITE_GANZZAHL_MAX] liegen (wie Admin-Uid-Routen).
    Grenzverletzungen geben 422, nicht 404."""
    _n, c = nutzer
    # tid=0 ist ungültig (ge=1)
    r = c.delete(f"{TOKENS}/0", headers=csrf())
    assert r.status_code == 422
    # tid > SQLITE_GANZZAHL_MAX ist ungültig (le=SQLITE_GANZZAHL_MAX)
    from scanview_server.web import SQLITE_GANZZAHL_MAX
    r = c.delete(f"{TOKENS}/{SQLITE_GANZZAHL_MAX + 1}", headers=csrf())
    assert r.status_code == 422


def test_abgelaufenes_eigenes_token_loeschen_404(client: TestClient) -> None:
    """Nur widerrufbar, was `liste` noch zeigt — ein bereits abgelaufener
    eigener Token ist wie ein fremder/unbekannter (404), nicht mehr eigens widerrufbar."""
    n, c = person(client, "e@test")
    conn = _conn(client)
    try:
        _roh, info = apitoken.erzeugen(conn, n.id, "t", "lesen", 1, now=1000)
    finally:
        conn.close()
    assert c.delete(f"{TOKENS}/{info.id}", headers=csrf()).status_code == 404


def test_bearer_allein_gilt_nicht_auf_konto_pfad(client: TestClient) -> None:
    """E-T3: Bearer zieht auf /api/v1/konto/* nicht, auch mit gültigem Token — es gilt allein das
    Cookie (hier gar keins: ein frischer Client ohne Sitzung bleibt 401)."""
    n, c = person(client, "f@test")
    conn = _conn(client)
    try:
        roh, _info = apitoken.erzeugen(conn, n.id, "t", "lesen")
    finally:
        conn.close()
    r = _anon(c).get(TOKENS, headers={"Authorization": f"Bearer {roh}"})
    assert r.status_code == 401
    # Mit CSRF-Kopfzeilen (die hier ohnehin nicht zählen, Bearer ist kein Konto-Weg): geprüft wird
    # die Anmeldung, nicht die CSRF-Sperre davor (dieselbe Reihenfolge wie
    # test_api_bearer.test_bearer_ausserhalb_erlaubter_pfade).
    r = _anon(c).post(TOKENS, headers={"Authorization": f"Bearer {roh}", **csrf()},
                      json={"name": "x", "umfang": "lesen", "tage": 30})
    assert r.status_code == 401


def test_cookie_gewinnt_gegen_fremden_bearer(client: TestClient) -> None:
    """Auf /api/v1/konto/* zählt allein das Cookie — ein gültiges
    Bearer-Token eines ANDEREN Kontos ändert daran nichts (anders als auf echten Bearer-Pfaden, wo
    das Token-Konto gewinnt; hier ist der Bearer-Header schlicht kein Weg, E-T3)."""
    _a, ca = person(client, "a2@test")
    b, _cb = person(client, "b2@test")
    conn = _conn(client)
    try:
        roh_b, _info_b = apitoken.erzeugen(conn, b.id, "b-token", "schreiben")
    finally:
        conn.close()
    kopf = {"Authorization": f"Bearer {roh_b}"}
    # Liste: A ist leer, obwohl B einen gültigen Token hat — der taucht hier nicht auf.
    assert ca.get(TOKENS, headers=kopf).json()["tokens"] == []
    # Anlegen landet bei A, nicht bei B.
    r = ca.post(TOKENS, headers={**kopf, **csrf()},
                json={"name": "x", "umfang": "lesen", "tage": 30})
    assert r.status_code == 201
    assert len(ca.get(TOKENS).json()["tokens"]) == 1
    conn = _conn(client)
    try:
        b_tokens = apitoken.liste(conn, b.id)
    finally:
        conn.close()
    assert [t.name for t in b_tokens] == ["b-token"]  # unverändert, nur der eine von B


def test_token_wert_nie_im_protokoll(nutzer: tuple[konten.Nutzer, TestClient]) -> None:
    _n, c = nutzer
    r = c.post(TOKENS, headers=csrf(), json={"name": "t", "umfang": "schreiben", "tage": 30})
    roh = r.json()["token"]
    conn = _conn(c)
    try:
        dump = "\n".join(str(tuple(row)) for row in conn.execute("SELECT * FROM ereignisse"))
    finally:
        conn.close()
    assert roh not in dump and roh[3:] not in dump
    assert "token.erstellt" in dump


# --- Git-Namens-Einwilligung (ADR 0011) ----------------------------------------------------------


def test_git_name_lesen_und_setzen(nutzer: tuple[konten.Nutzer, TestClient]) -> None:
    n, c = nutzer
    assert c.get(KONTO).json() == {"gitNameZeigen": False}
    r = c.patch(KONTO, headers=csrf(), json={"gitNameZeigen": True})
    assert r.status_code == 200 and r.json() == {"gitNameZeigen": True}
    assert c.get(KONTO).json() == {"gitNameZeigen": True}
    conn = _conn(c)
    try:
        e = protokoll.lesen(conn)[0]
    finally:
        conn.close()
    assert (e.art, e.kanal, e.nutzer_id, e.objekt_id) == ("konto.git_name", "web", n.id, str(n.id))
    assert e.vorher == {"gitNameZeigen": False}
    assert e.nachher == {"gitNameZeigen": True}
    # Zurückschalten protokolliert ebenfalls, mit vertauschtem vorher/nachher.
    c.patch(KONTO, headers=csrf(), json={"gitNameZeigen": False})
    conn = _conn(c)
    try:
        e2 = protokoll.lesen(conn)[0]
    finally:
        conn.close()
    assert e2.vorher == {"gitNameZeigen": True} and e2.nachher == {"gitNameZeigen": False}


def test_git_name_gleicher_wert_kein_zusaetzliches_ereignis(
    nutzer: tuple[konten.Nutzer, TestClient],
) -> None:
    """Unveränderter Wert schreibt weder UPDATE noch Ereignis,
    die Antwort bleibt aber 200. Zweimal derselbe (ändernde) Wert ergibt insgesamt genau ein
    Ereignis — nicht zwei."""
    _n, c = nutzer

    def git_name_ereignisse(conn: sqlite3.Connection) -> int:
        return sum(1 for e in protokoll.lesen(conn) if e.art == "konto.git_name")

    # Vorgabe ist schon False — derselbe Wert erneut gesendet ändert nichts.
    assert c.patch(KONTO, headers=csrf(), json={"gitNameZeigen": False}).status_code == 200
    conn = _conn(c)
    try:
        assert git_name_ereignisse(conn) == 0
    finally:
        conn.close()

    # Zweimal True hintereinander: nur der erste Aufruf ändert tatsächlich etwas.
    assert c.patch(KONTO, headers=csrf(), json={"gitNameZeigen": True}).status_code == 200
    assert c.patch(KONTO, headers=csrf(), json={"gitNameZeigen": True}).status_code == 200
    conn = _conn(c)
    try:
        assert git_name_ereignisse(conn) == 1
    finally:
        conn.close()


def test_git_name_ohne_csrf_403(nutzer: tuple[konten.Nutzer, TestClient]) -> None:
    _n, c = nutzer
    r = c.patch(KONTO, json={"gitNameZeigen": True})
    assert r.status_code == 403


def test_git_name_ohne_sitzung_401(client: TestClient) -> None:
    assert _anon(client).get(KONTO).status_code == 401


def test_anonymisieren_setzt_git_name_zurueck(nutzer: tuple[konten.Nutzer, TestClient]) -> None:
    n, c = nutzer
    assert c.patch(KONTO, headers=csrf(), json={"gitNameZeigen": True}).status_code == 200
    conn = _conn(c)
    try:
        verwaltung.anonymisieren(conn, n.id, von=None, kanal="cli")
        row = conn.execute(
            "SELECT git_name_zeigen FROM users WHERE id=?", (n.id,),
        ).fetchone()
    finally:
        conn.close()
    assert row["git_name_zeigen"] == 0


# --- Frische Anmeldung (Befund B-TE-M1) ---------------------------------------------------------


def _sitzung_altern(c: TestClient, sekunden: int) -> None:
    """Alle Sitzungen um `sekunden` altern lassen (wie test_passkey._sitzung_altern)."""
    conn = _conn(c)
    try:
        conn.execute(
            "UPDATE sessions SET created_at = created_at - ?, last_seen_at = last_seen_at - ?",
            (sekunden, sekunden),
        )
    finally:
        conn.close()


def test_token_anlegen_braucht_frische_anmeldung(
    nutzer: tuple[konten.Nutzer, TestClient],
) -> None:
    """Ein Token ist ein Zugangsmittel wie ein Passkey: eine Sitzung älter als 10 min legt keins
    an — dieselbe 403 und derselbe Textanfang wie bei „weiteres Gerät hinzufügen“."""
    n, c = nutzer
    _sitzung_altern(c, 11 * 60)
    r = c.post(TOKENS, headers=csrf(), json={"name": "x", "umfang": "lesen", "tage": 30})
    assert r.status_code == 403
    assert r.json()["fehler"] == "Bitte zuerst neu anmelden, um einen API-Zugang anzulegen."
    conn = _conn(c)
    try:
        assert apitoken.liste(conn, n.id) == []
        assert not any(e.art == "token.erstellt" for e in protokoll.lesen(conn))
    finally:
        conn.close()


def test_token_anlegen_unter_zehn_minuten_ok(nutzer: tuple[konten.Nutzer, TestClient]) -> None:
    _n, c = nutzer
    _sitzung_altern(c, 9 * 60)
    r = c.post(TOKENS, headers=csrf(), json={"name": "x", "umfang": "lesen", "tage": 30})
    assert r.status_code == 201


def test_auflisten_und_widerrufen_ohne_frische_anmeldung(
    nutzer: tuple[konten.Nutzer, TestClient],
) -> None:
    n, c = nutzer
    conn = _conn(c)
    try:
        _roh, info = apitoken.erzeugen(conn, n.id, "t", "lesen")
    finally:
        conn.close()
    _sitzung_altern(c, 11 * 60)
    assert c.get(TOKENS).status_code == 200
    assert c.delete(f"{TOKENS}/{info.id}", headers=csrf()).status_code == 204


def test_git_name_einschalten_braucht_frische_anmeldung(
    nutzer: tuple[konten.Nutzer, TestClient],
) -> None:
    _n, c = nutzer
    _sitzung_altern(c, 11 * 60)
    r = c.patch(KONTO, headers=csrf(), json={"gitNameZeigen": True})
    assert r.status_code == 403
    assert r.json()["fehler"] == "Bitte zuerst neu anmelden, um die Einwilligung einzuschalten."
    assert c.get(KONTO).json() == {"gitNameZeigen": False}
    conn = _conn(c)
    try:
        assert not any(e.art == "konto.git_name" for e in protokoll.lesen(conn))
    finally:
        conn.close()


def test_git_name_ausschalten_und_unveraendert_ohne_frische_anmeldung(
    nutzer: tuple[konten.Nutzer, TestClient],
) -> None:
    """Nur das Einschalten (aus → an) verlangt eine frische Anmeldung; Ausschalten, an → an und
    aus → aus gehen mit jeder gültigen Sitzung."""
    _n, c = nutzer
    assert c.patch(KONTO, headers=csrf(), json={"gitNameZeigen": True}).status_code == 200
    _sitzung_altern(c, 11 * 60)
    assert c.patch(KONTO, headers=csrf(), json={"gitNameZeigen": True}).status_code == 200
    assert c.patch(KONTO, headers=csrf(), json={"gitNameZeigen": False}).status_code == 200
    assert c.patch(KONTO, headers=csrf(), json={"gitNameZeigen": False}).status_code == 200
    assert c.get(KONTO).json() == {"gitNameZeigen": False}


@pytest.mark.parametrize("wert", [1, 0, "true", "false", "1"])
def test_git_name_nur_echter_wahrheitswert(nutzer: tuple[konten.Nutzer, TestClient],
                                          wert: object) -> None:
    """Befund B-FUZZ-2 (API-Fuzzing): die Einwilligung gibt den Klarnamen unwiderruflich frei —
    nur ein JSON-Wahrheitswert zählt, keine Zahl oder Zeichenkette (keine stille Umdeutung)."""
    _, c = nutzer
    r = c.patch(KONTO, headers=csrf(), json={"gitNameZeigen": wert})
    assert r.status_code == 422
    assert c.get(KONTO).json() == {"gitNameZeigen": False}


def test_token_fuer_agent_anlegen_und_listen(nutzer: tuple[konten.Nutzer, TestClient]) -> None:
    """G7: `agent` ist optional (Standard false) und nur ein echter JSON-Wahrheitswert."""
    _n, c = nutzer
    r = c.post(KONTO + "/tokens", headers=csrf(),
               json={"name": "Claude", "umfang": "schreiben", "tage": 30, "agent": True})
    assert r.status_code == 201, r.text
    assert r.json()["info"]["agent"] is True
    r = c.post(KONTO + "/tokens", headers=csrf(),
               json={"name": "Skript", "umfang": "schreiben", "tage": 30})
    assert r.status_code == 201 and r.json()["info"]["agent"] is False
    assert sorted(t["agent"] for t in c.get(KONTO + "/tokens").json()["tokens"]) == [False, True]
    for falsch in ("ja", 1, None):
        r = c.post(KONTO + "/tokens", headers=csrf(),
                   json={"name": "x", "umfang": "schreiben", "tage": 30, "agent": falsch})
        assert r.status_code == 422, falsch
