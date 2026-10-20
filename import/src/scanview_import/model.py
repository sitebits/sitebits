"""Datenmodelle für Inventar, Manifest und Standpunkte."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]
FaceName = Literal["px", "nx", "py", "ny", "pz", "nz"]


@dataclass(frozen=True)
class Pose:
    translation: Vec3
    rotation_wxyz: Quat


@dataclass
class ScanInfo:
    index: int
    guid: str
    name: str
    point_count: int
    fields: list[str]
    pose: Pose
    # Deklarierter Maximalwert des colorRed-Feldes im E57-Prototyp (8-bit-Quellen
    # haben hier 255, nicht erst am tatsächlichen Datenmaximum gemessen). None, wenn das Feld
    # fehlt oder kein IntegerNode ist.
    color_max: int | None = None
    # Deklarierter colorRedMaximum-Wert aus der Data3D-``colorLimits``-Struktur (PDALs
    # readers.e57 skaliert, wie sich zeigte, PDALs readers.e57 8-bit-RGB bereits selbst auf 16 bit
    # skaliert, wenn colorLimits vorhanden ist — empirisch an der Pilotkirche und einer Mini-Fixture
    # bestätigt, in --stream wie im Normalmodus). None, wenn die Struktur oder das Feld fehlt —
    # entscheidet zusammen mit ``color_max``, ob convert() selbst noch skalieren muss.
    color_limits_max: int | None = None
    coord_precision: Literal["single", "double", "scaled"] = "double"
    local_abs_max_m: float | None = None


@dataclass
class ImageInfo:
    index: int
    guid: str
    kind: Literal["pinhole", "spherical", "cylindrical", "visual"]
    width: int
    height: int
    pose: Pose
    scan_guid: str | None
    focal_length: float | None = None
    pixel_width: float | None = None
    pixel_height: float | None = None
    principal_point: tuple[float, float] | None = None
    # Pfad des jpegImage/pngImage-Blobs, z. B. "pinholeRepresentation/jpegImage".
    blob_node: str = ""


@dataclass
class Inventory:
    filename: str
    size_bytes: int
    crs_wkt: str | None
    scans: list[ScanInfo] = field(default_factory=list)
    images: list[ImageInfo] = field(default_factory=list)
