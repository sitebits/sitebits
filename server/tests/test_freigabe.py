"""F20: Freigabe von Panoramen und Fotos an Dateien gebunden (Negativtests der Etappe Mitmachen).

Abnahme: ausgetauschtes Bild nach Freigabe → gesperrt; Original- und Sicherungspfad → 404; jede
Panoramastufe geprüft; Bearbeiter sehen weiter den Prüfstand."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest
from conftest import csrf
from fastapi.testclient import TestClient
from pakete import lege_paket
from test_authz import authz

from scanview_server import db, freigabe, konten, projektverwaltung, teilen

SEITEN = ("px", "nx", "py", "ny", "pz", "nz")
STUFEN = (512, 1024)


def _pano_paket(data: Path, pid: str = "hof", *, anonym: bool = True) -> Path:
    d = lege_paket(data, pid)
    stations = []
    hashes: dict[str, str] = {}
    for sid in ("S001", "S002"):
        for f in SEITEN:
            for lv in STUFEN:
                rel = f"panos/{sid}/{f}/{lv}.jpg"
                (d / rel).parent.mkdir(parents=True, exist_ok=True)
                inhalt = b"\xff\xd8anonym " + rel.encode()
                (d / rel).write_bytes(inhalt)
                hashes[rel] = hashlib.sha256(inhalt).hexdigest()
        stations.append({"id": sid, "pose": {"translation": [0, 0, 0],
                                             "rotationQuat": [1, 0, 0, 0]},
                         "panorama": {"kind": "cube", "faceSize": 1024, "levels": list(STUFEN),
                                      "levelFile": "{level}.jpg",
                                      "faces": {f: f"panos/{sid}/{f}" for f in SEITEN}}})
    out: dict[str, object] = {"schemaVersion": 2, "stations": stations}
    if anonym:
        out["anonymization"] = {"required": True, "status": "reviewed", "tool": "pano-anonym",
                                "manifestSha256": "a" * 64, "files": hashes}
    (d / "stations.json").write_text(json.dumps(out))
    # Original und Sicherung, wie sie ein Mensch oder ein abgebrochener Lauf hinterlässt.
    for rel in ("panos/S001/original.jpg", "panos.alt/S001/px/512.jpg",
                "panos-original/S001.jpg", "Panos/S001/px/512.jpg"):
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(b"\xff\xd8original mit Personen")
    return d


def _konto(conn: sqlite3.Connection, email: str, grants: dict[str, str],
           admin: bool = False) -> tuple[konten.Nutzer, str]:
    t = konten.einladung_anlegen(conn, email=email, name=email.split("@")[0], grants=grants,
                                 is_admin=admin)
    n = konten.einladung_einloesen(conn, t, lambda _c, _u: None)
    return n, konten.sitzung_anlegen(conn, n.id)


class Welt:
    def __init__(self, client: TestClient, anonym: bool = True) -> None:
        self.c = client
        data = Path(client.app.state.settings.data_dir)  # type: ignore[attr-defined]
        self.paket = _pano_paket(data, anonym=anonym)
        client.app.state.katalog.laden()  # type: ignore[attr-defined]
        self.conn = db.connect(client.app.state.settings.db_path)  # type: ignore[attr-defined]
        self.sitzung: dict[str, str] = {}
        self.nutzer: dict[str, konten.Nutzer] = {}
        for rolle in ("betrachter", "rueckmelder", "bearbeiter", "projektleitung"):
            n, s = _konto(self.conn, f"{rolle}@x.test", {"hof": rolle})
            self.nutzer[rolle], self.sitzung[rolle] = n, s
        n, s = _konto(self.conn, "admin@x.test", {}, admin=True)
        self.nutzer["admin"], self.sitzung["admin"] = n, s

    def als(self, rolle: str) -> TestClient:
        self.c.cookies.set("__Host-scanview", self.sitzung[rolle])
        return self.c

    def alle_stufen(self) -> list[str]:
        return [f"/data/hof/panos/{s}/{f}/{lv}.jpg" for s in ("S001", "S002") for f in SEITEN
                for lv in STUFEN]


@pytest.fixture
def welt(client: TestClient) -> Welt:
    return Welt(client)


SPERRPFADE = ["/data/hof/panos/S001/original.jpg", "/data/hof/panos.alt/S001/px/512.jpg",
              "/data/hof/panos-original/S001.jpg", "/data/hof/Panos/S001/px/512.jpg"]


@pytest.mark.parametrize("rolle", ["betrachter", "rueckmelder"])
def test_ohne_freigabe_jede_stufe_404(welt: Welt, rolle: str) -> None:
    c = welt.als(rolle)
    for pfad in welt.alle_stufen():
        assert authz(c, pfad) == 404, pfad
    # Keine Pixel: Standpunkte und der Rest des Pakets bleiben sichtbar.
    assert authz(c, "/data/hof/stations.json") == 200
    assert authz(c, "/data/hof/cloud.copc.laz") == 200


@pytest.mark.parametrize("rolle", ["bearbeiter", "projektleitung", "admin"])
def test_bearbeiter_sehen_pruefstand(welt: Welt, rolle: str) -> None:
    c = welt.als(rolle)
    for pfad in [*welt.alle_stufen(), *SPERRPFADE]:
        assert authz(c, pfad) == 200, pfad


def _freigeben(welt: Welt, art: str = "anonymisiert", rolle: str = "projektleitung") -> int:
    r = welt.als(rolle).post("/api/v1/projekte/hof/freigaben/panos", json={"art": art},
                             headers=csrf())
    return r.status_code


def test_freigabe_gibt_jede_stufe_frei_aber_nie_original_und_sicherung(welt: Welt) -> None:
    assert _freigeben(welt) == 201
    for rolle in ("betrachter", "rueckmelder"):
        c = welt.als(rolle)
        for pfad in welt.alle_stufen():
            assert authz(c, pfad) == 200, pfad
        for pfad in SPERRPFADE:
            assert authz(c, pfad) == 404, pfad


def test_ausgetauschtes_bild_nach_freigabe_gesperrt(welt: Welt) -> None:
    assert _freigeben(welt) == 201
    c = welt.als("betrachter")
    ziel = welt.paket / "panos/S002/nz/1024.jpg"
    assert authz(c, "/data/hof/panos/S002/nz/1024.jpg") == 200
    st = ziel.stat()
    # Gleiche Größe, mtime zurückgesetzt: nur Inhalt und ctime verraten den Tausch.
    alt = ziel.read_bytes()
    ziel.write_bytes(alt[:-1] + bytes([alt[-1] ^ 1]))
    os.utime(ziel, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert authz(c, "/data/hof/panos/S002/nz/1024.jpg") == 404
    assert authz(c, "/data/hof/panos/S002/nz/512.jpg") == 200
    assert authz(welt.als("bearbeiter"), "/data/hof/panos/S002/nz/1024.jpg") == 200
    r = welt.als("bearbeiter").get("/api/v1/projekte/hof/freigaben")
    panos = next(e for e in r.json()["ebenen"] if e["ebene"] == "panos")
    assert "abweichend" not in panos["freigabe"]  # ohne pruefen kein Hashen
    r = welt.als("bearbeiter").get("/api/v1/projekte/hof/freigaben?pruefen=true")
    panos = next(e for e in r.json()["ebenen"] if e["ebene"] == "panos")
    assert panos["freigabe"]["abweichend"] == 1


def test_ausgetauscht_durch_umbenennen_gesperrt(welt: Welt) -> None:
    """Neue Inode mit gleichem Inhalt einer anderen Stufe: Hash passt nicht zur Freigabe."""
    assert _freigeben(welt) == 201
    andere = welt.paket / "panos/S001/px/512.jpg"
    ziel = welt.paket / "panos/S001/px/1024.jpg"
    os.replace(andere, ziel)
    assert authz(welt.als("betrachter"), "/data/hof/panos/S001/px/1024.jpg") == 404


def test_ruecknahme_wirkt_sofort(welt: Welt) -> None:
    assert _freigeben(welt) == 201
    assert authz(welt.als("betrachter"), welt.alle_stufen()[0]) == 200
    r = welt.als("projektleitung").delete("/api/v1/projekte/hof/freigaben/panos",
                                          headers=csrf())
    assert r.status_code == 200 and r.json()["freigabe"] is None
    assert authz(welt.als("betrachter"), welt.alle_stufen()[0]) == 404


def test_anonymisiert_nur_mit_passender_dateiliste(client: TestClient) -> None:
    w = Welt(client)
    (w.paket / "panos/S001/px/512.jpg").write_bytes(b"\xff\xd8vor der Freigabe getauscht")
    assert _freigeben(w) == 409
    assert freigabe.aktiv(w.conn, "hof", "panos") is None


def test_anonymisiert_nicht_ohne_geprueften_block(client: TestClient) -> None:
    w = Welt(client, anonym=False)
    assert _freigeben(w) == 409
    # „personenfrei“ erklärt nur ein Admin.
    assert _freigeben(w, "personenfrei") == 409
    assert _freigeben(w, "personenfrei", rolle="admin") == 201
    assert authz(w.als("betrachter"), w.alle_stufen()[-1]) == 200


@pytest.mark.parametrize("rolle", ["betrachter", "rueckmelder", "bearbeiter"])
def test_freigeben_nur_leitung(welt: Welt, rolle: str) -> None:
    assert _freigeben(welt, rolle=rolle) in (403, 404)
    assert freigabe.aktiv(welt.conn, "hof", "panos") is None


def test_freigeben_nicht_per_token(welt: Welt) -> None:
    from scanview_server import apitoken

    token, _ = apitoken.erzeugen(welt.conn, welt.nutzer["projektleitung"].id, "t",
                                 "schreiben", 30)
    welt.c.cookies.clear()
    r = welt.c.post("/api/v1/projekte/hof/freigaben/panos", json={"art": "anonymisiert"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_altpaket_ohne_block_sichtbar_bis_schalter(client: TestClient) -> None:
    w = Welt(client, anonym=False)
    c = w.als("betrachter")
    assert authz(c, w.alle_stufen()[0]) == 200
    projektverwaltung.einstellungen_aendern(w.conn, "hof", rueckmeldungen=None,
                                            export_bearbeiter=None,
                                            panoramen_nur_freigegeben=True,
                                            von=w.nutzer["projektleitung"].id)
    assert authz(w.als("betrachter"), w.alle_stufen()[0]) == 404
    # Original/Sicherung im Altpaket sind mit Schalter ebenfalls zu.
    assert authz(w.als("betrachter"), SPERRPFADE[1]) == 404


def test_neu_eingerichtet_schalter_an_bestand_aus(client: TestClient) -> None:
    w = Welt(client, anonym=False)
    assert projektverwaltung.einstellungen(w.conn, "hof").panoramen_nur_freigegeben is False
    projektverwaltung.einrichten(w.conn, "hof", "bauprojekt", von=w.nutzer["admin"].id)
    assert projektverwaltung.einstellungen(w.conn, "hof").panoramen_nur_freigegeben is True


def test_nebenbei_angelegte_zeile_laesst_bestand_sichtbar(client: TestClient) -> None:
    """Erste Kategorienänderung eines Bestandsprojekts legt die Zeile an — ohne Panoramasperre."""
    w = Welt(client, anonym=False)
    alle = [(k, True) for k in projektverwaltung.VORLAGEN[projektverwaltung.STANDARD_VORLAGE]]
    projektverwaltung.kategorien_setzen(w.conn, "hof", [*alle, ("Neu", True)],
                                        von=w.nutzer["projektleitung"].id)
    assert projektverwaltung.einstellungen(w.conn, "hof").panoramen_nur_freigegeben is False
    assert authz(w.als("betrachter"), w.alle_stufen()[0]) == 200


def test_paketuebersicht_ohne_panoramen_fuer_betrachter(welt: Welt) -> None:
    def ebenen(rolle: str) -> set[str]:
        r = welt.als(rolle).get("/api/v1/projekte/hof/paket")
        return {e["art"] for e in r.json()["ebenen"]}

    assert "panoramas" not in ebenen("betrachter")
    assert "panoramas" in ebenen("bearbeiter")
    assert _freigeben(welt) == 201
    assert "panoramas" in ebenen("betrachter")


def test_gast_link_nie_panoramen() -> None:
    assert teilen._paketpfad("project.json", "panos/S001/px/512.jpg") is None
    assert teilen._paketpfad("project.json", "panos.alt/S001/px/512.jpg") is None


def test_freigabe_dateien_unveraenderlich(welt: Welt) -> None:
    assert _freigeben(welt) == 201
    with pytest.raises(sqlite3.IntegrityError):
        welt.conn.execute("UPDATE freigabe_dateien SET sha256=?", ("b" * 64,))
    with pytest.raises(sqlite3.IntegrityError):
        welt.conn.execute("DELETE FROM freigabe_dateien")


def test_freigabe_im_protokoll(welt: Welt) -> None:
    assert _freigeben(welt) == 201
    r = welt.conn.execute("SELECT art, objekt_id, nachher FROM ereignisse"
                          " WHERE art LIKE 'freigabe.%'").fetchall()
    assert [(x["art"], x["objekt_id"]) for x in r] == [("freigabe.erteilt", "panos")]
    assert json.loads(r[0]["nachher"])["dateien"] == len(welt.alle_stufen())


def test_lesen_per_token_erlaubt(welt: Welt) -> None:
    from scanview_server import apitoken

    token, _ = apitoken.erzeugen(welt.conn, welt.nutzer["bearbeiter"].id, "t", "lesen", 30)
    welt.c.cookies.clear()
    r = welt.c.get("/api/v1/projekte/hof/freigaben",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    panos = next(e for e in r.json()["ebenen"] if e["ebene"] == "panos")
    assert panos["anonymisierung"]["geprueft"] is True and panos["torAktiv"] is True
    assert panos["dateien"] == len(welt.alle_stufen())


def test_projektkarte_meldet_panoramen_sichtbar(welt: Welt) -> None:
    assert welt.als("betrachter").get("/api/v1/projekte/hof").json()["panoramen_sichtbar"] is False
    assert welt.als("bearbeiter").get("/api/v1/projekte/hof").json()["panoramen_sichtbar"] is True
    assert _freigeben(welt) == 201
    assert welt.als("betrachter").get("/api/v1/projekte/hof").json()["panoramen_sichtbar"] is True


def test_stand_ohne_konto_id(welt: Welt) -> None:
    assert _freigeben(welt) == 201
    r = welt.als("bearbeiter").get("/api/v1/projekte/hof/freigaben?pruefen=true").json()
    panos = next(e for e in r["ebenen"] if e["ebene"] == "panos")
    assert set(panos["freigabe"]) == {"art", "dateien", "werkzeug", "manifestSha256",
                                      "freigegebenAm", "abweichend"}
