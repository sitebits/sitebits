"""Wochenscan: alle unterstützten Versionen (`betrieb/unterstuetzt.toml`) gegen eine frische
Schwachstellen-DB scannen. Fail-closed: ohne unterstützte Version, mit ungültiger Manifest-
Signatur, verändertem oder fehlendem SBOM oder veralteter DB gibt es kein Ergebnis, nur einen
`NachweisFehler`. Gescannt werden die SBOMs der ausgelieferten Artefakte aus dem signierten
Release im Archiv; die Lock-Hashes für die VEX-Aussagen stammen aus dem Quellcommit des Releases,
nicht aus dem aktuellen Stand des Repos.

Laufzeit ist reine Standardbibliothek (kein uv): der Wochenscan läuft aus einem signierten
Release-Tag heraus."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import osv, scan, vex
from .fehler import NachweisFehler
from .manifest import ausgeliefert_fuer, lade_signiertes_manifest, pruefe_version
from .werkzeuge import Werkzeug, gefilterte_umgebung, git_befehl

UNTERSTUETZT = Path("betrieb") / "unterstuetzt.toml"
_COMMIT = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class Wochenergebnis:
    """`blockierend` enthält nur Versionen mit blockierenden Treffern (Zeilen des Treffer-Gates),
    `neu` die Schwachstellen-IDs daraus, die nicht in `bekannt` standen. `artefakte` je Version
    und Artefakt: SBOM-Name und Trefferzahlen je Schwere."""

    versionen: list[str]
    db: scan.DbStand
    treffer_je_version: dict[str, list[scan.Treffer]]
    blockierend: dict[str, list[str]]
    neu: list[str]
    artefakte: dict[str, dict[str, Any]] = field(default_factory=dict)


def lade_unterstuetzt(archiv: Path) -> list[str]:
    """Unterstützte Versionen aus `betrieb/unterstuetzt.toml`. Fehlt die Datei, ist sie nicht
    lesbar oder die Liste leer, wäre „nichts gefunden“ nur „nichts geprüft“ —
    `NachweisFehler("wochenscan-versionen")`."""
    pfad = archiv / UNTERSTUETZT

    def fehler(text: str) -> NachweisFehler:
        return NachweisFehler("wochenscan-versionen", f"{pfad}: {text}")

    try:
        daten = tomllib.loads(pfad.read_text(encoding="utf-8"))
    except OSError:
        raise fehler("fehlt oder ist nicht lesbar — nichts zu scannen") from None
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise fehler(f"kein gültiges TOML ({e})") from None
    versionen = daten.get("versionen")
    if not isinstance(versionen, list) or not versionen:
        raise fehler("keine unterstützte Version — nichts zu scannen")
    if not all(isinstance(v, str) for v in versionen) or len(set(versionen)) != len(versionen):
        raise fehler("versionen muss eine Liste verschiedener Texte sein")
    for v in versionen:
        pruefe_version(v, "wochenscan-versionen")
    return list(versionen)


def release_sboms(archiv: Path, version: str, allowed_signers: Path
                  ) -> tuple[dict[str, Path], str]:
    """SBOMs der ausgelieferten Artefakte einer archivierten Version und ihr Quellcommit. Das
    Manifest ist gegen `allowed_signers` (den übergebenen Anker, nie die archivierte Kopie)
    signiert; der SHA-256 jedes SBOMs stimmt mit dem Manifest überein, alle ausgelieferten
    (Menge des Manifest-Schemas) sind vorhanden — sonst `NachweisFehler` (`signatur`,
    `wochenscan-sbom`)."""
    pruefe_version(version, "wochenscan-versionen")
    ordner = archiv / "releases" / version
    daten = lade_signiertes_manifest(ordner / "manifest.json", allowed_signers)
    if daten["version"] != version:
        raise NachweisFehler("wochenscan-sbom", f"{version}: Manifest gehört zur Version "
                                                f"{daten['version']}")
    sboms: dict[str, Path] = {}
    for name in sorted(ausgeliefert_fuer(daten)):
        datei = f"scanview-{name}-{version}.cdx.json"
        soll = daten["sboms"].get(datei)
        pfad = ordner / "sbom" / datei
        try:
            ist = hashlib.sha256(pfad.read_bytes()).hexdigest()
        except OSError:
            raise NachweisFehler("wochenscan-sbom", f"{version}: {pfad} fehlt oder ist nicht "
                                                    "lesbar") from None
        if soll is None or ist != soll:
            raise NachweisFehler("wochenscan-sbom", f"{version}: {datei} hat SHA-256 {ist}, "
                                                    f"Manifest {soll}")
        sboms[name] = pfad
    return sboms, daten["quellcommit"]


def lock_hashes_im_commit(repo: Path, commit: str) -> dict[str, str]:
    """SHA-256 der Lockdateien, wie sie in `commit` stehen (`git show <commit>:<datei>`). Ein
    Commit, der nicht im Repo liegt, oder eine fehlende Lockdatei → `scan-lock`."""
    if not _COMMIT.fullmatch(commit):
        raise NachweisFehler("scan-lock", f"„{commit}“ ist kein voller Commit-Hash")

    def lesen(pfad: str) -> bytes | None:
        try:
            r = subprocess.run(git_befehl("show", f"{commit}:{pfad}", repo=repo),
                               capture_output=True, check=False, stdin=subprocess.DEVNULL,
                               env=gefilterte_umgebung())
        except OSError as e:
            raise NachweisFehler("scan-lock", f"git nicht ausführbar: {e.strerror}") from None
        return r.stdout if r.returncode == 0 else None

    return vex.lock_hashes_aus(lesen)


def osv_abfragen(archiv: Path, allowed_signers: Path) -> list[osv.Abfrage]:
    """Eigenbau-Abfragen aller unterstützten Versionen (aus ihren signierten SBOMs) — für den
    OSV-Netzschritt vor dem Wochenscan."""
    abfragen: list[osv.Abfrage] = []
    for version in lade_unterstuetzt(archiv):
        sboms, _ = release_sboms(archiv, version, allowed_signers)
        for pfad in sboms.values():
            abfragen += osv.abfragen_aus_bom(json.loads(pfad.read_text(encoding="utf-8")))
    return abfragen


def wochenscan(archiv: Path, repo: Path, vex_pfad: Path, allowed_signers: Path, cache: Path,
               werkzeuge: dict[str, Werkzeug], ausgabe: Path, *, bekannt: set[str],
               jetzt: datetime) -> Wochenergebnis:
    """Alle unterstützten Versionen scannen. `ausgabe` darf nicht existieren und wird
    angelegt, wenn der Scan durchgelaufen ist. Die DB muss höchstens 24 h alt sein, bei
    verspäteter Quelle bis 72 h (`scan.db_pruefen`, gegen `jetzt`; vorher
    `scan.db_aktualisieren`, ein protokollierter Online-Schritt). Je Version:
    Scan der ausgelieferten SBOMs ohne Netz, dann `vex.treffer_gate` mit den Lock-Hashes des
    Release-Commits."""
    if ausgabe.exists():
        raise NachweisFehler("wochenscan-ausgabe", f"{ausgabe}: existiert schon — wird nicht "
                                                   "überschrieben")
    versionen = lade_unterstuetzt(archiv)
    release = {v: release_sboms(archiv, v, allowed_signers) for v in versionen}
    aussagen = vex.lade_vex(vex_pfad)
    # Eigenbauten prüft der OSV-Stand (ohne Netz geladen, wie die trivy-DB ≤ 24 h).
    osv_stand = osv.stand_falls_noetig(
        [p for sboms, _ in release.values() for p in sboms.values()], cache, jetzt)
    stand: scan.DbStand | None = None
    treffer_je_version: dict[str, list[scan.Treffer]] = {}
    blockierend: dict[str, list[str]] = {}
    artefakte: dict[str, dict[str, Any]] = {}
    for version in versionen:
        sboms, quellcommit = release[version]
        locks = lock_hashes_im_commit(repo, quellcommit)
        lauf = scan.scanne_alle(sboms, cache, vex_pfad, werkzeuge, uhr=jetzt, osv=osv_stand)
        stand = lauf.stand
        geliefert = [t for t in lauf.treffer if t.artefakt in sboms]
        treffer_je_version[version] = geliefert
        artefakte[version] = lauf.artefakte
        zeilen = vex.treffer_gate(geliefert, aussagen, jetzt.date(), lock_hashes=locks)
        if zeilen:
            blockierend[version] = zeilen
    if stand is None:  # nicht erreichbar: `lade_unterstuetzt` liefert mindestens eine Version
        raise NachweisFehler("wochenscan-versionen", "keine Version gescannt")
    neu = sorted({z.split(" ", 1)[0] for zeilen in blockierend.values() for z in zeilen}
                 - bekannt)
    try:
        ausgabe.mkdir(parents=True)
    except OSError as e:
        raise NachweisFehler("wochenscan-ausgabe", f"{ausgabe}: nicht anlegbar "
                                                   f"({e.strerror})") from None
    return Wochenergebnis(versionen, stand, treffer_je_version, blockierend, neu, artefakte)
