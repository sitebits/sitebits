"""RAD leaves, multiset preservation and rejection of malformed output."""

import json
import struct
import zlib

import numpy as np
import pytest
from test_splat_source import synthetic_gaussians

from scanview_import.splat_rad import read_rad_leaves, verify_rad_positions


def rad_fixture(root, centers, children=None):
    n = len(centers)
    children = np.zeros(n, dtype="<u2") if children is None else children
    values = [
        ("<center>", centers.astype("<f4").T.tobytes()),
        ("child_count", children.astype("<u2").tobytes()),
    ]
    payload = bytearray()
    properties = []
    for name, raw in values:
        data = zlib.compress(raw)[2:-4]  # miniz raw DEFLATE, despite the label "gz".
        properties.append(
            {
                "property": "center" if name == "<center>" else name,
                "encoding": "f32" if name == "<center>" else "u16",
                "compression": "gz",
                "offset": len(payload),
                "bytes": len(data),
            }
        )
        payload.extend(data)
        payload.extend(bytes((-len(data)) % 8))
    meta = json.dumps(
        {
            "version": 1,
            "base": 0,
            "count": n,
            "maxSh": 0,
            "lodTree": True,
            "payloadBytes": len(payload),
            "properties": properties,
        }
    ).encode()
    chunk = b"RADC" + struct.pack("<I", len(meta)) + meta + bytes((-len(meta)) % 8)
    chunk += struct.pack("<Q", len(payload)) + payload
    (root / "model-0.radc").write_bytes(chunk)
    meta = json.dumps(
        {
            "version": 1,
            "type": "gsplat",
            "count": n,
            "maxSh": 0,
            "lodTree": True,
            "chunks": [{"filename": "model-0.radc", "bytes": len(chunk)}],
        }
    ).encode()
    path = root / "model.rad"
    path.write_bytes(b"RAD0" + struct.pack("<I", len(meta)) + meta + bytes((-len(meta)) % 8))
    return path


def test_only_leaves_are_checked_and_order_is_irrelevant(tmp_path):
    g = synthetic_gaussians()
    origin = g.centers.min(0)
    centers = np.vstack([[999, 999, 999], (g.centers - origin)[::-1]])
    path = rad_fixture(tmp_path, centers, np.array([2] + [0] * len(g.centers)))
    leaves, meta = read_rad_leaves(path)
    assert len(leaves) == len(g.centers)
    report = verify_rad_positions(leaves, g.centers - origin)
    assert report["positionMaxM"] <= report["positionToleranceM"]
    assert meta["chunkCount"] == 1
    assert meta["sizeBytes"] == sum(p.stat().st_size for p in tmp_path.iterdir())


def test_shift_and_duplicate_with_missing_leaf_fail(tmp_path):
    g = synthetic_gaussians()
    leaves, _ = read_rad_leaves(rad_fixture(tmp_path, g.centers))
    with pytest.raises(ValueError, match="Positions"):
        verify_rad_positions(leaves + [0.01, 0, 0], g.centers)
    broken = leaves.copy()
    broken[0] = broken[-1]
    with pytest.raises(ValueError, match="Positions"):
        verify_rad_positions(broken, g.centers)


def test_unsafe_chunk_path_and_missing_chunk_fail(tmp_path):
    path = rad_fixture(tmp_path, np.zeros((2, 3)))
    data = path.read_bytes()
    meta = json.loads(data[8 : 8 + struct.unpack("<I", data[4:8])[0]])
    meta["chunks"][0]["filename"] = "../outside.radc"
    encoded = json.dumps(meta).encode()
    path.write_bytes(
        b"RAD0" + struct.pack("<I", len(encoded)) + encoded + bytes((-len(encoded)) % 8)
    )
    with pytest.raises(ValueError, match="Chunk"):
        read_rad_leaves(path)


def test_wrong_leaf_count_fails():
    g = synthetic_gaussians()
    with pytest.raises(ValueError, match="Anzahl"):
        verify_rad_positions(g.centers[:-1], g.centers)


def test_build_lod_failure_reports_log_and_relative_package_path(tmp_path, monkeypatch):
    import hashlib
    from pathlib import Path

    from scanview_import import splat_rad

    # Fake encoder: proves the source path resolves from cwd=dest, then fails with a log line.
    tool = tmp_path / "build-lod"
    tool.write_text(
        '#!/bin/sh\ntest -f "$4" || { echo "Quelle fehlt: $4"; exit 3; }\n'
        'echo "kaputt im Encoder"\nexit 1\n'
    )
    tool.chmod(0o755)
    digest = hashlib.sha256(tool.read_bytes()).hexdigest()
    monkeypatch.setattr(splat_rad, "BUILD_LOD_SHA256", digest)
    (tmp_path / "pkg" / "work").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    g = synthetic_gaussians()
    with pytest.raises(ValueError, match="kaputt im Encoder"):
        splat_rad.build_rad(Path("pkg/work"), g, np.zeros(3), tool)
