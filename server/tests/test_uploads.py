"""Hochladen in Stücken: Fortsetzen, Prüfsummen, Stückgrenzen, Quote, Aufräumen, Signaturen."""

from __future__ import annotations

import hashlib
import os
import time

import pytest
from conftest import csrf
from uploadwelt import GLB, PLY, SPZ, STUECK, Welt, digest, welt  # noqa: F401

from scanview_server import uploads as U
from scanview_server.routen_uploads import aufraeumen


def _daten(n: int, saat: int = 1) -> bytes:
    """Synthetische Datei mit GLB-Kopf und nicht wiederholendem Inhalt."""
    rumpf = b"".join(hashlib.sha256(f"{saat}:{i}".encode()).digest() for i in range(n // 32 + 1))
    return (GLB[:12] + rumpf)[:n]


def test_fortsetzen_nach_abbruch_gleiche_sha256(welt: Welt) -> None:  # noqa: F811
    daten = _daten(5 * STUECK + 77)
    c = welt.c["admin"]
    r = welt.anlegen(c, {"modell.glb": daten})
    assert r.status_code == 201, r.text
    u = r.json()
    assert u["stueckBytes"] == STUECK and u["dateien"][0]["stand"] == 0
    uid = u["id"]
    # Abbruch nach zwei Stücken (Browser geschlossen), Stand über HEAD.
    welt.senden(c, uid, 0, daten, bis=2 * STUECK)
    h = c.head(f"/api/v1/uploads/{uid}/dateien/0")
    assert h.status_code == 204 and h.headers["upload-offset"] == str(2 * STUECK)
    assert h.headers["upload-length"] == str(len(daten))
    # Doppelt gesendetes Stück (Wiederholung nach Zeitüberschreitung): nicht doppelt geschrieben.
    r = welt.stueck(c, uid, 0, daten, STUECK)
    assert r.status_code == 409 and r.headers["upload-offset"] == str(2 * STUECK)
    welt.senden(c, uid, 0, daten, ab=int(h.headers["upload-offset"]))
    r = c.post(f"/api/v1/uploads/{uid}/abschliessen",
               json={"dateien": [{"nr": 0, "sha256": hashlib.sha256(daten).hexdigest()}]},
               headers=csrf())
    assert r.status_code == 200, r.text
    d = r.json()["dateien"][0]
    assert d["sha256"] == hashlib.sha256(daten).hexdigest() and d["art"] == "glb"
    assert r.json()["status"] == "abgeschlossen"
    assert (welt.wurzel / uid / "modell.glb").read_bytes() == daten


def test_falsche_pruefsumme_des_stuecks_wird_verworfen(welt: Welt) -> None:  # noqa: F811
    daten = _daten(3 * STUECK)
    c = welt.c["admin"]
    uid = welt.anlegen(c, {"a.glb": daten}).json()["id"]
    welt.senden(c, uid, 0, daten, bis=STUECK)
    r = welt.stueck(c, uid, 0, daten, STUECK, pruef=digest(b"anders"))
    assert r.status_code == 422 and "Prüfsumme" in r.json()["fehler"]
    assert os.path.getsize(welt.wurzel / uid / "a.glb") == STUECK  # zurückgekürzt
    assert c.head(f"/api/v1/uploads/{uid}/dateien/0").headers["upload-offset"] == str(STUECK)
    # ohne Content-Digest: Absage
    r = c.put(f"/api/v1/uploads/{uid}/dateien/0", content=daten[STUECK:2 * STUECK], headers={
        **csrf(), "Content-Range": f"bytes {STUECK}-{2 * STUECK - 1}/{len(daten)}"})
    assert r.status_code == 422


def test_falsche_sha256_beim_abschliessen(welt: Welt) -> None:  # noqa: F811
    daten = _daten(STUECK + 5)
    c = welt.c["admin"]
    uid = welt.anlegen(c, {"a.glb": daten}).json()["id"]
    welt.senden(c, uid, 0, daten)
    r = c.post(f"/api/v1/uploads/{uid}/abschliessen",
               json={"dateien": [{"nr": 0, "sha256": "0" * 64}]}, headers=csrf())
    assert r.status_code == 422 and "SHA-256" in r.json()["fehler"]
    assert c.get(f"/api/v1/uploads/{uid}").json()["status"] == "offen"


@pytest.mark.parametrize("range_kopf", [
    "bytes 0-99/3072",            # nicht volle Stückgröße
    "bytes 1-1024/3072",          # nicht an der Stückgrenze
    "bytes 0-1023/9999",          # andere Gesamtgröße als angekündigt
    "bytes 2048-3072/3072",       # über das Ende
    "0-1023/3072", "",            # Form
])
def test_stueck_ausserhalb_der_ankuendigung(welt: Welt, range_kopf: str) -> None:  # noqa: F811
    daten = _daten(3 * STUECK)
    c = welt.c["admin"]
    uid = welt.anlegen(c, {"a.glb": daten}).json()["id"]
    teil = daten[:STUECK]
    r = c.put(f"/api/v1/uploads/{uid}/dateien/0", content=teil,
              headers={**csrf(), "Content-Range": range_kopf, "Content-Digest": digest(teil)})
    assert r.status_code == 422, r.text
    assert os.path.getsize(welt.wurzel / uid / "a.glb") == 0


def test_stueck_laenger_oder_kuerzer_als_range(welt: Welt) -> None:  # noqa: F811
    daten = _daten(3 * STUECK)
    c = welt.c["admin"]
    uid = welt.anlegen(c, {"a.glb": daten}).json()["id"]
    zu_lang = daten[:STUECK] + b"x"
    r = c.put(f"/api/v1/uploads/{uid}/dateien/0", content=zu_lang, headers={
        **csrf(), "Content-Range": f"bytes 0-{STUECK - 1}/{len(daten)}",
        "Content-Digest": digest(zu_lang)})
    assert r.status_code == 413
    kurz = daten[:STUECK - 1]
    r = c.put(f"/api/v1/uploads/{uid}/dateien/0", content=kurz, headers={
        **csrf(), "Content-Range": f"bytes 0-{STUECK - 1}/{len(daten)}",
        "Content-Digest": digest(kurz)})
    assert r.status_code == 422
    assert os.path.getsize(welt.wurzel / uid / "a.glb") == 0
    assert c.get(f"/api/v1/uploads/{uid}").json()["dateien"][0]["stand"] == 0


def test_abschliessen_erst_wenn_vollstaendig(welt: Welt) -> None:  # noqa: F811
    daten = _daten(2 * STUECK)
    c = welt.c["admin"]
    uid = welt.anlegen(c, {"a.glb": daten}).json()["id"]
    welt.senden(c, uid, 0, daten, bis=STUECK)
    r = c.post(f"/api/v1/uploads/{uid}/abschliessen", json={}, headers=csrf())
    assert r.status_code == 409


def test_quote_vor_der_annahme(welt: Welt) -> None:  # noqa: F811
    c = welt.c["admin"]
    quote = welt.settings.upload_quote_bytes
    r = welt.anlegen(c, [{"pfad": "a.glb", "groesse": quote + 1}])
    assert r.status_code == 413 and "Quote" in r.json()["fehler"]
    assert list(welt.wurzel.iterdir()) == []
    assert welt.anlegen(c, [{"pfad": "a.glb", "groesse": quote - 10}]).status_code == 201
    # Belegte Quote zählt angekündigte Größen, auch halber Uploads.
    assert welt.anlegen(c, [{"pfad": "b.glb", "groesse": 11}]).status_code == 413
    r = c.get("/api/v1/uploads")
    assert r.json()["belegtBytes"] == quote - 10 and r.json()["quoteBytes"] == quote


def test_aufraeumen_nach_24_stunden(welt: Welt) -> None:  # noqa: F811
    c = welt.c["admin"]
    daten = _daten(STUECK + 1)
    uid = welt.anlegen(c, {"a.glb": daten}).json()["id"]
    welt.senden(c, uid, 0, daten, bis=STUECK)
    frisch = welt.anlegen(c, {"b.glb": daten}).json()["id"]
    conn, jc = welt.conn(), welt.jobs()
    try:
        jetzt = int(time.time())
        assert aufraeumen(welt.wurzel, conn, jc, now=jetzt + U.FRIST_S - 60) == 0
        conn.execute("UPDATE uploads SET geaendert_am=? WHERE id=?", (jetzt - 10, frisch))
        assert aufraeumen(welt.wurzel, conn, jc, now=jetzt + U.FRIST_S + 1) == 2
        assert sorted(p.name for p in welt.wurzel.iterdir()) == []
        assert conn.execute("SELECT COUNT(*) FROM upload_dateien").fetchone()[0] == 0
        # Ordner ohne Eintrag (abgebrochenes Anlegen) verschwinden ebenfalls.
        (welt.wurzel / ("f" * 32)).mkdir()
        aufraeumen(welt.wurzel, conn, jc)
        assert not (welt.wurzel / ("f" * 32)).exists()
    finally:
        conn.close()
        jc.close()


def test_aufraeumen_laesst_upload_eines_offenen_imports(welt: Welt) -> None:  # noqa: F811
    c = welt.c["admin"]
    u = welt.hochladen(c, {"modell.glb": GLB})
    r = c.post("/api/v1/importe", json={"upload": u["id"], "typ": "neu_mesh",
                                          "optionen": {"projekt": "neu-mesh"}}, headers=csrf())
    assert r.status_code == 202, r.text
    conn, jc = welt.conn(), welt.jobs()
    try:
        assert aufraeumen(welt.wurzel, conn, jc, now=int(time.time()) + U.FRIST_S + 1) == 0
        assert (welt.wurzel / u["id"]).is_dir()
    finally:
        conn.close()
        jc.close()
    assert c.delete(f"/api/v1/uploads/{u['id']}", headers=csrf()).status_code == 409


def test_loeschen_raeumt_auf(welt: Welt) -> None:  # noqa: F811
    c = welt.c["admin"]
    uid = welt.anlegen(c, {"x/y/a.glb": GLB}).json()["id"]
    assert (welt.wurzel / uid / "x" / "y" / "a.glb").is_file()
    assert c.delete(f"/api/v1/uploads/{uid}", headers=csrf()).status_code == 204
    assert not (welt.wurzel / uid).exists()
    assert c.get(f"/api/v1/uploads/{uid}").status_code == 404


@pytest.mark.parametrize(("name", "inhalt"), [
    ("a.glb", PLY), ("a.ply", GLB), ("a.spz", GLB), ("a.laz", GLB), ("a.e57", GLB),
    ("a.obj", b"v 1 2 3\n\0"), ("a.png", GLB), ("a.splat", b"\0" * 33),
    ("a.glb", b"glTF" + (1).to_bytes(4, "little") + b"\0" * 8),
])
def test_signatur_passt_nicht_zur_endung(welt: Welt, name: str, inhalt: bytes) -> None:  # noqa: F811
    c = welt.c["admin"]
    uid = welt.anlegen(c, {name: inhalt}).json()["id"]
    welt.senden(c, uid, 0, inhalt)
    r = c.post(f"/api/v1/uploads/{uid}/abschliessen", json={}, headers=csrf())
    assert r.status_code == 422 and "passt nicht zur Endung" in r.json()["fehler"]
    assert c.get(f"/api/v1/uploads/{uid}").json()["status"] == "offen"


@pytest.mark.parametrize(("name", "inhalt", "art"), [
    ("a.glb", GLB, "glb"), ("A.PLY", PLY, "ply"), ("a.spz", SPZ, "spz"),
    ("a.splat", b"\1" * 64, "splat"), ("w.laz", b"LASF" + b"\0" * 300, "laz"),
    ("w.e57", b"ASTM-E57" + b"\0" * 100, "e57"), ("m.obj", b"v 1 2 3\n", "obj"),
])
def test_signatur_erkannt(welt: Welt, name: str, inhalt: bytes, art: str) -> None:  # noqa: F811
    u = welt.hochladen(welt.c["admin"], {name: inhalt})
    assert u["dateien"][0]["art"] == art


@pytest.mark.parametrize("name", ["daten.zip", "a.7z", "x.tar", "ohne", "a.gltf", "a.exe"])
def test_endung_vor_der_annahme(welt: Welt, name: str) -> None:  # noqa: F811
    r = welt.anlegen(welt.c["admin"], [{"pfad": name, "groesse": 10}])
    assert r.status_code == 422
    if name.endswith(("zip", "7z", "tar")):
        assert "Ordner entpacken" in r.json()["fehler"]
    assert list(welt.wurzel.iterdir()) == []


def test_dateien_haben_rechte_fuer_den_worker(welt: Welt) -> None:  # noqa: F811
    os.umask(0o077)  # wie der Webdienst (UMask=0077)
    try:
        uid = welt.anlegen(welt.c["admin"], {"o/a.glb": GLB}).json()["id"]
    finally:
        os.umask(0o022)
    assert (welt.wurzel / uid).stat().st_mode & 0o777 == 0o750
    assert (welt.wurzel / uid / "o").stat().st_mode & 0o777 == 0o750
    assert (welt.wurzel / uid / "o" / "a.glb").stat().st_mode & 0o777 == 0o640


def test_kein_pfad_ausserhalb_ueber_symlink(welt: Welt) -> None:  # noqa: F811
    """Ersetzt jemand einen Ordner im Upload durch einen Symlink, schreibt kein Stück hindurch."""
    c = welt.c["admin"]
    uid = welt.anlegen(c, {"o/a.glb": GLB}).json()["id"]
    ziel = welt.tmp / "draussen"
    (ziel).mkdir()
    (ziel / "a.glb").write_bytes(b"")
    (welt.wurzel / uid / "o" / "a.glb").unlink()
    (welt.wurzel / uid / "o").rmdir()
    (welt.wurzel / uid / "o").symlink_to(ziel)
    r = welt.stueck(c, uid, 0, GLB, 0)
    assert r.status_code >= 400
    assert (ziel / "a.glb").read_bytes() == b""
