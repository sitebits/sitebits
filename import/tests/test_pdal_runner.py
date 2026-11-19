import os
import re
import subprocess
from pathlib import Path

import pytest
from image_lock import from_zeilen_per_digest, lock_eintraege, lock_pakete, pixi_pakete

from scanview_import import pdal_runner
from scanview_import.pdal_runner import (
    DOCKERFILE,
    IMAGE,
    INVALID_DIM,
    LOCKFILE,
    MEMORY_LIMIT,
    RunStats,
    _docker_args,
    _read_peak_mb,
    build_copc_pipeline,
    build_extract_pipeline,
    combine_stats,
    docker_grenzen,
    ensure_gebaut,
    image_tag,
)

# --- Image scanview-pdal: Tag = Hash(Dockerfile + Lock), Basis per Digest, explizites Lock ------

#: Mindestversionen je gemeldeter Schwachstelle der alten Images (B-SCAN-IMG-PDAL/-UNTWINE).
#: trivy prüft conda-Pakete ohne PURL nicht; diese statische Prüfung des Locks gleicht das aus.
#: openssl: CVE-2026-84782, Fix je Release-Reihe laut OpenSSL Security Advisory 20260929
#: (https://openssl-library.org/news/secadv/20260929.txt); certifi: GHSA-6q48-6c7w-9gw2;
#: urllib3: CVE-2026-97687/-97689. Eine Reihe jünger als alle der Tabelle erschien nach dem
#: Advisory und gilt als behoben; eine ältere fehlt dort, weil sie keinen Fix bekommt; eine
#: Reihe dazwischen kennt die Tabelle nicht — das wird ausdrücklich gemeldet (fail-closed).
_OPENSSL_FIX = {(4, 0): (4, 0, 3), (3, 6): (3, 6, 5), (3, 5): (3, 5, 9), (3, 4): (3, 4, 8)}
_MINDEST = {"certifi": (2026, 10, 19), "urllib3": (2, 8, 0)}
#: Pakete, die nur im Image stehen und nicht in der PDAL-Umgebung des Servers (pixi.lock):
#: python 3.12 für den Messwrapper samt Abhängigkeiten. Alle übrigen sind dieselben Builds.
NUR_IMAGE = {"python", "libpython", "tk", "libffi", "libxcrypt", "libuuid", "ld_impl_linux-64",
             "tzdata"}
PIXI_LOCK = pdal_runner.DOCKER_DIR.parent / "pdal-umgebung" / "pixi.lock"


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(teil) for teil in re.findall(r"[0-9]+", text))


def _openssl_befund(version: str) -> str | None:
    v = _version(version)
    reihe = v[:2]
    if reihe in _OPENSSL_FIX:
        return f"openssl {version} unter der Fix-Version" if v < _OPENSSL_FIX[reihe] else None
    if reihe > max(_OPENSSL_FIX):
        return None
    if reihe < min(_OPENSSL_FIX):
        return f"openssl {version} unter der Fix-Version"
    return f"openssl {version}: Reihe nicht in der Advisory-Tabelle"


def lock_befunde(text: str) -> list[str]:
    """Verstöße des Locks gegen die Regeln: kein conda/rattler/pip, Fix-Versionen eingehalten."""
    pakete = lock_pakete(text)
    fehler = [
        f"{name}: Paketwerkzeug im Image"
        for name in pakete
        if name in ("conda", "pip", "mamba", "micromamba") or "rattler" in name
        or name.startswith("conda-")
    ]
    if "openssl" in pakete and (befund := _openssl_befund(pakete["openssl"])):
        fehler.append(befund)
    for name, mindest in _MINDEST.items():
        if name in pakete and _version(pakete[name]) < mindest:
            fehler.append(f"{name} {pakete[name]} unter der Fix-Version")
    return fehler


def gleiche_builds_befunde(image_lock: str, pixi_lock: str) -> list[str]:
    """Image-Lock gegen pixi.lock der PDAL-Umgebung: jedes gemeinsame Paket mit gleicher URL und
    gleichem SHA-256, nur `NUR_IMAGE` zusätzlich, keins der Umgebung fehlt."""
    bild = lock_eintraege(image_lock)
    umgebung = pixi_pakete(pixi_lock)
    fehler = [
        f"{name}: anderes Build als in pixi.lock"
        for name in sorted(set(bild) & set(umgebung))
        if (bild[name]["url"], bild[name]["sha256"]) != umgebung[name]
    ]
    fehler += [f"{n}: nur im Image, nicht in NUR_IMAGE" for n in sorted(set(bild) - set(umgebung)
                                                                       - NUR_IMAGE)]
    fehler += [f"{n}: in NUR_IMAGE, aber nicht im Image" for n in sorted(NUR_IMAGE - set(bild))]
    fehler += [f"{n}: in pixi.lock, fehlt im Image" for n in sorted(set(umgebung) - set(bild))]
    return fehler


def test_image_tag_ist_hash_von_dockerfile_und_lock():
    # Drift-Test: IMAGE bleibt ein Literal (die SBOM-Stufe liest es per Regex aus dem Quelltext).
    assert re.fullmatch(r"scanview-pdal:[0-9a-f]{12}", IMAGE)
    assert IMAGE == image_tag()
    quelltext = Path(pdal_runner.__file__).read_text(encoding="utf-8")
    assert re.search(rf'^IMAGE = "{re.escape(IMAGE)}"$', quelltext, re.MULTILINE)
    assert "/pdal:" not in quelltext and ":latest" not in quelltext


def test_image_tag_folgt_dockerfile_und_lock(tmp_path, monkeypatch):
    """Negativprobe: Ein geändertes Lock oder Dockerfile ohne neuen Tag fällt auf."""
    for name in ("DOCKERFILE", "LOCKFILE"):
        kopie = tmp_path / getattr(pdal_runner, name).name
        kopie.write_bytes(getattr(pdal_runner, name).read_bytes() + b"\n")
        monkeypatch.setattr(pdal_runner, name, kopie)
        assert image_tag() != IMAGE
        monkeypatch.undo()
    assert image_tag() == IMAGE


@pytest.fixture
def docker_aufrufe(monkeypatch):
    """subprocess.run für ensure_gebaut: zeichnet Aufrufe auf, `antworten` je Befehlsanfang."""
    monkeypatch.setattr(pdal_runner, "_GEPRUEFT", {})
    aufrufe: list[list[str]] = []
    antworten: dict[str, list[subprocess.CompletedProcess]] = {"inspect": [], "build": []}

    def run(args, **_kwargs):
        aufrufe.append(args)
        return antworten["inspect" if args[1] == "image" else "build"].pop(0)

    monkeypatch.setattr(pdal_runner.subprocess, "run", run)
    return aufrufe, antworten


def test_ensure_gebaut_bricht_bei_veraltetem_tag_ab(tmp_path, docker_aufrufe):
    """Review-Befund 5: Nach einer Änderung an Dockerfile oder Lock läuft nie still das alte
    Image weiter — vor jedem docker-Aufruf."""
    aufrufe, _ = docker_aufrufe
    lock = tmp_path / LOCKFILE.name
    lock.write_bytes(LOCKFILE.read_bytes() + b"\n")
    with pytest.raises(RuntimeError, match=r"passt nicht zu pdal\.Dockerfile und pdal\.conda-lock"):
        ensure_gebaut(IMAGE, DOCKERFILE, lock)
    assert aufrufe == []


def test_run_pipeline_startet_mit_der_gepruefen_image_id(monkeypatch):
    """Prüfung und Start zeigen auf dasselbe Image: gestartet wird die ID aus ensure_image(),
    nicht der Tag (Gegenprüfung 10.11.)."""
    import scanview_import.pdal_runner as pr

    monkeypatch.setenv("SCANVIEW_PDAL_AUSFUEHRUNG", "docker")
    monkeypatch.setattr(pr, "ensure_image", lambda: "sha256:geprueft")
    gestartet = []

    def docker_run(image, cmd, mounts, workdir):
        gestartet.append(image)
        return None

    monkeypatch.setattr(pr, "_docker_run", docker_run)
    pr.run_pipeline([{"type": "readers.las", "filename": "/in/a.las"}], {})
    pr.run_untwine("/in/a.las", "/out/a.copc.laz", "/tmp/u", {})
    assert gestartet == ["sha256:geprueft", "sha256:geprueft"]


def test_ensure_gebaut_prueft_einmal_je_prozess(docker_aufrufe):
    """Review-Befund 6: ein `docker image inspect` je Image und Prozess, nicht je Pipeline."""
    aufrufe, antworten = docker_aufrufe
    antworten["inspect"].append(subprocess.CompletedProcess([], 0, "sha256:abc\n", ""))
    assert ensure_gebaut(IMAGE, DOCKERFILE, LOCKFILE) == "sha256:abc"
    assert ensure_gebaut(IMAGE, DOCKERFILE, LOCKFILE) == "sha256:abc"
    assert aufrufe == [["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE]]


def test_ensure_gebaut_baut_fehlendes_image(docker_aufrufe):
    aufrufe, antworten = docker_aufrufe
    antworten["inspect"] += [subprocess.CompletedProcess([], 1, "", "No such image"),
                             subprocess.CompletedProcess([], 0, "sha256:neu\n", "")]
    antworten["build"].append(subprocess.CompletedProcess([], 0, "", ""))
    assert ensure_gebaut(IMAGE, DOCKERFILE, LOCKFILE) == "sha256:neu"
    assert aufrufe[1] == ["docker", "build", "-f", str(DOCKERFILE), "-t", IMAGE,
                          str(DOCKERFILE.parent)]


def test_ensure_gebaut_meldet_stdout_und_stderr_des_baus(docker_aufrufe):
    """Review-Befund 7: Der klassische Builder schreibt die RUN-Ausgabe (etwa den micromamba-
    Fehler) nach stdout — beide Enden stehen in der Meldung."""
    _, antworten = docker_aufrufe
    antworten["inspect"].append(subprocess.CompletedProcess([], 1, "", ""))
    antworten["build"].append(subprocess.CompletedProcess(
        [], 1, "Step 3/9 : RUN micromamba install\nerror: sha256 mismatch for libgdal\n",
        "The command returned a non-zero code: 1\n"))
    with pytest.raises(RuntimeError) as fehler:
        ensure_gebaut(IMAGE, DOCKERFILE, LOCKFILE)
    assert "sha256 mismatch for libgdal" in str(fehler.value)
    assert "non-zero code: 1" in str(fehler.value)
    assert IMAGE not in pdal_runner._GEPRUEFT


def test_dockerfile_basis_per_digest_gepinnt():
    assert len(from_zeilen_per_digest(DOCKERFILE)) == 2
    inhalt = DOCKERFILE.read_text()
    assert "-f /tmp/pdal.conda-lock.txt" in inhalt and "ENTRYPOINT []" in inhalt


def test_lock_explizit_mit_hash_ohne_paketwerkzeug_und_fix_versionen():
    text = LOCKFILE.read_text()
    pakete = lock_pakete(text)
    assert pakete["libpdal-core"] == "2.10.2" and pakete["untwine"] == "1.5.1"
    assert _version(pakete["python"])[:2] == (3, 12)
    assert lock_befunde(text) == []


def test_image_lock_gleiche_builds_wie_pdal_umgebung():
    """Review-Befund 3: Die Builds im Image sind dieselben wie in der PDAL-Umgebung des Servers
    (gleiche URL und SHA-256 je Paket) — darauf stützen sich OSV-Ersatzprüfung und VEX-Aussagen
    aus import/pdal-umgebung/osv.toml, wenn das Register sie aufs Image überträgt."""
    assert gleiche_builds_befunde(LOCKFILE.read_text(), PIXI_LOCK.read_text()) == []


def test_gleiche_builds_meldet_abweichungen():
    pixi = PIXI_LOCK.read_text()
    zeilen = LOCKFILE.read_text().splitlines()
    zlib = next(i for i, z in enumerate(zeilen) if "/libzlib-" in z)
    zeilen[zlib] = re.sub(r"sha256:[0-9a-f]{64}", "sha256:" + "0" * 64, zeilen[zlib])
    tk = next(i for i, z in enumerate(zeilen) if "/tk-" in z)
    geos = next(z for z in zeilen if "/geos-" in z)
    zeilen[tk] = zeilen[tk].replace("/tk-", "/tkx-")
    zeilen.remove(geos)
    assert gleiche_builds_befunde("\n".join(zeilen) + "\n", pixi) == [
        "libzlib: anderes Build als in pixi.lock",
        "tkx: nur im Image, nicht in NUR_IMAGE",
        "tk: in NUR_IMAGE, aber nicht im Image",
        "geos: in pixi.lock, fehlt im Image",
    ]


@pytest.mark.parametrize(
    ("zeile", "erwartet"),
    [
        ("openssl-3.6.4-h1_0", ["openssl 3.6.4 unter der Fix-Version"]),
        ("openssl-3.0.13-h1_0", ["openssl 3.0.13 unter der Fix-Version"]),
        ("openssl-3.7.0-h1_0", ["openssl 3.7.0: Reihe nicht in der Advisory-Tabelle"]),
        ("openssl-4.1.0-h1_0", []),
        ("openssl-5.0.0-h1_0", []),
        ("openssl-3.6.5-h1_0", []),
        ("certifi-2026.7.22-h1_0", ["certifi 2026.7.22 unter der Fix-Version"]),
        ("urllib3-2.7.0-h1_0", ["urllib3 2.7.0 unter der Fix-Version"]),
        ("py-rattler-0.1.0-h1_0", ["py-rattler: Paketwerkzeug im Image"]),
        ("conda-26.1.0-h1_0", ["conda: Paketwerkzeug im Image"]),
        ("pip-26.2.2-h1_0", ["pip: Paketwerkzeug im Image"]),
    ],
)
def test_lock_pruefung_meldet_verstoesse(zeile, erwartet):
    text = (
        "@EXPLICIT\nhttps://conda.anaconda.org/conda-forge/linux-64/"
        f"{zeile}.conda#sha256:{'0' * 64}\n"
    )
    assert lock_befunde(text) == erwartet


def test_lock_pruefung_verlangt_hash_je_zeile():
    with pytest.raises(AssertionError, match="ohne conda-forge-URL oder SHA-256"):
        lock_befunde("@EXPLICIT\nhttps://conda.anaconda.org/conda-forge/linux-64/a-1-h_0.conda\n")


def test_invalid_dim_is_omit():
    # Gemessen per `pdal info --schema` (Pilotkirche und Fixture).
    assert INVALID_DIM == "Omit"


def test_extract_pipeline_keeps_invalid_points_as_omit_extra_dim_streamable():
    """Stufe 1 (beide Modi, immer ``--stream``) schreibt ALLE gelesenen Punkte in
    Lesereihenfolge, ungültige nur markiert (``Omit`` als Extra-Byte-Dimension). Nur so bleiben
    die Scangrenzen aus den Punktzahlen des Inventars ableitbar; ``tag_point_sources`` verwirft
    die ungültigen danach und schreibt ``PointSourceId``."""
    p = build_extract_pipeline(
        "/in/a.e57", "/out/s/raw.laz", (1005.0, 2000.0, 10.0), max_points=1500
    )
    assert [s["type"] for s in p] == ["readers.e57", "filters.assign", "writers.las"]
    assert p[0]["count"] == 1500
    assert not any(s["type"] in ("filters.expression", "filters.head") for s in p)
    w = p[-1]
    assert w["extra_dims"] == f"{INVALID_DIM}=uint8"
    assert w["compression"] is True
    assert w["minor_version"] == 4
    assert w["dataformat_id"] == 7
    assert w["scale_x"] == w["scale_y"] == w["scale_z"] == 0.001
    assert (w["offset_x"], w["offset_y"], w["offset_z"]) == (1005.0, 2000.0, 10.0)


def test_extract_pipeline_parks_invalid_coordinates_at_offset():
    # Ungültige Punkte laufen jetzt durch writers.las: ihre Koordinaten (E57: bedeutungslos,
    # evtl. NaN) werden auf den LAS-Offset gesetzt, damit sie immer kodierbar sind.
    p = build_extract_pipeline("/in/a.e57", "/o.laz", (1005.0, 2000.0, 10.0))
    park = p[1]
    assert park["type"] == "filters.assign"
    assert park["value"] == [
        "X = 1005.0 WHERE Omit != 0",
        "Y = 2000.0 WHERE Omit != 0",
        "Z = 10.0 WHERE Omit != 0",
    ]


def test_extract_pipeline_without_max_points_has_no_count():
    p = build_extract_pipeline("/in/a.e57", "/o.laz", (0.0, 0.0, 0.0))
    assert "count" not in p[0]


def test_extract_pipeline_without_invalid_dimension_writes_no_extra_dim():
    p = build_extract_pipeline("/in/a.e57", "/o.laz", (0.0, 0.0, 0.0), has_invalid=False)
    assert [s["type"] for s in p] == ["readers.e57", "writers.las"]
    assert "extra_dims" not in p[-1]


def test_copc_pipeline_reads_tagged_laz_and_forwards_header():
    p = build_copc_pipeline("/out/s/points.laz", "/out/cloud.copc.laz")
    assert p == [
        {"type": "readers.las", "filename": "/out/s/points.laz"},
        {"type": "writers.copc", "filename": "/out/cloud.copc.laz", "forward": "all"},
    ]


def _assign_stage(pipeline: list[dict]) -> dict | None:
    return next(
        (s for s in pipeline if s["type"] == "filters.assign" and "Red = Red * 257" in s["value"]),
        None,
    )


def test_extract_pipeline_scales_8bit_rgb_to_16bit_when_flagged():
    # 8-bit-RGB wird x257 auf 16 bit skaliert (LAS-Konvention).
    p = build_extract_pipeline("/in/a.e57", "/o.laz", (0.0, 0.0, 0.0), rgb_is_8bit=True)
    stage = _assign_stage(p)
    assert stage is not None
    assert set(stage["value"]) == {"Red = Red * 257", "Green = Green * 257", "Blue = Blue * 257"}
    assert p.index(stage) == len(p) - 2
    assert p[-1]["type"] == "writers.las"


def test_extract_pipeline_no_rgb_scale_by_default():
    p = build_extract_pipeline("/in/a.e57", "/o.laz", (0.0, 0.0, 0.0))
    assert _assign_stage(p) is None


@pytest.fixture(autouse=True)
def clear_pdal_environment(monkeypatch):
    for name in ("SCANVIEW_PDAL_MEMORY", "SCANVIEW_PDAL_CPUS", "SCANVIEW_PDAL_LABEL",
                 "SCANVIEW_PDAL_AUSFUEHRUNG", "SCANVIEW_PDAL_UMGEBUNG"):
        monkeypatch.delenv(name, raising=False)


_BILD = "scanview-pdal:0123456789ab"


def test_docker_args_limit_memory_and_run_as_user(tmp_path):
    args = _docker_args(
        _BILD, ["pdal", "pipeline", "/work/p.json"], {Path("/data"): "/src"}, tmp_path
    )
    assert args[:3] == ["docker", "run", "--rm"]
    # Härtung (R-E-F-3): kein Netz, keine Capabilities, keine Rechteausweitung, Wurzel nur lesend
    assert args[args.index("--network") + 1] == "none"
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert args[args.index("--security-opt") + 1] == "no-new-privileges"
    assert "--read-only" in args and args.index("--network") < args.index(_BILD)
    assert f"--memory={MEMORY_LIMIT}" in args and f"--memory-swap={MEMORY_LIMIT}" in args
    assert MEMORY_LIMIT == "40g"
    assert not any(arg.startswith("--cpus") for arg in args)
    assert "--label" not in args
    i = args.index("--user")
    assert args[i + 1] == f"{os.getuid()}:{os.getgid()}"
    assert f"{tmp_path}:/work" in args and "/data:/src" in args
    img = args.index(_BILD)
    # Wrapper misst Peak-RSS des Kindprozesses, danach das eigentliche Kommando
    assert args[img + 1] == "python"
    assert args[-3:] == ["pdal", "pipeline", "/work/p.json"]


@pytest.mark.parametrize("memory", ["12g", "512m", "1024k"])
def test_docker_args_read_memory_at_runtime(monkeypatch, tmp_path, memory):
    before = _docker_args(IMAGE, ["pdal"], {}, tmp_path)
    monkeypatch.setenv("SCANVIEW_PDAL_MEMORY", memory)
    after = _docker_args(IMAGE, ["pdal"], {}, tmp_path)
    assert after == [
        arg.replace("=40g", f"={memory}") if arg.startswith("--memory") else arg
        for arg in before
    ]


@pytest.mark.parametrize("cpus", ["4", "0.5"])
def test_docker_args_read_cpus_and_label_at_runtime(monkeypatch, tmp_path, cpus):
    before = _docker_args(IMAGE, ["pdal"], {}, tmp_path)
    monkeypatch.setenv("SCANVIEW_PDAL_CPUS", cpus)
    monkeypatch.setenv("SCANVIEW_PDAL_LABEL", "scanview-paket-test_01.v2")
    after = _docker_args(IMAGE, ["pdal"], {}, tmp_path)
    limits_end = before.index("--user")
    assert after == [
        *before[:limits_end],
        f"--cpus={cpus}",
        "--label",
        "scanview.lauf=scanview-paket-test_01.v2",
        *before[limits_end:],
    ]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SCANVIEW_PDAL_MEMORY", value)
        for value in ("", "12", "12G", "1.5g", "-1g", "12gb", "12g\n", " 12g", "\uff11\uff12g")
    ]
    + [
        ("SCANVIEW_PDAL_CPUS", value)
        for value in ("", "0", "0.0", "-1", "vier", "nan", "inf", "1_0", "4\n", "9" * 400)
    ]
    + [
        ("SCANVIEW_PDAL_LABEL", value)
        for value in ("", "paket test", "paket/test", "ä", "test=1", "test\n")
    ],
)
def test_docker_args_reject_invalid_environment(monkeypatch, tmp_path, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=rf"{name} (muss|darf)"):
        docker_grenzen()
    with pytest.raises(ValueError, match=rf"{name} (muss|darf)"):
        _docker_args(IMAGE, ["pdal"], {}, tmp_path)


def test_docker_grenzen_defaults_and_runtime_overrides(monkeypatch):
    assert docker_grenzen() == ["--memory=40g", "--memory-swap=40g"]
    monkeypatch.setenv("SCANVIEW_PDAL_MEMORY", "512m")
    monkeypatch.setenv("SCANVIEW_PDAL_CPUS", ".5")
    monkeypatch.setenv("SCANVIEW_PDAL_LABEL", "test_01.v2")
    assert docker_grenzen() == [
        "--memory=512m", "--memory-swap=512m", "--cpus=.5",
        "--label", "scanview.lauf=test_01.v2",
    ]


def test_read_peak_mb(tmp_path):
    f = tmp_path / "peak"
    f.write_text("1572864\n")  # ru_maxrss in kB
    assert _read_peak_mb(f) == 1536.0
    assert _read_peak_mb(tmp_path / "fehlt") == 0.0
    (tmp_path / "leer").write_text("")
    assert _read_peak_mb(tmp_path / "leer") == 0.0


def test_combine_stats_sums_time_and_takes_peak_max():
    a = RunStats(1.5, 100.0, 0, "a")
    b = RunStats(2.0, 50.0, 0, "b")
    c = combine_stats([a, b])
    assert c.seconds == 3.5 and c.peak_rss_mb == 100.0 and c.returncode == 0


@pytest.mark.docker
def test_run_pipeline_reports_failure(tmp_path):
    from scanview_import.pdal_runner import run_pipeline

    p = [{"type": "readers.e57", "filename": "/src/fehlt.e57"}, {"type": "writers.null"}]
    stats = run_pipeline(p, {tmp_path: "/src"})
    assert stats.returncode != 0
    assert "fehlt.e57" in stats.stderr_tail


# --- Ausführungsart „lokal“ (Server: gelockte conda-forge-Umgebung, kein Docker) ---------------

_FAKE = """#!/bin/sh
# Schreibt Argumente und Umgebung des Aufrufs in den Protokollordner, gibt ggf. die Pipeline aus.
name=$(basename "$0")
printf '%s\\n' "$@" > "$FAKE_LOG/$name.argv"
env > "$FAKE_LOG/$name.env"
for a in "$@"; do case $a in *.json) cat "$a" > "$FAKE_LOG/pipeline.json";; esac; done
echo "ausgabe von $name"
exit ${FAKE_RC:-0}
"""


@pytest.fixture
def lokale_umgebung(tmp_path, monkeypatch):
    praefix = tmp_path / "umgebung"
    (praefix / "bin").mkdir(parents=True)
    for name in ("pdal", "untwine"):
        f = praefix / "bin" / name
        f.write_text(_FAKE)
        f.chmod(0o755)
    log = tmp_path / "log"
    log.mkdir()
    monkeypatch.setenv("SCANVIEW_PDAL_AUSFUEHRUNG", "lokal")
    monkeypatch.setenv("SCANVIEW_PDAL_UMGEBUNG", str(praefix))
    monkeypatch.setenv("FAKE_LOG", str(log))
    # Ein Docker-Aufruf im lokalen Modus wäre ein Fehler: subprocess.run ist dort tabu.
    import scanview_import.pdal_runner as pr

    def kein_docker(*_a, **_k):
        raise AssertionError("lokal darf kein docker aufrufen")

    monkeypatch.setattr(pr, "_docker_run", kein_docker)
    monkeypatch.setattr(pr, "ensure_image", kein_docker)
    return praefix, log


def test_ausfuehrung_standard_ist_docker_und_wird_geprueft(monkeypatch):
    from scanview_import.pdal_runner import ausfuehrung

    assert ausfuehrung() == "docker"
    monkeypatch.setenv("SCANVIEW_PDAL_AUSFUEHRUNG", "lokal")
    assert ausfuehrung() == "lokal"
    for falsch in ("", "Lokal", "podman", "lokal "):
        monkeypatch.setenv("SCANVIEW_PDAL_AUSFUEHRUNG", falsch)
        with pytest.raises(ValueError, match="SCANVIEW_PDAL_AUSFUEHRUNG"):
            ausfuehrung()


def test_lokale_umgebung_verlangt_absoluten_praefix_mit_beiden_programmen(tmp_path, monkeypatch):
    from scanview_import.pdal_runner import lokale_umgebung as pruefen

    for wert in ("", "relativ/umgebung", str(tmp_path)):
        monkeypatch.setenv("SCANVIEW_PDAL_UMGEBUNG", wert)
        with pytest.raises(ValueError, match="SCANVIEW_PDAL_UMGEBUNG"):
            pruefen()
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "pdal").write_text("")
    (tmp_path / "bin" / "pdal").chmod(0o755)
    with pytest.raises(ValueError, match="bin/untwine"):
        pruefen()


def test_lokal_pipeline_ohne_docker_mit_host_pfaden_und_variablen(lokale_umgebung, tmp_path):
    import json

    from scanview_import.pdal_runner import run_pipeline

    praefix, log = lokale_umgebung
    quelle, ziel = tmp_path / "quelle", tmp_path / "ziel"
    pipe = [
        {"type": "readers.las", "filename": "/src/a.laz"},
        {"type": "filters.assign", "value": ["Red = Red * 257"]},
        {"type": "writers.copc", "filename": "/out/s/cloud.copc.laz", "forward": "all"},
    ]
    stats = run_pipeline(pipe, {quelle: "/src:ro", ziel: "/out"}, stream=True)
    assert stats.returncode == 0 and "ausgabe von pdal" in stats.stderr_tail
    argv = (log / "pdal.argv").read_text().splitlines()
    assert argv[:2] == ["pipeline", "--stream"] and argv[2].endswith("/pipeline.json")
    assert not argv[2].startswith("/work")
    gelaufen = json.loads((log / "pipeline.json").read_text())
    assert gelaufen[0]["filename"] == f"{quelle}/a.laz"
    assert gelaufen[1]["value"] == ["Red = Red * 257"]
    assert gelaufen[2]["filename"] == f"{ziel}/s/cloud.copc.laz"
    env = dict(z.split("=", 1) for z in (log / "pdal.env").read_text().splitlines() if "=" in z)
    assert env["PROJ_DATA"] == f"{praefix}/share/proj"
    assert env["PROJ_NETWORK"] == "OFF"
    assert env["PDAL_DRIVER_PATH"] == f"{praefix}/lib"
    assert env["GDAL_DATA"] == f"{praefix}/share/gdal"


def test_lokal_untwine_und_fehlercode(lokale_umgebung, tmp_path, monkeypatch):
    from scanview_import.pdal_runner import run_untwine

    _, log = lokale_umgebung
    ziel = tmp_path / "ziel"
    monkeypatch.setenv("FAKE_RC", "3")
    stats = run_untwine("/out/s/points.laz", "/out/cloud.copc.laz", "/out/s/untwine",
                        {ziel: "/out"})
    assert stats.returncode == 3
    assert (log / "untwine.argv").read_text().splitlines() == [
        "--files", f"{ziel}/s/points.laz", "--output_dir", f"{ziel}/cloud.copc.laz",
        "--temp_dir", f"{ziel}/s/untwine"]


def test_pfad_uebersetzung_nur_ganze_praefixe():
    from scanview_import.pdal_runner import _host_pfad, _pfadtabelle

    tabelle = _pfadtabelle({Path("/h/quelle"): "/src:ro", Path("/h/aus"): "/out"}, Path("/h/w"))
    assert _host_pfad("/out", tabelle) == "/h/aus"
    assert _host_pfad("/out/a/b.laz", tabelle) == "/h/aus/a/b.laz"
    assert _host_pfad("/outside/x", tabelle) == "/outside/x"
    assert _host_pfad("/work/pipeline.json", tabelle) == "/h/w/pipeline.json"
    assert _host_pfad("Red = Red * 257", tabelle) == "Red = Red * 257"
