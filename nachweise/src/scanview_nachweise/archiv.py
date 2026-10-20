"""Nachweis-Archiv: eigenes Git-Repo außerhalb des geprüften Quellbaums,
**nur anhängen**. Jede Version landet genau einmal unter `releases/<version>/` und wird lokal
committet; eine vorhandene oder je committete Version wird nie wieder beschrieben. Gepusht wird
hier nicht — das entscheidet der Aufrufer (Release-Orchestrierung).

Vor dem Umbenennen der fertigen Kopie wird die Signatur **der Kopie** geprüft (einmal gelesene
Bytes, `lade_signiertes_manifest`): archiviert wird nur, was nachweislich signiert ist — auch
wenn die Quelle zwischen Vorabprüfung und Kopie ausgetauscht wurde. Das mitgelieferte
`allowed_signers` muss dem geprüften gleichen, damit das Manifest auch nach einem
Schlüsselwechsel prüfbar bleibt. Trockenlauf-Manifeste werden nie archiviert.

Nachweise **nach** dem Release (DAST, Update-Protokoll, security.txt, Branch-Schutz, Wochenscan,
Aufbewahrung, Renovate) schreibt `schreibe_pruefung` unter
`pruefungen/<JJJJ-MM-TT>[.<lauf>]-<art>[-<instanz>]/` (weitere Läufe eines Tages mit
Laufnummer ab 2) — ebenfalls nur anhängend, signiert committet und
gegen den Vertrauensanker des Archivs geprüft; `releases/<version>/` bleibt unberührt."""

from __future__ import annotations

import contextlib
import hashlib
import itertools
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from . import signaturen
from .fehler import NachweisFehler
from .manifest import (
    MANIFEST_DATEI,
    SIGNATUR_ENDUNG,
    kanonisch,
    lade_manifest,
    lade_signiertes_manifest,
    pruefe_version,
)
from .werkzeuge import gefilterte_umgebung, git_befehl, keine_git_filter

_COMMIT = re.compile(r"[0-9a-f]{40}")
ALLOWED_SIGNERS = "allowed_signers"
PRUEFUNGEN = "pruefungen"
ERGEBNIS_DATEI = "ergebnis.json"
BETRIEB = "betrieb"
#: Fortgeschriebene Betriebsdateien (feste Menge; die Historie steht in Git).
BETRIEB_DATEIEN = frozenset({"unterstuetzt.toml"})
#: Arten der Nachweise nach dem Release (feste Menge; eine neue Art ist eine Code-Änderung).
PRUEFUNG_ARTEN = frozenset({"dast", "updates", "securitytxt", "branchschutz", "wochenscan",
                            "aufbewahrung", "renovate"})
#: Instanzkennung (z. B. L, S): Buchstaben, Ziffern, innere Bindestriche — kein Pfadanteil.
_INSTANZ = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")
#: Ordnername eines Nachweises: `<datum>[.<lauf>]-<art>[-<instanz>]`. Der erste Lauf eines Tages
#: trägt keine Nummer, weitere ab `.2` (ohne führende Null). `.` kommt in Art und Instanz nicht
#: vor, also ist der Name eindeutig zerlegbar. Gruppen: Datum, Lauf (oder None), Art, Instanz.
PRUEFUNGSNAME = re.compile(r"(\d{4}-\d{2}-\d{2})(?:\.([2-9]|[1-9][0-9]+))?-([a-z0-9]+)"
                           r"(?:-([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*))?")
_BEIGABE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_STDERR_AUSZUG = 2000


def _git(repo: Path, *args: str) -> str:
    try:
        ergebnis = subprocess.run(git_befehl(*args, repo=repo), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", check=False,
                                  stdin=subprocess.DEVNULL, env=gefilterte_umgebung())
    except OSError as e:
        raise NachweisFehler("archiv", f"git nicht ausführbar: {e.strerror}") from None
    if ergebnis.returncode != 0:
        auszug = ergebnis.stderr.strip()[-_STDERR_AUSZUG:]
        raise NachweisFehler("archiv", f"git {args[0]} im Archiv endete mit Exit "
                                       f"{ergebnis.returncode}: {auszug or '(keine Ausgabe)'}")
    return ergebnis.stdout


def _git_bytes(repo: Path, *args: str) -> bytes:
    try:
        ergebnis = subprocess.run(git_befehl(*args, repo=repo), capture_output=True,
                                  check=False, stdin=subprocess.DEVNULL,
                                  env=gefilterte_umgebung())
    except OSError as e:
        raise NachweisFehler("archiv", f"git nicht ausführbar: {e.strerror}") from None
    if ergebnis.returncode != 0:
        raise NachweisFehler("archiv", f"git {args[0]} im Archiv endete mit Exit "
                                       f"{ergebnis.returncode}")
    return ergebnis.stdout


def _quellcommit(quellen: list[Path], version: str) -> str:
    """Quellcommit aus dem mitgelieferten Manifest (volle Formprüfung per `lade_manifest`);
    Manifest und Signatur sind Pflicht. Die Signatur prüft der Aufrufer vorher."""
    namen = {q.name: q for q in quellen}
    for pflicht in (MANIFEST_DATEI, MANIFEST_DATEI + SIGNATUR_ENDUNG, ALLOWED_SIGNERS):
        if pflicht not in namen:
            raise NachweisFehler("archiv", f"{pflicht} fehlt in den Quellen — ohne signiertes "
                                           "Manifest wird nichts archiviert")
    try:
        daten = lade_manifest(namen[MANIFEST_DATEI])
    except NachweisFehler as e:
        raise NachweisFehler("archiv", f"{MANIFEST_DATEI}: {e.text}") from None
    if daten["version"] != version:
        raise NachweisFehler("archiv", f"{MANIFEST_DATEI} gehört zu Version "
                                       f"„{daten['version']}“, archiviert werden soll {version}")
    if daten["trockenlauf"] is not False:
        raise NachweisFehler("archiv", f"{MANIFEST_DATEI} stammt aus einem Trockenlauf — wird nie "
                                       "archiviert")
    quellcommit = daten.get("quellcommit")
    if not isinstance(quellcommit, str) or not _COMMIT.fullmatch(quellcommit):
        raise NachweisFehler("archiv", f"{MANIFEST_DATEI}: Quellcommit fehlt oder ungültig")
    return quellcommit


def _sha(pfad: Path) -> tuple[str, str]:
    h256, h512 = hashlib.sha256(), hashlib.sha512()
    with pfad.open("rb") as f:
        while block := f.read(1 << 20):
            h256.update(block)
            h512.update(block)
    return h256.hexdigest(), h512.hexdigest()


def _pruefe_nachweise(unfertig: Path, daten: dict[str, Any]) -> None:
    """Jede Datei, die das signierte Manifest referenziert, liegt in der Kopie vor und trägt den
    signierten Hash: Artefakte unter `artefakte/<datei>` (SHA-256 und SHA-512),
    SBOMs und Berichte genau einmal irgendwo in der Kopie (Name eindeutig)."""
    regel = "archiv-nachweis"
    nach_name: dict[str, list[Path]] = {}
    for pfad in unfertig.rglob("*"):
        if pfad.is_symlink():
            raise NachweisFehler(regel, f"Symlink in der Kopie: {pfad.relative_to(unfertig)}")
        if pfad.is_file():
            nach_name.setdefault(pfad.name, []).append(pfad)
    for a in daten["artefakte"]:
        pfad = unfertig / "artefakte" / a["datei"]
        if not pfad.is_file() or pfad.is_symlink():
            raise NachweisFehler(regel, f"Artefakt {a['datei']} fehlt in der Kopie")
        if _sha(pfad) != (a["sha256"], a["sha512"]):
            raise NachweisFehler(regel, f"Artefakt {a['datei']}: Hash weicht vom signierten "
                                        "Manifest ab")
    erwartet = [(n, h) for n, h in daten["sboms"].items()]
    erwartet += [(b["datei"], b["sha256"]) for b in daten["berichte"].values()]
    for name, soll in erwartet:
        treffer = nach_name.get(name, [])
        if not treffer:
            raise NachweisFehler(regel, f"{name} fehlt in der Kopie")
        if len(treffer) > 1:
            raise NachweisFehler(regel, f"{name} liegt mehrfach in der Kopie — Zuordnung zum "
                                        "Manifest nicht eindeutig")
        if _sha(treffer[0])[0] != soll:
            raise NachweisFehler(regel, f"{name}: SHA-256 weicht vom signierten Manifest ab")


def _pruefe_commit(archiv_repo: Path, relativ: str, ziel: Path) -> None:
    """Der committete Baum unter `relativ` ist byte-gleich zur geprüften Kopie `ziel` (gleiche
    Pfade, gleicher Inhalt der Blobs) — sonst hätten Filter oder Attribute des Archiv-Repos
    etwas anderes committet als geprüft."""
    roh = _git(archiv_repo, "ls-tree", "-r", "-z", "HEAD", "--", relativ)
    commit: dict[str, str] = {}
    for eintrag in filter(None, roh.split("\0")):
        kopf, _, pfad = eintrag.partition("\t")
        _modus, typ, objekt = kopf.split()
        if typ != "blob":
            raise NachweisFehler("archiv-nachweis", f"Commit: {pfad} ist kein Blob")
        inhalt = _git_bytes(archiv_repo, "cat-file", "blob", objekt)
        commit[pfad] = hashlib.sha256(inhalt).hexdigest()
    kopie = {f"{relativ}/{p.relative_to(ziel).as_posix()}": _sha(p)[0]
             for p in ziel.rglob("*") if p.is_file()}
    if commit != kopie:
        abweichend = sorted(set(commit.items()) ^ set(kopie.items()))
        raise NachweisFehler("archiv-nachweis", "Commit weicht von der geprüften Kopie ab: "
                             + ", ".join(sorted({p for p, _ in abweichend})[:20]))


def _pruefe_kopie(unfertig: Path, version: str, allowed_signers: Path) -> None:
    """Signatur der bereitgestellten Kopie gegen das geprüfte `allowed_signers` (einmal gelesene
    Bytes); die mitkopierte `allowed_signers` muss byte-gleich sein; jede referenzierte
    Nachweisdatei liegt mit ihrem signierten Hash in der Kopie."""
    try:
        daten = lade_signiertes_manifest(unfertig / MANIFEST_DATEI, allowed_signers)
    except NachweisFehler as e:
        raise NachweisFehler("archiv-signatur", f"bereitgestellte Kopie: {e.text}") from None
    if daten["version"] != version or daten["trockenlauf"] is not False:
        raise NachweisFehler("archiv-signatur", "bereitgestellte Kopie: Version oder "
                                                "Trockenlauf-Kennzeichen weicht ab")
    try:
        gleich = (unfertig / ALLOWED_SIGNERS).read_bytes() == allowed_signers.read_bytes()
    except OSError as e:
        raise NachweisFehler("archiv-signatur", f"allowed_signers nicht lesbar: {e}") from None
    if not gleich:
        raise NachweisFehler("archiv-signatur", "mitgeliefertes allowed_signers weicht vom "
                                                "geprüften ab")
    _pruefe_nachweise(unfertig, daten)


def archiviere(version: str, quellen: list[Path], archiv_repo: Path, *,
               allowed_signers: Path) -> Path:
    """Quellen (Dateien oder Ordner, unter ihrem Namen) nach `<archiv>/releases/<version>/`
    kopieren und lokal committen (`nachweise: <version> (<quellcommit>)`). Abbruch mit
    `archiv-unveraenderlich`, wenn die Version schon existiert oder je im Archiv stand; mit
    `archiv-signatur`, wenn die Signatur der bereitgestellten Kopie nicht gegen
    `allowed_signers` besteht. Pflichtquellen: `manifest.json`, `manifest.json.sig`,
    `allowed_signers`."""
    pruefe_version(version, "archiv")
    if not (archiv_repo / ".git").exists():
        raise NachweisFehler("archiv", f"{archiv_repo}: kein Git-Repo")
    keine_git_filter(archiv_repo, "archiv")
    # Archiv-Commits sind signiert: ohne Anker (und Schlüssel) wird nichts archiviert.
    with signaturen.geladener_anker(archiv_repo) as anker:
        signaturen.pruefe_signierschluessel(archiv_repo, anker)
        return _archiviere(version, quellen, archiv_repo, allowed_signers, anker)


def _archiviere(version: str, quellen: list[Path], archiv_repo: Path, allowed_signers: Path,
                anker: signaturen.Anker) -> Path:
    relativ = f"releases/{version}"
    ziel = archiv_repo / "releases" / version
    if ziel.exists() or _git(archiv_repo, "log", "--format=%H", "-1", "--", relativ).strip():
        raise NachweisFehler("archiv-unveraenderlich", f"{relativ} existiert schon im Archiv — "
                                                       "Nachweise werden nie überschrieben")
    namen = [q.name for q in quellen]
    doppelt = sorted({n for n in namen if namen.count(n) > 1})
    if doppelt:
        raise NachweisFehler("archiv", f"Quelle doppelt: {', '.join(doppelt)}")
    for q in quellen:
        if not q.exists():
            raise NachweisFehler("archiv", f"{q}: Quelle fehlt")
    quellcommit = _quellcommit(quellen, version)
    if _git(archiv_repo, "status", "--porcelain", "--untracked-files=all").strip():
        raise NachweisFehler("archiv", f"{archiv_repo}: Arbeitsbaum nicht sauber — erst klären, "
                                       "dann archivieren")

    releases = archiv_repo / "releases"
    if releases.is_symlink() or (releases.exists() and not releases.is_dir()):
        raise NachweisFehler("archiv", f"{releases}: kein echtes Verzeichnis (Symlink?) — "
                                       "Archiv verweigert")
    releases.mkdir(exist_ok=True)
    # Version exklusiv belegen: mkdir scheitert, wenn jemand sie inzwischen angelegt hat. Der
    # leere Platzhalter wird am Ende durch rename der fertigen Kopie ersetzt.
    try:
        ziel.mkdir()
    except FileExistsError:
        raise NachweisFehler("archiv-unveraenderlich", f"{relativ} existiert schon im Archiv — "
                                                       "Nachweise werden nie überschrieben"
                             ) from None
    # Erst unter Hilfsnamen vollständig kopieren, dann umbenennen: ein Abbruch mitten im
    # Kopieren hinterlässt keine halbe Version, die die echte blockieren würde.
    unfertig = releases / f".{version}.unfertig"
    try:
        unfertig.mkdir()
    except FileExistsError:
        ziel.rmdir()
        raise NachweisFehler("archiv-unveraenderlich", f"{unfertig}: Rest eines abgebrochenen "
                                                       "Laufs — prüfen und von Hand entfernen"
                             ) from None
    try:
        for q in quellen:
            if q.is_dir():
                shutil.copytree(q, unfertig / q.name, symlinks=False)
            else:
                shutil.copy2(q, unfertig / q.name)
        _pruefe_kopie(unfertig, version, allowed_signers)
        unfertig.rename(ziel)  # ersetzt nur den leeren Platzhalter (sonst ENOTEMPTY)
    except (OSError, NachweisFehler) as e:
        shutil.rmtree(unfertig, ignore_errors=True)
        with contextlib.suppress(OSError):
            ziel.rmdir()  # nur, wenn noch der eigene leere Platzhalter
        if isinstance(e, NachweisFehler):
            raise
        raise NachweisFehler("archiv", f"Kopieren ins Archiv fehlgeschlagen: {e}") from None

    try:
        _git(archiv_repo, "add", "--", relativ)
        # -S: ohne Signierschlüssel scheitert der Commit, statt unsigniert zu entstehen.
        _git(archiv_repo, "commit", "-q", "-S", "-m", f"nachweise: {version} ({quellcommit})")
    except NachweisFehler:
        # Nur die eigene, nie committete Kopie entfernen — sonst blockiert sie die Version.
        subprocess.run(git_befehl("reset", "-q", "--", relativ, repo=archiv_repo),
                       capture_output=True, check=False, stdin=subprocess.DEVNULL,
                       env=gefilterte_umgebung())
        shutil.rmtree(ziel, ignore_errors=True)
        raise
    # Nach dem Commit ist die Version belegt (nur anhängen): ein Abweichen bricht ab, ohne
    # etwas zurückzunehmen — die Version ist dann von Hand zu klären.
    _pruefe_commit(archiv_repo, relativ, ziel)
    signaturen.pruefe_commit(archiv_repo, "HEAD", anker)
    return ziel


def _pruefe_pruefungsnamen(art: str, instanz: str | None, dateien: dict[str, bytes]) -> None:
    if not isinstance(art, str) or art not in PRUEFUNG_ARTEN:
        raise NachweisFehler("archiv", f"Prüfungsart „{art}“ unbekannt (erlaubt: "
                                       f"{', '.join(sorted(PRUEFUNG_ARTEN))})")
    if instanz is not None and (not isinstance(instanz, str) or not _INSTANZ.fullmatch(instanz)):
        raise NachweisFehler("archiv", f"Instanz „{instanz}“ ungültig (nur Buchstaben, Ziffern, "
                                       "innere Bindestriche)")
    for name, inhalt in dateien.items():
        if (not isinstance(name, str) or not _BEIGABE.fullmatch(name) or ".." in name
                or name == ERGEBNIS_DATEI):
            raise NachweisFehler("archiv", f"Dateiname „{name}“ unzulässig (kein Pfadanteil, "
                                           f"nicht {ERGEBNIS_DATEI})")
        if not isinstance(inhalt, bytes):
            raise NachweisFehler("archiv", f"{name}: Inhalt muss bytes sein")


def pruefungsname(heute: date, art: str, instanz: str | None, lauf: int) -> str:
    """Ordnername des `lauf`-ten Nachweises dieser Art und Instanz am Tag `heute` (ab 1)."""
    if not isinstance(lauf, int) or isinstance(lauf, bool) or lauf < 1:
        raise NachweisFehler("archiv", f"Laufnummer „{lauf}“ ungültig (ab 1)")
    datum = heute.isoformat() + (f".{lauf}" if lauf > 1 else "")
    return f"{datum}-{art}" + (f"-{instanz}" if instanz is not None else "")


def pruefe_schreibbar(archiv: Path) -> None:
    """Vorbedingungen eines Nachweises vorab prüfen, ohne zu schreiben: Git-Repo ohne Filter,
    Vertrauensanker, Signierschlüssel im Anker, `pruefungen/` kein Symlink, sauberer Arbeitsbaum.
    Für Läufe, die vorher etwas Teures oder eine Instanz anfassen (DAST) — damit ein Fehler im
    Archiv nicht erst nach dem Scan auffällt. `schreibe_pruefung` prüft dasselbe noch einmal."""
    if not (archiv / ".git").exists():
        raise NachweisFehler("archiv", f"{archiv}: kein Git-Repo")
    keine_git_filter(archiv, "archiv")
    with signaturen.geladener_anker(archiv) as anker:
        signaturen.pruefe_signierschluessel(archiv, anker)
    oberordner = archiv / PRUEFUNGEN
    if oberordner.is_symlink() or (oberordner.exists() and not oberordner.is_dir()):
        raise NachweisFehler("archiv", f"{oberordner}: kein echtes Verzeichnis (Symlink?) — "
                                       "Archiv verweigert")
    if _git(archiv, "status", "--porcelain", "--untracked-files=all").strip():
        raise NachweisFehler("archiv", f"{archiv}: Arbeitsbaum nicht sauber — erst klären, "
                                       "dann schreiben")


def schreibe_pruefung(archiv: Path, art: str, instanz: str | None, daten: dict[str, Any],
                      dateien: dict[str, bytes] | None = None, *, heute: date) -> Path:
    """Nachweis nach dem Release unter `pruefungen/<heute>[.<lauf>]-<art>[-<instanz>]/`:
    `ergebnis.json` (kanonisches JSON aus `daten`) und die `dateien` (Name → Inhalt), signiert
    committet (`pruefung: <art> [<instanz> ]<heute>[.<lauf>]`), nicht gepusht. Vorher muss der
    Vertrauensanker des Archivs vorliegen; danach wird der Commit gegen ihn geprüft (nur Status
    `G`). Weitere Läufe am selben Tag bekommen die nächste Laufnummer; ein Name, der existiert
    oder je im Archiv stand, wird nie wieder belegt — Nachweise werden nie überschrieben."""
    beigaben = dict(dateien or {})
    _pruefe_pruefungsnamen(art, instanz, beigaben)
    if not isinstance(heute, date):
        raise NachweisFehler("archiv", "heute muss ein Datum sein")
    if not isinstance(daten, dict):
        raise NachweisFehler("archiv", "Prüfergebnis muss ein JSON-Objekt sein")
    try:
        ergebnis = kanonisch(daten)
    except (TypeError, ValueError) as e:
        raise NachweisFehler("archiv", f"Prüfergebnis nicht als JSON darstellbar: {e}") from None
    if not (archiv / ".git").exists():
        raise NachweisFehler("archiv", f"{archiv}: kein Git-Repo")
    keine_git_filter(archiv, "archiv")
    with signaturen.geladener_anker(archiv) as anker:
        signaturen.pruefe_signierschluessel(archiv, anker)
        return _schreibe_pruefung(archiv, art, instanz, ergebnis, beigaben, heute, anker)


def _schreibe_pruefung(archiv: Path, art: str, instanz: str | None, ergebnis: bytes,
                       beigaben: dict[str, bytes], heute: date,
                       anker: signaturen.Anker) -> Path:
    oberordner = archiv / PRUEFUNGEN
    if oberordner.is_symlink() or (oberordner.exists() and not oberordner.is_dir()):
        raise NachweisFehler("archiv", f"{oberordner}: kein echtes Verzeichnis (Symlink?) — "
                                       "Archiv verweigert")
    # Nächster freier Lauf des Tages: übersprungen wird, was existiert oder je im Archiv stand.
    for lauf in itertools.count(1):
        name = pruefungsname(heute, art, instanz, lauf)
        relativ = f"{PRUEFUNGEN}/{name}"
        ziel = oberordner / name
        if not (ziel.exists() or ziel.is_symlink()
                or _git(archiv, "log", "--format=%H", "-1", "--", relativ).strip()):
            break
    if _git(archiv, "status", "--porcelain", "--untracked-files=all").strip():
        raise NachweisFehler("archiv", f"{archiv}: Arbeitsbaum nicht sauber — erst klären, "
                                       "dann schreiben")
    oberordner.mkdir(exist_ok=True)
    try:
        ziel.mkdir()
    except FileExistsError:
        raise NachweisFehler("archiv-unveraenderlich", f"{relativ} existiert schon im Archiv — "
                                                       "Nachweise werden nie überschrieben"
                             ) from None
    nachricht = " ".join(["pruefung:", art, *([instanz] if instanz is not None else []),
                          heute.isoformat() + (f".{lauf}" if lauf > 1 else "")])
    try:
        for datei, inhalt in [(ERGEBNIS_DATEI, ergebnis), *beigaben.items()]:
            with (ziel / datei).open("xb") as f:
                f.write(inhalt)
        _git(archiv, "add", "--", relativ)
        # -S: ohne Signierschlüssel scheitert der Commit, statt unsigniert zu entstehen.
        _git(archiv, "commit", "-q", "-S", "-m", nachricht)
    except (OSError, NachweisFehler) as e:
        subprocess.run(git_befehl("reset", "-q", "--", relativ, repo=archiv),
                       capture_output=True, check=False, stdin=subprocess.DEVNULL,
                       env=gefilterte_umgebung())
        shutil.rmtree(ziel, ignore_errors=True)
        with contextlib.suppress(OSError):
            oberordner.rmdir()  # nur, wenn leer
        if isinstance(e, NachweisFehler):
            raise
        raise NachweisFehler("archiv", f"Schreiben ins Archiv fehlgeschlagen: {e}") from None
    # Nach dem Commit ist das Ziel belegt (nur anhängen): Abweichungen brechen ab, ohne etwas
    # zurückzunehmen.
    _pruefe_commit(archiv, relativ, ziel)
    signaturen.pruefe_commit(archiv, "HEAD", anker)
    return ziel


def schreibe_betrieb(archiv: Path, datei: str, inhalt: bytes, nachricht: str) -> Path:
    """Fortgeschriebene Betriebsdatei `betrieb/<datei>` (nur aus `BETRIEB_DATEIEN`) schreiben und
    signiert committen (`<nachricht>`), nicht gepusht. Anders als Prüfungen wird sie ersetzt —
    die Historie in Git bleibt. Gleiche Bedingungen wie bei Prüfungen: Vertrauensanker,
    sauberer Arbeitsbaum, Commit gegen den Anker geprüft. Ein unverändertes Ergebnis erzeugt
    keinen Commit."""
    if datei not in BETRIEB_DATEIEN:
        raise NachweisFehler("archiv", f"Betriebsdatei „{datei}“ unbekannt (erlaubt: "
                                       f"{', '.join(sorted(BETRIEB_DATEIEN))})")
    if not isinstance(inhalt, bytes) or not nachricht.strip() or "\n" in nachricht:
        raise NachweisFehler("archiv", "Betriebsdatei: Inhalt als bytes, Nachricht einzeilig")
    if not (archiv / ".git").exists():
        raise NachweisFehler("archiv", f"{archiv}: kein Git-Repo")
    keine_git_filter(archiv, "archiv")
    with signaturen.geladener_anker(archiv) as anker:
        signaturen.pruefe_signierschluessel(archiv, anker)
        ordner = archiv / BETRIEB
        if ordner.is_symlink() or (ordner.exists() and not ordner.is_dir()):
            raise NachweisFehler("archiv", f"{ordner}: kein echtes Verzeichnis (Symlink?) — "
                                           "Archiv verweigert")
        ziel = ordner / datei
        if ziel.is_symlink() or (ziel.exists() and not ziel.is_file()):
            raise NachweisFehler("archiv", f"{ziel}: keine reguläre Datei — Archiv verweigert")
        if _git(archiv, "status", "--porcelain", "--untracked-files=all").strip():
            raise NachweisFehler("archiv", f"{archiv}: Arbeitsbaum nicht sauber — erst klären, "
                                           "dann schreiben")
        relativ = f"{BETRIEB}/{datei}"
        if ziel.exists() and ziel.read_bytes() == inhalt:
            return ziel
        ordner.mkdir(exist_ok=True)
        vorher = ziel.read_bytes() if ziel.exists() else None
        try:
            ziel.write_bytes(inhalt)
            _git(archiv, "add", "--", relativ)
            _git(archiv, "commit", "-q", "-S", "-m", nachricht)
        except (OSError, NachweisFehler) as e:
            subprocess.run(git_befehl("reset", "-q", "--", relativ, repo=archiv),
                           capture_output=True, check=False, stdin=subprocess.DEVNULL,
                           env=gefilterte_umgebung())
            with contextlib.suppress(OSError):
                if vorher is None:
                    ziel.unlink(missing_ok=True)
                    ordner.rmdir()
                else:
                    ziel.write_bytes(vorher)
            if isinstance(e, NachweisFehler):
                raise
            raise NachweisFehler("archiv", f"Schreiben ins Archiv fehlgeschlagen: {e}") from None
        # Der committete Blob ist byte-gleich zum Geschriebenen (Filter/Attribute ändern nichts).
        if _git_bytes(archiv, "show", f"HEAD:{relativ}") != inhalt:
            raise NachweisFehler("archiv-nachweis", f"Commit weicht von der geschriebenen Datei "
                                                    f"ab: {relativ}")
        signaturen.pruefe_commit(archiv, "HEAD", anker)
        return ziel
