"""Abschlussnachweis und Release-Ordner für den Host.

Ende-zu-Ende ohne Docker und ohne echten Schlüssel: echte `baue_artefakte`-Ausgabe aus einem
Mini-Repo, echtes Manifest (`baue_manifest`), echtes Archiv (`archiviere`), Abschluss,
`deploy_ordner` — und der Host-Prüfer `deploy/platform/release_pruefen.py` akzeptiert genau
diesen Ordner und entpackt ihn."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import signierhilfe
from pflicht import berichte_dateien, werkzeuge

from scanview_nachweise import abschluss, cli, manifest
from scanview_nachweise.archiv import archiviere
from scanview_nachweise.artefakte import Netzschritt, baue_artefakte
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.scan import DbStand

REPO_WURZEL = Path(__file__).resolve().parents[2]
HELFER = REPO_WURZEL / "deploy" / "platform" / "release_pruefen.py"
PYTHON = os.environ.get("SCANVIEW_TEST_PYTHON", sys.executable)
VERSION = "0.1.0"
ZEIT = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.strip()


def _init(repo: Path) -> None:
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "commit.gpgsign", "false")


@pytest.fixture(scope="module")
def schluessel(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    ordner = tmp_path_factory.mktemp("abschluss-schluessel")
    key = ordner / "k_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "t", "-f", str(key)],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True)
    typ, wert = key.with_suffix(".pub").read_text(encoding="utf-8").split()[:2]
    signers = ordner / "allowed_signers"
    signers.write_text(f'{manifest.KENNUNG} namespaces="{manifest.NAMESPACE},'
                       f'{abschluss.NAMESPACE}" {typ} {wert}\n', encoding="utf-8")
    return key, signers


def _quellrepo(wurzel: Path) -> Path:
    _init(wurzel)
    dateien = {
        "web/package.json": "{}",
        "server/pyproject.toml": "[project]\nname='s'\n", "server/uv.lock": "version = 1\n",
        "server/src/s/__init__.py": "",
        "deploy/platform/einrichten.sh": "#!/bin/sh\n",
        "deploy/platform/scanview-server.service": "[Unit]\n",
        "deploy/platform/README.md": "privat\n",
        "mcp-server/pyproject.toml": "[project]\nname='m'\n", "mcp-server/uv.lock": "v\n",
        "import/pyproject.toml": "[project]\nname='i'\n", "import/uv.lock": "v\n",
        "veroeffentlichung/positivliste.txt":
            "web/**\nserver/**\nmcp-server/**\ndeploy/platform/**\n!deploy/platform/README.md\n",
    }
    for relativ, inhalt in dateien.items():
        (wurzel / relativ).parent.mkdir(parents=True, exist_ok=True)
        (wurzel / relativ).write_text(inhalt, encoding="utf-8")
    (wurzel / "deploy/platform/einrichten.sh").chmod(0o755)
    _git(wurzel, "add", "-A")
    _git(wurzel, "commit", "-q", "-m", "init")
    return wurzel


def _web(worktree: Path) -> list[Netzschritt]:
    dist = worktree / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
    (dist / "portal.html").write_text("<!doctype html>\n", encoding="utf-8")
    (dist / "assets" / "a.js").write_text("1;\n", encoding="utf-8")
    return []


@pytest.fixture
def archiviert(tmp_path: Path, schluessel: tuple[Path, Path]) -> Path:
    """Archiv mit einem echt gebauten, signierten und archivierten Release 0.1.0."""
    key, signers = schluessel
    quelle = _quellrepo(tmp_path / "quelle")
    ausgabe = tmp_path / "ausgabe"
    bau = baue_artefakte(quelle, VERSION, ausgabe / "artefakte", bauen=_web)
    sbom = ausgabe / "sbom"
    sbom.mkdir()
    sboms = []
    for a in bau.artefakte:
        sboms.append(sbom / f"scanview-{a.name}-{VERSION}.cdx.json")
        sboms[-1].write_text(json.dumps({"artefakt": a.name}), encoding="utf-8")
    berichte = ausgabe / "berichte"
    bericht_pfade = berichte_dateien(berichte)
    m = manifest.baue_manifest(
        VERSION, _git(quelle, "rev-parse", "HEAD"), "e" * 40, bau.artefakte, sboms,
        bericht_pfade, werkzeuge(),
        DbStand(ZEIT, "c" * 64, "UpdatedAt", None),
        {n: "bestanden" for n in manifest.PFLICHT_PRUEFUNGEN},
        ausgeliefert=manifest.AUSGELIEFERT,
        umgebung={k: "x" for k in manifest.UMGEBUNG_PFLICHT}, veroeffentlichbar=True,
        erstellt=ZEIT)
    pfad = manifest.schreibe_manifest(m, ausgabe / "manifest.json")
    manifest.signiere(pfad, key)
    (ausgabe / "allowed_signers").write_bytes(signers.read_bytes())
    archiv = signierhilfe.signiertes_archiv(tmp_path / "archiv")
    archiviere(VERSION, [pfad, pfad.with_name("manifest.json.sig"), ausgabe / "allowed_signers",
                         ausgabe / "artefakte", sbom, berichte], archiv,
               allowed_signers=signers)
    return archiv


def _pruefer(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PYTHON, "-I", "-B", str(HELFER), *args], capture_output=True,
                          text=True, check=False, stdin=subprocess.DEVNULL)


def test_ende_zu_ende_archiv_abschluss_deploy_ordner_host(tmp_path, archiviert, schluessel):
    key, signers = schluessel
    ziel = abschluss.abschliessen(archiviert, VERSION, key, signers)
    assert ziel == archiviert / "abschluesse" / VERSION
    assert _git(archiviert, "status", "--porcelain") == ""
    ordner = abschluss.deploy_ordner(archiviert, VERSION, tmp_path / "deploy", signers)
    assert sorted(p.name for p in ordner.iterdir()) == [
        "abschluss.json", "abschluss.json.sig", "artefakte", "manifest.json",
        "manifest.json.sig"]
    assert sorted(p.name for p in (ordner / "artefakte").iterdir()) == [
        f"scanview-{n}-{VERSION}.tar.gz" for n in ("mcp", "server", "web")]
    # Der Host-Prüfer akzeptiert genau diesen Ordner — echte baue_artefakte-Ausgabe durch
    # _pruefe_tar — und entpackt web und server.
    r = _pruefer("pruefen", str(ordner), str(signers))
    assert r.returncode == 0, r.stderr
    r = _pruefer("entpacken", str(ordner), str(signers), str(tmp_path / "entpackt"))
    assert r.returncode == 0, r.stderr
    baum = tmp_path / "entpackt" / "baum"
    assert (baum / "web/dist/index.html").is_file()
    assert (baum / "deploy/platform/einrichten.sh").is_file()
    assert not (baum / "deploy/platform/README.md").exists()


def test_abschluss_signatur_nur_im_eigenen_namensraum(archiviert, schluessel):
    key, signers = schluessel
    ziel = abschluss.abschliessen(archiviert, VERSION, key, signers)
    roh = (ziel / "abschluss.json").read_bytes()
    sig = (ziel / "abschluss.json.sig").read_bytes()
    abschluss.verifiziere(roh, sig, signers)
    (ziel.parent / "p").write_bytes(sig)
    r = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(signers), "-I",
                        manifest.KENNUNG, "-n", manifest.NAMESPACE, "-s", str(ziel.parent / "p")],
                       input=roh, capture_output=True, check=False)
    assert r.returncode != 0


def test_kein_abschluss_ohne_archivierung(tmp_path, schluessel):
    key, signers = schluessel
    archiv = signierhilfe.signiertes_archiv(tmp_path / "archiv")
    with pytest.raises(NachweisFehler) as e:
        abschluss.abschliessen(archiv, VERSION, key, signers)
    assert e.value.regel == "abschluss" and "nicht committet" in e.value.text
    assert not (archiv / "abschluesse").exists()


def test_abschluss_nie_zweimal(archiviert, schluessel):
    key, signers = schluessel
    abschluss.abschliessen(archiviert, VERSION, key, signers)
    vorher = _git(archiviert, "rev-parse", "HEAD")
    with pytest.raises(NachweisFehler) as e:
        abschluss.abschliessen(archiviert, VERSION, key, signers)
    assert e.value.regel == "abschluss-unveraenderlich"
    assert _git(archiviert, "rev-parse", "HEAD") == vorher


def test_abschluss_prueft_archiviertes_manifest(archiviert, schluessel, tmp_path):
    """Liegt im committeten Archiv ein Manifest, das nicht (mehr) zur Signatur passt, gibt es
    keinen Abschluss."""
    key, signers = schluessel
    m = archiviert / "releases" / VERSION / "manifest.json"
    daten = json.loads(m.read_bytes())
    daten["erstellt"] = "2026-09-29T00:00:00+00:00"
    m.write_bytes(manifest.kanonisch(daten))
    _git(archiviert, "commit", "-q", "-S", "-am", "manipuliert")
    with pytest.raises(NachweisFehler) as e:
        abschluss.abschliessen(archiviert, VERSION, key, signers)
    assert e.value.regel == "signatur"
    assert not (archiviert / "abschluesse").exists()


def test_deploy_ordner_ohne_abschluss(archiviert, schluessel, tmp_path):
    with pytest.raises(NachweisFehler) as e:
        abschluss.deploy_ordner(archiviert, VERSION, tmp_path / "deploy", schluessel[1])
    assert e.value.regel == "abschluss"
    assert not (tmp_path / "deploy").exists()


def test_deploy_ordner_spaetere_aenderung_im_archiv(archiviert, schluessel, tmp_path):
    """Jede spätere Änderung unter releases/<v> (nur anhängen verletzt) passt nicht mehr zum
    Archiv-Commit im signierten Abschluss."""
    key, signers = schluessel
    abschluss.abschliessen(archiviert, VERSION, key, signers)
    tar = archiviert / "releases" / VERSION / "artefakte" / f"scanview-web-{VERSION}.tar.gz"
    tar.write_bytes(tar.read_bytes() + b"\0")
    _git(archiviert, "commit", "-q", "-S", "-am", "manipuliert")
    with pytest.raises(NachweisFehler) as e:
        abschluss.deploy_ordner(archiviert, VERSION, tmp_path / "deploy", signers)
    assert e.value.regel == "abschluss" and "Archiv-Commit" in e.value.text
    assert not (tmp_path / "deploy").exists()


def test_deploy_ordner_prueft_tarball_hash(archiviert, schluessel, tmp_path, monkeypatch):
    key, signers = schluessel
    abschluss.abschliessen(archiviert, VERSION, key, signers)
    original = abschluss._blob

    def blob(archiv: Path, rev: str, pfad: str) -> bytes:
        daten = original(archiv, rev, pfad)
        return daten + b"\0" if pfad.endswith(f"scanview-mcp-{VERSION}.tar.gz") else daten
    monkeypatch.setattr(abschluss, "_blob", blob)
    with pytest.raises(NachweisFehler) as e:
        abschluss.deploy_ordner(archiviert, VERSION, tmp_path / "deploy", signers)
    assert e.value.regel == "artefakt-hash"
    assert not (tmp_path / "deploy").exists()


def test_deploy_ordner_ziel_muss_neu_sein(archiviert, schluessel, tmp_path):
    key, signers = schluessel
    abschluss.abschliessen(archiviert, VERSION, key, signers)
    (tmp_path / "deploy").mkdir()
    with pytest.raises(NachweisFehler) as e:
        abschluss.deploy_ordner(archiviert, VERSION, tmp_path / "deploy", signers)
    assert e.value.regel == "deploy-ordner"


def test_cli_deploy_ordner(archiviert, schluessel, tmp_path, capsys):
    key, signers = schluessel
    abschluss.abschliessen(archiviert, VERSION, key, signers)
    code = cli.main(["deploy-ordner", "--version", VERSION, "--ziel", str(tmp_path / "d"),
                     "--archiv", str(archiviert), "--allowed-signers", str(signers)])
    assert code == 0
    assert (tmp_path / "d" / "abschluss.json").is_file()
    assert hashlib.sha256((tmp_path / "d" / "manifest.json").read_bytes()).hexdigest() == \
        json.loads((tmp_path / "d" / "abschluss.json").read_bytes())["manifest_sha256"]


# --- Nachholen prüft das ganze Archiv -------------------------------------------------------------

@pytest.mark.parametrize(("aktion", "datei"), [
    ("loeschen", "sbom/scanview-mcp-0.1.0.cdx.json"),
    ("aendern", "sbom/scanview-web-0.1.0.cdx.json"),
    ("loeschen", "berichte/scan-ergebnis.json"),
    ("aendern", "berichte/scan-ergebnis.json"),
    ("doppeln", "sbom/scanview-server-0.1.0.cdx.json"),
])
def test_kein_abschluss_ueber_unvollstaendiges_archiv(archiviert, schluessel, aktion, datei):
    """Jede Manifestreferenz (SBOMs, Berichte, Artefakte) wird gegen die Blobs des konkreten
    Archiv-Commits geprüft, bevor signiert wird — auch auf dem Wiederherstellungsweg."""
    key, signers = schluessel
    pfad = archiviert / "releases" / VERSION / datei
    if aktion == "loeschen":
        _git(archiviert, "rm", "-q", str(pfad))
    elif aktion == "aendern":
        pfad.write_text("verändert", encoding="utf-8")
        _git(archiviert, "add", str(pfad))
    else:
        kopie = archiviert / "releases" / VERSION / "berichte" / pfad.name
        kopie.write_text("zweite Fassung", encoding="utf-8")
        _git(archiviert, "add", str(kopie))
    _git(archiviert, "commit", "-q", "-S", "-m", "Archiv verändert")
    with pytest.raises(NachweisFehler) as e:
        abschluss.abschliessen(archiviert, VERSION, key, signers)
    assert e.value.regel == "archiv-nachweis" and Path(datei).name in e.value.text
    assert not (archiviert / "abschluesse").exists()


def test_artefakt_im_archiv_veraendert_kein_abschluss(archiviert, schluessel):
    key, signers = schluessel
    tar = archiviert / "releases" / VERSION / "artefakte" / f"scanview-import-{VERSION}.tar.gz"
    tar.write_bytes(tar.read_bytes() + b"\0")
    _git(archiviert, "commit", "-q", "-S", "-am", "Artefakt verändert")
    with pytest.raises(NachweisFehler) as e:
        abschluss.abschliessen(archiviert, VERSION, key, signers)
    assert e.value.regel == "archiv-nachweis"


def test_symlink_im_archiv_commit_kein_abschluss(archiviert, schluessel):
    """Wie der reguläre Archivpfad nur reguläre Blobs (100644/100755) — ein
    Symlink-Eintrag im Archiv-Commit bricht ab, statt als „blob“ zu gelten."""
    key, signers = schluessel
    ziel = archiviert / "releases" / VERSION / "berichte" / "zusatz.json"
    ziel.symlink_to("scan-ergebnis.json")
    _git(archiviert, "add", str(ziel))
    _git(archiviert, "commit", "-q", "-S", "-m", "Symlink")
    with pytest.raises(NachweisFehler) as e:
        abschluss.abschliessen(archiviert, VERSION, key, signers)
    assert e.value.regel == "archiv-nachweis" and "Symlink" in e.value.text


# --- Signierte Archiv-Commits (Review I6) ------------------------------------------------------

def test_abschluss_commit_signiert(archiviert, schluessel):
    key, signers = schluessel
    abschluss.abschliessen(archiviert, VERSION, key, signers)
    anker = archiviert / "vertrauensanker" / "commit-signers"
    assert signierhilfe.git(archiviert, "-c", f"gpg.ssh.allowedSignersFile={anker}", "log",
                            "-1", "--format=%G?") == "G"


def test_abschluss_ohne_signierschluessel_kein_commit(archiviert, schluessel):
    key, signers = schluessel
    signierhilfe.git(archiviert, "config", "user.signingkey", str(archiviert / "gibtsnicht"))
    kopf = signierhilfe.git(archiviert, "rev-parse", "HEAD")
    with pytest.raises(NachweisFehler):
        abschluss.abschliessen(archiviert, VERSION, key, signers)
    assert signierhilfe.git(archiviert, "rev-parse", "HEAD") == kopf
    assert not (archiviert / "abschluesse" / VERSION).exists()
    assert signierhilfe.git(archiviert, "status", "--porcelain") == ""


def test_abschluss_fremd_signiert_erkannt(archiviert, schluessel, tmp_path):
    key, signers = schluessel
    fremd, _ = signierhilfe.schluessel(tmp_path / "fremd")
    signierhilfe.git(archiviert, "config", "user.signingkey", str(fremd))
    kopf = signierhilfe.git(archiviert, "rev-parse", "HEAD")
    with pytest.raises(NachweisFehler, match="nicht im Vertrauensanker") as e:
        abschluss.abschliessen(archiviert, VERSION, key, signers)
    assert e.value.regel == "commit-signatur"
    assert signierhilfe.git(archiviert, "rev-parse", "HEAD") == kopf
    assert signierhilfe.git(archiviert, "status", "--porcelain") == ""
