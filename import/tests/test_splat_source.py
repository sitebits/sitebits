"""Dateien ausschließlich synthetisch: Position, Aktivierung und lokale Ausgabe."""

from pathlib import Path

import numpy as np
import pytest

from scanview_import.splat_source import Gaussians, read_gaussians, write_gaussians


def synthetic_gaussians(n: int = 1024) -> Gaussians:
    xy = np.array([(x / 100, y / 100, 0) for x in range(32) for y in range(32)])[:n]
    return Gaussians(
        xy + [500000, 5800000, 40],
        np.full((n, 3), 0.01),
        np.tile([0.0, 0, 0, 1], (n, 1)),
        np.full(n, 0.9),
        np.full((n, 3), 0.6),
    )


def test_binary_ply_roundtrip_large_world_coordinates(tmp_path: Path):
    g = synthetic_gaussians()
    path = tmp_path / "fixture.ply"
    origin = np.array([500000, 5800000, 40])
    write_gaussians(path, g, origin)
    out = read_gaussians(path)
    np.testing.assert_allclose(out.centers + origin, g.centers, atol=1e-6)
    np.testing.assert_allclose(out.scales, g.scales, rtol=1e-6)
    np.testing.assert_allclose(out.opacities, g.opacities, atol=1e-6)
    np.testing.assert_allclose(out.colors, g.colors, atol=1e-6)


def test_bad_splat_length_and_null_quaternion(tmp_path: Path):
    path = tmp_path / "fixture.splat"
    path.write_bytes(b"a" * 33)
    with pytest.raises(ValueError, match="Länge"):
        read_gaussians(path)
    path.write_bytes(b"\0" * 32)
    with pytest.raises(ValueError, match="Skalen"):
        read_gaussians(path)


def test_splat_binary_decodes_wxyz_color_and_opacity(tmp_path: Path):
    import struct

    path = tmp_path / "fixture.splat"
    path.write_bytes(
        struct.pack("<6f8B", 1, 2, 3, 0.1, 0.2, 0.3, 255, 128, 0, 200, 255, 128, 128, 128)
    )
    out = read_gaussians(path)
    np.testing.assert_allclose(out.centers, [[1, 2, 3]])
    np.testing.assert_allclose(out.scales, [[0.1, 0.2, 0.3]], rtol=1e-6)
    np.testing.assert_allclose(out.quaternions, [[0, 0, 0, 127 / 128]])
    assert out.opacities[0] == pytest.approx(200 / 255)


@pytest.mark.parametrize("version", [2, 3])
def test_spz_gzip_decodes_known_position_scale_quaternion(tmp_path: Path, version: int):
    import gzip
    import struct

    path = tmp_path / "fixture.spz"
    # xyz fixed24 = (4096,8192,12288), opacity=255, DC center=128, logscale=-2.
    pos = b"".join(int(v).to_bytes(3, "little", signed=True) for v in (4096, 8192, 12288))
    rotation = bytes([128, 128, 128]) if version == 2 else struct.pack("<I", 3 << 30)
    body = pos + bytes([255]) + bytes([128] * 3) + bytes([128] * 3) + rotation
    path.write_bytes(
        gzip.compress(struct.pack("<IIIBBBB", 0x5053474E, version, 1, 0, 12, 0, 0) + body)
    )
    out = read_gaussians(path)
    np.testing.assert_allclose(out.centers, [[1, -2, -3]], atol=1e-8)
    np.testing.assert_allclose(out.scales, [[np.exp(-2)] * 3])
    assert out.opacities[0] == 1


def test_spz_claimed_count_and_extensions_fail_closed(tmp_path: Path):
    import gzip
    import struct

    path = tmp_path / "fixture.spz"
    path.write_bytes(gzip.compress(struct.pack("<IIIBBBB", 0x5053474E, 3, 99999999, 0, 12, 0, 0)))
    with pytest.raises(ValueError, match="unterstützt"):
        read_gaussians(path)
