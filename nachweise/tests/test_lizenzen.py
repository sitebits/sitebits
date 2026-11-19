"""Lizenzabgleich der Release-Kette: jede Lizenz der ausgelieferten
SBOMs steht auf der Positivliste (`nachweise/lizenzen.toml`), und jede Komponente ist mit
Name **und** Version in docs/THIRD_PARTY.md genannt."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scanview_nachweise import lizenzen
from scanview_nachweise.fehler import NachweisFehler

WURZEL = Path(__file__).resolve().parents[2]

REGELN = lizenzen.Lizenzregeln(
    erlaubt=frozenset({"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "Zlib",
                       "GPL-3.0-or-later WITH GCC-exception-3.1"}),
    klassifikatoren={"License :: OSI Approved :: BSD License": "BSD-2-Clause OR BSD-3-Clause",
                     "License :: OSI Approved :: GNU General Public License v3 (GPLv3)":
                         "GPL-3.0-only"},
)

THIRD_PARTY = """# Drittkomponenten

| Komponente | Version | Lizenz | Einbindung |
|---|---|---|---|
| @giro3d/giro3d | 2.0.4 | MIT | npm |
| ol (OpenLayers) | 10.10.0 | BSD-2-Clause | npm |
| pydantic / pydantic-core | 2.13.5 / 2.46.5 | MIT | transitiv |
| PyJWT (Extra `crypto`) | 2.14.0 | MIT | transitiv |
| pako | 2.2.0 | MIT AND Zlib | transitiv |
| typing_extensions | 4.16.0 | PSF-2.0 | transitiv |

Fließtext erwähnt earcut 2.2.4 nur nebenbei — das zählt nicht als Nennung.
"""


def _k(name: str, version: str, purl: str | None, *lizenz: dict[str, Any],
       innen: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    k: dict[str, Any] = {"type": "library", "name": name, "version": version,
                         "bom-ref": purl or name, "licenses": list(lizenz)}
    if purl:
        k["purl"] = purl
    if innen:
        k["components"] = innen
    return k


def _id(spdx: str, ack: str = "declared") -> dict[str, Any]:
    return {"license": {"id": spdx, "acknowledgement": ack}}


def _bom(*komponenten: dict[str, Any]) -> dict[str, Any]:
    return {"metadata": {"component": {"name": "scanview-web", "licenses": [
        _id("AGPL-3.0-only")]}}, "components": list(komponenten)}


def _gut() -> dict[str, Any]:
    return _bom(
        _k("giro3d", "2.0.4", "pkg:npm/%40giro3d/giro3d@2.0.4", _id("MIT"),
           _id("MIT", "concluded")),
        _k("ol", "10.10.0", "pkg:npm/ol@10.10.0", _id("BSD-2-Clause")),
        _k("pako", "2.2.0", "pkg:npm/pako@2.2.0",
           {"expression": "(MIT AND Zlib)", "acknowledgement": "declared"}),
    )


def test_erlaubte_lizenzen_und_genannte_komponenten():
    bericht = lizenzen.pruefe({"web": _gut()}, REGELN, THIRD_PARTY)
    assert bericht["ergebnis"] == "bestanden"
    assert bericht["artefakte"]["web"]["komponenten"] == 3
    assert bericht["artefakte"]["web"]["lizenzen"] == {"(MIT AND Zlib)": 1, "BSD-2-Clause": 1,
                                                        "MIT": 1}


@pytest.mark.parametrize("ausdruck,erlaubt", [
    ("MIT", True), ("MIT AND Zlib", True), ("(MIT AND Zlib)", True),
    ("Apache-2.0 OR BSD-3-Clause", True), ("GPL-3.0-only OR MIT", True),
    ("GPL-3.0-only AND MIT", False), ("GPL-3.0-only", False),
    ("MIT AND (Zlib OR GPL-2.0-only)", True), ("(MIT OR GPL-2.0-only) AND AGPL-3.0-only", False),
    ("GPL-3.0-or-later WITH GCC-exception-3.1", True), ("GPL-3.0-only WITH GCC-exception-3.1",
                                                        False),
    ("mit", False), ("LicenseRef-proprietaer", False),
])
def test_ausdruecke(ausdruck, erlaubt):
    assert lizenzen.ausdruck_erlaubt(ausdruck, REGELN.erlaubt) is erlaubt


@pytest.mark.parametrize("kaputt", ["", "MIT AND", "(MIT", "MIT OR OR BSD-3-Clause",
                                    "MIT Zlib", "WITH MIT", "MIT)"])
def test_unlesbarer_ausdruck(kaputt):
    with pytest.raises(NachweisFehler) as e:
        lizenzen.ausdruck_erlaubt(kaputt, REGELN.erlaubt)
    assert e.value.regel == "lizenzen"


def test_nicht_erlaubte_lizenz():
    bom = _gut()
    bom["components"][1]["licenses"] = [_id("GPL-3.0-only")]
    with pytest.raises(NachweisFehler) as e:
        lizenzen.pruefe({"web": bom}, REGELN, THIRD_PARTY)
    assert e.value.regel == "lizenzen"
    assert "web: pkg:npm/ol@10.10.0: Lizenz GPL-3.0-only nicht erlaubt" in e.value.text


def test_ohne_lizenzangabe_fail_closed():
    bom = _gut()
    bom["components"][0]["licenses"] = [_id("MIT", "concluded")]  # nur abgeleitet, nichts erklärt
    with pytest.raises(NachweisFehler, match="keine erklärte Lizenz"):
        lizenzen.pruefe({"web": bom}, REGELN, THIRD_PARTY)
    del bom["components"][0]["licenses"]
    with pytest.raises(NachweisFehler, match="keine erklärte Lizenz"):
        lizenzen.pruefe({"web": bom}, REGELN, THIRD_PARTY)


def test_klassifikator_nur_ueber_zuordnung():
    bom = _gut()
    bom["components"][1]["licenses"] = [
        {"license": {"name": "License :: OSI Approved :: BSD License",
                     "acknowledgement": "declared"}}]
    lizenzen.pruefe({"web": bom}, REGELN, THIRD_PARTY)
    bom["components"][1]["licenses"] = [
        {"license": {"name": "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
                     "acknowledgement": "declared"}}]
    with pytest.raises(NachweisFehler, match="nicht erlaubt"):
        lizenzen.pruefe({"web": bom}, REGELN, THIRD_PARTY)
    bom["components"][1]["licenses"] = [
        {"license": {"name": "Freitext ohne Zuordnung", "acknowledgement": "declared"}}]
    with pytest.raises(NachweisFehler, match="ohne Zuordnung"):
        lizenzen.pruefe({"web": bom}, REGELN, THIRD_PARTY)


def test_jede_erklaerte_angabe_muss_passen():
    # id erlaubt, zusätzlicher Freitext nicht zugeordnet → trotzdem Fehler (certifi-Muster).
    bom = _gut()
    bom["components"][1]["licenses"].append(
        {"license": {"name": "Irgendwas", "acknowledgement": "declared"}})
    with pytest.raises(NachweisFehler, match="ohne Zuordnung"):
        lizenzen.pruefe({"web": bom}, REGELN, THIRD_PARTY)


def test_komponente_muss_mit_version_genannt_sein():
    bom = _gut()
    bom["components"][1]["version"] = "10.11.0"
    bom["components"][1]["purl"] = "pkg:npm/ol@10.11.0"
    with pytest.raises(NachweisFehler) as e:
        lizenzen.pruefe({"web": bom}, REGELN, THIRD_PARTY)
    assert "ol 10.11.0 nicht in docs/THIRD_PARTY.md" in e.value.text


def test_nennung_nur_in_tabellenzeile_zaehlt():
    bom = _bom(_k("earcut", "2.2.4", "pkg:npm/earcut@2.2.4", _id("MIT")))
    with pytest.raises(NachweisFehler, match=r"earcut 2\.2\.4 nicht in docs/THIRD_PARTY\.md"):
        lizenzen.pruefe({"web": bom}, REGELN, THIRD_PARTY)


def test_verschachtelte_komponenten_werden_geprueft():
    innen = _k("pako", "2.1.0", "pkg:npm/pako@2.1.0", _id("MIT"))
    bom = _gut()
    bom["components"][1]["components"] = [innen]
    with pytest.raises(NachweisFehler, match=r"pako 2\.1\.0"):
        lizenzen.pruefe({"web": bom}, REGELN, THIRD_PARTY)


def _datei(pfad: str) -> dict[str, Any]:
    return {"type": "file", "bom-ref": f"datei:{pfad}", "name": pfad.rsplit("/", 1)[-1],
            "hashes": [{"alg": "SHA-512", "content": "0" * 128}]}


def test_dateien_eines_pakets_gelten_ueber_das_paket():
    # sbom.nachbearbeiten hängt mehrere ausgelieferte Dateien eines Pakets als `file` darunter.
    bom = _gut()
    bom["components"][1]["components"] = [_datei("web/dist/wasm/basis/basis_transcoder.js"),
                                          _datei("web/dist/wasm/basis/basis_transcoder.wasm")]
    lizenzen.pruefe({"web": bom}, REGELN, THIRD_PARTY)


def test_datei_auf_oberster_ebene_braucht_lizenz():
    bom = _gut()
    bom["components"].append(_datei("web/dist/unbekannt.js"))
    with pytest.raises(NachweisFehler, match=r"datei:web/dist/unbekannt\.js: keine erklärte"):
        lizenzen.pruefe({"web": bom}, REGELN, THIRD_PARTY)


def test_datei_unter_datei_wird_geprueft():
    bom = _gut()
    aussen = _datei("web/dist/a.js")
    aussen["components"] = [_datei("web/dist/b.js")]
    bom["components"].append(aussen)
    with pytest.raises(NachweisFehler, match=r"datei:web/dist/b\.js: keine erklärte"):
        lizenzen.pruefe({"web": bom}, REGELN, THIRD_PARTY)


def test_namen_normalisiert_und_paare():
    bom = _bom(
        _k("pydantic_core", "2.46.5", "pkg:pypi/pydantic-core@2.46.5", _id("MIT")),
        _k("pydantic", "2.13.5", "pkg:pypi/pydantic@2.13.5", _id("MIT")),
        _k("PyJWT", "2.14.0", "pkg:pypi/pyjwt@2.14.0", _id("MIT")),
    )
    assert lizenzen.pruefe({"server": bom}, REGELN, THIRD_PARTY)["ergebnis"] == "bestanden"
    # Paarzuordnung: pydantic-core hat 2.46.5, nicht 2.13.5.
    falsch = _bom(_k("pydantic_core", "2.13.5", "pkg:pypi/pydantic-core@2.13.5", _id("MIT")))
    with pytest.raises(NachweisFehler, match=r"pydantic-core 2\.13\.5"):
        lizenzen.pruefe({"server": falsch}, REGELN, THIRD_PARTY)


def test_alle_probleme_auf_einmal():
    bom = _gut()
    bom["components"][0]["licenses"] = [_id("GPL-2.0-only")]
    bom["components"][2]["version"] = "9.9.9"
    bom["components"][2]["purl"] = "pkg:npm/pako@9.9.9"
    with pytest.raises(NachweisFehler) as e:
        lizenzen.pruefe({"web": bom}, REGELN, THIRD_PARTY)
    assert "GPL-2.0-only" in e.value.text and "pako 9.9.9" in e.value.text


def test_informativ_bricht_nicht_ab():
    bom = _gut()
    bom["components"][0]["licenses"] = [_id("GPL-2.0-only")]
    probleme = lizenzen.probleme(bom, REGELN, lizenzen.genannte_komponenten(THIRD_PARTY))
    assert any("GPL-2.0-only" in p for p in probleme)


def test_leeres_sbom_fail_closed():
    with pytest.raises(NachweisFehler, match="keine Komponenten"):
        lizenzen.pruefe({"web": _bom()}, REGELN, THIRD_PARTY)


def test_regeln_laden(tmp_path):
    datei = tmp_path / "lizenzen.toml"
    datei.write_text('erlaubt = ["MIT"]\n[klassifikatoren]\n"License :: X" = "MIT"\n',
                     encoding="utf-8")
    r = lizenzen.lade_regeln(datei)
    assert r.erlaubt == frozenset({"MIT"}) and r.klassifikatoren == {"License :: X": "MIT"}
    for kaputt in ('erlaubt = "MIT"\n', 'erlaubt = []\n', 'erlaubt = ["MIT"]\nx = 1\n',
                   'erlaubt = ["MIT"]\n[klassifikatoren]\n"A" = "MIT AND"\n',
                   'erlaubt = ["MIT"]\n[klassifikatoren]\n"A" = 3\n'):
        datei.write_text(kaputt, encoding="utf-8")
        with pytest.raises(NachweisFehler) as e:
            lizenzen.lade_regeln(datei)
        assert e.value.regel == "lizenzen"


def test_repo_regeln_gueltig():
    r = lizenzen.lade_regeln(WURZEL / "nachweise" / "lizenzen.toml")
    for spdx in ("MIT", "BSD-3-Clause", "Apache-2.0", "ISC", "MPL-2.0", "PSF-2.0"):
        assert spdx in r.erlaubt
    for verboten in ("GPL-3.0-only", "AGPL-3.0-only", "SSPL-1.0", "BUSL-1.1"):
        assert verboten not in r.erlaubt


def test_belegte_feststellung_nur_fuer_genau_diese_version():
    regeln = lizenzen.Lizenzregeln(REGELN.erlaubt, REGELN.klassifikatoren,
                                   {("ol", "10.10.0"): "BSD-2-Clause AND Apache-2.0"})
    bom = _gut()
    ol = next(k for k in bom["components"] if k.get("name") == "ol")
    del ol["licenses"]
    lizenzen.pruefe({"web": bom}, regeln, THIRD_PARTY)  # festgestellt → bestanden
    ol["version"] = "10.10.1"  # andere Version: Feststellung gilt nicht
    with pytest.raises(NachweisFehler, match="keine erklärte Lizenz"):
        lizenzen.pruefe({"web": bom}, regeln, THIRD_PARTY.replace("10.10.0", "10.10.1"))


def test_feststellung_ersetzt_keine_erklaerte_lizenz():
    regeln = lizenzen.Lizenzregeln(REGELN.erlaubt, REGELN.klassifikatoren,
                                   {("ol", "10.10.0"): "MIT"})
    bom = _gut()
    ol = next(k for k in bom["components"] if k.get("name") == "ol")
    ol["licenses"] = [{"license": {"id": "GPL-3.0-only"}}]
    with pytest.raises(NachweisFehler, match=r"GPL-3\.0-only nicht erlaubt"):
        lizenzen.pruefe({"web": bom}, regeln, THIRD_PARTY)


def test_feststellungen_laden(tmp_path):
    datei = tmp_path / "lizenzen.toml"
    gut = ('erlaubt = ["MIT"]\n[feststellungen]\n"x-y@1.0" = { ausdruck = "MIT", '
           f'beleg = "LICENSE", sha256 = "{"a" * 64}" }}\n')
    datei.write_text(gut, encoding="utf-8")
    assert lizenzen.lade_regeln(datei).feststellungen == {("x-y", "1.0"): "MIT"}
    for kaputt in (gut.replace("x-y@1.0", "x-y"),
                   gut.replace('ausdruck = "MIT"', 'ausdruck = "GPL-3.0-only"'),
                   gut.replace("a" * 64, "kurz"), gut.replace('beleg = "LICENSE", ', ""),
                   gut.replace("ausdruck", "lizenz")):
        assert kaputt != gut
        datei.write_text(kaputt, encoding="utf-8")
        with pytest.raises(NachweisFehler):
            lizenzen.lade_regeln(datei)


def test_repo_feststellung_embreex_passt_zum_lizenztext():
    r = lizenzen.lade_regeln(WURZEL / "nachweise" / "lizenzen.toml")
    assert r.feststellungen[("embreex", "4.4.0")] == "BSD-2-Clause AND Apache-2.0"


# --- Ausnahme „nur externer Prozess“ (PDAL-Umgebung, ADR 2026-10-22) ------------------------------

LOCK = "a" * 64
SHA = "b" * 64
EXTERN = lizenzen.Lizenzregeln(
    erlaubt=REGELN.erlaubt, klassifikatoren=REGELN.klassifikatoren,
    extern_umgebung="pdal", extern_lock_sha256=LOCK,
    extern={("untwine", "1.5.1"): lizenzen.ExternerProzess("h419fb5e_0", "GPL-3.0-or-later", SHA)})
GENANNT = {("untwine", "1.5.1"), ("libx", "1.0")}


def _conda(name: str = "untwine", version: str = "1.5.1", lizenz: str = "GPL-3.0-or-later", *,
           umgebung: str = "pdal", lock: str = LOCK, build: str = "h419fb5e_0",
           sha: str = SHA) -> dict[str, Any]:
    return {"type": "application", "name": name, "version": version, "bom-ref": f"pdal|{name}",
            "purl": f"pkg:conda/{name}@{version}?build={build}&channel=conda-forge",
            "licenses": [{"expression": lizenz}], "hashes": [{"alg": "SHA-256", "content": sha}],
            "properties": [{"name": "scanview:umgebung", "value": umgebung},
                           {"name": "scanview:conda-build", "value": build},
                           {"name": "scanview:lock-sha256", "value": lock}]}


def test_extern_gedeckt_nur_bei_voller_bindung():
    assert lizenzen.probleme(_bom(_conda()), EXTERN, GENANNT) == []


@pytest.mark.parametrize("abweichung", [
    {"lock": "c" * 64},                       # anderes Lock → Neubewertung
    {"sha": "c" * 64},                        # anderes Paket gleicher Version
    {"build": "h000000_1"},                   # anderer Build
    {"umgebung": "andere"},                   # anderes Paket außerhalb der Umgebung
    {"lizenz": "AGPL-3.0-only"},              # andere Lizenz
    {"version": "1.5.2"},                     # neue Version
], ids=["lock", "sha", "build", "umgebung", "lizenz", "version"])
def test_extern_jede_abweichung_ist_verstoss(abweichung):
    genannt = GENANNT | {("untwine", "1.5.2")}
    p = lizenzen.probleme(_bom(_conda(**abweichung)), EXTERN, genannt)
    assert len(p) == 1 and "nicht erlaubt" in p[0], p


def test_extern_neues_paket_ohne_eintrag_ist_verstoss():
    p = lizenzen.probleme(_bom(_conda("libx", "1.0", "GPL-2.0-or-later")), EXTERN, GENANNT)
    assert len(p) == 1 and "GPL-2.0-or-later nicht erlaubt" in p[0], p


def test_extern_gilt_nicht_ohne_regel():
    p = lizenzen.probleme(_bom(_conda()), REGELN, GENANNT)
    assert len(p) == 1 and "nicht erlaubt" in p[0]


def test_extern_laden(tmp_path):
    kopf = 'erlaubt = ["MIT"]\n[externer_prozess]\nadr = "x"\numgebung = "pdal"\n'
    gut = (kopf + f'lock_sha256 = "{LOCK}"\n[externer_prozess.pakete]\n'
           f'"untwine@1.5.1" = {{ build = "b", lizenz = "GPL-3.0-or-later", sha256 = "{SHA}" }}\n')
    (tmp_path / "gut.toml").write_text(gut)
    r = lizenzen.lade_regeln(tmp_path / "gut.toml")
    assert r.extern[("untwine", "1.5.1")].lizenz == "GPL-3.0-or-later"
    assert r.extern_umgebung == "pdal"
    for kaputt in (
        gut.replace(f'lock_sha256 = "{LOCK}"', 'lock_sha256 = "kurz"'),
        gut.replace('adr = "x"\n', ""),
        gut.replace('lizenz = "GPL-3.0-or-later"', 'lizenz = "MIT"'),  # schon erlaubt
        gut.replace(f'sha256 = "{SHA}"', 'sha256 = ""'),
        gut.replace('"untwine@1.5.1"', '"untwine"'),
        kopf + f'lock_sha256 = "{LOCK}"\n[externer_prozess.pakete]\n',
    ):
        (tmp_path / "k.toml").write_text(kaputt)
        with pytest.raises(NachweisFehler, match="externer_prozess"):
            lizenzen.lade_regeln(tmp_path / "k.toml")


def test_repo_ausnahme_passt_zum_lock_und_ist_eng():
    """Die Ausnahme im Repo nennt genau die fünf Pakete der ADR, bindet den aktuellen Lock und
    jede Eintragung stimmt mit dem Lock überein."""
    from scanview_nachweise import pdal_umgebung as pu

    r = lizenzen.lade_regeln(WURZEL / "nachweise" / "lizenzen.toml")
    lock = (WURZEL / pu.LOCK).read_text(encoding="utf-8")
    assert r.extern_umgebung == pu.UMGEBUNG and r.extern_lock_sha256 == pu.lock_sha256(lock)
    assert {n for n, _v in r.extern} == {"untwine", "librttopo", "readline", "libspatialite",
                                          "freexl"}
    pakete = {(p.name, p.version): p for p in pu.lies_lock(lock)}
    for schluessel, e in r.extern.items():
        p = pakete[schluessel]
        assert (p.build, p.lizenz, p.sha256) == (e.build, e.lizenz, e.sha256)
    for gpl in ("GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later", "MPL-1.1"):
        assert gpl not in r.erlaubt


def test_repo_komponenten_der_umgebung_bestehen_die_lizenzpruefung():
    """Alle Pakete der PDAL-Umgebung (wie im Import-SBOM) bestehen Positivliste + Ausnahme und
    sind in THIRD_PARTY.md genannt."""
    from scanview_nachweise import pdal_umgebung as pu

    r = lizenzen.lade_regeln(WURZEL / "nachweise" / "lizenzen.toml")
    lock = (WURZEL / pu.LOCK).read_text(encoding="utf-8")
    genannt = lizenzen.genannte_komponenten(
        (WURZEL / "docs" / "THIRD_PARTY.md").read_text(encoding="utf-8"))
    bom = {"metadata": {"component": {"name": "scanview-import"}},
           "components": pu.komponenten(lock)}
    assert lizenzen.probleme(bom, r, genannt) == []
