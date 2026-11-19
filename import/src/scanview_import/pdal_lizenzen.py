"""Lizenzbeilage der PDAL-Umgebung auf dem Host erzeugen (`scanview-import pdal-lizenzen`).

Die PDAL-Umgebung (``import/pdal-umgebung``) wird beim Betreiber aus ``pixi.lock`` installiert
und zählt damit als ausgeliefert. Die vollständigen Lizenztexte der Pakete stehen nicht im Repo
(sie enthalten fremde Autorennamen und GPL-Texte, die der öffentliche Export nicht führen soll),
sondern werden auf dem Host aus den entpackten Paketen erzeugt: ``einrichten.sh`` ruft diesen
Befehl nach ``pixi install`` mit dem Paket-Cache des Baus auf und legt das Ergebnis als
``/opt/scanview/pdal/LIZENZEN.txt`` ab. Scheitert er, startet kein Worker.

Eingaben:

- ``pixi.lock`` — nur für den SHA-256; ``quellen.json`` muss genau zu diesem Lock gehören;
- ``quellen.json`` — je Paket Name, Version, Build, Paket-URL, SHA-256, Lizenz, Rezept, Upstream,
  die Lizenzdateien im Paket (``lizenzdateien``) und belegte Ergänzungen aus anderen Paketen
  desselben Locks (``ergaenzungen``: Paket, Datei, Erklärung);
- der Paket-Cache von pixi (``<cache>/pkgs/<name>-<version>-<build>/``).

Fail-closed: fehlt ein Paket im Cache, passt dessen ``info/index.json`` nicht, fehlt eine
Lizenzdatei oder bleibt ein Paket ohne Lizenztext, bricht der Befehl ab. Nur Standardbibliothek.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

TRENNER = "=" * 78
#: Ein Lizenztext muss wenigstens so lang sein (gegen leere oder abgeschnittene Dateien).
MINDESTLAENGE = 40
_NAME = re.compile(r"[A-Za-z0-9_.+-]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class LizenzFehler(ValueError):
    """Beilage nicht erzeugbar (Eingaben passen nicht zusammen oder Text fehlt)."""


def _relativ(pfad: object, wo: str) -> PurePosixPath:
    """Relativer Pfad innerhalb eines Paketordners, ohne ``..`` und ohne absolute Teile."""
    if not isinstance(pfad, str) or not pfad:
        raise LizenzFehler(f"{wo}: Pfad fehlt")
    p = PurePosixPath(pfad)
    if p.is_absolute() or ".." in p.parts or "\\" in pfad:
        raise LizenzFehler(f"{wo}: Pfad {pfad!r} verlässt den Paketordner")
    return p


def _paketordner(cache: Path, eintrag: dict[str, Any]) -> Path:
    url = str(eintrag.get("url", ""))
    datei = url.rsplit("/", 1)[-1]
    if not datei.endswith(".conda") or not _NAME.fullmatch(datei.removesuffix(".conda")):
        raise LizenzFehler(f"{eintrag.get('name')}: Paket-URL unlesbar")
    ordner = cache / "pkgs" / datei.removesuffix(".conda")
    if ordner.is_symlink() or not ordner.is_dir():
        raise LizenzFehler(f"{eintrag.get('name')}: Paket fehlt im Cache ({ordner})")
    try:
        index = json.loads((ordner / "info" / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise LizenzFehler(f"{ordner}: info/index.json nicht lesbar ({e})") from None
    soll = (eintrag.get("name"), eintrag.get("version"), eintrag.get("build"))
    if not isinstance(index, dict) or (index.get("name"), index.get("version"),
                                       index.get("build")) != soll:
        raise LizenzFehler(f"{ordner}: info/index.json passt nicht zu quellen.json")
    return ordner


def _lies(ordner: Path, rel: PurePosixPath, wo: str) -> str:
    pfad = ordner.joinpath(*rel.parts)
    aktuell = ordner
    for teil in rel.parts:  # kein Symlink auf dem Weg (der Cache ist entpackte Fremdware)
        aktuell = aktuell / teil
        if aktuell.is_symlink():
            raise LizenzFehler(f"{wo}: {rel} ist ein Symlink")
    try:
        return pfad.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise LizenzFehler(f"{wo}: {rel} nicht lesbar ({e.strerror})") from None


def _kopfkommentar(text: str) -> str:
    """Erster C-Kommentarblock (Segenstext im Kopf einer Header-Datei)."""
    ende = text.find("*/")
    return text[: ende + 2] if text.startswith("/*") and ende > 0 else ""


def _kopf(e: dict[str, Any]) -> str:
    rezept, up = e.get("rezept", {}), e.get("upstream", {})
    kopf = (f"\n{TRENNER}\n{e['name']} {e['version']} (Build {e['build']})\n"
            f"Lizenz laut Paket: {e['lizenz']}\nPaket: {e['url']}\nSHA-256: {e['sha256']}\n"
            f"Rezept: {rezept.get('feedstock', '')} (Commit {rezept.get('commit') or 'unbekannt'})"
            f"\nUpstream: {up.get('dev') or up.get('home', '')}\n")
    if up.get("url"):
        beleg = (f"SHA-256 {up['sha256']}" if up.get("sha256") else f"Rev {up.get('rev', '')}")
        kopf += f"Quelle: {up['url']} ({beleg})\n"
    return kopf + f"{TRENNER}\n"


def _abschnitt(e: dict[str, Any], ordner: dict[str, Path]) -> str:
    name = str(e["name"])
    teile = [_kopf(e)]
    texte = 0
    for datei in e.get("lizenzdateien", []):
        rel = _relativ(datei, name)
        text = _lies(ordner[name], rel, name).rstrip()
        texte += len(text) >= MINDESTLAENGE
        teile.append(f"--- {rel} ---\n{text}\n")
    for erg in e.get("ergaenzungen", []):
        if not isinstance(erg, dict) or erg.get("paket") not in ordner:
            raise LizenzFehler(f"{name}: Ergänzung aus einem Paket außerhalb des Locks")
        rel = _relativ(erg.get("datei"), name)
        erklaerung = str(erg.get("erklaerung", "")).strip()
        if not erklaerung:
            raise LizenzFehler(f"{name}: Ergänzung ohne Erklärung")
        text = _lies(ordner[erg["paket"]], rel, name)
        if rel.suffix == ".h":
            text = _kopfkommentar(text)
        text = text.rstrip()
        texte += len(text) >= MINDESTLAENGE
        teile.append(f"--- Ergänzung: {erg['paket']}/{rel} — {erklaerung} ---\n{text}\n")
    if not texte:
        raise LizenzFehler(f"{name}: kein Lizenztext (weder im Paket noch als Ergänzung)")
    return "".join(teile)


def erzeuge(lock: bytes, quellen: dict[str, Any], cache: Path) -> str:
    """Text von ``LIZENZEN.txt`` aus Lock (SHA-256), ``quellen.json`` und Paket-Cache."""
    sha = hashlib.sha256(lock).hexdigest()
    if not isinstance(quellen, dict) or quellen.get("lockSha256") != sha:
        raise LizenzFehler(f"quellen.json gehört nicht zu diesem Lock (SHA-256 {sha})")
    pakete = quellen.get("pakete")
    if not isinstance(pakete, list) or not pakete or not all(isinstance(e, dict) for e in pakete):
        raise LizenzFehler("quellen.json: keine Pakete")
    for e in pakete:
        for feld in ("name", "version", "build", "url", "lizenz"):
            if not isinstance(e.get(feld), str) or not e[feld]:
                raise LizenzFehler(f"quellen.json: Paket ohne {feld}")
        if not _SHA256.fullmatch(str(e.get("sha256", ""))):
            raise LizenzFehler(f"{e['name']}: SHA-256 fehlt")
    namen = [str(e["name"]) for e in pakete]
    if len(set(namen)) != len(namen):
        raise LizenzFehler("quellen.json: Paketname doppelt")
    ordner = {str(e["name"]): _paketordner(cache, e) for e in pakete}
    kopf = (
        "Lizenztexte der PDAL-Umgebung des Imports (import/pdal-umgebung/pixi.lock)\n"
        f"Lock SHA-256: {sha}\n\n"
        "Die Pakete stammen unverändert von conda-forge; einrichten.sh hat sie geladen, ihre\n"
        "SHA-256 gegen das Lock geprüft und diese Datei aus den Paketen erzeugt. siteBits ruft\n"
        "PDAL und untwine nur als eigene Prozesse auf (kein Linken, kein Import als Bibliothek).\n"
        "Quelltext: Rezept (conda-forge-Feedstock) und Upstream-Quelle je Paket unten.\n")
    return kopf + "".join(_abschnitt(e, ordner) for e in pakete)


def schreibe(lock: Path, quellen: Path, cache: Path, ausgabe: Path | None) -> int:
    """Beilage erzeugen und nach ``ausgabe`` schreiben (atomar, 0644) bzw. auf stdout.
    Liefert die Zahl der Pakete."""
    try:
        lock_bytes = lock.read_bytes()
        daten = json.loads(quellen.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise LizenzFehler(f"Eingaben nicht lesbar: {e}") from None
    text = erzeuge(lock_bytes, daten, cache)
    if ausgabe is None:
        sys.stdout.write(text)
    else:
        fd, tmp = tempfile.mkstemp(dir=ausgabe.parent, prefix=".lizenzen-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.chmod(tmp, 0o644)
            os.replace(tmp, ausgabe)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    return len(daten["pakete"])
