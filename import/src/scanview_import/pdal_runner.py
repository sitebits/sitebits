"""PDAL und untwine als externe Prozesse (Docker). Keine Bindung an deren Bibliotheken.

PDAL (BSD) läuft aus ``pdal/pdal``, untwine (GPL-3) aus dem eigenen Image
``scanview-untwine`` (``import/docker/untwine.Dockerfile``). Jeder Container läuft mit
harter Speichergrenze, als aufrufender Nutzer und gehärtet wie der Textur-Container: ohne Netz
(R-E-F-3 — die Parser lesen fremde Dateien, lokale Dateien brauchen kein Netz), ohne
Capabilities, ohne Rechteausweitung, mit schreibgeschütztem Wurzeldateisystem (nur ``/work``,
die Ziel-Mounts und ``/tmp`` beschreibbar).
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import Vec3

# Gepinnt auf die getestete Version (Tag + Digest, lokal per `pdal --version` = 2.10.2 geprüft):
# convert verlässt sich auf gemessenes, nicht als API zugesichertes Verhalten von readers.e57
# (colorLimits-Skalierung, `count` nur im Stream-Modus, Dimension `Omit`). Mit Digest nutzt Docker
# genau dieses Image, auch wenn der Tag später neu vergeben wird.
IMAGE = "pdal/pdal:2.10.2@sha256:8e0eb32d54e92058e0e73a6a5ce8cd9ac3a59d30f07c6827e6d7cb7a2e392883"
UNTWINE_IMAGE = "scanview-untwine:latest"
UNTWINE_DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "untwine.Dockerfile"
# Dimension für ungültige Punkte (cartesianInvalidState), per `pdal info --schema` gemessen.
INVALID_DIM = "Omit"
# Harte Grenze je Container, Swap eingeschlossen — schützt die Workstation (62 GiB).
MEMORY_LIMIT = "40g"
SCALE = 0.001
# LAS-1.4-Punktformat mit RGB; writers.copc und untwine schreiben ebenfalls Format 7.
POINT_FORMAT = 7

# Läuft im Container vor dem eigentlichen Kommando und schreibt dessen Peak-RSS (kB,
# ru_maxrss unter Linux) nach /work/peak. Anders als cgroup memory.peak ohne Page-Cache.
_RSS_WRAPPER = (
    "import resource,subprocess,sys\n"
    "rc = subprocess.call(sys.argv[1:])\n"
    "kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss\n"
    "open('/work/peak', 'w').write(str(kb))\n"
    "sys.exit(rc)\n"
)


@dataclass
class RunStats:
    seconds: float
    peak_rss_mb: float
    returncode: int
    stderr_tail: str


def _reader_stage(src: str, max_points: int | None) -> dict[str, Any]:
    reader: dict[str, Any] = {"type": "readers.e57", "filename": src}
    if max_points is not None:
        # readers.e57 kennt keine Scan-Auswahl, nur `count` — und beachtet es
        # gemessen nur im Stream-Modus; Stufe 1 läuft deshalb immer mit `--stream`.
        reader["count"] = max_points
    return reader


def _park_invalid_stage(origin: Vec3) -> dict[str, Any]:
    """Ungültige Punkte laufen durch writers.las (verworfen wird erst beim Taggen).
    Ihre Koordinaten sind laut E57 bedeutungslos (evtl. NaN oder weit außerhalb) — auf den
    LAS-Offset gesetzt, sind sie immer kodierbar."""
    return {
        "type": "filters.assign",
        "value": [
            f"{axis} = {o!r} WHERE {INVALID_DIM} != 0"
            for axis, o in zip("XYZ", origin, strict=True)
        ],
    }


def _scaled(origin: Vec3) -> dict[str, float]:
    return {
        "scale_x": SCALE,
        "scale_y": SCALE,
        "scale_z": SCALE,
        "offset_x": origin[0],
        "offset_y": origin[1],
        "offset_z": origin[2],
    }


def _rgb_scale_stage(rgb_is_8bit: bool) -> list[dict[str, Any]]:
    """8-bit-RGB (E57-Quelle 0-255) wird x257 auf 16 bit skaliert (LAS-/COPC-
    Konvention, sonst rendert Giro3D nahezu schwarz). 255·257 = 65535 exakt — kein Clamping
    an der oberen Grenze nötig (empirisch mit ``pdal pipeline`` verifiziert, Task-7-Report).
    Läuft unter ``--stream`` identisch (ebenfalls verifiziert)."""
    if not rgb_is_8bit:
        return []
    return [
        {
            "type": "filters.assign",
            "value": ["Red = Red * 257", "Green = Green * 257", "Blue = Blue * 257"],
        }
    ]


def build_extract_pipeline(
    src: str,
    dst: str,
    origin: Vec3,
    max_points: int | None = None,
    has_invalid: bool = True,
    rgb_is_8bit: bool = False,
) -> list[dict[str, Any]]:
    """Stufe 1 (beide Modi, streamfähig): readers.e57 → [Invalid parken] → [RGB x257] →
    LAZ 1.4 mit **allen** gelesenen Punkten in Lesereihenfolge.

    readers.e57 liefert keine Scan-Kennung (``pdal --options``, Schema und Plugin geprüft),
    liest die Scans aber nacheinander in data3D-Reihenfolge (gemessen). Die Scangrenzen folgen
    deshalb aus den Rohpunktzahlen des Inventars — aber nur, solange kein Punkt vorher
    verworfen wird. Ungültige Punkte bleiben darum hier drin und tragen ``Omit`` als
    Extra-Byte-Dimension; ``convert.tag_point_sources`` schreibt ``PointSourceId`` und
    verwirft sie. Im Ergebnis landet ``Omit`` nicht (beide Modi, gleiches Punktschema).
    """
    stages: list[dict[str, Any]] = [_reader_stage(src, max_points)]
    if has_invalid:
        # Nur, wenn die Dimension existiert — sonst bricht PDAL ab.
        stages.append(_park_invalid_stage(origin))
    stages += _rgb_scale_stage(rgb_is_8bit)
    writer: dict[str, Any] = {
        "type": "writers.las",
        "filename": dst,
        "compression": True,
        "minor_version": 4,
        "dataformat_id": POINT_FORMAT,
        **_scaled(origin),
        "forward": "all",
    }
    if has_invalid:
        writer["extra_dims"] = f"{INVALID_DIM}=uint8"
    stages.append(writer)
    return stages


def build_copc_pipeline(src: str, dst: str) -> list[dict[str, Any]]:
    """Modus ``direct``, Stufe 3: getaggtes LAZ → writers.copc (im Speicher, nicht
    streamfähig). ``forward: all`` übernimmt Skalierung, Offset und SRS aus dem LAZ-Kopf;
    ``PointSourceId`` bleibt erhalten (gemessen)."""
    return [
        {"type": "readers.las", "filename": src},
        {"type": "writers.copc", "filename": dst, "forward": "all"},
    ]


def docker_grenzen() -> list[str]:
    """Validierte Docker-Ressourcengrenzen und Laufkennung aus der Umgebung."""
    memory = os.environ.get("SCANVIEW_PDAL_MEMORY", MEMORY_LIMIT)
    if re.fullmatch(r"[0-9]+[kmg]", memory) is None:
        raise ValueError(
            "SCANVIEW_PDAL_MEMORY muss aus Ziffern und k, m oder g bestehen (z. B. 12g)."
        )
    cpus = os.environ.get("SCANVIEW_PDAL_CPUS")
    if cpus is not None and (
        re.fullmatch(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", cpus) is None
        or not math.isfinite(float(cpus))
        or float(cpus) <= 0
    ):
        raise ValueError(
            "SCANVIEW_PDAL_CPUS muss eine endliche positive Zahl sein (z. B. 4 oder 0.5)."
        )
    label = os.environ.get("SCANVIEW_PDAL_LABEL")
    if label is not None and re.fullmatch(r"[A-Za-z0-9._-]+", label) is None:
        raise ValueError(
            "SCANVIEW_PDAL_LABEL darf nur A-Z, a-z, 0-9, Punkt, Unterstrich und - enthalten."
        )
    args = [f"--memory={memory}", f"--memory-swap={memory}"]
    if cpus is not None:
        args.append(f"--cpus={cpus}")
    if label is not None:
        args += ["--label", f"scanview.lauf={label}"]
    return args


def _docker_args(
    image: str, cmd: Sequence[str], mounts: dict[Path, str], workdir: Path
) -> list[str]:
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
        f"{workdir}:/work",
    ]
    for host, cont in mounts.items():
        args += ["-v", f"{host}:{cont}"]
    args += [image, "python", "-c", _RSS_WRAPPER, *cmd]
    return args


def _read_peak_mb(path: Path) -> float:
    try:
        text = path.read_text().strip()
    except OSError:
        return 0.0
    return float(text) / 1024.0 if text else 0.0


def _docker_run(image: str, cmd: Sequence[str], mounts: dict[Path, str], workdir: Path) -> RunStats:
    t0 = time.monotonic()
    proc = subprocess.run(
        _docker_args(image, cmd, mounts, workdir), capture_output=True, text=True, check=False
    )
    seconds = time.monotonic() - t0
    tail = (proc.stderr + proc.stdout)[-2000:]
    return RunStats(seconds, _read_peak_mb(workdir / "peak"), proc.returncode, tail)


def run_pipeline(
    pipeline: list[dict[str, Any]], mounts: dict[Path, str], stream: bool = False
) -> RunStats:
    """Führt eine PDAL-Pipeline aus. ``stream=True`` erzwingt den Stream-Modus
    (PDAL bricht ab, statt still auf den Standardmodus auszuweichen)."""
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        (work / "pipeline.json").write_text(json.dumps(pipeline))
        cmd = ["pdal", "pipeline", *(["--stream"] if stream else []), "/work/pipeline.json"]
        return _docker_run(IMAGE, cmd, mounts, work)


def ensure_untwine_image() -> None:
    """Baut ``scanview-untwine:latest`` aus dem Dockerfile, falls es lokal fehlt."""
    have = subprocess.run(
        ["docker", "image", "inspect", UNTWINE_IMAGE], capture_output=True, check=False
    )
    if have.returncode == 0:
        return
    proc = subprocess.run(
        [
            "docker",
            "build",
            "-f",
            str(UNTWINE_DOCKERFILE),
            "-t",
            UNTWINE_IMAGE,
            str(UNTWINE_DOCKERFILE.parent),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Bau von {UNTWINE_IMAGE} fehlgeschlagen: {proc.stderr[-2000:]}")


def run_untwine(src: str, dst: str, temp_dir: str, mounts: dict[Path, str]) -> RunStats:
    """untwine ``src`` (Datei oder Verzeichnis) → eine COPC-Datei ``dst``.

    In untwine 1.5 ist ``--output_dir`` der Ausgabe-Dateiname; Einzeldatei-COPC ist Standard
    (``--single_file`` ist veraltet und wirkungslos, laut ``untwine --help``).
    """
    ensure_untwine_image()
    with tempfile.TemporaryDirectory() as td:
        cmd = ["untwine", "--files", src, "--output_dir", dst, "--temp_dir", temp_dir]
        return _docker_run(UNTWINE_IMAGE, cmd, mounts, Path(td))


def combine_stats(stats: Sequence[RunStats]) -> RunStats:
    """Stufen nacheinander: Zeit summiert, Speicher als Maximum, erster Fehlercode zählt."""
    rc = next((s.returncode for s in stats if s.returncode != 0), 0)
    return RunStats(
        sum(s.seconds for s in stats),
        max((s.peak_rss_mb for s in stats), default=0.0),
        rc,
        stats[-1].stderr_tail if stats else "",
    )


def build_point_pipeline(
    source: Any,
    src: str,
    dst: str,
    origin: Vec3,
    max_points: int | None = None,
    ausduennen: float | None = None,
    preserve_coordinates: bool = False,
) -> list[dict[str, Any]]:
    """Scanlose Quelle → metrische, Z-oben-LAZ. RGB nur nach deklarierter PLY-Skalierung."""
    from .point_source import UNIT_FACTORS

    reader: dict[str, Any] = {"type": source.reader, "filename": src}
    if max_points is not None:
        reader["count"] = max_points
    if source.crs is not None:
        reader["override_srs"] = source.crs
    if source.reader == "readers.text":
        reader.update(
            header=source.separator.join(source.columns),
            separator=source.separator,
            skip=source.text_skip,
        )
    stages = [reader]
    factor = UNIT_FACTORS[source.unit]
    if factor != 1.0 or source.up != "z":
        matrix = (
            [factor, 0, 0, 0, 0, 0, -factor, 0, 0, factor, 0, 0, 0, 0, 0, 1]
            if source.up == "y"
            else [factor, 0, 0, 0, 0, factor, 0, 0, 0, 0, factor, 0, 0, 0, 0, 1]
        )
        stages.append(
            {"type": "filters.transformation", "matrix": " ".join(str(v) for v in matrix)}
        )
    stages.append({"type": "filters.assign", "value": ["PointSourceId = 0"]})
    if ausduennen is not None:
        stages.append({"type": "filters.voxelcenternearestneighbor", "cell": ausduennen})
    if preserve_coordinates:
        from .tiled_thinning import RAW_DIMS

        stages.append(
            {
                "type": "filters.ferry",
                "dimensions": ",".join(
                    f"{axis}=>{d}" for axis, d in zip("XYZ", RAW_DIMS, strict=True)
                ),
            }
        )
    writer: dict[str, Any] = {
        "type": "writers.las",
        "filename": dst,
        "compression": True,
        "minor_version": 4,
        "dataformat_id": POINT_FORMAT,
        **_scaled(origin),
    }
    if preserve_coordinates:
        writer["extra_dims"] = ",".join(f"{d}=double" for d in RAW_DIMS)
    if source.crs is not None:
        writer["a_srs"] = source.crs
    stages.append(writer)
    return stages
