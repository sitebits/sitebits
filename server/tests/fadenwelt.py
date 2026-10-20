"""Testhelfer Befund-Faden: Projekt „ost“ (Vorlage Bauprojekt, Fäden an) mit Gewerken
„Bauherr“ (Bauherrenseite) und „Dach“, Konten je Rolle mit eigener Sitzung, dazu API-Tokens.
Ein Befund P-1 mit einem öffentlichen und einem internen Kommentar. Nur synthetische Daten."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from conftest import ORIGIN, csrf
from fastapi.testclient import TestClient
from nutzer import person
from pakete import lege_paket

from scanview_server import apitoken, db, projektverwaltung

BASIS = "/api/v1/projekte/ost"
FADEN = BASIS + "/pois/P-1/faden"
POI = {"title": "Riss Attika", "category": "Mangel", "note": "", "position": [1.0, 2.0, 3.0],
       "station": None}
INTERN = "INTERN-Nachtrag-pruefen"
OEFFENTLICH = "Bitte bis Freitag beheben"

# Schlüssel → (Rolle in „ost“, Gewerk)
KONTEN: dict[str, tuple[str, str | None]] = {
    "leitung": ("projektleitung", None),
    "bh": ("bearbeiter", "Bauherr"),
    "bear": ("bearbeiter", "Dach"),
    "bear_ohne": ("bearbeiter", None),
    "rueck": ("rueckmelder", "Dach"),
    "rueck_bh": ("rueckmelder", "Bauherr"),
    "betr": ("betrachter", "Bauherr"),
}


@dataclass
class Welt:
    client: TestClient
    c: dict[str, TestClient] = field(default_factory=dict)
    id: dict[str, int] = field(default_factory=dict)
    gewerk: dict[str, int] = field(default_factory=dict)
    intern_id: str = ""
    oeffentlich_id: str = ""

    def conn(self) -> sqlite3.Connection:
        return db.connect(self.client.app.state.settings.db_path)  # type: ignore[attr-defined]

    def sql(self, sql: str, *werte: Any) -> list[sqlite3.Row]:
        conn = self.conn()
        try:
            return conn.execute(sql, werte).fetchall()
        finally:
            conn.close()

    def token(self, wer: str, umfang: str, *, agent: bool = False) -> TestClient:
        conn = self.conn()
        try:
            roh, _ = apitoken.erzeugen(conn, self.id[wer], f"{wer}-{umfang}", umfang,
                                       agent=agent)
        finally:
            conn.close()
        return TestClient(self.client.app, base_url=ORIGIN,
                          headers={"Authorization": f"Bearer {roh}"})

    def post(self, wer: str, pfad: str, koerper: Any = None) -> httpx.Response:
        return self.c[wer].post(pfad, json=koerper, headers=csrf())

    def stand(self) -> dict[str, Any]:
        r = self.c["leitung"].get(FADEN)
        assert r.status_code == 200
        st: dict[str, Any] = r.json()["stand"]
        return st


@pytest.fixture
def welt(client: TestClient) -> Iterator[Welt]:
    lege_paket(client.app.state.settings.data_dir, "ost")  # type: ignore[attr-defined]
    lege_paket(client.app.state.settings.data_dir, "kirche")  # type: ignore[attr-defined]
    client.app.state.katalog.laden()  # type: ignore[attr-defined]
    w = Welt(client)
    for schluessel, (rolle, _g) in KONTEN.items():
        n, c = person(client, f"{schluessel}@example.test", grants={"ost": rolle},
                      name=f"Name {schluessel}")
        w.c[schluessel], w.id[schluessel] = c, n.id
    n, c = person(client, "fremd@example.test", grants={"kirche": "bearbeiter"})
    w.c["fremd"], w.id["fremd"] = c, n.id
    n, c = person(client, "admin@example.test", admin=True)
    w.c["admin"], w.id["admin"] = c, n.id
    conn = w.conn()
    try:
        projektverwaltung.einrichten(conn, "ost", "bauprojekt", von=w.id["admin"])
        for name, bhs in (("Bauherr", True), ("Dach", False)):
            w.gewerk[name] = projektverwaltung.gewerk_anlegen(
                conn, "ost", name=name, kurz=name[:2].upper(), bauherrenseite=bhs,
                von=w.id["admin"]).id
        for schluessel, (_r, g) in KONTEN.items():
            if g is not None:
                conn.execute("UPDATE project_roles SET gewerk_id=? WHERE user_id=?",
                             (w.gewerk[g], w.id[schluessel]))
    finally:
        conn.close()
    assert w.post("leitung", BASIS + "/pois", {"id": "P-1", "daten": POI}).status_code == 201
    r = w.post("bh", FADEN, {"art": "kommentar", "text": INTERN, "intern": True})
    assert r.status_code == 201, r.text
    w.intern_id = r.json()["eintrag"]["schluessel"]
    r = w.post("bear", FADEN, {"art": "kommentar", "text": OEFFENTLICH})
    assert r.status_code == 201, r.text
    w.oeffentlich_id = r.json()["eintrag"]["schluessel"]
    yield w
