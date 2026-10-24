"""OSV-Ersatzprüfung für Eigenbauten: Gegenprobe, Commit und Tag, Frische des Stands, genaue
Abdeckung (bom-ref und PURL) und das VEX-Gate mit der Lockdatei `eigenbau.toml`."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scanview_nachweise import osv, sbom, scan, vex
from scanview_nachweise.fehler import NachweisFehler

WURZEL = Path(__file__).resolve().parents[2]
JETZT = datetime(2026, 10, 7, 20, 0, tzinfo=UTC)
REF = "eigenbau:basis-universal-transcoder@1.50.0"
REF_ZSTD = f"{REF}/zstd@1.5.7"
COMMIT = "051ad6d8a64bb95a79e8601c317055fd1782ad3e"
COMMIT_ZSTD = "f8745da6ff1ad1e7bab384bd1f9d742439278e99"
PURL = f"pkg:github/binomialllc/basis_universal@{COMMIT}"
PURL_ZSTD = f"pkg:github/facebook/zstd@{COMMIT_ZSTD}"
ZSTD_REPO = "https://github.com/facebook/zstd"


def _osv_attrappe(*, kontrolle_commit: bool = True, kontrolle_tag: bool = True,
                  seiten: bool = False) -> tuple[list[dict[str, Any]], osv.Holen]:
    """OSV nach dem am 07.10.2026 gemessenen Muster: CVE-2019-11922 für zstd v1.3.7 per Commit
    und Tag, nichts für basis_universal. Der zstd-Fund kommt nur per Tag — so meldete OSV damals
    OSV-2022-15 für v1.4.9. Heute ist zstd 1.5.7 eingebaut und ohne Fund; die Attrappe meldet
    dafür die erfundene Kennung OSV-2098-15, damit der Pfad „nur per Tag“ getestet bleibt."""
    anfragen: list[dict[str, Any]] = []

    def holen(nutzlast: dict[str, Any]) -> dict[str, Any]:
        anfragen.append(nutzlast)
        if nutzlast.get("commit") == osv.KONTROLLE_COMMIT:
            return {"vulns": [{"id": "OSV-2020-1", "aliases": [osv.KONTROLLE_ERWARTET]}]
                    if kontrolle_commit else []}
        paket = nutzlast.get("package", {})
        if paket.get("name") == ZSTD_REPO and nutzlast.get("version") == osv.KONTROLLE_TAG:
            return {"vulns": [{"id": osv.KONTROLLE_ERWARTET}] if kontrolle_tag else []}
        if paket.get("name") == ZSTD_REPO and nutzlast.get("version") == "v1.5.7":
            if seiten and "page_token" not in nutzlast:
                return {"vulns": [{"id": "OSV-2098-15", "modified": "2022-01-20"}],
                        "next_page_token": "weiter"}
            return {"vulns": [{"id": "OSV-2099-1" if seiten else "OSV-2098-15",
                               "modified": "2022-01-20"}]}
        return {}

    return anfragen, holen


def _abfragen() -> list[osv.Abfrage]:
    return osv.abfragen_aus_eigenbau(sbom.lade_eigenbau(WURZEL / "nachweise" / "eigenbau.toml"))


TAGS = {("https://github.com/BinomialLLC/basis_universal", "v1_50_0"): COMMIT,
        (ZSTD_REPO, "v1.5.7"): COMMIT_ZSTD}


def _tag_commit(repo: str, tag: str) -> str | None:
    return TAGS.get((repo, tag))


def _stand(tmp_path: Path, **attrappe: bool) -> osv.OsvStand:
    _, holen = _osv_attrappe(**attrappe)
    stand = osv.aktualisieren(_abfragen(), tmp_path, holen=holen, uhr=lambda: JETZT,
                              tag_commit=_tag_commit)
    assert stand is not None
    return stand


def _bom_mit_eigenbau(tmp_path: Path) -> dict[str, Any]:
    """Web-SBOM-Ausschnitt, wie `sbom.eigenbau_einfuegen` ihn aus dem echten `eigenbau.toml`
    schreibt (Dateien aus dem Repo)."""
    (e,) = sbom.lade_eigenbau(WURZEL / "nachweise" / "eigenbau.toml")
    for pfad in [*(p for p, _ in e.dateien), e.lizenzbeleg]:
        ziel = tmp_path / "wt" / pfad
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_bytes((WURZEL / pfad).read_bytes())
    bom: dict[str, Any] = {"metadata": {"component": {"bom-ref": "root"}}, "components": [
        {"type": "library", "bom-ref": "pkg:npm/three@0.180.0", "purl": "pkg:npm/three@0.180.0"}]}
    sbom.eigenbau_einfuegen(bom, [e], tmp_path / "wt")
    return bom


def _trivy(*purls: str) -> dict[str, Any]:
    return {"Results": [{"Type": "node-pkg", "Packages": [
        {"Identifier": {"PURL": p}} for p in purls]}]}


# --- Netzschritt ---------------------------------------------------------------------------------

def test_abfragen_aus_eigenbau_und_aus_dem_sbom_sind_gleich(tmp_path):
    # Was db-aktualisieren abfragt (eigenbau.toml), ist genau das, was die Scan-Stufe im SBOM
    # findet — sonst deckte der Stand die Komponenten nicht ab.
    assert sorted(_abfragen(), key=lambda a: a.bom_ref) == \
        sorted(osv.abfragen_aus_bom(_bom_mit_eigenbau(tmp_path)), key=lambda a: a.bom_ref)
    assert {(a.bom_ref, a.purl, a.tag) for a in _abfragen()} == {
        (REF, PURL, "v1_50_0"), (REF_ZSTD, PURL_ZSTD, "v1.5.7")}


def test_aktualisieren_fragt_commit_und_tag_und_vereinigt(tmp_path):
    anfragen, holen = _osv_attrappe()
    stand = osv.aktualisieren(_abfragen(), tmp_path, holen=holen, uhr=lambda: JETZT,
                              tag_commit=_tag_commit)
    je_ref = {a.bom_ref: [f.id for f in fs] for a, fs in stand.funde.items()}
    # OSV-2098-15 kommt nur über den Tag — ohne Tag-Abfrage bliebe er unsichtbar.
    assert je_ref == {REF: [], REF_ZSTD: ["OSV-2098-15"]}
    assert {"commit": COMMIT_ZSTD} in anfragen
    assert {"package": {"name": ZSTD_REPO, "ecosystem": "GIT"}, "version": "v1.5.7"} in anfragen
    gespeichert = json.loads((tmp_path / osv.OSV_STAND).read_text(encoding="utf-8"))
    assert gespeichert["kontrolle"]["gefunden"] == ["CVE-2019-11922", "OSV-2020-1"]
    assert osv.lade_stand(tmp_path, JETZT + timedelta(hours=1)).funde == stand.funde


@pytest.mark.parametrize("weg", ["kontrolle_commit", "kontrolle_tag"])
def test_blinde_gegenprobe_bricht_ab_und_schreibt_nichts(tmp_path, weg):
    with pytest.raises(NachweisFehler, match="osv-kontrolle"):
        _stand(tmp_path, **{weg: False})
    assert not (tmp_path / osv.OSV_STAND).exists()


def test_folgeseiten_werden_gelesen(tmp_path):
    stand = _stand(tmp_path, seiten=True)
    zstd = next(fs for a, fs in stand.funde.items() if a.bom_ref == REF_ZSTD)
    assert [f.id for f in zstd] == ["OSV-2098-15", "OSV-2099-1"]


def test_eigenbau_ohne_github_purl_ist_nicht_abfragbar():
    bom = {"components": [{"bom-ref": "eigenbau:x@1", "purl": "pkg:generic/x@1"}]}
    with pytest.raises(NachweisFehler, match="osv-abfrage"):
        osv.abfragen_aus_bom(bom)


# --- Stand ohne Netz ----------------------------------------------------------------------------

@pytest.mark.parametrize("uhr, meldung", [
    (JETZT + timedelta(hours=25), "25.0 h alt"),
    (JETZT - timedelta(minutes=1), "Zukunft"),
])
def test_stand_frisch_und_nicht_aus_der_zukunft(tmp_path, uhr, meldung):
    _stand(tmp_path)
    with pytest.raises(NachweisFehler, match=meldung):
        osv.lade_stand(tmp_path, uhr)


def test_stand_fehlt_oder_gegenprobe_manipuliert(tmp_path):
    with pytest.raises(NachweisFehler, match="db-aktualisieren"):
        osv.lade_stand(tmp_path, JETZT)
    _stand(tmp_path)
    pfad = tmp_path / osv.OSV_STAND
    daten = json.loads(pfad.read_text(encoding="utf-8"))
    daten["kontrolle"]["gefunden"] = []
    pfad.write_text(json.dumps(daten), encoding="utf-8")
    with pytest.raises(NachweisFehler, match="Gegenprobe"):
        osv.lade_stand(tmp_path, JETZT)


def test_stand_nur_noetig_mit_eigenbau(tmp_path):
    ohne = tmp_path / "ohne.cdx.json"
    ohne.write_text(json.dumps({"components": [{"bom-ref": "pkg:npm/a@1"}]}), encoding="utf-8")
    assert osv.stand_falls_noetig([ohne], tmp_path, JETZT) is None
    mit = tmp_path / "mit.cdx.json"
    mit.write_text(json.dumps(_bom_mit_eigenbau(tmp_path)), encoding="utf-8")
    with pytest.raises(NachweisFehler, match="osv-stand"):  # Eigenbau, aber kein Stand
        osv.stand_falls_noetig([ohne, mit], tmp_path / "leer", JETZT)


# --- Abdeckung im Scan ----------------------------------------------------------------------------

def test_abdeckung_eigenbau_ueber_osv_bleibt_als_ungescannt_sichtbar(tmp_path):
    bom = _bom_mit_eigenbau(tmp_path)
    stand = _stand(tmp_path)
    abd = scan.abdeckung(bom, _trivy("pkg:npm/three@0.180.0"), "web", osv.ersatz_fuer(bom, stand))
    assert abd["ungescannt_pflicht"] == []
    assert abd["ersatz"] == [REF, REF_ZSTD]
    assert abd["ungescannt"] == {"pkg:github": 2}


def test_abdeckung_ohne_stand_bricht_ab(tmp_path):
    bom = _bom_mit_eigenbau(tmp_path)
    abd = scan.abdeckung(bom, _trivy("pkg:npm/three@0.180.0"), "web", osv.ersatz_fuer(bom, None))
    assert abd["ungescannt_pflicht"] == [REF, REF_ZSTD]


def test_negativ_unerklaerte_github_komponente_bleibt_pflicht(tmp_path):
    # Ein pkg:github-Eintrag ohne Eigenbau-Erklärung — der OSV-Stand deckt ihn nicht ab.
    bom = _bom_mit_eigenbau(tmp_path)
    bom["components"].append({"type": "library", "bom-ref": "pkg:github/x/y@" + "1" * 40,
                              "purl": "pkg:github/x/y@" + "1" * 40})
    abd = scan.abdeckung(bom, _trivy("pkg:npm/three@0.180.0"), "web",
                         osv.ersatz_fuer(bom, _stand(tmp_path)))
    assert abd["ungescannt_pflicht"] == ["pkg:github/x/y@" + "1" * 40]


def test_negativ_anderer_commit_als_im_stand_bleibt_pflicht(tmp_path):
    # Derselbe Name, anderer Commit (z. B. Neubau ohne db-aktualisieren): nicht abgedeckt.
    stand = _stand(tmp_path)
    bom = _bom_mit_eigenbau(tmp_path)
    k = next(k for k in bom["components"] if k["bom-ref"] == REF)
    k["purl"] = k["purl"].replace(COMMIT, "2" * 40)
    abd = scan.abdeckung(bom, _trivy("pkg:npm/three@0.180.0"), "web", osv.ersatz_fuer(bom, stand))
    assert abd["ungescannt_pflicht"] == [REF]


# --- Treffer und VEX ------------------------------------------------------------------------------

def _aussage(lockdatei: str, lock_hash: str) -> vex.VexAussage:
    return vex.VexAussage(
        schwachstelle="OSV-2098-15", produkte=(PURL_ZSTD,), status="not_affected",
        justification="vulnerable_code_not_present",
        impact_statement="Betroffen ist ZSTD_compressBlock_opt0 (Kompressor); eingebaut ist nur "
                         "zstd/zstddeclib.c (Decoder), die Funktion fehlt dort.",
        zeit=JETZT, lock_hash=lock_hash, pruefer="Claude", lockdatei=lockdatei)


def test_osv_fund_ist_hoch_und_blockiert_ohne_vex(tmp_path):
    bom = _bom_mit_eigenbau(tmp_path)
    t = osv.treffer(bom, _stand(tmp_path), "web")
    assert [(x.id, x.paket, x.schwere, x.bom_ref) for x in t] == [
        ("OSV-2098-15", PURL_ZSTD, "hoch", REF_ZSTD)]
    assert vex.treffer_gate(t, [], date(2026, 10, 7)) == [
        f"OSV-2098-15 (hoch) {PURL_ZSTD}: keine freigebende VEX-Aussage"]


def _lock_hashes(tmp_path: Path) -> dict[str, str]:
    """Lockdateien in einem eigenen Baum: Der öffentliche Export enthält nicht alle (import/)."""
    wurzel = tmp_path / "wurzel"
    for pfad in (*vex.LOCKDATEIEN.values(), vex.EIGENBAU_LOCKDATEI):
        ziel = wurzel / pfad
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_bytes(pfad.encode())
    return vex.lock_hashes(wurzel)


def test_vex_fuer_eigenbau_gilt_nur_mit_eigenbau_toml(tmp_path):
    bom = _bom_mit_eigenbau(tmp_path)
    t = osv.treffer(bom, _stand(tmp_path), "web")
    locks = _lock_hashes(tmp_path)
    h = locks[vex.EIGENBAU_LOCKDATEI]
    heute = date(2026, 10, 7)
    gut = _aussage(vex.EIGENBAU_LOCKDATEI, h)
    assert vex.treffer_gate(t, [gut], heute, lock_hashes=locks) == []
    # An die Paket-Lockdatei gebunden gibt sie nicht frei — der Eigenbau ändert sich unabhängig.
    falsch = _aussage("web/package-lock.json", locks["web/package-lock.json"])
    assert vex.treffer_gate(t, [falsch], heute, lock_hashes=locks)
    # Ändert sich eigenbau.toml (neuer Commit, neue Dateien), verfällt die Aussage.
    alt = _aussage(vex.EIGENBAU_LOCKDATEI, "sha256:" + "0" * 64)
    assert vex.treffer_gate(t, [alt], heute, lock_hashes=locks)


def test_lock_hashes_eigenbau_optional(tmp_path):
    def lesen(pfad: str) -> bytes | None:
        return None if pfad == vex.EIGENBAU_LOCKDATEI else b"x"

    assert vex.EIGENBAU_LOCKDATEI not in vex.lock_hashes_aus(lesen)
    assert vex.EIGENBAU_LOCKDATEI in _lock_hashes(tmp_path)


# --- Härtung: Tag-Bindung, strenge Gegenprobe, Aliase, doppelte Refs ---------------------------

@pytest.mark.parametrize("aufloesung", [None, "3" * 40])
def test_tag_muss_auf_den_erklaerten_commit_zeigen(tmp_path, aufloesung):
    # Ein Tippfehler im Tag (OSV kennt ihn nicht → leer) oder ein Tag auf einem anderen Commit
    # darf nicht als vollständige Prüfung gelten.
    _, holen = _osv_attrappe()

    def tag_commit(repo: str, tag: str) -> str | None:
        return aufloesung if repo == ZSTD_REPO else _tag_commit(repo, tag)

    with pytest.raises(NachweisFehler, match="osv-tag"):
        osv.aktualisieren(_abfragen(), tmp_path, holen=holen, uhr=lambda: JETZT,
                          tag_commit=tag_commit)
    assert not (tmp_path / osv.OSV_STAND).exists()


@pytest.mark.parametrize("gefunden", ["CVE-2019-11922", ["CVE-2019-119220"], ["X-CVE-2019-11922"]])
def test_gegenprobe_im_stand_streng_geprueft(tmp_path, gefunden):
    _stand(tmp_path)
    pfad = tmp_path / osv.OSV_STAND
    daten = json.loads(pfad.read_text(encoding="utf-8"))
    daten["kontrolle"]["gefunden"] = gefunden
    pfad.write_text(json.dumps(daten), encoding="utf-8")
    with pytest.raises(NachweisFehler, match="Gegenprobe"):
        osv.lade_stand(tmp_path, JETZT)


def test_tagpruefung_im_stand_pflicht(tmp_path):
    _stand(tmp_path)
    pfad = tmp_path / osv.OSV_STAND
    daten = json.loads(pfad.read_text(encoding="utf-8"))
    daten["abfragen"][1]["tag_commit"] = "4" * 40
    pfad.write_text(json.dumps(daten), encoding="utf-8")
    with pytest.raises(NachweisFehler, match="osv-stand"):
        osv.lade_stand(tmp_path, JETZT)


def test_hash_des_stands_im_bericht(tmp_path):
    stand = _stand(tmp_path)
    import hashlib
    assert osv.stand_bericht(stand)["sha256"] == \
        hashlib.sha256((tmp_path / osv.OSV_STAND).read_bytes()).hexdigest()


def test_vex_widerruf_ueber_alias(tmp_path):
    # Fund OSV-X mit Alias CVE-X: alte Freigabe für OSV-X, neuere „affected“ für CVE-X → blockiert.
    bom = _bom_mit_eigenbau(tmp_path)
    stand = _stand(tmp_path)
    zstd = next(a for a in stand.funde if a.bom_ref == REF_ZSTD)
    stand = osv.OsvStand(stand.zeit, {**stand.funde,
                                      zstd: (osv.Fund("OSV-2098-15", ("CVE-2099-7",), ""),)})
    t = osv.treffer(bom, stand, "web")
    locks = _lock_hashes(tmp_path)
    h = locks[vex.EIGENBAU_LOCKDATEI]
    heute = date(2026, 10, 7)
    frei = _aussage(vex.EIGENBAU_LOCKDATEI, h)
    assert vex.treffer_gate(t, [frei], heute, lock_hashes=locks) == []
    widerruf = vex.VexAussage(
        schwachstelle="CVE-2099-7", produkte=(PURL_ZSTD,), status="affected", justification=None,
        impact_statement=None, zeit=JETZT + timedelta(hours=1), lock_hash=h, pruefer="Claude",
        lockdatei=vex.EIGENBAU_LOCKDATEI)
    assert vex.treffer_gate(t, [frei, widerruf], heute, lock_hashes=locks)
    # Freigabe allein über den Alias wirkt ebenso.
    ueber_alias = vex.VexAussage(**{**frei.__dict__, "schwachstelle": "CVE-2099-7"})
    assert vex.treffer_gate(t, [ueber_alias], heute, lock_hashes=locks) == []


def test_doppelte_eigenbau_refs_brechen_ab(tmp_path):
    bom = _bom_mit_eigenbau(tmp_path)
    k = next(k for k in bom["components"] if k["bom-ref"] == REF)
    zweit = json.loads(json.dumps(k))
    zweit.pop("components", None)
    zweit["purl"] = zweit["purl"].replace(COMMIT, "5" * 40)
    bom["components"].append(zweit)
    with pytest.raises(NachweisFehler, match="doppelt"):
        osv.ersatz_fuer(bom, _stand(tmp_path))


def test_abdeckung_prueft_purl_je_komponente(tmp_path):
    bom = _bom_mit_eigenbau(tmp_path)
    stand = _stand(tmp_path)
    ersatz = osv.ersatz_fuer(bom, stand)
    k = next(k for k in bom["components"] if k["bom-ref"] == REF)
    k["purl"] = k["purl"].replace(COMMIT, "6" * 40)   # nach der Ermittlung verändert
    abd = scan.abdeckung(bom, _trivy("pkg:npm/three@0.180.0"), "web", ersatz)
    assert abd["ungescannt_pflicht"] == [REF]


def test_gegenprobe_nur_ueber_alias_ergibt_gueltigen_stand(tmp_path):
    def holen(nutzlast: dict[str, Any]) -> dict[str, Any]:
        if nutzlast.get("commit") == osv.KONTROLLE_COMMIT or \
                nutzlast.get("version") == osv.KONTROLLE_TAG:
            return {"vulns": [{"id": "OSV-2020-1", "aliases": [osv.KONTROLLE_ERWARTET]}]}
        return {}

    osv.aktualisieren(_abfragen(), tmp_path, holen=holen, uhr=lambda: JETZT,
                      tag_commit=_tag_commit)
    assert osv.lade_stand(tmp_path, JETZT).funde


def test_ohne_eigenbauten_kein_osv_netzschritt(tmp_path):
    def holen(nutzlast: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("OSV darf ohne Eigenbauten nicht gefragt werden")

    assert osv.aktualisieren([], tmp_path, holen=holen, uhr=lambda: JETZT) is None
    assert not (tmp_path / osv.OSV_STAND).exists()


def test_gleiche_id_aus_commit_und_tag_vereinigt_die_aliase(tmp_path):
    # Commit-Antwort ohne Alias, Tag-Antwort mit Alias: der Alias darf nicht verloren gehen,
    # sonst wirkte ein Widerruf unter dem CVE-Alias nicht.
    _, basis = _osv_attrappe()

    def holen(nutzlast: dict[str, Any]) -> dict[str, Any]:
        if nutzlast.get("commit") == COMMIT_ZSTD:
            return {"vulns": [{"id": "OSV-2099-1", "modified": "2025-01-01"}]}
        if nutzlast.get("version") == "v1.5.7":
            return {"vulns": [{"id": "OSV-2099-1", "aliases": ["CVE-2099-7"],
                               "modified": "2026-01-01"}]}
        return basis(nutzlast)

    osv.aktualisieren(_abfragen(), tmp_path, holen=holen, uhr=lambda: JETZT,
                      tag_commit=_tag_commit)
    stand = osv.lade_stand(tmp_path, JETZT)
    (fund,) = next(fs for a, fs in stand.funde.items() if a.bom_ref == REF_ZSTD)
    assert fund.aliases == ("CVE-2099-7",)
    assert fund.modified == "2026-01-01"


def test_repo_eigenbau_zstd_ab_1_5_2_und_wie_im_bauskript():
    # B-LK-ZSTD: OSV-2022-15 betrifft zstd v1.4.8 bis v1.5.1 (behoben ab v1.5.2). Der
    # Basis-Transcoder baut den Decoder von zstd selbst; erklärte Version und der Commit, den
    # bauen.sh tatsächlich holt, müssen übereinstimmen und hinter dem betroffenen Bereich liegen.
    (e,) = sbom.lade_eigenbau(WURZEL / "nachweise" / "eigenbau.toml")
    (z,) = [c for c in e.enthalten if c.name == "zstd"]
    assert tuple(int(x) for x in z.version.split(".")) >= (1, 5, 2)
    assert z.tag == f"v{z.version}"
    skript = (WURZEL / e.bau).read_text(encoding="utf-8")
    treffer = re.search(r"^ZSTD_COMMIT=([0-9a-f]{40})\b", skript, re.MULTILINE)
    assert treffer is not None and treffer.group(1) == z.commit
