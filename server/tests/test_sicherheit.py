"""Security-Audits vom 27.09.2026.

PoC-Tests für die Verfügbarkeitsbefunde: Sie feuern echte parallele Anfragen gegen die App
(TestClient, jede Anfrage aus einem eigenen Thread — die App läuft in EINER Event-Loop mit dem
Standard-Thread-Pool von AnyIO wie unter uvicorn). argon2 ist durch einen zählenden, langsamen
Ersatz vertauscht, damit die Tests schnell sind und genau messen, wie oft geprüft wurde.
"""
from __future__ import annotations

import logging
import os
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from argon2.exceptions import VerifyMismatchError
from conftest import ORIGIN, csrf
from fastapi.testclient import TestClient
from pakete import lege_paket
from test_sitzung import anmelden

from scanview_server import db, katalog, konten, passkey, passwort
from scanview_server.config import Settings
from scanview_server.web import create_app


class _LangsamerHasher:
    """Ersatz für argon2.PasswordHasher: jede Prüfung dauert `dauer` s und schlägt fehl."""

    def __init__(self, dauer: float) -> None:
        self.dauer = dauer
        self.lock = threading.Lock()
        self.pruefungen = 0

    def hash(self, _pw: str) -> str:
        time.sleep(self.dauer)
        return "h"

    def verify(self, _h: str, _pw: str) -> bool:
        with self.lock:
            self.pruefungen += 1
        time.sleep(self.dauer)
        raise VerifyMismatchError


def _konto_mit_passwort(settings: Settings, email: str, grants: dict[str, str]) -> None:
    conn = db.connect(settings.db_path)
    t = konten.einladung_anlegen(conn, email=email, name="Opfer", grants=grants)
    h = passwort._ph.hash("Kirchturm-Kirche-7")

    def speichern(c: object, uid: int) -> None:
        conn.execute(
            "INSERT INTO passwords (user_id, hash, updated_at) VALUES (?,?,0)", (uid, h),
        )

    konten.einladung_einloesen(conn, t, speichern)  # type: ignore[arg-type]
    conn.close()


def test_m1_parallele_fehlversuche_hoechstens_fuenf_pruefungen(
    settings: Settings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WB-M1: 60 parallele Fehlversuche gegen EIN Konto → höchstens 5 argon2-Prüfungen.
    Vorher (a546418): 44 Prüfungen, weil Sperrprüfung und Zählen nicht atomar waren."""
    app = create_app(settings)
    _konto_mit_passwort(settings, "opfer@example.org", {})
    fake = _LangsamerHasher(0.05)
    monkeypatch.setattr(passwort, "_ph", fake)
    with TestClient(app, base_url=ORIGIN) as c:
        def versuch(i: int) -> int:
            return c.post(
                "/auth/passwort/anmelden", headers=csrf(),
                json={"email": "opfer@example.org", "passwort": f"falsch-falsch-{i}"},
            ).status_code

        with ThreadPoolExecutor(60) as ex:
            codes = Counter(ex.map(versuch, range(60)))
    assert fake.pruefungen <= passwort.FEHLVERSUCHE
    assert codes[401] == fake.pruefungen
    assert codes[401] + codes[429] == 60


def _freier_port() -> int:
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return int(so.getsockname()[1])


def test_m3_anmeldeflut_bremst_authz_nicht(tmp_path: Path) -> None:
    """WB-M3 wie im Audit-PoC: echter uvicorn-Prozess mit echtem argon2, 60 Clients schicken
    ununterbrochen falsche Passwörter; die Latenz von /api/authz bleibt niedrig, weil argon2
    nicht mehr im gemeinsamen Thread-Pool wartet. Vorher (a546418): Median 6 ms → 364 ms."""
    data, state = tmp_path / "data", tmp_path / "state"
    data.mkdir()
    state.mkdir()
    lege_paket(data, "p1")
    port = _freier_port()
    basis = f"http://127.0.0.1:{port}"
    env = {
        **os.environ, "SCANVIEW_DATA": str(data), "SCANVIEW_STATE": str(state),
        "SCANVIEW_ORIGIN": ORIGIN, "SCANVIEW_RP_ID": "scanview.test",
        # Globale Bremse aus: hier zählt nur der Thread-Pool.
        "SCANVIEW_ANMELDE_FEHLER_PRO_MINUTE": "1000000", "SCANVIEW_LOG_LEVEL": "WARNING",
    }
    dienst = subprocess.Popen(
        [sys.executable, "-c", "import scanview_server.cli as c; c.main(['serve', '--port', "
         f"'{port}'])"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ende = threading.Event()
    try:
        for _ in range(100):
            try:
                if httpx.get(basis + "/api/gesundheit").status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.1)
        conn = db.connect(state / "scanview.db")
        t = konten.einladung_anlegen(conn, email="n@b.de", name="N", grants={"p1": "betrachter"})
        n = konten.einladung_einloesen(conn, t, lambda _c, _u: None)
        cookie = konten.sitzung_anlegen(conn, n.id)
        conn.close()
        kopf = {"X-Forwarded-Uri": "/data/p1/cloud.copc.laz", "X-Forwarded-Method": "GET",
                "Cookie": f"__Host-scanview={cookie}"}

        def authz(c: httpx.Client) -> float:
            t0 = time.perf_counter()
            assert c.get(basis + "/api/authz", headers=kopf).status_code == 200
            return (time.perf_counter() - t0) * 1000

        with httpx.Client() as c:
            basis_ms = statistics.median(authz(c) for _ in range(20))
        codes: Counter[int] = Counter()

        def flut(i: int) -> None:
            with httpx.Client(timeout=30) as c:
                while not ende.is_set():
                    s = c.post(
                        basis + "/auth/passwort/anmelden", headers=csrf(),
                        json={"email": f"x{i}@b.de", "passwort": "falsch-falsch-falsch"},
                    ).status_code
                    codes[s] += 1
                    if s == 429:
                        # Abgewiesene Clients warten kurz: ohne das misst der Test eine reine
                        # HTTP-Flut (tausende sofortige 429/s), die jede Route einer einzelnen
                        # Event-Loop gleich trifft — dagegen helfen fail2ban/Caddy, nicht der Pool.
                        time.sleep(0.2)

        with ThreadPoolExecutor(60) as ex:
            for i in range(60):
                ex.submit(flut, i)
            time.sleep(2)
            with httpx.Client() as c:
                flut_ms = [authz(c) for _ in range(20)]
            ende.set()
    finally:
        ende.set()
        dienst.terminate()
        dienst.wait(10)
    print(f"authz Median ohne Flut {basis_ms:.1f} ms, mit Flut {statistics.median(flut_ms):.1f} ms,"
          f" Anmeldungen {dict(codes)}")
    assert codes[401] > 0  # die Flut hat tatsächlich argon2 ausgelöst
    assert statistics.median(flut_ms) < 50, flut_ms


# --- WB-M2 / BB-N5: globale Bremse und fail2ban-Zeile -------------------------------------------

def _app(tmp_path: Path, **kw: object) -> TestClient:
    data, state = tmp_path / "data", tmp_path / "state"
    data.mkdir(exist_ok=True)
    state.mkdir(exist_ok=True)
    s = Settings(data_dir=data, state_dir=state, origin=ORIGIN, rp_id="scanview.test",
                 rp_name="scanview", **kw)  # type: ignore[arg-type]
    return TestClient(create_app(s), base_url=ORIGIN)


def test_m2_globale_bremse_sperrt_alle_passwort_anmeldungen(tmp_path: Path) -> None:
    """Password-Spraying: je Konto nur ein Versuch (die Sperre je Konto greift nie), aber nach
    `anmelde_fehler_pro_minute` Fehlversuchen insgesamt bekommt jede Passwort-Anmeldung 429 —
    auch eine mit richtigem Passwort."""
    with _app(tmp_path, anmelde_fehler_pro_minute=3) as c:
        _konto_mit_passwort(c.app.state.settings, "echt@b.de", {})  # type: ignore[attr-defined]
        for i in range(3):
            r = c.post("/auth/passwort/anmelden", headers=csrf(),
                       json={"email": f"spray{i}@b.de", "passwort": "Sommer-2026-!!"})
            assert r.status_code == 401
        r = c.post("/auth/passwort/anmelden", headers=csrf(),
                   json={"email": "echt@b.de", "passwort": "Kirchturm-Kirche-7"})
        assert r.status_code == 429
        assert r.json()["fehler"] == "Gerade zu viele Anmeldeversuche – bitte kurz warten."  # noqa: RUF001


def test_m2_bremse_gleitendes_fenster() -> None:
    jetzt = [0.0]
    b = passwort.Anmeldebremse(3, fenster_s=60, uhr=lambda: jetzt[0])
    for t in (0.0, 10.0, 20.0):
        jetzt[0] = t
        assert b.gebremst() is False
        b.fehlschlag()
    assert b.gebremst() is True
    jetzt[0] = 59.9
    assert b.gebremst() is True
    jetzt[0] = 60.0  # der erste Fehlversuch fällt aus dem Fenster
    assert b.gebremst() is False


def test_m2_bremse_standard_und_umgebungsvariable(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in {"SCANVIEW_DATA": "/d", "SCANVIEW_STATE": "/s", "SCANVIEW_ORIGIN": ORIGIN,
                 "SCANVIEW_RP_ID": "x"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SCANVIEW_ANMELDE_FEHLER_PRO_MINUTE", raising=False)
    assert Settings.from_env().anmelde_fehler_pro_minute == 30
    monkeypatch.setenv("SCANVIEW_ANMELDE_FEHLER_PRO_MINUTE", "100")
    assert Settings.from_env().anmelde_fehler_pro_minute == 100
    for falsch in ("0", "-1", "viele"):
        monkeypatch.setenv("SCANVIEW_ANMELDE_FEHLER_PRO_MINUTE", falsch)
        with pytest.raises(SystemExit):
            Settings.from_env()


FILTER = Path(__file__).parents[2] / "deploy" / "platform" / "fail2ban" / "scanview.conf"


def _failregex() -> re.Pattern[str]:
    zeile = next(z for z in FILTER.read_text(encoding="utf-8").splitlines()
                 if z.startswith("failregex"))
    muster = zeile.split("=", 1)[1].strip()
    # fail2ban ersetzt <HOST> durch seine Adress-Gruppe; hier reicht eine einfache Gruppe.
    return re.compile(muster.replace("<HOST>", r"(?P<host>[0-9a-fA-F:.]+)"))


@pytest.mark.parametrize(("ip", "erwartet"), [
    ("203.0.113.12", "203.0.113.12"),
    ("2a02:2455::1", "2a02:2455::1"),
    ("1.2.3.4\nWARNING gefälscht", "unbekannt"),  # Log-Injection über den Header
    (None, "unbekannt"),
])
def test_m2_fehlversuch_zeile_mit_ip_passt_zum_fail2ban_filter(
    caplog: pytest.LogCaptureFixture, ip: str | None, erwartet: str,
) -> None:
    caplog.set_level(logging.WARNING, logger="scanview.anmeldung")
    passwort.fehlversuch_melden(passwort.schluessel("a@b.de"), ip)
    (rec,) = caplog.records
    # Wie im Journal: Format aus cli._konfiguriere_logging.
    zeile = f"{rec.levelname} {rec.name}: {rec.getMessage()}"
    assert zeile.endswith(f"von {erwartet}")
    m = _failregex().search(zeile)
    if erwartet == "unbekannt":
        assert m is None  # nichts zu sperren
    else:
        assert m is not None and m["host"] == erwartet


def test_m2_ip_nie_in_der_db(tmp_path: Path) -> None:
    with _app(tmp_path) as c:
        c.post("/auth/passwort/anmelden", headers={**csrf(), "X-Forwarded-For": "9.9.9.9"},
               json={"email": "x@b.de", "passwort": "falsch-falsch-falsch"})
        roh = (c.app.state.settings.db_path).read_bytes()  # type: ignore[attr-defined]
        wal = c.app.state.settings.db_path.with_name("scanview.db-wal")  # type: ignore[attr-defined]
        if wal.exists():
            roh += wal.read_bytes()
    assert b"9.9.9.9" not in roh and b"testclient" not in roh


def test_m3_warteschlange_voll_gibt_429_statt_zu_warten(
    settings: Settings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mehr gleichzeitige Passwort-Anmeldungen als Plätze + Warteschlange → sofort 429, nie 500,
    und nie mehr als ARGON2_PARALLEL gleichzeitige Prüfungen."""
    fake = _LangsamerHasher(0.2)
    monkeypatch.setattr(passwort, "_ph", fake)
    app = create_app(settings)
    with TestClient(app, base_url=ORIGIN) as c:
        def versuch(i: int) -> int:
            return c.post("/auth/passwort/anmelden", headers=csrf(),
                          json={"email": f"x{i}@b.de", "passwort": "falsch-falsch-1"}).status_code

        with ThreadPoolExecutor(40) as ex:
            codes = Counter(ex.map(versuch, range(40)))
    assert set(codes) <= {401, 429}
    assert codes[429] > 0 and codes[401] > 0


# --- WB-N1 / BB-I5: anonyme Challenges ---------------------------------------------------------

def test_n1_obergrenze_anonymer_challenges(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(passkey, "MAX_ANONYM", 3)
    for _ in range(3):
        assert client.post("/auth/passkey/optionen", headers=csrf()).status_code == 200
    r = client.post("/auth/passkey/optionen", headers=csrf())
    assert r.status_code == 429 and r.json()["dienst"] == "scanview"
    # Challenges mit Einladung zählen nicht mit und bleiben möglich.
    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    t = konten.einladung_anlegen(conn, email="neu@b.de", name="Neu", grants={})
    conn.close()
    r = client.post("/auth/einladung/passkey/optionen", json={"token": t}, headers=csrf())
    assert r.status_code == 200


def test_n1_abgelaufene_challenges_zaehlen_nicht(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "c.db")
    db.migrate(conn)
    alt = passkey.MAX_ANONYM
    try:
        passkey.MAX_ANONYM = 2
        passkey._challenge_ablegen(conn, b"c", "anmelden", None, None, now=1000)
        passkey._challenge_ablegen(conn, b"c", "anmelden", None, None, now=1000)
        with pytest.raises(passkey.ZuVieleChallenges):
            passkey._challenge_ablegen(conn, b"c", "anmelden", None, None, now=1000)
        # 5 min später sind beide abgelaufen und werden beim nächsten Aufruf gelöscht.
        passkey._challenge_ablegen(conn, b"c", "anmelden", None, None,
                                   now=1001 + passkey.CHALLENGE_S)
    finally:
        passkey.MAX_ANONYM = alt
    assert conn.execute("SELECT COUNT(*) FROM challenges").fetchone()[0] == 1
    conn.close()


# --- WB-N4: RecursionError im Katalog ----------------------------------------------------------

def test_n4_tief_verschachtelte_project_json_stoppt_den_start_nicht(
    settings: Settings, caplog: pytest.LogCaptureFixture,
) -> None:
    lege_paket(settings.data_dir, "gut")
    boese = settings.data_dir / "boese"
    boese.mkdir()
    (boese / "project.json").write_text("[" * 100_000, encoding="utf-8")
    app = create_app(settings)  # vorher: RecursionError → Dienst in der Restart-Schleife
    assert app.state.katalog.get("gut") is not None
    assert app.state.katalog.get("boese") is None
    assert any("boese" in r.getMessage() and "RecursionError" in r.getMessage()
               for r in caplog.records)


def test_n4_zu_grosse_project_json_wird_nicht_gelesen(tmp_path: Path) -> None:
    d = lege_paket(tmp_path, "riesig")
    with (d / "project.json").open("ab") as f:
        f.write(b" " * (katalog.MAX_PROJECT_JSON + 1))
    k = katalog.Katalog(tmp_path)
    k.laden()
    assert k.get("riesig") is None


# --- WB-N5: nur bekannte Paket-Dateiarten -------------------------------------------------------

@pytest.mark.parametrize(("pfad", "status"), [
    ("/data/p1/cloud.copc.laz", 200),
    ("/data/p1/project.json", 200),
    ("/data/p1/panos/S001/vorne.jpg", 200),
    ("/data/p1/panos/S001/vorne.JPG", 200),
    ("/data/p1/layers/mesh/model.glb", 200),
    ("/data/p1/layers/mesh/tiles/0/0.b3dm", 200),
    ("/data/p1/x.html", 404),
    ("/data/p1/x.htm", 404),
    ("/data/p1/x.svg", 404),
    ("/data/p1/x.js", 404),
    ("/data/p1/x.json.html", 404),
    ("/data/p1/ohne-endung", 404),
])
def test_n5_authz_nur_paket_endungen(client: TestClient, pfad: str, status: int) -> None:
    lege_paket(client.app.state.settings.data_dir, "p1")  # type: ignore[attr-defined]
    client.app.state.katalog.laden()  # type: ignore[attr-defined]
    anmelden(client, grants={"p1": "betrachter"})
    r = client.get("/api/authz", headers={"X-Forwarded-Uri": pfad, "X-Forwarded-Method": "GET"})
    assert r.status_code == status


# --- WB-N8: Sitzungen je Konto ------------------------------------------------------------------

def test_n8_hoechstens_zehn_sitzungen_die_aelteste_faellt_weg(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    t = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={})
    uid = konten.einladung_einloesen(conn, t, lambda _c, _u: None).id
    tokens = [konten.sitzung_anlegen(conn, uid, now=1000 + i) for i in range(12)]
    n = conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (uid,)).fetchone()[0]
    assert n == konten.MAX_SITZUNGEN == 10
    assert konten.sitzung_nutzer(conn, tokens[0], now=2000) is None
    assert konten.sitzung_nutzer(conn, tokens[1], now=2000) is None
    assert konten.sitzung_nutzer(conn, tokens[2], now=2000) is not None
    assert konten.sitzung_nutzer(conn, tokens[11], now=2000) is not None
    conn.close()


def test_n8_neue_anmeldung_beendet_das_alte_cookie_dieses_browsers(settings: Settings) -> None:
    app = create_app(settings)
    _konto_mit_passwort(settings, "a@b.de", {})
    with TestClient(app, base_url=ORIGIN) as c:
        gut = {"email": "a@b.de", "passwort": "Kirchturm-Kirche-7"}
        assert c.post("/auth/passwort/anmelden", json=gut, headers=csrf()).status_code == 200
        alt = c.cookies["__Host-scanview"]
        assert c.post("/auth/passwort/anmelden", json=gut, headers=csrf()).status_code == 200
        assert c.cookies["__Host-scanview"] != alt
    conn = db.connect(settings.db_path)
    assert konten.sitzung_nutzer(conn, alt) is None
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    conn.close()


# --- BB-I1 / WB-I2: Cache-Control no-store -----------------------------------------------------

def test_i1_api_und_auth_antworten_no_store(client: TestClient) -> None:
    for r in (
        client.get("/api/gesundheit"),
        client.get("/api/sitzung"),  # 401
        client.get("/api/projekte"),  # 401
        client.get("/api/authz", headers={"X-Forwarded-Method": "GET"}),
        client.post("/auth/passwort/anmelden", headers=csrf(),
                    json={"email": "x@b.de", "passwort": "falsch-falsch-1"}),
        client.post("/auth/abmelden"),  # 403 CSRF
        client.get("/api/gibt-es-nicht"),  # 404
    ):
        assert r.headers.get("cache-control") == "no-store", (r.url, r.status_code)
