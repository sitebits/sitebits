"""Negativtests Uploads und Importe: Pfade, Rechte, Schalter, Auftragsoptionen."""

from __future__ import annotations

import pytest
from conftest import csrf
from fastapi.testclient import TestClient
from uploadwelt import GLB, SPZ, Welt, welt, welt_aus  # noqa: F401

from scanview_server import auftraege
from scanview_server import uploads as U


def _nichts_angelegt(w: Welt) -> None:
    assert list(w.wurzel.iterdir()) == []
    conn, jc = w.conn(), w.jobs()
    try:
        assert conn.execute("SELECT COUNT(*) FROM uploads").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM upload_dateien").fetchone()[0] == 0
        assert jc.execute("SELECT COUNT(*) FROM auftraege").fetchone()[0] == 0
    finally:
        conn.close()
        jc.close()


TIEF = "/".join(["o"] * 6) + "/a.glb"  # sieben Ebenen


@pytest.mark.parametrize("pfade", [
    ["a/../b.glb"], ["./x.glb"], ["/abs.glb"], ["a/./b.glb"], [".."], ["a//b.glb"],
    ["a/b.glb/"], [TIEF], ["a\nb.glb"], ["a.glb\n"], ["a\x00.glb"], ["a\tb.glb"],
    ["a\x7f.glb"], ["a\\b.glb"], ["ä.glb"], [""], ["x" * 201 + ".glb"],
    ["A.glb", "a.glb"], ["Ordner/a.glb", "ordner/b.glb"], ["a.glb", "a.glb"],
    ["a.glb", "a.glb/b.glb"], ["x/a.glb", "X/A.glb"],
])
def test_pfad_abgelehnt_nichts_angelegt(welt: Welt, pfade: list[str]) -> None:  # noqa: F811
    r = welt.anlegen(welt.c["admin"], [{"pfad": p, "groesse": 10} for p in pfade])
    assert r.status_code == 422, (pfade, r.text)
    _nichts_angelegt(welt)


def test_tiefe_sechs_und_leerzeichen_erlaubt(welt: Welt) -> None:  # noqa: F811
    pfad = "/".join(["Ebene 1", "b", "c", "d", "e"]) + "/Modell_v2-final.glb"
    u = welt.hochladen(welt.c["admin"], {pfad: GLB})
    assert u["dateien"][0]["pfad"] == pfad
    assert (welt.wurzel / u["id"] / "Ebene 1" / "b" / "c" / "d" / "e" /
            "Modell_v2-final.glb").read_bytes() == GLB


def test_zehntausendeins_dateien_abgelehnt(welt: Welt) -> None:  # noqa: F811
    liste = [{"pfad": f"f{i}.glb", "groesse": 1} for i in range(U.MAX_DATEIEN + 1)]
    r = welt.anlegen(welt.c["admin"], liste)
    assert r.status_code == 422
    _nichts_angelegt(welt)


def test_pruefe_pfade_direkt() -> None:
    assert U.pruefe_pfade(["a/b c/d.glb"]) == [("a", "b c", "d.glb")]
    with pytest.raises(U.UploadFehler):
        U.pruefe_pfade(["a.glb\n"])  # fullmatch: kein Zeilenende hinter dem Muster
    with pytest.raises(U.UploadFehler):
        U.pruefe_pfade([])


# --- Rechte -----------------------------------------------------------------------------


def test_ohne_anmeldung_401(welt: Welt) -> None:  # noqa: F811
    c = welt.c["anonym"]
    assert c.get("/api/v1/uploads").status_code == 401
    assert welt.anlegen(c, {"a.glb": GLB}).status_code == 401


@pytest.mark.parametrize("wer", ["leitung", "bear"])
def test_nicht_admin_404(welt: Welt, wer: str) -> None:  # noqa: F811
    c = welt.c[wer]
    assert welt.anlegen(c, {"a.glb": GLB}).status_code == 404
    assert c.get("/api/v1/uploads").status_code == 404
    assert c.get("/api/v1/importe").status_code == 404
    assert c.post("/api/v1/importe", json={"upload": "0" * 32, "typ": "neu_mesh"},
                  headers=csrf()).status_code == 404
    # kaputter Körper verrät die Route nicht
    assert c.post("/api/v1/uploads", content=b"{", headers={
        **csrf(), "Content-Type": "application/json"}).status_code == 404
    _nichts_angelegt(welt)


def test_nicht_admin_kann_fremden_upload_nicht_beschreiben(welt: Welt) -> None:  # noqa: F811
    uid = welt.anlegen(welt.c["admin"], {"a.glb": GLB}).json()["id"]
    for wer in ("leitung", "bear", "anonym"):
        r = welt.stueck(welt.c[wer], uid, 0, GLB, 0)
        assert r.status_code in (401, 404)
        assert welt.c[wer].head(f"/api/v1/uploads/{uid}/dateien/0").status_code in (401, 404)
    assert (welt.wurzel / uid / "a.glb").read_bytes() == b""


def test_lese_token_darf_nicht_schreiben(welt: Welt) -> None:  # noqa: F811
    t = welt.token("admin", "lesen")
    assert t.get("/api/v1/uploads").status_code == 200
    assert welt.anlegen(t, {"a.glb": GLB}).status_code == 403
    assert t.post("/api/v1/uploads", content=b"{",
                  headers={"Content-Type": "application/json"}).status_code == 403
    _nichts_angelegt(welt)


def test_lese_token_eines_nicht_admins_404(welt: Welt) -> None:  # noqa: F811
    t = welt.token("leitung", "lesen")
    assert welt.anlegen(t, {"a.glb": GLB}).status_code == 404


def test_token_mit_schreibrecht_fuer_skripte_und_agenten(welt: Welt) -> None:  # noqa: F811
    for t in (welt.token("admin"), welt.token("admin", agent=True)):
        u = welt.hochladen(t, {"a.glb": GLB})
        assert u["status"] == "abgeschlossen" and u["kanal"] == "api"
    agent = welt.token("admin", agent=True)
    u = welt.hochladen(agent, {"b.glb": GLB})
    r = agent.post("/api/v1/importe", json={"upload": u["id"], "typ": "neu_mesh",
                                              "optionen": {"projekt": "vom-agenten"}})
    assert r.status_code == 202 and r.json()["kanal"] == "agent"


def test_import_aus_alle_routen_404(welt_aus: Welt) -> None:  # noqa: F811
    c = welt_aus.c["admin"]
    assert c.get("/api/v1/uploads").status_code == 404
    assert c.post("/api/v1/uploads", json={"dateien": [{"pfad": "a.glb", "groesse": 1}]},
                  headers=csrf()).status_code == 404
    assert c.post("/api/v1/uploads", content=b"{", headers={
        **csrf(), "Content-Type": "application/json"}).status_code == 404
    assert c.get("/api/v1/importe").status_code == 404
    assert c.head("/api/v1/uploads/" + "0" * 32 + "/dateien/0").status_code == 404
    assert c.get("/api/sitzung").json()["import"] is False


def test_sitzung_meldet_import_nur_admins(welt: Welt) -> None:  # noqa: F811
    assert welt.c["admin"].get("/api/sitzung").json()["import"] is True
    assert welt.c["leitung"].get("/api/sitzung").json()["import"] is False


def test_csrf_noetig(welt: Welt) -> None:  # noqa: F811
    r = welt.c["admin"].post("/api/v1/uploads",
                             json={"dateien": [{"pfad": "a.glb", "groesse": 1}]})
    assert r.status_code == 403


# --- Importe ----------------------------------------------------------------------------


def _start(c: TestClient, uid: str, typ: str = "neu_mesh", **optionen: str) -> object:
    return c.post("/api/v1/importe", json={"upload": uid, "typ": typ,
                                           "optionen": {"projekt": "neu", **optionen}},
                  headers=csrf())


def test_import_einreihen_lesen_abbrechen(welt: Welt) -> None:  # noqa: F811
    c = welt.c["admin"]
    u = welt.hochladen(c, {"Sub/modell.glb": GLB})
    r = c.post("/api/v1/importe", json={"upload": u["id"], "typ": "neu_mesh", "optionen": {
        "projekt": "neu-mesh", "titel": "Neues Modell", "crs": "EPSG:25833",
        "quellHoehe": "dhhn2016"}}, headers=csrf())
    assert r.status_code == 202, r.text
    a = r.json()
    assert a["status"] == "wartend" and a["warteplatz"] == 1 and a["datei"] == "Sub/modell.glb"
    assert a["titel"] == "Neues Modell" and a["projekt"] == "neu-mesh"
    liste = c.get("/api/v1/importe").json()
    assert [x["id"] for x in liste["importe"]] == [a["id"]] and liste["worker"] == "aus"
    # Gleiche ID noch einmal → vergeben (offener Auftrag)
    assert _start(c, u["id"], projekt="neu-mesh").status_code == 409  # type: ignore[attr-defined]
    r = c.delete(f"/api/v1/importe/{a['id']}", headers=csrf())
    assert r.status_code == 202 and r.json()["status"] == "abgebrochen"
    assert c.delete(f"/api/v1/importe/{a['id']}", headers=csrf()).status_code == 409
    # Importaufträge erscheinen nie unter den Auswertungen eines Projekts
    jc = welt.jobs()
    try:
        assert auftraege.liste(jc, "neu-mesh") == []
        assert auftraege.lesen(jc, "neu-mesh", a["id"]) is None
    finally:
        jc.close()


@pytest.mark.parametrize(("typ", "optionen", "status"), [
    ("auswerten_pruefen", {}, 422),
    ("neu_wolke", {}, 422),
    ("neu_splats", {}, 422),                         # GLB ist keine Splat-Datei
    ("neu_mesh", {"projekt": "Gross"}, 422),
    ("neu_mesh", {"projekt": "-x"}, 422),
    ("neu_mesh", {"projekt": "ost"}, 409),           # vorhandenes Projekt
    ("neu_mesh", {"crs": "25833"}, 422),
    ("neu_mesh", {"crs": "EPSG:25833; rm"}, 422),
    ("neu_mesh", {"quellHoehe": "dhhn2016"}, 422),   # Höhe ohne CRS
    ("neu_mesh", {"crs": "EPSG:25833", "quellHoehe": "nn"}, 422),
    ("neu_mesh", {"titel": "a\nb"}, 422),
    ("neu_mesh", {"titel": "x" * 121}, 422),
    ("neu_mesh", {"datei": "../a.glb"}, 422),
    ("neu_mesh", {"pfad": "/etc"}, 422),             # unbekannte Option
    ("neu_mesh", {"argumente": "--x"}, 422),
])
def test_import_optionen_abgelehnt(welt: Welt, typ: str, optionen: dict[str, str],  # noqa: F811
                                   status: int) -> None:
    c = welt.c["admin"]
    u = welt.hochladen(c, {"modell.glb": GLB})
    r = c.post("/api/v1/importe", json={"upload": u["id"], "typ": typ,
                                          "optionen": {"projekt": "neu", **optionen}},
               headers=csrf())
    assert r.status_code == status, r.text
    jc = welt.jobs()
    try:
        assert jc.execute("SELECT COUNT(*) FROM auftraege").fetchone()[0] == 0
    finally:
        jc.close()


def test_import_braucht_abgeschlossenen_upload_mit_einer_datei(welt: Welt) -> None:  # noqa: F811
    c = welt.c["admin"]
    offen = welt.anlegen(c, {"a.glb": GLB}).json()["id"]
    assert _start(c, offen).status_code == 409  # type: ignore[attr-defined]
    zwei = welt.hochladen(c, {"a.glb": GLB, "b.glb": GLB})
    r = _start(c, zwei["id"])
    assert r.status_code == 422 and "genau eine Datei" in r.json()["fehler"]  # type: ignore[attr-defined]
    assert _start(c, "0" * 32).status_code == 422  # type: ignore[attr-defined]
    splats = welt.hochladen(c, {"s.spz": SPZ})
    assert _start(c, splats["id"], "neu_splats").status_code == 202  # type: ignore[attr-defined]


def test_auswertungen_nehmen_keine_importtypen(welt: Welt) -> None:  # noqa: F811
    with pytest.raises(auftraege.AuftragUngueltig):
        auftraege.pruefe("neu_mesh", {"upload": "0" * 32, "projekt": "x"})
    with pytest.raises(auftraege.AuftragUngueltig):
        auftraege.pruefe("auswerten_pruefen", {}, erlaubt=auftraege.IMPORTE)
