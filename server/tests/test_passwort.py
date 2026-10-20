import threading
import time
from pathlib import Path

import pytest
from conftest import csrf
from fastapi.testclient import TestClient

from scanview_server import db, konten, passwort


def einladung(client: TestClient, **kw: object) -> str:
    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    t = konten.einladung_anlegen(
        conn, email=str(kw.get("email", "t@b.de")), name="Tina",
        grants={"kirche": "betrachter"},
    )
    conn.close()
    return t


def test_regeln() -> None:
    assert passwort.pruefe_regeln("kurz") is not None
    assert passwort.pruefe_regeln("passwort1234") is not None  # häufiges Passwort
    assert passwort.pruefe_regeln("Kirchturm-Kirche-7") is None


def test_regeln_lehnt_nicht_kodierbare_zeichen_ab() -> None:
    """Surrogat-Zeichen (verstümmelte Eingabe) sauber ablehnen statt argon2 crashen zu lassen."""
    assert passwort.pruefe_regeln("kaputt-\udc80-zeichen-lang-genug-12") is not None


def test_stimmt_lehnt_nicht_kodierbares_passwort_ab_statt_zu_crashen() -> None:
    """`stimmt` läuft z. B. beim Anmelden auch mit fremdem Input; ein Surrogat-Zeichen darin darf
    kein UnicodeEncodeError aus argon2 nach außen tragen (dieselbe Fehlerklasse wie
    konten._hash_oder_none bei verstümmelten Cookies)."""
    h = passwort.hash_pw("ein-gueltiges-passwort-123")
    assert passwort.stimmt(h, "kaputt-\udc80-zeichen") is False


def test_einladung_pruefen_und_passwort_setzen(client: TestClient) -> None:
    t = einladung(client)
    r = client.post("/auth/einladung/pruefen", json={"token": t}, headers=csrf())
    assert r.status_code == 200
    assert r.json() == {"name": "Tina", "email": "t@b.de", "zweck": "neu"}
    r = client.post(
        "/auth/einladung/passwort",
        json={"token": t, "passwort": "Kirchturm-Kirche-7"},
        headers=csrf(),
    )
    assert r.status_code == 200
    assert client.get("/api/sitzung").status_code == 200
    r = client.post(
        "/auth/einladung/passwort",
        json={"token": t, "passwort": "Kirchturm-Kirche-7"},
        headers=csrf(),
    )
    assert r.status_code == 404


def test_schwaches_passwort_verbraucht_einladung_nicht(client: TestClient) -> None:
    t = einladung(client)
    r = client.post(
        "/auth/einladung/passwort", json={"token": t, "passwort": "kurz"}, headers=csrf(),
    )
    assert r.status_code == 400
    assert client.post(
        "/auth/einladung/pruefen", json={"token": t}, headers=csrf(),
    ).status_code == 200


def test_zweite_neu_einladung_fuer_bestehendes_konto_ist_404_nicht_500(
    client: TestClient,
) -> None:
    """Zwei offene 'neu'-Einladungen für dieselbe E-Mail: Die erste legt per Passwort-Route das
    Konto an. Löst man danach die zweite ein, darf das nicht mit einem 500er durchfallen
    (sqlite3.IntegrityError o. Ä.), sondern muss die spezifische Meldung aus
    konten.einladung_einloesen als 404 JSON liefern."""
    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    t1 = konten.einladung_anlegen(conn, email="doppelt@b.de", name="D1", grants={})
    t2 = konten.einladung_anlegen(conn, email="Doppelt@b.de", name="D2", grants={})
    conn.close()
    r1 = client.post(
        "/auth/einladung/passwort",
        json={"token": t1, "passwort": "Kirchturm-Kirche-7"},
        headers=csrf(),
    )
    assert r1.status_code == 200
    client.cookies.clear()
    r2 = client.post(
        "/auth/einladung/passwort",
        json={"token": t2, "passwort": "Kirchturm-Kirche-7"},
        headers=csrf(),
    )
    assert r2.status_code == 404
    assert r2.headers["content-type"].startswith("application/json")
    assert "schon ein Konto" in r2.json()["fehler"]
    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    conn.close()


def test_anmelden_und_drosselung(client: TestClient) -> None:
    t = einladung(client)
    client.post(
        "/auth/einladung/passwort",
        json={"token": t, "passwort": "Kirchturm-Kirche-7"},
        headers=csrf(),
    )
    client.cookies.clear()
    falsch = {"email": "t@b.de", "passwort": "falsch-falsch-falsch"}
    for _ in range(5):
        r = client.post("/auth/passwort/anmelden", json=falsch, headers=csrf())
        assert r.status_code == 401
    r = client.post(
        "/auth/passwort/anmelden",
        json={"email": "t@b.de", "passwort": "Kirchturm-Kirche-7"},
        headers=csrf(),
    )
    assert r.status_code == 429
    erwartet = "Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen."  # noqa: RUF001
    assert r.json()["fehler"] == erwartet


def test_unbekannte_email_gleiche_antwort(client: TestClient) -> None:
    r = client.post(
        "/auth/passwort/anmelden",
        json={"email": "x@y.de", "passwort": "irgendwas-langes"},
        headers=csrf(),
    )
    assert r.status_code == 401
    assert r.json()["fehler"] == "E-Mail oder Passwort falsch."


def test_umlaut_email_login_gibt_401_statt_500(client: TestClient) -> None:
    """E-Mails mit Umlauten/IDN sind bei Vermessern real; die Drosselschlüssel-Bildung darf dabei
    nicht auf eine ASCII-strikte Kodierung stoßen (UnicodeEncodeError → 500)."""
    r = client.post(
        "/auth/passwort/anmelden",
        json={"email": "jürgen@müller-vermessung.example", "passwort": "irgendwas-langes"},
        headers=csrf(),
    )
    assert r.status_code == 401
    assert r.json()["fehler"] == "E-Mail oder Passwort falsch."


def test_ohne_csrf_verboten(client: TestClient) -> None:
    r = client.post("/auth/passwort/anmelden", json={"email": "a@b.de", "passwort": "x" * 12})
    assert r.status_code == 403


@pytest.mark.parametrize("body", [{}, {"token": 5}, {"token": "x" * 5000}])
def test_einladung_pruefen_kaputte_eingaben(client: TestClient, body: dict[str, object]) -> None:
    r = client.post("/auth/einladung/pruefen", json=body, headers=csrf())
    assert r.status_code in (404, 422)


def test_ungueltige_eingabe_gibt_422_json_mit_dienstkennung(client: TestClient) -> None:
    r = client.post("/auth/einladung/pruefen", json={}, headers=csrf())
    assert r.status_code == 422
    assert r.json() == {"dienst": "scanview", "fehler": "Eingabe ungültig."}


@pytest.mark.parametrize("feld", ["email", "passwort"])
def test_surrogat_escape_beim_anmelden_kein_500(client: TestClient, feld: str) -> None:
    """Ein per JSON-Escape eingeschleustes Surrogat-Zeichen (`\\udc80`, kein gültiges Unicode)
    darf weder beim sqlite3-Binden (E-Mail) noch bei argon2 (Passwort) crashen. Byte-genau als
    ASCII geschickt, damit httpx/json.dumps das Encoding nicht selbst schon verweigert."""
    werte = {"email": "t@b.de", "passwort": "irgendwas-langes"}
    werte[feld] = "kaputt-\\udc80-" + werte[feld]
    body = "{" + ",".join(f'"{k}":"{v}"' for k, v in werte.items()) + "}"
    r = client.post(
        "/auth/passwort/anmelden", content=body.encode("ascii"),
        headers={**csrf(), "Content-Type": "application/json"},
    )
    assert r.status_code in (401, 422)
    assert r.json()["dienst"] == "scanview"


def test_surrogat_escape_bei_einladung_passwort_kein_500(client: TestClient) -> None:
    body = '{"token":"abcdefghijklmnop","passwort":"kaputt-\\udc80-lang-genug-x"}'
    r = client.post(
        "/auth/einladung/passwort", content=body.encode("ascii"),
        headers={**csrf(), "Content-Type": "application/json"},
    )
    assert r.status_code in (400, 404, 422)
    assert r.json()["dienst"] == "scanview"


def test_zuruecksetzen_per_passwort_loescht_alte_sperre(client: TestClient) -> None:
    """Ein Konto ist nach 5 Fehlversuchen gesperrt. Ein Admin schickt eine
    `zuruecksetzen`-Einladung; nach deren Einlösung per neuem Passwort darf die alte Sperre die
    Anmeldung mit dem NEUEN Passwort nicht mehr blockieren (429)."""
    t = einladung(client)
    client.post(
        "/auth/einladung/passwort",
        json={"token": t, "passwort": "Kirchturm-Kirche-7"},
        headers=csrf(),
    )
    client.cookies.clear()
    falsch = {"email": "t@b.de", "passwort": "falsch-falsch-falsch"}
    for _ in range(passwort.FEHLVERSUCHE):
        r = client.post("/auth/passwort/anmelden", json=falsch, headers=csrf())
        assert r.status_code == 401
    assert client.post(
        "/auth/passwort/anmelden",
        json={"email": "t@b.de", "passwort": "Kirchturm-Kirche-7"},
        headers=csrf(),
    ).status_code == 429

    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    t2 = konten.einladung_anlegen(
        conn, email="t@b.de", name="Tina", grants={}, purpose="zuruecksetzen",
    )
    conn.close()
    r = client.post(
        "/auth/einladung/passwort",
        json={"token": t2, "passwort": "Neues-Passwort-99"},
        headers=csrf(),
    )
    assert r.status_code == 200
    client.cookies.clear()
    r = client.post(
        "/auth/passwort/anmelden",
        json={"email": "t@b.de", "passwort": "Neues-Passwort-99"},
        headers=csrf(),
    )
    assert r.status_code == 200


def test_drosselung_zaehler_sperrt_15_minuten_und_erfolg_loescht(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "d.db")
    db.migrate(conn)
    key = passwort.schluessel("a@b.de")
    for _ in range(passwort.FEHLVERSUCHE):
        assert passwort.reservieren(conn, key, now=1000) is True
    # Nach der fünften Reservierung ist gesperrt — die sechste bekommt keine Prüfung mehr.
    assert passwort.reservieren(conn, key, now=1001) is False
    assert passwort.gesperrt(conn, key, now=1000 + passwort.SPERRE_S - 1) is True
    assert passwort.gesperrt(conn, key, now=1000 + passwort.SPERRE_S) is False
    passwort.erfolg(conn, key)
    assert passwort.gesperrt(conn, key, now=1000) is False
    conn.close()


class _ZaehlenderHasher:
    """Ersatz für argon2.PasswordHasher: zählt, wie viele Aufrufe gleichzeitig laufen."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jetzt = 0
        self.hoechst = 0

    def _arbeit(self) -> None:
        with self.lock:
            self.jetzt += 1
            self.hoechst = max(self.hoechst, self.jetzt)
        time.sleep(0.05)
        with self.lock:
            self.jetzt -= 1

    def hash(self, _pw: str) -> str:
        self._arbeit()
        return "h"

    def verify(self, _h: str, _pw: str) -> bool:
        self._arbeit()
        return True


def test_argon2_laeuft_hoechstens_zweimal_gleichzeitig(monkeypatch: pytest.MonkeyPatch) -> None:
    """Speicher-DoS-Schutz: 64 MiB je argon2-Aufruf, 40 Threads, MemoryMax=512M — mehr als zwei
    gleichzeitige Rechnungen darf es nicht geben (hash_pw, stimmt und dummy_pruefen gemischt)."""
    fake = _ZaehlenderHasher()
    monkeypatch.setattr(passwort, "_ph", fake)
    aufrufe = [
        lambda: passwort.hash_pw("x"), lambda: passwort.stimmt("h", "x"),
        lambda: passwort.dummy_pruefen("x"),
    ] * 4
    threads = [threading.Thread(target=f) for f in aufrufe]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # == 2, nicht nur <= 2: eine komplett serialisierte Umsetzung fiele sonst nicht auf.
    assert fake.hoechst == passwort.ARGON2_PARALLEL == 2


def _alle_plaetze_belegt(monkeypatch: pytest.MonkeyPatch) -> None:
    voll = threading.BoundedSemaphore(1)
    voll.acquire()
    monkeypatch.setattr(passwort, "_argon2_plaetze", voll)
    monkeypatch.setattr(passwort, "ARGON2_WARTEN_S", 0.01)


def test_ueberlastet_anmelden_ist_429_ohne_fehlversuch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _alle_plaetze_belegt(monkeypatch)
    r = client.post(
        "/auth/passwort/anmelden", json={"email": "x@b.de", "passwort": "egal-egal-egal"},
        headers=csrf(),
    )
    assert r.status_code == 429
    assert r.json() == {
        "dienst": "scanview",
        "fehler": "Gerade zu viele Anmeldeversuche – bitte kurz warten.",  # noqa: RUF001
    }
    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    assert conn.execute("SELECT COUNT(*) FROM throttle").fetchone()[0] == 0
    conn.close()


def test_ueberlastet_einladung_ist_429_und_verbraucht_nichts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = einladung(client)
    _alle_plaetze_belegt(monkeypatch)
    r = client.post(
        "/auth/einladung/passwort", json={"token": t, "passwort": "Kirchturm-Kirche-7"},
        headers=csrf(),
    )
    assert r.status_code == 429
    r = client.post("/auth/einladung/pruefen", json={"token": t}, headers=csrf())
    assert r.status_code == 200


def test_ungueltige_einladung_hasht_nicht(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Erst die Einladung prüfen, dann hashen: ein falscher Token darf keine argon2-Rechnung
    auslösen."""
    aufrufe: list[str] = []
    monkeypatch.setattr(passwort, "hash_pw", lambda pw: aufrufe.append(pw) or "h")
    r = client.post(
        "/auth/einladung/passwort",
        json={"token": "gibt-es-nicht-0123456789", "passwort": "Kirchturm-Kirche-7"},
        headers=csrf(),
    )
    assert r.status_code == 404
    assert aufrufe == []


# --- Browser-Cache beim Wechsel der Sitzung (Restliste 1.0: Cache eingeloggter Seiten) -------
# Paketdateien liegen bis zu einer Stunde privat im Browser-Cache (Caddy, /data). Ohne Leeren
# könnte nach dem Abmelden eine andere Person im selben Browser sie ohne Rechteprüfung laden.

def test_sitzungswechsel_leert_den_browser_cache(client: TestClient) -> None:
    t = einladung(client)
    r = client.post("/auth/einladung/passwort",
                    json={"token": t, "passwort": "Kirchturm-Kirche-7"}, headers=csrf())
    assert r.status_code == 200, r.text
    assert r.headers.get("clear-site-data") == '"cache"'
    r = client.post("/auth/abmelden", headers=csrf())
    assert r.status_code == 200
    assert r.headers.get("clear-site-data") == '"cache"'
    r = client.post("/auth/passwort/anmelden",
                    json={"email": "t@b.de", "passwort": "Kirchturm-Kirche-7"}, headers=csrf())
    assert r.status_code == 200
    # Nur der Cache: Cookies (die neue Sitzung) und Speicher (Entwürfe im Viewer) bleiben.
    assert r.headers.get("clear-site-data") == '"cache"'


def test_fehlversuch_leert_nichts(client: TestClient) -> None:
    r = client.post("/auth/passwort/anmelden",
                    json={"email": "x@y.de", "passwort": "irgendwas-langes"}, headers=csrf())
    assert r.status_code == 401 and "clear-site-data" not in r.headers
