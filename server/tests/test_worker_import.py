"""Worker: neue Projekte aus Uploads (Test-Läufer statt Prozess) und die Übergabe an das
Freischalten (Meldung als ein Wort)."""

from __future__ import annotations

import os
import threading

import pytest
from conftest import csrf
from uploadwelt import GLB, SPZ, Welt, baut_paket, welt, ziel_von  # noqa: F401

from scanview_server import auftraege
from scanview_worker import worker as W
from scanview_worker.laeufer import Ergebnis, Lauf, TestLaeufer


class Freischalten:
    """Nachbildung der root-Übergabe: wartet auf `<übergabe>/<pid>`, legt eine Meldung ab."""

    def __init__(self, w: Welt, wort: str = "OK") -> None:
        self.w, self.wort = w, wort
        self.gesehen: list[str] = []
        self._ende = threading.Event()
        self.t = threading.Thread(target=self._lauf, daemon=True)

    def _lauf(self) -> None:
        ueb, mel = self.w.tmp / "uebergabe", self.w.tmp / "meldungen"
        while not self._ende.wait(0.05):
            for d in ueb.iterdir():
                if not d.name.startswith(".") and not (mel / d.name).exists():
                    self.gesehen.append(d.name)
                    (mel / d.name).write_text(f"{self.wort}\n")

    def __enter__(self) -> Freischalten:
        self.t.start()
        return self

    def __exit__(self, *_a: object) -> None:
        self._ende.set()
        self.t.join()


def _rechnen(w: Welt, aktion: object = baut_paket, laeufer: TestLaeufer | None = None,
             ) -> TestLaeufer:
    wk, t = w.worker(aktion, laeufer)
    wk.start()
    wk.schleife(einmal=True)
    wk.conn.close()
    return t


def _auftrag(w: Welt, aid: str) -> auftraege.Auftrag:
    jc = w.jobs()
    try:
        a = auftraege.import_lesen(jc, aid)
    finally:
        jc.close()
    assert a is not None
    return a


def test_splats_werden_gebaut_geprueft_und_uebergeben(welt: Welt) -> None:  # noqa: F811
    aid = welt.import_starten(welt.c["admin"], {"quelle/scan.spz": SPZ}, "neu_splats",
                              projekt="neu-splats", titel="Probe Splats", crs="EPSG:25833")
    with Freischalten(welt) as f:
        t = _rechnen(welt)
    a = _auftrag(welt, aid)
    assert a.status == "fertig" and a.urteil == "freigeschaltet", a
    assert f.gesehen == ["neu-splats"]
    konv, verify = t.laeufe
    assert konv.argv[1:3] == ["neu-ohne-wolke", str(welt.wurzel / a.optionen["upload"] / "quelle"
                                                    / "scan.spz")]
    assert konv.argv[4:] == ["--art", "splats", "--titel", "Probe Splats", "--crs", "EPSG:25833"]
    assert verify.argv[1] == "verify" and verify.name.endswith("-verify")
    upload = welt.wurzel / a.optionen["upload"]
    for lauf in (konv, verify):
        assert lauf.lesen == (upload,)
        assert set(lauf.verbergen) == {welt.settings.data_dir, welt.settings.berichte_dir,
                                       welt.wurzel, welt.tmp / "uebergabe"}
        assert lauf.arbeit.parent == welt.tmp / "uebergabe"
        assert lauf.grenzen.ram_mb == auftraege.BUDGETS["neu_splats"].ram_mb
    # Arbeitsordner und übergebenes Paket sind weg; die Meldung räumt die Übergabe selbst.
    assert [p.name for p in (welt.tmp / "uebergabe").iterdir()] == []
    assert welt.c["admin"].get(f"/api/v1/importe/{aid}").json()["status"] == "fertig"


def test_mesh_argumente_mit_hoehe(welt: Welt) -> None:  # noqa: F811
    welt.import_starten(welt.c["admin"], {"m.glb": GLB}, "neu_mesh", projekt="neu-mesh",
                        crs="EPSG:25832", quellHoehe="ellipsoidal-etrs89", zielHoehe="dhhn2016")
    with Freischalten(welt):
        t = _rechnen(welt)
    assert t.laeufe[0].argv[4:] == [
        "--art", "mesh", "--titel", "neu-mesh", "--crs", "EPSG:25832",
        "--quell-hoehensystem", "ellipsoidal-etrs89", "--ziel-hoehensystem", "dhhn2016"]


def test_eingabefehler_meldet_letzte_zeile_ohne_pfade(welt: Welt) -> None:  # noqa: F811
    def abgelehnt(lauf: Lauf) -> Ergebnis:
        lauf.protokoll.write_text("Warnung\nAbgelehnt: Texturierte Meshes werden hier noch nicht "
                                  "angenommen.\n")
        return Ergebnis("exit", 2, 0.1)

    aid = welt.import_starten(welt.c["admin"], {"m.glb": GLB}, "neu_mesh", projekt="neu-mesh")
    _rechnen(welt, abgelehnt)
    a = _auftrag(welt, aid)
    assert a.status == "fehlgeschlagen"
    assert a.fehler == "Texturierte Meshes werden hier noch nicht angenommen."


def test_meldung_mit_pfad_wird_nicht_weitergegeben(welt: Welt) -> None:  # noqa: F811
    def abgelehnt(lauf: Lauf) -> Ergebnis:
        lauf.protokoll.write_text("Abgelehnt: /srv/scanview/uploads/geheim kaputt\n")
        return Ergebnis("exit", 2, 0.1)

    aid = welt.import_starten(welt.c["admin"], {"m.glb": GLB}, "neu_mesh", projekt="neu-mesh")
    _rechnen(welt, abgelehnt)
    assert _auftrag(welt, aid).fehler == W.F_KONVERTIEREN.format(code=2)


def test_verify_scheitert_nichts_uebergeben(welt: Welt) -> None:  # noqa: F811
    def verify_rot(lauf: Lauf) -> Ergebnis:
        if lauf.argv[1] == "verify":
            return Ergebnis("exit", 1, 0.1)
        return baut_paket(lauf)

    aid = welt.import_starten(welt.c["admin"], {"s.spz": SPZ}, "neu_splats", projekt="neu")
    with Freischalten(welt) as f:
        _rechnen(welt, verify_rot)
    assert _auftrag(welt, aid).fehler == W.F_VERIFY
    assert f.gesehen == [] and list((welt.tmp / "uebergabe").iterdir()) == []


@pytest.mark.parametrize("art", ["symlink", "hardlink", "fifo", "schreibbar", "leer"])
def test_paketbaum_aus_dem_job_wird_geprueft(welt: Welt, art: str) -> None:  # noqa: F811
    def boese(lauf: Lauf) -> Ergebnis:
        if lauf.argv[1] == "neu-ohne-wolke":
            ziel = ziel_von(lauf)
            ziel.mkdir()
            if art != "leer":
                (ziel / "project.json").write_text("{}")
            if art == "symlink":
                (ziel / "x").symlink_to("/etc/passwd")
            elif art == "hardlink":
                os.link(ziel / "project.json", lauf.arbeit / "zweiter-name")
            elif art == "fifo":
                os.mkfifo(ziel / "rohr")
            elif art == "schreibbar":
                (ziel / "project.json").chmod(0o666)
        return Ergebnis("ok", 0, 0.1)

    aid = welt.import_starten(welt.c["admin"], {"s.spz": SPZ}, "neu_splats", projekt="neu")
    with Freischalten(welt) as f:
        _rechnen(welt, boese)
    assert _auftrag(welt, aid).fehler == W.F_PAKETBAUM
    assert f.gesehen == [] and list((welt.tmp / "uebergabe").iterdir()) == []


@pytest.mark.parametrize(("wort", "text"), [
    ("VORHANDEN", W.F_VORHANDEN), ("FREISCHALTEN", W.MELDUNGEN["FREISCHALTEN"]),
    ("KOPIE", W.MELDUNGEN["KOPIE"]), ("EINGANG_SYMLINK", "Freischalten abgelehnt "
                                      "(EINGANG_SYMLINK)."),
])
def test_meldung_der_uebergabe(welt: Welt, wort: str, text: str) -> None:  # noqa: F811
    aid = welt.import_starten(welt.c["admin"], {"s.spz": SPZ}, "neu_splats", projekt="neu")
    with Freischalten(welt, wort):
        _rechnen(welt)
    a = _auftrag(welt, aid)
    assert a.status == "fehlgeschlagen" and a.fehler == text


def test_ohne_meldung_zeitueberschreitung(welt: Welt, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
                                          ) -> None:
    monkeypatch.setattr(W, "UEBERGABE_WARTEN_S", 1)
    aid = welt.import_starten(welt.c["admin"], {"s.spz": SPZ}, "neu_splats", projekt="neu")
    _rechnen(welt)
    a = _auftrag(welt, aid)
    assert a.status == "fehlgeschlagen" and a.fehler == W.F_FREISCHALTEN_ZEIT
    assert [p.name for p in (welt.tmp / "uebergabe").iterdir()] == [f".verworfen-{aid}"]
    # Nächster Start räumt beiseitegelegte Pakete weg.
    wk, _t = welt.worker(baut_paket)
    wk.start()
    wk.conn.close()
    assert list((welt.tmp / "uebergabe").iterdir()) == []


def test_nicht_eingerichtet(welt: Welt) -> None:  # noqa: F811
    from dataclasses import replace

    aid = welt.import_starten(welt.c["admin"], {"s.spz": SPZ}, "neu_splats", projekt="neu")
    wk = W.Worker(replace(welt.einstellungen(), uploads=None, uebergabe=None, meldungen=None),
                  TestLaeufer(baut_paket))
    wk.start()
    wk.schleife(einmal=True)
    wk.conn.close()
    assert _auftrag(welt, aid).fehler == W.F_NICHT_EINGERICHTET


def test_upload_als_symlink_abgelehnt(welt: Welt) -> None:  # noqa: F811
    aid = welt.import_starten(welt.c["admin"], {"s.spz": SPZ}, "neu_splats", projekt="neu")
    datei = welt.wurzel / _auftrag(welt, aid).optionen["upload"] / "s.spz"
    datei.unlink()
    datei.symlink_to("/etc/passwd")
    t = _rechnen(welt)
    assert _auftrag(welt, aid).fehler == W.F_UPLOAD and t.laeufe == []


def test_vorhandenes_projekt_abgelehnt(welt: Welt) -> None:  # noqa: F811
    aid = welt.import_starten(welt.c["admin"], {"s.spz": SPZ}, "neu_splats", projekt="neu")
    (welt.settings.data_dir / "neu").mkdir()
    t = _rechnen(welt)
    assert _auftrag(welt, aid).fehler == W.F_VORHANDEN and t.laeufe == []


class Haelt(TestLaeufer):
    """Läufer, der bis `stoppen` blockiert (lange Konvertierung)."""

    def __init__(self) -> None:
        super().__init__(lambda _l: Ergebnis("ok", 0, 0))
        self.gestoppt_ev = threading.Event()
        self.laeuft = threading.Event()

    def ausfuehren(self, lauf: Lauf) -> Ergebnis:
        self.laeufe.append(lauf)
        self.laeuft.set()
        self.gestoppt_ev.wait(30)
        return Ergebnis("abgebrochen", None, 1.0)

    def stoppen(self, name: str) -> None:
        self.gestoppt.append(name)
        self.gestoppt_ev.set()


def test_abbrechen_waehrend_des_laufs(welt: Welt) -> None:  # noqa: F811
    aid = welt.import_starten(welt.c["admin"], {"s.spz": SPZ}, "neu_splats", projekt="neu")
    h = Haelt()
    wk, _ = welt.worker(None, h)
    wk.start()
    t = threading.Thread(target=wk.schleife, kwargs={"einmal": True})
    t.start()
    assert h.laeuft.wait(10)
    r = welt.c["admin"].delete(f"/api/v1/importe/{aid}", headers=csrf())
    assert r.status_code == 202 and r.json()["status"] == "laufend"
    t.join(30)
    wk.conn.close()
    a = _auftrag(welt, aid)
    assert a.status == "abgebrochen" and a.fehler == W.F_ABGEBROCHEN
    assert h.gestoppt[0] == f"scanview-auftrag-{aid}"
    assert list((welt.tmp / "uebergabe").iterdir()) == []


def test_neustart_raeumt_arbeitsordner_der_importe(welt: Welt) -> None:  # noqa: F811
    (welt.tmp / "uebergabe" / ".arbeit-0123456789abcdef").mkdir()
    wk, _ = welt.worker(baut_paket)
    wk.start()
    wk.conn.close()
    assert list((welt.tmp / "uebergabe").iterdir()) == []


def test_alte_meldung_ohne_auftrag_wird_samt_paket_geraeumt(welt: Welt) -> None:  # noqa: F811
    (welt.tmp / "uebergabe" / "alt").mkdir()
    (welt.tmp / "meldungen" / "alt").write_text("OK\n")
    wk, _ = welt.worker(baut_paket)
    wk.start()
    wk.schritt()
    wk.conn.close()
    assert not (welt.tmp / "uebergabe" / "alt").exists()


def test_stufen_werden_sichtbar(welt: Welt) -> None:  # noqa: F811
    stufen: list[str | None] = []

    def merken(lauf: Lauf) -> Ergebnis:
        jc = welt.jobs()
        try:
            stufen.append(auftraege.importe(jc)[0].stufe)
        finally:
            jc.close()
        return baut_paket(lauf)

    welt.import_starten(welt.c["admin"], {"s.spz": SPZ}, "neu_splats", projekt="neu")
    with Freischalten(welt):
        _rechnen(welt, merken)
    assert stufen == ["konvertiert", "prueft"]
