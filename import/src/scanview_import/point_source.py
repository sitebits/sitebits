"""Expliziter Quellenvertrag für scanlose Punktformate; keine CRS-/Einheitenheuristik."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import laspy
import numpy as np

UNIT_FACTORS = {"m": 1.0, "cm": 0.01, "mm": 0.001, "ft": 0.3048}
PLY_TYPES = {
    "char": "i1",
    "int8": "i1",
    "uchar": "u1",
    "uint8": "u1",
    "short": "i2",
    "int16": "i2",
    "ushort": "u2",
    "uint16": "u2",
    "int": "i4",
    "int32": "i4",
    "uint": "u4",
    "uint32": "u4",
    "float": "f4",
    "float32": "f4",
    "double": "f8",
    "float64": "f8",
}
COLUMN_NAMES = {
    "x": "X",
    "y": "Y",
    "z": "Z",
    "r": "Red",
    "red": "Red",
    "g": "Green",
    "green": "Green",
    "b": "Blue",
    "blue": "Blue",
    "i": "Intensity",
    "intensity": "Intensity",
}


@dataclass(frozen=True)
class PlyHeader:
    encoding: str
    count: int
    properties: tuple[tuple[str, str], ...]
    offset: int

    def dtype(self) -> np.dtype:
        endian = ">" if self.encoding == "binary_big_endian" else "<"
        return np.dtype([(name, endian + PLY_TYPES[typ]) for name, typ in self.properties])


def read_ply_header(path: Path) -> PlyHeader:
    with path.open("rb") as f:
        if f.readline() != b"ply\n" and not _is_crlf_ply(path):
            raise ValueError("Ungültiger PLY-Header")
        encoding, count, element = "", -1, ""
        properties: list[tuple[str, str]] = []
        for _ in range(4096):
            line = f.readline(4096)
            try:
                parts = line.decode("ascii").strip().split()
            except UnicodeDecodeError as exc:
                raise ValueError("Ungültiger PLY-Header") from exc
            if not parts:
                raise ValueError("Unvollständiger PLY-Header")
            if parts[0] == "format":
                if len(parts) != 3 or parts[2] != "1.0":
                    raise ValueError("Nicht unterstützte PLY-Version")
                encoding = parts[1]
            elif parts[0] == "element":
                if len(parts) != 3:
                    raise ValueError("Ungültiges PLY-Element")
                element = parts[1]
                n = int(parts[2])
                if n < 0:
                    raise ValueError("Negative PLY-Punktzahl")
                if element == "face" and n > 0:
                    raise ValueError("Mesh-PLY folgt mit Weg A")
                if element == "vertex":
                    if count != -1:
                        raise ValueError("Doppeltes PLY-Vertexelement")
                    count = n
                elif n > 0:
                    raise ValueError(f"Nicht unterstütztes PLY-Element: {element}")
            elif parts[0] == "property" and element == "vertex":
                if len(parts) != 3 or parts[1] not in PLY_TYPES:
                    raise ValueError("Nicht unterstützte PLY-Vertexeigenschaft")
                properties.append((parts[2], parts[1]))
            elif parts[0] == "end_header":
                names = [p[0] for p in properties]
                if (
                    encoding not in ("ascii", "binary_little_endian", "binary_big_endian")
                    or count < 0
                    or not {"x", "y", "z"} <= set(names)
                    or len(set(names)) != len(names)
                ):
                    raise ValueError(
                        "PLY benötigt gültiges Format und eindeutige XYZ-Eigenschaften"
                    )
                return PlyHeader(encoding, count, tuple(properties), f.tell())
        raise ValueError("PLY-Header zu lang")


def _is_crlf_ply(path: Path) -> bool:
    with path.open("rb") as f:
        return f.readline() == b"ply\r\n"


def detect_format(name: str) -> tuple[str, str]:
    lower = name.lower()
    if lower.endswith(".copc.laz"):
        return "copc", "readers.las"
    suffix = Path(lower).suffix
    formats = {
        ".e57": "e57",
        ".las": "las",
        ".laz": "laz",
        ".ply": "ply",
        ".pts": "pts",
        ".xyz": "xyz",
        ".txt": "txt",
        ".csv": "csv",
    }
    if suffix not in formats:
        raise ValueError(f"Nicht unterstütztes Punktformat {suffix!r}; PTX folgt später")
    fmt = formats[suffix]
    reader = "las" if fmt in ("las", "laz") else fmt
    if fmt in ("xyz", "txt", "csv"):
        reader = "text"
    return fmt, f"readers.{reader}"


def _epsg(crs: str) -> int | None:
    short = re.fullmatch(r"EPSG:(\d+)", crs.strip(), re.IGNORECASE)
    if short:
        return int(short[1])
    for match in re.finditer(
        r'(?:ID|AUTHORITY)\s*\[\s*"EPSG"\s*,\s*"?(\d+)"?\s*\]', crs, re.IGNORECASE
    ):
        depth, quoted = 0, False
        for char in crs[: match.start()]:
            if char == '"':
                quoted = not quoted
            elif not quoted:
                depth += (char == "[") - (char == "]")
        if depth == 1 and not quoted:
            return int(match[1])
    return None


def _same_crs(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return a == b
    ea, eb = _epsg(a), _epsg(b)
    return a.strip() == b.strip() or (ea is not None and ea == eb)


def las_crs(header: laspy.LasHeader) -> tuple[bool, str | None]:
    """WKT und GeoKeys unabhängig lesen; unbekannte benutzerdefinierte CRS ablehnen."""
    sources: list[str | None] = []
    vlrs = list(header.vlrs) + list(header.evlrs or [])
    citations = " ".join(
        " ".join(v.strings) for v in vlrs if isinstance(v, laspy.vlrs.known.GeoAsciiParamsVlr)
    )
    for v in vlrs:
        if isinstance(v, laspy.vlrs.known.WktCoordinateSystemVlr) and v.string.strip():
            sources.append(v.string.strip())
        if isinstance(v, laspy.vlrs.known.GeoKeyDirectoryVlr):
            keys = {k.id: k.value_offset for k in v.geo_keys if k.tiff_tag_location == 0}
            code = keys.get(3072, keys.get(2048))
            if code is not None:
                if code == 32767:
                    if "local" in citations.lower():
                        sources.append(None)
                    elif not any(
                        isinstance(w, laspy.vlrs.known.WktCoordinateSystemVlr) for w in vlrs
                    ):
                        raise ValueError("LAS: benutzerdefiniertes CRS ohne WKT/Local-Citation")
                else:
                    sources.append(f"EPSG:{code}")
    if sources and any(not _same_crs(sources[0], s) for s in sources[1:]):
        raise ValueError("CRS-Angaben in LAS widersprechen sich")
    return bool(sources), sources[0] if sources else None


def parse_columns(value: str) -> tuple[str, ...]:
    try:
        cols = tuple(COLUMN_NAMES[c.strip().lower()] for c in value.split(","))
    except KeyError as exc:
        raise ValueError("Spalten: erlaubt sind x,y,z,r,g,b,intensity") from exc
    if not {"X", "Y", "Z"} <= set(cols) or len(set(cols)) != len(cols):
        raise ValueError("Spalten müssen XYZ genau einmal enthalten")
    rgb = set(cols) & {"Red", "Green", "Blue"}
    if rgb and len(rgb) != 3:
        raise ValueError("Spalten: RGB nur vollständig angeben")
    return cols


@dataclass(frozen=True)
class PointSource:
    path: Path
    format: str
    reader: str
    crs: str | None
    crs_from: str
    unit: str
    unit_from: str
    up: str
    columns: tuple[str, ...] = ()
    separator: str = " "
    text_skip: int = 0
    ply: PlyHeader | None = None

    def transform(self, xyz: np.ndarray) -> np.ndarray:
        xyz = np.asarray(xyz, dtype=np.float64) * UNIT_FACTORS[self.unit]
        if self.up == "y":
            xyz = xyz[:, [0, 2, 1]] * [1.0, -1.0, 1.0]
        return xyz

    def options(self) -> dict[str, Any]:
        return {
            "crs": self.crs,
            "unit": self.unit,
            "up": self.up,
            "columns": ",".join(self.columns) if self.columns else None,
        }


def read_sidecar(path: Path) -> dict[str, Any]:
    side_path = path.with_name(path.name + ".scanview.json")
    side: dict[str, Any] = {}
    if side_path.exists():
        try:
            side = json.loads(side_path.read_text())
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError("Ungültiges scanview-Sidecar") from exc
        if not isinstance(side, dict):
            raise ValueError("scanview-Sidecar muss ein JSON-Objekt sein")
    return side


def resolve_declarations(
    side: dict[str, Any], crs: str | None, unit: str | None,
    crs_sources: list[tuple[str, str | None]], units: list[tuple[str, str]],
) -> tuple[list[tuple[str, str | None]], list[tuple[str, str]]]:
    for value in (crs, side.get("crs")):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("CRS muss eine nichtleere WKT-/EPSG-Zeichenkette sein")
    if "crs" in side:
        crs_sources.append(("sidecar", side["crs"]))
    if crs is not None:
        crs_sources.append(("cli", crs))
    if crs_sources and any(not _same_crs(crs_sources[0][1], c) for _, c in crs_sources[1:]):
        raise ValueError("CRS aus Datei, Sidecar und CLI widersprechen sich")
    if "unit" in side:
        units.append(("sidecar", side["unit"]))
    if unit is not None:
        units.append(("cli", unit))
    if not units:
        raise ValueError("Einheit fehlt: --unit m|cm|mm|ft oder Sidecar erforderlich")
    if any(not isinstance(u, str) or u not in UNIT_FACTORS for _, u in units):
        raise ValueError("Ungültige Einheit: erlaubt sind m, cm, mm, ft")
    if any(u != units[0][1] for _, u in units[1:]):
        raise ValueError("Einheiten aus Datei, Sidecar und CLI widersprechen sich")
    return crs_sources, units


def resolve_source(
    path: Path,
    crs: str | None = None,
    unit: str | None = None,
    up: str = "z",
    columns: str | None = None,
) -> PointSource:
    path = path.resolve()
    fmt, reader = detect_format(path.name)
    if fmt == "e57":
        raise ValueError("E57 nutzt den bestehenden Scan-Quellenvertrag")
    if up not in ("y", "z"):
        raise ValueError("Achse oben muss y oder z sein")
    side = read_sidecar(path)
    crs_sources: list[tuple[str, str | None]] = []
    units: list[tuple[str, str]] = []
    ph = read_ply_header(path) if fmt == "ply" else None
    if reader == "readers.las":
        with laspy.open(path) as f:
            present, file_crs = las_crs(f.header)
        if present:
            crs_sources.append(("file", file_crs))
        units.append(("file", "m"))
    crs_sources, units = resolve_declarations(side, crs, unit, crs_sources, units)
    cols: tuple[str, ...] = ()
    separator, skip = " ", 0
    if reader == "readers.text":
        if columns is None:
            raise ValueError("Textquelle benötigt --columns (z. B. x,y,z,r,g,b)")
        cols = parse_columns(columns)
        with path.open() as f:
            first = f.readline().strip()
        # Nur Syntax, keine Werte: Komma/Tab/Leerzeichen trennen Felder.
        separator = "," if "," in first else "\t" if "\t" in first else " "
        first_cols = first.split(",") if separator == "," else first.split()
        if first_cols and all(c.lower() in COLUMN_NAMES for c in first_cols):
            if parse_columns(",".join(first_cols)) != cols:
                raise ValueError("Spalten im Textheader widersprechen --columns")
            skip = 1
    elif columns is not None:
        raise ValueError("--columns ist nur für XYZ/TXT/CSV zulässig")
    return PointSource(
        path,
        fmt,
        reader,
        *(crs_sources[0][1::-1] if crs_sources else (None, "none")),
        units[0][1],
        units[0][0],
        up,
        cols,
        separator,
        skip,
        ph,
    )
