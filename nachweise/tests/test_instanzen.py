"""Goldene Host-Vorlagen und Rechte der Release-Grundlinie 0.5.0, ohne root."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[2] / "deploy/platform"
GOLDEN = Path(__file__).parent / "fixtures/instanzen-0.5.0.json"


def test_standardinstanz_bytegleich_zu_0_5_0(tmp_path: Path) -> None:
    lauf = subprocess.run(["bash", str(PLATFORM / "einrichten.sh"), "--render-root",
                           str(tmp_path)], capture_output=True, text=True, check=True)
    golden = json.loads(GOLDEN.read_text())
    assert json.loads(lauf.stdout) == golden["plan"]
    for pfad, sha256 in golden["sha256"].items():
        assert hashlib.sha256((tmp_path / pfad.lstrip("/")).read_bytes()).hexdigest() == sha256
    for _, pfad, _, _, modus in golden["plan"]["dateien"]:
        assert (tmp_path / pfad.lstrip("/")).stat().st_mode & 0o7777 == int(modus, 8)


def test_golden_erkennt_veraenderte_vorlage(tmp_path: Path) -> None:
    """Negativkontrolle: eine einzelne zusätzliche Zeile muss den Byte-Nachweis brechen."""
    golden = json.loads(GOLDEN.read_text())
    pfad = "/etc/systemd/system/scanview-server.service"
    daten = (PLATFORM / "scanview-server.service").read_bytes() + b"\n"
    assert hashlib.sha256(daten).hexdigest() != golden["sha256"][pfad]


def _renderer():
    import runpy
    return runpy.run_path(str(PLATFORM / "instanz_rendern.py"))


def test_instanz_ableitung_eine_stelle() -> None:
    m = _renderer()
    x = m["Instanz"]("beispiel", 8811)
    assert x.pfade == {
        "code": "/opt/scanview-beispiel", "srv": "/srv/scanview-beispiel",
        "etc": "/etc/scanview-beispiel", "log": "/var/log/scanview-beispiel",
        "backups": "/var/backups/scanview-beispiel",
        "install": "/var/lib/scanview-install-beispiel",
        "freischalten": "/usr/local/sbin/scanview-beispiel-paket-freischalten",
        "sudoers": "/etc/sudoers.d/scanview-beispiel-deploy",
        "caddy": "/etc/caddy/scanview-beispiel.caddy",
        "jail": "/etc/fail2ban/jail.d/scanview-beispiel.local",
        "filter": "/etc/fail2ban/filter.d/scanview-beispiel.conf",
    }
    assert x.nutzer == "scanview-beispiel"
    assert x.deploy == "scanview-beispiel-deploy"
    assert x.server == "scanview-beispiel-server"


def test_instanznamen_und_ports() -> None:
    import pytest
    instanz = _renderer()["Instanz"]
    for name in ("ab", "beispiel", "a-", "a" * 20, "a1-b2"):
        assert instanz(name, 8811).name == name
    for name in ("a", "a" * 21, "Aaa", "-abc", "1abc", "ab_", "äb", "scanview",
                 "ab-deploy", "../ab", "ab\n", "ab;id", "ab deploy"):
        with pytest.raises(ValueError, match="Instanzname"):
            instanz(name, 8811)
    for port in (None, 0, -1, 80, 8803, 65536, "8811;id", "01", True):
        with pytest.raises(ValueError, match="Port"):
            instanz("beispiel", port)
    assert instanz("beispiel", 65535).port == 65535


def test_port_anderer_instanz_verweigert(tmp_path: Path) -> None:
    import pytest
    m = _renderer()
    units = tmp_path / "etc/systemd/system"
    units.mkdir(parents=True)
    (units / "scanview-andere-server.service").write_text(
        "ExecStart=/opt/scanview-andere/server/.venv/bin/scanview-server serve --port 8811\n")
    with pytest.raises(ValueError, match=r"Port.*belegt"):
        m["port_pruefen"](m["Instanz"]("beispiel", 8811), tmp_path, sockets=False)
    m["port_pruefen"](m["Instanz"]("beispiel", 8812), tmp_path, sockets=False)


def test_eigener_port_fuer_update_erlaubt(tmp_path: Path) -> None:
    m = _renderer()
    units = tmp_path / "etc/systemd/system"
    units.mkdir(parents=True)
    (units / "scanview-beispiel-server.service").write_text("ExecStart=x --port 8811\n")
    m["port_pruefen"](m["Instanz"]("beispiel", 8811), tmp_path, sockets=False)
