"""Release-Artefakte: deterministische Tarballs aus einem eingefrorenen Worktree, mit
Lieferumfang je Artefakt. Getestet gegen ein kleines Test-Repo mit injizierter Bau-Funktion (kein
echter npm-Lauf); der Standard-Webbau wird mit gepatchtem `ausfuehren` geprüft."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from scanview_nachweise import artefakte
from scanview_nachweise.artefakte import (
    Artefakt,
    Bauergebnis,
    Datei,
    Netzschritt,
    baue_artefakte,
    installierte_registry_pakete,
    lade_npm_ausnahmen,
    npm_signaturen,
    web_bauen,
    werte_npm_signaturen_aus,
)
from scanview_nachweise.fehler import NachweisFehler

WURZEL = Path(__file__).resolve().parents[2]
NPM_FIXTURES = Path(__file__).parent / "fixtures" / "npm-signaturen"

LANGER_PFAD = "server/src/scanview_server/" + "sehr_langer_unterordner_name/" * 3 + "modul.py"
KOMMA_PFAD = "server/src/scanview_server/daten,mit komma.json"
UMLAUT_PFAD = "import/src/scanview_import/größen.py"
POSITIVLISTE = """\
web/**
server/**
mcp-server/**
import/**
deploy/platform/**
!deploy/platform/README.md
"""


def _git(wurzel: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=wurzel, check=True, capture_output=True,
                          text=True).stdout


def _schreibe(wurzel: Path, dateien: dict[str, str]) -> None:
    for relativ, inhalt in dateien.items():
        pfad = wurzel / relativ
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(inhalt, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Mini-Repo mit allen vier Komponenten, Tests, einem (versehentlich) versionierten
    `__pycache__`, einem ausführbaren Skript, langem Pfad, Komma und Umlaut."""
    wurzel = tmp_path / "repo"
    wurzel.mkdir()
    _git(wurzel, "init", "-q", "-b", "main")
    _git(wurzel, "config", "user.email", "test@example.invalid")
    _git(wurzel, "config", "user.name", "Test")
    _git(wurzel, "config", "commit.gpgsign", "false")
    _schreibe(wurzel, {
        "web/package.json": json.dumps({"name": "w", "scripts": {"build": "x"}}),
        "web/src/main.ts": "export {}\n",
        "server/pyproject.toml": "[project]\nname='s'\n",
        "server/uv.lock": "version = 1\n",
        "server/src/scanview_server/__init__.py": "",
        "server/src/scanview_server/migrations/0001_konten.sql": "CREATE TABLE t (x);\n",
        "server/tests/test_x.py": "def test_x(): pass\n",
        "server/tests/conftest.py": "",
        LANGER_PFAD: "x = 1\n",
        KOMMA_PFAD: "{}\n",
        "deploy/platform/einrichten.sh": "#!/bin/sh\necho ok\n",
        "deploy/platform/scanview.caddy": "# caddy\n",
        "deploy/platform/test/messen.sh": "#!/bin/sh\n",
        "mcp-server/pyproject.toml": "[project]\nname='m'\n",
        "mcp-server/uv.lock": "version = 1\n",
        "mcp-server/src/scanview_mcp/server.py": "pass\n",
        "mcp-server/tests/test_m.py": "",
        "import/pyproject.toml": "[project]\nname='i'\n",
        "import/uv.lock": "version = 1\n",
        "import/docker/untwine.Dockerfile": "FROM scratch\n",
        UMLAUT_PFAD: "pass\n",
        "import/scripts/abnahme_messung.py": "# Entwicklerwerkzeug, nicht ausliefern\n",
        "import/tests/test_i.py": "",
        "nachweise/README.md": "gehört in kein Artefakt\n",
        # Betriebs-README: versioniert, aber nicht öffentlich — also auch nicht ausgeliefert.
        "deploy/platform/README.md": "Betrieb auf host-intern mit Projekt kunde-x\n",
        "veroeffentlichung/positivliste.txt": POSITIVLISTE,
    })
    (wurzel / "deploy/platform/einrichten.sh").chmod(0o755)
    # Versehentlich versionierter Cache und .venv: dürfen trotz `git ls-files` nicht ins Artefakt.
    cache = wurzel / "server/src/scanview_server/__pycache__/db.cpython-312.pyc"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"\x00pyc")
    _git(wurzel, "add", "-A")
    venv = wurzel / "server/.venv/pyvenv.cfg"
    venv.parent.mkdir(parents=True)
    venv.write_text("home = /usr\n", encoding="utf-8")
    _git(wurzel, "add", "-f", str(cache.relative_to(wurzel)), str(venv.relative_to(wurzel)))
    _git(wurzel, "commit", "-q", "-m", "init")
    # Unversionierter Müll im Arbeitsbaum: darf ebenfalls nicht ins Artefakt.
    _schreibe(wurzel, {
        "server/.venv/lib/x.py": "",
        "server/.pytest_cache/v": "",
        "server/dist/alt-0.0.1-py3-none-any.whl": "alt",
        "server/src/scanview_server/lokal.py": "unversioniert\n",
    })
    return wurzel


def _test_bau(worktree: Path) -> list[Netzschritt]:
    """Ersetzt npm: legt `web/dist` mit fester Struktur an und meldet einen Netzschritt."""
    _schreibe(worktree, {
        "web/dist/index.html": "<html/>",
        "web/dist/assets/app,1.js": "console.log(1)",
        "web/node_modules/x/index.js": "nicht ausliefern",
    })
    return [Netzschritt(befehl=["npm", "ci", "--ignore-scripts"], cwd="web",
                        zeit="2026-09-28T00:00:00+00:00")]


def _namen(pfad: Path) -> list[str]:
    with tarfile.open(pfad, "r:gz") as tar:
        return tar.getnames()


def _nach_name(ergebnis: Bauergebnis) -> dict[str, Artefakt]:
    return {a.name: a for a in ergebnis.artefakte}


def test_vier_artefakte_mit_versionsnamen(repo: Path, tmp_path: Path) -> None:
    ergebnis = baue_artefakte(repo, "0.1.0", tmp_path / "ziel", bauen=_test_bau)
    namen = sorted(a.pfad.name for a in ergebnis.artefakte)
    assert namen == ["scanview-import-0.1.0.tar.gz", "scanview-mcp-0.1.0.tar.gz",
                     "scanview-server-0.1.0.tar.gz", "scanview-web-0.1.0.tar.gz"]
    assert sorted(_nach_name(ergebnis)) == ["import", "mcp", "server", "web"]
    for a in ergebnis.artefakte:
        daten = a.pfad.read_bytes()
        assert a.sha256 == hashlib.sha256(daten).hexdigest()
        assert a.sha512 == hashlib.sha512(daten).hexdigest()
        assert a.pfad.parent == tmp_path / "ziel"


def test_netzschritte_werden_durchgereicht(repo: Path, tmp_path: Path) -> None:
    ergebnis = baue_artefakte(repo, "0.1.0", tmp_path / "ziel", bauen=_test_bau)
    assert ergebnis.netzschritte == [Netzschritt(befehl=["npm", "ci", "--ignore-scripts"],
                                                 cwd="web", zeit="2026-09-28T00:00:00+00:00")]


def test_zwei_laeufe_byte_identisch(repo: Path, tmp_path: Path) -> None:
    a = baue_artefakte(repo, "0.1.0", tmp_path / "a", bauen=_test_bau)
    # Zweiter Lauf aus einer Kopie an anderem Pfad: das Ergebnis darf vom Ort nicht abhängen.
    kopie = tmp_path / "kopie"
    _git(tmp_path, "clone", "-q", str(repo), str(kopie))
    b = baue_artefakte(kopie, "0.1.0", tmp_path / "b", bauen=_test_bau)
    assert {x.name: x.sha256 for x in a.artefakte} == {x.name: x.sha256 for x in b.artefakte}
    assert {x.name: x.lieferumfang for x in a.artefakte} == \
        {x.name: x.lieferumfang for x in b.artefakte}


def test_web_enthaelt_nur_dist(repo: Path, tmp_path: Path) -> None:
    web = _nach_name(baue_artefakte(repo, "0.1.0", tmp_path / "z", bauen=_test_bau))["web"]
    assert _namen(web.pfad) == ["web", "web/dist", "web/dist/assets",
                                "web/dist/assets/app,1.js", "web/dist/index.html"]
    assert [d.pfad_im_artefakt for d in web.lieferumfang] == \
        ["web/dist/assets/app,1.js", "web/dist/index.html"]


def test_server_umfang(repo: Path, tmp_path: Path) -> None:
    server = _nach_name(baue_artefakte(repo, "0.1.0", tmp_path / "z", bauen=_test_bau))["server"]
    dateien = [d.pfad_im_artefakt for d in server.lieferumfang]
    assert "server/uv.lock" in dateien
    assert "server/src/scanview_server/migrations/0001_konten.sql" in dateien
    assert "deploy/platform/einrichten.sh" in dateien
    assert KOMMA_PFAD in dateien
    assert LANGER_PFAD in dateien
    for verboten in ("__pycache__", ".venv", ".pytest_cache", "/tests/", "/test/", "/dist/",
                     "lokal.py", "nachweise/"):
        assert not any(verboten in p for p in _namen(server.pfad)), verboten


def test_mcp_und_import_umfang(repo: Path, tmp_path: Path) -> None:
    alle = _nach_name(baue_artefakte(repo, "0.1.0", tmp_path / "z", bauen=_test_bau))
    assert [d.pfad_im_artefakt for d in alle["mcp"].lieferumfang] == [
        "mcp-server/pyproject.toml", "mcp-server/src/scanview_mcp/server.py",
        "mcp-server/uv.lock"]
    assert [d.pfad_im_artefakt for d in alle["import"].lieferumfang] == [
        "import/docker/untwine.Dockerfile", "import/pyproject.toml", UMLAUT_PFAD,
        "import/uv.lock"]


def test_eintraege_sortiert_und_normalisiert(repo: Path, tmp_path: Path) -> None:
    ergebnis = baue_artefakte(repo, "0.1.0", tmp_path / "z", bauen=_test_bau)
    commit_zeit = int(_git(repo, "show", "-s", "--format=%ct", "HEAD").strip())
    for a in ergebnis.artefakte:
        with tarfile.open(a.pfad, "r:gz") as tar:
            mitglieder = tar.getmembers()
        namen = [m.name for m in mitglieder]
        assert namen == sorted(namen)
        for m in mitglieder:
            assert m.mtime == commit_zeit
            assert (m.uid, m.gid, m.uname, m.gname) == (0, 0, "", "")
            assert m.isdir() or m.isfile()
            if m.isdir():
                assert m.mode == 0o755
        modi = {m.name: m.mode for m in mitglieder if m.isfile()}
        if a.name == "server":
            assert modi["deploy/platform/einrichten.sh"] == 0o755
            assert modi["deploy/platform/scanview.caddy"] == 0o644
        # gzip-Kopf: mtime 0, kein Dateiname.
        kopf = a.pfad.read_bytes()[:10]
        assert kopf[4:8] == b"\x00\x00\x00\x00"
        assert kopf[3] & 0x08 == 0


def test_lieferumfang_entspricht_tar_inhalt(repo: Path, tmp_path: Path) -> None:
    for a in baue_artefakte(repo, "0.1.0", tmp_path / "z", bauen=_test_bau).artefakte:
        erwartet: list[Datei] = []
        with tarfile.open(a.pfad, "r:gz") as tar:
            for m in tar.getmembers():
                if m.isfile():
                    f = tar.extractfile(m)
                    assert f is not None
                    inhalt = f.read()
                    erwartet.append(Datei(m.name, hashlib.sha512(inhalt).hexdigest(),
                                          len(inhalt)))
        assert a.lieferumfang == erwartet


def test_langer_pfad_und_umlaut_lassen_sich_entpacken(repo: Path, tmp_path: Path) -> None:
    alle = _nach_name(baue_artefakte(repo, "0.1.0", tmp_path / "z", bauen=_test_bau))
    ziel = tmp_path / "entpackt"
    for name in ("server", "import"):
        with tarfile.open(alle[name].pfad, "r:gz") as tar:
            tar.extractall(ziel, filter="data")
    assert (ziel / LANGER_PFAD).read_text(encoding="utf-8") == "x = 1\n"
    assert (ziel / KOMMA_PFAD).read_text(encoding="utf-8") == "{}\n"
    assert (ziel / UMLAUT_PFAD).read_text(encoding="utf-8") == "pass\n"


def test_gzip_ist_gueltig(repo: Path, tmp_path: Path) -> None:
    for a in baue_artefakte(repo, "0.1.0", tmp_path / "z", bauen=_test_bau).artefakte:
        with gzip.open(a.pfad) as f:
            assert f.read()


# --- fail-closed --------------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["", "0.1/0", "..", " 0.1.0", "0.1.0\n"])
def test_ungueltige_version(repo: Path, tmp_path: Path, version: str) -> None:
    with pytest.raises(NachweisFehler) as e:
        baue_artefakte(repo, version, tmp_path / "z", bauen=_test_bau)
    assert e.value.regel == "artefakt-version"


def test_vorhandenes_artefakt_wird_nicht_ueberschrieben(repo: Path, tmp_path: Path) -> None:
    ziel = tmp_path / "z"
    ziel.mkdir()
    (ziel / "scanview-server-0.1.0.tar.gz").write_bytes(b"alt")
    with pytest.raises(NachweisFehler) as e:
        baue_artefakte(repo, "0.1.0", ziel, bauen=_test_bau)
    assert e.value.regel == "artefakt-ziel"
    assert (ziel / "scanview-server-0.1.0.tar.gz").read_bytes() == b"alt"


def test_geaenderte_versionierte_datei_bricht_ab(repo: Path, tmp_path: Path) -> None:
    (repo / "server/uv.lock").write_text("manipuliert\n", encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        baue_artefakte(repo, "0.1.0", tmp_path / "z", bauen=_test_bau)
    assert e.value.regel == "artefakt-worktree"


def test_bau_der_versionierte_datei_aendert_bricht_ab(repo: Path, tmp_path: Path) -> None:
    def bau(worktree: Path) -> list[Netzschritt]:
        schritte = _test_bau(worktree)
        (worktree / "server/uv.lock").write_text("vom Bau verändert\n", encoding="utf-8")
        return schritte

    with pytest.raises(NachweisFehler) as e:
        baue_artefakte(repo, "0.1.0", tmp_path / "z", bauen=bau)
    assert e.value.regel == "artefakt-worktree"


def test_vorhandenes_dist_vor_dem_bau_bricht_ab(repo: Path, tmp_path: Path) -> None:
    # Nur frisches Bau-Ergebnis darf ins Artefakt, kein Rest aus einem früheren Lauf.
    _schreibe(repo, {"web/dist/alt.js": "veraltet"})
    with pytest.raises(NachweisFehler) as e:
        baue_artefakte(repo, "0.1.0", tmp_path / "z", bauen=_test_bau)
    assert e.value.regel == "artefakt-worktree"


def test_fehlendes_dist_bricht_ab(repo: Path, tmp_path: Path) -> None:
    with pytest.raises(NachweisFehler) as e:
        baue_artefakte(repo, "0.1.0", tmp_path / "z", bauen=lambda _w: [])
    assert e.value.regel == "artefakt-inhalt"


def test_fehlendes_lockfile_bricht_ab(repo: Path, tmp_path: Path) -> None:
    _git(repo, "rm", "-q", "mcp-server/uv.lock")
    _git(repo, "commit", "-q", "-m", "ohne lock")
    with pytest.raises(NachweisFehler) as e:
        baue_artefakte(repo, "0.1.0", tmp_path / "z", bauen=_test_bau)
    assert e.value.regel == "artefakt-inhalt"
    assert "mcp-server/uv.lock" in e.value.text


def test_symlink_in_dist_bricht_ab(repo: Path, tmp_path: Path) -> None:
    def bau(worktree: Path) -> list[Netzschritt]:
        schritte = _test_bau(worktree)
        (worktree / "web/dist/geheim").symlink_to("/etc/passwd")
        return schritte

    with pytest.raises(NachweisFehler) as e:
        baue_artefakte(repo, "0.1.0", tmp_path / "z", bauen=bau)
    assert e.value.regel == "artefakt-inhalt"


def test_versionierter_symlink_bricht_ab(repo: Path, tmp_path: Path) -> None:
    (repo / "server/verweis").symlink_to("uv.lock")
    _git(repo, "add", "server/verweis")
    _git(repo, "commit", "-q", "-m", "symlink")
    with pytest.raises(NachweisFehler) as e:
        baue_artefakte(repo, "0.1.0", tmp_path / "z", bauen=_test_bau)
    assert e.value.regel == "artefakt-inhalt"


# --- Standard-Webbau ----------------------------------------------------------------------------


def test_web_bauen_befehle_und_netzschritte(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "package.json").write_text(json.dumps(
        {"allowScripts": {"erlaubt": True, "verboten": False}}), encoding="utf-8")
    aufrufe: list[tuple[list[str], Path | None]] = []

    def falsch(befehl: list[str], regel: str, *, cwd: Path | None = None,
               umgebung: dict[str, str] | None = None) -> object:
        assert regel == "artefakt-bau"
        # Nie die Aufruferumgebung: immer die explizite Bau-Umgebung.
        assert umgebung is not None and "HOME" in umgebung
        aufrufe.append((befehl, cwd))
        return None

    monkeypatch.setattr(artefakte, "ausfuehren", falsch)
    schritte = web_bauen(tmp_path)
    assert aufrufe == [
        (["npm", "ci", "--ignore-scripts"], web),
        (["npm", "rebuild", "erlaubt", "--ignore-scripts=false"], web),
        (["npm", "run", "build"], web),
    ]
    assert [(s.befehl, s.cwd) for s in schritte] == [
        (["npm", "ci", "--ignore-scripts"], "web"),
        (["npm", "rebuild", "erlaubt", "--ignore-scripts=false"], "web"),
    ]
    for s in schritte:
        assert s.zeit.endswith("+00:00")
        assert str(tmp_path) not in " ".join(s.befehl) + s.cwd


def test_web_bauen_ungueltige_allowscripts(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "package.json").write_text(json.dumps({"allowScripts": {"x": "ja"}}),
                                      encoding="utf-8")
    monkeypatch.setattr(artefakte, "ausfuehren", lambda *a, **k: None)
    with pytest.raises(NachweisFehler) as e:
        web_bauen(tmp_path)
    assert e.value.regel == "artefakt-bau"


def test_web_bauen_ohne_web(tmp_path: Path) -> None:
    with pytest.raises(NachweisFehler) as e:
        web_bauen(tmp_path)
    assert e.value.regel == "artefakt-bau"


def test_web_bauen_ohne_aufruferumgebung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`VITE_STANDARDPAKET` & Co. aus der Aufruferumgebung dürfen
    den ausgelieferten Web-Bau nicht erreichen — echter Lauf mit einem npm-Stub, der seine
    Umgebung protokolliert."""
    stub = tmp_path / "bin"
    stub.mkdir()
    protokoll = tmp_path / "umgebungen.txt"
    (stub / "npm").write_text(f"#!/bin/sh\nenv >>{protokoll}\necho --- >>{protokoll}\n",
                              encoding="utf-8")
    (stub / "npm").chmod(0o755)
    web = tmp_path / "wt" / "web"
    web.mkdir(parents=True)
    (web / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PATH", f"{stub}:{os.environ['PATH']}")
    eingeschleust = {"VITE_STANDARDPAKET": "privat-kennung", "NODE_OPTIONS": "--require=/x.js",
                     "GIT_DIR": "/nirgends", "npm_config_registry": "http://fremd",
                     "GEHEIM_TOKEN": "sv_x"}
    for name, wert in eingeschleust.items():
        monkeypatch.setenv(name, wert)
    web_bauen(tmp_path / "wt")
    laeufe = protokoll.read_text(encoding="utf-8").split("---\n")[:-1]
    assert len(laeufe) == 2  # npm ci, npm run build
    for lauf in laeufe:
        namen = {z.split("=", 1)[0] for z in lauf.splitlines() if "=" in z}
        assert not namen & set(eingeschleust), namen
        assert f"HOME={Path.home()}" not in lauf.splitlines()


# --- Öffentliche Dateiauswahl -----------------------------------------------------------------

def test_ausgelieferte_artefakte_nur_mit_oeffentlichen_dateien(repo: Path, tmp_path: Path) -> None:
    """Server und MCP enthalten nur, was die Positivliste des öffentlichen Exports aufnimmt: das
    versionierte Betriebs-README (Hosts, Projektkennungen) wird nicht ausgeliefert. Der Import
    (nicht ausgeliefert, nicht öffentlich) bleibt vollständig."""
    a = _nach_name(baue_artefakte(repo, "0.1.0", tmp_path / "ziel", bauen=_test_bau))
    server = _namen(a["server"].pfad)
    assert "deploy/platform/README.md" not in server
    assert "deploy/platform/einrichten.sh" in server
    assert "server/uv.lock" in server
    assert "import/uv.lock" in _namen(a["import"].pfad)


def test_positivliste_fehlt_bricht_ab(repo: Path, tmp_path: Path) -> None:
    _git(repo, "rm", "-q", "veroeffentlichung/positivliste.txt")
    _git(repo, "commit", "-q", "-m", "ohne Positivliste")
    with pytest.raises(NachweisFehler) as e:
        baue_artefakte(repo, "0.1.0", tmp_path / "ziel", bauen=_test_bau)
    assert e.value.regel == "artefakt-inhalt"
    assert "positivliste" in e.value.text


def test_pflichtdatei_nicht_oeffentlich_bricht_ab(repo: Path, tmp_path: Path) -> None:
    _schreibe(repo, {"veroeffentlichung/positivliste.txt": POSITIVLISTE + "!server/uv.lock\n"})
    _git(repo, "commit", "-q", "-am", "uv.lock privat")
    with pytest.raises(NachweisFehler) as e:
        baue_artefakte(repo, "0.1.0", tmp_path / "ziel", bauen=_test_bau)
    assert e.value.regel == "artefakt-inhalt"
    assert "server/uv.lock" in e.value.text


# --- npm-Registry-Signaturen (CRA Art. 13 Abs. 5) ------------------------------------------------
#
# Die drei npm-Fixtures (`tests/fixtures/npm-signaturen/`) sind nicht von der echten Registry
# beobachtet: `gut.json` entspricht der echten Probe (2026-09-28, npm 12.0.2, siehe
# docs/compliance/werkzeuge.md); `ungueltig.json`/`fehlend.json` sind aus dem Feldkatalog von
# `verify-signatures.js` (npm-Quelltext) nachgebaut, weil sich eine ungültige bzw. fehlende
# Registry-Signatur nicht gefahrlos real erzeugen lässt.

def _npm_json(name: str) -> str:
    return (NPM_FIXTURES / f"{name}.json").read_text(encoding="utf-8")


def test_werte_npm_signaturen_aus_gut() -> None:
    ergebnis = werte_npm_signaturen_aus(_npm_json("gut"), installiert=frozenset(), ausnahmen={})
    assert ergebnis == {"abdeckung": 0, "geprueft": [], "ungueltig": 0, "fehlend": 0,
                        "ausnahmen": {}}


def test_werte_npm_signaturen_aus_ungueltig_bricht_immer_ab() -> None:
    installiert = frozenset({"node_modules/boese-paket"})
    with pytest.raises(NachweisFehler) as e:
        werte_npm_signaturen_aus(_npm_json("ungueltig"), installiert=installiert, ausnahmen={
            "boese-paket@1.2.3": "Begründung mit mindestens zwanzig Zeichen Länge"})
    assert e.value.regel == "npm-signaturen"
    assert "boese-paket@1.2.3" in e.value.text


def test_werte_npm_signaturen_aus_fehlend_ohne_ausnahme_bricht_ab() -> None:
    installiert = frozenset({"node_modules/altes-paket"})
    with pytest.raises(NachweisFehler) as e:
        werte_npm_signaturen_aus(_npm_json("fehlend"), installiert=installiert, ausnahmen={})
    assert e.value.regel == "npm-signaturen"
    assert "altes-paket@0.9.0" in e.value.text


def test_werte_npm_signaturen_aus_fehlend_mit_ausnahme_besteht() -> None:
    installiert = frozenset({"node_modules/altes-paket"})
    ergebnis = werte_npm_signaturen_aus(_npm_json("fehlend"), installiert=installiert, ausnahmen={
        "altes-paket@0.9.0": "Kein Registry-Schlüssel für dieses alte Release verfügbar"})
    assert ergebnis["fehlend"] == 1
    assert ergebnis["ausnahmen"] == {
        "altes-paket@0.9.0": "Kein Registry-Schlüssel für dieses alte Release verfügbar"}


def test_werte_npm_signaturen_aus_fremdes_paket_bricht_ab() -> None:
    """Ein gemeldetes Paket, das nicht zu den installierten Registry-Paketen gehört, ist
    verdächtig (falscher Baum/Lockfile) — kein Vertrauensbeweis für den Rest."""
    with pytest.raises(NachweisFehler) as e:
        werte_npm_signaturen_aus(_npm_json("fehlend"), installiert=frozenset(), ausnahmen={})
    assert e.value.regel == "npm-signaturen"
    assert "node_modules/altes-paket" in e.value.text


@pytest.mark.parametrize("ausgabe", ["kein json", "[]", '{"invalid": []}',
                                     '{"invalid": {}, "missing": []}',
                                     '{"invalid": [], "missing": [], "x": 1}'])
def test_werte_npm_signaturen_aus_muell_bricht_ab(ausgabe: str) -> None:
    with pytest.raises(NachweisFehler) as e:
        werte_npm_signaturen_aus(ausgabe, installiert=frozenset(), ausnahmen={})
    assert e.value.regel == "npm-signaturen"


def test_werte_npm_signaturen_aus_eintrag_ohne_pflichtfelder_bricht_ab() -> None:
    ausgabe = json.dumps({"invalid": [{"name": "x"}], "missing": []})
    with pytest.raises(NachweisFehler) as e:
        werte_npm_signaturen_aus(ausgabe, installiert=frozenset({"node_modules/x"}), ausnahmen={})
    assert e.value.regel == "npm-signaturen"


# --- Abdeckung: installierte Registry-Pakete -----------------------------------------------------

def _lock(pakete: dict[str, dict[str, object]]) -> str:
    return json.dumps({"packages": {"": {}, **pakete}})


def test_installierte_registry_pakete(tmp_path: Path) -> None:
    web = tmp_path / "web"
    (web / "node_modules" / "a").mkdir(parents=True)
    lock = _lock({
        "node_modules/a": {"resolved": "https://registry.npmjs.org/a/-/a-1.0.0.tgz"},
        "node_modules/lokal": {"resolved": "file:../lokal", "link": True},
        "node_modules/fremd-registry": {"resolved": "https://andere.registry.invalid/x.tgz"},
    })
    assert installierte_registry_pakete(web, lock) == frozenset({"node_modules/a"})


def test_installierte_registry_pakete_optional_darf_fehlen(tmp_path: Path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    lock = _lock({
        "node_modules/a": {"resolved": "https://registry.npmjs.org/a/-/a-1.0.0.tgz"},
        "node_modules/@esbuild/linux-x64": {
            "resolved": "https://registry.npmjs.org/@esbuild/linux-x64/-/linux-x64-1.0.0.tgz",
            "optional": True},
    })
    (web / "node_modules" / "a").mkdir(parents=True)
    assert installierte_registry_pakete(web, lock) == frozenset({"node_modules/a"})


def test_installierte_registry_pakete_gebuendelt_wie_normales_paket(tmp_path: Path) -> None:
    """`inBundle` (das Paket kommt im Tarball des Elternpakets mit, statt separat geholt zu
    werden) ändert laut npm-Quelltext (`@npmcli/arborist/lib/shrinkwrap.js`: `meta.resolved` wird
    unabhängig von `lock.bundled`/`inBundle` gesetzt) nichts an `resolved` — unsere Prüfung
    behandelt ein gebündeltes Registry-Paket deshalb wie jedes andere: verlangt, geprüft."""
    web = tmp_path / "web"
    (web / "node_modules" / "eltern" / "node_modules" / "gebuendelt").mkdir(parents=True)
    lock = _lock({
        "node_modules/eltern/node_modules/gebuendelt": {
            "resolved": "https://registry.npmjs.org/gebuendelt/-/gebuendelt-1.0.0.tgz",
            "inBundle": True,
        },
    })
    assert installierte_registry_pakete(web, lock) == frozenset(
        {"node_modules/eltern/node_modules/gebuendelt"})


def test_installierte_registry_pakete_weniger_als_gelockt_bricht_ab(tmp_path: Path) -> None:
    """Eine nicht-optionale Registry-Abhängigkeit des Lockfiles fehlt auf der Platte — ein
    unvollständiger `npm ci`-Lauf, fail-closed statt stillschweigend weniger zu prüfen."""
    web = tmp_path / "web"
    web.mkdir()
    lock = _lock({
        "node_modules/a": {"resolved": "https://registry.npmjs.org/a/-/a-1.0.0.tgz"},
        "node_modules/b": {"resolved": "https://registry.npmjs.org/b/-/b-1.0.0.tgz"},
    })
    (web / "node_modules" / "a").mkdir(parents=True)
    with pytest.raises(NachweisFehler) as e:
        installierte_registry_pakete(web, lock)
    assert e.value.regel == "npm-signaturen"
    assert "node_modules/b" in e.value.text


def test_installierte_registry_pakete_keine_bricht_ab(tmp_path: Path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    with pytest.raises(NachweisFehler) as e:
        installierte_registry_pakete(web, _lock({}))
    assert e.value.regel == "npm-signaturen"


@pytest.mark.parametrize("lock", ["kein json", "[]", '{"packages": []}'])
def test_installierte_registry_pakete_ungueltiges_lockfile_bricht_ab(tmp_path: Path,
                                                                     lock: str) -> None:
    with pytest.raises(NachweisFehler) as e:
        installierte_registry_pakete(tmp_path / "web", lock)
    assert e.value.regel == "npm-signaturen"


# --- npm_signaturen: Aufruf + Auswertung ----------------------------------------------------------

def test_npm_signaturen_ruft_auswertung_mit_installierten_paketen_auf(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    web = tmp_path / "web"
    (web / "node_modules" / "a").mkdir(parents=True)
    lock = web / "package-lock.json"
    lock.write_text(_lock({
        "node_modules/a": {"resolved": "https://registry.npmjs.org/a/-/a-1.0.0.tgz"}}),
        encoding="utf-8")
    aufrufe: list[tuple[Path, dict[str, str]]] = []

    def falsch(w: Path, umgebung: dict[str, str]) -> str:
        aufrufe.append((w, umgebung))
        return _npm_json("gut")
    monkeypatch.setattr(artefakte, "_npm_audit_signaturen_json", falsch)
    ergebnis = npm_signaturen(web, lock, {})
    assert ergebnis["abdeckung"] == 1
    assert aufrufe[0][0] == web
    assert "HOME" in aufrufe[0][1]  # explizite Bau-Umgebung, nie die Aufruferumgebung


def test_npm_signaturen_lockfile_nicht_lesbar_bricht_ab(tmp_path: Path) -> None:
    with pytest.raises(NachweisFehler) as e:
        npm_signaturen(tmp_path / "web", tmp_path / "fehlt.json", {})
    assert e.value.regel == "npm-signaturen"


def test_npm_audit_signaturen_json_nicht_ausfuehrbar_bricht_ab(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Eigener Aufruf (nicht `werkzeuge.ausfuehren`) — der OSError-Zweig braucht einen
    eigenen Test, weil hier bewusst nicht der sonst übliche Weg verwendet wird."""
    def kaputt(*a: object, **k: object) -> None:
        raise OSError(2, "No such file or directory")
    monkeypatch.setattr(artefakte.subprocess, "run", kaputt)
    with pytest.raises(NachweisFehler) as e:
        artefakte._npm_audit_signaturen_json(tmp_path, {})
    assert e.value.regel == "npm-signaturen"
    assert "nicht ausführbar" in e.value.text


# --- Ausnahmen ------------------------------------------------------------------------------------

def test_lade_npm_ausnahmen(tmp_path: Path) -> None:
    pfad = tmp_path / "a.toml"
    pfad.write_text('[ausnahme]\n"altes-paket@0.9.0" = '
                    '"Kein Registry-Schlüssel für dieses alte Release verfügbar"\n',
                    encoding="utf-8")
    assert lade_npm_ausnahmen(pfad) == {
        "altes-paket@0.9.0": "Kein Registry-Schlüssel für dieses alte Release verfügbar"}


def test_lade_npm_ausnahmen_scoped_paket(tmp_path: Path) -> None:
    """Scoped npm-Pakete (`@scope/name@version`, zwei „@“) müssen am **letzten** „@“ in
    Paket und Version zerlegt werden — nicht nur unscoped Namen."""
    pfad = tmp_path / "a.toml"
    pfad.write_text(
        '[ausnahme]\n"@rollup/rollup-linux-x64-gnu@4.24.0" = '
        '"Kein Registry-Schlüssel für dieses Paket laut Probe verfügbar"\n',
        encoding="utf-8")
    assert lade_npm_ausnahmen(pfad) == {
        "@rollup/rollup-linux-x64-gnu@4.24.0":
            "Kein Registry-Schlüssel für dieses Paket laut Probe verfügbar"}


def test_leere_npm_ausnahmedatei(tmp_path: Path) -> None:
    pfad = tmp_path / "a.toml"
    pfad.write_text("# leer\n", encoding="utf-8")
    assert lade_npm_ausnahmen(pfad) == {}


_NPM_AUSNAHMEN = WURZEL / "docs" / "compliance" / "npm-signaturen-ausnahmen.toml"


@pytest.mark.skipif(not _NPM_AUSNAHMEN.exists(),
                    reason="npm-signaturen-ausnahmen.toml liegt nur im privaten Repo "
                           "(nicht im Export)")
def test_echte_npm_ausnahmedatei_ist_gueltig() -> None:
    lade_npm_ausnahmen(_NPM_AUSNAHMEN)


@pytest.mark.parametrize("inhalt", [
    '[ausnahme]\n"x" = "ausreichend lange Begründung hier, mehr als zwanzig Zeichen"\n',
    '[ausnahme]\n"x@1.0.0" = "zu kurz"\n',
    'sonstwas = 1\n',
    'kaputt = \n',
    # Auch am letzten „@“ zerlegt darf weder Paket- noch Versionsteil leer bleiben.
    '[ausnahme]\n"x@" = "ausreichend lange Begründung hier, mehr als zwanzig Zeichen"\n',
    '[ausnahme]\n"@1.0.0" = "ausreichend lange Begründung hier, mehr als zwanzig Zeichen"\n',
])
def test_ungueltige_npm_ausnahmen(tmp_path: Path, inhalt: str) -> None:
    pfad = tmp_path / "a.toml"
    pfad.write_text(inhalt, encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        lade_npm_ausnahmen(pfad)
    assert e.value.regel == "npm-signaturen-ausnahmen"


def test_fehlende_npm_ausnahmedatei(tmp_path: Path) -> None:
    with pytest.raises(NachweisFehler) as e:
        lade_npm_ausnahmen(tmp_path / "fehlt.toml")
    assert e.value.regel == "npm-signaturen-ausnahmen"


# --- Einbindung in baue_artefakte -------------------------------------------------------------

def test_baue_artefakte_ohne_injizierten_bau_ruft_npm_signaturen(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nur der echte Webbau (kein injizierter `bauen`) löst die Pflichtprüfung aus; ihr Ergebnis
    landet in `Bauergebnis.npm_signaturen` und ihr Netzschritt in `netzschritte`."""
    def fake_web_bauen(worktree: Path) -> list[Netzschritt]:
        _schreibe(worktree, {"web/dist/index.html": "<html/>",
                             "web/node_modules/x/index.js": "nicht ausliefern"})
        return [Netzschritt(["npm", "ci", "--ignore-scripts"], "web",
                            "2026-09-28T00:00:00+00:00")]
    aufrufe: list[tuple[Path, Path, dict[str, str]]] = []

    def fake_npm_signaturen(web: Path, lock: Path,
                            ausnahmen: dict[str, str]) -> dict[str, object]:
        aufrufe.append((web, lock, ausnahmen))
        return {"abdeckung": 1, "geprueft": ["node_modules/x"], "ungueltig": 0, "fehlend": 0,
                "ausnahmen": {}}
    monkeypatch.setattr(artefakte, "web_bauen", fake_web_bauen)
    monkeypatch.setattr(artefakte, "npm_signaturen", fake_npm_signaturen)
    ergebnis = baue_artefakte(repo, "0.1.0", tmp_path / "ziel",
                              npm_ausnahmen={"x@1.0.0": "Testausnahme, lang genug begründet"})
    assert ergebnis.npm_signaturen == {"abdeckung": 1, "geprueft": ["node_modules/x"],
                                       "ungueltig": 0, "fehlend": 0, "ausnahmen": {}}
    assert aufrufe == [(repo / "web", repo / "web" / "package-lock.json",
                        {"x@1.0.0": "Testausnahme, lang genug begründet"})]
    assert ergebnis.netzschritte[0].befehl == ["npm", "ci", "--ignore-scripts"]
    letzter = ergebnis.netzschritte[-1]
    assert letzter.befehl == ["npm", "audit", "signatures", "--json"]
    assert letzter.cwd == "web"
    assert letzter.zeit.endswith("+00:00")


def test_baue_artefakte_mit_injiziertem_bau_hat_kein_npm_ergebnis(repo: Path,
                                                                  tmp_path: Path) -> None:
    ergebnis = baue_artefakte(repo, "0.1.0", tmp_path / "ziel", bauen=_test_bau)
    assert ergebnis.npm_signaturen is None
