from fastapi.testclient import TestClient


def test_gesundheit(client: TestClient) -> None:
    r = client.get("/api/gesundheit")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_sitzung_ohne_cookie_ist_401_json_mit_dienstkennung(client: TestClient) -> None:
    r = client.get("/api/sitzung")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["dienst"] == "scanview"


def test_http_fehler_reicht_header_durch(client: TestClient) -> None:
    """Abschluss-Review: der JSON-Fehlerhandler darf Header wie `Allow` (405) nicht verschlucken."""
    r = client.get("/auth/abmelden")
    assert r.status_code == 405
    assert r.json()["dienst"] == "scanview"
    assert "POST" in r.headers["allow"]
