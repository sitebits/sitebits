"""Spark v2.3.0 RAD0/RADC leaf-position audit; no LoD parents used as evidence.

Format source: spark-lib/src/rad.rs at cce78e2cf5505679abbaf64236e0834de385010c.
Only the approved encoder's Float32 centers and chunked LoD output are accepted.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import time
import zlib
from pathlib import Path
from typing import Any

import numpy as np

from .convert import _sha256
from .splat_source import MAX_SPLATS, Gaussians, write_gaussians

BUILD_LOD_SHA256 = "a90040fa53a4830ea894905a53375501918e766b5d150dfde80440104e1c4fbe"
# 10 Mio. Gaussians brauchten 114 s; zwei Stunden fangen nur einen hängenden Lauf ab.
BUILD_LOD_TIMEOUT_S = 7200
BUILD_LOD_COMMIT = "cce78e2cf5505679abbaf64236e0834de385010c"


def _header(data: bytes, magic: bytes) -> tuple[dict[str, Any], int]:
    if len(data) < 8 or data[:4] != magic:
        raise ValueError("RAD: ungültiger Header")
    length = struct.unpack_from("<I", data, 4)[0]
    end = 8 + ((length + 7) // 8) * 8
    if length > 1_000_000 or end > len(data):
        raise ValueError("RAD: ungültige Metadatenlänge")
    meta = json.loads(data[8 : 8 + length])
    if not isinstance(meta, dict) or meta.get("version") != 1:
        raise ValueError("RAD: nur Version 1 unterstützt")
    return meta, end


def _property(data: bytes, payload: int, prop: dict[str, Any], length: int) -> bytes:
    offset, size = prop["offset"], prop["bytes"]
    if not isinstance(offset, int) or not isinstance(size, int) or offset < 0 or size < 0:
        raise ValueError("RAD: Property-Bereich ungültig")
    start = payload + offset
    if start + size > len(data):
        raise ValueError("RAD: Property abgeschnitten")
    raw = data[start : start + size]
    if prop.get("compression") == "gz":
        decoder = zlib.decompressobj(-15)  # miniz raw DEFLATE, not gzip framing.
        raw = decoder.decompress(raw, length + 1)
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("RAD: komprimierte Property ungültig/zu groß")
    elif prop.get("compression") is not None:
        raise ValueError("RAD: Kompression nicht unterstützt")
    if len(raw) != length:
        raise ValueError("RAD: Property-Länge widerspricht Anzahl")
    return raw


def _chunk_leaves(data: bytes, base: int, degree: int) -> tuple[np.ndarray, int]:
    cm, payload = _header(data, b"RADC")
    n = cm.get("count", 0)
    if (
        cm.get("base") != base
        or not isinstance(n, int)
        or not 0 < n <= 65536
        or cm.get("lodTree") is not True
        or cm.get("maxSh") != degree
    ):
        raise ValueError("RAD: Chunkanzahl/Basis/SH-Vertrag ungültig")
    if payload + 8 > len(data):
        raise ValueError("RAD: Payload fehlt")
    length = struct.unpack_from("<Q", data, payload)[0]
    payload += 8
    if payload + length != len(data) or cm.get("payloadBytes") != length:
        raise ValueError("RAD: Payload-Länge ungültig")
    props = {p["property"]: p for p in cm["properties"]}
    if not {"center", "child_count"} <= props.keys():
        raise ValueError("RAD: Blattkennzeichnung/Mittelpunkte fehlen")
    center = props["center"]
    raw = _property(data, payload, center, n * 12)
    if center["encoding"] == "f32_lebytes":
        raw = (
            np.frombuffer(raw, dtype=np.uint8).reshape(4, 3, n).transpose(1, 2, 0).copy().tobytes()
        )
    elif center["encoding"] != "f32":
        raise ValueError("RAD: nur Float32-Mittelpunkte unterstützt")
    centers = np.frombuffer(raw, dtype="<f4").reshape(3, n).T
    child = props["child_count"]
    if child["encoding"] != "u16":
        raise ValueError("RAD: Blattkodierung ungültig")
    children = np.frombuffer(_property(data, payload, child, n * 2), dtype="<u2")
    if not np.isfinite(centers).all():
        raise ValueError("RAD: nicht endliche Mittelpunkte")
    return centers[children == 0], n


def read_rad_leaves(path: Path) -> tuple[np.ndarray, dict[str, Any]]:
    if path.stat().st_size > 1_000_000:
        raise ValueError("RAD: Metadatenbudget überschritten")
    meta, end = _header(path.read_bytes(), b"RAD0")
    if meta.get("type") != "gsplat" or meta.get("lodTree") is not True:
        raise ValueError("RAD: gestufter gsplat-Vertrag fehlt")
    count = meta.get("count", 0)
    degree = meta.get("maxSh")
    if not isinstance(count, int) or not 0 < count <= MAX_SPLATS * 4 or degree not in (0, 1, 2, 3):
        raise ValueError("RAD: Anzahl/SH-Grad ungültig")
    chunks = meta.get("chunks", [])
    if not isinstance(chunks, list) or not 0 < len(chunks) <= (count + 65535) // 65536:
        raise ValueError("RAD: Chunkanzahl ungültig")
    leaves = []
    base = 0
    total = path.stat().st_size
    names = set()
    for item in chunks:
        name = item.get("filename")
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not name.endswith(".radc")
            or name in names
        ):
            raise ValueError("RAD: Chunkpfad ungültig")
        names.add(name)
        chunk = path.parent / name
        if chunk.is_symlink() or not chunk.is_file() or chunk.stat().st_size != item["bytes"]:
            raise ValueError("RAD: Chunk fehlt oder Größe falsch")
        if chunk.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("RAD: Chunkbudget überschritten")
        data = chunk.read_bytes()
        centers, n = _chunk_leaves(data, base, degree)
        leaves.append(centers)
        base += n
        total += len(data)
    if base != count or path.stat().st_size != end:
        raise ValueError("RAD: Gesamtanzahl/Dateilänge ungültig")
    result = np.concatenate(leaves)
    if len(result) > MAX_SPLATS:
        raise ValueError("RAD: Blattbudget überschritten")
    return result, {
        "chunkCount": len(chunks),
        "sizeBytes": total,
        "shDegree": degree,
        "lodCount": count,
        "firstChunkBytes": chunks[0]["bytes"],
        "rootBytes": path.stat().st_size,
    }


def verify_rad_positions(leaves: np.ndarray, expected: np.ndarray) -> dict[str, Any]:
    if len(leaves) != len(expected):
        raise ValueError("RAD: Blatt-Anzahl widerspricht Quelle (keine Entfernung erlaubt)")
    quantized = expected.astype(np.float32)
    if not np.isfinite(quantized).all():
        raise ValueError("RAD: Float32-Positionsbereich überschritten")
    # Sorting the quantized multiset verifies multiplicities, unlike nearest-neighbor matching.
    order = np.lexsort(quantized.T[::-1])
    actual = leaves[np.lexsort(leaves.T[::-1])]
    if not np.array_equal(actual, quantized[order]):
        raise ValueError("RAD: Positionsmultimenge widerspricht transformierter Quelle")
    error = float(np.linalg.norm(actual.astype(float) - expected[order], axis=1).max())
    half_ulp = np.abs(np.spacing(np.max(np.abs(quantized), axis=0))) / 2
    tolerance = float(np.linalg.norm(half_ulp)) + 1e-8
    if error > tolerance:
        raise ValueError("RAD: Positionsfehler über Float32-Toleranz")
    return {
        "positionMaxM": error,
        "positionToleranceM": tolerance,
        "positionEncoding": "f32_lebytes",
        "positionToleranceRule": "sqrt(sum((0.5*ulp32(max(abs(localAxis))))^2)) + 1e-8 m",
        "positionCheck": "quantized-multiset-all-leaves",
        "leafCount": len(leaves),
    }


def build_rad(
    dest: Path, g: Gaussians, origin: np.ndarray, tool: Path
) -> tuple[np.ndarray, dict[str, Any]]:
    tool = tool.resolve()
    dest = dest.resolve()  # build-lod läuft mit cwd=dest; relative Pfade würden doppelt aufgelöst.
    if not tool.is_file() or not os.access(tool, os.X_OK):
        raise ValueError(
            "build-lod fehlt/nicht ausführbar: --build-lod oder SCANVIEW_BUILD_LOD setzen"
        )
    digest = _sha256(tool)
    if digest != BUILD_LOD_SHA256:
        raise ValueError("build-lod: SHA256 entspricht nicht dem freigegebenen v2.3.0-Binary")
    # PLY keeps package axes and full Float32 positions, avoiding SPZ's RUB/fixed24 conversion.
    source = dest / "model.ply"
    write_gaussians(source, g, origin)
    start = time.monotonic()
    log = dest / "build-lod.log"
    with log.open("w") as stream:
        try:
            subprocess.run(
                [str(tool), "--quality", "--max-sh=3", "--rad-chunked", str(source)],
                cwd=dest,
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=BUILD_LOD_TIMEOUT_S,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            # Das Log liegt im Arbeitsordner und wird beim Aufräumen gelöscht: Ursache mitnehmen.
            stream.flush()
            tail = log.read_text(errors="replace")[-2000:]
            raise ValueError(f"build-lod fehlgeschlagen ({exc}):\n{tail}") from exc
    generated = dest / "model-lod.rad"
    leaves, metadata = read_rad_leaves(generated)
    audit = verify_rad_positions(leaves, g.centers - origin)
    if metadata["shDegree"] != g.sh_degree:
        raise ValueError("RAD: SH-Grad widerspricht Quelle")
    source.unlink()  # Only our temporary intermediate; never the user's source.
    return leaves + origin, {
        **metadata,
        **audit,
        "buildSeconds": time.monotonic() - start,
        "lodMethod": "bhatt-lod",
        "buildLod": {
            "tool": "build-lod",
            "tag": "v2.3.0",
            "commit": BUILD_LOD_COMMIT,
            "sha256": digest,
            "license": "MIT",
            "source": "https://github.com/sparkjsdev/spark/tree/v2.3.0/rust/build-lod",
        },
    }
