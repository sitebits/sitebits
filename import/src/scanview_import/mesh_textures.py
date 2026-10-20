"""KTX2 kodieren und für echte UV-Farbprüfung wieder dekodieren (Docker)."""

from __future__ import annotations

import io
import os
import struct
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, cast

import numpy as np
from PIL import Image

from .pdal_runner import docker_grenzen

KTX_IMAGE = "sha256:36a148dcd76049e9cf0a199324d144a9284f2444e6c7229a9745a0f2273ad3e2"
KTX_MAGIC = b"\xabKTX 20\xbb\r\n\x1a\n"


def ktx_info(data: bytes) -> dict[str, Any]:
    if not data.startswith(KTX_MAGIC) or len(data) < 80:
        raise ValueError("Ungültige KTX2-Textur")
    _, _, width, height, _, _, _, levels, compression = struct.unpack("<9I", data[12:48])
    dfd_offset, dfd_length = struct.unpack("<2I", data[48:56])
    if dfd_length < 16 or dfd_offset + dfd_length > len(data):
        raise ValueError("KTX2 ohne vollständigen Data Format Descriptor")
    model = data[dfd_offset + 12]
    encoding = {163: "ktx2-etc1s", 166: "ktx2-uastc"}.get(model)
    if encoding is None:
        raise ValueError(f"KTX2: nicht unterstütztes Farbmodell {model}")
    return {
        "width": width,
        "height": height,
        "levels": levels,
        "encoding": encoding,
        "supercompression": compression,
    }


def _run(command: list[str], work: Path) -> dict[str, Any]:
    # Python-Wrapper misst den Kindprozess im Container, wie beim PDAL-Lauf.
    wrapper = (
        "import resource,subprocess,sys; "
        "rc=subprocess.call(sys.argv[1:]); "
        "open('/work/peak','w').write(str(resource.getrusage(resource.RUSAGE_CHILDREN)"
        ".ru_maxrss)); sys.exit(rc)"
    )
    args = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        *docker_grenzen(),
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=2g",
        "-v",
        f"{work.resolve()}:/work:rw",
        KTX_IMAGE,
        "python",
        "-c",
        wrapper,
        *command,
    ]
    t0 = time.monotonic()
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"KTX-Prozess fehlgeschlagen: {result.stderr[-2000:]}")
    return {
        "seconds": time.monotonic() - t0,
        "peakRssMb": float((work / "peak").read_text()) / 1024,
        "imageDigest": KTX_IMAGE,
    }


def decode_texture(texture: Path | bytes) -> Image.Image:
    data = texture.read_bytes() if isinstance(texture, Path) else texture
    if data.startswith(KTX_MAGIC):
        with tempfile.TemporaryDirectory(prefix="mesh-ktx-") as td:
            work = Path(td)
            (work / "input.ktx2").write_bytes(data)
            _run(
                ["ktx", "extract", "--transcode", "rgba8", "/work/input.ktx2", "/work/decoded.png"],
                work,
            )
            return Image.open(work / "decoded.png").convert("RGBA")
    return Image.open(io.BytesIO(data)).convert("RGBA")


def encode_texture(
    texture: Path | bytes, max_px: int, encoding: str,
) -> tuple[bytes, dict[str, Any]]:
    if encoding not in ("uastc", "etc1s"):
        raise ValueError("KTX-Kodierung muss uastc oder etc1s sein")
    image = decode_texture(texture)
    source_size = list(image.size)
    image.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    with tempfile.TemporaryDirectory(prefix="mesh-ktx-") as td:
        work = Path(td)
        image.save(work / "input.png")
        codec_options = (
            ["--zcmp", "9", "--uastc_rdo_l", "2", "--uastc_rdo_m"]
            if encoding == "uastc"
            else ["--qlevel", "255"]
        )
        stats = _run(
            [
                "toktx",
                "--t2",
                "--encode",
                encoding,
                "--genmipmap",
                *codec_options,
                "--threads",
                "4",
                "--assign_oetf",
                "srgb",
                "--assign_primaries",
                "bt709",
                "--upper_left_maps_to_s0t0",
                "/work/output.ktx2",
                "/work/input.png",
            ],
            work,
        )
        data = (work / "output.ktx2").read_bytes()
    info = ktx_info(data)
    if max(info["width"], info["height"]) > max_px or info["encoding"] != "ktx2-" + encoding:
        raise RuntimeError("KTX2-Ausgabe verletzt Texturvertrag")
    return data, {
        **info,
        **stats,
        "sourcePixels": source_size,
        "uastcRdoLambda": 2.0 if encoding == "uastc" else None,
        "zstdLevel": 9 if encoding == "uastc" else None,
    }


def sample_rgb(image: Image.Image, uv: np.ndarray) -> np.ndarray:
    pixels = np.asarray(image.convert("RGB"))
    uv = np.mod(uv, 1)
    x = np.minimum((uv[:, 0] * image.width).astype(int), image.width - 1)
    y = np.minimum((uv[:, 1] * image.height).astype(int), image.height - 1)
    return cast(np.ndarray, pixels[y, x].astype(np.float64))
