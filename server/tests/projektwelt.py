"""Testhelfer Projektverwaltung: Konten je Rolle in den Prüfprojekten „ost“ und „kirche“,
je Konto ein eigener TestClient mit Sitzung."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from conftest import csrf
from fastapi.testclient import TestClient
from nutzer import person
from test_authz import mit_paketen  # noqa: F401  (Fixture)

from scanview_server import db, konten

OST = "/api/v1/projekte/ost"
KIRCHE = "/api/v1/projekte/kirche"

# Schlüssel → (E-Mail, Admin, Rechte, Anzeigename)
KONTEN: dict[str, tuple[str, bool, dict[str, str], str]] = {
    "admin": ("adm@x.de", True, {}, "Anna Admin"),
    "leitung": ("l@x.de", False, {"ost": "projektleitung"}, "Lea Leitung"),
    "leitung2": ("l2@x.de", False, {"ost": "projektleitung"}, "Lars Leitung"),
    "leitung_kirche": ("lk@x.de", False, {"kirche": "projektleitung"}, "Kai Kirche"),
    "bearbeiter": (
        "b@x.de",
        False,
        {"ost": "bearbeiter", "kirche": "betrachter"},
        "Ben Bearbeiter",
    ),
    "rueckmelder": ("r@x.de", False, {"ost": "rueckmelder"}, "Rita Rueck"),
    "betrachter": ("t@x.de", False, {"ost": "betrachter"}, "Tom Betrachter"),
    "admin_mit_rolle": ("ar@x.de", True, {"ost": "bearbeiter"}, "Ada Adminrolle"),
}


@dataclass(frozen=True)
class Konto:
    nutzer: konten.Nutzer
    c: TestClient

    def get(self, pfad: str) -> httpx.Response:
        return self.c.get(pfad)

    def schreibe(self, methode: str, pfad: str, koerper: Any = None) -> httpx.Response:
        return self.c.request(methode, pfad, json=koerper, headers=csrf())


@pytest.fixture
def projektwelt(mit_paketen: TestClient) -> dict[str, Konto]:  # noqa: F811
    welt: dict[str, Konto] = {}
    for schluessel, (mail, ist_admin, rechte, name) in KONTEN.items():
        n, c = person(mit_paketen, mail, admin=ist_admin, grants=rechte, name=name)
        welt[schluessel] = Konto(n, c)
    return welt


def verbindung(c: TestClient) -> sqlite3.Connection:
    return db.connect(c.app.state.settings.db_path)  # type: ignore[attr-defined]


def ereignisse(c: TestClient, praefix: str) -> list[sqlite3.Row]:
    conn = verbindung(c)
    try:
        return conn.execute(
            "SELECT art, project_id, objekt_id, vorher, nachher, nutzer_id, kanal FROM ereignisse"
            " WHERE art LIKE ? ORDER BY id",
            (f"{praefix}%",),
        ).fetchall()
    finally:
        conn.close()


def rolle_in(c: TestClient, uid: int, pid: str) -> str | None:
    conn = verbindung(c)
    try:
        r = conn.execute(
            "SELECT role FROM project_roles WHERE user_id=? AND project_id=?", (uid, pid)
        ).fetchone()
        return None if r is None else str(r["role"])
    finally:
        conn.close()
