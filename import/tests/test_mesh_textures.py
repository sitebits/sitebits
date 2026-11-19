import io
import os
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest
from image_lock import from_zeilen_per_digest, lock_eintraege
from PIL import Image

from scanview_import import mesh_textures, pdal_runner
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
    bild_id = "sha256:" + "a" * 64
    monkeypatch.setattr(mesh_textures, "ensure_ktx_image", lambda: bild_id)
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
    # Gestartet wird die geprüfte Image-ID, und die steht als imageDigest im Bericht.
    assert args[args.index(f"{tmp_path.resolve()}:/work:rw") + 1] == bild_id
    assert stats["peakRssMb"] == 2 and stats["imageDigest"] == bild_id
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


def test_decode_eigene_grenze_statt_pillow_standard(monkeypatch):
    # Fotogrammetrie-Atlanten bis 16384² liegen über Pillows Standardgrenze (~179 Mpx); die Grenze
    # der Texturdekodierung darf nicht vom globalen Pillow-Wert abhängen und ihn nicht verändern.
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 20, 30)).save(buf, "PNG")
    image = decode_texture(buf.getvalue())
    assert image.size == (64, 64)
    assert image.getpixel((0, 0)) == (10, 20, 30, 255)
    assert Image.MAX_IMAGE_PIXELS == 100


def test_decode_textur_ueber_16384_je_achse_abgelehnt():
    buf = io.BytesIO()
    Image.new("L", (16385, 1)).save(buf, "PNG")
    with pytest.raises(ValueError, match="16384"):
        decode_texture(buf.getvalue())


# --- Image scanview-meshtools: Basis per Digest, Laufzeit distroless, Lock aus pdal.conda-lock --

MESH_DOCKERFILE = mesh_textures.KTX_DOCKERFILE
MESH_LOCK = mesh_textures.KTX_LOCKFILE
#: Python (Messwrapper, statisch gelinkt) und die C++-Laufzeit der KTX-Programme (ldd-geprüft).
MESH_PAKETE = {"python", "libstdcxx", "libgcc", "libgomp", "_openmp_mutex"}
KTX_DEB_SHA256 = "ca635ed489d8bf54fac8d7687056c651193de0740830a7738cc034adc63e3027"


def mesh_lock_befunde(mesh_lock: str, pdal_lock: str) -> list[str]:
    """Verstöße des meshtools-Locks: jede Zeile wörtlich aus dem PDAL-Lock (gleiche Builds, dort
    Hash-Pflicht und Fix-Versionen geprüft), genau die fünf Pakete für Wrapper und KTX."""
    mesh = lock_eintraege(mesh_lock)
    pdal = lock_eintraege(pdal_lock)
    fehler = [f"nicht im PDAL-Lock: {m[0]}" for name, m in mesh.items()
              if name not in pdal or pdal[name][0] != m[0]]
    fehler += [f"fehlt: {n}" for n in sorted(MESH_PAKETE - set(mesh))]
    fehler += [f"nicht vorgesehen: {n}" for n in sorted(set(mesh) - MESH_PAKETE)]
    return fehler


def test_meshtools_lock_nur_builds_aus_dem_pdal_lock():
    assert mesh_lock_befunde(MESH_LOCK.read_text(), pdal_runner.LOCKFILE.read_text()) == []


def test_meshtools_lock_pruefung_meldet_fremde_zeile():
    pdal = pdal_runner.LOCKFILE.read_text()
    fremd = re.sub(r"#sha256:[0-9a-f]{64}", "#sha256:" + "0" * 64, pdal.splitlines()[1])
    expat = next(z for z in pdal.splitlines() if "/libexpat-" in z)
    befunde = mesh_lock_befunde(f"@EXPLICIT\n{fremd}\n{expat}\n", pdal)
    assert befunde[0] == f"nicht im PDAL-Lock: {fremd}"
    assert "fehlt: python" in befunde and "fehlt: libstdcxx" in befunde
    assert "nicht vorgesehen: libexpat" in befunde


def test_ktx_image_tag_ist_hash_von_dockerfile_und_lock():
    """Review-Befund 1: KTX_IMAGE ist wie scanview-pdal ein Inhalts-Tag, kein lokales Image-ID-
    Literal, das kein Neubau reproduziert. Literal bleibt per Regex lesbar (SBOM-Stufe)."""
    assert re.fullmatch(r"scanview-meshtools:[0-9a-f]{12}", mesh_textures.KTX_IMAGE)
    assert mesh_textures.KTX_IMAGE == mesh_textures.ktx_image_tag()
    quelltext = Path(mesh_textures.__file__).read_text(encoding="utf-8")
    muster = rf'^KTX_IMAGE = "{re.escape(mesh_textures.KTX_IMAGE)}"$'
    assert re.search(muster, quelltext, re.MULTILINE)


def test_ktx_image_tag_folgt_dockerfile_und_lock(tmp_path, monkeypatch):
    """Negativprobe: Ein geändertes Lock oder Dockerfile ohne neuen Tag fällt auf."""
    for name in ("KTX_DOCKERFILE", "KTX_LOCKFILE"):
        kopie = tmp_path / getattr(mesh_textures, name).name
        kopie.write_bytes(getattr(mesh_textures, name).read_bytes() + b"\n")
        monkeypatch.setattr(mesh_textures, name, kopie)
        assert mesh_textures.ktx_image_tag() != mesh_textures.KTX_IMAGE
        with pytest.raises(RuntimeError, match="Tag im Quelltext nachziehen"):
            mesh_textures.ensure_ktx_image()
        monkeypatch.undo()
    assert mesh_textures.ktx_image_tag() == mesh_textures.KTX_IMAGE


def test_meshtools_dockerfile_distroless_ohne_paketverwaltung_und_hash_geprueft():
    """Retest B-SCAN-IMG-MESHTOOLS: kein python:slim/apt mehr, beide Basen per Digest, das
    KTX-Paket auch ohne BuildKit per sha256sum geprüft (der klassische Builder übergeht
    ADD --checksum)."""
    inhalt = MESH_DOCKERFILE.read_text()
    from_zeilen = from_zeilen_per_digest(MESH_DOCKERFILE)
    assert len(from_zeilen) == 2
    assert from_zeilen[1].startswith("FROM gcr.io/distroless/base-nossl-debian13:")
    anweisungen = "\n".join(z for z in inhalt.splitlines() if not z.startswith("#"))
    assert "apt-get" not in anweisungen and "python:3" not in anweisungen
    assert f"--checksum=sha256:{KTX_DEB_SHA256}" in inhalt
    assert f'echo "{KTX_DEB_SHA256}  /tmp/ktx.deb" | sha256sum -c -' in inhalt
    assert "-f /tmp/meshtools.conda-lock.txt" in inhalt and "ENTRYPOINT []" in inhalt
    assert re.fullmatch(r"scanview-meshtools:[0-9a-f]{12}", mesh_textures.KTX_IMAGE)


@pytest.mark.docker
def test_ktx_image_ohne_shell_und_paketverwaltung():
    bild_id = mesh_textures.ensure_ktx_image()
    probe = ("import os; print([p for p in ('/bin/sh', '/usr/bin/apt-get', '/usr/bin/dpkg', "
             "'/usr/bin/perl') if os.path.exists(p)])")
    ergebnis = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", bild_id, "python", "-c", probe],
        capture_output=True, text=True, check=True)
    assert ergebnis.stdout.strip() == "[]"
