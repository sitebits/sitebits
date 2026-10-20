"""Nur-Lese-Links (F28): Anlegen nur Projektleitung, Gast sieht nur Wolke und Meshes seines
Projekts, Widerruf/Ablauf/Schalter wirken sofort, Link-Passwort mit Sperre (ADR 0016)."""

from __future__ import annotations

import json
import logging
import os
import struct
from pathlib import Path

import pytest
from conftest import ORIGIN, csrf
from fastapi.testclient import TestClient
from projektwelt import OST, Konto, ereignisse, projektwelt, verbindung  # noqa: F401
from test_authz import mit_paketen  # noqa: F401

from scanview_server import teilen

TEILEN = f"{OST}/teilen"


def _glb(daten: dict[str, object]) -> bytes:
    j = json.dumps(daten).encode()
    j += b" " * ((4 - len(j) % 4) % 4)
    return b"glTF" + struct.pack("<II", 2, 20 + len(j)) + struct.pack("<I4s", len(j), b"JSON") + j


def _paket_ausbauen(c: TestClient, pid: str = "ost", *, mesh_uri: str = "model.bin") -> Path:
    """Ergänzt das Testpaket um Mesh (glb mit externem Buffer), Fotos und Panoramen."""
    d = Path(c.app.state.settings.data_dir) / pid  # type: ignore[attr-defined]
    (d / "layers/mesh").mkdir(parents=True)
    (d / "layers/mesh/model.glb").write_bytes(_glb({"buffers": [{"uri": mesh_uri,
                                                                  "byteLength": 4}]}))
    (d / "layers/mesh/model.bin").write_bytes(b"\0" * 4)
    (d / "layers/mesh/preview.glb").write_bytes(_glb({"buffers": [{"byteLength": 4}]}))
    (d / "fotos/F0001").mkdir(parents=True)
    (d / "fotos/F0001/512.jpg").write_bytes(b"\xff\xd8")
    (d / "images.json").write_text("{}")
    (d / "stations.json").write_text("{}")
    (d / "panos/S001").mkdir(parents=True)
    (d / "panos/S001/0.jpg").write_bytes(b"\xff\xd8")
    m = json.loads((d / "project.json").read_text())
    m["layers"] += [
        {"id": "mesh", "kind": "mesh", "format": "gltf", "title": "Mesh",
         "url": "layers/mesh/model.glb", "previewUrl": "layers/mesh/preview.glb",
         "triangleCount": 1, "boundsWorld": [[0, 0, 0], [1, 1, 1]], "positionStepM": 0.001},
        {"id": "fotos", "kind": "images", "title": "Fotos", "url": "images.json", "count": 1},
    ]
    m["conversion"] = {"geheim": "interne Werkzeugpfade"}
    (d / "project.json").write_text(json.dumps(m))
    st = (d / "project.json").stat()
    os.utime(d / "project.json", ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    return d


def _erlauben(leitung: Konto, an: bool = True) -> None:
    r = leitung.schreibe("PATCH", f"{OST}/einstellungen", {"nurLeseLinks": an})
    assert r.status_code == 200, r.text
    assert r.json()["einstellungen"]["nurLeseLinks"] is an


def _anlegen(leitung: Konto, **koerper: object) -> tuple[dict[str, object], str]:
    r = leitung.schreibe("POST", TEILEN, {"name": "Gast Ost", **koerper})
    assert r.status_code == 201, r.text
    return r.json()["link"], r.json()["token"]


def _gast(c: TestClient) -> TestClient:
    return TestClient(c.app, base_url=ORIGIN)


def _austausch(g: TestClient, token: str, passwort: str | None = None) -> int:
    koerper: dict[str, str] = {"token": token}
    if passwort is not None:
        koerper["passwort"] = passwort
    return g.post("/auth/teilen", json=koerper, headers=csrf()).status_code


def _authz(c: TestClient, uri: str, **kopf: str) -> int:
    return c.get("/api/authz", headers={"X-Forwarded-Uri": uri, "X-Forwarded-Method": "GET",
                                        **kopf}).status_code


@pytest.fixture
def welt(projektwelt: dict[str, Konto]) -> dict[str, Konto]:  # noqa: F811
    _paket_ausbauen(projektwelt["leitung"].c)
    return projektwelt


def test_schalter_standard_aus_und_anlegen_dann_403(welt: dict[str, Konto]) -> None:
    r = welt["leitung"].schreibe("POST", TEILEN, {"name": "x"})
    assert r.status_code == 403 and r.json()["fehler"] == teilen.AUSGESCHALTET
    assert welt["leitung"].get(TEILEN).json() == {"erlaubt": False, "links": []}


@pytest.mark.parametrize("wer,status", [("bearbeiter", 403), ("betrachter", 403),
                                        ("rueckmelder", 403), ("leitung_kirche", 404)])
def test_nur_projektleitung_verwaltet(welt: dict[str, Konto], wer: str, status: int) -> None:
    _erlauben(welt["leitung"])
    assert welt[wer].schreibe("POST", TEILEN, {"name": "x"}).status_code == status
    assert welt[wer].get(TEILEN).status_code == status


def test_admin_darf_anlegen(welt: dict[str, Konto]) -> None:
    _erlauben(welt["leitung"])
    assert welt["admin"].schreibe("POST", TEILEN, {"name": "x"}).status_code == 201


def test_anlegen_braucht_frische_sitzung(welt: dict[str, Konto]) -> None:
    _erlauben(welt["leitung"])
    conn = verbindung(welt["leitung"].c)
    conn.execute("UPDATE sessions SET created_at = created_at - 3600")
    conn.close()
    r = welt["leitung"].schreibe("POST", TEILEN, {"name": "x"})
    assert r.status_code == 403 and "neu anmelden" in r.json()["fehler"]


@pytest.mark.parametrize("koerper", [{"name": ""}, {"name": "x", "tage": 0},
                                     {"name": "x", "tage": 91}, {"name": "x", "passwort": "abc"},
                                     {"name": "x", "passwort": " abcd"}, {"name": "x", "frei": 1}])
def test_ungueltige_eingaben_422(welt: dict[str, Konto], koerper: dict[str, object]) -> None:
    _erlauben(welt["leitung"])
    assert welt["leitung"].schreibe("POST", TEILEN, koerper).status_code == 422


def test_gast_sieht_nur_wolke_und_mesh(welt: dict[str, Konto]) -> None:
    _erlauben(welt["leitung"])
    link, token = _anlegen(welt["leitung"], tage=7)
    assert "token" not in link and link["passwort"] is False
    g = _gast(welt["leitung"].c)
    r = g.post("/auth/teilen", json={"token": token}, headers=csrf())
    assert r.status_code == 200 and r.json() == {"projekt": "ost"}
    kopf = r.headers["set-cookie"]
    assert kopf.startswith(f"{teilen.GAST_COOKIE}=") and "HttpOnly" in kopf and "Secure" in kopf
    s = g.get("/api/sitzung").json()
    assert s["nutzer"] is None and s["gast"]["projekt"] == "ost" and s["gast"]["name"] == "Gast Ost"
    for pfad in ("cloud.copc.laz", "topview.png", "layers/mesh/model.glb",
                 "layers/mesh/preview.glb", "layers/mesh/model.bin"):
        assert _authz(g, f"/data/ost/{pfad}") == 200, pfad
    assert _authz(g, "/data/ost/cloud.copc.laz", Range="bytes=0-99") == 200
    for pfad in ("project.json", "stations.json", "images.json", "fotos/F0001/512.jpg",
                 "panos/S001/0.jpg", "layers/mesh/model.glb.bak", "Cloud.copc.laz",
                 "layers/mesh/../mesh/model.glb", "layers/%2e%2e/cloud.copc.laz",
                 "layers%2fmesh/model.glb"):
        assert _authz(g, f"/data/ost/{pfad}") == 404, pfad
    assert _authz(g, "/data/kirche/cloud.copc.laz") == 404  # anderes Projekt, gleiches Cookie
    assert _authz(g, "/data/ost/cloud.copc.laz", Range="bytes=0-9,20-29") == 404
    assert g.get("/api/authz", headers={"X-Forwarded-Uri": "/data/ost/cloud.copc.laz",
                                        "X-Forwarded-Method": "POST"}).status_code == 404
    # Kein Weg in die Projekt-API: der Gast ist keine Rolle.
    for pfad in ("/api/v1/projekte", f"{OST}", f"{OST}/pois", f"{OST}/messungen",
                 f"{OST}/befunde", "/api/v1/sitzung", "/api/projekte", TEILEN):
        assert g.get(pfad).status_code == 401, pfad


def test_gast_manifest_bereinigt(welt: dict[str, Konto]) -> None:
    _erlauben(welt["leitung"])
    _, token = _anlegen(welt["leitung"])
    g = _gast(welt["leitung"].c)
    assert g.get("/api/teilen/manifest").status_code == 401
    assert _austausch(g, token) == 200
    r = g.get("/api/teilen/manifest")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    m = r.json()
    assert [x["kind"] for x in m["layers"]] == ["pointcloud", "mesh"]
    assert "conversion" not in m and m["topview"]["url"] == "topview.png"
    assert m["projectId"] == "ost" and m["schemaVersion"] == 2


def _mobilstufe_eintragen(c: TestClient, *, datei: bool) -> None:
    d = Path(c.app.state.settings.data_dir) / "ost"  # type: ignore[attr-defined]
    if datei:
        (d / "layers/mesh/mobile.glb").write_bytes(_glb({"buffers": [{"byteLength": 4}]}))
    m = json.loads((d / "project.json").read_text())
    mesh = next(x for x in m["layers"] if x["kind"] == "mesh")
    mesh |= {"mobileUrl": "layers/mesh/mobile.glb", "mobileSizeBytes": 40,
             "mobileTriangleCount": 1}
    (d / "project.json").write_text(json.dumps(m))
    st = (d / "project.json").stat()
    os.utime(d / "project.json", ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


@pytest.mark.parametrize("datei", [True, False])
def test_gast_mobilstufe_optional(welt: dict[str, Konto], datei: bool) -> None:
    """Mobilstufe ist für Gäste erreichbar; fehlt die Datei, bleibt das Mesh ohne sie sichtbar."""
    _mobilstufe_eintragen(welt["leitung"].c, datei=datei)
    _erlauben(welt["leitung"])
    _, token = _anlegen(welt["leitung"])
    g = _gast(welt["leitung"].c)
    assert _austausch(g, token) == 200
    mesh = next(x for x in g.get("/api/teilen/manifest").json()["layers"] if x["kind"] == "mesh")
    assert ("mobileUrl" in mesh) is datei and ("mobileSizeBytes" in mesh) is datei
    assert _authz(g, "/data/ost/layers/mesh/mobile.glb") == (200 if datei else 404)
    assert _authz(g, "/data/ost/layers/mesh/model.glb") == 200


def test_widerruf_wirkt_sofort(welt: dict[str, Konto]) -> None:
    _erlauben(welt["leitung"])
    link, token = _anlegen(welt["leitung"])
    g = _gast(welt["leitung"].c)
    assert _austausch(g, token) == 200
    assert _authz(g, "/data/ost/cloud.copc.laz") == 200
    r = welt["leitung"].schreibe("DELETE", f"{TEILEN}/{link['id']}")
    assert r.status_code == 200 and r.json()["link"]["widerrufen"] is not None
    assert _authz(g, "/data/ost/cloud.copc.laz") == 401
    assert g.get("/api/sitzung").status_code == 401
    assert g.get("/api/teilen/manifest").status_code == 401
    assert _austausch(_gast(welt["leitung"].c), token) == 404
    # Zweiter Widerruf ändert nichts, fremder/unbekannter Link 404.
    assert welt["leitung"].schreibe("DELETE", f"{TEILEN}/{link['id']}").status_code == 200
    assert welt["leitung"].schreibe("DELETE", f"{TEILEN}/9999").status_code == 404
    arten = [e["art"] for e in ereignisse(welt["leitung"].c, "teilen.")]
    assert arten == ["teilen.angelegt", "teilen.widerrufen"]


def test_ablauf_und_schalter_aus_wirken_sofort(welt: dict[str, Konto]) -> None:
    _erlauben(welt["leitung"])
    link, token = _anlegen(welt["leitung"])
    g = _gast(welt["leitung"].c)
    assert _austausch(g, token) == 200
    _erlauben(welt["leitung"], False)
    assert _authz(g, "/data/ost/cloud.copc.laz") == 401
    assert _austausch(_gast(welt["leitung"].c), token) == 404
    _erlauben(welt["leitung"], True)
    assert _authz(g, "/data/ost/cloud.copc.laz") == 200
    conn = verbindung(welt["leitung"].c)
    conn.execute("UPDATE teilen_links SET erstellt_am = erstellt_am - 100,"
                 " ablauf = erstellt_am - 99 WHERE id=?", (link["id"],))
    conn.close()
    assert _authz(g, "/data/ost/cloud.copc.laz") == 401
    assert _austausch(_gast(welt["leitung"].c), token) == 404


def test_passwort(welt: dict[str, Konto]) -> None:
    _erlauben(welt["leitung"])
    link, token = _anlegen(welt["leitung"], passwort="hof1")
    assert link["passwort"] is True
    g = _gast(welt["leitung"].c)
    r = g.post("/auth/teilen", json={"token": token}, headers=csrf())
    assert r.status_code == 401 and r.json()["passwort"] is True
    assert teilen.GAST_COOKIE not in g.cookies
    assert _austausch(g, token, "falsch") == 401
    assert _austausch(g, token, "hof1") == 200
    assert _authz(g, "/data/ost/cloud.copc.laz") == 200


def test_passwort_sperre_nach_fuenf_fehlversuchen(welt: dict[str, Konto]) -> None:
    _erlauben(welt["leitung"])
    _, token = _anlegen(welt["leitung"], passwort="hof1")
    g = _gast(welt["leitung"].c)
    assert [_austausch(g, token, f"nein{i}") for i in range(5)] == [401] * 5
    assert _austausch(g, token, "hof1") == 429


def test_austausch_braucht_csrf_und_gueltiges_token(welt: dict[str, Konto]) -> None:
    _erlauben(welt["leitung"])
    _, token = _anlegen(welt["leitung"])
    g = _gast(welt["leitung"].c)
    assert g.post("/auth/teilen", json={"token": token}).status_code == 403
    for falsch in ("x", token[:-1] + ("A" if token[-1] != "A" else "B"), token + "A", "ä" * 43):
        assert _austausch(g, falsch) == 404


def test_angemeldet_ohne_rolle_oeffnet_link(welt: dict[str, Konto]) -> None:
    _erlauben(welt["leitung"])
    _, token = _anlegen(welt["leitung"])
    k = welt["leitung_kirche"].c  # Konto auf der Instanz, aber keine Rolle in „ost“
    assert _austausch(k, token) == 200
    assert _authz(k, "/data/ost/cloud.copc.laz") == 200
    assert _authz(k, "/data/ost/stations.json") == 404
    s = k.get("/api/sitzung").json()
    assert s["nutzer"]["name"] == "Kai Kirche" and s["gast"]["projekt"] == "ost"
    # Die eigene Rolle bleibt unberührt.
    assert _authz(k, "/data/kirche/stations.json") == 200


def test_liste_ohne_geheimnisse(welt: dict[str, Konto]) -> None:
    _erlauben(welt["leitung"])
    _, token = _anlegen(welt["leitung"], passwort="hof1")
    text = welt["leitung"].get(TEILEN).text
    assert token not in text and "argon2" not in text and "token_hash" not in text


def test_token_nie_im_log(welt: dict[str, Konto], caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    _erlauben(welt["leitung"])
    _, token = _anlegen(welt["leitung"], passwort="hof1")
    g = _gast(welt["leitung"].c)
    _austausch(g, token, "falsch")
    _austausch(g, token, "hof1")
    _authz(g, "/data/ost/cloud.copc.laz")
    gast_cookie = g.cookies.get(teilen.GAST_COOKIE) or ""
    assert gast_cookie and token not in caplog.text and gast_cookie not in caplog.text


def test_abrufgrenze_je_link(welt: dict[str, Konto]) -> None:
    _erlauben(welt["leitung"])
    _, token = _anlegen(welt["leitung"])
    g = _gast(welt["leitung"].c)
    assert _austausch(g, token) == 200
    welt["leitung"].c.app.state.teilen_drosseln.abrufe = teilen.Fenster(3)  # type: ignore[attr-defined]
    assert [_authz(g, "/data/ost/cloud.copc.laz") for _ in range(4)] == [200, 200, 200, 429]


def test_austausch_drossel_je_ip(welt: dict[str, Konto]) -> None:
    welt["leitung"].c.app.state.teilen_drosseln.austausch_ip = teilen.Fenster(2)  # type: ignore[attr-defined]
    g = _gast(welt["leitung"].c)
    assert [_austausch(g, "x" * 43) for _ in range(3)] == [404, 404, 429]


@pytest.mark.parametrize("uri", ["../../kirche/cloud.copc.laz", "/etc/passwd", "%2e%2e/x.bin",
                                 "https://fremd.test/x.bin", "fehlt.bin", ".versteckt.bin",
                                 "../../fotos/F0001/512.jpg", "../../images.json"])
def test_mesh_mit_unzulaessigem_verweis_faellt_raus(
    mit_paketen: TestClient, projektwelt: dict[str, Konto], uri: str,  # noqa: F811
) -> None:
    _paket_ausbauen(projektwelt["leitung"].c, mesh_uri=uri)
    _erlauben(projektwelt["leitung"])
    _, token = _anlegen(projektwelt["leitung"])
    g = _gast(projektwelt["leitung"].c)
    assert _austausch(g, token) == 200
    assert [x["kind"] for x in g.get("/api/teilen/manifest").json()["layers"]] == ["pointcloud"]
    assert _authz(g, "/data/ost/layers/mesh/model.glb") == 404
    assert _authz(g, "/data/ost/cloud.copc.laz") == 200


def test_paketpfad_normierung() -> None:
    assert teilen._paketpfad("layers/mesh/model.glb", "model.bin") == "layers/mesh/model.bin"
    assert teilen._paketpfad("layers/mesh/model.glb", "../tex/a.ktx2") == "layers/tex/a.ktx2"
    for ref in ("", "/x", "..\\x", "a%20b.bin", "../../../x", "data:x", "a//.b"):
        assert teilen._paketpfad("layers/mesh/model.glb", ref) is None, ref


def test_fehlversuch_im_fail2ban_format(
    welt: dict[str, Konto], caplog: pytest.LogCaptureFixture,
) -> None:
    import re
    _erlauben(welt["leitung"])
    _, token = _anlegen(welt["leitung"], passwort="hof1")
    caplog.set_level(logging.WARNING, logger="scanview.anmeldung")
    _austausch(_gast(welt["leitung"].c), token, "falsch")
    muster = re.compile(r"scanview Anmeldung fehlgeschlagen \(Konto-Schl\S+ [0-9a-f]+\) von \S+$")
    assert any(muster.search(r.getMessage()) for r in caplog.records)


def test_punktwolke_mit_fotopfad_faellt_raus(
    mit_paketen: TestClient, projektwelt: dict[str, Konto],  # noqa: F811
) -> None:
    d = _paket_ausbauen(projektwelt["leitung"].c)
    m = json.loads((d / "project.json").read_text())
    m["layers"][0]["url"] = "fotos/F0001/512.jpg"
    (d / "project.json").write_text(json.dumps(m))
    st = (d / "project.json").stat()
    os.utime(d / "project.json", ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    _erlauben(projektwelt["leitung"])
    _, token = _anlegen(projektwelt["leitung"])
    g = _gast(projektwelt["leitung"].c)
    assert _austausch(g, token) == 200
    assert g.get("/api/teilen/manifest").status_code == 404  # ohne Punktwolke keine Gast-Ansicht
    assert _authz(g, "/data/ost/fotos/F0001/512.jpg") == 404


def test_ip_flut_sperrt_andere_nicht(welt: dict[str, Konto]) -> None:
    app = welt["leitung"].c.app
    app.state.teilen_drosseln.austausch_ip = teilen.Fenster(2)  # type: ignore[attr-defined]
    app.state.teilen_drosseln.austausch_global = teilen.Fenster(3)  # type: ignore[attr-defined]
    g = _gast(welt["leitung"].c)
    assert [_austausch(g, "x" * 43) for _ in range(10)] == [404, 404] + [429] * 8
    # Die abgewiesenen Anfragen derselben IP belegen das globale Fenster nicht: ein Platz ist frei.
    assert app.state.teilen_drosseln.austausch_global.erlaubt("alle")  # type: ignore[attr-defined]
