"""Isolation der Importaufträge mit echtem `systemd-run --user`: derselbe Lauf, den der Worker für
`neu-ohne-wolke` baut, mit einer Attrappe statt des Imports. Sie versucht, was ein ausgebrochener
Parser versuchen würde: Container starten (Docker-Socket, docker/podman), Netz, fremde Uploads
und Pakete lesen, in den eigenen Upload oder die Übergabe schreiben."""

from __future__ import annotations

import json
import shutil
import sys
import threading
from pathlib import Path

import pytest
from test_isolation import _user_manager
from uploadwelt import SPZ, Welt, welt  # noqa: F401

from scanview_server import auftraege
from scanview_worker.laeufer import SystemdLaeufer
from scanview_worker.worker import Worker

pytestmark = pytest.mark.skipif(not _user_manager(), reason="kein systemd-User-Manager erreichbar")

ATTRAPPE = r"""
import json, os, socket, subprocess, sys
befehl = sys.argv[1]
if befehl == "verify":
    sys.exit(0)
quelle, ziel = sys.argv[2], sys.argv[3]
r = {}
for sock in ("/var/run/docker.sock", "/run/docker.sock", "/run/podman/podman.sock",
             f"/run/user/{os.getuid()}/podman/podman.sock",
             f"/run/user/{os.getuid()}/docker.sock"):
    try:
        s = socket.socket(socket.AF_UNIX)
        s.connect(sock)
        r[sock] = "offen"
    except OSError:
        r[sock] = "zu"
for prog in ("docker", "podman"):
    for pfad in ("/usr/bin/" + prog, "/usr/local/bin/" + prog):
        if os.path.exists(pfad):
            try:
                p = subprocess.run([pfad, "ps"], capture_output=True, timeout=20)
                r[pfad] = "offen" if p.returncode == 0 else "zu"
            except (OSError, subprocess.SubprocessError):
                r[pfad] = "zu"
try:
    os.unshare(os.CLONE_NEWUSER); r["userns"] = "offen"
except (OSError, AttributeError):
    r["userns"] = "zu"
try:
    socket.create_connection(("1.1.1.1", 443), timeout=3).close(); r["netz"] = "offen"
except OSError:
    r["netz"] = "zu"
r["quelle_lesbar"] = open(quelle, "rb").read(3) == b"\x1f\x8b\x08"
try:
    open(quelle, "ab").write(b"x"); r["quelle_schreiben"] = "offen"
except OSError:
    r["quelle_schreiben"] = "zu"
upload = os.path.dirname(quelle)
uploads = os.path.dirname(upload)
r["uploads_sichtbar"] = sorted(os.listdir(uploads))
for name in sys.argv[4:]:
    try:
        open(name, "rb").read(1); r[name] = "lesbar"
    except OSError:
        r[name] = "zu"
os.makedirs(ziel)
open(os.path.join(ziel, "project.json"), "w").write('{"schemaVersion": 2}')
open(os.path.join(ziel, "probe.json"), "w").write(json.dumps(r))
"""


def test_importauftrag_kann_keinen_container_starten(welt: Welt) -> None:  # noqa: F811
    c = welt.c["admin"]
    fremd = welt.hochladen(c, {"fremd.spz": SPZ})
    aid = welt.import_starten(c, {"eigen.spz": SPZ}, "neu_splats", projekt="neu-iso")
    eigen = welt.jobs()
    try:
        a = auftraege.import_lesen(eigen, aid)
    finally:
        eigen.close()
    assert a is not None
    (welt.settings.data_dir / "ost" / "geheim.txt").write_text("PAKET")
    bin_ = welt.tmp / "import-bin"
    bin_.mkdir()
    skript = bin_ / "scanview-import"
    skript.write_text(f"#!{sys.executable} -I\n{ATTRAPPE}")
    skript.chmod(0o755)
    # Die Attrappe bekommt dieselbe Argumentliste wie der echte Import; zusätzlich lesen wir
    # fremde Ziele über eine Umleitung der Argumente nicht nach — die Attrappe prüft sie selbst.
    ziele = [str(welt.wurzel / fremd["id"] / "fremd.spz"),
             str(welt.settings.data_dir / "ost" / "geheim.txt"),
             str(welt.settings.db_path)]
    ergebnis: dict[str, object] = {}
    ende = threading.Event()

    def freischalten() -> None:
        ueb, mel = welt.tmp / "uebergabe", welt.tmp / "meldungen"
        while not ende.wait(0.1):
            if (ueb / "neu-iso" / "probe.json").is_file():
                ergebnis.update(json.loads((ueb / "neu-iso" / "probe.json").read_text()))
                (mel / "neu-iso").write_text("OK\n")
                return

    class MitZielen(SystemdLaeufer):
        def befehl(self, lauf):  # type: ignore[no-untyped-def]
            cmd = super().befehl(lauf)
            return [*cmd, *ziele] if lauf.argv[1] == "neu-ohne-wolke" else cmd

    t = threading.Thread(target=freischalten, daemon=True)
    t.start()
    w = Worker(welt.einstellungen(), MitZielen(unsichtbar=(welt.settings.state_dir,)))
    try:
        w.start()
        w.schleife(einmal=True)
    finally:
        ende.set()
        t.join()
        w.conn.close()
    jc = welt.jobs()
    try:
        fertig = auftraege.import_lesen(jc, aid)
    finally:
        jc.close()
    assert fertig is not None and fertig.status == "fertig", fertig
    offen = {k: v for k, v in ergebnis.items() if v == "offen"}
    assert offen == {}, ergebnis
    assert ergebnis["quelle_lesbar"] is True and ergebnis["quelle_schreiben"] == "zu"
    assert ergebnis["uploads_sichtbar"] == [a.optionen["upload"]]
    assert all(ergebnis[z] == "zu" for z in ziele), ergebnis
    if shutil.which("docker"):
        assert any(k.endswith("/docker") for k in ergebnis), "docker-Aufruf nicht geprüft"
    assert (welt.wurzel / a.optionen["upload"] / "eigen.spz").read_bytes() == SPZ


def test_gegenprobe_ohne_isolation_ist_docker_erreichbar(tmp_path: Path) -> None:
    """Die Attrappe erkennt einen offenen Docker-Socket überhaupt (nur, wo der Testnutzer
    Docker nutzen darf)."""
    import subprocess

    if shutil.which("docker") is None or subprocess.run(
            ["docker", "info"], capture_output=True, check=False, timeout=30).returncode != 0:
        pytest.skip("Docker für den Testnutzer nicht nutzbar")
    quelle = tmp_path / "u" / "a.spz"
    quelle.parent.mkdir()
    quelle.write_bytes(SPZ)
    ziel = tmp_path / "ziel"
    subprocess.run([sys.executable, "-I", "-c", ATTRAPPE, "neu-ohne-wolke", str(quelle),
                    str(ziel)], check=True, timeout=120)
    r = json.loads((ziel / "probe.json").read_text())
    assert r["/var/run/docker.sock"] == "offen" and r["/usr/bin/docker"] == "offen"
