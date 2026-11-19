"""PDAL und untwine als externe Prozesse. Keine Bindung an deren Bibliotheken.

Zwei Ausführungsarten, gewählt per ``SCANVIEW_PDAL_AUSFUEHRUNG``:

``docker`` (Standard, Arbeitsplatz): PDAL (BSD) und untwine (GPL-3) laufen aus dem eigenen,
lokal gebauten Image ``scanview-pdal`` (``import/docker/pdal.Dockerfile`` + explizites conda-Lock
``pdal.conda-lock.txt``, beides im Tag gehasht; nie verteilt). Jeder Container
läuft mit harter Speichergrenze, als aufrufender Nutzer und gehärtet wie der Textur-Container:
ohne Netz (R-E-F-3 — die Parser lesen fremde Dateien, lokale Dateien brauchen kein Netz), ohne
Capabilities, ohne Rechteausweitung, mit schreibgeschütztem Wurzeldateisystem (nur ``/work``,
die Ziel-Mounts und ``/tmp`` beschreibbar).

``lokal`` (Server, Import-Worker): ``pdal`` und ``untwine`` aus der gelockten conda-forge-Umgebung
``import/pdal-umgebung`` (dieselben Pakete wie im Image), Präfix in ``SCANVIEW_PDAL_UMGEBUNG``.
Kein Docker: Die Isolation (kein Netz, Dateisystem nur lesend bis auf den Arbeitsordner,
RAM-Grenze) setzt der Aufrufer — der Worker startet ``scanview-import`` in einer transienten
systemd-Unit. Die Pipelines bleiben dieselben; Containerpfade (``/out``, ``/src``, ``/work``)
werden vor dem Start auf die Host-Pfade der Mounts zurückgeführt.
"""

from __future__ import annotations

import hashlib
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
from typing import Any, Literal

from .model import Vec3

# Ein Image für PDAL und untwine, gebaut aus Dockerfile + explizitem conda-Lock (pdal 2.10.2,
# untwine 1.5.1; Basis-Images per Digest). convert verlässt sich auf gemessenes, nicht als API
# zugesichertes Verhalten von readers.e57 (colorLimits-Skalierung, `count` nur im Stream-Modus,
# Dimension `Omit`) — darum feste Versionen. Der Tag sind die ersten 12 Hex der SHA-256 über
# Dockerfile und Lock (`image_tag()`); er bleibt ein String-Literal, weil die SBOM-Stufe ihn aus
# dem Quelltext liest. Ein Drift-Test prüft Literal = `image_tag()`, und ensure_image() bricht
# bei Abweichung ab: Wer Dockerfile oder Lock ändert, muss den Tag nachziehen, dann baut
# ensure_image() ein neues Image.
IMAGE = "scanview-pdal:e65b1fb8ba03"
DOCKER_DIR = Path(__file__).resolve().parents[2] / "docker"
DOCKERFILE = DOCKER_DIR / "pdal.Dockerfile"
LOCKFILE = DOCKER_DIR / "pdal.conda-lock.txt"
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


# Ausführungsart und Präfix der lokalen Umgebung (bin/pdal, bin/untwine, share/proj …).
AUSFUEHRUNG_VAR = "SCANVIEW_PDAL_AUSFUEHRUNG"
UMGEBUNG_VAR = "SCANVIEW_PDAL_UMGEBUNG"
Ausfuehrung = Literal["docker", "lokal"]


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


def ausfuehrung() -> Ausfuehrung:
    """``docker`` (Standard) oder ``lokal`` aus ``SCANVIEW_PDAL_AUSFUEHRUNG``."""
    wert = os.environ.get(AUSFUEHRUNG_VAR, "docker")
    if wert not in ("docker", "lokal"):
        raise ValueError(f"{AUSFUEHRUNG_VAR} muss docker oder lokal sein.")
    return "lokal" if wert == "lokal" else "docker"


def lokale_umgebung() -> Path:
    """Präfix der lokalen PDAL-Umgebung: absolut, mit ausführbarem ``bin/pdal`` und
    ``bin/untwine``. Wirft ValueError (fehlt die Umgebung, bricht der Import laut ab, statt
    still auf Docker oder ein ``pdal`` aus dem PATH auszuweichen)."""
    roh = os.environ.get(UMGEBUNG_VAR, "")
    praefix = Path(roh)
    if not roh or not praefix.is_absolute():
        raise ValueError(f"{UMGEBUNG_VAR} fehlt oder ist kein absoluter Pfad.")
    for name in ("pdal", "untwine"):
        if not os.access(praefix / "bin" / name, os.X_OK):
            raise ValueError(f"{UMGEBUNG_VAR}: bin/{name} fehlt oder ist nicht ausführbar.")
    return praefix


def lokale_variablen(praefix: Path) -> dict[str, str]:
    """Was die Aktivierungsskripte der Umgebung setzen (etc/conda/activate.d), fest statt per
    Shell: Datenordner von PROJ/GDAL/GeoTIFF, Plugin-Ordner von PDAL/GDAL und PROJ ohne Netz."""
    return {
        "PROJ_DATA": str(praefix / "share" / "proj"),
        "PROJ_NETWORK": "OFF",
        "GDAL_DATA": str(praefix / "share" / "gdal"),
        "GDAL_DRIVER_PATH": str(praefix / "lib" / "gdalplugins"),
        "GEOTIFF_CSV": str(praefix / "share" / "epsg_csv"),
        "PDAL_DRIVER_PATH": str(praefix / "lib"),
        "CPL_ZIP_ENCODING": "UTF-8",
        "XML_CATALOG_FILES": f"file://{praefix}/etc/xml/catalog",
    }


def _pfadtabelle(mounts: dict[Path, str], workdir: Path) -> list[tuple[str, str]]:
    """Containerpfad → Host-Pfad, längste Containerpfade zuerst (``:ro`` entfällt)."""
    tabelle = [(cont.split(":", 1)[0], str(host)) for host, cont in mounts.items()]
    tabelle.append(("/work", str(workdir)))
    return sorted(tabelle, key=lambda e: len(e[0]), reverse=True)


def _host_pfad(wert: str, tabelle: list[tuple[str, str]]) -> str:
    for cont, host in tabelle:
        if wert == cont or wert.startswith(cont + "/"):
            return host + wert[len(cont):]
    return wert


def _uebersetzen(obj: Any, tabelle: list[tuple[str, str]]) -> Any:
    """Alle Zeichenketten, die mit einem Containerpfad beginnen, auf den Host-Pfad abbilden."""
    if isinstance(obj, str):
        return _host_pfad(obj, tabelle)
    if isinstance(obj, list):
        return [_uebersetzen(v, tabelle) for v in obj]
    if isinstance(obj, dict):
        return {k: _uebersetzen(v, tabelle) for k, v in obj.items()}
    return obj


def _lokal_run(cmd: Sequence[str], workdir: Path) -> RunStats:
    """Startet ``cmd`` direkt (feste Argumentliste, keine Shell) und misst Laufzeit und
    Peak-RSS genau dieses Kindes (``wait4``)."""
    praefix = lokale_umgebung()
    argv = [str(praefix / "bin" / cmd[0]), *cmd[1:]]
    env = {**os.environ, **lokale_variablen(praefix)}
    t0 = time.monotonic()
    with (workdir / "ausgabe.log").open("w+b") as log:
        proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, env=env)
        _, status, nutzung = os.wait4(proc.pid, 0)
        proc.returncode = os.waitstatus_to_exitcode(status)
        seconds = time.monotonic() - t0
        log.seek(max(0, log.seek(0, os.SEEK_END) - 2000))
        tail = log.read().decode("utf-8", "replace")
    return RunStats(seconds, nutzung.ru_maxrss / 1024.0, proc.returncode, tail)


def _ausfuehren(
    image: str, cmd: Sequence[str], mounts: dict[Path, str], workdir: Path
) -> RunStats:
    if ausfuehrung() == "lokal":
        return _lokal_run(_uebersetzen(list(cmd), _pfadtabelle(mounts, workdir)), workdir)
    return _docker_run(image, cmd, mounts, workdir)


def run_pipeline(
    pipeline: list[dict[str, Any]], mounts: dict[Path, str], stream: bool = False
) -> RunStats:
    """Führt eine PDAL-Pipeline aus. ``stream=True`` erzwingt den Stream-Modus
    (PDAL bricht ab, statt still auf den Standardmodus auszuweichen)."""
    bild = ensure_image() if ausfuehrung() == "docker" else IMAGE
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        if ausfuehrung() == "lokal":
            pipeline = _uebersetzen(pipeline, _pfadtabelle(mounts, work))
        (work / "pipeline.json").write_text(json.dumps(pipeline))
        cmd = ["pdal", "pipeline", *(["--stream"] if stream else []), "/work/pipeline.json"]
        return _ausfuehren(bild, cmd, mounts, work)


def inhalt_tag(name: str, dockerfile: Path, lockfile: Path) -> str:
    """Soll-Tag eines lokal gebauten Images: ``<name>:`` + erste 12 Hex der SHA-256 über die
    Bytes des Dockerfiles, gefolgt von den Bytes des Locks."""
    digest = hashlib.sha256(dockerfile.read_bytes() + lockfile.read_bytes()).hexdigest()
    return f"{name}:{digest[:12]}"


def image_tag() -> str:
    """Soll-Tag von ``IMAGE`` aus Dockerfile und Lock."""
    return inhalt_tag("scanview-pdal", DOCKERFILE, LOCKFILE)


#: In diesem Prozess schon geprüfte Images (Tag → Image-ID): `docker image inspect` läuft je
#: Image einmal, nicht bei jeder Pipeline (die Kachelausdünnung startet eine je Kachel).
_GEPRUEFT: dict[str, str] = {}


def _image_id(image: str) -> str | None:
    have = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
        check=False,
    )
    return have.stdout.strip() if have.returncode == 0 else None


def ensure_gebaut(image: str, dockerfile: Path, lockfile: Path) -> str:
    """Stellt sicher, dass ``image`` lokal vorhanden ist, und liefert seine Image-ID. Der Tag
    muss dem Inhalt von Dockerfile und Lock entsprechen (sonst liefe nach einer Änderung still das
    alte Image weiter); fehlt das Image, wird es gebaut (Kontext: Ordner des Dockerfiles)."""
    soll = inhalt_tag(image.rsplit(":", 1)[0], dockerfile, lockfile)
    if image != soll:
        raise RuntimeError(
            f"{image} passt nicht zu {dockerfile.name} und {lockfile.name} (Soll {soll}): "
            "Tag im Quelltext nachziehen"
        )
    if image in _GEPRUEFT:
        return _GEPRUEFT[image]
    bild_id = _image_id(image)
    if bild_id is None:
        proc = subprocess.run(
            ["docker", "build", "-f", str(dockerfile), "-t", image, str(dockerfile.parent)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            # Der klassische Builder schreibt die RUN-Ausgabe nach stdout, BuildKit nach stderr.
            raise RuntimeError(
                f"Bau von {image} fehlgeschlagen (Exit {proc.returncode})\n"
                f"stdout (Ende):\n{proc.stdout[-2000:]}\nstderr (Ende):\n{proc.stderr[-2000:]}"
            )
        bild_id = _image_id(image)
        if bild_id is None:
            raise RuntimeError(f"{image} nach dem Bau nicht vorhanden")
    _GEPRUEFT[image] = bild_id
    return bild_id


def ensure_image() -> str:
    """Baut ``IMAGE`` aus Dockerfile und Lock, falls es lokal fehlt (siehe ``ensure_gebaut``), und
    liefert die geprüfte Image-ID. Gestartet wird mit der ID, nicht mit dem Tag: kein Wechsel
    zwischen Prüfung und Start (wie ``mesh_textures._run``)."""
    return ensure_gebaut(IMAGE, DOCKERFILE, LOCKFILE)


def run_untwine(src: str, dst: str, temp_dir: str, mounts: dict[Path, str]) -> RunStats:
    """untwine ``src`` (Datei oder Verzeichnis) → eine COPC-Datei ``dst``.

    In untwine 1.5 ist ``--output_dir`` der Ausgabe-Dateiname; Einzeldatei-COPC ist Standard
    (``--single_file`` ist veraltet und wirkungslos, laut ``untwine --help``).
    """
    bild = ensure_image() if ausfuehrung() == "docker" else IMAGE
    with tempfile.TemporaryDirectory() as td:
        cmd = ["untwine", "--files", src, "--output_dir", dst, "--temp_dir", temp_dir]
        return _ausfuehren(bild, cmd, mounts, Path(td))


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
