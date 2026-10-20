from dataclasses import dataclass
from typing import Any

import pytest
from conftest import csrf
from fastapi.testclient import TestClient
from test_passwort import einladung
from test_sitzung import anmelden

from scanview_server import db, konten, passkey, passwort
from scanview_server.web import COOKIE


@dataclass
class FakeReg:
    credential_id: bytes = b"cred-1"
    credential_public_key: bytes = b"pk"
    sign_count: int = 0


@dataclass
class FakeAuth:
    credential_id: bytes = b"cred-1"
    new_sign_count: int = 1


@pytest.fixture
def fake_webauthn(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    def reg(**kw: Any) -> FakeReg:
        calls["reg"] = kw
        return FakeReg()

    def auth(**kw: Any) -> FakeAuth:
        calls["auth"] = kw
        return FakeAuth()

    monkeypatch.setattr(passkey, "verify_registration_response", reg)
    monkeypatch.setattr(passkey, "verify_authentication_response", auth)
    return calls


def test_optionen_verlangen_user_verification(client: TestClient) -> None:
    t = einladung(client)
    r = client.post("/auth/einladung/passkey/optionen", json={"token": t}, headers=csrf())
    assert r.status_code == 200
    o = r.json()["optionen"]
    assert o["rp"]["id"] == "scanview.test"
    assert o["authenticatorSelection"]["userVerification"] == "required"
    assert o["authenticatorSelection"]["residentKey"] == "preferred"


def test_registrieren_und_anmelden(client: TestClient, fake_webauthn: dict[str, Any]) -> None:
    t = einladung(client)
    o = client.post("/auth/einladung/passkey/optionen", json={"token": t}, headers=csrf()).json()
    r = client.post(
        "/auth/einladung/passkey/abschluss",
        json={"token": t, "challengeId": o["challengeId"], "credential": {"id": "x"}},
        headers=csrf(),
    )
    assert r.status_code == 200
    kw = fake_webauthn["reg"]
    assert kw["expected_origin"] == "https://scanview.test" and kw["require_user_verification"]
    client.cookies.clear()
    a = client.post("/auth/passkey/optionen", json={}, headers=csrf()).json()
    cred = {"id": "Y3JlZC0x", "rawId": "Y3JlZC0x"}
    r = client.post(
        "/auth/passkey/anmelden",
        json={"challengeId": a["challengeId"], "credential": cred},
        headers=csrf(),
    )
    assert r.status_code == 200
    assert client.get("/api/sitzung").status_code == 200

    # An verify_authentication_response übergebene Argumente prüfen.
    auth_kw = fake_webauthn["auth"]
    assert auth_kw["expected_rp_id"] == "scanview.test"
    assert auth_kw["expected_origin"] == "https://scanview.test"
    assert auth_kw["require_user_verification"] is True
    assert auth_kw["credential_current_sign_count"] == 0

    # sign_count/last_used_at müssen nach der Anmeldung aktualisiert sein.
    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    row = conn.execute(
        "SELECT sign_count, last_used_at FROM passkeys WHERE id=?", ("Y3JlZC0x",),
    ).fetchone()
    conn.close()
    assert row["sign_count"] == 1
    assert row["last_used_at"] is not None


def test_anmelde_challenge_nur_einmal(client: TestClient, fake_webauthn: dict[str, Any]) -> None:
    """Dieselbe Anmelde-challengeId zweimal verwendet: das zweite Mal ist die Challenge schon
    gelöscht (siehe passkey._challenge_holen, DELETE ... RETURNING) und schlägt mit 401 fehl."""
    t = einladung(client)
    o = client.post("/auth/einladung/passkey/optionen", json={"token": t}, headers=csrf()).json()
    body = {"token": t, "challengeId": o["challengeId"], "credential": {"id": "x"}}
    r = client.post("/auth/einladung/passkey/abschluss", json=body, headers=csrf())
    assert r.status_code == 200
    client.cookies.clear()

    a = client.post("/auth/passkey/optionen", json={}, headers=csrf()).json()
    cred = {"id": "Y3JlZC0x", "rawId": "Y3JlZC0x"}
    anmelde_body = {"challengeId": a["challengeId"], "credential": cred}
    r1 = client.post("/auth/passkey/anmelden", json=anmelde_body, headers=csrf())
    assert r1.status_code == 200
    client.cookies.clear()
    r2 = client.post("/auth/passkey/anmelden", json=anmelde_body, headers=csrf())
    assert r2.status_code == 401


def test_hinzufuegen_challenge_nur_einmal(
    client: TestClient, fake_webauthn: dict[str, Any],
) -> None:
    """Dieselbe hinzufuegen-challengeId zweimal verwendet: das zweite Mal ist die Challenge schon
    weg und die Antwort ist 400."""
    anmelden(client)
    o = client.post("/auth/passkey/hinzufuegen/optionen", json={}, headers=csrf()).json()
    body = {"challengeId": o["challengeId"], "credential": {"id": "y"}}
    r1 = client.post("/auth/passkey/hinzufuegen/abschluss", json=body, headers=csrf())
    assert r1.status_code == 200
    r2 = client.post("/auth/passkey/hinzufuegen/abschluss", json=body, headers=csrf())
    assert r2.status_code == 400


def test_verifikationsfehler_verbraucht_challenge_auch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nach einem fehlgeschlagenen Verify ist nicht nur die Einladung unverbraucht (siehe
    test_verifikationsfehler_verbraucht_einladung_nicht), sondern auch die Challenge selbst weg:
    ein zweiter Versuch mit demselben challengeId scheitert auch dann, wenn die Verifikation beim
    zweiten Mal (fake) funktionieren würde."""
    def boom(**_kw: Any) -> None:
        raise ValueError("Signatur ungültig")

    monkeypatch.setattr(passkey, "verify_registration_response", boom)
    t = einladung(client)
    o = client.post("/auth/einladung/passkey/optionen", json={"token": t}, headers=csrf()).json()
    body = {"token": t, "challengeId": o["challengeId"], "credential": {"id": "x"}}
    r1 = client.post("/auth/einladung/passkey/abschluss", json=body, headers=csrf())
    assert r1.status_code == 400

    monkeypatch.setattr(passkey, "verify_registration_response", lambda **_kw: FakeReg())
    r2 = client.post("/auth/einladung/passkey/abschluss", json=body, headers=csrf())
    assert r2.status_code == 400


def test_challenge_nur_einmal(client: TestClient, fake_webauthn: dict[str, Any]) -> None:
    t = einladung(client)
    o = client.post("/auth/einladung/passkey/optionen", json={"token": t}, headers=csrf()).json()
    body = {"token": t, "challengeId": o["challengeId"], "credential": {"id": "x"}}
    r1 = client.post("/auth/einladung/passkey/abschluss", json=body, headers=csrf())
    assert r1.status_code == 200
    t2 = einladung(client, email="z@b.de")
    body2 = {"token": t2, "challengeId": o["challengeId"], "credential": {"id": "x"}}
    r = client.post("/auth/einladung/passkey/abschluss", json=body2, headers=csrf())
    assert r.status_code == 400


def test_challenge_einer_anderen_einladung_passt_nicht(
    client: TestClient, fake_webauthn: dict[str, Any],
) -> None:
    t1, t2 = einladung(client), einladung(client, email="z@b.de")
    o1 = client.post("/auth/einladung/passkey/optionen", json={"token": t1}, headers=csrf()).json()
    body = {"token": t2, "challengeId": o1["challengeId"], "credential": {"id": "x"}}
    r = client.post("/auth/einladung/passkey/abschluss", json=body, headers=csrf())
    assert r.status_code == 400


def test_verifikationsfehler_verbraucht_einladung_nicht(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(**_kw: Any) -> None:
        raise ValueError("Signatur ungültig")

    monkeypatch.setattr(passkey, "verify_registration_response", boom)
    t = einladung(client)
    o = client.post("/auth/einladung/passkey/optionen", json={"token": t}, headers=csrf()).json()
    r = client.post(
        "/auth/einladung/passkey/abschluss",
        json={"token": t, "challengeId": o["challengeId"], "credential": {"id": "x"}},
        headers=csrf(),
    )
    assert r.status_code == 400
    r2 = client.post("/auth/einladung/pruefen", json={"token": t}, headers=csrf())
    assert r2.status_code == 200


def test_unbekannter_passkey(client: TestClient, fake_webauthn: dict[str, Any]) -> None:
    a = client.post("/auth/passkey/optionen", json={}, headers=csrf()).json()
    cred = {"id": "dW5iZWthbm50", "rawId": "dW5iZWthbm50"}
    r = client.post(
        "/auth/passkey/anmelden",
        json={"challengeId": a["challengeId"], "credential": cred},
        headers=csrf(),
    )
    assert r.status_code == 401


def test_challenge_abgelaufen_ist_400(client: TestClient, fake_webauthn: dict[str, Any]) -> None:
    """5-Minuten-Regel: eine abgelaufene Challenge wird gelöscht und schlägt fehl statt geprüft
    zu werden."""
    t = einladung(client)
    o = client.post("/auth/einladung/passkey/optionen", json={"token": t}, headers=csrf()).json()
    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    conn.execute("UPDATE challenges SET expires_at=0 WHERE id=?", (o["challengeId"],))
    conn.close()
    r = client.post(
        "/auth/einladung/passkey/abschluss",
        json={"token": t, "challengeId": o["challengeId"], "credential": {"id": "x"}},
        headers=csrf(),
    )
    assert r.status_code == 400


def test_doppelter_credential_ist_400_nicht_500(
    client: TestClient, fake_webauthn: dict[str, Any],
) -> None:
    """Derselbe Credential zweimal registriert (z. B. doppelt geschickter Abschluss) darf nicht
    als sqlite3.IntegrityError/500 durchschlagen, sondern muss sauber 400 geben."""
    t1 = einladung(client)
    o1 = client.post("/auth/einladung/passkey/optionen", json={"token": t1}, headers=csrf()).json()
    body1 = {"token": t1, "challengeId": o1["challengeId"], "credential": {"id": "x"}}
    r1 = client.post("/auth/einladung/passkey/abschluss", json=body1, headers=csrf())
    assert r1.status_code == 200

    t2 = einladung(client, email="zweiter@b.de")
    o2 = client.post("/auth/einladung/passkey/optionen", json={"token": t2}, headers=csrf()).json()
    body2 = {"token": t2, "challengeId": o2["challengeId"], "credential": {"id": "x"}}
    r = client.post("/auth/einladung/passkey/abschluss", json=body2, headers=csrf())
    assert r.status_code == 400
    # Die Einladung bleibt gültig (nicht verbraucht) — derselbe Fehlerfall wie bei einem
    # Verifikationsfehler.
    r3 = client.post("/auth/einladung/pruefen", json={"token": t2}, headers=csrf())
    assert r3.status_code == 200


def test_weiteres_geraet_nur_angemeldet(client: TestClient, fake_webauthn: dict[str, Any]) -> None:
    r1 = client.post("/auth/passkey/hinzufuegen/optionen", json={}, headers=csrf())
    assert r1.status_code == 401
    body = {"challengeId": "x", "credential": {"id": "x"}}
    r2 = client.post("/auth/passkey/hinzufuegen/abschluss", json=body, headers=csrf())
    assert r2.status_code == 401


def test_weiteres_geraet_schliesst_vorhandene_aus_und_speichert_zweiten(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    n = anmelden(client)
    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    conn.execute(
        "INSERT INTO passkeys (id, user_id, public_key, sign_count, label, created_at)"
        " VALUES (?,?,?,?,?,strftime('%s','now'))",
        ("Y3JlZC0x", n.id, b"pk", 0, "Erstes Gerät"),
    )
    conn.close()

    o = client.post("/auth/passkey/hinzufuegen/optionen", json={}, headers=csrf()).json()
    assert o["optionen"]["excludeCredentials"][0]["id"] == "Y3JlZC0x"

    def reg(**_kw: Any) -> FakeReg:
        return FakeReg(credential_id=b"cred-2")

    monkeypatch.setattr(passkey, "verify_registration_response", reg)
    body = {"challengeId": o["challengeId"], "credential": {"id": "y"}, "geraet": "Zweites Gerät"}
    r = client.post("/auth/passkey/hinzufuegen/abschluss", json=body, headers=csrf())
    assert r.status_code == 200

    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    assert conn.execute("SELECT COUNT(*) FROM passkeys WHERE user_id=?", (n.id,)).fetchone()[0] == 2
    conn.close()


def test_sign_count_wird_nur_vorwaerts_aktualisiert(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein niedrigerer sign_count als der gespeicherte (z. B. ein geklonter Authenticator, der
    einen älteren Zustand wiedergibt) darf den gespeicherten Zähler nicht zurücksetzen."""
    t = einladung(client)

    def reg(**_kw: Any) -> FakeReg:
        return FakeReg(sign_count=5)

    monkeypatch.setattr(passkey, "verify_registration_response", reg)
    o = client.post("/auth/einladung/passkey/optionen", json={"token": t}, headers=csrf()).json()
    body = {"token": t, "challengeId": o["challengeId"], "credential": {"id": "x"}}
    r = client.post("/auth/einladung/passkey/abschluss", json=body, headers=csrf())
    assert r.status_code == 200
    client.cookies.clear()

    def auth(**_kw: Any) -> FakeAuth:
        return FakeAuth(new_sign_count=2)  # niedriger als der gespeicherte Stand (5)

    monkeypatch.setattr(passkey, "verify_authentication_response", auth)
    a = client.post("/auth/passkey/optionen", json={}, headers=csrf()).json()
    cred = {"id": "Y3JlZC0x", "rawId": "Y3JlZC0x"}
    r = client.post(
        "/auth/passkey/anmelden",
        json={"challengeId": a["challengeId"], "credential": cred},
        headers=csrf(),
    )
    assert r.status_code == 200

    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    row = conn.execute("SELECT sign_count FROM passkeys WHERE id=?", ("Y3JlZC0x",)).fetchone()
    conn.close()
    assert row["sign_count"] == 5  # bleibt unverändert, nicht auf 2 zurückgesetzt


def test_hinzufuegen_challenge_von_anderem_nutzer_passt_nicht(
    client: TestClient, fake_webauthn: dict[str, Any],
) -> None:
    """Die hinzufuegen-Challenge ist an die user_id gebunden (siehe registrier_optionen mit
    user_id=n.id): Nutzer A holt die Optionen, Nutzer B versucht mit derselben challengeId
    abzuschließen — das darf nicht als Passkey für B durchgehen, sondern muss 400 geben."""
    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    t_a = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={})
    n_a = konten.einladung_einloesen(conn, t_a, lambda _c, _u: None)
    client.cookies.set(COOKIE, konten.sitzung_anlegen(conn, n_a.id))
    conn.close()

    o = client.post("/auth/passkey/hinzufuegen/optionen", json={}, headers=csrf()).json()

    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    t_b = konten.einladung_anlegen(conn, email="b@b.de", name="B", grants={})
    n_b = konten.einladung_einloesen(conn, t_b, lambda _c, _u: None)
    client.cookies.set(COOKIE, konten.sitzung_anlegen(conn, n_b.id))
    conn.close()

    body = {"challengeId": o["challengeId"], "credential": {"id": "x"}}
    r = client.post("/auth/passkey/hinzufuegen/abschluss", json=body, headers=csrf())
    assert r.status_code == 400


def test_zuruecksetzen_per_passkey_loescht_passwort_sperre(
    client: TestClient, fake_webauthn: dict[str, Any],
) -> None:
    """Ein Konto ist nach 5 Fehlversuchen per Passwort gesperrt. Ein Admin schickt
    eine `zuruecksetzen`-Einladung, die per Passkey eingelöst wird — danach darf die alte
    Passwort-Sperre nicht mehr greifen (sie betrifft ohnehin ein inzwischen gelöschtes Passwort)."""
    t = einladung(client)
    client.post(
        "/auth/einladung/passwort", json={"token": t, "passwort": "Kirchturm-Kirche-7"},
        headers=csrf(),
    )
    client.cookies.clear()
    falsch = {"email": "t@b.de", "passwort": "falsch-falsch-falsch"}
    for _ in range(passwort.FEHLVERSUCHE):
        client.post("/auth/passwort/anmelden", json=falsch, headers=csrf())

    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    assert passwort.gesperrt(conn, passwort.schluessel("t@b.de")) is True
    t2 = konten.einladung_anlegen(
        conn, email="t@b.de", name="Tina", grants={}, purpose="zuruecksetzen",
    )
    conn.close()
    o = client.post("/auth/einladung/passkey/optionen", json={"token": t2}, headers=csrf()).json()
    r = client.post(
        "/auth/einladung/passkey/abschluss",
        json={"token": t2, "challengeId": o["challengeId"], "credential": {"id": "x"}},
        headers=csrf(),
    )
    assert r.status_code == 200

    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    assert passwort.gesperrt(conn, passwort.schluessel("t@b.de")) is False
    conn.close()


def _sitzung_altern(client: TestClient, sekunden: int) -> None:
    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    conn.execute(
        "UPDATE sessions SET created_at = created_at - ?, last_seen_at = last_seen_at - ?",
        (sekunden, sekunden),
    )
    conn.close()


def test_weiteres_geraet_braucht_frische_sitzung(
    client: TestClient, fake_webauthn: dict[str, Any],
) -> None:
    """Abschluss-Review: eine Sitzung älter als 10 min darf keinen weiteren Passkey anlegen."""
    anmelden(client)
    o = client.post("/auth/passkey/hinzufuegen/optionen", json={}, headers=csrf())
    assert o.status_code == 200
    _sitzung_altern(client, 11 * 60)
    r1 = client.post("/auth/passkey/hinzufuegen/optionen", json={}, headers=csrf())
    body = {"challengeId": o.json()["challengeId"], "credential": {"id": "x"}}
    r2 = client.post("/auth/passkey/hinzufuegen/abschluss", json=body, headers=csrf())
    for r in (r1, r2):
        assert r.status_code == 403
        assert r.json()["fehler"] == (
            "Bitte zuerst neu anmelden, um ein weiteres Gerät hinzuzufügen."
        )
    assert "reg" not in fake_webauthn  # abgewiesen, bevor irgendetwas geprüft wurde


def test_weiteres_geraet_mit_sitzung_unter_zehn_minuten(
    client: TestClient, fake_webauthn: dict[str, Any],
) -> None:
    anmelden(client)
    _sitzung_altern(client, 9 * 60)
    o = client.post("/auth/passkey/hinzufuegen/optionen", json={}, headers=csrf())
    assert o.status_code == 200
    body = {"challengeId": o.json()["challengeId"], "credential": {"id": "x"}}
    r = client.post("/auth/passkey/hinzufuegen/abschluss", json=body, headers=csrf())
    assert r.status_code == 200
