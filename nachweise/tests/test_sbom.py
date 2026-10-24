"""SBOM nach BSI TR-03183-2 v2.1.0: Nachbearbeitung, Abdeckung, Validierung,
Zusammenführung. Reine Funktionen gegen die gekürzte echte cyclonedx-npm-Ausgabe
(`fixtures/sbom-roh.json`); Docker-Läufe werden über gepatchtes `ausfuehren` geprüft."""

from __future__ import annotations

import base64
import copy
import dataclasses
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scanview_nachweise import cli, sbom
from scanview_nachweise import lizenzen as lizenzpruefung
from scanview_nachweise.artefakte import Artefakt, Bauergebnis, Datei, Netzschritt
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.werkzeuge import Werkzeug

FIXTURES = Path(__file__).parent / "fixtures"
WURZEL = Path(__file__).resolve().parents[2]
JETZT = datetime(2026, 9, 28, 10, 11, 12, tzinfo=UTC)
ERSTELLER = "security@example.org"

WEB_DATEIEN = [
    "web/dist/THIRD_PARTY_LICENSES.txt",
    "web/dist/assets/plattformApi-Byg8MBwP.js",
    "web/dist/assets/portal-BxtFp4Ha.js",
    "web/dist/assets/portal-DFtBkwb4.css",
    "web/dist/assets/viewer-CflcUFr4.js",
    "web/dist/assets/viewer-DBLl-oQl.css",
    "web/dist/index.html",
    "web/dist/portal.html",
    "web/dist/wasm/laz-perf.wasm",
    "web/dist/wasm/basis/basis_transcoder.js",
    "web/dist/wasm/basis/basis_transcoder.wasm",
]
SERVER_DATEIEN = [
    "deploy/platform/einrichten.sh",
    "deploy/platform/scanview.caddy",
    "server/pyproject.toml",
    "server/uv.lock",
    "server/src/scanview_server/web.py",
    "server/src/scanview_server/migrations/0001_konten.sql",
]


def _h(text: str) -> str:
    return hashlib.sha512(text.encode()).hexdigest()


def _artefakt(name: str, dateien: list[str], sha: str = "a" * 64) -> Artefakt:
    return Artefakt(name=name, pfad=Path(f"/x/scanview-{name}-0.1.0.tar.gz"), sha256=sha,
                    sha512="b" * 128,
                    lieferumfang=[Datei(p, _h(p), len(p)) for p in dateien])


def _roh(*, eigenbau: bool = True) -> dict[str, Any]:
    """Generator-SBOM wie `_roh_web` sie weiterreicht: npm-Fixture plus Eigenbau aus dem Repo
    (`eigenbau=False`: nur die npm-Ausgabe, wie `npm_pruefen` sie sieht)."""
    daten: dict[str, Any] = json.loads((FIXTURES / "sbom-roh.json").read_text(encoding="utf-8"))
    if eigenbau:
        sbom.eigenbau_einfuegen(daten, sbom.lade_eigenbau(WURZEL / "nachweise" / "eigenbau.toml"),
                                WURZEL)
    return daten


def _web(bom: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    art = kw.pop("artefakt", _artefakt("web", WEB_DATEIEN))
    return sbom.nachbearbeiten(bom if bom is not None else _roh(), art, ERSTELLER,
                               "urn:scanview:sbom:web:0.1.0", version="0.1.0", jetzt=JETZT, **kw)


def _komponenten(bom: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {k["bom-ref"]: k for k in sbom.alle_komponenten(bom["components"])}


def _props(k: dict[str, Any]) -> dict[str, list[str]]:
    ergebnis: dict[str, list[str]] = {}
    for p in k.get("properties", []):
        ergebnis.setdefault(p["name"], []).append(p["value"])
    return ergebnis


def _verteilung(k: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in k.get("externalReferences", []) if r["type"] == "distribution"]


def _sha512_verteilung(k: dict[str, Any]) -> list[str]:
    return [h["content"] for r in _verteilung(k) for h in r.get("hashes", [])
            if h["alg"] == "SHA-512"]


def _kompositionen(bom: dict[str, Any]) -> dict[str, set[str]]:
    ergebnis: dict[str, set[str]] = {}
    for c in bom["compositions"]:
        ergebnis.setdefault(c["aggregate"], set()).update(c.get("assemblies", []))
        assert set(c.get("assemblies", [])) == set(c.get("dependencies", []))
    return ergebnis


# --- Klassifikation (BSI-Taxonomie, Werte laut TR §5.2.2) -----------------------------------------

@pytest.mark.parametrize(("name", "erwartet"), [
    ("scanview-web-0.1.0.tar.gz", ("non-executable", "archive", "structured")),
    ("giro3d-2.0.4.tgz", ("non-executable", "archive", "structured")),
    ("fastapi-0.1-py3-none-any.whl", ("non-executable", "archive", "structured")),
    ("laz-perf.wasm", ("executable", "no archive", "unstructured")),
    ("viewer-CflcUFr4.js", ("executable", "no archive", "unstructured")),
    ("_cffi.cpython-311-x86_64-linux-gnu.so", ("executable", "no archive", "unstructured")),
    ("modul.py", ("executable", "no archive", "unstructured")),
    ("einrichten.sh", ("executable", "no archive", "unstructured")),
    ("endpunkte.json", ("non-executable", "no archive", "structured")),
    ("uv.lock", ("non-executable", "no archive", "structured")),
    ("portal.css", ("non-executable", "no archive", "structured")),
    ("index.html", ("non-executable", "no archive", "unstructured")),
])
def test_klassifikation(name: str, erwartet: tuple[str, str, str]) -> None:
    k = sbom.klassifiziere(name)
    assert k is not None
    assert (k["executable"], k["archive"], k["structured"]) == erwartet


def test_klassifikation_unbekannt_ist_none() -> None:
    assert sbom.klassifiziere("programm") is None
    assert sbom.klassifiziere("daten.xyz") is None


# --- Nachbearbeitung ----------------------------------------------------------------------------

def test_metadaten_ersteller_zeit_format() -> None:
    bom = _web()
    assert bom["specVersion"] == "1.6"
    assert bom["bomFormat"] == "CycloneDX"
    m = bom["metadata"]
    assert m["timestamp"] == "2026-09-28T10:11:12Z"
    ug = "Stiebitz Souverain Systems UG (haftungsbeschränkt)"
    assert m["manufacturer"] == {"name": ug, "contact": [{"email": ERSTELLER}]}
    assert m["authors"] == [{"name": ug, "email": ERSTELLER}]
    assert "cdx:reproducible" not in _props(m)
    assert _props(m)["scanview:sbom-uri"] == ["urn:scanview:sbom:web:0.1.0"]


def test_zeit_wird_nach_utc_umgerechnet() -> None:
    from datetime import timedelta, timezone
    lokal = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    bom = sbom.nachbearbeiten(_roh(), _artefakt("web", WEB_DATEIEN), ERSTELLER, "urn:x",
                              version="0.1.0", jetzt=lokal)
    assert bom["metadata"]["timestamp"] == "2026-09-28T10:00:00Z"


def test_seriennummer_deterministisch() -> None:
    a = _web()["serialNumber"]
    b = sbom.nachbearbeiten(_roh(), _artefakt("web", WEB_DATEIEN), ERSTELLER,
                            "urn:scanview:sbom:web:0.1.0", version="0.1.0",
                            jetzt=datetime(2030, 1, 1, tzinfo=UTC))["serialNumber"]
    assert a == b
    assert a.startswith("urn:uuid:")
    anders_sha = _web(artefakt=_artefakt("web", WEB_DATEIEN, sha="c" * 64))["serialNumber"]
    anders_version = sbom.nachbearbeiten(_roh(), _artefakt("web", WEB_DATEIEN), ERSTELLER,
                                         "urn:x", version="0.1.1", jetzt=JETZT)["serialNumber"]
    assert len({a, anders_sha, anders_version}) == 3


def test_eingabe_bleibt_unveraendert() -> None:
    roh = _roh()
    vorher = copy.deepcopy(roh)
    _web(roh)
    assert roh == vorher


def test_bsi_eigenschaften_je_komponente_genau_einmal() -> None:
    bom = _web()
    erlaubt = {
        "bsi:component:executable": {"executable", "non-executable"},
        "bsi:component:archive": {"archive", "no archive"},
        "bsi:component:structured": {"structured", "unstructured"},
    }
    alle = sbom.alle_komponenten(bom["components"])
    assert "scanview-web|ol@10.10.0|pbf@5.1.2" in {k["bom-ref"] for k in alle}  # verschachtelt
    for k in [bom["metadata"]["component"], *alle]:
        p = _props(k)
        for name, werte in erlaubt.items():
            assert len(p[name]) == 1, (k["bom-ref"], name)
            assert p[name][0] in werte
        assert len(p["bsi:component:filename"]) == 1
    giro = _komponenten(bom)["scanview-web|@giro3d/giro3d@2.0.4"]
    assert _props(giro)["bsi:component:filename"] == ["giro3d-2.0.4.tgz"]
    assert _props(giro)["bsi:component:archive"] == ["archive"]


def test_vorhandene_bsi_eigenschaften_werden_ersetzt_nicht_verdoppelt() -> None:
    roh = _roh()
    roh["components"][0]["properties"].append({"name": "bsi:component:archive",
                                               "value": "no archive"})
    giro = _komponenten(_web(roh))["scanview-web|@giro3d/giro3d@2.0.4"]
    assert _props(giro)["bsi:component:archive"] == ["archive"]


def test_hauptkomponente() -> None:
    art = _artefakt("web", WEB_DATEIEN)
    haupt = _web(artefakt=art)["metadata"]["component"]
    assert haupt["version"] == "0.1.0"
    assert _props(haupt)["bsi:component:filename"] == ["scanview-web-0.1.0.tar.gz"]
    assert _sha512_verteilung(haupt) == [art.sha512]
    assert _verteilung(haupt)[0]["url"] == "scanview-web-0.1.0.tar.gz"
    assert haupt["manufacturer"]["contact"] == [{"email": ERSTELLER}]
    lizenzen = {(lz["license"]["id"], lz["license"]["acknowledgement"])
                for lz in haupt["licenses"]}
    assert lizenzen == {("EUPL-1.2", "concluded"), ("EUPL-1.2", "declared")}


def test_npm_hash_bleibt_in_distribution() -> None:
    roh = _roh()
    erwartet = roh["components"][0]["externalReferences"][0]["hashes"][0]["content"]
    giro = _komponenten(_web(roh))["scanview-web|@giro3d/giro3d@2.0.4"]
    assert _sha512_verteilung(giro) == [erwartet]
    assert "hashes" not in giro


def test_beigabe_bekommt_dateiname_und_hash_der_datei() -> None:
    laz = _komponenten(_web())["scanview-web|laz-perf@0.0.7"]
    assert _props(laz)["bsi:component:filename"] == ["laz-perf.wasm"]
    assert _props(laz)["bsi:component:executable"] == ["executable"]
    assert laz["hashes"] == [{"alg": "SHA-512", "content": _h("web/dist/wasm/laz-perf.wasm")}]
    assert "components" not in laz
    assert len(_sha512_verteilung(laz)) == 1  # npm-Hash der tgz bleibt zusätzlich stehen


def test_verbreitungslizenz_aus_spdx_originallizenz() -> None:
    giro = _komponenten(_web())["scanview-web|@giro3d/giro3d@2.0.4"]
    lizenzen = [lz["license"] for lz in giro["licenses"]]
    assert {"id": "MIT", "acknowledgement": "declared"} in lizenzen
    assert {"id": "MIT", "acknowledgement": "concluded"} in lizenzen


def test_verbreitungslizenz_nicht_bei_ausdruck_freitext_oder_mehreren() -> None:
    # CycloneDX 1.6 erlaubt genau einen Lizenzausdruck je Komponente: declared und concluded
    # nebeneinander gehen nur mit Einzel-IDs. Freitext-Namen und mehrere IDs sind keine
    # eindeutige Aussage → nichts ableiten.
    roh = _roh()
    ausdruck = [{"expression": "Apache-2.0 OR BSD-3-Clause", "acknowledgement": "declared"}]
    freitext = [{"license": {"name": "License :: OSI Approved :: BSD License",
                             "acknowledgement": "declared"}}]
    mehrere = [{"license": {"id": "MIT", "acknowledgement": "declared"}},
               {"license": {"id": "Zlib", "acknowledgement": "declared"}}]
    for i, lizenzen in enumerate((ausdruck, freitext, mehrere)):
        roh["components"][i]["licenses"] = copy.deepcopy(lizenzen)
    k = list(_web(roh)["components"])
    assert [k[i]["licenses"] for i in range(3)] == [ausdruck, freitext, mehrere]


def test_keine_schwachstellen_im_ergebnis() -> None:
    roh = _roh()
    roh["vulnerabilities"] = [{"id": "CVE-2026-0001"}]
    assert "vulnerabilities" not in _web(roh)


def test_alles_belegt_ergibt_eine_vollstaendige_komposition() -> None:
    bom = _web()
    k = _kompositionen(bom)
    assert set(k) == {"complete"}
    alle = {bom["metadata"]["component"]["bom-ref"], *_komponenten(bom)}
    assert k["complete"] == alle


def test_jede_komponente_hat_abhaengigkeitseintrag() -> None:
    bom = _web()
    refs = {d["ref"] for d in bom["dependencies"]}
    assert set(_komponenten(bom)) | {"scanview-web"} <= refs


def test_fehlender_abhaengigkeitseintrag_macht_unvollstaendig() -> None:
    roh = _roh()
    roh["dependencies"] = [d for d in roh["dependencies"]
                           if d["ref"] != "scanview-web|fflate@0.8.3"]
    bom = _web(roh)
    k = _kompositionen(bom)
    assert "scanview-web|fflate@0.8.3" in k["incomplete"]
    eintrag = [d for d in bom["dependencies"] if d["ref"] == "scanview-web|fflate@0.8.3"]
    assert eintrag == [{"ref": "scanview-web|fflate@0.8.3", "dependsOn": []}]


# --- SHA-512 nur aus belegter Quelle ----------------------------------------------------------

def test_fehlender_hash_wird_nicht_erfunden() -> None:
    roh = _roh()
    del roh["components"][4]["externalReferences"][0]["hashes"]  # fflate
    bom = _web(roh)
    fflate = _komponenten(bom)["scanview-web|fflate@0.8.3"]
    assert _sha512_verteilung(fflate) == []
    assert "hashes" not in fflate
    k = _kompositionen(bom)
    assert "scanview-web|fflate@0.8.3" in k["incomplete"]
    # Hauptkomponente fasst alles zusammen → ebenfalls unvollständig, nie in beiden Listen
    assert "scanview-web" in k["incomplete"]
    assert not (k["incomplete"] & k.get("complete", set()))
    assert any("SHA-512" in v for v in _props(bom["metadata"])["scanview:unvollstaendig"])


def test_beleg_ergaenzt_distribution_mit_hash() -> None:
    roh = _roh()
    del roh["components"][4]["externalReferences"]  # fflate ohne Verweis
    beleg = sbom.Beleg(url="https://files.example/fflate-0.8.3.tgz", sha512="c" * 128,
                       dateiname="fflate-0.8.3.tgz")
    bom = _web(roh, belege={"scanview-web|fflate@0.8.3": beleg})
    fflate = _komponenten(bom)["scanview-web|fflate@0.8.3"]
    assert _sha512_verteilung(fflate) == ["c" * 128]
    assert _verteilung(fflate)[0]["url"] == "https://files.example/fflate-0.8.3.tgz"
    assert _props(fflate)["bsi:component:filename"] == ["fflate-0.8.3.tgz"]
    assert set(_kompositionen(bom)) == {"complete"}


def test_ungueltiger_beleg_ist_fehler() -> None:
    beleg = sbom.Beleg(url="https://x/y.whl", sha512="nicht-hex", dateiname="y.whl")
    with pytest.raises(NachweisFehler) as e:
        _web(belege={"scanview-web|fflate@0.8.3": beleg})
    assert e.value.regel == "sbom-hash"


def test_beleg_fuer_unbekannte_komponente_ist_fehler() -> None:
    beleg = sbom.Beleg(url="https://x/y.whl", sha512="c" * 128, dateiname="y.whl")
    with pytest.raises(NachweisFehler) as e:
        _web(belege={"gibt-es-nicht": beleg})
    assert e.value.regel == "sbom-hash"


def test_vorgegebene_unvollstaendigkeit() -> None:
    bom = _web(unvollstaendig={"scanview-web|ol@10.10.0": "Testgrund, belegt"})
    k = _kompositionen(bom)
    assert {"scanview-web|ol@10.10.0", "scanview-web"} <= k["incomplete"]
    assert "scanview-web|three@0.180.0" in k["complete"]
    assert not (k["incomplete"] & k["complete"])
    assert any("Testgrund" in v for v in _props(bom["metadata"])["scanview:unvollstaendig"])


def test_unvollstaendig_fuer_unbekannte_komponente_ist_fehler() -> None:
    with pytest.raises(NachweisFehler) as e:
        _web(unvollstaendig={"gibt-es-nicht": "x"})
    assert e.value.regel == "sbom-abdeckung"


# --- Abdeckung --------------------------------------------------------------------------------

def test_abdeckung_web_vollstaendig() -> None:
    assert sbom.abdeckung(_roh(), _artefakt("web", WEB_DATEIEN)) == []


WEB_APP_DATEIEN = [
    "web/dist/manifest.webmanifest",
    "web/dist/icons/scanview.svg",
    "web/dist/icons/scanview-180.png",
    "web/dist/icons/scanview-192.png",
    "web/dist/icons/scanview-512.png",
]


def test_abdeckung_vite_chunk_mit_punkt_im_namen() -> None:
    chunk = "web/dist/assets/spark.module-Bah5TqRU.js"
    assert sbom.abdeckung(_roh(), _artefakt("web", [*WEB_DATEIEN, chunk])) == []


def test_abdeckung_web_app_dateien_gehoeren_zur_hauptkomponente() -> None:
    """Manifest und Symbole erzeugt der Build selbst (web/scripts/webApp.ts)."""
    assert sbom.abdeckung(_roh(), _artefakt("web", [*WEB_DATEIEN, *WEB_APP_DATEIEN])) == []


@pytest.mark.parametrize("fremd", [
    "web/dist/icons/fremd.png",
    "web/dist/icons/scanview-999.png",
    "web/dist/icons/scanview.png",
    "web/dist/icons/unter/scanview.svg",
    "web/dist/manifest.json",
    "web/dist/assets/manifest.webmanifest",
])
def test_abdeckung_web_app_bleibt_eng(fremd: str) -> None:
    assert sbom.abdeckung(_roh(), _artefakt("web", [*WEB_DATEIEN, fremd])) == [fremd]


@pytest.mark.parametrize("fremd", [
    "web/dist/fremd.bin",
    "web/dist/assets/ohne_hash.js",
    "web/dist/assets/ohne.hash.js",
    "web/dist/assets/..-Bah5TqRU.js",
    "web/dist/wasm/anderes.wasm",
    "web/dist/assets/bild-Byg8MBwP.png",
    "web/dist/unter/index.html",
])
def test_abdeckung_web_fremde_datei(fremd: str) -> None:
    art = _artefakt("web", [*WEB_DATEIEN, fremd])
    assert sbom.abdeckung(_roh(), art) == [fremd]


def test_abdeckung_beigabe_ohne_komponente() -> None:
    roh = _roh()
    roh["components"] = [k for k in roh["components"] if k["name"] != "laz-perf"]
    assert sbom.abdeckung(roh, _artefakt("web", WEB_DATEIEN)) == ["web/dist/wasm/laz-perf.wasm"]


def test_abdeckung_basis_transcoder_ist_eigenbau() -> None:
    roh = _roh()
    roh["components"] = [k for k in roh["components"] if k["name"] != "basis-universal-transcoder"]
    assert sbom.abdeckung(roh, _artefakt("web", WEB_DATEIEN)) == [
        "web/dist/wasm/basis/basis_transcoder.js", "web/dist/wasm/basis/basis_transcoder.wasm"]


def test_abdeckung_python_eigene_dateien() -> None:
    bom = {"metadata": {"component": {"bom-ref": "root-component", "name": "scanview-server"}},
           "components": []}
    assert sbom.abdeckung(bom, _artefakt("server", SERVER_DATEIEN)) == []


@pytest.mark.parametrize("fremd", [
    "server/src/scanview_server/_nativ.cpython-311-x86_64-linux-gnu.so",
    "server/vendor/paket-1.0-py3-none-any.whl",
    "server/src/scanview_server/lib.wasm",
    "anderswo/datei.py",
])
def test_abdeckung_python_fremde_datei(fremd: str) -> None:
    bom = {"metadata": {"component": {"bom-ref": "root-component", "name": "scanview-server"}},
           "components": []}
    art = _artefakt("server", [*SERVER_DATEIEN, fremd])
    assert sbom.abdeckung(bom, art) == [fremd]


def test_abdeckung_unbekanntes_artefakt_ist_ganz_offen() -> None:
    bom = {"metadata": {"component": {"bom-ref": "r", "name": "x"}}, "components": []}
    assert sbom.abdeckung(bom, _artefakt("anderes", ["a/b.py"])) == ["a/b.py"]


def test_nicht_zugeordnete_datei_wird_ausgewiesen() -> None:
    fremd = "web/dist/fremd.bin"
    bom = _web(artefakt=_artefakt("web", [*WEB_DATEIEN, fremd]))
    datei = _komponenten(bom)[f"datei:{fremd}"]
    assert datei["type"] == "file"
    assert datei["hashes"] == [{"alg": "SHA-512", "content": _h(fremd)}]
    assert _props(datei)["bsi:component:filename"] == ["fremd.bin"]
    k = _kompositionen(bom)
    assert {"scanview-web", f"datei:{fremd}"} <= k["incomplete"]
    assert not (k["incomplete"] & k["complete"])
    wurzel = next(d for d in bom["dependencies"] if d["ref"] == "scanview-web")
    assert f"datei:{fremd}" in wurzel["dependsOn"]
    assert _props(bom["metadata"])["scanview:abdeckung:nicht-zugeordnet"] == [fremd]


# --- Ausnahmen --------------------------------------------------------------------------------

def test_ausnahmen_laden_und_anwenden(tmp_path: Path) -> None:
    pfad = tmp_path / "a.toml"
    pfad.write_text('[[ausnahme]]\nmuster = "web/dist/fonts/*"\n'
                    'begruendung = "Schriftdateien gehören zur Hauptkomponente (Test)"\n',
                    encoding="utf-8")
    ausnahmen = sbom.lade_ausnahmen(pfad)
    assert [a.muster for a in ausnahmen] == ["web/dist/fonts/*"]
    offen = ["web/dist/fonts/a.woff2", "web/dist/fremd.bin"]
    assert sbom.ohne_ausnahme(offen, ausnahmen) == ["web/dist/fremd.bin"]


def test_leere_ausnahmedatei(tmp_path: Path) -> None:
    pfad = tmp_path / "a.toml"
    pfad.write_text("# leer\n", encoding="utf-8")
    assert sbom.lade_ausnahmen(pfad) == []


def test_echte_ausnahmedatei_ist_gueltig() -> None:
    sbom.lade_ausnahmen(WURZEL / "nachweise" / "abdeckung-ausnahmen.toml")


@pytest.mark.parametrize("inhalt", [
    '[[ausnahme]]\nmuster = "x/*"\nbegruendung = "zu kurz"\n',
    '[[ausnahme]]\nmuster = ""\nbegruendung = "ausreichend lange Begründung hier"\n',
    '[[ausnahme]]\nmuster = "x/*"\nbegruendung = "ausreichend lange Begründung hier"\nx = 1\n',
    '[[ausnahme]]\nbegruendung = "ausreichend lange Begründung hier"\n',
    'sonstwas = 1\n',
    'kaputt = \n',
])
def test_ungueltige_ausnahmen(tmp_path: Path, inhalt: str) -> None:
    pfad = tmp_path / "a.toml"
    pfad.write_text(inhalt, encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        sbom.lade_ausnahmen(pfad)
    assert e.value.regel == "sbom-ausnahmen"


def test_fehlende_ausnahmedatei(tmp_path: Path) -> None:
    with pytest.raises(NachweisFehler) as e:
        sbom.lade_ausnahmen(tmp_path / "fehlt.toml")
    assert e.value.regel == "sbom-ausnahmen"


# --- Zusammenführung --------------------------------------------------------------------------

def _python_bom(unvollstaendig: bool = False) -> dict[str, Any]:
    roh: dict[str, Any] = {
        "bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
        "metadata": {"component": {"bom-ref": "root-component", "name": "scanview-server",
                                   "type": "application", "version": "0.1.0"}},
        "components": [{
            "bom-ref": "fastapi==0.141.1", "name": "fastapi", "version": "0.141.1",
            "type": "library", "purl": "pkg:pypi/fastapi@0.141.1",
            "licenses": [{"license": {"id": "MIT", "acknowledgement": "declared"}}],
        }],
        "dependencies": [{"ref": "root-component", "dependsOn": ["fastapi==0.141.1"]},
                         {"ref": "fastapi==0.141.1"}],
    }
    beleg = sbom.Beleg(url="https://files.example/fastapi-0.141.1-py3-none-any.whl",
                       sha512="d" * 128, dateiname="fastapi-0.141.1-py3-none-any.whl")
    return sbom.nachbearbeiten(
        roh, _artefakt("server", SERVER_DATEIEN), ERSTELLER, "urn:scanview:sbom:server:0.1.0",
        version="0.1.0", jetzt=JETZT, belege=None if unvollstaendig else
        {"fastapi==0.141.1": beleg})


def test_python_bom_komponente_mit_beleg() -> None:
    bom = _python_bom()
    fastapi = _komponenten(bom)["fastapi==0.141.1"]
    assert _props(fastapi)["bsi:component:filename"] == ["fastapi-0.141.1-py3-none-any.whl"]
    assert _props(fastapi)["bsi:component:archive"] == ["archive"]
    assert set(_kompositionen(bom)) == {"complete"}


def test_zusammenfuehren() -> None:
    gesamt = sbom.zusammenfuehren([_web(), _python_bom()], "0.1.0", ersteller=ERSTELLER,
                                  jetzt=JETZT)
    assert gesamt["specVersion"] == "1.6"
    assert "vulnerabilities" not in gesamt
    haupt = gesamt["metadata"]["component"]
    assert (haupt["name"], haupt["version"]) == ("scanview", "0.1.0")
    refs = [k["bom-ref"] for k in sbom.alle_komponenten(gesamt["components"])]
    assert len(refs) == len(set(refs))
    wurzel = next(d for d in gesamt["dependencies"] if d["ref"] == haupt["bom-ref"])
    assert len(wurzel["dependsOn"]) == 2
    bekannte = {haupt["bom-ref"], *refs}
    for d in gesamt["dependencies"]:
        assert d["ref"] in bekannte
        assert set(d.get("dependsOn", [])) <= bekannte
    for c in gesamt["compositions"]:
        assert set(c["assemblies"]) <= bekannte
    assert set(_kompositionen(gesamt)) == {"complete"}
    assert gesamt["metadata"]["timestamp"] == "2026-09-28T10:11:12Z"
    wieder = sbom.zusammenfuehren([_web(), _python_bom()], "0.1.0", ersteller=ERSTELLER,
                                  jetzt=JETZT)
    assert wieder["serialNumber"] == gesamt["serialNumber"]


def test_zusammenfuehren_unvollstaendiger_teil_macht_ganzes_unvollstaendig() -> None:
    gesamt = sbom.zusammenfuehren([_web(), _python_bom(unvollstaendig=True)], "0.1.0",
                                  ersteller=ERSTELLER, jetzt=JETZT)
    k = _kompositionen(gesamt)
    assert gesamt["metadata"]["component"]["bom-ref"] in k["incomplete"]
    assert not (k["incomplete"] & k["complete"])


def test_zusammenfuehren_teil_ohne_kompositionen_ist_fehler() -> None:
    teil = _python_bom()
    del teil["compositions"]
    with pytest.raises(NachweisFehler) as e:
        sbom.zusammenfuehren([_web(), teil], "0.1.0", ersteller=ERSTELLER, jetzt=JETZT)
    assert e.value.regel == "sbom-zusammenfuehren"


def test_zusammenfuehren_doppelte_hauptkomponente_ist_fehler() -> None:
    with pytest.raises(NachweisFehler) as e:
        sbom.zusammenfuehren([_web(), _web()], "0.1.0", ersteller=ERSTELLER, jetzt=JETZT)
    assert e.value.regel == "sbom-zusammenfuehren"


# --- Validierung (cyclonedx-cli + sbomqs) -------------------------------------------------------

def _werkzeuge() -> dict[str, Werkzeug]:
    return {n: Werkzeug(n, f"bild/{n}@sha256:" + "0" * 64, "1", None)
            for n in ("cyclonedx-cli", "sbomqs")}


def _sbomqs_json(luecken: bool = True, revision: str = "TR-03183-2 (2.1.0)") -> str:
    abschnitte = [
        {"section_data_field": "timestamp", "required": True, "element_id": "SBOM",
         "element_result": "2026-09-28", "score": 10},
        {"section_data_field": "filename", "required": True, "element_id": "a-1",
         "element_result": "", "score": 0 if luecken else 10},
        {"section_data_field": "filename", "required": True, "element_id": "b-2",
         "element_result": "", "score": 0 if luecken else 10},
        {"section_data_field": "security.txt URL", "required": False, "element_id": "a-1",
         "element_result": "", "score": 0},
    ]
    return json.dumps({"revision": revision, "summary": {"total_score": 7.5, "max_score": 10,
                                                   "required_elements_score": 7.0},
                       "sections": abschnitte})


class _Lauf:
    def __init__(self, cli_ausgabe: str, qs_ausgabe: str) -> None:
        self.befehle: list[list[str]] = []
        self.cli_ausgabe = cli_ausgabe
        self.qs_ausgabe = qs_ausgabe

    def __call__(self, befehl: list[str], regel: str,
                 cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        self.befehle.append(befehl)
        ausgabe = self.cli_ausgabe if "validate" in befehl else self.qs_ausgabe
        return subprocess.CompletedProcess(befehl, 0, ausgabe, "")


def _bom_datei(tmp_path: Path, bom: dict[str, Any] | None = None) -> Path:
    pfad = tmp_path / "bom.json"
    pfad.write_text(json.dumps(bom if bom is not None else _web()), encoding="utf-8")
    return pfad


def test_validieren(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lauf = _Lauf("BOM validated successfully.\n", _sbomqs_json())
    monkeypatch.setattr(sbom, "ausfuehren", lauf)
    befund = sbom.validieren(_bom_datei(tmp_path), _werkzeuge())
    assert befund["cyclonedx_cli"]["gueltig"] is True
    assert befund["sbomqs"]["revision"] == "TR-03183-2 (2.1.0)"
    assert befund["sbomqs"]["pflichtluecken"] == {"filename": 2}
    assert befund["sbomqs"]["gesamt"] == 7.5
    validate = lauf.befehle[0]
    for teil in ("--input-version", "v1_6", "--fail-on-errors", "--network", "none"):
        assert teil in validate
    assert validate[validate.index("--input-version") + 1] == "v1_6"
    qs = lauf.befehle[1]
    assert "--bsi-v21" in qs and "--json" in qs and "--bsi" not in qs


def test_validieren_ohne_erfolgsmeldung_ist_schemafehler(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sbom, "ausfuehren", _Lauf("irgendwas\n", _sbomqs_json()))
    with pytest.raises(NachweisFehler) as e:
        sbom.validieren(_bom_datei(tmp_path), _werkzeuge())
    assert e.value.regel == "sbom-schema"


def test_validieren_cli_exit_ungleich_null_ist_schemafehler(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def scheitert(befehl: list[str], regel: str,
                  cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        raise NachweisFehler(regel, "docker endete mit Exit 1: BOM is not valid.")
    monkeypatch.setattr(sbom, "ausfuehren", scheitert)
    with pytest.raises(NachweisFehler) as e:
        sbom.validieren(_bom_datei(tmp_path), _werkzeuge())
    assert e.value.regel == "sbom-schema"


def _sbomqs_veraendert(**aenderung: Any) -> str:
    daten = json.loads(_sbomqs_json())
    for pfad, wert in aenderung.items():
        ziel = daten
        *teile, letzter = pfad.split("__")
        for teil in teile:
            ziel = ziel[int(teil)] if teil.isdigit() else ziel[teil]
        ziel[letzter] = wert
    return json.dumps(daten)


@pytest.mark.parametrize("ausgabe", [
    "kein json", _sbomqs_json(revision="TR-03183-2 (2.0.0)"),
    json.dumps({"revision": "TR-03183-2 (2.1.0)"}),
    _sbomqs_veraendert(sections=[]),
    _sbomqs_veraendert(summary__total_score="7.5"),
    _sbomqs_veraendert(summary__required_elements_score=None),
    _sbomqs_veraendert(sections__0__score="10"),
    _sbomqs_veraendert(sections__0__score=True),
    _sbomqs_veraendert(sections__0__required="ja"),
])
def test_validieren_sbomqs_unbrauchbar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                       ausgabe: str) -> None:
    monkeypatch.setattr(sbom, "ausfuehren", _Lauf("BOM validated successfully.", ausgabe))
    with pytest.raises(NachweisFehler) as e:
        sbom.validieren(_bom_datei(tmp_path), _werkzeuge())
    assert e.value.regel == "sbom-werkzeug"


def test_validieren_lehnt_schwachstellen_ab(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    lauf = _Lauf("BOM validated successfully.", _sbomqs_json())
    monkeypatch.setattr(sbom, "ausfuehren", lauf)
    bom = _web()
    bom["vulnerabilities"] = []
    with pytest.raises(NachweisFehler) as e:
        sbom.validieren(_bom_datei(tmp_path, bom), _werkzeuge())
    assert e.value.regel == "sbom-schwachstellen"
    assert lauf.befehle == []


def test_validieren_falsche_specversion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sbom, "ausfuehren", _Lauf("BOM validated successfully.", _sbomqs_json()))
    bom = _web()
    bom["specVersion"] = "1.7"
    with pytest.raises(NachweisFehler) as e:
        sbom.validieren(_bom_datei(tmp_path, bom), _werkzeuge())
    assert e.value.regel == "sbom-schema"


# --- npm: Hash-Abgleich mit dem Lockfile, erwartete Komponentenzahl, Ersteller ----------------

def _integritaet(hexwert: str) -> str:
    return "sha512-" + base64.b64encode(bytes.fromhex(hexwert)).decode()


def _lock_aus_fixture(roh: dict[str, Any]) -> dict[str, Any]:
    pakete: dict[str, Any] = {"": {"name": "scanview-web"}}
    for k in sbom.alle_komponenten(roh["components"]):
        pfad = _props(k)["cdx:npm:package:path"][0]
        pakete[pfad] = {"version": k["version"],
                        "integrity": _integritaet(_sha512_verteilung(k)[0])}
    pakete["node_modules/vitest"] = {"version": "1.0.0", "dev": True,
                                     "integrity": "sha512-AAAA"}
    return {"lockfileVersion": 3, "packages": pakete}


def test_npm_pruefen_stimmt() -> None:
    roh = _roh(eigenbau=False)
    assert sbom.npm_pruefen(roh, _lock_aus_fixture(roh)) == {}


def test_npm_pruefen_abweichender_hash_ist_fehler() -> None:
    roh = _roh(eigenbau=False)
    lock = _lock_aus_fixture(roh)
    lock["packages"]["node_modules/fflate"]["integrity"] = _integritaet("e" * 128)
    with pytest.raises(NachweisFehler) as e:
        sbom.npm_pruefen(roh, lock)
    assert e.value.regel == "sbom-hash"


def test_npm_pruefen_sha1_ist_unvollstaendig() -> None:
    roh = _roh(eigenbau=False)
    lock = _lock_aus_fixture(roh)
    lock["packages"]["node_modules/fflate"]["integrity"] = "sha1-AAAAAAAAAAAAAAAAAAAAAAAAAAA="
    del roh["components"][4]["externalReferences"][0]["hashes"]
    assert list(sbom.npm_pruefen(roh, lock)) == ["scanview-web|fflate@0.8.3"]


def test_npm_pruefen_hash_ohne_lockbeleg_ist_fehler() -> None:
    roh = _roh(eigenbau=False)
    lock = _lock_aus_fixture(roh)
    del lock["packages"]["node_modules/fflate"]["integrity"]
    with pytest.raises(NachweisFehler) as e:
        sbom.npm_pruefen(roh, lock)
    assert e.value.regel == "sbom-hash"


def test_npm_pruefen_komponentenzahl() -> None:
    roh = _roh(eigenbau=False)
    lock = _lock_aus_fixture(roh)
    lock["packages"]["node_modules/zusatz"] = {"version": "1.0.0", "integrity": "sha512-AA=="}
    with pytest.raises(NachweisFehler) as e:
        sbom.npm_pruefen(roh, lock)
    assert e.value.regel == "sbom-komponenten"


@pytest.mark.parametrize(("paket", "erwartet"), [
    ({"author": "Jane Doe <jane@example.org> (https://jane.example)"},
     {"contact": [{"email": "jane@example.org"}]}),
    ({"author": {"name": "Jane", "email": "jane@example.org"}},
     {"contact": [{"email": "jane@example.org"}]}),
    ({"author": {"name": "Jane", "url": "https://jane.example"}},
     {"url": ["https://jane.example"]}),
    ({"author": "Jane Doe", "homepage": "https://proj.example"},
     {"url": ["https://proj.example"]}),
    ({"repository": {"type": "git", "url": "git+https://github.com/a/b.git"}},
     {"url": ["https://github.com/a/b"]}),
    ({"repository": "github:a/b"}, {"url": ["https://github.com/a/b"]}),
    ({"author": "Jane"}, None),
    ({"homepage": "javascript:alert(1)"}, None),
])
def test_npm_ersteller(paket: dict[str, Any], erwartet: dict[str, Any] | None) -> None:
    assert sbom.npm_ersteller(paket) == erwartet


def test_npm_anreichern(tmp_path: Path) -> None:
    roh = _roh()
    paket = tmp_path / "node_modules" / "@giro3d" / "giro3d" / "package.json"
    paket.parent.mkdir(parents=True)
    paket.write_text(json.dumps({"homepage": "https://giro3d.org"}), encoding="utf-8")
    sbom.npm_anreichern(roh, tmp_path)
    k = _komponenten(roh)
    assert k["scanview-web|@giro3d/giro3d@2.0.4"]["manufacturer"] == {
        "url": ["https://giro3d.org"]}
    assert "manufacturer" not in k["scanview-web|fflate@0.8.3"]


# --- Python: Wheel-Auswahl über Tags, Hashprüfung gegen uv.lock -------------------------------

def test_wheel_tags() -> None:
    assert sbom.wheel_tags(
        "pydantic_core-2.46.5-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl") == {
        "cp311-cp311-manylinux_2_17_x86_64", "cp311-cp311-manylinux2014_x86_64"}
    assert sbom.wheel_tags("six-1.17.0-py2.py3-none-any.whl") == {"py2-none-any",
                                                                   "py3-none-any"}
    assert sbom.wheel_tags("paket-1.0-1build-py3-none-any.whl") == {"py3-none-any"}
    assert sbom.wheel_tags("paket-1.0.tar.gz") == set()


LOCK = """
version = 1
[[package]]
name = "pydantic-core"
version = "2.46.5"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.example/pydantic_core-2.46.5.tar.gz", hash = "sha256:%s" }
wheels = [
    { url = "https://f.x/WHEEL_GLIBC", hash = "sha256:%s" },
    { url = "https://f.x/WHEEL_MUSL", hash = "sha256:%s" },
]
[[package]]
name = "nur-sdist"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.example/nur_sdist-1.0.tar.gz", hash = "sha256:%s" }
"""


WHEEL_GLIBC = "pydantic_core-2.46.5-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
WHEEL_MUSL = "pydantic_core-2.46.5-cp311-cp311-musllinux_1_1_x86_64.whl"


def _lock(inhalt_wheel: bytes) -> str:
    text = LOCK.replace("WHEEL_GLIBC", WHEEL_GLIBC).replace("WHEEL_MUSL", WHEEL_MUSL)
    return text % ("1" * 64, hashlib.sha256(inhalt_wheel).hexdigest(), "2" * 64, "3" * 64)


def _installiert(name: str = "pydantic_core", version: str = "2.46.5",
                 tags: tuple[str, ...] = ("cp311-cp311-manylinux_2_17_x86_64",
                                          "cp311-cp311-manylinux2014_x86_64")
                 ) -> sbom.Installiert:
    return sbom.Installiert(name=name, version=version, tags=frozenset(tags), metadaten={})


def test_python_belege() -> None:
    inhalt = b"wheel-bytes"
    geladen: list[str] = []

    def laden(url: str) -> bytes:
        geladen.append(url)
        return inhalt
    belege, fehlend, schritte = sbom.python_belege(
        [_installiert()], _lock(inhalt), laden, cwd="server")
    assert fehlend == {}
    beleg = belege[("pydantic-core", "2.46.5")]
    assert beleg.sha512 == hashlib.sha512(inhalt).hexdigest()
    assert beleg.dateiname == WHEEL_GLIBC
    assert geladen == [beleg.url]
    assert [s.befehl for s in schritte] == [["herunterladen", beleg.url]]
    assert schritte[0].cwd == "server"


def test_python_belege_hash_weicht_ab() -> None:
    with pytest.raises(NachweisFehler) as e:
        sbom.python_belege([_installiert()], _lock(b"echt"), lambda url: b"manipuliert",
                           cwd="server")
    assert e.value.regel == "sbom-hash"


def test_python_belege_ohne_passendes_wheel() -> None:
    belege, fehlend, schritte = sbom.python_belege(
        [_installiert(tags=("cp312-cp312-linux_x86_64",)),
         _installiert(name="nur_sdist", version="1.0", tags=("cp312-cp312-linux_x86_64",)),
         _installiert(name="unbekannt", version="9")],
        _lock(b"x"), lambda url: pytest.fail("kein Download erwartet"), cwd="import")
    assert belege == {} and schritte == []
    assert set(fehlend) == {("pydantic-core", "2.46.5"), ("nur-sdist", "1.0"),
                            ("unbekannt", "9")}


@pytest.mark.parametrize(("metadaten", "erwartet"), [
    ({"Author-email": "Jane <jane@example.org>"}, {"contact": [{"email": "jane@example.org"}]}),
    ({"Maintainer-email": "m@example.org"}, {"contact": [{"email": "m@example.org"}]}),
    ({"Home-page": "https://proj.example"}, {"url": ["https://proj.example"]}),
    ({"Project-URL": ["Documentation, https://docs.example", "Source, https://src.example"]},
     {"url": ["https://src.example"]}),
    ({"Author": "Jane"}, None),
])
def test_python_ersteller(metadaten: dict[str, Any], erwartet: dict[str, Any] | None) -> None:
    assert sbom.python_ersteller(metadaten) == erwartet


EXPORT = ("# kommentar\nannotated-doc==0.0.5\n    # via fastapi\n"
          "pycparser==3.0 ; implementation_name != 'PyPy'\n    # via cffi\n"
          "pywin32==312 ; sys_platform == 'win32'\n    # via mcp\n")


def test_erwartete_python_pakete_mit_markern() -> None:
    assert sbom.python_marker(EXPORT) == ["implementation_name != 'PyPy'",
                                          "sys_platform == 'win32'"]
    auswertung = {"implementation_name != 'PyPy'": True, "sys_platform == 'win32'": False}
    assert sbom.erwartete_python_pakete(EXPORT, auswertung.__getitem__) == {
        ("annotated-doc", "0.0.5"), ("pycparser", "3.0")}


def test_erwartete_python_pakete_marker_ohne_auswertung_ist_fehler() -> None:
    with pytest.raises(NachweisFehler) as e:
        sbom.erwartete_python_pakete(EXPORT)
    assert e.value.regel == "sbom-komponenten"
    assert sbom.erwartete_python_pakete("a==1\n") == {("a", "1")}


def test_erwartete_python_pakete_ohne_exakte_version_ist_fehler() -> None:
    with pytest.raises(NachweisFehler):
        sbom.erwartete_python_pakete("paket>=1\n")


def test_marker_auswertung() -> None:
    marker = sbom.python_marker(EXPORT)
    ausgabe = json.dumps(dict.fromkeys(marker, True))
    assert sbom.marker_auswertung(ausgabe, marker) == dict.fromkeys(marker, True)
    for kaputt in ("kein json", json.dumps({marker[0]: True}),
                   json.dumps({marker[0]: True, marker[1]: "nein"})):
        with pytest.raises(NachweisFehler):
            sbom.marker_auswertung(kaputt, marker)


def test_marker_code_im_echten_interpreter(tmp_path: Path) -> None:
    # Der dünne Container-Aufruf, hier mit dem Host-Interpreter (packaging aus der Dev-Umgebung
    # oder dem Werkzeugordner): liefert je Marker einen Wahrheitswert.
    import sys
    site = WURZEL / "nachweise" / "werkzeuge" / "cyclonedx-py" / "site"
    if not (site / "packaging").is_dir():
        pytest.skip("cyclonedx-py-Werkzeug nicht installiert")
    code = sbom._MARKER_CODE.format(site="x").replace("'/werkzeug/x'", repr(str(site)))
    marker = sbom.python_marker(EXPORT)
    ausgabe = subprocess.run([sys.executable, "-c", code, json.dumps(marker)], check=True,
                             capture_output=True, text=True).stdout
    ergebnis = sbom.marker_auswertung(ausgabe, marker)
    assert ergebnis["sys_platform == 'win32'"] is (sys.platform == "win32")


# --- CLI: Abdeckungsregel fail-closed ---------------------------------------------------------

def _cli_vorbereiten(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dateien: list[str],
                     ausnahmen: str = "", npm_ausnahmen: str = "") -> tuple[Path, list[str]]:
    wurzel = tmp_path / "repo"
    (wurzel / "nachweise").mkdir(parents=True)
    (wurzel / "docs" / "compliance").mkdir(parents=True)
    (wurzel / "docs" / "compliance" / "kontrollmatrix.toml").write_text("", encoding="utf-8")
    (wurzel / "nachweise" / "werkzeuge.json").write_text("{}", encoding="utf-8")
    (wurzel / "nachweise" / "abdeckung-ausnahmen.toml").write_text(ausnahmen, encoding="utf-8")
    (wurzel / "docs" / "compliance" / "npm-signaturen-ausnahmen.toml").write_text(
        npm_ausnahmen, encoding="utf-8")
    geschrieben: list[str] = []
    art = _artefakt("web", dateien)
    monkeypatch.setattr(sbom, "lade_werkzeuge", lambda pfad: _werkzeuge())
    monkeypatch.setattr(sbom, "werkzeuge_bereitstellen", lambda n, w: [])
    monkeypatch.setattr(sbom, "baue_artefakte", lambda wt, v, z, **kw: Bauergebnis(
        [art], [Netzschritt(["npm", "ci", "--ignore-scripts"], "web",
                            "2026-09-28T00:00:00+00:00")]))
    monkeypatch.setattr(sbom, "erzeuge_roh", lambda a, wt, w, **kw: sbom.Rohergebnis(
        bom=_roh(), belege={}, unvollstaendig={}, netzschritte=[]))

    def validieren(pfad: Path, w: dict[str, Werkzeug]) -> dict[str, Any]:
        geschrieben.append(pfad.name)
        return {"cyclonedx_cli": {"gueltig": True}, "sbomqs": {"pflichtluecken": {}}}
    monkeypatch.setattr(sbom, "validieren", validieren)
    return wurzel, geschrieben


def test_cli_sbom_erzeugen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    wurzel, geschrieben = _cli_vorbereiten(monkeypatch, tmp_path, WEB_DATEIEN)
    ziel = tmp_path / "ziel"
    code = cli.main(["sbom", "erzeugen", "--wurzel", str(wurzel), "--worktree",
                     str(tmp_path), "--version", "0.1.0", "--ziel", str(ziel)])
    assert code == 0
    assert geschrieben == ["scanview-web-0.1.0.cdx.json", "scanview-0.1.0.cdx.json"]
    bom = json.loads((ziel / "sbom" / "scanview-web-0.1.0.cdx.json").read_text("utf-8"))
    assert bom["metadata"]["manufacturer"]["contact"] == [{"email": sbom.ERSTELLER}]
    befunde = json.loads((ziel / "sbom" / "werkzeugbefunde.json").read_text("utf-8"))
    assert set(befunde) == {"scanview-web-0.1.0.cdx.json", "scanview-0.1.0.cdx.json"}
    netz = json.loads((ziel / "sbom" / "netzschritte.json").read_text("utf-8"))
    assert netz == [{"befehl": ["npm", "ci", "--ignore-scripts"], "cwd": "web",
                     "zeit": "2026-09-28T00:00:00+00:00"}]


def test_cli_bricht_bei_nicht_zugeordneter_datei_ab(monkeypatch: pytest.MonkeyPatch,
                                                    tmp_path: Path,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    wurzel, geschrieben = _cli_vorbereiten(monkeypatch, tmp_path,
                                           [*WEB_DATEIEN, "web/dist/fremd.bin"])
    ziel = tmp_path / "ziel"
    code = cli.main(["sbom", "erzeugen", "--wurzel", str(wurzel), "--worktree",
                     str(tmp_path), "--version", "0.1.0", "--ziel", str(ziel)])
    assert code == 2
    fehler = capsys.readouterr().err
    assert "NACHWEIS-FEHLER sbom-abdeckung" in fehler and "web/dist/fremd.bin" in fehler
    assert geschrieben == []
    assert not (ziel / "sbom").exists()


def test_cli_ausnahme_laesst_durch_und_weist_aus(monkeypatch: pytest.MonkeyPatch,
                                                 tmp_path: Path) -> None:
    wurzel, _ = _cli_vorbereiten(
        monkeypatch, tmp_path, [*WEB_DATEIEN, "web/dist/fremd.bin"],
        '[[ausnahme]]\nmuster = "web/dist/fremd.bin"\n'
        'begruendung = "Testausnahme mit ausreichend langer Begründung"\n')
    ziel = tmp_path / "ziel"
    code = cli.main(["sbom", "erzeugen", "--wurzel", str(wurzel), "--worktree",
                     str(tmp_path), "--version", "0.1.0", "--ziel", str(ziel)])
    assert code == 0
    bom = json.loads((ziel / "sbom" / "scanview-web-0.1.0.cdx.json").read_text("utf-8"))
    assert "incomplete" in {c["aggregate"] for c in bom["compositions"]}


def test_echte_werkzeuge_fuer_alle_python_versionen() -> None:
    from scanview_nachweise.werkzeuge import lade_werkzeuge
    w = lade_werkzeuge(WURZEL / "nachweise" / "werkzeuge.json")
    for name, _site in sbom._PY_WERKZEUGE.values():
        assert w[name].werkzeugordner == "werkzeuge/cyclonedx-py"
    for name in ("cyclonedx-npm", "syft", "cyclonedx-cli", "sbomqs"):
        assert name in w


def test_container_purl() -> None:
    digest = "sha256:" + "8e" * 32
    assert sbom.container_purl(f"pdal/pdal:2.10.2@{digest}") == \
        "pkg:docker/pdal/pdal@sha256%3A" + "8e" * 32
    assert sbom.container_purl("scanview-untwine:latest") is None
    assert sbom.container_purl("x@sha256:kurz") is None


# --- Werkzeuge bereitstellen (Netzschritt) ------------------------------------------------------

def _bereitstellung(tmp_path: Path) -> tuple[Path, dict[str, Werkzeug]]:
    nachweise = tmp_path / "nachweise"
    for ordner in ("werkzeuge/cyclonedx-npm", "werkzeuge/cyclonedx-py"):
        (nachweise / ordner).mkdir(parents=True)
    (nachweise / "werkzeuge/cyclonedx-npm/package-lock.json").write_text("{}", encoding="utf-8")
    (nachweise / "werkzeuge/cyclonedx-py/requirements.txt").write_text("a==1\n",
                                                                        encoding="utf-8")
    w = {n: Werkzeug(n, f"bild/{n}@sha256:" + "0" * 64, "1", o) for n, o in (
        ("cyclonedx-npm", "werkzeuge/cyclonedx-npm"),
        ("cyclonedx-py", "werkzeuge/cyclonedx-py"),
        ("cyclonedx-py-py312", "werkzeuge/cyclonedx-py"))}
    return nachweise, w


def test_werkzeuge_bereitstellen_installiert_fehlende(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    nachweise, w = _bereitstellung(tmp_path)
    lauf = _Installation(nachweise)
    monkeypatch.setattr(sbom, "ausfuehren", lauf)
    schritte = sbom.werkzeuge_bereitstellen(nachweise, w)
    assert len(lauf.befehle) == 3
    for befehl in lauf.befehle:
        assert "--network" not in befehl  # netz=True
        assert "--read-only" in befehl and "--cap-drop" in befehl
    assert [s.befehl[:2] for s in schritte] == [["npm", "ci"], ["uv", "pip"], ["uv", "pip"]]
    assert all(not s.cwd.startswith("/") for s in schritte)
    assert "/werkzeug/site-py312" in schritte[2].befehl
    assert "--require-hashes" in schritte[1].befehl
    for ziel in ZIELE:
        marke = json.loads((nachweise / "werkzeuge" / ziel / sbom._MARKE).read_text("utf-8"))
        assert set(marke) == {"image", "lockfile", "lockfile_sha256"}


ZIELE = ("cyclonedx-npm/node_modules", "cyclonedx-py/site", "cyclonedx-py/site-py312")


class _Installation(_Lauf):
    """Simuliert eine erfolgreiche Installation: legt das Ziel im Werkzeugordner an."""

    def __init__(self, nachweise: Path) -> None:
        super().__init__("", "")
        self.nachweise = nachweise

    def __call__(self, befehl: list[str], regel: str,
                 cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        ziel = "cyclonedx-npm/node_modules" if "npm" in befehl else \
            "cyclonedx-py/" + befehl[befehl.index("--target") + 1].rsplit("/", 1)[1]
        (self.nachweise / "werkzeuge" / ziel).mkdir()
        return super().__call__(befehl, regel, cwd)


def test_werkzeuge_bereitstellen_ueberspringt_passend_markierte(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    nachweise, w = _bereitstellung(tmp_path)
    monkeypatch.setattr(sbom, "ausfuehren", _Installation(nachweise))
    sbom.werkzeuge_bereitstellen(nachweise, w)
    monkeypatch.setattr(sbom, "ausfuehren", lambda *a, **k: pytest.fail("kein Lauf erwartet"))
    assert sbom.werkzeuge_bereitstellen(nachweise, w) == []
    sbom._pruefe_installiert(w, nachweise, "npm")
    sbom._pruefe_installiert(w, nachweise, "3.12")


@pytest.mark.parametrize("fall", ["ohne-marke", "lockfile-geaendert", "anderes-image"])
def test_werkzeuge_bereitstellen_keine_stille_wiederverwendung(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fall: str) -> None:
    nachweise, w = _bereitstellung(tmp_path)
    if fall == "ohne-marke":
        for ziel in ZIELE:  # z. B. halb installiert oder von Hand befüllt
            (nachweise / "werkzeuge" / ziel).mkdir()
    else:
        monkeypatch.setattr(sbom, "ausfuehren", _Installation(nachweise))
        sbom.werkzeuge_bereitstellen(nachweise, w)
        if fall == "lockfile-geaendert":
            (nachweise / "werkzeuge/cyclonedx-py/requirements.txt").write_text(
                "a==2\n", encoding="utf-8")
        else:
            w["cyclonedx-npm"] = Werkzeug("cyclonedx-npm", "bild/neu@sha256:" + "1" * 64, "1",
                                          "werkzeuge/cyclonedx-npm")
    monkeypatch.setattr(sbom, "ausfuehren", lambda *a, **k: pytest.fail("kein Lauf erwartet"))
    with pytest.raises(NachweisFehler) as e:
        sbom.werkzeuge_bereitstellen(nachweise, w)
    assert e.value.regel == "sbom-werkzeug"
    with pytest.raises(NachweisFehler):
        sbom._pruefe_installiert(w, nachweise, "npm" if fall != "lockfile-geaendert" else "3.11")


def test_pruefe_installiert_fehlt(tmp_path: Path) -> None:
    nachweise, w = _bereitstellung(tmp_path)
    with pytest.raises(NachweisFehler) as e:
        sbom._pruefe_installiert(w, nachweise, "npm")
    assert e.value.regel == "sbom-werkzeug"


def test_installation_ohne_ergebnis_ist_fehler(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    nachweise, w = _bereitstellung(tmp_path)
    monkeypatch.setattr(sbom, "ausfuehren", _Lauf("", ""))  # legt nichts an
    with pytest.raises(NachweisFehler) as e:
        sbom.werkzeuge_bereitstellen(nachweise, w)
    assert e.value.regel == "sbom-werkzeug"


@pytest.mark.parametrize("kaputt", ["fehlt", "ohne-ordner"])
def test_werkzeuge_bereitstellen_ungueltiger_eintrag(tmp_path: Path, kaputt: str) -> None:
    nachweise, w = _bereitstellung(tmp_path)
    if kaputt == "fehlt":
        del w["cyclonedx-py-py312"]
    else:
        w["cyclonedx-npm"] = Werkzeug("cyclonedx-npm", w["cyclonedx-npm"].image, "1", None)
    with pytest.raises(NachweisFehler) as e:
        sbom.werkzeuge_bereitstellen(nachweise, w)
    assert e.value.regel == "werkzeuge"


# --- Strukturiertes Ergebnis und Bindung SBOM ↔ Artefakt ----------------------------------------

def test_erzeuge_alle_liefert_pfade_und_bau(monkeypatch: pytest.MonkeyPatch,
                                            tmp_path: Path) -> None:
    wurzel, _ = _cli_vorbereiten(monkeypatch, tmp_path, WEB_DATEIEN)
    ziel = tmp_path / "ziel"
    e = sbom.erzeuge_alle(wurzel, tmp_path, "0.1.0", ziel)
    assert e.sboms == {"web": ziel / "sbom" / "scanview-web-0.1.0.cdx.json"}
    assert e.gesamt == ziel / "sbom" / "scanview-0.1.0.cdx.json"
    assert e.werkzeugbefunde == ziel / "sbom" / "werkzeugbefunde.json"
    assert [a.name for a in e.bau.artefakte] == ["web"]
    assert [s.befehl for s in e.netzschritte] == [["npm", "ci", "--ignore-scripts"]]
    assert e.zeilen and e.zeilen[0].startswith("scanview-web-0.1.0.cdx.json")


def test_erzeuge_alle_laedt_und_reicht_npm_ausnahmen_durch(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`baue_artefakte` bekommt die geladenen npm-Ausnahmen aus
    `docs/compliance/npm-signaturen-ausnahmen.toml` — nicht die SBOM-Abdeckungsausnahmen."""
    wurzel, _ = _cli_vorbereiten(monkeypatch, tmp_path, WEB_DATEIEN, npm_ausnahmen=(
        '[ausnahme]\n"x@1.0.0" = "Testausnahme, lang genug für die Prüfung begründet"\n'))
    aufrufe: list[dict[str, str]] = []
    art = _artefakt("web", WEB_DATEIEN)

    def falsch(wt: Path, v: str, z: Path, **kw: object) -> Bauergebnis:
        aufrufe.append(kw.get("npm_ausnahmen"))  # type: ignore[arg-type]
        return Bauergebnis([art], [])
    monkeypatch.setattr(sbom, "baue_artefakte", falsch)
    sbom.erzeuge_alle(wurzel, tmp_path, "0.1.0", tmp_path / "ziel")
    assert aufrufe == [{"x@1.0.0": "Testausnahme, lang genug für die Prüfung begründet"}]


def test_pruefe_bindung(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    wurzel, _ = _cli_vorbereiten(monkeypatch, tmp_path, WEB_DATEIEN)
    e = sbom.erzeuge_alle(wurzel, tmp_path, "0.1.0", tmp_path / "ziel")
    art = e.bau.artefakte[0]
    bom = json.loads(e.sboms["web"].read_text("utf-8"))
    sbom.pruefe_bindung(bom, art, "0.1.0")

    with pytest.raises(NachweisFehler) as fehler:
        sbom.pruefe_bindung(bom, art, "0.1.1")
    assert fehler.value.regel == "sbom-bindung"

    anders = dataclasses.replace(art, sha256="0" * 64)
    with pytest.raises(NachweisFehler, match="serialNumber"):
        sbom.pruefe_bindung(bom, anders, "0.1.0")

    kaputt = json.loads(json.dumps(bom))
    for h in kaputt["metadata"]["component"]["hashes"]:
        if h["alg"] == "SHA-512":
            h["content"] = "1" * 128
    with pytest.raises(NachweisFehler, match="SHA-512"):
        sbom.pruefe_bindung(kaputt, art, "0.1.0")

    ohne = json.loads(json.dumps(bom))
    del ohne["metadata"]["component"]
    with pytest.raises(NachweisFehler) as fehler:
        sbom.pruefe_bindung(ohne, art, "0.1.0")
    assert fehler.value.regel == "sbom-bindung"


# --- Nur-Quellpakete (nur-sdist.toml) -----------------------------------------------------------

_SDIST_SHA = "36b18b61a95ba992de804b1e91e86be2ba185925e4e2c1481495f8c68a17f5db"
_SDIST_URL = "https://files.example/meshoptimizer-0.2.30a0.tar.gz"
_LOCK_SDIST = f"""
[[package]]
name = "meshoptimizer"
version = "0.2.30a0"
source = {{ registry = "https://pypi.org/simple" }}
dependencies = [{{ name = "numpy" }}]
sdist = {{ url = "{_SDIST_URL}", hash = "sha256:{_SDIST_SHA}" }}

[[package]]
name = "numpy"
version = "2.5.3"
source = {{ registry = "https://pypi.org/simple" }}

[[package]]
name = "scanview-import"
version = "0.7.0"
source = {{ editable = "." }}
dependencies = [{{ name = "meshoptimizer" }}, {{ name = "numpy" }}]
"""


def _nur_sdist(**kw: str) -> sbom.NurSdist:
    werte = {"artefakt": "import", "name": "meshoptimizer", "version": "0.2.30a0",
             "sha256": _SDIST_SHA, "lizenz": "MIT",
             "lizenzbeleg": "web/licenses/meshoptimizer-MIT.txt",
             "grund": "nur sdist, im gehärteten Container nicht baubar", "adr": "ADR-x"}
    return sbom.NurSdist(**{**werte, **kw})


def _toml_eintrag(ns: sbom.NurSdist) -> str:
    return "[[paket]]\n" + "".join(f'{k} = "{getattr(ns, k)}"\n' for k in ns.__dataclass_fields__)


def test_nur_sdist_laden(tmp_path: Path) -> None:
    assert sbom.lade_nur_sdist(tmp_path / "fehlt.toml") == []
    pfad = tmp_path / "nur-sdist.toml"
    pfad.write_text(_toml_eintrag(_nur_sdist()), encoding="utf-8")
    assert sbom.lade_nur_sdist(pfad) == [_nur_sdist()]


@pytest.mark.parametrize(("feld", "wert", "meldung"), [
    ("sha256", "abc", "Hexwert"), ("lizenz", "MIT OR Apache-2.0", "SPDX-ID"),
    ("grund", "zu kurz", "grund zu kurz"), ("adr", " ", "nicht leere"),
])
def test_nur_sdist_laden_ungueltig(tmp_path: Path, feld: str, wert: str, meldung: str) -> None:
    pfad = tmp_path / "nur-sdist.toml"
    pfad.write_text(_toml_eintrag(_nur_sdist(**{feld: wert})), encoding="utf-8")
    with pytest.raises(NachweisFehler, match=meldung):
        sbom.lade_nur_sdist(pfad)


def test_nur_sdist_laden_unbekanntes_feld(tmp_path: Path) -> None:
    pfad = tmp_path / "nur-sdist.toml"
    pfad.write_text(_toml_eintrag(_nur_sdist()) + 'extra = "x"\n', encoding="utf-8")
    with pytest.raises(NachweisFehler, match="braucht genau"):
        sbom.lade_nur_sdist(pfad)


def test_nur_sdist_pruefen_passt() -> None:
    sbom.nur_sdist_pruefen([_nur_sdist()], _LOCK_SDIST)


@pytest.mark.parametrize(("ns", "lock", "meldung"), [
    (_nur_sdist(version="0.2.31"), _LOCK_SDIST, "nicht so in uv.lock"),
    (_nur_sdist(name="gibtsnicht"), _LOCK_SDIST, "nicht so in uv.lock"),
    (_nur_sdist(sha256="0" * 64), _LOCK_SDIST, "sdist-Hash"),
    (_nur_sdist(), _LOCK_SDIST.replace(
        'dependencies = [{ name = "numpy" }]\n',
        'dependencies = [{ name = "numpy" }]\n'
        'wheels = [{ url = "https://x/m.whl", hash = "sha256:00" }]\n', 1),
     "kennt Wheels"),
])
def test_nur_sdist_pruefen_bricht_ab(ns: sbom.NurSdist, lock: str, meldung: str) -> None:
    with pytest.raises(NachweisFehler, match=meldung):
        sbom.nur_sdist_pruefen([ns], lock)


def test_nur_sdist_einfuegen() -> None:
    bom: dict[str, Any] = {
        "metadata": {"component": {"bom-ref": "root-component", "name": "scanview-import"}},
        "components": [{"bom-ref": "numpy==2.5.3", "name": "numpy", "version": "2.5.3"}],
        "dependencies": [{"ref": "root-component", "dependsOn": ["numpy==2.5.3"]},
                         {"ref": "numpy==2.5.3"}],
    }
    luecken = sbom.nur_sdist_einfuegen(bom, [_nur_sdist()], _LOCK_SDIST)
    komp = bom["components"][-1]
    assert komp["bom-ref"] == "meshoptimizer==0.2.30a0"
    assert komp["purl"] == "pkg:pypi/meshoptimizer@0.2.30a0"
    assert komp["licenses"] == [{"license": {"id": "MIT", "acknowledgement": "declared"}}]
    assert komp["externalReferences"][0]["url"] == _SDIST_URL
    assert komp["externalReferences"][0]["hashes"] == [{"alg": "SHA-256", "content": _SDIST_SHA}]
    deps = {d["ref"]: d.get("dependsOn", []) for d in bom["dependencies"]}
    assert deps["meshoptimizer==0.2.30a0"] == ["numpy==2.5.3"]
    assert deps["root-component"] == ["numpy==2.5.3", "meshoptimizer==0.2.30a0"]
    assert list(luecken) == ["meshoptimizer==0.2.30a0"]
    assert "ADR ADR-x" in luecken["meshoptimizer==0.2.30a0"]


def test_mehrere_beigaben_haben_eigene_dateikomponenten() -> None:
    roh = _roh()
    # Die Fixture-Version bleibt maßgeblich, auch bei einem späteren Paketupdate.
    ref = next(k["bom-ref"] for k in roh["components"] if k["name"] == "basis-universal-transcoder")
    original = copy.deepcopy(next(k for k in roh["components"] if k["bom-ref"] == ref))
    bom = _web(roh)
    three = _komponenten(bom)[ref]
    # Unter dem Eigenbau: die ausgelieferten Dateien und die enthaltene Bibliothek zstd.
    dateien = [k for k in three["components"] if k["type"] == "file"]
    assert [k["name"] for k in three["components"] if k["type"] == "library"] == ["zstd"]
    erwartet = {p: _h(p) for p in WEB_DATEIEN if "/basis/" in p}
    assert {_props(k)["scanview:pfad"][0]: k["hashes"][0]["content"]
            for k in dateien} == erwartet
    assert three.get("hashes", []) == original.get("hashes", [])
    assert _sha512_verteilung(three) == _sha512_verteilung(original)
    refs = {k["bom-ref"] for k in dateien}
    dependencies = {d["ref"]: set(d["dependsOn"]) for d in bom["dependencies"]}
    assert refs <= dependencies[ref]
    assert all(dependencies[r] == set() for r in refs)
    assert refs <= _kompositionen(bom)["complete"]
    assert sbom.abdeckung(bom, _artefakt("web", WEB_DATEIEN)) == []
    gesamt = sbom.zusammenfuehren([bom], "0.1.0", ersteller=ERSTELLER, jetzt=JETZT)
    merged = next(k for k in sbom.alle_komponenten(gesamt["components"])
                  if k.get("name") == "basis-universal-transcoder")
    assert {_props(k)["scanview:pfad"][0]: k["hashes"][0]["content"]
            for k in merged["components"] if k["type"] == "file"} == erwartet
    assert roh == _roh()


def test_eigenbau_laden_und_pruefen(tmp_path: Path) -> None:
    assert sbom.lade_eigenbau(tmp_path / "fehlt.toml") == []
    echt = (WURZEL / "nachweise" / "eigenbau.toml").read_text(encoding="utf-8")
    eintraege = sbom.lade_eigenbau(WURZEL / "nachweise" / "eigenbau.toml")
    assert [e.name for e in eintraege] == ["basis-universal-transcoder"]
    for kaputt, meldung in [
        (echt.replace('commit = "051ad6d8', 'commit = "v1_50_0" # '), "commit"),
        (echt.replace("emsdk@sha256:", "emsdk:"), "Digest"),
        (echt.replace('sha256 = "6f2a', 'sha256 = "xyz'), "sha256"),
        (echt.replace('grund = "', 'grund = "kurz" # '), "grund"),
        (echt + '\n[sonstiges]\nx = "y"\n', "unbekannte"),
    ]:
        pfad = tmp_path / "eigenbau.toml"
        pfad.write_text(kaputt, encoding="utf-8")
        with pytest.raises(NachweisFehler, match=meldung):
            sbom.lade_eigenbau(pfad)


def test_eigenbau_abweichende_datei_ist_fehler(tmp_path: Path) -> None:
    (e,) = sbom.lade_eigenbau(WURZEL / "nachweise" / "eigenbau.toml")
    for pfad, _ in e.dateien:
        ziel = tmp_path / pfad
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_bytes((WURZEL / pfad).read_bytes())
    (tmp_path / e.lizenzbeleg).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / e.lizenzbeleg).write_text("Lizenz", encoding="utf-8")
    bom: dict[str, Any] = {"metadata": {"component": {"bom-ref": "root"}}, "components": []}
    sbom.eigenbau_einfuegen(bom, [e], tmp_path)
    k = bom["components"][0]
    assert k["purl"] == f"pkg:github/binomialllc/basis_universal@{e.commit}"
    assert k["licenses"] == [{"expression": "Apache-2.0 AND BSD-3-Clause",
                              "acknowledgement": "declared"}]
    assert {"ref": "root", "dependsOn": [k["bom-ref"]]} in bom["dependencies"]
    # Release-Stufe lizenzen: Positivliste und Nennung in THIRD_PARTY.md mit Version
    regeln = lizenzpruefung.lade_regeln(WURZEL / "nachweise" / "lizenzen.toml")
    genannt = lizenzpruefung.genannte_komponenten(
        (WURZEL / "docs" / "THIRD_PARTY.md").read_text(encoding="utf-8"))
    assert lizenzpruefung.probleme(bom, regeln, genannt) == []
    (tmp_path / e.dateien[0][0]).write_bytes(b"veraendert")
    with pytest.raises(NachweisFehler, match="weicht vom erklärten SHA-256 ab"):
        sbom.eigenbau_einfuegen({"metadata": {"component": {"bom-ref": "root"}}}, [e], tmp_path)


MEDIAPIPE_DATEIEN = [
    'web/dist/wasm/mediapipe/vision_wasm_internal.js',
    'web/dist/wasm/mediapipe/vision_wasm_internal.wasm',
    'web/dist/wasm/mediapipe/vision_wasm_nosimd_internal.js',
    'web/dist/wasm/mediapipe/vision_wasm_nosimd_internal.wasm',
    'web/dist/wasm/mediapipe/face_landmarker.task',
]


def _mediapipe_roh() -> dict[str, Any]:
    roh = _roh()
    roh['components'].append({
        'type': 'library', 'group': '@mediapipe', 'name': 'tasks-vision', 'version': '1.0.1',
        'bom-ref': 'pkg:npm/%40mediapipe/tasks-vision@1.0.1',
        'purl': 'pkg:npm/%40mediapipe/tasks-vision@1.0.1',
        'licenses': [{'license': {'id': 'Apache-2.0'}}],
    })
    sbom.mediapipe_modell_einfuegen(roh, WURZEL)
    return roh


def test_mediapipe_abdeckung_und_paketdateien() -> None:
    art = _artefakt('web', [*WEB_DATEIEN, *MEDIAPIPE_DATEIEN])
    roh = _mediapipe_roh()
    assert sbom.abdeckung(roh, art) == []
    bom = _web(roh, artefakt=art)
    paket = next(k for k in bom['components'] if k['name'] == 'tasks-vision')
    assert {_props(k)['scanview:pfad'][0] for k in paket['components']} == set(
        MEDIAPIPE_DATEIEN[:-1])
    for name in ('tasks-vision', 'MediaPipe Face Landmarker'):
        ohne = copy.deepcopy(roh)
        ohne['components'] = [k for k in ohne['components'] if k['name'] != name]
        assert sbom.abdeckung(ohne, art) == (
            MEDIAPIPE_DATEIEN[:-1] if name == 'tasks-vision' else MEDIAPIPE_DATEIEN[-1:])


@pytest.mark.parametrize('fremd', [
    'web/dist/wasm/mediapipe/zusatz.js',
    'web/dist/wasm/mediapipe/vision_wasm_module_internal.wasm',
    'web/dist/wasm/mediapipe/anderes.task',
    'web/dist/wasm/mediapipe/unter/vision_wasm_internal.js',
])
def test_mediapipe_unbekannte_datei_bleibt_offen(fremd: str) -> None:
    art = _artefakt('web', [*MEDIAPIPE_DATEIEN, fremd])
    roh = _mediapipe_roh()
    assert sbom.abdeckung(roh, art) == [fremd]
    with pytest.raises(NachweisFehler, match='keiner Komponente zugeordnet'):
        sbom.pruefe_abdeckung(roh, art, [])


def test_mediapipe_modell_belegt_und_vollstaendig() -> None:
    roh = _mediapipe_roh()
    bom = _web(roh, artefakt=_artefakt('web', MEDIAPIPE_DATEIEN))
    modell = next(k for k in bom['components'] if k['type'] == 'machine-learning-model')
    inhalt = (WURZEL / 'web/vendor/mediapipe/face_landmarker.task').read_bytes()
    sha256 = hashlib.sha256(inhalt).hexdigest()
    assert sha256 in (WURZEL / 'web/vendor/mediapipe/README.md').read_text()
    assert {'alg': 'SHA-256', 'content': sha256} in modell['hashes']
    assert _sha512_verteilung(modell) == [hashlib.sha512(inhalt).hexdigest()]
    assert _verteilung(modell)[0]['url'] == (
        'https://storage.googleapis.com/mediapipe-models/face_landmarker/'
        'face_landmarker/float16/1/face_landmarker.task')
    assert modell['supplier']['name'] == modell['manufacturer']['name'] == 'Google LLC'
    assert modell['manufacturer']['url']
    assert modell['version'] == '1'
    assert _props(modell)['scanview:modell-variante'] == ['float16/1']
    assert modell['licenses'] == [
        {'license': {'id': 'Apache-2.0', 'acknowledgement': 'declared'}},
        {'license': {'id': 'Apache-2.0', 'acknowledgement': 'concluded'}},
    ]
    assert {k: v for k, v in _props(modell).items() if k.startswith('bsi:')} == {
        'bsi:component:filename': ['face_landmarker.task'],
        'bsi:component:archive': ['archive'],
        'bsi:component:executable': ['non-executable'],
        'bsi:component:structured': ['structured'],
    }
    ref = modell['bom-ref']
    assert ref in _kompositionen(bom)['complete']
    deps = {d['ref']: d['dependsOn'] for d in bom['dependencies']}
    assert deps[ref] == []
    assert ref in deps[bom['metadata']['component']['bom-ref']]
    regeln = lizenzpruefung.lade_regeln(WURZEL / 'nachweise/lizenzen.toml')
    genannt = lizenzpruefung.genannte_komponenten((WURZEL / 'docs/THIRD_PARTY.md').read_text())
    paket = next(k for k in roh['components'] if k['name'] == 'tasks-vision')
    assert lizenzpruefung.probleme({'components': [modell, paket]}, regeln, genannt) == []
    assert sbom.klassifiziere('anderes.task') is None


@pytest.mark.parametrize('inhalt', [None, b'veraendertes Modell'])
def test_mediapipe_modell_fehlt_oder_hash_weicht_ab(tmp_path: Path, inhalt: bytes | None) -> None:
    if inhalt is not None:
        pfad = tmp_path / 'web/vendor/mediapipe/face_landmarker.task'
        pfad.parent.mkdir(parents=True)
        pfad.write_bytes(inhalt)
    with pytest.raises(NachweisFehler) as fehler:
        sbom.mediapipe_modell_einfuegen({'metadata': {'component': {'bom-ref': 'web'}}}, tmp_path)
    assert fehler.value.regel == 'sbom-modell'


def test_roh_web_fuegt_mediapipe_modell_ein(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    werkzeug = Werkzeug('npm', 'image@sha256:' + 'a' * 64, 'npm', None)
    monkeypatch.setattr(sbom, '_werkzeug', lambda *a: (werkzeug, tmp_path))
    monkeypatch.setattr(sbom, '_pruefe_installiert', lambda *a: None)
    monkeypatch.setattr(sbom, 'npm_pruefen', lambda *a: {})
    monkeypatch.setattr(sbom, 'npm_anreichern', lambda *a: None)

    def generator(*a: Any, **kw: Any) -> None:
        (tmp_path / 'aus/bom.json').write_text(json.dumps(_roh(eigenbau=False)))

    monkeypatch.setattr(sbom, 'ausfuehren', generator)
    roh = sbom._roh_web(WURZEL, {}, WURZEL / 'nachweise', tmp_path)
    modell = next(k for k in roh.bom['components'] if k['type'] == 'machine-learning-model')
    assert sbom.abdeckung(roh.bom, _artefakt('web', MEDIAPIPE_DATEIEN[-1:])) == []
    assert modell['manufacturer']['name'] == 'Google LLC'


# --- Nachweis-SBOM der Werkzeug-Images (nicht ausgeliefert) -------------------------------------

def _falsche_bilder(bom: dict[str, Any], worktree: Path, werkzeuge: object,
                    arbeit: Path) -> dict[str, str]:
    """Wie `_roh_images`, ohne Docker: zwei Container mit je einem Paket, alles lückenhaft."""
    luecken: dict[str, str] = {}
    for kennung, bild_id in (("pdal", "sha256:" + "a" * 64), ("untwine", "sha256:" + "b" * 64)):
        cref = f"image:{kennung}"
        bom["components"] += [
            {"type": "container", "bom-ref": cref, "name": kennung, "version": "1",
             "properties": [{"name": "scanview:image-id", "value": bild_id}]},
            {"type": "library", "bom-ref": f"{kennung}|p", "name": "gdal", "version": "3"}]
        bom["dependencies"].append({"ref": cref, "dependsOn": [f"{kennung}|p"]})
        luecken[cref] = luecken[f"{kennung}|p"] = f"Image {kennung}"
    for d in bom["dependencies"]:
        if d["ref"] == sbom._haupt(bom)["bom-ref"]:
            d["dependsOn"] += ["image:pdal", "image:untwine"]
    return luecken


def test_bilder_sbom_eigene_hauptkomponente_ohne_artefakt(monkeypatch: pytest.MonkeyPatch,
                                                         tmp_path: Path) -> None:
    monkeypatch.setattr(sbom, "_roh_images", _falsche_bilder)
    jetzt = datetime(2026, 10, 22, tzinfo=UTC)
    b = sbom.erzeuge_bilder(tmp_path, {}, version="0.10.3", ersteller=sbom.ERSTELLER,
                            arbeitsordner=tmp_path / "a", jetzt=jetzt)
    haupt = b["metadata"]["component"]
    assert haupt["name"] == "scanview-importbilder" and haupt["version"] == "0.10.3"
    assert "hashes" not in haupt  # kein Artefakt, also keine erfundenen Hashes oder Dateinamen
    assert {"name": "scanview:ausgeliefert", "value": "nein"} in b["metadata"]["properties"]
    # Alles lückenhaft und so ausgewiesen.
    assert [c["aggregate"] for c in b["compositions"]] == ["incomplete"]
    refs = {d["ref"] for d in b["dependencies"]}
    assert {"image:pdal", "pdal|p", "image:untwine", "untwine|p", haupt["bom-ref"]} <= refs
    # serialNumber deterministisch aus Version und Image-IDs, anders als die eines Artefakts.
    nochmal = sbom.erzeuge_bilder(tmp_path, {}, version="0.10.3", ersteller=sbom.ERSTELLER,
                                  arbeitsordner=tmp_path / "b", jetzt=jetzt)
    assert b["serialNumber"] == nochmal["serialNumber"]
    andere = sbom.erzeuge_bilder(tmp_path, {}, version="0.10.4", ersteller=sbom.ERSTELLER,
                                 arbeitsordner=tmp_path / "c", jetzt=jetzt)
    assert b["serialNumber"] != andere["serialNumber"]


def test_erzeuge_alle_import_ohne_images_bilder_getrennt(monkeypatch: pytest.MonkeyPatch,
                                                        tmp_path: Path) -> None:
    """B-SBOM-IMAGES: das ausgelieferte Import-SBOM enthält keinen Image-Inhalt; der steht im
    eigenen Nachweis-SBOM `importbilder`, das nicht ins Gesamt-SBOM (Produkt) eingeht."""
    wurzel, geschrieben = _cli_vorbereiten(monkeypatch, tmp_path, WEB_DATEIEN)
    imp = _artefakt("web", WEB_DATEIEN)
    imp = Artefakt("import", imp.pfad.with_name("scanview-import-0.1.0.tar.gz"), imp.sha256[::-1],
                   imp.sha512, [])
    monkeypatch.setattr(sbom, "baue_artefakte", lambda wt, v, z, **kw: Bauergebnis([imp], []))
    monkeypatch.setattr(sbom, "pruefe_abdeckung", lambda bom, a, aus: None)
    monkeypatch.setattr(sbom, "_roh_images", _falsche_bilder)
    ziel = tmp_path / "ziel"
    e = sbom.erzeuge_alle(wurzel, tmp_path, "0.1.0", ziel)
    assert set(e.sboms) == {"import", "importbilder"}
    assert geschrieben == ["scanview-import-0.1.0.cdx.json", "scanview-importbilder-0.1.0.cdx.json",
                           "scanview-0.1.0.cdx.json"]
    imp_bom = json.loads(e.sboms["import"].read_text("utf-8"))
    alle = [k["bom-ref"] for k in sbom.alle_komponenten(imp_bom["components"])]
    assert not any(r.startswith(("image:", "pdal|", "untwine|")) for r in alle)
    bilder = json.loads(e.sboms["importbilder"].read_text("utf-8"))
    assert {k["bom-ref"] for k in bilder["components"]} >= {"image:pdal", "image:untwine"}
    gesamt = json.loads(e.gesamt.read_text("utf-8"))
    assert "scanview-importbilder" not in json.dumps(gesamt)
