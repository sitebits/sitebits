"""SCANVIEW_LOG_LEVEL konfiguriert das Journal (cli.serve), und
/api/authz protokolliert bei DEBUG genau eine Zeile je Aufruf (Status, projectId, Dauer).

Die Konfigurationstests laufen bewusst in einem eigenen Unterprozess (subprocess): pytest hängt
selbst schon vor dem Testlauf einen Handler an den Root-Logger — `logging.basicConfig()` ist dann
ein No-Op (weder Handler noch Stufe werden gesetzt, siehe Python-Doku), ein `caplog`-Test würde
also nur beweisen, dass caplog selbst funktioniert, nicht dass `serve` das Journal konfiguriert.
Im Unterprozess ist der Root-Logger dagegen unberührt wie im echten Betrieb (systemd fängt
stdout/stderr des Prozesses ein), und der Test prüft die tatsächliche Zeile auf stderr.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pakete import lege_paket
from test_sitzung import anmelden

_SERVE_SKRIPT = (
    "import scanview_server.cli as c\n"
    "c.uvicorn.run = lambda *a, **k: None\n"
    "raise SystemExit(c.main(['serve']))\n"
)


def _serve_env(tmp_path: Path, log_level: str | None) -> dict[str, str]:
    d = tmp_path / "d"
    s = tmp_path / "s"
    d.mkdir()
    s.mkdir()
    env = {
        **os.environ,
        "SCANVIEW_DATA": str(d),
        "SCANVIEW_STATE": str(s),
        "SCANVIEW_ORIGIN": "https://x.test/",
        "SCANVIEW_RP_ID": "x.test",
    }
    if log_level is None:
        env.pop("SCANVIEW_LOG_LEVEL", None)
    else:
        env["SCANVIEW_LOG_LEVEL"] = log_level
    return env


def _serve_stderr(tmp_path: Path, log_level: str | None) -> str:
    r = subprocess.run(
        [sys.executable, "-c", _SERVE_SKRIPT],
        env=_serve_env(tmp_path, log_level),
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert r.returncode == 0, r.stderr
    return r.stderr


def test_serve_ohne_stufe_zeigt_info_von_katalog(tmp_path: Path) -> None:
    """Standard (keine SCANVIEW_LOG_LEVEL gesetzt) = INFO — die Start-Zeile aus katalog.laden()
    (log.info("Katalog: %d Pakete", ...)) muss im Journal (hier: stderr) landen."""
    stderr = _serve_stderr(tmp_path, None)
    assert "INFO scanview.katalog: Katalog: 0 Pakete" in stderr


def test_serve_warning_unterdrueckt_info(tmp_path: Path) -> None:
    stderr = _serve_stderr(tmp_path, "WARNING")
    assert "Katalog: 0 Pakete" not in stderr


def test_serve_unbekannte_stufe_faellt_auf_info_zurueck_mit_warnung(tmp_path: Path) -> None:
    stderr = _serve_stderr(tmp_path, "SCHWATZHAFT")
    assert "INFO scanview.katalog: Katalog: 0 Pakete" in stderr
    assert "SCANVIEW_LOG_LEVEL" in stderr and "SCHWATZHAFT" in stderr
    assert "WARNING scanview.cli:" in stderr


@pytest.fixture
def mit_paketen(client: TestClient) -> TestClient:
    d = client.app.state.settings.data_dir  # type: ignore[attr-defined]
    lege_paket(d, "kirche")
    client.app.state.katalog.laden()  # type: ignore[attr-defined]
    return client


def _authz(c: TestClient, uri: str) -> int:
    r = c.get("/api/authz", headers={"X-Forwarded-Uri": uri, "X-Forwarded-Method": "GET"})
    return r.status_code


def test_authz_debug_protokolliert_eine_zeile_ohne_cookie(
    mit_paketen: TestClient, caplog: pytest.LogCaptureFixture,
) -> None:
    anmelden(mit_paketen, grants={"kirche": "betrachter"})
    cookie_wert = mit_paketen.cookies.get("__Host-scanview")
    assert cookie_wert  # Sitzungscookie tatsächlich gesetzt (sonst testet der Ausschluss nichts)

    caplog.set_level(logging.DEBUG, "scanview")
    caplog.clear()  # Katalog-INFO-Zeile aus dem Fixture nicht mitzählen
    assert _authz(mit_paketen, "/data/kirche/cloud.copc.laz") == 200

    zeilen = [r for r in caplog.records if r.name == "scanview.api"]
    assert len(zeilen) == 1
    assert "authz 200 kirche" in zeilen[0].getMessage()

    text = caplog.text
    assert cookie_wert not in text
    assert "testclient" not in text.lower()


def test_authz_ohne_debug_stufe_keine_zeile(
    mit_paketen: TestClient, caplog: pytest.LogCaptureFixture,
) -> None:
    anmelden(mit_paketen, grants={"kirche": "betrachter"})
    caplog.set_level(logging.INFO, "scanview")
    caplog.clear()
    assert _authz(mit_paketen, "/data/kirche/cloud.copc.laz") == 200
    assert [r for r in caplog.records if r.name == "scanview.api"] == []
