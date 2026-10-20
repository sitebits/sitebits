"""Rootlose Installation/Update im Präfix mit echten Dateien, Rechten und ACL-Hashlisten."""
import ctypes
import errno
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from test_instanzen import PLATFORM, _renderer


def _hashliste(root: Path, name: str) -> dict:
    out = {}
    m = _renderer()
    x = m["Instanz"]() if name == "scanview" else m["Instanz"](name, 8811)
    plan = m["beschreibung"](x)
    baeume = [root / x.pfade[k].lstrip("/")
              for k in ("code", "srv", "etc", "log", "backups", "install")]
    dateien = [root / d[1].lstrip("/") for d in plan["dateien"]]
    for p in root.rglob("*"):
        if p not in dateien and not any(p == b or b in p.parents for b in baeume):
            continue
        s = p.lstat()
        out[str(p.relative_to(root))] = (
            s.st_uid, s.st_gid, s.st_mode,
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None,
            tuple((k, os.getxattr(p, k)) for k in os.listxattr(p)),
        )
    return out


def test_zweite_instanz_und_update_hashdiff_standard_leer(tmp_path: Path) -> None:
    m = _renderer()
    m["rendern"](PLATFORM, tmp_path)
    before = _hashliste(tmp_path, "scanview")
    x = m["Instanz"]("beispiel", 8811)
    m["rendern"](PLATFORM, tmp_path, x)
    assert _hashliste(tmp_path, "scanview") == before
    env = tmp_path / "etc/scanview-beispiel/scanview-server.env"
    env.write_text("SCANVIEW_ORIGIN=https://beispiel.example.org\n")
    m["rendern"](PLATFORM, tmp_path, x)
    assert env.read_text() == "SCANVIEW_ORIGIN=https://beispiel.example.org\n"
    assert _hashliste(tmp_path, "scanview") == before


def _installer_laufen(root: Path, instanz, *, basis: bool = False):
    """Nur privilegierte Systemwerkzeuge stubben; Bash-Kontrollfluss und Dateien sind echt."""
    m = _renderer()
    source = root / "quelle"
    (source / "server").mkdir(parents=True, exist_ok=True)
    (source / "web/dist").mkdir(parents=True, exist_ok=True)
    (source / "server/uv.lock").write_text("version = 1\n")
    for f in ("index.html", "portal.html"):
        (source / "web/dist" / f).write_text("<!doctype html>\n")
    dest = source / "deploy/platform"
    shutil.copytree(PLATFORM, dest, dirs_exist_ok=True)
    bin_ = root / "bin"
    bin_.mkdir(exist_ok=True)
    stubs = {
        "id": "#!/bin/sh\nexit 0\n",
        "getent": "#!/bin/sh\nexit 0\n",
        "chown": "#!/bin/sh\nexit 0\n",
        "setfacl": "#!/bin/sh\nexit 0\n",
        "systemctl": "#!/bin/sh\nexit 0\n",
        "git": "#!/bin/sh\nprintf '%040d\\n' 1\n",
        "date": "#!/bin/sh\necho 2026-10-04T12:00:00Z\n",
        "uv": "#!/bin/sh\nmkdir -p .venv\n",
        "curl": ('#!/bin/sh\necho \'{"kontakt":"x","erklaerung":"x",'
                 '"impressum":"x","datenschutz":"x"}\'\n'),
        "runuser": '#!/bin/sh\nshift 3\nexec "$@"\n',
        "rsync": ('#!/bin/bash\nquelle=${@: -2:1}; ziel=${@: -1}\n'
                  'mkdir -p "$ziel"\ncp -a "$quelle." "$ziel"\n'),
        "install": ('#!/bin/bash\na=(); while (($#)); do case $1 in -o|-g) shift 2;; '
                    '*) a+=("$1"); shift;; esac; done\nexec /usr/bin/install "${a[@]}"\n'),
        "stat": ('#!/bin/sh\nif [ "$1" = -c ] && [ "$2" = %u ]; then echo 0; '
                 'else exec /usr/bin/stat "$@"; fi\n'),
        "sqlite3": ('#!/usr/bin/python3\nimport sqlite3,sys\n'
                    'c=sqlite3.connect(sys.argv[1]); '
                    'out=sys.argv[2].split(" ",1)[1].strip("\\\'")\n'
                    'd=sqlite3.connect(out); c.backup(d); d.close(); c.close()\n'),
    }
    for name, content in stubs.items():
        (bin_ / name).write_text(content)
        (bin_ / name).chmod(0o755)
    for folder in ("var/tmp", "etc/sudoers.d", "etc/systemd/system", "usr/local/sbin"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    if basis:
        text = _installer_0_5_0(PLATFORM)
    else:
        text = m["installer_rendern"](PLATFORM, instanz)
    text = text.replace("[[ $EUID -eq 0 ]]", "[[ 1 -eq 1 ]]")
    text = re.sub(r"(?<![\w])/(srv|opt|etc|var)/", lambda a: str(root) + a[0], text)
    text = text.replace("/usr/local/sbin/", str(root) + "/usr/local/sbin/")
    # Nur der Stub sieht die temporäre Wurzel; installierte Vorlagen behalten Hostpfade.
    skript = root / "lauf.sh"
    skript.write_text(text)
    return subprocess.run(["bash", str(skript), str(source)], capture_output=True, text=True,
                          env=dict(os.environ, PATH=str(bin_) + ":" + os.environ["PATH"]))


def test_echter_installer_update_nur_eigene_db_sicherung_und_logs(tmp_path: Path) -> None:
    # Vorabprobe mit libacl liefert das echte errno; setfacl meldet es nur als Text.
    probe = tmp_path / "acl-vorabprobe"
    probe.mkdir()
    libacl = ctypes.CDLL("libacl.so.1", use_errno=True)
    libacl.acl_from_text.argtypes = [ctypes.c_char_p]
    libacl.acl_from_text.restype = ctypes.c_void_p
    libacl.acl_set_file.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
    libacl.acl_free.argtypes = [ctypes.c_void_p]
    acl = libacl.acl_from_text(b"u::rwx,u:65534:r-x,g::r-x,m::r-x,o::r-x")
    assert acl
    try:
        rc = libacl.acl_set_file(os.fsencode(probe), 0x8000, acl)  # ACL_TYPE_ACCESS
        code = ctypes.get_errno() if rc != 0 else 0
    finally:
        libacl.acl_free(acl)
    if code == errno.EINVAL:
        pytest.skip("Sandbox ohne Fremd-UID – außerhalb prüfen")  # noqa: RUF001 — Vorgabe
    assert rc == 0, os.strerror(code)
    m = _renderer()
    r = _installer_laufen(tmp_path, m["Instanz"](), basis=True)
    assert r.returncode == 0, r.stderr
    daten = tmp_path / "srv/scanview/data"
    subprocess.run(["setfacl", "-m", "u:65534:r-x", str(daten)], check=True)
    before = _hashliste(tmp_path, "scanview")
    x = m["Instanz"]("beispiel", 8811)
    r = _installer_laufen(tmp_path, x)
    assert r.returncode == 0, r.stderr
    assert _hashliste(tmp_path, "scanview") == before
    db = tmp_path / "srv/scanview-beispiel/state/scanview.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE probe (x)")
        c.execute("INSERT INTO probe VALUES (42)")
    r = _installer_laufen(tmp_path, x)
    assert r.returncode == 0, r.stderr
    assert _hashliste(tmp_path, "scanview") == before
    backups = list((tmp_path / "var/backups/scanview-beispiel/update-sicherungen").glob("*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as c:
        assert c.execute("SELECT x FROM probe").fetchone() == (42,)
    log = (tmp_path / "var/log/scanview-beispiel/updates.log").read_text()
    assert log.count("ergebnis=ok") == 2
    assert "sicherung=" + str(backups[0]) in log


def test_atomare_ausgabe_erhaelt_alte_inode_und_setzt_metadaten(tmp_path: Path) -> None:
    datei = tmp_path / "sudoers"
    datei.write_bytes(b"vorher")
    datei.chmod(0o440)
    with datei.open("rb") as alt:
        _renderer()["datei_atomar"](datei, b"nachher", 0o440)
        assert alt.read() == b"vorher"
        assert os.fstat(alt.fileno()).st_mode & 0o7777 == 0o440
    assert datei.read_bytes() == b"nachher"
    assert datei.stat().st_mode & 0o7777 == 0o440
    assert (datei.stat().st_uid, datei.stat().st_gid) == (os.geteuid(), os.getegid())
    assert list(tmp_path.iterdir()) == [datei]


def _installer_0_5_0(platform: Path) -> str:
    """Fassung von `einrichten.sh` aus 0.5.0. Der öffentliche Export hat keine Git-Historie;
    dort überspringt sich der Vergleich, im privaten Repo und in der Release-Kette läuft er."""
    import subprocess

    lauf = subprocess.run(["git", "show", "3a398ba:deploy/platform/einrichten.sh"],
                          cwd=platform, capture_output=True, text=True, check=False)
    if lauf.returncode != 0:
        pytest.skip("Vergleich mit 0.5.0 braucht die Git-Historie (nur im privaten Repo)")
    return lauf.stdout
