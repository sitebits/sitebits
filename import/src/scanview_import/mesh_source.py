"""Mesh-Leser: Float64 im metrischen Z-oben-Rahmen, UV je Grafikvertex."""

from __future__ import annotations

import shlex
import struct
from array import array
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .point_source import PLY_TYPES, PointSource, read_sidecar, resolve_declarations


@dataclass
class Mesh:
    positions: np.ndarray
    triangles: np.ndarray
    uv: np.ndarray | None
    colors: np.ndarray | None
    materials: np.ndarray
    textures: list[Path | bytes | None]
    source: PointSource
    source_vertex_count: int | None = None

    def validate(self) -> Mesh:
        if not len(self.positions) or not len(self.triangles):
            raise ValueError("Mesh ohne Dreiecke")
        if not np.isfinite(self.positions).all():
            raise ValueError("Mesh mit nicht-endlichen Positionen")
        if self.triangles.min() < 0 or self.triangles.max() >= len(self.positions):
            raise ValueError("Mesh-Index außerhalb des Vertexpuffers")
        for a in (self.uv, self.colors):
            if a is not None and (len(a) != len(self.positions) or not np.isfinite(a).all()):
                raise ValueError("Ungültige Mesh-Attribute")
        return self


def resolve_mesh_source(
    path: Path, crs: str | None = None, unit: str | None = None, up: str | None = None
) -> PointSource:
    path = path.resolve()
    fmt = path.suffix.lower().lstrip(".")
    if fmt not in ("obj", "ply", "stl", "gltf", "glb"):
        raise ValueError(f"Nicht unterstütztes Meshformat: {fmt}")
    side = read_sidecar(path)
    gltf = fmt in ("glb", "gltf")
    crss, units = resolve_declarations(side, crs, unit, [], [("file", "m")] if gltf else [])
    axis = "y" if gltf else side.get("up", "z")
    if axis not in ("y", "z") or (up is not None and up not in ("y", "z")):
        raise ValueError("Achse oben muss y oder z sein")
    if up is not None:
        if (gltf or "up" in side) and axis != up:
            raise ValueError("Achsen aus Format, Sidecar und CLI widersprechen sich")
        axis = up
    return PointSource(
        path,
        fmt,
        "",
        crss[0][1] if crss else None,
        crss[0][0] if crss else "none",
        units[0][1],
        units[0][0],
        axis,
    )


def is_mesh(path: Path) -> bool:
    if path.suffix.lower() in (".obj", ".stl", ".gltf", ".glb"):
        return True
    if path.suffix.lower() == ".ply":
        with path.open("rb") as f:
            for _ in range(4096):
                line = f.readline(4096)
                if line.startswith(b"element face "):
                    return int(line.split()[2]) > 0
                if line.strip() == b"end_header" or not line:
                    break
    return False


def read_mesh(
    path: Path, crs: str | None = None, unit: str | None = None, up: str | None = None
) -> Mesh:
    source = resolve_mesh_source(path, crs, unit, up)
    if source.format in ("gltf", "glb"):
        from .mesh_gltf import read_gltf

        return read_gltf(source).validate()
    mesh = {"obj": _obj, "stl": _stl, "ply": _ply}[source.format](source)
    mesh.positions = source.transform(mesh.positions)
    return mesh.validate()


def _obj(source: PointSource) -> Mesh:
    # Zwei Streaming-Durchgänge, kompakte Array-Puffer statt Millionen Python-Objekte.
    counts = dict.fromkeys(("v", "vt", "vn"), 0)
    ntri = 0
    mtls: list[str] = []
    with source.path.open() as f:
        for line in f:
            parts = line.split()
            if not parts or parts[0].startswith("#"):
                continue
            tag = parts[0]
            if tag in counts:
                counts[tag] += 1
            elif tag == "f":
                ntri += max(0, len(parts) - 3)
            elif tag == "mtllib":
                mtls.extend(shlex.split(line)[1:])
    positions = np.empty((counts["v"], 3), dtype=np.float64)
    colors = np.ones((counts["v"], 3), dtype=np.float32)
    texcoord = np.empty((counts["vt"], 2), dtype=np.float32)
    refs = np.empty((ntri, 3, 3), dtype=np.int64)
    mats = np.empty(ntri, dtype=np.int32)
    textures: list[Path | bytes | None] = [None]
    names = {"": 0}
    for mtl in mtls:
        mp = source.path.parent / mtl
        if not mp.is_file():
            raise ValueError(f"Materialdatei fehlt: {mtl}")
        current = ""
        for line in mp.read_text().splitlines():
            parts = shlex.split(line, comments=True)
            if not parts:
                continue
            if parts[0] == "newmtl":
                current = " ".join(parts[1:])
                names[current] = len(textures)
                textures.append(None)
            elif parts[0] == "map_Kd":
                if not current or len(parts) < 2 or parts[1].startswith("-"):
                    raise ValueError(f"Nicht unterstützte map_Kd-Option in {mtl}")
                texture = mp.parent / " ".join(parts[1:])
                if not texture.is_file():
                    raise ValueError(f"Textur fehlt: {texture.name}")
                textures[names[current]] = texture
    seen = dict.fromkeys(counts, 0)
    tri, material, has_color, has_uv = 0, 0, False, False
    with source.path.open() as f:
        for line in f:
            parts = line.split("#", 1)[0].split()
            if not parts:
                continue
            tag = parts[0]
            if tag == "v":
                vals = [float(x) for x in parts[1:]]
                if len(vals) not in (3, 4, 6):
                    raise ValueError("OBJ: ungültige v-Zeile")
                positions[seen["v"]] = vals[:3]
                if len(vals) == 6:
                    colors[seen["v"]] = vals[3:]
                    has_color = True
                seen["v"] += 1
            elif tag == "vt":
                texcoord[seen["vt"]] = [float(x) for x in parts[1:3]]
                seen["vt"] += 1
            elif tag == "vn":
                seen["vn"] += 1
            elif tag == "usemtl":
                name = " ".join(parts[1:])
                if name not in names:
                    raise ValueError(f"OBJ: unbekanntes Material {name}")
                material = names[name]
            elif tag == "f":
                corners = []
                for token in parts[1:]:
                    indices = token.split("/")
                    ref = []
                    for j, kind in enumerate(("v", "vt", "vn")):
                        if j >= len(indices) or not indices[j]:
                            ref.append(-1)
                        else:
                            ix = int(indices[j])
                            value = ix - 1 if ix > 0 else seen[kind] + ix
                            if ix == 0 or value < 0 or value >= counts[kind]:
                                raise ValueError("OBJ: Index außerhalb des Puffers")
                            ref.append(value)
                    if ref[0] < 0:
                        raise ValueError("OBJ: Position fehlt")
                    has_uv |= ref[1] >= 0
                    corners.append(ref)
                if len(corners) != 3:
                    raise ValueError("OBJ: nur triangulierte Flächen unterstützt")
                refs[tri] = corners
                mats[tri] = material
                tri += 1
    unique, inverse = np.unique(refs.reshape(-1, 3), axis=0, return_inverse=True)
    if has_uv and np.any(unique[:, 1] < 0):
        raise ValueError("OBJ: unvollständige UV-Koordinaten")
    uv = texcoord[unique[:, 1]].copy() if has_uv else None
    if uv is not None:
        uv[:, 1] = 1 - uv[:, 1]  # intern glTF-UV, v nach unten
    if any(t is not None for t in textures) and uv is None:
        raise ValueError("Texturiertes OBJ ohne UV")
    return Mesh(
        positions[unique[:, 0]],
        inverse.reshape(-1, 3).astype(np.uint32),
        uv,
        colors[unique[:, 0]] if has_color else None,
        mats,
        textures,
        source,
        source_vertex_count=counts["v"],
    )


def _stl(source: PointSource) -> Mesh:
    p = source.path
    with p.open("rb") as f:
        header = f.read(84)
    count = struct.unpack("<I", header[80:84])[0] if len(header) == 84 else 0
    if p.stat().st_size == 84 + count * 50:
        dtype = np.dtype([("normal", "<f4", (3,)), ("xyz", "<f4", (3, 3)), ("attr", "<u2")])
        data = np.memmap(p, dtype=dtype, mode="r", offset=84, shape=(count,))
        xyz = np.array(data["xyz"].reshape(-1, 3), dtype=np.float64)
    else:
        values = array("d")
        with p.open() as f:
            for line in f:
                parts = line.split()
                if parts and parts[0] == "vertex":
                    if len(parts) != 4:
                        raise ValueError("STL: ungültiger Vertex")
                    values.extend(float(x) for x in parts[1:])
        xyz = np.asarray(values).reshape(-1, 3)
        if len(xyz) % 3:
            raise ValueError("STL: unvollständiges Dreieck")
    positions, inv = np.unique(xyz, axis=0, return_inverse=True)
    faces = inv.reshape(-1, 3).astype(np.uint32)
    return Mesh(positions, faces, None, None, np.zeros(len(faces), dtype=np.int32), [None], source)


def _ply(source: PointSource) -> Mesh:
    elements: list[tuple[str, int, list[list[str]]]] = []
    encoding = ""
    with source.path.open("rb") as f:
        if f.readline().strip() != b"ply":
            raise ValueError("Ungültiges PLY")
        for _ in range(4096):
            parts = f.readline(4096).decode("ascii").split()
            if not parts:
                raise ValueError("Unvollständiges PLY")
            if parts[0] == "format":
                encoding = parts[1]
            elif parts[0] == "element":
                elements.append((parts[1], int(parts[2]), []))
            elif parts[0] == "property":
                elements[-1][2].append(parts[1:])
            elif parts[0] == "end_header":
                break
        offset = f.tell()
        if [e[0] for e in elements] != ["vertex", "face"]:
            raise ValueError("Mesh-PLY benötigt vertex und face in dieser Reihenfolge")
        _, n, props = elements[0]
        if any(len(prop) != 2 or prop[0] not in PLY_TYPES for prop in props):
            raise ValueError("Nicht unterstützte PLY-Vertexattribute")
        endian = ">" if encoding == "binary_big_endian" else "<"
        dtype = np.dtype([(name, endian + PLY_TYPES[typ]) for typ, name in props])
        if encoding == "ascii":
            records = np.empty(n, dtype=dtype)
            for i in range(n):
                record_vals = f.readline().decode().split()
                records[i] = tuple(record_vals)
        elif encoding in ("binary_little_endian", "binary_big_endian"):
            records = np.memmap(source.path, dtype=dtype, mode="r", offset=offset, shape=(n,))
            f.seek(offset + n * dtype.itemsize)
        else:
            raise ValueError("Nicht unterstützte PLY-Kodierung")
        faceprops = elements[1][2]
        if len(faceprops) != 1 or faceprops[0][0] != "list":
            raise ValueError("PLY: nur vertex_indices als Face-Attribut unterstützt")
        _, counttype, indextype, _ = faceprops[0]
        faces = np.empty((elements[1][1], 3), dtype=np.uint32)
        for i in range(len(faces)):
            if encoding == "ascii":
                face_vals = [int(x) for x in f.readline().split()]
                face_size = face_vals[0]
                indices = np.asarray(face_vals[1:])
            else:
                ct = np.dtype(endian + PLY_TYPES[counttype])
                it = np.dtype(endian + PLY_TYPES[indextype])
                face_size = int(np.frombuffer(f.read(ct.itemsize), ct)[0])
                indices = np.frombuffer(f.read(face_size * it.itemsize), it)
            if face_size != 3 or len(indices) != 3 or any(x < 0 for x in indices):
                raise ValueError("PLY: nur triangulierte Flächen unterstützt")
            faces[i] = indices
        xyz = np.column_stack([records[c] for c in ("x", "y", "z")]).astype(np.float64)
        colors = None
        if all(c in (dtype.names or ()) for c in ("red", "green", "blue")):
            colors = np.column_stack([records[c] for c in ("red", "green", "blue")])
            if colors.dtype.kind == "u":
                colors = colors / np.iinfo(colors.dtype).max
            colors = colors.astype(np.float32)
        uv = None
        if all(c in (dtype.names or ()) for c in ("s", "t")):
            uv = np.column_stack([records[c] for c in ("s", "t")]).astype(np.float32)
            uv[:, 1] = 1 - uv[:, 1]
    return Mesh(xyz, faces, uv, colors, np.zeros(len(faces), dtype=np.int32), [None], source)
