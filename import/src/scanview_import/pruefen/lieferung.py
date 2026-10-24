"""Lieferung: Dateien eines fremden Ordners sicher erfassen, hashen und ihre Kopfdaten lesen.

Nur gelesen, nie verändert. Symbolische Links werden nicht verfolgt, sondern als Befund geführt;
Pfade aus Katalog oder Prüfsummenliste müssen im Lieferordner bleiben (``sicher``).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import laspy
import pye57

from ..manifest_io import read_manifest

if TYPE_CHECKING:
    from .flaeche import Auftragsflaeche

# Dateiart nach Endung (klein geschrieben); was hier fehlt, zählt als „sonstige“.
ARTEN = {
    ".las": "las",
    ".laz": "laz",
    ".e57": "e57",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".glb": "gltf",
    ".gltf": "gltf",
    ".obj": "obj",
    ".ply": "ply",
    ".pdf": "pdf",
    ".csv": "csv",
    ".geojson": "geojson",
    ".rcp": "rcp",
    ".rcs": "rcp",
    ".dwg": "dwg",
    ".dxf": "dxf",
    ".ifc": "ifc",
}


# Was ein Leser bei einer kaputten fremden Datei werfen kann; jeder dieser Fehler ist ein Befund.
LESEFEHLER = (
    OSError,
    ValueError,
    RuntimeError,
    KeyError,
    IndexError,
    TypeError,
    struct.error,
    laspy.errors.LaspyException,
    pye57.libe57.E57Exception,
)


def art(rel: str) -> str:
    return ARTEN.get(PurePosixPath(rel).suffix.lower(), "sonstige")


def _sha256_datei(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while block := f.read(1 << 22):
            h.update(block)
    return h.hexdigest()


@dataclass
class Lieferung:
    wurzel: Path
    dateien: dict[str, Path]
    links: list[str]
    paket: dict[str, Any] | None = None
    paket_rel: str | None = None
    paket_pfad: Path | None = None
    flaeche: Auftragsflaeche | None = None
    # Kontrollpunktliste des Auftraggebers (--kontrollpunkte) und ihr erklärtes Bezugssystem
    kontrollpunkte: tuple[Path, str | None] | None = None
    # Referenz-DGM des Auftraggebers (--dgm), dgm.Referenz
    dgm: Any = None
    _hashes: dict[str, str] = field(default_factory=dict)
    gelesen: set[str] = field(default_factory=set)
    # Eingaben außerhalb der Lieferung (Auftragsfläche, externes Paket): Bezeichnung → SHA-256
    extern: dict[str, str] = field(default_factory=dict)
    cache: dict[Any, Any] = field(default_factory=dict)

    def paket_datei(self, name: str) -> Path:
        """Datei des siteBits-Pakets; liegt das Paket außerhalb, wird sie für die Kette gehasht."""
        if self.paket_pfad is None:
            raise ValueError("kein siteBits-Paket angegeben")
        if self.paket_rel is not None:
            return self.pfad(f"{self.paket_rel}/{name}" if self.paket_rel else name)
        p = self.paket_pfad / name
        self.extern[f"Paket {p.parent.name}/{name}"] = _sha256_datei(p)
        return p

    def pfad(self, rel: str) -> Path:
        """Pfad einer Lieferdatei; merkt sie für die Prüfsummen der Kette vor."""
        self.gelesen.add(rel)
        return self.dateien[rel]

    def finde(self, muster: str | list[str]) -> list[str]:
        """Relative Pfade, deren Pfad oder Name auf ein Muster passt (Groß/klein egal)."""
        liste = [muster] if isinstance(muster, str) else muster
        treffer = []
        for rel in self.dateien:
            klein = rel.lower()
            name = PurePosixPath(klein).name
            if any(
                fnmatch.fnmatchcase(klein, m.lower()) or fnmatch.fnmatchcase(name, m.lower())
                for m in liste
            ):
                treffer.append(rel)
        return sorted(treffer)

    def von_art(self, *arten: str) -> list[str]:
        return sorted(r for r in self.dateien if art(r) in arten)

    def sha256(self, rel: str) -> str:
        if rel not in self._hashes:
            self._hashes[rel] = _sha256_datei(self.pfad(rel))
        return self._hashes[rel]

    def gehasht(self) -> dict[str, str]:
        return dict(sorted(self._hashes.items()))

    def im_paket(self, rel: str) -> bool:
        return self.paket_rel is not None and (
            self.paket_rel == "" or rel.startswith(self.paket_rel + "/")
        )

    def sicher(self, rel: str) -> str | None:
        """Normierter relativer Pfad, wenn er im Lieferordner bleibt, sonst None."""
        p = PurePosixPath(rel.replace("\\", "/"))
        if p.is_absolute() or ".." in p.parts or not p.parts or ":" in p.parts[0]:
            return None
        if not (self.wurzel / p).resolve().is_relative_to(self.wurzel):
            return None
        return p.as_posix()


def erfasse(wurzel: Path, paket: Path | None = None) -> Lieferung:
    wurzel = wurzel.resolve()
    if not wurzel.is_dir():
        raise FileNotFoundError(f"Lieferordner fehlt: {wurzel}")
    dateien: dict[str, Path] = {}
    links: list[str] = []
    for ordner, unter, namen in os.walk(wurzel, followlinks=False):
        o = Path(ordner)
        for u in list(unter):
            if (o / u).is_symlink():
                links.append((o / u).relative_to(wurzel).as_posix())
                unter.remove(u)
        for n in namen:
            p = o / n
            rel = p.relative_to(wurzel).as_posix()
            if p.is_symlink():
                links.append(rel)
            elif p.is_file():
                dateien[rel] = p
    lf = Lieferung(wurzel=wurzel, dateien=dict(sorted(dateien.items())), links=sorted(links))
    if paket is not None:
        pr = paket.resolve()
        lf.paket_pfad = pr
        lf.paket = read_manifest(pr)
        lf.paket_rel = pr.relative_to(wurzel).as_posix() if pr.is_relative_to(wurzel) else None
        if lf.paket_rel == ".":
            lf.paket_rel = ""
        lf.paket_datei("project.json")
    return lf


def kopf_las(pfad: Path) -> dict[str, Any]:
    with laspy.open(pfad) as f:
        h = f.header
        fehler: str | None = None
        try:
            crs = h.parse_crs()
        except LESEFEHLER as e:
            crs, fehler = None, str(e)
        return {
            "version": f"{h.version.major}.{h.version.minor}",
            "punktformat": int(h.point_format.id),
            "punkte": int(h.point_count),
            "crs": crs,
            "crs_fehler": fehler,
        }


def kopf_e57(pfad: Path) -> dict[str, Any]:
    e = pye57.E57(str(pfad))
    try:
        root = e.image_file.root()

        def wert(k: str) -> Any:
            return root[k].value() if root.isDefined(k) else None

        return {
            "version": f"{wert('versionMajor')}.{wert('versionMinor')}",
            "scans": int(root["data3D"].childCount()),
            "koordinaten": str(wert("coordinateMetadata") or ""),
        }
    finally:
        e.close()


def kopf_gltf(pfad: Path) -> dict[str, Any]:
    """Version und Mesh-Anzahl aus dem JSON-Teil, ohne den Binärteil zu laden."""
    with pfad.open("rb") as f:
        if pfad.suffix.lower() == ".glb":
            kopf = f.read(20)
            magie, version, _laenge, json_laenge, typ = struct.unpack("<4sIIII", kopf)
            if magie != b"glTF" or typ != 0x4E4F534A:
                raise ValueError("keine GLB-Kennung")
            d = json.loads(f.read(json_laenge))
        else:
            d = json.load(f)
            version = 0
    asset = d.get("asset", {})
    if "version" not in asset:
        raise ValueError("glTF ohne asset.version")
    return {"version": asset["version"], "container": version, "meshes": len(d.get("meshes", []))}


def lesbar(lf: Lieferung, rel: str) -> tuple[bool, str]:
    """Kann die Datei als ihre Art gelesen werden? (ok, Beleg oder Grund)"""
    p, a = lf.pfad(rel), art(rel)
    try:
        if a in ("las", "laz"):
            k = kopf_las(p)
            return True, f"LAS {k['version']}, Punktformat {k['punktformat']}, {k['punkte']} Punkte"
        if a == "e57":
            k = kopf_e57(p)
            return True, f"E57 {k['version']}, {k['scans']} Scans"
        if a == "gltf":
            k = kopf_gltf(p)
            return True, f"glTF {k['version']}, {k['meshes']} Meshes"
        if a == "obj":
            with p.open("rb") as f:
                obj = f.read(1 << 20)
            if b"\nv " in b"\n" + obj:
                return True, "OBJ mit Eckpunkten"
            return False, "OBJ ohne Eckpunkte im Kopf"
        if a == "pdf":
            with p.open("rb") as f:
                ok = f.read(5) == b"%PDF-"
            return ok, "PDF-Kennung vorhanden" if ok else "keine PDF-Kennung"
        if a == "csv":
            with p.open(encoding="utf-8-sig") as f:
                kopf = f.readline().strip()
            return bool(kopf), f"CSV-Kopf: {kopf[:80]}" if kopf else "CSV leer"
        if a == "geojson":
            d = json.loads(p.read_text(encoding="utf-8"))
            ok = isinstance(d, dict) and d.get("type") in ("FeatureCollection", "Feature")
            return ok, f"GeoJSON {d.get('type')}" if ok else "kein GeoJSON-Objekt"
        if a == "tiff":
            from .raster import geotiff

            g = geotiff(p)
            return True, f"TIFF {g['breite']}\u00d7{g['hoehe']} px"
    except LESEFEHLER as e:
        return False, f"nicht lesbar: {type(e).__name__}: {str(e)[:160]}"
    return True, "nur vorhanden (kein freier Leser, Inhalt nicht geprüft)"
