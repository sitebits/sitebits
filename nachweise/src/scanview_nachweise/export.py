"""Öffentlicher Export (siehe ADR 0012 „Öffentlicher Export über Positivliste“).

Das private Repo bleibt kanonisch; öffentlich wird je Release ein Abzug, der **nur aus den
Git-Blobs eines Commits** entsteht (nie aus dem Arbeitsbaum):

1. `git_blobs` listet den Baum des Commits (`git ls-tree -r -z --full-tree`, sicher für
   Leerzeichen, Kommas, Umlaute).
2. `kandidaten` wählt per Positivliste (`veroeffentlichung/positivliste.txt`, aus dem Commit
   gelesen) aus. Symlinks, Submodule und bekannte Arbeitsnotizen (`HANDOFF*`, `INBOX*`,
   `*.ledger*`, `docs/superpowers/**`, `.superpowers/**`) im Kandidatensatz brechen ab — auch
   wenn ein breites Muster sie erfassen würde: Arbeitsnotizen werden nie öffentlich.
3. `schreibe_export` legt die Dateien in ein leeres Ziel (Inhalt per `git cat-file blob`) und
   liefert das Exportmanifest (Pfad, SHA-256).
4. Prüfungen des Pakets: Binärdateien/Bilder/große Dateien nur mit Hash-Freigabe
   (`veroeffentlichung/freigaben.txt`), Begriffsprüfung gegen die Liste aus dem Vault
   (`SCANVIEW_BEGRIFFE`, fehlt sie → Abbruch), gitleaks im gehärteten Container mit der
   gitleaks-Konfiguration des Commits (Standardregeln + `sv_`-Token), Meldeweg: `SECURITY.md`
   ohne Marker „Postfach offen“ — eigenständig Abbruch (`pruefe_postfach`), in der
   Release-Kette nur Vermerk `veroeffentlichbar = false` im Exportmanifest.
5. Release-Diff gegen das vorige Exportmanifest; weiter geht es nur mit
   `bestaetigt = diff_hash(diff)` — so bestätigt Andre genau diesen Diff.
6. Baubarkeit in einem Wegwerf-Abzug des Exports (das Ziel bleibt frei von `node_modules`,
   `.venv`, `dist`): web `npm ci --ignore-scripts` (+ freigegebene Install-Skripte),
   `typecheck`, `lint`, `test`, `build`; server, mcp-server, nachweise und import
   `uv sync --locked` + `pytest`. Netz nur
   für `npm ci`/`npm rebuild`/`uv sync`, als `Netzschritt`
   zurückgegeben. Das Web-Bauergebnis (`dist/`) durchläuft danach dieselbe Begriffs- und
   Geheimnisprüfung.

Der Export veröffentlicht nichts: Er schreibt nur lokal ins Ziel. Das Übertragen in ein
öffentliches Repo ist bewusst kein Teil dieses Moduls (Test `test_export_uebertraegt_nie`).

Alle externen Befehle laufen über `_lauf` (ohne Shell, Ausgabe als Bytes), damit Tests sie
vollständig mitschneiden und ersetzen können."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from .artefakte import Netzschritt, erlaubte_install_skripte
from .fehler import NachweisFehler
from .positivliste import POSITIVLISTE, Positivliste, lies_positivliste
from .positivliste import glob_regex as glob_regex  # Teil der Export-Schnittstelle
from .werkzeuge import Werkzeug, bau_umgebung, docker_befehl, gefilterte_umgebung, git_befehl

FREIGABEN = "veroeffentlichung/freigaben.txt"
#: Geprüfte Zufallstreffer der Begriffsprüfung im Bauergebnis (gebündelter Fremdcode); optional.
BAU_FREIGABEN = "veroeffentlichung/bauergebnis-freigaben.txt"
GITLEAKS_KONFIG = ".gitleaks.toml"
BEGRIFFE_VARIABLE = "SCANVIEW_BEGRIFFE"

#: Dateien ab dieser Größe brauchen eine Freigabe, auch wenn sie Text sind.
GRENZE_BYTES = 1024 * 1024
BILDENDUNGEN = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".tif", ".tiff", ".ico",
    ".svg", ".heic", ".psd",
})

_SHA256 = re.compile(r"[0-9a-f]{64}")
_STDERR_AUSZUG = 2000
# Buchstaben/Ziffern inkl. Umlaute (ohne `_`): beginnt ein Begriff mit einem solchen Zeichen, muss
# er am Wortanfang stehen.
_WORTZEICHEN = re.compile(r"[^\W_]")
_GIT_MODI = ("100644", "100755")


# --- Befehlsläufer -----------------------------------------------------------------------------

def _lauf(befehl: list[str], regel: str, *, cwd: Path | None = None,
          umgebung: dict[str, str] | None = None) -> bytes:
    """Befehl ohne Shell ausführen und stdout als Bytes liefern (Git-Blobs bleiben binär
    unverändert). Exit ≠ 0 oder fehlendes Programm → `NachweisFehler(regel)` mit dem Ende von
    stderr. `umgebung` ersetzt die Umgebung vollständig (None = `gefilterte_umgebung()`)."""
    try:
        ergebnis = subprocess.run(befehl, capture_output=True, check=False, cwd=cwd,
                                  env=gefilterte_umgebung() if umgebung is None else umgebung)
    except OSError as e:
        raise NachweisFehler(regel, f"{befehl[0]} nicht ausführbar: {e.strerror}") from None
    if ergebnis.returncode != 0:
        auszug = ergebnis.stderr.decode("utf-8", "replace").strip()[-_STDERR_AUSZUG:]
        if not auszug:
            auszug = ergebnis.stdout.decode("utf-8", "replace").strip()[-_STDERR_AUSZUG:]
        raise NachweisFehler(regel, f"{befehl[0]} endete mit Exit {ergebnis.returncode}: "
                                    f"{auszug or '(keine Ausgabe)'}")
    return ergebnis.stdout


# --- Git-Blobs ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Blob:
    """Ein Eintrag des Commit-Baums (`git ls-tree`): Modus, Typ (`blob`/`commit`), Hash, Pfad."""

    modus: str
    typ: str
    hash: str
    pfad: str


def commit_aufloesen(repo: Path, commit: str) -> str:
    """Voller Hash des Commits; unbekannt oder kein Commit → `export-commit`."""
    if commit.startswith("-"):
        raise NachweisFehler("export-commit", f"„{commit}“ ist kein Commit")
    roh = _lauf(git_befehl("rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}"),
                "export-commit", cwd=repo)
    return roh.decode("ascii").strip()


def git_blobs(repo: Path, commit: str) -> list[Blob]:
    """Alle Einträge des Commit-Baums, rekursiv, mit Pfaden relativ zur Repo-Wurzel."""
    voll = commit_aufloesen(repo, commit)
    roh = _lauf(git_befehl("ls-tree", "-r", "-z", "--full-tree", voll), "export-commit", cwd=repo)
    blobs: list[Blob] = []
    for eintrag in roh.split(b"\0"):
        if not eintrag:
            continue
        kopf, pfad = eintrag.split(b"\t", 1)
        modus, typ, hash_ = kopf.decode("ascii").split(" ")
        blobs.append(Blob(modus=modus, typ=typ, hash=hash_, pfad=pfad.decode("utf-8")))
    return blobs


def _blob_inhalt(repo: Path, hash_: str) -> bytes:
    return _lauf(git_befehl("cat-file", "blob", hash_), "export-inhalt", cwd=repo)


def datei_im_commit(repo: Path, commit: str, pfad: str) -> bytes:
    """Inhalt einer Datei im Commit (nicht im Arbeitsbaum); fehlt sie → `export-quelle`."""
    for blob in git_blobs(repo, commit):
        if blob.pfad == pfad and blob.typ == "blob" and blob.modus in _GIT_MODI:
            return _blob_inhalt(repo, blob.hash)
    raise NachweisFehler("export-quelle", f"{pfad} fehlt im Commit {commit}")


# --- Positivliste ------------------------------------------------------------------------------

def _ist_arbeitsnotiz(pfad: str) -> bool:
    """Groß/klein egal; ein Pfadteil „superpowers“ (auch mit Punkt davor) irgendwo im Pfad."""
    teile = [t.lower() for t in PurePosixPath(pfad).parts]
    name = teile[-1]
    return (name.startswith(("handoff", "inbox"))
            or ".ledger" in name
            or any(t.lstrip(".") == "superpowers" for t in teile[:-1]))


def kandidaten(blobs: Iterable[Blob], positivliste: list[str]) -> list[Blob]:
    """Einträge, die die Positivliste aufnimmt (sortiert nach Pfad). Wie bei `.gitignore`
    entscheidet das letzte passende Muster; `!muster` schließt aus. Im Ergebnis sind Symlinks
    (`export-symlink`), Submodule (`export-submodul`) und Arbeitsnotizen
    (`export-arbeitsnotiz`) verboten."""
    liste = Positivliste(positivliste)
    gewaehlt = [blob for blob in blobs if liste.nimmt_auf(blob.pfad)]
    gewaehlt.sort(key=lambda b: b.pfad)

    notizen = [b.pfad for b in gewaehlt if _ist_arbeitsnotiz(b.pfad)]
    if notizen:
        raise NachweisFehler("export-arbeitsnotiz", "Arbeitsnotizen werden nie öffentlich, die "
                                                    "Positivliste erfasst: " + ", ".join(notizen))
    for blob in gewaehlt:
        if blob.typ == "commit":
            raise NachweisFehler("export-submodul", f"{blob.pfad}: Submodul im Export")
        if blob.modus == "120000":
            raise NachweisFehler("export-symlink", f"{blob.pfad}: Symlink im Export")
        if blob.typ != "blob" or blob.modus not in _GIT_MODI:
            raise NachweisFehler("export-inhalt", f"{blob.pfad}: unerwarteter Eintrag "
                                                  f"{blob.modus} {blob.typ}")
    return gewaehlt


# --- Export schreiben, Manifest ----------------------------------------------------------------

@dataclass(frozen=True)
class Exportmanifest:
    """Aufgelöster Inhalt eines Exports: (Pfad, SHA-256, Git-Modus) je Datei, sortiert nach Pfad.
    Der Modus (`100644`/`100755`) gehört dazu: eine Datei, die ausführbar wird, ist eine
    Änderung."""

    eintraege: list[tuple[str, str, str]]
    #: false, wenn der Export lokal geprüft, aber nicht veröffentlicht werden darf
    #: (Meldepostfach offen); `sperrgrund` nennt dann den Grund.
    veroeffentlichbar: bool = True
    sperrgrund: str | None = None

    def hashes(self) -> dict[str, str]:
        return {p: s for p, s, _ in self.eintraege}

    def als_json(self) -> str:
        daten = {"dateien": [{"pfad": p, "sha256": s, "modus": m} for p, s, m in self.eintraege],
                 "veroeffentlichbar": self.veroeffentlichbar, "sperrgrund": self.sperrgrund}
        return json.dumps(daten, indent=2, ensure_ascii=False) + "\n"


def lade_manifest(pfad: Path) -> Exportmanifest:
    regel = "export-manifest"
    try:
        roh = json.loads(pfad.read_text(encoding="utf-8"))
    except OSError as e:
        raise NachweisFehler(regel, f"{pfad} nicht lesbar: {e.strerror}") from None
    except json.JSONDecodeError as e:
        raise NachweisFehler(regel, f"{pfad} ist kein gültiges JSON: {e}") from None
    dateien = roh.get("dateien") if isinstance(roh, dict) else None
    if not isinstance(dateien, list):
        raise NachweisFehler(regel, f"{pfad}: Liste „dateien“ fehlt")
    eintraege: list[tuple[str, str, str]] = []
    for d in dateien:
        if not isinstance(d, dict) or not isinstance(d.get("pfad"), str) \
                or not isinstance(d.get("sha256"), str) or not _SHA256.fullmatch(d["sha256"]) \
                or d.get("modus") not in _GIT_MODI:
            raise NachweisFehler(regel, f"{pfad}: ungültiger Eintrag {d!r}")
        eintraege.append((d["pfad"], d["sha256"], d["modus"]))
    if [e[0] for e in eintraege] != sorted({e[0] for e in eintraege}):
        raise NachweisFehler(regel, f"{pfad}: Pfade nicht eindeutig oder nicht sortiert")
    # Ältere Manifeste ohne dieses Feld: damals gab es die Veröffentlichungssperre noch nicht.
    veroeffentlichbar = roh.get("veroeffentlichbar", True)
    sperrgrund = roh.get("sperrgrund")
    if not isinstance(veroeffentlichbar, bool) or (
            veroeffentlichbar and sperrgrund is not None) or (
            not veroeffentlichbar and not (isinstance(sperrgrund, str) and sperrgrund.strip())):
        raise NachweisFehler(regel, f"{pfad}: veroeffentlichbar/sperrgrund ungültig "
                                    "(gesperrt nur mit Grund, frei nur ohne)")
    return Exportmanifest(eintraege, veroeffentlichbar=veroeffentlichbar, sperrgrund=sperrgrund)


def _ziel_pruefen(ziel: Path) -> None:
    if ziel.is_symlink() or (ziel.exists() and not ziel.is_dir()):
        raise NachweisFehler("export-ziel", f"{ziel}: kein Verzeichnis")
    if ziel.exists() and any(ziel.iterdir()):
        raise NachweisFehler("export-ziel", f"{ziel}: nicht leer — der Export braucht ein "
                                            "frisches Ziel")


def schreibe_export(repo: Path, commit: str, kandidaten: list[Blob], ziel: Path
                    ) -> Exportmanifest:
    """Kandidaten aus dem Commit ins leere (oder noch nicht vorhandene) Ziel schreiben. Inhalt
    per `git cat-file blob`, ausführbar nur bei Git-Modus 100755."""
    _ziel_pruefen(ziel)
    ziel.mkdir(parents=True, exist_ok=True)
    eintraege: list[tuple[str, str, str]] = []
    for blob in sorted(kandidaten, key=lambda b: b.pfad):
        teile = PurePosixPath(blob.pfad).parts
        if PurePosixPath(blob.pfad).is_absolute() or ".." in teile or not teile:
            raise NachweisFehler("export-inhalt", f"{blob.pfad}: unzulässiger Pfad")
        inhalt = _blob_inhalt(repo, blob.hash)
        datei = ziel.joinpath(*teile)
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.write_bytes(inhalt)
        datei.chmod(0o755 if blob.modus == "100755" else 0o644)
        eintraege.append((blob.pfad, hashlib.sha256(inhalt).hexdigest(), blob.modus))
    return Exportmanifest(eintraege)


# --- Release-Diff ------------------------------------------------------------------------------

@dataclass(frozen=True)
class ExportDiff:
    """Änderungen gegenüber dem vorigen Export: neue (Pfad, SHA-256, Modus), geänderte (Pfad,
    SHA alt, SHA neu, Modus alt, Modus neu — Inhalt oder Modus verschieden) und entfernte (Pfad,
    SHA-256, Modus) Dateien, jeweils nach Pfad sortiert."""

    neu: list[tuple[str, str, str]]
    geaendert: list[tuple[str, str, str, str, str]]
    entfernt: list[tuple[str, str, str]]

    def zeilen(self) -> list[str]:
        if not (self.neu or self.geaendert or self.entfernt):
            return ["Export-Diff: keine Änderungen gegenüber dem vorigen Export"]
        ausgabe = [f"Export-Diff: {len(self.neu)} neu, {len(self.geaendert)} geändert, "
                   f"{len(self.entfernt)} entfernt"]
        ausgabe += [f"NEU       {s[:12]} {m}  {p}" for p, s, m in self.neu]
        ausgabe += [f"GEÄNDERT  {a[:12]}→{n[:12]} {ma}→{mn}  {p}"
                    for p, a, n, ma, mn in self.geaendert]
        ausgabe += [f"ENTFERNT  {s[:12]} {m}  {p}" for p, s, m in self.entfernt]
        return ausgabe


def diff_manifeste(alt: Exportmanifest | None, neu: Exportmanifest) -> ExportDiff:
    vorher = {p: (s, m) for p, s, m in alt.eintraege} if alt is not None else {}
    jetzt = {p: (s, m) for p, s, m in neu.eintraege}
    return ExportDiff(
        neu=sorted((p, s, m) for p, (s, m) in jetzt.items() if p not in vorher),
        geaendert=sorted((p, vorher[p][0], s, vorher[p][1], m) for p, (s, m) in jetzt.items()
                         if p in vorher and vorher[p] != (s, m)),
        entfernt=sorted((p, s, m) for p, (s, m) in vorher.items() if p not in jetzt),
    )


def diff_hash(diff: ExportDiff) -> str:
    """SHA-256 über die kanonische JSON-Form des Diffs (mit vollen Hashes): der Wert, den
    `--bestaetigt` verlangt."""
    kanonisch = json.dumps(asdict(diff), sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(kanonisch.encode("utf-8")).hexdigest()


# --- Binärdateien / Freigaben ------------------------------------------------------------------

def lade_freigaben(text: str) -> dict[str, str]:
    """`freigaben.txt`: je Zeile `<sha256>  <pfad>  # Begründung` (zwei Leerzeichen als
    Trenner, damit Pfade mit Leerzeichen eindeutig bleiben). Begründung ist Pflicht."""
    regel = "export-freigaben"
    freigaben: dict[str, str] = {}
    for nummer, zeile in enumerate(text.splitlines(), 1):
        if not zeile.strip() or zeile.lstrip().startswith("#"):
            continue
        treffer = re.fullmatch(r"([0-9a-f]{64})  (\S(?:.*\S)?)  # *(.*)", zeile.rstrip())
        if treffer is None or not treffer.group(3).strip():
            raise NachweisFehler(regel, f"Zeile {nummer}: erwartet „<sha256>  <pfad>  # "
                                        "Begründung“")
        sha, pfad = treffer.group(1), treffer.group(2)
        if pfad in freigaben:
            raise NachweisFehler(regel, f"Zeile {nummer}: {pfad} doppelt freigegeben")
        freigaben[pfad] = sha
    return freigaben


def lade_bau_freigaben(text: str) -> dict[str, tuple[str, int]]:
    """`bauergebnis-freigaben.txt`: je Zeile `<sha256>  <pfad in web/dist>  <anzahl>  # Begründung`.
    Eine Freigabe deckt die Begriffstreffer genau einer Datei des Bauergebnisses — nur bei
    gleichem SHA-256 und exakt gleicher Trefferzahl. Begriffe selbst stehen nie in der Datei
    (sie liegt im Commit und würde sonst selbst leaken); dokumentiert werden sie privat."""
    regel = "export-bau-freigaben"
    freigaben: dict[str, tuple[str, int]] = {}
    for nummer, zeile in enumerate(text.splitlines(), 1):
        if not zeile.strip() or zeile.lstrip().startswith("#"):
            continue
        treffer = re.fullmatch(r"([0-9a-f]{64})  (\S(?:.*\S)?)  ([1-9][0-9]*)  # *(.*)",
                               zeile.rstrip())
        if treffer is None or not treffer.group(4).strip():
            raise NachweisFehler(regel, f"Zeile {nummer}: erwartet „<sha256>  <pfad>  <anzahl>  "
                                        "# Begründung“")
        pfad = treffer.group(2)
        if pfad in freigaben:
            raise NachweisFehler(regel, f"Zeile {nummer}: {pfad} doppelt freigegeben")
        freigaben[pfad] = (treffer.group(1), int(treffer.group(3)))
    return freigaben


def _braucht_freigabe(pfad: str, inhalt: bytes) -> str | None:
    if PurePosixPath(pfad).suffix.lower() in BILDENDUNGEN:
        return "Bild"
    if len(inhalt) > GRENZE_BYTES:
        return "größer als 1 MB"
    try:
        inhalt.decode("utf-8")
    except UnicodeDecodeError:
        return "nicht UTF-8 (binär)"
    return None


def pruefe_binaer(ziel: Path, manifest: Exportmanifest, freigaben: dict[str, str]) -> None:
    """Jede Datei, die ein Bild ist, nicht als UTF-8 lesbar ist oder größer als 1 MB, braucht
    ihren SHA-256 in `freigaben.txt` — eine ersetzte Datei gleichen Namens fällt so auf."""
    offen: list[str] = []
    for pfad, sha, _modus in manifest.eintraege:
        grund = _braucht_freigabe(pfad, ziel.joinpath(*PurePosixPath(pfad).parts).read_bytes())
        if grund is None:
            continue
        if freigaben.get(pfad) != sha:
            zustand = "Hash weicht von der Freigabe ab" if pfad in freigaben else "ohne Freigabe"
            offen.append(f"{pfad} ({grund}, {zustand}, sha256 {sha})")
    if offen:
        raise NachweisFehler("export-binaer", f"{len(offen)} Datei(en) brauchen eine Freigabe "
                                              f"in {FREIGABEN}: " + "; ".join(offen))


# --- Begriffe ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Treffer:
    """Fundstelle eines verbotenen Begriffs; `zeile` 0 = im Pfadnamen."""

    pfad: str
    zeile: int
    begriff: str


def lade_begriffe(pfad: Path | None = None) -> list[str]:
    """Begriffsliste aus `pfad` oder `$SCANVIEW_BEGRIFFE`. Fehlt die Variable, die Datei oder
    ist sie leer → `export-begriffe` (die Prüfung wird nie still übersprungen)."""
    regel = "export-begriffe"
    if pfad is None:
        wert = os.environ.get(BEGRIFFE_VARIABLE)
        if not wert:
            raise NachweisFehler(regel, f"{BEGRIFFE_VARIABLE} ist nicht gesetzt (Pfad der "
                                        "Begriffsliste im Vault)")
        pfad = Path(wert)
    try:
        text = pfad.read_text(encoding="utf-8")
    except OSError as e:
        raise NachweisFehler(regel, f"Begriffsliste {pfad} nicht lesbar: {e.strerror}") from None
    begriffe = lies_positivliste(text)
    if not begriffe:
        raise NachweisFehler(regel, f"Begriffsliste {pfad} ist leer")
    return begriffe


# Wortanfang: davor kein Buchstabe/keine Ziffer (`_` trennt also), ein Wechsel klein→groß
# (camelCase: `projektOrt`) oder Ziffer→Buchstabe (`S001Ort`, `v2ort`). Die Lookarounds sind
# absichtlich case-sensitiv, nur der Begriff selbst wird ohne Rücksicht auf Groß/klein verglichen.
_WORTANFANG = r"(?:(?<![^\W_])|(?<=[a-zäöüß])(?=[A-ZÄÖÜ])|(?<=[0-9])(?=[^\W\d_]))"


# Kodierte Daten. Nur zwei eng umrissene Formen, alles andere (lange Pfade, URLs, Bezeichner)
# ist Text und wird geprüft:
# - SRI-Hash-Token wie in npm-`integrity` in echter Länge (sha1 28, sha256 44, sha384 64,
#   sha512 88 Base64-Zeichen inkl. Auffüllung): tragen keinen Text, werden übersprungen.
#   `sha256-<wort>` in anderer Länge ist Text.
# - Base64-Nutzlasten von Daten-URIs (`base64,<…>`) und von Vite eingebetteten Workern
#   (`atob("<…>")`) sowie als String-Literal eingebettete WebAssembly-Module (`"AGFzbQ…"`,
#   Base64 der Wasm-Kennung `\0asm`, z. B. Spark): der Base64-Text selbst zählt nicht,
#   stattdessen wird der dekodierte Inhalt geprüft — rekursiv, weil ein eingebetteter Worker
#   selbst wieder einen einbettet (bis `_MAX_TIEFE`, darüber Abbruch). Eine Nutzlast, die sich
#   auch mit ergänzter Auffüllung nicht dekodieren lässt, bricht ab (`export-kodiert-unlesbar`)
#   — nie still als Text weiterprüfen.
_HASH_TOKEN = re.compile(
    r"(?:sha1-[A-Za-z0-9+/]{27}=|sha256-[A-Za-z0-9+/]{43}=|sha384-[A-Za-z0-9+/]{64}"
    r"|sha512-[A-Za-z0-9+/]{86}==)(?![A-Za-z0-9+/=])")
_NUTZLAST = re.compile(r'base64,([A-Za-z0-9+/]+={0,2})|atob\("([A-Za-z0-9+/]+={0,2})"\)'
                       r'|"(AGFzbQ[A-Za-z0-9+/]+={0,2})"')
_MAX_TIEFE = 4


class _Unlesbar(Exception):
    """Interner Abbruch beim Dekodieren; `pruefe_begriffe` ergänzt Pfad und Zeile."""


def _dekodiere(nutzlast: str) -> str:
    try:
        return base64.b64decode(nutzlast, validate=True).decode("utf-8", "replace")
    except (ValueError, binascii.Error):
        pass
    kern = nutzlast.rstrip("=")
    try:
        return base64.b64decode(kern + "=" * (-len(kern) % 4), validate=True).decode(
            "utf-8", "replace")
    except (ValueError, binascii.Error):
        raise _Unlesbar(f"Base64-Nutzlast ({len(nutzlast)} Zeichen) nicht dekodierbar") from None


def _funde(text: str, regex: re.Pattern[str], tiefe: int = 0) -> list[str]:
    """Gefundene Begriffe in `text` samt aller eingebetteten, dekodierten Nutzlasten."""
    if tiefe > _MAX_TIEFE:
        raise _Unlesbar(f"Base64-Nutzlasten tiefer als {_MAX_TIEFE} Ebenen verschachtelt")
    funde: list[str] = []
    for zeile in text.splitlines():
        bereiche = [(m.start(), m.end()) for m in _HASH_TOKEN.finditer(zeile)]
        innen: list[str] = []
        for m in _NUTZLAST.finditer(zeile):
            gruppe = next(g for g in (1, 2, 3) if m.group(g) is not None)
            bereiche.append((m.start(gruppe), m.end(gruppe)))
            innen.append(_dekodiere(m.group(gruppe)))
        funde += [f.group(0) for f in regex.finditer(zeile)
                  if not any(a <= f.start() and f.end() <= e for a, e in bereiche)]
        for inhalt in innen:
            funde += _funde(inhalt, regex, tiefe + 1)
    return funde


def _begriff_regex(begriffe: list[str]) -> re.Pattern[str]:
    """Ein Begriff trifft am Wortanfang und auch als Anfang eines längeren Worts (Ableitungen wie
    „…er“, Zusammensetzungen, `ORT_S001`, `ortDaten`, `S001Ort`): solche Lecks sind echt, ein
    Fehlalarm ist billiger. Mitten im Wort (`vorort…`) trifft er nicht."""
    teile = []
    for b in sorted(set(begriffe), key=len, reverse=True):
        links = _WORTANFANG if _WORTZEICHEN.match(b[0]) else ""
        teile.append(f"{links}(?i:{re.escape(b)})")
    return re.compile("|".join(teile))


def pruefe_begriffe(ziel: Path, begriffe: list[str]) -> list[Treffer]:
    """Alle Fundstellen der Begriffe in Pfadnamen und Dateiinhalten unter `ziel`
    (Groß/klein egal, am Wortanfang, auch als Anfang längerer Wörter). Binärdateien werden
    tolerant als UTF-8 gelesen, damit auch eingebettete Zeichenketten auffallen."""
    if not begriffe:
        raise NachweisFehler("export-begriffe", "keine Begriffe übergeben")
    regex = _begriff_regex(begriffe)
    nach_klein = {b.lower(): b for b in begriffe}
    treffer: list[Treffer] = []

    def _begriff(fund: str) -> str:
        return nach_klein.get(fund.lower(), fund)

    for datei in sorted(p for p in ziel.rglob("*") if p.is_file() and not p.is_symlink()):
        relativ = datei.relative_to(ziel).as_posix()
        for fund in regex.finditer(relativ):
            treffer.append(Treffer(relativ, 0, _begriff(fund.group(0))))
        text = datei.read_bytes().decode("utf-8", "replace")
        for nummer, zeile in enumerate(text.splitlines(), 1):
            try:
                funde = _funde(zeile, regex)
            except _Unlesbar as e:
                raise NachweisFehler("export-kodiert-unlesbar",
                                     f"{relativ}:{nummer}: {e}") from None
            treffer += [Treffer(relativ, nummer, _begriff(f)) for f in funde]
    return treffer


def ohne_bau_freigaben(treffer: list[Treffer], wurzel: Path,
                       freigaben: dict[str, tuple[str, int]], praefix: str = "",
                       melde: Callable[[str], None] = print) -> list[Treffer]:
    """Begriffstreffer ohne die freigegebenen: eine Freigabe (Pfad relativ zu `web/dist`,
    unter `wurzel` mit `praefix` davor) greift nur bei gleichem SHA-256 und exakt gleicher
    Trefferzahl dieser Datei — sonst bleiben alle ihre Treffer stehen."""
    for pfad, (sha, anzahl) in sorted(freigaben.items()):
        voll = praefix + pfad
        eigene = [t for t in treffer if t.pfad == voll]
        datei = wurzel / voll
        ist = hashlib.sha256(datei.read_bytes()).hexdigest() if datei.is_file() else None
        if ist == sha and len(eigene) == anzahl:
            treffer = [t for t in treffer if t.pfad != voll]
            melde(f"Bauergebnis-Freigabe angewandt: {voll} ({anzahl} geprüfte Zufallstreffer)")
        else:
            melde(f"Bauergebnis-Freigabe greift nicht: {voll} (SHA-256 oder Trefferzahl "
                  f"{len(eigene)} ≠ {anzahl})")
    return treffer


def lade_bau_freigaben_aus(wurzel: Path) -> dict[str, tuple[str, int]]:
    """`bauergebnis-freigaben.txt` aus einem Worktree; fehlt sie, gibt es keine Freigaben."""
    datei = wurzel / BAU_FREIGABEN
    return lade_bau_freigaben(datei.read_text(encoding="utf-8")) if datei.is_file() else {}


def _begriffe_gate(ziel: Path, begriffe: list[str], wo: str,
                   freigaben: dict[str, tuple[str, int]] | None = None,
                   melde: Callable[[str], None] = print) -> None:
    treffer = ohne_bau_freigaben(pruefe_begriffe(ziel, begriffe), ziel, freigaben or {},
                                 melde=melde)
    if treffer:
        auszug = [f"{t.pfad}:{t.zeile} „{t.begriff}“" for t in treffer[:50]]
        mehr = f" (+{len(treffer) - 50} weitere)" if len(treffer) > 50 else ""
        raise NachweisFehler("export-begriff", f"{len(treffer)} verbotene Begriffe {wo}: "
                                               + "; ".join(auszug) + mehr)


# --- Private Pfadangaben -----------------------------------------------------------------------

# Verweise auf Dateien, die nur im privaten Repo liegen (Audit-Berichte, Abnahmen, interne
# Entscheidungsvermerke, die Rechts-Recherche) und absolute Heimatverzeichnisse. Die Muster sind so
# geschrieben, dass ihr eigener Quelltext sie nicht trifft (export.py liegt selbst im Export).
_PRIVATE_DOKUMENTE = (r"docs/(?:security|acceptance)/"
                      r"|docs/rulings[-]"
                      r"|docs/recht-lizenzen-lieferkette[.]md")
_PRIVATE_PFADE = re.compile(_PRIVATE_DOKUMENTE + r"|/home[/]")


def bauergebnis_muster(arbeitsordner: Path) -> re.Pattern[str]:
    """Muster für das Bauergebnis (gebündelter Fremdcode): private Dokumente plus die konkreten
    Heimat- und Arbeitsverzeichnisse des Bau-Rechners. Jedes Heimatverzeichnis pauschal träfe
    dort feste Zeichenketten von Fremdbibliotheken (Emscripten-Laufzeit: „web_user“)."""
    orte = sorted({str(Path.home()), str(arbeitsordner.resolve()), str(arbeitsordner)},
                  key=len, reverse=True)
    return re.compile(_PRIVATE_DOKUMENTE + "|" + "|".join(re.escape(o) for o in orte))


def pruefe_private_pfade(ziel: Path, muster: re.Pattern[str] = _PRIVATE_PFADE
                         ) -> list[Treffer]:
    """Fundstellen privater Pfadangaben in Dateiinhalten unter `ziel` (`Treffer.begriff` = das
    gefundene Stück). Standard: private Dokumente und jedes absolute Heimatverzeichnis."""
    treffer: list[Treffer] = []
    for datei in sorted(p for p in ziel.rglob("*") if p.is_file() and not p.is_symlink()):
        relativ = datei.relative_to(ziel).as_posix()
        text = datei.read_bytes().decode("utf-8", "replace")
        for nummer, zeile in enumerate(text.splitlines(), 1):
            for fund in muster.finditer(zeile):
                treffer.append(Treffer(relativ, nummer, fund.group(0)))
    return treffer


def _private_pfade_gate(ziel: Path, wo: str,
                        muster: re.Pattern[str] = _PRIVATE_PFADE) -> None:
    treffer = pruefe_private_pfade(ziel, muster)
    if treffer:
        auszug = [f"{t.pfad}:{t.zeile} „{t.begriff}“" for t in treffer[:50]]
        raise NachweisFehler("export-privater-pfad", f"{len(treffer)} private Pfadangaben {wo}: "
                                                     + "; ".join(auszug))


# --- Geheimnisse -------------------------------------------------------------------------------

#: Exit-Code von gitleaks bei einem Fund (`--exit-code`); Werkzeugfehler enden mit 1.
GITLEAKS_FUND = 99
GITLEAKS_BERICHT = "gitleaks.json"


def _gitleaks_lauf(befehl: list[str]) -> int:
    """gitleaks-Container ausführen, Exit-Code zurück (0, `GITLEAKS_FUND` oder Fehler)."""
    try:
        r = subprocess.run(befehl, capture_output=True, check=False, env=gefilterte_umgebung(),
                           stdin=subprocess.DEVNULL)
    except OSError as e:
        raise NachweisFehler("export-geheimnis-werkzeug", f"{befehl[0]} nicht ausführbar: "
                                                          f"{e.strerror}") from None
    if r.returncode not in (0, GITLEAKS_FUND):
        auszug = r.stderr.decode("utf-8", "replace").strip()[-_STDERR_AUSZUG:]
        raise NachweisFehler("export-geheimnis-werkzeug", f"gitleaks endete mit Exit "
                                                          f"{r.returncode}: {auszug or '-'}")
    return r.returncode


def _gitleaks_funde(bericht: Path) -> list[dict[str, object]] | None:
    """Funde aus dem JSON-Bericht; None, wenn er fehlt oder nicht die erwartete Form hat."""
    try:
        daten = json.loads(bericht.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(daten, list) or not all(
            isinstance(f, dict) and isinstance(f.get("RuleID"), str) for f in daten):
        return None
    return daten


def pruefe_geheimnisse(ziel: Path, werkzeuge: dict[str, Werkzeug], konfig: Path) -> None:
    """gitleaks `dir` über das Paket im gehärteten Container (ohne Netz, Eingaben nur lesend)
    mit der gitleaks-Konfiguration des Repos (`--config`, Standardregeln + `sv_`-Detektor).

    Fund und Werkzeugfehler sind verschieden: ein Fund braucht Exit
    `GITLEAKS_FUND` **und** einen JSON-Bericht mit mindestens einem Treffer → `export-geheimnis`
    (geschwärzt: Regel-IDs und Dateien). Sauber ist nur Exit 0 mit leerem Bericht. Alles andere
    — anderer Exit-Code, fehlender oder kaputter Bericht, Exit und Bericht widersprechen sich —
    ist `export-geheimnis-werkzeug`, nie ein „erwarteter Abbruch“."""
    if "gitleaks" not in werkzeuge:
        raise NachweisFehler("export-geheimnis-werkzeug", "gitleaks fehlt in werkzeuge.json")
    with tempfile.TemporaryDirectory(prefix="scanview-gitleaks-") as tmp:
        bericht = Path(tmp)
        befehl = docker_befehl(
            werkzeuge["gitleaks"],
            ["dir", "/export", "--config", "/konfig/gitleaks.toml", "--no-banner", "--redact",
             "--exit-code", str(GITLEAKS_FUND), "--log-level", "warn", "--report-format", "json",
             "--report-path", f"/bericht/{GITLEAKS_BERICHT}"],
            {ziel.resolve(): "/export", konfig.resolve(): "/konfig/gitleaks.toml"},
            {bericht.resolve(): "/bericht"},
        )
        code = _gitleaks_lauf(befehl)
        funde = _gitleaks_funde(bericht / GITLEAKS_BERICHT)
    if code == 0 and funde == []:
        return
    if code == GITLEAKS_FUND and funde:
        regeln = sorted({str(f["RuleID"]) for f in funde})
        dateien = sorted({str(f.get("File", "?")).removeprefix("/export/") for f in funde})
        raise NachweisFehler("export-geheimnis", f"{len(funde)} Fund(e), Regeln "
                                                 f"{', '.join(regeln)}: {', '.join(dateien[:20])}")
    stand = "fehlt/ungültig" if funde is None else f"{len(funde)} Fund(e)"
    raise NachweisFehler("export-geheimnis-werkzeug", f"gitleaks Exit {code}, Bericht {stand} "
                                                      "— Ergebnis nicht auswertbar")


# --- Meldeweg ------------------------------------------------------------------------------------

SECURITY_MD = "SECURITY.md"
#: Steht in SECURITY.md, solange das Meldepostfach noch nicht eingerichtet und geprüft ist. Der
#: private Stand darf so bleiben (die Release-Kette braucht nur das Dokument); öffentlich wird
#: nichts, solange er da ist — ein nicht erreichbarer Meldeweg wäre eine falsche Zusage.
POSTFACH_MARKER = "POSTFACH" + "-OFFEN"


def postfach_sperre(ziel: Path) -> str | None:
    """Grund, warum der Export nicht veröffentlicht werden darf, oder None: `<ziel>/SECURITY.md`
    enthält den Marker `POSTFACH_MARKER`. Nur diese Datei zählt: Quelltext und Tests, die den
    Marker nennen, sperren nichts. Fehlt sie im Export oder ist sie unlesbar, gibt
    es gar keinen Meldeweg → `export-postfach` (Abbruch, auch in der Release-Kette)."""
    datei = ziel / SECURITY_MD
    try:
        if datei.is_symlink() or not datei.is_file():
            raise OSError("fehlt oder ist keine reguläre Datei")
        text = datei.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise NachweisFehler("export-postfach", f"{SECURITY_MD} fehlt im Export oder ist nicht "
                                                f"lesbar ({e}) — ohne Meldeweg kein Export; "
                                                "Positivliste prüfen") from None
    zeilen = [n for n, z in enumerate(text.splitlines(), 1) if POSTFACH_MARKER in z]
    if not zeilen:
        return None
    return (f"{SECURITY_MD}:{zeilen[0]} trägt den Marker {POSTFACH_MARKER} — das Meldepostfach "
            "ist noch nicht eingerichtet und geprüft; erst danach den Marker entfernen")


def pruefe_postfach(ziel: Path) -> None:
    """Veröffentlichungsweg: `export-postfach`, solange `postfach_sperre` einen Grund nennt."""
    grund = postfach_sperre(ziel)
    if grund:
        raise NachweisFehler("export-postfach", f"{grund} und erneut exportieren")


# --- Baubarkeit --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Bauprobe:
    """Ergebnis der Baubarkeitsprüfung: Netzschritte (fürs Manifest) und das Web-Bauergebnis
    im Wegwerf-Abzug (für die Paketprüfung der Build-Ausgaben)."""

    netzschritte: list[Netzschritt]
    web_dist: Path


_PYTHON_TEILE = ("server", "mcp-server", "nachweise", "import")
# import: Docker- und Echtdaten-Tests laufen im Qualitätstor (Repo); im Wegwerf-Abzug ohne beides.
_PYTEST_ZUSATZ: dict[str, tuple[str, ...]] = {"import": ("-m", "not docker and not data")}


def pruefe_baubarkeit(ziel: Path, arbeitsordner: Path) -> Bauprobe:
    """Baut und testet einen Wegwerf-Abzug des Exports unter `<arbeitsordner>/bau` (das Ziel
    selbst bleibt unverändert): web `npm ci --ignore-scripts`, freigegebene Install-Skripte,
    `npm run typecheck`, `npm run lint`, `npm test`, `npm run build`; server, mcp-server,
    nachweise und import `uv sync --locked`, `uv run pytest -q` (import ohne Docker- und
    Echtdaten-Tests; was der Abzug mitliefert, muss in ihm
    bestehen; Tests, die private Dateien brauchen, überspringen sich dort selbst). Fehlt eine
    Komponente, ein Lockfile oder das Bauergebnis → `export-baubarkeit`."""
    regel = "export-baubarkeit"
    for pflicht in ("web/package.json", *(f"{t}/{d}" for t in _PYTHON_TEILE
                                           for d in ("pyproject.toml", "uv.lock"))):
        if not (ziel / pflicht).is_file():
            raise NachweisFehler(regel, f"{pflicht} fehlt im Export")
    arbeitsordner.mkdir(parents=True, exist_ok=True)
    bau = arbeitsordner / "bau"
    if bau.exists():
        raise NachweisFehler(regel, f"{bau}: existiert schon — frischer Arbeitsordner nötig")
    shutil.copytree(ziel, bau, symlinks=True)
    umgebung = bau_umgebung(arbeitsordner)
    schritte: list[Netzschritt] = []

    def netz(befehl: list[str], teil: str) -> None:
        schritte.append(Netzschritt(befehl=befehl, cwd=teil, zeit=_jetzt()))
        _lauf(befehl, regel, cwd=bau / teil, umgebung=umgebung)

    web = bau / "web"
    netz(["npm", "ci", "--ignore-scripts"], "web")
    for paket in erlaubte_install_skripte(web / "package.json", regel):
        netz(["npm", "rebuild", paket, "--ignore-scripts=false"], "web")
    for befehl in (["npm", "run", "typecheck"], ["npm", "run", "lint"], ["npm", "test"],
                   ["npm", "run", "build"]):
        _lauf(befehl, regel, cwd=web, umgebung=umgebung)
    dist = web / "dist"
    if not dist.is_dir() or not any(dist.iterdir()):
        raise NachweisFehler(regel, "web/dist fehlt oder ist leer nach npm run build")
    for teil in _PYTHON_TEILE:
        netz(["uv", "sync", "--locked"], teil)
        _lauf(["uv", "run", "pytest", "-q", *_PYTEST_ZUSATZ.get(teil, ())], regel, cwd=bau / teil,
              umgebung=umgebung)
    return Bauprobe(netzschritte=schritte, web_dist=dist)


def _jetzt() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --- Gesamtablauf ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Exportergebnis:
    commit: str
    manifest: Exportmanifest
    diff: ExportDiff
    diff_hash: str
    netzschritte: list[Netzschritt]


def exportiere(
    repo: Path,
    commit: str,
    ziel: Path,
    *,
    begriffe: list[str],
    werkzeuge: dict[str, Werkzeug],
    vorheriges: Exportmanifest | None,
    bestaetigt: str | None,
    arbeitsordner: Path | None = None,
    melde: Callable[[str], None] = print,
    postfach_vermerken: bool = False,
) -> Exportergebnis:
    """Ganzer Export fail-closed: Blobs → Positivliste → Ziel schreiben → Freigaben, Begriffe,
    gitleaks → Meldeweg (SECURITY.md) → Diff zeigen und Bestätigung prüfen → Baubarkeit →
    Begriffe und gitleaks über `web/dist`.

    Meldeweg: Der Marker „Postfach offen“ sperrt die **Veröffentlichung**. Ohne
    `postfach_vermerken` (eigenständiger Export = Veröffentlichungsweg) bricht der Export mit
    `export-postfach` ab. Die Release-Kette setzt `postfach_vermerken=True`: dann laufen alle
    Prüfungen weiter, und das Exportmanifest trägt `veroeffentlichbar = false` mit Grund.

    Positivliste, Freigaben und gitleaks-Konfiguration stammen aus dem Commit.
    Bricht eine Stufe ab, bleibt das (unvollständige) Ziel liegen; ein neuer Lauf braucht ein
    frisches Ziel."""
    voll = commit_aufloesen(repo, commit)
    if arbeitsordner is None:
        arbeitsordner = Path(tempfile.mkdtemp(prefix="scanview-export-"))
    arbeitsordner.mkdir(parents=True, exist_ok=True)
    _ziel_pruefen(ziel)

    positivliste = lies_positivliste(
        datei_im_commit(repo, voll, POSITIVLISTE).decode("utf-8"))
    freigaben = lade_freigaben(datei_im_commit(repo, voll, FREIGABEN).decode("utf-8"))
    bau_freigaben = (lade_bau_freigaben(datei_im_commit(repo, voll, BAU_FREIGABEN).decode("utf-8"))
                     if any(b.pfad == BAU_FREIGABEN for b in git_blobs(repo, voll)) else {})
    konfig = arbeitsordner / "gitleaks.toml"
    konfig.write_bytes(datei_im_commit(repo, voll, GITLEAKS_KONFIG))

    manifest = schreibe_export(repo, voll, kandidaten(git_blobs(repo, voll), positivliste), ziel)
    melde(f"Export von {voll[:12]}: {len(manifest.eintraege)} Dateien → {ziel}")
    pruefe_binaer(ziel, manifest, freigaben)
    _begriffe_gate(ziel, begriffe, "im Export")
    _private_pfade_gate(ziel, "im Export")
    pruefe_geheimnisse(ziel, werkzeuge, konfig)
    sperre = postfach_sperre(ziel)
    if sperre and not postfach_vermerken:
        pruefe_postfach(ziel)
    if sperre:
        manifest = Exportmanifest(manifest.eintraege, veroeffentlichbar=False, sperrgrund=sperre)
        melde(f"Veröffentlichung gesperrt: {sperre}")

    diff = diff_manifeste(vorheriges, manifest)
    wert = diff_hash(diff)
    for zeile in diff.zeilen():
        melde(zeile)
    melde(f"Diff-Hash: {wert}")
    if bestaetigt != wert:
        grund = "fehlt" if bestaetigt is None else "passt nicht zu diesem Diff"
        raise NachweisFehler("export-bestaetigung", f"Bestätigung {grund} — nach Durchsicht des "
                                                    f"Diffs erneut mit --bestaetigt {wert}")

    probe = pruefe_baubarkeit(ziel, arbeitsordner)
    _begriffe_gate(probe.web_dist, begriffe, "im Bauergebnis web/dist", bau_freigaben, melde)
    _private_pfade_gate(probe.web_dist, "im Bauergebnis web/dist",
                        bauergebnis_muster(arbeitsordner))
    pruefe_geheimnisse(probe.web_dist, werkzeuge, konfig)
    melde("Baubarkeit: web, server, mcp-server, nachweise, import grün; Bauergebnis ohne Befund")
    return Exportergebnis(commit=voll, manifest=manifest, diff=diff, diff_hash=wert,
                          netzschritte=probe.netzschritte)
