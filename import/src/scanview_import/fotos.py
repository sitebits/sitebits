"""Orientierte Fotos: entzerren, Stufen und Kacheln schreiben, images.json, Ebene `fotos`.

Jedes Foto wird einmal auf eine Lochkamera entzerrt (`verzeichnung.py`), optional durch den
Anonymisierungs-Hook geschickt und erst danach in Stufen (lange Kante 512/2048/4096) und bei
voller Auflösung zusätzlich in Kacheln zerlegt. Jede Ausgabedatei wird aus Pixeln neu kodiert
(JPEG q85), Metadaten der Quelle (EXIF, GPS, XMP, ICC) gelangen nie ins Paket.

Posen: Rahmenkameras kommen in Metashape-Basis (X rechts, Y unten, Z vorn); das Paket nutzt
wie `stations.json` +X rechts, +Y oben, -Z Blick: R_paket = R_metashape @ diag(1, -1, -1).

Schreiben ist atomar je Lauf: Alles entsteht in `.fotos-neu/`; erst wenn jedes Foto fertig ist,
wird es gegen `fotos/` getauscht und `images.json` + Manifest geschrieben. Arbeitsordner beginnen
mit einem Punkt (`.fotos-neu/`, `.fotos-alt/`): Bleiben sie nach einem harten Abbruch liegen,
liefert sie weder `/api/authz` (Segment mit Punkt) noch Caddy (`hide .*`) aus.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from scipy.spatial.transform import Rotation

from .manifest_io import read_manifest, set_images, write_manifest
from .metashape import FrameCamera, FrameExport
from .verzeichnung import Kalibrierung, Lochkamera, entzerre_bild, remap_gitter, zielkamera

STUFEN = (512, 2048, 4096)
KACHEL = 1024
JPEG_QUALITAET = 85
FOTOS_DIR = "fotos"
IMAGES_FILE = "images.json"
IMAGES_SCHEMA_VERSION = 1
EXIF_ORIENTATION = 0x0112
# 36,6-MP-Drohnenbilder liegen unter Pillows Schutzgrenze; größere Rahmenbilder sind kein Fall
# für diese Scheibe und brechen mit Pillows eigener Meldung ab.

Anonymize = Callable[[NDArray[np.uint8], FrameCamera], NDArray[np.uint8]]


def stufen_fuer(lang: int, max_kante: int | None, *, stufen: Sequence[int] = STUFEN) -> list[int]:
    """Stufen als lange Kante; nie größer als das Bild oder `max_kante`."""
    grenze = stufen[-1] if max_kante is None else min(max_kante, stufen[-1])
    oben = min(lang, grenze)
    return [s for s in stufen if s < oben] + [oben]


def kachel_raster(
    breite: int, hoehe: int, groesse: int, *, bis_kante: int
) -> list[tuple[int, int, int, tuple[int, int, int, int]]]:
    """Kacheln je Ebene z (z = 0 volle Auflösung, je Ebene halbiert, bis lange Kante ≤ bis_kante).

    Liefert (z, x, y, (x0, y0, x1, y1)) in Pixeln der Ebene z.
    """
    raster = []
    z = 0
    while True:
        b = math.ceil(breite / 2**z)
        h = math.ceil(hoehe / 2**z)
        for y in range(math.ceil(h / groesse)):
            for x in range(math.ceil(b / groesse)):
                box = (
                    x * groesse,
                    y * groesse,
                    min(b, (x + 1) * groesse),
                    min(h, (y + 1) * groesse),
                )
                raster.append((z, x, y, box))
        if max(b, h) <= bis_kante:
            return raster
        z += 1


@lru_cache(maxsize=4)
def _gitter(k: Kalibrierung, ziel: Lochkamera) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
    # Je Prozess und Sensor einmal: das Gitter für 36,6 MP kostet 1,5 s und knapp 300 MB.
    return remap_gitter(k, ziel)


@dataclass(frozen=True)
class _Auftrag:
    foto_id: str
    kamera: FrameCamera
    kalibrierung: Kalibrierung
    ziel: Lochkamera
    stufen: tuple[int, ...]
    kachel: int | None
    ordner: Path
    anonymize: Anonymize | None


def _speichern(bild: Image.Image, pfad: Path) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    # Neues Bild aus Pixeln: kein exif, kein icc_profile, keine Kommentare.
    Image.fromarray(np.asarray(bild)).save(pfad, "JPEG", quality=JPEG_QUALITAET)


def _skaliert(bild: Image.Image, lang: int) -> Image.Image:
    b, h = bild.size
    if max(b, h) == lang:
        return bild
    faktor = lang / max(b, h)
    groesse = (max(1, round(b * faktor)), max(1, round(h * faktor)))
    return bild.resize(groesse, Image.Resampling.LANCZOS)


def _foto(auftrag: _Auftrag) -> None:
    kamera = auftrag.kamera
    with Image.open(kamera.image_path) as quelle:
        orientation = quelle.getexif().get(EXIF_ORIENTATION, 1)
        if orientation != kamera.exif_orientation:
            raise ValueError(
                f"{kamera.label}: EXIF-Orientation {orientation} — gedrehte Bilder werden nicht "
                "geraten; Metashape rechnet in Sensorpixeln"
            )
        pixel = np.asarray(quelle.convert("RGB"))
    bild = entzerre_bild(pixel, _gitter(auftrag.kalibrierung, auftrag.ziel))
    if auftrag.anonymize is not None:
        neu = auftrag.anonymize(bild, kamera)
        if neu.shape != bild.shape or neu.dtype != np.uint8:
            raise ValueError(f"{kamera.label}: Anonymisierung lieferte ein anderes Bildformat")
        bild = neu
    voll = Image.fromarray(bild)
    del pixel, bild
    for stufe in auftrag.stufen:
        _speichern(_skaliert(voll, stufe), auftrag.ordner / f"{stufe}.jpg")
    if auftrag.kachel is not None:
        ebene = voll
        for z, x, y, box in kachel_raster(*voll.size, auftrag.kachel, bis_kante=auftrag.stufen[-1]):
            groesse = (math.ceil(voll.width / 2**z), math.ceil(voll.height / 2**z))
            if ebene.size != groesse:
                ebene = voll.resize(groesse, Image.Resampling.LANCZOS)
            _speichern(ebene.crop(box), auftrag.ordner / "tiles" / str(z) / f"{x}_{y}.jpg")


def _quaternion(rotation: np.ndarray) -> list[float]:
    paket = rotation @ np.diag([1.0, -1.0, -1.0])
    x, y, z, w = Rotation.from_matrix(paket).as_quat()
    return [float(w), float(x), float(y), float(z)]


def revision(ordner: Path) -> str:
    """Inhalts-Revision eines Fotos: SHA-256 über relative Pfade und Bytes aller Dateien, 16 Hex.

    Steht in `images.json` (`rev`) und im Pfad (`fotos/<id>/<rev>/…`): Neuer Inhalt — anderes Foto
    unter derselben fortlaufenden ID, nachträglich anonymisiert — bekommt eine neue URL. Sonst
    zeigte der Browser bis zum Ablauf seines Caches (`max-age=3600`) das alte Bild (Minor M7).
    """
    h = hashlib.sha256()
    for datei in sorted(p for p in ordner.rglob("*") if p.is_file()):
        rel = datei.relative_to(ordner).as_posix().encode()
        h.update(len(rel).to_bytes(4, "big") + rel)
        daten = datei.read_bytes()
        h.update(len(daten).to_bytes(8, "big") + daten)
    return h.hexdigest()[:16]


def plane_fotos(
    pkg: Path,
    export: FrameExport,
    *,
    max_kante: int | None,
    kachel: int = KACHEL,
    stufen: Sequence[int] = STUFEN,
    anonymize: Anonymize | None = None,
) -> tuple[list[_Auftrag], list[dict[str, Any]]]:
    """IDs, Posen, Intrinsik, Stufen und Kacheln festlegen, ohne ein Pixel zu lesen."""
    kameras = sorted(export.cameras, key=lambda c: (c.group, c.label))
    neu = pkg / f".{FOTOS_DIR}-neu"
    auftraege, eintraege = [], []
    for n, kamera in enumerate(kameras, start=1):
        foto_id = f"F{n:04d}"
        k = export.sensors[kamera.sensor_id].kalibrierung
        ziel = zielkamera(k)
        lang = max(ziel.width, ziel.height)
        eigene = tuple(stufen_fuer(lang, max_kante, stufen=stufen))
        mit_kacheln = max_kante is None and lang > eigene[-1]
        auftraege.append(
            _Auftrag(
                foto_id,
                kamera,
                k,
                ziel,
                eigene,
                kachel if mit_kacheln else None,
                neu / foto_id,
                anonymize,
            )
        )
        tiles = None
        if mit_kacheln:
            ebenen = 1 + max(
                z
                for z, *_ in kachel_raster(ziel.width, ziel.height, kachel, bis_kante=eigene[-1])
            )
            tiles = {"size": kachel, "levels": ebenen, "width": ziel.width, "height": ziel.height}
        eintraege.append(
            {
                "id": foto_id,
                "label": kamera.label,
                "group": kamera.group,
                "pose": {
                    "translation": list(kamera.position),
                    "rotationQuat": _quaternion(kamera.rotation),
                },
                "intrinsics": {"focalPx": ziel.f, "width": ziel.width, "height": ziel.height},
                "levels": list(eigene),
                "tiles": tiles,
            }
        )
    return auftraege, eintraege


def write_images(
    pkg: Path,
    export: FrameExport,
    *,
    max_kante: int | None,
    kachel: int = KACHEL,
    stufen: Sequence[int] = STUFEN,
    prozesse: int = 1,
    anonymize: Anonymize | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Alle Rahmenkameras von `export` als Ebene `fotos` schreiben; `max_kante=None` = voll."""
    kameras = sorted(export.cameras, key=lambda c: (c.group, c.label))
    neu = pkg / f".{FOTOS_DIR}-neu"
    if neu.exists():
        shutil.rmtree(neu)
    auftraege, eintraege = plane_fotos(
        pkg, export, max_kante=max_kante, kachel=kachel, stufen=stufen, anonymize=anonymize
    )
    try:
        if prozesse > 1:
            with ProcessPoolExecutor(max_workers=prozesse) as pool:
                list(pool.map(_foto, auftraege))
        else:
            for auftrag in auftraege:
                _foto(auftrag)
        for auftrag, eintrag in zip(auftraege, eintraege, strict=True):
            rev = revision(auftrag.ordner)
            zwischen = neu / f".{auftrag.foto_id}"
            auftrag.ordner.rename(zwischen)
            auftrag.ordner.mkdir()
            zwischen.rename(auftrag.ordner / rev)
            eintrag["rev"] = rev
    except BaseException:
        shutil.rmtree(neu, ignore_errors=True)
        raise
    gruppen: dict[str, int] = {}
    for kamera in kameras:
        gruppen[kamera.group] = gruppen.get(kamera.group, 0) + 1
    out = {
        "schemaVersion": IMAGES_SCHEMA_VERSION,
        "groups": [
            {"id": g, "title": g or "ohne Gruppe", "count": c} for g, c in sorted(gruppen.items())
        ],
        "anonymization": {"required": True, "status": "unreviewed"},
        "provenance": dict(provenance or {}, maxReferenceErrorPx=export.max_referenz_abweichung_px),
        "images": eintraege,
    }
    alt = pkg / f".{FOTOS_DIR}-alt"
    ziel_dir = pkg / FOTOS_DIR
    if alt.exists():
        shutil.rmtree(alt)
    if ziel_dir.exists():
        ziel_dir.rename(alt)
    neu.rename(ziel_dir)
    tmp = pkg / f".{IMAGES_FILE}-neu"
    tmp.write_text(json.dumps(out, indent=2, ensure_ascii=False, allow_nan=False))
    os.replace(tmp, pkg / IMAGES_FILE)
    m = read_manifest(pkg)
    set_images(m, IMAGES_FILE, len(eintraege))
    write_manifest(pkg, m)
    if alt.exists():
        shutil.rmtree(alt)
    return out
