"""Release-Orchestrierung (siehe ADR 0010 „Release-Kette fail-closed“): eine fail-closed Kette
aus festen Stufen.

    einfrieren → register → qualitaetstor → sast → sbom → fuzzing → egress → scan
    → negativkontrollen → lizenzen → matrix → export → manifest → archiv → tag → abschluss

- Jede Stufe ist eine Funktion `Lauf → Bericht` (in Tests ersetzbar). Die Orchestrierung schreibt
  je Stufe `<arbeitsordner>/berichte/<stufe>.json` — auch für die abbrechende Stufe (mit Regel
  und Text). Bricht Stufe n ab, laufen die Stufen > n nicht; das Archiv beschreibt allein die
  Stufe `archiv`, ein Abbruch davor hinterlässt dort also nichts.
- **Alle Eingaben stammen aus dem eingefrorenen Worktree** (`git worktree add --detach` aus dem
  Haupt-Repo, volle Historie): Register, Werkzeug-Pins, VEX, Lockdateien, Lizenzregeln,
  Nachweisquellen, `allowed_signers`. Der Zustand des Aufrufer-Checkouts spielt keine Rolle.
  Einzige Ausnahme ist der Vertrauensanker der Commit-Signatur: er kommt aus dem Archiv
  (`vertrauensanker/`), nie aus dem geprüften Commit.
- Scan-Gate und Lizenzabgleich gelten den ausgelieferten Artefakten
  (`manifest.AUSGELIEFERT`, ab Schema /3 web, server, mcp und import). Nicht ausgelieferte
  Artefakte (bis Schema /2 der Import) werden mit `ausgeliefert: false` archiviert, ihre Treffer
  stehen informativ im Bericht.
- Die Nachweisschlüssel `manifest` und `archiv` werden nicht in der Stufe `matrix`,
  sondern nach Signaturprüfung bzw. Archivierung (vor dem Tag) bestätigt.
- `--trocken`: alles außer Archiv, Tag und Abschluss; das Manifest trägt `"trockenlauf": true`,
  der Export wird in ein Wegwerf-Repo im Arbeitsordner committet (nie in das echte
  Export-Repo).
- Export-Repo: der geprüfte Export wird als Commit auf `release-kandidat/<version>` abgelegt;
  `main` rückt erst in der Stufe `tag` vor — nach erfolgreicher Archivierung — und bekommt dort
  einen annotierten Tag `v<version>` mit dem Manifest-Hash. Ein abgebrochener Lauf lässt `main`
  unverändert; ein neuer Lauf derselben Version verwirft den übrig gebliebenen Kandidaten.
- Nichts verlässt den Rechner (außer den protokollierten Netzschritten für Abhängigkeiten und
  Werkzeuge): Export-Repo und Archiv werden nur lokal committet, der Tag nur lokal gesetzt.
- Die Manifest-Signatur belegt, dass die Kette bis einschließlich der Stufe `manifest` auf der
  Release-Workstation lief; erst der signierte Abschlussnachweis (Stufe `abschluss`, Modul
  `abschluss`) belegt, dass auch Archiv und Tag gelungen sind — der Zielhost verlangt beide.
  Keine der Signaturen belegt, dass der Autor das Release freigegeben hat."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from . import (
    abschluss,
    darstellung,
    egress,
    export,
    fuzzing,
    gate,
    gitobjekte,
    lizenzen,
    manifest,
    osv,
    register,
    sast,
    sbom,
    scan,
    signaturen,
    vex,
)
from . import negativkontrollen as nk
from .archiv import archiviere
from .artefakte import Netzschritt
from .fehler import NachweisFehler
from .register import Kontrolle
from .versionen import versionen_einheitlich
from .werkzeuge import (
    Werkzeug,
    gefilterte_umgebung,
    git_befehl,
    keine_git_filter,
    lade_werkzeuge,
)

STUFEN = ("einfrieren", "register", "qualitaetstor", "sast", "sbom", "fuzzing", "egress", "scan",
          "negativkontrollen", "lizenzen", "matrix", "export", "manifest", "archiv", "tag",
          "abschluss")
#: Stufen, die ein Trockenlauf auslässt.
NUR_ECHT = ("archiv", "tag", "abschluss")
#: Ein Release-Commit muss auf einem dieser Zweige liegen.
RELEASE_ZWEIGE = ("main", "feat/nachweise")
#: Neutrale Identität der öffentlichen Release-Commits (Autor und Committer).
OEFFENTLICHE_IDENTITAET = ("scanview release", "security@sitebits.de")
#: Nach Signatur bzw. Archivierung bestätigt, nicht in der Stufe `matrix`.
NACH_ARCHIV = ("manifest", "archiv")
NACHWEISQUELLEN = Path("nachweise/nachweisquellen.toml")
EXPORTMANIFEST = "export-manifest.json"
VAULT_VARIABLE = "SCANVIEW_VAULT"
STANDARD_ARCHIV = Path.home() / "Workspace" / "Code" / "scanview-nachweise"
STANDARD_SCHLUESSEL = Path.home() / ".ssh" / "scanview-release_ed25519"
STANDARD_EXPORT_ZIEL = Path.home() / "Workspace" / "Code" / "scanview-oeffentlich"

_VERSION = re.compile(r"\d+\.\d+\.\d+")
_STDERR_AUSZUG = 2000
_PYTHON_TEILE = ("server", "mcp-server", "nachweise", "import")
KANDIDAT = "release-kandidat/"


def heute() -> date:
    """Stichtag der Gates: UTC-Datum der echten Uhr (nie zurückdrehbar)."""
    return scan.jetzt().date()


# --- Befehle -----------------------------------------------------------------------------------

def _umgebung() -> dict[str, str]:
    """Aufruferumgebung ohne die Variablen, die Läufe still verändern könnten
    (`werkzeuge.gefilterte_umgebung`: u. a. `GIT_…`, `VITE_…`, `NODE_OPTIONS`)."""
    return gefilterte_umgebung()


def _befehl_roh(befehl: list[str], regel: str, *, cwd: Path | None = None,
                umgebung: dict[str, str] | None = None, eingabe: bytes | None = None) -> bytes:
    """Befehl ohne Shell, Ausgabe als Bytes; Exit ≠ 0 oder fehlendes Programm →
    `NachweisFehler(regel)` mit dem Ende der Ausgabe. Alle Befehle der Kette laufen hierüber
    (in Tests mitschneidbar)."""
    try:
        r = subprocess.run(befehl, capture_output=True, check=False, cwd=cwd,
                           env=umgebung if umgebung is not None else _umgebung(),
                           input=eingabe, stdin=None if eingabe is not None else subprocess.DEVNULL)
    except OSError as e:
        raise NachweisFehler(regel, f"{befehl[0]} nicht ausführbar: {e.strerror}") from None
    if r.returncode != 0:
        text = (r.stderr.strip() or r.stdout.strip()).decode("utf-8", "replace")
        raise NachweisFehler(regel, f"{' '.join(befehl)} endete mit Exit {r.returncode}"
                                    + (f" (in {cwd})" if cwd else "")
                                    + f": {text[-_STDERR_AUSZUG:]}")
    return r.stdout


def _befehl(befehl: list[str], regel: str, *, cwd: Path | None = None,
            umgebung: dict[str, str] | None = None) -> str:
    return _befehl_roh(befehl, regel, cwd=cwd, umgebung=umgebung).decode("utf-8", "replace")


def _git(repo: Path, *args: str, regel: str = "release-git") -> str:
    return _befehl(git_befehl(*args, repo=repo), regel).strip()


def _git_ok(repo: Path, *args: str) -> bool:
    try:
        _git(repo, *args)
    except NachweisFehler:
        return False
    return True


# --- Laufzustand --------------------------------------------------------------------------------

@dataclass
class Lauf:
    """Eingaben und Zwischenergebnisse eines Release-Laufs."""

    repo: Path
    commit: str
    version: str
    archiv: Path
    schluessel: Path
    export_ziel: Path
    bestaetigt: str | None
    trocken: bool
    arbeitsordner: Path
    cache: Path
    melde: Callable[[str], None] = print
    worktree: Path = field(default=Path())
    voll: str = ""
    worktree_angelegt: bool = False
    werkzeuge: dict[str, Werkzeug] = field(default_factory=dict)
    pruefungen: dict[str, str] = field(default_factory=dict)
    netzschritte: list[Netzschritt] = field(default_factory=list)
    sbom: sbom.SbomErgebnis | None = None
    db_stand: scan.DbStand | None = None
    exportcommit: str | None = None
    exportmanifest: Path | None = None
    #: Grund, warum der Export nicht veröffentlicht werden darf (None = veröffentlichbar).
    sperrgrund: str | None = None
    manifest_pfad: Path | None = None
    kontrollen: list[Kontrolle] | None = None

    def __post_init__(self) -> None:
        if self.worktree == Path():
            self.worktree = self.arbeitsordner / "worktree"

    @property
    def ausgabe(self) -> Path:
        return self.arbeitsordner / "ausgabe"

    @property
    def berichte(self) -> Path:
        return self.arbeitsordner / "berichte"


Stufe = Callable[[Lauf], dict[str, Any]]


def _bestanden(lauf: Lauf, *namen: str) -> None:
    for n in namen:
        lauf.pruefungen[n] = "bestanden"


def _json(pfad: Path) -> dict[str, Any]:
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise NachweisFehler("release-datei", f"{pfad}: nicht lesbar ({e})") from None
    if not isinstance(daten, dict):
        raise NachweisFehler("release-datei", f"{pfad}: kein JSON-Objekt")
    return daten


def _schreibe_json(pfad: Path, daten: object) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with pfad.open("x", encoding="utf-8") as f:
        f.write(json.dumps(daten, indent=2, ensure_ascii=False) + "\n")


# --- Stufe 1: einfrieren -------------------------------------------------------------------------

def _version_im_archiv(archiv: Path, version: str) -> bool:
    if (archiv / "releases" / version).exists():
        return True
    return bool(_git(archiv, "log", "--format=%H", "-1", "--", f"releases/{version}",
                     regel="release-version"))


def _abhaengigkeiten(lauf: Lauf) -> list[Netzschritt]:
    """Abhängigkeiten des eingefrorenen Worktrees gegen die Lockfiles (Netz, protokolliert):
    web `npm ci --ignore-scripts`, jedes Python-Teilprojekt `uv sync --locked`, danach die
    gitignorierten Probe-Werkzeuge setfacl/getfacl (`deploy_probe`, Paket per SHA-256 aus dem
    Lock geprüft) — ohne sie liefen die Freischalt-Probetests im Worktree übersprungen, und
    übersprungen blockiert (Negativtest-Kategorie NT-FREISCHALTEN, Retest B-WB-M4)."""
    schritte: list[Netzschritt] = []
    befehle = [(["npm", "ci", "--ignore-scripts"], "web"),
               *((["uv", "sync", "--locked"], t) for t in _PYTHON_TEILE),
               (["uv", "run", "--locked", "python", "-m", "scanview_nachweise.deploy_probe"],
                "nachweise")]
    for befehl, teil in befehle:
        schritte.append(Netzschritt(befehl, teil, datetime.now(UTC).isoformat(timespec="seconds")))
        _befehl(befehl, "release-abhaengigkeiten", cwd=lauf.worktree / teil)
    return schritte


def _keine_filtertreiber(repo: Path) -> None:
    """`werkzeuge.keine_git_filter` für das Einfrieren (Regel `release-worktree`)."""
    keine_git_filter(repo, "release-worktree")


def pruefe_worktree(repo: Path, worktree: Path, commit: str) -> int:
    """Der ausgecheckte Worktree entspricht dem (hashgeprüften) Baum von `commit` — unabhängig
    vom Index: jede Datei mit ihrer Blob-ID (Inhalt selbst gehasht), Ausführbarkeit und
    Symlink-Ziel; keine zusätzlichen (auch keine ignorierten) und keine fehlenden Dateien, keine
    Submodule, kein assume-unchanged/skip-worktree im Index. Rückgabe: Anzahl der Dateien."""
    regel = "release-worktree"
    index = _befehl(git_befehl("ls-files", "-v", "-z", repo=worktree), regel)
    markiert = [e[2:] for e in index.split("\0") if e and (e[0].islower() or e[0] == "S")]
    if markiert:
        raise NachweisFehler(regel, "Index mit assume-unchanged/skip-worktree: "
                                    + ", ".join(sorted(markiert)[:10]))
    with gitobjekte.Objektleser(repo, regel) as leser:
        soll = leser.dateien(commit)
        verfahren = leser.verfahren
    ist: dict[str, Path] = {}
    ordner_ist: set[str] = set()

    def lesefehler(fehler: OSError) -> None:
        raise NachweisFehler(regel, f"Worktree nicht vollständig lesbar: {fehler}")
    for ordner, unterordner, dateien in os.walk(worktree, onerror=lesefehler):
        basis = Path(ordner)
        if basis != worktree:
            ordner_ist.add(basis.relative_to(worktree).as_posix())
        if basis == worktree:
            if ".git" in dateien:
                dateien.remove(".git")
            if ".git" in unterordner:
                raise NachweisFehler(regel, "Worktree mit eigenem .git-Verzeichnis")
        for name in list(unterordner):
            pfad = basis / name
            if pfad.is_symlink():  # os.walk führt Links auf Ordner als Unterordner
                unterordner.remove(name)
                dateien.append(name)
        for name in dateien:
            pfad = basis / name
            ist[pfad.relative_to(worktree).as_posix()] = pfad
    if set(ist) != set(soll):
        zuviel = sorted(set(ist) - set(soll))
        fehlt = sorted(set(soll) - set(ist))
        raise NachweisFehler(regel, f"Worktree weicht vom Baum {commit[:12]} ab: zusätzlich "
                                    f"{zuviel[:10] or '-'}, fehlend {fehlt[:10] or '-'}")
    ordner_soll = {"/".join(p.split("/")[:i]) for p in soll for i in range(1, p.count("/") + 1)}
    if ordner_ist - ordner_soll:
        raise NachweisFehler(regel, "Worktree mit zusätzlichen Ordnern: "
                                    f"{sorted(ordner_ist - ordner_soll)[:10]}")
    for relativ, (modus, oid) in soll.items():
        pfad = ist[relativ]
        st = pfad.lstat()
        if modus == "120000":
            if not stat.S_ISLNK(st.st_mode):
                raise NachweisFehler(regel, f"{relativ}: Symlink erwartet")
            inhalt = os.fsencode(os.readlink(pfad))
        elif modus in ("100644", "100755"):
            if not stat.S_ISREG(st.st_mode):
                raise NachweisFehler(regel, f"{relativ}: reguläre Datei erwartet")
            ausfuehrbar = st.st_mode & 0o111
            if st.st_mode & 0o7000 or (ausfuehrbar != 0) != (modus == "100755") or (
                    modus == "100755" and not st.st_mode & 0o100):
                raise NachweisFehler(regel, f"{relativ}: Rechte {oct(st.st_mode & 0o7777)} passen "
                                            f"nicht zu {modus}")
            inhalt = pfad.read_bytes()
        else:
            raise NachweisFehler(regel, f"{relativ}: Modus {modus} (Submodul?) nicht erlaubt")
        if gitobjekte.blob_id(inhalt, verfahren) != oid:
            raise NachweisFehler(regel, f"{relativ}: Inhalt weicht vom Release-Commit ab")
    return len(soll)


def _commit_signatur(lauf: Lauf) -> dict[str, Any]:
    """Stichtag-Commit und jeder Commit, der vom Release-Commit aus erreichbar ist, aber weder vom
    Stichtag noch von einem Altstand (`git rev-list <release> --not <stichtag> <altstaende…>`),
    gegen den Vertrauensanker **im Archiv** (nie aus dem Worktree: ein Commit brächte sonst die
    Schlüssel mit, gegen die er geprüft wird)."""
    anker = signaturen.lade_anker(lauf.archiv, lauf.arbeitsordner / "vertrauensanker")
    if set(anker.zweige) != set(RELEASE_ZWEIGE):
        raise NachweisFehler(signaturen.REGEL_ANKER, f"zweige im Anker ({', '.join(anker.zweige)})"
                                                     " weichen von den Release-Zweigen der Kette "
                                                     f"({', '.join(RELEASE_ZWEIGE)}) ab")
    gesehen: set[str] = set()
    anzahl = signaturen.pruefe_seit_stichtag(lauf.repo, anker, lauf.voll, gesehen=gesehen)
    fingerprint = signaturen.pruefe_commit(lauf.repo, lauf.voll, anker)
    # Gegen welchen Anker geprüft wurde (Archiv-Commit, Prüfsumme der Signers) und welche
    # Schlüssel tatsächlich vorkamen — eindeutig auch nach einer Rotation.
    return {"fingerprint": fingerprint, "commits": anzahl, "stichtag": anker.stichtag,
            "altstaende": len(anker.altstaende), "fingerprints": sorted(gesehen),
            "anker_commit": anker.commit, "signers_sha256": anker.signers_sha256}


def stufe_einfrieren(lauf: Lauf) -> dict[str, Any]:
    """Version `X.Y.Z` (weder im Archiv noch als Tag vorhanden), Commit auf einem Release-Zweig,
    Signierschlüssel vorhanden, dann frischer Worktree mit voller Historie auf dem Commit, die
    Commit-Signaturen seit dem Stichtag (auch im Trockenlauf), die Abhängigkeiten gegen die
    Lockfiles und die Prüfwerkzeuge Bandit (SAST) und Schemathesis (Fuzzing) aus ihren Locks."""
    if not isinstance(lauf.version, str) or not _VERSION.fullmatch(lauf.version):
        raise NachweisFehler("release-version", f"Version „{lauf.version}“ entspricht nicht "
                                                "X.Y.Z (für Proben: --trocken)")
    if lauf.archiv.exists() or not lauf.trocken:
        if not (lauf.archiv / ".git").exists():
            raise NachweisFehler("release-archiv", f"{lauf.archiv}: kein Git-Repo")
        if _version_im_archiv(lauf.archiv, lauf.version):
            raise NachweisFehler("release-version", f"Version {lauf.version} steht schon im "
                                                    f"Archiv {lauf.archiv} — fehlt nur der Tag, "
                                                    "siehe docs/public/release.md "
                                                    "„Abbruch nach dem Archivieren“")
    if not lauf.schluessel.is_file():
        raise NachweisFehler("release-schluessel", f"{lauf.schluessel}: Signierschlüssel fehlt")
    if lauf.commit.startswith("-"):
        raise NachweisFehler("release-commit", f"„{lauf.commit}“ ist kein Commit")
    try:
        lauf.voll = _git(lauf.repo, "rev-parse", "--verify", "--end-of-options",
                         f"{lauf.commit}^{{commit}}", regel="release-commit")
    except NachweisFehler:
        raise NachweisFehler("release-commit", f"„{lauf.commit}“ ist kein Commit in "
                                               f"{lauf.repo}") from None
    zweige = [z for z in RELEASE_ZWEIGE
              if _git_ok(lauf.repo, "rev-parse", "--verify", "-q", f"refs/heads/{z}")
              and _git_ok(lauf.repo, "merge-base", "--is-ancestor", lauf.voll, f"refs/heads/{z}")]
    if not zweige:
        raise NachweisFehler("release-commit", f"{lauf.voll[:12]} liegt auf keinem Release-Zweig "
                                               f"({', '.join(RELEASE_ZWEIGE)})")
    if _git_ok(lauf.repo, "rev-parse", "--verify", "-q", f"refs/tags/v{lauf.version}"):
        raise NachweisFehler("release-tag", f"Tag v{lauf.version} existiert schon (Rest eines "
                                            "Laufs? siehe docs/public/release.md „Abbruch nach "
                                            "dem Archivieren“)")
    # Auch im Export-Repo: ein vorhandener Tag ließe die Freigabe erst nach der Archivierung
    # scheitern — dann wäre die Version verbraucht.
    if not lauf.trocken and (lauf.export_ziel / ".git").exists() and _git_ok(
            lauf.export_ziel, "rev-parse", "--verify", "-q", f"refs/tags/v{lauf.version}"):
        raise NachweisFehler("release-tag", f"Tag v{lauf.version} existiert schon im Export-Repo "
                                            f"{lauf.export_ziel}")

    if lauf.worktree.exists():
        raise NachweisFehler("release-worktree", f"{lauf.worktree}: existiert schon")
    _keine_filtertreiber(lauf.repo)
    # Erst ohne Checkout anlegen: Konfiguration, die nur im gitdir des neuen Worktrees gilt
    # (includeIf gitdir:…/worktrees/…), ist erst danach sichtbar und wird vor dem Auschecken
    # geprüft.
    _git(lauf.repo, "worktree", "add", "--no-checkout", "--detach",
         str(lauf.worktree), lauf.voll, regel="release-worktree")
    lauf.worktree_angelegt = True
    _keine_filtertreiber(lauf.worktree)
    _git(lauf.worktree, "checkout", "-q", "--detach", lauf.voll,
         regel="release-worktree")
    if _git(lauf.worktree, "rev-parse", "HEAD") != lauf.voll:
        raise NachweisFehler("release-worktree", "Worktree steht nicht auf dem Release-Commit")
    if _git(lauf.worktree, "rev-parse", "--is-shallow-repository") != "false":
        raise NachweisFehler("release-worktree", "flache Historie — Befund-Gate braucht alle "
                                                 "Fix-Commits")
    if _git(lauf.worktree, "status", "--porcelain", "--untracked-files=all"):
        raise NachweisFehler("release-worktree", "frischer Worktree ist nicht sauber")
    # Unabhängig vom Index (assume-unchanged/skip-worktree machen `status` blind).
    dateien = pruefe_worktree(lauf.repo, lauf.worktree, lauf.voll)
    einheitlich = versionen_einheitlich(lauf.worktree)
    if einheitlich != lauf.version:
        raise NachweisFehler("release-version", f"angefordert {lauf.version}, der Commit trägt "
                                                f"{einheitlich} (web, server, mcp-server, "
                                                "nachweise)")
    # Vor jedem Netzschritt: ein unsigniertes Release scheitert ohnehin.
    commit_signatur = _commit_signatur(lauf)
    _bestanden(lauf, "commit-signatur")
    lauf.netzschritte += _abhaengigkeiten(lauf)
    lauf.werkzeuge = lade_werkzeuge(lauf.worktree / "nachweise" / "werkzeuge.json")
    # Bandit schon hier (Netzschritt im Manifest): die Docker-Tests des Qualitätstors brauchen es
    # und installierten es sonst selbst, unprotokolliert; die Stufe `sast` prüft nur noch die Marke.
    lauf.netzschritte += sast.bandit_bereitstellen(lauf.worktree / "nachweise", lauf.werkzeuge)
    # Schemathesis ebenso: die Docker-Tests und die Negativkontrolle `fuzzing-serverfehler`
    # brauchen es vor der Stufe `fuzzing`.
    lauf.netzschritte += fuzzing.schemathesis_bereitstellen(lauf.worktree / "nachweise",
                                                            lauf.werkzeuge)
    return {"commit": lauf.voll, "version": lauf.version, "zweige": zweige,
            "trockenlauf": lauf.trocken, "netzschritte": len(lauf.netzschritte),
            "worktree_dateien": dateien, "commit_signatur": commit_signatur}


def worktree_entfernen(lauf: Lauf) -> str | None:
    """Eigenen Worktree mit einfachem `git worktree remove` entfernen (nie erzwungen); scheitert
    das, wird der Rest gemeldet statt gelöscht."""
    if not lauf.worktree_angelegt:
        return None
    try:
        _git(lauf.repo, "worktree", "remove", str(lauf.worktree), regel="release-worktree")
    except NachweisFehler as e:
        return f"Worktree nicht entfernt ({lauf.worktree}): {e.text}"
    lauf.worktree_angelegt = False
    return None


# --- Stufe 2: Register, Befund-Gate, Negativtests ------------------------------------------------

def stufe_register(lauf: Lauf) -> dict[str, Any]:
    wt = lauf.worktree
    r = register.lade_register(wt)
    abweichend = darstellung.abweichungen(wt, darstellung.fassungen(r))
    if abweichend:
        raise NachweisFehler("register-darstellung", "Markdown weicht von den Registern ab: "
                                                     + ", ".join(abweichend))
    stichtag = heute()
    # Befunde, die nur nicht ausgelieferte Artefakte betreffen, blockieren nicht, stehen
    # aber im Bericht (und damit im Manifest).
    blockiert = gate.befund_gate(r.befunde, stichtag, ausgeliefert=manifest.AUSGELIEFERT)
    informativ = gate.befunde_nicht_ausgeliefert(r.befunde, stichtag, manifest.AUSGELIEFERT)
    for zeile in informativ:
        lauf.melde(f"INFORMATIV {zeile}")
    if blockiert:
        raise NachweisFehler("befund-gate", f"{len(blockiert)} blockierende Befunde: "
                                            + "; ".join(blockiert))
    ergebnis = gate.negativtests_details(wt, kategorien=r.negativtests)
    rot = {i: e.grund for i, e in ergebnis.items() if not e.gruen}
    if rot:
        raise NachweisFehler("negativtest-gate", "rot: " + "; ".join(
            f"{i} ({g})" if g else i for i, g in rot.items()))
    # Jeder pytest- und Vitest-Retest eines behobenen Befunds läuft (übersprungen
    # oder unbekannte Art = rot); Caddy-Prüfpunkte laufen gegen die Instanz.
    retest = gate.retests_details(wt, r.befunde)
    if not retest.gruen:
        raise NachweisFehler("retest-gate", f"Retests behobener Befunde rot: {retest.grund}")
    _bestanden(lauf, "register", "befunde", "negativtests", "retests")
    return {"kontrollen": len(r.kontrollen), "befunde": len(r.befunde),
            "befunde_nicht_ausgeliefert": informativ, "stichtag":
            stichtag.isoformat(), "negativtests": {i: e.ausgefuehrt for i, e in ergebnis.items()},
            "retests": {"ausgefuehrt": retest.ausgefuehrt,
                        "ausserhalb": gate.retest_ausserhalb(r.befunde)}}


# --- Stufe 3: Qualitätstor -----------------------------------------------------------------------

#: Setzt die Kette für die Testläufe des Qualitätstors; Tests, die `tests/werkzeugpflicht.py`
#: benutzen (gpg, Docker), schlagen dann bei fehlendem Werkzeug fehl, statt zu überspringen.
#: Einzelne Tests überspringen weiterhin; was sie prüfen, deckt dann eine Pflichtprüfung mit
#: demselben Werkzeug ab (shellcheck der Betriebsskripte) bzw. die Stufe selbst (gitleaks im
#: Export).
QUALITAETSTOR_VARIABLE = "SCANVIEW_QUALITAETSTOR"
#: Schaltet die Server-Tests mit Marker `oom_live` ein (Unit endet mit `Result=oom-kill`, der
#: KDE-Melder zeigt das an; lokal daher übersprungen). Die Kette setzt sie verpflichtend, damit
#: der Live-Beleg „Speichermangel → speicher“ je Release läuft.
OOM_LIVE_VARIABLE = "SCANVIEW_OOM_LIVE"


def stufe_qualitaetstor(lauf: Lauf) -> dict[str, Any]:
    """Qualitätstor aller Teilprojekte im Worktree: web typecheck/lint/test, je Python-Teilprojekt
    ruff, mypy (`src`, bei vorhandenem `scripts/` auch das), pytest."""
    wt = lauf.worktree
    gelaufen: list[str] = []
    for befehl in (["npm", "run", "typecheck"], ["npm", "run", "lint"], ["npm", "test"]):
        _befehl(befehl, "qualitaetstor", cwd=wt / "web")
        gelaufen.append(f"web: {' '.join(befehl)}")
    for teil in _PYTHON_TEILE:
        ziele = ["src", *(["scripts"] if (wt / teil / "scripts").is_dir() else [])]
        ratsche = ["uv", "run", "--locked", "scanview-nachweise", "pruefen", "ratsche",
                   "--wurzel", str(wt.resolve()), "--teil", teil]
        _befehl(ratsche, "qualitaetstor", cwd=wt / "nachweise")
        gelaufen.append(f"{teil}: Ruff-Ratsche")
        for befehl in (
                       ["uv", "run", "--locked", "mypy", *ziele],
                       ["uv", "run", "--locked", "pytest", "-q"]):
            # Pytest bekommt die Variable ausdrücklich (die gefilterte Aufruferumgebung würde
            # sie abstreifen): ein fehlendes Werkzeug (gpg, Docker) ist hier ein Fehlschlag,
            # kein stilles Überspringen.
            nur_tests = "pytest" in befehl
            _befehl(befehl, "qualitaetstor", cwd=wt / teil,
                    umgebung={**_umgebung(), QUALITAETSTOR_VARIABLE: "1",
                              OOM_LIVE_VARIABLE: "1"} if nur_tests else None)
            gelaufen.append(f"{teil}: {' '.join(befehl)}")
    _bestanden(lauf, "qualitaetstor")
    return {"befehle": gelaufen}


# --- Stufe 4: SAST -------------------------------------------------------------------------------

def stufe_sast(lauf: Lauf) -> dict[str, Any]:
    """Semgrep (vendierter, per Lock geprüfter Regelsatz) und Bandit (aus seinem Lock installiert,
    Netzschritt) ohne Netz über die Quellbäume des Worktrees; Rohberichte
    `berichte/sast-semgrep.json` und `berichte/sast-bandit.json`. Gate: jeder Fund ≥ mittel
    steht im Befundregister (Fingerabdruck), jeder übrige dort oder in
    `docs/compliance/sast-ausnahmen.toml`; der Status der Befunde selbst bewertet schon das
    Befund-Gate der Stufe `register`. Der Rückgabewert ist der Stufenbericht `sast.json`."""
    e = sast.pruefen(lauf.worktree, lauf.werkzeuge, lauf.berichte)
    lauf.netzschritte += e.netzschritte
    for zeile in e.bericht["ausnahmen_ohne_fund"]:
        lauf.melde(f"HINWEIS {zeile}")
    if e.blockiert:
        raise NachweisFehler("sast-gate", f"{len(e.blockiert)} Fund(e) ohne Befund bzw. "
                                          "Ausnahme: " + "; ".join(e.blockiert))
    _bestanden(lauf, "sast")
    return e.bericht


# --- Stufe 5: Artefakte + SBOM ------------------------------------------------------------------

def stufe_sbom(lauf: Lauf) -> dict[str, Any]:
    """Artefakte bauen, je Artefakt SBOM erzeugen/nachbearbeiten/abdecken/validieren (Werkzeuge
    frisch in den Worktree installiert), dann jedes SBOM an sein Artefakt binden. Der Bau prüft
    dabei auch die npm-Registry-Signaturen — ihr Ergebnis schreibt diese Stufe
    als eigenen Bericht `berichte/npm-signaturen.json` und bestätigt den Nachweisschlüssel
    `npm-signaturen` gesondert von `sbom`."""
    wt = lauf.worktree
    e = sbom.erzeuge_alle(wt, wt, lauf.version, lauf.ausgabe,
                          arbeitsordner=lauf.arbeitsordner / "sbom-arbeit")
    lauf.sbom = e
    lauf.netzschritte += e.netzschritte
    for a in e.bau.artefakte:
        sbom.pruefe_bindung(_json(e.sboms[a.name]), a, lauf.version)
    if e.bau.npm_signaturen is None:
        raise NachweisFehler("release-reihenfolge", "npm-Signaturprüfung der Bau-Stufe fehlt")
    _schreibe_json(lauf.berichte / "npm-signaturen.json", e.bau.npm_signaturen)
    _bestanden(lauf, "sbom", "npm-signaturen")
    return {"zeilen": e.zeilen,
            "artefakte": {a.name: {"datei": a.pfad.name, "sha256": a.sha256,
                                   "dateien": len(a.lieferumfang),
                                   "ausgeliefert": a.name in manifest.AUSGELIEFERT}
                          for a in e.bau.artefakte}}


# --- Stufe 6: Fuzzing ---------------------------------------------------------------------------

def stufe_fuzzing(lauf: Lauf) -> dict[str, Any]:
    """Schemathesis gegen eine Wegwerf-Instanz aus dem Worktree (gebautes `web/dist`, Server aus
    dem Worktree, synthetische Pakete; Caddy auf localhost:18090, Server auf 127.0.0.1:18097), je
    als Betrachter und als Admin mit festem Seed. Rohberichte `berichte/fuzzing-admin.xml` und
    `berichte/fuzzing-betrachter.xml`; Host-Netz-Läufe als Netzschritte. Der Rückgabewert ist der
    Stufenbericht `fuzzing.json`."""
    if lauf.sbom is None:
        raise NachweisFehler("release-reihenfolge", "Fuzzing braucht das gebaute web/dist der "
                                                    "SBOM-Stufe")
    e = fuzzing.pruefen(lauf.worktree, lauf.werkzeuge, lauf.berichte, lauf.arbeitsordner)
    lauf.netzschritte += e.netzschritte
    for ergebnis in e.bericht["laeufe"].values():
        for zeile in ergebnis["hinweise"]:
            lauf.melde(f"HINWEIS {zeile}")
    if e.blockiert:
        raise NachweisFehler("fuzzing-gate", f"{len(e.blockiert)} Fehlschlag/Fehlschläge: "
                                             + "; ".join(e.blockiert))
    _bestanden(lauf, "fuzzing")
    return e.bericht


# --- Stufe 7: Egress ------------------------------------------------------------------------------

def stufe_egress(lauf: Lauf) -> dict[str, Any]:
    """Keine ausgehenden Verbindungen von Portal und Viewer: statische Prüfung des gebauten
    `web/dist` auf absolute URLs und ein Headless-Browser gegen eine Wegwerf-Instanz aus dem
    Worktree mit synthetischem Testpaket (Caddy auf localhost:18090, Server auf 127.0.0.1:18097,
    beide Ports müssen frei sein). Berichte `berichte/egress-statisch.json` und
    `berichte/egress-browser.json`; Host-Netz-Läufe als Netzschritte. Der Rückgabewert ist der
    Stufenbericht `egress.json`."""
    if lauf.sbom is None:
        raise NachweisFehler("release-reihenfolge", "Egress braucht das gebaute web/dist der "
                                                    "SBOM-Stufe")
    e = egress.pruefen(lauf.worktree, lauf.werkzeuge, lauf.berichte, lauf.arbeitsordner)
    lauf.netzschritte += e.netzschritte
    for zeile in e.bericht["hinweise"]:
        lauf.melde(f"HINWEIS {zeile}")
    if e.blockiert:
        raise NachweisFehler("egress-gate", f"{len(e.blockiert)} Fund/Funde: "
                                            + "; ".join(e.blockiert))
    _bestanden(lauf, "egress")
    return e.bericht


# --- Stufe 8: Scan --------------------------------------------------------------------------------

def _sbom_ergebnis(lauf: Lauf) -> sbom.SbomErgebnis:
    if lauf.sbom is None:
        raise NachweisFehler("release-reihenfolge", "SBOM-Stufe fehlt")
    return lauf.sbom


def stufe_scan(lauf: Lauf) -> dict[str, Any]:
    """trivy ohne Netz über alle vier Artefakt-SBOMs (DB ≤ 24 h, Abdeckung fail-closed). Das Gate
    gilt den ausgelieferten Artefakten; Import-Treffer stehen informativ im Ergebnis. Das
    Befund-Gate lief schon in der Stufe `register`."""
    e = _sbom_ergebnis(lauf)
    wt = lauf.worktree
    vex_pfad = wt / "docs" / "compliance" / "vex.openvex.json"
    gebaut = {a.name for a in e.bau.artefakte}
    scan.pruefe_sbom_vollstaendig(e.sboms, gebaut | manifest.AUSGELIEFERT
                                  | ({sbom.BILDER} if "import" in gebaut else set()))
    osv_stand = osv.stand_falls_noetig(e.sboms.values(), lauf.cache, scan.jetzt())
    erg = scan.scanne_alle(e.sboms, lauf.cache, vex_pfad, lauf.werkzeuge, uhr=scan.jetzt(),
                           osv=osv_stand)
    lauf.db_stand = erg.stand
    aussagen = vex.lade_vex(vex_pfad)
    locks = vex.lock_hashes(wt)
    stichtag = heute()
    geliefert = [t for t in erg.treffer if t.artefakt in manifest.AUSGELIEFERT]
    intern = [t for t in erg.treffer if t.artefakt not in manifest.AUSGELIEFERT]
    blockiert = vex.treffer_gate(geliefert, aussagen, stichtag, lock_hashes=locks)
    informativ = vex.treffer_gate(intern, aussagen, stichtag, lock_hashes=locks)
    ergebnis = {
        "heute": stichtag.isoformat(),
        "db": {"zeit": erg.stand.zeit.isoformat(), "feld": erg.stand.feld,
               "sha256": erg.stand.sha256, "verzoegert": erg.stand.verzoegert},
        "werkzeug": {n: {"image": w.image, "version": w.version}
                     for n, w in lauf.werkzeuge.items() if n == "trivy"},
        "vex": {"datei": vex_pfad.name, "aussagen": len(aussagen)},
        "osv": osv.stand_bericht(osv_stand) if osv_stand else None,
        "lockdateien": locks,
        "gate_artefakte": sorted(manifest.AUSGELIEFERT),
        "artefakte": erg.artefakte,
        "treffer": [{"id": t.id, "paket": t.paket, "version": t.version, "schwere": t.schwere,
                     "status_vex": t.status_vex, "artefakt": t.artefakt, "bom_ref": t.bom_ref,
                     "image_inhalt": vex.ist_image_inhalt(t)} for t in erg.treffer],
        "blockierend": blockiert,
        "informativ_blockierend": informativ,
        "vex_meldungen": vex.vex_meldungen(aussagen, stichtag, lock_hashes=locks,
                                           treffer=erg.treffer),
        "ergebnis": "blockiert" if blockiert else "grün",
    }
    _schreibe_json(e.gesamt.parent / "scan-ergebnis.json", ergebnis)
    if blockiert:
        raise NachweisFehler("scan-gate", f"{len(blockiert)} Treffer blockierend: "
                                          + "; ".join(blockiert))
    _bestanden(lauf, "scan")
    return {k: ergebnis[k] for k in ("heute", "db", "gate_artefakte", "blockierend",
                                     "informativ_blockierend", "vex_meldungen", "ergebnis")}


# --- Stufe 9: Negativkontrollen ------------------------------------------------------------------

def stufe_negativkontrollen(lauf: Lauf) -> dict[str, Any]:
    ordner = lauf.arbeitsordner / "negativkontrollen"
    k = nk.Kontext(wurzel=lauf.worktree, cache=lauf.cache, werkzeuge=lauf.werkzeuge,
                   arbeitsordner=ordner / "kontrollen", uhr=scan.jetzt())
    bericht = nk.ausfuehren(k, ausgabe=ordner / nk.DATEI)
    _bestanden(lauf, "negativkontrollen")
    return bericht


# --- Stufe 10: Lizenzen ---------------------------------------------------------------------------

def stufe_lizenzen(lauf: Lauf) -> dict[str, Any]:
    e = _sbom_ergebnis(lauf)
    wt = lauf.worktree
    regeln = lizenzen.lade_regeln(wt / "nachweise" / "lizenzen.toml")
    third_party = (wt / "docs" / "THIRD_PARTY.md").read_text(encoding="utf-8")
    geliefert = {n: _json(p) for n, p in sorted(e.sboms.items()) if n in manifest.AUSGELIEFERT}
    if set(geliefert) != manifest.AUSGELIEFERT:
        raise NachweisFehler("lizenzen", "SBOM eines ausgelieferten Artefakts fehlt")
    bericht = lizenzen.pruefe(geliefert, regeln, third_party)
    genannt = lizenzen.genannte_komponenten(third_party)
    bericht["informativ"] = {}
    for n, p in sorted(e.sboms.items()):
        if n not in manifest.AUSGELIEFERT:
            probleme = lizenzen.probleme(_json(p), regeln, genannt)
            bericht["informativ"][n] = {"verstoesse": len(probleme), "beispiele": probleme[:20]}
    _bestanden(lauf, "lizenzen")
    return bericht


# --- Stufe 12: Export -----------------------------------------------------------------------------

def letztes_exportmanifest(archiv: Path) -> export.Exportmanifest | None:
    """Exportmanifest der höchsten archivierten Version — nie eine frei gewählte Datei. Kein
    Archiv bzw. keine Version → `None` (erster Export). Fehlt es bei der höchsten Version →
    `export-manifest`."""
    releases = archiv / "releases"
    if not releases.is_dir():
        return None
    versionen = sorted((tuple(int(x) for x in p.name.split(".")), p) for p in releases.iterdir()
                       if p.is_dir() and _VERSION.fullmatch(p.name))
    if not versionen:
        return None
    letzte = versionen[-1][1]
    datei = letzte / EXPORTMANIFEST
    if not datei.is_file():
        raise NachweisFehler("export-manifest", f"archivierte Version {letzte.name} ohne "
                                                f"{EXPORTMANIFEST} — voriger Export unbekannt")
    return export.lade_manifest(datei)


def _stand(ordner: Path) -> set[tuple[str, str, str]]:
    """(Pfad, SHA-256, Git-Modus) aller Dateien unter `ordner` ohne `.git`."""
    ergebnis: set[tuple[str, str, str]] = set()
    for pfad in sorted(ordner.rglob("*")):
        relativ = pfad.relative_to(ordner)
        if relativ.parts[0] == ".git":
            continue
        if pfad.is_symlink():
            ergebnis.add((relativ.as_posix(), "symlink", "120000"))
        elif pfad.is_file():
            modus = "100755" if pfad.stat().st_mode & stat.S_IXUSR else "100644"
            ergebnis.add((relativ.as_posix(), hashlib.sha256(pfad.read_bytes()).hexdigest(),
                          modus))
    return ergebnis


def _commit_stand(repo: Path, rev: str) -> set[tuple[str, str, str]]:
    """(Pfad, SHA-256, Git-Modus) aller Dateien eines Commits — gelesen aus den Git-Objekten,
    nicht aus einem Arbeitsbaum."""
    roh = _befehl_roh(git_befehl("ls-tree", "-r", "-z", "--full-tree", rev, repo=repo),
                      "export-ziel")
    ergebnis: set[tuple[str, str, str]] = set()
    for eintrag in filter(None, roh.split(b"\0")):
        kopf, _, pfad = eintrag.partition(b"\t")
        modus, typ, objekt = kopf.decode().split()
        if typ != "blob":
            raise NachweisFehler("export-ziel", f"{rev[:12]}: {pfad!r} ist kein Blob ({typ})")
        inhalt = _befehl_roh(git_befehl("cat-file", "blob", objekt, repo=repo), "export-ziel")
        ergebnis.add((pfad.decode("utf-8"), hashlib.sha256(inhalt).hexdigest(), modus))
    return ergebnis


def _index_fuellen(repo: Path, quelle: Path, index: Path) -> None:
    """Alle Dateien unter `quelle` in einen eigenen Index des Export-Repos (Blobs ohne Filter,
    Modus aus dem x-Bit). Der Arbeitsbaum des Export-Repos wird nicht angefasst."""
    dateien = sorted(p for p in quelle.rglob("*") if p.is_file() or p.is_symlink())
    zeilen: list[bytes] = []
    for datei in dateien:
        relativ = datei.relative_to(quelle).as_posix()
        if datei.is_symlink() or "\n" in relativ:
            raise NachweisFehler("export-ziel", f"{relativ}: Symlink oder Zeilenumbruch im Pfad")
        blob = _befehl_roh(git_befehl("hash-object", "-w", "--no-filters",
                                      str(datei), repo=repo), "export-commit").decode().strip()
        modus = "100755" if datei.stat().st_mode & stat.S_IXUSR else "100644"
        zeilen.append(f"{modus} {blob}\t{relativ}".encode() + b"\0")
    _befehl_roh(git_befehl("update-index", "-z", "--add", "--index-info", repo=repo),
                "export-commit", umgebung={**_umgebung(), "GIT_INDEX_FILE": str(index)},
                eingabe=b"".join(zeilen))


def _vergleiche(ist: set[tuple[str, str, str]], soll: export.Exportmanifest, wo: str) -> None:
    erwartet = set(soll.eintraege)
    if ist != erwartet:
        zuviel = sorted(p for p, *_ in ist - erwartet)
        fehlt = sorted(p for p, *_ in erwartet - ist)
        raise NachweisFehler("export-ziel", f"{wo} weicht vom Exportmanifest ab — unerwartet "
                                            f"oder verändert: {', '.join(zuviel[:20]) or '-'}; "
                                            f"fehlt: {', '.join(fehlt[:20]) or '-'}")


def _identitaet() -> tuple[dict[str, str], list[str]]:
    name, mail = OEFFENTLICHE_IDENTITAET
    umgebung = {**_umgebung(), "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": mail,
                "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": mail}
    konfig = ["-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
              "-c", "tag.gpgsign=false", "-c", f"user.name={name}", "-c", f"user.email={mail}"]
    return umgebung, konfig


def _export_repo_bereit(repo: Path) -> bool:
    """Export-Repo steht sauber auf `main` (ggf. noch ohne Commit); Rückgabe: hat `main`."""
    if not (repo / ".git").is_dir():
        raise NachweisFehler("export-ziel", f"{repo}: kein Git-Repo")
    kopf = _git(repo, "symbolic-ref", "-q", "HEAD", regel="export-ziel")
    if kopf != "refs/heads/main":
        raise NachweisFehler("export-ziel", f"{repo} steht auf {kopf}, nicht auf main — "
                                            f"`git -C {repo} checkout main`")
    if _git(repo, "status", "--porcelain", "--untracked-files=all", regel="export-ziel"):
        raise NachweisFehler("export-ziel", f"{repo}: Arbeitsbaum nicht sauber")
    return _git_ok(repo, "rev-parse", "-q", "--verify", "refs/heads/main")


def export_committen(repo: Path, baum: Path, manifest_neu: export.Exportmanifest,
                     vorheriges: export.Exportmanifest | None, version: str,
                     diff_hash: str) -> str:
    """Geprüften Export als lokalen Commit auf `release-kandidat/<version>` im Export-Repo
    (angelegt, falls es fehlt); `main` und der Arbeitsbaum bleiben unberührt, bis
    `export_freigeben` nach der Archivierung vorspult. Vorher muss `main` dem vorigen
    Exportmanifest entsprechen; der Export wird unmittelbar vor und der Commit nach dem Anlegen
    gegen das neue Manifest geprüft (aus den Git-Objekten). Ein übrig gebliebener Kandidat
    derselben Version (abgebrochener, nie archivierter Lauf) wird ersetzt. Autor und Committer
    sind die neutrale Release-Identität, Hooks sind aus. Es wird nichts übertragen."""
    if not repo.exists():
        repo.mkdir(parents=True)
        _git(repo, "init", "-q", "-b", "main", regel="export-ziel")
    hat_main = _export_repo_bereit(repo)
    if hat_main and vorheriges is None:
        raise NachweisFehler("export-ziel", f"{repo} hat schon Commits auf main, aber im Archiv "
                                            "liegt kein voriges Exportmanifest")
    if not hat_main and vorheriges is not None:
        raise NachweisFehler("export-ziel", f"{repo}: main fehlt, obwohl das Archiv einen "
                                            "vorigen Export nennt")
    if hat_main and vorheriges is not None:
        _vergleiche(_commit_stand(repo, "refs/heads/main"), vorheriges, f"main von {repo}")
    _vergleiche(_stand(baum), manifest_neu, f"Export {baum}")

    umgebung, konfig = _identitaet()
    with tempfile.TemporaryDirectory(prefix="scanview-export-index-") as tmp:
        index = Path(tmp) / "index"
        _index_fuellen(repo, baum, index)
        _vergleiche(_stand(baum), manifest_neu, f"Export {baum} (vor dem Commit)")
        baum_id = _befehl_roh(git_befehl("write-tree", repo=repo), "export-commit",
                              umgebung={**umgebung, "GIT_INDEX_FILE": str(index)}
                              ).decode().strip()
    eltern = ["-p", "refs/heads/main"] if hat_main else []
    commit = _befehl_roh(git_befehl(*konfig, "commit-tree", baum_id, *eltern,
                                    "-m", f"scanview {version}\n\nExport-Diff {diff_hash}",
                                    repo=repo),
                         "export-commit", umgebung=umgebung).decode().strip()
    _vergleiche(_commit_stand(repo, commit), manifest_neu, f"Commit {commit[:12]}")
    kopf = _git(repo, "log", "-1", "--format=%an%x00%ae%x00%cn%x00%ce", commit,
                regel="export-commit")
    if kopf.split("\x00") != [*OEFFENTLICHE_IDENTITAET] * 2:
        raise NachweisFehler("export-commit", f"Commit-Identität weicht ab: {kopf!r}")
    _git(repo, "update-ref", f"refs/heads/{KANDIDAT}{version}", commit, regel="export-commit")
    return commit


def export_freigeben(repo: Path, version: str, commit: str, manifest_sha256: str, *,
                     sperrgrund: str | None, veroeffentlichen: bool = False) -> dict[str, str]:
    """Nach der Archivierung: `main` per Fast-Forward auf den Kandidaten vorspulen, annotierten
    Tag `v<version>` (Version + Manifest-SHA-256, neutrale Identität) setzen, Kandidat löschen.
    Nur lokal.

    `sperrgrund` (aus dem signierten Manifest, `veroeffentlichung.grund`) sperrt die
    Veröffentlichung, nicht diesen lokalen Schritt; der Tag trägt dann den Vermerk
    „Veröffentlichung gesperrt: …“. Wird eine Veröffentlichung verlangt (`veroeffentlichen`,
    heute ohne Befehl, für einen künftigen Veröffentlichungsweg), bricht die Freigabe bei
    gesetztem Sperrgrund ab, bevor sich etwas ändert."""
    if veroeffentlichen and sperrgrund:
        raise NachweisFehler("export-veroeffentlichung", f"Veröffentlichung gesperrt: "
                                                         f"{sperrgrund}")
    regel = "export-freigabe"
    kandidat = f"refs/heads/{KANDIDAT}{version}"
    tag = f"v{version}"
    if not _git_ok(repo, "rev-parse", "-q", "--verify", kandidat) or \
            _git(repo, "rev-parse", kandidat, regel=regel) != commit:
        raise NachweisFehler(regel, f"{kandidat} fehlt oder zeigt nicht auf {commit[:12]}")
    if _git_ok(repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}"):
        raise NachweisFehler(regel, f"Tag {tag} existiert im Export-Repo schon")
    hat_main = _export_repo_bereit(repo)
    if hat_main and not _git_ok(repo, "merge-base", "--is-ancestor", "refs/heads/main", commit):
        raise NachweisFehler(regel, f"main von {repo} ist kein Vorfahr von {commit[:12]}")
    umgebung, konfig = _identitaet()
    _befehl(git_befehl(*konfig, "merge", "-q", "--ff-only", commit, repo=repo), regel,
            umgebung=umgebung)
    if _git(repo, "rev-parse", "refs/heads/main", regel=regel) != commit:
        raise NachweisFehler(regel, "main steht nach dem Fast-Forward nicht auf dem Kandidaten")
    nachricht = f"scanview {version}\n\nManifest-SHA-256 {manifest_sha256}"
    if sperrgrund:
        nachricht += f"\nVeröffentlichung gesperrt: {sperrgrund}"
    _befehl(git_befehl(*konfig, "tag", "-a", tag, "-m", nachricht, commit, repo=repo), regel,
            umgebung=umgebung)
    pruefe_export_tag(repo, version, commit, manifest_sha256, sperrgrund=sperrgrund)
    _git(repo, "update-ref", "-d", kandidat, commit, regel=regel)
    return {"tag": tag, "commit": commit, "repo": str(repo)}


def pruefe_export_tag(repo: Path, version: str, commit: str, manifest_sha256: str, *,
                      sperrgrund: str | None) -> None:
    """Annotierter Tag `v<version>` im Export-Repo: zeigt auf `commit` (auch `main`), Tagger ist
    die neutrale Identität, die Nachricht nennt Version, Manifest-Hash und ggf. den Sperrgrund."""
    regel = "export-freigabe"
    tag = f"v{version}"
    if _git(repo, "cat-file", "-t", f"refs/tags/{tag}", regel=regel) != "tag":
        raise NachweisFehler(regel, f"{tag} im Export-Repo ist kein annotierter Tag")
    if _git(repo, "rev-parse", f"refs/tags/{tag}^{{commit}}", regel=regel) != commit:
        raise NachweisFehler(regel, f"{tag} zeigt nicht auf {commit[:12]}")
    if _git(repo, "rev-parse", "refs/heads/main", regel=regel) != commit:
        raise NachweisFehler(regel, f"main von {repo} steht nicht auf {commit[:12]}")
    roh = _git(repo, "cat-file", "tag", f"refs/tags/{tag}", regel=regel)
    tagger = next((z for z in roh.splitlines() if z.startswith("tagger ")), "")
    name, mail = OEFFENTLICHE_IDENTITAET
    if not tagger.startswith(f"tagger {name} <{mail}> "):
        raise NachweisFehler(regel, f"{tag}: Tagger nicht die neutrale Identität ({tagger!r})")
    erwartet = [f"scanview {version}", f"Manifest-SHA-256 {manifest_sha256}"]
    if sperrgrund:
        erwartet.append(f"Veröffentlichung gesperrt: {sperrgrund}")
    fehlend = [z for z in erwartet if z not in roh.splitlines()]
    if fehlend:
        raise NachweisFehler(regel, f"{tag}: Nachricht ohne {', '.join(fehlend)}")


def _oeffentliche_beigaben(lauf: Lauf, begriffe: list[str]) -> list[str]:
    """Was außer dem Quelltext öffentlich wird — die SBOMs der ausgelieferten Artefakte — durch
    Begriffs-, Privatpfad- und Geheimnisprüfung (inkl. der Pfade dieses Laufs)."""
    e = _sbom_ergebnis(lauf)
    ordner = lauf.arbeitsordner / "oeffentliche-beigaben"
    ordner.mkdir()
    namen = []
    for n in sorted(manifest.AUSGELIEFERT):
        shutil.copyfile(e.sboms[n], ordner / e.sboms[n].name)
        namen.append(e.sboms[n].name)
    treffer = export.pruefe_begriffe(ordner, begriffe)
    if treffer:
        raise NachweisFehler("export-begriffe", "öffentliche SBOMs: " + "; ".join(
            f"{t.pfad}:{t.zeile} {t.begriff}" for t in treffer[:20]))
    funde = [*export.pruefe_private_pfade(ordner),
             *export.pruefe_private_pfade(ordner, export.bauergebnis_muster(lauf.arbeitsordner))]
    if funde:
        raise NachweisFehler("export-privater-pfad", "öffentliche SBOMs: " + "; ".join(
            f"{t.pfad}:{t.zeile} „{t.begriff}“" for t in funde[:20]))
    export.pruefe_geheimnisse(ordner, lauf.werkzeuge,
                              lauf.worktree / export.GITLEAKS_KONFIG)
    return namen


def _entpacke_pruefkopie(tarball: Path, ziel: Path) -> None:
    """Tarball zur Prüfung entpacken: nur reguläre Dateien und Ordner mit relativen Pfaden ohne
    `..` (sonst `auslieferung-inhalt`)."""
    ziel.mkdir(parents=True)
    try:
        with tarfile.open(tarball, "r:gz") as tar:
            for m in tar:
                teile = PurePosixPath(m.name).parts
                if (m.name.startswith("/") or ".." in teile or not teile
                        or not (m.isreg() or m.isdir())):
                    raise NachweisFehler("auslieferung-inhalt", f"{tarball.name}: „{m.name}“ "
                                                                "unzulässig")
                if m.isdir():
                    (ziel / m.name).mkdir(parents=True, exist_ok=True)
                    continue
                quelle = tar.extractfile(m)
                if quelle is None:
                    raise NachweisFehler("auslieferung-inhalt", f"{m.name}: nicht lesbar")
                (ziel / m.name).parent.mkdir(parents=True, exist_ok=True)
                with (ziel / m.name).open("xb") as aus:
                    shutil.copyfileobj(quelle, aus)
    except (tarfile.TarError, OSError) as e:
        raise NachweisFehler("auslieferung-inhalt", f"{tarball.name}: nicht lesbar ({e})"
                             ) from None


def _ausgelieferte_inhalte(lauf: Lauf, begriffe: list[str]) -> list[str]:
    """Genau die ausgelieferten Tarballs (die signiert werden) entpackt durch dieselbe Begriffs-,
    Privatpfad- und Geheimnisprüfung wie der öffentliche Export: geprüft wird,
    was ausgeliefert wird — nicht ein Probe-Bau. Web mit dem Muster für Bauergebnisse
    (Heimat- und Arbeitsverzeichnis dieses Laufs), server/mcp mit dem Standardmuster."""
    e = _sbom_ergebnis(lauf)
    wurzel = lauf.arbeitsordner / "auslieferung-pruefung"
    geprueft = []
    for a in sorted(e.bau.artefakte, key=lambda x: x.name):
        if a.name not in manifest.AUSGELIEFERT:
            continue
        ordner = wurzel / a.name
        _entpacke_pruefkopie(a.pfad, ordner)
        treffer = export.pruefe_begriffe(ordner, begriffe)
        if a.name == "web":  # gleiche geprüfte Zufallstreffer wie im Bauergebnis des Exports
            treffer = export.ohne_bau_freigaben(
                treffer, ordner, export.lade_bau_freigaben_aus(lauf.worktree), "web/dist/",
                lauf.melde)
        if treffer:
            raise NachweisFehler("export-begriffe", f"ausgeliefertes Artefakt {a.name}: "
                                 + "; ".join(f"{t.pfad}:{t.zeile} {t.begriff}"
                                             for t in treffer[:20]))
        funde = (export.pruefe_private_pfade(ordner,
                                             export.bauergebnis_muster(lauf.arbeitsordner))
                 if a.name == "web" else export.pruefe_private_pfade(ordner))
        if funde:
            raise NachweisFehler("export-privater-pfad", f"ausgeliefertes Artefakt {a.name}: "
                                 + "; ".join(f"{t.pfad}:{t.zeile} „{t.begriff}“"
                                             for t in funde[:20]))
        export.pruefe_geheimnisse(ordner, lauf.werkzeuge,
                                  lauf.worktree / export.GITLEAKS_KONFIG)
        geprueft.append(a.pfad.name)
    if len(geprueft) != len(manifest.AUSGELIEFERT):
        raise NachweisFehler("auslieferung-inhalt", "nicht jedes ausgelieferte Artefakt gebaut: "
                                                    + ", ".join(geprueft))
    return geprueft


def _trocken_export_repo(lauf: Lauf) -> Path:
    """Wegwerf-Export-Repo für den Trockenlauf: ab dem zweiten Release muss
    `main` dem vorigen Export entsprechen — also ein Klon von `main` des echten Export-Repos
    (nur gelesen, ohne Tags); vor dem ersten Release ein leeres Repo."""
    ziel = lauf.arbeitsordner / "export-repo-trocken"
    echt = lauf.export_ziel
    if (echt / ".git").is_dir() and _git_ok(echt, "rev-parse", "-q", "--verify",
                                            "refs/heads/main"):
        _befehl(git_befehl("clone", "-q", "--no-hardlinks", "--single-branch", "--branch",
                            "main", "--no-tags", "--", str(echt), str(ziel)), "export-ziel")
    return ziel


def stufe_export(lauf: Lauf) -> dict[str, Any]:
    """Öffentlicher Export aus den Git-Blobs des Release-Commits inkl. bestätigtem
    Diff und Baubarkeit; öffentliche SBOMs geprüft; lokaler Commit im Export-Repo (Trockenlauf:
    Wegwerf-Repo im Arbeitsordner)."""
    begriffe = export.lade_begriffe()
    vorheriges = letztes_exportmanifest(lauf.archiv)
    ziel = lauf.arbeitsordner / "export"
    erg = export.exportiere(lauf.repo, lauf.voll, ziel, begriffe=begriffe,
                            werkzeuge=lauf.werkzeuge, vorheriges=vorheriges,
                            bestaetigt=lauf.bestaetigt,
                            arbeitsordner=lauf.arbeitsordner / "export-arbeit", melde=lauf.melde,
                            postfach_vermerken=True)
    lauf.sperrgrund = erg.manifest.sperrgrund
    lauf.netzschritte += erg.netzschritte
    lauf.exportmanifest = lauf.arbeitsordner / EXPORTMANIFEST
    with lauf.exportmanifest.open("x", encoding="utf-8") as f:
        f.write(erg.manifest.als_json())
    beigaben = _oeffentliche_beigaben(lauf, begriffe)
    ausgeliefert = _ausgelieferte_inhalte(lauf, begriffe)
    repo = _trocken_export_repo(lauf) if lauf.trocken else lauf.export_ziel
    lauf.exportcommit = export_committen(repo, ziel, erg.manifest, vorheriges, lauf.version,
                                         erg.diff_hash)
    _bestanden(lauf, "export")
    return {"commit": lauf.exportcommit, "repo": str(repo), "dateien": len(erg.manifest.eintraege),
            "diff_hash": erg.diff_hash, "vorheriges": vorheriges is not None,
            "oeffentliche_beigaben": beigaben, "ausgelieferte_artefakte_geprueft": ausgeliefert,
            "identitaet": " ".join(OEFFENTLICHE_IDENTITAET),
            "veroeffentlichbar": lauf.sperrgrund is None, "sperrgrund": lauf.sperrgrund}


# --- Stufe 11: Matrix -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Quelle:
    """Woher ein Nachweis kommt: genau eines von `pfad` (Datei im Release-Commit), `pruefung`
    (Ergebnis einer Stufe) oder `vault` (Datei unter `$SCANVIEW_VAULT`)."""

    pfad: str | None = None
    pruefung: str | None = None
    vault: str | None = None


def _relativ(wert: str, schluessel: str, pfad: Path) -> str:
    teile = PurePosixPath(wert).parts
    if not wert or PurePosixPath(wert).is_absolute() or ".." in teile:
        raise NachweisFehler("nachweisquellen", f"{pfad}: „{schluessel}“ braucht einen "
                                                f"relativen Pfad ohne „..“, nicht „{wert}“")
    return wert


def lade_nachweisquellen(pfad: Path) -> dict[str, Quelle]:
    """`nachweise/nachweisquellen.toml`: Nachweisschlüssel → Pfad (Zeichenkette, relativ zum
    Repo) oder `{ pruefung = "<stufe>" }` oder `{ vault = "<pfad unter $SCANVIEW_VAULT>" }`.
    `doku:*` sind immer Pfade, `vault:*` immer Vault-Dateien."""
    try:
        daten = tomllib.loads(pfad.read_text(encoding="utf-8"))
    except OSError as e:
        raise NachweisFehler("nachweisquellen", f"{pfad}: nicht lesbar ({e.strerror})") from None
    except tomllib.TOMLDecodeError as e:
        raise NachweisFehler("nachweisquellen", f"{pfad}: kein gültiges TOML ({e})") from None
    quellen: dict[str, Quelle] = {}
    for schluessel, wert in daten.items():
        if isinstance(wert, str):
            q = Quelle(pfad=_relativ(wert, schluessel, pfad))
        elif isinstance(wert, dict) and len(wert) == 1 and isinstance(
                next(iter(wert.values())), str) and next(iter(wert)) in ("pruefung", "vault"):
            art, ziel = next(iter(wert.items()))
            q = (Quelle(pruefung=ziel) if art == "pruefung"
                 else Quelle(vault=_relativ(ziel, schluessel, pfad)))
        else:
            raise NachweisFehler("nachweisquellen", f"{pfad}: „{schluessel}“ muss ein Pfad oder "
                                                    "{ pruefung = … } bzw. { vault = … } sein")
        if (schluessel.startswith("vault:")) != (q.vault is not None) or \
                (schluessel.startswith("doku:") and q.pfad is None):
            raise NachweisFehler("nachweisquellen", f"{pfad}: „{schluessel}“ hat die falsche Art "
                                                    "(doku:* → Pfad, vault:* → vault)")
        quellen[schluessel] = q
    return quellen


def _dokument(pfad: Path, anzeige: str) -> str:
    if not pfad.is_file():
        raise NachweisFehler("matrix", f"{anzeige} fehlt")
    inhalt = pfad.read_bytes()
    if not inhalt.strip():
        raise NachweisFehler("matrix", f"{anzeige} ist leer")
    return "sha256:" + hashlib.sha256(inhalt).hexdigest()


def _nachweis(lauf: Lauf, teil: str, quellen: Mapping[str, Quelle]) -> str:
    q = quellen.get(teil)
    if q is None:
        raise NachweisFehler("matrix", "kein Eintrag in nachweisquellen.toml")
    if q.pruefung is not None:
        if lauf.pruefungen.get(q.pruefung) != "bestanden":
            raise NachweisFehler("matrix", f"Prüfung {q.pruefung} nicht bestanden")
        return f"Prüfung {q.pruefung}: bestanden"
    if q.pfad is not None:
        return f"{q.pfad} {_dokument(lauf.worktree / q.pfad, q.pfad)}"
    assert q.vault is not None
    wurzel = os.environ.get(VAULT_VARIABLE)
    if not wurzel:
        raise NachweisFehler("matrix", f"{VAULT_VARIABLE} nicht gesetzt")
    return f"vault:{q.vault} {_dokument(Path(wurzel) / q.vault, 'vault:' + q.vault)}"


def stufe_matrix(lauf: Lauf) -> dict[str, Any]:
    """Jede Release-Voraussetzung der Kontrollmatrix braucht je Nachweisschlüssel eine bestandene
    Prüfung oder ein vorhandenes, nicht leeres Dokument (`nachweisquellen.toml`). `manifest` und
    `archiv` folgen nach Signatur bzw. Archivierung. Fehlt etwas → Abbruch mit Liste."""
    wt = lauf.worktree
    quellen = lade_nachweisquellen(wt / NACHWEISQUELLEN)
    kontrollen = (lauf.kontrollen if lauf.kontrollen is not None
                  else register.lade_matrix(wt / register.MATRIX))
    erfuellt: dict[str, dict[str, str]] = {}
    spaeter: dict[str, list[str]] = {}
    fehlend: list[str] = []
    for k in kontrollen:
        if not k.release_voraussetzung:
            continue
        for teil in k.schluessel_teile:
            if teil in NACH_ARCHIV:
                spaeter.setdefault(k.id, []).append(teil)
                continue
            try:
                erfuellt.setdefault(k.id, {})[teil] = _nachweis(lauf, teil, quellen)
            except NachweisFehler as e:
                fehlend.append(f"{k.id}: {teil} ({e.text})")
    if fehlend:
        raise NachweisFehler("matrix", f"{len(fehlend)} Nachweis(e) fehlen: "
                                       + "; ".join(fehlend))
    return {"erfuellt": erfuellt, "nach_archiv": spaeter}


# --- Stufe 13 bis 16: Manifest, Archiv, Tag, Abschluss --------------------------------------

def umgebung_erfassen(worktree: Path) -> dict[str, str]:
    """Toolchain-Versionen und Architektur des Baus."""
    python = worktree / "server" / ".venv" / "bin" / "python"
    if not python.exists():
        raise NachweisFehler("release-umgebung", f"{python} fehlt (uv sync --locked in server)")
    return {
        "node": _befehl(["node", "--version"], "release-umgebung").strip(),
        "npm": _befehl(["npm", "--version"], "release-umgebung").strip(),
        "python": _befehl([str(python), "--version"], "release-umgebung").strip(),
        "uv": _befehl(["uv", "--version"], "release-umgebung").strip(),
        "docker": _befehl(["docker", "version", "--format", "{{.Server.Version}}"],
                          "release-umgebung").strip(),
        "architektur": platform.machine(),
    }


def _allowed_signers(lauf: Lauf) -> Path:
    return lauf.worktree / "nachweise" / "allowed_signers"


def stufe_manifest(lauf: Lauf) -> dict[str, Any]:
    """Manifest bauen (alle Stufenberichte bis `matrix`, Scan-Ergebnis, Werkzeugbefunde,
    Exportmanifest), signieren, Signatur über dieselben Bytes prüfen und die Artefakt-Hashes
    gegen das signierte Manifest — damit ist der Nachweisschlüssel `manifest` erfüllt."""
    e = _sbom_ergebnis(lauf)
    if lauf.db_stand is None or lauf.exportmanifest is None:
        raise NachweisFehler("release-reihenfolge", "Scan- oder Export-Stufe fehlt")
    berichte = {f"stufe-{n}": lauf.berichte / f"{n}.json"
                for n in STUFEN[:STUFEN.index("manifest")]}
    berichte.update({"scan-ergebnis": e.gesamt.parent / "scan-ergebnis.json",
                     "werkzeugbefunde": e.werkzeugbefunde, "exportmanifest": lauf.exportmanifest,
                     "npm-signaturen": lauf.berichte / "npm-signaturen.json",
                     "sast-semgrep": lauf.berichte / sast.BERICHT_SEMGREP,
                     "sast-bandit": lauf.berichte / sast.BERICHT_BANDIT,
                     **{f"fuzzing-{i}": lauf.berichte / fuzzing.BERICHTE[i]
                        for i in ("admin", "betrachter")},
                     "egress-statisch": lauf.berichte / egress.BERICHT_STATISCH,
                     "egress-browser": lauf.berichte / egress.BERICHT_BROWSER})
    m = manifest.baue_manifest(
        lauf.version, lauf.voll, lauf.exportcommit, e.bau.artefakte,
        [*(e.sboms[n] for n in sorted(e.sboms)), e.gesamt], berichte, lauf.werkzeuge,
        lauf.db_stand, dict(lauf.pruefungen), ausgeliefert=manifest.AUSGELIEFERT,
        umgebung=umgebung_erfassen(lauf.worktree), trockenlauf=lauf.trocken,
        veroeffentlichbar=lauf.sperrgrund is None, sperrgrund=lauf.sperrgrund,
        netzschritte=lauf.netzschritte)
    pfad = manifest.schreibe_manifest(m, lauf.arbeitsordner / manifest.MANIFEST_DATEI)
    manifest.signiere(pfad, lauf.schluessel)
    geprueft = manifest.lade_signiertes_manifest(pfad, _allowed_signers(lauf))
    manifest.pruefe_artefakte(geprueft, lauf.ausgabe / "artefakte")
    lauf.manifest_pfad = pfad
    return {"manifest": pfad.name, "sha256": hashlib.sha256(pfad.read_bytes()).hexdigest(),
            "signatur": "gültig", "trockenlauf": lauf.trocken, "nachweis": "manifest"}


def stufe_archiv(lauf: Lauf) -> dict[str, Any]:
    """Nachweise ins Archiv (nur anhängen, lokal committet; Signatur der Kopie geprüft), danach
    Nachweisschlüssel `archiv`: die archivierte Fassung ist byte-gleich und committet."""
    e = _sbom_ergebnis(lauf)
    if lauf.manifest_pfad is None or lauf.exportmanifest is None:
        raise NachweisFehler("release-reihenfolge", "Manifest-Stufe fehlt")
    nachweise = lauf.worktree / "nachweise"
    quellen = [lauf.manifest_pfad,
               lauf.manifest_pfad.with_name(lauf.manifest_pfad.name + manifest.SIGNATUR_ENDUNG),
               nachweise / "allowed_signers", nachweise / "signierschluessel.pub",
               lauf.exportmanifest, lauf.ausgabe / "artefakte", e.gesamt.parent, lauf.berichte]
    ziel = archiviere(lauf.version, quellen, lauf.archiv, allowed_signers=_allowed_signers(lauf))
    if (ziel / manifest.MANIFEST_DATEI).read_bytes() != lauf.manifest_pfad.read_bytes():
        raise NachweisFehler("archiv", "archiviertes Manifest weicht ab")
    commit = _git(lauf.archiv, "log", "--format=%H", "-1", "--",
                  f"releases/{lauf.version}", regel="archiv")
    if not commit:
        raise NachweisFehler("archiv", f"releases/{lauf.version} nicht committet")
    return {"ziel": str(ziel), "commit": commit, "nachweis": "archiv"}


def wiederherstellung(lauf: Lauf, sha: str) -> str:
    """Anleitung, wenn die Kette nach dem Archivieren abbricht: das Archiv ist geschrieben, die
    Version kann nicht erneut laufen — die fehlenden Schritte werden von Hand nachgeholt."""
    v, tag = lauf.version, f"v{lauf.version}"
    ablage = lauf.archiv / "releases" / v
    return (
        f"Wiederherstellung (Archiv releases/{v} ist geschrieben, die Version läuft nicht erneut): "
        f"(1) Manifest prüfen: scanview-nachweise manifest pruefen {ablage}/manifest.json "
        f"--artefakte {ablage}/artefakte; "
        f"(2) Export freigeben, falls main von {lauf.export_ziel} noch nicht auf "
        f"{KANDIDAT}{v} steht: git -C {lauf.export_ziel} merge --ff-only {KANDIDAT}{v}, dann "
        f"annotierter Tag {tag} mit Nachricht „scanview {v}“ + „Manifest-SHA-256 {sha}“"
        + (f" + „Veröffentlichung gesperrt: {lauf.sperrgrund}“" if lauf.sperrgrund else "")
        + " "
        f"(Autor {OEFFENTLICHE_IDENTITAET[0]} <{OEFFENTLICHE_IDENTITAET[1]}>); "
        f"(3) ein ungültiger Rest-Tag im privaten Repo: git -C {lauf.repo} tag -d {tag}; "
        f"(4) Tag setzen: git -C {lauf.repo} -c gpg.format=ssh -c user.signingkey="
        f"{lauf.schluessel} tag -s -m \"scanview {v}\" -m \"Manifest-SHA-256 {sha}\" {tag} "
        f"{lauf.voll}; (5) prüfen: git -C {lauf.repo} -c gpg.ssh.allowedSignersFile=<Datei mit "
        f"„{manifest.KENNUNG} namespaces=\"git\" <nachweise/signierschluessel.pub>“> tag -v {tag}; "
        f"(6) Abschluss: scanview-nachweise abschluss --version {v} --repo {lauf.repo} "
        f"--archiv {lauf.archiv} --schluessel {lauf.schluessel} --export-ziel {lauf.export_ziel}"
    )


def _bericht_bestanden(lauf: Lauf, name: str) -> None:
    try:
        ergebnis = _json(lauf.berichte / f"{name}.json").get("ergebnis")
    except NachweisFehler:
        ergebnis = None
    if ergebnis != "bestanden":
        raise NachweisFehler("release-reihenfolge", f"Stufe {name} nicht bestanden — kein Tag")


def _tag_pruefen(lauf: Lauf, tag: str, pub: Path, sha: str) -> None:
    tag_pruefen(lauf.repo, lauf.voll, tag, pub, sha)


def tag_pruefen(repo: Path, voll: str, tag: str, pub: Path, sha: str) -> None:
    """Signatur (`tag -v` gegen den eingecheckten öffentlichen Schlüssel, Git-Namensraum), Ziel
    und Manifest-Hash in der Nachricht."""
    typ, wert = pub.read_text(encoding="utf-8").split()[:2]
    with tempfile.TemporaryDirectory(prefix="scanview-tag-") as tmp:
        signers = Path(tmp) / "allowed_signers"
        signers.write_text(f'{manifest.KENNUNG} namespaces="git" {typ} {wert}\n',
                           encoding="utf-8")
        _befehl(git_befehl("-c", f"gpg.ssh.allowedSignersFile={signers}",
                            "tag", "-v", tag, repo=repo), "release-tag")
    ziel = _git(repo, "rev-list", "-n", "1", tag, regel="release-tag")
    if ziel != voll:
        raise NachweisFehler("release-tag", f"{tag} zeigt auf {ziel}, nicht auf {voll}")
    if f"Manifest-SHA-256 {sha}" not in _git(repo, "cat-file", "-p", tag,
                                              regel="release-tag"):
        raise NachweisFehler("release-tag", f"{tag} nennt nicht den Manifest-Hash {sha}")


def stufe_tag(lauf: Lauf) -> dict[str, Any]:
    """Nach bestandenem Manifest und Archiv: Export freigeben (`main` vorspulen, annotierter Tag
    mit Manifest-Hash im Export-Repo), dann signierter Tag `v<version>` im privaten Repo auf dem
    Quellcommit. Der Release-Schlüssel gilt nur für diesen einen Befehl (`-c`); geprüft wird
    gegen den eingecheckten `nachweise/signierschluessel.pub`. Ein schon vorhandener Tag wird
    nur akzeptiert, wenn er diese Prüfung besteht. Jeder Fehler nennt die Wiederherstellung."""
    for name in ("manifest", "archiv"):
        _bericht_bestanden(lauf, name)
    if lauf.manifest_pfad is None or lauf.exportcommit is None:
        raise NachweisFehler("release-reihenfolge", "Manifest- oder Export-Stufe fehlt")
    tag = f"v{lauf.version}"
    sha = hashlib.sha256(lauf.manifest_pfad.read_bytes()).hexdigest()
    try:
        veroeffentlichung = manifest.lade_manifest(lauf.manifest_pfad).get("veroeffentlichung")
        if not isinstance(veroeffentlichung, dict):
            raise NachweisFehler("release-reihenfolge", "Manifest ohne Feld veroeffentlichung")
        freigabe = export_freigeben(lauf.export_ziel, lauf.version, lauf.exportcommit, sha,
                                    sperrgrund=veroeffentlichung["grund"])
        pub = lauf.worktree / "nachweise" / "signierschluessel.pub"
        if not pub.is_file():
            raise NachweisFehler("release-tag", f"{pub}: öffentlicher Schlüssel fehlt")
        if _git_ok(lauf.repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}"):
            try:
                _tag_pruefen(lauf, tag, pub, sha)
            except NachweisFehler as e:
                raise NachweisFehler("release-tag", f"Tag {tag} existiert schon und besteht die "
                                                    f"Prüfung nicht: {e.text}") from None
        else:
            _befehl(git_befehl("-c", "gpg.format=ssh",
                                "-c", f"user.signingkey={lauf.schluessel}", "tag", "-s", "-m",
                                f"scanview {lauf.version}\n\nManifest-SHA-256 {sha}", tag,
                                lauf.voll, repo=lauf.repo),
                    "release-tag")
            _tag_pruefen(lauf, tag, pub, sha)
    except NachweisFehler as e:
        raise NachweisFehler(e.regel, f"{e.text} — {wiederherstellung(lauf, sha)}") from None
    return {"tag": tag, "commit": lauf.voll, "manifest_sha256": sha, "export": freigabe}


def stufe_abschluss(lauf: Lauf) -> dict[str, Any]:
    """Nach bestandenem Archiv und Tag: signierter Abschlussnachweis im Archiv
    (`abschluss.abschliessen`). Erst damit ist das Release deploybar."""
    for name in ("manifest", "archiv", "tag"):
        _bericht_bestanden(lauf, name)
    try:
        ziel = abschluss.abschliessen(lauf.archiv, lauf.version, lauf.schluessel,
                                      _allowed_signers(lauf))
    except NachweisFehler as e:
        raise NachweisFehler(e.regel, f"{e.text} — Archiv und Tags stehen; Abschluss nachholen: "
                                      f"scanview-nachweise abschluss --version {lauf.version} "
                                      f"--archiv {lauf.archiv} --schluessel {lauf.schluessel} "
                                      f"--export-ziel {lauf.export_ziel}") from None
    commit = _git(lauf.archiv, "log", "--format=%H", "-1", "--",
                  f"{abschluss.ORDNER}/{lauf.version}", regel="abschluss")
    return {"ziel": str(ziel), "commit": commit, "nachweis": "abschluss"}


def abschluss_nachholen(repo: Path, archiv: Path, schluessel: Path, export_ziel: Path,
                        version: str) -> Path:
    """Wiederherstellung nach einem Abbruch hinter dem Archivieren (Schritt 6): erst wenn der
    signierte Tag im privaten Repo und der Tag im Export-Repo zum archivierten Manifest passen,
    wird der Abschlussnachweis geschrieben — wie die Stufe `abschluss` nach `tag`."""
    signers = repo / "nachweise" / "allowed_signers"
    pub = repo / "nachweise" / "signierschluessel.pub"
    commit = abschluss._archiv_commit(archiv, version)
    roh, m = abschluss._signiertes_manifest(archiv, commit, version, signers)
    sha = hashlib.sha256(roh).hexdigest()
    tag_pruefen(repo, m["quellcommit"], f"v{version}", pub, sha)
    pruefe_export_tag(export_ziel, version, m["exportcommit"], sha,
                      sperrgrund=m["veroeffentlichung"]["grund"])
    return abschluss.abschliessen(archiv, version, schluessel, signers)


STANDARD_STUFEN: dict[str, Stufe] = {
    "einfrieren": stufe_einfrieren, "register": stufe_register,
    "qualitaetstor": stufe_qualitaetstor, "sast": stufe_sast, "sbom": stufe_sbom,
    "fuzzing": stufe_fuzzing, "egress": stufe_egress, "scan": stufe_scan,
    "negativkontrollen": stufe_negativkontrollen, "lizenzen": stufe_lizenzen,
    "export": stufe_export, "matrix": stufe_matrix, "manifest": stufe_manifest,
    "archiv": stufe_archiv, "tag": stufe_tag, "abschluss": stufe_abschluss,
}


# --- Orchestrierung ------------------------------------------------------------------------------

def release(repo: Path, commit: str, version: str, archiv: Path, schluessel: Path,
            export_ziel: Path, bestaetigt: str | None, *, trocken: bool = False,
            arbeitsordner: Path | None = None, cache: Path | None = None,
            stufen: Mapping[str, Stufe] | None = None,
            melde: Callable[[str], None] = print) -> Path:
    """Die Kette in fester Reihenfolge (`STUFEN`); Rückgabe: Pfad des signierten Manifests.
    Jede Stufe schreibt `<arbeitsordner>/berichte/<stufe>.json`; die erste, die abbricht,
    beendet den Lauf (`NachweisFehler`), spätere laufen nicht. Der Worktree wird am Ende mit
    einfachem `git worktree remove` entfernt."""
    if arbeitsordner is None:
        arbeitsordner = Path(tempfile.mkdtemp(prefix=f"scanview-release-{version}-"))
    if arbeitsordner.exists() and any(arbeitsordner.iterdir()):
        raise NachweisFehler("release-arbeitsordner", f"{arbeitsordner}: nicht leer — jeder Lauf "
                                                      "braucht einen frischen Arbeitsordner")
    arbeitsordner.mkdir(parents=True, exist_ok=True)
    lauf = Lauf(repo=repo, commit=commit, version=version, archiv=archiv, schluessel=schluessel,
                export_ziel=export_ziel, bestaetigt=bestaetigt, trocken=trocken,
                arbeitsordner=arbeitsordner, cache=cache or scan.STANDARD_CACHE, melde=melde)
    tabelle = dict(STANDARD_STUFEN if stufen is None else stufen)
    try:
        for name in STUFEN:
            bericht_pfad = lauf.berichte / f"{name}.json"
            if trocken and name in NUR_ECHT:
                _schreibe_json(bericht_pfad, {"stufe": name,
                                              "ergebnis": "übersprungen (Trockenlauf)"})
                melde(f"[{name}] übersprungen (Trockenlauf)")
                continue
            melde(f"[{name}] …")
            try:
                bericht = tabelle[name](lauf)
            except NachweisFehler as e:
                _schreibe_json(bericht_pfad, {"stufe": name, "ergebnis": "abgebrochen",
                                              "regel": e.regel, "text": e.text})
                melde(f"[{name}] ABGEBROCHEN: {e}")
                raise
            except Exception as e:  # jeder Fehler ist ein Abbruch mit Bericht
                fehler = NachweisFehler("release-stufe", f"{name}: {type(e).__name__}: {e}")
                _schreibe_json(bericht_pfad, {"stufe": name, "ergebnis": "abgebrochen",
                                              "regel": fehler.regel, "text": fehler.text})
                melde(f"[{name}] ABGEBROCHEN: {fehler}")
                raise fehler from e
            _schreibe_json(bericht_pfad, {"stufe": name, "ergebnis": "bestanden", **bericht})
            melde(f"[{name}] bestanden")
    finally:
        rest = worktree_entfernen(lauf)
        if rest:
            melde(f"WARNUNG: {rest}")
    if lauf.manifest_pfad is None:
        raise NachweisFehler("release-stufe", "kein Manifest entstanden")
    if lauf.sperrgrund:
        melde(f"Veröffentlichung gesperrt: {lauf.sperrgrund}")
    return lauf.manifest_pfad
