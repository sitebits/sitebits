"""Ende zu Ende: Upload in Stücken (API) → Importauftrag → Worker → echter `scanview-import
neu-ohne-wolke` und `verify` im isolierten `systemd-run` → Übergabe (echte Kopie als Hochladender)
→ Nachbildung des Freischaltens → Projekt im Katalog, Paket ohne Wolke.

Braucht die Import-Umgebung (`import/.venv`) und einen systemd-User-Manager; sonst übersprungen.
Die Quelldateien erzeugt das Skript der Browser-Ende-zu-Ende-Tests (synthetisch)."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from conftest import csrf
from test_isolation import _user_manager
from uploadwelt import Welt, welt  # noqa: F401

from scanview_server import auftraege
from scanview_worker import uebergabe as UE
from scanview_worker.laeufer import SystemdLaeufer
from scanview_worker.worker import Worker

REPO = Path(__file__).resolve().parents[2]
IMPORT_BIN = REPO / "import" / ".venv" / "bin"
ERZEUGE = REPO / "web" / "e2e" / "fixtures" / "ohne-wolke" / "erzeuge.py"

pytestmark = pytest.mark.skipif(
    not (IMPORT_BIN / "scanview-import").is_file() or not _user_manager(),
    reason="Import-Umgebung oder systemd-User-Manager fehlt",
)


class Freischalten:
    """Wie das root-Skript der Übergabe, nur ohne root: `pruefen` (Ziel frei?), echte Kopie in
    den Eingang (neue Inodes, als dieser Nutzer), dann statt des Freischalt-Skripts ein rename
    Eingang → Datenwurzel und Katalog neu laden, Meldung als ein Wort."""

    def __init__(self, w: Welt) -> None:
        self.w = w
        self.ende = threading.Event()
        self.t = threading.Thread(target=self._lauf, daemon=True)
        for n in ("eingang",):
            (w.tmp / n).mkdir(exist_ok=True)

    def _lauf(self) -> None:
        ueb, mel, ein = (self.w.tmp / n for n in ("uebergabe", "meldungen", "eingang"))
        data = self.w.settings.data_dir
        while not self.ende.wait(0.2):
            UE.aufraeumen(uebergabe=ueb, meldungen=mel, eingang=ein)
            for d in ueb.iterdir():
                if not UE.ID_RE.fullmatch(d.name) or (mel / d.name).exists():
                    continue
                if (data / d.name).exists():
                    UE.melden(d.name, "VORHANDEN", meldungen=mel)
                    continue
                try:
                    UE.kopieren(d.name, uebergabe=ueb, eingang=ein)
                except UE.Abgelehnt:
                    UE.melden(d.name, "KOPIE", meldungen=mel)
                    continue
                (ein / d.name).rename(data / d.name)
                self.w.client.app.state.katalog.laden()  # type: ignore[attr-defined]
                UE.melden(d.name, "OK", meldungen=mel)

    def __enter__(self) -> Freischalten:
        self.t.start()
        return self

    def __exit__(self, *_a: object) -> None:
        self.ende.set()
        self.t.join()


@pytest.fixture
def quellen(tmp_path: Path) -> Path:
    ziel = tmp_path / "quellen"
    ziel.mkdir()
    subprocess.run([str(IMPORT_BIN / "python"), "-I", str(ERZEUGE), str(ziel)], check=True,
                   timeout=120)
    return ziel


@pytest.mark.parametrize(("datei", "typ", "art"), [
    ("klein.spz", "neu_splats", "splats"), ("klein.glb", "neu_mesh", "mesh")])
def test_neues_projekt_ende_zu_ende(welt: Welt, quellen: Path, datei: str, typ: str,  # noqa: F811
                                    art: str) -> None:
    c = welt.c["admin"]
    daten = (quellen / datei).read_bytes()
    # Abbruch nach dem ersten Stück, dann fortsetzen.
    r = welt.anlegen(c, {f"Quelle/{datei}": daten})
    uid = r.json()["id"]
    welt.senden(c, uid, 0, daten, bis=1024)
    stand = int(c.head(f"/api/v1/uploads/{uid}/dateien/0").headers["upload-offset"])
    welt.senden(c, uid, 0, daten, ab=stand)
    u = c.post(f"/api/v1/uploads/{uid}/abschliessen", json={}, headers=csrf()).json()
    assert u["dateien"][0]["sha256"] == hashlib.sha256(daten).hexdigest()
    pid = f"e2e-{art}"
    r = c.post("/api/v1/importe", json={"upload": uid, "typ": typ, "optionen": {
        "projekt": pid, "titel": f"Probe {art}"}}, headers=csrf())
    assert r.status_code == 202, r.text
    aid = r.json()["id"]

    s = welt.einstellungen()
    from dataclasses import replace
    w = Worker(replace(s, import_bin=IMPORT_BIN, reserve_bytes=0),
               SystemdLaeufer(unsichtbar=(welt.settings.state_dir,)))
    with Freischalten(welt):
        w.start()
        w.schleife(einmal=True)
    w.conn.close()

    a = c.get(f"/api/v1/importe/{aid}").json()
    assert a["status"] == "fertig", a
    karte = c.get(f"/api/v1/projekte/{pid}")
    assert karte.status_code == 200, karte.text
    paket = c.get(f"/api/v1/projekte/{pid}/paket").json()
    ebenen = paket["ebenen"] if "ebenen" in paket else paket["layers"]
    arten = [e.get("art", e.get("kind")) for e in ebenen]
    assert art in arten and "pointcloud" not in arten and "punktwolke" not in arten, paket
    manifest = json.loads((welt.settings.data_dir / pid / "project.json").read_text())
    assert manifest["frame"]["crs"] == {"wkt": None, "verticalDatum": None,
                                        "declaredBy": "keine Angabe (lokal, Modellmaß)"}
    assert manifest["title"] == f"Probe {art}"
    if art == "splats":
        assert manifest["layers"][0]["frameCheck"] == "keine Wolke"
    else:
        assert manifest["layers"][0]["provenance"]["scaleVerified"] is False
    # Übergabe und Eingang leer, Upload bleibt bis zur Frist.
    jc = welt.jobs()
    try:
        assert auftraege.import_lesen(jc, aid) is not None
    finally:
        jc.close()
    assert [p.name for p in (welt.tmp / "uebergabe").iterdir()] == []
    assert (welt.wurzel / uid / "Quelle" / datei).read_bytes() == daten
    shutil.rmtree(welt.settings.data_dir / pid)


# --- Punktwolke: convert + verify mit PDAL/untwine aus der gelockten Umgebung (kein Docker) -----

PDAL_UMGEBUNG = Path(__import__("os").environ.get("SCANVIEW_TEST_PDAL_UMGEBUNG", "/nicht/gesetzt"))
_LAZ_SKRIPT = r"""
import sys, numpy as np, laspy, pyproj
h = laspy.LasHeader(point_format=7, version="1.4")
h.scales = [0.001] * 3
h.offsets = [390000.0, 5800000.0, 30.0]
h.add_crs(pyproj.CRS.from_epsg(25833))
rng = np.random.default_rng(5)
n = 50_000
las = laspy.LasData(h)
# Fläche statt Würfel (wie ein Scan), Farbe 0 bis 255 in den 16-bit-Feldern (ODM-Art).
las.x = 390000 + rng.uniform(0, 40, n)
las.y = 5800000 + rng.uniform(0, 40, n)
las.z = 30 + 0.05 * (las.x - 390000)
las.red = rng.integers(0, 256, n); las.green = las.red; las.blue = las.red
las.write(sys.argv[1])
"""


@pytest.mark.skipif(not (PDAL_UMGEBUNG / "bin" / "untwine").is_file(),
                    reason="SCANVIEW_TEST_PDAL_UMGEBUNG (Präfix der pixi-Umgebung) nicht gesetzt")
def test_punktwolke_ende_zu_ende(welt: Welt, tmp_path: Path) -> None:  # noqa: F811
    c = welt.c["admin"]
    laz = tmp_path / "scan.laz"
    subprocess.run([str(IMPORT_BIN / "python"), "-I", "-c", _LAZ_SKRIPT, str(laz)], check=True,
                   timeout=120)
    daten = laz.read_bytes()
    r = welt.anlegen(c, {"Scan/scan.laz": daten})
    uid = r.json()["id"]
    welt.senden(c, uid, 0, daten, bis=4096)
    stand = int(c.head(f"/api/v1/uploads/{uid}/dateien/0").headers["upload-offset"])
    welt.senden(c, uid, 0, daten, ab=stand)
    u = c.post(f"/api/v1/uploads/{uid}/abschliessen", json={}, headers=csrf()).json()
    assert u["dateien"][0]["sha256"] == hashlib.sha256(daten).hexdigest()
    pid = "e2e-wolke"
    r = c.post("/api/v1/importe", json={"upload": uid, "typ": "neu_wolke", "optionen": {
        "projekt": pid, "titel": "Probe Wolke", "rgb8bit": True,
        "quellHoehe": "ellipsoidal-etrs89", "zielHoehe": "dhhn2016"}}, headers=csrf())
    assert r.status_code == 202, r.text
    aid = r.json()["id"]

    from dataclasses import replace
    s = welt.einstellungen()
    w = Worker(replace(s, import_bin=IMPORT_BIN, reserve_bytes=0, pdal_umgebung=PDAL_UMGEBUNG),
               SystemdLaeufer(unsichtbar=(welt.settings.state_dir,)))
    with Freischalten(welt):
        w.start()
        w.schleife(einmal=True)
    w.conn.close()

    a = c.get(f"/api/v1/importe/{aid}").json()
    assert a["status"] == "fertig", a
    assert c.get(f"/api/v1/projekte/{pid}").status_code == 200
    paket = welt.settings.data_dir / pid
    manifest = json.loads((paket / "project.json").read_text())
    assert manifest["title"] == "Probe Wolke"
    wolke = next(e for e in manifest["layers"] if e["kind"] == "pointcloud")
    assert wolke["pointCount"] == 50_000
    hoehe = wolke["sourceOptions"]["hoehe"]
    assert (hoehe["quelle"], hoehe["ziel"]) == ("ellipsoidal-etrs89", "dhhn2016")
    assert (paket / "cloud.copc.laz").read_bytes()[:4] == b"LASF"
    assert [p.name for p in (welt.tmp / "uebergabe").iterdir()] == []
    shutil.rmtree(paket)
