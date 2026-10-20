from conftest import ORIGIN, csrf
from fastapi.testclient import TestClient

from scanview_server import db, konten
from scanview_server.config import Settings
from scanview_server.web import create_app


def anmelden(
    client: TestClient, *, admin: bool = False, grants: dict[str, str] | None = None,
) -> konten.Nutzer:
    """Legt direkt in der DB ein Konto an und setzt das Sitzungscookie im Client."""
    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    t = konten.einladung_anlegen(
        conn, email="n@b.de", name="N", grants=grants or {}, is_admin=admin,
    )
    n = konten.einladung_einloesen(conn, t, lambda _c, _u: None)
    client.cookies.set("__Host-scanview", konten.sitzung_anlegen(conn, n.id))
    conn.close()
    return n


def test_sitzung_mit_cookie(client: TestClient) -> None:
    n = anmelden(client)
    r = client.get("/api/sitzung")
    assert r.status_code == 200
    assert r.json() == {
        "dienst": "scanview",
        "nutzer": {"id": n.id, "name": "N", "email": "n@b.de", "admin": False},
        "quelltext_url": None,
    }


def test_abmelden_braucht_csrf_und_loescht_sitzung(client: TestClient) -> None:
    anmelden(client)
    assert client.post("/auth/abmelden").status_code == 403
    assert client.post(
        "/auth/abmelden", headers={"Origin": "https://fremd.test", "X-Scanview": "1"},
    ).status_code == 403
    assert client.post("/auth/abmelden", headers={"Origin": ORIGIN}).status_code == 403
    r = client.post("/auth/abmelden", headers=csrf())
    assert r.status_code == 200
    assert "__Host-scanview" in r.headers.get("set-cookie", "")
    assert client.get("/api/sitzung").status_code == 401
    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    conn.close()


def test_cookie_attribute_beim_setzen(client: TestClient) -> None:
    # über Einladung + Passwort wird das Cookie gesetzt; hier nur die Hilfsfunktion
    from fastapi import Response

    from scanview_server.web import setze_sitzung

    resp = Response()
    setze_sitzung(resp, "abc", client.app.state.settings)  # type: ignore[attr-defined]
    c = resp.headers["set-cookie"]
    assert c.startswith("__Host-scanview=abc")
    for teil in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        assert teil.lower() in c.lower()
    assert "domain" not in c.lower()


def test_dienststart_raeumt_abgelaufene_sitzungen_auf(settings: Settings) -> None:
    """create_app ruft konten.aufraeumen auf (Abschluss-Review: veraltete Zeilen wuchsen sonst
    unbegrenzt)."""
    create_app(settings)
    conn = db.connect(settings.db_path)
    t = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={})
    uid = konten.einladung_einloesen(conn, t, lambda _c, _u: None).id
    konten.sitzung_anlegen(conn, uid, now=1)  # längst abgelaufen
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    create_app(settings)
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    conn.close()
