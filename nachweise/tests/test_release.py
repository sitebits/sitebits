"""Release-Orchestrierung: feste Stufenreihenfolge, fail-closed — bricht
Stufe n ab, laufen die Stufen > n nicht und das Archiv bleibt unberührt; jede Stufe schreibt
ihren Bericht. Signiert wird nur mit Wegwerf-Schlüsseln in tmp_path."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pytest
import signierhilfe
from pflicht import werkzeuge

from scanview_nachweise import export, manifest, release, sast, scan
from scanview_nachweise.artefakte import Artefakt, Bauergebnis, Netzschritt
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.register import Kontrolle
from scanview_nachweise.sbom import SbomErgebnis
from scanview_nachweise.werkzeuge import Werkzeug

WURZEL = Path(__file__).resolve().parents[2]
UMGEBUNG = {"node": "v22", "npm": "10", "python": "3.12", "uv": "uv 0.11", "docker": "28",
            "architektur": "x86_64"}


def _git(repo: Path, *args: str, **env: str) -> str:
    umgebung = {**os.environ, **env} if env else None
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, env=umgebung).stdout.strip()


def _versionsdateien(ordner: Path, version: str) -> None:
    """Die Versionsquellen, die `stufe_einfrieren` prüft (versionen.TEILE)."""
    (ordner / "web").mkdir(exist_ok=True)
    (ordner / "web" / "package.json").write_text(
        json.dumps({"name": "scanview-web", "version": version}), encoding="utf-8")
    (ordner / "web" / "package-lock.json").write_text(json.dumps(
        {"name": "scanview-web", "version": version,
         "packages": {"": {"name": "scanview-web", "version": version}}}), encoding="utf-8")
    for teil in ("server", "mcp-server", "nachweise"):
        (ordner / teil).mkdir(exist_ok=True)
        (ordner / teil / "pyproject.toml").write_text(
            f'[project]\nname = "{teil}"\nversion = "{version}"\n', encoding="utf-8")
    cli = ordner / "nachweise" / "src" / "scanview_nachweise"
    cli.mkdir(parents=True, exist_ok=True)
    (cli / "cli.py").write_text(f'_VERSION = "{version}"\n', encoding="utf-8")


def _repo(ordner: Path) -> Path:
    ordner.mkdir(parents=True)
    _git(ordner, "init", "-q", "-b", "main")
    _git(ordner, "config", "user.name", "Test")
    _git(ordner, "config", "user.email", "test@example.invalid")
    _git(ordner, "config", "commit.gpgsign", "false")
    _git(ordner, "config", "tag.gpgsign", "false")
    (ordner / "a.txt").write_text("a\n", encoding="utf-8")
    _versionsdateien(ordner, "0.1.0")
    _git(ordner, "add", ".")
    _git(ordner, "commit", "-q", "-m", "eins")
    (ordner / "b.txt").write_text("b\n", encoding="utf-8")
    _git(ordner, "add", "b.txt")
    _git(ordner, "commit", "-q", "-m", "zwei")
    return ordner


def _schluessel(ordner: Path) -> tuple[Path, Path]:
    ordner.mkdir(parents=True)
    key = ordner / "wegwerf_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", str(key)],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True)
    typ, wert = key.with_suffix(".pub").read_text(encoding="utf-8").split()[:2]
    signers = ordner / "allowed_signers"
    signers.write_text(f'release@scanview namespaces="scanview-release,scanview-abschluss" '
                       f'{typ} {wert}\n', encoding="utf-8")
    return key, signers


# --- Orchestrierung mit Attrappen --------------------------------------------------------------

class _Attrappen:
    def __init__(self, fehler_bei: str | None = None) -> None:
        self.gelaufen: list[str] = []
        self.fehler_bei = fehler_bei

    def stufen(self) -> dict[str, release.Stufe]:
        def mach(name: str) -> release.Stufe:
            def stufe(lauf: release.Lauf) -> dict[str, Any]:
                self.gelaufen.append(name)
                if name == self.fehler_bei:
                    raise NachweisFehler(f"test-{name}", f"{name} rot")
                if name == "manifest":
                    lauf.manifest_pfad = lauf.arbeitsordner / "manifest.json"
                    lauf.manifest_pfad.write_text("{}", encoding="utf-8")
                return {"nr": len(self.gelaufen)}
            return stufe
        return {n: mach(n) for n in release.STUFEN}


def _aufruf(tmp_path: Path, attrappen: _Attrappen, *, trocken: bool = False,
            arbeitsordner: Path | None = None) -> Path:
    return release.release(
        tmp_path / "repo", "HEAD", "0.1.0", tmp_path / "archiv", tmp_path / "key",
        tmp_path / "export-repo", None, trocken=trocken,
        arbeitsordner=arbeitsordner or tmp_path / "arbeit", stufen=attrappen.stufen(),
        melde=lambda _z: None)


def test_stufenreihenfolge_fest():
    # Matrix vor Export: fehlende Nachweise halten den Lauf an, bevor irgendetwas
    # ins Export-Repo geschrieben wird.
    # SAST direkt nach dem Qualitätstor, vor jedem Bau: Funde halten den Lauf früh an.
    # Fuzzing und Egress nach dem Bau (brauchen web/dist und die Server-venv des Worktrees).
    assert release.STUFEN == ("einfrieren", "register", "qualitaetstor", "sast", "sbom",
                              "fuzzing", "egress", "scan", "negativkontrollen", "lizenzen",
                              "matrix", "export", "manifest", "archiv", "tag", "abschluss")


def test_alle_stufen_in_reihenfolge_mit_bericht(tmp_path):
    a = _Attrappen()
    pfad = _aufruf(tmp_path, a)
    assert a.gelaufen == list(release.STUFEN)
    assert pfad == tmp_path / "arbeit" / "manifest.json"
    for nr, name in enumerate(release.STUFEN, 1):
        bericht = json.loads((tmp_path / "arbeit" / "berichte" / f"{name}.json").read_text("utf-8"))
        assert bericht["stufe"] == name and bericht["ergebnis"] == "bestanden"
        assert bericht["nr"] == nr


@pytest.mark.parametrize("stufe", release.STUFEN)
def test_abbruch_in_stufe_n_stoppt_alle_folgenden(tmp_path, stufe):
    a = _Attrappen(fehler_bei=stufe)
    with pytest.raises(NachweisFehler) as e:
        _aufruf(tmp_path, a)
    assert e.value.regel == f"test-{stufe}"
    n = release.STUFEN.index(stufe)
    assert a.gelaufen == list(release.STUFEN[:n + 1])
    berichte = tmp_path / "arbeit" / "berichte"
    abbruch = json.loads((berichte / f"{stufe}.json").read_text("utf-8"))
    assert abbruch == {"stufe": stufe, "ergebnis": "abgebrochen", "regel": f"test-{stufe}",
                       "text": f"{stufe} rot"}
    for spaeter in release.STUFEN[n + 1:]:
        assert not (berichte / f"{spaeter}.json").exists()
    # Das Archiv schreibt allein die Stufe „archiv“: vor ihr gibt es keinen Zugriff darauf.
    assert ("archiv" in a.gelaufen) is (n >= release.STUFEN.index("archiv"))


def test_trockenlauf_ohne_archiv_und_tag(tmp_path):
    a = _Attrappen()
    _aufruf(tmp_path, a, trocken=True)
    assert a.gelaufen == [s for s in release.STUFEN if s not in ("archiv", "tag", "abschluss")]
    for name in ("archiv", "tag", "abschluss"):
        bericht = json.loads((tmp_path / "arbeit" / "berichte" / f"{name}.json").read_text())
        assert bericht["ergebnis"] == "übersprungen (Trockenlauf)"


def test_arbeitsordner_muss_frisch_sein(tmp_path):
    (tmp_path / "arbeit").mkdir()
    (tmp_path / "arbeit" / "alt.txt").write_text("x", encoding="utf-8")
    a = _Attrappen()
    with pytest.raises(NachweisFehler) as e:
        _aufruf(tmp_path, a)
    assert e.value.regel == "release-arbeitsordner" and a.gelaufen == []


def test_unbekannte_ausnahme_wird_als_abbruch_berichtet(tmp_path):
    a = _Attrappen()
    stufen = a.stufen()

    def kaputt(lauf: release.Lauf) -> dict[str, Any]:
        raise OSError("Datenträger voll")
    stufen["sbom"] = kaputt
    with pytest.raises(NachweisFehler) as e:
        release.release(tmp_path / "repo", "HEAD", "0.1.0", tmp_path / "archiv",
                        tmp_path / "key", tmp_path / "x", None, arbeitsordner=tmp_path / "arbeit",
                        stufen=stufen, melde=lambda _z: None)
    assert e.value.regel == "release-stufe" and "Datenträger voll" in e.value.text
    bericht = json.loads((tmp_path / "arbeit" / "berichte" / "sbom.json").read_text("utf-8"))
    assert bericht["ergebnis"] == "abgebrochen"


# --- Stufe 1: einfrieren -----------------------------------------------------------------------

@pytest.fixture
def lauf(tmp_path: Path) -> release.Lauf:
    repo = _repo(tmp_path / "repo")
    archiv = signierhilfe.signiertes_archiv(tmp_path / "archiv")
    return release.Lauf(repo=repo, commit="HEAD", version="0.1.0", archiv=archiv,
                        schluessel=tmp_path / "key", export_ziel=tmp_path / "export-repo",
                        bestaetigt=None, trocken=False, arbeitsordner=tmp_path / "arbeit",
                        cache=tmp_path / "cache", melde=lambda _z: None)


@pytest.fixture
def ohne_abhaengigkeiten(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "_abhaengigkeiten", lambda lauf: [])
    monkeypatch.setattr(release, "lade_werkzeuge", lambda pfad: {})
    monkeypatch.setattr(release.sast, "bandit_bereitstellen", lambda nachweise, werkzeuge: [])
    monkeypatch.setattr(release.fuzzing, "schemathesis_bereitstellen",
                        lambda nachweise, werkzeuge: [])
    # Die Commit-Signatur prüfen eigene Tests mit signierten Repos (unten).
    monkeypatch.setattr(release, "_commit_signatur",
                        lambda lauf: {"fingerprint": "SHA256:attrappe", "commits": 1,
                                      "stichtag": lauf.voll})


def _key(lauf: release.Lauf) -> None:
    lauf.schluessel.write_text("KEY", encoding="utf-8")


def test_einfrieren_prueft_einheitliche_version(lauf, ohne_abhaengigkeiten):
    _key(lauf)
    lauf.arbeitsordner.mkdir()
    _versionsdateien(lauf.repo, "0.1.0")
    (lauf.repo / "server" / "pyproject.toml").write_text(
        '[project]\nname = "server"\nversion = "0.2.0"\n', encoding="utf-8")
    _git(lauf.repo, "commit", "-q", "-am", "abweichend")
    try:
        with pytest.raises(NachweisFehler) as e:
            release.stufe_einfrieren(lauf)
        assert e.value.regel == "versionen" and "server/pyproject.toml = 0.2.0" in e.value.text
    finally:
        release.worktree_entfernen(lauf)


def test_einfrieren_version_muss_zur_angeforderten_passen(lauf, ohne_abhaengigkeiten):
    _key(lauf)
    lauf.arbeitsordner.mkdir()
    lauf.version = "0.2.0"
    try:
        with pytest.raises(NachweisFehler) as e:
            release.stufe_einfrieren(lauf)
        assert e.value.regel == "release-version"
        assert "0.1.0" in e.value.text and "0.2.0" in e.value.text
    finally:
        release.worktree_entfernen(lauf)


def test_einfrieren_stellt_bandit_vor_dem_qualitaetstor_bereit(lauf, ohne_abhaengigkeiten,
                                                               monkeypatch):
    """Bandit wird schon beim Einfrieren installiert (Netzschritt im Manifest): die Docker-Tests
    des Qualitätstors bräuchten es sonst und installierten es unprotokolliert."""
    aufrufe: list[Path] = []
    schritt = Netzschritt(["uv", "sync", "--locked"], "nachweise/werkzeuge/bandit",
                          "2026-09-29T00:00:00+00:00")

    def bereitstellen(nachweise: Path, werkzeuge: Any, **kw: Any) -> list[Netzschritt]:
        aufrufe.append(nachweise)
        return [schritt]

    monkeypatch.setattr(release.sast, "bandit_bereitstellen", bereitstellen)
    _key(lauf)
    lauf.arbeitsordner.mkdir()
    try:
        release.stufe_einfrieren(lauf)
        assert aufrufe == [lauf.worktree / "nachweise"]
        assert schritt in lauf.netzschritte
    finally:
        release.worktree_entfernen(lauf)


def test_einfrieren_stellt_schemathesis_vor_dem_qualitaetstor_bereit(lauf, ohne_abhaengigkeiten,
                                                                     monkeypatch):
    """Schemathesis ebenso: Docker-Tests und die Negativkontrolle `fuzzing-serverfehler` laufen
    vor der Stufe `fuzzing` und installierten es sonst unprotokolliert."""
    aufrufe: list[Path] = []
    schritt = Netzschritt(["uv", "sync", "--locked"], "nachweise/werkzeuge/schemathesis",
                          "2026-09-29T00:00:00+00:00")

    def bereitstellen(nachweise: Path, werkzeuge: Any, **kw: Any) -> list[Netzschritt]:
        aufrufe.append(nachweise)
        return [schritt]

    monkeypatch.setattr(release.fuzzing, "schemathesis_bereitstellen", bereitstellen)
    _key(lauf)
    lauf.arbeitsordner.mkdir()
    try:
        release.stufe_einfrieren(lauf)
        assert aufrufe == [lauf.worktree / "nachweise"]
        assert schritt in lauf.netzschritte
    finally:
        release.worktree_entfernen(lauf)


def test_einfrieren_legt_frischen_worktree_mit_voller_historie_an(lauf, ohne_abhaengigkeiten):
    _key(lauf)
    lauf.arbeitsordner.mkdir()
    bericht = release.stufe_einfrieren(lauf)
    try:
        assert lauf.voll == _git(lauf.repo, "rev-parse", "HEAD")
        assert _git(lauf.worktree, "rev-parse", "HEAD") == lauf.voll
        assert _git(lauf.worktree, "rev-parse", "--is-shallow-repository") == "false"
        assert _git(lauf.worktree, "rev-list", "--count", "HEAD") == "2"
        assert bericht["commit"] == lauf.voll and bericht["zweige"] == ["main"]
    finally:
        release.worktree_entfernen(lauf)
    assert not lauf.worktree.exists()


@pytest.mark.parametrize("version", ["0.1", "0.1.0-probe", "v0.1.0", "0.1.0.1", "../0.1.0", ""])
def test_einfrieren_versionsregel(lauf, ohne_abhaengigkeiten, version):
    _key(lauf)
    lauf.version = version
    with pytest.raises(NachweisFehler) as e:
        release.stufe_einfrieren(lauf)
    assert e.value.regel == "release-version"


def test_einfrieren_version_schon_im_archiv(lauf, ohne_abhaengigkeiten):
    _key(lauf)
    (lauf.archiv / "releases" / "0.1.0").mkdir(parents=True)
    (lauf.archiv / "releases" / "0.1.0" / "manifest.json").write_text("{}", encoding="utf-8")
    _git(lauf.archiv, "add", "releases")
    _git(lauf.archiv, "commit", "-q", "-m", "0.1.0")
    with pytest.raises(NachweisFehler) as e:
        release.stufe_einfrieren(lauf)
    assert e.value.regel == "release-version" and "Archiv" in e.value.text


def test_einfrieren_commit_nicht_auf_release_zweig(lauf, ohne_abhaengigkeiten):
    _key(lauf)
    _git(lauf.repo, "checkout", "-q", "-b", "spielwiese")
    (lauf.repo / "c.txt").write_text("c\n", encoding="utf-8")
    _git(lauf.repo, "add", "c.txt")
    _git(lauf.repo, "commit", "-q", "-m", "nicht freigegeben")
    with pytest.raises(NachweisFehler) as e:
        release.stufe_einfrieren(lauf)
    assert e.value.regel == "release-commit"


def test_einfrieren_unbekannter_commit(lauf, ohne_abhaengigkeiten):
    _key(lauf)
    lauf.commit = "f" * 40
    with pytest.raises(NachweisFehler) as e:
        release.stufe_einfrieren(lauf)
    assert e.value.regel == "release-commit"


def test_einfrieren_tag_existiert_schon(lauf, ohne_abhaengigkeiten):
    _key(lauf)
    _git(lauf.repo, "tag", "v0.1.0")
    with pytest.raises(NachweisFehler) as e:
        release.stufe_einfrieren(lauf)
    assert e.value.regel == "release-tag"


def test_einfrieren_export_tag_existiert_schon(lauf, ohne_abhaengigkeiten):
    """Ein Tag v<version> im Export-Repo fiele sonst erst nach dem
    Archivieren auf (Version verbraucht)."""
    _key(lauf)
    _repo(lauf.export_ziel)
    _git(lauf.export_ziel, "tag", "v0.1.0")
    with pytest.raises(NachweisFehler) as e:
        release.stufe_einfrieren(lauf)
    assert e.value.regel == "release-tag" and "Export-Repo" in e.value.text
    assert not lauf.worktree_angelegt


def test_einfrieren_schluessel_fehlt(lauf, ohne_abhaengigkeiten):
    with pytest.raises(NachweisFehler) as e:
        release.stufe_einfrieren(lauf)
    assert e.value.regel == "release-schluessel"


# --- Stufe 1: Hooks beim Einfrieren aus, Worktree gegen den Baum geprüft --------------------------

def _hook(tmp_path: Path, repo: Path) -> Path:
    """post-checkout-Hook über core.hooksPath (Repo-Konfiguration): ändert eine Datei und
    versteckt die Änderung per assume-unchanged; Rückgabe: Marker-Datei."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    marker = tmp_path / "hook-lief"
    (hooks / "post-checkout").write_text(
        "#!/bin/sh\n"
        f"touch '{marker}'\n"
        "echo manipuliert > a.txt\n"
        "git update-index --assume-unchanged a.txt\n", encoding="utf-8")
    (hooks / "post-checkout").chmod(0o755)
    _git(repo, "config", "core.hooksPath", str(hooks))
    return marker


def test_einfrieren_fuehrt_keine_hooks_aus(lauf, ohne_abhaengigkeiten, tmp_path):
    _key(lauf)
    lauf.arbeitsordner.mkdir()
    marker = _hook(tmp_path, lauf.repo)
    try:
        release.stufe_einfrieren(lauf)
        assert (lauf.worktree / "a.txt").read_text(encoding="utf-8") == "a\n"
    finally:
        release.worktree_entfernen(lauf)
    assert not marker.exists()


@pytest.fixture
def worktree(lauf) -> Any:
    (lauf.repo / "x.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (lauf.repo / "x.sh").chmod(0o755)
    (lauf.repo / "link").symlink_to("a.txt")
    _git(lauf.repo, "add", "x.sh", "link")
    _git(lauf.repo, "commit", "-q", "-m", "drei")
    kopf = _git(lauf.repo, "rev-parse", "HEAD")
    wt = lauf.arbeitsordner / "wt"
    _git(lauf.repo, "worktree", "add", "-q", "--detach", str(wt), kopf)
    yield wt, kopf
    # Nach Manipulationen ist der Worktree nicht sauber: Ordner entfernen, Verwaltung aufräumen.
    shutil.rmtree(wt)
    _git(lauf.repo, "worktree", "prune")


def test_worktree_unveraendert_besteht(lauf, worktree):
    wt, kopf = worktree
    assert release.pruefe_worktree(lauf.repo, wt, kopf) == 10


@pytest.mark.parametrize("fall", ["inhalt-assume-unchanged", "skip-worktree", "zusatzdatei",
                                  "fehlt-skip-worktree", "ausfuehrbar", "symlink-ziel",
                                  "ignoriert"])
def test_worktree_manipulation_erkannt(lauf, worktree, fall):
    wt, kopf = worktree
    if fall == "inhalt-assume-unchanged":
        (wt / "a.txt").write_text("manipuliert\n", encoding="utf-8")
        _git(wt, "update-index", "--assume-unchanged", "a.txt")
    elif fall == "skip-worktree":
        _git(wt, "update-index", "--skip-worktree", "b.txt")
    elif fall == "zusatzdatei":
        (wt / "neu.txt").write_text("x", encoding="utf-8")
    elif fall == "fehlt-skip-worktree":
        _git(wt, "update-index", "--skip-worktree", "b.txt")
        (wt / "b.txt").unlink()
    elif fall == "ausfuehrbar":
        (wt / "x.sh").chmod(0o644)
    elif fall == "symlink-ziel":
        (wt / "link").unlink()
        (wt / "link").symlink_to("b.txt")
    else:
        (wt / ".gitignore").write_text("versteckt\n", encoding="utf-8")
        _git(wt, "update-index", "--assume-unchanged", "a.txt")
        (wt / "versteckt").write_text("x", encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        release.pruefe_worktree(lauf.repo, wt, kopf)
    assert e.value.regel == "release-worktree"


def test_einfrieren_verweigert_filtertreiber(lauf, ohne_abhaengigkeiten):
    _key(lauf)
    lauf.arbeitsordner.mkdir()
    _git(lauf.repo, "config", "filter.boese.smudge", "cat")
    with pytest.raises(NachweisFehler) as e:
        release.stufe_einfrieren(lauf)
    assert e.value.regel == "release-worktree" and "filter.boese.smudge" in e.value.text
    assert not lauf.worktree_angelegt and not lauf.worktree.exists()


def _include_nur_im_worktree(tmp_path: Path, repo: Path, inhalt: str) -> None:
    """Konfiguration, die nur im gitdir eines Worktrees gilt (includeIf gitdir:**/worktrees/**)."""
    datei = tmp_path / "nur-worktree.cfg"
    datei.write_text(inhalt, encoding="utf-8")
    _git(repo, "config", "includeIf.gitdir:**/worktrees/**.path", str(datei))


def test_einfrieren_filter_nur_im_worktree_kontext(lauf, ohne_abhaengigkeiten, tmp_path):
    """Filter plus core.attributesFile nur über includeIf im Worktree-gitdir —
    der Smudge-Befehl darf nie laufen, das Einfrieren bricht ab."""
    _key(lauf)
    lauf.arbeitsordner.mkdir()
    marker = tmp_path / "smudge-lief"
    attribute = tmp_path / "attribute"
    attribute.write_text("* filter=boese\n", encoding="utf-8")
    _include_nur_im_worktree(tmp_path, lauf.repo,
                             f"[filter \"boese\"]\n\tsmudge = sh -c 'touch {marker}; cat'\n"
                             f"[core]\n\tattributesFile = {attribute}\n")
    try:
        with pytest.raises(NachweisFehler) as e:
            release.stufe_einfrieren(lauf)
    finally:
        release.worktree_entfernen(lauf)
    assert e.value.regel == "release-worktree"
    assert not marker.exists()


def test_einfrieren_attributdatei_nur_im_worktree_kontext(lauf, ohne_abhaengigkeiten, tmp_path):
    """Attribute von außerhalb des Baums (core.attributesFile) werden verweigert."""
    _key(lauf)
    lauf.arbeitsordner.mkdir()
    attribute = tmp_path / "attribute"
    attribute.write_text("*.txt text eol=crlf\n", encoding="utf-8")
    _include_nur_im_worktree(tmp_path, lauf.repo, f"[core]\n\tattributesFile = {attribute}\n")
    try:
        with pytest.raises(NachweisFehler) as e:
            release.stufe_einfrieren(lauf)
    finally:
        release.worktree_entfernen(lauf)
    assert e.value.regel == "release-worktree" and "attributesFile" in e.value.text


def test_einfrieren_prueft_den_worktree(lauf, ohne_abhaengigkeiten, monkeypatch):
    """Der Aufruf von pruefe_worktree in stufe_einfrieren ist festgenagelt —
    ein Eingriff direkt nach dem Auschecken (ohne Hook) bricht die Stufe ab."""
    _key(lauf)
    lauf.arbeitsordner.mkdir()
    echt = release._git

    def mit_eingriff(repo: Path, *args: str, regel: str = "release-git") -> str:
        ergebnis = echt(repo, *args, regel=regel)
        if "checkout" in args:
            (lauf.worktree / "a.txt").write_text("eingriff\n", encoding="utf-8")
            echt(lauf.worktree, "update-index", "--assume-unchanged", "a.txt")
        return ergebnis
    monkeypatch.setattr(release, "_git", mit_eingriff)
    try:
        with pytest.raises(NachweisFehler) as e:
            release.stufe_einfrieren(lauf)
    finally:
        monkeypatch.setattr(release, "_git", echt)
        release.worktree_entfernen(lauf)
    assert e.value.regel == "release-worktree"


@pytest.mark.parametrize("fall", ["leerer-ordner", "leerer-unterordner", "unlesbarer-ordner",
                                  "gruppe-ausfuehrbar", "setuid"])
def test_worktree_ordner_und_rechte(lauf, worktree, fall):
    """Zusätzliche leere oder unlesbare Ordner, x-Bits für Gruppe/Andere
    und setuid an einer 100644-Datei."""
    wt, kopf = worktree
    if fall == "leerer-ordner":
        (wt / "leer").mkdir()
    elif fall == "leerer-unterordner":
        (wt / "web" / "neu" / "tiefer").mkdir(parents=True)
    elif fall == "unlesbarer-ordner":
        (wt / "versteckt").mkdir()
        (wt / "versteckt" / "x").write_text("x", encoding="utf-8")
        (wt / "versteckt").chmod(0o000)
    elif fall == "gruppe-ausfuehrbar":
        (wt / "a.txt").chmod(0o654)
    else:
        (wt / "a.txt").chmod(0o4644)
    try:
        with pytest.raises(NachweisFehler) as e:
            release.pruefe_worktree(lauf.repo, wt, kopf)
        assert e.value.regel == "release-worktree"
    finally:
        if (wt / "versteckt").exists():
            (wt / "versteckt").chmod(0o700)


# --- Kein Programm aus der Repo-Konfiguration, auch nicht beim Aufräumen --------------------------

def _marker_programm(tmp_path: Path, name: str) -> tuple[Path, Path]:
    marker = tmp_path / f"{name}-lief"
    programm = tmp_path / name
    programm.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n", encoding="utf-8")
    programm.chmod(0o755)
    return programm, marker


def test_fsmonitor_laeuft_nicht_beim_aufraeumen_nach_abbruch(lauf, tmp_path):
    """Nach gescheitertem Signatur-Gate räumt der finally-Block den
    Worktree auf (`worktree remove` ruft intern `status`) — ein konfigurierter fsmonitor läuft
    dabei nicht."""
    _key(lauf)
    lauf.arbeitsordner.mkdir()
    programm, marker = _marker_programm(tmp_path, "fsmonitor")
    _git(lauf.repo, "config", "core.fsmonitor", str(programm))
    try:
        with pytest.raises(NachweisFehler):
            release.stufe_einfrieren(lauf)
        assert lauf.worktree_angelegt
    finally:
        rest = release.worktree_entfernen(lauf)
    assert rest is None
    assert not marker.exists()


def test_signaturprogramm_im_archiv_laeuft_nicht_bei_der_versionsabfrage(lauf, tmp_path):
    """`log.showSignature=true` und ein eigenes `gpg.ssh.program` im Archiv;
    die Versionsabfrage findet einen signierten historischen Commit — das Programm läuft nicht."""
    _key(lauf)
    lauf.arbeitsordner.mkdir()
    a = lauf.archiv
    (a / "releases" / "0.1.0").mkdir(parents=True)
    (a / "releases" / "0.1.0" / "x").write_text("x", encoding="utf-8")
    signierhilfe.git(a, "add", "releases")
    signierhilfe.git(a, "commit", "-q", "-S", "-m", "alt")
    signierhilfe.git(a, "rm", "-q", "-r", "releases")
    signierhilfe.git(a, "commit", "-q", "-S", "-m", "entfernt")
    programm, marker = _marker_programm(tmp_path, "ssh-programm")
    signierhilfe.git(a, "config", "log.showSignature", "true")
    signierhilfe.git(a, "config", "gpg.ssh.program", str(programm))
    signierhilfe.git(a, "config", "gpg.ssh.allowedSignersFile",
                     str(a / "vertrauensanker" / "commit-signers"))
    with pytest.raises(NachweisFehler) as e:
        release.stufe_einfrieren(lauf)
    assert e.value.regel == "release-version"
    assert not marker.exists()


# --- Stufe 1: Commit-Signatur gegen den Vertrauensanker im Archiv -----------------------------

@pytest.fixture
def signierter_lauf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch
                    ) -> tuple[release.Lauf, str, list[str]]:
    """Produkt-Repo mit signierter Historie ab dem Stichtag, Archiv mit Vertrauensanker. Rückgabe
    (Lauf, Stichtag, Aufrufe von _abhaengigkeiten)."""
    key, zeile = signierhilfe.schluessel(tmp_path / "commit-schluessel")
    repo = signierhilfe.repo(tmp_path / "repo", key)
    signierhilfe.commit(repo, "alt", signiert=False)
    _versionsdateien(repo, "0.1.0")
    signierhilfe.git(repo, "add", ".")
    signierhilfe.git(repo, "commit", "-q", "-S", "-m", "stichtag")
    stichtag = signierhilfe.git(repo, "rev-parse", "HEAD")
    signierhilfe.commit(repo, "danach", signiert=True)
    archiv = signierhilfe.repo(tmp_path / "archiv", key)
    signierhilfe.commit(archiv, "start", signiert=True)
    signierhilfe.anker(archiv, zeile, stichtag)
    aufrufe: list[str] = []

    def abhaengigkeiten(lauf: release.Lauf) -> list[Netzschritt]:
        aufrufe.append(lauf.voll)
        return []
    monkeypatch.setattr(release, "_abhaengigkeiten", abhaengigkeiten)
    monkeypatch.setattr(release, "lade_werkzeuge", lambda pfad: {})
    monkeypatch.setattr(release.sast, "bandit_bereitstellen", lambda nachweise, werkzeuge: [])
    monkeypatch.setattr(release.fuzzing, "schemathesis_bereitstellen",
                        lambda nachweise, werkzeuge: [])
    lauf = release.Lauf(repo=repo, commit="HEAD", version="0.1.0", archiv=archiv,
                        schluessel=tmp_path / "key", export_ziel=tmp_path / "export-repo",
                        bestaetigt=None, trocken=False, arbeitsordner=tmp_path / "arbeit",
                        cache=tmp_path / "cache", melde=lambda _z: None)
    _key(lauf)
    lauf.arbeitsordner.mkdir()
    return lauf, stichtag, aufrufe


def _fingerprint(pub: Path) -> str:
    return subprocess.run(["ssh-keygen", "-l", "-f", str(pub)], check=True, capture_output=True,
                          text=True).stdout.split()[1]


def test_einfrieren_commit_signatur_bestanden(signierter_lauf, tmp_path):
    lauf, stichtag, aufrufe = signierter_lauf
    try:
        bericht = release.stufe_einfrieren(lauf)
    finally:
        release.worktree_entfernen(lauf)
    assert lauf.pruefungen["commit-signatur"] == "bestanden"
    fp = _fingerprint(tmp_path / "commit-schluessel" / "commit_ed25519.pub")
    signers = (lauf.archiv / "vertrauensanker" / "commit-signers").read_bytes()
    assert bericht["commit_signatur"] == {
        "fingerprint": fp, "commits": 2, "stichtag": stichtag, "altstaende": 0,
        "fingerprints": [fp],
        "anker_commit": signierhilfe.git(lauf.archiv, "rev-parse", "HEAD"),
        "signers_sha256": hashlib.sha256(signers).hexdigest()}
    assert aufrufe == [lauf.voll]


def test_einfrieren_anker_zweige_wie_release_zweige(signierter_lauf, tmp_path):
    """Die Release-Zweige im Anker und in der Kette laufen nicht auseinander."""
    lauf, stichtag, aufrufe = signierter_lauf
    # Ein neues Archiv, dessen Anker von Anfang an andere Zweige nennt (nachträglich ändern
    # verhindert schon die Ankerkette).
    key, zeile = signierhilfe.schluessel(tmp_path / "zweig-schluessel")
    archiv = signierhilfe.repo(tmp_path / "archiv-zweige", key)
    signierhilfe.anker(archiv, zeile, stichtag, zweige=("main",))
    lauf.archiv = archiv
    try:
        with pytest.raises(NachweisFehler) as e:
            release.stufe_einfrieren(lauf)
    finally:
        release.worktree_entfernen(lauf)
    assert e.value.regel == "commit-signatur-anker" and "zweige" in e.value.text
    assert aufrufe == []


def test_einfrieren_unsignierter_commit_nach_stichtag(signierter_lauf):
    lauf, _, aufrufe = signierter_lauf
    signierhilfe.commit(lauf.repo, "unsigniert", signiert=False)
    try:
        with pytest.raises(NachweisFehler) as e:
            release.stufe_einfrieren(lauf)
    finally:
        release.worktree_entfernen(lauf)
    assert e.value.regel == "commit-signatur" and "unsigniert" in e.value.text
    assert "commit-signatur" not in lauf.pruefungen
    # Kein Netzschritt für ein Release, das ohnehin scheitert.
    assert aufrufe == []


def test_einfrieren_ohne_anker(signierter_lauf):
    lauf, _, aufrufe = signierter_lauf
    signierhilfe.git(lauf.archiv, "rm", "-q", "-r", "vertrauensanker")
    signierhilfe.git(lauf.archiv, "commit", "-q", "-S", "-m", "weg")
    try:
        with pytest.raises(NachweisFehler) as e:
            release.stufe_einfrieren(lauf)
    finally:
        release.worktree_entfernen(lauf)
    assert e.value.regel == "commit-signatur-anker" and aufrufe == []


def test_einfrieren_trocken_ohne_archiv_scheitert_am_anker(signierter_lauf, tmp_path):
    """Auch ein Trockenlauf braucht die Prüfung (Pflichtprüfung des Manifests) — ohne Archiv
    bricht er am fehlenden Anker ab, statt die Signatur still auszulassen."""
    lauf, _, _ = signierter_lauf
    lauf.archiv = tmp_path / "gibtsnicht"
    lauf.trocken = True
    try:
        with pytest.raises(NachweisFehler) as e:
            release.stufe_einfrieren(lauf)
    finally:
        release.worktree_entfernen(lauf)
    assert e.value.regel == "commit-signatur-anker"


def test_einfrieren_liest_anker_nie_aus_dem_worktree(signierter_lauf, tmp_path):
    """Ein Commit, der seinen eigenen Vertrauensanker mitbringt und mit einem fremden Schlüssel
    signiert ist, besteht nicht: maßgeblich ist allein der Anker im Archiv."""
    lauf, _, _ = signierter_lauf
    fremd, fremd_zeile = signierhilfe.schluessel(tmp_path / "fremd")
    ordner = lauf.repo / "vertrauensanker"
    ordner.mkdir()
    (ordner / "commit-signers").write_text(fremd_zeile, encoding="utf-8")
    (ordner / "commit-signatur.toml").write_text(
        f'stichtag_commit = "{signierhilfe.git(lauf.repo, "rev-parse", "HEAD")}"\n'
        'zweige = ["main"]\n', encoding="utf-8")
    signierhilfe.git(lauf.repo, "add", "vertrauensanker")
    signierhilfe.git(lauf.repo, "-c", f"user.signingkey={fremd}", "commit", "-q", "-S",
                     "-m", "eigener Anker")
    try:
        with pytest.raises(NachweisFehler) as e:
            release.stufe_einfrieren(lauf)
    finally:
        release.worktree_entfernen(lauf)
    assert e.value.regel == "commit-signatur" and ": U " in e.value.text


# --- Stufe „register“ -----------------------------------------------------------------------------

def test_register_nur_import_befund_informativ_andere_blockieren(lauf, monkeypatch):
    from scanview_nachweise import register as reg
    from scanview_nachweise.register import Befund

    def befund(id_: str, artefakte: tuple[str, ...]) -> Befund:
        return Befund(id=id_, quelle="q", titel="t", schwere="hoch", kategorie="lieferkette",
                      status="offen", staende=("abc1234",), fix_commit=None, retest=None,
                      akzeptanz_begruendung=None, frist=None, entdeckt=date(2026, 9, 27),
                      artefakte=artefakte)
    befunde = [befund("B-IMG", ("import",))]
    monkeypatch.setattr(release.register, "lade_register",
                        lambda wt: reg.Register([], None, befunde, []))  # type: ignore[arg-type]
    monkeypatch.setattr(release.darstellung, "fassungen", lambda r: {})
    monkeypatch.setattr(release.gate, "negativtests_details", lambda wt, **kw: {})
    monkeypatch.setattr(release.gate, "retests_details",
                        lambda wt, b, **kw: release.gate.KategorieErgebnis(True, 0))
    bericht = release.stufe_register(lauf)
    assert lauf.pruefungen["befunde"] == "bestanden"
    assert bericht["befunde_nicht_ausgeliefert"] == [
        "B-IMG: hoch offen (betrifft nur import — nicht ausgeliefert, informativ)"]
    befunde.append(befund("B-ALLE", ()))
    with pytest.raises(NachweisFehler) as e:
        release.stufe_register(lauf)
    assert e.value.regel == "befund-gate" and "B-ALLE" in e.value.text
    assert "B-IMG" not in e.value.text


def test_register_retest_rot_blockiert(lauf, monkeypatch):
    """Ein Retest eines behobenen Befunds, der nicht läuft (z. B. übersprungen,
    weil ein Probe-Werkzeug fehlt), blockiert das Release."""
    from scanview_nachweise import register as reg
    monkeypatch.setattr(release.register, "lade_register",
                        lambda wt: reg.Register([], None, [], []))  # type: ignore[arg-type]
    monkeypatch.setattr(release.darstellung, "fassungen", lambda r: {})
    monkeypatch.setattr(release.gate, "negativtests_details", lambda wt, **kw: {})
    monkeypatch.setattr(release.gate, "retests_details", lambda wt, b, **kw:
                        release.gate.KategorieErgebnis(False, 3, "1 übersprungen/xfail"))
    with pytest.raises(NachweisFehler) as e:
        release.stufe_register(lauf)
    assert e.value.regel == "retest-gate" and "übersprungen" in e.value.text
    assert "retests" not in lauf.pruefungen
    monkeypatch.setattr(release.gate, "retests_details", lambda wt, b, **kw:
                        release.gate.KategorieErgebnis(True, 7))
    bericht = release.stufe_register(lauf)
    assert lauf.pruefungen["retests"] == "bestanden" and bericht["retests"]["ausgefuehrt"] == 7


def test_abhaengigkeiten_stellen_probe_werkzeuge_bereit(lauf, monkeypatch):
    """Die Freischalt-Probetests brauchen setfacl/getfacl (gitignoriert) — der
    eingefrorene Worktree bekommt sie als protokollierten Netzschritt, sonst liefen die Tests
    dort übersprungen."""
    aufrufe: list[tuple[list[str], Path | None]] = []
    monkeypatch.setattr(release, "_befehl",
                        lambda b, regel, cwd=None, **kw: aufrufe.append((b, cwd)) or "")
    lauf.worktree = lauf.arbeitsordner / "wt"
    schritte = release._abhaengigkeiten(lauf)
    probe = ["uv", "run", "--locked", "python", "-m", "scanview_nachweise.deploy_probe"]
    assert (probe, lauf.worktree / "nachweise") in aufrufe
    assert aufrufe.index((probe, lauf.worktree / "nachweise")) > aufrufe.index(
        (["uv", "sync", "--locked"], lauf.worktree / "nachweise"))
    assert any(s.befehl == probe and s.cwd == "nachweise" for s in schritte)


# --- Stufe „sbom“: npm-Registry-Signaturen -------------------------------------------------------

_NPM_SIGNATUREN_STANDARD = {"abdeckung": 1, "geprueft": ["node_modules/x"], "ungueltig": 0,
                           "fehlend": 0, "ausnahmen": {}}


def _sbom_lauf(lauf: release.Lauf, monkeypatch: pytest.MonkeyPatch, *,
              npm_signaturen: dict[str, Any] | str | None = "standard") -> SbomErgebnis:
    lauf.arbeitsordner.mkdir()
    wt = lauf.arbeitsordner / "wt"
    wt.mkdir()
    lauf.worktree = wt
    if npm_signaturen == "standard":
        npm_signaturen = _NPM_SIGNATUREN_STANDARD
    artefakt = Artefakt("web", Path("/x/web.tar.gz"), "0" * 64, "0" * 128, [])
    bau = Bauergebnis([artefakt], [], npm_signaturen=npm_signaturen)  # type: ignore[arg-type]
    sboms = lauf.ausgabe / "sbom"
    sboms.mkdir(parents=True)
    (sboms / "scanview-web-0.1.0.cdx.json").write_text("{}", encoding="utf-8")
    ergebnis = SbomErgebnis(bau, {"web": sboms / "scanview-web-0.1.0.cdx.json"},
                            sboms / "scanview-0.1.0.cdx.json", sboms / "werkzeugbefunde.json",
                            [], ["zeile"])
    monkeypatch.setattr(release.sbom, "erzeuge_alle", lambda *a, **k: ergebnis)
    monkeypatch.setattr(release.sbom, "pruefe_bindung", lambda *a, **k: None)
    return ergebnis


def test_stufe_sbom_schreibt_npm_signaturen_bericht_und_bestaetigt_pruefung(
        lauf: release.Lauf, monkeypatch: pytest.MonkeyPatch) -> None:
    _sbom_lauf(lauf, monkeypatch)
    bericht = release.stufe_sbom(lauf)
    assert lauf.pruefungen["sbom"] == "bestanden"
    assert lauf.pruefungen["npm-signaturen"] == "bestanden"
    geschrieben = json.loads((lauf.berichte / "npm-signaturen.json").read_text("utf-8"))
    assert geschrieben == _NPM_SIGNATUREN_STANDARD
    assert bericht["artefakte"]["web"]["datei"] == "web.tar.gz"


def test_stufe_sbom_ohne_npm_signaturen_bricht_ab(lauf: release.Lauf,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Bau-Stufe muss die npm-Signaturprüfung beim echten Webbau immer liefern — fehlt sie
    trotzdem, ist das ein Reihenfolgefehler, kein stilles Überspringen."""
    _sbom_lauf(lauf, monkeypatch, npm_signaturen=None)
    with pytest.raises(NachweisFehler) as e:
        release.stufe_sbom(lauf)
    assert e.value.regel == "release-reihenfolge"
    assert "npm-signaturen" not in lauf.pruefungen
    assert not (lauf.berichte / "npm-signaturen.json").exists()


# --- Stufe „sast“ ---------------------------------------------------------------------------------

_SAST_BEFUNDE = """
[[befund]]
id = "B-SAST-SEMGREP-{fp_gross}"
quelle = "SAST Semgrep, Regel python.x"
titel = "Test"
schwere = "hoch"
kategorie = "sonstiges"
status = "kein-befund"
staende = ["abc1234"]
entdeckt = 2026-09-29
fingerabdruck = "{fp}"
"""


def _sast_lauf(lauf: release.Lauf, monkeypatch: pytest.MonkeyPatch, *, registriert: bool,
               ausnahme: bool = True) -> list[str]:
    lauf.arbeitsordner.mkdir()
    wt = lauf.arbeitsordner / "wt"
    (wt / "docs" / "compliance").mkdir(parents=True)
    lauf.worktree = wt
    lauf.werkzeuge = werkzeuge()
    fp = "0123456789abcdef"
    (wt / "docs/compliance/befunde.toml").write_text(
        _SAST_BEFUNDE.format(fp=fp if registriert else "f" * 16,
                             fp_gross=(fp if registriert else "f" * 16).upper()),
        encoding="utf-8")
    (wt / "docs/compliance/sast-ausnahmen.toml").write_text(
        '[[ausnahme]]\nwerkzeug = "bandit"\nregel = "B404"\npfade = ["server/src/**"]\n'
        'schwere = "niedrig"\nbegruendung = "Import allein ist kein Befund, Aufrufe einzeln '
        'bewertet"\ndatum = 2026-09-29\n' if ausnahme else "", encoding="utf-8")
    aufrufe: list[str] = []
    fund_s = sast.Fund("semgrep", "python.x", "server/src/a.py", 3, "hoch", fp, "t")
    fund_b = sast.Fund("bandit", "B404", "server/src/a.py", 1, "niedrig", "1" * 16, "t")

    def semgrep(worktree, werkzeuge, ausgabe, **kw):
        aufrufe.append(f"semgrep {ausgabe.name}")
        ausgabe.write_text("{}", encoding="utf-8")
        return [fund_s], 2

    def bandit(worktree, werkzeuge, ausgabe, **kw):
        aufrufe.append(f"bandit {ausgabe.name}")
        ausgabe.write_text("{}", encoding="utf-8")
        return [fund_b], 1

    def regelsatz(nachweise: Path) -> sast.Regelquelle:
        aufrufe.append("regelsatz")
        return sast.Regelquelle("u", "0" * 40, "L", "lu", False, date(2026, 9, 29), "a")

    def bereitstellen(nachweise: Path, werkzeuge: Any, **kw: Any) -> list[Netzschritt]:
        aufrufe.append("bereitstellen")
        return [Netzschritt(["uv", "sync", "--locked"], "nachweise/werkzeuge/bandit",
                            "2026-09-29T00:00:00+00:00")]

    monkeypatch.setattr(sast, "pruefe_regelsatz", regelsatz)
    monkeypatch.setattr(sast, "bandit_bereitstellen", bereitstellen)
    monkeypatch.setattr(sast, "semgrep_laufen", semgrep)
    monkeypatch.setattr(sast, "bandit_laufen", bandit)
    return aufrufe


def test_stufe_sast_bestanden(lauf: release.Lauf, monkeypatch: pytest.MonkeyPatch) -> None:
    aufrufe = _sast_lauf(lauf, monkeypatch, registriert=True)
    bericht = release.stufe_sast(lauf)
    assert aufrufe == ["regelsatz", "bereitstellen", "semgrep sast-semgrep.json",
                       "bandit sast-bandit.json"]
    assert lauf.pruefungen["sast"] == "bestanden"
    assert any(s.befehl[:2] == ["uv", "sync"] for s in lauf.netzschritte)
    assert (lauf.berichte / "sast-semgrep.json").is_file()
    assert (lauf.berichte / "sast-bandit.json").is_file()
    # Den Stufenbericht (berichte/sast.json) schreibt die Orchestrierung aus dem Rückgabewert.
    assert not (lauf.berichte / "sast.json").exists()
    assert bericht["abdeckung"] == {"semgrep": 2, "bandit": 1}
    assert bericht["zaehlprobe"]["semgrep"]["hoch"] == 1
    assert bericht["zaehlprobe"]["bandit"]["niedrig"] == 1
    assert bericht["regelsatz"]["commit"] == "0" * 40
    assert [f["befund_id"] for f in bericht["funde"]] == [
        "B-SAST-SEMGREP-0123456789ABCDEF", "B-SAST-BANDIT-1111111111111111"]


def test_stufe_sast_unregistrierter_fund_blockiert(lauf: release.Lauf,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    _sast_lauf(lauf, monkeypatch, registriert=False)
    with pytest.raises(NachweisFehler) as e:
        release.stufe_sast(lauf)
    assert e.value.regel == "sast-gate"
    assert "B-SAST-SEMGREP-0123456789ABCDEF" in e.value.text
    assert "sast" not in lauf.pruefungen


def test_stufe_sast_niedriger_fund_ohne_ausnahme_blockiert(
        lauf: release.Lauf, monkeypatch: pytest.MonkeyPatch) -> None:
    _sast_lauf(lauf, monkeypatch, registriert=True, ausnahme=False)
    with pytest.raises(NachweisFehler) as e:
        release.stufe_sast(lauf)
    assert e.value.regel == "sast-gate" and "B404" in e.value.text


# --- Stufe „fuzzing“ ------------------------------------------------------------------------------

def _fuzzing_ergebnis(blockiert: list[str]) -> Any:
    from scanview_nachweise import fuzzing
    bericht = {"laeufe": {"betrachter": {"hinweise": []},
                          "admin": {"hinweise": ["admin: Ausschluss ohne Treffer: x"]}},
               "blockierend": blockiert}
    return fuzzing.Ergebnis(bericht, blockiert, [Netzschritt(
        ["docker", "run", "--network", "host"], "deploy/platform", "2026-09-29T00:00:00+00:00")])


def test_stufe_fuzzing_bestanden(lauf: release.Lauf, monkeypatch: pytest.MonkeyPatch) -> None:
    from scanview_nachweise import fuzzing
    gesehen: list[tuple[Path, Path, Path]] = []

    def pruefen(worktree: Path, werkzeuge: Any, berichte: Path, arbeitsordner: Path) -> Any:
        gesehen.append((worktree, berichte, arbeitsordner))
        return _fuzzing_ergebnis([])

    monkeypatch.setattr(fuzzing, "pruefen", pruefen)
    lauf.sbom = cast(Any, object())
    meldungen: list[str] = []
    lauf.melde = meldungen.append
    bericht = release.stufe_fuzzing(lauf)
    assert gesehen == [(lauf.worktree, lauf.berichte, lauf.arbeitsordner)]
    assert lauf.pruefungen["fuzzing"] == "bestanden"
    assert any(s.befehl[-1] == "host" for s in lauf.netzschritte)
    assert bericht["blockierend"] == []
    assert meldungen == ["HINWEIS admin: Ausschluss ohne Treffer: x"]


def test_stufe_fuzzing_fehlschlag_blockiert(lauf: release.Lauf,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    from scanview_nachweise import fuzzing
    monkeypatch.setattr(fuzzing, "pruefen", lambda *a: _fuzzing_ergebnis(
        ["admin: GET /api/v1/x: Server error"]))
    lauf.sbom = cast(Any, object())
    with pytest.raises(NachweisFehler) as e:
        release.stufe_fuzzing(lauf)
    assert e.value.regel == "fuzzing-gate" and "GET /api/v1/x" in e.value.text
    assert "fuzzing" not in lauf.pruefungen


def test_stufe_fuzzing_braucht_den_bau(lauf: release.Lauf) -> None:
    with pytest.raises(NachweisFehler) as e:
        release.stufe_fuzzing(lauf)
    assert e.value.regel == "release-reihenfolge"


# --- Stufe „egress“  -----------------------------------------------------------------------------

def _egress_ergebnis(blockiert: list[str], hinweise: list[str] | None = None) -> Any:
    from scanview_nachweise import egress
    bericht = {"hinweise": hinweise or [], "blockierend": blockiert}
    return egress.Ergebnis(bericht, blockiert, [Netzschritt(
        ["docker", "run", "--network", "host"], "web", "2026-10-01T00:00:00+00:00")])


def test_stufe_egress_bestanden(lauf: release.Lauf, monkeypatch: pytest.MonkeyPatch) -> None:
    from scanview_nachweise import egress
    gesehen: list[tuple[Path, Path, Path]] = []

    def pruefen(worktree: Path, werkzeuge: Any, berichte: Path, arbeitsordner: Path) -> Any:
        gesehen.append((worktree, berichte, arbeitsordner))
        return _egress_ergebnis([], ["Ausnahme ohne Fund: https://alt.example.org/x"])

    monkeypatch.setattr(egress, "pruefen", pruefen)
    lauf.sbom = cast(Any, object())
    meldungen: list[str] = []
    lauf.melde = meldungen.append
    bericht = release.stufe_egress(lauf)
    assert gesehen == [(lauf.worktree, lauf.berichte, lauf.arbeitsordner)]
    assert lauf.pruefungen["egress"] == "bestanden"
    assert any(s.befehl[-1] == "host" for s in lauf.netzschritte)
    assert bericht["blockierend"] == []
    assert meldungen == ["HINWEIS Ausnahme ohne Fund: https://alt.example.org/x"]


def test_stufe_egress_fund_blockiert(lauf: release.Lauf, monkeypatch: pytest.MonkeyPatch) -> None:
    from scanview_nachweise import egress
    monkeypatch.setattr(egress, "pruefen", lambda *a: _egress_ergebnis(
        ["browser viewer: requestfailed https://cdn.example.org/x.js — fremder Ursprung"]))
    lauf.sbom = cast(Any, object())
    with pytest.raises(NachweisFehler) as e:
        release.stufe_egress(lauf)
    assert e.value.regel == "egress-gate" and "cdn.example.org" in e.value.text
    assert "egress" not in lauf.pruefungen


def test_stufe_egress_braucht_den_bau(lauf: release.Lauf) -> None:
    with pytest.raises(NachweisFehler) as e:
        release.stufe_egress(lauf)
    assert e.value.regel == "release-reihenfolge"


def test_egress_laeuft_nach_fuzzing_und_vor_dem_scan() -> None:
    assert release.STUFEN.index("fuzzing") < release.STUFEN.index("egress") \
        < release.STUFEN.index("scan")
    assert release.STANDARD_STUFEN["egress"] is release.stufe_egress


# --- Stufe „scan“ ---------------------------------------------------------------------------------

def _treffer(artefakt: str, schwere: str = "hoch") -> scan.Treffer:
    return scan.Treffer(f"CVE-2099-{artefakt}", f"pkg:npm/{artefakt}@1.0.0", "1.0.0", schwere,
                        None, artefakt, f"pkg:npm/{artefakt}@1.0.0")


def _scan_lauf(lauf: release.Lauf, monkeypatch: pytest.MonkeyPatch,
               treffer: list[scan.Treffer]) -> None:
    lauf.arbeitsordner.mkdir()
    wt = lauf.arbeitsordner / "wt"
    for lock in ("web/package-lock.json", "server/uv.lock", "mcp-server/uv.lock",
                 "import/uv.lock"):
        (wt / lock).parent.mkdir(parents=True, exist_ok=True)
        (wt / lock).write_text(lock, encoding="utf-8")
    (wt / "docs" / "compliance").mkdir(parents=True)
    (wt / "docs" / "compliance" / "vex.openvex.json").write_text(json.dumps({
        "@context": "https://openvex.dev/ns/v0.2.0", "@id": "urn:test", "author": "Test",
        "version": 1, "timestamp": "2026-09-28T00:00:00Z", "statements": []}), encoding="utf-8")
    lauf.worktree = wt
    sboms = lauf.ausgabe / "sbom"
    sboms.mkdir(parents=True)
    namen = ("web", "server", "mcp", "import")
    for n in namen:
        (sboms / f"scanview-{n}-0.1.0.cdx.json").write_text("{}", encoding="utf-8")
    lauf.sbom = SbomErgebnis(Bauergebnis([Artefakt(n, Path(f"/x/{n}.tar.gz"), "0" * 64, "0" * 128,
                                                   []) for n in namen], []),
                             {n: sboms / f"scanview-{n}-0.1.0.cdx.json" for n in namen},
                             sboms / "scanview-0.1.0.cdx.json", sboms / "werkzeugbefunde.json",
                             [], [])
    lauf.werkzeuge = {"trivy": Werkzeug("trivy", "aquasec/trivy@sha256:" + "1" * 64, "0.74.0",
                                        None)}
    stand = scan.DbStand(datetime(2026, 9, 28, tzinfo=UTC), "a" * 64, "UpdatedAt", None)
    monkeypatch.setattr(release.scan, "scanne_alle", lambda *a, **k: scan.ScanErgebnis(
        stand, treffer, {"web": {"treffer": {}}}))


def test_scan_gate_nur_auf_ausgelieferte_artefakte(lauf, monkeypatch):
    _scan_lauf(lauf, monkeypatch, [_treffer("import")])
    bericht = release.stufe_scan(lauf)
    assert lauf.pruefungen["scan"] == "bestanden"
    assert bericht["gate_artefakte"] == ["mcp", "server", "web"]
    assert any("CVE-2099-import" in z for z in bericht["informativ_blockierend"])
    ergebnis = json.loads((lauf.ausgabe / "sbom" / "scan-ergebnis.json").read_text("utf-8"))
    assert ergebnis["ergebnis"] == "grün" and ergebnis["informativ_blockierend"]


def test_scan_bricht_ab_wenn_ein_sbom_fehlt(lauf, monkeypatch):
    _scan_lauf(lauf, monkeypatch, [])
    assert lauf.sbom is not None
    lauf.sbom = SbomErgebnis(
        Bauergebnis([Artefakt(n, Path(f"/x/{n}.tar.gz"), "0" * 64, "0" * 128, [])
                     for n in ("web", "server", "mcp")], []),
        {n: p for n, p in lauf.sbom.sboms.items() if n in ("web", "mcp")},
        lauf.sbom.gesamt, lauf.sbom.werkzeugbefunde, [], [])
    for p in lauf.sbom.sboms.values():
        p.write_text("{}", encoding="utf-8")
    gescannt: list[int] = []
    monkeypatch.setattr(release.scan, "scanne_alle", lambda *a, **k: gescannt.append(1))
    with pytest.raises(NachweisFehler) as e:
        release.stufe_scan(lauf)
    assert e.value.regel == "scan-sbom" and "server" in e.value.text
    assert gescannt == [] and "scan" not in lauf.pruefungen


def test_scan_verlangt_ausgelieferte_sboms_auch_wenn_nicht_gebaut(lauf, monkeypatch):
    _scan_lauf(lauf, monkeypatch, [])
    assert lauf.sbom is not None
    ohne_mcp = {n: p for n, p in lauf.sbom.sboms.items() if n != "mcp"}
    lauf.sbom = SbomErgebnis(
        Bauergebnis([a for a in lauf.sbom.bau.artefakte if a.name != "mcp"], []), ohne_mcp,
        lauf.sbom.gesamt, lauf.sbom.werkzeugbefunde, [], [])
    with pytest.raises(NachweisFehler) as e:
        release.stufe_scan(lauf)
    assert e.value.regel == "scan-sbom" and "mcp" in e.value.text


def test_scan_gate_blockiert_ausgeliefertes_artefakt(lauf, monkeypatch):
    _scan_lauf(lauf, monkeypatch, [_treffer("web"), _treffer("import")])
    with pytest.raises(NachweisFehler) as e:
        release.stufe_scan(lauf)
    assert e.value.regel == "scan-gate" and "CVE-2099-web" in e.value.text
    assert "CVE-2099-import" not in e.value.text
    assert "scan" not in lauf.pruefungen
    ergebnis = json.loads((lauf.ausgabe / "sbom" / "scan-ergebnis.json").read_text("utf-8"))
    assert ergebnis["ergebnis"] == "blockiert"


# --- Stufe „matrix“ ---------------------------------------------------------------------------

def _kontrolle(id_: str, schluessel: str, rv: bool = True) -> Kontrolle:
    return Kontrolle(id=id_, quelle="q", titel="t", art="pflicht", anwendbar=True,
                     begruendung="", pruefhandlung="p", sollwert="s", release_voraussetzung=rv,
                     iso=(), oeffentlich=True, nachweis_schluessel=schluessel)


QUELLEN = """
scan = { pruefung = "scan" }
lizenzen = { pruefung = "lizenzen" }
manifest = { pruefung = "manifest" }
archiv = { pruefung = "archiv" }
"doku:release" = "docs/public/release.md"
"doku:support" = "docs/public/support-und-patches.md"
"vault:meldeplaybook" = { vault = "Ops/meldeplaybook.md" }
"""


def _matrix_lauf(lauf: release.Lauf, tmp_path: Path, kontrollen: list[Kontrolle]) -> None:
    wt = tmp_path / "wt"
    (wt / "nachweise").mkdir(parents=True)
    (wt / "nachweise" / "nachweisquellen.toml").write_text(QUELLEN, encoding="utf-8")
    (wt / "docs" / "public").mkdir(parents=True)
    (wt / "docs" / "public" / "release.md").write_text("# Release\n", encoding="utf-8")
    lauf.worktree = wt
    lauf.arbeitsordner.mkdir()
    lauf.kontrollen = kontrollen


def test_matrix_bestanden_und_r3_schluessel_spaeter(lauf, tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "Ops").mkdir(parents=True)
    (vault / "Ops" / "meldeplaybook.md").write_text("Playbook\n", encoding="utf-8")
    monkeypatch.setenv("SCANVIEW_VAULT", str(vault))
    _matrix_lauf(lauf, tmp_path, [
        _kontrolle("K-A", "lizenzen+scan"), _kontrolle("K-B", "doku:release"),
        _kontrolle("K-C", "manifest"), _kontrolle("K-D", "archiv"),
        _kontrolle("K-E", "vault:meldeplaybook"), _kontrolle("K-F", "doku:support", rv=False)])
    lauf.pruefungen.update(scan="bestanden", lizenzen="bestanden")
    bericht = release.stufe_matrix(lauf)
    assert set(bericht["erfuellt"]) == {"K-A", "K-B", "K-E"}
    assert bericht["nach_archiv"] == {"K-C": ["manifest"], "K-D": ["archiv"]}


def test_matrix_zusammengesetzt_mit_r3_teil(lauf, tmp_path):
    """„manifest+doku:…“ (z. B. K-CRA-I-F): der Dokumentteil wird in der Stufe geprüft, der
    R3-Teil folgt nach der Signatur — der Lauf bricht nicht an „manifest“ ab."""
    _matrix_lauf(lauf, tmp_path, [_kontrolle("K-G", "manifest+doku:release")])
    bericht = release.stufe_matrix(lauf)
    assert set(bericht["erfuellt"]["K-G"]) == {"doku:release"}
    assert bericht["nach_archiv"] == {"K-G": ["manifest"]}
    (lauf.worktree / "docs" / "public" / "release.md").unlink()
    with pytest.raises(NachweisFehler, match="K-G: doku:release"):
        release.stufe_matrix(lauf)


def test_matrix_bricht_mit_liste_fehlender_nachweise_ab(lauf, tmp_path, monkeypatch):
    monkeypatch.delenv("SCANVIEW_VAULT", raising=False)
    _matrix_lauf(lauf, tmp_path, [
        _kontrolle("K-A", "lizenzen+scan"), _kontrolle("K-B", "doku:support"),
        _kontrolle("K-C", "doku:unbekannt"), _kontrolle("K-E", "vault:meldeplaybook")])
    lauf.pruefungen.update(scan="bestanden")
    with pytest.raises(NachweisFehler) as e:
        release.stufe_matrix(lauf)
    assert e.value.regel == "matrix"
    for teil in ("K-A: lizenzen", "K-B: doku:support (docs/public/support-und-patches.md fehlt)",
                 "K-C: doku:unbekannt (kein Eintrag in nachweisquellen.toml)",
                 "K-E: vault:meldeplaybook (SCANVIEW_VAULT nicht gesetzt)"):
        assert teil in e.value.text


def test_matrix_leeres_dokument_zaehlt_nicht(lauf, tmp_path):
    _matrix_lauf(lauf, tmp_path, [_kontrolle("K-B", "doku:release")])
    (lauf.worktree / "docs" / "public" / "release.md").write_text("  \n", encoding="utf-8")
    with pytest.raises(NachweisFehler, match="leer"):
        release.stufe_matrix(lauf)


@pytest.mark.parametrize("inhalt", ['scan = 3\n', 'scan = { pruefung = "scan", x = 1 }\n',
                                    '"doku:x" = "../ausserhalb.md"\n', '"doku:x" = "/etc/passwd"\n',
                                    'scan = { vault = "a" }\n'])
def test_nachweisquellen_ungueltig(tmp_path, inhalt):
    pfad = tmp_path / "nachweisquellen.toml"
    pfad.write_text(inhalt, encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        release.lade_nachweisquellen(pfad)
    assert e.value.regel == "nachweisquellen"


@pytest.mark.skipif(not (WURZEL / "docs" / "compliance" / "kontrollmatrix.toml").exists(),
                    reason="Kontrollmatrix liegt nur im privaten Repo (nicht Teil des öffentlichen "
                           "Exports)")
def test_repo_nachweisquellen_decken_alle_release_voraussetzungen():
    from scanview_nachweise.register import lade_matrix
    quellen = release.lade_nachweisquellen(WURZEL / "nachweise" / "nachweisquellen.toml")
    for k in lade_matrix(WURZEL / "docs" / "compliance" / "kontrollmatrix.toml"):
        if k.release_voraussetzung:
            for teil in k.schluessel_teile:
                assert teil in quellen, f"{k.id}: {teil}"
    assert quellen["doku:release"].pfad == "docs/public/release.md"
    assert quellen["manifest"].pruefung == "manifest" and quellen["archiv"].pruefung == "archiv"


# --- Export-Commit ----------------------------------------------------------------------------

def _exportbaum(ordner: Path, dateien: dict[str, str]) -> export.Exportmanifest:
    eintraege = []
    for pfad, inhalt in sorted(dateien.items()):
        datei = ordner / pfad
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.write_text(inhalt, encoding="utf-8")
        datei.chmod(0o644)
        eintraege.append((pfad, hashlib.sha256(inhalt.encode()).hexdigest(), "100644"))
    return export.Exportmanifest(eintraege)


def _ohne_ref(repo: Path, ref: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "-q", "--verify", ref],
                          capture_output=True).returncode != 0


def test_export_commit_nur_auf_kandidat_neutral_lokal(tmp_path, monkeypatch):
    aufrufe: list[list[str]] = []
    original = release._befehl_roh

    def mitschneiden(befehl: list[str], regel: str, **kw: Any) -> bytes:
        aufrufe.append(befehl)
        return original(befehl, regel, **kw)
    monkeypatch.setattr(release, "_befehl_roh", mitschneiden)

    baum = tmp_path / "export"
    m1 = _exportbaum(baum, {"README.md": "hallo\n", "web/a b,c.ts": "x\n"})
    repo = tmp_path / "oeffentlich"
    commit = release.export_committen(repo, baum, m1, None, "0.1.0", "d" * 64)
    # Nur der Kandidat-Zweig bewegt sich; main bleibt bis nach dem Archiv unberührt.
    assert _git(repo, "rev-parse", "refs/heads/release-kandidat/0.1.0") == commit
    assert _ohne_ref(repo, "refs/heads/main")
    assert _git(repo, "status", "--porcelain") == ""
    autor = _git(repo, "log", "-1", "--format=%an|%ae|%cn|%ce", commit)
    assert autor == "|".join([*release.OEFFENTLICHE_IDENTITAET] * 2)
    assert "0.1.0" in _git(repo, "log", "-1", "--format=%B", commit)
    assert sorted(_git(repo, "ls-tree", "-r", "--name-only", commit).splitlines()) == [
        "README.md", "web/a b,c.ts"]

    freigabe = release.export_freigeben(repo, "0.1.0", commit, "f" * 64, sperrgrund=None)
    assert _git(repo, "rev-parse", "refs/heads/main") == commit
    assert _ohne_ref(repo, "refs/heads/release-kandidat/0.1.0")
    assert sorted(_git(repo, "ls-files").splitlines()) == ["README.md", "web/a b,c.ts"]
    # Annotierter Tag mit Version und Manifest-Hash, neutrale Identität.
    assert _git(repo, "cat-file", "-t", "v0.1.0") == "tag"
    tag = _git(repo, "cat-file", "-p", "v0.1.0")
    assert "scanview 0.1.0" in tag and "Manifest-SHA-256 " + "f" * 64 in tag
    assert "tagger scanview release <security@sitebits.de>" in tag
    assert freigabe["tag"] == "v0.1.0" and freigabe["commit"] == commit
    # Nichts verlässt den Rechner: kein Befehl mit „push“, kein Remote.
    assert aufrufe and not any("push" in teil for befehl in aufrufe for teil in befehl)
    assert _git(repo, "remote") == ""

    # Folge-Release baut auf main auf; main bewegt sich erst mit der Freigabe.
    baum2 = tmp_path / "export2"
    m2 = _exportbaum(baum2, {"README.md": "hallo 2\n"})
    commit2 = release.export_committen(repo, baum2, m2, m1, "0.1.1", "e" * 64)
    assert _git(repo, "rev-parse", f"{commit2}~1") == commit
    assert _git(repo, "rev-parse", "refs/heads/main") == commit
    release.export_freigeben(repo, "0.1.1", commit2, "a" * 64, sperrgrund=None)
    assert _git(repo, "ls-files").splitlines() == ["README.md"]


def test_abbruch_nach_export_blockiert_keinen_neuen_lauf(tmp_path):
    baum = tmp_path / "export"
    m = _exportbaum(baum, {"README.md": "hallo\n"})
    repo = tmp_path / "oeffentlich"
    erster = release.export_committen(repo, baum, m, None, "0.1.0", "d" * 64)
    # Lauf bricht vor dem Archiv ab → Neustart derselben Version verwirft den Kandidaten.
    baum2 = tmp_path / "export2"
    m2 = _exportbaum(baum2, {"README.md": "hallo neu\n"})
    zweiter = release.export_committen(repo, baum2, m2, None, "0.1.0", "d" * 64)
    assert zweiter != erster
    assert _git(repo, "rev-parse", "refs/heads/release-kandidat/0.1.0") == zweiter
    assert _ohne_ref(repo, "refs/heads/main")


def test_export_commit_prueft_commit_inhalt_gegen_manifest(tmp_path, monkeypatch):
    baum = tmp_path / "export"
    m = _exportbaum(baum, {"README.md": "hallo\n"})
    repo = tmp_path / "oeffentlich"
    original = release._index_fuellen

    def mit_fremddatei(repo_: Path, quelle: Path, index: Path) -> None:
        (quelle / "untergeschoben.txt").write_text("x", encoding="utf-8")
        original(repo_, quelle, index)
    monkeypatch.setattr(release, "_index_fuellen", mit_fremddatei)
    with pytest.raises(NachweisFehler) as e:
        release.export_committen(repo, baum, m, None, "0.1.0", "d" * 64)
    assert e.value.regel == "export-ziel" and "untergeschoben.txt" in e.value.text
    assert _ohne_ref(repo, "refs/heads/release-kandidat/0.1.0")


def test_export_commit_stand_muss_zum_vorigen_manifest_passen(tmp_path):
    baum = tmp_path / "export"
    m1 = _exportbaum(baum, {"README.md": "hallo\n"})
    repo = tmp_path / "oeffentlich"
    c = release.export_committen(repo, baum, m1, None, "0.1.0", "d" * 64)
    release.export_freigeben(repo, "0.1.0", c, "f" * 64, sperrgrund=None)
    anders = export.Exportmanifest([("README.md", "0" * 64, "100644")])
    with pytest.raises(NachweisFehler) as e:
        release.export_committen(repo, baum, m1, anders, "0.1.1", "e" * 64)
    assert e.value.regel == "export-ziel"
    with pytest.raises(NachweisFehler) as e:
        release.export_committen(repo, baum, m1, None, "0.1.1", "e" * 64)
    assert e.value.regel == "export-ziel"


def test_export_freigeben_tag_existiert_schon(tmp_path):
    baum = tmp_path / "export"
    m = _exportbaum(baum, {"README.md": "hallo\n"})
    repo = tmp_path / "oeffentlich"
    c = release.export_committen(repo, baum, m, None, "0.1.0", "d" * 64)
    _git(repo, "-c", "user.name=x", "-c", "user.email=x@y", "tag", "-a", "v0.1.0", "-m", "fremd",
         c)
    with pytest.raises(NachweisFehler) as e:
        release.export_freigeben(repo, "0.1.0", c, "f" * 64, sperrgrund=None)
    assert e.value.regel == "export-freigabe"
    assert _ohne_ref(repo, "refs/heads/main")


def test_export_freigeben_gesperrt_nur_lokal_mit_vermerk(tmp_path):
    """Gesperrte Veröffentlichung — lokal vorspulen und taggen geht, der Tag trägt den
    Vermerk; ein (künftiger) Veröffentlichungswunsch wird verweigert, bevor sich etwas ändert."""
    baum = tmp_path / "export"
    m = _exportbaum(baum, {"README.md": "hallo\n"})
    repo = tmp_path / "oeffentlich"
    c = release.export_committen(repo, baum, m, None, "0.1.0", "d" * 64)
    with pytest.raises(NachweisFehler) as e:
        release.export_freigeben(repo, "0.1.0", c, "f" * 64, sperrgrund="SECURITY.md:3 Marker",
                                 veroeffentlichen=True)
    assert e.value.regel == "export-veroeffentlichung" and "SECURITY.md:3" in e.value.text
    assert _ohne_ref(repo, "refs/heads/main")
    release.export_freigeben(repo, "0.1.0", c, "f" * 64, sperrgrund="SECURITY.md:3 Marker")
    assert _git(repo, "rev-parse", "refs/heads/main") == c
    tag = _git(repo, "cat-file", "-p", "v0.1.0")
    assert "Veröffentlichung gesperrt: SECURITY.md:3 Marker" in tag


def test_stufe_export_vermerkt_gesperrte_veroeffentlichung(lauf, tmp_path, monkeypatch):
    """Marker im Release → Export-Stufe besteht, Sperre im Lauf, Bericht und Meldung."""
    gemeldet: list[str] = []
    lauf.melde = gemeldet.append
    lauf.arbeitsordner.mkdir()
    aufrufe: list[dict[str, Any]] = []

    def exportiere(*args: Any, **kw: Any) -> export.Exportergebnis:
        aufrufe.append(kw)
        m = export.Exportmanifest([("SECURITY.md", "0" * 64, "100644")],
                                  veroeffentlichbar=False, sperrgrund="SECURITY.md:3 Marker")
        return export.Exportergebnis(commit="a" * 40, manifest=m,
                                     diff=export.diff_manifeste(None, m), diff_hash="d" * 64,
                                     netzschritte=[])
    monkeypatch.setattr(release.export, "exportiere", exportiere)
    monkeypatch.setattr(release.export, "lade_begriffe", lambda: ["x"])
    monkeypatch.setattr(release, "letztes_exportmanifest", lambda archiv: None)
    monkeypatch.setattr(release, "_oeffentliche_beigaben", lambda lauf, begriffe: [])
    gepruefte: list[list[str]] = []
    monkeypatch.setattr(release, "_ausgelieferte_inhalte",
                        lambda lauf, begriffe: gepruefte.append(begriffe) or ["w", "s", "m"])
    monkeypatch.setattr(release, "export_committen", lambda *a, **k: "b" * 40)
    bericht = release.stufe_export(lauf)
    assert gepruefte == [["x"]]
    assert bericht["ausgelieferte_artefakte_geprueft"] == ["w", "s", "m"]
    assert aufrufe[0]["postfach_vermerken"] is True
    assert bericht["veroeffentlichbar"] is False and "SECURITY.md:3" in bericht["sperrgrund"]
    assert lauf.sperrgrund == "SECURITY.md:3 Marker"
    assert lauf.pruefungen["export"] == "bestanden"
    assert lauf.exportmanifest is not None
    assert json.loads(lauf.exportmanifest.read_text("utf-8"))["veroeffentlichbar"] is False


def test_release_meldet_gesperrte_veroeffentlichung(tmp_path):
    a = _Attrappen()
    stufen = a.stufen()
    innen = stufen["export"]

    def export_mit_sperre(lauf: release.Lauf) -> dict[str, Any]:
        lauf.sperrgrund = "SECURITY.md:3 Marker"
        return innen(lauf)
    stufen["export"] = export_mit_sperre
    gemeldet: list[str] = []
    release.release(tmp_path / "repo", "HEAD", "0.1.0", tmp_path / "archiv", tmp_path / "key",
                    tmp_path / "export-repo", None, arbeitsordner=tmp_path / "arbeit",
                    stufen=stufen, melde=gemeldet.append)
    assert gemeldet[-1] == "Veröffentlichung gesperrt: SECURITY.md:3 Marker"


def test_letztes_exportmanifest_aus_dem_archiv(tmp_path):
    archiv = tmp_path / "archiv"
    assert release.letztes_exportmanifest(archiv / "gibtsnicht") is None
    (archiv / "releases").mkdir(parents=True)
    assert release.letztes_exportmanifest(archiv) is None
    for v, inhalt in (("0.2.0", "zwei"), ("0.10.0", "zehn"), ("0.9.1", "neun")):
        (archiv / "releases" / v).mkdir()
        m = export.Exportmanifest([(f"{inhalt}.md", "0" * 64, "100644")])
        (archiv / "releases" / v / release.EXPORTMANIFEST).write_text(m.als_json(), "utf-8")
    (archiv / "releases" / ".0.11.0.unfertig").mkdir()
    gefunden = release.letztes_exportmanifest(archiv)
    assert gefunden is not None and gefunden.eintraege[0][0] == "zehn.md"
    (archiv / "releases" / "0.11.0").mkdir()
    with pytest.raises(NachweisFehler) as e:
        release.letztes_exportmanifest(archiv)
    assert e.value.regel == "export-manifest" and "0.11.0" in e.value.text


# --- Manifest, Archiv, Tag (ohne Docker, Wegwerf-Schlüssel) ------------------------------------

def _artefakte(ordner: Path) -> list[Artefakt]:
    ordner.mkdir(parents=True)
    liste = []
    for name in ("web", "server", "mcp", "import"):
        pfad = ordner / f"scanview-{name}-0.1.0.tar.gz"
        pfad.write_bytes(name.encode())
        liste.append(Artefakt(name, pfad, hashlib.sha256(name.encode()).hexdigest(),
                              hashlib.sha512(name.encode()).hexdigest(), []))
    return liste


def _bis_zur_matrix(lauf: release.Lauf, tmp_path: Path) -> tuple[Path, Path]:
    key, signers = _schluessel(tmp_path / "schluessel")
    lauf.schluessel = key
    wt = tmp_path / "wt"
    (wt / "nachweise").mkdir(parents=True)
    (wt / "nachweise" / "allowed_signers").write_bytes(signers.read_bytes())
    (wt / "nachweise" / "signierschluessel.pub").write_bytes(
        key.with_suffix(".pub").read_bytes())
    lauf.worktree = wt
    lauf.voll = _git(lauf.repo, "rev-parse", "HEAD")
    lauf.arbeitsordner.mkdir()
    artefakte = _artefakte(lauf.ausgabe / "artefakte")
    sboms = lauf.ausgabe / "sbom"
    sboms.mkdir()
    pfade = {}
    for a in artefakte:
        pfade[a.name] = sboms / f"scanview-{a.name}-0.1.0.cdx.json"
        pfade[a.name].write_text("{}", encoding="utf-8")
    (sboms / "scanview-0.1.0.cdx.json").write_text("{}", encoding="utf-8")
    (sboms / "werkzeugbefunde.json").write_text("{}", encoding="utf-8")
    (sboms / "scan-ergebnis.json").write_text("{}", encoding="utf-8")
    lauf.sbom = SbomErgebnis(Bauergebnis(artefakte, []), pfade, sboms / "scanview-0.1.0.cdx.json",
                             sboms / "werkzeugbefunde.json", [], [])
    lauf.db_stand = scan.DbStand(datetime(2026, 9, 28, tzinfo=UTC), "a" * 64, "UpdatedAt", None)
    lauf.werkzeuge = werkzeuge()
    lauf.netzschritte = [Netzschritt(["npm", "ci"], "web", "2026-09-28T00:00:00+00:00")]
    baum = tmp_path / "exportbaum"
    em = _exportbaum(baum, {"README.md": "hallo\n"})
    lauf.exportcommit = release.export_committen(lauf.export_ziel, baum, em, None, "0.1.0",
                                                 "d" * 64)
    lauf.exportmanifest = lauf.arbeitsordner / release.EXPORTMANIFEST
    lauf.exportmanifest.write_text(export.Exportmanifest([]).als_json(), encoding="utf-8")
    # Die Stufen bis `export` gelten als bestanden (auch commit-signatur aus `einfrieren`).
    lauf.pruefungen.update({n: "bestanden" for n in manifest.PFLICHT_PRUEFUNGEN})
    (lauf.berichte).mkdir()
    for n in release.STUFEN[:release.STUFEN.index("manifest")]:
        (lauf.berichte / f"{n}.json").write_text(json.dumps({"stufe": n}), encoding="utf-8")
    (lauf.berichte / "npm-signaturen.json").write_text(json.dumps({"abdeckung": 0}),
                                                        encoding="utf-8")
    for name in ("sast-semgrep", "sast-bandit"):
        (lauf.berichte / f"{name}.json").write_text(json.dumps({"results": []}),
                                                    encoding="utf-8")
    for name in ("fuzzing-admin", "fuzzing-betrachter"):
        (lauf.berichte / f"{name}.xml").write_text("<testsuites/>", encoding="utf-8")
    for name in ("egress-statisch", "egress-browser"):
        (lauf.berichte / f"{name}.json").write_text(json.dumps({"blockierend": []}),
                                                    encoding="utf-8")
    return key, signers


def test_manifest_archiv_tag_ende_zu_ende(lauf, tmp_path, monkeypatch):
    monkeypatch.setattr(release, "umgebung_erfassen", lambda wt: dict(UMGEBUNG))
    _, signers = _bis_zur_matrix(lauf, tmp_path)
    _stufen_mit_bericht(lauf, release.stufe_manifest, release.stufe_archiv, release.stufe_tag,
                        release.stufe_abschluss)
    # Erst jetzt gibt es den signierten Abschluss; er nennt den Archiv-Commit.
    ab = json.loads((lauf.archiv / "abschluesse" / "0.1.0" / "abschluss.json").read_bytes())
    assert ab["archiv_commit"] == _git(lauf.archiv, "log", "--format=%H", "-1", "--",
                                       "releases/0.1.0")
    assert ab["manifest_sha256"] == hashlib.sha256(lauf.manifest_pfad.read_bytes()).hexdigest()
    assert lauf.manifest_pfad is not None
    m = manifest.lade_signiertes_manifest(lauf.manifest_pfad, signers)
    assert m["trockenlauf"] is False and m["umgebung"] == UMGEBUNG
    assert m["exportcommit"] == lauf.exportcommit and m["quellcommit"] == lauf.voll
    assert m["pruefungen"]["negativkontrollen"] == "bestanden"
    assert {"stufe-einfrieren", "stufe-matrix", "scan-ergebnis", "exportmanifest",
            "werkzeugbefunde", "stufe-sast", "sast-semgrep", "sast-bandit"} <= set(m["berichte"])
    assert m["pruefungen"]["sast"] == "bestanden"
    ziel = lauf.archiv / "releases" / "0.1.0"
    for name in ("manifest.json", "manifest.json.sig", "allowed_signers",
                 "signierschluessel.pub", release.EXPORTMANIFEST, "artefakte", "sbom", "berichte"):
        assert (ziel / name).exists(), name
    assert (ziel / "manifest.json").read_bytes() == lauf.manifest_pfad.read_bytes()
    assert _git(lauf.archiv, "status", "--porcelain") == ""
    # Signierter Tag im privaten Repo auf dem Quellcommit.
    assert _git(lauf.repo, "rev-list", "-n", "1", "v0.1.0") == lauf.voll
    assert "SSH SIGNATURE" in _git(lauf.repo, "cat-file", "-p", "v0.1.0")
    # Der Signierschlüssel gilt nur für diesen Befehl: keine Konfiguration im Repo.
    assert "signingkey" not in (lauf.repo / ".git" / "config").read_text(encoding="utf-8")
    # Export erst jetzt freigegeben: main vorgespult, annotierter Tag mit Manifest-Hash.
    sha = hashlib.sha256(lauf.manifest_pfad.read_bytes()).hexdigest()
    assert _git(lauf.export_ziel, "rev-parse", "refs/heads/main") == lauf.exportcommit
    assert f"Manifest-SHA-256 {sha}" in _git(lauf.export_ziel, "cat-file", "-p", "v0.1.0")


def _stufen_mit_bericht(lauf: release.Lauf, *stufen: release.Stufe) -> None:
    """Stufen wie die Orchestrierung: nach jeder Stufe ihr Bericht mit „bestanden“."""
    for stufe in stufen:
        name = stufe.__name__.removeprefix("stufe_")
        bericht = stufe(lauf)
        (lauf.berichte / f"{name}.json").write_text(
            json.dumps({"stufe": name, "ergebnis": "bestanden", **bericht}), encoding="utf-8")


def test_tag_verlangt_bestandene_manifest_und_archivstufe(lauf, tmp_path, monkeypatch):
    monkeypatch.setattr(release, "umgebung_erfassen", lambda wt: dict(UMGEBUNG))
    _bis_zur_matrix(lauf, tmp_path)
    _stufen_mit_bericht(lauf, release.stufe_manifest)
    with pytest.raises(NachweisFehler) as e:
        release.stufe_tag(lauf)  # Archivbericht fehlt
    assert e.value.regel == "release-reihenfolge"
    assert _ohne_ref(lauf.repo, "refs/tags/v0.1.0")


def test_tag_prueft_gegen_eingecheckten_oeffentlichen_schluessel(lauf, tmp_path, monkeypatch):
    monkeypatch.setattr(release, "umgebung_erfassen", lambda wt: dict(UMGEBUNG))
    _bis_zur_matrix(lauf, tmp_path)
    _stufen_mit_bericht(lauf, release.stufe_manifest, release.stufe_archiv)
    anderer, _ = _schluessel(tmp_path / "anderer")
    (lauf.worktree / "nachweise" / "signierschluessel.pub").write_bytes(
        anderer.with_suffix(".pub").read_bytes())
    with pytest.raises(NachweisFehler) as e:
        release.stufe_tag(lauf)
    assert e.value.regel == "release-tag"
    assert "Wiederherstellung" in e.value.text and "tag -d v0.1.0" in e.value.text


def test_tag_vorhandener_ungueltiger_rest_wird_erkannt(lauf, tmp_path, monkeypatch):
    monkeypatch.setattr(release, "umgebung_erfassen", lambda wt: dict(UMGEBUNG))
    _bis_zur_matrix(lauf, tmp_path)
    _stufen_mit_bericht(lauf, release.stufe_manifest, release.stufe_archiv)
    _git(lauf.repo, "tag", "-a", "v0.1.0", "-m", "unsigniert", lauf.voll)
    with pytest.raises(NachweisFehler) as e:
        release.stufe_tag(lauf)
    assert e.value.regel == "release-tag" and "existiert" in e.value.text
    assert "Wiederherstellung" in e.value.text


def test_trockenlauf_manifest_gekennzeichnet(lauf, tmp_path, monkeypatch):
    monkeypatch.setattr(release, "umgebung_erfassen", lambda wt: dict(UMGEBUNG))
    lauf.trocken = True
    _, signers = _bis_zur_matrix(lauf, tmp_path)
    release.stufe_manifest(lauf)
    assert lauf.manifest_pfad is not None
    assert manifest.lade_signiertes_manifest(lauf.manifest_pfad, signers)["trockenlauf"] is True


def test_umgebung_erfassen(tmp_path, monkeypatch):
    antworten = {"node": "v22.20.0\n", "npm": "10.9.3\n", "uv": "uv 0.11.3\n", "docker": "28.4.0\n"}

    def befehl(b: list[str], regel: str, **kw: Any) -> str:
        if b[0].endswith("python"):
            return "Python 3.12.11\n"
        return antworten[b[0]]
    monkeypatch.setattr(release, "_befehl", befehl)
    (tmp_path / "server" / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / "server" / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    u = release.umgebung_erfassen(tmp_path)
    assert u["node"] == "v22.20.0" and u["python"] == "Python 3.12.11" and u["docker"] == "28.4.0"
    assert u["architektur"]


def test_umgebung_ohne_stille_einfluesse(monkeypatch):
    for name in ("VIRTUAL_ENV", "PYTEST_ADDOPTS", "PYTHONPATH", "PYTHONHOME", "NODE_OPTIONS",
                 "NODE_PATH", "npm_config_registry", "NPM_CONFIG_IGNORE_SCRIPTS", "UV_INDEX_URL",
                 "UV_NO_CONFIG", "PIP_INDEX_URL"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("HOME", "/nonexistent/test-home")
    umgebung = release._umgebung()
    for name in ("VIRTUAL_ENV", "PYTEST_ADDOPTS", "PYTHONPATH", "PYTHONHOME", "NODE_OPTIONS",
                 "NODE_PATH", "npm_config_registry", "NPM_CONFIG_IGNORE_SCRIPTS", "UV_INDEX_URL",
                 "UV_NO_CONFIG", "PIP_INDEX_URL"):
        assert name not in umgebung, name
    assert umgebung["HOME"] == "/nonexistent/test-home" and "PATH" in umgebung


def test_kein_push_im_quelltext():
    quelltext = (WURZEL / "nachweise/src/scanview_nachweise/release.py").read_text("utf-8")
    assert "push" not in quelltext.lower()


def test_cli_release_trocken_ruft_release(tmp_path, monkeypatch, capsys):
    from scanview_nachweise import cli
    gesehen: dict[str, Any] = {}

    def attrappe(repo, commit, version, archiv, schluessel, export_ziel, bestaetigt, **kw):
        gesehen.update(commit=commit, version=version, bestaetigt=bestaetigt, **kw)
        return tmp_path / "manifest.json"
    monkeypatch.setattr(release, "release", attrappe)
    code = cli.main(["release", "HEAD", "--version", "0.1.0", "--trocken", "--bestaetigt",
                     "abc", "--repo", str(WURZEL), "--arbeitsordner", str(tmp_path / "a")])
    assert code == 0
    assert gesehen["trocken"] is True and gesehen["bestaetigt"] == "abc"
    assert gesehen["version"] == "0.1.0" and gesehen["commit"] == "HEAD"
    assert "manifest.json" in capsys.readouterr().out


def test_heute_ist_utc_datum():
    assert isinstance(release.heute(), date)


# --- Ausgelieferte Inhalte ------------------------------------------------------------------------

def _tarball(pfad: Path, dateien: dict[str, str]) -> Artefakt:
    import io
    import tarfile
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(pfad, "w:gz") as tar:
        for name, inhalt in dateien.items():
            daten = inhalt.encode()
            info = tarfile.TarInfo(name)
            info.size = len(daten)
            tar.addfile(info, io.BytesIO(daten))
    roh = pfad.read_bytes()
    return Artefakt(pfad.name.split("-")[1], pfad, hashlib.sha256(roh).hexdigest(),
                    hashlib.sha512(roh).hexdigest(), [])


def _ausgeliefert_lauf(lauf: release.Lauf, inhalte: dict[str, dict[str, str]]) -> None:
    lauf.arbeitsordner.mkdir(exist_ok=True)
    artefakte = [_tarball(lauf.ausgabe / "artefakte" / f"scanview-{n}-0.1.0.tar.gz", d)
                 for n, d in inhalte.items()]
    lauf.sbom = SbomErgebnis(Bauergebnis(artefakte, []), {}, lauf.ausgabe / "g.json",
                             lauf.ausgabe / "w.json", [], [])


#: Zur Laufzeit zusammengesetzt: als Literal fände die Privatpfadprüfung des öffentlichen Exports
#: diese Testdatei selbst.
_HEIM = "/" + "home" + "/nutzer"
_SAUBER = {"web": {"web/dist/index.html": "<html/>"},
           "server": {"server/pyproject.toml": "[project]\n"},
           "mcp": {"mcp-server/pyproject.toml": "[project]\n"},
           "import": {"import/x.py": f"kunde-x {_HEIM}\n"}}


def test_ausgelieferte_inhalte_alle_drei_geprueft(lauf, monkeypatch):
    geheim: list[Path] = []
    monkeypatch.setattr(release.export, "pruefe_geheimnisse",
                        lambda ordner, w, k: geheim.append(ordner))
    _ausgeliefert_lauf(lauf, _SAUBER)
    geprueft = release._ausgelieferte_inhalte(lauf, ["kunde-x"])
    assert geprueft == ["scanview-mcp-0.1.0.tar.gz", "scanview-server-0.1.0.tar.gz",
                        "scanview-web-0.1.0.tar.gz"]  # import: nicht ausgeliefert
    assert sorted(p.name for p in geheim) == ["mcp", "server", "web"]


@pytest.mark.parametrize(("name", "datei", "inhalt", "regel"), [
    ("server", "deploy/platform/README.md", "Betrieb für kunde-x\n", "export-begriffe"),
    ("mcp", "mcp-server/README.md", f"siehe {_HEIM}/notizen\n", "export-privater-pfad"),
    ("web", "web/dist/assets/a.js", 'const p="VITE:kunde-x";\n', "export-begriffe"),
])
def test_ausgelieferte_inhalte_privates_bricht_ab(lauf, monkeypatch, name, datei, inhalt, regel):
    monkeypatch.setattr(release.export, "pruefe_geheimnisse", lambda *a: None)
    inhalte = {n: dict(d) for n, d in _SAUBER.items()}
    inhalte[name][datei] = inhalt
    _ausgeliefert_lauf(lauf, inhalte)
    with pytest.raises(NachweisFehler) as e:
        release._ausgelieferte_inhalte(lauf, ["kunde-x"])
    assert e.value.regel == regel and name in e.value.text


def test_ausgeliefertes_web_mit_arbeitsordner_pfad(lauf, monkeypatch):
    monkeypatch.setattr(release.export, "pruefe_geheimnisse", lambda *a: None)
    inhalte = {n: dict(d) for n, d in _SAUBER.items()}
    inhalte["web"]["web/dist/assets/a.js"] = f'//# {lauf.arbeitsordner}/worktree/web/src/x.ts\n'
    _ausgeliefert_lauf(lauf, inhalte)
    with pytest.raises(NachweisFehler) as e:
        release._ausgelieferte_inhalte(lauf, ["nichts"])
    assert e.value.regel == "export-privater-pfad"


def test_ausgelieferte_inhalte_gitleaks_fund_bricht_ab(lauf, monkeypatch):
    def fund(ordner: Path, w: object, k: object) -> None:
        if ordner.name == "server":
            raise NachweisFehler("export-geheimnis", "Fund")
    monkeypatch.setattr(release.export, "pruefe_geheimnisse", fund)
    _ausgeliefert_lauf(lauf, _SAUBER)
    with pytest.raises(NachweisFehler) as e:
        release._ausgelieferte_inhalte(lauf, ["nichts"])
    assert e.value.regel == "export-geheimnis"


# --- Abschluss, Export-Tag ------------------------------------------------------------------------

def test_abschluss_verlangt_bestandenen_tag(lauf, tmp_path, monkeypatch):
    monkeypatch.setattr(release, "umgebung_erfassen", lambda wt: dict(UMGEBUNG))
    _bis_zur_matrix(lauf, tmp_path)
    _stufen_mit_bericht(lauf, release.stufe_manifest, release.stufe_archiv)
    with pytest.raises(NachweisFehler) as e:
        release.stufe_abschluss(lauf)  # Tag-Bericht fehlt
    assert e.value.regel == "release-reihenfolge"
    assert not (lauf.archiv / "abschluesse").exists()


def test_abbruch_im_tag_nennt_abschluss_nachholen(lauf, tmp_path, monkeypatch):
    monkeypatch.setattr(release, "umgebung_erfassen", lambda wt: dict(UMGEBUNG))
    _bis_zur_matrix(lauf, tmp_path)
    _stufen_mit_bericht(lauf, release.stufe_manifest, release.stufe_archiv)
    (lauf.worktree / "nachweise" / "signierschluessel.pub").unlink()
    with pytest.raises(NachweisFehler) as e:
        release.stufe_tag(lauf)
    assert "scanview-nachweise abschluss --version 0.1.0" in e.value.text


def test_abschluss_nachholen_prueft_beide_tags(lauf, tmp_path, monkeypatch):
    """Wiederherstellung: Abschluss erst, wenn privater und Export-Tag zum archivierten Manifest
    passen."""
    monkeypatch.setattr(release, "umgebung_erfassen", lambda wt: dict(UMGEBUNG))
    key, signers = _bis_zur_matrix(lauf, tmp_path)
    _stufen_mit_bericht(lauf, release.stufe_manifest, release.stufe_archiv)
    (lauf.repo / "nachweise").mkdir(exist_ok=True)
    (lauf.repo / "nachweise" / "allowed_signers").write_bytes(signers.read_bytes())
    (lauf.repo / "nachweise" / "signierschluessel.pub").write_bytes(
        key.with_suffix(".pub").read_bytes())
    with pytest.raises(NachweisFehler) as e:  # noch kein Tag
        release.abschluss_nachholen(lauf.repo, lauf.archiv, key, lauf.export_ziel, "0.1.0")
    assert e.value.regel == "release-tag"
    assert not (lauf.archiv / "abschluesse").exists()
    _stufen_mit_bericht(lauf, release.stufe_tag)
    ziel = release.abschluss_nachholen(lauf.repo, lauf.archiv, key, lauf.export_ziel, "0.1.0")
    assert (ziel / "abschluss.json.sig").is_file()


def test_export_tag_wird_nach_dem_setzen_geprueft(tmp_path):
    repo = tmp_path / "export"
    baum = tmp_path / "baum"
    em = _exportbaum(baum, {"README.md": "x\n"})
    c = release.export_committen(repo, baum, em, None, "0.1.0", "d" * 64)
    release.export_freigeben(repo, "0.1.0", c, "f" * 64, sperrgrund=None)
    release.pruefe_export_tag(repo, "0.1.0", c, "f" * 64, sperrgrund=None)
    with pytest.raises(NachweisFehler, match="Manifest-SHA-256"):
        release.pruefe_export_tag(repo, "0.1.0", c, "0" * 64, sperrgrund=None)
    with pytest.raises(NachweisFehler, match="Veröffentlichung gesperrt"):
        release.pruefe_export_tag(repo, "0.1.0", c, "f" * 64, sperrgrund="Marker")
    _git(repo, "tag", "-d", "v0.1.0")
    _git(repo, "-c", "user.name=Jemand", "-c", "user.email=j@example.invalid", "tag", "-a",
         "v0.1.0", "-m", "scanview 0.1.0\n\nManifest-SHA-256 " + "f" * 64, c)
    with pytest.raises(NachweisFehler, match="Tagger"):  # nicht die neutrale Identität
        release.pruefe_export_tag(repo, "0.1.0", c, "f" * 64, sperrgrund=None)
    _git(repo, "tag", "-d", "v0.1.0")
    _git(repo, "tag", "v0.1.0", c)
    with pytest.raises(NachweisFehler, match="annotiert"):
        release.pruefe_export_tag(repo, "0.1.0", c, "f" * 64, sperrgrund=None)


# --- Trockenlauf ab dem zweiten Release -----------------------------------------------------------

def test_trockenlauf_zweites_release_nutzt_klon_des_export_main(lauf, tmp_path):
    """Nach dem ersten Release trägt das Archiv ein voriges Exportmanifest; der Trockenlauf
    committet deshalb in einen Klon von main des echten Export-Repos — das echte bleibt
    unberührt."""
    baum1 = tmp_path / "baum1"
    em1 = _exportbaum(baum1, {"README.md": "eins\n"})
    c1 = release.export_committen(lauf.export_ziel, baum1, em1, None, "0.1.0", "d" * 64)
    release.export_freigeben(lauf.export_ziel, "0.1.0", c1, "f" * 64, sperrgrund=None)
    vorher = _git(lauf.export_ziel, "for-each-ref")
    lauf.trocken = True
    lauf.arbeitsordner.mkdir()
    wegwerf = release._trocken_export_repo(lauf)
    baum2 = tmp_path / "baum2"
    em2 = _exportbaum(baum2, {"README.md": "zwei\n"})
    c2 = release.export_committen(wegwerf, baum2, em2, em1, "0.1.1", "e" * 64)
    assert _git(wegwerf, "rev-parse", f"{c2}^") == c1
    assert _git(lauf.export_ziel, "for-each-ref") == vorher
    assert _ohne_ref(lauf.export_ziel, "refs/heads/release-kandidat/0.1.1")


def test_trockenlauf_erstes_release_leeres_wegwerf_repo(lauf):
    lauf.trocken = True
    lauf.arbeitsordner.mkdir()
    wegwerf = release._trocken_export_repo(lauf)
    assert not wegwerf.exists()  # export_committen legt es leer an


def test_qualitaetstor_setzt_die_variable_fuer_pytest(tmp_path, monkeypatch):
    """Die Kette verlässt sich nicht auf den Aufrufer: `SCANVIEW_QUALITAETSTOR=1` steht
    ausdrücklich in der Umgebung der pytest-Läufe (und nur dort), auch wenn der Aufrufer sie
    nicht oder anders gesetzt hat."""
    from types import SimpleNamespace
    monkeypatch.setenv("SCANVIEW_QUALITAETSTOR", "0")
    aufrufe: list[tuple[list[str], dict[str, str] | None]] = []

    def befehl(b: list[str], regel: str, **kw: Any) -> str:
        aufrufe.append((b, kw.get("umgebung")))
        return ""
    monkeypatch.setattr(release, "_befehl", befehl)
    monkeypatch.setattr(release, "_bestanden", lambda *a, **k: None)
    release.stufe_qualitaetstor(SimpleNamespace(worktree=tmp_path))  # type: ignore[arg-type]
    tests = [(b, u) for b, u in aufrufe if "pytest" in b]
    assert tests and all(u is not None and u["SCANVIEW_QUALITAETSTOR"] == "1" for _, u in tests)
    assert all(u is None for b, u in aufrufe if "pytest" not in b)


def test_ohne_werkzeug_im_kettenlauf_ein_fehlschlag(monkeypatch):
    """Mit der Variablen aus der Kette wird fehlendes gpg/Docker zum Fehlschlag."""
    import pytest as _pytest
    import werkzeugpflicht
    monkeypatch.setenv(release.QUALITAETSTOR_VARIABLE, "1")
    with _pytest.raises(_pytest.fail.Exception):
        werkzeugpflicht.verlange("gpg", False, "Test")
