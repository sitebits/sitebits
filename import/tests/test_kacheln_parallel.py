"""Prozessgrenzen mit echten synthetischen Kacheln, nur KTX-Kodierung ersetzt."""

import importlib
import json
import multiprocessing as mp
import threading
from pathlib import Path

import pytest
from kachel_arbeiter_fixture import (
    mehr_flaechen,
    start_mit_encoder,
    start_mit_fehler,
    start_mit_packfehler,
    start_mit_teilung,
)
from spike.textur_mehrseitig import pruefen as textur_pruefen

from scanview_import.cli import main
from scanview_import.kacheln import kacheln, parallel
from tests.test_kacheln import fake_ktx, pruefen, quelle


def dateien(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*")
            if p.is_file() and p.name not in ("kacheln-log.json", "packen-log.json")}


def test_bytegleich_und_vertraege(tmp_path, monkeypatch):
    monkeypatch.setattr(parallel, "_initialisieren", start_mit_encoder)
    from scanview_import.kacheln import paket

    monkeypatch.setattr(paket, "encode_texture", fake_ktx)
    sampled = []
    original_peak = parallel.Auftraege.peak_mib

    def peak(self):
        if self.parallel:
            sampled.append(self._peak)
        return original_peak(self)

    monkeypatch.setattr(parallel.Auftraege, "peak_mib", peak)
    source = quelle(tmp_path / "quelle", n=12)
    reports = []
    for jobs in (1, 4):
        output = tmp_path / str(jobs)
        report = kacheln(source, output, jobs=jobs, max_dreiecke=40, blatt_dreiecke=12,
                         packen=True, crs="EPSG:25833", hoehe="DHHN2016")
        tiles = [p for p in report["measurements"] if p["phase"] == "kachel"]
        assert sum(not t["leaf"] for t in tiles) >= 16
        assert len({t["uri"] for t in tiles}) == report["tiles"]
        assert all(t["worker"] > 0 and t["queueSeconds"] >= 0 for t in tiles)
        assert report["measurements"][-1]["peakRssMiBTotal"] > 0
        assert all(t["texturCacheBytesBudget"] == 4 * 1024**3 // jobs for t in tiles)
        if jobs == 4:
            assert all(t["worker"] != mp.current_process().pid for t in tiles)
            assert len({t["worker"] for t in tiles}) > 1
            # ru_maxrss enthält auch frühere Tests; für diesen Vergleich zählt
            # die echte RSS-Abtastung statt des historischen Prozesshöchststands.
            own_rss = next(line for line in Path("/proc/self/status").read_text().splitlines()
                           if line.startswith("VmRSS:"))
            assert max(sampled) > int(own_rss.split()[1]) / 1024
        geometry = pruefen(source, output / "prueffassung")
        for key in ("K2", "K3", "K4", "K8", "K10"):
            assert geometry[key]["ok"] is True, geometry[key]
        texture = textur_pruefen(output / "prueffassung/tileset.json", source, samples=1200)
        assert texture["K9"]["ok"] is True
        assert texture["K12"]["ok"] is True
        reports.append([t["uri"] for t in tiles])
    assert reports[0] == reports[1]
    assert dateien(tmp_path / "1") == dateien(tmp_path / "4")
    # Auch die kodierungsunabhängigen Packberichte werden deterministisch zusammengeführt.
    a = json.loads((tmp_path / "1/paket/layers/kacheln/packen-log.json").read_text())
    b = json.loads((tmp_path / "4/paket/layers/kacheln/packen-log.json").read_text())
    assert a == b


def test_nachvereinfachung_bytegleich(tmp_path, monkeypatch):
    module = importlib.import_module("scanview_import.kacheln")
    monkeypatch.setattr(module, "backen", mehr_flaechen)
    monkeypatch.setattr(parallel, "_initialisieren", start_mit_teilung)
    source = quelle(tmp_path / "quelle", n=4)
    for jobs in (1, 4):
        report = kacheln(source, tmp_path / str(jobs), jobs=jobs,
                         max_dreiecke=12, blatt_dreiecke=6)
        inner = [t for t in report["measurements"] if t["phase"] == "kachel" and not t["leaf"]]
        assert inner and all(t["representationTriangles"] <= 3 for t in inner)
    assert dateien(tmp_path / "1") == dateien(tmp_path / "4")
    assert not mp.active_children()


@pytest.mark.parametrize("packing", [False, True])
def test_arbeiterfehler_ursache_und_keine_waisen(tmp_path, monkeypatch, packing):
    monkeypatch.setattr(parallel, "_initialisieren",
                        start_mit_packfehler if packing else start_mit_fehler)
    before = {p.pid for p in mp.active_children()}
    source = quelle(tmp_path / "quelle", n=4)
    output = tmp_path / "ausgabe"
    with pytest.raises(ValueError, match="Synthetischer Arbeiterfehler") as exc:
        kacheln(source, output, jobs=4, max_dreiecke=16, blatt_dreiecke=8,
                packen=packing, crs="EPSG:25833", hoehe="DHHN2016")
    assert exc.value.__cause__ is not None  # Ursprung aus dem anderen Prozess bleibt erhalten.
    assert {p.pid for p in mp.active_children()} == before
    assert not any(t.name == "kachel-rss" for t in threading.enumerate())
    assert not (output / "paket/project.json").exists()
    if not packing:
        assert not (output / "tileset.json").exists()
    if packing:
        assert (output / "prueffassung/tileset.json").exists()


def test_jobs_eins_ohne_pool_und_cli(tmp_path, monkeypatch):
    def verboten(*args, **kwargs):
        raise AssertionError("Kein Prozesspool bei jobs=1")

    monkeypatch.setattr(parallel, "ProcessPoolExecutor", verboten)
    source = quelle(tmp_path / "quelle", n=2)
    assert main(["kacheln", str(source), str(tmp_path / "eins"), "--jobs", "1"]) == 0
    with pytest.raises(ValueError, match="mindestens 1"):
        kacheln(source, tmp_path / "null", jobs=0)
    assert not (tmp_path / "null").exists()


def test_negatives_cachebudget_vor_dateizugriff(tmp_path):
    with pytest.raises(ValueError, match="mindestens 0 Bytes"):
        kacheln(tmp_path / "fehlt.obj", tmp_path / "ausgabe", jobs=4, textur_cache_bytes=-1)
    assert not (tmp_path / "ausgabe").exists()
