"""Tests für die Kommandozeile ``scanview-import``.

Schnelle Tests rufen ``cli.main()`` direkt auf und patchen die im ``cli``-Modul
gebundenen Namen (``scanview_import.cli.convert`` &c.) — ein Patch auf
``scanview_import.convert.convert`` hätte keine Wirkung, weil ``cli`` die
Funktion beim Import bereits gebunden hat. Docker-markierte Tests laufen als
echter Subprozess über ``python -m scanview_import.cli`` (robust gegenüber
PATH/Installationszustand).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import scanview_import.cli as cli_mod
from scanview_import.cli import main
from scanview_import.manifest_io import cloud_of, read_manifest, write_manifest


def test_inventory_cli_prints_json(cube_e57):
    out = subprocess.run(
        [sys.executable, "-m", "scanview_import.cli", "inventory", str(cube_e57.path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert json.loads(out)["images"][0]["kind"] == "pinhole"


@pytest.mark.docker
def test_build_cli_creates_package(cube_e57, tmp_path):
    pkg = tmp_path / "pkg"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "scanview_import.cli",
            "build",
            str(cube_e57.path),
            str(pkg),
            "--levels",
            "32,64",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    for f in (
        "project.json",
        "cloud.copc.laz",
        "stations.json",
        "inventory.json",
        "conversion-report.json",
        "topview.png",
    ):
        assert (pkg / f).exists(), f
    report = json.loads((pkg / "conversion-report.json").read_text())
    assert report["ok"] is True
    # Wanduhr je Phase + Host-Peak zusätzlich zu den Docker-/Container-Zahlen.
    assert set(report["host"]["phases"]) == {"convert", "panos", "overview", "verify"}
    for phase in report["host"]["phases"].values():
        assert phase["seconds"] > 0
    assert report["host"]["hostPeakRssMb"] > 0
    assert report["conversion"]["seconds"] > 0
    assert report["conversion"]["peakRssMb"] > 0
    # der reale build-Pfad schreibt project.json jetzt als Schema 2, mit Standpunkten
    # als Ebene und dem verify-Report-Verweis in der Punktwolken-Ebene.
    m = read_manifest(pkg)
    assert m["schemaVersion"] == 2
    assert [lay["id"] for lay in m["layers"]] == ["punkte", "standpunkte"]
    standpunkte = next(lay for lay in m["layers"] if lay["id"] == "standpunkte")
    assert standpunkte["count"] == 1  # cube_e57: ein Standpunkt
    assert "topview" in m
    assert cloud_of(m)["verify"] == {"ok": True, "report": "conversion-report.json"}


@pytest.mark.docker
def test_build_cli_max_points_end_to_end_fails_verify_but_exits_zero(two_scan_e57, tmp_path):
    """Belegt die Prämisse mit einem echten Lauf (nicht nur gestubbt): ``verify``
    zieht seine Stichprobe immer aus der vollen Quelle (``--max-points`` kennt keine
    Scan-Auswahl) — ein mit ``--max-points`` gekürztes Paket muss dabei durchfallen
    (``ok=False``), der Prozess aber trotzdem mit 0 enden."""
    pkg = tmp_path / "pkg"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "scanview_import.cli",
            "build",
            str(two_scan_e57.path),
            str(pkg),
            "--max-points",
            "500",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["ok"] is False
    assert "erwartet" in proc.stderr.lower()
    manifest = json.loads((pkg / "project.json").read_text())
    assert manifest["conversion"]["mode"] == "staged"  # --max-points ohne --mode


@pytest.mark.docker
def test_convert_cli_alone_does_not_run_panos_or_verify(two_scan_e57, tmp_path):
    pkg = tmp_path / "pkg"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "scanview_import.cli",
            "convert",
            str(two_scan_e57.path),
            str(pkg),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (pkg / "project.json").exists()
    assert (pkg / "cloud.copc.laz").exists()
    assert not (pkg / "stations.json").exists()
    assert not (pkg / "conversion-report.json").exists()


def _new_pkg_with_project_json(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "project.json").write_text(
        json.dumps({"stations": {"url": "stations.json", "count": 0}})
    )
    return pkg


def test_panos_cli_standalone_writes_stations(cube_e57, tmp_path):
    # panos braucht kein Docker (nur pye57 + PIL) — direkter main()-Aufruf reicht.
    pkg = _new_pkg_with_project_json(tmp_path)
    rc = main(["panos", str(cube_e57.path), str(pkg), "--levels", "32,64"])
    assert rc == 0
    assert (pkg / "stations.json").exists()
    assert json.loads((pkg / "project.json").read_text())["stations"]["count"] == 1


@pytest.mark.docker
def test_verify_cli_standalone_prints_report_and_exits_zero_on_ok(two_scan_e57, tmp_path, capsys):
    from scanview_import.convert import convert as real_convert

    pkg = tmp_path / "pkg"
    real_convert(two_scan_e57.path, pkg, None, None)
    rc = main(["verify", str(two_scan_e57.path), str(pkg), "--samples", "500"])
    out = capsys.readouterr().out
    assert rc == 0
    report = json.loads(out)
    assert report["ok"] is True
    assert json.loads((pkg / "conversion-report.json").read_text())["ok"] is True


def test_verify_cli_alone_exits_two_on_failure(monkeypatch, tmp_path):
    def fake_verify(e57: Path, pkg: Path, samples: int) -> dict[str, Any]:
        return {
            "ok": False,
            "pointBalance": {"ok": False},
            "samples": {"ok": False},
            "scansRepresented": {"ok": False},
            "attributes": {"ok": False},
            "pointSource": {"ok": False},
        }

    monkeypatch.setattr(cli_mod, "verify", fake_verify)
    rc = main(["verify", "irrelevant.e57", str(tmp_path), "--samples", "10"])
    assert rc == 2


def test_verify_cli_exits_two_when_only_attributes_fail(monkeypatch, tmp_path, capsys):
    """Regression: verify()['ok'] hängt jetzt auch von attributes.ok ab (z. B.
    unskaliertes 8-bit-RGB) — pointBalance/samples/scansRepresented können alle True sein und
    der Report trotzdem ok=False sein. Der CLI-Report muss attributes mit ausgeben, sonst sieht
    ein Aufrufer ok=false über drei True-Blöcken ohne erkennbaren Grund."""

    def fake_verify(e57: Path, pkg: Path, samples: int) -> dict[str, Any]:
        return {
            "ok": False,
            "pointBalance": {"ok": True},
            "samples": {"ok": True},
            "scansRepresented": {"ok": True},
            "attributes": {"ok": False, "rgbMax": 255},
            "pointSource": {"ok": True},
        }

    monkeypatch.setattr(cli_mod, "verify", fake_verify)
    rc = main(["verify", "irrelevant.e57", str(tmp_path), "--samples", "10"])
    report = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert report["attributes"]["ok"] is False


def test_verify_cli_reports_point_source_block(monkeypatch, tmp_path, capsys):
    """verify()['ok'] hängt auch an pointSource.ok (Scan-Zuordnung) — der CLI-Report
    muss den Block ausgeben, sonst steht ok=false ohne erkennbaren Grund da."""

    def fake_verify(e57: Path, pkg: Path, samples: int) -> dict[str, Any]:
        return {
            "ok": False,
            "pointBalance": {"ok": True},
            "samples": {"ok": True},
            "scansRepresented": {"ok": True},
            "attributes": {"ok": True},
            "pointSource": {"ok": False, "zeroIds": 3},
        }

    monkeypatch.setattr(cli_mod, "verify", fake_verify)
    rc = main(["verify", "irrelevant.e57", str(tmp_path), "--samples", "10"])
    report = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert report["pointSource"] == {"ok": False, "zeroIds": 3}


def test_max_points_without_mode_defaults_to_staged(monkeypatch, tmp_path):
    captured: dict[str, Any] = {}

    def fake_convert(
        e57: Path, pkg: Path, crs: str | None, max_points: int | None, mode: str
    ) -> dict[str, Any]:
        captured.update(e57=e57, pkg=pkg, crs=crs, max_points=max_points, mode=mode)
        return {}

    monkeypatch.setattr(cli_mod, "convert", fake_convert)
    rc = main(["convert", "a.e57", str(tmp_path), "--max-points", "500"])
    assert rc == 0
    assert captured["max_points"] == 500
    assert captured["mode"] == "staged"


def test_max_points_with_explicit_mode_is_not_overridden(monkeypatch, tmp_path):
    captured: dict[str, Any] = {}

    def fake_convert(
        e57: Path, pkg: Path, crs: str | None, max_points: int | None, mode: str
    ) -> dict[str, Any]:
        captured.update(max_points=max_points, mode=mode)
        return {}

    monkeypatch.setattr(cli_mod, "convert", fake_convert)
    main(["convert", "a.e57", str(tmp_path), "--max-points", "500", "--mode", "direct"])
    assert captured["mode"] == "direct"
    assert captured["max_points"] == 500


def test_mode_defaults_to_auto_without_max_points(monkeypatch, tmp_path):
    captured: dict[str, Any] = {}

    def fake_convert(
        e57: Path, pkg: Path, crs: str | None, max_points: int | None, mode: str
    ) -> dict[str, Any]:
        captured.update(max_points=max_points, mode=mode)
        return {}

    monkeypatch.setattr(cli_mod, "convert", fake_convert)
    main(["convert", "a.e57", str(tmp_path)])
    assert captured["mode"] == "auto"
    assert captured["max_points"] is None


def _stub_pipeline(monkeypatch: pytest.MonkeyPatch, ok: bool) -> None:
    monkeypatch.setattr(cli_mod, "convert", lambda *a, **kw: {})
    monkeypatch.setattr(cli_mod, "build_panos", lambda *a, **kw: {})
    monkeypatch.setattr(
        cli_mod,
        "build_overview",
        lambda *a, **kw: {"topview": {"widthPx": 1, "heightPx": 1}, "groundZ": {}},
    )
    monkeypatch.setattr(
        cli_mod,
        "verify",
        lambda *a, **kw: {
            "ok": ok,
            "pointBalance": {"ok": ok},
            "samples": {"ok": ok},
            "scansRepresented": {"ok": ok},
            "attributes": {"ok": ok},
            "pointSource": {"ok": ok},
        },
    )


def test_build_exit_code_two_on_full_verify_failure(monkeypatch, tmp_path):
    _stub_pipeline(monkeypatch, ok=False)
    rc = main(["build", "a.e57", str(tmp_path)])
    assert rc == 2


def test_build_exit_code_zero_and_note_on_truncated_verify_failure(monkeypatch, tmp_path, capsys):
    _stub_pipeline(monkeypatch, ok=False)
    rc = main(["build", "a.e57", str(tmp_path), "--max-points", "500"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "--max-points" in captured.err
    assert "ok=False ist hier erwartet" in captured.err


def test_build_prints_per_phase_wall_time_and_keeps_stdout_pure_json(monkeypatch, tmp_path, capsys):
    """Regression für den Echtdatenlauf: pro Phase eine Wanduhrzeit auf stderr (nicht nur
    ``conversion.seconds``, das nur die Docker-Laufzeit misst), stdout bleibt reines JSON."""
    _stub_pipeline(monkeypatch, ok=True)
    rc = main(["build", "a.e57", str(tmp_path)])
    captured = capsys.readouterr()
    assert rc == 0
    for phase in ("convert", "panos", "overview", "verify"):
        assert re.search(rf"\[{phase}\] fertig nach \d+\.\ds", captured.err), captured.err
    assert json.loads(captured.out)["ok"] is True


def test_build_exit_code_zero_on_full_verify_ok(monkeypatch, tmp_path):
    _stub_pipeline(monkeypatch, ok=True)
    rc = main(["build", "a.e57", str(tmp_path)])
    assert rc == 0


def test_build_writes_phase_times_and_host_peak_into_report(monkeypatch, tmp_path):
    """``conversion-report.json`` enthält Wanduhr je Phase und den Host-Peak-RSS
    (self + children), nicht nur die Docker-Zahlen aus ``conversion``."""
    _stub_pipeline(monkeypatch, ok=True)
    rc = main(["build", "a.e57", str(tmp_path)])
    assert rc == 0
    host = json.loads((tmp_path / "conversion-report.json").read_text())["host"]
    assert list(host["phases"]) == ["convert", "panos", "overview", "verify"]
    assert all(p["seconds"] >= 0 for p in host["phases"].values())
    assert host["totalSeconds"] == pytest.approx(sum(p["seconds"] for p in host["phases"].values()))
    peak = host["peakRssMb"]
    assert peak["self"] > 0  # dieser Testprozess belegt sicher Speicher
    assert peak["children"] >= 0
    assert host["hostPeakRssMb"] == max(peak["self"], peak["children"])


def test_verify_alone_reports_only_verify_phase(monkeypatch, tmp_path, capsys):
    _stub_pipeline(monkeypatch, ok=True)
    rc = main(["verify", "a.e57", str(tmp_path)])
    assert rc == 0
    host = json.loads((tmp_path / "conversion-report.json").read_text())["host"]
    assert list(host["phases"]) == ["verify"]
    assert json.loads(capsys.readouterr().out)["host"]["hostPeakRssMb"] > 0


def test_host_peak_rss_is_mib_from_ru_maxrss(monkeypatch):
    class Usage:
        def __init__(self, kb: int) -> None:
            self.ru_maxrss = kb

    monkeypatch.setattr(
        cli_mod.resource,
        "getrusage",
        lambda who: Usage(2048 if who == cli_mod.resource.RUSAGE_SELF else 4096),
    )
    assert cli_mod._host_peak_rss_mb() == {"self": 2.0, "children": 4.0, "max": 4.0}


def _write_manifest(pkg: Path, flag: bool | None, schema: int = 1) -> None:
    cloud: dict[str, object] = {"url": "cloud.copc.laz", "pointCount": 1}
    if flag is not None:
        cloud["pointSourceIsScan"] = flag
    if schema == 2:
        layer = {"id": "punkte", "kind": "pointcloud", **cloud}
        write_manifest(pkg, {"schemaVersion": 2, "layers": [layer]})
        return
    (pkg / "project.json").write_text(json.dumps({"schemaVersion": 1, "cloud": cloud}))


def _stub_point_source(monkeypatch: pytest.MonkeyPatch, ok: bool) -> None:
    _stub_pipeline(monkeypatch, ok=True)
    monkeypatch.setattr(
        cli_mod,
        "verify",
        lambda *a, **kw: {
            "ok": ok,
            "pointBalance": {"ok": True},
            "samples": {"ok": True},
            "scansRepresented": {"ok": True},
            "attributes": {"ok": True},
            "pointSource": {"ok": ok, "checked": True},
        },
    )


def test_build_withdraws_point_source_flag_when_verify_point_source_fails(monkeypatch, tmp_path):
    """Die Scan-Zuordnung ist nur mit bestandenem verify belastbar — sonst
    setzt build das Flag auf false, und der Viewer filtert nicht (Hinweis „ohne Scan-Zuordnung“)."""
    _write_manifest(tmp_path, True)
    _stub_point_source(monkeypatch, ok=False)
    assert main(["build", "a.e57", str(tmp_path)]) == 2
    cloud = cloud_of(read_manifest(tmp_path))
    assert cloud["pointSourceIsScan"] is False


def test_build_withdraws_point_source_flag_when_verify_point_source_fails_schema_2(
    monkeypatch, tmp_path
):
    """Wie oben, aber für ein Schema-2-Paket — ``cloud_of`` muss beide Schemata bedienen; das
    ist der Pfad, den ein realer ``build`` tatsächlich schreibt."""
    _write_manifest(tmp_path, True, schema=2)
    _stub_point_source(monkeypatch, ok=False)
    assert main(["build", "a.e57", str(tmp_path)]) == 2
    cloud = cloud_of(read_manifest(tmp_path))
    assert cloud["pointSourceIsScan"] is False


def test_build_keeps_point_source_flag_when_verify_point_source_ok(monkeypatch, tmp_path):
    _write_manifest(tmp_path, True)
    _stub_point_source(monkeypatch, ok=True)
    assert main(["build", "a.e57", str(tmp_path)]) == 0
    assert cloud_of(read_manifest(tmp_path))["pointSourceIsScan"] is True


def test_build_does_not_add_point_source_flag_to_packages_without_it(monkeypatch, tmp_path):
    _write_manifest(tmp_path, None)
    _stub_point_source(monkeypatch, ok=False)
    main(["build", "a.e57", str(tmp_path)])
    assert "pointSourceIsScan" not in cloud_of(read_manifest(tmp_path))


def test_build_withdraws_point_source_flag_when_verify_raises(monkeypatch, tmp_path):
    """Review 12c: bricht verify ab, ist die Zuordnung ebenfalls unbestätigt — Flag zurück, Fehler
    bleibt sichtbar."""
    _write_manifest(tmp_path, True)
    _stub_pipeline(monkeypatch, ok=True)

    def boom(*a: object, **kw: object) -> dict[str, Any]:
        raise RuntimeError("verify kaputt")

    monkeypatch.setattr(cli_mod, "verify", boom)
    with pytest.raises(RuntimeError, match="verify kaputt"):
        main(["build", "a.e57", str(tmp_path)])
    cloud = cloud_of(read_manifest(tmp_path))
    assert cloud["pointSourceIsScan"] is False


def test_verify_alone_does_not_touch_point_source_flag(monkeypatch, tmp_path):
    """Nur build zieht das pointSourceIsScan-Flag zurück; ein alleinstehendes verify
    lässt es unangetastet (anders als den verify-Report-Verweis — siehe unten)."""
    _write_manifest(tmp_path, True)
    _stub_point_source(monkeypatch, ok=False)
    main(["verify", "a.e57", str(tmp_path)])
    cloud = cloud_of(read_manifest(tmp_path))
    assert cloud["pointSourceIsScan"] is True


# --- verify-Report-Verweis in der Punktwolken-Ebene (nur Schema 2) ---------------------------


def _write_v2_manifest(pkg: Path) -> None:
    write_manifest(
        pkg,
        {
            "schemaVersion": 2,
            "layers": [
                {"id": "punkte", "kind": "pointcloud", "url": "cloud.copc.laz", "pointCount": 1}
            ],
        },
    )


def test_build_records_verify_report_in_schema_2_cloud_layer(monkeypatch, tmp_path):
    """Bei Schema 2 verweist die Punktwolken-Ebene mit ``verify.report`` auf
    ``conversion-report.json``."""
    _write_v2_manifest(tmp_path)
    _stub_point_source(monkeypatch, ok=True)
    assert main(["build", "a.e57", str(tmp_path)]) == 0
    cloud = cloud_of(read_manifest(tmp_path))
    assert cloud["verify"] == {"ok": True, "report": "conversion-report.json"}


def test_verify_alone_also_records_verify_report_for_schema_2(monkeypatch, tmp_path):
    """Der Report-Verweis gilt für ein alleinstehendes verify genauso — anders als das
    pointSourceIsScan-Flag, das nur build zurückzieht: hier geht es nur darum, wo der Report
    liegt."""
    _write_v2_manifest(tmp_path)
    _stub_pipeline(monkeypatch, ok=False)
    rc = main(["verify", "a.e57", str(tmp_path)])
    assert rc == 2
    cloud = cloud_of(read_manifest(tmp_path))
    assert cloud["verify"] == {"ok": False, "report": "conversion-report.json"}


def test_build_does_not_add_verify_field_to_schema_1_packages(monkeypatch, tmp_path):
    """Schema 1 kennt keine Ebenen — cloud.verify bleibt Schema-2-Paketen vorbehalten."""
    _write_manifest(tmp_path, True)
    _stub_point_source(monkeypatch, ok=True)
    main(["build", "a.e57", str(tmp_path)])
    cloud = cloud_of(read_manifest(tmp_path))
    assert "verify" not in cloud
