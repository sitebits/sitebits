"""Kugelpanoramen aus einem geprüften Anonymisierungslauf übernehmen (F20).

Eingabe ist das Manifest des Anonymisierungswerkzeugs (`manifest.json` neben `entscheidungen/`):

    {"projekt": …, "originale_unveraendert": true,
     "panos": [{"pano": "<id>", "quelle": "<pfad>", "sha256": "<quelle>", "revision": n,
                "aktion": "geschwaerzt" | "unveraendert", "ausgabe": "<pfad>" | null,
                "verifiziert": true, "ausgabe_sha256": "<optional>"}]}

Der Import prüft die Bedingungen des Werkzeugs selbst nach, statt ihm zu glauben:
- jede Kamera aus `kameras.json` hat genau einen Manifesteintrag mit dem SHA-256 ihrer Quelle
  (Zuordnung über den Inhalt, nicht über Pfadtexte), die Quelle ist seit der Prüfung unverändert;
- die gespeicherte Entscheidung (`entscheidungen/<pano>.json`) ist `fertig`, gleiche Revision,
  gleicher Quell-Hash;
- `geschwaerzt` nur mit `verifiziert: true` und einer Ausgabe, die eine reguläre Datei (kein
  Symlink) mit den Maßen der Quelle ist; `unveraendert` verwendet die Quelle selbst;
- nichts anderes als diese beiden Aktionen.

Verwendet werden nur die Ausgaben. Weil das Manifest (Stand heute) keinen Hash der Ausgabe
trägt, hält der Import ihn beim Prüfen fest und vergleicht nach dem Schreiben erneut — ein
Austausch während des Imports bricht ab. Trägt ein Eintrag `ausgabe_sha256`, muss er passen.
In `stations.json` schreibt der Import danach `anonymization` mit Werkzeug, Manifest-SHA-256,
Status `reviewed` und dem SHA-256 jeder geschriebenen Stufendatei — daran bindet der Server die
Freigabe.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WERKZEUG = "pano-anonym"
AKTIONEN = ("geschwaerzt", "unveraendert")
MANIFEST_MAX = 16 * 1024 * 1024


def sha256_datei(pfad: Path) -> str:
    h = hashlib.sha256()
    with pfad.open("rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


@dataclass(frozen=True)
class Uebernahme:
    """Je Kamera-Quelle: welche Datei die Pixel liefert und mit welchem Hash."""

    pano: str
    quelle: Path
    quelle_sha256: str
    aktion: str
    pixel: Path
    pixel_sha256: str


@dataclass(frozen=True)
class AnonManifest:
    pfad: Path
    sha256: str
    eintraege: dict[str, list[dict[str, Any]]]  # Quell-SHA-256 → Einträge
    entscheidungen: Path


def lade(pfad: Path) -> AnonManifest:
    if pfad.is_symlink() or not pfad.is_file():
        raise ValueError("Anonymisierungsmanifest fehlt oder ist keine reguläre Datei")
    roh = pfad.read_bytes()
    if len(roh) > MANIFEST_MAX:
        raise ValueError("Anonymisierungsmanifest zu groß")
    try:
        d = json.loads(roh)
    except ValueError as exc:
        raise ValueError(f"Anonymisierungsmanifest nicht lesbar ({exc})") from exc
    if not isinstance(d, dict) or not isinstance(d.get("panos"), list):
        raise ValueError("Anonymisierungsmanifest ohne Liste 'panos'")
    if d.get("originale_unveraendert") is not True:
        raise ValueError("Anonymisierungsmanifest bestätigt unveränderte Originale nicht")
    eintraege: dict[str, list[dict[str, Any]]] = {}
    for e in d["panos"]:
        if (not isinstance(e, dict) or not isinstance(e.get("pano"), str)
                or not isinstance(e.get("sha256"), str) or len(e["sha256"]) != 64):
            raise ValueError("Anonymisierungsmanifest: Eintrag ohne pano/sha256")
        eintraege.setdefault(e["sha256"], []).append(e)
    entscheidungen = pfad.parent / "entscheidungen"
    if not entscheidungen.is_dir():
        raise ValueError("Ordner 'entscheidungen' neben dem Manifest fehlt")
    return AnonManifest(pfad=pfad, sha256=hashlib.sha256(roh).hexdigest(), eintraege=eintraege,
                        entscheidungen=entscheidungen)


def _entscheidung_pruefen(m: AnonManifest, e: dict[str, Any]) -> None:
    pano = e["pano"]
    if "/" in pano or "\\" in pano or pano.startswith("."):
        raise ValueError(f"{pano}: unzulässige Pano-ID")
    datei = m.entscheidungen / f"{pano}.json"
    try:
        d = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{pano}: Entscheidung fehlt oder ist nicht lesbar") from exc
    quelle = d.get("quelle") if isinstance(d, dict) else None
    if not isinstance(d, dict) or d.get("status") != "fertig":
        raise ValueError(f"{pano}: nicht fertig geprüft")
    if d.get("revision") != e.get("revision"):
        raise ValueError(f"{pano}: nach dem Anwenden geändert — Manifest veraltet")
    if not isinstance(quelle, dict) or quelle.get("sha256") != e["sha256"]:
        raise ValueError(f"{pano}: Entscheidung gilt für eine andere Quelle")


def zuordnen(m: AnonManifest, kameras: list[tuple[str, Path, int, int]]) -> list[Uebernahme]:
    """`kameras`: (Bezeichnung, Quellpfad, Breite, Höhe) je Kugelkamera."""
    from .kameras import _panorama_oeffnen  # spät: kameras importiert dieses Modul

    out: list[Uebernahme] = []
    for label, quelle, breite, hoehe in kameras:
        sha = sha256_datei(quelle)
        treffer = m.eintraege.get(sha, [])
        if len(treffer) != 1:
            raise ValueError(f"{label}: Quelle {'fehlt im' if not treffer else 'mehrfach im'} "
                             "Anonymisierungsmanifest (oder wurde nach der Prüfung verändert)")
        e = treffer[0]
        _entscheidung_pruefen(m, e)
        aktion = e.get("aktion")
        if aktion not in AKTIONEN:
            raise ValueError(f"{label}: unbekannte Aktion {aktion!r}")
        if aktion == "unveraendert":
            pixel, pixel_sha = quelle, sha
        else:
            if e.get("verifiziert") is not True:
                raise ValueError(f"{label}: Ausgabe nicht verifiziert")
            ausgabe = e.get("ausgabe")
            if not isinstance(ausgabe, str) or not ausgabe:
                raise ValueError(f"{label}: geschwärzt, aber ohne Ausgabe")
            pixel = Path(ausgabe)
            if not pixel.is_absolute():
                pixel = m.pfad.parent / pixel
            if pixel.is_symlink() or not pixel.is_file():
                raise ValueError(f"{label}: Ausgabe fehlt oder ist keine reguläre Datei")
            pixel_sha = sha256_datei(pixel)
            erwartet = e.get("ausgabe_sha256")
            if erwartet is not None and erwartet != pixel_sha:
                raise ValueError(f"{label}: Ausgabe weicht vom Manifest ab")
            with _panorama_oeffnen(pixel) as bild:
                if bild.size != (breite, hoehe):
                    raise ValueError(f"{label}: Ausgabe hat andere Maße als die Quelle")
        out.append(Uebernahme(e["pano"], quelle, sha, aktion, pixel, pixel_sha))
    return out


def nachpruefen(uebernahmen: list[Uebernahme]) -> None:
    """Nach dem Schreiben: Quellen und Ausgaben haben sich während des Imports nicht geändert."""
    for u in uebernahmen:
        if sha256_datei(u.quelle) != u.quelle_sha256:
            raise ValueError(f"{u.pano}: Quelle während des Imports verändert")
        if sha256_datei(u.pixel) != u.pixel_sha256:
            raise ValueError(f"{u.pano}: Ausgabe während des Imports verändert")
