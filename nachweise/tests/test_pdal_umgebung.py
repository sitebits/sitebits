"""PDAL-Umgebung des Imports: Lock lesen (fail-closed), Beilage gegen das Lock, SBOM-Komponenten."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scanview_nachweise import pdal_umgebung as pu
from scanview_nachweise.fehler import NachweisFehler

WURZEL = Path(__file__).resolve().parents[2]
LOCK = (WURZEL / pu.LOCK).read_text(encoding="utf-8")


def test_repo_lock_gelesen():
    pakete = {p.name: p for p in pu.lies_lock(LOCK)}
    assert pakete["libpdal-core"].version == "2.10.2" and pakete["untwine"].version == "1.5.1"
    assert pakete["libarchive"].build.startswith("lgpl_")  # LGPL-Variante, kein lzo
    assert "lzo" not in pakete
    assert all(p.subdir in ("linux-64", "noarch") for p in pakete.values())


@pytest.mark.parametrize(("aendern", "meldung"), [
    (lambda t: t.replace("version: 7", "version: 6", 1), "Lock-Version 7"),
    (lambda t: t.replace("https://conda.anaconda.org/conda-forge/linux-64/untwine",
                         "https://example.org/linux-64/untwine"), "conda-forge"),
    (lambda t: t.replace("  license: GPL-3.0-or-later\n", "", 1), "keine Lizenzangabe"),
    (lambda t: t + "- pypi: https://files.example/x.whl\n", "PyPI"),
    (lambda t: t + "fremd: 1\n", "unerwartete Zeile"),
], ids=["version", "kanal", "lizenz", "pypi", "fremd"])
def test_lock_fail_closed(aendern, meldung):
    with pytest.raises(NachweisFehler, match=meldung):
        pu.lies_lock(aendern(LOCK))


def test_lock_sha_veraendert_paketliste_ungleich():
    url = next(p.url for p in pu.lies_lock(LOCK) if p.name == "untwine")
    ohne_liste = LOCK.replace(f"      - conda: {url}\n", "", 1)
    with pytest.raises(NachweisFehler, match="Paketliste"):
        pu.lies_lock(ohne_liste)


def _kopie(tmp_path: Path) -> Path:
    ziel = tmp_path / pu.ORDNER
    shutil.copytree(WURZEL / pu.ORDNER, ziel)
    return tmp_path


def test_repo_beilage_passt():
    assert pu.pruefe(WURZEL)["pakete"] == len(pu.lies_lock(LOCK))


def test_beilage_zu_altem_lock_ist_fehler(tmp_path):
    wt = _kopie(tmp_path)
    (wt / pu.LOCK).write_text(LOCK.replace("  timestamp:", "  timestamp:", 1) + "\n")
    with pytest.raises(NachweisFehler, match="gehört nicht zu diesem Lock"):
        pu.pruefe(wt)


def test_lizenzen_txt_im_repo_ist_fehler(tmp_path):
    """Weg 3: die Lizenztexte erzeugt einrichten.sh auf dem Host, nie das Repo."""
    wt = _kopie(tmp_path)
    (wt / pu.LIZENZTEXTE).write_text("Lizenztexte …\n", encoding="utf-8")
    with pytest.raises(NachweisFehler, match="gehört nicht ins Repo"):
        pu.pruefe(wt)


def _quellen_aendern(wt: Path, aendern) -> None:
    q = json.loads((wt / pu.QUELLEN).read_text(encoding="utf-8"))
    aendern({e["name"]: e for e in q["pakete"]})
    (wt / pu.QUELLEN).write_text(json.dumps(q), encoding="utf-8")


@pytest.mark.parametrize(("aendern", "meldung"), [
    (lambda q: q["untwine"].update(lizenzdateien=[], ergaenzungen=[]),
     "untwine: weder Lizenzdatei noch Ergänzung"),
    (lambda q: q["libgcc-ng"].update(ergaenzungen=[]), "libgcc-ng: weder Lizenzdatei"),
    (lambda q: q["untwine"].update(lizenzdateien=["../readline-8.3-hd6e31c0_1/info/x"]),
     "unzulässigem Pfad"),
    (lambda q: q["sqlite"]["ergaenzungen"][0].update(paket="fremd"), "außerhalb des Locks"),
    (lambda q: q["sqlite"]["ergaenzungen"][0].update(erklaerung=""), "unvollständig"),
    (lambda q: q["untwine"].pop("ergaenzungen"), "lizenzdateien/ergaenzungen fehlen"),
], ids=["ohne-bezug", "ng-ohne-ergaenzung", "pfad-hinaus", "fremdes-paket", "ohne-erklaerung",
        "feld-fehlt"])
def test_quellen_lizenzbezug_fail_closed(tmp_path, aendern, meldung):
    wt = _kopie(tmp_path)
    _quellen_aendern(wt, aendern)
    with pytest.raises(NachweisFehler, match=meldung):
        pu.pruefe(wt)


def test_quellen_ohne_rezept_ist_fehler(tmp_path):
    wt = _kopie(tmp_path)
    q = json.loads((wt / pu.QUELLEN).read_text(encoding="utf-8"))
    q["pakete"][0]["rezept"]["feedstock"] = ""
    (wt / pu.QUELLEN).write_text(json.dumps(q), encoding="utf-8")
    with pytest.raises(NachweisFehler, match="Rezept"):
        pu.pruefe(wt)


def test_repo_lizenzbezug_fuer_gpl_gcc_und_sqlite():
    """Die Texte entstehen auf dem Host; im Repo muss der Bezug stimmen: GPL-Pakete mit eigener
    Lizenzdatei, GCC-Laufzeit mit Ausnahme + GPL-3.0-Text aus readline, SQLite mit Segenstext."""
    q = {e["name"]: e for e in json.loads((WURZEL / pu.QUELLEN).read_text("utf-8"))["pakete"]}
    for name in ("untwine", "librttopo", "readline"):
        assert q[name]["lizenzdateien"], name
        assert q[name]["upstream"]["url"].startswith("http")
        assert q[name]["rezept"]["feedstock"].startswith("https://github.com/conda-forge/")
    gpl3 = {"paket": "readline", "datei": "info/licenses/COPYING"}
    for name in ("libgcc", "libstdcxx", "libgomp", "libgcc-ng", "libstdcxx-ng"):
        herkunft = [{k: e[k] for k in ("paket", "datei")} for e in q[name]["ergaenzungen"]]
        assert gpl3 in herkunft, name
        ausnahme = [*q[name]["lizenzdateien"], *(e["datei"] for e in q[name]["ergaenzungen"])]
        assert any("RUNTIME.LIBRARY.EXCEPTION" in d for d in ausnahme), name
    for name in ("libsqlite", "sqlite"):
        assert q[name]["ergaenzungen"] == [{"paket": "libsqlite", "datei": "include/sqlite3.h",
                                            "erklaerung": "Segenstext (Kopf von sqlite3.h)"}]
    assert not (WURZEL / pu.LIZENZTEXTE).exists()


def test_komponenten_und_abhaengigkeiten():
    k = pu.komponenten(LOCK)
    refs = {x["bom-ref"] for x in k}
    assert len(k) == len(refs) == len(pu.lies_lock(LOCK))
    untwine = next(x for x in k if x["name"] == "untwine")
    assert untwine["type"] == "application"
    assert untwine["licenses"] == [{"license": {"id": "GPL-3.0-or-later"}}]
    ncurses = next(x for x in k if x["name"] == "ncurses")
    assert ncurses["licenses"] == [{"expression": "X11 AND BSD-3-Clause"}]
    quellen = json.loads((WURZEL / pu.QUELLEN).read_text(encoding="utf-8"))
    mit = next(x for x in pu.komponenten(LOCK, quellen) if x["name"] == "untwine")
    typen = {r["type"]: r["url"] for r in mit["externalReferences"]}
    assert typen["source-distribution"].startswith("https://github.com/hobuinc/untwine")
    assert typen["vcs"] == "https://github.com/conda-forge/untwine-feedstock"
    assert mit["manufacturer"]["url"]
    eig = {p["name"]: p["value"] for p in untwine["properties"]}
    assert eig["scanview:lock-sha256"] == pu.lock_sha256(LOCK)
    assert eig["scanview:umgebung"] == "pdal"
    abh = {d["ref"]: d["dependsOn"] for d in pu.abhaengigkeiten(LOCK)}
    assert set(abh) == refs and all(set(v) <= refs for v in abh.values())
    assert any("libpdal-core" in r for r in abh[untwine["bom-ref"]])


def test_sha512_beleg_prueft_sha256():
    pakete = pu.lies_lock(LOCK)
    daten = {p.url: f"inhalt {p.name}".encode() for p in pakete}
    falsch = pakete[0]
    gefaelscht = LOCK.replace(falsch.sha256, hashlib.sha256(daten[falsch.url]).hexdigest())
    with pytest.raises(NachweisFehler, match="SHA-256 weicht"):
        pu.sha512_belege(gefaelscht, lambda url: daten[url])


def test_sbom_import_bekommt_umgebung(monkeypatch):
    from scanview_nachweise import sbom

    gesehen: list[str] = []
    monkeypatch.setattr(pu, "sha512_belege", lambda lock, laden: {
        k["bom-ref"]: (laden(k["externalReferences"][0]["url"]) and k["externalReferences"][0]
                       ["url"], "f" * 128) for k in pu.komponenten(lock)})
    bom = {"metadata": {"component": {"bom-ref": "haupt", "name": "scanview-import"}},
           "components": [{"bom-ref": "numpy", "name": "numpy"}],
           "dependencies": [{"ref": "haupt", "dependsOn": ["numpy"]}]}
    belege: dict[str, sbom.Beleg] = {}
    schritte = sbom.pdal_umgebung_einfuegen(bom, WURZEL, belege,
                                            lambda url: gesehen.append(url) or b"x")
    n = len(pu.lies_lock(LOCK))
    assert len(bom["components"]) == n + 1 and len(belege) == n and len(schritte) == n
    assert all(s.befehl[0] == "herunterladen" for s in schritte) and len(gesehen) == n
    haupt = next(d for d in bom["dependencies"] if d["ref"] == "haupt")
    assert "numpy" in haupt["dependsOn"] and len(haupt["dependsOn"]) == n + 1
    with pytest.raises(NachweisFehler, match="doppelt"):
        sbom.pdal_umgebung_einfuegen(bom, WURZEL, belege, lambda url: b"x")


def test_sbom_ohne_lock_unveraendert(tmp_path):
    from scanview_nachweise import sbom

    bom = {"metadata": {"component": {"bom-ref": "haupt"}}, "components": []}
    assert sbom.pdal_umgebung_einfuegen(bom, tmp_path, {}, lambda url: b"") == []
    assert bom["components"] == []


def _fixture_cache(tmp_path: Path) -> tuple[str, Path]:
    """Synthetischer Cache zum echten Lock: je Paket index.json/about.json, eine Lizenzdatei
    (GCC-Metapakete ohne, wie echt) und die Quelldateien der Ergänzungen."""
    cache = tmp_path / "cache"
    for p in pu.lies_lock(LOCK):
        ordner = cache / "pkgs" / p.dateiname.removesuffix(".conda")
        (ordner / "info").mkdir(parents=True)
        (ordner / "info" / "index.json").write_text(json.dumps(
            {"name": p.name, "version": p.version, "build": p.build}))
        (ordner / "info" / "about.json").write_text(json.dumps(
            {"home": "https://example.org", "extra": {
                "remote_url": f"https://github.com/conda-forge/{p.name}-feedstock",
                "sha": "a" * 40}}))
        if not p.name.endswith("-ng") and "sqlite" not in p.name:
            (ordner / "info" / "licenses").mkdir()
            (ordner / "info" / "licenses" / "LICENSE").write_text("synthetischer Lizenztext " * 4)
        extra = {"libgcc": "share/licenses/gcc-libs/RUNTIME.LIBRARY.EXCEPTION",
                 "libstdcxx": "share/licenses/libstdc++/RUNTIME.LIBRARY.EXCEPTION",
                 "readline": "info/licenses/COPYING"}.get(p.name)
        if extra:
            (ordner / extra).parent.mkdir(parents=True, exist_ok=True)
            (ordner / extra).write_text("synthetische Ausnahme bzw. GPL-Text " * 3)
        if p.name == "libsqlite":
            (ordner / "include").mkdir()
            (ordner / "include" / "sqlite3.h").write_text("/*\n** synthetischer Segen " * 3
                                                          + "\n*/\nint x;\n")
    return LOCK, cache


def test_erzeuge_aus_cache_mit_ergaenzungen(tmp_path):
    lock, cache = _fixture_cache(tmp_path)
    q = {e["name"]: e for e in pu.erzeuge(lock, cache)["pakete"]}
    assert len(q) == len(pu.lies_lock(LOCK))
    assert q["libgcc-ng"]["lizenzdateien"] == []
    assert [e["paket"] for e in q["libgcc-ng"]["ergaenzungen"]] == ["libgcc", "readline"]
    assert q["sqlite"]["ergaenzungen"][0]["datei"] == "include/sqlite3.h"


def test_erzeuge_ohne_lizenztext_ist_fehler(tmp_path):
    """Jedes Paket braucht einen Lizenztext (eigener oder belegte Ergänzung)."""
    lock, cache = _fixture_cache(tmp_path)
    untwine = next(p for p in pu.lies_lock(LOCK) if p.name == "untwine")
    (cache / "pkgs" / untwine.dateiname.removesuffix(".conda") / "info" / "licenses"
     / "LICENSE").write_text("kurz")
    with pytest.raises(NachweisFehler, match="untwine: kein Lizenztext"):
        pu.erzeuge(lock, cache)
