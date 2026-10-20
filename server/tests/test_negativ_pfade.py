"""Negativtests Paketpfade/Pfadtraversal (Kategorie NT-PFADE).

Ergänzt `test_authz.test_authz_pfadtricks` (relative Umwege `../`, `%2e%2e`, Kodierungen) um
absolute Pfade und absolute URIs in `X-Forwarded-Uri` sowie um Traversal in den Pfad- und
Körperparametern der API v1. Maßstab: jede solche Anfrage endet in 404 (bzw. 422 bei einer
ungültigen Eintrags-ID im Körper), auch wenn der Nutzer auf das Projekt selbst ein Recht hat."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import csrf
from fastapi.testclient import TestClient
from nutzer import person
from test_authz import authz, mit_paketen  # noqa: F401  (Fixture)
from test_sitzung import anmelden

POI: dict[str, Any] = {"title": "Tür Nord", "category": "Zugang", "note": "",
                       "position": [1.5, -2.25, 3.0], "station": "S001"}


@pytest.mark.parametrize("uri", [
    # absolute URIs (Caddy reicht nur Pfade weiter; alles andere ist ein Angriff)
    "https://scanview.test/data/kirche/cloud.copc.laz",
    "http://evil.example/data/kirche/cloud.copc.laz",
    "//evil.example/data/kirche/cloud.copc.laz",
    "file:///data/kirche/cloud.copc.laz",
    # absolute Dateisystempfade, auch hinter dem /data-Präfix
    "/etc/passwd",
    "/data//etc/passwd.json",
    "/data/kirche//etc/passwd.json",
    "/data/kirche/%2Fetc%2Fpasswd.json",
    "/data/%2Fetc/passwd.json",
    "/data/kirche/C:/Windows/win.json",
    "/data/kirche/~root/x.json",
    "/data/kirche/..",
    "/data/kirche/./cloud.copc.laz",
])
def test_authz_absolute_pfade_404(mit_paketen: TestClient, uri: str) -> None:  # noqa: F811
    anmelden(mit_paketen, grants={"kirche": "bearbeiter"})
    # Gegenprobe: derselbe Nutzer darf die Datei über den regulären Pfad.
    assert authz(mit_paketen, "/data/kirche/cloud.copc.laz") == 200
    assert authz(mit_paketen, uri) == 404


@pytest.mark.parametrize("pid", ["..", "%2e%2e", "..%2Fost", "%2Fetc%2Fpasswd", "ost%00",
                                 "ost%2F..%2Fkirche", "%2e", "~ost"])
def test_api_projekt_id_mit_traversal_404(mit_paketen: TestClient, pid: str) -> None:  # noqa: F811
    _, c = person(mit_paketen, "b@test", grants={"ost": "bearbeiter"})
    assert c.get("/api/v1/projekte/ost/pois").status_code == 200
    for methode, pfad, koerper in [
        ("GET", f"/api/v1/projekte/{pid}", None),
        ("GET", f"/api/v1/projekte/{pid}/pois", None),
        ("POST", f"/api/v1/projekte/{pid}/pois", {"id": "P-1", "daten": POI}),
        ("GET", f"/api/v1/projekte/{pid}/export.json", None),
    ]:
        r = c.request(methode, pfad, headers=csrf(), json=koerper)
        assert r.status_code == 404, (methode, pfad, r.status_code)


@pytest.mark.parametrize("eid", ["../../etc/passwd", "/etc/passwd", "..", "P-1/../P-2", "P-1\x00"])
def test_eintrags_id_mit_traversal_wird_abgelehnt(
    mit_paketen: TestClient, eid: str,  # noqa: F811
) -> None:
    _, c = person(mit_paketen, "b@test", grants={"ost": "bearbeiter"})
    r = c.post("/api/v1/projekte/ost/pois", headers=csrf(), json={"id": eid, "daten": POI})
    assert r.status_code == 422, (eid, r.status_code)
    assert c.get("/api/v1/projekte/ost/pois").json() == {"pois": []}
