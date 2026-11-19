"""PDAL-Umgebung im Schwachstellen-Scan: OSV-Ersatz mit Gegenprobe je Paket (`osv.toml`), VEX nur
aus `osv.toml` und an `pixi.lock` gebunden, blinde Restpakete nur mit akzeptiertem Befund."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scanview_nachweise import pdal_scan, scan, vex
from scanview_nachweise import pdal_umgebung as pu
from scanview_nachweise.fehler import NachweisFehler

WURZEL = Path(__file__).resolve().parents[2]
LOCK = (WURZEL / pu.LOCK).read_text(encoding="utf-8")
LOCK_SHA = pu.lock_sha256(LOCK)
JETZT = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
HEUTE = JETZT.date()
LEER = {"SchemaVersion": 2, "Results": []}
BEFUND = "B-SCAN-PDAL-UMGEBUNG-BLIND"
PAKET = next(p for p in pu.lies_lock(LOCK) if p.name == "libexpat")
REF = f"{pu.REF_PRAEFIX}{PAKET.purl}"
OSV_TOML = f"""lock_sha256 = "{LOCK_SHA}"

[[paket]]
name = "libexpat"
version = "{PAKET.version}"
build = "{PAKET.build}"
repo = "https://github.com/libexpat/libexpat"
tag = "R_neu"
commit = "{"a" * 40}"
gegenprobe_tag = "R_alt"
gegenprobe_commit = "{"b" * 40}"
gegenprobe_erwartet = "CVE-0000-1"
"""


def _bom() -> dict[str, Any]:
    return {"components": pu.komponenten(LOCK)}


def _holen(gegenprobe: bool = True, funde: tuple[str, ...] = ("CVE-0000-9",)):
    def holen(nutzlast: dict[str, Any]) -> dict[str, Any]:
        if nutzlast.get("commit") == "b" * 40 or nutzlast.get("version") == "R_alt":
            return {"vulns": [{"id": "CVE-0000-1"}] if gegenprobe else []}
        return {"vulns": [{"id": f, "aliases": [f"GHSA-{f}"]} for f in funde]}
    return holen


def _tag_commit(repo: str, tag: str) -> str:
    return {"R_neu": "a" * 40, "R_alt": "b" * 40}[tag]


def _welt(tmp_path: Path, osv_toml: str = OSV_TOML) -> Path:
    (tmp_path / pu.ORDNER).mkdir(parents=True)
    shutil.copy(WURZEL / pu.LOCK, tmp_path / pu.LOCK)
    (tmp_path / pdal_scan.OSV_TOML).write_text(osv_toml, encoding="utf-8")
    return tmp_path


def _stand(tmp_path: Path, **kw: Any) -> pdal_scan.PdalStand:
    stand = pdal_scan.aktualisieren(_welt(tmp_path), tmp_path / "cache", holen=_holen(**kw),
                                    uhr=lambda: JETZT, tag_commit=_tag_commit)
    assert stand is not None
    return stand


# --- OSV-Ersatz ----------------------------------------------------------------------------------

def test_gegenprobe_ok_deckt_paket_und_liefert_treffer_hoch(tmp_path):
    stand = _stand(tmp_path)
    assert pdal_scan.ersatz_fuer(_bom(), stand) == {REF: PAKET.purl}
    abd = scan.abdeckung(_bom(), LEER, "import", pdal_scan.ersatz_fuer(_bom(), stand))
    assert REF in abd["ersatz"] and REF not in abd["ungescannt_pflicht"]
    [t] = pdal_scan.treffer(_bom(), stand, "import")
    assert (t.id, t.schwere, t.bom_ref, t.aliase) == ("CVE-0000-9", "hoch", REF,
                                                      ("GHSA-CVE-0000-9",))


def test_leere_gegenprobe_ist_blind_und_pflicht(tmp_path):
    stand = _stand(tmp_path, gegenprobe=False)
    assert stand.blind == (REF,) and stand.gedeckt == {}
    abd = scan.abdeckung(_bom(), LEER, "import", pdal_scan.ersatz_fuer(_bom(), stand))
    assert REF in abd["ungescannt_pflicht"]
    assert pdal_scan.treffer(_bom(), stand, "import") == []


def test_anderer_lock_sha_verwirft_osv_toml(tmp_path):
    wt = _welt(tmp_path, OSV_TOML.replace(LOCK_SHA, "c" * 64))
    with pytest.raises(NachweisFehler, match="gehört nicht zu diesem Lock"):
        pdal_scan.aktualisieren(wt, tmp_path / "cache", holen=_holen(), uhr=lambda: JETZT,
                                tag_commit=_tag_commit)


def test_stand_zu_anderem_lock_deckt_nichts(tmp_path):
    stand = _stand(tmp_path)
    fremd = pdal_scan.PdalStand(stand.zeit, "c" * 64, stand.gedeckt, (), stand.sha256)
    assert pdal_scan.ersatz_fuer(_bom(), fremd) == {}
    assert REF in scan.abdeckung(_bom(), LEER, "import", {})["ungescannt_pflicht"]


def test_tag_zeigt_woanders_hin(tmp_path):
    with pytest.raises(NachweisFehler, match=r"osv\.toml korrigieren"):
        pdal_scan.aktualisieren(_welt(tmp_path), tmp_path / "cache", holen=_holen(),
                                uhr=lambda: JETZT, tag_commit=lambda r, t: "d" * 40)


@pytest.mark.parametrize(("ersetzen", "meldung"), [
    (('version = "2', 'version = "1'), "steht so nicht im Lock"),
    (("https://github.com/libexpat", "https://example.org/libexpat"), "Repo"),
    (('gegenprobe_tag = "R_alt"', 'gegenprobe_tag = "R_neu"'), "Gegenprobe = geprüfter Tag"),
    (('gegenprobe_erwartet = "CVE-0000-1"\n', ""), "braucht genau"),
], ids=["version", "repo", "gegenprobe-gleich", "feld-fehlt"])
def test_osv_toml_fail_closed(tmp_path, ersetzen, meldung):
    wt = _welt(tmp_path, OSV_TOML.replace(*ersetzen))
    with pytest.raises(NachweisFehler, match=meldung):
        pdal_scan.lade_osv_toml(wt)


def test_stand_alt_oder_kaputt(tmp_path):
    _stand(tmp_path)
    with pytest.raises(NachweisFehler, match="h alt"):
        pdal_scan.lade_stand(tmp_path / "cache", JETZT + timedelta(hours=25))
    pfad = tmp_path / "cache" / pdal_scan.STAND
    daten = json.loads(pfad.read_text())
    daten["pakete"][0]["gegenprobe"]["bestanden"] = False
    pfad.write_text(json.dumps(daten))
    with pytest.raises(NachweisFehler, match="Funde ohne bestandene Gegenprobe"):
        pdal_scan.lade_stand(tmp_path / "cache", JETZT)


def test_stand_noetig_nur_mit_umgebung(tmp_path):
    mit, ohne = tmp_path / "mit.json", tmp_path / "ohne.json"
    mit.write_text(json.dumps(_bom()))
    ohne.write_text(json.dumps({"components": [{"bom-ref": "numpy"}]}))
    assert pdal_scan.stand_falls_noetig([ohne], tmp_path / "leer", JETZT) is None
    with pytest.raises(NachweisFehler, match="db-aktualisieren"):
        pdal_scan.stand_falls_noetig([ohne, mit], tmp_path / "leer", JETZT)


def test_repo_osv_toml_passt_zum_lock():
    t = pdal_scan.lade_osv_toml(WURZEL)
    assert t.lock_sha256 == LOCK_SHA and len(t.pakete) >= 20
    assert all(e.tag != e.gegenprobe_tag for e in t.pakete)
    pdal_scan.vex_aussagen(WURZEL)  # Form der Aussagen gültig


# --- VEX: nur aus osv.toml, gebunden an pixi.lock -----------------------------------------------

def _aussage(lock_hash: str, lockdatei: str = vex.PDAL_LOCKDATEI) -> vex.VexAussage:
    return vex.VexAussage("CVE-0000-9", (PAKET.purl,), "not_affected",
                          "vulnerable_code_not_in_execute_path",
                          "Funktion X wird von PDAL/GDAL nie aufgerufen (Beleg im Test)", JETZT,
                          lock_hash, "Prüfer", lockdatei)


def _treffer(tmp_path: Path) -> list[scan.Treffer]:
    return pdal_scan.treffer(_bom(), _stand(tmp_path), "import")


def test_vex_mit_lock_sha_gibt_frei(tmp_path):
    locks = vex.lock_hashes(WURZEL)
    assert locks[vex.PDAL_LOCKDATEI] == f"sha256:{LOCK_SHA}"
    treffer = _treffer(tmp_path)
    assert vex.lockdatei_von(treffer[0]) == vex.PDAL_LOCKDATEI
    assert vex.treffer_gate(treffer, [_aussage(f"sha256:{LOCK_SHA}")], HEUTE,
                            lock_hashes=locks) == []


def test_vex_fuer_fremden_lock_sha_greift_nicht(tmp_path):
    zeilen = vex.treffer_gate(_treffer(tmp_path), [_aussage("sha256:" + "c" * 64)], HEUTE,
                              lock_hashes=vex.lock_hashes(WURZEL))
    assert zeilen and "CVE-0000-9" in zeilen[0]


def test_vex_mit_import_uv_lock_greift_nicht(tmp_path):
    """Der zweite, falsch gebundene Weg (Paket-Lockdatei des Artefakts) gibt nie frei."""
    locks = vex.lock_hashes(WURZEL)
    falsch = _aussage(locks["import/uv.lock"], "import/uv.lock")
    assert vex.treffer_gate(_treffer(tmp_path), [falsch], HEUTE, lock_hashes=locks)


def test_openvex_mit_pdal_lockdatei_ist_formfehler(tmp_path):
    dok = {"@context": "https://openvex.dev/ns/v0.2.0", "@id": "x", "author": "a", "version": 1,
           "timestamp": JETZT.isoformat(), "statements": [{
               "vulnerability": {"name": "CVE-0000-9"}, "products": [{"@id": PAKET.purl}],
               "status": "fixed", "scanview": {"lockdatei": vex.PDAL_LOCKDATEI,
                                               "lock_hash": f"sha256:{LOCK_SHA}",
                                               "pruefer": "p"}}]}
    pfad = tmp_path / "vex.openvex.json"
    pfad.write_text(json.dumps(dok))
    with pytest.raises(NachweisFehler, match="keine der Lockdateien"):
        vex.lade_vex(pfad)


def test_osv_toml_vex_wird_aussage(tmp_path):
    wt = _welt(tmp_path, OSV_TOML + """
[[vex]]
schwachstelle = "CVE-0000-9"
paket = "libexpat"
status = "not_affected"
justification = "vulnerable_code_not_in_execute_path"
impact_statement = "Funktion X wird von PDAL/GDAL nie aufgerufen (Beleg im Test)"
zeit = 2026-11-05T10:00:00Z
pruefer = "Prüfer"
""")
    [a] = pdal_scan.vex_aussagen(wt)
    assert (a.lockdatei, a.lock_hash, a.produkte) == (vex.PDAL_LOCKDATEI, f"sha256:{LOCK_SHA}",
                                                      (PAKET.purl,))
    kaputt = _welt(tmp_path / "b", (wt / pdal_scan.OSV_TOML).read_text().replace(
        'paket = "libexpat"', 'paket = "fremd"'))
    with pytest.raises(NachweisFehler, match="paket aus dem Lock"):
        pdal_scan.vex_aussagen(kaputt)
