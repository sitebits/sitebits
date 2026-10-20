"""Befund B-FUZZ-4 (API-Fuzzing): Ganzzahlen aus Pfad, Abfrage oder Körper, die nicht in eine
SQLite-Ganzzahl (64 Bit) passen, enden als 4xx statt als 500 (OverflowError beim Binden)."""

from __future__ import annotations

import pytest
from conftest import csrf
from fastapi.testclient import TestClient
from nutzer import person
from test_authz import mit_paketen  # noqa: F401  (Fixture)

ZU_GROSS = 2**63
GROSS_OK = 2**63 - 1


@pytest.mark.parametrize("wert", [ZU_GROSS, 10**25, -(10**25), GROSS_OK])
@pytest.mark.parametrize(("methode", "pfad", "body"), [
    ("POST", "/api/v1/admin/nutzer/{w}/sperren", None),
    ("POST", "/api/v1/admin/nutzer/{w}/entsperren", None),
    ("POST", "/api/v1/admin/nutzer/{w}/abmelden-ueberall", None),
    ("PUT", "/api/v1/admin/nutzer/{w}/rechte/ost", {"rolle": "betrachter"}),
    ("DELETE", "/api/v1/konto/tokens/{w}", None),
    ("GET", "/api/v1/protokoll?vor={w}", None),
    ("GET", "/api/v1/projekte/ost/protokoll?vor={w}", None),
    ("PATCH", "/api/v1/projekte/ost", {"titel": "t", "version": "{w}"}),
    ("PATCH", "/api/v1/projekte/ost/pois/x", {"daten": {}, "version": "{w}"}),
    ("DELETE", "/api/v1/projekte/ost/pois/x?version={w}", None),
])
def test_grosse_ganzzahl_kein_serverfehler(
    mit_paketen: TestClient,  # noqa: F811
    methode: str, pfad: str, body: dict[str, object] | None, wert: int,
) -> None:
    _, c = person(mit_paketen, "a@b.de", admin=True, grants={"ost": "bearbeiter"})
    daten = {k: (wert if v == "{w}" else v) for k, v in body.items()} if body else None
    r = c.request(methode, pfad.format(w=wert), headers=csrf(), json=daten)
    if wert == GROSS_OK:
        # Noch darstellbar: eine gewöhnliche Antwort (leere Seite, 404, 409, 422), nie 500.
        assert r.status_code < 500, (r.status_code, r.text)
    else:
        assert 400 <= r.status_code < 500, (r.status_code, r.text)
