"""Generate synthetic SPZ-v3 with ten million Gaussians; bounded memory, no repo data.

Run with the import environment, output MUST be outside the repository.
No importer budget bypass: this is a stress fixture for the viewer's rejection/streaming gate.
"""
from __future__ import annotations

import argparse
import gzip
import json
import struct
import time
from pathlib import Path

import numpy as np


def generate(path: Path, count: int = 10_000_000, spacing: float = 0.1) -> dict[str, object]:
    root = Path(__file__).resolve().parents[2]
    if path.resolve().is_relative_to(root) or path.exists():
        raise ValueError("Output must be new and outside the repository")
    if not 1 <= count <= 15_000_000:
        raise ValueError("Count must be 1..15 million")
    if not np.isfinite(spacing) or not 0 < spacing <= 0.1:
        raise ValueError("Spacing must be >0 and <=0.1 m")
    start = time.monotonic()
    with path.open("xb") as dest, gzip.GzipFile(filename="", mode="wb", fileobj=dest,
                                               mtime=0, compresslevel=1) as f:
        f.write(struct.pack("<IIIBBBB", 0x5053474E, 3, count, 3, 12, 0, 0))
        for field in ("position", "opacity", "color", "scale", "rotation", "sh"):
            for offset in range(0, count, 65536):
                n = min(65536, count - offset)
                ix = np.arange(offset, offset + n, dtype=np.uint32)
                if field == "position":
                    # RUB disk: x in [-50,50), y in (-50,50], z negative/up to 10m.
                    xyz = np.column_stack([((ix % 1000).astype(float) - 500) * spacing,
                                           (500 - ((ix // 1000) % 1000).astype(float)) * spacing,
                                           -(ix // 1_000_000).astype(float)])
                    fixed = np.rint(xyz * 4096).astype(np.int32).astype(np.uint32)
                    raw = np.stack([fixed & 255, (fixed >> 8) & 255,
                                    (fixed >> 16) & 255], axis=-1).astype(np.uint8).tobytes()
                elif field == "opacity":
                    raw = bytes([230]) * n
                elif field == "color":
                    raw = np.column_stack([128 + ix % 25, 140 + ix % 10,
                                           130 + ix % 15]).astype(np.uint8).tobytes()
                elif field == "scale":
                    code = np.rint(np.array([112, 112, 100]) + 16 * np.log(spacing / 0.1))
                    if (code < 0).any():
                        raise ValueError("Spacing too small for SPZ scale encoding")
                    raw = code.astype(np.uint8).tobytes() * n
                elif field == "rotation":
                    raw = bytes(4) * n  # largest x=1, RUB Rx(pi)
                else:
                    values = np.full((n, 45), 128, dtype=np.uint8)
                    values[:, 0] = 140 + ix % 8
                    values[:, 8] = 120 + ix % 5
                    raw = values.tobytes()
                f.write(raw)
    return {"count": count, "spacingM": spacing, "shDegree": 3, "format": "spz",
            "bytes": path.stat().st_size,
            "rawBytes": 16 + 65 * count, "seconds": time.monotonic() - start}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--count", type=int, default=10_000_000)
    parser.add_argument("--spacing", type=float, default=0.1)
    args = parser.parse_args()
    print(json.dumps(generate(args.output, args.count, args.spacing)))
