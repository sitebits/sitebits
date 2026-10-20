"""SPZ v2/v3 (gzip, RUB) gemäß Niantic-Format, ohne zusätzliche Bibliothek.

Formatreferenz: https://github.com/nianticlabs/spz (MIT), load-spz.cc.
SPZ RUB → Trainer RDF ist diag(1,-1,-1); Weltlage bleibt ausdrücklich zu belegen.
"""

from __future__ import annotations

import gzip
import struct
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from .splat_source import C0, MAX_SOURCE_BYTES, MAX_SPLATS, Gaussians


def read_spz(path: Path) -> Gaussians:
    with gzip.open(path, "rb") as f:
        header = f.read(16)
        if len(header) != 16:
            raise ValueError("SPZ: Header fehlt")
        magic, version, n, degree, bits, flags, reserved = struct.unpack("<IIIBBBB", header)
        if (
            magic != 0x5053474E
            or version not in (2, 3)
            or not 0 < n <= MAX_SPLATS
            or degree > 3
            or bits > 24
            or flags != 0
            or reserved != 0
        ):
            raise ValueError("SPZ: nur v2/v3 ohne Erweiterungen/Antialiasing unterstützt")
        length = n * (9 + 1 + 3 + 3 + (3 if version == 2 else 4) + 3 * ((degree + 1) ** 2 - 1))
        if length > MAX_SOURCE_BYTES:
            raise ValueError("SPZ: dekodiertes Budget überschritten")
        body = f.read(length + 1)
        if len(body) != length:
            raise ValueError("SPZ: Länge widerspricht Header")
    at = 0

    def take(count: int) -> np.ndarray:
        nonlocal at
        a = np.frombuffer(body, dtype=np.uint8, count=count, offset=at)
        at += count
        return a

    raw = take(n * 9).reshape(n, 3, 3).astype(np.int32)
    fixed = raw[:, :, 0] | raw[:, :, 1] << 8 | raw[:, :, 2] << 16
    fixed = np.where(fixed & 0x800000, fixed - 0x1000000, fixed)
    centers = fixed.astype(float) / (2**bits)
    opacity = take(n).astype(float) / 255
    colors = C0 * (take(n * 3).reshape(n, 3).astype(float) / 255 - 0.5) / 0.15 + 0.5
    scales = np.exp(take(n * 3).reshape(n, 3).astype(float) / 16 - 10)
    if version == 2:
        xyz = take(n * 3).reshape(n, 3).astype(float) / 127.5 - 1
        q = np.column_stack([xyz, np.sqrt(np.maximum(0, 1 - np.sum(xyz * xyz, axis=1)))])
    else:
        rawq = take(n * 4).reshape(n, 4).astype(np.uint32)
        packed = rawq[:, 0] | rawq[:, 1] << 8 | rawq[:, 2] << 16 | rawq[:, 3] << 24
        largest = packed >> 30
        q = np.zeros((n, 4))
        for j in range(4):
            selected = largest == j
            components = [k for k in range(4) if k != j]
            for k, shift in zip(components, (20, 10, 0), strict=True):
                field = (packed[selected] >> shift) & 1023
                q[selected, k] = (
                    (field & 511).astype(float) / 511 / np.sqrt(2) * np.where(field & 512, -1, 1)
                )
            q[selected, j] = np.sqrt(np.maximum(0, 1 - np.sum(q[selected] ** 2, axis=1)))
    from .splat_sh import rotate_sh

    sh = (take(n * 3 * ((degree + 1) ** 2 - 1)).reshape(n, -1, 3).astype(float) - 128) / 128
    flip = Rotation.from_euler("x", 180, degrees=True)
    return Gaussians(
        flip.apply(centers),
        scales,
        (flip * Rotation.from_quat(q)).as_quat(),
        opacity,
        colors,
        degree,
        rotate_sh(sh, flip.as_matrix(), degree),
    )


FRACTIONAL_BITS = 12
# Round-to-nearest fixed24: half a step per axis, Euclidean bound + Float64 world-add slack.
POSITION_TOLERANCE_M = np.sqrt(3) / (2 * 2**FRACTIONAL_BITS) + 1e-8


def write_spz(path: Path, g: Gaussians, origin: np.ndarray) -> None:
    """Native v3 encoder, RUB on disk; reject unrepresentable fields, never silently clip.

    Quantization: position step 2^-12 m, ln(scale) 1/16, SH 1/128, opacity 1/255.
    Reference: Niantic SPZ load-spz.cc, smallest-three sign/magnitude quaternion.
    """
    from .splat_sh import rotate_sh

    g.validate()
    flip = Rotation.from_euler("x", 180, degrees=True)
    centers = flip.apply(g.centers - origin)
    fixed = np.rint(centers * 2**FRACTIONAL_BITS)
    if (fixed < -0x800000).any() or (fixed > 0x7FFFFF).any():
        raise ValueError("SPZ: Positionsbereich ±2048 m relativ zum Ursprung überschritten")
    scales = np.rint((np.log(g.scales) + 10) * 16)
    colors = np.rint(((g.colors - 0.5) / C0 * 0.15 + 0.5) * 255)
    sh = (rotate_sh(g.sh_rest, flip.as_matrix(), g.sh_degree)
          if g.sh_rest is not None else np.empty((len(centers), 0, 3)))
    sh_bytes = np.rint(sh * 128) + 128
    for field in (scales, colors, sh_bytes):
        if (field < 0).any() or (field > 255).any():
            raise ValueError("SPZ: Skalen/Farben/SH außerhalb darstellbarem Bereich; PLY wählen")
    q = (flip * Rotation.from_quat(g.quaternions)).as_quat()
    largest = np.argmax(np.abs(q), axis=1)
    q *= np.where(q[np.arange(len(q)), largest] < 0, -1, 1)[:, None]
    packed = largest.astype(np.uint32) << 30
    for j in range(4):
        selected = largest == j
        components = [k for k in range(4) if k != j]
        for k, shift in zip(components, (20, 10, 0), strict=True):
            value = q[selected, k]
            code = np.rint(np.abs(value) * np.sqrt(2) * 511).astype(np.uint32)
            code |= np.where(value < 0, 512, 0).astype(np.uint32)
            packed[selected] |= code << shift
    raw = fixed.astype(np.int32).astype(np.uint32)
    positions = np.stack([raw & 255, (raw >> 8) & 255, (raw >> 16) & 255], axis=-1)
    with path.open("wb") as dest, gzip.GzipFile(filename="", mode="wb", fileobj=dest, mtime=0) as f:
        f.write(struct.pack("<IIIBBBB", 0x5053474E, 3, len(centers),
                            g.sh_degree, FRACTIONAL_BITS, 0, 0))
        for field in (positions, np.rint(g.opacities * 255), colors, scales):
            f.write(field.astype(np.uint8).tobytes())
        f.write(packed.astype("<u4").tobytes())
        f.write(sh_bytes.astype(np.uint8).tobytes())
