import io
import os
import subprocess

import numpy as np
import pytest
from PIL import Image

from scanview_import import mesh_textures
from scanview_import.mesh_textures import decode_texture, encode_texture, ktx_info, sample_rgb


@pytest.fixture(autouse=True)
def clear_pdal_environment(monkeypatch):
    for name in ("SCANVIEW_PDAL_MEMORY", "SCANVIEW_PDAL_CPUS", "SCANVIEW_PDAL_LABEL"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("configured", [False, True])
def test_ktx_run_docker_limits_and_isolation(monkeypatch, tmp_path, configured):
    if configured:
        monkeypatch.setenv("SCANVIEW_PDAL_MEMORY", "12g")
        monkeypatch.setenv("SCANVIEW_PDAL_CPUS", "0.5")
        monkeypatch.setenv("SCANVIEW_PDAL_LABEL", "test_01.v2")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        (tmp_path / "peak").write_text("2048")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(mesh_textures.subprocess, "run", run)
    stats = mesh_textures._run(["toktx", "--version"], tmp_path)
    args, = calls
    memory = "12g" if configured else "40g"
    assert f"--memory={memory}" in args
    assert f"--memory-swap={memory}" in args
    assert args[args.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert args[args.index("--network") + 1] == "none"
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert args[args.index("--security-opt") + 1] == "no-new-privileges"
    assert "--read-only" in args
    assert f"{tmp_path.resolve()}:/work:rw" in args
    assert args[-2:] == ["toktx", "--version"]
    assert stats["peakRssMb"] == 2
    if configured:
        assert "--cpus=0.5" in args
        assert args[args.index("--label") + 1] == "scanview.lauf=test_01.v2"
    else:
        assert not any(arg.startswith("--cpus") for arg in args)
        assert "--label" not in args


@pytest.mark.parametrize(
    "name,value",
    [("SCANVIEW_PDAL_MEMORY", "12G"), ("SCANVIEW_PDAL_CPUS", "0"),
     ("SCANVIEW_PDAL_LABEL", "test label")],
)
def test_ktx_run_rejects_invalid_limits_before_subprocess(monkeypatch, tmp_path, name, value):
    monkeypatch.setenv(name, value)

    def unexpected_run(*args, **kwargs):
        pytest.fail("Ungültige Docker-Grenzen dürfen keinen Prozess starten")

    monkeypatch.setattr(mesh_textures.subprocess, "run", unexpected_run)
    with pytest.raises(ValueError, match=name):
        mesh_textures._run(["toktx", "--version"], tmp_path)


@pytest.mark.docker
@pytest.mark.parametrize("encoding,max_px", [("uastc", 64), ("etc1s", 32)])
def test_real_ktx_roundtrip(encoding, max_px):
    x, y = np.meshgrid(np.arange(128, dtype=np.uint8), np.arange(64, dtype=np.uint8))
    pixels = np.stack([x * 2, y * 4, x], axis=-1)
    image = Image.fromarray(pixels)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    data, report = encode_texture(buf.getvalue(), max_px, encoding)
    assert report["encoding"] == "ktx2-" + encoding
    assert max(report["width"], report["height"]) <= max_px
    assert report["uastcRdoLambda"] == (2.0 if encoding == "uastc" else None)
    assert report["zstdLevel"] == (9 if encoding == "uastc" else None)
    assert report["levels"] > 1
    assert report["peakRssMb"] > 0
    decoded = decode_texture(data)
    uv = np.random.default_rng(42).random((1000, 2))
    assert np.abs(sample_rgb(image, uv) - sample_rgb(decoded, uv)).mean() < 10
    assert decoded.size == (max_px, max_px // 2)
    assert ktx_info(data)["encoding"] == "ktx2-" + encoding
