"""Blinde Restpakete der PDAL-Umgebung (`scan-blind.toml`): aus der Pflicht-Abdeckung nur bei
genauer Listung und akzeptiertem Befund, dessen `akzeptanz_bindung` zum Inhalt der Liste passt;
Fristen prüft allein das Befund-Gate."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scanview_nachweise import gate, pdal_scan, scan
from scanview_nachweise import pdal_umgebung as pu
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.register import Befund, Status

WURZEL = Path(__file__).resolve().parents[2]
LOCK = (WURZEL / pu.LOCK).read_text(encoding="utf-8")
LOCK_SHA = pu.lock_sha256(LOCK)
HEUTE = date(2026, 11, 5)
LEER = {"SchemaVersion": 2, "Results": []}
BEFUND = "B-SCAN-PDAL-UMGEBUNG-BLIND"


def _bom() -> dict[str, Any]:
    return {"components": pu.komponenten(LOCK)}


BLIND_PAKET = next(p for p in pu.lies_lock(LOCK) if p.name == "libtiff")
BLIND_REF = f"{pu.REF_PRAEFIX}{BLIND_PAKET.purl}"


def _blind(lock_sha: str = LOCK_SHA, name: str = "libtiff") -> pdal_scan.ScanBlind:
    p = next(x for x in pu.lies_lock(LOCK) if x.name == name)
    return pdal_scan.ScanBlind(lock_sha, BEFUND, frozenset({(p.name, p.version, p.build,
                                                              p.sha256)}))


def _befund(status: Status = "akzeptiert", frist: date | None = date(2027, 3, 31),
            bindung: str | None = None) -> Befund:
    """Befund zur Liste `_blind()`; akzeptiert trägt er standardmäßig deren Bindung."""
    akzeptiert = status == "akzeptiert"
    return Befund(BEFUND, "Test", "Restrisiko blinde Pakete (Test)", "hoch", "lieferkette",
                  status, (), None, None, "Begründung für die Annahme (Test)" if akzeptiert
                  else None, frist if akzeptiert else None, date(2026, 11, 5), ("import",),
                  "nur Import", akzeptanz_bindung=(bindung or pdal_scan.blind_bindung(_blind()))
                  if akzeptiert else None)


def _pflicht(bom: dict[str, Any], blind: pdal_scan.ScanBlind | None) -> list[str]:
    return scan.abdeckung(bom, LEER, "import", {}, pdal_scan.blind_refs(bom, blind))[
        "ungescannt_pflicht"]


def test_blind_akzeptiert_nimmt_aus_der_pflicht():
    blind = pdal_scan.blind_ausnahme(_blind(), [_befund()])
    abd = scan.abdeckung(_bom(), LEER, "import", {}, pdal_scan.blind_refs(_bom(), blind))
    assert BLIND_REF not in abd["ungescannt_pflicht"] and abd["restrisiko"] == [BLIND_REF]
    assert gate.befund_gate([_befund()], HEUTE) == []


def test_blind_paket_nicht_gelistet_bleibt_pflicht():
    libpng = _blind(name="libpng")
    blind = pdal_scan.blind_ausnahme(libpng, [_befund(bindung=pdal_scan.blind_bindung(libpng))])
    assert BLIND_REF in _pflicht(_bom(), blind)


def test_blind_lock_sha_anders_bleibt_pflicht():
    anders = _blind(lock_sha="c" * 64)
    blind = pdal_scan.blind_ausnahme(anders, [_befund(bindung=pdal_scan.blind_bindung(anders))])
    assert BLIND_REF in _pflicht(_bom(), blind)


def test_blind_bindung_passend_gibt_frei():
    befund = _befund()
    assert befund.akzeptanz_bindung == pdal_scan.blind_bindung(_blind())
    assert pdal_scan.blind_ausnahme(_blind(), [befund]) == _blind()


def test_blind_akzeptanz_ohne_bindung_rot():
    with pytest.raises(NachweisFehler, match="akzeptanz_bindung") as e:
        pdal_scan.blind_ausnahme(_blind(), [replace(_befund(), akzeptanz_bindung=None)])
    assert e.value.regel == "scan-blind"


def test_blind_paket_verschoben_alte_akzeptanz_rot():
    """Gleiches Lock, ein Paket aus osv.toml in die Blind-Liste verschoben: Die Akzeptanz galt
    der alten Paketmenge und gibt die neue nicht frei."""
    osv_paket = next(iter(pdal_scan.lade_osv_toml(WURZEL).pakete)).name
    p = next(x for x in pu.lies_lock(LOCK) if x.name == osv_paket)
    verschoben = replace(_blind(), pakete=_blind().pakete | {(p.name, p.version, p.build,
                                                               p.sha256)})
    with pytest.raises(NachweisFehler, match="akzeptanz_bindung") as e:
        pdal_scan.blind_ausnahme(verschoben, [_befund()])
    assert e.value.regel == "scan-blind"


def test_blind_liste_neu_gebunden_alte_akzeptanz_rot():
    """Nach einer Lock-Auffrischung neu gebundene Liste (anderer Lock-SHA, gleiche Pakete):
    Die alte Akzeptanz gibt sie nicht frei."""
    with pytest.raises(NachweisFehler, match="akzeptanz_bindung"):
        pdal_scan.blind_ausnahme(_blind(lock_sha="c" * 64), [_befund()])


def test_blind_bindung_haengt_nur_am_inhalt(tmp_path):
    """Kommentare und Reihenfolge in scan-blind.toml ändern die Bindung nicht, jedes Feld der
    Liste (Befund, Lock-SHA, Paketangaben) schon."""
    toml = (WURZEL / pdal_scan.SCAN_BLIND).read_text(encoding="utf-8")
    original = pdal_scan.blind_bindung(pdal_scan.lade_scan_blind(WURZEL / pdal_scan.SCAN_BLIND))
    kopf, *pakete = toml.split("[[paket]]")
    umgestellt = "# anderer Kommentar\n" + kopf + "[[paket]]" + "[[paket]]".join(reversed(pakete))
    pfad = tmp_path / "scan-blind.toml"
    pfad.write_text(umgestellt, encoding="utf-8")
    assert pdal_scan.blind_bindung(pdal_scan.lade_scan_blind(pfad)) == original
    basis = _blind()
    (name, version, build, sha), = basis.pakete
    for anders in (replace(basis, befund="B-ANDERS"), replace(basis, lock_sha256="c" * 64),
                   replace(basis, pakete=frozenset({(name, version, build + "x", sha)}))):
        assert pdal_scan.blind_bindung(anders) != pdal_scan.blind_bindung(basis)


def test_blind_befund_offen_bleibt_pflicht():
    assert pdal_scan.blind_ausnahme(_blind(), [_befund("offen")]) is None
    assert pdal_scan.blind_ausnahme(_blind(), []) is None
    assert BLIND_REF in _pflicht(_bom(), pdal_scan.blind_ausnahme(_blind(), [_befund("offen")]))


def test_blind_akzeptanz_abgelaufen_blockiert_ueber_gate():
    """Fristlogik nur im Befund-Gate: die Ausnahme greift, das Gate blockiert."""
    abgelaufen = _befund(frist=date(2026, 11, 1))
    blind = pdal_scan.blind_ausnahme(_blind(), [abgelaufen])
    assert BLIND_REF not in _pflicht(_bom(), blind)
    assert gate.befund_gate([abgelaufen], HEUTE) == [
        f"{BEFUND}: Akzeptanz abgelaufen am 2026-11-01"]


def test_blind_komponente_ohne_praefix_bleibt_pflicht():
    bom = _bom()
    k = next(x for x in bom["components"] if x["bom-ref"] == BLIND_REF)
    k["bom-ref"] = BLIND_PAKET.purl
    blind = pdal_scan.blind_ausnahme(_blind(), [_befund()])
    assert BLIND_PAKET.purl in _pflicht(bom, blind)


def test_repo_scan_blind_passt_zum_lock_und_osv():
    """Blind-Liste und OSV-Liste zusammen decken jedes Paket genau einmal ab."""
    blind = pdal_scan.lade_scan_blind(WURZEL / pdal_scan.SCAN_BLIND)
    assert blind.lock_sha256 == LOCK_SHA and blind.befund == BEFUND
    gedeckt = {e.name for e in pdal_scan.lade_osv_toml(WURZEL).pakete}
    gelistet = {n for n, *_ in blind.pakete}
    assert not gedeckt & gelistet
    assert gedeckt | gelistet == {p.name for p in pu.lies_lock(LOCK)}
    assert blind.pakete <= {(p.name, p.version, p.build, p.sha256) for p in pu.lies_lock(LOCK)}


def test_cli_blind_bindung_gibt_den_wert_der_repo_liste_aus(capsys):
    from scanview_nachweise import cli
    assert cli.main(["pdal-umgebung", "blind-bindung", "--wurzel", str(WURZEL)]) == 0
    blind = pdal_scan.lade_scan_blind(WURZEL / pdal_scan.SCAN_BLIND)
    assert f'akzeptanz_bindung = "{pdal_scan.blind_bindung(blind)}"' in capsys.readouterr().out
