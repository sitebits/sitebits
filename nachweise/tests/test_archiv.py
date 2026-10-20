"""Nachweis-Archiv: nur anhängen — jede Version genau einmal unter
`releases/<version>/`, lokal committet, nie überschrieben."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import date
from pathlib import Path

import pytest
import signierhilfe
from pflicht import werkzeuge_roh

from scanview_nachweise import archiv as archiv_modul
from scanview_nachweise.archiv import archiviere, schreibe_betrieb, schreibe_pruefung
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.manifest import PFLICHT_BERICHTE, PFLICHT_PRUEFUNGEN, SCHEMA, kanonisch

QUELLE = "d" * 40
UMGEBUNG = {"node": "v22", "npm": "10", "python": "3.12", "uv": "uv 0.11", "docker": "28",
            "architektur": "x86_64"}
#: Wegwerf-Schlüssel des Moduls (nie der echte Release-Schlüssel): Schlüssel, allowed_signers.
_SCHLUESSEL: dict[str, Path] = {}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout


@pytest.fixture
def archiv(tmp_path: Path) -> Path:
    """Archiv mit Vertrauensanker und Commit-Schlüssel (Archiv-Commits werden signiert)."""
    return signierhilfe.signiertes_archiv(tmp_path / "archiv")


@pytest.fixture
def archiv_ohne_anker(tmp_path: Path) -> Path:
    repo = tmp_path / "archiv"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("Archiv\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "Start")
    return repo


def _neuer_schluessel(ordner: Path) -> tuple[Path, Path]:
    ordner.mkdir(parents=True)
    key = ordner / "test_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", str(key)],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True)
    typ, wert = key.with_suffix(".pub").read_text(encoding="utf-8").split()[:2]
    signers = ordner / "allowed_signers"
    signers.write_text(f'release@scanview namespaces="scanview-release" {typ} {wert}\n',
                       encoding="utf-8")
    return key, signers


@pytest.fixture(scope="module", autouse=True)
def _schluessel(tmp_path_factory: pytest.TempPathFactory) -> None:
    key, signers = _neuer_schluessel(tmp_path_factory.mktemp("archiv-schluessel") / "k")
    _SCHLUESSEL.update(key=key, signers=signers)


def _signieren(manifest: Path, key: Path | None = None) -> None:
    sig = manifest.with_name(manifest.name + ".sig")
    sig.unlink(missing_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(key or _SCHLUESSEL["key"]),
                    "-n", "scanview-release", str(manifest)],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True)


def _archiviere(version: str, quellen: list[Path], repo: Path) -> Path:
    return archiviere(version, quellen, repo, allowed_signers=_SCHLUESSEL["signers"])


def _dateien(version: str) -> dict[str, bytes]:
    """Die Nachweisdateien, die das Manifest referenziert (relativer Pfad → Inhalt)."""
    d: dict[str, bytes] = {f"berichte/{n}.json": f'{{"b":"{n}"}}'.encode()
                           for n in PFLICHT_BERICHTE}
    for n in ("web", "server", "mcp"):
        d[f"artefakte/scanview-{n}-{version}.tar.gz"] = f"tar {n}".encode()
        d[f"sbom/scanview-{n}-{version}.cdx.json"] = f'{{"sbom":"{n}"}}'.encode()
    return d


def _manifest(version: str) -> dict[str, object]:
    """Formgültiges Manifest (wie `baue_manifest` es liefert), minimal befüllt; die Hashes
    passen zu `_dateien`."""
    d = _dateien(version)
    sha = {pfad: hashlib.sha256(inhalt).hexdigest() for pfad, inhalt in d.items()}
    return {
        "schema": SCHEMA, "version": version, "erstellt": "2026-09-27T12:00:00+00:00",
        "quellcommit": QUELLE, "exportcommit": "e" * 40,
        "artefakte": [{"name": n, "datei": f"scanview-{n}-{version}.tar.gz",
                       "sha256": sha[f"artefakte/scanview-{n}-{version}.tar.gz"],
                       "sha512": hashlib.sha512(
                           d[f"artefakte/scanview-{n}-{version}.tar.gz"]).hexdigest(),
                       "ausgeliefert": True}
                      for n in ("web", "server", "mcp")],
        "sboms": {f"scanview-{n}-{version}.cdx.json": sha[f"sbom/scanview-{n}-{version}.cdx.json"]
                  for n in ("web", "server", "mcp")},
        "berichte": {n: {"datei": f"{n}.json", "sha256": sha[f"berichte/{n}.json"]}
                     for n in PFLICHT_BERICHTE},
        "werkzeuge": werkzeuge_roh(),
        "netzschritte": [],
        "db_stand": {"zeit": "2026-09-27T12:00:00+00:00", "feld": "UpdatedAt",
                     "sha256": "0" * 64, "netzschritt": None},
        "pruefungen": {n: "bestanden" for n in PFLICHT_PRUEFUNGEN},
        "umgebung": dict(UMGEBUNG), "trockenlauf": False,
        "veroeffentlichung": {"veroeffentlichbar": True, "grund": None},
    }


def _quellen(tmp_path: Path, version: str = "0.1.0") -> list[Path]:
    q = tmp_path / f"quellen-{version}"
    q.mkdir()
    (q / "manifest.json").write_bytes(kanonisch(_manifest(version)))
    _signieren(q / "manifest.json")
    (q / "allowed_signers").write_bytes(_SCHLUESSEL["signers"].read_bytes())
    for relativ, inhalt in _dateien(version).items():
        (q / relativ).parent.mkdir(exist_ok=True)
        (q / relativ).write_bytes(inhalt)
    return [q / "manifest.json", q / "manifest.json.sig", q / "allowed_signers", q / "sbom",
            q / "artefakte", q / "berichte"]


def test_archivieren_und_commit(tmp_path, archiv):
    ziel = _archiviere("0.1.0", _quellen(tmp_path), archiv)
    assert ziel == archiv / "releases" / "0.1.0"
    assert (ziel / "manifest.json").is_file() and (ziel / "manifest.json.sig").is_file()
    assert (ziel / "sbom" / "scanview-web-0.1.0.cdx.json").is_file()
    assert _git(archiv, "log", "-1", "--format=%s").strip() == f"nachweise: 0.1.0 ({QUELLE})"
    assert _git(archiv, "status", "--porcelain") == ""
    assert "releases/0.1.0/sbom/scanview-web-0.1.0.cdx.json" in _git(archiv, "ls-files")


def test_zweites_schreiben_derselben_version_verweigert(tmp_path, archiv):
    _archiviere("0.1.0", _quellen(tmp_path), archiv)
    vorher = _git(archiv, "rev-parse", "HEAD")
    (tmp_path / "zweit").mkdir()
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", _quellen(tmp_path / "zweit"), archiv)
    assert e.value.regel == "archiv-unveraenderlich"
    assert _git(archiv, "rev-parse", "HEAD") == vorher


def test_geloeschtes_verzeichnis_mit_historie_verweigert(tmp_path, archiv):
    ziel = _archiviere("0.1.0", _quellen(tmp_path), archiv)
    _git(archiv, "rm", "-q", "-r", str(ziel))
    _git(archiv, "commit", "-q", "-S", "-m", "weg")
    (tmp_path / "zweit").mkdir()
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", _quellen(tmp_path / "zweit"), archiv)
    assert e.value.regel == "archiv-unveraenderlich"


def test_weitere_version_haengt_an(tmp_path, archiv):
    _archiviere("0.1.0", _quellen(tmp_path), archiv)
    _archiviere("0.1.1", _quellen(tmp_path, "0.1.1"), archiv)
    assert (archiv / "releases" / "0.1.0").is_dir() and (archiv / "releases" / "0.1.1").is_dir()
    assert len(_git(archiv, "log", "--format=%H").split()) == 4  # Start, Anker, zwei Versionen


@pytest.mark.parametrize("version", ["../x", "0.1", "", "0.1.0/..", "v0.1.0"])
def test_ungueltige_version(tmp_path, archiv, version):
    with pytest.raises(NachweisFehler) as e:
        _archiviere(version, _quellen(tmp_path), archiv)
    assert e.value.regel == "archiv"


def test_manifest_version_muss_passen(tmp_path, archiv):
    with pytest.raises(NachweisFehler, match=r"0\.2\.0"):
        _archiviere("0.2.0", _quellen(tmp_path), archiv)
    assert not (archiv / "releases").exists()


@pytest.mark.parametrize("weg", ["manifest.json", "manifest.json.sig"])
def test_manifest_und_signatur_pflicht(tmp_path, archiv, weg):
    quellen = [q for q in _quellen(tmp_path) if q.name != weg]
    with pytest.raises(NachweisFehler, match=weg.replace(".", r"\.")):
        _archiviere("0.1.0", quellen, archiv)


def test_fehlende_quelle(tmp_path, archiv):
    quellen = [*_quellen(tmp_path), tmp_path / "gibtsnicht.json"]
    with pytest.raises(NachweisFehler, match="gibtsnicht"):
        _archiviere("0.1.0", quellen, archiv)
    assert not (archiv / "releases" / "0.1.0").exists()


def test_doppelter_name(tmp_path, archiv):
    quellen = _quellen(tmp_path)
    anders = tmp_path / "anders"
    anders.mkdir()
    (anders / "manifest.json.sig").write_text("X", encoding="utf-8")
    with pytest.raises(NachweisFehler, match="doppelt"):
        _archiviere("0.1.0", [*quellen, anders / "manifest.json.sig"], archiv)


def test_unsauberes_archiv_verweigert(tmp_path, archiv):
    (archiv / "lose.txt").write_text("x", encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", _quellen(tmp_path), archiv)
    assert e.value.regel == "archiv"
    assert not (archiv / "releases" / "0.1.0").exists()


def test_kein_git_repo(tmp_path):
    (tmp_path / "leer").mkdir()
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", _quellen(tmp_path), tmp_path / "leer")
    assert e.value.regel == "archiv"


def test_commit_fehler_raeumt_eigene_kopie_auf(tmp_path, archiv, monkeypatch):
    # Der Commit scheitert (nur der öffentliche Schlüssel liegt vor, ohne privaten Teil und ohne
    # Agent wird nicht signiert; die Probesignatur vorab ist hier ausgeschaltet, damit der Fehler
    # erst beim Commit auftritt): die unfertige Kopie darf die Version nicht blockieren.
    from scanview_nachweise import signaturen
    monkeypatch.setattr(signaturen, "pruefe_signierschluessel", lambda *_: "")
    schluessel = _git(archiv, "config", "user.signingkey").strip()
    nur_oeffentlich = tmp_path / "nur-oeffentlich.pub"
    nur_oeffentlich.write_bytes(Path(schluessel + ".pub").read_bytes())
    _git(archiv, "config", "user.signingkey", str(nur_oeffentlich))
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", _quellen(tmp_path), archiv)
    assert e.value.regel == "archiv"
    assert not (archiv / "releases" / "0.1.0").exists()
    assert _git(archiv, "status", "--porcelain") == ""
    _git(archiv, "config", "user.signingkey", schluessel)
    (tmp_path / "zweit").mkdir()
    assert _archiviere("0.1.0", _quellen(tmp_path / "zweit"), archiv).is_dir()


# --- Vollständige Manifestprüfung vor dem Archivieren ------------------------------------------

def test_manifest_wird_voll_geprueft(tmp_path, archiv):
    quellen = _quellen(tmp_path)
    m = _manifest("0.1.0")
    m["pruefungen"] = {"scan": "gescheitert"}
    quellen[0].write_bytes(kanonisch(m))
    _signieren(quellen[0])
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", quellen, archiv)
    assert e.value.regel == "archiv" and "gescheitert" in e.value.text
    quellen[0].write_text('{"version": "0.1.0"}', encoding="utf-8")
    with pytest.raises(NachweisFehler, match="kanonisch"):
        _archiviere("0.1.0", quellen, archiv)


def test_releases_als_symlink_verweigert(tmp_path, archiv):
    anderswo = tmp_path / "anderswo"
    anderswo.mkdir()
    (archiv / "releases").symlink_to(anderswo)
    _git(archiv, "add", "releases")
    _git(archiv, "commit", "-q", "-S", "-m", "Symlink")
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", _quellen(tmp_path), archiv)
    assert e.value.regel == "archiv"
    assert list(anderswo.iterdir()) == []


def test_version_exklusiv_belegt(tmp_path, archiv, monkeypatch):
    # Wettlauf: zwischen Vorabprüfung und Anlegen legt jemand releases/0.1.0 an. Das exklusive
    # mkdir muss das erkennen und fremden Inhalt unangetastet lassen.
    original = archiv_modul._git
    fremd = archiv / "releases" / "0.1.0"

    def _git_mit_wettlauf(repo: Path, *args: str) -> str:
        ergebnis = original(repo, *args)
        if args[0] == "status":
            fremd.mkdir(parents=True)
            (fremd / "fremd.txt").write_text("fremd", encoding="utf-8")
        return ergebnis

    monkeypatch.setattr(archiv_modul, "_git", _git_mit_wettlauf)
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", _quellen(tmp_path), archiv)
    assert e.value.regel == "archiv-unveraenderlich"
    assert (fremd / "fremd.txt").read_text(encoding="utf-8") == "fremd"


def test_rest_eines_abgebrochenen_laufs(tmp_path, archiv):
    rest = archiv / "releases" / ".0.1.0.unfertig"
    rest.mkdir(parents=True)
    (rest / "x").write_text("x", encoding="utf-8")
    _git(archiv, "add", "releases")
    _git(archiv, "commit", "-q", "-S", "-m", "Rest")
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", _quellen(tmp_path), archiv)
    assert e.value.regel == "archiv-unveraenderlich"
    assert not (archiv / "releases" / "0.1.0").exists()
    assert (rest / "x").is_file()


# --- Signatur der bereitgestellten Kopie, Trockenlauf, allowed_signers -------------------------

def _sauber_ohne_version(archiv: Path) -> None:
    releases = archiv / "releases"
    assert not (releases / "0.1.0").exists()
    assert not (releases / ".0.1.0.unfertig").exists()
    assert _git(archiv, "status", "--porcelain") == ""


def test_fremde_signatur_verweigert(tmp_path, archiv):
    quellen = _quellen(tmp_path)
    fremd, _ = _neuer_schluessel(tmp_path / "fremd")
    _signieren(quellen[0], fremd)
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", quellen, archiv)
    assert e.value.regel == "archiv-signatur"
    _sauber_ohne_version(archiv)


def test_nach_signatur_veraendertes_manifest_verweigert(tmp_path, archiv):
    quellen = _quellen(tmp_path)
    m = _manifest("0.1.0")
    m["erstellt"] = "2026-09-28T00:00:00+00:00"  # kanonisch und formgültig, aber nicht signiert
    quellen[0].write_bytes(kanonisch(m))
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", quellen, archiv)
    assert e.value.regel == "archiv-signatur"
    _sauber_ohne_version(archiv)


def test_signatur_wird_an_der_kopie_geprueft(tmp_path, archiv, monkeypatch):
    # Austausch zwischen Vorabprüfung und Kopie: geprüft wird, was im Archiv landen würde.
    quellen = _quellen(tmp_path)
    m = _manifest("0.1.0")
    m["erstellt"] = "2026-09-28T00:00:00+00:00"
    untergeschoben = kanonisch(m)
    original = archiv_modul.shutil.copy2

    def kopie_mit_tausch(quelle, ziel, *args, **kwargs):
        ergebnis = original(quelle, ziel, *args, **kwargs)
        if Path(quelle).name == "manifest.json":
            Path(ziel).write_bytes(untergeschoben)
        return ergebnis

    monkeypatch.setattr(archiv_modul.shutil, "copy2", kopie_mit_tausch)
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", quellen, archiv)
    assert e.value.regel == "archiv-signatur"
    _sauber_ohne_version(archiv)


def test_trockenlauf_wird_nicht_archiviert(tmp_path, archiv):
    quellen = _quellen(tmp_path)
    m = _manifest("0.1.0")
    m["trockenlauf"] = True
    quellen[0].write_bytes(kanonisch(m))
    _signieren(quellen[0])
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", quellen, archiv)
    assert e.value.regel == "archiv" and "Trockenlauf" in e.value.text
    _sauber_ohne_version(archiv)


def test_allowed_signers_wird_mitarchiviert(tmp_path, archiv):
    ziel = _archiviere("0.1.0", _quellen(tmp_path), archiv)
    assert (ziel / "allowed_signers").read_bytes() == _SCHLUESSEL["signers"].read_bytes()


def test_allowed_signers_pflicht(tmp_path, archiv):
    quellen = [q for q in _quellen(tmp_path) if q.name != "allowed_signers"]
    with pytest.raises(NachweisFehler, match="allowed_signers"):
        _archiviere("0.1.0", quellen, archiv)
    _sauber_ohne_version(archiv)


def test_mitgeliefertes_allowed_signers_muss_dem_geprueften_gleichen(tmp_path, archiv):
    quellen = _quellen(tmp_path)
    _, anders = _neuer_schluessel(tmp_path / "anders")
    (tmp_path / "quellen-0.1.0" / "allowed_signers").write_bytes(anders.read_bytes())
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", quellen, archiv)
    assert e.value.regel == "archiv-signatur"
    _sauber_ohne_version(archiv)


def test_git_umgebung_des_aufrufers_wirkt_nicht(tmp_path, archiv, monkeypatch):
    """`GIT_DIR`/`GIT_INDEX_FILE` aus der Aufruferumgebung lenken die
    Archiv-Befehle nicht in ein anderes Repo bzw. einen anderen Index um."""
    fremd = tmp_path / "fremd"
    fremd.mkdir()
    _git(fremd, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(fremd / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "fremder-index"))
    ziel = _archiviere("0.1.0", _quellen(tmp_path), archiv)
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_INDEX_FILE")
    assert "releases/0.1.0/manifest.json" in _git(archiv, "ls-files")
    assert ziel.is_dir()


# --- Nachweise gegen das signierte Manifest ------------------------------------------------------

@pytest.mark.parametrize("relativ", ["artefakte/scanview-server-0.1.0.tar.gz",
                                     "sbom/scanview-mcp-0.1.0.cdx.json",
                                     "berichte/scan-ergebnis.json"])
def test_referenzierte_datei_fehlt(tmp_path, archiv, relativ):
    quellen = _quellen(tmp_path)
    (tmp_path / "quellen-0.1.0" / relativ).unlink()
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", quellen, archiv)
    assert e.value.regel == "archiv-nachweis" and Path(relativ).name in e.value.text
    _sauber_ohne_version(archiv)


@pytest.mark.parametrize("relativ", ["artefakte/scanview-web-0.1.0.tar.gz",
                                     "sbom/scanview-server-0.1.0.cdx.json",
                                     "berichte/scan-ergebnis.json"])
def test_referenzierte_datei_nach_signatur_veraendert(tmp_path, archiv, relativ):
    quellen = _quellen(tmp_path)
    (tmp_path / "quellen-0.1.0" / relativ).write_bytes(b"nachtraeglich")
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", quellen, archiv)
    assert e.value.regel == "archiv-nachweis" and Path(relativ).name in e.value.text
    _sauber_ohne_version(archiv)


def test_referenzierte_datei_mehrdeutig(tmp_path, archiv):
    quellen = _quellen(tmp_path)
    doppelt = tmp_path / "quellen-0.1.0" / "berichte" / "scanview-web-0.1.0.cdx.json"
    doppelt.write_bytes(b"anders")
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", quellen, archiv)
    assert e.value.regel == "archiv-nachweis" and "mehrfach" in e.value.text


def test_archiv_hooks_laufen_nie(tmp_path, archiv):
    """Hooks im Archiv (hier ein pre-commit, der eine Marker-Datei anlegt) laufen nicht."""
    marker = tmp_path / "hook-lief"
    hook = archiv / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    assert _archiviere("0.1.0", _quellen(tmp_path), archiv).is_dir()
    assert not marker.exists()


@pytest.mark.parametrize("wo", ["archiviere", "schreibe_pruefung"])
def test_filtertreiber_im_archiv_verweigert(tmp_path, archiv, wo):
    """Ein clean-Filter aus der Archiv-Konfiguration liefe bei `git add` als
    Programm — verweigert, bevor irgendetwas geschrieben wird."""
    marker = tmp_path / "filter-lief"
    (archiv / ".git" / "info").mkdir(exist_ok=True)
    (archiv / ".git" / "info" / "attributes").write_text("* filter=boese\n", encoding="utf-8")
    _git(archiv, "config", "filter.boese.clean", f"sh -c 'touch {marker}; cat'")
    with pytest.raises(NachweisFehler) as e:
        if wo == "archiviere":
            _archiviere("0.1.0", _quellen(tmp_path), archiv)
        else:
            schreibe_pruefung(archiv, "dast", "L", {"n": 1}, heute=HEUTE)
    assert "Filtertreiber" in e.value.text or "attributes" in e.value.text
    assert not marker.exists()
    assert not (archiv / "releases").exists() and not (archiv / "pruefungen").exists()


def test_commit_wird_gegen_die_kopie_geprueft(tmp_path, archiv, monkeypatch):
    """Der committete Baum muss byte-gleich zur geprüften Kopie sein — ein
    Filter (clean/smudge) im Archiv-Repo würde sonst unbemerkt andere Blobs committen."""
    (archiv / ".gitattributes").write_text("*.json filter=verfaelschen\n", encoding="utf-8")
    _git(archiv, "add", ".gitattributes")
    _git(archiv, "commit", "-q", "-S", "-m", "Filter")
    _git(archiv, "config", "filter.verfaelschen.clean", "sed s/sbom/SBOM/")
    # Die Filtersperre (siehe oben) ausgeschaltet: geprüft wird hier die letzte Verteidigung,
    # der Byte-Vergleich des Commits mit der geprüften Kopie.
    monkeypatch.setattr(archiv_modul, "keine_git_filter", lambda repo, regel: None)
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", _quellen(tmp_path), archiv)
    assert e.value.regel == "archiv-nachweis" and "Commit" in e.value.text


# --- schreibe_pruefung: Nachweise nach dem Release (pruefungen/<datum>[.<lauf>]-<art>[-<instanz>]/)

HEUTE = date(2026, 9, 28)


@pytest.fixture
def signiertes_archiv(tmp_path: Path) -> Path:
    key, zeile = signierhilfe.schluessel(tmp_path / "commit-schluessel")
    repo = signierhilfe.repo(tmp_path / "signiertes-archiv", key)
    signierhilfe.commit(repo, "start", signiert=True)
    signierhilfe.anker(repo, zeile, "a" * 40)
    return repo


def test_pruefung_schreiben_und_signiert_committen(signiertes_archiv):
    a = signiertes_archiv
    ziel = schreibe_pruefung(a, "dast", "L", {"ergebnis": "bestanden", "ä": 1},
                             {"zap-bericht.json": b"{}\n"}, heute=HEUTE)
    assert ziel == a / "pruefungen" / "2026-09-28-dast-L"
    assert (ziel / "ergebnis.json").read_bytes() == kanonisch({"ergebnis": "bestanden", "ä": 1})
    assert (ziel / "zap-bericht.json").read_bytes() == b"{}\n"
    assert signierhilfe.git(a, "log", "-1", "--format=%s") == "pruefung: dast L 2026-09-28"
    assert signierhilfe.git(a, "status", "--porcelain") == ""
    assert signierhilfe.git(a, "ls-tree", "-r", "--name-only", "HEAD", "--",
                            "pruefungen").splitlines() == [
        "pruefungen/2026-09-28-dast-L/ergebnis.json",
        "pruefungen/2026-09-28-dast-L/zap-bericht.json"]
    signers = a / "vertrauensanker" / "commit-signers"
    assert signierhilfe.git(a, "-c", f"gpg.ssh.allowedSignersFile={signers}", "log", "-1",
                            "--format=%G?") == "G"


def test_pruefung_ohne_instanz(signiertes_archiv):
    ziel = schreibe_pruefung(signiertes_archiv, "branchschutz", None, {"ok": True}, heute=HEUTE)
    assert ziel.name == "2026-09-28-branchschutz"
    assert sorted(p.name for p in ziel.iterdir()) == ["ergebnis.json"]
    assert signierhilfe.git(signiertes_archiv, "log", "-1", "--format=%s") == \
        "pruefung: branchschutz 2026-09-28"


def test_pruefung_zweiter_lauf_bekommt_laufnummer(signiertes_archiv):
    a = signiertes_archiv
    erster = schreibe_pruefung(a, "updates", "S", {"n": 1}, heute=HEUTE)
    zweiter = schreibe_pruefung(a, "updates", "S", {"n": 2}, heute=HEUTE)
    dritter = schreibe_pruefung(a, "updates", "S", {"n": 3}, heute=HEUTE)
    assert [erster.name, zweiter.name, dritter.name] == [
        "2026-09-28-updates-S", "2026-09-28.2-updates-S", "2026-09-28.3-updates-S"]
    assert signierhilfe.git(a, "log", "-1", "--format=%s") == "pruefung: updates S 2026-09-28.3"
    # Der erste Nachweis bleibt unverändert stehen.
    assert (erster / "ergebnis.json").read_bytes() == kanonisch({"n": 1})
    assert (zweiter / "ergebnis.json").read_bytes() == kanonisch({"n": 2})
    # Andere Art oder Instanz am selben Tag zählt eigene Läufe.
    assert schreibe_pruefung(a, "updates", "L", {"n": 1}, heute=HEUTE).name == \
        "2026-09-28-updates-L"


def test_pruefung_je_im_archiv_gestanden_wird_uebersprungen(signiertes_archiv):
    a = signiertes_archiv
    schreibe_pruefung(a, "wochenscan", None, {"n": 1}, heute=HEUTE)
    signierhilfe.git(a, "rm", "-q", "-r", "pruefungen")
    signierhilfe.git(a, "commit", "-q", "-S", "-m", "entfernt")
    # Der Name stand je im Archiv: er wird nicht wieder belegt, der Lauf bekommt die nächste Nummer.
    ziel = schreibe_pruefung(a, "wochenscan", None, {"n": 2}, heute=HEUTE)
    assert ziel.name == "2026-09-28.2-wochenscan"
    assert not (a / "pruefungen" / "2026-09-28-wochenscan").exists()


def test_pruefung_fremder_ordner_bricht_ab(signiertes_archiv):
    a = signiertes_archiv
    # Ein uncommitteter Eintrag mit dem Namen des ersten Laufs wird nie belegt; der unsaubere
    # Arbeitsbaum bricht ab, bevor etwas geschrieben wird.
    (a / "pruefungen").mkdir()
    (a / "pruefungen" / "2026-09-28-dast-L").symlink_to(a)
    with pytest.raises(NachweisFehler, match="nicht sauber"):
        schreibe_pruefung(a, "dast", "L", {"n": 1}, heute=HEUTE)


def test_pruefungsname_und_muster():
    from scanview_nachweise.archiv import PRUEFUNGSNAME, pruefungsname
    assert pruefungsname(HEUTE, "dast", "L", 1) == "2026-09-28-dast-L"
    assert pruefungsname(HEUTE, "dast", "L", 2) == "2026-09-28.2-dast-L"
    assert pruefungsname(HEUTE, "wochenscan", None, 12) == "2026-09-28.12-wochenscan"
    for lauf in (0, -1, True):
        with pytest.raises(NachweisFehler):
            pruefungsname(HEUTE, "dast", "L", lauf)
    for name, teile in [
        ("2026-09-28-dast-L", ("2026-09-28", None, "dast", "L")),
        ("2026-09-28.2-dast-L", ("2026-09-28", "2", "dast", "L")),
        ("2026-09-28.10-wochenscan", ("2026-09-28", "10", "wochenscan", None)),
        ("2026-09-28-dast-L-2", ("2026-09-28", None, "dast", "L-2")),
    ]:
        treffer = PRUEFUNGSNAME.fullmatch(name)
        assert treffer is not None and treffer.groups() == teile, name
    for name in ("2026-09-28.1-dast-L", "2026-09-28.0-dast-L", "2026-09-28.02-dast-L",
                 "2026-09-28-dast-L.2", "2026-09-28.-dast-L"):
        assert PRUEFUNGSNAME.fullmatch(name) is None, name


@pytest.mark.parametrize(("art", "instanz"), [
    ("../x", None), ("dast", "../x"), ("dast/x", None), ("dast", "L/x"), ("DAST", None),
    ("unbekannt", None), ("", None), ("dast", ""), ("dast", "L.S"), ("dast", "-"),
])
def test_pruefung_art_und_instanz_nur_erlaubte_namen(signiertes_archiv, art, instanz):
    with pytest.raises(NachweisFehler) as e:
        schreibe_pruefung(signiertes_archiv, art, instanz, {"n": 1}, heute=HEUTE)
    assert e.value.regel == "archiv"
    assert not (signiertes_archiv / "pruefungen").exists()


@pytest.mark.parametrize("art", ["dast", "updates", "securitytxt", "branchschutz", "wochenscan",
                                 "aufbewahrung", "renovate"])
def test_pruefung_alle_arten(signiertes_archiv, art):
    assert schreibe_pruefung(signiertes_archiv, art, "S", {"n": 1}, heute=HEUTE).is_dir()


@pytest.mark.parametrize("name", ["ergebnis.json", "../x", "a/b", ".versteckt", "", ".."])
def test_pruefung_dateinamen(signiertes_archiv, name):
    with pytest.raises(NachweisFehler, match="archiv"):
        schreibe_pruefung(signiertes_archiv, "dast", "L", {"n": 1}, {name: b"x"}, heute=HEUTE)
    assert not (signiertes_archiv / "pruefungen").exists()


def test_pruefung_daten_nicht_json(signiertes_archiv):
    with pytest.raises(NachweisFehler, match="archiv"):
        schreibe_pruefung(signiertes_archiv, "dast", "L", {"zeit": date(2026, 1, 1)},
                          heute=HEUTE)
    assert not (signiertes_archiv / "pruefungen").exists()


def test_pruefung_arbeitsbaum_nicht_sauber(signiertes_archiv):
    (signiertes_archiv / "fremd.txt").write_text("x", encoding="utf-8")
    with pytest.raises(NachweisFehler, match="nicht sauber"):
        schreibe_pruefung(signiertes_archiv, "dast", "L", {"n": 1}, heute=HEUTE)


def test_pruefung_ohne_anker(archiv_ohne_anker):
    with pytest.raises(NachweisFehler) as e:
        schreibe_pruefung(archiv_ohne_anker, "dast", "L", {"n": 1}, heute=HEUTE)
    assert e.value.regel == "commit-signatur-anker"
    assert not (archiv_ohne_anker / "pruefungen").exists()


# --- archiviere: signierte Commits (Review I6) --------------------------------------------------

def test_archivieren_commit_signiert(tmp_path, archiv):
    _archiviere("0.1.0", _quellen(tmp_path), archiv)
    anker = archiv / "vertrauensanker" / "commit-signers"
    assert _git(archiv, "-c", f"gpg.ssh.allowedSignersFile={anker}", "log", "-1",
                "--format=%G?").strip() == "G"


def test_archivieren_ohne_anker(tmp_path, archiv_ohne_anker):
    with pytest.raises(NachweisFehler) as e:
        _archiviere("0.1.0", _quellen(tmp_path), archiv_ohne_anker)
    assert e.value.regel == "commit-signatur-anker"
    assert not (archiv_ohne_anker / "releases").exists()


def test_archivieren_ohne_signierschluessel_kein_commit(tmp_path, archiv):
    _git(archiv, "config", "user.signingkey", str(tmp_path / "gibtsnicht"))
    kopf = _git(archiv, "rev-parse", "HEAD")
    with pytest.raises(NachweisFehler, match="archiv"):
        _archiviere("0.1.0", _quellen(tmp_path), archiv)
    assert _git(archiv, "rev-parse", "HEAD") == kopf
    assert not (archiv / "releases" / "0.1.0").exists()
    assert _git(archiv, "status", "--porcelain") == ""


def test_archivieren_fremd_signiert_erkannt(tmp_path, archiv):
    fremd, _ = signierhilfe.schluessel(tmp_path / "fremd")
    _git(archiv, "config", "user.signingkey", str(fremd))
    kopf = _git(archiv, "rev-parse", "HEAD")
    with pytest.raises(NachweisFehler, match="nicht im Vertrauensanker") as e:
        _archiviere("0.1.0", _quellen(tmp_path), archiv)
    assert e.value.regel == "commit-signatur"
    # Vor dem Commit erkannt: kein Commit, der jedes weitere Schreiben sperren würde.
    assert _git(archiv, "rev-parse", "HEAD") == kopf
    assert not (archiv / "releases" / "0.1.0").exists()


def test_pruefung_ohne_signierschluessel_nichts_committet(signiertes_archiv):
    """Ohne Signierschlüssel entsteht kein unsignierter Archiv-Commit — der Lauf bricht ab
    und hinterlässt keine halbe Prüfung."""
    a = signiertes_archiv
    signierhilfe.git(a, "config", "user.signingkey", str(a.parent / "gibtsnicht"))
    kopf = signierhilfe.git(a, "rev-parse", "HEAD")
    with pytest.raises(NachweisFehler, match="nicht signierbar") as e:
        schreibe_pruefung(a, "dast", "L", {"n": 1}, heute=HEUTE)
    assert e.value.regel == "commit-signatur"
    assert signierhilfe.git(a, "rev-parse", "HEAD") == kopf
    assert not (a / "pruefungen" / "2026-09-28-dast-L").exists()
    assert signierhilfe.git(a, "status", "--porcelain") == ""


def test_pruefung_fremd_signiert_wird_vor_dem_commit_erkannt(signiertes_archiv, tmp_path):
    """Ist ein Schlüssel konfiguriert, der nicht im Anker steht, bricht es vor dem Commit ab:
    ein fremd signierter Commit bliebe stehen (nur anhängen) und sperrte als Elternstand jedes
    weitere Schreiben, bis er von Hand zurückgesetzt ist."""
    a = signiertes_archiv
    fremd, _ = signierhilfe.schluessel(tmp_path / "fremd")
    signierhilfe.git(a, "config", "user.signingkey", str(fremd))
    kopf = signierhilfe.git(a, "rev-parse", "HEAD")
    with pytest.raises(NachweisFehler, match="nicht im Vertrauensanker") as e:
        schreibe_pruefung(a, "dast", "L", {"n": 1}, heute=HEUTE)
    assert e.value.regel == "commit-signatur"
    assert signierhilfe.git(a, "rev-parse", "HEAD") == kopf
    assert signierhilfe.git(a, "status", "--porcelain") == ""
    assert not (a / "pruefungen").exists()


def test_betriebsdatei_fremd_signiert_wird_vor_dem_commit_erkannt(signiertes_archiv, tmp_path):
    a = signiertes_archiv
    fremd, _ = signierhilfe.schluessel(tmp_path / "fremd")
    signierhilfe.git(a, "config", "user.signingkey", str(fremd))
    kopf = signierhilfe.git(a, "rev-parse", "HEAD")
    with pytest.raises(NachweisFehler, match="nicht im Vertrauensanker"):
        schreibe_betrieb(a, "unterstuetzt.toml", b"x = 1\n", "betrieb: test")
    assert signierhilfe.git(a, "rev-parse", "HEAD") == kopf
    assert signierhilfe.git(a, "status", "--porcelain") == ""


def test_pruefungen_symlink_verweigert(signiertes_archiv, tmp_path):
    woanders = tmp_path / "woanders"
    woanders.mkdir()
    (signiertes_archiv / "pruefungen").symlink_to(woanders)
    signierhilfe.git(signiertes_archiv, "add", "pruefungen")
    signierhilfe.git(signiertes_archiv, "commit", "-q", "-S", "-m", "symlink")
    with pytest.raises(NachweisFehler, match="archiv"):
        schreibe_pruefung(signiertes_archiv, "dast", "L", {"n": 1}, heute=HEUTE)
    assert list(woanders.iterdir()) == []
