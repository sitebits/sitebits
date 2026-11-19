"""Der Upload wählt ausschließlich Eingang und Freischaltweg seiner Instanz."""
import json
import os
import subprocess
from pathlib import Path

import pytest
from test_instanzen import PLATFORM


@pytest.mark.parametrize("name", ["beispiel", "andere"])
def test_upload_nutzt_nur_eigenen_eingang(tmp_path: Path, name: str) -> None:
    quelle = tmp_path / "quelle"
    quelle.mkdir()
    (quelle / "project.json").write_text(json.dumps({"projectId": "probe", "schemaVersion": 2}))
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "aufrufe"
    for tool, antwort in {"ssh": "echo FREI 0 999999999999", "rsync": ":"}.items():
        p = bin_ / tool
        p.write_text('#!/bin/sh\nprintf "%s %s\\n" "' + tool +
                     '" "$*" >>"$PROBE_LOG"\n' + antwort + '\n')
        p.chmod(0o755)
    r = subprocess.run(["bash", str(PLATFORM / "paket-bereitstellen.sh"), "--instanz",
                        name, str(quelle), "probe-host"], capture_output=True, text=True,
                       env=dict(os.environ, PATH=str(bin_) + ":" + os.environ["PATH"],
                                PROBE_LOG=str(log)))
    assert r.returncode == 0, r.stderr
    aufrufe = log.read_text()
    assert f"probe-host:/srv/scanview-{name}/eingang/probe/" in aufrufe
    assert f"sudo -n /usr/local/sbin/scanview-{name}-paket-freischalten probe" in aufrufe
    assert "/srv/scanview/eingang" not in aufrufe
    assert "/usr/local/sbin/scanview-paket-freischalten" not in aufrufe


@pytest.mark.parametrize("name", ["scanview", "ab-deploy", "../ab", "", "ab;id"])
def test_upload_ungueltige_instanz_vor_ssh_verweigert(tmp_path: Path, name: str) -> None:
    r = subprocess.run(["bash", str(PLATFORM / "paket-bereitstellen.sh"), "--instanz",
                        name, str(tmp_path), "probe-host"], capture_output=True, text=True)
    assert r.returncode != 0
    assert "Instanzname" in r.stderr


@pytest.mark.parametrize("vorn", [True, False], ids=["ersetzen-vorn", "ersetzen-hinten"])
def test_upload_reicht_ersetzen_an_die_instanz_durch(tmp_path: Path, vorn: bool) -> None:
    """`--ersetzen` erreicht über instanz_rendern.py das Skript der Instanz: Abfrage
    `pruefen-ersetzen`, Abschluss `ersetzen`, beides über den Freischaltweg der Instanz."""
    quelle = tmp_path / "quelle"
    quelle.mkdir()
    (quelle / "project.json").write_text(json.dumps({"projectId": "probe", "schemaVersion": 2}))
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "aufrufe"
    for tool, antwort in {"ssh": "echo FREI 0 999999999999", "rsync": ":"}.items():
        p = bin_ / tool
        p.write_text('#!/bin/sh\nprintf "%s %s\\n" "' + tool +
                     '" "$*" >>"$PROBE_LOG"\n' + antwort + '\n')
        p.chmod(0o755)
    argumente = (["--ersetzen", "--instanz", "beispiel"] if vorn
                 else ["--instanz", "beispiel", "--ersetzen"])
    r = subprocess.run(["bash", str(PLATFORM / "paket-bereitstellen.sh"), *argumente,
                        str(quelle), "probe-host"], capture_output=True, text=True,
                       env=dict(os.environ, PATH=str(bin_) + ":" + os.environ["PATH"],
                                PROBE_LOG=str(log)))
    assert r.returncode == 0, r.stderr
    aufrufe = log.read_text()
    skript = "/usr/local/sbin/scanview-beispiel-paket-freischalten"
    assert f"sudo -n {skript} pruefen-ersetzen probe" in aufrufe
    assert f"sudo -n {skript} ersetzen probe" in aufrufe
    assert f"sudo -n {skript} pruefen probe" not in aufrufe
    assert "probe-host:/srv/scanview-beispiel/eingang/probe/" in aufrufe
    assert ".ersetzt-probe" in r.stdout


@pytest.mark.parametrize("instanz", [[], ["--instanz", "beispiel"]], ids=["ohne", "ohne-port"])
def test_ersetzen_ohne_upload_verweigert(tmp_path: Path, instanz: list[str]) -> None:
    """Die Meldung zu --ersetzen kommt vor der Prüfung von Instanz und Port."""
    r = subprocess.run(["python3", "-I", str(PLATFORM / "instanz_rendern.py"), "--ersetzen",
                        *instanz, "--render-root", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 2
    assert "--ersetzen gilt nur für den Upload" in r.stderr
