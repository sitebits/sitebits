"""Signierter Abschlussnachweis eines Releases.

Die Manifest-Signatur entsteht vor Archivierung und Tag. Ohne weiteren Nachweis wäre ein Release,
dessen Archivierung danach scheitert, trotzdem deploybar. Deshalb schreibt die letzte Stufe der
Kette (`abschluss`, nach `archiv` und `tag`) einen eigenen, kanonischen und signierten
Abschlussnachweis in das Archiv — **nur**, wenn das Manifest dort committet ist und seine Signatur
besteht. Der Zielhost (`deploy/platform/release_pruefen.py`) verlangt ihn zwingend.

- Eigener Namensraum `scanview-abschluss` (nicht `scanview-release`): eine Manifest-Signatur kann
  nie als Abschluss-Signatur gelten und umgekehrt. `nachweise/allowed_signers` erlaubt dem
  Release-Schlüssel beide Namensräume.
- Inhalt: Version, SHA-256 der Manifest-Bytes, Quell- und Exportcommit (aus dem Manifest), der
  Archiv-Commit von `releases/<version>` und der Tag.
- Ablage: `abschluesse/<version>/abschluss.json` (+ `.sig`) im Archiv, eigener Commit, nur
  anhängen — `releases/<version>/` bleibt unverändert.
- `deploy_ordner` stellt den Release-Ordner für den Host aus dem **committeten** Archiv zusammen
  genau `manifest.json`, `manifest.json.sig`, `abschluss.json`,
  `abschluss.json.sig` und die ausgelieferten Tarballs (Menge je Schema) unter
  `artefakte/`, alles vorher gegen die Signaturen geprüft."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import signaturen
from .fehler import NachweisFehler
from .manifest import (
    KENNUNG,
    MANIFEST_DATEI,
    SIGNATUR_ENDUNG,
    ausgeliefert_fuer,
    kanonisch,
    lade_signiertes_manifest,
    pruefe_version,
)
from .werkzeuge import gefilterte_umgebung, git_befehl, keine_git_filter

SCHEMA = "scanview-release-abschluss/1"
NAMESPACE = "scanview-abschluss"
DATEI = "abschluss.json"
ORDNER = "abschluesse"
FELDER = ("schema", "version", "manifest_sha256", "quellcommit", "exportcommit",
          "archiv_commit", "tag", "erstellt")
ARTEFAKT_ORDNER = "artefakte"

_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_STDERR_AUSZUG = 2000


def _git(repo: Path, *args: str, regel: str = "abschluss") -> bytes:
    try:
        r = subprocess.run(git_befehl(*args, repo=repo), capture_output=True, check=False,
                           stdin=subprocess.DEVNULL, env=gefilterte_umgebung())
    except OSError as e:
        raise NachweisFehler(regel, f"git nicht ausführbar: {e.strerror}") from None
    if r.returncode != 0:
        text = r.stderr.decode("utf-8", "replace").strip()[-_STDERR_AUSZUG:]
        raise NachweisFehler(regel, f"git {args[0]} in {repo} endete mit Exit {r.returncode}: "
                                    f"{text or '(keine Ausgabe)'}")
    return r.stdout


def _ssh_keygen(befehl: list[str], eingabe: bytes | None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(["ssh-keygen", *befehl], input=eingabe, capture_output=True,
                              check=False, env=gefilterte_umgebung(),
                              stdin=subprocess.DEVNULL if eingabe is None else None)
    except OSError as e:
        raise NachweisFehler("abschluss-signatur", f"ssh-keygen nicht ausführbar: "
                                                   f"{e.strerror}") from None


def verifiziere(roh: bytes, sig: bytes, allowed_signers: Path) -> None:
    """Signatur `sig` über genau die Bytes `roh` im Namensraum `scanview-abschluss`."""
    with tempfile.TemporaryDirectory(prefix="scanview-abschluss-") as tmp:
        sig_pfad = Path(tmp) / (DATEI + SIGNATUR_ENDUNG)
        sig_pfad.write_bytes(sig)
        r = _ssh_keygen(["-Y", "verify", "-f", str(allowed_signers), "-I", KENNUNG,
                         "-n", NAMESPACE, "-s", str(sig_pfad)], roh)
    if r.returncode != 0:
        text = r.stderr.decode("utf-8", "replace").strip()[-_STDERR_AUSZUG:]
        raise NachweisFehler("abschluss-signatur", f"{DATEI}: Signatur ungültig (Exit "
                                                   f"{r.returncode}): {text or '(keine Ausgabe)'}")


def parse(roh: bytes, manifest_roh: bytes, manifest: dict[str, Any]) -> dict[str, Any]:
    """Abschluss-Bytes prüfen: kanonisch, genau die Felder, und er gehört zu genau diesem
    Manifest (SHA-256 der Bytes, Version, Quell- und Exportcommit, Tag `v<version>`)."""
    regel = "abschluss"
    try:
        daten = json.loads(roh.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise NachweisFehler(regel, f"{DATEI}: kein gültiges JSON ({e})") from None
    if not isinstance(daten, dict) or kanonisch(daten) != roh:
        raise NachweisFehler(regel, f"{DATEI}: nicht in kanonischer Form")
    if tuple(sorted(daten)) != tuple(sorted(FELDER)):
        raise NachweisFehler(regel, f"{DATEI}: Felder {sorted(daten)} — erwartet {sorted(FELDER)}")
    if daten["schema"] != SCHEMA:
        raise NachweisFehler(regel, f"{DATEI}: unbekanntes Schema „{daten['schema']}“")
    soll = {"version": manifest["version"],
            "manifest_sha256": hashlib.sha256(manifest_roh).hexdigest(),
            "quellcommit": manifest["quellcommit"], "exportcommit": manifest["exportcommit"],
            "tag": f"v{manifest['version']}"}
    abweichend = [k for k, v in soll.items() if daten[k] != v]
    if abweichend:
        raise NachweisFehler(regel, f"{DATEI} gehört nicht zu diesem Manifest: "
                                    f"{', '.join(abweichend)}")
    if not isinstance(daten["archiv_commit"], str) or not _COMMIT.fullmatch(
            daten["archiv_commit"]):
        raise NachweisFehler(regel, f"{DATEI}: Archiv-Commit ungültig")
    if not isinstance(daten["erstellt"], str) or not daten["erstellt"]:
        raise NachweisFehler(regel, f"{DATEI}: Zeitpunkt fehlt")
    return daten


def _blob(archiv: Path, rev: str, pfad: str) -> bytes:
    return _git(archiv, "cat-file", "blob", f"{rev}:{pfad}")


def _archiv_commit(archiv: Path, version: str) -> str:
    commit = _git(archiv, "log", "--format=%H", "-1", "--", f"releases/{version}").decode().strip()
    if not _COMMIT.fullmatch(commit):
        raise NachweisFehler("abschluss", f"releases/{version} ist im Archiv {archiv} nicht "
                                          "committet — ohne Archivierung kein Abschluss")
    return commit


def pruefe_archivierte_nachweise(archiv: Path, rev: str, version: str,
                                 m: dict[str, Any]) -> None:
    """Dieselbe Prüfung wie `archiv._pruefe_nachweise`, aber gegen die Blobs des committeten
    Archivbaums `rev`: jedes Artefakt unter `artefakte/` mit SHA-256 und SHA-512, jede SBOM und
    jeder Bericht genau einmal unter `releases/<version>/` mit dem signierten SHA-256. Damit
    trägt jeder Weg zum Abschluss (Stufe und Wiederherstellung) dieselbe Archivprüfung."""
    regel = "archiv-nachweis"
    wurzel = f"releases/{version}/"
    roh = _git(archiv, "ls-tree", "-r", "-z", rev, "--", wurzel)
    blobs: dict[str, str] = {}
    for eintrag in filter(None, roh.split(b"\0")):
        kopf, _, pfad = eintrag.partition(b"\t")
        modus, typ, objekt = kopf.decode().split()
        if typ != "blob" or modus not in ("100644", "100755"):
            raise NachweisFehler(regel, f"{pfad.decode()} ist kein reguläres Blob (Modus {modus}; "
                                        "Symlink/Submodul im Archiv nicht erlaubt)")
        blobs[pfad.decode("utf-8").removeprefix(wurzel)] = objekt
    nach_name: dict[str, list[str]] = {}
    for relativ in blobs:
        nach_name.setdefault(relativ.rsplit("/", 1)[-1], []).append(relativ)

    def inhalt(relativ: str) -> bytes:
        return _git(archiv, "cat-file", "blob", blobs[relativ])

    for a in m["artefakte"]:
        relativ = f"{ARTEFAKT_ORDNER}/{a['datei']}"
        if relativ not in blobs:
            raise NachweisFehler(regel, f"Artefakt {a['datei']} fehlt im Archiv-Commit")
        daten = inhalt(relativ)
        if (hashlib.sha256(daten).hexdigest(), hashlib.sha512(daten).hexdigest()) != (
                a["sha256"], a["sha512"]):
            raise NachweisFehler(regel, f"Artefakt {a['datei']}: Hash weicht vom signierten "
                                        "Manifest ab")
    erwartet = [*m["sboms"].items(), *((b["datei"], b["sha256"]) for b in m["berichte"].values())]
    for name, soll in erwartet:
        treffer = nach_name.get(name, [])
        if len(treffer) != 1:
            raise NachweisFehler(regel, f"{name} {'fehlt' if not treffer else 'liegt mehrfach'} "
                                        "im Archiv-Commit")
        if hashlib.sha256(inhalt(treffer[0])).hexdigest() != soll:
            raise NachweisFehler(regel, f"{name}: SHA-256 weicht vom signierten Manifest ab")


def _signiertes_manifest(archiv: Path, rev: str, version: str,
                         allowed_signers: Path) -> tuple[bytes, dict[str, Any]]:
    """Manifest und Signatur aus dem committeten Archivbaum `rev`, Signatur über genau diese
    Bytes geprüft; kein Trockenlauf, passende Version."""
    roh = _blob(archiv, rev, f"releases/{version}/{MANIFEST_DATEI}")
    sig = _blob(archiv, rev, f"releases/{version}/{MANIFEST_DATEI}{SIGNATUR_ENDUNG}")
    with tempfile.TemporaryDirectory(prefix="scanview-abschluss-") as tmp:
        pfad = Path(tmp) / MANIFEST_DATEI
        pfad.write_bytes(roh)
        pfad.with_name(MANIFEST_DATEI + SIGNATUR_ENDUNG).write_bytes(sig)
        daten = lade_signiertes_manifest(pfad, allowed_signers)
    if daten["version"] != version or daten["trockenlauf"] is not False:
        raise NachweisFehler("abschluss", "archiviertes Manifest: Version oder "
                                          "Trockenlauf-Kennzeichen weicht ab")
    return roh, daten


def abschliessen(archiv: Path, version: str, schluessel: Path, allowed_signers: Path, *,
                 erstellt: datetime | None = None) -> Path:
    """Abschlussnachweis schreiben, signieren, prüfen und im Archiv committen; Rückgabe:
    `<archiv>/abschluesse/<version>`. Nie überschreiben (`abschluss-unveraenderlich`)."""
    pruefe_version(version, "abschluss")
    if not (archiv / ".git").exists():
        raise NachweisFehler("abschluss", f"{archiv}: kein Git-Repo")
    keine_git_filter(archiv, "abschluss")
    # Archiv-Commits sind signiert: ohne Anker (und Schlüssel) entsteht kein Abschluss.
    with signaturen.geladener_anker(archiv) as anker:
        signaturen.pruefe_signierschluessel(archiv, anker)
        return _abschliessen(archiv, version, schluessel, allowed_signers, erstellt, anker)


def _abschliessen(archiv: Path, version: str, schluessel: Path, allowed_signers: Path,
                  erstellt: datetime | None, anker: signaturen.Anker) -> Path:
    commit = _archiv_commit(archiv, version)
    manifest_roh, m = _signiertes_manifest(archiv, commit, version, allowed_signers)
    pruefe_archivierte_nachweise(archiv, commit, version, m)
    relativ = f"{ORDNER}/{version}"
    ziel = archiv / ORDNER / version
    if ziel.exists() or _git(archiv, "log", "--format=%H", "-1", "--", relativ).strip():
        raise NachweisFehler("abschluss-unveraenderlich", f"{relativ} existiert schon im Archiv")
    if _git(archiv, "status", "--porcelain", "--untracked-files=all").strip():
        raise NachweisFehler("abschluss", f"{archiv}: Arbeitsbaum nicht sauber")
    daten = {"schema": SCHEMA, "version": version,
             "manifest_sha256": hashlib.sha256(manifest_roh).hexdigest(),
             "quellcommit": m["quellcommit"], "exportcommit": m["exportcommit"],
             "archiv_commit": commit, "tag": f"v{version}",
             "erstellt": (erstellt or datetime.now(UTC)).isoformat()}
    roh = kanonisch(daten)
    parse(roh, manifest_roh, m)

    ordner = archiv / ORDNER
    if ordner.is_symlink() or (ordner.exists() and not ordner.is_dir()):
        raise NachweisFehler("abschluss", f"{ordner}: kein echtes Verzeichnis")
    ordner.mkdir(exist_ok=True)
    unfertig = ordner / f".{version}.unfertig"
    try:
        unfertig.mkdir()
    except FileExistsError:
        raise NachweisFehler("abschluss-unveraenderlich", f"{unfertig}: Rest eines abgebrochenen "
                                                          "Laufs — prüfen und entfernen") from None
    try:
        pfad = unfertig / DATEI
        pfad.write_bytes(roh)
        r = _ssh_keygen(["-q", "-Y", "sign", "-f", str(schluessel), "-n", NAMESPACE, str(pfad)],
                        None)
        sig = pfad.with_name(DATEI + SIGNATUR_ENDUNG)
        if r.returncode != 0 or not sig.is_file():
            raise NachweisFehler("abschluss-signatur", f"Signieren fehlgeschlagen (Exit "
                                                       f"{r.returncode})")
        verifiziere(pfad.read_bytes(), sig.read_bytes(), allowed_signers)
        unfertig.rename(ziel)
    except (OSError, NachweisFehler) as e:
        shutil.rmtree(unfertig, ignore_errors=True)
        if isinstance(e, NachweisFehler):
            raise
        raise NachweisFehler("abschluss", f"Schreiben fehlgeschlagen: {e}") from None
    try:
        _git(archiv, "add", "--", relativ)
        # -S: ohne Signierschlüssel scheitert der Commit, statt unsigniert zu entstehen.
        _git(archiv, "commit", "-q", "-S", "-m", f"abschluss: {version} ({m['quellcommit']})")
    except NachweisFehler:
        subprocess.run(git_befehl("reset", "-q", "--", relativ, repo=archiv),
                       capture_output=True, check=False, stdin=subprocess.DEVNULL,
                       env=gefilterte_umgebung())
        shutil.rmtree(ziel, ignore_errors=True)
        raise
    for name in (DATEI, DATEI + SIGNATUR_ENDUNG):
        if _blob(archiv, "HEAD", f"{relativ}/{name}") != (ziel / name).read_bytes():
            raise NachweisFehler("abschluss", f"committeter {name} weicht von der geprüften "
                                              "Datei ab")
    signaturen.pruefe_commit(archiv, "HEAD", anker)
    return ziel


def deploy_ordner(archiv: Path, version: str, ziel: Path, allowed_signers: Path) -> Path:
    """Release-Ordner für `einrichten.sh --release` aus dem committeten Archiv (`HEAD`):
    Manifest- und Abschluss-Signatur geprüft, Abschluss passt zum Manifest und nennt einen
    Archiv-Commit, der `releases/<version>` enthält, jeder ausgelieferte Tarball mit SHA-256 und
    SHA-512 des Manifests. `ziel` muss neu sein."""
    pruefe_version(version, "deploy-ordner")
    if ziel.exists() or ziel.is_symlink():
        raise NachweisFehler("deploy-ordner", f"{ziel}: existiert schon — nur ein neuer Pfad")
    manifest_roh, m = _signiertes_manifest(archiv, "HEAD", version, allowed_signers)
    sig = _blob(archiv, "HEAD", f"releases/{version}/{MANIFEST_DATEI}{SIGNATUR_ENDUNG}")
    ab_roh = _blob(archiv, "HEAD", f"{ORDNER}/{version}/{DATEI}")
    ab_sig = _blob(archiv, "HEAD", f"{ORDNER}/{version}/{DATEI}{SIGNATUR_ENDUNG}")
    verifiziere(ab_roh, ab_sig, allowed_signers)
    ab = parse(ab_roh, manifest_roh, m)
    if _archiv_commit(archiv, version) != ab["archiv_commit"]:
        raise NachweisFehler("abschluss", "Archiv-Commit im Abschluss ist nicht der Commit von "
                                          f"releases/{version}")
    pruefe_archivierte_nachweise(archiv, "HEAD", version, m)
    tarballs: dict[str, bytes] = {}
    for a in m["artefakte"]:
        if a["name"] not in ausgeliefert_fuer(m):
            continue
        daten = _blob(archiv, "HEAD", f"releases/{version}/{ARTEFAKT_ORDNER}/{a['datei']}")
        if (hashlib.sha256(daten).hexdigest(), hashlib.sha512(daten).hexdigest()) != (
                a["sha256"], a["sha512"]):
            raise NachweisFehler("artefakt-hash", f"{a['datei']}: Hash weicht vom signierten "
                                                  "Manifest ab")
        tarballs[a["datei"]] = daten
    try:
        (ziel / ARTEFAKT_ORDNER).mkdir(parents=True)
        for name, inhalt in ((MANIFEST_DATEI, manifest_roh),
                             (MANIFEST_DATEI + SIGNATUR_ENDUNG, sig), (DATEI, ab_roh),
                             (DATEI + SIGNATUR_ENDUNG, ab_sig)):
            (ziel / name).write_bytes(inhalt)
        for name, inhalt in tarballs.items():
            (ziel / ARTEFAKT_ORDNER / name).write_bytes(inhalt)
    except OSError as e:
        with contextlib.suppress(OSError):
            shutil.rmtree(ziel)
        raise NachweisFehler("deploy-ordner", f"{ziel}: nicht schreibbar ({e})") from None
    return ziel
