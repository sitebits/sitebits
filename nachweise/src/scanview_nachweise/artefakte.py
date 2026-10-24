"""Release-Artefakte bauen und ihren Lieferumfang festhalten.

`baue_artefakte` erzeugt aus einem eingefrorenen Worktree (frischer `git worktree add --detach`
des Release-Commits) vier Tarballs:

1. `scanview-web-<v>.tar.gz` — nur `web/dist/` (Bau: `npm ci --ignore-scripts`, danach nur die in
   `package.json` unter `allowScripts` ausdrücklich freigegebenen Install-Skripte per
   `npm rebuild <paket>`, dann `npm run build`).
2. `scanview-server-<v>.tar.gz` — `server/` plus `deploy/platform/` (ohne `test/`).
3. `scanview-mcp-<v>.tar.gz` — `mcp-server/`.
4. `scanview-import-<v>.tar.gz` — `import/`.

Für die ausgelieferten Artefakte 1 bis 4 gilt zusätzlich die öffentliche Positivliste
(`veroeffentlichung/positivliste.txt`): ausgeliefert wird nur, was auch öffentlich sein darf
(z. B. nicht `deploy/platform/README.md` mit Betriebsdetails). Für 2 bis 4 zählen nur **von Git
versionierte** Dateien (`git ls-files` im Worktree); so kann kein
liegengebliebener Arbeitsbaum-Müll (`.venv`, alte Wheels unter `dist/`, lokale Notizen) in ein
Artefakt rutschen. Zusätzlich wird ausgefiltert, was nie ausgeliefert wird, selbst wenn es
versehentlich versioniert ist: `tests/`, `deploy/platform/test/`, Caches (`__pycache__`, `.venv`,
`.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `*.pyc`), `import/scripts/`
(Entwicklerwerkzeuge), Bau-Ausgaben (`dist/`, `build/`,
`node_modules/`). Pfade im Tar entsprechen den Pfaden im Repo (z. B. `server/uv.lock`,
`web/dist/index.html`), damit die SBOM sie eindeutig zuordnen kann.

Deterministisch: Einträge (Dateien und ihre Elternordner) nach Pfad sortiert, `mtime` = Commit-Zeit
von HEAD, uid/gid 0 ohne Namen, Modus 0755 für Ordner und ausführbare Dateien (laut Git-Index bzw.
x-Bit im Bau-Ergebnis), sonst 0644; GNU-Tarformat (keine PAX-Kopfzeilen mit Zeitstempeln, lange
Pfade über GNU-LongLink ohne Zeitangabe), gzip-Kopf mit `mtime=0` und ohne Dateinamen.

Fail-closed (`NachweisFehler`): ungültige Version, schon vorhandenes Zielartefakt (nie
überschreiben), versionierte Dateien im Worktree verändert (vor oder nach dem Bau), `web/dist`
schon vor dem Bau vorhanden (nur frisches Bau-Ergebnis wird ausgeliefert), Symlinks oder
Submodule im Lieferumfang, fehlendes/leeres `web/dist`, fehlende `pyproject.toml`/`uv.lock`.

Netz: nur die Bau-Stufe darf Abhängigkeiten gegen Lockfiles laden, alle anderen Stufen laufen
netzlos. Jeder solche Schritt wird als `Netzschritt` (Befehl, Arbeitsordner relativ zum Worktree,
UTC-Zeit) zurückgegeben — als Pflichtbestandteil von `Bauergebnis`, nicht als optionales Protokoll,
damit das Release-Manifest ihn nicht versehentlich verlieren kann. Absolute Pfade stehen bewusst
nicht darin: das Manifest wird mit dem Release veröffentlicht.

Pflichtprüfung der Bau-Stufe (CRA Art. 13 Abs. 5 — Sorgfalt bei Fremdkomponenten):
`npm_signaturen` führt nach dem Webbau `npm audit signatures --json` im Bau-Baum aus und wertet
die Ausgabe gegen die installierten Registry-Pakete des Lockfiles aus (`werte_npm_signaturen_aus`,
`installierte_registry_pakete`). Eine ungültige Signatur bricht immer ab, eine fehlende nur ohne
begründete Ausnahme (`docs/compliance/npm-signaturen-ausnahmen.toml`, geladen mit
`lade_npm_ausnahmen`). Das Ergebnis steht in `Bauergebnis.npm_signaturen` (nur beim echten
Webbau), der Registry-/Sigstore-Abruf ist ein weiterer `Netzschritt` der Bau-Stufe."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .fehler import NachweisFehler
from .positivliste import POSITIVLISTE, Positivliste, lies_positivliste
from .werkzeuge import ausfuehren, bau_umgebung, git_befehl

_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_~-]*")

#: Pfadbestandteile, die nie ausgeliefert werden (auch wenn versehentlich versioniert).
_AUSGESCHLOSSEN = frozenset({
    "tests", "__pycache__", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "dist", "build", "node_modules",
})
# `import/scripts/` sind Mess-/Abnahmewerkzeuge der Entwicklung (eigene Tests unter
# `import/tests/`), weder von `pyproject.toml` noch von `src/` referenziert.
_AUSGESCHLOSSEN_PRAEFIX = ("deploy/platform/test/", "import/scripts/")
_AUSGESCHLOSSEN_ENDUNG = (".pyc",)


@dataclass(frozen=True)
class _Komponente:
    name: str
    wurzeln: tuple[str, ...]
    pflicht: tuple[str, ...]
    #: Ausgeliefert: nur Dateien, die auch die öffentliche Positivliste aufnimmt.
    oeffentlich: bool


_PYTHON_KOMPONENTEN = (
    _Komponente("server", ("server/", "deploy/platform/"),
                ("server/pyproject.toml", "server/uv.lock"), True),
    _Komponente("mcp", ("mcp-server/",), ("mcp-server/pyproject.toml", "mcp-server/uv.lock"),
                True),
    _Komponente("import", ("import/",), ("import/pyproject.toml", "import/uv.lock"), True),
)
_WEB_DIST = "web/dist"


@dataclass(frozen=True)
class Datei:
    """Eine Datei im Artefakt: Pfad im Tar, SHA-512 (hex) und Größe in Byte."""

    pfad_im_artefakt: str
    sha512: str
    groesse: int


@dataclass(frozen=True)
class Artefakt:
    """Ein gebautes Release-Artefakt. `lieferumfang` ist der tatsächliche Tar-Inhalt (nur
    Dateien, in Tar-Reihenfolge) — Grundlage für die SBOM-Abdeckung."""

    name: str
    pfad: Path
    sha256: str
    sha512: str
    lieferumfang: list[Datei]


@dataclass(frozen=True)
class Netzschritt:
    """Ein Befehl der Bau-Stufe mit Netzzugriff. `cwd` relativ zum Worktree, `zeit` UTC
    ISO-8601 (Start des Schritts)."""

    befehl: list[str]
    cwd: str
    zeit: str


@dataclass(frozen=True)
class Bauergebnis:
    """Rückgabe von `baue_artefakte`: die vier Artefakte (Reihenfolge web, server, mcp, import)
    und alle Netzschritte des Baus fürs Manifest."""

    artefakte: list[Artefakt]
    netzschritte: list[Netzschritt]
    #: Ergebnis von `npm_signaturen` (Pflichtprüfung der Bau-Stufe) — nur gesetzt beim echten
    #: Web-Bau (`bauen` nicht injiziert); ein Test-Bau hat keinen echten `web`-Baum.
    npm_signaturen: dict[str, Any] | None = None


#: Bau-Schritt: bekommt die Worktree-Wurzel, erzeugt `web/dist` und liefert die Netzschritte.
BauFunktion = Callable[[Path], list[Netzschritt]]


def _jetzt() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def erlaubte_install_skripte(paket: Path, regel: str) -> list[str]:
    """Pakete, deren Install-Skripte `package.json` unter `allowScripts` mit `true` ausdrücklich
    freigibt (sortiert). Auch vom öffentlichen Export (Baubarkeit) genutzt, damit beide Bauwege
    dieselbe Freigabe lesen."""
    try:
        roh = json.loads(paket.read_text(encoding="utf-8"))
    except OSError as e:
        raise NachweisFehler(regel, f"{paket} nicht lesbar: {e.strerror}") from None
    except json.JSONDecodeError as e:
        raise NachweisFehler(regel, f"{paket} ist kein gültiges JSON: {e}") from None
    erlaubt = roh.get("allowScripts", {}) if isinstance(roh, dict) else None
    if not isinstance(erlaubt, dict) or \
            not all(isinstance(wert, bool) for wert in erlaubt.values()):
        raise NachweisFehler(regel, f"{paket}: „allowScripts“ muss ein Objekt Paket → true/false "
                                    "sein")
    return sorted(n for n, wert in erlaubt.items() if wert)


def web_bauen(worktree: Path) -> list[Netzschritt]:
    """Standard-Webbau in `<worktree>/web`: `npm ci --ignore-scripts`, danach je in
    `package.json` → `allowScripts` mit `true` freigegebenem Paket `npm rebuild <paket>
    --ignore-scripts=false` (sortiert), zuletzt `npm run build`. `npm ci` und die Rebuilds können
    Netz nutzen und werden als `Netzschritt` zurückgegeben.

    Alle Schritte laufen mit der expliziten Umgebung `werkzeuge.bau_umgebung` (dieselbe wie die
    Baubarkeitsprobe des Exports) in einem Wegwerf-Ordner: nichts aus der Aufruferumgebung
    (`VITE_…`, `NODE_OPTIONS`, `GIT_…`, npm-Konfiguration) kann in das ausgelieferte Bundle
    gelangen, das die Datenschutzprüfung nicht gesehen hätte."""
    regel = "artefakt-bau"
    web = worktree / "web"
    befehle = [["npm", "ci", "--ignore-scripts"]]
    befehle += [["npm", "rebuild", name, "--ignore-scripts=false"]
                for name in erlaubte_install_skripte(web / "package.json", regel)]
    schritte: list[Netzschritt] = []
    with tempfile.TemporaryDirectory(prefix="scanview-web-bau-") as tmp:
        umgebung = bau_umgebung(Path(tmp))
        for befehl in befehle:
            schritte.append(Netzschritt(befehl=befehl, cwd="web", zeit=_jetzt()))
            ausfuehren(befehl, regel, cwd=web, umgebung=umgebung)
        ausfuehren(["npm", "run", "build"], regel, cwd=web, umgebung=umgebung)
    return schritte


_NPM_REGISTRY = "https://registry.npmjs.org/"
_NPM_JSON_SCHLUESSEL = frozenset({"invalid", "missing"})


def _npm_ausnahme_gueltig(schluessel: str) -> bool:
    """„paket@version“, am **letzten** „@“ getrennt: Scoped-Pakete (`@scope/name@version`, z. B.
    `@rollup/rollup-linux-x64-gnu@4.24.0`) tragen ein zweites, führendes „@“ im Namensteil — ein
    Schnitt am ersten „@“ (oder ein Regex ohne „@“ im Namensteil) lehnt sie fälschlich ab."""
    if not schluessel or any(z.isspace() for z in schluessel):
        return False
    paket, trenner, version = schluessel.rpartition("@")
    return bool(trenner) and bool(paket) and bool(version)


def lade_npm_ausnahmen(pfad: Path) -> dict[str, str]:
    """`docs/compliance/npm-signaturen-ausnahmen.toml`: `[ausnahme]` bildet „paket@version“ auf
    eine Begründung (≥ 20 Zeichen) ab — nur für **fehlende** Registry-Signaturen, nie für
    ungültige (die brechen immer ab)."""
    regel = "npm-signaturen-ausnahmen"
    try:
        daten = tomllib.loads(pfad.read_text(encoding="utf-8"))
    except OSError as e:
        raise NachweisFehler(regel, f"{pfad} nicht lesbar: {e.strerror}") from None
    except tomllib.TOMLDecodeError as e:
        raise NachweisFehler(regel, f"{pfad}: ungültiges TOML: {e}") from None
    unbekannt = sorted(set(daten) - {"ausnahme"})
    if unbekannt:
        raise NachweisFehler(regel, f"{pfad}: unbekannte Schlüssel {', '.join(unbekannt)}")
    ausnahme = daten.get("ausnahme", {})
    if not isinstance(ausnahme, dict):
        raise NachweisFehler(regel, f"{pfad}: „ausnahme“ muss eine Tabelle „paket@version“ → "
                                    "Begründung sein")
    ergebnis: dict[str, str] = {}
    for schluessel, begruendung in ausnahme.items():
        if not isinstance(schluessel, str) or not _npm_ausnahme_gueltig(schluessel):
            raise NachweisFehler(regel, f"{pfad}: Schlüssel „{schluessel}“ muss „paket@version“ "
                                        "sein")
        if not isinstance(begruendung, str) or len(begruendung.strip()) < 20:
            raise NachweisFehler(regel, f"{pfad}: Ausnahme „{schluessel}“: Begründung fehlt oder "
                                        "ist kürzer als 20 Zeichen")
        ergebnis[schluessel] = begruendung.strip()
    return ergebnis


def installierte_registry_pakete(web: Path, lock_text: str) -> frozenset[str]:
    """Lockfile-Schlüssel (`node_modules/…`) der Registry-Pakete (`resolved` auf
    `https://registry.npmjs.org/`, keine Workspace-Links), die unter `web` tatsächlich installiert
    sind — Grundlage der Abdeckung von `npm audit signatures` (dessen `--json`-Ausgabe keinen
    Gesamtzähler trägt, Probe 2026-09-28 gegen npm 12.0.2/`verify-signatures.js`). Optionale
    (plattformspezifische) Pakete dürfen fehlen (z. B. `@esbuild/<plattform>`); jedes andere
    Registry-Paket im Lockfile muss installiert sein — sonst ein unvollständiger `npm ci`-Lauf,
    fail-closed."""
    regel = "npm-signaturen"
    try:
        lock = json.loads(lock_text)
    except json.JSONDecodeError as e:
        raise NachweisFehler(regel, f"package-lock.json ungültig: {e}") from None
    pakete = lock.get("packages") if isinstance(lock, dict) else None
    if not isinstance(pakete, dict):
        raise NachweisFehler(regel, "package-lock.json ohne packages")
    installiert: set[str] = set()
    fehlend: list[str] = []
    for pfad, eintrag in pakete.items():
        if not pfad or not isinstance(eintrag, dict) or eintrag.get("link"):
            continue
        resolved = eintrag.get("resolved")
        if not isinstance(resolved, str) or not resolved.startswith(_NPM_REGISTRY):
            continue
        if (web / pfad).is_dir():
            installiert.add(pfad)
        elif not eintrag.get("optional"):
            fehlend.append(pfad)
    if fehlend:
        raise NachweisFehler(regel, f"{len(fehlend)} Registry-Paket(e) aus dem Lockfile nicht "
                                    "installiert: " + ", ".join(sorted(fehlend)[:10]))
    if not installiert:
        raise NachweisFehler(regel, "keine installierten Registry-Pakete zu prüfen")
    return frozenset(installiert)


def _npm_eintrag(e: object, regel: str, art: str) -> dict[str, Any]:
    if not isinstance(e, dict) or not isinstance(e.get("name"), str) or not e["name"] \
            or not isinstance(e.get("version"), str) or not e["version"] \
            or not isinstance(e.get("location"), str) or not e["location"]:
        raise NachweisFehler(regel, f"npm audit signatures: {art}-Eintrag ohne "
                                    f"name/version/location: {str(e)[:300]}")
    return e


def werte_npm_signaturen_aus(ausgabe: str, *, installiert: frozenset[str],
                             ausnahmen: dict[str, str]) -> dict[str, Any]:
    """Reine Auswertung von `npm audit signatures --json` (Probe 2026-09-28: die Ausgabe trägt
    nur `invalid` und `missing`, keinen Gesamtzähler — die Abdeckung liefert
    `installierte_registry_pakete`). `installiert` sind die Lockfile-Schlüssel, gegen die jeder
    gemeldete Fund geprüft wird: ein Fund zu einem fremden Paket ist ein Fehler (Baum passt nicht
    zum Lockfile), kein Vertrauensbeweis für die übrigen. Eine ungültige Signatur bricht immer ab;
    eine fehlende ohne begründete Ausnahme (`paket@version` → Begründung ≥ 20 Zeichen in
    `ausnahmen`) ebenso, mit Ausnahme steht sie im Ergebnis."""
    regel = "npm-signaturen"
    try:
        daten = json.loads(ausgabe)
    except json.JSONDecodeError as e:
        raise NachweisFehler(regel, f"keine gültige JSON-Ausgabe: {e}") from None
    if not isinstance(daten, dict) or set(daten) != _NPM_JSON_SCHLUESSEL \
            or not isinstance(daten["invalid"], list) or not isinstance(daten["missing"], list):
        raise NachweisFehler(regel, "Ausgabe ohne genau „invalid“ und „missing“ (Listen): "
                                    + repr(ausgabe.strip()[:500]))
    invalid = [_npm_eintrag(e, regel, "invalid") for e in daten["invalid"]]
    missing = [_npm_eintrag(e, regel, "missing") for e in daten["missing"]]
    for eintrag in (*invalid, *missing):
        if eintrag["location"] not in installiert:
            raise NachweisFehler(regel, f"{eintrag['name']}@{eintrag['version']}: gemeldet, aber "
                                        "nicht unter den installierten Registry-Paketen "
                                        f"({eintrag['location']})")
    if invalid:
        raise NachweisFehler(regel, "ungültige Registry-Signatur: "
                                    + ", ".join(f"{e['name']}@{e['version']}" for e in invalid))
    ausgenommen: dict[str, str] = {}
    offen: list[str] = []
    for eintrag in missing:
        schluessel = f"{eintrag['name']}@{eintrag['version']}"
        begruendung = ausnahmen.get(schluessel)
        if begruendung is None:
            offen.append(schluessel)
        else:
            ausgenommen[schluessel] = begruendung
    if offen:
        raise NachweisFehler(regel, "fehlende Registry-Signatur ohne begründete Ausnahme: "
                                    + ", ".join(sorted(offen)))
    return {"abdeckung": len(installiert), "geprueft": sorted(installiert), "ungueltig": 0,
            "fehlend": len(missing), "ausnahmen": dict(sorted(ausgenommen.items()))}


def _npm_audit_signaturen_json(web: Path, umgebung: dict[str, str]) -> str:
    """Eigener, gezielt mockbarer Aufruf (nicht `werkzeuge.ausfuehren`, das bei Exit ≠ 0 abbricht):
    ein Fund lässt npm mit Exit 1 enden — erwartet, die Ausgabe wird trotzdem immer geparst."""
    try:
        ergebnis = subprocess.run(["npm", "audit", "signatures", "--json"], cwd=web,
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", check=False, env=umgebung)
    except OSError as e:
        raise NachweisFehler("npm-signaturen", f"npm nicht ausführbar: {e.strerror}") from None
    return ergebnis.stdout


def npm_signaturen(web: Path, lock: Path, ausnahmen: dict[str, str]) -> dict[str, Any]:
    """Pflichtprüfung der Bau-Stufe (CRA Art. 13 Abs. 5): `npm audit signatures
    --json` im bereits gebauten `web`-Baum (nach `npm ci --ignore-scripts`), gegen die
    installierten Registry-Pakete des Lockfiles ausgewertet. Netzschritt (Registry- und
    Sigstore-TUF-Abfrage) protokolliert der Aufrufer wie jeden anderen Netzschritt der Bau-Stufe."""
    regel = "npm-signaturen"
    try:
        lock_text = lock.read_text(encoding="utf-8")
    except OSError as e:
        raise NachweisFehler(regel, f"{lock} nicht lesbar: {e.strerror}") from None
    installiert = installierte_registry_pakete(web, lock_text)
    with tempfile.TemporaryDirectory(prefix="scanview-npm-signaturen-") as tmp:
        ausgabe = _npm_audit_signaturen_json(web, bau_umgebung(Path(tmp)))
    return werte_npm_signaturen_aus(ausgabe, installiert=installiert, ausnahmen=ausnahmen)


def _pruefe_sauber(worktree: Path, wann: str) -> None:
    """Versionierte Dateien dürfen im Worktree nicht verändert sein, sonst entspräche der
    gelesene Inhalt nicht dem Commit."""
    status = ausfuehren(git_befehl("status", "--porcelain", "--untracked-files=no"),
                        "artefakt-worktree", cwd=worktree).stdout.strip()
    if status:
        raise NachweisFehler("artefakt-worktree", f"versionierte Dateien {wann} verändert: "
                                                  + "; ".join(status.splitlines()[:10]))


def _ausgeschlossen(pfad: str) -> bool:
    return (any(teil in _AUSGESCHLOSSEN for teil in PurePosixPath(pfad).parts)
            or pfad.startswith(_AUSGESCHLOSSEN_PRAEFIX)
            or pfad.endswith(_AUSGESCHLOSSEN_ENDUNG))


def _versioniert(worktree: Path) -> dict[str, str]:
    """Pfad → Git-Modus (z. B. `100644`) aller versionierten Einträge."""
    ausgabe = ausfuehren(git_befehl("ls-files", "-s", "-z"), "artefakt-inhalt",
                         cwd=worktree).stdout
    ergebnis: dict[str, str] = {}
    for zeile in ausgabe.split("\0"):
        if zeile:
            kopf, pfad = zeile.split("\t", 1)
            ergebnis[pfad] = kopf.split(" ", 1)[0]
    return ergebnis


# Eintrag im Tar: Pfad → (Quelldatei, ausführbar).
_Eintraege = dict[str, tuple[Path, bool]]


def _positivliste(worktree: Path) -> Positivliste:
    pfad = worktree / POSITIVLISTE
    try:
        text = pfad.read_text(encoding="utf-8")
    except OSError as e:
        raise NachweisFehler("artefakt-inhalt", f"{POSITIVLISTE} nicht lesbar ({e.strerror}) — "
                                                "ohne sie keine öffentliche Dateiauswahl"
                             ) from None
    return Positivliste(lies_positivliste(text))


def _python_eintraege(worktree: Path, k: _Komponente, index: dict[str, str],
                      oeffentlich: Positivliste) -> _Eintraege:
    eintraege: _Eintraege = {}
    for pfad, modus in index.items():
        if not pfad.startswith(k.wurzeln) or _ausgeschlossen(pfad):
            continue
        if k.oeffentlich and not oeffentlich.nimmt_auf(pfad):
            continue  # z. B. deploy/platform/README.md (Betriebsdetails, nur privat)
        if modus not in ("100644", "100755"):
            raise NachweisFehler("artefakt-inhalt", f"{pfad}: Modus {modus} (Symlink/Submodul) "
                                                    "im Lieferumfang nicht erlaubt")
        eintraege[pfad] = (worktree / pfad, modus == "100755")
    fehlend = [p for p in k.pflicht if p not in eintraege]
    if fehlend:
        raise NachweisFehler("artefakt-inhalt", f"{k.name}: Pflichtdatei(en) fehlen: "
                                                + ", ".join(fehlend))
    return eintraege


def _web_eintraege(worktree: Path) -> _Eintraege:
    dist = worktree / _WEB_DIST
    if not dist.is_dir() or dist.is_symlink():
        raise NachweisFehler("artefakt-inhalt", f"{_WEB_DIST}: fehlt nach dem Bau")
    eintraege: _Eintraege = {}
    for ordner, unterordner, dateien in os.walk(dist):
        for name in [*unterordner, *dateien]:
            pfad = Path(ordner) / name
            relativ = pfad.relative_to(worktree).as_posix()
            if pfad.is_symlink():
                raise NachweisFehler("artefakt-inhalt", f"{relativ}: Symlink im Bau-Ergebnis "
                                                        "nicht erlaubt")
            if name in dateien:
                if not pfad.is_file():
                    raise NachweisFehler("artefakt-inhalt", f"{relativ}: keine reguläre Datei")
                eintraege[relativ] = (pfad, bool(pfad.stat().st_mode & 0o111))
    if not eintraege:
        raise NachweisFehler("artefakt-inhalt", f"{_WEB_DIST}: leer nach dem Bau")
    return eintraege


def _tarinfo(name: str, mtime: int, *, ordner: bool, ausfuehrbar: bool, groesse: int = 0
             ) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE if ordner else tarfile.REGTYPE
    info.mode = 0o755 if ordner or ausfuehrbar else 0o644
    info.size = groesse
    info.mtime = mtime
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def _schreibe_tar(ziel: Path, eintraege: _Eintraege, mtime: int) -> None:
    ordner = {str(p) for pfad in eintraege for p in PurePosixPath(pfad).parents
              if str(p) != "."}
    try:
        with open(ziel, "xb") as roh, \
                gzip.GzipFile(filename="", mode="wb", fileobj=roh, mtime=0,
                              compresslevel=9) as gz, \
                tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT,
                             encoding="utf-8", errors="strict") as tar:
            for name in sorted(ordner | set(eintraege)):
                if name in ordner:
                    tar.addfile(_tarinfo(name, mtime, ordner=True, ausfuehrbar=True))
                    continue
                quelle, ausfuehrbar = eintraege[name]
                daten = quelle.read_bytes()
                tar.addfile(_tarinfo(name, mtime, ordner=False, ausfuehrbar=ausfuehrbar,
                                     groesse=len(daten)), io.BytesIO(daten))
    except FileExistsError:
        raise NachweisFehler("artefakt-ziel", f"{ziel}: existiert schon — wird nicht "
                                              "überschrieben") from None
    except BaseException:
        ziel.unlink(missing_ok=True)
        raise


def _lieferumfang(pfad: Path) -> list[Datei]:
    """Liest das fertige Tar zurück: der Lieferumfang ist, was tatsächlich darin steht."""
    dateien: list[Datei] = []
    with tarfile.open(pfad, "r:gz") as tar:
        for m in tar:
            if m.isfile():
                f = tar.extractfile(m)
                if f is None:  # pragma: no cover — reguläre Datei hat immer Inhalt
                    raise NachweisFehler("artefakt-inhalt", f"{m.name}: nicht lesbar")
                inhalt = f.read()
                dateien.append(Datei(m.name, hashlib.sha512(inhalt).hexdigest(), len(inhalt)))
    return dateien


def baue_artefakte(
    worktree: Path, version: str, ziel: Path, *, bauen: BauFunktion | None = None,
    npm_ausnahmen: dict[str, str] | None = None,
) -> Bauergebnis:
    """Baut die vier Release-Artefakte aus `worktree` nach `ziel` (wird angelegt). `version`
    muss mit Buchstabe/Ziffer beginnen und darf nur `[0-9A-Za-z.+_~-]` enthalten (die Prüfung
    des Release-Formats übernimmt die Release-Orchestrierung). `bauen` ersetzt den Webbau für
    Tests, Standard ist `web_bauen` (spät gebunden). `npm_ausnahmen` geht an `npm_signaturen`:
    die Prüfung läuft **nur** beim echten Webbau (`bauen` nicht injiziert) — ein
    Test-Bau hat keinen echten `web`-Baum, den npm prüfen könnte, `Bauergebnis.npm_signaturen`
    bleibt dann `None`."""
    if _VERSION.fullmatch(version) is None:
        raise NachweisFehler("artefakt-version", f"ungültige Version {version!r}")
    namen = ["web", *(k.name for k in _PYTHON_KOMPONENTEN)]
    pfade = {name: ziel / f"scanview-{name}-{version}.tar.gz" for name in namen}
    vorhanden = [str(p) for p in pfade.values() if p.exists() or p.is_symlink()]
    if vorhanden:
        raise NachweisFehler("artefakt-ziel", "existiert schon — wird nicht überschrieben: "
                                              + ", ".join(vorhanden))
    _pruefe_sauber(worktree, "vor dem Bau")
    dist = worktree / _WEB_DIST
    if dist.exists() or dist.is_symlink():
        raise NachweisFehler("artefakt-worktree", f"{_WEB_DIST}: existiert schon vor dem Bau — "
                                                  "nur ein frischer Worktree ist zulässig")
    bau_schritt = bauen if bauen is not None else web_bauen
    netzschritte = list(bau_schritt(worktree))
    npm_ergebnis: dict[str, Any] | None = None
    if bauen is None:
        zeit = _jetzt()
        npm_ergebnis = npm_signaturen(worktree / "web", worktree / "web" / "package-lock.json",
                                      npm_ausnahmen or {})
        netzschritte.append(Netzschritt(befehl=["npm", "audit", "signatures", "--json"],
                                        cwd="web", zeit=zeit))
    _pruefe_sauber(worktree, "durch den Bau")

    mtime = int(ausfuehren(git_befehl("show", "-s", "--format=%ct", "HEAD"), "artefakt-inhalt",
                           cwd=worktree).stdout.strip())
    index = _versioniert(worktree)
    oeffentlich = _positivliste(worktree)
    inhalte: dict[str, _Eintraege] = {"web": _web_eintraege(worktree)}
    for k in _PYTHON_KOMPONENTEN:
        inhalte[k.name] = _python_eintraege(worktree, k, index, oeffentlich)

    ziel.mkdir(parents=True, exist_ok=True)
    artefakte: list[Artefakt] = []
    geschrieben: list[Path] = []
    try:
        for name in namen:
            pfad = pfade[name]
            _schreibe_tar(pfad, inhalte[name], mtime)
            geschrieben.append(pfad)
            daten = pfad.read_bytes()
            artefakte.append(Artefakt(
                name=name, pfad=pfad,
                sha256=hashlib.sha256(daten).hexdigest(),
                sha512=hashlib.sha512(daten).hexdigest(),
                lieferumfang=_lieferumfang(pfad),
            ))
    except BaseException:
        # Kein halber Satz Artefakte: was dieser Lauf schon geschrieben hat, wird entfernt.
        for pfad in geschrieben:
            pfad.unlink(missing_ok=True)
        raise
    return Bauergebnis(artefakte=artefakte, netzschritte=netzschritte,
                       npm_signaturen=npm_ergebnis)
