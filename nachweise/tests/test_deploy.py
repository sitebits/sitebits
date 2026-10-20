"""Deploy nur mit geprüftem Release: `release-pruefen.sh`
und der Prüf-/Entpack-Helfer `deploy/platform/release_pruefen.py`, der auf dem Zielhost **ohne**
das Paket `scanview_nachweise` läuft (nur Standardbibliothek).

Die Release-Verzeichnisse entstehen mit den echten Manifest-Funktionen (`baue_manifest`,
`schreibe_manifest`, `signiere`) und einem Wegwerf-Schlüssel in tmp_path — nie mit dem echten
Release-Schlüssel. Manipulierte Tarballs werden **vor** dem Signieren gebaut, damit die Prüfung
an der Tar-Inspektion scheitert und nicht schon an der Prüfsumme."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pflicht import auf_schema_1, berichte_dateien, werkzeuge

from scanview_nachweise import abschluss, manifest
from scanview_nachweise.artefakte import Artefakt, Netzschritt
from scanview_nachweise.manifest import baue_manifest, kanonisch, schreibe_manifest, signiere
from scanview_nachweise.scan import DbStand
from scanview_nachweise.werkzeuge import docker_befehl, lade_werkzeuge

REPO_WURZEL = Path(__file__).resolve().parents[2]
PLATFORM = REPO_WURZEL / "deploy" / "platform"
SKRIPT = PLATFORM / "release-pruefen.sh"
HELFER = PLATFORM / "release_pruefen.py"
EINRICHTEN = PLATFORM / "einrichten.sh"
#: Interpreter für den Helfer; auf dem Zielhost läuft Debian-Python 3.11 — zum Gegenprüfen
#: `SCANVIEW_TEST_PYTHON=<python3.11> pytest tests/test_deploy.py`.
PYTHON = os.environ.get("SCANVIEW_TEST_PYTHON", sys.executable)

VERSION = "0.1.0"
QUELLE = "a" * 40
ZEIT = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
DB = DbStand(ZEIT, "c" * 64, "UpdatedAt", None)
WERKZEUGE = werkzeuge()
PRUEFUNGEN = {n: "bestanden" for n in manifest.PFLICHT_PRUEFUNGEN}
EXPORT = "e" * 40
UMGEBUNG = {"node": "v22.20.0", "npm": "10.9.3", "python": "3.11.14", "uv": "uv 0.11.3",
            "docker": "28.4.0", "architektur": "x86_64"}

# Eintrag: (Name, Inhalt oder None für Ordner, Modus, Typ, Linkziel)
Eintrag = tuple[str, bytes | None, int, bytes, str]


def _datei(name: str, inhalt: bytes = b"x\n", modus: int = 0o644) -> Eintrag:
    return (name, inhalt, modus, tarfile.REGTYPE, "")


def _ordner(name: str) -> Eintrag:
    return (name, None, 0o755, tarfile.DIRTYPE, "")


STANDARD: dict[str, list[Eintrag]] = {
    "web": [_ordner("web"), _ordner("web/dist"),
            _datei("web/dist/index.html", b"<!doctype html>\n"),
            _datei("web/dist/portal.html", b"<!doctype html>\n"),
            _ordner("web/dist/assets"), _datei("web/dist/assets/a.js", b"1;\n")],
    "server": [_ordner("deploy"), _ordner("deploy/platform"),
               _datei("deploy/platform/einrichten.sh", b"#!/bin/sh\n", 0o755),
               _datei("deploy/platform/scanview-server.service", b"[Unit]\n"),
               _ordner("server"), _datei("server/pyproject.toml", b"[project]\n"),
               _datei("server/uv.lock", b"version = 1\n")],
    "mcp": [_ordner("mcp-server"), _datei("mcp-server/pyproject.toml", b"[project]\n")],
    "import": [_ordner("import"), _datei("import/pyproject.toml", b"[project]\n")],
}


def _tar(eintraege: list[Eintrag]) -> bytes:
    roh = io.BytesIO()
    with tarfile.open(fileobj=roh, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        for name, inhalt, modus, typ, ziel in eintraege:
            info = tarfile.TarInfo(name)
            info.type = typ
            info.mode = modus
            info.linkname = ziel
            info.mtime = 1_700_000_000
            if inhalt is not None and typ == tarfile.REGTYPE:
                info.size = len(inhalt)
                tar.addfile(info, io.BytesIO(inhalt))
            else:
                tar.addfile(info)
    return roh.getvalue()


def _schluessel(ordner: Path, name: str = "test") -> tuple[Path, Path]:
    """Wegwerf-ed25519 (stdin aus DEVNULL, `-f` explizit) und passende allowed_signers."""
    ordner.mkdir(parents=True, exist_ok=True)
    privat = ordner / f"{name}_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f",
                    str(privat)], check=True, stdin=subprocess.DEVNULL, capture_output=True)
    oeffentlich = privat.with_suffix(".pub").read_text().strip()
    signers = ordner / f"{name}_allowed_signers"
    signers.write_text(f'{manifest.KENNUNG} namespaces="{manifest.NAMESPACE},'
                       f'{abschluss.NAMESPACE}" {oeffentlich}\n')
    return privat, signers


@pytest.fixture(scope="module")
def schluessel(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    return _schluessel(tmp_path_factory.mktemp("schluessel"))


@pytest.fixture(scope="module")
def fremd(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    return _schluessel(tmp_path_factory.mktemp("fremd"), "fremd")


def _signiere_abschluss(ziel: Path, privat: Path,
                        aendern: Callable[[dict[str, Any]], None] | None = None,
                        namespace: str = abschluss.NAMESPACE) -> None:
    """abschluss.json (+ .sig) zum Manifest in `ziel`, wie abschluss.abschliessen ihn schreibt."""
    roh = (ziel / "manifest.json").read_bytes()
    m = json.loads(roh)
    daten = {"schema": abschluss.SCHEMA, "version": m.get("version"),
             "manifest_sha256": hashlib.sha256(roh).hexdigest(),
             "quellcommit": m.get("quellcommit"), "exportcommit": m.get("exportcommit"),
             "archiv_commit": "f" * 40, "tag": f"v{m.get('version')}",
             "erstellt": ZEIT.isoformat()}
    if aendern is not None:
        aendern(daten)
    pfad = ziel / "abschluss.json"
    pfad.write_bytes(kanonisch(daten))
    (ziel / "abschluss.json.sig").unlink(missing_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(privat), "-n", namespace,
                    str(pfad)], check=True, stdin=subprocess.DEVNULL, capture_output=True)


def _baue_release(ziel: Path, privat: Path, *,
                  inhalte: dict[str, list[Eintrag]] | None = None,
                  trockenlauf: bool = False,
                  aendern: Callable[[dict[str, Any]], None] | None = None) -> Path:
    """Release-Verzeichnis im Deploy-Format: manifest.json, manifest.json.sig, abschluss.json,
    abschluss.json.sig und artefakte/ mit genau den drei ausgelieferten Tarballs. `aendern`
    verändert das Manifest-dict vor dem Schreiben (dann kanonisch geschrieben und direkt per
    ssh-keygen signiert)."""
    bau = ziel.parent / f"{ziel.name}-bau"
    bau.mkdir(parents=True)
    artefakte: list[Artefakt] = []
    for name in ("web", "server", "mcp", "import"):
        daten = _tar((inhalte or {}).get(name, STANDARD[name]))
        pfad = bau / f"scanview-{name}-{VERSION}.tar.gz"
        pfad.write_bytes(daten)
        artefakte.append(Artefakt(name, pfad, hashlib.sha256(daten).hexdigest(),
                                  hashlib.sha512(daten).hexdigest(), []))
    sboms = []
    for name in ("web", "server", "mcp", "import"):
        sboms.append(bau / f"scanview-{name}-{VERSION}.cdx.json")
        sboms[-1].write_text(f'{{"sbom":"{name}"}}', encoding="utf-8")
    daten_manifest = baue_manifest(
        VERSION, QUELLE, EXPORT, artefakte, sboms, berichte_dateien(bau / "berichte"),
        WERKZEUGE, DB, PRUEFUNGEN,
        ausgeliefert=manifest.AUSGELIEFERT, umgebung=UMGEBUNG, trockenlauf=trockenlauf,
        veroeffentlichbar=True, netzschritte=[Netzschritt(["npm", "ci"], "web", ZEIT.isoformat())],
        erstellt=ZEIT)
    (ziel / "artefakte").mkdir(parents=True)
    m = ziel / "manifest.json"
    if aendern is None:
        schreibe_manifest(daten_manifest, m)
    else:
        aendern(daten_manifest)
        m.write_bytes(kanonisch(daten_manifest))
    signiere(m, privat)
    _signiere_abschluss(ziel, privat)
    for a in artefakte:
        if a.name in manifest.AUSGELIEFERT:
            shutil.copyfile(a.pfad, ziel / "artefakte" / a.pfad.name)
    return ziel


def _pruefen(release: Path, signers: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SKRIPT), str(release), str(signers)],
                          capture_output=True, text=True, check=False,
                          stdin=subprocess.DEVNULL,
                          env={**os.environ, "SCANVIEW_PYTHON": PYTHON})


def _entpacken(release: Path, signers: Path, ziel: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PYTHON, "-I", "-B", str(HELFER), "entpacken", str(release),
                           str(signers), str(ziel)],
                          capture_output=True, text=True, check=False,
                          stdin=subprocess.DEVNULL)


def _abgelehnt(ergebnis: subprocess.CompletedProcess[str], regel: str) -> None:
    assert ergebnis.returncode != 0, ergebnis.stdout
    assert f"NACHWEIS-FEHLER {regel}:" in ergebnis.stderr, ergebnis.stderr


# --- release-pruefen.sh ------------------------------------------------------------------------

def test_gueltiges_release(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    ergebnis = _pruefen(release, schluessel[1])
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert f"version={VERSION}" in ergebnis.stdout
    roh = (release / "manifest.json").read_bytes()
    assert f"manifest_sha256={hashlib.sha256(roh).hexdigest()}" in ergebnis.stdout
    assert f"quellcommit={QUELLE}" in ergebnis.stdout


@pytest.mark.parametrize("name", ["web", "server", "mcp"])
def test_ein_byte_im_tarball(tmp_path: Path, schluessel: tuple[Path, Path], name: str) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    pfad = release / "artefakte" / f"scanview-{name}-{VERSION}.tar.gz"
    daten = bytearray(pfad.read_bytes())
    daten[len(daten) // 2] ^= 0x01
    pfad.write_bytes(bytes(daten))
    _abgelehnt(_pruefen(release, schluessel[1]), "artefakt-hash")


def test_manifest_veraendert(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    m = release / "manifest.json"
    daten = json.loads(m.read_bytes())
    daten["quellcommit"] = "b" * 40
    m.write_bytes(kanonisch(daten))  # gültig und kanonisch — nur die Signatur passt nicht
    _abgelehnt(_pruefen(release, schluessel[1]), "signatur")


def test_fremder_schluessel(tmp_path: Path, schluessel: tuple[Path, Path],
                            fremd: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", fremd[0])
    _abgelehnt(_pruefen(release, schluessel[1]), "signatur")


def test_falscher_namespace(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    sig = release / "manifest.json.sig"
    sig.unlink()
    subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(schluessel[0]), "-n", "git",
                    str(release / "manifest.json")], check=True, stdin=subprocess.DEVNULL,
                   capture_output=True)
    _abgelehnt(_pruefen(release, schluessel[1]), "signatur")


def test_falsche_kennung(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    signers = tmp_path / "andere_kennung"
    signers.write_text(schluessel[1].read_text().replace(manifest.KENNUNG, "jemand@anders"))
    _abgelehnt(_pruefen(release, signers), "signatur")


def test_signatur_fehlt(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    (release / "manifest.json.sig").unlink()
    _abgelehnt(_pruefen(release, schluessel[1]), "release-verzeichnis")


def test_allowed_signers_fehlt(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    _abgelehnt(_pruefen(release, tmp_path / "gibt-es-nicht"), "signatur")


def test_allowed_signers_aus_dem_release_verweigert(tmp_path: Path,
                                                    fremd: tuple[Path, Path]) -> None:
    """Das Release darf seinen eigenen Vertrauensanker nicht mitbringen."""
    release = _baue_release(tmp_path / "rel", fremd[0])
    eigene = release / "artefakte" / "allowed_signers"
    shutil.copyfile(fremd[1], eigene)
    _abgelehnt(_pruefen(release, eigene), "signatur")


def test_trockenlauf_abgelehnt(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0], trockenlauf=True)
    ergebnis = _pruefen(release, schluessel[1])
    _abgelehnt(ergebnis, "manifest")
    assert "Trockenlauf" in ergebnis.stderr


@pytest.mark.parametrize("feld", ["umgebung", "trockenlauf", "pruefungen", "artefakte",
                                  "quellcommit", "schema"])
def test_pflichtfeld_fehlt(tmp_path: Path, schluessel: tuple[Path, Path], feld: str) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0],
                            aendern=lambda d: d.pop(feld))
    _abgelehnt(_pruefen(release, schluessel[1]), "manifest")


@pytest.mark.parametrize("aendern", [
    lambda m: m.update(sboms=None),
    lambda m: m.update(berichte=None),
    lambda m: m.update(db_stand=None),
    lambda m: m.update(exportcommit=None),
    lambda m: m.update(pruefungen={"irgendwas": "nicht-anwendbar"}),
    lambda m: m["pruefungen"].update(scan="nicht-anwendbar"),
    lambda m: m.pop("veroeffentlichung"),
    lambda m: m["sboms"].pop("scanview-server-0.1.0.cdx.json"),
    lambda m: m["db_stand"].update(sha256="kurz"),
    lambda m: m.update(werkzeuge={}),
    lambda m: m["berichte"].pop("stufe-scan"),
    lambda m: m["werkzeuge"].pop("gitleaks"),
], ids=["sboms-null", "berichte-null", "db-null", "export-null", "pruefungen-beliebig",
        "scan-nicht-anwendbar", "ohne-veroeffentlichung", "server-sbom-fehlt", "db-hash",
        "ohne-werkzeuge", "ein-bericht-fehlt", "ein-werkzeug-fehlt"])
def test_unvollstaendiger_inhalt_abgelehnt(tmp_path: Path, schluessel: tuple[Path, Path],
                                          aendern: Callable[[dict[str, Any]], None]) -> None:
    """Gültige Signatur macht fehlende Nachweise nicht vollständig — der Host prüft
    denselben Inhalt wie manifest._parse (Drift: dieselben Fälle in test_manifest)."""
    release = _baue_release(tmp_path / "rel", schluessel[0], aendern=aendern)
    _abgelehnt(_pruefen(release, schluessel[1]), "manifest")


def test_schema_1_ohne_commit_signatur_angenommen(tmp_path: Path,
                                                  schluessel: tuple[Path, Path]) -> None:
    """Archivierte Releases mit Schema /1 (0.1.0) bleiben deploybar."""
    release = _baue_release(tmp_path / "rel", schluessel[0], aendern=auf_schema_1)
    ergebnis = _pruefen(release, schluessel[1])
    assert ergebnis.returncode == 0, ergebnis.stderr


def test_schema_2_ohne_commit_signatur_abgelehnt(tmp_path: Path,
                                                 schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0],
                            aendern=lambda d: d["pruefungen"].pop("commit-signatur"))
    ergebnis = _pruefen(release, schluessel[1])
    _abgelehnt(ergebnis, "manifest")
    assert "commit-signatur" in ergebnis.stderr


def test_altes_schema_abgelehnt(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0],
                            aendern=lambda d: d.update(schema="scanview-release-manifest/0"))
    _abgelehnt(_pruefen(release, schluessel[1]), "manifest")


def test_nicht_kanonisch_abgelehnt(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    m = release / "manifest.json"
    daten = json.loads(m.read_bytes())
    m.write_bytes(json.dumps(daten, indent=1).encode())
    (release / "manifest.json.sig").unlink()
    signiere(m, schluessel[0])
    _abgelehnt(_pruefen(release, schluessel[1]), "manifest")


def _setze_ausgeliefert(name: str, wert: bool) -> Callable[[dict[str, Any]], None]:
    def aendern(d: dict[str, Any]) -> None:
        for a in d["artefakte"]:
            if a["name"] == name:
                a["ausgeliefert"] = wert
    return aendern


@pytest.mark.parametrize(("name", "wert"), [("mcp", False), ("import", True)])
def test_auslieferungsmenge_fest(tmp_path: Path, schluessel: tuple[Path, Path], name: str,
                                 wert: bool) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0],
                            aendern=_setze_ausgeliefert(name, wert))
    _abgelehnt(_pruefen(release, schluessel[1]), "manifest")


def test_artefakt_dateiname_fest(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    def aendern(d: dict[str, Any]) -> None:
        for a in d["artefakte"]:
            if a["name"] == "web":
                a["datei"] = "../web.tar.gz"
    release = _baue_release(tmp_path / "rel", schluessel[0], aendern=aendern)
    _abgelehnt(_pruefen(release, schluessel[1]), "manifest")


def test_zusaetzliche_datei_im_release(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    (release / "notiz.txt").write_text("x")
    _abgelehnt(_pruefen(release, schluessel[1]), "release-verzeichnis")


def test_zusaetzliches_artefakt(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    """Auch das nicht ausgelieferte import-Artefakt gehört nicht ins Deploy-Verzeichnis."""
    release = _baue_release(tmp_path / "rel", schluessel[0])
    shutil.copyfile(tmp_path / "rel-bau" / f"scanview-import-{VERSION}.tar.gz",
                    release / "artefakte" / f"scanview-import-{VERSION}.tar.gz")
    _abgelehnt(_pruefen(release, schluessel[1]), "release-verzeichnis")


def test_artefakt_fehlt(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    (release / "artefakte" / f"scanview-mcp-{VERSION}.tar.gz").unlink()
    _abgelehnt(_pruefen(release, schluessel[1]), "release-verzeichnis")


def test_artefakt_als_symlink(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    pfad = release / "artefakte" / f"scanview-web-{VERSION}.tar.gz"
    kopie = tmp_path / "woanders.tar.gz"
    shutil.move(pfad, kopie)
    pfad.symlink_to(kopie)
    _abgelehnt(_pruefen(release, schluessel[1]), "release-verzeichnis")


BOESE: dict[str, tuple[str, list[Eintrag]]] = {
    "punktpunkt": ("server", [*STANDARD["server"], _datei("server/../../x")]),
    "absolut": ("server", [*STANDARD["server"], _datei("/tmp/scanview-boese")]),
    "symlink": ("web", [*STANDARD["web"],
                        ("web/dist/link", None, 0o777, tarfile.SYMTYPE, "/etc/passwd")]),
    "hardlink": ("web", [*STANDARD["web"],
                         ("web/dist/hart", None, 0o644, tarfile.LNKTYPE, "web/dist/index.html")]),
    "geraet": ("server", [*STANDARD["server"],
                          ("server/null", None, 0o666, tarfile.CHRTYPE, "")]),
    "fifo": ("server", [*STANDARD["server"], ("server/rohr", None, 0o644, tarfile.FIFOTYPE, "")]),
    "ausserhalb": ("server", [*STANDARD["server"], _datei("etc/cron.d/x")]),
    "setuid": ("server", [*STANDARD["server"], _datei("server/s", modus=0o4755)]),
    "doppelt": ("web", [*STANDARD["web"], _datei("web/dist/index.html", b"anders\n")]),
    "mcp-ausserhalb": ("mcp", [*STANDARD["mcp"], _datei("server/untergeschoben.py")]),
}


@pytest.mark.parametrize("fall", sorted(BOESE))
def test_unsicherer_tar_inhalt(tmp_path: Path, schluessel: tuple[Path, Path], fall: str) -> None:
    name, eintraege = BOESE[fall]
    release = _baue_release(tmp_path / "rel", schluessel[0], inhalte={name: eintraege})
    _abgelehnt(_pruefen(release, schluessel[1]), "tar-inhalt")
    ziel = tmp_path / "ziel"
    _abgelehnt(_entpacken(release, schluessel[1], ziel), "tar-inhalt")
    assert not Path("/tmp/scanview-boese").exists()
    assert not (tmp_path / "x").exists()


# --- entpacken (einrichten.sh --release) -------------------------------------------------------

def test_entpacken(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    ziel = tmp_path / "ziel"
    ergebnis = _entpacken(release, schluessel[1], ziel)
    assert ergebnis.returncode == 0, ergebnis.stderr
    felder = dict(zeile.split("=", 1) for zeile in ergebnis.stdout.splitlines())
    roh = (release / "manifest.json").read_bytes()
    daten = json.loads(roh)
    assert felder["version"] == VERSION
    assert felder["manifest_sha256"] == hashlib.sha256(roh).hexdigest()
    assert felder["quellcommit"] == QUELLE
    sha = {a["name"]: a["sha256"] for a in daten["artefakte"]}
    assert felder["artefakte"] == f"web:{sha['web']},server:{sha['server']},mcp:{sha['mcp']}"
    baum = Path(felder["baum"])
    assert baum.parent == ziel
    assert (baum / "web/dist/index.html").read_bytes() == b"<!doctype html>\n"
    assert (baum / "server/uv.lock").is_file()
    assert (baum / "deploy/platform/scanview-server.service").is_file()
    # MCP läuft beim Nutzer, nicht auf dem Host: geprüft, aber nicht entpackt.
    assert not (baum / "mcp-server").exists()
    assert stat.S_IMODE((baum / "deploy/platform/einrichten.sh").stat().st_mode) == 0o755
    assert stat.S_IMODE((baum / "server/uv.lock").stat().st_mode) == 0o644
    assert stat.S_IMODE(ziel.stat().st_mode) == 0o700


def test_entpacken_liest_nur_private_kopien(tmp_path: Path,
                                            schluessel: tuple[Path, Path]) -> None:
    """Entpackt wird aus der geprüften Kopie im Ziel; die Kopien bleiben dort nicht liegen."""
    release = _baue_release(tmp_path / "rel", schluessel[0])
    ziel = tmp_path / "ziel"
    assert _entpacken(release, schluessel[1], ziel).returncode == 0
    assert sorted(p.name for p in ziel.iterdir()) == ["baum"]


def test_entpacken_ziel_existiert(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    ziel = tmp_path / "ziel"
    ziel.mkdir()
    _abgelehnt(_entpacken(release, schluessel[1], ziel), "release-ziel")


def test_entpacken_prueft_vorher(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    pfad = release / "artefakte" / f"scanview-server-{VERSION}.tar.gz"
    pfad.write_bytes(pfad.read_bytes() + b"\0")
    ziel = tmp_path / "ziel"
    _abgelehnt(_entpacken(release, schluessel[1], ziel), "artefakt-hash")
    assert not (ziel / "baum").exists()


def _stdout_felder(ergebnis: subprocess.CompletedProcess[str]) -> dict[str, str]:
    return dict(z.split("=", 1) for z in ergebnis.stdout.splitlines() if "=" in z)


def test_abgelehnt_meldet_grund_und_bekannte_felder(tmp_path: Path,
                                                     schluessel: tuple[Path, Path]) -> None:
    """Für die Zeile `ergebnis=abgelehnt grund=<regel>` im Update-Protokoll: nach geprüfter
    Signatur sind Version, Manifest-Hash und Quellcommit bekannt."""
    release = _baue_release(tmp_path / "rel", schluessel[0])
    pfad = release / "artefakte" / f"scanview-web-{VERSION}.tar.gz"
    pfad.write_bytes(pfad.read_bytes() + b"\0")
    ergebnis = _entpacken(release, schluessel[1], tmp_path / "ziel")
    _abgelehnt(ergebnis, "artefakt-hash")
    felder = _stdout_felder(ergebnis)
    roh = (release / "manifest.json").read_bytes()
    assert felder == {"grund": "artefakt-hash", "version": VERSION,
                      "manifest_sha256": hashlib.sha256(roh).hexdigest(),
                      "quellcommit": QUELLE}


def test_abgelehnt_vor_signatur_ohne_version(tmp_path: Path, schluessel: tuple[Path, Path],
                                             fremd: tuple[Path, Path]) -> None:
    """Ungeprüfter Inhalt wird nicht als Version protokolliert — nur der Hash der vorgelegten
    Manifest-Bytes."""
    release = _baue_release(tmp_path / "rel", fremd[0])
    ergebnis = _entpacken(release, schluessel[1], tmp_path / "ziel")
    _abgelehnt(ergebnis, "signatur")
    roh = (release / "manifest.json").read_bytes()
    assert _stdout_felder(ergebnis) == {"grund": "signatur",
                                        "manifest_sha256": hashlib.sha256(roh).hexdigest()}


def test_abgelehnt_ohne_manifest(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    (release / "notiz").write_text("x")
    ergebnis = _entpacken(release, schluessel[1], tmp_path / "ziel")
    assert _stdout_felder(ergebnis) == {"grund": "release-verzeichnis"}


def _fifo_lauf(funktion: str, fifo: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Ruft eine Lese-Funktion des Helfers auf einer FIFO ohne Schreiber auf — ohne O_NONBLOCK
    hinge das open() für immer; der Timeout macht den Hänger zum Testfehler."""
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('h', {str(HELFER)!r})\n"
        "h = importlib.util.module_from_spec(spec); spec.loader.exec_module(h)\n"
        "try:\n"
        f"    if {funktion!r} == 'lies':\n"
        f"        h._lies_datei(__import__('pathlib').Path({str(fifo)!r}), 'regel')\n"
        "    else:\n"
        f"        h._kopiere_mit_hash(__import__('pathlib').Path({str(fifo)!r}),\n"
        f"                            __import__('pathlib').Path({str(tmp_path / 'kopie')!r}))\n"
        "except h.Fehler as f:\n"
        "    print(f.regel, f.text); sys.exit(3)\n"
    )
    return subprocess.run([PYTHON, "-I", "-B", "-c", code], capture_output=True, text=True,
                          check=False, timeout=15, stdin=subprocess.DEVNULL)


@pytest.mark.parametrize("funktion", ["lies", "kopiere"])
def test_fifo_haengt_nicht(tmp_path: Path, funktion: str) -> None:
    fifo = tmp_path / "rohr"
    os.mkfifo(fifo)
    ergebnis = _fifo_lauf(funktion, fifo, tmp_path)
    assert ergebnis.returncode == 3, ergebnis.stderr
    assert "keine reguläre Datei" in ergebnis.stdout
    assert not (tmp_path / "kopie").exists()


def test_manifest_als_fifo(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    (release / "manifest.json").unlink()
    os.mkfifo(release / "manifest.json")
    ergebnis = subprocess.run(["bash", str(SKRIPT), str(release), str(schluessel[1])],
                              capture_output=True, text=True, check=False, timeout=15,
                              stdin=subprocess.DEVNULL,
                              env={**os.environ, "SCANVIEW_PYTHON": PYTHON})
    _abgelehnt(ergebnis, "release-verzeichnis")


def test_aufruf_falsch() -> None:
    ergebnis = subprocess.run(["bash", str(SKRIPT)], capture_output=True, text=True,
                              check=False, stdin=subprocess.DEVNULL)
    assert ergebnis.returncode != 0
    assert "Aufruf" in ergebnis.stderr


# --- Vertrag mit manifest.py -------------------------------------------------------------------

def _helfer_modul(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setattr(sys, "dont_write_bytecode", True)  # kein __pycache__ in deploy/platform
    spec = importlib.util.spec_from_file_location("release_pruefen", HELFER)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


# --- Abschlussnachweis ----------------------------------------------------------------------------

def test_ohne_abschluss_abgelehnt(tmp_path: Path, schluessel: tuple[Path, Path]) -> None:
    """Ein Release, dessen Kette nach dem Signieren des Manifests abbrach (kein Archiv, kein
    Tag), hat keinen Abschlussnachweis — der Host lehnt es ab."""
    release = _baue_release(tmp_path / "rel", schluessel[0])
    (release / "abschluss.json").unlink()
    (release / "abschluss.json.sig").unlink()
    _abgelehnt(_pruefen(release, schluessel[1]), "release-verzeichnis")


def test_abschluss_im_manifest_namensraum_abgelehnt(tmp_path: Path,
                                                    schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    _signiere_abschluss(release, schluessel[0], namespace=manifest.NAMESPACE)
    _abgelehnt(_pruefen(release, schluessel[1]), "abschluss")


def test_manifest_signatur_gilt_nicht_als_abschluss(tmp_path: Path,
                                                    schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    shutil.copyfile(release / "manifest.json", release / "abschluss.json")
    shutil.copyfile(release / "manifest.json.sig", release / "abschluss.json.sig")
    _abgelehnt(_pruefen(release, schluessel[1]), "abschluss")


@pytest.mark.parametrize("aendern", [
    lambda d: d.update(manifest_sha256="0" * 64),
    lambda d: d.update(version="0.1.1"),
    lambda d: d.update(quellcommit="b" * 40),
    lambda d: d.update(exportcommit="b" * 40),
    lambda d: d.update(tag="v9.9.9"),
    lambda d: d.update(archiv_commit="kurz"),
    lambda d: d.update(schema="scanview-release-abschluss/0"),
    lambda d: d.pop("erstellt"),
], ids=["manifest-hash", "version", "quellcommit", "exportcommit", "tag", "archiv-commit",
        "schema", "feld-fehlt"])
def test_abschluss_zu_anderem_manifest_abgelehnt(tmp_path: Path, schluessel: tuple[Path, Path],
                                                aendern: Callable[[dict[str, Any]], None]
                                                ) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    _signiere_abschluss(release, schluessel[0], aendern)
    _abgelehnt(_pruefen(release, schluessel[1]), "abschluss")


def test_abschluss_fremd_signiert(tmp_path: Path, schluessel: tuple[Path, Path],
                                  fremd: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    _signiere_abschluss(release, fremd[0])
    _abgelehnt(_pruefen(release, schluessel[1]), "abschluss")


def test_konstanten_wie_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Helfer darf das Paket nicht importieren (Zielhost) — also hält dieser Test die
    duplizierten Konstanten gleich."""
    h = _helfer_modul(monkeypatch)
    assert h.SCHEMA == manifest.SCHEMA
    assert h.NAMESPACE == manifest.NAMESPACE
    assert h.KENNUNG == manifest.KENNUNG
    assert h.AUSGELIEFERT == manifest.AUSGELIEFERT
    assert tuple(h.FELDER) == manifest._FELDER
    assert tuple(h.UMGEBUNG_PFLICHT) == manifest.UMGEBUNG_PFLICHT
    assert h.PRUEFUNG_WERTE == manifest.PRUEFUNG_WERTE
    assert h.PFLICHT_PRUEFUNGEN == manifest.PFLICHT_PRUEFUNGEN
    assert h.PFLICHT_BERICHTE == manifest.PFLICHT_BERICHTE
    assert h.PFLICHT_WERKZEUGE == manifest.PFLICHT_WERKZEUGE
    assert h.PFLICHT_JE_SCHEMA == manifest.PFLICHT_JE_SCHEMA
    assert h.ABSCHLUSS_DATEI == abschluss.DATEI
    assert h.ABSCHLUSS_SIGNATUR == abschluss.DATEI + manifest.SIGNATUR_ENDUNG
    assert h.ABSCHLUSS_SCHEMA == abschluss.SCHEMA
    assert h.ABSCHLUSS_NAMESPACE == abschluss.NAMESPACE
    assert h.ABSCHLUSS_FELDER == abschluss.FELDER
    assert h.MANIFEST_DATEI == manifest.MANIFEST_DATEI
    assert h.SIGNATUR_DATEI == manifest.MANIFEST_DATEI + manifest.SIGNATUR_ENDUNG
    assert set(h.WURZELN) == manifest.AUSGELIEFERT
    # Fester Pfad statt PATH-Suche (root-Kontext); einrichten.sh prüft genau diesen Pfad.
    assert h.SSH_KEYGEN == "/usr/bin/ssh-keygen"
    assert '"ssh-keygen"' not in HELFER.read_text(encoding="utf-8")
    assert "SSH_KEYGEN=/usr/bin/ssh-keygen" in EINRICHTEN.read_text(encoding="utf-8")


def test_helfer_nur_standardbibliothek() -> None:
    quelltext = HELFER.read_text(encoding="utf-8")
    importe = {zeile.split()[1].split(".")[0] for zeile in quelltext.splitlines()
               if zeile.startswith(("import ", "from "))}
    assert importe <= set(sys.stdlib_module_names) | {"__future__"}, importe


# --- einrichten.sh -----------------------------------------------------------------------------

def test_einrichten_prueft_vor_jeder_aenderung() -> None:
    """Im Release-Modus läuft die Prüfung (entpacken) vor dem ersten verändernden Befehl, die
    Sicherung vor dem Kopieren, und der Vertrauensanker ist die Host-Datei."""
    text = EINRICHTEN.read_text(encoding="utf-8")
    entpacken = text.index("release_pruefen.py\" entpacken")
    assert entpacken < text.index("useradd")
    assert text.index(".backup") < text.index("rsync -a --delete")
    assert "ALLOWED_SIGNERS=/etc/scanview/allowed_signers" in text
    assert "/var/log/scanview/updates.log" in text
    assert "WARNUNG: kein geprüftes Release" in text
    # Abgelehnte Releases werden protokolliert (ergebnis=abgelehnt grund=<regel>).
    assert "ERGEBNIS=abgelehnt" in text and "grund=" in text


def test_einrichten_ohne_root() -> None:
    if os.geteuid() == 0:
        pytest.skip("läuft als root")
    ergebnis = subprocess.run(["bash", str(EINRICHTEN), "--release", "/nirgends"],
                              capture_output=True, text=True, check=False,
                              stdin=subprocess.DEVNULL)
    assert ergebnis.returncode != 0
    assert "root" in ergebnis.stderr


# --- shellcheck --------------------------------------------------------------------------------

WERKZEUGE_JSON = REPO_WURZEL / "nachweise" / "werkzeuge.json"


def test_shellcheck_gepinnt() -> None:
    w = lade_werkzeuge(WERKZEUGE_JSON)["shellcheck"]
    assert w.image == ("koalaman/shellcheck@sha256:"
                       "bb596a0d169b85ddd81d8b6d3a2ff6d5baf5fca10b97f575ebc647c3dff62b3d")
    assert w.version == "0.11.0"


def _shellcheck_da() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        image = lade_werkzeuge(WERKZEUGE_JSON)["shellcheck"].image
    except KeyError:
        return False
    return subprocess.run(["docker", "image", "inspect", image], capture_output=True,
                          check=False).returncode == 0


@pytest.mark.docker
@pytest.mark.skipif(not _shellcheck_da(), reason="docker oder gepinntes shellcheck-Image fehlt")
def test_shellcheck_deploy_skripte() -> None:
    """Gehärteter Lauf über `docker_befehl` wie jedes andere Werkzeug (ohne Netz, nur lesend)."""
    # Dazu das Freischalt-Skript ohne Endung (läuft als root über sudo, Befund B-WB-M4).
    skripte = sorted([*(p.name for p in PLATFORM.glob("*.sh")), "scanview-paket-freischalten"])
    assert "release-pruefen.sh" in skripte and "einrichten.sh" in skripte
    befehl = docker_befehl(lade_werkzeuge(WERKZEUGE_JSON)["shellcheck"],
                           [f"/skripte/{name}" for name in skripte], {PLATFORM: "/skripte"})
    assert "--network" in befehl and "none" in befehl
    ergebnis = subprocess.run(befehl, capture_output=True, text=True, check=False,
                              stdin=subprocess.DEVNULL)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr


# --- Bootstrap aus docs/public/betrieb.md ---------------------------------------------------------

BETRIEB = REPO_WURZEL / "docs" / "public" / "betrieb.md"


def _bootstrap_skript(ziel: Path) -> Path:
    """Den Skript-Block (```bash … scanview-bootstrap.sh …```) aus der Anleitung wörtlich
    übernehmen — getestet wird genau, was ein Betreiber kopiert."""
    text = BETRIEB.read_text(encoding="utf-8")
    bloecke = re.findall(r"^( *)```bash\n(.*?)^\1```", text, flags=re.M | re.S)
    [(einrueckung, block)] = [(e, b) for e, b in bloecke if "scanview-bootstrap.sh <" in b]
    zeilen = [z[len(einrueckung):] if z.startswith(einrueckung) else z
              for z in block.splitlines()]
    ziel.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return ziel


#: Die festen Pfade des ausgelieferten Skripts; nur die Testkopie ersetzt sie textuell.
ANKER_ZEILE = "ANKER=/etc/scanview/allowed_signers   # Vertrauensanker: fest, nie aus der Umgebung"
WURZEL_ZEILE = "WURZEL=/root"


def test_bootstrap_anker_nicht_aus_der_umgebung() -> None:
    """Der Vertrauensanker des root-Skripts ist fest verdrahtet — keine
    Umgebungsvariable (sudo -E, env_keep, exportierte Variable) kann ihn verschieben."""
    text = _bootstrap_skript_text()
    assert ANKER_ZEILE in text.splitlines() and WURZEL_ZEILE in text.splitlines()
    assert "SCANVIEW_" not in text and "${" not in text
    # Werkzeuge nicht aus PATH — fester PATH am Anfang, feste Programmpfade.
    zeilen = text.splitlines()
    pfad = next(z.split("=", 1)[1].split()[0] for z in zeilen[:6] if z.startswith("export PATH="))
    assert pfad == SECURE_PATH  # fester Wert wie Debians sudo secure_path
    assert "ssh-keygen -Y" not in text.replace("/usr/bin/ssh-keygen -Y", "")
    assert "python3 -I" not in text.replace("/usr/bin/python3 -I", "")


def _bootstrap(tmp_path: Path, release: Path, signers: Path
               ) -> tuple[subprocess.CompletedProcess[str], Path]:
    skript = _bootstrap_skript(tmp_path / "scanview-bootstrap.sh")
    wurzel = tmp_path / "root"
    wurzel.mkdir()
    # Nur die Testkopie: feste Pfade textuell auf tmp umlenken (kein root nötig).
    text = skript.read_text(encoding="utf-8")
    assert ANKER_ZEILE in text and WURZEL_ZEILE in text
    skript.write_text(text.replace(ANKER_ZEILE, f"ANKER={signers}")
                      .replace(WURZEL_ZEILE, f"WURZEL={wurzel}"), encoding="utf-8")
    r = subprocess.run(["bash", str(skript), str(release), VERSION], capture_output=True,
                       text=True, check=False, stdin=subprocess.DEVNULL)
    return r, wurzel


def test_bootstrap_skript_ist_fail_closed() -> None:
    text = _bootstrap_skript_text()
    assert text.startswith("#!/bin/bash\n")
    assert "set -euo pipefail" in text
    # Entpacken und Ausführen erst nach allen Prüfungen.
    assert text.index("tar -xzf") > text.index("-n scanview-abschluss")
    assert text.index("tar -xzf") > text.index("PY\n", text.index("python3 -I"))


def _bootstrap_skript_text() -> str:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        return _bootstrap_skript(Path(tmp) / "s.sh").read_text(encoding="utf-8")


def test_bootstrap_gueltig_entpackt_und_ruft_einrichten(tmp_path: Path,
                                                        schluessel: tuple[Path, Path]) -> None:
    release = _baue_release(tmp_path / "rel", schluessel[0])
    r, wurzel = _bootstrap(tmp_path, release, schluessel[1])
    assert r.returncode == 0, r.stderr  # das Test-einrichten.sh ist ein leeres Skript
    assert (wurzel / "scanview-deploy" / VERSION / "deploy/platform/einrichten.sh").is_file()
    assert (wurzel / "scanview-release" / VERSION / "manifest.json").is_file()


@pytest.mark.parametrize("fall", ["fremde-signatur", "tarball-veraendert", "ohne-abschluss",
                                  "abschluss-fremd", "trockenlauf", "symlink"])
def test_bootstrap_bricht_vor_dem_entpacken_ab(tmp_path: Path, schluessel: tuple[Path, Path],
                                               fremd: tuple[Path, Path], fall: str) -> None:
    """Scheitert eine Prüfung, wird nichts aus dem Release entpackt oder ausgeführt — auch nicht
    der nächste Befehl (vorher: lose Befehle in einer interaktiven root-Shell)."""
    if fall == "fremde-signatur":
        release = _baue_release(tmp_path / "rel", fremd[0])
    elif fall == "trockenlauf":
        release = _baue_release(tmp_path / "rel", schluessel[0], trockenlauf=True)
    else:
        release = _baue_release(tmp_path / "rel", schluessel[0])
    if fall == "tarball-veraendert":
        pfad = release / "artefakte" / f"scanview-server-{VERSION}.tar.gz"
        pfad.write_bytes(_tar([*STANDARD["server"],
                               _datei("deploy/platform/boese.sh", b"#!/bin/sh\n", 0o755)]))
    elif fall == "ohne-abschluss":
        (release / "abschluss.json").unlink()
        (release / "abschluss.json.sig").unlink()
    elif fall == "abschluss-fremd":
        _signiere_abschluss(release, fremd[0])
    elif fall == "symlink":
        (release / "artefakte" / "link").symlink_to("/etc/passwd")
    r, wurzel = _bootstrap(tmp_path, release, schluessel[1])
    assert r.returncode != 0, r.stdout
    assert not (wurzel / "scanview-deploy" / VERSION).exists()


#: Debians sudo-`secure_path`; der Bootstrap vererbt seinen PATH per exec an einrichten.sh.
SECURE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def test_bootstrap_path_findet_uv_wie_einrichten_es_sucht(tmp_path: Path) -> None:
    """einrichten.sh sucht uv per PATH; der Bootstrap-PATH muss den
    dokumentierten Installationsort /usr/local/bin enthalten — geprüft mit genau dem PATH-Wert
    aus dem Skript und dem uv-Test aus einrichten.sh."""
    text = _bootstrap_skript_text()
    pfad = next(z.split("=", 1)[1].split()[0] for z in text.splitlines()
                if z.startswith("export PATH="))
    einrichten = EINRICHTEN.read_text(encoding="utf-8")
    assert "command -v uv" in einrichten
    doku = BETRIEB.read_text(encoding="utf-8")
    assert "/usr/local/bin/uv" in doku  # dokumentierter Ort
    wurzel = tmp_path / "usr" / "local" / "bin"
    wurzel.mkdir(parents=True)
    (wurzel / "uv").write_text("#!/bin/sh\n", encoding="utf-8")
    (wurzel / "uv").chmod(0o755)
    umgelenkt = ":".join(str(tmp_path) + d if d.startswith("/usr/local") else d
                         for d in pfad.split(":"))
    r = subprocess.run(["bash", "-c", "command -v uv"], capture_output=True, text=True,
                       check=False, env={"PATH": umgelenkt})
    assert r.stdout.strip() == str(wurzel / "uv"), (pfad, r.stdout)


def test_installationsreste_aus_einer_quelle() -> None:
    """Welche Reste ein Update auslösen, steht einmal in einrichten.sh
    (Feld SPUREN); Schleife und Fehlermeldung nutzen es, betrieb.md nennt jeden Rest und keine
    feste Anzahl."""
    text = EINRICHTEN.read_text(encoding="utf-8")
    m = re.search(r"^SPUREN=\(([^)]*)\)", text, flags=re.M)
    assert m, "SPUREN=(…) fehlt"
    spuren = [s.strip('"').replace("$STATE", "/srv/scanview/state")
              .replace("$INSTALL_STATUS_ORDNER", "/var/lib/scanview-install")
              for s in m.group(1).split()]
    assert "/var/lib/scanview-install" in spuren and len(spuren) >= 4
    assert 'for spur in "${SPUREN[@]}"' in text
    meldung = next(z for z in text.splitlines() if "bewusste Neuinstallation" in z)
    assert "${SPUREN[*]}" in meldung
    doku = BETRIEB.read_text(encoding="utf-8")
    abschnitt = doku[doku.index("**Installation ohne Datenbank.**"):]
    abschnitt = abschnitt[:abschnitt.index("\n\n")]
    for spur in spuren:
        assert f"`{spur}`" in abschnitt, spur
    assert not re.search(r"\b(drei|vier)\b", abschnitt)
    # Jede Aufzählung der Reste (Kopfkommentar, „Aktualisieren“) nennt
    # auch den Installationsstatus — nicht nur der eine geprüfte Abschnitt.
    for name, inhalt in (("einrichten.sh", text), ("betrieb.md", doku)):
        aufzaehlungen = list(re.finditer(
            r"scanview-server\.env`?(?:,| oder)\s*`?/opt/scanview/server", inhalt))
        assert aufzaehlungen, name
        for treffer in aufzaehlungen:
            umfeld = inhalt[treffer.end():treffer.end() + 120]
            assert "/var/lib/scanview-install" in umfeld, (name, umfeld)
