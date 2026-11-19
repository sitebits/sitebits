"""Projektliste (GET /api/v1/projekte): Anweisungen je weiterem Projekt bleiben fest (N+1 gemessen).

Die Route fragt je Projekt im Katalog die Rolle ab und, wenn Befund-Fäden an sind, Sicht und
„überfällig“ für die Projektkarte. Gezählt werden die SQL-Anweisungen auf der Verbindung der
Anfrage (Abhängigkeit `web.db`) per `set_trace_callback` — einmal mit 1, einmal mit 20 Projekten.
Zugesichert wird nur die Differenz: Fixture-Aufbau und Anmeldung kosten einen festen Sockel, der
hier nicht interessiert.

Gemessen am 05.11.2026 mit `_anweisungen` (je Fall zwei Läufe, beide gleich):
  Befund-Fäden aus: 1 Projekt 4, 20 Projekte 23 Anweisungen → Differenz 19 → K_AUS = 1 je Projekt
  Befund-Fäden an:  1 Projekt 6, 20 Projekte 63 Anweisungen → Differenz 57 → K_AN = 3 je Projekt
Nicht optimieren, solange K_AN ≤ 4 (Rolle, Sicht, überfällig — je eine kleine Indexabfrage).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import ORIGIN
from fastapi.testclient import TestClient
from pakete import lege_paket

from scanview_server import db as dbmod
from scanview_server import konten
from scanview_server.config import Settings
from scanview_server.web import create_app, db

K_AUS = 1
K_AN = 3


def _anweisungen(wurzel: Path, anzahl: int, *, faeden: bool) -> int:
    """Anweisungen der Projektliste für einen Nutzer mit Recht auf `anzahl` Projekte."""
    data = wurzel / "data"
    state = wurzel / "state"
    data.mkdir(parents=True)
    state.mkdir()
    settings = Settings(data_dir=data, state_dir=state, origin=ORIGIN, rp_id="scanview.test",
                        rp_name="scanview", cookie_secure=True)
    pids = [f"p{i:02d}" for i in range(anzahl)]
    for pid in pids:
        lege_paket(data, pid)
    app = create_app(settings)
    gesehen: list[str] = []

    def gezaehlt() -> Iterator[sqlite3.Connection]:
        conn = dbmod.connect(settings.db_path)
        conn.set_trace_callback(gesehen.append)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[db] = gezaehlt
    with TestClient(app, base_url=ORIGIN) as c:
        app.state.katalog.laden()
        conn = dbmod.connect(settings.db_path)
        try:
            t = konten.einladung_anlegen(conn, email="n@b.de", name="N",
                                         grants=dict.fromkeys(pids, "bearbeiter"), is_admin=False)
            n = konten.einladung_einloesen(conn, t, lambda _c, _u: None)
            c.cookies.set("__Host-scanview", konten.sitzung_anlegen(conn, n.id))
            for pid in pids:
                conn.execute("INSERT INTO projekte (project_id, vorlage, angelegt_am,"
                             " befund_faeden, panoramen_nur_freigegeben)"
                             " VALUES (?, 'bauprojekt', 0, ?, 0)", (pid, int(faeden)))
        finally:
            conn.close()
        gesehen.clear()
        r = c.get("/api/v1/projekte")
    assert r.status_code == 200
    assert [p["id"] for p in r.json()["projekte"]] == pids
    assert all(p["befund_faeden"] is faeden for p in r.json()["projekte"])
    return len(gesehen)


@pytest.mark.parametrize(("faeden", "k"), [(False, K_AUS), (True, K_AN)], ids=["aus", "an"])
def test_projektliste_waechst_je_projekt_hoechstens_um_k(tmp_path: Path, faeden: bool,
                                                          k: int) -> None:
    eins = _anweisungen(tmp_path / "eins", 1, faeden=faeden)
    zwanzig = _anweisungen(tmp_path / "zwanzig", 20, faeden=faeden)
    assert zwanzig - eins <= 19 * k, (eins, zwanzig)
