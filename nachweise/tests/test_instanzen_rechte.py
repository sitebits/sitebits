"""Benannte Units, Caddy, Jail und Freischaltweg haben getrennte Ziele."""
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_instanzen import PLATFORM, _renderer


def test_vorlagen_der_benannten_instanz(tmp_path: Path) -> None:
    m = _renderer()
    m["rendern"](PLATFORM, tmp_path, m["Instanz"]("beispiel", 8811))
    unit = (tmp_path / "etc/systemd/system/scanview-beispiel-server.service").read_text()
    assert "User=scanview-beispiel\nGroup=scanview-beispiel" in unit
    assert "/opt/scanview-beispiel/server/.venv/bin/scanview-server serve --port 8811" in unit
    assert "/etc/scanview-beispiel/scanview-server.env" in unit
    assert "ReadWritePaths=/srv/scanview-beispiel/state" in unit
    assert not list(tmp_path.rglob("*git-export*.service"))
    caddy = (tmp_path / "etc/caddy/scanview-beispiel.caddy").read_text()
    assert "{$SCANVIEW_" not in caddy
    assert "127.0.0.1:8811" in caddy
    assert "root * /srv/scanview-beispiel/data" in caddy
    assert "root * /opt/scanview-beispiel/web" in caddy
    jail = (tmp_path / "etc/fail2ban/jail.d/scanview-beispiel.local").read_text()
    assert "[scanview-beispiel]" in jail
    assert "_SYSTEMD_UNIT=scanview-beispiel-server.service" in jail
    assert "filter       = scanview-beispiel" in jail
    filter_ = (tmp_path / "etc/fail2ban/filter.d/scanview-beispiel.conf").read_text()
    assert "failregex = ^.*scanview Anmeldung fehlgeschlagen" in filter_


@pytest.mark.parametrize("name", ["", "beispiel"])
def test_deployweg_hat_keine_argumente_fuer_fremde_instanz(tmp_path: Path, name: str) -> None:
    m = _renderer()
    x = m["Instanz"](name, 8811 if name else None)
    plan = m["rendern"](PLATFORM, tmp_path, x)
    skript = tmp_path / x.pfade["freischalten"].lstrip("/")
    text = skript.read_text()
    assert "EINGANG=" + x.pfade["srv"] + "/eingang\n" in text
    assert "WURZEL=" + x.pfade["srv"] + "/data\n" in text
    assert "systemctl reload " + x.server in text
    for args in (["/srv/scanview-andere/data", "id"], ["--instanz", "andere"],
                 ["pruefen", "id", "/srv/scanview/data"], ["id", "--port", "8812"]):
        r = subprocess.run(["bash", str(skript), *args], capture_output=True, text=True)
        assert r.returncode == 2
    regel = tmp_path / x.pfade["sudoers"].lstrip("/")
    freigaben = [z for z in regel.read_text().splitlines() if z and not z.startswith("#")]
    assert freigaben == [f"%{x.deploy} ALL=(root) NOPASSWD: {x.pfade['freischalten']}"]
    fremd = m["Instanz"]("andere", 8812).pfade["freischalten"]
    assert fremd not in freigaben[0]
    assert "bash" not in freigaben[0] and "rsync" not in freigaben[0]
    visudo = shutil.which("visudo")
    if visudo:
        subprocess.run([visudo, "-cf", str(regel)], check=True,
                       capture_output=True, text=True)
    assert all("andere" not in d[1] for d in plan["dateien"])


def test_deploykonto_in_fremder_dienstgruppe_verweigert(monkeypatch) -> None:
    m = _renderer()
    gruppen = [SimpleNamespace(gr_name="scanview-beispiel-deploy", gr_gid=992,
                               gr_mem=["deploy-beispiel"]),
               SimpleNamespace(gr_name="scanview", gr_gid=990, gr_mem=["deploy-beispiel"])]
    monkeypatch.setattr(m["grp"], "getgrall", lambda: gruppen)
    monkeypatch.setattr(m["pwd"], "getpwall", lambda: [
        SimpleNamespace(pw_name="deploy-beispiel", pw_gid=992)])
    with pytest.raises(ValueError, match="Rechtegrenze"):
        m["konten_pruefen"](m["Instanz"]("beispiel", 8811))


def test_port_gleichheitsoption_wird_erkannt(tmp_path: Path) -> None:
    m = _renderer()
    units = tmp_path / "etc/systemd/system"
    units.mkdir(parents=True)
    (units / "scanview-andere-server.service").write_text("ExecStart=x --port=8811\n")
    with pytest.raises(ValueError, match=r"Port.*belegt"):
        m["port_pruefen"](m["Instanz"]("beispiel", 8811), tmp_path, sockets=False)
