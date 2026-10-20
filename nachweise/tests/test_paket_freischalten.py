"""Freischalt-Skript `deploy/platform/scanview-paket-freischalten` (Befund B-WB-M4).

Der Deploy-Nutzer darf per sudo nur noch dieses root-eigene Skript starten (kein `sudo bash`,
kein `sudo rsync`). Die Laufzeittests führen es **als root** in einem Wegwerf-Container aus,
denn geprüft wird genau das Verhalten als root: Verschieben, Eigentümer, Rechte, Abbruch ohne
Änderung.

Abweichung von `werkzeuge.docker_befehl` (verweigert uid 0): ein eigener, ebenso gehärteter
Aufruf — gepinntes Image aus `werkzeuge.json` (`deploy-probe`), `--pull never`,
`--network none`, `--read-only`, `--cap-drop ALL` und nur `CHOWN`, `FOWNER`, `DAC_OVERRIDE`
zurück (Eigentümer/Rechte fremder Dateien ändern), `no-new-privileges`. Aller Zustand liegt in
einem tmpfs unter `/srv/scanview`; nur Skript, Stubs und Kontendateien sind lesend eingehängt.
`systemctl` ist ein Stub, der seine Aufrufe protokolliert; `setfacl`/`getfacl` sind die echten
Debian-Werkzeuge (passend zur `libacl1` des Images, bereitgestellt über
`scanview_nachweise.deploy_probe`), damit ACLs wirklich gesetzt und gelesen werden;
`/usr/bin/python3` verweist auf das Python des Images (auf dem Host ist es Debian-Python).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from scanview_nachweise import deploy_probe
from scanview_nachweise.werkzeuge import lade_werkzeuge

REPO_WURZEL = Path(__file__).resolve().parents[2]
PLATFORM = REPO_WURZEL / "deploy" / "platform"
SKRIPT = PLATFORM / "scanview-paket-freischalten"
SUDOERS = PLATFORM / "sudoers.d" / "scanview-deploy"
BEREITSTELLEN = PLATFORM / "paket-bereitstellen.sh"
EINRICHTEN = PLATFORM / "einrichten.sh"
WERKZEUGE_JSON = REPO_WURZEL / "nachweise" / "werkzeuge.json"

ZIEL_SKRIPT = "/usr/local/sbin/scanview-paket-freischalten"
REGEL = "%scanview-deploy ALL=(root) NOPASSWD: /usr/local/sbin/scanview-paket-freischalten"

DATEN = "/srv/scanview/data"
EINGANG = "/srv/scanview/eingang"
UID_DEPLOY = 1000
UID_SCANVIEW = 990
GID_SCANVIEW = 990
GID_DEPLOYGRUPPE = 992

PASSWD = (
    "root:x:0:0:root:/root:/bin/bash\n"
    f"scanview:x:{UID_SCANVIEW}:{GID_SCANVIEW}::/nonexistent:/usr/sbin/nologin\n"
    "caddy:x:991:991::/nonexistent:/usr/sbin/nologin\n"
    f"deploy:x:{UID_DEPLOY}:{UID_DEPLOY}::/nonexistent:/usr/sbin/nologin\n"
)
GROUP = (
    "root:x:0:\n"
    f"scanview:x:{GID_SCANVIEW}:\n"
    "caddy:x:991:\n"
    f"deploy:x:{UID_DEPLOY}:\n"
    f"scanview-deploy:x:{GID_DEPLOYGRUPPE}:deploy\n"
)
STUB = "#!/bin/sh\nprintf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >>/tmp/aufrufe\n"
PYTHON_SHIM = "#!/bin/sh\nexec /usr/local/bin/python3 \"$@\"\n"

#: Stubs für den Ende-zu-Ende-Lauf von paket-bereitstellen.sh im selben Container: ssh führt den
#: entfernten Befehl lokal aus, sudo lässt nur das Freischalt-Skript zu (wie die sudoers-Regel),
#: rsync kopiert in den Eingang und gibt ihn dem Deploy-Nutzer (uid 1000) — wie ein Upload ohne
#: sudo. Jeder Aufruf landet im Protokoll.
E2E_STUBS = {
    "ssh": "#!/bin/bash\nshift\nexec bash -c \"$*\"\n",
    "sudo": (
        "#!/bin/bash\n"
        "[ \"$1\" = -n ] || { echo 'sudo ohne -n' >&2; exit 1; }\n"
        "shift\n"
        "echo \"sudo $*\" >>/tmp/aufrufe\n"
        "if [ -e /srv/scanview/keine-regel ]; then echo 'sudo: nicht erlaubt' >&2; exit 1; fi\n"
        "if [ \"$1\" = -l ] && [ $# -eq 2 ] && [ \"$2\" = " + ZIEL_SKRIPT + " ]; then\n"
        "  echo \"$2\"; exit 0\n"
        "fi\n"
        "[ \"$1\" = " + ZIEL_SKRIPT + " ] || { echo \"sudo: nicht erlaubt: $*\" >&2; exit 1; }\n"
        "exec \"$@\"\n"
    ),
    "rsync": (
        "#!/bin/bash\n"
        "echo \"rsync $*\" >>/tmp/aufrufe\n"
        "for a; do case $a in --rsync-path*) echo 'rsync: --rsync-path verboten' >&2; exit 1;; "
        "esac; done\n"
        "quelle=${@: -2:1}; ziel=${@: -1}; ziel=${ziel#*:}\n"
        "mkdir -p \"$ziel\" && cp -rL \"$quelle.\" \"$ziel\" && chown -R 1000:1000 \"$ziel\"\n"
    ),
}

#: Treiber im Container (läuft als root): Grundzustand wie nach einrichten.sh, dann die
#: Vorbereitung des Tests, Zustand vorher, Skriptlauf, Zustand nachher — alles als JSON.
TREIBER = r'''
import json, os, stat, subprocess, sys

vorbereitung, befehl = sys.argv[1], sys.argv[2:]
GRUND = """
set -eu
paket() {  # paket <ordner> <projectId>: Paket, wie es der Deploy-Nutzer hochlädt
    mkdir -p "$1/unter"
    printf '{"schemaVersion": 2, "projectId": "%s"}\n' "$2" >"$1/project.json"
    printf 'punkte\n' >"$1/unter/punkte.bin"
    chown -R 1000:1000 "$1"
    chmod -R u=rwX,g=rX,o= "$1"
}
install -d -o root -g scanview -m 0750 /srv/scanview/data
install -d -o root -g scanview-deploy -m 1770 /srv/scanview/eingang
"""
subprocess.run(["bash", "-c", GRUND + vorbereitung], check=True)


def zustand():
    out = {}
    for wurzel, ordner, dateien in os.walk("/srv/scanview"):
        for name in ordner + dateien:
            p = os.path.join(wurzel, name)
            s = os.lstat(p)
            typ = ("l" if stat.S_ISLNK(s.st_mode) else "d" if stat.S_ISDIR(s.st_mode)
                   else "f" if stat.S_ISREG(s.st_mode) else "a")
            out[p] = [typ, s.st_uid, s.st_gid, oct(stat.S_IMODE(s.st_mode)),
                      sorted(os.listxattr(p, follow_symlinks=False))]
    return out


def acls():
    """Echte ACLs (getfacl, numerisch, ohne Kopf) aller Nicht-Symlinks unter /srv/scanview."""
    out = {}
    for p, (typ, *_rest) in zustand().items():
        if typ in "df":
            r = subprocess.run(["getfacl", "-c", "-n", "-p", p], capture_output=True,
                               text=True, check=True)
            out[p] = [z for z in r.stdout.splitlines() if z]
    return out


vorher = zustand()
umgebung = dict(os.environ, PATH="/probe/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
r = subprocess.run(befehl, capture_output=True, text=True, env=umgebung)
# Optional: eine Nachbereitung, die die Vorbereitung abgelegt hat (läuft nach dem Skript).
nach = ""
if os.path.exists("/tmp/nach.sh"):
    nach = subprocess.run(["bash", "/tmp/nach.sh"], capture_output=True, text=True).stdout
nachher = zustand()
try:
    with open("/tmp/aufrufe") as f:
        aufrufe = f.read().splitlines()
except FileNotFoundError:
    aufrufe = []
print(json.dumps({"rc": r.returncode, "stdout": r.stdout, "stderr": r.stderr,
                  "vorher": vorher, "nachher": nachher, "aufrufe": aufrufe, "acl": acls(),
                  "nach": nach}))
'''


def _image() -> str:
    return lade_werkzeuge(WERKZEUGE_JSON)["deploy-probe"].image


def _probe_da() -> bool:
    if shutil.which("docker") is None or deploy_probe.bereit() is None:
        return False
    try:
        image = _image()
    except KeyError:
        return False
    return subprocess.run(["docker", "image", "inspect", image], capture_output=True,
                          check=False).returncode == 0


probe_da = pytest.mark.skipif(not _probe_da(),
                              reason="docker, gepinntes deploy-probe-Image oder setfacl/getfacl "
                                     "fehlen (python -m scanview_nachweise.deploy_probe)")


@dataclass
class Lauf:
    rc: int
    stdout: str
    stderr: str
    vorher: dict[str, Any]
    nachher: dict[str, Any]
    aufrufe: list[str]
    acl: dict[str, list[str]]
    nach: str = ""

    def unveraendert(self) -> None:
        assert self.nachher == self.vorher
        assert self.aufrufe == []


def _grundbefehl(tmp_path: Path, *, nutzer: str, eigenes_eingang_fs: bool = False,
                 group: str = GROUP, nofile: int | None = None) -> list[str]:
    """Gehärteter `docker run` für die Probe (Begründung der Abweichung: Modul-Docstring)."""
    acl_bin = deploy_probe.bereit()
    assert acl_bin is not None
    dateien = {"passwd": PASSWD, "group": group, "stub": STUB, "python3": PYTHON_SHIM,
               "treiber.py": TREIBER}
    for name, inhalt in dateien.items():
        (tmp_path / name).write_text(inhalt, encoding="utf-8")
    for name in ("stub", "python3"):
        (tmp_path / name).chmod(0o755)
    befehl = [
        "docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "FOWNER",
        "--cap-add", "DAC_OVERRIDE", "--security-opt", "no-new-privileges",
        "--user", nutzer,
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,mode=1777",
        # size: paket-bereitstellen.sh verlangt ≥ 10 GiB Rest (tmpfs belegt nur, was drin ist).
        "--tmpfs", "/srv/scanview:rw,nosuid,nodev,mode=0755,size=20g",
    ]
    if eigenes_eingang_fs:
        befehl += ["--tmpfs", f"{EINGANG}:rw,nosuid,nodev,mode=1770"]
    if nofile is not None:
        befehl += ["--ulimit", f"nofile={nofile}:{nofile}"]
    einhaengen = {
        SKRIPT: ZIEL_SKRIPT,
        tmp_path / "stub": "/usr/bin/systemctl",
        tmp_path / "python3": "/usr/bin/python3",
        tmp_path / "passwd": "/etc/passwd",
        tmp_path / "group": "/etc/group",
        tmp_path / "treiber.py": "/probe/treiber.py",
    }
    (tmp_path / "bin").mkdir()
    for name, inhalt in E2E_STUBS.items():
        (tmp_path / "bin" / name).write_text(inhalt, encoding="utf-8")
        (tmp_path / "bin" / name).chmod(0o755)
    einhaengen[tmp_path / "bin"] = "/probe/bin"
    einhaengen[BEREITSTELLEN] = "/probe/paket-bereitstellen.sh"
    einhaengen[acl_bin / "setfacl"] = "/usr/bin/setfacl"
    einhaengen[acl_bin / "getfacl"] = "/usr/bin/getfacl"
    for quelle, ziel in einhaengen.items():
        # --mount statt -v: eine fehlende Quelle ist ein Fehler, statt dass docker (als root)
        # dort einen leeren Ordner anlegt.
        assert "," not in f"{quelle}{ziel}"
        befehl += ["--mount", f"type=bind,source={quelle},target={ziel},readonly"]
    return [*befehl, _image()]


def _probe(tmp_path: Path, vorbereitung: str, *argumente: str,
           eigenes_eingang_fs: bool = False, aufruf: tuple[str, ...] = (ZIEL_SKRIPT,),
           group: str = GROUP, nofile: int | None = None) -> Lauf:
    befehl = _grundbefehl(tmp_path, nutzer="0:0", eigenes_eingang_fs=eigenes_eingang_fs,
                          group=group, nofile=nofile)
    ergebnis = subprocess.run(
        [*befehl, "/usr/local/bin/python3", "/probe/treiber.py", vorbereitung, *aufruf,
         *argumente],
        capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=120)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    d = json.loads(ergebnis.stdout)
    return Lauf(d["rc"], d["stdout"], d["stderr"], d["vorher"], d["nachher"], d["aufrufe"],
                d["acl"], d["nach"])


PID = "musterhalle-nord-v2"


# --- Freischalten --------------------------------------------------------------------------------

@pytest.mark.docker
@probe_da
def test_gueltige_id_wird_verschoben_und_gesperrt(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}", PID)
    assert lauf.rc == 0, lauf.stderr
    ziel = f"{DATEN}/{PID}"
    assert f"{EINGANG}/{PID}" not in lauf.nachher
    assert not any(".freischalten" in p for p in lauf.nachher)
    acl = ["system.posix_acl_access"]
    assert lauf.nachher[ziel] == ["d", 0, GID_SCANVIEW, "0o750", acl]
    assert lauf.nachher[f"{ziel}/unter"] == ["d", 0, GID_SCANVIEW, "0o750", acl]
    assert lauf.nachher[f"{ziel}/project.json"] == ["f", 0, GID_SCANVIEW, "0o640", acl]
    assert lauf.nachher[f"{ziel}/unter/punkte.bin"] == ["f", 0, GID_SCANVIEW, "0o640", acl]
    # Echte ACL (getfacl): genau caddy dazu, keine Default-ACL, kein anderer benannter Nutzer.
    ordner_acl = ["user::rwx", "user:991:r-x", "group::r-x", "mask::r-x", "other::---"]
    datei_acl = ["user::rw-", "user:991:r--", "group::r--", "mask::r--", "other::---"]
    assert lauf.acl[ziel] == ordner_acl
    assert lauf.acl[f"{ziel}/unter"] == ordner_acl
    assert lauf.acl[f"{ziel}/project.json"] == datei_acl
    assert lauf.aufrufe == ["systemctl reload scanview-server"]


@pytest.mark.docker
@probe_da
def test_default_acl_der_datenwurzel_wie_von_einrichten(tmp_path: Path) -> None:
    """einrichten.sh setzt auf die Datenwurzel `u:caddy:rX` samt Default-ACL. Die Prüfkopie
    entsteht darunter und erbt sie — das darf nicht als „erweitertes Attribut des Uploads“
    abbrechen. Am Ziel steht danach dieselbe ACL wie ohne Default-ACL: genau caddy, keine
    Default-ACL an den Paketordnern."""
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}; "
                            f"setfacl -m u:991:rX -m d:u:991:rX {DATEN}", PID)
    assert lauf.rc == 0, lauf.stdout + lauf.stderr
    ziel = f"{DATEN}/{PID}"
    assert not any(".freischalten" in p for p in lauf.nachher)
    ordner_acl = ["user::rwx", "user:991:r-x", "group::r-x", "mask::r-x", "other::---"]
    datei_acl = ["user::rw-", "user:991:r--", "group::r--", "mask::r--", "other::---"]
    assert lauf.acl[ziel] == ordner_acl
    assert lauf.acl[f"{ziel}/unter"] == ordner_acl
    assert lauf.acl[f"{ziel}/project.json"] == datei_acl
    assert lauf.acl[f"{ziel}/unter/punkte.bin"] == datei_acl
    assert lauf.nachher[ziel][1:4] == [0, GID_SCANVIEW, "0o750"]
    assert lauf.aufrufe == ["systemctl reload scanview-server"]


@pytest.mark.docker
@probe_da
def test_weite_rechte_des_uploads_werden_eingeengt(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}; chmod -R 0777 {EINGANG}/{PID}", PID)
    assert lauf.rc == 0, lauf.stderr
    for pfad, (typ, uid, gid, modus, _xattr) in lauf.nachher.items():
        if pfad.startswith(f"{DATEN}/{PID}"):
            assert (uid, gid) == (0, GID_SCANVIEW)
            assert modus == ("0o750" if typ == "d" else "0o640"), pfad


@pytest.mark.parametrize("pid", [
    "../etc", "a/b", "a b", "Gross", "-fuehrend", "", "a\nb", "a" * 65, "x.y", "a;id",
    "$(id)", "a*",
])
@pytest.mark.docker
@probe_da
def test_ungueltige_id_bricht_ohne_aenderung_ab(tmp_path: Path, pid: str) -> None:
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}", pid)
    assert lauf.rc != 0
    assert "projectId" in lauf.stderr
    lauf.unveraendert()


@pytest.mark.docker
@probe_da
def test_eingang_als_symlink_bricht_ohne_aenderung_ab(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, f"paket /srv/scanview/anderswo {PID}; "
                            f"ln -s /srv/scanview/anderswo {EINGANG}/{PID}", PID)
    assert lauf.rc != 0
    assert "Symlink" in lauf.stderr
    lauf.unveraendert()


@pytest.mark.docker
@probe_da
def test_eingang_keine_ordner_bricht_ohne_aenderung_ab(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, f"echo x >{EINGANG}/{PID}", PID)
    assert lauf.rc != 0
    lauf.unveraendert()


@pytest.mark.docker
@probe_da
def test_eingang_fehlt(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, "", PID)
    assert lauf.rc != 0
    assert "fehlt" in lauf.stderr
    lauf.unveraendert()


@pytest.mark.docker
@probe_da
def test_vorhandenes_ziel_wird_nie_ueberschrieben(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}; install -d {DATEN}/{PID}", PID)
    assert lauf.rc != 0
    assert "existiert schon" in lauf.stderr
    lauf.unveraendert()


@pytest.mark.docker
@probe_da
def test_ziel_als_symlink_wird_nie_ueberschrieben(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}; ln -s /nirgends {DATEN}/{PID}", PID)
    assert lauf.rc != 0
    lauf.unveraendert()


@pytest.mark.docker
@probe_da
def test_datenwurzel_als_symlink(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}; mv {DATEN} /srv/scanview/echt; "
                            f"ln -s /srv/scanview/echt {DATEN}", PID)
    assert lauf.rc != 0
    lauf.unveraendert()


@pytest.mark.docker
@probe_da
def test_anderes_dateisystem_bricht_ohne_aenderung_ab(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}", PID, eigenes_eingang_fs=True)
    assert lauf.rc != 0
    assert "Dateisystem" in lauf.stderr
    lauf.unveraendert()


@pytest.mark.parametrize("argumente", [
    (), (PID, "mehr"), ("pruefen",), ("pruefen", PID, "mehr"), ("--help",), ("-x", PID),
    ("freischalten", PID),
], ids=["keine", "zwei", "pruefen-allein", "pruefen-drei", "hilfe", "option", "unbekannt"])
@pytest.mark.docker
@probe_da
def test_falsche_argumente_brechen_ohne_aenderung_ab(tmp_path: Path,
                                                     argumente: tuple[str, ...]) -> None:
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}", *argumente)
    assert lauf.rc == 2
    assert "Aufruf" in lauf.stderr
    lauf.unveraendert()


@pytest.mark.parametrize(("vorbereitung", "text"), [
    (f"ln -s /etc/passwd {EINGANG}/{PID}/link", "Dateityp"),
    (f"mkfifo {EINGANG}/{PID}/rohr", "Dateityp"),
    (f"ln {EINGANG}/{PID}/project.json {EINGANG}/{PID}/zweitname", "Hardlink"),
    (f"printf '{{\"schemaVersion\": 2, \"projectId\": \"anders\"}}' "
     f">{EINGANG}/{PID}/project.json", "projectId"),
    (f"printf '{{\"schemaVersion\": 1, \"projectId\": \"{PID}\"}}' "
     f">{EINGANG}/{PID}/project.json", "schemaVersion"),
    (f"rm {EINGANG}/{PID}/project.json", "project.json"),
    (f"printf 'kein json' >{EINGANG}/{PID}/project.json", "project.json"),
], ids=["symlink", "fifo", "hardlink", "fremde-id", "schema-1", "ohne-project-json",
        "kein-json"])
@pytest.mark.docker
@probe_da
def test_unzulaessiger_inhalt_wird_nicht_freigeschaltet(tmp_path: Path, vorbereitung: str,
                                                        text: str) -> None:
    """Inhaltsprüfung erst nach dem Wegnehmen aus dem Eingang (kein TOCTOU): Das Paket liegt
    danach root-eigen und ohne Gruppen-/Fremdrechte im Quarantäneordner, nichts ist sichtbar,
    kein Reload."""
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}; {vorbereitung}", PID)
    assert lauf.rc != 0
    assert text in lauf.stderr
    assert f"{DATEN}/{PID}" not in lauf.nachher
    quarantaene = f"{DATEN}/.freischalten-{PID}"
    assert lauf.nachher[quarantaene][:4] == ["d", 0, 0, "0o700"]
    assert "rm -r" in lauf.stderr
    assert lauf.aufrufe == []


@pytest.mark.parametrize(("vorbereitung", "text"), [
    (f"setfacl -m u:{UID_DEPLOY}:rwx -m d:u:{UID_DEPLOY}:rwx {EINGANG}/{PID}/unter",
     "posix_acl_access, system.posix_acl_default"),
    (f"setfacl -m u:{UID_DEPLOY}:rwx {EINGANG}/{PID}", "system.posix_acl_access"),
    (f"setfacl -m u:{UID_DEPLOY}:rw {EINGANG}/{PID}/unter/punkte.bin",
     "system.posix_acl_access"),
    (f"setfacl -m g:{GID_SCANVIEW}:rwx {EINGANG}/{PID}/project.json", "system.posix_acl_access"),
    (f"python3 -c 'import os; os.setxattr(\"{EINGANG}/{PID}/project.json\", \"user.x\", b\"1\")'",
     "user.x"),
], ids=["ordner-acl-und-default", "wurzel-acl", "datei-acl", "gruppen-acl", "user-xattr"])
@pytest.mark.docker
@probe_da
def test_acl_im_upload_wird_nicht_uebernommen(tmp_path: Path, vorbereitung: str,
                                              text: str) -> None:
    """Eine benannte ACL (oder ein anderes xattr) würde die Übernahme überleben: über einen offen
    gehaltenen Deskriptor bekäme der Deploy-Nutzer nach setfacl (Maske neu) wieder Rechte.
    Abbruch, bevor irgendetwas sichtbar wird; ACL-Einträge echt gesetzt (setfacl)."""
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}; {vorbereitung}", PID)
    assert lauf.rc != 0
    assert "ACL bzw. erweiterte Attribute" in lauf.stderr and text in lauf.stderr
    assert f"{DATEN}/{PID}" not in lauf.nachher
    assert lauf.nachher[f"{DATEN}/.freischalten-{PID}"][:3] == ["d", 0, 0]
    assert lauf.aufrufe == []
    # Kein Eintrag mit fremdem Recht wird sichtbar; die Default-ACL ist nicht freigeschaltet.
    assert not any("/srv/scanview/data/" + PID in p for p in lauf.acl)


@pytest.mark.docker
@probe_da
def test_fremder_eigentuemer_im_upload(tmp_path: Path) -> None:
    """Ein root-eigener Eintrag im Upload wird nicht übernommen (sonst würde root-Inhalt, den
    der Deploy-Nutzer nicht erzeugen kann, mit freigeschaltet)."""
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}; echo x >{EINGANG}/{PID}/unter/root",
                  PID)
    assert lauf.rc != 0
    assert "gehört nicht dem Hochladenden" in lauf.stderr
    assert f"{DATEN}/{PID}" not in lauf.nachher
    assert lauf.aufrufe == []


@pytest.mark.docker
@probe_da
def test_root_eigener_eingang_bricht_ohne_aenderung_ab(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}; chown -R 0:0 {EINGANG}/{PID}", PID)
    assert lauf.rc != 0
    assert "gehört root" in lauf.stderr
    lauf.unveraendert()


@pytest.mark.docker
@probe_da
def test_hochladender_in_gruppe_scanview_verweigert(tmp_path: Path) -> None:
    """Mitglieder der Gruppe scanview erreichen die Datenwurzel (0750) und damit die Quarantäne."""
    group = GROUP.replace(f"scanview:x:{GID_SCANVIEW}:\n", f"scanview:x:{GID_SCANVIEW}:deploy\n")
    assert group != GROUP
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}", PID, group=group)
    assert lauf.rc != 0
    assert "Mitglied der Gruppe scanview" in lauf.stderr
    lauf.unveraendert()


@pytest.mark.parametrize("rest", [f".freischalten-{PID}", f".freischalten-{PID}.kopie"])
@pytest.mark.docker
@probe_da
def test_reste_eines_abgebrochenen_laufs_sperren(tmp_path: Path, rest: str) -> None:
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}; install -d -m 0700 "
                            f"{DATEN}/{rest}", PID)
    assert lauf.rc != 0
    assert ".freischalten" in lauf.stderr
    lauf.unveraendert()


#: Ein Prozess hält vor dem Freischalten Schreib-Deskriptoren auf zwei Upload-Dateien offen und
#: schreibt erst NACH dem Lauf (Signal /tmp/los) über sie — wie ein Hochladender, der seine
#: Deskriptoren über die Übernahme rettet.
_OFFENER_SCHREIBER = (
    "python3 -c '"
    "import os, time\n"
    f"a = os.open(\"{EINGANG}/{PID}/unter/punkte.bin\", os.O_WRONLY)\n"
    f"b = os.open(\"{EINGANG}/{PID}/project.json\", os.O_WRONLY)\n"
    "open(\"/tmp/bereit\", \"w\").close()\n"
    "while not os.path.exists(\"/tmp/los\"): time.sleep(0.02)\n"
    "os.pwrite(a, b\"MANIPULIERT\", 0); os.pwrite(b, b\"{}\" + b\" \" * 60, 0)\n"
    "os.fsync(a); os.fsync(b)\n"
    "open(\"/tmp/fertig\", \"w\").close()\n"
    "' >/dev/null 2>&1 &\n"
    "while [ ! -e /tmp/bereit ]; do sleep 0.02; done\n"
    "cat >/tmp/nach.sh <<'NACH'\n"
    "touch /tmp/los\n"
    "for _ in $(seq 250); do [ -e /tmp/fertig ] && break; sleep 0.02; done\n"
    "[ -e /tmp/fertig ] && echo GESCHRIEBEN\n"
    f"cat {DATEN}/{PID}/unter/punkte.bin {DATEN}/{PID}/project.json\n"
    "NACH\n"
)


@pytest.mark.docker
@probe_da
def test_offener_schreib_deskriptor_erreicht_das_paket_nicht(tmp_path: Path) -> None:
    """Eigentümer- und Rechtewechsel entziehen offene Deskriptoren nicht. Das
    freigeschaltete Paket besteht aus neuen Inodes (Kopie) — was über einen alten Deskriptor
    nach der Freischaltung geschrieben wird, erscheint dort nicht."""
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}\n{_OFFENER_SCHREIBER}", PID)
    assert lauf.rc == 0, lauf.stderr
    assert lauf.nach.startswith("GESCHRIEBEN\n"), lauf.nach  # der Schreiber hat geschrieben
    assert "MANIPULIERT" not in lauf.nach
    assert lauf.nach.endswith(f'punkte\n{{"schemaVersion": 2, "projectId": "{PID}"}}\n')
    assert not any(".freischalten" in p for p in lauf.nachher)


@pytest.mark.docker
@probe_da
def test_zu_wenig_platz_fuer_die_kopie(tmp_path: Path) -> None:
    """Die Kopie braucht die Paketgröße plus 10 GiB Reserve — sonst Abbruch vor dem Kopieren."""
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}; "
                            f"truncate -s 11G {EINGANG}/{PID}/unter/gross.bin; "
                            f"chown 1000:1000 {EINGANG}/{PID}/unter/gross.bin", PID)
    assert lauf.rc != 0
    assert "zu wenig Platz" in lauf.stderr
    assert f"{DATEN}/{PID}" not in lauf.nachher
    assert f"{DATEN}/.freischalten-{PID}.kopie" not in lauf.nachher
    assert lauf.aufrufe == []


@pytest.mark.docker
@probe_da
def test_viele_ordner_bei_kleinem_deskriptor_limit(tmp_path: Path) -> None:
    """Die Kopie hält nicht je Ordner einen Deskriptor offen: 300 Ordner bei nofile=256 (Pakete
    mit gekachelten Panoramen oder 3D Tiles haben viele Ordner; unter sudo gilt oft 1024)."""
    lauf = _probe(tmp_path, f"paket {EINGANG}/{PID} {PID}; "
                            f"for i in $(seq 300); do mkdir -p {EINGANG}/{PID}/unter/k$i; "
                            f"echo $i >{EINGANG}/{PID}/unter/k$i/kachel; done; "
                            f"chown -R 1000:1000 {EINGANG}/{PID}", PID, nofile=256)
    assert lauf.rc == 0, lauf.stderr
    assert lauf.nachher[f"{DATEN}/{PID}/unter/k300/kachel"][:4] == ["f", 0, GID_SCANVIEW,
                                                                    "0o640"]


# --- pruefen <id> --------------------------------------------------------------------------------

@pytest.mark.parametrize(("vorbereitung", "antwort"), [
    ("", "OK"),
    (f"paket {EINGANG}/{PID} {PID}", "OK"),
    (f"install -d {DATEN}/{PID}", "VORHANDEN"),
    (f"ln -s /tmp {EINGANG}/{PID}", "EINGANG_SYMLINK"),
    (f"echo x >{EINGANG}/{PID}", "EINGANG_KEIN_ORDNER"),
    (f"install -d {DATEN}/.freischalten-{PID}", "RESTE"),
    (f"mv {DATEN} /srv/scanview/echt; ln -s /srv/scanview/echt {DATEN}", "WURZEL"),
    (f"rmdir {EINGANG}; ln -s /tmp {EINGANG}", "EINGANG_WURZEL"),
], ids=["leer", "fortsetzen", "vorhanden", "eingang-symlink", "eingang-datei", "reste",
        "wurzel", "eingang-wurzel"])
@pytest.mark.docker
@probe_da
def test_pruefen_antwortet_ohne_aenderung(tmp_path: Path, vorbereitung: str,
                                          antwort: str) -> None:
    lauf = _probe(tmp_path, vorbereitung, "pruefen", PID)
    assert lauf.rc == 0, lauf.stderr
    assert lauf.stdout == f"{antwort}\n"
    lauf.unveraendert()


@pytest.mark.docker
@probe_da
def test_pruefen_anderes_dateisystem(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, "", "pruefen", PID, eigenes_eingang_fs=True)
    assert lauf.rc == 0, lauf.stderr
    assert lauf.stdout == "DATEISYSTEM\n"


@pytest.mark.docker
@probe_da
def test_ohne_root_verweigert(tmp_path: Path) -> None:
    befehl = _grundbefehl(tmp_path, nutzer=f"{UID_DEPLOY}:{UID_DEPLOY}")
    ergebnis = subprocess.run([*befehl, ZIEL_SKRIPT, PID], capture_output=True, text=True,
                              check=False, stdin=subprocess.DEVNULL, timeout=120)
    assert ergebnis.returncode != 0
    assert "root" in ergebnis.stderr


# --- paket-bereitstellen.sh Ende zu Ende (ssh/sudo/rsync als Stubs) -----------------------------

QUELLE = "/srv/scanview/quelle"


@pytest.mark.docker
@probe_da
def test_bereitstellen_ende_zu_ende(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, f"paket {QUELLE} {PID}", QUELLE, "ziel",
                  aufruf=("/probe/paket-bereitstellen.sh",))
    assert lauf.rc == 0, lauf.stdout + lauf.stderr
    assert lauf.nachher[f"{DATEN}/{PID}/project.json"] == ["f", 0, GID_SCANVIEW, "0o640",
                                                         ["system.posix_acl_access"]]
    assert f"{EINGANG}/{PID}" not in lauf.nachher
    sudo = [a for a in lauf.aufrufe if a.startswith("sudo ")]
    assert sudo == [f"sudo -l {ZIEL_SKRIPT}", f"sudo {ZIEL_SKRIPT} pruefen {PID}",
                    f"sudo {ZIEL_SKRIPT} {PID}"]
    rsync = [a for a in lauf.aufrufe if a.startswith("rsync ")]
    assert len(rsync) == 1 and rsync[0].endswith(f"{QUELLE}/ ziel:{EINGANG}/{PID}/")
    assert lauf.aufrufe[-1] == "systemctl reload scanview-server"


@pytest.mark.parametrize(("vorbereitung", "text"), [
    ("touch /srv/scanview/keine-regel", "nicht erlaubt"),
    (f"install -d {DATEN}/{PID}", "existiert schon"),
    (f"ln -s /tmp {EINGANG}/{PID}", "Symlink"),
], ids=["ohne-sudo-regel", "ziel-vorhanden", "eingang-symlink"])
@pytest.mark.docker
@probe_da
def test_bereitstellen_bricht_vor_dem_upload_ab(tmp_path: Path, vorbereitung: str,
                                                text: str) -> None:
    lauf = _probe(tmp_path, f"paket {QUELLE} {PID}; {vorbereitung}", QUELLE, "ziel",
                  aufruf=("/probe/paket-bereitstellen.sh",))
    assert lauf.rc != 0
    assert text in lauf.stderr
    assert not any(a.startswith("rsync ") for a in lauf.aufrufe)
    assert lauf.nachher == lauf.vorher


@pytest.mark.parametrize("host", ["-oProxyCommand=touch /tmp/x", "-v", ""])
def test_bereitstellen_ziel_nie_als_option(tmp_path: Path, host: str) -> None:
    """Ein ssh-Ziel mit führendem „-“ würde von ssh/rsync als Option
    gelesen — Abbruch, bevor ssh überhaupt läuft."""
    quelle = tmp_path / "paket"
    quelle.mkdir()
    (quelle / "project.json").write_text('{"schemaVersion": 2, "projectId": "p"}',
                                         encoding="utf-8")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name in ("ssh", "rsync"):
        (stubs / name).write_text(f"#!/bin/sh\necho {name} >>{tmp_path}/aufrufe\n",
                                  encoding="utf-8")
        (stubs / name).chmod(0o755)
    ergebnis = subprocess.run(["bash", str(BEREITSTELLEN), str(quelle), host],
                              capture_output=True, text=True, check=False,
                              stdin=subprocess.DEVNULL,
                              env={**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}"})
    assert ergebnis.returncode != 0
    assert "ssh-Ziel" in ergebnis.stderr
    assert not (tmp_path / "aufrufe").exists()


def test_bereitstellen_rechnet_die_freischalt_kopie_ein() -> None:
    text = BEREITSTELLEN.read_text(encoding="utf-8")
    assert "((FREI - NOCH - GROESSE < RESERVE))" in text


# --- statisch: Skript, sudoers, Aufrufer ---------------------------------------------------------

def test_skript_ausfuehrbar_und_gehaertet() -> None:
    assert os.access(SKRIPT, os.X_OK)
    text = SKRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/bash\n")
    assert "set -euo pipefail" in text
    assert re.search(r"^export PATH=/usr/sbin:/usr/bin:/sbin:/bin$", text, re.M)
    assert re.search(r"^export LC_ALL=C$", text, re.M)
    # Keine Umgebung, kein eval, keine Shell über fremde Eingaben.
    assert "eval" not in text
    assert "bash -c" not in text and "sh -c" not in text
    assert "SCANVIEW_" not in text


def test_sudoers_vorlage_erlaubt_nur_das_skript() -> None:
    zeilen = [z.strip() for z in SUDOERS.read_text(encoding="utf-8").splitlines()]
    regeln = [z for z in zeilen if z and not z.startswith("#")]
    assert regeln == [REGEL]


@pytest.mark.skipif(shutil.which("visudo") is None, reason="visudo fehlt")
def test_sudoers_vorlage_syntaktisch_gueltig() -> None:
    ergebnis = subprocess.run(["visudo", "-cf", str(SUDOERS)], capture_output=True, text=True,
                              check=False, stdin=subprocess.DEVNULL)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr


def test_bereitstellen_ohne_breite_sudo_rechte() -> None:
    text = BEREITSTELLEN.read_text(encoding="utf-8")
    for verboten in ("sudo -n bash", "sudo bash", "--rsync-path", "sudo -n rsync",
                     "sudo -n test", "sudo -n du", "sudo -n true", "SCANVIEW_ZIELWURZEL"):
        assert verboten not in text, verboten
    assert f"sudo -n -l {ZIEL_SKRIPT}" in text.replace("$FREISCHALTEN", ZIEL_SKRIPT)
    aufrufe = re.findall(r"sudo -n (\S+)", text.replace("$FREISCHALTEN", ZIEL_SKRIPT))
    assert aufrufe and set(aufrufe) <= {"-l", ZIEL_SKRIPT}, aufrufe


def test_einrichten_installiert_skript_eingang_und_sudoers() -> None:
    text = EINRICHTEN.read_text(encoding="utf-8")
    assert f'install -o root -g root -m 0755 "$HIER/scanview-paket-freischalten" {ZIEL_SKRIPT}' \
        in text
    assert "groupadd --system scanview-deploy" in text
    # Mitglieder von scanview-deploy dürfen nicht in scanview sein (Abbruch).
    assert "getent group scanview-deploy | cut -d: -f4" in text
    assert "grep -qx scanview" in text
    assert "install -d -o root -g scanview-deploy -m 1770 /srv/scanview/eingang" in text
    # sudoers: erst in eine von sudo ignorierte Datei (Punkt im Namen), prüfen, dann aktivieren.
    pruefen = text.index("visudo -cf")
    aktivieren = text.index("/etc/sudoers.d/scanview-deploy\n")
    assert pruefen < aktivieren
    assert "/etc/sudoers.d/.scanview-deploy.neu" in text
