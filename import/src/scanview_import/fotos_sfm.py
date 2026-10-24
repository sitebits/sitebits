"""Rahmenkameras aus COLMAP und OpenDroneMap (OpenSfM) als `FrameExport`.

Beide Werkzeuge rechnen in derselben Kamerabasis wie das Austauschformat (X rechts, Y unten,
Z Blick) und speichern die Pose als Welt → Kamera: ``X_kamera = R @ X_welt + t``. Daraus wird
die Form des Austauschformats (Spalten = Kameraachsen in Weltkoordinaten, Position =
Projektionszentrum): ``R_m = Rᵀ``, ``C = -Rᵀ t``.

Kameramodelle werden auf `verzeichnung.Kalibrierung` abgebildet, nie nach Namen geraten:
OpenCV/COLMAP/OpenSfM-brown führen die Tangentialterme in anderer Reihenfolge als das
Austauschformat (``P1 = p2``, ``P2 = p1``); ``fx ≠ fy`` wird zu ``f = fy``, ``b1 = fx - fy``.
Pixelkonvention aller drei Werkzeuge: Mitte des Pixels (i, j) bei (i + 0,5, j + 0,5). Referenzpaare
wie im Austauschformat gibt es nicht; den Beleg liefern die gemessenen Merkmalspositionen der
Rekonstruktion (Rückprojektion im Median, `modellbeleg`).

Georeferenz wird nicht geraten: COLMAP-Modelle müssen schon im Ziel-CRS liegen (`model_aligner`),
EPSG und Höhensystem gibt der Mensch an. ODM liefert EPSG und Versatz in `odm_georeferencing/`.
"""

from __future__ import annotations

import csv
import json
import re
import struct
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation

from .kameras import FrameCamera, FrameExport, FrameSensor, TieProjection
from .verzeichnung import Kalibrierung, VerzeichnungFehler, verzeichnen

# Je Foto so viele Beobachtungen für die Rückprojektion, gleichmäßig über die Liste verteilt.
BEOBACHTUNGEN_JE_FOTO = 100
# Tie Points für die Rahmenprüfung (gleichmäßig ausgedünnt).
TIE_POINTS_MAX = 20_000
MAX_BILDER = 100_000
MAX_BEOBACHTUNGEN = 10_000_000
MAX_PUNKTE = 50_000_000
# Modellbeleg: Median-Restvektor der Beobachtungen (ersetzt die Referenzpaare des Austauschformats).
RESTVEKTOR_GRENZE_PX = 0.1
MIN_BEOBACHTUNGEN = 20

# COLMAP-Modell-ID → (Name, Parameterzahl); nur die unterstützten Modelle.
COLMAP_MODELLE = {
    0: ("SIMPLE_PINHOLE", 3),
    1: ("PINHOLE", 4),
    2: ("SIMPLE_RADIAL", 4),
    3: ("RADIAL", 5),
    4: ("OPENCV", 8),
    6: ("FULL_OPENCV", 12),
}
_PARAMETERZAHL = dict(COLMAP_MODELLE.values())


def _opencv(
    width: int,
    height: int,
    fx: float,
    fy: float,
    cx: float,
    cy: float,
    k1: float = 0.0,
    k2: float = 0.0,
    p1: float = 0.0,
    p2: float = 0.0,
    k3: float = 0.0,
) -> Kalibrierung:
    """OpenCV-Modell (Hauptpunkt in Pixeln ab Bildecke) → Modell des Austauschformats."""
    return Kalibrierung(
        width=width,
        height=height,
        f=fy,
        b1=fx - fy,
        cx=cx - width / 2,
        cy=cy - height / 2,
        k1=k1,
        k2=k2,
        k3=k3,
        p1=p2,
        p2=p1,
    )


def colmap_kalibrierung(modell: str, width: int, height: int, p: list[float]) -> Kalibrierung:
    if modell not in _PARAMETERZAHL:
        raise VerzeichnungFehler(
            f"COLMAP-Kameramodell {modell} wird nicht unterstützt (nur SIMPLE_PINHOLE, PINHOLE, "
            "SIMPLE_RADIAL, RADIAL, OPENCV, FULL_OPENCV ohne k4 bis k6) — in COLMAP mit OPENCV "
            "neu rechnen"
        )
    if len(p) != _PARAMETERZAHL[modell]:
        raise VerzeichnungFehler(f"COLMAP {modell}: {len(p)} statt {_PARAMETERZAHL[modell]} Werte")
    if modell == "SIMPLE_PINHOLE":
        return _opencv(width, height, p[0], p[0], p[1], p[2])
    if modell == "PINHOLE":
        return _opencv(width, height, *p)
    if modell == "SIMPLE_RADIAL":
        return _opencv(width, height, p[0], p[0], p[1], p[2], k1=p[3])
    if modell == "RADIAL":
        return _opencv(width, height, p[0], p[0], p[1], p[2], k1=p[3], k2=p[4])
    if modell == "OPENCV":
        return _opencv(width, height, *p)
    if any(p[9:12]):
        raise VerzeichnungFehler(
            "COLMAP FULL_OPENCV mit k4 bis k6 ≠ 0 (rationales Modell) wird nicht unterstützt"
        )
    fx, fy, cx, cy, k1, k2, p1, p2, k3 = p[:9]
    return _opencv(width, height, fx, fy, cx, cy, k1, k2, p1, p2, k3)


def opensfm_kalibrierung(kamera: dict[str, Any]) -> Kalibrierung:
    """OpenSfM normiert mit max(w, h) und misst ab Bildmitte."""
    typ = kamera.get("projection_type")
    w, h = int(kamera["width"]), int(kamera["height"])
    size = max(w, h)
    if typ == "perspective":
        f = float(kamera["focal"]) * size
        return Kalibrierung(width=w, height=h, f=f, k1=float(kamera["k1"]), k2=float(kamera["k2"]))
    if typ == "brown":
        return _opencv(
            w,
            h,
            float(kamera["focal_x"]) * size,
            float(kamera["focal_y"]) * size,
            w / 2 + float(kamera["c_x"]) * size,
            h / 2 + float(kamera["c_y"]) * size,
            *(float(kamera[n]) for n in ("k1", "k2", "p1", "p2", "k3")),
        )
    raise VerzeichnungFehler(
        f"OpenSfM-Kameramodell {typ} wird nicht unterstützt (nur perspective, brown)"
    )


@dataclass(frozen=True)
class _Bild:
    name: str
    sensor: int
    drehung: np.ndarray  # Welt → Kamera
    t: np.ndarray
    # Beobachtungen: (u, v) in Pixeln (Mitte des ersten Pixels bei 0,5) und Punkt-ID
    pixel: np.ndarray
    punkt: np.ndarray


# --- COLMAP ------------------------------------------------------------------------------------


def _lies(f: BinaryIO, fmt: str) -> tuple[Any, ...]:
    n = struct.calcsize(fmt)
    data = f.read(n)
    if len(data) != n:
        raise ValueError("COLMAP-Binärdatei abgeschnitten")
    return struct.unpack(fmt, data)


def _anzahl(f: BinaryIO, grenze: int, was: str) -> int:
    n = int(_lies(f, "<Q")[0])
    if n > grenze:
        raise ValueError(f"COLMAP: zu viele {was} ({n})")
    return n


def _modellname(modell_id: int) -> str:
    if modell_id not in COLMAP_MODELLE:
        raise VerzeichnungFehler(
            f"COLMAP-Kameramodell-ID {modell_id} wird nicht unterstützt (Fisheye o. Ä.) — "
            "in COLMAP mit OPENCV neu rechnen"
        )
    return COLMAP_MODELLE[modell_id][0]


def _colmap_kameras(pfad: Path) -> dict[int, Kalibrierung]:
    out: dict[int, Kalibrierung] = {}
    if pfad.suffix == ".bin":
        with pfad.open("rb") as f:
            for _ in range(_anzahl(f, MAX_BILDER, "Kameras")):
                cid, mid, w, h = _lies(f, "<iiQQ")
                name = _modellname(mid)
                params = list(_lies(f, f"<{_PARAMETERZAHL[name]}d"))
                out[cid] = colmap_kalibrierung(name, int(w), int(h), params)
            if f.read(1):
                raise ValueError("COLMAP cameras.bin: zusätzliche Daten")
        return out
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        if zeile.startswith("#") or not zeile.strip():
            continue
        teile = zeile.split()
        cid = int(teile[0])
        if cid in out:
            raise ValueError(f"COLMAP cameras.txt: Kamera {cid} doppelt")
        out[cid] = colmap_kalibrierung(
            teile[1], int(teile[2]), int(teile[3]), [float(x) for x in teile[4:]]
        )
    return out


def _pose(q_wxyz: list[float], t: list[float]) -> tuple[np.ndarray, np.ndarray]:
    q = np.array(q_wxyz, float)[[1, 2, 3, 0]]
    if not np.isfinite(q).all() or abs(np.linalg.norm(q) - 1) > 1e-5:
        raise ValueError("COLMAP-Quaternion ungültig")
    t_arr = np.array(t, float)
    if not np.isfinite(t_arr).all():
        raise ValueError("COLMAP-Translation ungültig")
    return Rotation.from_quat(q).as_matrix(), t_arr


def _colmap_bilder(pfad: Path) -> list[_Bild]:
    out: list[_Bild] = []
    if pfad.suffix == ".bin":
        typ = np.dtype([("x", "<f8"), ("y", "<f8"), ("id", "<i8")])
        with pfad.open("rb") as f:
            for _ in range(_anzahl(f, MAX_BILDER, "Bilder")):
                kopf = _lies(f, "<i4d3di")
                name = bytearray()
                while (b := f.read(1)) != b"\0":
                    if not b or len(name) > 4096:
                        raise ValueError("COLMAP images.bin: Bildname ungültig")
                    name.extend(b)
                n = _anzahl(f, MAX_BEOBACHTUNGEN, "Beobachtungen")
                daten = f.read(n * typ.itemsize)
                if len(daten) != n * typ.itemsize:
                    raise ValueError("COLMAP images.bin abgeschnitten")
                obs = np.frombuffer(daten, typ)
                r, t = _pose(list(kopf[1:5]), list(kopf[5:8]))
                out.append(
                    _Bild(
                        name.decode("utf-8"),
                        int(kopf[8]),
                        r,
                        t,
                        np.column_stack([obs["x"], obs["y"]]),
                        obs["id"].astype(np.int64),
                    )
                )
            if f.read(1):
                raise ValueError("COLMAP images.bin: zusätzliche Daten")
        return out
    with pfad.open(encoding="utf-8") as f:
        while zeile := f.readline():
            if zeile.startswith("#") or not zeile.strip():
                continue
            teile = zeile.rstrip("\n").split(maxsplit=9)
            if len(teile) != 10:
                raise ValueError("COLMAP images.txt: Bildzeile ungültig")
            beob = f.readline()
            if beob == "":
                raise ValueError("COLMAP images.txt: Beobachtungszeile fehlt")
            werte = beob.split()
            if len(werte) % 3:
                raise ValueError(f"COLMAP images.txt: Beobachtungen von {teile[9]} ungültig")
            arr = np.array(werte, float).reshape(-1, 3)
            r, t = _pose([float(x) for x in teile[1:5]], [float(x) for x in teile[5:8]])
            out.append(_Bild(teile[9], int(teile[8]), r, t, arr[:, :2], arr[:, 2].astype(np.int64)))
    return out


def _colmap_punkte(pfad: Path) -> tuple[np.ndarray, np.ndarray]:
    """(IDs, XYZ) aller 3D-Punkte."""
    ids: list[int] = []
    xyz: list[tuple[float, float, float]] = []
    if pfad.suffix == ".bin":
        with pfad.open("rb") as f:
            for _ in range(_anzahl(f, MAX_PUNKTE, "Punkte")):
                pid, x, y, z, _r, _g, _b, _err, laenge = _lies(f, "<Q3d3BdQ")
                if laenge > MAX_BILDER or len(f.read(8 * laenge)) != 8 * laenge:
                    raise ValueError("COLMAP points3D.bin: Spur ungültig")
                ids.append(pid)
                xyz.append((x, y, z))
            if f.read(1):
                raise ValueError("COLMAP points3D.bin: zusätzliche Daten")
    else:
        with pfad.open(encoding="utf-8") as f:
            for zeile in f:
                if zeile.startswith("#") or not zeile.strip():
                    continue
                teile = zeile.split(maxsplit=4)
                ids.append(int(teile[0]))
                xyz.append((float(teile[1]), float(teile[2]), float(teile[3])))
    return np.array(ids, np.int64), np.array(xyz, float).reshape(-1, 3)


def _modelldateien(modell: Path) -> tuple[Path, Path, Path]:
    for endung in (".bin", ".txt"):
        dateien = tuple(modell / f"{n}{endung}" for n in ("cameras", "images", "points3D"))
        if all(d.is_file() for d in dateien):
            return dateien  # type: ignore[return-value]
    raise ValueError(
        f"{modell}: COLMAP-Modell erwartet cameras/images/points3D (.bin oder .txt) im Ordner"
    )


def read_colmap(
    modell: Path,
    bilder: Path,
    *,
    epsg: int,
    vertical_datum: str,
    versatz: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> FrameExport:
    kameras_pfad, bilder_pfad, punkte_pfad = _modelldateien(modell)
    try:
        kalibrierungen = _colmap_kameras(kameras_pfad)
        eintraege = _colmap_bilder(bilder_pfad)
        ids, xyz = _colmap_punkte(punkte_pfad)
    except (struct.error, IndexError, UnicodeDecodeError) as exc:
        raise ValueError(f"COLMAP-Modell ungültig ({exc})") from exc
    sensoren = {cid: FrameSensor(cid, f"COLMAP {cid}", k) for cid, k in kalibrierungen.items()}
    export = _export(
        sensoren,
        eintraege,
        ids,
        xyz,
        bilder,
        epsg=epsg,
        vertical_datum=vertical_datum,
        versatz=np.array(versatz, float),
        gruppe=lambda name: Path(name).parent.as_posix() if "/" in name else "",
    )
    # model_aligner bewegt Punkte und Kameras mit derselben Ähnlichkeit: Rahmen = Modellrahmen.
    return replace(export, modellbeleg=modellbeleg(export, "colmap-points2D"))


# --- OpenDroneMap / OpenSfM --------------------------------------------------------------------


def _odm_epsg(projekt: Path) -> int:
    """EPSG aus `odm_georeferencing/coords.txt` (Zeile 1 CRS; Zeile 2, der Versatz von ODMs
    UTM-Fassung, wird nicht gebraucht: hier wird aus der lokalen Fassung voll gerechnet)."""
    pfad = projekt / "odm_georeferencing" / "coords.txt"
    if not pfad.is_file():
        raise ValueError(
            f"{pfad}: fehlt — ODM-Lauf ist nicht georeferenziert (ohne GPS/GCP nicht verwendbar)"
        )
    zeilen = [z.strip() for z in pfad.read_text(encoding="utf-8").splitlines() if z.strip()]
    if not zeilen:
        raise ValueError(f"{pfad}: leer")
    m = re.fullmatch(r"WGS84 UTM (\d{1,2})([NS])", zeilen[0])
    if m is None:
        raise ValueError(
            f"{pfad}: CRS „{zeilen[0]}“ nicht erkannt (erwartet „WGS84 UTM <Zone><N|S>“)"
        )
    zone = int(m[1])
    if not 1 <= zone <= 60:
        raise ValueError(f"{pfad}: UTM-Zone {zone} ungültig")
    return (32600 if m[2] == "N" else 32700) + zone


TRACKS_KOPF = b"OPENSFM_TRACKS_VERSION_v"
# v102 (binär, OpenSfM in ODM 3.x): je Beobachtung uint16 Länge Bild-ID, uint16 Länge Spur-ID,
# beide IDs als Bytes, dann struct {int feature; float x, y, scale; uint8 r, g, b} (20 Byte mit
# Füllbyte). Text v0 bis v2: Tab-getrennt, Bild, Spur, Merkmal, x, y, …
_TRACKS_BINAER = struct.Struct("<ifff3Bx")


def _opensfm_spuren(daten: bytes) -> list[tuple[str, str, float, float]]:
    """(Bild, Spur, x, y) mit normierten Koordinaten, wie OpenSfM sie speichert."""
    version = 0
    if daten.startswith(TRACKS_KOPF):
        ende = daten.index(b"\n")
        version = int(daten[len(TRACKS_KOPF) : ende])
        daten = daten[ende + 1 :]
    out: list[tuple[str, str, float, float]] = []
    if version >= 100:
        if version != 102:
            raise ValueError(f"OpenSfM tracks.csv: Binärversion v{version} unbekannt")
        pos = 0
        while pos < len(daten):
            if len(out) >= MAX_BEOBACHTUNGEN * 10:
                raise ValueError("OpenSfM tracks.csv: zu viele Beobachtungen")
            lb, ls = struct.unpack_from("<HH", daten, pos)
            pos += 4
            bild = daten[pos : pos + lb].decode("utf-8")
            spur = daten[pos + lb : pos + lb + ls].decode("utf-8")
            pos += lb + ls
            _merkmal, x, y, *_ = _TRACKS_BINAER.unpack_from(daten, pos)
            pos += _TRACKS_BINAER.size
            out.append((bild, spur, x, y))
        return out
    for zeile in csv.reader(daten.decode("utf-8").splitlines(), delimiter="\t"):
        if zeile:
            out.append((zeile[0], zeile[1], float(zeile[3]), float(zeile[4])))
    return out


def _opensfm_beobachtungen(
    pfad: Path, groessen: dict[str, tuple[int, int]]
) -> dict[str, list[tuple[float, float, int]]]:
    try:
        spuren = _opensfm_spuren(pfad.read_bytes())
    except (struct.error, ValueError, IndexError, UnicodeDecodeError) as exc:
        raise ValueError(f"{pfad}: ungültig ({exc})") from exc
    out: dict[str, list[tuple[float, float, int]]] = {}
    for bild, spur, x, y in spuren:
        if bild not in groessen or not spur.isdigit():
            continue
        w, h = groessen[bild]
        size = max(w, h)
        # OpenSfM: normiert = (Pixelindex + 0,5 - Breite/2) / max(w, h)
        out.setdefault(bild, []).append((x * size + w / 2, y * size + h / 2, int(spur)))
    return out


def _odm_affin(projekt: Path, epsg: int) -> tuple[np.ndarray, np.ndarray]:
    """Lineare Abbildung topozentrisch → UTM, genau wie OpenSfM `export_geocoords` (ohne Versatz).

    OpenSfM bildet die drei Einheitsvektoren und den Ursprung des lokalen ENU-Systems
    (`reference_lla.json`, WGS84) nach UTM ab und nimmt die Differenzen als Matrix A. Dieselbe
    Abbildung hat ODM auf seine Punktwolke angewandt; so passen Fotos und Paketwolke zusammen.
    """
    from pyproj import CRS, Transformer

    pfad = projekt / "opensfm" / "reference_lla.json"
    try:
        ref = json.loads(pfad.read_text(encoding="utf-8"))
        lat, lon, alt = (float(ref[k]) for k in ("latitude", "longitude", "altitude"))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{pfad}: nicht lesbar ({exc})") from exc
    nach_ecef = Transformer.from_crs(4979, 4978, always_xy=True)
    nach_lla = Transformer.from_crs(4978, 4979, always_xy=True)
    nach_utm = Transformer.from_crs(4326, CRS.from_epsg(epsg), always_xy=True)
    ursprung = np.array(nach_ecef.transform(lon, lat, alt))
    phi, lam = np.radians(lat), np.radians(lon)
    enu = np.array(
        [
            [-np.sin(lam), np.cos(lam), 0.0],
            [-np.sin(phi) * np.cos(lam), -np.sin(phi) * np.sin(lam), np.cos(phi)],
            [np.cos(phi) * np.cos(lam), np.cos(phi) * np.sin(lam), np.sin(phi)],
        ]
    )

    def utm(p: np.ndarray) -> np.ndarray:
        lo, la, h = nach_lla.transform(*(ursprung + enu.T @ np.array(p, float)))
        e, n = nach_utm.transform(lo, la)
        return np.array([e, n, h])

    q = [utm(p) for p in np.vstack([np.eye(3), np.zeros(3)])]
    a = np.column_stack([q[i] - q[3] for i in range(3)])
    if not np.isfinite(a).all() or not np.isfinite(q[3]).all():
        raise ValueError(f"{pfad}: Bezugspunkt nicht nach EPSG:{epsg} abbildbar")
    return a, q[3]


def _affin_anwenden(export: FrameExport, a: np.ndarray, b: np.ndarray) -> FrameExport:
    """Punkte und Kamerazentren über A, Kameradrehung über die nächstgelegene Drehung von A.

    A ist nicht orthogonal (UTM-Maßstab ≈ 0,9996 waagrecht, 1 senkrecht). OpenSfM setzt
    ``R·A⁻¹`` als Drehung und verschiebt damit die Rückprojektion um Zehntelpixel (Aukerman:
    Median 2,0 px statt 1,4 px); die Polarzerlegung A = P·S hält Drehung und Bild zusammen.
    """
    u, _, vt = np.linalg.svd(a)
    p = u @ vt

    def neu(x: Any) -> tuple[float, float, float]:
        y = a @ np.asarray(x, float) + b
        return (float(y[0]), float(y[1]), float(y[2]))

    return replace(
        export,
        cameras=[
            replace(c, position=neu(c.position), rotation=p @ c.rotation) for c in export.cameras
        ],
        tie_points=export.tie_points @ a.T + b if len(export.tie_points) else export.tie_points,
        tie_projections=[replace(t, world=neu(t.world)) for t in export.tie_projections],
    )


def read_odm(projekt: Path, *, vertical_datum: str) -> FrameExport:
    """Topozentrische Rekonstruktion lesen, Modell dort belegen, dann selbst nach UTM rechnen.

    ODMs `reconstruction.json` liegt schon in UTM, aber mit OpenSfMs Näherung für die Drehung
    (siehe `_affin_anwenden`); deshalb wird die lokale Fassung gelesen.
    """
    epsg = _odm_epsg(projekt)
    pfad = projekt / "opensfm" / "reconstruction.topocentric.json"
    if not pfad.is_file():
        raise ValueError(
            f"{pfad}: fehlt — ODM-Lauf ohne export_geocoords (bis odm_georeferencing rechnen)"
        )
    try:
        rekonstruktionen = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{pfad}: nicht lesbar ({exc})") from exc
    if not isinstance(rekonstruktionen, list) or not rekonstruktionen:
        raise ValueError(f"{pfad}: keine Rekonstruktion")
    rek = rekonstruktionen[0]
    kalibrierungen: dict[str, Kalibrierung] = {}
    sensoren: dict[int, FrameSensor] = {}
    sensor_id: dict[str, int] = {}
    for n, (schluessel, kamera) in enumerate(sorted(rek["cameras"].items())):
        kalibrierungen[schluessel] = opensfm_kalibrierung(kamera)
        sensor_id[schluessel] = n
        sensoren[n] = FrameSensor(n, schluessel, kalibrierungen[schluessel])
    groessen = {
        name: (kalibrierungen[s["camera"]].width, kalibrierungen[s["camera"]].height)
        for name, s in rek["shots"].items()
    }
    beob = _opensfm_beobachtungen(projekt / "opensfm" / "tracks.csv", groessen)
    eintraege = []
    for name, shot in sorted(rek["shots"].items()):
        r = Rotation.from_rotvec(np.array(shot["rotation"], float)).as_matrix()
        b = np.array(beob.get(name, []), float).reshape(-1, 3)
        eintraege.append(
            _Bild(
                name,
                sensor_id[shot["camera"]],
                r,
                np.array(shot["translation"], float),
                b[:, :2],
                b[:, 2].astype(np.int64),
            )
        )
    punkte = rek.get("points", {})
    ids = np.array([int(k) for k in punkte], np.int64)
    xyz = np.array([p["coordinates"] for p in punkte.values()], float).reshape(-1, 3)
    lokal = _export(
        sensoren,
        eintraege,
        ids,
        xyz,
        projekt / "images",
        epsg=epsg,
        vertical_datum=vertical_datum,
        versatz=np.zeros(3),
        gruppe=lambda _: "",
    )
    beleg = modellbeleg(lokal, "opensfm-tracks")
    export = replace(_affin_anwenden(lokal, *_odm_affin(projekt, epsg)), modellbeleg=beleg)
    weitere = sum(len(r.get("shots", {})) for r in rekonstruktionen[1:])
    if weitere:
        export.skipped.append(
            {"label": f"{weitere} Foto(s)", "reason": "in Nebenrekonstruktion von OpenSfM"}
        )
    return export


def modellbeleg(export: FrameExport, quelle: str) -> dict[str, Any]:
    """Beobachtete Pixel gegen Projektion mit dem abgebildeten Modell, im Originalbild.

    Ersetzt die Referenzpaare des Austauschformats: Median-Restvektor ≤ `RESTVEKTOR_GRENZE_PX`,
    sonst Abbruch.
    Median statt Mittel, weil SfM-Beobachtungen Ausreißer enthalten (OpenSfM gleicht robust aus);
    ein Halbpixel-Versatz oder vertauschte Tangentialterme verschieben den Median trotzdem.
    Im Rahmen der Rekonstruktion selbst prüfen — eine spätere Umrechnung nach UTM ist nicht
    winkeltreu und gehört zur Rückprojektion, nicht zum Modell.
    """
    kameras = {c.label: c for c in export.cameras}
    rest = []
    for t in export.tie_projections:
        c = kameras[t.camera]
        lokal = c.rotation.T @ (np.array(t.world) - np.array(c.position))
        if lokal[2] <= 0:
            continue
        u, v = verzeichnen(
            export.sensors[c.sensor_id].kalibrierung,
            np.array([lokal[0] / lokal[2]]),
            np.array([lokal[1] / lokal[2]]),
        )
        rest.append((t.pixel[0] - float(u[0]), t.pixel[1] - float(v[0])))
    if len(rest) < MIN_BEOBACHTUNGEN:
        raise ValueError(
            f"Kameramodell: zu wenige Beobachtungen für den Beleg ({len(rest)} < "
            f"{MIN_BEOBACHTUNGEN})"
        )
    arr = np.array(rest)
    dx, dy = (float(x) for x in np.median(arr, axis=0))
    if not np.hypot(dx, dy) <= RESTVEKTOR_GRENZE_PX:
        raise ValueError(
            f"Kameramodell: Median-Restvektor ({dx:+.3f}, {dy:+.3f}) px über "
            f"{RESTVEKTOR_GRENZE_PX} px — Modellabbildung oder Pixelkonvention passt nicht"
        )
    return {
        "source": quelle,
        "n": len(arr),
        "medianPx": float(np.median(np.hypot(arr[:, 0], arr[:, 1]))),
        "medianResidualPx": [dx, dy],
        "limitPx": RESTVEKTOR_GRENZE_PX,
    }


# --- gemeinsam ---------------------------------------------------------------------------------


def _bildpfad(bilder: Path, name: str) -> Path:
    basis = bilder.resolve()
    pfad = (basis / name).resolve()
    if not pfad.is_relative_to(basis):
        raise ValueError(f"{name}: Bildpfad liegt außerhalb des Bildordners")
    if not pfad.is_file():
        raise ValueError(f"{name}: Bild fehlt: {pfad}")
    return pfad


def _gleichmaessig(n: int, hoechstens: int) -> np.ndarray:
    if n <= hoechstens:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, hoechstens).round().astype(np.int64))


def _export(
    sensoren: dict[int, FrameSensor],
    eintraege: list[_Bild],
    ids: np.ndarray,
    xyz: np.ndarray,
    bilder: Path,
    *,
    epsg: int,
    vertical_datum: str,
    versatz: np.ndarray,
    gruppe: Any,
) -> FrameExport:
    if not eintraege:
        raise ValueError("Rekonstruktion enthält keine Fotos")
    if not np.isfinite(xyz).all():
        raise ValueError("Rekonstruktion: 3D-Punkte nicht endlich")
    welt = {int(i): p + versatz for i, p in zip(ids, xyz, strict=True)}
    kameras: list[FrameCamera] = []
    projektionen: list[TieProjection] = []
    namen: set[str] = set()
    for b in sorted(eintraege, key=lambda e: e.name):
        if b.name in namen:
            raise ValueError(f"{b.name}: Bildname doppelt")
        namen.add(b.name)
        sensor = sensoren.get(b.sensor)
        if sensor is None:
            raise ValueError(f"{b.name}: Kamera {b.sensor} fehlt im Modell")
        k = sensor.kalibrierung
        pfad = _bildpfad(bilder, b.name)
        with Image.open(pfad) as image:
            if image.size != (k.width, k.height):
                raise ValueError(
                    f"{b.name}: Bildmaße {image.size} passen nicht zur Kamera "
                    f"({k.width}, {k.height})"
                )
            orientation = image.getexif().get(0x0112, 1)
            if orientation != 1:
                raise ValueError(
                    f"{b.name}: EXIF-Orientation {orientation} — gedrehte Bilder werden nicht "
                    "geraten"
                )
            image.verify()
        drehung = b.drehung.T
        zentrum = -drehung @ b.t + versatz
        kameras.append(
            FrameCamera(
                b.name,
                gruppe(b.name),
                pfad,
                k.width,
                k.height,
                (float(zentrum[0]), float(zentrum[1]), float(zentrum[2])),
                drehung,
                b.sensor,
            )
        )
        gueltig = np.flatnonzero([int(p) in welt for p in b.punkt])
        for i in gueltig[_gleichmaessig(len(gueltig), BEOBACHTUNGEN_JE_FOTO)]:
            w = welt[int(b.punkt[i])]
            projektionen.append(
                TieProjection(
                    b.name,
                    (float(b.pixel[i, 0]), float(b.pixel[i, 1])),
                    (float(w[0]), float(w[1]), float(w[2])),
                )
            )
    tie = xyz[_gleichmaessig(len(xyz), TIE_POINTS_MAX)] + versatz
    return FrameExport(
        epsg=epsg,
        vertical_datum=vertical_datum,
        cameras=kameras,
        sensors={s: sensoren[s] for s in sorted({c.sensor_id for c in kameras})},
        tie_points=tie,
        tie_projections=projektionen,
        skipped=[],
        max_referenz_abweichung_px=None,
    )
