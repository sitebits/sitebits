"""Geprüftes Kameraformat und Adapter für Metashape-Kugel- und Rahmenkameras.

kameras.json formatVersion 1 enthält nur Kugelkameras; formatVersion 2 (Export ab 0.9) trägt je
Kamera `kind` und für Rahmenkameras Sensoren mit Kalibrierung und Referenzpaaren. Der
Panoramaweg (`read_cameras`) nimmt aus v2 nur die Kugelkameras, `read_frame_cameras` nur die
Rahmenkameras. Rahmenkameras bleiben hier in Metashape-Basis; umgerechnet wird beim Schreiben.

Metashape: +X rechts, +Y unten, +Z vorn (Equirektangular-Bildmitte).
StationSource: +X rechts, +Y oben, -Z vorn. Die Basisänderung C=diag(1,-1,-1)
ist eine rechtshändige 180°-Drehung um X: R_station = R_metashape @ C.
Die Pixel bleiben unverändert: das kanonische Kugelbild hat Mitte -Z und oben +Y.
Nur dieser Adapter rechnet die fremde Kamerabasis um; Writer und Viewer bleiben gleich.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TypeVar
from uuid import NAMESPACE_URL, uuid5

import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation

from .equirectangular import FACE_ROTATIONS, cube_face
from .model import ImageInfo, Pose, Quat, Vec3
from .panos import FACES, StationSource
from .verzeichnung import Kalibrierung, pruefe_referenzpaare

# Kugelpanoramen sind groß: PTGui-Luftpanoramen haben rund 180 MP und liegen damit über Pillows
# Schutzgrenze gegen Dekompressionsbomben (zweimal 89 MP). Die Quelle ist eine lokale, vom Betreiber
# exportierte Datei. Statt die Grenze global abzuschalten, gilt beim Öffnen eine eigene,
# ausdrückliche Obergrenze; Pillows Wert wird danach wiederhergestellt.
PANORAMA_MAX_PIXELS = 1_000_000_000


@contextmanager
def _panorama_oeffnen(path: Path) -> Iterator[Image.Image]:
    alt = Image.MAX_IMAGE_PIXELS
    # Image.open liest nur den Kopf; die Größe prüft ausschließlich die eigene Grenze darunter.
    Image.MAX_IMAGE_PIXELS = None
    try:
        with Image.open(path) as image:
            if image.width * image.height > PANORAMA_MAX_PIXELS:
                raise ValueError(
                    f"{path.name}: Panorama zu groß ({image.width}x{image.height}, "
                    f"höchstens {PANORAMA_MAX_PIXELS:,} Pixel)"
                )
            yield image
    finally:
        Image.MAX_IMAGE_PIXELS = alt


@dataclass(frozen=True)
class Camera:
    label: str
    group: str
    image_path: Path
    width: int
    height: int
    position: Vec3
    rotation: np.ndarray


@dataclass(frozen=True)
class CameraExport:
    epsg: int
    vertical_datum: str
    cameras: list[Camera]
    skipped: list[dict[str, str]]


def _text(value: Any, field: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"kameras.json: {field} muss Text enthalten")
    return value


def _kopf(data: dict[str, Any], versions: tuple[int, ...]) -> tuple[int, str]:
    version = data["formatVersion"]
    if type(version) is not int or version not in versions:
        erwartet = " oder ".join(str(v) for v in versions)
        raise ValueError(f"kameras.json: unbekannte Format-Version (erwartet {erwartet})")
    crs = data["crs"]
    epsg = crs["epsg"]
    if type(epsg) is not int or epsg <= 0:
        raise ValueError("kameras.json: EPSG muss eine positive ganze Zahl sein")
    datum = _text(crs["verticalDatum"], "Höhensystem")
    if not isinstance(data["cameras"], list):
        raise ValueError("kameras.json: cameras muss eine Liste sein")
    return epsg, datum


def _identitaet(
    row: dict[str, Any], identities: set[tuple[Path, str, str]], labels: set[str] | None = None
) -> tuple[str, str, Path]:
    label = _text(row["label"], "Label")
    if labels is not None:
        # Rahmenkameras: Tie-Point-Projektionen hängen nur am Label, nicht an der Gruppe.
        if label in labels:
            raise ValueError(
                f"{label}: Label in mehreren Gruppen — Metashape ordnet Tie-Point-Projektionen "
                "nur über das Label zu; Kamera in Metashape umbenennen"
            )
        labels.add(label)
    group = _text(row["group"], "Gruppe", empty=True)
    image_path = Path(_text(row["imagePath"], "Bildpfad"))
    if not image_path.is_absolute():
        raise ValueError(f"{label}: Bildpfad muss absolut sein")
    if not image_path.is_file():
        raise ValueError(f"{label}: Bild fehlt: {image_path}")
    identity = (image_path, group, label)
    if identity in identities:
        raise ValueError(f"{label}: Kameraidentität ist doppelt vorhanden")
    identities.add(identity)
    return label, group, image_path


def _pose_lesen(row: dict[str, Any], label: str) -> tuple[Vec3, np.ndarray]:
    try:
        position = np.asarray(row["position"], dtype=float)
        rotation = np.asarray(row["rotation"], dtype=float)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label}: Position und Rotation müssen Zahlen enthalten") from exc
    if position.shape != (3,) or rotation.shape != (3, 3):
        raise ValueError(f"{label}: Position braucht 3 Werte, Rotation eine 3x3-Matrix")
    if not np.isfinite(position).all() or not np.isfinite(rotation).all():
        raise ValueError(f"{label}: Position und Rotation müssen endlich sein")
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6, rtol=0) or not np.isclose(
        np.linalg.det(rotation), 1, atol=1e-6, rtol=0
    ):
        raise ValueError(f"{label}: Rotation ist nicht rechtshändig orthonormal")
    return (float(position[0]), float(position[1]), float(position[2])), rotation


def _skipped(data: dict[str, Any]) -> list[dict[str, str]]:
    skipped = data.get("skipped", [])
    if not isinstance(skipped, list):
        raise ValueError("kameras.json: skipped muss eine Liste sein")
    return [
        {
            "label": _text(r["label"], "übersprungenes Label"),
            "reason": _text(r["reason"], "Überspringgrund"),
        }
        for r in skipped
    ]


def read_cameras(path: Path) -> CameraExport:
    """Validiert den gesamten Export einschließlich Bilddateien vor jedem Schreiben."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        epsg, datum = _kopf(data, (1, 2))
        cameras = []
        identities: set[tuple[Path, str, str]] = set()
        for row in data["cameras"]:
            if data["formatVersion"] == 2 and row.get("kind") != "spherical":
                continue
            label, group, image_path = _identitaet(row, identities)
            width, height = row["width"], row["height"]
            if (
                type(width) is not int
                or type(height) is not int
                or width < 4
                or height < 2
                or width != 2 * height
            ):
                raise ValueError(
                    f"{label}: Bildmaße müssen ein Equirektangularbild (2:1) beschreiben"
                )
            with _panorama_oeffnen(image_path) as image:
                if image.size != (width, height):
                    raise ValueError(f"{label}: Bildmaße stimmen nicht mit der Datei überein")
                image.verify()
            position, rotation = _pose_lesen(row, label)
            cameras.append(Camera(label, group, image_path, width, height, position, rotation))
        return CameraExport(epsg, datum, cameras, _skipped(data))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"kameras.json: ungültiges Format ({exc})") from exc
    except OSError as exc:
        raise ValueError(f"kameras.json: Datei oder Bild nicht lesbar ({exc})") from exc


def _quaternion(matrix: np.ndarray) -> Quat:
    x, y, z, w = Rotation.from_matrix(matrix).as_quat()
    return (float(w), float(x), float(y), float(z))


def _station(camera: Camera) -> StationSource:
    size = min(camera.width // 4, 4096)
    rotation = camera.rotation @ np.diag([1.0, -1.0, -1.0])
    guid = str(
        uuid5(NAMESPACE_URL, json.dumps([str(camera.image_path), camera.group, camera.label]))
    )
    images = [
        ImageInfo(
            index=index,
            guid=f"{guid}:{face}",
            kind="pinhole",
            width=size,
            height=size,
            pose=Pose(camera.position, _quaternion(rotation @ FACE_ROTATIONS[face])),
            scan_guid=guid,
            focal_length=1.0,
            pixel_width=2 / size,
            pixel_height=2 / size,
            principal_point=((size - 1) / 2, (size - 1) / 2),
        )
        for index, face in enumerate(FACES)
    ]

    def read_image(im: ImageInfo) -> Image.Image:
        # Eine Quelle plus eine Ausgabeseite, niemals sechs dekodierte Seiten im Cache.
        with _panorama_oeffnen(camera.image_path) as image:
            return cube_face(image, FACES[im.index], size)

    return StationSource(
        scan_guid=guid,
        name=f"{camera.group} / {camera.label}" if camera.group else camera.label,
        pose=Pose(camera.position, _quaternion(rotation)),
        images=images,
        faces={face: index for index, face in enumerate(FACES)},
        read_image=read_image,
        point_source_id=None,
    )


def metashape_station_sources(source: Path | CameraExport) -> list[StationSource]:
    """Je geprüfter Kugelkamera ein Standpunkt; Seitenpixel erst beim Schreiben lesen."""
    export = read_cameras(source) if isinstance(source, Path) else source
    return [_station(camera) for camera in export.cameras]


@dataclass(frozen=True)
class FrameSensor:
    id: int
    label: str
    kalibrierung: Kalibrierung


@dataclass(frozen=True)
class FrameCamera:
    """Rahmenkamera in Metashape-Basis (X rechts, Y unten, Z Blickrichtung), Weltposition."""

    label: str
    group: str
    image_path: Path
    width: int
    height: int
    position: Vec3
    rotation: np.ndarray
    sensor_id: int
    # Angenommene EXIF-Orientation der Datei (≠ 1 nur mit `exif_drehung_ignorieren`).
    exif_orientation: int = 1


# Drehungen um 90° (5 bis 8) vertauschen Breite und Höhe: Stimmen die Rohpixelmaße eines
# nicht-quadratischen Bildes mit Metashapes Kamera überein, hat Metashape ungedreht gerechnet.
# 2 bis 4 (Spiegeln, 180°) ändern die Maße nicht und bleiben unbelegbar.
EXIF_QUERFORMAT_BELEGBAR = frozenset({5, 6, 7, 8})


@dataclass(frozen=True)
class TieProjection:
    camera: str
    pixel: tuple[float, float]
    world: Vec3


@dataclass(frozen=True)
class FrameExport:
    epsg: int
    vertical_datum: str
    cameras: list[FrameCamera]
    sensors: dict[int, FrameSensor]
    tie_points: np.ndarray
    tie_projections: list[TieProjection]
    skipped: list[dict[str, str]]
    max_referenz_abweichung_px: float
    document: dict[str, Any] | None = None


def _endliche_reihe(value: Any, n: int, feld: str) -> tuple[float, ...]:
    try:
        werte = np.asarray(value, dtype=float)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"kameras.json: {feld} muss Zahlen enthalten") from exc
    if werte.shape != (n,) or not np.isfinite(werte).all():
        raise ValueError(f"kameras.json: {feld} braucht {n} endliche Zahlen")
    return tuple(float(w) for w in werte)


def read_frame_cameras(path: Path, *, exif_drehung_ignorieren: bool = False) -> FrameExport:
    """Rahmenkameras eines v2-Exports; prüft Sensoren gegen Metashapes Referenzpaare."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("formatVersion") != 2:
            raise ValueError("kameras.json: Rahmenkameras brauchen formatVersion 2 (neuer Export)")
        epsg, datum = _kopf(data, (2,))
        sensors: dict[int, FrameSensor] = {}
        groesste = 0.0
        for row in data["sensors"]:
            sid = row["id"]
            if type(sid) is not int:
                raise ValueError("kameras.json: Sensor-ID muss eine ganze Zahl sein")
            if row["type"] != "frame":
                raise ValueError(f"Sensor {sid}: Typ {row['type']} wird nicht unterstützt")
            try:
                kalibrierung = Kalibrierung.aus_export(
                    row["width"], row["height"], row["calibration"]
                )
                groesste = max(groesste, pruefe_referenzpaare(kalibrierung, row["referencePairs"]))
            except ValueError as exc:
                raise ValueError(f"Sensor {sid} ({row.get('label', '')}): {exc}") from exc
            sensors[sid] = FrameSensor(sid, str(row.get("label", "")), kalibrierung)
        cameras: list[FrameCamera] = []
        identities: set[tuple[Path, str, str]] = set()
        labels: set[str] = set()
        skipped = _skipped(data)
        for row in data["cameras"]:
            if row.get("kind") != "frame":
                skipped.append({"label": str(row.get("label", "")), "reason": "keine Rahmenkamera"})
                continue
            label, group, image_path = _identitaet(row, identities, labels)
            sensor = sensors.get(row["sensor"])
            if sensor is None:
                raise ValueError(f"{label}: Sensor {row['sensor']} fehlt im Export")
            k = sensor.kalibrierung
            if (row["width"], row["height"]) != (k.width, k.height):
                raise ValueError(f"{label}: Bildgröße passt nicht zum Sensor {sensor.id}")
            with Image.open(image_path) as image:
                if image.size != (k.width, k.height):
                    raise ValueError(f"{label}: Bildmaße stimmen nicht mit der Datei überein")
                # Nur der Kopf: so früh, dass kein Lauf erst nach Stunden Entzerrung abbricht.
                orientation = image.getexif().get(0x0112, 1)
                belegt = orientation in EXIF_QUERFORMAT_BELEGBAR and k.width != k.height
                if orientation != 1 and not (exif_drehung_ignorieren and belegt):
                    hinweis = (
                        " (mit --exif-drehung-ignorieren: Pixelmaße belegen Sensorpixel)"
                        if belegt
                        else ""
                    )
                    raise ValueError(
                        f"{label}: EXIF-Orientation {orientation} — gedrehte Bilder werden nicht "
                        f"geraten; Metashape rechnet in Sensorpixeln{hinweis}"
                    )
                image.verify()
            position, rotation = _pose_lesen(row, label)
            cameras.append(
                FrameCamera(
                    label,
                    group,
                    image_path,
                    k.width,
                    k.height,
                    position,
                    rotation,
                    sensor.id,
                    orientation,
                )
            )
        if not cameras:
            raise ValueError("kameras.json: keine Rahmenkameras im Export")
        tie = [_endliche_reihe(p, 3, "Tie Point") for p in data.get("tiePoints", [])]
        projections = [
            TieProjection(
                _text(p["camera"], "Tie-Point-Kamera"),
                _endliche_reihe(p["pixel"], 2, "Tie-Point-Pixel"),  # type: ignore[arg-type]
                _endliche_reihe(p["world"], 3, "Tie-Point-Welt"),  # type: ignore[arg-type]
            )
            for p in data.get("tieProjections", [])
        ]
        return FrameExport(
            epsg,
            datum,
            cameras,
            sensors,
            np.asarray(tie, dtype=float).reshape(-1, 3),
            projections,
            skipped,
            groesste,
            data.get("document") if isinstance(data.get("document"), dict) else None,
        )
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"kameras.json: ungültiges Format ({exc})") from exc
    except OSError as exc:
        raise ValueError(f"kameras.json: Datei oder Bild nicht lesbar ({exc})") from exc


# Bewusst TypeVar statt `def f[E: …]` (PEP 695): bandit im SAST-Werkzeug der Release-Kette läuft mit
# Python 3.11 und kann die 3.12-Syntax nicht parsen (Trockenlauf 0.9.0 brach daran ab).
E = TypeVar("E", "CameraExport", "FrameExport")


def hoehen_umrechnen(export: E, quelle: str, ziel: str) -> E:  # noqa: UP047
    """Kamerapositionen (und Tie Points) ins Höhensystem des Pakets umrechnen.

    Nur die Höhe ändert sich; die Drehung bleibt (die Neigung des Quasigeoids liegt im
    Bereich von Bogensekunden und ist für die Blickrichtung ohne Belang).
    """
    from . import hoehen

    def neu(punkte: np.ndarray) -> np.ndarray:
        if not len(punkte):
            return punkte
        out = np.array(punkte, dtype=float, copy=True)
        out[:, 2] = hoehen.rechne_um(
            out[:, 0], out[:, 1], out[:, 2], crs=export.epsg, quelle=quelle, ziel=ziel
        )
        return out

    positionen = neu(np.asarray([c.position for c in export.cameras], dtype=float).reshape(-1, 3))
    kameras = [
        replace(c, position=(float(p[0]), float(p[1]), float(p[2])))
        for c, p in zip(export.cameras, positionen, strict=True)
    ]
    if isinstance(export, FrameExport):
        welt = neu(
            np.asarray([t.world for t in export.tie_projections], dtype=float).reshape(-1, 3)
        )
        projektionen = [
            replace(t, world=(float(w[0]), float(w[1]), float(w[2])))
            for t, w in zip(export.tie_projections, welt, strict=True)
        ]
        return replace(
            export,
            cameras=kameras,
            tie_points=neu(export.tie_points),
            tie_projections=projektionen,
            vertical_datum=ziel,
        )
    return replace(export, cameras=kameras, vertical_datum=ziel)


def lage_anwenden(export: E, matrix: np.ndarray) -> E:  # noqa: UP047
    """Kameras (Position und Drehung) und Tie Points mit der Lagematrix des Pakets bewegen.

    Die Spalten der Kameradrehung sind die Kameraachsen in Weltkoordinaten; eine starre
    Bewegung der Welt dreht sie mit: ``R_neu = R_lage @ R``.
    """
    from .lage import wende_an

    drehung = np.asarray(matrix, dtype=float)[:3, :3]

    def neu(punkte: np.ndarray) -> np.ndarray:
        return wende_an(punkte, matrix) if len(punkte) else punkte

    positionen = neu(np.asarray([c.position for c in export.cameras], dtype=float).reshape(-1, 3))
    kameras = [
        replace(c, position=(float(p[0]), float(p[1]), float(p[2])), rotation=drehung @ c.rotation)
        for c, p in zip(export.cameras, positionen, strict=True)
    ]
    if isinstance(export, FrameExport):
        welt = neu(
            np.asarray([t.world for t in export.tie_projections], dtype=float).reshape(-1, 3)
        )
        projektionen = [
            replace(t, world=(float(w[0]), float(w[1]), float(w[2])))
            for t, w in zip(export.tie_projections, welt, strict=True)
        ]
        return replace(
            export, cameras=kameras, tie_points=neu(export.tie_points), tie_projections=projektionen
        )
    return replace(export, cameras=kameras)
