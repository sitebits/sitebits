"""Betrieb: das Update-Protokoll einer Instanz (`/var/log/scanview/updates.log`, geschrieben von
`deploy/platform/einrichten.sh`) gegen die signierten Manifeste im Nachweis-Archiv auswerten und
festhalten, welche Versionen unterstützt werden.

Zeilenformat (eine je Lauf, der den Host verändert):
  `<zeit> <version> <manifest-sha256> <quellcommit> sicherung=<pfad> artefakte=<name:sha256,…>
  ergebnis=ok|abgebrochen|abgelehnt[ grund=<regel>][ warnung=<kennung>]…`
(null oder mehr Warnkennungen, z. B. Barrierefreiheit und Impressum/Datenschutz zugleich).
Der Repo-Modus (nur lokale Tests) trägt die Version `ungeprüft`, `-` als Manifest, Quellcommit
und Artefakte. Jede `ok`-Zeile muss zu einem archivierten, signierten Release passen; wer im
Repo-Modus läuft, läuft ohne geprüftes Release."""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .fehler import NachweisFehler
from .manifest import MANIFEST_DATEI, lade_signiertes_manifest, pruefe_version

REPO_MODUS = "ungeprüft"
NICHT_AUSGEWERTET = "nicht ausgewertet"
UNTERSTUETZT_DATEI = "unterstuetzt.toml"
#: Instanzen des Betriebs (Bezeichner wie im Nachweis-Archiv:
#: pruefungen/<datum>[.<lauf>]-updates-<L|S>/).
INSTANZEN = ("L", "S")

_SHA = r"[0-9a-f]{64}"
_ZEILE = re.compile(
    r"(?P<zeit>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) (?P<version>\S+) (?P<manifest>\S+) "
    r"(?P<quellcommit>\S+) sicherung=(?P<sicherung>\S+) artefakte=(?P<artefakte>\S+) "
    r"ergebnis=(?P<ergebnis>ok|abgebrochen|abgelehnt)(?: grund=(?P<grund>[a-z-]+))?"
    # Zusätze in der Reihenfolge, in der einrichten.sh sie in HINWEIS zusammensetzt:
    # Repo-Modus-Warnung (Pfad darf Leerzeichen enthalten), fortgesetzte Ersteinrichtung,
    # Warnkennungen (null oder mehr, je eine Prüfung nach der Gesundheitsprüfung).
    r"(?: WARNUNG: kein geprüftes Release quelle=(?P<quelle>.+?))?"
    r"(?P<fortgesetzt> ersteinrichtung=fortgesetzt)?"
    r"(?P<warnungen>(?: warnung=[a-z-]+)*)")
_SHA_MUSTER = re.compile(_SHA)
_COMMIT = re.compile(r"[0-9a-f]{40}")
_ARTEFAKT = re.compile(rf"([a-z0-9-]+):({_SHA})")
_SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(.+))?")


@dataclass(frozen=True)
class UpdateZeile:
    nummer: int
    zeit: str
    version: str
    manifest_sha256: str
    quellcommit: str
    sicherung: str
    artefakte: dict[str, str]
    ergebnis: str
    grund: str | None
    #: Zusätze hinter dem Ergebnis (Repo-Modus, fortgesetzte Ersteinrichtung, Warnkennungen) als
    #: Klartext — informativ, keine Abweichung.
    zusaetze: tuple[str, ...] = ()


def _format(nummer: int, text: str) -> NachweisFehler:
    return NachweisFehler("updates-format", f"Zeile {nummer}: {text}")


def _artefakte(nummer: int, roh: str) -> dict[str, str]:
    if roh == "-":
        return {}
    paare: dict[str, str] = {}
    for teil in roh.split(","):
        treffer = _ARTEFAKT.fullmatch(teil)
        if treffer is None or treffer[1] in paare:
            raise _format(nummer, f"artefakte „{roh}“ ist keine Liste name:sha256")
        paare[treffer[1]] = treffer[2]
    return paare


def lies_updates(text: str) -> list[UpdateZeile]:
    """Protokolltext lesen; leere Zeilen werden übersprungen. Jede andere Zeile muss dem Format
    entsprechen — sonst `NachweisFehler("updates-format", "Zeile n: …")`. Bei `ok` müssen
    Manifest-Prüfsumme, Quellcommit und Artefakte gesetzt sein (oder die Version `ungeprüft`)."""
    zeilen: list[UpdateZeile] = []
    for nummer, roh in enumerate(text.splitlines(), start=1):
        if not roh.strip():
            continue
        t = _ZEILE.fullmatch(roh)
        if t is None:
            raise _format(nummer, "entspricht nicht dem Protokollformat von einrichten.sh")
        if t["grund"] is not None and t["ergebnis"] == "ok":
            raise _format(nummer, "grund= gibt es nur bei ergebnis=abgelehnt oder abgebrochen")
        if t["ergebnis"] == "abgelehnt" and t["grund"] is None:
            raise _format(nummer, "ergebnis=abgelehnt ohne grund=")
        warnungen = t["warnungen"].split()
        if len(set(warnungen)) != len(warnungen):
            raise _format(nummer, "Warnkennung doppelt (einrichten.sh schreibt jede einmal)")
        if t["ergebnis"] == "abgelehnt" and (t["quelle"] or t["fortgesetzt"] or warnungen):
            raise _format(nummer, "ergebnis=abgelehnt trägt nur grund=")
        repo = t["version"] == REPO_MODUS
        if t["quelle"] is not None and not repo:
            raise _format(nummer, "Warnung „kein geprüftes Release“ gibt es nur im Repo-Modus "
                                  f"(Version {REPO_MODUS})")
        zusaetze = tuple(
            [*([f"ohne geprüftes Release (quelle={t['quelle']})"] if t["quelle"] else []),
             *(["ersteinrichtung=fortgesetzt"] if t["fortgesetzt"] else []),
             *warnungen])
        if t["ergebnis"] == "ok" and not repo:
            try:
                pruefe_version(t["version"], "updates-format")
            except NachweisFehler:
                raise _format(nummer, f"Version „{t['version']}“ ungültig") from None
            if not _SHA_MUSTER.fullmatch(t["manifest"]):
                raise _format(nummer, "Manifest-Prüfsumme fehlt oder ist ungültig")
            if not _COMMIT.fullmatch(t["quellcommit"]):
                raise _format(nummer, "Quellcommit fehlt oder ist ungültig")
        zeilen.append(UpdateZeile(
            nummer=nummer, zeit=t["zeit"], version=t["version"], manifest_sha256=t["manifest"],
            quellcommit=t["quellcommit"], sicherung=t["sicherung"],
            artefakte=_artefakte(nummer, t["artefakte"]), ergebnis=t["ergebnis"],
            grund=t["grund"], zusaetze=zusaetze))
        if t["ergebnis"] == "ok" and not repo and not zeilen[-1].artefakte:
            raise _format(nummer, "ok-Zeile ohne Artefakt-Prüfsummen")
    return zeilen


def _gleich_manifest(z: UpdateZeile, daten: dict[str, Any], manifest_sha: str) -> list[str]:
    wo = f"{z.zeit} {z.version}"
    abweichungen: list[str] = []
    if z.manifest_sha256 != manifest_sha:
        abweichungen.append(f"{wo}: Manifest-Prüfsumme weicht vom archivierten Manifest ab")
    if z.quellcommit != daten["quellcommit"]:
        abweichungen.append(f"{wo}: Quellcommit weicht vom archivierten Manifest ab")
    soll = {a["name"]: a["sha256"] for a in daten["artefakte"] if a["ausgeliefert"]}
    for name in sorted(set(soll) | set(z.artefakte)):
        if name not in z.artefakte:
            abweichungen.append(f"{wo}: Artefakt {name} fehlt im Protokoll")
        elif name not in soll:
            abweichungen.append(f"{wo}: Artefakt {name} steht nicht im archivierten Manifest")
        elif z.artefakte[name] != soll[name]:
            abweichungen.append(f"{wo}: Artefakt {name} weicht vom archivierten Manifest ab")
    return abweichungen


def pruefe_gegen_archiv(zeilen: list[UpdateZeile], archiv: Path,
                        allowed_signers: Path) -> list[str]:
    """Abweichungen (Klartext) der `ok`-Zeilen gegen `releases/<version>/manifest.json`:
    Manifest signiert (`lade_signiertes_manifest` — eine falsche Signatur ist ein Fehler, keine
    Abweichung), Prüfsumme des Manifests, Quellcommit und jedes Artefakt-Paar gleich. Version
    `ungeprüft` (Repo-Modus) ist eine Abweichung. `abgelehnt`/`abgebrochen` haben keine
    (Release-)Wirkung und stehen in `informative_zeilen`."""
    abweichungen: list[str] = []
    geladen: dict[str, tuple[dict[str, Any], str] | None] = {}
    for z in zeilen:
        if z.ergebnis != "ok":
            continue
        if z.version == REPO_MODUS:
            abweichungen.append(f"Instanz läuft ohne geprüftes Release ({z.zeit})")
            continue
        if z.version not in geladen:
            manifest = archiv / "releases" / z.version / MANIFEST_DATEI
            if not manifest.is_file():
                geladen[z.version] = None
            else:
                daten = lade_signiertes_manifest(manifest, allowed_signers)
                geladen[z.version] = (daten, _sha256(manifest))
        eintrag = geladen[z.version]
        if eintrag is None:
            abweichungen.append(f"{z.zeit}: Version {z.version} liegt nicht im Archiv")
            continue
        abweichungen += _gleich_manifest(z, *eintrag)
    return abweichungen


def _sha256(pfad: Path) -> str:
    return hashlib.sha256(pfad.read_bytes()).hexdigest()


def informative_zeilen(zeilen: list[UpdateZeile]) -> list[str]:
    """`abgelehnt`- und `abgebrochen`-Läufe sowie Läufe mit Zusätzen (Warnkennung, fortgesetzte
    Ersteinrichtung, Repo-Modus) als Klartext (keine Abweichung, aber nachlesbar)."""
    return [f"{z.zeit}: {z.ergebnis}" + (f" (grund={z.grund})" if z.grund else "")
            + ("" if z.version in ("-", REPO_MODUS) else f", Version {z.version}")
            + (f" — {'; '.join(z.zusaetze)}" if z.zusaetze else "")
            for z in zeilen if z.ergebnis != "ok" or z.zusaetze]


def aktueller_stand(zeilen: list[UpdateZeile]) -> str | None:
    """Version der letzten `ok`-Zeile; `None`, wenn es keine gibt oder die Instanz zuletzt im
    Repo-Modus lief (dann gibt es keinen geprüften Stand)."""
    for z in reversed(zeilen):
        if z.ergebnis == "ok":
            return None if z.version == REPO_MODUS else z.version
    return None


_BLOCK = re.compile(r"\d+|\D+")


def _kennung(kennung: str) -> tuple[tuple[int, int, str], ...]:
    """Eine Vorabkennung in vergleichbare Blöcke zerlegen: Ziffernfolgen zählen als Zahl (`rc10`
    ist neuer als `rc2`, wie bei `rc.10` und `rc.2`), Text wird verglichen, und Zahlen sind vor
    Text kleiner. (Strenges SemVer §11 würde `rc10` < `rc2` ordnen; die Releasekette will die
    natürliche Reihenfolge.)"""
    return tuple((0, int(b), "") if b.isdigit() else (1, 0, b) for b in _BLOCK.findall(kennung))


def _semver_schluessel(version: str) -> tuple[int, int, int, int, tuple[Any, ...]]:
    t = _SEMVER.fullmatch(version)
    if t is None:  # nur Namen, die `_SEMVER` schon bestanden haben, kommen hierher
        raise NachweisFehler("unterstuetzt-format", f"Version „{version}“ ist keine SemVer")
    # Ein Release ohne Vorabkennung ist neuer als jede Vorabversion derselben Zahlen; die
    # Kennung wird je Punkt-getrenntem Teil verglichen, der kürzere Teil zuerst.
    vorab = tuple(_kennung(k) for k in t[4].split(".")) if t[4] else ()
    return int(t[1]), int(t[2]), int(t[3]), 1 if t[4] is None else 0, vorab


def _release_versionen(archiv: Path) -> list[str]:
    """Archivierte Versionen: Ordner mit SemVer-Namen, die ein `manifest.json` enthalten (ein
    leerer oder halb angelegter Ordner ist keine Version)."""
    ordner = archiv / "releases"
    if not ordner.is_dir():
        return []
    return [p.name for p in ordner.iterdir()
            if p.is_dir() and _SEMVER.fullmatch(p.name) and (p / "manifest.json").is_file()]


def unterstuetzte_versionen(archiv: Path, staende: dict[str, str | None]) -> list[str]:
    """Neueste archivierte Version (SemVer) und der aktuelle geprüfte Stand je Instanz,
    absteigend sortiert. Ohne eine einzige Version `NachweisFehler("unterstuetzt-leer")`."""
    versionen = set()
    vorhanden = _release_versionen(archiv)
    if vorhanden:
        versionen.add(max(vorhanden, key=_semver_schluessel))
    versionen |= {v for v in staende.values() if v not in (None, REPO_MODUS, NICHT_AUSGEWERTET)}
    if not versionen:
        raise NachweisFehler("unterstuetzt-leer", "keine Version im Archiv und kein geprüfter "
                                                  "Stand einer Instanz — nichts als unterstützt "
                                                  "festzuhalten")
    return sorted(versionen, key=_semver_schluessel, reverse=True)


def lies_unterstuetzt(pfad: Path) -> dict[str, str]:
    """Bisherigen `stand` je Instanz aus `betrieb/unterstuetzt.toml` (fehlt die Datei: leer)."""
    if not pfad.is_file():
        return {}
    try:
        daten = tomllib.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise NachweisFehler("unterstuetzt-format", f"{pfad}: nicht lesbar ({e})") from None
    stand = daten.get("stand")
    if not isinstance(stand, dict) or not all(
            k in INSTANZEN and isinstance(v, str) for k, v in stand.items()):
        raise NachweisFehler("unterstuetzt-format", f"{pfad}: stand ist keine Tabelle "
                                                    "Instanz → Version")
    return dict(stand)


def stelle_unterstuetzt_dar(versionen: list[str], stand: dict[str, str | None],
                            heute: date) -> bytes:
    """`betrieb/unterstuetzt.toml`: `versionen`, `stand` je Instanz (nie ausgewertet →
    „nicht ausgewertet“, kein stilles Weglassen) und `aktualisiert`."""
    liste = ", ".join(f'"{v}"' for v in versionen)
    tabelle = ", ".join(f'{i} = "{stand.get(i) or NICHT_AUSGEWERTET}"' for i in INSTANZEN)
    return (f"versionen = [{liste}]\nstand = {{{tabelle}}}\n"
            f"aktualisiert = {heute.isoformat()}\n").encode()
