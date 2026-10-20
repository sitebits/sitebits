"""Testhelfer: weitere Konten mit eigener Sitzung — je Person ein eigener TestClient, damit zwei
Personen gleichzeitig handeln können (Konflikt-Tests)."""

from __future__ import annotations

from conftest import ORIGIN
from fastapi.testclient import TestClient

from scanview_server import db, konten
from scanview_server.web import COOKIE


def person(
    client: TestClient, email: str, *, admin: bool = False,
    grants: dict[str, str] | None = None, name: str | None = None,
) -> tuple[konten.Nutzer, TestClient]:
    conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
    try:
        t = konten.einladung_anlegen(
            conn, email=email, name=name or email.split("@", 1)[0], grants=grants or {},
            is_admin=admin,
        )
        n = konten.einladung_einloesen(conn, t, lambda _c, _u: None)
        token = konten.sitzung_anlegen(conn, n.id)
    finally:
        conn.close()
    return n, TestClient(client.app, base_url=ORIGIN, cookies={COOKIE: token})
