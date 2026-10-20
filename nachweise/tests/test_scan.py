"""Schwachstellen-Scan mit trivy: DB-Frische (≤ 24 h), Scan im
SBOM-Modus ohne Netz, Schweregrade (UNKNOWN zählt als hoch), Auswertung inkl. der von trivy per
VEX unterdrückten Treffer, CLI `db-aktualisieren` / `scan`."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scanview_nachweise import cli, register, scan
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.scan import (
    DbStand,
    Treffer,
    abdeckung,
    db_aktualisieren,
    db_pruefen,
    flach_fuer_scan,
    je_komponente,
    scanne,
    trivy_scan_befehl,
    werte_trivy_aus,
)
from scanview_nachweise.vex import VexAussage, scan_gate
from scanview_nachweise.werkzeuge import Werkzeug

FIXTURE = Path(__file__).parent / "fixtures" / "trivy-sbom.json"
TRIVY = Werkzeug("trivy", "aquasec/trivy@sha256:" + "1" * 64, "0.74.0", None)
# Echte Zeitstempel aus dem Probe-Download vom 27.09.2026 (9 Nachkommastellen, „Z“).
METADATEN = {"Version": 2, "NextUpdate": "2026-09-28T19:01:19.12003187Z",
             "UpdatedAt": "2026-09-27T19:01:19.120032153Z",
             "DownloadedAt": "2026-09-27T23:22:25.247428807Z"}
AKTUALISIERT = datetime(2026, 9, 27, 19, 1, 19, 120032, tzinfo=UTC)


def fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def db_anlegen(cache: Path, metadaten: dict[str, Any] | None = None, *,
               stand: bool = True, zeit: datetime = AKTUALISIERT,
               feld: str = "UpdatedAt", geladen: datetime | None = None) -> str:
    (cache / "db").mkdir(parents=True)
    (cache / "db" / "trivy.db").write_bytes(b"trivy-db-inhalt")
    (cache / "db" / "metadata.json").write_text(json.dumps(metadaten or METADATEN))
    sha = hashlib.sha256(b"trivy-db-inhalt").hexdigest()
    if stand:
        roh: dict[str, Any] = {"sha256": sha, "zeit": zeit.isoformat(), "feld": feld}
        if geladen is not None:
            roh["netzschritt"] = {"befehl": ["trivy"], "cwd": "trivy-cache",
                                  "zeit": geladen.isoformat(timespec="seconds")}
        (cache / "db-stand.json").write_text(json.dumps(roh))
    return sha


# --- Auswertung --------------------------------------------------------------------------------


def test_auswertung_liefert_treffer_inklusive_unterdrueckter():
    t = werte_trivy_aus(fixture(), "web")
    assert [(x.id, x.schwere, x.status_vex) for x in t] == [
        ("CVE-2026-4800", "hoch", None),
        ("CVE-2020-28500", "mittel", None),
        ("CVE-2099-0002", "hoch", None),          # UNKNOWN → hoch
        ("CVE-2099-0003", "niedrig", None),
        ("CVE-2021-23337", "hoch", "not_affected"),
        ("GHSA-36hh-v3qg-5jq4", "kritisch", None),
    ]
    assert t[0] == Treffer("CVE-2026-4800", "pkg:npm/lodash@4.17.20", "4.17.20", "hoch", None,
                           artefakt="web", bom_ref="pkg:npm/lodash@4.17.20")


def test_trivy_status_fixed_ist_keine_vex_aussage():
    # `Vulnerabilities[].Status` = Fix verfügbar (Upstream), nicht der VEX-Status.
    t = werte_trivy_aus(fixture(), "web")
    assert t[0].status_vex is None
    assert scan_gate([t[0]], [], [], date(2026, 9, 27)) == \
        ["CVE-2026-4800 (hoch) pkg:npm/lodash@4.17.20: keine freigebende VEX-Aussage"]


def test_unterdrueckt_mit_abgelaufener_aussage_blockiert_weiter():
    unterdrueckt = next(x for x in werte_trivy_aus(fixture(), "web") if x.status_vex)
    v = VexAussage("CVE-2021-23337", ("pkg:npm/lodash@4.17.20",), "not_affected",
                   "vulnerable_code_not_in_execute_path", "template() wird nie aufgerufen",
                   datetime(2026, 6, 1, tzinfo=UTC), "sha256:" + "a" * 64, "Andre",
                   "web/package-lock.json")
    assert scan_gate([unterdrueckt], [v], [], date(2026, 9, 27),
                     lock_hashes={"web/package-lock.json": "sha256:" + "a" * 64}) == \
        ["CVE-2021-23337 (hoch) pkg:npm/lodash@4.17.20: keine freigebende VEX-Aussage"]


def test_unknown_zaehlt_als_hoch():
    d = fixture()
    d["Results"] = [d["Results"][0]]
    d["Results"][0]["Vulnerabilities"] = [d["Results"][0]["Vulnerabilities"][2]]
    del d["Results"][0]["ExperimentalModifiedFindings"]
    assert [x.schwere for x in werte_trivy_aus(d, "web")] == ["hoch"]


def test_unbekannte_schwere_ist_fehler():
    d = fixture()
    d["Results"][0]["Vulnerabilities"][0]["Severity"] = "SEVERE"
    with pytest.raises(NachweisFehler) as e:
        werte_trivy_aus(d, "web")
    assert e.value.regel == "scan-schwere"


@pytest.mark.parametrize("kaputt", [
    {},
    {"Results": {}},
    {"Results": [{"Vulnerabilities": [{"PkgName": "x"}]}]},
    {"Results": [{"Vulnerabilities": {}}]},
    {"Results": [{"ExperimentalModifiedFindings": [{"Status": "not_affected"}]}]},
])
def test_kaputte_trivy_ausgabe_ist_fehler(kaputt):
    with pytest.raises(NachweisFehler) as e:
        werte_trivy_aus(kaputt, "web")
    assert e.value.regel == "scan-ausgabe"


def test_ohne_ergebnisse_keine_treffer():
    # trivy lässt `Results` weg, wenn nichts erkannt wurde — das ist kein Fehler, aber die
    # Abdeckung zeigt es (siehe test_abdeckung_*).
    assert werte_trivy_aus({"SchemaVersion": 2, "ArtifactName": "x"}, "web") == []


def test_ohne_purl_bleibt_paketname_und_version():
    d = fixture()
    del d["Results"][0]["Vulnerabilities"][0]["PkgIdentifier"]["PURL"]
    assert werte_trivy_aus(d, "web")[0].paket == "lodash@4.17.20"


def test_abdeckung_nach_bom_ref():
    bom = {"components": [
        {"bom-ref": "pkg:npm/lodash@4.17.20", "type": "library", "purl": "pkg:npm/lodash@4.17.20"},
        {"bom-ref": "pdal|pkg:cargo/pyo3@0.25.1?package-id=e94aa02f239c2713", "type": "library",
         "purl": "pkg:cargo/pyo3@0.25.1"},
        {"bom-ref": "pdal|conda-1", "type": "library", "name": "gdal"},
        {"bom-ref": "pdal|deb-1", "type": "library", "purl": "pkg:deb/ubuntu/zlib1g@1.3"},
        {"bom-ref": "untwine|deb-1", "type": "library", "purl": "pkg:deb/ubuntu/zlib1g@1.3"},
        {"bom-ref": "wurzel", "type": "application", "name": "scanview-web",
         "components": [{"bom-ref": "innen", "type": "file", "name": "a.js"}]},
    ], "metadata": {"component": {"bom-ref": "haupt"}}}
    d = fixture()
    d["Results"].append({"Type": "ubuntu", "Packages": [
        {"Identifier": {"PURL": "pkg:deb/ubuntu/zlib1g@1.3", "BOMRef": "pdal|deb-1"}}]})
    a = abdeckung(bom, d, "import")
    assert a["komponenten"] == 6                 # ohne type=file
    assert a["gescannt"] == 4        # untwine|deb-1 über dieselbe PURL mit abgedeckt
    assert a["ungescannt"] == {"ohne PURL": 2}   # Dateikomponenten zählen nicht mit
    assert a["nach_typ"] == {"cargo": 1, "node-pkg": 1, "python-pkg": 0, "ubuntu": 1}
    # Nur beim Import ist der Image-Inhalt ausgenommen; der Rest muss gescannt sein.
    assert a["ungescannt_pflicht"] == ["wurzel"]
    # In web/server/mcp gibt es keinen Image-Inhalt: jede ungescannte Komponente zählt.
    assert abdeckung(bom, d, "web")["ungescannt_pflicht"] == ["pdal|conda-1", "wurzel"]


def test_je_komponente_ordnet_gleiche_purl_allen_images_zu():
    # trivy fasst gleiche PURLs verschiedener Images zusammen und nennt nur eine (zufällige)
    # bom-ref — das SBOM entscheidet, in welchen Images das Paket steckt.
    bom = {"components": [
        {"bom-ref": "untwine|deb-z", "purl": "pkg:deb/ubuntu/zlib1g@1.3"},
        {"bom-ref": "pdal|deb-z", "purl": "pkg:deb/ubuntu/zlib1g@1.3"},
        {"bom-ref": "pdal|x", "purl": "pkg:deb/ubuntu/x@1"}]}
    t = Treffer("CVE-1", "pkg:deb/ubuntu/zlib1g@1.3", "1.3", "mittel", None, artefakt="import",
                bom_ref="untwine|deb-z")
    ohne = Treffer("CVE-2", "demo@1", "1", "hoch", None, artefakt="import", bom_ref=None)
    assert je_komponente([t, ohne], bom) == [
        Treffer("CVE-1", "pkg:deb/ubuntu/zlib1g@1.3", "1.3", "mittel", None, artefakt="import",
                bom_ref="pdal|deb-z"),
        Treffer("CVE-1", "pkg:deb/ubuntu/zlib1g@1.3", "1.3", "mittel", None, artefakt="import",
                bom_ref="untwine|deb-z"),
        ohne]


def test_je_komponente_vergleicht_purl_dekodiert():
    # SBOM (syft) kodiert die Epoche als „1%3A…“, trivy gibt „1:…“ aus.
    bom = {"components": [
        {"bom-ref": "pdal|git", "purl": "pkg:deb/ubuntu/git@1%3A2.43.0?arch=amd64"},
        {"bom-ref": "untwine|git", "purl": "pkg:deb/ubuntu/git@1%3A2.43.0?arch=amd64"}]}
    t = Treffer("CVE-1", "pkg:deb/ubuntu/git@1:2.43.0?arch=amd64", "1:2.43.0", "mittel", None,
                artefakt="import", bom_ref="pdal|git")
    assert [x.bom_ref for x in je_komponente([t], bom)] == ["pdal|git", "untwine|git"]


def test_abdeckung_modell_offen_aber_keine_pflicht():
    bom = {"components": [
        {"bom-ref": "pkg:npm/lodash@4.17.20", "type": "library", "purl": "pkg:npm/lodash@4.17.20"},
        {"bom-ref": "modell:x@1", "type": "machine-learning-model", "name": "X"},
        {"bom-ref": "bibliothek-ohne-purl", "type": "library", "name": "y"},
    ], "metadata": {"component": {"bom-ref": "haupt"}}}
    a = abdeckung(bom, fixture(), "web")
    assert a["ungescannt"] == {"ohne PURL": 2}         # das Modell bleibt als ungescannt gezählt
    assert a["modelle"] == ["modell:x@1"]
    # Nur der Typ befreit, nicht „ohne PURL“:
    assert a["ungescannt_pflicht"] == ["bibliothek-ohne-purl"]


def test_abdeckung_vergleicht_purl_dekodiert():
    bom = {"components": [{"bom-ref": "untwine|git", "type": "library",
                           "purl": "pkg:deb/ubuntu/git@1%3A2.43.0"}]}
    daten = {"SchemaVersion": 2, "Results": [{"Type": "ubuntu", "Packages": [
        {"Identifier": {"PURL": "pkg:deb/ubuntu/git@1:2.43.0", "BOMRef": "pdal|git"}}]}]}
    assert abdeckung(bom, daten, "import")["gescannt"] == 1


# --- Befehl --------------------------------------------------------------------------------------


def test_scan_befehl_ohne_netz_und_ohne_db_update(tmp_path):
    sbom = tmp_path / "a.cdx.json"
    sbom.write_text("{}")
    vex = tmp_path / "vex.json"
    vex.write_text("{}")
    cache = tmp_path / "cache"
    cache.mkdir()
    b = trivy_scan_befehl(sbom, cache, vex, TRIVY)
    assert b[:5] == ["docker", "run", "--rm", "--pull", "never"]
    assert b[b.index("--network") + 1] == "none"
    assert f"{cache}:/cache:ro" in b and f"{sbom}:/eingabe/sbom.cdx.json:ro" in b
    assert f"{vex}:/eingabe/vex.openvex.json:ro" in b
    rest = b[b.index(TRIVY.image) + 1:]
    assert rest[0] == "sbom"
    for flag in ("--offline-scan", "--skip-db-update", "--skip-java-db-update",
                 "--skip-vex-repo-update", "--skip-version-check", "--disable-telemetry",
                 "--show-suppressed"):
        assert flag in rest
    assert rest[rest.index("--format") + 1] == "json"
    assert rest[rest.index("--vex") + 1] == "/eingabe/vex.openvex.json"
    assert rest[rest.index("--cache-dir") + 1] == "/cache"
    assert rest[-1] == "/eingabe/sbom.cdx.json"


def test_scanne_wertet_stdout_aus(tmp_path, monkeypatch):
    sbom = tmp_path / "scanview-web-0.1.0.cdx.json"
    sbom.write_text("{}")
    vex = tmp_path / "vex.json"
    vex.write_text("{}")
    aufrufe: list[list[str]] = []

    def laufen(befehl: list[str], regel: str, **_: object) -> subprocess.CompletedProcess[str]:
        aufrufe.append(befehl)
        assert regel == "scan-trivy"
        return subprocess.CompletedProcess(befehl, 0, FIXTURE.read_text(), "")

    monkeypatch.setattr(scan, "ausfuehren", laufen)
    t = scanne(sbom, tmp_path, vex, {"trivy": TRIVY}, artefakt="web")
    assert len(t) == 6 and all(x.artefakt == "web" for x in t)
    assert "--network" in aufrufe[0]


def test_flache_kopie_fuer_trivy():
    # trivy 0.74.0 prüft verschachtelte CycloneDX-Komponenten nicht (Probe: ol@10.10.0 bringt
    # eigene earcut/geotiff/pbf mit, die ungescannt blieben) — gescannt wird eine flache Kopie.
    bom = {"bomFormat": "CycloneDX", "components": [
        {"bom-ref": "a", "purl": "pkg:npm/a@1", "components": [
            {"bom-ref": "a|b", "purl": "pkg:npm/b@2", "components": [
                {"bom-ref": "a|b|c", "purl": "pkg:npm/c@3"}]}]},
        {"bom-ref": "d", "purl": "pkg:npm/d@4"}], "dependencies": [{"ref": "a"}]}
    flach = flach_fuer_scan(bom)
    assert [k["bom-ref"] for k in flach["components"]] == ["a", "a|b", "a|b|c", "d"]
    assert all("components" not in k for k in flach["components"])
    assert flach["dependencies"] == bom["dependencies"]
    assert "components" in bom["components"][0]          # Original unverändert


def test_scanne_uebergibt_flache_kopie(tmp_path, monkeypatch):
    sbom = tmp_path / "s.cdx.json"
    sbom.write_text(json.dumps({"components": [
        {"bom-ref": "a", "components": [{"bom-ref": "a|b"}]}]}))
    (tmp_path / "v.json").write_text("{}")
    gesehen: list[Any] = []

    def laufen(befehl: list[str], regel: str, **_: object) -> subprocess.CompletedProcess[str]:
        mount = next(b for b in befehl if b.endswith(":/eingabe/sbom.cdx.json:ro"))
        gesehen.append(json.loads(Path(mount.split(":")[0]).read_text()))
        return subprocess.CompletedProcess(befehl, 0, '{"SchemaVersion": 2}', "")

    monkeypatch.setattr(scan, "ausfuehren", laufen)
    assert scanne(sbom, tmp_path, tmp_path / "v.json", {"trivy": TRIVY}, artefakt="web") == []
    assert [k["bom-ref"] for k in gesehen[0]["components"]] == ["a", "a|b"]


def test_scanne_ohne_trivy_pin_ist_fehler(tmp_path):
    with pytest.raises(NachweisFehler) as e:
        scanne(tmp_path / "a.json", tmp_path, tmp_path / "v.json", {}, artefakt="web")
    assert e.value.regel == "werkzeuge"


def test_scanne_fehlendes_sbom_ist_fehler(tmp_path):
    with pytest.raises(NachweisFehler) as e:
        scanne(tmp_path / "fehlt.json", tmp_path, tmp_path / "v.json", {"trivy": TRIVY},
               artefakt="web")
    assert e.value.regel == "scan-sbom"


def test_scanne_kein_json_ist_fehler(tmp_path, monkeypatch):
    for n in ("a.json", "v.json"):
        (tmp_path / n).write_text("{}")
    monkeypatch.setattr(scan, "ausfuehren", lambda b, r, **_: subprocess.CompletedProcess(
        b, 0, "WARN kaputt", ""))
    with pytest.raises(NachweisFehler) as e:
        scanne(tmp_path / "a.json", tmp_path, tmp_path / "v.json", {"trivy": TRIVY},
               artefakt="web")
    assert e.value.regel == "scan-ausgabe"


# --- DB ------------------------------------------------------------------------------------------


def test_db_frisch(tmp_path):
    sha = db_anlegen(tmp_path)
    s = db_pruefen(tmp_path, AKTUALISIERT + timedelta(hours=23))
    assert (s.zeit, s.sha256, s.feld) == (AKTUALISIERT, sha, "UpdatedAt")


def test_db_genau_24_stunden_gilt_noch(tmp_path):
    db_anlegen(tmp_path)
    assert db_pruefen(tmp_path, AKTUALISIERT + timedelta(hours=24)).feld == "UpdatedAt"


def test_db_25_stunden_alt_ist_fehler(tmp_path):
    db_anlegen(tmp_path)
    with pytest.raises(NachweisFehler) as e:
        db_pruefen(tmp_path, AKTUALISIERT + timedelta(hours=25))
    assert e.value.regel == "db-alter"


def test_db_verzoegert_gilt_bis_72_stunden_wenn_frisch_geladen(tmp_path):
    # Die Quelle veröffentlicht verspätet: Inhalt 50 h alt, aber vor 2 h geladen — also der
    # neueste veröffentlichte Stand. Gilt, im Ergebnis als verzögert gekennzeichnet.
    jetzt = AKTUALISIERT + timedelta(hours=50)
    db_anlegen(tmp_path, geladen=jetzt - timedelta(hours=2))
    s = db_pruefen(tmp_path, jetzt)
    assert s.verzoegert is True


def test_db_frisch_ist_nicht_verzoegert(tmp_path):
    db_anlegen(tmp_path, geladen=AKTUALISIERT + timedelta(hours=1))
    assert db_pruefen(tmp_path, AKTUALISIERT + timedelta(hours=23)).verzoegert is False


@pytest.mark.parametrize("inhalt_h, geladen_vor_h, teil", [
    (25, 25, "nicht neu geladen"),  # alt und seit über 24 h nicht geprüft, ob es Neueres gibt
    (25, None, "nicht neu geladen"),  # ohne protokollierten Ladezeitpunkt kein Nachweis
    (73, 1, "72 h"),                # frisch geladen, aber die Quelle hängt zu lange
])
def test_db_verzoegert_grenzen(tmp_path, inhalt_h, geladen_vor_h, teil):
    jetzt = AKTUALISIERT + timedelta(hours=inhalt_h)
    geladen = None if geladen_vor_h is None else jetzt - timedelta(hours=geladen_vor_h)
    db_anlegen(tmp_path, geladen=geladen)
    with pytest.raises(NachweisFehler, match=teil) as e:
        db_pruefen(tmp_path, jetzt)
    assert e.value.regel == "db-alter"


def test_db_ladezeit_aus_der_zukunft_ist_kein_nachweis(tmp_path):
    jetzt = AKTUALISIERT + timedelta(hours=30)
    db_anlegen(tmp_path, geladen=jetzt + timedelta(hours=1))
    with pytest.raises(NachweisFehler) as e:
        db_pruefen(tmp_path, jetzt)
    assert e.value.regel == "db-alter"


def test_db_fehlt_ist_fehler(tmp_path):
    with pytest.raises(NachweisFehler) as e:
        db_pruefen(tmp_path, AKTUALISIERT)
    assert e.value.regel == "db-alter"


def test_db_ohne_trivy_db_ist_fehler(tmp_path):
    db_anlegen(tmp_path)
    (tmp_path / "db" / "trivy.db").unlink()
    with pytest.raises(NachweisFehler) as e:
        db_pruefen(tmp_path, AKTUALISIERT)
    assert e.value.regel == "db-alter"


def test_db_nur_downloaded_at_wird_vermerkt(tmp_path):
    db_anlegen(tmp_path, {"Version": 2, "DownloadedAt": "2026-09-27T23:22:25.247428807Z"},
               zeit=datetime(2026, 9, 27, 23, 22, 25, 247428, tzinfo=UTC), feld="DownloadedAt")
    s = db_pruefen(tmp_path, datetime(2026, 9, 28, tzinfo=UTC))
    assert s.feld == "DownloadedAt"
    assert s.zeit == datetime(2026, 9, 27, 23, 22, 25, 247428, tzinfo=UTC)


@pytest.mark.parametrize("metadaten", [{"Version": 2}, {"UpdatedAt": "gestern"},
                                       {"UpdatedAt": "2026-09-27T19:01:19"}, []])
def test_db_zeit_unlesbar_ist_fehler(tmp_path, metadaten):
    db_anlegen(tmp_path, metadaten or {"x": 1})
    if metadaten == []:
        (tmp_path / "db" / "metadata.json").write_text("[]")
    with pytest.raises(NachweisFehler) as e:
        db_pruefen(tmp_path, AKTUALISIERT)
    assert e.value.regel == "db-alter"


def test_db_aus_der_zukunft_ist_fehler(tmp_path):
    db_anlegen(tmp_path)
    with pytest.raises(NachweisFehler) as e:
        db_pruefen(tmp_path, AKTUALISIERT - timedelta(hours=2))
    assert e.value.regel == "db-alter"


def test_db_ausserhalb_des_protokollierten_schritts_geaendert(tmp_path):
    db_anlegen(tmp_path)
    (tmp_path / "db-stand.json").write_text(json.dumps({"sha256": "0" * 64}))
    with pytest.raises(NachweisFehler) as e:
        db_pruefen(tmp_path, AKTUALISIERT)
    assert e.value.regel == "db-stand"


@pytest.mark.parametrize("abweichung", [{"zeit": "2026-09-26T19:01:19.120032+00:00"},
                                        {"feld": "DownloadedAt"}])
def test_db_stand_zeit_oder_feld_weicht_ab(tmp_path, abweichung):
    # Gleicher Hash, aber metadata.json nachträglich verändert (z. B. UpdatedAt vorgedreht).
    sha = db_anlegen(tmp_path)
    stand = {"sha256": sha, "zeit": AKTUALISIERT.isoformat(), "feld": "UpdatedAt", **abweichung}
    (tmp_path / "db-stand.json").write_text(json.dumps(stand))
    with pytest.raises(NachweisFehler) as e:
        db_pruefen(tmp_path, AKTUALISIERT)
    assert e.value.regel == "db-stand"


def test_db_ohne_stand_datei_ist_fehler(tmp_path):
    db_anlegen(tmp_path, stand=False)
    with pytest.raises(NachweisFehler) as e:
        db_pruefen(tmp_path, AKTUALISIERT)
    assert e.value.regel == "db-stand"


def test_db_aktualisieren_ist_der_netzschritt(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    aufrufe: list[list[str]] = []

    def laufen(befehl: list[str], regel: str, **_: object) -> subprocess.CompletedProcess[str]:
        aufrufe.append(befehl)
        assert regel == "db-aktualisieren"
        db = cache / "db"
        db.mkdir()
        (db / "trivy.db").write_bytes(b"neu")
        (db / "metadata.json").write_text(json.dumps(METADATEN))
        return subprocess.CompletedProcess(befehl, 0, "", "")

    monkeypatch.setattr(scan, "ausfuehren", laufen)
    s = db_aktualisieren(cache, {"trivy": TRIVY})
    [befehl] = aufrufe
    assert "--network" not in befehl                    # einziger Schritt mit Netz
    assert f"{cache}:/cache:rw" in befehl
    rest = befehl[befehl.index(TRIVY.image) + 1:]
    assert rest[:2] == ["image", "--download-db-only"]
    assert s.sha256 == hashlib.sha256(b"neu").hexdigest()
    assert s.zeit == AKTUALISIERT and s.feld == "UpdatedAt"
    assert s.netzschritt is not None
    assert s.netzschritt.befehl == ["trivy", *rest]
    assert s.netzschritt.cwd == "trivy-cache"
    gespeichert = json.loads((cache / "db-stand.json").read_text())
    assert gespeichert["sha256"] == s.sha256 and gespeichert["feld"] == "UpdatedAt"
    assert gespeichert["netzschritt"]["befehl"] == s.netzschritt.befehl
    # Danach besteht die Prüfung (Hash passt zur protokollierten Aktualisierung).
    assert db_pruefen(cache, AKTUALISIERT + timedelta(hours=1)).netzschritt == s.netzschritt


def test_db_aktualisieren_ohne_db_danach_ist_fehler(tmp_path, monkeypatch):
    monkeypatch.setattr(scan, "ausfuehren", lambda b, r, **_: subprocess.CompletedProcess(
        b, 0, "", ""))
    with pytest.raises(NachweisFehler) as e:
        db_aktualisieren(tmp_path / "c", {"trivy": TRIVY})
    assert e.value.regel == "db-alter"


# --- CLI -----------------------------------------------------------------------------------------


def _repo(tmp_path: Path, vex_statements: list[Any] | None = None) -> Path:
    wurzel = tmp_path / "repo"
    (wurzel / "nachweise").mkdir(parents=True)
    (wurzel / "nachweise" / "werkzeuge.json").write_text(json.dumps(
        {"trivy": {"image": TRIVY.image, "version": "0.74.0"}}))
    (wurzel / "docs" / "compliance").mkdir(parents=True)
    (wurzel / "docs" / "compliance" / "vex.openvex.json").write_text(json.dumps({
        "@context": "https://openvex.dev/ns/v0.2.0", "@id": "urn:scanview:vex",
        "author": "Andre Stiebitz", "timestamp": "2026-09-27T00:00:00Z", "version": 1,
        "statements": vex_statements or []}))
    for pfad in ("web/package-lock.json", "server/uv.lock", "mcp-server/uv.lock",
                 "import/uv.lock"):
        (wurzel / pfad).parent.mkdir(parents=True, exist_ok=True)
        (wurzel / pfad).write_text(pfad)
    return wurzel


def _sbom_ordner(tmp_path: Path) -> Path:
    ordner = tmp_path / "ziel" / "sbom"
    ordner.mkdir(parents=True)
    for name in ("web", "server", "mcp", "import"):
        komponenten = [{"bom-ref": f"{name}-a", "type": "library",
                        "purl": f"pkg:npm/{name}-a@1"}]
        if name == "import":       # ungescannter Image-Inhalt ist nur hier ausgenommen
            komponenten.append({"bom-ref": "pdal|conda-x", "type": "library", "name": "gdal"})
        (ordner / f"scanview-{name}-0.1.0.cdx.json").write_text(json.dumps(
            {"components": komponenten}))
    (ordner / "scanview-0.1.0.cdx.json").write_text("{}")
    return ordner


@pytest.fixture
def cli_umgebung(tmp_path, monkeypatch):
    wurzel = _repo(tmp_path)
    ordner = _sbom_ordner(tmp_path)
    cache = tmp_path / "cache"
    db_anlegen(cache)
    monkeypatch.setattr(scan, "jetzt", lambda: AKTUALISIERT + timedelta(hours=1))
    monkeypatch.setattr(cli, "_register_laden", lambda w: register.Register([], None, [], []))  # type: ignore[arg-type]
    gescannt: list[tuple[str, str | None]] = []
    treffer: dict[str, list[Treffer]] = {}

    def falsch_trivy(sbom: Path, c: Path, vex: Path, w: object) -> dict[str, Any]:
        # trivy „kennt“ jede Komponente mit PURL (wie in echt: conda ohne PURL bleibt offen).
        assert c == cache
        bom = json.loads(sbom.read_text())
        pakete = [{"Identifier": {"PURL": k["purl"], "BOMRef": k["bom-ref"]}}
                  for k in bom.get("components", []) if "purl" in k]
        return {"SchemaVersion": 2, "sbom": sbom.name,
                "Results": [{"Type": "node-pkg", "Packages": pakete}]}

    def falsch_auswerten(daten: dict[str, Any], artefakt: str | None) -> list[Treffer]:
        gescannt.append((daten["sbom"], artefakt))
        return treffer.get(artefakt or "", [])

    monkeypatch.setattr(scan, "trivy_json", falsch_trivy)
    monkeypatch.setattr(scan, "werte_trivy_aus", falsch_auswerten)
    args = ["scan", "--sbom-ordner", str(ordner), "--cache", str(cache), "--wurzel", str(wurzel),
            "--heute", "2026-09-27"]
    return args, ordner, gescannt, treffer


def test_cli_scan_gruen(cli_umgebung, capsys):
    args, ordner, gescannt, _ = cli_umgebung
    assert cli.main(args) == 0
    assert sorted(gescannt) == [("scanview-import-0.1.0.cdx.json", "import"),
                                ("scanview-mcp-0.1.0.cdx.json", "mcp"),
                                ("scanview-server-0.1.0.cdx.json", "server"),
                                ("scanview-web-0.1.0.cdx.json", "web")]
    ergebnis = json.loads((ordner / "scan-ergebnis.json").read_text())
    assert ergebnis["ergebnis"] == "grün"
    assert ergebnis["db"]["feld"] == "UpdatedAt"
    assert ergebnis["blockierend_scan"] == [] and ergebnis["blockierend_befunde"] == []
    assert "grün" in capsys.readouterr().out


def test_cli_scan_blockiert_und_vorschlag(cli_umgebung, capsys):
    args, ordner, _, treffer = cli_umgebung
    treffer["web"] = [Treffer("CVE-2099-0001", "pkg:npm/demo@1.0.0", "1.0.0", "hoch", None,
                              artefakt="web", bom_ref="x"),
                      Treffer("CVE-2099-0002", "pkg:npm/demo@1.0.0", "1.0.0", "mittel", None,
                              artefakt="web", bom_ref="x")]
    assert cli.main(args) == 1
    out = capsys.readouterr().out
    assert "BLOCKIERT CVE-2099-0001 (hoch) pkg:npm/demo@1.0.0: keine freigebende VEX-Aussage" \
        in out
    assert "VORSCHLAG B-SCAN-CVE-2099-0002" in out
    ergebnis = json.loads((ordner / "scan-ergebnis.json").read_text())
    assert ergebnis["ergebnis"] == "blockiert"
    assert len(ergebnis["treffer"]) == 2
    assert ergebnis["befund_vorschlaege"][0].startswith("B-SCAN-CVE-2099-0002")


def test_cli_scan_db_alt_exit_2(cli_umgebung, monkeypatch, capsys):
    args, ordner, _, _ = cli_umgebung
    monkeypatch.setattr(scan, "jetzt", lambda: AKTUALISIERT + timedelta(hours=25))
    args[args.index("--heute") + 1] = "2026-09-28"      # nicht vor dem (gestellten) Datum
    assert cli.main(args) == 2
    assert "NACHWEIS-FEHLER db-alter" in capsys.readouterr().err
    assert not (ordner / "scan-ergebnis.json").exists()


def test_cli_scan_fehlendes_sbom_exit_2(cli_umgebung, capsys):
    args, ordner, _, _ = cli_umgebung
    (ordner / "scanview-mcp-0.1.0.cdx.json").unlink()
    assert cli.main(args) == 2
    assert "NACHWEIS-FEHLER scan-sbom" in capsys.readouterr().err


def test_cli_scan_ueberschreibt_nicht(cli_umgebung, capsys):
    args, ordner, _, _ = cli_umgebung
    (ordner / "scan-ergebnis.json").write_text("{}")
    assert cli.main(args) == 2
    assert "existiert schon" in capsys.readouterr().err


def test_cli_scan_ungescannte_eigene_komponente_exit_2(cli_umgebung, capsys):
    # Außerhalb des Image-Inhalts darf keine Komponente ungescannt bleiben: sonst hieße
    # „0 Treffer“ nur „nicht geprüft“.
    args, ordner, _, _ = cli_umgebung
    (ordner / "scanview-web-0.1.0.cdx.json").write_text(json.dumps(
        {"components": [{"bom-ref": "web-ohne-purl", "type": "library", "name": "x"}]}))
    assert cli.main(args) == 2
    err = capsys.readouterr().err
    assert "NACHWEIS-FEHLER scan-abdeckung" in err and "web-ohne-purl" in err
    assert not (ordner / "scan-ergebnis.json").exists()


def test_cli_scan_web_mit_image_praefix_exit_2(cli_umgebung, capsys):
    # Die Image-Ausnahme gilt nur für den Import — nicht für web/server/mcp.
    args, ordner, _, _ = cli_umgebung
    (ordner / "scanview-web-0.1.0.cdx.json").write_text(json.dumps(
        {"components": [{"bom-ref": "pdal|x", "type": "library", "name": "gdal"}]}))
    assert cli.main(args) == 2
    err = capsys.readouterr().err
    assert "NACHWEIS-FEHLER scan-abdeckung" in err and "pdal|x" in err


@pytest.mark.parametrize("inhalt", [{}, {"components": []},
                                    {"components": [{"bom-ref": "f", "type": "file"}]}])
def test_cli_scan_leeres_sbom_exit_2(cli_umgebung, capsys, inhalt):
    args, ordner, _, _ = cli_umgebung
    (ordner / "scanview-server-0.1.0.cdx.json").write_text(json.dumps(inhalt))
    assert cli.main(args) == 2
    err = capsys.readouterr().err
    assert "NACHWEIS-FEHLER scan-abdeckung" in err and "ohne Komponenten" in err


def test_cli_scan_heute_vor_dem_echten_datum_exit_2(cli_umgebung, capsys):
    # Ein zurückgedrehter Stichtag würde abgelaufene VEX-Aussagen/Befundfristen wieder gültig
    # machen.
    args, ordner, _, _ = cli_umgebung
    args[args.index("--heute") + 1] = "2026-09-26"
    assert cli.main(args) == 2
    assert "NACHWEIS-FEHLER scan-heute" in capsys.readouterr().err
    assert not (ordner / "scan-ergebnis.json").exists()


def test_cli_scan_heute_spaeter_ist_erlaubt(cli_umgebung):
    args, _, _, _ = cli_umgebung
    args[args.index("--heute") + 1] = "2026-10-15"
    assert cli.main(args) == 0


def test_cli_db_aktualisieren(tmp_path, monkeypatch, capsys):
    wurzel = _repo(tmp_path)
    cache = tmp_path / "neu" / "cache"
    gesehen: list[Path] = []

    def falsch(c: Path, w: object) -> DbStand:
        gesehen.append(c)
        return DbStand(AKTUALISIERT, "ab" * 32, "UpdatedAt")

    monkeypatch.setattr(scan, "db_aktualisieren", falsch)
    assert cli.main(["db-aktualisieren", "--cache", str(cache), "--wurzel", str(wurzel)]) == 0
    assert gesehen == [cache]
    assert "UpdatedAt" in capsys.readouterr().out


def test_standard_cache_ausserhalb_des_repos():
    assert scan.STANDARD_CACHE == Path.home() / ".cache" / "scanview-nachweise" / "trivy"
