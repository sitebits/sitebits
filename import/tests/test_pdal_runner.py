import os
from pathlib import Path

import pytest

from scanview_import.pdal_runner import (
    IMAGE,
    INVALID_DIM,
    MEMORY_LIMIT,
    RunStats,
    _docker_args,
    _read_peak_mb,
    build_copc_pipeline,
    build_extract_pipeline,
    combine_stats,
    docker_grenzen,
)


def test_pdal_image_is_pinned_to_tested_version():
    # convert hängt an gemessenem readers.e57-Verhalten (colorLimits, count, Omit) — kein "latest".
    assert IMAGE.startswith("pdal/pdal:2.10.2@sha256:")
    assert "latest" not in IMAGE


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
    for name in ("SCANVIEW_PDAL_MEMORY", "SCANVIEW_PDAL_CPUS", "SCANVIEW_PDAL_LABEL"):
        monkeypatch.delenv(name, raising=False)


def test_docker_args_limit_memory_and_run_as_user(tmp_path):
    args = _docker_args(
        "pdal/pdal:latest", ["pdal", "pipeline", "/work/p.json"], {Path("/data"): "/src"}, tmp_path
    )
    assert args[:3] == ["docker", "run", "--rm"]
    assert f"--memory={MEMORY_LIMIT}" in args and f"--memory-swap={MEMORY_LIMIT}" in args
    assert MEMORY_LIMIT == "40g"
    assert not any(arg.startswith("--cpus") for arg in args)
    assert "--label" not in args
    i = args.index("--user")
    assert args[i + 1] == f"{os.getuid()}:{os.getgid()}"
    assert f"{tmp_path}:/work" in args and "/data:/src" in args
    img = args.index("pdal/pdal:latest")
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
