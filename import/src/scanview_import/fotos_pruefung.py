"""Prüfungen für orientierte Fotos.

`rueckprojektion`: Tie-Point-Pixel der Ausrichtung (Originalbild) gegen die in images.json
gespeicherte Pose und Lochkamera-Intrinsik. Geprüft werden Pose, Posen-Basis, CRS-Projektion des
Exports und Verzeichnungsmodell sowie Brennweite und Bildmitte der Lochkamera — gegen den Ausgleich
der Photogrammetrie-Software. Nicht geprüft: die Pixel der entzerrten Datei selbst (Zuschnitt und
Neuabtastung sichern die Tests der Entzerrung). Stichprobe: je Gruppe höchstens 50 Kameras mit je
bis zu 200 Projektionen (Kameraexport); Kameras außerhalb der Stichprobe werden nicht einzeln
geprüft.
Grenze: Median je geprüftem Foto ≤ 2 px (volle entzerrte Auflösung); anhebbar bis 10 px
(`--rueckprojektion-grenze`), die Fotos über 2 px stehen dann in `aboveDefault`.

`pruefe_fotos_paket`: nur das Paket — Dateien vorhanden, Größen stimmen, keine Metadaten.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation

from .fotos import FOTOS_DIR, IMAGES_FILE, IMAGES_SCHEMA_VERSION, kachel_raster, revision
from .kameras import FrameExport, TieProjection
from .verzeichnung import unverzeichnen

GRENZE_PX = 2.0
# Obergrenze für `grenze_px`: Fehler der Kette (Pose, CRS, Modell) liegen bei vielen Pixeln;
# darüber prüfte die Rückprojektion nichts mehr.
GRENZE_PX_MAX = 10.0
MIN_PROJEKTIONEN = 20
# APP1 (EXIF/XMP), APP2 (ICC), APP13 (IPTC/Photoshop), COM (Kommentar)
METADATEN_MARKER = {0xE1, 0xE2, 0xED, 0xFE}


def _paket_rotation(quat_wxyz: list[float]) -> np.ndarray:
    w, x, y, z = quat_wxyz
    matrix: np.ndarray = Rotation.from_quat([x, y, z, w]).as_matrix()
    return matrix


def rueckprojektion(
    images: dict[str, Any],
    export: FrameExport,
    projektionen: list[TieProjection],
    *,
    grenze_px: float = GRENZE_PX,
) -> dict[str, Any]:
    if not 0 < grenze_px <= GRENZE_PX_MAX:
        raise ValueError(f"Rückprojektionsgrenze muss in (0, {GRENZE_PX_MAX}] px liegen")
    labels = [c.label for c in export.cameras]
    if len(set(labels)) != len(labels):
        doppelt = sorted({x for x in labels if labels.count(x) > 1})
        raise ValueError(
            f"Rückprojektion: Kameralabels nicht eindeutig ({', '.join(doppelt)}) — "
            "Tie-Point-Projektionen werden über das Label zugeordnet"
        )
    kameras = {c.label: c for c in export.cameras}
    eintraege = {e["label"]: e for e in images["images"]}
    passend = [p for p in projektionen if p.camera in eintraege]
    if len(passend) < MIN_PROJEKTIONEN:
        raise ValueError(
            f"Rückprojektion: zu wenige Tie-Point-Projektionen ({len(passend)} < "
            f"{MIN_PROJEKTIONEN}) — Export ohne Projektionen oder Gruppen ohne Stichprobe"
        )
    fehler: dict[str, list[float]] = {}
    rest: list[tuple[float, float]] = []
    for p in passend:
        e = eintraege[p.camera]
        k = export.sensors[kameras[p.camera].sensor_id].kalibrierung
        intr = e["intrinsics"]
        r_austausch = _paket_rotation(e["pose"]["rotationQuat"]) @ np.diag([1.0, -1.0, -1.0])
        lokal = r_austausch.T @ (np.array(p.world) - np.array(e["pose"]["translation"]))
        if lokal[2] <= 0:
            fehler.setdefault(e["id"], []).append(float("inf"))
            continue
        u = intr["width"] / 2 + lokal[0] / lokal[2] * intr["focalPx"]
        v = intr["height"] / 2 + lokal[1] / lokal[2] * intr["focalPx"]
        xm, ym = unverzeichnen(k, np.array([p.pixel[0]]), np.array([p.pixel[1]]))
        um = intr["width"] / 2 + float(xm[0]) * intr["focalPx"]
        vm = intr["height"] / 2 + float(ym[0]) * intr["focalPx"]
        fehler.setdefault(e["id"], []).append(float(np.hypot(u - um, v - vm)))
        rest.append((um - u, vm - v))
    mediane = {i: float(np.median(f)) for i, f in fehler.items()}
    je_bild = [{"id": i, "n": len(fehler[i]), "medianPx": mediane[i]} for i in sorted(fehler)]
    alle = np.concatenate([np.array(f) for f in fehler.values()])
    return {
        "limitPx": grenze_px,
        "defaultLimitPx": GRENZE_PX,
        "n": len(alle),
        "medianPx": float(np.median(alle)),
        "p95Px": float(np.percentile(alle, 95)),
        # Beobachtet minus berechnet, Median je Achse: systematischer Versatz (Pixelkonvention,
        # Modell). Median statt Mittel: SfM-Beobachtungen enthalten Ausreißer (OpenSfM).
        "medianResidualPx": [float(x) for x in np.median(rest, axis=0)] if rest else [0.0, 0.0],
        "perImage": je_bild,
        # Mit angehobener Grenze bleibt sichtbar, welche Fotos die Standardgrenze reißen.
        "aboveDefault": [b for b in je_bild if mediane[str(b["id"])] > GRENZE_PX],
        "ok": all(m <= grenze_px for m in mediane.values()),
    }


def jpeg_marker(data: bytes) -> list[int]:
    """Marker eines JPEG bis zum Bildbeginn (SOS)."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("kein JPEG")
    pos, marker = 2, []
    while pos + 4 <= len(data):
        m = data[pos + 1]
        marker.append(m)
        if m == 0xDA:
            break
        pos += 2 + int.from_bytes(data[pos + 2 : pos + 4], "big")
    return marker


def _datei(pfad: Path, groesse: tuple[int, int], fehler: list[str]) -> None:
    if not pfad.is_file():
        fehler.append(f"{pfad.name} fehlt ({pfad.parent.name})")
        return
    daten = pfad.read_bytes()
    try:
        if METADATEN_MARKER & set(jpeg_marker(daten)):
            fehler.append(f"{pfad}: Metadaten im JPEG")
        with Image.open(pfad) as im:
            if im.size != groesse:
                fehler.append(f"{pfad}: Größe {im.size} statt {groesse}")
    except (OSError, ValueError) as exc:
        fehler.append(f"{pfad}: nicht lesbar ({exc})")


def _stufengroesse(breite: int, hoehe: int, stufe: int) -> tuple[int, int]:
    if max(breite, hoehe) == stufe:
        return breite, hoehe
    faktor = stufe / max(breite, hoehe)
    return max(1, round(breite * faktor)), max(1, round(hoehe * faktor))


def pruefe_fotos_paket(pkg: Path) -> dict[str, Any]:
    try:
        images = json.loads((pkg / IMAGES_FILE).read_text(encoding="utf-8"))
        if images["schemaVersion"] != IMAGES_SCHEMA_VERSION:
            raise ValueError(f"unbekannte schemaVersion {images['schemaVersion']}")
        eintraege = images["images"]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{IMAGES_FILE} ungültig ({exc})") from exc
    fehler: list[str] = []
    dateien = 0
    for e in eintraege:
        ordner = pkg / FOTOS_DIR / e["id"]
        if "rev" in e:  # ab M7; ältere Pakete liegen direkt unter fotos/<id>/
            rev = e["rev"]
            ordner = ordner / str(rev)
            if not ordner.is_dir():
                fehler.append(f"{e['id']}: Revisionsordner {rev} fehlt")
            elif revision(ordner) != rev:
                fehler.append(
                    f"{e['id']}: Revision {rev} passt nicht zum Inhalt (Browser-Cache "
                    "zeigte veraltete Bilder) — Fotos neu schreiben"
                )
        b, h = e["intrinsics"]["width"], e["intrinsics"]["height"]
        for stufe in e["levels"]:
            _datei(ordner / f"{stufe}.jpg", _stufengroesse(b, h, stufe), fehler)
            dateien += 1
        kacheln = e.get("tiles")
        if kacheln:
            for z, x, y, (x0, y0, x1, y1) in kachel_raster(
                b, h, kacheln["size"], bis_kante=e["levels"][-1]
            ):
                _datei(ordner / "tiles" / str(z) / f"{x}_{y}.jpg", (x1 - x0, y1 - y0), fehler)
                dateien += 1
    return {"ok": not fehler, "bilder": len(eintraege), "dateien": dateien, "fehler": fehler}
